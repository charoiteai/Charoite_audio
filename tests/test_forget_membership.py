"""№843: forgetting removes machine membership, including header-only traces."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import forget_meeting as forget  # noqa: E402
import charoite_paths  # noqa: E402
from charoite_graph import dossier, frontmatter  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402

STAMP = "2026-07-15_1400"
OTHER = "2026-07-16_1000"
ALIAS = "Прежняя запись"
BODY = ("## Сейчас\nСохранённый вывод.\n## Как пришли\n—\n"
        "## Решено\n—\n## Открыто\n⚠ Срок пока не подтверждён.\n## Кто в теме\n—")


@pytest.fixture(autouse=True)
def no_brain(сервер_лежит):
    return сервер_лежит("POST", "http://127.0.0.1:8100/forget")


def _world(tmp_path):
    root, graph = tmp_path / "data", tmp_path / "graph"
    (root / "transcripts").mkdir(parents=True)
    (graph / "Встречи").mkdir(parents=True)
    for stamp in (STAMP, OTHER):
        (root / "transcripts" / f"{stamp}.md").write_text(f"# Встреча {stamp}\n", encoding="utf-8")
        (graph / "Встречи" / f"{stamp}.md").write_text(f"# {stamp}\n", encoding="utf-8")
    return root, graph


def _dossier(path, *, manual="—", revised=False, fields="", newline="\n", bom=False, body=BODY):
    text = dossier.render("Тема", body, [OTHER], {OTHER: {"kind": "Встречи"}},
                          "abc123", "2026-11-03", author_payload=f"\n{manual}\n")
    text = text.replace("type: досье\n", "type: досье\n" + fields)
    if revised:
        text = dossier.with_revision(text, "2026-11-02")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((("\ufeff" if bom else "") + text.replace("\n", newline)).encode("utf-8"))
    return path


def _assert_no_phantom_member(path, graph):
    snap = dossier.scan_snapshot(graph, schema=CHAROITE)
    next_plan = dossier.plan(dossier.load_dossier(path), [OTHER], snap, schema=CHAROITE, on_disk=True)
    assert next_plan.gone == () and next_plan.new == (), next_plan


@pytest.mark.parametrize("encoding", ["inline", "block"])
@pytest.mark.parametrize("hand", ["manual", "revision"])
@pytest.mark.parametrize("newline,bom", [("\n", False), ("\r\n", True)])
def test_forget_clears_both_memberships_when_only_headers_name_the_meeting(
        tmp_path, encoding, hand, newline, bom):
    root, graph = _world(tmp_path)
    if encoding == "inline":
        fields = f'состав: ["{STAMP}", "{OTHER}"]\nне_принято: ["{STAMP}", "{OTHER}"]\n'
    else:
        fields = f'состав:\n  - "{STAMP}"\n  - "{OTHER}"\nне_принято:\n- "{STAMP}"\n- "{OTHER}"\n'
    path = _dossier(graph / "Досье" / "Тема.md", fields=fields, newline=newline, bom=bom,
                    manual="Ремарка автора" if hand == "manual" else "—", revised=hand == "revision")
    before = path.read_bytes()
    before_parts = dossier.load_dossier(path).parsed
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert path.read_bytes() == before, "plan must remain read-only"
    assert forget.apply(planned, yes=True) and not planned.refused

    after = path.read_bytes().decode("utf-8")
    assert frontmatter.list_field(after, "состав") == [OTHER]
    assert frontmatter.list_field(after, "не_принято") == [OTHER]
    assert not dossier.revision(after)
    loaded = dossier.load_dossier(path)
    assert loaded.parsed.body == before_parts.body
    assert loaded.parsed.manual == before_parts.manual
    assert loaded.parsed.sources == before_parts.sources
    assert after.startswith("\ufeff") == bom
    if newline == "\r\n":
        assert "\n" not in after.replace("\r\n", "")
    _assert_no_phantom_member(path, graph)
    again = forget.plan(STAMP, root, graph)
    assert path not in again.edit and path not in again.delete


@pytest.mark.parametrize("field", ["состав", "не_принято"])
def test_forget_resolves_supported_membership_links_and_redirect_aliases(tmp_path, field):
    root, graph = _world(tmp_path)
    (graph / "Встречи" / f"{ALIAS}.md").write_text(
        f"# {ALIAS} → [[Встречи/{STAMP}]]\n", encoding="utf-8")
    values = [STAMP, f"Встречи/{STAMP}.md#Итог", f"[[Встречи/{STAMP}.md#Итог|подпись]]",
              f"[[{STAMP}\\|подпись]]", f"Встречи/{ALIAS}.md", f"[[{ALIAS}|старое имя]]", OTHER]
    fields = f"{field}: [" + ", ".join(frontmatter.yaml_str(value) for value in values) + "]\n"
    # Explicit composition avoids legacy source inference when testing rejections.
    if field != "состав":
        fields += f'состав: ["{OTHER}"]\n'
    path = _dossier(graph / "Досье" / "Тема.md", revised=True, fields=fields)
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert forget.apply(planned, yes=True) and not planned.refused
    after = path.read_text(encoding="utf-8")
    assert frontmatter.list_field(after, field) == [OTHER]
    assert bool(dossier.revision(after)) == (field == "не_принято")
    _assert_no_phantom_member(path, graph)


def test_forget_removes_an_automatic_dossier_with_only_header_composition(tmp_path):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", fields=f'состав: ["{STAMP}"]\n')
    before = path.read_bytes()
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.delete and path in planned.copy_first and path not in planned.edit
    assert forget.apply(planned, yes=True) and not planned.refused
    assert not path.exists()
    # Existing recovery policy intentionally retains the original dossier.
    assert (graph / forget.BACKUP_DIR / STAMP / "Досье" / "Тема.md").read_bytes() == before


@pytest.mark.parametrize("spelling", ["bare", "wiki", "version", "version_wiki"])
@pytest.mark.parametrize("revised", [False, True])
def test_forget_keeps_accepted_body_and_revision_when_only_rejection_mentions_the_meeting(
        tmp_path, spelling, revised):
    root, graph = _world(tmp_path)
    name = f"[[Встречи/{STAMP}|подпись]]" if "wiki" in spelling else STAMP
    field = "не_принято_версии" if spelling.startswith("version") else "не_принято"
    identity = ("a" * 64 + ":" if spelling.startswith("version") else "") + name
    path = _dossier(graph / "Досье" / "Тема.md", revised=revised,
                    fields=f'состав: ["{OTHER}"]\n{field}: ["{identity}"]\n')
    before = dossier.load_dossier(path)
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert forget.apply(planned, yes=True) and not planned.refused
    after = dossier.load_dossier(path)
    assert frontmatter.list_field(after.text, field) == []
    assert after.parsed.body == before.parsed.body
    assert after.parsed.sources == before.parsed.sources
    assert after.parsed.manual == before.parsed.manual
    assert dossier.revision(after.text) == dossier.revision(before.text)
    _assert_no_phantom_member(path, graph)


@pytest.mark.parametrize("location", ["legacy", "external"])
def test_forget_cleans_snapshot_membership_using_its_own_redirects(tmp_path, location):
    root, graph = _world(tmp_path)
    base = (graph / ".cloud_backup" if location == "legacy" else
            charoite_paths.graph_backups(graph, "cloud_backup", root=root))
    snapshot = base / "2026-11-01_0300"
    (snapshot / "Встречи").mkdir(parents=True)
    # This alias has already disappeared from the current graph.
    (snapshot / "Встречи" / f"{ALIAS}.md").write_text(
        f"# {ALIAS} → [[Встречи/{STAMP}]]\n", encoding="utf-8")
    path = _dossier(snapshot / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{ALIAS}", "{OTHER}"]\nне_принято: ["{STAMP}"]\n')
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert forget.apply(planned, yes=True) and not planned.refused
    after = path.read_text(encoding="utf-8")
    assert frontmatter.list_field(after, "состав") == [OTHER]
    assert frontmatter.list_field(after, "не_принято") == []
    assert not dossier.revision(after)


def test_forget_removes_content_addressed_rejections_without_touching_other_entries(tmp_path):
    root, graph = _world(tmp_path)
    kept = "b" * 64 + ":" + OTHER
    gone = "a" * 64 + ":" + STAMP
    path = _dossier(graph / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{OTHER}"]\nне_принято_версии:\n  - "{gone}"\n'
                           f'  - "{kept}"\n  - "unrecognized legacy value"\n')
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit
    assert forget.apply(planned, yes=True) and not planned.refused
    after = path.read_text(encoding="utf-8")
    assert frontmatter.list_field(after, "не_принято_версии") == [kept, "unrecognized legacy value"]
    assert dossier.revision(after) == "2026-11-02"


def test_forget_does_not_touch_an_unrelated_membership_or_revision(tmp_path):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{OTHER}"]\nне_принято: ["{OTHER}"]\n'
                           f'не_принято_версии: ["{"b" * 64}:{OTHER}", "legacy"]\n')
    before = path.read_bytes()
    planned = forget.plan(STAMP, root, graph)
    assert path not in planned.edit and path not in planned.delete
    forget.apply(planned, yes=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["ambiguous", "encoding"])
def test_forget_reports_and_preserves_unreadable_or_ambiguous_header_membership(tmp_path, damage):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True, fields=f'состав: ["{STAMP}"]\n')
    if damage == "ambiguous":
        path.write_text(path.read_text(encoding="utf-8") + "\n## Источники\nЛичный хвост\n", encoding="utf-8")
    else:
        path.write_bytes(path.read_text(encoding="utf-8").encode("cp1251", "replace"))
    before = path.read_bytes()
    planned = forget.plan(STAMP, root, graph)
    assert path not in planned.edit and path not in planned.delete
    assert any("Досье/Тема.md" in note for note in planned.check)
    forget.apply(planned, yes=True)
    assert path.read_bytes() == before


def test_forget_retains_capture_gate_for_a_header_only_edit(tmp_path):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True, fields=f'состав: ["{STAMP}"]\n')
    planned = forget.plan(STAMP, root, graph)
    changed = path.read_bytes() + b"\nconcurrent author change\n"
    path.write_bytes(changed)
    assert not forget.apply(planned, yes=True) and planned.refused
    assert path.read_bytes() == changed
    assert (graph / "Встречи" / f"{STAMP}.md").exists()


@pytest.mark.parametrize("field", ["состав", "не_принято", "не_принято_версии"])
@pytest.mark.parametrize("forgotten_first", [False, True])
def test_forget_preserves_duplicate_membership_keys_in_either_order(tmp_path, field, forgotten_first):
    root, graph = _world(tmp_path)
    prefix = "a" * 64 + ":" if field == "не_принято_версии" else ""
    values = [prefix + STAMP, prefix + OTHER]
    if not forgotten_first:
        values.reverse()
    fields = "".join(f'{field}: ["{value}"]\n' for value in values)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True, fields=fields,
                    body=BODY.replace("Сохранённый вывод.", f"Вывод [[Встречи/{STAMP}]]."))
    before = path.read_bytes()
    planned = forget.plan(STAMP, root, graph)
    assert path not in planned.edit and path not in planned.delete
    assert any("Досье/Тема.md" in note for note in planned.check)
    forget.apply(planned, yes=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["состав", "не_принято", "не_принято_версии"])
@pytest.mark.parametrize("value", ["", "null", "[", "{legacy: unknown}",
                                   f'["{STAMP}", {{legacy: unknown}}]'])
def test_forget_preserves_incomplete_or_unsupported_membership_values(tmp_path, field, value):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True, fields=f"{field}: {value}\n",
                    body=BODY.replace("Сохранённый вывод.", f"Вывод [[Встречи/{STAMP}]]."))
    before = path.read_bytes()
    planned = forget.plan(STAMP, root, graph)
    assert path not in planned.edit and path not in planned.delete
    assert any("Досье/Тема.md" in note for note in planned.check)
    forget.apply(planned, yes=True)
    assert path.read_bytes() == before


def test_forget_writes_explicit_empty_membership_lists(tmp_path):
    root, graph = _world(tmp_path)
    path = _dossier(graph / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{STAMP}"]\nне_принято: ["{STAMP}"]\n')
    planned = forget.plan(STAMP, root, graph)
    assert forget.apply(planned, yes=True) and not planned.refused
    after = path.read_text(encoding="utf-8")
    assert "состав: []" in after and "не_принято: []" in after
    assert dossier.composition(after, schema=CHAROITE) == []
    next_plan = dossier.plan(dossier.load_dossier(path), [], dossier.scan_snapshot(graph, schema=CHAROITE),
                             schema=CHAROITE, on_disk=True)
    assert next_plan.gone == () and next_plan.new == ()


def test_forget_header_cleanup_preserves_inline_quoted_and_fenced_protected_heading_text(tmp_path):
    root, graph = _world(tmp_path)
    literal = ("Сохранённый вывод с текстом ## Источники.\n"
               "> ## Правки автора\n```markdown\n## Источники\n```\n")
    path = _dossier(graph / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{STAMP}", "{OTHER}"]\n')
    # Fixture is an already stored dossier, not a generator response: render's
    # legacy protected-section filter would truncate this literal code block.
    path.write_text(path.read_text(encoding="utf-8").replace("Сохранённый вывод.", literal, 1), encoding="utf-8")
    before = dossier.load_dossier(path)
    assert before.parsed.complete and not before.refusal("forget")
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit and path not in planned.delete
    assert forget.apply(planned, yes=True) and not planned.refused
    after = dossier.load_dossier(path)
    assert after.parsed.complete and after.parsed.body == before.parsed.body
    assert after.parsed.sources == before.parsed.sources and after.parsed.manual == before.parsed.manual


@pytest.mark.parametrize("snapshot", [False, True])
def test_unreadable_redirect_preserves_dossier_with_an_explicit_check(tmp_path, monkeypatch, snapshot):
    root, graph = _world(tmp_path)
    base = graph / ".cloud_backup/run" if snapshot else graph
    alias = base / "Встречи" / f"{ALIAS}.md"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_text(f"# → [[Встречи/{STAMP}]]\n")
    path = _dossier(base / "Досье/Тема.md", revised=True,
                    fields=f'состав: ["{ALIAS}", "{OTHER}"]\n')
    before = path.read_bytes()
    read = pathlib.Path.read_bytes

    def unreadable(selected):
        if selected == alias:
            raise PermissionError("synthetic unreadable redirect")
        return read(selected)

    monkeypatch.setattr(pathlib.Path, "read_bytes", unreadable)
    planned = forget.plan(STAMP, root, graph)
    assert path not in planned.edit and path not in planned.delete
    assert any("канонический состав графа не прочитан" in check for check in planned.check)
    assert forget.apply(planned, yes=True) and path.read_bytes() == before


def test_forget_removes_owned_old_seconds_stamp_and_keeps_the_neighbour(tmp_path):
    root, graph = _world(tmp_path)
    old, neighbour = STAMP + "23", STAMP + "45"
    (root / "transcripts" / f"{old}_live.md").write_text(f"# Встреча {STAMP}\n", encoding="utf-8")
    (root / "transcripts" / f"{neighbour}.md").write_text(f"# Встреча {neighbour}\n", encoding="utf-8")
    (graph / "Встречи" / f"{neighbour}.md").write_text(f"# {neighbour}\n", encoding="utf-8")
    path = _dossier(graph / "Досье" / "Тема.md", revised=True,
                    fields=f'состав: ["{old}", "{neighbour}", "{OTHER}"]\n')
    planned = forget.plan(STAMP, root, graph)
    assert path in planned.edit
    assert forget.apply(planned, yes=True) and not planned.refused
    after = path.read_text(encoding="utf-8")
    assert frontmatter.list_field(after, "состав") == [neighbour, OTHER]
    assert (graph / "Встречи" / f"{neighbour}.md").exists()
    next_plan = dossier.plan(dossier.load_dossier(path), [OTHER, neighbour],
                             dossier.scan_snapshot(graph, schema=CHAROITE), schema=CHAROITE, on_disk=True)
    assert next_plan.gone == () and next_plan.new == ()
