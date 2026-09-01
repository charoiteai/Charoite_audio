"""Канон написаний из графа (№149): фамилии и аббревиатуры СТТ.

Живой прецедент 01.09: «Гельского» в стенограмме вместо «Вельского» —
потерялось единственное именное поручение встречи, при том что узел графа
знал девять вариантов фамилии в aliases.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import lexicon


def _graph(tmp_path):
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Системы").mkdir()
    (g / "Люди" / "Вельский Ян.md").write_text(
        '---\ntype: person\nотдел: "2-я линия поддержки"\n'
        'aliases: ["Гельский", "Ельский", "крам-неалиас"]\n---\n'
        "# Вельский Ян\nМониторинг DAG, вторая линия.\n",
        encoding="utf-8")
    (g / "Системы" / "КРАМ.md").write_text(
        "---\ntype: system\n---\n# КРАМ\nСистема планирования.\n",
        encoding="utf-8")
    (g / "Люди" / "_ЛЮДИ.md").write_text("| узел |\n", encoding="utf-8")
    return g


def test_alias_replaced_with_case_preserved(tmp_path):
    lex = lexicon.load(_graph(tmp_path))
    out, rep = lexicon.apply("Задачу поставили на Гельского, Гельскому сказали.", lex)
    assert "Вельского" in out and "Вельскому" in out, out
    assert "Гельск" not in out
    assert len(rep) == 2


def test_abbrev_upcased_by_node_name(tmp_path):
    lex = lexicon.load(_graph(tmp_path))
    out, rep = lexicon.apply("передали в крам и обратно", lex)
    assert "в КРАМ и" in out, out
    assert rep == ["крам→КРАМ"]


def test_similar_but_not_alias_untouched(tmp_path):
    """«Похожее» без алиаса НЕ заменяется — только в кандидаты."""
    lex = lexicon.load(_graph(tmp_path))
    text = "Пришёл Мельский с отчётом по мониторингу второй линии."
    out, rep = lexicon.apply(text, lex)
    # «Мельский» похож на канон (1 буква), но алиасом не объявлен:
    assert out == text and rep == []
    cand = lexicon.candidates(text, lex)
    assert cand and "мельский ~ вельский" in cand[0].lower()
    assert "✔" in cand[0], cand  # контекст «мониторинг/линии» совпал с узлом


def test_candidate_without_context_is_question(tmp_path):
    lex = lexicon.load(_graph(tmp_path))
    cand = lexicon.candidates("Просто Вэльский зашёл в кабинет молча.", lex)
    assert cand and "?" in cand[0] and "✔" not in cand[0], cand


def test_unrelated_names_untouched(tmp_path):
    lex = lexicon.load(_graph(tmp_path))
    text = "Слуцкий и Петров обсуждали КРАМ."
    out, rep = lexicon.apply(text, lex)
    assert out == text and rep == []


def test_canon_is_idempotent(tmp_path):
    lex = lexicon.load(_graph(tmp_path))
    once, _ = lexicon.apply("Вельского попросили про КРАМ.", lex)
    twice, rep = lexicon.apply(once, lex)
    assert once == twice and rep == []


def test_rebuild_wires_canonize():
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def write_final(")]
    assert "canonize(final_text, cfg)" in fn or "final_text = canonize(" in fn
    assert "canonize_file(live.with_name" in fn


def test_first_name_alias_never_maps_to_surname(tmp_path):
    """Живой смоук 01.09: алиас-ИМЯ («Марк» у узла «Ветров Марк»)
    превращал каждого Марка текста в Ветрова, а «Степанян» — во
    «Владимира». Имя и фамилия не делят суффикс основ — правило не
    создаётся, алиас остаётся поисковым синонимом."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Ветров Марк.md").write_text(
        '---\ntype: person\naliases: ["Марк", "Витров"]\n---\n# Ветров Марк\n',
        encoding="utf-8")
    lex = lexicon.load(g)
    text = "Марк сказал, что Витрова не будет."
    out, rep = lexicon.apply(text, lex)
    assert "Марк сказал" in out, out          # имя не тронуто
    assert "Ветрова не будет" in out, out       # искажение фамилии починено
    assert rep == ["Витрова→Ветрова"], rep


def test_two_letter_abbrev_node_is_ignored(tmp_path):
    """Узел «ВО» канонизировал предлог «во» по всей стенограмме (живой
    смоук 01.09): двухбуквенные аббревиатуры в правила не идут."""
    g = tmp_path / "g"
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "ВО.md").write_text("---\ntype: system\n---\n# ВО\n", encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("во дворе во всём", lex)
    assert rep == [] and out == "во дворе во всём"
