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
        '---\ntype: system\naliases: ["крам"]\n---\n# КРАМ\nСистема планирования.\n',
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


def test_alias_matching_other_nodes_surname_makes_no_rule(tmp_path):
    """DS C1 по #469: алиас «Гельский» у Вельского не переписывает
    реального Гельского Ивана с собственным узлом — правило не создаётся."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Вельский Ян.md").write_text(
        '---\naliases: ["Гельский"]\n---\n# Вельский Ян\n', encoding="utf-8")
    (g / "Люди" / "Гельский Иван.md").write_text(
        "---\n---\n# Гельский Иван\n", encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("Спроси у Гельского Ивана про отчёт", lex)
    assert rep == [] and "Гельского Ивана" in out, (rep, out)


def test_lowercase_common_noun_not_replaced_by_person_alias(tmp_path):
    """DS C2 по #469: алиас «Диктор» у Виктора не смеет переписывать
    строчного «диктора» — класс «хосты» через дверь персон."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Петров Виктор.md").write_text(
        '---\naliases: ["Диктор"]\n---\n# Петров Виктор\n', encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("говорил как диктор по радио", lex)
    assert rep == [] and out == "говорил как диктор по радио"
    # Заглавное STT-искажение при этом чинится:
    out2, rep2 = lexicon.apply("Задачу Диктору передали.", lex)
    assert rep2 == ["Диктору→Виктору"], rep2


def test_abbrev_node_without_alias_makes_no_rule(tmp_path):
    """DS I1 + GLM-3 по #469: капс-узел («МИР», «ПОЧТА») сам по себе не
    перекапсит частотное слово — строчная форма канонизируется только
    явным алиасом, гейт длины от «ПОЧТА» не спасал."""
    g = tmp_path / "g"
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "МИР.md").write_text("---\n---\n# МИР\n", encoding="utf-8")
    (g / "Системы" / "ПОЧТА.md").write_text("---\n---\n# ПОЧТА\n", encoding="utf-8")
    (g / "Системы" / "КРАМ.md").write_text(
        '---\naliases: ["крам"]\n---\n# КРАМ\n', encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("мир во всём мире, почта висит, крам работает", lex)
    assert rep == ["крам→КРАМ"], rep
    assert out.startswith("мир во всём мире, почта висит")


def test_shared_alias_of_two_nodes_is_dropped(tmp_path):
    """GLM-1 по #469: один алиас у двух узлов — победителя выбирал бы
    порядок glob; правило снимается, неоднозначное слово не трогается."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Вельский Ян.md").write_text(
        '---\naliases: ["Дельский"]\n---\n# Вельский Ян\n', encoding="utf-8")
    (g / "Люди" / "Мельский Лев.md").write_text(
        '---\naliases: ["Дельский"]\n---\n# Мельский Лев\n', encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("Дельского ждали к девяти.", lex)
    assert rep == [] and "Дельского" in out, (rep, out)
    assert lex.shared_alias >= 1


def test_lowercase_words_are_not_candidates(tmp_path):
    """GLM-4 отклонён данными смоука: строчные кандидаты либо дают ложные
    ✔ («машина ~ марина»), либо жёсткий гейт теряет целевой класс.
    Строчное — не кандидат; тема v2 вместе с фонетикой."""
    lex = lexicon.load(_graph(tmp_path))
    assert lexicon.candidates(
        "задачу получил мельский по мониторингу второй линии", lex) == []


def test_related_name_of_other_person_not_candidate(tmp_path):
    """Живой смоук: «Никитина» (узел Ольга Никитина) не кандидат к узлу
    «Никита» — это чьё-то каноническое имя; «Виктор» не кандидат к
    «Виктория» — родственные имена не делят суффикс основ."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Никита.md").write_text("---\n---\n# Никита\nДанные.\n", encoding="utf-8")
    (g / "Люди" / "Ольга Никитина.md").write_text(
        "---\n---\n# Ольга Никитина\nДанные отчёта.\n", encoding="utf-8")
    (g / "Люди" / "Виктория Юрьевна.md").write_text(
        "---\n---\n# Виктория Юрьевна\nСогласование данных.\n", encoding="utf-8")
    lex = lexicon.load(g)
    cand = lexicon.candidates(
        "Никитина и Виктор обсуждали данные отчёта и согласование.", lex)
    assert cand == [], cand


def test_canon_inflection_is_not_a_candidate(tmp_path):
    """DS I2 по #469: правильно склонённый канон («Вельским», дистанция 1)
    не мусорит отчёт кандидатов на каждой пересборке."""
    lex = lexicon.load(_graph(tmp_path))
    cand = lexicon.candidates("Работу согласовали с Вельским вчера.", lex)
    assert cand == [], cand


def test_all_caps_word_stays_all_caps(tmp_path):
    """DS M4 по #469: «ГЕЛЬСКОГО» в шапке не схлопывается в «Вельского»."""
    lex = lexicon.load(_graph(tmp_path))
    out, rep = lexicon.apply("СЛУШАЛИ ГЕЛЬСКОГО ПО ОТЧЁТУ", lex)
    assert "ВЕЛЬСКОГО" in out, out


def test_block_alias_trailing_punctuation_stripped(tmp_path):
    """DS M7 по #469: хвостовая запятая блочного алиаса не убивает правило."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Вельский Ян.md").write_text(
        "---\naliases:\n  - Гельский,\n---\n# Вельский Ян\n", encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("Гельского ждали к девяти.", lex)
    assert rep == ["Гельского→Вельского"], rep


def test_dropped_rule_stays_dropped(tmp_path):
    """Critical круга 2 (DS+GLM): второй алиас той же основы у второго
    узла воскрешал снятое правило — победителя снова выбирал порядок
    glob. Снятие липкое на весь load()."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Вельский Ян.md").write_text(
        '---\naliases: ["Дельский"]\n---\n# Вельский Ян\n', encoding="utf-8")
    (g / "Люди" / "Мельский Лев.md").write_text(
        '---\naliases: ["Дельский", "Дельская"]\n---\n# Мельский Лев\n',
        encoding="utf-8")
    lex = lexicon.load(g)
    out, rep = lexicon.apply("Дельского ждали к девяти.", lex)
    assert rep == [] and "Дельского" in out, (rep, out)
    assert lex.shared_alias >= 2


def test_namesake_substring_node_keeps_merged_context(tmp_path):
    """GLM-2/DS-5 круга 2: «Никита» — подстрока «Никита Соколов», и
    проверка по подстроке затирала контекст длинного тёзки."""
    g = tmp_path / "g"
    (g / "Люди").mkdir(parents=True)
    (g / "Люди" / "Никита Соколов.md").write_text(
        "---\n---\n# Никита Соколов\nДеплой сервера отчёта.\n", encoding="utf-8")
    (g / "Люди" / "Никита.md").write_text(
        "---\n---\n# Никита\n\n", encoding="utf-8")
    lex = lexicon.load(g)
    node, ctx = lex.context["никита"]
    assert "Никита Соколов" in node.split("; ") and "Никита" in node.split("; "), node
    assert any(c.startswith("депл") for c in ctx), ctx
