"""Потолок бессрочных append-логов (аудит 16.08, п.7).

daemon.err.log и mlx_server.log дописываются при каждом старте и никогда не
пересоздавались: у долгоживущей установки — гигабайты кусков стенограмм.
Усечение при старте оставляет хвост — именно он нужен для диагноза.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from charoite_paths import LOG_KEEP_BYTES, LOG_MAX_BYTES, trim_log  # noqa: E402


def test_small_log_is_left_alone(tmp_path):
    log = tmp_path / "x.log"
    log.write_bytes(b"line\n" * 10)
    assert trim_log(log, max_bytes=1000, keep_bytes=100) is False
    assert log.read_bytes() == b"line\n" * 10


def test_big_log_keeps_tail_on_line_boundary(tmp_path):
    log = tmp_path / "x.log"
    lines = [f"строка {i:05d}\n".encode("utf-8") for i in range(2000)]
    log.write_bytes(b"".join(lines))
    size = log.stat().st_size
    assert trim_log(log, max_bytes=size // 2, keep_bytes=size // 10) is True
    body = log.read_bytes()
    first, rest = body.split(b"\n", 1)
    assert first.decode("utf-8").startswith("[лог усечён при старте: было")
    assert rest.startswith(b"\xd1\x81\xd1\x82\xd1\x80\xd0\xbe\xd0\xba\xd0\xb0 ")  # «строка …», не обрывок
    assert rest.endswith(lines[-1])
    assert len(body) <= size // 10 + 80


def test_missing_log_is_not_an_error(tmp_path):
    assert trim_log(tmp_path / "нет.log") is False


def test_swift_mirror_is_wired_before_daemon_log_opens():
    """Swift-зеркало стоит там, где открывается daemon.err.log: без него
    потолок был бы только у mlx_server.log.

"""
    svc = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services"
           / "SuflerService.swift").read_text(encoding="utf-8")
    assert svc.index("LogTrim.trim(errURL)") < svc.index("FileHandle(forWritingTo: errURL)")


def test_mlx_log_is_trimmed_before_open_and_left_owner_only(tmp_path, monkeypatch):
    """Лог сервера моделей: потолок — до открытия, права — только владельцу, в том
    числе у файла и каталога, созданных до маски (0644/0755): режим ставится при
    открытии, а не маской процесса (Important DeepSeek, круг 2 по PR #634).
    Поведением, а не по тексту исходника — прежняя проверка искала строки
    вызовов (долг №329 круга 1 по коду)."""
    import os
    import stat

    import llm_health
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "mlx_server.log"
    log.write_bytes(b"x" * (LOG_MAX_BYTES + 10))
    log.chmod(0o644)
    logs.chmod(0o755)
    seen = {}

    class _Popen:
        def __init__(self, argv, stdout=None, **kw):
            seen["size"] = log.stat().st_size
            seen["mode"] = stat.S_IMODE(os.fstat(stdout.fileno()).st_mode)

    monkeypatch.setattr(llm_health, "_root", lambda: tmp_path)
    monkeypatch.setattr(llm_health, "_spare", lambda cfg, log, *, force: False)
    monkeypatch.setattr(llm_health, "_mlx_listener_pid", lambda url: None)
    monkeypatch.setattr(llm_health.privacy, "mlx_base_url", lambda cfg: "http://127.0.0.1:8080")
    monkeypatch.setattr(llm_health.subprocess, "Popen", _Popen)
    assert llm_health._restart_mlx({}, lambda message: None) is True
    assert seen["size"] <= LOG_KEEP_BYTES + 200, "потолок — до открытия лога"
    assert seen["mode"] == 0o600, "сервер пишет в лог, открытый только владельцу"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700


def test_trim_keeps_owner_only_permissions_and_leaves_no_temp(tmp_path):
    """Усечение идёт через временный файл: права итогового файла — 0600
    (явно в os.open, не по umask), хвоста .trim после успеха не остаётся
    (круг-1 по PR #377, qwen)."""
    log = tmp_path / "x.log"
    log.write_bytes(b"line\n" * 5000)
    size = log.stat().st_size
    assert trim_log(log, max_bytes=size // 2, keep_bytes=size // 10)
    assert (log.stat().st_mode & 0o777) == 0o600
    assert not (tmp_path / "x.log.trim").exists()
