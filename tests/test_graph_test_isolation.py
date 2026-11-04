"""Test graph discovery stays synthetic when a case intentionally removes ENV."""
from __future__ import annotations

import os

import graphs


def _nested(run, tmp_path, body):
    home = tmp_path / "child-home"
    home.mkdir()
    return run(body, обвязка=True, префикс=("/usr/bin/env", f"HOME={home}"))


def test_env_clear_keeps_real_forget_planning_out_of_an_unrelated_vault(внутренний_pytest, tmp_path):
    result = _nested(внутренний_pytest, tmp_path, r'''
        import json
        import os
        import pathlib
        import sys
        import pytest
        import conftest
        import graphs
        import charoite_paths

        assert graphs.ICLOUD.is_relative_to(pathlib.Path.home())
        sys.path.insert(0, str(pathlib.Path(conftest.__file__).parents[1] / "scripts"))
        import forget_meeting as forget

        # Collection happens before the per-test fixture. This is an entirely
        # synthetic stand-in for a previously imported ambient discovery root.
        UNRELATED = pathlib.Path(__file__).parent / "unrelated-vault"
        CANARY = UNRELATED / "UnrelatedGraph" / "Встречи" / "2030-01-01_1000.md"
        CANARY.parent.mkdir(parents=True)
        CANARY.write_text("SYNTHETIC_UNRELATED_CANARY\n")
        graphs.ICLOUD = UNRELATED

        def test_actual_plan(tmp_path, monkeypatch):
            root = tmp_path / "data"
            (root / "config").mkdir(parents=True)
            (root / "transcripts").mkdir()
            stamp = "2030-01-01_1000"
            (root / "transcripts" / (stamp + ".md")).write_text("Synthetic transcript\n")
            selected = tmp_path / "configured-vault" / "SelectedGraph"
            note = selected / "Встречи" / (stamp + ".md")
            note.parent.mkdir(parents=True)
            note.write_text("Synthetic selected meeting\n")
            (root / "config" / "config.yaml").write_text(
                "sufler:\n  graph_dir: " + json.dumps(str(selected)) + "\n")
            charoite_paths.use_data_root(root, replace=True)
            for name in graphs.ENV_GRAPH_NAMES:
                monkeypatch.delenv(name, raising=False)
            iterdir, read_bytes = pathlib.Path.iterdir, pathlib.Path.read_bytes
            def bounded_list(path):
                if path.is_relative_to(UNRELATED):
                    pytest.fail("unrelated synthetic vault was traversed")
                return iterdir(path)
            def bounded_read(path):
                if path.is_relative_to(UNRELATED):
                    pytest.fail("unrelated synthetic vault was read")
                return read_bytes(path)
            monkeypatch.setattr(pathlib.Path, "iterdir", bounded_list)
            monkeypatch.setattr(pathlib.Path, "read_bytes", bounded_read)
            planned = forget.plan(stamp, root)
            assert note in planned.delete
            assert set(planned.graph_scope) == {selected}
            assert CANARY not in planned.delete and CANARY not in planned.edit
            assert read_bytes(CANARY) == b"SYNTHETIC_UNRELATED_CANARY\n"
    ''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_an_explicit_synthetic_icloud_override_still_discovers_its_graph(tmp_path, monkeypatch):
    vault = tmp_path / "explicit-vault"
    graph = vault / "ExplicitGraph"
    (graph / "Встречи").mkdir(parents=True)
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(graphs, "ICLOUD", vault)
    assert graphs.all_graphs("Встречи") == [graph]


def test_fallback_graph_isolation_survives_the_shared_monkeypatch_undo(tmp_path, monkeypatch):
    fallback = graphs.ICLOUD
    assert fallback.is_relative_to(tmp_path) and not list(fallback.iterdir())
    monkeypatch.setenv("SYNTHETIC_GRAPH_ISOLATION_PROBE", "set")
    monkeypatch.undo()
    for name in graphs.ENV_GRAPH_NAMES:
        os.environ.pop(name, None)
    assert graphs.ICLOUD == fallback
    assert graphs.roots() == [fallback]
    assert graphs.all_graphs("Встречи") == []


def test_fallback_is_restored_after_an_exception_and_between_real_fixture_lifecycles(внутренний_pytest, tmp_path):
    result = _nested(внутренний_pytest, tmp_path, r'''
        import pathlib
        import pytest
        import graphs

        assert graphs.ICLOUD.is_relative_to(pathlib.Path.home())
        ORIGINAL = pathlib.Path(__file__).parent / "synthetic-original-vault"
        ORIGINAL.mkdir()
        graphs.ICLOUD = ORIGINAL

        class Audit:
            restored = []
            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_runtest_teardown(self, item, nextitem):
                yield
                self.restored.append(graphs.ICLOUD)
            def pytest_sessionfinish(self, session, exitstatus):
                assert self.restored == [ORIGINAL, ORIGINAL]
                assert graphs.ICLOUD == ORIGINAL
        audit = Audit()

        @pytest.mark.xfail(raises=RuntimeError, strict=True, reason="Synthetic exception tests fixture cleanup")
        def test_first_exception(tmp_path, monkeypatch, pytestconfig):
            pytestconfig.pluginmanager.register(audit, "synthetic-graph-cleanup-audit")
            assert graphs.ICLOUD.is_relative_to(tmp_path)
            override = tmp_path / "explicit-override"
            override.mkdir()
            monkeypatch.setattr(graphs, "ICLOUD", override)
            raise RuntimeError("Synthetic exception after an explicit graph override")

        def test_second_is_a_fresh_empty_vault(tmp_path):
            assert audit.restored == [ORIGINAL]
            assert graphs.ICLOUD.is_relative_to(tmp_path)
            assert not list(graphs.ICLOUD.iterdir())
    ''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 xfailed" in result.stdout
