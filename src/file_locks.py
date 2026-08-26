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

import contextlib
import fcntl
import os
import pathlib
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
    ядром. При busy=(OSError,) вторая ветка except мертва намеренно —
    порядок клауз менять нельзя (круг-1 по #415, DS: перестановка молча
    сменила бы политику ENOLCK; вызов демона пиннит структурный тест).
    """
    if attempts < 1:
        # «0 ретраев» читается как «одна попытка», а range(0) молча не делал
        # ни одной — для демона это ложное «уже слушает» (круг-1 по #415, GLM).
        raise ValueError(f"attempts must be >= 1, got {attempts}")
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


GRAPH_LOCK_POLL = 5.0


@contextlib.contextmanager
def graph_lock(lock_dir: pathlib.Path, wait: float, *,
               poll: float = GRAPH_LOCK_POLL,
               log=print, sleep=time.sleep, now=time.monotonic):
    """Один пишущий в граф за раз: `cloud.lock` рядом со снимками.

    Замок делят ВСЕ контуры, пишущие в файлы графа: разбор встречи
    (cloud_review) и ночная ревизия досье. Пока он жил в одном скрипте,
    ночная ревизия правила досье без него, и сверка соседа принимала её
    правки за правки облака и убирала их в карантин, отчитавшись при этом
    «✓ применены» (аудит облака 26.08, GLM I3).

    Даёт True, если замок взят; False — если за `wait` секунд сосед не
    освободил граф, каталог недоступен или ФС не умеет flock. False — это
    «работай на чтение», а не авария: судить о занятости по ошибке ФС
    нельзя (та же логика, что в held_by_someone).
    """
    try:
        fd = os.open(pathlib.Path(lock_dir) / "cloud.lock",
                     os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        log(f"замок графа не взять ({e}) — работаю на чтение")
        yield False
        return
    try:
        deadline = now() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:        # занято соседом — ждём
                if now() >= deadline:
                    yield False
                    return
                sleep(min(poll, max(0.0, deadline - now())))
            except OSError as e:           # ENOLCK и прочее — не «занято»
                log(f"замок графа не взять ({e}) — работаю на чтение")
                yield False
                return
        yield True
    finally:
        os.close(fd)
