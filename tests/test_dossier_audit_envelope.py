"""Audit-only JSON presentation envelopes through the real publication path."""
from __future__ import annotations

import json

import pytest

import dossier_incremental as inc
from charoite_graph import dossier
from charoite_schema import CHAROITE
from test_dossier_incremental_mutation_behaviors import (
    B, FACT, LOCAL, _answer, _source, _world,
    isolated as isolated, night,
)


def _fence(raw):
    return "```json\n" + raw + "\n```"


def _role(system):
    if system == inc.SUPPORT_AUDIT_SYSTEM:
        return "support"
    if system == inc.TOPIC_AUDIT_SYSTEM:
        return "topic"
    if "source relevance" in system:
        return "source"
    if "extend factual" in system:
        return "generator"
    return "aggregate"


def _scenario(isolated, monkeypatch, target, transform):
    graph, path = _world(isolated)
    assert night.run_all([graph], LOCAL, full=False, dry=False).attempts == 0
    _source(graph)
    protected = [path, path.parent / dossier.INDEX_JSON, path.parent / dossier.INDEX_MD]
    before = {item: item.read_bytes() for item in protected}
    calls = []

    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        role = _role(system)
        calls.append(role)
        answer = _answer(payload, system)
        return transform(answer) if role == target else answer

    monkeypatch.setattr(inc, "local_complete", complete)
    result = night.run_all([graph], LOCAL, full=False, dry=False)
    return result, path, protected, before, calls


@pytest.mark.parametrize("target", ["source", "generator", "aggregate", "support", "topic"])
def test_only_independent_audits_accept_one_complete_json_fence(isolated, monkeypatch, target):
    result, path, protected, before, calls = _scenario(isolated, monkeypatch, target, _fence)
    assert target in calls and result.attempts == 1
    if target in {"support", "topic"}:
        assert result.graphs[0].built == 1 and FACT in path.read_text()
        assert B in dossier.composition(path.read_text(), schema=CHAROITE)
        assert next(path.parent.glob(".backup/*/*.md")).read_bytes() == before[path]
    else:
        assert result.graphs[0].built == 0
        assert {item: item.read_bytes() for item in protected} == before
        assert not (path.parent / ".backup").exists()


def _invalid(raw, kind):
    if kind == "prefix":
        return "Explanation before JSON.\n" + _fence(raw)
    if kind == "suffix":
        return _fence(raw) + "\nExplanation after JSON."
    if kind == "two-fences":
        return _fence(raw) + "\n" + _fence(raw)
    if kind == "nested-fence":
        return _fence(_fence(raw))
    if kind == "two-objects":
        return _fence(raw + "\n" + raw)
    if kind == "truncated-fence":
        return "```json\n" + raw
    if kind == "wrong-opener":
        return "```JSON\n" + raw + "\n```"
    if kind == "wrong-closer":
        return "```json\n" + raw + "\n~~~"
    if kind == "empty":
        return _fence("")
    if kind == "duplicate-key":
        return _fence(raw[:-1] + ',"reason":"Duplicate reason"}')
    answer = json.loads(raw)
    if kind == "extra-field":
        answer["extra"] = True
    elif kind == "coerced-flag":
        answer["supported" if "supported" in answer else "relevant"] = 1
    else:
        answer["verdict"] = "reject"
    return _fence(json.dumps(answer))


@pytest.mark.parametrize("target", ["support", "topic"])
@pytest.mark.parametrize("kind", ["prefix", "suffix", "two-fences", "nested-fence", "two-objects",
                                 "truncated-fence", "wrong-opener", "wrong-closer", "empty", "duplicate-key",
                                 "extra-field", "coerced-flag", "reject"])
def test_bad_or_rejecting_audit_envelope_cannot_advance_publication(isolated, monkeypatch, target, kind):
    result, path, protected, before, calls = _scenario(
        isolated, monkeypatch, target, lambda raw: _invalid(raw, kind))
    assert target in calls and result.attempts == 1 and result.graphs[0].built == 0
    assert {item: item.read_bytes() for item in protected} == before
    assert not (path.parent / ".backup").exists()


def test_backticks_inside_a_json_string_are_data_in_one_outer_envelope():
    verdict = {"verdict": "accept", "supported": True, "reason": "A ``` marker is quoted data."}
    raw = " \n" + _fence(json.dumps(verdict)) + "\n "
    assert inc._json(raw, set(verdict), fenced=True) == verdict
