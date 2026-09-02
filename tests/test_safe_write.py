"""Обрыв записи не должен уничтожать то, что уже лежит на диске."""
import os
import pathlib
import time
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


def test_a_symlinked_note_is_written_through_not_replaced(tmp_path):
    """Симлинк ведёт к настоящему файлу — писать надо в него.

    Круг-1, DS: `replace` подменил бы саму ссылку обычным файлом, а цель
    осталась бы со старым текстом. В графе люди держат общие заметки
    ссылками, и такая подмена рвёт связь молча.
    """
    real = tmp_path / "общая" / "Хранилище.md"
    real.parent.mkdir(parents=True)
    real.write_text("старое\n", encoding="utf-8")
    link = tmp_path / "Ядра" / "Хранилище.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    safe_write.write_text(link, "новое\n")

    assert link.is_symlink(), "ссылку подменили обычным файлом"
    assert real.read_text(encoding="utf-8") == "новое\n", "цель ссылки не обновилась"


def test_file_permissions_survive_the_replace(tmp_path):
    """`replace` даёт новый inode — права и метки надо перенести.

    Круг-1, GLM: иначе узел после первой же правки терял бы выставленные
    вручную права, цветные метки Finder и комментарии Spotlight.
    """
    node = tmp_path / "Ядра" / "Секрет.md"
    node.parent.mkdir(parents=True)
    node.write_text("старое\n", encoding="utf-8")
    node.chmod(0o600)
    try:
        os.setxattr(node, "user.charoite.test", "метка".encode())
        had_xattr = True
    except (OSError, AttributeError):
        had_xattr = False

    safe_write.write_text(node, "новое\n")

    assert node.stat().st_mode & 0o777 == 0o600, "права не пережили запись"
    if had_xattr:
        assert os.getxattr(node, "user.charoite.test") == "метка".encode(), "метка не пережила запись"


def test_the_note_looks_fresh_after_a_rewrite(tmp_path):
    """После записи узел обязан выглядеть новее, чем был.

    Круг-2, DS: перенос метаданных через `copystat` тащил и mtime, а по нему
    ночь отбирает работу — `tier3.changed_since` берёт ядра свежее прошлого
    прогона. Ядро, обновлённое сегодня, но записанное до того неделю назад,
    выпадало бы из инкремента до следующего полного прохода. Тем же mtime
    живёт кэш индекса узлов.
    """
    node = tmp_path / "Ядра" / "Хранилище.md"
    node.parent.mkdir(parents=True)
    node.write_text("старое\n", encoding="utf-8")
    week_ago = time.time() - 7 * 24 * 3600
    os.utime(node, (week_ago, week_ago))

    safe_write.write_text(node, "новое\n")

    assert node.stat().st_mtime > week_ago + 3600, (
        "узел после записи выглядит недельной давности — ночь его не увидит"
    )


def test_expect_absent_keeps_a_file_that_appeared_meanwhile(tmp_path):
    """expect=None — свободная запись, не «пиши, только если файла нет»:
    минутки, созданные mcp или редактором за время долгой генерации
    пересборки, затирались бы молча (GLM Critical по #483)."""
    path = tmp_path / "2026-09-02_1021_minutes.md"
    assert safe_write.write_text(path, "первые\n", expect_absent=True), "файла не было — пишем"
    assert path.read_text(encoding="utf-8") == "первые\n"
    assert not safe_write.write_text(path, "поверх\n", expect_absent=True), (
        "файл появился — чужой документ остаётся")
    assert path.read_text(encoding="utf-8") == "первые\n"
    assert not list(tmp_path.glob("*.tmp*")), "временный файл убран"
