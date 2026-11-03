"""Aggregate, exact evidence and independent quote-only audit contracts.

All judgments here are explicit transport doubles, never real model results.
Writer scenarios reuse the invented incremental fixture and production locks,
backup, membership, CAS and index paths.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import nightly_dossier as night
import dossier_incremental as inc
from charoite_graph import dossier, frontmatter
from charoite_schema import CHAROITE
from test_dossier_incremental import world as world, _new, LOCAL
from test_dossier_model_contract import _old, _claim, _verdict, BODY, ADDED, A, B

C = "2099-01-03_100000"
FACT = "A rain cover was fitted."
AUDIT_SYSTEM = "You audit one proposed factual addition."
ACCEPT = {"verdict": "accept", "supported": True, "relevant": True, "reason": "Synthetic independent support and relevance."}


def _transport(monkeypatch, aggregate, narrow=ACCEPT):
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append((system, payload, kwargs, prompt))
        return json.dumps(narrow if system == AUDIT_SYSTEM else aggregate)
    monkeypatch.setattr(inc, "local_complete", complete)
    return calls


@pytest.mark.parametrize("change", [{"verdict": "reject"}, {"verdict": "uncertain"}, {"verdict": True},
    {"supported": False}, {"supported": 1}, {"supported": "true"}, {"supported": None},
    {"relevant": False}, {"relevant": 1}, {"relevant": "true"}, {"relevant": None},
    {"reason": ""}, {"reason": "   "}, {"reason": None}, {"reason": 1}, {"extra": True}])
def test_independent_narrow_verdict_requires_exact_schema_and_literal_true(monkeypatch, change):
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(ACCEPT | change))
    with pytest.raises(inc.Refused):
        inc.audit_claim("Glasshouse", _old(), _claim(), {})


@pytest.mark.parametrize("raw", ["not JSON", "null", "[]", "{}", json.dumps(_verdict()),
    '{"verdict":"accept","supported":true,"relevant":true,"reason":"one","reason":"two"}'])
def test_malformed_or_aggregate_shaped_narrow_response_is_retryable(monkeypatch, raw):
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: raw)
    with pytest.raises(inc.Refused):
        inc.audit_claim("Glasshouse", _old(), _claim(), {})


@pytest.mark.parametrize("proof", ["one-clause", "both-clauses"])
def test_aggregate_true_and_full_text_coverage_cannot_override_independent_rejection(monkeypatch, proof):
    foreign = "A level sensor was fitted to the WaterTank."
    complete_claim = FACT + "; " + foreign
    candidate = ADDED.replace(FACT, complete_claim)
    quote = FACT if proof == "one-clause" else complete_claim
    verdict = _verdict([_claim(complete_claim, quote=quote)])
    narrow = ACCEPT | {"verdict": "reject", "supported": proof == "both-clauses", "relevant": False,
                       "reason": "Synthetic refusal of the independent foreign assertion."}
    calls = _transport(monkeypatch, verdict, narrow)
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": quote}}, {}, candidate, [])
    assert len(calls) == 2
    assert calls[1][0] == AUDIT_SYSTEM
    assert calls[1][1]["candidate_text"] == complete_claim
    assert calls[1][1]["source_quotation"] == quote


def test_valid_addition_runs_independent_audit_with_complete_source_and_prior_dossier(monkeypatch):
    source = "Unrelated preceding source line.\n[[Ядра/Glasshouse]] " + FACT + "\nUnrelated following source line.\n"
    old = _old()
    calls = _transport(monkeypatch, _verdict())
    inc.validate_candidate("Glasshouse", old, [B], {B: {"text": source}}, {}, ADDED, [])
    assert len(calls) == 2
    _, payload, options, prompt = calls[1]
    assert payload == {"topic": "Glasshouse", "prior_dossier": old.parsed.body,
                       "candidate_text": FACT, "source_quotation": FACT, "source_context": source}
    assert options["num_predict"] == 350
    assert prompt.split("\n", 1)[1] == inc.CLAIM_AUDIT_INSTRUCTION
    assert "Unrelated preceding" in payload["source_context"] and "Unrelated following" in payload["source_context"]
    assert "author_notes" not in payload and "new_sources" not in payload


@pytest.mark.parametrize("quote,expected", [
    ("Vent A is closed.", "It is not confirmed that Vent A is closed.\n"),
    ("Vent A is closed.", "Vent A is closed. This statement is only a tentative hypothesis.\n"),
])
def test_narrow_audit_sees_negation_or_qualifiers_outside_the_selected_quote(monkeypatch, quote, expected):
    candidate = ADDED.replace(FACT, quote)
    files = {B: {"text": "Other topic before.\n" + expected + "Other topic after.\n"}}
    calls = _transport(monkeypatch, _verdict([_claim(quote, quote=quote)]), ACCEPT | {
        "verdict": "uncertain", "supported": False, "reason": "Synthetic quotation qualification refusal."})
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", _old(), [B], files, {}, candidate, [])
    assert calls[1][1]["source_quotation"] == quote
    assert calls[1][1]["source_context"] == files[B]["text"]


def test_multiline_evidence_anchor_keeps_the_complete_source_context(monkeypatch):
    quote = "A rain cover\nwas fitted."
    source = "Unrelated first line.\n[[Ядра/Glasshouse]] " + quote + " Done today.\nUnrelated last line.\n"
    calls = _transport(monkeypatch, _verdict([_claim(quote=quote)]))
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, ADDED, [])
    assert calls[1][1]["source_quotation"] == quote
    assert calls[1][1]["source_context"] == source


def test_repeated_literal_quote_is_refused_before_any_narrow_audit(monkeypatch):
    source = f"[[Ядра/Glasshouse]] {FACT}\n[[Ядра/WaterTank]] {FACT}\n"
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, ADDED, [])
    assert len(calls) == 1


def test_two_new_sources_can_prove_the_same_new_claim_with_separate_quotes(monkeypatch):
    second_quote = "A rain cover was fitted today."
    candidate = ADDED.replace(f"[[{B}]]", f"[[{B}]] [[{C}]]")
    claims = [_claim(), _claim(source=C, quote=second_quote)]
    calls = _transport(monkeypatch, _verdict(claims))
    inc.validate_candidate("Glasshouse", _old(), [B, C],
                           {B: {"text": FACT}, C: {"text": second_quote}}, {}, candidate, [])
    assert [call[1]["source_quotation"] for call in calls[1:]] == [FACT, second_quote]
    assert all(call[0] == AUDIT_SYSTEM for call in calls[1:])


def test_repeating_a_text_source_pair_is_refused_even_with_a_different_quote(monkeypatch):
    second_quote = "A rain cover was fitted today."
    calls = _transport(monkeypatch, _verdict([_claim(), _claim(quote=second_quote)]))
    with pytest.raises(inc.Refused, match="duplicate candidate claim"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": FACT + " " + second_quote}}, {}, ADDED, [])
    assert len(calls) == 1


def test_a_citation_to_another_accepted_source_does_not_supply_its_contribution(monkeypatch):
    candidate = ADDED.replace(f"[[{B}]]", f"[[{B}]] [[{C}]]")
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused, match="contribution"):
        inc.validate_candidate("Glasshouse", _old(), [B, C],
                               {B: {"text": FACT}, C: {"text": FACT}}, {}, candidate, [])
    assert len(calls) == 1


def test_no_new_sources_and_no_new_text_needs_no_claim_audit(monkeypatch):
    calls = _transport(monkeypatch, _verdict([]))
    inc.validate_candidate("Glasshouse", _old(), [], {}, {}, BODY, [])
    assert len(calls) == 1


@pytest.fixture
def writer_world(world, tmp_path, monkeypatch):
    _, graph, path = world
    monkeypatch.setattr(night, "_root", lambda: tmp_path)
    monkeypatch.setattr(night.graphs, "load_config", lambda: {})
    monkeypatch.setattr(night, "generator_label", lambda config: "")
    return graph, path


@pytest.mark.parametrize("failure", ["no-contribution", "narrow-uncertain", "narrow-timeout", "narrow-malformed"])
def test_writer_keeps_body_membership_index_and_backups_when_final_evidence_fails(writer_world, monkeypatch, failure):
    graph, path = writer_world
    _new(graph)
    before = path.read_bytes()
    dossier.rebuild_index(graph, schema=CHAROITE)
    index_before = dossier.load_index(path.parent)
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        calls.append(system)
        if system == "You judge source relevance conservatively.":
            return json.dumps({"verdict": "relevant", "reason": "Synthetic supported source.", "quote": FACT})
        if system == "You extend factual dossiers conservatively.":
            body = dossier.load_dossier(path).parsed.body
            body = body + f"\n[[{B}]]" if failure == "no-contribution" else body.replace(
                "## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
            return json.dumps({"body": body, "changes": []})
        if system == AUDIT_SYSTEM:
            if failure == "narrow-timeout":
                raise TimeoutError("Synthetic independent audit timeout.")
            if failure == "narrow-malformed":
                return "not JSON"
            return json.dumps(ACCEPT | {"verdict": "uncertain", "relevant": False})
        return json.dumps(_verdict([] if failure == "no-contribution" else [_claim()]))
    monkeypatch.setattr(inc, "local_complete", complete)
    result = night.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and result["отказы"] == 1
    assert path.read_bytes() == before
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
    assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
    assert dossier.load_index(path.parent) == index_before
    assert not (path.parent / ".backup").exists()
    assert (AUDIT_SYSTEM in calls) is (failure != "no-contribution")


@pytest.mark.parametrize("attribute", ["cloud_ready", "engine", "base", "_fell_back_local"])
def test_missing_supported_transport_attribute_is_an_explicit_refusal(monkeypatch, attribute):
    import llm
    class MissingAttribute:
        def __init__(self, config):
            self.cloud_ready, self.engine, self.base, self._fell_back_local = False, "ollama", "http://127.0.0.1:11434", False
            delattr(self, attribute)
        def stream(self, *args, **kwargs):
            pytest.fail("Unsupported adapter reached the transport.")
    monkeypatch.setattr(llm, "LLM", MissingAttribute)
    with pytest.raises(inc.Refused, match="missing transport attributes"):
        inc.local_complete("source", LOCAL, system="judge")


@pytest.mark.parametrize("cloud_ready,engine,base", [(True, "ollama", "http://127.0.0.1:11434"),
                                                    (False, "cloud", "http://127.0.0.1:11434"),
                                                    (False, "ollama", "https://example.invalid")])
def test_actual_adapter_transport_is_checked_again_after_local_factory(monkeypatch, cloud_ready, engine, base):
    import llm
    class WrongTransport:
        def __init__(self, config):
            self.cloud_ready, self.engine, self.base, self._fell_back_local = cloud_ready, engine, base, False
        def stream(self, *args, **kwargs):
            pytest.fail("Unexpected transport was used.")
    monkeypatch.setattr(llm, "LLM", WrongTransport)
    with pytest.raises(inc.Refused):
        inc.local_complete("source", LOCAL, system="judge")
