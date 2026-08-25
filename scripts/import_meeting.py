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
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
from config_loader import load_user_or_example  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import charoite_paths  # noqa: E402

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


def scan_candidates(folder: pathlib.Path) -> list[pathlib.Path]:
    """Поддерживаемые и уже полностью опубликованные файлы папки импорта."""
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
        if path.suffix.lower() == ".wav" and not wav_complete(path):
            continue
        out.append(path)
    return out


def postponed_files(folder: pathlib.Path) -> list[pathlib.Path]:
    """Файлы, отложенные до следующего скана: копирование ещё идёт.

    Нужны только чтобы сказать о них вслух. Молчаливый пропуск человек
    читает как «запись потерялась», и следующий его шаг — искать её руками.
    """
    return [
        path
        for path in sorted(folder.iterdir())
        if not path.is_symlink() and path.is_file()
        and path.suffix.lower() == ".wav" and not wav_complete(path)
    ]


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

    if args.scan:
        folder = pathlib.Path(args.file).expanduser()
        if not folder.is_dir():
            sys.exit(f"--scan ждёт папку: {folder}")
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
        todo = scan_candidates(folder)
        for waiting in postponed_files(folder):
            print(f"ещё копируется, отложен до следующего скана: {waiting.name}")
        if not todo:
            print("готовых файлов нет — нечего импортировать")
            return
        for f in todo:
            print(f"=== импорт {f.name} ===")
            r = subprocess.run([sys.executable, __file__, str(f)])
            if r.returncode == 0:
                f.rename(done / f.name)
                forget_seen_marker(f)
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
        return import_voice_note(src, diary=base.startswith("diary_"))

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
        return
    if stamp != f"{day}_{hhmm}":
        print(f"в минуте {day}_{hhmm} уже есть другая встреча — импорт под штампом {stamp}")
    slug = re.sub(r"[^\wА-Яа-яЁё-]+", "_", args.title).strip("_")[:40]
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
        tpath.write_text(subs_to_transcript(entries, stamp, source_mark(src.name, src.stat().st_size)), encoding="utf-8")
        speakers = sorted({sp for _, sp, _ in entries if sp})
        print(f"стенограмма из субтитров: {tpath}"
              + (f" · спикеры: {', '.join(speakers)}" if speakers else ""))
    elif ext in TEXT:
        from vocabulary import apply as vapply, compile_rules
        body = vapply(src.read_text(encoding="utf-8", errors="ignore").strip(),
                      compile_rules(cfg))
        if len(body) < 200:
            sys.exit("текст слишком короткий для встречи")
        tpath.write_text(f"# Встреча {stamp} — импорт {source_mark(src.name, src.stat().st_size)}\n\n{body}\n",
                         encoding="utf-8")
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
        return
    print("— догенерирую минутки/разбор/тезисы и раскладываю архив…")
    subprocess.run([sys.executable, str(CODE / "src" / "retro_fill.py")])
    # исходник — рядом с материалами встречи (APFS-клон: без лишнего места)
    graph = graphs.graph_dir(cfg) or pathlib.Path("")
    folder = archive_folder_for(graph, stamp)
    if folder is None:
        print(f"папка архива встречи {stamp} не найдена — исходник в архив не скопирован")
    else:
        dest = folder / f"Исходник{src.suffix.lower()}"
        if not dest.exists():
            subprocess.run(["cp", "-c", str(src), str(dest)],
                           capture_output=True)
    print(f"готово: встреча {stamp} в архиве и графе")


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
