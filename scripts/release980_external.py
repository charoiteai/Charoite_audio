#!/usr/bin/env python3
"""Pinned, supplemental release-970 verification; never a mutation runner.

ROOT must authenticate this file/runtime BEFORE invoking ``supervise``. The
supervisor seals the verified bytes, observes its child, and alone consumes the
published bundle. There is deliberately no CLI that accepts a supplied exit code.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import zipfile

REPO = "charoiteai/Charoite_audio"
DEFINITION = "74ad984b3b9d4be17586d607b27cd3f44924c459"
GATE_HASH = "efbb97708d07e1a0654155516711f7c303154c6a22a1976d8fef196b81bcc4bf"
RUNTIME = "94867ecb54593018b0568a18fa9501770cdfb768"
BASE = "005832892bfb8b9d8cca9d90ed70537d69242d3a"
PLAN_HASH = "091c9596f348066b323d22c6888872625c8c9d92f635dd7f8d382958f3bc3717"
APPROVAL_HASH = "83724a385fa27e7b0eaaf42c509b71cae97f06b5bd14e26dcb689a6471d1c40d"
FIXTURE_HASH = "c8e7c7b300465a4df7a88906dcc0907d07b6982e23d41ed150a4b6f2ff26aba5"
BRANCH = "verify/release970-32-94867ecb-74ad984b"
CI_WORKFLOW = 317312253
EMPTY_REGISTRATION_SUITE = 114879227597
EMPTY_REGISTRATION_APP = 1236702
PAIR = {"mutation (changed lines)", "mutation verdict"}
CI_NORMAL = {"lint", "pytest (src/)", "pytest macOS (src/)"}
CI_PR = CI_NORMAL | {"mutation verdict"} | {f"mutation (changed lines) ({k})" for k in range(1, 5)}
OTHER_WORKFLOWS = {
    ".github/workflows/pr-title.yml": {"conventional"},
    ".github/workflows/codeql.yml": {"analyze"},
    ".github/workflows/docs-guard.yml": {"docs-updated", "translation-size"},
    ".github/workflows/supply-chain.yml": {"dependency review", "zizmor (безопасность workflow)", "обезличивание (форматы)"},
    ".github/workflows/swift-tests.yml": {"swift test (app)", "build (app-ios)"},
    "dynamic/agents/copilot-pull-request-reviewer": {"copilot-pull-request-reviewer"},
}
MANDATORY = CI_PR | set().union(*(v for k, v in OTHER_WORKFLOWS.items() if not k.startswith("dynamic/"))) | {"CodeQL"}
LIMIT = 32 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")


class Refusal(ValueError):
    """A missing proof is HOLD, including an unsupported platform."""


def need(condition, message):
    if not condition:
        raise Refusal(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def parsed(raw):
    def pairs(items):
        value = {}
        for k, v in items:
            need(k not in value, "duplicate JSON field")
            value[k] = v
        return value
    def bad(_):
        raise Refusal("nonfinite JSON")
    need(type(raw) is bytes and len(raw) <= LIMIT, "JSON bytes/limit")
    return json.loads(raw.decode("utf8"), object_pairs_hook=pairs, parse_constant=bad)


def same(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def positive(value):
    return type(value) is int and value > 0


def forbidden_resource_name(path):
    name = Path(path).name.casefold()
    return name == ".env" or name.startswith(".env.") or name.endswith((".pem", ".key")) or name.startswith("credentials.")


def unread_resource(path, st):
    # Privacy takes precedence even if a forbidden name happens to be code.
    need(Path(path).suffix.casefold() not in {".py", ".pyc", ".pyo", ".so", ".dylib", ".dll", ".pyd", ".pth", ".zip", ".whl"},
         "forbidden code/startup resource required: refuse without reading")
    need(stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode), "forbidden resource shape")
    return {"kind": "forbidden_unread_unused_resource", "not_trusted_or_certified": True,
            "mode": stat.S_IMODE(st.st_mode), "size": st.st_size, "device": st.st_dev, "inode": st.st_ino}


def unique(rows, field):
    need(type(rows) is list and all(type(r) is dict for r in rows), "row collection")
    values = [r.get(field) for r in rows]
    need(all(positive(x) for x in values) and len(values) == len(set(values)), "duplicate/missing " + field)
    return {r[field]: r for r in rows}


def regular(path):
    path = Path(path)
    need(not forbidden_resource_name(path), "forbidden resource bytes requested")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_size <= LIMIT, "regular file/limit")
        with os.fdopen(os.dup(fd), "rb") as stream:
            return stream.read(), st
    finally:
        os.close(fd)


def write_new(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    need(result.returncode == 0, "command refused: " + Path(args[0]).name)
    return result.stdout


def isolated_command(executable, *args):
    return [str(executable), "-I", "-S", "-B", *map(str, args)]


def git(root, *args, executable="git"):
    return command([executable, "-C", str(root), *args]).decode().strip()


def clean_at(root, expected, executable="git"):
    need(git(root, "rev-parse", "HEAD", executable=executable) == expected, "checkout commit")
    need(not git(root, "status", "--porcelain", "--untracked-files=no", executable=executable), "tracked checkout changes")


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700)
        self.rows = []

    def add(self, label, raw):
        need(type(label) is str and type(raw) is bytes and len(raw) <= LIMIT, "raw evidence")
        name = f"{len(self.rows):04d}.json"
        write_new(self.directory / name, raw)
        self.rows.append({"name": name, "source": label, "bytes": len(raw), "sha256": sha(raw),
                          "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        return parsed(raw)

    def manifest(self):
        return {"v": 1, "responses": list(self.rows)}


class GitHub:
    def __init__(self, evidence):
        self.evidence = evidence

    def get(self, endpoint):
        need(endpoint.startswith(f"repos/{REPO}/") and ".." not in endpoint and "#" not in endpoint, "API scope")
        return self.evidence.add("GET " + endpoint, command(["gh", "api", "--method", "GET", endpoint]))

    def pages(self, endpoint, key):
        rows, total = [], None
        for page in range(1, 12):
            join = "&" if "?" in endpoint else "?"
            data = self.get(f"{endpoint}{join}per_page=100&page={page}")
            count = data.get("total_count")
            need(type(count) is int and 0 <= count <= 1000 and type(data.get(key)) is list, "pagination count/schema")
            total = count if total is None else total
            need(total == count and len(data[key]) == min(100, total - len(rows)), "pagination incomplete/drift")
            rows.extend(data[key])
            if len(rows) == total:
                unique(rows, "id")
                return rows
        raise Refusal("pagination limit")


def repo_identity(value):
    need(type(value) is dict and value.get("full_name") == REPO and positive(value.get("id")), "repository provenance")
    return value["id"]


def pr_ready(pr):
    need(type(pr) is dict and pr.get("headRefOid") == RUNTIME and pr.get("baseRefOid") == BASE, "PR head/base drift")
    need(pr.get("state") == "OPEN" and pr.get("isDraft") is False and pr.get("mergeable") == "MERGEABLE", "PR readiness")
    checks = pr.get("statusCheckRollup")
    need(type(checks) is list and checks, "PR rows missing")
    for c in checks:
        need(type(c) is dict and c.get("__typename") == "CheckRun" and c.get("status") == "COMPLETED", "PR check type/status")
        need(c.get("conclusion") in {"SUCCESS", "SKIPPED"}, "PR check failed")
    urls = [c.get("detailsUrl") for c in checks]
    need(all(type(u) is str and u for u in urls) and len(urls) == len(set(urls)), "PR duplicate/invalid URL")
    return checks


def collect(api, pr):
    """Independent complete observed graph. No mutation execution or API writes."""
    pr_ready(pr)
    base = f"repos/{REPO}"
    rest_pr = api.get(f"{base}/pulls/906")
    need(rest_pr.get("number") == 906 and rest_pr.get("state") == "open" and rest_pr.get("draft") is False
         and rest_pr.get("mergeable") is True, "REST PR readiness")
    need(rest_pr["head"]["sha"] == RUNTIME and rest_pr["base"]["sha"] == BASE, "REST PR head/base")
    repo_id = repo_identity(rest_pr["base"]["repo"])
    need(repo_identity(rest_pr["head"]["repo"]) == repo_id, "PR head repository")
    checks = api.pages(f"{base}/commits/{RUNTIME}/check-runs?filter=all", "check_runs")
    suites = api.pages(f"{base}/commits/{RUNTIME}/check-suites", "check_suites")
    independent = api.pages(f"{base}/actions/workflows/{CI_WORKFLOW}/runs?head_sha={RUNTIME}", "workflow_runs")
    need(independent, "independent CI runs missing")
    check_map, suite_map = unique(checks, "id"), unique(suites, "id")
    runs, jobs, memberships = {}, {}, {}
    # The independent source includes a CI run even when it has no check row.
    ids = set(unique(independent, "id"))
    for c in checks:
        for url in (c.get("details_url"), c.get("html_url")):
            m = re.fullmatch(r"https://github\.com/charoiteai/Charoite_audio/actions/runs/([0-9]+)/job/([0-9]+)", url or "")
            if m:
                ids.add(int(m.group(1)))
    for rid in sorted(ids):
        current = api.get(f"{base}/actions/runs/{rid}")
        need(current.get("id") == rid and positive(current.get("run_attempt")), "run identity/attempt")
        attempts = current["run_attempt"]
        need(attempts <= 100, "run history limit")
        for attempt in range(1, attempts + 1):
            run = api.get(f"{base}/actions/runs/{rid}/attempts/{attempt}")
            need(run.get("id") == rid and run.get("run_attempt") == attempt, "missing run attempt")
            js = api.pages(f"{base}/actions/runs/{rid}/attempts/{attempt}/jobs", "jobs")
            need(js, "run jobs missing")
            for job in js:
                need(job["id"] not in jobs, "job duplicated across attempts")
                jobs[job["id"]] = job
            runs[(rid, attempt)] = run
            memberships[(rid, attempt)] = sorted(j["id"] for j in js)
        need(same(run_projection(current), run_projection(runs[(rid, attempts)])), "current run/attempt drift")
    for listing in independent:
        rid = listing["id"]
        need(same(run_projection(listing), run_projection(runs[(rid, listing["run_attempt"])])), "independent run/attempt drift")
    # Per-suite enumeration detects suite/check omissions independently as well.
    suite_memberships = {}
    for sid, suite in suite_map.items():
        rows = api.pages(f"{base}/check-suites/{sid}/check-runs?filter=all", "check_runs")
        suite_memberships[sid] = sorted(unique(rows, "id"))
        need(set(unique(rows, "id")) == {c["id"] for c in checks if c.get("check_suite", {}).get("id") == sid}, "suite/check set mismatch")
        need(all(same(row, check_map[row["id"]]) for row in rows), "suite/check response drift")
        need(suite.get("head_sha") == RUNTIME, "suite head")
    snapshot = {"pr": pr, "rest_pr": rest_pr, "checks": checks, "suites": suites,
                "independent_ci": independent, "runs": runs, "jobs": jobs, "memberships": memberships,
                "suite_memberships": suite_memberships}
    return snapshot


def run_pr_association(run):
    links = run.get("pull_requests")
    need(type(links) is list, "PR run association missing")
    expected_repo = repo_identity(run["repository"])
    matches = [p for p in links if p.get("number") == 906 and p.get("url") == f"https://api.github.com/repos/{REPO}/pulls/906"
               and p.get("head", {}).get("sha") == RUNTIME and p.get("head", {}).get("ref") == run["head_branch"]
               and p.get("base", {}).get("sha") == BASE
               and p.get("base", {}).get("ref") == "main"
               and p.get("head", {}).get("repo", {}).get("id") == expected_repo == p.get("base", {}).get("repo", {}).get("id")]
    need(len(matches) == 1, "PR run head/base association")


def run_projection(row):
    fields = ("id", "name", "run_attempt", "workflow_id", "path", "event", "head_sha", "head_branch", "status", "conclusion", "check_suite_id", "html_url", "repository", "head_repository", "pull_requests")
    value = {k: row[k] for k in fields if k in row}
    for key in ("repository", "head_repository"):
        if key in value:
            value[key] = {k: value[key].get(k) for k in ("id", "full_name")}
    if "pull_requests" in value:
        value["pull_requests"] = [{"number": p.get("number"), "url": p.get("url"),
                                   **{side: {"sha": p.get(side, {}).get("sha"), "ref": p.get(side, {}).get("ref"),
                                             "repo_id": p.get(side, {}).get("repo", {}).get("id")} for side in ("head", "base")}} for p in value["pull_requests"]]
    return value


def validate_snapshot(s):
    rollup = pr_ready(s["pr"])
    checks, suites = unique(s["checks"], "id"), unique(s["suites"], "id")
    need(suites and checks, "checks/suites missing")
    need(set(s["suite_memberships"]) == set(suites)
         and all(s["suite_memberships"][sid] == sorted(c["id"] for c in checks.values() if c.get("check_suite", {}).get("id") == sid) for sid in suites), "complete suite/check membership")
    repo_id = repo_identity(s["rest_pr"]["base"]["repo"])
    registrations = []
    for suite in suites.values():
        if suite["id"] == EMPTY_REGISTRATION_SUITE:
            # This is not an executed check or a CI run. ROOT authorized this
            # exact observed empty app registration classification, not a skip.
            need({"id", "app", "status", "conclusion", "latest_check_runs_count", "pull_requests", "head_sha", "head_branch", "repository"} <= suite.keys()
                 and type(suite["head_branch"]) is str and repo_identity(suite["repository"]) == repo_id and suite.get("head_sha") == RUNTIME
                 and suite.get("app", {}).get("id") == EMPTY_REGISTRATION_APP and suite["app"].get("slug") == "claude"
                 and suite.get("status") == "queued" and suite.get("conclusion") is None
                 and type(suite.get("latest_check_runs_count")) is int and suite["latest_check_runs_count"] == 0
                 and suite.get("pull_requests") == [] and s["suite_memberships"].get(suite["id"]) == []
                 and not any(c.get("check_suite", {}).get("id") == suite["id"] for c in checks.values())
                 and not any(r.get("check_suite_id") == suite["id"] for r in s["runs"].values()), "empty app registration provenance/membership changed")
            registrations.append({"kind": "empty_unexecuted_app_registration", "suite_id": suite["id"],
                                  "app_id": EMPTY_REGISTRATION_APP, "head_sha": suite["head_sha"],
                                  "status": "queued", "conclusion": None, "complete_filter_all_count": 0})
            continue
        need(repo_identity(suite["repository"]) == repo_id and suite.get("head_sha") == RUNTIME
             and suite.get("status") == "completed" and suite.get("conclusion") == "success", "suite provenance/status")
    matched, successes = set(), set()
    need(len([c for c in checks.values() if c.get("name") == "CodeQL"]) == 1, "duplicate/missing CodeQL role")
    for row in rollup:
        candidates = [c for c in checks.values() if row["detailsUrl"] in {c.get("details_url"), c.get("html_url")}]
        need(len(candidates) == 1, "ambiguous/missing GraphQL to REST URL")
        c = candidates[0]
        need(c["id"] not in matched and row["name"] == c["name"] and row["status"].lower() == c["status"]
             and row["conclusion"].lower() == c["conclusion"], "GraphQL/REST typed check mismatch")
        matched.add(c["id"])
    job_checks = {}
    for c in checks.values():
        need(c.get("head_sha") == RUNTIME and c.get("status") == "completed" and c.get("conclusion") in {"success", "skipped"}, "observed check failed/incomplete")
        sid = c.get("check_suite", {}).get("id")
        need(sid in suites and c.get("app", {}).get("id") == suites[sid].get("app", {}).get("id")
             and c.get("app", {}).get("slug") == suites[sid].get("app", {}).get("slug"), "check suite/app")
        if c["name"] == "CodeQL":
            need(c["app"]["slug"] == "github-advanced-security" and c["app"]["id"] == 57789
                 and c["conclusion"] == "success" and c["id"] in matched, "CodeQL app/status/rollup")
            need(c.get("details_url") == c.get("html_url") == f"https://github.com/{REPO}/runs/{c['id']}", "CodeQL URL")
        else:
            need(c["app"]["slug"] == "github-actions" and c["app"]["id"] == 15368, "Actions app")
            linked = [j for j in s["jobs"].values() if j.get("check_run_url") == f"https://api.github.com/repos/{REPO}/check-runs/{c['id']}"]
            need(len(linked) == 1, "check job URL linkage")
            j = linked[0]
            allowed_urls = {j.get("html_url"), f"https://github.com/{REPO}/runs/{c['id']}"}
            need(j["id"] not in job_checks and j["name"] == c["name"]
                 and all(c.get(k) in allowed_urls for k in ("html_url", "details_url") if c.get(k) is not None)
                 and j.get("conclusion") == c["conclusion"] and j.get("status") == c["status"], "check/job fields")
            job_checks[j["id"]] = c
            if c["id"] not in matched:
                need(c["name"] == "copilot-pull-request-reviewer" and c["conclusion"] == "success", "unknown REST-only check")
        if c["conclusion"] == "success":
            successes.add(c["name"])
    need(set(job_checks) == set(s["jobs"]), "missing check rows for observed run/attempt jobs")
    independent_ids = set(unique(s["independent_ci"], "id"))
    observed_ci, pr_runs, push_groups = set(), [], []
    for key, run in s["runs"].items():
        rid, attempt = key
        need(repo_identity(run["repository"]) == repo_id and repo_identity(run["head_repository"]) == repo_id
             and run.get("head_sha") == RUNTIME and run.get("status") == "completed" and run.get("conclusion") == "success"
             and run.get("html_url") == f"https://github.com/{REPO}/actions/runs/{rid}"
             and run.get("id") == rid and run.get("run_attempt") == attempt and positive(run.get("workflow_id")), "run provenance/conclusion")
        sid = run.get("check_suite_id")
        need(sid in suites and suites[sid]["app"]["slug"] == "github-actions", "run/suite link")
        js = [s["jobs"][jid] for jid in s["memberships"][key]]
        names = [j.get("name") for j in js]
        need(len(names) == len(set(names)), "duplicate job role in attempt")
        for j in js:
            need(j.get("run_id") == rid and j.get("run_attempt") == attempt and j.get("head_sha") == RUNTIME
                 and j.get("workflow_name") == run.get("name")
                 and j.get("head_branch") == run.get("head_branch") and j.get("status") == "completed"
                 and job_checks[j["id"]]["check_suite"]["id"] == sid
                 and j.get("html_url") == f"https://github.com/{REPO}/actions/runs/{rid}/job/{j['id']}", "job/run/attempt/provenance")
        if run["path"] == ".github/workflows/ci.yml":
            need(run["workflow_id"] == CI_WORKFLOW and rid in independent_ids, "unknown official CI run")
            observed_ci.add(rid)
            if run["event"] == "push":
                need(set(names) == CI_NORMAL | PAIR, "push job cohort")
                need(all(j["conclusion"] == ("skipped" if j["name"] in PAIR else "success") for j in js), "push skip pair/conclusion")
                push_groups.append([rid, attempt])
            else:
                need(run["event"] == "pull_request" and set(names) == CI_PR, "PR CI cohort/event")
                need(all(j["conclusion"] == "success" for j in js), "mandatory PR jobs")
                run_pr_association(run)
                pr_runs.append([rid, attempt])
        else:
            expected = OTHER_WORKFLOWS.get(run["path"])
            need(expected is not None and set(names) == expected and all(j["conclusion"] == "success" for j in js), "unknown workflow/extra or failed job")
            dynamic = run["path"].startswith("dynamic/")
            need(run["event"] == ("dynamic" if dynamic else "pull_request"), "workflow event")
            if not dynamic:
                run_pr_association(run)
        for j in js:
            c = job_checks[j["id"]]
            rows = [row for row in rollup if row["detailsUrl"] in {c.get("details_url"), c.get("html_url")}]
            need(not rows or rows[0]["workflowName"] == run["name"], "workflow name drift")
    need(observed_ci == independent_ids and push_groups and pr_runs, "independent CI run union/mandatory PR run")
    need(MANDATORY <= successes, "mandatory roles missing")
    need({c["check_suite"]["id"] for c in checks.values()} | {r["suite_id"] for r in registrations} == set(suites), "empty/unknown suite")
    return {"push_groups": sorted(push_groups), "mandatory_pr_runs": sorted(pr_runs),
            "rest_only": sorted(set(checks) - matched), "rollup_count": len(rollup), "rest_count": len(checks),
            "empty_unexecuted_app_registrations": registrations}


def projection(s):
    """All fields used above; retain raw noise separately. Missing != null."""
    def select(row, fields):
        return {k: row[k] for k in fields if k in row}
    pr = select(s["pr"], ("headRefOid", "baseRefOid", "state", "isDraft", "mergeable"))
    pr["statusCheckRollup"] = sorted(s["pr"]["statusCheckRollup"], key=lambda x: x["detailsUrl"])
    return {"pr": pr, "rest_pr": {k: s["rest_pr"].get(k) for k in ("number", "state", "draft", "mergeable", "mergeable_state", "head", "base")},
            "checks": sorted([select(c, ("id", "node_id", "name", "head_sha", "status", "conclusion", "html_url", "details_url", "app", "check_suite")) for c in s["checks"]], key=lambda c: c["id"]),
            "suites": sorted([select(c, ("id", "head_sha", "head_branch", "status", "conclusion", "app", "repository", "head_repository", "latest_check_runs_count", "pull_requests")) for c in s["suites"]], key=lambda c: c["id"]),
            "runs": [[list(k), run_projection(r)] for k, r in sorted(s["runs"].items())],
            "jobs": sorted([select(j, ("id", "run_id", "run_attempt", "name", "workflow_name", "head_sha", "head_branch", "status", "conclusion", "html_url", "check_run_url")) for j in s["jobs"].values()], key=lambda j: j["id"]),
            "memberships": [[list(k), v] for k, v in sorted(s["memberships"].items())],
            "suite_memberships": [[k, v] for k, v in sorted(s["suite_memberships"].items())],
            "independent_ci": sorted([[r["id"], r["run_attempt"], r["workflow_id"], r["head_sha"]] for r in s["independent_ci"]]),
            "policy": validate_snapshot(s)}


def fresh_rollup(evidence):
    return evidence.add("gh pr view906 independent observation", command(["gh", "pr", "view", "906", "--repo", REPO, "--json", "headRefOid,baseRefOid,isDraft,state,mergeable,statusCheckRollup"]))


def load_frozen(root):
    root = Path(root).resolve()
    clean_at(root, DEFINITION)
    raw, _ = regular(root / "scripts/release970_gate.py")
    need(sha(raw) == GATE_HASH, "frozen gate hash")
    need(sha(regular(root / ".github/release970_plan.json")[0]) == PLAN_HASH, "frozen plan hash")
    spec = importlib.util.spec_from_file_location("release980_frozen", root / "scripts/release970_gate.py")
    module = importlib.util.module_from_spec(spec)
    # Compile the authenticated bytes; avoid a stale/unchecked pyc or reread.
    exec(compile(raw, str(spec.origin), "exec"), module.__dict__)
    need(module.ROOT == root, "original ROOT")
    return module


def namespace(module):
    return {k: (id(v), id(v.__code__) if callable(v) and hasattr(v, "__code__") else None) for k, v in vars(module).items()}


def invoke_original(module, argv, evidence):
    original_main, original_aggregate = module.main, module.aggregate
    original_namespace = namespace(module)
    plan, plan_hash = module.load_plan()
    need(plan_hash == PLAN_HASH, "original plan")
    captured = []

    def replacement(pr, p):
        need(not captured, "repeated PR callback")
        frame = sys._getframe(1)
        try:
            need(frame.f_code is original_aggregate.__code__ and frame.f_globals is vars(module)
                 and frame.f_code.co_filename == str(module.ROOT / "scripts/release970_gate.py")
                 and frame.f_lineno == 522, "unsupported original aggregate call site")
            process = frame.f_locals["pr"]  # Read this ONE named local only.
            need(type(process) is subprocess.CompletedProcess and type(process.stdout) is bytes, "original PR stdout shape")
            raw = process.stdout
        finally:
            del frame
        need(same(module.strict_json(raw), pr) and same(p, plan), "original PR bytes/typed argument mismatch")
        evidence.add("ORIGINAL aggregate pr.stdout", raw)
        api = GitHub(evidence)
        initial = projection(collect(api, pr))
        bracket = projection(collect(api, fresh_rollup(evidence)))
        need(same(initial, bracket), "metadata drift during initial collection")
        captured.append({"raw_sha256": sha(raw), "projection": initial})

    module.validate_release_pr = replacement
    expected = namespace(module)
    need({k for k in expected if expected[k] != original_namespace[k]} == {"validate_release_pr"}, "only one symbol replacement")
    saved = sys.argv
    try:
        need(argv[0] == "external" and "--K" not in argv and len(argv) == len(set(argv)), "original external arguments")
        sys.argv = [str(module.ROOT / "scripts/release970_gate.py"), *argv]
        result = original_main()
    finally:
        sys.argv = saved
    need(type(result) is int and result == 0 and len(captured) == 1, "genuine original invocation/exit")
    need(namespace(module) == expected and module.main is original_main and module.aggregate is original_aggregate, "frozen namespace/code changed")
    final = projection(collect(GitHub(evidence), fresh_rollup(evidence)))
    need(same(captured[0]["projection"], final), "post-replay metadata drift")
    return captured[0]


def tree_hash(root):
    """Explicit trusted runtime closure, including bytecode and startup files."""
    root = Path(root)
    need(not forbidden_resource_name(root), "forbidden runtime closure root")
    need(root.is_absolute() and root.resolve() == root and root.is_dir(), "runtime closure root")
    rows = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        if forbidden_resource_name(path):
            rows.append([path.relative_to(root).as_posix(), unread_resource(path, st)])
            continue  # lstat only: NO open, read, link resolution or byte hash
        if stat.S_ISLNK(st.st_mode):
            # Link text is hashed here; resolved targets must independently be
            # pinned AND covered by a declared real closure before execution.
            rows.append([path.relative_to(root).as_posix(), "symlink", os.readlink(path)])
            continue
        if stat.S_ISREG(st.st_mode):
            need(not st.st_mode & 0o022, "writable runtime closure")
            # Runtime binaries can exceed the JSON evidence size limit.
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            rows.append([path.relative_to(root).as_posix(), stat.S_IMODE(st.st_mode), h.hexdigest()])
        else:
            need(stat.S_ISDIR(st.st_mode) and not st.st_mode & 0o022, "runtime closure entry")
    return sha(encoded(rows))


def verified_runtime_closure(pin):
    need(type(pin["closure"]) is dict and pin["closure"] and type(pin["search_paths"]) is list
         and type(pin["links"]) is dict and type(pin["excluded_links"]) is dict and type(pin["files"]) is dict
         and type(pin["forbidden_resources"]) is dict
         and pin["unused_resources_contract"] == "fixed_release980_stdlib_merge_no_resource_reads", "runtime closure/search paths/links")
    roots = [Path(p) for p in pin["closure"]]
    for path, expected in pin["files"].items():
        raw, st = regular(path)
        need(str(Path(path).resolve()) == path and sha(raw) == expected and not st.st_mode & 0o022, "explicit runtime file pin")
    observed_links = {}
    observed_excluded = {}
    observed_unread = {}
    for root in roots:
        need(tree_hash(root) == pin["closure"][str(root)], "managed runtime closure changed")
        for path in root.rglob("*"):
            if forbidden_resource_name(path):
                observed_unread[str(path)] = unread_resource(path, path.lstat())
                continue
            if path.is_symlink():
                target = path.resolve(strict=True)
                if str(path) in pin["excluded_links"]:
                    exclusion = pin["excluded_links"][str(path)]
                    need(type(exclusion) is dict and set(exclusion) == {"text", "target", "reason"}
                         and path.name == "site-packages" and exclusion["reason"] == "unused_global_site_packages"
                         and exclusion["text"] == os.readlink(path) and exclusion["target"] == str(target), "excluded runtime link pin")
                    observed_excluded[str(path)] = exclusion
                    continue  # not traversed, imported or made trusted
                need(not forbidden_resource_name(target), "required runtime link targets unread resource")
                need(any(target == r or target.is_relative_to(r) for r in roots) or str(target) in pin["files"], "runtime link target outside approved closure")
                observed_links[str(path)] = str(target)
    need(same(observed_links, pin["links"]), "runtime link target pins changed/incomplete")
    need(same(observed_excluded, pin["excluded_links"]), "excluded runtime link set")
    need(same(observed_unread, pin["forbidden_resources"]), "unread resource lstat set changed/incomplete")
    for path in pin["search_paths"]:
        need(type(path) is str and Path(path) in roots, "unapproved package search path")
    reject_excluded_search_paths(pin["search_paths"], pin)
    return roots


def reject_excluded_search_paths(paths, pin):
    excluded = [Path(p) for key, value in pin["excluded_links"].items() for p in (key, value["target"])]
    for path in paths:
        need(type(path) is str and path, "explicit nonempty startup path")
        resolved = Path(path).resolve()
        need(not any(resolved == p.resolve() or resolved.is_relative_to(p.resolve()) for p in excluded), "excluded runtime path touched")


def sanitized_environment(tools):
    # Preserve HOME and existing authentication/proxy routing; never log values.
    keep = {"HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
            "https_proxy", "http_proxy", "all_proxy", "no_proxy"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    dirs = [str(Path(tools[k]["path"]).parent) for k in ("git", "gh")]
    env.update({"PATH": os.pathsep.join(dict.fromkeys(dirs + ["/usr/bin", "/bin"])),
                "PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "GH_PROMPT_DISABLED": "1"})
    return env


def executable_pin(pin):
    need(type(pin) is dict and set(pin) == {"path", "realpath", "sha256"}, "executable pin schema")
    path = Path(pin["path"])
    need(path.is_absolute() and str(path.resolve()) == pin["realpath"], "executable realpath")
    resolved = path.resolve()
    need(not forbidden_resource_name(path) and not forbidden_resource_name(resolved), "forbidden executable bytes")
    fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        h = hashlib.sha256()
        with os.fdopen(os.dup(fd), "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        need(stat.S_ISREG(st.st_mode) and h.hexdigest() == pin["sha256"] and not st.st_mode & 0o022 and st.st_mode & 0o111, "executable hash/mode")
    finally:
        os.close(fd)


def runtime_pin(pin, tools, *, probe=True):
    need(type(pin) is dict and set(pin) == {"path", "realpath", "sha256", "version", "closure", "search_paths", "links", "excluded_links", "files", "venv_config", "canonical_search_path", "forbidden_resources", "unused_resources_contract"}, "managed Python schema")
    executable_pin({k: pin[k] for k in ("path", "realpath", "sha256")})
    need(type(pin["version"]) is str and pin["version"].startswith("3.12."), "supported CPython version")
    roots = verified_runtime_closure(pin)
    cfg = pin["venv_config"]
    need(type(cfg) is dict and set(cfg) == {"path", "sha256"}
         and Path(cfg["path"]) == Path(pin["path"]).parent.parent / "pyvenv.cfg", "managed venv config identity")
    cfg_raw = regular(cfg["path"])[0]
    need(sha(cfg_raw) == cfg["sha256"], "managed venv config bytes")
    include = [line.partition("=")[2].strip().lower() for line in cfg_raw.decode().splitlines()
               if line.partition("=")[0].strip().lower() == "include-system-site-packages"]
    need(include == ["false"], "managed venv must exclude global site packages")
    if probe:
        raw = command(isolated_command(pin["path"], "-c",
                       "import json,sys,sysconfig; print(json.dumps({'version':'.'.join(map(str,sys.version_info[:3])), 'implementation':sys.implementation.name, 'stdlib':sysconfig.get_path('stdlib'), 'purelib':sysconfig.get_path('purelib'), 'platlib':sysconfig.get_path('platlib')}))"),
                      env=sanitized_environment(tools))
        info = parsed(raw)
        need(info["implementation"] == "cpython" and info["version"] == pin["version"], "managed interpreter version")
        expected = {Path(info["stdlib"]).resolve()}
        venv_site = Path(pin["path"]).parent.parent / "lib" / ("python" + ".".join(pin["version"].split(".")[:2])) / "site-packages"
        if venv_site.is_dir():
            expected.add(venv_site.resolve())
        need(all(any(p == root or p.is_relative_to(root) for root in roots) for p in expected), "incomplete managed startup closure")
        need(set(pin["excluded_links"]) <= {str(Path(info["stdlib"]) / "site-packages")}, "only unused stdlib site registration may be excluded")
        # Only after independently pinned/audited managed startup code is trusted
        # may this probe execute the unchanged canonical child's plain startup.
        paths = parsed(command([pin["path"], "-B", "-c", "import json,sys;print(json.dumps(sys.path))"], env=sanitized_environment(tools)))
        need(same(paths, pin["canonical_search_path"]), "canonical child startup path changed")
        reject_excluded_search_paths(paths, pin)
    else:
        need(sys.implementation.name == "cpython" and ".".join(map(str, sys.version_info[:3])) == pin["version"]
             and str(Path(sys.executable).resolve()) == pin["realpath"] and sys.flags.isolated and sys.flags.no_site, "isolated pinned interpreter")
        reject_excluded_search_paths(list(sys.path) + pin["search_paths"], pin)
    return pin


def authorization(path, expected_hash):
    raw, _ = regular(path)
    need(HEX.fullmatch(expected_hash) and sha(raw) == expected_hash, "independent supplemental record bytes")
    record = parsed(raw)
    fields = {"v", "task", "verifier_sha", "verifier_sha256", "frozen_definition_sha", "frozen_gate_sha256",
              "original_root_approval_record_sha256", "pr_policy_fixture_manifest_sha256", "scope", "managed_python", "managed_tools"}
    need(type(record) is dict and set(record) == fields and type(record["v"]) is int and record["v"] == 1
         and type(record["task"]) is int and record["task"] == 980, "supplemental record schema")
    expected = {"frozen_definition_sha": DEFINITION, "frozen_gate_sha256": GATE_HASH,
                "original_root_approval_record_sha256": APPROVAL_HASH, "pr_policy_fixture_manifest_sha256": FIXTURE_HASH,
                "scope": "replace_validate_release_pr_only"}
    need(all(record[k] == v for k, v in expected.items()), "supplemental immutable pins")
    need(type(record["verifier_sha"]) is str and re.fullmatch("[0-9a-f]{40}", record["verifier_sha"])
         and type(record["verifier_sha256"]) is str and HEX.fullmatch(record["verifier_sha256"]), "verifier pins")
    need(type(record["managed_tools"]) is dict and set(record["managed_tools"]) == {"git", "gh"}, "tool pins")
    for pin in record["managed_tools"].values():
        executable_pin(pin)
    return record


def directory_fd(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, "delivery parent symlink/relative path")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        checked_directory(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def checked_directory(fd):
    st = os.fstat(fd)
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700, "private trusted directory owner/mode")
    return st


def basename(name):
    need(type(name) is str and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,160}", name), "delivery basename")
    return name


def absent(fd, name):
    basename(name)
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise Refusal("existing delivery/stale result preserved")


def stage_directory(parent, parent_fd):
    name = "release980-private-" + secrets.token_hex(16)
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    st = checked_directory(fd)
    need(st.st_dev == checked_directory(parent_fd).st_dev, "same-filesystem staging")
    return Path(parent) / name, fd


def link_sealed(stage, stage_fd, parent_fd, source, target, raw):
    """One regular file, same filesystem, atomic EEXIST; never replace/unlink."""
    basename(source); basename(target)
    checked_directory(stage_fd); checked_directory(parent_fd)
    need(os.link in os.supports_dir_fd and os.link in os.supports_follow_symlinks, "atomic link platform unsupported")
    need(os.fstat(stage_fd).st_dev == os.fstat(parent_fd).st_dev, "cross-filesystem publication")
    path = Path(stage) / source
    write_new(path, raw)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=stage_fd)
    try:
        os.fchmod(fd, 0o400)
        os.fsync(fd)
    finally:
        os.close(fd)
    sealed, st = regular(path)
    need(sealed == raw and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o400, "sealed identity/mode")
    os.link(source, target, src_dir_fd=stage_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        final = os.fstat(fd)
        need((final.st_dev, final.st_ino, final.st_nlink) == (st.st_dev, st.st_ino, 2), "publication inode/link identity")
        with os.fdopen(os.dup(fd), "rb") as stream:
            need(sha(stream.read()) == sha(raw), "published bytes")
        os.fsync(parent_fd)
    finally:
        os.close(fd)
    return {"sha256": sha(raw), "device": st.st_dev, "inode": st.st_ino, "bytes": len(raw)}


def bundle_bytes(files, binding):
    need(type(files) is dict and {"strict.json", "canonical.txt", "supplemental-pr-metadata.json", "raw-manifest.json"} <= files.keys(), "bundle roles")
    need(all(type(v) is bytes for v in files.values()) and sum(map(len, files.values())) < LIMIT, "bundle size/types")
    manifest = {"v": 1, "binding": binding, "files": {k: {"sha256": sha(v), "bytes": len(v)} for k, v in sorted(files.items())}}
    need("bundle-manifest.json" not in files, "manifest collision")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in {**files, "bundle-manifest.json": encoded(manifest)}.items():
            need(name in {"strict.json", "canonical.txt", "supplemental-pr-metadata.json", "raw-manifest.json", "bundle-manifest.json"}
                 or re.fullmatch(r"evidence/[0-9]{4}\.json", name), "bundle member name")
            archive.writestr(name, raw)
    return out.getvalue()


def validate_bundle(raw, binding):
    need(type(raw) is bytes and len(raw) <= LIMIT, "bundle limit")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        need(5 <= len(infos) <= 2000 and len({i.filename for i in infos}) == len(infos)
             and sum(i.file_size for i in infos) <= LIMIT, "bundle duplicates/size")
        need(all(not i.is_dir() and not stat.S_ISLNK(i.external_attr >> 16) and not i.flag_bits & 1
                 and i.compress_type == zipfile.ZIP_STORED for i in infos), "bundle member type")
        files = {i.filename: archive.read(i) for i in infos}
    manifest = parsed(files.pop("bundle-manifest.json"))
    need(type(manifest) is dict and set(manifest) == {"v", "binding", "files"} and type(manifest["v"]) is int and manifest["v"] == 1
         and same(manifest["binding"], binding), "bundle binding/schema")
    need(same(manifest["files"], {k: {"sha256": sha(v), "bytes": len(v)} for k, v in sorted(files.items())}), "bundle member digest")
    need({"strict.json", "canonical.txt", "supplemental-pr-metadata.json", "raw-manifest.json"} <= files.keys(), "bundle required roles")
    need(all(k in {"strict.json", "canonical.txt", "supplemental-pr-metadata.json", "raw-manifest.json"}
             or re.fullmatch(r"evidence/[0-9]{4}\.json", k) for k in files), "unknown bundle role")
    side = parsed(files["supplemental-pr-metadata.json"])
    need(type(side) is dict and set(side) == {"v", "kind", "outcome", "binding", "strict_sha256", "canonical_sha256", "raw_manifest_sha256", "original_pr_sha256", "projection", "projection_sha256"}
         and type(side["v"]) is int and side["v"] == 1 and side["kind"] == "supplemental_external" and side["outcome"] == "PASS"
         and same(side["binding"], binding), "sidecar binding/schema")
    need(side["strict_sha256"] == sha(files["strict.json"]) and side["canonical_sha256"] == sha(files["canonical.txt"])
         and side["raw_manifest_sha256"] == sha(files["raw-manifest.json"])
         and side["projection_sha256"] == sha(encoded(side["projection"])), "sidecar digest")
    strict = parsed(files["strict.json"])
    need(type(strict) is dict and set(strict) == {"strict", "context", "candidate", "tree", "plan_sha256", "keys", "uncapped_killed", "canonical_exit", "artifacts", "external_completed_run_verified", "canonical_files", "root_approval"}, "original strict schema")
    need(strict.get("strict") == "PASS" and strict.get("external_completed_run_verified") is True
         and same(strict.get("context"), binding["context"]) and strict.get("candidate") == RUNTIME
         and strict.get("tree") == binding["runtime_tree"]
         and strict.get("plan_sha256") == PLAN_HASH and strict.get("keys") == 682 and type(strict.get("keys")) is int
         and type(strict.get("uncapped_killed")) is int and strict.get("uncapped_killed") == 682
         and type(strict.get("canonical_exit")) is int and strict.get("canonical_exit") == 0
         and strict.get("canonical_files") == {"canonical.txt": sha(files["canonical.txt"])}, "original strict/context/canonical binding")
    artifacts = strict["artifacts"]
    need(type(artifacts) is list and len(artifacts) == 32 and {a.get("K") for a in artifacts} == set(range(1, 33))
         and len({a.get("id") for a in artifacts}) == 32
         and all(set(a) == {"K", "id", "digest"} and positive(a["id"]) and type(a["K"]) is int
                 and type(a["digest"]) is str and a["digest"].startswith("sha256:") and HEX.fullmatch(a["digest"][7:]) for a in artifacts), "original artifact receipts")
    approval = strict.get("root_approval", {})
    need(same(approval, {"record_sha256": APPROVAL_HASH, "definition_sha": DEFINITION, "branch": BRANCH,
                         "runtime_candidate": RUNTIME, "plan_sha256": PLAN_HASH}), "original strict root approval")
    rm = parsed(files["raw-manifest.json"])
    need(type(rm) is dict and set(rm) == {"v", "responses"} and type(rm["v"]) is int and rm["v"] == 1 and type(rm["responses"]) is list, "raw manifest schema")
    names = [r.get("name") for r in rm["responses"]]
    need(names and len(names) == len(set(names)) and {"evidence/" + n for n in names} == {n for n in files if n.startswith("evidence/")}, "raw evidence cohort")
    originals = []
    for row in rm["responses"]:
        need(set(row) == {"name", "source", "bytes", "sha256", "observed_at_utc"}, "raw record schema")
        value = files["evidence/" + row["name"]]
        need(type(row["bytes"]) is int and type(row["observed_at_utc"]) is str
             and row["bytes"] == len(value) and row["sha256"] == sha(value), "raw response bytes")
        parsed(value)
        if row["source"] == "ORIGINAL aggregate pr.stdout":
            originals.append(value)
    need(len(originals) == 1 and side["original_pr_sha256"] == sha(originals[0]), "original stdout binding")
    return manifest


@dataclasses.dataclass(frozen=True)
class ObservedExit:
    pid: int
    returncode: int
    invocation: str
    command_sha256: str


def accept_published(stage, stage_fd, parent_fd, delivery, acceptance, binding, observed):
    """ROOT consumer; reachable from supervisor only after its own wait()."""
    need(type(observed) is ObservedExit and positive(observed.pid) and type(observed.returncode) is int
         and observed.returncode == 0 and observed.invocation == binding["invocation"]
         and observed.command_sha256 == binding["command_sha256"], "consumer requires independently observed actual exit0")
    absent(parent_fd, acceptance)
    fd = os.open(basename(delivery), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o400
             and st.st_nlink == 2 and st.st_size <= LIMIT, "consumer sealed file identity/mode")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read()
        validate_bundle(raw, binding)
        sealed_fd = os.open("bundle-sealed.zip", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=stage_fd)
        try:
            staged = os.fstat(sealed_fd)
            need(stat.S_ISREG(staged.st_mode) and staged.st_uid == os.getuid() and stat.S_IMODE(staged.st_mode) == 0o400
                 and staged.st_nlink == 2 and staged.st_dev == st.st_dev and staged.st_ino == st.st_ino,
                 "consumer staged/delivery alias identity")
            with os.fdopen(os.dup(sealed_fd), "rb") as sealed:
                need(sha(sealed.read()) == sha(raw), "consumer sealed alias bytes")
        finally:
            os.close(sealed_fd)
        need((st.st_dev, st.st_ino) == (os.stat(delivery, dir_fd=parent_fd, follow_symlinks=False).st_dev,
                                      os.stat(delivery, dir_fd=parent_fd, follow_symlinks=False).st_ino), "consumer path drift")
    finally:
        os.close(fd)
    record = {"v": 1, "accepted": True, "binding": binding, "bundle_sha256": sha(raw), "bundle_device": st.st_dev,
              "bundle_inode": st.st_ino, "bundle_name": delivery, "observed_child": dataclasses.asdict(observed)}
    # Protected fresh record is also published whole, without an overwrite window.
    link_sealed(stage, stage_fd, parent_fd, "acceptance-sealed.json", acceptance, encoded(record))
    return record


def core_arguments(config, stage):
    return ["external", "--source", config["source"], "--out", str(Path(stage) / "core-out"), "--context", config["context_path"],
            "--expected-definition-sha", DEFINITION, "--expected-branch", BRANCH, "--approval-record", config["original_approval"],
            "--approval-record-sha256", APPROVAL_HASH]


def child_verify(config_path):
    config = parsed(regular(config_path)[0])
    record = authorization(config["supplemental_record"], config["supplemental_record_hash"])
    need(sha(regular(Path(__file__))[0]) == record["verifier_sha256"], "sealed verified code bytes")
    runtime_pin(record["managed_python"], record["managed_tools"], probe=False)
    # Restore ONLY explicit cryptographically pinned package paths, no .pth/site.
    for path in record["managed_python"]["search_paths"]:
        if path not in sys.path:
            sys.path.append(path)
    stage = Path(config["stage"])
    parent_fd = config["parent_fd"]
    parent_st = checked_directory(parent_fd)
    need([parent_st.st_dev, parent_st.st_ino] == config["parent_identity"], "inherited parent identity")
    stage_fd = directory_fd(stage)
    try:
        absent(parent_fd, config["delivery"])
        need(not (stage / "core-out").exists() and not (stage / "core-out").is_symlink(), "fresh exclusive original out")
        evidence = Evidence(stage / "evidence")
        module = load_frozen(config["definition_root"])
        captured = invoke_original(module, core_arguments(config, stage), evidence)
        binding = config["binding"]
        strict = regular(stage / "core-out/strict.json")[0]
        canonical = regular(stage / "core-out/canonical.txt")[0]
        raw_manifest = encoded(evidence.manifest())
        side = {"v": 1, "kind": "supplemental_external", "outcome": "PASS", "binding": binding,
                "strict_sha256": sha(strict), "canonical_sha256": sha(canonical), "raw_manifest_sha256": sha(raw_manifest),
                "original_pr_sha256": captured["raw_sha256"], "projection": captured["projection"], "projection_sha256": sha(encoded(captured["projection"]))}
        files = {"strict.json": strict, "canonical.txt": canonical, "supplemental-pr-metadata.json": encoded(side), "raw-manifest.json": raw_manifest}
        files.update({"evidence/" + r["name"]: regular(evidence.directory / r["name"])[0] for r in evidence.rows})
        raw = bundle_bytes(files, binding)
        validate_bundle(raw, binding)
        link_sealed(stage, stage_fd, parent_fd, "bundle-sealed.zip", config["delivery"], raw)
        return 0
    finally:
        os.close(stage_fd)


def supervise(args):
    need(sys.flags.isolated and sys.flags.no_site and sys.implementation.name == "cpython", "ROOT must launch supervisor with -I -S")
    record = authorization(args.supplemental_approval_record, args.supplemental_approval_record_sha256)
    tools = record["managed_tools"]
    clean_at(args.verifier_root, record["verifier_sha"], executable=tools["git"]["path"])
    helper, _ = regular(Path(args.verifier_root) / "scripts/release980_external.py")
    need(sha(helper) == record["verifier_sha256"] == sha(regular(__file__)[0]), "pre-exec verified helper bytes")
    runtime_pin(record["managed_python"], tools)
    # Gate every external input before the candidate child is allowed to execute.
    clean_at(args.definition_root, DEFINITION, executable=tools["git"]["path"])
    need(sha(regular(Path(args.definition_root) / "scripts/release970_gate.py")[0]) == GATE_HASH, "pre-exec frozen gate")
    ctx_raw = regular(args.context)[0]
    ctx = parsed(ctx_raw)
    expected_ctx = {"run_id": 42405663537, "attempt": 1, "definition_sha": DEFINITION,
                    "repo": REPO, "branch": BRANCH, "workflow": ".github/workflows/release-970-critical.yml"}
    need(same(ctx, expected_ctx) and sha(regular(args.approval_record)[0]) == APPROVAL_HASH, "pre-exec context/original approval")
    plan_raw = regular(Path(args.definition_root) / ".github/release970_plan.json")[0]
    need(sha(plan_raw) == PLAN_HASH, "pre-exec plan bytes")
    need(HEX.fullmatch(args.invocation_id), "fresh invocation ID format")
    parent = Path(args.delivery_parent)
    parent_fd = directory_fd(parent)
    stage_fd = None
    try:
        need(args.delivery_name != args.acceptance_name, "distinct delivery and acceptance")
        absent(parent_fd, args.delivery_name); absent(parent_fd, args.acceptance_name)
        stage, stage_fd = stage_directory(parent, parent_fd)
        # Fresh invocation claim survives failures; a nonce can never be reused.
        write_new(parent / ("invocation-" + args.invocation_id + ".json"), encoded({"invocation": args.invocation_id}))
        sealed_helper = stage / "verified-helper.py"
        write_new(sealed_helper, helper, 0o400)
        write_new(stage / "context.json", ctx_raw, 0o400)
        write_new(stage / "original-approval.json", regular(args.approval_record)[0], 0o400)
        write_new(stage / "supplemental-record.json", regular(args.supplemental_approval_record)[0], 0o400)
        launch = stage / "launch.json"
        cmd = isolated_command(record["managed_python"]["path"], sealed_helper, "_verify", "--launch", launch)
        st = checked_directory(parent_fd)
        binding = {"invocation": args.invocation_id, "command_sha256": sha(encoded(cmd)), "verifier_sha": record["verifier_sha"],
                   "verifier_sha256": record["verifier_sha256"], "supplemental_record_sha256": args.supplemental_approval_record_sha256,
                   "definition_sha": DEFINITION, "gate_sha256": GATE_HASH, "runtime": RUNTIME, "plan_sha256": PLAN_HASH,
                   "runtime_tree": parsed(plan_raw)["candidate_tree"],
                   "original_approval_sha256": APPROVAL_HASH, "context_sha256": sha(ctx_raw), "context": ctx,
                   "managed_python": record["managed_python"], "parent_identity": [st.st_dev, st.st_ino],
                   "delivery": args.delivery_name, "acceptance": args.acceptance_name}
        config = {"stage": str(stage), "parent_fd": parent_fd, "parent_identity": [st.st_dev, st.st_ino], "definition_root": str(Path(args.definition_root).resolve()),
                  "source": str(Path(args.source).resolve()), "context_path": str(stage / "context.json"), "original_approval": str(stage / "original-approval.json"),
                  "supplemental_record": str(stage / "supplemental-record.json"), "supplemental_record_hash": args.supplemental_approval_record_sha256,
                  "delivery": args.delivery_name, "binding": binding}
        write_new(launch, encoded(config), 0o400)
        write_new(stage / "trusted-preexec.json", encoded({"v": 1, "binding": binding, "launch_sha256": sha(encoded(config)), "startup": "isolated-no-site; pinned package paths explicitly restored without .pth"}), 0o400)
        env = sanitized_environment(tools)
        # The observed result comes only from THIS Popen/wait, never a CLI flag.
        with (stage / "child.log").open("xb") as log:
            process = subprocess.Popen(cmd, cwd=stage, env=env, pass_fds=(parent_fd,), stdout=log, stderr=subprocess.STDOUT)
            rc = process.wait()
        observed = ObservedExit(process.pid, rc, args.invocation_id, binding["command_sha256"])
        write_new(stage / "observed-exit.json", encoded(dataclasses.asdict(observed)), 0o400)
        result = accept_published(stage, stage_fd, parent_fd, args.delivery_name, args.acceptance_name, binding, observed)
        print(json.dumps({"accepted": True, "invocation": args.invocation_id, "bundle_sha256": result["bundle_sha256"]}))
        return 0
    finally:
        if stage_fd is not None:
            os.close(stage_fd)
        os.close(parent_fd)


def parser():
    class StrictParser(argparse.ArgumentParser):
        def parse_args(self, args=None, namespace=None):
            values = sys.argv[1:] if args is None else args
            flags = [x for x in values if x.startswith("--")]
            need(len(flags) == len(set(x.split("=", 1)[0] for x in flags)), "duplicate CLI flags")
            return super().parse_args(args, namespace)
    ap = StrictParser(description=__doc__, allow_abbrev=False)
    sub = ap.add_subparsers(dest="mode", required=True)
    child = sub.add_parser("_verify", allow_abbrev=False)
    child.add_argument("--launch", required=True, type=Path)
    sup = sub.add_parser("supervise", allow_abbrev=False)
    for name in ("verifier-root", "definition-root", "source", "context", "approval-record", "supplemental-approval-record", "delivery-parent"):
        sup.add_argument("--" + name, required=True, type=Path)
    for name in ("supplemental-approval-record-sha256", "delivery-name", "acceptance-name", "invocation-id"):
        sup.add_argument("--" + name, required=True)
    return ap


def main():
    args = parser().parse_args()
    return child_verify(args.launch) if args.mode == "_verify" else supervise(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Refusal, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        # Private stage/logs survive. No cleanup can erase a failed proof.
        print(f"release980 HOLD: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
