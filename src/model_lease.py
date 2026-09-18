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
прогресса», и на него отвечает `deadline`: последний момент, когда работа
точно жила, плюс порог зависания, выведенный из read-таймаута того же
запроса (входной круг DS и GLM по №264, 18.09: файл на pid ломался
многопоточностью демона, `os.kill(pid, 0)` переоткрывал pid-reuse,
`started + timeout` лгал для стримов).

Протокол файла — один, и он принадлежит писателю (выходной круг DS C1/C2,
GLM I1/I2): аренда НИКОГДА не видна в каталоге без замка и НИКОГДА не
переписывается на месте. Каждая публикация — новый inode под временным
именем вне маски читателя, `flock`, полный JSON, `os.replace` поверх имени;
rename сохраняет inode и замок. Читатель судит о живости ТОЛЬКО по замку:
файл под замком — живой владелец, даже если содержимое не прочиталось;
файл без замка — сирота, её не считают. Уборкой сирот занимается писатель
(следующее взятие аренды), а не читатель: путь решения о kill не должен
иметь прав уничтожителя.

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
#: Запас поверх read-таймаута запроса: транспорт сам обрывает молчащий запрос
#: по своему таймауту и снимает аренду, поэтому «висит» для читателя — это
#: молчание дольше того таймаута плюс запас на троттлинг записи (5 с), опрос
#: читателя (5 с) и сам обрыв.
STALL_MARGIN_S = 30.0
#: Read-таймаут по умолчанию, когда транспорт его не назвал (timeout=None):
#: тот же, что у документных стримов llm.DOC_STREAM_TIMEOUT[1].
DEFAULT_READ_S = 300.0
#: Не чаще этого публикуем прогресс: читатель смотрит раз в RESTART_POLL = 5 с.
PROGRESS_EVERY_S = 5.0
#: Файл без замка младше этого не трогаем: страховка на случай, если протокол
#: публикации когда-нибудь снова даст окно «видим, но не заперт».
ORPHAN_GRACE_S = 60.0
_TMP_SUFFIX = ".tmp"          # временные имена не попадают под маску читателя *.json


def lease_dir(root: pathlib.Path) -> pathlib.Path:
    return root / LEASE_DIR


def stall_for(read_timeout: float | None) -> float:
    """Порог зависания из read-таймаута запроса: транспорт молчание дольше
    таймаута обрывает сам, порог читателя стоит чуть выше — иначе живой
    префилл документа (молчит до таймаута) сочли бы зависанием."""
    read = DEFAULT_READ_S if read_timeout is None else float(read_timeout)
    return read + STALL_MARGIN_S


class Lease:
    """Одна аренда на один запрос к модели — контекстный менеджер.

    `read_timeout` — read-таймаут запроса у транспорта; из него выводится
    порог зависания `stall`. `deadline = <последний момент жизни> + stall`:
    при взятии — старт, у стрима каждый байт (`progress()`) катит его вперёд;
    у не-стрима прогресс не наблюдаем, и deadline — старт плюс read-таймаут
    плюс запас: дольше живёт только запрос, которому транспорт что-то
    читает. Файл заперт `flock(LOCK_EX)` всю жизнь аренды: закрытие fd или
    смерть процесса освобождает его ядром. Ошибки файловой системы аренду не
    роняют — генерация важнее своего же маркера.
    """

    def __init__(self, root: pathlib.Path, *, server: str, engine: str, kind: str,
                 read_timeout: float | None = None) -> None:
        self.root = pathlib.Path(root)
        self.server = server                  # адрес сервера моделей, как его знает транспорт
        self.engine = engine
        self.kind = kind
        self.stall = stall_for(read_timeout)
        self.path: pathlib.Path | None = None
        self._f = None
        self._written = 0.0
        self.started = 0.0
        self.deadline = 0.0

    def __enter__(self) -> "Lease":
        self.started = time.time()
        self.deadline = self.started + self.stall
        try:
            d = lease_dir(self.root)
            d.mkdir(parents=True, exist_ok=True)
            self.path = d / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
            self._publish()
        except OSError:
            self._drop()
            return self
        self._sweep_orphans(d)
        return self

    def progress(self) -> None:
        """Байт пришёл: стрим живёт — deadline катится вперёд на порог зависания."""
        if self._f is None:
            return
        now = time.time()
        self.deadline = now + self.stall
        if now - self._written < PROGRESS_EVERY_S:
            return
        try:
            self._publish()
        except OSError:
            pass                              # прежняя публикация остаётся под замком

    def _publish(self) -> None:
        """Новый inode → flock → права → полный JSON → rename поверх имени.

        Ни один читатель не увидит `*.json` без замка (временное имя вне его
        маски) и не прочитает половину записи (файл на месте не меняется —
        подменяется целиком). Прежний fd закрывается только ПОСЛЕ rename:
        замок на имени не прерывается ни на миг."""
        assert self.path is not None
        tmp = self.path.with_name(f".{self.path.name}{_TMP_SUFFIX}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        f = os.fdopen(fd, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)   # свой новый файл: берётся всегда
            json.dump({"pid": os.getpid(), "server": self.server, "engine": self.engine,
                       "kind": self.kind, "started": self.started, "deadline": self.deadline},
                      f)
            f.flush()
            os.replace(tmp, self.path)
        except OSError:
            with contextlib.suppress(OSError):
                f.close()
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        old, self._f = self._f, f
        self._written = time.time()
        if old is not None:
            with contextlib.suppress(OSError):
                old.close()                   # прежний inode уже без имени — снимаем его замок

    @staticmethod
    def _sweep_orphans(d: pathlib.Path) -> None:
        """Уборка сирот — у писателя: файл без замка и старше ORPHAN_GRACE_S
        оставил умерший процесс. Читатель сирот только не считает."""
        try:
            files = list(d.iterdir())
        except OSError:
            return
        cutoff = time.time() - ORPHAN_GRACE_S
        for p in files:
            with contextlib.suppress(OSError):
                if p.stat().st_mtime > cutoff:
                    continue
                with p.open("r") as f:
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue              # держат — живой владелец
                    p.unlink()                # никем не заперт и не молод — сирота

    def _drop(self) -> None:
        f, path = self._f, self.path
        self._f, self.path = None, None
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()                 # сначала имя: читатель не увидит незапертый файл
        if f is not None:
            with contextlib.suppress(OSError):
                f.close()                     # снимает flock

    def __exit__(self, *exc) -> None:
        self._drop()


def live(root: pathlib.Path, *, server: str | None = None,
         now: float | None = None) -> list[dict]:
    """Аренды, которые сейчас держат живые процессы — на этот сервер.

    Живость решает ТОЛЬКО замок: файл, чей flock не берётся, — живой владелец;
    берётся — сирота (процесс умер, unlink не успел), её не считают и не
    трогают (уборка — у писателя). Содержимое решает только `stalled`
    (deadline прошёл — работа висит, её не щадят); замок есть, а JSON не
    читается — аренда живая с неизвестным deadline, не висящая: ложная
    «живая» стоит один цикл грейса, ложная «мёртвая» — убитую генерацию.
    Чужой сервер — пропуск. Инode под именем сверяется с открытым: файл,
    подменённый или снятый под рукой, перечитывается, а не судится по
    прежнему содержимому. Исключения ФС наружу: вызывающий различает
    «никого» и «не смог посмотреть».
    """
    now = time.time() if now is None else now
    out: list[dict] = []
    d = lease_dir(root)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        info = _read_held(p)
        if info is None:
            continue                          # сирота или файл исчез под рукой
        if not info:
            # замок держат, а полей нет: живой владелец, о котором известно
            # только это — ни сервер, ни deadline. Считаем живой и на ЭТОТ
            # сервер: фильтр по адресу отсеивать её не вправе, иначе порванная
            # запись выпадает из живых ровно там, где решают о kill
            out.append({"stalled": False, "unreadable": True, "path": str(p)})
            continue
        if server is not None and info.get("server") != server:
            continue                          # аренда на другой сервер — не наш перезапуск
        try:
            deadline = float(info["deadline"])
        except (KeyError, TypeError, ValueError):
            deadline = None                   # поля deadline нет: живая, висящей не считаем
        info["stalled"] = deadline is not None and deadline < now
        info["path"] = str(p)
        out.append(info)
    return out


def _read_held(p: pathlib.Path, attempts: int = 3) -> dict | None:
    """Содержимое аренды под чужим замком или None, если замок никто не держит.

    Открываем на чтение: flock не требует права записи, а аренда чужого
    пользователя должна быть видна. Inode после чтения сверяется с именем:
    писатель мог подменить файл публикацией — тогда читаем свежий."""
    for _ in range(attempts):
        try:
            f = p.open("r")
        except FileNotFoundError:
            return None
        with f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass                          # держат — живой владелец
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
                return None                   # никто не держит — сирота
            try:
                info = json.load(f)
            except ValueError:
                info = {}
            if not isinstance(info, dict):
                info = {}
            try:
                if os.fstat(f.fileno()).st_ino != p.stat().st_ino:
                    continue                  # подменён публикацией под рукой — перечитать
            except FileNotFoundError:
                return None                   # снят под рукой
            return info
    return {}                                 # замок держат, но подмены идут подряд: живая


def describe(leases: list[dict], *, now: float | None = None) -> str:
    """Строка для лога: чью работу ждём (адрес сервера в лог не идёт)."""
    now = time.time() if now is None else now
    parts = []
    for info in leases:
        age = max(0, int(now - float(info.get("started", now))))
        parts.append(f"pid {info.get('pid', '?')} ({info.get('kind', '?')}, {info.get('engine', '?')}, идёт {age} с"
                     + (", висит" if info.get("stalled") else "") + ")")
    return "; ".join(parts) or "никого"
