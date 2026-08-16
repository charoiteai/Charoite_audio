#!/bin/bash
# Переносимый python-контур внутрь бандла приложения.
#
# Зачем: до этого установка начиналась с терминала — git clone, python -m venv,
# pip install. Приложение уже умеет всё остальное (конфиг, модели, разрешения),
# и только интерпретатор оставался снаружи. Вложенный контур убирает из
# инструкции последние три шага, требующие консоли.
#
# Что кладём: CPython от python-build-standalone (релокатабельный — в отличие
# от Homebrew, чьи бинарники прибиты к /opt/homebrew абсолютными путями) плюс
# рантайм-зависимости из pyproject.
#
# Чего НЕ кладём:
#   • mlx-whisper и parakeet-mlx — опциональные пресеты STT, тянут torch на
#     529 МБ ради языков, которые дефолту не нужны (русский идёт gigaam);
#   • инструменты разработки (ruff, pytest, semgrep) — им в поставке не место.
# Кто выберет whisper-пресет, доставит его в свой контур сам: `--extras`.
#
#   scripts/build_embedded_python.sh [--extras]
#
# Итог: app/build/embedded-python/ — каталог, который make_app.sh копирует
# в Charoite.app/Contents/Resources/python. Каталог кэшируется между
# сборками: пересобирать его каждый раз незачем.
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VERSION="3.12.13"
BUILD_TAG="20260807"
ARCH="aarch64-apple-darwin"
OUT="app/build/embedded-python"
CACHE=".cache/python-standalone"
ASSET="cpython-${PY_VERSION}+${BUILD_TAG}-${ARCH}-install_only_stripped.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${BUILD_TAG}/${ASSET//+/%2B}"

WITH_EXTRAS=0
[ "${1:-}" = "--extras" ] && WITH_EXTRAS=1

if [ -x "$OUT/bin/python3" ] && "$OUT/bin/python3" -c "import numpy, onnxruntime, sounddevice" 2>/dev/null; then
    echo "контур уже собран: $OUT ($(du -sh "$OUT" | cut -f1))"
    exit 0
fi

mkdir -p "$CACHE"
if [ ! -f "$CACHE/$ASSET" ]; then
    echo "качаю CPython ${PY_VERSION} (~24 МБ)…"
    # Адрес печатаем до соединения — как это делает scripts/get_models.py:
    # сеть в этом продукте всегда явная.
    echo "  $URL"
    curl -fL --retry 3 --connect-timeout 20 -o "$CACHE/$ASSET.part" "$URL"
    mv "$CACHE/$ASSET.part" "$CACHE/$ASSET"
fi

# Проверка sha256 против SHA256SUMS, который python-build-standalone
# публикует на весь релиз (per-asset .sha256 у них нет). Этот интерпретатор
# уезжает пользователям внутри бандла — скачивание без верификации было
# дырой supply-chain (аудит 14.08). Проверяем и свежескачанный, и
# КЭШИРОВАННЫЙ файл: отравленный кэш ничем не лучше отравленной загрузки.
SUMS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${BUILD_TAG}/SHA256SUMS"
expected=$(curl -fsL --retry 3 --connect-timeout 20 "$SUMS_URL" \
    | awk -v a="$ASSET" '$2 == a {print $1}')
actual=$(shasum -a 256 "$CACHE/$ASSET" | awk '{print $1}')
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    echo "sha256 НЕ СОВПАЛ для $ASSET" >&2
    echo "  ожидали: ${expected:-<пусто>}" >&2
    echo "  получили: $actual" >&2
    rm -f "$CACHE/$ASSET"
    exit 1
fi
echo "sha256 сошёлся: $actual"

rm -rf "$OUT"
mkdir -p "$(dirname "$OUT")"
tar -xzf "$CACHE/$ASSET" -C "$(dirname "$OUT")"
mv "$(dirname "$OUT")/python" "$OUT"

echo "ставлю рантайм-зависимости…"
# --no-cache-dir: колёса на 300 МБ в кэше пользователя никому не нужны.
"$OUT/bin/python3" -m pip install --no-cache-dir --quiet --upgrade pip >/dev/null
if [ "$WITH_EXTRAS" = "1" ]; then
    "$OUT/bin/python3" -m pip install --no-cache-dir --quiet .
else
    # Только из lock-файла и только с хешами. Раньше здесь стояли диапазоны
    # из pyproject («numpy>=1.26,<3»), то есть в подписанный бандл уезжало
    # то, что лежало на PyPI в минуту сборки, вместе с транзитивными
    # пакетами, которых не видит ни dependabot, ни dependency-review.
    # Целостность CPython этот же скрипт сверяет с 14.08 — до пакетов урок
    # дошёл 16.08. Пересобрать lock:
    #   .venv/bin/python scripts/lock_runtime_deps.py
    LOCK="$(dirname "$0")/../requirements-runtime.lock"
    if [ ! -f "$LOCK" ]; then
        echo "нет requirements-runtime.lock — соберите его:" >&2
        echo "  .venv/bin/python scripts/lock_runtime_deps.py" >&2
        exit 1
    fi
    "$OUT/bin/python3" -m pip install --no-cache-dir --quiet \
        --require-hashes -r "$LOCK"
fi

# Проверяем то, без чего демон не стартует, — а не факт «pip не упал».
"$OUT/bin/python3" - <<'PY'
import sys
import numpy, yaml, requests, sounddevice, onnxruntime  # noqa: F401
print(f"контур готов: python {sys.version.split()[0]}, numpy {numpy.__version__}")
PY
echo "размер: $(du -sh "$OUT" | cut -f1) → $OUT"
