"""Подставные имена вместо меток маски (№793, решение 26.10 по замеру №799, плечо C)."""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import pii_mask as pm  # noqa: E402

pytest.importorskip("pymorphy3")
import pii_fake  # noqa: E402

NODES = (pm.NodeForms("Дмитрий Орлов", ("Дима",)), pm.NodeForms("Анна Белова"),
         pm.NodeForms("ЗЛК", person=False), pm.NodeForms("Старый офис", person=False))
KNOWN = {"старый", "офис"}.__contains__
SRC = ("Дима сказал Анне Беловой, что ЗЛКа нет. Диму и Орлова ждём. "
       "Позвони Дмитрию Орлову. Старым офисом занялись, в Старом офисе пусто.")


def _dressed(src=SRC, nodes=NODES, seed=1):
    forms = pm.build_forms(nodes, known=KNOWN)
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
