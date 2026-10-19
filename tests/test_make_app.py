"""Сборщик бандла берёт бинарь там, где его назвал SwiftPM (№745).

`app/make_app.sh` копировал продукт по жёсткому пути `.build/arm64-apple-macosx/release`.
Swift 6.4 по умолчанию собирает бэкендом swiftbuild и кладёт продукт в
`.build/out/Products/Release` — сборка проходила, бандла не было. Проба гонит
настоящий скрипт целиком в каркасе репозитория во временном каталоге: `swift`
подменён и кладёт продукт в каталог, которого нет ни в одной реальной раскладке,
подпись — ad-hoc, бинарь — копия `/bin/echo` (Mach-O, `codesign` его примет).

Граница: проба доказывает, что скрипт берёт бинарь там, где назвал SwiftPM, и
отказывает без продукта. Что настоящий SwiftPM называет верный каталог, она не
проверяет — это видит шаг сборки в workflow тестов Swift и сам `make_app.sh` на релизе.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not (shutil.which("otool") and shutil.which("codesign")),
    reason="codesign, otool и Mach-O — только macOS с инструментами командной строки")

FAKE_SWIFT = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_SWIFT_LOG"
for a in "$@"; do
    if [ "$a" = --show-bin-path ]; then echo "$FAKE_BIN_DIR"; exit 0; fi
done
mkdir -p "$FAKE_BIN_DIR"
case "${FAKE_SWIFT_PRODUCT:-macho}" in
    none) ;;
    script) printf '#!/bin/sh\n' > "$FAKE_BIN_DIR/CharoiteApp"; chmod +x "$FAKE_BIN_DIR/CharoiteApp" ;;
    *) cp /bin/echo "$FAKE_BIN_DIR/CharoiteApp" ;;
esac
"""


def _skeleton(tmp_path: pathlib.Path) -> pathlib.Path:
    """Каркас репозитория: настоящие make_app.sh и stage_code.sh, пустые входы кода."""
    repo = tmp_path / "репо"
    app = repo / "app"
    (app / "Resources").mkdir(parents=True)
    for name in ("make_app.sh", "stage_code.sh"):
        shutil.copy2(ROOT / "app" / name, app / name)
    shutil.copy2(ROOT / "app" / "Resources" / "AppIcon.icns", app / "Resources" / "AppIcon.icns")
    for d in ("src", "scripts", "config"):
        (repo / d).mkdir()
    for f in ("config/config.example.yaml", "pyproject.toml", "requirements-nemotron.lock"):
        (repo / f).write_text("", encoding="utf-8")
    # версию бандла скрипт берёт из git-тега — каркасу нужен свой репозиторий с тегом
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "каркас"], ["tag", "v9.8.7"]):
        subprocess.run(git + cmd, check=True, capture_output=True)
    return app


def _run(tmp_path: pathlib.Path, old_bundle: bool = False,
         **extra: str) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path]:
    app = _skeleton(tmp_path)
    if old_bundle:
        prev = app / "build" / "Charoite.app" / "Contents"
        prev.mkdir(parents=True)
        (prev / "Info.plist").write_text("прежний", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "swift").write_text(FAKE_SWIFT, encoding="utf-8")
    (bin_dir / "swift").chmod(0o755)
    product = tmp_path / "куда SwiftPM сказал" / "Products" / "Any"
    log = tmp_path / "swift.log"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path / "дом"),
           "GIT_CEILING_DIRECTORIES": str(tmp_path), "CHAROITE_SIGN_IDENTITY": "-",
           "FAKE_BIN_DIR": str(product), "FAKE_SWIFT_LOG": str(log), **extra}
    out = subprocess.run(["/bin/bash", str(app / "make_app.sh")], capture_output=True, text=True,
                         timeout=300, env=env)
    return out, app, product


def _uuids(path: pathlib.Path) -> list[str]:
    """LC_UUID по всем срезам: подпись меняет байты файла, но не UUID сборки."""
    text = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True).stdout
    lines = text.splitlines()
    return [lines[i + 2].split()[-1] for i, line in enumerate(lines) if line.strip() == "cmd LC_UUID"]


def test_bundle_takes_the_binary_where_swiftpm_put_it(tmp_path):
    out, app, product = _run(tmp_path)
    assert out.returncode == 0, out.stderr[-800:]
    bundled = app / "build" / "Charoite.app" / "Contents" / "MacOS" / "CharoiteApp"
    assert bundled.is_file(), out.stdout[-400:]
    # UUID продукта — не тот, что у любого бинаря, который скрипт мог бы взять сам
    assert _uuids(bundled) and _uuids(bundled) == _uuids(product / "CharoiteApp")
    assert _uuids(bundled) != _uuids(pathlib.Path("/usr/bin/true"))
    # путь спрашивается теми же флагами, что и сборка: иначе он указал бы в чужую конфигурацию
    calls = (tmp_path / "swift.log").read_text(encoding="utf-8").splitlines()
    builds = [c for c in calls if "--show-bin-path" not in c]
    asks = [c.replace(" --show-bin-path", "") for c in calls if "--show-bin-path" in c]
    assert len(builds) == 1 and builds[0].startswith("build "), calls
    assert asks == builds, calls


@pytest.mark.parametrize("kind", ["none", "script"])
def test_missing_product_refuses_and_keeps_the_previous_bundle(tmp_path, kind):
    """Сборка «прошла», продукта нет (или на его месте исполняемый не-Mach-O) —
    отказ с путём до того, как снесён прежний бандл."""
    out, app, product = _run(tmp_path, old_bundle=True, FAKE_SWIFT_PRODUCT=kind)
    assert out.returncode != 0
    assert "нет собранного бинаря" in out.stderr and str(product / "CharoiteApp") in out.stderr, out.stderr[-400:]
    plist = app / "build" / "Charoite.app" / "Contents" / "Info.plist"
    assert plist.read_text(encoding="utf-8") == "прежний"
