#!/bin/bash
# Страж обезличивания публичного репозитория.
#
# Раньше жил только в .git/hooks/pre-commit — а git хуки не переносит. На
# новой машине, в свежем клоне и у любого контрибьютора защиты не было вовсе.
# Теперь он в репозитории и подключён через .pre-commit-config.yaml.
#
# Сам список маркеров приватен и лежит вне git (~/.config/charoite/private_markers.txt):
# перечень того, что нельзя публиковать, сам по себе — чувствительные данные.
set -uo pipefail

MARKERS="${CHAROITE_MARKERS:-$HOME/.config/charoite/private_markers.txt}"

if [ ! -f "$MARKERS" ]; then
    # fail-closed на машине автора и мягкий пропуск в CI и у контрибьюторов:
    # у них приватного списка нет и быть не должно, а блокировать им коммиты
    # мы не вправе.
    if [ -n "${CI:-}" ]; then
        echo "список маркеров недоступен в CI — проверка пропущена"
        exit 0
    fi
    echo "❌ список маркеров отсутствует — fail-closed" >&2
    exit 1
fi

PATTERN=$(grep -v '^[[:space:]]*$' "$MARKERS" | paste -sd'|' -)
ADDED=$(git diff --cached -U0 | grep -E "^\+" || true)
HITS=$(echo "$ADDED" | grep -icE "$PATTERN" || true)

if [ "$HITS" -gt 0 ]; then
    echo "❌ КОММИТ ЗАБЛОКИРОВАН: $HITS строк с личными/банковскими маркерами:" >&2
    echo "$ADDED" | grep -iE "$PATTERN" | head -5 >&2
    echo "Обезличь (имена/системы/пути) и повтори. Список: $MARKERS" >&2
    exit 1
fi

AUTHOR=$(git config user.email)
if [ -z "${CI:-}" ] && [ "$AUTHOR" != "charoiteai@gmail.com" ]; then
    echo "❌ Автор коммита $AUTHOR ≠ charoiteai@gmail.com (публичный репо!)" >&2
    exit 1
fi
exit 0
