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

from charoite_paths import trim_log  # noqa: E402


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
    потолок был бы только у mlx_server.log."""
    svc = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services"
           / "SuflerService.swift").read_text(encoding="utf-8")
    assert svc.index("LogTrim.trim(errURL)") < svc.index("FileHandle(forWritingTo: errURL)")
    health = (ROOT / "src" / "llm_health.py").read_text(encoding="utf-8")
    assert health.index('trim_log(ROOT / "logs" / "mlx_server.log")') \
        < health.index('(ROOT / "logs" / "mlx_server.log").open("a")')
