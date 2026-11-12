"""Acknowledged ingestion of immutable accepted speech into an injected actor.

Display rendering is deliberately outside this boundary. The actor must provide
an atomic, idempotent append_source_at operation, including for archived epochs.
"""

from __future__ import annotations

import hashlib
import inspect
import threading
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transcript import AcceptedPortion


class AdapterRefused(ValueError):
    """Closed source-adapter refusal; never carries owner text."""


class AcceptedSource:
    """One epoch-bound cursor; failed deliveries retain their exact identity."""

    def __init__(self, actor, accessor: Callable[[int, int], tuple[AcceptedPortion, ...]], *,
                 session_epoch: int) -> None:
        if type(session_epoch) is not int or session_epoch < 1 or not callable(accessor):
            raise AdapterRefused("accepted source binding")
        if (not callable(getattr(actor, "append_source_at", None)) or
                not callable(getattr(actor, "bind_source", None))):
            raise AdapterRefused("atomic source actor required")
        source = getattr(accessor, "__self__", None)
        if (not inspect.ismethod(accessor) or source is None or
                accessor.__name__ != "accepted_portions" or
                accessor.__func__ is not getattr(type(source), "accepted_portions", None) or
                not callable(getattr(source, "accepted_cursor", None))):
            raise AdapterRefused("stable bound accepted_portions required")
        actor.bind_source(source, session_epoch=session_epoch)
        self._source = source
        self._actor = actor
        self._accessor = accessor
        self._epoch = session_epoch
        self._cursor = 0
        self._pending = None
        self._speaker_labels: dict[str, str] = {}
        self._lock = threading.RLock()
        self._boundary_cursor = 0
        self._boundary_bytes = 0

    @property
    def session_epoch(self) -> int:
        return self._epoch

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def speaker_labels(self) -> Mapping[str, str]:
        """Immutable snapshot of opaque IDs to capture-time display labels."""
        with self._lock:
            return MappingProxyType(dict(self._speaker_labels))

    def accepted_boundary(self) -> int:
        """Capture accepted body bytes, including speech not ingested yet.

        This derived cursor never acknowledges a portion. The immutable journal
        snapshot is the event's linearization point; display headers and mutable
        transcript markup do not participate in semantic source coordinates.
        """
        with self._lock:
            tail = self._source.accepted_cursor().portion
            if type(tail) is not int or tail < self._boundary_cursor:
                raise AdapterRefused("accepted journal regressed")
            rows = self._accessor(self._boundary_cursor, tail)
            if not isinstance(rows, tuple) or len(rows) != tail - self._boundary_cursor:
                raise AdapterRefused("accepted boundary snapshot shape")
            byte_count = self._boundary_bytes
            for index, row in enumerate(rows, self._boundary_cursor):
                if (type(getattr(row, "index", None)) is not int or row.index != index
                        or not isinstance(getattr(row, "body", None), str) or not row.body):
                    raise AdapterRefused("accepted boundary fields")
                try:
                    byte_count += len(row.body.encode("utf-8"))
                except UnicodeError as error:
                    raise AdapterRefused("accepted boundary unicode") from error
            self._boundary_cursor, self._boundary_bytes = tail, byte_count
            return byte_count

    def next(self):
        """Append one raw body; acknowledge only a successful actor receipt."""
        with self._lock:
            # The immutable journal is append-only; read at most one record.
            # EOF uses an empty valid range; truncation remains a range error.
            tail = self._source.accepted_cursor().portion
            if type(tail) is not int or tail < self._cursor:
                raise AdapterRefused("accepted journal regressed")
            snapshot = self._accessor(self._cursor, min(self._cursor + 1, tail))
            if not isinstance(snapshot, tuple):
                raise AdapterRefused("accepted snapshot shape")
            if not snapshot:
                if self._pending is not None:
                    raise AdapterRefused("accepted portion disappeared")
                return None
            raw = snapshot[0]
            try:
                identity = (raw.index, raw.body, raw.speaker, raw.stamp)
            except AttributeError as exc:
                raise AdapterRefused("accepted portion shape") from exc
            index, body, speaker, stamp = identity
            if (type(index) is not int or index != self._cursor or
                    not isinstance(body, str) or not body or
                    not isinstance(speaker, str) or not speaker.strip() or
                    not isinstance(stamp, str)):
                raise AdapterRefused("accepted portion fields")
            if self._pending is not None and identity != self._pending:
                raise AdapterRefused("accepted portion changed")
            self._pending = identity
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            speaker_id = "speaker:" + hashlib.sha256(speaker.encode("utf-8")).hexdigest()
            known_label = self._speaker_labels.get(speaker_id)
            if known_label is not None and known_label != speaker:
                raise AdapterRefused("speaker identity collision")
            self._speaker_labels[speaker_id] = speaker
            portion = self._actor.append_source_at(
                index, body, speaker_id, stamp, session_epoch=self._epoch,
                body_hash=digest, source=self._source, speaker_label=speaker,
            )
            if (portion.id, portion.session_epoch, portion.body_utf8, portion.speaker_id,
                    portion.timestamp, portion.body_hash) != (
                    f"p:{self._epoch}:{index}", self._epoch, body, speaker_id, stamp, digest):
                raise AdapterRefused("source receipt mismatch")
            self._cursor += 1
            self._pending = None
            return portion

    def stage_late_tail(self) -> int:
        """Stage every immutable row from cursor to tail for a late export.

        Used only after the export window has refused ACK: cursor and pending
        identity stay untouched, no row is acknowledged, and repeated rows are
        staged at most once by the actor's identity check.
        """
        from .actor_core import ActorStopped

        with self._lock:
            tail = self._source.accepted_cursor().portion
            if type(tail) is not int or tail < self._cursor:
                raise AdapterRefused("accepted journal regressed")
            staged = 0
            for index in range(self._cursor, tail):
                snapshot = self._accessor(index, index + 1)
                if not isinstance(snapshot, tuple) or len(snapshot) != 1:
                    raise AdapterRefused("accepted snapshot shape")
                raw = snapshot[0]
                try:
                    identity = (raw.index, raw.body, raw.speaker, raw.stamp)
                except AttributeError as exc:
                    raise AdapterRefused("accepted portion shape") from exc
                row_index, body, speaker, stamp = identity
                if (type(row_index) is not int or row_index != index or
                        not isinstance(body, str) or not body or
                        not isinstance(speaker, str) or not speaker.strip() or
                        not isinstance(stamp, str)):
                    raise AdapterRefused("accepted portion fields")
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                speaker_id = "speaker:" + hashlib.sha256(
                    speaker.encode("utf-8")).hexdigest()
                known_label = self._speaker_labels.get(speaker_id)
                if known_label is not None and known_label != speaker:
                    raise AdapterRefused("speaker identity collision")
                try:
                    self._actor.append_source_at(
                        index, body, speaker_id, stamp, session_epoch=self._epoch,
                        body_hash=digest, source=self._source, speaker_label=speaker,
                    )
                except ActorStopped:
                    staged += 1  # already/staged late; no ACK, cursor unchanged
            return staged

    def drain(self, *, limit: int = 8) -> list:
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise AdapterRefused("source drain bound")
        portions = []
        for _ in range(limit):
            portion = self.next()
            if portion is None:
                break
            portions.append(portion)
        return portions
