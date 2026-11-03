"""Manual real-local-model experiment: a narrow, independently framed claim audit.

Usage: python -I -B this_file.py PUBLIC_SOURCE OUTPUT_DIR
Fictional inputs only. This is not a pytest test or production acceptance.
"""
from pathlib import Path
import json
import os
import sys
import tempfile

source, output = (Path(arg).resolve() for arg in sys.argv[1:3])
output.mkdir(parents=True, exist_ok=True)
runtime = Path(tempfile.mkdtemp(prefix="charoite843-claim-audit-"))
root = runtime / "data"
root.mkdir()
os.environ.update(CHAROITE_ROOT=str(root), CHAROITE_NO_CLOUD="1",
                  XDG_CONFIG_HOME=str(runtime / "config"), XDG_CACHE_HOME=str(runtime / "cache"))
sys.dont_write_bytecode = True
events, calls = [], []


def audit(event, args):
    if event in {"open", "os.listdir", "os.scandir"} and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).absolute()
        if path.is_relative_to(Path.home()) and not any(
            path.is_relative_to(p) for p in (source, output, Path(sys.prefix))
        ):
            events.append("owner-path")
            raise PermissionError("owner path refused")
    if event == "socket.connect":
        address = args[1]
        if not (isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"} and address[1] == 11434):
            events.append("network")
            raise PermissionError("non-model network refused")


sys.addaudithook(audit)
sys.path.insert(0, str(source / "src"))
import charoite_paths  # noqa: E402
import dossier_incremental as inc  # noqa: E402
from llm import LLM  # noqa: E402

charoite_paths.use_data_root(root)
original = LLM.stream


def measured(self, prompt, *args, **kwargs):
    calls.append({"engine": self.engine, "model": self.model})
    yield from original(self, prompt, *args, **kwargs)


LLM.stream = measured
config = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434",
                   "model": "qwen3.6:35b-mlx", "num_ctx": 8192, "cloud_fallback_local": False},
          "sufler": {"role": "Audit invented factual additions conservatively."}}
own = "A rain cover was fitted to the Glasshouse."
foreign = "A level sensor was fitted to the WaterTank."
relation = "The WaterTank pump supplies irrigation water to the Glasshouse."
probes = [
    ("own", own, own, True),
    ("foreign", foreign, "[[\u042f\u0434\u0440\u0430/WaterTank]] " + foreign, False),
    ("compound-partial-proof", own + " " + foreign, own, False),
    ("ambiguous-relationship", relation,
     "A relationship between the WaterTank pump and Glasshouse irrigation is suspected but unverified.", False),
    ("explicit-relationship", relation, relation, True),
    ("paraphrased-foreign", "WaterTank's level monitor was installed.", foreign, False),
    ("compound-full-proof", own + " " + foreign, own + "\n" + foreign, False),
    ("prior-object-update", "Vent A is now closed.",
     "Vent A is now closed; this supersedes the earlier open state.", True),
    ("qualifier-amplification", "Vent A is closed.",
     "Vent A may be closed, but closure is not confirmed.", False),
    ("anonymous-scoped-own", "A rain cover was fitted.",
     "[[\u042f\u0434\u0440\u0430/Glasshouse]] A rain cover was fitted.", True),
    ("anonymous-scoped-foreign", "A level sensor was fitted.",
     "[[\u042f\u0434\u0440\u0430/WaterTank]] A level sensor was fitted.", False),
]
instruction = (
    "Audit ONLY this one proposed addition, separately from any whole-meeting verdict. "
    "The prior_dossier is explicitly the body of the supplied topic's dossier: an already "
    "identified object described there may be updated without repeating the topic name in "
    "every line. Do not treat its section headings or provenance citations as unrelated notes. "
    "A wiki topic tag at the start of the quotation explicitly annotates the following "
    "statement's subject context when it has no other named subject. It does not connect "
    "different named objects: a statement about a different named object keeps that subject. "
    "A tag alone cannot make substantively unrelated content relevant; still assess the "
    "topic's objects, activities and purpose. Bullet markers and provenance wiki citations "
    "are formatting/attribution, not additional factual assertions requiring verbatim quotation. "
    "Use exclusively the exact source quotation and prior dossier supplied. Do not assume "
    "that entities in the same meeting are connected. A different named object is NOT a "
    "component, dependency or part of the dossier topic unless the quotation or prior "
    "dossier explicitly states that relationship. Do not supply such a relationship from "
    "common knowledge. Shared activities, generic words or a generic warning are not an "
    "explicit connection between named objects. "
    "Judge support from the quotation FIRST, independently of topic relevance. If the quotation "
    "states exactly the proposed complete affirmative fact, supported=true even for a foreign "
    "topic. Then judge relevance separately. The NEW quotation CAN establish a new explicit "
    "relationship between a named object and the dossier topic; it need not already exist in "
    "the prior dossier. Absence from the prior dossier never overrides a relationship explicitly "
    "stated in the new quotation. This is an OR requirement: explicit NEW evidence is sufficient. "
    "A matching topic name identifies the same topic; articles, capitalization and possessives "
    "do not create a different entity. An action explicitly stated about the named topic is "
    "directly relevant, including a newly introduced component or action. Do not invent identity "
    "ambiguity merely because a fact/component is new or a definite article is present. "
    "Check EVERY independent assertion/clause in candidate_text: the quotation must entail "
    "all of it, including negation, uncertainty and qualifications. If the text combines "
    "several assertions but the quotation supports only one, supported=false. A suspected "
    "or unverified relationship does not support a definite relationship. "
    "relevant=true only if EVERY assertion is directly about this dossier's topic or an "
    "EXPLICIT source/prior-dossier relationship connecting the other named object to it. "
    "For foreign-object-only facts with no explicit connection set relevant=false even "
    "if factually true. Return ONLY JSON with exactly verdict(accept/reject/uncertain), "
    "supported(boolean), relevant(boolean), reason(nonempty string). Accept only if both "
    "booleans are true; uncertainty requires uncertain. Keep reason under 40 words. "
    "Calibration uses fictional labels, not special-case entity names: topic ProjectOne, prior "
    "ValveX is open; new quotation ProjectTwo pump supplies ProjectOne. An identical candidate "
    "MUST have supported=true and relevant=true: the NEW quotation explicitly establishes "
    "the relationship, even though ProjectTwo was absent from the prior dossier. In contrast, "
    "quotation and candidate ProjectTwo sensor was installed are supported but NOT relevant "
    "to ProjectOne without an explicit connection. A compound claim ProjectOne cover was fitted; "
    "ProjectTwo sensor was installed is NOT wholly relevant even when both facts are quoted. "
    "Never veto an explicitly quoted NEW relationship merely because it was absent before."
)
results = []
for label, candidate, quote, expected in probes:
    prior = ("## \u0421\u0435\u0439\u0447\u0430\u0441\n- Vent A is open. [[\u0412\u0441\u0442\u0440\u0435\u0447\u0438/2099-01-01_100000]]\n"
             "## \u041a\u0430\u043a \u043f\u0440\u0438\u0448\u043b\u0438\n- The opening decision is historical. [[\u0412\u0441\u0442\u0440\u0435\u0447\u0438/2099-01-01_100000]]\n"
             "## \u0420\u0435\u0448\u0435\u043d\u043e\n- Use purple labels. [[SensorGuide]]\n"
             "## \u041e\u0442\u043a\u0440\u044b\u0442\u043e\n- \u26a0 Sensor is unverified.\n"
             "## \u041a\u0442\u043e \u0432 \u0442\u0435\u043c\u0435\n- Maintainer.")
    payload = {"topic": "Glasshouse", "prior_dossier": prior,
               "candidate_text": "- " + candidate + " [[2099-01-02_100000]]", "source_quotation": quote}
    prompt = json.dumps(payload, ensure_ascii=False) + "\n" + instruction
    (output / f"{label}-prompt.txt").write_text(prompt)
    count, fallback = len(calls), []
    raw = inc.local_complete(prompt, config, system="You audit one proposed factual addition.",
                             num_predict=350, fell_back=fallback)
    (output / f"{label}-answer.txt").write_text(raw)
    verdict = inc._json(raw, {"verdict", "supported", "relevant", "reason"})
    accepted = (verdict["verdict"] == "accept" and verdict["supported"] is True
                and verdict["relevant"] is True and bool(verdict["reason"]))
    measured_result = {"case": label, "expected_accept": expected, "accepted": accepted,
                       "verdict": verdict, "real_calls": len(calls) - count,
                       "fell_back": any(fallback), "passed": accepted == expected}
    results.append(measured_result)
    print(json.dumps(measured_result), flush=True)
receipt = {"results": results, "real_model_calls": len(calls), "tripwire_events": events,
           "production_data_used": False, "passed": not events and all(
               r["passed"] and r["real_calls"] == 1 and not r["fell_back"] for r in results)}
(output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
raise SystemExit(0 if receipt["passed"] else 1)
