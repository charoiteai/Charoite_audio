#!/bin/bash
# Сборка Charoite.app из SPM-бинаря: swift build -c release → минимальный
# бандл с Info.plist и иконкой → подпись (Developer ID + hardened runtime,
# если сертификат есть, иначе ad-hoc). Итог: ./build/Charoite.app
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

# Код демона — тоже в бандл. Без него вложенный python бесполезен: демон
# запускается как src/daemon.py, и папку репозитория всё равно пришлось бы
# клонировать. Это 2 МБ на фоне 300 МБ контура.
#
# Данные при этом остаются у человека: приложение передаёт демону
# CHAROITE_ROOT — бандл подписан и доступен только на чтение.
CODE="$APP/Contents/Resources/charoite"
mkdir -p "$CODE"
cp -Rc ../src "$CODE/src"
cp -Rc ../scripts "$CODE/scripts"
mkdir -p "$CODE/config"
cp ../config/config.example.yaml "$CODE/config/config.example.yaml"
cp ../pyproject.toml "$CODE/pyproject.toml"
find "$CODE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
echo "код демона вложен ($(du -sh "$CODE" | cut -f1))"

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

# Подпись: Developer ID, если он есть в связке (или задан явно), иначе ad-hoc.
#
# Два разных зачем.
#   1) Разрешения. У ad-hoc подписи designated requirement — это
#      `cdhash H"…"`, привязка к точному хешу бинаря: любая пересборка
#      меняет хеш, и macOS считает приложение ДРУГИМ — доступы (микрофон,
#      системный звук, календарь) приходится выдавать заново. С Developer ID
#      requirement становится «identifier + команда» и переживает пересборки.
#   2) Дистрибуция. Релиз из CI с Developer ID + hardened runtime + метка
#      времени → нотаризация Apple → у пользователя приложение открывается
#      двойным кликом, без «Открыть всё равно» и xattr.
#
# Hardened runtime включаем ТОЛЬКО с настоящей подписью: нотаризация без него
# невозможна. Под ним дочерний процесс ничего не наследует от приложения,
# а микрофон у нас читает python-демон отдельным процессом — поэтому у
# вложенного интерпретатора СВОЙ набор entitlements (audio-input и т.д.),
# см. Resources/entitlements/embedded-python.entitlements. Без них демон
# получает тишину без единой ошибки. Проверка микрофона на первом
# подписанном релизе — руками, автоматом это не ловится.
#
# CHAROITE_SIGN_IDENTITY — явный выбор идентичности (CI кладёт сертификат во
# временную связку и передаёт имя). Пусто → ищем Developer ID в связках,
# нет → ad-hoc.
ENT_DIR="$(pwd)/Resources/entitlements"
SIGN_ID="${CHAROITE_SIGN_IDENTITY:-}"
# «-» / adhoc — явный запрос ad-hoc даже при сертификате в связке (проверка
# запасного пути на машине разработчика).
case "$SIGN_ID" in -|adhoc|ad-hoc) SIGN_ID=""; FORCE_ADHOC=1 ;; *) FORCE_ADHOC=0 ;; esac
if [ -z "$SIGN_ID" ] && [ "$FORCE_ADHOC" = 0 ]; then
    SIGN_ID="$(security find-identity -v -p codesigning 2>/dev/null \
        | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
fi

# Mach-O ли файл: по магии заголовка. Подписывать надо ВСЕ бинарники
# (нотаризация отвергает бандл с одним неподписанным .so), но только их —
# скрипты, .py, .h и заголовки .a codesign не примет, а раньше сбой на них
# глушился `|| true`, вместе со всеми настоящими сбоями подписи.
is_macho() {
    case "$(head -c 4 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')" in
        feedface|feedfacf|cefaedfe|cffaedfe|cafebabe|bebafeca|cafebabf|bfbafeca) return 0 ;;
        *) return 1 ;;
    esac
}
# Все Mach-O дерева, NUL-разделённые, — по магии, а не по имени или битам
# исполнения: `libfoo.so.1` с режимом 644 — тоже Mach-O, и нотаризация
# отвергнет бандл из-за него (второе мнение DeepSeek). Заведомо не бинарные
# расширения отсекаются до чтения заголовка — иначе это 5000 файлов вместо
# 900. `|| true`: последний файл может оказаться не Mach-O, а статус фильтра
# не должен ронять скрипт под set -e; ошибка обхода find — тоже.
only_macho() { while IFS= read -r -d '' f; do is_macho "$f" && printf '%s\0' "$f" || true; done; }
list_macho_all() {
    { find "$1" -type f ! \( -name '*.py' -o -name '*.pyc' -o -name '*.pyi' -o -name '*.txt' \
        -o -name '*.json' -o -name '*.md' -o -name '*.h' -o -name '*.rst' -o -name '*.pem' \
        -o -name '*.typed' \) -print0 2>/dev/null || true; } | only_macho
}

# Вложенные бинарники подписываются ПЕРВЫМИ и по одному.
#
# codesign --deep для такого дерева официально не поддерживается и молча
# оставляет часть .so неподписанными: приложение запускается, а первый же
# импорт numpy падает с «code signature invalid» — уже у пользователя.
#
# Любой сбой подписи валит сборку: неподписанный бинарь в поставке —
# это отказ Gatekeeper у пользователя, а не «предупреждение».
sign_embedded() {
    local id="$1" root="$APP/Contents/Resources/python"
    local runtime_opts=() ent_opts=()
    [ -d "$root" ] || return 0
    if [ "$id" != "-" ]; then
        runtime_opts=(--options runtime --timestamp)
        ent_opts=(--entitlements "$ENT_DIR/embedded-python.entitlements")
    fi
    echo "подписываю вложенный контур…"
    local all libs bins
    all="$(mktemp)"; libs="$(mktemp)"; bins="$(mktemp)"
    list_macho_all "$root" > "$all"
    # bin/ — интерпретатор и соседи: им entitlements. Всё остальное —
    # библиотеки и утилиты из колёс — без entitlements, hardened runtime тот
    # же. Симлинки не трогаем: подписывается файл, на который они указывают.
    while IFS= read -r -d '' f; do
        case "$f" in
            "$root"/bin/*) printf '%s\0' "$f" >> "$bins" ;;
            *) printf '%s\0' "$f" >> "$libs" ;;
        esac
    done < "$all"
    # ${arr[@]+"${arr[@]}"} — раскрытие пустого массива, которое не роняет
    # bash 3.2 (/bin/bash macOS) под set -u: у ad-hoc опций рантайма нет.
    if [ -s "$libs" ]; then
        xargs -0 -P 8 -n 20 codesign --force ${runtime_opts[@]+"${runtime_opts[@]}"} --sign "$id" < "$libs"
    fi
    if [ -s "$bins" ]; then
        xargs -0 -n 10 codesign --force ${runtime_opts[@]+"${runtime_opts[@]}"} ${ent_opts[@]+"${ent_opts[@]}"} --sign "$id" < "$bins"
    fi
    echo "  библиотек и утилит: $(tr -cd '\0' < "$libs" | wc -c | tr -d ' '), исполняемых в bin/: $(tr -cd '\0' < "$bins" | wc -c | tr -d ' ')"
    rm -f "$all" "$libs" "$bins"
}

if [ -n "$SIGN_ID" ]; then
    sign_embedded "$SIGN_ID"
    codesign --force --options runtime --timestamp \
        --entitlements "$ENT_DIR/Charoite.entitlements" \
        --sign "$SIGN_ID" "$APP"
    # Имя владельца сертификата в лог не печатаем: логи CI публичны, а в
    # самой подписи оно и так есть для тех, кому нужно (codesign -dv).
    echo "подписано: Developer ID Application, hardened runtime, метка времени"
else
    sign_embedded -
    codesign --force --sign - "$APP"
    echo "ВНИМАНИЕ: Developer ID не найден, подпись ad-hoc —"
    echo "  доступ к микрофону и системному звуку будет слетать при каждой сборке,"
    echo "  а первый запуск потребует «Открыть всё равно» в настройках macOS."
fi
# --strict --deep: цельность подписи всего дерева. Сломанная подпись здесь
# дешевле, чем у пользователя после скачивания.
codesign --verify --deep --strict --verbose=1 "$APP"
echo "готово: $APP"
