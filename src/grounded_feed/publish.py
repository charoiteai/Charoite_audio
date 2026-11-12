"""Serial NDJSON publisher for the Mac UI meeting-feed contract.

Contract (consumed by SuflerService.consume):
  {"type":"feed_mode","enabled":true}
  {"type":"thread","text":"<rendered panel>","live_feed":true,"blocks":[...]}
  {"type":"hint","text":"...","manual":true} / {"type":"hint_done","manual":true}
  {"type":"status","text":"..."}
One JSON object per line, UTF-8, flushed. A late/older snapshot can never
overwrite a newer one (monotonic version guard). While paused the canvas keeps
the owner state but no thread frame is emitted; resume publishes the pending
snapshot once.
"""

from __future__ import annotations

import json
import threading


class NdjsonPublisher:
    def __init__(self, emit, *, live_feed: bool = True) -> None:
        self._emit = emit
        self._lock = threading.RLock()
        self.live_feed = live_feed
        self._last_version = -1
        self._paused = False
        self._pending = None  # (version, text)
        self._pending_version = -1
        self._pending_questions = None
        self._pending_blocks = None
        self._pending_source = None

    def _write(self, event: dict) -> bool:
        """Return False only when the sink explicitly reports non-delivery."""
        result = self._emit(dict(event))
        if result is False:
            return False
        return True

    def feed_mode(self, enabled: bool) -> None:
        self.live_feed = bool(enabled)
        self._write({"type": "feed_mode", "enabled": bool(enabled)})

    def status(self, text: str) -> None:
        self._write({"type": "status", "text": text})

    def hint(self, text: str, *, manual: bool, manual_id: str | None = None) -> None:
        frame = {"type": "hint", "text": text, "manual": bool(manual)}
        if manual_id is not None:
            frame["manual_id"] = manual_id
        self._write(frame)

    def hint_done(self, *, manual: bool, manual_id: str | None = None) -> None:
        frame = {"type": "hint_done", "manual": bool(manual)}
        if manual_id is not None:
            frame["manual_id"] = manual_id
        self._write(frame)

    def manual(self, manual_id: str, state: str, text: str) -> bool:
        """Return False only when the sink explicitly reports non-delivery."""
        if state not in ("question", "answer"):
            raise ValueError("manual state")
        return self._write({"type": "manual", "manual_id": manual_id,
                            "state": state, "text": text})

    def publish(self, text: str, version: int, *, questions=None, blocks=None, source=None) -> bool:
        if type(version) is not int or version < 0:
            raise ValueError("version")
        with self._lock:
            if version <= self._last_version:
                return False  # stale render, never overwrite a newer snapshot
            if self._paused:
                if version <= self._pending_version:
                    return False  # a pending render is immutable: same/older never replaces it
                self._pending = (version, text)
                self._pending_version = version
                self._pending_questions = list(questions or [])
                self._pending_blocks = None if blocks is None else list(blocks)
                self._pending_source = source
                return False
            frame = {"type": "thread", "text": text, "live_feed": self.live_feed}
            if blocks is not None:
                frame["blocks"] = list(blocks)
            if source is not None:
                frame["source"] = dict(source)
            if questions is not None:
                frame["questions"] = list(questions)
            try:
                if not self._write(frame):
                    return False  # sink refused delivery: no version marker
            except Exception:  # noqa: BLE001 - not delivered: keep no version marker
                return False
            self._last_version = version
            return True

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> bool:
        with self._lock:
            if not self._paused:
                return False
            if self._pending is None:
                self._paused = False
                return False
            version, text = self._pending
            questions = self._pending_questions
            blocks = self._pending_blocks
            source = self._pending_source
            if version <= self._last_version:
                return False
            frame = {"type": "thread", "text": text, "live_feed": self.live_feed}
            if blocks is not None:
                frame["blocks"] = list(blocks)
            if source is not None:
                frame["source"] = dict(source)
            if questions is not None:
                frame["questions"] = list(questions)
            try:
                if not self._write(frame):
                    return False  # still paused: pending preserved
            except Exception:  # noqa: BLE001 - still paused: pending preserved
                return False
            self._paused = False
            self._last_version = version
            self._pending = None
            self._pending_questions = None
            self._pending_blocks = None
            self._pending_source = None
            return True

    @property
    def paused(self) -> bool:
        return self._paused


def ndjson_line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
