"""Две встречи одной минуты не должны затирать друг друга в графе.

Демон после краха поднимается за две секунды — обе записи попадают в одну
минуту, а весь конвейер строился от МИНУТНОГО штампа. Вторая встреча
затирала заметку первой, уводила её строку в MOC, забирала под свою тему
папку архива и файлы разбора, а метку отправки фактов
(`logs/brain_sent/{stamp}.txt`) видела как свою — и её собственные факты не
доезжали ни до узлов графа, ни до памяти (ревью 20.08, GLM; карточка №39).

Правило: минута достаётся самой ранней встрече, остальные живут под своим
посекундным штампом.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import graph_updater as gu  # noqa: E402

MINUTE = "2026-08-20_1430"
FIRST = "2026-08-20_143012"
SECOND = "2026-08-20_143047"


@pytest.fixture
def tdir(tmp_path):
    return tmp_path


def _meeting(tdir: pathlib.Path, name: str) -> None:
    (tdir / f"{name}.md").write_text("стенограмма", encoding="utf-8")


def test_одна_встреча_держит_минуту(tdir):
    _meeting(tdir, FIRST)
    assert gu.graph_stamp(MINUTE, FIRST, tdir) == MINUTE


def test_вторая_встреча_минуты_живёт_под_своей_секундой(tdir):
    _meeting(tdir, FIRST)
    _meeting(tdir, SECOND)

    assert gu.graph_stamp(MINUTE, FIRST, tdir) == MINUTE, "ранняя оставляет себе минуту"
    assert gu.graph_stamp(MINUTE, SECOND, tdir) == SECOND, (
        "поздняя обязана уйти под свой штамп, иначе затрёт заметку первой")


def test_переименованная_первая_не_отдаёт_минуту(tdir):
    """После наката темы главный файл первой носит минутное имя."""
    _meeting(tdir, f"{MINUTE}_Отчет_по_задачам")
    _meeting(tdir, SECOND)

    assert gu.graph_stamp(MINUTE, MINUTE, tdir) == MINUTE
    assert gu.graph_stamp(MINUTE, SECOND, tdir) == SECOND


def test_производные_файлы_не_считаются_встречами(tdir):
    """`_minutes`, `_разбор` и прочие хвосты — не вторая встреча."""
    _meeting(tdir, SECOND)
    for suffix in ("_minutes", "_разбор", "_hints", "_ревизия_claude"):
        _meeting(tdir, f"{SECOND}{suffix}")

    assert gu.graph_stamp(MINUTE, SECOND, tdir) == MINUTE, (
        "единственная встреча минуты не должна прятаться под посекундный штамп "
        "из-за собственных производных")


def test_легаси_встреча_без_секунд_держит_минуту(tdir):
    """До 28.07 штамп был пятнадцатизначным — сравнивать не с чем."""
    _meeting(tdir, MINUTE)
    assert gu.graph_stamp(MINUTE, MINUTE, tdir) == MINUTE


def test_имя_не_меняется_при_повторном_прогоне(tdir):
    """Пересборка и retry обязаны давать ТО ЖЕ имя: иначе повтор заведёт
    вторую заметку той же встречи и раздвоит её в графе."""
    _meeting(tdir, FIRST)
    _meeting(tdir, SECOND)

    assert gu.graph_stamp(MINUTE, SECOND, tdir) == gu.graph_stamp(MINUTE, SECOND, tdir)
    _meeting(tdir, f"{SECOND}_разбор")
    assert gu.graph_stamp(MINUTE, SECOND, tdir) == SECOND
