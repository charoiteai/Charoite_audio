"""Момент записи из самого файла (src/media_meta) и его место в импорте."""
import datetime as dt
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import media_meta  # noqa: E402
import import_meeting as im  # noqa: E402

LOCAL = dt.datetime(2026, 9, 5, 14, 13, 0)


def _utc_of(local: dt.datetime) -> dt.datetime:
    """Тот же момент в UTC — тесты не зависят от пояса машины."""
    return dt.datetime.fromtimestamp(local.timestamp(), dt.timezone.utc).replace(tzinfo=None)


def box(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), typ) + body


def mvhd(created_utc: dt.datetime | None) -> bytes:
    secs = 0 if created_utc is None else int((created_utc - dt.datetime(1904, 1, 1)).total_seconds())
    return box(b"mvhd", b"\0" * 4 + struct.pack(">II", secs, secs) + b"\0" * 92)


def ilst_day(text: str) -> bytes:
    data = box(b"data", struct.pack(">II", 1, 0) + text.encode())
    return box(b"udta", box(b"meta", b"\0" * 4 + box(b"ilst", box(b"\xa9day", data))))


def m4a(path: Path, *, created_utc=None, day: str | None = None, mdat: int = 5000) -> Path:
    moov_body = mvhd(created_utc) + (ilst_day(day) if day else b"")
    path.write_bytes(box(b"ftyp", b"M4A \0\0\0\0") + box(b"mdat", b"\0" * mdat) + box(b"moov", moov_body))
    return path


def caf(path: Path, info: dict[str, str] | None) -> Path:
    body = b"caff\0\1\0\0" + b"desc" + struct.pack(">q", 32) + b"\0" * 32
    if info is not None:
        pairs = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in info.items())
        body += b"info" + struct.pack(">q", 4 + len(pairs)) + struct.pack(">I", len(info)) + pairs
    body += b"data" + struct.pack(">q", -1) + b"\0" * 64
    path.write_bytes(body)
    return path


def wav(path: Path, icrd: str | None) -> Path:
    fmt = b"fmt " + struct.pack("<I", 16) + b"\0" * 16
    chunks = fmt
    if icrd is not None:
        raw = icrd.encode() + b"\0"
        sub = b"ICRD" + struct.pack("<I", len(raw)) + raw + (b"\0" if len(raw) & 1 else b"")
        chunks += b"LIST" + struct.pack("<I", 4 + len(sub)) + b"INFO" + sub
    chunks += b"data" + struct.pack("<I", 8) + b"\0" * 8
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return path


def id3(path: Path, frames: dict[bytes, str], major: int = 4) -> Path:
    body = b""
    for fid, text in frames.items():
        payload = b"\3" + text.encode()
        size = _syncsafe(len(payload)) if major == 4 else struct.pack(">I", len(payload))
        body += fid + size + b"\0\0" + payload
    path.write_bytes(b"ID3" + bytes([major, 0, 0]) + _syncsafe(len(body)) + body + b"\xff\xfb" * 100)
    return path


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


# --- контейнеры -----------------------------------------------------------

def test_m4a_mvhd_creation_is_utc_and_read_after_mdat(tmp_path):
    p = m4a(tmp_path / "Здесь аптека.m4a", created_utc=_utc_of(LOCAL), mdat=300_000)
    assert media_meta.recorded_at(p) == LOCAL


def test_m4a_day_with_offset_beats_mvhd(tmp_path):
    aware = LOCAL.astimezone()                        # локальный момент с поясом машины
    p = m4a(tmp_path / "video.mp4", created_utc=_utc_of(LOCAL + dt.timedelta(hours=5)),
            day=aware.isoformat())
    assert media_meta.recorded_at(p) == LOCAL


def test_m4a_day_without_time_falls_back_to_mvhd(tmp_path):
    p = m4a(tmp_path / "a.m4a", created_utc=_utc_of(LOCAL), day="2026-09-05")
    assert media_meta.recorded_at(p) == LOCAL


def test_m4a_zero_creation_means_unknown(tmp_path):
    # ffmpeg пишет 0 — «не знаю», а не 1904 год
    assert media_meta.recorded_at(m4a(tmp_path / "ffmpeg.m4a", created_utc=None)) is None


def test_caf_info_recorded_date(tmp_path):
    p = caf(tmp_path / "rec.caf", {"encoder": "x", "recorded date": _utc_of(LOCAL).isoformat() + "Z"})
    assert media_meta.recorded_at(p) == LOCAL


def test_caf_without_info_uses_name_stamp(tmp_path):
    # AVAudioRecorder на iPhone чанка info не пишет — компаньон кладёт штамп в имя
    p = caf(tmp_path / "iphone_2026-09-06_123910.caf", None)
    assert media_meta.recorded_at(p) == dt.datetime(2026, 9, 6, 12, 39, 10)


def test_caf_without_info_and_stamp_knows_nothing(tmp_path):
    assert media_meta.recorded_at(caf(tmp_path / "Исходник.caf", None)) is None


def test_wav_icrd_with_time(tmp_path):
    assert media_meta.recorded_at(wav(tmp_path / "r.wav", "2026-09-05 14:13:00")) == LOCAL


def test_wav_icrd_date_only_is_not_a_moment(tmp_path):
    assert media_meta.recorded_at(wav(tmp_path / "r.wav", "2026-09-05")) is None


def test_mp3_tdrc_v24(tmp_path):
    assert media_meta.recorded_at(id3(tmp_path / "r.mp3", {b"TDRC": "2026-09-05T14:13:00"})) == LOCAL


def test_mp3_tyer_tdat_time_v23(tmp_path):
    p = id3(tmp_path / "r.mp3", {b"TYER": "2026", b"TDAT": "0509", b"TIME": "1413"}, major=3)
    assert media_meta.recorded_at(p) == LOCAL


def test_mp3_year_only_is_not_a_moment(tmp_path):
    assert media_meta.recorded_at(id3(tmp_path / "r.mp3", {b"TYER": "2026"}, major=3)) is None


def test_garbage_and_truncated_files_do_not_raise(tmp_path):
    for name, payload in (("x.m4a", b"\0" * 7), ("x.caf", b"caff" + b"\1" * 20),
                          ("x.wav", b"RIFF\xff\xff\xff\xffWAVE"), ("x.mp3", b"ID3\4\0\0\x7f\x7f\x7f\x7f")):
        p = tmp_path / name
        p.write_bytes(payload)
        assert media_meta.recorded_at(p) is None, name
    assert media_meta.recorded_at(tmp_path / "нет-такого.m4a") is None


def test_future_or_ancient_moment_is_rejected(tmp_path):
    ancient = m4a(tmp_path / "old.m4a", created_utc=dt.datetime(1999, 12, 31, 12))
    assert media_meta.recorded_at(ancient) is None
    future = m4a(tmp_path / "new.m4a", created_utc=_utc_of(dt.datetime.now() + dt.timedelta(days=3)))
    assert media_meta.recorded_at(future) is None


# --- штамп в имени ----------------------------------------------------------

def test_name_stamps():
    assert media_meta.from_name("iphone_2026-09-06_123910") == dt.datetime(2026, 9, 6, 12, 39, 10)
    assert media_meta.from_name("Запись 2026-09-05 14-13-00") == LOCAL
    assert media_meta.from_name("2026-09-05T1413 планёрка") == LOCAL
    assert media_meta.from_name("Recording") is None
    assert media_meta.from_name("2026-13-40_9999") is None          # не дата
    assert media_meta.from_name("v12026-09-05_1413") is None         # цифры впритык — не штамп


def test_parse_iso_zone_and_no_zone():
    assert media_meta.parse_iso("2026-09-05T14:13:00") == LOCAL
    assert media_meta.parse_iso(_utc_of(LOCAL).isoformat() + "Z") == LOCAL
    assert media_meta.parse_iso(_utc_of(LOCAL).isoformat() + "+00:00") == LOCAL
    assert media_meta.parse_iso("2026-09-05") is None
    assert media_meta.parse_iso("") is None


# --- место в импорте ----------------------------------------------------------

def _touch(p: Path, moment: dt.datetime) -> None:
    ts = moment.timestamp()
    os.utime(p, (ts, ts))


def test_import_trusts_recording_when_mtime_drifted(tmp_path):
    p = m4a(tmp_path / "Recording.m4a", created_utc=_utc_of(LOCAL))
    _touch(p, LOCAL + dt.timedelta(days=1, hours=2))      # скачали на следующий день
    moment, note = im.meeting_moment(p)
    assert moment == LOCAL
    assert note and "2026-09-05 14:13" in note and "сдвинут" in note


def test_import_keeps_mtime_within_drift(tmp_path):
    p = m4a(tmp_path / "Recording.m4a", created_utc=_utc_of(LOCAL))
    _touch(p, LOCAL + dt.timedelta(seconds=40))          # телефон дописал — синк положил
    moment, note = im.meeting_moment(p)
    assert moment == LOCAL + dt.timedelta(seconds=40) and note is None


def test_import_falls_back_to_mtime_when_recording_knows_nothing(tmp_path):
    p = caf(tmp_path / "Исходник.caf", None)
    _touch(p, LOCAL)
    assert im.meeting_moment(p) == (LOCAL, None)


def test_import_text_sources_use_mtime(tmp_path):
    p = tmp_path / "zoom.vtt"
    p.write_text("WEBVTT\n", encoding="utf-8")
    _touch(p, LOCAL)
    assert im.meeting_moment(p) == (LOCAL, None)
