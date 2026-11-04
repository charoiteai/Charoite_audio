"""A declared supersession changes current state while retaining attributed history."""
import pytest

import dossier_incremental as inc
from test_dossier_model_contract import _old, BODY, A, B


BEFORE = f"Vent A is open. [[{A}]]"
AFTER = f"Vent A is now closed. [[{B}]]"
QUOTE = "Vent A is now closed; this supersedes the earlier open state."
CHANGE = {"before": BEFORE, "after": AFTER, "source": B, "quote": QUOTE}
FILES = {B: {"text": QUOTE}}
VALID = BODY.replace(BEFORE, AFTER).replace("## Как пришли\n—", f"## Как пришли\n- {BEFORE}")


@pytest.mark.parametrize("placement", ["both-current", "old-in-decisions", "replacement-in-history"])
def test_declared_correction_cannot_leave_an_obsolete_current_state_or_misplace_its_history(monkeypatch, placement):
    if placement == "both-current":
        body = BODY.replace("## Как пришли", f"- {AFTER}\n## Как пришли")
    elif placement == "old-in-decisions":
        body = VALID.replace(f"## Как пришли\n- {BEFORE}", "## Как пришли\n—").replace(
            "## Решено", f"## Решено\n- {BEFORE}")
    else:
        body = BODY.replace("## Как пришли\n—", f"## Как пришли\n- {AFTER}")
    calls = []
    monkeypatch.setattr(inc, "semantic_check", lambda *args: calls.append(args))
    with pytest.raises(inc.Refused, match="superseded current claim"):
        inc.validate_candidate("Glasshouse", _old(), [B], FILES, {}, body, [CHANGE])
    assert calls == []


@pytest.mark.parametrize("line_end", ["\n", "\r\n"])
def test_declared_correction_keeps_exact_old_history_and_replacement_current(monkeypatch, line_end):
    calls = []
    monkeypatch.setattr(inc, "semantic_check", lambda *args: calls.append(args))
    body = VALID.replace("\n", line_end)
    inc.validate_candidate("Glasshouse", _old(), [B], FILES, {}, body, [CHANGE])
    assert len(calls) == 1 and calls[0][5] == body
    assert calls[0][6] == [CHANGE]
