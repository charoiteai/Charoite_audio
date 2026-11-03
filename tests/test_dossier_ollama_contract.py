"""Fake-backend contract checks, not experiments on an installed Ollama.

LLM, local_complete, request serialization, stream decoding and response cleanup
are real. Only requests.get/post are replaced; no sockets or model registry
mutations occur. The fake's :local/overflow behavior models the trusted v0.34.4
server contract and still needs separate installed-server integration evidence.
"""
from __future__ import annotations

import copy
import json

import pytest
import requests

import dossier_incremental as inc
import llm
import privacy


MODEL = "qwen3.6:35b-mlx"
PROMPT = "Invented complete input; never send a partial result as success."
CONFIG = {"llm": {"engine": "ollama", "model": MODEL, "num_ctx": 8192,
                  "base_url": "http://127.0.0.1:11434", "cloud_fallback_local": False},
          "sufler": {"role": "Return a synthetic factual JSON object."}}


class Response:
    def __init__(self, body=None, *, status=200, raw=None, frames=()):
        self.content = json.dumps(body).encode() if raw is None else raw
        self.status_code, self.frames, self.closed = status, frames, False

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def iter_lines(self):
        for frame in self.frames:
            yield json.dumps(frame).encode()


class Backend:
    def __init__(self):
        self.version = {"version": "0.34.4"}
        self.version_status, self.version_raw, self.version_error = 200, None, None
        self.calls, self.responses = [], []
        self.remote_manifest, self.retag_after_version = False, False
        self.forwarded, self.truncated = 0, False
        self.template_cost, self.late_error = 0, False

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, "kwargs": copy.deepcopy(kwargs)})
        if url.endswith("/api/version"):
            if self.version_error:
                raise self.version_error
            response = Response(self.version, status=self.version_status, raw=self.version_raw)
            if self.retag_after_version:
                self.remote_manifest = True
        else:
            assert url.endswith("/api/tags"), "Unexpected metadata endpoint."
            response = Response({"models": [{"name": MODEL}]})
        self.responses.append((url, response))
        return response

    def post(self, url, *, json, **kwargs):
        payload = copy.deepcopy(json)
        self.calls.append({"method": "POST", "url": url, "payload": payload, "kwargs": copy.deepcopy(kwargs)})
        assert url.endswith("/api/chat"), "A model-registry mutation or unexpected endpoint was requested."
        if self.remote_manifest:
            if payload["model"].lower().endswith(":local"):
                response = Response({"error": "remote manifest cannot be used through an explicit local selector"}, status=404)
                self.responses.append((url, response))
                return response
            self.forwarded += 1
        message_cost = len(payload["messages"][-1]["content"]) + self.template_cost
        if message_cost > payload["options"]["num_ctx"]:
            if payload.get("truncate") is False and payload.get("shift") is False:
                response = Response({"error": "synthetic server template exceeds the input window"}, status=400)
                self.responses.append((url, response))
                return response
            self.truncated = True
        frames = [{"message": {"content": '{"ok":true}'}, "done": not self.late_error}]
        if self.late_error:
            frames.append({"error": "synthetic generation reached the context boundary with shift disabled"})
        response = Response({}, frames=frames)
        self.responses.append((url, response))
        return response


@pytest.fixture
def backend(monkeypatch, tmp_path):
    wire = Backend()
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.setattr(llm.requests, "get", wire.get)
    monkeypatch.setattr(llm.requests, "post", wire.post)
    return wire


@pytest.mark.parametrize("requested,expected", [
    (MODEL, MODEL + ":local"), (MODEL + ":local", MODEL + ":local"),
    (MODEL + ":LOCAL", MODEL + ":local"),
    ("registry.example:5000/org/model:tag", "registry.example:5000/org/model:tag:local"),
])
def test_force_local_reference_uses_only_closed_version_metadata(backend, requested, expected):
    selected = llm.LLM(copy.deepcopy(CONFIG)).require_local_ollama_model(requested)
    assert selected == expected
    assert len(backend.calls) == 1 and backend.calls[0]["method"] == "GET"
    call = backend.calls[0]
    assert call["url"].endswith("/api/version")
    assert call["kwargs"] == {"timeout": 3, **privacy.proxies_for(call["url"]), "allow_redirects": False}
    assert backend.responses[0][1].closed


@pytest.mark.parametrize("requested", ["model:cloud", "model:tag-cloud", "model:tag-CLOUD:local",
    "model:local:local", "model:cloud:local", "model:local:cloud", "model:local:other",
    "model:tag:LOCAL:local", "", " model", "model\n", 42])
def test_cloud_legacy_and_nested_selectors_are_refused_before_any_http(backend, requested):
    with pytest.raises(privacy.PrivacyRefused):
        llm.LLM(copy.deepcopy(CONFIG)).require_local_ollama_model(requested)
    assert backend.calls == [] and backend.forwarded == 0


def test_omitted_model_uses_existing_resolution_then_forces_local(backend):
    selected = llm.LLM(copy.deepcopy(CONFIG)).require_local_ollama_model()
    assert selected == MODEL + ":local"
    assert [call["url"].rsplit("/", 1)[-1] for call in backend.calls] == ["tags", "version"]
    assert backend.responses[-1][1].closed


@pytest.mark.parametrize("version", ["0.34.4", "0.34.5", "0.35.0", "1.0.0", "0.34.4+build.7"])
def test_supported_stable_versions_allow_actual_dossier_transport(backend, version):
    backend.version = {"version": version}
    answer = inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert json.loads(answer) == {"ok": True}
    assert [call["method"] for call in backend.calls] == ["GET", "POST"]
    assert backend.calls[-1]["payload"]["model"] == MODEL + ":local"
    assert backend.calls[-1]["payload"]["truncate"] is False and backend.calls[-1]["payload"]["shift"] is False
    assert all(response.closed for _, response in backend.responses)


@pytest.mark.parametrize("body", [{}, [], None, {"version": None}, {"version": 34},
    {"version": "0.34.4", "error": "synthetic metadata failure"},
    {"version": "0.34.3"}, {"version": "0.33.99"}, {"version": "v0.34.4"},
    {"version": "0.34"}, {"version": "0.34.4-rc.1"}, {"version": "0.34.4-dev"},
    {"version": "01.34.4"}, {"version": "0.34.4\n"}, {"version": "0.34.4.1"}])
def test_unknown_or_old_version_never_sends_the_dossier_prompt(backend, body):
    backend.version = body
    with pytest.raises((RuntimeError, inc.Refused)):
        inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert [call["method"] for call in backend.calls] == ["GET"]
    assert backend.responses[0][1].closed and backend.forwarded == 0


@pytest.mark.parametrize("status", [201, 302, 404, 500])
def test_version_endpoint_requires_exact_200_without_redirects(backend, status):
    backend.version_status = status
    with pytest.raises((RuntimeError, inc.Refused)):
        inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert len(backend.calls) == 1 and backend.calls[0]["kwargs"]["allow_redirects"] is False
    assert backend.responses[0][1].closed


def test_malformed_version_json_is_closed_before_prompt_delivery(backend):
    backend.version_raw = b"not JSON"
    with pytest.raises((RuntimeError, inc.Refused)):
        inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert len(backend.calls) == 1 and backend.responses[0][1].closed


def test_version_timeout_does_not_send_any_prompt(backend):
    backend.version_error = requests.Timeout("synthetic version timeout")
    with pytest.raises((requests.RequestException, inc.Refused, RuntimeError)):
        inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert len(backend.calls) == 1 and backend.forwarded == 0


def test_dossier_cloud_configuration_is_refused_before_metadata_or_chat(backend):
    config = copy.deepcopy(CONFIG)
    config["llm"]["engine"] = "cloud"
    with pytest.raises(inc.Refused):
        inc.local_complete(PROMPT, config, system="Return synthetic JSON.", num_predict=64)
    assert backend.calls == []


@pytest.mark.parametrize("model", [MODEL + ":cloud", MODEL + "-cloud", MODEL + ":local:local"])
def test_dossier_cloud_or_nested_reference_never_delivers_a_prompt(backend, model):
    config = copy.deepcopy(CONFIG)
    config["llm"]["model"] = model
    with pytest.raises((privacy.PrivacyRefused, inc.Refused)):
        inc.local_complete(PROMPT, config, system="Return synthetic JSON.", num_predict=64)
    assert backend.calls == [] and backend.forwarded == 0


@pytest.mark.parametrize("flags", [{"truncate": False}, {"shift": False}, {"truncate": False, "shift": False},
                                   {"truncate": True, "shift": True}])
def test_real_stream_serializer_preserves_explicit_flags_at_top_level(backend, flags):
    answer = "".join(llm.LLM(copy.deepcopy(CONFIG)).stream(PROMPT, model=MODEL, num_predict=64, **flags))
    assert json.loads(answer) == {"ok": True}
    payload = backend.calls[-1]["payload"]
    for name in ("truncate", "shift"):
        if name in flags:
            assert payload[name] is flags[name]
        else:
            assert name not in payload
        assert name not in payload["options"]


def test_none_flag_defaults_leave_the_existing_payload_unchanged(backend):
    client = llm.LLM(copy.deepcopy(CONFIG))
    assert "".join(client.stream(PROMPT, model=MODEL, num_predict=64)) == '{"ok":true}'
    baseline = backend.calls[-1]["payload"]
    assert "".join(client.stream(PROMPT, model=MODEL, num_predict=64, truncate=None, shift=None)) == '{"ok":true}'
    assert backend.calls[-1]["payload"] == baseline
    assert set(baseline) == {"model", "messages", "stream", "think", "keep_alive", "options"}


@pytest.mark.parametrize("flag", ["truncate", "shift"])
@pytest.mark.parametrize("value", [0, 1, "false", [], {}])
def test_explicit_flags_require_strict_booleans_before_http(backend, flag, value):
    with pytest.raises(ValueError, match="boolean"):
        list(llm.LLM(copy.deepcopy(CONFIG)).stream(PROMPT, model=MODEL, **{flag: value}))
    assert backend.calls == []


def test_nonollama_stream_cannot_accept_ollama_safety_flags(backend):
    config = copy.deepcopy(CONFIG)
    config["llm"]["engine"] = "mlx-server"
    with pytest.raises(privacy.PrivacyRefused):
        list(llm.LLM(config).stream(PROMPT, truncate=False, shift=False))
    assert backend.calls == []


def test_cloud_ready_stream_cannot_accept_ollama_safety_flags(backend):
    client = llm.LLM(copy.deepcopy(CONFIG))
    client.cloud_ready = True
    with pytest.raises(privacy.PrivacyRefused):
        list(client.stream(PROMPT, model=MODEL, truncate=False, shift=False))
    assert backend.calls == []


@pytest.mark.parametrize("changed", [{"cloud_ready": True}, {"engine": "cloud"}, {"base": "https://example.invalid"}])
def test_local_model_preflight_requires_actual_loopback_ollama_state(backend, changed):
    client = llm.LLM(copy.deepcopy(CONFIG))
    for name, value in changed.items():
        setattr(client, name, value)
    with pytest.raises(privacy.PrivacyRefused):
        client.require_local_ollama_model(MODEL)
    assert backend.calls == []


def test_large_fake_server_template_returns_error_instead_of_truncating(backend):
    backend.template_cost = CONFIG["llm"]["num_ctx"] * 2
    untouched = object()
    result = untouched
    with pytest.raises((llm.LLMHTTPError, inc.Refused)):
        result = inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert result is untouched and not backend.truncated
    payload = backend.calls[-1]["payload"]
    assert payload["messages"][-1]["content"] == PROMPT
    assert payload["truncate"] is False and payload["shift"] is False
    assert backend.responses[-1][1].closed


def test_valid_json_chunk_followed_by_error_is_not_a_successful_completion(backend):
    backend.late_error = True
    untouched = object()
    result = untouched
    with pytest.raises((llm.LLMHTTPError, inc.Refused)):
        result = inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert result is untouched and backend.responses[-1][1].closed
    assert backend.calls[-1]["payload"]["shift"] is False


def test_retag_between_version_and_chat_cannot_forward_explicit_local_request(backend):
    backend.retag_after_version = True
    with pytest.raises((llm.LLMHTTPError, inc.Refused)):
        inc.local_complete(PROMPT, copy.deepcopy(CONFIG), system="Return synthetic JSON.", num_predict=64)
    assert backend.remote_manifest and backend.forwarded == 0
    assert [call["method"] for call in backend.calls] == ["GET", "POST"]
    assert backend.calls[-1]["payload"]["model"] == MODEL + ":local"
    assert backend.responses[-1][1].closed
