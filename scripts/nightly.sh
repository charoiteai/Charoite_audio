#!/bin/bash
# Ночной «сон» Чароита (launchd ru.charoit.tier3, 04:15):
#   1) Tier3-ревизия ядер всех графов (дубли/вложения/пометки, с бэкапами)
#   2) Утренний бриф _Сегодня.md — готовый контекст дня из свежих строк графа
# Бриф идёт ПОСЛЕ ревизии: читает уже причёсанные ядра.
#
# Коды возврата. Шаги независимы: упавшая ревизия не отменяет бриф, поэтому
# каждый шаг ловится отдельно и только помечает прогон неуспешным. Без этого
# джоба всегда заканчивалась echo, то есть exit 0, и launchd показывал зелёный
# прогон даже когда ночью не делалось вообще ничего.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
rc=0
echo "=== nightly $(date '+%F %T') ==="
# право на слияние — у конфига (sufler.tier3_auto_apply), не у cron:
# --auto сливает только при true, иначе обратимые пометки (--mark)
$PY scripts/tier3_cores.py --all-graphs --auto || { echo "❌ РЕВИЗИЯ ЯДЕР УПАЛА (код $?)"; rc=1; }
echo "--- claude cores review ---"
# облачный взгляд на ядра (Opus): отчёт-рекомендации, ничего не правит.
# Выключено sufler.cloud_enrich/SUFLER_NO_CLOUD — шаг молчит.
$PY scripts/nightly_claude_cores.py || echo "⚠️ облачная ревизия не отработала"
echo "--- morning brief ---"
$PY scripts/morning_brief.py || { echo "❌ УТРЕННИЙ БРИФ УПАЛ (код $?)"; rc=1; }
echo "--- memory bench ---"
# не валит джобу: exit 1 бенча = сигнал деградации в логе, не авария
$PY scripts/memory_bench.py || echo "⚠️ БЕНЧ ПАМЯТИ ПРОСЕЛ — смотри выше"
echo "=== done $(date '+%F %T'), rc=$rc ==="
exit $rc
