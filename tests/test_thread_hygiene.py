"""Канарейка сторожа брошенных потоков (№415).

Сторож живёт в `tests/conftest.py` и включается только вместе с обвязкой.
Проба идёт в отдельном pytest-процессе (фикстура `внутренний_pytest` с
`обвязка=True`): брошенный поток не должен отравить текущую сессию, а
проверяем мы итоговый код выхода.
"""


def test_брошенный_поток_без_detached_красит(внутренний_pytest):
    """Поток продукта, оставленный тестом живым и не объявленный `detached`,
    обязан покрасить прогон — и назвать имя с ролью."""
    result = внутренний_pytest("""
        import time

        import threads


        def test_abandoned():
            threads.spawn(lambda: time.sleep(30), name="canary-abandoned", role="console")
    """, обвязка=True)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "canary-abandoned" in out, out
    assert "console" in out, out
    assert "detached" in out, out


def test_поток_с_detached_остаётся_зелёным(внутренний_pytest):
    """Тот же поток с названной причиной `detached` — законен: причина уже
    сказана в коде, и тест за него не отвечает."""
    result = внутренний_pytest("""
        import time

        import threads


        def test_detached():
            threads.spawn(lambda: time.sleep(30), name="canary-detached", role="console",
                          detached="канарейка живёт до конца процесса")
    """, обвязка=True)
    out = result.stdout + result.stderr
    assert result.returncode == 0, out
