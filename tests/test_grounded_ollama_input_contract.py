"""Synthetic overflow contract; not an installed Ollama/model experiment.

Exercise real feed/grounded_answer, _open_stream, stream decoding and cursor ACK.
Only metadata, ProcessResponse and lease/ledger I/O are replaced. The transport
models the trusted Ollama contract also used by test_dossier_ollama_contract.
"""
from __future__ import annotations

import contextlib
import copy
import json
import threading
from types import SimpleNamespace

import pytest

import cancellable_http
from grounded_feed.local_runner import LocalFeedRunner, LocalLoopbackAuthority
from grounded_feed.runtime import SourceProducerCutoff, bind_product_source
from grounded_feed.session import GroundedFeedSession
from llm import LLM, LLMHTTPError
from transcript import Transcript


@pytest.fixture
def overflow_transport(monkeypatch):
    client = LLM({"llm": {"engine": "ollama", "model": "synthetic-model",
                          "base_url": "http://127.0.0.1:11434", "num_ctx": 8192},
                  "sufler": {"language": "en", "role": "Synthetic assistant"}}, keep_alive="0")
    monkeypatch.setattr(client, "_ollama_metadata", lambda _ep: (200, {"version": "0.34.4"}))
    monkeypatch.setattr(client, "_call", lambda *_: SimpleNamespace(
        done_s=None, finish=lambda *_: None))
    lease = SimpleNamespace(progress=lambda: None, __exit__=lambda *_: None)
    monkeypatch.setattr(client, "_lease", lambda *_: SimpleNamespace(__enter__=lambda: lease))
    wire = SimpleNamespace(template_cost=8192, omit_controls=(), calls=[], responses=[])

    class Response:
        def __init__(self, status):
            self.status_code, self.closed = status, False
            self.text = "synthetic server template exceeds the input window" if status == 400 else ""

        def iter_lines(self):
            yield json.dumps({"message": {"content": '{"none":true}'},
                              "done": True, "done_reason": "stop"}).encode()

        def close(self):
            self.closed = True

    def transport(url, payload, timeout, *, cancel_event, request_kwargs):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert cancel_event is not None
        request = copy.deepcopy(payload)
        # Explicit negative control reconstructs requests before the fix, or
        # loss of either control. Normal regression cases leave payload intact.
        for key in wire.omit_controls:
            request.pop(key, None)
        wire.calls.append(request)
        cost = len(request["messages"][-1]["content"]) + wire.template_cost
        refuse = (cost > request["options"]["num_ctx"]
                  and request.get("truncate") is False and request.get("shift") is False)
        response = Response(400 if refuse else 200)
        wire.responses.append(response)
        return response

    monkeypatch.setattr(cancellable_http, "ProcessResponse", transport)
    return client, wire


@contextlib.contextmanager
def _slot(*_args, **_kwargs):
    yield True


@pytest.mark.parametrize("omit_controls", [(), ("truncate",), ("shift",), ("truncate", "shift")])
def test_overflow_feed_refuses_without_ack_and_retries_same_speech(
        tmp_path, overflow_transport, omit_controls):
    client, wire = overflow_transport
    wire.omit_controls = omit_controls
    session = GroundedFeedSession(lambda _event: None)
    transcript = Transcript(tmp_path)
    actor = bind_product_source(session, transcript, SourceProducerCutoff(lambda _t: True),
                                source_only_notice=False, local_product_authority=True)
    transcript.add("Synthetic speech: retain this complete input.", speaker="Speaker")
    session.ingest_source_once(limit=16)
    runner = LocalFeedRunner(actor, transcript, client, LocalLoopbackAuthority.verify(client),
                             slot=_slot, stop=threading.Event(), priority=threading.Event(),
                             enabled=lambda: True)
    before = actor.snapshot()
    result = runner.run_next("overflow", force=True)
    assert wire.calls[0]["model"] == "synthetic-model:local"
    assert wire.responses[0].closed

    if omit_controls:
        # Valid `none` after synthetic truncation still ACKs the whole window:
        # parser validation cannot detect that the server dropped input.
        assert wire.responses[0].status_code == 200
        assert result["status"] == "committed"
        assert actor.semantic.state.local_speech_cursor == (1, 0)
        return

    assert wire.responses[0].status_code == 400
    assert result == {"status": "failed", "reason": "LLMHTTPError"}
    assert actor.snapshot() == before
    assert actor.semantic.state.local_speech_cursor == (0, 0)
    wire.template_cost = 0
    assert runner.run_next("retry", force=True)["status"] == "committed"
    assert wire.calls[1]["messages"] == wire.calls[0]["messages"]
    assert wire.responses[1].closed
    assert actor.semantic.state.local_speech_cursor == (1, 0)


def test_manual_grounded_answer_refuses_overflow_before_yield(overflow_transport):
    client, wire = overflow_transport
    chunks = []
    with pytest.raises(LLMHTTPError) as caught:
        for chunk in client.grounded_answer("Synthetic question", system="Use the complete input",
                                            cancel_event=threading.Event()):
            chunks.append(chunk)
    assert caught.value.status == 400
    assert chunks == []
    assert len(wire.calls) == 1
    assert wire.calls[0]["model"] == "synthetic-model:local"
    assert wire.calls[0]["truncate"] is False
    assert wire.calls[0]["shift"] is False
    assert wire.responses[0].closed
