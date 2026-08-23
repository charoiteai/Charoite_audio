"""Единая точка «где граф» (карточка №36).

Относительный `sufler.graph_dir` считался Python-кодом от текущего каталога
процесса, а приложением — от папки данных: демон из приложения писал граф
в одно место, а ночные скрипты из launchd искали его в другом. Теперь все
24 места читают путь через `graphs.graph_dir`, и правило одно: `~`
раскрывается, относительный — от корня данных, SUFLER_GRAPH_DIR
перекрывает конфиг, пусто — None (а не «.», который молча лил граф в cwd).
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402


def test_resolve_relative_from_data_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)               # cwd больше не влияет
    assert graphs.resolve("demo/graph") == graphs.DATA_ROOT / "demo" / "graph"
    assert graphs.resolve("demo/graph", root=tmp_path) == tmp_path / "demo" / "graph"


def test_resolve_tilde_and_absolute():
    assert graphs.resolve("~/Vault/Work") == pathlib.Path.home() / "Vault" / "Work"
    assert graphs.resolve("/abs/graph") == pathlib.Path("/abs/graph")


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_resolve_empty_is_none_not_dot(raw):
    assert graphs.resolve(raw) is None


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv(graphs.ENV_GRAPH, "rel/test-graph")
    got = graphs.graph_dir({"sufler": {"graph_dir": "/cfg/graph"}})
    assert got == graphs.DATA_ROOT / "rel" / "test-graph"
    monkeypatch.setenv(graphs.ENV_GRAPH, "   ")           # пробельная = не задана
    assert graphs.graph_dir({"sufler": {"graph_dir": "/cfg/graph"}}) == pathlib.Path("/cfg/graph")


def test_config_shapes(monkeypatch):
    monkeypatch.delenv(graphs.ENV_GRAPH, raising=False)
    assert graphs.graph_dir({"sufler": {"graph_dir": "~/g"}}) == pathlib.Path.home() / "g"
    assert graphs.graph_dir({"sufler": None}) is None
    assert graphs.graph_dir({}) is None
    assert graphs.graph_dir("битый конфиг") is None
    monkeypatch.setenv(graphs.ENV_GRAPH, "/env/g")
    assert graphs.graph_dir({"sufler": {"graph_dir": "/cfg"}}, env=False) == pathlib.Path("/cfg")


def test_configured_graph_is_the_same_entry_point(monkeypatch):
    monkeypatch.setenv(graphs.ENV_GRAPH, "/env/only")
    assert graphs.configured_graph() == pathlib.Path("/env/only")


def test_both_env_names_same_priority(monkeypatch):
    # Приложение читает CHAROITE_GRAPH_DIR, Python — SUFLER_GRAPH_DIR; демон
    # наследует окружение приложения, поэтому оба имени обязаны совпадать по
    # приоритету (круг-1 по PR #385, DeepSeek).
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", "/app/g")
    assert graphs.graph_dir({"sufler": {"graph_dir": "/cfg"}}) == pathlib.Path("/app/g")
    monkeypatch.setenv("SUFLER_GRAPH_DIR", "/py/g")
    assert graphs.graph_dir({}) == pathlib.Path("/app/g")      # первое имя важнее
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", " ")
    assert graphs.graph_dir({}) == pathlib.Path("/py/g")       # пробельное = не задано
    assert graphs.env_override() == "/py/g"
    monkeypatch.setenv("SUFLER_GRAPH_DIR", "")
    assert graphs.env_override() is None
