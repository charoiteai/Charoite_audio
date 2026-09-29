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
import shlex
import subprocess
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


def test_unreadable_weights_are_a_recipe_not_a_trace(tmp_path):
    """Файл весов в каталоге есть, но не читается (ссылка на отвалившийся том) — рецепт с
    причиной, как у config.json, а не трассировка и не «каталог годится» (DS M4)."""
    d = _model_dir(tmp_path)
    (d / "model.safetensors").unlink()
    (d / "model.safetensors").symlink_to(tmp_path / "том-отвалился.safetensors")
    problem = nem.check_model_dir(d)
    assert problem and "не читаются" in problem and "FileNotFoundError" in problem
    assert nem.fetch_recipe(d) in problem


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
# Здесь стоит importorskip: mlx-audio живёт в окружении движка (№474), в тестовом
# окружении и в CI его нет — постоянный прогон этих тестов в окружении движка — №521.

def _tiny_model(tmp_path: pathlib.Path) -> pathlib.Path:
    """Крошечная модель со случайными весами в каталоге — стык с библиотекой, не качество."""
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
    return d


def test_wrapper_speaks_the_real_mlx_audio_api(tmp_path, monkeypatch):
    """Крошечная модель со случайными весами — не качество, а стык с библиотекой.

    Ловит дрейф API mlx-audio при обновлении пакета: загрузку из каталога,
    пресеты, feed/final, форму сегментов. И держит обещание модуля: ответ
    потока не зависит от того, какими кусками пришёл звук.
    """
    d = _tiny_model(tmp_path)
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


@pytest.mark.parametrize("preset", ["low", "offline"])
def test_the_real_stream_engine_keeps_its_front_on_the_audio(tmp_path, preset):
    """Сторона движка живого потока целиком — модель, рукопожатие, цикл по трубе, фронт —
    на настоящей mlx-audio и отдельным процессом, как её зовёт тень: `_protocol_channel`
    переставляет дескриптор 1, в процессе pytest его звать нельзя, а порог весов снят в
    самом ребёнке — подмена в pytest туда не доходит (входной круг фикса A2 №478, I2).
    Прежняя формула кадра роняла здесь ребёнка до рукопожатия, а с верным именем поля —
    сверкой фронта со звуком. Граница теста: боевой путь выше `serve_stream` — `main`,
    чистое окружение двери и порог весов — здесь не пройден; его проходит проба на
    машине с движком после раскатки, постоянный прогон в окружении движка — №521
    (выходной круг фикса A2 №478, M3)."""
    d = _tiny_model(tmp_path)
    n = nem.SAMPLE_RATE * 3 + 37
    code = ("import pathlib, sys; sys.path.insert(0, %r); import diarize_nemotron as dn; "
            "dn.MIN_WEIGHTS_BYTES = 0; sys.exit(dn.serve_stream(pathlib.Path(%r), %r))"
            % (str(REPO / "src"), str(d), preset))
    pcm = (np.random.default_rng(1).normal(0, 0.1, n) * 32767).astype("<i2").tobytes()
    out = subprocess.run([sys.executable, "-c", code], input=pcm, capture_output=True, timeout=300)
    assert out.returncode == 0, out.stderr.decode(errors="replace")[-2000:]
    lines = [json.loads(x) for x in out.stdout.decode().splitlines()]
    fronts = [m for m in lines if m["type"] == "front"]
    assert lines[0]["type"] == "ready" and lines[0]["frame_s"] == pytest.approx(0.01)
    assert fronts[-1].get("final") is True and fronts[-1]["fed"] == n
    assert fronts[-1]["frames"] == n // 160, "финал разметил весь звук, кадр — родной hop"


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


def test_the_product_reaches_the_engine_only_from_the_rebuild():
    """Движок в продукте зовут пересборка (№473) и живой поток в тени (№478), и только
    процессом чужого интерпретатора: импортёров в `src/` ровно два — `rebuild_transcript`
    и `live_nemotron`, по графу импортов раскладки, а не по докстрингу (выходной круг 1
    по #648, DS I2). Что mlx при этом не попадает в процесс вызывающего, держит
    `test_the_caller_never_imports_mlx`."""
    sys.path.insert(0, str(REPO / "scripts"))
    import layout_map as lm

    graph = lm.import_graph(lm.inventory(REPO))
    assert "diarize_nemotron" in graph, "модуль пропал из графа — сторож сторожил бы пустоту"
    importers = sorted(m for m, deps in graph.items() if "diarize_nemotron" in deps)
    assert importers == ["live_nemotron", "rebuild_transcript"], f"движок зовут не только пересборка и тень: {importers}"
    # граф раскладки видит только src/: точки входа из scripts/ — отдельным обходом, и
    # законные импортёры названы явно (круг 2 по #648, DS I1): бенч гоняет движок в
    # своём процессе, установщик и доктор — только через двери движка (№474), живых
    # путей среди них нет
    import ast
    callers = set()
    for f in sorted((REPO / "scripts").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(n.split(".")[0] == "diarize_nemotron" for n in names):
                callers.add(f"scripts/{f.name}")
    assert callers == {"scripts/diar_bench.py", "scripts/install_engine.py", "scripts/doctor.py"}, (
        f"движок зовут не только бенч, установщик и доктор: {callers}")


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


def test_availability_warns_to_stderr_by_default(tmp_path, monkeypatch, capsys):
    """Без своего стока предупреждение — одна строка в stderr, stdout чист: бенч печатает
    в stdout таблицу, и строка о версии не должна в неё попасть."""
    monkeypatch.setattr(nem.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda name: "0.5.7")
    assert nem.availability(_model_dir(tmp_path)) is None
    out, err = capsys.readouterr()
    assert out == "" and err.endswith("\n") and err.count("\n") == 1 and "0.5.7" in err


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


# ------------------------------------------------ протокол пересборки (№473)

import importlib.abc  # noqa: E402
import importlib.util  # noqa: E402
import wave  # noqa: E402

import foreign_python as fp  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402


def _wav(path: pathlib.Path, samples, *, channels=1, width=2, rate=16000) -> pathlib.Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        dtype = {1: np.uint8, 2: np.int16}[width]
        w.writeframes(np.asarray(samples, dtype=dtype).tobytes())
    return path


def _engine_stub(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    d = tmp_path / "engine"
    d.mkdir(exist_ok=True)
    p = d / "engine.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_read_wav_gives_float32_mono_like_the_rebuild(tmp_path):
    audio, sr = nem.read_wav(_wav(tmp_path / "m.wav", [0, 16384, -32768], rate=8000))
    assert sr == 8000 and audio.dtype == np.float32
    assert audio.tolist() == [0.0, 0.5, -1.0]
    stereo, _ = nem.read_wav(_wav(tmp_path / "s.wav", [16384, 0, -16384, -16384], channels=2))
    assert stereo.tolist() == [0.25, -0.5]


def test_read_wav_refuses_anything_but_16_bit(tmp_path):
    with pytest.raises(ValueError, match=r"u8\.wav: не 16-битный PCM \(8 бит\)"):
        nem.read_wav(_wav(tmp_path / "u8.wav", [0, 255], width=1))
    p24 = tmp_path / "s24.wav"
    with wave.open(str(p24), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00\x00" * 4)
    with pytest.raises(ValueError, match=r"s24\.wav: не 16-битный PCM \(24 бит\)"):
        nem.read_wav(p24)


def test_main_without_the_engine_refuses_before_reading_the_recording(tmp_path, monkeypatch, capsys):
    """Нечем работать — код 10 и причины ОДНОЙ строкой (дверь берёт последнюю
    строку stderr); запись не читается вовсе — её может и не быть."""
    monkeypatch.setattr(nem, "availability", lambda path: "нет пакета mlx-audio — рецепт\nнет весов — рецепт")
    monkeypatch.setattr(nem, "read_wav", lambda p: pytest.fail("запись читается до проверки движка"))
    assert nem.main([str(tmp_path / "нет.wav"), "--model", str(tmp_path)]) == EXIT_ENGINE_UNAVAILABLE
    out, err = capsys.readouterr()
    assert out == "" and err == "нет пакета mlx-audio — рецепт; нет весов — рецепт\n"


def test_main_reports_a_model_that_does_not_load_as_unavailable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nem, "availability", lambda path: None)

    def refuse(path, preset="offline"):
        raise nem.ModelUnavailable("модели не хватило памяти\n— закрыть приложения")
    monkeypatch.setattr(nem, "load_model", refuse)
    wav = _wav(tmp_path / "bh.wav", [0] * 160)
    assert nem.main([str(wav), "--model", str(tmp_path)]) == EXIT_ENGINE_UNAVAILABLE
    assert capsys.readouterr().err == "модели не хватило памяти; — закрыть приложения\n"


def test_main_prints_the_segments_as_one_json_object(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(nem, "availability", lambda path: seen.setdefault("checked", path) and None)

    def load(path, preset="offline"):
        seen["preset"] = preset
        return "модель"

    def diarize(model, audio, sr):
        seen["args"] = (model, audio.tolist(), sr)
        return [{"start": 0.5, "end": 1.5, "speaker": "nem0"}]
    monkeypatch.setattr(nem, "load_model", load)
    monkeypatch.setattr(nem, "diarize_file", diarize)
    wav = _wav(tmp_path / "bh.wav", [16384, 0], rate=16000)
    assert nem.main([str(wav), "--model", str(tmp_path / "w")]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "segments": [{"start": 0.5, "end": 1.5, "speaker": "nem0"}]}
    assert seen == {"checked": tmp_path / "w", "preset": "offline", "args": ("модель", [0.5, 0.0], 16000)}


def test_main_answers_help_and_refuses_without_the_model(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        nem.main(["--help"])
    assert e.value.code == 0 and "--model" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        nem.main([str(tmp_path / "a.wav")])
    assert e.value.code == 2


def test_the_real_entry_point_without_the_engine_exits_10(tmp_path):
    """Сквозь настоящую точку входа, как её зовёт пересборка: интерпретатор без
    mlx-audio (или каталог без весов) — UNAVAILABLE с рецептом, не падение."""
    wav = _wav(tmp_path / "bh.wav", [0] * 1600)
    out = nem.diarize_in_env(sys.executable, wav, root=tmp_path / "нет-весов", timeout=60)
    assert out.kind == fp.UNAVAILABLE, out
    assert "нет" in out.reason and "\n" not in out.reason


@pytest.mark.parametrize("payload,why", [
    ({}, "нет списка segments"),
    ({"segments": {}}, "нет списка segments"),
    ({"segments": [["0", 1]]}, "отрезок не объект"),
    ({"segments": [{"start": True, "end": 1.0, "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": "0", "end": 1.0, "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": float("nan"), "end": 1.0, "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": 0.0, "end": float("inf"), "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": -0.1, "end": 1.0, "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": 1.0, "end": 1.0, "speaker": "nem0"}]}, "границы"),
    ({"segments": [{"start": 0.0, "end": 1.0, "speaker": "spk0"}]}, "метка"),
    ({"segments": [{"start": 0.0, "end": 1.0, "speaker": "nem"}]}, "метка"),
    ({"segments": [{"start": 0.0, "end": 1.0, "speaker": "nem1x"}]}, "метка"),
    ({"segments": [{"start": 0.0, "end": 1.0, "speaker": 3}]}, "метка"),
])
def test_parse_segments_refuses_anything_off_the_protocol(payload, why):
    with pytest.raises(ValueError, match=why):
        nem.parse_segments(payload)


def test_parse_segments_turns_labels_into_slot_numbers():
    assert nem.parse_segments({"segments": [
        {"start": 0, "end": 1.5, "speaker": "nem0"},
        {"start": 1.5, "end": 3.25, "speaker": "nem12"},
    ]}) == [(0.0, 1.5, 0), (1.5, 3.25, 12)]
    assert nem.parse_segments({"segments": []}) == []


def test_diarize_in_env_without_a_setting_and_an_installed_engine_is_unavailable(tmp_path):
    """Пустая настройка и не установленное окружение — отказ с каталогом и полной
    командой установщика (№474), а не «не задан интерпретатор»."""
    out = nem.diarize_in_env("", tmp_path / "bh.wav", root=tmp_path, timeout=5)
    assert out.kind == fp.UNAVAILABLE
    assert out.reason == (f"окружение движка не установлено ({tmp_path / 'engines' / 'nemotron'}) — "
                          f"{nem.INSTALLER_POINTER}")


def test_an_empty_setting_takes_the_installed_engine(tmp_path, monkeypatch):
    """Пустая настройка — установленное окружение `engine_python(root)`; настройка
    задана — она, даже если окружение тоже стоит."""
    installed = nem.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.symlink_to(sys.executable)
    s = _engine_stub(tmp_path, 'import json, sys\n'
                               'print(json.dumps({"segments": [{"start": 0.0, "end": 1.0, "speaker": "nem0"}]}))\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.engine_interpreter("", tmp_path) == (str(installed), "")
    assert nem.diarize_in_env("  ", tmp_path / "bh.wav", root=tmp_path, timeout=30).ok
    assert nem.engine_interpreter(" /явный/python ", tmp_path) == ("/явный/python", "")


def test_an_explicit_interpreter_that_is_missing_fails_with_its_path(tmp_path):
    """Настройка задана, файла нет — отказ самой двери внешнего процесса (#669): путь в
    причине, установленное окружение не подставляется молча вместо явной настройки."""
    nem.engine_python(tmp_path).parent.mkdir(parents=True)
    nem.engine_python(tmp_path).symlink_to(sys.executable)
    missing = tmp_path / "нет" / "python"
    out = nem.diarize_in_env(str(missing), tmp_path / "bh.wav", root=tmp_path, timeout=5)
    assert out == fp.Outcome(fp.FAILED, reason=f"нет интерпретатора {missing}")


def test_an_explicit_interpreter_under_tilde_runs_expanded_and_nothing_more(tmp_path, monkeypatch):
    """Ведущая `~` в настройке — домашний каталог (№503): раньше `subprocess` получал
    `~/…` буквально, и пересборка молча уходила на sherpa. Путь только раскрывается:
    симлинк `bin/python` окружения не разворачивается до базового интерпретатора (тот
    без пакетов движка), а запись без `~` не нормализуется — «./python» через `Path`
    стал бы «python», и его искали бы по PATH."""
    home = tmp_path / "дом"
    python = home / "eng" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.setenv("HOME", str(home))
    s = _engine_stub(tmp_path, 'import json, sys\n'
                               'print(json.dumps({"segments": [{"start": 0.0, "end": 1.0, "speaker": "nem0"}]}))\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.engine_interpreter(" ~/eng/bin/python ", tmp_path) == (str(python), "")
    assert nem.diarize_in_env("~/eng/bin/python", tmp_path / "bh.wav", root=tmp_path, timeout=30).ok
    assert nem.engine_interpreter("./python", tmp_path) == ("./python", "")


def test_diarize_in_env_passes_the_recording_and_the_model_and_parses(tmp_path, monkeypatch):
    want = [str(tmp_path / "bh.wav"), "--model", str(nem.model_dir(tmp_path))]
    s = _engine_stub(tmp_path, f'import json, sys\nassert sys.argv[1:] == {want!r}, sys.argv\n'
                               'print(json.dumps({"segments": [{"start": 0.0, "end": 2.0, "speaker": "nem3"}]}))\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    out = nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30)
    assert out == fp.Outcome(fp.OK, payload=[(0.0, 2.0, 3)])


def test_diarize_in_env_turns_a_protocol_breach_into_a_failure(tmp_path, monkeypatch):
    s = _engine_stub(tmp_path, 'print(\'{"segments": [{"start": 2.0, "end": 1.0, "speaker": "nem0"}]}\')\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    out = nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith("ответ движка не по протоколу: границы")


def test_diarize_in_env_passes_unavailable_and_failed_through(tmp_path, monkeypatch):
    """Заданный ключ: UNAVAILABLE и FAILED уходят как есть — установщик ключ не лечит."""
    s = _engine_stub(tmp_path, f'import sys\nprint("нет весов", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30) == \
        fp.Outcome(fp.UNAVAILABLE, reason="нет весов")
    s.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    assert nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30) == \
        fp.Outcome(fp.FAILED, reason="код 1: без вывода")


def test_the_script_is_this_module_file():
    assert nem.SCRIPT == REPO / "src" / "diarize_nemotron.py"
    assert nem.SCRIPT.resolve() == pathlib.Path(nem.__file__).resolve()


def test_the_caller_never_imports_mlx(tmp_path, monkeypatch):
    """Искатель в sys.meta_path на всё время вызова: попытка импорта mlx в процессе
    пересборки видна, даже если модуль потом выгрузили (круг 6 по №473, I4)."""
    tried = []

    class Spy(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("mlx", "mlx_audio"):
                tried.append(name)
            return None

    for name in [m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_audio")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Spy(), *sys.meta_path])
    s = _engine_stub(tmp_path, 'print(\'{"segments": [{"start": 0.0, "end": 1.0, "speaker": "nem0"}]}\')\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30).ok
    monkeypatch.undo()
    wav = _wav(tmp_path / "bh.wav", [0] * 1600)
    for name in [m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_audio")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Spy(), *sys.meta_path])
    nem.diarize_in_env(sys.executable, wav, root=tmp_path, timeout=60)
    nem.probe_in_env(sys.executable, root=tmp_path, timeout=60)
    assert tried == [], f"процесс пересборки пытался импортировать {tried}"
    # сам искатель работает: прямой поиск модуля он видит (стоит mlx-audio или нет)
    importlib.util.find_spec("mlx_audio")
    assert tried == ["mlx_audio"]


def test_the_engine_lives_under_the_data_root_next_to_its_weights(tmp_path):
    """Раскладку движка — окружение и веса — знает его модуль (№474): установщик и
    дверь берут пути отсюда, строка «nemotron» не живёт в двух местах."""
    assert nem.engine_dir(tmp_path) == tmp_path / "engines" / "nemotron"
    assert nem.engine_python(tmp_path) == tmp_path / "engines" / "nemotron" / "python" / "bin" / "python3"
    assert nem.model_dir(tmp_path) == tmp_path / "models" / "diar" / "nemotron"


def test_inside_the_bundle_the_command_names_the_app_python_next_to_the_code(tmp_path, monkeypatch):
    """Код внутри Charoite.app: Python приложения — рядом, кто бы ни печатал команду
    (доктор из терминала — тоже), а корень данных — тот, с которым работает печатающий
    (№489); пути с пробелами не рвут команду."""
    resources = tmp_path / "Мой Charoite.app" / "Contents" / "Resources"
    bundled = resources / "python" / "bin" / "python3"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#", encoding="utf-8")
    monkeypatch.setattr(nem, "code_root", lambda _file: resources / "charoite")
    root = tmp_path / "Application Support" / "Charoite"
    assert shlex.split(nem.install_command(root)) == [
        f"CHAROITE_ROOT={root}", str(bundled), "-B", str(resources / "charoite" / "scripts" / "install_engine.py"),
        "nemotron"]


def test_the_command_is_one_shell_line_that_names_the_root(tmp_path):
    """Команду человек вставляет в shell целиком: присваивание корня — отдельным
    словом перед интерпретатором, путь с пробелом и кавычкой не рвёт его. Проверка —
    настоящим `sh -c`, а не разбором shlex (входной круг 1 по №489, I1)."""
    root = tmp_path / "Мои данные" / "it's"
    echo = f"{shlex.quote(sys.executable)} -c 'import os, sys; print(os.environ[\"CHAROITE_ROOT\"]); print(sys.argv[1:])'"
    command = nem.install_command(root)
    prefix = f"CHAROITE_ROOT={shlex.quote(str(root))}"
    assert command.startswith(prefix + " "), command
    out = subprocess.run(["sh", "-c", f"{prefix} {echo} nemotron"], capture_output=True, text=True, check=True)
    assert out.stdout.splitlines() == [str(root), "['nemotron']"]


@pytest.mark.parametrize("inside", ["", "Contents/Resources/charoite", "data"])
def test_a_root_inside_an_app_bundle_gets_words_not_a_command(tmp_path, inside):
    """Корень внутри бандла — догадка процесса, запущенного из бандла без корня:
    строка для shell поставила бы движок в подписанный `.app`. Вместо команды — слова,
    где взять корень, без метасимволов shell (входной круг 2 по №489, M1)."""
    (tmp_path / "Charoite.app" / "Contents").mkdir(parents=True)
    text = nem.install_command(tmp_path / "Charoite.app" / inside)
    assert "install_engine.py" not in text and "CHAROITE_ROOT" in text
    assert not set("<>|;&$`") & set(text)


def test_a_bundle_without_its_python_is_not_named(tmp_path, monkeypatch):
    """Код лежит как в бандле, а Python рядом нет: несуществующий путь не называем."""
    resources = tmp_path / "Charoite.app" / "Contents" / "Resources"
    monkeypatch.setattr(nem, "code_root", lambda _file: resources / "charoite")
    assert nem.app_python() == ""
    assert nem.install_command(tmp_path / "данные").startswith(
        f"CHAROITE_ROOT={shlex.quote(str(tmp_path / 'данные'))} {nem.APP_PYTHON} -B ")


def test_outside_the_bundle_the_command_asks_for_the_app_path(tmp_path):
    """Вне бандла (код из клона) Python приложения не узнать: `sys.executable` печатающего —
    доктор из venv разработчика, чужой переносимый Python другой версии — установщик отклонил
    бы. Команда называет путь внутри приложения и просит подставить свой Charoite.app."""
    script = str(REPO / "scripts" / "install_engine.py")
    assert nem.app_python() == ""
    assert nem.install_command(tmp_path) == (f"CHAROITE_ROOT={shlex.quote(str(tmp_path))} {nem.APP_PYTHON} -B "
                                             f"{shlex.join([script, 'nemotron'])} (путь к Charoite.app — ваш)")


def test_the_probe_has_a_working_default_timeout(tmp_path, monkeypatch):
    """Доктор и `--check` зовут пробу без своего потолка: умолчание обязано дать движку ответить."""
    monkeypatch.setattr(nem, "SCRIPT", _engine_stub(tmp_path, 'print(\'{"mlx_audio": "0.5.6"}\')\n'))
    assert nem.probe_in_env(sys.executable, root=tmp_path) == fp.Outcome(fp.OK, payload={"mlx_audio": "0.5.6"})


def test_the_probe_passes_the_model_and_returns_the_version(tmp_path, monkeypatch):
    want = ["--probe", "--model", str(nem.model_dir(tmp_path))]
    s = _engine_stub(tmp_path, f'import sys\nassert sys.argv[1:] == {want!r}, sys.argv\n'
                               'print(\'{"mlx_audio": "0.5.6"}\')\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.probe_in_env(sys.executable, root=tmp_path, timeout=30) == \
        fp.Outcome(fp.OK, payload={"mlx_audio": "0.5.6"})


@pytest.mark.parametrize("body", ['print(\'{"mlx_audio": 5}\')', 'print(\'{"version": "0.5.6"}\')'])
def test_the_probe_turns_a_protocol_breach_into_a_failure(tmp_path, monkeypatch, body):
    monkeypatch.setattr(nem, "SCRIPT", _engine_stub(tmp_path, body + "\n"))
    out = nem.probe_in_env(sys.executable, root=tmp_path, timeout=30)
    assert out.kind == fp.FAILED and out.reason.startswith("проба движка не по протоколу")


def test_the_probe_passes_unavailable_through_and_refuses_without_an_engine(tmp_path, monkeypatch):
    s = _engine_stub(tmp_path, f'import sys\nprint("нет весов", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    # заданный ключ главнее установленного окружения: установщик его не лечит, команды нет (I1)
    assert nem.probe_in_env(sys.executable, root=tmp_path, timeout=30) == \
        fp.Outcome(fp.UNAVAILABLE, reason="нет весов")
    out = nem.probe_in_env("", root=tmp_path, timeout=30)
    assert out.kind == fp.UNAVAILABLE and out.reason.count(nem.install_command(tmp_path)) == 1


def test_an_installed_engine_that_cannot_work_gets_the_command_once_in_the_probe(tmp_path, monkeypatch):
    """Пустой ключ, окружение стоит, движку нечем работать — проба (доктор, `--check`)
    отдаёт причину и команду установщика с корнем, один раз."""
    installed = nem.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.symlink_to(sys.executable)
    s = _engine_stub(tmp_path, f'import sys\nprint("нет весов", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    assert nem.probe_in_env("", root=tmp_path, timeout=30) == \
        fp.Outcome(fp.UNAVAILABLE, reason=f"нет весов — окружение ставит: {nem.install_command(tmp_path)}")


def test_the_transcript_gets_a_pointer_not_the_paths_of_the_machine(tmp_path, monkeypatch):
    """Причина отказа пересборки уходит в шапку стенограммы, а её пересылают людям:
    команда с корнем данных и путём к коду несёт имя учётки. В шапке — указатель на
    доктора, без `CHAROITE_ROOT` и путей установщика (выходной круг 1 по №489, M3)."""
    installed = nem.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.symlink_to(sys.executable)
    s = _engine_stub(tmp_path, f'import sys\nprint("нет весов", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    monkeypatch.setattr(nem, "SCRIPT", s)
    out = nem.diarize_in_env("", tmp_path / "bh.wav", root=tmp_path, timeout=30)
    assert out == fp.Outcome(fp.UNAVAILABLE, reason=f"нет весов — {nem.INSTALLER_POINTER}")
    assert str(tmp_path) not in out.reason and "install_engine.py" not in out.reason


def test_a_failure_of_the_engine_does_not_promise_that_reinstalling_helps(tmp_path, monkeypatch):
    """FAILED — падение, а не нехватка: команду установщика к нему вызывающий не дописывает."""
    monkeypatch.setattr(nem, "SCRIPT", _engine_stub(tmp_path, "import sys\nsys.exit(3)\n"))
    out = nem.diarize_in_env(sys.executable, tmp_path / "bh.wav", root=tmp_path, timeout=30)
    assert out.kind == fp.FAILED and "install_engine.py" not in out.reason


def test_main_probe_answers_the_version_without_reading_a_recording(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nem, "availability", lambda path: None)
    monkeypatch.setattr(nem.importlib.metadata, "version", lambda dist: {"mlx-audio": "0.5.6"}[dist])
    monkeypatch.setattr(nem, "read_wav", lambda p: pytest.fail("проба читает запись"))
    monkeypatch.setattr(nem, "load_model", lambda *a, **k: pytest.fail("проба грузит веса"))
    assert nem.main(["--probe", "--model", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"mlx_audio": "0.5.6"}


def test_main_probe_without_the_engine_exits_10_and_the_recording_stays_required(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nem, "availability", lambda path: "нет пакета mlx-audio — рецепт")
    assert nem.main(["--probe", "--model", str(tmp_path)]) == EXIT_ENGINE_UNAVAILABLE
    assert capsys.readouterr().err == "нет пакета mlx-audio — рецепт\n"
    with pytest.raises(SystemExit) as e:
        nem.main(["--model", str(tmp_path)])
    assert e.value.code == 2
