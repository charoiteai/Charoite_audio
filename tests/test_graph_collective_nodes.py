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


def test_name_words_follow_the_name_not_the_punctuation():
    """Скобка с заглавной — часть имени, как у name_key; дефис не режет; «ё» = «е»;
    число — копия, не слово (n611-s1-out1: DS I1, Haiku I1/M1, Sonnet I3)."""
    assert graph_names.name_words("Дмитрий (Лесков)") == ["дмитрий", "лесков"]
    assert graph_names.name_words("Саша (из минуток)") == ["саша"]
    assert graph_names.name_words("Вера (Собеседник 4)") == ["вера"]
    assert graph_names.name_words("Жан-Пьер") == ["жан-пьер"]
    assert graph_names.name_words("Саша 2") == ["саша"]
    idx = graph_names.namesake_index(["Иван (Петров)", "Жан-Пьер", "Саша 2", "Пётр Лесков"])
    assert graph_names.bare_namesakes("Иван", idx) == ["Иван (Петров)"], "тёзка в скобочной форме"
    assert graph_names.bare_namesakes("Иван (Иванов)", idx) == [], "скобка с фамилией — полное имя"
    assert graph_names.bare_namesakes("Жан", idx) == [] and graph_names.bare_namesakes("Саша", idx) == []
    assert graph_names.bare_namesakes("Петр", idx) == ["Пётр Лесков"]


def test_owner_full_name_is_not_a_namesake_but_owner_namesake_counts():
    """Узел владельца тёзкой не считается; голый «Саша» при владельце «Саша Образцов»
    и живом «Саша Сидоров» — сборный: под ним два человека (Sonnet I1, DS критика 2)."""
    idx = graph_names.namesake_index(["Саша Образцов", "Саша Сидоров", "Пётр Лесков"])
    assert graph_names.bare_namesakes("Саша", idx, owner="Саша Образцов") == ["Саша Сидоров"]
    assert graph_names.bare_namesakes("Пётр", idx, owner="Лесков Пётр") == [], "единственный тёзка — сам владелец"


def test_speaker_label_as_actor_in_text():
    assert graph_names.has_speaker_actor("Собеседник 3 предложил перенести релиз")
    assert graph_names.has_speaker_actor("- [[Люди/Собеседник 2]] — за")
    assert graph_names.has_speaker_actor("Speaker 2 agreed")
    assert graph_names.has_speaker_actor("собеседник №4 против")
    for text in ("слова Собеседника 3", "Участник 2 предложил", "Спикер-3 за", "Собеседник #3 за",
                 "发言人 3 决定", "Собеседник 3 (Саша) решил"):
        assert graph_names.has_speaker_actor(text), text   # словарь меток — один с _PLACEHOLDER_RE (DS I3)
    assert not graph_names.has_speaker_actor("Вера (Собеседник 4) сказала"), "пометка после имени — не деятель"
    assert not graph_names.has_speaker_actor("Вера (муж, Собеседник 4) за"), "внутри скобки — пометка"
    assert not graph_names.has_speaker_actor("(Спикер 31 и Спикер 32)")
    assert not graph_names.has_speaker_actor("спикер 2024 года"), "год — не номер метки"
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
    (graph / "Люди" / "Вера (ОТДЕЛ).md").write_text("# Вера (ОТДЕЛ)\n", encoding="utf-8")
    (graph / "Системы" / "ОТДЕЛ.md").write_text("# ОТДЕЛ\n", encoding="utf-8")   # аббревиатура — система
    (graph / "Досье" / "Релиз.md").write_text("# Релиз\nСобеседник 2 решил ждать.\n", encoding="utf-8")
    (graph / "Досье" / "Бюджет.md").write_text("# Бюджет\nВера (Собеседник 4) за.\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-01_1000.md").write_text(
        "# В1\n- [[Люди/Саша]] и снова [[Люди/Саша|Саша]]\n- [[Люди/Пётр]]\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-02_1000.md").write_text("# В2\n- [[Саша]]\n", encoding="utf-8")
    return graph


def test_doctor_counts_collective_nodes_without_owner_stub_or_label(tmp_path):
    """«Саша» при двух тёзках — сборный; «Пётр», чей единственный тёзка — сам
    владелец, — нет; заглушка «Олег» —
    нет; «Вера (Собеседник 4)» — метка, у неё свой счёт. Ссылки — по заметкам:
    две ссылки из одной встречи — одна встреча."""
    graph = _graph(tmp_path)
    before = {p: p.read_bytes() for p in graph.rglob("*.md")}

    rep = graph_doctor.inspect(graph, examples=5, owner="Пётр Лесков")

    assert rep["collective"] == 1 and rep["collective_multi"] == 1 and rep["collective_links"] == 2, rep
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


def test_collective_warning_fires_on_new_nodes_only(tmp_path):
    """База — прошлая лента, не число: известное не горит каждую ночь, «один слит,
    другой появился» — горит (Sonnet M3). Досье — без предупреждения: пересобираются
    каждую ночь (критика Sonnet 2)."""
    graph = _graph(tmp_path)
    first = graph_doctor.inspect(graph, owner="Пётр Лесков")
    assert any("сборных узлов в Люди" in w and "новых" not in w for w in first["warnings"]), first["warnings"]
    assert any("людей узлами в Системах: 1;" in w for w in first["warnings"])
    assert not [w for w in first["warnings"] if "досье" in w]

    same = graph_doctor.inspect(graph, owner="Пётр Лесков", prev_feed=first["feed"])
    assert not [w for w in same["warnings"] if "graph_feed" in w], "известное владельцу не горит каждую ночь"

    (graph / "Люди" / "Саша.md").write_text("---\ntags: [дубль-слит]\n---\n# Саша → см. [[Люди/Саша Образцов]]\n",
                                            encoding="utf-8")
    (graph / "Люди" / "Гоша Лесков.md").write_text("# Гоша Лесков\n", encoding="utf-8")
    swapped = graph_doctor.inspect(graph, owner="Пётр Лесков", prev_feed=first["feed"])
    assert swapped["collective"] == 1
    assert any("сборных узлов в Люди (голое имя при живых тёзках): 1" in w and "новых с прошлого прогона 1" in w
               for w in swapped["warnings"]), swapped["warnings"]


def test_note_linking_two_collective_nodes_counts_once(tmp_path):
    graph = _graph(tmp_path)
    (graph / "Люди" / "Гоша Лесков.md").write_text("# Гоша Лесков\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-03_1000.md").write_text("# В3\n- [[Люди/Гоша]] и [[Люди/Саша]]\n",
                                                          encoding="utf-8")
    rep = graph_doctor.inspect(graph, owner="Пётр Лесков")
    assert rep["collective"] == 2 and rep["collective_links"] == 3, rep   # В1, В2, В3 — три заметки


def test_main_writes_feed_beside_report_and_keeps_report_lean(tmp_path, monkeypatch):
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    monkeypatch.setattr(graph_doctor, "report_path", lambda: report)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: {"sufler": {"user_name": "Пётр Лесков"}})
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", "--graph", str(graph)])
    graph_doctor.main()

    rep = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    feed = json.loads((report.parent / "graph_feed.json").read_text(encoding="utf-8"))["graphs"][str(graph)]
    assert "feed" not in rep and rep["collective"] == 1
    assert [c["node"] for c in feed["collective"]] == ["Люди/Саша.md"], "владелец из конфига — не сборный"

    report.unlink()       # ночь при сбое доктора стирает отчёт — база в ленте остаётся (DS I2)
    graph_doctor.main()
    again = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    assert not [w for w in again["warnings"] if "graph_feed" in w], again["warnings"]


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
