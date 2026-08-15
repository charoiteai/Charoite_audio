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


# ── Движок mlx-server ────────────────────────────────────────────────────
# Контракт OpenAI-совместимого транспорта. Дефолт остаётся ollama: смена
# боевого движка — отдельное решение после bench_extract на mlx_lm.server
# и замера на живой встрече, а не побочный эффект этого кода.

CFG_MLX = {
    "llm": {"engine": "mlx-server", "model": "тест-модель",
            "small_model": "тест-мелкая", "mlx_model": "mlx-community/тест",
            "num_ctx": 8192, "temperature": 0.4},
    "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"},
}


class _SSEResp:
    """Стриминговый ответ mlx-сервера: SSE-строки + контекстный менеджер."""

    status_code = 200

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_mlx_complete_speaks_openai_and_collapses_models(monkeypatch):
    """/v1/chat/completions; переданный ollama-тег схлопывается в mlx_model.

    Вызовы шага 1 передают small/fallback-теги явно — mlx-сервер их не
    знает, он обслуживает одну модель, с которой запущен.
    """
    wire = _wire(monkeypatch, _Resp(
        {"choices": [{"message": {"content": " Ответ "}}]}))

    out = LLM(CFG_MLX).complete("вопрос", system="роль",
                                model="тест-мелкая", num_predict=7)

    assert out == "Ответ"
    body = wire.sent["json"]
    assert wire.sent["url"].endswith("/v1/chat/completions")
    assert body["model"] == "mlx-community/тест"
    assert body["max_tokens"] == 7
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    for alien in ("options", "format", "keep_alive", "think"):
        assert alien not in body, f"поле Ollama-протокола {alien} утекло в mlx"


def test_mlx_max_tokens_cap_is_always_explicit(monkeypatch):
    """Без num_predict уходит НАШ потолок: у сервера есть свой молчаливый
    дефолт из аргументов запуска, и он резал бы длинные ответы на полуслове —
    внешне неотличимо от короткого ответа модели."""
    wire = _wire(monkeypatch, _Resp({"choices": [{"message": {"content": "х"}}]}))

    LLM(CFG_MLX).complete("в")

    assert wire.sent["json"]["max_tokens"] == llm_mod.MLX_MAX_TOKENS_DEFAULT


def test_mlx_think_none_omits_template_kwargs(monkeypatch):
    """think=None — умолчание модели: kwargs шаблона не передаются вовсе."""
    wire = _wire(monkeypatch, _Resp({"choices": [{"message": {"content": "х"}}]}))

    LLM(CFG_MLX).complete("в", think=None)

    assert "chat_template_kwargs" not in wire.sent["json"]


def test_mlx_stream_parses_sse(monkeypatch):
    """SSE-стрим: «data: {…delta…}», keepalive-комментарии, [DONE].

    Комментарий «: keepalive …» сервер шлёт во время префилла — живой smoke
    15.08 уронил на нём первый вариант парсера (json.loads на не-data строке).
    """
    fake = _Requests(_SSEResp([
        b": keepalive 1/1",
        b'data: {"choices":[{"delta":{"content":"\xd0\x9f\xd1\x80\xd0\xb8"}}]}',
        b"",
        b'data: {"choices":[{"delta":{"content":"\xd0\xb2\xd0\xb5\xd1\x82"}}]}',
        b"data: [DONE]",
        b'data: {"choices":[{"delta":{"content":"\xd1\x85\xd0\xb2\xd0\xbe\xd1\x81\xd1\x82"}}]}',
    ]))
    monkeypatch.setattr(llm_mod, "requests", fake)

    chunks = list(LLM(CFG_MLX).stream("вопрос"))

    assert "".join(chunks) == "Привет", "хвост после [DONE] читать нельзя"
    assert fake.sent["json"]["stream"] is True
    assert fake.sent["url"].endswith("/v1/chat/completions")


def test_mlx_embeddings_stay_on_ollama(monkeypatch):
    """Эмбеддинги движка не выбирают: bge-m3 живёт на Ollama при любом engine."""
    wire = _wire(monkeypatch, _Resp({"embeddings": [[0.5]]}))

    llm_mod.embed(CFG_MLX, ["т"])

    assert wire.sent["url"].endswith("/api/embed")
    assert "11434" in wire.sent["url"], "эмбеддинги уехали с Ollama вслед за чатом"


def test_unknown_engine_is_rejected():
    """Опечатка в llm.engine — ошибка вслух, а не молчаливый откат на ollama."""
    with pytest.raises(RuntimeError, match="неизвестный движок"):
        LLM({"llm": {"engine": "vllm", "model": "х"}, "sufler": {"role": ""}})


def test_mlx_base_url_holds_the_privacy_line():
    """Второй движок — не второй немой путь наружу: чужой адрес в mlx_base_url
    требует того же явного llm.allow_remote, что и llm.base_url."""
    import privacy

    cfg = {"llm": {"engine": "mlx-server", "model": "х",
                   "mlx_base_url": "http://10.0.0.5:8080"}, "sufler": {"role": ""}}
    with pytest.raises(RuntimeError, match="allow_remote"):
        privacy.mlx_base_url(cfg, env={})
