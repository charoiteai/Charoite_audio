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

import ipaddress
import os
import urllib.parse

# Два имени одного рубильника: проект переименовался в Charoite, демон
# и старые скрипты знают SUFLER_NO_CLOUD — оба работают всегда.
KILL_SWITCHES = ("CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD")
KILL_SWITCH = KILL_SWITCHES[1]  # исторический алиас для существующих импортов

# Ключи конфига, которыми управляется облако. Список нужен снаружи: здесь они
# читаются через переменную, и сканер config.example.yaml по исходникам их не
# видит — без явного экспорта они выпадают из проверки на документированность.
KEYS = ("cloud_live", "cloud_enrich", "cloud_hints", "cloud_edit_graph")


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


DEFAULT_LLM_URL = "http://127.0.0.1:11434"

# localhost — не IP, ip_address() его не разбирает, а это самый частый адрес
# в конфиге. Остальное решает is_loopback: 127.0.0.0/8 целиком и ::1.
_LOCAL_NAMES = ("localhost",)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:      # имя машины, .local, домен — что угодно не-IP
        return False


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
    env = os.environ if env is None else env
    raw = str((cfg.get("llm") or {}).get("base_url") or DEFAULT_LLM_URL)
    url = raw.rstrip("/")
    host = urllib.parse.urlsplit(url).hostname
    if _is_loopback(host):
        return url
    if any(env.get(k) for k in KILL_SWITCHES):
        raise RuntimeError(
            f"llm.base_url = {raw} указывает не на эту машину, а рубильник "
            f"{'/'.join(k for k in KILL_SWITCHES if env.get(k))} запрещает "
            "любой выход наружу")
    if (cfg.get("llm") or {}).get("allow_remote") is True:
        return url
    raise RuntimeError(
        f"llm.base_url = {raw} указывает не на эту машину. Чароит локальный "
        "по умолчанию: чтобы слать запросы на другой адрес, поставьте в "
        "config.yaml явное llm.allow_remote: true")
