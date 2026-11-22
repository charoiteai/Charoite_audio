"""Public file and CLI behavior at the handoff between refinement and archival."""
import pytest

from charoite_graph import safe_write


@pytest.mark.parametrize("previously_existed", [False, True], ids=["created", "replaced"])
def test_write_text_returns_false_and_keeps_a_concurrent_publication(tmp_path, previously_existed):
    destination = tmp_path / "debrief.md"
    if previously_existed:
        destination.write_text("previous\n", encoding="utf-8")
    snapshot = safe_write.stat_snapshot(destination)

    def publish_concurrent_version(_candidate_stat):
        other = tmp_path / "other.md"
        other.write_text("concurrent\n", encoding="utf-8")
        other.replace(destination)

    result = safe_write.write_text(destination, "candidate\n", expect=snapshot,
                                   expect_absent=not previously_existed,
                                   before_replace=publish_concurrent_version)

    assert result is False
    assert destination.read_bytes() == b"concurrent\n"


def test_default_publication_copies_the_debrief_to_archive_and_vault(tmp_path, monkeypatch):
    import graph_updater
    import meeting_archive

    stamp = "2026-01-01_1200"
    meeting = tmp_path / (stamp + "_Макет.md")
    meeting.write_text("# Встреча " + stamp + "\n\nСогласовали макет.\n", encoding="utf-8")
    debrief = tmp_path / (meeting.stem + "_разбор.md")
    content = "Итог обсуждения макета.\r\n".encode("utf-8")
    debrief.write_bytes(content)
    graph = tmp_path / "graph"
    (graph / "Документация").mkdir(parents=True)
    monkeypatch.setattr(meeting_archive, "summary_pass",
                        lambda *a, **kw: meeting_archive.SummaryOutcome(meeting_archive.SummaryOutcome.NONE, None))
    monkeypatch.setattr(meeting_archive, "_derive_extras", lambda *a, **kw: None)
    monkeypatch.setattr(meeting_archive, "unhide_graph", lambda *a, **kw: None)

    graph_updater.archive_and_publish(meeting, graph, stamp)

    archived = list((graph / meeting_archive.ARCHIVE_DIR).glob("*/Разбор.md"))
    assert len(archived) == 1 and archived[0].read_bytes() == content
    assert (graph / "Документация" / "Стенограммы встреч" / debrief.name).read_bytes() == content


@pytest.mark.parametrize("interrupted", [False, True], ids=["initial", "resume"])
def test_bound_graph_retitle_preserves_file_ownership(tmp_path, interrupted):
    import debrief_pipeline
    import graph_updater
    import live_sidecar
    import meeting_stamp
    from test_debrief_pipeline import CFG, STAMP, make_meeting

    old, handoff = make_meeting(tmp_path)
    stamp = meeting_stamp.minute_of(STAMP)
    target = old.with_name(f"{stamp}_Макет.md")
    debrief_pipeline.prepare_for_graph(old, CFG, handoff=handoff, final_sha=live_sidecar.sha(old.read_text()))
    minutes = old.with_name(old.stem + "_minutes.md")
    minutes.write_bytes(b"minutes\n")
    unbound = old.with_name(old.stem + "_разбор.md")
    unbound.write_bytes(b"unclaimed legacy debrief\n")
    moved_minutes = target.with_name(target.stem + "_minutes.md")
    if interrupted:
        # The auxiliary files have moved, but both transcript names still
        # refer to the same inode until the no-clobber move unlinks the old one.
        debrief_pipeline.prepare_retitle(old, target, header=(f"# Встреча {STAMP}", f"# Встреча {stamp} — Макет"))
        minutes.rename(moved_minutes)
        target.hardlink_to(old)

    assert graph_updater.retitle(old, stamp, STAMP, "Макет") == target
    assert not old.exists() and not debrief_pipeline.binding_path(old).exists()
    assert target.read_text().startswith(f"# Встреча {stamp} — Макет")
    assert live_sidecar.read_direct(target)["stamp"] == STAMP
    assert moved_minutes.read_bytes() == b"minutes\n"
    assert unbound.read_bytes() == b"unclaimed legacy debrief\n"
    assert not target.with_name(target.stem + "_разбор.md").exists()


@pytest.mark.parametrize("argv", [[], ["graph_updater.py"]], ids=["empty", "executable-only"])
def test_graph_uses_latest_transcript_without_a_positional_path(tmp_path, monkeypatch, argv):
    import graph_updater

    latest = tmp_path / "2026-01-01_1200.md"
    latest.write_text("# Встреча 2026-01-01_1200\n\nКороткая реплика.\n", encoding="utf-8")
    monkeypatch.setattr(graph_updater.sys, "argv", argv)
    monkeypatch.setattr(graph_updater, "harden_umask", lambda: None)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: {})
    monkeypatch.setattr(graph_updater, "latest_transcript", lambda: latest)
    monkeypatch.setattr(graph_updater.graphs, "graph_dir", lambda cfg: None)
    opened = []
    monkeypatch.setattr(graph_updater, "_open_progress", opened.append)

    with pytest.raises(SystemExit) as exit_status:
        graph_updater.main()
    assert exit_status.value.code == graph_updater.EXIT_NO_SPEECH
    assert opened == [latest]


@pytest.mark.parametrize("mode", ["synchronous", "parent", "parent-crash"])
def test_archive_failure_keeps_the_committed_or_recovers_the_pending_files(tmp_path, monkeypatch, mode):
    import json
    from types import SimpleNamespace
    import debrief_pipeline as pipeline
    import debrief_refinement
    import graph_updater as graph
    import live_sidecar
    import meeting_archive
    import meeting_stamp
    import rebuild_transcript as rebuild
    from test_debrief_pipeline import CFG, SPEECH, make_meeting, run

    live, handoff = make_meeting(tmp_path)
    live.write_text(live.read_text() + SPEECH * 12)
    transcript = live.read_bytes()
    full = live_sidecar.sha(live.read_text())
    assert live_sidecar.remember(live, "transcript_sha256", full)
    gpath = tmp_path / "graph"
    (gpath / "Документация").mkdir(parents=True)
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(gpath))
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.setattr(debrief_refinement, "refine", lambda *a, **kw: "# Прежний разбор\n\nОбсудили макет.\n")
    destination = run(live, handoff)
    previous = destination.read_bytes()
    proof = pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=full)
    passport = live.with_name(live.name + ".live.json")
    binding = pipeline.binding_path(live)
    prior_passport, prior_binding = json.loads(passport.read_bytes()), json.loads(binding.read_bytes())
    monkeypatch.setattr(graph, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph, "harden_umask", lambda: None)
    monkeypatch.setattr(graph, "_open_progress", lambda *_: None)
    monkeypatch.setattr(graph, "_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(debrief_refinement, "refine", lambda *a, **kw: "# Новый разбор\n\nПроверили макет.\n")
    journal = pipeline.publication_path(live)
    pointer = pipeline._parent_ack_path(live, proof)
    candidate = []
    deferred = mode != "synchronous"
    monkeypatch.setattr(meeting_archive, "summary_pass",
                        lambda *a, **kw: meeting_archive.SummaryOutcome(meeting_archive.SummaryOutcome.NONE, None))
    monkeypatch.setattr(meeting_archive, "_derive_extras", lambda *a, **kw: None)
    monkeypatch.setattr(meeting_archive, "unhide_graph", lambda *a, **kw: None)
    archive = graph.archive_and_publish
    stamp = meeting_stamp.graph_key(live.parent, live.stem, gpath)
    archive(live, gpath, stamp)
    graph.publish_accepted_debrief(live, gpath, stamp)
    finder, = (gpath / meeting_archive.ARCHIVE_DIR).glob("*/Разбор.md")
    vault = gpath / "Документация" / "Стенограммы встреч" / destination.name
    assert finder.read_bytes() == vault.read_bytes() == previous

    def failed_archive(*args, **kwargs):
        candidate.append(destination.read_bytes())
        assert candidate[0] != previous
        assert pointer.exists() is deferred and journal.exists() is deferred
        archive(*args, **kwargs)
        raise OSError("synthetic archive write failure")

    monkeypatch.setattr(graph, "archive_and_publish", failed_archive)
    if mode == "synchronous":
        monkeypatch.setattr(graph.sys, "argv", ["graph_updater.py", str(live), "--skip-legacy-debrief",
                                                "--refine-before-archive", proof])
        with pytest.raises(OSError, match="synthetic archive write failure"):
            graph.main()
    else:
        (tmp_path / "config").mkdir()
        (tmp_path / "config/config.yaml").write_text("sufler:\n  graph: false\n  refined_debrief: true\n")
        monkeypatch.setattr(rebuild, "_root", lambda: tmp_path)
        monkeypatch.setattr(rebuild, "running_elsewhere", lambda *a: None)
        monkeypatch.setattr(rebuild, "mark_running", lambda *a: None)
        monkeypatch.setattr(rebuild, "_take_rebuild_queue", lambda: None)
        monkeypatch.setattr(rebuild, "_yield_to_live", lambda *a: None)
        monkeypatch.setattr(rebuild, "rebuild", lambda *a: live)
        monkeypatch.setattr(rebuild, "log", lambda *_: None)
        monkeypatch.setattr(rebuild.sys, "argv", ["rebuild_transcript.py", str(live)])

        def child(command, **kwargs):
            with monkeypatch.context() as child_patch:
                child_patch.setattr(graph.sys, "argv", command[1:])
                with pytest.raises(OSError, match="synthetic archive write failure"):
                    graph.main()
            assert journal.is_file() and pointer.is_file()
            assert destination.read_bytes() == candidate[0]
            assert finder.read_bytes() == vault.read_bytes() == previous
            if mode == "parent-crash":
                raise SystemExit("parent stopped before observing the child")
            return SimpleNamespace(returncode=1)

        monkeypatch.setattr(rebuild.subprocess, "run", child)
        if mode == "parent":
            with pytest.raises(RuntimeError, match="graph_updater"):
                rebuild.main()
        else:
            with pytest.raises(SystemExit, match="parent stopped"):
                rebuild.main()
            pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None)

    assert live.read_bytes() == transcript
    assert not journal.exists() and not pointer.exists()
    if deferred:
        assert destination.read_bytes() == finder.read_bytes() == vault.read_bytes() == previous
        assert json.loads(passport.read_bytes()) == prior_passport
        assert json.loads(binding.read_bytes()) == prior_binding
    else:
        assert destination.read_bytes() == finder.read_bytes() == candidate[0]
        assert pipeline.verify_publication(live) == destination


def test_accepted_debrief_without_configured_graph_stays_local(tmp_path):
    import graph_updater

    tmp_path = tmp_path / "local-meeting"
    tmp_path.mkdir()
    live = tmp_path / "2026-01-01_1200.md"
    debrief = tmp_path / "2026-01-01_1200_разбор.md"
    live.write_bytes(b"synthetic transcript\n")
    debrief.write_bytes(b"accepted local debrief\n")

    graph_updater.publish_accepted_debrief(live, None, live.stem)

    assert sorted(tmp_path.iterdir()) == [live, debrief]
    assert debrief.read_bytes() == b"accepted local debrief\n"


def test_graphless_verification_observes_source_edits_made_by_the_hook(tmp_path, monkeypatch):
    import debrief_refinement
    import graph_updater as graph
    import live_sidecar
    from test_debrief_pipeline import CFG, SPEECH, make_meeting, run

    live, handoff = make_meeting(tmp_path)
    live.write_text(live.read_text() + SPEECH * 12)
    assert live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(live.read_text()))
    monkeypatch.setattr(graph.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(debrief_refinement, "refine", lambda *a, **kw: "# Разбор\n\nПроверили макет.\n")
    destination = run(live, handoff)
    original = destination.read_bytes()
    monkeypatch.setattr(graph.sys, "argv", ["graph_updater.py", str(live), "--skip-legacy-debrief"])
    monkeypatch.setattr(graph, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph, "harden_umask", lambda: None)
    monkeypatch.setattr(graph, "_open_progress", lambda *_: None)
    calls = []

    def post_hook(_cfg, path, _stamp):
        calls.append(path)
        path.write_text(path.read_text() + "Уточнили срок.\n")

    monkeypatch.setattr(graph, "run_post_hook", post_hook)
    with pytest.raises(debrief_refinement.RefinementRefused, match="publication_source_changed"):
        graph.main()
    assert calls == [live]
    assert destination.read_bytes() == original


def test_existing_review_still_runs_the_meeting_hook_once(tmp_path, monkeypatch):
    import os
    import subprocess
    import graph_updater as graph
    from test_debrief_pipeline import CFG, SPEECH

    stamp = "2026-01-01_1200"
    live = tmp_path / (stamp + ".md")
    live.write_text(f"# Встреча {stamp}\n\n" + SPEECH * 12)
    revision = tmp_path / (stamp + "_ревизия_claude.md")
    revision.write_bytes(b"synthetic existing review\n")
    fresh = live.stat().st_mtime_ns + 1_000_000_000
    os.utime(revision, ns=(fresh, fresh))
    gpath = tmp_path / "graph"
    gpath.mkdir()
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(gpath))
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.setattr(graph.sys, "argv", ["graph_updater.py", str(live)])
    monkeypatch.setattr(graph, "load_cfg", lambda: CFG)
    monkeypatch.setattr(graph, "harden_umask", lambda: None)
    monkeypatch.setattr(graph, "_open_progress", lambda *_: None)
    monkeypatch.setattr(graph, "archive_and_publish", lambda *a, **kw: None)
    monkeypatch.setattr(graph.privacy, "cloud_enrich_enabled", lambda cfg: True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("existing review started a subprocess"))
    calls = []
    monkeypatch.setattr(graph, "run_post_hook", lambda cfg, path, key: calls.append((path, key)))

    graph.main()

    assert calls == [(live, stamp)]
    assert revision.read_bytes() == b"synthetic existing review\n"
