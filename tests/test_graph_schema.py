"""Схема хранилища графа: значение Чароита и его инварианты (№422, PR A).

Схема (`charoite_graph.graph_schema.GraphSchema`) — значение, а не литералы по
месту; `charoite_schema.CHAROITE` — единственное место значений владельца.
Здесь два сторожа: снимок `CHAROITE` против живых констант кода (пока те живы —
их переводит PR B) и инварианты, каждый отказ по отдельности. Снимок называет
модуль каждого источника; ядер отдельной константы в коде нет, и это ожидание
записано руками с адресом `файл:строка`.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import charoite_schema  # noqa: E402
import meeting_archive  # noqa: E402
from charoite_graph import dossier, graph_nodes, graph_search  # noqa: E402
from charoite_graph import graph_schema  # noqa: E402
from charoite_graph.graph_schema import GraphSchema  # noqa: E402

CHAROITE = charoite_schema.CHAROITE


def _person_folders() -> tuple[str, ...]:
    """Папки-люди из `Node.person` (`self.folder in (…)`) — тем же AST."""
    tree = ast.parse(inspect.getsource(graph_nodes))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "person":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Compare) and any(isinstance(op, ast.In) for op in sub.ops):
                    tuple_ = sub.comparators[0]
                    assert isinstance(tuple_, ast.Tuple), tuple_
                    return tuple(e.value for e in tuple_.elts)
    raise AssertionError("Node.person: кортеж папок-людей не найден")


def _link_prefixes() -> tuple[str, ...]:
    """Префиксы цели ссылки на встречу — кортеж в `startswith((…))` внутри
    `graph_nodes._strip_links` (тот же литерал читает и сторож литералов)."""
    tree = ast.parse(inspect.getsource(graph_nodes))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_strip_links":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                        and sub.func.attr == "startswith" and sub.args:
                    tuple_ = sub.args[0]
                    assert isinstance(tuple_, ast.Tuple), tuple_
                    values = tuple(e.value for e in tuple_.elts)
                    if "Встречи" in values:
                        return values
    raise AssertionError("_strip_links: кортеж префиксов встречи не найден")


def test_charoite_snapshot_matches_the_live_constants():
    """Снимок значения — против констант, пока те живы: у каждого источника
    назван модуль. Снимок не переживёт переезд потребителей, и это правильно:
    тогда источники исчезнут вместе с литералами (PR B)."""
    assert CHAROITE.node_folders == graph_nodes.NODE_FOLDERS, "graph_nodes.NODE_FOLDERS"
    assert CHAROITE.history_heads == graph_nodes.HISTORY_HEADS, "graph_nodes.HISTORY_HEADS"
    assert CHAROITE.dossier_dir == dossier.DOSSIER_DIR, "dossier.DOSSIER_DIR"
    assert CHAROITE.exclude_dirs == graph_search.EXCLUDE_DIRS, "graph_search.EXCLUDE_DIRS"
    assert CHAROITE.service_prefixes == graph_search._SERVICE_PREFIXES, "graph_search._SERVICE_PREFIXES"
    assert CHAROITE.people_folders == _person_folders(), "graph_nodes.Node.person"
    assert CHAROITE.meeting_link_prefixes == _link_prefixes(), "graph_nodes._strip_links"
    # сырьё — из `_RAW_RX.pattern`: делится по «|» ДО снятия якорей, кусок с «$» — суффикс
    pieces = graph_search._RAW_RX.pattern.split("|")
    assert CHAROITE.raw_markers == tuple(p for p in pieces if not p.endswith("$")), "graph_search._RAW_RX"
    assert CHAROITE.raw_suffixes == tuple(p.replace("\\", "").rstrip("$")
                                          for p in pieces if p.endswith("$")), "graph_search._RAW_RX"
    # встречи — тот же литерал `_strip_links` плюс архив `meeting_archive.ARCHIVE_DIR`
    assert set(CHAROITE.meeting_folders) == set(_link_prefixes()) | {meeting_archive.ARCHIVE_DIR}, \
        "graph_nodes._strip_links + meeting_archive.ARCHIVE_DIR"
    assert CHAROITE.meeting_dir in CHAROITE.meeting_folders, "meeting_archive._strip_links"
    # ядер отдельной константой в коде нет — ожидание руками
    assert CHAROITE.core_folders == ("Ядра", "Cores")  # graph_nodes.py:29 (в NODE_FOLDERS), рукой


def test_the_schema_is_frozen():
    """Схема — значение: её поля не переписываются после конструирования."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        CHAROITE.dossier_dir = "Другое"


def test_every_field_has_no_default():
    """У полей схемы нет значений по умолчанию: умолчание сделало бы схему
    частичной, и забытое поле молча взяло бы чужое имя."""
    for field in dataclasses.fields(GraphSchema):
        assert field.default is dataclasses.MISSING, f"{field.name}: значение по умолчанию"
        assert field.default_factory is dataclasses.MISSING, f"{field.name}: фабрика по умолчанию"


def _kwargs(**over) -> dict:
    """Полный набор значений Чароита с подменой — для проверки одного инварианта."""
    base = {field.name: getattr(CHAROITE, field.name) for field in dataclasses.fields(GraphSchema)}
    base.update(over)
    return base


@pytest.mark.parametrize("имя, over, фрагмент", [
    # инвариант 1: форма имени
    ("пустое", {"dossier_dir": ""}, "пустая строка это отсутствие имени"),
    ("пустой сегмент пути", {"exclude_dirs": ("Документация//Копии",)}, "непустое"),
    ("пробельное", {"dossier_dir": "   "}, "непустое"),
    ("с косой", {"meeting_dir": "Встречи/Х"}, "без «/»"),
    ("с точки", {"meeting_link_prefixes": (".Встречи",)}, "точки"),
    ("сегмент пути", {"exclude_dirs": ("Документация//Х",)}, "сегмент"),
    # инвариант 2: литерал, не регулярка
    ("пустой литерал", {"raw_markers": ("",)}, "пустая строка это отсутствие имени"),
    ("маркер с |", {"raw_markers": ("стенограмм|transcript",)}, "метки регулярки"),
    ("суффикс с экранированием", {"raw_suffixes": ("_live\\.md$",)}, "метки регулярки"),
    ("голова с ^", {"history_heads": ("## ^Встречи",)}, "метки регулярки"),
    # инвариант 3: подмножества
    ("люди не узлы", {"people_folders": ("Люди", "Призраки")}, "people_folders"),
    ("ядра не узлы", {"core_folders": ("Ядра", "Cores", "Призраки")}, "core_folders"),
    # инвариант 4: папка встреч
    ("не та папка встреч", {"meeting_dir": "Совещания"}, "meeting_dir"),
    # инвариант 5: досье против исключений
    ("досье внутри исключения", {"exclude_dirs": ("Документация/Досье",)}, "досье"),
    # инвариант 6: исключение не пересекает узлы
    ("исключение — папка узла", {"exclude_dirs": ("Люди",)}, "папк"),
    # инвариант 7: роли не пересекаются
    ("люди и ядра", {"core_folders": ("Ядра", "Cores", "Люди")}, "пересекаются"),
    ("узлы и встречи", {"meeting_folders": ("Встречи", "Встречи-архив", "Meetings", "Люди"),
                        "meeting_dir": "Люди"}, "пересекаются"),
    ("досье и узлы", {"dossier_dir": "Люди"}, "пересекаются"),
])
def test_graph_schema_refuses_each_invariant(имя, over, фрагмент):
    """Каждый инвариант отказывает сам по себе: подмена одной пары значений —
    и конструктор бросает `ValueError` с внятной причиной, а не пропускает
    схему, которой нельзя верить."""
    with pytest.raises(ValueError, match=фрагмент):
        GraphSchema(**_kwargs(**over))


def test_the_charoite_snapshot_constructs_and_passes_every_invariant():
    """Значение Чароита собирается и проходит все инварианты 1–7 сразу: снимок
    не только «совпадает с константами», но и сам себе не врёт."""
    assert isinstance(CHAROITE, GraphSchema)
    assert GraphSchema(**_kwargs()) == CHAROITE


@pytest.mark.parametrize("значение, ждём", [
    ("Черновики", ("Черновики",)),
    ("Документация/Черновики", ("Документация/Черновики",)),
    (["Черновики", "Документация/Черновики"], ("Черновики", "Документация/Черновики")),
], ids=["строка-имя", "строка-путь", "список"])
def test_поле_кортеж_хранится_кортежем_и_строка_это_одно_значение(значение, ждём):
    """Строка в поле-кортеже — одно имя или путь, а не набор букв: владелец
    значения приводит форму сам, и потребителю толковать её нечего (выходной
    круг 2 по #654, DS C1)."""
    своя = dataclasses.replace(charoite_schema.CHAROITE, exclude_dirs=значение)
    assert своя.exclude_dirs == ждём


def test_каждое_поле_кортеж_проходит_дверь_формы():
    """Список в ЛЮБОМ поле-кортеже хранится кортежем: нормализацию проходят все
    поля формы `tuple[str, ...]`, а не одно проверенное; у двух оставшихся полей
    форма — строка (выходной круг 3 по #654, DS M3)."""
    поля = [f.name for f in dataclasses.fields(GraphSchema) if f.type == "tuple[str, ...]"]
    строки = {f.name for f in dataclasses.fields(GraphSchema)} - set(поля)
    assert строки == {"dossier_dir", "meeting_dir"}
    своя = dataclasses.replace(CHAROITE, **{имя: list(getattr(CHAROITE, имя)) for имя in поля})
    assert [имя for имя in поля if type(getattr(своя, имя)) is not tuple] == []
    assert своя == CHAROITE


@pytest.mark.parametrize("значение", [
    {"Черновики"}, b"Drafts", 7, None, ["Черновики", 7],
], ids=["множество", "байты", "число", "None", "не-строка-в-списке"])
def test_дверь_формы_отказывает_не_имени(значение):
    """Дверь формы принимает строку, кортеж и список строк; прочее — `ValueError`
    с именем поля: у множества нет порядка, байты рассыпались бы на числа, а
    не-строка дошла бы до сравнения имён чужим исключением (круг 3, M2)."""
    with pytest.raises(ValueError, match="exclude_dirs"):
        dataclasses.replace(CHAROITE, exclude_dirs=значение)


def test_поле_строка_принимает_только_строку():
    """`dossier_dir` и `meeting_dir` — одно имя: кортеж там отвергается
    обещанным `ValueError`, а не `TypeError` из `unicodedata` (круг 3, M2)."""
    for имя in ("dossier_dir", "meeting_dir"):
        with pytest.raises(ValueError, match=имя):
            dataclasses.replace(CHAROITE, **{имя: (getattr(CHAROITE, имя),)})


def test_поле_без_объявленной_формы_отказ_класса():
    """Поле, чья аннотация не `str` и не `tuple[str, ...]`, — ошибка класса
    (`TypeError`), а не молчаливый пропуск нормализации (круг 3, M1)."""
    @dataclasses.dataclass(frozen=True)
    class Шире(GraphSchema):
        лишнее: int = 0

    with pytest.raises(TypeError, match="лишнее"):
        Шире(**_kwargs())


def test_форма_поля_читается_вычисленной_а_не_текстом():
    """Аннотация объектом (класс собран без `from __future__ import annotations`)
    даёт ту же форму, что и текстом: поле-кортеж нормализуется, поле-строка
    проверяется (выходной круг 4 по #654, DS I1)."""
    Своя = dataclasses.make_dataclass(
        "Своя", [("ещё", tuple[str, ...], dataclasses.field(default=())),
                 ("одно", str, dataclasses.field(default="x"))],
        bases=(GraphSchema,), frozen=True)
    assert Своя(**_kwargs(), ещё=["a", "b"]).ещё == ("a", "b")
    with pytest.raises(ValueError, match="одно"):
        Своя(**_kwargs(), одно=("x",))


def test_каждое_поле_схемы_под_своим_инвариантом():
    """Поле вне имён, литералов и путей исключений нормализовалось бы, но не
    проверялось ни на пустоту, ни на «/», ни на метки регулярки (выходной круг 4 по
    #654, DS I2)."""
    поля = {f.name for f in dataclasses.fields(GraphSchema)}
    assert set(graph_schema._NAME_FIELDS) | set(graph_schema._LITERAL_FIELDS) | {"exclude_dirs"} == поля


def test_дверь_формы_называет_виновника_и_отказывает_пустому_имени():
    """Отказ называет номер и значение, а не только контейнер (круг 4, M2); пустое
    имя — отказ и в `exclude` поиска, как в полях схемы (круг 4, M3)."""
    with pytest.raises(ValueError, match=r"exclude_dirs: значение №2 \(7\)"):
        dataclasses.replace(CHAROITE, exclude_dirs=["Черновики", 7])
    for пустое in ("", ["Черновики", ""]):
        with pytest.raises(ValueError, match="пуст"):
            graph_schema.as_names(пустое, "exclude")
    assert graph_schema.as_names([], "exclude") == ()
