"""Имена говорящих, когда сервер модели не умеет строгий JSON.

Сборка без грамматики отвечает на запрос с `format` отказом, клиент повторяет
без него — и модель отдаёт имена прозой или в ```-заборе. Прежде
`json.loads(raw or "{}")` ронял разбор, и оба модуля печатали «имена: не
удалось» без причины; теперь ответ разбирает `parse_json_block`, а на не-JSON
видно, что именно пришло. Ход — через маршрут сторожа сети `сценарий_чата`.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import diarize  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

CFG_REBUILD = {"llm": {"model": "тест"},
               "sufler": {"role": "тестовая роль", "user_name": "Игорь Ветров"}}
CFG_DIARIZE = {"llm": {"model": "тест"}, "sufler": {"role": "тестовая роль"}}


def _ответ(текст: str) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps({"message": {"content": текст}}, ensure_ascii=False).encode("utf-8")
    return r


def _чат(текст: str):
    return lambda url, **k: _ответ(текст)


def test_rebuild_names_read_the_fenced_json(_ollama_маршруты, monkeypatch):
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат('Вот имена:\n```json\n{"Собеседник 1": "Сергей"}\n```\n'))

    lines = [("Собеседник 1", "Привет, я Сергей")]

    assert rt.name_speakers(CFG_REBUILD, lines) == rt.NamesOutcome({"Собеседник 1": "Сергей"}, rt.NamesOutcome.ANSWERED, 1)


def test_rebuild_names_name_the_reason_for_non_json(_ollama_маршруты, monkeypatch, capsys):
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат("Извините, не могу"))

    lines = [("Собеседник 1", "Привет")]

    assert rt.name_speakers(CFG_REBUILD, lines) == rt.NamesOutcome({}, rt.NamesOutcome.SILENT), "не-JSON — это «не ответила»"
    out = capsys.readouterr().out
    assert "имена: не удалось" in out and "не-JSON" in out and "(17 знаков)" in out


def test_rebuild_names_name_the_empty_answer(_ollama_маршруты, monkeypatch, capsys):
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат(""))

    assert rt.name_speakers(CFG_REBUILD, [("Собеседник 1", "Привет")]) == rt.NamesOutcome({}, rt.NamesOutcome.SILENT)
    out = capsys.readouterr().out
    assert "имена: не удалось" in out and "пустой ответ" in out



def test_rebuild_names_log_which_guard_refused(_ollama_маршруты, monkeypatch, capsys):
    """Какое правило отвергло имя — в журнале разбора, а не общим перечнем (№502)."""
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат('{"Собеседник 1": "Ольга"}'))

    lines = [("Собеседник 1", "Привет, я Сергей")]

    assert rt.name_speakers(CFG_REBUILD, lines) == rt.NamesOutcome({}, rt.NamesOutcome.REJECTED, 1)
    out = capsys.readouterr().out
    assert "имена: «Ольга» для «Собеседник 1» не принято — не звучало в разговоре" in out

def test_diarize_names_read_the_fenced_json(_ollama_маршруты):
    _ollama_маршруты.сценарий_чата(_чат('```json\n{"speaker_0": "Сергей"}\n```'))

    lines = [("speaker_0", 0.0, 1.0, "Привет, я Сергей")]

    assert diarize.name_speakers(CFG_DIARIZE, lines) == {"speaker_0": "Сергей"}


def test_diarize_names_name_the_reason_for_non_json(_ollama_маршруты, capsys):
    _ollama_маршруты.сценарий_чата(_чат("Извините, не могу"))

    lines = [("speaker_0", 0.0, 1.0, "Привет")]

    assert diarize.name_speakers(CFG_DIARIZE, lines) == {}
    out = capsys.readouterr().out
    assert "имена: не удалось" in out and "не-JSON" in out and "(17 знаков)" in out


@pytest.mark.parametrize("заглушка", ["Имя", "«Имя»", "имя.", "Кто-то"])
def test_diarize_names_keep_only_names_heard_in_the_talk(_ollama_маршруты, заглушка):
    """Без грамматики модель повторяет образец из промпта и выдумывает: имя,
    которого в разговоре не слышно, — не имя, как бы оно ни было написано;
    чужие метки — тоже нет. Гвард тот же, что у пересборки (круги 1–2, DS M5/I2)."""
    _ollama_маршруты.сценарий_чата(_чат(
        f'Вот: {{"speaker_0": "{заглушка}", "speaker_1": "Павел", "speaker_9": "Павел"}}'))

    lines = [("speaker_0", 0.0, 1.0, "Привет"), ("speaker_1", 1.0, 2.0, "Меня зовут Павел")]

    assert diarize.name_speakers(CFG_DIARIZE, lines) == {"speaker_1": "Павел"}


@pytest.mark.parametrize("ответ, реплика", [
    ('{"speaker_0": "Павел"}', "Звонил Павелко"),                 # только внутри слова
    ('{"speaker_0": "speaker_1"}', "Привет"),                      # метка вместо имени
    ('{"speaker_0": "Да"}', "Да, конечно"),                        # короче трёх букв
    ('{"speaker_0": "Павел Иванович"}', "Меня зовут Павел Иванович"),  # не одно слово
], ids=["подстрока", "метка", "короткое", "два-слова"])
def test_diarize_names_follow_the_same_limits(_ollama_маршруты, ответ, реплика):
    """Правила проверки имён в CLI — все, а не только эхо образца: целым
    словом, не метка, 3–15 букв, одно слово (выходной круг 3, DS I2/M1)."""
    _ollama_маршруты.сценарий_чата(_чат(ответ))

    lines = [("speaker_0", 0.0, 1.0, реплика), ("speaker_1", 1.0, 2.0, "Ага")]

    assert diarize.name_speakers(CFG_DIARIZE, lines) == {}


@pytest.mark.parametrize("имя", ["Лев", "Константиновича"], ids=["3-буквы", "15-букв"])
def test_diarize_names_accept_the_edges_of_the_length_limit(_ollama_маршруты, имя):
    """Пределы включительные, как у speaker_names: 3 и 15 букв — ещё имя."""
    _ollama_маршруты.сценарий_чата(_чат(f'{{"speaker_0": "{имя}"}}'))

    lines = [("speaker_0", 0.0, 1.0, f"Спросите {имя}, он знает")]

    assert diarize.name_speakers(CFG_DIARIZE, lines) == {"speaker_0": имя}
