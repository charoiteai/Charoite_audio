#!/usr/bin/env python3
"""Read-only audit of exported decision evidence; does not infer current decisions.

python scripts/decision_evidence_audit.py --graph /path/to/graph records.json
Records: [{"id":"d1","kind":"decision","date":"2026-10-01",
"source":"Meetings/One.md","quote":"Approved B","supersedes":null}].
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import unicodedata


def normalized(text: str) -> str:
    # Keep negation, numbers, case and punctuation. No fuzzy semantic substitution.
    return " ".join(unicodedata.normalize("NFC", text).split())


def read_source(graph: pathlib.Path, source: str) -> bytes:
    rel = pathlib.PurePosixPath(source)
    if not source or rel.is_absolute() or "\\" in source or rel.suffix != ".md" \
            or any(part.startswith(".") for part in rel.parts):
        raise ValueError("unsafe_source")
    root = graph.resolve()
    path = (root / source).resolve()
    if not path.is_relative_to(root):
        raise ValueError("outside_graph")
    return path.read_bytes()


def audit(graph: pathlib.Path, records: list[dict]) -> list[dict]:
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("records must be a list of objects")
    indexed = {}
    for record in records:
        key = record.get("id")
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError("records need unique nonempty ids")
        indexed[key] = record
    report, dates = [], {}
    for key, record in indexed.items():
        findings = []
        kind = record.get("kind")
        if kind not in ("decision", "action", "proposal"):
            findings.append("invalid_kind")
        try:
            value = record.get("date")
            date = dt.date.fromisoformat(value)
            if date.isoformat() != value:
                raise ValueError("date must be YYYY-MM-DD")
            dates[key] = date
        except (TypeError, ValueError):
            findings.append("invalid_date")
        source, quote = record.get("source"), record.get("quote")
        digest, quote_present = None, False
        if not isinstance(source, str) or not isinstance(quote, str) or not normalized(quote):
            findings.append("missing_evidence")
        else:
            try:
                raw = read_source(graph, source)
                digest = hashlib.sha256(raw).hexdigest()
                text = raw.decode("utf-8")
                quote_present = normalized(quote) in normalized(text)
                if not quote_present:
                    findings.append("quote_not_found")
                if record.get("source_sha256") is not None and record["source_sha256"] != digest:
                    findings.append("source_changed")
            except UnicodeDecodeError:
                findings.append("source_not_utf8")
            except OSError:
                findings.append("source_unreadable")
            except ValueError as exc:
                findings.append(str(exc))
        report.append({"id": key, "quote_present": quote_present, "source_sha256": digest,
                       "semantic_support": "not_checked", "findings": findings})

    by_id = {row["id"]: row for row in report}
    for key, record in indexed.items():
        previous = record.get("supersedes")
        if previous is None:
            continue
        findings = by_id[key]["findings"]
        if not isinstance(previous, str) or previous not in indexed:
            findings.append("superseded_record_missing")
            continue
        if record.get("kind") != "decision" or indexed[previous].get("kind") != "decision":
            findings.append("supersession_requires_decisions")
        if key in dates and previous in dates and dates[key] < dates[previous]:
            findings.append("supersession_reverses_time")
        seen = {key}
        cursor = previous
        while isinstance(cursor, str) and cursor in indexed:
            if cursor in seen:
                findings.append("supersession_cycle")
                break
            seen.add(cursor)
            cursor = indexed[cursor].get("supersedes")
    # A matching quote is not proof that the quote entails the extracted claim.
    # No record is labeled "current", "fulfilled" or "verified" by this audit.
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=pathlib.Path, required=True)
    parser.add_argument("records", type=pathlib.Path)
    args = parser.parse_args()
    try:
        records = json.loads(args.records.read_text(encoding="utf-8"))
        report = audit(args.graph, records)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any(row["findings"] for row in report))


if __name__ == "__main__":
    raise SystemExit(main())
