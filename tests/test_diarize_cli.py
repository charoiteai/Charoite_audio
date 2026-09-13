"""Разбор аргументов офлайн-диаризатора: до 13.09 значение `--channel right`
уезжало в штамп файла, `--channel=right` молча давало left, `--speakers 2`
игнорировался (аудит 13.09, DS I1/I2/M5, GLM M3)."""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import diarize  # noqa: E402


def test_channel_value_does_not_leak_into_positionals():
    pos, channel, n = diarize.parse_args(["rec.wav", "--channel", "right", "1400"])
    assert (pos, channel, n) == (["rec.wav", "1400"], "right", -1)
    pos, channel, n = diarize.parse_args(["--channel", "right", "rec.wav"])
    assert (pos, channel, n) == (["rec.wav"], "right", -1)


def test_both_key_forms_are_equal():
    assert diarize.parse_args(["rec.wav", "--channel=right"])[1] == "right"
    assert diarize.parse_args(["rec.wav", "--speakers=2"])[2] == 2
    assert diarize.parse_args(["rec.wav", "--speakers", "3"])[2] == 3
    assert diarize.parse_args(["rec.wav"])[1:] == ("left", -1)
    assert diarize.parse_args(["rec.wav", "--speakers=-1"])[2] == -1   # явное «авто»


def test_wav_not_in_int16_goes_through_afconvert_keeping_channels(tmp_path, monkeypatch):
    """8-бит/float/EXTENSIBLE WAV раньше читались как int16 или роняли CLI; теперь
    сводятся afconvert БЕЗ -c 1 — каналы нужны раздельно (GLM r1 I1 по #555)."""
    import subprocess
    import wave

    import numpy as np

    src = tmp_path / "eight.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(2); w.setsampwidth(1); w.setframerate(16000)
        w.writeframes(bytes(3200))
    assert not diarize.wav_is_int16(src)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        with wave.open(cmd[-1], "wb") as w:            # afconvert «свёл» в 16 бит, каналы целы
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(np.zeros(3200, dtype="<i2").tobytes())
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(diarize.subprocess, "run", fake_run)
    audio, sr = diarize.load_audio(src, "right")
    assert sr == 16000 and len(audio) == 1600
    assert len(calls) == 1 and calls[0][0] == "afconvert" and "-c" not in calls[0]
    assert calls[0][-2] == str(src)
    ok = tmp_path / "ok.wav"
    with wave.open(str(ok), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(3200))
    assert diarize.wav_is_int16(ok)
    diarize.load_audio(ok, "left")
    assert len(calls) == 1, "16-битный WAV сводить незачем"


@pytest.mark.parametrize("argv", [
    ["rec.wav", "--channel", "centre"],
    ["rec.wav", "--channel"],
    ["rec.wav", "--speakers=two"],
    ["rec.wav", "--speakers", "0"],
    ["rec.wav", "--speakers=-3"],
    ["rec.wav", "--loud"],
    ["--channel", "right"],
])
def test_bad_values_fail_loudly_instead_of_silently(argv):
    with pytest.raises(SystemExit):
        diarize.parse_args(argv)
