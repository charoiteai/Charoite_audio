"""Дропы кадров быстрого триггера — вслух раз на окно (аудит 30.08, DS I2)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import frame_drops  # noqa: E402


def test_drops_are_reported_once_per_window_after_the_threshold():
    clock = [0.0]
    m = frame_drops.DropMeter(window_s=60, threshold=5, now=lambda: clock[0])
    assert [m.dropped() for _ in range(4)] == [None] * 4
    msg = m.dropped()
    assert msg and "не меньше 5 кадров" in msg and "60 с" in msg
    assert m.dropped() is None, "в том же окне второй раз не кричим"
    clock[0] = 61.0
    assert [m.dropped() for _ in range(4)] == [None] * 4
    assert m.dropped(), "новое окно — новый счёт и новое сообщение"
    assert m.total == 11
