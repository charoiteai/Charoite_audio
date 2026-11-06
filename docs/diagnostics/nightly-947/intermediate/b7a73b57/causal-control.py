"""Deterministic generate-boundary comparison; never a model experiment.

Run once in a fresh interpreter per tracked tree. The only generated input
is the literal fixture below. Every socket connection is prohibited. A fake
adapter records generation entry, so success means admission to the double.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tree", type=Path)
    args = parser.parse_args()
    tree = args.tree.resolve()
    with tempfile.TemporaryDirectory(prefix="nightly-947-causal-") as own:
        os.environ["CHAROITE_ROOT"] = own
        sys.path.insert(0, str(tree / "src"))
        import llm
        from charoite_graph import dossier
        from charoite_schema import CHAROITE

        def no_connection(*args, **kwargs):
            raise AssertionError("diagnostic control forbids actual sockets")

        socket.create_connection = no_connection
        socket.socket.connect = no_connection
        counters = {"constructors": 0, "stream_entries": 0, "contract_checks": 0}
        body = "## Сейчас\nInvented fact.\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"

        class Adapter:
            cloud_ready = False
            engine = "ollama"
            base = "http://127.0.0.1:13701"
            _fell_back_local = False

            def __init__(self, config):
                counters["constructors"] += 1

            def require_local_ollama_model(self, model):
                counters["contract_checks"] += 1
                return model + ":local"

            def stream(self, prompt, **kwargs):
                counters["stream_entries"] += 1
                yield body

        llm.LLM = Adapter
        spec = importlib.util.spec_from_file_location("nightly_947", tree / "scripts/nightly_dossier.py")
        nd = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(nd)
        results = []
        for name, text, context in [
            ("short_8192", "Invented short source.", 8192),
            ("long_8192", "Синтетический факт о вымышленном датчике. " * 200, 8192),
            ("long_32768", "Синтетический факт о вымышленном датчике. " * 200, 32768),
        ]:
            for key in counters:
                counters[key] = 0
            files = {"2099-01-01_100000": {"kind": "Встречи", "text": text}}
            members = list(files)
            prompt = dossier.build_prompt("Glasshouse", members, files, schema=CHAROITE)
            config = {"llm": {"engine": "ollama", "model": "invented-synthetic-model", "num_ctx": context,
                               "base_url": Adapter.base}, "sufler": {"role": "Invented fixture only."}}
            outcome, reason = "admitted_to_adapter_double", None
            try:
                assert nd.generate("Glasshouse", members, files, config) == body
            except ValueError as exc:
                outcome = "refused_before_stream" if counters["stream_entries"] == 0 else "failed_after_stream"
                reason = "context_budget" if "context budget" in str(exc) else type(exc).__name__
            results.append({"case": name, "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "prompt_bytes": len(prompt.encode()), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                            "complete_normalized_source_present_in_prompt": text.strip() in prompt,
                            "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                            "context": context,
                            "outcome": outcome, "reason_code": reason, **counters})
        print(json.dumps({"source_sha": subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"],
                         text=True).strip(), "real_generation_requests": 0, "actual_http_requests": 0,
                         "scope": "generate function with fake adapter; no graph scan/publication",
                         "control_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                         "source_file_sha256": {rel: hashlib.sha256((tree / rel).read_bytes()).hexdigest()
                           for rel in ["scripts/nightly_dossier.py", "src/charoite_graph/dossier.py", "src/llm.py",
                                       "src/privacy.py", "src/charoite_schema.py", "src/charoite_graph/frontmatter.py",
                                       "src/charoite_graph/graph_names.py"]},
                         "cases": results},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
