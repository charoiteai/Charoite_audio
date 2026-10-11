"""Regression: the ONNX output label order comes from local metadata."""
import json
import pathlib
import types

import numpy as np
import pytest

import nli
from charoite_graph.model_seam import SeamTransportError

onnxruntime = pytest.importorskip("onnxruntime")
tokenizers = pytest.importorskip("tokenizers")

#: config.json настоящего экспорта mDeBERTa-v3-base-xnli (наша поставка).
MDEBERTA_CONFIG = pathlib.Path(__file__).parent / "fixtures" / "nli_mdeberta_config.json"


class _Session:
    logits = [8., 0., -8.]

    def get_inputs(self):
        return []

    def run(self, outputs, feed):
        return [np.array([self.logits])]


@pytest.fixture
def model(tmp_path, monkeypatch):
    (tmp_path / "model.onnx").touch()
    (tmp_path / "tokenizer.json").write_text("{}")

    tokenizer = types.SimpleNamespace(encode=lambda a, b: types.SimpleNamespace(
        ids=[1, 2, 3], attention_mask=[1, 1, 1], type_ids=[0, 0, 1]))
    monkeypatch.setattr(nli, "_dir", lambda: tmp_path)
    monkeypatch.setattr(nli, "_session", None)
    monkeypatch.setattr(nli, "_tokenizer", None)
    monkeypatch.setattr(nli, "_failed", False)
    monkeypatch.setattr(nli, "_entail_index", None, raising=False)
    monkeypatch.setattr(nli, "_why", "", raising=False)
    monkeypatch.setattr(nli, "_cfg_cache", None, raising=False)
    monkeypatch.setattr(_Session, "logits", [8., 0., -8.])
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: _Session())
    monkeypatch.setattr(tokenizers.Tokenizer, "from_file", lambda path: tokenizer)
    return tmp_path


def _labels(*names):
    return {"id2label": {str(i): n for i, n in enumerate(names)}}


def _peak_at(model, index):
    logits = [-8., -8., -8.]
    logits[index] = 8.
    _Session.logits = logits


def test_shipped_mdeberta_config_reads_entailment_first(model):
    (model / "config.json").write_bytes(MDEBERTA_CONFIG.read_bytes())
    assert nli.judge().refused == ""
    assert nli.ready()
    assert nli._entail_index == 0
    _peak_at(model, 0)
    assert nli.entail_prob("approved", "approved") > .99


@pytest.mark.parametrize("names, index", [
    (("entailment", "neutral", "contradiction"), 0),
    (("contradiction", "neutral", "entailment"), 2),
    (("neutral", "entailment", "contradiction"), 1),
    (("neutral", "contradiction", "entailment"), 2),
    ((" ENTAILMENT ", "Neutral", "contradiction"), 0),
])
def test_entailment_index_is_read_from_config(model, names, index):
    (model / "config.json").write_text(json.dumps(_labels(*names)))
    assert nli.ready()
    assert nli._entail_index == index
    _peak_at(model, index)
    assert nli.entail_prob("approved", "approved") > .99
    _peak_at(model, (index + 1) % 3)
    assert nli.entail_prob("approved", "approved") < .01


@pytest.mark.parametrize("meta, reason", [
    (None, "нет config.json"),
    ({}, "id2label"),
    ([], "id2label"),
    (_labels("LABEL_0", "LABEL_1", "LABEL_2"), "LABEL_0"),
    (_labels("entailment", "entailment", "contradiction"), "entailment/neutral/contradiction"),
    ({"id2label": {"0": "entailment", "1": "neutral", "3": "contradiction"}}, "0, 1, 2"),
    (_labels("entailment", 1, "contradiction"), "строками"),
])
def test_missing_or_ambiguous_labels_refuse_with_the_reason(model, meta, reason):
    if meta is not None:
        (model / "config.json").write_text(json.dumps(meta))
    # дешёвая фаза: причина известна ДО подъёма сессии (tier3 выходит, не тронув ядер)
    assert nli.is_available() is False
    refused = nli.judge().refused
    assert reason in refused and "config.json" in refused
    assert "не найдена" not in refused
    assert nli.ready() is False
    with pytest.raises(SeamTransportError, match=reason):
        nli.judge().entail("a", "b")


def test_missing_model_file_is_named(model):
    (model / "config.json").write_bytes(MDEBERTA_CONFIG.read_bytes())
    (model / "model.onnx").unlink()
    assert "нет файла model.onnx" in nli.judge().refused


def test_broken_session_reports_its_cause_on_every_call(model, monkeypatch, capsys):
    (model / "config.json").write_bytes(MDEBERTA_CONFIG.read_bytes())

    def boom(*a, **k):
        raise RuntimeError("protobuf parsing failed")

    monkeypatch.setattr(onnxruntime, "InferenceSession", boom)
    judge = nli.judge()
    for _ in range(2):
        with pytest.raises(SeamTransportError, match="ONNX-сессия не собралась.*protobuf parsing failed"):
            judge.entail("a", "b")
    assert "protobuf parsing failed" in nli.judge().refused
    assert capsys.readouterr().out.count("protobuf parsing failed") == 1


def test_config_is_parsed_once_per_mtime(model, monkeypatch):
    (model / "config.json").write_bytes(MDEBERTA_CONFIG.read_bytes())
    calls = []
    real = nli._label_index
    monkeypatch.setattr(nli, "_label_index", lambda p: calls.append(p) or real(p))
    for _ in range(5):
        assert nli.is_available()
    assert len(calls) == 1


@pytest.mark.parametrize("logits", [[1., 2.], [float("nan"), 0., 1.]])
def test_output_must_match_three_finite_label_logits(model, monkeypatch, logits):
    (model / "config.json").write_bytes(MDEBERTA_CONFIG.read_bytes())
    assert nli.ready()
    monkeypatch.setattr(nli._session, "run", lambda *a: [np.array([logits])])
    with pytest.raises(SeamTransportError):
        nli.judge().entail("a", "b")
