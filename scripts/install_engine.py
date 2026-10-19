#!/usr/bin/env python3
"""Поставить окружение движка диаризации — копию интерпретатора, пакеты из лока, веса.

Зачем (№474). Движок Nemotron работает процессом другого интерпретатора: в
Python приложения mlx нет, бандл подписан. Раньше окружение ставил человек
руками — venv разработчика, который ломался от обновления Homebrew, а у
пользователя приложения его не было вовсе. Теперь окружение ставит продукт:

1. печатает, куда пойдёт в сеть (PyPI, Hugging Face, GitHub), — до соединения;
2. сверяет машину с шапкой лока: macOS не ниже `min-macos`, Apple Silicon,
   Python той версии, под которую собран лок;
3. копирует интерпретатор, которым запущен (`sys.base_prefix`), во временный
   каталог рядом с целью — без пакетов приложения, кроме pip;
4. ставит пакеты лока `pip install --require-hashes --no-deps`;
5. качает веса с проверкой sha256 (`get_models.fetch_nemotron`);
6. проба: новый интерпретатор размечает секунду тишины той же дверью, что
   пересборка стенограммы (`diarize_nemotron.diarize_in_env`);
7. меняет каталог окружения на новый переименованием в том же каталоге.

Шаги 3–7 идут под замком установки (`one_install`): вторая установка того же
движка получает отказ, а остатки прерванных — временный каталог, прежнее
окружение между двумя переименованиями — убираются до начала (`sweep`).

Почему копия, а не venv. venv хранит путь к базовому интерпретатору, а
приложение, запущенное с карантином из «Загрузок», macOS запускает со
случайного пути (App Translocation): venv поверх бандла сломался бы на
следующем запуске. python-build-standalone переносим — тот же довод, по
которому его выбрал `build_embedded_python.sh`. Копия подписанного
интерпретатора сохраняет подпись и entitlements бандла, в том числе
`disable-library-validation`: колёса mlx грузятся (опыт 28.09 — mlx 0.32.2 на GPU).

    CHAROITE_ROOT=<папка данных> <python приложения> scripts/install_engine.py nemotron          # поставить
    CHAROITE_ROOT=<папка данных> <python приложения> scripts/install_engine.py nemotron --check  # что стоит, без сети
    CHAROITE_ROOT=<папка данных> <python приложения> scripts/install_engine.py nemotron --plan   # план одной строкой JSON

Исход установщика честный: 0 — поставлено и проба зелёная; `EXIT_INSTALL_BUSY` —
машина занята, идёт другая установка или загрузка того же файла у другого
процесса не продвигается;
`EXIT_INSTALL_CANCELLED` — прервали
(SIGTERM/SIGHUP/Ctrl-C); 1 — отказ (`Refused`, том без flock, отказ загрузчика
весов). Единственный переводчик «исключение → код» — `main`.

Приложение запускает установщик лидером своей группы процессов — тогда SIGKILL
группе гасит и pip. Признак — переменная `CHAROITE_INSTALL_NEW_PGROUP=1`; без неё
группа не меняется и Ctrl-C из терминала доходит как обычно.

У приложения это `Charoite.app/Contents/Resources/python/bin/python3`; команду
целиком — с корнем данных — печатают доктор и `--check`, если движок выбран, а
окружения нет (`diarize_nemotron.install_command(root)`); в шапку стенограммы
уходит только указатель — её пересылают людям, а команда несёт пути машины. Корень
установщик НАЗЫВАЕТ дверью канона, а не выводит из положения файла: из бандла
догадкой вышел бы сам бандл, и окружение легло бы в подписанный `.app` (№489).

Режим `--verify-config-edit` — проверка правки `config.yaml` кнопкой Nemotron тем же
парсером, что у демона (`yaml.safe_load`, `daemon.py`). Вход — одна строка JSON на
stdin `{"old", "new", "key", "expected", "value"}`; выход — код 0 и строка `ok` или
код 1 и строка `отказ: <причина>`. Ничего не пишет и корня данных не требует.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import urllib.parse
import wave
from collections.abc import Callable

# Байткод — до первого импорта своих модулей: человек запускает установщик python
# бандла из Терминала, мимо PYTHONPYCACHEPREFIX, который ставит детям приложение, и
# `__pycache__` лёг бы в подписанный `.app` — `codesign --verify --strict` скажет
# «file added» (предрелизный прогон 0.88.1, Opus C1).
sys.dont_write_bytecode = True
# Вставки — канон путей и модуль движка (src), загрузчик весов (scripts).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from charoite_paths import code_root, free_bytes, harden_umask, name_data_root_or_exit  # noqa: E402
import busy_signals  # noqa: E402
import config_loader  # noqa: E402
import diarize_nemotron  # noqa: E402
import file_locks  # noqa: E402
import foreign_python  # noqa: E402
import get_models  # noqa: E402
from exit_codes import EXIT_INSTALL_BUSY, EXIT_INSTALL_CANCELLED  # noqa: E402

CODE = code_root(__file__)

def _net_host(url: str) -> str:
    """Хост адреса: в блоке сети называем его, а не весь путь."""
    return urllib.parse.urlsplit(url).hostname or url


#: Куда пойдёт сеть — печатается до соединения, как у get_models. Список — из
#: адресов, которые может скачать путь установки: эмбеддинги и сегментация
#: набора голосов (только когда его нет) и веса Nemotron. Один и тот же список
#: у блока «сеть:» и у поля `network` в `--plan`.
NETWORK = (
    "PyPI (pypi.org, files.pythonhosted.org) — пакеты из лока с хешами; ваши индексы и файлы настроек "
    "pip, ~/.netrc не читаются, прокси системы и окружения учитываются",
    f"{_net_host(get_models.MODELS[get_models.DEFAULT].url)} — эмбеддинги набора голосов, "
    "только если набора голосов нет; файлы этот хост отдаёт через свою сеть доставки",
    f"{_net_host(get_models.SEGMENTATION[get_models.SEG_DEFAULT].url)} — сегментация набора голосов, "
    "только если набора голосов нет; файлы этот хост отдаёт через свою сеть доставки",
    f"{_net_host(get_models.NEMOTRON.url)} — веса Nemotron с проверкой sha256; "
    "файлы этот хост отдаёт через свою сеть доставки",
)

#: Отпечаток python-build-standalone: сборка идёт с префиксом /install, и он
#: остаётся в данных sysconfig. У Homebrew, системного Python и venv поверх них —
#: путь установки: копию такого интерпретатора не унести в другой каталог.
STANDALONE_PREFIX = "/install"

PROBE_TIMEOUT_S = 180.0

#: Размер окружения движка, МБ. Замер 05.10.2026: `du -sm` окружения, поставленного
#: этим установщиком 29.09.2026 на Apple Silicon.
ENVIRONMENT_MB = 601

#: Гейт диска установки (№622 ч. 2) — что установка ДОБАВИТ на томе корня данных в
#: пике: новая копия окружения `.new-*` (`ENVIRONMENT_MB`), временные колёса pip
#: (`PIP_TEMP_MB`), недостающие веса и набор голосов (`sizes_mb`). Прежнее окружение
#: не считается: оно уже занимает место, а не просит свободного. Гейт считает полную
#: переустановку — окружение пересобирается всегда, даже когда чинить нужно только
#: веса. Итог: переустановка при готовых весах и наборе — 1202 МБ, первая установка —
#: 1440 МБ.
#:
#: Веса качаются в `.part` рядом с целью и переименовываются — второй копии нет.
#: Набор голосов: архив сегментации плюс распакованная копия, до двух размеров
#: (≈ +7–14 МБ), запас покрывает. Мера — по тому `engines/`: веса лежат в
#: `root/models/`, и при `models/` симлинком на другой том проверяется не тот том
#: (группировка слагаемых по `st_dev` — №640).
#:
#: Колёса pip — верхняя граница без замера: pip без кэша держит скачанные колёса во
#: временном каталоге до конца своей команды, а сжатое не больше установленного.
#: Лежат они в `TMPDIR` процесса, а гейт меряет том корня данных. `TMPDIR` на том же
#: томе — гейт верен; на другом и большем — гейт перестраховывается не больше чем на
#: `PIP_TEMP_MB`; на другом и меньшем — гейт недопроверяет: pip упадёт посреди
#: установки, а прежнее окружение останется целым. Перенос `TMPDIR` в
#: `staging.parent` — №640.
PIP_TEMP_MB = ENVIRONMENT_MB

#: Признак от приложения: установщик запускают лидером своей группы процессов,
#: чтобы SIGKILL группе погасил и pip. Без переменной группу не меняем — Ctrl-C
#: из терминала обязан доходить.
NEW_PGROUP_ENV = "CHAROITE_INSTALL_NEW_PGROUP"

#: Сигналы, которые считаем отменой установки: закрытие приложения (SIGTERM),
#: оборванный терминал (SIGHUP) и Ctrl-C (SIGINT). `install_signals` ставит
#: `_cancel` только на те из них, что не унаследованы как `SIG_IGN`: `nohup` и
#: фоновые задания неинтерактивного shell запускают процесс с SIGHUP и SIGINT в
#: `SIG_IGN`, и человек уже попросил сигнал игнорировать — закрытый терминал не
#: смеет обрывать установку на ~600 МБ.
#:
#: `except KeyboardInterrupt` в `main` ловит `KeyboardInterrupt`, пришедший в тело
#: не от этого обработчика: SIGINT там уже `_cancel` (или унаследован как
#: `SIG_IGN`), так что отмену сигнала переводит ветка `InstallCancelled`. Ветка
#: нужна для `KeyboardInterrupt` из другого источника — его подняла библиотека,
#: прежний обработчик вернули, тест подменил шаг, — и переводит его в тот же код
#: отмены. Ctrl-C до `install_signals()` сюда не попадает: тот промежуток лежит
#: вне `try`.
CANCEL_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class InstallCancelled(BaseException):
    """Установку прервали. Наследник `BaseException`, а не `Exception`: обработчик
    сигнала бросает её поверх любого шага, и внутренние `except Exception` (в том
    числе `SystemExit` вокруг загрузки набора голосов) не должны её проглотить."""


class InstallBusy(Exception):
    """Машина или каталог движка заняты другой работой; текст — чем именно."""


def busy_now(root: pathlib.Path) -> list[str]:
    """Чем занята машина глазами установщика — общий вердикт `busy_signals`.

    Раньше установщик брал занятость из скрипта ожидания ночи и не видел идущую
    ночь; `machine_busy` — то же определение, что у ночи, мутатора и прочих
    тяжеловесов: живая запись, разбор встречи (стадией словами), ночной цикл,
    мутация тестов."""
    return busy_signals.machine_busy(root)


@dataclasses.dataclass(frozen=True)
class EngineSpec:
    """Движок, который ставит установщик: лок, раскладка (из модуля движка), веса, проба."""
    lock: pathlib.Path
    home: Callable[[pathlib.Path], pathlib.Path]
    python: Callable[[pathlib.Path], pathlib.Path]
    weights: Callable[[pathlib.Path], pathlib.Path]
    fetch_weights: Callable[[pathlib.Path], None]
    probe: Callable[..., foreign_python.Outcome]
    diarize: Callable[..., foreign_python.Outcome]


ENGINES = {
    "nemotron": EngineSpec(
        lock=CODE / "requirements-nemotron.lock",
        home=diarize_nemotron.engine_dir,
        python=diarize_nemotron.engine_python,
        weights=diarize_nemotron.model_dir,
        fetch_weights=get_models.fetch_nemotron,
        probe=diarize_nemotron.probe_in_env,
        diarize=diarize_nemotron.diarize_in_env,
    ),
}


class Refused(Exception):
    """Ставить нельзя; текст — что не так и что сделать."""


def lock_platform(lock: pathlib.Path) -> tuple[str, str]:
    """`(версия Python, минимальная macOS)` из шапки лока — из того, что ставим.

    uv пишет свою команду в шапку (`--python-version 3.12`), скрипт лока —
    строку `# min-macos: 14.0`. Нет любой из двух — лок собран не нашим скриптом."""
    if not lock.is_file():
        raise Refused(f"нет лока {lock} — соберите: .venv/bin/python scripts/lock_runtime_deps.py nemotron")
    try:
        head = lock.read_text(encoding="utf-8")[:4000]
    except (OSError, UnicodeError) as e:
        # Лок есть, но не читается (права, битый том) или не декодируется в UTF-8:
        # `UnicodeDecodeError` — не `OSError`, и без перевода он печатался бы трассой
        # мимо `main`. Это детерминированный отказ кодом 1 (№622 B2, часть 1).
        raise Refused(f"лок {lock} не читается: {getattr(e, 'strerror', None) or e}") from e

    py = re.search(r"--python-version\s+(\d+\.\d+)", head)
    mac = re.search(r"^# min-macos:\s*(\d+(?:\.\d+)*)\s*$", head, re.M)
    if not py or not mac:
        raise Refused(f"в шапке {lock.name} нет версии Python или min-macos — лок собран не "
                      f"scripts/lock_runtime_deps.py; пересоберите его")
    return py.group(1), mac.group(1)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split(".") if p.isdigit())


def check_machine(lock: pathlib.Path) -> None:
    """Машина и интерпретатор годятся под этот лок, иначе Refused с рецептом."""
    want_py, want_mac = lock_platform(lock)
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise Refused(f"движок работает только на Mac с Apple Silicon, а это {sys.platform}/{platform.machine()}")
    mac = platform.mac_ver()[0]
    if not mac or _version_tuple(mac) < _version_tuple(want_mac):
        raise Refused(f"macOS {mac or '?'}, а колёса mlx собраны начиная с {want_mac}")
    have_py = "{}.{}".format(*sys.version_info[:2])
    if have_py != want_py:
        raise Refused(f"Python {have_py}, а лок собран под {want_py} — запустите установщик "
                      f"интерпретатором приложения ({diarize_nemotron.APP_PYTHON})")
    prefix = sysconfig.get_config_var("prefix")
    if prefix != STANDALONE_PREFIX:
        raise Refused(f"интерпретатор {sys.executable} собран не как python-build-standalone "
                      f"(prefix {prefix}) — его копия не переносится. Запустите установщик "
                      f"интерпретатором приложения ({diarize_nemotron.APP_PYTHON})")


def copy_ignore(site_packages: pathlib.Path) -> Callable[[str, list[str]], set[str]]:
    """Фильтр копии интерпретатора: из site-packages берётся только pip.

    Пакеты приложения (numpy, onnxruntime…) окружению движка не нужны и видны
    были бы его процессу — ставит оно только то, что в локе."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        if pathlib.Path(directory) != site_packages:
            return set()
        return {n for n in names if not (n == "pip" or (n.startswith("pip-") and n.endswith(".dist-info")))}
    return ignore


def copy_interpreter(dest: pathlib.Path) -> pathlib.Path:
    """Копия `sys.base_prefix` в `dest` без пакетов приложения; путь к новому python3."""
    base = pathlib.Path(sys.base_prefix)
    site = pathlib.Path(sysconfig.get_paths(vars={"base": str(base), "platbase": str(base)})["purelib"])
    shutil.copytree(base, dest, symlinks=True, ignore=copy_ignore(site))
    python = dest / "bin" / "python3"
    if not python.exists():
        raise Refused(f"в копии нет bin/python3 ({dest}) — интерпретатор устроен не как python-build-standalone")
    return python


def pip_install(python: pathlib.Path, lock: pathlib.Path) -> None:
    """Пакеты лока в копию: только с хешами и ровно по списку."""
    proc = foreign_python.run_pip(python, "install", "--require-hashes", "--no-deps", "--no-cache-dir",
                                  "--disable-pip-version-check", "--no-warn-script-location", "-r", str(lock),
                                  stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise Refused(f"pip не поставил пакеты лока (код {proc.returncode}) — вывод выше; ваши индексы и "
                      "настройки pip не читаются намеренно — нужен доступ к pypi.org напрямую или через прокси")


def silence(path: pathlib.Path, seconds: float = 1.0) -> pathlib.Path:
    """Секунда тишины 16 кГц 16 бит — запись для пробы движка."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(diarize_nemotron.SAMPLE_RATE)
        w.writeframes(b"\0\0" * int(diarize_nemotron.SAMPLE_RATE * seconds))
    return path


def swap_in(new: pathlib.Path, home: pathlib.Path) -> None:
    """Каталог окружения → новый: оба переименования в одном каталоге (один том).

    Оба переименования идут под маской сигналов отмены (SIGTERM, SIGHUP,
    SIGINT): отмена между ними иначе оставила бы `home` пустым, а прежнее
    окружение — в `.old-*` до следующей установки. Пока маска стоит, сигнал
    только откладывается — обработчик увидит его после конца отката (и тогда
    бросит отмену) или после фиксации (и тогда не бросит).

    Откат — на ЛЮБОМ исключении (в том числе отмене `BaseException`), а не
    только `OSError`. Решение об откате — по состоянию каталогов, как в
    `sweep`: `home` нет, `.old` есть — вернуть. Сразу после удачного второго
    переименования, ещё под маской, ставится признак фиксации
    (`_SWAP_COMMITTED`): с этого мига отмена не бросает, а просит доделать
    уборку. Прежнее, оставшееся после удачной замены, убирает `finally`
    установки или следующий `sweep`."""
    global _SWAP_COMMITTED
    old = home.with_name(f".{home.name}.old-{os.getpid()}")
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, CANCEL_SIGNALS)
    try:
        try:
            if home.exists():
                os.rename(home, old)
            os.rename(new, home)
        except BaseException:
            if not home.exists() and old.exists():
                os.rename(old, home)
            raise
        _SWAP_COMMITTED = True
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)


@contextlib.contextmanager
def one_install(home: pathlib.Path):
    """Одна установка движка за раз: замок рядом с каталогом окружения.

    Без него две установки меняли бы каталог наперегонки, а уборка остатков
    (`sweep`) сносила бы временный каталог живой соседки. Исход замка — функцией
    исхода: «занято» и «том без flock» — разные беды, и переводятся в разные коды."""
    fd = os.open(home.with_name(f".{home.name}.install.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as f:
        outcome = file_locks.acquire_outcome(f)
        if outcome == file_locks.BUSY:
            raise InstallBusy(f"идёт другая установка движка {home.name} — дождитесь её")
        if outcome == file_locks.NO_FLOCK:
            raise Refused(f"замок установки {home.name} не взять: том без flock — "
                          f"поставьте движок в локальный корень данных")
        yield


def sweep(home: pathlib.Path) -> None:
    """Остатки прерванных установок. Зовётся под замком `one_install`: значит, ничьи.

    `.new-*` — недоделанная копия: `finally` установки не выполняется при
    закрытом терминале (SIGHUP) и SIGKILL, и 600 МБ оставались бы навсегда.
    `.old-*` — прежнее окружение, если процесс умер между двумя
    переименованиями `swap_in`: на пустое место оно возвращается, при живом
    окружении — лишнее."""
    for p in sorted(home.parent.glob(f".{home.name}.new-*")):
        shutil.rmtree(p, ignore_errors=True)
    for p in sorted(home.parent.glob(f".{home.name}.old-*")):
        if home.exists():
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.rename(p, home)


def machine_fitness(lock: pathlib.Path) -> dict:
    """Годность машины полем `{ok, reason}`: отказ `check_machine` — не исключение
    наружу, а причина в JSON (`--plan` не должен ломаться на чужой машине)."""
    try:
        check_machine(lock)
    except Refused as e:
        return {"ok": False, "reason": str(e)}
    except OSError as e:
        # Лок без прав на чтение — тоже причина, а не исключение наружу: `--plan`
        # обязан отдать одну строку JSON и на ней.
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "reason": ""}


def enter_process_group(env=None) -> bool:
    """Лидер группы процессов — только по явному признаку от приложения.

    SIGKILL группе тогда гасит и pip (установщик и его дети — одна группа). Без
    переменной группу не трогаем: запуск из терминала обязан оставить Ctrl-C
    работающим как обычно.

    Уже лидер группы (а лидер сессии всегда и лидер группы) — выходим без
    `setpgid`: на лидере сессии он бросает EPERM, и это была бы трасса до разбора
    корня. Прочая `OSError` — `Refused` с текстом, `main` отдаёт её кодом 1."""
    env = os.environ if env is None else env
    if env.get(NEW_PGROUP_ENV) != "1":
        return False
    if os.getpgrp() == os.getpid():
        return True
    try:
        os.setpgid(0, 0)
    except OSError as e:
        raise Refused(f"не удалось стать лидером группы процессов ({e}) — "
                      f"запустите установщик без {NEW_PGROUP_ENV}") from e
    return True


#: Замена окружения зафиксирована: второе переименование `swap_in` удалось, и
#: отката больше нет. Ставится `swap_in` под маской сигналов; снимается `main`
#: в `finally` — второй вызов `main` в том же процессе не должен унаследовать
#: признак.
_SWAP_COMMITTED = False


def _cancel(signum, frame) -> None:
    """Обработчик отмены: `BaseException`, а не `SystemExit`.

    Наследник `BaseException`, а не `SystemExit`: `SystemExit` прочитался бы
    ошибкой загрузки весов и вернул бы код 1 вместо честного «отменено».

    Если замена уже зафиксирована (`_SWAP_COMMITTED`), отменять нечего: движок
    поставлен. Тогда обработчик не бросает — печатает в stderr, что отмена
    пришла после замены, — и уборка доходит до конца: `main` печатает «готово»
    и возвращает 0."""
    if _SWAP_COMMITTED:
        print("отмена пришла после замены — движок уже поставлен, доделываю уборку",
              file=sys.stderr)
        return
    raise InstallCancelled(f"получен сигнал {signum}")


def install_signals() -> dict:
    """Обработчики отмены — на все режимы, включая `--check`: отменённая проверка
    тоже честное «отменено». Возвращает прежние, их возвращает `main` на выходе.

    Сигнал, унаследованный как `SIG_IGN`, не трогаем: так запускают процесс
    `nohup` и фоновые задания неинтерактивного shell, и человек уже попросил его
    игнорировать. Возвращаются только реально заменённые обработчики, поэтому
    `restore_signals` вернёт ровно их и не тронет унаследованное `SIG_IGN`."""
    replaced = {}
    for s in CANCEL_SIGNALS:
        if signal.getsignal(s) is signal.SIG_IGN:
            continue
        replaced[s] = signal.signal(s, _cancel)
    return replaced


def restore_signals(previous: dict) -> None:
    for s, handler in previous.items():
        signal.signal(s, handler)


def plan(name: str, root: pathlib.Path) -> int:
    """План установки одной строкой JSON — без сети и без запуска движка.

    Битый или нечитаемый конфиг не роняет `--plan` трассой: `engine` тогда несёт
    поле `error`, остальные поля — как есть, код 0. Прочие исключения — забота
    `main`: последний рубеж вокруг режима `--plan` отдаёт одну строку и код 1."""
    spec = ENGINES[name]
    try:
        cfg = config_loader.load_user_or_example(root) or {}
    except Exception as e:  # noqa: BLE001 — одна строка JSON важнее трассы
        engine: dict = {"error": f"конфиг не прочитан: {e}"}
    else:
        engine = diarize_nemotron.engine_state(root, cfg)
    sizes = sizes_mb(spec, root)
    payload = {
        "machine": machine_fitness(spec.lock),
        "network": list(NETWORK),
        "license": {"name": get_models.NEMOTRON_LICENSE[0], "url": get_models.NEMOTRON_LICENSE[1]},
        "sizes_mb": sizes,
        "engine": engine,
    }
    payload["disk"] = disk_need(root, sizes)
    payload["memory"] = machine_memory()
    payload["offer"] = offer(payload["machine"], engine, payload["disk"], payload["memory"], sizes)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def sizes_mb(spec: EngineSpec, root: pathlib.Path) -> dict:
    """Сколько займёт на диске то, что поставит установка, — одно определение для
    `--plan` и гейта диска в `install`.

    Читает веса ради суммы (секунды: `_digest` по каждому файлу,
    `get_models.py:196–203`), отказ по диску наступает после."""
    return {
        # Числа — сколько займёт на диске то, что поставит установка (№622 B2).
        # Для весов и набора голосов это и объём загрузки, а для окружения
        # загрузка меньше: из сети идут только колёса лока, интерпретатор
        # копируется локально, так что `du -sm` окружения — не трафик.
        #
        # Набор голосов — по проверке «только если его нет»: блок сети помечает
        # его так же, и план обязан считать так же, иначе кнопка приложения
        # покажет загрузку, которой не будет.
        #
        # Окружение — всегда полный размер на диске: установка пересобирает его
        # всегда (`copy_interpreter` и `pip_install` идут без проверки «уже
        # стоит»), и проверки «на месте», которой 0 означал бы «ставить нечего»,
        # у неё нет. Ноль здесь обещал бы кнопке 0 МБ, а установка снова
        # копировала бы интерпретатор и ставила пакеты.
        "environment": ENVIRONMENT_MB,
        # Веса — по той же проверке, по которой их качает `fetch_nemotron`
        # (сумма каждого файла), а не по структуре каталога: `check_model_dir`
        # пропустила бы битый `model.safetensors` при правдоподобном
        # `config.json`, и план обещал бы 0, а установка тянула 190 МБ.
        "weights": sum(size_mb for _n, _u, size_mb, _s
                       in get_models.nemotron_missing(spec.weights(root))),
        "voice_set": sum(model.size_mb for dest, min_bytes, model in _voice_targets(root)
                         if get_models.check(dest, min_bytes=min_bytes) is not None),
    }


_MIB = 1024 * 1024


def _tree_bytes(path: pathlib.Path) -> int:
    """Размер дерева по `lstat`, без перехода по ссылкам. Ошибка чтения одного файла —
    ноль за файл, а не исключение: оценка не смеет ронять `--plan`."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    for top, dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in dirs + files:
            try:
                st = os.lstat(os.path.join(top, name))
            except OSError:
                continue
            if not stat.S_ISDIR(st.st_mode):
                total += st.st_size
    return total


def reclaimable_mb(home: pathlib.Path) -> int:
    """Сколько МБ уберёт `sweep`: все `.<имя>.new-*` и `.<имя>.old-*` при живом `home`
    (без него `sweep` возвращает `.old-*` на место, а не удаляет).

    Оценка без замка: `--plan` замок установки не берёт и посчитает `.new-*` живой
    соседней установки как освобождаемое; проба замка — №640. Без этой оценки 600 МБ
    остатков после SIGKILL по сроку держали бы гейт `--plan` закрытым, а `sweep`,
    который их убирает, зовёт только установка."""
    leftovers = list(home.parent.glob(f".{home.name}.new-*"))
    if home.exists():
        leftovers += home.parent.glob(f".{home.name}.old-*")
    return sum(_tree_bytes(p) for p in leftovers) // _MIB


def _existing_ancestor(path: pathlib.Path) -> pathlib.Path:
    """Ближайший существующий предок: `free_bytes` на несуществующем пути бросает
    `FileNotFoundError`, а у свежего корня данных ещё нет `engines/`."""
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def disk_need(root: pathlib.Path, sizes: dict, reclaim_mb: int | None = None) -> dict:
    """Хватит ли места на томе `engines/` для установки — поле `disk` у `--plan` и
    гейт `install`. Слагаемые и их оговорки — у `PIP_TEMP_MB`.

    `ok` — свободное плюс освобождаемое `sweep` не меньше нужного. `reclaim_mb` None —
    оценить освобождаемое (`--plan`, `sweep` ещё не звали); `install` передаёт 0: после
    `sweep` место уже освобождено, а остаток, который `rmtree(ignore_errors=True)` не
    убрал, места не вернёт. Мера свободного —
    `free_bytes`, одна на проект. Если она не сработала (`OSError`), гейт открыт:
    `free_mb` — null, причина «не измерено». На корне, где мера не работает,
    установка упадёт и сама, а ложный запрет хуже."""
    home = diarize_nemotron.engine_dir(root)
    need = sizes["environment"] + PIP_TEMP_MB + sizes["weights"] + sizes["voice_set"]
    reclaim = reclaimable_mb(home) if reclaim_mb is None else reclaim_mb
    try:
        free = free_bytes(_existing_ancestor(home)) // _MIB
    except OSError as e:
        return {"free_mb": None, "reclaimable_mb": reclaim, "need_mb": need, "ok": True,
                "reason": f"свободное место не измерено: {e}"}
    ok = free + reclaim >= need
    reason = "" if ok else (f"на томе с папкой данных свободно {free} МБ, установке нужно {need} МБ — "
                            "освободите место")
    return {"free_mb": free, "reclaimable_mb": reclaim, "need_mb": need, "ok": ok, "reason": reason}


#: Порог предупреждения о памяти: на машинах до 16 ГБ разбор с Nemotron не замерен.
#: Не запрет — память не меняет ни одного решения `offer`, только `warnings`.
MEMORY_WARN_MB = 16 * 1024
MEMORY_REASON = ("на машинах с 16 ГБ памяти и меньше разбор с Nemotron не замерен; пик на 64 ГБ — "
                 "около 2,9 ГБ (встреча 55 минут, 06.10.2026)")


def machine_memory() -> dict:
    """Объём памяти машины — поле `memory` у `--plan`: `{total_mb, warn, reason}`.

    Сравнение с порогом — в байтах: в целых МБ «16 ГиБ + страница» неотличимо от
    16 ГиБ. `total_mb` — только для показа. Объём не определён — тоже `warn`."""
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError):
        total = None
    if total is not None and total <= 0:   # sysconf отдаёт -1, если значение не определено
        total = None
    warn = total is None or total <= MEMORY_WARN_MB * _MIB
    if not warn:
        reason = ""
    elif total is None:
        reason = f"объём памяти не определён; {MEMORY_REASON}"
    else:
        reason = MEMORY_REASON
    return {"total_mb": None if total is None else total // _MIB, "warn": warn, "reason": reason}


#: Причины отказа действий кнопки — одна строка на один отказ (`offer`).
REASON_INSTALL_CUSTOM = "задан sufler.nemotron_python — установка не меняет, чем запускается движок"
REASON_INSTALL_UNKNOWN = "в конфиге diarize_backend: {backend!r} — неизвестное значение"
REASON_INSTALL_DONE = "движок поставлен и включён"
REASON_ENABLE_NOT_SHERPA = "в конфиге выбран не sherpa — включать нечего"
REASON_ENABLE_NOT_READY = "движок не поставлен или неполон — сначала поставьте"
REASON_DISABLE_SHERPA = "разбор уже идёт на sherpa"
#: Причины строки состояния.
REASON_STATE_UNKNOWN = "diarize_backend: {backend!r} — неизвестное значение, разбор идёт sherpa"
REASON_STATE_CUSTOM = "движок по ключу sufler.nemotron_python"
#: `ready` — готовность по раскладке: mlx-audio и запуск движка она не проверяет.
REASON_READY_BY_LAYOUT = "готов по раскладке; работу движка проверяет install_engine.py nemotron --check"


def _first_refusal(*checks: tuple[bool, str]) -> str:
    """Первая причина отказа сверху или `""` — разрешено."""
    return next((reason for failed, reason in checks if failed), "")


def _action(reason: str) -> dict:
    return {"allowed": reason == "", "reason": reason}


def offer(machine: dict, engine: dict, disk: dict, memory: dict, sizes: dict) -> dict:
    """Что стоит и что можно сделать — поле `offer` у `--plan`, одно место правила.

    Чистая функция без ввода-вывода: вход — уже собранные поля плана. Приложение
    состояние и действия только показывает, своих правил не строит.

    `install.allowed` — политика кнопки приложения: CLI `install_engine.py nemotron`
    конфиг не читает и при заданном `sufler.nemotron_python` или неизвестном
    `diarize_backend` по-прежнему ставит. `machine.ok` — гейт только установки, метку
    состояния он не меняет. Память — только предупреждение, ни одно `allowed` от неё
    не зависит. `backend` — уже нормализованное `engine_state` значение; в причинах
    оно через `!r`, чтобы пустое читалось как `''`.

    `engine` с полем `error` (битый конфиг) — `config_error`, все действия запрещены
    той же причиной; кроме `error` ничего не читается."""
    if "error" in engine:
        error = engine["error"]
        return {"state": "config_error", "reason": error, "install": _action(error),
                "enable": _action(error), "disable": _action(error), "warnings": []}
    backend = engine["backend"]
    ready = engine["ready"]
    custom = engine["nemotron_python_set"]
    known = backend in diarize_nemotron.ENGINE_BACKENDS
    install = _action(_first_refusal(
        (not machine["ok"], machine["reason"]),
        (not disk["ok"], disk["reason"]),
        (custom, REASON_INSTALL_CUSTOM),
        (not known, REASON_INSTALL_UNKNOWN.format(backend=backend)),
        (ready and backend == "nemotron", REASON_INSTALL_DONE),
    ))
    enable = _action(_first_refusal(
        (backend != "sherpa", REASON_ENABLE_NOT_SHERPA),
        (not ready, REASON_ENABLE_NOT_READY),
    ))
    disable = _action(_first_refusal((backend == "sherpa", REASON_DISABLE_SHERPA)))
    if not known:
        state, reason = "backend_unknown", REASON_STATE_UNKNOWN.format(backend=backend)
    elif custom:
        state, reason = "custom_interpreter", engine["ready_reason"] or REASON_STATE_CUSTOM
    elif backend == "nemotron":
        state, reason = ("enabled", REASON_READY_BY_LAYOUT) if ready else ("enabled_broken", engine["ready_reason"])
    elif ready:
        state, reason = "ready_off", REASON_READY_BY_LAYOUT
    elif engine["missing"] == "environment":
        state, reason = "not_installed", ""
    else:
        state, reason = "broken", engine["ready_reason"]
    warnings = []
    if memory["warn"] and (install["allowed"] or enable["allowed"] or backend == "nemotron"):
        warnings.append("memory")
    if sizes["voice_set"] > 0 and backend == "nemotron":
        # откат на sherpa невозможен, пока набора голосов нет
        warnings.append("voice_set_missing")
    if engine["live_mode"] != "off":
        # живой поток работает по своему ключу `sufler.live_nemotron`, кнопка им не управляет
        warnings.append("live_nemotron")
    return {"state": state, "reason": reason, "install": install, "enable": enable, "disable": disable,
            "warnings": warnings}


def _voice_targets(root: pathlib.Path) -> tuple[tuple[pathlib.Path, int, object], ...]:
    """Цели набора голосов sherpa: `(куда, минимальный размер, чем качать)`.

    Один список для установки и для `--plan`: размер в плане обязан считаться по
    той же проверке, по которой `ensure_voice_set` решает, качать ли файл."""
    return (
        (get_models.diar_target(root), get_models.MIN_BYTES,
         get_models.MODELS[get_models.DEFAULT]),
        (get_models.seg_target(root), get_models.SEG_MIN_BYTES,
         get_models.SEGMENTATION[get_models.SEG_DEFAULT]),
    )


def ensure_voice_set(root: pathlib.Path) -> None:
    """Недостающий набор голосов sherpa — запасной движок пересборки.

    Ставится тем же загрузчиком `get_models`, что `--diar`: веса нужны и без
    окружения Nemotron — на них уходит пересборка, если движок не разметил. Отказ
    загрузки — `SystemExit` из `get_models.download`, его переводит `main`."""
    for dest, min_bytes, model in _voice_targets(root):
        if get_models.check(dest, min_bytes=min_bytes) is None:
            print(f"набор голосов: {dest} уже на месте")
            continue
        print(f"набор голосов: {dest.name}…")
        get_models.download(model.url, dest, model.size_mb, sha256=model.sha256)


def install(name: str, root: pathlib.Path) -> int:
    """Поставить движок: детерминированные отказы — до переходящих.

    Сначала годность машины и лока (`check_machine`), потом занятость (`busy_now`):
    неподходящей машине ждать конца ночи нечего — повтор упрётся в ту же машину,
    и «занято» кодом 12 гнало бы кнопку приложения в пустой цикл ожидания. Лок
    `check_machine` читает первым делом, поэтому отсутствие лока — тоже
    детерминированный отказ, а не «занято»."""
    # В группу входят режимы, которые запускают процессы: установка (pip, проба)
    # и проверка (`check` зовёт `spec.probe`, а та запускает интерпретатор движка
    # дочерним процессом). `--plan` в группу не входит: он ничего не запускает и
    # обязан выдать одну строку JSON — отказ `setpgid` обернул бы её отказом с
    # пустым stdout.
    enter_process_group()
    spec = ENGINES[name]
    check_machine(spec.lock)
    busy = busy_now(root)
    if busy:
        raise InstallBusy(", ".join(busy) + " — дождитесь конца и повторите установку")
    home = spec.home(root)
    home.parent.mkdir(parents=True, exist_ok=True)
    with one_install(home):
        sweep(home)
        # Гейт диска — после уборки остатков (место уже освобождено) и до записи
        # окружения, весов и набора голосов: к этому мигу созданы только каталог
        # `engines/` и замок.
        disk = disk_need(root, sizes_mb(spec, root), reclaim_mb=0)
        if not disk["ok"]:
            raise Refused(disk["reason"])
        if disk["free_mb"] is None:
            print(disk["reason"])
        staging = home.with_name(f".{home.name}.new-{os.getpid()}")
        print(f"ставлю движок {name} в {home}\nсеть:")
        for line in NETWORK:
            print(f"  {line}")
        print(f"  лицензия весов: {get_models.NEMOTRON_LICENSE[0]} — {get_models.NEMOTRON_LICENSE[1]}")
        try:
            ensure_voice_set(root)
            staging.mkdir()
            print(f"копирую интерпретатор {sys.base_prefix}…")
            python = copy_interpreter(staging / "python")
            print(f"пакеты из {spec.lock.name}…")
            pip_install(python, spec.lock)
            print(f"веса в {spec.weights(root)}…")
            spec.fetch_weights(spec.weights(root))
            print("проба: секунда тишины через дверь пересборки…")
            with tempfile.TemporaryDirectory() as tmp:   # запись пробы — не часть окружения
                out = spec.diarize(str(python), silence(pathlib.Path(tmp) / "probe.wav"), root=root,
                                   timeout=PROBE_TIMEOUT_S)
            if not out.ok:
                raise Refused(f"проба не прошла ({out.kind}): {out.reason}")
            swap_in(staging, home)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    print(f"готово: {spec.python(root)}\n"
          f"пересборка берёт это окружение, если ключ sufler.nemotron_python пуст; заданный ключ главнее — "
          f"чей интерпретатор в работе, показывает scripts/doctor.py.\n"
          f"включить движок: sufler.diarize_backend: nemotron в конфиге (или кнопка установки движка в приложении)")
    return 0


def check(name: str, root: pathlib.Path) -> int:
    """Что стоит — без сети: окружение, версия mlx-audio, веса (через пробу движка)."""
    # `check` зовёт `spec.probe`, а та запускает интерпретатор движка дочерним
    # процессом: без входа в группу SIGKILL группе не погасит пробу вместе с
    # установщиком. Отказ `setpgid` — `Refused` кодом 1, как у установки.
    enter_process_group()
    spec = ENGINES[name]
    lock_platform(spec.lock)   # лок читают и установка, и проверка: «нет лока» не ждёт до установки (№489)
    python = spec.python(root)
    if not python.exists():
        print(f"окружения движка нет: {spec.home(root)} — {diarize_nemotron.install_command(root)}")
        return 1
    out = spec.probe("", root=root)
    if not out.ok:
        print(f"окружение {spec.home(root)} есть, но движку нечем работать ({out.kind}): {out.reason}")
        return 1
    print(f"движок {name} на месте: {python}, mlx-audio {out.payload['mlx_audio']}, веса {spec.weights(root)}")
    return 0


# Ключи, которые пишет кнопка Nemotron, и их значения (`verify_config_edit`).
_CONFIG_EDIT_VALUES = {"diarize_backend": ("nemotron", "sherpa")}


def _same(a: object, b: object) -> bool:
    """Равенство разобранного YAML с типами: `True == 1` и `1 == 1.0` в Python
    равны, а для демона это разные значения."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    # nan != nan: без этого правка ключа отказывала бы на любом конфиге с `.nan`.
    return a == b or (a != a and b != b)


def _matches_raw(current: object, expected: str | None) -> bool:
    """Значение ключа в старом файле — то, которое видело приложение.

    `expected` — сырое значение строкой (`AppSettings.parseValue`); `None` — ключа
    нет или он пуст. Голое `off` YAML читает как `False`, поэтому строка сверяется
    и сама, и прочитанной как скаляр YAML."""
    if expected is None:
        return current is None or current == ""
    if current == expected and isinstance(current, str):
        return True
    import yaml
    try:
        return _same(current, yaml.safe_load(expected))
    except yaml.YAMLError:
        return False


def verify_config_edit(payload: object) -> str | None:
    """Правка конфига кнопкой — ровно замена `sufler.<key>` на `value`.

    Оракул — тот же `yaml.safe_load`, которым конфиг читает демон: старый текст
    разбирается, значение ключа совпадает с ожидаемым; новый текст разбирается и
    равен старому с одной заменой `sufler.<key>`. `None` — правка верна, иначе
    причина отказа. Построчный разбор приложения — только первый фильтр:
    семь кругов подряд находили контекст документа, которого строка не видит
    (кавычка или скобка выше, второй блок `sufler`, секция в flow-форме)."""
    import yaml
    if not isinstance(payload, dict):
        return "вход не объект JSON"
    old_text, new_text = payload.get("old"), payload.get("new")
    key, expected, value = payload.get("key"), payload.get("expected"), payload.get("value")
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        return "нет текста old или new"
    if value not in _CONFIG_EDIT_VALUES.get(key, ()):
        return f"кнопка не пишет {key!r} = {value!r}"
    if expected is not None and not isinstance(expected, str):
        return "expected не строка"
    try:
        old = yaml.safe_load(old_text)
    except yaml.YAMLError as e:
        return f"старый config.yaml не читается YAML: {type(e).__name__}"
    old = {} if old is None else old
    if not isinstance(old, dict):
        return "старый config.yaml — не словарь"
    section = old.get("sufler")
    if section is not None and not isinstance(section, dict):
        return "секция sufler — не словарь"
    current = (section or {}).get(key)
    if not _matches_raw(current, expected):
        return f"sufler.{key} для YAML — {current!r}, а приложение видело {expected!r}"
    try:
        new = yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        return f"после правки config.yaml не читается YAML: {type(e).__name__}"
    want = dict(old)
    want["sufler"] = {**(section or {}), key: value}
    if not _same(new, want):
        return f"правка меняет не только sufler.{key}"
    return None


def _verify_config_edit_main() -> int:
    try:
        reason = verify_config_edit(json.loads(sys.stdin.read()))
    except Exception as e:  # noqa: BLE001 — любой сбой проверки — отказ записи, одной строкой
        reason = f"проверка упала: {type(e).__name__}: {e}"
    print("ok" if reason is None else f"отказ: {reason}")
    return 0 if reason is None else 1


def main(argv: list[str] | None = None) -> int:
    harden_umask()   # окружение и веса под корнем данных — только владельцу
    # Проверка правки конфига — до корня данных: он ей не нужен, и она ничего не пишет.
    if (sys.argv[1:] if argv is None else argv) == ["--verify-config-edit"]:
        return _verify_config_edit_main()
    # Корень — до разбора аргументов, как у import_meeting: проба `refuse` зовёт
    # вход без аргументов и ждёт отказа двери, а не ошибки argparse (№489).
    root = name_data_root_or_exit(__file__)
    # Строки «сеть: …» обязаны дойти до читателя раньше соединения и раньше вывода
    # pip, который пишет в тот же дескриптор сам. В терминале stdout буферизуется
    # строкой, в канале (`| tee`, лог) — блоком, и адреса приходили после всего
    # вывода pip (выкатка №474, 29.09). Команду раздают людям голой строкой —
    # запускающего, который выставил бы PYTHONUNBUFFERED, как ночь и мутатор, нет (№481).
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("engine", choices=sorted(ENGINES), help="какой движок ставить")
    ap.add_argument("--check", action="store_true", help="только проверить, что стоит (без сети)")
    ap.add_argument("--plan", action="store_true",
                    help="показать план установки одной строкой JSON (без сети)")
    args = ap.parse_args(argv)
    # Слово отказа задаёт режим, и выбирается оно здесь, в одном месте: в `--check`
    # ничего не ставилось, отказ `setpgid` или отсутствие лока — это «проверка не
    # выполнена», а в установке — «не поставлено». Ветки `except` слово не
    # дублируют. Код возврата у обоих один и тот же — 1.
    refusal = "проверка не выполнена" if args.check else "не поставлено"
    # Обработчики отмены — до работы и на все режимы: отменённый --check или --plan
    # тоже честное «отменено», а не обрыв без уборки.
    previous = install_signals()
    try:
        if args.plan:
            # Последний рубеж: `--plan` обязан выдать ровно одну строку JSON даже
            # на непредвиденном исключении. Отмена сохраняет свой код.
            try:
                return plan(args.engine, root)
            except Exception as e:  # noqa: BLE001 — `--plan` обязан выдать одну строку JSON при любом сбое, отмена (`BaseException`) проходит мимо
                print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
                return 1
        return check(args.engine, root) if args.check else install(args.engine, root)
    except InstallCancelled as e:
        print(f"отменено: {e}", file=sys.stderr)
        return EXIT_INSTALL_CANCELLED
    except KeyboardInterrupt:
        print("отменено: прервано с клавиатуры (Ctrl-C)", file=sys.stderr)
        return EXIT_INSTALL_CANCELLED
    except InstallBusy as e:
        print(f"занято: {e}", file=sys.stderr)
        return EXIT_INSTALL_BUSY
    except get_models.DownloadBusy as e:
        # Цель качает другой процесс (кнопка набора голосов, терминал): это
        # «занято», а не отказ — подкласс `SystemExit`, поэтому раньше общего
        # (№643; набор голосов и веса Nemotron — одной точкой).
        print(f"занято: {get_models._exit_text(e)}", file=sys.stderr)
        return EXIT_INSTALL_BUSY
    except Refused as e:
        print(f"{refusal}: {e}", file=sys.stderr)
        return 1
    except SystemExit as e:
        # Загрузчик весов (`get_models.download`) отказывает `SystemExit` с текстом:
        # для вызывающего это отказ кодом 1, словом режима. Голый выход (код 0 или
        # None) внутри `try` — тоже отказ, а не успех: argparse с `--help` стоит
        # выше `try` и сюда не попадает.
        if e.code in (0, None):
            print(f"{refusal}: загрузчик вышел без причины", file=sys.stderr)
        else:
            print(f"{refusal}: {get_models._exit_text(e)}", file=sys.stderr)
        return 1
    finally:
        # Обработчики возвращаем ПЕРЕД сбросом признака: сигнал между сбросом и
        # возвратом иначе попал бы в `_cancel` при `_SWAP_COMMITTED=False` и
        # бросил `InstallCancelled` из `finally` — трасса после удачной замены.
        global _SWAP_COMMITTED
        # Сброс признака — в `finally` вокруг возврата обработчиков: исключение
        # из `restore_signals` не смеет оставить признак фиксации взведённым.
        try:
            restore_signals(previous)
        finally:
            # Признак фиксации — на процесс: без сброса второй `main` в том же
            # процессе тестов унаследовал бы «замена зафиксирована» и не бросил
            # бы отмену до всякой установки.
            _SWAP_COMMITTED = False


if __name__ == "__main__":
    sys.exit(main())
