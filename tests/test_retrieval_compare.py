import math
import pytest
from scripts import retrieval_compare as rc


def run(ranking, *, abstained=False):
    return {"model": "test", "results": [{"id": "q", "ranking": ranking, "abstained": abstained}]}


def test_rank_quality_improves_without_changing_candidate_recall():
    truth = [{"id": "q", "relevant": {"decision": 3}}]
    result = rc.compare(truth, run(["noise", "decision"]), run(["decision", "noise"]), k=2)
    assert result["baseline"]["recall"] == result["candidate"]["recall"] == 1
    assert result["delta_candidate_minus_baseline"]["mrr"] == .5
    assert result["candidate"]["ndcg"] == 1


def test_refusal_metrics_do_not_hide_unanswerable_or_false_refusal():
    negative = rc.evaluate([{"id": "q", "relevant": {}}], run(["noise"]))
    assert negative["false_answer"] == 1
    assert negative["recall"] is None
    positive = rc.evaluate([{"id": "q", "relevant": {"decision": 1}}], run(["decision"], abstained=True))
    assert positive["false_refusal"] == 1
    assert positive["recall"] == 1  # retrieval quality is separate from answering


@pytest.mark.parametrize("result", [
    {"model": "bad", "results": []},
    {"model": "bad", "results": [{"id": "other", "ranking": [], "abstained": True}]},
    {"model": "bad", "results": [{"id": "q", "ranking": ["a", "a"], "abstained": False}]},
    {"model": "bad", "results": [{"id": "q", "ranking": []}]},
])
def test_missing_failed_queries_duplicates_and_missing_verdict_are_rejected(result):
    with pytest.raises(ValueError):
        rc.evaluate([{"id": "q", "relevant": {"a": 1}}], result)


def test_reranker_preserves_pool_and_stable_ties():
    assert rc.rerank(["a", "b", "c"], {"a": 1, "b": 2, "c": 2}) == ["b", "c", "a"]
    for scores in ({"a": 1}, {"a": 1, "b": math.nan}, {"a": True, "b": 2}):
        with pytest.raises(ValueError):
            rc.rerank(["a", "b"], scores)


def test_graded_relevance_and_cutoff():
    result = rc.evaluate([{"id": "q", "relevant": {"a": 3, "b": 1}}], run(["b", "a"]), k=1)
    assert result["recall"] == .5
    assert result["ndcg"] == pytest.approx(1 / 7)


def test_operational_error_is_never_counted_as_a_successful_negative_refusal():
    failed = run([], abstained=True)
    failed["results"][0]["status"] = "error"
    with pytest.raises(ValueError, match="operational errors"):
        rc.evaluate([{"id": "q", "relevant": {}}], failed)
