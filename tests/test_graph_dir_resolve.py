"""Единая точка «где граф» (карточка №36) и чей это корень (карточка №327).

Относительный `sufler.graph_dir` считался Python-кодом от текущего каталога
процесса, а приложением — от папки данных: демон из приложения писал граф
в одно место, а ночные скрипты из launchd искали его в другом. Теперь все
24 места читают путь через `graphs.graph_dir`, и правило одно: `~`
раскрывается, относительный — от корня данных, SUFLER_GRAPH_DIR
перекрывает конфиг, пусто — None (а не «.», который молча лил граф в cwd).

Сам корень данных называет точка входа (`charoite_paths.use_data_root`), а
выводом из положения модуля порядок заканчивается — это последняя догадка,
верная только пока модуль лежит в дереве репозитория. Поэтому ожидание здесь
строит фикстура.
Пока обе стороны считались одной формулой (`graphs.DATA_ROOT` против
`parent.parent` в проверяемом коде), перенос модуля двигал их вместе и тест
оставался зелёным на сломанном корне (GLM C3 по №327) — ровно тот случай,
который ловит `test_перенесённый_модуль_берёт_корень_у_вызывающего`.
"""
import pathlib
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import graphs  # noqa: E402

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def test_resolve_relative_from_given_root_not_cwd(tmp_path, monkeypatch):
    данные = (tmp_path / "данные").resolve()
    откуда_запустили = tmp_path / "откуда-запустили"
    откуда_запустили.mkdir(parents=True)
    monkeypatch.chdir(откуда_запустили)       # cwd не влияет
    charoite_paths.use_data_root(данные)
    assert graphs.resolve("demo/graph") == данные / "demo" / "graph"
    чужой = tmp_path / "чужой-корень"
    assert graphs.resolve("demo/graph", root=чужой) == чужой / "demo" / "graph"


def test_конфиг_считается_от_названного_корня(tmp_path, monkeypatch):
    """Конфиг — производная корня, а не константа импорта."""
    monkeypatch.chdir(tmp_path)
    данные = charoite_paths.use_data_root(tmp_path / "данные")
    assert graphs.config_path() == данные / "config" / "config.yaml"


def test_data_root_названный_сильнее_переменной(tmp_path, monkeypatch):
    """Граф видит тот же порядок, что и канон: названный корень сильнее env."""
    из_переменной, названный = tmp_path / "из-переменной", tmp_path / "названный"
    monkeypatch.setenv("CHAROITE_ROOT", str(из_переменной))
    assert graphs.data_root() == из_переменной.resolve()
    charoite_paths.use_data_root(названный)
    assert graphs.data_root() == названный.resolve()


def test_пробельная_переменная_корня_не_относительный_корень(tmp_path, monkeypatch):
    """`CHAROITE_ROOT=" "` — не задан, а не путь « » рядом с cwd.

    Раньше эту границу приходилось проверять через `importlib.reload`: корень
    считался на импорте. Теперь он считается на вызове — перезагрузка модуля
    в тесте больше не нужна, и это само по себе часть контракта.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAROITE_ROOT", "   ")
    корень = graphs.data_root()
    assert корень.is_absolute() and корень != pathlib.Path.cwd()
    monkeypatch.setenv("CHAROITE_ROOT", "~/charoite-data")
    assert graphs.data_root() == (pathlib.Path.home() / "charoite-data").resolve()


def test_перенесённый_модуль_берёт_корень_у_вызывающего(tmp_path):
    """Главное свойство №327: корень переживает переезд кода.

    Отдельно поставленный пакет лежит в `site-packages`, а не рядом со
    встречами человека: вывод корня из положения файла даёт там не ошибку в
    вычислении, а ответ на другой вопрос. Копируем графовый модуль в чужое
    дерево и спрашиваем дважды — до и после `use_data_root`. Ожидание обе
    стороны берут у фикстуры, поэтому подменить проверяемое вычисление той
    же формулой нельзя: пока корень выводился из `__file__`, вторая половина
    теста падала бы на первой же строке.
    """
    пакет = tmp_path / "site-packages"
    пакет.mkdir()
    for name in ("graphs.py", "charoite_paths.py"):
        (пакет / name).write_text((SRC / name).read_text(encoding="utf-8"), encoding="utf-8")
    данные = tmp_path / "данные-человека"
    код = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(пакет)!r})
        import charoite_paths, graphs
        print(graphs.config_path())
        charoite_paths.use_data_root({str(данные)!r})
        print(graphs.config_path())
    """)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}   # без CHAROITE_ROOT
    out = subprocess.run([sys.executable, "-c", код], capture_output=True,
                         text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr[-400:]
    из_положения, от_вызывающего = out.stdout.strip().splitlines()
    assert pathlib.Path(из_положения) == tmp_path.resolve() / "config" / "config.yaml"
    assert pathlib.Path(от_вызывающего) == данные.resolve() / "config" / "config.yaml"


def test_resolve_tilde_and_absolute():
    assert graphs.resolve("~/Vault/Work") == pathlib.Path.home() / "Vault" / "Work"
    assert graphs.resolve("/abs/graph") == pathlib.Path("/abs/graph")


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_resolve_empty_is_none_not_dot(raw):
    assert graphs.resolve(raw) is None


def test_env_overrides_config(tmp_path, monkeypatch):
    данные = (tmp_path / "данные").resolve()
    charoite_paths.use_data_root(данные)
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(graphs.ENV_GRAPH, "rel/test-graph")
    got = graphs.graph_dir({"sufler": {"graph_dir": "/cfg/graph"}})
    assert got == данные / "rel" / "test-graph"
    monkeypatch.setenv(graphs.ENV_GRAPH, "   ")           # пробельная = не задана
    assert graphs.graph_dir({"sufler": {"graph_dir": "/cfg/graph"}}) == pathlib.Path("/cfg/graph")


def test_config_shapes(monkeypatch):
    # env_override читает CHAROITE_GRAPH_DIR ПЕРВЫМ — без сброса обоих имён
    # машинная переменная решает исход теста (круг-2 по #405, DS).
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert graphs.graph_dir({"sufler": {"graph_dir": "~/g"}}) == pathlib.Path.home() / "g"
    assert graphs.graph_dir({"sufler": None}) is None
    assert graphs.graph_dir({}) is None
    assert graphs.graph_dir("битый конфиг") is None
    monkeypatch.setenv(graphs.ENV_GRAPH, "/env/g")
    assert graphs.graph_dir({"sufler": {"graph_dir": "/cfg"}}, env=False) == pathlib.Path("/cfg")


def test_configured_graph_is_the_same_entry_point(monkeypatch):
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
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
