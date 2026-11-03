"""Old wording in a new section or extra occurrence needs new evidence.

Aggregate/audit judgments are explicit doubles. No model-quality claim is made.
"""
import json

import pytest

import dossier_incremental as inc
from test_dossier_model_contract import A, B, BODY, _claim, _old, _verdict

FACT = "A rain cover was fitted."
OPEN = f"- Vent A is open. [[{A}]]"


def _transport(monkeypatch, claims):
    audited = []
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: json.dumps(_verdict(claims)))
    monkeypatch.setattr(inc, "audit_claim", lambda *args, **kwargs: audited.append(args[2]))
    return audited


@pytest.mark.parametrize("transfer", ["history-to-current", "history-to-current-alias",
                                     "warning-to-decided", "duplicate-current"])
def test_aggregate_acceptance_cannot_hide_recontextualized_or_duplicated_old_line(monkeypatch, transfer):
    prior = BODY
    if transfer.startswith("history-to-current"):
        copied = "- [[SensorGuide|Vent A is open.]]" if transfer.endswith("alias") else OPEN
        prior = prior.replace(OPEN, f"- Vent A is closed. [[{A}]]").replace("## Как пришли\n—", "## Как пришли\n" + copied)
        destination = "## Как пришли"
    elif transfer == "warning-to-decided":
        destination, copied = "## Открыто", "- ⚠ Sensor is unverified."
    else:
        destination, copied = "## Как пришли", OPEN
    candidate = prior.replace("## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
    candidate = candidate.replace(destination, copied + "\n" + destination)
    audited = _transport(monkeypatch, [_claim()])
    with pytest.raises(inc.Refused, match="uncovered new significant text"):
        inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": FACT}}, {}, candidate, [])
    assert audited == []
    assert copied in prior and candidate.count(copied) == prior.count(copied) + 1


def test_retained_current_and_historical_occurrences_do_not_need_new_source_proof(monkeypatch):
    prior = BODY.replace("## Как пришли\n—", "## Как пришли\n" + OPEN)
    candidate = prior.replace("## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
    audited = _transport(monkeypatch, [_claim()])
    inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": FACT}}, {}, candidate, [])
    assert audited == [_claim()] and candidate.count(OPEN) == prior.count(OPEN) == 2


@pytest.mark.parametrize("declared", [True, False])
def test_only_declared_current_correction_can_move_retained_line_to_history(monkeypatch, declared):
    replacement = f"- Vent A is closed now. [[{B}]]"
    candidate = BODY.replace(OPEN, replacement).replace("## Как пришли\n—", "## Как пришли\n" + OPEN)
    quote = "Vent A is closed now."
    claim = _claim(quote, quote=quote)
    changes = [{"before": OPEN, "after": replacement, "source": B, "quote": quote}] if declared else []
    audited = _transport(monkeypatch, [claim])
    if declared:
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": quote}}, {}, candidate, changes)
        assert audited == [claim]
    else:
        with pytest.raises(inc.Refused, match="uncovered new significant text"):
            inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": quote}}, {}, candidate, changes)
        assert audited == []
    assert OPEN not in candidate.split("## Как пришли")[0] and candidate.count(OPEN) == 1
