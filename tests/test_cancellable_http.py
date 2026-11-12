"""Model-free tests for the owned HTTP worker identity probe (no model calls)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cancellable_http  # noqa: E402


class FakeProc:
    def __init__(self, polls):
        self.pid = 4242
        self._polls = list(polls)

    def poll(self):
        return self._polls.pop(0) if len(self._polls) > 1 else self._polls[0]


def response_with(proc):
    response = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    response._proc = proc
    return response


def test_worker_exit_race_is_not_cleanup_incomplete(monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(argv, 1, stdout=b"")

    monkeypatch.setattr(cancellable_http.subprocess, "run", fake_run)
    # poll() is None on the first probe (zombie not reaped yet), then the worker
    # is gone: the probe must report a normal exit instead of CleanupIncomplete.
    proc = FakeProc([None, None, 0])
    assert response_with(proc)._check_owned_command() is False


def test_live_worker_identity_is_confirmed(monkeypatch):
    expected = str(cancellable_http.charoite_paths.code_root(cancellable_http.__file__) / "src/cancellable_http.py")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=(expected + " --http-worker").encode())

    monkeypatch.setattr(cancellable_http.subprocess, "run", fake_run)
    assert response_with(FakeProc([None, None]))._check_owned_command() is True


def test_foreign_worker_is_unconfirmed(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"/usr/bin/other --http-worker")

    monkeypatch.setattr(cancellable_http.subprocess, "run", fake_run)
    with pytest.raises(cancellable_http.CleanupIncomplete):
        response_with(FakeProc([None, None]))._check_owned_command()


def test_defunct_worker_is_normal_exit(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"<defunct>\n")

    monkeypatch.setattr(cancellable_http.subprocess, "run", fake_run)
    assert response_with(FakeProc([None, None]))._check_owned_command() is False


def test_transient_python_form_is_normal_exit(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"(Python)\n")

    monkeypatch.setattr(cancellable_http.subprocess, "run", fake_run)
    assert response_with(FakeProc([None, None]))._check_owned_command() is False


def test_context_manager_message_limits_quarantine_close_and_worker(monkeypatch, capsys, _сеть_закрыта):
    import io
    import threading
    from types import SimpleNamespace

    # __enter__ returns self; close() on already closed response is no-op; quarantined raises
    r_ctx = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_ctx._closed = True
    r_ctx._quarantined = True
    r_ctx._proc = None
    with r_ctx as entered:
        assert entered is r_ctx

    r_q = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_q._closed = False
    r_q._quarantined = True
    with pytest.raises(cancellable_http.CleanupIncomplete):
        r_q.close()

    # _message exact _MAX_LINE_BYTES boundary (== ok, +1 raises ValueError)
    r_msg = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_msg.cancel_event = threading.Event()
    r_msg._buffer = bytearray()
    r_msg._selector = SimpleNamespace(select=lambda timeout: [True])
    r_msg._proc = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 7))
    exact_payload = b'{"x":"' + b"a" * (cancellable_http._MAX_LINE_BYTES - 9) + b'"}\n'
    assert len(exact_payload) == cancellable_http._MAX_LINE_BYTES
    chunks = [exact_payload, b"a" * (cancellable_http._MAX_LINE_BYTES + 1)]
    monkeypatch.setattr(cancellable_http.os, "read", lambda _fd, _n: chunks.pop(0))
    parsed = r_msg._message()
    assert len(parsed["x"]) == cancellable_http._MAX_LINE_BYTES - 9
    with pytest.raises(ValueError, match="exceeds limit"):
        r_msg._message()

    # _check_owned_command early poll!=None, OSError + poll!=None vs None, post-ps poll!=None
    assert response_with(FakeProc([0]))._check_owned_command() is False

    def raise_oserror(*_a, **_kw):
        raise OSError("ps failed")

    monkeypatch.setattr(cancellable_http.subprocess, "run", raise_oserror)
    assert response_with(FakeProc([None, 0]))._check_owned_command() is False
    with pytest.raises(cancellable_http.CleanupIncomplete):
        response_with(FakeProc([None, None]))._check_owned_command()

    monkeypatch.setattr(
        cancellable_http.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, stdout=b""),
    )
    assert response_with(FakeProc([None, 0]))._check_owned_command() is False

    # quarantine idempotency, OSError retry, single stderr on progress failure, call finish & call=None
    spawned = []
    monkeypatch.setattr(cancellable_http, "time", SimpleNamespace(sleep=lambda _s: None))
    import threads

    monkeypatch.setattr(threads, "spawn", lambda fn, **_kw: spawned.append(fn))

    waits = [subprocess.TimeoutExpired("w", 0.25), OSError("echld"), 0]
    closed_streams = []

    class StreamStub:
        def close(self):
            closed_streams.append(True)

    r_quar = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_quar._quarantined = False
    r_quar._closed = False
    r_quar._selector = SimpleNamespace(close=lambda: closed_streams.append("sel"))
    r_quar._proc = SimpleNamespace(
        stdin=None,
        stdout=StreamStub(),
        wait=lambda timeout=None: (_ for _ in ()).throw(w) if isinstance(w := waits.pop(0), BaseException) else w,
    )
    lease_events = []

    class BadProgressLease:
        def progress(self):
            lease_events.append("progress")
            raise RuntimeError("telemetry broken")

        def __exit__(self, *exc):
            lease_events.append(("exit", exc))

    call_outcomes = []
    call_stub = SimpleNamespace(finish=call_outcomes.append)
    r_quar.quarantine(BadProgressLease(), call_stub)
    r_quar.quarantine(BadProgressLease(), call_stub)
    assert len(spawned) == 1
    assert r_quar in cancellable_http._QUARANTINED
    spawned[0]()
    err_out = capsys.readouterr().err
    assert err_out.count("lease progress failed") == 1
    assert r_quar._closed is True
    assert r_quar not in cancellable_http._QUARANTINED
    assert ("exit", (None, None, None)) in lease_events
    assert call_outcomes == ["failed"]

    # quarantine with call=None and _proc=None in _dispose
    r_quar2 = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_quar2._quarantined = False
    r_quar2._closed = False
    r_quar2._selector = SimpleNamespace(close=lambda: None)
    r_quar2._proc = SimpleNamespace(stdin=None, stdout=None, wait=lambda timeout=None: 0)
    r_quar2.quarantine(SimpleNamespace(progress=lambda: None, __exit__=lambda *_e: None), None)
    r_quar2._proc_saved = r_quar2._proc
    spawned[-1]()
    assert r_quar2._closed is True

    # close() with _proc=None, kill escalation, and OSError with poll()==0 vs None
    r_noproc = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
    r_noproc._closed = False
    r_noproc._quarantined = False
    r_noproc._selector = SimpleNamespace(close=lambda: None)
    r_noproc._proc = None
    r_noproc.close()
    assert r_noproc._closed is True

    for probe_res, expect_kill in ((True, True), (False, False)):
        actions = []
        waits_close = [subprocess.TimeoutExpired("w", 0.75), 0]
        r_kill = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
        r_kill._closed = False
        r_kill._quarantined = False
        r_kill._selector = SimpleNamespace(close=lambda: None)
        r_kill._check_owned_command = lambda pr=probe_res: pr
        r_kill._proc = SimpleNamespace(
            stdin=None, stdout=None,
            poll=lambda: None,
            terminate=lambda: actions.append("term"),
            kill=lambda: actions.append("kill"),
            wait=lambda timeout=None: (_ for _ in ()).throw(w) if isinstance(w := waits_close.pop(0), BaseException) else w,
        )
        r_kill.close()
        assert ("kill" in actions) is expect_kill

    for poll_after, should_raise in ((0, False), (None, True)):
        polls = [None, poll_after]
        r_err = cancellable_http.ProcessResponse.__new__(cancellable_http.ProcessResponse)
        r_err._closed = False
        r_err._quarantined = False
        r_err._selector = SimpleNamespace(close=lambda: None)
        r_err._check_owned_command = lambda: False
        r_err._proc = SimpleNamespace(
            stdin=None, stdout=None,
            poll=lambda p=polls: p.pop(0) if len(p) > 1 else p[0],
            wait=lambda timeout=None: (_ for _ in ()).throw(OSError("wait err")),
        )
        if should_raise:
            with pytest.raises(cancellable_http.CleanupIncomplete):
                r_err.close()
        else:
            r_err.close()
            assert r_err._closed is True

    # _worker(): non-loopback error, 500 error preview truncation + list timeout, and 200 streaming
    def run_worker_spec(spec, post_fn=None):
        if post_fn is not None:
            _сеть_закрыта[("POST", spec["url"])] = post_fn
        monkeypatch.setattr(cancellable_http.sys, "stdin", SimpleNamespace(
            buffer=io.BytesIO((cancellable_http.json.dumps(spec) + "\n").encode("utf-8"))
        ))
        out = io.StringIO()
        monkeypatch.setattr(cancellable_http.sys, "stdout", out)
        cancellable_http._worker()
        return [cancellable_http.json.loads(line) for line in out.getvalue().splitlines()]

    assert run_worker_spec(
        {"url": "https://evil.example.com/v1", "payload": {}, "timeout": 1, "kwargs": {}},
    ) == [{"kind": "error"}]

    captured_post = {}

    class FakeResp500:
        status_code = 500
        raw = io.BytesIO(b"E" * 700)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def iter_lines(self, **_kw):
            raise AssertionError("must not iterate non-200")

    msgs_500 = run_worker_spec(
        {"url": "http://127.0.0.1:11434/api/chat", "payload": {"a": 1},
         "timeout": [1.5, 2.5], "kwargs": {"headers": {"X": "1"}, "proxies": {"http": "bad"}, "allow_redirects": True}},
        lambda url, **kw: (captured_post.update(url=url, **kw) or FakeResp500()),
    )
    assert captured_post["timeout"] == (1.5, 2.5)
    assert captured_post["stream"] is True
    assert captured_post["allow_redirects"] is False
    assert captured_post["proxies"] != {"http": "bad"}
    assert msgs_500 == [{"kind": "head", "status": 500, "text": "E" * 500}, {"kind": "end"}]


def test_cancellable_http_cli_requires_worker_flag():
    cli = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "src/cancellable_http.py")],
        capture_output=True, text=True,
    )
    assert cli.returncode == 2



@pytest.fixture
def synthetic_worker(tmp_path, monkeypatch):
    """Real owned child/pipe; no socket, model, configuration or meeting data."""
    root = tmp_path / "worker"
    script = root / "src" / "cancellable_http.py"
    script.parent.mkdir(parents=True)
    monkeypatch.setattr(cancellable_http.charoite_paths, "code_root", lambda _path: root)
    real_popen = subprocess.Popen
    children = []

    def record_child(argv, **kwargs):
        child = real_popen(argv, **kwargs)
        if len(argv) > 1 and argv[1] == str(script):
            children.append(child)
        return child

    monkeypatch.setattr(cancellable_http.subprocess, "Popen", record_child)

    def configure(body):
        # Safety bound survives a regression in the parent's cancellation path.
        script.write_text("import sys, os, signal, time, json, hashlib\n"
                          "signal.alarm(8)\n" + body)
        return children

    yield configure
    for child in children:
        if child.poll() is None:
            command = subprocess.run(
                ["/bin/ps", "-ww", "-p", str(child.pid), "-o", "command="],
                capture_output=True, text=True, timeout=1,
            ).stdout
            assert str(script) in command and "--http-worker" in command
            child.terminate()
            child.wait(timeout=2)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                stream.close()


def test_cancel_interrupts_backpressured_request(synthetic_worker):
    import threading
    import time

    children = synthetic_worker("time.sleep(7)\n")
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    start = time.monotonic()
    timer.start()
    try:
        with pytest.raises(cancellable_http.StreamCancelled):
            cancellable_http.ProcessResponse(
                "http://127.0.0.1:1", {"speech": "я" * 9000, "context": "я" * 3400},
                (5, 5), cancel_event=cancel, request_kwargs={},
            )
    finally:
        timer.cancel()
        timer.join()
    assert time.monotonic() - start < 2
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


@pytest.mark.parametrize("phase", ["send", "headers", "shared_budget"])
def test_one_deadline_covers_request_and_headers(synthetic_worker, phase):
    import threading
    import time
    import requests

    bodies = {
        "send": "time.sleep(7)\n",
        "headers": "sys.stdin.buffer.readline()\ntime.sleep(7)\n",
        # A fresh budget after send would incorrectly accept these headers.
        "shared_budget": ("time.sleep(0.2)\nsys.stdin.buffer.readline()\ntime.sleep(0.2)\n"
                          "print(json.dumps({'kind': 'head', 'status': 200}), flush=True)\n"),
    }
    children = synthetic_worker(bodies[phase])
    start = time.monotonic()
    with pytest.raises(requests.Timeout, match="before headers"):
        cancellable_http.ProcessResponse(
            "http://127.0.0.1:1", {"speech": "я" * 12400}, (0.15, 0.15),
            cancel_event=threading.Event(), request_kwargs={},
        )
    assert time.monotonic() - start < 2
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


def test_partial_request_writes_preserve_every_byte(synthetic_worker, monkeypatch):
    import hashlib
    import json
    import threading

    children = synthetic_worker(
        "data = sys.stdin.buffer.readline()\n"
        "print(json.dumps({'kind': 'head', 'status': 200, 'text': hashlib.sha256(data).hexdigest()}), flush=True)\n"
        "print(json.dumps({'kind': 'line', 'text': 'complete'}), flush=True)\n"
        "print(json.dumps({'kind': 'end'}), flush=True)\n"
    )
    real_write = cancellable_http.os.write
    writes = []

    def partial_write(fd, data):
        writes.append(len(data))
        if len(writes) == 1:
            raise BlockingIOError()
        return real_write(fd, data[:1024])

    monkeypatch.setattr(cancellable_http.os, "write", partial_write)
    payload = {"speech": "я" * 20000}
    expected = json.dumps({"url": "http://127.0.0.1:1", "payload": payload,
                           "timeout": [2, 2], "kwargs": {}}).encode() + b"\n"
    with cancellable_http.ProcessResponse(
        "http://127.0.0.1:1", payload, (2, 2),
        cancel_event=threading.Event(), request_kwargs={},
    ) as response:
        assert response.text == hashlib.sha256(expected).hexdigest()
        assert list(response.iter_lines()) == [b"complete"]
    assert len(writes) > 100
    assert children[0].poll() is not None


def test_header_deadline_does_not_limit_generation(synthetic_worker):
    import threading

    children = synthetic_worker(
        "sys.stdin.buffer.readline()\n"
        "print(json.dumps({'kind': 'head', 'status': 200}), flush=True)\n"
        "time.sleep(0.5)\n"
        "print(json.dumps({'kind': 'line', 'text': 'complete'}), flush=True)\n"
        "print(json.dumps({'kind': 'end'}), flush=True)\n"
    )
    with cancellable_http.ProcessResponse(
        "http://127.0.0.1:1", {}, (0.2, 0.2),
        cancel_event=threading.Event(), request_kwargs={},
    ) as response:
        assert list(response.iter_lines()) == [b"complete"]
    assert children[0].poll() is not None


def test_child_exit_during_request_reaps_and_closes_pipes(synthetic_worker):
    import threading

    children = synthetic_worker("sys.exit(0)\n")
    with pytest.raises((BrokenPipeError, ConnectionError)):
        cancellable_http.ProcessResponse(
            "http://127.0.0.1:1", {"speech": "я" * 12400}, (1, 1),
            cancel_event=threading.Event(), request_kwargs={},
        )
    assert children[0].poll() is not None
    assert children[0].stdin.closed and children[0].stdout.closed


@pytest.mark.parametrize("timeout", [None, 0, -1, float("inf"), float("nan"), (1,), (1, 0), (1e308, 1e308)])
def test_invalid_preheader_budget_does_not_spawn(monkeypatch, timeout):
    import threading

    def unexpected_spawn(*_args, **_kwargs):
        pytest.fail("invalid timeout must be refused before spawning")

    monkeypatch.setattr(cancellable_http.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(ValueError, match="finite positive"):
        cancellable_http.ProcessResponse(
            "http://127.0.0.1:1", {}, timeout,
            cancel_event=threading.Event(), request_kwargs={},
        )
