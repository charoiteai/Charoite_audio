"""Правки по аудиту зоны «граф и досье» (26.08, DeepSeek + GLM).

Каждый тест закрывает находку, которую головы описали механикой, а не
подозрением: цитата с обратным слэшем, тема без своего же ядра в промпте,
очередь досье, китайский провенанс.
"""
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SCRIPTS))

import dossier  # noqa: E402
import graph_updater  # noqa: E402
import tier3  # noqa: E402


def test_backslash_in_a_quote_does_not_break_the_merge():
    """Цитата — дословный срез стенограммы, в ней бывает «C:\\1С\\base».

    re.sub разбирает обратные слэши в СТРОКЕ ЗАМЕНЫ: такая пара роняет
    ночную ревизию (invalid group reference) или тихо вставляет группу
    посреди текста. Тот же класс уже чинили в graph_updater — в tier3 он
    оставался. Проверяем по AST: замена в _merge — либо lambda, либо наш
    собственный строковый литерал, но никогда не подставленный контент.
    """
    import ast

    tree = ast.parse((SRC / "tier3.py").read_text(encoding="utf-8"))
    merge = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_merge")
    subs = [n for n in ast.walk(merge) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "sub"]
    assert subs, "в _merge не осталось re.sub — проверь тест"
    for call in subs:
        repl = call.args[1]
        assert isinstance(repl, (ast.Lambda, ast.Constant)), (
            f"tier3.py:{repl.lineno}: замена re.sub считается шаблоном — "
            "контент с обратным слэшем сломает слияние")
        if isinstance(repl, ast.Constant):
            assert "\\" not in repl.value, "литерал замены со слэшем"

    # и сама механика бага, чтобы тест не выродился в проверку стиля
    status = r"миграция C:\1С\base готова"
    text = "## Статус\nстарый\n\n## Хроника\n"
    try:
        re.sub(r"## Статус\n.*?(?=\n## |\Z)", f"## Статус\n{status}\n\n",
               text, count=1, flags=re.S)
        raise AssertionError("образец бага перестал воспроизводиться — проверь тест")
    except re.error:
        pass
    out = re.sub(r"## Статус\n.*?(?=\n## |\Z)", lambda _: f"## Статус\n{status}\n\n",
                 text, count=1, flags=re.S)
    assert status in out


def test_the_theme_core_itself_reaches_the_prompt():
    """Хаб — первый источник темы: без него сводка пишется вслепую.

    build_prompt берёт первые MAX_SOURCES участников, а сортировка ставила
    ядро в конец — в кластере из 14+ встреч сама тема в промпт не попадала.
    """
    files = {"Тема": {"kind": "Ядра", "text": "## Статус\nидёт", "title": "Тема"}}
    for i in range(20):
        files[f"2026-08-{i + 1:02d}_1000"] = {"kind": "Встречи", "text": "[[Тема]]",
                                              "title": f"2026-08-{i + 1:02d}_1000"}
    back = {"Тема": {k for k in files if k != "Тема"}}
    members = dossier.clusters(files, back)["Тема"]
    assert members[0] == "Тема", "ядро темы должно идти первым"
    assert "Тема" in members[:dossier.MAX_SOURCES]


def test_unbuilt_dossiers_go_first_in_the_queue(tmp_path):
    """Иначе большие «несвежие» темы забирают все слоты ночи навсегда."""
    folder = tmp_path / "Досье"
    folder.mkdir()
    (folder / "Большая.md").write_text("есть", encoding="utf-8")
    cl = {"Большая": ["a", "b", "c", "d"], "Новая": ["e", "f", "g"]}
    order = sorted(cl.items(),
                   key=lambda kv: ((folder / f"{kv[0]}.md").exists(), -len(kv[1])))
    assert [t for t, _ in order] == ["Новая", "Большая"]


def test_full_rebuild_is_not_capped_by_the_nightly_limit():
    """«--full» обещало «пересобрать все темы», а упиралось в потолок 12."""
    src = (SCRIPTS / "nightly_dossier.py").read_text(encoding="utf-8")
    assert "if not full and built >= limit:" in src, \
        "полный прогон снова ограничен лимитом ночи"


def test_nightly_runs_a_full_dossier_pass_weekly():
    """У ревизии ядер воскресный полный прогон был, у досье — нет."""
    sh = (SCRIPTS / "nightly.sh").read_text(encoding="utf-8")
    assert "DOSSIER_MODE" in sh and "--full" in sh
    assert 'date +%u' in sh


def test_chinese_quote_is_verified_not_dropped():
    """В zh-режиме второй уровень сверки не работал вовсе: токенизация
    ловила только кириллицу и латиницу, q_words выходил пустым."""
    found = graph_updater._closest_span(
        "我们 决定 采用 新 方案", "10:15 德米特里: 我们 决定 采用 新 方案 ,下周开始")
    assert found, "близкий китайский пересказ должен находиться в стенограмме"
    assert not graph_updater._closest_span(
        "完全 不同 的 内容 无关", "10:15 德米特里: 我们 决定 采用 新 方案"), \
        "выдумка обязана отбрасываться и в zh"


def test_prefilter_threshold_is_documented_as_is():
    """Порог отбора кандидатов — 0.55, а не 0.60: замер по неверной
    константе едва не привёл к правке, ломающей поиск вложений."""
    assert tier3.EMB_PREFILTER == 0.55
    assert tier3.NEST_LO == 0.5 and tier3.DUP_T == 0.72


# ── круг-2 по PR #438 (DeepSeek): правки самих правок ────────────────────────

def test_a_heading_is_not_a_speaker():
    """«Итог:», «Решения:», «Присутствовали:» — не имена.

    Первая версия фикса брала за говорящего любую короткую фразу перед
    двоеточием, а недиаризованная стенограмма состоит из них наполовину:
    ложная атрибуция вернулась бы из нового источника. Теперь имя обязано
    совпасть с участником встречи.
    """
    quote = "переходим на новую CRM"
    for line in ("Итог: переходим на новую CRM",
                 "## Решения: переходим на новую CRM",
                 "Присутствовали: переходим на новую CRM"):
        out = graph_updater.core_anchor({"цитата": quote, "кто": "Ира"}, line,
                                        {"Дмитрий", "Пётр"})
        assert "«" + quote + "»" in out, "цитата обязана остаться"
        assert "Итог" not in out and "Решения" not in out, f"подпись из мусора: {out}"


def test_a_known_participant_is_recognised_in_any_short_form():
    """Стенограмма зовёт по имени, граф хранит полное — это один человек."""
    tr = "10:15 Пётр: решили брать новый вариант со следующей недели"
    out = graph_updater.core_anchor(
        {"цитата": "решили брать новый вариант", "кто": "Ира"}, tr,
        {"Пётр Иванов"})
    assert "Пётр Иванов" in out, f"участник не узнан: {out}"
    # чужого имени в графе нет — подписи тоже нет
    out = graph_updater.core_anchor(
        {"цитата": "решили брать новый вариант", "кто": "Ира"}, tr, {"Дмитрий"})
    assert "Пётр" not in out and "Ира" not in out


def test_a_pair_that_failed_nli_comes_back_by_name():
    """Отметка инкремента идёт вперёд, но упавшая пара не теряется.

    Держать отметку на месте — тупик: одна стабильно падающая пара
    заблокировала бы инкремент навсегда, и фокус рос бы каждую ночь.
    """
    src = (SCRIPTS / "tier3_cores.py").read_text(encoding="utf-8")
    assert "_save_stamp(graph, started, r.get(\"failed_names\"))" in src
    assert "def _pending(" in src and "#pending" in src
    assert "stuck = [n for n in _pending(graph) if n not in only]" in src
    tier3_src = (SRC / "tier3.py").read_text(encoding="utf-8")
    assert '"failed_names": set()' in tier3_src


def test_the_index_is_written_atomically_and_without_the_lock():
    """Иначе занятый соседом граф оставлял бы свежие досье невидимыми сутки."""
    src = (SRC / "dossier.py").read_text(encoding="utf-8")
    assert "tmp.replace(folder / INDEX_JSON)" in src
    assert "tmp_md.replace(folder / INDEX_MD)" in src
    night = (SCRIPTS / "nightly_dossier.py").read_text(encoding="utf-8")
    tail = night[night.index("if not dry and entries:"):]
    assert "_graph_lock" not in tail.split("return {")[0], "индекс снова под замком"


def test_links_are_read_only_from_the_surviving_file(tmp_path):
    """При дубле имени кластер не должен тянуть связи проигравшего файла."""
    graph = tmp_path / "граф"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Ядра").mkdir()
    (graph / "Встречи").mkdir()
    (graph / "Люди" / "CRM.md").write_text("[[Ядра/Миграция]]", encoding="utf-8")
    (graph / "Ядра" / "CRM.md").write_text("## Статус\nидёт", encoding="utf-8")
    (graph / "Ядра" / "Миграция.md").write_text("## Статус\nидёт", encoding="utf-8")
    files, back = dossier.scan(graph)
    assert files["CRM"]["kind"] == "Ядра", "ядро должно побеждать в дубле имени"
    assert "CRM" not in back.get("Миграция", set()), \
        "ссылка проигравшего файла попала в кластер"


def test_two_people_with_the_same_first_name_stay_unsigned():
    """Подписать наугад — та же ложная атрибуция, просто реже."""
    tr = "10:15 Пётр: решили брать новый вариант со следующей недели"
    core = {"цитата": "решили брать новый вариант", "кто": "Ира"}
    out = graph_updater.core_anchor(core, tr, {"Пётр Иванов", "Пётр Сидоров"})
    assert "Пётр" not in out, f"выбрал одного из тёзок: {out}"
    assert "«решили брать новый вариант»" in out
    # а точное совпадение двусмысленности не создаёт
    assert "Пётр" in graph_updater.core_anchor(core, tr, {"Пётр", "Пётр Иванов"})


# ── круг-3 по PR #438 (GLM 5.3 Flash) ────────────────────────────────────────

def test_the_anchor_reads_the_format_charoite_actually_writes(tmp_path):
    """Тест собирает стенограмму НАСТОЯЩИМ рендером, а не строкой из головы.

    Круг-2 проверял выдуманный инлайн «10:15 Пётр: реплика», которого продукт
    не пишет никогда. Реальный формат — «**Пётр** [10:15–10:18]:» отдельной
    строкой, и на нём фикс не находил говорящего НИ РАЗУ: заголовок кончается
    двоеточием, после которого ничего нет.
    """
    import transcript as tr_mod

    tr = tr_mod.Transcript(tmp_path)
    tr.set_participants(["Ольга", "Мария"])
    tr.add("решено брать новую CRM со следующей недели", speaker="Ольга")
    tr.add("подготовлю смету к пятнице", speaker="Мария")
    text = tr._render()
    assert "**Ольга**" in text, "рендер сменился — тест надо переписать под него"

    out = graph_updater.core_anchor(
        {"цитата": "решено брать новую CRM", "кто": "Ира"}, text,
        {"Ольга", "Мария"})
    assert "Ольга" in out and "«решено брать новую CRM»" in out, out
    assert re.search(r"\d{1,2}:\d{2}", out), f"время потерялось: {out}"


def test_only_this_meetings_participants_may_sign_a_quote():
    """Список говорящих — участники встречи, а не вся история проекта.

    Пока в него входили стемы всех файлов «Люди», ушедший три года назад
    сотрудник оставался допустимым говорящим навсегда, а узел «Команда
    разработки» подписывал любую строку «Команда: …».
    """
    src = (SRC / "graph_updater.py").read_text(encoding="utf-8")
    block = src[src.index("    speakers = {p["):src.index("    for c in cores:")]
    assert "Люди" not in block, "список говорящих снова берётся из графа"
    assert "Участники" in block, "шапка стенограммы больше не читается"


def test_a_longer_phrase_containing_a_name_is_not_that_person():
    """Обратное вложение делало говорящим любую строку с именем внутри."""
    out = graph_updater.core_anchor(
        {"цитата": "обсудили бюджет на квартал", "кто": ""},
        "**Сергей Иванов по проекту** [10:15]: обсудили бюджет на квартал",
        {"Иванов"})
    assert "Иванов" not in out, f"обратное вложение вернулось: {out}"
    # а короткое имя внутри полного — по-прежнему тот же человек
    assert "Пётр Иванов" in graph_updater.core_anchor(
        {"цитата": "обсудили бюджет на квартал", "кто": ""},
        "**Пётр** [10:15]:\nобсудили бюджет на квартал", {"Пётр Иванов"})


def test_a_merged_stub_does_not_steal_links_from_a_live_namesake(tmp_path):
    """Имя занято живым узлом — входящие принадлежат ему, а не заглушке."""
    graph = tmp_path / "граф"
    for d in ("Ядра", "Системы", "Встречи"):
        (graph / d).mkdir(parents=True)
    (graph / "Ядра" / "CRM.md").write_text(
        "⚠️ **Дубль. Смерджен Tier3-NLI.** → [[Ядра/Миграция]]\n", encoding="utf-8")
    (graph / "Системы" / "CRM.md").write_text("## Суть\nживая система\n", encoding="utf-8")
    (graph / "Ядра" / "Миграция.md").write_text("## Статус\nидёт\n", encoding="utf-8")
    (graph / "Встречи" / "2026-08-27_1000.md").write_text(
        "обсудили [[Системы/CRM|CRM]]\n", encoding="utf-8")
    files, back = dossier.scan(graph)
    assert "CRM" in files and files["CRM"]["kind"] == "Системы"
    assert "2026-08-27_1000" in back.get("CRM", set()), "живой узел лишился ссылки"
    assert "2026-08-27_1000" not in back.get("Миграция", set()), \
        "заглушка увела ссылку живого однофамильца"


def test_two_index_writers_cannot_mix_bytes():
    """Общий tmp давал двум прогонам атомарно установить битый json."""
    src = (SRC / "dossier.py").read_text(encoding="utf-8")
    assert 'f"{INDEX_JSON}.{os.getpid()}.tmp"' in src
    assert 'f"{INDEX_MD}.{os.getpid()}.tmp"' in src


# ── круг-4 по PR #438 (DeepSeek), дельта ─────────────────────────────────────

def test_an_unknown_speaker_does_not_borrow_the_previous_one():
    """Заголовок реплики — граница: чужие слова не подписываются соседом.

    Пока заголовок был «просто ещё одним кандидатом», цикл шёл выше и брал имя
    ПРЕДЫДУЩЕЙ реплики вместе с её временем. Гость или неопознанный
    «Собеседник» — штатный случай, не экзотика.
    """
    text = ("# Встреча\nУчастники (звучали в разговоре): Мария\n\n"
            "**Мария** [10:19–10:20]:\nподготовлю смету к пятнице\n\n"
            "**Гость** [10:21–10:22]:\nобсудили бюджет на квартал\n")
    out = graph_updater.core_anchor(
        {"цитата": "обсудили бюджет на квартал", "кто": ""}, text, {"Мария"})
    assert "Мария" not in out, f"подпись уехала к соседу: {out}"
    assert "10:19" not in out, f"и время тоже: {out}"
    assert "10:21" in out, f"своё время реплики потеряно: {out}"


def test_a_long_turn_still_finds_its_header():
    """Реплику ищем до заголовка, а не «пять строк вверх»."""
    text = "**Пётр** [10:15]:\n" + "\n".join(f"строка {i}" for i in range(1, 9)) \
        + "\nитоговая мысль про бюджет\n"
    out = graph_updater.core_anchor(
        {"цитата": "итоговая мысль про бюджет", "кто": ""}, text, {"Пётр"})
    assert "Пётр" in out, f"говорящий потерян в длинном блоке: {out}"


def test_a_time_inside_the_quote_is_not_the_time_of_the_turn():
    text = "**Пётр** [10:15]:\nобсудили смету в 14:30 и разошлись\n"
    out = graph_updater.core_anchor(
        {"цитата": "обсудили смету в 14:30", "кто": ""}, text, {"Пётр"})
    assert "10:15" in out and "14:30" not in out.split("«")[0], out


def test_the_participants_header_is_read_in_three_languages_without_roles():
    src = (SRC / "graph_updater.py").read_text(encoding="utf-8")
    assert "Participants|参会者" in src, "шапка снова только по-русски"
    assert "[(（].*?[)）]" in src, "роль в скобках снова попадёт в имя узла"


def test_a_live_node_stops_the_redirect_chain(tmp_path):
    """A→B→C, где B — и заглушка, и живой тёзка: ссылки A остаются у B."""
    graph = tmp_path / "граф"
    for d in ("Ядра", "Системы", "Встречи"):
        (graph / d).mkdir(parents=True)
    (graph / "Ядра" / "A.md").write_text(
        "⚠️ **Дубль. Смерджен Tier3-NLI.** → [[Ядра/B]]\n", encoding="utf-8")
    (graph / "Ядра" / "B.md").write_text(
        "⚠️ **Дубль. Смерджен Tier3-NLI.** → [[Ядра/C]]\n", encoding="utf-8")
    (graph / "Системы" / "B.md").write_text("## Суть\nживая система\n", encoding="utf-8")
    (graph / "Ядра" / "C.md").write_text("## Статус\nидёт\n", encoding="utf-8")
    (graph / "Встречи" / "2026-08-27_1100.md").write_text(
        "обсудили [[Ядра/A|A]]\n", encoding="utf-8")
    _, back = dossier.scan(graph)
    assert "2026-08-27_1100" in back.get("B", set()), "живое звено пропущено"
    assert "2026-08-27_1100" not in back.get("C", set()), "ссылка ушла мимо живого"


def test_stale_temp_files_of_the_index_are_swept(tmp_path):
    folder = tmp_path / "Досье"
    folder.mkdir()
    junk = folder / f"{dossier.INDEX_JSON}.99999.tmp"
    junk.write_text("обрывок", encoding="utf-8")
    import os as _os
    _os.utime(junk, (0, 0))     # мусор прошлой ночи, а не живого соседа
    dossier.write_index(folder, [])
    assert not junk.exists(), "мусор прошлого прогона остался навсегда"
    assert (folder / dossier.INDEX_JSON).exists()


def test_the_format_is_parsed_where_it_is_written(tmp_path):
    """Провенанс спрашивает transcript.parse_blocks, а не гадает по строкам.

    Правило №62: два Critical подряд в одной моей правке — знак, что чинить
    надо не заплаткой. Формат заголовка знает ровно одно место — рядом с
    рендером; парсер провенанса больше не самодельный.
    """
    import transcript as tr_mod

    tr = tr_mod.Transcript(tmp_path)
    tr.set_participants(["Ольга", "Мария"])
    tr.add("решено брать новую CRM со следующей недели", speaker="Ольга")
    tr.add("подготовлю смету к пятнице", speaker="Мария")
    text = tr._render()
    blocks = tr_mod.parse_blocks(text)
    assert [b["speaker"] for b in blocks] == ["Ольга", "Мария"], blocks
    for b in blocks:
        assert text[b["start"]:b["end"]].strip(), "тело реплики потеряно"

    src = (SRC / "graph_updater.py").read_text(encoding="utf-8")
    assert "_TURN_RE" not in src, "самодельный парсер формата вернулся"
    assert "parse_blocks" in src


def test_an_external_transcript_still_gets_its_provenance():
    """Чужой формат без блоков — инлайновый путь остаётся."""
    out = graph_updater.core_anchor(
        {"цитата": "решили брать новый вариант", "кто": ""},
        "10:15 Пётр: решили брать новый вариант со следующей недели",
        {"Пётр"})
    assert "Пётр" in out and "10:15" in out, out


# ── круг-5 по PR #438 (GLM) ──────────────────────────────────────────────────

def test_the_co_thinking_notes_are_not_somebodys_speech(tmp_path):
    """Заметки в конце пишет модель — человек их не произносил.

    Без границы последний блок поглощал хвост файла, и цитата из «💭 мысли»
    получала имя последнего говорящего вместе с его временем.
    """
    import transcript as tr_mod

    tr = tr_mod.Transcript(tmp_path)
    tr.set_participants(["Ольга", "Мария"])
    tr.add("посмотрим смету на следующей неделе", speaker="Ольга")
    tr.add("подготовлю смету к пятнице", speaker="Мария")
    tr.note("11:41 💭 команда выбрала нового поставщика и начинает интеграцию")
    text = tr._render()
    assert "Ко-мышление" in text, "рендер сменился — тест переписать"

    blocks = tr_mod.parse_blocks(text)
    assert blocks and blocks[-1]["end"] <= tr_mod.notes_start(text), \
        "последний блок снова поглощает заметки"
    quote = "команда выбрала нового поставщика"
    assert quote in text, "заметка не попала в стенограмму — тест бессмыслен"
    out = graph_updater.core_anchor({"цитата": quote, "кто": ""}, text,
                                    {"Ольга", "Мария"})
    assert "Мария" not in out and "Ольга" not in out, \
        f"заметка модели подписана человеком: {out}"


def test_a_neighbours_fresh_temp_file_is_left_alone(tmp_path):
    """Снести чужой tmp между write и replace — уронить соседу всю ночь."""
    folder = tmp_path / "Досье"
    folder.mkdir()
    fresh = folder / f"{dossier.INDEX_JSON}.4242.tmp"
    fresh.write_text("сосед пишет прямо сейчас", encoding="utf-8")
    old = folder / f"{dossier.INDEX_JSON}.777.tmp"
    old.write_text("мусор прошлой ночи", encoding="utf-8")
    import os as _os
    _os.utime(old, (0, 0))
    dossier.write_index(folder, [])
    assert fresh.exists(), "снесли файл живого прогона"
    assert not old.exists(), "мусор прошлой ночи остался"


def test_seconds_do_not_break_the_inline_path():
    out = graph_updater.core_anchor(
        {"цитата": "решили брать новый вариант", "кто": ""},
        "10:15:30 Пётр: решили брать новый вариант со следующей недели",
        {"Пётр"})
    assert "Пётр" in out, f"инлайн с секундами потерял говорящего: {out}"


def test_a_role_with_a_comma_does_not_split_a_participant():
    src = (SRC / "graph_updater.py").read_text(encoding="utf-8")
    block = src[src.index("        head = re.sub("):src.index("    for c in cores:")]
    assert 'head.split(",")' in block, "скобки снова снимаются после запятой"
