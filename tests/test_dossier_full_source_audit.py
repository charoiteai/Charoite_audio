"""Full-source context contracts; judgments are explicit no-model doubles.

Real semantic/coverage/evidence orchestration is used. Writer scenarios reuse
invented graph fixtures and production locks, backup, CAS and index paths.
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
from test_dossier_model_contract import _old, _claim, _verdict, ADDED, A, B

FACT = "A rain cover was fitted."
SUPPORT = inc.SUPPORT_AUDIT_SYSTEM
TOPIC = inc.TOPIC_AUDIT_SYSTEM
NEIGHBOR_CONTEXTS = [
    "The next statement is tentative and has not been verified.\n" + FACT + "\n",
    FACT + "\nThe preceding statement is tentative and has not been verified.\n",
    "The next line is a false draft and must not be treated as a fact.\n" + FACT + "\n",
    FACT + "\nCorrection: the previous line is false; no rain cover was fitted.\n",
]


def _transport(monkeypatch, claim, *, supported, relevant):
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append((system, payload))
        if system == SUPPORT:
            return json.dumps({"verdict": "accept" if supported else "reject", "supported": supported,
                               "reason": "Synthetic complete-source factual judgment."})
        if system == TOPIC:
            return json.dumps({"verdict": "accept" if relevant else "reject", "relevant": relevant,
                               "reason": "Synthetic independent subject judgment."})
        return json.dumps(_verdict([claim]))
    monkeypatch.setattr(inc, "local_complete", complete)
    return calls


@pytest.mark.parametrize("source", NEIGHBOR_CONTEXTS)
def test_neighboring_qualification_or_denial_reaches_independent_audit_and_refuses(monkeypatch, source):
    old = _old()
    calls = _transport(monkeypatch, _claim(), supported=False, relevant=True)
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", old, [B], {B: {"text": source}}, {}, ADDED, [])
    assert len(calls) == 2 and calls[1][0] == SUPPORT
    assert all(system != TOPIC for system, _ in calls)
    assert calls[0][1]["new_sources"] == {B: source}
    assert calls[1][1] == {"topic": "Glasshouse", "prior_dossier": old.parsed.body,
                           "candidate_text": FACT, "source_quotation": FACT, "source_context": source}


def test_a_supported_qualified_addition_keeps_qualification_and_complete_source(monkeypatch):
    qualified = "A rain cover may have been fitted to the Glasshouse; this is unverified."
    source = "Preliminary observation.\n" + qualified + "\nDo not treat it as confirmed installation.\n"
    candidate = ADDED.replace(FACT, qualified)
    calls = _transport(monkeypatch, _claim(qualified, quote=qualified), supported=True, relevant=True)
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, candidate, [])
    assert [system for system, _ in calls[1:]] == [SUPPORT, TOPIC]
    assert calls[1][1]["candidate_text"] == qualified
    assert calls[1][1]["source_quotation"] == qualified and calls[1][1]["source_context"] == source
    assert calls[2][1] == calls[1][1]


@pytest.mark.parametrize("foreign", [False, True])
def test_shared_source_context_does_not_replace_independent_subject_judgment(monkeypatch, foreign):
    other_fact = "A level sensor was fitted to the WaterTank."
    source = f"[[Ядра/Glasshouse]] {FACT}\n[[Ядра/WaterTank]] {other_fact}\n"
    fact = other_fact if foreign else FACT
    calls = _transport(monkeypatch, _claim(fact, quote=fact), supported=True, relevant=not foreign)
    candidate = ADDED.replace(FACT, fact)
    if foreign:
        with pytest.raises(inc.Refused, match="independent claim audit"):
            inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, candidate, [])
    else:
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, candidate, [])
    assert [system for system, _ in calls[1:]] == [SUPPORT, TOPIC]
    assert calls[1][1]["candidate_text"] == fact and calls[1][1]["source_quotation"] == fact
    assert calls[1][1]["source_context"] == source
    assert set(calls[1][1]) == {"topic", "prior_dossier", "candidate_text", "source_quotation", "source_context"}
    assert calls[2][1] == calls[1][1]


def test_whole_source_does_not_remove_exact_unique_anchor_requirement(monkeypatch):
    source = FACT + "\n" + FACT + "\nThe draft has conflicting observations.\n"
    calls = _transport(monkeypatch, _claim(), supported=True, relevant=True)
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, ADDED, [])
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["prior-event-only", "different-source-event"])
def test_factual_anchor_cannot_borrow_an_event_from_prior_or_another_source_statement(monkeypatch, case):
    if case == "prior-event-only":
        fact, anchor = "Vent A is open today.", "We discussed the schedule."
        source = anchor + "\n"
    else:
        fact, anchor = FACT, "A level sensor was fitted to the WaterTank."
        source = FACT + "\n" + anchor + "\n"
    old = _old()
    calls = _transport(monkeypatch, _claim(fact, quote=anchor), supported=False, relevant=True)
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", old, [B], {B: {"text": source}}, {}, ADDED.replace(FACT, fact), [])
    assert [system for system, _ in calls[1:]] == [SUPPORT]
    assert calls[1][1] == {"topic": "Glasshouse", "prior_dossier": old.parsed.body,
                           "candidate_text": fact, "source_quotation": anchor, "source_context": source}


@pytest.mark.parametrize("source", [NEIGHBOR_CONTEXTS[0], NEIGHBOR_CONTEXTS[-1]])
def test_refused_contextual_addition_never_advances_writer_state(world, tmp_path, monkeypatch, source):
    _, graph, path = world
    _new(graph, body=source)
    monkeypatch.setattr(night, "_root", lambda: tmp_path)
    monkeypatch.setattr(night.graphs, "load_config", lambda: {})
    monkeypatch.setattr(night, "generator_label", lambda config: "")
    before = path.read_bytes()
    dossier.rebuild_index(graph, schema=CHAROITE)
    index_before = dossier.load_index(path.parent)
    source_on_disk = (graph / f"Встречи/{B}.md").read_text()
    audited = []
    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        if system == "You judge source relevance conservatively.":
            return json.dumps({"verdict": "relevant", "quote": FACT, "reason": "Synthetic source-level relevance."})
        if system == "You extend factual dossiers conservatively.":
            body = dossier.load_dossier(path).parsed.body.replace("## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
            return json.dumps({"body": body, "changes": []})
        if system == SUPPORT:
            audited.append(payload)
            return json.dumps({"verdict": "reject", "supported": False,
                               "reason": "Synthetic full-source denial is decisive."})
        if system == TOPIC:
            pytest.fail("An unsupported candidate reached topic judgment.")
        return json.dumps(_verdict())
    monkeypatch.setattr(inc, "local_complete", complete)
    result = night.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and result["отказы"] == 1
    assert path.read_bytes() == before
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
    assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
    assert dossier.load_index(path.parent) == index_before
    assert not (path.parent / ".backup").exists()
    assert audited and all(payload["source_context"] == source_on_disk for payload in audited)
    assert all(payload["source_quotation"] == FACT for payload in audited)
