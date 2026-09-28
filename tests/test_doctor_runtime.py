"""Диагностика должна отвечать на вопрос, который реально задают в сбой.

03.08 встречи перестали раскладываться по папкам. На выяснение ушёл час:
Ollama отвечала на `/api/tags` мгновенно, модель числилась загруженной, а
инференс стоял. Все нужные проверки уже были написаны — но лежали по разным
местам, и ни одна не собиралась в один ответ.

Здесь проверяется вторая половина doctor: та, что смотрит на работу, а не
на установку.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import doctor  # noqa: E402
import charoite_paths  # noqa: E402


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
    # корень подменяется публичной дверью канона: в процессе его уже назвала
    # обвязка, и переменную канон не услышал бы (сегодня)
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(MeetingStatusStore, "unfinished",
                        lambda self, **kw: [{"meeting_id": "2026-08-03_1030"}])
    monkeypatch.setattr(MeetingStatusStore, "typical_duration", lambda self, **kw: None)

    doctor.check_pipeline()

    out = _lines(capsys)
    assert "2026-08-03_1030" in out, "имя встречи важнее числа: с него начинается разбор"
    assert "rebuild_transcript" in out, "должна быть команда, а не только диагноз"
    # без корня скопированная команда получит отказ двери — подсказка повторяет
    # тот корень, которым запущен сам doctor (№340)
    assert f"CHAROITE_ROOT={shlex.quote(str(tmp_path.resolve()))} " in out


def test_clean_pipeline_says_so(capsys, monkeypatch, tmp_path):
    from meeting_processing import MeetingStatusStore

    (tmp_path / "logs" / "meeting-status").mkdir(parents=True)
    # корень подменяется публичной дверью канона: в процессе его уже назвала
    # обвязка, и переменную канон не услышал бы (сегодня)
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(MeetingStatusStore, "unfinished", lambda self, **kw: [])
    monkeypatch.setattr(MeetingStatusStore, "typical_duration", lambda self, **kw: 420.0)

    doctor.check_pipeline()

    out = _lines(capsys)
    assert "незавершённых встреч нет" in out
    assert "~7 мин" in out, "честное время — часть картины, а не украшение"


def test_all_clear_names_the_root_in_the_launch_command(capsys, monkeypatch, tmp_path):
    """«Всё на месте» подсказывает запуск — с тем корнем, который проверялся.

    Пробел в пути — чтобы кавычки были частью проверки: подсказку копируют в
    shell как есть."""
    root = tmp_path / "мой корень"
    root.mkdir()
    charoite_paths.use_data_root(root, replace=True)
    for name in ("check_python", "check_deps", "check_ollama", "check_stt", "check_models",
                 "check_llm_alive", "check_pipeline", "check_import_queue", "check_disk"):
        monkeypatch.setattr(doctor, name, lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "check_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["doctor.py"])

    doctor.main()

    out = _lines(capsys)
    assert f"CHAROITE_ROOT={shlex.quote(str(root.resolve()))} .venv/bin/python src/main.py" in out
    assert "'" in out, "путь с пробелом уходит в shell в кавычках"


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
    charoite_paths.use_data_root(tmp_path, replace=True)

    doctor.check_import_queue({})

    out = _lines(capsys)
    assert "ждёт файлов: 2" in out
    assert f"CHAROITE_ROOT={shlex.quote(str(tmp_path.resolve()))} " in out, \
        "ручной скан без корня кончится отказом двери — подсказка называет корень"


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


# ── дверь строгого JSON у доктора ─────────────────────────────────────────

def _cfg_строгого(engine: str = "ollama") -> dict:
    return {"llm": {"base_url": "http://127.0.0.1:11434", "engine": engine,
                    "model": "qwen3.6:35b-a3b"}}


def test_отвергнутый_адрес_молчит_о_строгом_json(capsys, monkeypatch):
    """Адрес отвергнут политикой — диагноз уже назван `llm_url`, второй
    строки нет; тем более её нет на mlx, где строгий JSON не спрашивают."""
    monkeypatch.setattr(doctor, "llm_url", lambda cfg: None)

    doctor.check_strict_json(_cfg_строгого("mlx-server"), True)

    assert _lines(capsys) == ""
    assert doctor.issues == 0


def test_проба_живости_не_дошла_молчит(capsys, monkeypatch):
    """`alive is None` — причина уже напечатана `check_llm_alive`."""
    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")

    doctor.check_strict_json(_cfg_строгого(), None)

    assert _lines(capsys) == ""
    assert doctor.issues == 0


def test_mlx_движок_не_проверяется(capsys, monkeypatch):
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:8080")
    monkeypatch.setattr(llm_health, "strict_json",
                        lambda *a, **k: pytest.fail("на mlx строгий JSON не спрашивают"))

    doctor.check_strict_json(_cfg_строгого("mlx-server"), True)

    out = _lines(capsys)
    assert "строгий JSON не проверяется на движке mlx-server" in out
    assert doctor.issues == 0


def test_непроверенный_строгий_json_по_исходу(capsys, monkeypatch):
    """Модель занята / медлит / не найдена / не отвечает — запрос не шлём, а
    называем исход пробы живости."""
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm_health, "strict_json",
                        lambda *a, **k: pytest.fail("при неживой модели проба не нужна"))

    for состояние, слово in ((llm_health.BUSY, "модель занята"),
                             (llm_health.SLOW, "модель отвечает медленно"),
                             (llm_health.MISSING, "модель не найдена"),
                             (False, "модель не отвечает")):
        doctor.check_strict_json(_cfg_строгого(), состояние)
        assert f"строгий JSON не проверен: {слово}" in _lines(capsys)
    assert doctor.issues == 0


def test_строгий_json_есть(capsys, monkeypatch):
    import llm
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm_health, "strict_json", lambda base, model, **k: (llm.STRICT_YES, ""))

    doctor.check_strict_json(_cfg_строгого(), True)

    assert "строгий JSON: есть" in _lines(capsys)
    assert doctor.issues == 0


def test_строгий_json_нет_печатает_фразу_двери(capsys, monkeypatch):
    """`no` — та же строка, что и в stderr (`llm.strict_json_sentence`), плюс
    указатель на документацию; рецепта пересборки сервера нет."""
    import llm
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm_health, "strict_json",
                        lambda base, model, **k: (llm.STRICT_NO, "structured output is unavailable"))

    doctor.check_strict_json(_cfg_строгого(), True)

    out = _lines(capsys)
    assert llm.strict_json_sentence("qwen3.6:35b-a3b", "http://127.0.0.1:11434",
                                    "structured output is unavailable") in out
    assert "docs/MODELS.md" in out
    assert "ollama pull" not in out
    assert doctor.issues == 0


def test_строгий_json_не_проверен_причиной(capsys, monkeypatch):
    import llm
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm_health, "strict_json",
                        lambda base, model, **k: (llm.STRICT_UNKNOWN, "таймаут"))

    doctor.check_strict_json(_cfg_строгого(), True)

    assert "строгий JSON не проверен — таймаут" in _lines(capsys)
    assert doctor.issues == 0


def test_без_llm_нет_строки_о_строгом_json(capsys, monkeypatch):
    """`llm` не читается (нет requests) — строки о строгом JSON нет: доктор
    обязан работать и на машине, где зависимостей ещё нет."""
    import builtins

    настоящее = builtins.__import__

    def без_llm(name, *a, **k):
        if name in ("llm", "llm_health"):
            raise ImportError(name)
        return настоящее(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", без_llm)
    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")

    doctor.check_strict_json(_cfg_строгого(), True)

    assert _lines(capsys) == ""
    assert doctor.issues == 0


def test_check_llm_alive_возвращает_исход_пробы(capsys, monkeypatch):
    """Пин: каким бы ни был исход пробы, функция отдаёт его наружу — на нём
    строится решение `check_strict_json`."""
    import llm_health

    monkeypatch.setattr(doctor, "llm_url", lambda cfg: "http://127.0.0.1:11434")
    monkeypatch.setattr(llm_health, "listener_path", lambda url: None)
    for состояние in (True, llm_health.BUSY, llm_health.MISSING, llm_health.SLOW, False):
        monkeypatch.setattr(llm_health, "probe",
                            lambda cfg, timeout=None, _s=состояние: _s)
        assert doctor.check_llm_alive(_cfg_строгого()) is состояние


def test_check_llm_alive_облако_тоже_возвращает_исход(capsys, monkeypatch):
    """Облачная ветка — отдельный выход: и он отдаёт исход пробы наружу."""
    import llm_health
    import privacy

    for рубильник in privacy.KILL_SWITCHES:
        monkeypatch.delenv(рубильник, raising=False)
    cfg = {"sufler": {"cloud_engine": True},
           "llm": {"engine": "cloud", "model": "м",
                   "cloud_base_url": "https://cloud.example/v1"}}
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)

    assert doctor.check_llm_alive(cfg) is False


def test_main_передаёт_исход_пробы_в_строгую_дверь(capsys, monkeypatch, tmp_path):
    charoite_paths.use_data_root(tmp_path, replace=True)
    for name in ("check_python", "check_deps", "check_ollama", "check_stt",
                 "check_models", "check_pipeline", "check_import_queue", "check_disk"):
        monkeypatch.setattr(doctor, name, lambda *a, **kw: None)
    monkeypatch.setattr(doctor, "check_config", lambda: {})
    monkeypatch.setattr(doctor, "check_llm_alive", lambda cfg: "МЕТКА")
    seen: dict = {}
    monkeypatch.setattr(doctor, "check_strict_json",
                        lambda cfg, alive: seen.update(alive=alive))
    monkeypatch.setattr(sys, "argv", ["doctor.py"])

    doctor.main()

    assert seen["alive"] == "МЕТКА"


def test_the_engine_line_appears_only_when_nemotron_is_chosen(capsys, monkeypatch):
    """Строка движка — только если выбран не sherpa (№474); проба — дверью движка."""
    import foreign_python as fp
    asked = []
    monkeypatch.setattr(doctor.diarize_nemotron, "probe_in_env",
                        lambda setting, *, root: asked.append(setting) or fp.Outcome(fp.OK, payload={"mlx_audio": "0.5.6"}))
    doctor.check_engine({"sufler": {"diarize_backend": "sherpa"}})
    doctor.check_engine({})
    assert capsys.readouterr().out == "" and asked == []
    doctor.check_engine({"sufler": {"diarize_backend": "Nemotron", "nemotron_python": "/env/python"}})
    assert "✓ Nemotron: mlx-audio 0.5.6, веса на месте, интерпретатор /env/python" in capsys.readouterr().out
    assert asked == ["/env/python"]


def test_the_engine_line_names_the_interpreter_the_door_chose(capsys, monkeypatch, tmp_path):
    """Заданный ключ главнее установленного окружения: доктор называет, чей интерпретатор
    в работе, — иначе старый venv в конфиге молча перекрывал бы поставленное окружение."""
    import foreign_python as fp
    monkeypatch.setattr(doctor, "_root", lambda: tmp_path)
    monkeypatch.setattr(doctor.diarize_nemotron, "probe_in_env",
                        lambda setting, *, root: fp.Outcome(fp.OK, payload={"mlx_audio": "0.5.6"}))
    installed = doctor.diarize_nemotron.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.write_text("#", encoding="utf-8")
    doctor.check_engine({"sufler": {"diarize_backend": "nemotron", "nemotron_python": " "}})
    assert f"интерпретатор {installed}\n" in capsys.readouterr().out
    doctor.check_engine({"sufler": {"diarize_backend": "nemotron", "nemotron_python": "/venv/bin/python"}})
    assert "интерпретатор /venv/bin/python\n" in capsys.readouterr().out


def test_an_unready_engine_is_a_warning_with_one_install_command(capsys, monkeypatch, tmp_path):
    """Не готов — «–», не «✗»: пересборка уходит на sherpa с причиной в шапке. Совет — по
    тому, что выбрала дверь: окружения нет — команда одна, из отказа двери; окружение есть,
    но не работает — переставить; задан ключ — установка его не заменит, ключ исправить."""
    import foreign_python as fp
    issues = doctor.issues
    monkeypatch.setattr(doctor, "_root", lambda: tmp_path)
    doctor.check_engine({"sufler": {"diarize_backend": "nemotron"}})
    out = capsys.readouterr().out
    assert out.startswith(" – Nemotron выбран, но не готов") and out.count("install_engine.py") == 1
    assert "не установлено" in out and doctor.issues == issues

    installed = doctor.diarize_nemotron.engine_python(tmp_path)
    installed.parent.mkdir(parents=True)
    installed.write_text("#", encoding="utf-8")
    monkeypatch.setattr(doctor.diarize_nemotron, "probe_in_env",
                        lambda setting, *, root: fp.Outcome(fp.UNAVAILABLE, reason="нет каталога модели"))
    doctor.check_engine({"sufler": {"diarize_backend": "nemotron"}})
    out = capsys.readouterr().out
    assert "нет каталога модели; переставить окружение:" in out and out.count("install_engine.py") == 1

    monkeypatch.setattr(doctor.diarize_nemotron, "probe_in_env",
                        lambda setting, *, root: fp.Outcome(fp.FAILED, reason=f"нет интерпретатора {setting}"))
    doctor.check_engine({"sufler": {"diarize_backend": "nemotron", "nemotron_python": "/снесённый/venv/python"}})
    out = capsys.readouterr().out
    assert "нет интерпретатора /снесённый/venv/python; ключ sufler.nemotron_python главнее" in out
    assert "исправьте или очистите его" in out and doctor.issues == issues
