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
    assert graph_names.bare_namesakes("Саша", idx, owner="") == ["Саша Образцов", "Сидоров Саша"]
    assert graph_names.bare_namesakes("Саша (из минуток)", idx, owner="") == ["Саша Образцов", "Сидоров Саша"], \
        "скобка — пометка, не часть имени"
    assert graph_names.bare_namesakes("САША", idx, owner=""), "регистр имени не различает"


def test_full_name_label_or_lonely_name_is_not_collective():
    idx = graph_names.namesake_index(["Саша Образцов", "Собеседник 3 (Саша)"])
    assert graph_names.bare_namesakes("Саша Образцов", idx, owner="") == [], "полное имя — не голое"
    assert graph_names.bare_namesakes("Собеседник 3", idx, owner="") == [], "метка — не имя"
    assert graph_names.bare_namesakes("Гоша", idx, owner="") == [], "без тёзок — обычный узел"
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
    assert graph_names.bare_namesakes("Иван", idx, owner="") == ["Иван (Петров)"], "тёзка в скобочной форме"
    assert graph_names.bare_namesakes("Иван (Иванов)", idx, owner="") == [], "скобка с фамилией — полное имя"
    assert graph_names.bare_namesakes("Жан", idx, owner="") == [] and graph_names.bare_namesakes("Саша", idx, owner="") == []
    assert graph_names.bare_namesakes("Петр", idx, owner="") == ["Пётр Лесков"]
    assert graph_names.bare_namesakes("Саша 2", {"саша": ["Саша Образцов"]}, owner="") == [], \
        "имя с числом — копия, не голое (n611-s1-out2 DS M3)"


def test_bracket_tag_is_not_part_of_the_name():
    """Скобка-пометка снимается: метка с запятой, аббревиатура, перечень, имя узла
    Систем; узел с местом в скобке тёзок различает сам и не голый (n611-s1-out2:
    Sonnet I3, DS I1, Haiku M4)."""
    tags = frozenset({"витрина"})
    assert graph_names.name_words("Иван (Витрина)", tags) == ["иван"]
    assert graph_names.name_words("Иван (Витрина)") == ["иван", "витрина"], "без индекса Систем — как имя"
    assert graph_names.name_words("Пётр Лесков (ОТДЕЛ)") == ["петр", "лесков"]
    assert graph_names.name_words("Саша (Собеседник 1, муж)") == ["саша"]
    assert graph_names.name_words("Костя (Андрей, Олег и др.)") == ["костя"]
    assert graph_names.is_placeholder_node("Саша (Собеседник 1, муж)")
    idx = graph_names.namesake_index(["Илья Петров", "Илья (ОТДЕЛ)", "Иван (Витрина)"], tags)
    assert "отдел" not in idx and "витрина" not in idx and "иван" not in idx
    assert graph_names.bare_namesakes("Илья (ОТДЕЛ)", idx, owner="") == [], "место в скобке различает тёзок"
    assert graph_names.bare_namesakes("Илья", idx, owner="") == ["Илья Петров"]


def test_owner_full_name_is_not_a_namesake_but_owner_namesake_counts():
    """Узел владельца тёзкой не считается; голый «Саша» при владельце «Саша Образцов»
    и живом «Саша Сидоров» — сборный: под ним два человека (Sonnet I1, DS критика 2)."""
    idx = graph_names.namesake_index(["Саша Образцов", "Саша Сидоров", "Пётр Лесков"])
    assert graph_names.bare_namesakes("Саша", idx, owner="Саша Образцов") == ["Саша Сидоров"]
    assert graph_names.bare_namesakes("Пётр", idx, owner="Лесков Пётр") == [], "единственный тёзка — сам владелец"
    assert graph_names.bare_namesakes("Пётр", idx, owner="Пётр Иванович Лесков") == [], \
        "узел владельца без отчества — тоже владелец (Sonnet 7)"
    idx = graph_names.namesake_index(["Пётр Лесков (ОТДЕЛ)"])
    assert graph_names.bare_namesakes("Пётр", idx, owner="Пётр Лесков") == [], "пометка у узла владельца (DS I1)"


def test_one_word_owner_node_is_the_owner_not_collective():
    """Однословный владелец: голый узел с его именем — его собственный, конвейер
    пишет владельца ровно так; другой голый узел с тёзками — сборный (DS I2)."""
    idx = graph_names.namesake_index(["Игорь Ветров", "Саша Образцов"])
    assert graph_names.bare_namesakes("Игорь", idx, owner="Игорь") == []
    assert graph_names.bare_namesakes("Саша", idx, owner="Игорь") == ["Саша Образцов"]


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
    for text in ("спикер 3D выступил", "участник 14.09 решил", "участник 2-й линии", "Speaker 10x"):
        assert not graph_names.has_speaker_actor(text), text   # число — целым словом (out2 Sonnet I4)
    for text in ("Итог :(\n\nСобеседник 3 решил", "(см. прошлую\n[[Люди/Спикер 41]] решил", "Собеседник 3."):
        assert graph_names.has_speaker_actor(text), text   # незакрытая скобка метку не прячет (DS I3)
    assert not graph_names.has_speaker_actor("((Собеседник 3))")
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
    (graph / "Системы" / "ОТДЕЛ.md").write_text("# ОТДЕЛ\n", encoding="utf-8")   # пометка у человека — не тёзка
    (graph / "Люди" / "Гоша (Витрина).md").write_text("# Гоша (Витрина)\n", encoding="utf-8")
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
    """База — лента прошлой ночи, не число: известное не горит каждую ночь, «один слит,
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
    assert any("сборных узлов в Люди (голое имя при живых тёзках): 1" in w and "новых с прошлой ночи 1" in w
               for w in swapped["warnings"]), swapped["warnings"]


def test_note_linking_two_collective_nodes_counts_once(tmp_path):
    graph = _graph(tmp_path)
    (graph / "Люди" / "Гоша Лесков.md").write_text("# Гоша Лесков\n", encoding="utf-8")
    (graph / "Встречи" / "2026-10-03_1000.md").write_text("# В3\n- [[Люди/Гоша]] и [[Люди/Саша]]\n",
                                                          encoding="utf-8")
    rep = graph_doctor.inspect(graph, owner="Пётр Лесков")
    assert rep["collective"] == 2 and rep["collective_links"] == 3, rep   # В1, В2, В3 — три заметки


def _run(monkeypatch, report, *args, user_name="Пётр Лесков"):
    monkeypatch.setattr(graph_doctor, "report_path", lambda: report)
    monkeypatch.setattr(graph_updater, "load_cfg", lambda: {"sufler": {"user_name": user_name}})
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", *map(str, args)])
    return graph_doctor.main()


def _warned(report, graph) -> bool:
    rep = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    return any("сборных узлов" in w for w in rep["warnings"])


def test_main_writes_feed_beside_report_and_keeps_report_lean(tmp_path, monkeypatch):
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    assert _run(monkeypatch, report, "--graph", graph) == 0

    rep = json.loads(report.read_text(encoding="utf-8"))["graphs"][str(graph)]
    feed = json.loads((report.parent / "graph_feed.json").read_text(encoding="utf-8"))["graphs"][str(graph)]
    assert "feed" not in rep and rep["collective"] == 1
    assert [c["node"] for c in feed["collective"]] == ["Люди/Саша.md"], "владелец из конфига — не сборный"
    assert not (report.parent / "graph_feed_base.json").exists(), "базу двигает только --advance-base"


def test_both_night_passes_warn_and_the_final_one_moves_the_base(tmp_path, monkeypatch):
    """Ночь зовёт доктора дважды: ранний проход и итоговый сравнивают с базой прошлой
    ночи, и новое горит в позднем брифе; базу двигает итоговый, следующая ночь молчит
    (n611-s1-out2 C1). Ручной прогон днём базу не трогает."""
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    _run(monkeypatch, report, "--graph", graph)
    assert _warned(report, graph), "ранний проход"
    _run(monkeypatch, report, "--graph", graph, "--advance-base")
    assert _warned(report, graph), "итоговый проход той же ночи — предупреждение доживает до брифа"
    _run(monkeypatch, report, "--graph", graph)
    assert not _warned(report, graph), "следующая ночь — известное не горит"
    (graph / "Люди" / "Гоша Лесков.md").write_text("# Гоша Лесков\n", encoding="utf-8")
    _run(monkeypatch, report, "--graph", graph)
    _run(monkeypatch, report, "--graph", graph)
    assert _warned(report, graph), "ручной прогон днём базу не сдвинул"


def test_base_keeps_graphs_outside_this_run(tmp_path, monkeypatch):
    """`--graph` и граф, не прочитанный этой ночью, не стирают чужую запись базы
    (Sonnet 6, Haiku I5)."""
    one, two = _graph(tmp_path / "a"), _graph(tmp_path / "b")
    report = tmp_path / "logs" / "graph_doctor.json"
    _run(monkeypatch, report, "--graph", one, "--advance-base")
    _run(monkeypatch, report, "--graph", two, "--advance-base")
    base = json.loads((report.parent / "graph_feed_base.json").read_text(encoding="utf-8"))["graphs"]
    assert set(base) == {str(one), str(two)}
    _run(monkeypatch, report, "--graph", one)
    assert not _warned(report, one)


def test_unwritten_report_fails_and_leaves_the_base(tmp_path, monkeypatch):
    """Отчёт не записан — код 1, база стоит: новое не уходит в базу мимо брифа
    (Sonnet I2, DS I4, Haiku I1)."""
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    real = graph_doctor.safe_write.write_text

    def flaky(path, text, **kw):
        if path.name == "graph_doctor.json":
            raise OSError("диск полон")
        return real(path, text, **kw)

    monkeypatch.setattr(graph_doctor.safe_write, "write_text", flaky)
    assert _run(monkeypatch, report, "--graph", graph, "--advance-base") == 1
    assert not (report.parent / "graph_feed_base.json").exists()


def test_hand_edited_report_or_odd_owner_does_not_crash(tmp_path, monkeypatch):
    """Запись графа в отчёте не словарь, имя владельца не строка — доктор работает
    (DS M1, Sonnet 7)."""
    graph = _graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"graphs": {str(graph): 1}}), encoding="utf-8")
    assert _run(monkeypatch, report, "--graph", graph, user_name=2026) == 0


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
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", "--graph", str(graph), "--advance-base"])
    graph_doctor.main()
    assert written[2:] == ["graph_doctor.json", "graph_feed.json", "graph_feed_base.json"]
