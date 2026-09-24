"""Пути утилит от корня данных — из канона, а не догадкой (№338).

Перевод двадцати пяти скриптов с `ROOT` на `resolve_root(__file__)`/`_root()` прошёл
по строкам, которые не исполнял ни один тест: мутатор в CI вернул тринадцать
выживших — `_root()` возвращает None, `/` в пути заменён на `*`. Здесь каждая такая
строка исполняется: корень называется публичной дверью канона, и путь обязан
оказаться внутри него. Модули импортируются по имени — так их тесты находит мутатор.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent / "src"))

import charoite_paths  # noqa: E402


@pytest.fixture
def root(tmp_path):
    """Корень данных теста — временный, названный каноном."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    return tmp_path.resolve()


def test_diar_bench_root_and_fixture_live_in_the_data_root(root):
    import diar_bench
    assert diar_bench._root() == root
    assert diar_bench._fixture() == root / "data" / "diar_bench"


def test_get_models_targets_default_to_the_data_root(root):
    import get_models
    assert get_models.seg_target(None) == root / "models" / "diar" / "segmentation.onnx"
    assert get_models.stt_target(None) == root / "models" / "stt" / "sensevoice.onnx"


def test_graph_doctor_report_lives_in_the_data_root(root):
    import graph_doctor
    assert graph_doctor.report_path() == root / "logs" / "graph_doctor.json"


def test_nightly_dossier_review_asks_the_canon(root):
    import nightly_dossier_review
    assert nightly_dossier_review._root() == root


def test_tier3_cores_asks_the_canon(root):
    # боевой путь, а не хелпер: единственный потребитель прежнего _root() ушёл за дверь
    # graphs.revise_cores, и тест сторожил функцию, которую никто не зовёт (Opus M1 круга 2)
    import tier3_cores
    assert tier3_cores.stamps_path() == root / "logs" / "tier3_last_run.json"


def test_bench_models_reads_transcripts_from_the_data_root(root):
    import bench_models
    (root / "transcripts").mkdir()
    (root / "transcripts" / "2026-01-01_1000.md").write_text("слово " * 5000, encoding="utf-8")
    prompt = bench_models.long_prompt()
    assert "слово" in prompt, "длинная стенограмма берётся из корня данных"


def test_doctor_looks_for_the_config_in_the_data_root(root, capsys):
    import doctor
    assert doctor.check_config() == {}
    assert "config/config.yaml отсутствует" in capsys.readouterr().out


def test_fix_action_items_checks_the_config_in_the_data_root(root):
    import fix_action_items
    assert fix_action_items.graph_dir(None) is None, "без config.yaml в корне данных графа нет"


def test_dedup_archive_reads_the_config_from_the_data_root(root, monkeypatch):
    import dedup_archive
    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text("sufler: {}\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["dedup_archive.py"])
    # конфиг корня прочитан, дальше — внятный отказ строкой (нет graph_dir или нет
    # архива); мутант пути `/`→`*` дал бы TypeError, а не SystemExit
    with pytest.raises(SystemExit) as выход:
        dedup_archive.main()
    assert isinstance(выход.value.code, str) and выход.value.code, "отказ — сообщением, а не кодом"


def test_bench_extract_writes_results_into_the_data_root(root, monkeypatch):
    import bench_extract

    class Стоп(Exception):
        pass

    стенограмма = root / "встреча.md"
    стенограмма.write_text("текст", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["bench_extract.py", "модель", "--files", str(стенограмма)])
    monkeypatch.setattr(bench_extract.gu, "load_cfg", lambda *a, **k: {})
    monkeypatch.setattr(bench_extract, "measure", lambda *a, **k: (_ for _ in ()).throw(Стоп()))
    with pytest.raises(Стоп):
        bench_extract.main()
    assert (root / "logs" / "bench_extract").is_dir(), "каталог замера создан в корне данных"
