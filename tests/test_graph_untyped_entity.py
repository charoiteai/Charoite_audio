"""Тип сущности вне перечня разбора не рождает узла (№836).

Замер 30.10 по графу владельца: тип вне `ENT_FOLDER` молча падал в «Системы» —
за октябрь 24 рождения, 10 из них люди («(человек)») в Системы/. Правило — только
на русском разборе (en/zh переводят значения, тип придёт «system»), только вместо
рождения: узел, который уже есть, и опечатка живого узла встречу получают.
"""
import inspect
import json
import pathlib
import re
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import graph_updater as g  # noqa: E402

LINK = "Встречи/2026-10-27_1000"


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    graph = tmp_path / "Граф"
    for d in ("Встречи", "Люди", "Системы", "Модели", "Команды"):
        (graph / d).mkdir(parents=True, exist_ok=True)
    return graph


def _node(graph: pathlib.Path, rel: str) -> pathlib.Path:
    p = graph / f"{rel}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: система\n---\n# {p.stem}\n\n## Встречи\n", encoding="utf-8")
    return p


def _meeting(tmp_path, monkeypatch, graph, ents, language=""):
    """Прогон main() на подставном разборе с одними сущностями; модель, архив, хук — заглушки."""
    data = {"люди": [{"имя": "Гоша", "роль": "разработчик", "вклад": "сроки"}], "сущности": ents}
    speech = "**Гоша** [10:00]: обсуждаем релиз и проверку стенда.\n" * 3 + "Конец обсуждения. " * 20
    tpath = tmp_path / "transcripts" / "2026-10-27_1000.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(speech, encoding="utf-8")
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(graph))
    monkeypatch.setattr(sys, "argv", ["graph_updater.py", str(tpath)])
    monkeypatch.setattr(g, "load_cfg", lambda: {"sufler": {"user_name": "", "language": language},
                                               "llm": {"model": "нет-такой"}})
    monkeypatch.setattr(g, "extract", lambda *a, **k: json.loads(json.dumps(data)))
    for fn in ("_open_progress", "harden_umask", "archive_and_publish", "run_post_hook",
               "reindex_memory", "meeting_facts_to_brain", "_revise_meeting_cores"):
        monkeypatch.setattr(g, fn, lambda *a, **k: None)
    monkeypatch.setattr(g.privacy, "cloud_enrich_enabled", lambda cfg: False)
    monkeypatch.setattr(g, "LLM", lambda cfg: (_ for _ in ()).throw(RuntimeError("модели нет")))
    g.main()
    notes = sorted((graph / "Встречи").glob("*.md"))
    assert len(notes) == 1, notes
    return notes[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("typ", ["человек", "задача", "Человек, упом."])
def test_type_outside_the_list_gives_no_node(tmp_path, monkeypatch, typ):
    graph = _graph(tmp_path)
    note = _meeting(tmp_path, monkeypatch, graph, [{"имя": "Иван Петров", "тип": typ, "суть": "руководитель"}])
    assert not (graph / "Системы" / "Иван Петров.md").exists()
    assert "Иван Петров" in note and g._HELD_WHY["untyped"] in note
    assert "[[Системы/Иван Петров" not in note
    assert not (graph / g.CANDIDATES_NOTE).exists(), "человеку подтверждать нечего — только лог"


def test_untyped_leaves_its_trace_in_the_log(tmp_path, monkeypatch):
    """След удержания — только журнал: кандидатов нет, значит строка лога обязана быть (out1 DS M7)."""
    graph = _graph(tmp_path)
    monkeypatch.setattr(g, "_root", lambda: tmp_path / "data")
    held = g.apply_entities(graph, [{"имя": "Иван Петров", "тип": "человек", "суть": "x"}], LINK, typed=True)
    assert held == {("Системы", "Иван Петров"): ("untyped", [])}
    log = (tmp_path / "data" / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "узел не создан: Системы/Иван Петров: тип вне перечня разбора" in log, log


@pytest.mark.parametrize("typ, folder", [("Команда", "Команды"), (" модель ", "Модели"), ("СИСТЕМА", "Системы")])
def test_listed_type_in_any_case_goes_to_its_folder(tmp_path, monkeypatch, typ, folder):
    """Гейт и папка читают тип одним `ent_kind`: «Команда» — не только «не untyped», но и в
    Команды/, а строка заметки — ссылкой туда же (n836-out1, обе головы)."""
    graph = _graph(tmp_path)
    note = _meeting(tmp_path, monkeypatch, graph, [{"имя": "Платформа", "тип": typ, "суть": "x"}])
    assert (graph / folder / "Платформа.md").exists()
    assert f"[[{folder}/Платформа" in note, note


def test_existing_node_still_gets_the_meeting(tmp_path, monkeypatch):
    graph = _graph(tmp_path)
    node = _node(graph, "Системы/Миграция Postgres")
    note = _meeting(tmp_path, monkeypatch, graph, [{"имя": "Миграция Postgres", "тип": "задача", "суть": "x"}])
    assert LINK in node.read_text(encoding="utf-8")
    assert "[[Системы/Миграция Postgres" in note
    assert g._HELD_WHY["untyped"] not in note


@pytest.mark.parametrize("ent", [{"имя": "Шлюз", "суть": "x"}, {"имя": "Шлюз", "тип": None, "суть": "x"},
                                 {"имя": "Шлюз", "тип": "", "суть": "x"}, {"имя": "Шлюз", "тип": "  ", "суть": "x"},
                                 {"имя": "Шлюз", "тип": "Система", "суть": "x"}])
def test_empty_or_listed_type_is_born_as_before(tmp_path, monkeypatch, ent):
    graph = _graph(tmp_path)
    _meeting(tmp_path, monkeypatch, graph, [ent])
    assert (graph / "Системы" / "Шлюз.md").exists()


@pytest.mark.parametrize("language", ["ru", "en-US", "de", "RU"])
def test_untranslated_profile_applies_the_rule(tmp_path, monkeypatch, language):
    """Промпт переводит значения только на «en» и «zh» — любой другой язык профиля получает
    русский промпт, а значит и «человек» в типе (n836-out1, DS I1)."""
    graph = _graph(tmp_path)
    _meeting(tmp_path, monkeypatch, graph, [{"имя": "Иван Петров", "тип": "человек", "суть": "x"}], language=language)
    assert not (graph / "Системы" / "Иван Петров.md").exists()


@pytest.mark.parametrize("language", ["en", "zh"])
def test_translated_profile_keeps_the_old_birth(tmp_path, monkeypatch, language):
    graph = _graph(tmp_path)
    _meeting(tmp_path, monkeypatch, graph, [{"имя": "Gateway", "тип": "system", "суть": "x"}], language=language)
    assert (graph / "Системы" / "Gateway.md").exists()


def test_same_name_with_a_listed_type_keeps_the_link(tmp_path, monkeypatch):
    graph = _graph(tmp_path)
    note = _meeting(tmp_path, monkeypatch, graph, [{"имя": "Шлюз", "тип": "задача", "суть": "x"},
                                                   {"имя": "Шлюз", "тип": "система", "суть": "y"}])
    assert (graph / "Системы" / "Шлюз.md").exists()
    assert g._HELD_WHY["untyped"] not in note


def test_same_name_born_in_another_folder_keeps_the_link(tmp_path, monkeypatch):
    """Имя с типом вне перечня и то же имя «моделью» в одной встрече: узел в Модели/, строка
    «тип вне перечня» снята — заметка ссылается на узел (n836-out1, DS M8)."""
    graph = _graph(tmp_path)
    note = _meeting(tmp_path, monkeypatch, graph, [{"имя": "Шлюз", "тип": "человек", "суть": "x"},
                                                   {"имя": "Шлюз", "тип": "модель", "суть": "y"}])
    assert (graph / "Модели" / "Шлюз.md").exists() and not (graph / "Системы" / "Шлюз.md").exists()
    assert g._HELD_WHY["untyped"] not in note, note


def test_typo_of_a_live_node_stays_near(tmp_path):
    graph = _graph(tmp_path)
    _node(graph, "Модели/Qwen 32B")
    held = g.apply_entities(graph, [{"имя": "Kwen 32B", "тип": "сервис", "суть": "x"}], LINK, typed=True)
    assert held[("Системы", "Kwen 32B")][0] == "near"


def test_prompt_types_are_keys_of_the_folder_map():
    m = re.search(r'"тип":"([а-я|]+)","суть"', inspect.getsource(g._extract))
    assert m, "строка сущностей в промпте _extract изменила вид — обнови тест"
    listed = m.group(1).split("|")
    assert listed and set(listed) <= set(g.ENT_FOLDER), "перечень промпта и карта папок разошлись"
