"""Провал разбора имён не должен выглядеть как удачный прогон.

12.08, встреча 15:32: локальная модель не ответила при разборе имён — пустой
ответ, JSON не распарсился. Пересборка продолжилась, стенограмма ушла с
метками «Собеседник 1..5», статус встречи получился неотличимым от полностью
удачного. Тот же класс тихой деградации, что чинили в ночных досье: шаг
«прошёл вхолостую» снаружи выглядит как «шаг прошёл».

Свойства, которые закрепляют тесты:

1) name_speakers различает «имён в разговоре не звучало» (нормально) и
   «модель молчала» (потеря) — по одному пустому словарю их не отличить;
2) при молчащей модели и оставшихся безымянных метках стенограмма получает
   пометку в шапке — человек открывает файл, а не logs/;
3) молчание модели при полностью названных участниках ничего не портит и
   пометки не даёт;
4) статус встречи несёт names_pending, и поле появляется только когда есть
   что сказать — читатели старого документа не ломаются.

Ollama здесь не поднимается: подменён клиент llm.LLM — единственная точка,
через которую name_speakers ходит в модель после консолидации транспорта.
"""
from __future__ import annotations

import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm as llm_mod  # noqa: E402
import meeting_processing  # noqa: E402
import rebuild_transcript as rt  # noqa: E402
import transcript  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

CFG = {"llm": {"model": "тест"}, "sufler": {"user_name": "Владелец"}}
LINES = [("Собеседник 1", "Привет, я Сергей"), ("Собеседник 2", "А я Юля")]


class _FakeLLM:
    """Клиент, отвечающий заготовкой; поднимать Ollama тестам не нужно."""

    answer: str = ""
    lang = "ru"

    def __init__(self, cfg: dict):
        pass

    recording_block = llm_mod.LLM.recording_block   # настоящий блок, взят до подмены llm.LLM

    def complete(self, *a, **k) -> str:
        return self.answer


def test_model_answered_without_names_is_not_a_failure(monkeypatch):
    """«Имён не звучало» — законный ответ, а не потеря."""
    fake = type("F", (_FakeLLM,), {
        "answer": '{"Собеседник 1": "?", "Собеседник 2": "?"}'})
    monkeypatch.setattr(llm_mod, "LLM", fake)

    out = rt.name_speakers(CFG, LINES)

    assert out.names == {}
    assert out.outcome == rt.NamesOutcome.ANSWERED


def test_silent_model_is_reported_as_such(monkeypatch):
    class _Silent(_FakeLLM):
        def complete(self, *a, **k) -> str:
            raise TimeoutError("модель молчит")

    monkeypatch.setattr(llm_mod, "LLM", _Silent)

    out = rt.name_speakers(CFG, LINES)

    assert out.names == {}
    assert out.outcome == rt.NamesOutcome.SILENT, "молчание модели неотличимо от «имён нет»"


def test_pending_note_is_found_in_the_transcript(tmp_path):
    live = tmp_path / "2026-08-12_153219.md"
    live.write_text(f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Собеседник 1** [15:32]:\nда\n",
                    encoding="utf-8")

    assert transcript.read_names_pending(live.read_text(encoding="utf-8")).pending is True


def test_both_notes_share_the_prefix_the_flag_looks_for():
    """№499: две причины — два текста, признак один. Обе плашки начинаются с
    общего префикса, по нему names_pending и узнаёт любую."""
    rejected = rt.NAMES_REJECTED_NOTE.format(proposed=2)
    assert rt.NAMES_PENDING_NOTE.startswith(rt.NAMES_PENDING_PREFIX)
    assert rejected.startswith(rt.NAMES_PENDING_PREFIX)
    assert rejected != rt.NAMES_PENDING_NOTE


def test_rejected_note_names_the_count_and_does_not_promise_a_rebuild():
    """Модель ответила, гварды отвергли всё: пересборка на том же тексте упрётся в
    те же гварды — совета «пересобрать» и слов «не ответила» в плашке нет (№499)."""
    rejected = rt.NAMES_REJECTED_NOTE.format(proposed=2)
    assert "(2)" in rejected
    assert "пересоберите" not in rejected and "не ответила" not in rejected
    assert "впишите имена" in rejected


def test_flag_finds_the_rejected_note_and_the_note_written_before_the_split(tmp_path):
    """Признак видит плашку любой причины и плашку, записанную до №499 (её текст
    зашит литералом — константа с тех пор могла измениться)."""
    before_split = ("> ⚠️ Имена участников не определены: модель не ответила на разборе. "
                    "Метки остались «Собеседник N» — пересоберите встречу, когда модель "
                    "свободна (кнопка «Пересобрать» или src/rebuild_transcript.py).")
    for note in (rt.NAMES_REJECTED_NOTE.format(proposed=1), before_split):
        live = tmp_path / "2026-09-29_103224.md"
        live.write_text(f"# Встреча\n\n{note}\n\n**Собеседник 1** [10:32]:\nда\n", encoding="utf-8")
        assert transcript.read_names_pending(live.read_text(encoding="utf-8")).pending is True, note


def test_clean_transcript_has_no_pending_flag(tmp_path):
    live = tmp_path / "2026-08-12_153219.md"
    live.write_text("# Встреча\n\n**Сергей** [15:32]:\nда\n", encoding="utf-8")

    assert transcript.read_names_pending(live.read_text(encoding="utf-8")).pending is False


def test_missing_transcript_does_not_break_the_status(tmp_path):
    """Файла нет — это не потеря: `_names_of` отдаёт None, пайплайн не падает."""
    assert meeting_processing._names_of(tmp_path / "нет-файла.md") is None


def test_ready_status_carries_names_pending(tmp_path):
    """Поле появляется, только когда в файле ещё есть безымянные метки под плашкой."""
    live = tmp_path / "transcripts" / "2026-08-12_153219.md"
    live.parent.mkdir()
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    live.write_text(f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 1** [15:32]:\nда\n",
                    encoding="utf-8")
    note = tmp_path / "graph" / "Встречи" / "2026-08-12_1532.md"
    note.parent.mkdir(parents=True)
    note.write_text("готово", encoding="utf-8")
    store = MeetingStatusStore(tmp_path)

    pending = json.loads(store.ready(live, note).read_text(encoding="utf-8"))
    live.write_text("# Встреча 2026-08-12_153219\n\n**Сергей** [15:32]:\nда\n", encoding="utf-8")
    clean = json.loads(store.ready(live, note).read_text(encoding="utf-8"))

    assert pending["state"] == "ready", "встреча разобрана — повторять весь конвейер незачем"
    assert pending["names_pending"] is True and pending["names_reason"] == "silent"
    assert "names_pending" not in clean and "names_reason" not in clean, \
        "поле появляется только когда есть что сказать"
