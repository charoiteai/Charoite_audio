"""Conservation/coverage contracts with explicit accepting judge doubles.

No renderer or model is used. New opaque markup must fail before the judge;
unchanged old literal markup may accompany an evidenced plain addition.
"""
import json

import pytest

import dossier_incremental as inc
from test_dossier_model_contract import A, B, BODY, _claim, _old, _verdict


OPEN = f"- Vent A is open. [[{A}]]"
WARNING = "- ⚠ Sensor is unverified."
FACT = "A rain cover was fitted."
ADDITION = f"- {FACT} [[{B}]]"


def _addition(prior):
    return prior.replace("## Как пришли", ADDITION + "\n## Как пришли")


def _check(monkeypatch, prior, candidate, *, changes=None, claims=None, source=FACT):
    calls = []

    def complete(*args, **kwargs):
        calls.append("aggregate")
        return json.dumps(_verdict(claims))

    monkeypatch.setattr(inc, "local_complete", complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: calls.append("audit"))
    inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": source}}, {},
                           candidate, [] if changes is None else changes)
    return calls


@pytest.mark.parametrize("old_line", [OPEN, WARNING])
@pytest.mark.parametrize("wrapper", ["<!--{}-->", "~~{}~~", "<span hidden>{}</span>", "`{}`"])
def test_an_accepting_aggregate_cannot_hide_a_prior_line(monkeypatch, old_line, wrapper):
    candidate = _addition(BODY).replace(old_line, wrapper.format(old_line))
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("hidden prior line reached a judge"))
    with pytest.raises(inc.Refused, match="prior claim|opaque markup"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": FACT}}, {}, candidate, [])


@pytest.mark.parametrize("opening,closing", [("<!--", "-->"), ("```", "```"), ("~~~", "~~~")])
def test_new_multiline_delimiters_cannot_recontextualize_a_prior_line(monkeypatch, opening, closing):
    candidate = _addition(BODY).replace(OPEN, opening + "\n" + OPEN + "\n" + closing)
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("new delimiters reached a judge"))
    with pytest.raises(inc.Refused):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": FACT}}, {}, candidate, [])


@pytest.mark.parametrize("suffix", ["<!--", " ~~", " <span hidden>", " `"])
def test_a_new_suffix_cannot_change_the_old_line_markup_context(monkeypatch, suffix):
    with pytest.raises(inc.Refused, match="prior claim|opaque markup"):
        _check(monkeypatch, BODY, _addition(BODY).replace(OPEN, OPEN + suffix))


@pytest.mark.parametrize("ending,suffix", [("<", "!--"), ("`", "`"), ("~", "~"), ("--", ">")])
def test_delimiters_cannot_be_completed_across_a_retained_prefix_boundary(monkeypatch, ending, suffix):
    literal = "- Literal marker " + ending
    prior = BODY.replace(OPEN, OPEN + "\n" + literal)
    with pytest.raises(inc.Refused, match="prior claim|opaque markup"):
        _check(monkeypatch, prior, _addition(prior).replace(literal, literal + suffix))


def test_old_line_prefix_allows_a_separately_evidenced_plain_suffix(monkeypatch):
    candidate = BODY.replace(OPEN, OPEN + f"; {FACT} [[{B}]]")
    assert _check(monkeypatch, BODY, candidate) == ["aggregate", "audit"]


@pytest.mark.parametrize("literal", [
    "- Use `Vent label` literally.", "<!-- Literal note. -->", "~~An obsolete label.~~",
    "<span>Literal label</span>", "```markdown\n## Источники\n```",
    "~~~\n##Правки автора\n~~~", "<!--\nLiteral note.\n-->", "> ## Правки автора",
])
@pytest.mark.parametrize("position", ["before", "after"])
def test_exact_old_literal_markup_allows_plain_additions_in_the_same_section(monkeypatch, literal, position):
    prior = BODY.replace(OPEN, literal + "\n" + OPEN)
    candidate = (prior.replace(literal, ADDITION + "\n" + literal, 1)
                 if position == "before" else _addition(prior))
    assert _check(monkeypatch, prior, candidate) == ["aggregate", "audit"]
    assert literal in candidate


@pytest.mark.parametrize("opening,closing", [("<!--", "-->"), ("```", "```"), ("~~~", "~~~")])
def test_plain_text_cannot_be_inserted_inside_an_old_opaque_span(monkeypatch, opening, closing):
    literal = opening + "\nLiteral note.\n\n" + closing
    prior = BODY.replace(OPEN, literal + "\n" + OPEN)
    candidate = prior.replace("Literal note.", "Literal note.\n" + ADDITION)
    with pytest.raises(inc.Refused, match="opaque markup context"):
        _check(monkeypatch, prior, candidate)


def test_unchanged_marker_counts_cannot_move_a_comment_boundary_past_an_old_fact(monkeypatch):
    literal = "<!--\nLiteral note.\n-->"
    prior = BODY.replace(OPEN, literal + "\n" + OPEN)
    candidate = _addition(prior).replace("-->\n" + OPEN, OPEN + "\n-->")
    assert candidate.count("<!--") == prior.count("<!--") == 1
    assert candidate.count("-->") == prior.count("-->") == 1
    with pytest.raises(inc.Refused, match="prior claim|opaque markup context"):
        _check(monkeypatch, prior, candidate)


def test_punctuation_only_delimiters_cannot_be_removed(monkeypatch):
    literal = "<!--\nLiteral note.\n-->"
    prior = BODY.replace(OPEN, literal + "\n" + OPEN)
    candidate = _addition(prior).replace("<!--\n", "").replace("-->\n", "")
    with pytest.raises(inc.Refused, match="prior claim"):
        _check(monkeypatch, prior, candidate)


@pytest.mark.parametrize("change", ["missing-occurrence", "reordered", "prefix"])
def test_prior_occurrences_keep_their_count_order_and_line_start(monkeypatch, change):
    other = "- Existing drain warning."
    prior = BODY.replace(WARNING, WARNING + "\n" + other + "\n" + WARNING)
    candidate = _addition(prior)
    if change == "missing-occurrence":
        candidate = candidate.replace(WARNING + "\n", "", 1)
    elif change == "reordered":
        candidate = candidate.replace(WARNING + "\n" + other, other + "\n" + WARNING, 1)
    else:
        candidate = candidate.replace(WARNING, "Already resolved: " + WARNING, 1)
    with pytest.raises(inc.Refused, match="prior claim"):
        _check(monkeypatch, prior, candidate)


def test_repeated_prior_lines_are_preserved_as_separate_occurrences(monkeypatch):
    prior = BODY.replace(WARNING, WARNING + "\n" + WARNING)
    assert _check(monkeypatch, prior, _addition(prior)) == ["aggregate", "audit"]


def test_an_extra_old_occurrence_still_needs_a_new_claim(monkeypatch):
    candidate = _addition(BODY).replace(OPEN, OPEN + "\n" + OPEN)
    with pytest.raises(inc.Refused, match="uncovered new significant text"):
        _check(monkeypatch, BODY, candidate)


@pytest.mark.parametrize("declared", [True, False])
def test_current_to_history_retention_still_requires_the_correction_ledger(monkeypatch, declared):
    quote = "Vent A is closed now."
    replacement = f"- {quote} [[{B}]]"
    candidate = BODY.replace(OPEN, replacement).replace("## Как пришли\n—", "## Как пришли\n" + OPEN)
    changes = [{"before": OPEN, "after": replacement, "source": B, "quote": quote}] if declared else []
    if declared:
        assert _check(monkeypatch, BODY, candidate, changes=changes,
                      claims=[_claim(quote, quote=quote)], source=quote) == ["aggregate", "audit"]
    else:
        with pytest.raises(inc.Refused, match="prior claim"):
            _check(monkeypatch, BODY, candidate, source=quote)


def test_new_markup_before_the_first_section_cannot_change_all_old_context(monkeypatch):
    with pytest.raises(inc.Refused, match="opaque markup"):
        _check(monkeypatch, BODY, "<!--\n" + _addition(BODY))


@pytest.mark.parametrize("duplicate", [False, True])
def test_a_correction_cannot_target_multiple_current_line_occurrences(monkeypatch, duplicate):
    other = OPEN if duplicate else f"- Valve B is open. [[{A}]]"
    prior = BODY.replace(OPEN, OPEN + "\n" + other)
    quote = "Vent A is closed now."
    replacement = f"- {quote} [[{B}]]"
    candidate = prior.replace(OPEN + "\n" + other, replacement).replace(
        "## Как пришли\n—", "## Как пришли\n" + OPEN + "\n" + other)
    changes = [{"before": OPEN if duplicate else "open", "after": replacement, "source": B, "quote": quote}]
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("ambiguous correction reached a judge"))
    with pytest.raises(inc.Refused, match="ambiguous correction"):
        inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": quote}}, {}, candidate, changes)


@pytest.mark.parametrize("before", [OPEN, OPEN[2:], "Vent A is open."])
@pytest.mark.parametrize("history_copy", [False, True])
def test_unique_current_corrections_accept_full_or_sentence_references(monkeypatch, before, history_copy):
    prior = BODY.replace("## Как пришли\n—", "## Как пришли\n" + OPEN) if history_copy else BODY
    quote = "Vent A is closed now."
    replacement = f"- {quote} [[{B}]]"
    candidate = prior.replace(OPEN, replacement, 1).replace(
        "## Как пришли\n", "## Как пришли\n" + OPEN + "\n", 1)
    changes = [{"before": before, "after": replacement, "source": B, "quote": quote}]
    assert _check(monkeypatch, prior, candidate, changes=changes,
                  claims=[_claim(quote, quote=quote)], source=quote) == ["aggregate", "audit"]
    assert candidate.count(OPEN) == (2 if history_copy else 1)
