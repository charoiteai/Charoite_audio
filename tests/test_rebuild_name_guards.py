"""Имена после пересборки проходят те же гварды доверия, что живое опознание.

Аудит зон 12.09 (зона 1): name_speakers принимал ответ модели почти на веру
— владелец отсекался только полной строкой user_name («Игорь» против
«Игорь Ветров» проходил), выдуманное имя не сверялось с текстом, обращение в
собственной реплике («Маш, ты смету видела?») делало говорящего Машей.
Уступка живой встрече шла без потолка — очередь пересборок парковалась на
всю чужую встречу. Ollama здесь не поднимается: подменён клиент llm.LLM.
"""
from __future__ import annotations

import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm as llm_mod  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

CFG = {"llm": {"model": "тест"}, "sufler": {"user_name": "Игорь Ветров"}}


def _model(monkeypatch, answer: dict) -> None:
    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, *a, **k) -> str:
            return json.dumps(answer, ensure_ascii=False)
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)


def test_owner_first_name_is_not_given_to_a_participant(monkeypatch):
    """Р1: «Игорь» — это «Игорь Ветров», владелец; собеседнику не достаётся."""
    lines = [("Собеседник 2", "Игорь, привет, это Сергей"), ("Собеседник 1", "Привет, Сергей")]
    _model(monkeypatch, {"Собеседник 1": "Игорь", "Собеседник 2": "Сергей"})
    names, answered = rt.name_speakers(CFG, lines)
    assert names == {"Собеседник 2": "Сергей"}
    assert answered is True


def test_invented_name_is_refused(monkeypatch):
    """Р3: имени, которого в разговоре не было, не существует."""
    lines = [("Собеседник 1", "Смету посмотрим завтра"), ("Собеседник 2", "Хорошо")]
    _model(monkeypatch, {"Собеседник 1": "Виктор", "Собеседник 2": "?"})
    names, answered = rt.name_speakers(CFG, lines)
    assert names == {}
    assert answered is True, "отказ гварда — не молчание модели"


def test_address_in_own_line_is_not_the_speaker(monkeypatch):
    """Р2: «Маш, ты смету видела?» говорит НЕ Маша."""
    lines = [("Собеседник 1", "Маш, ты смету видела?"), ("Собеседник 2", "Потом посмотрю")]
    _model(monkeypatch, {"Собеседник 1": "Маша"})
    assert rt.name_speakers(CFG, lines) == ({}, True)


def test_vocative_from_the_other_side_is_accepted_in_nominative(monkeypatch):
    """Обращение «Маш» с другой стороны — законный источник имени «Маша»:
    промпт просит именительный падеж, гвард не должен принимать его за выдумку."""
    lines = [("Собеседник 1", "Маш, ты смету видела?"), ("Собеседник 2", "Видела, завтра пришлю")]
    _model(monkeypatch, {"Собеседник 2": "Маша"})
    assert rt.name_speakers(CFG, lines) == ({"Собеседник 2": "Маша"}, True)


def test_junk_keys_and_values_are_ignored(monkeypatch):
    lines = [("Собеседник 1", "Привет, я Сергей")]
    _model(monkeypatch, {"Я": "Сергей", "Собеседник 1": 7, "Собеседник 9": " ", "Собеседник 3": "?"})
    assert rt.name_speakers(CFG, lines) == ({}, True)


def test_yield_to_live_has_a_cap(monkeypatch):
    """Очередь пересборок уже взята — уступка живой встрече не бесконечна."""
    seen: dict = {}

    def fake_yield(what, cap=None):
        seen.update(what=what, cap=cap)

    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, *a, **k) -> str:
            return "{}"
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", fake_yield)
    rt.name_speakers(CFG, [("Собеседник 1", "да")])
    assert seen == {"what": "имена", "cap": 600}
