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
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402

AUDIO = {".m4a", ".wav", ".mp3", ".aif", ".aiff", ".caf"}
TEXT = {".txt", ".md"}
SUBS = {".vtt", ".srt"}


def wav_complete(path: pathlib.Path) -> bool:
    """RIFF уже дописан до длины, объявленной в заголовке.

    Некоторые SAF/sync-провайдеры не умеют атомарный rename: тогда Android
    вынужден копировать запись сразу под конечным именем .wav. Первые 12 байт
    уже объявляют полный размер источника, поэтому растущий файл надёжно
    отличается от готового без таймеров и догадок по mtime.
    """
    try:
        actual = path.stat().st_size
        if actual < 44:
            return False
        with path.open("rb") as stream:
            header = stream.read(12)
    except OSError:
        return False
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return False
    declared = int.from_bytes(header[4:8], "little") + 8
    return declared >= 44 and actual >= declared


def scan_candidates(folder: pathlib.Path) -> list[pathlib.Path]:
    """Поддерживаемые и уже полностью опубликованные файлы папки импорта."""
    out = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (AUDIO | TEXT | SUBS):
            continue
        if path.suffix.lower() == ".wav" and not wav_complete(path):
            continue
        out.append(path)
    return out


def _cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="аудио/текст/субтитры записанной встречи")
    ap.add_argument("--date", help="дата встречи ГГГГ-ММ-ДД (по умолчанию mtime файла)")
    ap.add_argument("--time", help="время ЧЧММ (по умолчанию mtime файла)")
    ap.add_argument("--title", default="", help="тема встречи для архива")
    ap.add_argument("--scan", action="store_true",
                    help="файл = ПАПКА-вход: импортировать все поддерживаемые "
                         "файлы из неё, успешные переносить в done/")
    args = ap.parse_args()

    if args.scan:
        folder = pathlib.Path(args.file).expanduser()
        if not folder.is_dir():
            sys.exit(f"--scan ждёт папку: {folder}")
        done = folder / "done"
        done.mkdir(exist_ok=True)
        todo = scan_candidates(folder)
        if not todo:
            print("готовых файлов нет — нечего импортировать")
            return
        for f in todo:
            print(f"=== импорт {f.name} ===")
            r = subprocess.run([sys.executable, __file__, str(f)])
            if r.returncode == 0:
                f.rename(done / f.name)
        return

    src = pathlib.Path(args.file).expanduser()
    if not src.exists():
        sys.exit(f"нет файла: {src}")
    if src.suffix.lower() == ".wav" and not wav_complete(src):
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
    day = args.date or f"{mt:%Y-%m-%d}"
    hhmm = args.time or f"{mt:%H%M}"
    stamp = f"{day}_{hhmm}"
    slug = re.sub(r"[^\wА-Яа-яЁё-]+", "_", args.title).strip("_")[:40]
    tpath = tdir / (f"{stamp}_{slug}.md" if slug else f"{stamp}.md")
    if tpath.exists() or (tdir / f"{stamp}.md").exists():
        sys.exit(f"встреча {stamp} уже импортирована: {tpath} — повтор не нужен")

    ext = src.suffix.lower()
    if ext in AUDIO:
        # транскрибация пишет transcripts/<stamp>.md сама
        r = subprocess.run([sys.executable, str(ROOT / "src" / "transcribe_file.py"),
                            str(src), hhmm, day])
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
        tpath.write_text(subs_to_transcript(entries, stamp, src.name), encoding="utf-8")
        speakers = sorted({sp for _, sp, _ in entries if sp})
        print(f"стенограмма из субтитров: {tpath}"
              + (f" · спикеры: {', '.join(speakers)}" if speakers else ""))
    elif ext in TEXT:
        from vocabulary import apply as vapply, compile_rules
        body = vapply(src.read_text(encoding="utf-8", errors="ignore").strip(),
                      compile_rules(cfg))
        if len(body) < 200:
            sys.exit("текст слишком короткий для встречи")
        tpath.write_text(f"# Встреча {stamp} — импорт {src.name}\n\n{body}\n",
                         encoding="utf-8")
        print(f"стенограмма из текста: {tpath}")
    else:
        sys.exit(f"не понимаю формат {ext}: жду {sorted(AUDIO | TEXT | SUBS)}")

    # единый хвост: граф → минутки/разбор/тезисы/архив (идемпотентно)
    print("— обновляю граф…")
    subprocess.run([sys.executable, str(ROOT / "src" / "graph_updater.py"), str(tpath)])
    print("— догенерирую минутки/разбор/тезисы и раскладываю архив…")
    subprocess.run([sys.executable, str(ROOT / "src" / "retro_fill.py")])
    # исходник — рядом с материалами встречи (APFS-клон: без лишнего места)
    graph = pathlib.Path(str((cfg.get("sufler") or {}).get("graph_dir", ""))).expanduser()
    day = stamp[:10]
    for folder in sorted(graph.parent.glob(f"*/Встречи-архив/{day}*")) + \
                  sorted(graph.glob(f"Встречи-архив/{day}*")):
        dest = folder / f"Исходник{src.suffix.lower()}"
        if not dest.exists():
            subprocess.run(["cp", "-c", str(src), str(dest)],
                           capture_output=True)
        break
    print(f"готово: встреча {stamp} в архиве и графе")


def import_voice_note(src: pathlib.Path, diary: bool) -> None:
    """Голосовая заметка/дневник с телефона → тот же конвейер, что диктовка.

    m4a → wav 16k (afconvert, штатный macOS) → STT → dictate_note --text:
    модель чистит, вытаскивает идеи и задачи, кладёт в граф или Дневник.
    """
    import subprocess as sp
    import tempfile

    sys.path.insert(0, str(ROOT / "src"))
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
    r = sp.run([sys.executable, str(ROOT / "src" / "dictate_note.py"), "--text", *mode],
               input=text, text=True)
    if r.returncode != 0:
        sys.exit("конвейер заметки завершился с ошибкой")
    print(f"голосовая {'дневниковая ' if diary else ''}заметка обработана: {src.name}")


if __name__ == "__main__":
    main()
