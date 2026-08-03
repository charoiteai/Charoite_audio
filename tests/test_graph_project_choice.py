"""Рабочая встреча обязана попадать в рабочий граф.

03.08 разговор про обновление инфраструктуры уехал в новый граф «Linux 1.8»:
модель честно придумала имя по содержанию, потому что списка существующих
проектов ей никто не давал. Инструкция «НЕ выдумывай новых проектов» в промпте
была — без перечня она не правило, а пожелание.

Раскол дорого стоит: встречи одного проекта расползаются по вольту, обратные
ссылки рвутся, а поиск по графу начинает врать умолчанием.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import graph_updater as gu  # noqa: E402


def _vault(tmp_path: Path, *names: str) -> Path:
    for n in names:
        (tmp_path / n).mkdir(parents=True, exist_ok=True)
        (tmp_path / n / "_MOC.md").write_text(f"# {n}\n", encoding="utf-8")
    return tmp_path


def test_known_graphs_lists_neighbours(tmp_path):
    vault = _vault(tmp_path, "Проект_Альфа", "Ремонт")
    (vault / "Просто папка").mkdir()          # без _MOC.md — не граф
    assert gu.known_graphs(vault / "Проект_Альфа") == ["Проект_Альфа", "Ремонт"]


def test_known_graphs_survives_missing_vault(tmp_path):
    assert gu.known_graphs(tmp_path / "нет" / "и" / "не было") == []


def test_spacing_and_case_do_not_split_a_project():
    known = ["Проект_Альфа", "Ремонт"]
    for spelling in ("Проект Альфа", "проект-альфа", "ПРОЕКТ_АЛЬФА", "  Проект альфа  "):
        assert gu.match_known(spelling, known) == "Проект_Альфа", spelling


def test_genuinely_new_name_is_not_forced_into_existing():
    assert gu.match_known("Ремонт кухни", ["Проект_Альфа", "Ремонт"]) is None


def test_empty_project_matches_nothing():
    assert gu.match_known("", ["Проект_Альфа"]) is None


def test_prompt_rule_names_the_existing_projects():
    rule = gu._project_rule(["Проект_Альфа", "Ремонт"], "Проект_Альфа")
    assert "Проект_Альфа" in rule and "Ремонт" in rule
    assert "по умолчанию: Проект_Альфа" in rule


def test_prompt_rule_sends_infrastructure_talk_to_work():
    """Ровно тот разговор, который уехал в «Linux 1.8»."""
    rule = gu._project_rule(["Проект_Альфа"], "Проект_Альфа")
    assert "инфраструктура" in rule and "серверы" in rule


def test_prompt_rule_keeps_a_door_for_personal_topics():
    # мультиграф — фича, а не баг: личное не должно течь в рабочий проект
    rule = gu._project_rule(["Проект_Альфа"], "Проект_Альфа")
    assert "нерабочего" in rule


def test_no_vault_no_rule():
    # пустой список — не повод писать в промпт «выбирай из: »
    assert gu._project_rule([], "Проект_Альфа") == ""
