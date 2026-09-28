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

    .venv/bin/python scripts/lock_runtime_deps.py

Требует `uv` (быстрее и не тянет pip-tools). Результат коммитится.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

# Вставка — только чтобы импортировать сам канон путей. LOCK и INPUT — файлы
# РЕПОЗИТОРИЯ, то есть корень КОДА: канон считает его от положения файла.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from charoite_paths import code_root  # noqa: E402

ROOT = code_root(__file__)
LOCK = ROOT / "requirements-runtime.lock"
INPUT = ROOT / "requirements-runtime.in"

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
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    if "dependencies" not in project:
        raise SystemExit("в pyproject.toml нет [project].dependencies")
    return list(project["dependencies"])


def runtime_deps() -> list[str]:
    """Рантайм-зависимости для lock — проекция объявления: пресеты `SKIP` сняты,
    маркер отрезан. Вход `requirements-runtime.in` пишется отсюда."""
    return [dep.split(";")[0].strip() for dep in declared_deps()
            if not any(dep.startswith(s) for s in SKIP)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args()   # аргументов нет: без них — работа, --help печатает справку
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("нужен uv: brew install uv (или pipx install uv)")

    deps = runtime_deps()
    INPUT.write_text(
        "# Сгенерировано scripts/lock_runtime_deps.py из pyproject.toml.\n"
        "# Правьте pyproject, не этот файл.\n" + "\n".join(deps) + "\n",
        encoding="utf-8")

    # Платформа сборки бандла — macOS arm64, python 3.12 (см. release-app.yml
    # и build_embedded_python.sh). Лочим именно под неё: колёса разные.
    # Пути ОТНОСИТЕЛЬНЫЕ: uv вписывает свою команду в шапку lock-файла, а
    # абсолютный путь сборщика — это домашний каталог человека в публичном
    # репозитории.
    cmd = [uv, "pip", "compile", INPUT.name,
           "--generate-hashes", "--python-version", "3.12",
           "--python-platform", "macos", "-o", LOCK.name]
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode

    body = LOCK.read_text(encoding="utf-8")
    LOCK.write_text(
        "# Сгенерировано scripts/lock_runtime_deps.py — НЕ правьте руками.\n"
        "# Зачем: сборка встроенного python ставит зависимости с\n"
        "# --require-hashes, иначе в подписанный бандл уезжает то, что\n"
        "# лежало на PyPI в минуту сборки (аудит 16.08).\n" + body,
        encoding="utf-8")
    print(f"готово: {LOCK.relative_to(ROOT)} ({len(body.splitlines())} строк)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
