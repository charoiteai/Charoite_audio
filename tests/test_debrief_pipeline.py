"""Synthetic acceptance of exact Stop identity, publication, and background handoff."""
import ast
import hashlib
import json
import pathlib
import subprocess
import sys
import unicodedata
from types import SimpleNamespace

import pytest

import debrief_pipeline as pipeline
import debrief_refinement as refinement
import live_sidecar
import meeting_source
import meeting_stamp
from grounded_feed.export import write_snapshot

STAMP = "2026-01-01_120001"
SPEECH = "**Участник A** [12:00]:\nСначала проверим макет. Потом согласуем выпуск.\n"
CFG = {"sufler": {"refined_debrief": True, "graph": False}}


def make_meeting(tmp_path, stamp=STAMP):
    live = tmp_path / (stamp + ".md")
    live.write_text("# Встреча " + stamp + "\n\n" + SPEECH)
    meta = {"stamp": stamp, "transcript_sha256": live_sidecar.sha(live.read_text())}
    live.with_name(live.name + ".live.json").write_text(json.dumps(meta))
    handoff = pipeline.new_handoff(live, stamp)
    payload = {"version": 1, "session_epoch": stamp, "request_id": "close-" + stamp,
               "snapshot": {"complete": True, "source_drain_failure": None,
                            "producer_cutoff_failure": None,
                            "source": {"stopped": True, "portions": [
                                {"id": "p1", "body_utf8": SPEECH,
                                 "body_hash": hashlib.sha256(SPEECH.encode()).hexdigest()}]},
                            "blocks": [{"serial": 1, "kind": "paragraph", "text": "Проверяем макет.",
                                        "manual_id": None}]}}
    write_snapshot(tmp_path / handoff["file"], payload)
    pipeline.finish_stop(live, handoff, complete=True)
    return live, handoff


def run(live, handoff, **kwargs):
    return pipeline.run(live, CFG, handoff=handoff,
                        final_sha=live_sidecar.sha(live.read_text()),
                        before_request=lambda: None, timeout=0, **kwargs)


@pytest.fixture
def model(monkeypatch):
    seen = []
    def refine(speech, thread, previous, cfg, *, before_request, progress):
        before_request()
        seen.append((speech, thread, previous))
        return "# Разбор встречи\n\n## Ход разговора\n\nСначала проверят макет, затем согласуют выпуск.\n"
    monkeypatch.setattr(refinement, "refine", refine)
    return seen


def test_success_graph_off_and_repeat_preserves_previous(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    destination = run(live, handoff)
    assert destination == meeting_stamp.derivative_path(live, "debrief")
    original = destination.read_bytes()
    result = pipeline.run(live, CFG, handoff=None, final_sha=None,
                          before_request=lambda: None, timeout=0)
    assert result == destination
    assert live_sidecar.prev_path(live, destination).read_bytes() == original
    assert model[1][2] == original.decode()
    assert live_sidecar.derivative_state(destination, live_sidecar.read_direct(live), "debrief",
                                        meeting_source.of(live, live.read_text()).sha()) == live_sidecar.FRESH


@pytest.mark.parametrize("tamper", ["nonce", "stamp", "file", "owner"])
def test_explicit_identity_mismatch_never_calls_model(tmp_path, model, tamper):
    live, handoff = make_meeting(tmp_path)
    handoff = {**handoff, tamper: "wrong"}
    with pytest.raises(refinement.RefinementRefused):
        run(live, handoff)
    assert not model


def test_same_minute_orphan_sidecar_cannot_establish_binding(tmp_path, model):
    first, handoff = make_meeting(tmp_path)
    second = tmp_path / "2026-01-01_120002.md"
    second.write_text(first.read_text())  # even identical source bytes cannot transfer identity
    first.unlink()
    inherited = live_sidecar.read(first) or {}
    inherited["stamp"] = "2026-01-01_120002"
    inherited[refinement.RECEIPT_KEY] = refinement.receipt(tmp_path / handoff["file"], STAMP)
    second.with_name(second.name + ".live.json").write_text(json.dumps(inherited))
    with pytest.raises(refinement.RefinementRefused, match="identity_unreadable"):
        run(second, None)
    assert not model and not pipeline.binding_path(second).exists()


def test_terminal_receipt_is_exclusive_and_export_hash_is_checked(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    with pytest.raises(FileExistsError):
        pipeline.finish_stop(live, handoff, complete=True)
    export = tmp_path / handoff["file"]
    payload = json.loads(export.read_text())
    payload["snapshot"]["blocks"][0]["text"] = "Подмена"
    write_snapshot(export, payload)
    with pytest.raises(refinement.RefinementRefused, match="export_changed"):
        run(live, handoff)
    assert not model


@pytest.mark.parametrize("complete", [False, None])
def test_failed_or_missing_terminal_refuses_without_touching_debrief(tmp_path, model, complete):
    live, handoff = make_meeting(tmp_path)
    pipeline.receipt_path(live, handoff).unlink()
    if complete is False:
        pipeline.finish_stop(live, handoff, complete=False)
    with pytest.raises(refinement.RefinementRefused):
        run(live, handoff)
    assert not model


def test_machine_passport_alone_is_not_finalization_evidence(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    with pytest.raises(refinement.RefinementRefused, match="canonical_final_unproven"):
        pipeline.run(live, CFG, handoff=handoff, final_sha=None,
                     before_request=lambda: None, timeout=0)
    assert not model


def test_capture_records_only_actual_readback(tmp_path):
    live, _ = make_meeting(tmp_path)
    with pipeline.capture_final(live) as proof:
        pipeline.final_written(live, "not the file")
        assert proof["sha256"] is None
        pipeline.final_written(live, live.read_text())
    assert proof["sha256"] == live_sidecar.sha(live.read_text())


@pytest.mark.parametrize("owned", [False, True])
def test_existing_unknown_or_edited_debrief_is_preserved(tmp_path, model, owned):
    live, handoff = make_meeting(tmp_path)
    dest = meeting_stamp.derivative_path(live, "debrief")
    dest.write_text("Ручная правка\n")
    if owned:
        live_sidecar.attest(live, "debrief", "Прежний машинный текст\n", "0" * 64)
    with pytest.raises(refinement.RefinementRefused, match="debrief_not_owned"):
        run(live, handoff)
    assert dest.read_text() == "Ручная правка\n" and not model


def test_source_changed_during_model_refuses_publication(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    def changed(*args, **kwargs):
        live.write_text(live.read_text() + "Новая речь\n")
        return "# Разбор\nРезультат\n"
    monkeypatch.setattr(refinement, "refine", changed)
    with pytest.raises(refinement.RefinementRefused, match="source_changed"):
        run(live, handoff)
    assert not meeting_stamp.derivative_path(live, "debrief").exists()


def test_destination_created_during_model_is_preserved(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    dest = meeting_stamp.derivative_path(live, "debrief")
    def changed(*args, **kwargs):
        dest.write_text("Ручная правка\n")
        return "# Разбор\nРезультат\n"
    monkeypatch.setattr(refinement, "refine", changed)
    with pytest.raises(refinement.RefinementRefused, match="publication_unconfirmed"):
        run(live, handoff)
    assert dest.read_text() == "Ручная правка\n"


def test_passport_failure_is_not_ready(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    monkeypatch.setattr(live_sidecar, "attest", lambda *a, **k: False)
    with pytest.raises(refinement.RefinementRefused, match="publication_unconfirmed"):
        run(live, handoff)
    assert not meeting_stamp.derivative_path(live, "debrief").exists()


def test_retitle_keeps_exact_binding_and_updates_only_header(tmp_path, model):
    import graph_updater
    live, handoff = make_meeting(tmp_path)
    old_debrief = run(live, handoff)
    before = old_debrief.read_bytes()
    renamed = graph_updater.retitle(live, "2026-01-01_1200", STAMP, "Проверка макета")
    assert renamed != live and not pipeline.binding_path(live).exists()
    binding = json.loads(pipeline.binding_path(renamed).read_text())
    assert binding["handoff"] == handoff and binding["owner"] == renamed.name
    assert binding["full_sha256"] == live_sidecar.sha(renamed.read_text())
    assert renamed.with_name(renamed.stem + "_разбор.md").read_bytes() == before
    pipeline.run(renamed, CFG, handoff=None, final_sha=None, before_request=lambda: None, timeout=0)


def alternate_title_spelling(tmp_path, form, *, keep_derivative_name=False, pending_publication=False):
    import graph_updater

    live, handoff = make_meeting(tmp_path)
    run(live, handoff)
    title = unicodedata.normalize(form, "Крайний полёт")
    old = graph_updater.retitle(live, meeting_stamp.minute_of(STAMP), STAMP, title)
    alternate = "NFD" if form == "NFC" else "NFC"
    actual = old.with_name(unicodedata.normalize(alternate, old.name))
    assert actual != old and old != live
    if pending_publication:
        proof = pipeline.prepare_for_graph(old, CFG, handoff=None, final_sha=None)
        run(old, None, graph_input_sha=proof, defer_finalization=True)
    if not actual.exists():
        # A transfer to a byte-sensitive volume can change directory entries
        # while retaining the exact existing JSON records and document bytes.
        files = [old, pipeline.binding_path(old), old.with_name(old.name + ".live.json")]
        if pending_publication:
            files.append(pipeline.publication_path(old))
        if not keep_derivative_name:
            files.append(meeting_stamp.derivative_path(old, "debrief"))
        for path in files:
            path.rename(path.with_name(unicodedata.normalize(alternate, path.name)))
    return actual, old, handoff


@pytest.mark.parametrize("form", ["NFC", "NFD"])
def test_rebuild_after_retitle_accepts_equivalent_owner_spelling(tmp_path, model, monkeypatch, form):
    import graph_updater
    import rebuild_transcript as rebuild

    live, original, handoff = alternate_title_spelling(tmp_path, form, keep_derivative_name=form == "NFD")
    binding = pipeline.binding_path(live)
    legacy = binding.read_bytes()
    derivative_owner = original if form == "NFD" else live
    before = meeting_stamp.derivative_path(derivative_owner, "debrief").read_bytes()
    assert json.loads(legacy)["owner"] == original.name
    receipt = pipeline.receipt_path(live, handoff)
    export = tmp_path / handoff["file"]
    stop_bytes = (receipt.read_bytes(), export.read_bytes())

    # No audio survives. The regular retry still goes through rebuild(), the
    # graph child, durable parent acknowledgement and the public ready status.
    (tmp_path / "config").mkdir()
    (tmp_path / "config/config.yaml").write_text("audio: {samplerate: 16000}\nsufler: {refined_debrief: true, graph: false}\n")
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("CHAROITE_NO_RETRY", "1")
    monkeypatch.setattr(rebuild, "_root", lambda: tmp_path)
    monkeypatch.setattr(rebuild, "harden_umask", lambda: None)
    monkeypatch.setattr(rebuild, "_yield_to_live", lambda *_: None)
    monkeypatch.setattr(rebuild, "_take_rebuild_queue", lambda: None)
    monkeypatch.setattr(rebuild, "running_elsewhere", lambda *_: None)
    monkeypatch.setattr(rebuild, "mark_running", lambda *_: None)
    ready = []
    status = SimpleNamespace(processing=lambda *a: None, failed=lambda *a: None,
                             has_transcript=lambda path: path.is_file(), ready=lambda *args: ready.append(args))
    monkeypatch.setattr(rebuild, "MeetingStatusStore", lambda *_: status)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph_updater, "_open_progress", lambda *_: None)
    monkeypatch.setattr(graph_updater, "_report", lambda *_a, **_kw: None)
    monkeypatch.setattr(graph_updater, "_yield_to_live", lambda: None)
    monkeypatch.setattr(graph_updater, "harden_umask", lambda: None)
    monkeypatch.setattr(pipeline.graphs, "graph_dir", lambda cfg: None)

    graph_args = []

    def graph_child(argv, *, check):
        graph_args.extend(argv)
        with monkeypatch.context() as child:
            child.setattr(sys, "argv", argv[1:])
            try:
                graph_updater.main()
            except SystemExit as result:
                return SimpleNamespace(returncode=result.code)
            except RuntimeError:
                return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", graph_child)
    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)])
    rebuild.main()
    assert "--debrief-parent-ack" in graph_args and "--refine-before-archive" in graph_args
    assert len(ready) == 1 and ready[0][0] == live
    assert len(model) == 2 and model[-1][2].encode() == before
    assert live_sidecar.prev_path(live, pipeline.verify_publication(live)).read_bytes() == before
    assert json.loads(binding.read_bytes())["handoff"] == handoff
    assert not pipeline.publication_path(live).exists()

    # A later title change must still move exactly this transcript and debrief.
    renamed = graph_updater.retitle(live, meeting_stamp.minute_of(STAMP), STAMP, "Новый заголовок")
    assert renamed != live and not binding.exists()
    assert pipeline.verify_publication(renamed).is_file()
    assert (receipt.read_bytes(), export.read_bytes()) == stop_bytes


@pytest.mark.parametrize("field", ["owner", "debrief_file"])
def test_distinct_unicode_filenames_never_share_a_binding(tmp_path, model, field):
    live, original, _ = alternate_title_spelling(tmp_path, "NFC")
    current = live if field == "owner" else meeting_stamp.derivative_path(live, "debrief")
    competing = original if field == "owner" else meeting_stamp.derivative_path(original, "debrief")
    if competing.exists():
        pytest.skip("This volume aliases canonically equivalent names")
    competing.write_bytes(current.read_bytes())
    assert not current.samefile(competing)
    with pytest.raises(refinement.RefinementRefused):
        pipeline.verify_publication(live)
    assert not pipeline.can_retitle(live, live.with_name("different.md"))
    if field == "debrief_file":
        # Even a freshly written canonical transcript cannot authorize this
        # other inode or alter its previous binding before refusing the retry.
        live.write_text(live.read_text() + "Уточнили сроки.\n")
        assert live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(live.read_text()))
    evidence = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(refinement.RefinementRefused):
        run(live, None)
    assert len(model) == 1
    assert {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == evidence


@pytest.mark.parametrize("field", ["owner", "debrief_file"])
def test_retitle_binding_refuses_an_unrelated_name_with_identical_bytes(tmp_path, model, field):
    live, _, _ = alternate_title_spelling(tmp_path, "NFC")
    binding = pipeline.binding_path(live)
    value = json.loads(binding.read_bytes())
    derivative = pipeline.verify_publication(live)
    neighbor = live.with_name("2026-01-01_1200_Соседи.md")
    neighbor.write_bytes((live if field == "owner" else derivative).read_bytes())
    binding.write_text(json.dumps({**value, field: neighbor.name}))
    evidence = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    with pytest.raises(refinement.RefinementRefused):
        pipeline.verify_publication(live)
    with pytest.raises(refinement.RefinementRefused):
        run(live, None)
    assert not pipeline.can_retitle(live, live.with_name("unused.md"))
    assert len(model) == 1
    assert {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == evidence


def test_unicode_recovery_finishes_when_the_candidate_has_already_disappeared(tmp_path, model):
    live, original, _ = alternate_title_spelling(tmp_path, "NFC", pending_publication=True)
    marker = pipeline.publication_path(live)
    previous = json.loads(marker.read_bytes())["binding"]
    path = pipeline.verify_publication(live)
    path.unlink()
    with pytest.raises(refinement.RefinementRefused, match="debrief_binding_path"):
        pipeline.verify_publication(live)
    pipeline.recover_publication(live)
    assert not marker.exists() and not path.exists()
    assert json.loads(pipeline.binding_path(live).read_bytes()) == previous
    assert previous["owner"] == original.name
    # Rollback may finish with an absent file. A new graph must prove the
    # identity of its differently spelled derivative before using that name.
    with pytest.raises(refinement.RefinementRefused, match="debrief_binding_path"):
        pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None)


@pytest.mark.parametrize("damage", [None, "old_binding", "retitle_from", "replaced_owner"])
def test_interrupted_unicode_retitle_recovers_only_its_original_binding(tmp_path, model, damage):
    old, recorded, handoff = alternate_title_spelling(tmp_path, "NFC")
    old_binding = pipeline.binding_path(old)
    previous = old_binding.read_bytes()
    old_debrief = pipeline.verify_publication(old)
    debrief = old_debrief.read_bytes()
    assert old_binding.read_bytes() == previous
    new = old.with_name("2026-01-01_1200_Новая_тема.md")
    pipeline.prepare_retitle(old, new)
    pipeline.move_retitle_file(old, new)
    marker = pipeline.binding_path(new)
    reservation = json.loads(marker.read_bytes())
    assert reservation == {**json.loads(previous), "retitle_from": old.name, "retitle_header": None}
    assert reservation["owner"] == recorded.name
    if damage:
        if damage == "old_binding":
            old_binding.write_text(json.dumps({**json.loads(previous), "note": "changed"}))
        elif damage == "replaced_owner":
            recorded.write_bytes(new.read_bytes())
        else:
            marker.write_text(json.dumps({**reservation, "retitle_from": "neighbor.md"}))
        evidence = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
        with pytest.raises(refinement.RefinementRefused, match="retitle_binding_conflict|retitle_owner"):
            pipeline.recover_retitle(new)
        assert {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == evidence
    else:
        # The previous process also moved its derivative before crashing.
        pipeline.move_retitle_file(old_debrief, meeting_stamp.derivative_path(new, "debrief"))
        pipeline.recover_retitle(new)
        assert not old_binding.exists()
        result = json.loads(marker.read_bytes())
        assert set(result) == set(json.loads(previous)) and result["handoff"] == handoff
        assert pipeline.verify_publication(new).read_bytes() == debrief


def test_retitle_between_two_spellings_preserves_the_transcript(tmp_path, model):
    old, target, _ = alternate_title_spelling(tmp_path, "NFC")
    before = old.read_bytes()
    assert pipeline.can_retitle(old, target)
    pipeline.prepare_retitle(old, target)
    pipeline.move_retitle_file(old, target)
    pipeline.retitle_binding(old, target)
    assert target.read_bytes() == before and pipeline.verify_publication(target).is_file()


def test_foreign_retitle_binding_blocks_rename(tmp_path, model):
    import graph_updater
    live, handoff = make_meeting(tmp_path)
    run(live, handoff)
    target = tmp_path / "2026-01-01_1200_Проверка.md"
    pipeline.binding_path(target).write_text('{"owner": "foreign"}')
    assert graph_updater.retitle(live, "2026-01-01_1200", STAMP, "Проверка") == live
    assert live.exists() and not target.exists()


@pytest.mark.parametrize("mode", ["refusal", "silence", "legacy", "short"])
def test_canonical_and_archive_survive_refinement_refusal(tmp_path, monkeypatch, mode):
    import rebuild_transcript as rebuild
    # Isolated synthetic config never touches the installation.
    monkeypatch.setattr(rebuild, "_root", lambda: tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("sufler:\n  graph: false\n  refined_debrief: true\n")
    live, _ = make_meeting(tmp_path)
    events = []
    class Status:
        def __init__(self, *args): pass
        def processing(self, *args): events.append(("stage", args[1]))
        def failed(self, *args): events.append(("failed", args[1]))
        def ready(self, *args): events.append(("ready",))
        def no_speech(self, *args): events.append(("no_speech",))
        def has_transcript(self, *args): return True
    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)]
                        + ([] if mode == "legacy" else ["--refinement-unbound"]))
    monkeypatch.setattr(rebuild, "MeetingStatusStore", Status)
    monkeypatch.setattr(rebuild, "running_elsewhere", lambda *args: None)
    monkeypatch.setattr(rebuild, "mark_running", lambda *args: None)
    monkeypatch.setattr(rebuild, "_take_rebuild_queue", lambda: None)
    monkeypatch.setattr(rebuild, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rebuild, "rebuild", lambda *args: events.append(("canonical",)) or live)
    code = {"silence": rebuild.EXIT_NO_SPEECH, "short": rebuild.EXIT_SHORT_REFINED}.get(mode, 0)
    if mode == "short":
        def finalize(original, proof, *, reject=False):
            assert (original, proof, reject) == (live, "proof", False)
            events.append(("verified_publication",))
            return live
        monkeypatch.setattr(pipeline, "parent_publication", finalize)
        monkeypatch.setattr(pipeline, "prepare_for_graph", lambda *a, **kw: "proof")
        monkeypatch.setattr(rebuild.install_profile, "graph_enabled", lambda _cfg: True)
        monkeypatch.setattr(rebuild, "find_meeting_note", lambda *a, **kw: pytest.fail("short stop requested graph"))
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs:
                        events.append(("archive", args)) or SimpleNamespace(returncode=code))
    monkeypatch.setenv("CHAROITE_NO_RETRY", "1")
    if mode == "refusal":
        with pytest.raises(RuntimeError, match="Уточнение разбора"):
            rebuild.main()
    else:
        rebuild.main()
    expected = {"refusal": "failed", "silence": "no_speech", "legacy": "ready", "short": "ready"}[mode]
    expected_events = ["canonical", "archive"] + (["verified_publication"] if mode == "short" else [])
    assert [e[0] for e in events if e[0] != "stage"] == expected_events + [expected]
    args = next(e[1] for e in events if e[0] == "archive")
    assert ("--skip-legacy-debrief" in args) == (mode != "legacy")
    assert ("--refinement-refused" in args) == (mode in {"refusal", "silence"})
    assert ("--refine-before-archive" in args) == (mode == "short")
    assert ("--debrief-parent-ack" in args) == (mode == "short")


def test_early_child_spawn_precedes_stop_and_receipt():
    # Ordering is the regression contract: Swift may SIGKILL the daemon at 12 seconds.
    source = (pathlib.Path(__file__).parents[1] / "src" / "daemon.py").read_text()
    tree = ast.parse(source)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call)]
    spawn = [n.lineno for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == "Popen"
             and "rebuild_transcript.py" in ast.get_source_segment(source, n)]
    stop = [n.lineno for n in calls if isinstance(n.func, ast.Name) and n.func.id == "cut_stop_producers"]
    receipt = [n.lineno for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == "finish_stop"]
    assert len(spawn) == len(stop) == len(receipt) == 1
    assert spawn[0] < stop[0] < receipt[0]


def test_identity_reader_rejects_symlink_and_oversize(tmp_path):
    real = tmp_path / "real.json"
    real.write_text('{}')
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(refinement.RefinementRefused, match="identity_unreadable"):
        pipeline._read_json(link)
    real.write_bytes(b" " * 65537)
    with pytest.raises(refinement.RefinementRefused, match="identity_too_large"):
        pipeline._read_json(real)


def test_exclusive_receipt_is_private(tmp_path):
    path = tmp_path / "receipt.json"
    pipeline._exclusive_json(path, {"status": "ready"})
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("target", ["speech", "debrief"])
def test_graph_finish_rechecks_publication(tmp_path, model, target):
    live, handoff = make_meeting(tmp_path)
    debrief = run(live, handoff)
    pipeline.verify_publication(live)
    path = live if target == "speech" else debrief
    path.write_text(path.read_text() + "Ручная правка\n")
    with pytest.raises(refinement.RefinementRefused, match="publication"):
        pipeline.verify_publication(live)


def test_retitle_io_failure_returns_final_path_for_archive(tmp_path, model, monkeypatch):
    import graph_updater
    live, handoff = make_meeting(tmp_path)
    run(live, handoff)
    def fail(*args, **kwargs):
        raise OSError("synthetic disk refusal")
    monkeypatch.setattr(pipeline, "retitle_binding", fail)
    errors = []
    result = graph_updater.retitle(live, "2026-01-01_1200", STAMP, "Макет", refinement_errors=errors)
    assert result.exists() and result != live
    assert errors and "не перенесена" in errors[0]


def test_changed_source_during_graph_is_visible_retitle_refusal(tmp_path, model):
    import graph_updater
    live, handoff = make_meeting(tmp_path)
    run(live, handoff)
    live.write_text(live.read_text() + "Новая речь\n")
    errors = []
    assert graph_updater.retitle(live, "2026-01-01_1200", STAMP, "Макет",
                                 refinement_errors=errors) == live
    assert errors


@pytest.mark.parametrize("failure", ["attest", "binding_cas", "source_during_write"])
def test_failed_publication_restores_previous_visible_bytes(tmp_path, model, monkeypatch, failure):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    previous = "# Прежний разбор\n\nПроверка ещё обсуждается.\n"
    original_bytes = previous.replace("\n", "\r\n").encode("utf-8")
    destination.write_bytes(original_bytes)
    source = meeting_source.of(live, live.read_text())
    live_sidecar.attest(live, "debrief", previous, source.sha())
    if failure == "attest":
        monkeypatch.setattr(live_sidecar, "attest", lambda *a, **kw: False)
    elif failure == "binding_cas":
        original = pipeline.safe_write.write_text
        def refusing(path, text, **kwargs):
            if path == pipeline.binding_path(live) and '"publication_id"' in text:
                return False
            return original(path, text, **kwargs)
        monkeypatch.setattr(pipeline.safe_write, "write_text", refusing)
    else:
        original = live_sidecar.write_derivative
        def changing(*args, **kwargs):
            result = original(*args, **kwargs)
            live.write_text(live.read_text() + "Источник изменился.\n")
            return result
        monkeypatch.setattr(live_sidecar, "write_derivative", changing)
    with pytest.raises(refinement.RefinementRefused):
        run(live, handoff)
    assert model[0][2] == previous
    assert destination.read_bytes() == original_bytes
    assert live_sidecar.prev_path(live, destination).read_bytes() == original_bytes
    assert not pipeline.publication_path(live).exists()


def test_rollback_does_not_overwrite_concurrent_human_bytes(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    original = live_sidecar.write_derivative
    def editing(*args, **kwargs):
        result = original(*args, **kwargs)
        destination.write_text("Ручная правка после записи кандидата.\n")
        live.write_text(live.read_text() + "Источник изменился.\n")
        return result
    monkeypatch.setattr(live_sidecar, "write_derivative", editing)
    with pytest.raises(refinement.RefinementRefused):
        run(live, handoff)
    assert destination.read_text() == "Ручная правка после записи кандидата.\n"


def test_crash_between_publication_and_binding_is_recoverable(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    original = live_sidecar.write_derivative
    def crashing(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("synthetic process death after replace")
    monkeypatch.setattr(live_sidecar, "write_derivative", crashing)
    with pytest.raises(SystemExit):
        run(live, handoff)
    assert destination.exists() and pipeline.publication_path(live).exists()
    pipeline.recover_publication(live)
    assert not destination.exists() and not pipeline.publication_path(live).exists()


def test_refused_candidate_is_excluded_from_real_archive_and_vault(tmp_path, monkeypatch):
    import graph_updater
    import meeting_archive
    graph = tmp_path / "graph"
    (graph / "Документация").mkdir(parents=True)
    live = tmp_path / "2026-01-01_1200_Макет.md"
    live.write_text("# Встреча 2026-01-01_1200\n\n" + SPEECH)
    candidate = live.with_name(live.stem + "_разбор.md")
    candidate.write_text("Неподтверждённый кандидат\n")
    previous = live.with_name(live.stem + "_Старая_тема_разбор.md")
    previous.write_text("Старый неподтверждённый разбор\n")
    monkeypatch.setattr(meeting_archive, "summary_pass", lambda *a, **k: SimpleNamespace(action="kept"))
    monkeypatch.setattr(meeting_archive, "_derive_extras", lambda *a, **k: None)
    monkeypatch.setattr(meeting_archive, "unhide_graph", lambda *a, **k: None)
    graph_updater.archive_and_publish(live, graph, "2026-01-01_1200", exclude_debrief=True)
    assert list(graph.rglob("Стенограмма.md"))
    assert not list(graph.rglob("Разбор.md"))
    vault = graph / "Документация" / "Стенограммы встреч"
    assert (vault / live.name).read_bytes() == live.read_bytes()
    assert not (vault / candidate.name).exists()
    assert not (vault / previous.name).exists()


def test_source_change_inside_terminal_wait_refuses_before_model(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    original = pipeline._terminal
    def changing(*args, **kwargs):
        terminal = original(*args, **kwargs)
        live.write_text(live.read_text() + "Новая речь во время ожидания.\n")
        return terminal
    monkeypatch.setattr(pipeline, "_terminal", changing)
    with pytest.raises(refinement.RefinementRefused, match="source_changed"):
        run(live, handoff)
    assert not model and not pipeline.binding_path(live).exists()


def test_pending_handoff_allows_retry_after_child_dies_in_stt(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    pipeline.remember_handoff(live, handoff)
    assert not pipeline.binding_path(live).exists()
    restored = pipeline.pending_handoff(live)
    assert restored == handoff
    run(live, restored)
    assert pipeline.binding_path(live).exists() and not pipeline.handoff_path(live).exists()


def test_manual_rename_moves_dedicated_binding(tmp_path, model, monkeypatch):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).parents[1] / "scripts"))
    import rename_meeting
    live, handoff = make_meeting(tmp_path)
    debrief = run(live, handoff)
    target = tmp_path / "2026-01-01_1200_Макет.md"
    target_debrief = target.with_name(target.stem + "_разбор.md")
    graph = tmp_path / "graph"
    graph.mkdir()
    monkeypatch.setattr(rename_meeting, "_root", lambda: tmp_path)
    plan = {"moves": [(live, target), (live.with_name(live.name + ".live.json"),
                                       target.with_name(target.name + ".live.json")),
                       (debrief, target_debrief)],
            "stamps": [(target, STAMP)], "old_folder": None, "new_folder": None,
            "main": target, "note": graph / "absent.md"}
    rename_meeting.apply(plan, graph, "2026-01-01_1200", "Макет")
    assert not pipeline.binding_path(live).exists()
    pipeline.verify_publication(target)
    pipeline.run(target, CFG, handoff=None, final_sha=None, before_request=lambda: None, timeout=0)


def test_long_previous_debrief_rollback_does_not_expand_identity_json(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    previous = "Прежний подробный разбор.\n" * 5000
    assert len(previous.encode()) > 65536
    destination.write_text(previous)
    live_sidecar.attest(live, "debrief", previous, meeting_source.of(live, live.read_text()).sha())
    monkeypatch.setattr(live_sidecar, "attest", lambda *a, **kw: False)
    with pytest.raises(refinement.RefinementRefused):
        run(live, handoff)
    assert destination.read_text() == previous


def test_identity_writer_refuses_oversize_before_creating_file(tmp_path):
    target = tmp_path / "identity.json"
    with pytest.raises(refinement.RefinementRefused, match="identity_too_large"):
        pipeline._exclusive_json(target, {"payload": "x" * 65536})
    assert not target.exists()


def test_missing_graph_still_verifies_refinement(tmp_path, monkeypatch):
    import graph_updater
    live, _ = make_meeting(tmp_path)
    live.write_text(live.read_text() + SPEECH * 20)
    monkeypatch.setattr(sys, "argv", ["graph_updater.py", str(live), "--skip-legacy-debrief"])
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph_updater.graphs, "graph_dir", lambda *a, **k: None)
    monkeypatch.setattr(graph_updater, "_open_progress", lambda *a: None)
    hooks = []
    monkeypatch.setattr(graph_updater, "run_post_hook", lambda *a: hooks.append(a))
    with pytest.raises(refinement.RefinementRefused, match="identity_unreadable"):
        graph_updater.main()
    assert hooks


@pytest.mark.parametrize("source_changed", [False, True])
def test_crash_after_binding_requires_full_revalidation(tmp_path, model, monkeypatch, source_changed):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    original = pipeline.safe_write.write_text
    def crashing(path, text, **kwargs):
        result = original(path, text, **kwargs)
        if path == pipeline.binding_path(live) and '"publication_id"' in text:
            if source_changed:
                live.write_text(live.read_text() + "Изменение перед смертью процесса.\n")
            raise SystemExit("synthetic process death after binding CAS")
        return result
    monkeypatch.setattr(pipeline.safe_write, "write_text", crashing)
    with pytest.raises(SystemExit):
        run(live, handoff)
    assert destination.exists() and pipeline.publication_path(live).exists()
    if source_changed:
        with pytest.raises(refinement.RefinementRefused, match="publication_unconfirmed"):
            pipeline.recover_publication(live)
        assert not destination.exists()
    else:
        pipeline.recover_publication(live)
        pipeline.verify_publication(live)
        assert destination.exists()
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("existing", [False, True])
def test_recovery_retries_passport_after_bytes_were_already_restored(tmp_path, model, monkeypatch, existing):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    previous = "Прежний подтверждённый разбор.\n"
    if existing:
        destination.write_text(previous)
        live_sidecar.attest(live, "debrief", previous, meeting_source.of(live, live.read_text()).sha())
    old_meta = live_sidecar.read_direct(live)
    original_write = pipeline.safe_write.write_text
    original_derivative = live_sidecar.write_derivative
    fail_passport = False
    passport = live.with_name(live.name + ".live.json")
    def refusing(path, text, **kwargs):
        if fail_passport and path == passport:
            return False
        return original_write(path, text, **kwargs)
    def source_changed(*args, **kwargs):
        nonlocal fail_passport
        result = original_derivative(*args, **kwargs)
        live.write_text(live.read_text() + "Источник изменился.\n")
        fail_passport = True
        return result
    monkeypatch.setattr(pipeline.safe_write, "write_text", refusing)
    monkeypatch.setattr(live_sidecar, "write_derivative", source_changed)
    with pytest.raises(refinement.RefinementRefused, match="rollback_passport_race"):
        run(live, handoff)
    assert (destination.read_text() == previous) if existing else not destination.exists()
    assert pipeline.publication_path(live).exists()
    fail_passport = False
    pipeline.recover_publication(live)
    assert not pipeline.publication_path(live).exists()
    restored_meta = live_sidecar.read_direct(live)
    for key in ("debrief_sha256", "debrief_source_sha256"):
        assert restored_meta.get(key) == old_meta.get(key)
    assert (destination.read_text() == previous) if existing else not destination.exists()


def test_known_not_written_does_not_remove_identical_concurrent_file(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    concurrent = []
    def race(_live, path, kind, body, source_sha, **kwargs):
        path.write_text(body)
        concurrent.append(body)
        return live_sidecar.WriteOutcome(None, live_sidecar.WriteOutcome.RACE)
    monkeypatch.setattr(live_sidecar, "write_derivative", race)
    with pytest.raises(refinement.RefinementRefused, match="publication_unconfirmed"):
        run(live, handoff)
    assert destination.read_text() == concurrent[0]
    assert not pipeline.publication_path(live).exists()
    assert "debrief_sha256" not in live_sidecar.read_direct(live)


@pytest.mark.parametrize("marker", ["pending", "journal"])
def test_manual_rename_rejects_unfinished_identity_before_any_move(tmp_path, monkeypatch, marker):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).parents[1] / "scripts"))
    import rename_meeting
    live, handoff = make_meeting(tmp_path)
    if marker == "pending":
        pipeline.remember_handoff(live, handoff)
    else:
        pipeline.publication_path(live).write_text('{}')
    target = tmp_path / "2026-01-01_1200_Макет.md"
    with pytest.raises(RuntimeError, match="переименование отклонено"):
        rename_meeting.apply({"moves": [(live, target)]}, tmp_path / "graph", "2026-01-01_1200", "Макет")
    assert live.exists() and not target.exists()


@pytest.mark.parametrize("wait", ["live", "queue"])
def test_handoff_is_durable_before_either_long_wait(tmp_path, monkeypatch, wait):
    import rebuild_transcript as rebuild
    live, handoff = make_meeting(tmp_path)
    class Status:
        def __init__(self, *args): pass
        def processing(self, *args): pass
    monkeypatch.setattr(rebuild, "_root", lambda: tmp_path)
    monkeypatch.setattr(rebuild, "MeetingStatusStore", Status)
    monkeypatch.setattr(rebuild, "running_elsewhere", lambda *args: None)
    monkeypatch.setattr(rebuild, "mark_running", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)] + pipeline.arguments(handoff))
    def waiting(phase):
        assert pipeline.pending_handoff(live) == handoff
        if phase == wait:
            raise SystemExit("synthetic death during wait")
    monkeypatch.setattr(rebuild, "_yield_to_live", lambda *a, **k: waiting("live"))
    monkeypatch.setattr(rebuild, "_take_rebuild_queue", lambda: waiting("queue"))
    with pytest.raises(SystemExit, match="synthetic death"):
        rebuild.main()
    assert pipeline.pending_handoff(live) == handoff


@pytest.mark.parametrize("phase", ["before_callback", "after_callback"])
def test_crash_before_installation_preserves_identical_foreign_file(tmp_path, model, monkeypatch, phase):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    foreign = []
    if phase == "before_callback":
        def crash(_live, path, kind, body, source_sha, **kwargs):
            path.write_text(body)
            foreign.append((body, path.stat().st_ino))
            raise SystemExit("synthetic crash before callback")
        monkeypatch.setattr(live_sidecar, "write_derivative", crash)
    else:
        original_replace = pathlib.Path.replace
        def crash(path, target):
            if target == destination and path.name.startswith(destination.name + ".tmp"):
                body = path.read_text()
                destination.write_text(body)
                foreign.append((body, destination.stat().st_ino))
                raise SystemExit("synthetic crash before replacement")
            return original_replace(path, target)
        monkeypatch.setattr(pathlib.Path, "replace", crash)
    with pytest.raises(SystemExit):
        run(live, handoff)
    journal = json.loads(pipeline.publication_path(live).read_text())
    assert ("installed_identity" in journal) == (phase == "after_callback")
    pipeline.recover_publication(live)
    assert destination.read_text() == foreign[0][0]
    assert destination.stat().st_ino == foreign[0][1]
    assert "debrief_sha256" not in live_sidecar.read_direct(live)
    assert not pipeline.publication_path(live).exists()


def test_source_change_in_pre_replace_callback_prevents_installation(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    original_replace = pathlib.Path.replace
    installs = []

    def observe_replace(temp, target):
        result = original_replace(temp, target)
        if target == destination:
            installs.append(result)
        return result

    monkeypatch.setattr(pathlib.Path, "replace", observe_replace)
    original_update = pipeline._update_publication
    def changed(*args, **kwargs):
        updated = original_update(*args, **kwargs)
        if "installed_identity" in updated:
            live.write_text(live.read_text() + "Новая речь перед заменой.\n")
        return updated
    monkeypatch.setattr(pipeline, "_update_publication", changed)
    with pytest.raises(refinement.RefinementRefused, match="source_changed"):
        run(live, handoff)
    assert not installs  # A later rollback cannot erase an observed installation.
    assert not destination.exists()
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("marker", ["binding", "pending", "journal"])
def test_manual_rename_rejects_orphan_identity_at_target_without_old_metadata(tmp_path, monkeypatch, marker):
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).parents[1] / "scripts"))
    import rename_meeting
    live, _ = make_meeting(tmp_path)
    target = tmp_path / "2026-01-01_1200_Макет.md"
    path = {"binding": pipeline.binding_path, "pending": pipeline.handoff_path,
            "journal": pipeline.publication_path}[marker](target)
    path.write_text('{"foreign": true}')
    with pytest.raises(RuntimeError, match="переименование отклонено"):
        rename_meeting.apply({"moves": [(live, target)]}, tmp_path / "graph", "2026-01-01_1200", "Макет")
    assert live.exists() and not target.exists()
    assert path.read_text() == '{"foreign": true}'


def test_journal_link_then_directory_fsync_failure_retains_backup(tmp_path, model, monkeypatch):
    import stat
    live, handoff = make_meeting(tmp_path)
    destination = run(live, handoff)
    original_body = destination.read_bytes()
    original_meta = live_sidecar.read_direct(live)
    original_fsync = pipeline.os.fsync
    def refused(fd):
        if (stat.S_ISDIR(pipeline.os.fstat(fd).st_mode)
                and pipeline.publication_path(live).exists()):
            raise OSError("synthetic directory fsync failure after link")
        return original_fsync(fd)
    monkeypatch.setattr(pipeline.os, "fsync", refused)
    with pytest.raises(OSError, match="fsync failure"):
        run(live, None)
    journal = json.loads(pipeline.publication_path(live).read_text())
    assert "installed_identity" not in journal
    assert (live.parent / ".prev" / journal["backup"]).read_bytes() == original_body
    assert destination.read_bytes() == original_body
    monkeypatch.setattr(pipeline.os, "fsync", original_fsync)
    pipeline.recover_publication(live)
    assert destination.read_bytes() == original_body
    assert live_sidecar.read_direct(live) == original_meta
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("failure", ["backup_file", "backup_directory"])
def test_backup_fsync_failure_prevents_journal_and_publication(tmp_path, model, monkeypatch, failure):
    import stat
    live, handoff = make_meeting(tmp_path)
    destination = meeting_stamp.derivative_path(live, "debrief")
    previous = "Прежний подтверждённый разбор.\n"
    destination.write_text(previous)
    live_sidecar.attest(live, "debrief", previous, meeting_source.of(live, live.read_text()).sha())
    before_meta = live_sidecar.read_direct(live)
    original_fsync = pipeline.os.fsync
    injected = []
    def refused(fd):
        snapshot = pipeline.os.fstat(fd)
        backups = list((live.parent / ".prev").glob("*.rollback-*"))
        if failure == "backup_file":
            selected = any((path.stat().st_dev, path.stat().st_ino) ==
                           (snapshot.st_dev, snapshot.st_ino) for path in backups)
        else:
            folder = live.parent / ".prev"
            selected = (folder.exists() and stat.S_ISDIR(snapshot.st_mode)
                        and (folder.stat().st_dev, folder.stat().st_ino) == (snapshot.st_dev, snapshot.st_ino))
        if selected:
            injected.append(failure)
            raise OSError("synthetic backup fsync failure")
        return original_fsync(fd)
    monkeypatch.setattr(pipeline.os, "fsync", refused)
    with pytest.raises(OSError, match="backup fsync"):
        run(live, handoff)
    assert injected == [failure]
    assert destination.read_text() == previous
    assert live_sidecar.read_direct(live) == before_meta
    assert not pipeline.publication_path(live).exists()


def previous_debrief(live):
    path = meeting_stamp.derivative_path(live, "debrief")
    data = "Прежний разбор.\r\nСледующая строка.\r\n".encode("utf-8")
    path.write_bytes(data)
    assert live_sidecar.attest(live, "debrief", path.read_text(), meeting_source.of(live, live.read_text()).sha())
    return path, data


@pytest.mark.parametrize("target", ["debrief", "passport", "binding"])
def test_fsync_of_each_public_result_precedes_commit(tmp_path, model, monkeypatch, target):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    passport = live.with_name(live.name + ".live.json")
    binding = pipeline.binding_path(live)
    expected_meta = live_sidecar.read_direct(live)
    selected = {"debrief": destination, "passport": passport, "binding": binding}[target]
    original_fsync = pipeline.os.fsync
    failures = []

    def fail_once(fd):
        if (not failures and pipeline.publication_path(live).exists() and binding.exists()
                and "publication_id" in json.loads(binding.read_text())
                and pipeline.os.path.samestat(pipeline.os.fstat(fd), selected.stat())):
            failures.append(target)
            raise OSError("synthetic result fsync")
        original_fsync(fd)

    monkeypatch.setattr(pipeline.os, "fsync", fail_once)
    with pytest.raises(OSError, match="result fsync"):
        run(live, handoff)
    assert failures == [target]
    assert destination.read_bytes() == previous
    assert live_sidecar.read_direct(live) == expected_meta
    assert "publication_id" not in json.loads(binding.read_text())
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("fault", ["binding_read", "passport_read", "restored_fsync"])
def test_interrupted_compensation_preserves_journal_and_backup_for_retry(tmp_path, model, monkeypatch, fault):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    expected_meta = live_sidecar.read_direct(live)

    def crash():
        raise SystemExit("synthetic interrupted consumer")

    with pytest.raises(SystemExit):
        run(live, handoff, before_commit=crash)
    journal = pipeline.publication_path(live)
    backup = live.parent / ".prev" / json.loads(journal.read_text())["backup"]
    assert destination.read_bytes() != previous
    with monkeypatch.context() as patch:
        if fault == "binding_read":
            original = pipeline._read_json

            def read(path):
                if path == pipeline.binding_path(live):
                    raise refinement.RefinementRefused("synthetic unreadable binding")
                return original(path)
            patch.setattr(pipeline, "_read_json", read)
        elif fault == "passport_read":
            patch.setattr(live_sidecar, "read_direct", lambda path: None)
        else:
            original = pipeline._sync_files_and_parent

            def sync(path, *siblings):
                if destination in siblings:
                    raise OSError("synthetic restored fsync")
                original(path, *siblings)
            patch.setattr(pipeline, "_sync_files_and_parent", sync)
        with pytest.raises((refinement.RefinementRefused, OSError)):
            pipeline.recover_publication(live)
    assert journal.is_file() and backup.read_bytes() == previous
    pipeline.recover_publication(live)
    assert not journal.exists() and not backup.exists()
    assert destination.read_bytes() == previous
    assert live_sidecar.read_direct(live) == expected_meta


def test_backup_cleanup_error_after_commit_preserves_success(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    original = pathlib.Path.unlink
    orphans = []

    def fail_backup_unlink(path, *args, **kwargs):
        if path.parent.name == ".prev" and ".rollback-" in path.name and ".tmp" not in path.name:
            assert not pipeline.publication_path(live).exists()
            orphans.append(path)
            raise OSError("synthetic cleanup error")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", fail_backup_unlink)
    assert run(live, handoff) == destination
    pipeline.verify_publication(live)
    assert destination.read_bytes() != previous
    assert len(orphans) == 1 and orphans[0].read_bytes() == previous


@pytest.mark.parametrize("mode", ["graph", "changed_after_retitle", "disabled", "missing", "changed_in_hook",
                                  "refused_prepare", "graph_failure"])
def test_graph_consumes_only_final_verified_refinement(tmp_path, monkeypatch, model, mode):
    import graph_updater as graph

    live, handoff = make_meeting(tmp_path)
    live.write_text(live.read_text() + SPEECH * 12)
    full = live_sidecar.sha(live.read_text())
    assert live_sidecar.remember(live, "transcript_sha256", full)
    root = tmp_path / "graph"
    root.mkdir()
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(root))
    missing = mode in {"missing", "changed_in_hook"}
    if missing:
        monkeypatch.setattr(graph.graphs, "graph_dir", lambda *args, **kwargs: None)
    previous_file, previous = previous_debrief(live)
    cfg = {"sufler": {"refined_debrief": True, "graph": mode != "disabled", "graph_dir": str(root)}}
    pipeline.remember_handoff(live, handoff)
    if mode == "refused_prepare":
        with pytest.raises(refinement.RefinementRefused, match="canonical_final_unproven"):
            pipeline.prepare_for_graph(live, cfg, handoff=handoff, final_sha="stale", timeout=0)
        assert pipeline.handoff_path(live).exists()
    else:
        proof = pipeline.prepare_for_graph(live, cfg, handoff=handoff, final_sha=full, timeout=0)
        assert not pipeline.handoff_path(live).exists()
    assert not model and previous_file.read_bytes() == previous
    args = ["--refinement-refused"] if mode == "refused_prepare" else ["--refine-before-archive", proof]
    monkeypatch.setattr(sys, "argv", ["graph_updater.py", str(live), "--skip-legacy-debrief", *args])
    monkeypatch.setattr(graph, "load_cfg", lambda: cfg)
    monkeypatch.setattr(graph, "extract", lambda *a: None if mode == "graph_failure" else {"название": "Макет"})
    for name in ("harden_umask", "_open_progress", "reindex_memory", "_repath"):
        monkeypatch.setattr(graph, name, lambda *a: None)
    monkeypatch.setattr(graph.privacy, "cloud_enrich_enabled", lambda cfg: False)
    stages, yielded, archived, hooked = [], [], [], []
    model_call = refinement.refine

    def with_heartbeat(*args, **kwargs):
        kwargs["progress"]({"phase": "request"})
        return model_call(*args, **kwargs)

    monkeypatch.setattr(refinement, "refine", with_heartbeat)
    store = SimpleNamespace(processing=lambda path, stage, **kw: stages.append((path, stage)))
    monkeypatch.setattr(graph, "_progress", (store, live))
    monkeypatch.setattr(graph, "_yield_to_live", lambda: yielded.append(True))
    renamed = live.with_name("2026-01-01_1200_Макет.md")

    def after_graph(*args):
        old = meeting_stamp.derivative_path(renamed if renamed.exists() else live, "debrief")
        assert old.read_bytes() == previous
        if mode == "changed_after_retitle":
            renamed.write_text(renamed.read_text() + "Речь изменилась.\n")
    monkeypatch.setattr(graph, "meeting_facts_to_brain", after_graph)

    def archive(path, _graph, _stamp, *, exclude_debrief=False):
        archived.append((path, exclude_debrief, meeting_stamp.derivative_path(path, "debrief").read_bytes()))
    monkeypatch.setattr(graph, "archive_and_publish", archive)

    def post_hook(_cfg, path, _stamp):
        hooked.append(meeting_stamp.derivative_path(path, "debrief").read_bytes())
        if mode == "changed_in_hook":
            path.write_text(path.read_text() + "Речь изменилась в хуке.\n")
    monkeypatch.setattr(graph, "run_post_hook", post_hook)

    if mode in {"refused_prepare", "changed_after_retitle", "changed_in_hook"}:
        with pytest.raises((RuntimeError, refinement.RefinementRefused)):
            graph.main()
    elif mode == "graph_failure":
        with pytest.raises(SystemExit) as result:
            graph.main()
        assert result.value.code == graph.EXIT_NO_GRAPH
    else:
        graph.main()

    installed = mode in {"graph", "disabled", "missing"}
    called_model = installed or mode == "changed_in_hook"
    assert len(model) == len(yielded) == int(called_model)
    if called_model:
        assert stages.count((live, "refining_debrief")) == 2
        assert model[0][2] == previous.decode().replace("\r\n", "\n")
    final = renamed if renamed.exists() else live
    assert final.is_file() and not pipeline.publication_path(final).exists()
    assert len(hooked) == 1 and (hooked[0] != previous) == called_model
    if installed:
        pipeline.verify_publication(final)
        assert pipeline._read_json(pipeline.binding_path(final))["handoff"] == handoff
    else:
        assert meeting_stamp.derivative_path(final, "debrief").read_bytes() == previous
    if missing:
        assert not archived
    else:
        assert len(archived) == 1 and archived[0][:2] == (final, not installed)
        assert (archived[0][2] != previous) == installed


def test_graph_proof_rejects_removal_of_previous_debrief_before_model(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    destination, _ = previous_debrief(live)
    proof = pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()))
    destination.unlink()
    with pytest.raises(refinement.RefinementRefused, match="graph_inputs_changed"):
        pipeline.run(live, CFG, handoff=None, final_sha=None, before_request=lambda: None, graph_input_sha=proof)
    assert not destination.exists() and not model


def test_retitle_binding_rejects_unproven_header_despite_identical_speech(tmp_path, model):
    live, handoff = make_meeting(tmp_path)
    run(live, handoff)
    original_sha = live_sidecar.sha(live.read_text())
    source_before = meeting_source.of(live, live.read_text()).sha()
    original_binding = pipeline._read_json(pipeline.binding_path(live))
    live.write_text(live.read_text().replace("# Встреча " + STAMP, "# Встреча " + STAMP + " — Правка", 1))
    assert meeting_source.of(live, live.read_text()).sha() == source_before
    with pytest.raises(refinement.RefinementRefused, match="retitle_source_changed"):
        pipeline.retitle_binding(live, live, header_change=(original_sha, original_sha))
    assert pipeline._read_json(pipeline.binding_path(live)) == original_binding


def test_rejected_binding_cannot_commit_on_retry_after_transient_read_failure(tmp_path, model, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    binding = pipeline.binding_path(live)
    read = pipeline._read_json
    write = pipeline.safe_write.write_text
    failed = False

    def rejected_write(path, text, **kwargs):
        nonlocal failed
        result = write(path, text, **kwargs)
        if path == binding and "publication_id" in json.loads(text):
            failed = True
            raise OSError("synthetic error after binding replacement")
        return result

    def unavailable(path):
        if failed and path == binding:
            raise refinement.RefinementRefused("synthetic binding read failure")
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(pipeline.safe_write, "write_text", rejected_write)
        patch.setattr(pipeline, "_read_json", unavailable)
        with pytest.raises(refinement.RefinementRefused, match="binding read failure"):
            run(live, handoff)
    assert read(pipeline.publication_path(live))["rejected"] is True
    assert read(binding)["publication_id"] and destination.read_bytes() != previous
    pipeline.recover_publication(live)
    assert destination.read_bytes() == previous and "publication_id" not in read(binding)
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("spoken", [True, False])
@pytest.mark.parametrize("refused", [True, False])
def test_short_bound_stop_publishes_speech_without_graph_extraction(tmp_path, monkeypatch, model, spoken, refused):
    import graph_updater as graph

    live, handoff = make_meeting(tmp_path)
    if not spoken:
        live.write_text("# Встреча " + STAMP + "\n")
        assert live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(live.read_text()))
    proof = pipeline.prepare_for_graph(live, CFG, handoff=handoff,
                                       final_sha=live_sidecar.sha(live.read_text()), timeout=0)
    monkeypatch.setattr(graph, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph, "_open_progress", lambda *_: None)
    monkeypatch.setattr(graph, "_report", lambda *_a, **_kw: None)
    monkeypatch.setattr(graph, "_yield_to_live", lambda: None)
    monkeypatch.setattr(graph, "extract", lambda *_a: pytest.fail("short speech reached graph extraction"))
    flags = ["--refinement-refused"] if refused else ["--refine-before-archive", proof]
    monkeypatch.setattr(sys, "argv", ["graph_updater.py", str(live), "--skip-legacy-debrief", *flags])
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    if refused and spoken:
        with pytest.raises(RuntimeError, match="отклонено"):
            graph.main()
        assert not model and not meeting_stamp.derivative_path(live, "debrief").exists()
        return
    with pytest.raises(SystemExit) as outcome:
        graph.main()
    assert outcome.value.code == (graph.EXIT_SHORT_REFINED if spoken else graph.EXIT_NO_SPEECH)
    assert bool(model) is spoken
    if spoken:
        pipeline.verify_publication(live)
    else:
        assert not meeting_stamp.derivative_path(live, "debrief").exists()
