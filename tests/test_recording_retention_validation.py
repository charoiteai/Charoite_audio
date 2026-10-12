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
    assert audio.AudioHub.prune_recordings(tmp_path, days, protect={"2026-10-11_120000"}) == 1
    assert not files[0].exists()
    assert files[1].read_bytes() == b"audio"


def test_nan_from_yaml_does_not_remove_fresh_recording(tmp_path):
    import yaml
    days = yaml.safe_load("audio:\n  record_keep_days: .nan\n")["audio"]["record_keep_days"]
    path = tmp_path / "2026-10-12_120000_mic.wav"
    path.write_bytes(b"fresh recording")
    audio.AudioHub.prune_recordings(tmp_path, days)
    assert path.read_bytes() == b"fresh recording"
