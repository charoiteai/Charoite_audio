"""Calibration of the test oracle against the unchanged legacy exact mutant.

This control tests fixture sensitivity and owned-child cleanup. It is separate
from the canonical checker suite: the actual runtime regression lives there.
"""
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mutate_check as mc  # noqa: E402
from test_dossier_model_contract import B, BODY, _claim  # noqa: E402

KEY = "src/dossier_incremental.py::_check_claims::Add → Sub::b9a810e9dcc6a998#1"


def _exact_mutant(source, key=KEY):
    candidates = mc.scan(ROOT / "src/dossier_incremental.py", set(range(1, len(source.splitlines()) + 1)),
                         source=source, rel="src/dossier_incremental.py").mutations
    matches = [mut for mut in candidates if mut.key == key]
    assert len(matches) == 1, "exact mutation key is missing or ambiguous"
    mutated, reason = mc.applied(matches[0], source)
    assert mutated is not None and not reason, reason
    return mutated


CHILD = r'''
import hashlib, importlib.util, json, os, pathlib, sys
from charoite_graph import dossier
from conftest import ClaimProgressText
source, payload = map(pathlib.Path, sys.argv[1:3])
expected, enabled = sys.argv[3], sys.argv[4] == "on"
actual = hashlib.sha256(source.read_bytes()).hexdigest()
assert actual == expected, "source hash binding failed"
spec = importlib.util.spec_from_file_location("n976_checker", source)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
data = json.loads(payload.read_text())
print(json.dumps({"pid": os.getpid(), "source_sha256": actual, "oracle": enabled}), flush=True)
body = ClaimProgressText(data["body"]) if enabled else data["body"]
old = dossier.render("Glasshouse", data["prior"], [], {}, "fixture", "2099-01-01")
try:
    checker._check_claims(dossier.DossierLoad("ok", old, old.encode()), body,
                          data["names"], data["files"], data["claims"], [])
except checker.Refused as exc:
    print(json.dumps({"refused": str(exc)}))
else:
    print(json.dumps({"accepted": True}))
'''


def _probe(tmp_path, source, data, *, oracle, expected_hash=None):
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert expected_hash is None or digest == expected_hash, "source hash binding failed"
    source_path, payload = tmp_path / "checker.py", tmp_path / "payload.json"
    source_path.write_text(source, encoding="utf-8")
    payload.write_text(json.dumps(data), encoding="utf-8")
    data_root = tmp_path / "data"
    data_root.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in {"PYTEST_ADDOPTS", "PYTHONPATH", "CHAROITE_ROOT"}}
    env.update(PYTHONPATH=os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")]),
               CHAROITE_ROOT=str(data_root), PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(source_path), str(payload),
                             digest, "on" if oracle else "off"], cwd=tmp_path, env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=False, bufsize=0, start_new_session=True)
    killed = False
    started = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 15
            ready_bytes = bytearray()
            while not ready_bytes.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                assert remaining > 0 and selector.select(remaining), "owned checker did not announce readiness"
                chunk = proc.stdout.read(1)  # unbuffered: never prefetch result bytes
                if not chunk:
                    break
                ready_bytes.extend(chunk)
                assert len(ready_bytes) <= 4096, "owned checker readiness exceeded bound"
        assert ready_bytes, "owned checker exited before source binding"
        ready = json.loads(ready_bytes.decode("utf-8", errors="strict"))
        assert type(ready) is dict and type(ready.get("pid")) is int and type(ready.get("oracle")) is bool
        assert ready == {"pid": proc.pid, "source_sha256": digest, "oracle": oracle}
        try:
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            killed = True
            proc.kill()
            out, err = proc.communicate(timeout=5)
        out, err = out.decode("utf-8", errors="strict"), err.decode("utf-8", errors="strict")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    assert proc.returncode is not None and proc.stdout.closed and proc.stderr.closed
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)
    return {"returncode": proc.returncode, "killed": killed, "stdout": out, "stderr": err,
            "ready": ready, "elapsed_s": time.monotonic() - started, "reaped": True, "pipes_closed": True}


@pytest.fixture
def baseline_source():
    # Current HEAD is available in an ordinary depth-one checkout. No old
    # release/definition Git object or dirty runtime source is a prerequisite.
    return subprocess.check_output(["git", "show", "HEAD:src/dossier_incremental.py"],
                                   cwd=ROOT, text=True)


def _data(claims=None):
    fact = "A rain cover was fitted."
    return {"prior": BODY, "body": BODY.replace("## Как пришли", (f"- {fact} [[{B}]]\n" * 3) + "## Как пришли"),
            "names": [B], "files": {B: {"text": fact}}, "claims": [_claim()] if claims is None else claims}


@pytest.mark.parametrize("claims", [None, [], [_claim("Missing span")], [_claim(), _claim()],
                                    [_claim(quote="Unquoted fact")]])
def test_oracle_keeps_baseline_acceptance_and_refusals(tmp_path, baseline_source, claims):
    data = _data(claims)
    before = _probe(tmp_path, baseline_source, data, oracle=False)
    after = _probe(tmp_path, baseline_source, data, oracle=True)
    assert not before["killed"] and not after["killed"]
    assert before["returncode"] == after["returncode"] == 0
    assert before["stdout"] == after["stdout"]
    result = json.loads(after["stdout"])
    assert result == {"accepted": True} if claims is None else "refused" in result


def test_exact_mutant_is_assertion_failure_and_disabled_oracle_child_is_reaped(tmp_path, baseline_source):
    mutant = _exact_mutant(baseline_source)
    detected = _probe(tmp_path, mutant, _data(), oracle=True)
    assert detected["returncode"] == 1 and not detected["killed"]
    assert "AssertionError: claim search did not advance" in detected["stderr"]
    blind = _probe(tmp_path, mutant, _data(), oracle=False)
    assert blind["killed"] and blind["returncode"] < 0
    assert blind["ready"]["source_sha256"] == detected["ready"]["source_sha256"]
    assert blind["reaped"] and blind["pipes_closed"]


def test_calibration_rejects_wrong_key_or_source_before_execution(tmp_path, baseline_source):
    before = set(tmp_path.iterdir())
    with pytest.raises(AssertionError, match="key is missing"):
        _exact_mutant(baseline_source, KEY + "-foreign")
    with pytest.raises(AssertionError, match="source hash binding"):
        _probe(tmp_path, baseline_source, _data(), oracle=True, expected_hash="0" * 64)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("announcement", ["", '{"pid": 0, "source_sha256": "foreign", "oracle": true}'])
def test_invalid_child_receipt_still_reaps_owned_process_and_closes_pipes(tmp_path, baseline_source, monkeypatch,
                                                                         announcement):
    owned = []
    popen = subprocess.Popen

    def remember(*args, **kwargs):
        child = popen(*args, **kwargs)
        owned.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", remember)
    monkeypatch.setitem(globals(), "CHILD", "print(" + repr(announcement) + ", flush=True)")
    with pytest.raises((AssertionError, json.JSONDecodeError)):
        _probe(tmp_path, baseline_source, _data(), oracle=True)
    assert len(owned) == 1
    child = owned[0]
    assert child.returncode is not None and child.stdout.closed and child.stderr.closed
    with pytest.raises(ProcessLookupError):
        os.kill(child.pid, 0)


def test_coalesced_readiness_and_result_preserve_complete_owned_child_output(tmp_path, monkeypatch):
    """One pipe write must not strand result bytes in a separate text buffer."""
    coalesced = r'''
import hashlib, json, os, pathlib, sys
actual = hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()
assert actual == sys.argv[3]
ready = {"pid": os.getpid(), "source_sha256": actual, "oracle": sys.argv[4] == "on"}
result = {"refused": "synthetic refusal", "detail": "finite café"}
os.write(1, (json.dumps(ready) + "\n" + json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
'''
    monkeypatch.setitem(globals(), "CHILD", coalesced)
    result = _probe(tmp_path, "pass\n", {}, oracle=True)
    assert result["returncode"] == 0 and not result["killed"]
    assert json.loads(result["stdout"]) == {"refused": "synthetic refusal", "detail": "finite café"}
    assert result["stderr"] == "" and result["ready"]["oracle"] is True
    assert result["reaped"] and result["pipes_closed"]
