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
    assert rb.bridge(review, transcript, owner="Владелец") == 1
    text = minutes.read_text(encoding="utf-8")
    assert "**Саша Орлова** — подготовить демо для показа (из ревизии)" in text
    assert "Мария" not in text
    assert rb.bridge(review, transcript, owner="Владелец") == 0, "повторный прогон ничего не дописывает"
    # нет минуток или раздела в ревизии — 0, без исключений
    assert rb.bridge(tdir / "нет.md", transcript) == 0
    minutes.unlink()
    assert rb.bridge(review, transcript) == 0


def test_l4_prompt_names_the_strict_section():
    prompt = graph_updater.cloud_enrich_prompt(transcript_name="x.md", folder=Path("."), graph=Path("."),
                                               rev_name="r.md", stamp="2026-09-05_1413", may_edit=False, context="")
    assert "## Восстановленные поручения" in prompt and "- [ ] **Имя** — что сделать" in prompt
