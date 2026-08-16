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

import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-runtime.lock"
INPUT = ROOT / "requirements-runtime.in"

#: Пресеты STT под конкретное железо: ставятся отдельно, в бандл не входят.
SKIP = ("mlx-whisper", "parakeet-mlx")


def runtime_deps() -> list[str]:
    """Рантайм-зависимости из pyproject — тем же правилом, что у сборки."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"dependencies = \[(.*?)\n\]", text, re.S)
    if not block:
        raise SystemExit("в pyproject.toml не нашлась секция dependencies")
    out = []
    for line in block.group(1).splitlines():
        m = re.search(r'"([^"]+)"', line)
        if not m:
            continue
        dep = m.group(1)
        if any(dep.startswith(s) for s in SKIP):
            continue
        out.append(dep.split(";")[0].strip())
    return out


def main() -> int:
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
