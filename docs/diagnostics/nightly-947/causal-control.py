"""Compare exact archived source with early measured network/process denial."""
from __future__ import annotations

import argparse
import ctypes
import errno
import gzip
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


class BoundaryViolation(BaseException):
    """Cannot be swallowed by a target's ordinary Exception handler."""


class IntegrityViolation(ValueError):
    """The declared source object is not the object being executed."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def blob_oid(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def require(condition, why):
    if not condition:
        raise IntegrityViolation(why)


def bundle_files(raw, manifest, expected_sha):
    require(manifest["commit_sha"] == expected_sha, "wrong expected source SHA")
    require(sha256(raw) == manifest["archive_sha256"], "archive digest differs")
    result = {}
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(raw)), mode="r:") as archive:
        require(archive.pax_headers.get("comment") == expected_sha, "Git archive identity differs")
        for member in archive.getmembers():
            rel = Path(member.name)
            require(not rel.is_absolute() and ".." not in rel.parts and ".git" not in rel.parts,
                    "unsafe archive path")
            require(rel.parts[0] in {"src", "scripts"}, "unexpected source scope")
            if member.isdir():
                continue
            require(member.isfile() and member.name not in result, "nonregular/duplicate archive entry")
            require(member.name in manifest["entries"], "unmanifested archive entry")
            data = archive.extractfile(member).read()
            check_bytes(data, manifest["entries"][member.name])
            result[member.name] = data
    require(set(result) == set(manifest["entries"]), "incomplete archive")
    return result


def check_bytes(data, entry):
    require(len(data) == entry["size"] and sha256(data) == entry["sha256"]
            and blob_oid(data) == entry["git_blob_oid"], "source byte identity differs")


def verify_tree(root, manifest):
    files = {}
    for path in root.rglob("*"):
        require(not path.is_symlink(), "source symlink is forbidden")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path
    require(set(files) == set(manifest["entries"]), "untracked/shadow/missing source file")
    for rel, path in files.items():
        check_bytes(path.read_bytes(), manifest["entries"][rel])


def install_boundary():
    state = {"phase": "self_test", "events": {}}

    def deny(event):
        phase = state["phase"]
        counts = state["events"].setdefault(phase, {})
        counts[event] = counts.get(event, 0) + 1
        raise BoundaryViolation(event)

    def audit(event, args):
        if event.startswith("socket."):
            deny("audit_socket")
        if event in {"subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.posix_spawn",
                     "os.spawn", "os.exec", "pty.spawn"}:
            deny("audit_process")

    sys.addaudithook(audit)
    # urllib3 otherwise opens an IPv6 socket during import. Offline fixture only.
    socket.has_ipv6 = False
    state["capability_overrides"] = {"socket.has_ipv6": socket.has_ipv6}

    def blocked(*args, **kwargs):
        deny("socket_api")

    for name in ["create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                 "gethostbyaddr", "getnameinfo"]:
        setattr(socket, name, blocked)
    for name in ["connect", "connect_ex", "send", "sendall", "sendto", "sendmsg", "bind", "listen", "accept"]:
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, blocked)

    probes = {
        "socket_creation": lambda: socket.socket(),
        "create_connection": lambda: socket.create_connection(("127.0.0.1", 9)),
        "connect_ex": lambda: socket.socket.connect_ex(None, ("127.0.0.1", 9)),
        "sendto": lambda: socket.socket.sendto(None, b"synthetic", ("127.0.0.1", 9)),
        "name_resolution": lambda: socket.getaddrinfo("example.invalid", 9),
        "child_process": lambda: subprocess.run([sys.executable, "-c", "pass"], check=True),
        "shell_process": lambda: os.system(":"),
    }
    passed = []
    for name, probe in probes.items():
        try:
            probe()
        except BoundaryViolation:
            passed.append(name)
        else:
            raise AssertionError("boundary negative probe escaped: " + name)
    state["negative_probes"] = passed
    return state


def verify_os_network_denial(profile):
    expected = "(version 1)\n(allow default)\n(deny network*)\n(deny process-fork)\n"
    require(profile.read_text() == expected, "unexpected OS boundary profile")
    require(sys.platform == "darwin", "this boundary proof requires Darwin")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.socket.argtypes, libc.socket.restype = [ctypes.c_int, ctypes.c_int, ctypes.c_int], ctypes.c_int
    libc.connect.argtypes, libc.connect.restype = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int
    attempts = 0
    ctypes.set_errno(0)
    attempts += 1
    fd = libc.socket(2, 1, 0)
    operation, result, code = "socket", fd, ctypes.get_errno()
    if fd >= 0:
        try:
            address = ctypes.create_string_buffer(bytes([16, 2, 0, 9, 127, 0, 0, 1] + [0] * 8))
            ctypes.set_errno(0)
            attempts += 1
            operation, result = "connect", libc.connect(fd, address, 16)
            code = ctypes.get_errno()
        finally:
            os.close(fd)
    require(result == -1 and code == errno.EPERM, "OS network denial was not observed")
    return {"profile_sha256": sha256(profile.read_bytes()), "operation_denied": operation,
            "syscall_attempts": attempts, "observed_errno": code, "denial_observed": result == -1}


class VerifiedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name, path, expected, observed):
        super().__init__(name, str(path))
        self.expected, self.observed = expected, observed

    def get_code(self, fullname):
        data = Path(self.path).read_bytes()
        if "git_blob_oid" in self.expected:
            check_bytes(data, self.expected)
        else:
            require(sha256(data) == self.expected["sha256"], "dependency bytes changed before execution")
        self.observed[fullname] = self.expected
        return compile(data, self.path, "exec", dont_inherit=True)


class ImportBoundary(importlib.abc.MetaPathFinder):
    def __init__(self, root, manifest, dependencies):
        self.root, self.manifest, self.dependencies = root, manifest, dependencies
        self.target_names = {Path(p).parts[1].removesuffix(".py") for p in manifest["entries"]
                             if p.startswith("src/")}
        self.target, self.external = {}, {}
        self.stdlib = Path(sysconfig.get_path("stdlib")).resolve()

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.origin in {None, "built-in", "frozen"}:
            return spec
        origin = Path(spec.origin).resolve()
        if fullname.split(".")[0] in self.target_names:
            require(origin.is_relative_to(self.root), "target module shadowed outside the archive")
        if origin.is_relative_to(self.root):
            rel = origin.relative_to(self.root).as_posix()
            require(rel in self.manifest["entries"] and origin.suffix == ".py", "unmanifested target import")
            entry = {"path": rel, **self.manifest["entries"][rel]}
            spec.loader = VerifiedLoader(fullname, origin, entry, self.target)
        elif origin.is_relative_to(self.dependencies):
            rel = origin.relative_to(self.dependencies).as_posix()
            entry = {"path": rel, "sha256": sha256(origin.read_bytes())}
            self.external[fullname] = entry
            if origin.suffix == ".py":
                spec.loader = VerifiedLoader(fullname, origin, entry, self.external)
        else:
            require(origin.is_relative_to(self.stdlib) and "site-packages" not in origin.parts,
                    "import outside approved source/dependency/stdlib roots")
        return spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--sandbox-profile", type=Path, required=True)
    args = parser.parse_args()
    require(sys.flags.isolated and sys.flags.no_site, "run this control with -I -S")
    sys.dont_write_bytecode = True
    boundary = install_boundary()  # Before every target and third-party import.
    os_boundary = verify_os_network_denial(args.sandbox_profile)
    raw, manifest = args.archive.read_bytes(), json.loads(args.manifest.read_text())
    negative_integrity = []
    try:
        bundle_files(raw, manifest, "0" * 40)
    except IntegrityViolation:
        negative_integrity.append("wrong_expected_sha")
    else:
        raise AssertionError("wrong expected SHA was admitted")
    files = bundle_files(raw, manifest, args.expected_sha)
    with tempfile.TemporaryDirectory(prefix="nightly-947-archive-") as own:
        root = (Path(own) / "source").resolve()
        for rel, data in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        shadow = root / "src/_unexpected_shadow_947.py"
        shadow.write_text("raise AssertionError('shadow must never be imported')\n")
        try:
            verify_tree(root, manifest)
        except IntegrityViolation:
            negative_integrity.append("untracked_shadow_file")
        else:
            raise AssertionError("shadow file was admitted")
        shadow.unlink()
        sample = root / "scripts/nightly_dossier.py"
        good = sample.read_bytes()
        sample.write_bytes(good + b"\n")
        try:
            verify_tree(root, manifest)
        except IntegrityViolation:
            negative_integrity.append("modified_source_blob")
        else:
            raise AssertionError("modified source was admitted")
        sample.write_bytes(good)
        verify_tree(root, manifest)
        os.environ["CHAROITE_ROOT"] = str(Path(own) / "data")
        dependencies = args.dependencies.resolve()
        require(dependencies.is_dir(), "dependency root is missing")
        # -I -S omits CWD, script directory, .pth and sitecustomize processing.
        sys.path[:0] = [str(root / "src"), str(dependencies)]
        imports = ImportBoundary(root, manifest, dependencies)
        sys.meta_path.insert(0, imports)
        boundary["phase"] = "target_import"
        import llm
        from charoite_graph import dossier
        from charoite_schema import CHAROITE
        counts = {"constructors": 0, "stream_entries": 0, "contract_checks": 0}
        body = "## Сейчас\nInvented fact.\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"

        class Adapter:
            cloud_ready, engine, base, _fell_back_local = False, "ollama", "http://127.0.0.1:13701", False

            def __init__(self, config):
                counts["constructors"] += 1

            def require_local_ollama_model(self, model):
                counts["contract_checks"] += 1
                return model + ":local"

            def stream(self, prompt, **kwargs):
                counts["stream_entries"] += 1
                yield body

        llm.LLM = Adapter
        rel = "scripts/nightly_dossier.py"
        loader = VerifiedLoader("nightly_947", root / rel, {"path": rel, **manifest["entries"][rel]}, imports.target)
        spec = importlib.util.spec_from_loader("nightly_947", loader)
        nd = importlib.util.module_from_spec(spec)
        loader.exec_module(nd)
        results = []
        boundary["phase"] = "target_run"
        for name, text, context in [("short_8192", "Invented short source.", 8192),
                                    ("long_8192", "Синтетический факт о вымышленном датчике. " * 200, 8192),
                                    ("long_32768", "Синтетический факт о вымышленном датчике. " * 200, 32768)]:
            for key in counts:
                counts[key] = 0
            sources = {"2099-01-01_100000": {"kind": "Встречи", "text": text}}
            prompt = dossier.build_prompt("Glasshouse", list(sources), sources, schema=CHAROITE)
            config = {"llm": {"engine": "ollama", "model": "invented-synthetic-model", "num_ctx": context,
                               "base_url": Adapter.base}, "sufler": {"role": "Invented fixture only."}}
            outcome, reason = "admitted_to_adapter_double", None
            try:
                assert nd.generate("Glasshouse", list(sources), sources, config) == body
            except ValueError as exc:
                outcome = "refused_before_stream" if counts["stream_entries"] == 0 else "failed_after_stream"
                reason = "context_budget" if "context budget" in str(exc) else type(exc).__name__
            results.append({"case": name, "input_sha256": sha256(text.encode()), "context": context,
                            "prompt_bytes": len(prompt.encode()), "prompt_sha256": sha256(prompt.encode()),
                            "complete_normalized_source_present_in_prompt": text.strip() in prompt,
                            "config_sha256": sha256(json.dumps(config, sort_keys=True).encode()),
                            "outcome": outcome, "reason_code": reason, **counts})
        target_events = sum(sum(v.values()) for k, v in boundary["events"].items() if k != "self_test")
        require(target_events == 0, "unexpected target boundary event")
        verify_tree(root, manifest)
        for record in imports.external.values():
            require(sha256((dependencies / record["path"]).read_bytes()) == record["sha256"],
                    "dependency bytes changed after execution")
        stdlib_files, builtin_frozen = {}, []
        for name, module in tuple(sys.modules.items()):
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if origin in {"built-in", "frozen"}:
                builtin_frozen.append(name)
            elif origin:
                path = Path(origin).resolve()
                if path.is_file() and path.is_relative_to(imports.stdlib) and "site-packages" not in path.parts:
                    stdlib_files[name] = {"path": path.relative_to(imports.stdlib).as_posix(),
                                          "sha256": sha256(path.read_bytes())}
        require(socket.has_ipv6 is False, "offline capability fixture changed")
        incremental = "src/dossier_incremental.py"
        incremental_present = incremental in manifest["entries"]
        require(not incremental_present or any(v["path"] == incremental for v in imports.target.values()),
                "incremental source was not bound in the import closure")
        receipt = {"source_sha": args.expected_sha, "archive_sha256": sha256(raw),
                   "source_manifest_sha256": sha256(args.manifest.read_bytes()),
                   "verified_archive_files": len(files), "source_tree_verified_before_and_after": True,
                   "control_sha256": sha256(Path(__file__).read_bytes()),
                   "interpreter": {"version": list(sys.version_info[:3]), "cache_tag": sys.implementation.cache_tag,
                                   "executable_sha256": sha256(Path(sys.executable).resolve().read_bytes()),
                                   "isolated": bool(sys.flags.isolated), "no_site": bool(sys.flags.no_site)},
                   "boundary": {"python_negative_probes": boundary["negative_probes"],
                                "capability_overrides": boundary["capability_overrides"],
                                "observed_event_counts": boundary["events"], "unexpected_target_events": target_events,
                                "os_network_probe": os_boundary},
                   "integrity_negative_probes": negative_integrity,
                   "executed_target_sources": imports.target, "executed_external_dependencies": imports.external,
                   "loaded_stdlib_files": stdlib_files, "builtin_frozen_modules": sorted(builtin_frozen),
                   "incremental_source_in_archive": incremental_present, "cases": results,
                   "scope": "archive-bound generation function and adapter double; no graph publication/model experiment"}
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
