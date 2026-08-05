"""Метрика, которой меряют диаризацию, сама нуждается в проверке.

«Путает говорящих» до сих пор было мнением: качество памяти в проекте
меряется, качество разделения голосов — нет. Числа без проверенной метрики
хуже отсутствия чисел: на них начинают опираться.

DER (diarization error rate) — доля времени речи, подписанная неверно:
пропущенная речь, речь, услышанная в тишине, и время, отданное не тому
говорящему. Ключевая тонкость: диаризация не обязана угадывать ИМЕНА. Она
обязана отличать людей друг от друга, поэтому метки гипотезы сначала
сопоставляются с эталонными, и «speaker 0 = Милена» ошибкой не считается.

Здесь проверяется именно это: сопоставление меток, три слагаемых ошибки по
отдельности и границы (идеальный ответ, молчание, всё одним голосом).
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

# diar_bench на уровне модуля тянет numpy и soundfile. В CI они стоят всегда
# (зависимости из pyproject.toml ставятся целиком), а вот на неполном локальном окружении
# голый import ронял СБОР всех тестов, не только этих. skip честнее падения:
# здесь проверяется метрика, а не установленность аудиостека.
pytest.importorskip("numpy")
pytest.importorskip("soundfile")

import diar_bench  # noqa: E402

TRUTH = [
    {"start": 0.0, "end": 2.0, "speaker": "Милена"},
    {"start": 2.0, "end": 4.0, "speaker": "Фёдор"},
    {"start": 5.0, "end": 7.0, "speaker": "Милена"},
]
TOTAL = 8.0     # последняя секунда — тишина, её никто не обязан размечать


def test_perfect_answer_scores_zero():
    assert diar_bench.der(TRUTH, TRUTH, TOTAL)["der"] == 0.0


def test_renaming_speakers_is_not_an_error():
    """«spk0» вместо «Милена» — не ошибка: имена расставляет другой слой."""
    hyp = [{"start": s["start"], "end": s["end"],
            "speaker": "spk0" if s["speaker"] == "Милена" else "spk1"}
           for s in TRUTH]
    assert diar_bench.der(TRUTH, hyp, TOTAL)["der"] == 0.0


def test_everything_as_one_voice_is_counted_as_confusion():
    """Худший практический случай: все реплики свалены в одного человека."""
    hyp = [{"start": s["start"], "end": s["end"], "speaker": "spk0"} for s in TRUTH]
    scores = diar_bench.der(TRUTH, hyp, TOTAL)
    assert scores["missed"] == 0.0 and scores["false_alarm"] == 0.0
    # два сегмента Милены (4с) сопоставятся с spk0, две секунды Фёдора — мимо
    assert abs(scores["confusion"] - 2 / 6) < 0.02, scores
    assert scores["speakers_hyp"] == 1 and scores["speakers_ref"] == 2


def test_missed_speech_is_counted():
    hyp = [TRUTH[0]]        # услышали только первую реплику
    scores = diar_bench.der(TRUTH, hyp, TOTAL)
    assert abs(scores["missed"] - 4 / 6) < 0.02, scores
    assert scores["false_alarm"] == 0.0


def test_speech_heard_in_silence_is_counted():
    hyp = TRUTH + [{"start": 7.0, "end": 8.0, "speaker": "Милена"}]
    scores = diar_bench.der(TRUTH, hyp, TOTAL)
    assert abs(scores["false_alarm"] - 1 / 6) < 0.02, scores
    assert scores["missed"] == 0.0


def test_silence_only_hypothesis_is_total_miss():
    assert abs(diar_bench.der(TRUTH, [], TOTAL)["der"] - 1.0) < 0.01


def test_der_is_not_secretly_capped_at_one():
    """Лишняя речь поверх путаницы может дать DER больше единицы — и должна:
    метрика не имеет права выглядеть лучше, чем есть."""
    hyp = ([{"start": s["start"], "end": s["end"], "speaker": "spk0"} for s in TRUTH]
           + [{"start": 7.0, "end": 8.0, "speaker": "spk9"}])
    assert diar_bench.der(TRUTH, hyp, TOTAL)["der"] > 0.4


def test_dialog_fixture_uses_contrasting_voices():
    """Фикстура должна проверять разделение, а не один голос сам с собой."""
    voices = {v for v, _ in diar_bench.DIALOG}
    assert len(voices) >= 3, f"мало голосов в синтетическом диалоге: {voices}"
    assert len(diar_bench.DIALOG) >= 6, "слишком короткий диалог для замера"
