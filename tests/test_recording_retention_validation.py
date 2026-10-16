"""Invalid retention settings must neither erase recordings nor stop a meeting."""
import os
import time

import pytest

import audio
import charoite_paths


@pytest.mark.parametrize("days", [float("nan"), float("inf"), -float("inf"),
                                  -1, "nan", "-inf", None, "invalid", True, False,
                                  10 ** 400, 1e308])
@pytest.mark.parametrize("kind", ["recording", "stream", "log"])
def test_invalid_retention_keeps_files_and_reports_refusal(tmp_path, capsys, days, kind):
    charoite_paths.use_data_root(tmp_path, replace=True)
    if kind == "recording":
        folder = tmp_path / "recordings"
        path = folder / "2026-10-12_120000_mic.pcm"
    elif kind == "stream":
        folder = tmp_path / "data"
        path = folder / "sck" / "session" / "mic.raw"
    else:
        folder = tmp_path / "logs"
        path = folder / "graph_2026-10-12_120000.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"keep this recording or diagnostic")
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    before = path.stat()
    if kind == "recording":
        assert audio.AudioHub.prune_recordings(folder, days) == 0
    elif kind == "stream":
        assert audio.AudioHub.prune_stream_files(folder, days) == 0
    else:
        import daemon
        daemon._prune_graph_logs({"audio": {"record_keep_days": days}})
    assert path.read_bytes() == b"keep this recording or diagnostic"
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    output = capsys.readouterr()
    assert "record_keep_days" in output.out + output.err


@pytest.mark.parametrize("days", [0, 0.5, 2, "2"])
def test_valid_retention_expires_old_audio_and_preserves_protected(tmp_path, days):
    names = ["2026-10-10_120000_mic.pcm", "2026-10-11_120000_mic.pcm"]
    files = [tmp_path / name for name in names]
    for path in files:
        path.write_bytes(b"audio")
        old = time.time() - 3 * 86400
        os.utime(path, (old, old))
    fresh = tmp_path / "2026-10-12_120000_mic.pcm"
    fresh.write_bytes(b"fresh")
    if days:
        # Срок не ноль: запись младше срока обязана выжить — иначе тест прошла
        # бы и уборка «удалить всё непротектированное».
        assert audio.AudioHub.prune_recordings(tmp_path, days, protect={"2026-10-11_120000"}) == 1
        assert fresh.read_bytes() == b"fresh"
    else:
        audio.AudioHub.prune_recordings(tmp_path, days, protect={"2026-10-11_120000"})
    assert not files[0].exists()
    assert files[1].read_bytes() == b"audio"


@pytest.mark.parametrize("days", [0.5, 2, "2"])
def test_valid_retention_expires_old_streams_and_keeps_fresh(tmp_path, monkeypatch, days):
    monkeypatch.setattr(audio, "fresh_sck_manifest", lambda: None)
    old_session = tmp_path / "sck" / "old"
    new_session = tmp_path / "sck" / "new"
    for session, age in ((old_session, 3 * 86400), (new_session, 0)):
        session.mkdir(parents=True)
        raw = session / "mic.raw"
        raw.write_bytes(b"stream")
        then = time.time() - age
        os.utime(raw, (then, then))
    assert audio.AudioHub.prune_stream_files(tmp_path, days) == 1
    assert not (old_session / "mic.raw").exists()
    assert (new_session / "mic.raw").read_bytes() == b"stream"


@pytest.mark.parametrize("days", [False, True, None, "invalid", float("nan"), -1])
def test_refused_retention_is_a_sticky_error_for_the_app(days):
    # YAML `off` — это False: на main он стирал все записи сразу, теперь
    # уборка пропущена, и об этом знает владелец, а не только stderr.
    import stt_runtime
    status = audio.keep_days_refusal(days)
    assert status is not None
    event = stt_runtime.status_event(status)
    assert event["error"] is True and event["sticky"] is True
    assert event["topic"] == stt_runtime.TOPIC_RETENTION
    assert "record_keep_days" in event["text"]


@pytest.mark.parametrize("days", [0, 0.5, 2, "2"])
def test_valid_retention_has_no_refusal(days):
    assert audio.keep_days_refusal(days) is None


def test_nan_from_yaml_does_not_remove_fresh_recording(tmp_path):
    import yaml
    days = yaml.safe_load("audio:\n  record_keep_days: .nan\n")["audio"]["record_keep_days"]
    path = tmp_path / "2026-10-12_120000_mic.wav"
    path.write_bytes(b"fresh recording")
    audio.AudioHub.prune_recordings(tmp_path, days)
    assert path.read_bytes() == b"fresh recording"


@pytest.mark.parametrize("cfg", [{}, {"audio": {}}])
def test_missing_retention_keeps_logs_for_two_days(tmp_path, monkeypatch, cfg):
    import daemon
    charoite_paths.use_data_root(tmp_path, replace=True)
    now = 1_800_000_000.0
    monkeypatch.setattr(daemon.time, "time", lambda: now)
    folder = tmp_path / "logs"
    folder.mkdir()
    recent = folder / "graph_2026-10-10_120000.log"
    expired = folder / "graph_2026-10-09_120000.log"
    for path, age in ((recent, 1.5), (expired, 2.5)):
        path.write_bytes(b"meeting diagnostic")
        then = now - age * 86400
        os.utime(path, (then, then))
    daemon._prune_graph_logs(cfg)
    assert recent.read_bytes() == b"meeting diagnostic"
    assert not expired.exists()
