"""Manual local-model control: two dossiers, one graph, then a new meeting.

Usage: python -I -B this_file.py PUBLIC_SOURCE OUTPUT_DIR
Never collected as a pytest test; fixtures are invented and cloud is refused.
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

source, output = (Path(arg).resolve() for arg in sys.argv[1:3])
output.mkdir(parents=True, exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix="charoite843-two-dossiers-"))
data, graph = runtime / "data", runtime / "graph"
for path in (data, graph, runtime / "tmp"):
    path.mkdir()
os.environ.update(CHAROITE_ROOT=str(data), CHAROITE_NO_CLOUD="1",
                  XDG_CONFIG_HOME=str(runtime / "config"),
                  XDG_CACHE_HOME=str(runtime / "cache"), TMPDIR=str(runtime / "tmp"))
sys.dont_write_bytecode = True
tripwire, calls = [], []


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).absolute()
        if path.is_relative_to(Path.home()) and not any(
            path.is_relative_to(root) for root in (source, Path(sys.prefix), output)
        ):
            tripwire.append({"event": event, "path": "owner-path"})
            raise PermissionError("owner path refused")
    if event == "socket.connect":
        address = args[1]
        if not (isinstance(address, tuple)
                and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434):
            tripwire.append({"event": event, "path": "network"})
            raise PermissionError("non-model network refused")


sys.addaudithook(audit)
sys.path[:0] = [str(source / "src"), str(source / "scripts")]
from charoite_graph import dossier, frontmatter  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import charoite_paths  # noqa: E402
import dossier_incremental as incremental  # noqa: E402
from llm import LLM  # noqa: E402

charoite_paths.use_data_root(data)
spec = importlib.util.spec_from_file_location("two_dossiers_night", source / "scripts/nightly_dossier.py")
night = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = night
spec.loader.exec_module(night)
original_stream = LLM.stream


def measured_stream(self, prompt, *args, **kwargs):
    number = len(calls) + 1
    call = {"number": number, "engine": self.engine, "model": self.model,
            "system": kwargs.get("system"), "num_ctx": self.num_ctx,
            "temperature": kwargs.get("temperature")}
    calls.append(call)
    (output / f"call-{number:02}-prompt.txt").write_text(prompt)
    answer, started = [], time.monotonic()
    try:
        for chunk in original_stream(self, prompt, *args, **kwargs):
            answer.append(chunk)
            yield chunk
    finally:
        call.update(wall_s=round(time.monotonic() - started, 3),
                    fell_back=bool(self._fell_back_local))
        (output / f"call-{number:02}-answer.txt").write_text("".join(answer))
        print("CALL " + json.dumps(call), file=sys.__stdout__, flush=True)


LLM.stream = measured_stream
CONFIG = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434",
                  "model": "qwen3.6:35b-mlx", "num_ctx": 8192,
                  "cloud_fallback_local": False},
          "sufler": {"role": "You maintain invented factual topic dossiers."}}
MEETINGS, CORES = "\u0412\u0441\u0442\u0440\u0435\u0447\u0438", "\u042f\u0434\u0440\u0430"
A, B = "2099-01-01_100000", "2099-01-02_100000"
BUILT, THEME = "\u0441\u043e\u0431\u0440\u0430\u043d\u043e", "\u0442\u0435\u043c\u0430"
WARNING = "\u26a0 Sensor is unverified."
themes = {
    "Glasshouse": {"old": "Vent A is open.", "new": "A rain cover was fitted to the Glasshouse."},
    "WaterTank": {"old": "The water in the tank is clean.", "new": "A level sensor was fitted to the WaterTank."},
}
for folder in (MEETINGS, CORES, CHAROITE.dossier_dir):
    (graph / folder).mkdir()
for theme, other in (("Glasshouse", "WaterTank"), ("WaterTank", "Glasshouse")):
    (graph / CORES / f"{theme}.md").write_text(f"# {theme}\nInvented maintenance system. Associated [[{CORES}/{other}]].\n")
(graph / MEETINGS / f"{A}.md").write_text(
    f"# Synthetic old meeting\n[[{CORES}/Glasshouse]] Vent A is open.\n"
    f"[[{CORES}/WaterTank]] The water in the tank is clean.\n")
files, _ = dossier.scan(graph, schema=CHAROITE)
before, manual = {}, {}
for theme, info in themes.items():
    body = (f"## \u0421\u0435\u0439\u0447\u0430\u0441\n- {info['old']} [[{MEETINGS}/{A}]]\n"
            f"## \u041a\u0430\u043a \u043f\u0440\u0438\u0448\u043b\u0438\n- The initial decision is historical. [[{MEETINGS}/{A}]]\n"
            "## \u0420\u0435\u0448\u0435\u043d\u043e\n- Use purple labels. [[SensorGuide]]\n"
            f"## \u041e\u0442\u043a\u0440\u044b\u0442\u043e\n- {WARNING}\n"
            "## \u041a\u0442\u043e \u0432 \u0442\u0435\u043c\u0435\n- Maintainer.\n")
    manual[theme] = f"\n    Keep purple labels for {theme}.  \n### Note\nRetain this author payload.\n\n"
    text = dossier.render(theme, body, [theme, A], files, "baseline", "2099-01-01",
                          author_payload=manual[theme])
    text = dossier.with_composition(text, [A])
    if theme == "Glasshouse":
        text = dossier.with_revision(text, "2099-01-01")
    path = graph / CHAROITE.dossier_dir / f"{theme}.md"
    path.write_text(text)
    before[theme] = path.read_bytes()
    folder = output / theme
    folder.mkdir()
    (folder / "before.md").write_bytes(before[theme])


def metadata():
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        tags = json.load(response)
    return next({"name": m["name"], "digest": m["digest"], "details": m["details"]}
                for m in tags["models"] if m["name"] == CONFIG["llm"]["model"])


def run_phase(label):
    count, stream = len(calls), io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = night.run(graph, CONFIG, full=False, dry=False, limit=12, budget_s=600)
    (output / f"{label}-stdout.txt").write_text(stream.getvalue())
    measured = {"result": result, "model_calls": len(calls) - count}
    print("PHASE " + label + " " + json.dumps(measured, ensure_ascii=False), flush=True)
    return measured


preflight = metadata()
baseline = run_phase("before-new-meeting")
new_source = ("# Synthetic new meeting\n"
              f"[[{CORES}/Glasshouse]] {themes['Glasshouse']['new']}\n"
              f"[[{CORES}/WaterTank]] {themes['WaterTank']['new']}\n")
(graph / MEETINGS / f"{B}.md").write_text(new_source)
(output / "new-meeting.md").write_text(new_source)
updated = run_phase("after-new-meeting")
after, checks = {}, {}
for theme, info in themes.items():
    path = graph / CHAROITE.dossier_dir / f"{theme}.md"
    after[theme] = path.read_bytes()
    text = after[theme].decode()
    (output / theme / "after.md").write_bytes(after[theme])
    checks[theme] = {
        "new_fact_added": info["new"].removeprefix("A ").lower() in text.lower(),
        "other_topic_fact_absent": all(
            other["new"].removeprefix("A ").lower() not in text.lower()
            for other_theme, other in themes.items() if other_theme != theme),
        "old_fact_retained": info["old"] in text,
        "old_link_retained": f"[[{MEETINGS}/{A}]]" in text,
        "new_link_added": any(link in text for link in (f"[[{MEETINGS}/{B}]]", f"[[{B}]]")),
        "warning_retained": WARNING in text,
        "manual_retained": manual[theme] in text,
        "old_external_link_retained": "[[SensorGuide]]" in text,
        "composition": dossier.composition(text, schema=CHAROITE),
        "rejected": frontmatter.list_field(text, dossier.REJECTED_FIELD),
        "revision": dossier.revision(text),
    }
backups = sorted((graph / CHAROITE.dossier_dir / ".backup").rglob("*.md"))
repeat = run_phase("repeat")
repeat["bytes_unchanged"] = all((graph / CHAROITE.dossier_dir / f"{t}.md").read_bytes() == raw
                               for t, raw in after.items())
repeat["no_new_backup"] = sorted((graph / CHAROITE.dossier_dir / ".backup").rglob("*.md")) == backups
index = json.loads((graph / CHAROITE.dossier_dir / dossier.INDEX_JSON).read_text())
(output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
index_themes = {entry[THEME] for entry in index["\u0434\u043e\u0441\u044c\u0435"]}
judge_cases = []
files, _ = dossier.scan(graph, schema=CHAROITE)
for theme, other_theme in (("Glasshouse", "WaterTank"), ("WaterTank", "Glasshouse")):
    old = dossier.DossierLoad("ok", before[theme].decode(), before[theme])
    base_body = old.parsed.body
    marker = "## \u041a\u0430\u043a \u043f\u0440\u0438\u0448\u043b\u0438"

    def inserted(statement):
        return base_body.replace(marker, f"- {statement} [[{B}]]\n{marker}")

    own, other = themes[theme]["new"], themes[other_theme]["new"]
    probes = [("own-fact", inserted(own), True, None, False),
              ("mixed-bullets", inserted(own + f" [[{B}]]\n- " + other), False, None, False)]
    if theme == "Glasshouse":
        relation = "The WaterTank pump supplies irrigation water to the Glasshouse."
        probes.extend([
            ("mixed-clauses", inserted(own + " " + other), False, None, False),
            ("appended-to-old-line", base_body.replace(
                themes[theme]["old"], themes[theme]["old"] + " " + other + f" [[{B}]]"),
             False, None, True),
            ("explicit-relationship", inserted(relation), True, relation, False),
            ("ambiguous-relationship", inserted(relation), False,
             "A relationship between the WaterTank pump and Glasshouse irrigation is suspected but unverified.",
             False),
        ])
    for label, candidate, expected, source_override, semantic_only in probes:
        supplied = files
        if source_override:
            supplied = {name: dict(meta) for name, meta in files.items()}
            supplied[B]["text"] = "# Synthetic relationship\n" + source_override + "\n"
        count, accepted, reason = len(calls), False, None
        try:
            check = incremental.semantic_check if semantic_only else incremental.validate_candidate
            check(theme, old, [B], supplied, CONFIG, candidate, [])
            accepted = True
        except incremental.Refused as exc:
            reason = str(exc)
        judge_cases.append({"theme": theme, "case": label, "expected_accept": expected,
                            "accepted": accepted, "reason": reason, "calls": len(calls) - count,
                            "scope": "semantic-only" if semantic_only else "candidate-validation",
                            "passed": accepted == expected and len(calls) - count >= 1})
        print("JUDGE " + json.dumps(judge_cases[-1]), flush=True)
postflight = metadata()
backup_bytes_match = all(path.read_bytes() == before[path.stem] for path in backups)
passed = (baseline["model_calls"] == 0 and baseline["result"][BUILT] == 0
          and updated["result"][BUILT] == 2 and updated["model_calls"] >= 6
          and repeat["model_calls"] == 0 and repeat["result"][BUILT] == 0
          and repeat["bytes_unchanged"] and repeat["no_new_backup"]
          and len(backups) == 2 and backup_bytes_match and preflight["digest"] == postflight["digest"]
          and not tripwire and all(not c["fell_back"] for c in calls)
          and index_themes == set(themes) and all(case["passed"] for case in judge_cases))
for theme, checked in checks.items():
    passed = passed and all(checked[k] for k in (
        "new_fact_added", "other_topic_fact_absent", "old_fact_retained", "old_link_retained", "new_link_added",
        "warning_retained", "manual_retained", "old_external_link_retained"))
    passed = passed and checked["composition"] == [A, B] and checked["rejected"] == []
    passed = passed and checked["revision"] == ("2099-01-01" if theme == "Glasshouse" else "")
receipt = {"source": str(source), "runtime": str(runtime), "mode": "real-local-model",
           "production_data_used": False, "baseline": baseline, "updated": updated,
           "repeat": repeat, "dossiers": checks, "backup_count": len(backups),
           "real_model_calls": len(calls), "calls": calls, "tripwire_events": tripwire,
           "preflight": preflight, "postflight": postflight,
           "index_themes": sorted(index_themes), "judge_cases": judge_cases,
           "backup_bytes_match": backup_bytes_match,
           "source_hashes": {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in (
               "scripts/nightly_dossier.py", "src/dossier_incremental.py", "src/charoite_graph/dossier.py")},
           "passed": bool(passed)}
(output / "byte_exact_artifacts.json").write_text(json.dumps({
    "encoding": "UTF-8 strings; encode to recover byte-exact files",
    "files": {f"{theme}/{stage}.md": {
        "sha256": hashlib.sha256(raw).hexdigest(), "utf8": raw.decode()}
        for theme in themes for stage, raw in (
            ("before", before[theme]), ("after", after[theme]))},
}, ensure_ascii=True, indent=2) + "\n")
(output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
print("RECEIPT " + json.dumps(receipt, ensure_ascii=False), flush=True)
raise SystemExit(0 if passed else 1)
