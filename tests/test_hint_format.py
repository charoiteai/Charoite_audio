"""Подсказка на встрече — нить разговора, а не ответ на вопрос.

Прежний формат помогал отвечать: «суть претензии → 2-3 тезиса → встречный
вопрос». Он полезен, когда спрашивают тебя, но бесполезен, когда нужно просто
не потерять ход встречи: о чём сейчас, почему это обсуждают, что по этой теме
уже было.

Второе: формат лежал в `sufler.role` — то есть в пользовательском config.yaml.
Любая правка роли молча меняла то, что человек читает во время встречи, и
проверить это было нечем. Теперь формат в коде.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from llm import LLM  # noqa: E402


def _llm(lang: str = "ru") -> LLM:
    return LLM({"sufler": {"role": "роль", "language": lang},
                "llm": {"base_url": "http://127.0.0.1:11434", "model": "m"}})


def _prompt(lang: str = "ru") -> str:
    llm = _llm(lang)
    captured: dict = {}

    def fake_stream(prompt, model=None, system=None):
        captured["prompt"] = prompt
        return iter(())

    llm.stream = fake_stream
    list(llm.hint("…хвост стенограммы…"))
    return captured["prompt"]


def test_hint_asks_for_the_thread_not_for_an_answer():
    p = _prompt()
    assert "нить разговора" in p
    assert "о чём сейчас" in p


def test_all_four_sections_are_requested():
    p = _prompt()
    for section in ("● ", "Почему:", "Было:", "Открыто:"):
        assert section in p, f"нет раздела {section}"


def test_history_comes_only_from_the_archive():
    """«Было» — из памяти прошлых встреч, иначе модель напишет что вспомнит."""
    p = _prompt()
    assert "ТОЛЬКО из памяти прошлых встреч" in p


def test_empty_sections_are_skipped_not_left_hanging():
    # пустой заголовок «Открыто:» читается как «вопрос есть, но я его не знаю»
    assert "пропускай раздел" in _prompt()


def test_names_and_versions_stay_as_they_sounded():
    """Модель узнаёт тему и подставляет знакомое имя продукта.

    Замер 03.08: на разговоре про «Линукс 1.8» первая версия промпта выдала
    «Обновление ОС до RHEL 8» — названия, которого в стенограмме нет ни разу.
    """
    p = _prompt()
    assert "как звучали" in p
    assert "RHEL" in p, "пример подмены нужен прямо в промпте — общего запрета мало"


def test_transcript_tail_reaches_the_model():
    llm = _llm()
    captured: dict = {}
    llm.stream = lambda prompt, model=None, system=None: (captured.update(p=prompt), iter(()))[1]
    list(llm.hint("Коля: партиция не нарезалась"))
    assert "Коля: партиция не нарезалась" in captured["p"]


@pytest.mark.parametrize("lang,marker", [("en", "thread of the conversation"), ("zh", "对话的脉络")])
def test_other_languages_get_the_same_shape(lang, marker):
    p = _prompt(lang)
    assert marker in p
    assert "● " in p


def test_unknown_language_falls_back_to_english():
    assert "thread of the conversation" in _prompt("de")


def test_format_does_not_live_in_the_user_config():
    """Формат — код, а не роль: иначе его нельзя ни проверить, ни исправить
    централизованно."""
    assert "по формату из твоей роли" not in _prompt()


# --- нить встречи: дописываем, а не пересочиняем -----------------------------
#
# Подсказка каждый раз сочиняет конспект последних минут заново — и пересказывает
# уже сказанное: за встречу 03.08 её лог вырос до 68 КБ. Нить устроена иначе:
# модель видит уже собранное и добавляет только новое.

def _thread_prompt(so_far: str = "", lang: str = "ru") -> str:
    llm = _llm(lang)
    captured: dict = {}
    llm.stream = lambda prompt, model=None, system=None: (
        captured.update(p=prompt), iter(()))[1]
    list(llm.thread("…свежий кусок разговора…", so_far))
    return captured["p"]


def test_thread_shows_the_model_what_is_already_collected():
    p = _thread_prompt("● Партиции\n  - поток упал")

    assert "Партиции" in p and "поток упал" in p
    assert "ТОЛЬКО то, чего в нити ещё нет" in p


def test_empty_thread_does_not_send_an_empty_block():
    # в начале встречи нити нет — пустой блок «<нить></нить>» только сбивает
    assert "<нить>" not in _thread_prompt("")


def test_thread_asks_for_none_when_nothing_new():
    """Молчание лучше пересказа: не появилось нового — на экране не дёргается."""
    assert "NONE" in _thread_prompt()


def test_all_marks_are_explained():
    p = _thread_prompt()
    for mark in ("●", "-", "⚑", "?", "⏮"):
        assert mark in p, f"знак {mark} не объяснён модели"


def test_one_line_one_thought():
    """Строка в три предложения не читается краем глаза во время разговора."""
    p = _thread_prompt()
    assert "ОДНА СТРОКА — ОДНА МЫСЛЬ" in p
    assert "12 слов" in p


def test_archive_line_comes_only_from_memory():
    assert "ТОЛЬКО из памяти прошлых встреч" in _thread_prompt()


def test_thread_keeps_names_as_they_sounded():
    # та же ловушка, что у подсказки: «1.8» превращается в «RHEL 8»
    assert "RHEL" in _thread_prompt()


def test_thread_speaks_the_configured_language():
    assert "NONE" in _thread_prompt(lang="en")
    assert "会议脉络" in _thread_prompt(lang="zh")
