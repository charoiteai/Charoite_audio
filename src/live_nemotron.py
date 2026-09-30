"""Живой поток Nemotron в тени (№478, PR A): канал собеседников — процессу движка,
его метки — в журнал рядом с тем, что сделал с чанком живой трекер. Стенограмма не
меняется.

Зачем тень. После встречи Nemotron размечает голоса лучше трекера ERes2Net (№473),
и живой контур хочется перевести на него же: распознавание ждёт метку потока с
потолком (PR B). До этого нужны три ответа с настоящих звонков: сколько чанк ждал
бы метку, сколько стоит поток рядом со встречей и сходятся ли его слоты с разбором
после встречи. Тень их собирает и ничего не решает.

Устройство.
- Процесс движка — `diarize_nemotron.py --stream` интерпретатором окружения движка
  через дверь `foreign_python.spawn_stream`: mlx в процесс демона не попадает.
- Звук — слушатель кадров хаба (`AudioHub.add_frame_listener`) на канале
  собеседников: блоки с их местом на оси хаба. До рукопожатия блоки не копятся —
  поток начинается с первого блока после него, его место — `start0`. Ось ребёнка —
  сэмплы от `start0`, поэтому блок не с того места останавливает тень, а не сдвигает
  метки молча.
- Очередь к ребёнку с потолком `QUEUE_CAP_S`: ребёнок отстал больше — тень
  останавливается с причиной (это и есть ответ «не успевает»), а не копит память.
- Давление памяти macOS — аварийная остановка: лабораторный опыт 29.09 (A/B ABBAAB на
  свободной машине, 35b и 4b в памяти) показал, что поток не отстаёт (p95 0,2 с), но в
  фазах с ним в своп выгружалось 2,1–8,3 ГБ за четыре минуты (без него — 0–0,5 ГБ),
  занятый своп в двух фазах из трёх вырос на 2,7 и 6,9 ГБ, давление раз дошло до 2;
  своп-шторм 31.08 делал подсказку 19,7 с. Уровень ≥ `PRESSURE_STOP` на старте — тень не
  поднимается, на двух проверках подряд — останавливается: встреча важнее замера.
- Чанк распознавания (`note_chunk`) ждёт, пока фронт потока пройдёт его конец, — без
  ожидания в потоке STT: строка журнала ложится, когда метка готова. Строка — на
  КАЖДЫЙ принятый чанк: метка готова, чанк до старта потока, поток мёртв, не
  дождался за `PENDING_CAP_S`, остановка.

Журнал — `logs/nemotron_live_<штамп>.jsonl`, только владельцу: числа (сэмплы, слоты,
секунды) и причины остановки — ни звука, ни текста реплик. Строки: `header` (что известно до
ребёнка), `ready` (рукопожатие), `start` (`start0` — с него сверка режет `.wav`),
`seg` и `front` (поток на оси хаба; во фронте — CPU-секунды и пик памяти ребёнка),
`mem` (давление памяти машины и занятый своп, раз в `PRESSURE_CHECK_S`),
`chunk` (исход каждого чанка), `end` (причина конца и счётчики). Журнал открыт до
конца процесса: чанк, принятый распознаванием после конца потока, тоже получает
строку — она ложится после `end`.
"""
from __future__ import annotations

import collections
import ctypes
import ctypes.util
import json
import math
import os
import pathlib
import queue
import threading
import time
import typing

import audio
import charoite_paths
import diarize_nemotron
import foreign_python
import threads

#: Режимы `sufler.live_nemotron`: выключен (по умолчанию) и тень.
OFF, SHADOW = "off", "shadow"
MODES = (OFF, SHADOW)
#: Версия журнала тени.
JOURNAL_V = 1
#: Канал потока — собеседники звонка (метка захвата хаба).
CHANNEL = "blackhole"
#: Пресет задержки потока: 1,04 с входного буфера.
PRESET = "low"
#: Звука в очереди к ребёнку, секунды: больше — ребёнок не успевает, тень останавливается.
QUEUE_CAP_S = 10.0
#: Сколько чанк ждёт метку, секунды; и сколько чанков ждут разом.
PENDING_CAP_S = 120.0
PENDING_MAX = 1000
#: Сколько секунд потока за фронтом хранится для чанков, пришедших позже фронта.
SEG_KEEP_S = 900.0
#: Рукопожатие: запуск интерпретатора и загрузка модели.
HANDSHAKE_S = 120.0
#: Остановка: ребёнку на хвост, потом убийство.
STOP_GRACE_S = 5.0
#: Выход демона: сколько ждать штатного конца тени, прежде чем убить ребёнка самому, и
#: сколько потом ждать строку `end` убитого. Сторож приложения добивает демона SIGKILL
#: через 5 с после SIGTERM, а уборку двери при выходе SIGKILL не пускает: ждать таймер
#: отсрочки здесь нельзя (выходной круг 1 по №533, C1).
CLOSE_WAIT_S = 1.0
CLOSE_MARGIN_S = 0.5
#: Давление памяти: как часто смотреть и с какого уровня macOS тень уступает
#: (1 — норма, 2 — предупреждение, 4 — критично; `kern.memorystatus_vm_pressure_level`).
PRESSURE_CHECK_S = 5.0
PRESSURE_STOP = 2
#: Потолок журнала ребёнка (stderr): библиотека, печатающая на каждый блок, не
#: заполнит диск — тень остановится с причиной.
ERRLOG_CAP_BYTES = 20 * 2**20

#: Что живой трекер сделал с чанком — закрытый набор, пишет демон: разгрузка очереди,
#: раскладка по кускам, вся речь исключена, раскладка упала, трекера раскладки нет.
CHUNK_STATES = ("shed", "pieces", "none", "split_failed", "off")
#: Состояние вне набора — чанк всё равно получает строку, с этой меткой (выходной
#: круг 1 по №478 A2, M1): инвариант «строка на каждый чанк» не зависит от вызывающего.
UNKNOWN_STATE = "unknown"

#: Исходы чанка в журнале.
LABELED, BEFORE_STREAM, DEAD_STREAM, TIMEOUT, STOPPED, LATE = (
    "labeled", "before_stream", "dead", "timeout", "stopped", "late")

STARTING, LIVE, STOPPING, DEAD = "starting", "live", "stopping", "dead"


def _num(x: typing.Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class _SwapUsage(ctypes.Structure):
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_int32)]


def _sysctl(name: bytes, value: ctypes._SimpleCData | ctypes.Structure) -> bool:
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        return False
    size = ctypes.c_size_t(ctypes.sizeof(value))
    try:
        libc = ctypes.CDLL(libc_path)
        return libc.sysctlbyname(name, ctypes.byref(value), ctypes.byref(size), None, 0) == 0
    except (OSError, AttributeError):
        return False


def memory_state() -> dict | None:
    """Давление памяти macOS и занятый своп — `sysctlbyname`, без процесса; не macOS — None."""
    level = ctypes.c_int(0)
    if not _sysctl(b"kern.memorystatus_vm_pressure_level", level):
        return None
    swap = _SwapUsage()
    used = swap.used // 2**20 if _sysctl(b"vm.swapusage", swap) else None
    return {"pressure": int(level.value), "swap_used_mb": used}


def _open_private(path: pathlib.Path) -> typing.TextIO:
    """Журнал только владельцу — тем же способом, что журнал ребёнка у двери."""
    fd = foreign_python.open_private(path)
    try:
        return os.fdopen(fd, "a", buffering=1, encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


class Shadow:
    """Тень одной встречи. Замок один на состояние и очередь ожидающих; под ним не ждут
    ни процесса, ни трубы, ни диска, ни чужого кода и не заводят нитей: его ждёт
    аудиопоток хаба (`on_frame`), а контракт хаба — «держать нельзя» (№533). Строки
    журнала под замком только встают в очередь — в порядке изменений состояния, — а на
    диск их кладёт `_drain` вне замка: его зовут все входы, кроме `on_frame`. Ребёнка
    убивает переход в DEAD — SIGKILL без ожидания, на любом пути; ожидание выхода и
    строка человеку — своей нитью из `_drain`, и её потеря теряет строку, а не убийство."""

    def __init__(self, *, journal: pathlib.Path, sr: int, stamp: str,
                 say: typing.Callable[[str], None],
                 clock: typing.Callable[[], float] = time.monotonic,
                 memory: typing.Callable[[], dict | None] = memory_state):
        self._sr = sr
        self._memory = memory
        self._mem_checked: float | None = None
        self._mem_high = 0
        self._say = say
        self._clock = clock
        self._t0 = clock()
        self._lock = threading.Lock()
        self._state = STARTING
        self._reason = ""
        self._cancel = threading.Event()
        self._stream: foreign_python.StreamProcess | None = None
        self._frame_s = 0.0
        self._start0: int | None = None
        self._next = 0
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._queued = 0
        self._front: int | None = None
        self._segs: collections.deque = collections.deque()      # (start, end, slot) на оси хаба
        self._seg_floor = 0                                       # раньше него сегменты выброшены
        self._pending: dict[tuple[str, int], dict] = {}           # порядок вставки — порядок чанков
        self._counts: collections.Counter = collections.Counter()
        self._errlog: pathlib.Path | None = None
        self._stopped_at: float | None = None
        self._dead = threading.Event()
        self._death_said: str | None = None         # смерть есть, нить «после смерти» ещё не заведена
        self._out: collections.deque = collections.deque()   # строки журнала к записи
        self._journal_lock = threading.Lock()                 # один писатель на диск, порядок очереди
        self._journal: typing.TextIO | None = _open_private(journal)
        self._line({"type": "header", "v": JOURNAL_V, "sr": sr, "channel": CHANNEL,
                    "preset": PRESET, "stamp": stamp})
        self._drain()

    # ------------------------------------------------------------ запуск

    def begin(self, *, python: str, script: pathlib.Path, args: typing.Sequence[str],
              errlog: pathlib.Path,
              spawn: typing.Callable[..., typing.Any] = foreign_python.spawn_stream) -> None:
        """Запустить ребёнка своей нитью: рукопожатие — до `HANDSHAKE_S`, встреча не ждёт."""
        self._errlog = errlog
        try:
            threads.spawn(self._start, name="nemotron-live-start", role="audio",
                          args=(python, script, list(args), errlog, spawn))
        except RuntimeError as e:          # нить не завелась — тень кончается строкой end, а не висит
            self._die_from_thread(f"нить запуска не завелась: {e}")

    def _start(self, python, script, args, errlog, spawn) -> None:
        """Граница нити запуска: любой сбой — смерть тени со строкой `end`, а не вечное
        «стартует» (выходной круг 1 по №478 A2, I1)."""
        try:
            self._start_inner(python, script, args, errlog, spawn)
        except Exception as e:  # noqa: BLE001 — граница нити тени: сбой становится смертью со строкой end
            self._die_from_thread(f"не стартовал: {type(e).__name__}: {e}")
        finally:
            self._drain()

    def _start_inner(self, python, script, args, errlog, spawn) -> None:
        stream, out = spawn(python, script, args, stderr_path=errlog, handshake_timeout=HANDSHAKE_S,
                            role="audio", on_message=self._on_message, on_eof=self._on_eof,
                            cancel=self._cancel)
        with self._lock:
            if stream is None:
                if self._state == STOPPING:
                    self._end_locked("остановлен до старта потока")
                else:
                    self._die_locked(f"не стартовал ({out.kind}: {out.reason})")
                return
            problem = self._ready_problem(out.payload)
            if self._state != STARTING or problem:
                self._abandon_locked(stream, problem or "остановлен до старта потока")
                return
            self._stream = stream
            self._frame_s = float(out.payload["frame_s"])
            self._line({"type": "ready", "t": self._t(), "proto": out.payload["proto"],
                        "frame_s": self._frame_s, "step": out.payload["step"], "pid": stream.pid})
            self._state = LIVE
        try:
            threads.spawn(self._write_loop, name="nemotron-live-writer", role="audio")
        except RuntimeError as e:          # нить не завелась — живой ребёнок без писателя не нужен
            self._die_from_thread(f"писатель не завёлся: {e}")

    def _ready_problem(self, ready: typing.Any) -> str:
        if not isinstance(ready, dict):
            return "рукопожатие не объект"
        if ready.get("proto") != diarize_nemotron.STREAM_PROTO:
            return f"протокол потока {ready.get('proto')!r}, ждали {diarize_nemotron.STREAM_PROTO}"
        if ready.get("sr") != self._sr:
            return f"частота потока {ready.get('sr')!r}, у хаба {self._sr}"
        if not (_num(ready.get("frame_s")) and ready["frame_s"] > 0 and _num(ready.get("step"))):
            return "рукопожатие без кадра или шага"
        return ""

    def _abandon_locked(self, stream: foreign_python.StreamProcess, reason: str) -> None:
        """Ребёнок поднялся, а брать его нельзя: вход закрыт и SIGKILL — сразу, без нити и
        без ожидания (выход дождётся сборщик `subprocess`); потом конец тени."""
        stream.close_input()
        stream.kill_nowait()
        if self._state == STOPPING:
            self._end_locked(reason)
        else:
            self._die_locked(reason)

    def attach(self, hub: typing.Any) -> None:
        """Слушать кадры хаба."""
        hub.add_frame_listener(self.on_frame)

    # ------------------------------------------------------------ звук

    def on_frame(self, label: str, start: int, part: typing.Any) -> None:
        """Слушатель кадров хаба: блок канала собеседников — в очередь к ребёнку. Зовётся из
        аудиопотока: под замком только состояние, `_drain` не зовёт — свои строки (`start`,
        смерть) оставляет в очереди, их запишет писатель (его будит сентинель смерти) или
        следующий вход читателя и распознавания."""
        if label != CHANNEL:
            return
        try:
            with self._lock:
                if self._state != LIVE:
                    return
                if self._start0 is None:
                    self._start0 = self._next = self._seg_floor = start
                    self._line({"type": "start", "t": self._t(), "start0": start})
                if start != self._next:
                    self._die_locked(f"разрыв оси: ждали сэмпл {self._next}, пришёл {start}")
                    return
                n = len(part)
                if self._queued + n > QUEUE_CAP_S * self._sr:
                    self._die_locked(f"ребёнок отстал больше чем на {QUEUE_CAP_S:.0f} с звука")
                    return
                self._next = start + n
                self._queued += n
                self._queue.put(audio.pcm16(part))
        except Exception as e:  # noqa: BLE001 — тень не смеет ронять захват; след — в журнале и строке
            self._fault("звук", e)

    def _write_loop(self) -> None:
        """Нить-писатель: блоки — в трубу ребёнка; сентинель — закрыть вход (EOF ребёнку).
        Граница нити: сбой — смерть тени со строкой `end`."""
        try:
            self._write_loop_inner()
        except Exception as e:  # noqa: BLE001 — граница нити тени: сбой становится смертью со строкой end
            self._die_from_thread(f"писатель упал: {type(e).__name__}: {e}")
        finally:
            self._drain()                  # строки смерти из аудиопотока — на диск (№533)

    def _write_loop_inner(self) -> None:
        stream = self._stream
        while True:
            block = self._queue.get()
            if block is None:
                break
            with self._lock:
                self._queued -= len(block) // 2
            try:
                stream.write(block)
            except OSError as e:
                # Закрытый вход — симптом: ребёнок умер или умирает. Причину называет его
                # выход (код и последняя строка журнала — у двери), а не сломанная труба:
                # писатель замечает смерть раньше читателя, и строка `end` иначе теряла
                # причину — сверку фронта, падение модели (выходной круг фикса A2 №478).
                exit_ = stream.finish(STOP_GRACE_S)
                with self._lock:
                    if self._state == LIVE:
                        self._die_locked(f"вход ребёнка закрыт ({e}); ребёнок: {exit_.reason or 'вышел'}",
                                         exit_)
                break
        stream.close_input()

    # ------------------------------------------------------------ протокол

    def _on_message(self, message: dict) -> None:
        """Нить-читатель двери: сегменты и фронт — на ось хаба и в журнал. Сбой обработки —
        смерть тени со строкой `end`, а не молчаливый счётчик двери."""
        try:
            self._on_message_inner(message)
        except Exception as e:  # noqa: BLE001 — граница обратного вызова тени: сбой становится смертью
            self._die_from_thread(f"строка протокола не разобрана: {type(e).__name__}: {e}")
        finally:
            self._drain()

    def _on_message_inner(self, message: dict) -> None:
        now = self._clock()
        health_due = False
        with self._lock:
            if self._state not in (LIVE, STOPPING) or self._start0 is None:
                self._counts["message_out_of_state"] += 1
                return
            kind = message.get("type")
            if kind == "seg":
                self._take_segment_locked(message)
            elif kind == "front":
                health_due = self._take_front_locked(message, now)
            else:
                self._counts["message_unknown"] += 1
            errlog = self._errlog
        if health_due:
            self._check_health(now, errlog)

    def _axis(self, seconds: float) -> int:
        return self._start0 + round(seconds * self._sr)

    def _take_segment_locked(self, m: dict) -> None:
        if not (_num(m.get("start")) and _num(m.get("end")) and isinstance(m.get("slot"), int)
                and not isinstance(m.get("slot"), bool) and m["end"] > m["start"]):
            self._counts["seg_malformed"] += 1
            return
        s, e = self._axis(m["start"]), self._axis(m["end"])
        self._segs.append((s, e, m["slot"]))
        self._line({"type": "seg", "t": self._t(), "start": s, "end": e, "slot": m["slot"],
                    "open": bool(m.get("open"))})

    def _take_front_locked(self, m: dict, now: float) -> bool:
        """Фронт — на ось; ответ — пора ли проверить здоровье (её делают вне замка)."""
        if not (_num(m.get("frames")) and _num(m.get("fed"))):
            self._counts["front_malformed"] += 1
            return False
        front = self._axis(m["frames"] * self._frame_s)
        self._front = front
        line = {"type": "front", "t": self._t(), "fed": m["fed"], "frames": m["frames"], "front": front}
        for key in ("cpu_s", "rss_mb"):
            if _num(m.get(key)):
                line[key] = m[key]
        if m.get("final"):
            line["final"] = True
        self._line(line)
        for key, entry in list(self._pending.items()):
            if entry["end"] <= front:
                del self._pending[key]
                self._resolve_locked(entry, now)
        self._evict_locked(now)
        self._seg_floor = max(self._seg_floor, front - round(SEG_KEEP_S * self._sr))
        while self._segs and self._segs[0][1] <= self._seg_floor:
            self._segs.popleft()
        if self._mem_checked is not None and now - self._mem_checked < PRESSURE_CHECK_S:
            return False
        self._mem_checked = now
        return True

    def _check_health(self, now: float, errlog: pathlib.Path | None) -> None:
        """Раз в `PRESSURE_CHECK_S`: строка `mem`; давление ≥ `PRESSURE_STOP` дважды подряд —
        аварийная остановка тени; журнал ребёнка больше `ERRLOG_CAP_BYTES` — тоже. `stat` и
        опрос памяти — вне замка (№533), решение по ним — под замком, если тень ещё жива."""
        size = 0
        if errlog is not None:
            try:
                size = errlog.stat().st_size
            except OSError:
                size = 0
        state = self._memory() if size <= ERRLOG_CAP_BYTES else None
        with self._lock:
            if self._state not in (LIVE, STOPPING):
                return
            if size > ERRLOG_CAP_BYTES:
                self._die_locked(f"журнал ребёнка вырос сверх {ERRLOG_CAP_BYTES // 2**20} МБ")
                return
            if state is None:
                return
            self._line({"type": "mem", "t": self._t(), **state})   # метка — в момент постановки: t в журнале монотонно
            self._mem_high = self._mem_high + 1 if state["pressure"] >= PRESSURE_STOP else 0
            if self._mem_high >= 2:
                self._die_locked(f"давление памяти (уровень {state['pressure']}) — тень уступает встрече")

    # ------------------------------------------------------------ чанки

    def note_chunk(self, placed: typing.Any, state: str) -> None:
        """Чанк, принятый распознаванием, и что с ним сделал живой трекер.

        Не ждёт: строка журнала ложится сразу (чанк до старта потока, поток мёртв,
        фронт уже прошёл) или когда фронт пройдёт конец чанка. Чужой канал — мимо."""
        try:
            label, number = placed.seq
            if label != CHANNEL:
                return
            if state not in CHUNK_STATES:
                self._fault("чанк", ValueError(f"состояние чанка {state!r} вне {CHUNK_STATES}"))
                state = UNKNOWN_STATE
            now = self._clock()
            start = int(placed.start)
            entry = {"chunk": number, "start": start, "end": start + len(placed.chunk),
                     "state": state, "noted": now}
            with self._lock:
                entry["behind_s"] = (None if self._front is None
                                     else round((entry["end"] - self._front) / self._sr, 3))
                if self._state == DEAD:
                    self._chunk_line_locked(entry, DEAD_STREAM, now, reason=self._reason)
                elif self._start0 is None or start < self._start0:
                    self._chunk_line_locked(entry, BEFORE_STREAM, now)
                elif self._front is not None and entry["end"] <= self._front:
                    self._resolve_locked(entry, now)
                else:
                    self._pending[(label, number)] = entry
                    self._evict_locked(now)
        except Exception as e:  # noqa: BLE001 — тень не смеет ронять распознавание; след — в журнале и строке
            self._fault("чанк", e)
        finally:
            self._drain()

    def _resolve_locked(self, entry: dict, now: float) -> None:
        start, end = entry["start"], entry["end"]
        if start < self._seg_floor:                 # сегменты этого места уже выброшены
            self._chunk_line_locked(entry, LATE, now)
            return
        slots: dict[str, float] = {}
        for s, e, slot in self._segs:
            overlap = min(e, end) - max(s, start)
            if overlap > 0:
                slots[str(slot)] = round(slots.get(str(slot), 0.0) + overlap / self._sr, 3)
        self._chunk_line_locked(entry, LABELED, now, slots=slots)

    def _evict_locked(self, now: float) -> None:
        for key, entry in list(self._pending.items()):
            if now - entry["noted"] > PENDING_CAP_S or len(self._pending) > PENDING_MAX:
                del self._pending[key]
                self._chunk_line_locked(entry, TIMEOUT, now)
            else:
                break                       # порядок вставки — порядок возраста

    def _finish_pending_locked(self, outcome: str, reason: str = "") -> None:
        now = self._clock()
        for entry in self._pending.values():
            self._chunk_line_locked(entry, outcome, now, reason=reason)
        self._pending.clear()

    def _chunk_line_locked(self, entry: dict, outcome: str, now: float, *,
                           slots: dict | None = None, reason: str = "") -> None:
        self._counts[f"chunk_{outcome}"] += 1
        line = {"type": "chunk", "t": self._t(now), "chunk": entry["chunk"], "start": entry["start"],
                "end": entry["end"], "state": entry["state"], "outcome": outcome,
                "wait_s": round(now - entry["noted"], 3), "behind_s": entry["behind_s"]}
        if slots is not None:
            line["slots"] = slots
        if reason:
            line["reason"] = reason
        self._line(line)

    # ------------------------------------------------------------ конец

    def stop(self) -> None:
        """Штатная остановка без ожидания: ребёнку EOF, через `STOP_GRACE_S` — убийство.
        Журнал не дописывает: `_drain` ждёт диска, а главная нить демона после `stop` сразу
        запускает пересборку; допишут писатель, читатель и `close`."""
        with self._lock:
            if self._state in (STOPPING, DEAD):
                return
            self._state = STOPPING
            self._stopped_at = self._clock()
            self._cancel.set()
            self._queue.put(None)
        try:
            threads.timer(STOP_GRACE_S, self._kill_after_grace, name="nemotron-live-kill", role="audio",
                          detached="уборка ребёнка тени после стопа не держит выход демона")
        except RuntimeError:                  # нить не завелась — без отсрочки
            self._kill_after_grace()

    def _kill_after_grace(self) -> None:
        """Отсрочка вышла: живой ребёнок — SIGKILL без ожидания (выход дождётся `finish` в
        конце потока); в главной нити демона сюда приходят, если таймер не завёлся."""
        with self._lock:
            stream = self._stream
            if stream is None or not stream.alive():
                return
            self._counts["killed_after_grace"] += 1
        stream.kill_nowait()

    def _on_eof(self) -> None:
        """Ребёнок закрыл вывод: штатный конец после стопа или смерть. Граница обратного
        вызова: сбой — смерть тени со строкой `end`."""
        try:
            self._on_eof_inner()
        except Exception as e:  # noqa: BLE001 — граница обратного вызова тени: сбой становится смертью
            self._die_from_thread(f"конец потока не разобран: {type(e).__name__}: {e}")
        finally:
            self._drain()

    def _on_eof_inner(self) -> None:
        stream = self._stream
        exit_ = stream.finish(STOP_GRACE_S) if stream is not None else None
        with self._lock:
            if stream is not None:
                self._counts["nonjson"] = stream.nonjson
                self._counts["callback_errors"] = stream.callback_errors
            if self._state == STOPPING:
                self._end_locked("остановлен", exit_)
            elif self._state != DEAD:
                why = exit_.reason if exit_ is not None and exit_.reason else "вышел"
                self._die_locked(f"ребёнок закрыл вывод ({why})", exit_)

    def _die_locked(self, reason: str, exit_: foreign_python.Outcome | None = None) -> None:
        if self._state == DEAD:
            return
        self._finish(DEAD_STREAM, reason, exit_)
        self._death_said = reason          # нить «после смерти» заведёт _drain — не под замком (№533)

    def _end_locked(self, reason: str, exit_: foreign_python.Outcome | None = None) -> None:
        self._finish(STOPPED, reason, exit_)

    def _finish(self, outcome: str, reason: str, exit_: foreign_python.Outcome | None) -> None:
        """Единственный переход в DEAD — и он же владеет ребёнком: живой получает SIGKILL здесь,
        на любом пути (смерть, штатный конец, сбой нити в остановке), без нити и без ожидания
        (выходной круг 2 по №478 A2, I1)."""
        self._state, self._reason = DEAD, reason
        self._reap_locked()
        self._cancel.set()
        self._finish_pending_locked(outcome, reason)
        line = {"type": "end", "t": self._t(), "reason": reason, "counts": dict(self._counts)}
        if exit_ is not None:
            line["exit"] = exit_.kind
            if exit_.reason:
                line["exit_reason"] = exit_.reason
        self._line(line)
        # Будить ждущих — последним: проснувшийся писатель и `close` дописывают журнал, и
        # строки конца к этому моменту уже в очереди (выходной круг 1 по №533, I1).
        self._queue.put(None)
        self._dead.set()

    def _reap_locked(self) -> None:
        """Живой ребёнок — SIGKILL без ожидания. Вход не закрывается: им владеет писатель, и
        его следующая запись в трубу убитого получит BrokenPipeError."""
        stream = self._stream
        if stream is not None and stream.alive():
            stream.kill_nowait()

    def _after_death(self, reason: str) -> None:
        stream = self._stream
        if stream is not None and stream.alive():
            stream.kill()
        self._say(f"поток Nemotron (тень) остановлен: {reason}")

    def _die_from_thread(self, reason: str) -> None:
        """Смерть из нити тени по сбою вне замка: под замком, один раз; строки — на диск."""
        with self._lock:
            if self._state == STOPPING:
                self._end_locked(reason)
            else:
                self._die_locked(reason)
        self._drain()

    def close(self, timeout: float) -> None:
        """Выход демона: остановить, если не остановлен; дать тени кончиться штатно не
        дольше `CLOSE_WAIT_S` (и не дольше остатка отсрочки от `stop()`); не кончилась —
        убить ребёнка сейчас, не дожидаясь таймера отсрочки, и ждать строку `end` ещё
        `CLOSE_MARGIN_S`; затем дописать журнал. Ожидания ограничены `timeout`, запись
        журнала — нет: на повисшем диске демона добьёт сторож приложения, ребёнок к этому
        моменту уже убит (№533)."""
        try:
            self.stop()
            with self._lock:
                since = self._stopped_at if self._stopped_at is not None else self._clock()
            deadline = time.monotonic() + max(0.0, timeout)
            left = min(CLOSE_WAIT_S, since + STOP_GRACE_S - self._clock())
            if not self._dead.wait(max(0.0, min(left, deadline - time.monotonic()))):
                with self._lock:
                    stream = self._stream
                    if stream is not None and stream.alive():
                        self._counts["killed_at_close"] += 1
                    else:
                        stream = None
                if stream is not None:
                    stream.kill_nowait()
                self._dead.wait(max(0.0, min(CLOSE_MARGIN_S, deadline - time.monotonic())))
        finally:
            self._drain()

    def _say_async(self, text: str) -> None:
        """Строка человеку — своей нитью: `say` демона пишет в трубу приложения, и нить
        захвата или распознавания на ней не стоит (выходной круг 1 по №478 A2, M2)."""
        try:
            threads.spawn(self._say, name="nemotron-live-say", role="audio", args=(text,),
                          detached="строка человеку не держит ни захват, ни распознавание")
        except RuntimeError:
            pass                           # нить не завелась — строка не важнее захвата

    # ------------------------------------------------------------ журнал

    def _t(self, now: float | None = None) -> float:
        return round((self._clock() if now is None else now) - self._t0, 3)

    def _line(self, obj: dict) -> None:
        """Строка журнала — в очередь (под замком состояния: порядок строк = порядок
        изменений). Мёртвый журнал очередь не копит: её чистит `_drain`, а после смерти
        строки кладут только входы, которые сами зовут `_drain`."""
        self._out.append(obj)

    def _drain(self) -> None:
        """Строки из очереди — на диск, по порядку, одним писателем за раз; вне замка
        состояния и без вложенных замков. Сбой записи любого рода — смерть журнала и тени
        (под замком состояния, уже отпустив замок журнала). Затем — нить «после смерти»,
        если смерть была: её не заводят под замком. Не бросает."""
        failure: Exception | None = None
        with self._journal_lock:
            while self._journal is not None:
                try:
                    obj = self._out.popleft()
                except IndexError:
                    break
                try:
                    self._journal.write(json.dumps(obj, ensure_ascii=False) + "\n")
                except Exception as e:  # noqa: BLE001 — любой сбой записи — смерть журнала, а не исключение входу
                    failure, self._journal = e, None     # дальше писать некуда
            if self._journal is None:
                self._out.clear()                        # журнал мёртв — очередь не копится
        with self._lock:
            if failure is not None and self._state != DEAD:
                self._die_locked(f"журнал тени не пишется ({failure})")
            reason, self._death_said = self._death_said, None
        if reason is not None:
            try:
                threads.spawn(self._after_death, name="nemotron-live-death", role="audio", args=(reason,),
                              detached="ожидание выхода ребёнка и строка человеку не держат ни захват, ни выход")
            except RuntimeError:
                pass                       # ребёнок уже убит в _finish; теряется только строка человеку

    def _fault(self, where: str, e: BaseException) -> None:
        with self._lock:
            self._counts[f"fault_{where}"] += 1
            first = self._counts[f"fault_{where}"] == 1
        if first:
            self._say_async(f"поток Nemotron (тень): сбой ({where}): {type(e).__name__}: {e}")

    # ------------------------------------------------------------ для тестов и сводки

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason


def diarized_state(split_failed: bool, jobs: typing.Any) -> str:
    """Что живой трекер сделал с чанком на ветке раскладки: раскладка упала, вся речь
    исключена (`jobs` — None) или разложил по кускам. Решение здесь, а не условием в
    замыкании демона: там его не видел ни один тест."""
    if split_failed:
        return "split_failed"
    return "none" if jobs is None else "pieces"


class _NoShadow:
    """Тени нет (выключена или не поднялась): те же методы, что у `Shadow`, и ничего не
    делают. Решение «есть ли тень» принимает `start` один раз — демону не нужны проверки
    на None в каждой точке вызова (мутатор диапазона: их не видел ни один тест)."""

    def attach(self, hub: typing.Any) -> None:
        pass

    def on_frame(self, label: str, start: int, part: typing.Any) -> None:
        pass

    def note_chunk(self, placed: typing.Any, state: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self, timeout: float) -> None:
        pass


NO_SHADOW = _NoShadow()


def start(cfg: dict, *, root: pathlib.Path, stamp: str, sr: int, labels: typing.Collection[str],
          say: typing.Callable[[str], None],
          memory: typing.Callable[[], dict | None] = memory_state) -> Shadow | _NoShadow:
    """Поднять тень, если её просит `sufler.live_nemotron`; иначе — `NO_SHADOW` и строка, почему.

    `labels` — метки захватов хаба: без канала собеседников ребёнок держал бы модель
    весь звонок впустую. Давление памяти уже на уровне `PRESSURE_STOP` — тень не
    стартует: опыт 29.09 показал цену потока именно в памяти (выходной круг 1 по №478
    A2, M3 и критика 1)."""
    sufler = cfg.get("sufler") or {}
    raw = sufler.get("live_nemotron")
    if raw is None or raw is False:            # YAML читает голое off как false
        mode = OFF
    elif raw is True:                          # …а голое on — как true
        mode = "on"
    else:
        mode = str(raw).strip().lower() or OFF
    if mode == OFF:
        return NO_SHADOW
    if mode != SHADOW:
        say(f"sufler.live_nemotron: режим {mode!r} неизвестен ({', '.join(MODES)}) — поток Nemotron выключен")
        return NO_SHADOW
    if sr != diarize_nemotron.SAMPLE_RATE:
        say(f"поток Nemotron выключен: хаб пишет {sr} Гц, движку нужно {diarize_nemotron.SAMPLE_RATE}")
        return NO_SHADOW
    if CHANNEL not in labels:
        say("поток Nemotron выключен: канала собеседников в захвате нет")
        return NO_SHADOW
    state = memory()
    if state is not None and state["pressure"] >= PRESSURE_STOP:
        say(f"поток Nemotron выключен: давление памяти уже на уровне {state['pressure']}")
        return NO_SHADOW
    python, refusal = diarize_nemotron.engine_interpreter(str(sufler.get("nemotron_python") or ""), root)
    if refusal:
        say(f"поток Nemotron выключен: {refusal}")
        return NO_SHADOW
    journal = charoite_paths.meeting_log(root, "nemotron_live", stem=stamp, suffix=".jsonl")
    try:
        shadow = Shadow(journal=journal, sr=sr, stamp=stamp, say=say, memory=memory)
    except OSError as e:
        say(f"поток Nemotron выключен: журнал не открылся ({e})")
        return NO_SHADOW
    shadow.begin(python=python, script=diarize_nemotron.SCRIPT,
                 args=["--stream", "--model", str(diarize_nemotron.model_dir(root)), "--preset", PRESET],
                 errlog=charoite_paths.meeting_log(root, "nemotron_live", stem=stamp, suffix=".err"))
    say(f"поток Nemotron: тень включена, журнал {journal.name}")
    return shadow
