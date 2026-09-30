"""Скрипт продукта под чужим интерпретатором — одна дверь (№473, №475).

Зачем. Тяжёлый движок (сейчас Nemotron на mlx) живёт в своём окружении со своими
пакетами: в Python приложения его не поставить — бандл подписан, и mlx в нём нет.
Продукт зовёт СВОЙ скрипт процессом того интерпретатора и получает ответ
JSON-объектом в stdout.

Окружение ребёнка — рецепт пробы готовности (docs/ARCHITECTURE.md, «The readiness
probe does not trust the data folder»): ребёнок не подхватывает ни путей, ни
байткода, ни настроек родителя. Таблица `ISOLATION` — переменная и что с ней
делать, у каждой строки своя причина. Не `-I`: он подразумевает `-E` и глушит
заодно и то, что ребёнку нужно выставить. Проба готовности (Swift) и тестовая
проба пакета держат свои копии перечня — один источник для всех — №402.

pip ребёнка запускает только `run_pip` (№484): ему `-I` годится — выставлять
нечего, — а настройки самого pip гасят снятые `PIP_*`, таблица `PIP_ISOLATION` и
флаг `--isolated`.

Исход — значением (`Outcome`), не исключением: вызывающий обязан отличать «движка
нет на этой машине» (UNAVAILABLE — код `EXIT_ENGINE_UNAVAILABLE`) от «движок упал»
(FAILED — другой код, потолок времени, нет интерпретатора, ответ не JSON-объект).
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import subprocess
import threading
import time
import typing

from exit_codes import EXIT_ENGINE_UNAVAILABLE

#: Переменная окружения ребёнка → значение; None — снять.
ISOLATION: dict[str, str | None] = {
    # Текущий каталог не попадает в sys.path: подложенный рядом yaml.py не
    # исполнится с правами продукта и не сломает протокол JSON.
    "PYTHONSAFEPATH": "1",
    # Пользовательский site-packages (~/.local/lib) — не наш путь.
    "PYTHONNOUSERSITE": "1",
    # PYTHONPATH бьёт SAFEPATH: вставленный путь идёт впереди стандартных.
    "PYTHONPATH": None,
    # Подменяет всю стандартную библиотеку.
    "PYTHONHOME": None,
    # Файл, исполняемый при старте интерактивного режима, — снимается вместе с
    # остальными, как в рецепте пробы.
    "PYTHONSTARTUP": None,
    # Ребёнку байткод не нужен: разовый прогон компилирует в памяти.
    "PYTHONDONTWRITEBYTECODE": "1",
    # Снимается вместе с записью, по своей причине: иначе префикс становится
    # путём импорта с другой стороны — поддельный .pyc в доступном каталоге
    # кэша исполнится вместо модуля.
    "PYTHONPYCACHEPREFIX": None,
    # У чужого интерпретатора свой venv; переменная родителя указывает не туда.
    "VIRTUAL_ENV": None,
    # macOS: лаунчер venv передаёт через неё путь своего интерпретатора, и
    # ребёнок принимает чужой sys.executable, а с ним — чужой venv.
    "__PYVENV_LAUNCHER__": None,
    # macOS: тоже подменяет sys.executable ребёнка.
    "PYTHONEXECUTABLE": None,
    # Предупреждения-ошибки из окружения родителя роняли бы движок на чужом
    # DeprecationWarning стороннего пакета.
    "PYTHONWARNINGS": None,
    # Родитель читает stdout и stderr ребёнка как UTF-8.
    "PYTHONIOENCODING": "utf-8",
}


#: Виды исхода (`Outcome.kind`) — константы модуля: их читают как `fp.OK`. В теле
#: NamedTuple годится и голое имя (`OK = "ok"` — атрибут класса), а аннотированное
#: становится полем кортежа — под отложенными аннотациями и с `ClassVar` тоже; так
#: был устроен `live_sidecar.WriteOutcome` до №477, форму держит
#: `tests/test_namedtuple_fields.py`.
OK, UNAVAILABLE, FAILED = "ok", "unavailable", "failed"


class Outcome(typing.NamedTuple):
    """Исход запуска: `kind` — OK / UNAVAILABLE / FAILED; `payload` — ответ при OK;
    `reason` — строка для лога и шапки стенограммы при отказе."""
    kind: str
    payload: typing.Any = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == OK


def clean_env(base: typing.Mapping[str, str]) -> dict[str, str]:
    """Окружение ребёнка: `base` с применённой таблицей `ISOLATION`."""
    env = dict(base)
    for key, value in ISOLATION.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


#: Что pip ребёнка берёт у человека и что гасит дверь `run_pip` (№484, замер
#: pip 26.2 и 26.2.1). `-I` — изоляция Python, pip она не касается. `--isolated`
#: доходит только до подкоманды: главный разборщик pip читает `PIP_*` всегда, и
#: `PIP_PYTHON` человека перезапускал pip его интерпретатором, — поэтому все
#: переменные `PIP_*` снимаются по префиксу. Файл из `PIP_CONFIG_FILE`, глобальный
#: и `sys.prefix/pip.conf` гасит пустой файл настроек. `~/.netrc` pip читает и под
#: `--isolated`: запись `default` уходила заголовком Authorization на индекс; keyring
#: человека гасит `--no-input` двери. Прокси и сертификаты системы и окружения
#: остаются — адрес назначения они не меняют.
PIP_ISOLATION: dict[str, str] = {
    "PIP_CONFIG_FILE": os.devnull,
    "NETRC": os.devnull,
}


def pip_env(base: typing.Mapping[str, str] | None = None) -> dict[str, str]:
    """Окружение pip ребёнка: `clean_env`, без единой переменной `PIP_*` родителя
    (в любом регистре), плюс `PIP_ISOLATION`. `base` — по умолчанию `os.environ`."""
    env = {k: v for k, v in clean_env(os.environ if base is None else base).items()
           if not k.upper().startswith("PIP_")}
    return {**env, **PIP_ISOLATION}


def run_pip(python: str | os.PathLike, *args: str, base: typing.Mapping[str, str] | None = None,
            **run_kw: typing.Any) -> subprocess.CompletedProcess:
    """Запустить pip под интерпретатором `python` — единственный путь, которым продукт
    зовёт pip. Дверь запускает сама: пары argv и окружения наружу нет, и вызывающий не
    может взять одно без другого. `env` передать нельзя — дверь уже передаёт своё, и
    Python откажет дублем аргумента; остальное (`stdin`, `capture_output`, `timeout`…)
    уходит в `subprocess.run`."""
    # --no-input: на 401 pip иначе зовёт `keyring` из PATH человека (второе хранилище его учётных
    # данных рядом с ~/.netrc; провайдер keyring по умолчанию работает, только пока ввод разрешён)
    # и ждёт ввода с закрытого stdin (финальный круг №484, Opus M1; сторожит тест с 401)
    argv = [os.fspath(python), "-I", "-m", "pip", "--isolated", "--no-input", *args]
    return subprocess.run(argv, env=pip_env(base), **run_kw)


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_json(python: str | os.PathLike, script: pathlib.Path, args: typing.Sequence[str], *,
             timeout: float) -> Outcome:
    """Запустить `script` интерпретатором `python` в чистом окружении и прочесть
    JSON-объект из stdout.

    Скрипт — путём к файлу, рабочий каталог — каталог скрипта (код, а не данные).
    stdin закрыт: ребёнок, ждущий ввода, упрётся в потолок, а не повиснет.
    """
    cmd = [os.fspath(python), os.fspath(script), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              env=clean_env(os.environ), cwd=pathlib.Path(script).parent)
    except FileNotFoundError:
        return Outcome(FAILED, reason=f"нет интерпретатора {os.fspath(python)}")
    except subprocess.TimeoutExpired:
        return Outcome(FAILED, reason=f"не уложился в {timeout:.0f} с")
    except (OSError, ValueError) as e:       # ValueError — нулевой байт в пути из конфига
        return Outcome(FAILED, reason=f"не запустился: {e}")
    err = _last_line(proc.stderr.decode("utf-8", errors="replace"))
    if proc.returncode == EXIT_ENGINE_UNAVAILABLE:
        return Outcome(UNAVAILABLE, reason=err or "движок недоступен")
    if proc.returncode != 0:
        return Outcome(FAILED, reason=f"код {proc.returncode}: {err or 'без вывода'}")
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except ValueError as e:                 # битый UTF-8 или не JSON
        return Outcome(FAILED, reason=f"ответ не JSON: {e}")
    if not isinstance(payload, dict):
        return Outcome(FAILED, reason="ответ не JSON-объект")
    return Outcome(OK, payload=payload)


# ------------------------------------------------ долгий ребёнок (№478)

#: Режим журнала ребёнка и файлов его хозяина — только владельцу.
PRIVATE_MODE = 0o600
#: Сколько ждать выхода убитого ребёнка и ребёнка, закрывшего вывод без рукопожатия.
KILL_WAIT_S = 5.0
EXIT_WAIT_S = 5.0
#: Шаг опроса рукопожатия: отмена и потолок видны не позже чем через него.
HANDSHAKE_POLL_S = 0.2

#: Живые долгие дети процесса: pid → `Popen`. Ссылка сильная — хозяин, потерявший
#: `StreamProcess`, не прячет ребёнка от уборки при выходе (`Popen` при сборке
#: мусора ребёнка не убивает). Вышедшие вычищаются при следующей регистрации.
_children: dict[int, subprocess.Popen] = {}
_children_lock = threading.Lock()
_exiting = False                     # уборка при выходе уже сняла реестр — новых детей не выдаём


def _adopt(proc: subprocess.Popen) -> None:
    """Ребёнок `spawn_stream` — в реестр процесса, который уборка при выходе убивает.

    Смерть долгого ребёнка при выходе родителя держится здесь, а не у хозяина: выход
    мимо его `finally` (исключение до `try`, `sys.exit`, SIGTERM с обработчиком) проходит
    через `atexit` (выход по №533). SIGKILL родителя `atexit` не видит — №540. Процесс
    уже выходит (реестр снят) — `RuntimeError`: ребёнка, которого уборка не увидит, убивает
    граница `spawn_stream` (финальный Opus по №533, M1)."""
    with _children_lock:
        if _exiting:
            raise RuntimeError("процесс выходит — долгих детей не заводим")
        for pid, known in list(_children.items()):
            if known.poll() is not None:
                del _children[pid]
        _children[proc.pid] = proc


def _kill_children() -> None:
    """Выход процесса: живым детям — SIGKILL без ожидания. Снимок реестра — под его
    замком, но с потолком: нить-демон, застрявшая в `_adopt` на выходе, не держит выход.
    После снимка `_adopt` отказывает: ребёнок нити-демона, запущенный позже, не проскочит."""
    global _exiting
    locked = _children_lock.acquire(timeout=1.0)
    try:
        _exiting = True
        children: list = []
        for _ in range(3):                 # без замка словарь может меняться под снимком
            try:
                children = list(_children.values())
                break
            except RuntimeError:
                continue
    finally:
        if locked:
            _children_lock.release()
    for proc in children:
        try:
            proc.kill()
        except OSError:
            pass


atexit.register(_kill_children)       # при импорте: ребёнок, пришедший уже на выходе, не взводит хук сам


class StreamProcess:
    """Долгий ребёнок в своём окружении: звук — на stdin, протокол — JSON-строками
    на stdout, журнал — stderr в файл владельцу.

    stdin без буфера: `write` уходит в трубу сразу, `close_input` — голое закрытие
    дескриптора, которому нечего дописывать и не на чем встать. Писать и закрывать —
    одной нитью владельца. Строки протокола читает своя нить и отдаёт в
    `on_message`; конец stdout ребёнка — `on_eof()`. Строка не JSON-объект —
    счётчик `nonjson`; исключение обратного вызова не рвёт чтение: счётчик
    `callback_errors` и текст первого в `callback_error`."""

    def __init__(self, proc: subprocess.Popen, stderr_path: pathlib.Path | None = None):
        self._proc = proc
        self._stderr_path = stderr_path
        self.ready: dict = {}
        self.nonjson = 0
        self.callback_errors = 0
        self.callback_error = ""
        self._abandoned = False

    @property
    def pid(self) -> int:
        return self._proc.pid

    def alive(self) -> bool:
        return self._proc.poll() is None

    def write(self, data: bytes) -> None:
        """Отдать звук ребёнку целиком; мёртвая труба — OSError вызывающему."""
        pipe = self._proc.stdin
        if pipe is None or pipe.closed:
            raise BrokenPipeError("вход ребёнка закрыт")
        view = memoryview(data)
        while view:
            n = pipe.write(view)
            view = view[n:]

    def close_input(self) -> None:
        """EOF ребёнку: он дописывает хвост и выходит сам. Повторный вызов — пустой:
        закрытие закрытого файла ничего не делает."""
        pipe = self._proc.stdin
        if pipe is not None:
            try:
                pipe.close()
            except OSError:
                pass

    def finish(self, timeout: float) -> Outcome:
        """Дождаться выхода с потолком; не вышел — убить. Исход — кодом выхода, причина —
        последней строкой журнала ребёнка, тем же правилом, что у выхода до рукопожатия
        (`_await_handshake`): «код 1» без строки отправлял человека в соседний файл
        (выходной круг фикса A2 №478, M1)."""
        try:
            code = self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return Outcome(FAILED, reason=f"не вышел за {timeout:.0f} с — убит")
        if code == 0:
            return Outcome(OK)
        err = _last_line(_tail_text(self._stderr_path)) if self._stderr_path is not None else ""
        if code == EXIT_ENGINE_UNAVAILABLE:
            return Outcome(UNAVAILABLE, reason=err or "движок недоступен")
        return Outcome(FAILED, reason=f"код {code}: {err or 'без вывода'}")

    def kill_nowait(self) -> None:
        """SIGKILL без ожидания выхода: годится под замком хозяина и там, где ждать нельзя;
        выход дождутся `finish`, `kill` или сборщик `subprocess`."""
        try:
            self._proc.kill()
        except OSError:
            pass

    def kill(self) -> None:
        self.kill_nowait()
        try:
            self._proc.wait(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            pass

    def _deliver(self, fn: typing.Callable, *args: typing.Any) -> None:
        if self._abandoned:
            return
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 — чтение протокола не встаёт; след — в полях
            self.callback_errors += 1
            if not self.callback_error:
                self.callback_error = f"{type(e).__name__}: {e}"


def open_private(path: pathlib.Path) -> int:
    """Файл на дозапись только владельцу: режим при создании и `fchmod` для файла,
    который уже был. Журнал ребёнка здесь и журнал тени (`live_nemotron`) — одним
    способом."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, PRIVATE_MODE)
    try:
        os.fchmod(fd, PRIVATE_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_protocol(proc: subprocess.Popen, stream: StreamProcess, got_ready: typing.Any,
                   on_message: typing.Callable[[dict], None], on_eof: typing.Callable[[], None]) -> None:
    """Нить-читатель: первая строка `{"type": "ready"}` — рукопожатие, остальные —
    в `on_message`; конец stdout — `on_eof()`, если рукопожатие было."""
    import io
    try:
        for raw in io.BufferedReader(proc.stdout):
            try:
                message = json.loads(raw.decode("utf-8"))
            except ValueError:
                message = None
            if not isinstance(message, dict):
                stream.nonjson += 1
                continue
            if not got_ready.is_set():
                if message.get("type") == "ready":
                    stream.ready = message
                    got_ready.set()
                continue
            stream._deliver(on_message, message)
    except (OSError, ValueError):
        pass                          # труба умерла вместе с ребёнком — это и есть конец
    finally:
        got_ready.set()               # ждущий рукопожатия не висит до потолка
        if stream.ready:
            stream._deliver(on_eof)


def spawn_stream(python: str | os.PathLike, script: pathlib.Path, args: typing.Sequence[str], *,
                 stderr_path: pathlib.Path, handshake_timeout: float, role: str,
                 on_message: typing.Callable[[dict], None],
                 on_eof: typing.Callable[[], None],
                 cancel: typing.Any = None,
                 clock: typing.Callable[[], float] = time.monotonic) -> tuple[StreamProcess | None, Outcome]:
    """Запустить долгий скрипт в чистом окружении и дождаться рукопожатия.

    Рукопожатие — первая JSON-строка вида `{"type": "ready", ...}` за
    `handshake_timeout`. Ребёнок вышел раньше — исход его кодом (UNAVAILABLE по
    `EXIT_ENGINE_UNAVAILABLE`, иначе FAILED с последней строкой журнала); не успел —
    убит, FAILED, и обратных вызовов после этого не будет. При OK `payload` —
    рукопожатие, строки после него идут в `on_message`, конец stdout — `on_eof()`.
    `role` — роль нити-читателя в реестре потоков (`threads.ROLES`): чья это нить,
    знает вызывающий, а не дверь. `cancel` (`threading.Event`) обрывает ожидание
    рукопожатия: хозяин остановился, пока ребёнок грузил модель, — убит, FAILED.
    Исключение после запуска ребёнка — тоже FAILED, ребёнок убит. `clock` — часы
    потолка рукопожатия (тестам — свои)."""
    if _exiting:                             # уборка при выходе уже прошла — модель не поднимать зря
        return None, Outcome(FAILED, reason="процесс выходит — долгих детей не заводим")
    try:
        err_fd = open_private(stderr_path)
    except OSError as e:
        return None, Outcome(FAILED, reason=f"журнал ребёнка не открылся: {e}")
    cmd = [os.fspath(python), os.fspath(script), *args]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err_fd,
                                env=clean_env(os.environ), cwd=pathlib.Path(script).parent, bufsize=0)
    except FileNotFoundError:
        return None, Outcome(FAILED, reason=f"нет интерпретатора {os.fspath(python)}")
    except (OSError, ValueError) as e:       # ValueError — нулевой байт в пути из конфига
        return None, Outcome(FAILED, reason=f"не запустился: {e}")
    finally:
        os.close(err_fd)
    stream = StreamProcess(proc, stderr_path)
    try:
        _adopt(proc)                       # владение ребёнком — внутри границы: сбой здесь его убивает
        return _await_handshake(proc, stream, stderr_path=stderr_path,
                                handshake_timeout=handshake_timeout, role=role,
                                on_message=on_message, on_eof=on_eof, cancel=cancel, clock=clock)
    except BaseException as e:
        # после Popen выхода без закрытого входа и убитого ребёнка нет: исход двери —
        # значением, а ребёнок с моделью не остаётся без хозяина (выходной круг 1 по
        # №478 A2, I1); Ctrl-C и выход процесса — дальше, но уже без ребёнка
        stream._abandoned = True
        stream.close_input()
        stream.kill()
        if not isinstance(e, Exception):
            raise
        return None, Outcome(FAILED, reason=f"дверь упала после запуска: {type(e).__name__}: {e}")


def _await_handshake(proc: subprocess.Popen, stream: StreamProcess, *, stderr_path: pathlib.Path,
                     handshake_timeout: float, role: str,
                     on_message: typing.Callable[[dict], None], on_eof: typing.Callable[[], None],
                     cancel: typing.Any,
                     clock: typing.Callable[[], float]) -> tuple[StreamProcess | None, Outcome]:
    """Нить-читатель и рукопожатие — после запуска ребёнка; бросить может, убивает
    ребёнка при исключении вызывающий (`spawn_stream`)."""
    import threading

    import threads
    got_ready = threading.Event()
    threads.spawn(_read_protocol, name="foreign-stream-reader", role=role,
                  args=(proc, stream, got_ready, on_message, on_eof))
    deadline = clock() + handshake_timeout
    while not got_ready.wait(HANDSHAKE_POLL_S):          # потолок — с точностью до шага опроса
        if cancel is not None and cancel.is_set():
            why = "ожидание рукопожатия отменено — убит"
        elif clock() >= deadline:
            why = f"нет рукопожатия за {handshake_timeout:.0f} с — убит"
        else:
            continue
        stream._abandoned = True
        stream.close_input()
        stream.kill()
        return None, Outcome(FAILED, reason=why)
    if not stream.ready:
        # сюда приходят, когда читатель уже вышел (конец stdout без рукопожатия):
        # обратных вызовов больше не будет и без отметки «брошен»
        stream.close_input()
        try:
            code = proc.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:  # stdout закрыт, а процесс жив — не наш протокол
            stream.kill()
            return None, Outcome(FAILED, reason="закрыл вывод без рукопожатия — убит")
        err = _last_line(_tail_text(stderr_path))
        if code == EXIT_ENGINE_UNAVAILABLE:
            return None, Outcome(UNAVAILABLE, reason=err or "движок недоступен")
        return None, Outcome(FAILED, reason=f"код {code}: {err or 'без вывода'}")
    return stream, Outcome(OK, payload=dict(stream.ready))


def _tail_text(path: pathlib.Path, size: int = 4096) -> str:
    """Хвост журнала ребёнка — для причины отказа."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
