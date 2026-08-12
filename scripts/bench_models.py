#!/usr/bin/env python3
"""Замер моделей на наших сценариях, а не на синтетике.

Синтетический tok/s ничего не решает: у суфлёра узкое место — не средняя
скорость, а задержка до первой подсказки на живой встрече и обработка
длинного контекста при разборе стенограммы. Поэтому меряем три вещи на
трёх разных нагрузках и печатаем таблицу, по которой можно принимать
решение.

Запуск:
    .venv/bin/python scripts/bench_models.py qwen3.6:35b-a3b qwen3.6:35b-mlx

Машина должна быть свободна: при живом разборе встречи замер покажет
очередь, а не модель.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:11434"

# Короткая подсказка: так суфлёр отвечает во время встречи.
SHORT = "Одним предложением: что такое препрод и зачем он нужен?"

# Средний разбор: типовая задача «вытащи структуру из текста».
MEDIUM = (
    "Из текста ниже выпиши списком названия систем и таблиц, без пояснений.\n\n"
    "Ночная выгрузка складывает отчёт в хранилище: сервис учёта пишет в "
    "таблицу заказов, планировщик забирает её раз в сутки и обновляет "
    "витрину продаж. Список объектов формируется из карточки задачи."
)


def long_prompt() -> str:
    """Реальная стенограмма — самый честный длинный контекст, какой у нас есть."""
    tr = sorted((ROOT / "transcripts").glob("2026-*.md"))
    for p in reversed(tr):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > 20_000:
            return ("Ниже фрагмент стенограммы. Назови три главные темы "
                    "разговора, по одной строке на тему.\n\n" + text[:20_000])
    return MEDIUM


def ask(model: str, prompt: str, timeout: int = 600) -> dict:
    """Один вызов с замером: до первого токена и до конца ответа."""
    t0 = time.monotonic()
    first = None
    chunks = []
    with requests.post(f"{BASE}/api/chat", stream=True, timeout=timeout, json={
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.2, "num_ctx": 16384},
    }) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            piece = data.get("message", {}).get("content", "")
            if piece and first is None:
                first = time.monotonic() - t0
            if piece:
                chunks.append(piece)
            if data.get("done"):
                total = time.monotonic() - t0
                return {
                    "first": first if first is not None else total,
                    "total": total,
                    "eval_count": data.get("eval_count") or 0,
                    "prompt_eval": data.get("prompt_eval_count") or 0,
                    "text": "".join(chunks),
                }
    return {"first": 0, "total": 0, "eval_count": 0, "prompt_eval": 0, "text": ""}


def run(model: str, repeats: int) -> dict:
    cases = {"короткая подсказка": SHORT,
             "разбор текста": MEDIUM,
             "длинный контекст": long_prompt()}
    out = {}
    for name, prompt in cases.items():
        firsts, speeds = [], []
        for i in range(repeats):
            try:
                r = ask(model, prompt)
            except Exception as e:  # noqa: BLE001 — отчёт важнее падения
                print(f"  {model} / {name}: ошибка {type(e).__name__}: {e}")
                break
            firsts.append(r["first"])
            if r["total"] > 0 and r["eval_count"]:
                speeds.append(r["eval_count"] / r["total"])
            # Первый прогон включает загрузку весов — он показателен для
            # «холодного» старта, но портит среднее, поэтому виден отдельно.
            mark = " (холодный)" if i == 0 else ""
            print(f"  {name}{mark}: до первого токена {r['first']:.1f} с, "
                  f"{r['eval_count']} токенов за {r['total']:.1f} с")
        if firsts:
            out[name] = {
                "first_cold": firsts[0],
                "first_warm": statistics.median(firsts[1:]) if len(firsts) > 1 else firsts[0],
                "tok_s": statistics.median(speeds) if speeds else 0,
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="+")
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()

    try:
        requests.get(f"{BASE}/api/version", timeout=3)
    except requests.RequestException:
        print("ollama не отвечает на 11434")
        return 1

    results = {}
    for m in a.models:
        print(f"\n=== {m} ===")
        results[m] = run(m, a.repeats)

    print("\n\nИТОГ (медиана по тёплым прогонам)\n")
    print(f"{'сценарий':22} {'модель':26} {'1-й токен':>10} {'ток/с':>8}")
    print("-" * 70)
    for case in ("короткая подсказка", "разбор текста", "длинный контекст"):
        for m, data in results.items():
            d = data.get(case)
            if not d:
                continue
            print(f"{case:22} {m:26} {d['first_warm']:>9.1f}с {d['tok_s']:>7.1f}")
    print("\nХолодный старт (первый вызов, включает загрузку весов):")
    for m, data in results.items():
        cold = [d["first_cold"] for d in data.values()]
        if cold:
            print(f"  {m:26} {max(cold):.1f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
