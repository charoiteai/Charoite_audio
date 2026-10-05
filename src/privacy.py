"""Единственное место, где решается, уходит ли что-то с этой машины.

Чароит по умолчанию локальный: STT, модель, граф — всё на ноутбуке.
Облачный слой опционален, и PRIVACY.md обещает пользователю, что он
выключен, пока его не включили руками. Держать такое обещание
комментарием нельзя — раньше решение принималось в двух местах разными
выражениями, и дефолты разъехались: cloud_enrich был fail-closed,
cloud_live — fail-open (`get("cloud_live", True)`), то есть конфиг без
ключа отправлял вопросы встречи в Anthropic.

Правило: молчание конфига — это «нет». Разрешение бывает только явным и
только булевым: строка «false», пустое значение, ноль и прочий мусор — не
разрешение. Поверх всего рубильник SUFLER_NO_CLOUD: он выключает облако
независимо от конфига, чтобы «запустить заведомо офлайн» было одной
переменной окружения, а не редактированием YAML перед чужой встречей.

Что именно уходит, когда слой включён:
    cloud_live       — вопросы по ходу встречи, кусками стенограммы
    cloud_enrich     — стенограмма встречи целиком, после стопа
    cloud_hints      — стенограмма на КАЖДУЮ подсказку, то есть постоянным
                       потоком, пока идёт встреча
    cloud_edit_graph — не только отправка: право переписывать файлы графа
                       по итогам ночной ревизии

Последние два жили вне этого модуля и разъезжались ровно так, как описано
выше. cloud_hints читался в демоне и держался на том, что рядом в условии
стоял cloud_live; cloud_edit_graph — через `bool(...)`, а `bool("false")` это
True, то есть строка вместо булева давала облаку право на правку графа.

Два тумблера из четырёх — вложенные: подсказки идут по тому же живому каналу,
что ответы (cloud_live), а правка графа — часть шага разбора (cloud_enrich).
Свой ключ у них потому, что цена другая: подсказки шлют поток, а не пакет;
правка перезаписывает файлы. «Да» нужно на обоих уровнях, и записано это
здесь один раз, а не собирается из `and` по вызывающему коду.

Вызов идёт через Claude Code по подписке; ANTHROPIC_API_KEY из окружения
вычищается на стороне вызывающего, чтобы запрос не ушёл на потокенный
биллинг. Это отдельная гарантия, здесь только выключатель.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unicodedata
import urllib.parse

import charoite_graph.address_policy as address_policy   # без ребра на узел пакета (слой graph)
import charoite_paths
from charoite_graph.net import AmbiguousAddress, direct_url, is_loopback_host, loopback_url, url_host

# Два имени одного рубильника: проект переименовался в Charoite, демон
# и старые скрипты знают SUFLER_NO_CLOUD — оба работают всегда.
KILL_SWITCHES = ("CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD")
KILL_SWITCH = KILL_SWITCHES[1]  # исторический алиас для существующих импортов

# Ключи конфига, которыми управляется облако. Список нужен снаружи: здесь они
# читаются через переменную, и сканер config.example.yaml по исходникам их не
# видит — без явного экспорта они выпадают из проверки на документированность.
KEYS = ("cloud_live", "cloud_enrich", "cloud_hints", "cloud_edit_graph",
        "cloud_engine")


def _allowed(cfg: dict, key: str, env: dict | None) -> bool:
    env = os.environ if env is None else env
    if any(env.get(k) for k in KILL_SWITCHES):
        return False
    sufler = cfg.get("sufler") or {}
    # именно `is True`: «false», "", 0 и None разрешением не считаются
    return sufler.get(key) is True


def cloud_live_enabled(cfg: dict, env: dict | None = None) -> bool:
    """Живые ответы Claude по ходу встречи (куски стенограммы уходят в API)."""
    return _allowed(cfg, "cloud_live", env)


def cloud_enrich_enabled(cfg: dict, env: dict | None = None) -> bool:
    """Разбор встречи облаком после стопа (стенограмма уходит целиком)."""
    return _allowed(cfg, "cloud_enrich", env)


def cloud_hints_enabled(cfg: dict, env: dict | None = None) -> bool:
    """Облачное уточнение подсказок: поток стенограммы, пока идёт встреча.

    Требует и своего ключа, и разрешения на живой слой: канал тот же, что у
    ответов, а частота выше — поэтому отдельный тумблер поверх, а не вместо.
    """
    return _allowed(cfg, "cloud_hints", env) and cloud_live_enabled(cfg, env)


def cloud_edit_graph_enabled(cfg: dict, env: dict | None = None) -> bool:
    """Право облака ПРАВИТЬ файлы графа в ночной ревизии досье.

    Единственный ключ, который разрешает не отправку, а запись, поэтому
    планка та же, что у остальных: разрешение бывает только явным `true`.
    Поверх — общий тумблер разбора: без cloud_enrich шаг не идёт вовсе.
    """
    return _allowed(cfg, "cloud_edit_graph", env) and cloud_enrich_enabled(cfg, env)


DEFAULT_LLM_URL = address_policy.DEFAULT_OLLAMA_URL
DEFAULT_MLX_URL = "http://127.0.0.1:8080"


def cloud_engine_enabled(cfg: dict, env: dict | None = None) -> bool:
    """Разрешено ли гонять ВЕСЬ чат через облако (llm.engine: cloud).

    Отдельный тумблер, а не следствие `llm.engine`: остальные четыре ключа
    отправляют наружу отдельные куски по случаю, а этот — весь поток
    подсказок, тезисов и минуток, то есть стенограмму целиком и постоянно.
    Молчание конфига — «нет», как и везде здесь; рубильник сильнее конфига.
    """
    return _allowed(cfg, "cloud_engine", env)


def cloud_engine_active(cfg: dict, env: dict | None = None) -> bool:
    """Работает ли чат ЧЕРЕЗ ОБЛАКО на самом деле.

    Именно эту пару проверяет клиент при старте: адрес без разрешения (и
    разрешение без адреса) оставляют локальную модель. Диагностика и проба
    здоровья обязаны спрашивать то же самое, иначе доктор рапортует про
    шлюз машине, которая на нём не работает, а мёртвую локальную модель
    никто не чинит (круг-3 DS, I4).
    """
    return llm_engine(cfg) == "cloud" and cloud_engine_enabled(cfg, env)


#: Куда ходит облачный чат, если адрес не задан. Пусто — значит адрес
#: обязателен: угадывать провайдера за пользователя мы не станем.
DEFAULT_CLOUD_LLM_URL = ""


def cloud_llm_url(cfg: dict, env: dict | None = None) -> str:
    """Адрес облачного OpenAI-совместимого шлюза для llm.engine: cloud.

    Не проходит через `_guarded_url`: там политика «loopback свободно,
    остальное под allow_remote» — она для локальных серверов. Здесь наоборот:
    адрес заведомо внешний, и пускает его не allow_remote, а собственный
    тумблер `sufler.cloud_engine`, который спрашивается вызывающим. Что
    проверяем тут: адрес задан, схема https (ключ уходит в заголовке — по
    http его увидит любой на пути), и рубильник не взведён.
    """
    env = os.environ if env is None else env
    raw = str((cfg.get("llm") or {}).get("cloud_base_url") or DEFAULT_CLOUD_LLM_URL).strip()
    if not raw:
        raise PrivacyRefused(
            "llm.engine = cloud, но llm.cloud_base_url не задан: укажите "
            "адрес OpenAI-совместимого шлюза (…/v1)")
    if any(env.get(k) for k in KILL_SWITCHES):
        raise PrivacyRefused(
            f"llm.engine = cloud запрещён рубильником "
            f"{'/'.join(k for k in KILL_SWITCHES if env.get(k))}")
    url = raw.rstrip("/")
    try:
        scheme = urllib.parse.urlsplit(url).scheme
        host = url_host(url)
    except (AmbiguousAddress, ValueError) as e:
        raise PrivacyRefused(f"llm.cloud_base_url = {raw}: {e}") from e
    if scheme != "https" and not _is_loopback(host):
        raise PrivacyRefused(
            f"llm.cloud_base_url = {raw}: только https — по http ключ "
            "уходит открытым текстом (loopback разрешён для тестов)")
    return url


def llm_engine(cfg: dict) -> str:
    """Движок инференса чата: «ollama» (умолчание), «mlx-server» или «cloud».

    Единая точка, как и адреса: llm.py и llm_health спрашивают здесь, а не
    разбирают конфиг каждый по-своему. Эмбеддинги движка НЕ выбирают — bge-m3
    живёт на Ollama при любом значении (mlx_lm.server эмбеддинги не отдаёт).
    """
    raw = str((cfg.get("llm") or {}).get("engine") or "ollama").strip().lower()
    if raw not in ("ollama", "mlx-server", "cloud"):
        raise PrivacyRefused(
            f"llm.engine = {raw!r}: неизвестный движок, знаю ollama, "
            "mlx-server и cloud")
    return raw

def _is_loopback(host: str | None) -> bool:
    return is_loopback_host(host)


def proxies_for(url: str) -> dict:
    """Аргументы `requests` для адреса: прямой адрес (`net.direct_url`: эта машина или
    открытый http в свою сеть, №522) — без прокси и без редиректов.

    `proxies` с `None` отключает и переменные окружения, и системные настройки
    (requests берёт их через urllib.getproxies). Ключ `all` обязателен: `ALL_PROXY`
    окружения ложится в него, и без `None` там прокси обходил бы `http`/`https`
    (опыт с подставным прокси: ProxyError на loopback). Редирект с loopback
    уводил бы запрос на другую цель. Открытый http в свою сеть политика пустила,
    потому что текст остаётся в локальной сети, — прокси увёл бы его наружу. Для
    остальных адресов — `{}`: их прокси решает окружение, как раньше (№525).
    """
    if direct_url(url):
        return {"proxies": {"http": None, "https": None, "all": None}, "allow_redirects": False}
    return {}


def llm_base_url(cfg: dict, env: dict | None = None) -> str:
    """Единственный законный способ узнать адрес LLM-сервера.

    Четыре облачных тумблера выше стерегут слой Claude, но у проекта есть
    второй путь наружу, который они не видели: `llm.base_url`. Восемь мест
    читали его из конфига напрямую, и адрес чужой машины превращал «всё
    локально» в отправку стенограммы по сети — при выключенном облаке и
    даже под CHAROITE_NO_CLOUD. Обещание PRIVACY.md обязано держать и этот
    путь, поэтому адрес выдаётся только отсюда.

    Правило то же, что у тумблеров: молчание — «нет». Loopback проходит
    всегда. Всё остальное — только при явном `llm.allow_remote: true`
    (строго булево, как и облачные ключи) и никогда под рубильником:
    CHAROITE_NO_CLOUD означает «с этой машины ничего не уходит», а не
    «ничего, кроме того, что уходит в Ollama на другой машине».

    Отказ — исключение, а не тихий откат на localhost: молча подменить
    адрес значит сделать вид, что настройка применена.
    """
    return _guarded_url(cfg, env, key="base_url", default=DEFAULT_LLM_URL)


def mlx_base_url(cfg: dict, env: dict | None = None) -> str:
    """Адрес OpenAI-совместимого mlx_lm.server — та же дисциплина, что у
    llm_base_url: loopback свободно, чужая машина — только под явным
    llm.allow_remote и никогда под рубильником. Второй движок не должен
    стать вторым немым путём стенограммы наружу."""
    return _guarded_url(cfg, env, key="mlx_base_url", default=DEFAULT_MLX_URL)


def chat_model_url(cfg: dict, env: dict | None = None) -> str:
    """Адрес, который `LLM` спросит при сборке, — или `PrivacyRefused`.

    Развилка одна на конструктор и на старт демона: облако, когда его тумблер
    включён; mlx-server — его адрес; иначе Ollama. Демон зовёт это до загрузки
    весов, чтобы отказ грамматики (зона IPv6, логин) и отказ «чужая машина»
    были названы до `STT(cfg)`, а не трейсбеком после.
    """
    engine = llm_engine(cfg)
    if engine == "cloud" and cloud_engine_enabled(cfg, env):
        return cloud_llm_url(cfg, env)
    if engine == "mlx-server":
        return mlx_base_url(cfg, env)
    return llm_base_url(cfg, env)


class PrivacyRefused(RuntimeError):
    """Политика запретила адрес: чужая машина, открытый http наружу, рубильник.

    Свой класс, а не голый `RuntimeError`: потребителю нужно отличать «нельзя
    по настройке владельца» от любого другого сбоя в том же вызове. Пока класс
    был общим, ловящий обязан был угадывать, что ещё бросает соседний код, —
    и либо ловил лишнее, либо пропускал своё (круг 2 по коду, GLM I1).
    """


def model_address_error(cfg: dict, env: dict | None = None) -> str | None:
    """Текст отказа адреса для пробы готовности, либо None, если адрес принят.

    Та же развилка, что `chat_model_url` на старте демона. Проба кладёт текст
    в своё поле и не смешивает его с ошибкой разбора конфига: человек чинит
    адрес и сломанный yaml по-разному. Чужой сбой (конфиг не словарь) наружу
    не прячем — иначе он выглядел бы отказом адреса.
    """
    try:
        chat_model_url(cfg, env)
    except PrivacyRefused as exc:
        return str(exc)
    return None


def _switches_set(env: dict | None) -> list[str]:
    """Имена взведённых рубильников — для «взведён ли» и для текста отказа."""
    env = os.environ if env is None else env
    return [k for k in KILL_SWITCHES if env.get(k)]


#: Текст отказа приложения по виду отказа политики: ключ конфига и совет — наши,
#: решение — `address_policy.guard_model_url`.
_REFUSAL = {
    "ambiguous": lambda key, raw, e, sw: f"llm.{key} = {raw}: {e.detail}",
    "scheme": lambda key, raw, e, sw: (
        f"llm.{key} = {raw}: схема «{e.scheme or '—'}» не поддерживается, нужен http(s)"),
    "offline": lambda key, raw, e, sw: (
        f"llm.{key} = {raw} указывает не на эту машину, а рубильник "
        f"{'/'.join(sw)} запрещает любой выход наружу"),
    "cleartext": lambda key, raw, e, sw: (
        f"llm.{key} = {raw} — адрес вне своей сети по открытому http: стенограмма "
        "ушла бы по сети открытым текстом. Для удалённого адреса нужен https "
        "(llm.allow_remote этого не снимает)"
        + (address_policy.UNSPECIFIED_HINT if e.detail == address_policy.UNSPECIFIED else "")),
    "remote": lambda key, raw, e, sw: (
        f"llm.{key} = {raw} указывает не на эту машину. Чароит локальный "
        "по умолчанию: чтобы слать запросы на другой адрес, поставьте в "
        "config.yaml явное llm.allow_remote: true"),
}


def _guarded_url(cfg: dict, env: dict | None, *, key: str, default: str) -> str:
    """Конфиг и рубильник → аргументы политики пакета; отказ → `PrivacyRefused`.

    Решает `address_policy.guard_model_url` — атрибутом модуля, а не именем:
    сторож подменяет его и требует, чтобы вердикт прошёл сюда как есть (№522).
    """
    llm = cfg.get("llm") or {}
    raw = str(llm.get(key) or default)
    switches = _switches_set(env)
    try:
        return address_policy.guard_model_url(raw, allow_remote=llm.get("allow_remote"),
                                              offline=bool(switches))
    except address_policy.AddressRefused as e:
        raise PrivacyRefused(_REFUSAL[e.kind](key, raw, e, switches)) from e


def offline_required(env: dict | None = None) -> bool:
    """Рубильник «ничего не покидает машину» взведён?

    Отдельный вопрос от облачных тумблеров: те про стенограммы, а этот —
    про любой выход наружу, включая докачку весов моделей. Веса тянутся
    лениво, при первом же распознавании: с пустым `models/` демон уходил
    на huggingface.co посреди встречи, и рубильник этого не видел
    (аудит 16.08).
    """
    return bool(_switches_set(env))


def enforce_offline_downloads(env: dict | None = None) -> None:
    """Запретить библиотекам докачивать веса, когда взведён рубильник.

    Hugging Face (transformers, onnx-asr, mlx-whisper, parakeet-mlx) читает
    эти переменные сам: с ними библиотека берёт только локальный кэш и
    честно падает, если модели нет, — вместо тихого выхода в сеть.
    """
    target = os.environ if env is None else env
    if not offline_required(target):
        return
    # Присваиваем, не setdefault: заранее выставленный в окружении
    # `HF_HUB_OFFLINE=0` переживал рубильник, и библиотека уходила в сеть
    # (второе мнение по #324, 16.08). Рубильник — последнее слово.
    target["HF_HUB_OFFLINE"] = "1"
    target["TRANSFORMERS_OFFLINE"] = "1"


def is_loopback_url(url: str) -> bool:
    """Указывает ли адрес на эту машину.

    Отдельный вопрос от «можно ли туда слать»: разрешение выдаёт llm_base_url,
    а здесь спрашивают о собственности. Единственный законный повод — решить,
    вправе ли мы трогать сам сервис: перезапустить вставшую Ollama у себя можно,
    а на чужой машине это значит уронить её соседям.
    """
    return loopback_url(url)


#: Корень данных, который не лежит в домашнем каталоге. Не путь: в стенограмме
#: не должно остаться ни каталога, ни имени учётки.
DATA_ROOT_MARK = "‹данные Чароита›"


def _path_text(path: pathlib.Path | str) -> str:
    text = str(path)
    if len(text) > 1 and text.endswith("/"):
        text = text.rstrip("/")
    return text


def _realpath(path: pathlib.Path | str) -> str:
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return str(path)


def _needles() -> list[tuple[str, str, str]]:
    """Иглы этого вызова: (форма, «plain»|«percent», замена). Длинные — первыми.

    Считаются заново: тесты подменяют домашний каталог и корень данных.
    Процентная форма — только та, что отличается от обычной; ищем её в любом
    месте текста. Одинаковый текст иглы не заводим дважды.

    Корень данных и его ``realpath`` — игла, только когда путь не равен
    домашнему и не лежит в нём (и то же для ``realpath`` домашнего). Вложенный
    корень закрывает игла дома: замена та же, отдельная игла не нужна.
    """
    home = pathlib.Path.home()
    homes: list[str] = []
    for raw in (home, _realpath(home)):
        text = _path_text(raw)
        if text and text not in homes:
            homes.append(text)
    root = charoite_paths.resolve_root(__file__)
    roots: list[str] = []
    for raw in (root, _realpath(root)):
        text = _path_text(raw)
        if text and text not in roots:
            roots.append(text)

    repl: dict[str, str] = {}

    def put(path: str, replacement: str) -> None:
        # Короче двух знаков: пусто зациклило бы поиск, «/» заменил бы
        # каждый каталог. Прочий один знак иглой не бывает.
        if len(path) < 2:
            return
        repl[path] = replacement

    for path in homes:
        put(path, "~")
    for path in roots:
        if any(path == home or path.startswith(home + "/") for home in homes):
            continue
        put(path, DATA_ROOT_MARK)

    forms: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, replacement in repl.items():
        for norm in (unicodedata.normalize("NFC", path), unicodedata.normalize("NFD", path)):
            key = ("plain", norm)
            if key not in seen:
                seen.add(key)
                forms.append((norm, "plain", replacement))
            encoded = urllib.parse.quote(norm, safe="/")
            if encoded != norm:
                key = ("percent", encoded)
                if key not in seen:
                    seen.add(key)
                    forms.append((encoded, "percent", replacement))
    # Длинная игла раньше короткой: дом «…/u» иначе съедает начало корня
    # «…/u 2/данные» (пробел — граница) и оставляет «~ 2/данные».
    forms.sort(key=lambda item: len(item[0]), reverse=True)
    return forms


def _name_continues(char: str) -> bool:
    """Буква, цифра, «_» или «-»: этим знаком имя каталога ещё не кончилось."""
    return char.isalpha() or char.isdecimal() or char in "_-"


def _boundary(text: str, end: int) -> bool:
    """После иглы путь кончился, а не продолжилось имя каталога.

    Конец текста, «/», NUL и любой знак, кроме буквы, цифры, «_», «-» и «.».
    Точка — граница, только если за ней конец текста или знак, который имя
    не продолжает: «…/u.» обрывается, «…/u.old» — нет.
    """
    if end >= len(text):
        return True
    char = text[end]
    if char == ".":
        nxt = end + 1
        if nxt >= len(text):
            return True
        return not _name_continues(text[nxt])
    return not _name_continues(char)


def _percent_equal(segment: str, form: str) -> bool:
    """Процентная запись: ``%XX`` сравнивается без учёта регистра шестнадцатеричных."""
    index = 0
    size = len(form)
    while index < size:
        if form[index] == "%" and index + 2 < size and segment[index] == "%":
            if segment[index + 1].lower() != form[index + 1].lower() \
                    or segment[index + 2].lower() != form[index + 2].lower():
                return False
            index += 3
            continue
        if segment[index] != form[index]:
            return False
        index += 1
    return True


def scrub_local_paths(text: str) -> tuple[str, int]:
    """Убрать из текста встречи абсолютные пути этой машины.

    Домашний каталог (и его ``realpath``) становится ``~``. Корень данных вне
    домашнего — ``‹данные Чароита›``. Отказывать в записи из-за пути нельзя:
    пропала бы стенограмма. Возвращает (текст, число замен). В журнал, если
    замены были, уходит только это число.

    Иглы — на каждый вызов. Совпадает путь целиком: следом конец текста,
    «/», NUL или любой знак, кроме буквы, цифры, ``_``, ``-`` и точки внутри
    имени. Формы: NFC, NFD и процентная запись в любом месте текста.
    Длинная игла заменяется раньше короткой. Повторный вызов ничего не меняет.
    """
    if not text or "/" not in text:
        return text, 0
    needles = _needles()
    if not needles:
        return text, 0
    out: list[str] = []
    index = 0
    count = 0
    size = len(text)
    while index < size:
        if text[index] != "/":
            nxt = text.find("/", index)
            if nxt < 0:
                out.append(text[index:])
                break
            out.append(text[index:nxt])
            index = nxt
            continue
        matched: tuple[int, str] | None = None
        for form, kind, replacement in needles:
            end = index + len(form)
            if end > size or not _boundary(text, end):
                continue
            if kind == "percent":
                if not _percent_equal(text[index:end], form):
                    continue
            elif not text.startswith(form, index):
                continue
            matched = (len(form), replacement)
            break
        if matched is None:
            out.append("/")
            index += 1
            continue
        length, replacement = matched
        out.append(replacement)
        index += length
        count += 1
    if not count:
        return text, 0
    # Только число: сам путь в журнал не пишем. В stderr: stdout демона —
    # построчный JSON для приложения, а скраб зовут его нити мимо замка emit.
    print(f"[privacy] пути машины в тексте встречи заменены: {count}", file=sys.stderr, flush=True)
    return "".join(out), count
