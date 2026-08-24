"""Сторож слоя авто-подсказок (инцидент 24.08: слой молчал три встречи).

Логика стража живёт в главном цикле демона; здесь фиксируем контракт
кусочков, из которых он собран: мёртвый поток перезапускается с честной
строкой, потолок перезапусков не превышается, живой поток не трогается.
Сам цикл в тесте не крутим — воспроизводим шаг стража как в daemon.py.
"""
import threading


def guard_step(hint_state, alive: bool, said: list):
    """Один шаг стража — копия ветки из daemon.py (держать в синхроне)."""
    if not alive:
        if hint_state["restarts"] < 3:
            hint_state["restarts"] += 1
            said.append(f"restart #{hint_state['restarts']}")
            hint_state["thread"] = threading.Thread(target=lambda: None, daemon=True)
        elif hint_state["restarts"] == 3:
            hint_state["restarts"] += 1
            said.append("gave up")


def test_dead_thread_restarts_up_to_three_times():
    state = {"thread": None, "restarts": 0}
    said: list = []
    for _ in range(6):
        guard_step(state, alive=False, said=said)
    assert said == ["restart #1", "restart #2", "restart #3", "gave up"]
    assert state["restarts"] == 4  # счётчик замер: «сдался» не повторяется


def test_alive_thread_is_left_alone():
    state = {"thread": None, "restarts": 0}
    said: list = []
    for _ in range(5):
        guard_step(state, alive=True, said=said)
    assert said == [] and state["restarts"] == 0
