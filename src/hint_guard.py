"""Шаг стража слоя авто-подсказок: решение отдельно от исполнения.

Функция чистая и живёт вне daemon.py, чтобы тест проверял ТО ЖЕ самое,
что исполняет главный цикл, а не копию ветки (круг-1 по PR #398, DS).
"""

RESTART_CAP = 3


def hint_guard_step(restarts: int, alive: bool, cap: int = RESTART_CAP):
    """(действие, новый счётчик): 'restart' — поднять поток заново,
    'gave_up' — потолок исчерпан, сказать и замолчать; None — не трогать."""
    if alive:
        return None, restarts
    if restarts < cap:
        return "restart", restarts + 1
    if restarts == cap:
        return "gave_up", restarts + 1
    return None, restarts
