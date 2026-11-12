"""Typed non-model events for the grounded thread owner.

Channel events are not model facts: they carry their own kind and the captured
source boundary, are rendered as plain text and never go through the legacy
`MeetingThread.add_system` writer when the grounded backend is selected.
"""

from __future__ import annotations

from dataclasses import dataclass

CHANNEL_KINDS = frozenset({"channel_lost", "channel_back", "system"})
MAX_TEXT = 512


@dataclass(frozen=True)
class ChannelEvent:
    kind: str
    text: str
    at: str = ""
    position: int = 0

    def __post_init__(self) -> None:
        if self.kind not in CHANNEL_KINDS:
            raise ValueError("channel event kind")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text.encode("utf-8")) > MAX_TEXT:
            raise ValueError("channel event text")
        if type(self.position) is not int or self.position < 0:
            raise ValueError("channel event position")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "at": self.at, "position": self.position}
