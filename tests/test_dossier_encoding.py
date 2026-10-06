"""Повреждения синхронизации: ночь продолжает темы и сохраняет авторский текст."""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
from collections import Counter
from datetime import date

import pytest

from charoite_graph import dossier
from charoite_schema import CHAROITE

THEMES = ("Альфа", "Бета", "Гамма")
GOOD = ("## Сейчас\nновое тело\n## Как пришли\nт\n## Решено\nт\n"
        "## Открыто\nт\n## Кто в теме\nт")


def _script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts/nightly_dossier.py"
    spec = importlib.util.spec_from_file_location("nightly_dossier_encoding", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def night(tmp_path, monkeypatch):
    nd = _script()
    graph = tmp_path / "Граф"
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    folder = graph / CHAROITE.dossier_dir
    folder.mkdir()
    paths = {}
    for theme, size in zip(THEMES, (5, 4, 3)):
        (graph / "Ядра" / f"{theme}.md").write_text(f"# {theme}\nядро\n", encoding="utf-8")
        for i in range(size - 1):
            (graph / "Встречи" / f"{theme}_{i}.md").write_text(
                f"# Встреча\n[[Ядра/{theme}]] источник\n", encoding="utf-8")
        paths[theme] = folder / f"{theme}.md"
        paths[theme].write_text(
            f"---\nтема: {theme}\nотпечаток: старый\nсобрано: 2026-07-20\n---\n"
            f"# {theme}\n## Сейчас\nстарое тело\n## Правки автора\n\nоставить решение автора\n",
            encoding="utf-8")
    calls = []

    def generate(theme, members, files, c, **kw):
        calls.append((theme, list(members), files))
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(nd, "_graph_lock", lambda *a: contextlib.nullcontext(True))
    return nd, graph, folder, paths, calls


def _watch(monkeypatch, paths, unreadable=None):
    """Path.open — нижняя дверь и для read_text, и для read_bytes; copy2 не подменяется."""
    counts = Counter()
    original = pathlib.Path.open

    def open_path(self, mode="r", *a, **kw):
        if self in paths.values() and mode in ("r", "rb"):
            counts[self] += 1
            if self == unreadable:
                raise PermissionError(13, "Permission denied", str(self))
        return original(self, mode, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "open", open_path)
    return counts


def _damage(path, old, new):
    before = path.read_bytes().replace(old.encode(), new)
    path.write_bytes(before)
    return before


def _all_index(folder):
    entries = dossier.load_index(folder)
    assert {e["тема"] for e in entries} == set(THEMES)
    md = (folder / dossier.INDEX_MD).read_text(encoding="utf-8")
    assert all(theme in md for theme in THEMES)
    return {e["тема"]: e for e in entries}


def _account(result, counts, paths, capsys, damaged=1):
    assert result["битые"] == damaged
    assert counts == Counter({p: 1 for p in paths.values()}), counts
    log = capsys.readouterr().out
    damaged_path = str(paths["Бета"])
    assert sum(damaged_path in line for line in log.splitlines()) == damaged


@pytest.mark.parametrize("where", ["body", "fingerprint"])
def test_damaged_body_or_fingerprint_rebuilds_every_theme(night, monkeypatch, capsys, where):
    nd, graph, folder, paths, calls = night
    old, new = (("старое тело", b"old\xffbody") if where == "body"
                else ("отпечаток: старый", "отпечаток: ".encode() + b"old\xfffp"))
    before = _damage(paths["Бета"], old, new)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert result["собрано"] == 3 and result["отказы"] == 0
    assert [c[0] for c in calls] == list(THEMES)
    _all_index(folder)
    _account(result, counts, paths, capsys)
    copies = list((folder / ".backup").glob("*/Бета.md"))
    assert len(copies) == 1 and copies[0].read_bytes() == before
    text = paths["Бета"].read_text(encoding="utf-8")
    assert "новое тело" in text and "оставить решение автора" in text and "\ufffd" not in text


@pytest.mark.parametrize("where", ["manual", "heading"])
def test_damaged_author_text_refuses_before_model(night, monkeypatch, capsys, where):
    nd, graph, folder, paths, calls = night
    old, new = (("решение", b"re\xffsolution") if where == "manual"
                else ("Правки", b"Pr\xffavki"))
    before = _damage(paths["Бета"], old, new)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert [c[0] for c in calls] == ["Альфа", "Гамма"]
    assert result["собрано"] == 2 and result["отказы"] == 0
    _all_index(folder)
    _account(result, counts, paths, capsys)
    assert paths["Бета"].read_bytes() == before
    assert not list((folder / ".backup").glob("*/Бета.md"))


def test_damaged_graph_source_stays_in_cluster(night, capsys):
    nd, graph, folder, paths, calls = night
    source = graph / "Встречи/Бета_0.md"
    _damage(source, "источник", b"source\xfftext")
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert result["собрано"] == 3 and result["битые"] == 0
    beta = next(c for c in calls if c[0] == "Бета")
    assert "Бета_0" in beta[1] and "\ufffd" in beta[2]["Бета_0"]["text"]
    assert beta[2]["Бета_0"]["mtime"] == source.stat().st_mtime
    _all_index(folder)
    assert sum(str(source) in line for line in capsys.readouterr().out.splitlines()) == 1


def test_unreadable_refuses_before_model_and_keeps_index(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    counts = _watch(monkeypatch, paths, unreadable=paths["Бета"])
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    _all_index(folder)  # main теряет тему именно здесь
    assert [c[0] for c in calls] == ["Альфа", "Гамма"]
    assert result["собрано"] == 2 and result["отказы"] == 1
    assert result["битые"] == 0 and counts == Counter({p: 1 for p in paths.values()})
    assert "не прочитано" in capsys.readouterr().out


@pytest.mark.parametrize("raw,expected", [
    ("мусор", None), ("20261006", "2026-10-06"), ("2026-W41-1", "2026-10-05"),
])
def test_collected_date_is_parsed_and_normalized(night, raw, expected):
    nd, graph, folder, paths, calls = night
    path = paths["Бета"]
    path.write_text(path.read_text(encoding="utf-8").replace("2026-07-20", raw), encoding="utf-8")
    nd.run(graph, {}, full=False, dry=False, limit=0)
    assert _all_index(folder)["Бета"]["собрано"] == (expected or date.today().isoformat())
    assert calls == []


def test_matching_fingerprint_does_not_force_damaged_rebuild(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    files, links = dossier.scan(graph, schema=CHAROITE)
    clusters = dossier.clusters(files, links, schema=CHAROITE)
    for theme, path in paths.items():
        fp = dossier.fingerprint(clusters[theme], files)
        path.write_text(path.read_text(encoding="utf-8").replace("старый", fp), encoding="utf-8")
    before = _damage(paths["Бета"], "старое тело", b"old\xffbody")
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert calls == [] and result["без_изменений"] == 3
    assert _all_index(folder)["Бета"]["отпечаток"] == dossier.fingerprint(clusters["Бета"], files)
    _account(result, counts, paths, capsys)
    assert paths["Бета"].read_bytes() == before


def test_empty_graph_has_damage_count(tmp_path):
    assert _script().run(tmp_path, {}, full=False, dry=False, limit=12)["битые"] == 0


def test_closed_window_reads_tail_once(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    _damage(paths["Бета"], "старое тело", b"old\xffbody")
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: False)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert calls == [] and result["не_успели"] == 3 and result["собрано"] == 0
    _all_index(folder)
    _account(result, counts, paths, capsys)


@pytest.mark.parametrize("branch", ["limit", "model", "locked", "backup", "dry"])
def test_every_exit_reuses_loaded_dossier(night, monkeypatch, capsys, branch):
    nd, graph, folder, paths, calls = night
    before = _damage(paths["Бета"], "старое тело", b"old\xffbody")
    if branch == "model":
        monkeypatch.setattr(nd, "generate", lambda *a, **k: "ответ не по формату")
    elif branch == "locked":
        monkeypatch.setattr(nd, "_graph_lock", lambda *a: contextlib.nullcontext(False))
    elif branch == "backup":
        def no_copy(*a, **kw):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(dossier.shutil, "copy2", no_copy)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
    assert result["собрано"] == (3 if branch == "dry" else 0)
    assert result["отказы"] == (3 if branch in ("model", "backup") else 0)
    if branch != "dry":
        entries = _all_index(folder)
        assert entries["Бета"]["отпечаток"] == "старый", "нужен отпечаток прежнего досье"
        assert entries["Бета"]["собрано"] == "2026-07-20"
    else:
        assert not (folder / dossier.INDEX_JSON).exists() and calls == []
    _account(result, counts, paths, capsys)
    assert paths["Бета"].read_bytes() == before


@pytest.mark.parametrize("kind", ["empty", "no_heading", "literal_replacement", "crlf"])
def test_strict_utf8_never_refuses_author_text(night, monkeypatch, capsys, kind):
    nd, graph, folder, paths, calls = night
    path = paths["Бета"]
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "no_heading":
        path.write_bytes(b"# old dossier without a manual section\n")
    elif kind == "literal_replacement":
        path.write_text("## Правки автора\n\nавтор написал \ufffd\n", encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    assert result["собрано"] == 3 and result["отказы"] == 0
    _all_index(folder)
    _account(result, counts, paths, capsys, damaged=0)
    if kind == "literal_replacement":
        assert "автор написал \ufffd" in path.read_text(encoding="utf-8")
    elif kind == "crlf":
        assert "оставить решение автора" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("branch", ["limit", "model", "locked", "late", "build"])
def test_missing_dossier_is_only_indexed_after_build(night, monkeypatch, capsys, branch):
    nd, graph, folder, paths, calls = night
    paths["Бета"].unlink()
    if branch == "model":
        monkeypatch.setattr(nd, "generate", lambda *a, **k: "ответ не по формату")
    elif branch == "locked":
        monkeypatch.setattr(nd, "_graph_lock", lambda *a: contextlib.nullcontext(False))
    elif branch == "late":
        monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: False)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=0 if branch == "limit" else 12)
    assert {e["тема"] for e in dossier.load_index(folder)} == (
        set(THEMES) if branch == "build" else {"Альфа", "Гамма"})
    assert paths["Бета"].exists() == (branch == "build")
    _account(result, counts, paths, capsys, damaged=0)


def test_full_rebuilds_matching_damaged_dossier(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    files, links = dossier.scan(graph, schema=CHAROITE)
    clusters = dossier.clusters(files, links, schema=CHAROITE)
    for theme, path in paths.items():
        fp = dossier.fingerprint(clusters[theme], files)
        path.write_text(path.read_text(encoding="utf-8").replace("старый", fp), encoding="utf-8")
    before = _damage(paths["Бета"], "старое тело", b"old\xffbody")
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=True, dry=False, limit=0)
    assert result["собрано"] == 3 and [c[0] for c in calls] == list(THEMES)
    _account(result, counts, paths, capsys)
    assert next((folder / ".backup").glob("*/Бета.md")).read_bytes() == before


def test_main_reports_damage_without_model_failure_exit(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    for path in paths.values():
        _damage(path, "решение", b"re\xffsolution")
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph)])
    monkeypatch.setattr(nd, "cfg", lambda: {})
    counts = _watch(monkeypatch, paths)
    assert nd.main() == 0, "три повреждения не означают, что модель молчит"
    assert calls == []
    assert counts == Counter({p: 1 for p in paths.values()})
    _all_index(folder)
    log = capsys.readouterr().out
    assert "битые: 3" in log and "отказов модели:" not in log
    assert all(sum(str(p) in line for line in log.splitlines()) == 1 for p in paths.values())


def test_graph_with_nodes_but_no_themes_has_damage_count(tmp_path):
    (tmp_path / "note.md").write_text("# одна заметка\n", encoding="utf-8")
    result = _script().run(tmp_path, {}, full=False, dry=False, limit=12)
    assert result["тем"] == 0 and result["битые"] == 0


@pytest.mark.parametrize("kind,state,text", [
    ("missing", "missing", None), ("empty", "ok", ""),
    ("valid_replacement", "ok", "\ufffd"),
    ("tail", "damaged", "x" * 700 + "\ufffd"),
    ("unreadable", "unreadable", None),
])
def test_load_dossier_contract(tmp_path, monkeypatch, kind, state, text):
    path = tmp_path / "Досье.md"
    raw = {"empty": b"", "valid_replacement": "\ufffd".encode(),
           "tail": b"x" * 700 + b"\xff", "unreadable": b"valid"}
    if kind in raw:
        path.write_bytes(raw[kind])
    _watch(monkeypatch, {"one": path}, unreadable=path if kind == "unreadable" else None)
    loaded = dossier.load_dossier(path)
    assert (loaded.state, loaded.text) == (state, text)


def test_path_metadata_readers_use_load_dossier(tmp_path, monkeypatch):
    path = tmp_path / "Досье.md"
    path.write_bytes("отпечаток: old\nсобрано: 20261006\n".encode() + b"x" * 700 + b"\xff")
    counts = _watch(monkeypatch, {"one": path})
    assert dossier.read_fingerprint(path) == "old"
    assert _script()._собрано(path) == "2026-10-06"
    assert counts[path] == 2  # два независимых публичных вызова; run передаёт снимок


def test_closed_window_falls_back_to_each_theme_fingerprint(night, monkeypatch, capsys):
    nd, graph, folder, paths, calls = night
    files, links = dossier.scan(graph, schema=CHAROITE)
    clusters = dossier.clusters(files, links, schema=CHAROITE)
    for path in paths.values():
        path.write_text("# прежнее досье\n## Правки автора\n\n—\n", encoding="utf-8")
    _damage(paths["Бета"], "прежнее", b"old\xfftext")
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: False)
    counts = _watch(monkeypatch, paths)
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
    entries = _all_index(folder)
    assert all(entries[t]["отпечаток"] == dossier.fingerprint(clusters[t], files) for t in THEMES)
    assert all(entries[t]["собрано"] == date.today().isoformat() for t in THEMES)
    _account(result, counts, paths, capsys)
    assert calls == []
