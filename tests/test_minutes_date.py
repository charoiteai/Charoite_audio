"""Шапку минуток «Дата/время» пишет код по штампу встречи (№738).

Модель брала самую заметную дату речи: синтетика 2026-10-17_2000 получила
«Дата/время: 26 октября (ночь)» — день переезда, о котором говорили. На
минутках владельца (21.10, 1197 файлов) поле было в 652, верная дата — в 268;
пусто или «…» — 241, только время — 131, чужая дата или слово — 12.
`fact_check` такую дату не ловит: она есть в речи. Теперь поле модели снимается
до сверки, строку ставит `meeting_source.with_date` по `MeetingSource.began_at`.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import fact_check  # noqa: E402
import llm as llm_mod  # noqa: E402
import meeting_archive  # noqa: E402
import meeting_source  # noqa: E402
import meeting_stamp  # noqa: E402
import transcript_origin  # noqa: E402

SPEECH = "[20:00] Доброслав: переезд переносим на воскресенье, 26 октября\n"
MODEL_DOC = (
    "# Минутки встречи\n"
    "**Дата/время:** 26 октября (ночь) **Участники:** Доброслав, Заряна\n\n"
    "## Решения\n- **Перенос переезда** — на воскресенье\n"
)


def _finish(doc: str, began_at: str | None, speech: str = SPEECH) -> str:
    return meeting_source.finish_minutes(
        doc, meeting_source.live(speech, None, began_at=began_at),
        owner="Доброслав", participants={"Доброслав", "Заряна"}, lang="ru")


def test_the_model_date_is_replaced_by_the_stamp():
    out = _finish(MODEL_DOC, "2026-10-17 20:00")
    assert out.startswith("# Минутки встречи\n**Дата/время:** 2026-10-17 20:00\n**Участники:** Доброслав, Заряна\n")
    assert "26 октября (ночь)" not in out
    assert _finish(out, "2026-10-17 20:00") == out, "повтор — те же байты"


def test_the_entry_puts_the_stamp_where_the_model_field_was():
    """Поле снимается до сверки фактов, строка ставится после шагов — на то же
    место, а не под заголовок (out3 DS C2: один проход вместо двух границ)."""
    doc = "# Минутки встречи\n**Участники:** Доброслав, Заряна\n**Дата:** 26 октября\n\n## Решения\n- x\n"
    out = _finish(doc, "2026-10-17 20:00")
    assert out.startswith("# Минутки встречи\n**Участники:** Доброслав, Заряна\n**Дата/время:** 2026-10-17 20:00\n")
    assert _finish(out, "2026-10-17 20:00") == out


def test_the_stamp_date_never_reads_as_a_day_month_fact():
    """«2026-11-03» `fact_check` читает как 11.03 (день ≤ 12): шапка кода не
    должна давать сноску ни на первом проходе, ни на повторе (Sonnet C1 входа)."""
    assert "11.03" in fact_check._dates("**Дата/время:** 2026-11-03 14:00"), "предпосылка: формат ловится сверкой"
    once = _finish(MODEL_DOC, "2026-11-03 14:00")
    assert fact_check.FOOTNOTE_MARK not in once
    assert _finish(once, "2026-11-03 14:00") == once


def test_a_made_up_model_date_leaves_no_footnote_behind():
    """Дату модели, которой нет в речи, код заменяет — сноска про неё была бы
    про дату, которой в документе уже нет (DS I6 входа)."""
    doc = MODEL_DOC.replace("26 октября (ночь)", "5 ноября")
    out = _finish(doc, "2026-10-17 20:00")
    assert fact_check.FOOTNOTE_MARK not in out and "5 ноября" not in out


def test_without_a_start_the_document_is_left_as_is():
    assert _finish(MODEL_DOC, None).startswith(MODEL_DOC.split("\n\n")[0])


@pytest.mark.parametrize("lang,doc,want", [
    ("en", "# Meeting minutes\n**Date/time:** Oct 26 **Participants:** Ann\n## Topics\n",
     "# Meeting minutes\n**Date/time:** 2026-10-17 20:00\n**Participants:** Ann\n## Topics\n"),
    ("zh", "# 会议纪要\n**日期/时间：** 10月26日 **参会人：** 甲\n## 议题\n",
     "# 会议纪要\n**日期/时间：** 2026-10-17 20:00\n**参会人：** 甲\n## 议题\n"),
    ("zh", "# 会议纪要\n日期/时间: 10月26日\n## 议题\n", "# 会议纪要\n**日期/时间：** 2026-10-17 20:00\n## 议题\n"),
    ("ru", "Дата: 26 октября\n## Темы\n", "**Дата/время:** 2026-10-17 20:00\n## Темы\n"),
    ("ru", "# Минутки\n## Темы\n", "# Минутки\n**Дата/время:** 2026-10-17 20:00\n## Темы\n"),
])
def test_labels_of_three_languages_and_short_forms(lang, doc, want):
    assert meeting_source.with_date(doc, "2026-10-17 20:00", lang) == want


def test_the_value_never_crosses_a_line():
    """Без соседнего поля замена шла бы «до следующего `**…:**`» через разделы
    (S-I3 = DS-C2 входа): «Темы» и «Решения» обязаны остаться."""
    doc = "# Минутки\n**Дата/время:** 26 октября\n\n## Темы\n- **Риск:** лицензия\n## Решения\n- **x:** y\n"
    out = meeting_source.with_date(doc, "2026-10-17 20:00")
    assert out == doc.replace("26 октября", "2026-10-17 20:00")


@pytest.mark.parametrize("doc,want", [
    # пустое значение даты рядом с участниками: участники остаются (DS-C1 out1, опыт)
    ("**Дата/время:** **Участники:** Доброслав\n", "**Участники:** Доброслав\n"),
    # двоеточие снаружи жирного (Sonnet I1 out1)
    ("# М\n**Дата/время**: 26 октября (ночь)\n\n## Решения\n", "# М\n\n## Решения\n"),
    # шапка пунктами списка: поле даты снимается, второй даты нет (DS I2, Haiku I3)
    ("# М\n- **Дата/время:** 26 октября\n- **Участники:** А\n## Темы\n", "# М\n- **Участники:** А\n## Темы\n"),
    # соседнее поле не жирное, через «|» или «**М**:» (Sonnet I2, Haiku I2)
    ("# М\n**Дата/время:** 26 октября | Участники: Анна, Борис\n## Т\n", "# М\nУчастники: Анна, Борис\n## Т\n"),
    ("# М\n**Дата/время:** 26 октября **Участники**: Анна\n## Т\n", "# М\n**Участники**: Анна\n## Т\n"),
    # голая метка участников посреди строки (out2: Haiku C1 = Sonnet I1 = DS I3)
    ("# М\n**Дата/время:** 26 октября, участники: Анна, Борис\n## Т\n", "# М\nучастники: Анна, Борис\n## Т\n"),
    ("Дата/время: 26 октября; Participants: Ann\n## Т\n", "Participants: Ann\n## Т\n"),
    # преамбула над заголовком: комментарий ретро-прохода, проза модели (out2: DS C1 = Haiku C2;
    # корпус владельца — 116 файлов с двумя датами на прошлой правке)
    ("<!-- восстановлено -->\n# М\n**Дата/время:** 26 октября **Участники:** А\n## Т\n",
     "<!-- восстановлено -->\n# М\n**Участники:** А\n## Т\n"),
    ("Вот минутки:\n\n# М\n**Дата:** 26 октября\n## Т\n", "Вот минутки:\n\n# М\n## Т\n"),
    # заголовок черновика «##», заголовок прозой (корпус владельца, черновики 07–10.2026)
    ("## Черновик минуток\n\n**Дата:** [Указать дату]\n**Тема:** x\n", "## Черновик минуток\n\n**Тема:** x\n"),
    ("Протокол встречи\nДата: [Не указана]\nУчастники: А\n", "Протокол встречи\nУчастники: А\n"),
    # украшения метки, время после «|», «дата и время» (out2 Minor)
    ("# М\n***Дата/время:*** 26 октября\n## Т\n", "# М\n## Т\n"),
    ("# М\n📅 **Дата:** 26 октября\n## Т\n", "# М\n## Т\n"),
    ("# М\n*Дата:* 26 октября\n## Т\n", "# М\n## Т\n"),
    ("# М\n__Дата__: 26 октября\n## Т\n", "# М\n## Т\n"),
    ("# М\n**Дата/время:** 26 октября | 20:00\n## Т\n", "# М\n## Т\n"),
    ("# М\n**Дата и время:** 26 октября\n## Т\n", "# М\n## Т\n"),
])
def test_the_date_field_leaves_the_head_and_nothing_else(doc, want):
    assert meeting_source.without_date(doc) == want


@pytest.mark.parametrize("doc", [
    "**Meeting date:** 2026-10-17 **Участники:** Ann\n## Topics\n",   # своя метка шаблона (DS I1)
    "# М\nУчастники: Анна (Date: 5 ноября)\n## Т\n",                  # «date» внутри значения
    "# Х\nДата релиза: 10.11\n**Участники:** А\n",                    # своя метка шаблона
    "# М\n**Участники:** А\n\n**Темы:**\nДата: релиз 3 ноября\n",    # раздел жирным рвёт шапку
    "# М\n**Участники:** А\n### Решения\nДата: релиз 3 ноября\n",     # «###» рвёт шапку (DS M1)
    "# М\n**Участники:** А\n## Темы\n- Дата: релиз 3 ноября\n",       # под разделом — тело
    "# М\nИтог: ок\n**Решения:**\n- Дата: релиз\n",                    # раздел жирным без значения
    "# М\n**Участники:** А, **Дата:** 26 октября\n## Т\n",            # первое поле не дата — как на main (out3, решение А)
])
def test_other_fields_and_the_body_keep_their_bytes(doc):
    assert meeting_source.without_date(doc) == doc


@pytest.mark.parametrize("doc,want", [
    # заголовок с префиксом раздела, поле «Тема» над датой (out3: DS C1 = Sonnet I1/I2 = Haiku I2/I3)
    ("# Итоги квартала\n**Дата:** 26 октября\n**Участники:** А\n## Т\n",
     "# Итоги квартала\n**Дата/время:** X\n**Участники:** А\n## Т\n"),
    ("# Минутки\n**Тема:** Переезд\n**Дата:** 26 октября\n**Участники:** А\n## Т\n",
     "# Минутки\n**Тема:** Переезд\n**Дата/время:** X\n**Участники:** А\n## Т\n"),
    ("## Участники\n- Анна\n", "## Участники\n**Дата/время:** X\n- Анна\n"),
    # шум между заголовком и датой (out3 DS C2)
    ("# Минутки\n---\n**Дата:** 26 октября\n## Темы\n", "# Минутки\n---\n**Дата/время:** X\n## Темы\n"),
    ("# Минутки\n<!-- x -->\n**Дата:** 26 октября\n## Темы\n",
     "# Минутки\n<!-- x -->\n**Дата/время:** X\n## Темы\n"),
    # дата под разделом не шапка — как на main, где она единственная (решение А)
    ("# Минутки\n## Встреча\n**Дата:** 26 октября\n## Темы\n",
     "# Минутки\n**Дата/время:** X\n## Встреча\n**Дата:** 26 октября\n## Темы\n"),
    # проза перед полем без заголовка (out3 Haiku I4)
    ("Протокол встречи\nПроза раз\nПроза два\n**Дата:** 26 октября\n**Участники:** А\n",
     "Протокол встречи\nПроза раз\nПроза два\n**Дата/время:** X\n**Участники:** А\n"),
    # текст модели не теряется: кусок после «|», строка под пустой датой (out3 Haiku C1, DS I1)
    ("# М\n**Дата:** 26 октября | Место: офис\n## Т\n", "# М\n**Дата/время:** X\nМесто: офис\n## Т\n"),
    ("# М\n**Дата:**\nОбсудили 3 вопроса\n## Темы\n", "# М\n**Дата/время:** X\nОбсудили 3 вопроса\n## Темы\n"),
    ("# М\n**Дата/время:**\n26 октября\n## Т\n", "# М\n**Дата/время:** X\n26 октября\n## Т\n"),
    # пустой документ, преамбула без заголовка (out3 DS M2 = Haiku M9, Haiku M10)
    ("", "**Дата/время:** X\n"),
    ("Вот минутки:\n## Решения\n- А\n", "**Дата/время:** X\nВот минутки:\n## Решения\n- А\n"),
])
def test_the_date_takes_the_place_of_the_model_field_and_no_text_is_lost(doc, want):
    out = meeting_source.with_date(doc, "X")
    assert out == want
    assert meeting_source.with_date(out, "X") == out, "повтор — те же байты"


def test_a_document_without_sections_is_all_head():
    """Разделов нет — граница шапки не угадывается: строка, где первое поле
    дата, снимается и в «теле» (так модель пишет шапку пунктами; корпус: 0
    снятий ниже 12-й строки)."""
    doc = "# М\nВстреча прошла.\n- Дата: релиз 3 ноября\n"
    assert meeting_source.without_date(doc) == "# М\nВстреча прошла.\n"


def test_blank_lines_before_the_title_keep_the_date_under_it():
    out = meeting_source.with_date("\n# М\n**Участники:** А\n", "2026-10-17 20:00")
    assert out == "\n# М\n**Дата/время:** 2026-10-17 20:00\n**Участники:** А\n"


def test_only_the_head_is_searched():
    """Пункт тела с «Дата:» и слово с «date» внутри — не поле шапки."""
    doc = "# Минутки\n**Updated:** вчера\n## Темы\n- Дата: релиз 3 ноября\n**Дата/время:** в теле\n"
    out = meeting_source.with_date(doc, "2026-10-17 20:00")
    assert out == doc.replace("# Минутки\n", "# Минутки\n**Дата/время:** 2026-10-17 20:00\n")


def test_began_of_reads_the_stamp_of_any_main_file_name(tmp_path):
    assert meeting_source.began_of(tmp_path / "2026-10-17_2000.md") == "2026-10-17 20:00"
    assert meeting_source.began_of(tmp_path / "2026-10-17_200012-1.md") == "2026-10-17 20:00"
    assert meeting_source.began_of(tmp_path / "2026-10-17_2000_Миграция_Сапфир.md") == "2026-10-17 20:00"
    assert meeting_source.began_of(tmp_path / "2026-02-30_1200.md") is None, "невозможная дата — не падение"
    assert meeting_source.began_of(tmp_path / "заметка.md") is None


def test_an_imported_transcript_gets_the_date_without_time(tmp_path):
    """Штамп импорта — время файла записи (конец встречи), не начало: время
    было бы ложной точностью (Sonnet I5 и DS I5 входа)."""
    live = tmp_path / "2026-07-15_1158.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    assert transcript_origin.remember(live, "call.m4a", 10, transcript_origin.AUDIO)
    assert meeting_source.began_of(live) == "2026-07-15"
    assert meeting_source.of(live, "# Встреча\n").began_at == "2026-07-15"


def test_began_at_is_outside_the_source_hash(tmp_path):
    live = tmp_path / "2026-10-17_2000.md"
    live.write_text("# Встреча\n" + SPEECH, encoding="utf-8")
    src = meeting_source.of(live, live.read_text(encoding="utf-8"))
    assert src.began_at == "2026-10-17 20:00"
    assert src.sha() == meeting_source.live(src.speech, None, began_at=None).sha()


def test_started_at_on_an_impossible_date_is_none():
    assert meeting_stamp.started_at("2026-02-30_1200") is None
    assert meeting_stamp.meeting_start("2026-08-04_1203_Отчет").hour == 12


def test_the_manifest_start_follows_the_stamp_grammar(tmp_path):
    """Свой регексп манифеста давал null на штампе коллизии «-1» (DS C3 входа)."""
    folder = tmp_path / "2026-08-04 12-03 — Тема"
    folder.mkdir()
    assert meeting_archive.build_manifest(folder, "2026-08-04_120312-1", "Тема")["started_at"] == "2026-08-04T12:03:00"
    assert meeting_archive.build_manifest(folder, "2026-08-04_1203", "Тема")["started_at"] == "2026-08-04T12:03:00"


def _llm(lang: str) -> llm_mod.LLM:
    return llm_mod.LLM({"sufler": {"role": "роль", "language": lang},
                        "llm": {"base_url": "http://127.0.0.1:11434", "model": "m"}})


@pytest.mark.parametrize("lang,date_label,owner_word", [
    ("ru", "Дата/время", "кто внедряет"), ("en", "Date/time", "who implements"), ("zh", "日期/时间", "谁负责执行")])
def test_the_prompt_asks_neither_the_date_nor_who_implements_a_decision(lang, date_label, owner_word):
    """Дату модель не пишет; у решения нет исполнителя — «кто внедряет» ставил
    поручения в «Решения» и уже стоил саммари раздела решений (03.08)."""
    client = _llm(lang)
    seen: dict = {}

    def fake_stream(prompt, **kw):
        seen["prompt"] = prompt
        return iter(())

    client.stream = fake_stream
    list(client.minutes("[10:00] А: начнём\n"))
    assert date_label not in seen["prompt"] and owner_word not in seen["prompt"]

