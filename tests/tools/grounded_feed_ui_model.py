"""Model-free consumer mirroring the Mac UI rules for the grounded feed tests.

Not production code: it reproduces `SuflerService.consume` + `SuflerView` follow
rules + `MeetingFeedScroll` tolerance so the NDJSON frames can be checked without
Swift/window: thread frames replace the panel, but the reader is moved to the
bottom only when already there and no manual card is open; a manual request jumps
to the card; Stop keeps the last thread; a new meeting resets scroll/session.
"""

from __future__ import annotations

import json

FROM_BOTTOM_TOLERANCE = 48.0


def render_blocks_frame(blocks) -> str:
    """Render the atomic typed projection exactly like the future block Swift view."""
    parts, shown = [], ""
    for row in blocks:
        if row.get("kind") == "system":
            parts.append(row.get("text", ""))
            continue
        topic = row.get("topic_id") or ""
        if topic and topic != shown and row.get("topic_label"):
            parts.append(row["topic_label"])
            shown = topic
        parts.append(row.get("text", ""))
    return "\n\n".join(parts)


class MacFeedConsumer:
    def __init__(self) -> None:
        self.buffer = ""
        self.live_feed = False
        self.thread = ""
        self.blocks = []
        self.hint = ""
        self.hint_is_manual = False
        self.manual_card_open = False
        self.manual_question = ""
        self.manual_request_epoch = 0
        self.near_bottom = True
        self.is_user_scrolling = False
        self.revision = 0
        self.followed = 0
        self.thread_follows: list[bool] = []
        self.thread_records: list[dict] = []
        self.scroll_targets: list[str] = []
        self.events: list[dict] = []
        self.questions: list[dict] = []
        self.blocks: list[dict] = []
        self.stopped = False

    # ---- scroll ----------------------------------------------------------
    def user_scroll_up(self) -> None:
        self.is_user_scrolling = False
        self.revision += 1
        self.near_bottom = False

    def user_scroll_to_bottom(self) -> None:
        self.is_user_scrolling = False
        self.revision += 1
        self.near_bottom = True

    def observe_geometry(self, *, content_height: float, offset: float, viewport: float) -> bool:
        bottom = max(0.0, content_height - offset - viewport)
        before = self.near_bottom
        self.near_bottom = bottom <= FROM_BOTTOM_TOLERANCE and bottom >= -FROM_BOTTOM_TOLERANCE
        if before != self.near_bottom:
            self.revision += 1
        return self.near_bottom

    def follows_update(self) -> bool:
        return self.live_feed and self.near_bottom and not self.is_user_scrolling and not self.manual_card_open

    # ---- wire ------------------------------------------------------------
    def consume(self, data: bytes | str) -> list[dict]:
        chunk = data.decode("utf-8") if isinstance(data, bytes) else data
        self.buffer += chunk
        parsed: list[dict] = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
                continue
            parsed.append(obj)
            self.events.append(obj)
            self._apply(obj)
        return parsed

    def _apply(self, obj: dict) -> None:
        kind = obj["type"]
        text = obj.get("text") if isinstance(obj.get("text"), str) else ""
        if kind == "feed_mode":
            self.live_feed = bool(obj.get("enabled"))
        elif kind == "thread":
            self.live_feed = bool(obj.get("live_feed", self.live_feed))
            if isinstance(obj.get("blocks"), list) and obj["blocks"]:
                self.blocks = list(obj["blocks"])
                self.thread = render_blocks_frame(self.blocks)
            else:
                self.thread = text
            if isinstance(obj.get("questions"), list):
                self.questions = list(obj["questions"])
            followed = self.follows_update()
            self.thread_follows.append(followed)
            self.thread_records.append({"followed": followed, "near_bottom": self.near_bottom,
                                        "manual_card_open": self.manual_card_open,
                                        "user_scrolling": self.is_user_scrolling})
            if followed:
                self.followed += 1
                self.scroll_targets.append("hintBottom")
        elif kind == "hint":
            self.hint = text
            self.hint_is_manual = bool(obj.get("manual"))
            if self.hint_is_manual:
                self.manual_card_open = True
                self.manual_request_epoch += 1
                self.scroll_targets.append("hintTop")
        elif kind == "hint_done":
            if self.hint_is_manual:
                self.hint = ""
                self.manual_card_open = False
        elif kind == "autostop":
            self.stopped = True

    # ---- lifecycle -------------------------------------------------------
    def ask_manual(self, question: str) -> None:
        self.manual_question = question

    def stop_meeting(self) -> None:
        self.stopped = True

    def reset_for_new_meeting(self) -> None:
        self.__init__()
