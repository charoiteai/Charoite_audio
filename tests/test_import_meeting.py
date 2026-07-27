"""Импорт встреч: парсер vtt/srt и сборка стенограммы конвейера."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from import_meeting import parse_subs, subs_to_transcript  # noqa: E402

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
