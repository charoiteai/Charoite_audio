"""Machine-readable state for the post-meeting pipeline.

The daemon exits before the expensive rebuild and graph update finish. This
module stays dependency-free so the daemon and tests can publish progress
without importing audio, STT, or diarization stacks.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import time
from typing import Any


SCHEMA_VERSION = 1
STATUS_DIR = "meeting-status"
STATUS_KEEP_DAYS = 14
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{4})")
_AUX_SUFFIXES = ("_minutes", "_hints", "_live", "_debrief")


def short_stamp(transcript: pathlib.Path) -> str:
    """Graph notes use the minute stamp even when live files include seconds."""
    match = _STAMP_RE.match(transcript.stem)
    return match.group(1) if match else transcript.stem


def find_final_transcript(original: pathlib.Path) -> pathlib.Path:
    """Return the transcript even if graph_updater renamed it to its title."""
    if original.exists():
        return original.resolve()
    stamp = short_stamp(original)
    candidates = []
    for path in original.parent.glob(f"{stamp}_*.md"):
        suffix = path.stem[len(stamp):]
        if any(aux in suffix for aux in _AUX_SUFFIXES):
            continue
        candidates.append(path)
    if not candidates:
        return original.resolve()
    return max(candidates, key=lambda path: path.stat().st_mtime).resolve()


def find_meeting_note(
    cfg: dict[str, Any],
    transcript: pathlib.Path,
    *,
    newer_than: float | None = None,
) -> pathlib.Path | None:
    """Find the exact note created in the configured graph or a project graph."""
    override = os.environ.get("SUFLER_GRAPH_DIR")
    raw = override or (cfg.get("sufler") or {}).get("graph_dir", "")
    if not raw:
        return None
    configured = pathlib.Path(raw).expanduser()
    stamp = short_stamp(transcript)
    roots = [configured]
    # graph_updater may route a meeting to ``configured.parent/<project>``.
    if not override:
        try:
            roots.extend(path for path in configured.parent.iterdir() if path.is_dir())
        except OSError:
            pass
    seen: set[pathlib.Path] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen:
            continue
        seen.add(resolved)
        note = root / "Встречи" / f"{stamp}.md"
        try:
            if note.is_file() and (newer_than is None or note.stat().st_mtime >= newer_than):
                return note.resolve()
        except OSError:
            continue
    return None


class MeetingStatusStore:
    """Atomically publish one small status document per meeting."""

    def __init__(self, root: pathlib.Path, *, now=time.time):
        self.root = pathlib.Path(root)
        self.directory = self.root / "logs" / STATUS_DIR
        self._now = now

    def processing(self, transcript: pathlib.Path, stage: str) -> pathlib.Path:
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "processing",
            "stage": stage,
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
        }
        self._prune(now)
        return self._write(transcript, payload)

    def ready(self, transcript: pathlib.Path, note: pathlib.Path) -> pathlib.Path:
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "ready",
            "stage": "complete",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
            "note_path": str(pathlib.Path(note).resolve()),
        }
        return self._write(transcript, payload)

    def failed(self, transcript: pathlib.Path, error: object) -> pathlib.Path:
        transcript = pathlib.Path(transcript)
        current = self._read(transcript)
        now = float(self._now())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "meeting_id": transcript.stem,
            "state": "error",
            "stage": "failed",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "transcript_path": str(find_final_transcript(transcript)),
            "error": str(error)[:2000],
        }
        return self._write(transcript, payload)

    def has_transcript(self, transcript: pathlib.Path) -> bool:
        return find_final_transcript(pathlib.Path(transcript)).is_file()

    def _path(self, transcript: pathlib.Path) -> pathlib.Path:
        safe = re.sub(r"[^\w.-]+", "_", pathlib.Path(transcript).stem)
        return self.directory / f"{safe}.json"

    def _read(self, transcript: pathlib.Path) -> dict[str, Any]:
        try:
            data = json.loads(self._path(transcript).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, transcript: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(transcript)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.directory)
        tmp = pathlib.Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    def _prune(self, now: float) -> None:
        if not self.directory.is_dir():
            return
        cutoff = now - STATUS_KEEP_DAYS * 86400
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue
