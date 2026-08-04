"""Фильтр воды: обрывок STT не должен будить модель и занимать полотно.

04.08 в панели подряд висели «Что?», «С какого бы?», «Дром ирир? Да.» —
на каждый уходил вызов локальной модели И облачной, а в ответ приходило
«не вижу вопроса, уточните» на четыре строки.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import question_filter as qf  # noqa: E402


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
