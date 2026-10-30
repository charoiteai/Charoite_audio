"""Один каталог `cloud.lock` у всех, кто берёт замок графа (№853).

Формула каталога жила копией в шести местах, и тест «забыть» сверял её с тем же
хелпером, которым пользуется сам (Haiku M3 n841-opus-fix2). Здесь писатели
сверяются друг с другом: шпион на `file_locks.graph_lock` записывает, какой каталог
каждый из них отдал. Писатели без замка (конвейер графа, утилиты переименования)
так задуманы — их держит гейт снимка (`cloud_review.py`, «конвейер пишет в граф
без замка»), сюда они не входят."""

import contextlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import cloud_review as cr  # noqa: E402
import file_locks  # noqa: E402
import fix_action_items as fa  # noqa: E402
import forget_meeting as fm  # noqa: E402
import migrate_placeholders as mp  # noqa: E402
import nightly_dossier as nd  # noqa: E402


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


def test_lock_dir_formula_lives_in_one_place():
    """Новое место замка, вписавшее формулу руками, разошлось бы с соседями молча, а
    тест выше его не знает (n853-out1 Sonnet M1): формула — только в `charoite_paths`."""
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    copy = re.compile(r"graph_backups\([^)]*\)\s*\.parent")
    found = [f"{p.relative_to(root)}:{n}" for d in ("src", "scripts") for p in sorted((root / d).rglob("*.py"))
             if p.name != "charoite_paths.py"
             for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if copy.search(line)]
    assert not found, found
