"""Exact Stop handoff and guarded publication of the refined debrief.

This protocol deliberately never discovers identity through legacy live sidecars.
A dedicated binding is established only by the nonce supplied to the Stop child.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import pathlib
import re
import secrets
import stat
import time
import unicodedata

from charoite_graph import safe_write
import debrief_refinement as refinement
import live_sidecar
import graphs
import meeting_source
import meeting_stamp

BINDING_SUFFIX = ".debrief-binding.json"
_CAPTURE = contextvars.ContextVar("debrief_final_capture", default=None)


def enabled(cfg: dict) -> bool:
    return (cfg.get("sufler") or {}).get("refined_debrief") is True


def binding_path(live: pathlib.Path) -> pathlib.Path:
    return live.with_name(live.name + BINDING_SUFFIX)


def _equivalent_name(first: str, second: str) -> bool:
    return unicodedata.normalize("NFC", first) == unicodedata.normalize("NFC", second)


def _file_stat(path: pathlib.Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _same_entry(first: pathlib.Path, second: pathlib.Path) -> bool:
    """An NFC/NFD alias of one directory entry must never be unlinked."""
    if first == second:
        return True
    if first.parent != second.parent or not _equivalent_name(first.name, second.name):
        return False
    left, right = _file_stat(first), _file_stat(second)
    if not left or not right or not stat.S_ISREG(left.st_mode) or not os.path.samestat(left, right):
        return False
    if left.st_nlink == 1:
        return True
    # A crash after link() on a byte-sensitive volume can leave two genuine
    # hardlinks. Only unlink when both exact directory entries are present.
    names = {first.name, second.name}
    with os.scandir(first.parent) as entries:
        for entry in entries:
            names.discard(entry.name)
            if not names:
                return False
    return True


def _bound_file(expected: pathlib.Path, recorded: object, refusal: str, *, missing_ok: bool = False) -> pathlib.Path:
    """Use only the two supplied spellings, and refuse distinct owners."""
    if recorded == expected.name:
        return expected
    if isinstance(recorded, str) and _equivalent_name(recorded, expected.name):
        stored = expected.with_name(recorded)
        current, previous = _file_stat(expected), _file_stat(stored)
        if all(stat.S_ISREG(st.st_mode) for st in (current, previous) if st is not None):
            if current is None:
                if previous is not None:
                    return stored
                if missing_ok:
                    return expected
            elif previous is None or _same_entry(expected, stored):
                return expected
    raise refinement.RefinementRefused(refusal)


def _named_debrief(live: pathlib.Path, name: str, *, missing_ok: bool = False) -> pathlib.Path:
    expected = live.with_name(live.stem + meeting_stamp.DEBRIEF_FILE_SUFFIX)
    parsed = meeting_stamp.decompose(live.stem)
    if parsed and parsed[1]:
        return _bound_file(expected, name, "debrief_binding_path", missing_ok=missing_ok)
    # Untitled second-precision meetings can own a minute-precision derivative.
    return live.with_name(name)


def handoff_path(live: pathlib.Path) -> pathlib.Path:
    return live.with_name(live.name + ".debrief-handoff.json")


def remember_handoff(live: pathlib.Path, handoff: dict) -> None:
    """Persist the explicit child identity before STT, without legacy lookup."""
    _check_handoff(handoff)
    if live.name != handoff["owner"]:
        raise refinement.RefinementRefused("stop_owner")
    path = handoff_path(live)
    if path.exists():
        if _read_json(path) != handoff:
            raise refinement.RefinementRefused("handoff_conflict")
    else:
        _exclusive_json(path, handoff)


def pending_handoff(live: pathlib.Path) -> dict | None:
    if not handoff_path(live).exists():
        return None
    value = _read_json(handoff_path(live))
    _check_handoff(value)
    if value["owner"] != live.name:
        raise refinement.RefinementRefused("stop_owner")
    return value


def publication_path(live: pathlib.Path) -> pathlib.Path:
    return live.with_name(live.name + ".debrief-publication.json")


def _file_identity(st: os.stat_result) -> list[int]:
    return [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size]


def _sync_files_and_parent(path: pathlib.Path, *siblings: pathlib.Path) -> None:
    """Flush each result and then their shared directory, before discarding rollback."""
    for result in (path, *siblings):
        fd = os.open(result, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    _sync_directory(path.parent)


def _sync_directory(path: pathlib.Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _identity_json(value: dict) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text.encode("utf-8")) > 65536:
        raise refinement.RefinementRefused("identity_too_large")
    return text


def _update_publication(live: pathlib.Path, journal: dict, updates: dict) -> dict:
    """Durably record the exact inode before installation or compensation."""
    path = publication_path(live)
    before = safe_write.stat_snapshot(path)
    if _read_json(path) != journal:
        raise refinement.RefinementRefused("publication_journal_changed")
    updated = {**journal, **updates}
    text = _identity_json(updated)
    if not safe_write.write_text(path, text, expect=before, mode=0o600):
        raise refinement.RefinementRefused("publication_journal_changed")
    _sync_files_and_parent(path)
    return updated


def recover_publication(live: pathlib.Path, *, rejected: bool = False, finalize: bool = False,
                        parent_ack: dict | None = None) -> None:
    """Compensate an interrupted write only while the candidate bytes are ours.

    The journal is durable before replacing the public file. A committed binding
    carries the same publication id; otherwise a new process restores the prior
    bytes. Concurrent human bytes are never restored over.
    """
    journal_path = publication_path(live)
    try:
        journal = _read_json(journal_path)
    except refinement.RefinementRefused as error:
        if isinstance(error.__cause__, FileNotFoundError):
            if parent_ack is not None:
                raise refinement.RefinementRefused("parent_publication_identity") from error
            return
        raise
    if parent_ack is not None:
        _check_parent_journal(journal, parent_ack)
    name = journal.get("file")
    backup = journal.get("backup")
    _bound_file(live, journal.get("owner"), "publication_journal")
    if (not isinstance(name, str)
            or pathlib.Path(name).name != name
            or (backup is not None and (not isinstance(backup, str) or pathlib.Path(backup).name != backup))):
        raise refinement.RefinementRefused("publication_journal")
    if "installed_identity" not in journal:
        # The writer had not reached the pre-replace callback. Even matching
        # bytes at the destination belong to someone else.
        _discard_publication(live, journal)
        return
    rejected = rejected or (journal.get("pending_finalization") is True and not finalize)
    # A rejected run must remain rejected even if reading one of its results
    # fails and a later process has to finish compensation.
    if rejected and not (journal.get("rejected") or journal.get("rollback_started")):
        journal = _update_publication(live, journal, {"rejected": True})
    binding = _read_json(binding_path(live))
    destination = _named_debrief(live, name, missing_ok=True)
    passport = live.with_name(live.name + ".live.json")
    rejected = rejected or journal.get("rejected") is True or journal.get("rollback_started") is True
    invalid_commit = False
    if not rejected and binding.get("publication_id") == journal.get("id"):
        # Keep the journal throughout the durability barrier. Transient I/O
        # errors leave recovery retryable, without accepting a partial result.
        try:
            _sync_files_and_parent(destination, passport, binding_path(live))
            verify_publication(live)
        except FileNotFoundError as error:
            if error.filename != str(destination):
                raise
            rejected = invalid_commit = True
        except ValueError as error:  # the identity, export or UTF-8 proof no longer matches
            if isinstance(error.__cause__, OSError) and not isinstance(error.__cause__, FileNotFoundError):
                raise
            rejected = invalid_commit = True
    if rejected or binding.get("publication_id") != journal.get("id"):
        try:
            st = destination.stat()
        except FileNotFoundError:
            st = None
        installed = st is not None and _file_identity(st) == journal["installed_identity"]
        if (installed or binding.get("publication_id") == journal.get("id")) and not journal.get("rollback_started"):
            journal = _update_publication(live, journal, {"rollback_started": True})
        if installed and hashlib.sha256(destination.read_bytes()).hexdigest() == journal["candidate_sha256"]:
            current = safe_write.snapshot_of(st)
            if backup is None:
                if safe_write.stat_snapshot(destination) != current:
                    raise refinement.RefinementRefused("rollback_race")
                destination.unlink()
            else:
                previous = (live.parent / ".prev" / backup).read_bytes().decode("utf-8")
                if live_sidecar.sha(previous) != journal["previous_sha256"]:
                    raise refinement.RefinementRefused("rollback_backup_changed")
                if not safe_write.write_text(destination, previous, expect=current):
                    raise refinement.RefinementRefused("rollback_race")
        # Recovery may itself die after restoring the file. Finish the passport
        # on the next attempt even when the candidate bytes are already gone.
        if not journal.get("rollback_started"):
            _discard_publication(live, journal)
            return
        try:
            current_bytes = destination.read_bytes()
        except FileNotFoundError:
            current_bytes = None
        restored = (current_bytes is None if backup is None else current_bytes is not None
                    and hashlib.sha256(current_bytes).hexdigest() == journal["previous_sha256"])
        if restored:
            snapshot = safe_write.stat_snapshot(passport)
            meta = live_sidecar.read_direct(live)
            if meta is None or snapshot is None:
                raise refinement.RefinementRefused("rollback_passport_unreadable")
            if meta.get("debrief_sha256") == journal["candidate_sha256"]:
                for key in ("debrief_sha256", "debrief_source_sha256"):
                    if key in journal["passport"]:
                        meta[key] = journal["passport"][key]
                    else:
                        meta.pop(key, None)
                if not safe_write.write_text(passport, json.dumps(meta), expect=snapshot):
                    raise refinement.RefinementRefused("rollback_passport_race")
        if binding.get("publication_id") == journal.get("id"):
            snapshot = safe_write.stat_snapshot(binding_path(live))
            if _read_json(binding_path(live)) != binding or not safe_write.write_text(
                    binding_path(live), json.dumps(journal["binding"]), expect=snapshot):
                raise refinement.RefinementRefused("rollback_binding_race")
        # The shared directory barrier also persists an absent destination.
        restored_paths = [destination] if restored and current_bytes is not None else []
        _sync_files_and_parent(passport, binding_path(live), *restored_paths)
    _discard_publication(live, journal)
    if invalid_commit:
        raise refinement.RefinementRefused("publication_unconfirmed")


def _discard_publication(live: pathlib.Path, journal: dict) -> None:
    """Clean only our journal and private backup when no compensation is needed."""
    if journal.get("pending_finalization") is True:
        pointer = _parent_ack_path(live, journal["graph_input_sha"])
        if pointer.exists() and _read_json(pointer) == _parent_ack(journal):
            pointer.unlink()
        # The journal remains the recovery record until pointer removal is
        # durable, including a retry after an interrupted directory fsync.
        _sync_directory(live.parent)
    publication_path(live).unlink()
    # Only remove the backup once the journal deletion is durable. Failure
    # after unlink can leave a private orphan, without changing the outcome.
    try:
        _sync_directory(live.parent)
        if journal["backup"] is not None:
            (live.parent / ".prev" / journal["backup"]).unlink(missing_ok=True)
    except OSError as error:
        logging.getLogger(__name__).warning("Private debrief rollback cleanup deferred (%s)", type(error).__name__)


def new_handoff(live: pathlib.Path, stamp: str) -> dict:
    return {"nonce": secrets.token_hex(24), "stamp": stamp,
            "file": live.name + ".grounded.json", "owner": live.name}


def arguments(handoff: dict | None) -> list[str]:
    return ["--debrief-handoff", json.dumps(handoff)] if handoff is not None else []


def from_arguments(args: list[str]) -> dict | None:
    if "--debrief-handoff" not in args:
        return None
    try:
        return json.loads(args[args.index("--debrief-handoff") + 1])
    except (ValueError, IndexError) as error:
        raise refinement.RefinementRefused("stop_arguments") from error


def _check_handoff(value: dict) -> None:
    if (not isinstance(value, dict) or set(value) != {"nonce", "stamp", "file", "owner"}
            or not isinstance(value.get("nonce"), str)
            or re.fullmatch(r"[a-f0-9]{48}", value["nonce"]) is None
            or not isinstance(value.get("stamp"), str)
            or meeting_stamp.stamp_of(value["stamp"]) != value["stamp"]
            or value.get("owner") != value["stamp"] + ".md"
            or value.get("file") != value["owner"] + ".grounded.json"):
        raise refinement.RefinementRefused("stop_identity")


def receipt_path(live: pathlib.Path, handoff: dict) -> pathlib.Path:
    _check_handoff(handoff)
    return live.parent / (handoff["owner"] + ".stop-" + handoff["nonce"] + ".json")


def _exclusive_json(path: pathlib.Path, value: dict) -> None:
    """Publish a complete file once; readers never observe half a receipt."""
    data = _identity_json(value)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(8))
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)  # atomic, refuses an existing destination
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def finish_stop(live: pathlib.Path, handoff: dict, *, complete: bool) -> None:
    """Only the daemon calls this, after close/export returned successfully."""
    _check_handoff(handoff)
    if live.name != handoff["owner"]:
        raise refinement.RefinementRefused("stop_owner")
    value = {"handoff": handoff, "status": "failed"}
    if complete:
        value.update(status="ready", export=refinement.receipt(live.parent / handoff["file"], handoff["stamp"]))
    _exclusive_json(receipt_path(live, handoff), value)


def _read_json(path: pathlib.Path) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise refinement.RefinementRefused("identity_not_regular")
            data = stream.read(65537)
            if len(data) > 65536:
                raise refinement.RefinementRefused("identity_too_large")
        value = json.loads(data)
    except refinement.RefinementRefused:
        raise
    except (OSError, ValueError) as error:
        raise refinement.RefinementRefused("identity_unreadable") from error
    if not isinstance(value, dict):
        raise refinement.RefinementRefused("identity_invalid")
    return value


def _terminal(live: pathlib.Path, handoff: dict, *, timeout: float = 0) -> dict:
    path = receipt_path(live, handoff)
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    value = _read_json(path)
    if value.get("handoff") != handoff or value.get("status") != "ready":
        raise refinement.RefinementRefused("stop_incomplete")
    export = value.get("export")
    if (not isinstance(export, dict) or export.get("stamp") != handoff["stamp"]
            or export.get("file") != handoff["file"]):
        raise refinement.RefinementRefused("stop_export_identity")
    refinement.load_thread(live, export)
    return value


@contextlib.contextmanager
def capture_final(live: pathlib.Path):
    """In-process evidence of write_final, independent of inherited metadata."""
    proof = {"path": live, "sha256": None}
    token = _CAPTURE.set(proof)
    try:
        yield proof
    finally:
        _CAPTURE.reset(token)


def final_written(live: pathlib.Path, text: str) -> None:
    proof = _CAPTURE.get()
    if proof is not None and proof["path"] == live and live.read_text(encoding="utf-8") == text:
        proof["sha256"] = live_sidecar.sha(text)


def _source(live: pathlib.Path):
    text = live.read_text(encoding="utf-8")
    return live_sidecar.sha(text), meeting_source.of(live, text)


def _establish(live: pathlib.Path, handoff: dict | None, final_sha: str | None,
               timeout: float) -> tuple[dict, dict, safe_write.Snapshot | None]:
    refresh = None
    full_sha, source = _source(live)
    path = binding_path(live)
    if handoff is not None:
        _check_handoff(handoff)
        if live.name != handoff["owner"] or final_sha != full_sha:
            raise refinement.RefinementRefused("canonical_final_unproven")
        terminal = _terminal(live, handoff, timeout=timeout)
        now_sha, now_source = _source(live)
        if now_sha != full_sha or now_source.sha() != source.sha():
            raise refinement.RefinementRefused("source_changed")
        value = {"owner": live.name, "handoff": handoff, "full_sha256": full_sha,
                 "source_sha256": source.sha()}
        if path.exists():
            established = _read_json(path)
            if any(established.get(key) != item for key, item in value.items()):
                raise refinement.RefinementRefused("binding_conflict")
            value = established
        else:
            _exclusive_json(path, value)
    else:
        before = safe_write.stat_snapshot(path)
        value = _read_json(path)
        _bound_file(live, value.get("owner"), "binding_owner")
        handoff = value.get("handoff")
        _check_handoff(handoff)
        terminal = _terminal(live, handoff)
        # A new proven rebuild may change the speech. A no-op/human-edit path may not.
        if value.get("full_sha256") != full_sha or value.get("source_sha256") != source.sha():
            if final_sha != full_sha:
                raise refinement.RefinementRefused("canonical_final_unproven")
            value = {**value, "full_sha256": full_sha, "source_sha256": source.sha()}
            if before is None:
                raise refinement.RefinementRefused("binding_changed")
            refresh = before
    return value, terminal, refresh


def _inputs(live, cfg, handoff, final_sha, timeout):
    if not enabled(cfg):
        raise refinement.RefinementRefused("feature_disabled")
    recover_retitle(live)
    recover_publication(live)
    binding, terminal, refresh = _establish(live, handoff, final_sha, timeout)
    full_sha, source = _source(live)
    if full_sha != binding["full_sha256"] or source.sha() != binding["source_sha256"]:
        raise refinement.RefinementRefused("source_changed")
    meta = live_sidecar.read_direct(live) or {}
    if (meta.get("stamp") != binding["handoff"]["stamp"]
            or meta.get("transcript_sha256") != full_sha):
        raise refinement.RefinementRefused("canonical_passport")
    destination = meeting_stamp.derivative_path(live, "debrief", graphs.graph_dir(cfg))
    destination = _bound_file(destination, binding.get("debrief_file", destination.name), "debrief_binding_path")
    before = safe_write.stat_snapshot(destination)
    state = live_sidecar.derivative_state(destination, meta, "debrief", source.sha())
    if state not in {live_sidecar.MISSING, live_sidecar.FRESH, live_sidecar.STALE}:
        raise refinement.RefinementRefused("debrief_not_owned")
    if refresh is not None and not safe_write.write_text(binding_path(live), json.dumps(binding), expect=refresh):
        raise refinement.RefinementRefused("binding_changed")
    return binding, terminal, source, meta, destination, before


def _graph_input_sha(binding, terminal, previous_snapshot) -> str:
    # A retitle changes the path and header, but preserves the speech, Stop
    # identity, export and previous derivative inode (including its absence).
    return live_sidecar.sha(json.dumps([binding["handoff"], binding["source_sha256"], terminal, previous_snapshot],
                                      sort_keys=True))


def prepare_for_graph(live: pathlib.Path, cfg: dict, *, handoff: dict | None,
                      final_sha: str | None, timeout: float = 45) -> str:
    """Bind the proven rebuild before graph work, without installing a candidate."""
    binding, terminal, _, _, destination, before = _inputs(live, cfg, handoff, final_sha, timeout)
    if before is not None and not binding.get("debrief_file"):
        # The first refinement may follow a legacy machine debrief. Retitle
        # must carry that exact prior file even when its name uses minute precision.
        path = binding_path(live)
        snapshot = safe_write.stat_snapshot(path)
        previous_sha = hashlib.sha256(destination.read_bytes()).hexdigest()
        with_previous = {**binding, "debrief_file": destination.name, "debrief_sha256": previous_sha}
        if (snapshot is None or _read_json(path) != binding or safe_write.stat_snapshot(destination) != before
                or not safe_write.write_text(path, json.dumps(with_previous), expect=snapshot)):
            raise refinement.RefinementRefused("binding_changed")
    _sync_files_and_parent(binding_path(live))
    handoff_path(live).unlink(missing_ok=True)
    return _graph_input_sha(binding, terminal, before)


def run(live: pathlib.Path, cfg: dict, *, handoff: dict | None, final_sha: str | None,
        before_request, progress=None, timeout: float = 45, before_commit=None,
        graph_input_sha: str | None = None, defer_finalization: bool = False) -> pathlib.Path:
    """Refuse without changing the previous debrief unless every input is proven."""
    binding, terminal, source, meta, destination, before = _inputs(live, cfg, handoff, final_sha, timeout)
    if graph_input_sha is not None and _graph_input_sha(binding, terminal, before) != graph_input_sha:
        raise refinement.RefinementRefused("graph_inputs_changed")
    if defer_finalization and graph_input_sha is None:
        raise refinement.RefinementRefused("parent_ack_without_graph_proof")
    full_sha = binding["full_sha256"]
    previous = destination.read_bytes().decode("utf-8") if before is not None else ""
    thread = refinement.load_thread(live, terminal["export"])
    model_previous = previous.replace("\r\n", "\n").replace("\r", "\n")
    body = refinement.refine(source.speech, thread, model_previous, cfg,
                             before_request=before_request, progress=progress)
    body = meeting_source.finish_debrief(body, source)
    if not body.strip():
        raise refinement.RefinementRefused("empty_refinement")

    def unchanged():
        _bound_file(live, binding["owner"], "binding_owner")
        if "debrief_file" in binding:
            _named_debrief(live, binding["debrief_file"])
        current_full, current_source = _source(live)
        if (current_full != full_sha or current_source.sha() != source.sha()
                or _read_json(binding_path(live)) != binding
                or _terminal(live, binding["handoff"]) != terminal):
            raise refinement.RefinementRefused("source_changed")

    unchanged()
    transaction = secrets.token_hex(16)
    backup = None
    if before is not None:
        backup = destination.name + ".rollback-" + transaction
        backup_path = live.parent / ".prev" / backup
        if not safe_write.write_text(backup_path, previous, expect_absent=True, mode=0o600):
            raise refinement.RefinementRefused("rollback_backup_conflict")
        try:
            _sync_files_and_parent(backup_path)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
    journal = {"id": transaction, "pending_finalization": defer_finalization,
               "graph_input_sha": graph_input_sha, "owner": live.name, "file": destination.name, "binding": binding,
               "candidate_sha256": live_sidecar.sha(body), "backup": backup,
               "previous_sha256": live_sidecar.sha(previous),
               "passport": {key: meta[key] for key in ("debrief_sha256", "debrief_source_sha256") if key in meta}}
    try:
        _exclusive_json(publication_path(live), journal)
        if defer_finalization:
            _exclusive_json(_parent_ack_path(live, graph_input_sha), _parent_ack(journal))
    except Exception:
        # link may have succeeded before directory fsync failed. A visible
        # journal keeps its backup until recovery has discarded both safely.
        if backup is not None and not publication_path(live).exists():
            (live.parent / ".prev" / backup).unlink(missing_ok=True)
        raise

    def record_installation(snapshot):
        nonlocal journal
        journal = _update_publication(live, journal, {"installed_identity": _file_identity(snapshot)})
        unchanged()

    before_binding = safe_write.stat_snapshot(binding_path(live))
    outcome = None
    write_started = False
    try:
        unchanged()
        write_started = True
        outcome = live_sidecar.write_derivative(live, destination, "debrief", body, source.sha(),
                                               expect=before, expect_absent=before is None,
                                               before_replace=record_installation)
        unchanged()
        fresh_meta = live_sidecar.read_direct(live) or {}
        if (outcome.state != live_sidecar.FRESH
                or live_sidecar.derivative_state(destination, fresh_meta, "debrief", source.sha()) != live_sidecar.FRESH):
            raise refinement.RefinementRefused("publication_unconfirmed")
        if before_commit is not None:
            # The graph-less post-hook historically observes the candidate.
            # A crash in that hook must still have an uncommitted journal.
            before_commit()
            unchanged()
        published = {**binding, "debrief_file": destination.name, "debrief_sha256": live_sidecar.sha(body),
                     "publication_id": transaction}
        if not safe_write.write_text(binding_path(live), json.dumps(published), expect=before_binding):
            raise refinement.RefinementRefused("binding_changed")
        current_full, current_source = _source(live)
        if (current_full != full_sha or current_source.sha() != source.sha()
                or _terminal(live, binding["handoff"]) != terminal):
            raise refinement.RefinementRefused("source_changed")
        handoff_path(live).unlink(missing_ok=True)
        if defer_finalization:
            _sync_files_and_parent(destination, live.with_name(live.name + ".live.json"), binding_path(live))
            verify_publication(live)
        else:
            recover_publication(live)
    except Exception:
        if not write_started or (outcome is not None and not outcome.written):
            # A RACE/PREV result proves we did not publish. An identical file
            # created concurrently still belongs to the other writer.
            _discard_publication(live, journal)
        else:
            recover_publication(live, rejected=True)
        raise
    return destination


def verify_publication(live: pathlib.Path) -> pathlib.Path:
    """Check again after graph work, before archive and the final ready status."""
    value = _read_json(binding_path(live))
    full_sha, source = _source(live)
    name = value.get("debrief_file")
    _bound_file(live, value.get("owner"), "publication_source_changed")
    if (not value.get("publication_id") or value.get("full_sha256") != full_sha
            or value.get("source_sha256") != source.sha()
            or not isinstance(name, str) or pathlib.Path(name).name != name):
        raise refinement.RefinementRefused("publication_source_changed")
    _terminal(live, value.get("handoff"))
    path = _named_debrief(live, name)
    meta = live_sidecar.read_direct(live) or {}
    if (hashlib.sha256(path.read_bytes()).hexdigest() != value.get("debrief_sha256")
            or meta.get("stamp") != value["handoff"]["stamp"]
            or meta.get("transcript_sha256") != full_sha
            or live_sidecar.derivative_state(path, meta, "debrief", source.sha()) != live_sidecar.FRESH):
        raise refinement.RefinementRefused("publication_changed")
    return path


def _parent_ack_path(live: pathlib.Path, proof: str) -> pathlib.Path:
    if not isinstance(proof, str) or re.fullmatch(r"[a-f0-9]{64}", proof) is None:
        raise refinement.RefinementRefused("parent_publication_identity")
    return live.parent / (".debrief-parent-" + proof + ".json")


def _parent_ack(journal: dict) -> dict:
    return {"owner": journal["owner"], "id": journal["id"], "proof": journal["graph_input_sha"],
            "handoff": journal["binding"]["handoff"]}


def _check_parent_journal(journal: dict, ack: dict) -> None:
    try:
        matches = journal.get("pending_finalization") is True and _parent_ack(journal) == ack
    except (KeyError, TypeError):
        matches = False
    if not matches:
        raise refinement.RefinementRefused("parent_publication_identity")


def parent_publication(original: pathlib.Path, proof: str, *, reject: bool = False) -> pathlib.Path | None:
    """Resolve the exact child through its proof/Stop-nonce pointer, never a glob."""
    pointer = _parent_ack_path(original, proof)
    try:
        ack = _read_json(pointer)
    except refinement.RefinementRefused as error:
        if reject and isinstance(error.__cause__, FileNotFoundError):
            return None
        raise
    owner = ack.get("owner")
    _check_handoff(ack.get("handoff"))
    if (set(ack) != {"owner", "id", "proof", "handoff"} or ack["proof"] != proof
            or not isinstance(owner, str) or pathlib.Path(owner).name != owner):
        raise refinement.RefinementRefused("parent_publication_identity")
    live = original.parent / owner
    journal = _read_json(publication_path(live))
    _check_parent_journal(journal, ack)
    if reject:
        recover_publication(live, parent_ack=ack)
    else:
        try:
            verify_publication(live)
            recover_publication(live, finalize=True, parent_ack=ack)
        except Exception:
            recover_publication(live, parent_ack=ack)
            raise
    return live



def move_retitle_file(origin: pathlib.Path, target: pathlib.Path) -> None:
    """A no-clobber move which can be resumed after either directory operation."""
    if _same_entry(origin, target) or not origin.exists():
        return
    if origin.is_symlink() or target.is_symlink():
        raise refinement.RefinementRefused("retitle_file_conflict")
    try:
        os.link(origin, target)
    except FileExistsError:
        if not origin.samefile(target):
            raise refinement.RefinementRefused("retitle_file_conflict") from None
    _sync_directory(target.parent)
    if not origin.samefile(target):
        raise refinement.RefinementRefused("retitle_file_conflict")
    origin.unlink()


def _retitled_binding(value: dict, old: pathlib.Path, new: pathlib.Path, full_sha: str) -> dict:
    updated = {**value, "owner": new.name, "full_sha256": full_sha}
    if not _same_entry(old, new) and value.get("debrief_file") is not None:
        updated["debrief_file"] = new.stem + meeting_stamp.DEBRIEF_FILE_SUFFIX
    return updated


def _retitle_reservation(value: dict, old: pathlib.Path, header: list | None) -> dict:
    pending = {**value, "retitle_header": header}
    if old.name != value["owner"]:
        # Once the transcript moves, a byte-sensitive volume needs the original
        # spelling to locate the old binding and passport during crash recovery.
        pending["retitle_from"] = old.name
    return pending


def prepare_retitle(old: pathlib.Path, new: pathlib.Path, *, header: tuple[str, str] | None = None) -> None:
    """Durably reserve the destination for this exact binding before moving files."""
    path = binding_path(old)
    if not path.exists():
        return
    snapshot = safe_write.stat_snapshot(path)
    value = _read_json(path)
    full_sha, source = _source(old)
    _bound_file(old, value.get("owner"), "retitle_source_changed")
    if (old.parent != new.parent or "retitle_header" in value
            or value.get("full_sha256") != full_sha or value.get("source_sha256") != source.sha()):
        raise refinement.RefinementRefused("retitle_source_changed")
    pending = _retitle_reservation(value, old, list(header) if header else None)
    pending_text = _identity_json(pending)
    # The post-header SHA-256 has the same UTF-8 length as the verified old hash.
    # Measure the actual final filenames before reserving or moving anything.
    _identity_json(_retitled_binding(value, old, new, full_sha))
    destination = binding_path(new)
    if _same_entry(old, new):
        if not safe_write.write_text(path, pending_text, expect=snapshot):
            raise refinement.RefinementRefused("binding_changed")
    elif not os.path.lexists(destination):
        _exclusive_json(destination, pending)
    elif _read_json(destination) != pending:
        raise refinement.RefinementRefused("retitle_binding_conflict")
    _sync_files_and_parent(destination)


def recover_retitle(live: pathlib.Path) -> None:
    """Finish a reserved retitle using only the named source and its proven files."""
    path = binding_path(live)
    if not path.exists():
        return
    before = safe_write.stat_snapshot(path)
    pending = _read_json(path)
    if "retitle_header" not in pending:
        return
    value = pending.copy()
    header = value.pop("retitle_header")
    if header is not None and (not isinstance(header, list) or len(header) != 2
                               or not all(isinstance(part, str) and part for part in header)):
        raise refinement.RefinementRefused("retitle_header")
    # The old digest authorizes only this exact title substitution. Derive the
    # two possible full-file hashes without accepting arbitrary source-equivalent edits.
    if live.is_symlink():
        raise refinement.RefinementRefused("retitle_source_changed")
    live_before = safe_write.stat_snapshot(live)
    body = live.read_text(encoding="utf-8")
    next_body = body
    previous_sha = value.get("full_sha256")
    if live_sidecar.sha(body) != previous_sha:
        original = body.replace(header[1], header[0], 1) if header else body
        if (live_sidecar.sha(original) != previous_sha
                or original.replace(header[0], header[1], 1) != body):
            raise refinement.RefinementRefused("retitle_source_changed")
    elif header:
        next_body = body.replace(header[0], header[1], 1)
    name = value.get("owner")
    if not isinstance(name, str) or pathlib.Path(name).name != name or not name.endswith(".md"):
        raise refinement.RefinementRefused("retitle_owner")
    old_name = value.pop("retitle_from", name)
    if not isinstance(old_name, str) or not _equivalent_name(old_name, name):
        raise refinement.RefinementRefused("retitle_owner")
    old = live.with_name(old_name)
    recorded = live.with_name(name)
    if old_name != name and os.path.lexists(recorded):
        if not (_same_entry(recorded, live) or _same_entry(recorded, old)):
            raise refinement.RefinementRefused("retitle_owner")
    if _same_entry(old, live):
        old = live
    full_sha = live_sidecar.sha(next_body)
    updated = _retitled_binding(value, old, live, full_sha)
    text = _identity_json(updated)
    passport = live.with_name(live.name + ".live.json")
    old_passport = old.with_name(old.name + ".live.json")
    owner = old if old_passport.exists() else live
    if meeting_source.of(owner, next_body).sha() != value.get("source_sha256"):
        raise refinement.RefinementRefused("retitle_source_changed")
    old_binding = binding_path(old)
    if old != live and old_binding.exists() and _read_json(old_binding) != value:
        raise refinement.RefinementRefused("retitle_binding_conflict")
    if old != live and os.path.lexists(old):
        # A crash can leave the two names of the very same inode. A distinct
        # source still present means the destination did not come from our move.
        if old.is_symlink() or not old.samefile(live) or not old_binding.exists():
            raise refinement.RefinementRefused("retitle_source_changed")
        move_retitle_file(old, live)
    if next_body != body and not safe_write.write_text(live, next_body, expect=live_before):
        raise refinement.RefinementRefused("retitle_source_changed")
    derivative = value.get("debrief_file")
    if derivative is not None:
        if not isinstance(derivative, str) or pathlib.Path(derivative).name != derivative:
            raise refinement.RefinementRefused("retitle_debrief_conflict")
        origin = _named_debrief(old, derivative, missing_ok=True)
        target = origin if old == live else live.with_name(updated["debrief_file"])
        existing = origin if origin.exists() else target
        if existing.is_symlink() or hashlib.sha256(existing.read_bytes()).hexdigest() != value.get("debrief_sha256"):
            raise refinement.RefinementRefused("retitle_debrief_changed")
        move_retitle_file(origin, target)
    passport_before = safe_write.stat_snapshot(old_passport if owner == old else passport)
    meta = live_sidecar.read_direct(owner) or {}
    if (old_passport.is_symlink() or passport.is_symlink() or meta.get("stamp") != value["handoff"]["stamp"]
            or meta.get("transcript_sha256") not in (value["full_sha256"], full_sha)):
        raise refinement.RefinementRefused("retitle_passport_changed")
    move_retitle_file(old_passport, passport)
    if meta["transcript_sha256"] != full_sha:
        if not safe_write.write_text(passport, json.dumps({**meta, "transcript_sha256": full_sha}, ensure_ascii=False),
                                     expect=passport_before):
            raise refinement.RefinementRefused("retitle_passport_changed")
    _sync_files_and_parent(live, passport)
    if old_binding != path and old_binding.exists():
        if _read_json(old_binding) != value:
            raise refinement.RefinementRefused("retitle_binding_conflict")
        old_binding.unlink()
        _sync_directory(path.parent)
    if not safe_write.write_text(path, text, expect=before):
        raise refinement.RefinementRefused("binding_changed")
    _sync_files_and_parent(path)


def can_retitle(old: pathlib.Path, new: pathlib.Path) -> bool:
    """Avoid a rename that would orphan the exact binding or overwrite a debrief."""
    if any(os.path.lexists(path) for path in (publication_path(old), handoff_path(old),
                                      publication_path(new), handoff_path(new))):
        return False
    if not os.path.lexists(binding_path(old)):
        return not os.path.lexists(binding_path(new))
    try:
        value = _read_json(binding_path(old))
        full_sha, source = _source(old)
        _bound_file(old, value.get("owner"), "retitle_source_changed")
        if (value.get("full_sha256") != full_sha
                or value.get("source_sha256") != source.sha()):
            return False
        if not _same_entry(old, new) and os.path.lexists(binding_path(new)):
            pending = _read_json(binding_path(new))
            if "retitle_header" not in pending or pending != _retitle_reservation(value, old, pending["retitle_header"]):
                return False
        name = value.get("debrief_file")
        if name is not None:
            if not isinstance(name, str) or pathlib.Path(name).name != name:
                return False
            origin = _named_debrief(old, name)
            target = new.with_name(new.stem + meeting_stamp.DEBRIEF_FILE_SUFFIX)
            if (hashlib.sha256(origin.read_bytes()).hexdigest() != value.get("debrief_sha256")
                    or (target.exists() and not _same_entry(origin, target))):
                return False
        return True
    except (OSError, refinement.RefinementRefused):
        return False


def retitle_binding(old: pathlib.Path, new: pathlib.Path, *, header_change: tuple[str, str] | None = None) -> None:
    """Move only the exact established binding; no minute or sidecar lookup."""
    if _same_entry(old, new) and binding_path(old).exists():
        value = _read_json(binding_path(old))
        if header_change is not None and header_change != (value.get("full_sha256"), _source(new)[0]):
            raise refinement.RefinementRefused("retitle_source_changed")
    recover_retitle(new)
