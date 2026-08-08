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

# Python-контур внутрь бандла: с ним установка перестаёт начинаться с
# терминала (git clone → venv → pip). Кладём, если он собран
# scripts/build_embedded_python.sh; без него приложение работает по-старому
# от .venv рядом с репозиторием — сборка не должна падать из-за того, что
# кто-то не собрал контур.
EMBEDDED="build/embedded-python"
if [ -x "$EMBEDDED/bin/python3" ]; then
    echo "вкладываю python-контур ($(du -sh "$EMBEDDED" | cut -f1))…"
    # -c: APFS-клон вместо копии — мгновенно и без второго гигабайта на диске.
    cp -Rc "$EMBEDDED" "$APP/Contents/Resources/python"
    # Байт-кеш чужих машин в поставке не нужен: это мегабайты мусора и
    # лишние отличия между сборками.
    find "$APP/Contents/Resources/python" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
else
    echo "python-контур не собран (scripts/build_embedded_python.sh) — бандл без него"
fi

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
	<string>__BUILD__</string>
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
	<key>NSAudioCaptureUsageDescription</key>
	<string>Звук звонка записывается локально вместо установки стороннего драйвера: расшифровка не покидает этот Mac.</string>
	<!-- charoite:// — управление из Shortcuts/терминала: record/start|stop|toggle,
	     meeting/<id>, tasks, today. App Intents здесь не работают: их метаданные
	     извлекает Xcode-фаза, которой у swift build нет — Shortcuts видел бы
	     пустоту. URL scheme работает в любой сборке. -->
	<key>CFBundleURLTypes</key>
	<array>
		<dict>
			<key>CFBundleURLName</key>
			<string>ai.charoite.app.url</string>
			<key>CFBundleURLSchemes</key>
			<array><string>charoite</string></array>
		</dict>
	</array>
</dict>
</plist>
PLIST

/usr/bin/sed -i '' "s/__VERSION__/$VERSION/" "$APP/Contents/Info.plist"
# CFBundleVersion был вечной единицей. Именно по нему система и любой механизм
# автообновления решают, какая сборка новее, — с константой все версии
# выглядели одинаково. Счётчик коммитов монотонно растёт и не требует ведения.
BUILD="$(git rev-list --count HEAD 2>/dev/null || true)"
BUILD="${BUILD:-1}"
/usr/bin/sed -i '' "s/__BUILD__/$BUILD/" "$APP/Contents/Info.plist"

# Подпись: Developer ID, если он есть в связке, иначе ad-hoc.
#
# Это не про дистрибуцию, а про разрешения. У ad-hoc подписи designated
# requirement — это `cdhash H"…"`, то есть привязка к точному хешу бинаря:
# любая пересборка меняет хеш, и macOS считает приложение ДРУГИМ. Выданные
# доступы (микрофон, а с переходом на Core Audio tap — и системный звук)
# после каждой сборки приходится выдавать заново. С Developer ID requirement
# становится «identifier + команда» и переживает пересборки.
SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null \
    | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
# Вложенные бинарники подписываются ПЕРВЫМИ и по одному.
#
# codesign --deep для такого дерева официально не поддерживается и молча
# оставляет часть .so неподписанными: приложение запускается, а первый же
# импорт numpy падает с «code signature invalid» — уже у пользователя.
sign_embedded() {
    local id="$1" root="$APP/Contents/Resources/python"
    [ -d "$root" ] || return 0
    echo "подписываю вложенный контур…"
    # Порядок важен: сначала библиотеки, потом исполняемые файлы.
    find "$root" \( -name "*.so" -o -name "*.dylib" \) -type f -print0 \
        | xargs -0 -P 8 -n 20 codesign --force --timestamp=none --sign "$id" 2>/dev/null || true
    find "$root/bin" -type f -perm -111 -print0 \
        | xargs -0 -n 10 codesign --force --timestamp=none --sign "$id" 2>/dev/null || true
}

if [ -n "$SIGN_ID" ]; then
    sign_embedded "$SIGN_ID"
    # Без --options runtime: hardened runtime ломает наследование доступа
    # дочерними процессами, а микрофон у нас читает python-демон отдельным
    # процессом — при жёстком рантайме он получает тишину без единой ошибки.
    # Нотаризация нам не нужна, а стабильность requirement даёт сам Developer ID.
    codesign --force --sign "$SIGN_ID" --timestamp=none "$APP"
    echo "подписано: $SIGN_ID"
else
    sign_embedded -
    codesign --force --sign - "$APP"
    echo "ВНИМАНИЕ: Developer ID не найден, подпись ad-hoc —"
    echo "  доступ к микрофону и системному звуку будет слетать при каждой сборке."
fi
echo "готово: $APP"
