"""Гейт срока поручения в протоколе (№805).

На длинных встречах модель додумывает срок поручения (4 из 11 на текущем пути
минуток): «до 28.11» при «к 28-му» на встрече 26.10. Срок в окне «Задачи»
читается как факт, поэтому срок, которого речь не подтверждает, в строке
меняется на пометку «срок не из речи»; дата модели остаётся в сноске.
"""
import datetime
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_items  # noqa: E402
import fact_check  # noqa: E402
import meeting_source  # noqa: E402
import meeting_stamp  # noqa: E402
import task_line  # noqa: E402

DAY = datetime.date(2026, 10, 26)
WINDOW = json.loads((ROOT / "tests" / "fixtures" / "due_window.json").read_text(encoding="utf-8"))
MARK = task_line.DUE_UNHEARD_MARKS["ru"]


def _item(tail: str, who: str = "Участник А", what: str = "сдать отчёт") -> str:
    return f"- [ ] **{who}** — {what} — {tail}"


# ---- окно дня встречи: общая таблица с приложением -----------------------------

def test_grace_days_match_the_shared_table():
    """Люфт — одно число на Python и Swift: таблица держит его, XCTest сверяет
    `TaskDue.restatedGraceDays` с тем же полем."""
    assert fact_check.DUE_GRACE_DAYS == WINDOW["grace_days"]


@pytest.mark.parametrize("case", WINDOW["cases"], ids=lambda c: f"{c['meeting']}-{c['date']}")
def test_window_from_the_shared_table(case):
    meeting = datetime.date.fromisoformat(case["meeting"])
    dates = fact_check.due_dates(case["day"], meeting)
    assert sorted(d.isoformat() for d in dates) == case["dates"]
    assert sorted(fact_check.due_window(case["day"], meeting)) == sorted(case["window"])
    assert (case["date"] in fact_check.due_window(case["day"], meeting)) is case["in_window"]


def test_the_table_covers_month_and_year_borders():
    """Без случаев через границу месяца и года таблица не сверяет то, ради чего
    окно считается датами (in3 Sonnet I2)."""
    cases = WINDOW["cases"]
    assert any(c["dates"] and any(d[:4] != c["meeting"][:4] for d in c["dates"]) for c in cases), "год"
    assert any(c["in_window"] and c["date"][3:] != c["meeting"][5:7]
               and datetime.date.fromisoformat(c["dates"][0]) < datetime.date.fromisoformat(c["meeting"])
               for c in cases), "просрочка прошлого месяца"
    assert any(not c["in_window"] for c in cases) and any(c["in_window"] for c in cases)


def test_window_has_no_impossible_day():
    assert fact_check.due_window(31, datetime.date(2026, 2, 27)) == {"31.03"}
    assert fact_check.due_window(0, DAY) == set() and fact_check.due_window(32, DAY) == set()


# ---- дата подтверждена речью: одно решение на сноску и на гейт ----------------------

@pytest.mark.parametrize("doc,speech,day,heard", [
    ("до 28.10", "пришли отчёт к 28-му", DAY, True),
    ("до 28.11", "пришли отчёт к 28-му", DAY, False),             # додуманный месяц
    ("до 28.10", "пришли отчёт к 28-му", None, False),            # без дня встречи — как до №805
    ("до 28.10", "пришли к двадцать восьмому", DAY, True),        # порядковое словом
    ("до 21.10", "к двадцать первому", DAY, True),
    ("до 08.11", "к 28-му", DAY, False),                          # 8 не 28
    ("до 28.11", "к 28.11", DAY, True),                           # полная дата — как раньше
])
def test_date_heard_is_one_decision(doc, speech, day, heard):
    """Сноска (`unanchored`) и гейт (`due_heard`) судят дату одной функцией: иначе
    строка оставалась бы со сроком, а сноска называла бы его выдуманным."""
    date = fact_check._dates(doc).pop()
    assert (date not in fact_check.unanchored(doc, speech, day=day)) is heard
    assert fact_check.due_heard(doc, speech, day=day) is heard


def test_unanchored_without_day_keeps_the_old_contract():
    """Вызов без дня (`src/main.py`, старые пути) судит как раньше: дата без месяца
    дату документа не подтверждает."""
    assert fact_check.unanchored("до 28.10", "к 28-му") == ["28.10"]


# ---- похоже на срок -----------------------------------------------------------

@pytest.mark.parametrize("tail,due", [
    ("до 28.10", True), ("к пятнице", True), ("завтра", True), ("by Friday", True), ("下周五之前", True),
    ("через 2 недели", True),
    ("срок не указан", False), ("без срока", False), ("в процессе", False), ("tbd", False),
    ("пятница", True), ("срок: завтра", True),
    ("согласовать с юристами", False), ("на английском", False), ("по итогам аудита", False), ("в Jira", False),
    ("с учётом замечаний", False), ("срок: не указан", False), ("", False),
    # часть сроком начинается или вся — дата/время; цифра внутри дела срока не делает (out2, DS C1)
    ("28.11", True), ("28.11 включительно", True), ("17:00", True), ("в конце года", True), ("сегодня начать", True),
    ("комната 405", False), ("версия 2.1", False), ("за 2026 год", False), ("12 слайдов", False), ("周报", False),
    ("по TASK 3242 до 15.11", False),
])
def test_looks_like_due(tail, due):
    assert fact_check.looks_like_due(tail) is due


# ---- пункт поручения ----------------------------------------------------------

@pytest.mark.parametrize("tail,speech,kept", [
    ("до 28.10", "Участник А, сдай отчёт к 28-му.", True),
    ("до 28.11", "Участник А, сдай отчёт к 28-му.", False),
    ("к пятнице", "Участник А сдаст в пятницу.", True),
    ("к пятнице", "Участник А сдаст завтра.", False),
    ("к 16:00", "к 16 часам", True),
    ("к 17:00", "к 16 часам", False),
    ("срок не указан", "ни слова о сроке", True),
    ("согласовать с юристами", "ни слова", True),
])
def test_item(tail, speech, kept):
    line = _item(tail)
    out = action_items.check_deadline_item(line, speech, DAY)
    assert out == (line if kept else _item(MARK))


def test_item_keeps_the_pieces_suffix():
    line = _item("до 28.11") + " (куски 2, 5)"
    assert action_items.check_deadline_item(line, "к 28-му", DAY) == _item(MARK) + " (куски 2, 5)"


@pytest.mark.parametrize("line", [
    "- [x] **Участник А** — сдать отчёт — до 28.11",                  # статус решён человеком
    "- [ ] **Участник А** — сдать отчёт по версии 2.1 — комната 405",  # цифры дела — не срок
    "- [ ] **Участник А** — сдать отчёт — до 28.11 📅 2026-11-28",   # поле ставит человек
    "- [ ] **Участник А** — сдать отчёт — до 28.11 _(снято по сроку 24.09)_",
    "**Участник А** — сдать отчёт — до 28.11",                        # не пункт задачи
])
def test_item_left_alone(line):
    assert action_items.check_deadline_item(line, "ни слова о сроке", DAY) == line


@pytest.mark.parametrize("lang", sorted(task_line.DUE_UNHEARD_MARKS))
def test_item_mark_per_language_is_idempotent(lang):
    mark = task_line.DUE_UNHEARD_MARKS[lang]
    once = action_items.check_deadline_item(_item("до 28.11"), "к 28-му", DAY, lang=lang)
    assert once == _item(mark)
    assert action_items.check_deadline_item(once, "к 28-му", DAY, lang=lang) == once
    assert task_line.key(once) == task_line.key("- [ ] **Участник А** — сдать отчёт")


def test_mark_has_no_date_for_the_task_chip():
    """В пометке нет ДД.ММ — чип срока в «Задачах» (TaskDue.parse) не рисуется."""
    assert not fact_check._dates(MARK) and not any(ch.isdigit() for ch in MARK)


# ---- раздел поручений ---------------------------------------------------------

DOC = (
    "## Решения\n- перенести релиз — до 01.12 — по итогам\n\n"
    "## Поручения\n"
    + _item("до 28.11") + "\n"
    + _item("до 28.10", who="Участник Б") + "\n"
    + "\n## Риски\n- сорвать срок — до 05.12 — ещё\n"
)


def test_only_items_with_a_box():
    out = action_items.check_deadlines(DOC, "Участник Б, к 28-му.", DAY)
    assert out == DOC.replace(_item("до 28.11"), _item(MARK))


def test_item_under_any_heading():
    """Окно «Задачи» берёт любой «- [ ]» минуток, не только раздел поручений:
    срок под чужим заголовком тоже рисуется чипом (Haiku I3 out1)."""
    doc = "## Задачи\n" + _item("до 28.11") + "\n"
    assert action_items.check_deadlines(doc, "к пятнице", DAY) == "## Задачи\n" + _item(MARK) + "\n"


def test_empty_speech_changes_nothing():
    assert action_items.check_deadlines(DOC, "  ", DAY) == DOC


# ---- день встречи из имени файла ----------------------------------------------

@pytest.mark.parametrize("name,day", [
    ("t/2026-10-26_101500.md", DAY),                  # `.md` — stamp_of по .stem (in3 Haiku C1)
    ("2026-10-26_101500-2.md", DAY),                  # вторая встреча в ту же секунду
    ("2026-10-26_101500_Отчёт_по_задачам.md", DAY),   # после наката темы
    ("2026-09-02_1021.md", datetime.date(2026, 9, 2)),
    ("2026-10-26_101500_minutes.md", None),           # производная — не встреча
    ("2026-02-30_1200.md", None),
    ("заметка.md", None),
])
def test_day_of(name, day):
    assert meeting_stamp.day_of(pathlib.Path(name)) == day


def test_source_of_file_carries_the_day():
    text = "# Встреча\n[10:00] Участник А: сдай к 28-му\n"
    src = meeting_source.of(pathlib.Path("t/2026-10-26_101500.md"), text)
    assert src.day == DAY
    assert src.sha() == meeting_source.MeetingSource(src.speech, src.recording_note).sha(), "день не входит в хеш"


# ---- цепочка минуток ----------------------------------------------------------

def test_finish_minutes_marks_and_keeps_the_footnote():
    doc = "## Поручения\n" + _item("до 28.11") + "\n" + _item("до 28.10", who="Участник Б") + "\n"
    speech = "[10:00] Участник А: сдам к 28-му\n[10:01] Участник Б: и я к 28-му\n"
    src = meeting_source.live(speech, None, day=DAY)
    out = meeting_source.finish_minutes(doc, src, owner="Участник А",
                                        participants={"Участник А", "Участник Б"})
    assert _item(MARK) in out and _item("до 28.10", who="Участник Б") in out
    assert "28.11" in out.split(fact_check.FOOTNOTE_MARK)[1], "дата модели — в сноске"
    assert "28.10" not in out.split(fact_check.FOOTNOTE_MARK)[1], "дата в окне — не выдумка"
    again = meeting_source.finish_minutes(out, src, owner="Участник А",
                                          participants={"Участник А", "Участник Б"})
    assert again == out


def test_finish_minutes_checks_an_outsiders_deadline_before_the_mark():
    """Гейт стоит до `finalize_assignees`: пометка «не участник» снимает чекбокс,
    и срок поручения чужому иначе остался бы в тексте без проверки."""
    doc = "## Поручения\n" + _item("до 28.11", who="Посторонний") + "\n"
    src = meeting_source.live("[10:00] Участник А: к 28-му\n", None, day=DAY)
    out = meeting_source.finish_minutes(doc, src, owner="Участник А",
                                        participants={"Участник А", "Участник Б"})
    assert f"— {MARK}" in out and "— до 28.11" not in out.split(fact_check.FOOTNOTE_MARK)[0]


# ---- выходной круг out1 ---------------------------------------------------------

@pytest.mark.parametrize("tail", ["до 28.10 включительно", "до 28.10 (пятница)", "до 28 октября 2026 года"])
def test_confirmed_date_is_not_erased_by_a_word_next_to_it(tail):
    """Есть дата — решают даты: слово модели рядом с подтверждённой датой срок не
    стирает (DS C1 out1: срок пропадал и из строки, и из сноски)."""
    line = _item(tail)
    assert action_items.check_deadline_item(line, "Участник А, сдай отчёт к 28-му.", DAY) == line


@pytest.mark.parametrize("speech", [
    "сдам 28.11",                                   # полная дата — не день без месяца (DS I1)
    "у нас 28 человек",                             # голое число (Sonnet M5)
    "[10:28] Участник А: сдай отчёт",               # метка реплики
    "в 10:28 созвон",                               # время
])
def test_day_must_be_spoken_as_a_day(speech):
    assert fact_check.unanchored("до 28.10", speech, day=DAY) == ["28.10"]
    assert not fact_check.due_heard("до 28.10", speech, day=DAY)


@pytest.mark.parametrize("speech", ["к 28-му", "до 28", "28-го", "28 числа", "by the 28th", "28号之前",
                                    "by the twenty-eighth", "к 28\u2011го", "28–го", "二十八号之前"])
def test_day_spoken_forms(speech):
    assert fact_check.due_heard("до 28.10", speech, day=DAY)


@pytest.mark.parametrize("tail,speech,kept", [
    ("до 30.10", "сдай в пятницу", True),            # 26.10 — понедельник (Sonnet I3 out1)
    ("до 27.10", "сделаю завтра", True),
    ("до 28.10", "сделаю послезавтра", True),
    ("до 27.10", "сделаю послезавтра", False),
    ("до 31.10", "сдай в пятницу", False),           # суббота
    ("до 13.11", "сдай в пятницу", False),           # дальше двух недель
    ("через 2 недели", "сделаю через две недели", True),
    ("к 16:00", "к шестнадцати", True),
    ("в течение недели", "за неделю сделаю", True),
    ("до конца квартала", "до конца месяца", True),   # опоры нет — не судится (out3)
])
def test_retold_deadline(tail, speech, kept):
    line = _item(tail)
    assert (action_items.check_deadline_item(line, speech, DAY) == line) is kept


@pytest.mark.parametrize("line,marked", [
    ("- [ ] **Участник А** – отчёт – до 28.11", "- [ ] **Участник А** – отчёт – " + MARK),
    ("- [ ] **Участник А** - отчёт - до 28.11", "- [ ] **Участник А** - отчёт - " + MARK),
    ("  - [ ] **Участник А** - отчёт - до 28.11", "  - [ ] **Участник А** - отчёт - " + MARK),
    ("- [ ] **Участник А** — отчёт — до 28.11 — важно", "- [ ] **Участник А** — отчёт — " + MARK + " — важно"),
    ("1. [ ] **Участник А** — отчёт — до 28.11", "1. [ ] **Участник А** — отчёт — " + MARK),
])
def test_any_dash_and_any_part(line, marked):
    """Модель пишет тире любого начертания и приписки после срока (DS I3, Sonnet I1 out1)."""
    assert action_items.check_deadline_item(line, "к 28-му", DAY) == marked


def test_chinese_numeric_date_is_left_alone():
    """`_dates` китайскую дату цифрами не разбирает — подтвердить нечем, стирать нельзя (DS M5 out1)."""
    line = "- [ ] **参会者甲** — 准备报告 — 10月30日之前"
    assert action_items.check_deadline_item(line, "十月三十号之前完成", DAY, lang="zh") == line


def test_note_numbers_do_not_confirm_a_day():
    """Сноска и гейт судят по одному тексту, а «12 мин» оговорки днём не считается:
    гейт ставит пометку — сноска называет дату (Haiku I4 out1)."""
    note = "📋 запись неполная: канал собеседников 10:40–10:52 (12 мин)"
    src = meeting_source.live("[10:30] Участник А: сдай отчёт\n", note, day=DAY)
    out = meeting_source.finish_minutes("## Поручения\n" + _item("до 12.11") + "\n", src,
                                        owner="Участник А", participants={"Участник А", "Участник Б"})
    assert _item(MARK) in out and "12.11" in out.split(fact_check.FOOTNOTE_MARK)[1]


def test_annotate_carries_dates_of_every_footnote():
    doc = f"тело\n\n---\n{fact_check.FOOTNOTE_MARK} 01.11\n\n---\n{fact_check.FOOTNOTE_MARK} 02.11\n"
    out = fact_check.annotate(doc, "речь", day=DAY)
    assert out == f"тело\n\n---\n{fact_check.FOOTNOTE_MARK} 01.11, 02.11\n"


# ---- выходной круг out2 ---------------------------------------------------------

@pytest.mark.parametrize("line", [
    _item("комната 405", what="забронировать"),
    _item("версия 2.1", what="выпустить"),
    _item("за 2026 год", what="отчёт"),
    _item("12 слайдов", what="собрать материал"),
    "- [ ] **参会者甲** — 准备 — 周报",
])
def test_part_with_a_number_is_not_a_deadline(line):
    """Цифра или иероглиф в части дела срока не делают: пометка стирала текст дела
    (DS C1, Haiku C1, Sonnet I3 out2)."""
    assert action_items.check_deadline_item(line, "[10:00] Участник А: сделай", DAY, lang="ru") == line


@pytest.mark.parametrize("line", [
    "- [ ] **Участник А** — сдать отчёт до 28.11",
    "- [ ] **Участник А** — до 28.11",
    "- [ ] сдать отчёт — до 28.11",
    "- [ ] **Участник А** — обновить Python до 3.10 и прогнать тесты",
    "- [ ] **Участник А**, **Участник Б** — сегодня провести созвон",
    "- [ ] Участник А — сегодня провести созвон",
])
def test_name_and_deed_are_not_judged(line):
    """Срок — с третьей части; имя и дело гейт не читает: вырезание даты чипа из
    дела стирало версию «до 3.10», а дело без жирного имени судилось как срок
    (№805 out3: Sonnet C1, Haiku C1, Haiku I2). Дата внутри дела — №607."""
    assert action_items.check_deadline_item(line, "[10:00] Участник А: сдай", DAY) == line


def test_deed_without_a_bold_name_keeps_its_text():
    line = "- [ ] Участник А — сегодня провести созвон — до 30.10"
    out = action_items.check_deadline_item(line, "[10:00] Участник А: проведу созвон", DAY)
    assert out == f"- [ ] Участник А — сегодня провести созвон — {MARK}"


@pytest.mark.parametrize("tail,speech,kept", [
    ("до 30.10", "перенесём на пятницу", True),        # DS C1 out3
    ("до 30.10", "успеем до вечера пятницы", True),
    ("до 06.11", "выкатим к пятнице следующей недели", True),   # DS I3, Haiku I5 out3
    ("до 02.11", "сделаю в понедельник", True),        # встреча — понедельник: и через неделю
    ("до 06.11", "в пятницу релиз", True),
    ("до 30.10", "после пятничной аварии", False),     # другое слово
    ("до 28.10", "в нашей среде разработки", True),    # цена: слово то же
    ("до 09.11", "сделаю в понедельник", False),       # дальше двух недель
    ("до 31.10", "сделаю в пятницу", False),           # суббота
])
def test_weekday_in_any_form_within_two_weeks(tail, speech, kept):
    """День недели — любой формой слова, в двух неделях от встречи: закрытый список
    предлогов стирал верный срок (№805 out3, DS C1); выдуманная дата при слове
    «среда» в другом смысле доживёт до человека — пропуск верного хуже пометки."""
    line = _item(tail)
    assert (action_items.check_deadline_item(line, speech, DAY) == line) is kept


@pytest.mark.parametrize("speech,kept", [
    ("пригласим к 28 участникам", False), ("на 28 человек хватит", False), ("сдам до 28 включительно", True),
])
def test_count_after_a_preposition_is_not_a_day(speech, kept):
    """«к 28 участникам» — не 28-е (Haiku I3, DS I2 out2)."""
    assert fact_check.due_heard("до 28.10", speech, day=DAY) is kept


@pytest.mark.parametrize("tail,speech,kept", [
    ("к 17:00", "[10:17] Участник А: ок", False),
    ("к 17:00", "**Участник А** [15:16–15:17]: ок", False),     # метка-диапазон, как пишет стенограмма
    ("к 17:00", "[10:00] Участник А: сделаю к 17:00", True),
])
def test_stamps_do_not_confirm_numbers(tail, speech, kept):
    """Метка реплики — не слово речи (Haiku I4, DS I5 out2)."""
    assert fact_check.due_heard(tail, speech, day=DAY) is kept


@pytest.mark.parametrize("tail,speech,kept", [
    ("до 10 утра", "сдам к 10 утром", True),            # Haiku C2 out3
    ("к 10:00 утра", "сдам к десяти", True),
    ("к 22:00", "сдам к двадцати двум", True),
    ("к 22:00", "сдам к двадцати", False),
    ("к 21:30", "сдам к двадцати одному тридцати", True),
    ("к 4:00", "сдам к четырём", True),
    ("к 28-му числу", "сдам к двадцать восьмому", True),   # Haiku I4 out3
    ("до 28, если успеем", "сдам", False),            # DS M2 out3
    ("до 28, если успеем", "сдам до 28-го", True),
    ("к 5 сотрудникам", "пригласим пятерых", True),   # DS M2 out3: число единиц — не день
    ("до релиза 3 часть работ", "отчёт", True),       # Haiku I3 out3
    ("к завтрашней встрече", "подготовлю", False),
    ("к завтрашней встрече", "завтра встреча", True),
    ("к среде", "сделаю в среду", True),
    ("к среде", "сделаю на днях", False),
    ("срочно", "сделай", True),
    ("через 3 дня", "сделай", True),
    ("к концу недели", "концепция", True),
])
def test_only_the_anchor_of_a_deadline_is_judged(tail, speech, kept):
    """Без даты судится только опора срока: день недели, «сегодня/завтра», время,
    день «до 28»; слова и числа вокруг — нет: каждое правило о них стирало верный
    срок или дело (№805 out1–out3)."""
    assert fact_check.due_heard(tail, speech, day=DAY) is kept


@pytest.mark.parametrize("sep", ["—", "–", "-"])
def test_marked_key_is_the_same_for_any_dash(sep):
    """Ключ снимает пометку за любым тире — мост узнаёт ту же задачу (Haiku I5, DS I1 out2)."""
    marked = action_items.check_deadline_item(f"- [ ] **Участник А** {sep} отчёт {sep} до 28.11", "отчёт", DAY)
    assert MARK in marked and task_line.key(marked) == task_line.key(f"- [ ] **Участник А** {sep} отчёт")


def test_trailing_spaces_are_kept():
    """Гейт меняет только срок (Haiku M2 out2)."""
    assert action_items.check_deadline_item(_item("до 28.11") + "  ", "отчёт", DAY) == _item(MARK) + "  "


def test_second_pass_keeps_the_task_number_of_the_footnote():
    """Повторный проход по готовому документу — те же байты: номер задачи прежней
    сноски переносится, как и дата (DS M5 out2)."""
    src = meeting_source.live("[10:00] Участник А: согласуй к 28-му\n", None, day=DAY)
    doc = "## Поручения\n- [ ] **Участник А** — согласовать — по TASK 3242 до 15.11\n"
    kw = dict(owner="Участник А", participants={"Участник А"})
    once = meeting_source.finish_minutes(doc, src, **kw)
    assert "TASK 3242" in once.split(fact_check.FOOTNOTE_MARK)[1]
    assert meeting_source.finish_minutes(once, src, **kw) == once
    carried = fact_check.annotate(f"тело\n\n---\n{fact_check.FOOTNOTE_MARK} TASK 3242\n", "речь", day=DAY)
    assert carried == f"тело\n\n---\n{fact_check.FOOTNOTE_MARK} TASK 3242\n"


@pytest.mark.parametrize("tail,kept", [
    ("до релиза 3.4", True), ("в течение работ по TASK 3242", True), ("к версии 2.0", True),
    ("к 16:00", False), ("до 28", False),
])
def test_only_time_numbers_of_a_deadline_are_judged(tail, kept):
    """Номер версии или задачи в части-сроке не судится — пометка стирала бы дело;
    числа времени — судятся (опыт до out3)."""
    line = _item(tail)
    assert (action_items.check_deadline_item(line, "[10:00] Участник А: отчёт сделай", DAY) == line) is kept
