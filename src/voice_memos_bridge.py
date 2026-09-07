"""Мост из Диктофона (Voice Memos) в папку импорта: копия, никогда не перенос.

Зачем. Компаньон на iPhone 07.09 останавливался трижды за собрание (20 мин,
2 мин, 5 мин), а стандартный Диктофон писал без обрывов — владелец спросил,
нельзя ли брать полную запись оттуда. Ресёрч 08.09 (официальные источники
Apple и разборы 2025–2026): у Диктофона в Быстрых командах нет действия
«отдать файл записи» и нет триггера «новая запись» — на телефоне без ручного
«Поделиться → Сохранить в Файлы» не обойтись. Зато на Mac после включения
синхронизации iCloud (приложение Диктофон на Mac нужно открыть хотя бы раз)
записи лежат обычными `.m4a` в контейнере
`~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings`.
Эта папка — единственная точка автоматизации, и её нельзя трогать: на macOS
26.1 переименование или перенос файла ломает запись в приложении. Папка
закрыта TCC: читать её может только процесс с «Полным доступом к диску» —
сканер импорта запускает приложение, значит доступ выдаётся Чароиту. Поэтому
мост только копирует новые файлы в папку импорта, а дальше работает штатный
конвейер импорта (`scripts/import_meeting.py --scan`), как для записи с
телефона или перетащенного файла.

Мост включается явно (`audio.voice_memos_bridge: true`), смотрит только на
записи не старше `audio.voice_memos_max_age_days` (14) и берёт не больше
`audio.voice_memos_per_scan` (3) самых свежих за скан — первый скан после
включения не должен вылить многолетний архив Диктофона в конвейер, а потеря
журнала не должна вернуть в конвейер прошлогодние записи.

Что копируется: `.m4a`, лежащий без изменений не меньше минуты (синк iCloud
дописывает файл кусками), длиннее `audio.voice_memos_min_seconds` (по
умолчанию 120 с — короче обычно голосовая заметка, не встреча; длительность
не определилась — копируем, потерять хуже, чем импортировать лишнее) и ещё
не виденный по ключу «имя|размер|mtime» (журнал `logs/voice_memos_bridge.json`
в корне данных). Копия идёт через скрытый `.<имя>.<uuid>.part` и атомарный
rename — сканер импорта скрытые файлы не видит и не заберёт половину. mtime
копии не сохраняется намеренно: дата встречи берётся из контейнера m4a
(`media_meta.recorded_at`), а свежий mtime защищает `.part` от уборки
«брошенных временных файлов» сканера.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import uuid

DEFAULT_DIR = "~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
STATE_NAME = "voice_memos_bridge.json"
SETTLE_SECONDS = 60.0
DEFAULT_MIN_SECONDS = 120.0
DEFAULT_PER_SCAN = 3                 # самых свежих за скан: первый скан после включения не льёт весь архив
DEFAULT_MAX_AGE_DAYS = 14.0          # старше — архив Диктофона, не «вчерашняя встреча»: не трогаем
UNPARSED_GRACE_SECONDS = 3600.0      # afinfo не разобрал молодой файл — Диктофон пишет moov в конец, ждём
STATE_KEEP_SECONDS = 30 * 86400.0    # записи журнала старше месяца выкидываем (> DEFAULT_MAX_AGE_DAYS — запись к тому времени уже за гейтом возраста)
ERROR_MARKER_SUFFIX = ".import-error"   # как у import_meeting.error_marker: `.<имя>.import-error`
DENIED_HINT_SECONDS = 6 * 3600.0     # подсказка про полный доступ к диску — не чаще раза в 6 часов
DENIED_HINT = ("нет доступа к папке Диктофона: в Настройках → Конфиденциальность и "
               "безопасность → Полный доступ к диску добавьте Чароит (сканер импорта "
               "запускается от его имени) и перезапустите приложение")
SUFFIXES = (".m4a",)
_DURATION_RE = re.compile(r"estimated duration:\s*([\d.]+)\s*sec")


def audio_cfg(cfg: dict | None) -> dict:
    return ((cfg or {}).get("audio") or {}) if isinstance(cfg, dict) else {}


def enabled(cfg: dict | None) -> bool:
    """Мост включается явно: `audio.voice_memos_bridge: true`. По умолчанию
    выключен — контейнер Диктофона это личный архив, и лить его в
    транскрибацию, граф и архив без решения владельца нельзя (критика GLM
    r1 по #522). Включённый молчит, если папки Диктофона на Mac нет."""
    return bool(audio_cfg(cfg).get("voice_memos_bridge", False))


def per_scan(cfg: dict | None) -> int:
    try:
        return max(1, int(audio_cfg(cfg).get("voice_memos_per_scan", DEFAULT_PER_SCAN)))
    except (TypeError, ValueError):
        return DEFAULT_PER_SCAN


def max_age_seconds(cfg: dict | None) -> float:
    """Окно моста в секундах: записи старше `audio.voice_memos_max_age_days`
    (14) — архив, а не встреча на импорт. Гейт закрывает и первый скан после
    включения, и повторный импорт после потери журнала: копия в done/ живёт
    `import_keep_days`, контейнер Диктофона — годами (DS I2 по #522)."""
    try:
        return max(1.0, float(audio_cfg(cfg).get("voice_memos_max_age_days", DEFAULT_MAX_AGE_DAYS))) * 86400.0
    except (TypeError, ValueError):
        return DEFAULT_MAX_AGE_DAYS * 86400.0


def is_dataless(st: os.stat_result) -> bool:
    """Выселенный iCloud плейсхолдер: размер есть, блоков на диске нет —
    copyfile запустил бы докачку внутри скана (GLM I2 по #522)."""
    return getattr(st, "st_blocks", 1) == 0 and st.st_size > 0


def source_dir(cfg: dict | None) -> pathlib.Path | None:
    raw = audio_cfg(cfg).get("voice_memos_dir") or DEFAULT_DIR
    p = pathlib.Path(str(raw)).expanduser()
    return p if p.is_dir() else None


def min_seconds(cfg: dict | None) -> float:
    try:
        return float(audio_cfg(cfg).get("voice_memos_min_seconds", DEFAULT_MIN_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_MIN_SECONDS


def duration_seconds(path: pathlib.Path) -> float | None:
    """Длительность по `afinfo` (штатный macOS); нет утилиты или не разобралось — None."""
    try:
        r = subprocess.run(["afinfo", str(path)], capture_output=True, text=True,
                           timeout=20, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = _DURATION_RE.search(r.stdout or "")
    return float(m.group(1)) if m else None


def _state_path(root: pathlib.Path) -> pathlib.Path:
    return root / "logs" / STATE_NAME


def load_state(root: pathlib.Path) -> dict:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(root: pathlib.Path, state: dict, *, now: float | None = None) -> None:
    """Атомарная запись журнала. tmp-имя уникально на процесс: два скана разом
    (приложение раз в 2 мин + ручной CLI) с общим tmp оставляли обрывок
    (GLM r1 по #522). Записи старше месяца выкидываем — иначе журнал растёт
    вечно; ключ «имя|размер|mtime» после этого просто переоценится."""
    now = time.time() if now is None else now
    fresh = {k: v for k, v in state.items()
             if not (isinstance(v, dict) and isinstance(v.get("at"), (int, float))
                     and now - v["at"] > STATE_KEEP_SECONDS)}
    p = _state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    finally:
        tmp.unlink(missing_ok=True)


def file_key(path: pathlib.Path, st: os.stat_result) -> str:
    return f"{path.name}|{st.st_size}|{int(st.st_mtime)}"


def _mtime_desc(p: pathlib.Path) -> float:
    try:
        return -p.stat().st_mtime
    except OSError:
        return 0.0


def _same_recording(inbox: pathlib.Path, name: str, dur: float | None) -> bool:
    """Тёзка того же размера — та же запись? Диктофон переиспользует имена, и
    совпадение байт в размере хоть и редко, но возможно (GLM r1 по #522):
    если у обеих известна длительность, сверяем её с допуском в секунду;
    неизвестна — верим размеру."""
    if dur is None:
        return True
    for cand in (inbox / name, inbox / "done" / name):
        if cand.is_file() and not cand.is_symlink():
            other = duration_seconds(cand)
            return other is None or abs(other - dur) <= 1.0
    return True


def _imported_size(inbox: pathlib.Path, name: str) -> int | None:
    """Размер файла с таким именем, который папка импорта уже видела: в корне,
    в done/ или в сайдкаре `.<имя>.imported.json` (копия могла быть удалена
    ретеншном, сайдкар живёт дольше)."""
    for cand in (inbox / name, inbox / "done" / name):
        try:
            if cand.is_symlink():
                continue            # ссылку скан не импортирует — она не «уже виденная запись» (DS M1)
            if cand.is_file():
                if cand.parent == inbox and (inbox / f".{name}{ERROR_MARKER_SUFFIX}").exists():
                    continue        # сбойная копия имя не держит: её снимут «Повторить»/удалением (GLM r1)
                return cand.stat().st_size
        except OSError:
            continue
    for side in (inbox / f".{name}.imported.json", inbox / "done" / f".{name}.imported.json"):
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            if isinstance(meta, dict) and isinstance(meta.get("size"), int):
                return int(meta["size"])
        except (OSError, ValueError):
            continue
    return None


def target_name(inbox: pathlib.Path, src: pathlib.Path, st: os.stat_result) -> str:
    """Имя копии: как у Диктофона («Новая запись 4.m4a»), пока оно свободно;
    тёзка другого размера получает штамп mtime — Диктофон переиспользует
    номера в имени между днями."""
    seen = _imported_size(inbox, src.name)
    if seen is None or seen == st.st_size:
        return src.name
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(st.st_mtime))
    return f"{src.stem}_{stamp}{src.suffix}"


def bridge(cfg: dict | None, inbox: pathlib.Path, root: pathlib.Path, *,
           now: float | None = None, dry: bool = False) -> dict:
    """Скопировать новые записи Диктофона в `inbox`. Возвращает сводку:
    copied — имена копий, short/fresh/seen — сколько пропущено и почему.
    Ничего в папке Диктофона не меняет и не удаляет."""
    summary: dict = {"copied": [], "short": 0, "fresh": 0, "seen": 0, "old": 0, "cloud": 0, "queued": 0, "source": None}
    if not enabled(cfg):
        return summary
    src_dir = source_dir(cfg)
    if src_dir is None or not inbox.is_dir():
        return summary
    summary["source"] = str(src_dir)
    now = time.time() if now is None else now
    state = load_state(root)
    limit = min_seconds(cfg)
    limit_per_scan = per_scan(cfg)
    max_age = max_age_seconds(cfg)
    changed = False
    try:
        entries = sorted(src_dir.iterdir(), key=_mtime_desc)
    except PermissionError:
        # Контейнер Диктофона закрыт TCC: без «Полного доступа к диску» у
        # приложения stat папки проходит, а листинг — «Operation not
        # permitted» (проверено на этом Mac 07.09). Не ошибка моста, а шаг
        # настройки — подсказать, но не каждые две минуты.
        last = state.get("_denied_at")
        last = last if isinstance(last, (int, float)) else 0.0
        summary["denied"] = (now - last) >= DENIED_HINT_SECONDS
        if summary["denied"] and not dry:
            state["_denied_at"] = now
            try:
                save_state(root, state)
            except OSError:
                pass
        return summary
    except OSError as e:
        summary.setdefault("errors", []).append(f"{src_dir}: {e}")
        return summary
    for f in entries:
        try:
            if f.name.startswith(".") or f.is_symlink() or not f.is_file() or f.suffix.lower() not in SUFFIXES:
                continue
            st = f.stat()
        except OSError:
            continue
        key = file_key(f, st)
        if key in state:
            summary["seen"] += 1
            continue
        age = now - st.st_mtime
        if age > max_age:
            summary["old"] += 1                    # архив Диктофона: не наш, без журнала и без afinfo
            continue
        if age < SETTLE_SECONDS or st.st_size == 0:
            summary["fresh"] += 1                  # синк ещё дописывает — в следующий раз
            continue
        if is_dataless(st):
            summary["cloud"] += 1                  # выселенный iCloud плейсхолдер: ждём докачки
            continue
        if len(summary["copied"]) >= limit_per_scan:
            summary["queued"] += 1                 # остальное — следующими тактами, без afinfo сейчас (DS I3)
            continue
        dur = duration_seconds(f)
        if dur is None and age < UNPARSED_GRACE_SECONDS:
            summary["fresh"] += 1                  # moov ещё не дописан — недо-файл в конвейер не тащим (DS I1)
            continue
        if dur is not None and dur < limit:
            state[key] = {"skipped": "short", "seconds": round(dur, 1), "at": now}
            summary["short"] += 1
            changed = True
            continue
        seen_size = _imported_size(inbox, f.name)
        if seen_size == st.st_size and _same_recording(inbox, f.name, dur):
            state[key] = {"skipped": "already in inbox", "at": now}
            summary["seen"] += 1
            changed = True
            continue
        name = target_name(inbox, f, st)
        if dry:
            summary["copied"].append(name)
            continue
        tmp = inbox / f".{name}.{uuid.uuid4().hex[:8]}.part"
        try:
            shutil.copyfile(f, tmp)               # без mtime: свежий .part не «брошенный»
            tmp.replace(inbox / name)
            # Заменили сбойную тёзку — её метка ошибки иначе спрячет от скана и новую копию
            (inbox / f".{name}{ERROR_MARKER_SUFFIX}").unlink(missing_ok=True)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            summary.setdefault("errors", []).append(f"{f.name}: {e}")
            continue
        state[key] = {"copied_to": name, "size": st.st_size, "seconds": dur, "at": now}
        summary["copied"].append(name)
        changed = True
    if changed and not dry:
        try:
            save_state(root, state, now=now)
        except OSError as e:
            summary.setdefault("errors", []).append(f"журнал: {e}")
    return summary


def describe(summary: dict) -> str:
    """Одна строка для статуса приложения и лога."""
    if summary.get("source") is None:
        return ""
    if summary.get("denied") is not None:
        return ("Диктофон → импорт: " + DENIED_HINT) if summary["denied"] else ""
    parts = []
    if summary["copied"]:
        parts.append(f"скопировано {len(summary['copied'])}: {', '.join(summary['copied'])}")
    if summary["short"]:
        parts.append(f"короче порога — {summary['short']}")
    if summary["fresh"]:
        parts.append(f"ещё синхронизируются — {summary['fresh']}")
    if summary.get("cloud"):
        parts.append(f"ещё в облаке (выселены iCloud) — {summary['cloud']}")
    if summary.get("queued"):
        parts.append(f"в очереди на следующие сканы — {summary['queued']}")
    if summary.get("errors"):
        parts.append("ошибки: " + "; ".join(summary["errors"]))
    return "Диктофон → импорт: " + (", ".join(parts) if parts else "нового нет")


if __name__ == "__main__":     # ручной прогон: python3 src/voice_memos_bridge.py <папка импорта> [--dry]
    from charoite_paths import resolve_root
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("укажи папку импорта")
    out = bridge({}, pathlib.Path(args[0]).expanduser(), resolve_root(__file__), dry="--dry" in sys.argv)
    print(describe(out) or "папки Диктофона на этом Mac нет (включи синхронизацию iCloud и открой Диктофон на Mac один раз)")
