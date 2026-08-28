"""Проба готовности не даёт папке данных подменять стандартные модули."""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SWIFT = ROOT / "app/Sources/CharoiteApp/Services/SetupReadinessService.swift"


def test_probe_python_runs_isolated():
    """`python -c` с cwd в папке данных обязан идти с -I.

    Проба запускается с currentDirectoryURL = корень данных, куда пишут граф
    и владелец. Без -I интерпретатор ставит cwd первым в sys.path, и
    подложенный туда yaml.py исполнился бы с правами приложения (DS,
    security-класс, №102).
    """
    src = SWIFT.read_text(encoding="utf-8")
    m = re.search(r'process\.arguments = \[([^\]]*)\]', src)
    assert m, "не нашёл запуск python в SetupReadinessService"
    args = m.group(1)
    assert '"-I"' in args and args.index('"-I"') < args.index('"-c"'), (
        "python пробы запускается без -I — cwd снова в sys.path"
    )


def test_isolated_mode_actually_blocks_cwd_shadowing(tmp_path):
    """-I действительно закрывает перехват: живой опыт, не вера в флаг."""
    (tmp_path / "yaml.py").write_text(
        'raise SystemExit("ПЕРЕХВАЧЕНО из cwd")', encoding="utf-8")
    hijacked = subprocess.run(
        [sys.executable, "-c", "import yaml"],
        cwd=tmp_path, capture_output=True, text=True)
    assert "ПЕРЕХВАЧЕНО" in (hijacked.stderr + hijacked.stdout), (
        "стенд не воспроизводит дыру — тест ничего не проверяет"
    )
    safe = subprocess.run(
        [sys.executable, "-I", "-c", "import yaml; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True)
    assert safe.returncode == 0 and "ok" in safe.stdout, (
        f"-I не защитил от подмены: {safe.stderr[:200]}"
    )
