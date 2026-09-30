"""Подсказка числа голосов и склейка осколков в diarize.diarize() (№559)."""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import diarize as D  # noqa: E402


@pytest.fixture
def fake_sherpa(monkeypatch):
    """sherpa без моделей: процесс отдаёт три отрезка, кластеризацию запоминаем."""
    seen = {}

    class _Seg:
        def __init__(self, s, e, k):
            self.start, self.end, self.speaker = s, e, k

    class _Result(list):
        def sort_by_start_time(self):
            return self

    class _Diar:
        sample_rate = 16000

        def __init__(self, cfg):
            pass

        def process(self, audio):
            return _Result([_Seg(0.0, 5.0, 0), _Seg(5.0, 9.0, 1), _Seg(9.0, 12.0, 2)])

    def clustering(**kw):
        seen["clustering"] = kw
        return kw

    fake = types.SimpleNamespace(FastClusteringConfig=clustering,
                                 OfflineSpeakerDiarizationConfig=lambda **kw: kw,
                                 OfflineSpeakerDiarization=_Diar)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    monkeypatch.setattr(D.sherpa_config, "segmentation_config", lambda *a, **k: None)
    monkeypatch.setattr(D.sherpa_config, "embedding_config", lambda *a, **k: None)
    monkeypatch.setattr(D, "_merge_shards", lambda audio, sr, segs: seen.setdefault("merged", segs) and
                        [(s, e, 0) for s, e, _ in segs])
    return seen


AUDIO = np.zeros(16000 * 12, dtype=np.float32)


def test_auto_mode_merges_shards(fake_sherpa):
    assert {k for *_, k in D.diarize(AUDIO, 16000)} == {0}
    assert fake_sherpa["clustering"]["num_clusters"] == -1


def test_a_hint_alone_keeps_the_forced_clusters(fake_sherpa):
    """Канал собеседников с подсказкой — как было: число кластеров жёсткое, без склейки."""
    assert {k for *_, k in D.diarize(AUDIO, 16000, num_speakers=3)} == {0, 1, 2}
    assert "merged" not in fake_sherpa and fake_sherpa["clustering"] == {"num_clusters": 3}


def test_a_hint_with_merge_is_an_upper_bound(fake_sherpa):
    """Микрофон очной встречи: подсказка, потом склейка осколков — куски одного голоса,
    нарезанные по числу живого трекера, сводятся обратно (№559)."""
    assert {k for *_, k in D.diarize(AUDIO, 16000, num_speakers=3, merge_shards=True)} == {0}
    assert fake_sherpa["clustering"] == {"num_clusters": 3}


def test_merge_can_be_switched_off_in_auto_mode(fake_sherpa):
    assert {k for *_, k in D.diarize(AUDIO, 16000, merge_shards=False)} == {0, 1, 2}
