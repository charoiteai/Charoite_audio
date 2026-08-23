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

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from llm import LLM, LLMHTTPError  # noqa: E402
import meeting_stamp  # noqa: E402
from meeting_archive import archive_meeting  # noqa: E402

from charoite_paths import harden_umask, resolve_root

ROOT = resolve_root(__file__)
import datetime as _dt
import graphs
NOTE = f"<!-- восстановлено ретроспективно по стенограмме, {_dt.date.today()} -->\n"

MINUTES_PROMPT = (
    "Составь минутки в markdown строго по шаблону:\n"
    "# Минутки встречи\n"
    "**Дата/время:** … **Участники:** …\n"
    "## Темы\n## Решения\n## Поручения\n## Открытые вопросы\n## Риски\n\n"
    "Правила: только то, что прозвучало; пункт — одна строка; никаких "
    "markdown-таблиц; поручение чекбоксом «- [ ] **Имя** — что — срок»; "
    "решение «- **что решили** — кто внедряет»; пустой раздел — «нет»; "
    "весь документ до 900 знаков."
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
    try:
        return LLM(cfg).complete(
            f"Стенограмма встречи:\n\n{transcript[:24000]}\n\n{task}",
            system=system, model=cfg["llm"]["model"], think=False,
            temperature=0.3, num_ctx=16384, timeout=900)
    except LLMHTTPError as e:
        # как и раньше: ошибка сервера не валит весь ретро-прогон,
        # файл этого артефакта просто не создаётся
        print(f"ретро: {e}", file=sys.stderr)
        return ""


def main():
    harden_umask()   # минутки, разбор, архив — данные встреч, только владельцу
    cfg_p = ROOT / "config" / "config.yaml"
    if not cfg_p.exists():  # свежий клон: пример вместо жёсткого падения
        cfg_p = ROOT / "config" / "config.example.yaml"
    cfg = yaml.safe_load(cfg_p.read_text(encoding="utf-8"))
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    tdir = ROOT / cfg["log"]["transcripts_dir"]

    for f in sorted(tdir.glob("*.md")):
        if re.search(r"_(minutes|hints|разбор|ревизия_claude|спикеры)\.md$", f.name):
            continue
        # Посекундные стенограммы (с 28.07) минутный регэксп пропускал
        # целиком (круг-1 по PR #388, Codex); ключ — как у graph_updater.
        bare = meeting_stamp.stamp_of(f.stem)
        if bare is None or f.stat().st_size < 600:
            continue
        stamp = meeting_stamp.graph_key(tdir, f.stem, graph)
        slug = f.stem[len(bare) + 1:] if f.stem != bare else ""
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

        folder = archive_meeting(graph, tdir, stamp, slug, files_key=f.stem)
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
