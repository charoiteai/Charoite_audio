"""Импорт встреч: парсер vtt/srt и сборка стенограммы конвейера."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from import_meeting import (  # noqa: E402
    parse_subs,
    scan_candidates,
    subs_to_transcript,
    wav_complete,
)

VTT = """WEBVTT

00:00:03.000 --> 00:00:06.000
<v Maria Sokolova>Начнём с провайдера.

00:00:07.000 --> 00:00:12.000
<v Igor>FastPay дешевле, но онбординг шесть недель.

00:14:02.000 --> 00:14:04.000
Igor: Тогда берём YuPay.
"""

SRT = """1
00:00:03,000 --> 00:00:06,000
Мария: Начнём с провайдера.

2
00:00:07,500 --> 00:00:12,000
Просто реплика без спикера.
"""


def _wav(declared_data: int, actual_data: int) -> bytes:
    """Минимальный канонический WAV: заголовок объявляет одно, файл содержит другое."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + declared_data)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", declared_data)
        + bytes(actual_data)
    )


def test_incomplete_wav_is_not_ready(tmp_path):
    wav = tmp_path / "android_meeting.wav"
    wav.write_bytes(_wav(declared_data=32_000, actual_data=8_000))

    assert not wav_complete(wav)
    assert scan_candidates(tmp_path) == []


def test_complete_wav_becomes_scan_candidate(tmp_path):
    wav = tmp_path / "android_meeting.wav"
    wav.write_bytes(_wav(declared_data=32_000, actual_data=32_000))

    assert wav_complete(wav)
    assert scan_candidates(tmp_path) == [wav]


def test_scan_ignores_invalid_wav_and_supported_directories(tmp_path):
    (tmp_path / "broken.wav").write_bytes(b"not a wave")
    (tmp_path / "folder.wav").mkdir()
    note = tmp_path / "meeting.txt"
    note.write_text("готовая стенограмма", encoding="utf-8")

    assert scan_candidates(tmp_path) == [note]


def test_vtt_speakers_and_times():
    entries = parse_subs(VTT)
    assert ("00:00", "Maria Sokolova", "Начнём с провайдера.") in entries
    assert any(sp == "Igor" and "YuPay" in txt for _, sp, txt in entries)
    assert entries[-1][0] == "00:14", "таймкод часа:минуты из блока"


def test_srt_plain_lines_kept():
    entries = parse_subs(SRT)
    assert ("00:00", "Мария", "Начнём с провайдера.") in entries
    assert any(sp == "" and "без спикера" in txt for _, sp, txt in entries)


def test_transcript_format_matches_pipeline():
    body = subs_to_transcript(parse_subs(VTT), "2026-07-15_1400", "zoom.vtt")
    assert body.startswith("# Встреча 2026-07-15_1400 — импорт zoom.vtt")
    assert "**Maria Sokolova** [00:00]:" in body
    assert "**Igor** [00:14]:" in body
