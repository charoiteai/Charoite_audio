"""Архив встреч для Finder: папка «дата — название», внутри вся документация.

Структура (в iCloud-вольте, рядом с графом — синкается на все устройства):
    рабочий проект/Встречи-архив/
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

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import stat as _stat
import sys

from charoite_paths import resolve_root
from meeting_stamp import files_with_stamp
import graphs

ROOT = resolve_root(__file__)
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


def _folders_for(graph: pathlib.Path, stamp: str) -> list[pathlib.Path]:
    """Все папки архива, относящиеся к этой встрече, — свежие первыми.

    Ищем по дате и времени в начале имени: тема в хвосте меняется, встреча —
    нет. Свежие первыми, потому что при склейке дублей выживать должна та
    папка, куда писали последней.
    """
    prefix = f"{stamp[:10]} {stamp[11:13]}-{stamp[13:15]} "
    root = graph / ARCHIVE_DIR
    if not root.exists():
        return []
    found = [d for d in root.iterdir() if d.is_dir() and d.name.startswith(prefix)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def archive_meeting(graph: pathlib.Path, tdir: pathlib.Path, stamp: str, title: str,
                    files_key: str | None = None) -> pathlib.Path | None:
    """Собирает/обновляет папку встречи; возвращает её путь (None — исключена).

    `files_key` — стем главного файла встречи («2026-08-03_113012» у ещё не
    переименованной посекундной встречи, «2026-08-03_1130_Планёрка» после
    наката темы): её файлы — ровно `<стем>.md` и `<стем>_*.md`. Без ключа
    ищем по минутному штампу с границей — так зовут retro-прогоны, где стем
    и есть минутный штамп с темой.
    """
    if stamp in _excluded(graph):
        return None
    pretty = (title or "").replace("_", " ").strip() or "встреча"
    # время в имени папки: 5 встреч в день неотличимы по «дата — тема»,
    # а mtime врёт после доработок (ревизии дописывают файлы). Двоеточие
    # в имени нельзя (Finder/Windows/синк) — «11-30» читаемо и безопасно
    nice_time = f"{stamp[11:13]}-{stamp[13:15]}"
    folder = graph / ARCHIVE_DIR / f"{stamp[:10]} {nice_time} — {_safe(pretty)}"
    for legacy in (graph / ARCHIVE_DIR / f"{stamp} — {_safe(pretty)}",
                   graph / ARCHIVE_DIR / f"{stamp[:10]} — {_safe(pretty)}"):
        if legacy.exists() and not folder.exists():
            legacy.rename(folder)   # старые форматы: тихо мигрируем при обновлении
    # Тема встречи уточняется при повторных разборах, и папка называется по
    # теме. Прежде это плодило вторую папку на ту же встречу: у 21 встречи из
    # 62 в архиве оказалось по две — «Бюджет MVP» и «Бюджет и ресурсы MVP».
    # Папка на встречу одна: старую переименовываем.
    if not folder.exists():
        for old in _folders_for(graph, stamp):
            if old != folder:
                old.rename(folder)
                break
    folder.mkdir(parents=True, exist_ok=True)
    # Только файлы ЭТОЙ встречи: минутный штамп — префикс секундного, и голый
    # глоб `{stamp}*.md` тянул в папку файлы второй встречи той же минуты
    # (крэш-рестарт), причём с перезаписью по одноимённому назначению
    # (аудит DeepSeek 16.08). Правило границы — одно, в meeting_stamp.
    for f in files_with_stamp(tdir, files_key or stamp, suffix=".md"):
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
    _write_manifest(folder, stamp, pretty)
    _rebuild_index(graph)
    # Флаг снимаем со ВСЕГО графа, а не только с архивной папки.
    # iCloud метит UF_HIDDEN что угодно в своём контейнере, и на папках
    # «Люди», «Системы», «Встречи» это уже случилось: обходчик поиска
    # (FileManager с .skipsHiddenFiles) видел 546 файлов из 1172 — сердце
    # графа стало невидимым, и приложение отвечало «в памяти этого нет» про
    # людей, с которыми встречи были на этой неделе. Поиск с тех пор на флаг
    # не смотрит, но и графу незачем оставаться помеченным: он же открывается
    # в Finder и Obsidian.
    _unhide(graph)
    return folder


# Названия разделов саммари на трёх языках. Одно место на весь модуль:
# отсюда собирается промпт (пишем на языке конфига) и отсюда же читается
# готовый документ (разбираем на ЛЮБОМ языке).
#
# Разница принципиальная. Генерация зависит от `sufler.language` — что человек
# выбрал, на том и пишем. Разбор от конфига зависеть не должен: в архиве лежат
# встречи, записанные до переключения языка, и пересборка старой русской
# встречи при `language: en` не имеет права потерять её решения. Раньше и то,
# и другое было жёстко русским: при en/zh минутки выходили английские, а
# Саммари.md — всё равно русское.
SUMMARY_SECTIONS: dict[str, dict[str, str]] = {
    "ru": {
        "gist": "Суть одной строкой:",
        "topics": "О чём говорили",
        "decisions": "Решили",
        "tasks": "Поручения",
        "questions": "Открытые вопросы",
        "history": "Связь с прошлыми встречами",
        "none": "решений не было",
    },
    "en": {
        "gist": "Bottom line:",
        "topics": "What we talked about",
        "decisions": "Decisions",
        "tasks": "Action items",
        "questions": "Open questions",
        "history": "Link to past meetings",
        "none": "no decisions were made",
    },
    "zh": {
        "gist": "一句话概括：",
        "topics": "讨论了什么",
        "decisions": "决定",
        "tasks": "任务",
        "questions": "待解决问题",
        "history": "与过往会议的关联",
        "none": "没有做出决定",
    },
}

#: Все написания раздела на всех языках — для разбора готового документа.
def section_names(key: str) -> tuple[str, ...]:
    names = [SUMMARY_SECTIONS[lang][key] for lang in ("ru", "en", "zh")]
    # Исторические написания: «Решения» встречалось наравне с «Решили».
    if key == "decisions":
        names.append("Решения")
    return tuple(dict.fromkeys(names))


def _config_lang() -> str:
    """Язык НОВЫХ документов — из `sufler.language`, с откатом на русский.

    Отдельно от `summary_lang`: тот отвечает на вопрос «на чём написан этот
    файл», а этот — «на чём писать следующий». Конфиг может не читаться
    (архив зовут и из тестов, и из миграции) — тогда пишем по-русски, как
    было всегда.
    """
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        lang = str((cfg.get("sufler") or {}).get("language", "ru")).strip().lower()
    except Exception:  # noqa: BLE001 — язык документа не повод ронять архив
        return "ru"
    return lang if lang in SUMMARY_SECTIONS else "ru"


def _lang_name() -> str:
    """Как назвать язык в промпте (промпт остаётся русским, меняется ответ)."""
    return {"ru": "по-русски", "en": "по-английски", "zh": "по-китайски"}[_config_lang()]


def summary_lang(text: str) -> str:
    """На каком языке написано это саммари — по его же заголовкам.

    Нужен там, где документ обрабатывается после генерации: обрезка по лимиту
    и восстановление раздела решений. Конфиг здесь не годится — он говорит,
    на чём писать НОВОЕ, а на диске лежат встречи всех прошлых языков.
    """
    for lang in ("ru", "en", "zh"):
        words = SUMMARY_SECTIONS[lang]
        for key in ("decisions", "tasks", "questions", "topics"):
            if re.search(rf"(?m)^##\s*{re.escape(words[key])}\s*$", text):
                return lang
    return "ru"


def summary_gist(text: str) -> str | None:
    """Суть одной строкой — на любом из трёх языков."""
    for lang in ("ru", "en", "zh"):
        marker = SUMMARY_SECTIONS[lang]["gist"]
        match = re.search(rf"\*\*{re.escape(marker)}\*\*\s*(.+)", text)
        if match:
            return match.group(1).strip()
    return None


def _manifest_items(text: str, names: tuple[str, ...]) -> list[str]:
    """Пункты секции саммари для стабильных полей манифеста."""
    for name in names:
        match = re.search(
            rf"(?m)^##\s*{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)",
            text,
            re.S | re.M,
        )
        if not match:
            continue
        items = [
            re.sub(r"^\s*[-*]\s+", "", line).strip()
            for line in match.group(1).splitlines()
            if re.match(r"^\s*[-*]\s+\S", line)
        ]
        return [item for item in items if item]
    return []


def _manifest_participants(transcript: str) -> list[str]:
    for line in transcript.splitlines()[:8]:
        if not line.startswith(("Участники", "Participants", "参会者")) or ":" not in line:
            continue
        return [name.strip() for name in line.split(":", 1)[1].split(",") if name.strip()]
    return []


def _manifest_duration(transcript: str) -> int | None:
    times = []
    for line in transcript.splitlines():
        if not line.startswith("**"):
            continue
        for hour, minute in re.findall(r"\b(\d{1,2}):(\d{2})\b", line):
            h, m = int(hour), int(minute)
            if h < 24 and m < 60:
                times.append(h * 60 + m)
    if len(times) < 2:
        return None
    span = times[-1] - times[0]
    if span < 0:
        span += 24 * 60
    return span or None


def build_manifest(folder: pathlib.Path, stamp: str, title: str) -> dict:
    """Производный индекс встречи; все поля можно восстановить из Markdown."""
    summary_path = folder / "Саммари.md"
    transcript_path = folder / "Стенограмма.md"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    gist = summary_gist(summary)
    files = {
        key: name for key, name in (
            ("transcript", "Стенограмма.md"),
            ("summary", "Саммари.md"),
            ("minutes", "Минутки.md"),
            ("debrief", "Разбор.md"),
        ) if (folder / name).exists()
    }
    started = None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(?:\d{2})?", stamp)
    if match:
        started = f"{match.group(1)}T{match.group(2)}:{match.group(3)}:00"
    return {
        "schema_version": 1,
        "meeting_id": stamp,
        "title": title,
        "started_at": started,
        "duration_minutes": _manifest_duration(transcript),
        "participants": _manifest_participants(transcript),
        "summary": gist,
        "decisions": _manifest_items(summary, section_names("decisions")),
        "action_items": _manifest_items(summary, section_names("tasks")),
        "open_questions": _manifest_items(summary, section_names("questions")),
        "files": files,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _write_manifest(folder: pathlib.Path, stamp: str, title: str) -> None:
    """Атомарно обновить манифест после сборки человекочитаемых файлов."""
    path = folder / "meeting.meta.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(build_manifest(folder, stamp, title), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


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
                # хроника пишется newest-first (upsert_core вставляет под заголовок) —
                # берём ВЕРХНИЕ 3 (ближайшие к дате встречи), не hist[-3:] (самые старые)
                parts.append(f"Ядро «{p.stem}»:\n" + "\n".join(hist[:3]))
    prev = [p for p in sorted(folder.parent.iterdir())
            if p.is_dir() and p.name < folder.name and (p / "Саммари.md").exists()]
    for p in prev[-2:]:
        parts.append(f"Саммари встречи {p.name}:\n"
                     + (p / "Саммари.md").read_text(encoding="utf-8")[:1200])
    return "\n\n".join(parts)[:3500]


def decisions_of(folder: pathlib.Path) -> list[str]:
    """Решения встречи так, как их записали минутки, — по пункту на строку.

    Минутки и разбор пишут решения структурно: заголовок «Решения» (иногда
    «### ✅ Решения:») и под ним список. Просить модель найти их заново —
    лишний риск: замер 03.08 на одной и той же встрече дал 1 попадание из 3.
    Дешевле подать готовое.
    """
    for name in ("Минутки.md", "Разбор.md"):
        f = folder / name
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        m = re.search(r"(?m)^#{2,4}[^\n]*Решени\w*[^\n]*$\n(.*?)(?=\n#{2,4} |\Z)", text, re.S)
        if not m:
            continue
        items = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip()
                 for ln in m.group(1).splitlines()
                 if re.match(r"\s*(?:[-*]|\d+[.)])\s+\S", ln)]
        if items:
            return items
    return []


def _force_decisions(text: str, decisions: list[str], per_item: int = 165) -> str:
    """Вернуть в саммари решения, если модель написала «решений не было».

    Промпт этот раздел не удерживает: модель то находит решения, то нет, и
    цена ошибки высокая — «решений не было» поверх трёх записанных решений
    читается как факт. Раз данные есть, последнее слово за кодом.
    """
    if not decisions:
        return text
    # Язык берём из самого документа, а не из конфига: пересборка старой
    # русской встречи при `language: en` обязана лечить её же русский раздел.
    lang = summary_lang(text)
    words = SUMMARY_SECTIONS[lang]
    if words["none"].lower() not in text.lower():
        return text

    def short(s: str) -> str:
        s = re.sub(r"\*\*", "", s).strip()
        if len(s) <= per_item:
            return s
        return s[:per_item].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"

    head = f"## {words['decisions']}"
    block = head + "\n" + "\n".join(f"- {short(d)}" for d in decisions[:3])
    return re.sub(rf"(?ms)^{re.escape(head)}\n.*?(?=^## |\Z)", block + "\n\n", text)


def _trim_summary(text: str, limit: int = 900, per_item: int = 165, per_section: int = 3) -> str:
    """Гарантирует лимит саммари структурно, а не обрезкой по символу.

    Промптом объём не удержать: модель не считает собственную длину (замер
    22.07: при лимите «900 знаков / 120 слов» qwen выдавал 1421-1830). Поэтому
    режем детерминированно — длинные пункты по границе слова, лишние пункты
    сверх per_section, а если всё ещё длинно — целиком наименее важные разделы
    с конца. Суть, решения и поручения переживают обрезку последними.
    """
    if len(text) <= limit:
        return text

    def cut(line: str) -> str:
        if len(line) <= per_item:
            return line
        head = line[:per_item].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        return head + "…"

    blocks = re.split(r"(?m)^(?=## )", text)
    out_blocks: list[str] = []
    for b in blocks:
        lines = b.rstrip().splitlines()
        kept, items = [], 0
        for ln in lines:
            if ln.lstrip().startswith(("- ", "* ")):
                if items >= per_section:
                    continue
                items += 1
                kept.append(cut(ln))
            else:
                kept.append(cut(ln) if len(ln) > per_item * 2 else ln)
        out_blocks.append("\n".join(kept))

    # Всё ещё длинно — жертвуем разделами по важности, а не по месту в тексте.
    # Прежде отбрасывался хвост, и «Поручения» гибли раньше обзорного «О чём
    # говорили»: у встречи 15.07 из выжимки пропало, кто что должен сделать, —
    # то есть ровно то, ради чего её открывают. Порядок жертв — от наименее
    # ценного к более ценному; суть, решения и поручения не трогаем.
    words = SUMMARY_SECTIONS[summary_lang(text)]
    for head in (f"## {words['history']}", f"## {words['questions']}", f"## {words['topics']}"):
        if len(("\n\n".join(out_blocks)).strip()) <= limit:
            break
        out_blocks = [b for b in out_blocks if not b.lstrip().startswith(head)]
    # Если и теперь длинно — убираем по одному пункту из самого раскормленного
    # раздела, а не раздел целиком. Иначе на очень длинной встрече исчезали
    # «Поручения»: они шли последними и попадали под нож, хотя пережить обрезку
    # должны были в первую очередь.
    def size() -> int:
        return len(("\n\n".join(out_blocks)).strip())

    while size() > limit:
        fattest, best = -1, 0
        for i, b in enumerate(out_blocks):
            items = [ln for ln in b.splitlines() if ln.lstrip().startswith(("- ", "* "))]
            if len(items) > 1 and len(b) > best:
                fattest, best = i, len(b)
        if fattest < 0:
            break                       # резать больше нечего, лимит недостижим
        lines = out_blocks[fattest].splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].lstrip().startswith(("- ", "* ")):
                del lines[i]
                break
        out_blocks[fattest] = "\n".join(lines)
    return ("\n\n".join(out_blocks)).strip()


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
    # Решения — отдельным блоком, а не «найди в материалах»: они уже записаны
    # минутками структурно, и искать их заново модель умеет через раз.
    decided = decisions_of(folder)
    words = SUMMARY_SECTIONS[_config_lang()]
    decided_block = (f"\n\n=== Решения встречи (перенеси их в раздел «{words['decisions']}», "
                     "сократив каждое до строки) ===\n"
                     + "\n".join(f"- {d}" for d in decided)) if decided else ""
    hist_block = (
        "\n\n=== История (Ядра и прошлые встречи) — ТОЛЬКО для раздела "
        f"«{words['history']}» ===\n" + history) if history else ""
    hist_tpl = (
        f"\n\n## {words['history']}\n"
        "(1-3 пункта «- **тема** — было: … (DD.MM) → сегодня: …» — ТОЛЬКО темы, "
        "которых сегодняшняя встреча реально касалась: продвижение, подтверждение "
        "или отмена прошлой договорённости. Нет пересечений — пропусти раздел)"
    ) if history else ""
    try:
        import yaml

        from llm import LLM
        # Модель и адрес — из конфига через llm.py, а не хардкодом: боевой
        # конфиг 12.08 переехал на mlx-сборку, а Саммари продолжало звать
        # старую модель мимо него (аудит 14.08). Лестница resolve_model
        # заодно даёт фолбэк, если основная модель не установлена.
        cfg_p = ROOT / "config" / "config.yaml"
        if not cfg_p.exists():   # свежий клон: пример вместо падения
            cfg_p = ROOT / "config" / "config.example.yaml"
        cfg = yaml.safe_load(cfg_p.read_text(encoding="utf-8"))
        text = LLM(cfg).complete(
            "<материалы>\n" + "\n\n".join(src_parts) + decided_block + hist_block
            + "\n</материалы>\n\n"
            f"Составь саммари {_lang_name()} по шаблону "
            "(заголовки — дословно как здесь):\n"
            f"**{words['gist']}** …\n\n"
            f"## {words['topics']}\n(до 3 пунктов «- **тема** — что по ней», не проза)\n\n"
            # «кто внедряет» тут стояло — и глушило весь раздел. У решений
            # в минутках исполнителя обычно нет («признаны неподходящими»,
            # «отказ от эскалации»), модель не находила его и писала
            # «решений не было» поверх трёх записанных решений. Замер 03.08
            # на четырёх встречах: без этого требования — 4 из 4 верно.
            f"## {words['decisions']}\n(список «- **тема решения** — суть одной "
            "строкой»; если в материалах нет ни одного решения — "
            f"«{words['none']}»)\n\n"
            f"## {words['tasks']}\n(список «- **Кто** — что — срок»)\n\n"
            f"## {words['questions']}\n(список; это последний раздел — следующие "
            "шаги уже перечислены в поручениях)" + hist_tpl,
            system=(
                # правила позитивные и данные в тегах — qwen следует такому лучше,
                # чем стопке «БЕЗ / НЕ / никогда» (замер 22.07 на минутках)
                "Ты делаешь выжимку рабочей встречи для быстрого чтения. Пишешь "
                f"{_lang_name()}, сухо, по фактам из материалов. Оформляешь списками "
                "«- …» с жирным ключом в начале пункта. Держишь весь текст в "
                "пределах 120 слов (900 знаков): максимум 3 пункта в разделе, "
                "пункт — одна строка до 12 слов, в пустом разделе одно слово «нет». "
                "Саммари читают за минуту — это выжимка; детали остаются в "
                "Минутках и Разборе."),
            think=False,
            # 560 токенов ≈ 1900 знаков: потолок НЕ должен резать (у русского в qwen
            # ~3.4 знака на токен, прежние 420 обрубали саммари на полуслове).
            # Объём держит промпт (120 слов), потолок — лишь страховка от простыни
            temperature=0.2, num_predict=560, num_ctx=8192,
            timeout=180,
        )
        if text:
            # Последнее слово за кодом: раздел решений слишком дорог, чтобы
            # зависеть от того, разглядела ли модель их в этот раз.
            text = _force_decisions(text, decided)
            text = _trim_summary(text)  # лимит гарантирует код, не промпт
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
        archive_meeting(graph, tdir, stamp, slug, files_key=f.stem)
        done += 1
    return done


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    if "--all" in sys.argv:
        n = migrate_all(graph, tdir)
        print(f"архив: {n} встреч в {graph / ARCHIVE_DIR}")
    else:
        print("использование: meeting_archive.py --all (миграция истории)")
