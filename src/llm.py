"""Клиент модели: ЕДИНСТВЕННАЯ точка разговора с LLM-сервером.

Весь транспорт живёт здесь: стриминг подсказок, не-стриминговые документы
(complete) и эмбеддинги (embed). Модули конвейера не собирают HTTP-запросы
сами — иначе смена сервера превращается в правку тринадцати файлов, а модель
из конфига подменяется захардкоженной (аудит 14.08: Саммари и заметки месяц
звали старую модель).

Движка два, выбирает llm.engine в конфиге (см. privacy.llm_engine):

  ollama      — умолчание, Ollama API (/api/chat, NDJSON-стрим);
  mlx-server  — OpenAI-совместимый mlx_lm.server (/v1/chat/completions, SSE).
                Зачем: у него кэш префикса (LRUPromptCache/fetch_nearest_cache),
                и растущая нить встречи получает префилл 0.3с вместо 30с
                (замер 14.08, 88×). Ограничения честно: строгого JSON-режима
                (format:"json") у него нет — complete(json_format=True) там
                полагается на промпт и разбор текста; сервер обслуживает ОДНУ
                модель, с которой запущен, поэтому small/fallback-лестница
                схлопывается в неё же (семантика CHAROITE_ONE_MODEL).

Эмбеддинги движка не выбирают: bge-m3 живёт на Ollama при любом engine —
mlx_lm.server эмбеддингов не отдаёт.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator

import requests

import charoite_paths
import model_lease
import privacy
from model_seam import (DEFAULT_EMBED_MODEL, NO_MODEL, Embedder,  # noqa: F401 — реэкспорт канона
                        SeamTransportError, embed_model_name)

# «Модель занята» — не сбой, а очередь без очереди. Ollama 0.32 с MLX-раннером
# на занятой модели отвечает 503 за ~250 мс вместо того, чтобы поставить
# запрос в очередь (факт 18.08: подсказки живой встречи 45 минут подряд
# падали, пока фон держал тяжёлую модель). Такие ответы повторяем с растущей
# паузой в пределах бюджета вызывающего — живой контур ждёт недолго
# (BUSY_WAIT_LIVE), фоновый может и подольше.
BUSY_STATUSES = frozenset({429, 502, 503})
BUSY_WAIT_LIVE = 30.0
BUSY_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
FIT_PART_BUSY_WAIT = 5.0   # сводка одной части длинных минуток (под hint_lock)

# Сводки частей длинной встречи (№265). MCP-минутки строят новый LLM на каждый
# вызов, и повтор после упавшей главной генерации заново платил за все части —
# поэтому кэш на модуле, а не на экземпляре. Ключ — речь и всё, от чего зависит
# сводка: сменилось что-то одно — пересчёт. Держим мало и недолго: это текст
# встречи в памяти процесса, на диск он не пишется.
FIT_CACHE_SIZE = 4
FIT_CACHE_TTL = 30 * 60.0
FIT_CACHE_SWEEP = 60.0     # как часто уборщик заглядывает, пока кэш не пуст
FIT_PROMPT_VERSION = 1     # поднять при правке промпта summary(): старые сводки уже не те
_fit_cache: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
_fit_cache_lock = threading.Lock()
_fit_sweeper: threading.Timer | None = None
# Стенные часы, не monotonic: monotonic на macOS и Linux стоит, пока ноутбук
# спит, и «30 минут» растягивались бы на ночь со спящей крышкой. Часы,
# ушедшие назад, запись не продлевают — она считается истёкшей.
def _fit_clock() -> float:     # тесты подменяют часы только кэшу, не всему time
    return time.time()


def _fit_expired(now: float, stamp: float) -> bool:
    return not 0 <= now - stamp < FIT_CACHE_TTL


def _fit_cache_sweep_locked(now: float) -> None:
    for key in [k for k, (stamp, _) in _fit_cache.items() if _fit_expired(now, stamp)]:
        del _fit_cache[key]


def _fit_cache_sweep() -> None:
    """Уборка по таймеру: истёкшая сводка уходит из памяти и без новых вызовов —
    иначе текст встречи висел бы в простаивающем MCP-сервере до его выхода."""
    global _fit_sweeper
    with _fit_cache_lock:
        # убирает только свой таймер: отменённый clear-ом, но уже сработавший
        # и ждавший замка, иначе обнулил бы чужой и завёл вторую цепочку
        if _fit_sweeper is not threading.current_thread():
            return
        now = _fit_clock()
        _fit_cache_sweep_locked(now)
        _fit_sweeper = None
        if _fit_cache:
            _fit_arm_sweeper_locked(now)


def _fit_arm_sweeper_locked(now: float) -> None:
    global _fit_sweeper
    if _fit_sweeper is not None:
        return
    # До ближайшего срока, но не дольше шага: таймер идёт по monotonic и после
    # сна проснулся бы поздно — с шагом отстаём от срока не больше минуты.
    due = min(stamp for stamp, _ in _fit_cache.values()) + FIT_CACHE_TTL - now
    _fit_sweeper = threading.Timer(min(FIT_CACHE_SWEEP, due), _fit_cache_sweep)
    _fit_sweeper.daemon = True
    _fit_sweeper.start()


def _fit_cache_get(key: tuple) -> str | None:
    """Свёртка из кэша; None — нет или истёк срок (истёкшие тогда уходят все)."""
    now = _fit_clock()
    with _fit_cache_lock:
        _fit_cache_sweep_locked(now)
        hit = _fit_cache.get(key)
        if hit is None:
            return None
        _fit_cache.move_to_end(key)          # LRU: прочитанное вытесняется последним
        return hit[1]


def _fit_cache_put(key: tuple, text: str) -> None:
    now = _fit_clock()
    with _fit_cache_lock:
        _fit_cache_sweep_locked(now)
        _fit_cache.pop(key, None)            # повторная запись — снова самая свежая
        _fit_cache[key] = (now, text)
        while len(_fit_cache) > FIT_CACHE_SIZE:
            _fit_cache.popitem(last=False)
        _fit_arm_sweeper_locked(now)


def _fit_speech_id(transcript: str) -> str:
    """Первое поле ключа кэша: по нему же forget_fit находит записи речи."""
    return hashlib.sha256(transcript.encode("utf-8")).hexdigest()


def forget_fit(transcript: str) -> None:
    """Сбросить сводки этой речи при любой модели и настройке: минутки
    выданы, повтор не нужен — держать текст встречи в памяти незачем."""
    sid = _fit_speech_id(transcript)
    with _fit_cache_lock:
        for key in [k for k in _fit_cache if k[0] == sid]:
            del _fit_cache[key]


def _fit_cache_clear() -> None:
    global _fit_sweeper
    with _fit_cache_lock:
        _fit_cache.clear()
        if _fit_sweeper is not None:
            _fit_sweeper.cancel()
            _fit_sweeper = None


class LLMHTTPError(RuntimeError):
    """Сервер ответил, но не результатом: HTTP-статус ≠ 200 или поле error.

    Отдельный класс, а не голый None: вызывающему коду нужны и статус
    (404 → «модель установлена?»), и текст ошибки — сообщения пользователю
    в mcp_server и graph_updater различают эти случаи.
    """

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status}: {detail[:200]}")
        self.status = status
        self.detail = detail


def parse_json_block(text: str) -> dict | None:
    """Первый JSON-объект из ответа модели.

    Модели заворачивают JSON в прозу и ```-заборы даже при прямом запрете.
    Разбор — от каждой «{» по порядку через raw_decode: жадный «{.*}» брал
    диапазон от первой скобки до последней, и «{"a":1}\nПримечание: {"b":2}»
    не разбирался вовсе (аудит 13.09, GLM M1). None — разобрать нечего.
    """
    text = text or ""
    dec = json.JSONDecoder()

    def first_object(s: str) -> dict | None:
        for m in re.finditer(r"\{", s):
            try:
                data, _ = dec.raw_decode(s, m.start())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        return None

    # ```json-забор — явный ответ: пример из прозы перед ним не должен победить
    # (круг-1 по #562, DS M8)
    fence = re.search(r"```(?:json)?[ \t]*\n(.*?)```", text, re.S)
    if fence:
        found = first_object(fence.group(1))
        if found is not None:
            return found
    return first_object(text)


# Дефолтные веса для mlx-server: тот же MoE, что боевой ollama-тег, только
# именем HF-репозитория — mlx_lm.server грузит модели из huggingface-кэша.
DEFAULT_MLX_MODEL = "mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit"

#: Где лежит ключ облачного шлюза. В конфиг его класть нельзя: config.yaml
#: попадает в бэкапы, в скриншоты и в чужие руки, а ключ — это деньги и
#: доступ. Отдельный файл с правами 600, как токен GitHub у релизов.
DEFAULT_CLOUD_KEY_FILE = "~/.config/charoite/llm_key"

#: Сколько ждём недоступный шлюз, прежде чем ответить локальной моделью.
CLOUD_BUSY_WAIT = 5.0
#: Таймаут САМОГО запроса к шлюзу (connect, read). Бюджет ретраев выше не
#: помогает против шлюза, который принял соединение и молчит: один запрос с
#: дефолтными 300 с держал бы живую подсказку пять минут (круг-2 DS, I2).
CLOUD_TIMEOUT = (5.0, 45.0)
#: Стрим локальной модели: connect и read-таймаут МЕЖДУ байтами. Прежние 300 с
#: держали hint_lock пять минут на молчащей генерации — все контуры подсказок
#: стояли, ручная отвечала «занято» (аудит 13.09, DS I2 / GLM I1; №53). Токены
#: и строки thinking сбрасывают таймер, так что долгая, но живая генерация
#: под потолок не попадает.
STREAM_TIMEOUT = (10.0, 120.0)
#: Документы (протокол, минутки): префилл 25-тысячезначного куска на холодной
#: модели или медленной машине молчит дольше двух минут — им прежний потолок
#: (DS r1 критика / GLM r1 M5 по #558).
DOC_STREAM_TIMEOUT = (10.0, 300.0)

# Оговорка о неполной записи — факт о ЗАПИСИ, не речь: блок снаружи тегов
# стенограммы, ПОСЛЕ свёртки `_fit` и любой обрезки (иначе уедет в сводку
# части или отброшенную середину), с правилом «не цитировать как сказанное»
# (№317). Формулировки промпта живут здесь, рядом с остальными; данные (текст
# оговорки) отдаёт channel_trace (Important DS входного круга).
RECORDING_BLOCK = {
    "ru": ("<о_записи>\n{note}\n</о_записи>\n"
           "Это факт о записи, не речь участников: не цитируй его как сказанное; "
           "молчание пропавшего канала не толкуй как согласие или отсутствие возражений.\n\n"),
    "en": ("<recording>\n{note}\n</recording>\n"
           "This is a fact about the recording, not a participant's words: never quote it as speech; "
           "do not read the silence of a lost channel as agreement or absence of objections.\n\n"),
    "zh": ("<recording>\n{note}\n</recording>\n"
           "这是关于录音本身的事实，不是参会者的发言：不要把它当作有人说过的话引用；"
           "丢失通道的沉默不能理解为同意或没有异议。\n\n"),
}
#: Сколько ждём ПЕРВЫЙ токен, прежде чем считать шлюз молчащим.
CLOUD_FIRST_TOKEN = 30.0
#: Во сколько раз терпеливее к шлюзу, который шлёт keepalive: он
#: думает над промптом, а не завис.
ACTIVE_FACTOR = 4.0


def cloud_key(cfg: dict) -> str:
    """Ключ облачного шлюза из файла. Пусто — значит его нет.

    Ключ НИКОГДА не логируется и не попадает в сообщения об ошибках: путь
    к файлу назвать можно, содержимое — нет.
    """
    raw = str((cfg.get("llm") or {}).get("cloud_key_file") or DEFAULT_CLOUD_KEY_FILE)
    path = pathlib.Path(raw).expanduser()
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    try:
        mode = path.stat().st_mode & 0o077
    except OSError:
        mode = 0
    if mode:
        # Не отказ: ключ уже лежит на диске, и молчаливо не работать хуже.
        # Но сказать вслух обязаны — доки обещают 600 (круг-1 DS, Minor).
        print(f"llm: {path} доступен не только владельцу (chmod 600 рекомендуется)",
              file=sys.stderr, flush=True)
    return key


# Явный потолок ответа для mlx-server, когда вызывающий не задал num_predict.
# У Ollama отсутствие num_predict означает «без потолка», а mlx_lm.server
# молча применяет СВОЙ дефолт из аргументов запуска (обычно 512) — живой тред
# и разбор графа резались бы на полуслове, внешне неотличимо от короткого
# ответа модели.
MLX_MAX_TOKENS_DEFAULT = 4096


#: Сколько Ollama держит модель эмбеддингов в памяти после запроса. Контуры
#: встречи спрашивают её десятки раз за час: без резидентности каждый вопрос
#: владельца после паузы платил бы загрузку 1.2 ГБ внутри своего таймаута, а
#: таймаут короткий — семантика просто не успевала бы.
EMBED_KEEP_ALIVE = "30m"


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


def _embed_batches(texts: list[str]) -> list[list[str]]:
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


def embedder(cfg: dict, *, model: str | None = None,
             keep_alive: str | None = EMBED_KEEP_ALIVE) -> Embedder:
    """Собрать векторизатор для того, кому нельзя знать про Ollama.

    Отдаёт пару «функция и имя»: имя нужно получателю, чтобы подписать кэш, а
    считать он всё равно не умеет. Обе половины родом из одного вызова, поэтому
    подписать чужие векторы привычным именем нечем.

    Пустой конфиг — не «возьми дефолты», а «моделей нет»: так индексатор без
    `config.yaml` и демо-прогон бенча остаются в лексике вместо того, чтобы
    стучаться на localhost пачками. Эту гарантию раньше держал гард внутри
    поиска; теперь она у того, кто вообще знает про сервер.
    """
    if not cfg:
        if model is not None:
            raise ValueError(
                "модель названа, а конфига нет: считать её негде — "
                "пустой конфиг значит «моделей нет»")
        return Embedder(lambda texts, timeout: [], NO_MODEL)
    name = embed_model_name(cfg, model)

    # Спрашиваем политику сразу, при сборке: иначе владелец узнает о своей
    # настройке только с первым вектором, а на пустом кэше поиск за встречу
    # не спросит ни одного — отказ так и останется неназванным (круг 2, DS I5).
    refused = ""
    try:
        privacy.llm_base_url(cfg)
    except privacy.PrivacyRefused as exc:
        refused = str(exc)
        _say_once(f"эмбеддинги недоступны: {exc}")

    def run(texts: list[str], timeout: float) -> list[list[float]]:
        return embed(cfg, texts, model=name, keep_alive=keep_alive, timeout=timeout)

    return Embedder(run, name, refused)


def embed(cfg: dict, texts: list[str], model: str | None = None,
          keep_alive: str | None = None, timeout: float = 20) -> list[list[float]]:
    """Эмбеддинги через /api/embed. Пустой список — сервер не ответил векторами.

    Длинный список дверь режет на пачки сама (EMBED_BATCH_TEXTS / _CHARS) и
    отдаёт всё или ничего: вектор на каждый текст, иначе `[]` и строка в
    stderr с кодом и телом ответа. `timeout` — срок всего вызова, а не каждой
    пачки: иначе 120 с ревизии на 13 пачках становились 26 минутами, а 20 с
    дежавю — четырьмя (круг 1 по коду, Opus I1/I3).

    Прямой вызов — для разовых контуров, которым резидентность не нужна
    (дежавю на встрече спрашивает раз в сорок секунд и делит слот с чат-моделью).
    Всё, что векторизует регулярно, получает `embedder()`: он несёт и имя, и
    время жизни модели.

    Всегда Ollama (privacy.llm_base_url), независимо от llm.engine:
    mlx_lm.server эмбеддингов не отдаёт, bge-m3 остаётся здесь.

    Таймаут по умолчанию короткий (20с): эмбеддинг занимает ~0.2с, и если
    сервер занят тяжёлой генерацией, вызывающему контуру дешевле пропустить
    проход, чем стоять заблокированным (замер дежавю).
    """
    payload: dict = {
        "model": embed_model_name(cfg, model),
        "input": texts,
    }
    if keep_alive:
        payload["keep_alive"] = keep_alive
    try:
        url = privacy.llm_base_url(cfg)
    except privacy.PrivacyRefused as exc:
        # Отказ политики — такой же исход «векторов не будет», как оборванная
        # сеть, и объявить это обязана дверь, а не каждый вызывающий: через
        # неё ходят и шов, и дежавю, и ревизия ядер (круг 2, DS I4 / GLM I1).
        _say_once(f"эмбеддинги недоступны: {exc}")
        raise SeamTransportError(str(exc), policy=True) from exc
    пачки = _embed_batches(texts)
    векторы: list[list[float]] = []
    dim: int | None = None
    срок = time.monotonic() + timeout
    for номер, пачка in enumerate(пачки, 1):
        payload["input"] = пачка
        где = f"пачка {номер}/{len(пачки)}, {len(пачка)} текстов"
        осталось = срок - time.monotonic()
        if осталось <= 0:
            _say_once(f"эмбеддинги: не уложились в {timeout:.0f} с на {len(texts)} текстов ({где})",
                      key=f"embed:budget:{len(пачки)}")
            return []
        try:
            r = requests.post(url + "/api/embed", json=payload, timeout=осталось)
        except OSError as exc:                     # отказ, таймаут, обрыв
            raise SeamTransportError(str(exc)) from exc
        if r.status_code != 200:
            # Отказ сервера — не молча: код и тело в stderr, один раз на
            # одинаковый ответ. `[]` без строки стоил месяца слепой ночи —
            # ревизия ядер печатала «лежит Ollama» на HTTP 400 (№358). 503 на
            # занятом сервере приходит с не-JSON телом — тело берём текстом.
            тело = (r.text or "").strip()[:200]
            _say_once(f"эмбеддинги: HTTP {r.status_code} ({где}): {тело}",
                      key=f"embed:{r.status_code}:{тело[:120]}")
            return []
        try:
            got = (r.json() or {}).get("embeddings", [])
        except ValueError:
            тело = (r.text or "").strip()[:120]
            _say_once(f"эмбеддинги: ответ не JSON ({где}): {тело}", key=f"embed:not-json:{тело}")
            return []
        if not _vectors_ok(got, len(пачка), dim):
            # Ключ — с формой ответа: в демоне, который живёт днями, другой
            # сбой той же природы у другого потребителя не должен молчать
            # (круг 1 по коду, Opus M3)
            форма = (f"{type(got).__name__}:{len(got) if isinstance(got, list) else '-'}"
                     f"/{len(пачка)}")
            _say_once(f"эмбеддинги: сервер дал не по вектору на текст ({где}, ответ {форма})",
                      key=f"embed:bad-vectors:{форма}")
            return []
        dim = len(got[0])
        векторы.extend(got)
    return векторы


class LLM:
    def __init__(self, cfg: dict):
        l = cfg["llm"]
        self._cfg = cfg          # для оживления вставшей модели в complete()
        self._warned_mlx: set[tuple[str, str]] = set()
        self._warned_mlx_lock = threading.Lock()
        self.engine = privacy.llm_engine(cfg)
        # У каждого движка свой адрес: у Ollama — llm.base_url (:11434),
        # у mlx_lm.server — llm.mlx_base_url (:8080). Оба под одной
        # privacy-дисциплиной loopback/allow_remote.
        # Облачный движок пускает не allow_remote, а свой тумблер: он
        # отправляет наружу ВЕСЬ поток, а не отдельный кусок по случаю.
        # Рубильник сильнее конфига — под ним падаем обратно на локальную
        # модель, чтобы «запустить офлайн» оставалось одной переменной.
        self.cloud_ready = False
        if self.engine == "cloud":
            if privacy.cloud_engine_enabled(cfg):
                self.base = privacy.cloud_llm_url(cfg)
                self.cloud_model = str(l.get("cloud_model") or "")
                self._key = cloud_key(cfg)
                if not self.cloud_model:
                    raise RuntimeError(
                        "llm.engine = cloud, но llm.cloud_model не задан")
                if not self._key:
                    raise RuntimeError(
                        "llm.engine = cloud, но ключ не найден: положите его в "
                        f"{(l.get('cloud_key_file') or DEFAULT_CLOUD_KEY_FILE)} "
                        "(права 600)")
                self.cloud_ready = True
            else:
                print("llm.engine = cloud, но sufler.cloud_engine не включён "
                      "(или взведён рубильник) — работаю на локальной модели",
                      file=sys.stderr, flush=True)
                self.engine = "ollama"
        self.base = (privacy.cloud_llm_url(cfg) if self.cloud_ready else
                     privacy.mlx_base_url(cfg) if self.engine == "mlx-server"
                     else privacy.llm_base_url(cfg))
        self.mlx_model = str(l.get("mlx_model") or DEFAULT_MLX_MODEL)
        self.model = l["model"]
        self.small = l.get("small_model", self.model)
        self.fallback = l.get("fallback_model", self.small)
        # Ночью — одна модель на всё.
        #
        # Днём мелкие задачи уходят на маленькую модель, и это правильно: они
        # быстрее и не занимают большую. Ночью же шаги идут подряд, большая и
        # маленькая чередуются, и на занятой памяти сервер начинает выгружать
        # одну ради другой: 12.08 за один прогон модель грузилась 41 раз,
        # запросы висели по 2-6 минут, а потом сервер лёг совсем. Держать в
        # памяти одну — дешевле, чем экономить на её размере: у 35B-A3B
        # активны те же 3B, что и у маленькой.
        if os.environ.get("CHAROITE_ONE_MODEL"):
            self.small = self.model
            self.fallback = self.model
        # Локальный запас на случай, когда облачного шлюза нет: без него
        # пропавшая сеть означает встречу без подсказок вовсе.
        fb = l.get("cloud_fallback_local")
        if fb is None:
            fb = True
        elif not isinstance(fb, bool):
            # строгий булев, как у тумблеров privacy: «false» в кавычках из YAML —
            # не False, и молча считать его истиной нельзя (аудит 13.09, DS M5)
            print(f"llm: cloud_fallback_local = {fb!r} — ожидается true/false, "
                  "считаю true", file=sys.stderr, flush=True)
            fb = True
        self.fallback_local = fb
        # Поднимает любой уход с облака на локальный запас (стрим и complete):
        # _fit не кладёт такую сводку в кэш под облачным ключом.
        self._fell_back_local = False
        # На что падаем, когда шлюза нет. Жёсткий «ollama» бил мимо у тех, чей
        # локальный движок — mlx_lm.server: в Ollama у них из чат-моделей никого,
        # она держит только bge-m3 (круг-1, обе головы).
        self.fallback_engine = str(l.get("cloud_fallback_engine") or "").strip().lower()
        if self.fallback_engine not in ("ollama", "mlx-server"):
            self.fallback_engine = "mlx-server" if l.get("mlx_model") else "ollama"
        self.temperature = float(l.get("temperature", 0.4))
        # num_ctx ЯВНО: без него Ollama грузит модель с контекстом из Modelfile
        # (qwen3.6 — 262144), KV-кэш раздувается и генерации медленнее в разы
        # (20.07: подсказка не укладывалась в 90с на «тёплой» модели)
        self.num_ctx = int(l.get("num_ctx", 8192))
        self.system = cfg["sufler"]["role"]
        # свой шаблон минуток: разделы/формат под команду, не наш дефолт
        self.minutes_template = str(cfg["sufler"].get("minutes_template", "")).strip()
        # язык генерируемых документов (минутки/саммари/мгновенный ответ):
        # ru (дефолт) | en. Роль подсказок задаёт сам пользователь в sufler.role.
        self.lang = str(cfg["sufler"].get("language", "ru")).strip().lower()

    def _models_available(self) -> set[str]:
        """Имена скачанных моделей; «сервера нет» и «моделей нет» — оба `set()`.

        Различать их незачем: `resolve_model` в обоих случаях берёт модель из
        конфига, и об ошибке скажет сама Ollama. Глотается только сеть: не-JSON
        тело `requests` отдаёт своим `JSONDecodeError`, а он тоже
        `RequestException`. Прежний `except Exception` глотал и отказ сторожа
        тестов, и ошибку в самом разборе — тесты шли по «сервер лежит», не зная
        об этом (№397). Форма ответа проверяется явно, а не падением на `m["name"]`.
        """
        try:
            r = requests.get(f"{self.base}/api/tags", timeout=3)
            body = r.json()
        except requests.RequestException:
            return set()
        models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(models, list) or not all(
                isinstance(m, dict) and isinstance(m.get("name"), str) for m in models):
            return set()
        return {m["name"] for m in models}

    def document_model(self) -> str:
        """Модель для документов — назначенная конфигом, без тихой лестницы.

        `resolve_model()` подставляет fallback или small, если основная не
        скачана: для живой подсказки это верно (лучше слабый ответ сейчас, чем
        никакого), для минуток — нет. Документ, написанный четырёхмиллиардной
        моделью вместо назначенной, выглядит так же и ничем не помечен; пусть
        лучше ollama ответит 404, а инструмент скажет владельцу «проверьте
        настройку модели» (круг 6 по коду №329, DS I1).

        На mlx-server имя своё: сервер обслуживает одну модель, с которой
        запущен, и ollama-тег ему ничего не говорит.
        """
        return self.mlx_model if self.engine == "mlx-server" else self.model

    def resolve_model(self) -> str:
        """Основная, если скачана; иначе fallback (чтобы прототип работал сразу).

        На mlx-server лестницы нет: сервер обслуживает одну модель, с которой
        запущен, — возвращаем её имя (HF-репо), не спрашивая /api/tags.
        """
        if self.engine == "mlx-server":
            return self.mlx_model
        have = self._models_available()
        for m in (self.model, self.fallback, self.small):
            if m in have:
                return m
        return self.model  # пусть ollama сам скажет об ошибке

    def _first_mlx_warn(self, model: str) -> bool:
        """True — эту пару (model, mlx_model) ещё не объявляли (потокобезопасно)."""
        with self._warned_mlx_lock:
            key = (model, self.mlx_model)
            if key in self._warned_mlx:
                return False
            self._warned_mlx.add(key)
            return True

    def stream(self, prompt: str, model: str | None = None, system: str | None = None,
               think: bool = False, num_predict: int | None = None,
               temperature: float | None = None,
               busy_wait: float = BUSY_WAIT_LIVE,
               timeout: tuple[float, float] = STREAM_TIMEOUT) -> Iterator[str]:
        # think=False КРИТИЧЕН для live-контуров: дефолтный thinking у gemma4
        # молча съедает ~10с до первого слова (замер 17.07: TTFT 10.4с → 0.5с).
        # think=True в живом контуре не используется (deep_loop удалён 26.08).
        #
        # ЛОВУШКА (замер 22.07): в Ollama num_predict ОДИН на рассуждение и ответ
        # (у Gemini это раздельные thinkingBudget/maxOutputTokens). qwen3.6 на
        # задаче «разложи по шаблону» думает на 12 тыс. знаков и съедает бюджет
        # целиком: минутки при think=True вышли ПУСТЫМИ (0 знаков) на бюджетах
        # 500 и 1600, а при 4000 — 83с против 10с и документ вдвое беднее.
        # Для документов рассуждение не включать; при think=True num_predict
        # либо не задавать вовсе (deep_loop удалён), либо давать с запасом ×8.
        messages = [
            {"role": "system", "content": system or self.system},
            {"role": "user", "content": prompt},
        ]
        if self.engine == "mlx-server":
            if model and model != self.mlx_model \
                    and self._first_mlx_warn(model):
                # Запрошенная модель на mlx-server не транслируется — это
                # больше не молча (круг-2 DS), но и не потоп: одна строка на
                # пару моделей за процесс, иначе err-лог с потолком 2 МБ
                # вытесняет реальные диагностики (круг-3 DS I1).
                print(f"llm: mlx-server игнорирует model={model} — "
                      f"гонит {self.mlx_model}", file=sys.stderr, flush=True)
            yield from self._stream_mlx(messages, think=think,
                                        num_predict=num_predict,
                                        temperature=temperature,
                                        busy_wait=busy_wait, timeout=timeout)
            return
        if self.cloud_ready:
            yield from self._stream_cloud(messages, num_predict=num_predict,
                                          temperature=temperature,
                                          busy_wait=busy_wait, timeout=timeout)
            return
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.resolve_model(),
            "messages": messages,
            "stream": True,
            "think": think,
            "keep_alive": "90m",  # держать модель в памяти всю встречу
            "options": options,
        }
        with self._open_stream(f"{self.base}/api/chat", payload, busy_wait,
                               timeout=timeout) as r:
            done = False
            for line in r.iter_lines():
                if not line:
                    continue
                data = self._json_line(r, line)
                if data.get("error"):
                    # ошибка ПОСРЕДИ 200-стрима приходит строкой {"error": …}:
                    # без этой проверки поток заканчивался «нормально» пустым,
                    # и подсказка тихо не приходила (аудит 18.08)
                    raise self._fail(r.status_code, str(data["error"]))
                chunk = self._ndjson_chunk(r, data, line)
                if chunk:
                    yield chunk
                if data.get("done"):
                    done = True
                    break
            if not done:
                # соединение закрылось без терминатора: сервер упал или сеть
                # оборвалась. Усечённый ответ не выдаём за целый — минутки
                # без хвоста встречи внешне неотличимы от готовых.
                raise self._fail(r.status_code, "стрим оборван без завершения")

    def _ndjson_chunk(self, r, data: dict, line: bytes) -> str:
        """Текст чанка NDJSON-строки Ollama; `message: null` — пустой чанк, иная
        форма — LLMHTTPError, как у SSE (круг-1 по #562, GLM M1)."""
        try:
            return (data.get("message") or {}).get("content") or ""
        except AttributeError:
            raise self._fail(r.status_code, f"неожиданная форма строки потока: {line[:120]!r}") from None

    def _json_line(self, r, line: bytes):
        """Строка потока → JSON, иначе LLMHTTPError по контракту стрима.

        Страница прокси или портала внутри 200-стрима роняла итератор голым
        ValueError мимо задокументированного LLMHTTPError, и обработчики живого
        контура его не ловили (аудит 13.09, DS M1/M2)."""
        try:
            data = json.loads(line)
        except ValueError:
            raise self._fail(r.status_code, f"не-JSON в потоке: {line[:120]!r}") from None
        if not isinstance(data, dict):
            raise self._fail(r.status_code, f"неожиданная форма строки потока: {line[:120]!r}")
        return data

    def _fail(self, status: int, detail: str) -> LLMHTTPError:
        """ЕДИНСТВЕННЫЙ способ создать LLMHTTPError внутри клиента.

        Круг-1 закрыл утечку ключа «в точке, где тело становится
        исключением», но точек оказалось несколько, и круг-2 нашёл
        пропущенную: ошибка внутри 200-стрима (`data: {"error": …}`) шла
        мимо маскировки прямо в карточку подсказки и в файл `_hints.md`.
        Заплатать третье место — значит ждать четвёртого, поэтому способ
        один, и структурный тест следит, чтобы прямой `raise LLMHTTPError`
        в этом классе больше не появлялся.
        """
        return LLMHTTPError(status, self._hide_key(detail))

    def _hide_key(self, text: str) -> str:
        """Убрать ключ из текста ошибки шлюза.

        OpenAI-совместимые шлюзы возвращают его эхом: «Incorrect API key
        provided: sk-…». Тело ответа доезжает до карточки подсказки, до файла
        стенограммы и до ответа MCP-инструмента — то есть ключ оказался бы
        записан на диск и показан на экране (круг-1 DS, Critical). Маскируем
        в единственной точке, где тело превращается в исключение.
        """
        if not self.cloud_ready or not self._key:
            return text
        return text.replace(self._key, "***")

    def _auth(self) -> dict:
        """Именованные аргументы запроса: облаку — заголовок авторизации.

        Для локальных движков возвращает ПУСТОЙ словарь, а не `headers=None`:
        вызов `requests.post(url, json=…, timeout=…)` остаётся ровно таким,
        каким был, — его форму пиннят существующие тесты, подменяющие post.
        Новый словарь на каждый вызов: requests не должен получить ссылку на
        общий объект, который кто-то дополнит и запишет в лог.
        """
        if not self.cloud_ready:
            return {}
        return {"headers": {"Authorization": f"Bearer {self._key}"}}

    # ---- аренда модели: «наша генерация в полёте» как факт для llm_health ----
    # Два шва ниже — единственная дверь к локальному серверу для стримов и
    # complete (все движки, фолбэк облака на локальную, warmup). На время
    # запроса клиент держит flock-файл (model_lease): решение о перезапуске
    # сервера в llm_health перестаёт тикать константой и щадит живую работу
    # (№264; входной круг DS и GLM 18.09). Аренда живёт короче вызова: у
    # стрима — до закрытия ответа, у complete — на один POST, поэтому к
    # моменту, когда _post_with_revive позовёт ensure_alive после отказа,
    # своей аренды на диске уже нет и вопрос «свой или чужой» не возникает.
    # Мимо швов к модели ходят только embed() (0,2 с, убить не жалко) и проба
    # llm_health.probe (тот, кто спрашивает) — структурный тест пиннит список.
    def _root(self) -> pathlib.Path:
        """Корень данных — на вызове: аренду пишет этот процесс, а читает её
        сторож здоровья по ТЕКУЩЕМУ корню (`llm_health.busy_with_ours`).

        Поле класса вычислялось при импорте, то есть раньше, чем точка входа
        называла корень: писатель клал аренду в один каталог, читатель искал в
        другом — «наша генерация в полёте» отвечало «нет», и сторож убивал
        сервер под живой генерацией (круг 1 по коду №329, GLM C1).
        """
        return charoite_paths.resolve_root(__file__)

    def lease_dir(self) -> pathlib.Path:
        """Куда этот процесс кладёт аренды модели."""
        return model_lease.lease_dir(self._root())

    def lease_stamp(self) -> str:
        """Строка в лог старта: каталог аренд, отпечаток адреса сервера (сам
        адрес в лог не идёт) и годность каталога — писатель и читатель аренд
        в разных процессах обязаны сходиться по обоим (круг 2 DS I2/M4)."""
        import hashlib
        fp = hashlib.sha256(self.base.encode("utf-8")).hexdigest()[:8]
        корень = self._root()            # один ответ канона на всю операцию: иначе
        bad = model_lease.selfcheck(корень)     # каталог в строке и проверенный каталог
        каталог = model_lease.lease_dir(корень)  # могли бы оказаться разными (круг 2, DS I2)
        return f"{каталог} · сервер {fp}" + (f" · НЕ РАБОТАЮТ: {bad}" if bad else " · годен")

    def _lease(self, kind: str, timeout) -> model_lease.Lease:
        """Аренда на один запрос; порог зависания — из read-таймаута этого же
        запроса (тот самый, по которому транспорт сам оборвёт молчание)."""
        read = timeout[1] if isinstance(timeout, tuple) else timeout
        return model_lease.Lease(self._root(), server=self.base, engine=self.engine,
                                 kind=kind, read_timeout=read)

    @staticmethod
    def _is_payload(line: bytes) -> bool:
        """Строка стрима, которая считается прогрессом генерации: NDJSON-объект
        или SSE `data:` с непустым полем. Комментарии `: keepalive` и пустые
        `data:` — «жив и молчит», ими шлюз держал бы аренду вечно (выходной
        круг DS I1 по №264)."""
        line = line.strip()
        if not line or line.startswith(b":"):
            return False
        if line.startswith(b"data:"):
            return bool(line[5:].strip())
        return True

    class _LeasedStream:
        """Стримовый ответ с арендой: байты катят deadline, выход из with —
        снимает аренду вместе с закрытием ответа. Всё остальное — у ответа."""

        def __init__(self, r, lease: model_lease.Lease) -> None:
            self._r, self._lease = r, lease

        def __enter__(self):
            enter = getattr(self._r, "__enter__", None)
            if enter is not None:
                enter()
            return self

        def __exit__(self, *exc) -> None:
            try:
                leave = getattr(self._r, "__exit__", None)
                if leave is not None:
                    leave(*exc)
            finally:
                self._lease.__exit__(*exc)

        def iter_lines(self, *a, **kw):
            for line in self._r.iter_lines(*a, **kw):
                if LLM._is_payload(line):
                    self._lease.progress()
                yield line

        def close(self) -> None:
            try:
                self._r.close()
            finally:
                self._lease.__exit__(None, None, None)

        def __getattr__(self, name: str):
            return getattr(self._r, name)

    def _open_stream(self, url: str, payload: dict, busy_wait: float,
                     timeout: float | tuple = 300):
        """POST со стримом; занятый сервер (503/429, отказ соединения) —
        повторяем с растущей паузой, пока не выйдем за busy_wait. Аренда
        модели берётся, когда сервер ПРИНЯЛ запрос (200) — очередь за занятым
        сервером аренду не держит: она самозалечивается ретраями, а на
        висящем сервере держала бы перезапуск навсегда."""
        deadline = time.monotonic() + max(0.0, busy_wait)
        for n, delay in enumerate(BUSY_BACKOFF + (BUSY_BACKOFF[-1],) * 1000):
            try:
                r = requests.post(url, json=payload, stream=True, timeout=timeout,
                                  **self._auth())
            except requests.ConnectionError:
                if time.monotonic() + delay > deadline:
                    raise
                time.sleep(delay)
                continue
            if r.status_code in BUSY_STATUSES and time.monotonic() + delay <= deadline:
                r.close()
                time.sleep(delay)
                continue
            if r.status_code != 200:
                # закрыть до raise: with-блок вызывающего сюда не дойдёт, а
                # ответ открыт как стрим; ошибка — в контракте модуля LLMHTTPError
                detail = r.text[:500]
                r.close()
                raise self._fail(r.status_code, detail)
            return self._LeasedStream(r, self._lease("stream", timeout).__enter__())
        raise RuntimeError("unreachable")  # pragma: no cover

    def _mlx_payload(self, messages: list[dict], *, think: bool | None,
                     num_predict: int | None, temperature: float | None,
                     stream: bool) -> dict:
        """Тело запроса к mlx_lm.server (/v1/chat/completions).

        Модель всегда self.mlx_model: сервер обслуживает ту одну, с которой
        запущен, и переданные ollama-теги (small/fallback из вызовов) сюда
        не транслируются — лестница схлопывается осознанно (докстринг модуля).
        num_ctx не передаётся: контекст — параметр старта сервера, проблемы
        «Modelfile раздувает KV-кэш» у этого движка нет.
        """
        payload: dict = {
            "model": self.mlx_model,
            "messages": messages,
            "stream": stream,
            "temperature": self.temperature if temperature is None else temperature,
            # потолок ВСЕГДА явный: без него сервер молча применяет свой
            # дефолт из аргументов запуска, и длинный ответ режется на
            # полуслове — внешне неотличимо от короткого ответа модели
            "max_tokens": num_predict or MLX_MAX_TOKENS_DEFAULT,
        }
        if think is not None:
            # enable_thinking — kwarg chat-шаблона qwen; сервер пробрасывает
            # его через chat_template_kwargs (mlx_lm 0.31: server.py:545)
            payload["chat_template_kwargs"] = {"enable_thinking": bool(think)}
        return payload

    def stream_messages(self, messages: list[dict], *, num_predict: int | None = None,
                        temperature: float | None = None,
                        busy_wait: float = BUSY_WAIT_LIVE,
                        timeout: tuple[float, float] = STREAM_TIMEOUT) -> Iterator[str]:
        """Стрим по готовым messages — путь для локального запаса облака.

        stream() собирает messages из prompt+system; здесь они уже собраны,
        и пересобирать их (теряя роли) ради одного вызова незачем.
        """
        if self.cloud_ready:
            # Метод существует ради локального запаса облака; вызвать его на
            # самом облачном клиенте — значит собрать ollama-протокол и
            # отправить его шлюзу (круг-1 DS, Minor).
            yield from self._stream_cloud(messages, num_predict=num_predict,
                                          temperature=temperature, busy_wait=busy_wait)
            return
        if self.engine == "mlx-server":
            yield from self._stream_mlx(messages, think=False, num_predict=num_predict,
                                        temperature=temperature, busy_wait=busy_wait,
                                        timeout=timeout)
            return
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {"model": self.resolve_model(), "messages": messages,
                   "stream": True, "think": False, "keep_alive": "90m",
                   "options": options}
        with self._open_stream(f"{self.base}/api/chat", payload, busy_wait,
                               timeout=timeout) as r:
            for line in r.iter_lines():
                if not line:
                    continue
                data = self._json_line(r, line)
                if data.get("error"):
                    raise self._fail(r.status_code, str(data["error"]))
                chunk = self._ndjson_chunk(r, data, line)
                if chunk:
                    yield chunk
                if data.get("done"):
                    return
            raise self._fail(r.status_code, "стрим оборван без завершения")

    def _cloud_payload(self, messages: list[dict], *, num_predict: int | None,
                       temperature: float | None, stream: bool) -> dict:
        """Тело запроса к облачному OpenAI-совместимому шлюзу.

        Без qwen-специфики mlx-ветки: `chat_template_kwargs` понимает
        mlx_lm.server, а чужой шлюз на неизвестное поле отвечает 400.
        Модель одна — та, что названа в конфиге: лестница «основная →
        малая» здесь не нужна, размер выбирается ценой запроса, а не
        памятью машины.
        """
        payload: dict = {
            "model": self.cloud_model,
            "messages": messages,
            "stream": stream,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if num_predict:
            payload["max_tokens"] = num_predict
        return payload

    def _stream_cloud(self, messages: list[dict], *, num_predict: int | None,
                      temperature: float | None, busy_wait: float,
                      timeout: float | tuple = CLOUD_TIMEOUT) -> Iterator[str]:
        """Стрим облачного шлюза с падением обратно на локальную модель.

        Фолбэк разрешён СТРОГО до первого отданного токена: подсказка,
        начатая облаком и продолженная локальной моделью, склеится в две
        разные мысли — этим уже обжигались на mid-stream ретрае (#430).
        Отсюда флаг `emitted`.

        Что считаем поводом упасть на локальную: сеть, таймаут и 5xx —
        то есть «шлюза сейчас нет». Ошибки 401/403/400 фолбэк НЕ ловит:
        неверный ключ или кривой запрос должны быть громкими, иначе месяц
        работаем локально и не знаем об этом.
        """
        emitted = False
        payload = self._cloud_payload(messages, num_predict=num_predict,
                                      temperature=temperature, stream=True)
        # Ждать шлюз столько же, сколько локальный сервер, незачем: у локального
        # «занято» проходит само, а недоступный шлюз лечится только запасом.
        # Живая подсказка не должна молчать полминуты (круг-1 DS, Minor).
        wait = min(busy_wait, CLOUD_BUSY_WAIT) if self.fallback_local else busy_wait
        # read-таймаут — от вызывающего, не меньше облачного минимума: документные
        # стримы (минутки, сводки — DOC_STREAM_TIMEOUT 300 с) резались 45 с и молча
        # уходили на локальную модель (круг-1 по #562, DS I1)
        read = timeout[1] if isinstance(timeout, tuple) else float(timeout)
        try:
            for chunk in self._sse(f"{self.base}/chat/completions", payload, wait,
                                   timeout=(CLOUD_TIMEOUT[0], max(CLOUD_TIMEOUT[1], read)),
                                   first_token=CLOUD_FIRST_TOKEN):
                emitted = True
                yield chunk
            return
        except LLMHTTPError as e:
            # 200 в этом исключении — не «успех»: так выглядит оборванный
            # стрим, пустое тело и HTML вместо потока (WAF, страница квоты).
            # Это главный вид «битого шлюза», и запас нужен именно здесь
            # (круг-2 GLM, I1). Отказ доступа (4xx) по-прежнему громкий.
            if emitted or (e.status != 200 and e.status < 500):
                raise
            # detail уже без ключа (_fail) — но именно он объясняет,
            # почему ушли на локальную: «Insufficient balance» иначе
            # терялся молча (круг-3 DS, I2).
            reason = f"шлюз ответил {e.status}: {e.detail[:120]}"
        except requests.RequestException as e:
            if emitted:
                raise
            reason = self._hide_key(str(e))[:150] or type(e).__name__
        except (ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
            # Битый SSE, HTML вместо потока, «choices» строкой — самый частый
            # сбой чужого шлюза (прокси, rate-limiter). До первого токена это
            # такой же повод уйти на локальную, как и сеть (круг-1 DS).
            if emitted:
                raise
            reason = f"битый ответ шлюза: {type(e).__name__}"
        if not self.fallback_local:
            raise self._fail(503, f"облако недоступно ({reason}), "
                                    "локальный запас выключен")
        print(f"llm: облако недоступно ({reason}) — отвечаю локальной моделью",
              file=sys.stderr, flush=True)
        # _fit смотрит на флаг: сводку локального запаса нельзя класть в кэш
        # под облачным ключом — повтор через минуту взял бы её вместо облака
        self._fell_back_local = True
        local = LLM({**self._cfg,
                     "llm": {**self._cfg["llm"], "engine": self.fallback_engine}})
        yield from local.stream_messages(messages, num_predict=num_predict,
                                         temperature=temperature,
                                         busy_wait=busy_wait)

    def _stream_mlx(self, messages: list[dict], *, think: bool | None,
                    num_predict: int | None,
                    temperature: float | None,
                    busy_wait: float = BUSY_WAIT_LIVE,
                    timeout: tuple[float, float] = STREAM_TIMEOUT) -> Iterator[str]:
        payload = self._mlx_payload(messages, think=think, num_predict=num_predict,
                                    temperature=temperature, stream=True)
        # тот же потолок, что у Ollama-стрима: mlx-server держал hint_lock 300 с
        # на молчащей генерации (GLM r1 M2 по #558)
        yield from self._sse(f"{self.base}/v1/chat/completions", payload, busy_wait,
                             timeout=timeout)

    def _sse(self, url: str, payload: dict, busy_wait: float,
             timeout: float | tuple = 300,
             first_token: float | None = None) -> Iterator[str]:
        """Разбор SSE-ответа OpenAI-совместимого сервера. Общий для mlx и облака.

        `first_token` — сколько ждём ПЕРВЫЙ содержательный чанк. Проверять это
        снаружи бесполезно: строки `: keepalive` и пустые дельты уходят через
        `continue`, наружу управление не возвращается, а read-таймаут сокета
        обнуляется каждым пришедшим байтом — шлюз, который «жив и молчит»,
        держал бы подсказку и слот вечно (круг-3 DS, I1).

        Тишина и активность считаются по-разному: пока идёт keepalive, шлюз
        скорее думает над длинным промптом, и рвать его на тридцатой секунде
        значит молча подменить модель локальной (круг-3 DS, I3).
        """
        silence = first_token
        active = first_token * ACTIVE_FACTOR if first_token else None
        started = time.monotonic()
        last_byte = started
        with self._open_stream(url, payload, busy_wait, timeout=timeout) as r:
            # SSE: полезная нагрузка ТОЛЬКО в строках «data: …», терминатор
            # «data: [DONE]». Всё остальное пропускаем по спецификации: во
            # время префилла сервер шлёт keepalive-комментарии «: keepalive
            # 1/1» — живой smoke 15.08 упал ровно на такой строке.
            done = False
            for line in r.iter_lines():
                now = time.monotonic()
                if line:
                    last_byte = now
                if silence is not None:
                    quiet = now - last_byte
                    # «совсем молчит» и «шлёт keepalive, но не отвечает» —
                    # разные беды с разным терпением
                    limit = silence if quiet >= silence else active
                    if now - started > limit:
                        raise self._fail(200, f"шлюз не отдал ни одного токена "
                                              f"за {int(now - started)} с")
                # «data:» без пробела — тоже валидный SSE (аудит 13.09, DS M1)
                if not line or not line.startswith(b"data:"):
                    continue
                line = line[5:].lstrip(b" ")
                if not line:
                    continue        # пустое поле data: — keepalive у части шлюзов (круг-1 по #562)
                if line.strip() == b"[DONE]":
                    done = True
                    break
                data = self._json_line(r, line)
                if isinstance(data, dict) and data.get("error"):
                    raise self._fail(r.status_code, str(data["error"]))
                try:
                    chunk = (((data.get("choices") or [{}])[0].get("delta") or {})
                             .get("content") or "")
                except (AttributeError, TypeError, IndexError, KeyError):
                    raise self._fail(r.status_code,
                                     f"неожиданная форма SSE: {line[:120]!r}") from None
                if chunk:
                    silence = active = None      # ответ пошёл — дедлайн снят
                    yield chunk
            if not done:
                raise self._fail(r.status_code, "стрим оборван без завершения")

    def warmup(self):
        """Гоним модель в память заранее — иначе первая подсказка ждёт ~20с загрузки."""
        try:
            for _ in self.stream("Ответь одним словом: готов", system="Ты просто отвечаешь: готов."):
                break
        except LLMHTTPError as e:
            # Неверный ключ шлюза молчал до первой подсказки встречи: прогрев
            # глотал всё подряд (круг-1 DS, Minor). Отказ доступа — единственное,
            # что здесь стоит сказать вслух: остальное чинится ретраями.
            if self.cloud_ready and e.status in (401, 403):
                print(f"llm: шлюз не принял ключ (HTTP {e.status}) — проверьте "
                      "llm.cloud_key_file", file=sys.stderr, flush=True)
            elif self.cloud_ready and 400 <= e.status < 500 and e.status != 429:
                # 404 — обычно неверное имя модели: молчать до первой подсказки
                # встречи так же плохо, как молчать про ключ (круг-2 DS, M5).
                print(f"llm: шлюз отказал (HTTP {e.status}) — проверьте "
                      f"llm.cloud_model и адрес", file=sys.stderr, flush=True)
        except requests.RequestException:
            pass  # ollama может быть не поднят — не валим старт; прочее — дефект, не сеть

    def complete(self, prompt: str, *, system: str | None = None,
                 model: str | None = None, think: bool | None = False,
                 json_format: bool = False, num_predict: int | None = None,
                 num_ctx: int | None = None, temperature: float | None = None,
                 timeout: float = 300, revive: bool = False,
                 busy_wait: float | None = None) -> str:
        """Не-стриминговый чат: документы, разборы, классификация.

        Возвращает текст ответа модели («» — модель промолчала).
        LLMHTTPError — сервер ответил статусом ≠ 200 или полем error;
        requests.RequestException — сеть; ValueError — тело не JSON.
        Обработка у вызывающего: у минуток, графа и заметок разная цена
        ошибки, и превращать её здесь в пустую строку — та самая тихая
        деградация (пустышка ложилась поверх готовых минуток, аудит 0.46.0).

        revive=True — одна попытка поднять вставшую посреди работы модель
        через llm_health: для длинной стенограммы между частями проходят
        минуты, и «стояла с самого начала» ловит проба ДО разбора, а этот
        флаг — падение посреди.

        num_ctx без явного значения берётся из конфига (см. __init__:
        без явного num_ctx Ollama грузит модель с контекстом из Modelfile
        и KV-кэш раздувается в разы).

        think=None — поле не передаётся вовсе, действует умолчание модели
        (у qwen3.6 это ВКЛючённое рассуждение): так исторически работают
        разбор встречи и минутки через MCP, и выключать им рассуждение —
        отдельное решение с замером, а не побочный эффект рефакторинга.

        busy_wait — сколько секунд терпеть «модель занята» (503/429, отказ
        соединения) с растущей паузой, прежде чем отдать ошибку. По умолчанию
        не дольше самого timeout и не дольше минуты; фон (разбор графа) вправе
        ждать дольше — ему некуда спешить, а живой контур ждёт мало.
        """
        if busy_wait is None:
            busy_wait = min(60.0, float(timeout))
        messages = ([{"role": "system", "content": system}] if system else []) \
                   + [{"role": "user", "content": prompt}]
        if self.cloud_ready:
            # У шлюзов есть response_format, но он у каждого свой; json_format
            # здесь, как и на mlx, держится на промпте и разборе вызывающим.
            payload = self._cloud_payload(messages, num_predict=num_predict,
                                          temperature=temperature, stream=False)
            wait = min(busy_wait, CLOUD_BUSY_WAIT) if self.fallback_local else busy_wait
            try:
                # read-таймаут — от вызывающего, не меньше облачного минимума: документные
                # вызовы (минутки timeout=600, разбор графа) резались 45 с и молча уходили
                # на локальную модель; CLOUD_TIMEOUT целиком — только для стрима подсказок
                # (аудит 13.09, DS I1)
                r = self._post_busy(f"{self.base}/chat/completions", payload,
                                    (CLOUD_TIMEOUT[0], max(CLOUD_TIMEOUT[1], float(timeout))),
                                    wait)
                body = self._checked_body(r)
                msg = ((body.get("choices") or [{}])[0].get("message") or {})
                return (msg.get("content") or "").strip()
            except (LLMHTTPError, requests.RequestException, ValueError,
                    KeyError, IndexError, AttributeError, TypeError) as e:
                # Ответ атомарный — склейки двух мыслей, из-за которой в
                # стриме фолбэк запрещён после первого токена, здесь быть не
                # может. Отказ доступа не подменяем: он должен быть громким.
                if isinstance(e, LLMHTTPError) and e.status not in (200,) and e.status < 500:
                    raise
                if not self.fallback_local:
                    raise self._fail(503, f"облако недоступно ({type(e).__name__}), "
                                          "локальный запас выключен") from e
                print(f"llm: облако не ответило ({self._hide_key(str(e))[:120]}) — "
                      "считаю локальной моделью", file=sys.stderr, flush=True)
                self._fell_back_local = True     # см. __init__: сводку запаса не кэшировать
                local = LLM({**self._cfg,
                             "llm": {**self._cfg["llm"], "engine": self.fallback_engine}})
                # model НЕ передаём: у облака имя своё (cloud_model), и
                # локальный движок с ним получил бы 404 — пусть берёт своё
                # из конфига (находка Qwen 3.8 на бенче головы 26.08).
                return local.complete(prompt, system=system, think=think,
                                      json_format=json_format, num_predict=num_predict,
                                      num_ctx=num_ctx, temperature=temperature,
                                      timeout=timeout, revive=revive,
                                      busy_wait=busy_wait)
        if self.engine == "mlx-server":
            # Строгого JSON-режима у mlx-server нет: json_format здесь
            # полагается на промпт и разбор текста вызывающим (докстринг
            # модуля). Перед сменой боевого движка это меряет bench_extract.
            payload = self._mlx_payload(messages, think=think,
                                        num_predict=num_predict,
                                        temperature=temperature, stream=False)
            r = self._post_with_revive(f"{self.base}/v1/chat/completions",
                                       payload, timeout, revive, busy_wait)
            body = self._checked_body(r)
            msg = ((body.get("choices") or [{}])[0].get("message") or {})
            return (msg.get("content") or "").strip()
        options: dict = {
            "temperature": self.temperature if temperature is None else temperature,
            "num_ctx": num_ctx or self.num_ctx,
        }
        if num_predict:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.resolve_model(),
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if think is not None:
            payload["think"] = think
        if json_format:
            payload["format"] = "json"
        r = self._post_with_revive(f"{self.base}/api/chat", payload, timeout, revive, busy_wait)
        body = self._checked_body(r)
        return ((body.get("message") or {}).get("content") or "").strip()

    def _post_busy(self, url: str, payload: dict, timeout: float, busy_wait: float):
        """POST без стрима; «занято» (503/429) повторяем с паузой в бюджете busy_wait.

        Отказ соединения и таймаут наружу не глотаем — их различает
        _post_with_revive: там решается, поднимать ли модель.
        """
        deadline = time.monotonic() + max(0.0, busy_wait)
        # аренда — на один POST: не-стрим генерирует весь ответ внутри него;
        # паузы «занято» между попытками — без аренды
        for delay in BUSY_BACKOFF + (BUSY_BACKOFF[-1],) * 1000:
            with self._lease("complete", timeout):
                r = requests.post(url, json=payload, timeout=timeout, **self._auth())
            if r.status_code in BUSY_STATUSES and time.monotonic() + delay <= deadline:
                r.close()          # соединение не держим до GC на каждой паузе (GLM M2), как в _open_stream
                time.sleep(delay)
                continue
            return r
        raise RuntimeError("unreachable")  # pragma: no cover

    def _post_with_revive(self, url: str, payload: dict, timeout: float,
                          revive: bool, busy_wait: float = 0.0):
        """POST с одной попыткой поднять модель, вставшую посреди работы."""
        try:
            return self._post_busy(url, payload, timeout, busy_wait)
        except requests.RequestException:
            if not revive:
                raise
            import llm_health
            print("llm: запрос к модели не прошёл — пробую оживить")
            if not llm_health.ensure_alive(self._cfg, lambda m: print(f"llm: {m}")):
                raise
            return self._post_busy(url, payload, timeout, busy_wait)

    def _checked_body(self, r) -> dict:
        """Тело ответа или LLMHTTPError — общая часть всех движков.

        Не @staticmethod: тело ошибки чужого шлюза может содержать ключ
        эхом, и убрать его умеет только экземпляр (круг-1 DS, Critical).
        """
        if r.status_code != 200:
            raise self._fail(r.status_code, r.text[:500])
        body = r.json()
        if isinstance(body, dict) and body.get("error"):
            raise self._fail(r.status_code, str(body["error"]))
        return body

    # Формат подсказки живёт в коде, а не в роли из конфига. Роль отвечает на
    # вопрос «кто ты и в каком контексте», формат — общий для всех и проверяем
    # тестом. Раньше он был размазан по пользовательскому config.yaml, и любая
    # правка роли молча меняла то, что человек читает во время встречи.
    HINT_FORMAT = {
        "ru": (
            "Веди конспект встречи для того, кто её слушает. Не ответ на вопрос, "
            "а нить разговора: о чём сейчас, почему это обсуждают, что было по "
            "этой теме раньше.\n\n"
            "Формат (пропускай раздел, если сказать нечего — пустых заголовков не пиши):\n"
            "● <тема сейчас, 3-5 слов>\n"
            "- <что сказали — до 2 строк, каждая до 12 слов>\n"
            "Почему: <зачем это обсуждают, что стоит за спором — одна строка>\n"
            "Было: <что по этой теме в памяти прошлых встреч, с датой — одна строка>\n"
            "Открыто: <вопрос, который висит без ответа — одна строка>\n\n"
            "Имя говорящего пиши ТОЛЬКО при передаче слова: несколько реплик одного "
            "человека подряд — имя один раз, дальше без него. Не пересказывай "
            "отчётными глаголами («утверждает», «уточняет», «упоминает») — пиши саму "
            "суть: не «Мария: Уточняет, что нужен стенд», а «нужен стенд, неделя на "
            "отработку кода».\n"
            "Пиши телеграфно, по-русски, фактами из разговора. Имена людей, систем и "
            "версий — ровно как звучали: «1.8», а не «RHEL 8». Узнаваемое название "
            "продукта, которого в разговоре не было, — выдумка, даже если оно кажется "
            "очевидным. Раздел «Было» бери ТОЛЬКО из памяти прошлых встреч выше; нет "
            "совпадений — пропусти его."
        ),
        "en": (
            "Keep a running digest for someone following the meeting. Not an answer "
            "to a question — the thread of the conversation: what is being discussed "
            "now, why, and what happened on this topic before.\n\n"
            "Format (skip a section when there is nothing to say):\n"
            "● <current topic, 3-5 words>\n"
            "- <what was said — up to 2 lines, 12 words each>\n"
            "Why: <what is behind this discussion — one line>\n"
            "Before: <what past-meeting memory says on this topic, with a date — one line>\n"
            "Open: <the question left hanging — one line>\n\n"
            "Name a speaker ONLY when the voice changes: several lines by the same "
            "person carry the name once. No reporting verbs (“states”, “clarifies”, "
            "“mentions”) — write the substance itself.\n"
            "Be terse, factual, in English. «Before» comes ONLY from the past-meeting "
            "memory above; no match — skip it."
        ),
        "zh": (
            "为正在旁听会议的人做实时纪要。不是回答问题，而是对话的脉络：现在在谈什么、"
            "为什么谈、这个话题此前有过什么。\n\n"
            "格式（没有内容的部分直接跳过）：\n"
            "● <当前话题，3-5 个词>\n"
            "- <说了什么——最多 2 行，每行不超过 12 个字>\n"
            "为什么：<讨论背后的原因——一行>\n"
            "此前：<过往会议记忆中关于该话题的内容，带日期——一行>\n"
            "待解：<悬而未决的问题——一行>\n\n"
            "发言人名字只在换人时写一次，同一人连续发言不重复名字。不用「表示」「指出」"
            "「提到」这类转述动词——直接写内容本身。\n"
            "简洁、用中文、只用对话中的事实。「此前」只能来自上方的过往会议记忆。"
        ),
    }

    # Дописывание нити. Отличие от HINT_FORMAT принципиальное: там модель
    # каждый раз пишет конспект заново, здесь — только то, чего в нити ещё нет.
    # Приём из практики прогрессивных заметок (arXiv:2510.06677): показать уже
    # собранное и попросить «только новое»; нет нового — вернуть NONE.
    THREAD_FORMAT = {
        "ru": (
            "Ниже уже собранная нить встречи и свежий кусок разговора.\n"
            "Добавь ТОЛЬКО то, чего в нити ещё нет. Ничего нового не прозвучало — "
            "ответь ровно: NONE\n\n"
            "Каждая строка начинается со знака:\n"
            "● — новая тема разговора (3-5 слов). Ставь, только если тема сменилась.\n"
            "- — что сказали: кто и что предложил, возразил, сообщил\n"
            "⚑ — решение, срок, поручение: то, за что потом спросят\n"
            "? — вопрос, оставшийся без ответа\n"
            "⏮ — что по этой теме было раньше, с датой; ТОЛЬКО из памяти прошлых "
            "встреч выше, иначе не пиши\n\n"
            "ОДНА СТРОКА — ОДНА МЫСЛЬ, до 12 слов. Это читают краем глаза во время "
            "разговора: строка в три предложения там не читается вовсе. Два факта — "
            "две строки.\n"
            "Телеграфно, по-русски, фактами из разговора. Имена людей, систем и "
            "версий — ровно как звучали: «1.8», а не «RHEL 8»: узнаваемое название "
            "продукта, которого в разговоре не было, — выдумка. Пересказывать уже "
            "записанное другими словами не нужно: это и есть повтор."
        ),
        "en": (
            "Below is the thread of the meeting so far and a fresh stretch of talk.\n"
            "Add ONLY what is not in the thread yet. Nothing new — answer exactly: NONE\n\n"
            "Every line starts with a mark:\n"
            "● — a new topic (3-5 words). Only when the topic actually changed.\n"
            "- — what was said: who proposed, objected, reported\n"
            "⚑ — a decision, a deadline, an assignment: what you will be asked about\n"
            "? — a question left unanswered\n"
            "⏮ — what happened on this topic before, with a date; ONLY from the "
            "past-meeting memory above\n\n"
            "Terse, factual, in English. Names and versions exactly as they sounded."
        ),
        "zh": (
            "以下是已整理的会议脉络和最新一段对话。\n"
            "只补充脉络中还没有的内容。没有新内容就回答：NONE\n\n"
            "每行以符号开头：\n"
            "● — 新话题（3-5 个词），仅在话题真正改变时使用\n"
            "- — 谁说了什么：提议、反对、通报\n"
            "⚑ — 决定、期限、任务\n"
            "? — 尚未回答的问题\n"
            "⏮ — 该话题此前的情况，带日期；只能来自上方的过往会议记忆\n\n"
            "简洁、用中文、只用对话中的事实。名称和版本号照原样。"
        ),
    }

    def thread(self, transcript_tail: str, so_far: str,
               model: str | None = None) -> Iterator[str]:
        """Дописать нить встречи по свежему куску разговора."""
        collected = (f"<нить>\n{so_far}\n</нить>\n\n" if so_far.strip() else "")
        return self.stream(
            collected
            + f"<свежий разговор>\n{transcript_tail}\n</свежий разговор>\n\n"
            + self.THREAD_FORMAT.get(self.lang, self.THREAD_FORMAT["en"]),
            model=model,
        )

    def hint(self, transcript_tail: str, model: str | None = None) -> Iterator[str]:
        return self.stream(
            "Свежая стенограмма встречи (последние минуты):\n\n"
            f"{transcript_tail}\n\n"
            + self.HINT_FORMAT.get(self.lang, self.HINT_FORMAT["en"]),
            model=model,
        )

    def instant(self, tail: str, model: str | None = None,
                nodes: str = "", question: str = "") -> Iterator[str]:
        """Мгновенный готовый ответ на вопрос собеседника (режим собеседования).

        По умолчанию — лёгкая модель: TTFT доли секунды и кулер не раскручивает.
        nodes — история узлов графа, упомянутых в самом вопросе: передаётся
        ЗАПРОСУ, а не через self.system — общий system делят несколько потоков,
        и узлы одного вопроса попадали бы в чужой ответ (ревью 15.08).
        question — сам вопрос ЯВНО: быстрый триггер ловит его из
        gigastt-стрима, текст которого в стенограмму не идёт, — в tail
        вопроса ещё нет, и модель отвечала по хвосту без него (аудит
        18.08, №52). Пусто — вопрос протух (fresh_question, TTL 25 с):
        тогда прежняя формулировка «последняя реплика — вопрос».
        """
        mem = ""
        if "Память прошлых встреч" in self.system:
            mem = "Память прошлых встреч" + self.system.split("Память прошлых встреч", 1)[1]
        node_block = (
            f"Из графа проекта — история узлов, упомянутых в вопросе (даты и "
            f"факты прошлых встреч, можно опираться):\n{nodes}\n\n" if nodes else "")
        ask = (
            f"Собеседник задал вопрос: «{question}». Дай на него ГОТОВЫЙ ответ "
            if question else
            "Последняя реплика собеседника — вопрос. Дай ГОТОВЫЙ ответ ")
        return self.stream(
            f"Разговор (последние реплики):\n{tail}\n\n" + node_block +
            ask + "от первого лица, "
            "2-4 предложения, по делу, без вступлений и без маркеров.",
            model=model or self.small,
            # «Уверенно» из промпта убрано сознательно: в паре с памятью прошлых
            # встреч оно на тонкой стенограмме (первая минута) рождало ответы про
            # задачи и системы, которых на ЭТОЙ встрече никто не называл, —
            # модель уверенно выдавала контекст памяти за текущую повестку.
            system=((
                "你代表主人在工作会议或面试中发言，用他的口吻回答。简短、具体，用中文。"
                "诚实优先于自信：本次会议的事实（议程、任务、名称、数字）只能来自对话内容。"
                "下方的过往会议记忆只是风格和术语的背景，不是本次会议的议程。"
                "对话中没有的信息——直接说明或给出不含具体细节的笼统回答。\n\n" + mem
            ) if self.lang == "zh" else (
                "You answer AS the owner in a work meeting or interview, in their voice. "
                "Short, concrete, in English. HONESTY OVER CONFIDENCE: facts of THIS "
                "meeting (agenda, tasks, names, numbers) come ONLY from the conversation. "
                "Past-meeting memory below is style/terminology background, NOT today's "
                "agenda. No data in the conversation — say so or answer vaguely.\n\n" + mem
            ) if self.lang == "en" else (
                "Ты отвечаешь ЗА владельца на рабочей встрече или собеседовании, его голосом. "
                "Коротко, конкретно, по-русски. ЧЕСТНОСТЬ ВАЖНЕЕ УВЕРЕННОСТИ: факты этой "
                "встречи (повестка, задачи, названия, цифры) бери ТОЛЬКО из реплик разговора. "
                "Память прошлых встреч ниже — фон для стиля и терминов, НЕ повестка текущей "
                "встречи. Нет данных в разговоре — скажи прямо или дай обтекаемую "
                "формулировку без конкретики.\n\n" + mem
            )),
            # полный ответ за ~3с вместо 5-7с: глубокую версию параллельно даёт облако
            num_predict=180,
        )

    # Единый стиль всех документов встреч: plain-md читается без рендера.
    # Правила из практик (Google md-style, meeting-minutes best practices):
    # списки вместо таблиц, жирный ключ в начале пункта, короткие блоки,
    # одинаковая структура каждый раз — читатель знает, где что искать.
    STYLE = (
        "ФОРМАТ: никаких markdown-таблиц (|…|) — они нечитаемы в plain-тексте, "
        "только списки «- …» с жирным ключом в начале пункта "
        "(например «- **Иван** — подготовить расчёт — к пятнице»). "
        "Пустая строка после каждого заголовка. Коротко, без воды."
    )
    STYLE_EN = (
        "FORMAT: no markdown tables (|…|) — unreadable as plain text; "
        "use lists «- …» with a bold key first "
        "(e.g. «- **Ivan** — prepare the estimate — by Friday»). "
        "Blank line after every heading. Terse, no filler."
    )
    STYLE_ZH = (
        "格式：禁止使用 markdown 表格（|…|）——纯文本下不可读；"
        "只用列表「- …」，每项开头加粗关键词"
        "（例如「- **伊万** — 准备预算 — 周五前」）。"
        "每个标题后空一行。简洁，不说废话。"
    )

    def summary(self, transcript: str, busy_wait: float = BUSY_WAIT_LIVE) -> Iterator[str]:
        if self.lang == "zh":
            return self._doc_stream(
                f"会议记录：\n\n{transcript}\n\n"
                "压缩成会议纪要：决定事项、任务用「- **谁** — 做什么 — 期限」格式、"
                "待解决问题。用列表，中文。"
                "硬性限制：不超过700字符，每项一行。",
                model=self.small,
                system="你把工作会议记录压缩成清晰的纪要。不说废话。" + self.STYLE_ZH,
                num_predict=320,
                temperature=0.0,
                busy_wait=busy_wait,
            )
        if self.lang == "en":
            return self._doc_stream(
                f"Meeting transcript:\n\n{transcript}\n\n"
                "Compress into a protocol: decisions, tasks as «- **Who** — what — due», "
                "open questions. Bullets, in English. "
                "HARD LIMIT: under 700 characters, one line per item.",
                model=self.small,
                system="You compress work-meeting transcripts into a crisp protocol. No filler. " + self.STYLE_EN,
                num_predict=320,
                temperature=0.0,
                busy_wait=busy_wait,
            )
        return self._doc_stream(
            f"Стенограмма встречи:\n\n{transcript}\n\n"
            "Сожми в протокол: решения, задачи списком «- **Кто** — что — срок», "
            "открытые вопросы. Маркерами, по-русски. "
            "ЖЁСТКИЙ ЛИМИТ: не длиннее 700 знаков, каждый пункт — одна строка.",
            model=self.small,
            system="Ты сжимаешь стенограммы рабочих встреч в чёткий протокол. Без воды. " + self.STYLE,
            num_predict=320,
            temperature=0.0,  # см. minutes(): документ — не творческая задача
        )

    def _doc_stream(self, *args, **kwargs) -> Iterator[str]:
        """Стрим документа (протокол, минутки): тот же stream(), но с потолком
        DOC_STREAM_TIMEOUT — префилл большого куска на холодной модели молчит
        дольше двух минут живого потолка (DS/GLM r1 по #558)."""
        kwargs.setdefault("timeout", DOC_STREAM_TIMEOUT)
        return self.stream(*args, **kwargs)

    def fit(self, transcript: str) -> str:
        """Публичный вход `_fit` для внешних сборщиков промпта (MCP-минутки):
        длинную встречу сворачивают так же, как демон (аудит 13.09, GLM I1)."""
        return self._fit(transcript)

    def _fit(self, transcript: str, *, cache: bool = True) -> str:
        """Длинную встречу сворачиваем в сводки частей, а не отдаём на обрезку.

        num_ctx 8192 — это примерно 25 000 знаков русского, то есть полчаса
        разговора. Часовая встреча не влезала вдвое, трёхчасовая вшестеро, и
        Ollama молча обрезала промпт: минутки выходили без единого решения из
        первого часа, но выглядели нормальным документом. Плюс num_predict
        делит тот же бюджет — при переполнении ответ обрывался на полуслове.
        """
        limit = max(4_000, self.num_ctx * 3 - 4_000)   # ~3 знака на токен, запас на ответ
        if len(transcript) <= limit:
            return transcript
        # В ключе — кто на деле отвечает, а не только self.small: mlx-server
        # игнорирует model= и гонит mlx_model, облако — cloud_model по своему
        # адресу. Сменили движок или сервер — прежние сводки уже чужие.
        key: tuple = ()
        if cache:      # демону (cache=False) ни ключ, ни sha256 речи не нужны
            answering = (self.mlx_model if self.engine == "mlx-server"
                         else self.cloud_model if self.cloud_ready else self.small)
            key = (_fit_speech_id(transcript), self.engine, self.base, answering,
                   self.small, self.lang, self.num_ctx, FIT_PROMPT_VERSION)
            cached = _fit_cache_get(key)
            if cached is not None:
                return cached
        step = limit // 2
        parts = [transcript[i:i + step] for i in range(0, len(transcript), step)]
        digests = []
        failures = 0
        skipped = 0    # не подряд, а всего: дыра в нумерации — результат неполный
        self._fell_back_local = False    # поднимет _stream_cloud, если облако не ответило
        for n, part in enumerate(parts, 1):
            try:
                # бюджет на «занято» — маленький: минутки идут под hint_lock,
                # и 70 частей по 30 с ожидания держали бы весь живой контур
                # полчаса без единого токена (ревью 18.08)
                text = "".join(self.summary(part, busy_wait=FIT_PART_BUSY_WAIT)).strip()
                failures = 0
            except Exception:  # noqa: BLE001 — одна упавшая часть не роняет документ
                # как в graph_updater._extract_long: часть пропускаем, остальное
                # собираем; сводки помечены номерами — дыра видна по нумерации.
                # Две подряд — модель лежит или занята надолго: дальше не
                # мучаем ни её, ни очередь за локом — отказ наружу
                failures += 1
                if failures >= 2:
                    raise
                text = ""
            if text:
                digests.append(f"[Часть {n} из {len(parts)}]\n{text}")
            else:
                skipped += 1
        if digests:
            out = "\n\n".join(digests)
            if cache and not skipped and not self._fell_back_local:
                # кэшируем только полную свёртку той модели, что в ключе: повтор
                # должен иметь шанс закрыть дыру, а «голова и хвост» ниже — не
                # свёртка вовсе
                _fit_cache_put(key, out)
            return out
        # все сводки пустые: не резать молча голову — отдать голову и хвост,
        # где обычно и решения (конец встречи), и повестка (начало)
        half = limit // 2
        return transcript[:half] + "\n\n[… середина встречи опущена …]\n\n" + transcript[-half:]

    def recording_block(self, note: str | None) -> str:
        """Блок оговорки для промпта на языке модели; пусто, если оговорки нет.
        Словарь — на уровне модуля: подделки клиента в тестах берут этот метод
        как есть, и через `self`/`LLM` до него не дотянуться."""
        if not note:
            return ""
        return RECORDING_BLOCK.get(self.lang, RECORDING_BLOCK["ru"]).format(note=note)

    def minutes(self, transcript: str, recording_note: str | None = None) -> Iterator[str]:
        """Полноценные минутки встречи (markdown, сохраняются файлом)."""
        # без кэша: он для повтора MCP-минуток (fit), а демон живёт днями, и
        # сводки его встреч держать в памяти незачем — PRIVACY обещает только MCP
        transcript = self._fit(transcript, cache=False)
        block = self.recording_block(recording_note)   # после _fit — свёртка его не трогает
        if self.lang == "zh":
            return self._doc_stream(
                f"<transcript>\n{transcript}\n</transcript>\n\n" + block +
                "按以下模板用 markdown 写会议纪要：\n"
                + (self.minutes_template + "\n\n" if self.minutes_template else
                   "# 会议纪要\n"
                   "**日期/时间：** … **参会人：** …\n"
                   "## 议题\n## 决定\n## 行动项\n## 待解决问题\n## 风险\n\n")
                + "规则：\n"
                "- 只用会议记录中说过的内容\n"
                "- 每项一行，每节最多3项\n"
                "- 行动项用复选框：「- [ ] **姓名** — 做什么 — 期限」\n"
                "  例：「- [ ] **德米特里** — 与财务对齐预算 — 7月25日前」\n"
                "- 行动项只分配给与会者（发言或被点名的人）；缺席者的事项写入「待解决问题」：「转交某某：…」\n"
                "- 行动项常以口语出现：「你来负责？」「你来做吧」「发给我」「那我来做」「请看一下」——这些也是行动项，不是闲聊\n"
                "- 决定的格式：「- **决定了什么** — 谁负责执行」\n"
                "- 参会人：对话中出现的名字；一个都没听到——写「主人及对方」\n"
                "- 空的部分只写一个词：「无」\n"
                "- 全文控制在900字符以内：纪要要一分钟能读完",
                system="你是会议记录员。准确、简练的中文会议纪要。" + self.STYLE_ZH,
                num_predict=420,
                temperature=0.0,
            )
        if self.lang == "en":
            return self._doc_stream(
                f"<transcript>\n{transcript}\n</transcript>\n\n" + block +
                "Write meeting minutes in markdown using this template:\n"
                + (self.minutes_template + "\n\n" if self.minutes_template else
                   "# Meeting minutes\n"
                   "**Date/time:** … **Participants:** …\n"
                   "## Topics\n## Decisions\n## Action items\n## Open questions\n## Risks\n\n")
                + "Rules:\n"
                "- use only what was said in the transcript\n"
                "- one line per item, at most 3 items per section\n"
                "- action items as checkboxes: «- [ ] **Name** — what — due»\n"
                "  example: «- [ ] **Dmitry** — align the budget with finance — by Jul 25»\n"
                "- assign action items only to participants (who spoke or was addressed); "
                "for someone absent write under Open questions: «pass on to Name: …»\n"
                "- an action item is often phrased casually: «can you take this?», «you do it», "
                "«send it to me», «I'll handle that», «have a look, please» — these are action items, not chatter\n"
                "- decisions as: «- **what was decided** — who implements»\n"
                "- participants: names from the conversation; none heard — «owner and counterparts»\n"
                "- empty section: single word «none»\n"
                "- keep the whole document under 900 characters: minutes are a one-minute read",
                system="You are the meeting secretary. Precise, dry minutes in English. " + self.STYLE_EN,
                num_predict=420,
                temperature=0.0,
            )
        return self._doc_stream(
            # Данные отделены тегами от инструкций, правила — позитивные
            # («пиши так»), а не отрицания: qwen следует им заметно лучше
            f"<стенограмма>\n{transcript}\n</стенограмма>\n\n" + block +
            "Составь минутки встречи в markdown по шаблону:\n"
            + (self.minutes_template + "\n\n" if self.minutes_template else
               "# Минутки встречи\n"
               "**Дата/время:** … **Участники:** …\n"
               "## Темы\n## Решения\n## Поручения\n## Открытые вопросы\n## Риски\n\n")
            + "Правила:\n"
            "- бери только то, что прозвучало в стенограмме\n"
            "- каждый пункт — одна строка, максимум 3 пункта в разделе\n"
            "- поручение пиши чекбоксом: «- [ ] **Имя** — что сделать — срок»\n"
            "  пример: «- [ ] **Дмитрий** — согласовать бюджет с финансами — до 25.07»\n"
            "- исполнитель поручения — только участник встречи (кто говорил или к кому "
            "обращались); дело для отсутствующего пиши в «Открытые вопросы»: «передать Имени: …»\n"
            "- поручение часто звучит разговорно: «возьмёшь на себя?», «давай ты», «кинь мне», "
            "«я тогда сделаю», «посмотри, пожалуйста» — это тоже поручения, а не реплики\n"
            "- решение пиши так: «- **что решили** — кто внедряет»\n"
            "- участники: имена из разговора; если имена не звучали — «владелец и собеседники»\n"
            "- в пустом разделе ставь одно слово «нет»\n"
            "- держи весь документ в пределах 900 знаков: минутки читают за минуту, "
            "это выжимка решений и поручений",
            system="Ты секретарь встречи. Пишешь точные, сухие минутки по-русски. " + self.STYLE,
            num_predict=420,  # потолок ≈1400 знаков: страховка от простыни
            # Замер на реальной встрече: при t=0.3 четыре прогона одной
            # встречи дали 39 разных утверждений, 32 — в единственном
            # экземпляре; один прогон выдумал номер задачи, которого в
            # стенограмме нет. При t=0 три прогона совпали побуквенно,
            # выдумка ушла: жадная выборка режет хвост распределения,
            # где галлюцинации и живут.
            # Оговорка: одинаковость ≠ правота. Стабильная ошибка
            # останется стабильной — на это работает сверка в
            # fact_check, а не температура.
            temperature=0.0,
        )
