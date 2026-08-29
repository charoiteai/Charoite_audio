"""Две пересборки одной встречи не должны идти одновременно.

12.08: обновление приложения посреди разбора дало два прогона на одну
встречу. Первый осиротел — его демона закрыли, но сам он продолжил
работать; новый демон при старте увидел прерванную встречу и запустил
пересборку заново. Он проверяет лок демона, а осиротевшая пересборка лока
не держит вовсе.

Итог: два процесса по 100% CPU диаризовали одно и то же при 10 ГБ
свободной памяти и дрались за финальный файл — чей `replace` последний,
того и результат. Встреча не доехала до готовности вовсе.
"""
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rebuild_transcript as rt  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "transcripts").mkdir()
    monkeypatch.setattr(rt, "ROOT", tmp_path)
    return tmp_path


def _live(root: pathlib.Path, stamp: str = "2026-08-12_1532") -> pathlib.Path:
    p = root / "transcripts" / f"{stamp}.md"
    p.write_text("стенограмма", encoding="utf-8")
    return p


def test_no_marks_means_free(root):
    assert rt.running_elsewhere(_live(root)) is None


def test_live_run_is_detected(root):
    """Чужой прогон держит flock на своей отметке — второй заход запрещён,
    pid держателя читается из файла (для строки лога)."""
    import fcntl
    live = _live(root)
    alien = os.getppid()
    f = rt._pid_file("2026-08-12_1532")
    f.write_text(str(alien), encoding="utf-8")
    fh = f.open("r+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert rt.running_elsewhere(live) == alien
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def test_own_pid_is_not_a_conflict(root):
    """Собственная отметка не должна выглядеть чужим прогоном — иначе
    пересборка запретит сама себя при повторном заходе в ту же функцию."""
    live = _live(root)
    rt._pid_file("2026-08-12_1532").write_text(str(os.getpid()), encoding="utf-8")

    assert rt.running_elsewhere(live) is None


def test_dead_mark_does_not_block(root):
    """Машину выключили посреди пересборки: замка на отметке нет — она ничего
    не запрещает, а следующий прогон переписывает её под своим замком. Живой
    pid без замка — тоже не прогон: pid переиспользуется (хвост 20.08, GLM),
    а признак живости один — flock (круг по #455)."""
    live = _live(root)
    f = rt._pid_file("2026-08-12_1532")
    for pid in (2 ** 22, os.getppid()):          # мёртвый и живой чужой
        f.write_text(str(pid), encoding="utf-8")
        assert rt.running_elsewhere(live) is None
    mark = rt.mark_running(live)
    assert mark == f and f.read_text(encoding="utf-8") == str(os.getpid())
    rt._RUNNING_LOCKS.clear()


def test_broken_mark_does_not_block(root):
    """Мусор в файле — не повод отказываться от работы."""
    live = _live(root)
    rt._pid_file("2026-08-12_1532").write_text("не число", encoding="utf-8")

    assert rt.running_elsewhere(live) is None


def test_mark_is_written_and_readable(root):
    live = _live(root)
    f = rt.mark_running(live)

    assert f is not None and f.exists()
    assert f.read_text().strip() == str(os.getpid())


def test_marks_are_per_meeting(root):
    """Пересборка одной встречи не должна запрещать другую: они не
    конкурируют ни за файлы, ни за результат."""
    first = _live(root, "2026-08-12_1532")
    second = _live(root, "2026-08-12_1700")
    assert rt.mark_running(_live(root, "2026-08-12_1532")) is not None   # живой прогон = замок

    assert rt.running_elsewhere(first) is not None
    assert rt.running_elsewhere(second) is None
    for fh in rt._RUNNING_LOCKS:
        fh.close()
    rt._RUNNING_LOCKS.clear()



def test_titled_transcript_maps_to_same_meeting(root):
    """После наката темы имя файла меняется, встреча — нет. Отметка обязана
    находиться и по титульному имени, иначе защита обходится сама собой."""
    titled = root / "transcripts" / "2026-08-12_1532_Планирование_пилота.md"
    titled.write_text("стенограмма", encoding="utf-8")
    assert rt.mark_running(_live(root, "2026-08-12_1532")) is not None   # живой прогон = замок

    assert rt.running_elsewhere(titled) is not None
    for fh in rt._RUNNING_LOCKS:
        fh.close()
    rt._RUNNING_LOCKS.clear()



def test_guard_is_wired_into_entry_point():
    """Сторож проводки: проверка обязана стоять в main до самой работы —
    иначе она написана, но дубль всё равно стартует."""
    text = (REPO / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    body = text[text.index("def main():"):]
    guard = body.index("running_elsewhere(live)")
    work = body.index("status.processing")

    assert guard < work, "проверка после начала работы бессмысленна"
    assert "mark_running(live)" in body, "прогон не отмечает себя — дубль не увидит его"


def test_flock_holder_is_a_live_run_even_without_liveness_check(root, monkeypatch):
    """Свой же замок в другом дескрипторе: держатель flock — живой прогон."""
    live = _live(root)
    mark = rt.mark_running(live)
    assert mark is not None
    monkeypatch.setattr(os, "getpid", lambda: 4242)   # «мы» — другой процесс
    assert rt.running_elsewhere(live) is not None, "замок держится — прогон идёт"
    rt._RUNNING_LOCKS.clear()


def test_second_mark_does_not_wipe_the_live_pid_and_is_refused(root, monkeypatch):
    """Гонка двух прогонов: второй mark_running не усекает pid живого и
    получает отказ (замок), а не тихо идёт без отметки (DS по #455)."""
    live = _live(root)
    mark = rt.mark_running(live)
    assert mark is not None and mark.read_text(encoding="utf-8") == str(os.getpid())
    monkeypatch.setattr(os, "getpid", lambda: 4242)
    with pytest.raises(rt.RunningElsewhere):
        rt.mark_running(live)
    assert mark.read_text(encoding="utf-8") != "" and mark.read_text(encoding="utf-8") != "4242"
    assert rt.running_elsewhere(live) is not None
    rt._RUNNING_LOCKS.clear()


def test_empty_pid_file_under_lock_still_counts_as_running(root):
    live = _live(root)
    f = rt._pid_file("2026-08-12_1532")
    f.write_text("", encoding="utf-8")
    fh = f.open("r+")
    import fcntl
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert rt.running_elsewhere(live) == -1
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    assert rt.running_elsewhere(live) is None, "замок снят — прогона нет, файл не важен"


def test_second_main_exits_while_the_lock_is_held(root, monkeypatch):
    """Сквозной случай: второй `main()` той же встречи при живом замке выходит
    до статусов и конвертации (luna/GLM по #455)."""
    import fcntl
    import sys
    live = _live(root)
    f = rt._pid_file("2026-08-12_1532")
    f.write_text("777", encoding="utf-8")
    fh = f.open("r+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    lines = []
    monkeypatch.setattr(rt, "log", lambda m: lines.append(m))
    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)])
    monkeypatch.setattr(rt, "MeetingStatusStore", lambda *a, **k: (_ for _ in ()).throw(AssertionError("дошли до статусов")))
    try:
        rt.main()
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    assert lines and "уже идёт" in lines[-1] and "777" in lines[-1]

