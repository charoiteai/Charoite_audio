#!/usr/bin/env python3
"""Пересобрать `requirements-runtime.lock` — с версиями и хешами.

Встроенный в приложение python-контур ставился так: из `pyproject.toml`
брались диапазоны (`numpy>=1.26,<3`, `requests>=2.31`…) и подавались в
`pip install` без единого хеша. Значит в подписанный бандл, который
уезжает всем пользователям, попадало то, что лежало на PyPI в минуту
сборки, — вместе с транзитивными зависимостями, которых не видит ни
dependabot (у него диапазоны), ни dependency-review. Тот же урок про
CPython в `build_embedded_python.sh` уже выучен и записан там прямо в
комментарии — до пакетов он не дошёл (аудит 16.08).

Список берётся из `pyproject.toml` ровно тем же правилом, что и раньше,
чтобы не разъехаться с ним; тест `tests/test_runtime_lock.py` следит,
что lock не отстал от манифеста.

Вторая цель — окружение движка Nemotron (№474): `requirements-nemotron.lock`,
вход — одна строка из `MLX_AUDIO_VERSION` модуля движка. Его ставит
`scripts/install_engine.py nemotron` тем же `--require-hashes`. Команда
`uv pip compile` у целей одна (`compile_lock`): платформа и версия Python
у бандла и движка общие — один интерпретатор python-build-standalone.

    .venv/bin/python scripts/lock_runtime_deps.py            # бандл (runtime)
    .venv/bin/python scripts/lock_runtime_deps.py nemotron   # движок

Требует `uv` (быстрее и не тянет pip-tools). Результат коммитится.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable

# Вставка — только чтобы импортировать сам канон путей. Входы и локи целей —
# файлы РЕПОЗИТОРИЯ, то есть корень КОДА: канон считает его от положения файла.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_paths import code_root  # noqa: E402

CODE = code_root(__file__)

#: Под какой Python лочим обе цели: бандл и окружение движка живут на одном
#: python-build-standalone (`build_embedded_python.sh`). Установщик движка
#: читает версию из шапки лока — из того, что ставит, а не отсюда.
PYTHON_VERSION = "3.12"

#: Пресеты STT под конкретное железо: ставятся отдельно, в бандл не входят.
SKIP = ("mlx-whisper", "parakeet-mlx")


#: Имя дистрибутива в начале строки требования — грамматика PEP 508 (`identifier`):
#: буквы и цифры по краям, внутри ещё `.`, `_`, `-`. За именем идут extras, версии
#: или маркер — их проекция имени не читает.
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def dist_name(requirement: str) -> str:
    """Строка требования → нормализованное имя дистрибутива по PEP 503: регистр
    снят, серии `-`, `_`, `.` свёрнуты в `-`. Одна проекция на проект: её зовут
    lock-тест и гейт колеса пакета графа (№446), чтобы одно объявление не судилось
    двумя разными правилами имени."""
    m = _REQUIREMENT_NAME.match(requirement.strip())
    if not m:
        raise ValueError(f"не требование PEP 508 — имени нет: {requirement!r}")
    return re.sub(r"[-_.]+", "-", m.group(0)).lower()


def declared_deps() -> list[str]:
    """`[project].dependencies` корневого манифеста как есть: маркеры и пресеты
    `SKIP` на месте. Один читатель объявления корня — `tomllib`; `runtime_deps`
    и гейт колеса пакета графа (№446) берут строки отсюда."""
    project = tomllib.loads((CODE / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    if "dependencies" not in project:
        raise SystemExit("в pyproject.toml нет [project].dependencies")
    return list(project["dependencies"])


def runtime_deps() -> list[str]:
    """Рантайм-зависимости для lock — проекция объявления: пресеты `SKIP` сняты,
    маркер отрезан. Вход `requirements-runtime.in` пишется отсюда."""
    return [dep.split(";")[0].strip() for dep in declared_deps()
            if not any(dep.startswith(s) for s in SKIP)]


def nemotron_deps() -> list[str]:
    """Вход лока движка — версия стыка из `MLX_AUDIO_VERSION` модуля движка.
    Читается разбором исходника, не импортом: скрипт не тянет обвязку движка,
    а версию знает одно место — модуль, который с ней стыкуется."""
    source = CODE / "src" / "diarize_nemotron.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "MLX_AUDIO_VERSION" for t in node.targets)):
            return [f"mlx-audio=={ast.literal_eval(node.value)}"]
    raise SystemExit(f"в {source.relative_to(CODE)} нет MLX_AUDIO_VERSION")


@dataclasses.dataclass(frozen=True)
class Target:
    """Цель лока: вход, выход, откуда список и зачем хеши — последнее уходит
    в шапки файлов, чтобы читатель лока видел, кто его ставит."""
    input: pathlib.Path
    lock: pathlib.Path
    deps: Callable[[], list[str]]
    source: str
    why: str
    #: Минимальная macOS колёс цели. Алиас `macos` у uv 0.9.20 — это 13.0, а
    #: колёса mlx собраны начиная с 14.0: без поднятой цели резолв падает.
    #: Пусто — умолчание uv (бандл). Строка `min-macos:` уходит в шапку лока,
    #: установщик сверяет её с машиной.
    min_macos: str = ""


TARGETS = {
    "runtime": Target(
        CODE / "requirements-runtime.in", CODE / "requirements-runtime.lock", runtime_deps,
        source="# Сгенерировано scripts/lock_runtime_deps.py из pyproject.toml.\n"
               "# Правьте pyproject, не этот файл.\n",
        why="# Зачем: сборка встроенного python ставит зависимости с\n"
            "# --require-hashes, иначе в подписанный бандл уезжает то, что\n"
            "# лежало на PyPI в минуту сборки (аудит 16.08).\n"),
    "nemotron": Target(
        CODE / "requirements-nemotron.in", CODE / "requirements-nemotron.lock", nemotron_deps,
        source="# Сгенерировано scripts/lock_runtime_deps.py nemotron из MLX_AUDIO_VERSION\n"
               "# в src/diarize_nemotron.py. Правьте версию там, не этот файл.\n",
        why="# Зачем: окружение движка Nemotron ставит scripts/install_engine.py\n"
            "# с --require-hashes --no-deps — ровно этот список и ничего с PyPI\n"
            "# в минуту установки (№474).\n",
        min_macos="14.0"),
}


def compile_lock(target: Target, uv: str) -> int:
    """Вход цели → `uv pip compile` с хешами → шапка «кто ставит». Одна команда
    на все цели: платформа и Python общие."""
    target.input.write_text(target.source + "\n".join(target.deps()) + "\n", encoding="utf-8")

    # Платформа сборки бандла — macOS arm64 (см. release-app.yml и
    # build_embedded_python.sh). Лочим именно под неё: колёса разные.
    # Пути ОТНОСИТЕЛЬНЫЕ: uv вписывает свою команду в шапку lock-файла, а
    # абсолютный путь сборщика — это домашний каталог человека в публичном
    # репозитории.
    cmd = [uv, "pip", "compile", target.input.name,
           "--generate-hashes", "--python-version", PYTHON_VERSION,
           "--python-platform", "macos", "-o", target.lock.name]
    env = dict(os.environ)
    if target.min_macos:
        env["MACOSX_DEPLOYMENT_TARGET"] = target.min_macos
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=CODE, env=env)
    if proc.returncode != 0:
        return proc.returncode

    body = target.lock.read_text(encoding="utf-8")
    platform_line = f"# min-macos: {target.min_macos}\n" if target.min_macos else ""
    target.lock.write_text(
        "# Сгенерировано scripts/lock_runtime_deps.py — НЕ правьте руками.\n"
        + target.why + platform_line + body, encoding="utf-8")
    print(f"готово: {target.lock.name} ({len(body.splitlines())} строк)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default="runtime", choices=sorted(TARGETS),
                    help="какой лок пересобрать (по умолчанию runtime — бандл приложения)")
    args = ap.parse_args(argv)
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("нужен uv: brew install uv (или pipx install uv)")
    return compile_lock(TARGETS[args.target], uv)


if __name__ == "__main__":
    sys.exit(main())
