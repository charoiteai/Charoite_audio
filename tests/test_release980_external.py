"""Finite release980 controls. Offline fixtures; no real frozen replay/mutants.

Publication/consumer tests are labelled helper integration. The original-core
test really invokes unchanged main/aggregate and intentionally fails its real
runtime preflight; it is not a successful full-34-job integration.
"""
import copy
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release980_under_test", ROOT / "scripts/release980_external.py")
g = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = g
SPEC.loader.exec_module(g)
OLD = ROOT / "docs/diagnostics/release-980-input/fixtures"
NEW = ROOT / "docs/diagnostics/release-980-implementation/fixtures"


def read(path):
    return json.loads(path.read_bytes())


def snapshot():
    checks = read(OLD / "runtime-check-runs-all.json")["check_runs"]
    # Explicit original ten-suite policy fixture, not a claim about the fresh
    # eleven-suite independent listing (which has its own refusal control).
    suites = [read(p) for p in OLD.glob("check-suite-*.json")]
    runs, jobs, members = {}, {}, {}
    for file in OLD.glob("run-*.json"):
        rid = read(file)["id"]
        run = read(NEW / f"attempt-{rid}-1.json")
        js = read(OLD / f"jobs-{rid}-attempt-1.json")["jobs"]
        runs[(rid, 1)] = run
        jobs.update({j["id"]: j for j in js})
        members[(rid, 1)] = sorted(j["id"] for j in js)
    return {"pr": read(OLD / "pr-906-rollup.json"), "rest_pr": read(OLD / "pr-906-rest.json"), "checks": checks,
            "suites": suites, "independent_ci": read(NEW / "ci-runs-page-1.json")["workflow_runs"],
            "runs": runs, "jobs": jobs, "memberships": members,
            "suite_memberships": {suite["id"]: sorted(c["id"] for c in checks if c["check_suite"]["id"] == suite["id"]) for suite in suites}}


class FixtureAPI(g.GitHub):
    """Collector transport fixture, not an actual GitHub invocation."""
    def __init__(self, s):
        self.s = s
        self.calls = []

    def get(self, endpoint):
        self.calls.append(endpoint)
        tail = endpoint.removeprefix(f"repos/{g.REPO}/")
        if tail == "pulls/906":
            return copy.deepcopy(self.s["rest_pr"])
        if tail.startswith("commits/") and "/check-runs?" in tail:
            rows, key = self.s["checks"], "check_runs"
        elif tail.startswith("commits/") and "/check-suites?" in tail:
            rows, key = self.s["suites"], "check_suites"
        elif tail.startswith("actions/workflows/"):
            rows, key = self.s["independent_ci"], "workflow_runs"
        elif tail.startswith("check-suites/"):
            sid = int(tail.split("/")[1]);rows = [c for c in self.s["checks"] if c["check_suite"]["id"] == sid];key = "check_runs"
        elif tail.startswith("actions/runs/"):
            parts = tail.split("/");rid = int(parts[2])
            if len(parts) == 3:
                return copy.deepcopy(max((r for (i, _), r in self.s["runs"].items() if i == rid), key=lambda r: r["run_attempt"]))
            attempt = int(parts[4])
            if len(parts) == 5:
                return copy.deepcopy(self.s["runs"][(rid, attempt)])
            rows = [self.s["jobs"][jid] for jid in self.s["memberships"][(rid, attempt)]];key = "jobs"
        else:
            raise AssertionError(endpoint)
        page = int(endpoint.rsplit("page=", 1)[1])
        return {"total_count": len(rows), key: copy.deepcopy(rows[(page - 1) * 100:page * 100])}


def binding():
    ctx = read(OLD / "critical-context.json")
    return {"invocation": "a" * 64, "command_sha256": "c" * 64, "context": ctx,
            "verifier_sha": "d" * 40, "verifier_sha256": "e" * 64, "runtime_tree": "f" * 40}


def helper_bundle(bound=None):
    """Synthetic bound bundle for publisher/consumer ONLY, never a core proof."""
    bound = binding() if bound is None else bound
    canonical = b"synthetic canonical helper fixture\n"
    strict = {"strict": "PASS", "context": bound["context"], "candidate": g.RUNTIME, "tree": bound["runtime_tree"],
              "plan_sha256": g.PLAN_HASH, "keys": 682, "uncapped_killed": 682, "canonical_exit": 0,
              "artifacts": [{"K": k, "id": k, "digest": "sha256:" + "1" * 64} for k in range(1, 33)],
              "external_completed_run_verified": True, "canonical_files": {"canonical.txt": g.sha(canonical)},
              "root_approval": {"record_sha256": g.APPROVAL_HASH, "definition_sha": g.DEFINITION, "branch": g.BRANCH,
                                "runtime_candidate": g.RUNTIME, "plan_sha256": g.PLAN_HASH}}
    raw_pr = g.encoded(read(OLD / "pr-906-rollup.json"))
    rm = g.encoded({"v": 1, "responses": [{"name": "0000.json", "source": "ORIGINAL aggregate pr.stdout", "bytes": len(raw_pr),
                                         "sha256": g.sha(raw_pr), "observed_at_utc": "synthetic"}]})
    strict_raw = g.encoded(strict)
    side = {"v": 1, "kind": "supplemental_external", "outcome": "PASS", "binding": bound,
            "strict_sha256": g.sha(strict_raw), "canonical_sha256": g.sha(canonical), "raw_manifest_sha256": g.sha(rm),
            "original_pr_sha256": g.sha(raw_pr), "projection": {"synthetic_helper_only": True}, "projection_sha256": g.sha(g.encoded({"synthetic_helper_only": True}))}
    return g.bundle_bytes({"strict.json": strict_raw, "canonical.txt": canonical, "supplemental-pr-metadata.json": g.encoded(side),
                           "raw-manifest.json": rm, "evidence/0000.json": raw_pr}, bound)


class MetadataControls(unittest.TestCase):
    def test_real_public_two_groups_union_and_original_before_false_gate(self):
        s = snapshot()
        actual = g.validate_snapshot(s)
        self.assertEqual(actual["push_groups"], [[42403270503, 1], [42405431329, 1]])
        self.assertEqual((actual["rollup_count"], actual["rest_count"]), (28, 29))
        self.assertEqual(actual["rest_only"], [128502032210])
        spec = importlib.util.spec_from_file_location("original_before", ROOT / "scripts/release970_gate.py")
        before = importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
        with self.assertRaisesRegex(before.GateError, "expected push skips"):
            before.validate_release_pr(copy.deepcopy(s["pr"]), {"candidate": g.RUNTIME})
        self.assertEqual(g.projection(g.collect(FixtureAPI(s), s["pr"]))["policy"], actual)

    def test_collector_independently_fetches_all_ci_runs_and_attempts(self):
        s = snapshot();api = FixtureAPI(s)
        g.collect(api, s["pr"])
        self.assertTrue(any("actions/workflows/317312253/runs?head_sha=" in c for c in api.calls))
        self.assertTrue(all(any(f"runs/{rid}/attempts/1/jobs?" in c for c in api.calls) for rid in (42403270503, 42405431329, 42405436253)))

    def test_history_missing_earlier_attempt_refuses_even_with_green_rows(self):
        s = snapshot();rid = 42405436253
        run = copy.deepcopy(s["runs"][(rid, 1)]);run["run_attempt"] = 2
        s["runs"][(rid, 2)] = run;s["runs"].pop((rid, 1))
        api = FixtureAPI(s)
        with self.assertRaises(KeyError):g.collect(api, s["pr"])
        self.assertTrue(any(f"runs/{rid}/attempts/1" in c for c in api.calls))

    def test_fresh_independent_registration_is_not_a_check_or_ci_pass(self):
        s = snapshot();s["suites"] = read(NEW / "check-suites-page-1.json")["check_suites"]
        s["suite_memberships"][g.EMPTY_REGISTRATION_SUITE] = []
        self.assertEqual(len(s["suites"]), 11)
        result = g.validate_snapshot(g.collect(FixtureAPI(s), s["pr"]))
        self.assertEqual(result["empty_unexecuted_app_registrations"][0]["kind"], "empty_unexecuted_app_registration")
        self.assertEqual(result["empty_unexecuted_app_registrations"][0]["status"], "queued")
        self.assertIsNone(result["empty_unexecuted_app_registrations"][0]["conclusion"])
        for field, value in (("id", 111), ("latest_check_runs_count", 1), ("latest_check_runs_count", False), ("status", "completed"), ("head_sha", "0" * 40)):
            changed = copy.deepcopy(s);suite = next(x for x in changed["suites"] if x["id"] == g.EMPTY_REGISTRATION_SUITE);suite[field] = value
            with self.subTest(field=field), self.assertRaises((g.Refusal, KeyError)):g.validate_snapshot(changed)
        for app in (15368, 42):
            changed = copy.deepcopy(s);suite = next(x for x in changed["suites"] if x["id"] == g.EMPTY_REGISTRATION_SUITE);suite["app"]["id"] = app
            with self.assertRaises(g.Refusal):g.validate_snapshot(changed)
        changed = copy.deepcopy(s);c = copy.deepcopy(changed["checks"][0]);c.update(id=777, status="queued", conclusion=None, check_suite={"id": g.EMPTY_REGISTRATION_SUITE});changed["checks"].append(c);changed["suite_memberships"][g.EMPTY_REGISTRATION_SUITE] = [777]
        with self.assertRaises(g.Refusal):g.validate_snapshot(changed)

    def test_one_and_three_push_groups_no_literal_skip_count(self):
        s = snapshot();rid = 42403270503
        removed = set(s["memberships"].pop((rid, 1)));s["runs"].pop((rid, 1))
        s["jobs"] = {i: j for i, j in s["jobs"].items() if i not in removed}
        s["checks"] = [c for c in s["checks"] if c["id"] not in removed]
        s["pr"]["statusCheckRollup"] = [c for c in s["pr"]["statusCheckRollup"] if f"/runs/{rid}/" not in c["detailsUrl"]]
        s["suites"] = [c for c in s["suites"] if c["id"] != 114879230001]
        s["suite_memberships"].pop(114879230001)
        s["independent_ci"] = [r for r in s["independent_ci"] if r["id"] != rid]
        self.assertEqual(len(g.validate_snapshot(s)["push_groups"]), 1)
        s = snapshot();old = 42403270503;new = 999999;sid = 999998
        run = copy.deepcopy(s["runs"][(old, 1)]);run.update(id=new, check_suite_id=sid, html_url=f"https://github.com/{g.REPO}/actions/runs/{new}")
        suite = copy.deepcopy(next(x for x in s["suites"] if x["id"] == 114879230001));suite["id"] = sid;s["suites"].append(suite)
        s["runs"][(new, 1)] = run;s["independent_ci"].append(copy.deepcopy(run));s["memberships"][(new, 1)] = []
        s["suite_memberships"][sid] = []
        for jid in s["memberships"][(old, 1)]:
            j = copy.deepcopy(s["jobs"][jid]);cid = jid + 900000000000
            j.update(id=cid, run_id=new, html_url=f"https://github.com/{g.REPO}/actions/runs/{new}/job/{cid}", check_run_url=f"https://api.github.com/repos/{g.REPO}/check-runs/{cid}")
            c = copy.deepcopy(next(x for x in s["checks"] if x["id"] == jid));c.update(id=cid, html_url=j["html_url"], details_url=j["html_url"], check_suite={"id": sid})
            s["checks"].append(c);s["jobs"][cid] = j;s["memberships"][(new, 1)].append(cid)
            s["suite_memberships"][sid].append(cid)
            r = copy.deepcopy(next(x for x in s["pr"]["statusCheckRollup"] if x["detailsUrl"].endswith(f"/job/{jid}")));r["detailsUrl"] = j["html_url"];s["pr"]["statusCheckRollup"].append(r)
        s["suite_memberships"][sid].sort()
        self.assertEqual(len(g.validate_snapshot(s)["push_groups"]), 3)

    def test_authenticated_push_pair_cannot_be_successful(self):
        s = snapshot()
        for c in s["checks"]:
            if c["check_suite"]["id"] == 114879230001 and c["name"] in g.PAIR:
                c["conclusion"] = "success";s["jobs"][c["id"]]["conclusion"] = "success"
                next(row for row in s["pr"]["statusCheckRollup"] if row["detailsUrl"] == c["details_url"])["conclusion"] = "SUCCESS"
        with self.assertRaisesRegex(g.Refusal, "push skip pair/conclusion"):g.validate_snapshot(s)

    def test_mutated_metadata_negatives(self):
        def change(s, case):
            j = next(s["jobs"][i] for i in s["memberships"][(42403270503, 1)] if s["jobs"][i]["name"] in g.PAIR)
            c = next(c for c in s["checks"] if c["id"] == j["id"])
            if case == "failed": c["conclusion"] = "failure"
            elif case == "incomplete": c["status"] = "queued"
            elif case == "rest_only_failed": next(x for x in s["checks"] if x["name"] == "copilot-pull-request-reviewer")["conclusion"] = "failure"
            elif case == "unknown_skip": next(x for x in s["checks"] if x["name"] == "copilot-pull-request-reviewer")["conclusion"] = "skipped"
            elif case == "missing_pair": s["checks"] = [x for x in s["checks"] if x["id"] != j["id"]]
            elif case == "duplicate": s["checks"].append(copy.deepcopy(c))
            elif case == "job_url": j["check_run_url"] += "0"
            elif case == "app": c["app"]["id"] = 3
            elif case == "head": c["head_sha"] = "0" * 40
            elif case == "repo": s["runs"][(42403270503, 1)]["repository"]["full_name"] = "other/repo"
            elif case == "event": s["runs"][(42403270503, 1)]["event"] = "workflow_dispatch"
            elif case == "workflow": s["runs"][(42403270503, 1)]["workflow_id"] = 1
            elif case == "missing_mandatory": s["memberships"][(42405436253, 1)].pop()
            elif case == "foreign_pr": s["runs"][(42405436253, 1)]["pull_requests"][0]["head"]["repo"]["id"] = 1
            elif case == "missing_all_rows":
                ids = set(s["memberships"][(42403270503, 1)]);s["checks"] = [x for x in s["checks"] if x["id"] not in ids]
                s["pr"]["statusCheckRollup"] = [x for x in s["pr"]["statusCheckRollup"] if "/runs/42403270503/" not in x["detailsUrl"]]
            elif case == "missing_attempt": s["independent_ci"][0]["run_attempt"] = 2
            elif case == "base": s["pr"]["baseRefOid"] = "0" * 40
        cases = ("failed", "incomplete", "rest_only_failed", "unknown_skip", "missing_pair", "duplicate", "job_url", "app", "head", "repo", "event", "workflow", "missing_mandatory", "foreign_pr", "missing_all_rows", "missing_attempt", "base")
        for case in cases:
            with self.subTest(case=case):
                s = snapshot();change(s, case)
                with self.assertRaises((g.Refusal, KeyError)):
                    g.validate_snapshot(g.collect(FixtureAPI(s), s["pr"]))

    def test_html_only_and_codeql_urls_not_suffix_inference(self):
        s = snapshot();c = next(c for c in s["checks"] if c["name"] == "lint");c["details_url"] = None
        g.validate_snapshot(g.collect(FixtureAPI(s), s["pr"]))
        self.assertEqual(next(c for c in s["checks"] if c["name"] == "CodeQL")["app"]["slug"], "github-advanced-security")
        c["html_url"] += "0"
        with self.assertRaises(g.Refusal):g.validate_snapshot(s)

    def test_full_projection_detects_policy_fields(self):
        original = snapshot();expected = g.projection(original)
        for field in ("head_branch", "run_attempt", "workflow_id", "path", "event", "html_url"):
            s = snapshot();s["runs"][(42403270503, 1)][field] = "changed"
            with self.subTest(field=field):
                try: actual = g.projection(s)
                except (g.Refusal, KeyError, TypeError): actual = None
                self.assertFalse(g.same(expected, actual))
        self.assertFalse(g.same(1, True))
        with self.assertRaises(g.Refusal):g.parsed(b'{"x":1,"x":1}')

    def test_pagination_complete_and_truncated_controls(self):
        class PageAPI(g.GitHub):
            def __init__(self):self.bad = False
            def get(self, endpoint):
                page = int(endpoint.rsplit("page=", 1)[1]);rows = [{"id": n} for n in range(1, 102)]
                return {"total_count": 101, "jobs": rows[:100] if page == 1 else ([] if self.bad else rows[100:])}
        api = PageAPI();self.assertEqual(len(api.pages("unused", "jobs")), 101);api.bad = True
        with self.assertRaises(g.Refusal):api.pages("unused", "jobs")


class PublicationConsumerControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="release980-controls-")
        self.parent = Path(self.temp.name).resolve();self.parent.chmod(0o700)
        self.parent_fd = g.directory_fd(self.parent)
        self.stage, self.stage_fd = g.stage_directory(self.parent, self.parent_fd)
        self.binding = binding();self.raw = helper_bundle(self.binding)

    def tearDown(self):
        os.close(self.stage_fd);os.close(self.parent_fd);self.temp.cleanup()

    def publish(self):
        return g.link_sealed(self.stage, self.stage_fd, self.parent_fd, "bundle.zip", "delivery.zip", self.raw)

    def observed(self, rc=0):
        return g.ObservedExit(12345, rc, self.binding["invocation"], self.binding["command_sha256"])

    def test_helper_fresh_success_requires_concrete_consumer_record(self):
        self.publish()
        self.assertFalse((self.parent / "accepted.json").exists())  # no consumer == HOLD
        result = g.accept_published(self.stage, self.stage_fd, self.parent_fd, "delivery.zip", "accepted.json", self.binding, self.observed())
        self.assertTrue(result["accepted"])
        self.assertEqual(g.parsed((self.parent / "accepted.json").read_bytes())["bundle_sha256"], g.sha(self.raw))
        self.assertEqual((self.parent / "delivery.zip").stat().st_nlink, 2)

    def test_helper_actual_child_wait_nonzero_crash_and_success(self):
        self.publish()
        for expression in ("raise SystemExit(4)", "import os;os._exit(9)", "pass"):
            process = subprocess.Popen([sys.executable, "-I", "-S", "-c", expression]);rc = process.wait()
            observed = g.ObservedExit(process.pid, rc, self.binding["invocation"], self.binding["command_sha256"])
            if rc:
                with self.assertRaises(g.Refusal):g.accept_published(self.stage, self.stage_fd, self.parent_fd, "delivery.zip", "accepted.json", self.binding, observed)
                self.assertFalse((self.parent / "accepted.json").exists())
            else:
                self.assertTrue(g.accept_published(self.stage, self.stage_fd, self.parent_fd, "delivery.zip", "accepted.json", self.binding, observed)["accepted"])

    def test_existing_stale_symlink_directory_and_hardlink_are_preserved(self):
        for kind in ("regular", "symlink", "dangling", "directory", "hardlink"):
            name = "existing-" + kind
            path = self.parent / name
            if kind == "directory":path.mkdir()
            elif kind == "regular":path.write_bytes(b"stale PASS")
            elif kind == "hardlink":
                (self.stage / "prior").write_bytes(b"prior");os.link(self.stage / "prior", path)
            else:path.symlink_to(self.stage / ("prior" if kind == "symlink" else "missing"))
            before = path.lstat()
            with self.subTest(kind=kind):
                with self.assertRaises(g.Refusal):g.absent(self.parent_fd, name)
                self.assertEqual(path.lstat().st_ino, before.st_ino)

    def test_atomic_eexist_has_no_overwrite_even_without_precheck(self):
        self.publish();before = (self.parent / "delivery.zip").read_bytes()
        with self.assertRaises(FileExistsError):g.link_sealed(self.stage, self.stage_fd, self.parent_fd, "contender.zip", "delivery.zip", b"loser")
        self.assertEqual((self.parent / "delivery.zip").read_bytes(), before)

    def test_postlink_unknown_nonzero_stale_and_corruption_hold(self):
        self.publish()
        for observation in (None, self.observed(1), dataclasses.replace(self.observed(), invocation="b" * 64)):
            with self.assertRaises(g.Refusal):g.accept_published(self.stage, self.stage_fd, self.parent_fd, "delivery.zip", "accepted.json", self.binding, observation)
            self.assertFalse((self.parent / "accepted.json").exists())
        for mutate in (lambda b: b[:-1], lambda b: b.replace(b"synthetic canonical", b"corrupted canonical", 1)):
            with self.assertRaises((g.Refusal, zipfile.BadZipFile)):g.validate_bundle(mutate(self.raw), self.binding)
        with self.assertRaises(g.Refusal):g.validate_bundle(self.raw, {**self.binding, "invocation": "b" * 64})

    def test_consumer_rejects_corrupt_published_bytes(self):
        self.publish();path = self.parent / "delivery.zip";path.chmod(0o600);path.write_bytes(b"not a proof bundle");path.chmod(0o400)
        with self.assertRaises(zipfile.BadZipFile):g.accept_published(self.stage, self.stage_fd, self.parent_fd, "delivery.zip", "accepted.json", self.binding, self.observed())
        self.assertFalse((self.parent / "accepted.json").exists())

    def test_raw_strict_only_and_sidecar_only_never_accepted(self):
        for value in (b'{"strict":"PASS","external_completed_run_verified":true}', b'{"outcome":"PASS"}'):
            with self.assertRaises(zipfile.BadZipFile):g.validate_bundle(value, self.binding)
        self.assertFalse((self.parent / "accepted.json").exists())

    def test_failures_preserve_private_evidence_no_destination(self):
        (self.stage / "raw-strict.json").write_bytes(b'{"strict":"PASS"}')
        for fail in ("write", "fsync", "link"):
            with self.subTest(fail=fail):
                target = "failed-" + fail
                if fail == "write":patcher = mock.patch.object(g, "write_new", side_effect=OSError("partial write"))
                elif fail == "fsync":patcher = mock.patch.object(g.os, "fsync", side_effect=OSError("sync"))
                else:patcher = mock.patch.object(g.os, "link", side_effect=OSError("EXDEV"))
                with patcher, self.assertRaises((OSError, g.Refusal)):
                    g.link_sealed(self.stage, self.stage_fd, self.parent_fd, target + ".zip", target, self.raw)
                self.assertFalse((self.parent / target).exists())
        self.assertTrue((self.stage / "raw-strict.json").exists())
        self.assertEqual(self.stage.stat().st_mode & 0o777, 0o700)

    def test_parent_fsync_failure_afterlink_does_not_make_acceptance(self):
        original = g.os.fsync
        def sync(fd):
            if fd == self.parent_fd:raise OSError("parent fsync")
            return original(fd)
        with mock.patch.object(g.os, "fsync", side_effect=sync), self.assertRaises(OSError):self.publish()
        self.assertTrue((self.parent / "delivery.zip").exists())
        self.assertFalse((self.parent / "accepted.json").exists())

    def test_wrong_modes_symlinked_stage_and_cleanup_alias_safety(self):
        self.parent.chmod(0o755)
        with self.assertRaises(g.Refusal):g.checked_directory(self.parent_fd)
        self.parent.chmod(0o700)
        (self.stage / "bundle.zip").symlink_to(self.stage / "missing")
        with self.assertRaises(FileExistsError):self.publish()
        (self.stage / "bundle.zip").unlink();self.publish()
        # No product cleanup exists. Removing only our test alias leaves published bytes.
        (self.stage / "bundle.zip").unlink()
        self.assertEqual((self.parent / "delivery.zip").read_bytes(), self.raw)
        self.assertEqual((self.parent / "delivery.zip").stat().st_nlink, 1)


class LaunchCoreControls(unittest.TestCase):
    def test_startup_attack_isolated_no_site_and_sanitized_environment(self):
        with tempfile.TemporaryDirectory(prefix="release980-startup-") as tmp:
            root = Path(tmp);marker = root / "executed"
            (root / "sitecustomize.py").write_text("from pathlib import Path;Path(" + repr(str(marker)) + ").write_text('attack')")
            tools = {k: {"path": "/usr/bin/" + k} for k in ("git", "gh")}
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(root), "PYTHONSTARTUP": str(root / "sitecustomize.py"), "LD_PRELOAD": "bad"}):
                env = g.sanitized_environment(tools)
                self.assertNotIn("PYTHONPATH", env);self.assertNotIn("PYTHONSTARTUP", env);self.assertNotIn("LD_PRELOAD", env)
                self.assertEqual(env.get("HOME"), os.environ.get("HOME"))
                result = subprocess.run(g.isolated_command(sys.executable, "-c", "import sys;assert sys.flags.isolated and sys.flags.no_site"), cwd=root, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0);self.assertFalse(marker.exists())

    def test_tree_runtime_and_executable_changes_refused(self):
        with tempfile.TemporaryDirectory(prefix="release980-runtime-") as tmp:
            root = Path(tmp).resolve();file = root / "sitecustomize.py";file.write_text("# trusted fixture\n")
            old = g.tree_hash(root);file.write_text("raise RuntimeError('attack')\n")
            self.assertNotEqual(old, g.tree_hash(root))
            with self.assertRaises(g.Refusal):g.executable_pin({"path": str(file), "realpath": str(file), "sha256": "0" * 64})

    def test_native_runtime_links_require_exact_pinned_covered_targets(self):
        with tempfile.TemporaryDirectory(prefix="release980-closure-") as tmp:
            root = Path(tmp).resolve();a = root / "stdlib";b = root / "packages";a.mkdir();b.mkdir()
            target = b / "module.py";target.write_text("# approved fixture\n");link = a / "package-link";link.symlink_to(target)
            pin = {"closure": {str(a): g.tree_hash(a), str(b): g.tree_hash(b)}, "search_paths": [str(b)], "links": {str(link): str(target)}, "excluded_links": {}, "files": {}}
            self.assertEqual(g.verified_runtime_closure(pin), [a, b])
            for changed in ({**pin, "links": {}}, {**pin, "links": {str(link): str(a)}}):
                with self.assertRaises(g.Refusal):g.verified_runtime_closure(changed)
            target.write_text("# changed\n")
            with self.assertRaisesRegex(g.Refusal, "closure changed"):g.verified_runtime_closure(pin)
            outside = root / "outside.py";outside.write_text("# not approved\n");link.unlink();link.symlink_to(outside)
            pin["closure"] = {str(a): g.tree_hash(a), str(b): g.tree_hash(b)};pin["links"] = {str(link): str(outside)}
            with self.assertRaisesRegex(g.Refusal, "outside approved closure"):g.verified_runtime_closure(pin)

    def test_runtime_excluded_unused_link_cannot_enter_search_path_or_escape(self):
        with tempfile.TemporaryDirectory(prefix="release980-excluded-") as tmp:
            root = Path(tmp).resolve();stdlib = root / "stdlib";stdlib.mkdir();external = root / "global-packages";external.mkdir()
            native = root / "Python";native.write_bytes(b"pinned native fixture")
            used = stdlib / "native";used.symlink_to(native)
            unused = stdlib / "site-packages";unused.symlink_to(external)
            pin = {"closure": {str(stdlib): g.tree_hash(stdlib)}, "search_paths": [str(stdlib)], "links": {str(used): str(native)},
                   "files": {str(native): g.sha(native.read_bytes())},
                   "excluded_links": {str(unused): {"text": str(external), "target": str(external), "reason": "unused_global_site_packages"}}}
            self.assertEqual(g.verified_runtime_closure(pin), [stdlib])
            for path in (unused, external, external / "module.py"):
                with self.assertRaisesRegex(g.Refusal, "excluded runtime path touched"):g.reject_excluded_search_paths([str(path)], pin)
            bad = copy.deepcopy(pin);bad["excluded_links"][str(unused)]["text"] = "wrong"
            with self.assertRaises(g.Refusal):g.verified_runtime_closure(bad)
            used.unlink();used.symlink_to(used);pin["closure"][str(stdlib)] = g.tree_hash(stdlib)
            with self.assertRaises((RuntimeError, OSError)):g.verified_runtime_closure(pin)

    def test_preimport_frozen_hash_rejection_without_exec(self):
        with tempfile.TemporaryDirectory(prefix="release980-loader-") as tmp:
            root = Path(tmp).resolve();(root / "scripts").mkdir();(root / "scripts/release970_gate.py").write_text("raise RuntimeError('MUST NOT EXEC')")
            (root / ".github").mkdir();shutil.copyfile(ROOT / ".github/release970_plan.json", root / ".github/release970_plan.json")
            with mock.patch.object(g, "clean_at"), self.assertRaisesRegex(g.Refusal, "gate hash"):
                g.load_frozen(root)

    def test_original_main_aggregate_real_runtime_failure_no_guard_stubs(self):
        with tempfile.TemporaryDirectory(prefix="release980-core-") as tmp:
            root = Path(tmp).resolve();(root / "scripts").mkdir();(root / ".github").mkdir()
            shutil.copyfile(ROOT / "scripts/release970_gate.py", root / "scripts/release970_gate.py")
            shutil.copyfile(ROOT / ".github/release970_plan.json", root / ".github/release970_plan.json")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "scripts/release970_gate.py", ".github/release970_plan.json"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Charoite AI", "-c", "user.email=charoiteai@gmail.com", "commit", "-qm", "fixture"], check=True)
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            # Actual fixture HEAD; no historical Git object lookup in depth1 CI.
            context = {**read(OLD / "critical-context.json"), "definition_sha": head, "branch": "verify/release970-synthetic"}
            ctx = root / "ctx.json";ctx.write_bytes(g.encoded(context))
            approval = root / "approval.json";approval.write_bytes(g.encoded({"definition_sha": head, "branch": context["branch"], "runtime_candidate": g.RUNTIME, "plan_sha256": g.PLAN_HASH}))
            spec = importlib.util.spec_from_file_location("real_frozen_fixture", root / "scripts/release970_gate.py")
            original = importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
            main_code, aggregate_code, preflight_code = original.main.__code__, original.aggregate.__code__, original.preflight.__code__
            args = ["external", "--source", str(root), "--out", str(root / "out"), "--context", str(ctx), "--expected-definition-sha", head,
                    "--expected-branch", context["branch"], "--approval-record", str(approval), "--approval-record-sha256", g.sha(approval.read_bytes())]
            try:
                g.invoke_original(original, args, g.Evidence(root / "evidence"))
            except original.GateError as exc:
                self.assertEqual(str(exc), "runtime checkout SHA")
                codes = [];tb = exc.__traceback__
                while tb:codes.append(tb.tb_frame.f_code);tb = tb.tb_next
                self.assertTrue(all(c in codes for c in (main_code, aggregate_code, preflight_code)))
            else:self.fail("genuine original runtime guard did not reject")
            self.assertFalse((root / "out").exists())

    def test_fake_success_noinvoke_and_wrong_modes_refused(self):
        spec = importlib.util.spec_from_file_location("fake_main_boundary", ROOT / "scripts/release970_gate.py")
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.main = lambda: 0  # explicit negative helper stub, not integration
        with tempfile.TemporaryDirectory() as tmp:
            for index, argv in enumerate((["external"], ["aggregate"], ["run", "--K", "1"])):
                with self.subTest(argv=argv), self.assertRaises(g.Refusal):
                    g.invoke_original(module, argv, g.Evidence(Path(tmp) / str(index)))

    def test_capture_callback_closure_controls_helper_only(self):
        # Test capture logic with an explicit frame-shaped stub; never full core.
        spec = importlib.util.spec_from_file_location("frame_helper_boundary", ROOT / "scripts/release970_gate.py")
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        raw = (OLD / "pr-906-rollup.json").read_bytes();pr = g.parsed(raw)
        plan, _ = module.load_plan();code = module.aggregate.__code__
        frame = types.SimpleNamespace(f_code=code, f_globals=vars(module), f_lineno=522,
                                      f_locals={"pr": subprocess.CompletedProcess([], 0, raw, b"")})
        def fake_main():module.validate_release_pr(copy.deepcopy(pr), plan);return 1
        module.main = fake_main
        for variant in ("good_capture", "wrong_line", "wrong_globals", "nonbytes", "typed_mismatch"):
            with tempfile.TemporaryDirectory() as tmp:
                frame.f_lineno = 521 if variant == "wrong_line" else 522
                frame.f_globals = {} if variant == "wrong_globals" else vars(module)
                frame.f_locals["pr"] = subprocess.CompletedProcess([], 0, raw.decode() if variant == "nonbytes" else raw, b"")
                value = copy.deepcopy(pr)
                if variant == "typed_mismatch":value["isDraft"] = 0
                def main():module.validate_release_pr(value, plan);return 1
                module.main = main
                with mock.patch.object(g.sys, "_getframe", return_value=frame), mock.patch.object(g, "collect", return_value=snapshot()), mock.patch.object(g, "fresh_rollup", return_value=pr):
                    with self.assertRaises(g.Refusal):g.invoke_original(module, ["external"], g.Evidence(Path(tmp) / "evidence"))
                rows = list((Path(tmp) / "evidence").glob("*.json"))
                self.assertEqual(len(rows), 1 if variant == "good_capture" else 0)

    def test_cli_refused_modes_unknown_and_duplicate(self):
        for argv in (["external"], ["supervise", "--out", "unsafe"], ["_verify", "--launch", "a", "--launch", "b"]):
            with self.subTest(argv=argv), self.assertRaises((g.Refusal, SystemExit)):
                g.parser().parse_args(argv)


class TargetedMutationControls(unittest.TestCase):
    def test_five_changed_guard_mutants_are_killed(self):
        source = (ROOT / "scripts/release980_external.py").read_text()
        cases = [
            ("exit0", "and observed.returncode == 0", "and observed.returncode != 0", PublicationConsumerControls, "test_helper_fresh_success_requires_concrete_consumer_record"),
            ("consumer-validation", "        validate_bundle(raw, binding)\n        need((st.st_dev", "        pass  # MUTANT: consumer skipped validation\n        need((st.st_dev", PublicationConsumerControls, "test_consumer_rejects_corrupt_published_bytes"),
            ("push-pair", "need(all(j[\"conclusion\"] == (\"skipped\" if j[\"name\"] in PAIR else \"success\") for j in js),", "need(True,", MetadataControls, "test_authenticated_push_pair_cannot_be_successful"),
            ("gate-pin", "need(sha(raw) == GATE_HASH,", "need(sha(raw) != GATE_HASH,", LaunchCoreControls, "test_preimport_frozen_hash_rejection_without_exec"),
            ("startup-isolation", 'return [str(executable), "-I", "-S", "-B", *map(str, args)]', 'return [str(executable), "-S", "-B", *map(str, args)]', LaunchCoreControls, "test_startup_attack_isolated_no_site_and_sanitized_environment"),
        ]
        killed = []
        for label, old, new, cls, method in cases:
            with self.subTest(mutant=label):
                self.assertEqual(source.count(old), 1)
                name = "release980_mutant_" + label.replace("-", "_")
                mutant = types.ModuleType(name);mutant.__file__ = str(ROOT / "scripts/release980_external.py");sys.modules[name] = mutant
                exec(compile(source.replace(old, new, 1), mutant.__file__, "exec"), mutant.__dict__)
                result = unittest.TestResult()
                with mock.patch.object(sys.modules[__name__], "g", mutant):cls(method).run(result)
                self.assertFalse(result.wasSuccessful(), "surviving targeted guard mutant: " + label)
                killed.append(label)
                sys.modules.pop(name)
        self.assertEqual(len(killed), 5)


if __name__ == "__main__":
    unittest.main()
