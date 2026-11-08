"""Invented user scenarios for incremental dossier boundary regressions.

Generation and semantic judgments below are transport doubles: zero real model
calls, no claim about model quality or mutation scores. Planning, source scans,
locks, backup, CAS writing and index publication use the production path.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import nightly_dossier as night
import dossier_incremental as inc
from charoite_graph import dossier, frontmatter
from charoite_schema import CHAROITE

THEME = "Glasshouse"
A, B, X, Y = (f"2099-01-0{i}_100000" for i in range(1, 5))
FACT = "A rain cover was fitted."
BODY = (f"## Сейчас\n- Vent A is open. [[{A}]]\n## Как пришли\n—\n"
        "## Решено\n- Keep labels. [[SensorGuide]]\n## Открыто\n- ⚠ Sensor is unverified.\n"
        "## Кто в теме\n—")
LOCAL = {"llm": {"engine": "ollama", "model": "invented", "base_url": "http://127.0.0.1:11434", "num_ctx": 8192}}


def _world(tmp, name="graph", body=BODY):
    graph = tmp / name
    for folder in ("Ядра", "Встречи", "Документы", "Досье"):
        (graph / folder).mkdir(parents=True)
    (graph / f"Ядра/{THEME}.md").write_text(f"# {THEME}\n")
    (graph / f"Встречи/{A}.md").write_text(f"[[Ядра/{THEME}]]\nVent A is open.\n")
    (graph / "Документы/SensorGuide.md").write_text(f"[[Ядра/{THEME}]] guide\n")
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    text = dossier.render(THEME, body, [THEME, A, "SensorGuide"], snap.files, "fixture", "2099-01-01",
                          author_payload="\nKeep purple labels.\n")
    path = graph / f"Досье/{THEME}.md"
    path.write_text(dossier.with_composition(text, [A]))
    return graph, path


def _source(graph, name=B, fact=FACT):
    path = graph / f"Встречи/{name}.md"
    path.write_text(f"[[Ядра/{THEME}]]\n{fact}\n")
    return path


def _addition(body=BODY):
    return body.replace("## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")


def _answer(payload, system, *, irrelevant=(), candidate=None):
    if "source relevance" in system:
        no = payload["source"] in irrelevant
        return json.dumps({"verdict": "irrelevant" if no else "relevant", "reason": "Synthetic source judgment.",
                           "quote": "" if no else FACT})
    if "extend factual" in system:
        return json.dumps({"body": candidate or _addition(payload["existing_dossier"]), "changes": []})
    if system == inc.SUPPORT_AUDIT_SYSTEM:
        return json.dumps({"verdict": "accept", "supported": True,
                           "reason": "Synthetic source-support judgment."})
    if system == inc.TOPIC_AUDIT_SYSTEM:
        return json.dumps({"verdict": "accept", "relevant": True,
                           "reason": "Synthetic topic judgment."})
    return json.dumps({"verdict": "accept", "conserved": True, "supported": True,
                       "corrections_valid": True, "reason": "Synthetic candidate judgment.",
                       "claims": [{"text": FACT, "source": B, "quote": FACT,
                                   "relevant": True, "supported": True, "reason": "Synthetic source evidence."}]})


def _transport(monkeypatch, *, irrelevant=(), after_generate=None, candidate=None):
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append((system, payload))
        answer = _answer(payload, system, irrelevant=irrelevant, candidate=candidate)
        if after_generate and "extend factual" in system:
            after_generate()
        return answer
    monkeypatch.setattr(inc, "local_complete", complete)
    return calls


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(night, "_root", lambda: tmp_path)
    monkeypatch.setattr(night.graphs, "load_config", lambda: {})
    monkeypatch.setattr(night, "generator_label", lambda config: "")
    monkeypatch.setattr(night.live_gate, "night_window_open", lambda *args, **kwargs: True)
    for name in (*night.graphs.ENV_GRAPH_NAMES, night.live_gate.NIGHTLY_UNTIL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    return tmp_path


def _run(graph, **kwargs):
    return night.run(graph, LOCAL, full=False, dry=False, limit=None, **kwargs)


@pytest.mark.parametrize("invalid", [None, 0, 1, "irrelevant", [], {}])
def test_unknown_source_classification_cannot_stamp_a_semantic_rejection_or_acceptance(isolated, monkeypatch, invalid):
    graph, path = _world(isolated)
    _source(graph)
    before = path.read_bytes()
    dossier.rebuild_index(graph, schema=CHAROITE)
    index_before = dossier.load_index(path.parent)
    calls = _transport(monkeypatch)
    monkeypatch.setattr(inc, "source_relevance", lambda *args: invalid)
    result = _run(graph)
    assert result["собрано"] == 0 and result["отказы"] == 1 and calls == []
    assert path.read_bytes() == before and dossier.composition(path.read_text(), schema=CHAROITE) == [A]
    assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
    assert dossier.rejection_versions(path.read_text()) == {}
    assert dossier.load_index(path.parent) == index_before and not (path.parent / ".backup").exists()


def test_rejection_only_update_retains_prior_versions_and_repeat_is_a_noop(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph, X, "An unrelated recipe.")
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    text = frontmatter.set_list_field(path.read_text(), dossier.REJECTED_FIELD, [X])
    version = dossier.rejection_version(text, X, snap.files[X])
    text = frontmatter.set_list_field(text, dossier.REJECTION_VERSIONS_FIELD, [f"{version}:{X}"])
    path.write_text(text)
    before = dossier.load_dossier(path)
    _source(graph, Y, "Another unrelated recipe.")
    calls = _transport(monkeypatch, irrelevant=(X, Y))
    result = _run(graph)
    after = dossier.load_dossier(path)
    assert result["собрано"] == 1 and len(calls) == 1
    assert after.parsed.body == before.parsed.body and after.parsed.manual == before.parsed.manual
    assert frontmatter.list_field(after.text, dossier.REJECTED_FIELD) == [X, Y]
    assert dossier.rejection_versions(after.text)[X] == version
    assert set(dossier.rejection_versions(after.text)) == {X, Y}
    before_bytes, backups = path.read_bytes(), list(path.parent.glob(".backup/*/*.md"))
    calls.clear()
    assert _run(graph)["собрано"] == 0
    assert calls == [] and path.read_bytes() == before_bytes
    assert list(path.parent.glob(".backup/*/*.md")) == backups


def test_accepted_addition_keeps_composition_and_counts_canonical_source_targets(isolated, monkeypatch):
    graph, path = _world(isolated)
    # A qualified alias and a bare citation identify one existing source.
    path.write_text(path.read_text().replace("## Правки автора", f"- **Встречи:** [[Встречи/{A}|earlier]]\n\n## Правки автора"))
    _source(graph)
    _transport(monkeypatch)
    assert _run(graph)["собрано"] == 1
    text = path.read_text()
    assert dossier.composition(text, schema=CHAROITE) == [A, B]
    assert int(frontmatter.scalar_field(text, "источников")) == 4  # topic, A, guide, B
    assert FACT in dossier.load_dossier(path).parsed.body


def test_first_validation_read_failure_does_not_create_a_backup(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph)
    before = path.read_bytes()
    original = dossier.scan_snapshot
    def scan(*args, **kwargs):
        if kwargs.get("warn_damaged") is False:
            raise PermissionError("Synthetic source became unreadable under the graph lock.")
        return original(*args, **kwargs)
    monkeypatch.setattr(dossier, "scan_snapshot", scan)
    _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_removal_after_locked_source_scan_is_detected_by_membership_replanning(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph)
    before = path.read_bytes()
    original = dossier.scan_snapshot
    def scan(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if kwargs.get("warn_damaged") is False:
            (graph / f"Встречи/{A}.md").unlink()
        return snapshot
    monkeypatch.setattr(dossier, "scan_snapshot", scan)
    _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


@pytest.mark.parametrize("change", ["new-redirect", "new-address-alias"])
def test_graph_identity_changes_during_generation_are_not_committed(isolated, monkeypatch, change):
    graph, path = _world(isolated)
    _source(graph)
    before = path.read_bytes()
    def concurrent_change():
        if change == "new-redirect":
            (graph / "Встречи/SourceAlias.md").write_text(f"# SourceAlias → [[Встречи/{A}]]\n")
        else:
            alias = isolated / "other-address"
            alias.symlink_to(graph, target_is_directory=True)
            monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(alias))
    _transport(monkeypatch, after_generate=concurrent_change)
    result = _run(graph)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_source_change_after_backup_reports_a_retry_in_both_counters(isolated, monkeypatch):
    graph, path = _world(isolated)
    new = _source(graph)
    before = path.read_bytes()
    original = dossier.backup
    def backup(*args, **kwargs):
        result = original(*args, **kwargs)
        new.write_text(new.read_text() + "Concurrent new fact.\n")
        return result
    monkeypatch.setattr(dossier, "backup", backup)
    _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 0 and result["пропущено"] == 1 and result["досье_изменилось"] == 1
    assert path.read_bytes() == before
    assert next(path.parent.glob(".backup/*/*.md")).read_bytes() == before


def test_failed_append_cost_uses_elapsed_time_so_another_graph_can_finish(isolated, monkeypatch):
    first, first_path = _world(isolated, "first")
    second, second_path = _world(isolated, "second")
    _source(first)
    _source(second)
    initial = first_path.read_bytes()
    ticks, calls = {"now": 1000.0}, []
    monkeypatch.setattr(night, "clock", lambda: ticks["now"])
    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append(system)
        if len(calls) == 1:
            ticks["now"] += 20
            raise TimeoutError("Synthetic first source judgment timeout.")
        return _answer(payload, system)
    monkeypatch.setattr(inc, "local_complete", complete)
    result = night.run_all([first, second], LOCAL, full=False, dry=False, budget_s=100)
    assert result.attempts == 2
    assert [g.built for g in result.graphs] == [0, 1]
    assert first_path.read_bytes() == initial and FACT in second_path.read_text()


@pytest.mark.parametrize("damage", ["unknown-body", "empty-version-identity"])
def test_unparsed_or_ambiguous_membership_holds_before_generation(isolated, monkeypatch, damage):
    graph, path = _world(isolated)
    _source(graph)
    if damage == "unknown-body":
        path.write_text(path.read_text().replace("## Сейчас", "## Today"))
    else:
        path.write_text(frontmatter.set_list_field(path.read_text(), dossier.REJECTION_VERSIONS_FIELD,
                                                  ["a" * 64 + ":[[#fragment]]"]))
    before = path.read_bytes()
    calls = _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 0 and result["пропущено"] == 1 and calls == []
    assert result["правки_повреждены"] == int(damage == "unknown-body")
    assert path.read_bytes() == before


def test_a_damaged_new_source_is_refused_before_any_model_call(isolated, monkeypatch):
    graph, path = _world(isolated)
    source = _source(graph)
    source.write_bytes(source.read_bytes() + b"damaged\xfftail")
    before = path.read_bytes()
    calls = _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 0 and result["отказы"] == 1 and calls == []
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_all_and_only_required_source_citations_are_a_valid_candidate(isolated, monkeypatch):
    body = BODY.replace(f" [[{A}]]", "").replace(" [[SensorGuide]]", "")
    graph, path = _world(isolated, body=body)
    _source(graph)
    _transport(monkeypatch)
    result = _run(graph)
    assert result["собрано"] == 1 and FACT in path.read_text()
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A, B]


@pytest.mark.parametrize("invalid", ["new-only-before", "empty-quote"])
def test_correction_without_prior_claim_or_nonempty_quote_never_reaches_judging(monkeypatch, invalid):
    text = dossier.render(THEME, BODY, [], {}, "fixture", "2099-01-01")
    loaded = dossier.DossierLoad("ok", text, text.encode())
    before = FACT if invalid == "new-only-before" else f"Vent A is open. [[{A}]]"
    change = {"before": before, "after": f"{FACT} [[{B}]]", "source": B,
              "quote": "" if invalid == "empty-quote" else FACT}
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("Invalid correction reached semantic judging."))
    with pytest.raises(inc.Refused, match="correction"):
        inc.validate_candidate(THEME, loaded, [B], {B: {"text": FACT}}, {}, _addition(), [change])


@pytest.mark.parametrize("damaged", ["replacement-character", "missing-heading"])
def test_candidate_format_failure_precedes_semantic_judging(monkeypatch, damaged):
    text = dossier.render(THEME, BODY, [], {}, "fixture", "2099-01-01")
    loaded = dossier.DossierLoad("ok", text, text.encode())
    body = _addition() + "\ufffd" if damaged == "replacement-character" else _addition().replace("## Открыто\n", "")
    monkeypatch.setattr(inc, "semantic_check", lambda *args: pytest.fail("Invalid candidate reached semantic judging."))
    with pytest.raises(inc.Refused):
        inc.validate_candidate(THEME, loaded, [B], {B: {"text": FACT}}, {}, body, [])


def test_legacy_rejection_cleanup_and_reader_preserve_unrelated_identities():
    text = frontmatter.set_list_field("---\nkind: synthetic\n---\nbody\n", dossier.REJECTION_VERSIONS_FIELD,
                                     [f":{A}", "opaque legacy", "opaque:LegacyName", "b" * 64, "a" * 64 + f":{B}"])
    cleaned = dossier.without_rejections(text, {A})
    assert frontmatter.list_field(cleaned, dossier.REJECTION_VERSIONS_FIELD) == [
        "opaque legacy", "opaque:LegacyName", "b" * 64, "a" * 64 + f":{B}"]
    assert dossier.rejection_versions(cleaned) == {B: "a" * 64}


@pytest.mark.parametrize("context", [1024, None])
def test_transport_respects_explicit_window_and_supplies_the_documented_default(monkeypatch, context):
    import llm
    calls = []
    class Local:
        engine, base, cloud_ready, _fell_back_local = "ollama", "http://127.0.0.1:11434", False, False
        def __init__(self, config):
            calls.append(config)
        def require_local_ollama_model(self, model=None):
            return model + ":local"
        def stream(self, prompt, **kwargs):
            assert kwargs["model"] == calls[-1]["llm"]["model"] + ":local"
            assert kwargs["truncate"] is False and kwargs["shift"] is False
            if kwargs["num_predict"] > 0:
                yield "synthetic local answer"
    monkeypatch.setattr(llm, "LLM", Local)
    config = {"llm": {**LOCAL["llm"]}}
    if context is None:
        config["llm"].pop("num_ctx")
        assert inc.local_complete("source", config, system="judge") == "synthetic local answer"
        seen = []
        assert inc.local_complete("source", config, system="judge", fell_back=seen) == "synthetic local answer"
        assert seen == [False]
    else:
        config["llm"]["num_ctx"] = context
        budget = (context - 100 - 256) * inc.ADMISSION_CHARS_PER_TOKEN
        with pytest.raises(inc.Refused, match="context budget"):
            inc.local_complete("x" * (budget + 1), config, system="judge", num_predict=100)
        assert calls == []


@pytest.mark.parametrize("remove,warning_present", [("- ⚠ Sensor is unverified.\n", False),
                                                     (f"- Vent A is open. [[{A}]]\n", True)])
def test_semantic_judge_receives_measured_warning_presence_not_ordinary_fact_presence(monkeypatch, remove, warning_present):
    old = dossier.render(THEME, BODY, [], {}, "fixture", "2099-01-01")
    loaded = dossier.DossierLoad("ok", old, old.encode())
    payloads = []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        payloads.append(payload)
        return json.dumps({"verdict": "reject", "conserved": False, "supported": False,
                           "corrections_valid": True, "reason": "Synthetic fixed-candidate refusal.", "claims": []})
    monkeypatch.setattr(inc, "local_complete", complete)
    with pytest.raises(inc.Refused):
        inc.semantic_check(THEME, loaded, [B], {B: {"text": FACT}}, {}, _addition().replace(remove, ""), [])
    assert payloads[0]["warning_conservation_passed"] is warning_present


def test_semantic_judge_receives_new_content_hint_without_old_lines_or_headings(monkeypatch):
    old = dossier.render(THEME, BODY, [], {}, "fixture", "2099-01-01")
    loaded = dossier.DossierLoad("ok", old, old.encode())
    payloads = []
    def complete(prompt, config, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        payloads.append(payload)
        return json.dumps({"verdict": "reject", "conserved": False, "supported": False,
                           "corrections_valid": True, "reason": "Synthetic fixed-candidate refusal.", "claims": []})
    monkeypatch.setattr(inc, "local_complete", complete)
    with pytest.raises(inc.Refused):
        inc.semantic_check(THEME, loaded, [B], {B: {"text": FACT}}, {}, _addition(), [])
    assert payloads[0]["new_statements"] == [f"- {FACT} [[{B}]]"]


def test_source_edit_refusal_explains_the_retry_without_advancing_the_dossier(isolated, monkeypatch, capsys):
    graph, path = _world(isolated)
    source = _source(graph)
    before = path.read_bytes()
    _transport(monkeypatch, after_generate=lambda: source.write_text(source.read_text() + "Concurrent edit.\n"))
    result = _run(graph)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()
    assert "источник изменён или заменён" in capsys.readouterr().out
