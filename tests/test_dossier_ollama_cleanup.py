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
            if signum == signal.SIGSTOP:
                live[pid]["state"] = "T"
            else:
                live.pop(pid)

    class Server:
        def wait(self, *, timeout):
            waits.append(timeout)

    return (lambda: _stop_owned_processes(Server(), owned, lambda: complete_capture,
                                         signal_process, lambda: not live,
                                         read_process=live.get), live, signals, waits)


def test_an_unobserved_orphan_prevents_success_after_known_children_stop():
    cleanup, live, signals, waits = _world(complete_capture=False, orphan=True)
    with pytest.raises(RuntimeError, match="unobserved descendants"):
        cleanup()
    assert set(live) == {13}
    assert signals == [(12, signal.SIGKILL)] and waits == [10]


@pytest.mark.parametrize("capture", [False, None])
def test_a_stale_empty_inventory_cannot_prove_that_no_unknown_runner_exists(capture):
    cleanup, live, signals, waits = _world(complete_capture=capture, orphan=False)
    with pytest.raises(RuntimeError, match="unobserved descendants"):
        cleanup()
    assert not live and len(signals) == 2 and waits == [10]


def test_complete_final_capture_and_stopped_identities_allow_success():
    cleanup, live, signals, waits = _world(complete_capture=True, orphan=False)
    assert cleanup() is None
    assert not live and signals == [(11, signal.SIGSTOP), (12, signal.SIGSTOP),
                                   (12, signal.SIGKILL), (11, signal.SIGKILL)] and waits == [10]


def test_a_child_created_during_the_first_capture_is_stopped_before_the_final_inventory():
    root = {"pid": 11, "pgid": 11, "started": "root", "state": "S"}
    child = {"pid": 12, "pgid": 11, "started": "child", "state": "S"}
    owned, live, events = {11: root.copy()}, {11: root.copy()}, []
    captures = 0

    def capture():
        nonlocal captures
        captures += 1
        if captures == 2:
            # This child was created just before the root received SIGSTOP,
            # after the previous inventory. It must not escape cleanup.
            owned[12], live[12] = child.copy(), child.copy()
        events.append(("capture", {pid: value["state"] for pid, value in live.items()}))
        return True

    def signal_process(pid, signum):
        if pid in live:
            events.append((signum, pid))
            if signum == signal.SIGSTOP:
                live[pid]["state"] = "T"
            else:
                live.pop(pid)

    class Server:
        def wait(self, *, timeout):
            assert timeout == 10

    _stop_owned_processes(Server(), owned, capture, signal_process,
                         lambda: not live, read_process=live.get)
    assert captures == 3 and not live
    assert events.index((signal.SIGSTOP, 12)) < events.index((signal.SIGKILL, 11))
    assert ("capture", {11: "T", 12: "T"}) in events
