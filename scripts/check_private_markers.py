#!/usr/bin/env python3
"""Страж обезличивания публичного репозитория.

Раньше жил только в .git/hooks/pre-commit — а git хуки не переносит: на новой
машине, в свежем клоне и у любого контрибьютора защиты не было вовсе. Теперь
он в репозитории и подключён через .pre-commit-config.yaml.

Сам список маркеров приватен и лежит вне git (~/.config/charoite/private_markers.txt):
перечень того, что нельзя публиковать, сам по себе — чувствительные данные.

Почему Python, а не grep: коротким аббревиатурам нужны границы слова, иначе
трёхбуквенный маркер находится внутри обычных слов («слЕПАя зона») и страж
блокирует коммит на ровном месте. А `\\b` понимает GNU grep, но не BSD на
macOS; `[[:<:]]` — наоборот. Страж, который врёт, быстро начинают обходить.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

# До этой длины маркер считается аббревиатурой и ищется по границам слова.
SHORT_MARKER = 4


def markers_path() -> pathlib.Path:
    env = os.environ.get("CHAROITE_MARKERS")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".config" / "charoite" / "private_markers.txt"


def build_pattern(markers: list[str]) -> re.Pattern[str]:
    parts = []
    for m in markers:
        esc = re.escape(m)
        parts.append(rf"\b{esc}\b" if len(m) <= SHORT_MARKER else esc)
    return re.compile("|".join(parts), re.IGNORECASE)


def main() -> int:
    path = markers_path()
    if not path.exists():
        # fail-closed на машине автора и мягкий пропуск в CI и у контрибьюторов:
        # приватного списка у них нет и быть не должно.
        if os.environ.get("CI"):
            print("список маркеров недоступен в CI — проверка пропущена")
            return 0
        print("❌ список маркеров отсутствует — fail-closed", file=sys.stderr)
        return 1

    markers = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not markers:
        print("❌ список маркеров пуст — fail-closed", file=sys.stderr)
        return 1
    pattern = build_pattern(markers)

    diff = subprocess.run(["git", "diff", "--cached", "-U0"],
                          capture_output=True, text=True).stdout
    added = [ln for ln in diff.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    hits = [ln for ln in added if pattern.search(ln)]

    if hits:
        print(f"❌ КОММИТ ЗАБЛОКИРОВАН: {len(hits)} строк с личными/банковскими маркерами:",
              file=sys.stderr)
        for ln in hits[:5]:
            print(f"  {ln[:160]}", file=sys.stderr)
        print(f"Обезличь (имена/системы/пути) и повтори. Список: {path}", file=sys.stderr)
        return 1

    author = subprocess.run(["git", "config", "user.email"],
                            capture_output=True, text=True).stdout.strip()
    if not os.environ.get("CI") and author != "charoiteai@gmail.com":
        print(f"❌ Автор коммита {author} ≠ charoiteai@gmail.com (публичный репо!)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
