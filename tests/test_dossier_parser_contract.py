"""Task 818: synthetic user paths; generation doubles make zero real model calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from charoite_graph import dossier, frontmatter, graph_names
from charoite_schema import CHAROITE

BODY = "## Сейчас\n- discussed ##Правки автора\nbody-tail\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"
STAMP = "2026-07-15_1400"


@pytest.fixture(autouse=True)
def synthetic_forget_transport(сервер_лежит):
    return сервер_лежит("POST", "http://127.0.0.1:8100/forget")


def script(name):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def complete(body=BODY, manual="owner-note"):
    return dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-02").replace(
        "## Правки автора\n\n—\n", f"## Правки автора\n\n{manual}\n")


@pytest.fixture
def world(tmp_path, monkeypatch):
    nd = script("nightly_dossier")
    graph = tmp_path / "graph"
    for folder in ("Ядра", "Встречи", "Досье"):
        (graph / folder).mkdir(parents=True)
    (graph / "Ядра/Sample.md").write_text("# Sample\nsynthetic core\n")
    for i in range(2):
        (graph / f"Встречи/Sample_{i}.md").write_text("# Synthetic\n[[Ядра/Sample]] source\n")
    path = graph / "Досье/Sample.md"
    path.write_text(complete())
    calls = []

    def generate(*args, **kwargs):
        calls.append(args[0])
        return BODY

    monkeypatch.setattr(nd, "generate", generate)
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(nd, "_graph_snapshot", lambda graph, *a: nd.graphs.mutation_snapshot(graph, nd._root(), 0))
    return nd, graph, path, calls


def test_P01_actual_rebuild_is_stable(world):
    nd, graph, path, calls = world
    lengths = [len(path.read_text())]
    for _ in range(2):
        result = nd.run(graph, {}, full=True, dry=False, limit=12)
        assert result["собрано"] == 1
        lengths.append(len(path.read_text()))
    print("actual rebuild lengths:", lengths, "generation doubles:", len(calls), "real models: 0")
    assert lengths[1] == lengths[2]
    assert dossier.load_dossier(path).parsed.body.count("body-tail") == 1
    assert dossier.preserve_manual(path.read_text()) == "owner-note"


@pytest.mark.parametrize("bad", [BODY.rsplit("## Кто в теме", 1)[0], BODY.replace("## Решено", "## Открыто"), BODY + "\n```\n", BODY + "\u2028tail"])
@pytest.mark.parametrize("author_payload", [None, "", "  —  \n", "\n\nowner-note\n\n"])
def test_P03_actual_producer_closure(world, monkeypatch, bad, author_payload):
    nd, graph, path, calls = world
    if author_payload is not None:
        loaded = dossier.load_dossier(path)
        section = next(s for s in loaded.parsed.sections if s.title == "Правки автора")
        path.write_text(loaded.text[:section.content] + author_payload)
    before = path.read_bytes()
    attempts = []
    monkeypatch.setattr(nd, "generate", lambda *a, **k: attempts.append(k["temperature"]) or bad)
    result = nd.run(graph, {}, full=True, dry=False, limit=12)
    print("malformed producer built:", result["собрано"], "real models: 0")
    assert result["собрано"] == 0 and result["отказы"] == 1
    assert attempts == list(dossier.GEN_TEMPERATURES)
    assert path.read_bytes() == before
    assert not (path.parent / ".backup").exists()


@pytest.mark.parametrize("snapshot", [False, "old", "external"])
def test_E03_actual_forget_preserves_ambiguous(tmp_path, snapshot):
    forget = script("forget_meeting")
    root, graph = tmp_path / "data", tmp_path / "graph"
    (root / "transcripts").mkdir(parents=True)
    (graph / "Встречи").mkdir(parents=True)
    meeting = graph / f"Встречи/{STAMP}.md"
    meeting.write_text("# Synthetic meeting\n")
    if snapshot == "external":
        import charoite_paths
        charoite_paths.use_data_root(root, replace=True)
        base = charoite_paths.graph_backups(graph, "cloud_backup", root=root) / "run"
    else:
        base = graph / ".cloud_backup/run" if snapshot else graph
    path = base / "Досье/Sample.md"
    path.parent.mkdir(parents=True)
    original = (f"---\nотпечаток: old\nревизия: 2026-11-02\n---\n## Сейчас\n"
                f"- [[Встречи/{STAMP}]] synthetic fact\n## Правки автора\nowner-note\n").encode()
    path.write_bytes(original)
    plan = forget.plan(STAMP, root, graph)
    print("ambiguous forget edit/delete:", path in plan.edit, path in plan.delete, "snapshot:", snapshot)
    forget.apply(plan, yes=False)
    assert path.read_bytes() == original
    assert path not in plan.edit and path not in plan.delete
    assert any("Sample.md" in check and "руками" in check for check in plan.check)
    forget.apply(plan, yes=True)
    assert path.read_bytes() == original
    assert not meeting.exists()


def test_E03_forget_skips_edit_helpers_and_continues_other_files(tmp_path, monkeypatch):
    forget = script("forget_meeting")
    root, graph = tmp_path / "data", tmp_path / "graph"
    (root / "transcripts").mkdir(parents=True)
    (graph / "Встречи").mkdir(parents=True)
    (graph / f"Встречи/{STAMP}.md").write_text("# Synthetic meeting\n")
    folder = graph / "Досье"
    folder.mkdir()
    ambiguous = folder / "Ambiguous.md"
    raw = (f"---\nревизия: 2026-11-02\n---\n## Сейчас\n- [[Встречи/{STAMP}]] fact\n"
           "## Правки автора\nowner\n").encode()
    ambiguous.write_bytes(raw)
    automatic = folder / "Automatic.md"
    automatic.write_text(complete(body=BODY.replace("body-tail", f"body-tail [[Встречи/{STAMP}]]"), manual="—"))
    monkeypatch.setattr(forget, "_strip_meeting", lambda *a, **k: pytest.fail("ambiguous text reached strip"))
    monkeypatch.setattr(dossier, "without_revision", lambda *a, **k: pytest.fail("ambiguous text reached revision removal"))
    plan = forget.plan(STAMP, root, graph)
    assert ambiguous not in plan.edit and ambiguous not in plan.delete and automatic in plan.delete
    assert forget.apply(plan, yes=True)
    assert ambiguous.read_bytes() == raw and not automatic.exists()
    assert (graph / forget.BACKUP_DIR / STAMP / "Досье/Automatic.md").exists()


def test_P01_full_rebuild_preserves_author_indentation_and_new_warnings(world, monkeypatch):
    nd, graph, path, calls = world
    body = BODY.replace("body-tail", "body-tail ⚠️ [[Source]]")
    author = "    indented author code  \n### detail\nauthor-tail  "
    path.write_text(complete(body=body, manual=author))
    monkeypatch.setattr(nd, "generate", lambda *a, **k: body)
    for _ in range(2):
        assert nd.run(graph, {}, full=True, dry=False, limit=12)["собрано"] == 1
        text = path.read_text()
        assert body in text and author in text
        assert text.count("author-tail") == 1


def test_G01_B03_shared_load_provenance(tmp_path):
    path = tmp_path / "dossier.md"
    path.write_bytes(complete().encode())
    disk = dossier.load_dossier(path)
    direct = dossier.DossierLoad("ok", disk.text)
    assert direct.parsed == disk.parsed and direct.raw is None
    assert direct.refusal("edit") == "нет исходного снимка байтов"
    assert disk.parsed.complete and not disk.refusal("rebuild") and not disk.refusal("edit")
    assert dossier.split_dossier(direct.text) == (direct.parsed.head, direct.parsed.body, direct.parsed.sources, direct.parsed.manual)
    with pytest.raises(ValueError, match="снимка байтов"):
        direct.replace_body(BODY)
    assert disk.replace_body(BODY).encode() == path.read_bytes()
    sections = disk.parsed.sections
    assert sections[0].start == len(disk.parsed.head)
    assert all(a.end == b.start for a, b in zip(sections, sections[1:]))
    assert sections[-1].end == len(disk.text)


@pytest.mark.parametrize("level", [1, 2, 3, 6, 7])
@pytest.mark.parametrize("indent", ["", "  ", "\t"])
@pytest.mark.parametrize("gap", ["", " ", "\t"])
@pytest.mark.parametrize("tail", ["", "  ", "\t"])
def test_G04_protected_grammar(level, indent, gap, tail):
    heading = indent + "#" * level + gap + "Правки автора" + tail
    full = complete().replace("## Правки автора", heading)
    parsed = dossier.DossierLoad("ok", full)
    assert parsed.parsed.complete and parsed.parsed.manual == "owner-note"
    legacy = dossier.DossierLoad("ok", heading + "\nowner-note\n")
    assert legacy.parsed.manual == "owner-note" and legacy.refusal("rebuild")


@pytest.mark.parametrize("heading", ["## Сейчас", "##Сейчас", "  ## Сейчас", "\t##Сейчас"])
def test_G10_exact_body_grammar(heading):
    assert dossier.DossierLoad("ok", complete().replace("## Сейчас", heading)).parsed.complete
    assert not dossier.DossierLoad("ok", complete().replace("## Сейчас", heading + " extra")).parsed.complete


def test_G02_G03_G07_inline_and_manual_terminal_text():
    manual = "first-note\n- discussed ## Правки автора\nsecond-note\n### author subheading\ntail"
    text = complete(manual=manual).replace("\n## Правки автора\n", "\n##Правки автора\n")
    assert dossier.preserve_manual(text) == manual
    assert dossier.split_dossier(text)[1] == BODY
    assert dossier.DossierLoad("ok", text).parsed.complete
    inline = "см. ## Правки автора\nordinary tail\n"
    assert dossier.preserve_manual(inline) is None
    assert not dossier.DossierLoad("ok", inline).refusal("rebuild")
    assert dossier.DossierLoad("ok", inline).refusal("edit")


@pytest.mark.parametrize("fence", ["```", "~~~~", "   ````"])
def test_G08_G09_fences_and_quotes(fence):
    body = BODY.replace("body-tail", f"body-tail\n{fence} text\n## Правки автора\n## Источники\n{fence}\n> ## Источники\n >> ## Правки автора")
    # Construct stored bytes directly; model-output sanitization is a separate boundary.
    text = complete().replace(BODY, body)
    loaded = dossier.DossierLoad("ok", text)
    assert loaded.parsed.complete and loaded.parsed.body == body
    assert loaded.parsed.manual == "owner-note"
    assert dossier.DossierLoad("ok", text + "\n```\n").refusal("rebuild") == "незакрытый блок кода"


@pytest.mark.parametrize("text", [
    "## Источники\nbody-tail\n## Как пришли\nmore-body\n",
    "## Правки автора\nowner-note\n",
    complete().replace("## Источники\n\n", ""),
    complete().split("## Правки автора")[0],
    complete().replace("## Источники\n\n## Правки автора\n\nowner-note", "## Правки автора\nowner-note\n## Источники\nlinks"),
    complete() + "\n## Источники\nsecond-source-tail\n",
    complete() + "\n## Правки автора\nsecond-author-tail\n",
    complete().replace("## Решено", "## Решено extra"),
    complete().replace("## Решено", "## Кто в теме"),
    "outer remainder\n" + complete(),
])
def test_G05_G06_S01_S02_S03_S04_ambiguous_is_not_permission(text):
    loaded = dossier.DossierLoad("ok", text)
    assert not loaded.parsed.complete
    assert loaded.refusal("rebuild") and loaded.refusal("edit") and loaded.refusal("forget")
    assert dossier.kept_from_night(text)
    assert dossier.labeled_body(text) is None


@pytest.mark.parametrize("plain", ["", "# arbitrary text\n", "## Сейчас extra\ntext\n", BODY])
def test_W01_plain_has_distinct_admission(plain):
    loaded = dossier.DossierLoad("ok", plain)
    assert not loaded.refusal("rebuild")
    assert loaded.refusal("edit") and dossier.kept_from_night(plain)


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
@pytest.mark.parametrize("bom", ["", "\ufeff"])
@pytest.mark.parametrize("existing_revision", [False, True])
def test_B01_W03_W04_exact_splice(tmp_path, nl, bom, existing_revision):
    text = complete(manual="author\n### subheading\nprotected tail\n")
    if existing_revision:
        text = text.replace("tags:", "ревизия: '2026-10-30' # retain comment\ntags:")
    original = (bom + text).replace("\n", nl).encode()
    path = tmp_path / "dossier.md"
    path.write_bytes(original)
    loaded = dossier.load_dossier(path)
    fixed = BODY.replace("body-tail", "body-tail ⚠️ [[Source]]")
    expected = original.replace(BODY.replace("\n", nl).encode(), fixed.replace("\n", nl).encode())
    assert loaded.replace_body(fixed).encode() == expected
    revised = loaded.replace_body(fixed, "2026-11-03")
    assert dossier.revision(revised) == "2026-11-03"
    if existing_revision:
        assert revised.encode() == expected.replace(b"'2026-10-30'", b'"2026-11-03"')
    else:
        assert frontmatter.without_field(revised, dossier.REVISION_FIELD).encode() == expected


@pytest.mark.parametrize("newline", ["bare", "mixed", "unicode"])
def test_B02_unsupported_newlines_refuse(tmp_path, newline):
    text = complete()
    if newline == "bare":
        text = text.replace("\n", "\r")
    elif newline == "mixed":
        text = text.replace("\n", "\r\n", 1)
    else:
        text += "\u2028line\n"
    path = tmp_path / "dossier.md"
    path.write_bytes(text.encode())
    loaded = dossier.load_dossier(path)
    assert "переводы строк" in loaded.refusal("rebuild")
    assert "переводы строк" in loaded.refusal("edit")
    assert path.read_bytes() == text.encode()


def test_B04_missing_empty_unreadable(tmp_path, monkeypatch):
    path = tmp_path / "dossier.md"
    assert dossier.load_dossier(path).state == "missing"
    path.write_bytes(b"")
    assert dossier.load_dossier(path).state == "ok"
    monkeypatch.setattr(Path, "read_bytes", lambda p: (_ for _ in ()).throw(PermissionError("synthetic")))
    loaded = dossier.load_dossier(path)
    assert loaded.state == "unreadable" and loaded.refusal("rebuild")


@pytest.mark.parametrize("where", ["body", "fingerprint", "author", "author-heading"])
def test_D01_D02_raw_damage_boundary(tmp_path, where):
    raw = complete(manual="author literal \ufffd").encode()
    target = {"body": b"body-tail", "fingerprint": b"synthetic-fingerprint", "author": b"author literal", "author-heading": "Правки автора".encode()}[where]
    path = tmp_path / "dossier.md"
    path.write_bytes(raw.replace(target, target[:2] + b"\xff" + target[3:]))
    loaded = dossier.load_dossier(path)
    assert loaded.state == "damaged"
    assert bool(loaded.refusal("rebuild")) == (where in ("author", "author-heading"))
    assert loaded.refusal("edit")


def test_D03_valid_replacement_is_not_decode_damage(tmp_path):
    path = tmp_path / "dossier.md"
    path.write_text(complete(manual="author \ufffd"))
    loaded = dossier.load_dossier(path)
    assert loaded.state == "ok" and not loaded.refusal("rebuild")
    assert loaded.parsed.manual == "author \ufffd"


@pytest.mark.parametrize("revision", ["2026-99-99", "2026-11-03", "2026-11-0\ufffd", "literal \ufffd"])
def test_D04_revision_hold_is_shape_only(world, revision):
    nd, graph, path, calls = world
    text = dossier.with_revision(complete(), revision)
    path.write_text(text)
    for full in (False, True):
        result = nd.run(graph, {}, full=full, dry=False, limit=12)
        assert result["ревизия"] == 1 and result["собрано"] == 0
        assert path.read_text() == text and calls == []
    assert not (path.parent / ".backup").exists()


def test_R02_R03_R04_shared_reader_boundaries():
    text = complete(body=BODY.replace("body-tail", "Собеседник 3 решил"), manual="Собеседник 4")
    assert dossier.labeled_body(text) is True
    clean = text.replace("Собеседник 3 решил", "plain")
    assert dossier.labeled_body(clean) is False
    assert dossier.composition(clean, schema=CHAROITE) == []
    empty = dossier.with_composition(clean, [])
    assert dossier.composition(empty, schema=CHAROITE) == []
    malformed = empty.replace("состав: []", "состав:")
    assert dossier.composition(malformed, schema=CHAROITE) is None
    body_metadata = clean.replace("plain", "отпечаток: forged\nвход: forged\nревизия: 2026-11-03")
    assert dossier.read_fingerprint(dossier.DossierLoad("ok", body_metadata)) == "synthetic-fingerprint"
    assert dossier.read_input(dossier.DossierLoad("ok", body_metadata)) == ""
    assert dossier.revision(body_metadata) == ""


@pytest.mark.parametrize("labels_only", [False, True])
@pytest.mark.parametrize("existing_revision", [False, True])
def test_E01_P02_actual_review_splices_twice(world, monkeypatch, labels_only, existing_revision):
    nd, graph, path, calls = world
    ndr = script("nightly_dossier_review")
    monkeypatch.setattr(ndr.live_gate, "night_window_open", lambda *a, **k: True)
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Sample"]
    old = complete(manual="owner\n### detail\nprotected-tail").replace("synthetic-fingerprint", dossier.fingerprint_on_disk(members, files))
    if existing_revision:
        old = dossier.with_revision(old, "2026-10-30")
    path.write_bytes(("\ufeff" + old).replace("\n", "\r\n").encode())
    raw = path.read_bytes()
    fixed = BODY.replace("body-tail", "body-tail ⚠️ [[Source]]")
    monkeypatch.setattr(ndr, "review", lambda *a, **k: (fixed, ndr.LABELS_ONLY if labels_only else ""))
    cfg = {"sufler": {"cloud_enrich": True, "cloud_mask": False, "cloud_edit_graph": True}}
    stamp = "2026-11-03_030000"
    for _ in range(2):
        assert ndr._review_loop(graph, path.parent, {"Sample": members}, files, [path], stamp, "double", cfg,
                                dry=False, limit=1, may_edit=True, lock_dir=graph / "locks") == 1
    text = path.read_bytes()
    assert text.count(b"protected-tail") == 1 and text.count(b"owner") == 1
    assert dossier.load_dossier(path).parsed.complete
    if labels_only:
        assert text == raw.replace(BODY.replace("\n", "\r\n").encode(), fixed.replace("\n", "\r\n").encode())
    else:
        assert dossier.revision(text.decode()) == "2026-11-03"
    assert calls == []


def test_W02_review_refuses_ambiguity_before_cloud(tmp_path, monkeypatch):
    ndr = script("nightly_dossier_review")
    monkeypatch.setattr(ndr.privacy, "night_provider", lambda cfg: "anthropic")
    path = tmp_path / "dossier.md"
    path.write_text("## Сейчас\nbody\n## Источники\nlinks\n")
    monkeypatch.setattr(ndr.subprocess, "run", lambda *a, **k: pytest.fail("cloud must not run"))
    fixed, reason = ndr.review("Sample", path, tmp_path, {}, [], "double", {"sufler": {"cloud_enrich": True}})
    assert fixed is None and "неоднозначная" in reason


def test_E01_actual_review_labels_response(world, monkeypatch):
    _nd, graph, path, calls = world
    ndr = script("nightly_dossier_review")
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)["Sample"]
    labeled = BODY.replace("body-tail", "Собеседник 3 body-tail")
    old = complete(body=labeled).replace("synthetic-fingerprint", dossier.fingerprint_on_disk(members, files))
    path.write_bytes(old.replace("\n", "\r\n").encode())
    monkeypatch.setattr(ndr.privacy, "night_provider", lambda cfg: "anthropic")
    monkeypatch.setattr(ndr.live_gate, "night_window_open", lambda *a, **k: True)
    replies = []

    def reply(*a, **k):
        replies.append(1)
        return SimpleNamespace(returncode=0, stderr="", stdout=graph_names.unlabel_speakers(labeled))

    monkeypatch.setattr(ndr.subprocess, "run", reply)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_mask": False, "cloud_edit_graph": True}}
    assert ndr._review_loop(graph, path.parent, {"Sample": members}, files, [path], "2026-11-03_030000", "double", cfg,
                            dry=False, limit=1, may_edit=True, lock_dir=graph / "locks") == 1
    expected = old.replace(labeled, graph_names.unlabel_speakers(labeled)).replace("\n", "\r\n").encode()
    assert path.read_bytes() == expected and not dossier.revision(path.read_text())
    assert replies == [1] and calls == []


def test_F03_normalized_equal_bytes_changed_is_held(world, monkeypatch):
    nd, graph, path, calls = world
    original = path.read_bytes()

    def generate(*a, **k):
        path.write_bytes(original.replace(b"\n", b"\r\n"))
        return BODY

    monkeypatch.setattr(nd, "generate", generate)
    result = nd.run(graph, {}, full=True, dry=False, limit=12)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    assert path.read_bytes() == original.replace(b"\n", b"\r\n")
    assert not (path.parent / ".backup").exists()


@pytest.mark.parametrize("field", ["ревизия: [old]", "ревизия: old\nревизия: other"])
def test_F03_unsupported_scalar_mapping_refuses(tmp_path, field):
    path = tmp_path / "dossier.md"
    path.write_text(complete().replace("tags:", field + "\ntags:"))
    loaded = dossier.load_dossier(path)
    with pytest.raises(ValueError, match="единственным скаляром"):
        loaded.replace_body(BODY, "2026-11-03")


@pytest.mark.parametrize("variant", ["####### Правки автора", "  ## Правки автора", "\t## Правки автора", "####### Источники", "  ## Источники"])
def test_W05_new_read_variants_do_not_expand_deletion(variant):
    title = "Правки автора" if "Правки автора" in variant else "Источники"
    text = complete(manual="—").replace("## " + title, variant)
    assert dossier.DossierLoad("ok", text).parsed.complete
    assert dossier.kept_from_night(text) == "защищённый заголовок вне прежнего допуска удаления"


def test_W05_full_header_reader_does_not_expand_deletion():
    text = complete(manual="—").replace("отпечаток:", "extra: " + "x" * 650 + "\nотпечаток:")
    loaded = dossier.DossierLoad("ok", text)
    assert loaded.parsed.complete and dossier.read_fingerprint(loaded) == "synthetic-fingerprint"
    assert dossier.kept_from_night(text) == "нет отпечатка ночи"


@pytest.mark.parametrize("neighbor", ["тема: Sample", "тема: A: B"])
@pytest.mark.parametrize("field, scalar, held", [
    ("2026-11-03 # checked", "2026-11-03", True),
    ("'2026-11-03' # checked", "2026-11-03", True),
    ('"2026-11-03" # checked', "2026-11-03", True),
    ("\n  2026-11-03 # checked", "2026-11-03", True),
    ("# checked\n  '2026-11-03'", "2026-11-03", True),
    ("2026-99-99 # shape only", "2026-99-99", True),
    ("2026-11-0\ufffd # damaged", "2026-11-0\ufffd", True),
    ('"literal \\ufffd" # checked', "literal \ufffd", True),
    ("'2026-11-03 # literal' # outside", "2026-11-03 # literal", False),
    ("2026-11-03#literal", "2026-11-03#literal", False),
    ("false # no hold", "false", False),
])
def test_D04_revision_scalar_comment_with_invalid_neighbor(neighbor, field, scalar, held):
    text = f"---\n{neighbor}\nревизия: {field}\n---\n# Synthetic\n"
    assert frontmatter.scalar_field(text, "ревизия") == scalar
    assert dossier.revision(text) == (scalar if held else "")
    # A renderer-supported unquoted colon in another field remains readable.
    assert frontmatter.scalar_field(text, "тема") == neighbor.split(": ", 1)[1]


@pytest.mark.parametrize("full", [False, True])
def test_D04_actual_revision_comment_hold_with_invalid_neighbor(world, full):
    nd, graph, path, calls = world
    (graph / "Ядра/Other.md").write_text("# Other\nsynthetic core\n")
    for i in range(2):
        (graph / f"Встречи/Other_source_{i}.md").write_text("# Synthetic\n[[Ядра/Other]] source\n")
    original = complete().replace("тема: Sample", "тема: A: B\nревизия: 2026-11-03 # checked").encode()
    path.write_bytes(original)
    assert dossier.load_dossier(path).parsed.complete
    result = nd.run(graph, {}, full=full, dry=False, limit=12)
    assert path.read_bytes() == original
    assert result["ревизия"] == 1 and result["пропущено"] == 1
    assert result["собрано"] == 1 and result["отказы"] == 0
    assert calls == ["Other"]
    assert not list((path.parent / ".backup").glob("*/Sample.md"))
    assert (path.parent / "Other.md").exists()


@pytest.mark.parametrize("false_heading", [
    "## Источники\n## Правки автора\nfenced note",
    "#Источники\n######Правки автора\nfenced note",
    "##\nИсточники\n##\nПравки автора\nfenced note",
    "##\t\nИсточники\n# \n\tПравки автора\nfenced note",
])
@pytest.mark.parametrize("fenced", [False, True])
def test_W05_legacy_protected_matches_veto_new_deletion(false_heading, fenced):
    extra = f"```text\n{false_heading}\n```" if fenced else false_heading
    text = complete(manual="—").replace("body-tail", f"body-tail\n{extra}")
    loaded = dossier.DossierLoad("ok", text)
    if loaded.parsed.complete:
        assert loaded.parsed.manual == "—" and extra in loaded.parsed.body
    assert dossier.kept_from_night(text)


def test_W05_legacy_body_start_in_header_vetoes_new_deletion():
    text = complete(manual="—").replace("tags:", "note: |\n  ## Сейчас\n  Собеседник 3\ntags:")
    assert dossier.DossierLoad("ok", text).parsed.complete
    assert dossier.kept_from_night(text)


def test_W05_actual_forget_keeps_legacy_fenced_file_and_continues(tmp_path):
    forget = script("forget_meeting")
    root, graph = tmp_path / "data", tmp_path / "graph"
    (root / "transcripts").mkdir(parents=True)
    (graph / "Встречи").mkdir(parents=True)
    (graph / f"Встречи/{STAMP}.md").write_text("# Synthetic meeting\n")
    folder = graph / "Досье"
    folder.mkdir()
    path = folder / "Fenced.md"
    text = complete(manual="—").replace("body-tail", (
        f"body-tail [[Встречи/{STAMP}]]\n```text\n## Источники\n## Правки автора\nfenced note\n```"))
    path.write_text(text)
    automatic = folder / "Automatic.md"
    automatic.write_text(complete(manual="—").replace("body-tail", f"body-tail [[Встречи/{STAMP}]]"))
    plan = forget.plan(STAMP, root, graph)
    assert path not in plan.delete and path in plan.edit
    assert automatic in plan.delete
    assert any("Fenced.md" in line and "проверь сам" in line for line in plan.check)
    assert forget.apply(plan, yes=True)
    assert path.exists() and "fenced note" in path.read_text()
    assert not dossier.cites(path.read_text(), [STAMP])
    assert not automatic.exists()
    assert (graph / forget.BACKUP_DIR / STAMP / "Досье/Automatic.md").exists()


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("nl", ["\n", "\r\n"])
@pytest.mark.parametrize("author_payload", ["  —  \n", "", "\n\nowner-note\n\n", " \t\n\n", "—"])
def test_P01_actual_rebuild_retains_exact_author_payload_twice(world, full, nl, author_payload):
    import os

    nd, graph, path, calls = world
    loaded = dossier.load_dossier(path)
    section = next(s for s in loaded.parsed.sections if s.title == "Правки автора")
    path.write_bytes((loaded.text[:section.content] + author_payload).replace("\n", nl).encode())
    for iteration in range(2):
        # Each normal run sees a real changed synthetic source, not a SAME skip.
        source = graph / "Ядра/Sample.md"
        source.write_text(f"# Sample\nsynthetic source version {iteration}\n")
        os.utime(source, (1_700_000_000 + iteration, 1_700_000_000 + iteration))
        before = path.read_bytes()
        result = nd.run(graph, {}, full=full, dry=False, limit=12)
        assert result["собрано"] == 1 and result["отказы"] == 0
        rebuilt = dossier.load_dossier(path)
        assert rebuilt.parsed.complete
        author = next(s for s in rebuilt.parsed.sections if s.title == "Правки автора")
        assert rebuilt.text[author.content:] == author_payload
        assert any(copy.read_bytes() == before for copy in (path.parent / ".backup").glob("*/Sample.md"))
    assert calls == ["Sample", "Sample"]


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("old", [None, "", "# Plain previous dossier\n"])
def test_P01_actual_rebuild_without_author_section_keeps_default(world, full, old):
    import os

    nd, graph, path, calls = world
    if old is None:
        path.unlink()
    else:
        path.write_text(old)
    for iteration in range(2):
        source = graph / "Ядра/Sample.md"
        source.write_text(f"# Sample\nsynthetic source version {iteration}\n")
        os.utime(source, (1_700_000_000 + iteration, 1_700_000_000 + iteration))
        assert nd.run(graph, {}, full=full, dry=False, limit=12)["собрано"] == 1
        rebuilt = dossier.load_dossier(path)
        author = next(s for s in rebuilt.parsed.sections if s.title == "Правки автора")
        assert rebuilt.parsed.complete and rebuilt.text[author.content:] == "\n—\n"
    assert calls == ["Sample", "Sample"]


@pytest.mark.parametrize("author_payload", [None, "", "  —  \n", "\n\nowner-note\n\n"])
def test_G01_render_embeds_exact_author_payload(author_payload):
    default = dossier.render("Sample", BODY, [], {}, "synthetic-fingerprint", "2026-11-02")
    rendered = dossier.render("Sample", BODY, [], {}, "synthetic-fingerprint", "2026-11-02",
                              author_payload=author_payload)
    assert default.endswith("## Правки автора\n\n—\n")
    assert rendered == (default if author_payload is None else default.removesuffix("\n—\n") + author_payload)
    assert dossier.DossierLoad("ok", rendered).parsed.complete
