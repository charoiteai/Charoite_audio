"""Real dossier transport orchestration with a bounded, offline LLM provider.

Only llm.LLM is replaced. local_config, local_complete, source selection,
strict JSON, conservation, coverage, quotation context and all audit stages are real.
The provider's character budget is an explicit test protocol, not a tokenizer
or model-quality measurement; zero output allowance yields no response.
"""
from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest

import dossier_incremental as inc
import llm
import privacy
from charoite_graph import dossier

A, B = "2099-01-01_100000", "2099-01-02_100000"
SOURCE = "You judge source relevance conservatively."
GENERATE = "You extend factual dossiers conservatively."
MAIN = "You are an independent conservative dossier judge."
PROFILE = "Synthetic explicit transport profile."
OLD_SOURCE_ONLY = "OLD_SOURCE_A_FULL_TEXT_MUST_NOT_REACH_PROVIDER"


def _scenario(*, russian=False, long_source=False):
    topic = "Вымышленная теплица" if russian else "Glasshouse"
    fact = "На теплице установлен дождевой козырёк." if russian else "A rain cover was fitted."
    previous = "Форточка А открыта." if russian else "Vent A is open."
    warning = "Датчик ещё не проверен." if russian else "Sensor is unverified."
    body = (f"## Сейчас\n- {previous} [[{A}]]\n## Как пришли\n—\n"
            f"## Решено\n- Keep labels. [[SensorGuide]]\n## Открыто\n- ⚠ {warning}\n"
            "## Кто в теме\n—")
    text = dossier.render(topic, body, [], {}, "fixture", "2099-01-01", author_payload="\nKeep purple labels.\n")
    old = dossier.DossierLoad("ok", text, text.encode())
    context = ""
    if long_source:
        paragraph = ("В рабочем журнале записали состояние вымышленной теплицы: дорожки сухие, "
                     "стёкла целы, а план следующего наблюдения согласован.")
        context = "".join(f"Запись {i}: {paragraph}\n" for i in range(1, 57))
    source = context + f"[[Ядра/{topic}]] {fact}\nCOMPLETE_NEW_SOURCE_TAIL\n"
    candidate = body.replace("## Как пришли", f"- {fact} [[{B}]]\n## Как пришли")
    files = {A: {"text": OLD_SOURCE_ONLY}, B: {"text": source}}
    claim = {"text": fact, "source": B, "quote": fact, "relevant": True, "supported": True,
             "reason": "Synthetic quotation supports this one topic fact."}
    return SimpleNamespace(topic=topic, fact=fact, old=old, candidate=candidate, source=source,
                           files=files, claim=claim)


def _config(*, context=16384, engine="ollama"):
    options = {"engine": engine, "model": "invented-local-json-model",
               "base_url": "http://127.0.0.1:13701", "mlx_base_url": "http://[::1]:13702",
               "cloud_fallback_local": True, "allow_remote": True}
    if context != "omitted":
        options["num_ctx"] = context
    return {"llm": options}


@pytest.fixture
def provider(monkeypatch):
    state = SimpleNamespace(scenario=_scenario(), constructors=[], calls=[], actual_overrides={}, responses={})

    class BoundedLocalProvider:
        def __init__(self, config):
            state.constructors.append(copy.deepcopy(config))
            self.engine = privacy.llm_engine(config)
            self.base = privacy.chat_model_url(config)
            self.cloud_ready = False
            self._fell_back_local = False
            # Match the concrete LLM constructor. Explicit None is not silently
            # treated as an adapter-supported default by this test double.
            self.num_ctx = int(config["llm"].get("num_ctx", 8192))
            for name, value in state.actual_overrides.items():
                setattr(self, name, value)

        def stream(self, prompt, **kwargs):
            payload, end = json.JSONDecoder().raw_decode(prompt)
            call = {"prompt": prompt, "payload": payload, "suffix": prompt[end:],
                    "options": copy.deepcopy(kwargs), "context": self.num_ctx,
                    "engine": self.engine, "base": self.base}
            state.calls.append(call)
            scenario = state.scenario
            system = kwargs["system"]
            if system == SOURCE:
                answer = {"verdict": "relevant", "reason": "Synthetic supported topic fact.", "quote": scenario.fact}
            elif system == GENERATE:
                answer = {"body": scenario.candidate, "changes": []}
            elif system == MAIN:
                answer = {"verdict": "accept", "conserved": True, "supported": True, "corrections_valid": True,
                          "reason": "Synthetic candidate verdict.", "claims": [scenario.claim]}
            elif system == inc.SUPPORT_AUDIT_SYSTEM:
                answer = {"verdict": "accept", "supported": True,
                          "reason": "Synthetic faithful source entailment."}
            elif system == inc.TOPIC_AUDIT_SYSTEM:
                answer = {"verdict": "accept", "relevant": True,
                          "reason": "Synthetic explicit topic relationship."}
            else:
                assert system == PROFILE, "Unexpected transport role in the invented scenario."
                answer = {"text": "local profile answer"}
            answer = state.responses.get(system, answer)
            if isinstance(answer, Exception):
                raise answer
            serialized = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            # Finite output really affects the caller: an insufficient budget
            # truncates JSON; zero returns nothing. No canned full response is
            # yielded regardless of the requested output allowance.
            bounded = serialized[:max(0, int(kwargs["num_predict"]))]
            for start in range(0, len(bounded), 17):
                yield bounded[start:start + 17]

    monkeypatch.setattr(llm, "LLM", BoundedLocalProvider)
    return state


def _validate(scenario, config):
    inc.validate_candidate(scenario.topic, scenario.old, [B], scenario.files, config, scenario.candidate, [])


def _assert_finite_json_profiles(calls, config):
    for call in calls:
        options = call["options"]
        assert options["model"] == config["llm"]["model"]
        assert options["system"] in {SOURCE, GENERATE, MAIN, inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM}
        assert type(options["num_predict"]) is int and 0 < options["num_predict"] <= call["context"]
        assert options["think"] is False
        assert isinstance(options["temperature"], (int, float)) and math.isfinite(options["temperature"])
        if options["system"] in (inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM):
            assert type(options["temperature"]) in (int, float)
        assert set(options) == {"model", "system", "temperature", "num_predict", "think"}


def test_source_and_candidate_reach_bounded_local_provider_with_complete_new_input(provider):
    scenario, config = provider.scenario, _config()
    original_config = copy.deepcopy(config)
    assert inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config) is True
    _validate(scenario, config)
    assert [call["options"]["system"] for call in provider.calls] == [SOURCE, MAIN, inc.SUPPORT_AUDIT_SYSTEM,
                                                                    inc.TOPIC_AUDIT_SYSTEM]
    source, main, support, topic = provider.calls
    assert source["payload"]["source_text"] == scenario.source
    assert main["payload"]["new_sources"] == {B: scenario.source}
    assert main["payload"]["candidate"] == scenario.candidate
    assert main["payload"]["existing_dossier"] == scenario.old.parsed.body
    expected_audit = {"topic": scenario.topic, "prior_dossier": scenario.old.parsed.body,
                      "candidate_text": scenario.fact,
                      "source_quotation": scenario.fact, "source_context": scenario.source}
    assert support["payload"] == topic["payload"] == expected_audit
    assert support["options"]["system"] != topic["options"]["system"]
    assert support["suffix"].strip() and topic["suffix"].strip()
    assert support["suffix"] != topic["suffix"]
    assert all(OLD_SOURCE_ONLY not in call["prompt"] for call in provider.calls)
    assert all("COMPLETE_NEW_SOURCE_TAIL" in call["payload"]["source_context"] for call in (support, topic))
    _assert_finite_json_profiles(provider.calls, config)
    assert config == original_config
    assert all(c["llm"]["cloud_fallback_local"] is False and c["llm"]["allow_remote"] is False
               for c in provider.constructors)


def test_explicit_caller_profile_and_optional_fallback_record_are_preserved(provider):
    config = _config()
    requested = {"system": PROFILE, "temperature": 0.37, "think": False, "num_predict": 96}
    prompt = json.dumps({"request": "invented explicit caller profile"})
    fallback = []
    answer = inc.local_complete(prompt, config, fell_back=fallback, **requested)
    assert json.loads(answer) == {"text": "local profile answer"} and fallback == [False]
    assert inc.local_complete(prompt, config, **requested) == answer
    assert len(provider.calls) == 2
    assert all(call["options"] == {"model": config["llm"]["model"], **requested} for call in provider.calls)


@pytest.mark.parametrize("engine", ["ollama"])
def test_supported_local_adapter_uses_its_selected_loopback_endpoint(provider, engine):
    config, scenario = _config(engine=engine), provider.scenario
    assert inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config) is True
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["engine"] == engine and call["base"] == config["llm"]["base_url"]
    assert privacy.is_loopback_url(call["base"])
    _assert_finite_json_profiles(provider.calls, config)


@pytest.mark.parametrize("changed", [{"cloud_ready": True}, {"engine": "cloud"}, {"engine": "mlx-server"}, {"base": "https://example.invalid"}])
def test_an_actual_adapter_that_changes_transport_is_refused_before_streaming(provider, changed):
    provider.actual_overrides = changed
    with pytest.raises(inc.Refused):
        inc.local_complete(json.dumps({"request": "transport must remain local"}), _config(), system=PROFILE)
    assert len(provider.constructors) == 1 and provider.calls == []


@pytest.mark.parametrize("changed", [{"engine": "cloud"}, {"engine": "mlx-server"}, {"base_url": "https://example.invalid"}])
def test_nonlocal_caller_configuration_never_reaches_the_provider_factory(provider, changed):
    config = _config()
    config["llm"].update(changed)
    with pytest.raises((inc.Refused, privacy.PrivacyRefused)):
        inc.local_complete(json.dumps({"request": "must not leave this machine"}), config, system=PROFILE)
    assert provider.constructors == [] and provider.calls == []


def test_complete_russian_source_fits_utf8_window_and_generation_and_judges_succeed(provider):
    scenario = provider.scenario = _scenario(russian=True, long_source=True)
    config = _config(context=32768)
    assert inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config) is True
    assert inc.append(scenario.topic, scenario.old, [B], scenario.files, config) == scenario.candidate
    assert [call["options"]["system"] for call in provider.calls] == [SOURCE, GENERATE, MAIN,
                                                                    inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM]
    _assert_finite_json_profiles(provider.calls, config)
    for call in provider.calls[:3]:
        supplied = call["payload"].get("source_text") or call["payload"]["new_sources"][B]
        assert supplied == scenario.source
        assert "Запись 56:" in call["prompt"] and "COMPLETE_NEW_SOURCE_TAIL" in supplied
    for call in provider.calls[3:]:
        assert call["payload"]["source_context"] == scenario.source
        assert call["payload"]["prior_dossier"] == scenario.old.parsed.body
        assert "Запись 1:" in call["prompt"] and "Запись 56:" in call["prompt"]
        assert "COMPLETE_NEW_SOURCE_TAIL" in call["payload"]["source_context"]
    # Compare equivalent JSON encodings at the provider boundary. The ASCII
    # representation cannot fit even before accounting for message-envelope
    # overhead; the complete UTF-8 input just traversed the real admission gate.
    for call in provider.calls[1:]:
        ascii_prompt = json.dumps(call["payload"], ensure_ascii=True) + call["suffix"]
        assert len((call["options"]["system"] + ascii_prompt).encode()) + call["options"]["num_predict"] > call["context"]
    assert all(OLD_SOURCE_ONLY not in call["prompt"] for call in provider.calls)


@pytest.mark.parametrize("role", [SOURCE, GENERATE, MAIN, inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM])
def test_no_room_for_message_envelope_refuses_before_the_factory(provider, role):
    scenario, config = provider.scenario, _config()
    assert inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config) is True
    assert inc.append(scenario.topic, scenario.old, [B], scenario.files, config) == scenario.candidate
    reference = next(call for call in provider.calls if call["options"]["system"] == role)
    # One remaining byte cannot encode a system/user message envelope. This
    # test deliberately does not assert the implementation's reserve constant.
    payload_bytes = len((role + reference["prompt"]).encode())
    config["llm"]["num_ctx"] = payload_bytes + reference["options"]["num_predict"] + 1
    provider.calls.clear()
    provider.constructors.clear()
    with pytest.raises(inc.Refused, match="context budget"):
        if role == SOURCE:
            inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config)
        elif role == GENERATE:
            inc.append(scenario.topic, scenario.old, [B], scenario.files, config)
        elif role == MAIN:
            _validate(scenario, config)
        else:
            # Reuse the exact real stage request. Otherwise the support stage's
            # own admission could mask the topic stage's envelope refusal.
            inc.local_complete(reference["prompt"], config, system=role,
                               num_predict=reference["options"]["num_predict"],
                               temperature=reference["options"]["temperature"], think=False)
    assert provider.constructors == [] and provider.calls == []


@pytest.mark.parametrize("context", ["omitted", None, 0, "16384"])
def test_guard_and_factory_share_effective_context_without_mutating_caller(provider, context):
    # Root's explicit context-consistency fix normalizes the cloned config
    # after admission. This is the corrected contract, not evidence that the
    # earlier provider accepted a literal None context.
    config, scenario = _config(context=context), provider.scenario
    original_config = copy.deepcopy(config)
    assert inc.source_relevance(scenario.topic, scenario.old, B, scenario.files[B], config) is True
    _validate(scenario, config)
    assert [call["options"]["system"] for call in provider.calls] == [SOURCE, MAIN, inc.SUPPORT_AUDIT_SYSTEM,
                                                                    inc.TOPIC_AUDIT_SYSTEM]
    effective = provider.constructors[0]["llm"]["num_ctx"]
    assert type(effective) is int and effective > 0
    assert all(config_copy["llm"]["num_ctx"] == effective for config_copy in provider.constructors)
    assert all(call["context"] == effective for call in provider.calls)
    assert config == original_config
    if context == "16384":
        assert effective == int(context)
    _assert_finite_json_profiles(provider.calls, config)
    provider.constructors.clear()
    provider.calls.clear()
    with pytest.raises(inc.Refused, match="context budget"):
        inc.local_complete(json.dumps({"request": "x" * (effective + 1)}), config, system=PROFILE, num_predict=96)
    assert provider.constructors == [] and provider.calls == []


def test_explicit_none_context_still_rejects_overflow_before_constructor(provider):
    with pytest.raises(inc.Refused, match="context budget"):
        inc.local_complete(json.dumps({"request": "x" * 20000}), _config(context=None), system=PROFILE, num_predict=96)
    assert provider.constructors == [] and provider.calls == []


@pytest.mark.parametrize("stage", ["support", "topic"])
@pytest.mark.parametrize("failure", ["reject", "uncertain", "coerced-true", "combined-schema",
                                    "duplicate-key", "malformed", "truncated-output", "timeout"])
def test_real_transport_and_strict_stage_parser_stop_on_either_failure(provider, stage, failure):
    role = inc.SUPPORT_AUDIT_SYSTEM if stage == "support" else inc.TOPIC_AUDIT_SYSTEM
    flag = "supported" if stage == "support" else "relevant"
    answer = {"verdict": "accept", flag: True, "reason": "Synthetic stage response."}
    if failure in ("reject", "uncertain"):
        answer["verdict"] = failure
    elif failure == "coerced-true":
        answer[flag] = 1
    elif failure == "combined-schema":
        answer["relevant" if stage == "support" else "supported"] = True
    elif failure == "duplicate-key":
        answer = json.dumps(answer)[:-1] + ', "reason":"second reason"}'
    elif failure == "malformed":
        answer = "not JSON"
    elif failure == "truncated-output":
        answer["reason"] = "x" * 1000
    else:
        answer = TimeoutError("Synthetic bounded provider timeout.")
    provider.responses[role] = answer
    with pytest.raises((inc.Refused, TimeoutError)):
        _validate(provider.scenario, _config())
    expected = [MAIN, inc.SUPPORT_AUDIT_SYSTEM]
    if stage == "topic":
        expected.append(inc.TOPIC_AUDIT_SYSTEM)
    assert [call["options"]["system"] for call in provider.calls] == expected
    assert all(OLD_SOURCE_ONLY not in call["prompt"] for call in provider.calls)
    _assert_finite_json_profiles(provider.calls, _config())
