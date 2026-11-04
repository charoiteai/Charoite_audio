"""Replay invented audit prompts in order through the real local adapter.

Manual diagnostic only: PUBLIC_SOURCE OUTPUT_DIR PROMPT [PROMPT ...].
Use external quiet/heavy gates. A replay never replaces the acceptance matrix.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    args = sys.argv[1:]
    think = bool(args and args[0] == "--think")
    if think:
        args = args[1:]
    source, output, *prompts = map(lambda p: Path(p).resolve(), args)
    num_predict = 2000 if think else 350
    home = Path.home().resolve()
    if any(p.is_relative_to(home) for p in (source, output, *prompts)):
        raise SystemExit("public source, invented prompts and output must be outside the owner home")
    output.mkdir(parents=True, exist_ok=False)
    runtime = Path(tempfile.mkdtemp(prefix="charoite843-audit-replay-"))
    os.environ.update(CHAROITE_ROOT=str(runtime / "data"), CHAROITE_NO_CLOUD="1",
                      XDG_CONFIG_HOME=str(runtime / "config"), XDG_CACHE_HOME=str(runtime / "cache"))
    os.chdir(runtime)
    control = Path(__file__).resolve()
    allowed = (source, output, runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    events, calls = [], []

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir"} and args:
            raw = args[0]
            path = Path(os.fsdecode(raw)).absolute() if isinstance(raw, (str, bytes)) else None
            if path is not None and path.is_relative_to(home) and path != control and not any(path.is_relative_to(p) for p in allowed):
                events.append("owner-path")
                raise PermissionError("owner path refused")
        if event == "socket.connect":
            address = args[1]
            if not (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434):
                events.append("network")
                raise PermissionError("non-model network refused")
        if event == "socket.getaddrinfo" and (args[0] not in {"127.0.0.1", "::1"} or str(args[1]) != "11434"):
            events.append("network-lookup")
            raise PermissionError("non-model lookup refused")

    sys.addaudithook(audit)
    sys.path.insert(0, str(source / "src"))
    import dossier_incremental as inc

    config = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434",
                       "model": "qwen3.6:35b-mlx", "num_ctx": 8192,
                       "cloud_fallback_local": False, "allow_remote": False},
              "sufler": {"role": "Audit invented factual additions conservatively."}}
    receipt = {"production_data_used": False, "diagnostic_only": True,
               "calls": calls, "tripwire_events": events, "complete": False,
               "request_profile": {"think": think, "num_predict": num_predict, "num_ctx": 8192},
               "source_sha256": hashlib.sha256((source / "src/dossier_incremental.py").read_bytes()).hexdigest()}
    for path in prompts:
        prompt = path.read_text()
        fallbacks = []
        started = time.monotonic()
        raw, verdict, error = "", None, None
        try:
            raw = inc.local_complete(prompt, config, system=inc.SUPPORT_AUDIT_SYSTEM,
                                     think=think, num_predict=num_predict, fell_back=fallbacks)
            verdict = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - preserve a failed diagnostic, never acceptance
            error = type(exc).__name__ + ": " + str(exc)
        n = len(calls) + 1
        (output / f"{n}-prompt.txt").write_text(prompt)
        (output / f"{n}-answer.txt").write_text(raw)
        calls.append({"prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                      "verdict": verdict, "fell_back": any(fallbacks), "error": error,
                      "wall_s": round(time.monotonic() - started, 3)})
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["complete"] = True
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
