"""post_meeting_hook: запускается с env, пустой конфиг — тишина."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_updater  # noqa: E402


def test_hook_runs_with_env(tmp_path):
    out = tmp_path / "hook.log"
    cfg = {"sufler": {"post_meeting_hook": f'echo "$SUFLER_STAMP" > "{out}"'}}
    graph_updater.run_post_hook(cfg, tmp_path / "t.txt", "2026-07-27_0900")
    assert out.read_text().strip() == "2026-07-27_0900"


def test_hook_absent_is_noop(tmp_path):
    graph_updater.run_post_hook({"sufler": {}}, tmp_path / "t.txt", "s")


def test_hook_failure_does_not_raise(tmp_path):
    cfg = {"sufler": {"post_meeting_hook": "exit 7"}}
    graph_updater.run_post_hook(cfg, tmp_path / "t.txt", "s")
