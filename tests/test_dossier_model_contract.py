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
    verdict = {"verdict": "accept", "conserved": True, "supported": True,
               "corrections_valid": True, "reason": "Supported addition."} | changed
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(verdict))
    with pytest.raises(inc.Refused):
        inc.validate_candidate("Glasshouse", old, [B], {B: {"text": "A rain cover was fitted."}}, {}, ADDED, [])
    assert old.raw == text.encode()


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
