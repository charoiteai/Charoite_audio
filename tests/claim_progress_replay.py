"""Replay the finite baseline/whole-suite exact-mutant proof in owned copies.

Run under the project's fresh quiet/heavy gates. Source tree is never mutated.
"""
import dataclasses
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mutate_check as mc  # noqa: E402

BASE = "50b0704fe280360b1d7f828fd601c6216959e55d"
KEY = "src/dossier_incremental.py::_check_claims::Add → Sub::b9a810e9dcc6a998#1"
CHANGED_TESTS = ("tests/conftest.py", "tests/test_dossier_section_claim_coverage.py",
                 "tests/test_claim_progress_oracle.py")


def main():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT), "commit changes before replay"
    assert subprocess.check_output(["git", "rev-parse", BASE + "^{commit}"], cwd=ROOT, text=True).strip() == BASE
    subprocess.run(["git", "merge-base", "--is-ancestor", BASE, head], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--exit-code", BASE, head, "--", "src", "scripts", "app"],
                   cwd=ROOT, check=True)
    source = subprocess.check_output(["git", "show", "HEAD:src/dossier_incremental.py"], cwd=ROOT, text=True)
    assert source == subprocess.check_output(["git", "show", BASE + ":src/dossier_incremental.py"], cwd=ROOT, text=True)
    approved = json.loads((ROOT / "docs/diagnostics/release-976/SOURCE-APPLICABILITY.json").read_text())
    test_hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in CHANGED_TESTS}
    for rel, digest in test_hashes.items():
        expected = approved["selected_tests"].get(rel, approved["selected_dependency_overrides"].get(rel))
        assert digest == expected, "approved core test hash changed: " + rel
    dependency_hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                         for p in approved["selected_dependency_overrides"]}
    assert dependency_hashes == approved["selected_dependency_overrides"], "approved dependencies changed"
    selected = mc.tests_for(ROOT, ROOT / "src/dossier_incremental.py")
    assert len(selected) == 17 and "tests/test_claim_progress_oracle.py" in selected, selected
    matches = [m for m in mc.scan(ROOT / "src/dossier_incremental.py",
                set(range(1, len(source.splitlines()) + 1)), source=source,
                rel="src/dossier_incremental.py").mutations if m.key == KEY]
    assert len(matches) == 1
    mutant, why = mc.applied(matches[0], source)
    assert mutant is not None and not why, why
    receipt = {"base_head": BASE, "candidate_head": head, "key": KEY, "selected": selected,
               "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "mutant_sha256": hashlib.sha256(mutant.encode()).hexdigest(),
               "changed_tests": test_hashes, "dependencies": dependency_hashes,
               "selected_hashes": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in selected},
               "canonical_timeout_s": 120, "outer_cap_s": 480, "no_x": True, "phases": {}}
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert not temp_root.is_relative_to(ROOT), "replay output must stay outside Git"
    log_dir = Path(tempfile.mkdtemp(prefix="n976-replay-", dir=temp_root))
    output = log_dir / "receipt.json"
    with tempfile.TemporaryDirectory(prefix="n976-whole-selected-") as tmp:
        work = mc.copy_tree(ROOT, BASE, Path(tmp))
        original_selected = mc.tests_for(work, work / "src/dossier_incremental.py")
        assert len(original_selected) == 16 and set(original_selected) < set(selected)
        receipt["before_selected"] = original_selected
        for phase in ("before_baseline", "after_baseline", "exact_b9"):
            if phase == "after_baseline":
                subprocess.run(["git", "-C", str(work), "checkout", "-q", "--detach", head], check=True)
                assert subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip() == head
                for rel, digest in {**receipt["selected_hashes"], **dependency_hashes}.items():
                    assert hashlib.sha256((work / rel).read_bytes()).hexdigest() == digest, rel
                assert (work / "src/dossier_incremental.py").read_text(encoding="utf-8") == source
            if phase == "exact_b9":
                (work / "src/dossier_incremental.py").write_text(mutant, encoding="utf-8")
            phase_selected = original_selected if phase == "before_baseline" else selected
            assert mc.tests_for(work, work / "src/dossier_incremental.py") == phase_selected
            started = time.monotonic()
            # Passive receipt only: preserve the exact canonical command and
            # subprocess return object; capture its otherwise truncated output.
            captured = []
            real_run = mc.subprocess.run

            def observed_run(*args, **kwargs):
                actual = real_run(*args, **kwargs)
                captured.append((args[0], actual.stdout + actual.stderr))
                return actual

            mc.subprocess.run = observed_run
            try:
                result = mc.run_tests(work, phase_selected, timeout=120, cap=480)
            finally:
                mc.subprocess.run = real_run
            assert len(captured) == 1
            argv, raw = captured[0]
            assert argv == [sys.executable, "-B", "-m", "pytest", *phase_selected,
                            "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "--timeout", "120"]
            raw_bytes = raw.encode()
            (log_dir / (phase + ".log")).write_bytes(raw_bytes)
            receipt["phases"][phase] = {**dataclasses.asdict(result), "elapsed_s": time.monotonic() - started}
            receipt["phases"][phase].update(
                raw_sha256=hashlib.sha256(raw_bytes).hexdigest(), raw_bytes=len(raw_bytes),
                progress_assertions=raw.count("AssertionError: claim search did not advance"),
                pytest_timeout_failures=raw.count("Failed: Timeout"),
                summary=re.findall(r"^.*(?:[0-9]+ passed|[0-9]+ failed).* in [0-9.]+s.*$", raw, re.M)[-1:])
            print(phase, receipt["phases"][phase], flush=True)
            output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            expected = mc.FAILED if phase == "exact_b9" else mc.PASSED
            assert result.status == expected and result.rc == (1 if phase == "exact_b9" else 0), result
            if phase == "exact_b9":
                assert receipt["phases"][phase]["progress_assertions"] > 0
                assert receipt["phases"][phase]["pytest_timeout_failures"] == 0
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT), "replay changed source worktree"
    print("receipt:", output, "private raw logs:", log_dir, flush=True)


if __name__ == "__main__":
    main()
