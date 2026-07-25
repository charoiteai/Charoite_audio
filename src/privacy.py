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
    cloud_live    — вопросы по ходу встречи, кусками стенограммы
    cloud_enrich  — стенограмма встречи целиком, после стопа
Вызов идёт через Claude Code по подписке; ANTHROPIC_API_KEY из окружения
вычищается на стороне вызывающего, чтобы запрос не ушёл на потокенный
биллинг. Это отдельная гарантия, здесь только выключатель.
"""
from __future__ import annotations

import os

KILL_SWITCH = "SUFLER_NO_CLOUD"


def _allowed(cfg: dict, key: str, env: dict | None) -> bool:
    env = os.environ if env is None else env
    if env.get(KILL_SWITCH):
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
