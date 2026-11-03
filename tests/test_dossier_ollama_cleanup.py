"""Cleanup verdicts from synthetic process receipts, without OS signals or models.

An unobserved orphan must prevent success even after every known child stopped.
These unit cases do not prove installed-server cleanup or model behavior.
"""
import signal

import pytest

from dossier_ollama_control import _stop_owned_processes


def _world(*, complete_capture, orphan):
    owned = {11: {"pid": 11, "pgid": 11, "started": "synthetic-start"},
             12: {"pid": 12, "pgid": 11, "started": "synthetic-child"}}
    live = {pid: {**identity, "state": "S"} for pid, identity in owned.items()}
    # The server vanished before the last observation; a runner was never seen.
    if orphan:
        live.pop(11)
        live[13] = {"pid": 13, "pgid": 11, "started": "unobserved-child", "state": "S"}
    signals, waits = [], []

    def signal_process(pid, signum):
        if pid in live:
            signals.append((pid, signum))
            live.pop(pid)

    class Server:
        def wait(self, *, timeout):
            waits.append(timeout)

    return (lambda: _stop_owned_processes(Server(), owned, lambda: complete_capture,
                                         signal_process, read_process=live.get), live, signals, waits)


def test_an_unobserved_orphan_prevents_success_after_known_children_stop():
    cleanup, live, signals, waits = _world(complete_capture=False, orphan=True)
    with pytest.raises(RuntimeError, match="unobserved descendants"):
        cleanup()
    assert set(live) == {13}
    assert signals == [(12, signal.SIGTERM)] and waits == [10]


@pytest.mark.parametrize("capture", [False, None])
def test_a_stale_empty_inventory_cannot_prove_that_no_unknown_runner_exists(capture):
    cleanup, live, signals, waits = _world(complete_capture=capture, orphan=False)
    with pytest.raises(RuntimeError, match="unobserved descendants"):
        cleanup()
    assert not live and len(signals) == 2 and waits == [10]


def test_complete_final_capture_and_stopped_identities_allow_success():
    cleanup, live, signals, waits = _world(complete_capture=True, orphan=False)
    assert cleanup() is None
    assert not live and signals == [(12, signal.SIGTERM), (11, signal.SIGTERM)] and waits == [10]
