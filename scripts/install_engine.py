#!/usr/bin/env python3
"""Поставить окружение движка диаризации — копию интерпретатора, пакеты из лока, веса.

Зачем (№474). Движок Nemotron работает процессом другого интерпретатора: в
Python приложения mlx нет, бандл подписан. Раньше окружение ставил человек
руками — venv разработчика, который ломался от обновления Homebrew, а у
пользователя приложения его не было вовсе. Теперь окружение ставит продукт:

1. печатает, куда пойдёт в сеть (PyPI, Hugging Face), — до соединения;
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
машина занята или идёт другая установка; `EXIT_INSTALL_CANCELLED` — прервали
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
import subprocess
import sys
import sysconfig
import tempfile
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
from charoite_paths import code_root, harden_umask, name_data_root_or_exit  # noqa: E402
import config_loader  # noqa: E402
import diarize_nemotron  # noqa: E402
import file_locks  # noqa: E402
import foreign_python  # noqa: E402
import get_models  # noqa: E402
import wait_for_idle  # noqa: E402
from exit_codes import EXIT_INSTALL_BUSY, EXIT_INSTALL_CANCELLED  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

CODE = code_root(__file__)

#: Куда пойдёт сеть — печатается до соединения, как у get_models.
NETWORK = ("PyPI (pypi.org, files.pythonhosted.org) — пакеты из лока с хешами; ваши индексы и файлы настроек "
           "pip, ~/.netrc не читаются, прокси системы и окружения учитываются",
           "Hugging Face (huggingface.co) — веса с проверкой sha256")

#: Отпечаток python-build-standalone: сборка идёт с префиксом /install, и он
#: остаётся в данных sysconfig. У Homebrew, системного Python и venv поверх них —
#: путь установки: копию такого интерпретатора не унести в другой каталог.
STANDALONE_PREFIX = "/install"

PROBE_TIMEOUT_S = 180.0

#: Размер окружения движка, МБ. Замер 05.10.2026: `du -sm` окружения, поставленного
#: этим установщиком 29.09.2026 на Apple Silicon.
ENVIRONMENT_MB = 601

#: Признак от приложения: установщик запускают лидером своей группы процессов,
#: чтобы SIGKILL группе погасил и pip. Без переменной группу не меняем — Ctrl-C
#: из терминала обязан доходить.
NEW_PGROUP_ENV = "CHAROITE_INSTALL_NEW_PGROUP"

#: Сигналы, которые считаем отменой установки: закрытие приложения (SIGTERM) и
#: оборванный терминал (SIGHUP). Ctrl-C ловится обычным `KeyboardInterrupt`.
CANCEL_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class InstallCancelled(BaseException):
    """Установку прервали. Наследник `BaseException`, а не `Exception`: обработчик
    сигнала бросает её поверх любого шага, и внутренние `except Exception` (в том
    числе `SystemExit` вокруг загрузки набора голосов) не должны её проглотить."""


class InstallBusy(Exception):
    """Машина или каталог движка заняты другой работой; текст — чем именно."""


def busy_now(root: pathlib.Path) -> list[str]:
    """Чем занята машина глазами установщика: разбор встреч, живая запись, мутация."""
    return wait_for_idle.busy_now(MeetingStatusStore(root), root)


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
    head = lock.read_text(encoding="utf-8")[:4000]
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

    Прежнее окружение живёт до последнего шага. Откат — на ЛЮБОМ исключении (в том
    числе отмене `BaseException`), а не только `OSError`: сигнал между двумя
    переименованиями иначе оставлял бы окружение в `.old`, а `home` пустым.
    Решение об откате — по состоянию каталогов, как в `sweep`: `home` нет, `.old`
    есть — вернуть. Прежнее, оставшееся после удачной замены, убирает `finally`
    установки или следующий `sweep`."""
    old = home.with_name(f".{home.name}.old-{os.getpid()}")
    if home.exists():
        os.rename(home, old)
    try:
        os.rename(new, home)
    except BaseException:
        if not home.exists() and old.exists():
            os.rename(old, home)
        raise
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
    return {"ok": True, "reason": ""}


def enter_process_group(env=None) -> bool:
    """Лидер группы процессов — только по явному признаку от приложения.

    SIGKILL группе тогда гасит и pip (установщик и его дети — одна группа). Без
    переменной группу не трогаем: запуск из терминала обязан оставить Ctrl-C
    работающим как обычно."""
    env = os.environ if env is None else env
    if env.get(NEW_PGROUP_ENV) != "1":
        return False
    os.setpgid(0, 0)
    return True


def _cancel(signum, frame) -> None:
    """Обработчик отмены: `BaseException`, а не `SystemExit`.

    `get_models._install_diar_bundle` ловит `SystemExit` вокруг загрузки набора —
    отмена, бросившая `SystemExit(EXIT_INSTALL_CANCELLED)`, прочиталась бы там
    ошибкой загрузки и вернула бы код 1 вместо честного «отменено»."""
    raise InstallCancelled(f"получен сигнал {signum}")


def install_signals() -> dict:
    """Обработчики отмены — на все режимы, включая `--check`: отменённая проверка
    тоже честное «отменено». Возвращает прежние, их возвращает `main` на выходе."""
    return {s: signal.signal(s, _cancel) for s in CANCEL_SIGNALS}


def restore_signals(previous: dict) -> None:
    for s, handler in previous.items():
        signal.signal(s, handler)


def plan(name: str, root: pathlib.Path) -> int:
    """План установки одной строкой JSON — без сети и без запуска движка."""
    spec = ENGINES[name]
    cfg = config_loader.load_user_or_example(root) or {}
    payload = {
        "machine": machine_fitness(spec.lock),
        "network": list(NETWORK),
        "license": {"name": get_models.NEMOTRON_LICENSE[0], "url": get_models.NEMOTRON_LICENSE[1]},
        "sizes_mb": {
            "environment": ENVIRONMENT_MB,
            "weights": get_models.NEMOTRON.size_mb,
            "voice_set": (get_models.MODELS[get_models.DEFAULT].size_mb
                          + get_models.SEGMENTATION[get_models.SEG_DEFAULT].size_mb),
        },
        "engine": diarize_nemotron.engine_state(root, cfg),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def ensure_voice_set(root: pathlib.Path) -> None:
    """Недостающий набор голосов sherpa — запасной движок пересборки.

    Ставится тем же загрузчиком `get_models`, что `--diar`: веса нужны и без
    окружения Nemotron — на них уходит пересборка, если движок не разметил. Отказ
    загрузки — `SystemExit` из `get_models.download`, его переводит `main`."""
    targets = (
        (get_models.diar_target(root), get_models.MIN_BYTES,
         get_models.MODELS[get_models.DEFAULT]),
        (get_models.seg_target(root), get_models.SEG_MIN_BYTES,
         get_models.SEGMENTATION[get_models.SEG_DEFAULT]),
    )
    for dest, min_bytes, model in targets:
        if get_models.check(dest, min_bytes=min_bytes) is None:
            print(f"набор голосов: {dest} уже на месте")
            continue
        print(f"набор голосов: {dest.name}…")
        get_models.download(model.url, dest, model.size_mb, sha256=model.sha256)


def install(name: str, root: pathlib.Path) -> int:
    spec = ENGINES[name]
    busy = busy_now(root)
    if busy:
        raise InstallBusy(", ".join(busy))
    check_machine(spec.lock)
    home = spec.home(root)
    home.parent.mkdir(parents=True, exist_ok=True)
    with one_install(home):
        sweep(home)
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


def main(argv: list[str] | None = None) -> int:
    harden_umask()   # окружение и веса под корнем данных — только владельцу
    enter_process_group()   # лидер группы — только по признаку от приложения
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
    # Обработчики отмены — до работы и на все режимы: отменённый --check или --plan
    # тоже честное «отменено», а не обрыв без уборки.
    previous = install_signals()
    try:
        if args.plan:
            return plan(args.engine, root)
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
    except Refused as e:
        print(f"не поставлено: {e}", file=sys.stderr)
        return 1
    except SystemExit as e:
        # Загрузчик весов (`get_models.download`) отказывает `SystemExit` с текстом:
        # для вызывающего это «не поставлено» кодом 1. Чужие коды (0 от --help)
        # не переводим — пусть уходят как есть.
        if e.code in (0, None):
            raise
        print(f"не поставлено: {get_models._exit_text(e)}", file=sys.stderr)
        return 1
    finally:
        restore_signals(previous)


if __name__ == "__main__":
    sys.exit(main())
