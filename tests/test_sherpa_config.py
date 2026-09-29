"""№508: одна фабрика конфигов sherpa-onnx — число потоков решается при сборке конфига.

Умолчание sherpa-onnx — один поток, и микрофон 69-минутной встречи 29.09 размечался
одним ядром из восьми около 22 минут. Фабрика даёт живому пути один поток всегда,
разбору после встречи — один поток, пока идёт запись, иначе наибольшее измеренное
значение не больше половины производительных ядер. Конфиг, собранный мимо фабрики,
молча вернул бы умолчание библиотеки — это держит сторож внизу файла.
"""
import ast
import pathlib
import sys
import types

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import sherpa_config as sc  # noqa: E402


@pytest.fixture
def measured(monkeypatch):
    """Как после замера: проверенные числа потоков 1, 2 и 4, записи нет."""
    monkeypatch.setattr(sc, "MEASURED_THREADS", (1, 2, 4))
    monkeypatch.setattr(sc.live_gate, "daemon_alive", lambda root: False)


def test_the_live_path_is_always_one_thread(measured, monkeypatch):
    monkeypatch.setattr(sc, "performance_cores", lambda: 8)
    assert sc.threads_for(sc.LIVE) == 1


@pytest.mark.parametrize("cores,want", [(8, 4), (7, 2), (6, 2), (4, 2), (3, 1), (1, 1)])
def test_after_a_meeting_the_largest_measured_count_within_half_the_performance_cores(
        measured, monkeypatch, tmp_path, cores, want):
    """7 ядер → предел 3 → 2, а не 3: неизмеренное число не выходит никогда."""
    monkeypatch.setattr(sc, "performance_cores", lambda: cores)
    assert sc.threads_for(sc.POST, tmp_path) == want


def test_after_a_meeting_one_thread_while_a_recording_runs(measured, monkeypatch, tmp_path):
    """Пересборка, уступившая встрече до потолка, продолжает в тесноте — одним потоком."""
    monkeypatch.setattr(sc, "performance_cores", lambda: 8)
    monkeypatch.setattr(sc.live_gate, "daemon_alive", lambda root: True)
    assert sc.threads_for(sc.POST, tmp_path) == 1


def test_until_measured_the_factory_changes_nothing(monkeypatch):
    """Без замера — только один поток: фабрика задаёт число явно и не ускоряет наугад."""
    monkeypatch.setattr(sc, "performance_cores", lambda: 16)
    monkeypatch.setattr(sc.live_gate, "daemon_alive", lambda root: False)
    assert sc.threads_for(sc.POST, None) in sc.MEASURED_THREADS
    assert sc.MEASURED_THREADS[0] == 1


def test_an_unknown_kind_of_work_is_refused():
    with pytest.raises(ValueError, match="неизвестен"):
        sc.threads_for("fast")


def test_performance_cores_fall_back_to_half_the_logical_ones(monkeypatch):
    """Нет ключа perflevel0 (Intel, Rosetta, не macOS) — половина логических ядер."""
    def no_sysctl(*a, **k):
        raise OSError("нет sysctl")
    monkeypatch.setattr(sc.subprocess, "run", no_sysctl)
    monkeypatch.setattr(sc.os, "cpu_count", lambda: 10)
    assert sc.performance_cores() == 5
    monkeypatch.setattr(sc.os, "cpu_count", lambda: None)
    assert sc.performance_cores() == 1


def test_the_configs_carry_the_thread_count(monkeypatch, tmp_path):
    made = []

    class _Cfg:
        def __init__(self, **kw):
            self.kw = kw
            made.append(type(self).__name__)

    fake = types.SimpleNamespace(
        OfflineSpeakerSegmentationModelConfig=type("OfflineSpeakerSegmentationModelConfig", (_Cfg,), {}),
        OfflineSpeakerSegmentationPyannoteModelConfig=type(
            "OfflineSpeakerSegmentationPyannoteModelConfig", (_Cfg,), {}),
        SpeakerEmbeddingExtractorConfig=type("SpeakerEmbeddingExtractorConfig", (_Cfg,), {}))
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    monkeypatch.setattr(sc, "threads_for", lambda kind, root=None: 3 if kind == sc.POST else 1)
    seg = sc.segmentation_config(tmp_path / "seg.onnx", kind=sc.POST, root=tmp_path)
    emb = sc.embedding_config(tmp_path / "emb.onnx", kind=sc.LIVE)
    assert seg.kw["num_threads"] == 3
    assert seg.kw["pyannote"].kw["model"] == str(tmp_path / "seg.onnx")
    assert emb.kw == {"model": str(tmp_path / "emb.onnx"), "num_threads": 1}


# ------------------------------------------------------------ сторож фабрики

GUARDED = frozenset({"OfflineSpeakerSegmentationModelConfig", "SpeakerEmbeddingExtractorConfig"})


def _mentions(source: str) -> list[int]:
    """Строки, где названы конфиги sherpa: вызов через модуль, импорт с псевдонимом,
    имя в выражении и строка для getattr — любой способ собрать конфиг мимо фабрики."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in GUARDED:
            lines.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id in GUARDED:
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and any(a.name in GUARDED for a in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and node.value in GUARDED:
            lines.append(node.lineno)
    return lines


def test_the_guard_sees_every_way_to_build_a_config():
    """Отрицательный опыт сторожа: каждая из форм обязана им ловиться."""
    for source in ("import sherpa_onnx\nsherpa_onnx.SpeakerEmbeddingExtractorConfig(model='m')\n",
                   "import sherpa_onnx as so\nso.OfflineSpeakerSegmentationModelConfig()\n",
                   "from sherpa_onnx import SpeakerEmbeddingExtractorConfig as C\nC(model='m')\n",
                   "import sherpa_onnx\nK = getattr(sherpa_onnx, 'SpeakerEmbeddingExtractorConfig')\n"):
        assert _mentions(source), source
    assert _mentions("import sherpa_onnx\nsherpa_onnx.OfflineRecognizer\n") == []


#: Скрипты, которым пока можно собирать конфиг сами, — с причиной и карточкой. Бенч
#: гоняет свои настройки вместо продакшен-прохода — это №472; когда он перейдёт на
#: `diarize.diarize()`, строка уходит (выходной круг 1 по №508, M1).
SCRIPT_EXCEPTIONS = {"scripts/diar_bench.py": "№472: бенч должен звать продакшен-проход"}


def test_sherpa_configs_are_built_only_by_the_factory():
    """Обход — `src/` и `scripts/`: скрипт, собранный мимо фабрики, тоже берёт умолчание
    библиотеки, и замер потоков через него мерил бы не то, что уйдёт в прод."""
    repo = SRC.parent
    files = [p for p in sorted(SRC.rglob("*.py")) if p.name != "sherpa_config.py"]
    files += sorted((repo / "scripts").glob("*.py"))
    offenders = [f"{p.relative_to(repo)}:{line}" for p in files
                 if str(p.relative_to(repo)) not in SCRIPT_EXCEPTIONS
                 for line in _mentions(p.read_text(encoding="utf-8"))]
    assert offenders == [], "конфиг sherpa мимо sherpa_config — умолчание библиотеки, один поток"
    stale = [f for f in SCRIPT_EXCEPTIONS
             if not _mentions((repo / f).read_text(encoding="utf-8"))]
    assert stale == [], f"исключение без нарушения — убрать из списка: {stale}"
