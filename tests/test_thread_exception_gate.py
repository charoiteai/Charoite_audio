"""Fail-closed контракт для исключений фоновых потоков в pytest."""


def test_unhandled_thread_exception_makes_pytest_red(внутренний_pytest):
    """Удаление filterwarnings обязано сделать этот тест красным.

    Проба идёт в отдельном pytest-процессе: намеренно упавший поток не может
    отравить текущую сессию, а мы проверяем именно итоговый exit code гейта.

    Без обвязки (`обвязка=False`) — привязка к pyproject.toml НАМЕРЕННА: гейт
    живёт только там, и перенос его в conftest должен сломать эту канарейку —
    чтобы решение о новом месте принималось глазами, а не терялось молча (круг
    по #420, DS I2).
    """
    result = внутренний_pytest(
        """
        import threading


        def test_background_crash():
            def crash():
                raise RuntimeError("thread-gate-canary")

            worker = threading.Thread(target=crash, name="thread-gate-canary")
            worker.start()
            worker.join()
        """,
        обвязка=False,
        имя="test_thread_crash.py",
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "PytestUnhandledThreadExceptionWarning" in output, output
    assert "thread-gate-canary" in output, output
