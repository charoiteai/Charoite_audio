"""Профиль установки: выключатели должны выключать.

До этого «Граф знаний выключен» было фразой в описании пресета, а конвейер
всё равно звал разбор: на лёгкой модели он ломал JSON, ставил встрече статус
ошибки и заказывал повтор. Здесь проверяется, что флаг читается честно (в том
числе строкой из приложения) и что статус готовности живёт без заметки графа.
"""
from __future__ import annotations

import json
import pathlib
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


def test_graph_off_does_not_disable_the_rest_of_the_pipeline():
    """Выключен граф — выключены только узлы.

    Первый вариант правки выходил из graph_updater сразу, и вместе с узлами
    пропадали архив встречи, копии в vault и post_meeting_hook — то есть всё,
    что от модели не зависит (ревью 19.08, Gemini). Проверяем по коду: гейт
    стоит вокруг extract, а не вокруг main, и путь к архиву остаётся общим с
    веткой «модель молчала».
    """
    source = (ROOT / "src" / "graph_updater.py").read_text(encoding="utf-8")
    gate = source.index("graph_off = not install_profile.graph_enabled(cfg)")
    archive = source.index("from meeting_archive import archive_meeting")
    hook = source.rindex("run_post_hook(cfg, tpath, stamp)")
    assert gate < archive < hook, "архив и хук обязаны идти ПОСЛЕ гейта, а не мимо"
    assert "if not graph_ok and not graph_off:" in source, \
        "выключенный профилем граф — код 0, а не EXIT_NO_GRAPH с ретраем"


def test_pipeline_asks_for_a_note_only_when_the_graph_is_on():
    """Заметку встречи создаёт разбор в узлы: без него требовать её нельзя,
    иначе готовая встреча падает в «ошибку обработки»."""
    source = (ROOT / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    assert "if graph_on and note is None:" in source
    assert "publish(status.ready, live, note, names_pending(live))" in source
