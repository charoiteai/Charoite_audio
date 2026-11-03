"""Standalone invented before/after scenarios; no production data or config.

Usage: python -I -B this_file.py PUBLIC_SOURCE LABEL OUTPUT_DIR fixed|model
The fixed transport is a deterministic double, never model evidence.
"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import urllib.request

source = Path(sys.argv[1]).resolve()
label, output, mode = sys.argv[2], Path(sys.argv[3]).resolve(), sys.argv[4]
if mode not in {"fixed", "model"}:
    raise SystemExit("fixed or model required")
output.mkdir(parents=True, exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix="charoite843-control-"))
data = runtime / "data"
data.mkdir()
os.environ.update(CHAROITE_ROOT=str(data), CHAROITE_NO_CLOUD="1",
                  XDG_CONFIG_HOME=str(runtime / "config"),
                  XDG_CACHE_HOME=str(runtime / "cache"), TMPDIR=str(runtime / "tmp"))
(runtime / "tmp").mkdir()
sys.dont_write_bytecode = True
tripwire = []


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).absolute()
        # Libraries in the selected interpreter's environment are permitted;
        # owner configuration, graphs and records are never permitted.
        home = Path.home()
        if path.is_relative_to(home) and not (
            path.is_relative_to(source) or path.is_relative_to(Path(sys.prefix))
            or path.is_relative_to(output)
        ):
            tripwire.append({"event": event, "path": "owner-path"})
            raise PermissionError("owner path refused by scenario runner")
    if event == "socket.connect":
        address = args[1]
        allowed = mode == "model" and isinstance(address, tuple) and address[0] in {
            "127.0.0.1", "::1"
        } and address[1] == 11434
        if not allowed:
            tripwire.append({"event": event, "path": "network"})
            raise PermissionError("non-model network refused by scenario runner")


sys.addaudithook(audit)
sys.path[:0] = [str(source / "src"), str(source / "scripts")]
from charoite_graph import dossier, frontmatter  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import charoite_paths  # noqa: E402

charoite_paths.use_data_root(data)
spec = importlib.util.spec_from_file_location("incremental_control_night", source / "scripts/nightly_dossier.py")
night = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = night
spec.loader.exec_module(night)

A, B = "2099-01-01_100000", "2099-01-02_100000"
BODY = (f"## Сейчас\n- Vent A is open. [[Встречи/{A}]]\n"
        f"## Как пришли\n- The opening decision is historical. [[Встречи/{A}]]\n"
        "## Решено\n- Use purple labels. [[SensorGuide]]\n"
        "## Открыто\n- ⚠ Sensor is unverified.\n"
        "## Кто в теме\n- Maintainer.\n")
AUTHOR = "\n    Keep purple labels.  \n### Note\nRetain this author payload.\n\n"
CONFIG = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434",
                  "model": "qwen3.6:35b-mlx", "num_ctx": 8192,
                  "cloud_fallback_local": False},
          "sufler": {"role": "You maintain invented factual topic dossiers."}}


def metadata():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(CONFIG["llm"]["base_url"] + "/api/tags", timeout=15) as response:
        tags = json.load(response)
    model = next(item for item in tags["models"]
                 if item["name"] == CONFIG["llm"]["model"].removesuffix(":local"))
    with opener.open(CONFIG["llm"]["base_url"] + "/api/version", timeout=15) as response:
        version = json.load(response)
    return {"name": model["name"], "digest": model["digest"], "details": model.get("details"),
            "server_version": version.get("version")}


def request_profile_ok(call):
    return (call["engine"] == "ollama" and call["transport"] == CONFIG["llm"]["base_url"]
            and call["model"] == CONFIG["llm"]["model"] + ":local"
            and call["truncate"] is False and call["shift"] is False and not call["fell_back"])


results, calls = [], []
if mode == "model":
    from llm import LLM
    original_stream = LLM.stream

    def measured_stream(self, prompt, *args, **kwargs):
        number = len(calls) + 1
        call = {"number": number, "engine": self.engine,
                "model": kwargs.get("model"), "base_model": self.model,
                "role": kwargs.get("system"), "temperature": kwargs.get("temperature"),
                "num_predict": kwargs.get("num_predict"), "think": kwargs.get("think"),
                "truncate": kwargs.get("truncate"), "shift": kwargs.get("shift"), "transport": self.base,
                "num_ctx": self.num_ctx, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
        calls.append(call)
        (output / f"call-{number:02}-prompt.txt").write_text(prompt)
        started = time.monotonic()
        answer = []
        try:
            for chunk in original_stream(self, prompt, *args, **kwargs):
                answer.append(chunk)
                yield chunk
        finally:
            call.update(wall_s=round(time.monotonic() - started, 3), fell_back=bool(self._fell_back_local))
            (output / f"call-{number:02}-answer.txt").write_text("".join(answer))
            print("CALL " + json.dumps(call), file=sys.__stdout__, flush=True)

    LLM.stream = measured_stream
    preflight = metadata()

for scenario, revised, new_fact in [
    ("S01-plain", False, "A rain cover was fitted."),
    ("S01-revised", True, "A rain cover was fitted."),
    ("S02-correction", False, "Vent A is now closed; this supersedes the earlier open state."),
    ("S04-irrelevant", False, "A recipe uses flour and water. No Glasshouse information is present."),
]:
    graph = runtime / scenario
    for folder in ("Ядра", "Встречи", CHAROITE.dossier_dir):
        (graph / folder).mkdir(parents=True, exist_ok=True)
    (graph / "Ядра/Glasshouse.md").write_text("# Glasshouse\nInvented greenhouse maintenance.\n")
    (graph / f"Встречи/{A}.md").write_text("# Synthetic A\n[[Ядра/Glasshouse]] Vent A is open.\n")
    (graph / f"Встречи/{B}.md").write_text(f"# Synthetic B\n[[Ядра/Glasshouse]] {new_fact}\n")
    path = graph / f"{CHAROITE.dossier_dir}/Glasshouse.md"
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    text = dossier.render("Glasshouse", BODY, ["Glasshouse", A], files, "baseline", "2099-01-01", author_payload=AUTHOR)
    text = dossier.with_composition(text, [A])
    if revised:
        text = dossier.with_revision(text, "2099-01-01")
    path.write_text(text)
    before = path.read_bytes()
    case_output = output / scenario
    case_output.mkdir(exist_ok=True)
    (case_output / "before.md").write_bytes(before)
    (case_output / "new-source.md").write_text(files[B]["text"])
    seen = []
    candidate = BODY.replace("Vent A is open.", new_fact).replace("⚠ Sensor is unverified.", "No open issues.")
    candidate += f"\n- Added [[Встречи/{B}]]\n"

    def fixed_generate(theme, members, files, config, **kwargs):
        seen.append(list(members))
        return candidate

    if mode == "fixed":
        night.generate = fixed_generate
        try:
            import dossier_incremental as incremental
        except ImportError:
            incremental = None
        if incremental is not None:
            def fixed_complete(prompt, config, *, system, **kwargs):
                payload, _ = json.JSONDecoder().raw_decode(prompt)
                if "source relevance" in system:
                    return json.dumps({"verdict": "relevant", "reason": "fixed test input",
                                       "quote": new_fact})
                if "extend factual" in system:
                    seen.append(list(payload["new_sources"]))
                    return json.dumps({"body": candidate, "changes": []})
                return json.dumps({"verdict": "accept", "conserved": True, "supported": True,
                                   "corrections_valid": True, "reason": "fixed test input"})
            incremental.local_complete = fixed_complete
    stream = io.StringIO()
    started, first_call = time.monotonic(), len(calls)
    try:
        with contextlib.redirect_stdout(stream):
            result = night.run(graph, CONFIG, full=False, dry=False, limit=12, budget_s=600)
        error = None
    except Exception as exc:  # noqa: BLE001 — diagnostic receipt records any producer failure
        result, error = {}, {"type": type(exc).__name__, "message": str(exc)}
    after = path.read_bytes()
    (case_output / "after.md").write_bytes(after)
    (case_output / "stdout.txt").write_text(stream.getvalue())
    repeat = None
    if mode == "model" and result.get("собрано") == 1:
        count = len(calls)
        backups = sorted((graph / CHAROITE.dossier_dir / ".backup").rglob("*.md"))
        with contextlib.redirect_stdout(stream):
            repeated = night.run(graph, CONFIG, full=False, dry=False, limit=12, budget_s=600)
        repeat = {"model_calls": len(calls) - count, "unchanged": path.read_bytes() == after,
                  "no_new_backup": sorted((graph / CHAROITE.dossier_dir / ".backup").rglob("*.md")) == backups,
                  "built": repeated["собрано"]}
    parsed_after = dossier.DossierLoad("ok", after.decode(), after)
    sections = {s.title: parsed_after.text[s.content:s.end] for s in parsed_after.parsed.sections}
    old_body = dossier.DossierLoad("ok", before.decode(), before).parsed.body
    behavior = {
        "current_state_consistent": scenario != "S02-correction" or (
            "Vent A is open." not in sections.get("Сейчас", "")
            and "closed" in sections.get("Сейчас", "")
            and f"- Vent A is open. [[Встречи/{A}]]" in sections.get("Как пришли", "")),
        "relevant_fact_added": scenario not in {"S01-plain", "S01-revised"}
                                or "rain cover" in sections.get("Сейчас", "").lower(),
        "irrelevant_body_unchanged": scenario != "S04-irrelevant" or parsed_after.parsed.body == old_body,
    }
    result = {"scenario": scenario, "revision": revised, "wall_s": round(time.monotonic() - started, 3),
              "result": result, "error": error, "calls": len(calls) - first_call,
              "generation_double_inputs": seen, "before_sha256": hashlib.sha256(before).hexdigest(),
              "after_sha256": hashlib.sha256(after).hexdigest(), "unchanged": before == after,
              "warning_retained": "⚠ Sensor is unverified." in after.decode(),
              "manual_retained": AUTHOR in after.decode(),
              "old_link_retained": f"[[Встречи/{A}]]" in after.decode(),
              "rejected": frontmatter.list_field(after.decode(), dossier.REJECTED_FIELD),
              "composition": dossier.composition(after.decode(), schema=CHAROITE), "repeat": repeat,
              "behavior": behavior}
    if mode == "model":
        result["request_profiles_ok"] = all(request_profile_ok(call) for call in calls[first_call:])
        result["passed"] = (error is None and result["result"].get("собрано") == 1
                            and all(result[k] for k in ("warning_retained", "manual_retained", "old_link_retained"))
                            and all(behavior.values()) and result["request_profiles_ok"]
                            and repeat == {"model_calls": 0, "unchanged": True, "no_new_backup": True, "built": 0}
                            and result["composition"] == ([A] if scenario == "S04-irrelevant" else [A, B])
                            and result["rejected"] == ([B] if scenario == "S04-irrelevant" else []))
    results.append(result)
    print("CASE " + json.dumps(result, ensure_ascii=False), flush=True)

judge_cases = []
if mode == "model":
    import dossier_incremental as incremental
    for case_name in ("S04-fixed-unrelated", "S05-fixed-warning-loss", "S02-fixed-contradictory-current"):
        selected = ("S04-irrelevant" if case_name.startswith("S04") else
                    "S02-correction" if case_name.startswith("S02") else "S01-plain")
        original = (output / selected / "before.md").read_bytes()
        old = dossier.DossierLoad("ok", original.decode(), original)
        files, _ = dossier.scan(runtime / selected, schema=CHAROITE)
        body = BODY.replace("## Как пришли", f"- A recipe uses flour and water. [[{B}]]\n## Как пришли")
        function = incremental.validate_candidate
        if case_name.startswith("S05"):
            body = BODY.replace("- ⚠ Sensor is unverified.\n", "").replace(
                "## Как пришли", f"- A rain cover was fitted. [[{B}]]\n## Как пришли")
            function = incremental.semantic_check
        if case_name.startswith("S02"):
            body = BODY.replace("## Как пришли", f"- Vent A is now closed. [[{B}]]\n## Как пришли")
            function = incremental.semantic_check
        count, rejected, error = len(calls), False, None
        try:
            function("Glasshouse", old, [B], files, CONFIG, body, [])
        except incremental.Refused as exc:
            rejected, error = True, str(exc)
        measured = {"scenario": case_name, "calls": len(calls) - count, "rejected": rejected,
                    "reason": error, "scope": "semantic-only" if case_name.startswith(("S05", "S02")) else "candidate-validation",
                    "passed": rejected and len(calls) - count >= 1}
        judge_cases.append(measured)
        print("JUDGE " + json.dumps(measured), flush=True)

receipt = {"source_label": label, "source": str(source), "mode": mode, "runtime": str(runtime),
           "real_model_calls": len(calls), "calls": calls, "cases": results,
           "judge_cases": judge_cases, "tripwire_events": tripwire, "production_data_used": False}
if mode == "model":
    receipt["preflight"], receipt["postflight"] = preflight, metadata()
    receipt["request_profiles_ok"] = all(request_profile_ok(call) for call in calls)
    receipt["passed"] = (not tripwire and all(r["passed"] for r in results + judge_cases)
                         and receipt["request_profiles_ok"]
                         and receipt["preflight"]["digest"] == receipt["postflight"]["digest"])
(output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
print("RECEIPT " + json.dumps(receipt, ensure_ascii=False), flush=True)
if mode == "model" and not receipt["passed"]:
    raise SystemExit(1)
