"""Обрыв записи не должен уничтожать то, что уже лежит на диске."""
import os
import pathlib
import stat
import time
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_graph import safe_write  # noqa: E402


@pytest.mark.parametrize("copy, mode", [(False, None), (False, 0o600), (True, None)])
def test_threads_writing_one_target_have_independent_temporary_files(tmp_path, monkeypatch, copy, mode):
    """Оба писателя обязаны закончить; завершившийся не забирает tmp соседа."""
    target = tmp_path / "общая.md"
    target.write_text("до записи", encoding="utf-8")
    first_ready, release_first = threading.Event(), threading.Event()
    errors = []
    real_replace = pathlib.Path.replace

    def ordered_replace(path, dest):
        if dest == target and threading.current_thread().name == "first-writer":
            first_ready.set()
            assert release_first.wait(5), "второй писатель не завершился"
        return real_replace(path, dest)

    monkeypatch.setattr(pathlib.Path, "replace", ordered_replace)
    first_src, second_src = tmp_path / "первая.md", tmp_path / "вторая.md"
    first_src.write_text("первая целая версия", encoding="utf-8")
    second_src.write_text("вторая целая версия", encoding="utf-8")

    def write_first():
        try:
            if copy:
                safe_write.copy_if_changed(first_src, target)
            else:
                safe_write.write_text(target, first_src.read_text(encoding="utf-8"), mode=mode)
        except Exception as exc:  # noqa: BLE001 — ошибку потока проверяет тест
            errors.append(exc)

    worker = threading.Thread(target=write_first, name="first-writer")
    worker.start()
    try:
        assert first_ready.wait(5), "первый писатель не дошёл до replace"
        if copy:
            assert safe_write.copy_if_changed(second_src, target)
        else:
            assert safe_write.write_text(target, second_src.read_text(encoding="utf-8"), mode=mode)
        assert target.read_text(encoding="utf-8") == "вторая целая версия"
    finally:
        release_first.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors, errors
    assert target.read_text(encoding="utf-8") == "первая целая версия"
    assert not list(tmp_path.glob("*.tmp*"))


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


def test_claim_takes_a_name_exactly_once_and_write_text_fills_it(tmp_path):
    """Имя заметки занимается эксклюзивным созданием: `expect_absent` сжимал окно
    гонки, но проверка и replace — две операции (DS r2 M2 / GLM r2 M4 по #559)."""
    path = tmp_path / "Заметки" / "2026-09-13_1200_идея.md"
    assert safe_write.claim(path) is True
    assert path.exists() and path.read_text(encoding="utf-8") == ""
    assert safe_write.claim(path) is False, "занятое имя отдано второй раз"
    assert safe_write.write_text(path, "текст") is True
    assert path.read_text(encoding="utf-8") == "текст"
    # права не проверяем: их задаёт umask процесса (CI прогоняет всё дерево одним процессом)


def test_lost_race_tells_a_missing_file_from_an_unreachable_one(tmp_path, monkeypatch):
    """№273, круг 4. «Файла нет» и «не дотянулись» — разные факты: в
    исчезнувшем файле нечего править, недоступный остаётся как был. Различаем
    в момент отказа, а не опросом диска в обработчике: `Path.exists()` там сам
    бросает на EACCES/EIO и роняет перенос уже ПОСЛЕ записи."""
    gone = tmp_path / "нет.md"
    with pytest.raises(safe_write.LostRace) as exc:
        safe_write.rewrite_file(gone, lambda t: (t, 1), "проверка")
    assert exc.value.reason == "файла нет", exc.value.reason
    assert exc.value.gone, "исчезнувший файл опознаётся свойством, а не сверкой текста"

    # Файл исчез МЕЖДУ снимком и чтением — тот же факт, та же причина, иначе
    # вызывающий обвинит в мёртвых ссылках файл, которого нет (DS r5 Critical 2)
    live = tmp_path / "жил.md"
    live.write_text("текст\n", encoding="utf-8")
    real_read = pathlib.Path.read_text

    def vanish(self, *a, **kw):
        if self.name == "жил.md":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_read(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", vanish)
    with pytest.raises(safe_write.LostRace) as exc2:
        safe_write.rewrite_file(live, lambda t: (t, 1), "проверка")
    assert exc2.value.gone and exc2.value.reason == "файла нет", exc2.value.reason

    # А теперь ВТОРАЯ половина имени теста: «не дотянулись». Настоящий EACCES,
    # без подмен — иначе текст причины, который воркер печатает в единственный
    # канал, не закреплён ничем (DS r6 I1)
    closed = tmp_path / "закрыто"
    closed.mkdir()
    hidden = closed / "узел.md"
    hidden.write_text("текст\n", encoding="utf-8")
    closed.chmod(0o000)
    try:
        with pytest.raises(safe_write.LostRace) as exc3:
            safe_write.rewrite_file(hidden, lambda t: (t, 1), "проверка")
    finally:
        closed.chmod(0o700)
    assert exc3.value.unreachable, exc3.value.kind
    assert not exc3.value.gone, "недоступный файл не «исчез» — он остался как был"
    # подробность системы — ОТДЕЛЬНОЕ поле, а не хвост текста: пока она жила
    # в `reason` за префиксом, третий вид опознавали через startswith, то есть
    # текстом (DS I3 и GLM критика 1, круг 1 по №277)
    assert exc3.value.detail and "Permission denied" in exc3.value.detail, exc3.value.detail
    assert exc3.value.reason == f"снимок не снят: {exc3.value.detail}", exc3.value.reason


def test_the_kind_of_a_lost_race_must_be_chosen_not_inherited(tmp_path):
    """№277, круг 2, DS I2 и M3. Умолчанием вида был CHANGED — самая
    обвинительная из трёх причин: новое место отказа получало «поверх писал
    кто-то ещё» бесплатно, ничем не подтверждённое, в единственный канал
    воркера. Вид обязан быть решением вызывающего. Неизвестный вид — своя
    понятная ошибка, а не KeyError поверх настоящей причины отказа записи."""
    p = tmp_path / "узел.md"
    with pytest.raises(TypeError):
        safe_write.LostRace(p, "что-то не сделано")          # без вида — нельзя
    with pytest.raises(ValueError) as bad:
        safe_write.LostRace(p, "что-то не сделано", kind="unreachble")
    assert "неизвестный вид причины" in str(bad.value), bad.value
    # все три вида живые и различимы признаками, ни один не «по умолчанию»
    kinds = {safe_write.LostRace.CHANGED: (False, False),
             safe_write.LostRace.GONE: (True, False),
             safe_write.LostRace.UNREACHABLE: (False, True)}
    for kind, (gone, unreachable) in kinds.items():
        exc = safe_write.LostRace(p, "что-то не сделано", kind=kind)
        assert (exc.gone, exc.unreachable) == (gone, unreachable), kind
        assert exc.reason and exc.path is p and kind not in exc.reason, \
            "вид — ключ для машины, reason — текст для человека"


def test_write_bytes_keeps_the_mode_of_an_existing_file(tmp_path):
    """`write_bytes` — права как у `write_text` без `mode`: у существующего файла
    сохраняются его прежние права, а не права источника (которых у байтов нет).
    Так пишется `.prev`: минутки 0644 не должны оставлять читаемую всем копию
    (№663)."""
    node = tmp_path / "prev.md"
    node.write_bytes("старое".encode("cp1251"))
    os.chmod(node, 0o640)

    safe_write.write_bytes(node, "новое".encode("cp1251"))

    assert node.read_bytes() == "новое".encode("cp1251"), "байты заменены целиком"
    assert stat.S_IMODE(node.stat().st_mode) == 0o640, "права прежнего файла сохранены"
    assert not list(tmp_path.glob("*.tmp*")), "остаток tmp убран"


def test_write_bytes_creates_a_new_file_by_the_process_mask(tmp_path):
    """Нового файла ещё нет — права по маске процесса, как у `write_text` без
    `mode`: это и есть исход «минутки 0644 → `.prev` 0600» (№663)."""
    path = tmp_path / ".prev" / "минутки.md"          # каталога ещё нет — создаём
    old_umask = os.umask(0o077)
    try:
        safe_write.write_bytes(path, b"\xff\xfe cp1251 \xea")
    finally:
        os.umask(old_umask)
    assert path.read_bytes() == b"\xff\xfe cp1251 \xea"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp*"))


def test_write_bytes_follows_a_symlink_instead_of_replacing_it(tmp_path):
    """Симлинк ведёт к настоящему файлу — пишем в него, а не подменяем саму
    ссылку обычным файлом (та же защита, что у `write_text`)."""
    real = tmp_path / "настоящий.md"
    real.write_bytes("старое".encode("utf-8"))
    link = tmp_path / "ссылка.md"
    link.symlink_to(real)

    safe_write.write_bytes(link, "новое".encode("utf-8"))

    assert link.is_symlink() and real.read_bytes() == "новое".encode("utf-8")


def test_write_bytes_creates_every_missing_parent_directory(tmp_path):
    """Контракт `write_bytes` шире одного уровня: недостающих каталогов может
    быть несколько, и с `parents=False` запись падала бы на втором. Единственный
    вызывающий (`.prev` в `live_sidecar`) достраивает лишь один уровень, и на
    этом мутация «`parents=True` → `parents=False`» выживала (№663)."""
    path = tmp_path / "встреча" / ".prev" / "минутки.md"
    assert not (tmp_path / "встреча").exists(), "каталога ещё нет — создаём оба уровня"

    data = "минутки встречи".encode("utf-8")
    safe_write.write_bytes(path, data)

    assert path.read_bytes() == data, "байты записаны целиком"
    assert not list(path.parent.glob("*.tmp*")), "остаток tmp убран"
