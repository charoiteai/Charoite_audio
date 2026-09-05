"""Ретеншн копий импорта из демона (№170): папки — из config.yaml и из
настроек приложения (обе), уборка — тем же скриптом, что зовёт приложение,
в фоне, вывод ребёнка в файл, итог по машинному маркеру."""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import daemon  # noqa: E402


class _Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _defaults(path):
    """`defaults read` отвечает путём; всё остальное — не сюда."""
    def run(cmd, **kw):
        assert cmd[:2] == ["defaults", "read"] and cmd[2] == daemon.IMPORT_DEFAULTS_DOMAIN, cmd
        return _Done(0, path + "\n") if path else _Done(1, "", "does not exist")
    return run


def test_import_folders_takes_both_config_and_the_apps_choice(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cli-inbox"
    app_dir = tmp_path / "Charoite Inbox"
    cfg_dir.mkdir()
    app_dir.mkdir()
    monkeypatch.setattr(daemon.subprocess, "run", _defaults(str(app_dir)))
    assert daemon._import_folders({"audio": {"import_dir": str(cfg_dir)}}) == [cfg_dir, app_dir]
    # одна и та же папка — один раз
    assert daemon._import_folders({"audio": {"import_dir": str(app_dir)}}) == [app_dir]
    # без ключа — только выбор приложения; без него — пусто
    assert daemon._import_folders({"audio": {}}) == [app_dir]
    monkeypatch.setattr(daemon.subprocess, "run", _defaults(""))
    assert daemon._import_folders({}) == []


def test_dead_config_path_does_not_silence_the_apps_folder(tmp_path, monkeypatch, capsys):
    """Important GLM r1: протухший audio.import_dir раньше возвращал None до
    фолбэка — живая папка приложения не чистилась, и никто об этом не знал."""
    app_dir = tmp_path / "Charoite Inbox"
    app_dir.mkdir()
    monkeypatch.setattr(daemon.subprocess, "run", _defaults(str(app_dir)))
    assert daemon._import_folders({"audio": {"import_dir": str(tmp_path / "old")}}) == [app_dir]
    assert "audio.import_dir" in capsys.readouterr().err


def test_prune_runs_the_import_script_with_output_in_a_file_and_reports_only_removals(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    monkeypatch.setattr(daemon, "ROOT", tmp_path / "root")
    calls, events = [], []

    def run(cmd, **kw):
        if cmd[:2] == ["defaults", "read"]:
            return _Done(1, "", "does not exist")
        calls.append((cmd, kw))
        kw["stdout"].write("ретеншн импорта: удалено копий — 2, аудио-исходников в архиве — 1\n"
                           "prune=copies:2,archive:1,temporaries:0\n")
        return _Done(0)
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setattr(daemon, "emit", lambda obj: events.append(obj))
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    cmd, kw = calls[0]
    assert cmd[1].endswith("scripts/import_meeting.py") and cmd[2:] == ["--prune", str(inbox)]
    assert kw["timeout"] == daemon.IMPORT_PRUNE_TIMEOUT and kw["stdin"] is subprocess.DEVNULL
    assert kw["stderr"] is subprocess.STDOUT and hasattr(kw["stdout"], "write"), "вывод ребёнка — в файл, не в пайп"
    assert list((tmp_path / "root" / "logs").glob("import_prune-Inbox.log")), "лог — на папку"
    assert events and "удалено копий — 2" in events[0]["text"] and "в архиве — 1" in events[0]["text"]

    # ничего не удалено — тишина
    events.clear()

    def quiet(cmd, **kw):
        if cmd[:2] == ["defaults", "read"]:
            return _Done(1, "", "")
        kw["stdout"].write("ретеншн импорта: удалено копий — 0\nprune=copies:0,archive:0,temporaries:0\n")
        return _Done(0)
    monkeypatch.setattr(daemon.subprocess, "run", quiet)
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert events == []


def test_prune_failures_stay_in_stderr(tmp_path, monkeypatch, capsys):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    monkeypatch.setattr(daemon, "ROOT", tmp_path / "root")
    monkeypatch.setattr(daemon, "emit", lambda obj: (_ for _ in ()).throw(AssertionError("emit при ошибке")))

    def run(cmd, **kw):
        if cmd[:2] == ["defaults", "read"]:
            return _Done(1, "", "")
        kw["stdout"].write("boom\n")
        return _Done(1)
    monkeypatch.setattr(daemon.subprocess, "run", run)
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert f"ретеншн импорта ({inbox}): код 1" in capsys.readouterr().err


def test_prune_without_the_marker_is_reported_not_silenced(tmp_path, monkeypatch, capsys):
    """Minor GLM r2: дрейф формата маркера в ребёнке — строка в stderr, а не
    «ничего не удалено»; брошенные временные тоже попадают в статус."""
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    monkeypatch.setattr(daemon, "ROOT", tmp_path / "root")
    events = []
    monkeypatch.setattr(daemon, "emit", lambda obj: events.append(obj))

    def no_marker(cmd, **kw):
        if cmd[:2] == ["defaults", "read"]:
            return _Done(1, "", "")
        kw["stdout"].write("ретеншн импорта: удалено копий — 3\n")
        return _Done(0)
    monkeypatch.setattr(daemon.subprocess, "run", no_marker)
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert events == [] and "маркера итога нет" in capsys.readouterr().err

    def temporaries(cmd, **kw):
        if cmd[:2] == ["defaults", "read"]:
            return _Done(1, "", "")
        kw["stdout"].write("prune=copies:0,archive:0,temporaries:2\n")
        return _Done(0)
    monkeypatch.setattr(daemon.subprocess, "run", temporaries)
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert events and "брошенных временных файлов — 2" in events[0]["text"]


def test_meeting_start_cleanup_prunes_the_import_folder():
    """Вызов стоит в блоке уборки при старте встречи, рядом с ретеншном записей."""
    src = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    block = src[src.index("AudioHub.prune_recordings("):src.index("system_base = llm.system")]
    assert "_prune_import_folder(cfg)" in block


def test_prune_cli_prints_the_machine_marker(tmp_path, monkeypatch, capsys):
    """Демон читает итог по маркеру `prune=…`, а не по прозе."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import import_meeting as im

    (tmp_path / "done").mkdir()
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {"import_keep_days": 2}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", "--prune", str(tmp_path)])
    before = os.umask(0o022)   # main() ужесточает umask процесса — вернуть, иначе соседние тесты видят чужие права
    os.umask(before)
    try:
        im.main()
    finally:
        os.umask(before)
    assert "prune=copies:0,archive:0,temporaries:0" in capsys.readouterr().out
