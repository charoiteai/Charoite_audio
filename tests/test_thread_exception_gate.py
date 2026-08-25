"""Fail-closed контракт для исключений фоновых потоков в pytest."""

import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_unhandled_thread_exception_makes_pytest_red(tmp_path):
    """Удаление filterwarnings обязано сделать этот тест красным.

    Проба идёт в отдельном pytest-процессе: намеренно упавший поток не может
    отравить текущую сессию, а мы проверяем именно итоговый exit code гейта.
    """
    probe = tmp_path / "test_thread_crash.py"
    probe.write_text(
        textwrap.dedent(
            """
            import threading


            def test_background_crash():
                def crash():
                    raise RuntimeError("thread-gate-canary")

                worker = threading.Thread(target=crash, name="thread-gate-canary")
                worker.start()
                worker.join()
            """
        ),
        encoding="utf-8",
    )

    # Привязка к pyproject.toml НАМЕРЕННА: гейт живёт только там, и перенос
    # его в conftest должен сломать эту канарейку — чтобы решение о новом
    # месте принималось глазами, а не терялось молча (круг по #420, DS I2).
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(ROOT / "pyproject.toml"),
            str(probe),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,   # запас на холодный интерпретатор занятого раннера
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "PytestUnhandledThreadExceptionWarning" in output, output
    assert "thread-gate-canary" in output, output
