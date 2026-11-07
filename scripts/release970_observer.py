"""Passive startup observer copied alone to an isolated sitecustomize directory."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time


def _start():
    location = os.environ.get("CHAROITE970_OBSERVER_CONFIG")
    if not location:
        return
    here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or os.curdir).resolve() != here]
    cfg = json.loads(Path(location).read_text(encoding="utf-8"))
    directory = Path(cfg["directory"])
    pid, parent = os.getpid(), os.getppid()
    argv = list(sys.orig_argv)
    marker = directory / "canonical.pid"
    canonical = (argv[1:] == cfg["command"][1:] and sys.executable == cfg["command"][0]
                 and parent == cfg["policy_pid"])
    direct = (marker.is_file() and parent == int(marker.read_text())
              and argv[1:4] == ["-B", "-m", "pytest"] and sys.executable == cfg["command"][0])
    # Other fixture subprocesses are outside this observation's attestation.
    if not canonical and not direct:
        return
    start = time.monotonic_ns()
    cwd = Path.cwd().resolve()
    record = {"v": 1, "pid": pid, "parent": parent, "role": "canonical" if canonical else "pytest",
              "cwd": str(cwd), "argv": argv, "executable": sys.executable, "started_ns": start, "elapsed_ns": 0,
              "head": "", "tree": "",
              "key": None, "snapshot": {}, "origins": [], "errors": [], "context": cfg["context"],
              "K": cfg["K"], "observer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    def error(reason):
        if len(record["errors"]) < 16:
            record["errors"].append(reason)

    if record["observer_sha256"] != cfg["observer_sha256"]:
        error("observer source differs")
    if canonical:
        with marker.open("x") as f:
            f.write(str(pid))
    expected = cfg["hashes"]
    seen = set()
    core_names = {Path(p).name for p in expected if not p.startswith("tests/")}
    try:
        record["head"], record["tree"] = subprocess.check_output(
            ["git", "-C", str(cwd), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
        record["snapshot"] = {p: hashlib.sha256((cwd / p).read_bytes()).hexdigest() for p in expected}
        if canonical:
            if cwd != Path(cfg["runtime_root"]) or record["snapshot"] != expected:
                error("canonical root/hash")
        else:
            changed = {p: h for p, h in record["snapshot"].items() if h != expected[p]}
            if changed:
                matches = [key for key, item in cfg["variants"].items() if changed == {item["path"]: item["sha256"]}]
                if len(matches) != 1:
                    error("unknown/ambiguous mutant bytes")
                else:
                    record["key"] = matches[0]
            if list(cwd.rglob("__pycache__")):
                error("primary checkout contains bytecode cache")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        error(type(exc).__name__)

    def observe(event, args):
        if event != "exec" or args[0].co_name != "<module>":
            return
        try:
            name = args[0].co_filename
            if name.startswith("<"):
                return
            origin = Path(name).resolve()
            if origin.is_relative_to(cwd):
                rel = origin.relative_to(cwd).as_posix()
                if rel not in expected:
                    if origin.name in core_names:
                        error("unapproved same-name source")
                    return
            elif origin.name in core_names:
                error("foreign same-name source")
                return
            else:
                return
            sha = hashlib.sha256(origin.read_bytes()).hexdigest()
            identity = (str(origin), sha)
            if identity not in seen:
                if len(seen) >= 256:
                    error("origin limit")
                    return
                seen.add(identity)
                record["origins"].append({"path": str(origin), "relative": rel, "sha256": sha})
        except Exception as exc:  # noqa: BLE001 — observation must never change canonical exec/rc
            error(type(exc).__name__)

    def finish():
        record["elapsed_ns"] = time.monotonic_ns() - start
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        if len(raw) > 100_000:
            return  # Missing receipt is a strict failure, never a canonical error.
        target = directory / f"process-{pid}.json"
        with target.open("xb") as f:
            f.write(raw)

    atexit.register(finish)
    sys.addaudithook(observe)


_start()
