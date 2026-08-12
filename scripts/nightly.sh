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
STARTED=$(date '+%F %T')
# Куда кладём машиночитаемый итог. Логи launchd живут в /tmp и исчезают при
# перезагрузке — по ним нельзя отличить «ночью ничего не делалось» от
# «файл стёрся». Статус нужен рядом с данными и должен переживать ребут:
# его читает приложение и показывает на «Сегодня».
STATUS_DIR="${CHAROITE_ROOT:-$PWD}/logs"
STATUS="$STATUS_DIR/nightly.json"
FAILED=""
mkdir -p "$STATUS_DIR"

# Пишем статус в любом случае — в том числе если прогон оборвут на середине:
# незавершённый прогон это тоже факт, и человек должен его увидеть.
write_status() {
  local state="$1"
  printf '{"started":"%s","finished":"%s","state":"%s","rc":%s,"failed":"%s"}\n' \
    "$STARTED" "$(date '+%F %T')" "$state" "$rc" "$FAILED" > "$STATUS"
}
trap 'write_status interrupted' INT TERM

echo "=== nightly $(date '+%F %T') ==="
# право на слияние — у конфига (sufler.tier3_auto_apply), не у cron:
# --auto сливает только при true, иначе обратимые пометки (--mark)
$PY scripts/tier3_cores.py --all-graphs --auto || { echo "❌ РЕВИЗИЯ ЯДЕР УПАЛА (код $?)"; rc=1; FAILED="$FAILED ревизия-ядер"; }
echo "--- dossiers ---"
# Сводки по темам поверх ядер + индекс для поиска. Инкрементально:
# пересобираются только темы, у которых изменился хоть один источник.
$PY scripts/nightly_dossier.py --all-graphs || { echo "⚠️ сборка досье не отработала"; FAILED="$FAILED досье"; }
echo "--- dossier review (cloud, optional) ---"
# Второй проход по свежим досье: облако видит связи между источниками,
# которых локальная модель не замечает. Правит сам только при
# sufler.cloud_edit_graph: true, иначе пишет отчёт-рекомендации.
$PY scripts/nightly_dossier_review.py --all-graphs || echo "⚠️ ревизия досье не отработала"
echo "--- claude cores review ---"
# облачный взгляд на ядра (Opus): отчёт-рекомендации, ничего не правит.
# Выключено sufler.cloud_enrich/SUFLER_NO_CLOUD — шаг молчит.
$PY scripts/nightly_claude_cores.py || echo "⚠️ облачная ревизия не отработала"
echo "--- dedup graph files ---"
# Побайтовые копии документов встречи (оригинал в «Документация», копия в
# «Встречи-архив» для Finder) связываются жёсткими ссылками: место и
# синхронизация iCloud перестают удваиваться, оба пути остаются рабочими.
# Не путать с ревизией ядер выше — та сшивает СМЫСЛОВЫЕ дубли тем.
$PY scripts/dedup_graph.py || { echo "⚠️ дедупликация файлов не отработала"; FAILED="$FAILED дедуп"; }

echo "--- morning brief ---"
$PY scripts/morning_brief.py || { echo "❌ УТРЕННИЙ БРИФ УПАЛ (код $?)"; rc=1; FAILED="$FAILED утренний-бриф"; }
echo "--- memory bench ---"
# не валит джобу: exit 1 бенча = сигнал деградации в логе, не авария
$PY scripts/memory_bench.py || echo "⚠️ БЕНЧ ПАМЯТИ ПРОСЕЛ — смотри выше"
echo "=== done $(date '+%F %T'), rc=$rc ==="
FAILED="${FAILED# }"
if [ "$rc" -eq 0 ]; then write_status ok; else write_status failed; fi
exit $rc
