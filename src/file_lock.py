"""Маленькая граница над ``fcntl.flock`` без политики ожидания.

Владельцы локов решают разное: демон делает несколько коротких попыток,
пересборка ждёт очереди, облачный воркер ждёт до дедлайна, а сигналы
занятости только проверяют shared-локом. Здесь живут лишь одинаковые
системные примитивы; retries, timeout, sleep и деградация остаются у
вызывающего контура.
"""
from __future__ import annotations

import fcntl
import pathlib


def try_acquire(fd, *, shared: bool = False) -> bool:
    """Неблокирующе взять flock; False означает только занятость соседом.

    Прочие ``OSError`` не маскируются: ENOLCK, неподдерживаемый том и ошибки
    дескриптора имеют разную цену для разных контуров, поэтому их трактует
    вызывающий код.
    """
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def acquire(fd, *, shared: bool = False) -> None:
    """Блокирующе взять flock; системную ошибку передать вызывающему."""
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    fcntl.flock(fd, mode)


def is_held(path: pathlib.Path) -> bool:
    """Держит ли сосед exclusive-лок; неизвестность трактуется как False.

    Проба открывает файл только на чтение и берёт shared-лок: несколько
    наблюдателей не мешают друг другу. Нет файла/прав/flock — судить не по
    чему, поэтому занятость не выдумывается.
    """
    try:
        handle = pathlib.Path(path).open("r")
    except OSError:
        return False
    with handle:
        try:
            return not try_acquire(handle, shared=True)
        except OSError:
            return False
