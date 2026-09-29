#!/bin/bash
# Код, который едет в бандл: один список для сборки (make_app.sh) и теста
# (tests/test_install_engine.py). Входы читают из корня кода не только src/ и
# scripts/: установщик движка берёт лок requirements-nemotron.lock, и бандл без
# него отказывал рецептом, невыполнимым для пользователя приложения (№489).
#
# Использование: app/stage_code.sh <каталог назначения>
set -euo pipefail

dest="${1:?куда класть код: app/stage_code.sh <каталог>}"
repo="$(cd "$(dirname "$0")/.." && pwd -P)"

# APFS-клон (-c) — только у cp macOS; у GNU cp ключ -c значит другое, а тест
# идёт и на Linux-раннере CI.
copy=(cp -R)
[ "$(uname -s)" = Darwin ] && copy=(cp -Rc)

mkdir -p "$dest/config"
"${copy[@]}" "$repo/src" "$dest/src"
"${copy[@]}" "$repo/scripts" "$dest/scripts"
cp "$repo/config/config.example.yaml" "$dest/config/config.example.yaml"
cp "$repo/pyproject.toml" "$dest/pyproject.toml"
cp "$repo/requirements-nemotron.lock" "$dest/requirements-nemotron.lock"
find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
