"""Canonical immutable source and single-pass local speech state for the grounded feed.

Enforces provenance, UTF-8/identifier bounds, monotonic speech cursors, and a
single in-process atomic state owner (`SemanticActor`) whose derived `Block`
projection is prepared before swapping `self._state`.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import Callable


class ContractError(ValueError):
    """Untrusted data does not satisfy the closed contract."""


def _hash(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _integer(value, low=0):
    if type(value) is not int or value < low:
        raise ContractError("strict integer required")
    return value


def _string(value, limit=640, *, nonempty=True, single_line=True):
    if not isinstance(value, str):
        raise ContractError("string required")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContractError("invalid Unicode") from exc
    if size > limit or (nonempty and not value.strip()):
        raise ContractError("empty or oversized string")
    if single_line and any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in value):
        raise ContractError("control or newline in surface")
    return value


def _identifier(value):
    _string(value, 128)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ContractError("invalid identifier")
    return value


def _list(value, maximum, *, nonempty=False):
    if not isinstance(value, list) or len(value) > maximum or (nonempty and not value):
        raise ContractError("invalid bounded list")
    return value


@dataclass(frozen=True)
class Portion:
    session_epoch: int
    id: str
    body_utf8: str
    speaker_id: str
    timestamp: str
    body_hash: str
    position: int

    @property
    def source_tail(self):
        """Accepted-source compatibility: byte position at the start of this row."""
        return self.position


@dataclass(frozen=True)
class SourceUnit:
    id: str
    portion_id: str
    start: int
    end: int
    body_utf8: str
    body_hash: str
    speaker_id: str
    timestamp: str
    position: int
    artificial_start: bool
    artificial_end: bool

    def to_dict(self):
        return dict(self.__dict__)


def speech_cursor_to_byte(portions: tuple[Portion, ...], portion_index: int, character_offset: int) -> int:
    """Explicit adapter: SpeechCursor character offsets are NOT UTF-8 byte offsets."""
    _integer(portion_index)
    _integer(character_offset)
    if portion_index == len(portions) and character_offset == 0:
        return sum(len(p.body_utf8.encode("utf-8")) for p in portions)
    if portion_index >= len(portions) or character_offset > len(portions[portion_index].body_utf8):
        raise ContractError("character cursor out of bounds")
    return portions[portion_index].position + len(portions[portion_index].body_utf8[:character_offset].encode("utf-8"))


def _units(portion: Portion) -> tuple[SourceUnit, ...]:
    raw = portion.body_utf8.encode("utf-8")
    result, start = [], 0
    while start < len(raw):
        end = min(start + 1024, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        result.append(SourceUnit(
            f"u:{portion.id}:{start}:{end}", portion.id, start, end,
            raw[start:end].decode("utf-8"), portion.body_hash, portion.speaker_id,
            portion.timestamp, portion.position + start, start > 0, end < len(raw)))
        start = end
    return tuple(result)


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    topic: str
    source_refs: tuple[str, ...]
    source_hash: str
    version: int
    scopes: tuple[tuple[str, str, str], ...]
    partition_supported: bool
    answer_ids: tuple[str, ...] = ()
    source_units: tuple[SourceUnit, ...] = ()

    @property
    def resolved(self):
        return self.partition_supported and bool(self.scopes) and all(s[2] == "answered" for s in self.scopes)

    def to_dict(self):
        return {"id": self.id, "text": self.text, "topic": self.topic, "source_refs": list(self.source_refs),
                "source_hash": self.source_hash, "version": self.version,
                "scopes": [{"id": i, "text": t, "status": s} for i, t, s in self.scopes],
                "partition_supported": self.partition_supported, "resolved": self.resolved,
                "answer_ids": list(self.answer_ids), "source_units": [u.to_dict() for u in self.source_units]}


@dataclass(frozen=True)
class Pending:
    unit_id: str
    reason: str
    blocked_at: tuple[int, int, int, str]


@dataclass(frozen=True)
class SemanticState:
    session_epoch: int
    version: int = 0
    q_version: int = 0
    manual_generation: int = 0
    portions: tuple[Portion, ...] = ()
    speaker_labels: tuple[tuple[str, str], ...] = ()
    source_tail: int = 0
    questions: tuple[Question, ...] = ()
    answers_json: tuple[str, ...] = ()
    pending: tuple[Pending, ...] = ()
    stopped: bool = False
    paused: bool = False
    local_events_json: tuple[str, ...] = ()
    local_speech_events_json: tuple[str, ...] = ()
    local_speech_window_ids: tuple[str, ...] = ()
    local_speech_cursor: tuple[int, int] = (0, 0)
    local_speech_cursor_byte: int = 0
    local_topic_labels: tuple[tuple[str, str], ...] = ()
    projection: tuple = ()
    publication_order: tuple[str, ...] = ()


ShadowState = SemanticState


class SemanticActor:
    """One lock and immutable state swap; in-process ownership, not persistence."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, lock=None,
                 projection=None, local_product_authority: bool = False):
        self._lock = lock if lock is not None else threading.RLock()
        self._projection = projection
        self._clock = clock
        self._local_product_authority = local_product_authority is True
        self._state = SemanticState(1)
        self._archives: dict[int, SemanticState] = {}
        self._source_bindings: dict[int, object] = {}

    @property
    def state(self):
        with self._lock:
            return self._state

    def append_source(self, body: str, speaker_id: str, timestamp: str = "", *, session_epoch=None) -> Portion:
        _string(body, 10_000_000, single_line=False)
        _identifier(speaker_id)
        _string(timestamp, 128, nonempty=False)
        with self._lock:
            epoch = self._state.session_epoch if session_epoch is None else _integer(session_epoch, 1)
            if epoch in self._source_bindings:
                raise ContractError("indexed append required for bound source epoch")
            return self._append_source_locked(body, speaker_id, timestamp, epoch)

    def _append_source_locked(self, body, speaker_id, timestamp, epoch, *, speaker_label=None):
        """Caller holds the actor lock and has checked source ownership."""
        state = self._state if epoch == self._state.session_epoch else self._archives.get(epoch)
        if state is None:
            raise ContractError("unknown source session")
        p = Portion(epoch, f"p:{epoch}:{len(state.portions)}", body, speaker_id, timestamp, _hash(body), state.source_tail)
        labels = dict(state.speaker_labels)
        if speaker_label is not None:
            labels[speaker_id] = speaker_label
        state = replace(state, portions=state.portions + (p,), speaker_labels=tuple(labels.items()),
                        source_tail=state.source_tail + len(body.encode("utf-8")))
        if state.stopped:
            pending = tuple(
                Pending(u.id, "stopped_tail", (state.source_tail, state.q_version, state.manual_generation, "stopped"))
                for u in _units(p)
            )
            state = replace(state, pending=state.pending + pending)
        if epoch == self._state.session_epoch:
            self._state = state
        else:
            self._archives[epoch] = state
        return p

    def bind_source(self, source, *, session_epoch: int) -> None:
        """One journal per epoch and one epoch per journal, retained on reset."""
        _integer(session_epoch, 1)
        with self._lock:
            bound = self._source_bindings.get(session_epoch)
            if bound is not None:
                if bound is not source:
                    raise ContractError("source epoch already bound")
                return
            if any(existing is source for existing in self._source_bindings.values()):
                raise ContractError("source already belongs to another epoch")
            if session_epoch != self._state.session_epoch:
                raise ContractError("source epoch must be current for first binding")
            if (self._state.portions or self._state.source_tail or
                    self._state.pending or self._state.speaker_labels or
                    self._state.local_speech_events_json or self._state.local_speech_cursor_byte or
                    self._state.local_speech_cursor != (0, 0)):
                raise ContractError("source epoch must be pristine for first binding")
            self._source_bindings[session_epoch] = source

    def append_source_at(self, index: int, body: str, speaker_id: str, timestamp: str = "", *,
                         session_epoch: int, body_hash: str, source, speaker_label: str) -> Portion:
        """Atomically append or verify a retry, without crossing session epochs."""
        _integer(index, 0)
        _integer(session_epoch, 1)
        _string(body, 10_000_000, single_line=False)
        _identifier(speaker_id)
        _string(timestamp, 128, nonempty=False)
        _string(speaker_label, 10000, single_line=False)
        if body_hash != _hash(body):
            raise ContractError("source body hash mismatch")
        with self._lock:
            if self._source_bindings.get(session_epoch) is not source or source is None:
                raise ContractError("source epoch binding mismatch")
            state = (self._state if session_epoch == self._state.session_epoch
                     else self._archives.get(session_epoch))
            if state is None:
                raise ContractError("unknown source session")
            if speaker_id != "speaker:" + _hash(speaker_label):
                raise ContractError("speaker label hash mismatch")
            known_label = dict(state.speaker_labels).get(speaker_id)
            if known_label is not None and known_label != speaker_label:
                raise ContractError("speaker label identity changed")
            if index < len(state.portions):
                portion = state.portions[index]
                if (portion.body_utf8, portion.speaker_id, portion.timestamp, portion.body_hash) != (
                        body, speaker_id, timestamp, body_hash):
                    raise ContractError("accepted source identity changed")
                return portion
            if index != len(state.portions):
                raise ContractError("nonmonotonic accepted source index")
            return self._append_source_locked(body, speaker_id, timestamp, session_epoch,
                                              speaker_label=speaker_label)

    def _publish_state(self, state):
        """Prepare an immutable projection before the single canonical swap."""
        before = self._state
        order = list(state.publication_order)
        known = set(order)
        keys = [f"local:{i}" for i in range(len(state.local_events_json))]
        keys.extend(f"local_speech:{i}" for i in range(len(state.local_speech_events_json)))
        for key in keys:
            if key not in known:
                order.append(key)
                known.add(key)
        state = replace(state, publication_order=tuple(order))
        if self._projection is not None:
            state = replace(state, projection=self._projection(state))
        if self._state is not before:
            raise ContractError("state changed during projection")
        self._state = state

    def record_local_event(self, event, *, question=None):
        """Store owner/manual/system data without inferring semantic scope completion."""
        if not isinstance(event, dict) or event.get("kind") not in {"question", "answer", "system"}:
            raise ContractError("local event kind")
        _string(event.get("text"), 8000, single_line=False)
        _integer(event.get("position"))
        if event["kind"] != "system" and not event.get("manual_id"):
            raise ContractError("local manual provenance required")
        with self._lock:
            if self._state.stopped:
                raise ContractError("local event after stop")
            s = self._state
            qmap = {q.id: q for q in s.questions}
            answers = s.answers_json
            changes_question = False
            if question is not None:
                if event["kind"] != "question" or question.id in qmap or question.id != event.get("question_id"):
                    raise ContractError("local question identity")
                qmap[question.id] = question
                changes_question = True
            elif event["kind"] == "question":
                raise ContractError("local question ledger required")
            if event["kind"] == "answer":
                qid, aid = event.get("question_id"), event.get("answer_id")
                if qid not in qmap or not isinstance(aid, str) or not aid:
                    raise ContractError("local answer identity")
                q = qmap[qid]
                if aid in q.answer_ids:
                    raise ContractError("local answer already recorded")
                qmap[qid] = replace(q, version=q.version + 1, answer_ids=q.answer_ids + (aid,))
                answers += (_json({"id": aid, "question_id": qid, "text": event["text"],
                                   "scope_ids": [], "claim_ids": [], "status": "unknown",
                                   "origin": "manual", "manual_id": event["manual_id"]}),)
                changes_question = True
            state = replace(s, version=s.version + 1,
                            manual_generation=s.manual_generation + bool(event.get("manual_id")),
                            q_version=s.q_version + changes_question, questions=tuple(qmap.values()),
                            answers_json=answers, local_events_json=s.local_events_json + (_json(event),))
            self._publish_state(state)

    @staticmethod
    def _norm_speech_surface(text: str) -> str:
        collapsed = re.sub(r"\s+", " ", text).strip(" \t\r\n?.!,:;«»\"'—–-")
        return collapsed.lower()

    @classmethod
    def _clean_speech_topic(cls, raw_topic: str, topic_labels: dict[str, str]) -> tuple[str, str]:
        if not isinstance(raw_topic, str):
            raise ContractError("topic label must be a string")
        cleaned = "".join(
            " " if unicodedata.category(ch).startswith("C") or ch in "\u2028\u2029" else ch
            for ch in raw_topic
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            raise ContractError("empty topic label")
        if len(cleaned) > 240:
            cleaned = cleaned[:240].rstrip()
        while len(cleaned.encode("utf-8")) > 960:
            cleaned = cleaned[:-1].rstrip()
        _string(cleaned, 960, nonempty=True, single_line=True)
        norm_key = cls._norm_speech_surface(cleaned) or cleaned.lower()
        topic_id = "topic:" + _hash(norm_key)[:16]
        if topic_id in topic_labels:
            return topic_id, topic_labels[topic_id]
        topic_labels[topic_id] = cleaned
        return topic_id, cleaned

    def commit_local_speech_batch(self, authority, *, window_id: str, items: list,
                                  expected_cursor: tuple[int, int],
                                  next_cursor: tuple[int, int]) -> dict:
        """Atomically admit one validated single-pass local speech window."""
        from .local_runner import LocalLoopbackAuthority

        if not self._local_product_authority:
            raise ContractError("local speech authority not enabled on this actor")
        if not isinstance(authority, LocalLoopbackAuthority) or not authority.verified:
            raise ContractError("local loopback authority required")
        _identifier(window_id)
        if (not isinstance(expected_cursor, (tuple, list)) or len(expected_cursor) != 2
                or not isinstance(next_cursor, (tuple, list)) or len(next_cursor) != 2):
            raise ContractError("speech cursor pair required")
        exp_c = (_integer(expected_cursor[0], 0), _integer(expected_cursor[1], 0))
        nxt_c = (_integer(next_cursor[0], 0), _integer(next_cursor[1], 0))
        _list(items, 32)
        with self._lock:
            s = self._state
            if s.stopped:
                raise ContractError("local speech commit after stop")
            if s.paused:
                raise ContractError("local speech commit while paused")
            if window_id in s.local_speech_window_ids:
                return {"committed": False, "idempotent": True, "added_blocks": 0,
                        "cursor": s.local_speech_cursor, "cursor_byte": s.local_speech_cursor_byte}
            if exp_c != s.local_speech_cursor:
                raise ContractError("stale local speech cursor")
            start_byte = speech_cursor_to_byte(s.portions, exp_c[0], exp_c[1])
            next_byte = speech_cursor_to_byte(s.portions, nxt_c[0], nxt_c[1])
            if start_byte != s.local_speech_cursor_byte or next_byte <= start_byte:
                raise ContractError("nonmonotonic local speech byte window")
            window_pos = min(next_byte - 1, 1_000_000)

            topic_labels = dict(s.local_topic_labels)
            qmap = {q.id: q for q in s.questions}
            prior_events = [json.loads(raw) for raw in s.local_speech_events_json]
            speech_q_events = {ev["question_id"]: ev for ev in prior_events if ev["kind"] == "question"}
            answers_by_qid: dict[str, set[str]] = {}
            for ev in prior_events:
                if ev["kind"] == "answer" and ev.get("question_id"):
                    answers_by_qid.setdefault(ev["question_id"], set()).add(
                        self._norm_speech_surface(ev["text"]))
            seen_paragraphs = {
                (ev["topic_id"], " ".join(ev["text"].split()).lower())
                for ev in prior_events if ev["kind"] == "paragraph"
            }
            questions_by_norm = {
                self._norm_speech_surface(ev["text"]): ev["question_id"]
                for ev in prior_events
                if ev["kind"] == "question" and self._norm_speech_surface(ev["text"])
            }
            next_q_num = len(speech_q_events) + 1
            new_events: list[str] = []
            new_answers_json: list[str] = list(s.answers_json)
            added_blocks = 0
            q_changed = False

            def _record_answer(target_qid: str, raw_ans: str) -> None:
                nonlocal added_blocks, q_changed
                if target_qid not in speech_q_events or target_qid not in qmap:
                    raise ContractError("answers_q references unknown speech question")
                atext = _string(raw_ans.strip(), 4000, single_line=False)
                anorm = self._norm_speech_surface(atext)
                if anorm and anorm in answers_by_qid.get(target_qid, set()):
                    return
                q_ev = speech_q_events[target_qid]
                q = qmap[target_qid]
                aid = f"ans:{target_qid}:{len(q.answer_ids)}"
                ans_pos = max(window_pos, int(q_ev["position"]))
                a_ev = {
                    "kind": "answer",
                    "topic_id": q_ev["topic_id"],
                    "topic_label": q_ev["topic_label"],
                    "text": atext,
                    "position": ans_pos,
                    "question_id": target_qid,
                    "answer_id": aid,
                    "scope_ids": [f"scope:{target_qid}:0"],
                    "status": "answered",
                    "origin": "local_speech",
                    "admission_mode": "local_singlepass",
                    "window_id": window_id,
                }
                new_events.append(_json(a_ev))
                if anorm:
                    answers_by_qid.setdefault(target_qid, set()).add(anorm)
                qmap[target_qid] = replace(
                    q, version=q.version + 1,
                    scopes=((f"scope:{target_qid}:0", q.text, "answered"),),
                    answer_ids=q.answer_ids + (aid,),
                )
                new_answers_json.append(_json({
                    "id": aid, "question_id": target_qid, "text": atext,
                    "scope_ids": [f"scope:{target_qid}:0"], "claim_ids": [],
                    "status": "answered", "origin": "local_speech",
                    "window_id": window_id,
                }))
                added_blocks += 1
                q_changed = True

            for item in items:
                if isinstance(item, dict):
                    allowed = {"topic", "text", "question", "answers_q", "answer"}
                    if set(item) - allowed:
                        raise ContractError("unexpected local speech item fields")
                    raw_topic = item.get("topic", "")
                    raw_text = item.get("text", "")
                    raw_question = item.get("question", "")
                    raw_answers_q = item.get("answers_q", "")
                    raw_answer = item.get("answer", "")
                else:
                    raw_topic = getattr(item, "topic", "")
                    raw_text = getattr(item, "text", "")
                    raw_question = getattr(item, "question", "")
                    raw_answers_q = getattr(item, "answers_q", "")
                    raw_answer = getattr(item, "answer", "")
                for val in (raw_topic, raw_text, raw_question, raw_answers_q, raw_answer):
                    if not isinstance(val, str):
                        raise ContractError("local speech fields must be strings")
                topic_id, topic_label = self._clean_speech_topic(raw_topic, topic_labels)
                clean_text = raw_text.strip()
                clean_q = raw_question.strip()
                clean_aq = raw_answers_q.strip()
                clean_a = raw_answer.strip()
                if not (clean_text or clean_q or clean_a):
                    raise ContractError("empty local speech item")
                if clean_aq and (not clean_a or clean_q):
                    raise ContractError("answers_q requires answer without question")
                if clean_a and not (clean_q or clean_aq):
                    raise ContractError("answer requires question or answers_q")

                if clean_text:
                    ptext = _string(clean_text, 4000, single_line=False)
                    pkey = (topic_id, " ".join(ptext.split()).lower())
                    if pkey not in seen_paragraphs:
                        seen_paragraphs.add(pkey)
                        p_ev = {
                            "kind": "paragraph",
                            "topic_id": topic_id,
                            "topic_label": topic_label,
                            "text": ptext,
                            "position": window_pos,
                            "origin": "local_speech",
                            "admission_mode": "local_singlepass",
                            "window_id": window_id,
                        }
                        new_events.append(_json(p_ev))
                        added_blocks += 1

                if clean_q:
                    qtext = _string(clean_q, 4000, single_line=False)
                    qnorm = self._norm_speech_surface(qtext)
                    existing_qid = questions_by_norm.get(qnorm) if qnorm else None
                    if existing_qid is not None:
                        existing_q = qmap.get(existing_qid)
                        existing_ev = speech_q_events.get(existing_qid)
                        if (existing_q is not None and existing_q.answer_ids
                                and existing_ev is not None
                                and existing_ev.get("topic_id") != topic_id):
                            existing_qid = None
                    if existing_qid is not None:
                        if clean_a:
                            _record_answer(existing_qid, clean_a)
                    else:
                        while f"q{next_q_num}" in qmap:
                            next_q_num += 1
                        qid = f"q{next_q_num}"
                        next_q_num += 1
                        q_ev = {
                            "kind": "question",
                            "topic_id": topic_id,
                            "topic_label": topic_label,
                            "text": qtext,
                            "position": window_pos,
                            "question_id": qid,
                            "origin": "local_speech",
                            "admission_mode": "local_singlepass",
                            "window_id": window_id,
                        }
                        new_events.append(_json(q_ev))
                        speech_q_events[qid] = q_ev
                        if qnorm:
                            questions_by_norm[qnorm] = qid
                        qmap[qid] = Question(
                            qid, qtext, topic_id, (), _hash(qtext), 1,
                            ((f"scope:{qid}:0", qtext, "open"),), True,
                        )
                        added_blocks += 1
                        q_changed = True
                        if clean_a:
                            _record_answer(qid, clean_a)

                if clean_aq:
                    _record_answer(_identifier(clean_aq), clean_a)

            next_state = replace(
                s,
                version=s.version + 1,
                q_version=s.q_version + int(q_changed),
                questions=tuple(qmap.values()),
                answers_json=tuple(new_answers_json),
                local_speech_events_json=s.local_speech_events_json + tuple(new_events),
                local_speech_window_ids=s.local_speech_window_ids + (window_id,),
                local_speech_cursor=nxt_c,
                local_speech_cursor_byte=next_byte,
                local_topic_labels=tuple(topic_labels.items()),
            )
            self._publish_state(next_state)
            return {"committed": True, "idempotent": False, "added_blocks": added_blocks,
                    "cursor": nxt_c, "cursor_byte": next_byte}

    def skip_local_speech_batch(self, authority, *, window_id: str,
                                expected_cursor: tuple[int, int],
                                next_cursor: tuple[int, int],
                                notice: str) -> dict:
        """Advance local speech cursor past a poison window and emit a system notice."""
        from .local_runner import LocalLoopbackAuthority

        if not self._local_product_authority:
            raise ContractError("local speech authority not enabled on this actor")
        if not isinstance(authority, LocalLoopbackAuthority) or not authority.verified:
            raise ContractError("local loopback authority required")
        _identifier(window_id)
        if (not isinstance(expected_cursor, (tuple, list)) or len(expected_cursor) != 2
                or not isinstance(next_cursor, (tuple, list)) or len(next_cursor) != 2):
            raise ContractError("speech cursor pair required")
        exp_c = (_integer(expected_cursor[0], 0), _integer(expected_cursor[1], 0))
        nxt_c = (_integer(next_cursor[0], 0), _integer(next_cursor[1], 0))
        if not isinstance(notice, str):
            raise ContractError("skip notice must be a string")
        clean_notice = _string(notice.strip(), 1000, nonempty=True, single_line=True)
        with self._lock:
            s = self._state
            if s.stopped:
                raise ContractError("local speech commit after stop")
            if s.paused:
                raise ContractError("local speech commit while paused")
            if window_id in s.local_speech_window_ids:
                return {"skipped": False, "idempotent": True,
                        "cursor": s.local_speech_cursor, "cursor_byte": s.local_speech_cursor_byte}
            if exp_c != s.local_speech_cursor:
                raise ContractError("stale local speech cursor")
            start_byte = speech_cursor_to_byte(s.portions, exp_c[0], exp_c[1])
            next_byte = speech_cursor_to_byte(s.portions, nxt_c[0], nxt_c[1])
            if start_byte != s.local_speech_cursor_byte or next_byte <= start_byte:
                raise ContractError("nonmonotonic local speech byte window")
            window_pos = min(next_byte - 1, 1_000_000)
            sys_ev = {
                "kind": "system",
                "topic_id": "",
                "topic_label": "",
                "text": clean_notice,
                "position": window_pos,
                "system_after": True,
                "origin": "local_speech",
                "admission_mode": "local_singlepass",
                "window_id": window_id,
            }
            next_state = replace(
                s,
                version=s.version + 1,
                local_speech_events_json=s.local_speech_events_json + (_json(sys_ev),),
                local_speech_window_ids=s.local_speech_window_ids + (window_id,),
                local_speech_cursor=nxt_c,
                local_speech_cursor_byte=next_byte,
            )
            self._publish_state(next_state)
            return {"skipped": True, "idempotent": False,
                    "cursor": nxt_c, "cursor_byte": next_byte}

    def cancel(self):
        with self._lock:
            return None

    def pause(self):
        with self._lock:
            if self._state.stopped:
                return
            self._state = replace(self._state, paused=True)

    def resume(self):
        with self._lock:
            if self._state.stopped:
                return
            self._state = replace(self._state, paused=False)

    def stop(self):
        with self._lock:
            self._state = self._stopped(self._state)
            return self._export(self._state)

    @staticmethod
    def _stopped(s):
        pending = {p.unit_id: p for p in s.pending}
        for portion in s.portions:
            for unit in _units(portion):
                if unit.id not in pending:
                    pending[unit.id] = Pending(
                        unit.id, "stopped_tail",
                        (s.source_tail, s.q_version, s.manual_generation, "stopped"),
                    )
        return replace(s, stopped=True, pending=tuple(pending.values()))

    def reset(self):
        with self._lock:
            old = self._stopped(self._state)
            self._archives[old.session_epoch] = old
            self._state = SemanticState(old.session_epoch + 1)
            return self._state.session_epoch

    @staticmethod
    def _export(s):
        return {
            "version": 1,
            "session_epoch": s.session_epoch,
            "state_version": s.version,
            "q_version": s.q_version,
            "manual_generation": s.manual_generation,
            "source_tail": s.source_tail,
            "portions": [dict(p.__dict__) for p in s.portions],
            "speaker_labels": dict(s.speaker_labels),
            "questions": [q.to_dict() for q in s.questions],
            "answers": [json.loads(a) for a in s.answers_json],
            "local_events": [json.loads(e) for e in s.local_events_json],
            "local_speech": {
                "admission_mode": "local_singlepass",
                "cursor": list(s.local_speech_cursor),
                "cursor_byte": s.local_speech_cursor_byte,
                "window_ids": list(s.local_speech_window_ids),
                "topic_labels": dict(s.local_topic_labels),
                "events": [json.loads(e) for e in s.local_speech_events_json],
            },
            "publication_order": list(s.publication_order),
            "pending": [{**p.__dict__, "blocked_at": list(p.blocked_at)} for p in s.pending],
            "stopped": s.stopped,
            "paused": s.paused,
            "durability": "in_process_only",
        }

    def export(self, session_epoch=None):
        with self._lock:
            epoch = self._state.session_epoch if session_epoch is None else _integer(session_epoch, 1)
            s = self._state if epoch == self._state.session_epoch else self._archives.get(epoch)
            if s is None:
                raise ContractError("unknown export session")
            return self._export(s)


ShadowActor = SemanticActor
