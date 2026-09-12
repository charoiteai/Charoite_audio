"""Мост L4 → минутки: восстановленные ревизией поручения — чекбоксами (хвост №152)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import review_bridge as rb  # noqa: E402
import graph_updater  # noqa: E402

REVIEW = """# Ревизия
## 1. Решения
- **Стенд поднят** — в разборе нет.
## Восстановленные поручения
- [ ] **Иван** — согласовать бюджет стенда — до 10.09
- **Саша Орлова** — подготовить демо для показа
- [x] **Пётр** — собрать примеры вопросов
нет
## 7. Что сделано в графе
- [ ] **Кто-то** — это уже не поручения
"""

MINUTES = """# Минутки встречи
**Дата/время:** 2026-09-05 14:13 **Участники:** Иван, Пётр, Саша Орлова
## Темы
- Планёрка
## Решения
- **Проверить сборку на стенде** — команда
## Поручения
- [ ] **Иван** — уточнить и согласовать бюджет стенда — до 10.09
- [ ] **Пётр** — собрать примеры вопросов для теста

## Открытые вопросы
- Кто ведёт протокол дальше?
"""


def test_recovered_items_take_only_the_strict_section():
    items = rb.recovered_items(REVIEW)
    assert items == ["**Иван** — согласовать бюджет стенда — до 10.09",
                     "**Саша Орлова** — подготовить демо для показа",
                     "**Пётр** — собрать примеры вопросов"]
    assert rb.recovered_items("## 2. Поручения и сроки\n- что-то\n") == [], "старый свободный формат — не раздел моста"
    assert rb.recovered_items("## Восстановленные поручения\nнет\n") == []


def test_merge_appends_missing_items_with_a_mark_and_keeps_the_rest():
    text, added = rb.merge_into_minutes(MINUTES, rb.recovered_items(REVIEW))
    # Иван и Пётр уже есть в минутках другими словами — ревизия их
    # пересказала, а не нашла; новое — только Саша Орлова
    # Иван — 3/4 значимых слов, Пётр — его слова целиком вложены в пункт
    # минуток (ревизия сжимает): оба пересказ; новое — только Саша
    assert added == 1
    section = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in section.split("\n") if ln.strip()]
    assert lines == ["- [ ] **Иван** — уточнить и согласовать бюджет стенда — до 10.09",
                     "- [ ] **Пётр** — собрать примеры вопросов для теста",
                     "- [ ] **Саша Орлова** — подготовить демо для показа (из ревизии)"]
    # остальные разделы и пустая строка перед следующим — на месте
    assert "## Открытые вопросы\n- Кто ведёт протокол дальше?" in text
    assert "(из ревизии)\n\n## Открытые вопросы" in text
    assert text.startswith("# Минутки встречи\n**Дата/время:**")


def test_merge_is_idempotent_and_dedups_by_meaning():
    once, added = rb.merge_into_minutes(MINUTES, rb.recovered_items(REVIEW))
    twice, again = rb.merge_into_minutes(once, rb.recovered_items(REVIEW))
    assert added == 1 and again == 0 and twice == once
    # тот же исполнитель, другое дело — новый пункт
    _, n = rb.merge_into_minutes(once, ["**Иван** — заказать сервер под стенд"])
    assert n == 1
    # тот же пункт другим регистром и пунктуацией — повтор
    _, n = rb.merge_into_minutes(once, ["**иван** — согласовать бюджет стенда, до 10.09!"])
    assert n == 0
    # пункт с пометкой «не участник» в минутках — тоже уже есть
    flagged = once.replace("- [ ] **Саша Орлова** — подготовить демо для показа (из ревизии)",
                           "- ⚠ не участник (Саша Орлова): **Саша Орлова** — подготовить демо для показа (из ревизии)")
    _, n = rb.merge_into_minutes(flagged, ["**Саша Орлова** — подготовить демо для показа"])
    assert n == 0


def test_merge_creates_the_section_and_replaces_an_empty_marker():
    text, added = rb.merge_into_minutes("# Минутки\n## Темы\n- одна\n", ["**Иван** — позвонить"])
    assert added == 1 and text.endswith("## Поручения\n- [ ] **Иван** — позвонить (из ревизии)")
    empty = "# Минутки\n## Поручения\n- нет\n\n## Риски\n- нет\n"
    text, added = rb.merge_into_minutes(empty, ["**Иван** — позвонить"])
    assert added == 1
    assert "## Поручения\n- [ ] **Иван** — позвонить (из ревизии)\n\n## Риски\n- нет" in text


def test_outsiders_get_the_minutes_mark_and_language_mark_follows_lang():
    text, added = rb.merge_into_minutes(MINUTES, ["**Мария** — принести отчёт"],
                                        participants={"Иван", "Пётр", "Саша Орлова"})
    assert added == 1 and "- ⚠ не участник (Мария): **Мария** — принести отчёт (из ревизии)" in text
    en = "# Minutes\n## Action items\n- [ ] **Bob** — call\n"
    text, added = rb.merge_into_minutes(en, ["**Alice** — write"], lang="en")
    assert added == 1 and "- [ ] **Alice** — write (from the review)" in text


def test_bridge_writes_minutes_next_to_the_transcript(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-09-05_1413_Планёрка.md"
    transcript.write_text("# Встреча 2026-09-05_1413 — Планёрка\n\nУчастники (звучали в разговоре): "
                          "Иван, Пётр, Саша Орлова\n\n**Иван** [14:13]: начнём\n", encoding="utf-8")
    minutes = tdir / "2026-09-05_1413_Планёрка_minutes.md"
    minutes.write_text(MINUTES, encoding="utf-8")
    review = tdir / "2026-09-05_1413_Планёрка_ревизия_claude.md"
    review.write_text(REVIEW + "- [ ] **Мария** — принести отчёт\n", encoding="utf-8")
    # последняя строка ревизии — вне строгого раздела (после «## 7.»), в минутки не идёт
    dropped: list[str] = []
    assert rb.bridge(review, transcript, owner="Владелец", dropped=dropped) == 1
    assert dropped == ["нет"], "выброшенное из раздела доходит до лога через bridge (GLM r1 по #533)"
    text = minutes.read_text(encoding="utf-8")
    assert "**Саша Орлова** — подготовить демо для показа (из ревизии)" in text
    assert "Мария" not in text
    assert rb.bridge(review, transcript, owner="Владелец") == 0, "повторный прогон ничего не дописывает"
    # нет минуток или раздела в ревизии — 0, без исключений
    assert rb.bridge(tdir / "нет.md", transcript) == 0
    minutes.unlink()
    assert rb.bridge(review, transcript) == 0


def test_bridge_writes_the_owner_one_way_before_dedup(tmp_path):
    """№188: «**Ивану** — позвонить заказчику» из ревизии против «**Иван** —
    позвонить заказчику» в минутках — тот же пункт, а не второй; новый пункт
    владельца пишется первым словом user_name, как его ищет окно «Задачи»."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-09-05_1413_Планёрка.md"
    transcript.write_text("# Встреча\n\nУчастники (звучали в разговоре): Иван, Пётр\n\n"
                          "**Иван** [14:13]: начнём\n", encoding="utf-8")
    minutes = tdir / "2026-09-05_1413_Планёрка_minutes.md"
    # три написания владельца, которые уже могут лежать в минутках: канон,
    # легаси-падеж (минутки до канонизации) и полное user_name
    minutes.write_text("# Минутки\n## Поручения\n- [ ] **Иван** — позвонить заказчику\n"
                       "- [ ] **Ивану** — согласовать бюджет\n- [ ] **Иван Орлов** — собрать демо\n"
                       "- **Ивану** — уточнить смету\n",          # буллет без чекбокса, руками (GLM r2 M5)
                       encoding="utf-8")
    review = tdir / "2026-09-05_1413_Планёрка_ревизия_claude.md"
    review.write_text("## Восстановленные поручения\n- [ ] **Ивану** — позвонить заказчику\n"
                      "- [ ] **Ивану** — согласовать бюджет\n- [ ] **Ивану** — собрать демо\n"
                      "- [ ] **Ивану** — уточнить смету\n- [ ] **Ивану** — написать отчёт\n", encoding="utf-8")
    assert rb.bridge(review, transcript, owner="Иван Орлов") == 1, minutes.read_text(encoding="utf-8")
    text = minutes.read_text(encoding="utf-8")
    for phrase in ("позвонить заказчику", "согласовать бюджет", "собрать демо", "уточнить смету"):
        assert text.count(phrase) == 1, (phrase, text)
    assert "- [ ] **Иван** — написать отчёт (из ревизии)" in text, text
    # старые строки минуток не переписываются — сравнение шло по канону, файл не трогали
    assert "- [ ] **Ивану** — согласовать бюджет\n" in text and "- [ ] **Иван Орлов** — собрать демо\n" in text
    # полное имя без ё в минутках против user_name с ё — тот же владелец (GLM r2 I1)
    _, added = rb.merge_into_minutes("## Поручения\n- [ ] **Петр Иванов** — собрать демо\n",
                                     ["**Пётр** — собрать демо"], owner="Пётр Иванов")
    assert added == 0


def test_bridge_does_not_mark_the_owner_legacy_dative_line(tmp_path):
    """Important GLM r3 по #536: мост гонит flag_outsiders по всему разделу, и
    легаси-строка «**Павлу**» владельца-Павла (написана до канонизации) теряла
    чекбокс — _same_person не знает беглой гласной. Формы владельца теперь в
    самих участниках (participants_of), строка остаётся задачей."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-09-05_1413_Планёрка.md"
    transcript.write_text("# Встреча\n\nУчастники (звучали в разговоре): Павел, Пётр\n\n"
                          "**Пётр** [14:13]: начнём\n", encoding="utf-8")
    minutes = tdir / "2026-09-05_1413_Планёрка_minutes.md"
    minutes.write_text("# Минутки\n## Поручения\n- [ ] **Павлу** — согласовать бюджет\n", encoding="utf-8")
    review = tdir / "2026-09-05_1413_Планёрка_ревизия_claude.md"
    review.write_text("## Восстановленные поручения\n- [ ] **Петру** — собрать демо\n", encoding="utf-8")
    assert rb.bridge(review, transcript, owner="Павел Иванов") == 1
    text = minutes.read_text(encoding="utf-8")
    assert "- [ ] **Павлу** — согласовать бюджет\n" in text and "не участник" not in text, text


def test_checkbox_no_is_an_empty_section_too():
    text, added = rb.merge_into_minutes("## Поручения\n\n- [ ] нет\n", ["**Иван** — позвонить"])
    assert added == 1 and "нет" not in text.split("## Поручения", 1)[1]
    assert text.rstrip("\n").endswith("## Поручения\n- [ ] **Иван** — позвонить (из ревизии)")
    text, added = rb.merge_into_minutes("## Поручения\n- **нет**\n", ["**Иван** — позвонить"])
    assert added == 1 and "**нет**" not in text


def test_stop_words_do_not_glue_two_different_items():
    two = ["**Пётр** — согласовать бюджет с финансами", "**Пётр** — согласовать бюджет с юристами"]
    _, added = rb.merge_into_minutes("## Поручения\n- нет\n", two)
    assert added == 2
    # а пересказ того же дела — по-прежнему одно
    _, again = rb.merge_into_minutes("## Поручения\n- [ ] **Пётр** — согласовать бюджет с финансами\n",
                                     ["**Пётр** — бюджет согласовать с финансами до пятницы"])
    assert again == 0


def test_bold_and_bare_headings_and_wrapped_items():
    # жирная подпись следующего раздела без двоеточия — тоже граница, когда пункты уже были (GLM r3 I1)
    bold = "**Восстановленные поручения:**\n- [ ] **Иван** — согласовать\n  бюджет стенда\n- [ ] **Пётр** — позвонить\n**Что сделано в графе**\n- узел\n"
    assert rb.recovered_items(bold) == ["**Иван** — согласовать бюджет стенда", "**Пётр** — позвонить"]
    bare = "Восстановленные поручения:\n- **Иван** — позвонить\n\n## Другое\n- x\n"
    assert rb.recovered_items(bare) == ["**Иван** — позвонить"]
    dot = "## Восстановленные поручения.\n- **Иван** — позвонить\nнет\n"
    assert rb.recovered_items(dot) == ["**Иван** — позвонить"]


def test_review_noise_lines_are_not_items():
    review = ("## **Восстановленные поручения**\n- **нет**\n- [ ] **Иван** — согласовать бюджет\n"
              "(срок не назван)\n- [ ] **Пётр** — написать\n-**Саша** — позвонить\n"
              "**Решения:**\n- приняли бюджет\n")
    assert rb.recovered_items(review) == ["**Иван** — согласовать бюджет", "**Пётр** — написать", "**Саша** — позвонить"]
    bare_end = "Восстановленные поручения:\n- [ ] **Иван** — позвонить\nРешения:\n- приняли бюджет\n"
    assert rb.recovered_items(bare_end) == ["**Иван** — позвонить"]
    assert not rb.section_present("…восстановленных поручений в этой ревизии нет…")
    assert rb.section_present("текст\n**Восстановленные поручения:**\n- x")
    # «—» в минутках — пустой раздел, как и «нет»
    text, added = rb.merge_into_minutes("## Поручения\n—\n\n## Риски\n- нет\n", ["**Иван** — позвонить"])
    assert added == 1 and "—\n" not in text.split("## Поручения", 1)[1].split("## Риски")[0]
    # заголовок про обсуждение поручений — не раздел поручений
    text, added = rb.merge_into_minutes("# М\n## Обсуждение поручений\n- обсудили\n", ["**Пётр** — написать"])
    assert added == 1 and text.count("## Поручения\n") == 1 and "обсудили\n- [ ]" not in text


def test_more_review_forms():
    outside = "**Восстановленные поручения**:\n- [ ] **Иван** — позвонить\n"
    assert rb.recovered_items(outside) == ["**Иван** — позвонить"], "двоеточие снаружи жирного"
    dash = "## Восстановленные поручения\n- [ ] **Иван** — позвонить\n---\nвсё\n"
    assert rb.recovered_items(dash) == ["**Иван** — позвонить всё"], "«---» — не пункт"
    bold_item = "## Восстановленные поручения\n- **Иван** — согласовать бюджет\n**Пётр** — позвонить\n**Саша**: написать\n"
    assert rb.recovered_items(bold_item) == ["**Иван** — согласовать бюджет", "**Пётр** — позвонить", "**Саша**: написать"], \
        "поручение без маркера — свой пункт, не хвост чужого и не потеря (GLM r5)"
    # вложенность только «ревизия сжимает»: длинный ревизионный пункт с новым делом — не дубль
    _, n = rb.merge_into_minutes("## Поручения\n- [ ] **Иван** — отправить отчёт\n",
                                 ["**Иван** — отправить отчёт и презентацию заказчику"])
    assert n == 1
    _, n = rb.merge_into_minutes("## Поручения\n- [ ] **Иван** — отправить отчёт и презентацию заказчику\n",
                                 ["**Иван** — отправить отчёт"])
    assert n == 0
    # легаси-заголовок без решётки
    text, added = rb.merge_into_minutes("# М\n**Поручения и сроки:**\n- [ ] **Иван** — позвонить\n", ["**Пётр** — написать"])
    assert added == 1 and text.count("Поручения") == 1


def test_round4_edges():
    # перенос с жирного спана — продолжение; «**Пётр** — …» без маркера — нет
    wrapped = "## Восстановленные поручения\n- [ ] **Иван** — согласовать бюджет\n**на стенд** до пятницы\n**срок:** пятница\n**Пётр** — позвонить\n"
    assert rb.recovered_items(wrapped) == ["**Иван** — согласовать бюджет **на стенд** до пятницы **срок:** пятница",
                                           "**Пётр** — позвонить"]
    # «–» и «•» одиночные — пусто, не хвост пункта
    assert rb.recovered_items("## Восстановленные поручения\n- [ ] **Иван** — позвонить\n–\n•\n") == ["**Иван** — позвонить"]
    # проза со словом «поручений» — не раздел: пункты уходят в новый раздел в конце
    text, added = rb.merge_into_minutes("Обсудили статус.\nПоручений нет — все задачи закрыты.\n", ["**Иван** — позвонить"])
    assert added == 1 and text.endswith("## Поручения\n- [ ] **Иван** — позвонить (из ревизии)")
    # дедуп внутри ревизии не зависит от порядка строк
    long_first = ["**Иван** — отправить отчёт и презентацию заказчику", "**Иван** — отправить отчёт"]
    ta, a = rb.merge_into_minutes("## Поручения\n- нет\n", long_first)
    tb, b = rb.merge_into_minutes("## Поручения\n- нет\n", list(reversed(long_first)))
    assert a == b == 1 and ta == tb and "презентацию" in ta, "выживает самая полная форма, порядок не решает"
    # цепочка маркеров с пробелами — пусто; проза с двоеточием — не раздел
    assert rb.recovered_items("## Восстановленные поручения\n- [ ] **Иван** — позвонить\n– —\n") == ["**Иван** — позвонить"]
    text, added = rb.merge_into_minutes("## Решения\nAction items discussed below:\n\n## Риски\n- нет\n", ["**Иван** — позвонить"])
    assert added == 1 and text.endswith("## Поручения\n- [ ] **Иван** — позвонить (из ревизии)")


def test_legacy_section_title_is_reused_not_duplicated():
    old = "# Минутки\n## Поручения и сроки\n- [ ] **Иван** — позвонить\n\n## Риски\n- нет\n"
    text, added = rb.merge_into_minutes(old, ["**Пётр** — написать"])
    assert added == 1 and text.count("## Поручения") == 1
    assert "## Поручения и сроки\n- [ ] **Иван** — позвонить\n- [ ] **Пётр** — написать (из ревизии)\n\n## Риски" in text
    # упоминание в прозе — не раздел (DS r2 M5); заголовок — раздел
    assert not rb.section_present("…в разделе восстановленных поручений пусто…") and not rb.section_present("ничего")
    assert rb.section_present("## Восстановленные поручения\n- x")


WITHDRAWING = """# Ревизия
## Ошибки минуток
- ⛔ «Мария — пересчитать оценки по проекту — к 25.09» — Марии на встрече нет.
## Снятые поручения
- **Мария** — пересчитать оценки по проекту, отчёт по срокам — причина: Марии на встрече нет, даты 25.09 в записи нет
- **Пётр** — согласовать план релиза и бюджет — причина: срока нет, уходит в отпуск
- **Пётр** — собрать примеры вопросов
нет
## Восстановленные поручения
- [ ] **Иван** — прислать сводку по плану
"""

MINUTES_FALSE = """# Минутки
## Поручения
- [ ] **Мария** — пересчитать оценки по проекту, отчёт по срокам — к 25.09
- [ ] **Петру** — согласовать план релиза и бюджет — до конца недели
- [x] **Пётр** — собрать примеры вопросов для теста
- [ ] **Анна** — исключить Ольгу из рабочей группы

## Открытые вопросы
- Кто ведёт протокол дальше?
"""


def test_withdrawn_items_parse_reason_and_only_the_strict_section():
    dropped: list[str] = []
    items = rb.withdrawn_items(WITHDRAWING, dropped=dropped)
    assert items[0] == ("**Мария** — пересчитать оценки по проекту, отчёт по срокам", "Марии на встрече нет, даты 25.09 в записи нет")
    assert items[1][1] == "срока нет, уходит в отпуск" and items[2] == ("**Пётр** — собрать примеры вопросов", "")
    assert len(items) == 3 and dropped == ["нет"], "восстановленные — другой раздел, «нет» — шум"
    assert rb.withdrawn_items("## Восстановленные поручения\n- [ ] **Иван** — x\n") == []


def test_withdraw_moves_false_items_out_of_tasks_but_not_out_of_minutes():
    """№238: снятый пункт исчезает из раздела поручений (его читает вкладка
    «Задачи»), но остаётся в минутках зачёркнутым с причиной; выполненный
    человеком «[x]» не снимается; исполнитель в другом падеже — тот же."""
    text, moved = rb.withdraw_from_minutes(MINUTES_FALSE, rb.withdrawn_items(WITHDRAWING), owner="Владелец")
    assert moved == 2, text
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert "Мария" not in tasks and "Петру" not in tasks
    assert "- [x] **Пётр** — собрать примеры вопросов для теста" in tasks, "сделанное человеком — факт, не пересказ"
    assert "- [ ] **Анна** — исключить Ольгу из рабочей группы" in tasks
    gone = text.split("## Снято ревизией\n", 1)[1].split("\n## ", 1)[0]
    assert "- ~~**Мария** — пересчитать оценки по проекту, отчёт по срокам — к 25.09~~ _(снято ревизией: Марии на встрече нет, даты 25.09 в записи нет)_" in gone
    assert "- ~~**Петру** — согласовать план релиза и бюджет — до конца недели~~ _(снято ревизией: срока нет, уходит в отпуск)_" in gone
    assert text.index("## Поручения") < text.index("## Снято ревизией") < text.index("## Открытые вопросы")
    # идемпотентно: второй проход ничего не находит и не дублирует раздел
    again, moved2 = rb.withdraw_from_minutes(text, rb.withdrawn_items(WITHDRAWING), owner="Владелец")
    assert moved2 == 0 and again == text
    # раздел уже есть — новая ревизия дописывает в него, а не заводит второй
    more, moved3 = rb.withdraw_from_minutes(text, [("**Анна** — исключить Ольгу из рабочей группы", "говорила Анна, поручения не было")])
    assert moved3 == 1 and more.count("## Снято ревизией") == 1 and "**Анна** — исключить" in more.split("## Снято ревизией\n", 1)[1]
    # без раздела поручений и без пунктов — минутки те же
    assert rb.withdraw_from_minutes("# Минутки\n## Темы\n- a\n", rb.withdrawn_items(WITHDRAWING)) == ("# Минутки\n## Темы\n- a\n", 0)
    assert rb.withdraw_from_minutes(MINUTES_FALSE, []) == (MINUTES_FALSE, 0)
    # английские минутки — свой заголовок и пометка
    en, n = rb.withdraw_from_minutes("# Minutes\n## Action items\n- [ ] **Ann** — send the report\n",
                                     [("**Ann** — send the report", "not said")], lang="en")
    assert n == 1 and "## Withdrawn by the review\n- ~~**Ann** — send the report~~ _(withdrawn by the review: not said)_" in en


def test_withdrawal_is_one_to_one_and_does_not_guess_between_brothers():
    """DS r1 Critical по #545: один снятый пункт снимал все похожие строки
    минуток; GLM r1 I1: одно общее слово дела снимало любой пункт человека
    в другом падеже. Теперь: подходит к нескольким — не гадаем, точная
    строка среди похожих — ровно она, однословное дело — по Жаккару."""
    minutes = ("# Минутки\n## Поручения\n"
               "- [ ] **Иван** — подготовить отчёт по бюджету\n"
               "- [ ] **Иван** — подготовить отчёт по срокам\n"
               "- [ ] **Сергей** — позвонить в банк\n"
               "- [ ] **Сергей** — позвонить юристу\n"
               "- [ ] **Сергей** — позвонить\n")
    dropped: list[str] = []
    text, moved = rb.withdraw_from_minutes(minutes, [("**Иван** — подготовить отчёт", "шутка")], dropped=dropped)
    assert (moved, text) == (0, minutes)
    assert dropped == ["снятие «**Иван** — подготовить отчёт» подходит к 2 пунктам минуток — не гадаем, оставлены"]
    text, moved = rb.withdraw_from_minutes(minutes, [("**Сергею** — позвонить", "шутка")])
    assert moved == 1 and "- ~~**Сергей** — позвонить~~ _(снято ревизией: шутка)_" in text
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert "позвонить в банк" in tasks and "позвонить юристу" in tasks, "одно общее слово — не то же дело"
    both = minutes + "- [ ] **Иван** — подготовить отчёт\n"
    text, moved = rb.withdraw_from_minutes(both, [("**Иван** — подготовить отчёт", "шутка")])
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert moved == 1 and "по бюджету" in tasks and "по срокам" in tasks and "- [ ] **Иван** — подготовить отчёт\n" not in tasks + "\n"
    # две причины на одну строку — снимается один раз, с первой
    text, moved = rb.withdraw_from_minutes(minutes, [("**Сергею** — позвонить", "раз"), ("**Сергей** — позвонить", "два")])
    assert moved == 1 and text.count("~~") == 2 and "(снято ревизией: раз)" in text


def test_withdrawn_item_takes_its_wrapped_lines_along():
    """DS r1 I2, GLM r1 M3 по #545: перенос пункта на вторую строку уезжает
    вместе с ним — в «Поручениях» не остаётся сироты без исполнителя, в
    зачёркнутом тексте не теряется хвост."""
    minutes = ("# Минутки\n## Поручения\n"
               "- [ ] **Мария** — пересчитать оценки по проекту,\n"
               "  отчёт по срокам — к 25.09\n"
               "- [ ] **Анна** — исключить Ольгу из рабочей группы\n\n"
               "## Открытые вопросы\n- Кто ведёт протокол дальше?\n")
    text, moved = rb.withdraw_from_minutes(minutes, [("**Мария** — пересчитать оценки по проекту, отчёт по срокам", "Марии нет")])
    assert moved == 1, text
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert tasks.strip() == "- [ ] **Анна** — исключить Ольгу из рабочей группы", tasks
    assert "- ~~**Мария** — пересчитать оценки по проекту, отчёт по срокам — к 25.09~~ _(снято ревизией: Марии нет)_" in text
    assert rb._continuation("  отчёт по срокам") and not rb._continuation("- [ ] x") \
        and not rb._continuation("**Пётр** — свой пункт") and not rb._continuation("**Срок**") and not rb._continuation("  ")


def test_two_word_owner_is_compared_in_one_canon_on_both_sides(tmp_path):
    """DS r2 Critical, GLM r2 I1 по #545: у владельца «Иван Орлов» строка
    минуток сводилась к «**Иван**», а пункт ревизии оставался «**Иван
    Орлов**» — снятие не срабатывало никогда, восстановление давало дубль."""
    minutes = "## Поручения\n- [ ] **Иван Орлов** — подготовить отчёт по бюджету\n"
    assert rb.withdraw_from_minutes(minutes, [("**Иван Орлов** — подготовить отчёт по бюджету", "не прозвучало")],
                                    owner="Иван Орлов")[1] == 0, "сама функция ждёт пункт в каноне минуток"
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-09-11_1533_Планёрка.md"
    transcript.write_text("# Встреча\n\n**Иван Орлов** [15:33]: начнём\n", encoding="utf-8")
    mpath = tdir / "2026-09-11_1533_Планёрка_minutes.md"
    mpath.write_text("# Минутки\n" + minutes + "- [ ] **Иван Орлов** — согласовать план\n", encoding="utf-8")
    review = tdir / "2026-09-11_1533_Планёрка_ревизия_claude.md"
    review.write_text("# Ревизия\n## Снятые поручения\n- **Иван Орлов** — подготовить отчёт по бюджету — причина: не прозвучало\n"
                      "## Восстановленные поручения\n- [ ] **Иван Орлов** — согласовать план\n"
                      "- [ ] **Иван Орлов** — позвонить юристу\n", encoding="utf-8")
    assert rb.withdraw(review, transcript, owner="Иван Орлов") == 1
    assert rb.bridge(review, transcript, owner="Иван Орлов") == 1, "«согласовать план» уже есть — не дубль"
    text = mpath.read_text(encoding="utf-8")
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert tasks.strip() == ("- [ ] **Иван Орлов** — согласовать план\n"
                             "- [ ] **Иван Орлов** — позвонить юристу (из ревизии)"), text
    assert "~~**Иван Орлов** — подготовить отчёт по бюджету~~" in text, "в минутках — как было написано, не первое слово"


def test_blank_line_inside_a_wrapped_item_does_not_orphan_its_tail():
    """DS r2 M4 по #545: перенос через пустую строку — тоже хвост пункта."""
    minutes = "## Поручения\n- [ ] **Мария** — пересчитать оценки,\n\n  отчёт по срокам к 25.09\n- [ ] **Анна** — x\n"
    text, moved = rb.withdraw_from_minutes(minutes, [("**Мария** — пересчитать оценки", "нет на встрече")])
    assert moved == 1
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert tasks.strip() == "- [ ] **Анна** — x", tasks
    assert "- ~~**Мария** — пересчитать оценки, отчёт по срокам к 25.09~~" in text
    # абзац прозы после пустой строки — не хвост пункта (DS r3 M5): без отступа остаётся
    prose = "## Поручения\n- [ ] **Мария** — пересчитать оценки\n\nСроки уточняются.\n"
    text, moved = rb.withdraw_from_minutes(prose, [("**Мария** — пересчитать оценки", "")])
    assert moved == 1 and "Сроки уточняются." in text.split("## Снято ревизией", 1)[0] and "~~**Мария** — пересчитать оценки~~" in text


def test_review_items_are_deduped_among_themselves_in_the_owner_canon():
    """GLM r3 M2 по #545: «**Иван Орлов** — X» и «**Иван** — X» в одной ревизии — один пункт."""
    text, n = rb.merge_into_minutes("## Поручения\n- [ ] **Анна** — y\n",
                                    ["**Иван Орлов** — позвонить юристу", "**Иван** — позвонить юристу"], owner="Иван Орлов")
    assert n == 1 and text.count("позвонить юристу") == 1


def test_existing_withdrawn_section_dedups_only_within_itself():
    """GLM r1 M6 по #545: ключи дедупа — из своего раздела, а не до конца файла."""
    minutes = ("# Минутки\n## Поручения\n- [ ] **Анна** — исключить Ольгу из рабочей группы\n\n"
               "## Снято ревизией\n- ~~**Пётр** — старое~~ _(снято ревизией: x)_\n\n"
               "## Открытые вопросы\n- ~~**Анна** — исключить Ольгу из рабочей группы~~ _(снято ревизией: y)_\n")
    text, moved = rb.withdraw_from_minutes(minutes, [("**Анна** — исключить Ольгу из рабочей группы", "y")])
    assert moved == 1
    gone = text.split("## Снято ревизией\n", 1)[1].split("\n## ", 1)[0]
    assert "**Анна** — исключить" in gone and "**Пётр** — старое" in gone
    assert text.count("**Анна** — исключить Ольгу из рабочей группы~~") == 2, "чужой раздел не глушит перенос"


def test_withdrawn_section_present_mirrors_the_recovered_check():
    assert rb.withdrawn_section_present("## Снятые поручения (проверка)\n- x")
    assert rb.withdrawn_section_present("**Снятые поручения:**")
    assert not rb.withdrawn_section_present("## Восстановленные поручения\n- x")
    assert not rb.withdrawn_section_present("в записи снятых поручений нет")


def test_withdraw_then_bridge_on_disk_keeps_the_verified_item(tmp_path):
    """Снятие идёт до дописывания: ревизия снимает ложный пункт и
    восстанавливает верный похожий — верный не должен погибнуть в дедупе
    против ложного."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-09-11_1533_Планёрка.md"
    transcript.write_text("# Встреча\n\nУчастники (звучали в разговоре): Олег, Анна, Иван\n\n**Олег** [15:33]: начнём\n",
                          encoding="utf-8")
    minutes = tdir / "2026-09-11_1533_Планёрка_minutes.md"
    minutes.write_text("# Минутки\n## Поручения\n- [ ] **Иван** — прислать сводку по плану к пятнице\n", encoding="utf-8")
    review = tdir / "2026-09-11_1533_Планёрка_ревизия_claude.md"
    review.write_text("# Ревизия\n## Снятые поручения\n- **Иван** — прислать сводку по плану к пятнице — причина: срока не было\n"
                      "## Восстановленные поручения\n- [ ] **Иван** — прислать сводку по плану\n", encoding="utf-8")
    assert rb.withdraw(review, transcript, owner="Владелец") == 1
    assert rb.bridge(review, transcript, owner="Владелец") == 1
    text = minutes.read_text(encoding="utf-8")
    tasks = text.split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert tasks.strip() == "- [ ] **Иван** — прислать сводку по плану (из ревизии)", text
    assert "~~**Иван** — прислать сводку по плану к пятнице~~ _(снято ревизией: срока не было)_" in text


def test_l4_prompt_names_the_strict_section():
    prompt = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                               rev_name="r.md", stamp="2026-09-05_1413", may_edit=False, context="")
    assert "## Восстановленные поручения" in prompt and "- [ ] **Имя** — что сделать" in prompt
    assert "## Снятые поручения" in prompt and "— причина: …" in prompt, "снятие — тот же строгий контракт (№238)"
    editing = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                                rev_name="r.md", stamp="2026-09-05_1413", may_edit=True, context="")
    assert "«- 📌 …» → «- ⛔ …»" in editing and "не удаляй и не переставляй" in editing, "решения помечаются на месте (№237)"


def test_classify_line_table():
    """Таблица «строка → класс» — единственное место, где живут правила
    разбора раздела (№187, критика GLM r4 по #518). had_items — были ли
    уже пункты в разделе: от этого зависят граница без двоеточия, свой
    пункт без маркера и перенос. Новый крайний случай — новая строка
    здесь, а не новая ветка в цикле."""
    cases = [
        # заголовок раздела в трёх формах
        ("## Восстановленные поручения", False, rb.LINE_HEAD),
        ("**Восстановленные поручения:**", False, rb.LINE_HEAD),
        ("Восстановленные поручения:", True, rb.LINE_HEAD),
        # границы раздела
        ("## 7. Что сделано в графе", True, rb.LINE_END),
        ("**Решения:**", False, rb.LINE_END),            # жирная подпись с двоеточием — граница всегда
        ("**Что сделано в графе**", True, rb.LINE_END),    # без двоеточия — граница, когда пункты были
        ("**Что сделано в графе**", False, rb.LINE_NOISE), # …а до первого пункта это подпись раздела
        ("Решения:", True, rb.LINE_END),                 # голая известная секция
        # пустые
        ("", True, rb.LINE_BLANK),
        ("   ", False, rb.LINE_BLANK),
        # пункты с маркером
        ("- [ ] **Иван** — согласовать бюджет", False, rb.LINE_ITEM),
        ("- **Саша Орлова** — подготовить демо", True, rb.LINE_ITEM),
        ("-**Саша** — позвонить", True, rb.LINE_ITEM),    # без пробела после маркера (GLM r2 M6)
        ("1) **Пётр** — написать", False, rb.LINE_ITEM),
        # маркер есть, пункта нет
        ("- нет", False, rb.LINE_NOISE),
        ("- [ ] нет", True, rb.LINE_NOISE),
        ("- **нет**", False, rb.LINE_NOISE),
        ("- ---", True, rb.LINE_NOISE),
        ("- [ ]", True, rb.LINE_NOISE),                   # чекбокс без текста — не пункт «[ ]» (DS r1 по #533)
        ("- [x]", False, rb.LINE_NOISE),
        ("-[ ] ", False, rb.LINE_NOISE),
        # без маркера
        ("**Пётр** — позвонить", True, rb.LINE_OWN_ITEM),  # свой пункт (GLM r5 I1)
        ("**Саша**: написать", True, rb.LINE_OWN_ITEM),
        ("**Пётр** — позвонить", False, rb.LINE_NOISE),    # до первого пункта — мимо (GLM r3 M3)
        ("  бюджет стенда", True, rb.LINE_CONT),           # перенос внутри пункта (DS r1 I2)
        ("**на стенд** до пятницы", True, rb.LINE_CONT),   # перенос с жирного спана (круг 4)
        ("всё", True, rb.LINE_CONT),
        ("(срок не назван)", True, rb.LINE_NOISE),         # комментарий модели (DS r2 I2)
        ("нет", True, rb.LINE_NOISE),
        ("–", True, rb.LINE_NOISE),
        ("– —", True, rb.LINE_NOISE),
        ("**", True, rb.LINE_NOISE),                       # одни звёздочки — не хвост пункта (GLM r1 по #533)
        ("*", True, rb.LINE_NOISE),
        ("строка до первого пункта", False, rb.LINE_NOISE),
    ]
    for line, had, want in cases:
        assert rb.classify_line(line, had) == want, f"{line!r} had_items={had}: ждали {want}"


def test_dropped_lines_are_collected_for_the_log():
    """Что мост выбросил из раздела, должно быть видно в логе ревизии:
    иначе потерянное поручение и честно пустой раздел выглядят одинаково."""
    review = ("## Восстановленные поручения\n- нет\n  (срок не назван)  \nпреамбула до пункта\n- [ ]\n"
              "- [ ] **Иван** — позвонить\n---\nвсё\n## Далее\n- x\n")
    dropped: list[str] = []
    assert rb.recovered_items(review, dropped=dropped) == ["**Иван** — позвонить всё"]
    # строки в лог — без отступов (GLM r1 по #533); пустой чекбокс — выброшен, а не пункт «[ ]»
    assert dropped == ["- нет", "(срок не назван)", "преамбула до пункта", "- [ ]", "---"]
    # без списка — прежнее поведение, ничего не копится и не ломается
    assert rb.recovered_items(review) == ["**Иван** — позвонить всё"]
    # пустые строки и границы раздела — не «выброшенное»
    dropped = []
    rb.recovered_items("## Восстановленные поручения\n\n- [ ] **Иван** — позвонить\n\n## Далее\n", dropped=dropped)
    assert dropped == []
