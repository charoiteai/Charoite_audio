"""Один каталог `cloud.lock` у всех, кто берёт замок графа (№853).

Формула каталога жила копией в шести местах, и тест «забыть» сверял её с тем же
хелпером, которым пользуется сам (Haiku M3 n841-opus-fix2). Здесь писатели
сверяются друг с другом: шпион на `file_locks.graph_lock` записывает, какой каталог
каждый из них отдал. Писатели без замка (конвейер графа, утилиты переименования)
так задуманы — их держит гейт снимка (`cloud_review.py`, «конвейер пишет в граф
без замка»), сюда они не входят."""

import contextlib
import json
import os
import pathlib
import select
import subprocess
import sys
import textwrap
import unicodedata

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import cloud_review as cr  # noqa: E402
import charoite_paths  # noqa: E402
import file_locks  # noqa: E402
import fix_action_items as fa  # noqa: E402
import forget_meeting as fm  # noqa: E402
import migrate_placeholders as mp  # noqa: E402
import nightly_dossier as nd  # noqa: E402


@pytest.mark.корень_называет_тест
@pytest.mark.parametrize("alias_kind", ["case", "unicode", "symlink"])
def test_cli_alias_contends_with_cloud_review_lock(tmp_path, monkeypatch, alias_kind):
    data = tmp_path / "data"
    data.mkdir()
    data = charoite_paths.use_data_root(data)
    graph = tmp_path / "vault" / "Работа-й"
    graph.mkdir(parents=True)
    if alias_kind == "symlink":
        alias = tmp_path / "graph-link"
        alias.symlink_to(graph, target_is_directory=True)
    else:
        name = graph.name.lower() if alias_kind == "case" else unicodedata.normalize("NFD", graph.name)
        alias = graph.with_name(name)
        if not alias.exists():
            pytest.skip(f"ФС различает написания пути: {alias_kind}")
    assert alias.samefile(graph)
    (data / "config").mkdir()
    (data / "config" / "config.yaml").write_text(
        "sufler:\n  graph_dir: " + json.dumps(str(graph), ensure_ascii=False) + "\n", encoding="utf-8")
    for name in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(fa, "LOCK_WAIT", 0)
    env = dict(os.environ, CHAROITE_ROOT=str(data), HOME=str(tmp_path))
    worker = textwrap.dedent("""
        import pathlib, select, sys
        repo, data = map(pathlib.Path, sys.argv[1:])
        sys.path[:0] = [str(repo / 'src'), str(repo / 'scripts')]
        import charoite_paths
        charoite_paths.use_data_root(data)
        import cloud_review, graphs
        with cloud_review.graph_lock(graphs.graph_dir(), wait=0) as acquired:
            print('held' if acquired else 'not-held', flush=True)
            assert acquired
            assert select.select([sys.stdin], [], [], 15)[0], 'release was not received'
            assert sys.stdin.readline().strip() == 'release'
    """)
    repo = pathlib.Path(__file__).resolve().parent.parent
    process = subprocess.Popen([sys.executable, "-c", worker, str(repo), str(data)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=env)
    try:
        assert select.select([process.stdout], [], [], 10)[0], "cloud_review did not take the lock"
        assert process.stdout.readline().strip() == "held"
        # Другой процесс держит настоящий flock; утилите менять граф нельзя.
        with fa._graph_lock(fa.graph_dir(str(alias)), data) as acquired:
            assert acquired is False
    finally:
        _, stderr = process.communicate("release\n", timeout=20)
        assert process.returncode == 0, stderr
    # После управляемого освобождения та же обёртка утилиты получает замок.
    with fa._graph_lock(fa.graph_dir(str(alias)), data) as acquired:
        assert acquired is True


def test_every_graph_lock_taker_uses_one_lock_dir(tmp_path, monkeypatch):
    data = tmp_path / "данные"
    data.mkdir()
    data = data.resolve()        # корень канона приходит resolve-нутым (`/private/var` на macOS)
    graph = tmp_path / "граф"
    graph.mkdir()
    monkeypatch.setenv("CHAROITE_ROOT", str(data))
    taken: dict[str, pathlib.Path] = {}
    who = [""]

    @contextlib.contextmanager
    def spy(lock_dir, wait, **kw):
        taken[who[0]] = pathlib.Path(lock_dir)
        yield True

    monkeypatch.setattr(file_locks, "graph_lock", spy)

    def take(name, cm):
        who[0] = name
        with cm:
            pass

    take("cloud_review", cr.graph_lock(graph))
    take("nightly_dossier", nd._graph_lock(graph))
    take("migrate_placeholders", mp._graph_lock(graph, data))
    take("fix_action_items", fa._graph_lock(graph, data))
    plan_dirs = [d for d, g in fm.plan("2026-10-01_1000", data, graph).lock_dirs.items() if g == graph]

    assert len(taken) == 4, taken
    dirs = set(taken.values()) | set(plan_dirs)
    assert len(dirs) == 1, taken | {"forget_meeting": plan_dirs}
    assert next(iter(dirs)).is_relative_to(data), dirs


def _lock_dir_copies(source: str) -> list[int]:
    """Строки, где `.parent` берётся прямо от вызова `graph_backups(...)` — формула замка руками.
    По AST, а не по строке: `root=_root()` и перенос `.parent` строкой регэксп не видел
    (n853-out2 Sonnet I1)."""
    import ast
    return [n.lineno for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Attribute) and n.attr == "parent" and isinstance(n.value, ast.Call)
            and getattr(n.value.func, "attr", getattr(n.value.func, "id", None)) == "graph_backups"]


def test_lock_dir_formula_lives_in_one_place():
    """Новое место замка, вписавшее формулу руками, разошлось бы с соседями молча, а
    тест выше его не знает (n853-out1 Sonnet M1): формула — только в `charoite_paths`."""
    # сторож обязан видеть то написание, которое скопируют чаще всего
    assert _lock_dir_copies('d = charoite_paths.graph_backups(g, "cloud_backup", root=_root()).parent') == [1]
    assert _lock_dir_copies('d = (graph_backups(g, "cloud_backup",\n    root=r)\n    .parent)') == [1]
    assert _lock_dir_copies('d = charoite_paths.graph_lock_dir(g, root=_root())') == []
    root = pathlib.Path(__file__).resolve().parent.parent
    found = [f"{p.relative_to(root)}:{n}" for d in ("src", "scripts") for p in sorted((root / d).rglob("*.py"))
             if p.name != "charoite_paths.py" for n in _lock_dir_copies(p.read_text(encoding="utf-8"))]
    assert not found, found
