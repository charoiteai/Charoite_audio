"""Маска ПДн перед облаком (№793, этап A): формы, маска, постусловие, обратный ход."""
from __future__ import annotations

import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import cloud_door  # noqa: E402
import pii_mask as pm  # noqa: E402
import privacy  # noqa: E402
from charoite_graph.graph_nodes import NodeIndex  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402


def _forms(*nodes, **kw):
    return pm.build_forms(nodes, **kw)


PEOPLE = (pm.NodeForms("Анна Белова"), pm.NodeForms("Иван Сидоров"),
          pm.NodeForms("ЗЛК", person=False), pm.NodeForms("Платёжный шлюз", person=False))


def test_round_trip_restores_every_surface_form():
    forms = _forms(*PEOPLE)
    src = "Анне Беловой написал Сидоров, ЗЛКа нет. Иван Сидоров против, шлюз Платёжный шлюз стоит."
    m = pm.mask(src, forms)
    assert "Бел" not in m.text and "Сидор" not in m.text and "ЗЛК" not in m.text
    back, unresolved = pm.unmask(m.text, m)
    assert back == src and unresolved == 0


@pytest.mark.parametrize("text", [
    "аннотацию к январю, пирамида Ивановых-младших не при чём",
    "январь, аннотация, Анкара, белок",
    "злк строчными — обычное слово",
])
def test_common_words_are_not_masked_and_do_not_refuse(text):
    forms = _forms(pm.NodeForms("Анна"), pm.NodeForms("Ян"), pm.NodeForms("ЗЛК", person=False),
                   pm.NodeForms("Белова"))
    m = pm.mask(text, forms)
    assert m.text == text and m.replaced == 0


def test_people_need_a_capital_letter():
    forms = _forms(pm.NodeForms("Роза"))
    assert pm.mask("роза в саду", forms).replaced == 0
    assert pm.mask("Розе сказали", forms).replaced == 1


def test_namesakes_share_a_form_without_a_node():
    forms = _forms(pm.NodeForms("Иван Петров"), pm.NodeForms("Пётр Петров"))
    m = pm.mask("Петров сказал", forms)
    gid = next(iter(m.groups))
    assert gid.startswith(pm.PERSON + "~") and m.groups[gid].canon is None


def test_token_without_form_unmasks_to_the_node_name():
    forms = _forms(pm.NodeForms("Анна Белова"))
    m = pm.mask("Беловой отправили", forms)
    gid = next(iter(m.groups))
    back, n = pm.unmask(f"⟦{gid}⟧ согласует", m)
    assert back == "Анна Белова согласует" and n == 0


@pytest.mark.parametrize("reply", ["⟦Ч9.1⟧ согласует", "⟦Ч1.7⟧ согласует", "⟦Ч1 согласует", "⟦Ч~1⟧ согласует"])
def test_unknown_or_broken_token_becomes_unresolved(reply):
    forms = _forms(pm.NodeForms("Анна Белова"))
    m = pm.mask("Беловой отправили", forms)
    back, n = pm.unmask(reply, m)
    assert back.startswith(pm.UNRESOLVED) and n == 1


def test_brackets_in_source_cannot_forge_a_token():
    forms = _forms(pm.NodeForms("Анна Белова"))
    m = pm.mask("⟦Ч1.1⟧ и Белова", forms)
    assert m.text.startswith("[Ч1.1]")
    back, _n = pm.unmask(m.text, m)
    assert back == "[Ч1.1] и Белова"


def test_ner_rest_is_masked_to_word_boundaries():
    forms = _forms(pm.NodeForms("Иван"))
    text = pm.prepare("звонил Иван Сидоров вчера")
    s = text.index("Иван")
    m = pm.mask(text, forms, [(s, s + len("Иван Сидоро"), pm.PERSON)])
    assert "Сидор" not in m.text and m.replaced == 2
    assert pm.unmask(m.text, m) == (text, 0)


def test_postcondition_catches_a_form_the_mask_missed():
    forms = _forms(pm.NodeForms("Анна Белова"))
    assert pm.postcondition_hits("потом Беловой позвонили", forms) == 1
    assert pm.postcondition_hits("потом ⟦Ч1.1⟧ позвонили", forms) == 0


def test_mask_refuses_when_postcondition_sees_a_form(monkeypatch):
    forms = _forms(pm.NodeForms("Анна Белова"))
    monkeypatch.setattr(pm, "_dictionary_spans", lambda text, f: [])
    with pytest.raises(pm.MaskRefused):
        pm.mask("Беловой отправили", forms)


def test_common_file_words_go_as_is():
    forms = _forms(pm.NodeForms("Шлюз", person=False), common=["Шлюз"])
    assert pm.mask("Шлюз упал", forms).replaced == 0


def test_stub_name_masks_to_its_canon_or_to_a_nodeless_form():
    forms = _forms(pm.NodeForms("Анна Белова"), stubs={"Белова А.": "Анна Белова", "Сидорова": None})
    m = pm.mask("Белова А. и Сидорова", forms)
    canons = {g.canon for g in m.groups.values()}
    assert canons == {"Анна Белова", None}


def test_owner_from_config_is_masked_even_without_a_node():
    forms = _forms(owner="Игорь Петренко")
    assert pm.mask("Игорю Петренко передали", forms).replaced == 1


COMMON = {"поддержка", "поддержки", "вот", "их", "второй", "вторая", "вторник", "линия", "труба",
          "я", "подсистема"}


def test_system_is_masked_only_by_its_distinctive_part():
    """Замер 26.10: имена и псевдонимы узлов систем из общих слов давали сотни замен
    («вот», «их», «вторник»). Система маскируется аббревиатурой, буквами с цифрами
    или словом, которого нет в общем словаре."""
    forms = _forms(pm.NodeForms("Поддержка", person=False),
                   pm.NodeForms("ИФТ", ("Их",), person=False),
                   pm.NodeForms("ВО", person=False),
                   pm.NodeForms("Вторая линия поддержки", person=False),
                   pm.NodeForms("Шлюз S3 Препрод", person=False),
                   pm.NodeForms("28-я подсистема", person=False),
                   known=COMMON.__contains__)
    text = "Вот их поддержка во вторник, второй раз. Препрода нет, S3 и ВО живы, 28 тоже."
    m = pm.mask(text, forms)
    assert {g.forms[0] for g in m.groups.values()} == {"Препрода", "S3", "ВО"}


def test_short_base_does_not_swallow_ordinary_words():
    forms = _forms(pm.NodeForms("Дела", person=False), pm.NodeForms("Вор", person=False))
    assert pm.mask("Делать ворота не надо", forms).replaced == 0
    assert pm.mask("Делу и Вора", forms).replaced == 2


def test_abbreviation_written_as_a_word_is_masked():
    forms = _forms(pm.NodeForms("ОРИС", person=False))
    m = pm.mask("в Орисе и в ОРИСа, но не в орисе", forms)
    assert m.replaced == 2


@pytest.mark.parametrize("text, word", [
    ("Ну, начнём. Ну и ну, сказал он.", "Ну"),
    ("А-а, понятно.", "А-а"),
])
def test_speech_is_not_a_name_for_the_name_model(text, word):
    t = pm.prepare(text)
    s = t.index(word)
    m = pm.mask(t, _forms(), [(s, s + len(word), pm.PERSON)])
    assert m.replaced == 0


def test_name_model_span_is_masked_when_the_word_is_only_a_name():
    t = pm.prepare("Пришёл Гоша, гоши нет")
    s = t.index("Гоша")
    assert pm.mask(t, _forms(), [(s, s + 4, pm.PERSON)]).replaced == 1


def test_same_name_compares_by_stem():
    assert pm.same_name("Игорю", "Игорь")
    assert pm.same_name("Анне Беловой", "Анна Белова")
    assert not pm.same_name("Анна", "Анна Белова")


# ------------------------------------------------------------- privacy


@pytest.mark.parametrize("value, required", [
    (None, True), (True, True), ("false", True), ("", True), (0, True), (False, False),
])
def test_only_boolean_false_switches_the_mask_off(value, required):
    sufler = {} if value is None else {"cloud_mask": value}
    assert privacy.mask_required({"sufler": sufler}) is required


def test_mask_level_defaults_to_basic_and_refuses_unknown():
    assert privacy.mask_level({}) == "basic"
    assert privacy.mask_level({"sufler": {"cloud_mask_level": "full"}}) == "full"
    with pytest.raises(privacy.PrivacyRefused):
        privacy.mask_level({"sufler": {"cloud_mask_level": "max"}})


def test_graph_mode_is_text_under_the_mask(monkeypatch):
    for k in privacy.KILL_SWITCHES:
        monkeypatch.delenv(k, raising=False)
    on = {"cloud_enrich": True, "cloud_edit_graph": True}
    assert privacy.cloud_graph_mode({"sufler": on}) == "text"
    assert privacy.cloud_graph_mode({"sufler": {**on, "cloud_mask": False}}) == "edit"
    assert privacy.cloud_graph_mode({"sufler": {"cloud_enrich": True, "cloud_mask": False}}) == "read"
    assert privacy.cloud_graph_mode({"sufler": {}}) == "off"


# ------------------------------------------------------------- граф и дверь


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    for d in ("Люди", "Системы", "Ядра"):
        (tmp_path / d).mkdir()
    (tmp_path / "Люди" / "Анна Белова.md").write_text(
        "---\naliases:\n  - Аня\n---\n# Анна Белова\n", encoding="utf-8")
    (tmp_path / "Люди" / "Белова А.md").write_text(
        "# Белова А → [[Люди/Анна Белова]]\n\n⚠️ **Дубль слит.**\n", encoding="utf-8")
    (tmp_path / "Системы" / "ЗЛК.md").write_text("# ЗЛК\n", encoding="utf-8")
    (tmp_path / "Ядра" / "Ретеншн.md").write_text("# Ретеншн\n", encoding="utf-8")
    return tmp_path


def test_snapshot_for_mask_carries_stubs_and_aliases(tmp_path):
    snap = NodeIndex(_graph(tmp_path), schema=CHAROITE).snapshot_for_mask()
    anna = next(n for n in snap.nodes if n.name == "Анна Белова")
    assert anna.aliases == ("Аня",)
    assert [(s.name, s.target) for s in snap.stubs] == [("Белова А", "Люди/Анна Белова.md")]
    assert not snap.unreadable


def test_unreadable_person_node_refuses_the_mask(tmp_path):
    root = _graph(tmp_path)
    (root / "Люди" / "Пётр.md").write_bytes(b"\xff\xfe# \x00")
    snap = NodeIndex(root, schema=CHAROITE).snapshot_for_mask()
    assert root / "Люди" / "Пётр.md" in snap.unreadable
    with pytest.raises(pm.MaskRefused):
        cloud_door._forms({}, root)


def _fake_ner(monkeypatch, spans=()):
    mod = types.ModuleType("pii_ner")
    mod.spans = lambda text: list(spans)
    mod.is_common = lambda word: False
    monkeypatch.setitem(sys.modules, "pii_ner", mod)


def test_mask_prompt_masks_names_aliases_stubs_and_owner(tmp_path, monkeypatch):
    _fake_ner(monkeypatch)
    cfg = {"sufler": {"user_name": "Игорь"}}
    m = cloud_door.mask_prompt(cfg, "Аня, Белова А и Игорь обсудили ЗЛК; Ретеншн растёт",
                               _graph(tmp_path))
    for name in ("Аня", "Белова", "Игорь", "ЗЛК"):
        assert name not in m.text
    assert "Ретеншн" in m.text, "ядра — темы, а не имена"
    back, n = cloud_door.unmask_reply(m.text, m)
    assert back.startswith("Аня, Белова А и Игорь") and n == 0


def test_mask_prompt_refuses_full_level_and_missing_graph(tmp_path, monkeypatch):
    _fake_ner(monkeypatch)
    with pytest.raises(pm.MaskRefused):
        cloud_door.mask_prompt({"sufler": {"cloud_mask_level": "full"}}, "x", _graph(tmp_path))
    with pytest.raises(pm.MaskRefused):
        cloud_door.mask_prompt({}, "x", tmp_path / "нет")
    with pytest.raises(pm.MaskRefused):
        cloud_door.mask_prompt({"sufler": {"cloud_mask_level": "max"}}, "x", _graph(tmp_path))


def test_mask_off_passes_text_but_still_scrubs_paths(tmp_path):
    home = str(pathlib.Path.home())
    m = cloud_door.mask_prompt({"sufler": {"cloud_mask": False}}, f"Анна, файл {home}/a.md", None)
    assert m.level == "off" and "Анна" in m.text and home not in m.text
    assert cloud_door.unmask_reply("⟦Ч1.1⟧", m) == ("⟦Ч1.1⟧", 0)


def test_missing_ner_engine_refuses(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "natasha", None)
    import pii_ner
    monkeypatch.setattr(pii_ner, "_model", None)
    with pytest.raises(pm.MaskRefused, match="natasha"):
        pii_ner.spans("Анна")
