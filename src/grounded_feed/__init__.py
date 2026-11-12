"""Product package for the local sequential meeting feed (Task №859).

Default-off: when `sufler.live_feed` is false, `bridge.select` leaves the
classic `MeetingThread` path untouched. When `sufler.live_feed` is true,
accepted speech flows strictly locally through `AcceptedSource` -> `ActorCore`
-> `LocalFeedRunner` (loopback Ollama / mlx-server only, zero cloud egress).
"""

from __future__ import annotations

__all__ = [
    "accepted_source",
    "actor_core",
    "bridge",
    "events",
    "export",
    "local_runner",
    "publish",
    "render",
    "runtime",
    "semantic",
    "session",
    "source_actor",
    "thread",
]
__version__ = "0.1.0"
