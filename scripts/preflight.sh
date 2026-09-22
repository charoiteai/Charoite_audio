#!/bin/bash
# Предполётная сводка ЛОКАЛЬНЫХ условий — то, что не воспроизводится на раннере
# и нужно человеку до круга голов и до приёмки чужой работы: занятость машины,
# статика, полный pytest, swift при правках приложения, мутатор на диапазон.
# Итог машинный: `preflight: ok` или `FAIL: <шаги>` и имена упавших тестов.
#
# Чего здесь НЕТ и почему. Проверка «каждая точка входа ведёт себя по контракту»
# живёт в pytest (`tests/test_entry_points_contract.py`; реестр — `layout_map`,
# контракты — `run_contracts` в docs/design/layout.json): её гоняют и CI, и
# мутатор, а bash-шаг никто бы не проверял. Первый черновик держал её здесь и
# утверждал код отказа, которого в базе ветки не было, — ровно тот класс
# дефектов, ради которого всё затевалось (входной круг №339, DS C1 = GLM C1).
#
# Использование:
#   scripts/preflight.sh [база]          # диапазон база...HEAD, по умолчанию origin/main
#   PREFLIGHT_SKIP=mutation,swift scripts/preflight.sh   # пропустить шаги (повторный прогон)
#   PREFLIGHT_FORCE=1 ...                # идти поверх занятой машины — только с чужого согласия
# Работает и в git worktree: корень данных владельца — основной checkout.
# Bash читает файл по мере исполнения (правка во время прогона сломала прогон
# 22.09) — тело в функции, разобрано целиком до старта. /bin/bash на macOS — 3.2:
# массивов под `set -u` здесь нет намеренно.
main() {
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
PY="${PREFLIGHT_PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "preflight: нет $PY — создайте .venv или укажите PREFLIGHT_PY"; exit 2
fi
BASE="${1:-origin/main}"
if ! git rev-parse --verify -q "$BASE^{commit}" >/dev/null; then
  echo "preflight: базы $BASE нет (нефетченный клон?) — назовите базу явно"; exit 2
fi
RANGE="$BASE...HEAD"
NOTHING_TO_CHECK=$("$PY" -c "import sys; sys.path.insert(0, 'src'); import exit_codes; print(exit_codes.EXIT_NOTHING_TO_CHECK)") \
  || { echo "preflight: коды канона не читаются (src/exit_codes.py) — прогон не стартует"; exit 2; }
FAIL=""
SKIPPED=""
T0=$(date +%s)
SKIP=",${PREFLIGHT_SKIP:-},"
WORK=$(mktemp -d -t preflight)
trap 'rm -rf "$WORK"' EXIT
skipped() { case "$SKIP" in *",$1,"*) SKIPPED="$SKIPPED $1"; printf '   – %s пропущен (PREFLIGHT_SKIP)\n' "$1"; return 0;; esac; return 1; }
step() { printf '\n── %s\n' "$1"; }
verdict() { if [ "$1" -ne 0 ]; then FAIL="$FAIL $2"; printf '   ✗ %s (код %s)\n' "$2" "$1"; else printf '   ✓ %s\n' "$2"; fi; }
show() { sed 's/^/   /' "$1" | head -20; }
# «что под судом» — один список на все шаги: диапазон плюс рабочее дерево, включая новые файлы
changed() { { git diff --name-only "$RANGE" -- "$@"; git status --porcelain --untracked-files=all -- "$@" | cut -c4-; } | sort -u; }

# Шаг 0. Машина занята встречей, разбором или ночным циклом — тяжёлые прогоны
# ждут. Сигналы те же, что у мутатора (src/busy_signals.py). Правило «проверять
# перед КАЖДЫМ прогоном» держит только изнутри прогона: 22.09 полный pytest и
# мутатор ушли поверх встречи, начавшейся через пять минут после проверки.
# Корень данных — основной checkout владельца, и из worktree тоже.
COMMON=$(git rev-parse --path-format=absolute --git-common-dir) || { echo "preflight: не git-репозиторий"; exit 2; }
DATA_ROOT="${CHAROITE_ROOT:-$(dirname "$COMMON")}"
busy=$("$PY" -c "import sys, pathlib; sys.path.insert(0, 'src'); import busy_signals; print(', '.join(busy_signals.machine_busy(pathlib.Path(sys.argv[1]))))" "$DATA_ROOT") \
  || { echo "preflight: сигналы занятости не читаются — прогон не стартует (сломанный сигнал ≠ свободная машина)"; exit 3; }
if [ -n "$busy" ] && [ -z "${PREFLIGHT_FORCE:-}" ]; then
  echo "preflight: машина занята ($busy) — тяжёлые шаги отложены; PREFLIGHT_FORCE=1 только с чужого согласия"; exit 3
fi
[ -z "$(git status --porcelain)" ] || echo "   ! рабочее дерево не чистое: шаг 4 (мутатор) судит HEAD, незакоммиченного не видит"

step "1. статика: ruff E9,F · раскладка · маркеры приватности"
"$PY" -m ruff check --select E9,F src/ scripts/ tests/ -q;                        verdict $? ruff
"$PY" scripts/layout_map.py --check > "$WORK/layout.log" 2>&1; rc=$?;  [ $rc -eq 0 ] || show "$WORK/layout.log";  verdict $rc layout
"$PY" scripts/check_private_markers.py --all > "$WORK/markers.log" 2>&1; rc=$?; [ $rc -eq 0 ] || show "$WORK/markers.log"; verdict $rc markers

step "2. pytest (полный набор — включая контракты точек входа)"
if ! skipped pytest; then
  "$PY" -m pytest tests/ -q -p no:cacheprovider > "$WORK/pytest.log" 2>&1; rc=$?
  tail -1 "$WORK/pytest.log"
  # упавшие — по именам: хвост «1 failed» без имени — не отчёт, а загадка
  [ $rc -eq 0 ] || grep -E "^(FAILED|ERROR) " "$WORK/pytest.log" | head -20 | sed 's/^/   /'
  verdict $rc pytest
fi

step "3. swift — если тронут app/ (сборка, тесты и SwiftLint как в swift-tests.yml; app-ios не собирается)"
if skipped swift; then :
elif [ -z "$(changed app/)" ]; then
  echo "   – app/ не тронут"; SKIPPED="$SKIPPED swift(app/ не тронут)"
else
  # Линтер: его СОБСТВЕННЫЙ код возврата плюс отсутствие строк error — так же, как
  # в swift-tests.yml под `set -euo pipefail`. Раньше код глотался `;`, и краш или
  # битый конфиг давали зелёный шаг (круг 2 по №339, DS I2).
  if command -v swiftlint >/dev/null; then
    swiftlint lint --quiet > "$WORK/swiftlint.log" 2>&1; lint=$?
    if [ $lint -ne 0 ] || grep -q "error" "$WORK/swiftlint.log"; then
      grep -E "error|Could not|Unknown" "$WORK/swiftlint.log" | head -10 | sed 's/^/   /'; verdict 1 swiftlint
    else verdict 0 swiftlint; fi
  else
    echo "   – swiftlint не установлен (в CI он гейт)"; SKIPPED="$SKIPPED swiftlint(нет бинарника)"
  fi
  if (cd app && swift build --build-tests > "$WORK/swift-build.log" 2>&1); then
    (cd app && swift test --skip-build --filter '^CharoiteAppTests\.' > "$WORK/swift-test.log" 2>&1 \
       && grep -Eq 'Executed [1-9][0-9]* test' "$WORK/swift-test.log"); rc=$?
    grep -E "Executed [0-9]+ tests?" "$WORK/swift-test.log" | tail -1 | sed 's/^/   /'
    [ $rc -eq 0 ] || grep -E "error:| failed " "$WORK/swift-test.log" | head -10 | sed 's/^/   /'
    verdict $rc swift
  else
    grep -E "error:" "$WORK/swift-build.log" | head -10 | sed 's/^/   /'; verdict 1 swift
  fi
fi

step "4. мутация изменённых строк ($RANGE)"
if ! skipped mutation; then
  # корень данных владельца — мутатору явно: сам он берёт корень от своего файла
  # и в worktree не видит лока живой встречи (№339, DS I9 = GLM I1)
  CHAROITE_ROOT="$DATA_ROOT" "$PY" scripts/mutate_check.py --range "$RANGE" > "$WORK/mutation.log" 2>&1; rc=$?
  tail -4 "$WORK/mutation.log" | sed 's/^/   /'
  # «Проверять было нечего» — отдельный КОД канона, а не подстрока в выводе: исходов
  # два (пустой диапазон и нет мутируемых строк), текст у них разный, и grep по
  # одному из них пропускал второй в зелёное (круг 2 по №339, DS C1).
  if [ "$rc" -eq "$NOTHING_TO_CHECK" ]; then SKIPPED="$SKIPPED mutation(проверять нечего)"; else verdict $rc mutation; fi
  # проверено подмножество — тоже неполнота, и она обязана быть в сводке, а не в логе
  cut_off=$(grep -o "СРЕЗАНО [0-9]*" "$WORK/mutation.log" | head -1 | tr -d "СРЕЗАНО ")
  [ -z "$cut_off" ] || SKIPPED="$SKIPPED mutation(срез $cut_off)"
fi

printf '\n══ preflight за %s с: ' "$(( $(date +%s) - T0 ))"
# сводка — одна строка, которую процитирует человек или исполнитель: «ok» только
# когда шли все шаги; пропуск — вслух в той же строке (круг 1 по №339, DS I5)
if [ -n "$FAIL" ]; then echo "FAIL:$FAIL"; exit 1
elif [ -n "$SKIPPED" ]; then echo "неполный — пропущены:$SKIPPED"; exit 0
else echo "ok"; exit 0; fi
}
main "$@"
