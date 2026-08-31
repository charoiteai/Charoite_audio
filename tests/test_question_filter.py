"""Фильтр воды: обрывок STT не должен будить модель и занимать полотно.

04.08 в панели подряд висели «Что?», «С какого бы?», «Дром ирир? Да.» —
на каждый уходил вызов локальной модели И облачной, а в ответ приходило
«не вижу вопроса, уточните» на четыре строки.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import question_filter as qf  # noqa: E402


class TestQuestionCandidate:
    def test_question_mark_anywhere_is_enough(self):
        assert qf.looks_question("Можно я задам вопрос? Потом продолжу")

    def test_opening_word_without_punctuation_stays_fail_open(self):
        assert qf.looks_question("Когда переносим витрину")
        assert qf.looks_question("Есть ли окно на выходных")

    def test_statement_and_empty_text_do_not_schedule_hint(self):
        assert not qf.looks_question("Переносим витрину в субботу")
        assert not qf.looks_question("   ")

    def test_detector_has_no_model_or_io_dependency(self, monkeypatch):
        """Горячий STT-путь должен работать, даже если любой LLM запрещён.

        Подмена ``open`` ловит и случай, когда вместо модели сюда случайно
        протащат файловый вызов: детектор обязан быть чистой функцией.
        """
        monkeypatch.setattr("builtins.open", lambda *_a, **_kw: pytest.fail("I/O in detector"))
        assert qf.looks_question("Почему релиз задержался")

    def test_daemon_hot_path_has_no_question_model_call(self):
        daemon = (Path(__file__).resolve().parent.parent / "src" / "daemon.py") \
            .read_text(encoding="utf-8")
        assert "ask_question_model" not in daemon
        # Шим daemon.looks_question убран (партия D, 22.08): мутация
        # `return X → return None` на строке-обёртке выживала при зелёных
        # текстовых тестах. Горячие пути зовут фильтр напрямую.
        assert "question_filter.looks_question(added)" in daemon
        assert "question_filter.looks_question(recent)" in daemon
        assert "def looks_question(" not in daemon


class TestWorthAsking:
    def test_real_question_passes(self):
        assert qf.is_worth_asking("Когда переносим витрину на новый кластер?")
        assert qf.is_worth_asking("Кто отвечает за график дежурств в сентябре?")

    def test_stt_scraps_are_dropped(self):
        # ровно то, что висело в панели
        for scrap in ("Что?", "С какого бы?", "Дром ирир? Да.", "А он?", "Да?"):
            assert not qf.is_worth_asking(scrap), scrap

    def test_repeat_of_previous_question_is_dropped(self):
        q = "Когда переносим витрину на новый кластер?"
        assert not qf.is_worth_asking(q, previous=q)
        # переформулировка того же — тоже повтор
        assert not qf.is_worth_asking("Когда переносим витрину на новый кластер",
                                      previous=q)

    def test_new_question_after_previous_passes(self):
        assert qf.is_worth_asking("А кто принимает риски рассинхрона?",
                                  previous="Когда переносим витрину?")

    def test_empty_is_dropped(self):
        assert not qf.is_worth_asking("")
        assert not qf.is_worth_asking("   ")


class TestRefusal:
    def test_model_refusals_are_recognised(self):
        for text in (
            "Последняя фраза в стенограмме обрывается и неясна — не вижу полного вопроса.",
            "Не вижу чёткого вопроса в конце стенограммы — текст обрывается.",
            "Пожалуйста, уточните, какой именно вопрос в конце — тогда дам точный ответ.",
            "Я не понял вопрос, переформулируйте, пожалуйста.",
        ):
            assert qf.is_refusal(text), text

    def test_real_answer_is_not_refusal(self):
        assert not qf.is_refusal(
            "С 1 сентября вступает новая структура, погружение начинается на этой неделе.")
        # слово «вопрос» само по себе отказом не делает
        assert not qf.is_refusal("Вопрос закрыт: миграция в субботу ночью.")


class TestSqueeze:
    def test_long_answer_is_cut_to_first_sentences(self):
        long = ("С 1 сентября вступает новая структура. Погружение начинается "
                "на этой неделе. Дальше идут детали по бюджету. И ещё абзац "
                "про регламент. И совсем лишнее предложение в конце.")
        out = qf.squeeze(long, max_lines=2)
        assert out.startswith("С 1 сентября")
        assert "регламент" not in out

    def test_short_answer_survives_intact(self):
        short = "Миграция в субботу ночью, ответственный — Инженер."
        assert qf.squeeze(short) == short

    def test_empty_stays_empty(self):
        assert qf.squeeze("") == ""
        assert qf.squeeze(None) == ""


class TestQuestionFormSoftensThreshold:
    """Явная вопросная форма («?» + вопросный старт) снижает порог предмета
    до двух: «Что с деплоем?» — настоящий вопрос с одним значимым словом,
    общий порог в три его резал (аудит 18.08, №52)."""

    def test_short_real_questions_pass(self):
        assert qf.is_worth_asking("Что с деплоем?")
        assert qf.is_worth_asking("Когда релиз?")
        assert qf.is_worth_asking("Есть ли смысл?")

    def test_subjectless_scraps_still_rejected(self):
        # «Расскажи?» — старт сам значим, бонус не удваивается
        assert not qf.is_worth_asking("Что?")
        assert not qf.is_worth_asking("А он?")
        assert not qf.is_worth_asking("Расскажи?")
        assert not qf.is_worth_asking("Ну когда?")


class TestLiveLoopWiring:
    """Контракты проводки №52: тексты исходников, в стиле соседних
    контракт-тестов (daemon и llm без Ollama юнитом не поднять)."""

    def test_daemon_dedups_against_fresh_question_only(self):
        # Сырой _pending_q жил до конца встречи: после отказа модели повтор
        # того же вопроса глушился навсегда.
        src = (Path(__file__).resolve().parent.parent / "src" / "daemon.py").read_text(encoding="utf-8")
        fn = src[src.index("def fire_question("):]
        fn = fn[: fn.index("\n    def ")]
        assert "question_filter.is_worth_asking(" in fn
        assert "fresh_question(_pending_q[0], time.monotonic())" in fn, (
            "previous обязан проходить через fresh_question, а не сырой text")

    def test_instant_receives_the_question_explicitly(self):
        # fast_trigger ловит вопрос из стрима, которого в стенограмме нет:
        # instant обязан получать вопрос явно, а не надеяться на tail.
        root = Path(__file__).resolve().parent.parent / "src"
        daemon = (root / "daemon.py").read_text(encoding="utf-8")
        assert "llm.instant(tail, nodes=nodes_block, question=q" in daemon
        llm_src = (root / "llm.py").read_text(encoding="utf-8")
        inst = llm_src[llm_src.index("def instant("):]
        inst = inst[: inst.index("\n    def ")]
        assert 'question: str = ""' in inst
        assert "Собеседник задал вопрос" in inst


class TestEchoAndLeadingGlue:
    """Круг-1 по #466: смягчение не возвращает эхо-класс (DS F1), пауза и
    ведущая склейка не роняют настоящий вопрос (DS M1/M2)."""

    def test_comma_echo_is_still_rejected(self):
        assert not qf.is_worth_asking("Что, опять?")
        assert not qf.is_worth_asking("Что, Мира?")
        assert not qf.is_worth_asking("Когда, блин?")
        assert not qf.is_worth_asking("Что, если?")
        assert not qf.is_worth_asking("Ну что, Мира?")

    def test_comma_with_two_subjects_passes(self):
        assert qf.is_worth_asking("Что, если поедем?")

    def test_stt_pause_and_leading_glue_pass(self):
        assert qf.is_worth_asking("Что… с деплоем?")
        assert qf.is_worth_asking("А что с деплоем?")
        assert qf.is_worth_asking("Так когда релиз?")


class TestStrictAndRefusalReset:
    """Круг-1 по #466, GLM: облако — по строгому порогу (мягкая форма
    оплачивается секундами малой модели, не квотой Claude), а отказ модели
    освобождает вопрос для повтора."""

    def test_strict_disables_softening(self):
        assert qf.is_worth_asking("Что с деплоем?")
        assert not qf.is_worth_asking("Что с деплоем?", strict=True)
        assert qf.is_worth_asking("Что с деплоем сегодня по плану?", strict=True)

    def test_cloud_branch_requires_strict(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "daemon.py").read_text(encoding="utf-8")
        fn = src[src.index("def fire_question("):]
        fn = fn[: fn.index("\n    def ")]
        assert "strict=True" in fn.split("cloud_evt.set()")[0].split("instant_evt.set()")[1], (
            "облачная ветка обязана перепроверять вопрос строгим порогом")

    def test_refusal_frees_the_pending_question(self):
        src = (Path(__file__).resolve().parent.parent / "src" / "daemon.py").read_text(encoding="utf-8")
        loop = src[src.index("def instant_loop("):]
        loop = loop[: loop.index("\n    def ")]
        assert 'is_refusal(answer)' in loop
        assert '_pending_q[0] = {"text": "", "at": 0.0}' in loop, (
            "отказ модели обязан освобождать вопрос — иначе «я же спросил» молчит до TTL")
