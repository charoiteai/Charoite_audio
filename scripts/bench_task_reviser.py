#!/usr/bin/env python3
"""Синтетический замер ревизора поручений (№998): вердикты, сроки и полный путь правки.

Набор — tests/fixtures/task_reviser_cases.jsonl: типичные случаи протоколов (дело сделано, не
поручение, открытое поручение, подвохи: инъекция, «сказал, что отправит», «забыл отправить»), имена
и темы вымышленные. Реальные встречи не читаются. Отвечает та же локальная модель, что и в бою;
запись идёт только в синтетический граф во временном каталоге.

  bench_task_reviser.py                 замер и отчёт «вредных правок / пропусков»
  bench_task_reviser.py --json FILE     то же с записью машинного результата
  bench_task_reviser.py --model NAME    другая модель
  bench_task_reviser.py --cases PATH    другой набор случаев (JSON Lines)

Критерий до включения записи (карточка №998): вредных правок (строка должна остаться открытой, а
ревизор её снял) — 0. Пропуски (дело сделано, а строка осталась открытой) допустимы: они лишь
откладывают автоматизацию, но считаются отдельно. Любая вредная правка, неверный вердикт, неверный
срок или сбой полного пути — ненулевой код выхода.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import charoite_paths  # noqa: E402
import task_reviser as tr  # noqa: E402


CASES_PATH = charoite_paths.code_root(__file__) / "tests/fixtures/task_reviser_cases.jsonl"
#: Встреча для полного пути; today рядом с ней, чтобы правила возраста не снимали строки.
FIXED_ANCHOR = dt.date(2026, 10, 5)


def load_cases(path: pathlib.Path = CASES_PATH) -> tuple[list[tuple[str, str]], list[tuple[str, dt.date, dt.date | None]]]:
    """(строка, ожидаемый вердикт) и (строка, дата встречи, ожидаемый срок | None)."""
    verdict, due = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        case = json.loads(raw)
        if case["kind"] == "verdict":
            verdict.append((case["line"], case["want"]))
        elif case["kind"] == "due":
            want = dt.date.fromisoformat(case["want"]) if case["want"] else None
            due.append((case["line"], dt.date.fromisoformat(case["meeting_date"]), want))
        else:
            raise SystemExit(f"{path}: неизвестный вид случая {case['kind']!r}")
    if not verdict or not due:
        raise SystemExit(f"{path}: пустой набор случаев")
    return verdict, due
def retrying_asker(text, anchor, today, variant, model=tr.MODEL):
    """Один повтор на молчание модели: в бою строка просто проверится в следующий прогон."""
    verdict = tr.ask(text, anchor, today, variant, model)
    return verdict if verdict is not None else tr.ask(text, anchor, today, variant, model)


def retrying_due(text, anchor, variant, model=tr.MODEL):
    try:
        return tr.ask_due(text, anchor, variant, model)
    except tr._NoAnswer:
        return tr.ask_due(text, anchor, variant, model)


def masking_problems(missed: int, plan_missed: int, cases) -> list[str]:
    """Порог «модель не работает» — от случаев, где действие требовалось, а не от всех подряд.

    «Открыто» — законный ответ для 21 из 37 строк набора, поэтому missed * 2 >= total недостижимо:
    полное вырождение модели в константное «open» выглядело бы «чисто» (замечание второго ревью)."""
    actionable = sum(1 for _, want in cases if want != "open")
    problems: list[str] = []
    if actionable and missed * 2 >= actionable:
        problems.append(f"пропусков {missed}/{actionable} — модель фактически не отвечает (пустой прогон не «чистый»)")
    if actionable and plan_missed * 2 >= actionable:
        problems.append(f"полный путь: пропусков {plan_missed}/{actionable} — модель не работает")
    return problems


def main() -> int:
    charoite_paths.harden_umask()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json")
    ap.add_argument("--model", default=tr.MODEL)
    ap.add_argument("--cases", default=CASES_PATH, type=pathlib.Path)
    args = ap.parse_args()

    CASES, DUE_CASES = load_cases(args.cases)

    if not tr.model_alive(args.model):
        print(f"модель {args.model} недоступна")
        return 2
    today = dt.date.today()
    started = time.monotonic()
    rows, harmful, missed, wrong = [], 0, 0, 0
    for text, want in CASES:
        got = tr.judge(text, FIXED_ANCHOR, today, model=args.model, asker=retrying_asker)
        # None — модель не ответила: это не вердикт, правки не будет; учитываем как пропуск.
        if got == want:
            mark = "ok"
        elif got is None:                                 # модель не ответила — не вердикт и не правка
            mark = "без ответа"
        elif want == "open":
            mark = "ВРЕДНАЯ ПРАВКА"
            harmful += 1
        elif got == "open":
            mark = "пропуск"
            missed += 1
        else:
            mark = "неверный вердикт"
            wrong += 1
        rows.append({"text": text, "want": want, "got": got, "mark": mark})

    due_rows, due_ok, due_bad = [], 0, 0
    for text, anchor, want_due in DUE_CASES:
        confirmed, due = tr.judge_due(text, anchor, model=args.model, due_asker=retrying_due)
        got = due if confirmed else None
        ok = confirmed and due == want_due
        due_ok += bool(ok)
        due_bad += not ok
        due_rows.append({"text": text, "want": str(want_due) if want_due else None,
                         "got": str(got) if got else None, "confirmed": confirmed, "ok": ok})

    # Полный путь: plan_edits на синтетическом графе (today рядом со встречей, чтобы правила возраста
    # не снимали строки). Вредной считается правка строки, которая должна остаться открытой.
    plan_harmful, plan_missed = [], []
    plan_rows: list[dict] = []
    # MAX_EDITS ограничивает прогон: без батчей часть случаев не проверялась бы вовсе, и «чистый»
    # результат полного пути молчал бы о непройденных строках.
    asked_plan = failed_plan = 0
    plan_wrong: list[str] = []
    edits_by_index: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        data = pathlib.Path(tmp) / "data"
        for start in range(0, len(CASES), tr.MAX_EDITS):
            chunk = CASES[start:start + tr.MAX_EDITS]
            graph = pathlib.Path(tmp) / f"граф{start}"
            rel = f"Встречи-архив/{FIXED_ANCHOR:%Y-%m-%d} 10-00 — Бенч/{tr.tc.MINUTES}"
            (graph / rel).parent.mkdir(parents=True)
            texts = [re.sub(r"^(?:Коля|Оля|Иван|Саша|Пётр|Мария|Анна|Сергей) — ", "", text) for text, _ in chunk]
            (graph / rel).write_text("\n".join(f"- [ ] **Коля** — {t}" for t in texts) + "\n", encoding="utf-8")
            plan, _, asked_chunk, failed_chunk = tr.plan_edits(
                graph, "Пётр", FIXED_ANCHOR + dt.timedelta(days=1), set(), {},
                asker=retrying_asker, due_asker=retrying_due, model=args.model,
                deadline=time.monotonic() + 900)
            asked_plan += asked_chunk
            failed_plan += failed_chunk
            edits_by_index.update({start + i: new for edits in plan.values() for i, _, new, _ in edits})
            # Запись тем же путём, что в бою: apply на графе ЭТОГО батча. Раньше apply вызывался один
            # раз после цикла — на графе последнего батча и с его планом, а планы остальных батчей
            # в запись не попадали вовсе (замечание второго ревью).
            try:
                applied_writes, skipped_writes, _ = tr.tc.apply(graph, plan, data)
            except Exception as e:                        # noqa: BLE001 — замер должен падать с причиной
                plan_wrong.append(f"apply (батч {start}): {type(e).__name__}: {e}")
            else:
                planned = sum(len(v) for v in plan.values())
                if len(applied_writes) != planned:
                    plan_wrong.append(f"apply (батч {start}) записал {len(applied_writes)} из {planned}")
                if skipped_writes:
                    plan_wrong.append(f"apply (батч {start}) пропустил {skipped_writes}")
    edited = set(edits_by_index)
    for i, (text, want) in enumerate(CASES):
        new_line = edits_by_index.get(i)
        if new_line is not None and want == "open":
            plan_harmful.append(text)
        if new_line is None and want != "open":
            plan_missed.append(text)
        if new_line is not None:
            if want == "done" and " [x] " not in new_line:
                plan_wrong.append(text)
            if want == "not_task" and not (" [-] " in new_line and tr.tc.REASONS["не поручение"] in new_line):
                plan_wrong.append(text)
    plan_rows = [{"text": t, "want": w, "edited": i in edited} for i, (t, w) in enumerate(CASES)]

    elapsed = time.monotonic() - started
    total = len(CASES)
    correct = sum(r["mark"] == "ok" for r in rows)
    unanswered = sum(r["got"] is None for r in rows)
    print("=== вердикты ===")
    for r in rows:
        if r["mark"] != "ok":
            print(f"  [{r['mark']}] хотели {r['want']}, получили {r['got']}: {r['text']}")
    print(f"верно {correct}/{total}; вредных правок {harmful}; пропусков {missed}; неверных {wrong}; "
          f"без ответа {unanswered}")
    print(f"полный путь plan_edits: опрошено {asked_plan}, сбоев {failed_plan}, "
          f"вредных правок {len(plan_harmful)}, пропусков {len(plan_missed)}")
    if plan_harmful:
        print("  ВРЕДНЫЕ ПРАВКИ В ПОЛНОМ ПУТИ:", "; ".join(plan_harmful[:5]))
    print("=== сроки ===")
    for r in due_rows:
        if not r["ok"]:
            print(f"  [неверно] хотели {r['want']}, получили {r['got']} (срок подтверждён: {r['confirmed']}): {r['text']}")
    print(f"срок верно {due_ok}/{len(DUE_CASES)}")
    print(f"модель {args.model}, время {elapsed:.1f} с")
    problems = []
    if harmful:
        problems.append(f"вредных правок: {harmful}")
    if plan_harmful:
        problems.append(f"вредных правок в полном пути: {len(plan_harmful)}")
    if unanswered:
        problems.append(f"без ответа модели: {unanswered}")
    problems.extend(masking_problems(missed, len(plan_missed), CASES))
    if wrong:
        problems.append(f"неверных вердиктов: {wrong}")
    if failed_plan:
        problems.append(f"полный путь: сбоев модели {failed_plan}")
    if plan_wrong:
        problems.append(f"полный путь: правок не того типа {len(plan_wrong)}")
    if due_bad:
        problems.append(f"сроки: неверных {due_bad}")
    print("замер ревизора:", "чисто" if not problems else "ЕСТЬ ПРОБЛЕМЫ — " + "; ".join(problems))
    if args.json:
        out = {"model": args.model, "when": dt.datetime.now().isoformat(timespec="seconds"),
               "today": today.isoformat(), "anchor": FIXED_ANCHOR.isoformat(), "elapsed_s": round(elapsed, 1),
               "verdicts": rows, "due": due_rows, "plan": plan_rows,
               "summary": {"correct": correct, "total": total, "harmful": harmful,
                           "missed": missed, "wrong": wrong, "unanswered": unanswered,
                           "plan_harmful": len(plan_harmful), "plan_missed": len(plan_missed),
                           "plan_wrong": len(plan_wrong),
                           "plan_asked": asked_plan, "plan_failed": failed_plan,
                           "due_ok": due_ok, "due_total": len(DUE_CASES),
                           "clean": not problems}}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
