"""Offline regressions for transport bounds and dossier evidence boundaries."""
from __future__ import annotations

import json

import pytest

import dossier_incremental as inc
import llm
from test_dossier_model_contract import A, B, BODY, _claim, _old, _verdict


FACT = "A rain cover was fitted."
MODEL = "synthetic-model"
EMPTY = ("## Сейчас\n—\n## Как пришли\n—\n## Решено\n—\n"
         "## Открыто\n—\n## Кто в теме\n—")


@pytest.fixture
def provider(monkeypatch):
    state = {"reference": MODEL + ":local", "contracts": [], "streams": [], "configs": []}

    class Local:
        engine, base, cloud_ready, _fell_back_local = "ollama", "http://127.0.0.1:11434", False, False

        def __init__(self, config):
            state["configs"].append(config)

        def require_local_ollama_model(self, model=None):
            state["contracts"].append(model)
            return state["reference"]

        def stream(self, prompt, **options):
            state["streams"].append((prompt, options))
            yield "ok"

    monkeypatch.setattr(llm, "LLM", Local)
    return state


def _config(**options):
    return {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434",
                    "num_ctx": 8192, **options}}


def _judges(monkeypatch, claims=None):
    calls = []

    def complete(prompt, *args, **kwargs):
        calls.append(json.JSONDecoder().raw_decode(prompt)[0])
        return json.dumps(_verdict(claims))

    monkeypatch.setattr(inc, "local_complete", complete)
    monkeypatch.setattr(inc, "audit_claim", lambda *a, **k: None)
    return calls


def test_complete_utf8_input_at_the_exact_reserved_window_is_delivered_intact(provider):
    prompt, system, output = "é" * 7, "judge", 2
    context = len(system + prompt) // inc.ADMISSION_CHARS_PER_TOKEN + output + 256
    assert inc.local_complete(prompt, _config(model=MODEL, num_ctx=context),
                              system=system, num_predict=output) == "ok"
    assert provider["configs"][0]["llm"]["num_ctx"] == context
    sent, options = provider["streams"][0]
    assert sent == prompt and options["system"] == system
    assert options["num_predict"] == output and options["truncate"] is False and options["shift"] is False


@pytest.mark.parametrize("model", [[], {}, 42])
def test_a_nonstring_requested_model_is_refused_before_the_provider_contract(provider, model):
    with pytest.raises(inc.Refused, match="invalid requested model"):
        inc.local_complete("synthetic input", _config(model=model), system="judge", num_predict=2)
    assert provider["contracts"] == [] and provider["streams"] == []


@pytest.mark.parametrize("reference", ["model:cloud:local", "model:local:local", "model-cloud:local"])
def test_an_adapter_cannot_smuggle_a_nested_remote_or_local_selector(provider, reference):
    provider["reference"] = reference
    with pytest.raises(inc.Refused, match="verified local model reference"):
        inc.local_complete("synthetic input", _config(), system="judge", num_predict=2)
    assert provider["contracts"] == [None] and provider["streams"] == []


def test_an_explicit_historical_correction_does_not_require_moving_a_current_line(monkeypatch):
    old_fact = f"- An earlier inspection was recorded at noon. [[{A}]]"
    new_fact = "The earlier inspection was at 13:00."
    replacement = f"- {new_fact} [[{B}]]"
    prior = BODY.replace("## Как пришли\n—", "## Как пришли\n" + old_fact)
    candidate = prior.replace(old_fact, old_fact + "\n" + replacement)
    quote = "The earlier inspection was at 13:00; the earlier noon time is incorrect."
    change = {"before": "An earlier inspection was recorded at noon.", "after": replacement,
              "source": B, "quote": quote}
    _judges(monkeypatch, [_claim(new_fact, quote=quote)])
    inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": quote}}, {}, candidate, [change])
    assert candidate.split("## Как пришли")[0] == prior.split("## Как пришли")[0]


def test_even_an_empty_prior_dossier_keeps_all_five_structural_sections(monkeypatch):
    candidate = EMPTY.replace("## Сейчас\n—", f"## Сейчас\n- {FACT} [[{B}]]")
    candidate = candidate.replace("## Кто в теме\n—", "")
    _judges(monkeypatch)
    with pytest.raises(inc.Refused, match="incomplete sections"):
        inc.validate_candidate("Glasshouse", _old(EMPTY), [B], {B: {"text": FACT}}, {}, candidate, [])


def test_a_punctuation_only_old_rule_cannot_become_a_new_prose_line(monkeypatch):
    prior = BODY.replace("## Как пришли", "---\n## Как пришли")
    candidate = prior.replace("---\n", f"---; {FACT} [[{B}]]\n")
    _judges(monkeypatch)
    with pytest.raises(inc.Refused, match="prior claim"):
        inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": FACT}}, {}, candidate, [])


def test_a_known_target_does_not_exempt_its_new_factual_alias_from_evidence(monkeypatch):
    prior = BODY.replace("purple labels.", "purple labels. [[SensorGuide]]")
    candidate = prior.replace("## Как пришли", f"- {FACT} [[{B}]] [[SensorGuide|The sensor is verified.]]\n## Как пришли")
    _judges(monkeypatch)
    with pytest.raises(inc.Refused, match="uncovered new significant text"):
        inc.validate_candidate("Glasshouse", _old(prior), [B], {B: {"text": FACT}}, {}, candidate, [])


def test_a_qualified_provenance_identifier_alias_does_not_need_a_second_factual_claim(monkeypatch):
    citation = f"[[Meetings/{B}|Meetings/{B}]]"
    candidate = BODY.replace("## Как пришли", f"- {FACT} {citation}\n## Как пришли")
    calls = _judges(monkeypatch)
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": FACT}}, {}, candidate, [])
    assert len(calls) == 1 and calls[0]["new_sources"] == {B: FACT}


def test_an_evidenced_leading_current_span_is_not_misclassified_as_missing(monkeypatch):
    leading = f"## Сейчас\n- {FACT}"
    candidate = EMPTY.replace("## Сейчас\n—", leading + f" [[{B}]]")
    _judges(monkeypatch, [_claim(leading, quote=FACT)])
    inc.validate_candidate("Glasshouse", _old(EMPTY), [B], {B: {"text": FACT}}, {}, candidate, [])
    assert candidate.startswith(leading)
