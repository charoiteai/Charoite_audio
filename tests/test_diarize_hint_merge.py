"""Склейка осколков после подсказки числа голосов (№559): дверь diarize.merge_shards
и её вызов из diarize_channel пересборки."""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import diarize as D  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

AUDIO = np.zeros(16000 * 12, dtype=np.float32)
SEGS = [(0.0, 5.0, 0), (5.0, 9.0, 1), (9.0, 12.0, 2)]


def test_the_merge_door_is_the_module_merge(monkeypatch):
    seen = []
    monkeypatch.setattr(D, "_merge_shards", lambda audio, sr, segs: seen.append((sr, segs)) or [(0.0, 12.0, 0)])
    assert D.merge_shards(AUDIO, 16000, SEGS) == [(0.0, 12.0, 0)]
    assert seen == [(16000, SEGS)]


def test_a_hinted_channel_is_merged_after_the_forced_clusters(monkeypatch):
    """Микрофон очной встречи: подсказка, потом склейка — число становится верхней
    границей, куски одного голоса сводятся обратно."""
    calls = []
    monkeypatch.setattr(rt, "diarize", lambda a, sr, num_speakers=-1: calls.append(num_speakers) or list(SEGS))
    monkeypatch.setattr(rt, "merge_voice_shards", lambda a, sr, segs: [(s, e, 0) for s, e, _ in segs])
    out = rt.diarize_channel(AUDIO, 16000, num_speakers=3, merge_shards=True)
    assert calls == [3] and {k for *_, k in out} == {0}


def test_without_the_flag_the_forced_clusters_stay(monkeypatch):
    """Канал собеседников с подсказкой — как было: число кластеров жёсткое, без склейки."""
    monkeypatch.setattr(rt, "diarize", lambda a, sr, num_speakers=-1: list(SEGS))
    monkeypatch.setattr(rt, "merge_voice_shards", lambda *a: (_ for _ in ()).throw(AssertionError("склейка")))
    assert {k for *_, k in rt.diarize_channel(AUDIO, 16000, num_speakers=3)} == {0, 1, 2}


def test_merged_segments_still_lose_the_short_ones(monkeypatch):
    monkeypatch.setattr(rt, "diarize", lambda a, sr, num_speakers=-1: list(SEGS))
    monkeypatch.setattr(rt, "merge_voice_shards", lambda a, sr, segs: [(0.0, 0.5, 0), (1.0, 3.0, 0)])
    assert rt.diarize_channel(AUDIO, 16000, num_speakers=3, merge_shards=True) == [(1.0, 3.0, 0)]


def test_a_failing_merge_is_a_failed_channel(monkeypatch):
    def boom(*a):
        raise RuntimeError("эмбеддинги не посчитались")
    monkeypatch.setattr(rt, "diarize", lambda a, sr, num_speakers=-1: list(SEGS))
    monkeypatch.setattr(rt, "merge_voice_shards", boom)
    assert rt.diarize_channel(AUDIO, 16000, num_speakers=3, merge_shards=True) is None
