#!/usr/bin/env python3
"""Импорт записанной встречи одним действием: файл → полный архив + граф.

Входы:
  аудио  .m4a .wav .mp3 .aif  — транскрибация GigaAM (src/transcribe_file)
  текст  .txt .md             — готовая расшифровка как есть
  сабы   .vtt .srt            — экспорт Zoom/Teams: таймкоды и ИМЕНА
                                спикеров сохраняются — диаризация не нужна

Дальше единый хвост конвейера: минутки+разбор+тезисы (retro_fill,
идемпотентно), обновление графа (graph_updater), раскладка в архив
встреч. Дата встречи — из mtime файла, точнее: --date/--time.

    .venv/bin/python scripts/import_meeting.py запись.m4a --date 2026-07-15
    .venv/bin/python scripts/import_meeting.py zoom.vtt --title "Планёрка"
    .venv/bin/python scripts/import_meeting.py --scan -- ~/Charoite_inbox
    .venv/bin/python scripts/import_meeting.py --prune -- ~/Charoite_inbox

Папка импорта (--scan): успешные файлы переезжают в done/ с сайдкаром
`.<имя>.imported.json` (когда импортирован, во что превратился, когда
удалить); сбойные остаются в корне с меткой `.<имя>.import-error` и больше
не пересканируются, пока метку не снимут (--retry-failed или кнопка
«Повторить» во вкладке «Внешняя запись»). Копия в done/ и аудио-«Исходник»
в архиве встречи удаляются через `audio.import_keep_days` (по умолчанию 2)
после импорта — --prune делает это отдельно, --scan — в конце каждого
прохода.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import graphs  # noqa: E402
import meeting_stamp  # noqa: E402
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

# ПОСЛЕ хука: config_loader тянет yaml, а рецепт вместо трейсбека обязан
# успеть встать до первого стороннего импорта (круг-1 по #418, DS+GLM+Codex).
from config_loader import load_user_or_example  # noqa: E402

import charoite_paths  # noqa: E402
import safe_write  # noqa: E402

AUDIO = {".m4a", ".wav", ".mp3", ".aif", ".aiff", ".caf"}
TEXT = {".txt", ".md"}
SUBS = {".vtt", ".srt"}


# Размеры, которыми потоковые писатели (ffmpeg в pipe, часть диктофонов)
# помечают «длину не знаю»: ноль и «все единицы».
RIFF_SIZE_UNKNOWN = {0, 0xFFFFFFFF}
# WAV без честного размера в заголовке готов, когда его перестали писать:
# столько секунд РАЗМЕР файла должен стоять на месте (аудит 16.08, п.4).
# Не mtime: провайдер синка трогает mtime без записи (файл завис бы
# навсегда), часы устройства впереди (то же), старый mtime сохранён при
# ещё идущем копировании (импорт половины) — круг-1 по PR #377, три головы.
# Память между сканами — скрытый сайдкар рядом с файлом.
WAV_SETTLE_SECONDS = 30

# Сколько дней копия исходника живёт в done/ после успешного импорта — и
# столько же аудио-«Исходник» в папке встречи. Срок отдельный от
# record_keep_days: внешняя запись (диктофон телефона, чужая запись звонка)
# приходит готовой, пересобирать её из done/ незачем, а держать вечно —
# нарушение обещания PRIVACY (карточка №166, 05.09).
IMPORT_KEEP_DAYS_DEFAULT = 2
ERROR_MARKER_SUFFIX = ".import-error"
IMPORTED_SIDECAR_SUFFIX = ".imported.json"


def _seen_marker(path: pathlib.Path) -> pathlib.Path:
    return path.with_name(f".{path.name}.import-seen")


def _size_settled(path: pathlib.Path, size: int) -> bool:
    """Размер не менялся WAV_SETTLE_SECONDS с момента, когда его впервые
    увидели таким. Первое наблюдение (или рост) пишет сайдкар и отвечает
    «ещё нет»; следующий скан сравнивает."""
    marker = _seen_marker(path)
    now = time.time()
    try:
        prev_size, prev_t = marker.read_text(encoding="ascii").split()
        if int(prev_size) == size:
            return now - float(prev_t) >= WAV_SETTLE_SECONDS
    except (OSError, ValueError):
        pass
    try:
        # 0600 явно, не по umask: папка импорта — у человека, не у демона
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(f"{size} {now:.0f}\n")
    except OSError:
        pass
    return False


def forget_seen_marker(path: pathlib.Path) -> None:
    """Файл импортирован или исчез — сайдкар больше не нужен."""
    try:
        _seen_marker(path).unlink()
    except OSError:
        pass


def error_marker(path: pathlib.Path) -> pathlib.Path:
    """Метка сбойного импорта рядом с файлом.

    Без неё сканер заново гонял STT по тому же файлу каждые две минуты,
    пока человек не уберёт его руками, а во вкладке не было чем отличить
    «ждёт» от «не вышло» (№166). Снимается --retry-failed или кнопкой
    «Повторить»; сам файл остаётся на месте — при ошибке ничего не удаляем.
    """
    return path.with_name(f".{path.name}{ERROR_MARKER_SUFFIX}")


def imported_sidecar(done_file: pathlib.Path) -> pathlib.Path:
    """Память о том, КОГДА файл импортирован и во что превратился.

    mtime у перенесённого файла — время записи (оно же штамп встречи), а
    ретеншну нужен момент импорта: по mtime вчерашняя запись, импортированная
    сегодня, улетела бы сразу.
    """
    return done_file.with_name(f".{done_file.name}{IMPORTED_SIDECAR_SUFFIX}")


def _write_json(path: pathlib.Path, data: dict) -> None:
    safe_write.write_text(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _read_json(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _report(path: str | None, data: dict) -> None:
    """Итог одного импорта для сканера (--result-json): штамп, стенограмма,
    копия исходника в архиве. Сканер кладёт это в сайдкар done/."""
    if path:
        _write_json(pathlib.Path(path), data)


def free_name(folder: pathlib.Path, name: str) -> pathlib.Path:
    """Свободное имя в done/: два Recording.m4a с телефона — две разные
    записи, и вторая не должна затирать копию первой (rename на POSIX
    перезаписывает молча)."""
    dest = folder / name
    stem, suffix = pathlib.Path(name).stem, pathlib.Path(name).suffix
    n = 1
    while dest.exists() or imported_sidecar(dest).exists():
        dest = folder / f"{stem}-{n}{suffix}"
        n += 1
    return dest


def import_keep_days(cfg: dict, override=None) -> float:
    """Срок жизни копии в done/ (`audio.import_keep_days`, дни).

    Не задан — 2, БЕЗ каскада из `record_keep_days`: каскад (круг 1 по #496)
    держался на «пересборка читает Исходник» — а она его не читает, зато
    чужая запись (диктофон, звонок) растягивалась бы до срока живых записей
    (14 дней в поле) вопреки просьбе владельца «удалять через два дня»
    (аудит GLM/DS 05.09). Кривое значение — не повод удалять сразу или не
    удалять никогда: говорим вслух и берём умолчание. Отрицательное — отказ:
    «удалить до импорта» не бывает. Ноль допустим — копия уходит первым же
    проходом. Бесконечность — как кривое: в JSON сайдкара она не проходит.
    """
    raw = override
    audio = cfg.get("audio") or {}
    if raw is None:
        raw = audio.get("import_keep_days")
    if raw is None:          # ключа нет или `import_keep_days:` пустой
        raw = IMPORT_KEEP_DAYS_DEFAULT
        if str(audio.get("record_keep_days", IMPORT_KEEP_DAYS_DEFAULT)) != str(IMPORT_KEEP_DAYS_DEFAULT):
            # До 0.70.1 срок наследовался от record_keep_days — сказать один раз
            # в лог, что теперь он свой (критика DS по #499)
            print(f"import_keep_days не задан — копии импорта живут {IMPORT_KEEP_DAYS_DEFAULT} дн. "
                  f"(record_keep_days={audio.get('record_keep_days')} на них больше не влияет; "
                  f"задайте audio.import_keep_days явно)")
    try:
        days = float(raw)
    except (TypeError, ValueError):
        print(f"import_keep_days: непонятное значение {raw!r} — беру {IMPORT_KEEP_DAYS_DEFAULT}")
        return float(IMPORT_KEEP_DAYS_DEFAULT)
    if math.isnan(days) or math.isinf(days):
        print(f"import_keep_days: непонятное значение {raw!r} — беру {IMPORT_KEEP_DAYS_DEFAULT}")
        return float(IMPORT_KEEP_DAYS_DEFAULT)
    if days < 0:
        sys.exit(f"import_keep_days не может быть отрицательным: {raw!r}")
    return days


def _archive_source_for(meta: dict, done_file: pathlib.Path,
                        graph: pathlib.Path | None) -> pathlib.Path | None:
    """Аудио-«Исходник» этой копии в архиве встречи.

    Путь пишет ребёнок в сайдкар; но ребёнок мог упасть после `cp -c` и до
    отчёта, а повтор импорта отчитывается без копии (GLM r1 по #496) — тогда
    ищем по штампу в графе, тем же поиском, что и импорт. Только аудио и
    только имя «Исходник…», которое писали сами.
    """
    candidates: list[pathlib.Path] = []
    archive = meta.get("archive_source")
    if isinstance(archive, str):
        candidates.append(pathlib.Path(archive))
    # Путь — подсказка, не истина: переименование встречи переносит всю
    # папку архива, и сохранённый путь мёртв; штамп находит папку по
    # meeting_id манифеста (DS аудит 05.09)
    if graph is not None and isinstance(meta.get("stamp"), str):
        found = archive_folder_for(graph, meta["stamp"])
        if found is not None:
            candidates.append(found / f"Исходник{done_file.suffix.lower()}")
    for candidate in candidates:
        if (candidate.suffix.lower() in AUDIO and candidate.name.startswith("Исходник")
                and not candidate.is_symlink() and candidate.is_file()):
            return candidate
    return None


def prune_done(folder: pathlib.Path, keep_days: float, *, now: float | None = None,
               graph: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Удалить из done/ копии, отслужившие срок, и их аудио-«Исходник» в архиве.

    Срок — от момента ИМПОРТА (поле delete_after сайдкара), не от mtime.
    Файл без сайдкара (импорт до этой версии) получает сайдкар «увидели
    сейчас» и живёт keep_days с этого момента: срок не зависит ни от ctime,
    ни от того, как файл попал в done/ (GLM r1 по #496). Сбойные файлы лежат
    в корне папки, а не в done/, — ретеншн их не видит по построению («при
    ошибке не удалять»). Текстовые исходники (txt/md/vtt/srt) в архиве не
    трогаем: чужая расшифровка, положенная человеком, голоса в ней нет.
    Два уборщика разом (хвост скана и --prune приложения) — норма: файл,
    исчезнувший из-под ног, просто пропускается и не считается.
    """
    done = folder / "done"
    if done.is_symlink() or not done.is_dir():
        return []
    now = time.time() if now is None else now
    removed: list[pathlib.Path] = []
    # Сайдкар без файла (копию убрали руками) — мусор, ничего не решает.
    # Моложе минуты не трогаем: скан пишет сайдкар ДО переноса, и второй
    # процесс (--prune приложения) успевал бы съесть живой (r3 по #496)
    for orphan in done.glob(f".*{IMPORTED_SIDECAR_SUFFIX}"):
        owner = done / orphan.name[1:-len(IMPORTED_SIDECAR_SUFFIX)]
        try:
            young = now - orphan.stat().st_mtime < 60
        except OSError:
            continue
        if not owner.exists() and not young:
            orphan.unlink(missing_ok=True)
    for f in sorted(done.iterdir()):
        if f.is_symlink() or not f.is_file() or f.name.startswith("."):
            continue
        sidecar = imported_sidecar(f)
        meta = _read_json(sidecar)
        deadline = None
        if meta is not None:
            try:
                deadline = float(meta.get("delete_after"))
            except (TypeError, ValueError):
                deadline = None
        if deadline is None:
            # Первый взгляд новой версии на старую копию: срок с этого момента
            try:
                _write_json(sidecar, {"legacy": True, "imported_at": now,
                                      "keep_days": keep_days,
                                      "delete_after": now + keep_days * 86400})
            except OSError as e:
                print(f"ретеншн импорта: сайдкар для {f.name} не записался: {e}")
            continue
        if now < deadline:
            continue
        try:
            f.unlink()
        except FileNotFoundError:
            continue        # второй уборщик успел первым — не наш счёт
        except OSError as e:
            print(f"ретеншн импорта: {f.name} не удалился: {e}")
            continue
        removed.append(f)
        print(f"ретеншн импорта: удалена копия {f.name}")
        src = _archive_source_for(meta or {}, f, graph)
        if src is not None:
            try:
                src.unlink()
                removed.append(src)
                print(f"ретеншн импорта: удалён аудио-исходник в архиве встречи {(meta or {}).get('stamp')}")
            except OSError as e:
                print(f"ретеншн импорта: исходник в архиве не удалился: {e}")
        try:
            sidecar.unlink()
        except OSError:
            pass
    return removed


def wav_complete(path: pathlib.Path, *, settle: bool = True) -> bool:
    """RIFF уже дописан до длины, объявленной в заголовке.

    Некоторые SAF/sync-провайдеры не умеют атомарный rename: тогда Android
    вынужден копировать запись сразу под конечным именем .wav. Первые 12 байт
    уже объявляют полный размер источника, поэтому растущий файл надёжно
    отличается от готового без таймеров и догадок по mtime.

    Заголовок без честного размера — не приговор файлу. Такие WAV читаются
    и импортировались годами; судить по их заголовку нельзя, а запирать
    запись в папке импорта навсегда — хуже, чем импортировать лишнее. Но и
    считать такой файл готовым в момент появления нельзя: пока его ещё
    копируют, импорт забрал бы половину записи (аудит 16.08). Критерий —
    покой размера между сканами (_size_settled); settle=False — для файла,
    который человек выбрал руками: он заведомо готов, ждать нечего.
    Оборотная сторона принята сознательно: писатель, замолчавший больше
    WAV_SETTLE_SECONDS посреди копирования, будет импортирован неполным —
    этого без его участия не отличить от готового файла.
    """
    try:
        st = path.stat()
        actual = st.st_size
        if actual < 44:
            return False
        with path.open("rb") as stream:
            header = stream.read(12)
    except OSError:
        return False
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return False
    riff = int.from_bytes(header[4:8], "little")
    if riff in RIFF_SIZE_UNKNOWN:
        return _size_settled(path, actual) if settle else True
    declared = riff + 8
    return declared >= 44 and actual >= declared


def settled_enough(path: pathlib.Path, *, settle_all: bool) -> bool:
    """Готов ли файл к импорту с точки зрения «его ещё пишут?».

    WAV судится по заголовку (или по покою размера, если заголовок без
    длины). Остальные форматы полноту не объявляют: с `settle_all` (тик
    слежения) любой файл обязан 30 с не менять размер — Finder-копия
    большого m4a или материализация из iCloud иначе брались бы на середине
    (критика GLM/DS r3 по #496). Без флага (кнопка «Обработать сейчас»,
    скан после дропа — копии вкладки опубликованы атомарно) — сразу.
    """
    if path.suffix.lower() == ".wav":
        return wav_complete(path)
    if not settle_all:
        return True
    try:
        return _size_settled(path, path.stat().st_size)
    except OSError:
        return False


def scan_candidates(folder: pathlib.Path, *, skip_failed: bool = True,
                    settle_all: bool = False) -> list[pathlib.Path]:
    """Поддерживаемые и уже полностью опубликованные файлы папки импорта.

    Файл с меткой ошибки прошлого импорта не берём: он ждёт «Повторить».
    """
    out = []
    for path in sorted(folder.iterdir()):
        # Симлинк в папке импорта — чужой файл в графе и в LLM-конвейере:
        # is_file() разыменовывает ссылку, поэтому проверка отдельно
        # (аудит 16.08, п.3). Папка импорта — для файлов, не для ссылок.
        if path.is_symlink():
            print(f"импорт: симлинк пропущен — {path.name}")
            continue
        if not path.is_file() or path.suffix.lower() not in (AUDIO | TEXT | SUBS):
            continue
        if skip_failed and error_marker(path).exists():
            print(f"импорт: {path.name} — прошлый импорт не удался, ждёт «Повторить»")
            continue
        if not settled_enough(path, settle_all=settle_all):
            continue
        out.append(path)
    return out


def postponed_files(folder: pathlib.Path, *, settle_all: bool = False) -> list[pathlib.Path]:
    """Файлы, отложенные до следующего скана: копирование ещё идёт.

    Нужны только чтобы сказать о них вслух. Молчаливый пропуск человек
    читает как «запись потерялась», и следующий его шаг — искать её руками.
    """
    return [
        path
        for path in sorted(folder.iterdir())
        if not path.is_symlink() and path.is_file()
        and path.suffix.lower() in (AUDIO | TEXT | SUBS)
        and not settled_enough(path, settle_all=settle_all)
    ]


# Временные файлы моложе этого возраста считаются живыми: копия вкладки
# (`.<имя>.<uuid>.part`) и отчёт ребёнка (`.<имя>.import-result.json`)
# при живом процессе не живут дольше минут; час — запас на гигабайт из iCloud.
TEMP_ORPHAN_AGE = 3600


def sweep_temporaries(folder: pathlib.Path, *, now: float | None = None) -> list[pathlib.Path]:
    """Убрать скрытые временные файлы без владельца.

    `.part` пишет вкладка (копия до атомарной публикации), отчёт ребёнка —
    скан; краш или Cmd-Q посреди копии оставляли их навсегда: скрытые, ни
    одним сканером не видимые гигабайты, при синкаемой папке — ещё и в
    iCloud (GLM/DS r3 по #496). Только по возрасту: живую копию не задеть.
    """
    now = time.time() if now is None else now
    removed: list[pathlib.Path] = []
    for p in list(folder.glob(".*.part")) + list(folder.glob(".*.import-result.json")):
        try:
            if p.is_symlink() or not p.is_file() or now - p.stat().st_mtime < TEMP_ORPHAN_AGE:
                continue
            p.unlink()
            removed.append(p)
            print(f"импорт: убран временный файл без владельца — {p.name}")
        except OSError:
            continue
    return removed


def _cfg() -> dict:
    return load_user_or_example(ROOT)


def clean_time(value: str) -> str:
    """Время встречи → ЧЧММ, как его пишет весь конвейер.

    Справка говорит «ЧЧММ», но человек пишет время так, как привык, —
    `08:44`. Раньше значение уходило в имя файла как есть, и на диске
    появлялась стенограмма `2026-08-03_08:44.md`: штамп у неё уже не
    четырёхзначный, а двоеточие в имени ломает и разбор имени, и половину
    инструментов, которые с этим файлом работают.
    """
    digits = re.sub(r"\D", "", value)
    if len(digits) == 3:            # «8:44» — ведущий ноль человек опускает
        digits = "0" + digits
    if len(digits) != 4 or int(digits[:2]) > 23 or int(digits[2:]) > 59:
        sys.exit(f"время встречи непонятно: {value!r} — нужно ЧЧММ, например 0844")
    return digits


def clean_date(value: str) -> str:
    """Дата встречи → ГГГГ-ММ-ДД. Разделитель человек ставит любой."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        sys.exit(f"дата встречи непонятна: {value!r} — нужно ГГГГ-ММ-ДД, например 2026-08-03")
    day = f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        sys.exit(f"такой даты не бывает: {value!r}")
    return day


def parse_subs(text: str) -> list[tuple[str, str, str]]:
    """.vtt/.srt → [(HH:MM, спикер, реплика)]; спикер может быть пустым.

    Понимает `<v Имя>текст`, `Имя: текст` и голые строки; srt-номера и
    служебные заголовки vtt отбрасываются.
    """
    out: list[tuple[str, str, str]] = []
    cur_time = ""
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if re.fullmatch(r"\d+", line):  # srt-номер блока
            continue
        m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})[.,]\d{1,3}\s*-->", line)
        if m:
            cur_time = f"{int(m.group(1)):02d}:{m.group(2)}"
            continue
        speaker = ""
        vm = re.match(r"<v\s+([^>]+)>\s*(.*)", line)
        if vm:
            speaker, line = vm.group(1).strip(), vm.group(2).strip()
        else:
            sm = re.match(r"([A-ZА-ЯЁ][\w .\-]{1,30}):\s+(.+)", line)
            if sm:
                speaker, line = sm.group(1).strip(), sm.group(2).strip()
        line = re.sub(r"</?[^>]+>", "", line).strip()
        if line:
            out.append((cur_time, speaker, line))
    return out


def source_mark(name: str, size: int | None) -> str:
    """«Recording.m4a (123456 Б)» — исходник в шапке стенограммы импорта.

    Размер отличает две РАЗНЫЕ записи с одним именем: диктофон на телефоне
    экспортирует всё как Recording.m4a, и по одному имени вторая запись той
    же минуты считалась бы повтором первой (круг-1 по PR #388, Sonnet и
    DeepSeek)."""
    return f"{name} ({size} Б)" if size is not None else name


_SOURCE_HEAD_RE = re.compile(r"— (?:импорт|запись) (?P<tail>.+?)\s*$")


def same_source(head: str, name: str, size: int | None) -> bool:
    """Шапка стенограммы — про этот исходник? Хвост шапки сравнивается с
    именем БЕЗ разбора регэкспом: имя вроде «memo (7 Б).m4a» иначе теряло
    хвост за «размер» (круг-2 по PR #388, Codex и Sonnet). Хвост равен имени
    (шапка без размера, до 23.08) — повтор; равен «имя (N Б)» — повтор, если
    размер совпал или неизвестен."""
    m = _SOURCE_HEAD_RE.search(head)
    if not m:
        return False
    tail = m.group("tail")
    if tail == name:
        return True
    if not tail.startswith(name + " (") or not tail.endswith(" Б)"):
        return False
    theirs = tail[len(name) + 2:-3]
    return theirs.isdigit() and (size is None or int(theirs) == size)


def subs_to_transcript(entries: list[tuple[str, str, str]], stamp: str, src: str) -> str:
    lines = [f"# Встреча {stamp} — импорт {src}", ""]
    prev_key = None
    for tm, sp, txt in entries:
        key = (tm, sp)
        if key != prev_key:
            head = f"**{sp or 'Голос'}** [{tm or '—'}]:"
            lines.append(head)
            prev_key = key
        lines.append(txt)
    return "\n".join(lines) + "\n"


def archive_folder_for(graph: pathlib.Path, stamp: str) -> pathlib.Path | None:
    """Папка архива ИМЕННО этой встречи — в этом графе или соседних графах vault.

    Имя папки: «ГГГГ-ММ-ДД ЧЧ-ММ — Тема» (meeting_archive) или старый формат
    «<штамп> — Тема». Раньше глоб шёл по одной дате и брал первую папку дня —
    исходник второй встречи ложился к первой, а при занятом имени молча не
    копировался вовсе (аудит DeepSeek 16.08).
    """
    # Время целиком («12-58 — », «12-58-12 — ») и meeting_id манифеста: минутный
    # глоб брал папку соседки той же минуты (круг-1 по PR #388, Codex).
    head = f"{stamp[:10]} {meeting_stamp.archive_time(stamp)}"
    patterns = (f"{head} — *", f"{stamp} — *")
    for pat in patterns:
        for f in sorted(graph.parent.glob(f"*/Встречи-архив/{pat}")) + sorted(graph.glob(f"Встречи-архив/{pat}")):
            if not f.is_dir():
                continue
            try:
                owner = json.loads((f / "meeting.meta.json").read_text(encoding="utf-8")).get("meeting_id")
            except (OSError, ValueError, AttributeError):
                owner = None
            if owner is None or owner == stamp:
                return f
    return None


def title_slug(title: str) -> str:
    """Тема импорта в имени файла — через тот же страховщик, что и конвейер:
    «Демо live» без него давал `…_Демо_live.md`, который stamp_of читает
    как копию `_live` (DS r4 по #455)."""
    slug = re.sub(r"[^\wА-Яа-яЁё-]+", "_", title).strip("_")[:40]
    return meeting_stamp.guard_slug(slug) if slug else slug


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="аудио/текст/субтитры записанной встречи")
    ap.add_argument("--date", help="дата встречи ГГГГ-ММ-ДД, разделитель любой "
                                   "(по умолчанию mtime файла)")
    ap.add_argument("--time", help="время встречи ЧЧММ или ЧЧ:ММ "
                                   "(по умолчанию mtime файла)")
    ap.add_argument("--title", default="", help="тема встречи для архива")
    ap.add_argument("--scan", action="store_true",
                    help="файл = ПАПКА-вход: импортировать все поддерживаемые "
                         "файлы из неё, успешные переносить в done/")
    ap.add_argument("--retry-failed", action="store_true",
                    help="со --scan: снять метки ошибок и попробовать сбойные ещё раз")
    ap.add_argument("--settle-all", action="store_true",
                    help="со --scan: любой файл должен 30 с не менять размер "
                         "(тик слежения: чужие копии в папку не атомарны)")
    ap.add_argument("--prune", action="store_true",
                    help="файл = ПАПКА-вход: только удалить из done/ копии, "
                         "отслужившие import_keep_days")
    ap.add_argument("--keep-days", default=None,
                    help="срок жизни копии в done/ вместо audio.import_keep_days")
    ap.add_argument("--result-json", default=None,
                    help=argparse.SUPPRESS)   # служебный: итог импорта для сканера
    return ap


def import_stamp(tdir: pathlib.Path, minute: str, src_name: str,
                 seconds: str, src_size: int | None = None) -> tuple[str, pathlib.Path | None]:
    """Штамп импорта и найденный повтор.

    Повтор — та же ЗАПИСЬ, а не та же минута: шапка стенограммы импорта
    хранит имя исходника («— импорт <файл>»), по нему и узнаём. Раньше
    повтором считалась любая встреча той же минуты — вторая запись с
    телефона в ту же минуту (или рядом со встречей демона) молча уезжала
    в done/ без импорта (аудит 17.08, карточка №41). Чужая встреча в этой
    минуте — импортируем под посекундным штампом (секунды — от mtime записи,
    «00» при времени от человека), как демон при крэш-рестарте: граф даст
    ей свой ключ (meeting_stamp.graph_key). Занятые секунды — суффикс «-N».
    """
    taken = False
    for p in sorted(tdir.glob(f"{minute}*.md")) if tdir.is_dir() else ():
        s = meeting_stamp.stamp_of(p.stem)
        if s is None or meeting_stamp.minute_of(s) != minute:
            continue
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                head = fh.readline()
        except OSError:
            head = ""
        # Шапка текста/субтитров — «— импорт <файл> (<размер> Б)», аудио
        # (transcribe_file) — «— запись …»: повтор узнаём по обеим, с
        # размером, когда он есть.
        if same_source(head, src_name, src_size):
            return s, p
        taken = True
    if not taken:
        return minute, None
    stamp = f"{minute}{seconds}"
    n = 1
    while any(meeting_stamp.stamp_of(p.stem) == stamp for p in tdir.glob(f"{stamp}*.md")):
        stamp = f"{minute}{seconds}-{n}"
        n += 1
    return stamp, None


def main() -> None:
    # Импорт пишет стенограмму, записи и архивную папку — те же данные, что
    # демон, и с теми же правами: только владельцу (аудит DeepSeek 16.08).
    charoite_paths.harden_umask()
    ap = build_parser()
    args = ap.parse_args()

    if args.prune:
        folder = pathlib.Path(args.file).expanduser()
        if not folder.is_dir():
            sys.exit(f"--prune ждёт папку: {folder}")
        cfg = _cfg()
        removed = prune_done(folder, import_keep_days(cfg, args.keep_days),
                             graph=graphs.graph_dir(cfg))
        copies = sum(1 for p in removed if p.parent.name == "done")
        print(f"ретеншн импорта: удалено копий — {copies}"
              + (f", аудио-исходников в архиве — {len(removed) - copies}" if len(removed) > copies else ""))
        temporaries = sweep_temporaries(folder)
        if temporaries:
            print(f"ретеншн импорта: + временных без владельца — {len(temporaries)}")
        return

    if args.scan:
        folder = pathlib.Path(args.file).expanduser()
        if not folder.is_dir():
            sys.exit(f"--scan ждёт папку: {folder}")
        cfg = _cfg()
        keep_days = import_keep_days(cfg, args.keep_days)
        graph = graphs.graph_dir(cfg)
        if args.retry_failed:
            for marker in folder.glob(f".*{ERROR_MARKER_SUFFIX}"):
                marker.unlink(missing_ok=True)
        done = folder / "done"
        # done/ — только настоящий каталог: симлинк увёл бы импортированные
        # файлы в чужую папку, а битая ссылка ронила бы весь скан на
        # mkdir (круг-1 по PR #377, Codex).
        if done.is_symlink() or (done.exists() and not done.is_dir()):
            sys.exit(f"{done}: ожидается обычный каталог, а не ссылка или файл")
        done.mkdir(exist_ok=True)
        for marker in folder.glob(".*.import-seen"):
            if not (folder / marker.name[1:-len(".import-seen")]).exists():
                forget_seen_marker(folder / marker.name[1:-len(".import-seen")])
        # Метка ошибки без файла: человек убрал сбойный файл руками, и
        # следующий файл с тем же именем (диктофон зовёт всё Recording.m4a)
        # считался бы сбойным навсегда (GLM r1 по #496).
        for marker in folder.glob(f".*{ERROR_MARKER_SUFFIX}"):
            if not (folder / marker.name[1:-len(ERROR_MARKER_SUFFIX)]).exists():
                marker.unlink(missing_ok=True)
        sweep_temporaries(folder)
        todo = scan_candidates(folder, settle_all=args.settle_all)
        postponed = postponed_files(folder, settle_all=args.settle_all)
        for waiting in postponed:
            print(f"ещё копируется, отложен до следующего скана: {waiting.name}")
        if postponed:
            # Машинный маркер для приложения (догон через 35 с): фраза выше —
            # для человека, и её правят; маркер — контракт (GLM r5 по #496)
            print(f"postponed={len(postponed)}")
        if not todo:
            print("готовых файлов нет — нечего импортировать")
            prune_done(folder, keep_days, graph=graph)
            return
        failed = 0
        for f in todo:
            print(f"=== импорт {f.name} ===")
            # Один файл, исчезнувший из-под ног во время транскрибации, или
            # полный том не должны ронять очередь и уборку (GLM r1 по #496)
            try:
                failed += 0 if _scan_one(f, done, keep_days) else 1
            except OSError as e:
                failed += 1
                print(f"импорт {f.name}: {e} — файл пропущен до следующего скана")
        prune_done(folder, keep_days, graph=graph)
        # Сбой хотя бы одного файла — ненулевой код: приложение судит по нему,
        # и «импорт завершён» при красной строке в списке — ложь (DS r1 по #496)
        if failed:
            sys.exit(1)
        return

    src = pathlib.Path(args.file).expanduser()
    if not src.exists():
        sys.exit(f"нет файла: {src}")
    # Прямой импорт: файл выбрал человек — ждать покоя размера незачем
    if src.suffix.lower() == ".wav" and not wav_complete(src, settle=False):
        sys.exit(f"WAV ещё дописывается или повреждён: {src}")

    # записи с телефона: note_*/diary_* — это НЕ встречи, а голосовые заметки
    # и дневник; транскрибируем и отдаём конвейеру заметок
    base = src.name.lower()
    if base.startswith(("note_", "diary_")) and src.suffix.lower() in AUDIO:
        import_voice_note(src, diary=base.startswith("diary_"))
        _report(args.result_json, {"kind": "diary" if base.startswith("diary_") else "note",
                                   "source": src.name, "size": src.stat().st_size})
        return

    cfg = _cfg()
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    tdir.mkdir(parents=True, exist_ok=True)

    mt = dt.datetime.fromtimestamp(src.stat().st_mtime)
    day = clean_date(args.date) if args.date else f"{mt:%Y-%m-%d}"
    hhmm = clean_time(args.time) if args.time else f"{mt:%H%M}"
    stamp, already = import_stamp(tdir, f"{day}_{hhmm}", src.name,
                                  f"{mt:%S}" if not args.time else "00",
                                  src.stat().st_size)
    if already is not None:
        # Код 0, а не sys.exit(строка): выход строкой возвращает 1, скан
        # считал повтор ОТКАЗОМ и не переносил файл в done/ — тот застревал
        # в папке импорта навсегда и пересканировался каждые две минуты
        # (найдено 06.08: три файла с телефона молотились по кругу).
        # Повтор — это успех: встреча уже в архиве.
        print(f"встреча {already.name} уже импортирована — повтор не нужен")
        old_stamp = meeting_stamp.stamp_of(already.stem)
        old_folder = archive_folder_for(graphs.graph_dir(cfg) or pathlib.Path(""), old_stamp) if old_stamp else None
        old_src = old_folder / f"Исходник{src.suffix.lower()}" if old_folder is not None else None
        _report(args.result_json, {"kind": "meeting", "source": src.name,
                                   "size": src.stat().st_size, "repeat": True,
                                   "stamp": old_stamp, "transcript": str(already),
                                   "archive_source": str(old_src) if old_src is not None and old_src.exists() else None})
        return
    if stamp != f"{day}_{hhmm}":
        print(f"в минуте {day}_{hhmm} уже есть другая встреча — импорт под штампом {stamp}")
    slug = title_slug(args.title)
    tpath = tdir / (f"{stamp}_{slug}.md" if slug else f"{stamp}.md")

    ext = src.suffix.lower()
    if ext in AUDIO:
        # транскрибация пишет transcripts/<stamp>.md сама; время отдаём
        # целиком — с секундами и суффиксом у соседки в занятой минуте,
        # иначе она ложилась в минутный файл поверх первой (круг-1 по
        # PR #388, DeepSeek).
        r = subprocess.run([sys.executable, str(CODE / "src" / "transcribe_file.py"),
                            str(src), stamp[11:], day])
        if r.returncode != 0:
            sys.exit("транскрибация не удалась")
        tpath = tdir / f"{stamp}.md"
        if slug:
            titled = tdir / f"{stamp}_{slug}.md"
            tpath.rename(titled)
            tpath = titled
    elif ext in SUBS:
        from vocabulary import apply as vapply, compile_rules
        entries = parse_subs(vapply(src.read_text(encoding="utf-8", errors="ignore"),
                                    compile_rules(cfg)))
        if not entries:
            sys.exit("в субтитрах не нашлось реплик")
        safe_write.write_text(tpath, subs_to_transcript(entries, stamp, source_mark(src.name, src.stat().st_size)))
        speakers = sorted({sp for _, sp, _ in entries if sp})
        print(f"стенограмма из субтитров: {tpath}"
              + (f" · спикеры: {', '.join(speakers)}" if speakers else ""))
    elif ext in TEXT:
        from vocabulary import apply as vapply, compile_rules
        body = vapply(src.read_text(encoding="utf-8", errors="ignore").strip(),
                      compile_rules(cfg))
        if len(body) < 200:
            sys.exit("текст слишком короткий для встречи")
        safe_write.write_text(tpath, f"# Встреча {stamp} — импорт {source_mark(src.name, src.stat().st_size)}\n\n{body}\n")
        print(f"стенограмма из текста: {tpath}")
    else:
        sys.exit(f"не понимаю формат {ext}: жду {sorted(AUDIO | TEXT | SUBS)}")

    # единый хвост: граф → минутки/разбор/тезисы/архив (идемпотентно)
    print("— обновляю граф…")
    graph_run = subprocess.run(
        [sys.executable, str(CODE / "src" / "graph_updater.py"), str(tpath)])
    # = graph_updater.EXIT_NO_SPEECH. Именно копия, не импорт: верхний уровень
    # модуля тянет requests/llm_health — дорого и с сайд-эффектами для обвязки.
    no_speech, no_graph = 3, 4
    if graph_run.returncode == no_graph:
        # graph_updater.EXIT_NO_GRAPH: модель не дала разбор — узлов графа нет,
        # архив со стенограммой собран. Хвост (минутки/разбор) всё равно
        # пробуем: retro_fill сам переживёт лежащую модель.
        print("⚠️ модель не дала разбор — граф не обновлён; архив собран, "
              "повторите обработку позже (rebuild_transcript или «Повторить обработку»)")
    if graph_run.returncode == no_speech:
        # В записи нет речи — генерить минутки и разбор не из чего. Раньше
        # хвост шёл дальше, и retro_fill гонял LLM по всему бэклогу встреч
        # из-за трёхсекундной пустышки: импорт случайного обрывка стоил
        # минуты полной загрузки машины (найдено 06.08 на тестовом файле).
        print(f"готово: пустая запись {stamp} — стенограмма сохранена, конвейер не нужен")
        _report(args.result_json, {"kind": "meeting", "source": src.name,
                                   "size": src.stat().st_size, "no_speech": True,
                                   "stamp": stamp, "transcript": str(tpath)})
        return
    print("— догенерирую минутки/разбор/тезисы и раскладываю архив…")
    subprocess.run([sys.executable, str(CODE / "src" / "retro_fill.py")])
    # исходник — рядом с материалами встречи (APFS-клон: без лишнего места)
    graph = graphs.graph_dir(cfg) or pathlib.Path("")
    folder = archive_folder_for(graph, stamp)
    archived: pathlib.Path | None = None
    if folder is None:
        print(f"папка архива встречи {stamp} не найдена — исходник в архив не скопирован")
    else:
        dest = folder / f"Исходник{src.suffix.lower()}"
        if not dest.exists():
            subprocess.run(["cp", "-c", str(src), str(dest)],
                           capture_output=True)
        if dest.exists():
            archived = dest
    print(f"готово: встреча {stamp} в архиве и графе")
    _report(args.result_json, {"kind": "meeting", "source": src.name,
                               "size": src.stat().st_size, "stamp": stamp,
                               "transcript": str(tpath),
                               "archive_source": str(archived) if archived else None})


def _scan_one(f: pathlib.Path, done: pathlib.Path, keep_days: float) -> bool:
    """Один файл папки импорта: ребёнок → done/ с сайдкаром (True) или метка
    ошибки (False)."""
    result_path = f.with_name(f".{f.name}.import-result.json")
    result_path.unlink(missing_ok=True)
    try:
        # Вывод ребёнка собираем, а не пускаем в свой stdout целиком:
        # приложение читает наш stdout через трубу, и мегабайт логов
        # транскрибации подвесил бы импорт на полном буфере. Наружу —
        # хвост, в метку ошибки — тоже хвост.
        r = subprocess.run([sys.executable, __file__, str(f),
                            "--result-json", str(result_path)],
                           capture_output=True, text=True, errors="replace")
        lines = [ln for ln in (r.stdout + "\n" + r.stderr).splitlines() if ln.strip()]
        for ln in lines[-8:]:
            print(f"  {ln}")
        if r.returncode == 0:
            dest = free_name(done, f.name)
            imported_at = time.time()
            meta = _read_json(result_path) or {}
            # Сайдкар ДО переноса: обрыв между ними оставлял копию без штампа,
            # и аудио-«Исходник» в архиве жил бы вечно; сайдкар без файла —
            # сирота, её убирает уборка (GLM r2 по #496)
            _write_json(imported_sidecar(dest), {
                **meta,
                "source": f.name,
                "imported_at": imported_at,
                "keep_days": keep_days,
                "delete_after": imported_at + keep_days * 86400,
            })
            f.rename(dest)
            forget_seen_marker(f)
            error_marker(f).unlink(missing_ok=True)
            return True
        _write_json(error_marker(f), {
            "failed_at": time.time(),
            "code": r.returncode,
            "message": (lines[-1] if lines else f"код {r.returncode}")[:300],
            "tail": lines[-20:],
        })
        # Маркер покоя размера НЕ трогаем: он и есть доказательство, что файл
        # отстоял свои 30 с; сброс заставлял «Повторить» ждать заново
        # (GLM r2 и DS r2 по #496 — против моей правки по DS r1)
        print(f"импорт {f.name} не удался (код {r.returncode}) — файл остаётся, "
              f"повтор: кнопка «Повторить» или --retry-failed")
        return False
    finally:
        result_path.unlink(missing_ok=True)


def import_voice_note(src: pathlib.Path, diary: bool) -> None:
    """Голосовая заметка/дневник с телефона → тот же конвейер, что диктовка.

    m4a → wav 16k (afconvert, штатный macOS) → STT → dictate_note --text:
    модель чистит, вытаскивает идеи и задачи, кладёт в граф или Дневник.
    """
    import subprocess as sp
    import tempfile

    sys.path.insert(0, str(CODE / "src"))
    import soundfile as sf
    from stt import STT

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = pathlib.Path(f.name)
    try:
        conv = sp.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                       str(src), str(wav)], capture_output=True, text=True)
        if conv.returncode != 0:
            sys.exit(f"afconvert не смог: {conv.stderr.strip()[:200]}")
        audio, sr = sf.read(wav, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        text = STT(_cfg()).transcribe(audio, sr).strip()
    finally:
        wav.unlink(missing_ok=True)
    if len(text) < 3:
        sys.exit("в записи не расслышалось ни слова")
    mode = ["--diary"] if diary else []
    r = sp.run([sys.executable, str(CODE / "src" / "dictate_note.py"), "--text", *mode],
               input=text, text=True)
    if r.returncode != 0:
        sys.exit("конвейер заметки завершился с ошибкой")
    print(f"голосовая {'дневниковая ' if diary else ''}заметка обработана: {src.name}")


if __name__ == "__main__":
    main()
