"""Автостоп записи: когда останавливаем сами, а когда молчим.

17.08 запись шла 18 ч 25 мин в пустой комнате. Здесь проверяется правило,
которое это закрывает, и — важнее — что оно не срывает живую встречу: пауза
на чтение документа, тихое демо, первые минуты записи, очная встреча без
моделей диаризации (все реплики одной меткой).
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import autostop  # noqa: E402

L = autostop.limits_from_cfg({})       # дефолты: 5 мин без речи, 15 тишины, 6 ч
MIN = 60.0


def d(*, age: float, quiet: float, spoke: bool = True, limits=L):
    return autostop.decide(age_s=age, quiet_s=quiet, spoke=spoke, limits=limits)


def test_forgotten_recording_in_an_empty_room_stops():
    """Ровно случай 17.08: включили, ушли, речи не было вовсе."""
    stop = d(age=6 * MIN, quiet=6 * MIN, spoke=False)
    assert stop.action == "stop" and stop.reason == autostop.NO_SPEECH
    assert "речи не было" in stop.text


def test_room_that_went_quiet_gets_fifteen_minutes():
    """Говорили и замолчали: пауза на чтение документа не повод останавливать."""
    assert not d(age=40 * MIN, quiet=10 * MIN)
    assert d(age=40 * MIN, quiet=14.5 * MIN).action == "warn", "сначала предупреждение"
    stop = d(age=40 * MIN, quiet=16 * MIN)
    assert stop.action == "stop" and stop.reason == autostop.SILENCE


def test_single_label_meeting_is_not_punished():
    """Очная встреча без моделей диаризации: все реплики идут одной меткой
    канала. Порог по числу «голосов» резал бы такой живой разговор через пять
    минут паузы — поэтому решает только факт речи (ревью 18.08, DeepSeek)."""
    assert not d(age=60 * MIN, quiet=12 * MIN, spoke=True)


def test_first_minutes_are_never_touched():
    """«Включил, пока все собираются» — запись не должна умереть на старте."""
    assert not d(age=1 * MIN, quiet=1 * MIN, spoke=False)
    assert not d(age=1.9 * MIN, quiet=1.9 * MIN, spoke=False)


def test_warning_says_how_to_cancel_and_never_promises_zero_minutes():
    warn = d(age=20 * MIN, quiet=15 * MIN - 30)
    assert warn.action == "warn" and 0 < warn.seconds_left <= 60
    assert "скажите" in warn.text
    assert "0 минут" not in warn.text, "«через 0 минут» — не срок, а недоразумение"
    assert not d(age=21 * MIN, quiet=2.0), "заговорили — предупреждение снимается"


def test_duration_ceiling_stops_even_a_live_meeting():
    assert not d(age=5.9 * 3600, quiet=10.0)
    assert d(age=6 * 3600 - 30, quiet=10.0).action == "warn"
    stop = d(age=6 * 3600 + 1, quiet=10.0)
    assert stop.action == "stop" and stop.reason == autostop.LIMIT


def test_ceiling_wins_over_silence_when_both_fire():
    """Обе причины сразу — называем потолок: он безусловен, тишина обсуждаема."""
    assert d(age=7 * 3600, quiet=7 * 3600, spoke=False).reason == autostop.LIMIT


def test_switches_are_honest():
    off = autostop.limits_from_cfg({"sufler": {"autostop": {"enabled": False}}})
    assert not d(age=10 * 3600, quiet=10 * 3600, spoke=False, limits=off)

    for value in (False, "false", "нет", 0):
        lim = autostop.limits_from_cfg({"sufler": {"autostop": value}})
        assert not lim.any_rule, f"autostop: {value!r} должен выключать"

    only_meeting = autostop.limits_from_cfg({"sufler": {"autostop": {
        "no_speech_minutes": 0, "max_hours": 0}}})
    assert not d(age=3 * 3600, quiet=3 * 3600, spoke=False, limits=only_meeting)
    assert d(age=3 * 3600, quiet=20 * MIN, limits=only_meeting).action == "stop", \
        "выключенное правило «речи не было» не смеет глушить тишину после разговора"

    only_idle = autostop.limits_from_cfg({"sufler": {"autostop": {
        "silence_minutes": 0, "max_hours": 0}}})
    assert not d(age=3 * 3600, quiet=60 * MIN, limits=only_idle)
    assert d(age=10 * MIN, quiet=6 * MIN, spoke=False, limits=only_idle).action == "stop"

    no_cap = autostop.limits_from_cfg({"sufler": {"autostop": {"max_hours": 0}}})
    assert not d(age=20 * 3600, quiet=10.0, limits=no_cap), "потолок выключен"


def test_silence_threshold_never_stricter_than_the_empty_room():
    """Значения перепутали местами — живой разговор не должен резаться раньше
    пустой комнаты."""
    lim = autostop.limits_from_cfg({"sufler": {"autostop": {
        "no_speech_minutes": 20, "silence_minutes": 5}}})
    assert lim.silence_s == lim.no_speech_s == 20 * MIN
    assert not d(age=60 * MIN, quiet=18 * MIN, limits=lim)


def test_broken_config_falls_back_to_defaults():
    lim = autostop.limits_from_cfg({"sufler": {"autostop": {
        "no_speech_minutes": "пять", "max_hours": None, "warn_seconds": -10}}})
    assert lim.no_speech_s == 5 * MIN and lim.max_s == 6 * 3600
    assert lim.warn_s == 0.0, "отрицательное — это ноль, а не сюрприз"


# --------------------------------------------------------------- Watch (состояние)

def test_watch_warns_once_and_takes_it_back_when_talk_resumes():
    w = autostop.Watch(L)
    first = w.tick(now=100.0, age_s=20 * MIN, quiet_s=15 * MIN - 30, spoke=True)
    assert first.action == "warn"
    again = w.tick(now=105.0, age_s=20 * MIN, quiet_s=15 * MIN - 25, spoke=True)
    assert not again, "предупреждение не повторяется каждые пять секунд"
    back = w.tick(now=110.0, age_s=21 * MIN, quiet_s=3.0, spoke=True)
    assert back.action == "resumed", "заговорили — сказать, что автостоп снят"
    assert not w.tick(now=115.0, age_s=21 * MIN, quiet_s=8.0, spoke=True)


def test_watch_does_not_nag_after_an_unanswered_request():
    """Приложение не ответило (старая версия): просим один раз, потом молчим."""
    w = autostop.Watch(L, mute_s=1800.0)
    asked = w.tick(now=1000.0, age_s=40 * MIN, quiet_s=16 * MIN, spoke=True)
    assert asked.action == "stop"
    assert not w.tick(now=1100.0, age_s=42 * MIN, quiet_s=18 * MIN, spoke=True)
    assert not w.tick(now=2500.0, age_s=65 * MIN, quiet_s=40 * MIN, spoke=True)
    # прошли полчаса — пробуем ещё раз
    assert w.tick(now=2900.0, age_s=72 * MIN, quiet_s=48 * MIN, spoke=True).action == "stop"


def test_watch_wakes_up_as_soon_as_the_talk_returns():
    """Поговорили после неудачной просьбы и снова ушли — ждать полчаса нельзя."""
    w = autostop.Watch(L, mute_s=1800.0)
    assert w.tick(now=1000.0, age_s=40 * MIN, quiet_s=16 * MIN, spoke=True).action == "stop"
    # в 1200 прозвучала речь
    assert not w.tick(now=1210.0, age_s=44 * MIN, quiet_s=10.0, spoke=True,
                      last_speech_at=1200.0)
    stop = w.tick(now=2200.0, age_s=60 * MIN, quiet_s=16 * MIN, spoke=True,
                  last_speech_at=1200.0)
    assert stop.action == "stop", "новая тишина — новая просьба, а не остаток мута"


# --- Правило 3: тишина, когда собеседников не было слышно (20.08) ------------

def test_один_без_собеседников_режется_коротким_порогом():
    """Говорил только владелец, системный канал молчал всю запись: пятнадцать
    минут — перестраховка под паузу в разговоре, которого не было."""
    limits = autostop.limits_from_cfg({})
    d = autostop.decide(age_s=900, quiet_s=610, spoke=True, limits=limits, alone=True)
    assert d.action == "stop" and d.reason == autostop.ALONE
    assert "собеседников не слышно" in d.text, "человек должен понять, почему раньше"


def test_тот_же_случай_с_собеседниками_ждёт_пятнадцать_минут():
    limits = autostop.limits_from_cfg({})
    d = autostop.decide(age_s=900, quiet_s=610, spoke=True, limits=limits, alone=False)
    assert not d, "разговор двоих не должен резаться на пятиминутной паузе"
    late = autostop.decide(age_s=1200, quiet_s=910, spoke=True, limits=limits, alone=False)
    assert late.action == "stop" and late.reason == autostop.SILENCE


def test_очную_встречу_можно_вернуть_одной_строкой_конфига():
    """Канал не отличает очную встречу от диктофона — у кого встречи очные,
    поднимает порог обратно."""
    limits = autostop.limits_from_cfg({"sufler": {"autostop": {"alone_minutes": 15}}})
    d = autostop.decide(age_s=900, quiet_s=610, spoke=True, limits=limits, alone=True)
    assert not d
    assert limits.alone_s == 900


def test_ноль_выключает_своё_правило_но_не_соседнее():
    """Мой первый тест кодировал ДЕФЕКТ: проверял, что при alone_minutes: 0
    решения нет — и пропускал, что вместе с ним пропадала обычная тишина.
    Человек, вернувший себе пятнадцать минут нулём, получал ноль автостопа
    вовсе (ревью 20.08, DeepSeek C1)."""
    limits = autostop.limits_from_cfg({"sufler": {"autostop": {"alone_minutes": 0}}})
    assert limits.alone_s == 0
    assert limits.silence_s == 900 and limits.no_speech_s == 300
    # пятиминутная пауза — рано для всех
    assert not autostop.decide(age_s=600, quiet_s=310, spoke=True,
                               limits=limits, alone=True)
    # а пятнадцать минут тишины обязаны сработать по обычному правилу
    late = autostop.decide(age_s=1200, quiet_s=910, spoke=True, limits=limits, alone=True)
    assert late.action == "stop" and late.reason == autostop.SILENCE


def test_выключенная_тишина_не_включает_alone_втихую():
    """Старый конфиг с silence_minutes: 0 не должен молча получить новое
    правило, которого владелец не просил (ревью 20.08, DeepSeek I3)."""
    limits = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"silence_minutes": 0, "no_speech_minutes": 0,
                                 "max_hours": 0}}})
    assert limits.alone_s == 0 and not limits.any_rule


def test_одинокая_запись_не_режется_раньше_пустой_комнаты():
    """Перепутанные местами значения не должны давать порог строже, чем
    «речи не было ни разу» — тот же порядок, что у silence_s."""
    limits = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"alone_minutes": 1, "no_speech_minutes": 5}}})
    assert limits.alone_s == 300


def test_молчание_вообще_остаётся_за_правилом_один():
    """Никто не говорил и собеседников нет — это по-прежнему no_speech,
    отдельная причина для человека в статусе."""
    limits = autostop.limits_from_cfg({})
    d = autostop.decide(age_s=400, quiet_s=310, spoke=False, limits=limits, alone=True)
    assert d.action == "stop" and d.reason == autostop.NO_SPEECH


def test_признак_звонка_не_зависит_от_трекера_голосов():
    """Модель диаризации в поставку не входит. Если признак «слышно
    собеседников» ставить только когда трекер определил голос, обычная
    удалённая встреча выглядит как разговор с самим собой — и правило alone
    режет её втрое раньше срока (ревью 20.08, локальная голова)."""
    from owner_voice import Heard

    heard = Heard()
    # трекер не определился: голоса нет, но канал известен
    heard.note(None, 0.0, is_mic=False)
    assert heard.call, "речь с системного канала — это собеседник, даже без трекера"

    свой = Heard()
    свой.note(None, 0.0, is_mic=True)
    assert not свой.call, "собственный микрофон признаком звонка не является"


def test_явный_alone_работает_даже_при_выключенной_тишине():
    """Гвард против «воскрешения по умолчанию» не должен глушить просьбу.

    Конфиг `silence_minutes: 0, alone_minutes: 10` — это «обычную тишину не
    считай, а диктофон обрывай». Первая версия гварда давала на нём ноль
    автостопа вовсе (ревью 20.08, второй круг, DeepSeek I1).
    """
    limits = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"silence_minutes": 0, "alone_minutes": 10}}})
    assert limits.alone_s == 600 and limits.silence_s == 0

    d = autostop.decide(age_s=1800, quiet_s=611, spoke=True, limits=limits, alone=True)
    assert d.action == "stop" and d.reason == autostop.ALONE
    # а разговор с собеседниками при выключенной тишине по-прежнему не трогаем
    assert not autostop.decide(age_s=1800, quiet_s=611, spoke=True,
                               limits=limits, alone=False)


def test_дефолт_limits_совпадает_с_конфигом():
    """Две точки истины разошлись бы молча: DEFAULTS подняли, датакласс нет."""
    assert autostop.Limits().alone_s == autostop.DEFAULTS["alone_minutes"] * 60


# --- Прощания (№151, запрос владельца 01.09) ---

def test_farewell_detector_forms():
    """Короткие прощальные реплики матчатся, союз «пока» — нет."""
    yes = ["Всем пока!", "Ну всё, пока.", "До свидания", "Пока",
           "давайте пока", "Спасибо, до связи", "До завтра", "Пока-пока"]
    no = ["пока не забыл про отчёт", "я пока посмотрю документ",
          "пока идёт репликация посмотрим логи",
          "созвонимся завтра по этому вопросу и обсудим детали позже",
          "всем нужно посмотреть протокол до завтрашней встречи и дать ответ",
          # слабые формы — не прощание сами по себе: живой замер поймал их
          # посреди встреч («Ну, а пока» на 23-й реплике из 266)
          "хорошего дня", "Всем спасибо.", "Ну, а пока. Пока.",
          "Пока. Угу, спасибо, хорошего дня."]
    for t in yes:
        assert autostop.is_farewell(t), t
    for t in no:
        assert not autostop.is_farewell(t), t


def test_one_farewell_cuts_silence_threshold_with_instant_warn():
    """Одно прощание: порог тишины 60с вместо 15 минут, warn сразу."""
    lim = autostop.limits_from_cfg({})
    d = autostop.decide(age_s=600, quiet_s=5, spoke=True, limits=lim,
                        farewells=1)
    assert d.action == "warn" and d.reason == autostop.FAREWELL, d
    d = autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim,
                        farewells=1)
    assert d.action == "stop" and d.reason == autostop.FAREWELL, d


def test_two_farewells_stop_immediately():
    """Обмен прощаниями (≥2 подряд) — стоп без ожидания тишины.
    Риск двойного «пока» посреди встречи взят владельцем явно (01.09)."""
    lim = autostop.limits_from_cfg({})
    d = autostop.decide(age_s=600, quiet_s=0, spoke=True, limits=lim,
                        farewells=2)
    assert d.action == "stop" and d.reason == autostop.FAREWELL, d


def test_farewell_needs_speech_and_min_age():
    """Прощание не трогает свежую запись (min_s) и не работает без речи."""
    lim = autostop.limits_from_cfg({})
    assert not autostop.decide(age_s=30, quiet_s=0, spoke=True, limits=lim,
                               farewells=2)
    assert not autostop.decide(age_s=600, quiet_s=5, spoke=False, limits=lim,
                               farewells=2).reason == autostop.FAREWELL


def test_farewell_zero_disables_rule():
    """farewell_seconds: 0 выключает обе ветки правила."""
    lim = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"farewell_seconds": 0}}})
    assert not autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim,
                               farewells=2)
    d = autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim,
                        farewells=1)
    assert d.reason != autostop.FAREWELL


def test_farewell_does_not_raise_stricter_threshold():
    """Если обычный порог УЖЕ короче (нестандартный конфиг), прощание его
    не удлиняет."""
    lim = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"silence_minutes": 0.5,
                                 "no_speech_minutes": 0.5,
                                 "farewell_seconds": 120}}})
    d = autostop.decide(age_s=600, quiet_s=40, spoke=True, limits=lim,
                        farewells=1)
    assert d.reason != autostop.FAREWELL or d.action == ""


def test_watch_resumes_after_farewell_when_talk_continues():
    """Warn после прощания снимается новой речью — «автостоп отменён»."""
    lim = autostop.limits_from_cfg({})
    w = autostop.Watch(lim)
    d = w.tick(now=1000, age_s=600, quiet_s=5, spoke=True, farewells=1)
    assert d.action == "warn" and d.reason == autostop.FAREWELL
    # разговор продолжился: farewells сброшен демоном, тишины нет
    d = w.tick(now=1010, age_s=610, quiet_s=2, spoke=True, farewells=0)
    assert d.action == "resumed", d


def test_break_announcement_is_not_farewell():
    """«Через пять минут увидимся» — перерыв: единственный ложный класс,
    найденный смоуком по 20 тысячам живых реплик."""
    assert not autostop.is_farewell("Через пять минут увидимся.")
    assert autostop.is_farewell("Всё, давай, увидимся.")


def test_streak_dedups_chunk_seam_and_echo():
    """DS r1 по #471: шов чанков («всем пока» → хвост «пока») и эхо той
    же фразы в двух каналах не удваивают одно прощание."""
    st = autostop.FarewellStreak()
    assert st.feed("всем пока", now=10.0) == 1
    assert st.feed("пока", now=11.5) == 1          # шовный хвост — не второй
    assert st.feed("всем пока", now=12.0) == 1     # эхо канала — не второй
    # настоящий второй: другие слова ЛИБО пауза больше окна
    assert st.feed("до свидания", now=13.0) == 2


def test_streak_counts_late_repeat_as_real():
    """То же «пока» после паузы больше эхо-окна — настоящий обмен."""
    st = autostop.FarewellStreak()
    assert st.feed("пока", now=10.0) == 1
    assert st.feed("пока", now=16.0) == 2


def test_streak_resets_on_ordinary_remark():
    st = autostop.FarewellStreak()
    st.feed("всем пока", now=10.0)
    assert st.feed("стой, ещё один вопрос по отчёту", now=11.0) == 0
    assert st.feed("ну всё, пока", now=12.0) == 1


def test_any_rule_counts_farewell_alone():
    """Minor-2 DS: конфиг с одними прощаниями — правило живо."""
    lim = autostop.limits_from_cfg({"sufler": {"autostop": {
        "no_speech_minutes": 0, "silence_minutes": 0, "alone_minutes": 0,
        "max_hours": 0, "farewell_seconds": 60}}})
    assert lim.any_rule
    d = autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim,
                        farewells=1)
    assert d.action == "stop" and d.reason == autostop.FAREWELL, d


def test_farewell_does_not_resurrect_disabled_silence():
    """GLM r1 по #471: silence_minutes: 0 — «не останавливай после
    молчания»; дефолтное прощание порог не воскрешает, ЯВНЫЙ
    farewell_seconds — просьба и работает."""
    lim = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"silence_minutes": 0, "alone_minutes": 0}}})
    d = autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim,
                        farewells=1)
    assert d.reason != autostop.FAREWELL, d
    lim2 = autostop.limits_from_cfg(
        {"sufler": {"autostop": {"silence_minutes": 0, "alone_minutes": 0,
                                 "farewell_seconds": 60}}})
    d2 = autostop.decide(age_s=600, quiet_s=61, spoke=True, limits=lim2,
                        farewells=1)
    assert d2.action == "stop" and d2.reason == autostop.FAREWELL, d2


def test_break_in_a_while_wording_not_farewell():
    """GLM r1: «спустя» — тот же класс анонса перерыва, что «через»."""
    assert not autostop.is_farewell("Спустя пять минут увидимся")
