"""Дверь векторов: как спросить /api/embed, не зная, кто отвечает.

Модуль пакета графа и второй его вход (№323 PR 1, №462): пару «векторизатор и
имя» пользователь пакета собирает здесь по адресу модели, без приложения. Сюда
же переехало всё, что раньше жило в `llm.py` рядом с чат-транспортом: пачки,
бюджет всего вызова, разбор ответа и таблица стока ошибок. Слой моделей
приложения (`llm.embedder`) — только адаптер: он резолвит имя, спрашивает
политику адреса и передаёт сюда адрес, способ отправки и свой реестр строк.
У самой двери (`embedder`) политики адреса нет — адрес задаёт вызывающий;
пользователю пакета векторизатор собирает фабрика `ollama_embedder`: адрес
проходит `address_policy.guard_model_url` (№522). Шов (`model_seam`) остаётся
без знания о сервере.

Транспорт — значение, а не импорт: `post(url, payload, timeout)` отдаёт
`(status, text)` на ЛЮБОЙ HTTP-ответ и бросает только транспортные отказы.
По умолчанию — `urllib_post`. Слой моделей подставляет `_requests_post`.

Строки об отказах говорит реестр «сказать один раз» (`notices`: ключ — код и тело
ответа, у отказа без повтора — ещё и причина, почему повтора не было). Реестр —
значение: приложение передаёт свой реестр процесса (модуль `once`), без него у
каждой собранной двери свой `Notices()` — глобала в пакете нет. Своего `forget`
у этих строк нет намеренно: сервер, который мигает (ответил — отказал —
ответил), с ним печатал бы строку на каждом мигании. Исключение — эпизод
усечения, и он один: запрос несёт `truncate: false`, чтобы сервер не резал
длинный вход молча (замер 27.09). Как дверь повторяет пачку, что говорит и
когда эпизод кончается, — правило одно, и живёт оно в докстроке `run`.
"""

from __future__ import annotations

import http.client
import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import charoite_graph.address_policy as address_policy   # атрибутом модуля: сторож подменяет guard_model_url
from charoite_graph.model_seam import EmbedProfile, Embedder, SeamTransportError
from charoite_graph.net import open_url
from charoite_graph.notices import Notices


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
        if not isinstance(v, list) or not v:
            return False
        for value in v:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            try:
                if not math.isfinite(value):
                    return False
            except OverflowError:
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


#: Потолок цитаты тела ответа — в строке и в ключе реестра строк. Один на модуль: тело
#: пишет сервер, и отказ по длине входа может процитировать сам вход, а раньше
#: потолки разъехались по четырём местам (120 и 200 знаков; выходной круг 1 по
#: №433, DS M5).
BODY_EXCERPT = 120


def _excerpt(text: str | None) -> str:
    """Тело ответа → кусок строки: без краевых пробелов, не длиннее BODY_EXCERPT."""
    return (text or "").strip()[:BODY_EXCERPT]


def _longest(texts: list[str]) -> tuple[int, int]:
    """Номер (с единицы) и длина самого длинного текста; при равенстве — первого."""
    номер = max(range(len(texts)), key=lambda i: len(texts[i]))
    return номер + 1, len(texts[номер])


def _parse_response(status: int, text: str, count: int, dim: int | None,
                    where: str) -> tuple[list | None, str | None, str | None]:
    """Ответ сервера → `(векторы, None, None)` или `(None, смысл, строка)`.

    Смысл — то, что делает повтор повтором (код и тело, форма ответа), без
    приставки пространства: ключ реестра собирает зовущий. Строку он же печатает
    один раз. Разбор один для первого хода и для повтора: у отказа на повторе
    та же таблица, отличается только приписка про первый ответ.
    """
    краткое = _excerpt(text)
    if status != 200:
        return (None, f"{status}:{краткое}",
                f"эмбеддинги: HTTP {status} ({where}): {краткое}")
    try:
        body = json.loads(text)
    except ValueError:
        return (None, f"not-json:{краткое}",
                f"эмбеддинги: ответ не JSON ({where}): {краткое}")
    if not isinstance(body, dict):
        # JSON не объектом (`[1,2]`, `"ok"`, `null`): `.get` у списка вылетел бы
        # исключением мимо таблицы строк. Форма названа своей строкой.
        return (None, f"not-object:{краткое}",
                f"эмбеддинги: ответ не объект JSON ({where}, {type(body).__name__}): {краткое}")
    got = body.get("embeddings", [])
    if not _vectors_ok(got, count, dim):
        форма = (f"{type(got).__name__}:{len(got) if isinstance(got, list) else '-'}"
                 f"/{count}")
        return (None, f"bad-vectors:{форма}",
                f"эмбеддинги: сервер дал не по вектору на текст ({where}, ответ {форма})")
    return got, None, None


#: Признак отказа сервера из-за длины входа: все куски в теле, без учёта
#: регистра. Ollama 0.34 с `truncate: false` отвечает «the input length exceeds
#: the context length» (замер 27.09, bge-m3; та же фраза в ollama/ollama#14186),
#: сборки конца 2024 — начала 2025 года писали «input length exceeds maximum
#: context length» (n8n-io/n8n#11144, ollama/ollama#8376).
#: Два куска ловят обе формулировки, одна фраза целиком — только первую
#: (выходной круг 1 по №433, DS I4).
CONTEXT_LENGTH_MARKS = ("input length exceeds", "context length")


def _is_length_refusal(body: str) -> bool:
    """Тело отказа называет длину входа — по всем кускам CONTEXT_LENGTH_MARKS."""
    low = (body or "").lower()
    return all(кусок in low for кусок in CONTEXT_LENGTH_MARKS)


def _truncate_line(first_body: str, texts: list[str], where: str) -> str:
    """Строка эпизода усечения: повтор с `truncate: true` дал векторы.

    `first_body` — тело первого отказа (HTTP 400 на запрос без усечения).
    Текст входа в строку не попадает: только номер и число знаков самого
    длинного текста пачки — в обеих ветках. Вторая ветка — тело причину не
    назвало признаком длины: дверь не знает, о длине ли был отказ, и не
    утверждает ни того, ни другого, но векторы повтора построены с усечением,
    и об этом строка говорит.
    """
    номер, знаков = _longest(texts)
    самый_длинный = f"самый длинный текст пачки — №{номер}, {знаков} знаков"
    if _is_length_refusal(first_body):
        return (f"эмбеддинги: вход длиннее предела сервера ({where}) — "
                f"сервер усёк его, хвост текста в вектор не попал; {самый_длинный}")
    return (f"эмбеддинги: сервер отказал на запрос без усечения "
            f"(HTTP 400 ({where}): {_excerpt(first_body)}); повтор с усечением дал "
            f"векторы — если отказ был о длине, хвост текста в вектор не попал; "
            f"{самый_длинный}")


#: Что дверь считает транспортным отказом. `requests.RequestException` и
#: `urllib.error.URLError` — подклассы `OSError`; `http.client.HTTPException`
#: ловит `IncompleteRead`/`BadStatusLine`, у которых общего предка с ним нет.
#: Всё пойманное становится `SeamTransportError(policy=False)`.
TRANSPORT_ERRORS = (OSError, http.client.HTTPException)


def urllib_post(url: str, payload: dict, timeout: float) -> tuple[int, str]:
    """POST на /api/embed через stdlib: `(код, тело)` на любой HTTP-ответ.

    Ход идёт через `net.open_url`: адрес на этой машине — мимо прокси окружения и
    системы (№525), иначе — `urllib.request.urlopen`, атрибутом модуля: сторож
    сети в тестах подменяет его и обязан видеть этот ход.
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
        with open_url(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def embedder(base_url: str, model: str, *, keep_alive: str | None = None,
             post=urllib_post, refused: str = "", notices=None,
             profile: EmbedProfile | None = None) -> Embedder:
    """Собрать векторизатор на адрес и модель.

    `refused` непуст — политика уже отказала: адрес не нужен (передают `""`),
    строка отказа печатается один раз при сборке, а `run` бросает свойство
    «нельзя по настройке владельца» (`policy=True`). Иначе адрес обязан быть
    http(s), а имя модели — непустым (это держит `model_seam.Embedder`).

    `post` — транспорт значением: по умолчанию stdlib, слой моделей передаёт
    свой `requests`-адаптер. `notices` — реестр «сказать один раз» (`say` и
    `forget` с ключом `(пространство, смысл)`): приложение передаёт реестр
    процесса, без него у двери свой `Notices()`. Собирайте дверь один раз: у
    каждой сборки без реестра своя память «уже сказали».

    `profile` — префиксы и пороги модели (`model_seam.EmbedProfile`); не задан —
    профиль по имени модели. Дверь префиксов не ставит: что запрос, а что
    документ, знает только потребитель, — профиль едет к нему в паре.
    """
    if notices is None:     # первой строкой, до любой ветки: и отказу нужен реестр (вход 4, M2)
        notices = Notices()
    if refused:
        строка = refusal_line(refused)
        notices.say(("embed", строка), строка)

        def run_refused(texts: list[str], timeout: float) -> list[list[float]]:
            raise SeamTransportError(refused, policy=True)

        return Embedder(run_refused, model, refused, profile)

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

    def ход(запрос: dict, срок_хода: float) -> tuple[int, str]:
        """Один запрос двери: `(код, тело)` или `SeamTransportError`.

        Таблица «что бросил транспорт → что летит из двери» одна на оба хода
        пачки — первый и повтор с усечением. Свой отказ шва идёт как есть: он
        тоже `OSError`, и кортеж ниже перевыпустил бы его с `policy=False` —
        отказ по настройке владельца стал бы «сервер не ответил» (выходной круг
        1 по №423, DS C1). Транспортные сбои становятся
        `SeamTransportError(policy=False)`. Остальное — ошибка проводки,
        `pytest.fail` — не ловится вовсе. Раньше таблица стояла копией в
        каждом ходе, и повтор ловил отказ шва вместе с транспортом (выходной
        круг 1 по №433, DS C1/M6).
        """
        try:
            return post(endpoint, запрос, срок_хода)
        except SeamTransportError:
            raise
        except TRANSPORT_ERRORS as exc:       # отказ, таймаут, обрыв
            raise SeamTransportError(str(exc), policy=False) from exc

    def run(texts: list[str], timeout: float) -> list[list[float]]:
        """Векторы через /api/embed. Пустой список — сервер не дал векторов.

        Дверь режет список на пачки сама (`batches`) и отдаёт всё или ничего:
        вектор на каждый текст, иначе `[]` и строка в stderr с кодом и телом
        ответа. `timeout` — срок всего вызова, а не каждой пачки: иначе 120 с
        ревизии на 13 пачках становились 26 минутами, а 20 с дежавю — четырьмя
        (круг 1 по коду, Opus I1/I3).

        Первый ход пачки всегда несёт `truncate: false`: Ollama на длинный
        вход отвечает тогда 400, а не режет его молча (замер 27.09). Сервер,
        который поле не знает и режет сам, дверь не видит: его 200 неотличим
        от честного — это граница двери, а не обещание. На 400 — повтор той же
        пачки с `truncate: true`, если срок ещё остался; повтор дал векторы —
        они возвращаются. Отказ шва по настройке владельца на любом ходе летит
        как есть; транспортный сбой повтора — отказ по первому ответу: сервер
        уже ответил. Вызов, в котором ни одна пачка не получила векторов повтора
        с усечением, зовёт `notices.forget` ключа усечения: эпизод кончился,
        следующий длинный вход скажется снова — на любом выходе вызова, не
        только на успешном. Повтор, отказавший 400, усечением не считается:
        векторов по усечённому входу вызов не отдал.
        Не каждая принятая пачка: на чередовании длинной и короткой пачки
        строка звучала бы на каждой длинной (выходной круг 1 по №433, DS I2).
        """
        усекали = False
        try:
            пачки = batches(texts)
            векторы: list[list[float]] = []
            dim: int | None = None
            срок = time.monotonic() + timeout
            for номер, пачка in enumerate(пачки, 1):
                где = f"пачка {номер}/{len(пачки)}, {len(пачка)} текстов"
                осталось = срок - time.monotonic()
                if осталось <= 0:
                    notices.say(
                        ("embed", f"budget:{len(пачка)}"),
                        f"эмбеддинги: не уложились в {timeout:.0f} с на {len(texts)} текстов ({где})")
                    return []
                status, text = ход(тело(пачка, False), осталось)

                if status == 400:
                    # Сервер отказал — возможно, из-за длины входа. Повторяем ту же
                    # пачку с усечением, пока цел срок. Срок вышел или повтор
                    # оборвался транспортом — пути «как сегодня»: отказ по первому
                    # ответу, не исключение (сервер уже ответил); строка называет,
                    # почему повтора не было, но не пересказывает сбой как причину.
                    первое = (status, text)
                    остаток = срок - time.monotonic()
                    повтор = None
                    без_повтора = "срок вышел"
                    if остаток > 0:
                        try:
                            повтор = ход(тело(пачка, True), остаток)
                        except SeamTransportError as exc:
                            if exc.policy:
                                raise
                            без_повтора = "повтор не дошёл"
                    status, text = первое if повтор is None else повтор
                    got, смысл, строка = _parse_response(status, text, len(пачка), dim, где)
                    if got is None:
                        if повтор is not None:
                            # Строка — по второму ответу, в ней же тело первого:
                            # дежурный видит, что и повтор с усечением не прошёл.
                            строка = (f"{строка}; повтор с усечением не прошёл "
                                      f"(первый ответ: {_excerpt(первое[1])})")
                            notices.say(("embed", f"truncate-fail:{смысл}"), строка)
                        else:
                            notices.say(("embed", f"{смысл}|{без_повтора}"),
                                        f"{строка}; повтор с усечением: {без_повтора}")
                        return []
                    # Сюда доходит только повтор с векторами: 400 первого хода
                    # векторов не даёт, и без повтора ветка выше уже вернула [].
                    усекали = True
                    notices.say(ключ_усечения, _truncate_line(первое[1], пачка, где))
                    dim = len(got[0])
                    векторы.extend(got)
                    continue

                got, смысл, строка = _parse_response(status, text, len(пачка), dim, где)
                if got is None:
                    # Отказ сервера — не молча: код и тело в stderr, один раз на
                    # одинаковый ответ. `[]` без строки стоил месяца слепой ночи —
                    # ревизия ядер печатала «лежит Ollama» на HTTP 400 (№358). 503
                    # на занятом сервере приходит с не-JSON телом — тело текстом.
                    notices.say(("embed", смысл), строка)
                    return []
                dim = len(got[0])
                векторы.extend(got)
            return векторы
        finally:
            # Один владелец эпизода на все выходы вызова: векторы, `[]` на
            # бюджете и на отказе, исключение шва. Вызов без усечения эпизод
            # снимает — следующий длинный вход сервер назовёт снова, а не
            # промолчит до конца процесса (выходной круг 2 по №433, DS C1:
            # снятие стояло только на успешном выходе).
            if not усекали:
                notices.forget(ключ_усечения)

    return Embedder(run, model, profile=profile)


def ollama_embedder(model: str, *, url: str = address_policy.DEFAULT_OLLAMA_URL,
                    allow_remote: bool = False, keep_alive: str | None = None,
                    post=urllib_post, notices=None,
                    profile: EmbedProfile | None = None) -> Embedder:
    """Векторизатор Ollama `/api/embed` по адресу и имени модели — вход пакета.

    Адрес проходит `address_policy.guard_model_url`: сервер на этой машине —
    да; другой — только при `allow_remote=True` и только по https, открытый http —
    в своей сети. Отказ — `address_policy.AddressRefused` (подкласс `ValueError`)
    сразу, при сборке: вызывающий назвал адрес явно, и тихая лексика вместо
    векторов значила бы «сделали вид, что настройка применена». Дверь-отказ
    (`embedder(..., refused=...)`) — путь приложения, где демон не должен падать.

    `keep_alive` по умолчанию не задан — сколько держать модель в памяти, решает
    сервер (у Ollama — 5 минут). Остальное — как у `embedder`: транспорт `post`,
    реестр строк `notices`; собирайте векторизатор один раз.
    """
    return embedder(address_policy.guard_model_url(url, allow_remote=allow_remote), model,
                    keep_alive=keep_alive, post=post, notices=notices, profile=profile)
