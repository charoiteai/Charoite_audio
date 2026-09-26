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

    assert rt.name_speakers(CFG_REBUILD, lines) == ({"Собеседник 1": "Сергей"}, True)


def test_rebuild_names_name_the_reason_for_non_json(_ollama_маршруты, monkeypatch, capsys):
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат("Извините, не могу"))

    lines = [("Собеседник 1", "Привет")]

    assert rt.name_speakers(CFG_REBUILD, lines) == ({}, False), "не-JSON — это «не ответила»"
    out = capsys.readouterr().out
    assert "имена: не удалось" in out and "не-JSON" in out and "(17 знаков)" in out


def test_rebuild_names_name_the_empty_answer(_ollama_маршруты, monkeypatch, capsys):
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    _ollama_маршруты.сценарий_чата(_чат(""))

    assert rt.name_speakers(CFG_REBUILD, [("Собеседник 1", "Привет")]) == ({}, False)
    out = capsys.readouterr().out
    assert "имена: не удалось" in out and "пустой ответ" in out


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
