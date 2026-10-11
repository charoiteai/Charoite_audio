#!/usr/bin/env python3
"""Profile a local runtime through Charoite's actual streaming transport.

CHAROITE_ROOT=/path/to/data python scripts/runtime_profile.py --engine ollama
--model name --prompt-file prompt.txt --repeats 4
No cloud or model downloads. Run both engines on the same prompt files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import platform
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))


def percentile(values: list[float], fraction: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def profile(call, prompts: list[str], repeats: int, *, may_run=lambda: True,
            clock=time.monotonic) -> dict:
    if isinstance(repeats, bool) or repeats < 2:
        raise ValueError("at least two repeats are needed to separate first and subsequent requests")
    rows = []
    interrupted = False
    for case, prompt in enumerate(prompts):
        for repeat in range(repeats):
            if not may_run():
                interrupted = True
                break
            start, first = clock(), None
            pieces = []
            error = None
            try:
                for piece in call(prompt):
                    if piece:
                        if first is None:
                            first = clock() - start
                        pieces.append(piece)
            except Exception as exc:  # noqa: BLE001 — retain failed trials, never print transport secrets
                error = type(exc).__name__
            output = "".join(pieces)
            rows.append({"case": case, "repeat": repeat, "first_observed": repeat == 0,
                         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                         "prompt_chars": len(prompt), "ttft_s": first, "elapsed_s": clock() - start,
                         "response_chars": len(output), "response_sha256": hashlib.sha256(output.encode()).hexdigest(),
                         "status": "error" if error else "ok" if output.strip() else "empty", "error": error})
        if interrupted:
            break
    subsequent = [row for row in rows if not row["first_observed"] and row["status"] == "ok"]
    ttfts = [row["ttft_s"] for row in subsequent if row["ttft_s"] is not None]
    elapsed = [row["elapsed_s"] for row in subsequent]
    return {"trials": rows, "interrupted_by_live_meeting": interrupted,
            "expected_trials": len(prompts) * repeats, "completed_trials": len(rows),
            "failed_or_empty_trials": sum(row["status"] != "ok" for row in rows),
            "subsequent_successful_trials": len(subsequent),
            "ttft_median_s": statistics.median(ttfts) if ttfts else None,
            "ttft_p95_s": percentile(ttfts, .95), "elapsed_p95_s": percentile(elapsed, .95)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["ollama", "mlx-server"], required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-file", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--num-context", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=256)
    args = parser.parse_args()
    if args.repeats < 2 or args.num_context < 1 or args.num_predict < 1:
        parser.error("repeats >= 2; context and prediction limits must be positive")
    from charoite_paths import harden_umask, resolve_root
    import live_gate
    from llm import LLM
    harden_umask()
    try:
        root = resolve_root(__file__)
        prompts = [path.read_text(encoding="utf-8") for path in args.prompt_file]
        if any(not prompt.strip() for prompt in prompts):
            raise ValueError("prompt files must be nonempty")
        cfg = {"llm": {"engine": args.engine, "model": args.model, "mlx_model": args.model,
                       "num_ctx": args.num_context, "temperature": 0}, "sufler": {}}
        model = LLM(cfg)
        report = profile(lambda prompt: model.stream(prompt, model=args.model, think=False,
                                                     num_predict=args.num_predict, temperature=0, busy_wait=0),
                         prompts, args.repeats, may_run=lambda: not live_gate.daemon_alive(root))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    report["configuration"] = {"engine": args.engine, "model": args.model, "num_ctx": args.num_context,
                               "num_predict": args.num_predict, "temperature": 0, "think": False,
                               "platform": platform.platform(), "python": platform.python_version()}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return int(report["failed_or_empty_trials"] > 0 or report["interrupted_by_live_meeting"])


if __name__ == "__main__":
    raise SystemExit(main())
