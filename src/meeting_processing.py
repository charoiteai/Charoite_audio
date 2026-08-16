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
_AUX_SUFFIXES = ("_minutes", "_hints", "_live", "_debrief",
                 "_разбор", "_ревизия_claude", "_спикеры")


def short_stamp(transcript: pathlib.Path) -> str:
    """Graph notes use the minute stamp even when live files include seconds."""
    match = _STAMP_RE.match(transcript.stem)
    return match.group(1) if match else transcript.stem


def find_final_transcript(original: pathlib.Path) -> pathlib.Path:
    """Return the transcript even if graph_updater renamed it to its title."""
    if original.exists():
        return original.resolve()
    stamp = short_stamp(original)
    candidates = []
    for path in original.parent.glob(f"{stamp}_*.md"):
        # По ХВОСТУ имени, как meeting_stamp.stamp_of и rename_meeting: проверка
        # подстрокой вычёркивала главный файл встречи «План_разбора» (тема со
        # словом «разбор» внутри) и статус получал несуществующий путь
        # (аудит DeepSeek 16.08).
        suffix = path.stem[len(stamp):].lower()
        if any(suffix.endswith(aux) for aux in _AUX_SUFFIXES):
            continue
        candidates.append(path)
    if not candidates:
        return original.resolve()
    return max(candidates, key=lambda path: path.stat().st_mtime).resolve()


def find_meeting_note(
    cfg: dict[str, Any],
    transcript: pathlib.Path,
    *,
    newer_than: float | None = None,
) -> pathlib.Path | None:
    """Find the exact note created in the configured graph or a project graph."""
    override = os.environ.get("SUFLER_GRAPH_DIR")
    raw = override or (cfg.get("sufler") or {}).get("graph_dir", "")
    if not raw:
        return None
    configured = pathlib.Path(raw).expanduser()
    stamp = short_stamp(transcript)
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

    def ready(self, transcript: pathlib.Path, note: pathlib.Path,
              names_pending: bool = False) -> pathlib.Path:
        """Встреча разобрана. names_pending — разбор прошёл, но не целиком.

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
            "note_path": str(pathlib.Path(note).resolve()),
        }
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
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # ready — сделано; empty — сделано и повторять нечего: тишину
            # можно разбирать хоть трижды, речи в ней не появится.
            if not isinstance(data, dict) or data.get("state") in ("ready", "empty"):
                continue
            if int(data.get("attempts", 0)) >= limit:
                continue
            if data.get("state") == "processing":
                if now - float(data.get("updated_at", 0)) < stale_after:
                    continue          # идёт прямо сейчас — не мешаем
            elif data.get("state") != "error":
                continue
            transcript = pathlib.Path(str(data.get("transcript_path", "")))
            if not transcript.is_file():
                continue              # стенограмму удалили — повторять нечего
            out.append(data)
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
            if now - float(data.get("updated_at", 0)) >= stale_after:
                continue
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

    def _path(self, transcript: pathlib.Path) -> pathlib.Path:
        safe = re.sub(r"[^\w.-]+", "_", pathlib.Path(transcript).stem)
        return self.directory / f"{safe}.json"

    def _read(self, transcript: pathlib.Path) -> dict[str, Any]:
        try:
            data = json.loads(self._path(transcript).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, transcript: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(transcript)
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
