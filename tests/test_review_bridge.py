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
                       "- [ ] **Ивану** — согласовать бюджет\n- [ ] **Иван Орлов** — собрать демо\n",
                       encoding="utf-8")
    review = tdir / "2026-09-05_1413_Планёрка_ревизия_claude.md"
    review.write_text("## Восстановленные поручения\n- [ ] **Ивану** — позвонить заказчику\n"
                      "- [ ] **Ивану** — согласовать бюджет\n- [ ] **Ивану** — собрать демо\n"
                      "- [ ] **Ивану** — написать отчёт\n", encoding="utf-8")
    assert rb.bridge(review, transcript, owner="Иван Орлов") == 1, minutes.read_text(encoding="utf-8")
    text = minutes.read_text(encoding="utf-8")
    assert text.count("позвонить заказчику") == 1 and text.count("согласовать бюджет") == 1 \
        and text.count("собрать демо") == 1, text
    assert "- [ ] **Иван** — написать отчёт (из ревизии)" in text, text
    # старые строки минуток не переписываются — сравнение шло по канону, файл не трогали
    assert "- [ ] **Ивану** — согласовать бюджет\n" in text and "- [ ] **Иван Орлов** — собрать демо\n" in text


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


def test_l4_prompt_names_the_strict_section():
    prompt = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                               rev_name="r.md", stamp="2026-09-05_1413", may_edit=False, context="")
    assert "## Восстановленные поручения" in prompt and "- [ ] **Имя** — что сделать" in prompt


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
