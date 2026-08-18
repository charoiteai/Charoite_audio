"""Автостоп записи: когда останавливаем сами, а когда молчим.

17.08 запись шла 18 ч 25 мин в пустой комнате. Здесь проверяется правило,
которое это закрывает, и — важнее — что оно не срывает живую встречу: пауза
на чтение документа, демо без звука, первые минуты записи.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import autostop  # noqa: E402

L = autostop.limits_from_cfg({})       # дефолты: 5 / 15 мин тишины, 6 ч потолок
MIN = 60.0


def d(*, age: float, quiet: float | None, voices: int = 1, limits=L):
    """Решение через `age` секунд записи при тишине `quiet` (None — речи не было)."""
    now = 10_000.0
    started = now - age
    last = None if quiet is None else now - quiet
    return autostop.decide(now=now, started_at=started, last_speech_at=last,
                           voices=voices, limits=limits)


def test_forgotten_recording_in_an_empty_room_stops():
    """Ровно случай 17.08: включили, ушли, речи не было вовсе."""
    assert d(age=6 * MIN, quiet=None).action == "stop"
    assert d(age=6 * MIN, quiet=None).reason == autostop.SILENCE


def test_one_voice_gets_five_minutes_of_silence():
    assert not d(age=20 * MIN, quiet=3 * MIN, voices=1)
    assert d(age=20 * MIN, quiet=5 * MIN + 1, voices=1).action == "stop"


def test_real_meeting_survives_a_long_pause():
    """Двое и больше — это разговор: пауза на чтение документа не повод."""
    assert not d(age=40 * MIN, quiet=10 * MIN, voices=2)
    assert not d(age=40 * MIN, quiet=13 * MIN, voices=5)
    assert d(age=40 * MIN, quiet=14.5 * MIN, voices=5).action == "warn", "сначала предупреждение"
    assert d(age=40 * MIN, quiet=16 * MIN, voices=2).action == "stop"


def test_first_minutes_are_never_touched():
    """«Включил, пока все собираются» — запись не должна умереть на старте."""
    assert not d(age=1 * MIN, quiet=None)
    assert not d(age=1.9 * MIN, quiet=None)


def test_warning_comes_before_the_stop_and_speech_cancels_it():
    warn = d(age=20 * MIN, quiet=5 * MIN - 30, voices=1)
    assert warn.action == "warn" and warn.reason == autostop.SILENCE
    assert 0 < warn.seconds_left <= 60
    assert "скажите" in warn.text, "человеку должно быть понятно, как отменить"
    # заговорили — предупреждение снимается само (тишина обнулилась)
    assert not d(age=21 * MIN, quiet=2.0, voices=1)


def test_duration_ceiling_stops_even_a_live_meeting():
    assert not d(age=5.9 * 3600, quiet=10.0, voices=5)
    warn = d(age=6 * 3600 - 30, quiet=10.0, voices=5)
    assert warn.action == "warn" and warn.reason == autostop.LIMIT
    stop = d(age=6 * 3600 + 1, quiet=10.0, voices=5)
    assert stop.action == "stop" and stop.reason == autostop.LIMIT


def test_ceiling_wins_over_silence_when_both_fire():
    """Обе причины сразу — называем потолок: он безусловен, тишина обсуждаема."""
    assert d(age=7 * 3600, quiet=None, voices=1).reason == autostop.LIMIT


def test_switches_are_honest():
    off = autostop.limits_from_cfg({"sufler": {"autostop": {"enabled": False}}})
    assert not d(age=10 * 3600, quiet=None, limits=off)

    short = autostop.limits_from_cfg({"sufler": {"autostop": False}})
    assert not d(age=10 * 3600, quiet=None, limits=short)

    no_silence = autostop.limits_from_cfg({"sufler": {"autostop": {"silence_minutes": 0}}})
    assert not d(age=3 * 3600, quiet=None, limits=no_silence), "тишина выключена"
    assert d(age=7 * 3600, quiet=None, limits=no_silence).reason == autostop.LIMIT

    no_cap = autostop.limits_from_cfg({"sufler": {"autostop": {"max_hours": 0}}})
    assert not d(age=20 * 3600, quiet=10.0, voices=3, limits=no_cap), "потолок выключен"


def test_meeting_threshold_never_stricter_than_the_lonely_one():
    """Значения перепутали местами — живой разговор не должен резаться раньше
    пустой комнаты."""
    lim = autostop.limits_from_cfg({"sufler": {"autostop": {
        "silence_minutes": 20, "meeting_silence_minutes": 5}}})
    assert lim.meeting_silence_s == lim.silence_s == 20 * MIN
    assert not d(age=60 * MIN, quiet=18 * MIN, voices=3, limits=lim)


def test_broken_config_falls_back_to_defaults():
    lim = autostop.limits_from_cfg({"sufler": {"autostop": {
        "silence_minutes": "пять", "max_hours": None, "warn_seconds": -10}}})
    assert lim.silence_s == 5 * MIN and lim.max_s == 6 * 3600
    assert lim.warn_s == 0.0, "отрицательное — это ноль, а не сюрприз"


def test_texts_are_human_readable():
    assert "5 минут" in d(age=20 * MIN, quiet=5 * MIN + 1, voices=1).text
    assert "речи не было" in d(age=6 * MIN, quiet=None).text
    assert "6 ч" in d(age=7 * 3600, quiet=10.0).text
