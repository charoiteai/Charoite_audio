"""Daemon binding for the canonical feed owner and source cutoff.

No model, socket, owner configuration or synthetic authorization is created here.
"""

from __future__ import annotations

import threading

from .actor_core import ActorCore


class SourceProducerCutoff:
    """Acknowledge retirement only after the bounded producer callback succeeds."""

    def __init__(self, cancel_and_join) -> None:
        if not callable(cancel_and_join):
            raise ValueError("source cutoff callback required")
        self._callback = cancel_and_join
        self._lock = threading.Lock()
        self._confirmed = False

    @property
    def shutdown_confirmed(self) -> bool:
        with self._lock:
            return self._confirmed

    def cancel_and_join(self, timeout_s: float) -> bool:
        with self._lock:
            if self._confirmed:
                return True
            result = self._callback(timeout_s)
            self._confirmed = result is True
            return self._confirmed


def bind_product_source(session, transcript, source_worker, *,
                        source_only_notice: bool = True,
                        local_product_authority: bool = False) -> ActorCore:
    """Bind the producer before its journal, using the actual daemon seam."""
    if session.closed or session._actor is not None or session._accepted_source is not None:
        raise ValueError("session source already bound or closed")
    if not callable(getattr(source_worker, "cancel_and_join", None)):
        raise ValueError("source cutoff interface required")
    actor = ActorCore(
        canvas=session._canvas,
        session_epoch=1,
        local_product_authority=local_product_authority is True,
    )
    actor.attach_source_worker(source_worker)
    session.attach_actor(actor)
    session.attach_transcript(transcript, source_only_notice=source_only_notice)
    return actor
