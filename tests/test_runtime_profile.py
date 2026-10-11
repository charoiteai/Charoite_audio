import pytest
from scripts import runtime_profile as rp


def clock():
    values = iter(range(100))
    return lambda: float(next(values))


def test_first_nonempty_chunk_and_first_trial_are_separated():
    result = rp.profile(lambda _: iter(["", "ответ"]), ["prompt"], 3, clock=clock())
    assert result["completed_trials"] == 3
    assert result["subsequent_successful_trials"] == 2
    assert result["trials"][0]["first_observed"]
    assert result["ttft_median_s"] == 1
    assert result["elapsed_p95_s"] == 2
    assert "response" not in result["trials"][0]


def test_empty_and_failed_trials_are_retained_not_silently_dropped():
    counter = iter([0, 1, 2])
    def call(_):
        value = next(counter)
        if value == 1:
            raise RuntimeError("secret-token")
        return iter([""] if value == 0 else ["ok"])
    result = rp.profile(call, ["prompt"], 3, clock=clock())
    assert [row["status"] for row in result["trials"]] == ["empty", "error", "ok"]
    assert result["failed_or_empty_trials"] == 2
    assert result["trials"][1]["error"] == "RuntimeError"
    assert "secret-token" not in str(result)


def test_live_meeting_stops_before_next_request():
    allowed = iter([True, False])
    calls = []
    result = rp.profile(lambda prompt: calls.append(prompt) or iter(["ok"]), ["one", "two"], 3,
                        may_run=lambda: next(allowed), clock=clock())
    assert calls == ["one"]
    assert result["interrupted_by_live_meeting"]
    assert result["expected_trials"] == 6
    assert result["completed_trials"] == 1


def test_invalid_repeats_and_no_successful_subsequent_trials():
    with pytest.raises(ValueError):
        rp.profile(lambda _: iter([]), ["one"], 1)
    result = rp.profile(lambda _: iter([]), ["one"], 2, clock=clock())
    assert result["ttft_p95_s"] is None
    assert result["failed_or_empty_trials"] == 2
