"""Finite S01-S19 synthetic policy controls. No real mutants or model calls."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release970_policy", ROOT / "scripts/release970_gate.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
P, SHA = g.load_plan()
CTX = {"run_id": 700, "attempt": 1, "definition_sha": "d" * 40, "repo": g.REPO,
       "branch": "verify/release970-synthetic", "workflow": g.WORKFLOW}


def encode(obj):
    return (json.dumps(obj) + "\n").encode()


def rows(k=1):
    return [{"manifest": 2, "range": P["range"], "max": 60, "timeout": 120,
             "sample": P["totals"]["sample"], "caps": list(P["caps"])}] + [
        {"key": key, "outcome": "killed", "s": 1.0} for key in g.expected_keys(P, k)]


def journal(rs):
    return b"".join(map(encode, rs))


def facts(k=1):
    n = len(g.expected_keys(P, k))
    return {"v": 2, "K": k, "N": 16, "M": n, "P": 682, "full": 687, "tested": n,
            "skipped": 0, "unread": 0, "nodes": P["totals"]["nodes"], "lines_in": P["totals"]["lines_in"],
            "broken": "", "broken_rc": 0, "aborted": "", "critical_full": 682,
            "critical_sampled": 682, "critical_M": n, "critical_tested": n, "critical_skipped": 0,
            "zones": P["totals"]["zones"], "zones_note": "", "base": P["baseline"],
            "head": P["candidate"], "run": P["canonical_run_key"], "survivors": [], "word": "ok"}


def payload(k):
    env = {"python": "3.12.16", "packages": {n: "1.0" for n in
           ("pytest", "pytest-timeout", "PyYAML", "sounddevice", "numpy", "onnxruntime", "setuptools")},
           "native_portaudio": "19.6.0-1.2build3", "image_os": "ubuntu24", "image_version": "synthetic"}
    env["packages"].update({"pytest": "9.1.1", "pytest-timeout": "2.4.0", "setuptools": "83.0.0"})
    files = {"journal.jsonl": journal(rows(k)), "facts.json": encode(facts(k)),
             "report.txt": b"synthetic evidence only\n", "environment.json": encode(env)}
    files["provenance.json"] = encode({"context": CTX, "candidate": P["candidate"], "tree": P["candidate_tree"],
        "plan_sha256": SHA, "K": k, "N": 16, "mutator_rc": 0, "hashes": P["hashes"],
        "files": {name: g.digest(data) for name, data in files.items()}})
    return files


def archive(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def platform():
    run = {"id": 700, "run_attempt": 1, "head_sha": CTX["definition_sha"], "head_branch": CTX["branch"],
           "path": g.WORKFLOW, "event": "push", "repository": {"full_name": g.REPO, "id": 42},
           "status": "completed", "conclusion": "success"}
    names = ["release970 plan+policy"] + [f"release970 critical {k}/16" for k in range(1, 17)] + ["release970 aggregate"]
    jobs = [{"id": i+1, "run_id": 700, "head_sha": CTX["definition_sha"], "name": n,
             "status": "completed", "conclusion": "success"} for i, n in enumerate(names)]
    plan_files = {"plan.json": (ROOT / ".github/release970_plan.json").read_bytes(),
                  "provenance.json": encode(g.plan_provenance(P, SHA, CTX))}
    blobs = {"plan": archive(plan_files)} | {f"shard-{k}": archive(payload(k)) for k in range(1, 17)}
    artifacts = []
    for i, (name, blob) in enumerate(blobs.items(), 100):
        artifacts.append({"id": i, "name": f"release970-700-1-{name}", "size_in_bytes": len(blob),
            "expired": False, "digest": "sha256:" + g.digest(blob), "workflow_run":
            {"id": 700, "head_sha": CTX["definition_sha"], "head_branch": CTX["branch"],
             "repository_id": 42, "head_repository_id": 42}})
    return run, jobs, artifacts, {a["id"]: blobs[a["name"].removeprefix("release970-700-1-")] for a in artifacts}


def assert_rejected(call, *args):
    with pytest.raises((g.GateError, KeyError, TypeError, ValueError)):
        call(*args)


def test_s01_valid_all_682_and_independent_final_replay(tmp_path, monkeypatch):
    run, jobs, artifacts, blobs = platform()
    monkeypatch.setattr(g, "preflight", lambda *args: None)
    original_run = g.subprocess.run
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return g.subprocess.CompletedProcess(cmd, 0, encode(valid_pr()), b"")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    def api(endpoint, binary=False):
        if binary:
            return blobs[int(endpoint.split("/")[-2])]
        if "/jobs?" in endpoint:
            return {"total_count": len(jobs), "jobs": jobs}
        if "/artifacts?" in endpoint:
            return {"total_count": len(artifacts), "artifacts": artifacts}
        return run
    monkeypatch.setattr(g, "api", api)
    out = tmp_path / "aggregate"
    assert g.aggregate(ROOT, P, SHA, CTX, out) == 0
    files = {n: (out / n).read_bytes() for n in g.FINAL_PAYLOAD}
    blob = archive(files)
    final = copy.deepcopy(artifacts[0])
    final.update(id=999, name="release970-700-1-final", size_in_bytes=len(blob), digest="sha256:"+g.digest(blob))
    artifacts.append(final)
    blobs[999] = blob
    assert g.aggregate(ROOT, P, SHA, CTX, tmp_path / "external", True) == 0
    assert json.loads((tmp_path / "external/strict.json").read_bytes())["external_completed_run_verified"] is True


def test_s02_canonical_green_capped_assert_rejected(tmp_path):
    for k in range(1, 17):
        (tmp_path / f"shard-{k}.txt.json").write_bytes(encode(facts(k)))
    # Actual unchanged canonical merge reads Facts only; no mutants execute.
    canonical = g.engine(ROOT)
    assert canonical.merge_shards(tmp_path) == 0
    r = rows()
    r[1]["capped"] = True
    assert_rejected(g.validate_journal, journal(r), P, 1)


def test_s03_outcomes_flags_reasons():
    for field, value in [("outcome", x) for x in ("TIMEOUT", "survived", "skipped", "unknown", None)] + [
        ("capped", x) for x in (True, 0, 1, "false", None)] + [("why", "timeout"), ("why", False), ("extra", 0)]:
        r = rows(); r[1][field] = value
        assert_rejected(g.validate_journal, journal(r), P, 1)
    r = rows(); r[1].update(capped=False, why="")
    assert len(g.validate_journal(journal(r), P, 1)) == 43


def test_s04_missing_duplicate_extra_ids():
    for change in (lambda r: r.pop(), lambda r: r.append(r[1]), lambda r: r[1].update(key=r[2]["key"]),
                   lambda r: r[1].update(key="substituted-consistent-count")):
        r = rows(); change(r)
        assert_rejected(g.validate_journal, journal(r), P, 1)


def test_s05_modulo_swap_preserves_union():
    a, b = rows(1), rows(2)
    a[1], b[1] = b[1], a[1]
    for k, r in [(1, a), (2, b)]:
        assert_rejected(g.validate_journal, journal(r), P, k)


def test_s06_plan_identity_reorder_and_metadata():
    r = rows(); r[1], r[2] = r[2], r[1]
    assert_rejected(g.validate_journal, journal(r), P, 1)
    for token in ("path", "qualname", "operator"):
        r = rows(); r[1]["key"] += "-wrong-" + token
        assert_rejected(g.validate_journal, journal(r), P, 1)
    assert len(set(r["key"] for r in P["plan"])) == 682


def test_s07_header_identity_schema():
    for field, value in [("manifest", 1), ("range", "old"), ("max", 61), ("timeout", 121),
                         ("sample", "old"), ("caps", [960, 480, 480, 480, 2401]), ("run", "old")]:
        r = rows(); r[0][field] = value
        assert_rejected(g.validate_journal, journal(r), P, 1)
    r = rows(); r[2] = r[0]
    assert_rejected(g.validate_journal, journal(r), P, 1)


def test_s08_strict_bytes_and_truncation():
    good = journal(rows())
    for bad in (good[:-1], b"\n"+good, good+b"\n", b"\xff\n", b"{\n", b"", good[:-20]+b"\n"):
        assert_rejected(g.validate_journal, bad, P, 1)


def test_s09_strict_json_and_numeric_types():
    for raw in (b'{"x":1,"x":1}', b'{"x":NaN}', b'{"x":Infinity}'):
        assert_rejected(g.strict_json, raw)
    for value in (True, "1", -1, float("nan"), float("inf")):
        r = rows(); r[1]["s"] = value
        assert_rejected(g.validate_journal, journal(r), P, 1)
    for field in g.INT_FACTS | {"v"}:
        for value in (True, "1", 1.0):
            f = facts(); f[field] = value
            assert_rejected(g.validate_facts, encode(f), P, 1)
    r = rows(); r[0]["caps"][0] = True
    assert_rejected(g.validate_journal, journal(r), P, 1)


def test_s10_local_counters_disagree():
    for field in ("K", "N", "M", "P", "tested", "critical_tested", "critical_M"):
        f = facts(); f[field] += 1
        assert_rejected(g.validate_facts, encode(f), P, 1)


def test_s11_unjudged_broken_incomplete_and_exit():
    for field, value in [("unread", 1), ("skipped", 1), ("critical_skipped", 1), ("broken", "bad"),
                         ("aborted", "budget"), ("survivors", [{"critical": True}]), ("word", "partial")]:
        f = facts(); f[field] = value
        assert_rejected(g.validate_facts, encode(f), P, 1)
    for field, value in [("mutator_rc", 1), ("mutator_rc", False), ("N", 15), ("K", 2)]:
        files = payload(1); prov = json.loads(files["provenance.json"]); prov[field] = value
        files["provenance.json"] = encode(prov)
        assert_rejected(g.validate_payload, files, P, SHA, CTX, 1)


def test_s12_source_definition_hashes():
    for field, value in [("candidate", "f"*40), ("tree", "f"*40), ("plan_sha256", "f"*64), ("hashes", {})]:
        files = payload(1); prov = json.loads(files["provenance.json"]); prov[field] = value
        files["provenance.json"] = encode(prov)
        assert_rejected(g.validate_payload, files, P, SHA, CTX, 1)
    files = payload(1); files["report.txt"] += b"changed"
    assert_rejected(g.validate_payload, files, P, SHA, CTX, 1)


def test_s13_api_run_jobs_identity_and_conclusions():
    run, jobs, arts, _ = platform()
    for field, value in [("id", True), ("run_attempt", 2), ("head_sha", "f"*40), ("head_branch", "wrong"),
                         ("path", "wrong"), ("event", "workflow_dispatch"), ("repository", {"full_name": "other/repo", "id": 42})]:
        r = copy.deepcopy(run); r[field] = value
        assert_rejected(g.validate_platform, r, jobs, arts, CTX)
    for conclusion in ("failure", "cancelled", "skipped", "neutral", "timed_out", None):
        j = copy.deepcopy(jobs); j[1]["conclusion"] = conclusion
        assert_rejected(g.validate_platform, run, j, arts, CTX)
    for j in (jobs[:-1], jobs+[jobs[1]]):
        assert_rejected(g.validate_platform, run, j, arts, CTX)


def test_s14_artifact_metadata_and_download(monkeypatch):
    run, jobs, arts, blobs = platform()
    for field, value in [("id", True), ("name", "wrong"), ("size_in_bytes", 0), ("expired", True), ("digest", "bad")]:
        a = copy.deepcopy(arts); a[1][field] = value
        assert_rejected(g.validate_platform, run, jobs, a, CTX)
    for field, value in [("id", True), ("id", 699), ("head_sha", "f"*40), ("repository_id", 43), ("head_branch", "old")]:
        a = copy.deepcopy(arts); a[1]["workflow_run"][field] = value
        assert_rejected(g.validate_platform, run, jobs, a, CTX)
    monkeypatch.setattr(g, "api", lambda *a, **kw: blobs[100]+b"tampered")
    assert_rejected(g.download, arts[0], g.PLAN_PAYLOAD)


def test_s15_attempt_mix_and_typed_context():
    run, jobs, arts, _ = platform()
    arts[1]["name"] = arts[1]["name"].replace("700-1-", "700-2-")
    assert_rejected(g.validate_platform, run, jobs, arts, CTX)
    for field, value in [("run_id", True), ("attempt", True), ("attempt", 1.0), ("attempt", "1")]:
        c = dict(CTX); c[field] = value
        assert_rejected(g.validate_context, c)
    files = payload(1); prov = json.loads(files["provenance.json"]); prov["context"]["attempt"] = True
    files["provenance.json"] = encode(prov)
    assert_rejected(g.validate_payload, files, P, SHA, CTX, 1)


def test_s16_archive_allowlist_and_regular_files():
    good = payload(1)
    assert g.unzip(archive(good), g.PAYLOAD) == good
    for name in ("../journal.jsonl", "/journal.jsonl", "extra"):
        assert_rejected(g.unzip, archive(good | {name: b"bad"}), g.PAYLOAD)
    for mode, duplicate in [(stat.S_IFLNK, False), (stat.S_IFREG, True)]:
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            info = zipfile.ZipInfo("journal.jsonl"); info.external_attr = (mode | 0o600) << 16
            z.writestr(info, b"bad")
            if duplicate:
                with pytest.warns(UserWarning):
                    z.writestr(info, b"duplicate")
        assert_rejected(g.unzip, b.getvalue(), g.PAYLOAD)


def test_s17_self_pass_is_insufficient():
    run, jobs, arts, _ = platform()
    assert_rejected(g.validate_platform, run, jobs, arts, CTX, True)  # final upload absent
    run["conclusion"] = "failure"
    assert_rejected(g.validate_platform, run, jobs, arts, CTX, True)
    run["conclusion"] = "success"; jobs[-1]["conclusion"] = "failure"
    assert_rejected(g.validate_platform, run, jobs, arts, CTX, True)
    assert not g.same_typed({"canonical_exit": False}, {"canonical_exit": 0})
    assert not g.same_typed({"external": 0}, {"external": False})


def workflow_guard(w):
    assert set(w["jobs"]) == {"plan", "critical", "aggregate"}
    assert w["permissions"] == {"contents": "read", "actions": "read"}
    assert w[True] == {"push": {"branches": ["verify/release970-*"]}}
    assert w["concurrency"]["cancel-in-progress"] is False
    assert w["jobs"]["critical"]["strategy"] == {"fail-fast": False, "matrix": {"K": list(range(1, 17))}}
    assert w["jobs"]["aggregate"]["if"] == "${{ always() }}"
    allowed = {"actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
               "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
               "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"}
    for role, job in w["jobs"].items():
        assert job["runs-on"] == "ubuntu-24.04"
        assert sum(s["timeout-minutes"] for s in job["steps"]) + 1 <= job["timeout-minutes"] <= 100
        steps = {s["name"]: s for s in job["steps"]}
        assert steps["definition checkout"]["with"] == {"ref": "${{ github.sha }}", "path": "policy", "persist-credentials": False}
        assert steps["runtime checkout"]["with"] == {"ref": P["candidate"], "path": "runtime", "fetch-depth": 0, "persist-credentials": False}
        assert steps["Python"]["with"] == {"python-version": "3.12.16"}
        assert all(s["uses"] in allowed for s in job["steps"] if "uses" in s)
        upload = steps["evidence upload"]
        assert upload["if"] == "${{ always() }}" and upload["with"]["if-no-files-found"] == "error"
        assert "overwrite" not in upload["with"]
        assert upload["with"]["name"].startswith("release970-${{ github.run_id }}-${{ github.run_attempt }}-")
        if role == "critical":
            mutation = steps["fresh canonical shard"]
            assert mutation["timeout-minutes"] == 60
            assert mutation["run"] == 'python policy/scripts/release970_gate.py run --source "$PWD/runtime" --out "$PWD/shard-evidence" --K "$K"'
            assert P["budget_s"] + P["caps"][1] <= 60*60


def test_s18_workflow_bounds_pins_and_directed_weakening():
    assert g.canonical_command(Path("runtime"), P, 3, Path("out/report.txt")) == [
        g.sys.executable, "runtime/scripts/mutate_check.py", "--range", P["range"], "--only-critical",
        "--max", "60", "--timeout", "120", "--budget-s", "3000", "--shard", "3/16", "--report", "out/report.txt"]
    w = yaml.safe_load((ROOT / g.WORKFLOW).read_text())
    workflow_guard(w)
    for mutate in [lambda x: x["permissions"].update(contents="write"),
        lambda x: x["jobs"]["critical"]["strategy"]["matrix"]["K"].pop(),
        lambda x: x["jobs"]["critical"].update(**{"timeout-minutes": 92}),
        lambda x: x["jobs"]["critical"]["steps"][1]["with"].update(ref="main"),
        lambda x: x["jobs"]["critical"]["steps"][-1]["with"].update(overwrite=True),
        lambda x: x["jobs"]["critical"]["steps"][-2].update(**{"timeout-minutes": 59}),
        lambda x: x["jobs"]["aggregate"].update(**{"if": "success()"}),
        lambda x: x["jobs"]["plan"]["steps"][0].update(uses="actions/checkout@main")]:
        bad = copy.deepcopy(w); mutate(bad)
        with pytest.raises(AssertionError):
            workflow_guard(bad)
    workflow_spec = importlib.util.spec_from_file_location("workflow_policy_guard", ROOT / "tests/test_workflows.py")
    guard = importlib.util.module_from_spec(workflow_spec)
    workflow_spec.loader.exec_module(guard)
    for role in ("plan", "critical", "aggregate"):
        opts = next(s for s in w["jobs"][role]["steps"] if s["name"] == "evidence upload")["with"]
        guard.assert_attempt_scoped_release_artifact(opts, role)
        for field, value in [("overwrite", True), ("overwrite", 0), ("if-no-files-found", "ignore"),
            ("name", opts["name"].replace("${{ github.run_id }}", "old")),
            ("name", opts["name"].replace("${{ github.run_attempt }}", "old")),
            ("name", "release970-${{ github.run_id }}-${{ github.run_attempt }}-wrong-role")]:
            bad = dict(opts); bad[field] = value
            with pytest.raises(AssertionError):
                guard.assert_attempt_scoped_release_artifact(bad, role)
        if role == "critical":
            bad = dict(opts); bad["name"] = opts["name"].replace("${{ matrix.K }}", "1")
            with pytest.raises(AssertionError):
                guard.assert_attempt_scoped_release_artifact(bad, role)


def valid_pr():
    names = {"pytest (src/)", "pytest macOS (src/)", "lint", "analyze", "docs-updated", "conventional",
             "dependency review", "mutation verdict", "swift test (app)", "build (app-ios)", "translation-size",
             "zizmor (безопасность workflow)", "обезличивание (форматы)", "CodeQL"}
    names |= {f"mutation (changed lines) ({k})" for k in range(1, 5)}
    return {"headRefOid": P["candidate"], "baseRefOid": g.RELEASE_BASE, "state": "OPEN", "isDraft": False,
            "mergeable": "MERGEABLE", "statusCheckRollup": [{"name": n, "conclusion": "SUCCESS", "status": "COMPLETED"} for n in names]
            + [{"name": n, "conclusion": "SKIPPED", "status": "COMPLETED", "workflowName": "CI"} for n in ("mutation (changed lines)", "mutation verdict")]}


def test_s19_normal_candidate_ci_is_independently_required():
    pr = valid_pr()
    g.validate_release_pr(pr, P)
    for field, value in [("headRefOid", "f"*40), ("baseRefOid", "f"*40), ("isDraft", True), ("mergeable", "CONFLICTING")]:
        bad = copy.deepcopy(pr); bad[field] = value
        assert_rejected(g.validate_release_pr, bad, P)
    for conclusion in ("FAILURE", "CANCELLED", "SKIPPED", None):
        bad = copy.deepcopy(pr); bad["statusCheckRollup"][0]["conclusion"] = conclusion
        assert_rejected(g.validate_release_pr, bad, P)
    bad = copy.deepcopy(pr); bad["statusCheckRollup"] = []
    assert_rejected(g.validate_release_pr, bad, P)
