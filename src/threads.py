"""Потоки продукта — только через реестр: имя и роль обязательны.

Поток продукта — сам `threading.Thread` или `threading.Timer`, обёртки нет;
реестр — метаданные при потоке. `spawn`/`timer` помнят роль и признак
`detached` для каждого заведённого потока, а тестовая обвязка
(`tests/conftest.py`) читает их: поток, за которым тест не следил и который не
объявлен `detached`, дожидается с общим бюджетом и красит прогон по имени и
роли. Без реестра брошенный поток был виден только как сторож таймаута без
виновника.

Почему имя, роль и дверь, а не голый `threading.Thread`: у потока без имени не
спросить, чей он, а у потока без роли — куда его отнести. Чужая роль и пустое
имя — `ValueError` до старта: молча заведённый поток неотличим от чужого.

`detached` — строка-причина: поток сознательно живёт до конца процесса, и
ждать его некому (демон не дожидается своих слоёв, консоль — прогрева). Причина
строка, а не флаг: в отчёте сторожа видно, почему поток брошен, — иначе
`detached` превращается в способ отключить правило одной буквой.

Замок реестра листовой: под ним не зовётся ни чужой код, ни чужой замок —
только запись или снимок словаря. Поэтому `ours()` сначала снимает пары под
замком, а `is_alive()` спрашивает уже без него, а `describe` отдаёт кортеж из
словаря, а не зовёт чужой метод.

Импортов из репозитория нет: модуль живёт в базовом слое и не знает ни корня,
ни конфига.
"""
from __future__ import annotations

import threading
import weakref
from collections.abc import Callable, Mapping
from typing import Any

#: Роли потока продукта. Чужая роль — отказ: роль нужна не для красоты, а
#: чтобы тест и разбор знали, чей поток пережил свою границу.
ROLES = ("meeting", "process", "audio", "dictate", "console")

#: Реестр: поток → (роль, причина `detached`). Ключ слабый: реестр не держит
#: поток живым и не течёт на длинной встрече.
_registry: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_lock = threading.Lock()


def _check(name: str, role: str) -> None:
    """Имя и роль — обязательны, и оба отказом до старта потока."""
    if not name:
        raise ValueError("у потока продукта должно быть имя")
    if role not in ROLES:
        raise ValueError(f"чужая роль потока {role!r}: роли — {', '.join(ROLES)}")


def _remember(thread: threading.Thread, role: str, detached: str | None) -> None:
    """Записать метаданные при потоке. Объект без слабой ссылки (заглушка в
    тесте) пропускается: регистрация не смеет ронять вызов."""
    try:
        with _lock:
            _registry[thread] = (role, detached)
    except TypeError:  # заглушка не поддерживает weakref — регистрировать нечего
        pass


def spawn(target: Callable[..., Any], *, name: str, role: str,
          args: tuple = (), kwargs: Mapping[str, Any] | None = None,
          daemon: bool = True, detached: str | None = None) -> threading.Thread:
    """Завести поток продукта: `threading.Thread` через атрибут модуля (тесты
    `tests/test_audio_capture.py` подменяют `threading.Thread`), запомнить роль
    и стартовать. `RuntimeError` старта летит наружу — его ловит вызывающий."""
    _check(name, role)
    thread = threading.Thread(target=target, name=name, args=args,
                              kwargs=dict(kwargs) if kwargs else {}, daemon=daemon)
    _remember(thread, role, detached)
    thread.start()
    return thread


def timer(interval: float, function: Callable[[], Any], *, name: str, role: str,
          detached: str | None = None) -> threading.Timer:
    """Отложенная уборка продукта: `threading.Timer` — всегда `daemon=True`,
    иначе таймер пережил бы владельца и держал выход."""
    _check(name, role)
    thread = threading.Timer(interval, function)
    thread.name = name
    thread.daemon = True
    _remember(thread, role, detached)
    thread.start()
    return thread


def ours() -> list[threading.Thread]:
    """Живые зарегистрированные потоки. Объект без `is_alive` (заглушка в
    тесте) пропускается: реестр помнит и такие, спрашивать их нечем."""
    with _lock:
        snapshot = list(_registry)
    return [t for t in snapshot
            if callable(getattr(t, "is_alive", None)) and t.is_alive()]


def describe(thread: threading.Thread) -> tuple[str, str | None] | None:
    """Роль и причина `detached` потока — или None, если он не зарегистрирован.
    Читает тестовая обвязка: по роли видно, чей поток пережил свою границу."""
    with _lock:
        return _registry.get(thread)
