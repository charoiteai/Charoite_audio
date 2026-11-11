"""Model-free: Stop gives STT/transcript producers a bounded, visible cutoff."""

import sys
import threading
import pytest
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "probes"))
import daemon  # noqa: E402
import threads  # noqa: E402


def test_finished_producers_do_not_block_export():
    done = threading.Event()

    def producer():
        time.sleep(0.01)
        done.set()

    worker = threads.spawn(producer, name="stt-loop", role="meeting")
    worker.join(1)
    assert daemon.wait_live_producers(0.5) == []


def test_a_producer_alive_past_cutoff_is_reported_not_hidden():
    release = threading.Event()
    worker = threads.spawn(release.wait, name="thread-loop", role="meeting")
    try:
        assert daemon.wait_live_producers(0.05) == ["thread-loop"]
    finally:
        release.set()
        worker.join(1)


class _FactoryHub:
    def stop(self):
        raise RuntimeError("synthetic hub failure")


class _FakeGrounded:
    producer_cutoff_failure = None


def test_hub_stop_failure_still_cuts_producers_and_marks_incomplete():
    release = threading.Event()
    worker = threads.spawn(release.wait, name="stt-loop", role="meeting")
    grounded = _FakeGrounded()
    try:
        late = daemon.cut_stop_producers(_FactoryHub(), grounded, timeout=0.05)
        assert late == ["stt-loop"]
        assert grounded.producer_cutoff_failure.startswith("hub_stop_failed")
        assert "stt-loop" in grounded.producer_cutoff_failure
    finally:
        release.set()
        worker.join(1)


def test_legacy_stop_keeps_propagating_hub_failure():
    with pytest.raises(RuntimeError, match="synthetic hub failure"):
        daemon.cut_stop_producers(_FactoryHub(), None, timeout=0.05)


def test_emit_returns_exact_bool_and_sets_stop_on_broken_pipe(monkeypatch):
    import io

    buf = io.StringIO()
    monkeypatch.setattr(daemon.sys, "stdout", buf)
    assert daemon.emit({"type": "status", "text": "ок"}) is True
    assert '"text": "ок"' in buf.getvalue()

    class BrokenOut:
        def write(self, _s):
            raise BrokenPipeError("closed")

        def flush(self):
            pass

    stop = threading.Event()
    monkeypatch.setattr(daemon, "_stop_event", stop)
    monkeypatch.setattr(daemon.sys, "stdout", BrokenOut())
    assert daemon.emit({"type": "status"}) is False
    assert stop.is_set()

    monkeypatch.setattr(daemon, "_stop_event", None)
    assert daemon.emit({"type": "status"}) is False

