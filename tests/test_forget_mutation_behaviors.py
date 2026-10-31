"""Наблюдаемые отказы уточнения плана и контракт индексных файлов."""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import socket
import subprocess
import sys

import pytest

from test_forget_night_processes import REPO, STAMP, OTHER, THEME, _world, _body, _seed_indexes
from test_forget_index_recovery import _neighbor, NEIGHBOR
import charoite_paths
import file_locks
import forget_meeting as forget
import nightly_dossier as night
from charoite_graph import dossier
from charoite_schema import CHAROITE


def _environment(root):
    needed = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
              "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING")
    return {**{name: os.environ[name] for name in needed if name in os.environ},
            "CHAROITE_ROOT": str(root)}


def _run(root, graphs, case):
    return subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()), case,
                           str(root), *(str(g) for g in graphs)], cwd=REPO,
                          env=_environment(root),
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("case", ["scan-error", "new-graph", "author-edit", "archive-owner"])
def test_cli_refuses_before_deletion_when_locked_rescan_is_not_safe(tmp_path, case):
    root, graphs = _world(tmp_path, second_graph=case == "new-graph")
    archive = graphs[0] / forget.ARCHIVE_DIR / "Unclaimed"
    note = graphs[0] / "Ядра" / "Existing note.md"
    if case == "archive-owner":
        archive.mkdir(parents=True)
        note = archive / "note.md"
    if case in ("author-edit", "archive-owner"):
        note.write_text(f"# Keep this heading\n- [[Встречи/{STAMP}]] synthetic fact\n", encoding="utf-8")
    result = _run(root, graphs, case)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "ничего не сделано" in result.stdout.lower(), result.stdout
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert (graphs[0] / "Встречи" / f"{STAMP}.md").exists()
    if case in ("author-edit", "archive-owner"):
        assert STAMP in note.read_text(encoding="utf-8")
    if case == "author-edit":
        assert "Author edit after rescan" in note.read_text(encoding="utf-8")
    if case == "new-graph":
        assert (graphs[1] / "Встречи" / f"{STAMP}.md").exists()


def test_new_link_is_cleaned_and_new_manual_dossier_is_reported_as_partial(tmp_path):
    root, graphs = _world(tmp_path)
    result = _run(root, graphs, "new-traces")
    assert result.returncode == 0, result.stdout + result.stderr
    note = graphs[0] / "Ядра" / "New note.md"
    assert "Keep unrelated content" in note.read_text(encoding="utf-8")
    assert STAMP not in note.read_text(encoding="utf-8")
    manual = graphs[0] / CHAROITE.dossier_dir / "Manual dossier.md"
    assert "Author note" in manual.read_text(encoding="utf-8")
    assert "забыто частично" in result.stdout and "проверь сам" in result.stdout
    assert "Manual dossier.md" in result.stdout and "есть правки автора" in result.stdout


def test_cli_day_does_not_hide_a_refused_meeting_behind_a_successful_one(tmp_path):
    root, graphs = _world(tmp_path)
    later = "2030-01-01_1100"
    (root / "transcripts" / f"{later}.md").write_text("# Synthetic later meeting\n", encoding="utf-8")
    (graphs[0] / "Встречи" / f"{later}.md").write_text("# Synthetic later meeting\n", encoding="utf-8")
    result = _run(root, graphs, "mixed-day")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert (graphs[0] / "Встречи" / f"{STAMP}.md").exists()
    assert not (root / "transcripts" / f"{later}.md").exists()
    assert not (graphs[0] / "Встречи" / f"{later}.md").exists()


def test_explicit_plan_without_rescan_still_deletes_only_its_requested_files(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    transcript = root / "transcripts" / f"{STAMP}.md"
    node = graph / "Встречи" / f"{STAMP}.md"
    p = forget.Plan(stamp=STAMP, delete=[transcript, node],
                    lock_dirs={charoite_paths.graph_lock_dir(graph, root=root): graph})
    monkeypatch.setattr(forget, "brain_forget", lambda *args, **kwargs: "")
    forget.apply(p, yes=True)
    assert not transcript.exists() and not node.exists()
    assert (graph / "Встречи" / f"{OTHER}.md").exists()


def test_repair_command_respects_the_explicit_plan_root(tmp_path, monkeypatch, capsys):
    root, graphs = _world(tmp_path / "explicit root")
    graph = graphs[0]
    _seed_indexes(graph)
    _neighbor(graph)
    dossier.refresh_index(graph / CHAROITE.dossier_dir, [THEME, NEIGHBOR])
    assert root != forget._root(), "фикстура проверяет явный корень, отличающийся от корня вызывающего"
    original = pathlib.Path.replace

    def replace(path, target):
        if pathlib.Path(target) == graph / CHAROITE.dossier_dir / dossier.INDEX_MD:
            raise OSError("synthetic Markdown fault")
        return original(path, target)

    monkeypatch.setattr(pathlib.Path, "replace", replace)
    monkeypatch.setattr(forget, "brain_forget", lambda *args, **kwargs: "")
    forget.apply(forget.plan(STAMP, root, graph), yes=True)
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("обновить индексы: "))
    command = shlex.split(line.removeprefix("обновить индексы: "))
    key, value = command.pop(0).split("=", 1)
    assert key == "CHAROITE_ROOT" and pathlib.Path(value) == root
    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text('llm:\n  model: ""\n', encoding="utf-8")
    result = subprocess.run(command, cwd=REPO, env=_environment(value),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {NEIGHBOR}
    assert THEME not in (graph / CHAROITE.dossier_dir / dossier.INDEX_MD).read_text(encoding="utf-8")


def test_nightly_clears_both_stale_indexes_even_without_any_existing_dossier(tmp_path):
    root, graphs = _world(tmp_path)
    _seed_indexes(graphs[0], dossier_file=False)
    result = _run(root, graphs, "empty-index")
    assert result.returncode == 0, result.stdout + result.stderr
    folder = graphs[0] / CHAROITE.dossier_dir
    assert dossier.load_index(folder) == []
    assert THEME not in (folder / dossier.INDEX_MD).read_text(encoding="utf-8")


def test_nightly_index_failure_contributes_to_the_cli_failure_verdict(tmp_path):
    root, graphs = _world(tmp_path)
    _seed_indexes(graphs[0])
    _neighbor(graphs[0])
    for last in ("Last topic", "Second failing topic"):
        (graphs[0] / "Ядра" / f"{last}.md").write_text(f"# {last}\n", encoding="utf-8")
        for stamp in (STAMP, OTHER):
            p = graphs[0] / "Встречи" / f"{stamp}.md"
            p.write_text(p.read_text(encoding="utf-8") + f"[[Ядра/{last}]]\n", encoding="utf-8")
    result = _run(root, graphs, "index-error-verdict")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "индекс не обновлён" in result.stdout


def test_dossier_index_is_a_readable_utf8_version_one_document(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    title = "Живое досье"
    (graph / "Ядра" / f"{title}.md").write_text("# Живая тема\n", encoding="utf-8")
    for stamp in (STAMP, OTHER):
        path = graph / "Встречи" / f"{stamp}.md"
        path.write_text(path.read_text(encoding="utf-8") + f"[[Ядра/{title}]]\n", encoding="utf-8")
    (graph / CHAROITE.dossier_dir / f"{title}.md").write_text(
        "## Сейчас\nQuartzmarker — синтетический итог\n", encoding="utf-8")
    result = _run(root, graphs, "empty-index")
    assert result.returncode == 0, result.stdout + result.stderr
    raw = (graph / CHAROITE.dossier_dir / dossier.INDEX_JSON).read_bytes()
    payload = json.loads(raw)
    assert payload["версия"] == 1 and {e["тема"] for e in payload["досье"]} == {title}
    assert title.encode("utf-8") in raw, "имя темы должно читаться и искаться в обычном UTF-8 файле"
    assert "quartzmarker" in payload["досье"][0]["ключи"], "поиск должен видеть факт из тела досье"


def _worker(case, root, graphs):
    charoite_paths.use_data_root(root, replace=True)

    def denied(*args, **kwargs):
        raise AssertionError("network is forbidden")

    socket.socket.connect = denied
    socket.create_connection = denied
    forget._graph_roots = lambda explicit: [explicit] if explicit else graphs
    forget.brain_forget = lambda *args, **kwargs: ""
    forget._brain_flags = lambda *args: {"brain_enabled": False, "brain_explicit": False}
    if case in ("empty-index", "index-error-verdict"):
        night.cfg = lambda: {}
        night.live_gate.night_window_open = lambda *args, **kwargs: True
        night.generate = lambda theme, *args, **kwargs: (
            "invalid" if theme in ("Last topic", "Second failing topic") else _body())
        if case == "index-error-verdict":
            original = pathlib.Path.replace

            def replace(path, target):
                if pathlib.Path(target).name == dossier.INDEX_MD:
                    raise OSError("synthetic Markdown fault")
                return original(path, target)

            pathlib.Path.replace = replace
        sys.argv = ["nightly_dossier.py", "--graph", str(graphs[0])]
        sys.argv += ["--limit", "0"] if case == "empty-index" else ["--full"]
        return night.main()
    original = forget.plan
    calls = 0

    def plan(*args, **kwargs):
        nonlocal calls
        calls += 1
        invocation = calls
        if invocation == 2 and case in ("scan-error", "mixed-day"):
            raise OSError("synthetic inventory fault")
        p = original(*args, **kwargs)
        if invocation == 1:
            graph = graphs[-1] if case == "new-graph" else graphs[0]
            lock = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
            with file_locks.graph_lock(lock, 0) as taken:
                assert taken
                if case == "new-graph":
                    (graph / "Встречи" / f"{STAMP}.md").write_text("# Republished meeting\n", encoding="utf-8")
                elif case == "archive-owner":
                    (graph / forget.ARCHIVE_DIR / "Unclaimed" / "meeting.meta.json").write_text(
                        json.dumps({"meeting_id": STAMP}), encoding="utf-8")
                elif case == "new-traces":
                    (graph / "Ядра" / "New note.md").write_text(
                        f"# Keep unrelated content\n- [[Встречи/{STAMP}]] synthetic fact\n", encoding="utf-8")
                    files, _ = dossier.scan(graph, schema=CHAROITE)
                    text = dossier.render("Manual dossier", _body(), list(files), files, "fp", "2030-01-04")
                    text = text.replace("## Правки автора\n\n—\n", "## Правки автора\n\nAuthor note\n")
                    (graph / CHAROITE.dossier_dir / "Manual dossier.md").write_text(text, encoding="utf-8")
        if invocation == 2 and case == "author-edit":
            note = graphs[0] / "Ядра" / "Existing note.md"
            note.write_text(note.read_text(encoding="utf-8") + "Author edit after rescan\n", encoding="utf-8")
        return p

    forget.plan = plan
    sys.argv = ["forget_meeting.py", STAMP[:10] if case == "mixed-day" else STAMP, "--yes"]
    if len(graphs) == 1:
        sys.argv += ["--graph", str(graphs[0])]
    return forget.main()


if __name__ == "__main__":
    raise SystemExit(_worker(sys.argv[1], pathlib.Path(sys.argv[2]), [pathlib.Path(p) for p in sys.argv[3:]]))
