"""Файловые локи: общие приёмы пробы и захвата (партия D-П6).

Пять контуров держат flock по-своему, и это НЕ дубль: проба ночного
фона, замок мутатора, очередь пересборок (блокирующий EX), deadline
замка графа и одиночность демона — разные политики. Сюда сведены только
приёмы, которые дублировались дословно. Всё остальное — blocking или
nonblocking, таймауты, что писать в файл лока — остаётся у вызывающего
(бриф партии, #407).

Политика функций модуля — одной таблицей:

| функция | flock | «занято» | ФС без flock (OSError) |
|---|---|---|---|
| `held_by_someone` | проба SH, одна попытка | чужой эксклюзив | «свободно» |
| `held_by_anyone` | проба EX, 3 попытки через 0,1 с | любой держатель, SH или EX | «свободно» |
| `acquire_exclusive` | EX, `attempts` через `pause` | чужой лок любого вида | отказ сразу (демон: `busy=(OSError,)` — ретраи) |
| `acquire_shared` | SH, `attempts` через `pause` | чужой эксклюзив | отказ сразу |
| `acquire_outcome` | EX или `op`, `attempts` через `pause` | исход `BUSY` | исход `NO_FLOCK` |

Пробы берут лок на микросекунды и сразу отпускают — поэтому у захватов и у
пробы эксклюзивом есть ретраи: чужая проба не должна читаться как держатель.
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


#: Исходы попытки взять лок: взят; занят держателем (ретраи кончились); ФС
#: без flock — судить не по чему. Три значения, а не bool: проба эксклюзивом
#: читает «занят» и «без flock» по-разному, а захваты — одинаково (отказ).
#: Публичные имена — для исхода `acquire_outcome`: установщику важно их
#: различить (занято — дождаться соседа, без flock — отказ с причиной).
TAKEN, BUSY, NO_FLOCK = "taken", "busy", "no-flock"
_TAKEN, _BUSY, _NO_FLOCK = TAKEN, BUSY, NO_FLOCK


def _try_lock(f, op: int, *, attempts: int, pause: float,
              busy: tuple[type[BaseException], ...], sleep) -> str:
    """Одна механика неблокирующего захвата с короткими ретраями для всех
    приёмов модуля: `op` — `LOCK_EX` или `LOCK_SH`. При busy=(OSError,) вторая
    ветка except мертва намеренно — порядок клауз менять нельзя (круг-1 по
    #415, DS: перестановка молча сменила бы политику ENOLCK; вызов демона
    пиннит структурный тест)."""
    if attempts < 1:
        # «0 ретраев» читается как «одна попытка», а range(0) молча не делал
        # ни одной — для демона это ложное «уже слушает» (круг-1 по #415, GLM).
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    for attempt in range(attempts):
        try:
            fcntl.flock(f, op | fcntl.LOCK_NB)
            return _TAKEN
        except busy:
            if attempt == attempts - 1:
                return _BUSY
            sleep(pause)
        except OSError:
            return _NO_FLOCK
    # сюда не доходит: attempts ≥ 1, и последняя попытка возвращает в каждой ветке
    raise AssertionError("unreachable")  # pragma: no cover


def acquire_exclusive(f, *, attempts: int = 5, pause: float = 0.2,
                      busy: tuple[type[BaseException], ...] = (BlockingIOError,),
                      sleep=time.sleep) -> bool:
    """Неблокирующий эксклюзивный захват с короткими ретраями.

    Ретраи — потому что пробы (held_by_someone, held_by_anyone) держат файл
    микросекунды, и единственная попытка ложно отказывала при свободном
    локе (круг-2 по PR #399, DS). `busy` — что считать «занято и стоит
    повторить»: по умолчанию только честный BlockingIOError, прочие
    OSError (ФС без flock) — отказ сразу; демон передаёт (OSError,) —
    он не различает причины и одинаково не стартует вторым. Взятый лок
    остаётся на f: закрытие файла или смерть процесса освобождает его
    ядром.
    """
    return _try_lock(f, fcntl.LOCK_EX, attempts=attempts, pause=pause,
                     busy=busy, sleep=sleep) == _TAKEN


def acquire_shared(f, *, attempts: int = 5, pause: float = 0.2, sleep=time.sleep) -> bool:
    """Неблокирующий разделяемый захват — как `acquire_exclusive`, но `LOCK_SH`:
    держателей может быть сколько угодно, конфликтует он только с чужим
    эксклюзивом. Эксклюзивом файл берёт лишь проба `held_by_anyone` — на
    микросекунды, отсюда ретраи. ФС без flock — отказ сразу. Взятый лок
    остаётся на f до закрытия файла или смерти процесса."""
    return _try_lock(f, fcntl.LOCK_SH, attempts=attempts, pause=pause,
                     busy=(BlockingIOError,), sleep=sleep) == _TAKEN


def acquire_outcome(f, op: int = fcntl.LOCK_EX, *, attempts: int = 5, pause: float = 0.2,
                    busy: tuple[type[BaseException], ...] = (BlockingIOError,),
                    sleep=time.sleep) -> str:
    """Исход попытки взять лок: `TAKEN` / `BUSY` / `NO_FLOCK`.

    Та же механика, что у булевых захватов, но «занято» и «том без flock» —
    разные исходы: установщику первый говорит «дождитесь соседа», второй — «лок
    здесь не берётся вовсе». Умолчания — как у `acquire_exclusive` (пять попыток
    через 0,2 с): чужая проба держит файл микросекунды, и одна попытка ложно
    отвечала бы «занято». Булевы `acquire_exclusive`/`acquire_shared` не трогаем:
    их читают демон и замок мутации, и им обоих отказов довольно.
    """
    return _try_lock(f, op, attempts=attempts, pause=pause, busy=busy, sleep=sleep)


def held_by_anyone(f, *, sleep=time.sleep) -> bool:
    """True — лок держит хоть кто-то, разделяемо или эксклюзивно; False —
    свободен ИЛИ судить не по чему.

    Проба эксклюзивом: он конфликтует с любым держателем. Три попытки через
    0,1 с — такая же проба соседа держит файл микросекунды, и одна попытка
    ложно отвечала бы «занято». «Занято» — только когда все попытки упёрлись
    в BlockingIOError; ФС без flock — «свободно», фон не останавливается (как
    у held_by_someone). Взятый эксклюзив отпускается сразу.
    """
    got = _try_lock(f, fcntl.LOCK_EX, attempts=3, pause=0.1,
                    busy=(BlockingIOError,), sleep=sleep)
    if got == _TAKEN:
        fcntl.flock(f, fcntl.LOCK_UN)
    return got == _BUSY


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
