#!/usr/bin/env python3
"""Compare ranked retrieval runs on the same frozen judgments; no model/network calls.

python scripts/retrieval_compare.py judgments.json baseline.json candidate.json --k 5
Runs: {"model": "name", "results": [{"id": "q1", "ranking": ["doc.md"],
"abstained": false}]}. Judgments: [{"id": "q1", "relevant": {"doc.md": 2}}].
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib


def rerank(candidates: list[str], scores: dict[str, float]) -> list[str]:
    """A reranker must score every candidate exactly once, without changing the pool."""
    if len(set(candidates)) != len(candidates) or set(candidates) != set(scores):
        raise ValueError("reranker scores must cover the unique candidate pool exactly")
    if any(isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s)
           for s in scores.values()):
        raise ValueError("reranker score must be finite")
    # Stable ties preserve the original retrieval order.
    return sorted(candidates, key=lambda doc: -scores[doc])


def evaluate(judgments: list[dict], run: dict, k: int = 5) -> dict:
    if isinstance(k, bool) or k < 1:
        raise ValueError("k must be positive")
    if not isinstance(run.get("model"), str) or not run["model"]:
        raise ValueError("run must identify its model/configuration")
    truth, results = {}, {}
    for case in judgments:
        key, relevant = case.get("id"), case.get("relevant")
        if not isinstance(key, str) or not key or key in truth or not isinstance(relevant, dict):
            raise ValueError("judgments need unique ids and a relevance mapping")
        if any(not isinstance(doc, str) or not doc or isinstance(grade, bool)
               or not isinstance(grade, int) or grade < 1 or grade > 3
               for doc, grade in relevant.items()):
            raise ValueError("relevance grades must be integers from 1 to 3")
        truth[key] = relevant
    for result in run.get("results", []):
        key, ranking = result.get("id"), result.get("ranking")
        if not isinstance(key, str) or key in results or not isinstance(ranking, list):
            raise ValueError("results need unique ids and rankings")
        if any(not isinstance(doc, str) or not doc for doc in ranking) or len(set(ranking)) != len(ranking):
            raise ValueError("ranking contains invalid or duplicate documents")
        if not isinstance(result.get("abstained"), bool):
            raise ValueError("every result must state whether it abstained")
        results[key] = result
    if not truth or set(truth) != set(results):
        raise ValueError("a run must cover every judged query exactly; no dropped failures")

    rows = []
    for key, relevant in truth.items():
        result = results[key]
        ranking = result["ranking"][:k]
        recall = sum(doc in relevant for doc in ranking) / len(relevant) if relevant else None
        reciprocal = next((1 / (i + 1) for i, doc in enumerate(ranking) if doc in relevant), 0.0)
        gains = [(2 ** relevant.get(doc, 0) - 1) / math.log2(i + 2) for i, doc in enumerate(ranking)]
        ideal = sum((2 ** grade - 1) / math.log2(i + 2)
                    for i, grade in enumerate(sorted(relevant.values(), reverse=True)[:k]))
        rows.append({"id": key, "recall": recall, "mrr": reciprocal if relevant else None,
                     "ndcg": sum(gains) / ideal if ideal else None,
                     "false_answer": not result["abstained"] if not relevant else None,
                     "false_refusal": result["abstained"] if relevant else None})

    def mean(field):
        values = [row[field] for row in rows if row[field] is not None]
        return sum(values) / len(values) if values else None

    return {"model": run["model"], "k": k, "queries": len(rows),
            "answerable": sum(bool(v) for v in truth.values()),
            "unanswerable": sum(not v for v in truth.values()),
            **{field: mean(field) for field in ("recall", "mrr", "ndcg", "false_answer", "false_refusal")},
            "per_query": rows}


def compare(judgments: list[dict], baseline: dict, candidate: dict, k: int = 5) -> dict:
    before, after = evaluate(judgments, baseline, k), evaluate(judgments, candidate, k)
    fields = ("recall", "mrr", "ndcg", "false_answer", "false_refusal")
    delta = {field: after[field] - before[field] if before[field] is not None else None for field in fields}
    return {"baseline": before, "candidate": after, "delta_candidate_minus_baseline": delta}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("judgments", type=pathlib.Path)
    parser.add_argument("baseline", type=pathlib.Path)
    parser.add_argument("candidate", type=pathlib.Path)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    try:
        values = [json.loads(path.read_text(encoding="utf-8"))
                  for path in (args.judgments, args.baseline, args.candidate)]
        report = compare(*values, k=args.k)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
