"""Default-off activation seam between the daemon and the local grounded feed.

`daemon.py` calls `select()` once after parsing `FeedPolicy`. When `policy.feed`
is false, classic mode (`LEGACY`) runs unchanged. When `policy.feed` is true,
`GROUNDED` constructs `GroundedFeedSession` and disables competing classic hint
and archive-expansion writers.
"""

from __future__ import annotations

from dataclasses import dataclass

LEGACY, GROUNDED = "legacy", "grounded"


@dataclass(frozen=True)
class Activation:
    mode: str
    feed_on: bool
    auto_hint: bool
    auto_expand: bool
    session: object | None
    reason: str = ""


def select(policy, emit) -> Activation:
    """Resolve the feed mode from `FeedPolicy` without touching owner storage."""
    if bool(policy.feed):
        from .session import GroundedFeedSession

        session = GroundedFeedSession(emit, interval_s=0.5)
        return Activation(GROUNDED, True, False, False, session, reason="local_product")
    return Activation(LEGACY, False, bool(policy.auto_hint), bool(policy.auto_expand), None)
