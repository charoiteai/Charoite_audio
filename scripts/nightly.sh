#!/bin/bash
# Ночной «сон» Чароита (launchd ru.charoit.tier3, 04:15):
#   1) Tier3-ревизия ядер всех графов (дубли/вложения/пометки, с бэкапами)
#   2) Утренний бриф _Сегодня.md — готовый контекст дня из свежих строк графа
# Бриф идёт ПОСЛЕ ревизии: читает уже причёсанные ядра.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
echo "=== nightly $(date '+%F %T') ==="
$PY scripts/tier3_cores.py --all-graphs --apply
echo "--- morning brief ---"
$PY scripts/morning_brief.py
echo "--- memory bench ---"
# не валит джобу: exit 1 бенча = сигнал деградации в логе, не авария
$PY scripts/memory_bench.py || echo "⚠️ БЕНЧ ПАМЯТИ ПРОСЕЛ — смотри выше"
echo "=== done $(date '+%F %T') ==="
