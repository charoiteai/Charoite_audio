"""Общие фикстуры.

Снимки и карантин графа из тестов cloud_review ложились в настоящий
`backups/` репозитория: `backup_root()` берёт `cloud_review.ROOT`, а он —
корень кода. К 22.08 там лежало 69 каталогов «Работа-*» от тестовых графов.
Корень данных для каждого теста — его tmp_path.
"""
import sys

import pytest


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
