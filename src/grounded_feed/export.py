"""Versioned atomic export/readback for the grounded session state.

One writer: caller-supplied path, temp file in the same directory, fsync,
os.replace. A failed write leaves the previous file untouched; a readback checks
the closed envelope. This is not a crash-recovery replay contract (the actor's
source/Q/pending ledger remains in-process until a versioned checkpoint lands).
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

EXPORT_VERSION = 1
ENVELOPE_KEYS = frozenset({"version", "session_epoch", "request_id", "snapshot"})
MAX_EXPORT_BYTES = 64_000_000  # Full local source + semantic history; never an RPC limit.


class ExportRefused(ValueError):
    """Closed refusal; never carries owner text."""


class ExportDurability(ExportRefused):
    """The new snapshot was replaced into place, but its directory fsync failed.

    The caller must treat durability as UNKNOWN for this path (the previous file
    is already gone); retry/export policy belongs to the caller.
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        super().__init__("export durability")


def _canonical(value) -> bytes:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExportRefused("export payload") from exc
    return (text + "\n").encode("utf-8")


def validate_envelope(value) -> dict:
    if not isinstance(value, dict) or set(value) != ENVELOPE_KEYS:
        raise ExportRefused("export envelope")
    if type(value["version"]) is not int or value["version"] != EXPORT_VERSION:
        raise ExportRefused("export version")
    for name in ("session_epoch", "request_id"):
        if not isinstance(value[name], str) or not value[name].strip() or len(value[name]) > 96:
            raise ExportRefused("export identity")
    if not isinstance(value["snapshot"], dict):
        raise ExportRefused("export snapshot")
    return value


def write_snapshot(path, payload: dict) -> Path:
    path = Path(path)
    validate_envelope(payload)
    raw = _canonical(payload)
    if len(raw) > MAX_EXPORT_BYTES:
        raise ExportRefused("export too large")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        temporary = None
        try:
            os.fsync(directory)
        except OSError as exc:
            raise ExportDurability(path) from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        os.close(directory)
    return path


def read_snapshot(path) -> dict:
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ExportRefused("export open") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EXPORT_BYTES:
            raise ExportRefused("export file")
        raw = b""
        while len(raw) <= info.st_size:
            block = os.read(fd, info.st_size + 1 - len(raw))
            if not block:
                break
            raw += block
    finally:
        os.close(fd)
    if len(raw) > MAX_EXPORT_BYTES:
        raise ExportRefused("export size")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ExportRefused("export json") from exc
    return validate_envelope(value)
