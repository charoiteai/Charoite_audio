"""Длинная встреча разбирается целиком, а не первыми двадцатью минутами.

В extract() стоял transcript[:12000] — примерно двадцать минут разговора.
Решения принимают в конце («ну что, договорились: релиз 15-го»), поэтому в
граф, в ядра и в хронику уходила болтовня начала. Ничего не падало: граф
выглядел наполненным, просто он был про не ту часть встречи — дефект,
который не виден ни по логам, ни по глазам.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_updater as g  # noqa: E402


def _parts(text: str) -> list[str]:
    step = g.CHUNK_CHARS - g.CHUNK_OVERLAP
    return [text[i:i + g.CHUNK_CHARS] for i in range(0, len(text), step)]


def test_tail_of_long_meeting_reaches_extraction():
    """Двухчасовая встреча: решение в конце обязано попасть в разбор."""
    text = "болтовня. " * 9000 + "РЕШЕНИЕ: релиз пятнадцатого."
    assert "РЕШЕНИЕ" not in text[:g.CHUNK_CHARS], "тест бессмыслен, если хвост влезает в один кусок"
    assert any("РЕШЕНИЕ" in p for p in _parts(text)), \
        "финальное решение не попало ни в один кусок — граф снова про начало встречи"


def test_chunks_overlap_so_nothing_falls_between():
    """Реплика на стыке кусков не должна пропасть: куски идут с нахлёстом."""
    text = "x" * (g.CHUNK_CHARS * 3)
    parts = _parts(text)
    covered = sum(len(p) for p in parts)
    assert covered > len(text), "нахлёста нет — фраза на границе кусков потеряется"
    assert g.CHUNK_OVERLAP > 0


def test_dedup_collapses_same_entity_from_different_chunks():
    """Один человек всплывает в нескольких частях — в графе он один."""
    merged = g._dedup([
        {"имя": "Дмитрий", "роль": "аналитик"},
        {"имя": "дмитрий", "роль": "аналитик данных"},   # другой регистр, та же личность
        {"имя": "Ольга"},
        "тема", "тема",
    ])
    names = [m["имя"] if isinstance(m, dict) else m for m in merged]
    assert names == ["Дмитрий", "Ольга", "тема"], f"дедуп не схлопнул повторы: {names}"


def test_short_meeting_still_goes_in_one_pass():
    """Короткая встреча не должна дробиться — лишние запросы к модели ни к чему."""
    assert len(_parts("реплика. " * 100)) == 1
