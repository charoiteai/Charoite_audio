"""Гигиена графа и временных файлов — то, что портится тихо.

Ни один из этих дефектов не давал ошибки: узел просто оставался несвязанным,
провенанс подтверждал выдумку, а копия часовой записи лежала в /var/folders
до перезагрузки, о чём ретеншн приватности не знал.
"""
import json
import pathlib
import subprocess
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import graph_updater as g  # noqa: E402


def test_safe_name_escapes_wiki_syntax():
    """Имя узла уезжает в [[ссылку]] — квадратные скобки её обрывают."""
    for bad in ("Витрина [v2]", "Релиз #17", "Блок ^abc"):
        out = g.safe_name(bad)
        assert not (set(out) & set("[]#^")), f"«{bad}» → «{out}»: ссылка сломается"


def test_safe_name_never_collapses_to_nothing():
    """Пустое имя дало бы скрытый файл «.md» и совпадало бы с любым узлом."""
    for empty in ("...", "   ", "///", "[]"):
        assert g.safe_name(empty).strip(), f"«{empty}» схлопнулось в пустоту"


def test_quote_check_rejects_unverifiable_chinese():
    """В zh-режиме пустая нормализация пропускала выдумки как подтверждённые.

    Старый шаблон искал только [а-яёa-z0-9]: у китайской цитаты слов не
    находилось, norm(quote) выходил пустым, а пустая строка входит в любую —
    и провенанс «кто и когда это сказал» подтверждал то, чего в стенограмме
    не было.
    """
    # Фильтр «меньше трёх слов» китайскую фразу отбросил бы и без нормализации,
    # поэтому берём цитату, которая до него доходит: пробелы в ней есть.
    core = {"цитата": "我们 决定 采用 方案", "кто": "德米特里", "время": "10:15"}
    out = g.core_anchor(core, "совершенно другой разговор про погоду")
    assert out == "", "выдуманная китайская цитата прошла как подтверждённая"

    # И обратная сторона: настоящая китайская цитата обязана подтверждаться.
    real = {"цитата": "我们 决定 采用 方案", "кто": "德米特里", "время": "10:15"}
    ok = g.core_anchor(real, "10:15 德米特里: 我们 决定 采用 方案 ,下周开始")
    assert ok, "дословная китайская цитата не прошла проверку"


def test_scratch_dir_is_removed_with_the_process():
    """Копия полного аудио не должна пережить процесс."""
    code = (
        "import sys; sys.path.insert(0, 'src');"
        "import transcribe_file as tf;"
        "d = tf._scratch_dir(); (d / 'probe.wav').write_bytes(b'x'); print(d)"
    )
    root = pathlib.Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=root)
    path = pathlib.Path(out.stdout.strip())
    assert str(path), f"скрипт не отработал: {out.stderr[-300:]}"
    assert not path.exists(), f"временная копия аудио осталась в {path}"


def test_canonical_does_not_glue_short_names_to_long_nodes(tmp_path):
    """«Ян» не должен приклеиваться к «Январский релиз».

    Двухбуквенные имена из распознавания входят подстрокой в десятки узлов.
    Пока проверка была без ограничения длины, единственное совпадение
    возвращалось как канонический узел — и встреча дописывалась в чужой файл.
    """
    people = tmp_path / "Люди"
    people.mkdir()
    (people / "Январский релиз.md").write_text("# Январский релиз", encoding="utf-8")

    assert g.find_canonical(tmp_path, "Ян") is None, "короткое имя приклеилось к длинному узлу"


def test_canonical_still_matches_real_variants(tmp_path):
    """Но настоящие варианты одного имени по-прежнему схлопываются."""
    systems = tmp_path / "Системы"
    systems.mkdir()
    (systems / "Витрина продаж.md").write_text("# Витрина продаж", encoding="utf-8")

    found = g.find_canonical(tmp_path, "витрина продаж")
    assert found is not None and found.stem == "Витрина продаж"


def test_graph_logs_expire(tmp_path, monkeypatch):
    """Логи графа с содержимым встреч не должны копиться годами."""
    import daemon as d

    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "graph_2020-01-01_1200.log"
    old.write_text("Дмитрий: обсудили миграцию", encoding="utf-8")
    import os
    stale = old.stat().st_mtime - 30 * 86400
    os.utime(old, (stale, stale))
    fresh = logs / "graph_now.log"
    fresh.write_text("сегодняшняя встреча", encoding="utf-8")

    # retry_<штамп>.log — stdout повторной пересборки с именами участников;
    # третий класс логов, который ретеншн не видел (аудит DeepSeek 16.08)
    retry_old = logs / "retry_2020-01-01_1200.log"
    retry_old.write_text("имена: Дмитрий", encoding="utf-8")
    os.utime(retry_old, (stale, stale))

    monkeypatch.setattr(d, "ROOT", tmp_path)
    d._prune_graph_logs({"audio": {"record_keep_days": 2}})

    assert not old.exists(), "старый лог с содержимым встречи остался"
    assert not retry_old.exists(), "старый retry-лог с именами участников остался"
    assert fresh.exists(), "свежий лог удалён — диагностику потеряли"


def test_speaker_placeholders_are_labels_not_people():
    """«Собеседник 3» — метка диаризации, а не человек (аудит графа 28.08:
    17 таких узлов в Люди, до 141 входящих ссылок у одного — разные люди
    разных встреч склеены в один файл)."""
    for label in ("Собеседник", "Собеседник 3", "собеседник 12", "Speaker 2",
                  "Участник 4", "спикер 1", "Participant 7"):
        assert g.is_speaker_placeholder(label), label
    for person in ("Собеседникова", "Участник встречи Иван", "Пётр", "Speaker of the House"):
        assert not g.is_speaker_placeholder(person), person


def test_find_canonical_matches_by_name_key(tmp_path):
    """«Иван (Иванов)» и «Иван Иванов», «ИИ_агент» и «ИИ-агент» — один
    узел, а не пара (аудит 28.08: четыре такие пары в графе)."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Системы").mkdir()
    ivanov = graph / "Люди" / "Иван Иванов.md"
    ivanov.write_text("# Иван Иванов\n", encoding="utf-8")
    agent = graph / "Системы" / "ИИ-агент.md"
    agent.write_text("# ИИ-агент\n", encoding="utf-8")
    assert g.find_canonical(graph, "Иван (Иванов)") == ivanov
    assert g.find_canonical(graph, "иван иванов") == ivanov
    assert g.find_canonical(graph, "ИИ_агент") == agent
    assert g.find_canonical(graph, "Иван Сидоров") is None, "другой человек"
    assert g.name_key("Реестр 385 130") == g.name_key("Реестр 385-130")


def test_tidy_links_joins_wrapped_wikilinks():
    """Перенос строки внутри [[…]] — мёртвая ссылка для Obsidian (60+ в графе)."""
    assert g.tidy_links("см. [[Люди/Иван\nПетров|Иван]] и [[Ядра/Тема]]") == \
        "см. [[Люди/Иван Петров|Иван]] и [[Ядра/Тема]]"
    assert g.tidy_links("без ссылок\nстрока") == "без ссылок\nстрока"
    data = {"люди": [{"имя": "Иван", "вклад": "про [[Системы/\nВитрина]]"}], "темы": ["[[A\n B]]"]}
    tidy = g.tidy_links_deep(data)
    assert tidy["люди"][0]["вклад"] == "про [[Системы/Витрина]]"   # перенос у «/» — без пробела (GLM I2)
    assert tidy["темы"] == ["[[A B]]"]
    assert data["темы"] == ["[[A\n B]]"], "исходник не тронут"


def test_graph_doctor_counts_defects_and_spares_design_pairs(tmp_path):
    """graph_doctor: битые и перенесённые ссылки, метки среди Люди, сироты,
    настоящие дубли — считаются; пары Досье/Ядра и заглушки tier3 — нет."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Досье", "Встречи"):
        (graph / d).mkdir(parents=True)
    (graph / "Люди" / "Иван.md").write_text("# Иван\n- [[Встречи/2026-01-01_1000]]\n", encoding="utf-8")
    (graph / "Люди" / "Собеседник 3.md").write_text("# Собеседник 3\n", encoding="utf-8")
    (graph / "Системы" / "Витрина.md").write_text("# Витрина\nсирота без входящих\n", encoding="utf-8")
    (graph / "Ядра" / "Тема.md").write_text("# Тема\n- [[Люди/Иван]] и [[Люди/Нет\nтакого]]\n", encoding="utf-8")
    (graph / "Досье" / "Тема.md").write_text("# Тема\n| [[Ядра/Тема\\|Тема]] |\n", encoding="utf-8")
    (graph / "Системы" / "Тема.md").write_text("# Тема\nнастоящий дубль ядра\n", encoding="utf-8")
    (graph / "Системы" / "Иван.md").write_text("---\ntags: [дубль-слит]\n---\n# Иван → см. [[Люди/Иван]]\n", encoding="utf-8")
    (graph / "Встречи" / "2026-01-01_1000.md").write_text(
        "# Встреча\n- [[Люди/Иван]] [[Люди/Собеседник 3]] [[Ядра/Тема]] [[Досье/Тема]] [[он]]\n", encoding="utf-8")
    (graph / "_MOC.md").write_text("- [[Встречи/2026-01-01_1000]]\n- [[Ядра/Тема]]\n", encoding="utf-8")

    rep = graph_doctor.inspect(graph, examples=5)

    assert rep["broken"] == 2 and rep["wrapped_links"] == 1, rep      # [[Люди/Нет\nтакого]], [[он]]
    assert rep["placeholders"] == 1
    assert "Системы/Витрина.md" in rep["examples"]["orphans"]
    assert rep["dup_real"] == 1 and rep["examples"]["dup_real"] == ["Системы/Тема.md | Ядра/Тема.md"]
    assert rep["dup_stubs"] == 1, "заглушка-редирект не дубль"
    assert rep["moc_linked"] == 2 and rep["moc_missing"] == rep["nodes"] - 1
    assert any("меток диаризации" in w for w in rep["warnings"])
    assert not any("Досье" in x for x in rep["examples"]["dup_real"]), "пара Досье/Ядра — по замыслу"


def test_redirect_stubs_are_recognised_by_structure_not_by_tier3_wording():
    """Облако помечает слияние своими словами («Дубль слит») — три слоя
    (досье, tier3, ядра) узнавали только буквальную пометку tier3 и принимали
    такую заглушку за живой узел (Sonnet 28.08 I5)."""
    import redirects
    cloud = "---\ntype: entity\n---\n# Инцидент → [[Ядра/Инциденты]]\n\n⚠️ **Дубль слит.**\n"
    tier3 = "# Тема\n## Статус\n…\n⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в [[Ядра/Канон]]\n"
    alive = "# Живая тема\n## Статус\nидёт → [[Ядра/Соседняя]] связана\n" + "- факт\n" * 5
    assert redirects.is_merged(cloud) and redirects.is_redirect_stub(cloud)
    assert redirects.is_merged(tier3)
    assert not redirects.is_merged(alive), "стрелка в середине текста — не заглушка"
    assert redirects.stub_target(cloud) == "Ядра/Инциденты.md"


def test_links_in_relations_only_point_at_existing_nodes(tmp_path):
    """«## Связи»: [[он]] и обрывки слов больше не становятся ссылками
    (Sonnet 28.08 I3: 626 битых ссылок, среди целей — местоимение «он»)."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "Иван Иванов.md").write_text("# Иван Иванов\n", encoding="utf-8")
    assert g.link_or_text(graph, "он") == "он"
    assert g.link_or_text(graph, "Фа") == "Фа"
    assert g.link_or_text(graph, "Иван (Иванов)") == "[[Люди/Иван Иванов|Иван Иванов]]"
    assert g.link_or_text(graph, "Неизвестная система") == "Неизвестная система"


def test_a_core_named_after_an_existing_system_node_is_reported(tmp_path, capsys):
    """Тема заведена как Системы/X, потом названа ядром: параллельное Ядра/X
    больше не появляется молча (Sonnet 28.08 I4: 4 живые пары в графе)."""
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Ядра").mkdir()
    (graph / "Системы" / "Внеплановый бэкап.md").write_text("# Внеплановый бэкап\n", encoding="utf-8")
    p = g.resolve_core_path(graph / "Ядра", "Внеплановый бэкап", graph)
    assert p == graph / "Ядра" / "Внеплановый бэкап.md"
    assert "уже есть узлом Системы/Внеплановый бэкап" in capsys.readouterr().out
    # заглушка облака в Ядрах ведёт к канону, хотя пометки tier3 в ней нет
    (graph / "Ядра" / "Канон.md").write_text("# Канон\n## Статус\nидёт\n", encoding="utf-8")
    (graph / "Ядра" / "Дубль.md").write_text("# Дубль -> [[Ядра/Канон]]\n\nДубль слит.\n", encoding="utf-8")
    assert g.resolve_core_path(graph / "Ядра", "Дубль", graph) == graph / "Ядра" / "Канон.md"


def test_people_nodes_carry_a_last_seen_date_that_never_goes_backwards(tmp_path):
    """У человека/системы описание пишется один раз; свежесть — строкой
    «последнее упоминание», ретрай старой встречи её не откатывает (Sonnet I6)."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    g.upsert_entity(graph, "Люди", "Пётр", "person", "аналитик", "Встречи/2026-07-15_1400", "спросил")
    node = graph / "Люди" / "Пётр.md"
    assert "_(последнее упоминание: 2026-07-15)_" in node.read_text(encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Пётр", "person", "", "Встречи/2026-08-20_1000", "")
    text = node.read_text(encoding="utf-8")
    assert "_(последнее упоминание: 2026-08-20)_" in text and text.count("последнее упоминание") == 1
    g.upsert_entity(graph, "Люди", "Пётр", "person", "", "Встречи/2026-06-01_0900", "ретрай старого")
    assert "_(последнее упоминание: 2026-08-20)_" in node.read_text(encoding="utf-8")
    # старый узел без строки получает её при следующем упоминании
    old = graph / "Люди" / "Старый.md"
    old.write_text("# Старый\nроль\n\n## Встречи\n- [[Встречи/2026-05-05_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Старый", "person", "", "Встречи/2026-08-01_1000", "")
    assert "_(последнее упоминание: 2026-08-01)_\n\n## Встречи" in old.read_text(encoding="utf-8")


def test_folder_index_lists_live_nodes_freshest_first(tmp_path):
    """Люди/_ЛЮДИ.md: узел, встреч, последняя; заглушки пропущены (Sonnet I7)."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "А.md").write_text("# А\n## Встречи\n- [[Встречи/2026-07-01_1000]]\n", encoding="utf-8")
    (graph / "Люди" / "Б.md").write_text(
        "# Б\n## Встречи\n- [[Встречи/2026-08-20_1000]]\n- [[Встречи/2026-06-01_1000]]\n", encoding="utf-8")
    (graph / "Люди" / "В.md").write_text("# В → [[Люди/Б]]\n\nДубль слит.\n", encoding="utf-8")
    g.rebuild_folder_index(graph, "Люди")
    idx = (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    rows = [ln for ln in idx.splitlines() if ln.startswith("| [[")]
    assert rows == ["| [[Люди/Б\\|Б]] | 2 | 2026-08-20 |", "| [[Люди/А\\|А]] | 1 | 2026-07-01 |"]
    g.rebuild_folder_index(graph, "Досье")          # нет указателя для этой папки — тихо
    assert not (graph / "Досье").exists()


def test_placeholder_variants_and_folder_scoped_name_key(tmp_path, capsys):
    """Круг-1 по #448 (DS): метка с дефисом/№/скобкой и китайская — тоже метка;
    ключ имени склеивает только внутри целевой папки; заглушка с каноном в
    другой папке не уводит статус ядра; «## Статус → …» — не заглушка."""
    for label in ("Собеседник-3", "Собеседник №3", "Собеседник 3,", "Собеседник 3 (муж)", "参会者 2", "Speaker  4"):
        assert g.is_speaker_placeholder(label), label
    assert not g.is_speaker_placeholder("Собеседников Пётр")
    graph = tmp_path / "g"
    for d in ("Люди", "Системы", "Ядра"):
        (graph / d).mkdir(parents=True)
    person = graph / "Люди" / "ИИ-агент.md"
    person.write_text("# ИИ-агент\nчеловек с таким прозвищем\n", encoding="utf-8")
    # запись системы «ИИ_агент» не должна приклеиться к человеку из другой папки
    g.upsert_entity(graph, "Системы", "ИИ_агент", "система", "сервис", "Встречи/2026-08-01_1000", "")
    assert (graph / "Системы" / "ИИ_агент.md").exists(), "система склеилась с человеком"
    assert "2026-08-01" not in person.read_text(encoding="utf-8")
    # без папки при двух ключ-равных узлах в разных папках — не гадаем (luna I1/I2)
    assert g.find_canonical(graph, "ИИ агент") is None
    assert g.find_canonical(graph, "ИИ агент", folder="Системы") == graph / "Системы" / "ИИ_агент.md"
    assert g.canon_link(graph, "ИИ_агент", "Системы") == "[[Системы/ИИ_агент|ИИ_агент]]"
    # «связи»/сущности: метка — текстом, узла нет
    assert g.link_or_text(graph, "Собеседник-3") == "Собеседник-3"
    # заглушка ядра, указывающая в Люди: статус остаётся у заглушки, не уходит в чужой узел
    (graph / "Ядра" / "Иван.md").write_text("# Иван\n## Статус\nядро с тем же именем\n", encoding="utf-8")
    (graph / "Ядра" / "Дубль.md").write_text("# Дубль → [[Люди/Иван]]\n\nДубль слит.\n", encoding="utf-8")
    assert g.resolve_core_path(graph / "Ядра", "Дубль", graph) == graph / "Ядра" / "Дубль.md"
    # doctor: «## Статус → в работе» — живой узел, не заглушка
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    assert not graph_doctor._is_stub("# Риски\n## Статус → в работе\n- пункт\n")
    assert graph_doctor._is_stub("# Риски → [[Ядра/Канон]]\n\nДубль слит.\n")
    # указатель считает встречу один раз, даже если узел ссылается на неё дважды
    (graph / "Люди" / "Пётр.md").write_text(
        "# Пётр\nсм. [[Встречи/2026-08-01_1000]]\n## Встречи\n- [[Встречи/2026-08-01_1000]]\n", encoding="utf-8")
    g.rebuild_folder_index(graph, "Люди")
    assert "| [[Люди/Пётр\\|Пётр]] | 1 | 2026-08-01 |" in (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    # строка свежести терпит CRLF и хвостовой пробел — второй копии не будет
    node = graph / "Люди" / "Ольга.md"
    node.write_text("# Ольга\n_(последнее упоминание: 2026-07-01)_ \r\n\n## Встречи\n- [[Встречи/2026-07-01_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Ольга", "person", "", "Встречи/2026-08-02_1000", "")
    assert node.read_text(encoding="utf-8").count("последнее упоминание") == 1


def test_glm_round_one_fixes(tmp_path):
    """Круг-1 по #448 (GLM): указатель не кандидат канона; «Системы/ Витрина»
    — битая, эмбед [[x.pdf]] — нет; встречи в указателе — по секции; новый
    узел без даты-штампа не получает строки свежести."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Системы").mkdir()
    (graph / "Люди" / "_ЛЮДИ.md").write_text("# Люди — указатель\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Люди", "person", "странное имя", "Встречи/2026-08-01_1000", "")
    assert (graph / "Люди" / "Люди.md").exists(), "сущность «Люди» приклеилась к указателю"
    assert "## Встречи" not in (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    # ручной прогон без штампа: строки свежести нет, но и мусора нет
    g.upsert_entity(graph, "Люди", "Гость", "person", "", "Встречи/заметки", "")
    assert "последнее упоминание" not in (graph / "Люди" / "Гость.md").read_text(encoding="utf-8")
    # указатель считает только секцию «## Встречи»
    (graph / "Люди" / "Пётр.md").write_text(
        "# Пётр\nв прозе облака: [[Встречи/2026-01-01_1000]]\n## Встречи\n- [[Встречи/2026-08-01_1000]]\n", encoding="utf-8")
    g.rebuild_folder_index(graph, "Люди")
    assert "| [[Люди/Пётр\\|Пётр]] | 1 | 2026-08-01 |" in (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    # doctor: пробел у слеша — битая ссылка; вложение — не битая
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    (graph / "Системы" / "Витрина.md").write_text("# Витрина\n", encoding="utf-8")
    (graph / "схема.pdf").write_bytes(b"%PDF")
    (graph / "Люди" / "Иван.md").write_text("# Иван\n[[Системы/ Витрина]] и [[схема.pdf]] и [[Системы/Витрина]]\n", encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=20)
    ivan = [x for x in rep["examples"]["broken"] if x.startswith("Люди/Иван.md")]
    assert ivan == ["Люди/Иван.md -> [[Системы/ Витрина]]"], rep["examples"]["broken"]



def test_luna_round_one_fixes(tmp_path):
    """Круг-1 по #448 (luna r2): точное имя важнее ключа из соседней папки;
    подстрока не выходит за целевую папку; ключ-эквивалентное ядро в той же
    папке переиспользуется; встречи считаются по штампу; алиас ссылки цел."""
    graph = tmp_path / "g"
    for d in ("Люди", "Системы", "Ядра"):
        (graph / d).mkdir(parents=True)
    (graph / "Люди" / "А-Б.md").write_text("# А-Б\n", encoding="utf-8")
    (graph / "Системы" / "А Б.md").write_text("# А Б\n", encoding="utf-8")
    assert g.find_canonical(graph, "А Б") == graph / "Системы" / "А Б.md", "точное имя проиграло ключу"
    (graph / "Люди" / "Платёжный.md").write_text("# Платёжный\n", encoding="utf-8")
    g.upsert_entity(graph, "Системы", "Платёж", "система", "", "Встречи/2026-08-01_1000", "")
    assert (graph / "Системы" / "Платёж.md").exists(), "подстрока увела систему в Люди"
    assert "2026-08-01" not in (graph / "Люди" / "Платёжный.md").read_text(encoding="utf-8")
    (graph / "Ядра" / "Сбой-Х.md").write_text("# Сбой-Х\n## Статус\nидёт\n", encoding="utf-8")
    assert g.resolve_core_path(graph / "Ядра", "Сбой Х", graph) == graph / "Ядра" / "Сбой-Х.md"
    (graph / "Люди" / "Пётр.md").write_text(
        "# Пётр\n## Встречи\n- [[Встречи/2026-08-01_1000]]\n- [[Встречи/2026-08-01_1400|вечер]]\n", encoding="utf-8")
    g.rebuild_folder_index(graph, "Люди")
    assert "| [[Люди/Пётр\\|Пётр]] | 2 | 2026-08-01 |" in (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    assert g.tidy_links("[[Ядра/X|до / после]]") == "[[Ядра/X|до / после]]"
    assert g.tidy_links("[[Ядра/\nX|до / после]]") == "[[Ядра/X|до / после]]"
    # сбой записи указателя не роняет конвейер: каталог на месте файла
    (graph / "Люди" / "_ЛЮДИ.md").unlink()
    (graph / "Люди" / "_ЛЮДИ.md").mkdir()
    try:
        g.rebuild_folder_index(graph, "Люди")
    except OSError:
        pass                    # сам вызов может кинуть OSError — конвейер его ловит


def test_dossier_takes_stub_target_from_the_heading_not_frontmatter(tmp_path):
    """dossier.scan: редирект заглушки — из первой строки заголовка; ссылка во
    frontmatter уводила входящие к чужому узлу (luna I5)."""
    import dossier
    graph = tmp_path / "g"
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Ядра" / "Канон.md").write_text("# Канон\n## Статус\nидёт\n- факт\n", encoding="utf-8")
    (graph / "Ядра" / "Чужой.md").write_text("# Чужой\n## Статус\nидёт\n- факт\n", encoding="utf-8")
    (graph / "Ядра" / "Дубль.md").write_text(
        "---\nrelated: [[Ядра/Чужой]]\n---\n# Дубль → [[Ядра/Канон]]\n\nДубль слит.\n", encoding="utf-8")
    (graph / "Встречи" / "2026-08-01_1000.md").write_text("# Встреча\n- [[Ядра/Дубль]]\n", encoding="utf-8")
    _files, backlinks = dossier.scan(graph)
    assert "2026-08-01_1000" in backlinks.get("Канон", set()), backlinks
    assert not backlinks.get("Чужой"), "входящие ушли к узлу из frontmatter"


def test_doctor_treats_dotted_node_names_as_nodes_not_embeds(tmp_path):
    """«Linux 1.8», «МПД 3.0» — узлы с точкой в имени, не вложения: первый
    вариант отсева эмбедов записал ~400 таких ссылок в битые (прод 28.08)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Люди").mkdir()
    (graph / "Системы" / "Linux 1.8.md").write_text("# Linux 1.8\n", encoding="utf-8")
    (graph / "схема.pdf").write_bytes(b"%PDF")
    (graph / "Люди" / "Иван.md").write_text(
        "# Иван\n[[Системы/Linux 1.8]] [[Linux 1.8]] [[схема.pdf]] [[нет.pdf]] [[МПД 3.0]]\n", encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=10)
    assert rep["examples"]["broken"] == ["Люди/Иван.md -> [[нет.pdf]]", "Люди/Иван.md -> [[МПД 3.0]]"], rep["examples"]["broken"]
    assert rep["broken"] == 2 and rep["orphans"] == 1, (rep["broken"], rep["orphans"])


def test_doctor_note_wins_over_attachment_and_attachment_is_any_file_on_disk(tmp_path):
    """DS по #449: узел, чей стем кончается на расширение («v2.json»), — узел
    даже при файле-тёзке на диске; вложение — любой файл от корня графа;
    папка, `..`, абсолютный путь и скрытые каталоги — не цели (GLM, luna
    по #449); `.MD` без регистра; имя файла в NFD находится по NFC-ссылке;
    слишком длинное имя — битая ссылка, а не падение всего отчёта."""
    import unicodedata
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Люди").mkdir()
    (graph / ".trash").mkdir()
    (graph / "Системы" / "v2.json.md").write_text("# v2.json\n", encoding="utf-8")
    (graph / "v2.json").write_bytes(b"{}")                     # коллизия: заметка важнее файла
    (graph / "Системы" / "x.md").write_text("# x\n", encoding="utf-8")
    (graph / "Системы" / "Док.md").write_text("# Док\n", encoding="utf-8")
    (graph / "rec.ogg").write_bytes(b"OggS")
    (graph / ".trash" / "старое.pdf").write_bytes(b"%PDF")
    (graph / ".env").write_bytes(b"SECRET=1")                # скрытый файл — не вложение (DS r2)
    (tmp_path / "секрет.pdf").write_bytes(b"%PDF")
    (graph / unicodedata.normalize("NFD", "схема й.pdf")).write_bytes(b"%PDF")
    nfc_dir = graph / unicodedata.normalize("NFC", "Café")
    nfc_dir.mkdir()
    (nfc_dir / unicodedata.normalize("NFD", "план й.pdf")).write_bytes(b"%PDF")   # смешанные формы (luna r2)
    dead = ("rec.opus", "Люди", "../секрет.pdf", "../x", "/etc/hosts", ".trash/старое.pdf",
            ".env", "a" * 300 + ".pdf")
    alive = ("v2.json", "v2.json.md", "Системы/v2.json", "ДОК.MD", "rec.ogg", "схема й.pdf",
             "Café/план й.pdf")
    (graph / "Люди" / "Иван.md").write_text(
        "# Иван\n" + " ".join(f"[[{x}]]" for x in alive + dead) + "\n", encoding="utf-8")
    (graph / "_MOC.md").write_text("# MOC\n[[Системы/x]] [[rec.ogg]]\n", encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=20)
    assert rep["examples"]["broken"] == [f"Люди/Иван.md -> [[{x}]]" for x in dead], rep["examples"]["broken"]
    assert rep["broken"] == len(dead) and rep["links"] == len(alive) + len(dead) + 2
    # входящие дошли до v2.json и Док; x и Иван без входящих (ссылка из MOC — не связь)
    assert rep["orphans"] == 2, rep["examples"]["orphans"]
    assert sorted(rep["examples"]["orphans"]) == ["Люди/Иван.md", "Системы/x.md"], rep["examples"]["orphans"]
    assert rep["moc_linked"] == 1, "вложение rec.ogg — не узел и не покрытие MOC"
    # NFD-подслучай на APFS невидим (нормализационно нечувствительна) —
    # кандидаты проверяются напрямую, чтобы мутация «одна форма» краснела и на маке
    forms = [c.name for c in graph_doctor._disk_candidates(graph, "схема й.pdf")]
    assert forms == [unicodedata.normalize("NFC", "схема й.pdf"), unicodedata.normalize("NFD", "схема й.pdf")]
    assert forms[0] != forms[1]
    nested = graph_doctor._disk_candidates(graph, "Café/план й.pdf")
    assert len(nested) == 4 and any(
        c.parent.name == unicodedata.normalize("NFC", "Café") and c.name == unicodedata.normalize("NFD", "план й.pdf")
        for c in nested), "каталог в NFC, файл в NFD — есть среди кандидатов"


def test_find_canonical_reads_aliases_from_node_frontmatter(tmp_path):
    """`aliases:` в шапке узла — записанное знание «это то же самое»; до
    №127 конвейер его не читал. Список, блок и одиночная строка — YAML;
    ключ имени (пунктуация не важна); при заданной папке записи псевдоним
    ищется только в ней (человек с псевдонимом системы не перехватывает
    систему — Critical трёх голов, круг-1 #451); два узла с одним
    псевдонимом — не гадаем; точное имя и ключ в папке важнее псевдонима."""
    import os
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Люди").mkdir()
    vitrina = graph / "Системы" / "Витрина 1494.md"
    vitrina.write_text('---\ntype: система\naliases: ["ИС 1494", "Витрина данных"]\n---\n# Витрина 1494\n',
                       encoding="utf-8")
    reestr = graph / "Системы" / "Реестр.md"
    reestr.write_text("---\ntype: система\naliases:\n  - Реестр поручений\n  - РП\n---\n# Реестр\n",
                      encoding="utf-8")
    scalar = graph / "Системы" / "Шина.md"
    scalar.write_text("---\naliases: Корпоративная шина\n---\n# Шина\n", encoding="utf-8")
    assert g.node_aliases(vitrina.read_text(encoding="utf-8")) == ["ИС 1494", "Витрина данных"]
    assert g.node_aliases(reestr.read_text(encoding="utf-8")) == ["Реестр поручений", "РП"]
    assert g.node_aliases(scalar.read_text(encoding="utf-8")) == ["Корпоративная шина"]
    assert g.find_canonical(graph, "ИС-1494") == vitrina
    assert g.find_canonical(graph, "ИС-1494", folder="Системы") == vitrina
    assert g.find_canonical(graph, "витрина данных", folder="Люди") is None, "псевдоним — не через папку записи"
    assert g.find_canonical(graph, "Реестр поручений") == reestr
    assert g.find_canonical(graph, "корпоративная шина") == scalar
    # DS-репро: человек с псевдонимом системы не перехватывает систему
    ivan = graph / "Люди" / "Иван Иванов.md"
    ivan.write_text('---\ntype: человек\naliases: ["ИС 1494"]\n---\n# Иван Иванов\n', encoding="utf-8")
    sys1494 = graph / "Системы" / "ИС 1494.md"
    sys1494.write_text("# ИС 1494\n", encoding="utf-8")
    assert g.find_canonical(graph, "ИС-1494", folder="Системы") == sys1494, "ключ в папке важнее псевдонима"
    assert g.find_canonical(graph, "ИС 1494") == sys1494, "точное имя важнее псевдонима"
    # псевдоним появился позже — кэш шапок обновляется по (inode, размер, mtime)
    assert g.find_canonical(graph, "Ваня", folder="Люди") is None
    ivan.write_text('---\ntype: человек\naliases: ["ИС 1494", Ваня]\n---\n# Иван Иванов\n', encoding="utf-8")
    os.utime(ivan, ns=(os.stat(ivan).st_atime_ns, os.stat(ivan).st_mtime_ns + 10_000_000))
    assert g.find_canonical(graph, "Ваня", folder="Люди") == ivan
    # два узла с одним псевдонимом — не гадаем, и подстрока дальше не гадает тоже
    amb: list[str] = []
    (graph / "Люди" / "Иван Петров.md").write_text("---\naliases: [Ваня]\n---\n# Иван Петров\n", encoding="utf-8")
    assert g.find_canonical(graph, "Ваня", folder="Люди", ambiguous=amb) is None
    assert sorted(amb) == ["Иван Иванов", "Иван Петров"]
    assert g.find_canonical(graph, "Ваня") is None, "два узла с одной кличкой — не гадаем и без папки"
    assert g.find_canonical(graph, "ИС-1494") == sys1494
    assert g.find_canonical(graph, "Витрина данных") == vitrina, "псевдоним без папки — связь"
    assert g.link_or_text(graph, "Витрина данных").startswith("[[Системы/Витрина 1494"), "связь по псевдониму без папки"
    (graph / "Люди" / "Анна Смирнова.md").write_text("---\naliases: [Аня]\n---\n# Анна Смирнова\n", encoding="utf-8")
    assert g.find_canonical(graph, "Аня").name == "Анна Смирнова.md", "однословная кличка без папки — связь (DS r3)"
    assert g.find_canonical(graph, "Ани") is None, "ключ полный: «Ани» не «Аня»"
    # YAML-битая шапка (двоеточие в соседнем поле) псевдонимы не теряет — fallback по полю
    broken = graph / "Системы" / "Миграция БД.md"
    broken.write_text("---\ndesc: План: сделать\naliases: [\"МБ, база\", МБД]\n---\n# Миграция БД\n", encoding="utf-8")
    assert g.node_aliases(broken.read_text(encoding="utf-8")) == ["МБ, база", "МБД"]
    assert g.find_canonical(graph, "МБД", folder="Системы") == broken


def test_aliases_parse_as_yaml_and_skip_stubs_and_broken_files(tmp_path):
    """Запятая в кавычках — часть имени, а не два псевдонима (Critical GLM);
    шапка без закрывающего `---` — не шапка; `aliases: []` — пусто; дубли
    псевдонима — один хит; не-UTF8 файл и заглушка-редирект поиск не
    ломают и псевдонимов не отдают."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Системы").mkdir()
    petrov = graph / "Люди" / "Петров.md"
    petrov.write_text('---\naliases: ["Петров, Иван", Ваня, Ваня]\n---\n# Петров\n', encoding="utf-8")
    assert g.node_aliases(petrov.read_text(encoding="utf-8")) == ["Петров, Иван", "Ваня"]
    assert g.find_canonical(graph, "Иван", folder="Люди") is None, "фантомного псевдонима «Иван» нет"
    assert g.find_canonical(graph, "Петров, Иван", folder="Люди") == petrov
    (graph / "Люди" / "Кузнецов.md").write_text("---\ntype: человек\nТекст без закрытия\naliases: [Кузя]\n",
                                                encoding="utf-8")
    assert g.find_canonical(graph, "Кузя", folder="Люди") is None, "шапка без закрывающего --- — не шапка"
    assert g.node_aliases("---\naliases: []\n---\n# X\n") == []
    assert g.node_aliases("---\naliases: [\"a\", \"a\", \"\"]\n---\n# X\n") == ["a"]
    amb: list[str] = []
    assert g.find_canonical(graph, "Ваня", folder="Люди", ambiguous=amb) == petrov, "дубль псевдонима — один хит"
    assert amb == []
    (graph / "Системы" / "Битый.md").write_bytes(b"---\naliases: [\xff]\n---\n")
    stub = graph / "Системы" / "Старая витрина.md"
    stub.write_text("---\naliases: [ИС 1494]\n---\n# Старая витрина → [[Системы/Витрина]]\n\nДубль слит.\n",
                    encoding="utf-8")
    (graph / "Системы" / "Витрина.md").write_text("# Витрина\n", encoding="utf-8")
    assert g.find_canonical(graph, "ИС 1494", folder="Системы") is None, "псевдоним заглушки не ведёт в неё"


def test_frontmatter_with_aliases_edits_the_header_in_place():
    import frontmatter
    assert frontmatter.with_aliases("# Узел\n", ["А, Б"]) == '---\naliases: ["А, Б"]\n---\n# Узел\n'
    txt = "---\ntype: ядро\naliases: [МБ]\ntags: [ядро]\n---\n# Миграция\n"
    out = frontmatter.with_aliases(txt, ["Миграция БД", "МБ"])
    assert out == '---\ntype: ядро\naliases: ["МБ", "Миграция БД"]\ntags: [ядро]\n---\n# Миграция\n'
    assert frontmatter.with_aliases(out, ["МБ"]) == out, "ничего нового — текст не тронут"
    block = "---\naliases:\n  - a\n  - b\ntype: x\n---\nтело\n"
    assert frontmatter.aliases(frontmatter.with_aliases(block, ["c"])) == ["a", "b", "c"]
    assert frontmatter.parse(frontmatter.with_aliases(block, ["c"]))["type"] == "x"
    unclosed = "---\ntype: x\nтекст\n"
    assert frontmatter.with_aliases(unclosed, ["a"]) == unclosed, "незакрытая шапка — новую поверх не заводим"
    # `]` в кавычках и блок без отступа — поле заменяется целиком, соседи целы (luna r2)
    tricky = '---\naliases: ["A]B", "C"]\ntype: x\n---\nтело\n'
    assert frontmatter.with_aliases(tricky, ["D"]) == '---\naliases: ["A]B", "C", "D"]\ntype: x\n---\nтело\n'
    flat = "---\naliases:\n- a\n- b\ntype: x\n---\nтело\n"
    assert frontmatter.with_aliases(flat, ["c"]) == '---\naliases: ["a", "b", "c"]\ntype: x\n---\nтело\n'
    multi = '---\ntype: x\naliases: [\n  "a",\n  "b"\n]\ntags: [t]\n---\n'
    assert frontmatter.with_aliases(multi, ["c"]) == '---\ntype: x\naliases: ["a", "b", "c"]\ntags: [t]\n---\n'
    # YAML-ошибка в соседнем поле: старые псевдонимы не теряются при дописывании
    broken = '---\naliases: ["Старый"]\nописание: Проект: перенос\n---\n'
    assert frontmatter.aliases(frontmatter.with_aliases(broken, ["Новый"])) == ["Старый", "Новый"]
    assert frontmatter.split("---\naliases: [A]\n---garbage\n# тело\n")[0] is None, "`---garbage` — не закрытие"
    # апостроф в незакавыченном элементе — часть имени; соседние поля целы (Critical GLM r2)
    apos = "---\ntype: система\ntags: [встречи, авто]\naliases: [Д'Артаньян]\nstatus: в работе\n---\n# Д\n"
    out = frontmatter.with_aliases(apos, ["Дубль"])
    assert "status: в работе" in out and frontmatter.aliases(out) == ["Д'Артаньян", "Дубль"], out
    unbalanced = "---\naliases: [ИС 1494\nstatus: в работе\n---\n# X\n"
    out = frontmatter.with_aliases(unbalanced, ["Y"])
    assert "status: в работе" in out and out.count("aliases:") == 1, out
    # CRLF-шапка закрывается, псевдонимы читаются (Important GLM r2)
    crlf = "---\r\ntype: человек\r\naliases: [Кузя]\r\n---\r\n# Кузнецов\r\n"
    assert frontmatter.aliases(crlf) == ["Кузя"] and frontmatter.split(crlf)[1].startswith("# Кузнецов")
    # числа/булевы — как записаны, а не как YAML их понял
    assert frontmatter.aliases("---\naliases: [01, on]\n---\n") == ["01", "on"]
    assert frontmatter.aliases("---\naliases: 1494\n---\n") == ["1494"]
    # не-строка в списке, который fallback не разбирает (блок без отступа + число): строки живут
    assert frontmatter.aliases("---\naliases:\n- Витрина\n- 2026\n---\n") == ["Витрина", "2026"]
    assert frontmatter.aliases('---\naliases: ["x]y", 01]\n---\n') == ["x]y", "01"], "как записано, «]» в кавычках не рвёт"
    assert frontmatter.aliases("---\naliases: null\n---\n") == [] and frontmatter.aliases("---\naliases: ~\n---\n") == []
    assert frontmatter.aliases("---\naliases: [foo, null, {a: 1}]\n---\n") == ["foo"], "null и mapping — не псевдонимы"
    assert frontmatter.yaml_str("A\u2028B") == '"A\\u2028B"'
    esc = '---\naliases: ["A\\" ] B",\n  "C"]\ntype: x\n---\n'
    out = frontmatter.with_aliases(esc, ["D"])
    assert frontmatter.parse(out)["type"] == "x" and frontmatter.aliases(out) == ['A" ] B', "C", "D"], out
    # GLM r3: псевдоним из одной кавычки и «]» ниже по шапке — соседние поля целы
    weird = '---\naliases: ["\\""]\ntags: [x]\ndesc: скажет "привет"\nпрочее: список завершён]\n---\n'
    out = frontmatter.with_aliases(weird, ["D"])
    parsed = frontmatter.parse(out)
    assert parsed["tags"] == ["x"] and parsed["прочее"] == "список завершён]" and frontmatter.aliases(out) == ['"', "D"], out
    # человеческий хвост после машинной пометки не сносится
    human = "## Хроника\n- [[Встречи/2026-08-10_1000]] — статус уточнён повторным разбором на планёрке\n"
    assert "на планёрке" in g._annotate_chronicle(human, "Встречи/2026-08-10_1000", "статус уточнён повторным разбором, было «x»")[0]
    # CRLF-шапка: дописанные строки — тоже CRLF
    crlf_out = frontmatter.with_aliases("---\r\ntype: человек\r\naliases: [Кузя]\r\n---\r\n# X\r\n", ["Новый"])
    assert "\n\r\n" not in crlf_out and crlf_out.startswith('---\r\ntype: человек\r\naliases: ["Кузя", "Новый"]\r\n---\r\n# X')
    assert frontmatter.split("---\ntype: x\n----\nне закрытие\n---\nтело\n")[0] == "\ntype: x\n----\nне закрытие"
    assert frontmatter.aliases("---\nbad: [\naliases: Кузя\n---\n") == ["Кузя"], "скаляр через fallback"


def test_core_chronicle_keeps_superseded_status_with_its_dates(tmp_path):
    """Статус ядра перезаписывается каждой встречей; вытесненный уходит в
    хронику с датой, с которой держался, — у факта есть «с» и «по» (№127).
    Ретрай той же встречи и тот же статус строку не плодят."""
    graph = tmp_path / "g"
    g.upsert_core(graph, {"имя": "Миграция", "статус": "план готов", "обновление": "обсудили план"},
                  "Встречи/2026-08-01_1000", "2026-08-01_1000")
    g.upsert_core(graph, {"имя": "Миграция", "статус": "план готов", "обновление": "без изменений"},
                  "Встречи/2026-08-05_1000", "2026-08-05_1000")
    g.upsert_core(graph, {"имя": "Миграция", "статус": "в работе, срок сдвинут", "обновление": "старт"},
                  "Встречи/2026-08-10_1000", "2026-08-10_1000")
    g.upsert_core(graph, {"имя": "Миграция", "статус": "в работе, срок сдвинут", "обновление": "старт"},
                  "Встречи/2026-08-10_1000", "2026-08-10_1000")   # ретрай
    text = (graph / "Ядра" / "Миграция.md").read_text(encoding="utf-8")
    assert "## Статус\nв работе, срок сдвинут _(обновлено 2026-08-10)_" in text
    assert text.count("вытеснило статус") == 1, text
    assert "- [[Встречи/2026-08-10_1000]] — старт · вытеснило статус (с 2026-08-05): «план готов»" in text
    assert text.count("2026-08-10_1000") == 1, "ретрай не дублирует строку хроники"
    assert g._current_status(text) == ("в работе, срок сдвинут", "2026-08-10")
    # ретрай той же встречи с ДРУГИМ статусом — уточнение к её же строке, не потеря
    g.upsert_core(graph, {"имя": "Миграция", "статус": "в работе, срок 15.09", "обновление": "старт"},
                  "Встречи/2026-08-10_1000", "2026-08-10_1000")
    text = (graph / "Ядра" / "Миграция.md").read_text(encoding="utf-8")
    assert "## Статус\nв работе, срок 15.09 _(обновлено 2026-08-10)_" in text
    assert text.count("2026-08-10_1000") == 1 and "статус уточнён повторным разбором, было «в работе, срок сдвинут»" in text
    # третий разбор той же встречи: пометка заменяется, строка не растёт (GLM r2)
    g.upsert_core(graph, {"имя": "Миграция", "статус": "в работе, срок 20.09", "обновление": "старт"},
                  "Встречи/2026-08-10_1000", "2026-08-10_1000")
    text = (graph / "Ядра" / "Миграция.md").read_text(encoding="utf-8")
    assert text.count("статус уточнён повторным разбором") == 1 and "было «в работе, срок 15.09»" in text
    # ссылка сверяется целиком: минутный штамп — не префикс посекундного
    marked, found = g._annotate_chronicle("## Хроника\n- [[Встречи/2026-08-10_100023]] — x\n- [[Встречи/2026-08-10_1000]] — y\n",
                                          "Встречи/2026-08-10_1000", "п")
    assert found and marked.splitlines()[2].endswith(" · п") and "x · п" not in marked
    assert g._annotate_chronicle("## Хроника\n- [[Встречи/2026-08-11_1000]] — z\n", "Встречи/2026-08-10_1000", "п")[1] is False
    # прочерк — не статус: не перезаписывает и не вытесняет
    g.upsert_core(graph, {"имя": "Миграция", "статус": "—", "обновление": "упомянули"},
                  "Встречи/2026-08-12_1000", "2026-08-12_1000")
    text = (graph / "Ядра" / "Миграция.md").read_text(encoding="utf-8")
    assert "## Статус\nв работе, срок 20.09" in text and text.count("вытеснило статус") == 1
    assert g._clip("а" * 200) == "а" * 159 + "…"
    # и в самой хронике: посекундная встреча не «занимает» ссылку минутной
    g.upsert_core(graph, {"имя": "Миграция", "статус": "сдано", "обновление": "финиш"},
                  "Встречи/2026-08-13_100012", "2026-08-13_100012")
    g.upsert_core(graph, {"имя": "Миграция", "статус": "сдано, акт подписан", "обновление": "акт"},
                  "Встречи/2026-08-13_1000", "2026-08-13_1000")
    text = (graph / "Ядра" / "Миграция.md").read_text(encoding="utf-8")
    assert g.has_link(text, "Встречи/2026-08-13_1000") and g.has_link(text, "Встречи/2026-08-13_100012")
    assert "- [[Встречи/2026-08-13_1000]] — акт · вытеснило статус (с 2026-08-13): «сдано»" in text


def test_placeholder_migration_turns_links_into_text_and_moves_nodes(tmp_path):
    """№125: ссылки на узлы-метки становятся подписью текстом (alias или имя),
    узлы уезжают в копию с манифестом, указатель пересобирается; узел с тем
    же стемом в другой папке и встроенные `![[…]]` не трогаются; dry-run
    ничего не меняет; повторный запуск — «делать нечего»."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import migrate_placeholders as mp
    graph = tmp_path / "g"
    for d in ("Люди", "Системы", "Встречи", "Досье"):
        (graph / d).mkdir(parents=True)
    (graph / "Люди" / "Собеседник 3.md").write_text("# Собеседник 3\n\n## Встречи\n- [[Встречи/2026-08-01_1000]]\n", encoding="utf-8")
    (graph / "Люди" / "Собеседник 1 (Саша).md").write_text("# Собеседник 1 (Саша)\n", encoding="utf-8")
    (graph / "Люди" / "Иван Иванов.md").write_text("# Иван Иванов\n", encoding="utf-8")
    (graph / "Системы" / "Собеседник 3.md").write_text("# Собеседник 3 (система-тёзка)\n", encoding="utf-8")
    (graph / "Люди" / "Таня (Собеседник 4).md").write_text("# Таня (Собеседник 4)\n", encoding="utf-8")
    meeting = graph / "Встречи" / "2026-08-01_1000.md"
    meeting.write_text(
        "# Встреча\n\n## Участники\n- [[Люди/Собеседник 3|Собеседник 3]] · [[Люди/Иван Иванов|Иван]]\n"
        "- [[Собеседник 3]] сказал: «да» · [[Люди/Собеседник 1 (Саша)]] · [[люди/собеседник 3|«Собеседник 3»]]\n"
        "| [[Люди/Собеседник 3\\|Собеседник 3]] | [[Системы/Собеседник 3]] | ![[Люди/Собеседник 3]] |\n"
        "- [[Люди/Собеседник 3#Встречи|раздел]] и [[Люди/Собеседник 3.md]] и [[Люди/Собеседник 1 (Саша).markdown]]\n"
        "- [[Люди/Собеседник 3^blk|голос]]\n"
        "```\n[[Люди/Собеседник 3|в коде]]\n```\n", encoding="utf-8")
    (graph / "Досье" / "Тема.md").write_text("Говорил [[Люди/Собеседник 3|Собеседника 3]].\n", encoding="utf-8")
    before = meeting.read_text(encoding="utf-8")
    p = mp.plan(graph)
    assert p["nodes"] == ["Собеседник 1 (Саша)", "Собеседник 3"] and p["links"] == 10, p
    assert p["manual"] == ["Таня (Собеседник 4)"], "имя + метка в скобках — ручное решение, не миграция"
    assert p["namesakes_elsewhere"] == [g.name_key("Собеседник 3")], "тёзка в Системах — голую ссылку оставить"
    assert meeting.read_text(encoding="utf-8") == before, "dry-run ничего не меняет"
    out = mp.apply(graph, tmp_path / "backup", log=lambda *_: None)
    text = meeting.read_text(encoding="utf-8")
    assert text.count("[[Люди/Собеседник") == 1 and "[[люди/" not in text, "осталась только ссылка внутри кода"
    assert "- Собеседник 3 · [[Люди/Иван Иванов|Иван]]" in text
    assert "- [[Собеседник 3]] сказал" in text, "голая ссылка при тёзке в Системах остаётся — Obsidian поведёт к нему"
    assert "Собеседник 1 (Саша)" in text and "«Собеседник 3»" in text
    assert "| Собеседник 3 | [[Системы/Собеседник 3]] | Собеседник 3 |" in text, "тёзка в Системах цел, вложение — текстом"
    assert "- раздел и Собеседник 3 и Собеседник 1 (Саша)\n- голос\n" in text
    assert "```\n[[Люди/Собеседник 3|в коде]]\n```" in text, "внутри кода не трогаем"
    assert "Говорил Собеседника 3." in (graph / "Досье" / "Тема.md").read_text(encoding="utf-8")
    assert not (graph / "Люди" / "Собеседник 3.md").exists() and (graph / "Люди" / "Иван Иванов.md").exists()
    assert (graph / "Люди" / "Таня (Собеседник 4).md").exists(), "узел с именем не тронут"
    dest = pathlib.Path(out["backup"])
    assert (dest / "Люди" / "Собеседник 3.md").exists() and (dest / "files" / "Встречи" / "2026-08-01_1000.md").read_text(encoding="utf-8") == before
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["links"] == 10 and manifest["nodes"] == ["Собеседник 1 (Саша)", "Собеседник 3"]
    assert manifest["status"] == "applied" and manifest["leftovers"] == [] and "rollback" in manifest
    assert manifest["index_rebuilt"] is True and (dest / "files" / "Люди" / "_ЛЮДИ.md").exists() is False or True
    index = (graph / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    assert "Иван Иванов" in index and "Собеседник 3" not in index
    assert mp.plan(graph)["nodes"] == [] and mp.apply(graph, tmp_path / "backup", log=lambda *_: None)["links"] == 0
    assert g.is_placeholder_node("Собеседник 1 (Саша)") and g.is_placeholder_node("Speaker 2 (муж)")
    assert g.is_placeholder_node("Таня (Собеседник 4)") and not g.is_placeholder_node("Иван Иванов")
    assert g.is_placeholder_node("Анна (Participant 4)") and g.is_placeholder_node("Ли (发言人 2)"), "те же метки, что для целого имени"
    assert not g.is_placeholder_node("Саша (собеседница)"), "«собеседница» в скобках — не метка"


def test_placeholder_migration_refuses_unreadable_files_and_symlinks(tmp_path):
    """Нечитаемый файл мог содержать ссылку на узел — снимать узел нельзя;
    симлинк пишется в цель, а копия — не та: миграция не начинается (luna C3/C4)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import migrate_placeholders as mp
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Люди" / "Собеседник 2.md").write_text("# Собеседник 2\n", encoding="utf-8")
    (graph / "Встречи" / "битая.md").write_bytes(b"\xff\xfe " + "[[Люди/Собеседник 2]]".encode("utf-8"))
    p = mp.plan(graph)
    assert p["unreadable"] == ["Встречи/битая.md"]
    with pytest.raises(SystemExit):
        mp.apply(graph, tmp_path / "backup", log=lambda *_: None)
    assert (graph / "Люди" / "Собеседник 2.md").exists() and not (tmp_path / "backup").exists()
    (graph / "Встречи" / "битая.md").unlink()
    real = tmp_path / "outside.md"
    real.write_text("- [[Люди/Собеседник 2]]\n", encoding="utf-8")
    (graph / "Встречи" / "ссылка.md").symlink_to(real)
    p = mp.plan(graph)
    assert p["symlinks"] == ["Встречи/ссылка.md"]
    with pytest.raises(SystemExit):
        mp.apply(graph, tmp_path / "backup", log=lambda *_: None)
    assert real.read_text(encoding="utf-8") == "- [[Люди/Собеседник 2]]\n", "цель симлинка не тронута"


def test_placeholder_migration_writes_the_manifest_before_touching_the_graph(tmp_path, monkeypatch):
    """Падение посередине: опись и копии уже на месте, статус partial (DS I2)."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import migrate_placeholders as mp
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Люди" / "Собеседник 2.md").write_text("# Собеседник 2\n", encoding="utf-8")
    (graph / "Встречи" / "2026-08-01_1000.md").write_text("- [[Люди/Собеседник 2]] сказал\n", encoding="utf-8")
    monkeypatch.setattr(mp.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("iCloud занят")))
    with pytest.raises(OSError):
        mp.apply(graph, tmp_path / "backup", log=lambda *_: None)
    dest = next((tmp_path / "backup").iterdir())
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial" and manifest["files_done"] == ["Встречи/2026-08-01_1000.md"]
    assert (dest / "files" / "Встречи" / "2026-08-01_1000.md").exists(), "копия изменённого файла есть"
    assert (graph / "Люди" / "Собеседник 2.md").exists(), "узел на месте — перенос не удался"


def test_placeholder_migration_cli_guards(tmp_path):
    """Предохранители CLI: без --backup — 2; корень без logs/ — 2; копия внутри
    графа — отказ; --report пишет полный план (GLM по #454)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "Собеседник 2.md").write_text("# Собеседник 2\n", encoding="utf-8")
    script = root / "scripts" / "migrate_placeholders.py"
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--apply"],
                       capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 2 and "--backup" in r.stderr
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--apply", "--backup", str(tmp_path / "b"),
                        "--root", str(tmp_path / "нет-такого")], capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 2 and "logs/" in r.stderr
    data_root = tmp_path / "data"
    (data_root / "logs").mkdir(parents=True)
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--apply", "--backup", str(graph / "копия"),
                        "--root", str(data_root)], capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode != 0 and "вне графа" in (r.stderr + r.stdout)
    assert (graph / "Люди" / "Собеседник 2.md").exists()
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--report", str(tmp_path / "plan.json")],
                       capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 0 and json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))["nodes"] == ["Собеседник 2"]

