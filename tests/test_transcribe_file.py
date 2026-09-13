"""Офлайн-расшифровка файла: WAV не в формате STT сводится afconvert, а не
уходит в модель как есть (аудит 13.09, DS I3 / GLM I1); afconvert — с потолком
времени (DS M4); список NOISE сравнивается после strip точек (GLM M2)."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import transcribe_file as tf  # noqa: E402
from transcript import NOISE  # noqa: E402


def _wav(path: pathlib.Path, channels: int, rate: int, width: int = 2, frames: int = 1600) -> pathlib.Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * frames * channels * width)
    return path


def test_mono_16k_wav_passes_through(tmp_path):
    src = _wav(tmp_path / "ok.wav", 1, 16000)
    assert tf.wav_is_mono_16bit(src)
    assert tf.to_wav16k(src) == src


def test_stereo_or_other_rate_wav_is_converted_with_a_timeout(tmp_path, monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(tf.subprocess, "run", fake_run)
    for name, channels, rate in (("stereo.wav", 2, 16000), ("hi.wav", 1, 44100), ("both.wav", 2, 48000)):
        src = _wav(tmp_path / name, channels, rate)
        assert not tf.wav_is_mono_16bit(src)
        out = tf.to_wav16k(src)
        assert out != src and out.suffix == ".wav"
    assert len(calls) == 3
    for cmd, kw in calls:
        assert cmd[0] == "afconvert" and "-c" in cmd and cmd[cmd.index("-c") + 1] == "1"
        assert "LEI16@16000" in cmd and kw.get("timeout") == tf.AFCONVERT_TIMEOUT


def test_broken_wav_header_goes_to_afconvert_too(tmp_path, monkeypatch):
    src = tmp_path / "broken.wav"
    src.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    assert not tf.wav_is_mono_16bit(src)
    monkeypatch.setattr(tf.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0))
    assert tf.to_wav16k(src) != src


def test_pcm_at_a_non_16k_rate_is_resampled_not_passed_through(tmp_path, monkeypatch):
    """Крэш-запись .pcm пишется на audio.samplerate конфига; при 48 кГц она уходила
    в модель под своей частотой без единой ошибки (DS r1 I1 / GLM M4 по #555)."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(tf.subprocess, "run", fake_run)
    pcm = tmp_path / "rec.pcm"
    pcm.write_bytes(bytes(4800 * 2))
    out = tf.to_wav16k(pcm, pcm_rate=48000)
    assert len(calls) == 1 and calls[0][0] == "afconvert" and out.name == "rec16k.wav"
    assert tf.wav_is_mono_16bit(pathlib.Path(calls[0][-2])) is False, "промежуточный WAV — 48 кГц"
    calls.clear()
    out = tf.to_wav16k(pcm, pcm_rate=16000)
    assert not calls and tf.wav_is_mono_16bit(out)


def test_noise_list_matches_after_the_readers_strip():
    from transcript import is_noise
    for phrase in ("Продолжение следует...", "Продолжение следует…", "Спасибо за просмотр!",
                   "продолжение следует", "СПАСИБО ЗА ПРОСМОТР."):
        assert is_noise(phrase), phrase
    assert not is_noise("продолжение следует в понедельник")
    for phrase in NOISE:
        assert is_noise(phrase)
