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
    assert install_profile.graph_enabled({}) is True
    assert install_profile.graph_enabled({"sufler": {}}) is True
    assert install_profile.deja_vu_enabled({"sufler": {"graph": False}}) is True


def test_boolean_false_turns_the_graph_off():
    assert install_profile.graph_enabled({"sufler": {"graph": False}}) is False


def test_string_false_also_turns_it_off():
    """Приложение пишет значения в кавычках (`graph: "false"`), и строгая
    проверка `is True` считала бы такую строку включённым флагом."""
    for value in ("false", "False", " no ", "off", "0", "нет"):
        assert install_profile.graph_enabled({"sufler": {"graph": value}}) is False, value


def test_string_true_keeps_it_on():
    for value in ("true", "True", "yes", "on", "1", "да"):
        assert install_profile.graph_enabled({"sufler": {"graph": value}}) is True, value


def test_garbage_keeps_the_default():
    """Опечатка в конфиге не должна молча отключать граф на рабочей машине."""
    assert install_profile.graph_enabled({"sufler": {"graph": "может быть"}}) is True
    assert install_profile.graph_enabled({"sufler": {"graph": None}}) is True


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
