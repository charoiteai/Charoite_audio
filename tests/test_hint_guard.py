"""Сторож слоя авто-подсказок (инцидент 24.08: слой молчал три встречи).

Тестируем ТУ ЖЕ функцию, что исполняет главный цикл демона, — не копию
(круг-1 по PR #398, DS: копия молча рассинхронится).
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from hint_guard import hint_guard_step  # noqa: E402


def run(steps_alive):
    said, restarts = [], 0
    for alive in steps_alive:
        action, restarts = hint_guard_step(restarts, alive)
        if action:
            said.append(action if action == "gave_up" else f"restart #{restarts}")
    return said, restarts


def test_dead_thread_restarts_up_to_three_times():
    said, restarts = run([False] * 6)
    assert said == ["restart #1", "restart #2", "restart #3", "gave_up"]
    assert restarts == 4  # счётчик замер: «сдался» не повторяется


def test_alive_thread_is_left_alone():
    said, restarts = run([True] * 5)
    assert said == [] and restarts == 0


def test_revived_thread_keeps_budget():
    # Ожил после первого рестарта — бюджет не тратится, пока снова не умрёт.
    said, _ = run([False, True, True, False, False, False, False])
    assert said == ["restart #1", "restart #2", "restart #3", "gave_up"]
