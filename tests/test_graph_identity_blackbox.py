"""Actual CLI regressions, with legacy addresses computed independently.

The children use disposable HOME/data/vault roots and reject model/network access
before importing product modules. No identity resolver or lock planner is mocked.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
STAMP = "2030-01-01_1000"
TOKEN = "SYNTHETIC_IDENTITY_BLACKBOX_857"
ORIGINAL = f"**Поручения:**\n- Role — {TOKEN}\n".encode()
RELATIVE_MINUTE = Path("Встречи-архив/2030-01-01 10-00 — Synthetic topic/Минутки.md")

BOOTSTRAP = """import os, socket, sys, types
def denied(*args, **kwargs):
    with open(os.environ['IDENTITY_NETWORK_LOG'], 'a') as stream:
        stream.write('prohibited network/model access\\n')
    raise AssertionError('synthetic identity test: network/model access prohibited')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
socket.getaddrinfo = denied
brain = types.ModuleType('brain')
brain.__getattr__ = denied
sys.modules['brain'] = brain
"""

# These are real CLI main entry points. Only the busy-lock wait and the external
# brain operation are replaced; graph discovery, planning and file effects run.
MAIN_ENTRY = """import os, pathlib, sys
import charoite_paths, graphs
root = pathlib.Path(os.environ['CHAROITE_ROOT'])
charoite_paths.use_data_root(root, replace=True)
assert graphs.ICLOUD.is_relative_to(pathlib.Path.home())
assert not graphs.ICLOUD.exists()
mode = sys.argv.pop(1)
if mode == 'fix':
    import fix_action_items as cli
else:
    import forget_meeting as cli
    cli.brain_forget = lambda *args, **kwargs: ''
cli.LOCK_WAIT = 0.05
sys.argv[0] = cli.__file__
raise SystemExit(cli.main())
"""


def _legacy_base(data, graph):
    """The exact pre-identity formula, without importing the new implementation."""
    resolved = graph.resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return data / "backups" / f"{resolved.name}-{digest}"


def _sandbox(tmp_path, *, require_case_alias):
    home, data, vault, bootstrap = [tmp_path / name for name in
                                    ("home", "data", "vault", "bootstrap")]
    for folder in (home, data / "config", data / "transcripts", vault, bootstrap):
        folder.mkdir(parents=True)
    physical = vault / "WorkCase"
    physical.mkdir()
    configured = physical.with_name("workcase")
    equivalent = configured.exists() and configured.samefile(physical)
    if require_case_alias and not equivalent:
        pytest.skip("filesystem does not equate actual WorkCase/workcase directories")
    if not equivalent:
        configured = physical
    (physical / "Встречи").mkdir()
    (physical / "Встречи" / f"{STAMP}.md").write_text("# Synthetic meeting\n")
    minute = physical / RELATIVE_MINUTE
    minute.parent.mkdir(parents=True)
    minute.write_bytes(ORIGINAL)
    (data / "transcripts" / f"{STAMP}.md").write_text(f"Synthetic transcript {TOKEN}\n")
    (data / "config/config.yaml").write_text(
        "sufler:\n  graph_dir: " + json.dumps(str(configured)) + "\n")
    (bootstrap / "sitecustomize.py").write_text(BOOTSTRAP)
    network_log = bootstrap / "network-attempts.txt"
    # A new mapping deliberately excludes inherited graph overrides, credentials,
    # proxy settings and model endpoints. Descendants inherit the same tripwire.
    env = {
        "PATH": os.defpath,
        "HOME": str(home),
        "CHAROITE_ROOT": str(data),
        "LANG": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(map(str, (bootstrap, REPO / "src", REPO / "scripts"))),
        "TMPDIR": str(tmp_path),
        "IDENTITY_NETWORK_LOG": str(network_log),
    }
    return data, physical, configured, minute, env


def _invoke(env, mode, *arguments):
    result = subprocess.run(
        [sys.executable, "-c", MAIN_ENTRY, mode, *map(str, arguments)],
        cwd=REPO, env=env, text=True, capture_output=True, timeout=15,
    )
    log = Path(env["IDENTITY_NETWORK_LOG"])
    assert not log.exists(), "a CLI attempted prohibited network/model access"
    return result


def _files(*roots):
    # Opening an empty lock file is permissible; existing payload bytes, newly
    # written payloads and deletion are all included in the refusal comparison.
    return {(str(root), str(path.relative_to(root))): path.read_bytes()
            for root in roots for path in root.rglob("*")
            if path.is_file() and path.name != "cloud.lock"}


@contextlib.contextmanager
def _held_legacy_lock(data, configured):
    base = _legacy_base(data, configured)
    base.mkdir(parents=True, exist_ok=True)
    with (base / "cloud.lock").open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield base
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _writer_copy(data, physical, minute, env):
    result = _invoke(env, "fix", "--graph", physical, "--apply")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert minute.read_bytes() != ORIGINAL and TOKEN.encode() in minute.read_bytes()
    copies = list((data / "backups").rglob("Минутки.md"))
    manifests = list((data / "backups").rglob("manifest.tsv"))
    assert len(copies) == len(manifests) == 1, (copies, manifests)
    copy, manifest = copies[0], manifests[0]
    assert copy.read_bytes() == ORIGINAL
    assert str(RELATIVE_MINUTE) in manifest.read_text()
    return copy, manifest


def test_explicit_writer_refuses_held_configured_legacy_lock(tmp_path):
    data, physical, configured, minute, env = _sandbox(tmp_path, require_case_alias=True)
    assert physical.samefile(configured)
    assert _legacy_base(data, physical) != _legacy_base(data, configured)
    with _held_legacy_lock(data, configured):
        before = _files(data, physical)
        result = _invoke(env, "fix", "--graph", physical, "--apply")
        unchanged = _files(data, physical) == before
    assert result.returncode == 1 and unchanged, (
        f"held configured legacy lock: rc={result.returncode}, payload_unchanged={unchanged}",
        result.stdout, result.stderr,
    )
    assert minute.read_bytes() == ORIGINAL


def test_automatic_forget_locks_empty_legacy_sibling_of_backup_only_actions(tmp_path):
    data, physical, configured, minute, env = _sandbox(tmp_path, require_case_alias=True)
    copy, manifest = _writer_copy(data, physical, minute, env)
    legacy = _legacy_base(data, configured)
    assert not copy.is_relative_to(legacy)
    # Retain graph discovery markers, but leave no graph payload action. The only
    # graph-owned actions are now in the real writer's primary backup address.
    minute.unlink()
    (physical / "Встречи" / f"{STAMP}.md").unlink()
    with _held_legacy_lock(data, configured) as held:
        assert [p.name for p in held.rglob("*") if p.is_file()] == ["cloud.lock"]
        before = _files(data, physical)
        result = _invoke(env, "forget", STAMP, "--yes")
        unchanged = _files(data, physical) == before
    assert result.returncode == 1 and unchanged, (
        f"held empty legacy sibling: rc={result.returncode}, payload_unchanged={unchanged}",
        result.stdout, result.stderr,
    )
    assert copy.read_bytes() == ORIGINAL and str(RELATIVE_MINUTE) in manifest.read_text()


def test_released_forget_removes_writer_copy_but_keeps_unknown_same_name_mirror(tmp_path):
    data, physical, configured, minute, env = _sandbox(tmp_path, require_case_alias=False)
    copy, manifest = _writer_copy(data, physical, minute, env)
    unknown_graph = tmp_path / "unconfigured-vault" / physical.name
    unknown_graph.mkdir(parents=True)
    assert unknown_graph.name == physical.name and not unknown_graph.samefile(physical)
    unknown = _legacy_base(data, unknown_graph)
    unknown_run = unknown / "fix_action_items" / "synthetic-run"
    unknown_copy = unknown_run / RELATIVE_MINUTE
    unknown_copy.parent.mkdir(parents=True)
    unknown_copy.write_bytes(ORIGINAL)
    (unknown_run / "manifest.tsv").write_text(
        "file\tbefore\tafter\n" + str(RELATIVE_MINUTE) + "\tsynthetic\tsynthetic\n")
    with _held_legacy_lock(data, configured):
        unknown_before = _files(unknown)
    result = _invoke(env, "forget", STAMP, "--yes")  # automatic discovery, after release
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not copy.exists(), "the real writer's new backup survived successful forget"
    assert not manifest.exists() or str(RELATIVE_MINUTE) not in manifest.read_text()
    assert _files(unknown) == unknown_before, "an unproven same-name mirror was changed"
    assert unknown_copy.read_bytes() == ORIGINAL
