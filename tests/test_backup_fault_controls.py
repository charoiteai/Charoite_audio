"""Artifact paths remain valid when the diagnostic runner changes cwd."""
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile

import pytest


@pytest.mark.parametrize("absolute_output", [False, True])
def test_fault_runner_output_survives_child_cwd(tmp_path, monkeypatch, absolute_output):
    runner = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / "reports/handoff/tasks/875_backup_collision/fault_controls.py"))
    output = tmp_path / "fault results" if absolute_output else Path("fault results")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["fault_controls.py", "--checkout", ".",
                                     "--output", str(output), "--python", sys.executable,
                                     "--guard", "synthetic-guard"])
    monkeypatch.setattr(subprocess, "check_output",
                        lambda command, **kwargs: "" if "status" in command else "f" * 40)
    mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kwargs: mkdtemp(dir=tmp_path, **kwargs))

    def synthetic_process(command, **kwargs):
        if command[:2] == ["git", "archive"]:
            files = {
                "src/charoite_graph/dossier.py": b"# synthetic source\n",
                "reports/handoff/tasks/875_backup_collision/validation.json": json.dumps({
                    "mutant_test_targets": ["synthetic_test"],
                    "manual_mutation_recipes": [],
                }).encode(),
            }
            with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
                for name, data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
        elif command == ["synthetic-guard"]:
            pass  # Only this isolated unit test substitutes the host gate.
        elif command[:4] == [sys.executable, "-B", "-m", "pytest"]:
            xml = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--junitxml=")))
            if not xml.is_absolute():
                xml = Path(kwargs["cwd"]) / xml
            xml.parent.mkdir(parents=True, exist_ok=True)
            xml.write_text('<testsuite>' + '<testcase name="synthetic"/>' * 13 + '</testsuite>')
        else:
            raise AssertionError(f"Unexpected subprocess: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", synthetic_process)
    assert runner["main"]() == 0
    artifacts = tmp_path / "fault results"
    assert (artifacts / "baseline.xml").is_file()
    assert (artifacts / "baseline.log").is_file()
    result = json.loads((artifacts / "result.json").read_text())
    assert result["runs"][0]["valid"] is True
    assert result["runs"][0]["cases"] == 13
