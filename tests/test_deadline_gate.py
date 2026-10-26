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
    ("согласовать с юристами", False), ("пятница", False), ("", False),
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
    "- [ ] **Участник А** — сдать отчёт до 28.11",                    # хвоста-срока нет
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


def test_section_only():
    out = action_items.check_deadlines(DOC, "Участник Б, к 28-му.", DAY)
    assert out == DOC.replace(_item("до 28.11"), _item(MARK))


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
