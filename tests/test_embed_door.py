"""Дверь векторов (№358): пачки, бюджет, строки стока и транспорт-значение.

Дверь принимает транспорт значением: `post(url, payload, timeout) -> (status,
text)`. Здесь он — кортеж-заготовка, часы — `embed_door.time`; настоящий
`requests` живёт в адаптере (`llm._requests_post`), настоящий сокет — только в
тесте под маркером `сеть_разрешена`.
"""
from __future__ import annotations

import http.client
import http.server
import inspect
import io
import json
import pathlib
import sys
import threading
import urllib.error
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import embed_door  # noqa: E402
import graph_search  # noqa: E402
from model_seam import SeamTransportError  # noqa: E402


def _векторы(texts: list[str]) -> list[list[float]]:
    return [[float(len(t)), 1.0] for t in texts]


class Wire:
    """Транспорт-кортеж: помнит пачки и тело, отвечает заготовкой."""

    def __init__(self, vectors=None, status: int = 200, body: str | None = None,
                 raw: str | None = None, fail_on: int | None = None):
        self.inputs: list[list[str]] = []
        self.payloads: list[dict] = []
        self.timeouts: list[float] = []
        self.vectors, self.status, self.body, self.raw, self.fail_on = (
            vectors, status, body, raw, fail_on)

    def __call__(self, url, payload, timeout):
        self.inputs.append(list(payload["input"]))
        self.payloads.append(dict(payload))
        self.timeouts.append(timeout)
        if self.raw is not None:
            return self.status, self.raw
        if self.fail_on == len(self.inputs) or (self.fail_on is None and self.status != 200):
            return self.status, (self.body if self.body is not None else "отказ")
        got = (self.vectors(payload["input"], len(self.inputs)) if self.vectors
               else _векторы(payload["input"]))
        return 200, json.dumps({"embeddings": got})


def _дверь(*, post=None, refused: str = "", keep_alive: str | None = None):
    kwargs = {"refused": refused, "keep_alive": keep_alive}
    if post is not None:
        kwargs["post"] = post
    return embed_door.embedder("http://127.0.0.1:11434", "m", **kwargs)


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://127.0.0.1:1/api/embed", code, "err", {}, io.BytesIO(body))


# ── пачки, порядок, «всё или ничего» ─────────────────────────────────────

def test_batches_cut_by_texts_and_keep_order():
    w = Wire()
    texts = ["я" * (i % 7 + 1) for i in range(150)]

    vecs = _дверь(post=w).run(texts, 30)

    assert [len(b) for b in w.inputs] == [64, 64, 22], w.inputs
    assert [v[0] for v in vecs] == [float(len(t)) for t in texts], "порядок векторов сбит"


def test_batches_cut_by_characters_and_never_drop_a_long_text():
    w = Wire()
    texts = ["а" * 20_000] * 7 + ["б" * 70_000]

    vecs = _дверь(post=w).run(texts, 60)

    assert [len(b) for b in w.inputs] == [3, 3, 1, 1], w.inputs
    assert len(vecs) == len(texts), "длинный текст выпал вместо отдельной пачки"


def test_the_char_budget_is_a_strict_ceiling():
    """Ровно 72 000 знаков — ещё одна пачка: потолок не включается на равенстве."""
    w = Wire()
    _дверь(post=w).run(["а" * 36_000, "б" * 36_000], 30)
    assert [len(b) for b in w.inputs] == [2], w.inputs


def test_a_failed_batch_returns_nothing_not_a_partial_answer():
    w = Wire(fail_on=2, status=400, body='Post "tokenize": EOF')

    assert _дверь(post=w).run(["текст"] * 100, 30) == [], "частичный ответ — не вектор на каждый текст"
    assert [len(b) for b in w.inputs] == [64, 36], w.inputs


def test_nothing_asked_about_nothing():
    w = Wire()
    assert _дверь(post=w).run([], 30) == [] and w.inputs == []


# ── строки стока: один раз на ключ ───────────────────────────────────────

def _бюджет(monkeypatch):
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])

    def post(url, payload, timeout):
        часы[0] += 8.0                            # пачка дороже оставшегося срока
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    e = _дверь(post=post)
    return lambda: e.run(["т"] * 200, 20)


def _http(monkeypatch):
    e = _дверь(post=Wire(status=400, body='Post "tokenize": EOF <err>'))
    return lambda: e.run(["т"], 10)


def _не_json(monkeypatch):
    e = _дверь(post=Wire(raw="<html>"))
    return lambda: e.run(["т"], 10)


def _не_те_векторы(monkeypatch):
    e = _дверь(post=Wire(vectors=lambda inp, n: [[] for _ in inp]))
    return lambda: e.run(["т"], 10)


def _отказ_политики(monkeypatch):
    e = _дверь(refused="10.1.2.3: чужой адрес")

    def запуск():
        with pytest.raises(SeamTransportError):
            e.run(["т"], 10)

    return запуск


@pytest.mark.parametrize("имя, случай, подстрока", [
    ("бюджет", _бюджет, "не уложились в 20 с"),
    ("HTTP-код", _http, "HTTP 400"),
    ("не-JSON", _не_json, "ответ не JSON"),
    ("не те векторы", _не_те_векторы, "не по вектору на текст"),
    ("отказ политики", _отказ_политики, "эмбеддинги недоступны"),
])
def test_every_outcome_speaks_once(monkeypatch, capsys, имя, случай, подстрока):
    monkeypatch.setattr(embed_door, "_said", set())
    запуск = случай(monkeypatch)

    запуск()
    assert подстрока in capsys.readouterr().err, имя
    запуск()
    assert capsys.readouterr().err == "", f"{имя}: повтор должен молчать"


def test_a_different_key_is_not_silenced_by_the_first(monkeypatch, capsys):
    """Ключ — код и тело: другой отказ сервера обязан быть слышен (Opus M3)."""
    monkeypatch.setattr(embed_door, "_said", set())
    _дверь(post=Wire(status=400, body="тело A")).run(["т"], 10)
    _дверь(post=Wire(status=400, body="тело B")).run(["т"], 10)
    assert capsys.readouterr().err.count("HTTP 400") == 2


def test_the_same_key_prints_on_repeat_after_a_reset(monkeypatch, capsys):
    monkeypatch.setattr(embed_door, "_said", set())
    _дверь(post=Wire(status=400, body="тело")).run(["т"], 10)
    monkeypatch.setattr(embed_door, "_said", set())
    _дверь(post=Wire(status=400, body="тело")).run(["т"], 10)
    assert capsys.readouterr().err.count("HTTP 400") == 2


@pytest.mark.parametrize("vectors, count, dim", [
    ("нет", 1, None),                     # не список
    ([[]], 1, None),                      # пустой вектор
    ([1.0], 1, None),                     # элемент — не список
    ([[1.0]], 2, None),                   # векторов меньше текстов
    ([["x"]], 1, None),                   # не числа
    ([[1.0], [1.0, 2.0]], 2, None),       # размерность скачет
    ([[1.0]], 1, 3),                      # не та размерность кэша
])
def test_vectors_ok_reports_false_not_a_falsy_value(vectors, count, dim):
    """Контракт возвращает именно `False`: `None` прошёл бы как «нет», и форма
    ответа перестала бы быть наблюдаемой (мутационный сторож)."""
    assert embed_door._vectors_ok(vectors, count, dim) is False


def test_vectors_ok_accepts_a_full_answer():
    assert embed_door._vectors_ok([[1.0, 2.0], [3.0, 4.0]], 2, None) is True


# ── бюджет всего вызова, не пачки ────────────────────────────────────────

def test_timeout_is_the_budget_of_the_whole_call(monkeypatch, capsys):
    """120 с ревизии на 13 пачках были 26 минутами: срок — на весь вызов."""
    monkeypatch.setattr(embed_door, "_said", set())
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])
    сроки = []

    def post(url, payload, timeout):
        сроки.append(timeout)
        часы[0] += 8.0
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    assert _дверь(post=post).run(["т"] * 200, 20) == [], "срок вышел — не вектор на каждый текст"
    assert сроки == [20.0, 12.0, 4.0], сроки
    assert "не уложились в 20 с" in capsys.readouterr().err


def test_the_budget_stops_at_exactly_zero(monkeypatch, capsys):
    """Ровно ноль оставшегося срока — уже бюджет: вторым запросом не идём."""
    monkeypatch.setattr(embed_door, "_said", set())
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])
    сроки = []

    def post(url, payload, timeout):
        сроки.append(timeout)
        часы[0] += 20.0                        # первая пачка съела ровно бюджет
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    assert _дверь(post=post).run(["т"] * 100, 20) == []
    assert сроки == [20.0], сроки
    assert "не уложились в 20 с" in capsys.readouterr().err


def test_say_once_flushes_stderr(monkeypatch):
    """Строка уходит владельцу сразу: на встрече между записью и чтением журнала
    живёт процесс, и невытолкнутый буфер — это молчание."""
    class Поток:
        def __init__(self):
            self.wrote: list[str] = []
            self.flushed = 0

        def write(self, s):
            self.wrote.append(s)

        def flush(self):
            self.flushed += 1

    поток = Поток()
    monkeypatch.setattr(embed_door, "_said", set())
    monkeypatch.setattr(embed_door.sys, "stderr", поток)

    embed_door._say_once("строка")

    assert "".join(поток.wrote).strip() == "строка"
    assert поток.flushed >= 1, "строка осталась в буфере"


# ── транспортные исходы ──────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    ConnectionRefusedError("нет соединения"),
    http.client.RemoteDisconnected("сервер закрыл"),
    urllib.error.URLError("упало"),
])
def test_transport_failures_become_a_seam_transport_error(exc):
    def post(url, payload, timeout):
        raise exc

    with pytest.raises(SeamTransportError) as e:
        _дверь(post=post).run(["т"], 10)
    assert e.value.policy is False


def test_a_seam_refusal_from_the_transport_keeps_its_policy_flag():
    """Свой отказ шва — тоже OSError: кортеж транспорта не перевыпускает его с
    policy=False, и отказ по настройке владельца не становится «сервер не
    ответил» (выходной круг 1, DS C1)."""
    отказ = SeamTransportError("адрес запрещён настройкой", policy=True)

    def post(url, payload, timeout):
        raise отказ

    with pytest.raises(SeamTransportError) as e:
        _дверь(post=post).run(["т"], 10)
    assert e.value is отказ and e.value.policy is True


@pytest.mark.parametrize("raw", ["[1, 2]", '"ok"', "null"])
def test_json_that_is_not_an_object_is_named_not_raised(monkeypatch, capsys, raw):
    """`.get` у списка вылетал бы AttributeError мимо таблицы строк (выходной круг 1,
    DS M2); строка называет форму, а не «не JSON» (круг 2, DS M1)."""
    monkeypatch.setattr(embed_door, "_said", set())
    assert _дверь(post=Wire(raw=raw)).run(["т"], 10) == []
    err = capsys.readouterr().err
    assert "ответ не объект JSON" in err and "ответ не JSON" not in err, err


def test_pytest_fail_from_the_transport_escapes_the_door():
    def post(url, payload, timeout):
        pytest.fail("бум")

    with pytest.raises(pytest.fail.Exception):
        _дверь(post=post).run(["т"], 10)


# ── urllib_post ──────────────────────────────────────────────────────────

class _Ответ:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_urllib_post_reads_the_http_error_body(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(400, b"\xff\xfe<err>")))

    status, text = embed_door.urllib_post("http://127.0.0.1:1/api/embed", {"input": ["т"]}, 5)

    assert status == 400 and isinstance(text, str) and "<err>" in text


def test_http_error_becomes_code_and_body_through_the_door(monkeypatch, capsys):
    monkeypatch.setattr(embed_door, "_said", set())
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(400, b"\xff\xfe<err>")))

    assert _дверь().run(["т"], 5) == []
    said = capsys.readouterr().err
    assert "HTTP 400" in said and "<err>" in said, said


def test_urllib_post_sends_json_and_reads_vectors(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"], seen["timeout"] = request, timeout
        return _Ответ(200, json.dumps({"embeddings": [[1.0, 2.0]]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert _дверь().run(["текст"], 7) == [[1.0, 2.0]]
    request = seen["request"]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8"))["input"] == ["текст"]


def test_the_network_guard_catches_the_default_transport():
    """Транспорт по умолчанию — urllib; сторож сети обязан его видеть."""
    with pytest.raises(pytest.fail.Exception):
        _дверь().run(["т"], 5)


@pytest.mark.сеть_разрешена
def test_a_real_http_server_round_trip():
    """Настоящий сокет на 127.0.0.1: дверь и stdlib-транспорт говорят по HTTP."""
    class Обработчик(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n) or b"{}")
            out = json.dumps({"embeddings": _векторы(data.get("input", []))}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    try:
        srv = http.server.HTTPServer(("127.0.0.1", 0), Обработчик)
    except PermissionError:            # песочница без права слушать сокет
        pytest.skip("loopback недоступен: сокет слушать нечем")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert embed_door.embedder(base, "m").run(["аб"], 5) == [[2.0, 1.0]]
    finally:
        srv.shutdown()
        srv.server_close()


# ── пачка поиска помещается в одну пачку двери ───────────────────────────

def test_a_search_batch_fits_in_one_door_batch():
    assert graph_search.EMBED_BATCH * graph_search.CHUNK_CHARS <= embed_door.EMBED_BATCH_CHARS
    w = Wire()
    _дверь(post=w).run(["к" * graph_search.CHUNK_CHARS] * graph_search.EMBED_BATCH, 30)
    assert [len(b) for b in w.inputs] == [graph_search.EMBED_BATCH]


# ── фабрика: сигнатура, отказ, адрес, имя ────────────────────────────────

def test_the_door_signature_does_not_take_a_config():
    p = inspect.signature(embed_door.embedder).parameters
    assert "cfg" not in p
    assert list(p) == ["base_url", "model", "keep_alive", "post", "refused"]


def test_a_policy_refusal_is_built_not_raised_and_speaks_once(monkeypatch, capsys):
    monkeypatch.setattr(embed_door, "_said", set())

    e = embed_door.embedder("", "m", refused="чужой адрес")
    assert e.refused == "чужой адрес"
    with pytest.raises(SeamTransportError) as ошибка:
        e.run(["т"], 5)
    assert ошибка.value.policy is True

    embed_door.embedder("", "m", refused="чужой адрес")            # вторая сборка
    assert capsys.readouterr().err.count("эмбеддинги недоступны: чужой адрес") == 1


def test_the_exception_carries_the_raw_reason_and_the_line_is_formatted():
    e = embed_door.embedder("", "m", refused="чужой адрес")
    with pytest.raises(SeamTransportError) as ошибка:
        e.run(["т"], 5)
    assert str(ошибка.value) == "чужой адрес", "исключение несёт сырую причину, без приставки"
    assert embed_door.refusal_line("чужой адрес") == "эмбеддинги недоступны: чужой адрес"


@pytest.mark.parametrize("base, model", [
    ("localhost:11434", "m"),                       # нет схемы http(s)
    ("ftp://127.0.0.1:11434", "m"),                 # чужая схема
    ("http://127.0.0.1:11434", ""),                 # имени нет — подписывать нечем
])
def test_a_bad_address_or_empty_model_is_a_value_error(base, model):
    with pytest.raises(ValueError):
        embed_door.embedder(base, model)


def test_the_address_is_stripped_of_the_trailing_slash():
    seen = {}

    def post(url, payload, timeout):
        seen["url"] = url
        return 200, json.dumps({"embeddings": [[1.0]]})

    embed_door.embedder("http://127.0.0.1:11434/", "m", post=post).run(["т"], 5)
    assert seen["url"] == "http://127.0.0.1:11434/api/embed"


def test_keep_alive_none_omits_the_key_and_a_value_travels():
    seen = {}

    def post(url, payload, timeout):
        seen.clear()
        seen.update(payload)
        return 200, json.dumps({"embeddings": [[1.0]]})

    embed_door.embedder("http://x", "m", keep_alive=None, post=post).run(["т"], 5)
    assert "keep_alive" not in seen and seen["model"] == "m"
    embed_door.embedder("http://x", "m", keep_alive="30m", post=post).run(["т"], 5)
    assert seen["keep_alive"] == "30m"
