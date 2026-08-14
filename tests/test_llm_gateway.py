"""Единая точка LLM-вызовов (src/llm.py): транспорт живёт в одном месте.

Аудит 14.08 нашёл семь модулей с собственными requests.post, четыре — с
захардкоженными адресом и моделью: боевой конфиг 12.08 переехал на
mlx-сборку, а Саммари и заметки продолжали звать старую модель и не
заметили бы её удаления. Эти тесты закрепляют контракт complete()/embed():
что уходит на провод и как возвращаются ошибки. Ошибка сервера обязана
быть исключением, а не пустой строкой: молчаливая пустышка уже ложилась
поверх готовых минуток (аудит 0.46.0).

Сервер не поднимается: подменён llm.requests.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm as llm_mod  # noqa: E402
from llm import LLM, LLMHTTPError, parse_json_block  # noqa: E402

CFG = {
    "llm": {"model": "тест-модель", "small_model": "тест-мелкая",
            "num_ctx": 8192, "temperature": 0.4},
    "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"},
}


class _Resp:
    def __init__(self, payload: dict, status: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self) -> dict:
        return self._payload


class _Requests:
    """Подмена модуля requests: запоминает запрос, отвечает заготовкой."""

    RequestException = Exception  # except в complete() ссылается на атрибут модуля

    def __init__(self, resp: _Resp):
        self.resp = resp
        self.sent: dict = {}

    def post(self, url, json=None, timeout=None, **kw):
        self.sent = {"url": url, "json": json, "timeout": timeout}
        return self.resp

    def get(self, url, timeout=None, **kw):  # resolve_model() ходит в /api/tags
        return _Resp({"models": []})


def _wire(monkeypatch, resp: _Resp) -> _Requests:
    fake = _Requests(resp)
    monkeypatch.setattr(llm_mod, "requests", fake)
    return fake


def test_complete_returns_text_and_sends_explicit_options(monkeypatch):
    """Модель, num_ctx и temperature уходят в запрос явно.

    num_ctx обязан быть в каждом запросе: без него Ollama грузит модель с
    контекстом из Modelfile и KV-кэш раздувается (замер 20.07 в llm.py).
    """
    wire = _wire(monkeypatch, _Resp({"message": {"content": "  Ответ \n"}}))

    out = LLM(CFG).complete("вопрос", system="роль", model="тест-модель",
                            num_predict=42, timeout=7)

    assert out == "Ответ"
    body = wire.sent["json"]
    assert body["model"] == "тест-модель"
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.4, "num_ctx": 8192,
                               "num_predict": 42}
    assert body["messages"][0] == {"role": "system", "content": "роль"}
    assert body["messages"][1] == {"role": "user", "content": "вопрос"}
    assert wire.sent["timeout"] == 7
    assert wire.sent["url"].endswith("/api/chat")


def test_json_format_and_default_think_off(monkeypatch):
    """format=json по флагу; think по умолчанию выключен явно (TTFT-замер 17.07)."""
    wire = _wire(monkeypatch, _Resp({"message": {"content": "{}"}}))

    LLM(CFG).complete("в", model="м", json_format=True)

    assert wire.sent["json"]["format"] == "json"
    assert wire.sent["json"]["think"] is False


def test_think_none_leaves_model_default(monkeypatch):
    """think=None — поля нет вовсе: разбор и минутки исторически живут на
    умолчании модели, и выключать им рассуждение — отдельное решение с
    замером, а не побочный эффект рефакторинга."""
    wire = _wire(monkeypatch, _Resp({"message": {"content": "х"}}))

    LLM(CFG).complete("в", model="м", think=None)

    assert "think" not in wire.sent["json"]


def test_http_error_is_an_exception_not_empty_string(monkeypatch):
    """404 (модель удалена) — исключение со статусом, а не тихая пустышка."""
    _wire(monkeypatch, _Resp({}, status=404, text="model not found"))

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="нет-такой")

    assert e.value.status == 404
    assert "model not found" in e.value.detail


def test_error_field_in_body_is_an_exception_too(monkeypatch):
    """Ollama умеет отвечать 200 с полем error — это тоже отказ."""
    _wire(monkeypatch, _Resp({"error": "loading model"}))

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="м")

    assert "loading model" in e.value.detail


def test_network_error_without_revive_raises(monkeypatch):
    """Сетевая ошибка без revive уходит наружу: у минуток, графа и заметок
    разная цена отказа, решает вызывающий."""
    fake = _Requests(_Resp({}))

    def boom(*a, **k):
        raise fake.RequestException("нет сети")

    fake.post = boom
    monkeypatch.setattr(llm_mod, "requests", fake)

    with pytest.raises(Exception, match="нет сети"):
        LLM(CFG).complete("в", model="м")


def test_embed_sends_model_from_config_and_keep_alive(monkeypatch):
    wire = _wire(monkeypatch, _Resp({"embeddings": [[0.1, 0.2]]}))

    vecs = llm_mod.embed(CFG, ["текст"], keep_alive="60m")

    assert vecs == [[0.1, 0.2]]
    assert wire.sent["json"] == {"model": "тест-эмбеддер", "input": ["текст"],
                                 "keep_alive": "60m"}
    assert wire.sent["url"].endswith("/api/embed")


def test_embed_without_vectors_returns_empty_list(monkeypatch):
    """Пустой ответ — пустой список: контуру дежавю дешевле пропустить проход."""
    _wire(monkeypatch, _Resp({}))

    assert llm_mod.embed(CFG, ["текст"]) == []


def test_parse_json_block_digs_json_out_of_prose():
    assert parse_json_block('Вот:\n```json\n{"а": 1}\n```\nготово') == {"а": 1}
    assert parse_json_block("слова без JSON") is None
    assert parse_json_block("{битый json") is None
    assert parse_json_block("[1, 2]") is None, "нужен объект, а не список"
