"""Обёртка float → int16 для Parakeet: без клипа громкий всплеск переворачивал
знак, NaN давал мусор (хвост аудита 20.08, DS)."""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from stt import STT  # noqa: E402


def test_to_int16_clips_and_cleans():
    out = STT.to_int16(np.array([0.0, 0.5, 1.7, -3.0, np.nan, np.inf], dtype=np.float32))
    assert out.dtype == np.int16
    assert list(out) == [0, 16383, 32767, -32767, 0, 32767]
