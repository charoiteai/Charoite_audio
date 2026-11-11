"""Append-only grounded thread canvas for the №859 product prototype.

Model-free: blocks are committed from synthetic fixtures or from an already
compiled canonical grounded PreviewV1. No model call,
no tokenizer, no transport, no persistence. The canvas is the single owner of
the published order; blocks are immutable once accepted.
"""

from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass

KINDS = frozenset({"paragraph", "question", "answer", "system"})
ANSWER_STATUSES = frozenset({"partially_answered", "answered", "unknown"})
LEGACY_ANSWER_ID_PREFIX = "legacy-answer:"


def legacy_answer_id(qid: str) -> str:
    """Stable ID for the legacy scalar answer of one question (idempotent migration)."""
    return f"{LEGACY_ANSWER_ID_PREFIX}{qid}"


class CanvasRefused(ValueError):
    """Contract violation; closed reason, never model text."""


def _clean(text: str, *, limit: int = 8000, single_line: bool = False) -> str:
    if not isinstance(text, str):
        raise CanvasRefused("text_required")
    if not text.strip() or len(text.encode("utf-8")) > limit:
        raise CanvasRefused("text_bounds")
    if "\x00" in text:
        raise CanvasRefused("text_control")
    if single_line and any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in text):
        raise CanvasRefused("text_control")
    return text


@dataclass(frozen=True)
class Block:
    serial: int
    kind: str
    topic_id: str
    topic_label: str
    text: str
    position: int
    question_id: str | None = None
    system_after: bool = False
    manual_id: str | None = None
    # Typed answer identity (owner decision 859 §4): several distinct answer_id
    # values of one qid are stored in order; a repeated ID is idempotent.
    answer_id: str | None = None
    scope_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    status: str | None = None
    source_refs: tuple[str, ...] = ()

    @property
    def qid(self) -> str | None:
        """Owner-decision alias: the block's question id."""
        return self.question_id

    def to_dict(self) -> dict:
        return {"serial": self.serial, "kind": self.kind, "topic_id": self.topic_id,
                "topic_label": self.topic_label, "text": self.text,
                "position": self.position, "question_id": self.question_id,
                "system_after": self.system_after, "manual_id": self.manual_id,
                "answer_id": self.answer_id, "scope_ids": list(self.scope_ids),
                "claim_ids": list(self.claim_ids), "status": self.status,
                "source_refs": list(self.source_refs)}


def _ids(values, reason: str) -> tuple[str, ...]:
    """Bounded ordered ID list: exact strings, duplicates collapsed, never nulls."""
    if values is None:
        return ()
    if not isinstance(values, (list, tuple)):
        raise CanvasRefused(reason)
    ordered: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 96:
            raise CanvasRefused(reason)
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered)


class ThreadCanvas:
    """One owner. Append-only, no rewrites, deterministic ordering."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._blocks: list[Block] = []
        self._questions: set[str] = set()
        self._question_index: dict[str, Block] = {}
        self._answers_by_id: dict[str, Block] = {}   # answer_id -> immutable block
        self._answers_by_qid: dict[str, list[str]] = {}  # qid -> ordered answer_ids
        self._serial = 0
        self._version = 0
        self._seen_paragraphs: set[tuple[str, str]] = set()
        self._writer_state = threading.local()
        self._canonical = None

    def _canonical_blocks(self):
        return None if self._canonical is None else self._canonical.state.projection

    @property
    def version(self) -> int:
        with self._lock:
            return (self._version if self._canonical is None else self._canonical.state.version)

    def snapshot(self) -> tuple[Block, ...]:
        with self._lock:
            blocks = self._canonical_blocks()
            return tuple(self._blocks) if blocks is None else blocks

    def snapshot_versioned(self) -> tuple[int, tuple[Block, ...]]:
        """One atomic read: the version and its exact ordered blocks."""
        with self._lock:
            version = self.version
            ordered = tuple(sorted(self.snapshot(), key=lambda b: (b.position, b.system_after, b.serial)))
            return version, ordered

    def ordered(self) -> tuple[Block, ...]:
        # MeetingThread._render_feed rule: captured source boundary first, then
        # non-model events at an equal boundary (after speech), then append order.
        with self._lock:
            return tuple(sorted(self.snapshot(), key=lambda b: (b.position, b.system_after, b.serial)))

    def _require_writer(self) -> None:
        """An actor-owned canvas is writable only inside the actor write scope."""
        if getattr(self, "_actor_owner", None) is None:
            return
        if not getattr(self._writer_state, "depth", 0):
            raise CanvasRefused("actor_owned")

    @staticmethod
    def _validate_position(position: int) -> None:
        if type(position) is not int or position < 0:
            raise CanvasRefused("position")

    def _append(self, kind: str, topic_id: str, topic_label: str, text: str,
                position: int, question_id: str | None = None,
                system_after: bool = False, manual_id: str | None = None,
                answer_id: str | None = None, scope_ids: tuple[str, ...] = (),
                claim_ids: tuple[str, ...] = (), status: str | None = None,
                source_refs: tuple[str, ...] = ()) -> Block:
        self._require_writer()
        if kind not in KINDS:
            raise CanvasRefused("kind")
        if type(position) is not int or position < 0:
            raise CanvasRefused("position")
        with self._lock:
            self._require_writer()  # owner-check INSIDE the canvas lock: attach cannot race past it
            block = Block(self._serial, kind, topic_id, topic_label, _clean(text), position,
                          question_id, system_after, manual_id, answer_id if kind == "answer" else None,
                          scope_ids if kind == "answer" else (), claim_ids if kind == "answer" else (),
                          status if kind == "answer" else None,
                          source_refs if kind == "answer" else ())
            self._blocks.append(block)
            self._serial += 1
            self._version += 1
            return block

    def paragraph(self, topic_id: str, topic_label: str, text: str, *, position: int = 0) -> Block:
        self._require_writer()
        if not topic_id or not topic_label:
            raise CanvasRefused("topic_required")
        text = _clean(text)
        self._validate_position(position)
        key = (topic_id, " ".join(text.split()).lower())
        with self._lock:  # RLock: duplicate key and append must be one atomic step
            self._require_writer()
            if key in self._seen_paragraphs:
                raise CanvasRefused("duplicate_paragraph")
            self._seen_paragraphs.add(key)
            return self._append("paragraph", topic_id, topic_label, text, position)

    def question(self, topic_id: str, topic_label: str, text: str, *, position: int = 0,
                 manual_id: str | None = None) -> str:
        self._require_writer()
        text = _clean(text)
        self._validate_position(position)
        with self._lock:
            self._require_writer()
            qid = f"q{len(self._questions) + 1}"
            self._questions.add(qid)
            block = self._append("question", topic_id, topic_label, text, position, question_id=qid,
                                 manual_id=manual_id)
            self._question_index[qid] = block
            return qid

    def answer(self, qid: str, text: str, *, position: int = 0,
               manual_id: str | None = None, answer_id: str | None = None,
               scope_ids=(), claim_ids=(), status: str | None = None,
               source_refs=()) -> Block:
        """Add one answer block of a question (lossless multi-answer).

        A distinct answer_id is a distinct answer of the same qid: blocks are
        stored and rendered in arrival order. Repeating the same answer_id with
        the same payload is idempotent (returns the stored block, no new serial).
        A legacy scalar answer (no answer_id) gets one stable legacy ID and a
        conflicting rewrite is still refused.
        """
        self._require_writer()
        text = _clean(text)
        self._validate_position(position)
        if answer_id is not None and (not isinstance(answer_id, str) or not answer_id
                                      or len(answer_id) > 96):
            raise CanvasRefused("answer_id")
        if status is not None and (type(status) is not str or status not in ANSWER_STATUSES):
            raise CanvasRefused("answer_status")
        scope_ids = _ids(scope_ids, "answer_scope_ids")
        claim_ids = _ids(claim_ids, "answer_claim_ids")
        source_refs = _ids(source_refs, "answer_source_refs")
        with self._lock:
            self._require_writer()
            if qid not in self._questions:
                raise CanvasRefused("unknown_question")
            question = self._question_index.get(qid)
            if question is None:
                raise CanvasRefused("question_block")
            aid = legacy_answer_id(qid) if answer_id is None else answer_id
            existing = self._answers_by_id.get(aid)
            # The ID is globally unique: the owning question and manual provenance
            # are part of the identity, so reusing one ID elsewhere is a closed
            # conflict instead of silently dropping the second answer.
            payload = (qid, manual_id, text, scope_ids, claim_ids, status, source_refs)
            if existing is not None:
                if (existing.question_id, existing.manual_id, existing.text,
                        existing.scope_ids, existing.claim_ids, existing.status,
                        existing.source_refs) == payload:
                    return existing  # idempotent repeat of the same answer ID
                raise CanvasRefused("answered_question" if answer_id is None else "answer_conflict")
            block = self._append("answer", question.topic_id, question.topic_label, text,
                                 max(position, question.position), qid, manual_id=manual_id,
                                 answer_id=aid, scope_ids=scope_ids, claim_ids=claim_ids,
                                 status=status, source_refs=source_refs)
            self._answers_by_id[aid] = block
            self._answers_by_qid.setdefault(qid, []).append(aid)
            return block

    def system(self, text: str, *, position: int = 0) -> Block:
        self._require_writer()
        return self._append("system", "", "", text, position, system_after=True)

    def question_by_manual(self, manual_id: str) -> Block | None:
        with self._lock:
            return next((b for b in self.snapshot() if b.kind == "question" and b.manual_id == manual_id), None)

    def answer_by_manual(self, manual_id: str) -> Block | None:
        with self._lock:
            return next((b for b in self.snapshot() if b.kind == "answer" and b.manual_id == manual_id), None)

    def question_by_id(self, qid: str) -> Block | None:
        with self._lock:
            return next((b for b in self.snapshot() if b.kind == "question" and b.question_id == qid), None)

    def answer_by_id(self, answer_id: str) -> Block | None:
        with self._lock:
            return next((b for b in self.snapshot() if b.kind == "answer" and b.answer_id == answer_id), None)

    def answer_ids(self, qid: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(b.answer_id for b in self.snapshot() if b.kind == "answer" and b.question_id == qid)

    def is_open(self, qid: str) -> bool:
        with self._lock:
            if self._canonical is not None:
                question = next((q for q in self._canonical.state.questions if q.id == qid), None)
                return question is not None and not question.resolved
            return qid in self._questions and not self._answers_by_qid.get(qid)
