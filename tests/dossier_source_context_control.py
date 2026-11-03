"""Manual full-source audit control using the production local model path.

Usage: python -I -B this_file.py PUBLIC_SOURCE OUTPUT_DIR
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
SYSTEM = "You audit one proposed factual addition."
OWN = "A rain cover was fitted to the Glasshouse."
FOREIGN = "A level sensor was fitted to the WaterTank."
RELATION = "The WaterTank pump supplies irrigation water to the Glasshouse."
QUALIFIED = "A rain cover may have been fitted to the Glasshouse; this is unverified."
MIXED = f"[[Ядра/Glasshouse]] {OWN}\n[[Ядра/WaterTank]] {FOREIGN}\n"
PRIOR = (f"## Сейчас\n- Vent A is open. [[Встречи/{A}]]\n"
         f"## Как пришли\n- The opening decision is historical. [[Встречи/{A}]]\n"
         "## Решено\n- Use purple labels. [[SensorGuide]]\n"
         "## Открыто\n- ⚠ Sensor is unverified.\n"
         "## Кто в теме\n- Maintainer.")


def _probes():
    return [
        {"case": "qualifier-before-anchor", "candidate": OWN, "quote": OWN,
         "source": "The following statement is tentative and has not been verified.\n" + OWN + "\n", "accept": False},
        {"case": "qualifier-after-anchor", "candidate": OWN, "quote": OWN,
         "source": OWN + "\nThe preceding statement is only a hypothesis and has not been verified.\n", "accept": False},
        {"case": "negation-before-anchor", "candidate": OWN, "quote": OWN,
         "source": "The following line is a false draft, not an accepted fact.\n" + OWN + "\n", "accept": False},
        {"case": "negation-after-anchor", "candidate": OWN, "quote": OWN,
         "source": OWN + "\nCorrection: the preceding line is false. No rain cover was fitted to the Glasshouse.\n", "accept": False},
        {"case": "fully-qualified-positive", "candidate": QUALIFIED, "quote": QUALIFIED,
         "source": "Preliminary observation:\n" + QUALIFIED + "\nDo not treat it as confirmed installation.\n", "accept": True},
        {"case": "own-only-positive", "candidate": OWN, "quote": OWN,
         "source": f"[[Ядра/Glasshouse]] {OWN}\n", "accept": True},
        {"case": "mixed-context-own", "candidate": OWN, "quote": OWN, "source": MIXED, "accept": True},
        {"case": "mixed-context-foreign", "candidate": FOREIGN, "quote": FOREIGN, "source": MIXED, "accept": False},
        {"case": "explicit-new-relationship", "candidate": RELATION, "quote": RELATION,
         "source": OWN + "\n" + RELATION + "\n" + FOREIGN + "\n", "accept": True},
        {"case": "suspected-relationship", "candidate": RELATION,
         "quote": "A relationship between the WaterTank pump and Glasshouse irrigation is suspected but unverified.",
         "source": "A relationship between the WaterTank pump and Glasshouse irrigation is suspected but unverified.\n",
         "accept": False},
        {"case": "compound-full-proof", "candidate": OWN + " " + FOREIGN,
         "quote": OWN + "\n" + FOREIGN, "source": OWN + "\n" + FOREIGN + "\n", "accept": False},
        {"case": "compound-partial-proof", "candidate": OWN + " " + FOREIGN,
         "quote": OWN, "source": OWN + "\n", "accept": False},
    ]


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python -I -B control.py PUBLIC_SOURCE OUTPUT_DIR")
    source, output = (Path(arg).resolve() for arg in sys.argv[1:3])
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
    old_text = dossier.render("Glasshouse", PRIOR, [], {}, "synthetic", "2099-01-01", author_payload="\nKeep purple labels.\n")
    loaded = dossier.DossierLoad("ok", old_text, old_text.encode())
    (output / "prior-dossier.md").write_text(old_text)
    source_paths = ("src/dossier_incremental.py", "src/charoite_graph/dossier.py", "src/llm.py")

    def hashes():
        return {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in source_paths}

    original_stream = LLM.stream
    def measured(self, prompt, *args, **kwargs):
        number, started = len(calls) + 1, time.monotonic()
        row = {"number": number, "engine": self.engine,
               "model": self.effective_model(kwargs.get("model") or self.model),
               "num_ctx": self.num_ctx, "system": kwargs.get("system"),
               "num_predict": kwargs.get("num_predict"), "transport": self.base,
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
        model = next((item for item in tags["models"] if item["name"] == MODEL), None)
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
               "instruction_sha256": hashlib.sha256(inc.CLAIM_AUDIT_INSTRUCTION.encode()).hexdigest(),
               "real_model_calls": 0, "complete": False, "passed": False}
    def checkpoint():
        receipt["real_model_calls"] = len(calls)
        receipt["audit_event_count"] = len(events)
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")

    checkpoint()
    LLM.stream = measured
    try:
        receipt["preflight"] = metadata()
        for probe in _probes():
            label, start = probe["case"], len(calls)
            (output / f"{label}-source.md").write_text(probe["source"])
            claim = {"text": probe["candidate"], "source": B, "quote": probe["quote"],
                     "relevant": True, "supported": True, "reason": "Control evidence; independent audit decides."}
            reason, accepted, selected = None, False, None
            try:
                selected = inc._quote_context(probe["source"], probe["quote"])
                inc.audit_claim("Glasshouse", loaded, claim, config, quote_context=selected)
                accepted = True
            except Exception as exc:  # noqa: BLE001 - record operational failures; strict verdict still required
                reason = f"{type(exc).__name__}: {exc}"
            real_calls = len(calls) - start
            verdict, shape_ok, payload_ok = None, False, False
            if real_calls == 1:
                number = calls[-1]["number"]
                raw = (output / f"call-{number:02d}-answer.txt").read_text()
                prompt = (output / f"call-{number:02d}-prompt.txt").read_text()
                try:
                    verdict = inc._json(raw.strip(), {"verdict", "supported", "relevant", "reason"})
                    shape_ok = (verdict["verdict"] in {"accept", "reject", "uncertain"}
                                and type(verdict["supported"]) is bool and type(verdict["relevant"]) is bool
                                and isinstance(verdict["reason"], str) and bool(verdict["reason"].strip()))
                    payload, _ = json.JSONDecoder().raw_decode(prompt)
                    payload_ok = payload == {"topic": "Glasshouse", "prior_dossier": loaded.parsed.body,
                                             "candidate_text": probe["candidate"], "source_quotation": probe["quote"],
                                             "source_context": probe["source"]}
                except (ValueError, TypeError, KeyError):
                    shape_ok = False
            passed = (real_calls == 1 and shape_ok and payload_ok and selected == probe["source"]
                      and calls[-1]["error"] is None and not calls[-1]["fell_back"] and accepted == probe["accept"])
            result = {"case": label, "expected_accept": probe["accept"], "accepted": accepted,
                      "reason": reason, "real_calls": real_calls, "verdict": verdict,
                      "fell_back": any(call["fell_back"] for call in calls[start:]),
                      "strict_verdict": shape_ok, "full_context_payload": payload_ok,
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
    receipt["passed"] = bool(receipt["complete"] and len(results) == len(_probes())
                             and all(result["passed"] for result in results) and not events
                             and len(calls) == len(_probes()) and all(not call["fell_back"] for call in calls)
                             and before_hashes == receipt["source_hashes_after"]
                             and receipt["preflight"]["digest"] == receipt["postflight"]["digest"])
    checkpoint()
    print("RECEIPT " + json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
