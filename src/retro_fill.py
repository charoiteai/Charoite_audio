#!/usr/bin/env python3
"""Ретро-генерация артефактов для встреч, где суфлёр не работал вживую.

Встречи 15.07 восстановлены из записей задним числом — минуток/тезисов/
разборов у них не существовало. Генерим по стенограмме (Ollama qwen):
  - {stamp}_minutes.md и {stamp}_разбор.md → в transcripts (штатные имена,
    конвейер и recall их видят), архив подхватит как Минутки/Разбор;
  - Тезисы.md → сразу в папку архива (в живой стенограмме их дом —
    секция «Ко-мышление», ретроспективно её не подделываем).

Запуск: .venv/bin/python src/retro_fill.py   # только недостающее, идемпотентно
"""
from __future__ import annotations

import pathlib
import re
import sys

import requests
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from meeting_archive import archive_meeting  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTE = "<!-- восстановлено ретроспективно по стенограмме, 2026-07-20 -->\n"

MINUTES_PROMPT = (
    "Составь минутки в markdown строго по шаблону:\n"
    "# Минутки встречи\n"
    "**Дата/время:** … **Участники:** (имена из контекста разговора; если не звучали — владелец и собеседники)\n"
    "## Темы\n(нумерованный список обсуждённых тем, по строке)\n"
    "## Решения\n(что решили; если решений не было — так и напиши)\n"
    "## Поручения\n(таблица: Кто | Что | Срок — только реально прозвучавшее)\n"
    "## Открытые вопросы\n## Риски\n"
    "Только факты из стенограммы, ничего не выдумывай."
)
DEBRIEF_PROMPT = (
    "Составь разбор строго по разделам:\n"
    "# Разбор встречи\n"
    "## Вопросы встречи и ответы\n(каждый прозвучавший вопрос → ответ, если прозвучал; если нет — «открыт»)\n"
    "## Задачи\n(кто/что/срок)\n"
    "## Возможные решения открытых вопросов\n(варианты с плюсами/минусами, кратко)\n"
    "## Рекомендации: что проработать до следующей встречи\n(конкретные шаги)"
)
THESES_PROMPT = (
    "Выдели из стенограммы всё по-настоящему ценное, каждое с новой строки со строгим префиксом:\n"
    "📌 — контрольная точка: решение, договорённость, срок, поручение (кто/что/когда)\n"
    "💎 — ценная информация: цифра, имя, обещание, условие, риск\n"
    "💭 — мысль (до трёх): противоречие, упущенный вопрос, скрытый риск\n"
    "Телеграфно, по-русски, без вступлений."
)


def gen(cfg: dict, system: str, transcript: str, task: str) -> str:
    r = requests.post(
        cfg["llm"]["base_url"].rstrip("/") + "/api/chat",
        json={"model": cfg["llm"]["model"], "stream": False, "think": False,
              "options": {"temperature": 0.3, "num_ctx": 16384},
              "messages": [
                  {"role": "system", "content": system},
                  {"role": "user", "content": f"Стенограмма встречи:\n\n{transcript[:24000]}\n\n{task}"},
              ]},
        timeout=900,
    )
    return (r.json().get("message", {}).get("content", "") or "").strip()


def main():
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = pathlib.Path(cfg["sufler"]["graph_dir"]).expanduser()
    tdir = ROOT / cfg["log"]["transcripts_dir"]

    for f in sorted(tdir.glob("*.md")):
        if re.search(r"_(minutes|hints|разбор|ревизия_claude|спикеры)\.md$", f.name):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})(?:_(.+))?\.md$", f.name)
        if not m or f.stat().st_size < 600:
            continue
        stamp, slug = m.group(1), m.group(2) or ""
        text = f.read_text(encoding="utf-8")
        base = f.with_suffix("")
        made = []

        mpath = pathlib.Path(str(base) + "_minutes.md")
        if not mpath.exists():
            out = gen(cfg, "Ты секретарь встречи. Пишешь точные, сухие минутки по-русски.",
                      text, MINUTES_PROMPT)
            if out:
                mpath.write_text(NOTE + out + "\n", encoding="utf-8")
                made.append("минутки")

        dpath = pathlib.Path(str(base) + "_разбор.md")
        if not dpath.exists():
            out = gen(cfg, "Ты аналитик после рабочей встречи. Пиши по-русски, сухо, markdown. "
                           "Не выдумывай факты.", text, DEBRIEF_PROMPT)
            if out:
                dpath.write_text(NOTE + out + "\n", encoding="utf-8")
                made.append("разбор")

        folder = archive_meeting(graph, tdir, stamp, slug)
        if folder is not None:
            tpath = folder / "Тезисы.md"
            if not tpath.exists():
                out = gen(cfg, "Ты выделяешь ценное из стенограмм. Телеграфно, по-русски.",
                          text, THESES_PROMPT)
                if out:
                    tpath.write_text(
                        "# Тезисы встречи (📌 КТ · 💎 факты · 💭 мысли)\n" + NOTE + "\n"
                        + out + "\n", encoding="utf-8")
                    made.append("тезисы")
        print(f"{stamp}: {', '.join(made) if made else 'полная'}")


if __name__ == "__main__":
    main()
