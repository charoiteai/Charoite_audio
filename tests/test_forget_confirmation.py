"""Two-process deletion consent: synthetic files only, no brain/network access."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys

import pytest

from test_forget_meeting import forget, charoite_paths, _world, STAMP, OTHER, REPO


def _cli(root, graph, *args):
    # Exercise the actual parser/main in a fresh interpreter. Only the brain side
    # effect is stubbed; socket use is forbidden even if product code regresses.
    code = """
import sys, socket
sys.path.insert(0, 'scripts')
import forget_meeting as f
def denied(*a, **kw):
    raise AssertionError('synthetic worker must never access the network')
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
f.brain_forget = lambda *a, **kw: ''
sys.exit(f.main())
"""
    return subprocess.run([sys.executable, "-c", code, STAMP, "--graph", str(graph), *args],
                          cwd=REPO, env=dict(os.environ, CHAROITE_ROOT=str(root)),
                          capture_output=True, text=True, timeout=20)


def _preview(root, graph):
    result = _cli(root, graph, "--plan-json")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _files(root, graph):
    return {str(p): p.read_bytes() for base in (root, graph)
            for p in base.rglob("*") if p.is_file() and not p.is_symlink()
            and not p.name.endswith(".lock")}


@pytest.mark.parametrize("change", ["add", "modify", "replace", "remove", "archive", "edit", "symlink"])
def test_stale_preview_refuses_every_effect_in_second_process(tmp_path, change):
    root, graph = _world(tmp_path)
    preview = _preview(root, graph)
    target = root / "transcripts" / f"{STAMP}.md"
    if change == "add":
        (root / "transcripts" / f"{STAMP}_new.md").write_text("new unapproved artifact")
    elif change == "modify":
        st = target.stat()
        target.write_bytes(b"x" * st.st_size)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif change == "replace":
        replacement = target.with_suffix(".replacement")
        replacement.write_bytes(target.read_bytes())
        replacement.replace(target)
    elif change == "remove":
        target.unlink()
    elif change == "archive":
        folder = next(p for p in (graph / forget.ARCHIVE_DIR).iterdir() if p.name.startswith(STAMP[:10]))
        (folder / "unapproved.bin").write_bytes(b"private new contents")
    elif change == "edit":
        core = next((graph / "Ядра").iterdir())
        core.write_text(core.read_text() + "\nNew user note\n")
    else:
        target.unlink()
        target.symlink_to(root / "transcripts" / f"{OTHER}.md")
    before = _files(root, graph)
    result = _cli(root, graph, "--yes", "--expected-plan", preview["token"])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ничего не сделано" in result.stdout
    assert _files(root, graph) == before
    assert not list(graph.rglob(forget.BACKUP_DIR))


def test_unchanged_preview_applies_and_preserves_other_meeting(tmp_path):
    root, graph = _world(tmp_path)
    preview = _preview(root, graph)
    assert str(root / "transcripts" / f"{STAMP}.md") in preview["text"]
    assert _preview(root, graph)["token"] == preview["token"]
    neighbor = root / "transcripts" / f"{OTHER}.md"
    before = neighbor.read_bytes()
    result = _cli(root, graph, "--yes", "--expected-plan", preview["token"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "transcripts" / f"{STAMP}.md").exists()
    assert neighbor.read_bytes() == before
    assert list(graph.rglob(forget.BACKUP_DIR))


@pytest.mark.parametrize("change", ["graph-add", "graph-edit", "archive-child", "replace", "edit"])
def test_final_rescan_under_locks_cannot_expand_confirmed_plan(tmp_path, monkeypatch, change):
    root, graph = _world(tmp_path)
    charoite_paths.use_data_root(root, replace=True)
    p = forget.plan(STAMP, root, graph)
    p.expected_token = forget.confirmation_token(p)
    original = forget.file_locks.graph_locks
    added = graph / "Документация" / "Стенограммы встреч" / f"{STAMP}_late.md"

    @contextlib.contextmanager
    def locks(*args):
        with original(*args) as taken:
            assert taken
            if change == "graph-add":
                added.write_text("new trace after initial confirmation check")
            elif change == "graph-edit":
                (graph / "Люди" / "Late.md").write_text(f"new reference [[Встречи/{STAMP}]]")
            elif change == "archive-child":
                folder = next(path for path in p.delete if path.is_dir())
                (folder / "late.txt").write_text("late child")
            elif change == "replace":
                path = root / "transcripts" / f"{STAMP}.md"
                replacement = path.with_suffix(".new")
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            else:
                core = next(iter(p.edit))
                core.write_text(core.read_text() + "\nlate edit")
            yield taken

    monkeypatch.setattr(forget.file_locks, "graph_locks", locks)
    def forbidden(*args, **kwargs):
        raise AssertionError("stale confirmation reached a mutation")
    monkeypatch.setattr(forget, "_apply_locked", forbidden)
    monkeypatch.setattr(forget, "brain_forget", forbidden)
    assert not forget.apply(p, yes=True)
    assert p.refused
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert not list(graph.rglob(forget.BACKUP_DIR))


@pytest.mark.parametrize("flags", [("--expected-plan", "a" * 64),
                                    ("--yes", "--expected-plan", "bad"),
                                    ("--yes", "--plan-json")])
def test_invalid_protocol_arguments_never_delete(tmp_path, flags):
    root, graph = _world(tmp_path)
    before = _files(root, graph)
    result = _cli(root, graph, *flags)
    assert result.returncode == 2
    assert _files(root, graph) == before


def test_legacy_preview_and_yes_remain_supported(tmp_path):
    root, graph = _world(tmp_path)
    result = _cli(root, graph)
    assert result.returncode == 0 and (root / "transcripts" / f"{STAMP}.md").exists()
    (root / "transcripts" / f"{STAMP}_late.md").write_text("legacy CLI includes current files")
    result = _cli(root, graph, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "transcripts" / f"{STAMP}_late.md").exists()


def test_changed_scope_options_refuse_even_if_file_effects_are_identical(tmp_path):
    root, graph = _world(tmp_path)
    preview = _preview(root, graph)
    result = _cli(root, graph, "--yes", "--expected-plan", preview["token"],
                  "--import-folder", str(tmp_path / "empty-import"))
    assert result.returncode == 1
    assert (root / "transcripts" / f"{STAMP}.md").exists()


def test_unrelated_file_does_not_invalidate_confirmation(tmp_path):
    root, graph = _world(tmp_path)
    preview = _preview(root, graph)
    unrelated = root / "transcripts" / "2031-01-01_1200.md"
    unrelated.write_text("another meeting")
    result = _cli(root, graph, "--yes", "--expected-plan", preview["token"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert unrelated.read_text() == "another meeting"


@pytest.mark.parametrize("scope", ["root", "graph"])
def test_empty_plan_binds_directory_identity_even_without_file_actions(tmp_path, scope):
    root, graph = tmp_path / "data", tmp_path / "graph"
    root.mkdir()
    graph.mkdir()
    p = forget.Plan(STAMP, data_root=root, graph_scope=(graph,))
    token = forget.confirmation_token(p)
    directory = root if scope == "root" else graph
    directory.rename(directory.with_name(directory.name + "-old"))
    directory.mkdir()
    assert forget.confirmation_token(p) != token


def test_graph_scope_is_part_of_consent_even_when_root_and_actions_match(tmp_path):
    p = forget.Plan(STAMP, data_root=tmp_path, graph_scope=())
    local_only = forget.confirmation_token(p)
    p.graph_scope = (tmp_path,)
    assert forget.confirmation_token(p) != local_only


def test_v1_token_wire_compatibility():
    # A filesystem-independent protocol vector. Previously issued tokens must
    # keep their meaning across interpreters/processes, including Unicode and
    # reordered caller dictionaries. Change this vector only with a protocol bump.
    p = forget.Plan("2030-01-01_1000", notes=["Встреча"],
                    edit={}, graph_scope=())
    assert forget.confirmation_token(p, ("scope",)) == "3c429cc3ddabeb8e84984d3b31731d156a77ae7a34bf66ff87f3395c5971f77b"


@pytest.mark.parametrize("yes", [False, True])
def test_stale_plan_is_rejected_before_locks_only_when_applying(tmp_path, monkeypatch, yes):
    root, graph = _world(tmp_path)
    charoite_paths.use_data_root(root, replace=True)
    p = forget.plan(STAMP, root, graph)
    p.expected_token = forget.confirmation_token(p)
    path = root / "transcripts" / f"{STAMP}.md"
    path.write_text("changed after preview")

    def forbidden(*args, **kwargs):
        raise AssertionError("stale consent must not wait for locks or perform mutations")

    monkeypatch.setattr(forget.file_locks, "graph_locks", forbidden)
    monkeypatch.setattr(forget, "_apply_locked", forbidden)
    assert forget.apply(p, yes=yes) is False
    assert p.refused is yes
    assert path.read_text() == "changed after preview"


def _main(root, graph, monkeypatch, *args, target=STAMP):
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(sys, "argv", ["forget_meeting.py", target, "--graph", str(graph), *args])
    return forget.main()


@pytest.mark.parametrize("mode", ["preview", "apply"])
def test_two_step_protocol_refuses_a_day_with_multiple_meetings(tmp_path, monkeypatch, capsys, mode):
    root, graph = _world(tmp_path)
    preview = _preview(root, graph)
    (root / "transcripts" / f"{STAMP[:10]}_1500.md").write_text("second meeting that day")
    before = _files(root, graph)
    flags = ("--plan-json",) if mode == "preview" else ("--yes", "--expected-plan", preview["token"])
    assert _main(root, graph, monkeypatch, *flags, target=STAMP[:10]) == 1
    assert "точный штамп одной встречи" in capsys.readouterr().out
    assert _files(root, graph) == before


def test_preview_snapshot_failure_returns_failure_and_no_token(tmp_path, monkeypatch, capsys):
    root, graph = _world(tmp_path)
    before = _files(root, graph)

    def unreadable(*args, **kwargs):
        raise PermissionError("synthetic target cannot be inspected")

    monkeypatch.setattr(forget, "confirmation_token", unreadable)
    assert _main(root, graph, monkeypatch, "--plan-json") == 1
    output = capsys.readouterr().out
    assert "план не подтверждён" in output
    assert '"token"' not in output
    assert _files(root, graph) == before


def test_json_preview_returns_success_and_readable_text_without_mutations(tmp_path, monkeypatch, capsys):
    root, graph = _world(tmp_path)
    before = _files(root, graph)
    assert _main(root, graph, monkeypatch, "--plan-json") == 0
    output = capsys.readouterr().out
    preview = json.loads(output)
    assert f"Встреча {STAMP}" in output
    assert f"Встреча {STAMP}" in preview["text"]
    assert len(preview["token"]) == 64
    assert _files(root, graph) == before


def test_equivalent_brain_options_do_not_change_confirmation(tmp_path, monkeypatch, capsys):
    root, graph = _world(tmp_path)
    flags = {"brain_enabled": False, "brain_explicit": True}
    monkeypatch.setattr(forget, "_brain_flags", lambda root: flags)
    assert _main(root, graph, monkeypatch, "--plan-json") == 0
    first = json.loads(capsys.readouterr().out)
    flags = dict(reversed(list(flags.items())))
    assert _main(root, graph, monkeypatch, "--plan-json") == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first
