"""Слияние графов: перенос, коллизии дописыванием, _MOC, план без записи.

Раскол графа — реальная авария (03.08: встреча уехала в новый граф
«Linux 1.8»). Конвейер расколы теперь предотвращает, а этот инструмент
сшивает уже случившиеся.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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

    backup = mg.apply(src, dst, *mg.plan(src, dst))

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
    # исходники операции остаются в отдельной резервной копии
    assert backup.is_dir()
    assert (backup / "donor/Встречи/2026-08-01_1000.md").read_text(
        encoding="utf-8") == "встреча"


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


def test_apply_survives_cross_device_move(tmp_path, monkeypatch):
    """Донор на другом томе: rename падает EXDEV, move обязан докопировать."""
    import errno
    import pathlib as pl

    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    (src / "Встречи" / "2026-08-01_1000.md").write_text("встреча", encoding="utf-8")

    real_rename = pl.Path.rename

    def exdev(self, target):  # noqa: ANN001 — сигнатура Path.rename
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(pl.Path, "rename", exdev)
    try:
        mg.apply(src, dst, *mg.plan(src, dst))
    finally:
        monkeypatch.setattr(pl.Path, "rename", real_rename)

    assert (dst / "Встречи" / "2026-08-01_1000.md").read_text(encoding="utf-8") == "встреча"
    assert not (src / "Встречи" / "2026-08-01_1000.md").exists()


def test_binary_collision_aborts_before_first_move(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    movable = src / "Встречи/2026-08-01_1000.md"
    movable.write_text("встреча", encoding="utf-8")
    (src / "Люди/Фото.md").write_bytes(b"\xff\x00donor")
    (dst / "Люди/Фото.md").write_bytes(b"\x89PNGreceiver")

    with pytest.raises(mg.MergeError, match="не UTF-8"):
        mg.plan(src, dst)

    assert movable.exists(), "проверка плана не должна переносить ранние файлы"
    assert not (dst / "Встречи/2026-08-01_1000.md").exists()


def test_non_markdown_text_collision_is_not_corrupted(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    (src / "index.json").write_text('{"graph": "donor"}', encoding="utf-8")
    (dst / "index.json").write_text('{"graph": "receiver"}', encoding="utf-8")

    with pytest.raises(mg.MergeError, match="не Markdown"):
        mg.plan(src, dst)

    assert (dst / "index.json").read_text(encoding="utf-8") == '{"graph": "receiver"}'


def test_destination_symlink_cannot_escape_receiver(tmp_path):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    outside = tmp_path / "Снаружи"
    outside.mkdir()
    (dst / "Встречи").rmdir()
    (dst / "Встречи").symlink_to(outside, target_is_directory=True)
    (src / "Встречи/2026-08-01_1000.md").write_text("встреча", encoding="utf-8")

    with pytest.raises(mg.MergeError, match="за пределы графа"):
        mg.plan(src, dst)
    assert not (outside / "2026-08-01_1000.md").exists()


def test_nested_graphs_are_rejected_in_both_directions(tmp_path):
    outer = _graph(tmp_path, "Внешний")
    inner = _graph(outer, "Внутренний")

    with pytest.raises(mg.MergeError, match="вложены"):
        mg.plan(outer, inner)
    with pytest.raises(mg.MergeError, match="вложены"):
        mg.plan(inner, outer)


def test_failure_after_move_rolls_every_file_back(tmp_path, monkeypatch):
    src = _graph(tmp_path, "Донор")
    dst = _graph(tmp_path, "Приёмник")
    moved = src / "Встречи/2026-08-01_1000.md"
    moved.write_text("встреча", encoding="utf-8")
    donor_person = src / "Люди/Иван.md"
    receiver_person = dst / "Люди/Иван.md"
    donor_person.write_text("донор", encoding="utf-8")
    receiver_person.write_text("приёмник", encoding="utf-8")
    donor_moc = (src / "_MOC.md").read_text(encoding="utf-8")
    receiver_moc = (dst / "_MOC.md").read_text(encoding="utf-8")
    moves, appends, moc_lines = mg.plan(src, dst)

    real_write = mg.atomic_write_text

    def fail_first_graph_write(path, text):  # noqa: ANN001
        if path == receiver_person:
            raise OSError("диск заполнен")
        real_write(path, text)

    monkeypatch.setattr(mg, "atomic_write_text", fail_first_graph_write)
    with pytest.raises(mg.MergeError, match="изменения откачены"):
        mg.apply(src, dst, moves, appends, moc_lines)

    assert moved.read_text(encoding="utf-8") == "встреча"
    assert not (dst / "Встречи/2026-08-01_1000.md").exists()
    assert donor_person.read_text(encoding="utf-8") == "донор"
    assert receiver_person.read_text(encoding="utf-8") == "приёмник"
    assert (src / "_MOC.md").read_text(encoding="utf-8") == donor_moc
    assert (dst / "_MOC.md").read_text(encoding="utf-8") == receiver_moc
    assert list(tmp_path.glob(".charoite-merge-backup-*")), "backup должен остаться"
