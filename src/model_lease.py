"""Аренда модели у клиента: «наша генерация в полёте» как факт, а не догадка.

Локальный сервер моделей обслуживает несколько наших процессов разом (демон
встречи, разбор графа, ночная пересборка), и решение о его перезапуске
принимал `llm_health` по времени: сервер на связи, проба не ответила за
таймаут → грейс 180 с → перезапуск. Грейс спасал очередь из одной работы;
разбор графа — очередь из нескольких длинных вызовов, проба не пробивалась,
и перезапуск убивал ту работу, ради которой грейс завели (12.08).

Здесь клиент сам говорит, что занят: на время запроса к модели он держит
файл-аренду под `flock`. Ядро снимает аренду в момент смерти процесса,
поэтому pid, mtime и протухание «по часам» не нужны — от них проект уже
отказался письменно (busy_signals: pid+mtime+STALE дал три дыры, flock
закрывает класс). Остаётся один честный вопрос — «висит ли живая аренда без
прогресса», и на него отвечает `deadline`: у не-стрима — бюджет запроса, у
стрима — скользящий, катится на каждом байте (входной круг DS и GLM по
№264, 18.09: файл на pid ломался многопоточностью демона, `os.kill(pid, 0)`
переоткрывал pid-reuse, `started + timeout` лгал для стримов).

Пишет аренду только транспорт (`llm._open_stream`, `llm._post_busy`);
читает только решающий (`llm_health`). Файл — на ВЫЗОВ (`<pid>-<id>.json`),
не на процесс: демон гоняет прогрев, подсказки и облачную доводку из разных
нитей одного pid. Очередь за занятым сервером аренду не держит: она
самозалечивается ретраями, а на висящем сервере держала бы перезапуск
навсегда. Исключения, идущие к модели мимо швов: `llm.embed` (0,2 с, убить
не жалко) и сама проба `llm_health.probe` — тот, кто спрашивает.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pathlib
import time
import uuid

LEASE_DIR = pathlib.Path("data") / "llm_inflight"
#: Стрим без байта дольше этого — зависание, а не работа. Больше read-таймаута
#: документных стримов (llm.DOC_STREAM_TIMEOUT[1] = 300): префилл 25-тысячного
#: куска на холодной модели честно молчит до пяти минут, и порог меньше
#: убивал бы живую работу (тест пиннит пару с DOC_STREAM_TIMEOUT).
STALL_S = 330.0
#: Не чаще этого перезаписываем файл при прогрессе: читатель смотрит раз в
#: RESTART_POLL = 5 с, писать на каждом чанке незачем.
PROGRESS_EVERY_S = 5.0


def lease_dir(root: pathlib.Path) -> pathlib.Path:
    return root / LEASE_DIR


class Lease:
    """Одна аренда на один запрос к модели — контекстный менеджер.

    `budget` — секунды до `deadline` у не-стрима (read-таймаут запроса);
    None — стрим: `deadline` скользит на `progress()`, начальный — STALL_S
    (префилл). Файл открыт и заперт `flock(LOCK_EX)` всю жизнь аренды:
    закрытие fd или смерть процесса освобождает его ядром. Ошибки файловой
    системы аренду не роняют — генерация важнее своего же маркера.
    """

    def __init__(self, root: pathlib.Path, *, server: str, engine: str, kind: str,
                 budget: float | None = None) -> None:
        self.root = pathlib.Path(root)
        self.server = server                  # адрес сервера моделей, как его знает транспорт
        self.engine = engine
        self.kind = kind
        self.budget = budget
        self.path: pathlib.Path | None = None
        self._f = None
        self._written = 0.0
        self.started = 0.0
        self.deadline = 0.0

    def __enter__(self) -> "Lease":
        self.started = time.time()
        self.deadline = self.started + (self.budget if self.budget is not None else STALL_S)
        try:
            d = lease_dir(self.root)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
            f = path.open("w")
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)   # свой новый файл: берётся всегда
            os.chmod(path, 0o600)
            self._f, self.path = f, path
            self._write(force=True)
        except OSError:
            self._drop()
        return self

    def progress(self) -> None:
        """Байт пришёл: стрим живёт — deadline катится вперёд на STALL_S.
        У не-стрима бюджет фиксирован, прогресса у него нет."""
        if self._f is None or self.budget is not None:
            return
        self.deadline = time.time() + STALL_S
        self._write()

    def _write(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._written < PROGRESS_EVERY_S:
            return
        try:
            self._f.seek(0)
            self._f.truncate()
            json.dump({"pid": os.getpid(), "server": self.server, "engine": self.engine,
                       "kind": self.kind, "started": self.started, "deadline": self.deadline},
                      self._f)
            self._f.flush()
            self._written = now
        except OSError:
            pass

    def _drop(self) -> None:
        f, path = self._f, self.path
        self._f, self.path = None, None
        if f is not None:
            with contextlib.suppress(OSError):
                f.close()                     # снимает flock
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()

    def __exit__(self, *exc) -> None:
        self._drop()


def live(root: pathlib.Path, *, server: str | None = None,
         now: float | None = None) -> list[dict]:
    """Аренды, которые сейчас держат живые процессы — на этот сервер.

    Каждая запись — dict аренды плюс `stalled`: deadline прошёл (стрим без
    байта дольше STALL_S или не-стрим дольше своего бюджета) — работа висит,
    её не щадят. Файл, чей flock берётся, — сирота (процесс умер, unlink не
    успел) — убирается. Битый JSON, чужой сервер — пропуск: мусор в
    служебной папке не должен ронять разбор встречи.
    """
    now = time.time() if now is None else now
    out: list[dict] = []
    d = lease_dir(root)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for p in files:
        try:
            f = p.open("r+")
        except OSError:
            continue
        with f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass                          # держат — живой владелец
            except OSError:
                continue                      # ФС без flock — судить не по чему
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    p.unlink()               # сирота: процесс умер, файл остался
                continue
            try:
                info = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(info, dict):
                continue
        if server is not None and info.get("server") != server:
            continue                          # аренда на другой сервер — не наш перезапуск
        try:
            deadline = float(info.get("deadline", 0.0))
        except (TypeError, ValueError):
            continue
        info["stalled"] = deadline < now
        info["path"] = str(p)
        out.append(info)
    return out


def describe(leases: list[dict], *, now: float | None = None) -> str:
    """Строка для лога: чью работу ждём."""
    now = time.time() if now is None else now
    parts = []
    for info in leases:
        age = max(0, int(now - float(info.get("started", now))))
        parts.append(f"pid {info.get('pid')} ({info.get('kind', '?')}, {info.get('engine', '?')}, идёт {age} с"
                     + (", висит" if info.get("stalled") else "") + ")")
    return "; ".join(parts) or "никого"
