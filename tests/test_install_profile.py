"""Профиль установки: выключатели должны выключать.

До этого «Граф знаний выключен» было фразой в описании пресета, а конвейер
всё равно звал разбор: на лёгкой модели он ломал JSON, ставил встрече статус
ошибки и заказывал повтор. Здесь проверяется, что флаг читается честно (в том
числе строкой из приложения) и что статус готовности живёт без заметки графа.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import install_profile  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402


def test_default_is_everything_on():
    assert install_profile.graph_enabled({"sufler": {"graph_dir": "~/V"}}) is True
    assert install_profile.deja_vu_enabled({"sufler": {"graph": False}}) is True
    assert install_profile.tier3_enabled({}) is True


def test_embedding_loops_have_their_own_switch():
    """Ревизия ядер поднимает bge-m3 так же, как дежавю: без своего
    выключателя обещание «на 8 ГБ эмбеддера нет» ломалось на первой встрече."""
    light = {"sufler": {"deja_vu": "false", "tier3": "false", "graph_dir": "~/V"}}
    assert install_profile.deja_vu_enabled(light) is False
    assert install_profile.tier3_enabled(light) is False
    assert install_profile.graph_enabled(light) is True, "узлы при этом строятся"


def test_graph_dir_not_set_means_no_graph():
    """`graph_dir: ""` — законный режим «только расшифровка». Раньше конвейер
    доходил до конца, не находил заметку (её некуда класть) и ронял ГОТОВУЮ
    встречу в «ошибку обработки», а повтор проходил тот же путь трижды
    (ревью 19.08, второй круг Gemini)."""
    assert install_profile.graph_enabled({"sufler": {"graph_dir": "", "graph": True}}) is False
    assert install_profile.graph_enabled({"sufler": {"graph_dir": "   "}}) is False
    assert install_profile.graph_enabled({"sufler": {"graph_dir": "~/Vault/Work"}}) is True


def test_graph_dir_from_env_counts_too(monkeypatch):
    """Тесты и разовые прогоны задают путь через SUFLER_GRAPH_DIR."""
    monkeypatch.setenv("SUFLER_GRAPH_DIR", "/tmp/graph")
    assert install_profile.graph_enabled({"sufler": {"graph_dir": ""}}) is True


def test_boolean_false_turns_the_graph_off():
    assert install_profile.graph_enabled(
        {"sufler": {"graph": False, "graph_dir": "~/Vault/Work"}}) is False


def test_string_false_also_turns_it_off():
    """Приложение пишет значения в кавычках (`graph: "false"`), и строгая
    проверка `is True` считала бы такую строку включённым флагом."""
    for value in ("false", "False", " no ", "off", "0", "нет"):
        assert install_profile.graph_enabled(
            {"sufler": {"graph": value, "graph_dir": "~/V"}}) is False, value


def test_string_true_keeps_it_on():
    for value in ("true", "True", "yes", "on", "1", "да"):
        assert install_profile.graph_enabled(
            {"sufler": {"graph": value, "graph_dir": "~/V"}}) is True, value


def test_garbage_keeps_the_default():
    """Опечатка в конфиге не должна молча отключать граф на рабочей машине."""
    assert install_profile.graph_enabled(
        {"sufler": {"graph": "может быть", "graph_dir": "~/V"}}) is True
    assert install_profile.graph_enabled({"sufler": {"graph": None, "graph_dir": "~/V"}}) is True


def test_ready_status_survives_without_a_graph_note(tmp_path):
    """Лёгкий профиль: узлов нет, а встреча готова — это не ошибка."""
    store = MeetingStatusStore(tmp_path)
    transcript = tmp_path / "transcripts" / "2026-08-19_1000.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("- Коля: привет\n", encoding="utf-8")

    path = store.ready(transcript, None)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["state"] == "ready" and data["stage"] == "complete"
    assert "note_path" not in data, "заметки графа нет — и поля быть не должно"
    assert data["transcript_path"].endswith("2026-08-19_1000.md")


def _pipeline_sandbox(tmp_path, *, graph: str, graph_dir: str, hook: pathlib.Path,
                      transcript_text: str) -> tuple[pathlib.Path, dict]:
    """Песочница конвейера: корень данных, конфиг, стенограмма, хук."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text(
        "llm:\n  model: нет-такой\n  small_model: нет-такой\n"
        "sufler:\n"
        f"  graph: \"{graph}\"\n"
        f"  graph_dir: \"{graph_dir}\"\n"
        f"  post_meeting_hook: \"touch {hook}\"\n",
        encoding="utf-8")
    tdir = tmp_path / "transcripts"
    tdir.mkdir(exist_ok=True)
    live = tdir / "2026-08-19_1200.md"
    live.write_text(transcript_text, encoding="utf-8")
    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    env.pop("SUFLER_GRAPH_DIR", None)
    return live, env


def _run_graph_updater(live: pathlib.Path, env: dict):
    return subprocess.run([sys.executable, str(ROOT / "src" / "graph_updater.py"), str(live)],
                          capture_output=True, text=True, env=env, timeout=120)


def test_graph_off_still_runs_the_hook(tmp_path):
    """Выключен граф — выключены только узлы.

    Первая версия правки выходила из graph_updater сразу, и вместе с узлами
    пропадали архив встречи, копии в vault и post_meeting_hook — всё, что от
    модели не зависит (ревью 19.08, все три головы). Проверяем поведением, а
    не текстом файла: хук обязан отработать, а модель — не позваться (её тут
    и нет: в конфиге несуществующий тег).
    """
    graph = tmp_path / "Граф"
    (graph / "Встречи").mkdir(parents=True)
    hook = tmp_path / "hook-ran"
    live, env = _pipeline_sandbox(tmp_path, graph="false", graph_dir=str(graph), hook=hook,
                                  transcript_text="- Коля: " + "разговор про релиз. " * 40)

    result = _run_graph_updater(live, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert hook.exists(), "post_meeting_hook обязан отработать и без графа"
    assert "узлы не строим" in result.stdout


def test_no_graph_dir_is_not_an_error_and_keeps_the_hook(tmp_path):
    """`graph_dir: ""` — законный режим «только расшифровка»: не ошибка."""
    hook = tmp_path / "hook-ran"
    live, env = _pipeline_sandbox(tmp_path, graph="true", graph_dir="", hook=hook,
                                  transcript_text="- Коля: " + "разговор про релиз. " * 40)

    result = _run_graph_updater(live, env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert hook.exists(), "хук не должен теряться из-за ненастроенной папки графа"


def test_silence_is_reported_even_without_a_graph_dir(tmp_path):
    """Запись без речи — это `empty`, а не «готово»: проверка длины
    стенограммы обязана стоять выше вопроса о папке графа (ревью 19.08, GLM)."""
    hook = tmp_path / "hook-ran"
    live, env = _pipeline_sandbox(tmp_path, graph="true", graph_dir="", hook=hook,
                                  transcript_text="- Коля: угу\n")

    result = _run_graph_updater(live, env)

    assert result.returncode == 3, "EXIT_NO_SPEECH (3), а не 0"
    assert not hook.exists(), "хук на тишине не запускаем"
