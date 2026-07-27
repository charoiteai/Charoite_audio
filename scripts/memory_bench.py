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


def search_brain(graph: pathlib.Path, query: str) -> str | None:
    """Боевой контур: vault_search на brain :8100 (тот же, что в приложении).

    Бенч обязан мерить то, что видит пользователь, а не свою копию
    алгоритма — иначе улучшения ранжирования в brain остаются незамеренными.
    None → сервер лежит, вызывающий уходит на локальный фолбэк.
    """
    import json
    import urllib.request

    folder = graph.name  # стандартная раскладка: vault/<граф>/…
    # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
    req = urllib.request.Request(
        "http://127.0.0.1:8100/vault_search",
        data=json.dumps({"query": query, "folder": folder,
                         "limit": LIMIT_FILES, "snippet_chars": SNIPPET},
                        ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
        with urllib.request.urlopen(req, timeout=25) as resp:
            text = json.load(resp).get("text", "")
    except OSError:
        return None
    if text.startswith("Ничего не найдено"):
        return ""
    # граф вне vault brain-сервера (демо, другой диск): честный фолбэк
    # на локальный поиск, а не сообщение об ошибке в роли «сырья»
    if text.startswith("Папка не найдена") or text.startswith("Недопустимый путь"):
        return None
    # срезаем шапку «Найдено в vault (N из M):»
    _, _, body = text.partition("\n\n")
    return body or text


def search(graph: pathlib.Path, query: str) -> str:
    """Локальный фолбэк: та же механика, что vault_search: слова → скоринг файлов → сниппеты."""
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
    ap.add_argument("--demo", action="store_true",
                    help="демо-граф из репозитория вместо вашего: проверка контура без встреч")
    ap.add_argument("--demo-en", action="store_true",
                    help="английский демо-граф (demo/graph_en) и английские кейсы")
    args = ap.parse_args()

    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.exists() and (args.demo or args.demo_en):
        # демо-режим работает и до настройки: дефолтная модель Ollama
        cfg = {"llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b"},
               "sufler": {"role": "Ассистент по архиву встреч."}}
    else:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if args.demo_en:
        args.demo = True
        graph = ROOT / "demo" / "graph_en"
        bench_file = ROOT / "config" / "memory_bench_demo_en.yaml"
    elif args.demo:
        graph = ROOT / "demo" / "graph"
        bench_file = ROOT / "config" / "memory_bench_demo.yaml"
    else:
        graph = pathlib.Path(cfg["sufler"]["graph_dir"]).expanduser()
        bench_file = ROOT / "config" / "memory_bench.yaml"  # см. memory_bench.example.yaml
    if not bench_file.exists():
        sys.exit(f"нет файла бенча: {bench_file}")
    cases = yaml.safe_load(bench_file.read_text(encoding="utf-8")) or []
    if args.limit:
        cases = cases[:args.limit]

    llm = LLM(cfg)
    passed, failures = 0, []
    # демо-граф живёт в репозитории, вне vault brain-сервера — только локальный
    brain_alive = (not args.demo) and search_brain(graph, "проверка") is not None
    print(f"контур поиска: {'brain :8100 (боевой)' if brain_alive else 'локальный фолбэк (brain лежит)'}")
    for i, case in enumerate(cases, 1):
        q, must = case["q"], case.get("must", [])
        found = (search_brain(graph, q) if brain_alive else None)
        if found is None:
            found = search(graph, q)
        if not found:
            failures.append((q, must, "поиск ничего не нашёл"))
            print(f"[{i}/{len(cases)}] ✗ {q} — поиск пуст")
            continue
        # диагностика: чей провал — ПОИСКА (факт не в выдаче) или СИНТЕЗА
        # (факт в выдаче, LLM не включил в ответ). Лечатся по-разному.
        retr_missing = [m for m in must if norm(m) not in norm(found)]
        answer = "".join(llm.stream(
            f"Вопрос: {q}\n\nФрагменты из архива встреч:\n{found}\n\n"
            "Ответь на вопрос по фрагментам: кратко, с конкретными фактами "
            "(имена, числа, идентификаторы) из фрагментов. Ничего не выдумывай.",
            system="Ты — ассистент по архиву рабочих встреч. Только факты из фрагментов.",
            temperature=0.0,  # бенч — регрессия, не творчество: убираем флап
        ))
        missing = [m for m in must if norm(m) not in norm(answer)]
        if missing:
            stage = "ПОИСК" if retr_missing else "синтез"
            failures.append((q, missing, f"[{stage}] " + answer[:160]))
            print(f"[{i}/{len(cases)}] ✗ {q} — нет: {missing} (этап: {stage})")
        else:
            passed += 1
            note = f" (в выдаче не было: {retr_missing})" if retr_missing else ""
            print(f"[{i}/{len(cases)}] ✓ {q}{note}")

    total = len(cases)
    print(f"\nИТОГ: {passed}/{total}")
    for q, missing, ctx in failures:
        print(f"  ✗ «{q}»: не найдено {missing}\n    ответ: {ctx}…")
    if total and passed < total * 2 / 3:
        sys.exit(1)   # деградация больше трети — сигнал в ночном логе


if __name__ == "__main__":
    main()
