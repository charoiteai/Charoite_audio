"""Regression: the ONNX output label order comes from local metadata."""
import json
import types

import numpy as np
import pytest

import nli


@pytest.fixture
def model(tmp_path, monkeypatch):
    import onnxruntime
    import tokenizers
    (tmp_path / "model.onnx").touch()
    (tmp_path / "tokenizer.json").write_text("{}")
    class Session:
        def get_inputs(self): return []
        def run(self, outputs, feed): return [np.array([[-8., 0., 8.]])]
    tokenizer = types.SimpleNamespace(no_truncation=lambda: None, encode=lambda a,b: types.SimpleNamespace(
        ids=[1,2,3], attention_mask=[1,1,1], type_ids=[0,0,1]))
    monkeypatch.setattr(nli, "_dir", lambda: tmp_path)
    monkeypatch.setattr(nli, "_session", None)
    monkeypatch.setattr(nli, "_tokenizer", None)
    monkeypatch.setattr(nli, "_failed", False)
    monkeypatch.setattr(nli, "_entail_index", None, raising=False)
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a,**k: Session())
    monkeypatch.setattr(tokenizers.Tokenizer, "from_file", lambda path: tokenizer)
    return tmp_path


def test_reordered_entailment_is_read_from_config(model):
    (model / "config.json").write_text(json.dumps({"id2label": {
        "0": "contradiction", "1": "neutral", "2": "entailment"}}))
    assert nli.ready()
    assert nli.entail_prob("approved", "approved") > .99


@pytest.mark.parametrize("meta", [
    None, {}, {"id2label": {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}},
    {"id2label": {"0": "entailment", "1": "entailment", "2": "contradiction"}},
    {"id2label": {"0": "entailment", "1": "neutral", "3": "contradiction"}},
])
def test_missing_or_ambiguous_labels_refuse_model(model, meta):
    if meta is not None:
        (model / "config.json").write_text(json.dumps(meta))
    assert nli.ready() is False
    with pytest.raises(OSError):
        nli.judge().entail("a", "b")


@pytest.mark.parametrize("logits", [[1., 2.], [float("nan"), 0., 1.]])
def test_output_must_match_three_finite_label_logits(model, monkeypatch, logits):
    from charoite_graph.model_seam import SeamTransportError
    (model / "config.json").write_text(json.dumps({"id2label": {
        "0": "entailment", "1": "neutral", "2": "contradiction"}}))
    assert nli.ready()
    monkeypatch.setattr(nli._session, "run", lambda *a: [np.array([logits])])
    with pytest.raises(SeamTransportError):
        nli.judge().entail("a", "b")
