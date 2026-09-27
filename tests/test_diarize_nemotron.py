"""Nemotron 3 Diarization: обвязка, которую можно проверить без модели.

Сама модель (mlx, Apple Silicon, веса по OpenMDW) в CI не живёт. Проверяется
то, что делает обвязка и что ломается молча:

    * сеть: строка «org/name» — отказ с рецептом, а не скачивание; в mlx-audio
      уходит pathlib.Path — для Path библиотека не ходит на хаб;
    * каталог весов: указатель git-lfs и обрыв закачки не выдают себя за модель;
    * поток: состояние (кэш голосов) переходит из вызова в вызов, хвост
      сбрасывается на close, у каждого потока своё состояние;
    * выход: куски одного голоса, разрезанные чанком, склеиваются, а
      перекрытия разных голосов — то, ради чего модель берут, — сохраняются.
"""
import json
import pathlib
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

np = pytest.importorskip("numpy")

import diarize_nemotron as nem  # noqa: E402


def _model_dir(tmp_path: pathlib.Path, *, model_type="nemotron_diarization",
               weights_bytes: int | None = None) -> pathlib.Path:
    d = tmp_path / "nemotron"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"model_type": model_type}), encoding="utf-8")
    w = d / "model.safetensors"
    with w.open("wb") as f:     # разреженный файл: размер есть, места на диске нет
        f.truncate(nem.MIN_WEIGHTS_BYTES if weights_bytes is None else weights_bytes)
    return d


@pytest.fixture
def fake_mlx(monkeypatch):
    """Подменённый mlx_audio.vad: записывает, чем и как его позвали."""
    calls = []

    class Model:
        def __init__(self):
            self.presets = []

        def set_streaming_config(self, preset):
            self.presets.append(preset)

    def load(path, **kw):
        calls.append((path, kw))
        return Model()

    vad = types.ModuleType("mlx_audio.vad")
    vad.load = load
    pkg = types.ModuleType("mlx_audio")
    pkg.vad = vad
    monkeypatch.setitem(sys.modules, "mlx_audio", pkg)
    monkeypatch.setitem(sys.modules, "mlx_audio.vad", vad)
    return calls


# --- каталог весов ---------------------------------------------------------

def test_good_model_dir_passes(tmp_path):
    assert nem.check_model_dir(_model_dir(tmp_path)) is None


def test_missing_dir_names_the_download_recipe(tmp_path):
    problem = nem.check_model_dir(tmp_path / "nope")
    assert "нет каталога" in problem
    assert f"hf download {nem.HF_REPO} --local-dir {tmp_path / 'nope'}" in problem


def test_repo_id_is_refused_not_downloaded():
    """«org/name» не существует как каталог — значит, отказ, а не хаб."""
    problem = nem.check_model_dir(pathlib.Path(nem.HF_REPO))
    assert problem and "нет каталога" in problem


def test_dir_without_config_is_refused(tmp_path):
    d = _model_dir(tmp_path)
    (d / "config.json").unlink()
    assert "config.json" in nem.check_model_dir(d)


@pytest.mark.parametrize("text", ["{не json", "[1, 2]"])
def test_unreadable_config_is_refused(tmp_path, text):
    d = _model_dir(tmp_path)
    (d / "config.json").write_text(text, encoding="utf-8")
    assert "JSON" in nem.check_model_dir(d)


def test_foreign_checkpoint_is_refused(tmp_path):
    """Прежний Sortformer на 4 голоса — тоже «диаризация», но не та модель."""
    problem = nem.check_model_dir(_model_dir(tmp_path, model_type="sortformer"))
    assert "не Nemotron" in problem and "sortformer" in problem


def test_dir_without_weights_is_refused(tmp_path):
    d = _model_dir(tmp_path)
    (d / "model.safetensors").unlink()
    assert "нет весов" in nem.check_model_dir(d)


def test_git_lfs_pointer_is_not_a_model(tmp_path):
    """Клон без LFS: файл весов на месте, но это 130 байт текста."""
    d = _model_dir(tmp_path, weights_bytes=130)
    assert "малы" in nem.check_model_dir(d)
    # на байт меньше границы — ещё отказ; ровно граница годна (см. фикстуру
    # test_good_model_dir_passes): граница включительная
    (d / "model.safetensors").unlink()
    with (d / "model.safetensors").open("wb") as f:
        f.truncate(nem.MIN_WEIGHTS_BYTES - 1)
    assert nem.check_model_dir(d) is not None


# --- загрузка --------------------------------------------------------------

def test_load_hands_mlx_a_path_not_a_string(tmp_path, fake_mlx):
    """Path, а не str: строку mlx-audio сочтёт репозиторием и пойдёт на хаб."""
    d = _model_dir(tmp_path)
    model = nem.load_model(str(d), preset="low")
    (path, kw), = fake_mlx
    assert isinstance(path, pathlib.Path) and path == d
    assert kw.get("strict") is True, "неполный чекпойнт должен падать, а не молчать"
    assert model.presets == ["low"]


def test_load_refuses_repo_id_before_touching_mlx(fake_mlx):
    with pytest.raises(nem.ModelUnavailable, match="hf download"):
        nem.load_model(nem.HF_REPO)
    assert fake_mlx == [], "mlx-audio позвали с идентификатором репозитория"


def test_unknown_preset_is_refused_before_loading(tmp_path, fake_mlx):
    with pytest.raises(ValueError, match="пресет"):
        nem.load_model(_model_dir(tmp_path), preset="instant")
    assert fake_mlx == []


def test_missing_package_gives_install_recipe(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx_audio", None)     # import → ImportError
    monkeypatch.setitem(sys.modules, "mlx_audio.vad", None)
    with pytest.raises(nem.ModelUnavailable, match="pip install mlx-audio"):
        nem.load_model(_model_dir(tmp_path))


def test_availability_lists_every_problem_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: None)
    problem = nem.availability(tmp_path / "nope")
    lines = problem.splitlines()
    assert len(lines) == 2, problem
    assert "mlx-audio" in lines[0] and "hf download" in lines[1]


def test_availability_is_quiet_when_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: object())
    assert nem.availability(_model_dir(tmp_path)) is None


# --- сегменты --------------------------------------------------------------

def _seg(start, end, speaker):
    return types.SimpleNamespace(start=start, end=end, speaker=speaker)


def test_to_segments_labels_sorts_and_drops_empty():
    raw = [_seg(2.0, 3.0, 1), _seg(0.0, 1.23456, 0), _seg(5.0, 5.0, 2), _seg(6.0, 5.5, 3)]
    assert nem.to_segments(raw) == [
        {"start": 0.0, "end": 1.235, "speaker": "nem0"},
        {"start": 2.0, "end": 3.0, "speaker": "nem1"},
    ]


def test_chunk_split_pieces_of_one_voice_are_glued():
    pieces = [{"start": 0.0, "end": 1.04, "speaker": "nem0"},
              {"start": 1.04, "end": 2.08, "speaker": "nem0"},
              {"start": 2.08, "end": 2.5, "speaker": "nem0"}]
    assert nem.merge_same_speaker(pieces) == [{"start": 0.0, "end": 2.5, "speaker": "nem0"}]


def test_pause_longer_than_gap_keeps_turns_apart():
    turns = [{"start": 0.0, "end": 1.0, "speaker": "nem0"},
             {"start": 1.3, "end": 2.0, "speaker": "nem0"}]
    assert len(nem.merge_same_speaker(turns)) == 2
    assert len(nem.merge_same_speaker(turns, gap=0.29)) == 2
    assert len(nem.merge_same_speaker(turns, gap=0.3)) == 1


def test_overlap_of_different_voices_survives_merge():
    """Перекрытие двух голосов — ради него модель и берут; склейка его не трогает."""
    talk = [{"start": 0.0, "end": 3.0, "speaker": "nem0"},
            {"start": 2.0, "end": 4.0, "speaker": "nem1"},
            {"start": 3.0, "end": 5.0, "speaker": "nem0"}]
    merged = nem.merge_same_speaker(talk)
    assert merged == [{"start": 0.0, "end": 5.0, "speaker": "nem0"},
                      {"start": 2.0, "end": 4.0, "speaker": "nem1"}]
    assert talk[0]["end"] == 3.0, "склейка испортила вход"


# --- поток и файл ----------------------------------------------------------

class _FakeStreamModel:
    """Модель-заглушка: состояние — счётчик, сегменты — по одному на вызов."""

    def __init__(self):
        self.inits = 0
        self.calls = []

    def init_streaming_state(self):
        self.inits += 1
        return ("state", self.inits, 0)

    def feed(self, pcm, state, sample_rate, *, final=False, threshold=0.5):
        self.calls.append({"n": len(pcm), "state": state, "sr": sample_rate,
                           "final": final, "threshold": threshold})
        k = state[2]
        out = types.SimpleNamespace(segments=[_seg(k, k + 1.0, 0)])
        return out, (state[0], state[1], k + 1)


def test_stream_threads_state_and_flushes_on_close():
    model = _FakeStreamModel()
    stream = nem.NemotronStream(model, threshold=0.4)
    first = stream.feed(np.zeros(8000, dtype=np.float32))
    second = stream.feed(np.zeros(8000, dtype=np.float32))
    tail = stream.close()
    assert [c["state"][2] for c in model.calls] == [0, 1, 2], "состояние потерялось между вызовами"
    assert [c["final"] for c in model.calls] == [False, False, True]
    assert model.calls[-1]["n"] == 0
    assert {c["sr"] for c in model.calls} == {16000}
    assert {c["threshold"] for c in model.calls} == {0.4}
    assert (first[0]["start"], second[0]["start"], tail[0]["start"]) == (0.0, 1.0, 2.0)


def test_each_stream_starts_with_no_known_voices():
    """Память о голосах живёт в объекте: новая встреча — новое состояние."""
    model = _FakeStreamModel()
    nem.NemotronStream(model).feed(np.zeros(10, dtype=np.float32))
    nem.NemotronStream(model).feed(np.zeros(10, dtype=np.float32))
    assert model.inits == 2
    assert [c["state"][1] for c in model.calls] == [1, 2]


def test_stream_refuses_multichannel_input():
    with pytest.raises(ValueError, match="моно"):
        nem.NemotronStream(_FakeStreamModel()).feed(np.zeros((10, 2), dtype=np.float32))


def test_diarize_file_downmixes_and_glues():
    seen = {}

    class Model:
        def generate(self, audio, sample_rate):
            seen["shape"], seen["sr"], seen["first"] = audio.shape, sample_rate, audio[0]
            return types.SimpleNamespace(
                segments=[_seg(1.0, 2.0, 1), _seg(0.0, 1.0, 1), _seg(0.5, 1.5, 0)])

    stereo = np.array([[1.0, 0.0]] * 4, dtype=np.float32)
    out = nem.diarize_file(Model(), stereo, 48000)
    assert seen["shape"] == (4,) and seen["sr"] == 48000 and seen["first"] == 0.5
    assert out == [{"start": 0.0, "end": 2.0, "speaker": "nem1"},
                   {"start": 0.5, "end": 1.5, "speaker": "nem0"}]


# --- настоящий mlx-audio (только там, где он стоит: Mac с Apple Silicon) -------

def test_wrapper_speaks_the_real_mlx_audio_api(tmp_path, monkeypatch):
    """Крошечная модель со случайными весами — не качество, а стык с библиотекой.

    Ловит дрейф API mlx-audio при обновлении пакета: загрузку из каталога,
    пресеты, feed/final, форму сегментов. И держит обещание модуля: ответ
    потока не зависит от того, какими кусками пришёл звук.
    """
    pytest.importorskip("mlx_audio.vad.models.nemotron_diarization.nemotron_diarization")
    from dataclasses import asdict

    import mlx.core as mx
    from mlx.utils import tree_flatten
    from mlx_audio.vad.models.nemotron_diarization import config as cfg_mod
    from mlx_audio.vad.models.nemotron_diarization import nemotron_diarization as arch

    cfg = cfg_mod.ModelConfig(
        encoder_config=cfg_mod.EncoderConfig(d_model=64, n_layers=1, n_heads=4),
        modules_config=cfg_mod.DiarizationModulesConfig(fc_d_model=64, tf_d_model=32))
    d = tmp_path / "tiny"
    d.mkdir()
    mx.save_safetensors(str(d / "model.safetensors"),
                        dict(tree_flatten(arch.Model(cfg).parameters())))
    (d / "config.json").write_text(json.dumps(asdict(cfg)), encoding="utf-8")
    monkeypatch.setattr(nem, "MIN_WEIGHTS_BYTES", 0)

    audio = np.random.default_rng(0).normal(0, 0.1, nem.SAMPLE_RATE * 4).astype(np.float32)
    model = nem.load_model(d, "low")

    def stream(step):
        s = nem.NemotronStream(model)
        out = []
        for i in range(0, len(audio), step):
            out += s.feed(audio[i:i + step])
        return nem.merge_same_speaker(out + s.close())

    small, large = stream(8000), stream(20800)
    assert small == large, "ответ потока зависит от нарезки входа"
    whole = nem.diarize_file(nem.load_model(d, "offline"), audio, nem.SAMPLE_RATE)
    for seg in small + whole:
        assert 0.0 <= seg["start"] < seg["end"] <= 4.0 + 1e-6, seg
        assert seg["speaker"] in {f"nem{i}" for i in range(8)}, seg
