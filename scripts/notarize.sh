#!/bin/bash
# Нотаризация артефакта у Apple и прошивка билета (staple).
#
#   scripts/notarize.sh <что_отправить> <что_прошить> <key.p8> <key_id> <issuer_id>
#
# Отправляем zip приложения (или сам DMG), прошиваем .app (или тот же DMG):
# zip Apple принимает, но билет в zip не кладётся — его получает бандл
# внутри, поэтому zip после прошивки пересобирается (release-app.yml).
#
# Учётные данные — ключ App Store Connect API (роль Developer достаточна),
# а не Apple ID с паролем: без 2FA-танцев в неинтерактивном прогоне и
# отзывается одним кликом. Ключ живёт только в $RUNNER_TEMP на время прогона.
#
# Вердикт ждём здесь же (--wait): при отказе печатаем журнал Apple — в нём
# по именам файлов написано, что именно не так (неподписанный .so, нет
# hardened runtime, нет метки времени). Без журнала отказ нечитаем.
set -euo pipefail

SUBMIT="${1:?что отправить}"
STAPLE="${2:?что прошить}"
KEY="${3:?путь к ключу .p8}"
KEY_ID="${4:?key id}"
ISSUER="${5:?issuer id}"

[ -f "$SUBMIT" ] || { echo "нет файла $SUBMIT" >&2; exit 1; }
[ -e "$STAPLE" ] || { echo "нет объекта $STAPLE" >&2; exit 1; }
[ -f "$KEY" ] || { echo "нет ключа $KEY" >&2; exit 1; }
command -v jq >/dev/null || { echo "нужен jq" >&2; exit 1; }

OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

echo "нотаризация $(basename "$SUBMIT"): отправляю Apple и жду вердикт…"
# Таймаут меньше таймаута job: упасть с журналом лучше, чем оборваться молча.
set +e
xcrun notarytool submit "$SUBMIT" \
    --key "$KEY" --key-id "$KEY_ID" --issuer "$ISSUER" \
    --wait --timeout 40m --output-format json > "$OUT"
rc=$?
set -e
ID="$(jq -r '.id // empty' < "$OUT" 2>/dev/null || true)"
STATUS="$(jq -r '.status // empty' < "$OUT" 2>/dev/null || true)"
echo "  submission ${ID:-<нет id>}: ${STATUS:-<нет статуса>} (rc=$rc)"

if [ "$rc" -ne 0 ] || [ "$STATUS" != "Accepted" ]; then
    echo "нотаризация НЕ прошла" >&2
    if [ -n "$ID" ]; then
        echo "--- журнал Apple ---" >&2
        xcrun notarytool log "$ID" --key "$KEY" --key-id "$KEY_ID" --issuer "$ISSUER" >&2 || true
    else
        cat "$OUT" >&2 || true
    fi
    exit 1
fi

xcrun stapler staple "$STAPLE"
xcrun stapler validate "$STAPLE"
echo "билет прошит: $STAPLE"
