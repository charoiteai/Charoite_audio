"""Аудит памяти 07.09 — граф как память: резолв ссылок, doctor, канон, падежи.

Три головы (GLM ×2, DS) на одной зоне сошлись на трёх дырах: doctor не знал
псевдонимов, порядок слов в имени заводил второго человека, облачная ревизия
переносила ссылки на несуществующие узлы. Каждая — тестом ниже.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import graph_doctor  # noqa: E402
import graph_links  # noqa: E402
import graph_names  # noqa: E402
import graph_updater as g  # noqa: E402
from graph_nodes import NodeIndex  # noqa: E402
from speaker_names import nominative_candidates, resolve_vocative  # noqa: E402


def _graph(tmp: pathlib.Path) -> pathlib.Path:
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Встречи-архив"):
        (tmp / d).mkdir()
    (tmp / "Люди" / "Петров Иван.md").write_text(
        '---\naliases: ["Иван П."]\n---\n# Петров Иван\n', encoding="utf-8")
    (tmp / "Системы" / "Витрина 1494.md").write_text(
        "---\naliases: [ИС 1494]\n---\n# Витрина 1494\n", encoding="utf-8")
    (tmp / "схема.png").write_bytes(b"png")
    return tmp


def test_resolver_knows_paths_stems_aliases_and_attachments(tmp_path):
    r = graph_links.LinkResolver(_graph(tmp_path))
    assert r.resolve("Люди/Петров Иван").name == "Петров Иван.md"
    assert r.resolve("Петров Иван").name == "Петров Иван.md"
    assert r.resolve("ИС 1494").name == "Витрина 1494.md", "псевдоним — живая цель, как у Obsidian"
    assert r.resolve("Системы/ИС 1494").name == "Витрина 1494.md"
    assert r.resolve("Люди/ИС 1494") is None, "псевдоним в чужой папке"
    assert r.resolve("схема.png").name == "схема.png"
    assert r.resolve("Собеседник 2") is None and r.resolve("он") is None


def test_unlink_unresolved_turns_dead_links_into_text_and_spares_code(tmp_path):
    r = graph_links.LinkResolver(_graph(tmp_path))
    text = ("- [[Люди/Петров Иван|Иван]] обсудил с [[Сашей]] и [[он]] [[ИС 1494]]\n"
            "| [[Kwen 32B\\|Kwen]] | ![[схема.png]] |\n"
            "```\n[[Собеседник 3]]\n```\n")
    out, gone = graph_links.unlink_unresolved(text, r)
    assert gone == ["Сашей", "он", "Kwen 32B"]
    assert "[[Люди/Петров Иван|Иван]]" in out and "[[ИС 1494]]" in out and "![[схема.png]]" in out
    assert "с Сашей и он" in out and "| Kwen |" in out
    assert "[[Собеседник 3]]" in out, "внутри кода ничего не меняется"
    assert graph_links.unlink_unresolved(out, r)[1] == [], "повторный проход ничего не находит"
    kept, gone2 = graph_links.unlink_unresolved("[[Новый узел]]", r, keep={"Новый узел"})
    assert kept == "[[Новый узел]]" and gone2 == []


def test_doctor_splits_active_and_archive_and_honours_aliases(tmp_path):
    graph = _graph(tmp_path)
    (graph / "Встречи-архив" / "2026-07-01_1000.md").write_text(
        "# a\n[[Собеседник 2]] [[он]] [[ИС 1494]] [[Kwen 32B]]\n", encoding="utf-8")
    (graph / "Встречи" / "2026-09-01_1000.md").write_text(
        "# b\n[[Люди/Петров Иван]] [[Перенос на завтра]]\n", encoding="utf-8")
    (graph / "Люди" / "Иван Петров.md").write_text("# Иван Петров\n", encoding="utf-8")

    rep = graph_doctor.inspect(graph, examples=5)

    assert (rep["broken"], rep["broken_archive"], rep["broken_active"]) == (4, 3, 1), rep
    assert rep["broken_targets"] == 4
    assert rep["examples"]["broken"] == ["Встречи/2026-09-01_1000.md -> [[Перенос на завтра]]"]
    assert rep["near_dups"] == 1 and rep["examples"]["near_dups"] == ["Иван Петров | Петров Иван"]
    assert any("активных" in w and "в архиве ещё 3" in w for w in rep["warnings"]), rep["warnings"]


def test_canonical_folds_word_order_for_people_only(tmp_path):
    graph = _graph(tmp_path)
    assert g.find_canonical(graph, "Иван Петров", folder="Люди").name == "Петров Иван.md"
    assert g.find_canonical(graph, "Иван Петров").name == "Петров Иван.md"
    (graph / "Системы" / "Реестр Витрин.md").write_text("# Реестр Витрин\n", encoding="utf-8")
    assert g.find_canonical(graph, "Витрин Реестр", folder="Системы") is None, "у систем порядок слов — смысл"
    (graph / "Люди" / "Иван Петров (Отдел).md").write_text("# Иван Петров (Отдел)\n", encoding="utf-8")
    amb: list[str] = []
    assert g.find_canonical(graph, "Петров Иван Отдел", ambiguous=amb, folder="Люди").name == "Иван Петров (Отдел).md"


def test_canonical_follows_redirect_stub_to_its_canon(tmp_path):
    graph = _graph(tmp_path)
    (graph / "Люди" / "Иван.md").write_text("# Иван → [[Люди/Петров Иван]]\n", encoding="utf-8")
    assert g.find_canonical(graph, "Иван").name == "Петров Иван.md", "встреча дописывалась в заглушку"
    (graph / "Люди" / "Оля.md").write_text("# Оля → [[Люди/Нет такой]]\n", encoding="utf-8")
    assert g.find_canonical(graph, "Оля") is None, "канон заглушки исчез — узла нет"


def test_speaker_label_is_stripped_from_real_names_only():
    assert graph_names.strip_speaker_label("Саша (Спикер 1)") == "Саша"
    assert graph_names.strip_speaker_label("Собеседник 2 (Саша)") == "Собеседник 2 (Саша)"
    assert graph_names.strip_speaker_label("Иван (руководитель)") == "Иван (руководитель)"
    assert g.is_speaker_placeholder("Собеседник 2 (Саша)")
    assert g.is_placeholder_node("Саша (Спикер 1)")
    assert g.ENT_FOLDER["модель"] == "Модели"
    assert g.link_or_text(pathlib.Path("/nonexistent"), "его") == "его"


def test_instrumental_case_resolves_to_a_known_person_only_when_unique():
    assert "Саша" in nominative_candidates("Сашей")
    assert nominative_candidates("Ромой") == ("Рома",)
    assert resolve_vocative("Колей", ["Коля", "Петя"]) == "Коля"
    assert resolve_vocative("Иваном", ["Иван"]) == "Иван"
    assert resolve_vocative("Игорем", ["Игорь"]) == "Игорь"
    assert resolve_vocative("Сашей", ["Саша", "Сашя"]) is None, "две формы — не гадаем"
    assert resolve_vocative("Сашей", ["Маша"]) is None


def test_node_index_skips_placeholder_nodes(tmp_path):
    graph = _graph(tmp_path)
    (graph / "Люди" / "Собеседник 3.md").write_text("# Собеседник 3\n", encoding="utf-8")
    idx = NodeIndex(graph)
    idx.refresh()
    names = {n.name for n in idx._nodes.values()}
    assert "Петров Иван" in names and "Собеседник 3" not in names
