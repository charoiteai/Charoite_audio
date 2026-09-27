"""Дверь векторов: как спросить /api/embed, не зная, кто отвечает.

Отдельный модуль нижнего слоя. Сюда переехало всё, что раньше жило в
`llm.py` рядом с чат-транспортом: пачки, бюджет всего вызова, разбор ответа и
таблица стока ошибок. Слой моделей (`llm.embedder`) — только адаптер: он
резолвит имя, спрашивает политику и передаёт сюда адрес и способ отправки.
Так векторный путь может собрать и тот, у кого нет `requests` (тесты, доктор),
а сам шов (`model_seam`) остаётся без знания о сервере.

Транспорт — значение, а не импорт: `post(url, payload, timeout)` отдаёт
`(status, text)` на ЛЮБОЙ HTTP-ответ и бросает только транспортные отказы.
По умолчанию — `urllib_post`. Слой моделей подставляет `_requests_post`.
"""

from __future__ import annotations

import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from model_seam import Embedder, SeamTransportError

_said: set[str] = set()


def _say_once(text: str, key: str | None = None) -> None:
    """Сказать владельцу один раз за жизнь процесса.

    Отказ политики повторяется на каждом вопросе; в журнале встречи это был бы
    шум, из-за которого настоящую причину не видно. `key` — чем повтор
    считается тем же: у отказа сервера в тексте есть размер пачки, а повтор —
    это тот же код с тем же телом.
    """
    k = key or text
    if k in _said:
        return
    _said.add(k)
    print(text, file=sys.stderr, flush=True)


#: Потолок одного запроса к /api/embed — по числу текстов и по знакам. Ollama
#: 0.34 с bge-m3 рвёт соединение на большой пачке: 808 ядер (162 677 знаков)
#: — HTTP 400 «tokenize: EOF» за 6,8 с, те же тексты пачками по 100 — 9 из 9
#: за 14,3 с, по 400 — 1 из 3 (замер 23.09, №358). Пачка — забота двери, а не
#: каждого потребителя: ревизия ядер не резала вовсе и молчала месяц. Поиск по
#: графу шлёт по 16 кусков чуть больше 4000 знаков (склейка добирает хвост до
#: +80 и крошку) — потолок знаков с запасом над 16 × 4 500, чтобы его пачка
#: оставалась одной (круг 1 по коду, Opus M2): по 16 таких кусков поиск ходит
#: давно и без отказов.
EMBED_BATCH_TEXTS = 64
EMBED_BATCH_CHARS = 72_000


def batches(texts: list[str]) -> list[list[str]]:
    """Тексты подряд, пачками не больше EMBED_BATCH_TEXTS и EMBED_BATCH_CHARS.

    Текст длиннее потолка знаков идёт отдельной пачкой, а не выпадает: вектор
    на каждый текст — контракт двери.
    """
    пачки: list[list[str]] = []
    пачка: list[str] = []
    знаков = 0
    for text in texts:
        if пачка and (len(пачка) >= EMBED_BATCH_TEXTS
                      or знаков + len(text) > EMBED_BATCH_CHARS):
            пачки.append(пачка)
            пачка, знаков = [], 0
        пачка.append(text)
        знаков += len(text)
    if пачка:
        пачки.append(пачка)
    return пачки


def _vectors_ok(vectors, count: int, dim: int | None) -> bool:
    """По вектору на текст, непустые, одной размерности, числа.

    Длину списка потребитель проверял и раньше; `[[], [1.0]]` проходил и
    давал косинус 0 — пара молча не судилась (входной круг №358, Codex I2).
    """
    if not isinstance(vectors, list) or len(vectors) != count:
        return False
    for v in vectors:
        if not isinstance(v, list) or not v or not isinstance(v[0], (int, float)):
            return False
        if dim is not None and len(v) != dim:
            return False
        dim = len(v)
    return True


def refusal_line(reason: object) -> str:
    """Строка отказа политики: сырая причина с приставкой ровно один раз.

    Исключение несёт причину без приставки — «10.1.2.3: чужой адрес». Строку
    собирает дверь, чтобы потребителю было что напечатать владельцу, а
    повторное заворачивание не давало «эмбеддинги недоступны: эмбеддинги
    недоступны: …».
    """
    return f"эмбеддинги недоступны: {reason}"


#: Что дверь считает транспортным отказом. `requests.RequestException` и
#: `urllib.error.URLError` — подклассы `OSError`; `http.client.HTTPException`
#: ловит `IncompleteRead`/`BadStatusLine`, у которых общего предка с ним нет.
#: Всё пойманное становится `SeamTransportError(policy=False)`.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


def urllib_post(url: str, payload: dict, timeout: float) -> tuple[int, str]:
    """POST на /api/embed через stdlib: `(код, тело)` на любой HTTP-ответ.

    `urlopen` берётся атрибутом модуля, а не локальным именем: сторож сети в
    тестах подменяет `urllib.request.urlopen` и обязан видеть этот ход.
    `HTTPError` — тоже ответ сервера, а не сбой: отдаём её код и тело.

    Схема — только http(s), и здесь тоже, а не только при сборке двери:
    `urlopen` умеет `file://`, а транспорт по умолчанию зовут и напрямую.
    """
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError(f"транспорт векторов: адрес не http(s): {url!r}")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        # nosemgrep — схема проверена строкой выше (только http/https), file:// не проходит
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def embedder(base_url: str, model: str, *, keep_alive: str | None = None,
             post=urllib_post, refused: str = "") -> Embedder:
    """Собрать векторизатор на адрес и модель.

    `refused` непуст — политика уже отказала: адрес не нужен (передают `""`),
    строка отказа печатается один раз при сборке, а `run` бросает свойство
    «нельзя по настройке владельца» (`policy=True`). Иначе адрес обязан быть
    http(s), а имя модели — непустым (это держит `model_seam.Embedder`).

    `post` — транспорт значением: по умолчанию stdlib, слой моделей передаёт
    свой `requests`-адаптер.
    """
    if refused:
        _say_once(refusal_line(refused))

        def run_refused(texts: list[str], timeout: float) -> list[list[float]]:
            raise SeamTransportError(refused, policy=True)

        return Embedder(run_refused, model, refused)

    if urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError(f"адрес эмбеддингов не http(s): {base_url!r}")
    endpoint = base_url.rstrip("/") + "/api/embed"

    def run(texts: list[str], timeout: float) -> list[list[float]]:
        """Векторы через /api/embed. Пустой список — сервер не дал векторов.

        Дверь режет список на пачки сама (`batches`) и отдаёт всё или ничего:
        вектор на каждый текст, иначе `[]` и строка в stderr с кодом и телом
        ответа. `timeout` — срок всего вызова, а не каждой пачки: иначе 120 с
        ревизии на 13 пачках становились 26 минутами, а 20 с дежавю — четырьмя
        (круг 1 по коду, Opus I1/I3).
        """
        payload: dict = {"model": model, "input": []}
        if keep_alive:
            payload["keep_alive"] = keep_alive
        пачки = batches(texts)
        векторы: list[list[float]] = []
        dim: int | None = None
        срок = time.monotonic() + timeout
        for номер, пачка in enumerate(пачки, 1):
            payload["input"] = пачка
            где = f"пачка {номер}/{len(пачки)}, {len(пачка)} текстов"
            осталось = срок - time.monotonic()
            if осталось <= 0:
                _say_once(
                    f"эмбеддинги: не уложились в {timeout:.0f} с на {len(texts)} текстов ({где})",
                    key=f"embed:budget:{len(пачка)}")
                return []
            try:
                status, text = post(endpoint, payload, осталось)
            except SeamTransportError:
                # свой отказ шва — как есть: он тоже OSError, и без этой строки
                # кортеж ниже перевыпустил бы его с policy=False — отказ по
                # настройке владельца стал бы «сервер не ответил» (выходной круг 1, DS C1)
                raise
            except TRANSPORT_ERRORS as exc:       # отказ, таймаут, обрыв
                raise SeamTransportError(str(exc), policy=False) from exc
            if status != 200:
                # Отказ сервера — не молча: код и тело в stderr, один раз на
                # одинаковый ответ. `[]` без строки стоил месяца слепой ночи —
                # ревизия ядер печатала «лежит Ollama» на HTTP 400 (№358). 503
                # на занятом сервере приходит с не-JSON телом — тело текстом.
                тело = (text or "").strip()[:200]
                _say_once(f"эмбеддинги: HTTP {status} ({где}): {тело}",
                          key=f"embed:{status}:{тело[:120]}")
                return []
            try:
                body = json.loads(text)
            except ValueError:
                тело = (text or "").strip()[:120]
                _say_once(f"эмбеддинги: ответ не JSON ({где}): {тело}",
                          key=f"embed:not-json:{тело}")
                return []
            if not isinstance(body, dict):
                # JSON не объектом (`[1,2]`, `"ok"`, `null`): `.get` у списка вылетел
                # бы исключением мимо таблицы строк (выходной круг 1, DS M2). Форма
                # названа своей строкой: «не JSON» отправил бы дежурного искать HTML
                # прокси, а сервер ответил разбираемым документом (круг 2, DS M1)
                тело = (text or "").strip()[:120]
                _say_once(f"эмбеддинги: ответ не объект JSON ({где}, {type(body).__name__}): {тело}",
                          key=f"embed:not-object:{тело}")
                return []
            got = body.get("embeddings", [])
            if not _vectors_ok(got, len(пачка), dim):
                # Ключ — с формой ответа: в демоне, который живёт днями, другой
                # сбой той же природы у другого потребителя не должен молчать
                # (круг 1 по коду, Opus M3)
                форма = (f"{type(got).__name__}:{len(got) if isinstance(got, list) else '-'}"
                         f"/{len(пачка)}")
                _say_once(
                    f"эмбеддинги: сервер дал не по вектору на текст ({где}, ответ {форма})",
                    key=f"embed:bad-vectors:{форма}")
                return []
            dim = len(got[0])
            векторы.extend(got)
        return векторы

    return Embedder(run, model)
