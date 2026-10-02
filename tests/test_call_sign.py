"""Признак звонка — один порог речи канала собеседников для живой ленты и
пересборки (№586).

Гейт хаба — энергия, не модель речи: «дзынь» уведомления на очной проходит его
одним-тремя чанками. Звонок — это плотность: ≥ CALL_MIN_SPEECH_S прошедших
чанков (число × шаг) внутри окна CALL_WINDOW_S. Сырое «канал хоть раз прошёл
гейт» живёт отдельно — его читают автостоп и статус.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import owner_voice as ov  # noqa: E402
import speech_gate  # noqa: E402

STEP = 2.5
SR = 16000


def _gate(times, *, is_mic=False) -> ov.Heard:
    heard = ov.Heard()
    for at in times:
        heard.note_gate(at, STEP, is_mic=is_mic)
    return heard


# ------------------------------------------------------------ формула гейта

def test_a_chunk_is_speech_strictly_above_the_threshold():
    loud = np.full(SR, 10 ** (-40 / 20), dtype=np.float32)       # −40 дБFS
    quiet = np.full(SR, 10 ** (-60 / 20), dtype=np.float32)      # −60 дБFS
    assert speech_gate.chunk_is_speech(loud, -55.0)
    assert not speech_gate.chunk_is_speech(quiet, -55.0)
    assert not speech_gate.chunk_is_speech(np.zeros(SR, dtype=np.float32), -55.0)
    assert not speech_gate.chunk_is_speech(loud, -40.0)          # ровно порог — ещё не речь


def test_the_hub_and_the_rebuild_share_one_formula():
    """AudioHub.is_speech зовёт тот же гейт: правка формулы в одном месте не
    разведёт живой признак с пересборочным."""
    import audio
    hub = audio.AudioHub.__new__(audio.AudioHub)
    hub.vad_db = -55.0
    for level_db in (-70.0, -55.5, -54.5, -30.0):
        chunk = np.full(SR, 10 ** (level_db / 20), dtype=np.float32)
        assert hub.is_speech(chunk) == speech_gate.chunk_is_speech(chunk, -55.0)


def test_settings_read_the_audio_section():
    assert speech_gate.settings({"chunk_seconds": 3, "overlap_seconds": "0.5",
                                 "vad_energy_db": -55, "samplerate": 16000}) == (3.0, 0.5, -55.0)
    with pytest.raises(KeyError):
        speech_gate.settings({"chunk_seconds": 3.0, "overlap_seconds": 0.5})


def test_speech_starts_cut_the_recording_like_the_hub():
    """Окно 3 с, шаг 2,5 с от нуля; хвост короче окна не судится."""
    x = np.zeros(SR * 11, dtype=np.float32)
    x[int(5.2 * SR):int(5.6 * SR)] = 0.5                         # звук на 5,2–5,6 с
    starts = speech_gate.speech_starts(x, SR, 3.0, 0.5, -55.0)
    assert starts == [2.5, 5.0]                                  # окна 2,5–5,5 и 5,0–8,0
    assert speech_gate.speech_starts(np.full(SR * 11, 0.5, dtype=np.float32),
                                     SR, 3.0, 0.5, -55.0) == [0.0, 2.5, 5.0, 7.5]
    assert speech_gate.speech_starts(x, SR, 3.0, 3.0, -55.0) == []   # шаг 0 — не режем
    assert speech_gate.step_seconds(3.0, 0.5) == 2.5


# ------------------------------------------------------------ правило окна

def test_thirty_seconds_in_the_window_is_a_call_one_chunk_less_is_not():
    need = int(ov.CALL_MIN_SPEECH_S / STEP)                      # 12 чанков × 2,5 с
    assert _gate([i * STEP for i in range(need)]).call
    assert not _gate([i * STEP for i in range(need - 1)]).call


def test_the_window_is_a_window_not_a_sum_over_the_meeting():
    """Уведомления на трёхчасовой очной: по чанку раз в 12 минут — 15 штук,
    37,5 с в сумме, но в любом окне 120 с — один чанк. Не звонок."""
    heard = _gate([k * 720.0 for k in range(15)])
    assert not heard.call
    assert ov.call_state(heard) == ov.CALL_PENDING


def test_chunks_spread_wider_than_the_window_do_not_add_up():
    """12 чанков, но по одному раз в 11 с: в окно 120 с влезает 11 — 27,5 с."""
    assert not _gate([k * 11.0 for k in range(12)]).call
    assert _gate([k * 10.0 for k in range(12)]).call           # 12 в 110 с — уже звонок


def test_the_call_is_sticky():
    heard = _gate([i * STEP for i in range(12)])
    for k in range(40):                                          # потом час редких чанков
        heard.note_gate(1000.0 + k * 90.0, STEP, is_mic=False)
    assert heard.call and ov.call_state(heard) == ov.CALL_YES


def test_the_mic_never_counts():
    heard = _gate([i * STEP for i in range(100)], is_mic=True)
    assert not heard.call and not heard.bh_heard
    assert ov.call_state(heard) == ov.CALL_NONE


def test_call_state_has_three_answers():
    assert ov.call_state(ov.Heard()) == ov.CALL_NONE
    assert ov.call_state(_gate([0.0])) == ov.CALL_PENDING
    assert ov.call_state(_gate([i * STEP for i in range(12)])) == ov.CALL_YES


def test_note_does_not_set_the_call_any_more():
    """Секунды трекера и распознанная речь признак звонка не ставят: его ставит
    только гейт, один раз на чанк (вход №586, круг 1)."""
    heard = ov.Heard()
    heard.note(3, 600.0, is_mic=False)
    heard.note(None, 0.0, is_mic=False)
    assert not heard.call and ov.call_state(heard) == ov.CALL_NONE


def test_call_from_gate_is_the_live_rule_over_a_recording():
    assert ov.call_from_gate([], STEP) is False
    assert ov.call_from_gate([i * STEP for i in range(12)], STEP) is True
    assert ov.call_from_gate([k * 720.0 for k in range(15)], STEP) is False
    for times in ([k * 11.0 for k in range(12)], [k * 10.0 for k in range(12)]):
        assert ov.call_from_gate(times, STEP) == _gate(times).call


def test_a_call_cut_to_twenty_seconds_is_not_a_call_forty_is():
    """Синтетика из таблицы замера: звук на всю запись канала."""
    def call(seconds):
        x = np.full(int(SR * seconds), 0.05, dtype=np.float32)
        return ov.call_from_gate(speech_gate.speech_starts(x, SR, 3.0, 0.5, -55.0), STEP)
    assert not call(20)
    assert call(40)


# ------------------------------------------------------------ живой путь демона

def _mic(heard: ov.Heard, seconds: float) -> ov.Heard:
    heard.note(0, seconds, is_mic=True)
    return heard


def test_alone_reads_the_raw_sign_not_the_threshold():
    """Автостоп: пока порог звонка копится, владелец не одинок (выход №586)."""
    assert ov.alone(ov.Heard())
    assert not ov.alone(_gate([0.0]))                        # звук был, порога нет
    assert not ov.alone(_gate([i * STEP for i in range(12)]))


def test_the_unsigned_reason_waits_for_speech_and_for_the_threshold():
    """Статус демона: мало речи или порог копится — молчим; канал тих — «очная»;
    звонок без подписи — «несколько человек» (выход №586, обе головы)."""
    few = ov.MIN_MIC_SECONDS - 1
    enough = ov.MIN_MIC_SECONDS + 1
    assert ov.unsigned_reason(_mic(ov.Heard(), few)) == ov.SAY_NOTHING
    assert ov.unsigned_reason(_mic(ov.Heard(), enough)) == ov.SAY_ROOM
    assert ov.unsigned_reason(_mic(_gate([0.0]), enough)) == ov.SAY_NOTHING
    assert ov.unsigned_reason(_mic(_gate([i * STEP for i in range(12)]), enough)) == ov.SAY_CROWD


def _daemon_tree():
    # Через импорт, а не путём к файлу: мутатор выбирает тесты модуля по
    # импорту, и сторож проводки без него мутантов демона не видит (CI #721).
    import ast
    import daemon
    return ast.parse(pathlib.Path(daemon.__file__).read_text(encoding="utf-8"))


def _daemon_calls(attr: str) -> list:
    import ast
    return [n for n in ast.walk(_daemon_tree()) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def test_the_daemon_feeds_the_gate_from_the_batch_with_the_hub_step():
    """Проводка живого признака: демон кормит `note_gate` ровно в одном месте —
    моментом чанка на оси хаба в секундах и шагом разреза хаба. Юниты `Heard`
    не видят, что вызов удалён или кормится нулём (выход №586, обе головы)."""
    import ast
    calls = _daemon_calls("note_gate")
    assert len(calls) == 1
    at, step = calls[0].args
    assert ast.unparse(at) == "placed.start / hub.sr"
    assert ast.unparse(step) == "gate_step_s"
    steps = _daemon_calls("step_seconds")
    assert [ast.unparse(c) for c in steps] == [
        "speech_gate.step_seconds(hub.chunk_s, hub.overlap_s)"]


def test_the_daemon_asks_owner_voice_who_is_alone_and_why_unsigned():
    """Автостоп и статус читают решения `owner_voice`, а не собирают их сами."""
    import ast
    alone_kw = [kw for c in _daemon_calls("tick") for kw in c.keywords if kw.arg == "alone"]
    assert [ast.unparse(kw.value) for kw in alone_kw] == ["owner_voice.alone(heard_by_channel)"]
    assert len(_daemon_calls("unsigned_reason")) == 1


def test_the_daemon_status_branches_follow_the_unsigned_reason():
    """Ветки `_say_owner_state`: «рано» молчит, «комната» говорит про очную.
    Решение судят тесты `unsigned_reason`; здесь — что демон читает его
    равенством и не путает тексты веток (мутации CI #721)."""
    import ast
    fn = next(n for n in ast.walk(_daemon_tree())
              if isinstance(n, ast.FunctionDef) and n.name == "_say_owner_state")
    branches = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and ast.unparse(node.test.left) == "why"):
            assert [type(op) for op in node.test.ops] == [ast.Eq]
            branches[ast.unparse(node.test.comparators[0])] = node.body
    assert set(branches) == {"owner_voice.SAY_NOTHING", "owner_voice.SAY_ROOM"}
    assert [type(n) for n in branches["owner_voice.SAY_NOTHING"]] == [ast.Return]
    assert "очн" in ast.unparse(branches["owner_voice.SAY_ROOM"][0])
