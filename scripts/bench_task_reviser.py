#!/usr/bin/env python3
"""Замер локальных моделей на синтетических поручениях (№998): вердикт и срок.

    heavy_run.sh .venv/bin/python scripts/bench_task_reviser.py MODEL [MODEL ...]

Данные — только tests/fixtures/task_reviser_cases.jsonl (выдуманные строки).
Ничего не пишет в граф; печатает точность и время на строку.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request

CASES = pathlib.Path(__file__).resolve().parent.parent / "tests/fixtures/task_reviser_cases.jsonl"
URL = "http://127.0.0.1:11434/api/chat"
VERDICTS = ("open", "done", "not_task", "stale")

PROMPT = """Ты проверяешь строку поручения из протокола встречи. Сегодня {today}, встреча была {meeting_date}.

Строка: {line}

Определи:
- verdict: "done" — в самой строке сказано, что уже сделано; "not_task" — это не поручение (констатация, решение, мнение, итог обсуждения); "stale" — срок указан и просрочен больше чем на 14 дней от сегодня; иначе "open".
- due: срок исполнения в формате ГГГГ-ММ-ДД, посчитанный от даты встречи (слова «до пятницы», «завтра», «через две недели» считай от неё), либо null, если срока в строке нет. Дату, которая только упоминается как прошлое событие, сроком не считай.

Ответ — только JSON: {{"verdict": "...", "due": "ГГГГ-ММ-ДД" | null}}"""


def ask(model: str, case: dict) -> tuple[dict | None, float]:
    body = {"model": model, "stream": False, "think": False, "format": "json",
            "options": {"temperature": 0, "num_predict": 120},
            "messages": [{"role": "user", "content": PROMPT.format(**case)}]}
    request = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            content = json.loads(response.read())["message"]["content"]
        return json.loads(content), time.monotonic() - started
    except (OSError, ValueError, KeyError):
        return None, time.monotonic() - started


def main(models: list[str]) -> int:
    cases = [json.loads(line) for line in CASES.read_text().splitlines()]
    for model in models:
        verdict_ok = due_ok = bad = 0
        seconds = []
        misses = []
        for case in cases:
            got, took = ask(model, case)
            seconds.append(took)
            if not isinstance(got, dict) or got.get("verdict") not in VERDICTS:
                bad += 1
                misses.append((case["id"], "invalid", got))
                continue
            verdict_ok += got["verdict"] == case["verdict"]
            due_ok += got.get("due") == case["due"]
            if got["verdict"] != case["verdict"] or got.get("due") != case["due"]:
                misses.append((case["id"], got, (case["verdict"], case["due"])))
        n = len(cases)
        seconds.sort()
        print(f"{model}: verdict {verdict_ok}/{n}, due {due_ok}/{n}, invalid {bad}, "
              f"median {seconds[n // 2]:.1f}s, max {seconds[-1]:.1f}s")
        for miss in misses:
            print("   miss", *miss)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) if len(sys.argv) > 1 else 2)
