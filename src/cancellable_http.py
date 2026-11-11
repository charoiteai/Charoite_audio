"""Owned HTTP subprocess for local feed requests, including pre-header cancel.

The child owns the connection; the parent reaps it before releasing its model
lease. Closing a client is NOT evidence that a model server stopped computing.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import time

import charoite_paths


class StreamCancelled(Exception):
    """The caller cancelled a request; no speech may be acknowledged."""


class CleanupIncomplete(RuntimeError):
    def __init__(self, response):
        super().__init__("HTTP child cleanup unconfirmed; model slot quarantined")
        self.response = response


_QUARANTINED = set()
_SELECT_POLL_S = 0.025
_READ_CHUNK_BYTES = 65536
_MAX_LINE_BYTES = 2_000_000
_PROBE_ATTEMPTS = 5
_PROBE_TIMEOUT_S = 0.5
_PROBE_RETRY_S = 0.02
_QUARANTINE_POLL_S = 0.25
_STOP_WAIT_S = 0.75
_ERR_PREVIEW_BYTES = 500


class ProcessResponse:
    def __init__(self, url, payload, timeout, *, cancel_event, request_kwargs):
        self.cancel_event = cancel_event
        self.status_code = 0
        self.text = ""
        self._buffer = bytearray()
        self._closed = False
        self._quarantined = False
        self._ended = False
        self._selector = selectors.DefaultSelector()
        self._proc = None
        if cancel_event.is_set():
            raise StreamCancelled()
        try:
            self._proc = subprocess.Popen(
                [sys.executable, str(charoite_paths.code_root(__file__) / "src/cancellable_http.py"), "--http-worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            self._selector.register(self._proc.stdout, selectors.EVENT_READ)
            message = json.dumps({"url": url, "payload": payload, "timeout": timeout,
                                  "kwargs": request_kwargs}).encode() + b"\n"
            self._proc.stdin.write(message)
            self._proc.stdin.close()
            head = self._message()
            if head.get("kind") != "head":
                raise RuntimeError("HTTP worker did not return headers")
            self.status_code, self.text = head["status"], head.get("text", "")
        except BaseException:
            self.close()
            raise

    def _message(self):
        while True:
            if self.cancel_event.is_set():
                raise StreamCancelled()
            if b"\n" in self._buffer:
                line, _, rest = self._buffer.partition(b"\n")
                self._buffer = bytearray(rest)
                value = json.loads(line)
                if value.get("kind") == "error":
                    # Never echo request/response content into an exception.
                    import requests
                    raise requests.ConnectionError("local HTTP worker failed")
                return value
            if not self._selector.select(timeout=_SELECT_POLL_S):
                continue
            data = os.read(self._proc.stdout.fileno(), _READ_CHUNK_BYTES)
            if not data:
                raise ConnectionError("HTTP worker ended before stream completion")
            self._buffer.extend(data)
            if len(self._buffer) > _MAX_LINE_BYTES:
                raise ValueError("HTTP worker line exceeds limit")

    def iter_lines(self):
        while not self._ended:
            message = self._message()
            if message["kind"] == "line":
                yield message["text"].encode("utf-8")
            elif message["kind"] == "end":
                self._ended = True
            else:
                raise ValueError("unexpected HTTP worker message")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _check_owned_command(self):
        proc = self._proc
        expected = str(charoite_paths.code_root(__file__) / "src/cancellable_http.py")
        # The worker exits naturally right after it sends the final stream line.
        # ps can therefore miss a not-yet-reaped child (rc=1, empty output) while
        # poll() is still None; that is a normal exit, not an unconfirmed identity.
        for _ in range(_PROBE_ATTEMPTS):
            if proc.poll() is not None:
                return False
            try:
                result = subprocess.run(
                    ["/bin/ps", "-ww", "-p", str(proc.pid), "-o", "command="],
                    capture_output=True, timeout=_PROBE_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired):
                if proc.poll() is not None:
                    return False
                raise CleanupIncomplete(self) from None
            if proc.poll() is not None:
                return False
            command = result.stdout.decode("utf-8", "replace")
            if "<defunct>" in command or "(Python)" in command:
                # Exited worker: zombie or macOS' transient "(Python)" form.
                proc.poll()
                return False
            if result.returncode == 0:
                if expected in command and "--http-worker" in command:
                    return True
                raise CleanupIncomplete(self)
            time.sleep(_PROBE_RETRY_S)
        raise CleanupIncomplete(self)

    def quarantine(self, lease, call):
        """Retain/refresh the lease until the unconfirmed child actually exits.

        No signals are sent after an identity-probe failure. The daemon's slot
        stays poisoned, so a manual question gets an honest busy response.
        """
        if self._quarantined:
            return
        self._quarantined = True
        _QUARANTINED.add(self)
        def await_exit():
            progress_failed = False
            while True:
                try:
                    self._proc.wait(timeout=_QUARANTINE_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    pass
                except OSError:
                    # Cannot confirm reap: retain ownership and do not release the lease.
                    time.sleep(_QUARANTINE_POLL_S)
                try:
                    lease.progress()
                except Exception:  # noqa: BLE001 — lost telemetry must not release a live request
                    if not progress_failed:
                        print("HTTP quarantine: lease progress failed; child still owned", file=sys.stderr)
                        progress_failed = True
            self._dispose()
            lease.__exit__(None, None, None)
            if call is not None:
                call.finish("failed")
            _QUARANTINED.discard(self)
        import threads
        threads.spawn(await_exit, name="http-quarantine", role="meeting",
                      detached="retain an unconfirmed HTTP child and its lease until it exits")

    def _dispose(self):
        self._closed = True
        self._selector.close()
        if self._proc is not None:
            for stream in (self._proc.stdin, self._proc.stdout):
                if stream is not None:
                    stream.close()

    def close(self):
        if self._closed:
            return
        if self._quarantined:
            raise CleanupIncomplete(self)
        proc = self._proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    if self._check_owned_command():
                        proc.terminate()
                    try:
                        proc.wait(timeout=_STOP_WAIT_S)
                    except subprocess.TimeoutExpired:
                        if self._check_owned_command():
                            proc.kill()
                        proc.wait(timeout=_STOP_WAIT_S)
            except (OSError, subprocess.TimeoutExpired):
                if proc.poll() is None:
                    raise CleanupIncomplete(self) from None
        self._dispose()


def _worker():
    import requests
    import privacy

    def send(value):
        sys.stdout.write(json.dumps(value) + "\n")
        sys.stdout.flush()

    spec = json.loads(sys.stdin.buffer.readline())
    try:
        url = spec["url"]
        if not privacy.is_loopback_url(url):
            raise ValueError("HTTP worker requires loopback")
        kwargs = {k: v for k, v in spec["kwargs"].items() if k not in ("proxies", "allow_redirects")}
        with requests.post(url, json=spec["payload"], stream=True,
                           timeout=tuple(spec["timeout"]) if isinstance(spec["timeout"], list)
                           else spec["timeout"], **kwargs, **privacy.proxies_for(url)) as response:
            send({"kind": "head", "status": response.status_code,
                  "text": response.raw.read(_ERR_PREVIEW_BYTES).decode("utf-8", "replace")
                  if response.status_code != 200 else ""})
            if response.status_code == 200:
                for line in response.iter_lines(chunk_size=1):
                    send({"kind": "line", "text": line.decode("utf-8")})
            send({"kind": "end"})
    except Exception:  # noqa: BLE001 — communicate only a sanitized failure to the parent
        send({"kind": "error"})


if __name__ == "__main__":
    from charoite_paths import harden_umask
    harden_umask()
    import argparse
    parser = argparse.ArgumentParser(description="Owned local feed HTTP worker")
    parser.add_argument("--http-worker", action="store_true", required=True,
                        help="read one request specification from stdin")
    parser.parse_args()
    _worker()
