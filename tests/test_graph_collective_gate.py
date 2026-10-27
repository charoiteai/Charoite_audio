"""Затвор сборных имён (№611, этап 2): голое имя при живых тёзках не пишется в
узел встречей — ни человеком, ни сущностью, ни концом связи. В заметке оно
остаётся строкой с кандидатами, в `_Кандидаты.md` — записью для человека.

Сценарии — из входного круга n611-in3: псевдоним на самом сборном узле, один
тёзка, ссылка в «Связях», цель вне папок людей, владелец среди тёзок.
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
import graph_updater as g  # noqa: E402


def _node(graph: pathlib.Path, rel: str, extra: str = "") -> pathlib.Path:
    p = graph / f"{rel}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{extra}---\n# {p.stem}\n", encoding="utf-8")
    return p


def _graph(tmp_path: pathlib.Path, *nodes: str) -> pathlib.Path:
    graph = tmp_path / "Граф"
    for d in ("Встречи", "Люди", "Системы"):
        (graph / d).mkdir(parents=True, exist_ok=True)
    for n in nodes:
        _node(graph, n)
    return graph


def _held(graph, names, owner=""):
    got = g.collective_held(graph, [(n, "Люди") for n in names], owner=owner, born=names)
    assert all(f == "Люди" for _, f in got), got
    return {k: cands for (k, _), (_, cands, _) in got.items()}


def _meeting(tmp_path, monkeypatch, graph, data, owner="", sent=None):
    """Прогон main() на подставном разборе: модель, архив, хук и память — заглушки;
    `sent` собирает имена участников, ушедших в память."""
    voices = [p["имя"] for p in data.get("люди", [])]
    speech = "".join(f"**{n}** [10:{i:02d}]: обсуждаем релиз, сроки и проверку стенда, {n} отвечает по делу.\n"
                     for i, n in enumerate(voices * 3)) + "Конец обсуждения. " * 20
    tpath = tmp_path / "transcripts" / "2026-10-27_1000.md"
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(speech, encoding="utf-8")
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(graph))
    monkeypatch.setattr(sys, "argv", ["graph_updater.py", str(tpath)])
    monkeypatch.setattr(g, "load_cfg", lambda: {"sufler": {"user_name": owner}, "llm": {"model": "нет-такой"}})
    monkeypatch.setattr(g, "extract", lambda *a, **k: json.loads(json.dumps(data)))
    for fn in ("_open_progress", "harden_umask", "archive_and_publish", "run_post_hook",
               "reindex_memory", "meeting_facts_to_brain", "_revise_meeting_cores"):
        monkeypatch.setattr(g, fn, lambda *a, **k: None)
    if sent is not None:
        monkeypatch.setattr(g, "meeting_facts_to_brain",
                            lambda cfg, stamp, title, people, *a: sent.extend(p["имя"] for p in people))
    monkeypatch.setattr(g.privacy, "cloud_enrich_enabled", lambda cfg: False)
    monkeypatch.setattr(g, "LLM", lambda cfg: (_ for _ in ()).throw(RuntimeError("модели нет")))
    g.main()
    notes = sorted((graph / "Встречи").glob("*.md"))
    assert len(notes) == 1, notes
    return notes[0].read_text(encoding="utf-8")


def _person(name):
    return {"имя": name, "роль": "разработчик", "вклад": "сроки релиза"}


# --- сценарии n611-in3 -------------------------------------------------------


def test_alias_on_the_collective_node_itself_is_held(tmp_path):
    """S-C1: «Сашка» — псевдоним самого сборного «Саша»; поиск приводит к нему, и
    судится найденная цель, а не входная форма."""
    graph = _graph(tmp_path, "Люди/Саша Образцов", "Люди/Сидоров Саша")
    _node(graph, "Люди/Саша", 'aliases: ["Сашка"]\n')
    assert _held(graph, ["Сашка"]) == {graph_names.name_key("Сашка"): ["Люди/Саша Образцов", "Люди/Сидоров Саша"]}


def test_one_namesake_is_enough(tmp_path):
    """C2: порог доктора и затвора один — хватает одного многословного тёзки."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Сидоров")
    assert _held(graph, ["Саша"], owner="Пётр Иванов") == {"саша": ["Люди/Саша Сидоров"]}
    assert graph_names.is_collective("Саша", graph_names.namesake_index(["Саша", "Саша Сидоров"]),
                                     owner="Пётр Иванов")


def test_collective_link_end_is_text_not_a_link(tmp_path, monkeypatch):
    """C3: конец связи со сборным именем — текстом; ссылка [[Люди/Саша]] копила бы
    встречу в сборный узел через обратные ссылки."""
    graph = _graph(tmp_path, "Люди/Саша Образцов", "Системы/Релиз")
    sasha = _node(graph, "Люди/Саша")
    before = sasha.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {
        "люди": [_person("Гоша")],
        "связи": [{"от": "Саша", "к": "Релиз", "тип": "ведёт"}]})
    assert "- Саша → [[Системы/Релиз|Релиз]]: ведёт" in note, note
    assert "[[Люди/Саша|" not in note and "[[Люди/Саша]]" not in note
    assert sasha.read_bytes() == before


def test_target_outside_people_folders_is_not_held(tmp_path):
    """DS-C2: имя, которое поиск приводит к узлу вне папок людей, судит прежний
    путь (вердикт сущностей), а не затвор людей."""
    graph = _graph(tmp_path, "Системы/Саша", "Люди/Саша Образцов")
    assert g.find_canonical(graph, "Саша", folder="Люди") == graph / "Системы" / "Саша.md"
    assert _held(graph, ["Саша"]) == {}


def test_owner_is_counted_once(tmp_path):
    """DS-C4: полное имя владельца — не тёзка; тёзка владельца — тёзка; голый
    владелец из одного слова при многословном тёзке — сборный, как у доктора."""
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    assert _held(graph, ["Саша"], owner="Саша Образцов") == {}
    _node(graph, "Люди/Саша Сидоров")
    assert _held(graph, ["Саша"], owner="Саша Образцов") == {"саша": ["Люди/Саша Сидоров"]}
    graph2 = _graph(tmp_path / "п", "Люди/Пётр Лесков")
    assert _held(graph2, ["Пётр"], owner="Пётр") == {graph_names.name_key("Пётр"): ["Люди/Пётр Лесков"]}
    assert _held(graph, ["Саша"], owner="") == {"саша": ["Люди/Саша Образцов", "Люди/Саша Сидоров"]}, \
        "без владельца в конфиге считаются все тёзки"


# --- таблица и main() ----------------------------------------------------------


def test_namesake_born_in_the_same_meeting_holds_the_bare_name(tmp_path):
    """«Саша» и «Саша Сидоров» пришли одной встречей в пустой граф: таблица строится
    до записи, и голое имя не успевает стать узлом раньше тёзки."""
    graph = _graph(tmp_path)
    assert _held(graph, ["Саша", "Саша Сидоров"]) == {"саша": ["Люди/Саша Сидоров"]}
    assert _held(graph, ["Саша"]) == {}, "без тёзок — обычное имя"


def test_merged_or_placeholder_nodes_are_not_namesakes(tmp_path):
    graph = _graph(tmp_path, "Люди/Саша")
    (graph / "Люди" / "Саша Сидоров.md").write_text(
        "---\ntags: [дубль-слит]\n---\n# Саша Сидоров → см. [[Люди/Саша]]\n", encoding="utf-8")
    _node(graph, "Люди/Собеседник 3 (Саша)")
    assert _held(graph, ["Саша"]) == {}


def test_main_keeps_collective_node_and_note_keeps_the_fact(tmp_path, monkeypatch):
    """Сквозной прогон: человек «Саша», сущность «Саша» и связь — ни одна запись в
    Люди/Саша.md, ни нового Системы/Саша.md; строка участника — с ролью, вкладом и
    кандидатами; запись в `_Кандидаты.md` — одна."""
    graph = _graph(tmp_path, "Люди/Саша Образцов", "Люди/Сидоров Саша", "Системы/Релиз")
    sasha = _node(graph, "Люди/Саша")
    before = sasha.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {
        "люди": [_person("Саша"), _person("Гоша")],
        "сущности": [{"имя": "Саша", "тип": "система", "суть": "бот"},
                     {"имя": "Релиз", "тип": "система", "суть": "выпуск"}],
        "связи": [{"от": "Гоша", "к": "Саша", "тип": "спросил"}]})

    assert sasha.read_bytes() == before, "сборный узел не тронут"
    assert not (graph / "Системы" / "Саша.md").exists()
    assert (graph / "Люди" / "Гоша.md").exists(), "прочие имена пишутся как раньше"
    cands = "[[Люди/Саша Образцов|Саша Образцов]], [[Люди/Сидоров Саша|Сидоров Саша]]"
    assert f"- Саша — разработчик: сроки релиза _(узел не выбран: голое имя при живых тёзках: {cands})_" in note, note
    assert "- Саша (система) — бот _(узел не создан: голое имя при живых тёзках: " in note
    assert "- [[Люди/Гоша|Гоша]] → Саша: спросил" in note or "- [[Люди/Гоша]] → Саша: спросил" in note, note
    assert "[[Люди/Саша|" not in note and "[[Люди/Саша]]" not in note
    journal = (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert journal.count("«Саша»") == 1 and "голое имя при живых тёзках: [[Люди/Саша Образцов]]" in journal, journal


def test_alias_input_through_main_does_not_grow_the_node(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    sasha = _node(graph, "Люди/Саша", 'aliases: ["Сашка"]\n')
    before = sasha.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {"люди": [_person("Сашка")]})
    assert sasha.read_bytes() == before
    assert "- Сашка — разработчик: сроки релиза _(узел не выбран:" in note, note


def test_held_reason_is_named():
    assert g._HELD_WHY["collective"] == "голое имя при живых тёзках"
    assert g.held_note([]) == " _(узел не выбран: голое имя при живых тёзках)_"


# --- доктор: рост ссылок в известные сборные --------------------------------------


def test_doctor_counts_new_links_into_known_collective_nodes(tmp_path):
    """Затвор держит встречу; если запись прошла мимо него, доктор видит рост
    ссылок в уже известный сборный узел против ленты прошлой ночи."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Образцов")
    (graph / "Встречи" / "2026-10-01_1000.md").write_text("# В1\n- [[Люди/Саша]]\n", encoding="utf-8")
    first = graph_doctor.inspect(graph, owner="")
    assert first["collective_new_links"] is None and first["feed"]["collective_new_links"] is None

    same = graph_doctor.inspect(graph, owner="", prev_feed=first["feed"])
    assert same["collective_new_links"] == 0
    assert not [w for w in same["warnings"] if "мимо затвора" in w]

    (graph / "Встречи" / "2026-10-02_1000.md").write_text("# В2\n- [[Люди/Саша|Саша]]\n", encoding="utf-8")
    grown = graph_doctor.inspect(graph, owner="", prev_feed=first["feed"])
    assert grown["collective_new_links"] == 1 and grown["feed"]["collective_new_links"] == 1
    assert any("новых ссылок в известные сборные узлы с прошлой ночи: 1" in w for w in grown["warnings"]), \
        grown["warnings"]


def test_doctor_new_links_ignore_new_nodes_and_odd_feed(tmp_path):
    """Новый сборный узел — уже своё предупреждение, ростом ссылок не считается;
    битая лента прошлой ночи — не падение."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Образцов")
    (graph / "Встречи" / "2026-10-01_1000.md").write_text("# В1\n- [[Люди/Саша]]\n", encoding="utf-8")
    assert graph_doctor.inspect(graph, owner="", prev_feed={"collective": []})["collective_new_links"] == 0
    odd = {"collective": [{"node": "Люди/Саша.md", "linked_from": "два"}, "мусор"]}
    assert graph_doctor.inspect(graph, owner="", prev_feed=odd)["collective_new_links"] is None, \
        "база без меры — не с чем сравнить, не ноль (n611-out1 Sonnet M1)"
    assert graph_doctor.inspect(graph, owner="", prev_feed={"collective": "мусор"})["collective_new_links"] is None
    assert graph_doctor.inspect(graph, owner="", prev_feed={})["collective_new_links"] is None


# --- круг n611-out1 --------------------------------------------------------------


def test_unreadable_namesake_keeps_the_gate_shut(tmp_path):
    """Тёзка — по имени файла: нечитаемый узел (конфликтная копия iCloud) не доказан
    заглушкой и считается живым, как у доктора (n611-out1 DS C1)."""
    graph = _graph(tmp_path, "Люди/Саша")
    (graph / "Люди" / "Саша Сидоров.md").write_bytes(b"---\n---\n# \xff\xfe\n")
    assert _held(graph, ["Саша"]) == {"саша": ["Люди/Саша Сидоров"]}
    assert [c["node"] for c in graph_doctor.inspect(graph, owner="")["feed"]["collective"]] == ["Люди/Саша.md"]


def test_key_carries_the_place(tmp_path):
    """Один ключ — разные места: человек «Сашка» ведёт в сборный по псевдониму и
    держится, сущность «Сашка» с псевдонимом на своём узле Систем — нет
    (n611-out1 Sonnet I1 / DS I2)."""
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    _node(graph, "Люди/Саша", 'aliases: ["Сашка"]\n')
    _node(graph, "Системы/Бот", 'aliases: ["Сашка"]\n')
    got = g.collective_held(graph, [("Сашка", "Люди"), ("Сашка", "Системы")], owner="")
    assert set(got) == {("сашка", "Люди")}, got
    assert got[("сашка", "Люди")] == ("Сашка", ["Люди/Саша Образцов"], "Люди/Саша")


def test_entity_resolved_by_alias_outside_people_is_not_held(tmp_path):
    """Писатель кладёт сущность по псевдониму в узел другой папки — затвор её не
    держит (n611-out1 DS I1)."""
    graph = _graph(tmp_path, "Люди/Саня Петров")
    _node(graph, "Модели/М", 'aliases: ["Саня"]\n')
    assert g.entity_node_verdict(graph, "Системы", "Саня") == ("resolved", ["Модели/М"])
    assert g.collective_held(graph, [("Саня", "Системы")], owner="") == {}


def test_new_entity_with_a_person_word_is_held(tmp_path):
    """Новая сущность с голым именем при живом полном тёзке держится: родившись в
    Системах, она перехватит следующую встречу этого человека — поиск находит
    точное имя в любой папке (n611-in2; out1 Haiku I1 — принятая цена)."""
    graph = _graph(tmp_path, "Люди/Ромашка Иванова")
    got = g.collective_held(graph, [("Ромашка", "Системы")], owner="")
    assert got == {("ромашка", "Системы"): ("Ромашка", ["Люди/Ромашка Иванова"], "")}


def test_fuller_name_glued_to_a_bare_node_is_held_not_written(tmp_path):
    """Поиск подстрокой ведёт «Владимир Попов» в голый «Владимир»; без затвора
    встреча легла бы в сборный узел чужого человека. Затвор держит — факт в
    заметке с кандидатами (n611-out1 DS C2 / Haiku C1; склейка поиска — №607)."""
    graph = _graph(tmp_path, "Люди/Владимир", "Люди/Владимир Иванов")
    assert g.find_canonical(graph, "Владимир Попов", folder="Люди") == graph / "Люди" / "Владимир.md"
    assert _held(graph, ["Владимир Попов"]) == {"владимир попов": ["Люди/Владимир Иванов"]}


def test_link_only_name_goes_to_the_journal(tmp_path, monkeypatch):
    """Имя только концом связи — в заметке текстом и строкой в `_Кандидаты.md`
    (n611-out1 DS M1 / Sonnet I2)."""
    graph = _graph(tmp_path, "Люди/Саша Образцов", "Системы/Релиз")
    sasha = _node(graph, "Люди/Саша")
    before = sasha.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {
        "люди": [_person("Гоша")], "связи": [{"от": "Саша", "к": "Релиз", "тип": "ведёт"}]})
    assert "- Саша → [[Системы/Релиз|Релиз]]: ведёт" in note
    journal = (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert journal.count("«Саша» (связь) — голое имя при живых тёзках: [[Люди/Саша Образцов]]") == 1, \
        "имя только концом связи — тип «связь» (n611-out2 Haiku M3)"
    assert sasha.read_bytes() == before


def test_owner_from_config_reaches_the_gate(tmp_path, monkeypatch):
    """Владелец из `sufler.user_name` доходит до затвора в main(): его полное имя —
    не тёзка, «Саша» пишется в свой узел как раньше (DS-C4; out1 Sonnet M4)."""
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    sasha = _node(graph, "Люди/Саша")
    before = sasha.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {"люди": [_person("Саша")]}, owner="Саша Образцов")
    assert sasha.read_bytes() != before, "не сборный — встреча легла в узел"
    assert "узел не выбран" not in note and not (graph / g.CANDIDATES_NOTE).exists()


def test_one_journal_line_for_a_person_and_an_untyped_entity(tmp_path, monkeypatch):
    """Человек и сущность без типа под одним сборным именем — одна строка журнала,
    и повтор той же встречи её не дублирует (out1 Sonnet M3, M4)."""
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    _node(graph, "Люди/Саша")
    data = {"люди": [_person("Саша"), _person("Гоша")], "сущности": [{"имя": "Саша", "суть": "бот"}]}
    _meeting(tmp_path, monkeypatch, graph, data)
    _meeting(tmp_path, monkeypatch, graph, data)
    journal = (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert journal.count("«Саша»") == 1, journal

    graph2 = _graph(tmp_path / "2", "Люди/Саша Образцов")
    _node(graph2, "Люди/Саша")
    data2 = {"люди": [_person("Гоша")], "сущности": [{"имя": "Саша", "суть": "бот"}]}
    _meeting(tmp_path / "2", monkeypatch, graph2, data2)
    _meeting(tmp_path / "2", monkeypatch, graph2, data2)
    journal2 = (graph2 / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert journal2.count("«Саша» (entity)") == 1, journal2


def test_gate_reads_nothing_without_names(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Образцов")
    monkeypatch.setattr(g, "_read_node", lambda *a, **k: (_ for _ in ()).throw(AssertionError("читал узлы")))
    assert g.collective_held(graph, [], owner="") == {}


# --- круг n611-out2 --------------------------------------------------------------


def test_unreadable_stub_redirect_is_not_a_namesake(tmp_path):
    """Заглушка-редирект с битым байтом (конфликт iCloud) — заглушка и для затвора,
    и для доктора: читается с заменой, как `graph_links.read_notes` (n611-out2 Haiku C1)."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Иван Петров")
    (graph / "Люди" / "Саша Старый.md").write_bytes("# Саша Старый → [[Люди/Иван Петров]]\n".encode() + b"\xff\n")
    assert _held(graph, ["Саша"]) == {}
    assert graph_doctor.inspect(graph, owner="")["feed"]["collective"] == []


def test_unopenable_namesake_still_holds(tmp_path, monkeypatch):
    """Узел, который не открыть вовсе (OSError), остаётся живым тёзкой: заглушкой он
    не доказан (n611-out1 DS C1 после правки out2)."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Сидоров")
    real = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self.stem == "Саша Сидоров":
            raise PermissionError("нет прав")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    assert _held(graph, ["Саша"]) == {"саша": ["Люди/Саша Сидоров"]}


def test_doctor_feed_record_without_node_is_skipped(tmp_path):
    """Запись прошлой ленты без `node` — не падение доктора (n611-out2 DS M2 / Haiku I2)."""
    graph = _graph(tmp_path, "Люди/Саша", "Люди/Саша Образцов")
    feed = {"collective": [{"linked_from": 1}, {"node": 7, "linked_from": 1}]}
    assert graph_doctor.inspect(graph, owner="", prev_feed=feed)["collective_new_links"] is None


def test_glued_fuller_name_names_the_node_it_was_led_to(tmp_path, monkeypatch):
    """Полное имя, которое поиск приводит в голый узел, — в заметке видно, куда его
    вёл поиск: «голое имя» у полного имени иначе не читается (n611-out2 DS M3)."""
    graph = _graph(tmp_path, "Люди/Владимир Иванов")
    bare = _node(graph, "Люди/Владимир")
    before = bare.read_bytes()
    note = _meeting(tmp_path, monkeypatch, graph, {"люди": [_person("Владимир Попов"), _person("Гоша")]})
    assert "- Владимир Попов — разработчик: сроки релиза _(узел не выбран: поиск ведёт в Владимир — " \
           "голое имя при живых тёзках: [[Люди/Владимир Иванов|Владимир Иванов]])_" in note, note
    assert bare.read_bytes() == before
    assert g.held_note(["Люди/Саша Образцов"]) == \
        " _(узел не выбран: голое имя при живых тёзках: [[Люди/Саша Образцов|Саша Образцов]])_"


def test_held_participant_still_reaches_memory(tmp_path, monkeypatch):
    """Затвор держит узел, а не факт: участник со сборным именем уходит в память
    встречи вместе с остальными (n611-out2, шаг Haiku по понижению)."""
    graph = _graph(tmp_path, "Люди/Саша Образцов")
    _node(graph, "Люди/Саша")
    sent = []
    _meeting(tmp_path, monkeypatch, graph, {"люди": [_person("Саша"), _person("Гоша")]}, sent=sent)
    assert sent == ["Саша", "Гоша"]
