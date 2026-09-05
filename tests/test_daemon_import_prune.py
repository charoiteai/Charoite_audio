"""Ретеншн копий импорта из демона (№170): папка — из config.yaml или из
настроек приложения, уборка — тем же скриптом, что зовёт приложение, в
фоне и с потолком времени."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import daemon  # noqa: E402


class _Done:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_import_folder_prefers_config_and_requires_an_existing_dir(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _Done(0, "/nowhere\n"))[1])
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    assert daemon._import_folder({"audio": {"import_dir": str(inbox)}}) == inbox
    assert calls == [], "при ключе в конфиге настройки приложения не читаются"
    assert daemon._import_folder({"audio": {"import_dir": str(tmp_path / "нет")}}) is None


def test_import_folder_falls_back_to_the_apps_setting(tmp_path, monkeypatch):
    inbox = tmp_path / "Charoite Inbox"
    inbox.mkdir()
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        assert cmd[:2] == ["defaults", "read"] and cmd[2] == daemon.IMPORT_DEFAULTS_DOMAIN
        return _Done(0, str(inbox) + "\n")
    monkeypatch.setattr(daemon.subprocess, "run", run)
    assert daemon._import_folder({"audio": {}}) == inbox
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, **kw: _Done(1, "", "does not exist"))
    assert daemon._import_folder({}) is None


def test_prune_runs_the_import_script_in_the_background_and_reports_only_removals(tmp_path, monkeypatch):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    calls, events = [], []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return _Done(0, "ретеншн импорта: удалено копий — 2, аудио-исходников в архиве — 1\n")
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setattr(daemon, "emit", lambda obj: events.append(obj))
    t = daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}})
    assert t is not None
    t.join(timeout=10)
    cmd, kw = calls[0]
    assert cmd[1].endswith("scripts/import_meeting.py") and cmd[2:] == ["--prune", str(inbox)]
    assert kw["timeout"] == daemon.IMPORT_PRUNE_TIMEOUT and kw["stdin"] is subprocess.DEVNULL
    assert events and "удалено копий — 2" in events[0]["text"]

    # ничего не удалено — тишина; нет папки — ни процесса, ни потока
    events.clear()
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, **kw: _Done(0, "ретеншн импорта: удалено копий — 0\n"))
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert events == []
    assert daemon._prune_import_folder({"audio": {"import_dir": str(tmp_path / "нет")}}) is None


def test_prune_failures_stay_in_stderr(tmp_path, monkeypatch, capsys):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    monkeypatch.setattr(daemon, "emit", lambda obj: (_ for _ in ()).throw(AssertionError("emit при ошибке")))
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, **kw: _Done(1, "", "boom"))
    daemon._prune_import_folder({"audio": {"import_dir": str(inbox)}}).join(timeout=10)
    assert "ретеншн импорта: код 1" in capsys.readouterr().err


def test_meeting_start_cleanup_prunes_the_import_folder():
    """Вызов стоит в блоке уборки при старте встречи, рядом с ретеншном записей."""
    src = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    block = src[src.index("AudioHub.prune_recordings("):src.index("system_base = llm.system")]
    assert "_prune_import_folder(cfg)" in block
