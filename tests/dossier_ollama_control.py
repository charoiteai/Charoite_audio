"""Manual installed-Ollama contract control; never collected by pytest.

Run only after review and the project's external resource gate permits it::

    python -I -B tests/dossier_ollama_control.py PUBLIC_SOURCE NEW_OUTPUT \
        --ollama /path/to/ollama --model-blob /path/to/sha256-HEX \
        --quiet-guard /path/to/quiet_guard.sh --execute

The single input blob must be an already downloaded public GGUF generation
model (at most 4 GiB). No registry is queried, no model is downloaded, and no
owner configuration, graph, or recording is loaded. The original blob is
opened read-only and copied to a separate inode before the server starts.
The installed binary runs with its own HOME/store/config and a macOS sandbox
which denies owner-HOME reads, non-loopback networking, and writes outside
the temporary runtime. A raw TCP tripwire counts remote routing attempts;
it implements no HTTP or model API and supplies no responses.

Manifest format and routing/control semantics were checked against official
Ollama v0.34.4 sources: manifest/{manifest,layer}.go, types/model/config.go,
server/{images,routes,prompt}.go, and envconfig/config.go at
https://github.com/ollama/ollama/tree/v0.34.4 . This control tests prompt
overflow and request flags; it does not prove generation-time shift refusal.
Preparing this file is not an executed installed-server experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


MODEL = "synthetic-contract:probe"
SYSTEM = "This is an invented contract control. Reply with one short word."
TEMPLATE = '{{ range .Messages }}{{ .Role }}: {{ .Content }}\n{{ end }}assistant: '
CONTEXT = 256
MAX_MODEL_BYTES = 4 * 1024**3


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True).encode()


def save(path, value):
    path.write_bytes(json_bytes(value) + b"\n")


def process_receipt(pid):
    """Read the full command of one addressed process, never a system-wide ps."""
    result = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o",
                             "pid=,ppid=,pgid=,stat=,lstart=,command="],
                            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                            capture_output=True, timeout=10, check=False)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    check(result.returncode == 0, "cannot inspect an experiment process")
    parts = result.stdout.decode().strip().split(None, 9)
    check(len(parts) == 10 and int(parts[0]) == pid, "invalid process identity receipt")
    return {"pid": pid, "ppid": int(parts[1]), "pgid": int(parts[2]), "state": parts[3],
            "started": " ".join(parts[4:9]), "command": parts[9]}


def same_process(first, second):
    return second is not None and all(first[key] == second[key] for key in ("pid", "pgid", "started"))


def _stop_owned_processes(server, owned, capture, signal_process, read_process=process_receipt):
    """Stop known identities, but require a complete final descendant capture.

    If the leader has disappeared, old observations cannot establish that an
    unobserved runner is gone. Known children are still stopped; cleanup stays
    unconfirmed rather than making a successful claim about unknown children.
    """
    complete_capture = capture()
    for pid in reversed(owned):
        signal_process(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        live = [pid for pid, identity in owned.items()
                if same_process(identity, current := read_process(pid)) and "Z" not in current["state"]]
        if not live:
            break
        time.sleep(0.1)
    for pid in reversed(owned):
        signal_process(pid, signal.SIGKILL)
    server.wait(timeout=10)
    for pid, identity in owned.items():
        current = read_process(pid)
        check(current is None or (same_process(identity, current) and "Z" in current["state"]),
              "experiment descendant cleanup was not confirmed")
    check(complete_capture is True,
          "server leader disappeared before final capture; unobserved descendants may remain")


class Tripwire:
    """Accept and close TCP connections without implementing an HTTP fake."""

    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.connections = 0
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()

    def watch(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            connection.close()

    def close(self):
        self.stopped.set()
        self.socket.close()
        self.thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_source", type=Path)
    parser.add_argument("new_output", type=Path)
    parser.add_argument("--ollama", required=True, type=Path)
    parser.add_argument("--model-blob", required=True, type=Path)
    parser.add_argument("--quiet-guard", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required after review and an external resource gate")
    check(sys.platform == "darwin", "this control requires the macOS sandbox")
    sandbox = Path("/usr/bin/sandbox-exec")
    check(sandbox.is_file(), "sandbox-exec is unavailable; do not run without it")
    owner_home = Path.home().resolve()
    source, output, binary, blob, guard = (
        path.resolve() for path in (args.public_source, args.new_output,
                                    args.ollama, args.model_blob, args.quiet_guard))
    check(not source.is_relative_to(owner_home), "use a reviewed public source snapshot outside owner HOME")
    check(not output.is_relative_to(owner_home), "use a fresh output directory outside owner HOME")
    check((source / "src/llm.py").is_file(), "public source snapshot has no LLM adapter")
    check(binary.is_file() and os.access(binary, os.X_OK), "installed Ollama binary is unavailable")
    check(guard.is_file() and os.access(guard, os.X_OK), "an executable project resource guard is required")
    check(re.fullmatch(r"sha256-[0-9a-f]{64}", blob.name) is not None,
          "model input must be one explicitly selected content-addressed public blob")
    check(blob.is_file() and 0 < blob.stat().st_size <= MAX_MODEL_BYTES,
          "select one already downloaded generation GGUF no larger than 4 GiB")
    output.mkdir(parents=False, exist_ok=False)
    runtime = Path(tempfile.mkdtemp(prefix="charoite-ollama-contract-", dir="/tmp")).resolve()
    report = {"executed": True, "passed": False, "checks": {}, "guards": [],
              "limitations": ["Generation-time context shifting is not exercised.",
                              "This server control does not test dossier model quality."],
              "runtime": str(runtime)}
    server = tripwire = None
    copied_blob = None
    original_send = None
    guard_env = dict(os.environ)
    owned = {}
    receipts = []

    def remember_children():
        if server is None:
            return False
        root = process_receipt(server.pid)
        if root is None:
            return False
        if server.pid not in owned:
            check(root["ppid"] == os.getpid() and root["pgid"] == server.pid,
                  "server PID does not belong to this experiment")
        else:
            check(same_process(owned[server.pid], root), "server PID identity changed")
        # An anchored, still-identical session leader also identifies children
        # reparented after a crash. This enumerates only our private PGID.
        group = subprocess.run(["/usr/bin/pgrep", "-g", str(root["pgid"])],
                               env={"PATH": "/usr/bin:/bin"}, capture_output=True,
                               timeout=10, check=False)
        check(group.returncode in (0, 1), "cannot inspect the experiment process group")
        pending = [root]
        for member_pid in group.stdout.split():
            member = process_receipt(int(member_pid))
            if member is not None and member["pgid"] == root["pgid"]:
                pending.append(member)
        observed = set()
        while pending:
            current = pending.pop()
            if current["pid"] in observed:
                continue
            observed.add(current["pid"])
            previous = owned.get(current["pid"])
            check(previous is None or same_process(previous, current), "descendant PID identity changed")
            owned[current["pid"]] = current
            receipts.append({"phase": "observe", **current})
            children = subprocess.run(["/usr/bin/pgrep", "-P", str(current["pid"])],
                                      env={"PATH": "/usr/bin:/bin"}, capture_output=True,
                                      timeout=10, check=False)
            check(children.returncode in (0, 1), "cannot inspect experiment descendants")
            for child_pid in children.stdout.split():
                child = process_receipt(int(child_pid))
                if child is not None and child["ppid"] == current["pid"]:
                    pending.append(child)
        return True

    def signal_owned(pid, signum):
        current = process_receipt(pid)  # Full command is read immediately before every signal.
        if current is None:
            return
        receipts.append({"phase": "before-" + signal.Signals(signum).name, **current})
        check(same_process(owned[pid], current), "refusing to signal a reused experiment PID")
        check(current["command"] == owned[pid]["command"], "refusing to signal a changed process command")
        if "Z" not in current["state"]:
            try:
                os.kill(pid, signum)
            except ProcessLookupError:
                pass

    def gate(step):
        result = subprocess.run([str(guard)], env=guard_env, cwd=source,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=60, check=False)
        report["guards"].append({"step": step, "returncode": result.returncode})
        save(output / "result.json", report)
        check(result.returncode == 0, f"resource guard refused {step}: exit {result.returncode}")

    try:
        gate("prepare-and-start-isolated-server")
        with blob.open("rb") as stream:
            check(stream.read(4) == b"GGUF", "the selected blob is not GGUF")
        before = blob.stat()
        digest = sha256(blob)
        check(blob.name == "sha256-" + digest, "source blob digest does not match its content address")
        models = runtime / "models"
        blobs = models / "blobs"
        blobs.mkdir(parents=True)
        copied_blob = blobs / blob.name
        shutil.copyfile(blob, copied_blob)
        copied_blob.chmod(0o444)
        check((copied_blob.stat().st_dev, copied_blob.stat().st_ino) != (before.st_dev, before.st_ino),
              "the model copy must have a separate inode")
        check(sha256(copied_blob) == digest, "isolated model copy digest mismatch")
        for name in ("home", "tmp", "data", "config", "cache"):
            (runtime / name).mkdir()
        (runtime / "home/.ollama").mkdir()
        # Cloud routing is allowed only to our loopback tripwire. The native
        # sandbox independently prohibits all external network connections.
        save(runtime / "home/.ollama/server.json", {"disable_ollama_cloud": False})
        tripwire = Tripwire()
        check(tripwire.port != 11434, "production port may not be used")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        check(port != 11434, "production port may not be used")
        base = f"http://127.0.0.1:{port}"
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(runtime / "home"), "TMPDIR": str(runtime / "tmp"),
            "XDG_CONFIG_HOME": str(runtime / "config"), "XDG_CACHE_HOME": str(runtime / "cache"),
            "CHAROITE_ROOT": str(runtime / "data"), "CHAROITE_NO_CLOUD": "1",
            "OLLAMA_HOST": base, "OLLAMA_MODELS": str(models),
            "OLLAMA_REMOTES": "127.0.0.1", "OLLAMA_NO_CLOUD": "0",
            "OLLAMA_NOPRUNE": "1", "OLLAMA_NOHISTORY": "1",
            "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_CONTEXT_LENGTH": str(CONTEXT), "OLLAMA_GO_TEMPLATE": "1",
            "OLLAMA_KEEP_ALIVE": "5m", "OLLAMA_LOAD_TIMEOUT": "120s",
            "NO_PROXY": "*", "no_proxy": "*",
        }
        os.environ.clear()
        os.environ.update(environment)
        os.chdir(runtime)

        def layer(data, media_type):
            payload = data if isinstance(data, bytes) else json_bytes(data)
            address = "sha256:" + hashlib.sha256(payload).hexdigest()
            (blobs / address.replace(":", "-")).write_bytes(payload)
            return {"mediaType": media_type, "digest": address, "size": len(payload)}

        config = {"model_format": "gguf", "capabilities": ["completion"]}
        local_manifest = {"schemaVersion": 2,
                          "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                          "config": layer(config, "application/vnd.docker.container.image.v1+json"),
                          "layers": [{"mediaType": "application/vnd.ollama.image.model",
                                      "digest": "sha256:" + digest, "size": before.st_size},
                                     layer(TEMPLATE.encode(), "application/vnd.ollama.image.template")]}
        manifest_path = models / "manifests/registry.ollama.ai/library/synthetic-contract/probe"
        manifest_path.parent.mkdir(parents=True)

        def write_manifest(manifest, label):
            payload = json_bytes(manifest)
            pending = manifest_path.with_name("probe.pending")
            pending.write_bytes(payload)
            pending.replace(manifest_path)
            (output / f"manifest-{label}.json").write_bytes(payload)
            report[label + "_manifest_sha256"] = hashlib.sha256(payload).hexdigest()

        write_manifest(local_manifest, "local")
        save(output / "model-config-local.json", config)
        save(output / "environment.json", environment)
        report.update(model_blob_sha256=digest, model_bytes=before.st_size,
                      binary_sha256=sha256(binary), source_llm_sha256=sha256(source / "src/llm.py"))
        # The original model path is deliberately absent from the child profile:
        # its only model is the independent temporary copy.
        quote = lambda path: json.dumps(str(path))
        profile = ("(version 1)\n(allow default)\n"
                   "(deny network*)\n"
                   '(allow network-inbound (local ip "127.0.0.1:*"))\n'
                   '(allow network-outbound (require-all (remote ip "127.0.0.1:*") '
                   '(require-not (remote ip "127.0.0.1:11434"))))\n'
                   f"(deny file-read* (subpath {quote(owner_home)}))\n"
                   f"(allow file-read* (subpath {quote(runtime)}))\n"
                   "(deny file-write*)\n"
                   f"(allow file-write* (subpath {quote(runtime)}) (literal \"/dev/null\"))\n")
        (runtime / "server.sb").write_text(profile)
        # Do not copy the profile to public artifacts: it contains owner HOME.
        gate("start-isolated-server")
        with (runtime / "ollama.log").open("wb") as log:
            server = subprocess.Popen([str(sandbox), "-f", str(runtime / "server.sb"), str(binary), "serve"],
                                      env=environment, cwd=runtime, stdin=subprocess.DEVNULL,
                                      stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        remember_children()
        sys.dont_write_bytecode = True

        def audit(event, values):
            if event in {"open", "os.listdir", "os.scandir"} and values:
                raw = values[0]
                path = Path(os.fsdecode(raw)).absolute() if isinstance(raw, (str, bytes)) else None
                if path is not None and path.is_relative_to(owner_home):
                    allowed = (runtime, output, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
                    # The selected public blob can be rehashed, never written.
                    blob_read = (path == blob and event == "open" and len(values) > 2
                                 and values[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) == 0)
                    check(blob_read or any(path.is_relative_to(item) for item in allowed),
                          "owner HOME access refused")
            if event == "socket.connect":
                address = values[1]
                check(isinstance(address, tuple) and address[:2] == ("127.0.0.1", port),
                      "connection outside the isolated server refused")
            if event == "socket.getaddrinfo":
                check(values[0] == "127.0.0.1" and str(values[1]) == str(port),
                      "address lookup outside the isolated server refused")

        sys.addaudithook(audit)
        sys.path.insert(0, str(source / "src"))
        import requests
        from llm import LLM, LLMHTTPError

        original_send = requests.sessions.Session.send
        wire = []

        def measured_send(session, request, **kwargs):
            parsed = urlsplit(request.url)
            check(parsed.hostname == "127.0.0.1" and parsed.port == port,
                  "transport must target only the isolated server")
            started = time.monotonic()
            response = original_send(session, request, **kwargs)
            # Materializing a real response records the wire bytes. It does not
            # supply a fake response or replace any server-side mechanism.
            payload = request.body.decode() if isinstance(request.body, bytes) else request.body
            row = {"path": parsed.path, "method": request.method,
                   "request": json.loads(payload) if payload else None,
                   "status": response.status_code, "response": response.content.decode("utf-8", "replace"),
                   "wall_s": round(time.monotonic() - started, 3)}
            wire.append(row)
            with (output / "http.jsonl").open("ab") as journal:
                journal.write(json_bytes(row) + b"\n")
            remember_children()
            return response

        requests.sessions.Session.send = measured_send
        session = requests.Session()
        session.trust_env = False

        def request(path, payload=None):
            if path == "/api/chat":
                gate("chat-or-render")
            return session.request("GET" if payload is None else "POST", base + path,
                                   json=payload, timeout=(3, 180), allow_redirects=False)

        deadline = time.monotonic() + 30
        while True:
            check(server.poll() is None, "isolated Ollama exited during startup; inspect ollama.log")
            try:
                response = request("/api/version")
                if response.status_code == 200:
                    report["server_version"] = response.json()
                    response.close()
                    break
                response.close()
            except requests.ConnectionError:
                pass
            check(time.monotonic() < deadline, "isolated Ollama startup timed out")
            time.sleep(0.1)
        listener = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(server.pid),
                                   f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                  env=environment, cwd=runtime, capture_output=True, timeout=10, check=False)
        check(listener.returncode == 0 and str(server.pid) in listener.stdout.decode().split(),
              "the isolated child does not own the selected listening port")
        report["checks"]["server_owns_port"] = True
        config_app = {"llm": {"engine": "ollama", "base_url": base, "model": MODEL,
                               "num_ctx": CONTEXT, "cloud_fallback_local": False, "allow_remote": False},
                      "sufler": {"role": SYSTEM}}
        client = LLM(config_app, keep_alive="5m")
        selected = client.require_local_ollama_model(MODEL)
        check(selected == MODEL + ":local", "production adapter did not select :local")
        save(output / "adapter-config.json", config_app)
        gate("production-adapter-local-pass")
        local_text = "".join(client.stream("Say OK.", model=selected, system=SYSTEM,
                                         num_predict=8, temperature=0, busy_wait=0,
                                         timeout=(3, 180), truncate=False, shift=False))
        check(local_text.strip(), "local adapter returned no generated text")
        sent = wire[-1]["request"]
        check(wire[-1]["path"] == "/api/chat" and sent["model"] == selected
              and sent.get("truncate") is False and sent.get("shift") is False,
              "the real adapter request did not carry the required local/no-truncation controls")
        report["checks"]["local_pass"] = True
        (output / "local-response.txt").write_text(local_text)

        # Both explicit controls use identical invented messages and options.
        # Only truncate changes. Debug rendering proves which input is lost;
        # actual completion proves overflow 400 versus successful truncation.
        sentinel = "SYNTHETIC_OLD_MESSAGE_SENTINEL"
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": sentinel + " alpha beta gamma delta" * 2048},
                    {"role": "assistant", "content": "Acknowledged."},
                    {"role": "user", "content": "Say OK."}]
        payload = {"model": selected, "messages": messages, "stream": False, "think": False,
                   "truncate": False, "shift": False, "keep_alive": "5m",
                   "options": {"num_ctx": CONTEXT, "num_predict": 1, "temperature": 0, "seed": 1}}
        rendered = []
        for truncate in (False, True):
            with request("/api/chat", {**payload, "truncate": truncate, "_debug_render_only": True}) as response:
                check(response.status_code == 200, "debug rendering did not succeed")
                rendered.append(response.json().get("_debug_info", {}).get("rendered_template", ""))
        check(sentinel in rendered[0] and sentinel not in rendered[1] and rendered[1],
              "the truncate control did not demonstrably remove the old synthetic message")
        with request("/api/chat", payload) as response:
            body = response.json()
            check(response.status_code == 400 and re.search(r"context|input.*(long|length)", body.get("error", ""), re.I),
                  "truncate=false/shift=false did not produce an explicit context HTTP 400")
        report["checks"]["overflow_400"] = True
        with request("/api/chat", {**payload, "truncate": True}) as response:
            body = response.json()
            check(response.status_code == 200 and body.get("done") is True and not body.get("error"),
                  "same-input truncate=true control did not complete")
        report["checks"]["same_input_truncation_control"] = True

        # Replace only our temporary tag, after a real local request passed.
        remote_config = {**config, "remote_host": f"http://127.0.0.1:{tripwire.port}",
                         "remote_model": "synthetic-remote:probe"}
        remote_manifest = {**local_manifest,
                           "config": layer(remote_config, "application/vnd.docker.container.image.v1+json"),
                           "layers": []}
        save(output / "model-config-remote.json", remote_config)
        write_manifest(remote_manifest, "remote")
        before_remote = tripwire.connections
        gate("production-adapter-retag-refusal")
        try:
            list(client.stream("Say OK.", model=selected, system=SYSTEM,
                               num_predict=1, temperature=0, busy_wait=0,
                               timeout=(3, 30), truncate=False, shift=False))
        except LLMHTTPError as exc:
            check(exc.status == 404 and "not found" in str(exc), "retag refused for an unexpected reason")
        else:
            raise RuntimeError("remote retag was accepted by :local")
        check(tripwire.connections == before_remote, ":local retag request reached the remote tripwire")
        report["checks"]["retag_refused_before_forward"] = True
        with request("/api/chat", {**payload, "model": MODEL,
                                    "messages": [{"role": "user", "content": "Say OK."}]}) as response:
            response.content
        check(tripwire.connections > before_remote, "ordinary remote tag did not reach the TCP negative control")
        report["checks"]["remote_route_negative_control"] = True
        report["remote_tcp_connections"] = tripwire.connections
        after = blob.stat()
        check((before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) ==
              (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev), "source model metadata changed")
        check(sha256(blob) == digest, "source model content changed")
        report["checks"]["source_blob_unchanged"] = True
        check(sha256(source / "src/llm.py") == report["source_llm_sha256"], "source adapter changed during the experiment")
        report["checks"]["source_adapter_unchanged"] = True
        report["passed"] = True
    except BaseException as exc:
        detail = str(exc)
        for path in (owner_home, source, binary, blob, guard):
            detail = detail.replace(str(path), "<input-path>")
        report["error"] = {"type": type(exc).__name__, "detail": detail}
        raise
    finally:
        if original_send is not None:
            requests.sessions.Session.send = original_send
        cleanup_ok = True
        if server is not None:
            try:
                # Descendants were observed through this server's ancestry.
                # Signal individual verified identities; never signal a PGID.
                _stop_owned_processes(server, owned, remember_children, signal_owned)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
                cleanup_ok = False
                report["cleanup_error"] = type(exc).__name__ + ": " + str(exc)
            report["server_returncode"] = server.returncode
            report["checks"]["own_processes_stopped"] = cleanup_ok
            save(output / "process-receipts.json", receipts)
        if tripwire is not None:
            tripwire.close()
        if (runtime / "ollama.log").is_file():
            shutil.copyfile(runtime / "ollama.log", output / "ollama.log")
        if cleanup_ok and copied_blob is not None and copied_blob.exists():
            copied_blob.unlink()  # Only the exact separate-inode copy created above.
        report["passed"] = report["passed"] and cleanup_ok
        save(output / "result.json", report)
        print(json.dumps({"passed": report["passed"], "checks": report["checks"],
                          "limitations": report["limitations"]}, ensure_ascii=True))
        if not cleanup_ok:
            raise RuntimeError("experiment process cleanup is unconfirmed; inspect process-receipts.json")


if __name__ == "__main__":
    main()
