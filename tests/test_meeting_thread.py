"""Нить встречи: растёт, не повторяется, читается боковым зрением.

Прежняя подсказка перегенерировалась целиком каждые несколько минут и потому
пересказывала одно и то же разными словами — за встречу 03.08 лог вырос до
68 КБ. Здесь проверяется другое поведение: модель дописывает к уже собранному,
повторы отбрасываются, а вид строки виден по знаку.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meeting_thread import ARCHIVE, DECISION, QUESTION, SAY, TOPIC, Thread  # noqa: E402


def test_thread_grows_instead_of_being_rewritten():
    t = Thread()
    t.ingest(f"{TOPIC} Партиции цеховых таблиц\n{SAY} Коля: механизм не нарезал", at="10:34")
    t.ingest(f"{DECISION} чинить механизм, не генератор", at="10:41")

    assert t.size == 2
    assert "Коля" in t.render() and "чинить механизм" in t.render()


def test_repeat_in_other_words_is_dropped():
    """Модель возвращается к теме и пересказывает вывод, к которому уже пришла."""
    t = Thread()
    t.ingest(f"{TOPIC} Партиции\n{SAY} механизм не создал партицию, поток загрузки упал")
    added = t.ingest(f"{SAY} механизм не создал партицию и поток загрузки упал")

    assert added == 0
    assert t.size == 1


def test_repeat_is_caught_across_the_whole_thread():
    # к теме возвращаются через десять минут — сравнивать только с хвостом мало
    t = Thread()
    t.ingest(f"{TOPIC} Партиции\n{SAY} фильтр исключает единственную партицию")
    t.ingest(f"{TOPIC} Обновление ОС\n{SAY} срок до конца года")
    added = t.ingest(f"{SAY} фильтр исключает единственную партицию")

    assert added == 0


def test_same_topic_named_differently_does_not_split_the_thread():
    """«Обновление ОС» и «Обновление операционной системы» — одна тема."""
    t = Thread()
    t.ingest(f"{TOPIC} Обновление ОС\n{SAY} требование безопасности")
    t.ingest(f"{TOPIC} Обновление ОС\n{SAY} срок до конца года")

    assert len(t.topics) == 1
    assert t.size == 2


def test_new_topic_opens_a_new_anchor():
    t = Thread()
    t.ingest(f"{TOPIC} Партиции\n{SAY} поток упал")
    t.ingest(f"{TOPIC} Сертификаты PXF\n{SAY} кто владелец")

    assert [x.title for x in t.topics] == ["Партиции", "Сертификаты PXF"]


def test_line_kinds_are_visible_by_their_mark():
    t = Thread()
    t.ingest(f"{TOPIC} Тема\n{SAY} говорят\n{DECISION} решили\n{QUESTION} открыто\n{ARCHIVE} 30.07 было")
    out = t.render()

    for mark in (SAY, DECISION, QUESTION, ARCHIVE):
        assert mark in out, f"знак {mark} потерялся"


def test_model_chatter_without_a_mark_is_ignored():
    """«Вот что нового:» в нити выглядит как реплика участника."""
    t = Thread()
    added = t.ingest("Вот что нового по встрече:\nКонечно, добавлю пункты.")

    assert added == 0 and t.size == 0


def test_none_answer_adds_nothing():
    t = Thread()
    t.ingest(f"{TOPIC} Тема\n{SAY} что-то")
    assert t.ingest("NONE") == 0


def test_old_topics_collapse_so_the_thread_stays_readable():
    t = Thread(live_topics=2)
    for i in range(4):
        t.ingest(f"{TOPIC} Тема {i}\n{SAY} первое утверждение {i}\n{SAY} совсем другое дело {i}")
    out = t.render()

    assert "(2)" in out, "ранние темы сворачиваются в заголовок со счётчиком"
    assert "первое утверждение 3" in out, "последние темы видны целиком"
    assert "первое утверждение 0" not in out, "ранние строки убраны с экрана"


def test_full_keeps_everything_for_the_meeting_file():
    t = Thread(live_topics=1)
    for i in range(3):
        t.ingest(f"{TOPIC} Тема {i}\n{SAY} строка {i}")

    assert all(f"строка {i}" in t.full() for i in range(3))


def test_context_for_the_model_is_the_tail_not_the_whole_thread():
    """Длинная встреча выест контекст, а дописывать надо к концу."""
    t = Thread()
    for i in range(5):
        t.ingest(f"{TOPIC} Тема {i}\n{SAY} строка {i}")
    ctx = t.as_context(topics=2)

    assert "Тема 4" in ctx and "Тема 3" in ctx
    assert "Тема 0" not in ctx


def test_line_before_any_topic_still_lands_somewhere():
    # первая минута разговора: тема ещё не названа, а сказанное терять нельзя
    t = Thread()
    t.ingest(f"{SAY} начали с обсуждения выходных")

    assert t.size == 1
    assert t.topics[0].title == "Разговор"


def test_decision_carries_its_time():
    t = Thread()
    t.ingest(f"{TOPIC} Тема\n{DECISION} чинить механизм", at="10:41")

    assert "10:41" in t.render(), "у решения время важно: по нему ищут в стенограмме"


def test_empty_and_marker_only_lines_are_skipped():
    t = Thread()
    assert t.ingest(f"{SAY}   \n{DECISION}\n\n") == 0


# --- пересказ теми же фактами, но другими словами ---------------------------
#
# На длинной встрече модель возвращается к мысли и излагает её иначе. Для
# посимвольного сравнения это разные строки, для человека — одна и та же,
# прочитанная дважды. Замер 03.08 на живой стенограмме: из 11 строк нити две
# были таким пересказом.

def test_reworded_repeat_is_caught_by_words():
    t = Thread()
    t.ingest(f"{TOPIC} Обновление\n{SAY} Собеседник 3 сообщил указание перейти "
             f"на версию 1.8 до конца года")
    added = t.ingest(f"{SAY} Собеседник 3 сообщил требование сверху перейти "
                     f"на версию 1.8 до конца года")

    assert added == 0, "тот же факт, переставленные слова — повтор"


def test_different_facts_with_shared_words_survive():
    """«Обновить Arena» и «обновить Postgres» делят половину слов — но это
    два разных дела, и потерять одно из них нельзя."""
    t = Thread()
    t.ingest(f"{TOPIC} Обновление\n{SAY} Arena обновляется силами вендора по отдельной заявке")
    added = t.ingest(f"{SAY} Postgres обновляется через интерфейс Cloud по кнопке")

    assert added == 1


def test_short_lines_are_not_merged_by_word_overlap():
    # на трёх словах пересечение случайно: «поток упал» и «поток встал»
    t = Thread()
    t.ingest(f"{TOPIC} Тема\n{SAY} поток упал")
    added = t.ingest(f"{SAY} поток встал")

    assert added == 1


# --- ⏮ разбор темы по клавише -------------------------------------------------

def test_expand_writes_into_named_topic_not_tail():
    """Архивные строки идут в ту тему, по которой спросили, а не в хвост нити."""
    t = Thread()
    t.ingest(f"{TOPIC} Обновление ОС\n{SAY} обсуждают сроки", at="10:00")
    t.ingest(f"{TOPIC} Бюджет\n{SAY} считают смету", at="10:20")
    added = t.add_archive("Обновление ОС", ["30.07: решили катить волнами"])
    assert added == 1
    rendered = t.full()
    os_block = rendered.split(f"{TOPIC} Бюджет")[0]
    assert f"{ARCHIVE} 30.07: решили катить волнами" in os_block


def test_expand_opens_topic_when_thread_lacks_it():
    """Просьба «что было по X» сама делает X темой разговора."""
    t = Thread()
    added = t.add_archive("Платёжный провайдер", ["17.07: выбрали YuPay"])
    assert added == 1
    assert t.last_topic_title == "Платёжный провайдер"


def test_expand_deduplicates_known_lines():
    t = Thread()
    t.ingest(f"{TOPIC} Обновление ОС\n{ARCHIVE} 30.07: мяч у отдела, дата не назначена")
    added = t.add_archive("Обновление ОС",
                          ["30.07: мяч у отдела, дата не назначена", "и новый факт про волны"])
    assert added == 1


def test_last_topic_title_on_empty_thread_is_blank():
    assert Thread().last_topic_title == ""
