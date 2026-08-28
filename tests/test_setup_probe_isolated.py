"""Проба готовности не даёт папке данных подменять стандартные модули."""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SWIFT = ROOT / "app/Sources/CharoiteApp/Services/SetupReadinessService.swift"


def test_probe_python_runs_with_safepath():
    """Проба обязана идти с PYTHONSAFEPATH, и НЕ с -I.

    Проба запускается с currentDirectoryURL = корень данных, куда пишут граф
    и владелец: без защиты подложенный yaml.py исполнился бы с правами
    приложения (№102). Именно SAFEPATH, а не -I: тот тянет -E и глушит
    PYTHONPYCACHEPREFIX — проба писала бы .pyc в подписанный бандл и ломала
    подпись, как в 0.52.0 (DS, круг-1).
    """
    src = SWIFT.read_text(encoding="utf-8")
    assert 'env["PYTHONSAFEPATH"] = "1"' in src, "SAFEPATH не выставляется"
    m = re.search(r'process\.arguments = \[([^\]]*)\]', src)
    assert m and '"-I"' not in m.group(1), (
        "-I вернулся — вместе с ним вернётся и запись байткода в бандл"
    )


def test_safepath_actually_blocks_cwd_shadowing(tmp_path):
    """SAFEPATH действительно закрывает перехват: живой опыт, не вера в флаг."""
    (tmp_path / "yaml.py").write_text(
        'raise SystemExit("ПЕРЕХВАЧЕНО из cwd")', encoding="utf-8")
    hijacked = subprocess.run(
        [sys.executable, "-c", "import yaml"],
        cwd=tmp_path, capture_output=True, text=True)
    assert "ПЕРЕХВАЧЕНО" in (hijacked.stderr + hijacked.stdout), (
        "стенд не воспроизводит дыру — тест ничего не проверяет"
    )
    safe = subprocess.run(
        [sys.executable, "-c", "import yaml; print('ok')"],
        cwd=tmp_path, capture_output=True, text=True,
        env={**os.environ, "PYTHONSAFEPATH": "1"})
    assert safe.returncode == 0 and "ok" in safe.stdout, (
        f"SAFEPATH не защитил от подмены: {safe.stderr[:200]}"
    )


def test_safepath_keeps_the_bytecode_cache_prefix(tmp_path):
    """SAFEPATH не глушит PYTHONPYCACHEPREFIX — печать бандла держится им.

    Ровно этим -I и был опасен: подразумевает -E, префикс кэша пропадает, и
    импорты пробы пишут __pycache__ рядом с исходниками — в подписанные
    Resources вложенной установки.
    """
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; print('PREFIX', sys.pycache_prefix)"],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONSAFEPATH": "1",
             "PYTHONPYCACHEPREFIX": str(tmp_path)})
    assert f"PREFIX {tmp_path}" in r.stdout, (
        "SAFEPATH затёр префикс кэша байткода — бандл снова под угрозой"
    )
