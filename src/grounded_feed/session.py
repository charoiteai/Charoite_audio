"""Grounded feed session facade: one owner of canvas, renderer, publisher, and Stop export.

Constructed by `bridge.select` when `sufler.live_feed` is true. Coordinates
accepted-speech ingestion (`AcceptedSource`), local single-pass steps
(`LocalFeedRunner`), manual Q/A preemption, channel events, and verified atomic
Stop export (`<transcript>.grounded.json`).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .actor_core import ActorRefused, ActorStopped
from .events import ChannelEvent
from .publish import NdjsonPublisher
from .render import render_blocks
from .thread import ThreadCanvas


@dataclass(frozen=True)
class ManualGenerationToken:
    """Opaque session-owned handle for one manual answer generation."""

    manual_id: str
    generation: int
    actor_manual_generation: int | None
    cancel_event: threading.Event
    done_event: threading.Event


def _verify_published_target(target, raw: bytes) -> None:
    """Open-first verification: identity via O_NOFOLLOW/fstat, bounded byte compare."""
    import os
    import stat as stat_module

    from .export import ExportRefused

    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                      | getattr(os, "O_NONBLOCK", 0))
    except OSError as error:
        raise ExportRefused("published target unreadable") from error
    fields = lambda info: (info.st_dev, info.st_ino, info.st_size,  # noqa: E731
                           info.st_mtime_ns, info.st_ctime_ns)
    try:
        try:
            before = os.fstat(fd)
            if not stat_module.S_ISREG(before.st_mode):
                raise ExportRefused("published target not a regular file")
            chunks = []
            remaining = len(raw) + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(fd)
        except OSError as error:
            raise ExportRefused("published target unreadable") from error
        data = b"".join(chunks)
        if len(data) != len(raw) or data != raw:
            raise ExportRefused("published target changed")
        if fields(after) != fields(before):
            raise ExportRefused("published target changed")
        try:
            path_info = os.stat(target, follow_symlinks=False)
        except OSError as error:
            raise ExportRefused("published target unreadable") from error
        if (not stat_module.S_ISREG(path_info.st_mode)
                or (path_info.st_dev, path_info.st_ino) != (before.st_dev, before.st_ino)):
            raise ExportRefused("published target changed")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _block_row(block, index: int, *, answer_to) -> dict:
    return {"id": f"b{block.serial}", "index": index, "kind": block.kind,
            "text": block.text, "topic_id": block.topic_id,
            "topic_label": block.topic_label, "position": block.position,
            "question_id": block.question_id, "qid": block.question_id,
            "answer_to": answer_to,
            "manual_id": block.manual_id, "system_after": block.system_after,
            "answer_id": block.answer_id, "scope_ids": list(block.scope_ids),
            "claim_ids": list(block.claim_ids), "status": block.status,
            "source_refs": list(block.source_refs)}


class SessionClosed(RuntimeError):
    pass


class GroundedFeedSession:
    feed_on = True

    def __init__(self, emit, *, interval_s: float = 0.25) -> None:
        self._emit = emit
        self._canvas = ThreadCanvas()
        self.publisher = NdjsonPublisher(emit)
        self.interval_s = float(interval_s)
        self.closed = False
        self.steps_committed = 0
        self._lock = threading.RLock()
        self._boundary_provider = None
        self._source_actor = None
        self._accepted_source = None
        self._actor = None
        self._closing = False
        self.incomplete_close = False
        self._internal_cutoff_failure = None
        self._internal_drain_failure = None
        self.export_fsync_failure = None
        self.export_commit_failure = None
        self.export_replace_failure = None
        self._frozen_export = None
        self.source_ingested = 0
        self.source_drain_failure: str | None = None
        self.producer_cutoff_failure: str | None = None
        self._frame_version = 0
        self._local_runner = None
        self._step_serial = 0
        self._manual_events: set[tuple[str, str, str]] = set()
        self._manual_thread_projected: set[tuple[str, str, str]] = set()
        self._manual_gen_seq = 0
        self._active_manual: ManualGenerationToken | None = None
        self._auto_feed_enabled = True

    # ---- legacy-compatible readonly facade ------------------------------
    @property
    def feed(self) -> bool:
        return True

    def _blocks(self):
        if self._actor is not None:
            return self._actor.snapshot()[1]
        return self._canvas.ordered()

    def render(self) -> str:
        return render_blocks(self._blocks())

    def full(self) -> str:
        return self.render()

    @property
    def size(self) -> int:
        return len(self._blocks())

    def last_topic_title(self) -> str:
        topics = [block.topic_label for block in self._blocks() if block.topic_label]
        return topics[-1] if topics else ""

    def as_context(self, *, topics: int = 2) -> str:
        blocks = [b for b in self._blocks() if b.kind != "system"]
        if not blocks or topics <= 0:
            return ""
        seen: list[str] = []
        for b in reversed(blocks):
            if b.topic_id and b.topic_id not in seen:
                seen.append(b.topic_id)
                if len(seen) >= topics:
                    break
        keep = set(seen)
        selected = [b for b in blocks if b.topic_id in keep]
        return render_blocks(selected) if selected else ""

    # ---- source & runner wiring -----------------------------------------
    def attach_actor(self, actor) -> None:
        """Bind the one production actor that owns this session's canvas."""
        with self._lock:
            if self.closed or self._closing or self._actor is not None:
                raise SessionClosed("actor already attached")
            if getattr(actor, "_canvas", None) is not self._canvas:
                raise SessionClosed("actor must own this session canvas")
            self._actor = actor

    def _require_owner(self, actor):
        if actor is not None and self._actor is not None and actor is not self._actor:
            raise SessionClosed("another actor already owns this session")

    def attach_worker(self, worker) -> None:
        """Attach the model worker whose cutoff must be acknowledged before export."""
        if self._actor is None:
            raise SessionClosed("worker requires the bound actor")
        self._actor.attach_worker(worker)

    def attach_source_worker(self, worker) -> None:
        """Attach the accepted-source producer whose cutoff precedes export."""
        if self._actor is None:
            raise SessionClosed("source worker requires the bound actor")
        self._actor.attach_source_worker(worker)

    def attach_transcript(self, transcript, actor=None, *,
                          source_only_notice: bool = True) -> int:
        """Production source promotion: real Transcript accepted portions -> actor."""
        from .accepted_source import AcceptedSource
        from .source_actor import SourceActor

        with self._lock:
            self._require_owner(actor)
            if self.closed:
                raise SessionClosed("closed")
            if self._accepted_source is not None:
                raise SessionClosed("source already attached")
            actor = actor or self._actor or SourceActor()
            source = AcceptedSource(actor, transcript.accepted_portions, session_epoch=1)
            self._source_actor = actor
            self._accepted_source = source
            self.set_boundary_provider(source.accepted_boundary)
            if source_only_notice:
                self.publisher.status("Grounded feed: source ingestion only; narrative runner is not landed")
            return source.session_epoch

    def attach_source(self, actor, source) -> None:
        with self._lock:
            self._require_owner(actor)
            adapter_owner = getattr(source, "_actor", None)
            if adapter_owner is not None and adapter_owner is not actor:
                raise SessionClosed("source adapter belongs to another actor")
            if self.closed or self._accepted_source is not None:
                raise SessionClosed("source already attached")
            self._source_actor = actor
            self._accepted_source = source
            boundary = getattr(source, "accepted_boundary", None)
            if callable(boundary):
                self.set_boundary_provider(boundary)

    def attach_local_runner(self, runner) -> None:
        """Bind the strictly local single-pass product runner."""
        from .local_runner import LocalFeedRunner

        with self._lock:
            if self.closed or self._closing or self._local_runner is not None:
                raise SessionClosed("runner already attached or session closing")
            if (self._actor is None or not isinstance(runner, LocalFeedRunner)
                    or runner.actor is not self._actor or not runner.authority.admitted):
                raise SessionClosed("local runner must use this session actor and authority")
            self._actor.attach_worker(runner)
            self._local_runner = runner

    def adopt_local_authority(self, authority) -> None:
        """Promote the attached local runner authority once verified."""
        from .local_runner import LocalLoopbackAuthority

        if not isinstance(authority, LocalLoopbackAuthority) or not authority.verified:
            raise ValueError("verified LocalLoopbackAuthority required")
        with self._lock:
            if self._local_runner is not None:
                self._local_runner.authority = authority

    def begin_manual_generation(self, manual_id: str) -> ManualGenerationToken:
        """Synchronously register a manual generation token and supersede any prior one."""
        if not isinstance(manual_id, str) or not manual_id:
            raise ValueError("manual id")
        cancel_ev = threading.Event()
        done_ev = threading.Event()
        with self._lock:
            actor_gen = self._actor.manual_generation if self._actor is not None else None
            if self._actor is not None:
                self._actor.invalidate_local_speech_window()
            if self.closed or self._closing:
                cancel_ev.set()
            self._manual_gen_seq += 1
            if self._active_manual is not None:
                self._active_manual.cancel_event.set()
            token = ManualGenerationToken(
                manual_id=manual_id,
                generation=self._manual_gen_seq,
                actor_manual_generation=actor_gen,
                cancel_event=cancel_ev,
                done_event=done_ev,
            )
            self._active_manual = token
            local_runner = self._local_runner
        if local_runner is not None:
            local_runner.cancel_active()
        return token

    def commit_manual_answer(self, token: ManualGenerationToken, answer: str, *,
                             position: int | None = None):
        """Atomically commit a manual answer iff its generation token is still current."""
        if not isinstance(token, ManualGenerationToken):
            raise ValueError("manual generation token required")
        with self._lock:
            if (self.closed or self._closing
                    or token.cancel_event.is_set()
                    or self._active_manual is not token):
                return None
            if self._actor is not None:
                try:
                    block = self._actor.manual_answer(
                        token.manual_id,
                        answer,
                        position=self._boundary() if position is None else position,
                        expected_manual_generation=token.actor_manual_generation,
                        cancel_event=token.cancel_event,
                    )
                except (ActorRefused, ActorStopped) as exc:
                    if isinstance(exc, ActorStopped) or any(
                            tok in str(exc) for tok in ("superseded", "cancelled")
                    ):
                        return None
                    raise
                self._publish_manual_once(token.manual_id, "answer", answer)
                return block
            return self.manual_answer(token.manual_id, answer, position=position)

    def end_manual_generation(self, token_or_id, cancel_ev: threading.Event | None = None) -> bool:
        """Finish manual generation; return True iff it was not superseded/cancelled."""
        with self._lock:
            current = self._active_manual
            if isinstance(token_or_id, ManualGenerationToken):
                token = token_or_id
                still_current = current is token and not token.cancel_event.is_set()
                token.done_event.set()
                if current is token:
                    self._active_manual = None
                return still_current
            ev = cancel_ev
            still_current = (current is not None and current.manual_id == token_or_id
                             and current.cancel_event is ev and not ev.is_set())
            if current is not None and current.cancel_event is ev:
                current.done_event.set()
                self._active_manual = None
            return still_current

    def cancel_live_generation(self, *, timeout_s: float = 2.0) -> bool:
        """Cancel any active manual answer or local feed stream and wait boundedly."""
        done_ev = None
        with self._lock:
            if self._active_manual is not None:
                self._active_manual.cancel_event.set()
                done_ev = self._active_manual.done_event
            local_runner = self._local_runner
        if local_runner is not None:
            local_runner.cancel_active()
        if done_ev is not None:
            return done_ev.wait(max(0.0, float(timeout_s)))
        return True

    def set_auto_feed_enabled(self, enabled: bool) -> None:
        """Enable/disable automatic speech windows while keeping manual Q/A live."""
        with self._lock:
            if self.closed or self._closing or (self._actor is not None and self._actor.is_stopped):
                return
            self._auto_feed_enabled = bool(enabled)
            if self._actor is not None:
                self._actor.set_local_auto_feed(self._auto_feed_enabled)
            local_runner = self._local_runner
        if not enabled and local_runner is not None:
            local_runner.cancel_active()

    def step(self):
        """One daemon step over accepted source and the attached local runner."""
        self.ingest_source_once(limit=64 if self._local_runner is not None else 8)
        with self._lock:
            if self.closed or self._closing or self.publisher.paused:
                return None
            if self._local_runner is None or not self._auto_feed_enabled:
                return None
            local_runner = self._local_runner
            self._step_serial += 1
            request_id = f"feed_step_{self._step_serial}"
        # Model I/O must never hold the session/actor locks.
        outcome = local_runner.run_next(request_id)
        if isinstance(outcome, dict) and outcome.get("status") in ("committed", "skipped"):
            self.publish()
        return outcome

    def ingest_source_once(self, *, limit: int = 8) -> int:
        """Drain available accepted portions into the production actor; publish once."""
        with self._lock:
            if self._accepted_source is None or self.closed:
                return 0
            portions = self._accepted_source.drain(limit=limit)
            if not portions:
                return 0
            self.source_ingested += len(portions)
            self.publish()
            return len(portions)

    def source_export(self) -> dict | None:
        with self._lock:
            if self._actor is not None:
                return self._actor.source_export()
            if self._source_actor is None:
                return None
            return self._source_actor.export()

    def set_boundary_provider(self, provider) -> None:
        if provider is not None and not callable(provider):
            raise ValueError("boundary provider")
        self._boundary_provider = provider

    def _boundary(self) -> int:
        if self._boundary_provider is not None:
            return min(max(0, int(self._boundary_provider())), 1_000_000)
        blocks = self._canvas.ordered()
        return max((block.position for block in blocks), default=0)

    def add_system(self, text: str, at: str = "") -> bool:
        with self._lock:
            if self.closed:
                return False
            event = ChannelEvent("system", text, at=at, position=self._boundary())
            if self._actor is not None:
                self._actor.system_event(event.text, position=event.position)
            else:
                self._canvas.system(event.text, position=event.position)
            self.publish()
            return True

    def channel_event(self, kind: str, text: str, *, at: str = "", position: int | None = None) -> bool:
        with self._lock:
            if self.closed:
                return False
            event = ChannelEvent(kind, text, at=at,
                                 position=self._boundary() if position is None else position)
            if self._actor is not None:
                self._actor.system_event(event.text, position=event.position)
            else:
                self._canvas.system(event.text, position=event.position)
            self.publish()
            return True

    # ---- publishing ------------------------------------------------------
    def publish(self) -> bool:
        with self._lock:
            self._frame_version += 1
            if self._actor is not None:
                version, blocks = self._frame_version, self._actor.snapshot()[1]
            else:
                version, blocks = self._frame_version, self._canvas.snapshot_versioned()[1]
            source = None
            if self._accepted_source is not None:
                source = {"cursor": self._accepted_source.cursor,
                          "epoch": self._accepted_source.session_epoch,
                          "ingested": self.source_ingested}
            return self.publisher.publish(render_blocks(blocks), version,
                                          questions=self._projection(blocks),
                                          blocks=self._projection_blocks(blocks),
                                          source=source)

    def projection(self) -> list[dict]:
        """Readonly Q/A metadata; additive to the legacy thread frame."""
        return self._projection(self._blocks())

    def projection_blocks(self) -> list[dict]:
        """Atomic ordered typed block projection: the UI single source of truth."""
        return self._projection_blocks(self._blocks())

    @staticmethod
    def _projection_blocks(blocks) -> list[dict]:
        ordered = sorted(blocks, key=lambda b: (b.position, b.system_after, b.serial))
        questions = {b.question_id: b for b in ordered if b.kind == "question"}
        answers: dict[str, list] = {}
        for block in ordered:
            if block.kind != "answer":
                continue
            question = questions.get(block.question_id)
            if question is None:
                raise ValueError("answer without a question block")
            if (block.position, block.system_after, block.serial) < (
                    question.position, question.system_after, question.serial):
                raise ValueError("answer block before its question")
            answers.setdefault(block.question_id, []).append(block)
        for group in answers.values():
            group.sort(key=lambda answer: answer.serial)
        rows, index = [], 0
        for block in ordered:
            if block.kind == "answer":
                continue
            rows.append(_block_row(block, index, answer_to=None))
            index += 1
            if block.kind == "question":
                for answer in answers.get(block.question_id, ()):
                    rows.append(_block_row(answer, index, answer_to=block.question_id))
                    index += 1
        return rows

    def _projection(self, blocks) -> list[dict]:
        answers: dict[str, str] = {}
        for block in blocks:
            if block.kind == "answer" and block.question_id not in answers:
                answers[block.question_id] = block.text
        rows = []
        for block in blocks:
            if block.kind != "question":
                continue
            answer = answers.get(block.question_id)
            rows.append({"id": block.question_id, "topic": block.topic_id,
                         "topic_label": block.topic_label, "question": block.text,
                         "state": "answered" if answer else "open", "answer": answer or ""})
        return rows

    def _publish_manual_once(self, manual_id: str, state: str, text: str) -> bool:
        key = (manual_id, state, text)
        if key not in self._manual_events:
            if not self.publisher.manual(manual_id, state, text):
                return False
            self._manual_events.add(key)
        if key in self._manual_thread_projected:
            return False
        if not self.publish():
            return False
        self._manual_thread_projected.add(key)
        return True

    def manual_request(self, manual_id: str, question: str, *, topic_id: str = "manual",
                       topic_label: str = "Manual question", position: int | None = None) -> str:
        if not isinstance(manual_id, str) or not manual_id or len(manual_id) > 96:
            raise ValueError("manual id")
        with self._lock:
            if self._actor is not None:
                qid = self._actor.manual_request(
                    manual_id, question, topic_id=topic_id, topic_label=topic_label,
                    position=self._boundary() if position is None else position)
                self._publish_manual_once(manual_id, "question", question)
                return qid
            existing = self._canvas.question_by_manual(manual_id)
            if existing is not None:
                self._publish_manual_once(manual_id, "question", existing.text)
                return existing.question_id
            qid = self._canvas.question(topic_id, topic_label, question,
                                        position=self._boundary() if position is None else position,
                                        manual_id=manual_id)
            self._publish_manual_once(manual_id, "question", question)
            return qid

    def manual_answer(self, manual_id: str, answer: str, *, position: int | None = None):
        if not isinstance(manual_id, str) or not manual_id:
            raise ValueError("manual id")
        with self._lock:
            if self._actor is not None:
                block = self._actor.manual_answer(
                    manual_id, answer, position=self._boundary() if position is None else position)
                self._publish_manual_once(manual_id, "answer", answer)
                return block
            existing = self._canvas.answer_by_manual(manual_id)
            if existing is not None:
                if existing.text == answer:
                    self._publish_manual_once(manual_id, "answer", answer)
                    return existing
                raise ActorRefused("manual answer already recorded")
            question = self._canvas.question_by_manual(manual_id)
            if question is None:
                raise ValueError("manual answer without question")
            block = self._canvas.answer(question.question_id, answer,
                                        position=self._boundary() if position is None else position,
                                        manual_id=manual_id)
            self._publish_manual_once(manual_id, "answer", answer)
            return block

    def pause(self) -> None:
        with self._lock:
            if self.closed or self._closing or (self._actor is not None and self._actor.is_stopped):
                return
            if self._actor is not None:
                self._actor.pause()
            self.publisher.pause()
            local_runner = self._local_runner
        if local_runner is not None:
            local_runner.cancel_active()

    def resume(self) -> None:
        with self._lock:
            if self.closed or self._closing or (self._actor is not None and self._actor.is_stopped):
                return
            if self._actor is not None:
                self._actor.resume()
            self.publisher.resume()

    def close(self, *, export_path=None, session_epoch: str = "close",
              request_id: str = "close") -> bool:
        """Lossless stop: worker cutoff outside this lock, then export/readback."""
        with self._lock:
            if self.closed or self._closing:
                return False
            self._closing = True
        try:
            self.cancel_live_generation(timeout_s=2.0)
            actor = self._actor
            if (self.producer_cutoff_failure is not None
                    and self.producer_cutoff_failure == self._internal_cutoff_failure):
                self.producer_cutoff_failure = None
                self._internal_cutoff_failure = None
            if (self.source_drain_failure is not None
                    and self.source_drain_failure == self._internal_drain_failure):
                self.source_drain_failure = None
                self._internal_drain_failure = None
            cutoff_ok = True
            if actor is not None:
                try:
                    actor.begin_stop()
                except ActorStopped:
                    pass  # retry after a failed export
                except Exception as error:  # noqa: BLE001
                    self.producer_cutoff_failure = type(error).__name__
                cutoff_ok = actor.stop_workers(2.0)
                if not cutoff_ok:
                    self.producer_cutoff_failure = (actor.cutoff_failure_reason
                                                    or "worker_cutoff_timeout")
                    self._internal_cutoff_failure = self.producer_cutoff_failure
            with self._lock:
                if self.closed:
                    return False
                if actor is not None and not cutoff_ok:
                    self.incomplete_close = "cutoff_timeout"
                    return False
                if actor is not None:
                    try:
                        actor.freeze_snapshot()
                    except Exception as error:  # noqa: BLE001
                        self.producer_cutoff_failure = type(error).__name__
                        self._internal_cutoff_failure = self.producer_cutoff_failure
                if self._accepted_source is not None:
                    self.source_drain_failure = None
                    drained = 0
                    while True:
                        try:
                            batch = self._accepted_source.drain(limit=1024)
                        except ActorStopped:
                            try:
                                self._accepted_source.stage_late_tail()
                            except Exception as error:  # noqa: BLE001
                                self.source_drain_failure = type(error).__name__
                                self._internal_drain_failure = self.source_drain_failure
                            break
                        except Exception as error:  # noqa: BLE001
                            self.source_drain_failure = type(error).__name__
                            self._internal_drain_failure = self.source_drain_failure
                            break
                        if not batch:
                            break
                        drained += len(batch)
                        if drained >= 1_000_000:
                            self.source_drain_failure = "source_drain_unbounded"
                            break
                    self.source_ingested += drained
                incomplete = (actor is not None and (
                    self.source_drain_failure is not None
                    or self.producer_cutoff_failure is not None))
                if incomplete:
                    self.incomplete_close = True
                    return False
                if export_path is not None:
                    self._write_export_locked(export_path, session_epoch, request_id)
                self.closed = True
                self.incomplete_close = False
                return (self.source_drain_failure is None
                        and self.producer_cutoff_failure is None)
        finally:
            with self._lock:
                self._closing = False

    def late_results(self) -> dict[str, str]:
        return {}

    def write_export(self, export_path, *, session_epoch: str = "export",
                     request_id: str = "export") -> bool:
        """Write a readback only after Stop/cutoff."""
        with self._lock:
            if self._closing:
                raise SessionClosed("close in progress")
            if self.incomplete_close:
                raise SessionClosed("incomplete close; retry close() after recovery")
            if self._actor is not None and (not self._actor.is_stopped
                                            or not self._actor.cutoff_acknowledged):
                raise SessionClosed("export before Stop/cutoff")
            self._write_export_locked(export_path, session_epoch, request_id)
            return True

    def _write_export_locked(self, export_path, session_epoch: str, request_id: str) -> None:
        import os
        import tempfile
        from pathlib import Path

        from .export import (MAX_EXPORT_BYTES, ExportDurability, ExportRefused, _canonical,
                             read_snapshot, write_snapshot)

        actor = self._actor
        target = Path(export_path)
        staged = None
        success = False
        finalized = False
        target_committed = False
        durability_retry = bool(actor is not None and actor.is_exported
                                and self.export_replace_failure is None
                                and self.export_fsync_failure is not None
                                and self._frozen_export is not None)
        if durability_retry:
            raw, frozen_target, frozen_epoch, frozen_request = self._frozen_export
            if (Path(frozen_target).resolve() != target.resolve()
                    or frozen_epoch != session_epoch or frozen_request != request_id):
                raise ExportRefused("frozen export parameters differ")
            _verify_published_target(target, raw)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            except OSError as error:
                self.export_fsync_failure = type(error).__name__
                raise ExportDurability(target) from error
            finally:
                os.close(directory)
            _verify_published_target(target, raw)
            self.export_fsync_failure = None
            self.export_commit_failure = None
            self._frozen_export = None
            return
        retry_finalized = bool(actor is not None and actor.is_exported
                               and self.export_replace_failure is not None)
        if retry_finalized:
            if self._frozen_export is None:
                raise ExportRefused("frozen export bytes missing")
            raw, frozen_target, frozen_epoch, frozen_request = self._frozen_export
            if (Path(frozen_target).resolve() != target.resolve()
                    or frozen_epoch != session_epoch or frozen_request != request_id):
                raise ExportRefused("frozen export parameters differ")
            payload = None
        if actor is not None and not retry_finalized:
            actor.begin_export()
        try:
            fd, staged_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".staged",
                                               dir=target.parent)
            staged = Path(staged_name)
            os.close(fd)
            if retry_finalized:
                with open(staged, "wb") as staged_file:
                    staged_file.write(raw)
                    staged_file.flush()
                    os.fsync(staged_file.fileno())
                readback = read_snapshot(staged)
                if staged.read_bytes() != raw or readback.get("snapshot") is None:
                    raise ExportRefused("frozen export readback mismatch")
            else:
                payload = self.export_payload(session_epoch=session_epoch,
                                              request_id=request_id)
                raw = _canonical(payload)
                if len(raw) > MAX_EXPORT_BYTES:
                    raise ExportRefused("export too large")
                write_snapshot(staged, payload)
                readback = read_snapshot(staged)
                if readback != payload or staged.read_bytes() != raw:
                    raise ExportRefused("export readback mismatch")
            if actor is not None and not retry_finalized:
                actor.finalize_export(payload["snapshot"]["late_results"],
                                      payload["snapshot"]["late_sources"])
                finalized = True
                self._frozen_export = (raw, str(target), session_epoch, request_id)
            elif retry_finalized:
                finalized = True
            os.replace(staged, target)
            staged = None
            target_committed = True
            if actor is not None:
                actor.commit_export()
            try:
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except BaseException as error:
                self.export_fsync_failure = type(error).__name__
                if isinstance(error, OSError):
                    raise ExportDurability(target) from error
                raise
            _verify_published_target(target, raw)
            success = True
            self.export_replace_failure = None
            self.export_commit_failure = None
            self.export_fsync_failure = None
            self._frozen_export = None
        except BaseException as error:
            if actor is not None and finalized and not target_committed:
                self.export_commit_failure = "replace_after_finalize"
                self.export_replace_failure = type(error).__name__
            elif (actor is not None and finalized and target_committed
                  and self.export_fsync_failure is None):
                self.export_replace_failure = type(error).__name__
            raise
        finally:
            if staged is not None:
                try:
                    staged.unlink()
                except OSError:
                    pass
            if actor is not None and not success and not finalized:
                actor.abort_export()

    def run(self, stop, enabled=None) -> None:
        if self._accepted_source is None:
            self.publisher.status("Grounded feed: no accepted source bound")
            return
        self._run_source(stop, enabled)

    def _run_source(self, stop, enabled) -> None:
        while not stop.is_set():
            if self._local_runner is not None:
                if enabled is not None:
                    want = bool(enabled())
                    if want != self._auto_feed_enabled:
                        self.set_auto_feed_enabled(want)
                if not self._auto_feed_enabled:
                    stop.wait(self.interval_s)
                    continue
                self.step()
                stop.wait(self.interval_s)
                continue
            if enabled is not None and not enabled():
                self.pause()
                stop.wait(self.interval_s)
                continue
            if self.publisher.paused:
                self.resume()
            self.step()
            stop.wait(self.interval_s)

    def export_payload(self, *, session_epoch: str, request_id: str) -> dict:
        with self._lock:
            actor_export = None
            if self._actor is not None:
                (_version, blocks), actor_export = self._actor.snapshot_bundle()
            else:
                _version, blocks = self._canvas.snapshot_versioned()
            effective_cutoff = self.producer_cutoff_failure
            if effective_cutoff is None and self._actor is not None:
                effective_cutoff = self._actor.cutoff_failure_reason
            complete = (self.source_drain_failure is None
                        and effective_cutoff is None
                        and (self._actor is None
                             or (self._actor.is_frozen
                                 and self._actor.cutoff_acknowledged)))
            return {"version": 1, "session_epoch": session_epoch, "request_id": request_id,
                    "snapshot": {"render": render_blocks(blocks),
                                 "blocks": [b.to_dict() for b in blocks],
                                 "projection": self._projection_blocks(blocks),
                                 "questions": self._projection(blocks),
                                 "source": (actor_export["source"] if actor_export is not None
                                            else self.source_export()),
                                 "semantic": (actor_export["semantic"] if actor_export is not None
                                              else None),
                                 "source_drain_failure": self.source_drain_failure,
                                 "complete": complete,
                                 "producer_cutoff_failure": effective_cutoff,
                                 "source_cursor": (self._accepted_source.cursor
                                                   if self._accepted_source is not None else None),
                                 "pending": (actor_export["pending"]
                                             if actor_export is not None else {}),
                                 "steps_committed": self.steps_committed,
                                 "late_results": (actor_export["late_results"]
                                                  if actor_export is not None else {}),
                                 "late_sources": (actor_export["late_sources"]
                                                  if actor_export is not None else [])}}
