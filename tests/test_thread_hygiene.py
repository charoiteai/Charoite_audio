"""Канарейка сторожа брошенных потоков (№415).

Сторож живёт в `tests/conftest.py` и включается только вместе с обвязкой.
Проба идёт в отдельном pytest-процессе: брошенный поток не должен отравить
текущую сессию, а проверяем мы итоговый код выхода. Обвязка в тот процесс
приезжает плагином (`-p conftest`, `PYTHONPATH=tests`): файл пробы лежит в
`tmp_path`, и `conftest.py` из `tests/` ему не предок — без плагина сторож
не поднялся бы вовсе, и канарейка «зеленела» бы слепотой.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _проба(tmp_path, body: str) -> subprocess.CompletedProcess:
    probe = tmp_path / "test_probe.py"
    probe.write_text(textwrap.dedent(body), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(ROOT / "pyproject.toml"),
         "-p", "conftest", str(probe)],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=120,
        env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
    )


def test_брошенный_поток_без_detached_красит(tmp_path):
    """Поток продукта, оставленный тестом живым и не объявленный `detached`,
    обязан покрасить прогон — и назвать имя с ролью."""
    result = _проба(tmp_path, """
        import time

        import threads


        def test_abandoned():
            threads.spawn(lambda: time.sleep(30), name="canary-abandoned", role="console")
    """)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "canary-abandoned" in out, out
    assert "console" in out, out
    assert "detached" in out, out


def test_поток_с_detached_остаётся_зелёным(tmp_path):
    """Тот же поток с названной причиной `detached` — законен: причина уже
    сказана в коде, и тест за него не отвечает."""
    result = _проба(tmp_path, """
        import time

        import threads


        def test_detached():
            threads.spawn(lambda: time.sleep(30), name="canary-detached", role="console",
                          detached="канарейка живёт до конца процесса")
    """)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
