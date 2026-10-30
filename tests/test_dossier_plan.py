"""`dossier.plan`: судьба тела досье по составу встреч, а не по отпечатку (№843)."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_graph import dossier, frontmatter  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402

THEME = "Настройка доступа"
OLD, NEW = "2026-07-22_1000", "2026-07-24_1100"
BODY = ("## Сейчас\n- токен получен\n## Как пришли\n- первая попытка\n## Решено\n—\n"
        "## Открыто\n- 403\n## Кто в теме\n- Пётр\n")


def _graph(tmp: pathlib.Path, *, new_meeting: bool = True) -> pathlib.Path:
    g = tmp / "Граф"
    for d in ("Ядра", "Встречи", "Люди"):
        (g / d).mkdir(parents=True)
    (g / "Ядра" / f"{THEME}.md").write_text(
        f"# {THEME}\n## Хроника\n- [[Встречи/{OLD}]]\n", encoding="utf-8")
    (g / "Встречи" / f"{OLD}.md").write_text(f"Обсуждали [[Ядра/{THEME}]], [[Люди/Пётр]].\n",
                                             encoding="utf-8")
    if new_meeting:
        (g / "Встречи" / f"{NEW}.md").write_text(f"Продолжение [[Ядра/{THEME}]].\n", encoding="utf-8")
    (g / "Люди" / "Пётр.md").write_text(f"Ведёт [[Ядра/{THEME}]].\n", encoding="utf-8")
    return g


def _dossier(snap: dossier.GraphSnapshot, members: list[str], body: str = BODY) -> str:
    return dossier.render(THEME, body, members, snap.files, "fp", "2026-07-23")


def _plan(text: str | None, members: list[str], snap: dossier.GraphSnapshot, **kw) -> dossier.Plan:
    loaded = dossier.DossierLoad("missing") if text is None else dossier.DossierLoad("ok", text)
    kw.setdefault("on_disk", False)
    return dossier.plan(loaded, members, snap, schema=CHAROITE, **kw)


@pytest.fixture
def world(tmp_path):
    snap = dossier.scan_snapshot(_graph(tmp_path), schema=CHAROITE)
    built = [THEME, OLD, "Пётр"]
    return snap, built, built + [NEW]


def test_scan_keeps_its_two_values(tmp_path):
    g = _graph(tmp_path)
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    assert dossier.scan(g, schema=CHAROITE) == (snap.files, snap.backlinks)


def test_composition_reads_meetings_of_sources_without_the_field(world):
    snap, built, _ = world
    assert dossier.composition(_dossier(snap, built), schema=CHAROITE) == [OLD]


def test_composition_field_wins_over_sources(world):
    snap, built, _ = world
    text = dossier.with_composition(_dossier(snap, built), [NEW, OLD])
    assert dossier.composition(text, schema=CHAROITE) == [OLD, NEW]
    assert frontmatter.list_field(text, dossier.COMPOSITION_FIELD) == [OLD, NEW]
    # пустой состав — не «нет поля»: Источники его не подменяют
    empty = dossier.with_composition(text, [])
    assert dossier.composition(empty, schema=CHAROITE) == []


def test_manual_dossier_has_no_composition():
    assert dossier.composition("# Досье руками\n## Сейчас\n- факт\n", schema=CHAROITE) is None


def test_missing_file_is_rebuilt(world):
    snap, _, members = world
    assert _plan(None, members, snap) == dossier.Plan("rebuild", "new")


def test_unreadable_file_is_held(world):
    snap, _, members = world
    loaded = dossier.DossierLoad("unreadable")
    assert dossier.plan(loaded, members, snap, schema=CHAROITE, on_disk=False).action == "hold"


def test_same_meetings_skip_even_if_other_members_changed(world):
    """Общий узел переписал описание или в тему пришло ядро — встреч не прибавилось:
    тело не трогаем (отпечаток с mtime пересобирал 68 % тем, №842)."""
    snap, built, _ = world
    text = _dossier(snap, built)
    assert _plan(text, built + ["Ещё ядро"], snap) == dossier.Plan("skip", "same")


def test_new_meeting_is_added(world):
    snap, built, members = world
    assert _plan(_dossier(snap, built), members, snap) == dossier.Plan("add", "appendable", (NEW,))


def test_new_meeting_is_added_under_revision(world):
    snap, built, members = world
    text = dossier.with_revision(_dossier(snap, built), "2026-07-23")
    assert _plan(text, members, snap).action == "add"


def test_rejected_meeting_is_not_new_again(world):
    snap, built, members = world
    text = frontmatter.set_list_field(_dossier(snap, built), dossier.REJECTED_FIELD, [NEW])
    assert _plan(text, members, snap) == dossier.Plan("skip", "same")


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_gone_meeting(world, held, action):
    snap, _, members = world
    text = _dossier(snap, members)
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    snap.files[NEW]["path"].unlink()
    # снимок ещё помнит файл: уход видит только сверка по диску
    assert _plan(text, members, snap).action == "skip"
    p = _plan(text, members, snap, on_disk=True)
    assert (p.action, p.reason, p.gone) == (action, "gone", (NEW,))


def test_meeting_turned_redirect_is_not_gone(tmp_path):
    """Встреча, ставшая заглушкой слияния, живёт под каноном (n843-in3 I4)."""
    g = _graph(tmp_path)
    (g / "Встречи" / "Старое имя.md").write_text(f"# Старое имя → [[Встречи/{OLD}]]\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    assert snap.redirects == {"Старое имя": OLD}
    text = dossier.with_composition(_dossier(snap, [THEME, OLD]), ["Старое имя"])
    assert _plan(text, [THEME, OLD], snap) == dossier.Plan("skip", "same")


def test_other_kinds_leaving_is_not_gone(world):
    snap, built, _ = world
    text = _dossier(snap, built)
    del snap.files["Пётр"]
    assert _plan(text, [THEME, OLD], snap).action == "skip"


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "skip")])
def test_force_rebuilds_only_unheld(world, held, action):
    snap, built, _ = world
    text = _dossier(snap, built)
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    assert _plan(text, built, snap, force=True).action == action


def test_force_spares_labeled_body(world):
    snap, built, _ = world
    text = _dossier(snap, built, BODY.replace("- Пётр", "- Собеседник 3 предложил план"))
    assert dossier.labeled_body(text)
    assert _plan(text, built, snap, force=True).action == "skip"


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_labeled_body_with_new_meeting(world, held, action):
    """Метку дописыванием не снять: тело собирается заново (n843-in3 I1)."""
    snap, built, members = world
    text = _dossier(snap, built, BODY.replace("- Пётр", "- Собеседник 3 предложил план"))
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    assert _plan(text, members, snap).action == action


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_grown_body(world, held, action):
    snap, built, members = world
    big = BODY.replace("- 403", "- 403 " + "х" * dossier.GROW_LIMIT)
    text = _dossier(snap, built, big)
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    p = _plan(text, members, snap)
    assert (p.action, p.reason) == (action, "grown")


@pytest.mark.parametrize("held", [False, True])
def test_manual_dossier_without_sources_is_held(world, held):
    """Досье без «Источников» собрано руками: писать в него ночь не вправе."""
    snap, _, members = world
    text = "---\nтип: досье\n---\n## Сейчас\n- факт\n"
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    assert _plan(text, members, snap) == dossier.Plan("hold", "no_composition")


def test_empty_file_is_rebuilt(world):
    snap, _, members = world
    assert _plan(" \n", members, snap) == dossier.Plan("rebuild", "new")


def test_body_without_now_heading_is_held(world):
    """Автор переименовал «## Сейчас» — тело не разобрано, дописывать некуда (out1 Sonnet I3)."""
    snap, built, members = world
    text = dossier.with_composition(_dossier(snap, built), [OLD]).replace("## Сейчас", "## Сегодня")
    assert _plan(text, members, snap) == dossier.Plan("hold", "unparsed", (NEW,))


def test_composition_survives_bom(world):
    """BOM перед шапкой: поле видно и читается — иначе все встречи «новые» (out1 Sonnet I1)."""
    snap, built, members = world
    text = dossier.with_composition("\ufeff" + _dossier(snap, built), [OLD])
    assert text.startswith("\ufeff---")
    assert dossier.composition(text, schema=CHAROITE) == [OLD]
    assert _plan(text, members, snap) == dossier.Plan("add", "appendable", (NEW,))


@pytest.mark.parametrize("value", ["", "~", "null"])
def test_composition_without_value_is_unreadable(world, value):
    snap, built, members = world
    text = _dossier(snap, built).replace("tags:", f"{dossier.COMPOSITION_FIELD}: {value}\ntags:", 1)
    assert frontmatter.has_field(text, dossier.COMPOSITION_FIELD)
    assert dossier.composition(text, schema=CHAROITE) is None
    assert _plan(text, members, snap) == dossier.Plan("hold", "no_composition")


def test_block_list_composition(world):
    snap, built, members = world
    text = _dossier(snap, built).replace("tags:", f"{dossier.COMPOSITION_FIELD}:\n  - {OLD}\ntags:", 1)
    assert dossier.composition(text, schema=CHAROITE) == [OLD]
    assert _plan(text, members, snap).action == "add"


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_damaged_file_is_never_appended(world, held, action):
    """Тело с U+FFFD: дописка вернула бы знаки замены в файл (out1 Haiku C1)."""
    snap, built, members = world
    text = _dossier(snap, built, BODY.replace("первая", "перв\ufffdя"))
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    p = dossier.plan(dossier.DossierLoad("damaged", text), members, snap, schema=CHAROITE, on_disk=False)
    assert (p.action, p.reason) == (action, "damaged")
    same = dossier.plan(dossier.DossierLoad("damaged", text), built, snap, schema=CHAROITE, on_disk=False)
    assert same.action == "skip"


def test_meeting_missing_from_snapshot_is_gone(tmp_path):
    snap = dossier.scan_snapshot(_graph(tmp_path, new_meeting=False), schema=CHAROITE)
    text = dossier.with_composition(_dossier(snap, [THEME, OLD]), [OLD, NEW])
    p = _plan(text, [THEME, OLD], snap)
    assert (p.action, p.reason, p.gone) == ("rebuild", "gone", (NEW,))


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_meeting_that_left_the_theme_is_gone(tmp_path, held, action):
    """Встреча сама больше не ссылается на ядро — файл жив, но тело её пересказывать
    не вправе (out1 Haiku I2)."""
    g = _graph(tmp_path)
    (g / "Встречи" / f"{NEW}.md").write_text("Продолжение без ссылки.\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    text = dossier.with_composition(_dossier(snap, [THEME, OLD, "Пётр"]), [OLD, NEW])
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    p = _plan(text, [THEME, OLD, "Пётр"], snap)
    assert (p.action, p.gone) == (action, (NEW,))


def test_fork_stub_is_not_gone(tmp_path):
    """Заглушка-развилка без одной цели — не уход (out1 Haiku I1)."""
    g = _graph(tmp_path)
    (g / "Встречи" / "Старое имя.md").write_text(
        f"# Старое имя → [[Встречи/{OLD}]] · [[Встречи/{NEW}]]\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    assert "Старое имя" in snap.stubs and "Старое имя" not in snap.redirects
    text = dossier.with_composition(_dossier(snap, [THEME, OLD]), [OLD, "Старое имя"])
    assert _plan(text, [THEME, OLD], snap) == dossier.Plan("skip", "same")


def test_stub_ring_does_not_hang(world):
    snap, built, _ = world
    ring = dossier.GraphSnapshot(snap.files, snap.backlinks, {"A": "B", "B": "A"}, frozenset())
    text = dossier.with_composition(_dossier(snap, built), [OLD, "A"])
    p = _plan(text, built, ring)
    assert (p.action, p.gone) == ("rebuild", ("A",))


def test_rejected_through_stub_stays_rejected(tmp_path):
    g = _graph(tmp_path)
    (g / "Встречи" / "Старое имя.md").write_text(f"# Старое имя → [[Встречи/{NEW}]]\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    text = frontmatter.set_list_field(_dossier(snap, [THEME, OLD]), dossier.REJECTED_FIELD, ["Старое имя"])
    assert _plan(text, [THEME, OLD, NEW], snap) == dossier.Plan("skip", "same")


def test_gone_wins_over_force(tmp_path):
    snap = dossier.scan_snapshot(_graph(tmp_path, new_meeting=False), schema=CHAROITE)
    text = dossier.with_revision(dossier.with_composition(_dossier(snap, [THEME, OLD]), [OLD, NEW]), "2026-07-23")
    assert _plan(text, [THEME, OLD], snap, force=True).action == "hold"


def test_grow_limit_counts_each_new_meeting(tmp_path):
    g = _graph(tmp_path)
    third = "2026-07-25_0900"
    (g / "Встречи" / f"{third}.md").write_text(f"Третья [[Ядра/{THEME}]].\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    built = [THEME, OLD, "Пётр"]
    base = len(dossier.split_dossier(_dossier(snap, built))[1])
    pad = dossier.GROW_LIMIT - dossier.ADD_CHARS - base
    text = _dossier(snap, built, BODY.replace("- 403", "- 403" + "х" * pad))
    assert len(dossier.split_dossier(text)[1]) + dossier.ADD_CHARS == dossier.GROW_LIMIT
    assert _plan(text, built + [NEW], snap).action == "add"
    assert _plan(text, built + [NEW, third], snap).reason == "grown"
    over = _dossier(snap, built, BODY.replace("- 403", "- 403" + "х" * (pad + 1)))
    assert _plan(over, built + [NEW], snap).reason == "grown"


def test_has_field_and_set_list_field_keep_crlf():
    text = "---\r\nтип: досье\r\n---\r\nтело\r\n"
    out = frontmatter.set_list_field(text, dossier.COMPOSITION_FIELD, ["a", "b"])
    assert "\r\nсостав: [\"a\", \"b\"]\r\n" in out
    assert frontmatter.has_field(out, dossier.COMPOSITION_FIELD)
    assert frontmatter.list_field(out, dossier.COMPOSITION_FIELD) == ["a", "b"]


@pytest.mark.parametrize("stub", [
    "# Старое имя\n\nДубль. Смерджен в канон.\n",                       # заглушка без цели
    f"# Старое имя → [[Встречи/{OLD}]] · [[Встречи/{NEW}]]\n",           # развилка, цели забыты
])
def test_stub_is_never_gone(tmp_path, stub):
    """Цели заглушки не судим: заглушка в составе — не уход (out3 C1)."""
    g = _graph(tmp_path)
    (g / "Встречи" / "Старое имя.md").write_text(stub, encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    assert "Старое имя" in snap.stubs and "Старое имя" not in snap.files
    text = dossier.with_composition(_dossier(snap, [THEME, OLD]), [OLD, "Старое имя"])
    for name in (NEW,):
        snap.files[name]["path"].unlink()
    p = _plan(text, [THEME, OLD], snap, on_disk=True)
    assert "Старое имя" not in p.gone, p


def test_stub_pointing_to_a_fork_is_not_gone(tmp_path):
    """A → B, B — развилка: цепочка кончается на заглушке, не уход (out3 I1)."""
    g = _graph(tmp_path)
    (g / "Встречи" / "A.md").write_text("# A → [[Встречи/B]]\n", encoding="utf-8")
    (g / "Встречи" / "B.md").write_text(f"# B → [[Встречи/{OLD}]] · [[Встречи/{NEW}]]\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    text = dossier.with_composition(_dossier(snap, [THEME, OLD, NEW]), [OLD, NEW, "A"])
    assert _plan(text, [THEME, OLD, NEW], snap) == dossier.Plan("skip", "same")


def test_live_file_named_like_a_stub_can_be_gone(tmp_path):
    """Имя занято и заглушкой, и живым файлом: судит живой файл (out2 Haiku I1)."""
    g = _graph(tmp_path)
    (g / "Люди" / f"{NEW}.md").write_text(f"# {NEW} → [[Встречи/{OLD}]]\n", encoding="utf-8")
    snap = dossier.scan_snapshot(g, schema=CHAROITE)
    assert NEW in snap.stubs and NEW in snap.files
    text = _dossier(snap, [THEME, OLD, NEW])
    snap.files[NEW]["path"].unlink()
    p = _plan(text, [THEME, OLD, NEW], snap, on_disk=True)
    assert (p.action, p.gone) == ("rebuild", (NEW,))


@pytest.mark.parametrize("value", ["# потом", "Null", "NULL"])
def test_composition_comment_or_null_is_unreadable(world, value):
    snap, built, members = world
    text = dossier.with_revision(_dossier(snap, built), "2026-07-23")
    text = text.replace("tags:", f"{dossier.COMPOSITION_FIELD}: {value}\ntags:", 1)
    assert _plan(text, members, snap) == dossier.Plan("hold", "no_composition")


def test_unparsed_hold_names_the_gone_meeting(tmp_path):
    """hold тела без «## Сейчас» всё равно называет забытое (out2 Haiku C3)."""
    snap = dossier.scan_snapshot(_graph(tmp_path, new_meeting=False), schema=CHAROITE)
    text = dossier.with_composition(_dossier(snap, [THEME, OLD]), [OLD, NEW]).replace("## Сейчас", "## Сегодня")
    p = _plan(text, [THEME, OLD], snap)
    assert (p.action, p.reason, p.gone) == ("hold", "unparsed", (NEW,))


def test_bom_only_file_is_rebuilt(world):
    snap, _, members = world
    assert _plan("\ufeff\n", members, snap) == dossier.Plan("rebuild", "new")


def test_composition_of_other_kind_leaving_is_not_gone(world):
    """В составе узел не встреча, и его нет в теме — не уход."""
    snap, built, _ = world
    text = dossier.with_composition(_dossier(snap, built), [OLD, "Пётр"])
    assert _plan(text, [THEME, OLD], snap) == dossier.Plan("skip", "same")


@pytest.mark.parametrize("value", ["[ ]", "[] # потом"])
def test_empty_composition_with_spaces_or_comment_is_empty(world, value):
    snap, built, members = world
    text = _dossier(snap, built).replace("tags:", f"{dossier.COMPOSITION_FIELD}: {value}\ntags:", 1)
    assert dossier.composition(text, schema=CHAROITE) == []
