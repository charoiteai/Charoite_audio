"""Сторож литералов имён папок и разделов в пакете (№422, PR A).

Значение схемы хранилища (`charoite_schema.CHAROITE`) — единственное место имён;
сторож (`literals_measure` / `folder_literal` в `scripts/layout_map.py`) ищет
копии этих имён в литералах пакета и держит их как долг с карточкой. Здесь
самотест сторожа на синтетической области, отказы замера, долг и главный тракт.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402


#: Поля схемы и её значение для синтетической области: те же имена и пробы, что у
#: Чароита, своим файлом — самотест не зависит от боевых файлов.
LITERAL_FIELDS = ("node_folders", "people_folders", "core_folders", "meeting_folders",
                  "dossier_dir", "meeting_dir", "exclude_dirs", "service_prefixes",
                  "history_heads", "raw_markers", "raw_suffixes", "meeting_link_prefixes")
GUARD_VALUES = {
    "node_folders": ("Люди", "Команды", "Системы", "Модели", "Блокеры", "Ядра",
                     "People", "Teams", "Systems", "Models", "Blockers", "Cores"),
    "people_folders": ("Люди", "People"),
    "core_folders": ("Ядра", "Cores"),
    "meeting_folders": ("Встречи", "Встречи-архив", "Meetings"),
    "dossier_dir": "Досье",
    "meeting_dir": "Встречи",
    "exclude_dirs": ("Встречи-архив", "Документация/Стенограммы встреч"),
    "service_prefixes": ("_", "Служебное_"),
    "history_heads": ("## Встречи", "## Хроника", "## Meetings", "## History"),
    "raw_markers": ("стенограмм", "transcript"),
    "raw_suffixes": ("_live.md",),
    "meeting_link_prefixes": ("Встречи", "Meetings"),
}


def _layout(**over) -> dict:
    base = {"order": ["low"], "allowed": {"low": []},
            "brief_layers": {"low": ["core_mod"]}, "layer_overrides": {},
            "allowed_edges": [], "manual_entry_points": {}, "root_exemptions": {},
            "generated": "2026-09-19T00:00Z", "run_contracts": {},
            "schema_module": "src/schema.py", "schema_values": "src/schema_values.py",
            "folder_literals": [], "folder_literal_exemptions": {},
            "package": "pkg", "package_entry": "pkg.entry"}
    base.update(over)
    return base


def _schema_source() -> str:
    return "class GraphSchema:\n" + "".join(f"    {name}: object\n" for name in LITERAL_FIELDS)


def _values_source(values: dict | None = None, star: bool = False) -> str:
    values = GUARD_VALUES if values is None else values
    if star:
        return "CHAROITE = GraphSchema(**{})\n"
    return "CHAROITE = GraphSchema(\n" + "".join(f"    {name}={values[name]!r},\n"
                                                 for name in values) + ")\n"


def _literal_area(tmp_path, body: str, values: dict | None = None, entry: str = "pkg.entry"):
    """Синтетическая область: пакет с данным телом модуля, схема и её значение."""
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True, exist_ok=True)
    (src / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (src / "pkg" / "entry.py").write_text(body, encoding="utf-8")
    (src / "schema.py").write_text(_schema_source(), encoding="utf-8")
    (src / "schema_values.py").write_text(_values_source(values), encoding="utf-8")
    layout = _layout(package="pkg", package_entry=entry)
    return lm.inventory(tmp_path), layout


def _hits(tmp_path, body: str):
    inv, layout = _literal_area(tmp_path, body)
    return lm.literals_measure(inv, layout).hits


@pytest.mark.parametrize("body, field, literal", [
    ('x = "Люди"\n', "node_folders", "Люди"),
    ('x = "Досье/_index.json"\n', "dossier_dir", "Досье/_index.json"),
    ('a = "Документация"\nb = "Стенограммы встреч"\n', "exclude_dirs", "Стенограммы встреч"),
    ('x = r"стенограмм|_live\\.md$|transcript"\n', "raw_markers", r"стенограмм|_live\.md$|transcript"),
    ('x = r"стенограмм|_live\\.md$|transcript"\n', "raw_suffixes", r"стенограмм|_live\.md$|transcript"),
    ('x = "свои_стенограммы"\n', "raw_markers", "свои_стенограммы"),
    ('x = "старая_live.md"\n', "raw_suffixes", "старая_live.md"),
    ('x = "## Встречи сегодня"\n', "history_heads", "## Встречи сегодня"),
    ('a = 1\nx = f"Встречи/{a}"\n', "meeting_dir", "Встречи/"),
    ('x = "Служебное_x"\n', "service_prefixes", "Служебное_x"),
])
def test_the_literal_guard_reds_on_the_copies(tmp_path, body, field, literal):
    """Копия имени в пакете — попадание в поле схемы: имя целиком, путь сегментом,
    регулярка сырья (и маркер, и суффикс), f-строка и служебный префикс."""
    assert ("src/pkg/entry.py", field, literal) in _hits(tmp_path, body)


@pytest.mark.parametrize("body", [
    '"""Люди"""\n',            # докстринг — не литерал
    'x = "📁 Досье «"\n',      # имя с обвесом — не равно пробе
    'x = "_"\n',               # проба короче трёх знаков не участвует
    'x = "a_b"\n',             # нет такой пробы
    'x = b"Люди"\n',           # байты не участвуют: isinstance(value, str)
])
def test_the_literal_guard_is_silent_on_noise(tmp_path, body):
    assert _hits(tmp_path, body) == frozenset()


def test_the_literal_guard_scans_exactly_the_package(tmp_path):
    """Область сторожа — файлы пакета, ровно как план пробы. Модуль пакета вне
    замыкания называет гейт пакета одной строкой; замер литералов её не повторяет
    (выходной круг 1 по #654, DS M3)."""
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    assert lm.package_area(inv, layout) == lm.package_files(inv, layout)
    assert set(lm.package_area(inv, layout)) == {"src/pkg/__init__.py", "src/pkg/entry.py"}
    (tmp_path / "src" / "pkg" / "extra.py").write_text("", encoding="utf-8")
    inv2 = lm.inventory(tmp_path)
    assert not any("pkg.extra" in p for p in lm.literals_measure(inv2, layout).problems)
    gate = lm.package_problems(lm.import_graph(inv2), layout, inv2)
    assert sum("pkg.extra" in p for p in gate) == 1, gate


def test_the_schema_module_outside_the_entry_closure_is_a_gate_line(tmp_path):
    """Изъятий из правила «член пакета вне замыкания входа» нет, и модуль схемы —
    не исключение: путь-решение сторожа литералов гейт пакета не читает (выходной
    круг 2 по #654, DS M1; мёртвый вывод изъятия снят в круге 3, DS I1)."""
    _inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "pkg" / "schema.py").write_text(_schema_source(), encoding="utf-8")
    layout = dict(layout, schema_module="src/pkg/schema.py")
    inv = lm.inventory(tmp_path)
    gate = lm.package_problems(lm.import_graph(inv), layout, inv)
    assert sum("pkg.schema" in p for p in gate) == 1, gate
    measure = lm.literals_measure(inv, layout).problems
    assert not any("pkg.schema" in p for p in measure), "строку называет гейт пакета, замер её не повторяет"


@pytest.mark.parametrize("exemptions, fragment", [
    ({"src/pkg/entry.py": {}}, "поле → литерал → причина"),
    ({"src/pkg/entry.py": ["Люди"]}, "поле → литерал → причина"),
    ({"src/pkg/entry.py": {"node_folders": {}}}, "литерал → причина"),
    ({"src/pkg/entry.py": {"node_folders": ["Люди"]}}, "литерал → причина"),
    ({"src/pkg/entry.py": {"node_folders": {"Люди": "  "}}}, "непустая причина"),
    ({"src/pkg/entry.py": {"node_folders": {"Люди": 5}}}, "непустая причина"),
], ids=["пустая-карта-полей", "список-вместо-карты", "пустая-карта-литералов",
        "список-литералов", "причина-из-пробелов", "причина-не-строка"])
def test_an_exemption_of_the_wrong_shape_is_refused_at_load(exemptions, fragment):
    """Прощение — карта «поле → литерал → причина» с непустой причиной на каждое
    попадание; иная форма — отказ загрузки, а не молчаливое прощение (мутатор по
    #654: ветки формы не были покрыты)."""
    layout = lm.load_layout()
    layout["folder_literal_exemptions"] = exemptions
    with pytest.raises(lm.LayoutError, match=fragment):
        lm.validate_layout(layout)


def test_only_name_fields_split_a_path_into_segments():
    """Путь в поле-имени ловится и целиком, и сегментами; у поля сырья «/» — часть
    литерала, а не граница имён (мутатор по #654)."""
    assert lm._probe_parts("exclude_dirs", "Документация/Стенограммы встреч") == [
        "документация/стенограммы встреч", "документация", "стенограммы встреч"]
    assert lm._probe_parts("raw_markers", "стенограмм/черновик") == ["стенограмм/черновик"]


def test_a_non_string_in_a_value_tuple_is_a_refusal(tmp_path):
    """Элемент кортежа значения — строковый литерал; число там — отказ, а не проба
    (мутатор по #654)."""
    values = dict(GUARD_VALUES, raw_suffixes=("_live.md", 5))
    inv, layout = _literal_area(tmp_path, "x = 1\n", values=values)
    with pytest.raises(lm.LayoutError, match="строковый литерал, а не выражение"):
        lm.literals_measure(inv, layout)


def test_other_assignments_beside_the_value_are_not_the_value(tmp_path):
    """Рядом со значением схемы живут другие присваивания — имя и атрибут; они не
    второе значение и не ломают чтение (мутатор по #654)."""
    _inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema_values.py").write_text(
        "OTHER = 1\nOTHER.attr = 2\n" + _values_source(), encoding="utf-8")
    assert lm.literals_measure(lm.inventory(tmp_path), layout).hits == frozenset()


def test_a_field_declared_twice_is_a_refusal(tmp_path):
    """Поле класса схемы, объявленное дважды, — отказ с адресом исходника (мутатор
    по #654)."""
    _inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema.py").write_text(_schema_source() + "    node_folders: object\n",
                                                encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="объявлено дважды") as refused:
        lm.literals_measure(lm.inventory(tmp_path), layout)
    assert refused.value.culprit == lm.REPO / "src/schema.py"


def test_a_values_file_that_does_not_parse_is_a_refusal(tmp_path):
    """Файл значений не разбирается — отказ с адресом исходника, а не пустой замер
    (мутатор по #654)."""
    _inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema_values.py").write_text("CHAROITE = (\n", encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="не разбирается") as refused:
        lm.literals_measure(lm.inventory(tmp_path), layout)
    assert refused.value.culprit == lm.REPO / "src/schema_values.py"


def test_the_artifact_text_keeps_cyrillic_and_two_space_indent():
    """Артефакт читают глазами в диффе PR: кириллица как есть, отступ 2 (мутатор по
    #654: запись при `--regen` не была покрыта)."""
    assert lm.dump_layout({"a": ["Люди"]}) == '{\n  "a": [\n    "Люди"\n  ]\n}\n'


def test_debt_and_exemption_of_one_hit_are_refused_at_load():
    """Одно попадание и долгом, и прощением — артефакт себе противоречит, а сверка
    вычитала реестры друг из друга и молчала на обе стороны (выходной круг 1 по
    #654, DS I1). Отказ — на загрузке: такое состояние нельзя ни прочесть, ни
    записать регеном."""
    layout = lm.load_layout()
    # свой долг: у боевого артефакта долг ноль с PR B №422, попадание — рукой
    entry = {"rel": "src/charoite_graph/dossier.py", "field": "dossier_dir", "literal": "Досье", "ticket": "№422"}
    layout["folder_literals"] = [entry]
    layout["folder_literal_exemptions"] = {entry["rel"]: {entry["field"]: {entry["literal"]: "шум"}}}
    with pytest.raises(lm.LayoutError, match="оставить одно"):
        lm.validate_layout(layout)


@pytest.mark.parametrize("groups, fragment", [
    ({"LITERAL_SUFFIX_FIELDS": ()}, "без группы ['raw_suffixes']"),
    ({"LITERAL_SUFFIX_FIELDS": ("raw_suffixes", "raw_markers")}, "в двух группах ['raw_markers']"),
    ({"LITERAL_PREFIX_FIELDS": lm.LITERAL_PREFIX_FIELDS + ("мёртвое",)}, "нет в схеме ['мёртвое']"),
])
def test_rule_groups_cover_the_schema_fields_exactly_once(tmp_path, monkeypatch, groups, fragment):
    """Правило поиска выбирается первой совпавшей веткой: поле в двух группах молча
    сменило бы правило, мёртвое имя жило бы в таблице. Отказ, и адрес — код сторожа
    (выходной круг 1 по #654, DS M4 и I3)."""
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    for name, value in groups.items():
        monkeypatch.setattr(lm, name, value)
    with pytest.raises(lm.LayoutError) as refused:
        lm.literals_measure(inv, layout)
    assert fragment in str(refused.value)
    assert refused.value.culprit == lm.LAYOUT_CODE


def test_refusals_name_the_file_to_fix(tmp_path):
    """Адрес отказа — файл, который править (№384): нет файла в инвентаре — путь в
    артефакте; значение без вызова или класс без полей — сам исходник (выходной
    круг 1 по #654, DS I3)."""
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    with pytest.raises(lm.LayoutError, match="не читается") as refused:
        lm.literals_measure(inv, dict(layout, schema_module="src/нет.py"))
    assert refused.value.culprit == lm.LAYOUT
    values = tmp_path / "src" / "schema_values.py"
    values.write_text("CHAROITE = None\n", encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="вызова CHAROITE") as refused:
        lm.literals_measure(lm.inventory(tmp_path), layout)
    assert refused.value.culprit == lm.REPO / "src/schema_values.py"
    values.write_text(_values_source(), encoding="utf-8")
    (tmp_path / "src" / "schema.py").write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="класса GraphSchema") as refused:
        lm.literals_measure(lm.inventory(tmp_path), layout)
    assert refused.value.culprit == lm.REPO / "src/schema.py"


def test_a_second_assignment_of_the_value_is_a_refusal(tmp_path):
    """Python исполняет последнее присваивание, сторож мерил бы первое — значение
    схемы одно (выходной круг 1 по #654, DS M5)."""
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema_values.py").write_text(_values_source() + _values_source(),
                                                        encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="присвоено 2 раза"):
        lm.literals_measure(lm.inventory(tmp_path), layout)


def test_a_missing_schema_module_is_a_refusal(tmp_path):
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema.py").unlink()
    with pytest.raises(lm.LayoutError, match="не читается"):
        lm.literals_measure(lm.inventory(tmp_path), layout)


def test_a_missing_values_file_is_a_refusal(tmp_path):
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    (tmp_path / "src" / "schema_values.py").unlink()
    with pytest.raises(lm.LayoutError, match="не читается"):
        lm.literals_measure(lm.inventory(tmp_path), layout)


def test_the_blocked_note_names_the_carrier():
    """Строка блокировки различает ребро и литерал: без неё неоткарточенное
    попадание печаталось бы пустым местом."""
    assert "ребро a → b" in lm._blocked_note(("a", "b"))
    note = lm._blocked_note(("src/x.py", "node_folders", "Люди"))
    assert "литерал" in note and "Люди" in note and "folder_literals" in note


def test_a_three_character_probe_participates(tmp_path):
    """Проба ровно в три знака участвует: порог отсева — «короче трёх»."""
    values = dict(GUARD_VALUES, history_heads=("abc",))
    inv, layout = _literal_area(tmp_path, 'x = "abcdef"\n', values=values)
    assert ("src/pkg/entry.py", "history_heads", "abcdef") in lm.literals_measure(inv, layout).hits


def test_no_measurement_is_silence_not_red():
    """Без замера сторож молчит (`None`), а не краснит всё подряд."""
    assert lm.folder_literal(None, _layout()) == []


def test_a_literal_file_without_a_tree_is_a_line(tmp_path):
    """Файл области без дерева — строка замера, а не молчаливый пропуск."""
    inv, layout = _literal_area(tmp_path, "def (:\n")
    measured = lm.literals_measure(inv, layout)
    assert any("без дерева" in p and "src/pkg/entry.py" in p for p in measured.problems), measured.problems


def test_an_empty_literal_area_is_a_refusal(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "schema.py").write_text(_schema_source(), encoding="utf-8")
    (tmp_path / "src" / "schema_values.py").write_text(_values_source(), encoding="utf-8")
    layout = _layout(package="pkg", package_entry="pkg.entry")
    with pytest.raises(lm.LayoutError, match="область сторожа литералов пуста"):
        lm.literals_measure(lm.inventory(tmp_path), layout)


def test_a_field_without_long_probes_is_a_refusal(tmp_path):
    values = dict(GUARD_VALUES, service_prefixes=("_",))
    inv, layout = _literal_area(tmp_path, "x = 1\n", values=values)
    with pytest.raises(lm.LayoutError, match="без проб после отсева коротких"):
        lm.literals_measure(inv, layout)


def test_call_keys_must_match_the_annotations(tmp_path):
    """Ключи вызова ≠ аннотации: пропуск и `**kwargs` — отказ."""
    inv, layout = _literal_area(tmp_path, "x = 1\n")
    missing = {k: v for k, v in GUARD_VALUES.items() if k != "node_folders"}
    (tmp_path / "src" / "schema_values.py").write_text(_values_source(missing), encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="не совпадают с аннотациями"):
        lm.literals_measure(lm.inventory(tmp_path), layout)
    extra = dict(GUARD_VALUES, bonus=("x",))
    (tmp_path / "src" / "schema_values.py").write_text(_values_source(extra), encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="не совпадают с аннотациями"):
        lm.literals_measure(lm.inventory(tmp_path), layout)
    (tmp_path / "src" / "schema_values.py").write_text(_values_source(star=True), encoding="utf-8")
    with pytest.raises(lm.LayoutError, match="ключи вызова обязаны быть названы"):
        lm.literals_measure(lm.inventory(tmp_path), layout)


def test_declared_decision_paths_exist_and_parse():
    """Пути-решения сторожа (модуль схемы и файл значений) объявлены в артефакте,
    существуют на диске и разбираются — иначе сторожу нечего читать."""
    layout = lm.load_layout()
    inv = lm.inventory()
    assert lm.decision_paths(layout) == ("src/charoite_graph/graph_schema.py", "src/charoite_schema.py")
    for rel in lm.decision_paths(layout):
        assert rel in inv.files, rel
        assert inv.files[rel].tree is not None, rel


def test_the_literal_guard_sees_debt_exemptions_and_stale_composition():
    """Долг с карточкой — не расхождение; прощённое — не долг; прощённое, но не
    найденное и устаревшая запись — «снять»."""
    measured = lm.Literals(frozenset({("src/a.py", "node_folders", "Люди")}), {}, {}, [])
    declared = _layout(folder_literals=[{"rel": "src/a.py", "field": "node_folders",
                                         "literal": "Люди", "ticket": "№422"}])
    assert lm.folder_literal(measured, declared) == []
    new = _layout(folder_literals=[])
    assert any("новое попадание без карточки" in p for p in lm.folder_literal(measured, new))
    exempt = _layout(folder_literals=[],
                     folder_literal_exemptions={"src/a.py": {"node_folders": {"Люди": "шум"}}})
    assert lm.folder_literal(measured, exempt) == [], "прощённое — не долг и не расхождение"
    stale = _layout(folder_literals=[{"rel": "src/a.py", "field": "node_folders",
                                      "literal": "Фантом", "ticket": "№422"}])
    assert any("снять" in p for p in lm.folder_literal(measured, stale))
    gone = _layout(folder_literals=[],
                   folder_literal_exemptions={"src/a.py": {"node_folders": {"Фантом": "шум"}}})
    assert any("снять" in p for p in lm.folder_literal(measured, gone))


def test_regen_needs_the_measure_to_touch_the_debt():
    """Без замера реген долг литералов не трогает: вслепую пересобрать его —
    значит стереть карточки."""
    layout = _layout(folder_literals=[{"rel": "src/a.py", "field": "node_folders",
                                       "literal": "Люди", "ticket": "№422"}])
    fresh, unticketed = lm.regen(json.loads(json.dumps(layout)), {})
    assert fresh["folder_literals"] == layout["folder_literals"]
    assert unticketed == []


def test_regen_heals_the_debt_and_keeps_cards():
    """С замером состав пересобирается: прощённое не входит, устаревшее уходит,
    карточки живых записей переносятся, новое без карточки — на печать."""
    measured = lm.Literals(frozenset({("src/a.py", "node_folders", "Люди"),
                                      ("src/b.py", "raw_markers", "стенограмм")}), {}, {}, [])
    layout = _layout(folder_literals=[{"rel": "src/b.py", "field": "raw_markers",
                                       "literal": "стенограмм", "ticket": "№7"},
                                      {"rel": "src/x.py", "field": "node_folders",
                                       "literal": "Фантом", "ticket": "№7"}],
                     folder_literal_exemptions={"src/a.py": {"node_folders": {"Люди": "шум"}}})
    fresh, unticketed = lm.regen(json.loads(json.dumps(layout)), {}, None, [], measured)
    assert fresh["folder_literals"] == [{"rel": "src/b.py", "field": "raw_markers",
                                         "literal": "стенограмм", "ticket": "№7"}]
    assert unticketed == [], "прощённое и устаревшее не блокируют запись"
    blank = _layout(folder_literals=[])
    fresh2, unticketed2 = lm.regen(json.loads(json.dumps(blank)), {}, None, [], measured)
    assert unticketed2 == [("src/a.py", "node_folders", "Люди"), ("src/b.py", "raw_markers", "стенограмм")]
    assert all(e["ticket"] == "" for e in fresh2["folder_literals"])


def test_the_literal_guard_is_asked_by_the_main_path(tmp_path, monkeypatch, capsys):
    """Главный тракт: пробное дерево, пробный артефакт, `main(["--check"])`.
    Литерал в модуле пакета красит код 1, без литерала — зелёный; отказ замера
    на `--regen` печатает «✗» и не пишет артефакт."""
    repo = tmp_path
    (repo / "docs" / "design").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "layout_map.py").write_text(
        ROOT.joinpath("scripts", "layout_map.py").read_text(encoding="utf-8"), encoding="utf-8")
    layout_path = repo / "docs" / "design" / "layout.json"
    map_path = repo / "docs" / "design" / "layout.md"
    layout = {
        "generated": "2026-09-19T00:00Z", "order": ["base"], "allowed": {"base": []},
        "brief_layers": {"base": ["pkg", "pkg.entry", "schema", "schema_values"]},
        "layer_overrides": {}, "allowed_edges": [], "manual_entry_points": {},
        "root_exemptions": {"scripts/layout_map.py": {"file": "пробное дерево: строка считает корень репозитория"}},
        "run_contracts": {}, "package": "pkg", "package_entry": "pkg.entry",
        "schema_module": "src/schema.py", "schema_values": "src/schema_values.py",
        "folder_literals": [], "folder_literal_exemptions": {},
    }
    monkeypatch.setattr(lm, "REPO", repo)
    monkeypatch.setattr(lm, "LAYOUT", layout_path)
    monkeypatch.setattr(lm, "MAP", map_path)
    monkeypatch.setattr(lm, "ENV_SEAMS", {})
    monkeypatch.setattr(lm, "scan", lambda inv: lm.Scan({}, {}, {}, []))
    monkeypatch.setattr(lm, "executables", lambda inv: {})

    def build(body: str) -> None:
        inv, _ = _literal_area(repo, body)
        layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        graph = lm.import_graph(inv)
        map_path.write_text(lm.render_map(layout, graph, lm.scan(inv), lm.executables(inv)), encoding="utf-8")

    build('x = 1\n')
    assert lm.main(["--check"]) == 0, capsys.readouterr().out
    assert "раскладка совпадает с кодом" in capsys.readouterr().out

    build('x = "Люди"\n')
    assert lm.main(["--check"]) == 1
    assert "литерал" in capsys.readouterr().out

    # литерал без карточки блокирует запись артефакта, как ребро без карточки
    before = layout_path.read_text(encoding="utf-8")
    assert lm.main(["--regen"]) == 1
    out = capsys.readouterr().out
    assert "без карточки" in out and "литерал" in out
    assert layout_path.read_text(encoding="utf-8") == before, "литерал без карточки не пишется"

    # отказ замера: файл значений не совпадает с аннотациями
    (repo / "src" / "schema_values.py").write_text(_values_source(star=True), encoding="utf-8")
    assert lm.main(["--regen"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("✗"), out
    assert layout_path.read_text(encoding="utf-8") == before, "при отказе замера артефакт не пишется"
