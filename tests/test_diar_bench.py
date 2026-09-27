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
import re
import subprocess
import sys
import types

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


def test_labels_are_matched_by_shared_time_not_by_order():
    """x звучит секунду с A и три — с B: x — это B, а секунда A — путаница."""
    truth = [{"start": 0.0, "end": 1.0, "speaker": "A"},
             {"start": 1.0, "end": 4.0, "speaker": "B"}]
    hyp = [{"start": 0.0, "end": 4.0, "speaker": "x"}]
    scores = diar_bench.der_overlap(truth, hyp, 4.0)
    assert scores["confusion"] == pytest.approx(0.25, abs=0.01)


@pytest.mark.parametrize("total", [0.29, 1.0, 5.0, 7.37])
def test_both_grids_cut_time_the_same_way(total):
    """Сетка множеств и прежняя сетка обязаны резать время одинаково:
    der_overlap(hyp_single=True) сшивает их кадр в кадр."""
    segs = [{"start": 0.0, "end": 0.29, "speaker": "A"},
            {"start": 0.57, "end": total, "speaker": "B"}]
    old = [frozenset([c]) if c else frozenset() for c in diar_bench._grid(segs, total)]
    assert diar_bench._grid_sets(segs, total) == old


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
    # и держится именно за предыдущую реплику, а не за первую в диалоге
    assert diar_bench.layout_turns([100, 50, 100], 10, {2: 500}) == [0, 110, 110]


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
    # ровно полная шкала — ещё не перегруз: такой сигнал остаётся бит в бит
    full = [np.array([1.0, -1.0, 0.5], dtype=np.float32)]
    assert np.array_equal(diar_bench.mix_turns(full, [0], tail=0), full[0])


def test_crosstalk_script_overlaps_different_voices():
    """Перебивание собой — не перебивание: соседние реплики — разные голоса."""
    for i in diar_bench.CROSSTALK:
        assert 0 < i < len(diar_bench.DIALOG)
        assert diar_bench.DIALOG[i][0] != diar_bench.DIALOG[i - 1][0], i


def test_has_overlap_sees_crosstalk_only_between_different_voices():
    assert diar_bench.has_overlap(CROSS) is True
    assert diar_bench.has_overlap(TRUTH) is False
    assert diar_bench.has_overlap([{"start": 0, "end": 2, "speaker": "A"},
                                   {"start": 1, "end": 3, "speaker": "A"}]) is False
    # стык в один кадр — округление разметки, а не перебивание
    assert diar_bench.has_overlap([{"start": 0.0, "end": 1.0, "speaker": "A"},
                                   {"start": 0.99, "end": 2.0, "speaker": "B"}]) is False


# --- своя запись: эталон и метки Audacity ------------------------------------

def test_audacity_labels_are_read_as_truth(tmp_path):
    f = tmp_path / "labels.txt"
    f.write_text("7.0 8.0 Анна Мария\n"                 # пробелы вместо табуляций
                 "0.000000\t2.500000\tМилена Петровна\n"
                 "\\\t0.000000\t0.000000\n"            # частотное выделение Audacity
                 "2,5\t4,0\tФёдор\n"                   # десятичная запятая
                 "5.0\t5.0\tточка\n"                   # метка-точка: не речь
                 "\n", encoding="utf-8")
    assert diar_bench.read_truth(f) == [
        {"start": 7.0, "end": 8.0, "speaker": "Анна Мария"},
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


@pytest.mark.parametrize("shape, sr", [((4800, 2), 16000), (4800, 48000)],
                         ids=["stereo", "48kHz"])
def test_recording_in_wrong_format_gets_a_conversion_recipe(tmp_path, shape, sr):
    wav = tmp_path / "meeting.wav"
    diar_bench.sf.write(wav, np.zeros(shape, dtype=np.float32), sr)
    out = _bench("--wav", str(wav), "--engine", "live")
    assert out.returncode == 1
    assert "afconvert" in out.stderr and "meeting_16k.wav" in out.stderr


# --- сборка фикстуры (синтезатор macOS подменён тоном известной длины) --------

@pytest.fixture
def fake_say(monkeypatch):
    """`say` есть только на macOS; длина реплики i — 0.5 + 0.1·i секунды."""
    def say(voice, text, dest):
        i = [t for _v, t in diar_bench.DIALOG].index(text)
        n = int((0.5 + 0.1 * i) * diar_bench.SR)
        diar_bench.sf.write(dest, np.full(n, 0.1, dtype=np.float32), diar_bench.SR)
    monkeypatch.setattr(diar_bench, "_say", say)


def _fixture_truth(folder):
    return json.loads((folder / "truth.json").read_text(encoding="utf-8"))["segments"]


def test_plain_fixture_goes_where_asked_and_never_overlaps(tmp_path, fake_say, capsys):
    wav = diar_bench.make_fixture(tmp_path / "здесь")
    assert wav == tmp_path / "здесь" / "dialog.wav"
    assert "· 10.0с ·" in capsys.readouterr().out     # 6.8 с речи + 8 пауз по 0.4
    truth = _fixture_truth(tmp_path / "здесь")
    assert truth[1] == {"start": 0.9, "end": 1.5, "speaker": diar_bench.DIALOG[1][0]}
    assert diar_bench.has_overlap(truth) is False
    assert [s["speaker"] for s in truth] == [v for v, _t in diar_bench.DIALOG]
    total = sum(0.5 + 0.1 * i for i in range(len(diar_bench.DIALOG))) \
        + diar_bench.PAUSE * len(diar_bench.DIALOG)
    assert diar_bench.sf.info(str(wav)).duration == pytest.approx(total, abs=0.01)


def test_default_fixture_is_the_plain_one(fake_say):
    """Без флага — прежняя фикстура на прежнем месте: старые замеры сравнимы."""
    wav = diar_bench.make_fixture()
    assert wav.parent == diar_bench._fixture() and wav.parent.name == "diar_bench"
    assert diar_bench.has_overlap(_fixture_truth(wav.parent)) is False


def test_crosstalk_fixture_has_one_overlap_per_scripted_interruption(fake_say):
    wav = diar_bench.make_fixture(crosstalk=True)
    assert wav.parent.name == "diar_bench_crosstalk"
    truth = _fixture_truth(wav.parent)
    overlapping = [i for i in range(1, len(truth))
                   if truth[i]["start"] < truth[i - 1]["end"]]
    assert overlapping == sorted(diar_bench.CROSSTALK)


# --- движки Nemotron в бенче (модель подменена) ------------------------------

class _FakeNemotron:
    """Поток: каждый блок звука — сегмент голоса 0 ровно на его время."""

    def __init__(self):
        self.blocks = []

    def init_streaming_state(self):
        return 0

    def feed(self, pcm, state, sample_rate, *, final=False, threshold=0.5):
        self.blocks.append(len(pcm))
        segs = []
        if len(pcm):
            segs = [types.SimpleNamespace(start=state / sample_rate,
                                          end=(state + len(pcm)) / sample_rate, speaker=0)]
        return types.SimpleNamespace(segments=segs), state + len(pcm)

    def generate(self, audio, sample_rate):
        half = len(audio) / sample_rate / 2
        return types.SimpleNamespace(segments=[
            types.SimpleNamespace(start=half, end=2 * half, speaker=1),
            types.SimpleNamespace(start=0.0, end=half, speaker=1)])


@pytest.fixture
def fake_nemotron(monkeypatch):
    model, loads = _FakeNemotron(), []

    def load_model(path, preset="offline"):
        loads.append((path, preset))
        return model
    monkeypatch.setattr(diar_bench.nemotron, "load_model", load_model)
    return model, loads


def _wav(path, seconds, sr=16000):
    diar_bench.sf.write(path, np.zeros(int(seconds * sr), dtype=np.float32), sr)
    return path


def test_live_engine_streams_half_second_blocks_and_glues(tmp_path, fake_nemotron):
    model, loads = fake_nemotron
    hyp = diar_bench.run_nemotron_live(_wav(tmp_path / "a.wav", 1.2))
    assert hyp == [{"start": 0.0, "end": 1.2, "speaker": "nem0"}]
    assert model.blocks == [8000, 8000, 3200, 0], "поток кормится не блоками по 0.5 с"
    (path, preset), = loads
    assert preset == "low"
    assert path == diar_bench.nemotron.model_dir(diar_bench._root())


def test_explicit_model_dir_wins_over_the_default(tmp_path, fake_nemotron):
    _model, loads = fake_nemotron
    diar_bench.run_nemotron(_wav(tmp_path / "a.wav", 1.0), tmp_path / "веса")
    assert loads == [(tmp_path / "веса", "offline")]


def test_whole_file_engine_returns_glued_segments(tmp_path, fake_nemotron):
    assert diar_bench.run_nemotron(_wav(tmp_path / "a.wav", 2.0)) == \
        [{"start": 0.0, "end": 2.0, "speaker": "nem1"}]


def test_live_engine_refuses_wrong_sample_rate(tmp_path, fake_nemotron):
    with pytest.raises(SystemExit, match="16000"):
        diar_bench.run_nemotron_live(_wav(tmp_path / "a.wav", 0.5, sr=48000))


def test_missing_model_becomes_a_message_not_a_traceback(monkeypatch):
    def refuse(path, preset="offline"):
        raise diar_bench.nemotron.ModelUnavailable("нет каталога модели")
    monkeypatch.setattr(diar_bench.nemotron, "load_model", refuse)
    with pytest.raises(SystemExit, match="^nemotron: нет каталога модели$"):
        diar_bench._nemotron(None, "low")


# --- печать и сквозной прогон main() ------------------------------------------

def test_report_prints_timing_and_crosstalk(capsys):
    scores = diar_bench.der_overlap(CROSS, [CROSS[0]], CROSS_TOTAL)
    diar_bench.report("x", scores, [CROSS[0]], elapsed=2.0, total=4.0)
    out = capsys.readouterr().out
    assert "RTF 0.50" in out and "смен говорящего 0" in out
    assert "в разметке 2.0 с, у движка 0.0 с" in out
    diar_bench.report("x", diar_bench.der(TRUTH, TRUTH, TOTAL))
    assert "RTF" not in capsys.readouterr().out


def _main(monkeypatch, capsys, *argv):
    """main() в процессе, движок live подменён: ответ — ровно эталон CROSS."""
    runs = []

    def engines(args):
        return {"live": (lambda wav: runs.append(wav) or CROSS, False)}
    monkeypatch.setattr(diar_bench, "_engines", engines)
    monkeypatch.setattr(sys, "argv", ["diar_bench.py", *argv])
    code = diar_bench.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err, runs


FOOTER = "DER — доля времени речи"


def test_own_recording_without_truth_prints_a_summary(tmp_path, monkeypatch, capsys):
    wav = _wav(tmp_path / "m.wav", CROSS_TOTAL)
    code, out, _err, runs = _main(monkeypatch, capsys, "--wav", str(wav), "--engine", "live")
    assert code == 0 and runs == [wav]
    assert "разметки нет" in out and "голосов 2" in out and "перекрытие 2.0 с" in out
    assert not re.search(r"DER \d", out), "без эталона DER посчитан из воздуха"
    assert FOOTER not in out
    assert re.search(r"время 0\.\d с", out), "время прогона посчитано неверно"


def test_own_recording_with_truth_scores_and_writes_labels(tmp_path, monkeypatch, capsys):
    wav = _wav(tmp_path / "m.wav", CROSS_TOTAL)
    truth = tmp_path / "ref.txt"
    diar_bench.write_labels(CROSS, truth)
    labels = tmp_path / "метки" / "прогон"          # вложенный и ещё не созданный
    for _ in range(2):                               # второй раз — каталог уже есть
        code, out, _err, _ = _main(monkeypatch, capsys, "--wav", str(wav),
                                   "--truth", str(truth), "--engine", "live",
                                   "--labels", str(labels))
        assert code == 0
    assert "DER 0.000" in out and "одновременной речи 2.0с" in out and FOOTER in out
    assert diar_bench.read_truth(labels / "live.txt") == CROSS


def test_missing_recording_is_refused(tmp_path, monkeypatch, capsys):
    code, _out, err, runs = _main(monkeypatch, capsys, "--wav", str(tmp_path / "нет.wav"),
                                  "--engine", "live")
    assert code == 1 and "нет файла" in err and runs == []


def test_fixture_run_reads_the_fixture_it_was_given(tmp_path, monkeypatch, capsys, fake_say):
    diar_bench.make_fixture(tmp_path / "фикс")
    code, out, _err, runs = _main(monkeypatch, capsys, "--fixture", str(tmp_path / "фикс"),
                                  "--engine", "live")
    assert code == 0 and runs == [tmp_path / "фикс" / "dialog.wav"]
    assert "голосов в разметке: 4" in out and FOOTER in out


def test_missing_crosstalk_fixture_names_the_right_recipe(monkeypatch, capsys):
    code, _out, err, runs = _main(monkeypatch, capsys, "--crosstalk", "--engine", "live")
    assert code == 1 and runs == []
    assert "diar_bench_crosstalk" in err and "--make --crosstalk" in err


def test_make_writes_where_told_and_crosstalk_where_expected(tmp_path, monkeypatch,
                                                              capsys, fake_say):
    code, *_ = _main(monkeypatch, capsys, "--make", "--fixture", str(tmp_path / "сюда"))
    assert code == 0 and (tmp_path / "сюда" / "truth.json").exists()
    code, *_ = _main(monkeypatch, capsys, "--make", "--crosstalk")
    truth = _fixture_truth(diar_bench._fixture(crosstalk=True))
    assert code == 0 and diar_bench.has_overlap(truth) is True


def test_engine_names_call_the_right_runner_with_the_right_switches(monkeypatch):
    calls = []
    for fn in ("run_live", "run_sherpa", "run_nemotron", "run_nemotron_live"):
        monkeypatch.setattr(diar_bench, fn,
                            lambda *a, _fn=fn, **kw: calls.append((_fn, a[1:], kw)) or [])
    import argparse
    args = argparse.Namespace(overlap=True, speakers=3, nemotron_model="M",
                              nemotron_preset="ultra_low")
    for name, (run, _single) in diar_bench._engines(args).items():
        run("x.wav")
    assert calls == [
        ("run_live", (), {"overlap": True}),
        ("run_live", (), {"split": True, "overlap": True}),
        ("run_live", (), {"legacy": True, "overlap": True}),
        ("run_sherpa", (3,), {}),
        ("run_nemotron", ("M",), {}),
        ("run_nemotron_live", ("M", "ultra_low"), {}),
    ]
