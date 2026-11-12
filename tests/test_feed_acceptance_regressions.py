"""Regressions for LLM.feed local model sampling/validation and example configs."""

import contextlib
import json
import threading
from pathlib import Path

import pytest
import yaml

from live_feed import FeedPolicy
from llm import LLM


@pytest.mark.parametrize("engine", ["ollama", "mlx-server"])
def test_live_feed_sends_pinned_sampling_to_its_selected_local_model(engine):
    class CapturedCall(Exception):
        pass

    captured = {}
    model = "qwen3.8:27b-mlx" if engine == "ollama" else "mlx-community/feed-27b"
    llm = LLM.__new__(LLM)
    llm.cloud_ready = False
    llm.base = "http://127.0.0.1:11434"
    llm._cfg = {"sufler": {"feed_model": model}}
    llm.model = "other-model"
    llm.mlx_model = "mlx-community/feed-27b"
    llm.engine = engine
    llm.lang = "en"
    llm.num_ctx = 8192
    llm._chat_keep_alive = lambda: "90m"

    def capture(url, payload, *_args, **_kwargs):
        captured.update(url=url, payload=payload)
        raise CapturedCall

    llm._open_stream = capture
    with pytest.raises(CapturedCall):
        list(llm.feed("The demo starts Tuesday.", "{}", cancel_event=threading.Event()))
    payload = captured["payload"]
    assert payload["model"] == model and payload["stream"] is True
    assert (payload["options"] if engine == "ollama" else payload)["temperature"] == 0.7
    sampling = payload["options"] if engine == "ollama" else payload
    assert sampling["top_p"] == 0.8 and sampling["top_k"] == 20
    assert sampling["repeat_penalty" if engine == "ollama" else "repetition_penalty"] == 1.1
    assert sampling["num_predict" if engine == "ollama" else "max_tokens"] == 600
    if engine == "ollama":
        assert payload["think"] is False
    else:
        assert payload["chat_template_kwargs"]["enable_thinking"] is False


def test_live_feed_rejects_a_model_not_served_by_the_local_mlx_server():
    llm = LLM.__new__(LLM)
    llm.cloud_ready = False
    llm.base = "http://127.0.0.1:8080"
    llm._cfg = {"sufler": {"feed_model": "qwen3.8:27b-mlx"}}
    llm.model = "other-model"
    llm.mlx_model = "mlx-community/other-model"
    llm.engine = "mlx-server"
    llm.lang = "en"
    with pytest.raises(ValueError, match="does not match"):
        list(llm.feed("The demo starts Tuesday.", "{}", cancel_event=threading.Event()))


@pytest.mark.parametrize("name", ["config.example.yaml", "config.example.en.yaml", "config.example.zh.yaml"])
def test_real_example_configs_enable_live_feed_and_preserve_classic_fallback(name):
    cfg = yaml.safe_load((Path(__file__).parents[1] / "config" / name).read_text(encoding="utf-8"))
    policy = FeedPolicy.from_config(cfg)
    assert policy.feed and not policy.instant and not policy.auto_hint and not policy.auto_expand
    assert "feed_model" in cfg["sufler"]
    classic_cfg = {**cfg, "sufler": {**cfg["sufler"], "live_feed": False}}
    classic = FeedPolicy.from_config(classic_cfg)
    assert not classic.feed and classic.instant and classic.auto_hint and classic.auto_expand


@pytest.mark.parametrize(
    "language,prefix",
    [("ru", "Уже сохранённая лента"), ("en", "Current account"), ("zh", "已保存纪要")],
)
def test_feed_payload_formats_context_and_system_prompt_by_language(monkeypatch, language, prefix):
    context = json.dumps({"open_questions": [{"id": "q1", "topic": "Plan", "question": "When?"}]})
    captured = []

    @contextlib.contextmanager
    def transport(url, payload, *_, **__):
        captured.append((url, payload))

        class Response:
            status_code = 200

            def iter_lines(self):
                yield b'{"message":{"content":"{\\"none\\":true}"},"done":true,"done_reason":"stop"}'

        yield Response()

    client = LLM({"llm": {"model": "synthetic-local"}, "sufler": {"role": "Synthetic test", "language": language}})
    monkeypatch.setattr(client, "_open_stream", transport)
    assert "".join(client.feed("New synthetic speech", context, cancel_event=threading.Event())) == '{"none":true}'
    assert captured[0][1]["messages"][1]["content"].startswith(prefix + ":\n" + context)
    assert captured[0][1]["messages"][1]["content"].endswith("New synthetic speech")
    assert captured[0][1]["messages"][0]["content"].startswith({"ru": "Все текстовые", "en": "Write every", "zh": "所有"}[language])
