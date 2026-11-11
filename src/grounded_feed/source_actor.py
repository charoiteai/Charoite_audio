"""Production source actor for accepted speech (model-free contract only).

This is the product-owned subset of the test actor contract required by
AcceptedSource: one journal per epoch, idempotent indexed appends, opaque
speaker ids, body hashes and stable portion ids. Semantic state belongs to the canonical engine; this adapter validates the
accepted-source boundary without a second journal. Crash restore remains unavailable.
"""

from __future__ import annotations

import hashlib
from .semantic import ContractError, Portion, SemanticActor


class SourceRefused(ContractError):
    """Closed refusal; never carries owner text."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceActor:
    """Accepted-source validation view over the canonical immutable owner."""

    def __init__(self, semantic: SemanticActor | None = None) -> None:
        self.semantic = semantic if semantic is not None else SemanticActor()
        self._lock = self.semantic._lock
        self._bindings = self.semantic._source_bindings

    @property
    def _epoch(self):
        return self.semantic.state.session_epoch

    @property
    def _portions_list(self):
        return self.semantic.state.portions

    @property
    def _labels(self):
        return dict(self.semantic.state.speaker_labels)

    @property
    def _tail(self):
        return self.semantic.state.source_tail

    @property
    def session_epoch(self) -> int:
        return self._epoch

    @property
    def portion_count(self) -> int:
        """O(1) accepted count under the source lock (late-dedupe reads this)."""
        with self._lock:
            return len(self._portions_list)

    @property
    def portions(self) -> tuple[Portion, ...]:
        with self._lock:
            return tuple(self._portions_list)

    @property
    def source_tail(self) -> int:
        with self._lock:
            return self._tail

    def bind_source(self, source, *, session_epoch: int) -> None:
        try:
            self.semantic.bind_source(source, session_epoch=session_epoch)
        except ContractError as error:
            raise SourceRefused(str(error)) from error

    def _validate_fields(self, index: int, body: str, speaker_id: str, timestamp: str, *,
                         session_epoch: int, body_hash: str, speaker_label: str) -> None:
        if type(index) is not int or index < 0 or type(session_epoch) is not int or session_epoch < 1:
            raise SourceRefused("source fields")
        for value in (body, speaker_id, timestamp, body_hash, speaker_label):
            if not isinstance(value, str):
                raise SourceRefused("source json-native fields")
        if not body or body_hash != _hash(body):
            raise SourceRefused("source body")
        if not speaker_label.strip():
            raise SourceRefused("speaker label")
        expected = "speaker:" + _hash(speaker_label)
        if speaker_id != expected:
            raise SourceRefused("speaker label hash mismatch")
        if len(timestamp.encode("utf-8")) > 128:
            raise SourceRefused("timestamp")

    def validate_source_at(self, index: int, body: str, speaker_id: str, timestamp: str = "", *,
                           session_epoch: int, body_hash: str, source, speaker_label: str) -> None:
        """Validate a prospective portion without mutating (late rows must be authentic)."""
        self._validate_fields(index, body, speaker_id, timestamp,
                              session_epoch=session_epoch, body_hash=body_hash,
                              speaker_label=speaker_label)
        with self._lock:
            if source is None or self._bindings.get(session_epoch) is not source:
                raise SourceRefused("source epoch binding mismatch")
            if speaker_id in self._labels and self._labels[speaker_id] != speaker_label:
                raise SourceRefused("speaker label identity changed")
            if index < len(self._portions_list):
                portion = self._portions_list[index]
                if (portion.body_utf8, portion.speaker_id, portion.timestamp, portion.body_hash) != (
                        body, speaker_id, timestamp, body_hash):
                    raise SourceRefused("accepted source identity changed")
                return
            if index != len(self._portions_list):
                raise SourceRefused("nonmonotonic accepted source index")

    def validate_late_at(self, index: int, body: str, speaker_id: str, timestamp: str = "", *,
                         session_epoch: int, body_hash: str, source, speaker_label: str,
                         expected_index: int) -> None:
        """Validate a late row against the immutable prefix plus already staged late rows."""
        self._validate_fields(index, body, speaker_id, timestamp,
                              session_epoch=session_epoch, body_hash=body_hash,
                              speaker_label=speaker_label)
        with self._lock:
            if source is None or self._bindings.get(session_epoch) is not source:
                raise SourceRefused("source epoch binding mismatch")
            if speaker_id in self._labels and self._labels[speaker_id] != speaker_label:
                raise SourceRefused("speaker label identity changed")
            prefix = len(self._portions_list)
            if index < prefix:
                portion = self._portions_list[index]
                if (portion.body_utf8, portion.speaker_id, portion.timestamp, portion.body_hash) != (
                        body, speaker_id, timestamp, body_hash):
                    raise SourceRefused("accepted source identity changed")
                return
            if index == prefix:
                return  # first late row immediately after the immutable prefix
            if index != expected_index:
                raise SourceRefused("nonmonotonic late source index")

    def validate_late_authentic_at(self, index: int, body: str, speaker_id: str,
                                   timestamp: str = "", *, session_epoch: int,
                                   body_hash: str, source, speaker_label: str) -> None:
        """Authenticity checks only, for retry/conflict checks against staged late rows."""
        self._validate_fields(index, body, speaker_id, timestamp,
                              session_epoch=session_epoch, body_hash=body_hash,
                              speaker_label=speaker_label)
        with self._lock:
            if source is None or self._bindings.get(session_epoch) is not source:
                raise SourceRefused("source epoch binding mismatch")
            if speaker_id in self._labels and self._labels[speaker_id] != speaker_label:
                raise SourceRefused("speaker label identity changed")
            prefix = len(self._portions_list)
            if index < prefix:
                portion = self._portions_list[index]
                if (portion.body_utf8, portion.speaker_id, portion.timestamp, portion.body_hash) != (
                        body, speaker_id, timestamp, body_hash):
                    raise SourceRefused("accepted source identity changed")

    def append_source_at(self, index: int, body: str, speaker_id: str, timestamp: str = "", *,
                         session_epoch: int, body_hash: str, source, speaker_label: str) -> Portion:
        if type(index) is not int or index < 0 or type(session_epoch) is not int or session_epoch < 1:
            raise SourceRefused("source fields")
        if not body or not isinstance(body, str) or body_hash != _hash(body):
            raise SourceRefused("source body")
        if not isinstance(speaker_label, str) or not speaker_label.strip():
            raise SourceRefused("speaker label")
        expected = "speaker:" + _hash(speaker_label)
        if speaker_id != expected:
            raise SourceRefused("speaker label hash mismatch")
        if not isinstance(timestamp, str) or len(timestamp.encode("utf-8")) > 128:
            raise SourceRefused("timestamp")
        with self._lock:
            if source is None or self._bindings.get(session_epoch) is not source:
                raise SourceRefused("source epoch binding mismatch")
            if speaker_id in self._labels and self._labels[speaker_id] != speaker_label:
                raise SourceRefused("speaker label identity changed")
            if index < len(self._portions_list):
                portion = self._portions_list[index]
                if (portion.body_utf8, portion.speaker_id, portion.timestamp, portion.body_hash) != (
                        body, speaker_id, timestamp, body_hash):
                    raise SourceRefused("accepted source identity changed")
                return portion
            if index != len(self._portions_list):
                raise SourceRefused("nonmonotonic accepted source index")
            try:
                return self.semantic.append_source_at(
                    index, body, speaker_id, timestamp, session_epoch=session_epoch,
                    body_hash=body_hash, source=source, speaker_label=speaker_label)
            except ContractError as error:
                raise SourceRefused(str(error)) from error

    def export(self) -> dict:
        with self._lock:
            return {"version": 1, "session_epoch": self._epoch,
                    "source_tail": self._tail,
                    "portions": [{"id": p.id, "session_epoch": p.session_epoch,
                                  "body_utf8": p.body_utf8, "speaker_id": p.speaker_id,
                                  "timestamp": p.timestamp, "body_hash": p.body_hash,
                                  "source_tail": p.source_tail} for p in self._portions_list],
                    "speaker_labels": dict(self._labels),
                    "durability": "in_process_only; no crash restore"}
