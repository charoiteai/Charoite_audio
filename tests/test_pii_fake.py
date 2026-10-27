"""Подставные имена вместо меток маски (№793, решение 26.10 по замеру №799, плечо C)."""
from __future__ import annotations

import pathlib
import random
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pii_mask as pm  # noqa: E402

import pymorphy3  # noqa: E402,F401 — зависимость продукта (n793-out5 DS-C1): без неё тест красный, не skip
import cloud_door  # noqa: E402
import pii_fake  # noqa: E402

NODES = (pm.NodeForms("Дмитрий Орлов", ("Дима",)), pm.NodeForms("Анна Белова"),
         pm.NodeForms("ЗЛК", person=False), pm.NodeForms("Старый офис", person=False))
KNOWN = {"старый", "офис"}.__contains__
SRC = ("Дима сказал Анне Беловой, что ЗЛКа нет. Диму и Орлова ждём. "
       "Позвони Дмитрию Орлову. Старым офисом занялись, в Старом офисе пусто.")


def _dressed(src=SRC, nodes=NODES, seed=1):
    forms = pm.build_forms(nodes, known=KNOWN, lexeme=pii_fake.same_lexeme)   # как `cloud_door._forms`
    m = pm.mask(src, forms)
    d = pii_fake.dress(m, src, forms, seed=seed)
    return m, d, forms


def _lemmas(word: str) -> set[str]:
    return pii_fake._lemmas(pii_fake._load(), word)[0]


def test_one_fake_per_node_in_the_case_of_each_form():
    """Все формы и псевдонимы узла — одна подделка, падеж формы переносится: «Дима»,
    «Диму», «Дмитрию» — одно подставное имя (след меток №799: «Дим/Диму» — два человека)."""
    m, d, _f = _dressed()
    assert "⟦" not in m.text and not any(n in m.text for n in ("Дим", "Бел", "Орлов", "ЗЛК", "офис"))
    by_orig = dict(d.pairs["Ч1"])
    names = [by_orig[f].split()[0] for f in ("Дима", "Диму", "Дмитрию Орлову")]
    assert len(set.intersection(*(_lemmas(n) for n in names))) >= 1, names
    assert len(set(names)) == 3, "падеж разный — и форма подделки разная"
    morph = pii_fake._load()
    anna = dict(d.pairs["Ч2"])["Анне Беловой"].split()
    assert any("datv" in p.tag for p in morph.parse(anna[0])), anna


def test_fake_never_matches_the_graph_or_the_sent_text():
    """Коллизии: подделка — не форма словаря графа и не слово текста. Текст занимает
    мужские имена пула, кроме первого и последнего, первое занято узлом графа, —
    выбрано обязано быть последнее."""
    pii_fake._load()
    pool = pii_fake._pools["masc"]
    keep = pool[-1]
    # первое имя — только в графе: в тексте оно стало бы второй группой и само просило подделку
    src = "Дима пришёл. " + " ".join(pool[1:-1])
    # узлы графа с именем из пула: его формы тоже заняты
    nodes = (pm.NodeForms("Дмитрий Орлов", ("Дима",)), pm.NodeForms(pool[0] + " Сидоров"))
    m, d, forms = _dressed(src, nodes)
    fake = dict(d.pairs["Ч1"])["Дима"]
    assert fake == keep
    for pairs in d.pairs.values():
        for _o, f in pairs:
            assert not pm._dictionary_spans(f, forms)


def test_pool_exhausted_is_a_refusal(monkeypatch):
    """Оба яруса заняты — отказ, а не повтор подделки."""
    pii_fake._load()
    monkeypatch.setitem(pii_fake._pools, "masc2", [])
    src = "Дима пришёл. " + " ".join(pii_fake._pools["masc"])
    forms = pm.build_forms(NODES, known=KNOWN)
    m = pm.mask(src, forms)
    with pytest.raises(pm.MaskRefused, match="пул"):
        pii_fake.dress(m, src, forms, seed=1)


def test_first_tier_taken_falls_to_the_second():
    pii_fake._load()
    src = "Дима пришёл. " + " ".join(pii_fake._pools["masc"])
    forms = pm.build_forms(NODES, known=KNOWN)
    m = pm.mask(src, forms)
    d = pii_fake.dress(m, src, forms, seed=1)
    assert dict(d.pairs["Ч1"])["Дима"] in pii_fake._pools["masc2"]


def test_a_meeting_of_a_hundred_and_twenty_people_gets_distinct_fakes():
    """Замер №793: в промпте встречи до 90 людей (85 мужчин), первый ярус — 75 имён и
    46 фамилий; c0107 падала отказом «пул исчерпан». Узлы — из второго яруса, чтобы и
    он был частью занят."""
    import pii_fake_pool
    people = [f"{n} {s}" for n, s in zip(pii_fake_pool.MASC[:120], pii_fake_pool.SURNAMES[:120])]
    nodes = tuple(pm.NodeForms(x) for x in people)
    src = " ".join(f"{x} пришёл." for x in people)
    forms = pm.build_forms(nodes, known=KNOWN)
    m = pm.mask(src, forms)
    d = pii_fake.dress(m, src, forms, seed=1)
    fakes = [f for prs in d.pairs.values() for _o, f in prs]
    assert len(d.pairs) == 120 and len(set(fakes)) == len(fakes)
    assert not any(w in m.text for x in people for w in x.split())


def test_fakes_change_between_requests_and_hold_within_one():
    """Облако не связывает узел между запросами: подделки новые на каждый запрос."""
    seen = {dict(_dressed(seed=s)[1].pairs["Ч2"])["Анне Беловой"] for s in range(6)}
    assert len(seen) > 1
    assert dict(_dressed(seed=3)[1].pairs["Ч2"]) == dict(_dressed(seed=3)[1].pairs["Ч2"])


def test_reply_comes_back_to_the_original_forms():
    m, d, _f = _dressed()
    back, left = pii_fake.undress(m.text, d, sent=m.text)
    assert back == SRC and left == 0
    assert d.back["exact"] > 0 and d.back["canon"] == 0


def test_other_case_of_the_fake_comes_back_in_that_case():
    """Модель склонила подделку иначе, чем в тексте: исходное имя — в падеже ответа."""
    m, d, _f = _dressed("Анна Белова пришла.", (pm.NodeForms("Анна Белова"),))
    name, surname = dict(d.pairs["Ч1"])["Анна Белова"].split()
    morph = pii_fake._load()
    datv = pii_fake._inflect(morph, name, "datv", "sing", "femn", "Name")
    back, left = pii_fake.undress(f"Поручение {datv} {surname[:-1]}ой.", d, sent=m.text)
    assert back == "Поручение Анне Беловой." and left == 0 and d.back["case"] == 2


def test_link_to_a_fake_is_the_node_name():
    """`[[Подделка в падеже]]` — ссылка на узел: каноном, иначе ссылка битая."""
    m, d, _f = _dressed("Анну Белову ждём.", (pm.NodeForms("Анна Белова"),))
    fake = dict(d.pairs["Ч1"])["Анну Белову"]
    back, _n = pii_fake.undress(f"см. [[{fake}]]", d, sent=m.text)
    assert back == "см. [[Анна Белова]]"


def test_new_name_of_the_model_is_counted_not_touched():
    m, d, _f = _dressed()
    back, left = pii_fake.undress(m.text + " Ещё звонила Марина.", d, sent=m.text)
    assert back.endswith("Ещё звонила Марина.") and d.back["new"] == 1 and left == 0


def test_fake_inside_a_hyphenated_word_comes_back():
    """«Аметист-луп» в ответе №799 осталось подделкой: слово делится по дефису."""
    m, d, _f = _dressed("Старый офис лежит.", (pm.NodeForms("Анна Белова"),
                                              pm.NodeForms("Старый офис", person=False)))
    fake = dict(d.pairs["С1"])["Старый офис"]
    back, left = pii_fake.undress(f"{fake}-луп", d, sent=m.text)
    assert back == "Старый офис-луп" and left == 0


def test_missing_morphology_refuses(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymorphy3", None)
    monkeypatch.setattr(pii_fake, "_morph", None)
    with pytest.raises(pm.MaskRefused, match="pymorphy3"):
        pii_fake._load()


def test_every_pool_word_declines():
    """В пуле только склоняемое: «Есения» осталась бы в именительном («сказал Есения»)."""
    morph = pii_fake._load()
    for g in ("masc", "femn"):
        assert len(pii_fake._pools[g]) >= 40 and len(pii_fake._pools[g + "2"]) >= 400
        for x in pii_fake._pools[g] + pii_fake._pools[g + "2"]:
            assert pii_fake._inflect(morph, x, "datv", "sing", g, "Name") != x


def test_link_with_a_fake_among_other_words_keeps_the_target():
    """Узел — только когда вся цель ссылки из его подделок: «[[Встреча с <подделка> 12.10]]» —
    заметка встречи; переписать её в «[[Дмитрий Орлов]]» значит записать в граф чужую ссылку
    (n793-out4 H-C1)."""
    src = "Дима пришёл, см. [[Встреча с Димой 12.10]] и [[Дима]]."
    m, d, _f = _dressed(src, NODES[:1])
    back, _left = pii_fake.undress(m.text, d, sent=m.text)
    assert back == src.replace("[[Дима]]", "[[Дмитрий Орлов]]")


def test_lowercase_word_of_the_reply_is_not_a_capitalised_fake(monkeypatch):
    """Подделка «Корней» ушла с заглавной — «причины корней» в ответе остаются словом
    (n793-out4 H-C2); «Корней сказал» — подделка."""
    pii_fake._load()
    monkeypatch.setitem(pii_fake._pools, "masc", ["Корней"])
    m, d, _f = _dressed("Дима пришёл.", NODES[:1])
    assert m.text == "Корней пришёл."
    back, left = pii_fake.undress("Разберём причины корней проблемы. Корней сказал.", d, sent=m.text)
    assert back == "Разберём причины корней проблемы. Дима сказал." and left == 0


def test_fake_left_unresolved_goes_out_marked():
    """Подделка, которая разбирается в две группы, не разворачивается и уходит в граф
    помеченной «⟦?…⟧», как обещают сообщения вызывающих (n793-out4 S-I3)."""
    m, d, _f = _dressed()
    fake = dict(d.pairs["Ч1"])["Дима"]
    d.pairs["Ч9"] = [("Петя", fake)]
    d.canon["Ч9"] = None
    back, left = pii_fake.undress(f"{fake} молчал.", d, sent=m.text)
    assert back == f"⟦?{fake}⟧ молчал." and left == 1
    # счёт доходит до вызывающих через дверь: по нему ночь не пишет досье (n793-out19 DS-I1)
    m.dress = d
    assert cloud_door.unmask_reply(f"{fake} молчал.", m) == (back, 1)


def test_fake_patronymic_never_equals_the_real_one():
    """Отчество строится из свободного имени пула и мимо `taken`: «Тимофей» свободен, а
    «Тимофеевич» — настоящее отчество узла (n793-out5 DS-C3, на сиде 24 совпадало)."""
    src = "Андрей Тимофеевич Орлов пришёл. Андрей Тимофеевич сказал."
    nodes = (pm.NodeForms("Андрей Тимофеевич Орлов"),)
    for seed in range(60):
        _m, d, _f = _dressed(src, nodes, seed=seed)
        fakes = " ".join(f for prs in d.pairs.values() for _o, f in prs)
        assert "Тимофеев" not in fakes, (seed, fakes)


def test_two_words_of_one_role_get_two_fakes():
    """«Иванов-Петров»: две фамилии одной формы — две подделки; одна давала разворот
    «Иванов-Иванов» (n793-out5 DS-C4)."""
    for src, node in (("Иванов-Петров пришёл.", "Иванов-Петров"), ("Анна-Мария пришла.", "Анна-Мария")):
        m, d, _f = _dressed(src, (pm.NodeForms(node),))
        a, b = m.text.split()[0].split("-")
        assert a != b, m.text
        assert pii_fake.undress(m.text, d, sent=m.text)[0] == src


def test_single_second_word_keeps_its_own_fake():
    """«Анна-Мария» и отдельно «Мария»: одиночное слово — вторая подделка пары, а не первая;
    иначе «Мария ушла» развернулось бы в «Анна ушла» (n793-out6 H-C1 = S-I1)."""
    for src, node, alone in (("Анна-Мария пришла. Мария ушла.", "Анна-Мария", "Мария ушла."),
                             ("Иванов-Петров пришёл. Петров ушёл.", "Иванов-Петров", "Петров ушёл.")):
        for seed in range(3):
            m, d, _f = _dressed(src, (pm.NodeForms(node),), seed=seed)
            reply = m.text.split(". ")[1]
            assert pii_fake.undress(reply, d, sent=m.text)[0] == alone, (seed, m.text)


def test_initials_stay_and_survive_a_rewritten_form():
    """Инициалы в спане NER (фамилия и две буквы) уходят как есть: модель переписывает форму
    («Фамилия И.», «И. П. Фамилия»), и подставные буквы ушли бы в граф чужими (n793-out7
    S-C1 = DS-C1). Фамилия — подделка, обратно верно в любой записи."""
    src = "Орлов И.П. пришёл. Иван Орлов ушёл."  # приватный-образец: синтетические инициалы
    forms = pm.build_forms((pm.NodeForms("Иван Орлов"),))
    for seed in range(5):
        m = pm.mask(src, forms, [(0, 10, pm.PERSON)])
        d = pii_fake.dress(m, src, forms, seed=seed)
        sur = m.text.split()[0]
        assert sur != "Орлов" and m.text.split()[1] == "И.П."  # приватный-образец: синтетические инициалы
        assert pii_fake.undress(m.text, d, sent=m.text)[0] == src
        assert pii_fake.undress(f"Вёл {sur} И.", d, sent=m.text)[0] == "Вёл Орлов И."
        assert pii_fake.undress(f"И. П. {sur} пришёл", d, sent=m.text)[0] == "И. П. Орлов пришёл"


@pytest.mark.parametrize("node, src, back", [
    (("Пётр Кузнец",), "Кузнец пришёл. Пётр Кузнец ушёл.", None),
    (("Анна-Мария", ("Аня",)), "Аня пришла. Анна-Мария ушла.", None),
    (("Анна-Мария", ("Аня",)), "Анна-Мария пришла. Аня ушла.", None),
    (("Иванович Кузнец",), "Иванович Кузнец пришёл. Кузнец ушёл.", None),
    # «Дима» и «Дмитрий» — одна подделка (решение №799), обратно — слово канона (out9 S-M1)
    (("Дмитрий Орлов", ("Дима",)), "Дима пришёл. Дмитрий Орлов ушёл.", "Дмитрий пришёл. Дмитрий Орлов ушёл."),
    # одна подделка фамилии у двух форм одного человека — обратно форма канона узла
    (("Анна Иванова", ("Анна Петрова",)), "Анна Иванова пришла. Анна Петрова ушла.",
     "Анна Иванова пришла. Анна Иванова ушла."),
    (("Анна Иванова", ("Анна Петрова",)), "Анна Петрова пришла. Анна Иванова ушла.",
     "Анна Иванова пришла. Анна Иванова ушла.")])
def test_different_words_of_a_group_never_share_a_fake(node, src, back):
    """Слово без тега словаря («Кузнец») и псевдоним вне многословной формы («Аня») в любом
    порядке не делят подделку с другим словом группы (n793-out7 DS-C2, out8 S-I1). Где
    подделка общая, разворот не берёт первое попавшееся — только канон (out8 DS-C1)."""
    for seed in range(5):
        m, d, _f = _dressed(src, (pm.NodeForms(*node),), seed=seed)
        assert pii_fake.undress(m.text, d, sent=m.text)[0] == (back or src), (seed, m.text)


def test_lone_patronymic_makes_the_other_word_a_surname():
    """«Иванович Кузнец»: при одном отчестве слово без тега — фамилия, подделка из пула фамилий
    (n793-out8 DS-M3, out9 DS-M3)."""
    pii_fake._load()
    surn = {pii_fake._e(x) for x in (*pii_fake.SURNAMES, *pii_fake._pools["surn2"])}
    for seed in range(5):
        m, d, _f = _dressed("Иванович Кузнец пришёл.", (pm.NodeForms("Иванович Кузнец"),), seed=seed)
        fake = dict(next(iter(d.pairs.values())))["Иванович Кузнец"].split()[-1]
        assert pii_fake._e(fake) in surn, (seed, fake)


def test_system_in_a_third_case_comes_back_not_marked():
    """Многословная система в двух падежах, ответ — в третьем: обратно имя, не «⟦?…⟧»
    (n793-out9 S-I1 = DS-I1: классы `same_word` — по леммам каждого слова)."""
    morph = pii_fake._load()
    forms = pm.build_forms([pm.NodeForms("Северный Портал", person=False)],
                           known={"северный", "портал"}.__contains__)
    src = "Северный Портал упал. Нет Северного Портала."
    done = 0
    for seed in range(40):
        m = pm.mask(src, forms)
        d = pii_fake.dress(m, src, forms, seed=seed)
        fake = dict(next(iter(d.pairs.values())))["Северный Портал"]
        datv = morph.parse(fake)[0].inflect({"datv"})
        if " " in fake or not datv or datv.word == fake.lower():
            continue
        reply = f"Писали {pii_fake._keep_case(fake, datv.word)}."
        back, left = pii_fake.undress(reply, d, sent=m.text)
        assert left == 0 and "⟦" not in back and "Портал" in back, (seed, reply, back)
        done += 1
    assert done, "ни одной склоняемой подделки — тест ничего не проверил"


@pytest.mark.parametrize("node, src", [("Северный Маяк", "Северный Портал закрыли"),
                                       ("Маяк 12", "Портал 12 закрыли")])
def test_fake_never_joins_a_neighbour_into_a_graph_name(monkeypatch, node, src):
    """Подделка «Маяк» рядом с «Северный» складывает узел «Северный Маяк», которого в тексте
    не было: гейт смотрит итоговую строку, а не подделку в отрыве (n793-out6 DS-C1)."""
    forms = pm.build_forms([pm.NodeForms(node, person=False), pm.NodeForms("Портал", person=False)],
                           known={"северный", "маяк", "портал"}.__contains__)
    pii_fake._load()
    for pool in (["Маяк"], ["Маяк", "Кальцит"]):
        monkeypatch.setitem(pii_fake._pools, "system", pool)
        for seed in range(20):
            m = pm.mask(src, forms)
            pii_fake.dress(m, src, forms, seed=seed)
            assert "Маяк" not in m.text and pm.postcondition_hits(m.text, forms) == 0, (pool, seed, m.text)


def test_single_letter_fake_is_not_replaced_inside_words():
    """Мусорный спан NER «А» с подделкой «П»: ответ без имён не меняется — «ДАП» и
    «сканирование» остаются (n793-out6, опыт на ответах без маски: 299 ложных замен)."""
    d = pii_fake.Dress()
    d.pairs = {"Ч~1": [("А", "П")], "Ч~2": [("с", "а")], "Ч1": [("Орлов И.П.", "Белов И.П.")]}  # приватный-образец: синтетические инициалы
    reply = "ДАП сканирование П с. Белов И.П. пришёл"  # приватный-образец: синтетические инициалы
    assert pii_fake.undress(reply, d, sent=reply)[0] == "ДАП сканирование П с. Орлов И.П. пришёл"  # приватный-образец: синтетические инициалы


def test_abbreviation_fake_is_not_a_word(monkeypatch):
    """Аббревиатура-подделка — не слово языка: «ПЛАН» в начале фразы ответа развернулся
    бы в систему (n793-out5 H-C2)."""
    pk = pii_fake._Picker(pii_fake._load(), "текст", pm.build_forms([]), random.Random(1))
    seq = iter([4, "П", "Л", "А", "Н", 3, "З", "Л", "К"])     # длина, буквы; длина, буквы
    monkeypatch.setattr(pk.rnd, "choice", lambda _xs: next(seq))
    monkeypatch.setattr(pk.rnd, "random", lambda: 1.0)
    assert pk.abbr() != "ПЛАН"


@pytest.mark.parametrize("node, src, guest", [
    ("Василий Петров", "Василиса пришла. Василий Петров ушёл.", "Василиса"),
    ("Василий Петров", "Васильев пришёл. Василий Петров ушёл.", "Васильев"),
    ("Юлий Орлов", "Юлиана пришла. Юлий Орлов ушёл.", "Юлиана"),
    ("Дмитрий Орлов", "Дмитриева пришла. Дмитрий Орлов ушёл.", "Дмитриева"),
    ("Василий Петров", "Василию сказали. Василием Петровым довольны. Василий Петров ушёл.", None),
    # фамилия от имени узла: «ов/ев» — и падеж, и другое слово; решает лексема (n793-out10 C1)
    ("Пётр Орлов", "Петров пришёл. Пётр Орлов ушёл.", "Петров"),
    ("Иван Сидоров", "Иванов пришёл. Иван Сидоров ушёл.", "Иванов"),
    ("Николай Орлов", "Николаев пришёл. Николай Орлов ушёл.", "Николаев"),
    ("Роман Орлов", "Романов пришёл. Роман Орлов ушёл.", "Романов"),
    ("Пётр Орлов", "Петра позвали. Петру сказали. Петром довольны. Пётр Орлов ушёл.", None),
    # падежи узла вне перечня хвостов — тоже узел (n793-out10 S-M2, DS-M3)
    ("Иван Вельский", "Вельских позвали. Вельскими довольны. Иван Вельский ушёл.", None),
    # «Юлия» — и род. «Юлий»: словарь не различает, канон узла остаётся (долг №609)
    ("Юлий Орлов", "Юлия пришла. Юлий Орлов ушёл.", None),
])
def test_other_name_of_the_same_stem_is_not_the_node(node, src, guest):
    """Гость с основой узла («Василиса» при «Василий») — своя группа и своя подделка:
    иначе разворот писал в граф имя узла (n793-final-opus C1). Падеж узла — узел:
    группа с каноном одна, гость — вне её (n793-out10 DS-M4: дословный возврат этого не видит)."""
    m, d, _ = _dressed(src, (pm.NodeForms(node),), seed=0)
    assert pii_fake.undress(m.text, d, sent=m.text) == (src, 0), m.text
    ours = [g for g in m.groups.values() if g.canon == node]
    assert len(ours) == 1, m.groups
    others = [f for g in m.groups.values() if g.canon != node for f in g.forms]
    assert others == ([guest] if guest else []), m.groups


@pytest.mark.parametrize("node, src, gender", [
    ("Дмитрий Орлов", "Орлова пришла. Дмитрий Орлов ушёл.", "masc"),
    ("Ольга Орлова", "Ольга Орлова пришла. Орлову сказали.", "femn"),
])
def test_group_with_a_node_takes_the_node_gender(node, src, gender):
    """«Орлова» при узле «Дмитрий Орлов» — и род. падеж узла, и жена: род подделки — по узлу,
    иначе облако видело «Миланы Зимогорова ушёл», а ответ разворачивался в «Дмитрии Орлова»
    (n793-out11 DS-C1)."""
    for seed in range(5):
        m, d, _ = _dressed(src, (pm.NodeForms(node),), seed=seed)
        assert set(d.gender.values()) == {gender}, (seed, m.text)
        assert pii_fake.undress(m.text, d, sent=m.text) == (src, 0), m.text


@pytest.mark.parametrize("node, src, tail, has", [
    ("Дмитрий Орлов", "Орловой передали. Дмитрий Орлов ушёл.", "ой", True),
    ("Ольга Орлова", "Орлов пришёл. Ольга Орлова ушла.", "а", False),   # «Брусницын», не «Брусницына»
])
def test_surname_ending_follows_the_form_gender(node, src, tail, has):
    """Род группы — по узлу, окончание фамилии — по роду самой формы, если в роде группы её не
    разобрать: иначе «Орловой» уходила облаку «Зимогорове» (n793-out12 H-I2 = DS-I2)."""
    for seed in range(5):
        m, d, _ = _dressed(src, (pm.NodeForms(node),), seed=seed)
        assert m.text.split()[0].endswith(tail) is has, (seed, m.text)
        assert pii_fake.undress(m.text, d, sent=m.text) == (src, 0), m.text
