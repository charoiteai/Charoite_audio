"""NLI diagnostics take readiness from nli.refusal() and distinguish it from working inference."""
import json
import shutil
import sys
from pathlib import Path
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import doctor
import charoite_paths
import nli

GOOD_CONFIG = Path(__file__).resolve().parent / "fixtures" / "nli_mdeberta_config.json"


@pytest.fixture
def root(tmp_path, monkeypatch):
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(nli, "_failed", False)
    monkeypatch.setattr(nli, "_why", "")
    monkeypatch.setattr(nli, "_cfg_cache", None)
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
    assert " – NLI: нет файла model.onnx" in out
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
    """Комплект с годными метками; веса — заглушки, модель не грузится."""
    folder = root / "models/nli"
    folder.mkdir(parents=True)
    for name in ("model.onnx", "tokenizer.json"):
        (folder / name).write_text("stub")
    shutil.copy(GOOD_CONFIG, folder / "config.json")
    monkeypatch.setattr(nli, "_packages", lambda: "")
    return folder


def test_valid_set_is_ok_without_loading_model(files, monkeypatch, capsys):
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("must not load NLI"))
    assert doctor.check_nli() is None
    out = capsys.readouterr().out
    assert " ✓ NLI: файлы, метки и пакеты годны" in out and "--probe-nli" in out
    assert "проба прошла" not in out
    assert doctor.issues == 0


def _labels(folder):
    meta = json.loads((folder / "config.json").read_text())
    meta["id2label"] = {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}
    (folder / "config.json").write_text(json.dumps(meta))


def _broken(folder):
    (folder / "config.json").write_text("{broken")


@pytest.mark.parametrize("spoil", [_labels, _broken], ids=["LABEL_0..2", "broken-json"])
def test_unusable_config_reports_nli_refusal(files, monkeypatch, capsys, spoil):
    spoil(files)
    why = nli.refusal()
    assert why
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("must not load NLI"))
    assert doctor.check_nli() is False
    out = capsys.readouterr().out
    assert f" – NLI: {why}" in out and "годны" not in out
    assert doctor.issues == 0


@pytest.mark.parametrize("spoil", [_labels, _broken], ids=["LABEL_0..2", "broken-json"])
def test_probe_fails_with_refusal_before_meeting_check(files, monkeypatch, capsys, spoil):
    import live_gate
    spoil(files)
    why = nli.refusal()
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: pytest.fail("refusal comes first"))
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("must not load NLI"))
    assert doctor.check_nli(probe=True) is False
    assert f" ✗ NLI: {why}" in capsys.readouterr().out
    assert doctor.issues == 1


def test_missing_metadata_names_the_exact_file(files, capsys):
    (files / "config.json").unlink()
    assert doctor.check_nli() is False
    assert "нет config.json" in capsys.readouterr().out
    assert doctor.issues == 0


def test_missing_package_is_not_a_model_success(files, monkeypatch, capsys):
    monkeypatch.setattr(nli, "_packages", lambda: "нет onnxruntime/tokenizers")
    assert doctor.check_nli() is False
    assert " – NLI: нет onnxruntime/tokenizers" in capsys.readouterr().out


def test_absent_nli_folder_is_a_warning(root, capsys):
    assert doctor.check_nli() is False
    assert " – NLI: нет файла model.onnx" in capsys.readouterr().out
    assert doctor.issues == 0


@pytest.mark.parametrize("ready", [True, False])
def test_explicit_probe_reports_actual_judge_readiness(files, monkeypatch, capsys, ready):
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: False)
    monkeypatch.setattr(nli, "judge", lambda: types.SimpleNamespace(refused="", ready=lambda: ready))
    assert doctor.check_nli(probe=True) is ready
    assert (" ✓ NLI" in capsys.readouterr().out) is ready
    assert doctor.issues == int(not ready)


def test_probe_names_judge_refusal_verbatim(files, monkeypatch, capsys):
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: False)
    monkeypatch.setattr(nli, "judge", lambda: types.SimpleNamespace(refused="boom", ready=lambda: True))
    assert doctor.check_nli(probe=True) is False
    out = capsys.readouterr().out
    assert " ✗ NLI: boom" in out and " ✓ NLI" not in out
    assert doctor.issues == 1


def test_live_meeting_defers_probe_before_loading_model(files, monkeypatch, capsys):
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: True)
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("meeting has priority"))
    assert doctor.check_nli(probe=True) is None
    assert "идёт встреча" in capsys.readouterr().out
    assert doctor.issues == 0


def test_deferred_probe_exits_one(files, monkeypatch):
    import live_gate
    monkeypatch.setattr(live_gate, "daemon_alive", lambda root: True)
    monkeypatch.setattr(nli, "judge", lambda: pytest.fail("meeting has priority"))
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--probe-nli"])
    with pytest.raises(SystemExit) as done:
        doctor.main()
    assert done.value.code == 1


def test_probe_exception_is_named_without_raw_error_text(files, monkeypatch, capsys):
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
