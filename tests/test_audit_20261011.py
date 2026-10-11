"""Adversarial regression witnesses from the 11 October audit."""
import errno
import importlib.util
import pathlib
import shutil
import json
import os
import sys

import pytest

from charoite_graph import dossier, graph_search as gs
from charoite_graph.model_seam import Embedder
from charoite_schema import CHAROITE


def _nightly_dossier():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts/nightly_dossier.py"
    spec = importlib.util.spec_from_file_location("nightly_dossier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("payload", [[], {"досье": [None]}, {"досье": ["broken"]}, {"досье": None}])
def test_bad_dossier_index_keeps_graph_search_available(tmp_path, payload):
    graph = tmp_path / "graph"
    graph.mkdir()
    (graph / "Доступ.md").write_text("# Доступ\nСервисный токен получен.", encoding="utf-8")
    folder = graph / CHAROITE.dossier_dir
    folder.mkdir()
    (folder / dossier.INDEX_JSON).write_text(json.dumps(payload), encoding="utf-8")
    search = gs.GraphSearch(graph, data_dir=None, schema=CHAROITE,
                           embedder=Embedder(lambda texts, timeout: [], "audit"))
    search.refresh(force=True)
    assert dossier.load_index(folder) == []
    result = search.search("сервисный токен", semantic=False)
    assert any("Доступ.md" in block for block in result.blocks)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("error", [errno.EACCES, errno.ENOSPC, errno.EIO])
def test_one_dossier_write_failure_does_not_abort_other_themes(tmp_path, monkeypatch, existing, error):
    from test_dossier import _граф

    nd = _nightly_dossier()
    graph = _граф(tmp_path)
    extra = graph / "Ядра" / "Вторая тема.md"
    extra.write_text("# Вторая тема\n## Статус\nРаботает [[Встречи/2026-07-22_1000]] [[Встречи/2026-07-24_1100]]\n", encoding="utf-8")
    previous = "# Настройка доступа\nПрежний документ.\n## Правки автора\n\nРучная правка.\n"
    target = graph / CHAROITE.dossier_dir / "Настройка доступа.md"
    if existing:
        target.parent.mkdir()
        target.write_text(previous, encoding="utf-8")
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(nd, "generate", lambda *a, **k:
                        "## Сейчас\nготово\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт")
    real_write = nd.safe_write.write_text
    failed = []

    def write(path, text, **kwargs):
        if path.name == "Настройка доступа.md":
            failed.append(path)
            raise OSError(error, "injected single-file refusal")
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(nd.safe_write, "write_text", write)
    result = nd.run(graph, {"sufler": {}}, full=False, dry=False, limit=5)
    assert failed
    assert result["отказы"] == 0
    assert result["ошибки_записи"] == 1
    assert result["собрано"] == 1
    assert (graph / CHAROITE.dossier_dir / dossier.INDEX_JSON).is_file()
    if existing:
        assert target.read_text(encoding="utf-8") == previous
        assert {e["тема"] for e in dossier.load_index(target.parent)} == {"Вторая тема", "Настройка доступа"}
    monkeypatch.setattr(nd, "cfg", lambda: {"sufler": {}})
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    assert nd.main() == 2


def test_vectors_of_wrong_dimension_do_not_prove_empty_result(tmp_path):
    graph = tmp_path / "graph"
    graph.mkdir()
    (graph / "note.md").write_text("# Заметка\nОбсуждали поставку оборудования.", encoding="utf-8")
    dimension = [2]
    search = gs.GraphSearch(graph, data_dir=tmp_path / "cache", schema=CHAROITE,
                           embedder=Embedder(lambda texts, timeout: [[1.0] * dimension[0] for _ in texts], "audit"))
    search.refresh(force=True)
    assert search.embed_pending() > 0
    dimension[0] = 3
    result = search.search("кварц", dossiers=False)
    assert result.status == gs.Verdict.UNVERIFIED, result
    assert not result.sem_used
    assert gs.REASON_DIMENSION in result.why_low
    assert search.pending_vectors() == [str(graph / "note.md")]
    assert search.embed_pending() == 1
    repaired = search.search("кварц", dossiers=False)
    assert repaired.sem_used and repaired.status == gs.Verdict.CONFIDENT
    # Новый читатель получает пригодный дисковый кэш при прежнем mtime заметки.
    cold = gs.GraphSearch(graph, data_dir=tmp_path / "cache", schema=CHAROITE,
                         embedder=Embedder(lambda texts, timeout: [[1.0] * 3 for _ in texts], "audit"))
    cold.refresh(force=True)
    assert cold.pending_vectors() == []
    assert cold.search("кварц", dossiers=False).status == gs.Verdict.CONFIDENT


def test_index_keeps_good_entries_next_to_malformed_ones(tmp_path):
    good = {"тема": "Доступ", "ключи": ["токен"], "файл": "Досье/Доступ.md",
            "собрано": "2026-10-11", "источников": 2}
    payload = {"досье": [None, good, {"тема": 42, "ключи": ["токен"]},
                        {"тема": "Брак", "ключи": [None]}]}
    (tmp_path / dossier.INDEX_JSON).write_text(json.dumps(payload), encoding="utf-8")
    assert [e["тема"] for e in dossier.lookup(tmp_path, "токен")] == ["Доступ"]


def test_a_partial_dimension_rebuild_does_not_publish_a_mixed_blob(tmp_path):
    graph = tmp_path / "graph"
    graph.mkdir()
    dimension = [2]

    def embed(texts, timeout):
        return [([1.0, 0.0, 0.0] if text == "кварц" else
                 [0.0, 1.0] + [0.0] * (dimension[0] - 2)) for text in texts]

    for i in range(3):
        (graph / f"note{i}.md").write_text("# Поставка\nОборудование доставлено.", encoding="utf-8")
    cache = tmp_path / "cache"
    search = gs.GraphSearch(graph, data_dir=cache, schema=CHAROITE,
                           embedder=Embedder(embed, "audit"))
    search.refresh(force=True)
    assert search.embed_pending() == 3
    dimension[0] = 3
    changed = graph / "note0.md"
    stamp = changed.stat().st_mtime + 2
    changed.write_text("# Поставка\nОборудование доставлено сегодня.", encoding="utf-8")
    os.utime(changed, (stamp, stamp))
    search.refresh(force=True)
    assert search.embed_pending() == 1
    assert search.search("кварц", dossiers=False).status == gs.Verdict.UNVERIFIED
    assert len(search.pending_vectors()) == 2
    cold = gs.GraphSearch(graph, data_dir=cache, schema=CHAROITE,
                         embedder=Embedder(embed, "audit"))
    cold.refresh(force=True)
    assert cold.load_vectors() == 1   # пригодный блоб читается, а не гасится целиком
    assert cold.embed_pending() == 2
    assert cold.search("кварц", dossiers=False).status == gs.Verdict.EMPTY


@pytest.mark.parametrize("phase", ["mkdir", "index", "backup"])
def test_nightly_storage_failures_are_visible_to_the_entry_point(tmp_path, monkeypatch, phase):
    from test_dossier import _граф

    nd = _nightly_dossier()
    graph = _граф(tmp_path)
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(nd, "generate", lambda *a, **k:
                        "## Сейчас\nготово\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт")
    if phase == "index":
        def fail_index(*a, **k):
            raise OSError(errno.EIO, "injected index refusal")
        monkeypatch.setattr(nd.dossier, "write_index", fail_index)
    elif phase == "backup":
        def fail_backup(*a, **k):
            raise OSError(errno.EIO, "injected backup refusal")
        monkeypatch.setattr(nd.dossier, "backup", fail_backup)
    else:
        real_mkdir = type(graph).mkdir
        def mkdir(path, *a, **k):
            if path == graph / CHAROITE.dossier_dir:
                raise OSError(errno.EACCES, "injected directory refusal")
            return real_mkdir(path, *a, **k)
        monkeypatch.setattr(type(graph), "mkdir", mkdir)
    result = nd.run(graph, {"sufler": {}}, full=False, dry=False, limit=5)
    assert result["ошибки_записи"] == 1
    assert result["отказы"] == 0
    monkeypatch.setattr(nd, "cfg", lambda: {"sufler": {}})
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    assert nd.main() == 2


@pytest.mark.parametrize("batch", [1, 2])
def test_inconsistent_embedding_dimensions_stop_before_mixing(tmp_path, batch):
    graph = tmp_path / "graph"
    graph.mkdir()
    for i in range(2):
        (graph / f"note{i}.md").write_text("# Поставка\nОборудование доставлено.", encoding="utf-8")
    sequence = iter([[1.0, 0.0], [1.0, 0.0, 0.0]])
    search = gs.GraphSearch(graph, data_dir=tmp_path / "cache", schema=CHAROITE,
                           embedder=Embedder(lambda texts, timeout: [next(sequence) for _ in texts], "audit"))
    search.refresh(force=True)
    assert search.embed_pending(batch=batch) == (1 if batch == 1 else 0)
    assert "размерность" in search.note
    assert len(search.pending_vectors()) == (1 if batch == 1 else 2)
    cold = gs.GraphSearch(graph, data_dir=tmp_path / "cache", schema=CHAROITE,
                         embedder=Embedder(lambda texts, timeout: [], "audit"))
    cold.refresh(force=True)
    assert cold.load_vectors() == (1 if batch == 1 else 0)


def test_dossier_generation_does_not_recreate_a_removed_graph(tmp_path, monkeypatch):
    from test_dossier import _граф

    nd = _nightly_dossier()
    graph = _граф(tmp_path)
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)

    def generate(*a, **k):
        shutil.rmtree(graph)
        return "## Сейчас\nготово\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт"

    monkeypatch.setattr(nd, "generate", generate)
    result = nd.run(graph, {"sufler": {}}, full=False, dry=False, limit=5)
    assert not graph.exists()
    assert result["собрано"] == 0
    assert result["ошибки_записи"] >= 1
