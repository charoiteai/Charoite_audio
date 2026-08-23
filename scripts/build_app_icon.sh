#!/bin/bash
# Иконка macOS-приложения из документа Icon Composer (app/Resources/AppIcon.icon).
#
# На macOS 26 (Tahoe) старый .icns со скруглённым квадратом и прозрачными
# полями показывается в серой «плитке»: система считает иконку чужой по
# форме. Новый формат .icon (слои + Liquid Glass) компилируется actool из
# Xcode 26 в Assets.car (его читает Tahoe и новее) и в legacy AppIcon.icns
# (macOS ≤ 15 и CFBundleIconFile). Оба артефакта лежат в репозитории:
# CI собирает на macos-15, где actool формат .icon не знает.
#
#   scripts/build_app_icon.sh            # пересобрать оба файла
#
# Исходник слоя: app/Resources/AppIcon.icon/Assets/owl.png — белая сова на
# прозрачном фоне 1024×1024 (та же, что в iOS-иконке); фон — automatic
# gradient фирменного фиолетового в icon.json.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/app/Resources/AppIcon.icon"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

ver="$(actool --version 2>/dev/null | grep -A1 short-bundle-version | grep -oE '[0-9]+' | head -1 || true)"
if [ -z "$ver" ] || [ "$ver" -lt 26 ]; then
  echo "нужен actool из Xcode 26+ (формат .icon), найден: ${ver:-нет}" >&2
  exit 1
fi
actool --output-format human-readable-text --notices --warnings --errors \
  --output-partial-info-plist "$OUT/partial.plist" \
  --app-icon AppIcon --include-all-app-icons \
  --target-device mac --minimum-deployment-target 13.0 --platform macosx \
  --compile "$OUT" "$SRC" | grep -vE '^\s*$|compilation-results' || true
if [ ! -s "$OUT/Assets.car" ] || [ ! -s "$OUT/AppIcon.icns" ]; then
  echo "actool не дал Assets.car/AppIcon.icns" >&2
  exit 1
fi
cp "$OUT/Assets.car" "$ROOT/app/Resources/Assets.car"
cp "$OUT/AppIcon.icns" "$ROOT/app/Resources/AppIcon.icns"
echo "готово: app/Resources/Assets.car ($(du -h "$ROOT/app/Resources/Assets.car" | cut -f1)), AppIcon.icns ($(du -h "$ROOT/app/Resources/AppIcon.icns" | cut -f1))"
