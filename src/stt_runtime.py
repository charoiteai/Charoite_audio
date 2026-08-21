"""Small, deterministic policies for keeping live STT ahead of audio.

The runtime code lives in ``daemon.py``; only the decision is here so its
hysteresis can be tested without loading audio devices or inference models.
"""
from __future__ import annotations

import math


def should_shed_diarization(*, backlog_seconds: float, active: bool,
                            chunk_seconds: float) -> bool:
    """Whether live positional diarization should yield to transcription.

    Enter after two chunks (never below six seconds) and recover only after
    the queue falls below half a chunk.  Hysteresis prevents switching modes
    on every pull.  Non-finite telemetry fails safe toward the cheaper path.
    The recording on disk is independent and remains untouched.
    """
    if not math.isfinite(backlog_seconds):
        return True
    backlog_seconds = max(0.0, backlog_seconds)
    chunk_seconds = max(0.1, chunk_seconds)
    enter = max(6.0, 2.0 * chunk_seconds)
    recover = max(1.0, 0.5 * chunk_seconds)
    if active:
        return backlog_seconds > recover
    return backlog_seconds >= enter


def use_positional_split(*, lagging: bool, has_split: bool) -> bool:
    """Whether this chunk goes through live positional diarization.

    Kept as a pure function so both branches of the daemon's job planning
    are pinned by behavioural tests: a survived ``and``/``or`` mutation in
    the inline condition meant "diarization silently off forever" while
    every string-based assertion stayed green (review 21.08, GLM).
    """
    return has_split and not lagging
