"""Addressed passive-observation fixtures; no release mutant/model/owner data."""
import copy
import json
import os
from pathlib import Path
import subprocess
import types

import pytest

from test_release970_gate import CTX, P, ROOT, SHA, encode, facts, fake_observation_policy, g, imports_evidence, journal, payload, rows


def test_observation_closed_negative_matrix():
    good = imports_evidence(1)
    hashes, variants = fake_observation_policy(1)
    assert g.validate_imports(encode(good), P, CTX, 1, hashes, variants)["v"] == 1
    changes = [lambda x: x["records"].pop(), lambda x: x.update(canonical_pid=True),
        lambda x: x.update(canonical_pid=x["policy_pid"]),
        lambda x: x["records"][1].update(parent=999),
        lambda x: x["records"][-1].update(errors=["observer error"]),
        lambda x: x["records"][-1].update(head="f"*40),
        lambda x: x["records"][-1].update(key="wrong-key"),
        lambda x: x["records"][-1]["origins"][0].update(path="/installed/site-packages/llm.py"),
        lambda x: x["records"][-1]["origins"][0].update(sha256="0"*64),
        lambda x: x["records"][-1].update(origins=[]),
        lambda x: x["records"][-1].update(elapsed_ns=3601*10**9),
        lambda x: x["records"][-1].update(extra=True)]
    for change in changes:
        bad = copy.deepcopy(good); change(bad)
        with pytest.raises(g.GateError):
            g.validate_imports(encode(bad), P, CTX, 1, hashes, variants)
    bad = copy.deepcopy(good)
    child = bad["records"][-1]; path = variants[child["key"]]["path"]
    child["snapshot"][path] = hashes[path]  # unmutated source despite a valid key
    with pytest.raises(g.GateError):
        g.validate_imports(encode(bad), P, CTX, 1, hashes, variants)
    with pytest.raises(g.GateError, match="payload bytes"):
        g.validate_imports(b" "*4_000_001, P, CTX, 1, hashes, variants)


def test_observer_refuses_startup_shadow_without_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "preflight", lambda *a: None)
    monkeypatch.setattr(g, "observation_policy", lambda *a: fake_observation_policy(1))
    monkeypatch.setattr(g.subprocess, "Popen", lambda *a, **k: pytest.fail("refused observer must never launch"))
    with monkeypatch.context() as scope:
        scope.setenv("PYTHONPATH", "another-bootstrap")
        with pytest.raises(g.GateError, match="empty inherited PYTHONPATH"):
            g.collect(tmp_path, P, SHA, CTX, tmp_path / "out1", 1)
    with monkeypatch.context() as scope:
        scope.delenv("PYTHONPATH", raising=False)
        scope.setattr(g.importlib.util, "find_spec", lambda *a: object())
        with pytest.raises(g.GateError, match="existing startup customizer"):
            g.collect(tmp_path, P, SHA, CTX, tmp_path / "out2", 1)


def test_cli_resolves_relative_source_and_output_before_subprocess_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(g, "preflight", lambda source, p: seen.append(source))
    monkeypatch.setattr(g, "context", lambda: CTX)
    monkeypatch.setattr(g.sys, "argv", ["gate", "preflight", "--source", "runtime", "--out", "output"])
    assert g.main() == 0
    assert seen == [(tmp_path / "runtime").resolve()]
    assert (tmp_path / "output/plan.json").is_file()


def test_collection_emits_exact_archive_payload_without_duplicate_facts(tmp_path, monkeypatch):
    source, out = tmp_path / "runtime", tmp_path / "out"
    source.mkdir()
    monkeypatch.setattr(g, "preflight", lambda *a: None)
    monkeypatch.setattr(g, "observation_policy", lambda *a: fake_observation_policy(1))
    monkeypatch.setattr(g, "environment", lambda: json.loads(payload(1)["environment.json"]))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(g.importlib.util, "find_spec", lambda *a: None)
    class FakeCanonical:
        pid = 10
        def __init__(self, cmd, cwd, env):
            self.cmd, self.source, self.config = cmd, cwd, json.loads(Path(env["CHAROITE970_OBSERVER_CONFIG"]).read_text())
        def wait(self):
            report = Path(self.cmd[-1])
            report.write_text("synthetic canonical report\n")
            report.with_name(report.name+".json").write_bytes(encode(facts(1)))
            (self.source / "logs").mkdir()
            (self.source / "logs" / f"mutation_run-{P['canonical_run_key']}-1.jsonl").write_bytes(journal(rows(1)))
            observed = imports_evidence(1)
            observed["records"][0]["parent"] = os.getpid()
            for r in observed["records"]:
                (Path(self.config["directory"]) / f"process-{r['pid']}.json").write_bytes(encode(r))
            return 0
    monkeypatch.setattr(g.subprocess, "Popen", FakeCanonical)
    assert g.collect(source, P, SHA, CTX, out, 1) == 0
    assert {p.name for p in out.iterdir()} == g.PAYLOAD
    assert not (out / "report.txt.json").exists()
    assert len(g.validate_payload({p.name:p.read_bytes() for p in out.iterdir()}, P, SHA, CTX, 1, *fake_observation_policy(1))) == 43


def test_before_old_receipt_has_no_actual_import_proof_after_rejects():
    old = types.ModuleType("old_release970_validator")
    old.__file__ = str(ROOT / "scripts/release970_gate.py")
    raw = subprocess.check_output(["git", "-C", str(ROOT), "show", "79ea7fed419027c65f90f87d4c1d0d331a949544:scripts/release970_gate.py"])
    exec(compile(raw, "<old-public-validator>", "exec"), old.__dict__)
    files = payload(1); files.pop("imports.json")
    prov = json.loads(files["provenance.json"])
    prov["files"] = {n: g.digest(b) for n, b in files.items() if n != "provenance.json"}
    files["provenance.json"] = encode(prov)
    assert len(old.validate_payload(files, P, SHA, CTX, 1)) == 43
    files = payload(1); observed = json.loads(files["imports.json"])
    observed["records"][-1]["errors"] = ["foreign same-name source"]
    files["imports.json"] = encode(observed)
    prov = json.loads(files["provenance.json"]); prov["files"]["imports.json"] = g.digest(files["imports.json"])
    files["provenance.json"] = encode(prov)
    with pytest.raises(g.GateError, match="observer error/foreign source"):
        g.validate_payload(files, P, SHA, CTX, 1, *fake_observation_policy(1))


def run_native_probe(tmp_path, foreign=False, callback_error=False):
    """A fake canonical script runs two actual one-test pytest subprocesses."""
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "--detach", P["candidate"]], check=True)
    (work / "mini").mkdir()
    original, mutated = b"V = 1\n", b"V = 2\n"
    (work / "src/n970_probe.py").write_bytes(original)
    other = tmp_path / "installed/site-packages"
    other.mkdir(parents=True); (other / "n970_probe.py").write_bytes(original)
    test = 'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parents[1]/"src"))\n'
    if foreign:
        test += f'sys.path.insert(0, {str(other)!r})\n'
    test += 'import n970_probe\ndef test_value():\n    assert n970_probe.V == 1\n'
    (work / "mini/test_probe.py").write_text(test)
    script = '''import json, os, pathlib, subprocess, sys
work = pathlib.Path.cwd()
args = [sys.executable, "-B", "-m", "pytest", "mini/test_probe.py", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "--timeout", "120"]
def run():
    return subprocess.run(args, cwd=work, env={**os.environ, "PYTHONDONTWRITEBYTECODE":"1"}, capture_output=True).returncode
rc = [run()]
(work/"src/n970_probe.py").write_text("V = 2\\n")
rc.append(run())
(work/"src/n970_probe.py").write_text("V = 1\\n")
(work/"probe-rc.json").write_text(json.dumps(rc))
'''
    if callback_error:
        script += '''import hashlib
saved = hashlib.sha256
hashlib.sha256 = lambda data: (_ for _ in ()).throw(RuntimeError("synthetic observer failure"))
for _ in range(32):
    exec(compile("RESULT=1", str(work/"src/n970_probe.py"), "exec"))
hashlib.sha256 = saved
'''
    script += 'print("canonical-end")\n'
    (work / "scripts/mutate_check.py").write_text(script)
    bootstrap, receipts = tmp_path / "bootstrap", tmp_path / "receipts"
    bootstrap.mkdir(); receipts.mkdir()
    observer = (ROOT / "scripts/release970_observer.py").read_bytes()
    (bootstrap / "sitecustomize.py").write_bytes(observer)
    cmd = g.canonical_command(work, P, 1, tmp_path / "report.txt")
    hashes = {p: g.digest((work / p).read_bytes()) for p in ("scripts/mutate_check.py", "src/n970_probe.py")}
    key = "synthetic::V1_to_V2"
    variants = {key: {"path": "src/n970_probe.py", "sha256": g.digest(mutated)}}
    config = {"directory": str(receipts), "runtime_root": str(work), "policy_pid": os.getpid(), "command": cmd,
              "hashes": hashes, "variants": variants, "context": CTX, "K": 1, "observer_sha256": g.digest(observer)}
    config_path = tmp_path / "config.json"; config_path.write_text(json.dumps(config))
    before = os.environ.get("PYTHONPATH")
    proc = subprocess.Popen(cmd, cwd=work, env={**os.environ, "PYTHONPATH": str(bootstrap),
        "CHAROITE970_OBSERVER_CONFIG": str(config_path), "PYTHONDONTWRITEBYTECODE":"1"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 0 and b"canonical-end" in stdout, stderr.decode()
    assert os.environ.get("PYTHONPATH") == before
    result = {"v": 1, "canonical_pid": proc.pid, "policy_pid": os.getpid(), "observer_sha256": g.digest(observer),
              "records": [json.loads(p.read_text()) for p in receipts.glob("process-*.json")]}
    mini = copy.deepcopy(P)
    mini["plan"] = [{"key": key, "K": 1, "path": "src/n970_probe.py"}]
    mini["suites_by_path"] = {"src/n970_probe.py": ["mini/test_probe.py"]}
    return result, mini, hashes, variants, json.loads((work / "probe-rc.json").read_text())


def test_native_passive_observer_records_actual_sources(tmp_path, monkeypatch):
    evidence, plan, hashes, variants, child_rc = run_native_probe(tmp_path)
    monkeypatch.setattr(g, "CANONICAL_PATHS", ("scripts/mutate_check.py",))
    assert child_rc == [0, 1]  # Observer did not reinterpret baseline/failure.
    assert len(g.validate_imports(encode(evidence), plan, CTX, 1, hashes, variants)["records"]) == 3
    child = next(r for r in evidence["records"] if r["key"] is not None)
    assert any(
        o["relative"] == "src/n970_probe.py" and o["sha256"] == variants[child["key"]]["sha256"] for o in child["origins"])


def test_native_foreign_unmutated_import_is_rejected(tmp_path, monkeypatch):
    evidence, plan, hashes, variants, child_rc = run_native_probe(tmp_path, foreign=True)
    monkeypatch.setattr(g, "CANONICAL_PATHS", ("scripts/mutate_check.py",))
    assert child_rc == [0, 0]  # Wrong installed code stays visible, never hidden by observer.
    assert any("foreign same-name source" in r["errors"] for r in evidence["records"])
    with pytest.raises(g.GateError, match="observer error/foreign source"):
        g.validate_imports(encode(evidence), plan, CTX, 1, hashes, variants)


def test_native_callback_error_preserves_rc_but_refuses_evidence(tmp_path, monkeypatch):
    evidence, plan, hashes, variants, child_rc = run_native_probe(tmp_path, callback_error=True)
    monkeypatch.setattr(g, "CANONICAL_PATHS", ("scripts/mutate_check.py",))
    assert child_rc == [0, 1]
    assert any("RuntimeError" in r["errors"] for r in evidence["records"])
    assert max(len(r["errors"]) for r in evidence["records"]) == 16
    with pytest.raises(g.GateError, match="observer error/foreign source"):
        g.validate_imports(encode(evidence), plan, CTX, 1, hashes, variants)
