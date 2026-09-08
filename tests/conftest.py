"""Общие фикстуры.

Снимки и карантин графа из тестов cloud_review ложились в настоящий
`backups/` репозитория: `backup_root()` берёт `cloud_review.ROOT`, а он —
корень кода. К 22.08 там лежало 69 каталогов «Работа-*» от тестовых графов.
Корень данных для каждого теста — его tmp_path.
"""
import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _data_root_in_tmp(tmp_path, monkeypatch):
    mod = sys.modules.get("cloud_review")
    if mod is not None:
        monkeypatch.setattr(mod, "ROOT", tmp_path / "data")
    # Граф по умолчанию — пустой каталог в tmp, а не рабочий из config.yaml
    # и не обход iCloud (graphs.roots): тест, который зовёт инструмент без
    # явного графа, не должен читать личные графы владельца (№197: один
    # тест forget_meeting читал 14 651 файл из iCloud, под нагрузкой — 120 с).
    # Тесты самого резолва графа ставят и снимают переменные сами.
    if not any(os.environ.get(n, "").strip() for n in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR")):
        monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(tmp_path / "graph-isolated" / "Граф"))
