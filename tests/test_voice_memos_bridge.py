"""Мост Диктофон → папка импорта: копия, не перенос; порог длительности;
покой синка; журнал; тёзки; скрытый .part и атомарная публикация."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import voice_memos_bridge as vm


def _setup(tmp_path, monkeypatch, durations: dict[str, float | None]):
    src = tmp_path / "Recordings"
    inbox = tmp_path / "Inbox"
    root = tmp_path / "data"
    for d in (src, inbox, root):
        d.mkdir()
    monkeypatch.setattr(vm, "duration_seconds", lambda p: durations.get(p.name))
    cfg = {"audio": {"voice_memos_dir": str(src)}}
    return src, inbox, root, cfg


def test_copies_settled_long_recordings_and_leaves_the_source_alone(tmp_path, monkeypatch):
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"Новая запись 4.m4a": 600.0, "Заметка.m4a": 40.0})
    long = src / "Новая запись 4.m4a"
    long.write_bytes(b"m4a" * 1000)
    (src / "Заметка.m4a").write_bytes(b"m4a" * 10)
    (src / ".hidden.m4a").write_bytes(b"x")
    (src / "notes.txt").write_text("не аудио", encoding="utf-8")
    now = long.stat().st_mtime + 3600

    out = vm.bridge(cfg, inbox, root, now=now)

    assert out["copied"] == ["Новая запись 4.m4a"] and out["short"] == 1
    copy = inbox / "Новая запись 4.m4a"
    assert copy.read_bytes() == long.read_bytes()
    assert long.exists(), "исходник Диктофона не трогаем"
    assert not list(inbox.glob(".*.part")), "временный .part не остался"
    state = json.loads((root / "logs" / vm.STATE_NAME).read_text(encoding="utf-8"))
    assert any(v.get("copied_to") == "Новая запись 4.m4a" for v in state.values())
    assert any(v.get("skipped") == "short" for v in state.values())
    # второй прогон — ничего нового, копия не дублируется
    again = vm.bridge(cfg, inbox, root, now=now + 10)
    assert again["copied"] == [] and again["seen"] == 2
    assert copy.stat().st_size == long.stat().st_size


def test_fresh_files_wait_for_the_sync_to_settle(tmp_path, monkeypatch):
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"a.m4a": 300.0})
    f = src / "a.m4a"
    f.write_bytes(b"x" * 100)
    out = vm.bridge(cfg, inbox, root, now=f.stat().st_mtime + 5)
    assert out["copied"] == [] and out["fresh"] == 1
    assert vm.bridge(cfg, inbox, root, now=f.stat().st_mtime + vm.SETTLE_SECONDS + 1)["copied"] == ["a.m4a"]


def test_unknown_duration_is_copied_and_namesakes_get_a_stamp(tmp_path, monkeypatch):
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"Новая запись 4.m4a": None})
    f = src / "Новая запись 4.m4a"
    f.write_bytes(b"y" * 500)
    # в папке импорта уже есть тёзка другого размера (прошлый день) — копия со штампом
    (inbox / "done").mkdir()
    (inbox / "done" / "Новая запись 4.m4a").write_bytes(b"z" * 5)
    out = vm.bridge(cfg, inbox, root, now=f.stat().st_mtime + 3600)
    assert len(out["copied"]) == 1 and out["copied"][0].startswith("Новая запись 4_20") and out["copied"][0].endswith(".m4a")
    # тёзка ТОГО ЖЕ размера (уже импортирован, копия удалена ретеншном, сайдкар жив) — не копируем
    g = src / "Ещё.m4a"
    g.write_bytes(b"w" * 77)
    (inbox / "done" / ".Ещё.m4a.imported.json").write_text(json.dumps({"size": 77}), encoding="utf-8")
    out2 = vm.bridge(cfg, inbox, root, now=g.stat().st_mtime + 3600)
    assert out2["copied"] == [] and out2["seen"] >= 1


def test_switches_off_by_config_and_without_the_folder(tmp_path, monkeypatch):
    src, inbox, root, _ = _setup(tmp_path, monkeypatch, {"a.m4a": 300.0})
    (src / "a.m4a").write_bytes(b"x" * 10)
    off = {"audio": {"voice_memos_dir": str(src), "voice_memos_bridge": False}}
    assert vm.bridge(off, inbox, root, now=10 ** 10) == {"copied": [], "short": 0, "fresh": 0, "seen": 0, "source": None}
    missing = {"audio": {"voice_memos_dir": str(tmp_path / "нет такой")}}
    assert vm.bridge(missing, inbox, root, now=10 ** 10)["source"] is None
    assert vm.describe(vm.bridge(missing, inbox, root, now=10 ** 10)) == ""
    assert vm.min_seconds({"audio": {"voice_memos_min_seconds": "abc"}}) == vm.DEFAULT_MIN_SECONDS
