"""Preservation at rendering and bounded generation-correction boundaries."""
import json

import pytest

from charoite_graph import dossier
import dossier_incremental as inc

A, B = "2099-01-01_100000", "2099-01-02_100000"
BODY = (f"## Сейчас\n- Vent A is open. [[{A}]]\n## Как пришли\n—\n"
        "## Решено\n- Keep purple labels.\n## Открыто\n- ⚠ Sensor is unverified.\n"
        "## Кто в теме\n—")
ADDED = BODY.replace("## Как пришли", f"- A rain cover was fitted. [[{B}]]\n## Как пришли")


@pytest.fixture(autouse=True)
def aggregate_component_boundary(monkeypatch):
    """This module isolates aggregate/coverage checks; narrow audits have their
    own whole-pipeline contract module with an independent transport callback.
    """
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: None)


def _old(body=BODY):
    text = dossier.render("Glasshouse", body, [], {}, "fixture", "2099-01-01")
    return dossier.DossierLoad("ok", text, text.encode())


def _claim(text="A rain cover was fitted.", quote="A rain cover was fitted.", **fields):
    return {"text": text, "source": B, "quote": quote, "relevant": True,
            "supported": True, "reason": "The new source explicitly supports this topic fact."} | fields


def _verdict(claims=None, **fields):
    return {"verdict": "accept", "conserved": True, "supported": True,
            "corrections_valid": True, "reason": "Supported addition.",
            "claims": [_claim()] if claims is None else claims} | fields


@pytest.mark.parametrize("literal", ["inline ## Источники text", "> ## Правки автора",
                                    "```markdown\n## Источники\n```", "~~~\n##Правки автора\n~~~"])
def test_render_retains_literal_protected_heading(literal):
    body = BODY.replace("- Vent A is open.", literal + "\n- Vent A is open.")
    text = dossier.render("Glasshouse", body, [], {}, "fixture", "2099-01-01")
    parsed = dossier.DossierLoad("ok", text).parsed
    assert parsed.complete and parsed.body == body
    assert dossier.strip_protected(body) == body


@pytest.mark.parametrize("heading", ["## Источники", "###Правки автора", "\t# Источники"])
def test_render_still_strips_real_protected_sections(heading):
    assert dossier.strip_protected(BODY + "\n" + heading + "\ninjected") == BODY


@pytest.mark.parametrize("correct_second", [True, False])
def test_generation_feedback_preserves_guards_and_is_bounded(monkeypatch, correct_second):
    text = dossier.render("Glasshouse", BODY, [], {}, "fixture", "2099-01-01")
    loaded = dossier.DossierLoad("ok", text, text.encode())
    files = {B: {"text": "A rain cover was fitted."}}
    calls, judged = [], []

    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append(payload)
        body = ADDED if len(calls) == 2 and correct_second else ADDED.replace("- ⚠ Sensor is unverified.\n", "")
        return json.dumps({"body": body, "changes": []})

    monkeypatch.setattr(inc, "local_complete", complete)
    monkeypatch.setattr(inc, "semantic_check", lambda *args: judged.append(1))
    if correct_second:
        assert inc.append("Glasshouse", loaded, [B], files, {}) == ADDED
        assert judged == [1]
    else:
        with pytest.raises(inc.Refused, match="prior claim"):
            inc.append("Glasshouse", loaded, [B], files, {})
        assert judged == []
    assert len(calls) == 2
    assert "correction_feedback" not in calls[0]
    assert "- ⚠ Sensor is unverified." in calls[1]["correction_feedback"]["required_verbatim_lines"]
    assert loaded.raw == text.encode()


@pytest.mark.parametrize("raw", ["not JSON", "null", "[]", "{}",
    '{"verdict":"relevant","reason":"supported","quote":"absent from source"}',
    '{"verdict":"irrelevant","reason":"","quote":""}',
    '{"verdict":"uncertain","reason":"ambiguous","quote":"rain cover"}',
    '{"verdict":"irrelevant","reason":"off topic","quote":"","extra":true}',
    '{"verdict":"irrelevant","verdict":"relevant","reason":"ambiguous","quote":"rain cover"}',
])
def test_incomplete_source_verdict_is_operational_refusal(monkeypatch, raw):
    text = dossier.render("Glasshouse", BODY, [], {}, "fixture", "2099-01-01")
    old = dossier.DossierLoad("ok", text, text.encode())
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: raw)
    with pytest.raises(inc.Refused):
        inc.source_relevance("Glasshouse", old, B, {"text": "A rain cover was fitted."}, {})
    assert old.raw == text.encode()


@pytest.mark.parametrize("changed", [{"verdict": "reject"}, {"verdict": "uncertain"},
    {"conserved": False}, {"conserved": 1}, {"supported": False}, {"supported": "true"},
    {"corrections_valid": False}, {"reason": ""}, {"reason": None}, {"extra": True}])
def test_nonaccepting_candidate_verdict_cannot_authorize_update(monkeypatch, changed):
    text = dossier.render("Glasshouse", BODY, [], {}, "fixture", "2099-01-01")
    old = dossier.DossierLoad("ok", text, text.encode())
    verdict = _verdict(**changed)
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(verdict))
    with pytest.raises(inc.Refused):
        inc.validate_candidate("Glasshouse", old, [B], {B: {"text": "A rain cover was fitted."}}, {}, ADDED, [])
    assert old.raw == text.encode()


@pytest.mark.parametrize("claims", [None, {}, "claims", [None], [{}],
    [_claim(relevant=False)], [_claim(relevant=1)], [_claim(supported=False)],
    [_claim(supported="true")], [_claim(source=A)], [_claim(quote="not in source")],
    [_claim(quote="")], [_claim(reason="")], [_claim(text="")], [_claim(text="not in candidate")],
    [_claim(extra=True)], [_claim(), _claim()], []])
def test_aggregate_accept_does_not_override_invalid_or_missing_claims(monkeypatch, claims):
    # All aggregate booleans remain true: only the per-assertion contract refuses.
    verdict = _verdict() | {"claims": claims}
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(verdict))
    with pytest.raises(inc.Refused, match="claim|uncovered"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": "A rain cover was fitted."}}, {}, ADDED, [])


def test_legacy_aggregate_only_verdict_is_not_a_default_accept(monkeypatch):
    verdict = _verdict()
    del verdict["claims"]
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(verdict))
    with pytest.raises(inc.Refused, match="JSON fields"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": "A rain cover was fitted."}}, {}, ADDED, [])


@pytest.mark.parametrize("layout", ["separate-bullets", "compound-line", "appended-to-old-line", "factual-subheading"])
def test_omitted_new_clause_or_subheading_is_uncovered(monkeypatch, layout):
    unrelated = "A level sensor was fitted to the WaterTank."
    if layout == "separate-bullets":
        candidate = ADDED.replace("## Как пришли", f"- {unrelated} [[{B}]]\n## Как пришли")
    elif layout == "compound-line":
        candidate = ADDED.replace("A rain cover was fitted.", f"A rain cover was fitted.; {unrelated}")
    elif layout == "appended-to-old-line":
        candidate = ADDED.replace(f"- Vent A is open. [[{A}]]", f"- Vent A is open. [[{A}]]; {unrelated} [[{B}]]")
    else:
        candidate = ADDED.replace("## Как пришли", f"### {unrelated}\n## Как пришли")
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict()))
    files = {B: {"text": "A rain cover was fitted. " + unrelated}}
    with pytest.raises(inc.Refused, match="uncovered"):
        inc.validate_candidate("Glasshouse", _old(), [B], files, {}, candidate, [])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_two_atomic_spans_on_one_line_have_complete_evidence(monkeypatch, newline):
    first, second = "A rain cover was fitted.", "The glasshouse drain is clear."
    candidate = ADDED.replace(first, first + "; " + second).replace("\n", newline)
    files = {B: {"text": first + " " + second}}
    verdict = _verdict([_claim(first), _claim(second, quote=second)])
    calls = []
    def complete(prompt, config, **kwargs):
        calls.append((prompt, kwargs))
        return json.dumps(verdict)
    monkeypatch.setattr(inc, "local_complete", complete)
    inc.validate_candidate("Glasshouse", _old(), [B], files, {}, candidate, [])
    assert len(calls) == 1 and calls[0][1]["num_predict"] == 1200
    assert "splitting a compound bullet" in calls[0][0]


def test_new_prose_appended_to_an_old_line_requires_its_own_claim(monkeypatch):
    fact = "A rain cover was fitted."
    candidate = BODY.replace(f"- Vent A is open. [[{A}]]", f"- Vent A is open. [[{A}]]; {fact} [[{B}]]")
    judgments = []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        judgments.append((payload, kwargs["system"]))
        return json.dumps(_verdict())
    monkeypatch.setattr(inc, "local_complete", complete)
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": fact}}, {}, candidate, [])
    assert len(judgments) == 1
    payload, system = judgments[0]
    assert system == "You are an independent conservative dossier judge."
    assert payload["candidate"] == candidate and payload["existing_dossier"] == BODY
    assert payload["new_sources"] == {B: fact}
    assert payload["new_statements"] == [f"- Vent A is open. [[{A}]]; {fact} [[{B}]]"]


@pytest.mark.parametrize("explicit", [True, False])
def test_relationship_requires_its_own_explicit_source_support(monkeypatch, explicit):
    fact = "The WaterTank pump supplies irrigation water to the Glasshouse."
    source = fact if explicit else "A connection between WaterTank and Glasshouse is suspected but unverified."
    candidate = BODY.replace("## Как пришли", f"- {fact} [[{B}]]\n## Как пришли")
    claim = _claim(fact, quote=source, supported=explicit)
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict([claim])))
    if explicit:
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, candidate, [])
    else:
        # The exact quote alone is insufficient; the model's per-claim verdict
        # must also attest that it supports the asserted relationship.
        with pytest.raises(inc.Refused, match="relevant and supported"):
            inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, candidate, [])


def test_correction_retains_old_history_without_unsourced_explanation(monkeypatch):
    before = f"Vent A is open. [[{A}]]"
    after = f"Vent A is now closed. [[{B}]]"
    quote = "Vent A is now closed; this supersedes the earlier open state."
    candidate = BODY.replace(before, after).replace("## Как пришли\n—", f"## Как пришли\n- {before}")
    changes = [{"before": before, "after": after, "source": B, "quote": quote}]
    verdict = _verdict([_claim("Vent A is now closed.", quote=quote)])
    judgments = []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        judgments.append((payload, kwargs["system"]))
        return json.dumps(verdict)
    monkeypatch.setattr(inc, "local_complete", complete)
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": quote}}, {}, candidate, changes)
    assert len(judgments) == 1
    payload, system = judgments[0]
    assert system == "You are an independent conservative dossier judge."
    assert payload["candidate"] == candidate and payload["changes"] == changes
    assert payload["new_sources"] == {B: quote} and payload["existing_dossier"] == BODY
    assert payload["candidate"].split("## Как пришли\n", 1)[1].startswith(f"- {before}\n")
    assert payload["new_statements"] == [f"- {after}"]


def test_preserved_old_offtopic_claim_does_not_need_a_new_source(monkeypatch):
    old_body = BODY.replace("## Как пришли\n—", "## Как пришли\n- A distant orchard has blue gates.")
    candidate = old_body.replace("## Как пришли", f"- A rain cover was fitted. [[{B}]]\n## Как пришли")
    judgments = []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        judgments.append((payload, kwargs["system"]))
        return json.dumps(_verdict())
    monkeypatch.setattr(inc, "local_complete", complete)
    inc.validate_candidate("Glasshouse", _old(old_body), [B], {B: {"text": "A rain cover was fitted."}}, {}, candidate, [])
    assert len(judgments) == 1
    payload, system = judgments[0]
    assert system == "You are an independent conservative dossier judge."
    assert payload["existing_dossier"] == old_body and payload["candidate"] == candidate
    assert payload["new_sources"] == {B: "A rain cover was fitted."}
    assert payload["new_statements"] == [f"- A rain cover was fitted. [[{B}]]"]


@pytest.mark.parametrize("quote", ["A rain cover was fitted", "rain cover was fitted."])
def test_claim_quote_is_an_exact_nonempty_source_substring(monkeypatch, quote):
    judgments, audited = [], []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        judgments.append((payload, kwargs["system"]))
        return json.dumps(_verdict([_claim(quote=quote)]))
    monkeypatch.setattr(inc, "local_complete", complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: audited.append((args[2], kwargs["quote_context"])))
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": "A rain cover was fitted."}}, {}, ADDED, [])
    assert len(judgments) == 1 and len(audited) == 1
    payload, system = judgments[0]
    assert system == "You are an independent conservative dossier judge."
    assert payload["candidate"] == ADDED and payload["new_sources"] == {B: "A rain cover was fitted."}
    assert audited[0][0]["quote"] == quote and audited[0][1] == "A rain cover was fitted."


def test_new_citation_without_a_claim_is_not_a_source_contribution(monkeypatch):
    candidate = BODY + f"\n[[{B}]] — !"
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict([])))
    with pytest.raises(inc.Refused, match="contribution"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": "A rain cover was fitted."}}, {}, candidate, [])


def test_claim_for_only_an_old_fact_does_not_cover_new_text(monkeypatch):
    verdict = _verdict([_claim("Vent A is open.", quote="Vent A is open.")])
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(verdict))
    with pytest.raises(inc.Refused, match="new significant"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": "Vent A is open. A rain cover was fitted."}}, {}, ADDED, [])


@pytest.mark.parametrize("field,bad", [("before", "unknown prior claim"), ("after", "not in candidate"),
                                      ("source", "not-a-new-source"), ("quote", "not in source")])
def test_correction_evidence_is_checked_before_semantic_judge(monkeypatch, field, bad):
    before = f"Vent A is open. [[{A}]]"
    after = f"Vent A is now closed. [[{B}]]"
    corrected = BODY.replace(before, after).replace("## Как пришли\n—", f"## Как пришли\n- {before}")
    quote = "Vent A is now closed; this supersedes the earlier open state."
    files = {B: {"text": quote}}
    text = dossier.render("Glasshouse", BODY, [], {}, "fixture", "2099-01-01")
    old = dossier.DossierLoad("ok", text, text.encode())
    good = {"before": before, "after": after, "source": B, "quote": quote}
    inc.deterministic_check(old, corrected, [B], files, [good])
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("unsupported evidence reached the judge"))
    with pytest.raises(inc.Refused):
        inc.validate_candidate("Glasshouse", old, [B], files, {}, corrected, [good | {field: bad}])
