"""Общие фикстуры.

Снимки и карантин графа из тестов cloud_review ложились в настоящий
`backups/` репозитория: `backup_root()` берёт `cloud_review.ROOT`, а он —
корень кода. К 22.08 там лежало 69 каталогов «Работа-*» от тестовых графов.
Корень данных для каждого теста — его tmp_path.
"""
import os
import pathlib
import sys

import pytest

# `src/` в пути: фикстуры conftest трогают канон путей раньше, чем первый
# тест успеет вставить его себе сам.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import charoite_paths  # noqa: E402


@pytest.fixture(autouse=True)
def _корень_данных_не_протекает():
    """Корень процесса — состояние, а не аргумент: возвращаем как было.

    `charoite_paths.use_data_root` пишет и в модульную переменную, и в
    окружение (дети процесса читают его оттуда). Тест, который корень назвал,
    иначе оставил бы его следующему — и тот судил бы чужую установку, зеленея
    по чужой причине. Заодно снимаем внешнюю `CHAROITE_ROOT`: её экспортируют
    и приложение, и `scripts/nightly.sh`, а тест канона корней в таком шелле
    краснел бы по причине окружения (круг 1 по коду №327, DS M3).

    Руками, а не через `monkeypatch`: у теста и фикстуры он ОДИН, и
    `monkeypatch.undo()` в теле теста (так делает
    `test_graph_hygiene.py:1804`) откатил бы заодно и этот сброс — корень
    предыдущего теста вернулся бы посреди следующего.
    """
    был, было = charoite_paths._given, os.environ.get("CHAROITE_ROOT")
    charoite_paths._given = None
    os.environ.pop("CHAROITE_ROOT", None)
    try:
        yield
    finally:
        charoite_paths._given = был
        if было is None:
            os.environ.pop("CHAROITE_ROOT", None)
        else:
            os.environ["CHAROITE_ROOT"] = было


@pytest.fixture(autouse=True)
def _data_root_in_tmp(tmp_path, monkeypatch):
    mod = sys.modules.get("cloud_review")
    if mod is not None:
        monkeypatch.setattr(mod, "ROOT", tmp_path / "data")
    # Граф по умолчанию — пустой каталог в tmp (существует, чтобы
    # install_profile.graph_enabled не гас из-за отсутствия папки, но без
    # папок-маркеров: тесты «vault не найден» ждут ноль графов), а не рабочий
    # из config.yaml и не обход iCloud (graphs.roots): тест, который зовёт
    # инструмент без явного графа, не должен читать личные графы владельца
    # (№197: один тест forget_meeting читал 14 651 файл из iCloud, под
    # нагрузкой — 120 с). Ставится БЕЗ оглядки на окружение: экспортированная
    # в шелле переменная иначе молча возвращала тесты в рабочий граф (DS I1,
    # GLM M3 по #525). Тесты резолва графа снимают переменные сами.
    graph = tmp_path / "graph-isolated" / "Граф"
    graph.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(graph))
