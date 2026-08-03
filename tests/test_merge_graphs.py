"""Слияние графов: перенос, коллизии дописыванием, _MOC, план без записи.

Раскол графа — реальная авария (03.08: встреча уехала в новый граф
«Linux 1.8»). Конвейер расколы теперь предотвращает, а этот инструмент
сшивает уже случившиеся.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location(
    "merge_graphs", ROOT / "scripts" / "merge_graphs.py")
mg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mg)


def _graph(tmp_path: Path, name: str) -> Path:
    g = tmp_path / name
    for d in ("Встречи", "Люди"):
        (g / d).mkdir(parents=True)
    (g / "_MOC.md").write_text(
        f"# {name} — MOC\n\n## 🗓 Встречи\n", encoding="utf-8")
    return g


def test_plan_moves_new_and_flags_collisions(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    (src / "Встречи" / "2026-08-01_1000.md").write_text("встреча", encoding="utf-8")
    (src / "Люди" / "Иван.md").write_text("донорская история", encoding="utf-8")
    (dst / "Люди" / "Иван.md").write_text("приёмная история", encoding="utf-8")

    moves, appends, _ = mg.plan(src, dst)
    assert [m[0].name for m in moves] == ["2026-08-01_1000.md"]
    assert [a[0].name for a in appends] == ["Иван.md"]
    # план ничего не тронул
    assert (src / "Встречи" / "2026-08-01_1000.md").exists()
    assert (dst / "Люди" / "Иван.md").read_text(encoding="utf-8") == "приёмная история"


def test_apply_moves_appends_and_marks_donor(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    (src / "Встречи" / "2026-08-01_1000.md").write_text("встреча", encoding="utf-8")
    (src / "Люди" / "Иван.md").write_text(
        "---\ntype: person\n---\nдонорская история", encoding="utf-8")
    (dst / "Люди" / "Иван.md").write_text(
        "---\ntype: person\n---\nприёмная история", encoding="utf-8")
    (src / "_MOC.md").write_text(
        "# Донор — MOC\n\n## 🗓 Встречи\n- [[Встречи/2026-08-01_1000|Тема]] — тема\n",
        encoding="utf-8")

    mg.apply(src, dst, *mg.plan(src, dst))

    # новое переехало
    assert (dst / "Встречи" / "2026-08-01_1000.md").read_text(encoding="utf-8") == "встреча"
    assert not (src / "Встречи" / "2026-08-01_1000.md").exists()
    # коллизия дописана секцией, вторая YAML-шапка срезана
    merged = (dst / "Люди" / "Иван.md").read_text(encoding="utf-8")
    assert "приёмная история" in merged and "донорская история" in merged
    assert "Перенесено из графа Донор" in merged
    assert merged.count("type: person") == 1
    # строка встречи доехала до _MOC приёмника
    assert "- [[Встречи/2026-08-01_1000|Тема]]" in (dst / "_MOC.md").read_text(encoding="utf-8")
    # донор помечен слитым
    assert "слит в Приёмник" in (src / "_MOC.md").read_text(encoding="utf-8")


def test_moc_lines_already_in_receiver_are_not_duplicated(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    line = "- [[Встречи/2026-08-01_1000|Тема]] — тема\n"
    (src / "_MOC.md").write_text(f"# Донор\n\n## 🗓 Встречи\n{line}", encoding="utf-8")
    (dst / "_MOC.md").write_text(f"# Приёмник\n\n## 🗓 Встречи\n{line}", encoding="utf-8")

    _, _, moc_lines = mg.plan(src, dst)
    assert moc_lines == []


def test_identical_copies_are_not_appended(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    (src / "Люди" / "Иван.md").write_text("одно и то же", encoding="utf-8")
    (dst / "Люди" / "Иван.md").write_text("одно и то же", encoding="utf-8")

    moves, appends, _ = mg.plan(src, dst)
    assert moves == [] and appends == []


def test_configured_graph_honours_env(tmp_path, monkeypatch):
    """Все инструменты резолвят граф через graphs.configured_graph —
    и тестовый прогон не должен дотягиваться до рабочего графа."""
    import graphs
    monkeypatch.setenv("SUFLER_GRAPH_DIR", str(tmp_path))
    assert graphs.configured_graph() == tmp_path
