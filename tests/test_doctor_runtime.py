"""Диагностика должна отвечать на вопрос, который реально задают в сбой.

03.08 встречи перестали раскладываться по папкам. На выяснение ушёл час:
Ollama отвечала на `/api/tags` мгновенно, модель числилась загруженной, а
инференс стоял. Все нужные проверки уже были написаны — но лежали по разным
местам, и ни одна не собиралась в один ответ.

Здесь проверяется вторая половина doctor: та, что смотрит на работу, а не
на установку.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import doctor  # noqa: E402


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """Счётчик проблем глобальный — иначе тесты считают чужие ошибки."""
    monkeypatch.setattr(doctor, "issues", 0)
    yield


def _lines(capsys) -> str:
    return capsys.readouterr().out


def test_stalled_inference_is_reported_even_when_the_server_answers(capsys, monkeypatch):
    """Главный случай: сервер жив, список моделей отдаётся, инференс стоит."""
    import llm_health

    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)
    monkeypatch.setattr(llm_health, "listener_path",
                        lambda url: "/opt/homebrew/opt/ollama/bin/ollama")

    doctor.check_llm_alive({"llm": {"base_url": "http://127.0.0.1:11434",
                                    "model": "qwen3.6:35b-a3b"}})

    out = _lines(capsys)
    assert "не отвечает на генерацию" in out
    assert "ollama" in out, "кто держит порт — это первое, что спрашиваешь в сбой"
    assert doctor.issues == 1, "вставший инференс — это ✗, а не пометка"


def test_live_model_is_not_alarming(capsys, monkeypatch):
    import llm_health

    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: True)
    doctor.check_llm_alive({"llm": {"model": "qwen3.6:35b-a3b"}})

    assert "отвечает" in _lines(capsys)
    assert doctor.issues == 0


def test_stuck_meetings_are_named(capsys, monkeypatch, tmp_path):
    from meeting_processing import MeetingStatusStore

    store = MeetingStatusStore(tmp_path)
    store.directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(MeetingStatusStore, "unfinished",
                        lambda self, **kw: [{"meeting_id": "2026-08-03_1030"}])
    monkeypatch.setattr(MeetingStatusStore, "typical_duration", lambda self, **kw: None)

    doctor.check_pipeline()

    out = _lines(capsys)
    assert "2026-08-03_1030" in out, "имя встречи важнее числа: с него начинается разбор"
    assert "rebuild_transcript" in out, "должна быть команда, а не только диагноз"


def test_clean_pipeline_says_so(capsys, monkeypatch, tmp_path):
    from meeting_processing import MeetingStatusStore

    (tmp_path / "logs" / "meeting-status").mkdir(parents=True)
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    monkeypatch.setattr(MeetingStatusStore, "unfinished", lambda self, **kw: [])
    monkeypatch.setattr(MeetingStatusStore, "typical_duration", lambda self, **kw: 420.0)

    doctor.check_pipeline()

    out = _lines(capsys)
    assert "незавершённых встреч нет" in out
    assert "~7 мин" in out, "честное время — часть картины, а не украшение"


def test_import_folder_is_looked_up_where_the_app_keeps_it(monkeypatch):
    """Путь задают в приложении, config.yaml о нём не знает."""
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **kw: type("R", (), {"returncode": 0,
                                                        "stdout": "/tmp/Inbox\n"})())
    assert doctor._import_dir({}) == "/tmp/Inbox"


def test_config_wins_over_app_settings(monkeypatch):
    monkeypatch.setattr(doctor.subprocess, "run",
                        lambda *a, **kw: pytest.fail("конфиг уже ответил"))
    assert doctor._import_dir({"charoite": {"importDir": "/tmp/FromConfig"}}) == "/tmp/FromConfig"


def test_waiting_files_are_counted(capsys, tmp_path, monkeypatch):
    (tmp_path / "встреча.m4a").write_bytes(b"0")
    (tmp_path / "заметки.txt").write_text("текст", encoding="utf-8")
    (tmp_path / "done").mkdir()          # папки не считаем
    monkeypatch.setattr(doctor, "_import_dir", lambda cfg: str(tmp_path))

    doctor.check_import_queue({})

    assert "ждёт файлов: 2" in _lines(capsys)


def test_missing_import_folder_is_a_failure(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_import_dir", lambda cfg: str(tmp_path / "нет-такой"))

    doctor.check_import_queue({})

    assert doctor.issues == 1, "папка настроена и не существует — это поломка"


def test_full_disk_is_a_failure(capsys, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 2e9})())
    doctor.check_disk()

    assert doctor.issues == 1
    assert "2.0 ГБ" in _lines(capsys)
