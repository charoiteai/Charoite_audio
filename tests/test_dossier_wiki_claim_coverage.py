"""Visible link text cannot hide new assertions from independent evidence checks.

Transport answers are explicit doubles. These tests verify coverage and routing,
not the semantic quality of a real model.
"""
import json

import pytest

import dossier_incremental as inc
from test_dossier_model_contract import A, B, BODY, _claim, _old, _verdict

FACT = "A rain cover was fitted."
PRIOR = BODY.replace("Keep purple labels.", "Keep purple labels. [[SensorGuide]]")


def _check(monkeypatch, body, claims):
    calls = []

    def complete(prompt, config, **options):
        calls.append(("aggregate", json.JSONDecoder().raw_decode(prompt)[0]))
        return json.dumps(_verdict(claims))

    def audit(theme, loaded, claim, config, *, quote_context):
        calls.append(("audit", claim, quote_context))

    monkeypatch.setattr(inc, "local_complete", complete)
    monkeypatch.setattr(inc, "audit_claim", audit)
    inc.validate_candidate("Glasshouse", _old(PRIOR), [B], {B: {"text": FACT}}, {}, body, [])
    return calls


@pytest.mark.parametrize("hidden", [
    "[[SensorGuide|sensor is verified]]",
    f"[[{B}|sensor is verified]]",
    "[[All sensors verified]]",
    "[[SensorGuide#sensor is verified]]",
    f"[[{B}|{B}|sensor is verified]]",
])
def test_unclaimed_link_assertion_is_refused_before_any_independent_audit(monkeypatch, hidden):
    body = PRIOR.replace("## Как пришли", f"- {FACT} [[{B}]] {hidden}\n## Как пришли")
    audits = []
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict()))
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: audits.append(args))
    with pytest.raises(inc.Refused, match="uncovered new significant text"):
        inc.validate_candidate("Glasshouse", _old(PRIOR), [B], {B: {"text": FACT}}, {}, body, [])
    assert audits == []


@pytest.mark.parametrize("citation", [f"[[{B}]]", f"[[Meetings/{B}]]", f"[[{B}|{B}]]"])
def test_plain_known_provenance_identifier_needs_a_fact_but_no_extra_claim(monkeypatch, citation):
    body = PRIOR.replace("## Как пришли", f"- {FACT} {citation}\n## Как пришли")
    calls = _check(monkeypatch, body, [_claim()])
    assert [call[0] for call in calls] == ["aggregate", "audit"]
    assert calls[-1][1]["text"] == FACT and calls[-1][2] == FACT
    assert f"[[{A}]]" in body and "Sensor is unverified." in body


def test_explicitly_claimed_new_visible_alias_reaches_independent_audit(monkeypatch):
    alias = "[[SensorGuide|A rain cover was fitted.]]"
    body = PRIOR.replace("## Как пришли", f"- {alias} [[{B}]]\n## Как пришли")
    calls = _check(monkeypatch, body, [_claim(alias)])
    assert [call[0] for call in calls] == ["aggregate", "audit"]
    assert calls[-1][1]["text"] == alias and calls[-1][2] == FACT


def test_unchanged_prior_alias_is_preserved_without_revalidating_old_evidence(monkeypatch):
    prior_alias = "[[SensorGuide|existing label]]"
    global_prior = PRIOR.replace("[[SensorGuide]]", prior_alias)
    body = global_prior.replace("## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
    audits = []
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict()))
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: audits.append(args[2]))
    inc.validate_candidate("Glasshouse", _old(global_prior), [B], {B: {"text": FACT}}, {}, body, [])
    assert audits == [_claim()] and prior_alias in body


def test_identifier_only_alias_cannot_fake_new_source_contribution(monkeypatch):
    citation = f"[[{B}|{B}]]"
    body = PRIOR.replace("## Как пришли", citation + "\n## Как пришли")
    audits = []
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict([_claim(citation)])))
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: audits.append(args))
    with pytest.raises(inc.Refused, match="does not represent new significant text"):
        inc.validate_candidate("Glasshouse", _old(PRIOR), [B], {B: {"text": FACT}}, {}, body, [])
    assert audits == []
