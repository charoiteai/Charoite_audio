"""Момент записи из самого файла — для штампа встречи при импорте.

mtime файла — ненадёжный источник даты встречи: провайдер синка трогает
его без записи, скачивание из почты, браузера или мессенджера ставит
момент скачивания, `cp` без `-p` — момент копирования. Сама запись знает,
когда её сделали:

- MP4-семейство (m4a/mp4/mov/m4v): `creation_time` в `moov/mvhd` (UTC от
  1904-01-01; ffmpeg пишет 0 — «не знаю») и дата `©day` в
  `moov/udta/meta/ilst` (строка ISO-8601, часто с поясом);
- CAF: пары «ключ\\0значение\\0» в чанке `info` (`recorded date`);
  AVAudioRecorder на iPhone чанка не пишет — компаньон кладёт штамп в имя
  (`iphone_2026-09-06_123910.caf`), имя разбираем как последний источник
  и только у медиа-файлов (у текста и сабов имя — что угодно);
- WAV: `ICRD` в `LIST/INFO`; MP3: `TDRC` (ID3v2.4) или `TYER`+`TDAT`+`TIME`
  (ID3v2.3).

`recorded_at(path)` — наивное локальное время (как
`datetime.fromtimestamp(mtime)`), None — файл ничего не знает или знает
нелепое (эпоха 1904, момент позже «сейчас» — чужие часы или чужой пояс у
штампа без пояса). Дата без времени — тоже None: для штампа встречи нужна
минута, а не день. Правдоподобие — здесь и только здесь (_sane), импорт
его не перепроверяет.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import struct

MP4_SUFFIXES = {".m4a", ".mp4", ".mov", ".m4v", ".m4b"}
# штамп в имени берём только у записей: у текста/сабов имя — что угодно
MEDIA_SUFFIXES = MP4_SUFFIXES | {".caf", ".wav", ".wave", ".mp3", ".aif", ".aiff",
                                 ".ogg", ".opus", ".flac", ".webm", ".amr"}
_MP4_EPOCH = dt.datetime(1904, 1, 1, tzinfo=dt.timezone.utc)
_MOOV_LIMIT = 64 * 1024 * 1024        # больше — не индекс, а мусор
_LIST_LIMIT = 16 * 1024 * 1024        # LIST/INFO у WAV: с запасом на раздутые комментарии
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:[.,]\d+)?\s*"
                  r"(Z|[+-]\d{2}:?\d{2})?")
# штамп в имени: iphone_2026-09-06_123910, «2026-09-05 14-13-00», 2026-09-05T1413
_NAME = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[-.:_]?(\d{2})(?:[-.:_]?(\d{2}))?(?!\d)")
_EARLIEST = dt.datetime(2000, 1, 1)


def recorded_at(path: pathlib.Path | str) -> dt.datetime | None:
    """Момент записи по контейнеру, иначе по штампу в имени (только у
    медиа), иначе None. Каждый источник проходит _sane сам по себе: нелепый
    ©day не должен глушить честный mvhd или штамп в имени."""
    p = pathlib.Path(path)
    suffix = p.suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        return None
    found = None
    try:
        if suffix in MP4_SUFFIXES:
            found = _mp4(p)
        elif suffix == ".caf":
            found = _caf(p)
        elif suffix in (".wav", ".wave"):
            found = _wav(p)
        elif suffix == ".mp3":
            found = _mp3(p)
    except (OSError, struct.error, ValueError, UnicodeDecodeError, IndexError):
        found = None
    found = _sane(found)
    if found is None:
        found = _sane(from_name(p.stem))
    return found


def from_name(stem: str) -> dt.datetime | None:
    """Штамп из имени файла: компаньон, диктофоны, экспорт встреч."""
    m = _NAME.search(stem)
    if not m:
        return None
    y, mo, d, hh, mm, ss = (int(x) if x else 0 for x in m.groups())
    try:
        return dt.datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


def has_zone(text: str) -> bool:
    """В строке ISO-8601 назван пояс (Z или ±ЧЧ:ММ)?"""
    m = _ISO.search(text or "")
    return bool(m and m.group(7))


def parse_iso(text: str) -> dt.datetime | None:
    """ISO-8601 с временем → наивное локальное; без времени или мусор → None."""
    m = _ISO.search(text or "")
    if not m:
        return None
    y, mo, d, hh, mm = (int(x) for x in m.groups()[:5])
    ss = int(m.group(6) or 0)
    try:
        moment = dt.datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None
    zone = m.group(7)
    if not zone:
        return moment                      # пояса нет — считаем локальным
    if zone == "Z":
        offset = dt.timedelta(0)
    else:
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        offset = sign * dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    aware = moment.replace(tzinfo=dt.timezone(offset))
    return aware.astimezone().replace(tzinfo=None)


def _sane(moment: dt.datetime | None) -> dt.datetime | None:
    if moment is None:
        return None
    if moment < _EARLIEST or moment > dt.datetime.now():
        return None
    return moment


# --- MP4 / QuickTime -------------------------------------------------------

def _mp4(p: pathlib.Path) -> dt.datetime | None:
    with p.open("rb") as fh:
        moov = _find_top_box(fh, b"moov")
    if moov is None:
        return None
    # ©day с поясом — самый честный источник (время устройства + его пояс);
    # ©day без пояса — время неизвестно чьё, уступает mvhd (тот всегда UTC)
    day_text = _ilst_day(moov)
    day = _sane(parse_iso(day_text)) if day_text else None
    if day is not None and has_zone(day_text):
        return day
    mvhd = _mvhd_created(moov)
    return mvhd if mvhd is not None else day


def _mvhd_created(moov: bytes) -> dt.datetime | None:
    mvhd = _child(moov, b"mvhd")
    if mvhd is None or len(mvhd) < 20:
        return None
    version = mvhd[0]
    if version == 0:
        created = struct.unpack(">I", mvhd[4:8])[0]
    else:
        created = struct.unpack(">Q", mvhd[4:12])[0]
    if created == 0:
        return None
    moment = _MP4_EPOCH + dt.timedelta(seconds=created)
    return _sane(moment.astimezone().replace(tzinfo=None))


def _find_top_box(fh, wanted: bytes) -> bytes | None:
    """Тело верхнего бокса `wanted`: идём по заголовкам seek-ом, mdat не читаем."""
    fh.seek(0, 2)
    end = fh.tell()
    off = 0
    while off + 8 <= end:
        fh.seek(off)
        head = fh.read(16)
        if len(head) < 8:
            return None
        size, typ = struct.unpack(">I4s", head[:8])
        hdr = 8
        if size == 1:
            if len(head) < 16:
                return None
            size = struct.unpack(">Q", head[8:16])[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr:
            return None
        if typ == wanted:
            if size - hdr > _MOOV_LIMIT:
                return None
            fh.seek(off + hdr)
            return fh.read(size - hdr)
        off += size
    return None


def _boxes(buf: bytes):
    """Дочерние боксы буфера: (тип, тело)."""
    off = 0
    while off + 8 <= len(buf):
        size, typ = struct.unpack(">I4s", buf[off:off + 8])
        hdr = 8
        if size == 1:
            if off + 16 > len(buf):
                return                         # обрезанный 64-битный заголовок
            size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
            hdr = 16
        elif size == 0:
            size = len(buf) - off
        if size < hdr:
            return
        yield typ, buf[off + hdr:off + size]
        off += size


def _child(buf: bytes, wanted: bytes) -> bytes | None:
    for typ, body in _boxes(buf):
        if typ == wanted:
            return body
    return None


def _ilst_day(moov: bytes) -> str | None:
    """Строка ©day из udta/meta/ilst, как записана."""
    udta = _child(moov, b"udta")
    if udta is None:
        return None
    meta = _child(udta, b"meta")
    if meta is None:
        return None
    ilst = _child(meta[4:], b"ilst")          # meta — full box: 4 байта версии/флагов
    if ilst is None:
        return None
    day = _child(ilst, b"\xa9day")
    if day is None:
        return None
    data = _child(day, b"data")
    if data is None or len(data) < 8:
        return None
    return data[8:].decode("utf-8", "replace")


# --- CAF ------------------------------------------------------------------

def _caf(p: pathlib.Path) -> dt.datetime | None:
    with p.open("rb") as fh:
        if fh.read(4) != b"caff":
            return None
        fh.seek(8)
        while True:
            head = fh.read(12)
            if len(head) < 12:
                return None
            typ, size = struct.unpack(">4sq", head)
            if typ == b"data" or size < 0:
                return None                    # info всегда до data
            if typ == b"info":
                body = fh.read(min(size, 1 << 20))
                return _caf_info(body)
            fh.seek(size, 1)


def _caf_info(body: bytes) -> dt.datetime | None:
    if len(body) < 4:
        return None
    parts = body[4:].split(b"\0")
    pairs = dict(zip(parts[0::2], parts[1::2]))
    for key in (b"recorded date", b"date", b"year"):
        value = pairs.get(key)
        if value:
            return parse_iso(value.decode("utf-8", "replace"))
    return None


# --- WAV ------------------------------------------------------------------

def _wav(p: pathlib.Path) -> dt.datetime | None:
    with p.open("rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return None
        while True:
            ch = fh.read(8)
            if len(ch) < 8:
                return None
            typ, size = struct.unpack("<4sI", ch)
            if typ == b"LIST":
                body = fh.read(min(size, _LIST_LIMIT))
                if body[:4] == b"INFO":
                    found = _riff_info(body[4:])
                    if found is not None:
                        return found
                # adtl/exif или INFO без даты: дочитать хвост и байт
                # выравнивания, иначе курсор встанет на байт раньше
                # следующего заголовка; INFO может лежать и дальше
                fh.seek(size - len(body) + (size & 1), 1)
            else:
                # data — тоже мимо: многие диктофоны пишут LIST/INFO после него
                fh.seek(size + (size & 1), 1)


def _riff_info(body: bytes) -> dt.datetime | None:
    off = 0
    while off + 8 <= len(body):
        typ, size = struct.unpack("<4sI", body[off:off + 8])
        value = body[off + 8:off + 8 + size]
        if typ == b"ICRD":
            return parse_iso(value.rstrip(b"\0").decode("utf-8", "replace"))
        off += 8 + size + (size & 1)
    return None


# --- MP3 / ID3v2 ----------------------------------------------------------

def _mp3(p: pathlib.Path) -> dt.datetime | None:
    with p.open("rb") as fh:
        head = fh.read(10)
        if len(head) < 10 or head[:3] != b"ID3":
            return None
        major, flags = head[3], head[5]
        if major < 3:
            return None                        # v2.2 — трёхбуквенные кадры, не поддерживаем
        size = _syncsafe(head[6:10])
        body = fh.read(min(size, 1 << 20))
    if major == 3 and flags & 0x80:
        body = body.replace(b"\xff\x00", b"\xff")   # unsync всего тега (v2.3)
    off = 0
    if flags & 0x40:                           # расширенный заголовок
        ext = _syncsafe(body[:4]) if major == 4 else struct.unpack(">I", body[:4])[0] + 4
        off = ext
    frames: dict[bytes, str] = {}
    while off + 10 <= len(body):
        fid = body[off:off + 4]
        if fid == b"\0\0\0\0":
            break
        fsize = _syncsafe(body[off + 4:off + 8]) if major == 4 else struct.unpack(">I", body[off + 4:off + 8])[0]
        fflags = body[off + 9]
        payload = body[off + 10:off + 10 + fsize]
        if major == 4:
            # v2.4: unsync — на кадре (размер кадра уже по раз-синхронизированному
            # телу), снимаем сами; сжатие, шифрование, индикатор длины — тело
            # упаковано, текст из него не читаем
            packed = fflags & 0x0D
            if fflags & 0x02:
                payload = payload.replace(b"\xff\x00", b"\xff")
        else:
            packed = fflags & 0xE0                 # сжатие, шифрование, группа
        if fid in (b"TDRC", b"TYER", b"TDAT", b"TIME") and payload and not packed:
            frames[fid] = _id3_text(payload)
        off += 10 + fsize
    if frames.get(b"TDRC"):
        return parse_iso(frames[b"TDRC"])
    year, date, clock = frames.get(b"TYER"), frames.get(b"TDAT"), frames.get(b"TIME")
    if year and date and clock and len(date) >= 4 and len(clock) >= 4:
        return parse_iso(f"{year}-{date[2:4]}-{date[:2]} {clock[:2]}:{clock[2:4]}")
    return None


def _syncsafe(raw: bytes) -> int:
    n = 0
    for b in raw[:4]:
        n = (n << 7) | (b & 0x7F)
    return n


def _id3_text(payload: bytes) -> str:
    enc, raw = payload[0], payload[1:]
    if enc == 0:
        text = raw.decode("latin-1", "replace")
    elif enc == 1:
        text = raw.decode("utf-16", "replace")
    elif enc == 2:
        text = raw.decode("utf-16-be", "replace")
    else:
        text = raw.decode("utf-8", "replace")
    return text.strip("\0").strip()
