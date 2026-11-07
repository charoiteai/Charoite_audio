"""One frozen release proof. Does not change the canonical mutator's policy."""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ".github/workflows/release-970-critical.yml"
REPO = "charoiteai/Charoite_audio"
RELEASE_BASE = "005832892bfb8b9d8cca9d90ed70537d69242d3a"
SHARDS = 32
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PAYLOAD = {"facts.json", "journal.jsonl", "report.txt", "environment.json", "provenance.json", "imports.json"}
CANONICAL_PATHS = ("scripts/mutate_check.py", "scripts/layout_map.py", "src/exit_codes.py",
                   "src/live_gate.py", "src/machine_power.py", "src/busy_signals.py",
                   "src/charoite_paths.py", "src/file_locks.py")
PLAN_PAYLOAD = {"plan.json", "provenance.json"}
FINAL_PAYLOAD = {"strict.json", "canonical.txt"}
INT_FACTS = {"K", "N", "M", "P", "full", "tested", "skipped", "unread", "nodes", "lines_in",
             "broken_rc", "critical_full", "critical_sampled", "critical_M", "critical_tested", "critical_skipped"}
STR_FACTS = {"broken", "aborted", "zones", "zones_note", "base", "head", "run", "word"}


class GateError(ValueError):
    pass


def require(ok, field):
    if not ok:
        raise GateError(field)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(items):
    out = {}
    for key, value in items:
        require(key not in out, "duplicate JSON field")
        out[key] = value
    return out


def strict_json(data):
    def bad_constant(_):
        raise GateError("nonfinite JSON number")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=bad_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("malformed UTF8/JSON") from exc


def integer(x):
    return type(x) is int


def same_typed(actual, expected):
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(same_typed(actual[k], v) for k, v in expected.items())
    if type(expected) is list:
        return len(actual) == len(expected) and all(same_typed(a, b) for a, b in zip(actual, expected))
    return actual == expected


def validate_context(d):
    require(type(d) is dict and set(d) == {"run_id", "attempt", "definition_sha", "repo", "branch", "workflow"}, "context schema")
    require(all(integer(d[x]) and d[x] > 0 for x in ("run_id", "attempt")), "context numeric identity")
    require(d["repo"] == REPO and type(d["definition_sha"]) is str and HEX40.fullmatch(d["definition_sha"]), "context repository/definition")
    require(type(d["branch"]) is str and d["branch"].startswith("verify/release970-") and d["workflow"] == WORKFLOW, "context workflow/branch")
    return d


def validate_approval(ctx, p, plan_sha, expected_definition_sha, expected_branch,
                      approval_record, approval_record_sha256):
    require(type(expected_definition_sha) is str and HEX40.fullmatch(expected_definition_sha), "trusted expected definition required")
    require(type(expected_branch) is str and expected_branch.startswith("verify/release970-"), "trusted expected exact branch required")
    require(type(approval_record_sha256) is str and HEX64.fullmatch(approval_record_sha256), "trusted approval record hash required")
    require(type(approval_record) is bytes and digest(approval_record) == approval_record_sha256, "root approval record bytes")
    expected = {"definition_sha": expected_definition_sha, "branch": expected_branch,
                "runtime_candidate": p["candidate"], "plan_sha256": plan_sha}
    require(same_typed(strict_json(approval_record), expected), "root approval record pins")
    require(ctx["definition_sha"] == expected_definition_sha and ctx["branch"] == expected_branch,
            "context differs from trusted approved definition/branch")
    return {**expected, "record_sha256": approval_record_sha256}


def context():
    d = {"run_id": int(os.environ["GITHUB_RUN_ID"]), "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
         "definition_sha": os.environ["GITHUB_SHA"], "repo": os.environ["GITHUB_REPOSITORY"],
         "branch": os.environ["GITHUB_REF_NAME"], "workflow": WORKFLOW}
    validate_context(d)
    require(git(ROOT, "rev-parse", "HEAD") == d["definition_sha"], "definition checkout")
    require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"), "definition tracked changes")
    return d


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def load_plan():
    raw = (ROOT / ".github/release970_plan.json").read_bytes()
    p = strict_json(raw)
    require(type(p) is dict and p["shards"] == SHARDS and integer(p["shards"]), "plan N")
    require(len(p["plan"]) == 682 and p["totals"]["critical_full"] == 682, "plan count")
    require(HEX40.fullmatch(p["candidate"]) and HEX40.fullmatch(p["baseline"]), "plan SHAs")
    keys = [x["key"] for x in p["plan"]]
    require(len(set(keys)) == 682, "plan unique keys")
    for i, row in enumerate(p["plan"]):
        require(integer(row["index"]) and row["index"] == i, "plan index")
        require(integer(row["K"]) and row["K"] == i % SHARDS + 1 and integer(row["N"]) and row["N"] == SHARDS, "plan modulo")
        require(row["critical"] is True and type(row["key"]) is str, "plan critical key")
    require(digest("\n".join(keys).encode()) == p["ordered_keys_sha256"], "plan ordered digest")
    return p, digest(raw)


def engine(source):
    sys.path[:0] = [str(source / "scripts"), str(source / "src")]
    spec = importlib.util.spec_from_file_location("release970_canonical", source / "scripts/mutate_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name in ("layout_map", "charoite_paths", "exit_codes", "busy_signals", "machine_power", "live_gate"):
        obj = sys.modules.get(name)
        if obj:
            require(pathlib.Path(obj.__file__).resolve().is_relative_to(source.resolve()), "canonical import origin")
    return module


def preflight(source, p):
    require(git(source, "rev-parse", "HEAD") == p["candidate"], "runtime checkout SHA")
    require(git(source, "rev-parse", "HEAD^{tree}") == p["candidate_tree"], "runtime tree")
    require(not git(source, "status", "--porcelain", "--untracked-files=no"), "runtime tracked changes")
    require(subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", p["baseline"], p["candidate"]],
                           capture_output=True).returncode == 0, "baseline ancestry")
    for path, expected in p["hashes"].items():
        require(digest((source / path).read_bytes()) == expected, "runtime dependency hash")
    m = engine(source)
    plan, totals = m.plan_for(source, p["range"], max_n=p["max"], only_critical=True)
    require([x.key for x in plan] == [x["key"] for x in p["plan"]], "actual canonical IDs/order")
    for actual, row in zip(plan, p["plan"]):
        require((actual.path.relative_to(source).as_posix(), actual.line, actual.qualname, actual.what, actual.critical)
                == (row["path"], row["line"], row["qualname"], row["what"], row["critical"]), "actual canonical identity metadata")
    for field in ("planned", "full", "critical_full", "critical_sampled", "files_unreadable", "sample", "zones"):
        require(getattr(totals, field) == p["totals"][field], "actual canonical plan metadata")
    for path, expected in p["suites_by_path"].items():
        require(m.tests_for(source, source / path) == expected, "actual selected suite")
    require(m.run_key(p["range"], p["max"], p["timeout"], totals.sample) == p["canonical_run_key"], "run key")
    require(m.run_caps(p["timeout"]) == p["caps"], "canonical caps")
    return m


def environment():
    packages = {}
    for name in ("pytest", "pytest-timeout", "PyYAML", "sounddevice", "numpy", "onnxruntime", "setuptools"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    native = subprocess.run(["dpkg-query", "-W", "-f=${Version}", "libportaudio2"], capture_output=True, text=True)
    return {"python": ".".join(map(str, sys.version_info[:3])), "packages": packages,
            "native_portaudio": native.stdout.strip() if native.returncode == 0 else None,
            "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion")}


def expected_keys(p, k):
    require(integer(k) and 1 <= k <= SHARDS, "K type/range")
    return [r["key"] for r in p["plan"] if r["K"] == k]


def validate_journal(raw, p, k):
    require(raw and raw.endswith(b"\n"), "journal truncated/empty")
    lines = raw.splitlines()
    require(all(lines), "journal blank line")
    rows = [strict_json(line) for line in lines]
    header = {"manifest": p["facts_version"], "range": p["range"], "max": p["max"],
              "timeout": p["timeout"], "sample": p["totals"]["sample"], "caps": p["caps"]}
    require(type(rows[0]) is dict and set(rows[0]) == set(header), "journal header schema")
    require(all(type(rows[0][x]) is type(y) and rows[0][x] == y for x, y in header.items()), "journal header")
    require(all(integer(x) for x in rows[0]["caps"]), "journal cap types")
    records = rows[1:]
    require(len(records) == len(expected_keys(p, k)), "journal count")
    keys = []
    for row in records:
        require(type(row) is dict and {"key", "outcome", "s"} <= set(row)
                and set(row) <= {"key", "outcome", "s", "capped", "why"}, "journal row schema")
        require(type(row["key"]) is str and row["outcome"] == "killed", "journal outcome/key")
        require(type(row["s"]) in (int, float) and math.isfinite(row["s"]) and row["s"] >= 0, "journal duration")
        require("capped" not in row or row["capped"] is False, "journal capped")
        require("why" not in row or (type(row["why"]) is str and row["why"] == ""), "journal reason")
        keys.append(row["key"])
    require(keys == expected_keys(p, k) and len(set(keys)) == len(keys), "journal exact IDs/order/modulo")
    return keys


def validate_facts(raw, p, k):
    f = strict_json(raw)
    require(type(f) is dict and set(f) == INT_FACTS | STR_FACTS | {"v", "survivors"}, "Facts schema")
    require(all(integer(f[x]) for x in INT_FACTS | {"v"}), "Facts integer types")
    require(all(type(f[x]) is str for x in STR_FACTS) and type(f["survivors"]) is list, "Facts scalar types")
    n = len(expected_keys(p, k))
    expected = {"v": 2, "K": k, "N": SHARDS, "M": n, "P": 682, "full": p["totals"]["full"],
                "tested": n, "skipped": 0, "unread": 0, "nodes": p["totals"]["nodes"],
                "lines_in": p["totals"]["lines_in"], "broken": "", "broken_rc": 0, "aborted": "",
                "critical_full": 682, "critical_sampled": 682, "critical_M": n, "critical_tested": n,
                "critical_skipped": 0, "zones": p["totals"]["zones"], "zones_note": "",
                "base": p["baseline"], "head": p["candidate"], "run": p["canonical_run_key"],
                "survivors": [], "word": "ok"}
    require(f == expected, "Facts expected counts/source/outcome")
    return f


def unzip(raw, expected):
    require(raw, "empty artifact")
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [info.filename for info in z.infolist()]
            require(len(names) == len(set(names)), "archive duplicate names")
            for info in z.infolist():
                require(info.filename in expected and info.filename not in files, "archive filename/duplicate")
                require(not info.is_dir() and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG), "archive regular file")
                require(info.file_size <= 5_000_000, "artifact member size")
                files[info.filename] = z.read(info)
    except zipfile.BadZipFile as exc:
        raise GateError("artifact ZIP") from exc
    require(set(files) == expected and all(files.values()), "artifact payload files")
    return files


def observation_policy(source, p, k):
    m = engine(source)
    planned, _ = m.plan_for(source, p["range"], max_n=60, only_critical=True)
    keys = set(expected_keys(p, k))
    variants = {}
    with tempfile.TemporaryDirectory(prefix="release970-expected-") as directory:
        own = pathlib.Path(directory) / "source.py"
        for mut in planned:
            if mut.key not in keys:
                continue
            own.write_bytes(mut.path.read_bytes())
            text, why = m.applied(mut, own.read_text(encoding="utf-8"))
            require(text is not None and not why, "legacy expected variant preparation")
            path = mut.path.relative_to(source).as_posix()
            variants[mut.key] = {"path": path, "sha256": digest(text.encode("utf-8"))}
    require(set(variants) == keys and len({(v["path"], v["sha256"]) for v in variants.values()}) == len(keys), "unique legacy variant bytes")
    hashes = dict(p["hashes"])
    for path in CANONICAL_PATHS:
        raw = (source / path).read_bytes()
        blob = subprocess.check_output(["git", "-C", str(source), "show", f"{p['candidate']}:{path}"])
        require(raw == blob, "actual canonical observer dependency")
        hashes[path] = digest(raw)
    return hashes, variants


def validate_imports(raw, p, ctx, k, hashes, variants):
    require(type(raw) is bytes and len(raw) <= 4_000_000, "observer payload bytes")
    data = strict_json(raw)
    require(type(data) is dict and set(data) == {"v", "canonical_pid", "policy_pid", "observer_sha256", "records"}, "import envelope schema")
    require(data["v"] == 1 and integer(data["v"]) and all(integer(data[x]) and data[x] > 0 for x in ("canonical_pid", "policy_pid")), "observer numeric identities")
    require(data["canonical_pid"] != data["policy_pid"], "observer parent differs from canonical PID")
    observer_sha = digest((ROOT / "scripts/release970_observer.py").read_bytes())
    require(data["observer_sha256"] == observer_sha and type(data["records"]) is list, "observer source/records")
    records = data["records"]
    wanted = expected_keys(p, k)
    suites = {tuple(p["suites_by_path"][r["path"]]) for r in p["plan"] if r["K"] == k}
    require(len(records) == 1 + len(wanted) + len(suites) <= 64, "observer process count")
    fields = {"v", "pid", "parent", "role", "cwd", "argv", "executable", "head", "tree", "started_ns", "elapsed_ns", "key", "snapshot", "origins", "errors", "context", "K", "observer_sha256"}
    seen_pids = set()
    for record in records:
        require(type(record) is dict and set(record) == fields, "process receipt schema")
        require(len(json.dumps(record).encode()) + 1 <= 100_000, "process receipt bytes")
        require(all(integer(record[x]) and record[x] > 0 for x in ("v", "pid", "parent", "started_ns", "elapsed_ns", "K")), "process receipt integer types")
        require(record["v"] == 1 and record["K"] == k and record["elapsed_ns"] <= 3600*10**9, "process shard/elapsed")
        require(record["pid"] not in seen_pids, "duplicate observed PID")
        seen_pids.add(record["pid"])
        require(validate_context(record["context"]) == ctx and record["observer_sha256"] == observer_sha, "process run/observer identity")
        require(type(record["errors"]) is list and not record["errors"], "observer error/foreign source")
        require(type(record["cwd"]) is str and pathlib.Path(record["cwd"]).is_absolute(), "process actual cwd")
        require(record["head"] == p["candidate"] and record["tree"] == p["candidate_tree"], "observed checkout head/tree")
        require(type(record["argv"]) is list and all(type(x) is str for x in record["argv"]) and len(record["argv"]) <= 128, "process actual argv")
        require(type(record["executable"]) is str and pathlib.Path(record["executable"]).is_absolute(), "process executable")
        require(type(record["snapshot"]) is dict and type(record["origins"]) is list and len(record["origins"]) <= 256, "observed source schema")
    canonical = [r for r in records if r["role"] == "canonical"]
    require(len(canonical) == 1, "one actual canonical process")
    canonical = canonical[0]
    require(canonical["pid"] == data["canonical_pid"] and canonical["parent"] == data["policy_pid"] and canonical["key"] is None, "canonical parent linkage")
    require(canonical["snapshot"] == hashes, "actual canonical source hashes")
    cwd = pathlib.Path(canonical["cwd"])
    require(canonical["argv"][1:] == canonical_command(cwd, p, k, pathlib.Path(canonical["argv"][-1]))[1:], "observed canonical command")
    children = sorted((r for r in records if r["role"] != "canonical"), key=lambda r: r["started_ns"])
    base_suites, mutant_keys = [], []
    for r in [canonical] + children:
        expected = dict(hashes)
        if r is not canonical:
            require(r["role"] == "pytest" and r["parent"] == canonical["pid"], "direct pytest parent linkage")
            require(r["executable"] == canonical["executable"], "actual interpreter linkage")
            require(r["started_ns"] > canonical["started_ns"] and r["started_ns"] + r["elapsed_ns"] <= canonical["started_ns"] + canonical["elapsed_ns"], "observed child elapsed linkage")
            require(r["argv"][:4] == [canonical["argv"][0], "-B", "-m", "pytest"] and r["argv"][-7:] == ["-q", "-p", "no:cacheprovider", "-p", "no:xdist", "--timeout", "120"], "observed pytest command")
            suite = tuple(r["argv"][4:-7])
            if r["key"] is None:
                base_suites.append(suite)
            else:
                require(type(r["key"]) is str and r["key"] in variants, "observed approved mutant key")
                variant = variants[r["key"]]
                expected[variant["path"]] = variant["sha256"]
                require(variant["sha256"] != hashes[variant["path"]], "mutant differs from baseline")
                require(suite == tuple(p["suites_by_path"][variant["path"]]), "observed key/suite")
                mutant_keys.append(r["key"])
            require(r["snapshot"] == expected, "actual pytest mutated/baseline hashes")
        origins = {}
        for origin in r["origins"]:
            require(type(origin) is dict and set(origin) == {"path", "relative", "sha256"}, "actual origin schema")
            rel = origin["relative"]
            require(type(rel) is str and rel in expected and origin["path"] == str(pathlib.Path(r["cwd"]) / rel)
                    and origin["sha256"] == expected[rel], "actual origin/root/source hash")
            require(rel not in origins, "duplicate executed source")
            origins[rel] = origin["sha256"]
        if r is canonical:
            require(set(CANONICAL_PATHS) <= set(origins), "actual canonical imports missing")
        elif r["key"] is not None:
            require(variants[r["key"]]["path"] in origins, "actual mutant source execution missing")
    require(collections.Counter(base_suites) == collections.Counter(suites) and mutant_keys == wanted, "exact baseline suites and ordered mutant children")
    return data


def validate_payload(files, p, plan_sha, ctx, k, hashes=None, variants=None):
    require(set(files) == PAYLOAD, "payload allowlist")
    prov = strict_json(files["provenance.json"])
    require(type(prov) is dict and set(prov) == {"context", "candidate", "tree", "plan_sha256", "K", "N", "mutator_rc", "hashes", "files"}, "provenance schema")
    require(validate_context(prov["context"]) == ctx, "payload run/attempt/definition")
    require(integer(prov["K"]) and prov["K"] == k and integer(prov["N"]) and prov["N"] == SHARDS, "payload K/N")
    require(integer(prov["mutator_rc"]) and prov["mutator_rc"] == 0, "mutator exit")
    require(prov["candidate"] == p["candidate"] and prov["tree"] == p["candidate_tree"], "payload checkout/tree")
    require(prov["plan_sha256"] == plan_sha and prov["hashes"] == p["hashes"], "payload source/config hashes")
    expected_files = {name: digest(data) for name, data in files.items() if name != "provenance.json"}
    require(prov["files"] == expected_files, "payload member hashes")
    env = strict_json(files["environment.json"])
    require(type(env) is dict and set(env) == {"python", "packages", "native_portaudio", "image_os", "image_version"}, "environment schema")
    require(env["python"] == "3.12.16" and type(env["packages"]) is dict
            and type(env["native_portaudio"]) is str and env["native_portaudio"], "environment baseline")
    names = {"pytest", "pytest-timeout", "PyYAML", "sounddevice", "numpy", "onnxruntime", "setuptools"}
    require(set(env["packages"]) == names and all(type(x) is str and x for x in env["packages"].values()), "environment versions")
    require(all(type(env[x]) is str and env[x] for x in ("image_os", "image_version")), "runner image")
    require(env["packages"]["pytest"] == "9.1.1" and env["packages"]["pytest-timeout"] == "2.4.0"
            and env["packages"]["setuptools"] == "83.0.0", "environment pinned tools")
    keys = validate_journal(files["journal.jsonl"], p, k)
    validate_facts(files["facts.json"], p, k)
    require(hashes is not None and variants is not None, "independently derived observation policy required")
    validate_imports(files["imports.json"], p, ctx, k, hashes, variants)
    return keys


def api(endpoint, binary=False):
    require(endpoint.startswith(f"repos/{REPO}/actions/"), "API repository scope")
    result = subprocess.run(["gh", "api", endpoint], capture_output=True)
    require(result.returncode == 0, "GitHub API access")
    return result.stdout if binary else strict_json(result.stdout)


def validate_platform(run, jobs, artifacts, ctx, external=False):
    validate_context(ctx)
    require(type(run) is dict and integer(run.get("id")) and run["id"] == ctx["run_id"], "API run")
    require(integer(run.get("run_attempt")) and run["run_attempt"] == ctx["attempt"], "API attempt")
    require(run.get("head_sha") == ctx["definition_sha"] and run.get("head_branch") == ctx["branch"]
            and run.get("path") == WORKFLOW and run.get("event") == "push", "API definition/path/branch")
    require(run.get("repository", {}).get("full_name") == REPO and integer(run["repository"].get("id"))
            and run["repository"]["id"] > 0, "API repo")
    if external:
        require(run.get("status") == "completed" and run.get("conclusion") == "success", "final run conclusion")
    required = {"release970 plan+policy"} | {f"release970 critical {k}/{SHARDS}" for k in range(1, SHARDS + 1)}
    names = [j.get("name") for j in jobs]
    require(len(names) == len(set(names)) and set(names) == required | {"release970 aggregate"}, "API exact job roles")
    require(len({j.get("id") for j in jobs}) == len(jobs), "API duplicate job IDs")
    for job in jobs:
        require(integer(job.get("id")) and integer(job.get("run_id")) and job["run_id"] == ctx["run_id"], "API job identity")
        require(job.get("head_sha") == ctx["definition_sha"], "API job head")
        if job["name"] in required or external:
            require(job.get("status") == "completed" and job.get("conclusion") == "success", "API job conclusion")
    prefix = f"release970-{ctx['run_id']}-{ctx['attempt']}-"
    expected = {prefix + "plan"} | {prefix + f"shard-{k}" for k in range(1, SHARDS + 1)}
    if external:
        expected.add(prefix + "final")
    cohort = [a for a in artifacts if type(a.get("name")) is str and a["name"].startswith(prefix)]
    require(len(cohort) == len(expected) and {a["name"] for a in cohort} == expected, "API artifact cohort roles")
    require(len({a.get("id") for a in cohort}) == len(cohort), "API artifact duplicate IDs")
    for a in cohort:
        require(integer(a.get("id")) and a["id"] > 0 and integer(a.get("size_in_bytes")) and a["size_in_bytes"] > 0, "API artifact size/ID")
        require(a.get("expired") is False and type(a.get("digest")) is str
                and a["digest"].startswith("sha256:") and HEX64.fullmatch(a["digest"][7:]), "API artifact digest/expiry")
        link = a.get("workflow_run", {})
        require(integer(link.get("id")) and link["id"] == ctx["run_id"]
                and link.get("head_sha") == ctx["definition_sha"] and link.get("head_branch") == ctx["branch"]
                and integer(link.get("repository_id")) and link["repository_id"] == run["repository"]["id"]
                and integer(link.get("head_repository_id")) and link["head_repository_id"] == run["repository"]["id"], "API artifact provenance")
    return {a["name"]: a for a in cohort}


def plan_provenance(p, plan_sha, ctx):
    return {"context": ctx, "candidate": p["candidate"], "tree": p["candidate_tree"],
            "plan_sha256": plan_sha, "hashes": p["hashes"], "planned": 682}


def validate_plan_payload(files, p, plan_sha, ctx):
    require(set(files) == PLAN_PAYLOAD and digest(files["plan.json"]) == plan_sha, "plan artifact bytes")
    require(strict_json(files["plan.json"]) == p, "plan artifact schema/values")
    prov = strict_json(files["provenance.json"])
    validate_context(prov["context"])
    require(prov == plan_provenance(p, plan_sha, ctx) and integer(prov["planned"]), "plan artifact provenance")


def download(a, expected):
    raw = api(f"repos/{REPO}/actions/artifacts/{a['id']}/zip", binary=True)
    require("sha256:" + digest(raw) == a["digest"], "downloaded artifact digest")
    return unzip(raw, expected)


def validate_release_pr(pr, p):
    require(pr.get("headRefOid") == p["candidate"] and pr.get("baseRefOid") == RELEASE_BASE,
            "release PR source changed: re-plan/review required")
    require(pr.get("state") == "OPEN" and pr.get("isDraft") is False and pr.get("mergeable") == "MERGEABLE", "release PR readiness")
    checks = pr.get("statusCheckRollup")
    require(type(checks) is list and checks, "release PR checks missing")
    successful = set()
    skips = []
    for c in checks:
        require(c.get("status") == "COMPLETED", "release PR check incomplete")
        if c.get("conclusion") == "SKIPPED":
            require(c.get("workflowName") == "CI" and c.get("name") in {"mutation (changed lines)", "mutation verdict"}, "release PR unexpected skip")
            skips.append(c["name"])
        else:
            require(c.get("conclusion") == "SUCCESS", "release PR check failed")
            successful.add(c.get("name"))
    require(sorted(skips) == ["mutation (changed lines)", "mutation verdict"], "release PR expected push skips")
    require({"pytest (src/)", "pytest macOS (src/)", "lint", "analyze", "docs-updated", "conventional", "dependency review", "mutation verdict", "swift test (app)", "build (app-ios)", "translation-size", "zizmor (безопасность workflow)", "обезличивание (форматы)", "CodeQL"}
            | {f"mutation (changed lines) ({k})" for k in range(1, 5)} <= successful, "release PR mandatory role missing")


def canonical_command(source, p, k, report):
    expected_keys(p, k)
    return [sys.executable, str(source / "scripts/mutate_check.py"), "--range", p["range"],
            "--only-critical", "--max", "60", "--timeout", "120", "--budget-s", "3000",
            "--shard", f"{k}/{SHARDS}", "--report", str(report)]


def collect(source, p, plan_sha, ctx, out, k):
    preflight(source, p)
    require(not (source / "logs").is_symlink(), "journal root symlink")
    require(not list((source / "logs").glob("mutation_run-*.jsonl")), "fresh journal root")
    out.mkdir(parents=True)
    report = out / "report.txt"
    cmd = canonical_command(source, p, k, report)
    hashes, variants = observation_policy(source, p, k)
    require(not os.environ.get("PYTHONPATH"), "observer requires empty inherited PYTHONPATH")
    require(importlib.util.find_spec("sitecustomize") is None, "observer refuses to shadow an existing startup customizer")
    observer = (ROOT / "scripts/release970_observer.py").read_bytes()
    with tempfile.TemporaryDirectory(prefix="release970-observer-") as temp:
        root = pathlib.Path(temp)
        bootstrap, receipts = root / "bootstrap", root / "receipts"
        bootstrap.mkdir(); receipts.mkdir()
        (bootstrap / "sitecustomize.py").write_bytes(observer)
        config = {"directory": str(receipts), "runtime_root": str(source.resolve()), "policy_pid": os.getpid(),
                  "command": cmd, "hashes": hashes, "variants": variants, "context": ctx, "K": k,
                  "observer_sha256": digest(observer)}
        (root / "config.json").write_text(json.dumps(config) + "\n")
        process = subprocess.Popen(cmd, cwd=source, env={**os.environ, "CHAROITE_ROOT": str(source),
            "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(bootstrap), "CHAROITE970_OBSERVER_CONFIG": str(root / "config.json")})
        rc = process.wait()
        paths = list(receipts.iterdir())
        require(all(x.is_file() and not x.is_symlink() and (x.name == "canonical.pid" or re.fullmatch(r"process-[0-9]+\.json", x.name)) for x in paths), "observer file allowlist")
        paths = [x for x in paths if x.name != "canonical.pid"]
        require(len(paths) <= 64 and sum(x.stat().st_size for x in paths) <= 4_000_000, "observer byte/process limit")
        observed = {"v": 1, "canonical_pid": process.pid, "policy_pid": os.getpid(), "observer_sha256": digest(observer),
                    "records": [strict_json(x.read_bytes()) for x in paths]}
        (out / "imports.json").write_text(json.dumps(observed) + "\n")
    journal = source / "logs" / f"mutation_run-{p['canonical_run_key']}-{k}.jsonl"
    require(set((source / "logs").glob("mutation_run-*.jsonl")) == {journal}, "exact fresh journal file")
    require(all(path.is_file() and not path.is_symlink() for path in
                (journal, report, report.with_name(report.name + ".json"))), "canonical evidence regular files")
    shutil.copyfile(journal, out / "journal.jsonl")
    report.with_name(report.name + ".json").replace(out / "facts.json")
    (out / "environment.json").write_text(json.dumps(environment()) + "\n")
    prov = {"context": ctx, "candidate": p["candidate"], "tree": p["candidate_tree"], "plan_sha256": plan_sha,
            "K": k, "N": SHARDS, "mutator_rc": rc, "hashes": p["hashes"],
            "files": {f.name: digest(f.read_bytes()) for f in out.iterdir()}}
    (out / "provenance.json").write_text(json.dumps(prov) + "\n")
    validate_imports((out / "imports.json").read_bytes(), p, ctx, k, hashes, variants)
    return rc


def aggregate(source, p, plan_sha, ctx, out, external=False, trust=None):
    approval = None
    if external:
        require(type(trust) is dict, "external trusted root approval required")
        approval = validate_approval(ctx, p, plan_sha, **trust)
        require(git(ROOT, "rev-parse", "HEAD") == approval["definition_sha"], "approved definition checkout")
        require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"), "approved definition tracked changes")
    preflight(source, p)
    if external:
        pr = subprocess.run(["gh", "pr", "view", "906", "--repo", REPO, "--json",
                             "headRefOid,baseRefOid,isDraft,state,mergeable,statusCheckRollup"], capture_output=True)
        require(pr.returncode == 0, "release PR API access")
        validate_release_pr(strict_json(pr.stdout), p)
    base = f"repos/{REPO}/actions/runs/{ctx['run_id']}"
    run = api(f"{base}/attempts/{ctx['attempt']}")
    jobs = api(f"{base}/attempts/{ctx['attempt']}/jobs?per_page=100")
    artifacts = api(f"{base}/artifacts?per_page=100")
    require(integer(jobs["total_count"]) and integer(artifacts["total_count"])
            and jobs["total_count"] == len(jobs["jobs"]) and artifacts["total_count"] == len(artifacts["artifacts"]), "API pagination incomplete")
    cohort = validate_platform(run, jobs["jobs"], artifacts["artifacts"], ctx, external)
    out.mkdir(parents=True)
    all_keys = []
    prefix = f"release970-{ctx['run_id']}-{ctx['attempt']}-"
    validate_plan_payload(download(cohort[prefix + "plan"], PLAN_PAYLOAD), p, plan_sha, ctx)
    artifact_facts = []
    for k in range(1, SHARDS + 1):
        a = cohort[prefix + f"shard-{k}"]
        files = download(a, PAYLOAD)
        hashes, variants = observation_policy(source, p, k)
        all_keys.extend(validate_payload(files, p, plan_sha, ctx, k, hashes, variants))
        target = out / "shards" / str(k)
        target.mkdir(parents=True)
        (target / "report.txt.json").write_bytes(files["facts.json"])
        artifact_facts.append({"K": k, "id": a["id"], "digest": a["digest"]})
    require(collections.Counter(all_keys) == collections.Counter(r["key"] for r in p["plan"]), "global exact identity multiset")
    canonical = subprocess.run([sys.executable, str(source / "scripts/mutate_check.py"), "--merge-shards", str(out / "shards"), "--report", str(out / "canonical.txt")], cwd=source)
    require(canonical.returncode == 0, "canonical aggregate")
    result = {"strict": "PASS", "context": ctx, "candidate": p["candidate"], "tree": p["candidate_tree"],
              "plan_sha256": plan_sha, "keys": 682, "uncapped_killed": 682, "canonical_exit": 0,
              "artifacts": artifact_facts, "external_completed_run_verified": False,
              "canonical_files": {"canonical.txt": digest((out / "canonical.txt").read_bytes())}}
    if external:
        final = download(cohort[prefix + "final"], FINAL_PAYLOAD)
        receipt = strict_json(final["strict.json"])
        require(same_typed(receipt, result), "final receipt differs from independent replay")
        require(all(digest(final[name]) == result["canonical_files"][name] for name in result["canonical_files"]), "final canonical evidence")
        result["external_completed_run_verified"] = True
        result["root_approval"] = approval
    excluded = [a["name"] for a in artifacts["artifacts"] if a["name"] not in cohort]
    print(json.dumps({"excluded_artifact_metadata_only": excluded}))
    (out / "strict.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["preflight", "run", "aggregate", "external"])
    ap.add_argument("--source", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--K", type=int)
    ap.add_argument("--context", type=pathlib.Path)
    ap.add_argument("--expected-definition-sha")
    ap.add_argument("--expected-branch")
    ap.add_argument("--approval-record", type=pathlib.Path)
    ap.add_argument("--approval-record-sha256")
    args = ap.parse_args()
    args.source, args.out = args.source.resolve(), args.out.resolve()
    p, plan_sha = load_plan()
    if args.mode == "preflight":
        preflight(args.source, p)
        args.out.mkdir(parents=True)
        shutil.copyfile(ROOT / ".github/release970_plan.json", args.out / "plan.json")
        (args.out / "provenance.json").write_text(json.dumps(plan_provenance(p, plan_sha, context())) + "\n")
        return 0
    ctx = strict_json(args.context.read_bytes()) if args.context else context()
    validate_context(ctx)
    require(git(ROOT, "rev-parse", "HEAD") == ctx["definition_sha"], "definition checkout")
    require(not git(ROOT, "status", "--porcelain", "--untracked-files=no"), "definition tracked changes")
    if args.mode == "run":
        expected_keys(p, args.K)
        return collect(args.source, p, plan_sha, ctx, args.out, args.K)
    trust = None
    if args.mode == "external":
        require(args.approval_record is not None, "external root approval record required")
        trust = {"expected_definition_sha": args.expected_definition_sha,
                 "expected_branch": args.expected_branch,
                 "approval_record": args.approval_record.read_bytes(),
                 "approval_record_sha256": args.approval_record_sha256}
    return aggregate(args.source, p, plan_sha, ctx, args.out, args.mode == "external", trust)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (GateError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"release970 strict FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
