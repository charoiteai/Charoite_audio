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

def test_weights_live_next_to_the_other_diarization_models(tmp_path):
    """Путь из документации и рецепта загрузки: models/diar/nemotron корня данных."""
    assert nem.model_dir(tmp_path) == tmp_path / "models" / "diar" / "nemotron"


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
    with pytest.raises(nem.ModelUnavailable) as err:
        nem.load_model(_model_dir(tmp_path))
    # рецепт с версией, по коду которой проверен стык (круг 1 по #648, DS M2)
    assert nem.INSTALL_RECIPE in str(err.value) and "mlx-audio==" in nem.INSTALL_RECIPE


def test_availability_lists_every_problem_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: None)
    problem = nem.availability(tmp_path / "nope")
    lines = problem.splitlines()
    assert len(lines) == 2, problem
    assert "mlx-audio" in lines[0] and "hf download" in lines[1]


def test_availability_is_quiet_when_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda name: nem.MLX_AUDIO_VERSION)
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


def test_glue_extends_the_latest_turn_not_the_first():
    """Три куска одного голоса: пауза, затем разрез чанком — склеивается хвост."""
    pieces = [{"start": 0.0, "end": 1.0, "speaker": "nem0"},
              {"start": 2.0, "end": 3.0, "speaker": "nem0"},
              {"start": 3.0, "end": 4.0, "speaker": "nem0"}]
    assert nem.merge_same_speaker(pieces) == [
        {"start": 0.0, "end": 1.0, "speaker": "nem0"},
        {"start": 2.0, "end": 4.0, "speaker": "nem0"}]
    # кусок внутри уже склеенного не укорачивает его
    inside = pieces[:2] + [{"start": 2.2, "end": 2.5, "speaker": "nem0"}]
    assert nem.merge_same_speaker(inside)[-1] == {"start": 2.0, "end": 3.0, "speaker": "nem0"}


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
    assert {c["threshold"] for c in model.calls} == {0.5}, "порог по умолчанию — 0.5, как у NVIDIA"


def test_stream_refuses_multichannel_input():
    with pytest.raises(ValueError, match="моно"):
        nem.NemotronStream(_FakeStreamModel()).feed(np.zeros((10, 2), dtype=np.float32))


def test_diarize_file_passes_mono_through_untouched():
    seen = {}

    class Model:
        def generate(self, audio, sample_rate):
            seen["audio"] = audio
            return types.SimpleNamespace(segments=[])

    mono = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    assert nem.diarize_file(Model(), mono, 16000) == []
    assert np.array_equal(seen["audio"], mono)


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


@pytest.mark.parametrize("fail_at", ["load", "set_streaming_config"])
def test_a_model_that_does_not_rise_gives_the_recipe_not_a_trace(tmp_path, monkeypatch, fail_at):
    """Каталог прошёл проверку формы, а тензоры не той архитектуры или закачка
    оборвана выше порога — `ModelUnavailable` с рецептом, а не трассировка mlx
    (выходной круг 1 по #648, DS I4)."""
    import types

    class Model:
        def set_streaming_config(self, preset):
            if fail_at == "set_streaming_config":
                raise KeyError("encoder.layers.0")

    def load(path, strict):
        if fail_at == "load":
            raise ValueError("shape mismatch")
        return Model()

    vad = types.ModuleType("mlx_audio.vad")
    vad.load = load
    monkeypatch.setitem(sys.modules, "mlx_audio", types.ModuleType("mlx_audio"))
    monkeypatch.setitem(sys.modules, "mlx_audio.vad", vad)
    with pytest.raises(nem.ModelUnavailable, match="не поднялась") as err:
        nem.load_model(_model_dir(tmp_path))
    assert "hf download" in str(err.value)


def test_unreadable_config_is_a_recipe(tmp_path, monkeypatch):
    """`config.json`, который не читается (права, битый том), — строка с рецептом,
    а не `OSError` наружу (DS I4)."""
    d = _model_dir(tmp_path)
    real = pathlib.Path.read_text

    def refuse(self, *a, **kw):
        if self.name == "config.json":
            raise PermissionError("нет прав")
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    problem = nem.check_model_dir(d)
    assert problem and "PermissionError" in problem and "hf download" in problem


def test_the_product_does_not_import_the_experiment():
    """Эксперимент зовут только бенч и тесты: ни один модуль продукта не
    импортирует `diarize_nemotron` — по графу импортов раскладки, а не по
    докстрингу (выходной круг 1 по #648, DS I2)."""
    sys.path.insert(0, str(REPO / "scripts"))
    import layout_map as lm

    graph = lm.import_graph(lm.inventory(REPO))
    assert "diarize_nemotron" in graph, "модуль пропал из графа — сторож сторожил бы пустоту"
    importers = sorted(m for m, deps in graph.items() if "diarize_nemotron" in deps)
    assert importers == [], f"продукт зовёт эксперимент: {importers}"
    # граф раскладки видит только src/: точки входа из scripts/ — отдельным обходом, и
    # единственный законный импортёр назван явно (круг 2 по #648, DS I1)
    import ast
    callers = set()
    for f in sorted((REPO / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(n.split(".")[0] == "diarize_nemotron" for n in names):
                callers.add(f"scripts/{f.name}")
    assert callers == {"scripts/diar_bench.py"}, f"эксперимент зовут не только из бенча: {callers}"


def test_the_wrapper_writes_nothing_to_disk(tmp_path, monkeypatch):
    """Прогон обёртки потоком с подменённой моделью: на диске ничего не появляется —
    ни в рабочем каталоге, ни в репозитории. Что пишет сама mlx-audio, этот тест не
    видит: это прогон с весами на Mac (№443, DS I3)."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    before = {p for p in REPO.rglob("*") if ".git" not in p.parts}
    stream = nem.NemotronStream(_FakeStreamModel())
    rng = np.random.default_rng(0)
    for _ in range(4):
        stream.feed(rng.normal(0, 0.05, 8000).astype(np.float32))
    stream.close()
    assert list(work.iterdir()) == [], f"обёртка создала файлы: {list(work.iterdir())}"
    new = {p for p in REPO.rglob("*") if ".git" not in p.parts} - before
    assert not {p for p in new if "__pycache__" not in p.parts}, sorted(new)


@pytest.mark.parametrize("error, says, weights", [
    (TypeError("load() got an unexpected keyword 'strict'"), "mlx-audio ответил не так", True),
    (MemoryError("нет памяти"), "не хватило памяти", False)])
def test_library_drift_and_memory_get_their_own_recipe(tmp_path, monkeypatch, error, says, weights):
    """Нехватка памяти — не битые веса: свой рецепт без «скачать заново» (выходной
    круг 2 по #648, DS I2). TypeError/AttributeError класс не различает: дрейф API
    пакета или веса не той формы — названы оба рецепта (круг 3, критика DS 1)."""
    import types

    def load(path, strict):
        raise error

    vad = types.ModuleType("mlx_audio.vad")
    vad.load = load
    monkeypatch.setitem(sys.modules, "mlx_audio", types.ModuleType("mlx_audio"))
    monkeypatch.setitem(sys.modules, "mlx_audio.vad", vad)
    with pytest.raises(nem.ModelUnavailable, match=says) as err:
        nem.load_model(_model_dir(tmp_path))
    assert ("hf download" in str(err.value)) is weights
    assert (nem.INSTALL_RECIPE in str(err.value)) is weights


def test_availability_names_a_version_other_than_the_checked_one(tmp_path, monkeypatch):
    """Стык проверен на одной ветке mlx-audio: другая ветка — строка с рецептом до
    прогона (круг 2 по #648, DS I2; ветка вместо строгого равенства — круг 3)."""
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda name: "0.4.9")
    d = _model_dir(tmp_path)
    problem = nem.availability(d, warn=lambda line: pytest.fail(f"другая ветка — не предупреждение: {line}"))
    assert problem and "mlx-audio 0.4.9" in problem and nem.MLX_AUDIO_VERSION in problem
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda name: nem.MLX_AUDIO_VERSION)
    assert nem.availability(d) is None


@pytest.mark.parametrize("installed, refused, warned", [
    ("0.5.6", False, False),
    ("0.5.7", False, True),
    ("0.5.7.dev0+g1a2b3c", False, True),
    ("0.5.6+local", False, True),
    ("0.6.0", True, False),
    ("0.4.9", True, False),
    ("мусор", True, False),
    (None, True, False),
])
def test_version_policy_refuses_another_branch_and_warns_on_a_patch(installed, refused, warned):
    """Строгое равенство отказывало любой сборке из git без выхода. Отказ — другая
    ветка major.minor или нет метаданных; та же ветка — предупреждение и прогон
    (выходной круг 3 по #648, DS I2)."""
    refusal, warning = nem.version_verdict(installed)
    assert bool(refusal) is refused and bool(warning) is warned
    for text in filter(None, (refusal, warning)):
        assert nem.MLX_AUDIO_VERSION in text and nem.INSTALL_RECIPE in text
    if installed is None:
        assert "метаданных" in refusal


def test_availability_warns_on_a_patch_and_still_passes(tmp_path, monkeypatch):
    """Та же ветка, другой патч: не проблема, а строка в сток предупреждений."""
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda name: "0.5.7")
    warned = []
    assert nem.availability(_model_dir(tmp_path), warn=warned.append) is None
    assert len(warned) == 1 and "0.5.7" in warned[0]


def test_the_docs_install_the_pinned_version():
    """Пин живёт в двух местах — константа и доки: доки обязаны ставить ровно
    проверенную версию, иначе человек поставит ту, которой availability откажет
    (выходной круг 3 по #648, DS M4)."""
    import re as _re
    docs = sorted((REPO / "docs").rglob("*.md"))
    pins = {str(d.relative_to(REPO)): _re.findall(r"mlx-audio==([0-9][^\"'\s]*)", d.read_text(encoding="utf-8"))
            for d in docs}
    pins = {k: v for k, v in pins.items() if v}
    assert pins, "доки перестали называть версию mlx-audio — сторож смотрит мимо"
    for doc, versions in pins.items():
        assert set(versions) == {nem.MLX_AUDIO_VERSION}, (doc, versions)
