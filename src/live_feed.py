"""Feed policy and manual priority, without audio or owner configuration."""

from __future__ import annotations

import functools
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedPolicy:
    feed: bool
    auto_hint: bool
    auto_expand: bool
    instant: bool

    @classmethod
    def from_config(cls, cfg):
        sufler = cfg.get("sufler") or {}
        feed = bool(sufler.get("live_feed", False))
        return cls(feed, not feed and bool(sufler.get("auto_hint", True)),
                   not feed and bool(sufler.get("auto_expand", True)),
                   not feed and bool(sufler.get("instant", True)))


class ManualPriority:
    """Each waiting caller owns its registration; nobody clears another ask."""

    def __init__(self):
        self._owners = set()
        self._lock = threading.Lock()
        self._event = threading.Event()

    def set(self):
        with self._lock:
            self._owners.add(threading.get_ident())
            self._event.set()

    def clear(self):
        with self._lock:
            self._owners.discard(threading.get_ident())
            if not self._owners:
                self._event.clear()

    def is_set(self):
        return self._event.is_set()

    def requested(self, method):
        @functools.wraps(method)
        def request(*args, **kwargs):
            self.set()
            try:
                return method(*args, **kwargs)
            finally:
                self.clear()
        return request
