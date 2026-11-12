"""Run task875 fault controls on a fresh tracked snapshot under the host gate."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import xml.etree.ElementTree as ET


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--python", type=Path, required=True)
    ap.add_argument("--guard", type=Path, required=True)
    args = ap.parse_args()
    args.output = args.output.resolve()
    checkout = args.checkout.resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=checkout):
        raise RuntimeError("Source checkout must be clean")
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "snapshot.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "archive", head], cwd=checkout, stdout=stream, check=True)
    snapshot = Path(tempfile.mkdtemp(prefix="n875-fault-snapshot-"))
    with tarfile.open(archive) as bundle:
        bundle.extractall(snapshot, filter="data")
    archive.unlink()
    config = json.loads((snapshot / "reports/handoff/tasks/875_backup_collision/validation.json").read_text())
    source = snapshot / "src/charoite_graph/dossier.py"
    original = source.read_text()
    expected_tests = {
        "overwrite": "test_same_stamp_refuses_without_overwriting_preimage",
        "skip-publication": "test_complete_copy_metadata_and_distinct_names",
        "swallow-collision": "test_same_stamp_refuses_without_overwriting_preimage",
        "lose-metadata": "test_complete_copy_metadata_and_distinct_names",
        "leave-staging": "test_complete_copy_metadata_and_distinct_names",
    }
    expected_reasons = {
        "overwrite": ["DID NOT RAISE FileExistsError"],
        "skip-publication": ["FileNotFoundError", "/.backup/2030-01-01_010101/Sample.md"],
        "swallow-collision": ["DID NOT RAISE FileExistsError"],
        "lose-metadata": ["assert copy.stat().st_mtime_ns == expected_stat.st_mtime_ns"],
        "leave-staging": ["assert copy.stat().st_nlink == 1", "assert 2 == 1"],
    }
    result = {"head": head, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "snapshot": str(snapshot), "status": "incomplete", "runs": []}
    result_path = args.output / "result.json"

    def save():
        result_path.write_text(json.dumps(result, indent=2) + "\n")

    def run_case(name, target, recipe=None):
        # Real installed gate, with inherited data-root overrides removed. Busy
        # ends the experiment; a later run gets a new baseline and snapshot.
        gate_env = {k: v for k, v in os.environ.items()
                    if k not in {"CHAROITE_ROOT", "QG_CODE", "QG_RECORDINGS"}}
        guard = subprocess.run([str(args.guard)], env=gate_env, capture_output=True, text=True)
        if guard.returncode:
            result["gate"] = {"returncode": guard.returncode, "message": guard.stderr}
            save()
            return False
        xml_path = args.output / f"{name}.xml"
        log_path = args.output / f"{name}.log"
        temp_root = Path(tempfile.mkdtemp(prefix=f"n875-{name}-"))
        command = [str(args.python), "-B", "-m", "pytest", *target, "-q", "--tb=short", "-x",
                   f"--basetemp={temp_root / 'pytest'}", f"--junitxml={xml_path}"]
        with log_path.open("w") as stream:
            completed = subprocess.run(command, cwd=snapshot, stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=180)
        tree = ET.parse(xml_path)
        cases = list(tree.iter("testcase"))
        failures = [c for c in cases if c.find("failure") is not None]
        errors = [c for c in cases if c.find("error") is not None]
        skipped = [c for c in cases if c.find("skipped") is not None]
        failure_text = ""
        if len(failures) == 1:
            failure = failures[0].find("failure")
            failure_text = failure.get("message", "") + "\n" + (failure.text or "")
        reason_matches = name != "baseline" and all(
            text in failure_text for text in expected_reasons[name])
        valid = (completed.returncode == 0 and len(cases) == 13 and not failures and not errors
                 and not skipped) if name == "baseline" else (
                     completed.returncode == 1 and len(failures) == 1 and not errors
                     and not skipped and reason_matches
                     and failures[0].get("name", "").startswith(expected_tests[name]))
        entry = {"name": name, "returncode": completed.returncode, "cases": len(cases),
                 "failures": [c.get("name") for c in failures], "errors": len(errors),
                 "valid": valid, "reason_matches": reason_matches,
                 "command": command, "log": log_path.name}
        if recipe:
            entry["recipe"] = recipe
            entry["mutant_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        result["runs"].append(entry)
        save()
        print(f"{name}: {'expected result' if valid else 'UNEXPECTED'}", flush=True)
        return valid

    save()
    try:
        if not run_case("baseline", config["mutant_test_targets"]):
            return 1
        for recipe in config["manual_mutation_recipes"]:
            if original.count(recipe["from"]) != 1:
                raise RuntimeError(f"Ambiguous mutation: {recipe['name']}")
            source.write_text(original.replace(recipe["from"], recipe["to"], 1))
            target = "tests/test_dossier_backup_collision.py::" + expected_tests[recipe["name"]]
            if not run_case(recipe["name"], [target], recipe):
                return 1
            source.write_text(original)
        result["status"] = "five_faults_detected"
        save()
        return 0
    finally:
        source.write_text(original)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == result["source_sha256"]


if __name__ == "__main__":
    raise SystemExit(main())
