"""Regression: never infer entailment from a truncated pair."""
import types

import numpy as np
import pytest

import nli
from charoite_graph.model_seam import SeamTransportError


@pytest.fixture
def transport(monkeypatch):
    class Session:
        calls = 0
        def get_inputs(self):
            return [types.SimpleNamespace(name="token_type_ids")]
        def run(self, outputs, feed):
            self.calls += 1
            self.feed = feed
            return [np.array([[8., 0., -8.]])]
    session = Session()
    monkeypatch.setattr(nli, "is_available", lambda: True)
    monkeypatch.setattr(nli, "_load", lambda: None)
    monkeypatch.setattr(nli, "_session", session)
    monkeypatch.setattr(nli, "_entail_index", 0, raising=False)
    def encoding(size):
        return types.SimpleNamespace(ids=list(range(size)), attention_mask=[1]*size,
                                     type_ids=[0]*(size-2)+[1, 1])
    def size(value):
        monkeypatch.setattr(nli, "_tokenizer", types.SimpleNamespace(encode=lambda a,b: encoding(value)))
    return session, size


def test_long_pair_is_refused_before_onnx_inference(transport):
    session, size = transport
    size(603)
    with pytest.raises(SeamTransportError, match="512"):
        nli.entail_prob("long premise", "not approved")
    assert session.calls == 0


def test_nightly_judge_preserves_explicit_refusal(transport):
    session, size = transport
    size(513)
    with pytest.raises(SeamTransportError):
        nli.judge().entail("long premise", "hypothesis")
    assert session.calls == 0


def test_live_dedup_keeps_thesis_when_pair_is_too_long(transport):
    session, size = transport
    size(513)
    assert nli.is_duplicate("long thesis", "another thesis") is False
    assert session.calls == 0


def test_exact_limit_keeps_aligned_inputs_and_hypothesis(transport):
    session, size = transport
    size(512)
    assert nli.entail_prob("premise", "hypothesis") > .99
    assert session.feed["input_ids"][0, -1] == 511
    assert session.feed["token_type_ids"][0, -1] == 1
    assert session.feed["attention_mask"].shape == (1, 512)


def test_exported_tokenizer_cannot_hide_truncation(tmp_path, monkeypatch):
    import json
    import onnxruntime
    from tokenizers import Tokenizer, models, pre_tokenizers
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0, "approved": 1, "not": 2}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.enable_truncation(max_length=4)
    tokenizer.save(str(tmp_path / "tokenizer.json"))
    (tmp_path / "config.json").write_text(json.dumps({"id2label": {
        "0": "entailment", "1": "neutral", "2": "contradiction"}}))
    (tmp_path / "model.onnx").touch()
    class Session:
        calls = 0
        def get_inputs(self): return []
        def run(self, outputs, feed):
            self.calls += 1
            return [np.array([[8., 0., -8.]])]
    session = Session()
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a,**k: session)
    monkeypatch.setattr(nli, "_dir", lambda: tmp_path)
    monkeypatch.setattr(nli, "_session", None)
    monkeypatch.setattr(nli, "_tokenizer", None)
    monkeypatch.setattr(nli, "_failed", False)
    monkeypatch.setattr(nli, "_entail_index", None, raising=False)
    with pytest.raises(SeamTransportError):
        nli.entail_prob("approved " * 600, "not approved")
    assert session.calls == 0
