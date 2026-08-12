#!/bin/bash
# Установщик Charoite.dmg из собранного бандла.
#
# Zip остаётся — им обновляется уже установленное приложение (распаковка без
# монтирования тома). Но первая установка из zip выглядит как «распакуйте и
# сами перетащите куда-нибудь»: половина пользователей оставляет приложение в
# «Загрузках», и оно живёт там, ломая права macOS при каждой чистке папки.
# DMG показывает окно с двумя иконками и стрелкой — куда класть, вопросов не
# возникает.
#
# Рядом кладём .sha256 на оба файла: обновление внутри приложения проверяет
# скачанное перед тем, как заменить себя.
set -euo pipefail
cd "$(dirname "$0")/.."

APP="app/build/Charoite.app"
OUT="app/build"
DMG="$OUT/Charoite.dmg"
STAGE="$OUT/dmg-stage"

[ -d "$APP" ] || { echo "нет $APP — сначала app/make_app.sh"; exit 1; }

VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
    "$APP/Contents/Info.plist" 2>/dev/null || echo "0.0.0")

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
# -c: APFS-клон вместо копии — 300-мегабайтный контур не копируется побайтно.
cp -Rc "$APP" "$STAGE/Charoite.app"
ln -s /Applications "$STAGE/Applications"

# UDZO — сжатый образ только для чтения: пользователь физически не может
# записать что-то внутрь установщика и потом удивляться, куда это делось.
hdiutil create -volname "Charoite $VERSION" \
    -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG"
rm -rf "$STAGE"

# Подпись образа, если есть Developer ID: без неё Gatekeeper ругается на сам
# DMG ещё до того, как человек доберётся до приложения внутри.
SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
if [ -n "$SIGN_ID" ]; then
    codesign --force --sign "$SIGN_ID" --timestamp=none "$DMG"
    echo "образ подписан: $SIGN_ID"
fi

for f in "$DMG" "$OUT/Charoite.app.zip"; do
    [ -f "$f" ] || continue
    shasum -a 256 "$f" | awk '{print $1}' > "$f.sha256"
    echo "$(basename "$f"): $(du -h "$f" | cut -f1), sha256 $(cut -c1-12 < "$f.sha256")…"
done

echo "готово: $DMG"
