#!/usr/bin/env python3
"""Замер решающего гейта (src/decision_gate.py): сколько пустых вызовов модели
он снял бы и сколько настоящих вопросов потерял.

Гейт в каскаде молчит за модель только там, где уверен, поэтому решают две
цифры на каждый порог уверенности τ:

* «снято» — доля реплик без вопроса, которые гейт уверенно отсёк (сэкономленные
  вызовы модели и облака);
* «потеряно» — доля настоящих вопросов, которые он уверенно отсёк бы (ответ,
  которого человек не получит). Цена ошибки несимметрична: потеря вопроса хуже
  лишнего вызова.

Три режима:

    # 1. кандидаты из своих стенограмм — то, что сегодня видит ⚡ (разметить руками)
    .venv/bin/python scripts/gate_bench.py harvest ~/Charoite/transcripts --out gate.jsonl

    # 2. размеченный набор: структурный фильтр против NLI zero-shot / обученной головы
    .venv/bin/python scripts/gate_bench.py eval gate.jsonl --backend structural,nli

    # 3. тень с живых встреч (sufler.decision_gate_shadow: true): исход ⚡ как метка
    .venv/bin/python scripts/gate_bench.py shadow logs/daemon.err.log

Формат размеченного набора — JSONL, строка на реплику:
{"text": "Когда релиз?", "label": "ask"} — label: ask | skip.
В тени метка слабая: отказ модели («вопроса не вижу») считается skip, ответ —
ask. Это не истина, а ровно та цена, которую гейт призван снять.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import decision_gate as dg  # noqa: E402
import meeting_stamp  # noqa: E402
import nli  # noqa: E402
import question_filter  # noqa: E402
import transcript  # noqa: E402
from charoite_paths import harden_umask  # noqa: E402

DEFAULT_THRESHOLDS = (0.6, 0.7, 0.8, 0.9, 0.95)
BACKENDS = ("structural", "nli", "head")


@dataclasses.dataclass(frozen=True)
class Row:
    """Одна реплика замера: истина, вердикт, уверенность, задержка."""

    gold: str
    label: str
    confidence: float
    ms: float = 0.0


def _share(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def ece(rows: list[Row], bins: int = 10) -> float | None:
    """Ожидаемая ошибка калибровки: насколько уверенность врёт о точности.

    Для каскада это главное свойство: порог τ имеет смысл, только если «0.9»
    действительно значит «прав девять раз из десяти».
    """
    if not rows:
        return None
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [r for r in rows if lo < r.confidence <= hi or (b == 0 and r.confidence == 0)]
        if not inside:
            continue
        acc = sum(r.gold == r.label for r in inside) / len(inside)
        conf = sum(r.confidence for r in inside) / len(inside)
        total += len(inside) / len(rows) * abs(acc - conf)
    return total


def summary(rows: list[Row]) -> dict:
    """Сводка без порога: точность, удержание вопросов, отсев пустого, ECE, задержка."""
    asks = [r for r in rows if r.gold == "ask"]
    skips = [r for r in rows if r.gold == "skip"]
    times = sorted(r.ms for r in rows if r.ms > 0)
    return {
        "n": len(rows),
        "accuracy": _share(sum(r.gold == r.label for r in rows), len(rows)),
        "ask_kept": _share(sum(r.label == "ask" for r in asks), len(asks)),
        "skip_caught": _share(sum(r.label == "skip" for r in skips), len(skips)),
        "ece": ece(rows),
        "p50_ms": statistics.median(times) if times else None,
        "p95_ms": times[min(len(times) - 1, int(0.95 * len(times)))] if times else None,
    }


def sweep(rows: list[Row], thresholds=DEFAULT_THRESHOLDS) -> list[dict]:
    """Каскад по порогам: гейт отсекает, только если уверен в skip не меньше τ."""
    asks = sum(r.gold == "ask" for r in rows)
    skips = sum(r.gold == "skip" for r in rows)
    out = []
    for tau in thresholds:
        cut = [r for r in rows if dg.cascade(_verdict(r), tau) == "skip"]
        decided = [r for r in rows if dg.cascade(_verdict(r), tau) != "escalate"]
        out.append({
            "tau": tau,
            "decided": _share(len(decided), len(rows)),
            "decided_acc": _share(sum(r.gold == r.label for r in decided), len(decided)),
            "saved": _share(sum(r.gold == "skip" for r in cut), skips),
            "lost": _share(sum(r.gold == "ask" for r in cut), asks),
        })
    return out


def _verdict(r: Row) -> dg.Verdict:
    return dg.Verdict(r.label, r.confidence, {}, "bench", r.ms)


def read_labeled(path: pathlib.Path) -> tuple[list[dict], int]:
    """Размеченный JSONL → строки с меткой ask/skip и число пропущенных."""
    rows, skipped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("label") in dg.LABELS and str(item.get("text") or "").strip():
            rows.append(item)
        else:
            skipped += 1
    return rows, skipped


def structural_row(text: str, gold: str) -> Row:
    """Сегодняшний фильтр ⚡ как бэкенд: детерминирован, уверенность 1.0."""
    label = "ask" if question_filter.is_worth_asking(text) else "skip"
    return Row(gold, label, 1.0)


def shadow_rows(lines) -> tuple[dict[str, list[Row]], int]:
    """Строки тени → замер по бэкендам. Исход «failed» метки не даёт и не
    считается; вердикт none (решатель опоздал или упал) считается отдельно."""
    by_backend: dict[str, list[Row]] = {}
    missing = 0
    for line in lines:
        rec = dg.parse_shadow_line(line)
        if rec is None or rec["outcome"] == "failed":
            continue
        if rec.get("verdict") == "none":
            missing += 1
            continue
        gold = "skip" if rec["outcome"] == "refusal" else "ask"
        by_backend.setdefault(rec["backend"], []).append(
            Row(gold, rec["label"], rec["p"], rec["ms"]))
    return by_backend, missing


_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def harvest(directory: pathlib.Path) -> list[dict]:
    """Кандидаты для разметки: фразы стенограмм, которые сегодня будят ⚡.

    Берутся только главные файлы встреч (производные `_hints`, `_minutes` и
    прочие — мимо, их отсекает `meeting_stamp.stamp_of`). Фраза — кандидат, если
    её пропускает `looks_question`: ровно этот поток видит гейт.
    """
    out, seen = [], set()
    for path in sorted(directory.rglob("*.md")):
        if not meeting_stamp.stamp_of(path.stem):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for block in transcript.parse_blocks(text):
            body = " ".join(text[block["start"]:block["end"]].split())
            for phrase in _SENTENCE.split(body):
                phrase = phrase.strip()
                key = phrase.lower()
                if not phrase or key in seen or not question_filter.looks_question(phrase):
                    continue
                seen.add(key)
                out.append({
                    "text": phrase,
                    "label": "",
                    "structural": "ask" if question_filter.is_worth_asking(phrase) else "skip",
                    "speaker": block["speaker"],
                    "source": path.stem,
                })
    return out


def _fmt(x, pct: bool = True) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.1f}%" if pct else f"{x:.0f}"


def print_report(name: str, rows: list[Row], thresholds) -> None:
    s = summary(rows)
    print(f"\n## {name}: {s['n']} реплик")
    calib = "—" if s["ece"] is None else f"{s['ece']:.3f}"
    print(f"точность {_fmt(s['accuracy'])} · вопросов удержано {_fmt(s['ask_kept'])} · "
          f"пустого отсеяно {_fmt(s['skip_caught'])} · ECE {calib} · "
          f"p50 {_fmt(s['p50_ms'], False)} мс · p95 {_fmt(s['p95_ms'], False)} мс")
    if name.startswith("structural"):
        return      # детерминированному фильтру порог не нужен
    print("   τ  | решено сам | точность решённого | снято пустого | потеряно вопросов")
    for p in sweep(rows, thresholds):
        print(f"  {p['tau']:.2f} | {_fmt(p['decided']):>10} | {_fmt(p['decided_acc']):>18} | "
              f"{_fmt(p['saved']):>13} | {_fmt(p['lost']):>17}")


def _thresholds(raw: str) -> tuple[float, ...]:
    values = tuple(float(x) for x in raw.split(",") if x.strip())
    for v in values:
        dg.cascade(None, v)     # та же проверка порога, что у гейта: (0.5, 1]
    return values


def _backend(name: str, args) -> dg.Decider | None:
    hyps = {"ask": args.hyp_ask, "skip": args.hyp_skip}
    if name == "nli":
        judge = nli.judge()
        if judge.refused:
            print(f"nli: {judge.refused}", file=sys.stderr)
            return None
        return dg.Decider("nli-zero-shot", lambda t: dg.zero_shot_probs(t, judge.entail, hyps))
    head_dir = pathlib.Path(args.head_dir) if args.head_dir else dg.default_head_dir()
    if not all((head_dir / f).exists() for f in dg.HEAD_FILES):
        print(f"head: в {head_dir} нет {', '.join(dg.HEAD_FILES)}", file=sys.stderr)
        return None
    return dg.Decider(f"head:{head_dir.name}", dg.load_head(head_dir))


def cmd_eval(args) -> int:
    items, skipped = read_labeled(pathlib.Path(args.data))
    if skipped:
        print(f"пропущено строк без метки ask/skip: {skipped}", file=sys.stderr)
    if not items:
        print("размеченных строк нет — сначала harvest и разметка", file=sys.stderr)
        return 2
    thresholds = _thresholds(args.thresholds)
    for name in [b.strip() for b in args.backend.split(",") if b.strip()]:
        if name not in BACKENDS:
            print(f"бэкенд {name!r} неизвестен: {', '.join(BACKENDS)}", file=sys.stderr)
            return 2
        if name == "structural":
            print_report("structural (question_filter)",
                         [structural_row(i["text"], i["label"]) for i in items], thresholds)
            continue
        dec = _backend(name, args)
        if dec is None:
            continue
        rows = []
        for i in items:
            v = dg.decide(dec, i["text"])
            rows.append(Row(i["label"], v.label, v.confidence, v.ms))
        print_report(dec.name, rows, thresholds)
    return 0


def cmd_shadow(args) -> int:
    lines: list[str] = []
    for p in args.logs:
        lines += pathlib.Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
    by_backend, missing = shadow_rows(lines)
    if missing:
        print(f"вердиктов нет (решатель опоздал или упал): {missing}")
    if not by_backend:
        print("строк тени нет: включите sufler.decision_gate_shadow и проведите встречу",
              file=sys.stderr)
        return 2
    thresholds = _thresholds(args.thresholds)
    for backend, rows in sorted(by_backend.items()):
        print_report(f"{backend} (тень: отказ модели = skip)", rows, thresholds)
    return 0


def cmd_harvest(args) -> int:
    rows = harvest(pathlib.Path(args.dir))
    if args.limit:
        rows = rows[:args.limit]
    out = pathlib.Path(args.out)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    print(f"{len(rows)} кандидатов → {out}; проставьте label: ask | skip")
    return 0


def main(argv: list[str] | None = None) -> int:
    harden_umask()   # harvest пишет реплики встреч в файл — только владельцу (№385)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("eval", help="размеченный JSONL: бэкенды и каскад по порогам")
    ev.add_argument("data")
    ev.add_argument("--backend", default="structural,nli",
                    help=f"через запятую: {', '.join(BACKENDS)}")
    ev.add_argument("--head-dir", default="", help="каталог обученной головы (по умолчанию models/decision/question_gate)")
    ev.add_argument("--hyp-ask", default=dg.HYPOTHESES["ask"])
    ev.add_argument("--hyp-skip", default=dg.HYPOTHESES["skip"])
    ev.add_argument("--thresholds", default=",".join(map(str, DEFAULT_THRESHOLDS)))
    ev.set_defaults(fn=cmd_eval)

    sh = sub.add_parser("shadow", help="строки тени из err-лога демона")
    sh.add_argument("logs", nargs="+")
    sh.add_argument("--thresholds", default=",".join(map(str, DEFAULT_THRESHOLDS)))
    sh.set_defaults(fn=cmd_shadow)

    hv = sub.add_parser("harvest", help="кандидаты для разметки из стенограмм")
    hv.add_argument("dir")
    hv.add_argument("--out", required=True)
    hv.add_argument("--limit", type=int, default=0)
    hv.set_defaults(fn=cmd_harvest)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
