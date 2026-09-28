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

Исход — значением (`Outcome`), не исключением: вызывающий обязан отличать «движка
нет на этой машине» (UNAVAILABLE — код `EXIT_ENGINE_UNAVAILABLE`) от «движок упал»
(FAILED — другой код, потолок времени, нет интерпретатора, ответ не JSON-объект).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sysconfig
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


#: Отпечаток python-build-standalone: сборка идёт с префиксом /install, и он
#: остаётся в данных sysconfig. У Homebrew, системного Python и venv поверх них —
#: путь установки. Переносимой сборкой собран Python приложения
#: (`build_embedded_python.sh`), и только её копия годится окружением движка.
STANDALONE_PREFIX = "/install"


def is_portable() -> bool:
    """Этот процесс — переносимая сборка python-build-standalone: копию его
    `sys.base_prefix` можно унести в другой каталог (установщик движка, №474)."""
    return sysconfig.get_config_var("prefix") == STANDALONE_PREFIX


#: Виды исхода (`Outcome.kind`). Константы модуля, а не класса: в NamedTuple
#: `ClassVar` без отложенных аннотаций — TypeError, а с ними становится ПОЛЕМ, и
#: атрибут класса оказывается дескриптором поля, а не строкой (так устроен
#: `live_sidecar.WriteOutcome`, №477).
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
