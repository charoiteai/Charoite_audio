"""Invented metadata, planning and forget roundtrips for task 843.

The only completion double returns source-irrelevant judgments. Scans, planning,
locks, backups, dossier writes and both indexes use the production path. Stored
version vectors below come from fixed UTF-8 inputs, independently of the reader.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dossier_incremental as inc
import forget_meeting as forget
import nightly_dossier as night
import charoite_paths
from charoite_graph import dossier, frontmatter
from charoite_schema import CHAROITE

THEME = "Синтетическая оранжерея"
A, B, C = "2099-01-01_1000", "2099-01-02_1000", "Синтетический журнал"
ALIAS = "Former synthetic log β"
BODY = ("## Сейчас\n- Synthetic orchidmarker is covered.\n"
        "## Как пришли\n- Испытана вымышленная створка.\n"
        "## Решено\n- Keep the violet marker.\n"
        "## Открыто\n- ⚠ Synthetic sensor remains unverified.\n"
        "## Кто в теме\n—")
MANUAL = "Keep mauve labels.\n    synthetic author code  \n### Detail\nmanual-tail"
# Frozen metadata compatibility vectors, not calls to the implementation under
# test: SHA-256 of the documented UTF-8 context/source/name envelope. B's physical
# CRLF source deliberately differs from its normalized text projection; C's
# Unicode canonical name also exercises the outer envelope's UTF-8 spelling.
VERSIONS = {
    B: "64233e209df8d4e27f9a611489b2b058eeeb35180ce1872bd80c9d0bbe660bbe",
    C: "da245ec1fb481aa4cd289fd3873188ace3ac2328d47e9e9ea35d02ce1ff1c02c",
}
B_TEXT_VERSION = "0f68da257169f8fcad0badd398cf962c7da31ed66762344bb28d398ab0eaff17"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root, graph = tmp_path / "data", tmp_path / "graph"
    charoite_paths.use_data_root(root, replace=True)
    (root / "transcripts").mkdir(parents=True)
    (root / "transcripts" / f"{B}.md").write_text(f"# Встреча {B}\n", encoding="utf-8")
    for folder in ("Ядра", "Встречи", CHAROITE.dossier_dir):
        (graph / folder).mkdir(parents=True)
    (graph / "Ядра" / f"{THEME}.md").write_text(f"# {THEME}\n", encoding="utf-8")
    sources = {
        A: f"[[Ядра/{THEME}]]\nA synthetic cover was fitted.\n",
        B: f"[[Ядра/{THEME}]]\r\nСинтетический рецепт не относится к створке.\r\n",
        C: f"[[Ядра/{THEME}]]\nA separate invented recipe.\n",
    }
    for name, text in sources.items():
        (graph / "Встречи" / f"{name}.md").write_bytes(text.encode("utf-8"))
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    text = dossier.render(THEME, BODY, [THEME, A], snap.files, "synthetic-fingerprint", "2099-01-08",
                          author_payload=f"\n{MANUAL}\n")
    text = dossier.with_revision(dossier.with_composition(text, [A]), "2099-01-09")
    text = frontmatter.set_list_field(text, dossier.REJECTED_FIELD, [B, C])
    text = frontmatter.set_list_field(text, dossier.REJECTION_VERSIONS_FIELD,
                                     [f"{VERSIONS[name]}:{name}" for name in (B, C)])
    path = graph / CHAROITE.dossier_dir / f"{THEME}.md"
    _store(path, text)
    monkeypatch.setattr(night, "_root", lambda: root)
    monkeypatch.setattr(night.graphs, "load_config", lambda: {})
    monkeypatch.setattr(night, "generator_label", lambda config: "")
    monkeypatch.setattr(night.live_gate, "night_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(night, "generate", lambda *args, **kwargs: pytest.fail("Metadata reached a full rebuild."))
    monkeypatch.setattr(inc, "local_complete", lambda *args, **kwargs: pytest.fail("Stable/held metadata reached a model."))
    monkeypatch.setattr(forget, "brain_forget", lambda *args, **kwargs: "")
    for name in (*night.graphs.ENV_GRAPH_NAMES, night.live_gate.NIGHTLY_UNTIL_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAROITE_ROOT", str(root))
    return root, graph, path


def _store(path, text):
    # All stored dossiers have a BOM and CRLF, including deliberately hand-edited
    # metadata. The production loader, not Path.read_text, projects their text.
    path.write_bytes(("\ufeff" + text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\n", "\r\n")).encode("utf-8"))


def _plan(graph, path, *, snapshot=None, force=False):
    snap = snapshot or dossier.scan_snapshot(graph, schema=CHAROITE)
    members = dossier.clusters(snap.files, snap.backlinks, schema=CHAROITE)[THEME]
    return dossier.plan(dossier.load_dossier(path), members, snap, schema=CHAROITE, on_disk=True, force=force)


def _indexes(graph):
    return {name: (graph / CHAROITE.dossier_dir / name).read_bytes()
            for name in (dossier.INDEX_JSON, dossier.INDEX_MD)}


def _seed_indexes(graph):
    dossier.rebuild_index(graph, schema=CHAROITE)
    return _indexes(graph)


def _envelope(raw):
    return frontmatter.split(raw.decode("utf-8").removeprefix("\ufeff"))[1]


def _replace_metadata(path, key, value):
    text = frontmatter.without_field(dossier.load_dossier(path).text, key)
    _store(path, text.replace("tags:", f"{key}: {value}\ntags:", 1))


@pytest.mark.parametrize("key,value", [
    (dossier.COMPOSITION_FIELD, f'["{A}", ""]'),
    (dossier.COMPOSITION_FIELD, '"[[unfinished"'),
    (dossier.REJECTED_FIELD, f'["{B}", ""]'),
    (dossier.REJECTED_FIELD, '"[[|label]]"'),
    (dossier.REJECTED_FIELD, f'["{B}", null]'),
    (dossier.REJECTION_VERSIONS_FIELD, "null"),
    (dossier.REJECTION_VERSIONS_FIELD, f'["{VERSIONS[B]}:{B}", null]'),
    (dossier.REJECTION_VERSIONS_FIELD, f'["{VERSIONS[B]}:{B}", {{legacy: unknown}}]'),
    (dossier.REJECTION_VERSIONS_FIELD, '["' + "a" * 64 + ':"]'),
    (dossier.REJECTION_VERSIONS_FIELD, '["' + "a" * 64 + ':[[|label]]"]'),
    (dossier.REJECTION_VERSIONS_FIELD, '["' + "a" * 64 + ':[[#fragment]]"]'),
    (dossier.REJECTION_VERSIONS_FIELD,
     f'["{VERSIONS[B]}:{B}"]\n{dossier.REJECTION_VERSIONS_FIELD}: []'),
], ids=["empty-composed-item", "malformed-composed-scalar", "empty-rejected-item", "empty-rejected-scalar",
        "null-rejected-item", "null-versions",
        "null-version-item", "mapping-version-item", "version-empty-name", "version-empty-target",
        "version-fragment-only", "duplicate-versions"])
def test_ambiguous_metadata_holds_before_night_and_forget_writes(world, key, value):
    root, graph, path = world
    _replace_metadata(path, key, value)
    before = dossier.load_dossier(path)
    assert before.parsed.complete
    if key != dossier.COMPOSITION_FIELD:
        assert dossier.composition(before.text, schema=CHAROITE) == [A]
    before_bytes, indexes = path.read_bytes(), _seed_indexes(graph)
    for full in (False, True):
        decision = _plan(graph, path, force=full)
        assert (decision.action, decision.reason) == ("hold", "ambiguous_membership")
        result = night.run(graph, {}, full=full, dry=False, limit=None)
        assert result["собрано"] == 0 and result["пропущено"] == 1
        assert path.read_bytes() == before_bytes and _indexes(graph) == indexes
    planned = forget.plan(B, root, graph)
    assert path not in planned.edit and path not in planned.delete
    assert any(path.name in note and "membership" in note for note in planned.check)
    assert forget.apply(planned, yes=True) and not planned.refused
    assert path.read_bytes() == before_bytes and _indexes(graph) == indexes
    after = dossier.load_dossier(path)
    assert (after.parsed.body, after.parsed.manual, dossier.revision(after.text)) == (
        before.parsed.body, before.parsed.manual, dossier.revision(before.text))
    assert not (path.parent / dossier.BACKUP_DIR).exists()


@pytest.mark.parametrize("tail", [[], [""], [f"opaque:{B}"], ["b" * 64, "opaque legacy"]],
                         ids=["saved", "empty-legacy-item", "opaque-collision", "no-envelope"])
def test_saved_utf8_rejections_survive_opaque_rows_and_bookkeeping_updates(world, tail):
    _, graph, path = world
    # Write the empty legacy item literally: the normal list setter drops it.
    rows = [f"{VERSIONS[B]}:{B}", f"{VERSIONS[C]}:[[Встречи/{C}.md#Итог|kept]]", *tail]
    _replace_metadata(path, dossier.REJECTION_VERSIONS_FIELD, json.dumps(rows, ensure_ascii=False))
    text = dossier.with_revision(dossier.load_dossier(path).text, "2099-02-01")
    text = frontmatter.with_field(text, "отпечаток", "new-bookkeeping-fingerprint")
    text = frontmatter.set_list_field(text, dossier.REJECTED_FIELD, [C, B])
    _store(path, text)
    before, indexes = path.read_bytes(), _seed_indexes(graph)
    assert _plan(graph, path) == dossier.Plan("skip", "same")
    result = night.run(graph, {}, full=False, dry=False, limit=None)
    assert result["собрано"] == 0 and result["пропущено"] == 1
    assert path.read_bytes() == before and _indexes(graph) == indexes
    assert not (path.parent / dossier.BACKUP_DIR).exists()


def test_saved_rejection_supports_the_legacy_snapshot_without_a_raw_digest(world):
    _, graph, path = world
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    # Older scan callers supply normalized text but no physical-byte hash.
    for meta in snap.files.values():
        meta.pop("sha256", None)
    _replace_metadata(path, dossier.REJECTION_VERSIONS_FIELD,
                      json.dumps([f"{B_TEXT_VERSION}:{B}", f"{VERSIONS[C]}:{C}"], ensure_ascii=False))
    before = path.read_bytes()
    assert _plan(graph, path, snapshot=snap) == dossier.Plan("skip", "same")
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "short-digest", "uppercase-digest", "opaque-digest",
                                    "missing-separator", "empty-digest", "source", "body", "manual"])
def test_retryable_rejection_is_rejudged_once_without_rewriting_accepted_content(world, monkeypatch, damage):
    _, graph, path = world
    version = VERSIONS[B]
    bad = {
        "short-digest": f"{version[:-1]}:{B}",
        "uppercase-digest": f"{version.upper()}:{B}",
        "opaque-digest": f"legacy:{B}",
        "missing-separator": version,
        "empty-digest": f":{B}",
    }
    if damage == "missing":
        _store(path, frontmatter.without_field(dossier.load_dossier(path).text, dossier.REJECTION_VERSIONS_FIELD))
    elif damage in bad:
        _replace_metadata(path, dossier.REJECTION_VERSIONS_FIELD,
                          json.dumps([bad[damage], f"{VERSIONS[C]}:{C}"], ensure_ascii=False))
    elif damage == "source":
        source = graph / "Встречи" / f"{B}.md"
        source.write_bytes(source.read_bytes() + b"Another invented recipe.\r\n")
    else:
        text = dossier.load_dossier(path).text
        text = text.replace("orchidmarker", "amendedmarker") if damage == "body" else text.replace("manual-tail", "amended-tail")
        _store(path, text)
    expected = (B, C) if damage in ("missing", "body", "manual") else (B,)
    decision = _plan(graph, path)
    assert (decision.action, decision.reason, decision.new) == ("add", "appendable", expected)
    before, indexes = dossier.load_dossier(path), _seed_indexes(graph)
    calls = []

    def irrelevant(prompt, config, *, system, **kwargs):
        assert "source relevance" in system, "Retry metadata must not generate an addition."
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append(payload["source"])
        return json.dumps({"verdict": "irrelevant", "reason": "Invented unrelated recipe.", "quote": ""})

    monkeypatch.setattr(inc, "local_complete", irrelevant)
    result = night.run(graph, {}, full=False, dry=False, limit=None)
    after = dossier.load_dossier(path)
    assert result["собрано"] == 1 and calls == list(expected)
    assert (after.parsed.body, after.parsed.sources, after.parsed.manual, dossier.revision(after.text)) == (
        before.parsed.body, before.parsed.sources, before.parsed.manual, dossier.revision(before.text))
    assert _envelope(after.raw) == _envelope(before.raw)
    assert after.raw.startswith(b"\xef\xbb\xbf") and b"\n" not in after.raw.replace(b"\r\n", b"")
    assert dossier.composition(after.text, schema=CHAROITE) == [A]
    assert _indexes(graph) == indexes
    assert _plan(graph, path) == dossier.Plan("skip", "same")
    original = before.raw
    backups = list((path.parent / dossier.BACKUP_DIR).glob(f"*/{path.name}"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    stable = path.read_bytes()
    calls.clear()
    assert night.run(graph, {}, full=False, dry=False, limit=None)["собрано"] == 0
    assert calls == [] and path.read_bytes() == stable and _indexes(graph) == indexes
    assert list((path.parent / dossier.BACKUP_DIR).glob(f"*/{path.name}")) == backups


def test_forget_removes_legacy_and_alias_versions_but_preserves_other_rejections(world):
    root, graph, path = world
    (graph / "Встречи" / f"{ALIAS}.md").write_text(
        f"# {ALIAS} → [[Встречи/{B}]]\n", encoding="utf-8")
    retained = [f"{VERSIONS[C]}:[[Встречи/{C}.md#Итог|kept]]", f"opaque:{C}",
                "opaque legacy", "opaque:OtherInventedName", "d" * 64]
    removed = [f"{VERSIONS[B]}:{B}", f":{B}", f"legacy:{B}",
               "a" * 64 + f":[[Встречи/{ALIAS}.md#Итог|former]]"]
    text = frontmatter.set_list_field(dossier.load_dossier(path).text, dossier.REJECTED_FIELD,
                                     [B, f"[[Встречи/{ALIAS}|former]]", C])
    text = frontmatter.set_list_field(text, dossier.REJECTION_VERSIONS_FIELD, removed + retained)
    _store(path, text)
    before, indexes = dossier.load_dossier(path), _seed_indexes(graph)
    assert _plan(graph, path) == dossier.Plan("skip", "same")
    planned = forget.plan(B, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert path.read_bytes() == before.raw, "Forget planning is read-only."
    assert forget.apply(planned, yes=True) and not planned.refused
    after = dossier.load_dossier(path)
    assert frontmatter.list_field(after.text, dossier.REJECTION_VERSIONS_FIELD) == retained
    assert frontmatter.list_field(after.text, dossier.REJECTED_FIELD) == [C]
    assert dossier.composition(after.text, schema=CHAROITE) == [A]
    assert (after.parsed.body, after.parsed.sources, after.parsed.manual, dossier.revision(after.text)) == (
        before.parsed.body, before.parsed.sources, before.parsed.manual, dossier.revision(before.text))
    assert _envelope(after.raw) == _envelope(before.raw)
    assert after.raw.startswith(b"\xef\xbb\xbf") and b"\n" not in after.raw.replace(b"\r\n", b"")
    # BOM-specific keyword projection is a separate concern. The accepted
    # dossier must keep its index identity and remain discoverable after forget.
    current_indexes = _indexes(graph)
    before_index = json.loads(indexes[dossier.INDEX_JSON])
    current_index = json.loads(current_indexes[dossier.INDEX_JSON])
    assert before_index["версия"] == current_index["версия"] == 1
    before_rows = {(row["тема"], row["файл"]): row for row in before_index["досье"]}
    current_rows = {(row["тема"], row["файл"]): row for row in current_index["досье"]}
    assert len(current_index["досье"]) == len(before_index["досье"]) == len(current_rows)
    assert current_rows.keys() == before_rows.keys()
    identity = (THEME, f"{CHAROITE.dossier_dir}/{path.name}")
    row = current_rows[identity]
    for key in ("тема", "файл", "источников", "собрано", "отпечаток"):
        assert row[key] == before_rows[identity][key]
    hits = dossier.lookup(path.parent, "orchidmarker", limit=None)
    assert [(hit["тема"], hit["файл"]) for hit in hits] == [identity]
    markdown = current_indexes[dossier.INDEX_MD].decode("utf-8")
    link = f"[[{row['файл'].removesuffix('.md')}\\|{row['тема']}]]"
    assert f"| {link} | {row['источников']} | {row['собрано']} |" in markdown
    assert _plan(graph, path) == dossier.Plan("skip", "same")
    assert (graph / forget.BACKUP_DIR / B / CHAROITE.dossier_dir / path.name).read_bytes() == before.raw
    again = forget.plan(B, root, graph)
    assert path not in again.edit and path not in again.delete
