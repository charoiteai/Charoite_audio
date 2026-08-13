#!/usr/bin/env python3
"""Сравнение моделей на РАЗБОРЕ встречи, а не на скорости.

bench_models.py отвечает на вопрос «кто быстрее», и по нему 12.08 конфиг
переехал на MLX-сборку. Вопрос «кто точнее» остался открытым: качество
сравнивали на одной минутке. Здесь — та же функция разбора, что ходит в граф
(graph_updater.extract), одни и те же стенограммы, разные модели.

Что меряется. Содержательную полноту («попал ли в поручения») машина не
рассудит — её читает человек, для этого сырые ответы пишутся рядом. Зато
объективно проверяются вещи, на которых модель ломается тихо:

  цитаты   доля «цитат» в ядрах, которые НАЙДЕНЫ в стенограмме тем же
           поиском, что использует граф (_closest_span). Выдуманная цитата —
           это выдуманное основание для узла: сам узел выглядит нормально,
           и заметить подмену можно только так
  время    доля отметок ЧЧ:ММ из ядер, реально встречающихся в стенограмме
  объём    сколько решений, ядер, людей, сущностей вытащено
  json     вернула ли модель разбираемый ответ вообще

Модели гоняются по очереди, все стенограммы на одной — потом следующая:
две модели по 20+ ГБ в памяти не живут, и чередование превратило бы замер
в измерение скорости выгрузки.

Запуск:
    .venv/bin/python scripts/bench_extract.py qwen3.6:35b-a3b qwen3.6:35b-mlx
    .venv/bin/python scripts/bench_extract.py --meetings 5 <модель> <модель>
    .venv/bin/python scripts/bench_extract.py --files a.md b.md <модель>...

Машина должна быть свободна: при живой встрече замер отберёт память у
подсказок — и покажет очередь, а не модель.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

# Код берём рядом со скриптом, данные — из корня установки: во вложенной
# установке (код в одном месте, стенограммы и логи в другом) это разные папки,
# и «src» от корня данных там просто нет.
CODE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CODE / "src"))

import graph_updater as gu  # noqa: E402
from charoite_paths import resolve_root  # noqa: E402

ROOT = resolve_root(__file__)

OUT = ROOT / "logs" / "bench_extract"
# Служебные соседи стенограммы: подсказки, минутки, разбор, живой черновик,
# облачная ревизия. Их разбирать бессмысленно — это уже продукты модели, а не
# речь людей, и модель на них меряет саму себя.
SUFFIXES = ("_hints", "_minutes", "_разбор", "_live", "_ревизия_claude")
# Стенограмма, которую дописывали только что, — либо идущая встреча, либо
# ещё не пересобранная. Замер на половине разговора сравнивает не модели.
FRESH_S = 10 * 60


def meetings(limit: int, now: float | None = None) -> list[pathlib.Path]:
    """Свежие стенограммы встреч, без служебных файлов и незакрытых записей."""
    now = time.time() if now is None else now
    files = [p for p in (ROOT / "transcripts").glob("*.md")
             if not any(p.stem.endswith(s) for s in SUFFIXES)
             and now - p.stat().st_mtime > FRESH_S]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def quote_hits(data: dict, transcript: str) -> tuple[int, int]:
    """Сколько цитат из ядер нашлось в стенограмме — и сколько их всего."""
    quotes = [str(c.get("цитата", "")).strip()
              for c in (data.get("ядра") or []) if isinstance(c, dict)]
    quotes = [q for q in quotes if q]
    if not quotes:
        return 0, 0
    return sum(1 for q in quotes if gu._closest_span(q, transcript)), len(quotes)


def time_hits(data: dict, transcript: str) -> tuple[int, int]:
    """Сколько отметок ЧЧ:ММ из ядер реально стоят в стенограмме."""
    stamps = [str(c.get("время", "")).strip()
              for c in (data.get("ядра") or []) if isinstance(c, dict)]
    stamps = [s for s in stamps if re.fullmatch(r"\d{1,2}:\d{2}", s)]
    if not stamps:
        return 0, 0
    return sum(1 for s in stamps if s in transcript), len(stamps)


def measure(cfg: dict, model: str, path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    cfg = {**cfg, "llm": {**cfg["llm"], "model": model}}
    started = time.time()
    data = gu.extract(cfg, text)
    took = time.time() - started
    if not isinstance(data, dict):
        return {"файл": path.stem, "модель": model, "сек": took, "json": False}
    q_hit, q_all = quote_hits(data, text)
    t_hit, t_all = time_hits(data, text)
    return {
        "файл": path.stem, "модель": model, "сек": took, "json": True,
        "решения": len(data.get("решения") or []),
        "ядра": len(data.get("ядра") or []),
        "люди": len(data.get("люди") or []),
        "сущности": len(data.get("сущности") or []),
        "цитаты": (q_hit, q_all),
        "время": (t_hit, t_all),
        "ответ": data,
    }


def share(hits: int, total: int) -> str:
    return "—" if not total else f"{hits}/{total} ({100 * hits // total}%)"


def report(rows: list[dict]) -> None:
    print(f"\n{'модель':22} {'файл':34} {'сек':>6} {'реш':>4} {'ядра':>5} "
          f"{'цитаты':>12} {'время':>12}")
    for r in rows:
        if not r["json"]:
            print(f"{r['модель']:22} {r['файл'][:34]:34} {r['сек']:6.0f} "
                  f"{'— ответ не разобрался':>40}")
            continue
        print(f"{r['модель']:22} {r['файл'][:34]:34} {r['сек']:6.0f} "
              f"{r['решения']:4} {r['ядра']:5} {share(*r['цитаты']):>12} "
              f"{share(*r['время']):>12}")
    print("\nИтого по моделям:")
    for model in dict.fromkeys(r["модель"] for r in rows):
        mine = [r for r in rows if r["модель"] == model]
        ok = [r for r in mine if r["json"]]
        q = (sum(r["цитаты"][0] for r in ok), sum(r["цитаты"][1] for r in ok))
        t = (sum(r["время"][0] for r in ok), sum(r["время"][1] for r in ok))
        print(f"  {model:22} разобрано {len(ok)}/{len(mine)}, "
              f"решений {sum(r['решения'] for r in ok)}, "
              f"ядер {sum(r['ядра'] for r in ok)}, "
              f"цитаты {share(*q)}, время {share(*t)}, "
              f"медиана {sorted(r['сек'] for r in mine)[len(mine) // 2]:.0f} с")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="+", help="теги моделей Ollama")
    ap.add_argument("--meetings", type=int, default=3,
                    help="сколько свежих встреч взять (по умолчанию 3)")
    ap.add_argument("--files", nargs="*", type=pathlib.Path,
                    help="конкретные стенограммы вместо свежих")
    args = ap.parse_args()

    files = args.files or meetings(args.meetings)
    if not files:
        print("нет стенограмм для замера")
        return
    cfg = gu.load_cfg()
    print(f"встреч: {len(files)}, моделей: {len(args.models)}")
    for f in files:
        print(f"  {f.stem} ({f.stat().st_size // 1000} тыс. знаков)")

    rows = []
    OUT.mkdir(parents=True, exist_ok=True)
    # Внешний цикл по МОДЕЛЯМ, внутренний по встречам: так каждая модель
    # грузится в память один раз.
    for model in args.models:
        for f in files:
            print(f"… {model} на {f.stem}", flush=True)
            r = measure(cfg, model, f)
            answer = r.pop("ответ", None)
            if answer is not None:
                # Сырые ответы рядом: полноту решений и попадание в поручения
                # читает человек, и ему нужен текст, а не только числа.
                (OUT / f"{f.stem}__{model.replace(':', '_').replace('/', '_')}.json"
                 ).write_text(json.dumps(answer, ensure_ascii=False, indent=1),
                              encoding="utf-8")
            rows.append(r)
    report(rows)
    print(f"\nсырые разборы: {OUT}")


if __name__ == "__main__":
    main()
