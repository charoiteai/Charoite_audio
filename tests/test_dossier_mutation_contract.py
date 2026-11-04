"""Synthetic boundary/API regressions for task 818's CI mutation survivors."""
from __future__ import annotations

import importlib.util
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys

import pytest

from charoite_graph import dossier, frontmatter

BODY = "## Сейчас\ncurrent\n## Как пришли\nhistory\n## Решено\ndecision\n## Открыто\nopen\n## Кто в теме\npeople"


def _load(name):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(name, scripts / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def complete(body=BODY, *, author="—"):
    return dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-03", author_payload=author)


def test_closed_header_does_not_hide_first_protected_physical_line(tmp_path):
    text = "---\nотпечаток: synthetic\n---\n## Правки автора\nowner text\n"
    path = tmp_path / "dossier.md"
    path.write_text(text)
    loaded = dossier.load_dossier(path)
    assert loaded.parsed.manual == "owner text"
    assert loaded.parsed.protected is True
    why = "неоднозначная структура защищённых разделов: неполный набор защищённых разделов"
    for operation in ("rebuild", "review", "edit", "forget"):
        assert loaded.refusal(operation) == why
    assert path.read_text() == text


@pytest.mark.parametrize("opening,closing,admitted", [
    ("~~~info`tick", "~~~", True),
    ("```info`tick", "```", False),
    ("   ```info`tick", "   ```", False),
])
def test_backtick_in_fence_info_obeys_opening_character(opening, closing, admitted):
    body = BODY.replace("current", f"current\n{opening}\n## Источники\n## Правки автора\n{closing}")
    # Stored text bypasses the model sanitizer: these are literal file contents.
    text = complete().replace(BODY, body)
    loaded = dossier.DossierLoad("ok", text)
    assert loaded.parsed.complete is admitted
    if admitted:
        assert loaded.parsed.body == body
        assert loaded.parsed.sources == ""
        assert loaded.parsed.manual == "—"
        assert loaded.refusal("rebuild") == ""
    else:
        assert loaded.refusal("rebuild") == "незакрытый блок кода"
        assert loaded.parsed.remainder == text


@pytest.mark.parametrize("change,detail", [
    (lambda text: text + "\n## Источники\nsecond\n", "повтор защищённого заголовка"),
    (lambda text: text.split("## Правки автора")[0], "неполный набор защищённых разделов"),
    (lambda text: text.replace("## Источники\n\n## Правки автора\n—", "## Правки автора\n—\n## Источники\n"), "обратный порядок защищённых разделов"),
    (lambda text: text.replace("\n## Сейчас\n", "\nunclassified prefix\n## Сейчас\n", 1), "неизвестная внешняя оболочка или остаток перед телом"),
    (lambda text: "\n".join(line for line in text.split("\n") if not line.startswith("отпечаток:")), "нет поля отпечатка в закрытой шапке"),
    (lambda text: text.replace("## Источники\n", "## Источники\n## Сейчас\nsource-looking text\n"), "заголовок тела внутри раздела Источники"),
    (lambda text: text.replace("## Решено\n", "## Решено extra\n"), "неполный или нарушенный порядок пяти разделов тела"),
])
def test_parse_refusal_names_the_specific_boundary_problem(change, detail):
    text = change(complete())
    loaded = dossier.DossierLoad("ok", text)
    why = f"неоднозначная структура защищённых разделов: {detail}"
    assert loaded.parsed.complete is False
    assert loaded.parsed.remainder == text
    for operation in ("rebuild", "review", "edit", "forget"):
        assert loaded.refusal(operation) == why
    assert dossier.kept_from_night(text) != ""


@pytest.mark.parametrize("operation", ["rebuild", "review", "forget"])
def test_admitted_refusal_api_returns_empty_string(operation):
    loaded = dossier.DossierLoad("ok", complete())
    assert loaded.refusal(operation) == ""


def test_replace_body_reports_missing_raw_snapshot_in_value_error():
    loaded = dossier.DossierLoad("ok", complete())
    with pytest.raises(ValueError, match="^нет исходного снимка байтов$"):
        loaded.replace_body(BODY)


@pytest.mark.parametrize("replacement,reason", [
    (BODY.replace("## Решено", "## Решено extra"), "неполный или нарушенный порядок пяти разделов тела"),
    (BODY + "\u2028literal separator", "ответ не образует полное досье"),
])
def test_replace_body_rejects_structural_or_newline_damage(tmp_path, replacement, reason):
    path = tmp_path / "dossier.md"
    original = complete().encode()
    path.write_bytes(original)
    loaded = dossier.load_dossier(path)
    with pytest.raises(ValueError, match=reason):
        loaded.replace_body(replacement)
    assert path.read_bytes() == original


@pytest.mark.parametrize("line,expected", [
    ("ревизия:", 'ревизия: "2026-11-03"'),
    ("ревизия: ", 'ревизия: "2026-11-03" '),
    ("ревизия:  ", 'ревизия: "2026-11-03"  '),
    ("ревизия: old", 'ревизия: "2026-11-03"'),
    ("ревизия: 'old' # retain", 'ревизия: "2026-11-03" # retain'),
])
@pytest.mark.parametrize("newline,bom", [("\n", ""), ("\r\n", "\ufeff")])
def test_scalar_replacement_preserves_spacing_and_neighbors(line, expected, newline, bom):
    original = bom + f"---\nother: untouched\n{line}\nlast: stable\n---\nbody\n".replace("\n", newline)
    actual = frontmatter.with_scalar_preserving(original, "ревизия", "2026-11-03")
    assert actual == original.replace(line, expected)
    assert frontmatter.scalar_field(actual, "ревизия") == "2026-11-03"


@pytest.mark.parametrize("state", ["missing", "unreadable", "damaged"])
def test_review_unread_file_returns_named_failure_without_cloud(tmp_path, monkeypatch, state):
    ndr = _load("nightly_dossier_review")
    path = tmp_path / "dossier.md"
    if state != "missing":
        path.write_bytes(b"synthetic\xff" if state == "damaged" else b"synthetic")
    if state == "unreadable":
        monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError("synthetic")))
    # Fail if this refusal reaches any generation transport.
    monkeypatch.setattr(ndr.cloud, "claude_bin", lambda *a, **k: pytest.fail("unread file reached cloud"))
    result = ndr.review("Sample", path, tmp_path, {}, [], "double", {"sufler": {"cloud_enrich": True}})
    assert result == (None, f"сбой: досье не прочитано ({state})")


def test_keep_reason_exposes_parser_refusal_for_body_heading_in_sources():
    text = complete().replace("## Источники\n", "## Источники\n## Сейчас\nsource text\n")
    expected = "неоднозначная структура защищённых разделов: заголовок тела внутри раздела Источники"
    assert dossier.kept_from_night(text) == expected


def test_replace_body_preserves_the_specific_refusal_reason(tmp_path):
    text = complete() + "\u2028unsupported separator"
    path = tmp_path / "dossier.md"
    path.write_text(text)
    loaded = dossier.load_dossier(path)
    with pytest.raises(ValueError, match="^неподдерживаемые переводы строк$"):
        loaded.replace_body(BODY)
    assert path.read_text() == text


@pytest.mark.parametrize("bad", [
    BODY.rsplit("## Кто в теме", 1)[0],
    BODY.replace("## Решено", "## Открыто"),
    BODY + "\n```\n",
    BODY + "\u2028unsupported separator",
])
def test_actual_nightly_rejects_incomplete_rendered_candidate(tmp_path, monkeypatch, bad):
    nd = _load("nightly_dossier")
    graph = tmp_path / "graph"
    for folder in ("Ядра", "Встречи", "Досье"):
        (graph / folder).mkdir(parents=True)
    (graph / "Ядра/Sample.md").write_text("# Sample\nsynthetic core\n")
    for i in range(2):
        (graph / f"Встречи/Source_{i}.md").write_text("# Synthetic\n[[Ядра/Sample]] source\n")
    path = graph / "Досье/Sample.md"
    original = complete(author="exact author payload\n").encode()
    path.write_bytes(original)
    attempts = []
    monkeypatch.setattr(nd, "generate", lambda *a, **k: attempts.append(k["temperature"]) or bad)
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(nd, "_graph_snapshot", lambda graph, *a: nd.graphs.mutation_snapshot(graph, nd._root(), 0))
    result = nd.run(graph, {}, full=True, dry=False, limit=12)
    assert result["собрано"] == 0
    assert result["отказы"] == 1
    assert attempts == list(dossier.GEN_TEMPERATURES)
    assert path.read_bytes() == original
    assert not (path.parent / ".backup").exists()


def test_parse_result_cannot_be_changed_after_admission():
    loaded = dossier.DossierLoad("ok", complete())
    with pytest.raises(FrozenInstanceError):
        loaded.parsed.complete = False
    with pytest.raises(FrozenInstanceError):
        loaded.parsed.body = "different interpretation"
    assert loaded.parsed.complete is True
    assert loaded.parsed.body == BODY


@pytest.mark.parametrize("header,expected", [
    ('{"ревизия":"old", "other":"untouched"}', '{"ревизия":"2026-11-03", "other":"untouched"}'),
    ('{"ревизия":null, "other":"untouched"}', '{"ревизия":"2026-11-03", "other":"untouched"}'),
])
def test_flow_mapping_scalar_replacement_keeps_existing_colon_spacing(header, expected):
    original = f"---\n{header}\n---\nbody\n"
    assert frontmatter.with_scalar_preserving(original, "ревизия", "2026-11-03") == f"---\n{expected}\n---\nbody\n"


def test_author_body_headings_remain_author_payload_during_body_edit(tmp_path):
    author = "\n## Сейчас\nowner status\n## Решено\nowner decision\n"
    original = complete(author=author).encode()
    path = tmp_path / "dossier.md"
    path.write_bytes(original)
    loaded = dossier.load_dossier(path)
    assert loaded.parsed.complete is True
    assert loaded.parsed.body == BODY
    assert loaded.parsed.manual == author.strip()
    replacement = BODY.replace("current", "updated")
    assert loaded.replace_body(replacement).encode() == original.replace(BODY.encode(), replacement.encode())
    assert path.read_bytes() == original


def test_protected_section_before_first_body_is_not_the_body_end():
    text = "## Источники\nearlier source\n" + BODY + "\n## Правки автора\nowner\n"
    loaded = dossier.DossierLoad("ok", text)
    assert loaded.parsed.body == BODY
    assert loaded.parsed.manual == "owner"
    assert loaded.parsed.sources == "earlier source\n" + BODY
    assert loaded.parsed.complete is False
    assert loaded.refusal("rebuild") == "неоднозначная структура защищённых разделов: неизвестная внешняя оболочка или остаток перед телом"


def test_section_boundaries_cannot_be_changed_after_parsing():
    loaded = dossier.DossierLoad("ok", complete())
    section = loaded.parsed.sections[0]
    with pytest.raises(FrozenInstanceError):
        section.start = 0
    assert loaded.parsed.body == BODY


def test_load_debug_representation_omits_raw_byte_snapshot():
    raw = complete().encode()
    loaded = dossier.DossierLoad("ok", raw=raw)
    assert loaded.raw == raw
    assert "raw=" not in repr(loaded)
    assert "state='ok'" in repr(loaded)


@pytest.mark.parametrize("level", [1, 6])
def test_old_deletion_heading_levels_remain_eligible(level):
    text = complete().replace("## Источники", "#" * level + " Источники").replace("## Правки автора", "#" * level + " Правки автора")
    assert dossier.DossierLoad("ok", text).parsed.complete is True
    assert dossier.kept_from_night(text) == ""


def test_incomplete_body_has_explicit_deletion_hold_reason():
    text = complete().replace("## Решено", "## Решено extra")
    assert dossier.kept_from_night(text) == "неоднозначная структура защищённых разделов: неполный или нарушенный порядок пяти разделов тела"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_every_invalid_single_body_byte_keeps_confirmed_author_boundary(tmp_path, newline):
    path = tmp_path / "dossier.md"
    intact = complete(author="author literal �\n").replace("\n", newline).encode()
    for byte in range(0x80, 0x100):
        raw = intact.replace(b"current", b"current" + bytes([byte]), 1)
        path.write_bytes(raw)
        loaded = dossier.load_dossier(path)
        assert loaded.state == "damaged", byte
        assert loaded.parsed.complete is True, byte
        assert loaded.refusal("rebuild") == "", byte
        assert loaded.refusal("edit") == "Правки автора повреждены или не найдены", byte
        assert loaded.raw == raw
        assert path.read_bytes() == raw


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_damaged_author_returns_the_specific_hold_without_changing_bytes(tmp_path, newline):
    path = tmp_path / "dossier.md"
    raw = complete(author="owner payload\n").replace("\n", newline).encode().replace(b"owner payload", b"owner\xffpayload")
    path.write_bytes(raw)
    loaded = dossier.load_dossier(path)
    assert loaded.state == "damaged"
    assert loaded.parsed.complete is True
    assert loaded.refusal("rebuild") == "Правки автора повреждены"
    assert path.read_bytes() == raw


def test_complete_body_with_unsupported_newline_cannot_be_deleted():
    text = complete().replace("current", "current\u2028separator")
    loaded = dossier.DossierLoad("ok", text)
    assert loaded.parsed.complete is True
    assert dossier.kept_from_night(text) == "неподдерживаемые переводы строк"


@pytest.mark.parametrize("labels_only", [False, True])
@pytest.mark.parametrize("existing_revision", [False, True])
def test_actual_review_preview_and_write_use_the_same_revision_date(tmp_path, monkeypatch, labels_only, existing_revision):
    ndr = _load("nightly_dossier_review")
    graph = (tmp_path / "graph").resolve()
    folder = graph / "Досье"
    folder.mkdir(parents=True)
    path = folder / "Sample.md"
    text = complete(author="exact author\n").replace("synthetic-fingerprint", dossier.fingerprint_on_disk([], {}))
    if existing_revision:
        text = dossier.with_revision(text, "2026-10-30")
    raw = ("\ufeff" + text).replace("\n", "\r\n").encode()
    path.write_bytes(raw)
    fixed = BODY.replace("current", "updated ⚠️ [[Source]]")
    calls = []
    monkeypatch.setattr(ndr.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(ndr, "review", lambda *a, **k: calls.append(1) or (fixed, ndr.LABELS_ONLY if labels_only else ""))
    cfg = {"sufler": {"cloud_enrich": True, "cloud_mask": False, "cloud_edit_graph": True}}
    stamp = "2026-11-03_030000"
    assert ndr._review_loop(graph, folder, {"Sample": []}, {}, [path], stamp, "double", cfg,
                            dry=False, limit=1, may_edit=True, lock_dir=graph / "locks") == 1
    assert calls == [1]
    actual = path.read_bytes()
    assert dossier.load_dossier(path).parsed.complete is True
    assert actual.count(b"exact author") == 1
    assert (folder / ".backup" / stamp / path.name).read_bytes() == raw
    if labels_only:
        assert actual == raw.replace(BODY.replace("\n", "\r\n").encode(), fixed.replace("\n", "\r\n").encode())
        assert dossier.revision(actual.decode()) == ("2026-10-30" if existing_revision else "")
    else:
        assert dossier.revision(actual.decode()) == "2026-11-03"
