#!/usr/bin/env python3
"""Validate frozen demo questions and optionally export actual lexical retrieval.

python scripts/memory_case_suite.py
CHAROITE_ROOT=/path/to/data python scripts/memory_case_suite.py --collect
The export is compatible with retrieval_compare.py; no LLM synthesis or network.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from charoite_paths import code_root

REPO = code_root(__file__)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def within(root: pathlib.Path, relative: str) -> pathlib.Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("invalid relative path")
    rel = pathlib.PurePosixPath(relative)
    if rel.is_absolute() or any(part.startswith(".") for part in rel.parts):
        raise ValueError("unsafe path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path leaves root")
    return path


def validate(cases: list[dict], root: pathlib.Path) -> None:
    if not isinstance(cases, list) or not cases:
        raise ValueError("nonempty case list required")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("case must be an object")
        key = case.get("id")
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("unique nonempty ids required")
        seen.add(key)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError("query required")
        if not case.get("language") or not case.get("category"):
            raise ValueError("language and category required")
        graph = within(root, case.get("graph"))
        if not graph.is_dir():
            raise ValueError("graph directory missing")
        relevant, evidence = case.get("relevant"), case.get("evidence")
        if not isinstance(relevant, dict) or not isinstance(evidence, dict) or set(relevant) != set(evidence):
            raise ValueError("every relevant source needs literal evidence")
        for source, grade in relevant.items():
            if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (1, 2, 3):
                raise ValueError("grade must be 1, 2 or 3")
            quote = evidence[source]
            if not isinstance(quote, str) or not normalize(quote):
                raise ValueError("nonempty evidence required")
            document = within(graph, source)
            if document.suffix != ".md" or normalize(quote) not in normalize(document.read_text(encoding="utf-8")):
                raise ValueError(f"evidence absent: {key}: {source}")


def collect(cases: list[dict], root: pathlib.Path, search) -> dict:
    rows = []
    errors = 0
    for case in cases:
        try:
            result = search(within(root, case["graph"]), case["query"])
            rows.append({"id": case["id"], **result})
        except Exception as exc:  # noqa: BLE001 — export all failed queries, never silently drop them
            errors += 1
            rows.append({"id": case["id"], "ranking": [], "abstained": True,
                         "status": "error", "error": type(exc).__name__})
    return {"judgments": [{"id": case["id"], "relevant": case["relevant"]} for case in cases],
            "run": {"model": "charoite-lexical/no-synthesis", "results": rows},
            "failed_queries": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=pathlib.Path, default=REPO / "config/memory_quality_cases.json")
    parser.add_argument("--root", type=pathlib.Path, default=REPO)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        validate(cases, args.root)
        if args.collect:
            import brain
            import graphs
            import llm
            from charoite_paths import harden_umask, resolve_root
            harden_umask()
            resolve_root(__file__)
            indexes = {}
            def search(graph, query):
                if graph not in indexes:
                    index = graphs.open_search(graph, llm.embedder({}))
                    index.refresh(force=True)
                    indexes[graph] = index
                result = indexes[graph].search(query, limit=5, semantic=False)
                return {"ranking": list(result.sources), "abstained": brain.absence_only(result),
                        "status": result.status.value, "skipped": list(result.skipped), "unread": result.unread}
            output = collect(cases, args.root, search)
        else:
            output = {"validated": len(cases), "languages": sorted({c["language"] for c in cases}),
                      "categories": sorted({c["category"] for c in cases})}
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int(output.get("failed_queries", 0) > 0)


if __name__ == "__main__":
    raise SystemExit(main())
