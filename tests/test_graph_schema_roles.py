"""Роли хранилища — предикаты схемы и их потребители (№422, PR B).

Схема отвечает на вопрос о пути предикатом, потребители пакета — поиск, досье,
индекс узлов — спрашивают только её. Приёмка постановки в три слоя:

- оборот схемы: каждое имя значения Чароита заменено ASCII-токеном, граф собран
  только из имён схемы, и наблюдаемое поведение потребителей после обратной
  подстановки совпадает с поведением на `CHAROITE`. Литерал имени, переживший
  перевод, на обороте расходится — токен не равен ни одному литералу;
- `PLAIN`: хранилище без ролей — предикаты отвечают «нет» сами;
- изменения поведения, названные в постановке, — по тесту на каждое.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
import unicodedata

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from charoite_graph import dossier, graph_nodes  # noqa: E402
from charoite_graph import graph_search as gs  # noqa: E402
from charoite_graph.graph_schema import PLAIN, GraphSchema  # noqa: E402
from charoite_graph.model_seam import Embedder  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402


def _embedder() -> Embedder:
    """Векторы не наблюдаются — нужен только шов: постоянный вектор под своим именем."""
    return Embedder(lambda texts, timeout: [[1.0, 0.0] for _ in texts], "test-roles")


# ---------------------------------------------------------------- оборот схемы

_TOKEN = re.compile(r"zz\d{2}")
_HEAD_MARK = re.compile(r"#+ ")


def rotate(schema: GraphSchema) -> tuple[GraphSchema, dict[str, str]]:
    """Биекция имён схемы в ASCII-токены и обратная подстановка.

    Равные имена — один токен: архив встреч в `meeting_folders` и в `exclude_dirs` —
    одна папка на диске, префикс ссылки на встречу — имя папки встреч. Голова
    истории и суффикс сырья — значения целиком, у них свои токены: совпадение
    «## Встречи» с папкой «Встречи» — случайность написания, и потребитель, который
    выводил бы одно из другого, на обороте разойдётся. Меняются имена, а не
    грамматика: разделитель пути «/», маркер заголовка «# » и расширение «.md» —
    правила продукта, а не значения схемы, и оборот их сохраняет."""
    token_of: dict[tuple[str, str], str] = {}
    back: dict[str, str] = {}

    def tok(kind: str, key: str, text: str) -> str:
        if (kind, key) not in token_of:
            token_of[(kind, key)] = f"zz{len(token_of):02d}"
            back[token_of[(kind, key)]] = text
        return token_of[(kind, key)]

    def one(field: str, value: str) -> str:
        if field == "history_heads":
            mark = _HEAD_MARK.match(value)
            assert mark, f"голова истории без маркера заголовка: {value!r}"
            return mark.group() + tok("head", value, value[mark.end():])
        if field == "raw_suffixes" and value.endswith(".md"):
            return tok("suffix", value, value[:-3]) + ".md"
        return "/".join(tok("name", seg, seg) for seg in value.split("/"))

    rotated = {}
    for f in dataclasses.fields(schema):
        value = getattr(schema, f.name)
        rotated[f.name] = (None if value is None else one(f.name, value) if isinstance(value, str)
                           else tuple(one(f.name, v) for v in value))
    return GraphSchema(**rotated), back


def _unrotate(value, back: dict[str, str]):
    """Обратная подстановка токенов во всём наблюдаемом: строки, кортежи,
    множества, словари."""
    if isinstance(value, str):
        return _TOKEN.sub(lambda m: back[m.group()], value)
    if isinstance(value, tuple):
        return tuple(_unrotate(v, back) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_unrotate(v, back) for v in value)
    if isinstance(value, dict):
        return {_unrotate(k, back): _unrotate(v, back) for k, v in value.items()}
    return value


def build(s: GraphSchema, root: pathlib.Path) -> None:
    """Граф из имён схемы: имя каждой папки, префикс служебного, маркер и суффикс
    сырья, голова истории и префикс ссылки — значение `s`; имена файлов и текст от
    схемы не зависят. Два правила против зависимости от написания токена (вопрос 2
    постановки): у тёзок в `scan` нет равного ранга — при равенстве дубль решал бы
    путь, то есть написание; и заметки, чей порядок в кластере решает роль папки,
    названы так, что алфавит один на обе схемы."""
    people = s.people_folders[0]
    plain = next(f for f in s.node_folders if f not in s.people_folders + s.core_folders)
    core = s.core_folders[0]
    meeting = s.meeting_dir
    archive = next(f for f in s.meeting_folders if f in s.exclude_dirs)
    copies = next(e for e in s.exclude_dirs if "/" in e)
    docs = copies.split("/")[0]
    head, chronicle = s.history_heads[0], s.history_heads[1]
    link = s.meeting_link_prefixes[0]
    under, service = s.service_prefixes
    marker, suffix = s.raw_markers[0], s.raw_suffixes[0]
    files = {
        f"{people}/Иван Мироненко.md": (
            f"# Иван Мироненко\nВедёт интеграцию платёжного шлюза.\n\n{head}\n"
            f"- [[{meeting}/2026-08-01_1000]] — взял интеграцию на себя\n"
            f"- [[{link}/Итоговая сводка]] — подвёл итоги квартала\n"
            f"- [[{plain}/Платёжный шлюз|шлюз]] согласовал доступ\n"
            "\n## Заметки\n- не история\n"),
        f"{people}/Собеседник 3.md": f"# Собеседник 3\n\n{head}\n- [[{meeting}/2026-08-01_1000]] — говорил\n",
        f"{plain}/Платёжный шлюз.md": (
            f"# Платёжный шлюз\nСтатус: пилот до сентября\n\n{chronicle}\n"
            f"- 2026-08-02 запуск [[{core}/Платежи]]\n"),
        # тёзка ядра длиннее ядра: выигрывает ядро только по роли папки
        f"{plain}/Платежи.md": "# Платежи\n" + "тёзка ядра, подробный текст. " * 20 + "\n",
        f"{plain}/{service}отчёт.md": "# отчёт\nслужебный файл в папке узлов\n",
        f"{core}/Платежи.md": f"# Платежи\n[[{plain}/Платёжный шлюз]]\n",
        f"{meeting}/2026-08-01_1000.md": f"# Интеграция\n[[{people}/Иван Мироненко]] [[{core}/Платежи]]\n",
        f"{meeting}/2026-08-02_1500.md": f"# Запуск\n[[Платежи]] [[{plain}/Платёжный шлюз]]\n",
        # встреча без даты в имени: по алфавиту после «Аудита», в кластере — раньше
        f"{meeting}/Итоги квартала.md": "# Итоги\n[[Платежи]]\n",
        f"{meeting}/2026-08-01_1000_{marker}а.md": "сырьё: интеграция платёжного шлюза\n",
        f"{meeting}/2026-08-01_1000{suffix}": "живой лог встречи\n",
        f"{archive}/2026-01-01_старое.md": f"# Старое\n[[{core}/Платежи]] архив\n",
        f"{archive}/Юбилей платежей.md": "# Юбилей\n[[Платежи]]\n",
        f"{copies}/копия.md": "копия стенограммы [[Платежи]]\n",
        f"{docs}/Аудит платежей.md": "# Аудит\n[[Платежи]]\n",
        f"{docs}/Концепция шлюза.md": "# Концепция\nплатёжный шлюз [[Платежи]]\n",
        f"{s.dossier_dir}/Платежи.md": "# Платежи — сводка\nсводка темы\n",
        f"{under}MOC.md": f"# MOC\n[[{plain}/Платёжный шлюз]]\n",
        f"{service}ревизия.md": "служебный отчёт ревизии\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    # индекс досье пишет сам пакет — его имя не значение схемы
    dossier.write_index(root / s.dossier_dir, [{"тема": "Платежи", "источников": 5, "собрано": "2026-08-03",
                                                "ключи": ["платежи"], "файл": f"{s.dossier_dir}/Платежи.md"}])


def observe(s: GraphSchema, root: pathlib.Path, data: pathlib.Path) -> dict:
    """Наблюдаемое поведение трёх потребителей на графе `root` при схеме `s`."""
    search = gs.GraphSearch(root, embedder=_embedder(), data_dir=data, schema=s)
    search.refresh(force=True)
    gen = search._gen
    on_disk = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))
    files, backlinks = dossier.scan(root, schema=s)
    nodes = graph_nodes.NodeIndex(root, schema=s)
    nodes.refresh()
    return {
        "роль в поколении": frozenset((d.rel, d.role) for d in gen.docs.values()),
        "отсечено обходом": frozenset(gen.skipped),
        "служебных вне индекса": gen.service,
        "узел для поиска": frozenset(r for r in on_disk if s.is_node_path(r)),
        # демпфер действует в выдаче — на документах поколения, а не на исключённых
        # копиях: у Чароита имя папки копий само содержит маркер сырья, и на
        # обороте это совпадение двух значений схемы исчезает
        "сырьё": frozenset(d.rel for d in gen.docs.values() if gs.raw_dampener(d.rel, s) < 1.0),
        "состав scan": frozenset((t, m["rel"], m["kind"]) for t, m in files.items()),
        "обратные ссылки": frozenset((t, frozenset(who)) for t, who in backlinks.items()),
        # порядок кластера решают роль папки и имя файла, а не написание токена
        "кластеры": {t: tuple(m) for t, m in dossier.clusters(files, backlinks, schema=s).items()},
        "узлы индекса": frozenset((n.folder, n.name, n.person) for n in nodes._nodes.values()),
        "дайджест": frozenset((n.name, n.digest_lines) for n in nodes._nodes.values()),
    }


def test_оборот_схемы_не_меняет_наблюдаемого_поведения(tmp_path):
    """Постановка п. 10: `obs(CHAROITE) == back(obs(rotate(CHAROITE)))`. Литерал
    имени Чароита в любом из трёх обходов (`_walk`, `scan`, `refresh`) на обороте
    перестал бы совпадать с токеном — и наблюдаемое разошлось бы."""
    rotated, back = rotate(CHAROITE)
    assert rotated != CHAROITE and all(_TOKEN.fullmatch(v) for v in rotated.node_folders)
    build(CHAROITE, tmp_path / "как есть")
    build(rotated, tmp_path / "оборот")
    ours = observe(CHAROITE, tmp_path / "как есть", tmp_path / "данные-1")
    theirs = observe(rotated, tmp_path / "оборот", tmp_path / "данные-2")
    assert _unrotate(theirs, back) == ours


def test_у_каждой_наблюдаемой_оборота_есть_положительный_случай(tmp_path):
    """Наблюдаемая, пустая на `CHAROITE`, совпала бы на обороте и при сломанном
    переводе — поэтому у каждой здесь свой положительный случай."""
    build(CHAROITE, tmp_path / "г")
    obs = observe(CHAROITE, tmp_path / "г", tmp_path / "данные")
    roles = dict(obs["роль в поколении"])
    assert roles["Досье/Платежи.md"] == gs.DOSSIER and roles["Встречи/Итоги квартала.md"] == gs.PRIMARY
    assert obs["отсечено обходом"] == {"Встречи-архив", "Документация/Стенограммы встреч"}
    # _MOC, Служебное_ревизия, служебный отчёт в папке узлов и индекс досье
    assert obs["служебных вне индекса"] == 4
    assert "Люди/Иван Мироненко.md" in obs["узел для поиска"]
    assert "Команды/Служебное_отчёт.md" not in obs["узел для поиска"]
    assert obs["сырьё"] == {"Встречи/2026-08-01_1000_стенограмма.md", "Встречи/2026-08-01_1000_live.md"}
    assert ("Платежи", "Ядра/Платежи.md", "Ядра") in obs["состав scan"], "тёзка ядра проиграл по роли папки"
    assert not any(rel.startswith("Досье/") for _t, rel, _k in obs["состав scan"])
    assert dict(obs["обратные ссылки"])["Платежи"] >= {"2026-08-01_1000", "Юбилей платежей", "копия"}
    assert obs["кластеры"]["Платежи"][:6] == ("Платежи", "2026-01-01_старое", "2026-08-01_1000",
                                              "2026-08-02_1500", "Итоги квартала", "Юбилей платежей")
    nodes = {(folder, name): person for folder, name, person in obs["узлы индекса"]}
    assert nodes[("Люди", "Иван Мироненко")] is True and nodes[("Ядра", "Платежи")] is False
    assert ("Люди", "Собеседник 3") not in nodes and ("Команды", "Служебное_отчёт") not in nodes
    ivan = dict(obs["дайджест"])["Иван Мироненко"]
    assert "подвёл итоги квартала" in ivan and not any("Итоговая" in line for line in ivan)


def test_оборот_ловит_литерал_имени(tmp_path, monkeypatch):
    """Опровергающий опыт: верни в потребителя литерал «Ядра» — оборот расходится.
    Без этого совпадение оборота ничего бы не доказывало."""
    rotated, back = rotate(CHAROITE)
    monkeypatch.setattr(GraphSchema, "is_core_folder", lambda self, name: name == "Ядра")
    build(CHAROITE, tmp_path / "как есть")
    build(rotated, tmp_path / "оборот")
    ours = observe(CHAROITE, tmp_path / "как есть", tmp_path / "данные-1")
    theirs = observe(rotated, tmp_path / "оборот", tmp_path / "данные-2")
    assert _unrotate(theirs, back)["кластеры"] != ours["кластеры"]
    assert _unrotate(theirs, back)["состав scan"] != ours["состав scan"]


# ---------------------------------------------------------------- предикаты

@pytest.mark.parametrize("предикат, путь, ждём", [
    ("is_service_name", "_MOC.md", True),
    ("is_service_name", "Служебное_ревизия.md", True),
    ("is_service_name", "служебное_ревизия.md", True),
    ("is_service_name", "Иван.md", False),
    ("is_dossier", "Досье/Платежи.md", True),
    ("is_dossier", "досье/Платежи.md", True),
    ("is_dossier", "Досье", False),
    ("is_dossier", "Люди/Досье/x.md", False),
    ("is_node_path", "Люди/Иван.md", True),
    ("is_node_path", "Проект/Люди/Иван.md", True),
    ("is_node_path", "Люди/_ЛЮДИ.md", False),
    ("is_node_path", "Встречи/2026-08-01.md", False),
    ("is_node_path", "Иван.md", False),
    ("is_person_folder", "People", True),
    ("is_person_folder", "Системы", False),
    ("is_core_folder", "Cores", True),
    ("is_core_folder", "ядра", True),
    ("is_meeting_folder", "Встречи-архив", True),
    ("is_meeting_folder", "Meetings", True),
    ("is_meeting_folder", "Люди", False),
    ("excluded", "Встречи-архив", True),
    ("excluded", "Документация/Стенограммы встреч", True),
    ("excluded", "Документация", False),
    ("excluded", "Встречи-архив/2026", False),
    ("is_raw", "Встречи/2026-08-01_стенограмма.md", True),
    ("is_raw", "Встречи/Transcript.md", True),
    ("is_raw", "Встречи/2026-08-01_live.md", True),
    ("is_raw", "Встречи/2026-08-01_live.md.bak", False),
    ("is_raw", "Встречи/2026-08-01.md", False),
    ("is_meeting_link", "Встречи/Итоги", True),
    ("is_meeting_link", "Архив/2026-08-01", True),
    ("is_meeting_link", "Люди/Иван", False),
    ("is_history_head", "## Хроника", True),
    ("is_history_head", "  ## Встречи (12)", True),
    ("is_history_head", "## Заметки", False),
])
def test_предикаты_схемы_чароита(предикат, путь, ждём):
    """Каждый предикат — на своём положительном и отрицательном случае; регистр и
    ё — формой `fold`, как у поиска."""
    assert getattr(CHAROITE, предикат)(путь) is ждём


@pytest.mark.parametrize("предикат, путь", [
    ("is_service_name", "_MOC.md"), ("is_dossier", "Досье/Платежи.md"), ("is_node_path", "Люди/Иван.md"),
    ("is_person_folder", "Люди"), ("is_core_folder", "Ядра"), ("is_meeting_folder", "Встречи"),
    ("excluded", "Встречи-архив"), ("is_raw", "стенограмма.md"), ("is_meeting_link", "Встречи/Итоги"),
    ("is_history_head", "## Встречи"),
])
def test_plain_отвечает_нет_само(предикат, путь):
    """`PLAIN` — значение без ролей: отдельной реализации «без ролей» нет, предикаты
    отвечают «нет» на пустых полях. Дата в цели ссылки — правило схемы, а не поле,
    и узнаётся у любой схемы (проверено отдельно)."""
    assert getattr(PLAIN, предикат)(путь) is False


def test_plain_узнаёт_встречу_по_дате_в_ссылке():
    assert PLAIN.is_meeting_link("Архив/2026-08-01_1000") is True


def test_третья_форма_поля_роли_нет():
    """`dossier_dir` и `meeting_dir` — `str | None`: `None` — значение формы «роли
    нет», а не пропуск проверки; пустая строка по-прежнему отказ."""
    assert PLAIN.dossier_dir is None and PLAIN.meeting_dir is None
    no_dossier = dataclasses.replace(CHAROITE, dossier_dir=None)
    assert no_dossier.is_dossier("Досье/Платежи.md") is False
    with pytest.raises(ValueError, match="пустая строка"):
        dataclasses.replace(CHAROITE, dossier_dir="")
    with pytest.raises(ValueError, match="meeting_dir"):
        dataclasses.replace(CHAROITE, meeting_dir=7)


def test_кэш_имён_роли_у_каждого_значения_свой():
    """Схема заморожена, кэш свёрнутых имён пишется мимо запрета присваивания — у
    `replace` он свой, чужой роли не наследует."""
    assert CHAROITE.is_core_folder("Ядра")
    other = dataclasses.replace(CHAROITE, core_folders=("Cores",))
    assert other.is_core_folder("Ядра") is False and CHAROITE.is_core_folder("Ядра") is True


# ---------------------------------------------------------------- выведенные роли

def test_роль_документа_и_человек_узла_выводятся_а_не_передаются(tmp_path):
    """`Doc.role` и `Node.person` — поля `init=False`: конструктору их не передать,
    значение считает схема, которую объект не хранит."""
    doc = gs.Doc("/x", "Досье/Платежи.md", 0.0, "", "", 0.0, "платежи", schema=CHAROITE)
    assert doc.role == gs.DOSSIER and not hasattr(doc, "schema")
    assert gs.Doc("/x", "Досье/Платежи.md", 0.0, "", "", 0.0, "платежи", schema=PLAIN).role == gs.PRIMARY
    with pytest.raises(TypeError):
        gs.Doc("/x", "a.md", 0.0, "", "", 0.0, "a", schema=CHAROITE, role=gs.DOSSIER)
    with pytest.raises(TypeError):
        gs.Doc("/x", "a.md", 0.0, "", "", 0.0, "a")
    node = graph_nodes.Node(tmp_path / "Иван.md", "People", "Иван", schema=CHAROITE)
    assert node.person is True and not hasattr(node, "schema")
    assert graph_nodes.Node(tmp_path / "Иван.md", "People", "Иван", schema=PLAIN).person is False
    with pytest.raises(TypeError):
        graph_nodes.Node(tmp_path / "Иван.md", "Люди", "Иван", schema=CHAROITE, person=False)


def test_схема_обязательна_у_индекса_узлов_и_досье(tmp_path):
    """Их зовёт только Чароит: забытая схема — `TypeError`, а не молча пустой
    индекс узлов на встрече."""
    with pytest.raises(TypeError):
        graph_nodes.NodeIndex(tmp_path)
    with pytest.raises(TypeError):
        dossier.scan(tmp_path)
    with pytest.raises(TypeError):
        dossier.clusters({}, {})


# ---------------------------------------------------------------- PLAIN у поиска

def test_индекс_досье_служебный_по_имени_а_не_по_префиксу_схемы():
    """Индекс досье пишет пакет, его имя не значение схемы: у хранилища без «_»
    среди служебных префиксов он всё равно служебный, а не документ досье.
    Прочие файлы папки досье — досье; у Чароита ответ прежний."""
    no_under = dataclasses.replace(CHAROITE, service_prefixes=("Служебное_",))
    assert gs.doc_role(f"Досье/{dossier.INDEX_MD}", no_under) == gs.SERVICE
    assert gs.doc_role("Досье/_Заметка.md", no_under) == gs.DOSSIER
    assert gs.doc_role(f"Досье/{dossier.INDEX_MD}", CHAROITE) == gs.SERVICE
    assert gs.doc_role(dossier.INDEX_MD, no_under) == gs.PRIMARY, "вне папки досье — не индекс досье"


def test_поиск_без_схемы_простое_хранилище(tmp_path):
    """`GraphSearch` без схемы — `PLAIN`: ни узлов, ни досье, ни служебного, ни
    исключений; архивная папка стороннего хранилища больше не исключается молча
    (изменение поведения п. 9)."""
    g = tmp_path / "г"
    for rel in ("Встречи-архив/2026-01-01.md", "Досье/Тема.md", "_MOC.md", "Люди/Иван.md",
                "Встречи/2026-08-01_стенограмма.md"):
        (g / rel).parent.mkdir(parents=True, exist_ok=True)
        (g / rel).write_text("текст заметки\n", encoding="utf-8")
    search = gs.GraphSearch(g, embedder=_embedder(), data_dir=tmp_path / "данные")
    assert search.schema is PLAIN and search.exclude == ()
    search.refresh(force=True)
    gen = search._gen
    assert {d.rel: d.role for d in gen.docs.values()} == {
        "Встречи-архив/2026-01-01.md": gs.PRIMARY, "Досье/Тема.md": gs.PRIMARY, "_MOC.md": gs.PRIMARY,
        "Люди/Иван.md": gs.PRIMARY, "Встречи/2026-08-01_стенограмма.md": gs.PRIMARY}
    assert gen.skipped == () and gen.service == 0
    assert gs.raw_dampener("Встречи/2026-08-01_стенограмма.md", search.schema) == 1.0
    assert search._dossier_blocks("тема", 200, gen) == ([], 0.0)


# ---------------------------------------------------------------- кэш векторов

def _long_node(root: pathlib.Path) -> pathlib.Path:
    """Узел длиннее потолка блоков не-узла: у узла блоков больше."""
    p = root / "Люди" / "Иван.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# Иван\n\n" + "\n\n".join(f"## Встреча {i}\n" + f"абзац {i} " * 600 for i in range(30)),
                 encoding="utf-8")
    return p


def test_кэш_векторов_сверяет_число_блоков_при_том_же_mtime(tmp_path):
    """Постановка п. 5: потолок блоков зависит от роли файла, а роль — от схемы.
    Тот же mtime и другое число блоков — файл уходит в доиндексацию, а не живёт с
    векторами чужой нарезки. Ключ кэша схему не подписывает: у владельца с
    постоянной схемой кэш не холодеет."""
    g = tmp_path / "г"
    node = _long_node(g)
    data = tmp_path / "данные"
    as_node = gs.GraphSearch(g, embedder=_embedder(), data_dir=data, schema=CHAROITE)
    as_node.refresh(force=True)
    assert as_node.embed_pending() == 1
    assert len(as_node._vecs[str(node)][1]) == gs.MAX_CHUNKS_NODE
    assert as_node.pending_vectors() == []
    as_plain = gs.GraphSearch(g, embedder=_embedder(), data_dir=data, schema=PLAIN)
    assert as_plain.cache_key() == as_node.cache_key()
    as_plain.refresh(force=True)
    assert as_plain.pending_vectors() == [str(node)], "векторы нарезки узла под схемой без узлов"
    assert as_plain.embed_pending() == 1
    assert len(as_plain._vecs[str(node)][1]) == gs.MAX_CHUNKS
    again = gs.GraphSearch(g, embedder=_embedder(), data_dir=data, schema=PLAIN)
    again.refresh(force=True)
    assert again.pending_vectors() == [], "та же схема — кэш тёплый"


def test_число_блоков_считается_раз_на_версию_файла(tmp_path, monkeypatch):
    """Нарезка для сверки кэша — один раз на (mtime, потолок), а не на каждую сверку."""
    g = tmp_path / "г"
    _long_node(g)
    search = gs.GraphSearch(g, embedder=_embedder(), data_dir=tmp_path / "данные", schema=CHAROITE)
    search.refresh(force=True)
    calls = []
    real = gs.chunks
    monkeypatch.setattr(gs, "chunks", lambda *a, **kw: (calls.append(1), real(*a, **kw))[1])
    search.pending_vectors()
    search.pending_vectors()
    assert len(calls) == 1


# ---------------------------------------------------------------- изменения поведения (п. 9)

def _write(root: pathlib.Path, rel: str, text: str = "текст\n") -> pathlib.Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_досье_в_нижнем_регистре_то_же_в_scan_и_в_поиске(tmp_path):
    """Сравнения `scan` нормализованные, как у поиска: папка «досье» — досье и для
    обхода досье (не тема), и для поиска (роль DOSSIER). Раньше `scan` сравнивал
    литерал «Досье» точно и брал сводку в темы."""
    g = tmp_path / "г"
    _write(g, "досье/Платежи.md")
    _write(g, "Встречи/2026-08-01.md", "[[Платежи]]\n")
    files, _ = dossier.scan(g, schema=CHAROITE)
    assert "Платежи" not in files and "2026-08-01" in files
    assert gs.doc_role("досье/Платежи.md", CHAROITE) == gs.DOSSIER


def test_nfd_имя_папки_ядра_узнаётся_в_scan_и_clusters(tmp_path):
    """Имя папки с диска в разложенной форме (macOS отдаёт NFD) — та же роль, что
    в NFC у схемы: ядро с «ё» строит кластер и выигрывает ранг тёзки."""
    schema = dataclasses.replace(CHAROITE, node_folders=CHAROITE.node_folders + ("Ёмкие темы",),
                                 core_folders=CHAROITE.core_folders + ("Ёмкие темы",))
    g = tmp_path / "г"
    nfd = unicodedata.normalize("NFD", "Ёмкие темы")
    assert nfd != "Ёмкие темы"
    _write(g, f"{nfd}/Платежи.md", "[[Люди/Иван]]\n")
    _write(g, "Команды/Платежи.md", "тёзка ядра, подробный текст\n" * 10)
    for i in (1, 2):
        _write(g, f"Встречи/2026-08-0{i}.md", "[[Платежи]]\n")
    files, backlinks = dossier.scan(g, schema=schema)
    assert unicodedata.normalize("NFC", files["Платежи"]["kind"]) == "Ёмкие темы"
    assert list(dossier.clusters(files, backlinks, schema=schema)) == ["Платежи"]


def test_обход_поиска_исключает_по_нормализованному_пути(tmp_path):
    """`_walk` сравнивает накопленный путь формой `fold`: NFD-имя и другой регистр
    исключённой папки на диске исключаются. Раньше — точное `rel in self.exclude`,
    и такая папка молча попадала в индекс."""
    schema = dataclasses.replace(CHAROITE, exclude_dirs=CHAROITE.exclude_dirs + ("Черновики-ёлки",))
    g = tmp_path / "г"
    _write(g, unicodedata.normalize("NFD", "Черновики-ёлки") + "/черновик.md")
    _write(g, "встречи-архив/2026-01-01.md")
    _write(g, "Люди/Иван.md")
    search = gs.GraphSearch(g, embedder=_embedder(), data_dir=tmp_path / "данные", schema=schema)
    search.refresh(force=True)
    assert {d.rel for d in search._gen.docs.values()} == {"Люди/Иван.md"}
    assert len(search._gen.skipped) == 2


def test_кластер_вокруг_cores_и_встречи_архива_первыми(tmp_path):
    """`clusters` строит темы вокруг любой папки ядер схемы (у демо-графа — Cores),
    а к встречам для сортировки относит все `meeting_folders`, включая архив:
    раньше — только «Ядра» и только «Встречи»."""
    g = tmp_path / "г"
    _write(g, "Cores/Payments.md")
    _write(g, "Meetings/Weekly.md", "[[Payments]]\n")
    _write(g, "Встречи-архив/Юбилей.md", "[[Payments]]\n")
    _write(g, "Документация/Аудит.md", "[[Payments]]\n")
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    assert dossier.clusters(files, backlinks, schema=CHAROITE) == {
        "Payments": ["Payments", "Weekly", "Юбилей", "Аудит"]}


def test_ранг_тёзки_ядро_cores(tmp_path):
    """Ранг дубля в `scan`: ядром считается и `Cores`, сравнение нормализованное —
    короткое ядро выигрывает у длинного тёзки. Раньше выигрывал более длинный,
    раз «Cores» не равно литералу «Ядра»."""
    g = tmp_path / "г"
    _write(g, "cores/CRM.md", "ядро\n")
    _write(g, "Systems/CRM.md", "система, длинное описание\n" * 10)
    files, _ = dossier.scan(g, schema=CHAROITE)
    assert files["CRM"]["rel"] == "cores/CRM.md"


def test_служебное_в_папке_узлов_не_узел(tmp_path):
    """Файл «Служебное_*.md» в папке узлов — не узел, как уже не документ поиска.
    Раньше индекс узлов пропускал только «_» и «.»."""
    g = tmp_path / "г"
    _write(g, "Команды/Служебное_отчёт.md", "# Отчёт\n")
    _write(g, "Команды/Платформа.md", "# Платформа\n")
    idx = graph_nodes.NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert {n.name for n in idx._nodes.values()} == {"Платформа"}


def test_метка_диаризации_не_узел_во_всех_папках_людей(tmp_path):
    """Фильтр меток диаризации — во всех `people_folders`: «Speaker 2» в People
    тоже склейка разных людей. Раньше фильтр смотрел только «Люди»."""
    g = tmp_path / "г"
    _write(g, "People/Speaker 2.md", "# Speaker 2\n")
    _write(g, "People/Anna Smith.md", "# Anna Smith\n")
    _write(g, "Люди/Собеседник 3.md", "# Собеседник 3\n")
    idx = graph_nodes.NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert {n.name for n in idx._nodes.values()} == {"Anna Smith"}


def test_голова_истории_и_ссылка_на_встречу_нормализованные():
    """Голова истории и префикс ссылки на встречу сравниваются формой `fold`:
    «## встречи» — история, «[[встречи/Итоги]]» — ссылка на встречу, которая из
    дайджеста уходит. Раньше обе проверки были точным `startswith`."""
    text = "# Иван\n\n## встречи\n- [[встречи/Итоги]] — подвёл итоги\n- [[Люди/Анна|Анна]] помогла\n"
    assert graph_nodes._digest(text, "2026", schema=CHAROITE) == ["подвёл итоги", "Анна помогла"]


def test_полноширинные_имена_папок_сворачиваются(tmp_path):
    """Сравнения досье и индекса узлов — формой `fold`, как у поиска: полноширинная
    латиница в имени папки ядер — те же Cores, полноширинный «＿» — служебный
    префикс."""
    g = tmp_path / "г"
    _write(g, "Ｃｏｒｅｓ/Payments.md")
    for name in ("A", "B"):
        _write(g, f"Meetings/{name}.md", "[[Payments]]\n")
    files, backlinks = dossier.scan(g, schema=CHAROITE)
    assert list(dossier.clusters(files, backlinks, schema=CHAROITE)) == ["Payments"]
    _write(g, "Teams/＿ИНДЕКС.md", "# индекс\n")
    _write(g, "Teams/Platform.md", "# Platform\n")
    idx = graph_nodes.NodeIndex(g, schema=CHAROITE)
    idx.refresh()
    assert {n.name for n in idx._nodes.values()} == {"Platform"}


# ---------------------------------------------------------------- выдача: демпфер и фрагмент

def test_демпфер_сырья_приглушает_но_не_гасит():
    """Сырьё по схеме — ниже дистиллята при равной релевантности, но в выдаче остаётся:
    множитель строго между нулём и единицей; не-сырьё — без множителя."""
    raw = gs.raw_dampener("Встречи/2026-08-01_стенограмма.md", CHAROITE)
    assert 0.0 < raw < 1.0
    assert gs.raw_dampener("Встречи/2026-08-01.md", CHAROITE) == 1.0
    assert gs.raw_dampener("Встречи/2026-08-01_стенограмма.md", PLAIN) == 1.0, "у PLAIN сырья нет"


def _doc(search: gs.GraphSearch, rel: str) -> gs.Doc:
    return next(d for d in search._gen.docs.values() if d.rel == rel)


def test_фрагмент_без_шапки_и_шире_у_дистиллята(tmp_path):
    """Одно правило фрагмента на выдачу и переход: тело без YAML-шапки, у дистиллята окно
    в полтора раза шире, чем у сырья того же текста."""
    g = tmp_path / "г"
    _write(g, "Документация/Коротко.md", "---\ntags: [СЕКРЕТ_ШАПКИ]\n---\nплатёжный шлюз запущен\n")
    long = "вступление " * 300 + "платёжный шлюз запущен в пилот " + "хвост " * 300
    _write(g, "Документация/Итоги.md", long)
    _write(g, "Встречи/2026-08-01_стенограмма.md", long)
    search = gs.GraphSearch(g, embedder=_embedder(), data_dir=tmp_path / "данные", schema=CHAROITE)
    search.refresh(force=True)
    rx = re.compile("шлюз")
    short = search._fragment(_doc(search, "Документация/Коротко.md"), rx, 200, ["шлюз"])
    assert "шлюз" in short and "СЕКРЕТ_ШАПКИ" not in short and "tags" not in short
    dense = search._fragment(_doc(search, "Документация/Итоги.md"), rx, 200, ["шлюз"])
    plain = search._fragment(_doc(search, "Встречи/2026-08-01_стенограмма.md"), rx, 200, ["шлюз"])
    assert "шлюз" in dense and "шлюз" in plain
    assert len(dense) > len(plain) * 1.2, (len(dense), len(plain))


def test_дайджест_узла_без_истории_режет_первую_строку():
    """Узел без статуса и истории отдаёт первую содержательную строку описания — не
    длиннее 120 знаков: дайджест читают на встрече, простыня туда не помещается."""
    text = "# Платформа\n" + "длинное описание платформы " * 20 + "\n"
    lines = graph_nodes._digest(text, "2026", schema=CHAROITE)
    assert len(lines) == 1 and len(lines[0]) == 120 and lines[0].startswith("длинное описание")
