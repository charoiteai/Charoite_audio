"""Manual full-source audit control using the production local model path.

Usage: python -I -B this_file.py PUBLIC_SOURCE OUTPUT_DIR [boundary]
Invented inputs only. This file neither loads a production graph nor installs
anything. Run it only under the project's external resource gates.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.request


A, B = "2099-01-01_100000", "2099-01-02_100000"
MODEL = "qwen3.6:35b-mlx"
BASE = "http://127.0.0.1:11434"
FIXTURE = "tests/fixtures/dossier_claim_audit_cases.json"
FIXTURE_SHA256 = "ff0382ed38c828715ac29b078ae557e15d204fb00d302b7645c95fb90016c491"
BOUNDARY_FIXTURE = "tests/fixtures/dossier_claim_audit_boundary_cases.json"
BOUNDARY_SHA256 = "289ee11b9322039ec15b100c6639c790dbba608612f98ec682d9aec43b060744"
PRIOR = (f"## Сейчас\n- Vent A is open. [[Встречи/{A}]]\n"
         f"## Как пришли\n- The opening decision is historical. [[Встречи/{A}]]\n"
         "## Решено\n- Use purple labels. [[SensorGuide]]\n"
         "## Открыто\n- ⚠ Sensor is unverified.\n"
         "## Кто в теме\n- Maintainer.")


def main():
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "boundary"):
        raise SystemExit("Usage: python -I -B control.py PUBLIC_SOURCE OUTPUT_DIR [boundary]")
    source, output = (Path(arg).resolve() for arg in sys.argv[1:3])
    fixture_path, expected_hash = ((BOUNDARY_FIXTURE, BOUNDARY_SHA256) if len(sys.argv) == 4
                                   else (FIXTURE, FIXTURE_SHA256))
    output.mkdir(parents=True, exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="charoite843-source-context-"))
    root = runtime / "data"
    root.mkdir()
    os.environ.update(CHAROITE_ROOT=str(root), CHAROITE_NO_CLOUD="1",
                      XDG_CONFIG_HOME=str(runtime / "config"), XDG_CACHE_HOME=str(runtime / "cache"))
    sys.dont_write_bytecode = True
    os.chdir(runtime)
    home, control = Path.home().absolute(), Path(__file__).resolve()
    allowed = (source, output, runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())
    events, calls, results = [], [], []

    def audit(event, args):
        if event in {"open", "os.listdir", "os.scandir"} and args:
            raw = args[0]
            path = Path.cwd() if raw is None else Path(os.fsdecode(raw)).absolute() if isinstance(raw, (str, bytes)) else None
            if path is not None and path.is_relative_to(home) and path != control and not any(path.is_relative_to(p) for p in allowed):
                events.append({"event": event, "path": "owner-path"})
                raise PermissionError("owner path refused")
        if event == "socket.connect":
            address = args[1]
            if not (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434):
                events.append({"event": event, "path": "network"})
                raise PermissionError("non-model network refused")
        if event == "socket.getaddrinfo":
            if args[0] not in {"127.0.0.1", "::1"} or str(args[1]) != "11434":
                events.append({"event": event, "path": "network-lookup"})
                raise PermissionError("non-model address lookup refused")

    sys.addaudithook(audit)
    sys.path.insert(0, str(source / "src"))
    import charoite_paths
    import dossier_incremental as inc
    from charoite_graph import dossier
    from llm import LLM

    charoite_paths.use_data_root(root)
    config = {"llm": {"engine": "ollama", "base_url": BASE, "model": MODEL,
                       "num_ctx": 8192, "cloud_fallback_local": False, "allow_remote": False},
              "sufler": {"role": "Audit invented factual additions conservatively."}}
    fixture_bytes = (source / fixture_path).read_bytes()
    fixture_hash = hashlib.sha256(fixture_bytes).hexdigest()
    if fixture_hash != expected_hash:
        raise RuntimeError("the predeclared claim-audit fixture changed")
    matrix = json.loads(fixture_bytes)
    probes, topic = matrix["cases"], matrix["topic"]
    for probe in probes:
        if type(probe["expected_support"]) is not bool:
            raise RuntimeError("invalid predeclared stage expectations")
        topic_is_valid = (type(probe["expected_topic"]) is bool if probe["expected_support"]
                          else probe["expected_topic"] is None)
        if not topic_is_valid:
            raise RuntimeError("invalid predeclared stage expectations")
    expected_support_calls = len(probes)
    expected_topic_calls = sum(probe["expected_support"] for probe in probes)
    expected_total_calls = expected_support_calls + expected_topic_calls
    (output / "cases.json").write_bytes(fixture_bytes)
    source_paths = ("src/dossier_incremental.py", "src/charoite_graph/dossier.py", "src/llm.py", fixture_path)

    def hashes():
        return {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in source_paths}

    original_stream = LLM.stream
    def measured(self, prompt, *args, **kwargs):
        number, started = len(calls) + 1, time.monotonic()
        row = {"number": number, "engine": self.engine,
               "model": kwargs.get("model"), "base_model": self.model,
               "num_ctx": self.num_ctx, "system": kwargs.get("system"),
               "num_predict": kwargs.get("num_predict"), "transport": self.base,
               "truncate": kwargs.get("truncate"), "shift": kwargs.get("shift"),
               "think": kwargs.get("think"), "temperature": kwargs.get("temperature"),
               "fell_back": False, "error": None}
        calls.append(row)
        (output / f"call-{number:02d}-prompt.txt").write_text(prompt)
        pieces = []
        try:
            for chunk in original_stream(self, prompt, *args, **kwargs):
                pieces.append(chunk)
                yield chunk
        except BaseException as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            row["wall_s"] = round(time.monotonic() - started, 3)
            row["fell_back"] = bool(self._fell_back_local)
            (output / f"call-{number:02d}-answer.txt").write_text("".join(pieces))

    def metadata():
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(BASE + "/api/tags", timeout=15) as response:
            tags = json.load(response)
        model = next((item for item in tags["models"] if item["name"] == MODEL.removesuffix(":local")), None)
        if model is None:
            raise RuntimeError("required local model is unavailable")
        if not isinstance(model.get("digest"), str) or not model["digest"]:
            raise RuntimeError("local model does not expose a stable digest")
        with opener.open(BASE + "/api/version", timeout=15) as response:
            version = json.load(response)
        return {"name": model["name"], "digest": model.get("digest"), "details": model.get("details"),
                "server_version": version.get("version")}

    before_hashes = hashes()
    receipt = {"mode": "real-local-model", "production_data_used": False, "source": str(source),
               "runtime": str(runtime), "results": results, "calls": calls, "tripwire_events": events,
               "source_hashes_before": before_hashes,
               "control_sha256": hashlib.sha256(control.read_bytes()).hexdigest(),
               "instruction_sha256": {
                   "support": hashlib.sha256(inc.SUPPORT_AUDIT_INSTRUCTION.encode()).hexdigest(),
                   "topic": hashlib.sha256(inc.TOPIC_AUDIT_INSTRUCTION.encode()).hexdigest()},
               "fixture_sha256": fixture_hash,
               "expected_calls": {"support": expected_support_calls, "topic": expected_topic_calls,
                                  "total": expected_total_calls},
               "expected_request": {"model": MODEL + ":local", "truncate": False, "shift": False},
               "real_model_calls": 0, "complete": False, "passed": False}
    def checkpoint():
        receipt["real_model_calls"] = len(calls)
        receipt["audit_event_count"] = len(events)
        receipt["actual_calls"] = {"support": sum(row["system"] == inc.SUPPORT_AUDIT_SYSTEM for row in calls),
                                   "topic": sum(row["system"] == inc.TOPIC_AUDIT_SYSTEM for row in calls),
                                   "total": len(calls)}
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")

    def inspect_stage(row, specification, expected_payload):
        expected_system, flag_name, expected_flag = specification
        raw = (output / f"call-{row['number']:02d}-answer.txt").read_text()
        prompt = (output / f"call-{row['number']:02d}-prompt.txt").read_text()
        verdict, shape_ok, payload_ok, error = None, False, False, None
        try:
            verdict = inc._json(raw.strip(), {"verdict", flag_name, "reason"})
            shape_ok = (verdict["verdict"] in {"accept", "reject", "uncertain"}
                        and type(verdict[flag_name]) is bool
                        and isinstance(verdict["reason"], str) and bool(verdict["reason"].strip()))
            payload, _ = json.JSONDecoder().raw_decode(prompt)
            payload_ok = payload == expected_payload
        except (ValueError, TypeError, KeyError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        flag_ok = shape_ok and verdict[flag_name] is expected_flag
        decision_ok = shape_ok and (verdict["verdict"] == "accept" if expected_flag
                                    else verdict["verdict"] in {"reject", "uncertain"})
        profile_ok = (row["engine"] == "ollama" and row["model"] == MODEL + ":local" and row["transport"] == BASE
                      and row["truncate"] is False and row["shift"] is False
                      and row["num_ctx"] == config["llm"]["num_ctx"] and row["num_predict"] == 350
                      and row["think"] is False and type(row["temperature"]) in (int, float) and row["temperature"] == 0)
        order_ok = row["system"] == expected_system
        passed = bool(shape_ok and payload_ok and flag_ok and decision_ok and order_ok and profile_ok
                      and row["error"] is None and not row["fell_back"])
        return {"call": row["number"], "expected_system": expected_system, "actual_system": row["system"],
                "flag": flag_name, "expected_flag": expected_flag,
                "observed_flag": verdict[flag_name] if shape_ok else None, "verdict": verdict,
                "strict_verdict": shape_ok, "payload_ok": payload_ok, "component_matches": bool(flag_ok),
                "decision_matches": bool(decision_ok), "order_ok": order_ok, "profile_ok": profile_ok,
                "parse_error": error, "passed": passed}

    checkpoint()
    LLM.stream = measured
    try:
        receipt["preflight"] = metadata()
        for probe in probes:
            label, start = probe["case"], len(calls)
            (output / f"{label}-source.md").write_text(probe["source"])
            prior = PRIOR
            if note := probe.get("prior_history_note"):
                prior = prior.replace("## Решено\n", f"- {note}\n## Решено\n", 1)
            old_text = dossier.render(topic, prior, [], {}, "synthetic", "2099-01-01", author_payload="\nKeep purple labels.\n")
            loaded = dossier.DossierLoad("ok", old_text, old_text.encode())
            (output / f"{label}-prior-dossier.md").write_text(old_text)
            claim = {"text": probe["candidate"], "source": B, "quote": probe["quote"],
                     "relevant": True, "supported": True, "reason": "Control evidence; independent audit decides."}
            reason, accepted, selected = None, False, None
            try:
                selected = inc._quote_context(probe["source"], probe["quote"])
                inc.audit_claim(topic, loaded, claim, config, quote_context=selected)
                accepted = True
            except Exception as exc:  # noqa: BLE001 - record operational failures; strict verdict still required
                reason = f"{type(exc).__name__}: {exc}"
            real_calls = len(calls) - start
            expected_stages = [(inc.SUPPORT_AUDIT_SYSTEM, "supported", probe["expected_support"])]
            if probe["expected_support"]:
                expected_stages.append((inc.TOPIC_AUDIT_SYSTEM, "relevant", probe["expected_topic"]))
            expected_payload = {"topic": topic, "prior_dossier": loaded.parsed.body,
                                "candidate_text": probe["candidate"], "source_quotation": probe["quote"],
                                "source_context": probe["source"]}
            stage_results = []
            for offset, row in enumerate(calls[start:]):
                if offset < len(expected_stages):
                    stage_results.append(inspect_stage(row, expected_stages[offset], expected_payload))
                else:
                    stage_results.append({"call": row["number"], "actual_system": row["system"],
                                          "passed": False, "error": "unexpected extra stage"})
            order_ok = [row["system"] for row in calls[start:]] == [spec[0] for spec in expected_stages]
            expected_accept = bool(probe["expected_support"] and probe["expected_topic"])
            passed = bool(real_calls == len(expected_stages) and order_ok and stage_results
                          and all(stage["passed"] for stage in stage_results)
                          and selected == probe["source"] and accepted == expected_accept)
            observed_support = stage_results[0].get("observed_flag") if stage_results else None
            observed_topic = stage_results[1].get("observed_flag") if len(stage_results) > 1 else None
            result = {"case": label, "expected_accept": expected_accept, "accepted": accepted,
                      "expected_support": probe["expected_support"], "expected_topic": probe["expected_topic"],
                      "observed_support": observed_support, "observed_topic": observed_topic,
                      "reason": reason, "real_calls": real_calls, "expected_real_calls": len(expected_stages),
                      "stages": stage_results, "order_ok": order_ok,
                      "fell_back": any(call["fell_back"] for call in calls[start:]),
                      "full_context_selected": selected == probe["source"], "passed": passed}
            results.append(result)
            checkpoint()
            print(json.dumps(result, ensure_ascii=False), flush=True)
        receipt["postflight"] = metadata()
        receipt["complete"] = True
    except Exception as exc:  # noqa: BLE001 - preserve an explicitly failed partial receipt
        receipt["fatal_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        LLM.stream = original_stream
    receipt["source_hashes_after"] = hashes()
    receipt["passed"] = bool(receipt["complete"] and len(results) == len(probes)
                             and all(result["passed"] for result in results) and not events
                             and len(calls) == expected_total_calls and all(not call["fell_back"] for call in calls)
                             and before_hashes == receipt["source_hashes_after"]
                             and receipt["preflight"]["digest"] == receipt["postflight"]["digest"])
    checkpoint()
    print("RECEIPT " + json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
