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
    """Отметка чужого живого процесса запрещает второй прогон."""
    live = _live(root)
    # Живой процесс, который точно существует и не наш: родитель теста.
    alien = os.getppid()
    rt._pid_file("2026-08-12_1532").write_text(str(alien), encoding="utf-8")

    assert rt.running_elsewhere(live) == alien


def test_own_pid_is_not_a_conflict(root):
    """Собственная отметка не должна выглядеть чужим прогоном — иначе
    пересборка запретит сама себя при повторном заходе в ту же функцию."""
    live = _live(root)
    rt._pid_file("2026-08-12_1532").write_text(str(os.getpid()), encoding="utf-8")

    assert rt.running_elsewhere(live) is None


def test_dead_mark_is_cleaned_up(root):
    """Машину выключили посреди пересборки. Мёртвая отметка не должна
    запрещать встречу навсегда."""
    live = _live(root)
    dead = 2 ** 22          # заведомо несуществующий pid
    f = rt._pid_file("2026-08-12_1532")
    f.write_text(str(dead), encoding="utf-8")

    assert rt.running_elsewhere(live) is None
    assert not f.exists(), "мёртвая отметка осталась и заблокирует повтор"


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
    rt._pid_file("2026-08-12_1532").write_text(str(os.getppid()), encoding="utf-8")

    assert rt.running_elsewhere(first) is not None
    assert rt.running_elsewhere(second) is None


def test_titled_transcript_maps_to_same_meeting(root):
    """После наката темы имя файла меняется, встреча — нет. Отметка обязана
    находиться и по титульному имени, иначе защита обходится сама собой."""
    titled = root / "transcripts" / "2026-08-12_1532_Планирование_пилота.md"
    titled.write_text("стенограмма", encoding="utf-8")
    rt._pid_file("2026-08-12_1532").write_text(str(os.getppid()), encoding="utf-8")

    assert rt.running_elsewhere(titled) is not None


def test_guard_is_wired_into_entry_point():
    """Сторож проводки: проверка обязана стоять в main до самой работы —
    иначе она написана, но дубль всё равно стартует."""
    text = (REPO / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    body = text[text.index("def main():"):]
    guard = body.index("running_elsewhere(live)")
    work = body.index("status.processing")

    assert guard < work, "проверка после начала работы бессмысленна"
    assert "mark_running(live)" in body, "прогон не отмечает себя — дубль не увидит его"
