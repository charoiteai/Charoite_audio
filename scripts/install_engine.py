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

    <python приложения> scripts/install_engine.py nemotron          # поставить
    <python приложения> scripts/install_engine.py nemotron --check  # что стоит, без сети

У приложения это `Charoite.app/Contents/Resources/python/bin/python3`; команду
печатают доктор и шапка стенограммы, если движок выбран, а окружения нет
(`diarize_nemotron.install_command`: питон приложения, когда его можно узнать).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import wave
from collections.abc import Callable

# Вставки — канон путей и модуль движка (src), загрузчик весов (scripts).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from charoite_paths import code_root, harden_umask, resolve_root  # noqa: E402
import diarize_nemotron  # noqa: E402
import file_locks  # noqa: E402
import foreign_python  # noqa: E402
import get_models  # noqa: E402

CODE = code_root(__file__)

#: Куда пойдёт сеть — печатается до соединения, как у get_models.
NETWORK = ("PyPI (pypi.org, files.pythonhosted.org) — пакеты из лока с хешами",
           "Hugging Face (huggingface.co) — веса с проверкой sha256")

#: Отпечаток python-build-standalone: сборка идёт с префиксом /install, и он
#: остаётся в данных sysconfig. У Homebrew, системного Python и venv поверх них —
#: путь установки: копию такого интерпретатора не унести в другой каталог.
STANDALONE_PREFIX = "/install"

PROBE_TIMEOUT_S = 180.0


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
    cmd = [str(python), "-I", "-m", "pip", "install", "--require-hashes", "--no-deps", "--no-cache-dir",
           "--disable-pip-version-check", "--no-warn-script-location", "-r", str(lock)]
    proc = subprocess.run(cmd, env=foreign_python.clean_env(os.environ), stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise Refused(f"pip не поставил пакеты лока (код {proc.returncode}) — вывод выше")


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

    Прежнее окружение живёт до последнего шага; сбой на замене возвращает его."""
    old = home.with_name(f".{home.name}.old-{os.getpid()}")
    had_old = home.exists()
    if had_old:
        os.rename(home, old)
    try:
        os.rename(new, home)
    except OSError:
        if had_old:
            os.rename(old, home)
        raise
    if had_old:
        shutil.rmtree(old, ignore_errors=True)


@contextlib.contextmanager
def one_install(home: pathlib.Path):
    """Одна установка движка за раз: замок рядом с каталогом окружения.

    Без него две установки меняли бы каталог наперегонки, а уборка остатков
    (`sweep`) сносила бы временный каталог живой соседки."""
    fd = os.open(home.with_name(f".{home.name}.install.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as f:
        if not file_locks.acquire_exclusive(f):
            raise Refused(f"замок установки {home.name} не взят: идёт другая установка "
                          f"(или том без flock) — дождитесь её")
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


def install(name: str, root: pathlib.Path) -> int:
    spec = ENGINES[name]
    check_machine(spec.lock)
    home = spec.home(root)
    home.parent.mkdir(parents=True, exist_ok=True)
    with one_install(home):
        sweep(home)
        staging = home.with_name(f".{home.name}.new-{os.getpid()}")
        print(f"ставлю движок {name} в {home}\nсеть:")
        for line in NETWORK:
            print(f"  {line}")
        try:
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
          f"чей интерпретатор в работе, показывает scripts/doctor.py")
    return 0


def check(name: str, root: pathlib.Path) -> int:
    """Что стоит — без сети: окружение, версия mlx-audio, веса (через пробу движка)."""
    spec = ENGINES[name]
    python = spec.python(root)
    if not python.exists():
        print(f"окружения движка нет: {spec.home(root)} — {diarize_nemotron.install_command()}")
        return 1
    out = spec.probe("", root=root)
    if not out.ok:
        print(f"окружение {spec.home(root)} есть, но движку нечем работать ({out.kind}): {out.reason}")
        return 1
    print(f"движок {name} на месте: {python}, mlx-audio {out.payload['mlx_audio']}, веса {spec.weights(root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    harden_umask()   # окружение и веса под корнем данных — только владельцу
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("engine", choices=sorted(ENGINES), help="какой движок ставить")
    ap.add_argument("--check", action="store_true", help="только проверить, что стоит (без сети)")
    args = ap.parse_args(argv)
    root = resolve_root(__file__)
    try:
        return check(args.engine, root) if args.check else install(args.engine, root)
    except Refused as e:
        print(f"не поставлено: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
