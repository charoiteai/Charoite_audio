"""Файловые локи: два общих приёма — проба и захват (партия D-П6).

Пять контуров держат flock по-своему, и это НЕ дубль: проба ночного
фона, отказ мутатора, очередь пересборок (блокирующий EX), deadline
замка графа и одиночность демона — разные политики. Дословно
дублировались только два приёма, они и сведены сюда. Всё остальное —
shared/exclusive, blocking/nonblocking, таймауты, что писать в файл
лока и как трактовать ФС без flock — остаётся у вызывающего (бриф
партии, #407).
"""
from __future__ import annotations

import fcntl
import time


def held_by_someone(f) -> bool:
    """True — лок держит чужой эксклюзив; False — свободен ИЛИ судить не по чему.

    Разделяемая неблокирующая проба: с эксклюзивным локом владельца она
    конфликтует, с такими же проверяющими — нет. «Занято» — только когда
    flock честно отказал из-за чужого лока (BlockingIOError); ФС без
    flock (SMB/NFS) — не повод останавливать фон (ревью 18.08 ×2 и
    круг-2 по PR #399: `except OSError: return True` превращал сетевой
    том в вечное «уступаю»). Взятая проба отпускается сразу.
    """
    try:
        fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    fcntl.flock(f, fcntl.LOCK_UN)
    return False


def acquire_exclusive(f, *, attempts: int = 5, pause: float = 0.2,
                      busy: tuple[type[BaseException], ...] = (BlockingIOError,),
                      sleep=time.sleep) -> bool:
    """Неблокирующий эксклюзивный захват с короткими ретраями.

    Ретраи — потому что разделяемые пробы (held_by_someone) держат файл
    микросекунды, и единственная попытка ложно отказывала при свободном
    локе (круг-2 по PR #399, DS). `busy` — что считать «занято и стоит
    повторить»: по умолчанию только честный BlockingIOError, прочие
    OSError (ФС без flock) — отказ сразу; демон передаёт (OSError,) —
    он не различает причины и одинаково не стартует вторым. Взятый лок
    остаётся на f: закрытие файла или смерть процесса освобождает его
    ядром.
    """
    for attempt in range(attempts):
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except busy:
            if attempt == attempts - 1:
                return False
            sleep(pause)
        except OSError:
            return False
    return False
