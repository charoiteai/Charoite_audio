"""Импорт встреч: парсер vtt/srt и сборка стенограммы конвейера."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from import_meeting import (  # noqa: E402
    clean_date,
    clean_time,
    parse_subs,
    postponed_files,
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


def test_streaming_header_does_not_freeze_file_forever(tmp_path):
    """Заголовок без честного размера — не повод потерять запись навсегда.

    Потоковые писатели (ffmpeg в pipe, часть диктофонов) оставляют в RIFF
    либо ноль, либо 0xFFFFFFFF. Данные при этом целы, и до проверки такие
    файлы импортировались годами. Судить по такому заголовку нельзя —
    значит и запирать файл в папке импорта на веки нельзя тоже.
    """
    streaming = _wav(declared_data=32_000, actual_data=32_000)
    unknown = tmp_path / "unknown_size.wav"
    unknown.write_bytes(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + streaming[8:])
    zeroed = tmp_path / "zero_size.wav"
    zeroed.write_bytes(b"RIFF" + struct.pack("<I", 0) + streaming[8:])

    assert wav_complete(unknown)
    assert wav_complete(zeroed)
    assert sorted(scan_candidates(tmp_path)) == [unknown, zeroed]


def test_scan_reports_files_it_postponed(tmp_path):
    """Молчание про отложенный файл человек читает как «Чароит меня потерял»."""
    growing = tmp_path / "android_meeting.wav"
    growing.write_bytes(_wav(declared_data=32_000, actual_data=8_000))

    postponed = postponed_files(tmp_path)

    assert postponed == [growing]


# --- время и дата встречи из того, что ввёл человек -------------------------
#
# Справка говорила «ЧЧММ», и значение уходило в имя файла как есть. Человек
# пишет время привычно — `--time 08:44`, — и на диске появлялась стенограмма
# `2026-08-03_08:44.md`: штамп уже не четырёхзначный, а двоеточие в имени
# ломает и разбор имени, и инструменты, которые с этим файлом работают.

def test_time_with_colon_becomes_a_stamp():
    assert clean_time("08:44") == "0844"


def test_time_without_leading_zero_survives():
    assert clean_time("8:44") == "0844"


def test_plain_hhmm_passes_through():
    assert clean_time("0844") == "0844"


def test_stray_punctuation_does_not_make_the_time_wrong():
    # «844:» — это те же 08:44 с промахом по клавише, а не ошибка ввода
    assert clean_time("844:") == "0844"


def test_impossible_time_is_refused():
    import pytest
    for bad in ("2599", "0899", "штука", "123456", ""):
        with pytest.raises(SystemExit):
            clean_time(bad)


def test_date_separator_is_up_to_the_human():
    assert clean_date("2026-08-03") == "2026-08-03"
    assert clean_date("2026.08.03") == "2026-08-03"
    assert clean_date("20260803") == "2026-08-03"


def test_impossible_date_is_refused():
    import pytest
    for bad in ("2026-13-03", "2026-02-31", "03.08.26", "вчера"):
        with pytest.raises(SystemExit):
            clean_date(bad)
