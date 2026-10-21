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


def test_the_stamp_date_goes_first_and_the_model_text_stays():
    """Поле модели не снимается: четыре круга подряд снятие по границе шапки
    теряло текст (выход №738 out1–out4). Строка кода — первой под заголовком,
    байты модели под ней те же."""
    out = _finish(MODEL_DOC, "2026-10-17 20:00")
    assert out.startswith("# Минутки встречи\n**Дата/время:** 2026-10-17 20:00\n"
                          "**Дата/время:** 26 октября (ночь) **Участники:** Доброслав, Заряна\n")
    assert _finish(out, "2026-10-17 20:00") == out, "повтор — те же байты"


def test_the_stamp_date_never_reads_as_a_day_month_fact():
    """«2026-11-03» `fact_check` читает как 11.03 (день ≤ 12): шапка кода не
    должна давать сноску ни на первом проходе, ни на повторе (Sonnet C1 входа)."""
    assert "11.03" in fact_check._dates("**Дата/время:** 2026-11-03 14:00"), "предпосылка: формат ловится сверкой"
    doc = MODEL_DOC.replace("26 октября (ночь) ", "")
    once = _finish(doc, "2026-11-03 14:00")
    assert fact_check.FOOTNOTE_MARK not in once
    assert _finish(once, "2026-11-03 14:00") == once


def test_without_a_start_the_document_is_left_as_is():
    assert _finish(MODEL_DOC, None).startswith(MODEL_DOC.split("\n\n")[0])


@pytest.mark.parametrize("lang,doc,want", [
    ("en", "# Meeting minutes\n## Topics\n", "# Meeting minutes\n**Date/time:** X\n## Topics\n"),
    ("zh", "# 会议纪要\n## 议题\n", "# 会议纪要\n**日期/时间：** X\n## 议题\n"),
    ("ru", "# Минутки\n## Темы\n", "# Минутки\n**Дата/время:** X\n## Темы\n"),
    ("ru", "## Черновик минуток\n- a\n", "## Черновик минуток\n**Дата/время:** X\n- a\n"),
    # без заголовка — перед первой строкой содержания, комментарии над ней не трогаем
    ("ru", "Протокол встречи\nУчастники: А\n", "**Дата/время:** X\nПротокол встречи\nУчастники: А\n"),
    ("ru", "<!-- x -->\n# М\n", "<!-- x -->\n# М\n**Дата/время:** X\n"),
    ("ru", "\n# М\n**Участники:** А\n", "\n# М\n**Дата/время:** X\n**Участники:** А\n"),
    ("ru", "", "**Дата/время:** X\n"),
])
def test_the_line_goes_under_the_title(lang, doc, want):
    out = meeting_source.with_date(doc, "X", lang)
    assert out == want
    assert meeting_source.with_date(out, "X", lang) == out, "повтор — те же байты"


@pytest.mark.parametrize("doc", [
    # формы out1–out4, на которых снятие теряло текст: теперь байты модели те же
    "**Дата/время:** **Участники:** Доброслав\n",
    "## Решения\n- Дата: перенос на пятницу\n- Б\n",
    "# М\n**Участники:** А\n**Ход встречи**\n- Дата: релиз 3 ноября\n",
    "# М\n**Дата:** 26 октября, перенос на воскресенье | Место: офис\n## Т\n",
    "# М\n**Дата:**\nОбсудили 3 вопроса\n## Темы\n",
    "# Итоги квартала\n**Тема:** Переезд\n**Дата:** 26 октября\n",
])
def test_the_model_text_keeps_its_bytes(doc):
    out = meeting_source.with_date(doc, "X")
    assert out.replace("**Дата/время:** X\n", "", 1) == doc


def test_only_the_own_line_is_dropped_on_repeat():
    """Снимается ровно строка кода (как машинная оговорка `without_note`), а не
    поле модели с другой датой."""
    doc = "# М\n**Дата/время:** X\n**Дата/время:** 26 октября\n"
    assert meeting_source.with_date(doc, "X") == doc


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

