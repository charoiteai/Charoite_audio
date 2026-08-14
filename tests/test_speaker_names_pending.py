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
import rebuild_transcript as rt  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

CFG = {"llm": {"model": "тест"}, "sufler": {"user_name": "Владелец"}}
LINES = [("Собеседник 1", "Привет, я Сергей"), ("Собеседник 2", "А я Юля")]


class _FakeLLM:
    """Клиент, отвечающий заготовкой; поднимать Ollama тестам не нужно."""

    answer: str = ""

    def __init__(self, cfg: dict):
        pass

    def complete(self, *a, **k) -> str:
        return self.answer


def test_model_answered_without_names_is_not_a_failure(monkeypatch):
    """«Имён не звучало» — законный ответ, а не потеря."""
    fake = type("F", (_FakeLLM,), {
        "answer": '{"Собеседник 1": "?", "Собеседник 2": "?"}'})
    monkeypatch.setattr(llm_mod, "LLM", fake)

    names, answered = rt.name_speakers(CFG, LINES)

    assert names == {}
    assert answered is True


def test_silent_model_is_reported_as_such(monkeypatch):
    class _Silent(_FakeLLM):
        def complete(self, *a, **k) -> str:
            raise TimeoutError("модель молчит")

    monkeypatch.setattr(llm_mod, "LLM", _Silent)

    names, answered = rt.name_speakers(CFG, LINES)

    assert names == {}
    assert answered is False, "молчание модели неотличимо от «имён нет»"


def test_pending_note_is_found_in_the_transcript(tmp_path):
    live = tmp_path / "2026-08-12_153219.md"
    live.write_text(f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Собеседник 1** [15:32]:\nда\n",
                    encoding="utf-8")

    assert rt.names_pending(live) is True


def test_clean_transcript_has_no_pending_flag(tmp_path):
    live = tmp_path / "2026-08-12_153219.md"
    live.write_text("# Встреча\n\n**Сергей** [15:32]:\nда\n", encoding="utf-8")

    assert rt.names_pending(live) is False


def test_missing_transcript_does_not_break_the_status(tmp_path):
    assert rt.names_pending(tmp_path / "нет-файла.md") is False


def test_ready_status_carries_names_pending(tmp_path):
    live = tmp_path / "transcripts" / "2026-08-12_153219.md"
    live.parent.mkdir()
    live.write_text("# Встреча\nтекст\n", encoding="utf-8")
    note = tmp_path / "graph" / "Встречи" / "2026-08-12_1532.md"
    note.parent.mkdir(parents=True)
    note.write_text("готово", encoding="utf-8")
    store = MeetingStatusStore(tmp_path)

    pending = json.loads(store.ready(live, note, names_pending=True)
                         .read_text(encoding="utf-8"))
    clean = json.loads(store.ready(live, note).read_text(encoding="utf-8"))

    assert pending["state"] == "ready", "встреча разобрана — повторять весь конвейер незачем"
    assert pending["names_pending"] is True
    assert "names_pending" not in clean, "поле появляется только когда есть что сказать"
