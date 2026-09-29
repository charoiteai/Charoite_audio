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

import json
import os
import pathlib
import subprocess
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
