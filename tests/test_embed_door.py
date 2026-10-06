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

import once  # noqa: E402
from charoite_graph import embed_door  # noqa: E402
from charoite_graph import graph_search  # noqa: E402
from charoite_graph.model_seam import SeamTransportError  # noqa: E402


def _embedder(*args, **kwargs):
    """Дверь с реестром процесса — как её собирает приложение (`llm.embedder`): строки и
    их сброс (`once.reset`, `conftest`) общие с реестром процесса (вход 3 PR 1 №323,
    критика 2). Дверь без реестра судит отдельный тест."""
    kwargs.setdefault("notices", once)
    return embed_door.embedder(*args, **kwargs)


def _векторы(texts: list[str]) -> list[list[float]]:
    return [[float(len(t)), 1.0] for t in texts]


class Wire:
    """Транспорт-кортеж: помнит пачки и тело, отвечает заготовкой.

    `script` — очередь ходов: элемент либо исключение (бросается), либо
    `payload -> (код, тело)`. Нужна там, где ответ зависит от номера запроса
    (400 на первом ходе пачки, 200 на повторе); без неё отвечает заготовкой.
    """

    def __init__(self, vectors=None, status: int = 200, body: str | None = None,
                 raw: str | None = None, fail_on: int | None = None,
                 script: list | None = None):
        self.inputs: list[list[str]] = []
        self.payloads: list[dict] = []
        self.timeouts: list[float] = []
        self.vectors, self.status, self.body, self.raw, self.fail_on = (
            vectors, status, body, raw, fail_on)
        self.script = script

    def __call__(self, url, payload, timeout):
        self.inputs.append(list(payload["input"]))
        self.payloads.append(dict(payload))
        self.timeouts.append(timeout)
        if self.script is not None:
            шаг = self.script.pop(0)
            if isinstance(шаг, BaseException):
                raise шаг
            return шаг(payload)
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
    return _embedder("http://127.0.0.1:11434", "m", **kwargs)


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
    """Вторая пачка отказала и на повторе с усечением — весь вызов пуст."""
    def ок(payload):
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    def нет(payload):
        return 400, 'Post "tokenize": EOF'

    w = Wire(script=[ок, нет, нет])

    assert _дверь(post=w).run(["текст"] * 100, 30) == [], "частичный ответ — не вектор на каждый текст"
    assert [len(b) for b in w.inputs] == [64, 36, 36], w.inputs


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
    once.reset("embed")
    запуск = случай(monkeypatch)

    запуск()
    assert подстрока in capsys.readouterr().err, имя
    запуск()
    assert capsys.readouterr().err == "", f"{имя}: повтор должен молчать"


@pytest.mark.parametrize("raw, строка", [
    ("<" + "x" * 300, "ответ не JSON"),
    ('"' + "x" * 300 + '"', "ответ не объект JSON"),
])
def test_the_line_quotes_the_body_up_to_120_chars(monkeypatch, capsys, raw, строка):
    """Строка отказа цитирует тело — по нему дежурный отличает HTML прокси от
    обрыва, — но не больше 120 знаков (выживший мутант `[:120] → [:0]`)."""
    once.reset("embed")
    assert _дверь(post=Wire(raw=raw)).run(["т"], 10) == []
    err = capsys.readouterr().err
    # первый знак тела — «<» или «"», дальше ровно 119 «x»: сдвиг потолка на единицу виден
    assert строка in err and "x" * 119 in err and "x" * 120 not in err, err


def test_a_different_key_is_not_silenced_by_the_first(monkeypatch, capsys):
    """Ключ — код и тело: другой отказ сервера обязан быть слышен (Opus M3)."""
    once.reset("embed")
    _дверь(post=Wire(status=400, body="тело A")).run(["т"], 10)
    _дверь(post=Wire(status=400, body="тело B")).run(["т"], 10)
    assert capsys.readouterr().err.count("HTTP 400") == 2


def test_the_same_key_prints_on_repeat_after_a_reset(monkeypatch, capsys):
    once.reset("embed")
    _дверь(post=Wire(status=400, body="тело")).run(["т"], 10)
    once.reset("embed")
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
    ([[1.0, float("inf")]], 1, None),     # не конечная координата после первой
    ([[1.0, 10 ** 400]], 1, None),        # переполнение преобразования
])
def test_vectors_ok_reports_false_not_a_falsy_value(vectors, count, dim):
    """Контракт возвращает именно `False`: `None` прошёл бы как «нет», и форма
    ответа перестала бы быть наблюдаемой (мутационный сторож)."""
    assert embed_door._vectors_ok(vectors, count, dim) is False


def test_vectors_ok_accepts_a_full_answer():
    assert embed_door._vectors_ok([[1.0, 2.0], [3.0, 4.0]], 2, None) is True


@pytest.mark.parametrize("bad", ["не число", None, True, [], {}, float("nan"),
                                 float("inf"), float("-inf"), 10 ** 400])
def test_embedding_transport_rejects_invalid_components_after_the_first(bad, capsys):
    """Число в первой координате не делает остальные координаты пригодными."""
    wire = Wire(vectors=lambda inp, n: [[1.0, bad] for _ in inp])
    answer = _дверь(post=wire).run(["проверка памяти"], timeout=5)
    assert answer == []
    assert "не по вектору на текст" in capsys.readouterr().err


# ── усечение: `truncate: false`, повтор с `truncate: true` ───────────────

def _отказ(тело: str, код: int = 400):
    """Ход транспорта: ответ `(код, тело)` независимо от тела запроса."""
    def ответ(payload):
        return код, тело
    return ответ


def _ок(payload):
    """Ход транспорта: 200 и вектор на каждый текст пачки."""
    return 200, json.dumps({"embeddings": _векторы(payload["input"])})


def _усечение_сервер(тело: str = "the input length exceeds the context length"):
    """Транспорт: без усечения — 400 с телом, с усечением — вектор на текст.

    Так ведёт себя Ollama: с `truncate: false` она отвечает 400 на длинный
    вход, а с `truncate: true` режет его и отдаёт векторы (замер 27.09).
    """
    def post(url, payload, timeout):
        if payload["truncate"] is False:
            return 400, тело
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})
    return post


def test_the_truncate_field_is_false_first_and_true_only_on_the_retry():
    """Поле не протекает: первая пачка и следующая — снова `false`."""
    w = Wire(script=[_отказ("длинно"), _ок, _отказ("длинно"), _ок])
    _дверь(post=w).run(["а" * 40_000, "б" * 40_000], 30)
    assert [p["truncate"] for p in w.payloads] == [False, True, False, True], w.payloads
    assert all(p["model"] == "m" for p in w.payloads)


def test_a_length_refusal_retried_with_truncate_returns_vectors_and_speaks_once(capsys):
    """Один эпизод на адрес и модель: вторая встреча того же длинного входа молчит."""
    once.reset("embed")
    e = _дверь(post=_усечение_сервер("The input length EXCEEDS THE CONTEXT LENGTH"))
    texts = ["к" * 10, "д" * 50]

    vecs = e.run(texts, 10)
    assert len(vecs) == 2, "векторы повтора обязаны вернуться"
    err = capsys.readouterr().err
    assert "вход длиннее предела сервера" in err
    assert "№2, 50 знаков" in err, "самый длинный текст пачки — №2"

    e.run(texts, 10)
    assert capsys.readouterr().err == "", "эпизод уже сказан"


def test_the_truncation_episode_is_known_per_address_and_model(capsys):
    """Ключ эпизода — адрес и модель: другая дверь скажется своим голосом."""
    once.reset("embed")
    _embedder("http://127.0.0.1:1", "m1", post=_усечение_сервер()).run(["т"], 10)
    _embedder("http://127.0.0.1:2", "m2", post=_усечение_сервер()).run(["т"], 10)
    assert capsys.readouterr().err.count("вход длиннее предела сервера") == 2


def test_a_plain_400_retried_with_truncate_speaks_neutrally(capsys):
    """Тело не назвало длину: строка не выводит причину ни в одну сторону, но
    говорит, что векторы построены с усечением, и называет самый длинный текст
    (выходной круг 1 по №433, DS I4)."""
    once.reset("embed")
    e = _дверь(post=_усечение_сервер("bad request: the model choked"))

    assert e.run(["к", "длинный"], 10) == _векторы(["к", "длинный"])
    err = capsys.readouterr().err
    assert "сервер отказал на запрос без усечения" in err
    assert "повтор с усечением дал векторы" in err
    assert "если отказ был о длине, хвост текста в вектор не попал" in err
    assert "№2, 7 знаков" in err, err
    assert "bad request: the model choked" in err
    assert "вход длиннее предела" not in err


@pytest.mark.parametrize("тело", [
    "the input length exceeds the context length",           # Ollama 0.34, замер 27.09
    "input length exceeds maximum context length",           # сборки 2024 — начала 2025
    '{"error":"The Input Length Exceeds The Context Length"}',
])
def test_both_wordings_of_the_length_refusal_are_recognised(capsys, тело):
    """Одна фраза целиком ловила только новую формулировку (DS I4)."""
    once.reset("embed")
    assert _дверь(post=_усечение_сервер(тело)).run(["т"], 10) == _векторы(["т"])
    assert "вход длиннее предела сервера" in capsys.readouterr().err


@pytest.mark.parametrize("тело", [
    "input length is fine, context length is fine",           # оба слова, но не отказ
    "the input length exceeds the batch size",               # длина, но не контекст
])
def test_a_body_with_only_part_of_the_mark_is_not_a_length_refusal(capsys, тело):
    """Признак — все куски: «context length» без «input length exceeds» не длина."""
    once.reset("embed")
    _дверь(post=_усечение_сервер(тело)).run(["т"], 10)
    err = capsys.readouterr().err
    assert "вход длиннее предела сервера" not in err and "если отказ был о длине" in err, err


def test_a_second_400_returns_nothing_and_quotes_both_bodies(capsys):
    """Повтор тоже отказал: строка — по второму ответу, в ней же тело первого."""
    once.reset("embed")
    w = Wire(script=[_отказ("первое тело"), _отказ("второе тело")])

    assert _дверь(post=w).run(["т"], 10) == []
    err = capsys.readouterr().err
    assert "HTTP 400" in err and "второе тело" in err and "первое тело" in err, err


def test_a_transport_break_on_the_retry_is_judged_by_the_first_answer(capsys):
    """Сервер уже ответил 400: «недоступен» было бы неправдой — не исключение, `[]`."""
    once.reset("embed")
    w = Wire(script=[_отказ("первый ответ"), ConnectionResetError("обрыв связи")])

    assert _дверь(post=w).run(["т"], 10) == []
    err = capsys.readouterr().err
    assert "HTTP 400" in err and "первый ответ" in err, err
    assert "обрыв связи" not in err, "транспорт не пересказывается как причина"
    assert "повтор с усечением: повтор не дошёл" in err, "почему повтора нет — названо (DS M7)"


def test_no_retry_when_the_deadline_expired_after_the_first_answer(monkeypatch, capsys):
    """Срок вышел на первом ответе — повтора нет, отказ по первому, путь как сегодня."""
    once.reset("embed")
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])
    вызовы = []

    def post(url, payload, timeout):
        вызовы.append(dict(payload))
        часы[0] += 10.0                     # ровно весь срок
        return 400, "отказ без усечения"

    assert _дверь(post=post).run(["т"], 10) == []
    assert len(вызовы) == 1, "на исходе срока в сеть второй раз не идём"
    assert [p["truncate"] for p in вызовы] == [False]
    err = capsys.readouterr().err
    assert "HTTP 400" in err and "повтор с усечением: срок вышел" in err, err


def test_no_retry_reasons_do_not_silence_each_other(monkeypatch, capsys):
    """«Срок вышел» и «повтор не дошёл» — два исхода и два ключа: второй не
    глохнет от первого при одном и том же теле ответа (DS M7)."""
    once.reset("embed")
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])

    def post_срок(url, payload, timeout):
        часы[0] += 10.0
        return 400, "отказ"

    _дверь(post=post_срок).run(["т"], 10)
    assert "срок вышел" in capsys.readouterr().err
    _дверь(post=Wire(script=[_отказ("отказ"), ConnectionResetError("обрыв")])).run(["т"], 10)
    assert "повтор не дошёл" in capsys.readouterr().err


def test_a_server_that_does_not_know_truncate_stays_quiet(capsys):
    """Сервер игнорирует поле и отвечает 200 — векторы есть, строк нет.

    Граница двери, а не обещание: 200 такого сервера неотличим от честного,
    и докстрока `run` говорит об этом прямо (выходной круг 1 по №433, DS I3)."""
    once.reset("embed")
    w = Wire()                              # всегда 200 с векторами

    assert len(_дверь(post=w).run(["т", "т2"], 10)) == 2
    assert capsys.readouterr().err == ""
    assert [p["truncate"] for p in w.payloads] == [False]


def test_a_call_without_truncation_forgets_the_episode(capsys):
    """Вызов без усечения снимает ключ: следующий длинный вход скажется снова."""
    once.reset("embed")
    режим = {"усечён": True}

    def post(url, payload, timeout):
        if payload["truncate"] is False and режим["усечён"]:
            return 400, "the input length exceeds the context length"
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    e = _дверь(post=post)
    e.run(["длинный вход"], 10)
    assert "вход длиннее предела сервера" in capsys.readouterr().err

    режим["усечён"] = False
    e.run(["короткий"], 10)                 # принято без усечения → forget
    assert capsys.readouterr().err == ""

    режим["усечён"] = True
    e.run(["длинный вход"], 10)             # новый эпизод говорит снова
    assert "вход длиннее предела сервера" in capsys.readouterr().err


def _эпизод_сервер(часы=None):
    """Транспорт по первой букве первого текста пачки: «д» — длинный вход (400 без
    усечения, векторы с ним), «р» — отказ на обоих ходах, «с» — съедает весь срок,
    «о» — обрыв транспорта, иначе — 200 с векторами."""
    def post(url, payload, timeout):
        буква = payload["input"][0][:1]
        if буква == "д":
            if payload["truncate"] is False:
                return 400, "the input length exceeds the context length"
            return 200, json.dumps({"embeddings": _векторы(payload["input"])})
        if буква == "р":
            return 400, "tokenize: EOF"
        if буква == "с":
            часы[0] += 1000.0
            return 200, json.dumps({"embeddings": _векторы(payload["input"])})
        if буква == "о":
            raise ConnectionResetError("обрыв")
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})
    return post


@pytest.mark.parametrize("выход, тексты", [
    ("бюджет", ["с" * 40_000, "к" * 40_000]),        # пачка приняла, срок вышел до следующей
    ("отказ", ["к" * 40_000, "р" * 40_000]),         # пачка приняла, следующая — 400 на обоих ходах
    ("исключение", ["к" * 40_000, "о" * 40_000]),    # пачка приняла, следующая — обрыв транспорта
])
def test_every_exit_of_a_call_without_truncation_ends_the_episode(monkeypatch, capsys, выход, тексты):
    """Эпизод снимает любой выход вызова без усечения, а не только успех: иначе
    вызов «пачка принята — следующая отказала» оставлял ключ до конца процесса,
    и дверь снова резала вход молча (выходной круг 2 по №433, DS C1)."""
    once.reset("embed")
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])
    e = _дверь(post=_эпизод_сервер(часы))

    e.run(["д" * 40_000], 30)
    assert "вход длиннее предела сервера" in capsys.readouterr().err

    if выход == "исключение":
        with pytest.raises(SeamTransportError):
            e.run(тексты, 30)
    else:
        assert e.run(тексты, 30) == [], выход
    capsys.readouterr()

    e.run(["д" * 40_000], 30)
    assert "вход длиннее предела сервера" in capsys.readouterr().err, f"{выход}: эпизод не снят"


@pytest.mark.parametrize("выход, тексты", [
    ("бюджет", ["д" * 40_000, "с" * 40_000, "к" * 40_000]),   # усекли, срок вышел до третьей пачки
    ("отказ", ["д" * 40_000, "р" * 40_000]),                  # усекли, следующая — 400 на обоих ходах
    ("исключение", ["д" * 40_000, "о" * 40_000]),             # усекли, следующая — обрыв транспорта
    ("успех", ["д" * 40_000, "к" * 40_000]),                  # усекли, дальше всё приняли
])
def test_a_call_that_truncated_keeps_the_episode_on_every_exit(monkeypatch, capsys, выход, тексты):
    """Обратная сторона той же таблицы: вызов, получивший векторы повтора с
    усечением, эпизод не снимает ни на каком выходе. Проверка — СЛЕДУЮЩИМ длинным
    вызовом: вывод того же вызова молчит и при снятом эпизоде, потому что ключ ещё
    жив от первого (выходной круг 3 по №433, DS I1)."""
    once.reset("embed")
    часы = [1000.0]
    monkeypatch.setattr(embed_door.time, "monotonic", lambda: часы[0])
    e = _дверь(post=_эпизод_сервер(часы))
    e.run(["д" * 40_000], 30)
    assert "вход длиннее предела сервера" in capsys.readouterr().err

    if выход == "исключение":
        with pytest.raises(SeamTransportError):
            e.run(тексты, 30)
    else:
        e.run(тексты, 30)
    capsys.readouterr()

    e.run(["д" * 40_000], 30)
    assert capsys.readouterr().err == "", f"{выход}: вызов, который усекал, эпизод снял"


def test_alternating_long_and_short_batches_speak_once_per_call(capsys):
    """Длинная, короткая, длинная пачка в одном вызове — одна строка: эпизод
    снимает только вызов без усечения, а не каждая принятая пачка (DS I2)."""
    once.reset("embed")

    def post(url, payload, timeout):
        if payload["truncate"] is False and payload["input"][0].startswith("д"):
            return 400, "the input length exceeds the context length"
        return 200, json.dumps({"embeddings": _векторы(payload["input"])})

    тексты = ["д" * 40_000, "к" * 40_000, "д" * 40_000]
    assert len(embed_door.batches(тексты)) == 3, "три пачки по одному тексту"
    e = _дверь(post=post)
    assert len(e.run(тексты, 30)) == 3
    assert capsys.readouterr().err.count("вход длиннее предела сервера") == 1

    e.run(тексты, 30)                        # тот же эпизод: вызов снова усекал
    assert capsys.readouterr().err == ""


# ── бюджет всего вызова, не пачки ────────────────────────────────────────

def test_timeout_is_the_budget_of_the_whole_call(monkeypatch, capsys):
    """120 с ревизии на 13 пачках были 26 минутами: срок — на весь вызов."""
    once.reset("embed")
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
    once.reset("embed")
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


# ── транспортные исходы ──────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    ConnectionRefusedError("нет соединения"),
    http.client.RemoteDisconnected("сервер закрыл"),
    urllib.error.URLError("упало"),
    http.client.IncompleteRead(b"{\"embeddings\": [["),   # не OSError: держит тип в TRANSPORT_ERRORS
    http.client.BadStatusLine("мусор"),
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
    once.reset("embed")
    assert _дверь(post=Wire(raw=raw)).run(["т"], 10) == []
    err = capsys.readouterr().err
    assert "ответ не объект JSON" in err and "ответ не JSON" not in err, err


def test_pytest_fail_from_the_transport_escapes_the_door():
    def post(url, payload, timeout):
        pytest.fail("бум")

    with pytest.raises(pytest.fail.Exception):
        _дверь(post=post).run(["т"], 10)


def test_a_seam_refusal_on_the_retry_keeps_its_policy_flag():
    """Отказ шва на повторе с усечением летит как есть, а не глохнет в «повтор
    не дошёл» с отказом по первому ответу: таблица исключений одна на оба хода
    (выходной круг 1 по №433, DS C1)."""
    отказ = SeamTransportError("адрес запрещён настройкой", policy=True)
    w = Wire(script=[_отказ("the input length exceeds the context length"), отказ])

    with pytest.raises(SeamTransportError) as e:
        _дверь(post=w).run(["т"], 10)
    assert e.value is отказ and e.value.policy is True
    assert [p["truncate"] for p in w.payloads] == [False, True]


@pytest.mark.parametrize("ошибка", [TypeError("проводка"), pytest.fail.Exception("бум")])
def test_a_wiring_error_on_the_retry_escapes_the_door(ошибка):
    """Ошибка проводки на повторе не становится «повтор не дошёл» и `[]`:
    перехват повтора не шире транспортного (DS M6)."""
    w = Wire(script=[_отказ("отказ"), ошибка])

    with pytest.raises(type(ошибка)):
        _дверь(post=w).run(["т"], 10)


def test_every_quote_of_a_body_stops_at_the_one_ceiling(capsys):
    """Код ответа, отказ повтора и строка усечения цитируют тело одним потолком
    BODY_EXCERPT — раньше их было четыре, 120 и 200 (DS M5)."""
    длинное = "<" + "x" * 300
    случаи = [
        Wire(status=503, body=длинное),                               # HTTP-код
        Wire(script=[_отказ("а" + "y" * 300), _отказ(длинное)]),      # повтор отказал
        _усечение_сервер(длинное),                                    # запасная строка усечения
    ]
    for транспорт in случаи:
        once.reset("embed")
        _дверь(post=транспорт).run(["т"], 10)
        err = capsys.readouterr().err
        assert "x" * (embed_door.BODY_EXCERPT - 1) in err, err
        assert "x" * embed_door.BODY_EXCERPT not in err, err
        assert "y" * embed_door.BODY_EXCERPT not in err, err


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


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://h/x", "localhost:11434/api/embed"])
def test_urllib_post_refuses_a_non_http_address_before_opening(monkeypatch, url):
    """`urlopen` умеет `file://`: транспорт по умолчанию отказывает сам, не открывая
    ничего (находка semgrep dynamic-urllib-use в CI по #646)."""
    def не_звать(*a, **k):
        pytest.fail("urlopen не должен вызываться для не-http адреса")

    monkeypatch.setattr(embed_door.urllib.request, "urlopen", не_звать)
    with pytest.raises(ValueError):
        embed_door.urllib_post(url, {"input": []}, 1)


def test_urllib_post_reads_the_http_error_body(monkeypatch):
    monkeypatch.setattr(embed_door, "open_url",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(400, b"\xff\xfe<err>")))

    status, text = embed_door.urllib_post("http://127.0.0.1:1/api/embed", {"input": ["т"]}, 5)

    assert status == 400 and isinstance(text, str) and "<err>" in text


def test_http_error_becomes_code_and_body_through_the_door(monkeypatch, capsys):
    once.reset("embed")
    monkeypatch.setattr(embed_door, "open_url",
                        lambda *a, **k: (_ for _ in ()).throw(http_error(400, b"\xff\xfe<err>")))

    assert _дверь().run(["т"], 5) == []
    said = capsys.readouterr().err
    assert "HTTP 400" in said and "<err>" in said, said


def test_urllib_post_sends_json_and_reads_vectors(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"], seen["timeout"] = request, timeout
        return _Ответ(200, json.dumps({"embeddings": [[1.0, 2.0]]}).encode("utf-8"))

    monkeypatch.setattr(embed_door, "open_url", fake_urlopen)

    assert _дверь().run(["текст"], 7) == [[1.0, 2.0]]
    request = seen["request"]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8"))["input"] == ["текст"]


def test_urllib_post_sends_over_https(monkeypatch):
    """https — единственная законная схема удалённого адреса (privacy запрещает http вне
    своей сети): отказ по схеме её не задевает (выходной круг 5, DS M3)."""
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return _Ответ(200, b'{"embeddings": [[1.0]]}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert embed_door.urllib_post("https://h/api/embed", {"input": ["т"]}, 1) == (200, '{"embeddings": [[1.0]]}')
    assert seen["url"] == "https://h/api/embed"


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
        assert _embedder(base, "m").run(["аб"], 5) == [[2.0, 1.0]]
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
    """Дверь не знает конфига: адрес и имя — позиционно, остальное — только по имени,
    реестр строк — значением (вход 4 PR 1 №323, M1: пин сторожит смысл, а не порядок)."""
    p = inspect.signature(embed_door.embedder).parameters
    assert "cfg" not in p
    assert list(p)[:2] == ["base_url", "model"]
    assert all(v.kind is inspect.Parameter.KEYWORD_ONLY for k, v in p.items() if k not in ("base_url", "model"))
    assert p["notices"].default is None


def test_doors_without_a_registry_do_not_share_one(capsys):
    """Без реестра у каждой собранной двери свой `Notices()`: глобала в пакете нет, и
    отказ второй двери звучит снова. Умолчание раскрывается до ветки отказа — отказу
    реестр нужен тоже (вход 4 PR 1 №323, M2)."""
    for _ in range(2):
        e = embed_door.embedder("", "m", refused="чужой адрес")
        assert e.refused == "чужой адрес"
    assert capsys.readouterr().err.count("эмбеддинги недоступны: чужой адрес") == 2
    once.reset("embed")
    for _ in range(2):
        _embedder("", "m", refused="чужой адрес")
    assert capsys.readouterr().err.count("эмбеддинги недоступны: чужой адрес") == 1, "реестр процесса — один на все"


def test_a_policy_refusal_is_built_not_raised_and_speaks_once(monkeypatch, capsys):
    once.reset("embed")

    e = _embedder("", "m", refused="чужой адрес")
    assert e.refused == "чужой адрес"
    with pytest.raises(SeamTransportError) as ошибка:
        e.run(["т"], 5)
    assert ошибка.value.policy is True

    _embedder("", "m", refused="чужой адрес")            # вторая сборка
    assert capsys.readouterr().err.count("эмбеддинги недоступны: чужой адрес") == 1


def test_the_exception_carries_the_raw_reason_and_the_line_is_formatted():
    e = _embedder("", "m", refused="чужой адрес")
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
        _embedder(base, model)


def test_the_address_is_stripped_of_the_trailing_slash():
    seen = {}

    def post(url, payload, timeout):
        seen["url"] = url
        return 200, json.dumps({"embeddings": [[1.0]]})

    _embedder("http://127.0.0.1:11434/", "m", post=post).run(["т"], 5)
    assert seen["url"] == "http://127.0.0.1:11434/api/embed"


def test_keep_alive_none_omits_the_key_and_a_value_travels():
    seen = {}

    def post(url, payload, timeout):
        seen.clear()
        seen.update(payload)
        return 200, json.dumps({"embeddings": [[1.0]]})

    _embedder("http://x", "m", keep_alive=None, post=post).run(["т"], 5)
    assert "keep_alive" not in seen and seen["model"] == "m"
    _embedder("http://x", "m", keep_alive="30m", post=post).run(["т"], 5)
    assert seen["keep_alive"] == "30m"
