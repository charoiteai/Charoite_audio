"""Machine-readable state for the post-meeting pipeline.

The daemon exits before the expensive rebuild and graph update finish. This
module stays dependency-free so the daemon and tests can publish progress
without importing audio, STT, or diarization stacks.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from typing import Any
import graphs
import meeting_stamp


SCHEMA_VERSION = 1
STATUS_DIR = "meeting-status"
STATUS_KEEP_DAYS = 14

# Сколько раз возвращаемся к упавшей встрече. Три попытки покрывают типовую
# причину — вставшую или занятую LLM, — а дальше дело в самой встрече, и
# бесконечный круг только жёг бы машину и путал статусы.
RETRY_LIMIT = 3

# Статус `processing`, который не обновлялся дольше этого, — брошенный: процесс
# умер, не дописав ни ready, ни error. Час выбран с запасом на честную работу:
# полная пересборка часовой встречи со всеми моделями укладывается в минуты.
STALE_PROCESSING = 3600
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{4})")
# Производные файлы конвейера. Список обязан совпадать со Swift-стороной
# (MeetingProcessingService.title) и rename_meeting.SUFFIXES: пропущенный
# хвост здесь = файл разбора в transcript_path и тема «… разбор» в списке
# (инцидент 04.08 — «_разбор» был свежее стенограммы и выигрывал по mtime).
_AUX_SUFFIXES = meeting_stamp.AUX_SUFFIXES   # один список на все стороны (аудит 30.08, luna)


def short_stamp(transcript: pathlib.Path) -> str:
    """Graph notes use the minute stamp even when live files include seconds."""
    match = _STAMP_RE.match(transcript.stem)
    return match.group(1) if match else transcript.stem


def _looks_main(path: pathlib.Path) -> bool:
    """Главная стенограмма начинается с «# Встреча <штамп>»; разбор, минутки и
    подсказки — нет. Читаем первые байты, кандидатов единицы."""
    try:
        with path.open("rb") as fh:
            head = fh.read(200).decode("utf-8", errors="ignore")
    except OSError:
        return False
    return head.lstrip().startswith("# Встреча ")


def find_final_transcript(original: pathlib.Path) -> pathlib.Path:
    """Return the transcript even if graph_updater renamed it to its title."""
    if original.exists():
        return original.resolve()
    # Сначала посекундное имя («…125812_Тема»), потом минутное: вторая
    # встреча той же минуты носит тему при посекундном штампе, и минутный
    # глоб находил бы файл соседки (карточка №39).
    bare = meeting_stamp.stamp_of(original.stem) or short_stamp(original)
    for stamp in dict.fromkeys((bare, short_stamp(original))):
        candidates, live_copies = [], []
        for path in original.parent.glob(f"{stamp}_*.md"):
            # По ХВОСТУ имени, как meeting_stamp.stamp_of и rename_meeting: проверка
            # подстрокой вычёркивала главный файл встречи «План_разбора» (тема со
            # словом «разбор» внутри) и статус получал несуществующий путь
            # (аудит DeepSeek 16.08).
            suffix = path.stem[len(stamp):].lower()
            if suffix.endswith("_live"):
                live_copies.append(path)   # копия или тема на «live» — решаем ниже
                continue
            if any(suffix.endswith(aux) for aux in _AUX_SUFFIXES) and not _looks_main(path):
                # тема встречи может сама кончаться на «разбор»: производный файл
                # отличаем по содержимому, а не только по имени (хвост 20.08, DS)
                continue
            candidates.append(path)
        if not candidates:
            # «_live» — дословная копия главного файла с тем же началом: рядом с
            # настоящим главным её спасать по содержимому нельзя, даже если она
            # моложе (DS по #455). А без главного тема встречи может сама
            # кончаться на «live» («Демо live») — тогда судим по содержимому,
            # как остальных (DS r2). Копия узнаётся по имени источника, а не
            # по mtime: `<штамп>_live` — копия голого штампа, `X_live` при
            # живом `X` — копия `X`; иначе тронутая синком копия обгоняла бы
            # главный (DS r3)
            # источники копий — все файлы с этим штампом в каталоге: «<штамп>_live»
            # — копия голого файла, «X_live» при живом X — копия X (GLM r1: множество
            # из одних live-копий делало средний ярус пустым)
            stems = {path.stem for path in original.parent.glob(f"{stamp}*.md")}
            mains = [path for path in live_copies if _looks_main(path)]
            # Сначала не-«<штамп>_live» без живого источника (главный «…_Демо_live»
            # при переименованном голом файле); затем «<штамп>_live» без голого
            # рядом — главный прежних версий с темой «live», а не копия (luna по
            # аудиту 30.08); mtime — только внутри остатка.
            primary = [path for path in mains
                       if path.stem[len(stamp):].lower() != "_live"
                       and path.stem[:-len("_live")] not in stems]
            candidates = (primary
                          or [path for path in mains if path.stem[:-len("_live")] not in stems]
                          or mains)
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime).resolve()
    return original.resolve()


def find_meeting_note(
    cfg: dict[str, Any],
    transcript: pathlib.Path,
    *,
    newer_than: float | None = None,
) -> pathlib.Path | None:
    """Find the exact note created in the configured graph or a project graph."""
    override = graphs.env_override() is not None
    configured = graphs.graph_dir(cfg)
    if configured is None:
        return None
    # Ключ заметки — как его выбрал graph_updater по ТЕКУЩЕМУ имени файла:
    # минутный у владельца минуты, посекундный у второй встречи той же
    # минуты (meeting_stamp.graph_key, карточка №39).
    current = find_final_transcript(transcript)
    roots = [configured]
    # graph_updater may route a meeting to ``configured.parent/<project>``.
    if not override:
        try:
            roots.extend(path for path in configured.parent.iterdir() if path.is_dir())
        except OSError:
            pass
    seen: set[pathlib.Path] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        stamp = meeting_stamp.graph_key(current.parent, current.stem, root)
        note = root / "Встречи" / f"{stamp}.md"
        try:
            if note.is_file() and (newer_than is None or note.stat().st_mtime >= newer_than):
                return note.resolve()
        except OSError:
            continue
    return None


class MeetingStatusStore:
    """Atomically publish one small status document per meeting."""

    def __init__(self, root: pathlib.Path, *, now=time.time):
        self.root = pathlib.Path(root)
        self.directory = self.root / "logs" / STATUS_DIR
        self._now = now
        self._keys = {}   # путь стенограммы → ключ статуса, на жизнь процесса

    def processing(self, transcript: pathlib.Path, stage: str,
                   part: int | None = None, parts: int | None = None) -> pathlib.Path:
        """Текущий этап; для длинной стенограммы — ещё и какая часть из скольких.

        «Обновляю граф» на встрече в двадцать тысяч знаков висит минутами и
        ничем не отличается от зависшего процесса. Номер части — единственное,
        что отличает работу от смерти, не заглядывая в логи.
        """
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "processing",
            "stage": stage,
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
            # Повтор всегда проходит через processing. Обнулять здесь счётчик
            # значило бы никогда не дойти до предела: встреча, падающая по
            # своей причине, каталась бы по кругу вечно.
            "attempts": int(current.get("attempts", 0)),
        }
        if part and parts:
            payload["part"] = int(part)
            payload["parts"] = int(parts)
        self._prune(now)
        return self._write(transcript, payload)

    def ready(self, transcript: pathlib.Path, note: pathlib.Path | None,
              names_pending: bool = False) -> pathlib.Path:
        """Встреча разобрана. names_pending — разбор прошёл, но не целиком.

        note=None — заметки нет и не будет: на лёгком профиле граф знаний
        выключен (`sufler.graph: false`), стенограмма и минутки собраны, а
        узлов никто не строил. Это готовность, а не ошибка: повторять
        конвейер незачем. Поле `note_path` в таком статусе отсутствует —
        читатели уже умеют его не находить (в приложении оно опционально).

        12.08 модель молчала на разборе имён, стенограмма ушла с «Собеседник
        1..5», и статус был неотличим от полностью удачного. Готовность и
        полнота — разные вещи: граф обновлён (значит ready, повторять весь
        конвейер незачем), но человеку есть что доделать.
        """
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "ready",
            "stage": "complete",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
        }
        if note is not None:
            payload["note_path"] = str(pathlib.Path(note).resolve())
        # Поле появляется только когда есть что сказать: старые читатели
        # статуса (и приложение до обновления) видят прежний документ.
        if names_pending:
            payload["names_pending"] = True
        return self._write(transcript, payload)

    def failed(self, transcript: pathlib.Path, error: object) -> pathlib.Path:
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "error",
            "stage": "failed",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
            "error": str(error)[:2000],
            # Счётчик живёт в статусе, а не в памяти процесса: тот, кто будет
            # повторять, — уже другой процесс, запущенный после следующей встречи.
            "attempts": int(current.get("attempts", 0)) + 1,
        }
        return self._write(transcript, payload)

    def unfinished(self, *, stale_after: float = STALE_PROCESSING,
                   limit: int = RETRY_LIMIT) -> list[dict[str, Any]]:
        """Встречи, которые не доехали до готовности, — свежие первыми.

        03.08 разбор упал на вставшей LLM в 10:33, и встреча пролежала
        необработанной до вечера: повторять её было некому. Со стороны это
        читается как «программа перестала раскладывать встречи по папкам» —
        худший вид поломки, молчаливый.

        Берём два случая: явную ошибку и брошенный `processing` — процесс,
        который умер, не дописав ни ready, ни error (SIGKILL, перезагрузка,
        закрытая крышка).
        """
        now = float(self._now())
        out: list[dict[str, Any]] = []
        # Одна встреча — один голос. Записи группируются по файлу, куда сегодня
        # ведёт их transcript_path: записи с мёртвым путём уступают живым (мёртвый
        # «error» соседки той же минуты не роняет и не угоняет чужой повтор), а
        # оставшиеся дедуплицируются по ключу — свежая перевешивает (DS r1 #456).
        by_file: dict[pathlib.Path, list[tuple[bool, float, str, dict[str, Any]]]] = {}
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            tp = str(data.get("transcript_path") or "")
            if not tp:
                continue
            raw = pathlib.Path(tp)
            current = find_final_transcript(raw)
            if not current.is_file():
                continue              # стенограмму удалили — повторять нечего
            key = str(data.get("key") or "") or self._key(raw)
            try:
                upd = float(data.get("updated_at", 0) or 0)
                int(data.get("attempts", 0) or 0)
            except (TypeError, ValueError):
                continue              # повреждённая запись не должна ронять весь подбор (luna r1)
            by_file.setdefault(current, []).append((raw.is_file(), upd, key, data))
        latest: dict[str, tuple[float, pathlib.Path, dict[str, Any]]] = {}
        for current, group in by_file.items():
            alive = [g for g in group if g[0]]
            # Только мёртвые пути на файл: это либо своё окно между retitle и
            # следующей записью, либо соседка, удалённая руками мимо forget, чей
            # путь резолвится на чужой файл. По именам их не различить (DS r2);
            # выбрано первое — автоповтор дороже, чем один лишний прогон в
            # экзотике ручного удаления (копия до пересборки лежит в .prev).
            for _, upd, key, data in (alive or [max(group, key=lambda g: g[1])]):
                if key not in latest or upd > latest[key][0]:
                    latest[key] = (upd, current, data)
        for _, current, data in latest.values():
            # ready — сделано; empty — сделано и повторять нечего: тишину
            # можно разбирать хоть трижды, речи в ней не появится.
            if data.get("state") in ("ready", "empty"):
                continue
            if int(data.get("attempts", 0)) >= limit:
                continue
            if data.get("state") == "processing":
                if now - float(data.get("updated_at", 0)) < stale_after:
                    continue          # идёт прямо сейчас — не мешаем
            elif data.get("state") != "error":
                continue
            # путь — текущий: после retitle старый мёртв, а повтору нужен файл
            out.append({**data, "transcript_path": str(current)})
        return sorted(out, key=lambda d: float(d.get("updated_at", 0)), reverse=True)

    def busy(self, *, stale_after: float = STALE_PROCESSING) -> list[str]:
        """Встречи, которые обрабатываются прямо сейчас.

        Нужно ночному циклу: 12.08 он совпал с разбором встречи, и на 64 ГБ
        одновременно не поместились транскрипция, ревизия ядер и досье.
        Локальный сервер начал выгружать и грузить модели по кругу — 41 раз
        за прогон, — запросы стали висеть минутами, а потом он лёг совсем:
        258 тем ушли без разбора.

        Брошенный `processing` (процесс умер, не дописав итог) занятостью не
        считается — иначе одна мёртвая запись отменяла бы ночи навсегда.
        """
        now = float(self._now())
        out: list[str] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("state") != "processing":
                continue
            try:
                if now - float(data.get("updated_at", 0) or 0) >= stale_after:
                    continue
            except (TypeError, ValueError):
                continue              # повреждённая запись — не занятость (GLM r2)
            out.append(str(data.get("stage") or path.stem))
        return out

    def typical_duration(self, *, minimum_samples: int = 3) -> float | None:
        """Сколько обычно занимает обработка — по прошлым встречам, в секундах.

        Приложение обещало «~2-4 минуты» константой, а живые встречи считались
        по пять и по тридцать минут: обещание расходилось с правдой в разы, и
        человек шёл искать поломку там, где всё шло нормально. Длительность
        прошлых обработок уже лежит в статусах — её достаточно, чтобы говорить
        правду и подстраиваться под машину.

        Медиана, а не среднее: одна встреча со вставшей LLM тянула тридцать
        минут и в среднем перевесила бы десяток нормальных. Пока готовых
        встреч мало, честнее не обещать ничего.
        """
        spans: list[float] = []
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("state") != "ready":
                continue
            span = float(data.get("updated_at", 0)) - float(data.get("started_at", 0))
            if span > 0:
                spans.append(span)
        if len(spans) < minimum_samples:
            return None
        spans.sort()
        mid = len(spans) // 2
        return spans[mid] if len(spans) % 2 else (spans[mid - 1] + spans[mid]) / 2

    def no_speech(self, transcript: pathlib.Path) -> pathlib.Path:
        """Запись есть, речи в ней нет.

        Отдельное состояние, а не ошибка: 03.08 сорокасекундная запись с
        одной тишиной получила «ошибку обработки» — и человек искал поломку
        там, где её не было, а конвейер собирался повторять разбор тишины.
        """
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "empty",
            "stage": "no_speech",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
            "attempts": int(current.get("attempts", 0)),
        }
        return self._write(transcript, payload)

    def has_transcript(self, transcript: pathlib.Path) -> bool:
        return find_final_transcript(pathlib.Path(transcript)).is_file()

    def _key(self, transcript: pathlib.Path) -> str:
        """Ключ файла статуса — штамп ИСХОДНОГО файла встречи, один на всю жизнь.

        graph_updater переименовывает стенограмму посреди прогона (retitle),
        а падение после этого писало «error» под старым стемом и указывало на
        новый файл: повтор шёл под новым ключом, старый json оставался
        «error» навсегда — и каждая тихая итерация заново пересобирала встречу
        из записей, затирая стенограмму (аудит 30.08, GLM Critical 2).

        Ключ считается один раз и запоминается: в процессе — по пути, на
        диске — полем `key` в самом статусе. Новый процесс (graph_updater,
        повтор по титулованному пути) находит запись той же встречи по тому,
        куда сегодня резолвится её `transcript_path`, и берёт ключ оттуда;
        записи с мёртвым путём уступают живым — мёртвый статус соседки той
        же минуты не угоняет чужой ключ (DS r1 по #456). Ни `graph_key`, ни
        владения минутой здесь нет: имя статуса не обязано совпадать с именем
        заметки графа, ему достаточно быть неизменным.
        """
        transcript = pathlib.Path(transcript)
        tp = str(transcript)
        cached = self._keys.get(tp)
        if cached:
            return cached
        current = find_final_transcript(transcript)
        key = self._stored_key_for(current)
        if key is None:
            stem = current.stem if current.exists() else transcript.stem
            key = meeting_stamp.stamp_of(stem) or stem
        key = re.sub(r"[^\w.-]+", "_", key)
        self._keys[tp] = key
        return key

    def _stored_key_for(self, current: pathlib.Path) -> str | None:
        """Ключ уже существующей записи, чей transcript_path ведёт к этому файлу."""
        best: tuple[int, float, int, str] | None = None
        candidates = 0
        for path in self.directory.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = data.get("key") if isinstance(data, dict) else None
            tp = str(data.get("transcript_path") or "") if isinstance(data, dict) else ""
            if not key or not tp:
                continue
            raw = pathlib.Path(tp)
            if find_final_transcript(raw) != current:
                continue
            candidates += 1
            # живой путь → свежесть → запись, названная своим ключом (первичная)
            try:
                upd = float(data.get("updated_at", 0) or 0)
            except (TypeError, ValueError):
                upd = 0.0
            rank = (1 if raw.is_file() else 0, upd, 1 if path.stem == str(key) else 0, str(key))
            if best is None or rank[:3] > best[:3]:
                best = rank
        if best is None:
            return None
        if best[0] == 0 and candidates > 1:
            # только мёртвые пути и их несколько: свежесть — не признак владения,
            # соседка той же минуты угнала бы ключ и meeting_id (GLM r2) —
            # не гадаем, дальше сработает детерминированный штамп текущего файла
            return None
        return best[3]

    def _path(self, transcript: pathlib.Path) -> pathlib.Path:
        return self.directory / f"{self._key(transcript)}.json"

    def _read(self, transcript: pathlib.Path) -> dict[str, Any]:
        try:
            data = json.loads(self._path(transcript).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, transcript: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(transcript)
        # meeting_id не меняется за жизнь встречи: приложение принимает итог
        # повтора только при точном совпадении id, а retitle посреди прогона
        # давал бы новый стем (luna r1 по #456). Тема для карточки берётся из
        # transcript_path, не из id.
        previous = self._read(transcript).get("meeting_id")
        payload = {**payload, "key": self._key(transcript),
                   "meeting_id": previous or payload.get("meeting_id")}
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        tmp = pathlib.Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    def _prune(self, now: float) -> None:
        if not self.directory.is_dir():
            return
        cutoff = now - STATUS_KEEP_DAYS * 86400
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
