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
    assert _plan(_dossier(snap, built), members, snap) == dossier.Plan("add", "meetings", (NEW,))


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


@pytest.mark.parametrize("held,action", [(False, "rebuild"), (True, "hold")])
def test_manual_dossier_without_sources(world, held, action):
    snap, _, members = world
    text = "---\nтип: досье\n---\n## Сейчас\n- факт\n"
    if held:
        text = dossier.with_revision(text, "2026-07-23")
    assert _plan(text, members, snap) == dossier.Plan(action, "no_composition")
