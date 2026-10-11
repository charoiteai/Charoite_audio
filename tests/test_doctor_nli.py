"""NLI diagnostics distinguish installed files from working inference."""
import sys
from pathlib import Path
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import doctor
import charoite_paths


@pytest.fixture
def root(tmp_path):
    charoite_paths.use_data_root(tmp_path, replace=True)
    doctor.issues = 0
    yield tmp_path
    doctor.issues = 0


def test_default_doctor_names_missing_optional_nli(root, monkeypatch, capsys):
    for name in ("check_python", "check_deps", "check_ollama", "check_stt", "check_models",
                 "check_engine", "check_llm_alive", "check_strict_json", "check_pipeline",
                 "check_import_queue", "check_disk"):
        monkeypatch.setattr(doctor, name, lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "check_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["doctor.py"])
    doctor.main()
    out = capsys.readouterr().out
    assert "NLI" in out
    assert "model.onnx" in out and "config.json" in out
    assert doctor.issues == 0


def test_probe_is_a_dedicated_local_route(root, monkeypatch):
    calls = []
    monkeypatch.setattr(doctor, "check_nli", lambda **kwargs: calls.append(kwargs) or True,
                        raising=False)
    monkeypatch.setattr(doctor, "check_config", lambda: pytest.fail("must not check LLM"))
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--probe-nli"])
    with pytest.raises(SystemExit) as done:
        doctor.main()
    assert done.value.code == 0
    assert calls == [{"probe": True}]


@pytest.fixture
def files(root, monkeypatch):
    folder = root / "models/nli"
    folder.mkdir(parents=True)
    for name in ("model.onnx", "tokenizer.json", "config.json"):
        (folder / name).write_text("fixture")
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: object())
    return folder


def test_files_alone_never_claim_success_or_load_model(files, monkeypatch, capsys):
    import nli
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("must not load NLI"))
    assert doctor.check_nli() is None
    out = capsys.readouterr().out
    assert "инференс не проверен" in out
    assert " ✓ NLI" not in out
    assert doctor.issues == 0


def test_missing_metadata_names_the_exact_file(files, capsys):
    (files / "config.json").unlink()
    assert doctor.check_nli() is False
    assert "config.json" in capsys.readouterr().out
    assert doctor.issues == 0


def test_missing_package_is_not_a_model_success(files, monkeypatch, capsys):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None if name == "tokenizers" else object())
    assert doctor.check_nli() is False
    assert "tokenizers" in capsys.readouterr().out


@pytest.mark.parametrize("ready", [True, False])
def test_explicit_probe_reports_actual_judge_readiness(files, monkeypatch, capsys, ready):
    import nli
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: False)
    monkeypatch.setattr(nli, "judge", lambda: types.SimpleNamespace(refused="", ready=lambda: ready))
    assert doctor.check_nli(probe=True) is ready
    assert (" ✓ NLI" in capsys.readouterr().out) is ready
    assert doctor.issues == int(not ready)


def test_live_meeting_defers_probe_before_loading_model(files, monkeypatch, capsys):
    import nli
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: True)
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("meeting has priority"))
    assert doctor.check_nli(probe=True) is None
    assert "идёт встреча" in capsys.readouterr().out
    assert doctor.issues == 0


def test_probe_exception_is_named_without_raw_error_text(files, monkeypatch, capsys):
    import nli
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: False)
    def broken(): raise RuntimeError("private fixture content")
    monkeypatch.setattr(nli, "judge", broken)
    assert doctor.check_nli(probe=True) is False
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "private fixture content" not in out
    assert doctor.issues == 1


def test_unavailable_explicit_probe_exits_nonzero(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--probe-nli"])
    with pytest.raises(SystemExit) as done:
        doctor.main()
    assert done.value.code == 1


def test_probe_and_restart_are_mutually_exclusive(root, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--probe-nli", "--restart-llm"])
    monkeypatch.setattr(doctor, "restart_llm", lambda: pytest.fail("must not restart"))
    with pytest.raises(SystemExit) as done:
        doctor.main()
    assert done.value.code == 2
