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

import json
import os
import pathlib
import re
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import graphs  # noqa: E402

import charoite_paths  # noqa: E402
import safe_write  # noqa: E402
import meeting_stamp  # noqa: E402
from meeting_archive import ARCHIVE_DIR, _safe  # noqa: E402

# Хвосты производных файлов. Слаг стоит между штампом и хвостом:
# 2026-08-03_1130_Обновление_ОС_разбор.md
# Один список на все стороны: свой экземпляр без `_debrief` не переименовывал
# «…_debrief.md» и путал титулованную производную с темой (аудит 30.08, luna)
SUFFIXES = meeting_stamp.AUX_SUFFIXES

STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}(?:-\d+)?)?")


def short_stamp(raw: str) -> str:
    """«2026-08-03_113012» → «2026-08-03_113012», «2026-08-03_1130_Тема» → «2026-08-03_1130».

    Секунды, если человек их дал, сохраняются: так выбирается вторая встреча
    той же минуты (карточка №39). Без секунд — минутный штамп.
    """
    m = STAMP_RE.match(raw.strip())
    if not m:
        sys.exit(f"это не штамп встречи: {raw!r} — нужен вид 2026-08-03_1130")
    return m.group(0)


def resolve_key(tdir: pathlib.Path, stamp: str, graph: pathlib.Path | None = None) -> str:
    """Ключ встречи в графе по штампу от человека.

    Посекундный штамп — ключ той самой встречи (`graph_key` по её главному
    файлу). Минутный — владелец минуты; если в минуте несколько встреч без
    владельца, просим уточнить секундами, а не переименовываем первую
    попавшуюся.
    """
    mains = [p for p in tdir.glob(f"{meeting_stamp.minute_of(stamp)}*.md")
             if (s := meeting_stamp.stamp_of(p.stem)) and meeting_stamp.minute_of(s) == meeting_stamp.minute_of(stamp)] \
        if tdir.is_dir() else []
    if stamp != meeting_stamp.minute_of(stamp):
        mine = [p for p in mains if meeting_stamp.stamp_of(p.stem) == stamp]
        return meeting_stamp.graph_key(tdir, mine[0].stem, graph) if mine else stamp
    keys = {meeting_stamp.graph_key(tdir, p.stem, graph) for p in mains}
    if len(keys) > 1 and stamp not in keys:
        sys.exit(f"в минуте {stamp} несколько встреч ({', '.join(sorted(keys))}) — "
                 "укажите штамп с секундами")
    return stamp


def pretty_and_slug(title: str) -> tuple[str, str]:
    """Тема для папки (с пробелами) и для имени файла (с подчёркиваниями)."""
    pretty = _safe(title.replace("_", " ").strip())
    if not pretty:
        sys.exit("новая тема пуста")
    slug = meeting_stamp.guard_slug(re.sub(r"[,;:!?.]", "", pretty).replace(" ", "_")[:50])
    return pretty, slug


def resolve_graph(cfg: dict) -> pathlib.Path:
    """SUFLER_GRAPH_DIR перекрывает конфиг — как во всём конвейере.

    Найдено аудитом: скрипт читал граф только из config.yaml, и прогон в
    тестовом окружении (SUFLER_GRAPH_DIR на временный граф) переименовывал
    файлы transcripts/, а папку архива и заметку молча искал в РАБОЧЕМ
    графе. «Готово» при полдела — и рука в проде, куда тестовый запуск не
    должен дотягиваться вовсе.
    """
    return graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")


def retitled(name: str, stamp: str, slug: str) -> str | None:
    """Новое имя файла — или None, если файл темы не касается.

    Три случая, когда тему в имя можно и нужно положить:
    - слаг уже есть — стоит между штампом и известным хвостом, меняем его;
    - главный файл вообще без темы: «2026-08-03_1130.md» — дописываем;
    - главный файл с посекундным штампом: «2026-08-03_113012.md». Свежий
      конвейер называет такие сам (graph_updater.parse_stem/retitle), но
      встречи, разобранные до фикса, остались голыми — переименовываем в
      короткий штамп со слагом, как назвал бы сам конвейер.

    ПРОИЗВОДНЫЕ посекундные файлы («…113012_hints.md») получают то же имя,
    что дал бы им сам конвейер (`graph_updater.retitle`: `{bare}_*` →
    `{stamp}_{slug}{suffix}`). Раньше их не трогали — «по полному стему их
    находит конвейер», — но после переименования главного файла полный стем
    встречи другой: архив, облачный контекст и повторные прогоны ищут файлы
    по стему главного файла и посекундные производные больше не видели
    (второе мнение DeepSeek по партии 16.08). Незнакомый хвост не трогаем.
    """
    stem, dot, ext = name.partition(".md")
    if not dot or ext:                      # .md.live.json и прочие — не трогаем
        return None
    if not stem.startswith(stamp):
        return None
    rest = stem[len(stamp):]
    if rest == "" or re.fullmatch(r"\d\d", rest):
        return f"{stamp}_{slug}.md"         # главный файл без темы
    m_sec = re.fullmatch(r"\d\d(_.+)", rest)
    if m_sec:                               # посекундный производный: «12_hints»
        suffix = m_sec.group(1)
        return f"{stamp}_{slug}{suffix}.md" if suffix in SUFFIXES else None
    m = re.match(r"^_(?!\d)(.+)$", rest)
    if not m:
        return None
    body = m.group(1)
    if f"_{body}" in SUFFIXES:              # производный посекундного ключа: «…125812_hints»
        return f"{stamp}_{slug}_{body}.md"
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
    # Файлы ИМЕННО этой встречи: главные файлы, чей ключ графа — наш stamp,
    # и их производные. Минутный ключ раньше захватывал голые посекундные
    # файлы соседки («…125812.md» → «…1258_Тема.md»), а настоящий владелец
    # пропускался как «имя занято» (круг-1 по PR #388, Codex).
    minute = meeting_stamp.minute_of(stamp)
    mains = {meeting_stamp.stamp_of(p.stem): p
             for p in (tdir.glob(f"{minute}*.md") if tdir.is_dir() else ())
             if meeting_stamp.stamp_of(p.stem)}
    mine = {b for b, p in mains.items() if meeting_stamp.graph_key(tdir, p.stem, graph) == stamp}
    # Главный файл прежних версий с темой на служебное слово («…_Демо_live.md»):
    # по имени — копия, по содержимому — встреча. Такой файл rename и лечит:
    # новое имя идёт через guard_slug (DS r4 по #455).
    legacy = legacy_mains(tdir, minute)
    legacy_names: set[str] = set()
    for bare, p in legacy.items():
        # посекундный legacy-файл зовут по минуте, как и всё остальное:
        # единственный кандидат минуты — он (DS r5)
        if (bare == stamp or (stamp == minute and len(legacy) == 1)) and bare not in mains:
            mains[bare] = p
            mine.add(bare)
            legacy_names.add(p.name)
            print(f"{p.name} — главный по содержимому (источника рядом нет): имя лечится")
    if stamp == minute and tdir.is_dir():
        # Бесхозные посекундные производные («…113012_hints.md» без главного
        # файла «…113012») — владельца минуты: так их оставлял конвейер до
        # наката темы.
        docs = graph / "Документация" / "Стенограммы встреч"
        for f in tdir.glob(f"{minute}[0-9][0-9]_*.md"):
            b = f.name[:17]
            if b in mains:
                continue
            # След соседки в графе — заметка под её ключом или копии её
            # файлов в Документации: тогда производная её, а не бесхозная
            # (круг-2 по PR #388, Sonnet).
            if (graph / "Встречи" / f"{b}.md").exists() or \
                    meeting_stamp.files_with_stamp(docs, b, suffix=".md"):
                continue             # с границей штампа: «…812-1_*» — не её копии
            mine.add(b)
    for folder in (tdir, graph / "Документация" / "Стенограммы встреч"):
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            if not any(f.name.startswith(b) and not f.name[len(b):len(b) + 1].isdigit()
                       and not re.match(r"-\d", f.name[len(b):]) for b in mine):
                continue
            # копия главного в Документации носит то же имя — тоже главный (DS r5)
            new = f"{stamp}_{slug}.md" if f.name in legacy_names else retitled(f.name, stamp, slug)
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

    day, hhmm = stamp[:10], meeting_stamp.archive_time(stamp)
    old_folder = archive_folder(graph, stamp)
    new_folder = (old_folder.with_name(f"{day} {hhmm} — {pretty}")
                  if old_folder is not None else None)
    if old_folder is not None and old_folder == new_folder:
        old_folder = new_folder = None

    return {"moves": moves, "old_folder": old_folder, "new_folder": new_folder,
            "note": meeting_stamp.find_note(graph, stamp, tdir) or graph / "Встречи" / f"{stamp}.md"}


def legacy_mains(tdir: pathlib.Path, minute: str) -> dict[str, pathlib.Path]:
    """Главные файлы прежних версий, чья тема кончается служебным словом:
    `stamp_of` их не узнаёт, но начинаются они с «# Встреча », а файла-источника
    (имя без хвоста) рядом нет — значит, это не копия. Штамп → путь."""
    found: dict[str, pathlib.Path] = {}
    if not tdir.is_dir():
        return found
    names = {p.stem for p in tdir.glob("*.md")}
    for p in tdir.glob(f"{minute}*.md"):
        if meeting_stamp.stamp_of(p.stem):
            continue
        parts = meeting_stamp.decompose(p.stem)
        if not parts or not parts[1]:
            continue
        low = p.stem.lower()
        aux = next((a for a in meeting_stamp.AUX_SUFFIXES if low.endswith(a)), "")
        if not aux or p.stem[:-len(aux)] in names:
            continue                        # копия живого файла — не встреча
        try:
            with p.open("rb") as fh:
                head = fh.read(200).decode("utf-8", errors="ignore")
        except OSError:
            continue
        if head.lstrip().startswith("# Встреча "):
            found[parts[0]] = p
    return found


def archive_folder(graph: pathlib.Path, stamp: str) -> pathlib.Path | None:
    """Папка архива этой встречи: время в имени целиком («12-58 — », «12-58-12 — »),
    и не чужая по манифесту — у второй встречи той же минуты своя папка."""
    root = graph / ARCHIVE_DIR
    if not root.exists():
        return None
    head = f"{stamp[:10]} {meeting_stamp.archive_time(stamp)}"
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d.name == head or d.name.startswith(head + " ")):
            continue
        try:
            owner = json.loads((d / "meeting.meta.json").read_text(encoding="utf-8")).get("meeting_id")
        except (OSError, ValueError, AttributeError):
            owner = None
        if owner is None or owner == stamp:
            return d
    return None


BRAIN = "http://127.0.0.1:8100"


def brain_rename(stamp: str, pretty: str) -> str:
    """Новая тема — и в фактах памяти Чароита (brain /rename, карточка №41).
    brain выключен — сказать, как повторить, а не молчать."""
    try:
        import requests
        r = requests.post(f"{BRAIN}/rename", json={"meeting": stamp, "title": pretty}, timeout=60)
        text = (r.json() or {}).get("text", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
        return text if r.status_code == 200 else f"отказ ({r.status_code}): {text[:160]}"
    except Exception as e:  # noqa: BLE001
        return (f"недоступна ({type(e).__name__}) — тема в памяти осталась старой; повторить: "
                f"curl -X POST {BRAIN}/rename -H 'content-type: application/json' "
                f"-d '{{\"meeting\":\"{stamp}\",\"title\":\"{pretty}\"}}'")


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
                # tmp+replace: write_text усекает файл до нуля ДО записи, и обрыв
                # (полный том iCloud, kill) оставлял пустую заметку (аудит 30.08, GLM)
                safe_write.write_text(f, text.replace(old_folder.name, new_folder.name))

    # Манифест meeting.meta.json — JSON с темой внутри; текстовая замена по
    # *.md его не видит, а телефоны берут карточку именно из него. Пересборка
    # из свежих Markdown возвращает манифесту правду (и создаёт его старым
    # встречам, которых архивация до манифестов не застала).
    folder = new_folder
    if folder is None:
        folder = archive_folder(graph, stamp)
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
            safe_write.write_text(note, text)   # единственный экземпляр заметки встречи

    # Статус для приложения: transcript_path указывает на переименованный файл.
    # Через разбор JSON, не заменой подстроки: json.dumps по умолчанию
    # экранирует кириллицу (О…), и путь со старой темой в сыром тексте
    # файла просто не находится.
    status_dir = ROOT / "logs" / "meeting-status"
    if status_dir.exists():
        import json
        mapping = {str(old): str(new) for old, new in p["moves"]}
        # Глоб по минутному префиксу намеренно: файл статуса назван по ЖИВОЙ
        # стенограмме с секундами (2026-08-03_113012.json), а стамп встречи
        # минутный; чью встречу файл описывает, решает transcript_path.
        for sf in status_dir.glob(f"{stamp}*.json"):
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            tp = str(data.get("transcript_path", ""))
            if tp in mapping:
                data["transcript_path"] = mapping[tp]
                safe_write.write_text(sf, json.dumps(data, ensure_ascii=False))

    try:  # оглавление архива хранит имена папок — пересобрать лучше, чем врать
        from meeting_archive import _rebuild_index
        _rebuild_index(graph)
    except Exception as e:  # noqa: BLE001 — индекс вспомогателен
        print(f"оглавление архива не пересобралось: {e}")


def main() -> None:
    # Переименование переписывает стенограмму и узлы графа: права как у
    # конвейера, а не по umask вызывающего (аудит DeepSeek 16.08).
    charoite_paths.harden_umask()
    args = [a for a in sys.argv[1:] if a != "--yes"]
    do_apply = "--yes" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__.strip().splitlines()[0]
                 + "\nиспользование: rename_meeting.py <штамп> <новая тема> [--yes]")
    pretty, slug = pretty_and_slug(args[1])

    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = resolve_graph(cfg)
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    stamp = resolve_key(tdir, short_stamp(args[0]), graph)

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
    print(f"память Чароита: {brain_rename(stamp, pretty)}")
    print(f"готово: {stamp} — «{pretty}»")


if __name__ == "__main__":
    main()
