"""Actual ephemeral loopback sockets and owned children, no model or owner data."""

import contextlib
import json
import selectors
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import cancellable_http
from cancellable_http import CleanupIncomplete, ProcessResponse, StreamCancelled
from llm import LLM


pytestmark = pytest.mark.сеть_разрешена


@contextlib.contextmanager
def local_server(stage):
    started, released, finished = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            started.set()
            try:
                if stage != "headers":
                    self.send_response(200)
                    self.end_headers()
                    if stage == "read":
                        self.wfile.write(b'{"message":{"content":"first"}}\n')
                        self.wfile.flush()
                while not released.wait(.01):
                    with selectors.DefaultSelector() as selector:
                        selector.register(self.connection, selectors.EVENT_READ)
                        if selector.select(0) and self.connection.recv(1, socket.MSG_PEEK) == b"":
                            return
                if stage == "headers":
                    self.send_response(200)
                    self.end_headers()
                self.wfile.write(b'{"done":true}\n')
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                finished.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", started, released, finished
    finally:
        released.set()
        server.shutdown()
        server.server_close()
        worker.join(2)
        assert not worker.is_alive()


@pytest.mark.parametrize("stage", ["headers", "first_token", "read"])
def test_cancel_before_headers_first_token_or_read_reaps_child(stage, monkeypatch):
    children, failures, cancel = [], [], threading.Event()
    original = cancellable_http.subprocess.Popen
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(cancellable_http.subprocess, "Popen", spawn)
    with local_server(stage) as (url, started, released, finished):
        def request():
            try:
                with ProcessResponse(url, {}, (5, 30), cancel_event=cancel, request_kwargs={}) as response:
                    list(response.iter_lines())
            except StreamCancelled:
                failures.append("cancelled")
        worker = threading.Thread(target=request)
        worker.start()
        assert started.wait(3)
        began = time.monotonic()
        cancel.set()
        worker.join(4)
        assert not worker.is_alive()
        assert time.monotonic() - began < 5
        assert failures == ["cancelled"]
        assert children and all(p.poll() is not None for p in children)
        assert finished.wait(1), "controlled cooperative server must observe EOF"


@pytest.mark.parametrize("progress_fails", [False, True])
def test_fault_in_command_probe_keeps_lease_until_confirmed_child_exit(monkeypatch, progress_fails):
    class Lease:
        released = False
        def progress(self):
            if progress_fails:
                raise OSError("synthetic progress failure")
        def __exit__(self, *_):
            self.released = True
    class Call:
        outcome = None
        def finish(self, outcome):
            self.outcome = outcome
    lease, call = Lease(), Call()
    with local_server("first_token") as (url, started, released, finished):
        response = ProcessResponse(url, {}, (5, 5), cancel_event=threading.Event(), request_kwargs={})
        child = response._proc
        original = cancellable_http.subprocess.run
        def failed_probe(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], .5)
        monkeypatch.setattr(cancellable_http.subprocess, "run", failed_probe)
        try:
            with pytest.raises(CleanupIncomplete):
                LLM._LeasedStream(response, lease, call).close()
            time.sleep(.35)  # let the quarantine encounter a progress fault
            assert child.poll() is None and not lease.released
        finally:
            monkeypatch.setattr(cancellable_http.subprocess, "run", original)
            released.set()
        deadline = time.monotonic() + 3
        while not lease.released and time.monotonic() < deadline:
            time.sleep(.01)
        assert lease.released and child.poll() is not None and call.outcome == "failed"
        assert finished.wait(1)


def test_failed_terminate_is_not_reported_as_successful_cleanup(monkeypatch):
    with local_server("first_token") as (url, started, released, finished):
        response = ProcessResponse(url, {}, (5, 5), cancel_event=threading.Event(), request_kwargs={})
        original = response._proc.terminate
        def refuse_signal():
            raise PermissionError("synthetic signal failure")
        monkeypatch.setattr(response._proc, "terminate", refuse_signal)
        try:
            with pytest.raises(CleanupIncomplete):
                response.close()
            assert response._proc.poll() is None and not response._closed
        finally:
            monkeypatch.setattr(response._proc, "terminate", original)
            response.close()
        assert response._proc.poll() is not None


def test_completed_stream_closes_child_before_releasing_lease():
    events = []
    with local_server("first_token") as (url, started, released, finished):
        response = ProcessResponse(url, {}, (5, 5), cancel_event=threading.Event(), request_kwargs={})
        class Lease:
            def progress(self):
                pass
            def __exit__(self, *_):
                assert response._proc.poll() is not None
                events.append("lease closed")
        wrapped = LLM._LeasedStream(response, Lease())
        released.set()
        with wrapped:
            assert [json.loads(line) for line in wrapped.iter_lines()] == [{"done": True}]
        assert events == ["lease closed"]
