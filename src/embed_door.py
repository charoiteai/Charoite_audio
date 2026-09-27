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

Строки об отказах говорит общий реестр `once` (ключ — код и тело ответа).
Своего `forget` у этих строк нет намеренно: сервер, который мигает (ответил —
отказал — ответил), с ним печатал бы строку на каждом мигании. Исключение —
эпизод усечения, и он один: запрос всегда несёт `truncate: false`, чтобы
сервер не резал длинный вход молча (замер 27.09: bge-m3 при 2102 токенах
ответил 200, а `prompt_eval_count` показал 2048 — хвост в вектор не попал).
На HTTP 400 дверь повторяет ту же пачку с `truncate: true`; повтор дал
векторы — дверь говорит один раз на адрес и модель, а пачка, принятая без
усечения, зовёт `forget` этого ключа, и следующий длинный вход скажется
снова. У остальных строк отказа `forget` нет: иначе мигающий сервер печатал
бы строку на каждом мигании.
"""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import once
from charoite_graph.model_seam import Embedder, SeamTransportError


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


def _longest(texts: list[str]) -> tuple[int, int]:
    """Номер (с единицы) и длина самого длинного текста; при равенстве — первого."""
    номер = max(range(len(texts)), key=lambda i: len(texts[i]))
    return номер + 1, len(texts[номер])


def _parse_response(status: int, text: str, count: int, dim: int | None,
                    where: str) -> tuple[list | None, str | None, str | None]:
    """Ответ сервера → `(векторы, None, None)` или `(None, смысл, строка)`.

    Смысл — то, что делает повтор повтором (код и тело, форма ответа), без
    приставки пространства: ключ `once` собирает зовущий. Строку он же печатает
    один раз. Разбор один для первого хода и для повтора: у отказа на повторе
    та же таблица, отличается только приписка про первый ответ.
    """
    тело = (text or "").strip()
    if status != 200:
        краткое = тело[:200]
        return (None, f"{status}:{тело[:120]}",
                f"эмбеддинги: HTTP {status} ({where}): {краткое}")
    try:
        body = json.loads(text)
    except ValueError:
        краткое = тело[:120]
        return (None, f"not-json:{краткое}",
                f"эмбеддинги: ответ не JSON ({where}): {краткое}")
    if not isinstance(body, dict):
        # JSON не объектом (`[1,2]`, `"ok"`, `null`): `.get` у списка вылетел бы
        # исключением мимо таблицы строк. Форма названа своей строкой.
        краткое = тело[:120]
        return (None, f"not-object:{краткое}",
                f"эмбеддинги: ответ не объект JSON ({where}, {type(body).__name__}): {краткое}")
    got = body.get("embeddings", [])
    if not _vectors_ok(got, count, dim):
        форма = (f"{type(got).__name__}:{len(got) if isinstance(got, list) else '-'}"
                 f"/{count}")
        return (None, f"bad-vectors:{форма}",
                f"эмбеддинги: сервер дал не по вектору на текст ({where}, ответ {форма})")
    return got, None, None


#: Признак отказа сервера из-за длины входа. Ollama 0.34 с `truncate: false`
#: так отвечает, когда вход длиннее контекста (замер 27.09, bge-m3). Сравнение
#: без учёта регистра: сборки называют причину по-разному.
CONTEXT_LENGTH_MARK = "exceeds the context length"


def _truncate_line(first_body: str, texts: list[str], where: str) -> str:
    """Строка эпизода усечения: повтор с `truncate: true` дал векторы.

    `first_body` — тело первого отказа (HTTP 400 на запрос без усечения).
    Текст входа в строку не попадает: только номер и число знаков самого
    длинного текста пачки. Вторая ветка — сервер отказывал не из-за длины.
    """
    if CONTEXT_LENGTH_MARK in first_body.lower():
        номер, знаков = _longest(texts)
        return (f"эмбеддинги: вход длиннее предела сервера ({where}) — "
                f"сервер усёк его, хвост текста в вектор не попал; "
                f"самый длинный текст пачки — №{номер}, {знаков} знаков")
    return (f"эмбеддинги: сервер отказал без усечения "
            f"(HTTP 400 ({where}): {first_body[:200]}), повтор с усечением прошёл")


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
        строка = refusal_line(refused)
        once.say(("embed", строка), строка)

        def run_refused(texts: list[str], timeout: float) -> list[list[float]]:
            raise SeamTransportError(refused, policy=True)

        return Embedder(run_refused, model, refused)

    if urlsplit(base_url).scheme not in {"http", "https"}:
        raise ValueError(f"адрес эмбеддингов не http(s): {base_url!r}")
    endpoint = base_url.rstrip("/") + "/api/embed"
    #: Ключ эпизода усечения — адрес и модель, а не пачка: эпизод один на
    #: дверь, и `forget` снимает его целиком.
    ключ_усечения = ("embed", ("truncate", endpoint, model))

    def тело(пачка: list[str], truncate: bool) -> dict:
        """Тело одного запроса — новое на каждый ход.

        Один словарь на вызов протёк бы: `truncate: true` повтора остался бы в
        следующей пачке, и она молча отдала бы усечённый вход.
        """
        запрос = {"model": model, "input": пачка, "truncate": truncate}
        if keep_alive:
            запрос["keep_alive"] = keep_alive
        return запрос

    def run(texts: list[str], timeout: float) -> list[list[float]]:
        """Векторы через /api/embed. Пустой список — сервер не дал векторов.

        Дверь режет список на пачки сама (`batches`) и отдаёт всё или ничего:
        вектор на каждый текст, иначе `[]` и строка в stderr с кодом и телом
        ответа. `timeout` — срок всего вызова, а не каждой пачки: иначе 120 с
        ревизии на 13 пачках становились 26 минутами, а 20 с дежавю — четырьмя
        (круг 1 по коду, Opus I1/I3).

        Первый ход пачки всегда несёт `truncate: false`: сервер обязан сказать
        400, а не молча усечь длинный вход. На 400 — повтор той же пачки с
        `truncate: true`, если срок ещё остался; повтор дал векторы — они
        возвращаются. Пачка, принятая без усечения, зовёт `once.forget` ключа
        усечения: эпизод кончился, следующий длинный вход скажется снова.
        """
        пачки = batches(texts)
        векторы: list[list[float]] = []
        dim: int | None = None
        срок = time.monotonic() + timeout
        for номер, пачка in enumerate(пачки, 1):
            где = f"пачка {номер}/{len(пачки)}, {len(пачка)} текстов"
            осталось = срок - time.monotonic()
            if осталось <= 0:
                once.say(
                    ("embed", f"budget:{len(пачка)}"),
                    f"эмбеддинги: не уложились в {timeout:.0f} с на {len(texts)} текстов ({где})")
                return []
            try:
                status, text = post(endpoint, тело(пачка, False), осталось)
            except SeamTransportError:
                # свой отказ шва — как есть: он тоже OSError, и без этой строки
                # кортеж ниже перевыпустил бы его с policy=False — отказ по
                # настройке владельца стал бы «сервер не ответил» (выходной круг 1, DS C1)
                raise
            except TRANSPORT_ERRORS as exc:       # отказ, таймаут, обрыв
                raise SeamTransportError(str(exc), policy=False) from exc

            if status == 400:
                # Сервер отказал — возможно, из-за длины входа. Повторяем ту же
                # пачку с усечением, пока цел срок. Срок вышел или повтор
                # оборвался транспортом — пути «как сегодня»: отказ по первому
                # ответу, не исключение (сервер уже ответил).
                первое = (status, text)
                остаток = срок - time.monotonic()
                повтор = None
                if остаток > 0:
                    try:
                        повтор = post(endpoint, тело(пачка, True), остаток)
                    except TRANSPORT_ERRORS:
                        повтор = None
                status, text = первое if повтор is None else повтор
                got, смысл, строка = _parse_response(status, text, len(пачка), dim, где)
                if got is None:
                    if повтор is not None:
                        # Строка — по второму ответу, в ней же тело первого:
                        # дежурный видит, что и повтор с усечением не прошёл.
                        строка = (f"{строка}; повтор с усечением не прошёл "
                                  f"(первый ответ: {первое[1].strip()[:200]})")
                        once.say(("embed", f"truncate-fail:{смысл}"), строка)
                    else:
                        once.say(("embed", смысл), строка)
                    return []
                if повтор is not None:
                    once.say(ключ_усечения, _truncate_line(первое[1], пачка, где))
                dim = len(got[0])
                векторы.extend(got)
                continue

            got, смысл, строка = _parse_response(status, text, len(пачка), dim, где)
            if got is None:
                # Отказ сервера — не молча: код и тело в stderr, один раз на
                # одинаковый ответ. `[]` без строки стоил месяца слепой ночи —
                # ревизия ядер печатала «лежит Ollama» на HTTP 400 (№358). 503
                # на занятом сервере приходит с не-JSON телом — тело текстом.
                once.say(("embed", смысл), строка)
                return []
            # Пачка принята без усечения — эпизод кончился: следующий длинный
            # вход сервер назовёт снова, а не промолчит навсегда.
            once.forget(ключ_усечения)
            dim = len(got[0])
            векторы.extend(got)
        return векторы

    return Embedder(run, model)
