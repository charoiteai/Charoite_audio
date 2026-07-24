#!/usr/bin/env python3
"""Мини-бенч памяти: эталонные вопросы по архиву → проверка фактов в ответе.

Регрессионные тесты, но для КАЧЕСТВА ПАМЯТИ, а не кода: пороги NLI, длина
сниппетов, промпты синтеза крутятся часто — и каждый твик может незаметно
уронить ответы по архиву. Бенч гоняет реальный RAG-контур (поиск по графу →
синтез локальной моделью) на вопросах из config/memory_bench.yaml и
проверяет, что обязательные факты (`must`) присутствуют в ответе.

Формат memory_bench.yaml:
    - q: "Что решили по доступу к API?"
      must: ["токен", "403"]

Сверка подстрокой: нормализация регистра и ё→е. Итог N/M и список провалов;
exit code 1, если провалов больше трети — заметная деградация.

    .venv/bin/python scripts/memory_bench.py            # весь бенч
    .venv/bin/python scripts/memory_bench.py --limit 3  # быстрый смок
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from llm import LLM  # noqa: E402

SNIPPET = 1200   # как в боевом RAG приложения
LIMIT_FILES = 5


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def search(graph: pathlib.Path, query: str) -> str:
    """Та же механика, что vault_search: слова → скоринг файлов → сниппеты."""
    stop = {"что", "как", "где", "когда", "это", "нас", "есть", "про", "для",
            "или", "чем", "кто", "было", "быть", "по", "мы", "решили"}
    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9_-]{3,}", query)
             if norm(w) not in stop]
    rx = re.compile("|".join(re.escape(w) for w in words), re.I) if words else re.compile(re.escape(query), re.I)
    scored: list[tuple[int, str]] = []
    for p in graph.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = rx.search(text)
        if not m:
            continue
        low = norm(text)
        rel = str(p.relative_to(graph))
        score = sum(1 for w in words if norm(w) in low)
        score += sum(3 for w in words if norm(w) in norm(rel))
        start = max(0, m.start() - 150)
        frag = " ".join(text[start:m.end() + SNIPPET].split())
        scored.append((score, f"• {rel}\n  …{frag}…"))
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(h for _, h in scored[:LIMIT_FILES])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="только первые N вопросов")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = pathlib.Path(cfg["sufler"]["graph_dir"]).expanduser()
    bench_file = ROOT / "config" / "memory_bench.yaml"  # см. memory_bench.example.yaml
    if not bench_file.exists():
        sys.exit(f"нет файла бенча: {bench_file}")
    cases = yaml.safe_load(bench_file.read_text(encoding="utf-8")) or []
    if args.limit:
        cases = cases[:args.limit]

    llm = LLM(cfg)
    passed, failures = 0, []
    for i, case in enumerate(cases, 1):
        q, must = case["q"], case.get("must", [])
        found = search(graph, q)
        if not found:
            failures.append((q, must, "поиск ничего не нашёл"))
            print(f"[{i}/{len(cases)}] ✗ {q} — поиск пуст")
            continue
        answer = "".join(llm.stream(
            f"Вопрос: {q}\n\nФрагменты из архива встреч:\n{found}\n\n"
            "Ответь на вопрос по фрагментам: кратко, с конкретными фактами "
            "(имена, числа, идентификаторы) из фрагментов. Ничего не выдумывай.",
            system="Ты — ассистент по архиву рабочих встреч. Только факты из фрагментов.",
        ))
        missing = [m for m in must if norm(m) not in norm(answer)]
        if missing:
            failures.append((q, missing, answer[:160]))
            print(f"[{i}/{len(cases)}] ✗ {q} — нет: {missing}")
        else:
            passed += 1
            print(f"[{i}/{len(cases)}] ✓ {q}")

    total = len(cases)
    print(f"\nИТОГ: {passed}/{total}")
    for q, missing, ctx in failures:
        print(f"  ✗ «{q}»: не найдено {missing}\n    ответ: {ctx}…")
    if total and passed < total * 2 / 3:
        sys.exit(1)   # деградация больше трети — сигнал в ночном логе


if __name__ == "__main__":
    main()
