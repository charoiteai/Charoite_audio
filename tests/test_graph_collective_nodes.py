"""Сборные узлы графа (№611, этап 1): голое имя при живых тёзках, человек узлом в
Системах, метка диаризации деятелем в досье — доктор считает, лента перечисляет,
граф не меняется.

Под узлом «Саша» граф копил факты семерых разных Саш: каждый «Саша» встречи
садился в один файл (замер 26.10: 36 таких узлов, 1553 ссылки из встреч), а
доктор считал это нормой — «Иван» и «Иван Петров» дублем не считались по замыслу.
"""
import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

from charoite_graph import graph_names  # noqa: E402
import graph_doctor  # noqa: E402
import graph_updater  # noqa: E402


def test_bare_name_with_namesakes_in_any_word_order():
    idx = graph_names.namesake_index(["Саша Образцов", "Сидоров Саша", "Саша", "Иван Петров",
                                      "Собеседник 3 (Саша)"])
    assert graph_names.bare_namesakes("Саша", idx) == ["Саша Образцов", "Сидоров Саша"]
    assert graph_names.bare_namesakes("Саша (из минуток)", idx) == ["Саша Образцов", "Сидоров Саша"], \
        "скобка — пометка, не часть имени"
    assert graph_names.bare_namesakes("САША", idx), "регистр имени не различает"


def test_full_name_label_or_lonely_name_is_not_collective():
    idx = graph_names.namesake_index(["Саша Образцов", "Собеседник 3 (Саша)"])
    assert graph_names.bare_namesakes("Саша Образцов", idx) == [], "полное имя — не голое"
    assert graph_names.bare_namesakes("Собеседник 3", idx) == [], "метка — не имя"
    assert graph_names.bare_namesakes("Гоша", idx) == [], "без тёзок — обычный узел"
    assert "собеседник" not in idx and "3" not in idx, "метка в индекс тёзок не идёт"


def test_speaker_label_as_actor_in_text():
    assert graph_names.has_speaker_actor("Собеседник 3 предложил перенести релиз")
    assert graph_names.has_speaker_actor("- [[Люди/Собеседник 2]] — за")
    assert graph_names.has_speaker_actor("Speaker 2 agreed")
    assert graph_names.has_speaker_actor("собеседник №4 против")
    assert not graph_names.has_speaker_actor("Вера (Собеседник 4) сказала"), "пометка после имени — не деятель"
    assert not graph_names.has_speaker_actor("собеседники согласились")
    assert not graph_names.has_speaker_actor("")


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    graph = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Досье", "Встречи"):
        (graph / d).mkdir(parents=True)
    for name in ("Саша", "Саша Образцов", "Сидоров Саша", "Пётр", "Пётр Лесков", "Олег", "Олег Сомов",
                 "Гоша", "Настя Иванова", "Вера (Собеседник 4)", "Вера Сомова"):
        (graph / "Люди" / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    (graph / "Люди" / "Олег.md").write_text(
        "---\ntags: [дубль-слит]\n---\n# Олег → см. [[Люди/Олег Сомов]]\n", encoding="utf-8")
    (graph / "Системы" / "Настя.md").write_text("# Настя\n", encoding="utf-8")
    (graph / "Системы" / "Витрина.md").write_text("# Витрина\n", encoding="utf-8")
    (graph / "Досье" / "Релиз.md").write_text("# Релиз\nСобеседник 2 решил ждать.\n", encoding="utf-8")
    (graph / "Досье" / "Бюджет.md").write_text("# Бюджет\nВера (Собеседник 4) за.\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-01_1000.md").write_text(
        "# В1\n- [[Люди/Саша]] и снова [[Люди/Саша|Саша]]\n- [[Люди/Пётр]]\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-02_1000.md").write_text("# В2\n- [[Саша]]\n", encoding="utf-8")
    return graph


def test_doctor_counts_collective_nodes_without_owner_stub_or_label(tmp_path):
    """«Саша» при двух тёзках — сборный; «Пётр» владельца — нет; заглушка «Олег» —
    нет; «Вера (Собеседник 4)» — метка, у неё свой счёт. Ссылки — по заметкам:
    две ссылки из одной встречи — одна встреча."""
    graph = _graph(tmp_path)
    before = {p: p.read_bytes() for p in graph.rglob("*.md")}

    rep = graph_doctor.inspect(graph, examples=5, owner="Пётр Хозяинов")

    assert rep["collective"] == 1 and rep["collective_links"] == 2, rep
    assert rep["feed"]["collective"] == [{"node": "Люди/Саша.md", "linked_from": 2,
                                          "namesakes": ["Саша Образцов", "Сидоров Саша"]}]
    assert rep["examples"]["collective"] == ["Люди/Саша.md ← из 2 заметок; тёзки: Саша Образцов, Сидоров Саша"]
    assert rep["people_in_systems"] == 1 and rep["feed"]["people_in_systems"] == ["Системы/Настя.md"]
    assert rep["dossier_labels"] == 1 and rep["feed"]["dossier_labels"] == ["Досье/Релиз.md"]
    assert rep["feed"]["placeholders"] == ["Люди/Вера (Собеседник 4).md"]
    assert {p: p.read_bytes() for p in graph.rglob("*.md")} == before, "доктор граф не меняет"


def test_owner_name_counts_without_config(tmp_path):
    rep = graph_doctor.inspect(_graph(tmp_path))
    assert {c["node"] for c in rep["feed"]["collective"]} == {"Люди/Саша.md", "Люди/Пётр.md"}


def test_collective_warning_fires_on_growth_only(tmp_path):
    graph = _graph(tmp_path)
    first = graph_doctor.inspect(graph, owner="Пётр")
    assert any("сборных узлов в Люди" in w and "(было" not in w for w in first["warnings"]), first["warnings"]
    assert any("людей узлами в Системах: 1;" in w for w in first["warnings"])
    assert any("досье с меткой диаризации в тексте: 1;" in w for w in first["warnings"])

    same = graph_doctor.inspect(graph, owner="Пётр", prev=first)
    assert not [w for w in same["warnings"] if "graph_feed" in w], "известное владельцу не горит каждую ночь"

    (graph / "Люди" / "Гоша Лесков.md").write_text("# Гоша Лесков\n", encoding="utf-8")
    grown = graph_doctor.inspect(graph, owner="Пётр", prev=first)
    assert any("сборных узлов в Люди (голое имя при живых тёзках): 2 (было 1)" in w for w in grown["warnings"])


def test_main_writes_feed_beside_report_and_keeps_report_lean(tmp_path, monkeypatch):
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    monkeypatch.setattr(graph_doctor, "report_path", lambda: report)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: {"sufler": {"user_name": "Пётр"}})
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", "--graph", str(graph)])
    graph_doctor.main()

    rep = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    feed = json.loads((report.parent / "graph_feed.json").read_text(encoding="utf-8"))["graphs"][str(graph)]
    assert "feed" not in rep and rep["collective"] == 1
    assert [c["node"] for c in feed["collective"]] == ["Люди/Саша.md"], "владелец из конфига — не сборный"

    graph_doctor.main()   # второй прогон: прошлый отчёт — база, сигнала о росте нет
    again = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    assert not [w for w in again["warnings"] if "graph_feed" in w]


def test_doctor_writes_through_the_atomic_door(tmp_path, monkeypatch):
    """Отчёт и лента — через safe_write: читатель посреди записи не видит обрезанный
    JSON (n611-in1 M9)."""
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    written = []
    real = graph_doctor.safe_write.write_text
    monkeypatch.setattr(graph_doctor.safe_write, "write_text",
                        lambda path, text, **kw: written.append(path.name) or real(path, text, **kw))
    monkeypatch.setattr(graph_doctor, "report_path", lambda: report)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: {})
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", "--graph", str(graph)])
    graph_doctor.main()
    assert written == ["graph_doctor.json", "graph_feed.json"]
