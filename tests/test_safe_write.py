"""Обрыв записи не должен уничтожать то, что уже лежит на диске."""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import safe_write  # noqa: E402


def test_a_failed_write_leaves_the_previous_version_intact(tmp_path, monkeypatch):
    """Диск кончился посреди записи — узел графа остаётся прежним, не пустым.

    Голый `write_text` усекает файл до нуля ПРЕЖДЕ, чем пишет: ENOSPC, квота
    iCloud или kill ночного цикла оставляли вместо ядра, копившегося год,
    файл в 0 байт. Восстанавливать неоткуда — это единственный экземпляр.
    """
    node = tmp_path / "Ядра" / "Хранилище.md"
    node.parent.mkdir(parents=True)
    node.write_text("# ядро\nгод накопленной истории\n", encoding="utf-8")

    real = pathlib.Path.write_text

    def full_disk(self, *a, **kw):
        if self.name.startswith("Хранилище.md.tmp"):
            raise OSError(28, "No space left on device")
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", full_disk)
    with pytest.raises(OSError):
        safe_write.write_text(node, "новая версия")

    assert node.read_text(encoding="utf-8") == "# ядро\nгод накопленной истории\n"
    assert not list(node.parent.glob("*.tmp*")), "временный файл остался мусором"


def test_a_successful_write_replaces_the_content(tmp_path):
    node = tmp_path / "Люди" / "Дмитрий.md"
    safe_write.write_text(node, "# Дмитрий\n")          # каталога ещё нет
    assert node.read_text(encoding="utf-8") == "# Дмитрий\n"
    safe_write.write_text(node, "# Дмитрий\nобновлено\n")
    assert node.read_text(encoding="utf-8") == "# Дмитрий\nобновлено\n"
    assert not list(node.parent.glob("*.tmp*"))


def test_two_writers_do_not_assemble_one_file_out_of_two(tmp_path):
    """Каждый пишет в свой временный файл: победит последний, но целиком."""
    node = tmp_path / "Ядра" / "Общее.md"
    node.parent.mkdir(parents=True)
    other_pid_tmp = node.with_name(f"{node.name}.tmp{os.getpid() + 1}")
    other_pid_tmp.write_text("чужая недописанная версия", encoding="utf-8")

    safe_write.write_text(node, "моя целая версия\n")

    assert node.read_text(encoding="utf-8") == "моя целая версия\n"
    assert other_pid_tmp.exists(), "хелпер тронул временный файл соседа"
