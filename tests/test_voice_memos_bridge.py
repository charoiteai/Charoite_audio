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
    cfg = {"audio": {"voice_memos_dir": str(src), "voice_memos_bridge": True}}
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
    assert vm.bridge(off, inbox, root, now=10 ** 10)["source"] is None
    by_default = {"audio": {"voice_memos_dir": str(src)}}
    assert vm.bridge(by_default, inbox, root, now=10 ** 10)["source"] is None, "мост включается явно"
    missing = {"audio": {"voice_memos_dir": str(tmp_path / "нет такой"), "voice_memos_bridge": True}}
    assert vm.bridge(missing, inbox, root, now=10 ** 10)["source"] is None
    assert vm.describe(vm.bridge(missing, inbox, root, now=10 ** 10)) == ""
    assert vm.min_seconds({"audio": {"voice_memos_min_seconds": "abc"}}) == vm.DEFAULT_MIN_SECONDS


def test_tcc_denied_folder_gives_one_hint_and_no_spam(tmp_path, monkeypatch):
    """Контейнер Диктофона без полного доступа к диску: stat проходит, листинг —
    «Operation not permitted». Мост подсказывает шаг настройки раз в 6 часов
    и не считает это сбоем."""
    _, inbox, root, cfg = _setup(tmp_path, monkeypatch, {})

    def denied(self):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(pathlib.Path, "iterdir", denied)
    t0 = 10 ** 9
    first = vm.bridge(cfg, inbox, root, now=t0)
    assert first["denied"] is True and "errors" not in first
    assert "Полный доступ к диску" in vm.describe(first)
    second = vm.bridge(cfg, inbox, root, now=t0 + 60)
    assert second["denied"] is False and vm.describe(second) == ""
    later = vm.bridge(cfg, inbox, root, now=t0 + vm.DENIED_HINT_SECONDS)
    assert later["denied"] is True


def test_archive_beyond_the_age_window_is_never_touched(tmp_path, monkeypatch):
    """Гейт возраста: записи старше voice_memos_max_age_days — архив Диктофона.
    Ни копии, ни afinfo, ни записи в журнале — потеря журнала после ретеншна
    done/ не вернёт их в конвейер (DS I2)."""
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {})
    calls = []
    monkeypatch.setattr(vm, "duration_seconds", lambda p: calls.append(p.name) or 900.0)
    import os
    old = src / "Прошлогодняя.m4a"
    old.write_bytes(b"o" * 50)
    now = old.stat().st_mtime + 20 * 86400
    os.utime(old, (now - 20 * 86400, now - 20 * 86400))
    recent = src / "Вчерашняя.m4a"
    recent.write_bytes(b"r" * 50)
    os.utime(recent, (now - 86400, now - 86400))
    out = vm.bridge(cfg, inbox, root, now=now)
    assert out["copied"] == ["Вчерашняя.m4a"] and out["old"] == 1 and calls == ["Вчерашняя.m4a"]
    assert not (inbox / "Прошлогодняя.m4a").exists()


def test_per_scan_cap_takes_the_newest_and_queues_the_rest(tmp_path, monkeypatch):
    import os
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(vm, "duration_seconds", lambda p: 600.0)
    base = 10 ** 9
    for i in range(5):
        f = src / f"Запись {i}.m4a"
        f.write_bytes(b"x" * (10 + i))
        os.utime(f, (base + i * 100, base + i * 100))
    out = vm.bridge(cfg, inbox, root, now=base + 3600)
    assert out["copied"] == ["Запись 4.m4a", "Запись 3.m4a", "Запись 2.m4a"] and out["queued"] == 2
    again = vm.bridge(cfg, inbox, root, now=base + 3700)
    assert again["copied"] == ["Запись 1.m4a", "Запись 0.m4a"] and again["queued"] == 0


def test_replacing_a_failed_namesake_drops_its_error_marker(tmp_path, monkeypatch):
    """Сбойная тёзка в корне папки имя не держит; новая копия встаёт на её
    место, а метка ошибки снимается — иначе скан не взял бы и новую."""
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"Новая запись 4.m4a": 600.0})
    f = src / "Новая запись 4.m4a"
    f.write_bytes(b"full" * 100)
    (inbox / "Новая запись 4.m4a").write_bytes(b"cut")
    marker = inbox / f".Новая запись 4.m4a{vm.ERROR_MARKER_SUFFIX}"
    marker.write_text("транскрибация не удалась", encoding="utf-8")
    out = vm.bridge(cfg, inbox, root, now=f.stat().st_mtime + 3600)
    assert out["copied"] == ["Новая запись 4.m4a"]
    assert (inbox / "Новая запись 4.m4a").read_bytes() == f.read_bytes() and not marker.exists()


def test_symlink_namesake_in_inbox_does_not_count_as_imported(tmp_path, monkeypatch):
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"a.m4a": 600.0})
    f = src / "a.m4a"
    f.write_bytes(b"x" * 64)
    elsewhere = tmp_path / "elsewhere.m4a"
    elsewhere.write_bytes(b"y" * 64)
    (inbox / "a.m4a").symlink_to(elsewhere)
    out = vm.bridge(cfg, inbox, root, now=f.stat().st_mtime + 3600)
    assert out["copied"] == ["a.m4a"]
    assert not (inbox / "a.m4a").is_symlink() and (inbox / "a.m4a").read_bytes() == f.read_bytes()


def test_unparsed_young_file_waits_and_dataless_placeholder_is_skipped(tmp_path, monkeypatch):
    """Диктофон пишет moov в конец: afinfo не разобрал молодой файл — ждём час,
    старше часа — копируем (конвейер сам скажет, что с ним). Выселенный iCloud
    файл (блоков нет) — ждём докачки, copyfile его не тянет."""
    import os
    src, inbox, root, cfg = _setup(tmp_path, monkeypatch, {"a.m4a": None})
    f = src / "a.m4a"
    f.write_bytes(b"x" * 64)
    t0 = f.stat().st_mtime
    assert vm.bridge(cfg, inbox, root, now=t0 + 600)["fresh"] == 1
    assert vm.bridge(cfg, inbox, root, now=t0 + vm.UNPARSED_GRACE_SECONDS + 1)["copied"] == ["a.m4a"]
    real = os.stat(f)
    assert not vm.is_dataless(real)
    dataless = os.stat_result((real.st_mode, real.st_ino, real.st_dev, real.st_nlink, real.st_uid, real.st_gid,
                               real.st_size, real.st_atime, real.st_mtime, real.st_ctime), {"st_blocks": 0})
    assert vm.is_dataless(dataless)


def test_journal_drops_month_old_entries_and_leaves_no_temp(tmp_path):
    root = tmp_path / "data"
    now = 10 ** 9
    vm.save_state(root, {"old|1|1": {"copied_to": "x", "at": now - vm.STATE_KEEP_SECONDS - 1},
                         "new|2|2": {"copied_to": "y", "at": now - 100}}, now=now)
    assert set(vm.load_state(root)) == {"new|2|2"}
    assert [p.name for p in (root / "logs").iterdir()] == [vm.STATE_NAME]
