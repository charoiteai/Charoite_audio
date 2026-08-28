"""Гигиена графа и временных файлов — то, что портится тихо.

Ни один из этих дефектов не давал ошибки: узел просто оставался несвязанным,
провенанс подтверждал выдумку, а копия часовой записи лежала в /var/folders
до перезагрузки, о чём ретеншн приватности не знал.
"""
import pathlib
import subprocess
import sys

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
    assert tidy["люди"][0]["вклад"] == "про [[Системы/ Витрина]]".replace("/ ", "/ ")  # пробел склеен в один
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
