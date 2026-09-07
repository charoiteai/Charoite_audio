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


def test_canonical_walks_stub_chains_and_bare_targets_and_never_writes_into_a_stub(tmp_path):
    """Круг 2 (GLM Critical / DS B1): один хоп возвращал встречу в мёртвую
    заглушку при цепочке A→B→C и при цели без папки; upsert в заглушку —
    никогда."""
    graph = _graph(tmp_path)
    people = graph / "Люди"
    (people / "Оля.md").write_text("# Оля → [[Люди/Петров]]\n", encoding="utf-8")
    (people / "Петров.md").write_text("# Петров → [[Петров Иван]]\n\nДубль. Смерджен\n", encoding="utf-8")
    assert g.find_canonical(graph, "Оля").name == "Петров Иван.md", "цепочка A→B→C и цель без папки"
    (people / "Кольцо.md").write_text("# Кольцо → [[Люди/Кольцо2]]\n", encoding="utf-8")
    (people / "Кольцо2.md").write_text("# Кольцо2 → [[Люди/Кольцо]]\n", encoding="utf-8")
    assert g.find_canonical(graph, "Кольцо") is None, "кольцо редиректов — обрыв"
    (people / "Мёртвая.md").write_text("# Мёртвая → [[Люди/Нет такой]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Мёртвая", "person", "аналитик", "Встречи/2026-09-07_1000", "")
    assert "2026-09-07" not in (people / "Мёртвая.md").read_text(encoding="utf-8"), "встреча дописана в заглушку"
    # узел заведён ПО ЦЕЛИ заглушки: слияние сказало «этот человек теперь там» (DS r3 I1)
    born = people / "Нет такой.md"
    assert born.exists() and "2026-09-07_1000" in born.read_text(encoding="utf-8") and "аналитик" in born.read_text(encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Мёртвая", "person", "", "Встречи/2026-09-08_1000", "")
    assert born.read_text(encoding="utf-8").count("## Встречи") == 1 and "2026-09-08" in born.read_text(encoding="utf-8")
    # голая цель заглушки: сосед по папке важнее корневого тёзки (GLM r3 M1)
    (graph / "Петров Иван.md").write_text("# черновик в корне\n", encoding="utf-8")
    assert g.find_canonical(graph, "Оля") == people / "Петров Иван.md"


def test_instrumental_rules_do_not_glue_nominative_names(tmp_path):
    """DS r2 B2/M1: «Алексей» — именительный, не творительный «-ей»;
    «Андреем» → «Андрей»."""
    assert nominative_candidates("Алексей") == ()
    assert nominative_candidates("Сергей") == ()
    assert nominative_candidates("Марией") == ("Мария",) and nominative_candidates("Юлией") == ("Юлия",)
    assert nominative_candidates("Гришей") == (), "принятая цена гейта: длинные формы не на «-ия»"
    assert "Андрей" in nominative_candidates("Андреем")
    assert "Сергей" in nominative_candidates("Сергеем")
    graph = _graph(tmp_path)
    (graph / "Люди" / "Алекса.md").write_text("# Алекса\n", encoding="utf-8")
    assert g.find_canonical(graph, "Алексей", folder="Люди") is None, "Алексей приклеился к Алексе"


def test_unlink_reports_each_target_once_and_doctor_strips_table_backslash(tmp_path):
    graph = _graph(tmp_path)
    r = graph_links.LinkResolver(graph)
    out, gone = graph_links.unlink_unresolved("[[Kwen 32B]] и снова [[Kwen 32B]]", r)
    assert gone == ["Kwen 32B"] and "[[" not in out
    (graph / "Встречи" / "2026-09-01_1000.md").write_text("| [[Kwen 32B\\|Kwen]] |\n", encoding="utf-8")
    (graph / "Системы" / "Реестр Витрин.md").write_text("# Реестр Витрин\n", encoding="utf-8")
    (graph / "Системы" / "Витрин Реестр.md").write_text("# Витрин Реестр\n", encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=5)
    assert rep["examples"]["broken"] == ["Встречи/2026-09-01_1000.md -> [[Kwen 32B]]"]
    assert rep["near_dups"] == 0, "порядок слов у систем — не дубль (как в find_canonical)"
