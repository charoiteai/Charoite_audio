"""№947: допуск полного входа досье считается по знакам окна, а не по байтам.

Синтетика: `llm.LLM` подменён двойником, сеть и модель не вызываются,
данных владельца нет. Масштаб входа — измеренный ранее порядок штатного
русского досье: ~9914 знаков / 18375 UTF-8 байт при `num_ctx = 8192`.
"""
from __future__ import annotations

import json

import pytest

import dossier_incremental as inc
import dossier_telemetry as tel
import llm


WINDOW = 8192
NUM_PREDICT = 1800
#: Прежний байтовый допуск (PR909) — его не хватало типовому входу.
BYTE_BUDGET = WINDOW - NUM_PREDICT - 256
#: Действующий допуск в знаках — независимое число, а не пересчёт константы:
#: 12272 = (8192 - 1800 - 256) * 2. Если коэффициент продукта изменится,
#: тест обязан упасть, а не согласованно подстроиться.
CHAR_BUDGET = 12272

CONFIG = {"llm": {"engine": "ollama", "model": "invented-local-model",
                  "base_url": "http://127.0.0.1:11434", "num_ctx": WINDOW}}


@pytest.fixture
def provider(monkeypatch):
    state = {"constructors": [], "streams": []}

    class Local:
        engine, base, cloud_ready, _fell_back_local = "ollama", "http://127.0.0.1:11434", False, False

        def __init__(self, config):
            state["constructors"].append(config)

        def require_local_ollama_model(self, model=None):
            return (model or CONFIG["llm"]["model"]) + ":local"

        def stream(self, prompt, **options):
            state["streams"].append((prompt, options))
            yield "synthetic answer"

    monkeypatch.setattr(llm, "LLM", Local)
    return state


def _russian(chars: int) -> str:
    paragraph = "Встреча участников обсуждала план работ и сроки. "
    return (paragraph * (chars // len(paragraph) + 1))[:chars]


def test_typical_russian_dossier_input_passes_admission_at_the_working_window(provider):
    source = _russian(9909)
    prompt = json.dumps({"new_sources": {"2099-01-02_100000": source}}, ensure_ascii=False)
    system = "You extend factual dossiers conservatively."
    # Регрессия №947: вход проходит по знакам и не проходит по прежнему байтовому допуску.
    assert len((system + prompt).encode("utf-8")) > BYTE_BUDGET
    assert len(system + prompt) <= CHAR_BUDGET
    assert inc.local_complete(prompt, CONFIG, system=system, num_predict=NUM_PREDICT) == "synthetic answer"
    assert len(provider["constructors"]) == 1
    sent, options = provider["streams"][0]
    # Полный вход доходит до адаптера байт-в-байт и без серверного усечения.
    assert sent == prompt and options["system"] == system
    assert options["num_predict"] == NUM_PREDICT
    assert options["truncate"] is False and options["shift"] is False


def test_exact_character_budget_is_admitted_and_one_more_refuses(provider):
    assert inc.local_complete("x" * CHAR_BUDGET, CONFIG, system="", num_predict=NUM_PREDICT) == "synthetic answer"
    assert provider["streams"][0][0] == "x" * CHAR_BUDGET
    provider["constructors"].clear()
    provider["streams"].clear()
    with pytest.raises(inc.Refused, match="context budget"):
        inc.local_complete("x" * (CHAR_BUDGET + 1), CONFIG, system="", num_predict=NUM_PREDICT)
    assert provider["constructors"] == [] and provider["streams"] == []


def test_worst_case_full_source_is_refused_with_context_telemetry(provider):
    # PROMPT_CHARS = 28_000 в src/charoite_graph/dossier.py:59 — худший случай.
    collector = tel.Collector()
    with collector.activate():
        with pytest.raises(inc.Refused, match="context budget"):
            inc.local_complete("я" * 28000, CONFIG, system="judge", num_predict=NUM_PREDICT)
    snapshot = collector.snapshot()
    assert snapshot["request_outcomes"] == {"admission": {"context_budget": 1}}
    assert snapshot["local_completion_attempts"] == 1
    assert provider["constructors"] == [] and provider["streams"] == []


def test_the_documented_coefficient_is_two(provider):
    assert inc.ADMISSION_CHARS_PER_TOKEN == 2


@pytest.mark.parametrize("context,num_predict,text,admitted", [
    (8192, 0, "x" * 15872, True),    # ровно бюджет без резерва ответа
    (8192, 0, "x" * 15873, False),   # один знак сверх
    (1024, 768, "", True),           # нулевой бюджет: пустой вход проходит
    (1024, 768, "x", False),         # нулевой бюджет: любой знак — отказ
    (1024, 1024, "x", False),        # отрицательный бюджет — отказ
    (1024, 2000, "x", False),        # num_predict больше окна — отказ
])
def test_budget_boundaries(provider, context, num_predict, text, admitted):
    config = {"llm": {**CONFIG["llm"], "num_ctx": context}}
    if admitted:
        assert inc.local_complete(text, config, system="", num_predict=num_predict) == "synthetic answer"
        assert provider["streams"][0][0] == text
    else:
        with pytest.raises(inc.Refused, match="context budget"):
            inc.local_complete(text, config, system="", num_predict=num_predict)
        assert provider["constructors"] == [] and provider["streams"] == []


def test_omitted_window_uses_the_same_default_budget(provider):
    config = {"llm": {key: value for key, value in CONFIG["llm"].items() if key != "num_ctx"}}
    with pytest.raises(inc.Refused, match="context budget"):
        inc.local_complete("x" * (CHAR_BUDGET + 1), config, system="", num_predict=NUM_PREDICT)
    assert provider["constructors"] == []
