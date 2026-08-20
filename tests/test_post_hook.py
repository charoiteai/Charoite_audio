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


def test_hook_absent_is_noop(tmp_path, monkeypatch):
    """Хук не задан — процесс не запускается вовсе.

    Раньше тест просто звал функцию и ничего не проверял: он был зелёным и
    при `subprocess.run("")`, то есть не держал ни одного обещания.
    """
    started: list = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: started.append(a))

    graph_updater.run_post_hook({"sufler": {}}, tmp_path / "t.txt", "s")

    assert not started, "без команды в конфиге запускать нечего"


def test_hook_failure_does_not_raise(tmp_path, monkeypatch):
    """Сбой хука не валит конвейер — встреча к этому моменту уже обработана.

    Через настоящий сбой запуска, а не через `exit 7`: ненулевой код без
    `check=True` исключения не бросает, и прежний тест оставался зелёным,
    даже если убрать весь try/except (ревью 20.08, GLM).
    """
    def boom(*a, **k):
        raise RuntimeError("хук упал посреди работы")

    monkeypatch.setattr("subprocess.run", boom)
    cfg = {"sufler": {"post_meeting_hook": "break-things"}}

    assert graph_updater.run_post_hook(cfg, tmp_path / "t.txt", "s") is None


def test_hook_timeout_does_not_hang_the_pipeline(tmp_path, monkeypatch):
    """Зависший хук обязан быть прибит по таймауту, а не держать конвейер."""
    import subprocess as _sp

    seen: dict = {}

    def boom(*a, **k):
        # Проверять ВНУТРИ подменённой функции нельзя: AssertionError уйдёт в
        # `except Exception` проверяемого кода и растворится — тест останется
        # зелёным при любом дефекте. Запоминаем и судим снаружи
        # (ревью 20.08, GLM: Critical в этом самом тесте).
        seen.update(k)
        raise _sp.TimeoutExpired(cmd="hook", timeout=k.get("timeout") or 1)

    monkeypatch.setattr("subprocess.run", boom)
    cfg = {"sufler": {"post_meeting_hook": "sleep 99999"}}

    assert graph_updater.run_post_hook(cfg, tmp_path / "t.txt", "s") is None
    assert seen.get("timeout"), "хук без таймаута повесил бы конвейер навсегда"


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
