#!/usr/bin/env python3
"""Замер локальных моделей на синтетических поручениях (№998): вердикт и срок.

    heavy_run.sh .venv/bin/python scripts/bench_task_reviser.py [--reviser] MODEL [MODEL ...]

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


def bench_reviser(models: list[str]) -> int:
    """Вердикт ровно так, как его выносит ревизор (две формулировки, согласие): «просрочено» там считает код."""
    sys.path.insert(0, str(pathlib.Path.home() / ".config/charoite/tasks_control"))
    import datetime as dt

    import reviser

    cases = [json.loads(line) for line in CASES.read_text().splitlines()]
    for model in models:
        harmful = missed = fail = 0
        seconds = []
        for case in cases:
            want = "open" if case["verdict"] == "stale" else case["verdict"]
            started = time.monotonic()
            got = reviser.judge(case["line"], dt.date.fromisoformat(case["meeting_date"]),
                                dt.date.fromisoformat(case["today"]), model)
            seconds.append(time.monotonic() - started)
            if got is None:
                fail += 1
            elif got != want:
                if got == "open":
                    missed += 1
                else:
                    harmful += 1
                    print("   вредная", case["id"], got, "вместо", want)
        seconds.sort()
        print(f"{model}: вредных {harmful}, пропущено {missed}, сбоев {fail}, "
              f"median {seconds[len(cases) // 2]:.1f}s, max {seconds[-1]:.1f}s")
    return 0


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
    if sys.argv[1:2] == ["--reviser"]:
        sys.exit(bench_reviser(sys.argv[2:]))
    sys.exit(main(sys.argv[1:]) if len(sys.argv) > 1 else 2)
