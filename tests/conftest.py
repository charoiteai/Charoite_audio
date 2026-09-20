"""Общие фикстуры.

Снимки и карантин графа из тестов cloud_review ложились в настоящий
`backups/` репозитория: `backup_root()` берёт корень облачной ревизии, а он —
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

    Через публичную дверь канона, а не записью в приватный глобал: после
    упаковки тот же тест может импортировать канон из пакета — это другой
    объект модуля со своим состоянием, и запись «мимо двери» до него не
    достанет (круг 2 по коду №327, DS I3).
    """
    было = os.environ.get("CHAROITE_ROOT")
    charoite_paths.forget_data_root()
    try:
        yield
    finally:
        charoite_paths.forget_data_root()
        if было is not None:
            os.environ["CHAROITE_ROOT"] = было


@pytest.fixture(autouse=True)
def _data_root_in_tmp(tmp_path, monkeypatch):
    mod = sys.modules.get("cloud_review")
    if mod is not None:
        # корень данных облачной ревизии — функция канона (№327), подменяем её,
        # а не бывшую константу модуля
        monkeypatch.setattr(mod, "_root", lambda: tmp_path / "data")
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
