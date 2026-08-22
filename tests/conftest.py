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
