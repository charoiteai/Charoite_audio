"""Гигиена графа и временных файлов — то, что портится тихо.

Ни один из этих дефектов не давал ошибки: узел просто оставался несвязанным,
провенанс подтверждал выдумку, а копия часовой записи лежала в /var/folders
до перезагрузки, о чём ретеншн приватности не знал.
"""
import json
import os
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


def test_people_description_is_superseded_with_dates_not_silently(tmp_path):
    """№194 (Q4 аудита памяти 07.09): описание человека/системы — факт с датой.
    Существенно другое описание вытесняет прежнее, прежнее уходит в «## Хроника»
    целиком с обеими формулировками и датой встречи, как статус у Ядер.
    Пересказ (то же, короче, перестановка слов) новым фактом не считается;
    обогащение, отрицание, смена одного слова из трёх, смена даты — считаются
    (Critical DS и Important GLM, круг 1 по #539: ошибка в сторону «новый факт»
    видна строкой и правится, ошибка в сторону «пересказ» невидима). Пустое не
    вытесняет; ретрай той же встречи строк не плодит; пустой узел заполняется
    без следа."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    g.upsert_entity(graph, "Люди", "Пётр", "person", "аналитик", "Встречи/2026-07-15_1400", "спросил")
    node = graph / "Люди" / "Пётр.md"
    g.upsert_entity(graph, "Люди", "Пётр", "person", "Аналитик", "Встречи/2026-07-20_1000", "")
    text = node.read_text(encoding="utf-8")
    assert "# Пётр\nаналитик\n" in text and "## Хроника" not in text, "регистр — не новый факт"
    g.upsert_entity(graph, "Люди", "Пётр", "person", "", "Встречи/2026-07-25_1000", "")
    assert "# Пётр\nаналитик\n" in node.read_text(encoding="utf-8"), "пустое описание не вытесняет"
    g.upsert_entity(graph, "Люди", "Пётр", "person", "руководитель проекта миграции",
                    "Встречи/2026-08-20_1000", "ведёт")
    text = node.read_text(encoding="utf-8")
    assert "# Пётр\nруководитель проекта миграции\n\n_(последнее упоминание: 2026-08-20)_" in text
    assert "\nаналитик\n" not in text and text.count("последнее упоминание") == 1
    assert ("## Хроника\n- [[Встречи/2026-08-20_1000]] — _(было: «аналитик» → "
            "стало: «руководитель проекта миграции», 2026-08-20)_") in text, text
    assert text.index("## Хроника") < text.index("## Встречи") and "- [[Встречи/2026-08-20_1000]] — ведёт" in text
    g.upsert_entity(graph, "Люди", "Пётр", "person", "руководитель проекта миграции",
                    "Встречи/2026-08-20_1000", "ведёт")                          # ретрай
    assert node.read_text(encoding="utf-8").count("2026-08-20_1000") == 2, "хроника + встречи, без дублей"
    # пересказ: перестановка слов — не новый факт
    g.upsert_entity(graph, "Люди", "Пётр", "person", "руководитель миграции проекта", "Встречи/2026-08-22_1000", "")
    text = node.read_text(encoding="utf-8")
    assert "# Пётр\nруководитель проекта миграции\n" in text and text.count("_(было:") == 1
    # смена одного слова из трёх — новый факт (GLM: «проекта миграции» → «проекта бюджета»)
    g.upsert_entity(graph, "Люди", "Пётр", "person", "руководитель проекта бюджета", "Встречи/2026-09-01_1000", "")
    text = node.read_text(encoding="utf-8")
    assert text.count("_(было:") == 2 and text.index("2026-09-01_1000") < text.index("2026-08-20_1000")
    assert "было: «руководитель проекта миграции» → стало: «руководитель проекта бюджета», 2026-09-01" in text
    # обогащение и отрицание — новый факт, а не «пересказ» (Critical DS)
    g.upsert_entity(graph, "Люди", "Пётр", "person", "бывший руководитель проекта бюджета, ушёл в другой отдел",
                    "Встречи/2026-09-03_1000", "")
    text = node.read_text(encoding="utf-8")
    assert "# Пётр\nбывший руководитель проекта бюджета, ушёл в другой отдел\n" in text and text.count("_(было:") == 3
    # узел без описания получает первое без строки хроники — это не вытеснение; «—» не описание
    old = graph / "Люди" / "Старый.md"
    old.write_text("# Старый\n\n## Встречи\n- [[Встречи/2026-05-05_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Старый", "person", "—", "Встречи/2026-07-31_1000", "")
    assert not old.read_text(encoding="utf-8").startswith("# Старый\n—")
    g.upsert_entity(graph, "Люди", "Старый", "person", "тестировщик", "Встречи/2026-08-01_1000", "")
    t = old.read_text(encoding="utf-8")
    assert t.startswith("# Старый\nтестировщик\n\n_(последнее упоминание: 2026-08-01)_\n\n## Встречи\n") \
        and "## Хроника" not in t, t
    # описание из двух абзацев уходит в «было» целиком, сироты под новым не остаётся (DS/GLM Minor)
    multi = graph / "Люди" / "Многострочный.md"
    multi.write_text("# Многострочный\nстрока раз\nстрока два\n\nведёт отчётность\n\n"
                     "_(последнее упоминание: 2026-06-01)_\n\n## Встречи\n- [[Встречи/2026-06-01_1000]]\n",
                     encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Многострочный", "person", "совсем новая роль", "Встречи/2026-08-02_1000", "")
    t = multi.read_text(encoding="utf-8")
    assert "# Многострочный\nсовсем новая роль\n\n_(последнее упоминание: 2026-08-02)_" in t, t
    assert "было: «строка раз строка два ведёт отчётность»" in t and "отчётность\n" not in t.split("## Хроника")[0]


def test_same_fact_rule_table():
    """Одно правило (три круга по #539, Critical в каждом — упрощали, а не
    латали): пересказ = те же слова и те же числа (регистр, ё/е, порядок,
    повторы); всё остальное — новый факт, усечение и словари-переворотов
    включительно."""
    same = g._same_fact
    for old, new in [("аналитик", "Аналитик"), ("ведёт отчётность", "ведет отчетность"),
                     ("руководитель проекта миграции", "руководитель миграции проекта"),
                     ("в отпуске до 2026-09-20", "до 2026-09-20 в отпуске"),
                     ("ведёт проект, проект ведёт", "ведёт проект"),          # повторы
                     ("робот 2026", "2026 робот"), ("проект 1С и SAP", "SAP и 1С проект")]:  # мимо шортката равенства (DS r4)
        assert same(old, new), (old, new)
    for old, new in [("не участвует в проекте", "участвует в проекте"),      # снятое отрицание (Critical r2)
                     ("участвует в проекте", "не участвует в проекте"),
                     ("бывший аналитик", "аналитик"), ("аналитик", "бывший аналитик"),
                     ("бывшие руководители проекта", "руководители проекта"),  # формы слова (Critical DS r3)
                     ("перестал вести проект", "вести проект"), ("курирует проект временно", "курирует проект"),
                     ("нет в городе", "в городе"),                             # GLM I1 r3
                     ("руководитель проекта миграции", "руководитель проекта"),  # усечение — новый факт
                     ("участвует в проекте и в тестировании", "участвует в проекте"),
                     ("в отпуске до 2026-09-20", "в отпуске"),              # дата изъята
                     ("в отпуске до 2026-09-20", "в отпуске до 2026-09-27"),
                     ("отпуск с 12.09", "отпуск с 09.12"),                    # порядок чисел — сам факт (Important DS r4)
                     ("версия 1.2", "версия 2.1"),
                     ("аналитик", "аналитик данных"),                         # обогащение
                     ("руководитель проекта миграции", "руководитель проекта бюджета"),
                     ("ведущий инженер КЭСП", "старший инженер КЭСП"),
                     ("курирует поставки оборудования для строек северного направления в регионах",
                      "курирует поставки оборудования для строек южного направления в регионах"),  # DS I1 r2
                     ("отвечает за миграцию для проекта", "отвечает за тестирование для проекта"),
                     ("QA", "руководитель QA-направления"), ("2026", "2027"), ("", "аналитик"), ("аналитик", "")]:
        assert not same(old, new), (old, new)


def test_people_chronicle_keeps_last_ten_and_archives_the_rest(tmp_path):
    """№233 (критика DS r3/r4 по #539): хроника вытеснений держит последние
    десять строк, старшие переезжают в «## Архив хроники» в конец узла — новые
    сверху в обоих; ссылка встречи из архива по-прежнему гасит ретрай; раздел
    с рукописной строкой не режется."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    node = graph / "Люди" / "Лента.md"
    for i in range(13):
        g.upsert_entity(graph, "Люди", "Лента", "person", f"роль {i}", f"Встречи/2026-01-{i + 1:02d}_1000", "")
    text = node.read_text(encoding="utf-8")
    chron = text.split("## Хроника\n", 1)[1].split("\n## ", 1)[0]
    items = [ln for ln in chron.splitlines() if ln.startswith("- ")]
    assert g.CHRONICLE_KEEP == 10 and len(items) == 10, items
    assert "2026-01-13" in items[0] and "2026-01-04" in items[-1], "новые сверху, десятая — самая старая из оставленных"
    assert text.index("## Встречи") < text.index("## Архив хроники"), "архив — в конце узла, не перед встречами"
    archive = [ln for ln in text.split("## Архив хроники\n", 1)[1].splitlines() if ln.startswith("- ")]
    assert [ln.split("]]")[0] for ln in archive] == ["- [[Встречи/2026-01-03_1000", "- [[Встречи/2026-01-02_1000"], archive
    assert "роль 0" in archive[-1] and "роль 1" in archive[-1], "факт переехал целиком, не обрезан"
    assert text.count("- [[Встречи/2026-01-") == 13 + 12, "13 ссылок встреч + 12 строк вытеснений, ничего не потеряно"
    # ретрай встречи, чья строка уже в архиве, — без изменений
    g.upsert_entity(graph, "Люди", "Лента", "person", "роль 99", "Встречи/2026-01-02_1000", "")
    assert node.read_text(encoding="utf-8") == text
    # следующее вытеснение: в хронике по-прежнему десять, архив растёт сверху
    g.upsert_entity(graph, "Люди", "Лента", "person", "роль 13", "Встречи/2026-01-14_1000", "")
    text = node.read_text(encoding="utf-8")
    chron = [ln for ln in text.split("## Хроника\n", 1)[1].split("\n## ", 1)[0].splitlines() if ln.startswith("- ")]
    archive = [ln.split("]]")[0] for ln in text.split("## Архив хроники\n", 1)[1].splitlines() if ln.startswith("- ")]
    assert len(chron) == 10 and "2026-01-14" in chron[0]
    assert archive == ["- [[Встречи/2026-01-04_1000", "- [[Встречи/2026-01-03_1000", "- [[Встречи/2026-01-02_1000"], archive
    # рукопись в хронике остаётся на месте — свой пункт, строка без пункта, пустая строка
    # между группами; режутся только машинные строки сверх десяти (Important DS r1 по #542)
    line = "- [[Встречи/2026-02-{0:02d}_1000]] — _(было: «a{0}» → стало: «b{0}»)_"
    manual = ("# Ручной\nаналитик\n\n## Хроника\n- моя заметка сверху\n" + "\n".join(line.format(i) for i in range(12, 6, -1))
              + "\n\nсюда я дописал сам\n" + "\n".join(line.format(i) for i in range(6, 0, -1))
              + "\n- мой пункт снизу\n\n## Встречи\n- [[Встречи/2026-02-01_1000]]\n")
    capped = g._cap_chronicle(manual)
    chron = capped.split("## Хроника\n", 1)[1].split("\n## Встречи", 1)[0]
    assert chron.startswith("- моя заметка сверху\n") and "\n\nсюда я дописал сам\n" in chron and chron.endswith("- мой пункт снизу\n"), chron
    assert [ln for ln in chron.splitlines() if g._CHRONICLE_LINE_RE.match(ln)] == [line.format(i) for i in range(12, 2, -1)]
    archive = capped.split("## Архив хроники\n", 1)[1]
    assert archive == line.format(2) + "\n" + line.format(1) + "\n", archive
    assert g._cap_chronicle(capped) == capped, "идемпотентно"
    # края (Minor DS r1): хроника последним разделом, переполнение блоком, CRLF не трогаем
    tail = "# Хвост\nроль\n\n## Хроника\n" + "\n".join(line.format(i) for i in range(13, 0, -1)) + "\n"
    capped = g._cap_chronicle(tail)
    assert "\n\n\n" not in capped and capped.endswith(line.format(1) + "\n"), capped
    assert capped.split("## Архив хроники\n", 1)[1].splitlines() == [line.format(i) for i in (3, 2, 1)], "блок из трёх — новее выше"
    crlf = manual.replace("\n", "\r\n")
    assert g._cap_chronicle(crlf) == crlf, "CRLF до read_text не трогаем — в бою его нет"
    # узел без «## Встречи» (человек снёс): раздел встреч встаёт ПЕРЕД архивом (Minor DS/GLM r1)
    (graph / "Люди" / "Безвстреч.md").write_text(
        "# Безвстреч\nаналитик\n\n## Хроника\n" + "\n".join(line.format(i) for i in range(12, 0, -1)) + "\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Безвстреч", "person", "директор", "Встречи/2026-03-01_1000", "")
    text = (graph / "Люди" / "Безвстреч.md").read_text(encoding="utf-8")
    assert text.index("## Хроника") < text.index("## Встречи") < text.index("## Архив хроники"), text
    assert "- [[Встречи/2026-03-01_1000]]\n\n## Архив хроники\n" in text
    # ядра: лента встреч — не хроника вытеснений, режется только у людей и систем
    (graph / "Ядра").mkdir()
    for i in range(1, 14):
        g.upsert_core(graph, {"имя": "Тема", "статус": f"этап {i}", "обновление": "шаг"}, f"Встречи/2026-03-{i:02d}_1000", f"2026-03-{i:02d}")
    core = (graph / "Ядра" / "Тема.md").read_text(encoding="utf-8")
    assert core.count("- [[Встречи/2026-03-") == 13 and "## Архив хроники" not in core


def test_entity_node_policy_holds_junk_typos_and_ambiguity(tmp_path, monkeypatch):
    """№193 (DS F4, критика GLM r1b, аудит памяти 07.09): узел сущности не
    заводится для мусора, для имени в одной букве от существующего узла той же
    папки и для имени, подходящего нескольким узлам; кандидаты — в
    logs/graph_ambiguous.md, в заметке встречи — текст с пометкой и ссылками
    на кандидатов. Новое и точно найденное — как раньше."""
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Модели").mkdir()
    for stem in ("Qwen 32B", "ИС 1494", "Реестр Витрин", "ИИ-агент", "ИИ_агент"):
        (graph / "Системы" / f"{stem}.md").write_text(f"# {stem}\n\n## Встречи\n", encoding="utf-8")
    (graph / "Системы" / "Витрина 1494.md").write_text('---\naliases: ["ИС Витрина"]\n---\n# Витрина 1494\n', encoding="utf-8")
    verdict = lambda name: g.entity_node_verdict(graph, "Системы", name)  # noqa: E731
    assert verdict("Qwen 32B") == ("existing", [])
    assert verdict("Kwen 32B") == ("near", ["Системы/Qwen 32B"]), "опечатка в одну букву — не новый узел"
    assert verdict("Реестр Витрины") == ("existing", []), "подстрока в папке — прежний проход 3 find_canonical"
    assert verdict("Реестр Витрен") == ("near", ["Системы/Реестр Витрин"]), "замена буквы — подстрокой не ловится, опечаткой да"
    assert verdict("ИС Витрино") == ("near", ["Системы/Витрина 1494"]), "опечатка в псевдоним — тоже кандидат (GLM M2)"
    assert verdict("ИС 1495") == ("new", []), "числа обязаны совпадать — соседняя система реальна"
    assert verdict("Витрина ЕИС") == ("new", [])
    assert verdict("ИИ агент") == ("ambiguous", ["Системы/ИИ-агент", "Системы/ИИ_агент"]), "два ключ-равных узла — не гадаем"
    assert verdict("ИС 1") == ("new", []), "короткий ключ опечаткой не судим"
    for junk in ("он", "их", "x", "Собеседник 2", "«»", "///", "!!", ""):
        assert verdict(junk) == ("junk", []), junk       # пунктуация и «без имени» — тоже обрывки (GLM I1, DS I3)
    assert g._one_edit_away("kwen32b", "qwen32b") and g._one_edit_away("abc", "abcd") and g._one_edit_away("abcd", "acd")
    assert not g._one_edit_away("abc", "abcde") and not g._one_edit_away("abcd", "abdc")
    # заглушки-редиректы: кандидат-заглушка отдаёт свой канон из другой папки; точный стем с
    # оборванным каноном — не кандидат сам себе (DS I1/I2): вердикт new, а upsert_entity
    # дальше сам скажет «заглушка без канона»
    (graph / "Модели" / "Qwen 32B.md").write_text("# Qwen 32B\n\n## Встречи\n", encoding="utf-8")
    (graph / "Системы" / "Qwen 32B.md").write_text("# Qwen 32B → [[Модели/Qwen 32B]]\n\nДубль. Смерджен\n", encoding="utf-8")
    assert verdict("Kwen 32B") == ("near", ["Модели/Qwen 32B"]), "near по всем папкам, заглушка → канон, без дубля"
    (graph / "Системы" / "Qwen 32B.md").write_text("# Qwen 32B → [[Модели/Нет такой]]\n\nДубль. Смерджен\n", encoding="utf-8")
    assert verdict("Qwen 32B") == ("new", []), "оборванная заглушка не кандидат самой себе"
    assert verdict("Kwen 32B") == ("near", ["Модели/Qwen 32B"]), "оборванная заглушка мимо, живой узел Моделей остаётся"
    (graph / "Системы" / "Qwen 32B.md").write_text("# Qwen 32B\n\n## Встречи\n", encoding="utf-8")
    (graph / "Модели" / "Qwen 32B.md").unlink()
    # шов main: apply_entities заводит новое и существующее, держит остальное, журналит один раз (DS I4, GLM M1)
    monkeypatch.setattr(g, "ROOT", tmp_path)
    ents = [{"имя": "Kwen 32B", "тип": "система", "суть": "модель"},
            {"имя": "Витрина ЕИС", "тип": "система", "суть": "витрина"},
            {"имя": "он", "тип": "система", "суть": ""},
            {"имя": "Qwen 32B", "тип": "система", "суть": "модель"}]
    held = g.apply_entities(graph, ents, "Встречи/2026-09-12_1000")
    assert held == {("Системы", "Kwen 32B"): ("near", ["Системы/Qwen 32B"]), ("Системы", "он"): ("junk", [])}, held
    assert (graph / "Системы" / "Витрина ЕИС.md").exists() and not (graph / "Системы" / "Kwen 32B.md").exists()
    assert "[[Встречи/2026-09-12_1000]]" in (graph / "Системы" / "Qwen 32B.md").read_text(encoding="utf-8")
    note = (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert note.startswith("# Кандидаты на узлы графа") and note.count("\n- ") == 1, note
    assert "- 2026-09-12 [[Встречи/2026-09-12_1000]] · «Kwen 32B» (система) — похоже на существующий узел: [[Системы/Qwen 32B]]" in note
    assert "«он»" not in note, "мусор — не кандидат для человека"
    unlinked = (tmp_path / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "узел не создан: Системы/Kwen 32B: похоже на существующий узел — Системы/Qwen 32B" in unlinked
    assert "узел не создан: Системы/он: не имя" in unlinked
    g.apply_entities(graph, ents, "Встречи/2026-09-12_1000")             # повтор обработки встречи
    assert (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8").count("\n- ") == 1, "ретрай не дублирует строку"
    assert verdict("Kwen 32B")[0] == "near", "файл _Кандидаты.md в корне графа сканами не считается узлом"
    # строка заметки встречи: без фантомной ссылки, с кандидатами; обычная сущность — ссылкой;
    # ключ held — папка и имя: одно имя в двух типах не затирает друг друга (DS M5 / GLM M4)
    assert g.entity_line(graph, {"имя": "Kwen 32B", "тип": "система", "суть": "модель"}, held) == \
        "- Kwen 32B (система) — модель _(узел не создан: похоже на существующий узел: [[Системы/Qwen 32B|Qwen 32B]])_"
    assert g.entity_line(graph, {"имя": "он", "тип": "система", "суть": ""}, held) == "- он (система) —  _(узел не создан: не имя)_"
    assert g.entity_line(graph, {"имя": "Qwen 32B", "тип": "система", "суть": "модель"}, held) == \
        "- [[Системы/Qwen 32B|Qwen 32B]] (система) — модель"
    assert g.entity_line(graph, {"имя": "Kwen 32B", "тип": "модель", "суть": "модель"}, held) == \
        "- [[Модели/Kwen 32B|Kwen 32B]] (модель) — модель", "тот же текст другим типом — не в held этой папки"


def test_description_supersede_edges(tmp_path):
    """Края №194 по кругам 1–2 (#539): даты, короткие описания, frontmatter с
    комментарием и с «## » в шапке, CRLF, ссылка без штампа, «## Встречи-архив»,
    ё/е, прочерки, журнал после записи."""
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    # «## …» внутри YAML-шапки — не раздел: служебная строка и хроника идут в тело (GLM r2)
    yaml_head = graph / "Люди" / "Шапка2.md"
    yaml_head.write_text("---\ntype: person\n## кто это\n---\n# Шапка2\nаналитик\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n",
                         encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Шапка2", "person", "директор по данным", "Встречи/2026-09-01_1000", "")
    t = yaml_head.read_text(encoding="utf-8")
    assert t.startswith("---\ntype: person\n## кто это\n---\n# Шапка2\nдиректор по данным\n\n_(последнее упоминание: 2026-09-01)_\n\n## Хроника\n"), t
    # прочерки любого вида — не описание: ни в старом узле (DS r2), ни в новом (DS r3)
    dash = graph / "Люди" / "Прочерк.md"
    dash.write_text("# Прочерк\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Прочерк", "person", "–", "Встречи/2026-09-01_1000", "")
    assert not dash.read_text(encoding="utf-8").startswith("# Прочерк\n–")
    g.upsert_entity(graph, "Люди", "Новый", "person", "–", "Встречи/2026-09-01_1000", "")
    fresh = (graph / "Люди" / "Новый.md").read_text(encoding="utf-8")
    assert "# Новый\n\n_(последнее упоминание: 2026-09-01)_\n" in fresh and "–" not in fresh, fresh
    g.upsert_entity(graph, "Люди", "Новый", "person", "директор", "Встречи/2026-09-02_1000", "")
    assert "## Хроника" not in (graph / "Люди" / "Новый.md").read_text(encoding="utf-8"), "первое описание — не вытеснение"
    for i, dash_char in enumerate("−―‒-"):   # минус, horizontal bar, figure dash, дефис (Minor DS r4)
        g.upsert_entity(graph, "Люди", f"Тире{i}", "person", dash_char, "Встречи/2026-09-01_1000", "")
        assert f"# Тире{i}\n\n_(последнее" in (graph / "Люди" / f"Тире{i}.md").read_text(encoding="utf-8"), dash_char
    # третий путь создания — узел по цели заглушки: тот же формат без описания (Minor DS r4)
    (graph / "Люди" / "Заглушка.md").write_text("# Заглушка → [[Люди/Канон]]\n\nДубль. Смерджен\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Заглушка", "person", "—", "Встречи/2026-09-01_1000", "")
    canon = (graph / "Люди" / "Канон.md").read_text(encoding="utf-8")
    assert "# Канон\n\n_(последнее упоминание: 2026-09-01)_\n\n## Встречи\n" in canon, canon
    # узел из одного заголовка: ровно одна пустая строка перед служебной (GLM r3)
    bare = graph / "Люди" / "Голый.md"
    bare.write_text("# Голый\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Голый", "person", "аналитик", "Встречи/2026-09-01_1000", "")
    assert bare.read_text(encoding="utf-8").startswith("# Голый\nаналитик\n\n_(последнее упоминание: 2026-09-01)_\n\n## Встречи\n")
    # журнал графа пишется после записи узла и различает вытеснение и пересказ (DS r3 Minor, критика GLM)
    monkeypatch_root = graph.parent / "data"
    orig_root = g.ROOT
    g.ROOT = monkeypatch_root
    try:
        g.upsert_entity(graph, "Люди", "Журнал", "person", "аналитик", "Встречи/2026-09-01_1000", "")
        g.upsert_entity(graph, "Люди", "Журнал", "person", "директор по данным", "Встречи/2026-09-02_1000", "")
        g.upsert_entity(graph, "Люди", "Журнал", "person", "по данным директор", "Встречи/2026-09-03_1000", "")
    finally:
        g.ROOT = orig_root
    journal = (monkeypatch_root / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "описание вытеснено: Журнал: «аналитик» → «директор по данным»" in journal, journal
    assert "описание — пересказ: Журнал: «по данным директор» ≈ «директор по данным»" in journal, journal
    # смена даты — единственное содержание обновления, и это новый факт (GLM)
    g.upsert_entity(graph, "Люди", "Отпуск", "person", "в отпуске до 2026-09-20", "Встречи/2026-09-01_1000", "")
    g.upsert_entity(graph, "Люди", "Отпуск", "person", "в отпуске до 2026-09-27", "Встречи/2026-09-02_1000",
                    "обсудили отпуск")
    t = (graph / "Люди" / "Отпуск.md").read_text(encoding="utf-8")
    assert "# Отпуск\nв отпуске до 2026-09-27\n" in t and "было: «в отпуске до 2026-09-20»" in t
    # короткое описание без значимых слов не замораживает узел (DS I3)
    qa = graph / "Люди" / "QA.md"
    qa.write_text("# QA\nQA\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "QA", "person", "руководитель QA-направления", "Встречи/2026-09-01_1000", "")
    t = qa.read_text(encoding="utf-8")
    assert "# QA\nруководитель QA-направления\n" in t and "было: «QA»" in t
    # служебная строка встаёт после описания, ПЕРЕД первым разделом — не между хроникой и встречами
    assert "\n\n_(последнее упоминание: 2026-09-01)_\n\n## Хроника\n" in t, t
    # комментарий «# …» в YAML-шапке — не заголовок узла (DS/GLM Minor)
    fm = graph / "Люди" / "Шапка.md"
    fm.write_text("---\n# note\ntype: person\n---\n# Шапка\nаналитик\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n",
                  encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Шапка", "person", "директор по данным", "Встречи/2026-09-01_1000", "")
    t = fm.read_text(encoding="utf-8")
    assert t.startswith("---\n# note\ntype: person\n---\n# Шапка\nдиректор по данным\n") and "было: «аналитик»" in t
    # CRLF-узел не получает смешанных концов строк (GLM Minor)
    crlf = graph / "Люди" / "Окна.md"
    crlf.write_bytes("# Окна\r\nаналитик\r\n\r\n## Встречи\r\n- [[Встречи/2026-01-01_1000]]\r\n".encode("utf-8"))
    g.upsert_entity(graph, "Люди", "Окна", "person", "директор по данным", "Встречи/2026-09-01_1000", "")
    raw = crlf.read_bytes().decode("utf-8")
    # read_text сводит CRLF к LF ещё до правки — узел пишется однородно, без смеси концов строк
    assert "\r" not in raw and "# Окна\nдиректор по данным\n\n" in raw and "было: «аналитик»" in raw, raw
    assert raw.index("## Хроника") < raw.index("## Встречи")
    # ссылка без штампа — строка хроники без даты (корректная деградация)
    nd = graph / "Люди" / "Безштампа.md"
    nd.write_text("# Безштампа\nаналитик\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Безштампа", "person", "директор по данным", "Встречи/заметки", "")
    assert "стало: «директор по данным»)_" in nd.read_text(encoding="utf-8")
    # «## Встречи-архив» выше настоящего раздела — хроника встаёт перед «## Встречи» (DS Minor)
    arc = graph / "Люди" / "Архивный.md"
    arc.write_text("# Архивный\nаналитик\n\n## Встречи-архив\n- старое\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n",
                   encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Архивный", "person", "директор по данным", "Встречи/2026-09-01_1000", "")
    t = arc.read_text(encoding="utf-8")
    assert t.index("## Встречи-архив") < t.index("## Хроника") < t.index("\n## Встречи\n")
    # ё/е — одно слово: «ведёт»/«ведет» не рождают строку хроники (DS Minor)
    yo = graph / "Люди" / "Ё.md"
    yo.write_text("# Ё\nведёт отчётность отдела\n\n## Встречи\n- [[Встречи/2026-01-01_1000]]\n", encoding="utf-8")
    g.upsert_entity(graph, "Люди", "Ё", "person", "ведет отчетность отдела", "Встречи/2026-09-01_1000", "")
    assert "## Хроника" not in yo.read_text(encoding="utf-8")
    # дайджест узла читает и хронику, и встречи — «## Встречи» после «## Хроника» сбор не обрывает
    import graph_nodes
    digest = graph_nodes._digest((graph / "Люди" / "Отпуск.md").read_text(encoding="utf-8"), "2026")
    assert any("2026-09-20" in d for d in digest) and any("обсудили отпуск" in d for d in digest), digest


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


def test_brain_mark_counts_successful_posts_and_retry_sends_only_the_rest(tmp_path):
    """4xx/5xx у requests — не исключение: без raise_for_status отметка вставала
    при потерянных фактах (хвост 20.08, GLM). Обрыв после удачного POST при
    повторе досылал бы всё заново — дубль в памяти (GLM по #455): отметка —
    счётчик `n/всего`, повтор шлёт только остаток."""
    import graph_updater as g

    class Resp:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("500")

    sent, fail_from = [], [2]

    def post(url, json, timeout):
        if len(sent) + 1 >= fail_from[0]:
            return Resp(False)
        sent.append(json["text"])
        return Resp(True)

    mark = tmp_path / "brain_sent" / "2026-08-29_1200.txt"
    people = [{"имя": "Иван"}]
    args = ("2026-08-29_1200", "Планёрка", people, ["релиз"], ["ждём CI", "мёрж в пятницу"], mark)
    n = g.send_to_brain(*args, post=post)
    assert n == 1 and mark.read_text(encoding="utf-8").startswith("sent 1/3\n"), mark.read_text(encoding="utf-8")
    fail_from[0] = 99
    n = g.send_to_brain(*args, post=post)
    assert n == 2 and mark.read_text(encoding="utf-8").startswith("sent 3/3\n")
    assert [t[:7] for t in sent] == ["Встреча", "Решение", "Решение"], sent
    assert g.send_to_brain(*args, post=post) == 0
    for old in ("Старый формат: только заголовок\n", "3/5\n", "sent 3/3\nsha1:abc\n# Планёрка\n"):   # прежние форматы = всё ушло
        mark.write_text(old, encoding="utf-8")
        assert g.send_to_brain("2026-08-29_1200", "Планёрка", people, ["релиз"], ["а", "б", "в", "г"], mark, post=post) == 0
    assert len(sent) == 3
    # повтор обработки извлёк решения заново, в другом порядке и с новым: ушли
    # только новые, старые не дублируются (luna r2 по #455)
    mark.unlink()
    sent.clear()
    fail_from[0] = 3
    g.send_to_brain(*args, post=post)                       # шапка + «ждём CI», обрыв на втором решении
    assert mark.read_text(encoding="utf-8").startswith("sent 2/3\n")
    fail_from[0] = 99
    sent.clear()
    # темы тоже переизвлечены — шапка не уходит второй раз (DS r3)
    n = g.send_to_brain("2026-08-29_1200", "Планёрка", people, ["сроки", "релиз"], ["новое решение", "мёрж в пятницу", "ждём CI"], mark, post=post)
    assert n == 2 and [t.split(": ")[1] for t in sent] == ["новое решение", "мёрж в пятницу"], sent
    assert mark.read_text(encoding="utf-8").startswith("sent 4/4\n")
    # тему переименовали (brain /rename уже знает новую) — факты не «новые»;
    # одно решение дважды в списке — один факт (GLM r3, luna r3)
    sent.clear()
    assert g.send_to_brain("2026-08-29_1200", "Другая тема", people, ["релиз"], ["ждём CI", "ждём CI", "мёрж в пятницу"], mark, post=post) == 0
    # список короче, чем когда-либо ушло: счётчик по текущему списку, не «5/2» (GLM r3)
    assert g.send_to_brain("2026-08-29_1200", "Планёрка", people, ["релиз"], ["Z"], mark, post=post) == 1
    assert mark.read_text(encoding="utf-8").startswith("sent 2/2\n")
    mark.unlink()
    sent.clear()
    assert g.send_to_brain("2026-08-29_1200", "Планёрка", people, ["релиз"], ["X", "X"], mark, post=post) == 2 and len(sent) == 2


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
    crlf = graph / "Встречи" / "2026-08-02_1000.md"
    crlf.write_bytes("# Встреча\r\n- [[Люди/Собеседник 3]] сказал\r\n- вторая строка\r\n".encode("utf-8"))
    index_before = "# Люди\n- [[Люди/Собеседник 3|Собеседник 3]]\n"
    (graph / "Люди" / "_ЛЮДИ.md").write_text(index_before, encoding="utf-8")
    before = meeting.read_text(encoding="utf-8")
    p = mp.plan(graph)
    assert p["nodes"] == ["Собеседник 1 (Саша)", "Собеседник 3"] and p["links"] == 12, p   # + CRLF-файл + указатель
    assert p["manual"] == ["Таня (Собеседник 4)"], "имя + метка в скобках — ручное решение, не миграция"
    (graph / "Люди" / "Таня.md").write_text("# Таня\n", encoding="utf-8")
    (graph / "Люди" / "Таня Петрова.md").write_text("# Таня Петрова\n", encoding="utf-8")
    assert mp.manual_hints(graph, p["manual"]) == {"Таня (Собеседник 4)": ["Таня", "Таня Петрова (частично)"]}
    (graph / "Люди" / "Таня.md").unlink()
    (graph / "Люди" / "Таня Петрова.md").unlink()
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
    assert manifest["links"] == 12 and manifest["nodes"] == ["Собеседник 1 (Саша)", "Собеседник 3"]
    assert manifest["files"] == p["files"] and out["links"] == 12, "в описи — фактические счётчики"
    assert manifest["status"] == "applied" and manifest["leftovers"] == [] and "rollback" in manifest
    assert manifest["index_rebuilt"] is True
    assert (dest / "files" / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8") == index_before, "копия указателя как был"
    assert crlf.read_bytes() == "# Встреча\r\n- Собеседник 3 сказал\r\n- вторая строка\r\n".encode("utf-8"), "CRLF цел"
    assert manifest["kept_bare"] == {"Встречи/2026-08-01_1000.md": ["[[Собеседник 3]]"]}, "оставленная голая ссылка — в описи"
    assert manifest["fenced_links"] == {"Встречи/2026-08-01_1000.md": 1}
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
    # гейт живой встречи повторяется ПОСЛЕ замка: под настоящим data_root замок берётся,
    # и live() видит, что вход в замок уже случился (luna r2)
    (graph / "Встречи" / "2026-08-01_1000.md").write_text("- [[Люди/Собеседник 2]] сказал\n", encoding="utf-8")
    monkeypatch.setattr(mp.shutil, "copy2", __import__("shutil").copy2)
    data_root = tmp_path / "data2"
    (data_root / "logs").mkdir(parents=True)
    import charoite_paths
    lock_file = charoite_paths.graph_backups(graph, "cloud_backup", root=data_root).parent / "cloud.lock"
    seen = {}

    def live_after_lock() -> bool:
        seen["lock_exists"] = lock_file.exists()
        return True

    with pytest.raises(SystemExit):
        mp.apply(graph, tmp_path / "backup2", log=lambda *_: None, data_root=data_root, live=live_after_lock)
    assert seen == {"lock_exists": True}, "гейт вызван уже под замком"
    assert (graph / "Люди" / "Собеседник 2.md").exists() and "[[Люди/Собеседник 2]]" in (graph / "Встречи" / "2026-08-01_1000.md").read_text(encoding="utf-8")
    # partial-опись: объект в работе назван, счётчики фактические
    real_write = mp.safe_write.write_text

    def flaky(path, text, *a, **k):
        real_write(path, text, *a, **k)
        raise KeyboardInterrupt
    monkeypatch.setattr(mp.safe_write, "write_text", flaky)
    with pytest.raises(KeyboardInterrupt):
        mp.apply(graph, tmp_path / "backup3", log=lambda *_: None)
    dest3 = next((tmp_path / "backup3").iterdir())
    m3 = json.loads((dest3 / "manifest.json").read_text(encoding="utf-8"))
    assert m3["status"] == "partial" and m3["in_flight"]["file"] == "Встречи/2026-08-01_1000.md"
    assert m3["files_done"] == [] and m3["links"] == 0


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
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--apply", "--backup", str(tmp_path / "b")],
                       capture_output=True, text=True, cwd=root, check=False, env=env)
    assert r.returncode == 2 and "CHAROITE_ROOT" in r.stderr, "без явного корня данных — отказ (DS r2)"
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--apply", "--backup", str(graph / "копия"),
                        "--root", str(tmp_path / "любой")], capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 2 and "вне графа" in r.stderr, "копия внутри графа — до всех сторожей"
    assert (graph / "Люди" / "Собеседник 2.md").exists()
    r = subprocess.run([sys.executable, str(script), "--graph", str(graph), "--report", str(tmp_path / "plan.json")],
                       capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 0 and json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))["nodes"] == ["Собеседник 2"]


def test_placeholder_migration_waits_for_the_shared_graph_lock_and_reports_leftovers(tmp_path):
    """Замок cloud.lock занят соседом — миграция не пишет; leftovers видит
    папочную ссылку, пережившую прогон; CLI с --root доходит до конца (GLM r2)."""
    import file_locks
    import charoite_paths
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import migrate_placeholders as mp
    graph = tmp_path / "g"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Люди" / "Собеседник 2.md").write_text("# Собеседник 2\n", encoding="utf-8")
    (graph / "Встречи" / "2026-08-01_1000.md").write_text("- [[Люди/Собеседник 2]] сказал\n", encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / "logs").mkdir(parents=True)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_backups(graph, "cloud_backup", root=data_root).parent)
    mp.LOCK_WAIT = 0.2
    with file_locks.graph_lock(lock_dir, 0.1) as taken:
        assert taken
        with pytest.raises(SystemExit):
            mp.apply(graph, tmp_path / "backup", log=lambda *_: None, data_root=data_root)
    assert (graph / "Люди" / "Собеседник 2.md").exists(), "под чужим замком ничего не тронуто"
    # leftovers: ссылка с папкой на снятый ключ
    assert mp.leftovers(graph, {"собеседник 2"}) == ["Встречи/2026-08-01_1000.md: [[Люди/Собеседник 2]]"]
    # успешный CLI-путь с явным корнем данных
    root = pathlib.Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(root / "scripts" / "migrate_placeholders.py"), "--graph", str(graph),
                        "--apply", "--backup", str(tmp_path / "b2"), "--root", str(data_root)],
                       capture_output=True, text=True, cwd=root, check=False)
    assert r.returncode == 0, r.stderr + r.stdout
    assert not (graph / "Люди" / "Собеседник 2.md").exists() and "cloud.lock" in r.stdout
    assert "- Собеседник 2 сказал" in (graph / "Встречи" / "2026-08-01_1000.md").read_text(encoding="utf-8")



def test_doctor_names_files_that_do_not_decode_strictly(tmp_path):
    """№275. `read_notes` читает граф с ЗАМЕНОЙ нечитаемых байтов, поэтому
    испорченный файл выглядит целым: текст уже потерян, облачная ревизия его
    не тронет (правка с «U+FFFD» уходит в карантин, №263), а поиск и индексы
    разъедутся на этом месте. Доктор — справка человеку: он называет такие
    файлы и позицию байта, ничего не чиня."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "Работа"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Системы").mkdir()
    (graph / "Люди" / "Иван.md").write_text("# Иван\nцелый файл\n", encoding="utf-8")
    (graph / "Системы" / "Битая.md").write_bytes(
        "# Битая\nтело с ".encode("utf-8") + b"\xd0" + "байтом\n".encode("utf-8"))
    # авторский «U+FFFD» в валидном UTF-8 — не порча файла, доктор его не считает
    (graph / "Системы" / "Символ.md").write_text(
        "# Символ\nразбор знака � в выгрузке\n", encoding="utf-8")

    rep = graph_doctor.inspect(graph, examples=5)

    assert rep["not_utf8"] == 1, rep
    assert any("Системы/Битая.md" in x and "байт" in x for x in rep["examples"]["not_utf8"]), rep["examples"]
    assert not any("Символ" in x for x in rep["examples"]["not_utf8"]), "авторский символ — не порча"
    assert any("не в UTF-8" in w for w in rep["warnings"]), rep["warnings"]
    assert "не в UTF-8 1" in graph_doctor.summary(rep)


def test_doctor_stays_quiet_when_every_file_decodes(tmp_path):
    """Контроль: на чистом графе проверка молчит и лишнего прохода по диску
    не делает — кандидаты только там, где символ вообще появился."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "Работа"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "Иван.md").write_text("# Иван\nвсё в порядке\n", encoding="utf-8")

    rep = graph_doctor.inspect(graph, examples=5)
    assert rep["not_utf8"] == 0 and not rep["examples"]["not_utf8"], rep
    assert not any("UTF-8" in w for w in rep["warnings"]), rep["warnings"]


def test_doctor_does_not_nag_about_the_archive_but_still_counts_it(tmp_path):
    """№275, круг 3, DS Important 2. Архив — легаси: конвейер его не
    перечитывает и не чинит, а утренний бриф печатает ВСЕ предупреждения
    каждую ночь. Битый байт в архиве дал бы вечное ⚠️ и привыкание к нему.
    Считаем отдельно, кричим только по активным папкам — как у битых ссылок."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "Работа"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Встречи-архив").mkdir()
    (graph / "Люди" / "Иван.md").write_text("# Иван\nцелый\n", encoding="utf-8")
    (graph / "Встречи-архив" / "Старая.md").write_bytes(
        "# Старая\nтело ".encode("utf-8") + b"\xd0" + " байтом\n".encode("utf-8"))

    rep = graph_doctor.inspect(graph, examples=5)

    assert rep["not_utf8"] == 0 and rep["not_utf8_archive"] == 1, rep
    assert not any("UTF-8" in w for w in rep["warnings"]), rep["warnings"]
    assert "не в UTF-8 0 (архив 1)" in graph_doctor.summary(rep)
    # файл назван: счёт без списка — тупик, найти потерянный текст нечем
    assert any("Встречи-архив/Старая.md" in x and "байт" in x
               for x in rep["examples"]["not_utf8_archive"]), rep["examples"]


def test_held_entity_repeat_escalates(tmp_path, monkeypatch, capsys):
    """№236: та же отложенная пара во второй встрече. Опечатка с одним и тем же
    кандидатом и общим числом — псевдоним в узле-кандидате «по повтору», встреча
    ложится в узел сразу по РЕШЁННОМУ пути со следом в его «## Встречи»;
    настоящая неоднозначность — только счётчик; ретрай той же встречи в любой
    раскладке — не повтор. Круг 1 (DS/GLM): вердикт с папкой ТИПА видит псевдоним
    в чужой папке, успех записи — от гейта. Круг 2: адрес не перерешивается при
    тёзке (DS C1), псевдоним человека — не «по повтору» (DS I2), снятый человеком
    псевдоним машина не переклеивает (GLM I1), в шапку человека не пишем (GLM M2),
    строка с хвостом человека считается (DS I4), склейка — только по общему числу.
    Круг 3: снятый псевдоним — вето, не пауза на одну встречу (DS C1 / GLM I1),
    решённый путь без файла не создаёт узел (DS M1 / GLM M2), пометка «записан» —
    из значения гейта (DS M2), stdout различает склейку и псевдоним человека.
    Круг 4: пара — имя, причина и набор кандидатов (DS I1); «0 изменений» шапки
    — не «уже был» (DS I2); создание по решённому пути недостижимо (DS I3);
    перечень папок — из graph_nodes (DS I4). №286: след склейки — `auto_aliases:` в
    шапке узла, вето читается из узла и не зависит от журнала; строки журнала —
    события без состояния."""
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Модели").mkdir()
    node = graph / "Модели" / "Qwen 32B.md"
    node.write_text("# Qwen 32B\n\n## Встречи\n", encoding="utf-8")
    for stem in ("ИИ-агент", "ИИ_агент"):
        (graph / "Системы" / f"{stem}.md").write_text(f"# {stem}\n\n## Встречи\n", encoding="utf-8")
    monkeypatch.setattr(g, "ROOT", tmp_path)
    ents = [{"имя": "Kwen 32B", "тип": "система", "суть": "модель"},
            {"имя": "ИИ агент", "тип": "система", "суть": "агент"}]
    note_path = graph / g.CANDIDATES_NOTE
    unlinked = tmp_path / "logs" / "graph_unlinked.log"
    # встреча 1: оба отложены, псевдонимов нет, счётчика нет
    held1 = g.apply_entities(graph, ents, "Встречи/2026-09-14_1000")
    assert held1[("Системы", "Kwen 32B")] == ("near", ["Модели/Qwen 32B"])
    assert held1[("Системы", "ИИ агент")][0] == "ambiguous"
    assert "aliases" not in node.read_text(encoding="utf-8")
    note = note_path.read_text(encoding="utf-8")
    assert "повтор ×" not in note and note.count("\n- ") == 2
    g.apply_entities(graph, ents, "Встречи/2026-09-14_1000")            # ретрай той же встречи
    g.apply_entities(graph, [{"имя": "kwen  32b", "тип": "система", "суть": "модель"}], "Встречи/2026-09-14_1000")
    assert note_path.read_text(encoding="utf-8").count("\n- ") == 2, "ретрай в любой раскладке — не повтор (DS I3)"
    assert "aliases" not in node.read_text(encoding="utf-8")
    # встреча 2: опечатка получает псевдоним, встреча ложится в узел со следом; неоднозначность — счётчик
    held2 = g.apply_entities(graph, ents, "Встречи/2026-09-15_1100")
    assert held2[("Системы", "Kwen 32B")] == ("aliased", ["Модели/Qwen 32B"]), held2
    assert held2[("Системы", "ИИ агент")][0] == "ambiguous"
    text = node.read_text(encoding="utf-8")
    assert text.startswith('---\naliases: ["Kwen 32B"]\nauto_aliases: ["Kwen 32B"]\n---\n'), text
    assert "- [[Встречи/2026-09-15_1100]] — псевдоним «Kwen 32B» записан по повтору" in text
    assert "[[Встречи/2026-09-14_1000]]" not in text
    assert not (graph / "Системы" / "Kwen 32B.md").exists(), "фантомного узла нет"
    note = note_path.read_text(encoding="utf-8")
    assert "«Kwen 32B» (система) — похоже на существующий узел: [[Модели/Qwen 32B]] — повтор ×2 → псевдоним записан в [[Модели/Qwen 32B]] (по повтору, 2026-09-15)" in note
    assert "«ИИ агент» (система) — подходит нескольким узлам: [[Системы/ИИ-агент]], [[Системы/ИИ_агент]] — повтор ×2\n" in note
    for stem in ("ИИ-агент", "ИИ_агент"):
        assert "aliases" not in (graph / "Системы" / f"{stem}.md").read_text(encoding="utf-8"), "неоднозначность не склеиваем"
    assert g.entity_line(graph, ents[0], held2) == "- [[Модели/Qwen 32B|Kwen 32B]] (система) — модель _(псевдоним по повтору)_"
    assert (g.name_key("Kwen 32B"), "похоже на существующий узел", ("Модели/Qwen 32B",), "Встречи/2026-09-15_1100") \
        in g._held_pairs(note), "форма строки журнала: ключ, почему, кандидаты, встреча (DS M6); состояния в строке нет (№286)"
    assert "Системы/Kwen 32B → Модели/Qwen 32B (псевдоним по повтору)" in capsys.readouterr().out
    log = unlinked.read_text(encoding="utf-8")
    assert "псевдоним по повтору: Модели/Qwen 32B ← «Kwen 32B»" in log
    assert "узел не создан: Системы/Kwen 32B: похоже на существующий узел — Модели/Qwen 32B → лёг в Модели/Qwen 32B по псевдониму" in log
    # боевой путь: вердикт с папкой ТИПА видит псевдоним в чужой папке — отдельным словом (DS C1 / GLM C1, GLM К2)
    assert g.entity_node_verdict(graph, "Системы", "Kwen 32B") == ("resolved", ["Модели/Qwen 32B"])
    # встреча 3: ложится в узел по псевдониму штатно — обычной ссылкой, без пометки «по повтору» (DS I2)
    held3 = g.apply_entities(graph, ents, "Встречи/2026-09-16_1200")
    assert held3[("Системы", "Kwen 32B")] == ("by_alias", ["Модели/Qwen 32B"]), held3
    assert "[[Встречи/2026-09-16_1200]]" in node.read_text(encoding="utf-8")
    assert not (graph / "Системы" / "Kwen 32B.md").exists()
    assert note_path.read_text(encoding="utf-8").count("«Kwen 32B»") == 2, "третья встреча в кандидаты не пишет"
    assert g.entity_line(graph, ents[0], held3) == "- [[Модели/Qwen 32B|Kwen 32B]] (система) — модель"
    assert "Системы/Kwen 32B → Модели/Qwen 32B (по псевдониму узла)" in capsys.readouterr().out
    assert g.apply_entities(graph, ents, "Встречи/2026-09-15_1100")[("Системы", "Kwen 32B")][0] == "by_alias"
    # тёзка в папке типа: адрес решён вердиктом, писатель его не перерешивает (DS C1 круга 2)
    twin = graph / "Системы" / "Qwen 32B.md"
    twin.write_text("# Qwen 32B\n\n## Встречи\n", encoding="utf-8")
    g.apply_entities(graph, ents, "Встречи/2026-09-16_1300")
    assert "[[Встречи/2026-09-16_1300]]" in node.read_text(encoding="utf-8")
    assert "[[Встречи/2026-09-16_1300]]" not in twin.read_text(encoding="utf-8"), "встреча не уехала в тёзку"
    twin.unlink()
    # человек снял псевдоним, след auto_aliases: остался — вето читается из УЗЛА, не пауза:
    # машина не переклеивает ни через одну встречу, ни через две (GLM I1 круга 2;
    # DS C1 / GLM I1 круга 3; №286)
    node.write_text(node.read_text(encoding="utf-8").replace('aliases: ["Kwen 32B"]\n', "", 1), encoding="utf-8")
    assert node.read_text(encoding="utf-8").startswith('---\nauto_aliases: ["Kwen 32B"]\n---\n')
    assert g._alias_veto(graph, "Модели/Qwen 32B", "Kwen 32B", "") and g._alias_veto(graph, "Модели/Qwen 32B", "kwen  32b", "")
    held5 = g.apply_entities(graph, ents, "Встречи/2026-09-17_1000")
    assert held5[("Системы", "Kwen 32B")] == ("near", ["Модели/Qwen 32B"]), held5
    assert "- 2026-09-17 [[Встречи/2026-09-17_1000]] · «Kwen 32B» (система) — похоже на существующий узел: [[Модели/Qwen 32B]] — псевдоним снимал человек, машина не переклеивает\n" in note_path.read_text(encoding="utf-8")
    # повторов в журнале уже ≥2 — без вето склеилось бы; под вето — пометка, не склейка
    # (ассерт круга 1 по #576 стоял после чистки журнала и доказывал только repeat == 1 — DS M6)
    held6 = g.apply_entities(graph, ents, "Встречи/2026-09-18_1000")
    assert held6[("Системы", "Kwen 32B")] == ("near", ["Модели/Qwen 32B"]), held6
    assert not g.frontmatter.aliases(node.read_text(encoding="utf-8")), "снятый человеком псевдоним не вернулся и через две встречи (DS C1 круга 3)"
    assert "- 2026-09-18 [[Встречи/2026-09-18_1000]] · «Kwen 32B» (система) — похоже на существующий узел: [[Модели/Qwen 32B]] — псевдоним снимал человек, машина не переклеивает\n" in note_path.read_text(encoding="utf-8"), "под вето — пометка, не счётчик"
    assert "узел не создан: Системы/Kwen 32B: похоже на существующий узел — Модели/Qwen 32B — псевдоним снимал человек" in unlinked.read_text(encoding="utf-8")
    # вето не зависит от журнала: человек вычистил _Кандидаты.md — машина всё равно молчит
    # (критика DS круга 4: якорь в человеческом файле терялся при чистке)
    note_path.write_text("", encoding="utf-8")
    g.apply_entities(graph, ents, "Встречи/2026-09-19_1000")
    assert not g.frontmatter.aliases(node.read_text(encoding="utf-8"))
    assert "- 2026-09-19 [[Встречи/2026-09-19_1000]] · «Kwen 32B» (система) — похоже на существующий узел: [[Модели/Qwen 32B]] — псевдоним снимал человек, машина не переклеивает\n" in note_path.read_text(encoding="utf-8")
    # снять вето — убрать имя и из auto_aliases: (здесь — всю шапку); повторы по журналу уже
    # накоплены — склейка приходит следующей встречей (GLM I1 по #576: так и написано в доках)
    node.write_text(node.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")
    assert not g._alias_veto(graph, "Модели/Qwen 32B", "Kwen 32B", "Встречи/2026-09-20_1000")
    held7 = g.apply_entities(graph, ents, "Встречи/2026-09-20_1000")
    assert held7[("Системы", "Kwen 32B")] == ("aliased", ["Модели/Qwen 32B"]), held7
    assert node.read_text(encoding="utf-8").startswith('---\naliases: ["Kwen 32B"]\nauto_aliases: ["Kwen 32B"]\n---\n')
    node.write_text(node.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")   # снова снят целиком — для проверок ниже
    # решённый путь, которого нет на диске (параллельный писатель убрал узел между
    # вердиктом и записью) — событие, не новый узел с типом из разбора (DS M1 / GLM M2)
    gone = graph / "Модели" / "Нет такого.md"
    g.upsert_entity(graph, "Модели", "Нет такого", "система", "м", "Встречи/2026-09-16_1700", "", node=gone)
    assert not gone.exists()
    assert "узел по решённому пути исчез: Модели/Нет такого" in unlinked.read_text(encoding="utf-8")
    assert "узел по решённому пути исчез, встреча Встречи/2026-09-16_1700 не дописана" in capsys.readouterr().out
    # исчез в окне МЕЖДУ предчеком и ветками записи (параллельный писатель) — ветка
    # создания по решённому пути недостижима по построению (DS I3 круга 4)
    gone2 = graph / "Модели" / "Исчезающий.md"
    gone2.write_text("# Исчезающий\n\n## Встречи\n", encoding="utf-8")
    real_placeholder = g._is_placeholder
    monkeypatch.setattr(g, "_is_placeholder", lambda d: (gone2.unlink(), real_placeholder(d))[1])
    g.upsert_entity(graph, "Модели", "Исчезающий", "система", "м", "Встречи/2026-09-16_1800", "", node=gone2)
    monkeypatch.setattr(g, "_is_placeholder", real_placeholder)
    assert not gone2.exists(), "фантом с типом из разбора не воскрес"
    assert "узел по решённому пути исчез: Модели/Исчезающий" in unlinked.read_text(encoding="utf-8")
    # перечень папок — проекта (graph_nodes), сканы вердикта и канона его слушают (DS M5 / GLM M4; DS I4 круга 4)
    assert g.NODE_FOLDERS is g.graph_nodes.NODE_FOLDERS
    assert set(g._AUTO_ALIAS_FOLDERS) <= set(g.NODE_FOLDERS)
    assert "Люди" not in g._AUTO_ALIAS_FOLDERS and "People" not in g._AUTO_ALIAS_FOLDERS, "люди обоих графов — мимо автопсевдонимов"
    (graph / "People").mkdir()
    (graph / "People" / "Operator 42.md").write_text("# Operator 42\n\n## Meetings\n", encoding="utf-8")
    ent_p = [{"имя": "Operatr 42", "тип": "команда", "суть": "shift"}]
    g.apply_entities(graph, ent_p, "Встречи/2026-09-14_1900")
    assert g.apply_entities(graph, ent_p, "Встречи/2026-09-15_1900")[("Команды", "Operatr 42")] == ("near", ["People/Operator 42"])
    assert "aliases" not in (graph / "People" / "Operator 42.md").read_text(encoding="utf-8")
    assert "псевдоним по повтору не записан: People/Operator 42 ← «Operatr 42»: кандидат — узел человека" in unlinked.read_text(encoding="utf-8")
    (graph / "Проекты").mkdir()
    (graph / "Проекты" / "Витрина Х.md").write_text("# Витрина Х\n\n## Встречи\n", encoding="utf-8")
    assert g.entity_node_verdict(graph, "Системы", "Витрина Х") == ("new", []), "папка вне перечня для сканов не существует"
    monkeypatch.setattr(g, "NODE_FOLDERS", g.NODE_FOLDERS + ("Проекты",))
    assert g.entity_node_verdict(graph, "Системы", "Витрина Х") == ("existing", [])
    assert g.find_canonical(graph, "Витрина Х") == graph / "Проекты" / "Витрина Х.md"
    monkeypatch.setattr(g, "NODE_FOLDERS", g.graph_nodes.NODE_FOLDERS)
    # два ключ-равных псевдонима в двух ЧУЖИХ папках — не гадаем; в папке типа — перехватывает первым (#451)
    (graph / "Команды").mkdir()
    (graph / "Команды" / "Другой.md").write_text('---\naliases: ["Kwen 32B"]\n---\n# Другой\n', encoding="utf-8")
    (graph / "Модели" / "Третий.md").write_text('---\naliases: ["Kwen 32B"]\n---\n# Третий\n', encoding="utf-8")
    assert g.entity_node_verdict(graph, "Системы", "Kwen 32B") == ("ambiguous", ["Команды/Другой", "Модели/Третий"])
    (graph / "Системы" / "Свой.md").write_text('---\naliases: ["Kwen 32B"]\n---\n# Свой\n', encoding="utf-8")
    assert g.entity_node_verdict(graph, "Системы", "Kwen 32B") == ("existing", [])
    for f in ("Системы/Свой", "Команды/Другой", "Модели/Третий"):
        (graph / f"{f}.md").unlink()
    # повтор — по строкам, по ключу имени, без папки; строка с хвостом человека считается (DS I4)
    for stem in ("Реестр Витрин", "Реестр Витрон"):
        (graph / "Системы" / f"{stem}.md").write_text(f"# {stem}\n\n## Встречи\n", encoding="utf-8")
    ent_r = [{"имя": "Реестр Витрен", "тип": "система", "суть": "реестр"}]
    assert g.apply_entities(graph, ent_r, "Встречи/2026-09-14_1300")[("Системы", "Реестр Витрен")] == \
        ("near", ["Системы/Реестр Витрин", "Системы/Реестр Витрон"])
    note_path.write_text(note_path.read_text(encoding="utf-8").rstrip("\n") + " (проверено)\n", encoding="utf-8")
    held_r = g.apply_entities(graph, [dict(ent_r[0], тип="проект")], "Встречи/2026-09-15_1300")   # тип прыгнул — пара та же
    assert held_r[("Системы", "Реестр Витрен")] == ("near", ["Системы/Реестр Витрин", "Системы/Реестр Витрон"])
    note = note_path.read_text(encoding="utf-8")
    assert "«Реестр Витрен» (проект) — похоже на существующий узел: [[Системы/Реестр Витрин]], [[Системы/Реестр Витрон]] — повтор ×2\n" in note
    (graph / "Системы" / "Реестр Витрон.md").unlink()                    # один кандидат слили руками
    g.apply_entities(graph, ent_r, "Встречи/2026-09-16_1300")           # набор кандидатов сменился — другая пара, счёт заново
    note = note_path.read_text(encoding="utf-8")
    assert "- 2026-09-16 [[Встречи/2026-09-16_1300]] · «Реестр Витрен» (система) — похоже на существующий узел: [[Системы/Реестр Витрин]]\n" in note
    assert "псевдоним записан в [[Системы/Реестр Витрин]]" not in note, "склейка требует общего числа"
    assert "aliases" not in (graph / "Системы" / "Реестр Витрин.md").read_text(encoding="utf-8")
    assert g._safe_to_auto_alias("Kwen 32B", "Модели/Qwen 32B") and g._safe_to_auto_alias("Кэш 1494", "Системы/Кеш 1494")
    assert not g._safe_to_auto_alias("Препрод", "Системы/Препрот") and not g._safe_to_auto_alias("Реестр Витрен", "Системы/Реестр Витрин")
    # общее число есть, но набор кандидатов между повторами сменился — это другая
    # пара: ни склейки, ни счётчика (DS I1 круга 4)
    for stem in ("Кеш 77", "Кэшь 77"):
        (graph / "Системы" / f"{stem}.md").write_text(f"# {stem}\n\n## Встречи\n", encoding="utf-8")
    ent_c = [{"имя": "Кэш 77", "тип": "система", "суть": "кэш"}]
    assert g.apply_entities(graph, ent_c, "Встречи/2026-09-14_1400")[("Системы", "Кэш 77")] == \
        ("near", ["Системы/Кеш 77", "Системы/Кэшь 77"])
    (graph / "Системы" / "Кэшь 77.md").unlink()
    assert g.apply_entities(graph, ent_c, "Встречи/2026-09-15_1400")[("Системы", "Кэш 77")] == ("near", ["Системы/Кеш 77"])
    assert "«Кэш 77» (система) — похоже на существующий узел: [[Системы/Кеш 77]]\n" in note_path.read_text(encoding="utf-8")
    assert "aliases" not in (graph / "Системы" / "Кеш 77.md").read_text(encoding="utf-8"), "набор кандидатов сменился — не клеим"
    # автопсевдоним в узел человека не пишется, даже с общим числом (GLM M2)
    (graph / "Люди").mkdir()
    (graph / "Люди" / "Оператор 42.md").write_text("# Оператор 42\n\n## Встречи\n", encoding="utf-8")
    ent_h = [{"имя": "Оператр 42", "тип": "команда", "суть": "смена"}]
    g.apply_entities(graph, ent_h, "Встречи/2026-09-14_1600")
    held_h = g.apply_entities(graph, ent_h, "Встречи/2026-09-15_1600")
    assert held_h[("Команды", "Оператр 42")] == ("near", ["Люди/Оператор 42"])
    assert "aliases" not in (graph / "Люди" / "Оператор 42.md").read_text(encoding="utf-8")
    assert "псевдоним по повтору не записан: Люди/Оператор 42 ← «Оператр 42»: кандидат — узел человека" in unlinked.read_text(encoding="utf-8")
    # проигранная гонка записи (GLM C2 / DS C2): write_text узла отвечает False дважды → LostRace → «не записан»
    (graph / "Системы" / "Кеш 1494.md").write_text("# Кеш 1494\n\n## Встречи\n", encoding="utf-8")
    ent_k = [{"имя": "Кэш 1494", "тип": "система", "суть": "кэш"}]
    g.apply_entities(graph, ent_k, "Встречи/2026-09-14_1500")
    real_write = g.safe_write.write_text
    target = graph / "Системы" / "Кеш 1494.md"
    monkeypatch.setattr(g.safe_write, "write_text",
                        lambda path, text, **kw: False if path == target else real_write(path, text, **kw))
    held_k = g.apply_entities(graph, ent_k, "Встречи/2026-09-15_1500")
    assert held_k[("Системы", "Кэш 1494")] == ("near", ["Системы/Кеш 1494"]), held_k
    assert "aliases" not in target.read_text(encoding="utf-8")
    assert "«Кэш 1494» (система) — похоже на существующий узел: [[Системы/Кеш 1494]] — повтор ×2\n" in note_path.read_text(encoding="utf-8")
    assert "псевдоним по повтору не записан: Системы/Кеш 1494 ← «Кэш 1494»:" in unlinked.read_text(encoding="utf-8")
    monkeypatch.setattr(g.safe_write, "write_text", real_write)
    # не-UTF-8 байт в журнале кандидатов (конфликтная копия iCloud) встречу не роняет (GLM I4)
    note_path.write_bytes(b"# \xff\xfe\n")
    held_b = g.apply_entities(graph, ent_k, "Встречи/2026-09-16_1500")
    assert held_b[("Системы", "Кэш 1494")][0] == "near"
    assert "журнал кандидатов не прочитан" in unlinked.read_text(encoding="utf-8")
    # гейт ответил «0 изменений», а псевдоним в шапке есть: строка журнала не обещает
    # записи — она якорь вето, и врать ей нельзя (DS M2 круга 3)
    note_path.write_text("", encoding="utf-8")
    g.apply_entities(graph, ent_k, "Встречи/2026-09-17_1500")
    real_aliases = g.frontmatter.aliases
    monkeypatch.setattr(g.frontmatter, "aliases", lambda text, where="": ["Кэш 1494"])
    assert g._journal_held_entity(graph, "Системы", "Кэш 1494", "система", "near", ["Системы/Кеш 1494"],
                                  "Встречи/2026-09-18_1500") == (target, False)
    monkeypatch.setattr(g.frontmatter, "aliases", real_aliases)
    assert "auto_aliases" not in target.read_text(encoding="utf-8"), "псевдоним стоял до машины — следа машины нет"
    note = note_path.read_text(encoding="utf-8")
    assert "— повтор ×2 → лёг в [[Системы/Кеш 1494]] (псевдоним уже был)\n" in note
    assert "псевдоним записан" not in note
    assert "псевдоним по повтору: Системы/Кеш 1494 ← «Кэш 1494» (уже был)" in unlinked.read_text(encoding="utf-8")
    # «0 изменений» от НЕЗАКРЫТОЙ шапки — ничего не записано: отказ с причиной, а не
    # «лёг в узел» (DS I2 круга 4)
    broken = graph / "Системы" / "Кеш 555.md"
    broken.write_text("---\n# Кеш 555\n\n## Встречи\n", encoding="utf-8")
    ent_b = [{"имя": "Кэш 555", "тип": "система", "суть": "кэш"}]
    g.apply_entities(graph, ent_b, "Встречи/2026-09-17_1600")
    held_u = g.apply_entities(graph, ent_b, "Встречи/2026-09-18_1600")
    assert held_u[("Системы", "Кэш 555")] == ("near", ["Системы/Кеш 555"]), held_u
    assert broken.read_text(encoding="utf-8") == "---\n# Кеш 555\n\n## Встречи\n", "незакрытая шапка не тронута, встреча не легла"
    note = note_path.read_text(encoding="utf-8")
    assert "«Кэш 555» (система) — похоже на существующий узел: [[Системы/Кеш 555]] — повтор ×2\n" in note
    assert "Кеш 555]] (псевдоним уже был)" not in note
    assert "псевдоним по повтору не записан: Системы/Кеш 555 ← «Кэш 555»: шапка не приняла псевдоним" in unlinked.read_text(encoding="utf-8")
    # якорь на СТАРЫЙ адрес не ветоит новый: узел слили в другой (заглушка), вердикт
    # даёт новый адрес — это новая пара, счёт заново (DS I1 круга 4)
    g.apply_entities(graph, ents[:1], "Встречи/2026-09-24_1000")
    assert g.apply_entities(graph, ents[:1], "Встречи/2026-09-25_1000")[("Системы", "Kwen 32B")] == ("aliased", ["Модели/Qwen 32B"])
    assert "→ псевдоним записан в [[Модели/Qwen 32B]]" in note_path.read_text(encoding="utf-8")
    node.write_text("# Qwen 32B → [[Модели/Qwen-32B]]\n", encoding="utf-8")          # слили: шапка с псевдонимом ушла
    (graph / "Модели" / "Qwen-32B.md").write_text("# Qwen-32B\n\n## Встречи\n", encoding="utf-8")
    held_m = g.apply_entities(graph, ents[:1], "Встречи/2026-09-26_1000")
    assert held_m[("Системы", "Kwen 32B")] == ("near", ["Модели/Qwen-32B"]), held_m
    assert "- 2026-09-26 [[Встречи/2026-09-26_1000]] · «Kwen 32B» (система) — похоже на существующий узел: [[Модели/Qwen-32B]]\n" \
        in note_path.read_text(encoding="utf-8"), "новый адрес — новая пара: без вето и без счётчика"
    held_n = g.apply_entities(graph, ents[:1], "Встречи/2026-09-27_1000")
    assert held_n[("Системы", "Kwen 32B")] == ("aliased", ["Модели/Qwen-32B"]), held_n
    assert "aliases" in (graph / "Модели" / "Qwen-32B.md").read_text(encoding="utf-8")


def test_parallel_core_and_its_twin_node_point_at_each_other(tmp_path, monkeypatch):
    """№266: тема заведена узлом Системы/X (боевым писателем upsert_entity), потом
    названа ядром — Ядра/X и Системы/X получают по машинной строке «смотри также»
    под «## Связи»: раздел встаёт перед «## Архив хроники», ретрай не дублирует,
    смена канона ядра заменяет строку (одна на пару). Двойник — только по точному
    ключу имени, без подстрок и без Людей; два одноимённых узла — не гадаем. Пара
    целиком или никак: заглушка-ядро и нечитаемый двойник не дают ни одной строки.
    CRLF и «## Связи» внутри фенса цитаты не ломают вставку."""
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    (graph / "Ядра").mkdir()
    monkeypatch.setattr(g, "ROOT", tmp_path)
    g.upsert_entity(graph, "Системы", "Внеплановый бэкап", "система", "резервная копия вне графика",
                    "Встречи/2026-09-10_0900", "")
    twin = graph / "Системы" / "Внеплановый бэкап.md"
    twin.write_text(twin.read_text(encoding="utf-8") + "\n## Архив хроники\n- старое\n", encoding="utf-8")
    core_dict = {"имя": "Внеплановый бэкап", "статус": "идёт", "обновление": "старт"}
    g.upsert_core(graph, core_dict, "Встречи/2026-09-16_1000", "2026-09-16 10:00")
    core = graph / "Ядра" / "Внеплановый бэкап.md"
    ct, tt = core.read_text(encoding="utf-8"), twin.read_text(encoding="utf-8")
    assert "\n## Связи\n- смотри также [[Системы/Внеплановый бэкап]] — та же тема узлом другого типа _(авто, №266)_\n" in ct
    assert "## Встречи\n- [[Встречи/2026-09-10_0900]]\n\n## Связи\n- смотри также [[Ядра/Внеплановый бэкап]] — сквозная тема _(авто, №266)_\n\n## Архив хроники\n" in tt, tt
    g.upsert_core(graph, core_dict, "Встречи/2026-09-16_1000", "2026-09-16 10:00")          # ретрай
    g.upsert_core(graph, dict(core_dict, статус="готово"), "Встречи/2026-09-17_1000", "2026-09-17 10:00")
    assert core.read_text(encoding="utf-8").count("смотри также") == 1
    assert twin.read_text(encoding="utf-8").count("смотри также") == 1
    # ядро слили — канон сменился: строка двойника заменяется, а не дописывается (DS I2)
    (graph / "Ядра" / "Иное.md").write_text("# Иное\n\n## Статус\nидёт\n\n## Хроника\n", encoding="utf-8")
    core.write_text("# Внеплановый бэкап → [[Ядра/Иное]]\n\nДубль. Смерджен\n", encoding="utf-8")
    g.upsert_core(graph, core_dict, "Встречи/2026-09-18_1000", "2026-09-18 10:00")
    tt = twin.read_text(encoding="utf-8")
    assert tt.count("смотри также") == 1 and "смотри также [[Ядра/Иное]] — сквозная тема" in tt, tt
    assert "смотри также [[Системы/Внеплановый бэкап]]" in (graph / "Ядра" / "Иное.md").read_text(encoding="utf-8")
    # двойник — по точному ключу имени: подстрока не двойник (DS I3), человек не двойник (GLM I1),
    # два одноимённых узла разных папок — не гадаем
    (graph / "Люди").mkdir()
    (graph / "Команды").mkdir()
    (graph / "Системы" / "Миграция БД витрины.md").write_text("# Миграция БД витрины\n\n## Встречи\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Миграция БД", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1100", "2026-09-16 11:00")
    assert "смотри также" not in (graph / "Ядра" / "Миграция БД.md").read_text(encoding="utf-8")
    (graph / "Люди" / "Иванов.md").write_text("# Иванов\n\n## Встречи\n", encoding="utf-8")
    (graph / "Системы" / "Иванов.md").write_text("# Иванов\n\n## Встречи\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Иванов", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1130", "2026-09-16 11:30")
    assert "смотри также [[Системы/Иванов]]" in (graph / "Ядра" / "Иванов.md").read_text(encoding="utf-8")
    assert "смотри также" not in (graph / "Люди" / "Иванов.md").read_text(encoding="utf-8")
    (graph / "Команды" / "Иванов.md").write_text("# Иванов\n\n## Встречи\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Иванов-2", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1140", "2026-09-16 11:40")
    assert g._core_twin(graph, graph / "Ядра", "Иванов") is None, "два одноимённых узла — не гадаем"
    # заглушка-двойник ведёт к своему канону; заглушка-ЯДРО — пара не пишется ни с одной стороны (DS I1 / GLM I2)
    (graph / "Команды" / "Канон.md").write_text("# Канон\n\n## Встречи\n", encoding="utf-8")
    (graph / "Системы" / "Дубль.md").write_text("# Дубль → [[Команды/Канон]]\n\nДубль. Смерджен\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Дубль", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1200", "2026-09-16 12:00")
    assert "смотри также [[Команды/Канон]]" in (graph / "Ядра" / "Дубль.md").read_text(encoding="utf-8")
    assert "смотри также [[Ядра/Дубль]]" in (graph / "Команды" / "Канон.md").read_text(encoding="utf-8")
    assert "смотри также" not in (graph / "Системы" / "Дубль.md").read_text(encoding="utf-8")
    (graph / "Ядра" / "Оборв.md").write_text("# Оборв → [[Ядра/Нет такого]]\n\nДубль. Смерджен\n", encoding="utf-8")
    (graph / "Системы" / "Оборв.md").write_text("# Оборв\n\n## Встречи\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Оборв", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1400", "2026-09-16 14:00")
    assert "смотри также" not in (graph / "Ядра" / "Оборв.md").read_text(encoding="utf-8")
    assert "смотри также" not in (graph / "Системы" / "Оборв.md").read_text(encoding="utf-8"), "ссылки на заглушку нет"
    log = (tmp_path / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "связь ядра и узла не ставится: Ядра/Оборв ↔ Системы/Оборв: Ядра/Оборв — заглушка-редирект" in log
    # двойник не читается — ядро записано, пара не ставится ни с одной стороны
    (graph / "Системы" / "Битый.md").write_bytes(b"# \xff\n")
    g.upsert_core(graph, {"имя": "Битый", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1500", "2026-09-16 15:00")
    assert (graph / "Ядра" / "Битый.md").exists()
    assert "смотри также" not in (graph / "Ядра" / "Битый.md").read_text(encoding="utf-8")
    assert "не ставится: Ядра/Битый ↔ Системы/Битый: Системы/Битый — не прочитан" in (tmp_path / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    # «## Связи» внутри фенса цитаты — не раздел, строка не лезет в цитату (DS M5). Окончания строк
    # здесь не проверяются: гейт rewrite_file читает текстом с нормализацией — CRLF-узел после
    # любой записи любым писателем проекта становится LF, это не свойство этой правки
    quoted = graph / "Системы" / "Окна.md"
    quoted.write_text("# Окна\n\n```\n## Связи\n- цитата\n```\n\n## Встречи\n", encoding="utf-8")
    g.upsert_core(graph, {"имя": "Окна", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1600", "2026-09-16 16:00")
    raw = quoted.read_text(encoding="utf-8")
    assert raw.count("## Связи") == 2 and raw.index("- смотри также") > raw.index("```\n\n## Встречи"), raw
    assert "## Связи\n- цитата\n```" in raw, "цитата не тронута"
    # ядро без двойника — связей не появляется
    g.upsert_core(graph, {"имя": "Одиночка", "статус": "идёт", "обновление": "старт"}, "Встречи/2026-09-16_1300", "2026-09-16 13:00")
    assert "смотри также" not in (graph / "Ядра" / "Одиночка.md").read_text(encoding="utf-8")


def test_machine_trace_lives_in_the_node_not_in_the_journal(tmp_path, monkeypatch):
    """№286. Псевдоним, который человек поставил сам и снял, — не вето: следа
    машины в узле нет, повторы считаются и склейка идёт как обычно, а со склейкой
    в узел ложится след `auto_aliases:`. Доктор называет пары, где след есть, а
    псевдонима нет — человек снял склейку машины."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    import graph_doctor
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    monkeypatch.setattr(g, "ROOT", tmp_path)
    node = graph / "Системы" / "Кеш 900.md"
    node.write_text('---\naliases: ["Кэш 900"]\n---\n# Кеш 900\n\n## Встречи\n', encoding="utf-8")
    ent = [{"имя": "Кэш 900", "тип": "система", "суть": "кэш"}]
    # псевдоним человека в папке типа — узел найден каноном (#451), встреча легла без записи в held
    assert ("Системы", "Кэш 900") not in g.apply_entities(graph, ent, "Встречи/2026-09-14_1000")
    assert "[[Встречи/2026-09-14_1000]]" in node.read_text(encoding="utf-8")
    # человек снял СВОЙ псевдоним: следа машины нет — не вето, счёт повторов и склейка по правилам
    node.write_text(node.read_text(encoding="utf-8").split("---\n", 2)[2], encoding="utf-8")
    assert not g._alias_veto(graph, "Системы/Кеш 900", "Кэш 900", "")
    assert g.apply_entities(graph, ent, "Встречи/2026-09-15_1000")[("Системы", "Кэш 900")] == ("near", ["Системы/Кеш 900"])
    assert g.apply_entities(graph, ent, "Встречи/2026-09-16_1000")[("Системы", "Кэш 900")] == ("aliased", ["Системы/Кеш 900"])
    text = node.read_text(encoding="utf-8")
    assert g.frontmatter.aliases(text) == ["Кэш 900"] and g.frontmatter.list_field(text, g.AUTO_ALIASES_KEY) == ["Кэш 900"]
    assert not g._alias_veto(graph, "Системы/Кеш 900", "Кэш 900", ""), "след при живом псевдониме — не вето"
    # доктор: след без псевдонима — «склейка машины, снятая человеком»; с псевдонимом — не тревога
    rep = graph_doctor.inspect(graph, examples=5)
    assert rep["alias_vetoes"] == 0 and not rep["examples"]["alias_vetoes"], rep
    node.write_text(text.replace('aliases: ["Кэш 900"]\n', "", 1), encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=5)
    assert rep["alias_vetoes"] == 1 and rep["examples"]["alias_vetoes"] == ["Системы/Кеш 900.md ← «Кэш 900»"], rep
    assert not any("склеек" in w for w in rep["warnings"]), "справка, не тревога: тревога толкает снести след уборкой (DS критика 2)"
    assert "вето склеек 1" in graph_doctor.summary(rep)
    # демо-граф на английском — те же папки политики (DS M5); два следа одного ключа — два вето (GLM M3)
    (graph / "Systems").mkdir()
    (graph / "Systems" / "Cache.md").write_text('---\nauto_aliases: ["Kash", "kash"]\n---\n# Cache\n', encoding="utf-8")
    rep = graph_doctor.inspect(graph, examples=5)
    assert rep["alias_vetoes"] == 3 and "Systems/Cache.md ← «kash»" in rep["examples"]["alias_vetoes"], rep["examples"]
    # нечитаемый узел-кандидат — не вето, но и не молча: событие «узел не прочитан» (DS M3 / GLM M1)
    unlinked = tmp_path / "logs" / "graph_unlinked.log"
    (graph / "Системы" / "Кеш 800.md").write_bytes(b"# \xff\xfe\n")
    assert not g._alias_veto(graph, "Системы/Кеш 800", "Кэш 800", "Встречи/2026-09-16_1500")
    assert "узел не прочитан: Системы/Кеш 800" in unlinked.read_text(encoding="utf-8")
    # псевдоним уже стоял (гонка: человек поставил между вердиктом и записью) — машина ничего не
    # приписала: в заметке обычная ссылка, не «по повтору» (DS I1)
    monkeypatch.setattr(g, "_auto_alias", lambda graph_, cand, name, link: (graph / "Системы" / "Кеш 900.md", False))
    ent9 = [{"имя": "Кэш 901", "тип": "система", "суть": "кэш"}]
    (graph / "Системы" / "Кеш 901.md").write_text("# Кеш 901\n\n## Встречи\n", encoding="utf-8")
    g.apply_entities(graph, ent9, "Встречи/2026-09-14_1700")
    held9 = g.apply_entities(graph, ent9, "Встречи/2026-09-15_1700")
    assert held9[("Системы", "Кэш 901")] == ("by_alias", ["Системы/Кеш 900"]), held9
    assert g.entity_line(graph, ent9[0], held9) == "- [[Системы/Кеш 900|Кэш 901]] (система) — кэш"
    monkeypatch.undo()
    monkeypatch.setattr(g, "ROOT", tmp_path)
    # журнал пишется через гейт: чужая правка между чтением и записью не затирается (GLM I2)
    note = graph / g.CANDIDATES_NOTE
    real_write = g.safe_write.write_text
    monkeypatch.setattr(g.safe_write, "write_text",
                        lambda path, text, **kw: False if path == note else real_write(path, text, **kw))
    before = note.read_text(encoding="utf-8")
    ent7 = [{"имя": "Кэш 770", "тип": "система", "суть": "кэш"}]
    (graph / "Системы" / "Кеш 770.md").write_text("# Кеш 770\n\n## Встречи\n", encoding="utf-8")
    assert g.apply_entities(graph, ent7, "Встречи/2026-09-14_1800")[("Системы", "Кэш 770")] == ("near", ["Системы/Кеш 770"])
    assert note.read_text(encoding="utf-8") == before, "проигранная гонка — файл не затёрт"
    assert "журнал кандидатов не записан: _Кандидаты.md: запись не состоялась" in unlinked.read_text(encoding="utf-8")
    monkeypatch.setattr(g.safe_write, "write_text", real_write)
    # ошибка самого преобразования шапки в _auto_alias — не «не записан», летит наверх (DS M4)
    monkeypatch.setattr(g.frontmatter, "with_list_field", lambda text, key, names: (_ for _ in ()).throw(ValueError("шапка сломана")))
    (graph / "Системы" / "Кеш 660.md").write_text("# Кеш 660\n\n## Встречи\n", encoding="utf-8")
    ent6 = [{"имя": "Кэш 660", "тип": "система", "суть": "кэш"}]
    g.apply_entities(graph, ent6, "Встречи/2026-09-14_1900")
    with pytest.raises(ValueError, match="шапка сломана"):
        g.apply_entities(graph, ent6, "Встречи/2026-09-15_1900")
    # след у одного из ДВУХ кандидатов — не вето, а обычный счётчик: склейка при двух
    # кандидатах невозможна и так, пометка «машина не переклеивает» здесь врала бы
    for stem, head in (("Кеш 700", '---\nauto_aliases: ["Кэш 700"]\n---\n'), ("Кэшь 700", "")):
        (graph / "Системы" / f"{stem}.md").write_text(f"{head}# {stem}\n\n## Встречи\n", encoding="utf-8")
    ent2 = [{"имя": "Кэш 700", "тип": "система", "суть": "кэш"}]
    assert g.apply_entities(graph, ent2, "Встречи/2026-09-14_1100")[("Системы", "Кэш 700")] == ("near", ["Системы/Кеш 700", "Системы/Кэшь 700"])
    g.apply_entities(graph, ent2, "Встречи/2026-09-15_1100")
    note = (graph / g.CANDIDATES_NOTE).read_text(encoding="utf-8")
    assert "«Кэш 700» (система) — похоже на существующий узел: [[Системы/Кеш 700]], [[Системы/Кэшь 700]] — повтор ×2\n" in note
    assert "Кэш 700» (система) — похоже на существующий узел: [[Системы/Кеш 700]], [[Системы/Кэшь 700]] — псевдоним снимал" not in note
    # след читается во всех формах поля, как и псевдонимы: блок и одиночная строка
    for head in ("auto_aliases:\n  - Кэш 900\n", "auto_aliases: Кэш 900\n"):
        assert g.frontmatter.list_field(f"---\n{head}---\n# X\n", g.AUTO_ALIASES_KEY) == ["Кэш 900"], head
    assert g.frontmatter.list_field("---\nauto_aliases: [\"Кэш 900\"]\n---\n", "aliases") == []


def test_entity_writer_goes_through_the_update_gate(tmp_path, monkeypatch, capsys):
    """№286 (DS I3 круга 4 по №236). Строка встречи в существующий узел идёт через
    rewrite_file: узел, сменившийся под рукой дважды, не перезаписывается
    прочитанным — событие с причиной-значением, встреча не дописана; проигранная
    первая попытка не дублирует события вытеснения; новый узел пишется с гейтом
    «файла не было» — появившийся в окне чужой файл дописывается, не затирается."""
    graph = tmp_path / "g"
    (graph / "Системы").mkdir(parents=True)
    monkeypatch.setattr(g, "ROOT", tmp_path)
    unlinked = tmp_path / "logs" / "graph_unlinked.log"
    node = graph / "Системы" / "Витрина.md"
    node.write_text("# Витрина\nстарое описание витрины\n\n## Встречи\n", encoding="utf-8")
    real_write = g.safe_write.write_text
    # сменился под рукой дважды: гейт отказывает обе попытки → LostRace.CHANGED
    monkeypatch.setattr(g.safe_write, "write_text",
                        lambda path, text, **kw: False if path == node else real_write(path, text, **kw))
    g.upsert_entity(graph, "Системы", "Витрина", "система", "совсем новое описание слоя данных", "Встречи/2026-09-16_1000", "")
    assert node.read_text(encoding="utf-8") == "# Витрина\nстарое описание витрины\n\n## Встречи\n", "чужая версия не затёрта"
    assert "встреча в узел не дописана: Системы/Витрина: сменились под рукой" in unlinked.read_text(encoding="utf-8")
    assert "«Витрина» — сменились под рукой, встреча Встречи/2026-09-16_1000 не дописана" in capsys.readouterr().out
    assert "описание вытеснено" not in unlinked.read_text(encoding="utf-8"), "журнал не обещает того, чего в узле нет"
    # первая попытка проиграна, вторая легла: запись одна, событие вытеснения одно
    calls = {"n": 0}
    def flaky(path, text, **kw):
        if path == node:
            calls["n"] += 1
            if calls["n"] == 1:
                return False
        return real_write(path, text, **kw)
    monkeypatch.setattr(g.safe_write, "write_text", flaky)
    g.upsert_entity(graph, "Системы", "Витрина", "система", "совсем новое описание слоя данных", "Встречи/2026-09-16_1100", "")
    text = node.read_text(encoding="utf-8")
    assert text.split("## Встречи", 1)[1].count("[[Встречи/2026-09-16_1100]]") == 1 and "совсем новое описание слоя данных" in text
    assert unlinked.read_text(encoding="utf-8").count("описание вытеснено") == 1, "событие — один раз, не на каждую попытку гейта"
    monkeypatch.setattr(g.safe_write, "write_text", real_write)
    # новый узел: чужой файл появился между проверкой и записью — дописываем в него, не затираем
    fresh = graph / "Системы" / "Новая.md"
    def appears(path, text, **kw):
        if path == fresh and kw.get("expect_absent") and not fresh.exists():
            fresh.write_text("# Новая\nчужое описание\n\n## Встречи\n", encoding="utf-8")
        return real_write(path, text, **kw)
    monkeypatch.setattr(g.safe_write, "write_text", appears)
    g.upsert_entity(graph, "Системы", "Новая", "система", "", "Встречи/2026-09-16_1200", "")
    text = fresh.read_text(encoding="utf-8")
    assert "чужое описание" in text and "[[Встречи/2026-09-16_1200]]" in text and "tags: [встречи, авто]" not in text
    monkeypatch.setattr(g.safe_write, "write_text", real_write)
    # узел стал нечитаемым под рукой — событие и сводка нечитаемых (GLM M2 по #576); ошибка
    # самого преобразования — не «не прочитан», она летит наверх (DS M4)
    def unreadable(path, transform, what):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(g.safe_write, "rewrite_file", unreadable)
    g._SKIPPED_NODES.clear()
    g.upsert_entity(graph, "Системы", "Витрина", "система", "", "Встречи/2026-09-16_1400", "")
    assert g._SKIPPED_NODES == ["Системы/Витрина"] and "встреча в узел не дописана: Системы/Витрина: 'utf-8'" in unlinked.read_text(encoding="utf-8")
    monkeypatch.setattr(g.safe_write, "rewrite_file", lambda path, transform, what: (_ for _ in ()).throw(ValueError("сломанное преобразование")))
    with pytest.raises(ValueError, match="сломанное преобразование"):
        g.upsert_entity(graph, "Системы", "Витрина", "система", "", "Встречи/2026-09-16_1500", "")
    monkeypatch.undo()
    monkeypatch.setattr(g, "ROOT", tmp_path)
    # обычный путь без гонок — как и был
    g.upsert_entity(graph, "Системы", "Ещё одна", "система", "описание", "Встречи/2026-09-16_1300", "")
    assert (graph / "Системы" / "Ещё одна.md").read_text(encoding="utf-8").startswith("---\ntype: система\n")
