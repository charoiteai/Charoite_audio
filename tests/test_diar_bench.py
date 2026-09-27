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
import importlib.util
import json
import pathlib
import subprocess
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
import numpy as np  # noqa: E402

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


# --- перекрытия: DER по NIST -------------------------------------------------
#
# Nemotron сравнивают ради перебиваний, а прежняя метрика клала на кадр одну
# метку — последнюю записанную. Двое говорят разом, система слышит одного —
# и это не считалось ошибкой вовсе. der_overlap считает честно и обязан
# совпадать с der там, где перекрытий нет: иначе старые замеры перестали бы
# сравниваться с новыми.

SINGLE_LABEL_HYPS = [
    TRUTH,
    [{"start": s["start"], "end": s["end"], "speaker": "spk0"} for s in TRUTH],
    [TRUTH[0]],
    TRUTH + [{"start": 7.0, "end": 8.0, "speaker": "Милена"}],
    [],
    ([{"start": s["start"], "end": s["end"], "speaker": "spk0"} for s in TRUTH]
     + [{"start": 7.0, "end": 8.0, "speaker": "spk9"}]),
]


@pytest.mark.parametrize("hyp", SINGLE_LABEL_HYPS)
@pytest.mark.parametrize("single", [False, True])
def test_overlap_metric_equals_old_one_without_overlaps(hyp, single):
    old = diar_bench.der(TRUTH, hyp, TOTAL)
    new = diar_bench.der_overlap(TRUTH, hyp, TOTAL, hyp_single=single)
    for key in old:
        assert new[key] == pytest.approx(old[key]), (key, old, new)
    assert new["overlap_ref_s"] == 0 and new["overlap_hyp_s"] == 0


CROSS = [{"start": 0.0, "end": 4.0, "speaker": "A"},
         {"start": 2.0, "end": 4.0, "speaker": "B"}]     # B перебил A на 2 с
CROSS_TOTAL = 5.0


def test_second_voice_in_crosstalk_missed_is_counted():
    """Слышим только A: две секунды B пропущены из шести секунд-голосов."""
    scores = diar_bench.der_overlap(CROSS, [CROSS[0]], CROSS_TOTAL)
    assert scores["missed"] == pytest.approx(2 / 6, abs=0.01)
    assert scores["confusion"] == 0 and scores["false_alarm"] == 0
    assert scores["overlap_ref_s"] == pytest.approx(2.0, abs=0.02)
    assert scores["overlap_hyp_s"] == 0
    # старая метрика в этом месте видела одного говорящего — пропуск ей не виден
    assert diar_bench.der(CROSS, [CROSS[0]], CROSS_TOTAL)["missed"] == 0


def test_both_voices_heard_scores_zero():
    hyp = [{"start": 0.0, "end": 4.0, "speaker": "x"},
           {"start": 2.0, "end": 4.0, "speaker": "y"}]
    scores = diar_bench.der_overlap(CROSS, hyp, CROSS_TOTAL)
    assert scores["der"] == 0.0
    assert scores["overlap_hyp_s"] == pytest.approx(2.0, abs=0.02)


def test_crosstalk_given_to_wrong_pair_is_confusion():
    """Два голоса услышаны, но второй — не тот: путаница, а не пропуск."""
    truth = CROSS + [{"start": 4.5, "end": 5.0, "speaker": "C"}]
    hyp = [{"start": 0.0, "end": 4.0, "speaker": "x"},
           {"start": 2.0, "end": 4.0, "speaker": "z"},
           {"start": 4.5, "end": 5.0, "speaker": "z"}]
    scores = diar_bench.der_overlap(truth, hyp, CROSS_TOTAL)
    assert scores["missed"] == 0 and scores["false_alarm"] == 0
    # z ↔ B (2 с против 0.5 с у C): путаница — полсекунды C
    assert scores["confusion"] == pytest.approx(0.5 / 6.5, abs=0.01)


def test_chunk_windows_of_a_single_label_engine_are_not_false_alarm():
    """Окна живого трекера перекрываются из-за нарезки, а не из-за двух голосов."""
    truth = [{"start": 0.0, "end": 3.0, "speaker": "A"},
             {"start": 3.0, "end": 6.0, "speaker": "B"}]
    windows = [{"start": 0.0, "end": 3.0, "speaker": "voice1"},
               {"start": 2.5, "end": 5.5, "speaker": "voice2"}]
    single = diar_bench.der_overlap(truth, windows, 6.0, hyp_single=True)
    sets = diar_bench.der_overlap(truth, windows, 6.0, hyp_single=False)
    assert single["false_alarm"] == 0
    assert sets["false_alarm"] == pytest.approx(0.5 / 6, abs=0.01)


# --- фикстура с перебиваниями -----------------------------------------------

def test_plain_layout_is_the_old_concatenation():
    """Без перекрытий фикстура обязана остаться прежней — старые замеры в силе."""
    lengths, pause = [100, 250, 80], 40
    assert diar_bench.layout_turns(lengths, pause, {}) == [0, 140, 430]
    parts = [np.full(n, 0.1 * (i + 1), dtype=np.float32) for i, n in enumerate(lengths)]
    silence = np.zeros(pause, dtype=np.float32)
    old = np.concatenate([x for p in parts for x in (p, silence)])
    new = diar_bench.mix_turns(parts, diar_bench.layout_turns(lengths, pause, {}), tail=pause)
    assert np.array_equal(old, new)


def test_crosstalk_turn_starts_before_previous_ends():
    starts = diar_bench.layout_turns([100, 100, 100], 40, {1: 30})
    assert starts[1] == 70                      # залезла на 30 сэмплов
    assert starts[2] == 170 + 40                # следующая ждёт, пока замолчат все


def test_crosstalk_never_starts_before_previous_turn():
    starts = diar_bench.layout_turns([50, 100], 10, {1: 500})
    assert starts == [0, 0]


def test_long_turn_holds_the_floor_over_a_short_interjection():
    """Короткая реплика внутри длинной: следующая ждёт конца длинной."""
    starts = diar_bench.layout_turns([300, 20, 50], 10, {1: 100})
    assert starts[1] == 200 and starts[2] == 300 + 10


def test_voices_add_up_in_crosstalk_and_never_clip():
    loud = [np.full(10, 0.8, dtype=np.float32), np.full(10, 0.8, dtype=np.float32)]
    mixed = diar_bench.mix_turns(loud, [0, 5], tail=0)
    assert len(mixed) == 15
    assert np.max(np.abs(mixed)) <= 0.99 + 1e-6
    assert mixed[7] > mixed[2], "в перекрытии голоса не сложились"


def test_crosstalk_script_overlaps_different_voices():
    """Перебивание собой — не перебивание: соседние реплики — разные голоса."""
    for i in diar_bench.CROSSTALK:
        assert 0 < i < len(diar_bench.DIALOG)
        assert diar_bench.DIALOG[i][0] != diar_bench.DIALOG[i - 1][0], i


def test_has_overlap_sees_crosstalk_only_between_different_voices():
    assert diar_bench.has_overlap(CROSS)
    assert not diar_bench.has_overlap(TRUTH)
    assert not diar_bench.has_overlap([{"start": 0, "end": 2, "speaker": "A"},
                                       {"start": 1, "end": 3, "speaker": "A"}])


# --- своя запись: эталон и метки Audacity ------------------------------------

def test_audacity_labels_are_read_as_truth(tmp_path):
    f = tmp_path / "labels.txt"
    f.write_text("0.000000\t2.500000\tМилена Петровна\n"
                 "\\\t0.000000\t0.000000\n"            # частотное выделение Audacity
                 "2,5\t4,0\tФёдор\n"                   # десятичная запятая
                 "5.0\t5.0\tточка\n"                   # метка-точка: не речь
                 "\n", encoding="utf-8")
    assert diar_bench.read_truth(f) == [
        {"start": 0.0, "end": 2.5, "speaker": "Милена Петровна"},
        {"start": 2.5, "end": 4.0, "speaker": "Фёдор"},
    ]


def test_rttm_and_json_are_read_as_truth(tmp_path):
    rttm = tmp_path / "ref.rttm"
    rttm.write_text("SPEAKER meet 1 1.50 2.25 <NA> <NA> alice <NA> <NA>\n", encoding="utf-8")
    assert diar_bench.read_truth(rttm) == [{"start": 1.5, "end": 3.75, "speaker": "alice"}]
    js = tmp_path / "truth.json"
    js.write_text(json.dumps({"segments": TRUTH}, ensure_ascii=False), encoding="utf-8")
    assert diar_bench.read_truth(js) == TRUTH


@pytest.mark.parametrize("line, why", [
    ("0.0\t1.0\n", "кто говорит"),
    ("0.0\t1.0\t   \n", "нет имени"),
    ("ноль\t1.0\tА\n", "кто говорит"),
])
def test_broken_truth_names_the_line(tmp_path, line, why):
    f = tmp_path / "labels.txt"
    f.write_text("0.0\t1.0\tА\n" + line, encoding="utf-8")
    with pytest.raises(ValueError, match=rf"labels\.txt:2: .*{why}"):
        diar_bench.read_truth(f)


def test_labels_written_for_audacity_read_back_the_same(tmp_path):
    hyp = [{"start": 2.0, "end": 4.0, "speaker": "nem1"},
           {"start": 0.0, "end": 3.0, "speaker": "nem0"}]
    f = tmp_path / "nemotron.txt"
    diar_bench.write_labels(hyp, f)
    assert f.read_text(encoding="utf-8").splitlines()[0] == "0.000\t3.000\tnem0"
    assert diar_bench.read_truth(f) == sorted(hyp, key=lambda s: s["start"])


def test_blind_summary_counts_voices_speech_and_crosstalk():
    hyp = [{"start": 0.0, "end": 4.0, "speaker": "x"},
           {"start": 2.0, "end": 4.0, "speaker": "y"}]
    s = diar_bench.summary(hyp, 5.0)
    assert s["voices"] == 2 and s["switches"] == 1
    assert s["speech_s"] == pytest.approx(4.0, abs=0.02)
    assert s["overlap_s"] == pytest.approx(2.0, abs=0.02)
    # тот же вход от движка «одна метка на момент» — перекрытия нет по контракту
    assert diar_bench.summary(hyp, 5.0, hyp_single=True)["overlap_s"] == 0


# --- движки -------------------------------------------------------------------

def test_every_engine_choice_has_an_implementation():
    import argparse
    engines = diar_bench._engines(argparse.Namespace())
    assert set(engines) == set(diar_bench.ENGINE_NAMES)
    for group in diar_bench.GROUPS.values():
        assert set(group) <= set(engines), group


def test_old_engine_groups_did_not_change():
    """«both» — вариант по умолчанию, «all» — из документации: их состав — контракт."""
    assert diar_bench.GROUPS["both"] == ("live", "sherpa")
    assert diar_bench.GROUPS["all"] == ("live", "live-split", "live-legacy", "sherpa")


def test_only_live_tracker_engines_claim_one_voice_per_moment():
    import argparse
    engines = diar_bench._engines(argparse.Namespace())
    single = {name for name, (_run, one) in engines.items() if one}
    assert single == {"live", "live-split", "live-legacy"}


def _bench(*args, **kw):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "diar_bench.py"), *args],
                          capture_output=True, text=True, timeout=60, **kw)


def test_nemotron_without_model_explains_before_running_anything():
    """Нет весов (и, в CI, пакета) — рецепт сразу, а не после прогона остальных движков."""
    out = _bench("--engine", "compare")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "hf download mlx-community/Nemotron-3-Diarization" in out.stderr
    if importlib.util.find_spec("mlx_audio") is None:
        assert "pip install mlx-audio" in out.stderr
    assert "DER" not in out.stdout, "движки успели поработать до отказа"


def test_truth_without_recording_is_a_usage_error(tmp_path):
    out = _bench("--truth", str(tmp_path / "labels.txt"))
    assert out.returncode == 2 and "--wav" in out.stderr


def test_recording_in_wrong_format_gets_a_conversion_recipe(tmp_path):
    wav = tmp_path / "meeting.wav"
    diar_bench.sf.write(wav, np.zeros((4800, 2), dtype=np.float32), 48000)
    out = _bench("--wav", str(wav), "--engine", "live")
    assert out.returncode == 1
    assert "afconvert" in out.stderr and "meeting_16k.wav" in out.stderr
