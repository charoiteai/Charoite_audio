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


def tracked_files() -> list[pathlib.Path]:
    """Файлы под учётом git — то, что уже опубликовано."""
    out = subprocess.run(["git", "ls-files", "-z"],
                         capture_output=True, text=True).stdout
    return [pathlib.Path(p) for p in out.split("\0") if p]


SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
               ".zip", ".onnx", ".wav", ".m4a", ".mp3"}


def scan_files(pattern: re.Pattern[str], files: list[pathlib.Path]) -> list[str]:
    """Где в этих файлах маркеры. Возвращает «путь:строка» — без цитаты.

    Диф страж показывает строкой: там она ещё не опубликована и автору нужно
    видеть, что именно он пишет. Для файлов, которые УЖЕ в репозитории, вывод
    попадает в логи CI и чужие терминалы, поэтому здесь только место — автор
    откроет файл сам.
    """
    hits: list[str] = []
    for f in files:
        if f.suffix.lower() in SKIP_SUFFIX:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue        # бинарь или удалённый файл — не наша забота
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f}:{i}")
    return hits


# Второй рубеж: форматы, а не имена.
#
# Список поимённых маркеров приватен и живёт только на машине автора — в CI
# его нет и быть не должно: перечень того, что мы прячем, сам по себе
# чувствителен, а секреты GitHub вдобавок не отдаются в PR из форков, то
# есть проверка не сработала бы ровно в самом опасном случае.
#
# Поэтому здесь — публичные шаблоны, которые ничего не выдают своим видом,
# но ловят самый частый способ утечки: скопированный кусок конфига, лога или
# пути с рабочей машины. Это ВТОРОЙ рубеж, а не замена первому: фамилию
# коллеги в комментарии поймает только локальный хук.
#
# Синтетические имена («a», «user», «test») пропускаем: примеры и тесты
# обязаны показывать пути, а страж, который ругается на документацию,
# начинает восприниматься как шум.
# Пометка строки, которой разрешено выглядеть как утечка.
PUBLIC_ALLOW = "приватный-образец"
FAKE_USER = r"(?!a/|x/|user/|test/|someone/|you/|me/|ПУТЬ/)"
PUBLIC_PATTERNS: dict[str, str] = {
    "внутренний хост": r"[\w.-]+\.(corp|intranet|internal|lan)\b|[\w-]+-gw-[\w.-]+",
    "почта на непубличном домене": r"[\w.+-]+@[\w-]+\.(ru|local|corp|lan)\b",
    "личный путь": rf"/Users/{FAKE_USER}[a-z][\w-]*/",
    "фамилия с инициалами": r"[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.",
}


def scan_public(files: list[pathlib.Path]) -> list[str]:
    """Находки по публичным шаблонам — «путь:строка: чем сработало»."""
    hits: list[str] = []
    for name, raw in PUBLIC_PATTERNS.items():
        rx = re.compile(raw)
        for f in files:
            if f.suffix.lower() in SKIP_SUFFIX:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                # Явная пометка в самой строке, а не исключённый файл: тесты
                # этого стража обязаны содержать образцы утечек, но глушить
                # файл целиком — значит открыть место, где можно спрятать
                # что угодно. Пометка видна в ревью построчно.
                if PUBLIC_ALLOW in line:
                    continue
                if rx.search(line):
                    hits.append(f"{f}:{i}: {name}")
    return hits


def main() -> int:
    full_only = "--all" in sys.argv
    # Режим CI: только публичные шаблоны, приватного списка там нет.
    if "--public-only" in sys.argv:
        hits = scan_public(tracked_files())
        if hits:
            print("❌ похоже на приватные данные в публичном дереве:", file=sys.stderr)
            for h in hits:
                print(f"  {h}", file=sys.stderr)
            print("Это проверка ФОРМАТОВ. Имена и внутренние названия ловит "
                  "локальный хук — он остаётся главным рубежом.", file=sys.stderr)
            return 1
        print("публичные шаблоны: чисто")
        return 0
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

    # Полный проход по дереву. Диф-проверка ловит то, что пишут сейчас, а это —
    # то, что уже опубликовано: маркер, попавший в main до пополнения списка,
    # иначе не всплывёт никогда. Стоит миллисекунды на двух сотнях файлов.
    stale = scan_files(pattern, tracked_files())
    if stale:
        print(f"❌ приватные маркеры в опубликованном дереве: {len(stale)}",
              file=sys.stderr)
        for place in stale[:10]:
            print(f"  {place}", file=sys.stderr)
        print("Обезличь эти строки. Полный список мест: "
              "python3 scripts/check_private_markers.py --all", file=sys.stderr)
        return 1
    if full_only:
        print(f"дерево чисто: {len(tracked_files())} файлов, маркеров нет")
        return 0

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
