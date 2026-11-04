"""Invented artifacts: real planning/writing with deterministic local responses.

These tests make no model-quality claim. The separate acceptance runner records
actual local generation and judging on the same finite scenario matrix.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from charoite_graph import dossier, frontmatter
import dossier_incremental as inc
from charoite_schema import CHAROITE

THEME = "Glasshouse"
A, B, X = "2099-01-01_100000", "2099-01-02_100000", "2099-01-03_100000"
BODY = (f"## Сейчас\n- Vent A is open. [[{A}]]\n## Как пришли\n—\n"
        "## Решено\n- Read the guide. [[SensorGuide]]\n"
        "## Открыто\n- ⚠ Sensor is unverified.\n## Кто в теме\n—")
NEW_BODY = BODY.replace("## Как пришли", f"- A rain cover was fitted. [[{B}]]\n## Как пришли")
LOCAL = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "invented", "num_ctx": 8192}}


def _script():
    spec = importlib.util.spec_from_file_location("nightly_dossier_843_test", Path(__file__).parents[1] / "scripts/nightly_dossier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def world(tmp_path, monkeypatch):
    nd = _script()
    graph = tmp_path / "graph"
    for folder in ("Ядра", "Встречи", "Документы", "Досье"):
        (graph / folder).mkdir(parents=True)
    (graph / f"Ядра/{THEME}.md").write_text(f"# {THEME}\n")
    (graph / f"Встречи/{A}.md").write_text(f"[[Ядра/{THEME}]] OLD_SOURCE_ONLY_SENTINEL\nVent A is open.\n")
    (graph / "Документы/SensorGuide.md").write_text(f"[[Ядра/{THEME}]] guide\n")
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    text = dossier.render(THEME, BODY, [THEME, A, "SensorGuide"], snap.files, "base", "2099-01-01",
                          author_payload="\nKeep purple labels.\n\n")
    text = dossier.with_composition(text, [A])
    path = graph / f"Досье/{THEME}.md"
    path.write_text(text)
    monkeypatch.setattr(nd, "_root", lambda: tmp_path)
    monkeypatch.setattr(nd, "generator_label", lambda c: "synthetic")
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.delenv(nd.live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    return nd, graph, path


def _new(graph, name=B, body="A rain cover was fitted."):
    path = graph / f"Встречи/{name}.md"
    path.write_text(f"[[Ядра/{THEME}]]\n{body}\n")
    return path


def _responses(monkeypatch, *, body=NEW_BODY, irrelevant=(), failure=None):
    calls = []

    def complete(prompt, c, *, system, **kw):
        calls.append((system, prompt))
        if failure:
            raise failure
        data = json.loads(prompt.split("\n", 1)[0])
        if system == "You judge source relevance conservatively.":
            no = data["source"] in irrelevant
            return json.dumps({"verdict": "irrelevant" if no else "relevant", "reason": "invented judgment",
                               "quote": "" if no else data["source_text"].splitlines()[-1]})
        if system == "You extend factual dossiers conservatively.":
            return json.dumps({"body": body, "changes": []})
        if system == inc.SUPPORT_AUDIT_SYSTEM:
            return json.dumps({"verdict": "accept", "supported": True,
                               "reason": "Invented source support judgment."})
        if system == inc.TOPIC_AUDIT_SYSTEM:
            return json.dumps({"verdict": "accept", "relevant": True,
                               "reason": "Invented independent topic judgment."})
        return json.dumps({"verdict": "accept", "conserved": True, "supported": True,
                           "corrections_valid": True, "reason": "invented judgment",
                           "claims": [{"text": "A rain cover was fitted.", "source": B,
                                       "quote": "A rain cover was fitted.", "relevant": True,
                                       "supported": True, "reason": "Invented source-backed addition."}]})
    monkeypatch.setattr(inc, "local_complete", complete)
    return calls


@pytest.mark.parametrize("revision,newline,bom", [(False, "\n", ""), (True, "\n", ""), (True, "\r\n", "\ufeff")])
def test_append_keeps_history_warning_manual_revision_and_only_new_input(world, monkeypatch, revision, newline, bom):
    nd, graph, path = world
    text = path.read_text()
    if revision:
        text = dossier.with_revision(text, "2099-01-01")
    path.write_bytes((bom + text.replace("\n", newline)).encode())
    old = dossier.load_dossier(path)
    _new(graph, body="padding " * 600 + "\nA rain cover was fitted. FULL_NEW_TAIL")
    calls = _responses(monkeypatch)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 1 and result["отказы"] == 0
    loaded = dossier.load_dossier(path)
    assert loaded.parsed.body == NEW_BODY
    assert loaded.parsed.manual == old.parsed.manual
    assert dossier.revision(loaded.text) == ("2099-01-01" if revision else "")
    assert dossier.composition(loaded.text, schema=CHAROITE) == [A, B]
    assert all("OLD_SOURCE_ONLY_SENTINEL" not in prompt for _, prompt in calls)
    assert all("FULL_NEW_TAIL" in prompt for _, prompt in calls)
    assert [system for system, _ in calls] == ["You judge source relevance conservatively.",
        "You extend factual dossiers conservatively.", "You are an independent conservative dossier judge.",
        inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM]
    before = path.read_bytes()
    backups = sorted((graph / "Досье/.backup").rglob("*.md"))
    calls.clear()
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 0
    assert calls == [] and path.read_bytes() == before
    assert sorted((graph / "Досье/.backup").rglob("*.md")) == backups


def test_mixed_sources_reject_independently_and_changed_rejection_retries(world, monkeypatch):
    nd, graph, path = world
    _new(graph)
    x = _new(graph, X, "A recipe uses cinnamon.")
    calls = _responses(monkeypatch, irrelevant=(X,))
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 1
    text = path.read_text()
    assert dossier.composition(text, schema=CHAROITE) == [A, B]
    assert frontmatter.list_field(text, dossier.REJECTED_FIELD) == [X]
    assert len(calls) == 6
    calls.clear()
    # X was judged against the old body. B changed that context, so the next
    # run reassesses X once rather than stamping an unseen context accepted.
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 1
    assert len(calls) == 1
    calls.clear()
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 0 and calls == []
    # Change full contents without changing mtime, beyond the old source slice.
    old_stat = x.stat()
    x.write_text(x.read_text() + "padding " * 600 + " changed tail\n")
    os.utime(x, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 1
    assert len(calls) == 1


def test_legacy_rejection_is_reassessed(world, monkeypatch):
    nd, graph, path = world
    _new(graph, X, "A recipe uses cinnamon.")
    path.write_text(frontmatter.set_list_field(path.read_text(), dossier.REJECTED_FIELD, [X]))
    calls = _responses(monkeypatch, irrelevant=(X,))
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["собрано"] == 1
    assert len(calls) == 1
    assert X in dossier.rejection_versions(path.read_text())


@pytest.mark.parametrize("body", [NEW_BODY.replace("- ⚠ Sensor is unverified.\n", ""),
                                  NEW_BODY.replace("- Read the guide. [[SensorGuide]]", "- Read [[SensorGuide]]."),
                                  NEW_BODY + "\n## Правки автора\nchanged\n"])
def test_bad_candidate_never_stamps_a_relevant_source(world, monkeypatch, body):
    nd, graph, path = world
    _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch, body=body)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["отказы"] == 1 and result["собрано"] == 0
    assert path.read_bytes() == before
    assert not (graph / "Досье/.backup").exists()


@pytest.mark.parametrize("failure", [TimeoutError("synthetic"), ValueError("malformed")])
def test_operational_failure_keeps_input_retryable(world, monkeypatch, failure):
    nd, graph, path = world
    _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch, failure=failure)
    assert nd.run(graph, LOCAL, full=False, dry=False, limit=None)["отказы"] == 1
    assert path.read_bytes() == before


def test_dossier_and_source_interleavings_fail_closed(world, monkeypatch):
    nd, graph, path = world
    source = _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch)
    original = inc.append

    def racing(*a, **kw):
        result = original(*a, **kw)
        stat = source.stat()
        source.write_text(source.read_text() + "changed source\n")
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return result
    monkeypatch.setattr(inc, "append", racing)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["досье_изменилось"] == 1 and path.read_bytes() == before
    assert not (graph / "Досье/.backup").exists()


def test_index_failure_recovers_from_disk_on_skip(world, monkeypatch):
    nd, graph, path = world
    _new(graph)
    calls = _responses(monkeypatch)
    original = dossier.write_index
    monkeypatch.setattr(dossier, "write_index", lambda *a: (_ for _ in ()).throw(OSError("synthetic")))
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 1 and result["индекс_не_обновлён"]
    before = path.read_bytes()
    calls.clear()
    monkeypatch.setattr(dossier, "write_index", original)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and calls == [] and path.read_bytes() == before
    assert dossier.load_index(graph / "Досье")[0]["тема"] == THEME


@pytest.mark.parametrize("config", [{"llm": {"engine": "cloud", "model": "invented"}},
                                    {"llm": {"engine": "ollama", "base_url": "https://example.invalid", "model": "invented"}}])
def test_nonlocal_transport_refused_before_llm_construction(config, monkeypatch):
    import llm
    monkeypatch.setattr(llm, "LLM", lambda c: pytest.fail("LLM constructed for nonlocal transport"))
    with pytest.raises((inc.Refused, RuntimeError)):
        inc.local_complete("data", config, system="judge")


def test_complete_context_overflow_refuses_without_call(monkeypatch):
    import llm
    monkeypatch.setattr(llm, "LLM", lambda c: pytest.fail("oversized prompt reached LLM"))
    with pytest.raises(inc.Refused, match="context budget"):
        inc.local_complete("x" * 9000, LOCAL, system="judge")


def test_unsupported_correction_ledger_refuses_before_judge(world, monkeypatch):
    _, graph, path = world
    _new(graph, body="Vent A may be closed.")
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    old = dossier.load_dossier(path)
    changed = NEW_BODY.replace("- A rain cover was fitted.", "- Vent A is now closed.")
    change = {"before": f"Vent A is open. [[{A}]]", "after": f"Vent A is now closed. [[{B}]]",
              "source": B, "quote": "This explicitly supersedes the old state."}
    monkeypatch.setattr(inc, "local_complete", lambda *a, **k: pytest.fail("unsupported quote reached judge"))
    with pytest.raises(inc.Refused, match="unsupported"):
        inc.validate_candidate(THEME, old, [B], snap.files, LOCAL, changed, [change])


def test_source_replacement_with_identical_bytes_and_mtime_is_stale(world, monkeypatch):
    nd, graph, path = world
    source = _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch)
    original = inc.append

    def racing(*a, **kw):
        body = original(*a, **kw)
        observed = source.stat()
        replacement = source.with_suffix(".swap")
        replacement.write_bytes(source.read_bytes())
        os.utime(replacement, ns=(observed.st_atime_ns, observed.st_mtime_ns))
        replacement.replace(source)
        return body
    monkeypatch.setattr(inc, "append", racing)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["досье_изменилось"] == 1 and path.read_bytes() == before
    assert not (graph / "Досье/.backup").exists()


def test_rebuild_stamps_only_meetings_actually_in_the_prompt(world, monkeypatch):
    nd, graph, path = world
    _new(graph)
    monkeypatch.setattr(dossier, "MAX_SOURCES", 2)
    supplied = []
    def generate(theme, members, files, c, **kw):
        supplied.extend(members)
        return BODY
    monkeypatch.setattr(nd, "generate", generate)
    result = nd.run(graph, LOCAL, full=True, dry=False, limit=None)
    assert result["собрано"] == 1 and len(supplied) == 2
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    expected = [name for name in supplied if CHAROITE.is_meeting_folder(snap.files[name]["kind"])]
    assert dossier.composition(path.read_text(), schema=CHAROITE) == expected
    assert A not in expected
    next_plan = dossier.plan(dossier.load_dossier(path), dossier.clusters(snap.files, snap.backlinks, schema=CHAROITE)[THEME],
                             snap, schema=CHAROITE, on_disk=False)
    assert A in next_plan.new


@pytest.mark.parametrize("key", [dossier.COMPOSITION_FIELD, dossier.REJECTED_FIELD, dossier.REJECTION_VERSIONS_FIELD])
def test_duplicate_membership_is_a_hold_without_generation(world, monkeypatch, key):
    nd, graph, path = world
    _new(graph)
    original = path.read_text().replace("tags:", f'{key}: ["{B}"]\n{key}: []\ntags:')
    path.write_text(original)
    calls = _responses(monkeypatch)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and calls == [] and path.read_text() == original


@pytest.mark.parametrize("key", [dossier.REJECTED_FIELD, dossier.REJECTION_VERSIONS_FIELD])
@pytest.mark.parametrize("value", ["null", "{}", "[null]", "[{}]", "[[nested]]"])
def test_unreadable_membership_is_retryable_hold(world, monkeypatch, key, value):
    nd, graph, path = world
    _new(graph)
    original = path.read_text().replace("tags:", f"{key}: {value}\ntags:")
    path.write_text(original)
    calls = _responses(monkeypatch)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and calls == [] and path.read_text() == original


@pytest.mark.parametrize("value", ["", " ", "[[]]", "[[|alias]]", "[[#fragment]]"])
@pytest.mark.parametrize("key", [dossier.COMPOSITION_FIELD, dossier.REJECTED_FIELD])
def test_empty_canonical_member_is_held_before_generation(world, monkeypatch, key, value):
    nd, graph, path = world
    _new(graph)
    # A hand-edited invalid scalar must not pass through the canonical setter,
    # which deliberately removes empty names and would turn the input into [].
    text = frontmatter.without_field(path.read_text(), key)
    path.write_text(text.replace("tags:", f"{key}: {json.dumps([value])}\ntags:"))
    before = path.read_bytes()
    calls = _responses(monkeypatch)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and calls == [] and path.read_bytes() == before


def test_rebuild_never_stamps_a_source_tail_that_was_not_supplied(world, monkeypatch):
    nd, graph, path = world
    _new(graph, body="padding " * 500 + "MATERIAL_TAIL")
    monkeypatch.setattr(dossier, "SRC_CHARS", 20)
    observed = []

    def generate(theme, members, files, config, **kwargs):
        prompt = dossier.build_prompt(theme, members, files, schema=CHAROITE)
        observed.append(prompt)
        return BODY

    monkeypatch.setattr(nd, "generate", generate)
    result = nd.run(graph, LOCAL, full=True, dry=False, limit=None)
    assert result["собрано"] == 1 and "MATERIAL_TAIL" in observed[0]
    assert B in dossier.composition(path.read_text(), schema=CHAROITE)
    # If the complete block exceeds the total budget it is not supplied or
    # stamped; a later plan still sees that meeting as new.
    monkeypatch.setattr(dossier, "PROMPT_CHARS", 100)
    result = nd.run(graph, LOCAL, full=True, dry=False, limit=None)
    assert result["собрано"] == 1
    assert B not in dossier.composition(path.read_text(), schema=CHAROITE)


@pytest.mark.parametrize("stage", ["generation", "judge"])
@pytest.mark.parametrize("fault", ["timeout", "malformed"])
def test_late_transport_failure_keeps_the_new_source_retryable(world, monkeypatch, stage, fault):
    nd, graph, path = world
    _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch)
    complete = inc.local_complete
    failures = []

    def failing(prompt, config, *, system, **kwargs):
        selected = "extend factual" in system if stage == "generation" else "independent conservative" in system
        if selected:
            failures.append(system)
            if fault == "timeout":
                raise TimeoutError("synthetic late model timeout")
            return "not JSON"
        return complete(prompt, config, system=system, **kwargs)

    monkeypatch.setattr(inc, "local_complete", failing)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert failures and result["отказы"] == 1 and result["собрано"] == 0
    assert path.read_bytes() == before
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
    assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
    assert not (path.parent / ".backup").exists()


def test_graph_alias_retarget_during_append_preserves_both_dossiers(world, monkeypatch, tmp_path):
    import shutil

    nd, graph, path = world
    _new(graph)
    other = tmp_path / "other-graph"
    shutil.copytree(graph, other)
    other_path = other / path.relative_to(graph)
    other_path.write_text(other_path.read_text().replace("Keep purple labels.", "Other author payload."))
    before, other_before = path.read_bytes(), other_path.read_bytes()
    alias = tmp_path / "selected-graph"
    alias.symlink_to(graph, target_is_directory=True)
    _responses(monkeypatch)
    original = inc.append

    def retarget(*args, **kwargs):
        candidate = original(*args, **kwargs)
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
        return candidate

    monkeypatch.setattr(inc, "append", retarget)
    result = nd.run(alias, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0
    assert path.read_bytes() == before and other_path.read_bytes() == other_before
    assert not (graph / "Досье/.backup").exists()
    assert not (other / "Досье/.backup").exists()


@pytest.mark.parametrize("failure", ["backup", "write"])
def test_append_backup_or_final_write_failure_keeps_membership(world, monkeypatch, failure):
    nd, graph, path = world
    _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch)
    if failure == "backup":
        monkeypatch.setattr(dossier, "backup", lambda *args: (_ for _ in ()).throw(OSError("synthetic")))
    else:
        monkeypatch.setattr(nd.safe_write, "write_text", lambda *args, **kwargs: False)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and path.read_bytes() == before
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
    assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
    assert dossier.load_index(path.parent)[0]["отпечаток"] == "base"


@pytest.mark.parametrize("race", ["author", "source-delete", "source-delete-after-backup"])
def test_append_revalidates_concurrent_author_and_deleted_source(world, monkeypatch, race):
    nd, graph, path = world
    new = _new(graph)
    before = path.read_bytes()
    _responses(monkeypatch)
    original = dossier.backup if race == "source-delete-after-backup" else inc.append
    edited = before.replace(b"Keep purple labels.", b"Concurrent author edit.")

    def racing(*args, **kwargs):
        candidate = original(*args, **kwargs)
        if race == "author":
            path.write_bytes(edited)
        else:
            new.unlink()
        return candidate

    monkeypatch.setattr(dossier if race == "source-delete-after-backup" else inc,
                        "backup" if race == "source-delete-after-backup" else "append", racing)
    result = nd.run(graph, LOCAL, full=False, dry=False, limit=None)
    assert result["собрано"] == 0
    assert path.read_bytes() == (edited if race == "author" else before)
    assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
