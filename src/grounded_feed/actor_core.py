"""One-owner production actor: accepted-source journal, local speech windows, and Stop fences.

`ActorCore` is the sole writer of its `ThreadCanvas` and accepted-source journal.
Source appends, single-pass local speech windows, manual Q/A, channel events, and
Stop/export barriers all share `self._lock`; only worker cancellation/joins run
outside the lock to prevent callback deadlocks.
"""

from __future__ import annotations

import hashlib
import json
import threading

from .semantic import ContractError, Question, SemanticActor, _hash, _string
from .source_actor import SourceActor, SourceRefused
from .thread import Block, ThreadCanvas, _clean


class ActorRefused(ValueError):
    """Closed admission/ownership refusal."""


class ActorStopped(ActorRefused):
    """The actor stopped; late work is staged, never fresh."""


class ActorCore:
    """Single owner of the canvas and of the accepted source journal."""

    def __init__(self, *, session_epoch: int = 1, canvas: ThreadCanvas | None = None,
                 local_product_authority: bool = False) -> None:
        if type(session_epoch) is not int or session_epoch < 1:
            raise ActorRefused("session epoch")
        if session_epoch != 1:
            raise ActorRefused("initial source epoch is 1")
        self._lock = threading.RLock()
        self._epoch = session_epoch
        self._local_product_authority = local_product_authority is True
        self._local_auto_feed = True
        self._local_window_epoch = 0
        self.semantic = self._make_semantic()
        self._source = SourceActor(self.semantic)
        self._canvas = canvas or ThreadCanvas()
        with self._canvas._lock:
            if getattr(self._canvas, "_actor_owner", None) is not None:
                raise ActorRefused("canvas already has an owner")
            self._canvas._actor_owner = self
            self._canvas._canonical = self.semantic
            self._canvas._lock = self._lock
        self._late = []
        self._stopped = False
        self._stop_snapshot = None
        self._worker = None
        self._worker_live = False
        self._source_worker = None
        self._source_worker_live = False
        self._attach_generation = 0
        self._cutoff_ack = True
        self._cutoff_failure_reason = None
        self._late_sources = []
        self._exported = False
        self._exporting = False
        self._export_committed = False

    def _make_semantic(self):
        return SemanticActor(
            lock=self._lock,
            projection=self._prepare_semantic_projection,
            local_product_authority=self._local_product_authority,
        )

    def _prepare_semantic_projection(self, state):
        """Pure derived canvas built from canonical local/manual and speech events."""
        blocks = []
        serials = {key: i for i, key in enumerate(state.publication_order)}
        for index, raw in enumerate(state.local_events_json):
            event = json.loads(raw)
            blocks.append(Block(serials[f"local:{index}"], **event))
        for index, raw in enumerate(state.local_speech_events_json):
            event = json.loads(raw)
            blocks.append(Block(
                serials[f"local_speech:{index}"],
                event["kind"],
                event["topic_id"],
                event["topic_label"],
                event["text"],
                event["position"],
                question_id=event.get("question_id"),
                system_after=bool(event.get("system_after", False)),
                manual_id=event.get("manual_id"),
                answer_id=event.get("answer_id"),
                scope_ids=tuple(event.get("scope_ids", ())),
                claim_ids=tuple(event.get("claim_ids", ())),
                status=event.get("status"),
                source_refs=tuple(event.get("source_refs", ())),
            ))
        return tuple(sorted(blocks, key=lambda block: block.serial))

    def semantic_export(self):
        with self._lock:
            return self.semantic.export()

    def set_local_auto_feed(self, enabled: bool) -> None:
        """Toggle automatic local speech windows while keeping manual Q/A live."""
        with self._lock:
            self._local_auto_feed = bool(enabled)
            self._local_window_epoch += 1

    def begin_local_speech_window(self) -> int:
        """Capture the current local window generation under the actor lock."""
        with self._lock:
            if not self._local_product_authority:
                raise ActorRefused("local speech authority not enabled on this actor")
            if self._stopped or self._exported or self._exporting:
                raise ActorStopped("local speech window after stop")
            if self.semantic.state.paused or not self._local_auto_feed:
                raise ActorRefused("local speech window paused")
            return self._local_window_epoch

    def invalidate_local_speech_window(self) -> int:
        """Invalidate any in-flight local speech window generation under lock."""
        with self._lock:
            self._local_window_epoch += 1
            return self._local_window_epoch

    def commit_local_speech_window(self, authority, *, window_id: str, items: list,
                                   expected_cursor: tuple[int, int],
                                   next_cursor: tuple[int, int],
                                   window_epoch: int | None = None) -> dict:
        """Admit a single-pass local speech batch under the actor lock."""
        with self._lock:
            if self._stopped or self._exported or self._exporting:
                raise ActorStopped("local speech window after stop")
            if not self._local_product_authority:
                raise ActorRefused("local speech authority not enabled on this actor")
            if self.semantic.state.paused or not self._local_auto_feed:
                raise ActorRefused("local speech window paused")
            if window_epoch is not None and window_epoch != self._local_window_epoch:
                raise ActorRefused("local speech window superseded")
            try:
                return self.semantic.commit_local_speech_batch(
                    authority,
                    window_id=window_id,
                    items=items,
                    expected_cursor=expected_cursor,
                    next_cursor=next_cursor,
                )
            except ContractError as error:
                raise ActorRefused(str(error)) from error

    def skip_local_speech_window(self, authority, *, window_id: str,
                                 expected_cursor: tuple[int, int],
                                 next_cursor: tuple[int, int],
                                 notice: str,
                                 window_epoch: int | None = None) -> dict:
        """Advance local speech cursor past a poison window under the actor lock."""
        with self._lock:
            if self._stopped or self._exported or self._exporting:
                raise ActorStopped("local speech window after stop")
            if not self._local_product_authority:
                raise ActorRefused("local speech authority not enabled on this actor")
            if self.semantic.state.paused or not self._local_auto_feed:
                raise ActorRefused("local speech window paused")
            if window_epoch is not None and window_epoch != self._local_window_epoch:
                raise ActorRefused("local speech window superseded")
            try:
                return self.semantic.skip_local_speech_batch(
                    authority,
                    window_id=window_id,
                    expected_cursor=expected_cursor,
                    next_cursor=next_cursor,
                    notice=notice,
                )
            except ContractError as error:
                raise ActorRefused(str(error)) from error

    def pause(self):
        with self._lock:
            self._local_window_epoch += 1
            self.semantic.pause()

    def resume(self):
        with self._lock:
            if self._stopped:
                raise ActorStopped("resume after stop")
            self.semantic.resume()

    def cancel(self):
        with self._lock:
            self._local_window_epoch += 1
            self.semantic.cancel()

    def attach_worker(self, worker) -> None:
        """Attach the model worker; only its cutoff runs outside actor lock."""
        if worker is not None and not callable(getattr(worker, "cancel_and_join", None)):
            raise ActorRefused("worker cutoff interface")
        with self._lock:
            if self._stopped:
                raise ActorStopped("worker after stop")
            if self._worker is not None and self._worker_live:
                raise ActorRefused("worker_already_live")
            self._worker = worker
            self._worker_live = worker is not None
            self._attach_generation += 1
            self._cutoff_ack = False

    def attach_source_worker(self, worker) -> None:
        """Attach the accepted-source producer whose cutoff must precede export."""
        if worker is not None and not callable(getattr(worker, "cancel_and_join", None)):
            raise ActorRefused("source cutoff interface")
        with self._lock:
            if self._stopped:
                raise ActorStopped("source worker after stop")
            if self._source_worker is not None and self._source_worker_live:
                raise ActorRefused("source_worker_already_live")
            self._source_worker = worker
            self._source_worker_live = worker is not None
            self._attach_generation += 1
            self._cutoff_ack = False

    def stop_workers(self, timeout_s: float = 2.0) -> bool:
        """Cancel/join ALL current workers, including any attached during cutoff."""
        joined: set[int] = set()
        errors: list[BaseException] = []
        ok = True
        reason = None
        while True:
            with self._lock:
                generation = self._attach_generation
                workers = [w for w in (self._worker, self._source_worker)
                           if w is not None and id(w) not in joined]
            for worker in workers:
                joined.add(id(worker))
                try:
                    if not bool(worker.cancel_and_join(timeout_s)):
                        ok = False
                        reason = "worker_cutoff_unconfirmed"
                        continue
                    confirmed = getattr(worker, "shutdown_confirmed", None)
                    if callable(confirmed):
                        confirmed = confirmed()
                    if confirmed is False:
                        ok = False
                        reason = "source_worker_shutdown_unconfirmed"
                except BaseException as error:  # noqa: BLE001 - join every worker first
                    ok = False
                    reason = "worker_cutoff_exception"
                    errors.append(error)
            with self._lock:
                if generation == self._attach_generation:
                    self._cutoff_ack = ok
                    self._cutoff_failure_reason = None if ok else reason
                    if ok:
                        self._worker_live = False
                        self._source_worker_live = False
                    break
        if errors:
            raise errors[0]
        return ok

    def stop_worker(self, timeout_s: float = 2.0) -> bool:
        return self.stop_workers(timeout_s)

    @property
    def cutoff_acknowledged(self) -> bool:
        with self._lock:
            return self._cutoff_ack

    def begin_export(self) -> None:
        """Freeze the export window before the payload is captured."""
        with self._lock:
            if self._exported:
                raise ActorStopped("already exported")
            self._local_window_epoch += 1
            self._exporting = True

    def abort_export(self) -> None:
        with self._lock:
            if self._exported:
                return
            self._exporting = False

    def mark_exported(self) -> None:
        with self._lock:
            self._exporting = False
            self._exported = True

    def commit_export(self) -> None:
        """The verified snapshot is now the immutable target."""
        with self._lock:
            self._export_committed = True

    @property
    def is_stopped(self) -> bool:
        with self._lock:
            return self._stopped

    @property
    def is_exported(self) -> bool:
        with self._lock:
            return self._exported

    @property
    def is_frozen(self) -> bool:
        with self._lock:
            return self._stop_snapshot is not None

    @property
    def cutoff_failure_reason(self) -> str | None:
        with self._lock:
            return self._cutoff_failure_reason

    def late_results(self) -> dict[str, str]:
        return {}

    def late_sources(self) -> list:
        with self._lock:
            return [list(row) for row in self._late_sources]

    def finalize_export(self, expected_results, expected_sources) -> None:
        """Atomically compare late data and close the export window."""
        with self._lock:
            if expected_results or self.late_sources() != expected_sources:
                raise ActorStopped("late data during export")
            self._exporting = False
            self._exported = True

    # ---- source ownership (AcceptedSource contract) ---------------------
    def bind_source(self, source, *, session_epoch: int) -> None:
        with self._lock:
            if self._source_worker is None:
                raise ActorRefused("source_worker_required")
            if not callable(getattr(source, "accepted_portions", None)):
                raise ActorRefused("source immutable journal accessor required")
            self._source.bind_source(source, session_epoch=session_epoch)

    def _accepted_row_at(self, index, source):
        """Return the bound source's immutable row at index, or None when absent."""
        accessor = getattr(source, "accepted_portions", None)
        if not callable(accessor):
            raise SourceRefused("accepted journal accessor unavailable")
        try:
            rows = accessor(index, index + 1)
        except (ValueError, IndexError):
            return None
        except Exception as error:  # noqa: BLE001 - accessor refusal is closed
            raise SourceRefused("accepted journal unavailable") from error
        if not isinstance(rows, tuple) or len(rows) > 1:
            raise SourceRefused("accepted journal range")
        return rows[0] if rows else None

    def _verify_accepted_row(self, row, index, body, speaker_id, timestamp, *,
                             body_hash: str, session_epoch: int) -> None:
        try:
            self._verify_accepted_row_fields(
                row, index, body, speaker_id, timestamp,
                body_hash=body_hash, session_epoch=session_epoch)
        except (AttributeError, TypeError, KeyError) as error:
            raise SourceRefused("accepted journal row malformed") from error

    def _verify_accepted_row_fields(self, row, index, body, speaker_id, timestamp, *,
                                    body_hash: str, session_epoch: int) -> None:
        if hasattr(row, "body"):
            if (getattr(row, "index", None) != index
                    or row.body != body or row.stamp != timestamp
                    or speaker_id != "speaker:" + hashlib.sha256(
                        str(getattr(row, "speaker", "")).encode()).hexdigest()):
                raise SourceRefused("accepted source identity changed")
            return
        if (row.session_epoch != session_epoch or row.body_utf8 != body
                or row.speaker_id != speaker_id or row.timestamp != timestamp
                or row.body_hash != body_hash):
            raise SourceRefused("accepted source identity changed")

    def _verify_immutable_accepted(self, index, body, speaker_id, timestamp, *,
                                   body_hash: str, source, session_epoch: int) -> None:
        row = self._accepted_row_at(index, source)
        if row is None:
            raise SourceRefused("accepted journal range")
        self._verify_accepted_row(row, index, body, speaker_id, timestamp,
                                  body_hash=body_hash, session_epoch=session_epoch)

    def append_source_at(self, index, body, speaker_id, timestamp="", *,
                         session_epoch: int, body_hash: str, source, speaker_label: str):
        with self._lock:
            if self._exported:
                raise ActorStopped("late source after finalized export")
            if self._exporting:
                prefix = self._source.portion_count
                staged = self._late_sources
                expected = prefix + len(staged)
                if index < expected:
                    self._source.validate_late_authentic_at(
                        index, body, speaker_id, timestamp, session_epoch=session_epoch,
                        body_hash=body_hash, source=source, speaker_label=speaker_label)
                    if index < prefix:
                        self._verify_immutable_accepted(
                            index, body, speaker_id, timestamp, body_hash=body_hash,
                            source=source, session_epoch=session_epoch)
                        raise ActorStopped("late source duplicate of accepted portion")
                    row = staged[index - prefix]
                    if (row[1], row[2], row[3], row[4], row[5], row[6]) != (
                            body, speaker_id, timestamp, session_epoch, body_hash,
                            speaker_label):
                        raise SourceRefused("conflicting late source row")
                    raise ActorStopped("late source duplicate of staged row")
                if index != expected:
                    raise SourceRefused("nonmonotonic late source index")
                self._source.validate_late_at(
                    index, body, speaker_id, timestamp, session_epoch=session_epoch,
                    body_hash=body_hash, source=source, speaker_label=speaker_label,
                    expected_index=expected)
                accepted = self._accepted_row_at(index, source)
                if accepted is not None:
                    self._verify_accepted_row(
                        accepted, index, body, speaker_id, timestamp,
                        body_hash=body_hash, session_epoch=session_epoch)
                    self._late_sources.append(
                        [index, body, speaker_id, timestamp, session_epoch,
                         body_hash, speaker_label])
                    raise ActorStopped("late source during export")
                if callable(getattr(source, "accepted_portions", None)):
                    raise SourceRefused("late source not in accepted journal")
                self._late_sources.append(
                    [index, body, speaker_id, timestamp, session_epoch,
                     body_hash, speaker_label])
                raise ActorStopped("late source during export")
            staged_at = None
            for position, row in enumerate(self._late_sources):
                if row[0] == index:
                    staged_at = position
                    if row[1:] != [body, speaker_id, timestamp, session_epoch,
                                   body_hash, speaker_label]:
                        raise SourceRefused("conflicting staged late source")
                    break
            self._source.validate_source_at(
                index, body, speaker_id, timestamp, session_epoch=session_epoch,
                body_hash=body_hash, source=source, speaker_label=speaker_label)
            accepted = self._accepted_row_at(index, source)
            if accepted is None:
                raise SourceRefused("source row is not in the accepted journal")
            self._verify_accepted_row(
                accepted, index, body, speaker_id, timestamp,
                body_hash=body_hash, session_epoch=session_epoch)
            before = self._source.portion_count
            portion = self._source.append_source_at(
                index, body, speaker_id, timestamp, session_epoch=session_epoch,
                body_hash=body_hash, source=source, speaker_label=speaker_label)
            if staged_at is not None:
                del self._late_sources[staged_at]
            if self._stopped and index >= before:
                self._late.append(portion)
            return portion

    def source_export(self) -> dict:
        with self._lock:
            export = self._source.export()
            late_ids = {p.id for p in self._late}
            export["portions"] = [row for row in export["portions"]
                                  if row["id"] not in late_ids]
            export["late_portions"] = [{"id": p.id, "session_epoch": p.session_epoch,
                                        "body_utf8": p.body_utf8, "speaker_id": p.speaker_id,
                                        "timestamp": p.timestamp, "body_hash": p.body_hash}
                                       for p in self._late]
            export["stopped"] = self._stopped
            return export

    # ---- manual questions: validate first, invalidate under same lock --
    def manual_request(self, manual_id: str, question: str, *, topic_id: str = "manual",
                       topic_label: str = "Manual question", position: int = 0) -> str:
        if not isinstance(manual_id, str) or not manual_id or len(manual_id) > 96:
            raise ActorRefused("manual id")
        if (not isinstance(question, str) or not question.strip()
                or len(question.encode("utf-8")) > 8000 or "\x00" in question):
            raise ActorRefused("manual question")
        if not isinstance(topic_id, str) or len(topic_id) > 96:
            raise ActorRefused("manual topic")
        if not isinstance(topic_label, str) or len(topic_label) > 256:
            raise ActorRefused("manual topic label")
        try:
            _string(topic_id, 128)
            _string(topic_label, 1024)
        except ContractError as error:
            raise ActorRefused("manual topic text") from error
        if type(position) is not int or not 0 <= position <= 1_000_000:
            raise ActorRefused("manual position")
        with self._lock:
            if self._stopped:
                raise ActorStopped("manual after stop")
            existing = self._canvas.question_by_manual(manual_id)
            if existing is not None:
                if existing.text != question:
                    raise ActorRefused("manual question already recorded")
                return existing.question_id
            if any(block.topic_id == topic_id and block.topic_label != topic_label
                   for block in self._canvas.snapshot()):
                raise ActorRefused("manual topic label changed")
            qid = f"q:manual:{self._epoch}:{len(self.semantic.state.local_events_json)}"
            event = {"kind": "question", "topic_id": topic_id, "topic_label": topic_label,
                     "text": question, "position": position, "question_id": qid,
                     "manual_id": manual_id}
            ledger = Question(qid, question, topic_id, (), _hash(question), 1,
                              ((f"scope:{qid}:0", question, "open"),), True)
            self.semantic.record_local_event(event, question=ledger)
            self._local_window_epoch += 1
            return qid

    def manual_answer(self, manual_id: str, answer: str, *, position: int | None = None,
                      expected_manual_generation: int | None = None,
                      cancel_event: threading.Event | None = None):
        if (not isinstance(answer, str) or not answer.strip()
                or len(answer.encode("utf-8")) > 8000 or "\x00" in answer):
            raise ActorRefused("manual answer")
        with self._lock:
            if self._stopped:
                raise ActorStopped("manual answer after stop")
            if cancel_event is not None and cancel_event.is_set():
                raise ActorRefused("manual answer cancelled")
            if (expected_manual_generation is not None
                    and self.semantic.state.manual_generation != expected_manual_generation):
                raise ActorRefused("manual answer superseded")
            existing = self._canvas.answer_by_manual(manual_id)
            if existing is not None:
                if existing.text == answer:
                    return existing
                raise ActorRefused("manual answer already recorded")
            question = self._canvas.question_by_manual(manual_id)
            if question is None:
                raise ActorRefused("manual answer without question")
            if position is not None and (type(position) is not int or not 0 <= position <= 1_000_000):
                raise ActorRefused("manual position")
            event = {"kind": "answer", "topic_id": question.topic_id,
                     "topic_label": question.topic_label, "text": answer,
                     "position": question.position if position is None else max(position, question.position),
                     "question_id": question.question_id, "manual_id": manual_id,
                     "answer_id": f"legacy-answer:{question.question_id}", "status": "unknown"}
            self.semantic.record_local_event(event)
            return self._canvas.answer_by_manual(manual_id)

    @property
    def manual_generation(self) -> int:
        with self._lock:
            return self.semantic.state.manual_generation

    # ---- stop & snapshot export -----------------------------------------
    def begin_stop(self) -> tuple:
        with self._lock:
            if self._stopped:
                raise ActorStopped("stop already begun")
            self._local_window_epoch += 1
            self.semantic.stop()
            self._stopped = True
            return ()

    def lock_owned(self) -> bool:
        """True only inside the actor lock; retirement/join must be outside."""
        return bool(getattr(self._lock, "_is_owned", lambda: False)())

    def freeze_snapshot(self):
        with self._lock:
            if not self._stopped:
                raise ActorRefused("freeze before stop")
            if self._stop_snapshot is not None:
                return self._stop_snapshot
            version, blocks = self._canvas.snapshot_versioned()
            self._stop_snapshot = (version, blocks)
            return self._stop_snapshot

    def snapshot(self):
        with self._lock:
            if self._stop_snapshot is not None:
                return self._stop_snapshot[0], self._stop_snapshot[1]
            return self._canvas.snapshot_versioned()

    def snapshot_bundle(self):
        """Capture published blocks and complete canonical export under one lock."""
        with self._lock:
            return self.snapshot(), self.export()

    def system_event(self, text: str, *, position: int = 0):
        with self._lock:
            if self._stopped:
                raise ActorStopped("system event after stop")
            if type(position) is not int or not 0 <= position <= 1_000_000:
                raise ActorRefused("system position")
            event = {"kind": "system", "topic_id": "", "topic_label": "",
                     "text": _clean(text), "position": position, "system_after": True}
            self.semantic.record_local_event(event)
            return self._canvas.version

    def export(self) -> dict:
        with self._lock:
            version, blocks = self.snapshot()
            return {"version": 1, "session_epoch": self._epoch, "stopped": self._stopped,
                    "canvas_version": version,
                    "blocks": [b.to_dict() for b in blocks],
                    "source": self.source_export(),
                    "semantic": self.semantic_export(),
                    "pending": {},
                    "late_results": {},
                    "late_sources": self.late_sources(),
                    "manual_generation": self.manual_generation,
                    "durability": "in_process_only; no crash restore"}
