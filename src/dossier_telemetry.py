"""Closed, content-free counters for one nightly dossier run.

Adapter entries are not physical HTTP requests. Counters are observational:
no retry, admission, exception, graph or publication policy lives here.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from functools import wraps
import json

import requests


class Phase(str, Enum):
    PREFLIGHT = "preflight"
    ADMISSION = "admission"
    CONTRACT = "contract"
    STREAM = "stream"
    FALLBACK = "fallback"


class Reason(str, Enum):
    RETURNED = "returned"
    EMPTY = "empty_reply"
    PREFLIGHT = "preflight_refused"
    CONTEXT = "context_budget"
    CONTRACT = "contract_refused"
    TIMEOUT = "timeout"
    HTTP = "http_error"
    STREAM = "stream_error"
    TRANSPORT = "transport_error"
    ERROR = "unexpected_error"
    INTERRUPTED = "interrupted"
    INPUT = "input_refused"
    FORMAT = "format_refused"
    VALIDATION = "validation_refused"
    IO = "io_refused"
    UNKNOWN = "unknown"


class Stage(str, Enum):
    SOURCE_RELEVANCE = "source_relevance"
    APPEND = "append"
    DETERMINISTIC = "deterministic_check"
    SEMANTIC = "semantic_check"
    AUDIT_SUPPORT = "audit_support"
    AUDIT_TOPIC = "audit_topic"
    APPEND_COMMIT = "append_commit"
    REBUILD = "rebuild"
    SCAN = "scan"
    UNKNOWN = "unknown"


class Detail(str, Enum):
    JSON_MALFORMED = "json_malformed"
    JSON_SCHEMA = "json_schema"
    RELEVANCE_INCOMPLETE = "relevance_incomplete"
    RELEVANCE_UNCERTAIN = "relevance_uncertain"
    RELEVANCE_QUOTE_MISSING = "relevance_quote_missing"
    CONTEXT_BUDGET = "context_budget"
    PREFLIGHT = "preflight_refused"
    CONTRACT = "contract_refused"
    LEDGER_MALFORMED = "ledger_malformed"
    LEDGER_SOURCE = "ledger_source"
    LEDGER_QUOTE = "ledger_quote"
    LEDGER_PLACEMENT = "ledger_placement"
    SECTIONS_INVALID = "sections_invalid"
    SPEAKER_LABELS = "speaker_labels"
    LINKS_LOST = "links_lost"
    SOURCE_UNATTRIBUTED = "source_unattributed"
    CONSERVATION_LOST = "conservation_lost"
    MARKUP_CHANGED = "markup_changed"
    SEMANTIC_REJECTED = "semantic_rejected"
    CLAIM_MALFORMED = "claim_malformed"
    CLAIM_QUOTE_MISSING = "claim_quote_missing"
    CLAIM_QUOTE_AMBIGUOUS = "claim_quote_ambiguous"
    CLAIM_SPAN_MISSING = "claim_span_missing"
    CLAIM_UNCOVERED = "claim_uncovered"
    CLAIM_SOURCE_MISSING = "claim_source_missing"
    AUDIT_SUPPORT_REJECTED = "audit_support_rejected"
    AUDIT_TOPIC_REJECTED = "audit_topic_rejected"
    RENDER_INVALID = "render_invalid"
    INPUT_REFUSED = "input_refused"
    FORMAT_REFUSED = "format_refused"
    EMPTY_REPLY = "empty_reply"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    STREAM_ERROR = "stream_error"
    TRANSPORT_ERROR = "transport_error"
    IO_REFUSED = "io_refused"
    INTERRUPTED = "interrupted"
    UNEXPECTED_ERROR = "unexpected_error"
    UNKNOWN = "unknown"


_REASON_TO_DETAIL: dict[Reason, Detail] = {
    Reason.EMPTY: Detail.EMPTY_REPLY,
    Reason.PREFLIGHT: Detail.PREFLIGHT,
    Reason.CONTEXT: Detail.CONTEXT_BUDGET,
    Reason.CONTRACT: Detail.CONTRACT,
    Reason.TIMEOUT: Detail.TIMEOUT,
    Reason.HTTP: Detail.HTTP_ERROR,
    Reason.STREAM: Detail.STREAM_ERROR,
    Reason.TRANSPORT: Detail.TRANSPORT_ERROR,
    Reason.ERROR: Detail.UNEXPECTED_ERROR,
    Reason.INTERRUPTED: Detail.INTERRUPTED,
    Reason.INPUT: Detail.INPUT_REFUSED,
    Reason.FORMAT: Detail.FORMAT_REFUSED,
    Reason.IO: Detail.IO_REFUSED,
}

_MESSAGE_TO_DETAIL: dict[str, Detail] = {
    "malformed model JSON": Detail.JSON_MALFORMED,
    "unexpected model JSON fields": Detail.JSON_SCHEMA,
    "duplicate verdict key": Detail.JSON_SCHEMA,
    "incomplete relevance verdict": Detail.RELEVANCE_INCOMPLETE,
    "source relevance did not return a literal boolean": Detail.RELEVANCE_INCOMPLETE,
    "empty or damaged candidate": Detail.SECTIONS_INVALID,
    "candidate has protected, ambiguous or incomplete sections": Detail.SECTIONS_INVALID,
    "candidate contains speaker labels": Detail.SPEAKER_LABELS,
    "candidate lost a canonical link target": Detail.LINKS_LOST,
    "candidate does not attribute each accepted source": Detail.SOURCE_UNATTRIBUTED,
    "missing change ledger": Detail.LEDGER_MALFORMED,
    "malformed correction ledger": Detail.LEDGER_MALFORMED,
    "empty correction ledger entry": Detail.LEDGER_MALFORMED,
    "unsupported correction: before must occur in both old and new body": Detail.LEDGER_PLACEMENT,
    "unsupported correction: after must be an exact substring of the candidate body": Detail.LEDGER_PLACEMENT,
    "superseded current claim must move to history with its replacement current": Detail.LEDGER_PLACEMENT,
    "ambiguous correction: before must identify one current physical-line occurrence": Detail.LEDGER_PLACEMENT,
    "unsupported or unattributed correction ledger": Detail.LEDGER_SOURCE,
    "candidate lost a prior claim or has uncovered new significant text (section/count/order)": Detail.CONSERVATION_LOST,
    "candidate changed prior opaque markup context": Detail.MARKUP_CHANGED,
    "candidate introduced opaque markup around dossier content": Detail.MARKUP_CHANGED,
    "candidate semantic conservation was not accepted": Detail.SEMANTIC_REJECTED,
    "candidate claim is not relevant and supported": Detail.SEMANTIC_REJECTED,
    "candidate claims must be an array": Detail.CLAIM_MALFORMED,
    "malformed candidate claim": Detail.CLAIM_MALFORMED,
    "empty candidate claim evidence": Detail.CLAIM_MALFORMED,
    "duplicate candidate claim": Detail.CLAIM_MALFORMED,
    "candidate claim lacks exact new-source evidence": Detail.CLAIM_QUOTE_MISSING,
    "complete source context is required for claim audits": Detail.CLAIM_QUOTE_MISSING,
    "ambiguous claim evidence: quote must identify one source occurrence": Detail.CLAIM_QUOTE_AMBIGUOUS,
    "candidate claim text is not an exact candidate span": Detail.CLAIM_SPAN_MISSING,
    "candidate claim does not represent new significant text": Detail.CLAIM_SPAN_MISSING,
    "candidate has uncovered new significant text": Detail.CLAIM_UNCOVERED,
    "candidate lacks a new claim contribution from every accepted source": Detail.CLAIM_SOURCE_MISSING,
    "independent claim audit was not accepted (supported)": Detail.AUDIT_SUPPORT_REJECTED,
    "independent claim audit was not accepted (relevant)": Detail.AUDIT_TOPIC_REJECTED,
    "invalid rendered append": Detail.RENDER_INVALID,
    "author payload changed": Detail.RENDER_INVALID,
    "new source is unreadable or damaged": Detail.INPUT_REFUSED,
}


@dataclass
class _Request:
    phase: Phase = Phase.PREFLIGHT
    reason: Reason | None = None


@dataclass
class _Topic:
    reason: Reason = Reason.UNKNOWN
    request_failure: Reason | None = None
    stage: Stage | None = None
    detail: Detail | None = None


_run: ContextVar[Collector | None] = ContextVar("dossier_telemetry_run", default=None)
_request: ContextVar[_Request | None] = ContextVar("dossier_telemetry_request", default=None)
_topic: ContextVar[_Topic | None] = ContextVar("dossier_telemetry_topic", default=None)


class Collector:
    def __init__(self):
        self.topics = self.completed_topics = self.interrupted_topics = 0
        self.legacy_attempts = self.committed_topics = self.final_refusals = 0
        self.requests = self.stream_entries = 0
        self.outcomes = Counter()
        self.final_reasons = Counter()
        self.stage_refusals = Counter()
        self.interrupted = False
        self.scan_failed = self.index_failed = self.scanned_topics = self.scan_refusals = 0

    def snapshot(self) -> dict:
        return {
            "version": 1, "topics_started": self.topics,
            "topics_completed": self.completed_topics, "topics_interrupted": self.interrupted_topics,
            "legacy_attempts": self.legacy_attempts, "committed_topics": self.committed_topics,
            "final_refusals": self.final_refusals,
            "local_completion_attempts": self.requests, "adapter_stream_entries": self.stream_entries,
            "request_outcomes": {phase.value: {reason.value: self.outcomes[phase, reason]
                                              for reason in Reason if self.outcomes[phase, reason]}
                                 for phase in Phase if any(self.outcomes[phase, reason] for reason in Reason)},
            "final_reasons": {reason.value: self.final_reasons[reason]
                              for reason in Reason if self.final_reasons[reason]},
            "stage_refusals": {st.value: {dt.value: self.stage_refusals[st, dt]
                                          for dt in Detail if self.stage_refusals[st, dt]}
                               for st in Stage if any(self.stage_refusals[st, dt] for dt in Detail)},
            "interrupted": self.interrupted, "scan_failed": self.scan_failed,
            "index_failed": self.index_failed, "topics_scanned": self.scanned_topics,
            "scan_refusals": self.scan_refusals,
        }

    def scanned(self, *, topics: int, refused: int):
        self.scanned_topics += topics
        self.scan_refusals += refused
        self.final_refusals += refused
        if refused:
            self.final_reasons[Reason.INPUT] += refused
            self.stage_refusals[Stage.SCAN, Detail.INPUT_REFUSED] += refused

    @contextmanager
    def activate(self, on_emit=None):
        token = _run.set(self)
        original_error = False
        try:
            yield self
        except BaseException:
            original_error = True
            self.interrupted = True
            raise
        finally:
            _run.reset(token)
            emit_error = None
            try:
                snap = self.snapshot()
                try:
                    print("dossier_telemetry " + json.dumps(snap))
                except BaseException as exc:  # noqa: BLE001
                    emit_error = exc
                if on_emit is not None:
                    try:
                        on_emit(snap)
                    except BaseException as exc:  # noqa: BLE001
                        if emit_error is None:
                            emit_error = exc
            except BaseException as exc:  # noqa: BLE001
                emit_error = exc
            if emit_error is not None and not original_error:
                # An output failure cannot replace the run's original exception.
                # Delivery is unconfirmed; with no original error, propagate it.
                raise emit_error

    @contextmanager
    def topic(self):
        self.topics += 1
        token = _topic.set(_Topic())
        try:
            yield
        except BaseException:
            self.interrupted_topics += 1
            raise
        finally:
            _topic.reset(token)

    def complete_topic(self, *, attempted: bool, committed: int, refused: int):
        self.completed_topics += 1
        self.legacy_attempts += int(attempted)
        self.committed_topics += committed
        self.final_refusals += refused
        if refused:
            topic = _topic.get()
            final_reason = topic.reason if topic else Reason.UNKNOWN
            self.final_reasons[final_reason] += refused
            st = topic.stage if topic and topic.stage is not None else Stage.UNKNOWN
            dt = (topic.detail if topic and topic.detail is not None
                  else _REASON_TO_DETAIL.get(final_reason, Detail.UNKNOWN))
            self.stage_refusals[st, dt] += refused


def current() -> Collector | None:
    return _run.get()


def phase(value: Phase):
    if not isinstance(value, Phase):
        raise TypeError("closed telemetry phase required")
    if current := _request.get():
        current.phase = value


def begin_attempt():
    """A retry's preparation cannot inherit an earlier empty/failed reply."""
    if topic := _topic.get():
        topic.reason = Reason.UNKNOWN
        topic.request_failure = None
        topic.stage = None
        topic.detail = None


@contextmanager
def stage(value: Stage):
    if not isinstance(value, Stage):
        raise TypeError("closed telemetry stage required")
    topic = _topic.get()
    if topic is None:
        yield
        return
    previous = topic.stage
    topic.stage = value
    try:
        yield
    except BaseException:
        raise
    else:
        if topic.detail is None and topic.reason == Reason.UNKNOWN:
            topic.stage = previous


def detail(value: Detail):
    if not isinstance(value, Detail):
        raise TypeError("closed telemetry detail required")
    if topic := _topic.get():
        topic.detail = value


def record_refusal_message(message: str):
    """Map a closed internal refusal constant to a closed Detail; never store freeform text."""
    topic = _topic.get()
    if topic is not None and topic.detail is None:
        mapped = _MESSAGE_TO_DETAIL.get(message)
        if mapped is not None:
            topic.detail = mapped


def refusal(value: Reason):
    if not isinstance(value, Reason):
        raise TypeError("closed telemetry reason required")
    if topic := _topic.get():
        topic.reason = value
    if current := _request.get():
        current.reason = value


def request_refusal(default: Reason):
    """Preserve an explicit refusal when no failed transport request exists."""
    topic = _topic.get()
    if topic and topic.request_failure is not None:
        refusal(topic.request_failure)
    elif topic and topic.reason != Reason.UNKNOWN:
        refusal(topic.reason)
    else:
        refusal(default)


def stream_entry():
    if current := _run.get():
        current.stream_entries += 1


def exception_reason(exc: BaseException) -> Reason:
    if not isinstance(exc, Exception):
        return Reason.INTERRUPTED
    if isinstance(exc, (TimeoutError, requests.Timeout)):
        return Reason.TIMEOUT
    if isinstance(exc, requests.HTTPError):
        return Reason.HTTP
    if isinstance(exc, requests.RequestException):
        return Reason.TRANSPORT
    return Reason.ERROR


def observe_request(refusal_type: type[Exception]):
    def decorate(function):
        @wraps(function)
        def observed(*args, **kwargs):
            run = _run.get()
            if run is None:
                return function(*args, **kwargs)
            run.requests += 1
            request = _Request()
            token = _request.set(request)
            reason = Reason.ERROR
            try:
                result = function(*args, **kwargs)
                reason = Reason.RETURNED if result else Reason.EMPTY
                return result
            except BaseException as exc:
                if isinstance(exc, refusal_type):
                    reason = request.reason or {
                        Phase.PREFLIGHT: Reason.PREFLIGHT,
                        Phase.CONTRACT: Reason.CONTRACT,
                        Phase.FALLBACK: Reason.CONTRACT,
                    }.get(request.phase, Reason.UNKNOWN)
                else:
                    reason = request.reason or exception_reason(exc)
                raise
            finally:
                run.outcomes[request.phase, reason] += 1
                if topic := _topic.get():
                    # A request outcome is not a final topic verdict; the rejecting gate owns it.
                    topic.request_failure = None if reason == Reason.RETURNED else reason
                _request.reset(token)
        return observed
    return decorate
