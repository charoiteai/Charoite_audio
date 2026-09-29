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
- Чанк распознавания (`note_chunk`) ждёт, пока фронт потока пройдёт его конец, — без
  ожидания в потоке STT: строка журнала ложится, когда метка готова. Строка — на
  КАЖДЫЙ принятый чанк: метка готова, чанк до старта потока, поток мёртв, не
  дождался за `PENDING_CAP_S`, остановка.

Журнал — `logs/nemotron_live_<штамп>.jsonl`, только владельцу: числа (сэмплы, слоты,
секунды) и причины остановки — ни звука, ни текста реплик. Строки: `header` (что известно до
ребёнка), `ready` (рукопожатие), `start` (`start0` — с него сверка режет `.wav`),
`seg` и `front` (поток на оси хаба; во фронте — CPU-секунды и пик памяти ребёнка),
`chunk` (исход каждого чанка), `end` (причина конца и счётчики). Журнал открыт до
конца процесса: чанк, принятый распознаванием после конца потока, тоже получает
строку — она ложится после `end`.
"""
from __future__ import annotations

import collections
import json
import math
import os
import pathlib
import queue
import threading
import time
import typing

import audio
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

#: Что живой трекер сделал с чанком — закрытый набор, пишет демон: разгрузка очереди,
#: раскладка по кускам, вся речь исключена, раскладка упала, трекера раскладки нет.
CHUNK_STATES = ("shed", "pieces", "none", "split_failed", "off")

#: Исходы чанка в журнале.
LABELED, BEFORE_STREAM, DEAD_STREAM, TIMEOUT, STOPPED, LATE = (
    "labeled", "before_stream", "dead", "timeout", "stopped", "late")

STARTING, LIVE, STOPPING, DEAD = "starting", "live", "stopping", "dead"


def _num(x: typing.Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _open_private(path: pathlib.Path) -> typing.TextIO:
    """Журнал только владельцу: режим при создании и `fchmod` для файла, который был."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", buffering=1, encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


class Shadow:
    """Тень одной встречи. Замок один на состояние, очередь ожидающих и журнал; под ним
    не ждут ни процесса, ни трубы, ни чужого кода — убийство ребёнка и строка человеку
    уходят в свою нить."""

    def __init__(self, *, journal: pathlib.Path, sr: int, stamp: str,
                 say: typing.Callable[[str], None],
                 clock: typing.Callable[[], float] = time.monotonic):
        self._sr = sr
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
        self._journal: typing.TextIO | None = _open_private(journal)
        self._line({"type": "header", "v": JOURNAL_V, "sr": sr, "channel": CHANNEL,
                    "preset": PRESET, "stamp": stamp})

    # ------------------------------------------------------------ запуск

    def begin(self, *, python: str, script: pathlib.Path, args: typing.Sequence[str],
              errlog: pathlib.Path,
              spawn: typing.Callable[..., typing.Any] = foreign_python.spawn_stream) -> None:
        """Запустить ребёнка своей нитью: рукопожатие — до `HANDSHAKE_S`, встреча не ждёт."""
        threads.spawn(self._start, name="nemotron-live-start", role="audio",
                      args=(python, script, list(args), errlog, spawn))

    def _start(self, python, script, args, errlog, spawn) -> None:
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
        threads.spawn(self._write_loop, name="nemotron-live-writer", role="audio")

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
        """Ребёнок поднялся, а брать его нельзя: закрыть вход, убить вне замка."""
        stream.close_input()
        if self._state == STOPPING:
            self._end_locked(reason)
        else:
            self._die_locked(reason)
        threads.spawn(stream.kill, name="nemotron-live-abandon", role="audio",
                      detached="убийство неподобранного ребёнка не держит ни захват, ни выход")

    # ------------------------------------------------------------ звук

    def on_frame(self, label: str, start: int, part: typing.Any) -> None:
        """Слушатель кадров хаба: блок канала собеседников — в очередь к ребёнку."""
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
        """Нить-писатель: блоки — в трубу ребёнка; сентинель — закрыть вход (EOF ребёнку)."""
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
                with self._lock:
                    if self._state == LIVE:
                        self._die_locked(f"вход ребёнка закрыт ({e})")
                break
        stream.close_input()

    # ------------------------------------------------------------ протокол

    def _on_message(self, message: dict) -> None:
        """Нить-читатель двери: сегменты и фронт — на ось хаба и в журнал."""
        now = self._clock()
        with self._lock:
            if self._state not in (LIVE, STOPPING) or self._start0 is None:
                self._counts["message_out_of_state"] += 1
                return
            kind = message.get("type")
            if kind == "seg":
                self._take_segment_locked(message)
            elif kind == "front":
                self._take_front_locked(message, now)
            else:
                self._counts["message_unknown"] += 1

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

    def _take_front_locked(self, m: dict, now: float) -> None:
        if not (_num(m.get("frames")) and _num(m.get("fed"))):
            self._counts["front_malformed"] += 1
            return
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
        floor = front - round(SEG_KEEP_S * self._sr)
        if floor > self._seg_floor:
            self._seg_floor = floor
            while self._segs and self._segs[0][1] <= floor:
                self._segs.popleft()

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
                raise ValueError(f"состояние чанка {state!r} вне {CHUNK_STATES}")
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
        """Штатная остановка без ожидания: ребёнку EOF, через `STOP_GRACE_S` — убийство."""
        with self._lock:
            if self._state in (STOPPING, DEAD):
                return
            self._state = STOPPING
            self._cancel.set()
            self._queue.put(None)
        try:
            threads.timer(STOP_GRACE_S, self._kill_after_grace, name="nemotron-live-kill", role="audio",
                          detached="уборка ребёнка тени после стопа не держит выход демона")
        except RuntimeError:                  # нить не завелась — без отсрочки
            self._kill_after_grace()

    def _kill_after_grace(self) -> None:
        with self._lock:
            stream = self._stream
            if self._state == DEAD or stream is None or not stream.alive():
                return
            self._counts["killed_after_grace"] += 1
        stream.kill()

    def _on_eof(self) -> None:
        """Ребёнок закрыл вывод: штатный конец после стопа или смерть."""
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
        threads.spawn(self._after_death, name="nemotron-live-death", role="audio",
                      args=(reason,), detached="убийство ребёнка тени не держит ни захват, ни выход")

    def _end_locked(self, reason: str, exit_: foreign_python.Outcome | None = None) -> None:
        self._finish(STOPPED, reason, exit_)

    def _finish(self, outcome: str, reason: str, exit_: foreign_python.Outcome | None) -> None:
        self._state, self._reason = DEAD, reason
        self._cancel.set()
        self._queue.put(None)
        self._finish_pending_locked(outcome, reason)
        line = {"type": "end", "t": self._t(), "reason": reason, "counts": dict(self._counts)}
        if exit_ is not None:
            line["exit"] = exit_.kind
            if exit_.reason:
                line["exit_reason"] = exit_.reason
        self._line(line)

    def _after_death(self, reason: str) -> None:
        stream = self._stream
        if stream is not None and stream.alive():
            stream.kill()
        self._say(f"поток Nemotron (тень) остановлен: {reason}")

    # ------------------------------------------------------------ журнал

    def _t(self, now: float | None = None) -> float:
        return round((self._clock() if now is None else now) - self._t0, 3)

    def _line(self, obj: dict) -> None:
        if self._journal is None:
            return
        try:
            self._journal.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except (OSError, ValueError) as e:
            self._journal = None                      # дальше писать некуда
            if self._state != DEAD:
                self._die_locked(f"журнал тени не пишется ({e})")

    def _fault(self, where: str, e: BaseException) -> None:
        with self._lock:
            self._counts[f"fault_{where}"] += 1
            first = self._counts[f"fault_{where}"] == 1
        if first:
            self._say(f"поток Nemotron (тень): сбой ({where}): {type(e).__name__}: {e}")

    # ------------------------------------------------------------ для тестов и сводки

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason


def start(cfg: dict, *, root: pathlib.Path, stamp: str, sr: int,
          say: typing.Callable[[str], None]) -> Shadow | None:
    """Поднять тень, если её просит `sufler.live_nemotron`; иначе — None и строка, почему."""
    sufler = cfg.get("sufler") or {}
    raw = sufler.get("live_nemotron")
    if raw is None or raw is False:            # YAML читает голое off как false
        mode = OFF
    elif raw is True:                          # …а голое on — как true
        mode = "on"
    else:
        mode = str(raw).strip().lower() or OFF
    if mode == OFF:
        return None
    if mode != SHADOW:
        say(f"sufler.live_nemotron: режим {mode!r} неизвестен ({', '.join(MODES)}) — поток Nemotron выключен")
        return None
    if sr != diarize_nemotron.SAMPLE_RATE:
        say(f"поток Nemotron выключен: хаб пишет {sr} Гц, движку нужно {diarize_nemotron.SAMPLE_RATE}")
        return None
    python, refusal = diarize_nemotron.engine_interpreter(str(sufler.get("nemotron_python") or ""), root)
    if refusal:
        say(f"поток Nemotron выключен: {refusal}")
        return None
    logs = root / "logs"
    try:
        shadow = Shadow(journal=logs / f"nemotron_live_{stamp}.jsonl", sr=sr, stamp=stamp, say=say)
    except OSError as e:
        say(f"поток Nemotron выключен: журнал не открылся ({e})")
        return None
    shadow.begin(python=python, script=diarize_nemotron.SCRIPT,
                 args=["--stream", "--model", str(diarize_nemotron.model_dir(root)), "--preset", PRESET],
                 errlog=logs / f"nemotron_live_{stamp}.err")
    say(f"поток Nemotron: тень включена, журнал nemotron_live_{stamp}.jsonl")
    return shadow
