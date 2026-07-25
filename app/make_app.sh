#!/bin/bash
# Сборка Charoite.app из SPM-бинаря: swift build -c release → минимальный
# бандл с Info.plist и иконкой → ad-hoc подпись. Итог: ./build/Charoite.app
set -euo pipefail
cd "$(dirname "$0")"

swift build -c release --arch arm64

# версия бандла = последний git-тег (release-please), не зашитая константа:
# приложение перестаёт представляться древней версией
git fetch --tags --quiet 2>/dev/null || true   # свежие release-please теги
VERSION=$(git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')
VERSION=${VERSION:-0.0.0}

APP=build/Charoite.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/arm64-apple-macosx/release/CharoiteApp "$APP/Contents/MacOS/CharoiteApp"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
mkdir -p "$APP/Contents/Resources/ru.lproj"
printf '/* русская локаль — AppKit берёт русские системные меню */\n' \
    > "$APP/Contents/Resources/ru.lproj/InfoPlist.strings"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDisplayName</key>
	<string>Charoite</string>
	<key>CFBundleExecutable</key>
	<string>CharoiteApp</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundleIdentifier</key>
	<string>ai.charoite.app</string>
	<key>CFBundleName</key>
	<string>Charoite</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>__VERSION__</string>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>
	<!-- ВЕРХНИЙ уровень плиста: вложенные куда-либо ключи локализации
	     молча игнорируются, и системные меню остаются английскими -->
	<key>CFBundleDevelopmentRegion</key>
	<string>ru</string>
	<key>CFBundleLocalizations</key>
	<array><string>ru</string><string>en</string></array>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSCalendarsUsageDescription</key>
	<string>Название ближайшей встречи — для кнопки «Бриф» (локально, только чтение).</string>
	<key>NSCalendarsFullAccessUsageDescription</key>
	<string>Название ближайшей встречи — для кнопки «Бриф» (локально, только чтение).</string>
	<key>NSMicrophoneUsageDescription</key>
	<string>Суфлёр слушает встречу локально: распознавание речи не покидает этот Mac.</string>
</dict>
</plist>
PLIST

/usr/bin/sed -i '' "s/__VERSION__/$VERSION/" "$APP/Contents/Info.plist"

codesign --force --sign - "$APP"
echo "готово: $APP"
