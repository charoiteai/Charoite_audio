"""post_meeting_hook: запускается с env, пустой конфиг — тишина."""
import os
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


def test_hook_does_not_get_the_api_key(tmp_path, monkeypatch):
    """Ключ Anthropic до хука не доходит — проверяем в самом дочернем процессе.

    Не текстом функции, а окружением запущенной команды: хук — это чужой
    скрипт из конфига, и всё, что он запустит дальше, унаследует ключ.
    Инвариант «облако только через подписку» держится именно здесь.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-не-должен-утечь")
    monkeypatch.setenv("SUFLER_HOOK_CANARY", "виден")
    out = tmp_path / "env.txt"
    cfg = {"sufler": {"post_meeting_hook":
                      f'echo "[${{ANTHROPIC_API_KEY}}][${{SUFLER_HOOK_CANARY}}]" > "{out}"'}}
    graph_updater.run_post_hook(cfg, tmp_path / "t.txt", "s")

    got = out.read_text().strip()
    assert got == "[][виден]", (
        f"хук получил окружение {got}: ключ Anthropic ушёл произвольной команде "
        f"из конфига — вызовы оттуда пойдут на потокенный биллинг")
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-не-должен-утечь", \
        "фильтр правит само окружение процесса вместо копии"
