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
import threading
import time

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

    def close(self) -> None:   # _post_busy закрывает ответ «занято» перед паузой
        pass


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


# ---------------------------------------------------------------- занятая модель
# Факт 18.08: Ollama 0.32 с MLX-раннером на занятой модели отвечает 503 за
# ~250 мс вместо очереди; подсказки живой встречи 45 минут падали с
# «[LLM: 503 …]». Клиент обязан переждать «занято», а ошибку внутри стрима и
# обрыв без терминатора — не выдавать за ответ.


class _StreamResp:
    """NDJSON-стрим Ollama: строки + контекстный менеджер + статус."""

    text = "busy"

    def __init__(self, lines: list[bytes], status: int = 200):
        self._lines = lines
        self.status_code = status
        self.closed = False

    def iter_lines(self):
        return iter(self._lines)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise llm_mod.requests.HTTPError(f"{self.status_code} busy")

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _BusyThenOk:
    """Первые N ответов — 503, потом настоящий стрим. Считает попытки."""

    RequestException = Exception
    ConnectionError = ConnectionError
    HTTPError = RuntimeError

    def __init__(self, busy: int, then):
        self.busy, self.then, self.calls = busy, then, 0

    def post(self, url, json=None, timeout=None, **kw):
        self.calls += 1
        self.last = _StreamResp([], status=503) if self.calls <= self.busy else self.then
        return self.last

    def get(self, url, timeout=None, **kw):
        return _Resp({"models": []})


def _no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", slept.append)
    return slept


def test_stream_waits_out_a_busy_model(monkeypatch):
    ok = _StreamResp([b'{"message":{"content":"a"},"done":false}',
                      b'{"message":{"content":"b"},"done":true}'])
    fake = _BusyThenOk(2, ok)
    monkeypatch.setattr(llm_mod, "requests", fake)
    slept = _no_sleep(monkeypatch)

    assert "".join(LLM(CFG).stream("в", model="м")) == "ab"
    assert fake.calls == 3, "две занятые попытки, третья удалась"
    assert slept == [1.0, 2.0], "растущая пауза, а не долбёжка"


def test_stream_gives_up_when_busy_outlasts_budget(monkeypatch):
    fake = _BusyThenOk(100, _StreamResp([]))
    monkeypatch.setattr(llm_mod, "requests", fake)
    _no_sleep(monkeypatch)

    with pytest.raises(LLMHTTPError) as e:   # контракт модуля, не голый requests.HTTPError
        list(LLM(CFG).stream("в", model="м", busy_wait=5))
    assert e.value.status == 503
    assert fake.calls <= 4, "бюджет 5 с: 1+2 с пауз, дальше честная ошибка"
    assert fake.last.closed, "стрим-ответ с ошибкой закрыт до raise, а не оставлен GC"


def test_error_line_inside_stream_is_an_exception(monkeypatch):
    """Ollama шлёт ошибку строкой {"error": …} внутри 200-стрима: раньше поток
    заканчивался «нормально» пустым, и подсказка тихо не приходила."""
    fake = _BusyThenOk(0, _StreamResp([b'{"message":{"content":"a"},"done":false}',
                                       b'{"error":"model runner has unexpectedly stopped"}']))
    monkeypatch.setattr(llm_mod, "requests", fake)

    with pytest.raises(LLMHTTPError, match="runner"):
        list(LLM(CFG).stream("в", model="м"))


def test_stream_without_terminator_is_not_a_full_answer(monkeypatch):
    """Соединение закрылось без done: усечённые минутки не должны выглядеть готовыми."""
    fake = _BusyThenOk(0, _StreamResp(['{"message":{"content":"половина"},"done":false}'
                                       .encode("utf-8")]))
    monkeypatch.setattr(llm_mod, "requests", fake)

    got: list[str] = []
    with pytest.raises(LLMHTTPError, match="оборван"):
        for tok in LLM(CFG).stream("в", model="м"):
            got.append(tok)
    assert got == ["половина"], "что успело прийти — пришло; но это не полный ответ"


def test_mlx_stream_error_and_missing_done(monkeypatch):
    fake = _Requests(_SSEResp([b'data: {"error":{"message":"oom"}}']))
    monkeypatch.setattr(llm_mod, "requests", fake)
    with pytest.raises(LLMHTTPError, match="oom"):
        list(LLM(CFG_MLX).stream("в"))

    fake = _Requests(_SSEResp([b'data: {"choices":[{"delta":{"content":"x"}}]}']))
    monkeypatch.setattr(llm_mod, "requests", fake)
    with pytest.raises(LLMHTTPError, match="оборван"):
        list(LLM(CFG_MLX).stream("в"))


def test_complete_waits_out_a_busy_model_within_budget(monkeypatch):
    class _Busy503:
        RequestException = Exception

        def __init__(self):
            self.calls = 0

        def post(self, url, json=None, timeout=None, **kw):
            self.calls += 1
            if self.calls < 3:
                return _Resp({}, status=503, text="busy")
            return _Resp({"message": {"content": "готово"}})

        def get(self, url, timeout=None, **kw):
            return _Resp({"models": []})

    fake = _Busy503()
    monkeypatch.setattr(llm_mod, "requests", fake)
    slept = _no_sleep(monkeypatch)

    assert LLM(CFG).complete("в", model="м", busy_wait=60) == "готово"
    assert fake.calls == 3 and slept == [1.0, 2.0]


def test_complete_busy_beyond_budget_is_http_error_not_revive(monkeypatch):
    """503 — ответ сервера, не сеть: revive (перезапуск) на него не идёт."""
    fake = _Requests(_Resp({}, status=503, text="busy"))
    monkeypatch.setattr(llm_mod, "requests", fake)
    _no_sleep(monkeypatch)
    import llm_health
    monkeypatch.setattr(llm_health, "ensure_alive",
                        lambda *a, **k: pytest.fail("занятую модель не оживляют перезапуском"))

    with pytest.raises(LLMHTTPError) as e:
        LLM(CFG).complete("в", model="м", timeout=5, revive=True)
    assert e.value.status == 503


def test_embed_on_busy_server_returns_empty_not_valueerror(monkeypatch):
    class _Busy(_Resp):
        def json(self):
            raise ValueError("not json")

    _wire(monkeypatch, _Busy({}, status=503, text="busy"))
    assert llm_mod.embed(CFG, ["текст"]) == []


def test_fit_survives_a_failed_part_and_keeps_head_and_tail(monkeypatch):
    """Одна упавшая часть не роняет минутки; все пустые — голова+хвост, не голова."""
    l = LLM(CFG)
    l.num_ctx = 2000                       # limit = 4000
    calls = {"n": 0}

    def summary(part, busy_wait=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503 busy")
        return iter(["сводка"])

    monkeypatch.setattr(l, "summary", summary)
    text = "А" * 5000 + "Я" * 5000
    out = l._fit(text)
    assert "[Часть 2" in out and "[Часть 1" not in out

    monkeypatch.setattr(l, "summary", lambda part, busy_wait=None: iter([""]))
    out = l._fit(text)
    assert out.startswith("АААА") and out.endswith("ЯЯЯЯ"), "хвост встречи (решения) не теряем"
    assert "опущена" in out


def test_fit_stops_after_two_failed_parts_in_a_row(monkeypatch):
    """Минутки идут под hint_lock: 70 частей по 30 с ожидания занятой модели
    держали бы весь живой контур полчаса. Две части подряд не сжались — отказ
    наружу, а не тихий перебор всех частей (ревью 18.08)."""
    l = LLM(CFG)
    l.num_ctx = 2000
    calls = {"n": 0, "budgets": []}

    def summary(part, busy_wait=None):
        calls["n"] += 1
        calls["budgets"].append(busy_wait)
        raise RuntimeError("503 busy")

    monkeypatch.setattr(l, "summary", summary)
    with pytest.raises(RuntimeError, match="503"):
        l._fit("А" * 40_000)
    assert calls["n"] == 2, "после двух отказов подряд остальные части не мучаем"
    assert all(b == llm_mod.FIT_PART_BUSY_WAIT for b in calls["budgets"]), "у сводок частей маленький бюджет"


# ── Аудит 13.09, зона 4 ──────────────────────────────────────────────────

def test_parse_json_block_takes_the_first_object_not_the_whole_span():
    """Жадный «{.*}» брал диапазон от первой скобки до последней и не разбирал
    ответ с двумя объектами вовсе (GLM M1)."""
    assert parse_json_block('Вот: {"a": 1}\nПримечание: {"b": 2}') == {"a": 1}
    assert parse_json_block('{не json} потом {"x": 2}') == {"x": 2}
    assert parse_json_block('{"a": {"b": 1}} и {"c": 2}') == {"a": {"b": 1}}


def test_garbage_line_in_ndjson_stream_is_an_http_error(monkeypatch):
    """Страница прокси внутри 200-стрима роняла итератор голым ValueError мимо
    контракта LLMHTTPError (DS M2)."""
    fake = _BusyThenOk(0, _StreamResp([b"<html>proxy portal</html>"]))
    monkeypatch.setattr(llm_mod, "requests", fake)
    with pytest.raises(LLMHTTPError, match="не-JSON"):
        list(LLM(CFG).stream("в", model="м"))
    monkeypatch.setattr(llm_mod, "requests", _BusyThenOk(0, _StreamResp([b"[1, 2]"])))
    with pytest.raises(LLMHTTPError, match="форма"):
        list(LLM(CFG).stream("в", model="м"))
    # message: null — пустой чанк, message списком — форма (GLM M1 по #562)
    ok = _StreamResp([b'{"message": null, "done": false}', b'{"message":{"content":"a"},"done":true}'])
    monkeypatch.setattr(llm_mod, "requests", _BusyThenOk(0, ok))
    assert "".join(LLM(CFG).stream("в", model="м")) == "a"
    monkeypatch.setattr(llm_mod, "requests", _BusyThenOk(0, _StreamResp([b'{"message": [1], "done": false}'])))
    with pytest.raises(LLMHTTPError, match="форма"):
        list(LLM(CFG).stream("в", model="м"))


def test_sse_accepts_data_without_space_and_rejects_garbage(monkeypatch):
    """«data:{…}» без пробела — валидный SSE, а не мусор; мусор и неожиданная
    форма — LLMHTTPError, не ValueError/AttributeError (DS M1)."""
    fake = _Requests(_SSEResp([b'data:{"choices":[{"delta":{"content":"x"}}]}',
                               b"data: [DONE]"]))
    monkeypatch.setattr(llm_mod, "requests", fake)
    assert "".join(LLM(CFG_MLX).stream("в")) == "x"

    monkeypatch.setattr(llm_mod, "requests", _Requests(_SSEResp([b"data: <html>oops</html>"])))
    with pytest.raises(LLMHTTPError, match="не-JSON"):
        list(LLM(CFG_MLX).stream("в"))

    monkeypatch.setattr(llm_mod, "requests", _Requests(_SSEResp(['data: {"choices":["строка"]}'.encode("utf-8")])))
    with pytest.raises(LLMHTTPError, match="форма"):
        list(LLM(CFG_MLX).stream("в"))

    # пустое поле data: — keepalive, не обрыв; choices словарём — форма, не KeyError (круг-1 по #562)
    fake = _Requests(_SSEResp([b"data:", b'data:{"choices":[{"delta":{"content":"y"}}]}', b"data: [DONE]"]))
    monkeypatch.setattr(llm_mod, "requests", fake)
    assert "".join(LLM(CFG_MLX).stream("в")) == "y"
    monkeypatch.setattr(llm_mod, "requests", _Requests(_SSEResp([b'data: {"choices":{"0":{"delta":{"content":"x"}}}}'])))
    with pytest.raises(LLMHTTPError, match="форма"):
        list(LLM(CFG_MLX).stream("в"))


def test_cloud_complete_honours_the_callers_read_timeout(monkeypatch):
    """Облачный complete() резал документы 45-секундным CLOUD_TIMEOUT и молча
    уходил на локальную модель; таймаут вызывающего не читался вовсе (DS I1)."""
    inst = LLM(CFG)
    inst.cloud_ready = True
    seen = {}

    def post_busy(url, payload, timeout, wait):
        seen["timeout"] = timeout
        return _Resp({"choices": [{"message": {"content": "ок"}}]})

    monkeypatch.setattr(inst, "_cloud_payload", lambda *a, **k: {"m": 1})
    monkeypatch.setattr(inst, "_post_busy", post_busy)
    assert inst.complete("вопрос", timeout=600) == "ок"
    assert seen["timeout"] == (llm_mod.CLOUD_TIMEOUT[0], 600.0)
    inst.complete("вопрос", timeout=7)
    assert seen["timeout"] == llm_mod.CLOUD_TIMEOUT, "короче облачного минимума не режем"


def test_cloud_fallback_flag_is_strictly_boolean(capsys):
    """«false» в кавычках из YAML — не False: модуль приватности требует явного
    булева, а флаг читался как «не False» (DS M5)."""
    cfg = {**CFG, "llm": {**CFG["llm"], "cloud_fallback_local": "false"}}
    assert LLM(cfg).fallback_local is True
    assert "cloud_fallback_local" in capsys.readouterr().err
    assert LLM({**CFG, "llm": {**CFG["llm"], "cloud_fallback_local": False}}).fallback_local is False
    assert LLM(CFG).fallback_local is True


def test_cloud_stream_honours_the_callers_read_timeout(monkeypatch):
    """Документные стримы (минутки, сводки) в облаке резались 45 с CLOUD_TIMEOUT и
    молча уходили на локальную модель (круг-1 по #562, DS I1)."""
    inst = LLM(CFG)
    inst.cloud_ready = True
    seen = {}

    def sse(url, payload, wait, timeout=None, first_token=None):
        seen["timeout"] = timeout
        yield "ок"

    monkeypatch.setattr(inst, "_cloud_payload", lambda *a, **k: {"m": 1})
    monkeypatch.setattr(inst, "_sse", sse)
    assert "".join(inst.stream("в", timeout=llm_mod.DOC_STREAM_TIMEOUT)) == "ок"
    assert seen["timeout"] == (llm_mod.CLOUD_TIMEOUT[0], llm_mod.DOC_STREAM_TIMEOUT[1])
    "".join(inst.stream("в", timeout=(5.0, 20.0)))
    assert seen["timeout"] == llm_mod.CLOUD_TIMEOUT, "короче облачного минимума не режем"


def test_parse_json_block_prefers_the_fenced_answer_over_a_prose_example():
    text = 'Пример формата: {"заголовок": "X"}.\n```json\n{"заголовок": "Итог"}\n```\nготово'
    assert parse_json_block(text) == {"заголовок": "Итог"}
    assert parse_json_block('```json\nне json\n```\n{"a": 1}') == {"a": 1}


# ── Дверь эмбеддингов: пачки, громкий отказ, контракт векторов (№358) ─────
class _EmbedServer:
    """Подмена requests для /api/embed: отвечает по вектору на текст, помнит
    каждую пачку. `fail_on` — номер запроса (с 1), на котором ответить отказом."""

    RequestException = Exception

    def __init__(self, fail_on: int | None = None, status: int = 400,
                 body: str = 'Post "http://127.0.0.1:1/tokenize": EOF', vectors=None):
        self.inputs: list[list[str]] = []
        self.fail_on, self.status, self.body, self.vectors = fail_on, status, body, vectors

    def post(self, url, json=None, timeout=None, **kw):
        self.inputs.append(list(json["input"]))
        if self.fail_on == len(self.inputs):
            return _Resp({}, status=self.status, text=self.body)
        if self.vectors is not None:
            return _Resp({"embeddings": self.vectors(json["input"], len(self.inputs))})
        return _Resp({"embeddings": [[float(len(t)), 1.0] for t in json["input"]]})


def _embed_wire(monkeypatch, server: _EmbedServer, fresh: bool = True) -> _EmbedServer:
    monkeypatch.setattr(llm_mod, "requests", server)
    if fresh:
        monkeypatch.setattr(llm_mod, "_said", set())
    return server


def test_embed_cuts_a_long_list_into_batches_and_keeps_order(monkeypatch):
    """808 ядер одной пачкой Ollama 0.34 рвала на tokenize (HTTP 400), по 100 —
    отвечала (замер 23.09). Резать — дело двери, порядок векторов — порядок
    текстов."""
    server = _embed_wire(monkeypatch, _EmbedServer())
    texts = ["я" * (i % 7 + 1) for i in range(150)]

    vecs = llm_mod.embed(CFG, texts)

    assert [len(b) for b in server.inputs] == [64, 64, 22]
    assert [v[0] for v in vecs] == [float(len(t)) for t in texts], "порядок векторов сбит"


def test_embed_cuts_by_characters_and_never_drops_a_long_text(monkeypatch):
    server = _embed_wire(monkeypatch, _EmbedServer())
    texts = ["а" * 20_000] * 7 + ["б" * 70_000]

    vecs = llm_mod.embed(CFG, texts)

    assert [len(b) for b in server.inputs] == [3, 3, 1, 1], server.inputs and [len(b) for b in server.inputs]
    assert len(vecs) == len(texts), "длинный текст выпал вместо отдельной пачки"


def test_embed_refusal_names_code_and_body_once(monkeypatch, capsys):
    """Отказ сервера — не молчаливый `[]`: код и тело в stderr, один раз на
    одинаковый ответ (месяц ночь печатала «лежит Ollama» на HTTP 400)."""
    _embed_wire(monkeypatch, _EmbedServer(fail_on=2))
    texts = ["текст"] * 100

    assert llm_mod.embed(CFG, texts) == [], "частичный ответ — не вектор на каждый текст"
    first = capsys.readouterr().err
    assert "HTTP 400" in first and "tokenize" in first and "2/2" in first, first

    # Тот же отказ на другой пачке другого размера — в журнал второй раз не идёт
    _embed_wire(monkeypatch, _EmbedServer(fail_on=1), fresh=False)
    assert llm_mod.embed(CFG, ["текст"] * 10) == []
    assert "HTTP 400" not in capsys.readouterr().err, "тот же отказ повторён в журнал"


@pytest.mark.parametrize("vectors, what", [
    (lambda inp, n: [[] for _ in inp], "пустой вектор"),
    (lambda inp, n: [[1.0, 2.0]] * (len(inp) - 1), "векторов меньше текстов"),
    (lambda inp, n: [[1.0] * (2 if n == 1 else 3) for _ in inp], "размерность скачет между пачками"),
    (lambda inp, n: [["x", "y"] for _ in inp], "не числа"),
])
def test_embed_rejects_vectors_that_break_the_contract(monkeypatch, capsys, vectors, what):
    """`[[], [1.0]]` проходил проверку длины у потребителя и давал косинус 0:
    пара молча не судилась (входной круг №358, Codex I2)."""
    _embed_wire(monkeypatch, _EmbedServer(vectors=vectors))

    assert llm_mod.embed(CFG, ["т"] * 70) == [], what
    assert "не по вектору на текст" in capsys.readouterr().err, what


def test_embed_non_json_answer_is_empty_not_a_crash(monkeypatch):
    class _NotJson(_Resp):
        def json(self):
            raise ValueError("not json")

    class _Server(_EmbedServer):
        def post(self, url, json=None, timeout=None, **kw):
            return _NotJson({}, status=200, text="<html>")

    _embed_wire(monkeypatch, _Server())
    assert llm_mod.embed(CFG, ["текст"]) == []


def test_embed_of_nothing_asks_nothing(monkeypatch):
    server = _embed_wire(monkeypatch, _EmbedServer())
    assert llm_mod.embed(CFG, []) == [] and server.inputs == []


def test_embed_timeout_is_the_budget_of_the_whole_call(monkeypatch, capsys):
    """120 с ревизии на 13 пачках были 26 минутами, 20 с дежавю — четырьмя:
    срок — на весь вызов (круг 1 по коду №358, Opus I1/I3)."""
    часы = [1000.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: часы[0])
    сроки = []

    class _Slow(_EmbedServer):
        def post(self, url, json=None, timeout=None, **kw):
            сроки.append(timeout)
            часы[0] += 8.0                  # каждая пачка — 8 с
            return super().post(url, json=json, timeout=timeout, **kw)

    _embed_wire(monkeypatch, _Slow())
    assert llm_mod.embed(CFG, ["т"] * 200, timeout=20) == [], "срок вышел — не вектор на каждый текст"
    assert сроки == [20.0, 12.0, 4.0], сроки
    assert "не уложились в 20 с" in capsys.readouterr().err


def test_sixteen_search_chunks_stay_one_batch(monkeypatch):
    """Поиск шлёт по 16 кусков чуть больше 4000 знаков — одной пачкой, как раньше
    (круг 1 по коду №358, Opus M2)."""
    server = _embed_wire(monkeypatch, _EmbedServer())
    llm_mod.embed(CFG, ["к" * 4_400] * 16)
    assert [len(b) for b in server.inputs] == [16]


def test_a_different_bad_answer_is_not_silenced_by_the_first(monkeypatch, capsys):
    """Ключ отказа — с формой ответа: в демоне, который живёт днями, второй сбой
    другой формы не молчит (круг 1 по коду №358, Opus M3)."""
    _embed_wire(monkeypatch, _EmbedServer(vectors=lambda inp, n: [[1.0]] * (len(inp) - 1)))
    llm_mod.embed(CFG, ["т"] * 5)
    _embed_wire(monkeypatch, _EmbedServer(vectors=lambda inp, n: {"x": 1}), fresh=False)
    llm_mod.embed(CFG, ["т"] * 5)
    assert capsys.readouterr().err.count("не по вектору на текст") == 2


# ── №265: кэш сводок частей длинной встречи ──────────────────────────────

def _counting_summary(monkeypatch, l, calls: list, text: str = "сводка"):
    def summary(part, busy_wait=None):
        calls.append(part)
        return iter([text])
    monkeypatch.setattr(l, "summary", summary)


def _short_llm(**over) -> LLM:
    l = LLM(CFG)
    l.num_ctx = 2000                       # limit = 4000: 10 000 знаков — пять частей по 2000
    for k, v in over.items():
        setattr(l, k, v)
    return l


LONG = "А" * 5000 + "Я" * 5000


def test_second_fit_of_the_same_speech_does_not_summarise_again(monkeypatch):
    """Повтор минуток через MCP строит НОВЫЙ LLM: сводки частей берутся из
    кэша модуля, а не считаются заново."""
    calls: list = []
    first = _short_llm()
    _counting_summary(monkeypatch, first, calls)
    out = first.fit(LONG)
    assert len(calls) == 5 and "[Часть 3 из 5]" in out
    again = _short_llm()
    _counting_summary(monkeypatch, again, calls)
    assert again.fit(LONG) == out
    assert len(calls) == 5, "повтор той же речи не зовёт summary"
    assert again.fit("Б" * 100) == "Б" * 100, "короткая речь идёт как есть"


@pytest.mark.parametrize("change", [{"lang": "en"}, {"small": "другая-модель"}, {"num_ctx": 2001}])
def test_fit_is_recomputed_when_the_summary_settings_change(monkeypatch, change):
    calls: list = []
    l = _short_llm()
    _counting_summary(monkeypatch, l, calls)
    l.fit(LONG)
    other = _short_llm(**change)
    _counting_summary(monkeypatch, other, calls)
    other.fit(LONG)
    assert len(calls) == 10, f"{change}: сводки другой настройки — пересчёт"


def test_fit_is_recomputed_when_the_summary_prompt_version_changes(monkeypatch):
    calls: list = []
    l = _short_llm()
    _counting_summary(monkeypatch, l, calls)
    l.fit(LONG)
    monkeypatch.setattr(llm_mod, "FIT_PROMPT_VERSION", llm_mod.FIT_PROMPT_VERSION + 1)
    l.fit(LONG)
    assert len(calls) == 10


def test_fit_with_a_hole_or_head_and_tail_is_not_cached(monkeypatch):
    """Упавшая или пустая часть — результат с дырой: повтор обязан попробовать
    снова. «Голова и хвост» — не свёртка, её не кэшируем никогда."""
    l = _short_llm()
    calls: list = []

    def one_fails(part, busy_wait=None):
        calls.append(part)
        if len(calls) == 1:
            raise RuntimeError("503 busy")
        return iter(["сводка"])

    monkeypatch.setattr(l, "summary", one_fails)
    assert "[Часть 1" not in l.fit(LONG)
    _counting_summary(monkeypatch, l, calls)
    assert "[Часть 1 из 5]" in l.fit(LONG), "дыру закрыл повтор, а не кэш"
    assert len(calls) == 10

    other = "Б" * 5000 + "Ю" * 5000
    _counting_summary(monkeypatch, l, calls, text="")
    assert "опущена" in l.fit(other)
    _counting_summary(monkeypatch, l, calls)
    assert "[Часть 1 из 5]" in l.fit(other), "голова и хвост не легли в кэш"

    third = "В" * 5000 + "Э" * 5000
    empties = iter(["сводка", "", "сводка", "сводка", "сводка"])
    monkeypatch.setattr(l, "summary", lambda part, busy_wait=None: iter([next(empties)]))
    assert "[Часть 2" not in l.fit(third)
    _counting_summary(monkeypatch, l, calls)
    assert "[Часть 2 из 5]" in l.fit(third), "пустая сводка части — тоже дыра"


def test_fit_cache_entry_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    calls: list = []
    l = _short_llm()
    _counting_summary(monkeypatch, l, calls)
    l.fit(LONG)
    now[0] += llm_mod.FIT_CACHE_TTL - 1
    l.fit(LONG)
    assert len(calls) == 5, "до срока — из кэша"
    now[0] = 1000.0 + llm_mod.FIT_CACHE_TTL
    l.fit(LONG)
    assert len(calls) == 10, "срок вышел ровно — пересчёт"
    assert llm_mod.FIT_CACHE_TTL == 30 * 60


def test_fifth_entry_evicts_the_least_recently_used(monkeypatch):
    assert llm_mod.FIT_CACHE_SIZE == 4
    calls: list = []
    l = _short_llm()
    _counting_summary(monkeypatch, l, calls)
    speeches = [ch * 10_000 for ch in "АБВГД"]
    for s in speeches[:4]:
        l.fit(s)
    l.fit(speeches[0])                     # прочитанная — самая свежая
    assert len(calls) == 20
    l.fit(speeches[4])                     # пятая вытесняет самую давнюю — Б
    assert len(calls) == 25
    l.fit(speeches[0])
    assert len(calls) == 25, "прочитанная недавно осталась"
    l.fit(speeches[1])
    assert len(calls) == 30, "самая давняя вытеснена"


def test_rewriting_an_entry_makes_it_the_freshest(monkeypatch):
    """Две одновременные свёртки одной речи пишут ключ дважды: вторая запись
    — снова самая свежая, а не остаётся на месте первой."""
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: 0.0)
    for k in ("а", "б", "в"):
        llm_mod._fit_cache_put((k,), k)
    llm_mod._fit_cache_put(("а",), "а2")
    for k in ("г", "д"):
        llm_mod._fit_cache_put((k,), k)
    assert llm_mod._fit_cache_get(("а",)) == "а2"
    assert llm_mod._fit_cache_get(("б",)) is None


@pytest.mark.parametrize("before, after", [
    ({}, {"engine": "mlx-server"}),
    ({"engine": "mlx-server", "mlx_model": "mlx/одна"}, {"engine": "mlx-server", "mlx_model": "mlx/другая"}),
    ({"cloud_ready": True, "cloud_model": "облако-1"}, {"cloud_ready": True, "cloud_model": "облако-2"}),
    ({}, {"base": "http://127.0.0.1:11435"}),
])
def test_fit_is_recomputed_when_another_model_would_answer(monkeypatch, before, after):
    """small — не вся правда о том, кто пишет сводку: mlx-server гонит
    mlx_model, облако — cloud_model по своему адресу. Сменили движок, модель
    сервера или адрес — сводки прежней модели не годятся."""
    calls: list = []
    l = _short_llm(**before)
    _counting_summary(monkeypatch, l, calls)
    l.fit(LONG)
    other = _short_llm(**after)
    _counting_summary(monkeypatch, other, calls)
    other.fit(LONG)
    assert len(calls) == 10, f"{before} → {after}: пересчёт"


def test_daemon_minutes_do_not_leave_digests_in_memory(monkeypatch):
    """Кэш — для повтора MCP-минуток. Демон живёт днями, и сводки его встреч
    в памяти держать незачем: PRIVACY обещает только процесс MCP-сервера."""
    calls: list = []
    l = _short_llm()
    _counting_summary(monkeypatch, l, calls)
    monkeypatch.setattr(l, "_doc_stream", lambda *a, **kw: iter(["минутки"]))
    assert "".join(l.minutes(LONG)) == "минутки"
    assert len(calls) == 5 and not llm_mod._fit_cache
    l.fit(LONG)
    assert len(calls) == 10, "минутки демона не наполнили кэш для MCP"


def _sweepers() -> list:
    return [t for t in threading.enumerate()
            if getattr(t, "function", None) is llm_mod._fit_cache_sweep and t.is_alive()]


def _until(cond, deadline: float = 2.0) -> bool:
    end = time.monotonic() + deadline
    while not cond():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


def test_expired_digests_leave_memory_without_another_call(monkeypatch):
    """«До 30 минут» — это про память, а не только про выдачу: истёкшую
    запись убирает настоящий таймер, даже если за ней никто не придёт."""
    now = [1000.0]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    monkeypatch.setattr(llm_mod, "FIT_CACHE_SWEEP", 0.01)
    llm_mod._fit_cache_put(("а",), "сводка")
    assert llm_mod._fit_sweeper.daemon, "уборщик не держит процесс MCP-сервера на выходе"
    time.sleep(0.05)                       # несколько тиков: живая запись на месте
    assert ("а",) in llm_mod._fit_cache and llm_mod._fit_sweeper is not None
    now[0] += llm_mod.FIT_CACHE_TTL
    assert _until(lambda: not llm_mod._fit_cache and llm_mod._fit_sweeper is None), \
        "таймер убрал истёкшую сводку и не взвёлся на пустой кэш"
    assert _until(lambda: not _sweepers())


def test_sweeper_wakes_at_the_nearest_deadline_not_a_full_step_later(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    llm_mod._fit_cache_put(("а",), "а")
    assert llm_mod._fit_sweeper.interval == llm_mod.FIT_CACHE_SWEEP == 60
    llm_mod._fit_sweeper.cancel()
    llm_mod._fit_sweeper = None
    now[0] += llm_mod.FIT_CACHE_TTL - 5
    llm_mod._fit_cache_put(("б",), "б")
    assert llm_mod._fit_sweeper.interval == 5, "до срока «а» — 5 с, а не минута"


def test_a_stale_sweeper_does_not_start_a_second_chain(monkeypatch):
    """Настоящая гонка: таймер сработал и ждёт замка, а clear и новая запись
    успели раньше. Проснувшись, он не трогает таймер новой записи — уборщик
    всегда один, и clear его останавливает."""
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: 1000.0)
    monkeypatch.setattr(llm_mod, "FIT_CACHE_SWEEP", 0.01)
    assert _until(lambda: not _sweepers()), "отменённые таймеры прошлых тестов вышли"
    llm_mod._fit_cache_put(("а",), "а")
    old = llm_mod._fit_sweeper
    with llm_mod._fit_cache_lock:
        # Timer ставит finished только после функции, а она ждёт этот замок:
        # ждём с запасом больше интервала, пока старый проснётся
        time.sleep(0.1)
        llm_mod._fit_cache.clear()         # тело _fit_cache_clear — под тем же замком
        old.cancel()
        llm_mod._fit_sweeper = None
    monkeypatch.setattr(llm_mod, "FIT_CACHE_SWEEP", 60.0)   # новая цепочка стоит на месте
    llm_mod._fit_cache_put(("б",), "б")
    new = llm_mod._fit_sweeper
    assert _until(lambda: not old.is_alive())
    assert llm_mod._fit_sweeper is new and _sweepers() == [new], \
        "проснувшийся старый таймер не завёл вторую цепочку"
    llm_mod._fit_cache_sweep()             # и вызов не из таймера-владельца — не в счёт
    assert llm_mod._fit_sweeper is new and _sweepers() == [new]
    llm_mod._fit_cache_clear()
    assert llm_mod._fit_sweeper is None and _until(lambda: not _sweepers())


def test_any_access_drops_every_expired_entry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    llm_mod._fit_cache_put(("а",), "а")
    now[0] += llm_mod.FIT_CACHE_TTL
    assert llm_mod._fit_cache_get(("б",)) is None
    assert not llm_mod._fit_cache, "чужая истёкшая запись ушла при промахе"
    llm_mod._fit_cache_put(("в",), "в")
    now[0] += llm_mod.FIT_CACHE_TTL
    llm_mod._fit_cache_put(("г",), "г")
    assert list(llm_mod._fit_cache) == [("г",)], "и при записи"


def test_clock_going_back_does_not_extend_an_entry(monkeypatch):
    """Стенные часы могут уйти назад (перевод, синхронизация): запись из
    «будущего» не живёт лишние полчаса, а считается истёкшей."""
    now = [1000.0]
    monkeypatch.setattr(llm_mod, "_fit_clock", lambda: now[0])
    llm_mod._fit_cache_put(("а",), "а")
    now[0] -= 1
    assert llm_mod._fit_cache_get(("а",)) is None


def test_fit_cache_runs_on_wall_clock():
    """monotonic на macOS и Linux стоит, пока ноутбук спит: «30 минут»
    растянулись бы на ночь с закрытой крышкой."""
    assert llm_mod._fit_clock is time.time
