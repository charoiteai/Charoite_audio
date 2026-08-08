#!/usr/bin/env python3
"""Переименовать встречу: одна команда — новая тема везде, где живёт старая.

Тему встречи придумывает модель по стенограмме, и она бывает мимо: «Обсуждение
обновлений» вместо «Инцидент загрузки». Поменять её руками значило обойти пять мест —
файлы в transcripts/, папку в «Встречи-архив» (и ссылки внутри неё), копии в
«Документация/Стенограммы встреч», заголовок заметки графа и статус для
приложения. Пропущенное место расходится с остальными навсегда.

    .venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "Инцидент загрузки"
    .venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "Инцидент загрузки" --yes

Без --yes печатает план и ничего не трогает. Ссылки вида [[Встречи/штамп]] не
рвутся: имя заметки графа — это штамп, тема в нём не участвует.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or
                    pathlib.Path(__file__).resolve().parent.parent).expanduser()
sys.path.insert(0, str(ROOT / "src"))

from meeting_archive import ARCHIVE_DIR, _safe  # noqa: E402

# Хвосты производных файлов. Слаг стоит между штампом и хвостом:
# 2026-08-03_1130_Обновление_ОС_разбор.md
SUFFIXES = ("_minutes", "_hints", "_разбор", "_ревизия_claude", "_live", "_спикеры")

STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}")


def short_stamp(raw: str) -> str:
    """«2026-08-03_113012» и «2026-08-03_1130_Тема» → «2026-08-03_1130»."""
    m = STAMP_RE.match(raw.strip())
    if not m:
        sys.exit(f"это не штамп встречи: {raw!r} — нужен вид 2026-08-03_1130")
    return m.group(0)


def pretty_and_slug(title: str) -> tuple[str, str]:
    """Тема для папки (с пробелами) и для имени файла (с подчёркиваниями)."""
    pretty = _safe(title.replace("_", " ").strip())
    if not pretty:
        sys.exit("новая тема пуста")
    slug = re.sub(r"[,;:!?.]", "", pretty).replace(" ", "_")[:50]
    return pretty, slug


def resolve_graph(cfg: dict) -> pathlib.Path:
    """SUFLER_GRAPH_DIR перекрывает конфиг — как во всём конвейере.

    Найдено аудитом: скрипт читал граф только из config.yaml, и прогон в
    тестовом окружении (SUFLER_GRAPH_DIR на временный граф) переименовывал
    файлы transcripts/, а папку архива и заметку молча искал в РАБОЧЕМ
    графе. «Готово» при полдела — и рука в проде, куда тестовый запуск не
    должен дотягиваться вовсе.
    """
    raw = os.environ.get("SUFLER_GRAPH_DIR") or cfg["sufler"]["graph_dir"]
    return pathlib.Path(raw).expanduser()


def retitled(name: str, stamp: str, slug: str) -> str | None:
    """Новое имя файла — или None, если файл темы не касается.

    Три случая, когда тему в имя можно и нужно положить:
    - слаг уже есть — стоит между штампом и известным хвостом, меняем его;
    - главный файл вообще без темы: «2026-08-03_1130.md» — дописываем;
    - главный файл с посекундным штампом: «2026-08-03_113012.md». Свежий
      конвейер называет такие сам (graph_updater.parse_stem/retitle), но
      встречи, разобранные до фикса, остались голыми — переименовываем в
      короткий штамп со слагом, как назвал бы сам конвейер.

    ПРОИЗВОДНЫЕ посекундные файлы («…113012_hints.md») не трогаем: темы в их
    именах нет, а по полному стему их находит конвейер.
    """
    stem, dot, ext = name.partition(".md")
    if not dot or ext:                      # .md.live.json и прочие — не трогаем
        return None
    if not stem.startswith(stamp):
        return None
    rest = stem[len(stamp):]
    if rest == "" or re.fullmatch(r"\d\d", rest):
        return f"{stamp}_{slug}.md"         # главный файл без темы
    m = re.match(r"^_(?!\d)(.+)$", rest)
    if not m:
        return None
    body = m.group(1)
    suffix = next((s for s in SUFFIXES if body.endswith(s)), "")
    old_slug = body[: len(body) - len(suffix)] if suffix else body
    if not old_slug or old_slug == slug:
        return None
    return f"{stamp}_{slug}{suffix}.md"


def plan(graph: pathlib.Path, tdir: pathlib.Path, stamp: str,
         pretty: str, slug: str) -> dict:
    """Что переименуется и что перепишется. Считается без единой записи."""
    moves: list[tuple[pathlib.Path, pathlib.Path]] = []
    taken: set[pathlib.Path] = set()
    for folder in (tdir, graph / "Документация" / "Стенограммы встреч"):
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            new = retitled(f.name, stamp, slug)
            if not new:
                continue
            target = f.with_name(new)
            # Два кандидата на одно имя (короткий и посекундный главные файлы
            # разом) или уже занятое имя — второго не двигаем: затирать файл
            # встречи переименованием нельзя ни при каком раскладе.
            if target in taken or target.exists():
                print(f"пропуск: {f.name} — имя {new} уже занято")
                continue
            taken.add(target)
            moves.append((f, target))

    day, hhmm = stamp[:10], f"{stamp[11:13]}-{stamp[13:15]}"
    arch_prefix = f"{day} {hhmm} "
    old_folder = next((d for d in sorted((graph / ARCHIVE_DIR).iterdir())
                       if d.is_dir() and d.name.startswith(arch_prefix)), None) \
        if (graph / ARCHIVE_DIR).exists() else None
    new_folder = (old_folder.with_name(f"{day} {hhmm} — {pretty}")
                  if old_folder is not None else None)
    if old_folder is not None and old_folder == new_folder:
        old_folder = new_folder = None

    return {"moves": moves, "old_folder": old_folder, "new_folder": new_folder,
            "note": graph / "Встречи" / f"{stamp}.md"}


def apply(p: dict, graph: pathlib.Path, stamp: str, pretty: str) -> None:
    for old, new in p["moves"]:
        old.rename(new)

    old_folder, new_folder = p["old_folder"], p["new_folder"]
    if old_folder is not None and new_folder is not None:
        old_folder.rename(new_folder)
        # Внутри папки файлы ссылаются на неё по имени: «Подробнее:
        # [[Встречи-архив/<папка>/Минутки|…]]» и заголовок Саммари. Оставить
        # старое имя — оставить битые ссылки ровно там, куда человек смотрит.
        for f in new_folder.glob("*.md"):
            text = f.read_text(encoding="utf-8")
            if old_folder.name in text:
                f.write_text(text.replace(old_folder.name, new_folder.name),
                             encoding="utf-8")

    # Манифест meeting.meta.json — JSON с темой внутри; текстовая замена по
    # *.md его не видит, а телефоны берут карточку именно из него. Пересборка
    # из свежих Markdown возвращает манифесту правду (и создаёт его старым
    # встречам, которых архивация до манифестов не застала).
    folder = new_folder
    if folder is None and (graph / ARCHIVE_DIR).exists():
        day, hhmm = stamp[:10], f"{stamp[11:13]}-{stamp[13:15]}"
        prefix = f"{day} {hhmm} "
        folder = next((d for d in sorted((graph / ARCHIVE_DIR).iterdir())
                       if d.is_dir() and d.name.startswith(prefix)), None)
    if folder is not None:
        try:
            from meeting_archive import _write_manifest
            _write_manifest(folder, stamp, pretty)
        except Exception as e:  # noqa: BLE001 — манифест производный
            print(f"манифест не пересобрался: {e}")

    note = p["note"]
    if note.exists():
        text = note.read_text(encoding="utf-8")
        text, n = re.subn(rf"(?m)^# Встреча {re.escape(stamp)}.*$",
                          f"# Встреча {stamp} — {pretty}", text, count=1)
        # Старая тема — в aliases: по ней встречу уже искали и находили,
        # обрывать этот след переименованием нельзя.
        m = re.search(r'(?m)^aliases:\s*\[(.*)\]$', text)
        if m and f'"{pretty}"' not in m.group(1):
            joined = (m.group(1).strip() + ", " if m.group(1).strip() else "")
            text = text[:m.start()] + f'aliases: [{joined}"{pretty}"]' + text[m.end():]
        if n or m:
            note.write_text(text, encoding="utf-8")

    # Статус для приложения: transcript_path указывает на переименованный файл.
    # Через разбор JSON, не заменой подстроки: json.dumps по умолчанию
    # экранирует кириллицу (О…), и путь со старой темой в сыром тексте
    # файла просто не находится.
    status_dir = ROOT / "logs" / "meeting-status"
    if status_dir.exists():
        import json
        mapping = {str(old): str(new) for old, new in p["moves"]}
        for sf in status_dir.glob(f"{stamp}*.json"):
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tp = str(data.get("transcript_path", ""))
            if tp in mapping:
                data["transcript_path"] = mapping[tp]
                sf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    try:  # оглавление архива хранит имена папок — пересобрать лучше, чем врать
        from meeting_archive import _rebuild_index
        _rebuild_index(graph)
    except Exception as e:  # noqa: BLE001 — индекс вспомогателен
        print(f"оглавление архива не пересобралось: {e}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    do_apply = "--yes" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__.strip().splitlines()[0]
                 + "\nиспользование: rename_meeting.py <штамп> <новая тема> [--yes]")
    stamp = short_stamp(args[0])
    pretty, slug = pretty_and_slug(args[1])

    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = resolve_graph(cfg)
    tdir = ROOT / cfg["log"]["transcripts_dir"]

    p = plan(graph, tdir, stamp, pretty, slug)
    if not p["moves"] and p["old_folder"] is None and not p["note"].exists():
        sys.exit(f"встреча {stamp} не нашлась ни в transcripts/, ни в графе")

    for old, new in p["moves"]:
        print(f"файл:  {old.name}  →  {new.name}")
    if p["old_folder"] is not None:
        print(f"архив: {p['old_folder'].name}  →  {p['new_folder'].name}")
    if p["note"].exists():
        print(f"заметка: {p['note'].name} — заголовок и aliases")

    if not do_apply:
        print("\nЭто был план. Применить: --yes")
        return
    apply(p, graph, stamp, pretty)
    print(f"готово: {stamp} — «{pretty}»")


if __name__ == "__main__":
    main()
