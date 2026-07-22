"""Архив встреч для Finder: папка «дата — название», внутри вся документация.

Структура (в iCloud-вольте, рядом с графом — синкается на все устройства):
    <граф>/Встречи-архив/
        _ОГЛАВЛЕНИЕ.md
        2026-07-20 — Планирование релизов и задач на август/
            Стенограмма.md · Минутки.md · Подсказки и ответы.md
            Разбор.md · Ревизия Claude.md · Голоса и спикеры.md
            Граф.md   ← вики-ссылка на заметку встречи (все связи там)

Вызывается из graph_updater после каждой встречи; повторный вызов
до-подхватывает файлы, появившиеся позже (ревизия Опуса, диаризация).
Миграция всей истории: .venv/bin/python src/meeting_archive.py --all
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat as _stat
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

ARCHIVE_DIR = "Встречи-архив"
# суффикс исходника → человеческое имя в папке встречи
NICE = [
    ("_minutes.md", "Минутки.md"),
    ("_hints.md", "Подсказки и ответы.md"),
    ("_разбор.md", "Разбор.md"),
    ("_ревизия_claude.md", "Ревизия Claude.md"),
    ("_спикеры.md", "Голоса и спикеры.md"),
    ("_live.md", "Черновик (live).md"),
]


def _safe(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()[:80]


def _obsidian_url(graph: pathlib.Path, rel_note: str) -> str:
    """obsidian://open на заметку. Имя вольта — из конфига Obsidian (папка,
    содержащая граф); фолбэк — родитель графа."""
    import json
    import urllib.parse
    vault = graph.parent.name
    try:
        cfg = json.loads((pathlib.Path.home() /
                          "Library/Application Support/obsidian/obsidian.json").read_text())
        for v in cfg.get("vaults", {}).values():
            vp = pathlib.Path(v.get("path", ""))
            if vp in graph.parents or vp == graph:
                vault = vp.name
                rel_note = str(pathlib.Path(graph.name) / rel_note.split(graph.name + "/", 1)[-1]) \
                    if not rel_note.startswith(graph.name) else rel_note
                break
    except Exception:  # noqa: BLE001
        pass
    q = urllib.parse.quote
    return f"obsidian://open?vault={q(vault)}&file={q(rel_note)}"


def _write_opener(path: pathlib.Path, url: str):
    """Кликабельный запуск obsidian:// из Finder. .webloc для не-HTTP схем
    macOS открывать отказывается (-10400) — .command работает всегда."""
    path.write_text(f'#!/bin/bash\nopen "{url}"\n', encoding="utf-8")
    path.chmod(0o755)


def _excluded(graph: pathlib.Path) -> set[str]:
    """Stamp'ы, исключённые из архива руками: Встречи-архив/_исключено.md,
    по строке «2026-07-17_1029 — причина» (тесты звука, демо-зачитки)."""
    f = graph / ARCHIVE_DIR / "_исключено.md"
    if not f.exists():
        return set()
    return set(re.findall(r"\d{4}-\d{2}-\d{2}_\d{4}", f.read_text(encoding="utf-8")))


def archive_meeting(graph: pathlib.Path, tdir: pathlib.Path, stamp: str, title: str) -> pathlib.Path | None:
    """Собирает/обновляет папку встречи; возвращает её путь (None — исключена)."""
    if stamp in _excluded(graph):
        return None
    pretty = (title or "").replace("_", " ").strip()
    if not pretty:  # безымянная: время в имени, иначе встречи дня слипнутся
        pretty = f"встреча {stamp[11:13]}:{stamp[13:15]}"
    folder = graph / ARCHIVE_DIR / f"{stamp[:10]} — {_safe(pretty)}"
    folder.mkdir(parents=True, exist_ok=True)
    for f in sorted(tdir.glob(f"{stamp}*.md")):
        dest = "Стенограмма.md"
        for suf, nice in NICE:
            if f.name.endswith(suf):
                dest = nice
                break
        shutil.copy2(f, folder / dest)
    obs_url = _obsidian_url(graph, f"{graph.name}/Встречи/{stamp}")
    (folder / "Граф.md").write_text(
        f"---\ntype: ссылка\nдата: {stamp}\n---\n"
        f"# Граф этой встречи\n\n"
        f"[Открыть заметку встречи в Obsidian]({obs_url}) — дальше «Локальный граф» "
        f"покажет все связи (люди, системы, решения).\n\n"
        f"Внутри Obsidian: [[Встречи/{stamp}]] · оглавление проекта [[_MOC]]\n",
        encoding="utf-8",
    )
    # двойной клик в Finder → Obsidian на заметке встречи (Граф.md открывался текстом)
    _write_opener(folder / "Открыть в Obsidian.command", obs_url)
    _derive_extras(folder)
    _gen_summary(folder)
    _rebuild_index(graph)
    _unhide(graph / ARCHIVE_DIR)
    return folder


def _history_context(folder: pathlib.Path) -> str:
    """История для саммари: Ядра (хроника до даты встречи) + 2 прошлых саммари.

    Для перегенерации старых встреч будущее не утекает в прошлое: строки
    хроники ядра позже даты встречи отсекаются, саммари берутся только более
    ранние (сортировка имён папок = сортировка дат).
    """
    date_cut = folder.name[:10]
    parts: list[str] = []
    cores = folder.parent.parent / "Ядра"
    if cores.exists():
        for p in sorted(cores.glob("*.md")):
            if p.name.startswith("_"):
                continue
            text = p.read_text(encoding="utf-8")
            # формат хроники: «- [[Встречи/2026-07-20_1053]] — событие»
            hist = [ln for ln in text.splitlines()
                    if (m := re.search(r"- \[\[Встречи/(\d{4}-\d{2}-\d{2})", ln))
                    and m.group(1) <= date_cut]
            if hist:
                # хроника пишется newest-first — берём ВЕРХНИЕ 3 (ближайшие к дате
                # встречи), а не hist[-3:], где лежат самые старые события
                parts.append(f"Ядро «{p.stem}»:\n" + "\n".join(hist[:3]))
    prev = [p for p in sorted(folder.parent.iterdir())
            if p.is_dir() and p.name < folder.name and (p / "Саммари.md").exists()]
    for p in prev[-2:]:
        parts.append(f"Саммари встречи {p.name}:\n"
                     + (p / "Саммари.md").read_text(encoding="utf-8")[:1200])
    return "\n\n".join(parts)[:3500]


def _gen_summary(folder: pathlib.Path, force: bool = False):
    """Саммари.md — выжимка встречи на минуту чтения (первое, что открывают).

    Формат по практикам минуток: суть одной строкой → решили → поручения
    (кто/что/срок) → открытое. 100-300 слов, списки, без таблиц.
    """
    out = folder / "Саммари.md"
    if out.exists() and not force:
        return
    src_parts: list[str] = []
    for name, cap in (("Минутки.md", 3500), ("Тезисы.md", 1500),
                      ("Разбор.md", 2000), ("Стенограмма.md", 4000)):
        f = folder / name
        if f.exists():
            text = f.read_text(encoding="utf-8")
            # у стенограммы важнее конец (итоги), у остальных — начало
            src_parts.append(f"=== {name} ===\n" +
                             (text[-cap:] if name == "Стенограмма.md" else text[:cap]))
    if not src_parts:
        return
    history = _history_context(folder)
    hist_block = (
        "\n\n=== История (Ядра и прошлые встречи) — ТОЛЬКО для раздела "
        "«Связь с прошлыми встречами» ===\n" + history) if history else ""
    hist_tpl = (
        "\n\n## Связь с прошлыми встречами\n"
        "(1-3 пункта «- **тема** — было: … (DD.MM) → сегодня: …» — ТОЛЬКО темы, "
        "которых сегодняшняя встреча реально касалась: продвижение, подтверждение "
        "или отмена прошлой договорённости. Нет пересечений — пропусти раздел)"
    ) if history else ""
    try:
        import requests
        r = requests.post("http://127.0.0.1:11434/api/chat", json={
            "model": "qwen3.6:35b-a3b", "stream": False, "think": False,
            "messages": [
                {"role": "system", "content":
                    "Ты делаешь выжимку рабочей встречи для быстрого чтения. По-русски, "
                    "сухо, только факты из материалов. БЕЗ markdown-таблиц — только "
                    "списки «- …» с жирным ключом. 100-300 слов."},
                {"role": "user", "content":
                    "Материалы встречи:\n\n" + "\n\n".join(src_parts) + hist_block + "\n\n"
                    "Составь саммари строго по шаблону:\n"
                    "**Суть одной строкой:** …\n\n"
                    "## О чём говорили\n(2-4 пункта «- **тема** — что по ней», не проза)\n\n"
                    "## Решили\n(список «- **решение** — кто внедряет»; нет решений — «решений не было»)\n\n"
                    "## Поручения\n(список «- **Кто** — что — срок»)\n\n"
                    "## Открытые вопросы\n(список)\n\n"
                    "## Следующие шаги\n(список)" + hist_tpl},
            ],
            "options": {"temperature": 0.2, "num_predict": 900, "num_ctx": 8192},
        }, timeout=180)
        text = r.json().get("message", {}).get("content", "").strip()
        if text:
            date = folder.name[:10]
            # progressive disclosure: из выжимки видно, куда идти за деталями
            deeper = " · ".join(
                f"[[{ARCHIVE_DIR}/{folder.name}/{n}|{n}]]"
                for n in ("Минутки", "Разбор", "Стенограмма")
                if (folder / f"{n}.md").exists())
            out.write_text(f"---\ntype: саммари\nдата: {date}\n---\n\n"
                           f"# Саммари — {folder.name}\n\n{text}\n"
                           + (f"\n---\nПодробнее: {deeper}\n" if deeper else ""),
                           encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"саммари: {e}", file=sys.stderr)


def _unhide(path: pathlib.Path):
    """iCloud-контейнер помечает элементы UF_HIDDEN — Finder показывал архив
    «пустым» (20.07). Снимаем флаг с архива и всего содержимого."""
    try:
        for p in (path, *path.rglob("*")):
            fl = p.stat().st_flags
            if fl & _stat.UF_HIDDEN:
                os.chflags(p, fl & ~_stat.UF_HIDDEN)
    except Exception:  # noqa: BLE001 — косметика, не валим архивацию
        pass


def _derive_extras(folder: pathlib.Path):
    """Производные файлы: Тезисы.md и Вопросы и ответы.md из уже скопированных."""
    tr = folder / "Стенограмма.md"
    if tr.exists():  # тезисы ко-мышления: строки «> HH:MM 📌/💎/💭/🔬 …»
        notes = [line[2:].strip() for line in tr.read_text(encoding="utf-8").splitlines()
                 if line.startswith("> ") and re.search(r"[📌💎💭🔬]", line)]
        if notes:
            (folder / "Тезисы.md").write_text(
                "# Тезисы встречи (📌 КТ · 💎 факты · 💭 мысли · 🔬 переоценка)\n\n"
                + "\n".join(f"- {n}" for n in notes) + "\n", encoding="utf-8")

    qa: list[str] = []
    rb = folder / "Разбор.md"
    if rb.exists():  # аналитический раздел «вопрос → ответ/открыт» после встречи
        m = re.search(r"##\s*Вопросы встречи и ответы?\s*\n(.*?)(?=\n##\s|\Z)",
                      rb.read_text(encoding="utf-8"), re.S)
        if m and m.group(1).strip():
            qa += ["## Вопросы встречи и ответы (аналитика после встречи)", "",
                   m.group(1).strip(), ""]
    h = folder / "Подсказки и ответы.md"
    if h.exists():
        # построчный парс блоков «## [HH:MM] <тип>» (регекс-вариант молча давал 0);
        # эпизод вопроса: ❓/⚡ открывает, ☁️ прикрепляется; порядок в эпизоде
        # СТРОГО «вопрос → локальная модель (⚡) → Claude (☁️)» — облако можно
        # отключить, структура файла не изменится
        blocks: list[tuple[str, str, list[str]]] = []  # (тип, заголовок, строки)
        for line in h.read_text(encoding="utf-8").splitlines():
            mm = re.match(r"##\s*(\[\d{1,2}:\d{2}\])\s*(.*)", line)
            if mm:
                head = mm.group(2).strip()
                kind = ("q" if head.startswith("❓") else
                        "local" if head.startswith("⚡") else
                        "cloud" if "☁" in head else "hint")
                blocks.append((kind, f"{mm.group(1)} {head}", []))
            elif blocks:
                blocks[-1][2].append(line)
        episodes: list[dict] = []
        for kind, head, body in blocks:
            text = "\n".join(body).strip()
            if not text or kind == "hint":
                continue
            if kind in ("q", "local") or not episodes:
                episodes.append({})
            ep = episodes[-1]
            if kind in ep:  # тот же тип повторно — новый эпизод
                episodes.append({})
                ep = episodes[-1]
            ep[kind] = (head, text)
        if episodes:
            qa += ["## Ответы в темпе встречи", ""]
            for ep in episodes:
                for kind, label in (("q", "Вопрос"), ("local", "Локальная модель (⚡)"),
                                    ("cloud", "Claude (☁️)")):
                    if kind in ep:
                        head, text = ep[kind]
                        qa += [f"**{label}** {head}", "", text, ""]
                qa.append("---")
            if qa[-1] == "---":
                qa.pop()
    if qa:
        (folder / "Вопросы и ответы.md").write_text(
            "# Вопросы и ответы\n\n" + "\n".join(qa) + "\n", encoding="utf-8")


def _rebuild_index(graph: pathlib.Path):
    adir = graph / ARCHIVE_DIR
    folders = sorted((p for p in adir.iterdir() if p.is_dir()), reverse=True)
    lines = ["# Архив встреч\n",
             "Папка = встреча: дата — о чём говорили. Внутри вся документация "
             "и ссылка на граф.\n"]
    for p in folders:
        names = sorted(f.stem for f in p.glob("*.md") if f.stem != "Граф")
        # Саммари — первое, что читают: ссылка ведёт на него и в списке оно первое
        target = "Саммари" if "Саммари" in names else "Стенограмма"
        if "Саммари" in names:
            names.remove("Саммари")
            names.insert(0, "Саммари")
        lines.append(f"- [[{ARCHIVE_DIR}/{p.name}/{target}|{p.name}]] — {', '.join(names)}")
    (adir / "_ОГЛАВЛЕНИЕ.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def migrate_all(graph: pathlib.Path, tdir: pathlib.Path) -> int:
    """Разовая миграция истории: все стенограммы transcripts/ → папки архива."""
    done = 0
    for f in sorted(tdir.glob("*.md")):
        if any(f.name.endswith(suf) for suf, _ in NICE):
            continue  # это артефакт, не стенограмма
        if f.stat().st_size < 600:
            continue  # пустышка (тест старт/стоп) — не встреча
        m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})(?:_(.+))?\.md$", f.name)
        if not m:
            continue
        stamp, slug = m.group(1), m.group(2) or ""
        archive_meeting(graph, tdir, stamp, slug)
        done += 1
    return done


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load(_cfg_text(ROOT))
    graph = pathlib.Path(cfg["sufler"]["graph_dir"]).expanduser()
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    if "--all" in sys.argv:
        n = migrate_all(graph, tdir)
        print(f"архив: {n} встреч в {graph / ARCHIVE_DIR}")
    else:
        print("использование: meeting_archive.py --all (миграция истории)")
