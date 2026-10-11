import json
import pytest
from scripts import memory_case_suite as mc


def cases():
    return json.loads((mc.REPO / "config/memory_quality_cases.json").read_text(encoding="utf-8"))


def test_frozen_cases_have_real_source_quotes_three_languages_and_negative_cases():
    suite = cases()
    mc.validate(suite, mc.REPO)
    assert len(suite) == 18
    assert {case["language"] for case in suite} == {"ru", "en", "zh"}
    assert {case["category"] for case in suite} == {"decision", "number", "action", "completed", "history", "negative"}
    assert sum(not case["relevant"] for case in suite) == 3


def test_wrong_quote_and_duplicate_id_are_rejected():
    suite = cases()
    suite[0]["evidence"] = {next(iter(suite[0]["relevant"])): "вымышленное решение"}
    with pytest.raises(ValueError, match="evidence absent"):
        mc.validate(suite, mc.REPO)
    with pytest.raises(ValueError, match="unique"):
        mc.validate([cases()[0], cases()[0]], mc.REPO)


def test_graph_and_source_must_stay_within_root():
    suite = cases()
    suite[0]["graph"] = "../outside"
    with pytest.raises(ValueError):
        mc.validate(suite, mc.REPO)


def test_failed_queries_stay_in_export_with_failure_count():
    suite = cases()[:2]
    def broken(*_):
        raise RuntimeError("do not expose source text")
    export = mc.collect(suite, mc.REPO, broken)
    assert export["failed_queries"] == 2
    assert len(export["run"]["results"]) == len(export["judgments"]) == 2
    assert all(row["status"] == "error" for row in export["run"]["results"])
    assert "do not expose" not in str(export)


def test_actual_lexical_collector_preserves_verdict_and_sources(tmp_path):
    import graphs
    import llm
    import brain
    graph = mc.REPO / "demo/graph"
    index = graphs.open_search(graph, llm.embedder({}))
    index.refresh(force=True)
    def search(_, query):
        result = index.search(query, limit=5, semantic=False)
        return {"ranking": list(result.sources), "abstained": brain.absence_only(result), "status": result.status.value}
    export = mc.collect(cases()[:6], mc.REPO, search)
    assert export["failed_queries"] == 0
    assert len(export["run"]["results"]) == 6
    assert all(row["status"] == "unverified" for row in export["run"]["results"])
    assert any("Встречи/2026-07-17_1400.md" in row["ranking"] for row in export["run"]["results"])
