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

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

AUDIO = {".m4a", ".wav", ".mp3", ".aif", ".aiff", ".caf"}
TEXT = {".txt", ".md"}
SUBS = {".vtt", ".srt"}


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
        todo = [p for p in sorted(folder.iterdir())
                if p.suffix.lower() in (AUDIO | TEXT | SUBS)]
        if not todo:
            print("папка пуста — нечего импортировать")
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


if __name__ == "__main__":
    main()
