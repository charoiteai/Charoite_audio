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


@dataclass
class _Request:
    phase: Phase = Phase.PREFLIGHT
    reason: Reason | None = None


@dataclass
class _Topic:
    reason: Reason = Reason.UNKNOWN
    request_failure: Reason | None = None


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
            "interrupted": self.interrupted, "scan_failed": self.scan_failed,
            "index_failed": self.index_failed, "topics_scanned": self.scanned_topics,
            "scan_refusals": self.scan_refusals,
        }

    def scanned(self, *, topics: int, refused: int):
        self.scanned_topics += topics
        self.scan_refusals += refused
        self.final_refusals += refused
        self.final_reasons[Reason.INPUT] += refused

    @contextmanager
    def activate(self):
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
            try:
                print("dossier_telemetry " + json.dumps(self.snapshot()))
            except (OSError, ValueError):
                # A closed output cannot replace the run's original exception.
                # Delivery is unconfirmed; with no original error, propagate it.
                if not original_error:
                    raise

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
            self.final_reasons[topic.reason if topic else Reason.UNKNOWN] += refused


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


def refusal(value: Reason):
    if not isinstance(value, Reason):
        raise TypeError("closed telemetry reason required")
    if topic := _topic.get():
        topic.reason = value
    if current := _request.get():
        current.reason = value


def request_refusal(default: Reason):
    """Prefer the current request's closed failure over a generic outer catch."""
    topic = _topic.get()
    refusal(topic.request_failure if topic and topic.request_failure is not None else default)


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
                    topic.request_failure = None if reason == Reason.RETURNED else reason
                    topic.reason = topic.request_failure or Reason.UNKNOWN
                _request.reset(token)
        return observed
    return decorate
