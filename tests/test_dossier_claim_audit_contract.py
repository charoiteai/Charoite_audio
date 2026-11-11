"""Aggregate, exact evidence and separate support/topic audit contracts.

All judgments here are explicit transport doubles, never real model results.
Writer scenarios reuse the invented incremental fixture and production locks,
backup, membership, CAS and index paths.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import nightly_dossier as night
import dossier_incremental as inc
from charoite_graph import dossier, frontmatter
from charoite_schema import CHAROITE
from test_dossier_incremental import world as world, _new, LOCAL
from test_dossier_model_contract import _old, _claim, _verdict, BODY, ADDED, A, B

C = "2099-01-03_100000"
FACT = "A rain cover was fitted."
SUPPORT_ACCEPT = {"verdict": "accept", "supported": True, "reason": "Synthetic independent source support."}
TOPIC_ACCEPT = {"verdict": "accept", "relevant": True, "reason": "Synthetic independent topic relationship."}
MAIN_SYSTEM = "You are an independent conservative dossier judge."
SOURCE_SYSTEM = "You judge source relevance conservatively."
GENERATE_SYSTEM = "You extend factual dossiers conservatively."
CASES = json.loads((Path(__file__).parent / "fixtures/dossier_claim_audit_cases.json").read_text(encoding="utf-8"))["cases"]


def _transport(monkeypatch, aggregate, *, support=SUPPORT_ACCEPT, topic=TOPIC_ACCEPT,
               configs=None):
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        if configs is not None:
            configs.append(config)
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append((system, payload, kwargs, prompt))
        if system == inc.SUPPORT_AUDIT_SYSTEM:
            answer = support
        elif system == inc.TOPIC_AUDIT_SYSTEM:
            answer = topic
        else:
            assert system == MAIN_SYSTEM, "Unexpected role reached the claim-audit transport."
            answer = aggregate
        return answer if isinstance(answer, str) else json.dumps(answer)
    monkeypatch.setattr(inc, "local_complete", complete)
    return calls


def _stage_response(stage):
    return dict(SUPPORT_ACCEPT if stage == "support" else TOPIC_ACCEPT)


def _direct_audit(monkeypatch, stage, response):
    calls = _transport(monkeypatch, _verdict(), **{stage: response})
    with pytest.raises(inc.Refused):
        inc.audit_claim("Glasshouse", _old(), _claim(), {}, quote_context=FACT + "\nComplete source tail.\n")
    expected = [inc.SUPPORT_AUDIT_SYSTEM]
    if stage == "topic":
        expected.append(inc.TOPIC_AUDIT_SYSTEM)
    assert [call[0] for call in calls] == expected
    return calls


@pytest.mark.parametrize("stage", ["support", "topic"])
@pytest.mark.parametrize("field,value", [("verdict", "reject"), ("verdict", "uncertain"),
    ("verdict", True), ("verdict", None), ("flag", False), ("flag", 0), ("flag", 1),
    ("flag", "true"), ("flag", None), ("reason", ""), ("reason", "   "),
    ("reason", None), ("reason", 1), ("extra", True)])
def test_each_stage_requires_exact_schema_and_literal_true(monkeypatch, stage, field, value):
    response = _stage_response(stage)
    response[({"support": "supported", "topic": "relevant"}[stage] if field == "flag" else field)] = value
    calls = _direct_audit(monkeypatch, stage, response)
    assert calls[-1][1]["source_context"] == FACT + "\nComplete source tail.\n"


@pytest.mark.parametrize("stage", ["support", "topic"])
@pytest.mark.parametrize("missing", ["verdict", "flag", "reason"])
def test_missing_stage_field_is_retryable_and_support_refusal_stops_topic(monkeypatch, stage, missing):
    response = _stage_response(stage)
    response.pop({"support": "supported", "topic": "relevant"}[stage] if missing == "flag" else missing)
    calls = _direct_audit(monkeypatch, stage, response)
    assert calls[-1][1]["source_context"] == FACT + "\nComplete source tail.\n"


@pytest.mark.parametrize("stage", ["support", "topic"])
@pytest.mark.parametrize("raw", ["not JSON", "null", "[]", "{}", json.dumps(_verdict()),
    '{"verdict":"accept","supported":true,"relevant":true,"reason":"legacy combined schema"}'])
def test_malformed_aggregate_or_old_combined_schema_cannot_pass_either_stage(monkeypatch, stage, raw):
    calls = _direct_audit(monkeypatch, stage, raw)
    assert calls[-1][1]["source_context"] == FACT + "\nComplete source tail.\n"


@pytest.mark.parametrize("stage", ["support", "topic"])
@pytest.mark.parametrize("duplicate", ["verdict", "flag", "reason"])
def test_duplicate_json_key_cannot_be_resolved_into_a_stage_acceptance(monkeypatch, stage, duplicate):
    response = _stage_response(stage)
    field = {"support": "supported", "topic": "relevant"}[stage] if duplicate == "flag" else duplicate
    raw = json.dumps(response)[:-1] + ", " + json.dumps(field) + ": " + json.dumps(response[field]) + "}"
    calls = _direct_audit(monkeypatch, stage, raw)
    assert calls[-1][1]["source_context"] == FACT + "\nComplete source tail.\n"


def test_complete_source_context_is_a_required_keyword_only_argument(monkeypatch):
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(TypeError):
        inc.audit_claim("Glasshouse", _old(), _claim(), {})
    with pytest.raises(TypeError):
        inc.audit_claim("Glasshouse", _old(), _claim(), {}, FACT)
    assert calls == []


@pytest.mark.parametrize("context", [None, "", "   "])
def test_missing_complete_context_cannot_fall_back_to_the_selected_quote(monkeypatch, context):
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused):
        inc.audit_claim("Glasshouse", _old(), _claim(), {}, quote_context=context)
    assert calls == []


def test_large_complete_source_audit_uses_bounded_context_without_losing_evidence(monkeypatch):
    source = "Remote qualification. " * 1100 + FACT + "\nLater qualification remains in source."
    configs = []
    original_config = {}
    calls = _transport(monkeypatch, _verdict(), configs=configs)
    inc.audit_claim("Glasshouse", _old(), _claim(), original_config, quote_context=source)
    audit_calls = [call for call in calls if call[0] in (inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM)]
    assert len(audit_calls) == 2
    assert all(call[1]["source_context"] == source for call in audit_calls)
    assert [config["llm"]["num_ctx"] for config in configs] == [32768, 32768]
    assert original_config == {}


def test_oversized_complete_audit_refuses_before_transport(monkeypatch):
    calls = _transport(monkeypatch, _verdict())
    reasons = []
    monkeypatch.setattr(inc.telemetry, "refusal", reasons.append)
    with pytest.raises(inc.Refused, match="maximum context budget"):
        inc.audit_claim("Glasshouse", _old(), _claim(), {},
                        quote_context=FACT + "\n" + "Outside source. " * 5000)
    assert calls == []
    assert reasons == [inc.telemetry.Reason.CONTEXT]


def test_oversized_configured_audit_window_refuses_before_transport(monkeypatch):
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused, match="maximum context budget"):
        inc.audit_claim("Glasshouse", _old(), _claim(), {"llm": {"num_ctx": 131072}},
                        quote_context=FACT + "\nComplete source tail.")
    assert calls == []


@pytest.mark.parametrize("config,source", [
    ({}, FACT + "\n" + "Outside source. " * 5000),
    ({"llm": {"num_ctx": 131072}}, FACT + "\nComplete source tail."),
])
def test_oversized_audit_survives_nightly_refusal_classification(monkeypatch, config, source):
    calls = _transport(monkeypatch, _verdict())
    collector = inc.telemetry.Collector()
    with collector.activate(), collector.topic():
        with pytest.raises(inc.Refused, match="maximum context budget"):
            inc.audit_claim("Glasshouse", _old(), _claim(), config, quote_context=source)
        inc.telemetry.request_refusal(inc.telemetry.Reason.VALIDATION)
        collector.complete_topic(attempted=True, committed=0, refused=1)
    assert collector.snapshot()["final_reasons"] == {"context_budget": 1}
    assert calls == []


def test_audit_context_steps_cover_exact_char_and_output_reserves():
    longest = max(len(inc.SUPPORT_AUDIT_SYSTEM), len(inc.TOPIC_AUDIT_SYSTEM))
    assert inc._audit_config({}, "x")["llm"]["num_ctx"] == 8192
    # 5936 input tokens + 2000 output + 256 envelope = exactly 8192.
    assert inc._audit_config({}, "x" * (11872 - longest))["llm"]["num_ctx"] == 8192
    assert inc._audit_config({}, "x" * (11874 - longest))["llm"]["num_ctx"] == 16384


@pytest.mark.parametrize("stage", ["support", "topic"])
def test_a_stage_cannot_substitute_the_other_roles_boolean(monkeypatch, stage):
    response = _stage_response(stage)
    own, other = ("supported", "relevant") if stage == "support" else ("relevant", "supported")
    response[other] = response.pop(own)
    calls = _direct_audit(monkeypatch, stage, response)
    assert calls[-1][1]["source_context"] == FACT + "\nComplete source tail.\n"


@pytest.mark.parametrize("proof", ["one-clause", "both-clauses"])
def test_aggregate_true_and_full_text_coverage_cannot_override_either_independent_rejection(monkeypatch, proof):
    foreign = "A level sensor was fitted to the WaterTank."
    complete_claim = FACT + "; " + foreign
    candidate = ADDED.replace(FACT, complete_claim)
    quote = FACT if proof == "one-clause" else complete_claim
    verdict = _verdict([_claim(complete_claim, quote=quote)])
    support = SUPPORT_ACCEPT if proof == "both-clauses" else SUPPORT_ACCEPT | {
        "verdict": "reject", "supported": False, "reason": "Synthetic missing support for the second assertion."}
    topic = TOPIC_ACCEPT | {"verdict": "reject", "relevant": False,
                            "reason": "Synthetic independent foreign assertion refusal."}
    calls = _transport(monkeypatch, verdict, support=support, topic=topic)
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": quote}}, {}, candidate, [])
    expected = [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM]
    if proof == "both-clauses":
        expected.append(inc.TOPIC_AUDIT_SYSTEM)
    assert [call[0] for call in calls] == expected
    assert all(call[1]["candidate_text"] == complete_claim and call[1]["source_quotation"] == quote
               for call in calls[1:])


def test_valid_addition_runs_separate_audits_with_complete_source_and_prior_dossier(monkeypatch):
    source = "Unrelated preceding source line.\n[[Ядра/Glasshouse]] " + FACT + "\nUnrelated following source line.\n"
    old = _old()
    calls = _transport(monkeypatch, _verdict())
    inc.validate_candidate("Glasshouse", old, [B], {B: {"text": source}}, {}, ADDED, [])
    assert [call[0] for call in calls] == [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM]
    assert inc.SUPPORT_AUDIT_SYSTEM != inc.TOPIC_AUDIT_SYSTEM
    for _, payload, options, _ in calls[1:]:
        assert payload == {"topic": "Glasshouse", "prior_dossier": old.parsed.body,
                           "candidate_text": FACT, "source_quotation": FACT, "source_context": source}
        assert type(options["num_predict"]) is int and options["num_predict"] > 0
        assert "Unrelated preceding" in payload["source_context"] and "Unrelated following" in payload["source_context"]
        assert "author_notes" not in payload and "new_sources" not in payload


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case"])
def test_invented_case_outcomes_follow_support_then_topic_without_borrowing_old_source(monkeypatch, case):
    """The labels are explicit doubles: this verifies routing and complete inputs,
    not whether a real model has learned any of these semantic distinctions.
    """
    prior = BODY
    if "prior_history_note" in case:
        prior = prior.replace("## Как пришли\n—", "## Как пришли\n- " + case["prior_history_note"])
    old = _old(prior)
    candidate = prior.replace("## Как пришли", f"- {case['candidate']} [[{B}]]\n## Как пришли")
    support = SUPPORT_ACCEPT | {"verdict": "accept" if case["expected_support"] else "reject",
                                "supported": case["expected_support"]}
    topic = TOPIC_ACCEPT | {"verdict": "accept" if case["expected_topic"] else "reject",
                            "relevant": case["expected_topic"]}
    claim = _claim(case["candidate"], quote=case["quote"])
    calls = _transport(monkeypatch, _verdict([claim]), support=support, topic=topic)
    files = {A: {"text": "OLD_SOURCE_A_MUST_NOT_PROVE_THIS_NEW_EVENT"}, B: {"text": case["source"]}}
    if case["expected_support"] and case["expected_topic"]:
        inc.validate_candidate("Glasshouse", old, [B], files, {}, candidate, [])
    else:
        with pytest.raises(inc.Refused, match="independent claim audit"):
            inc.validate_candidate("Glasshouse", old, [B], files, {}, candidate, [])
    expected = [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM]
    if case["expected_support"]:
        expected.append(inc.TOPIC_AUDIT_SYSTEM)
    assert [call[0] for call in calls] == expected
    for _, payload, _, prompt in calls[1:]:
        assert payload == {"topic": "Glasshouse", "prior_dossier": prior,
                           "candidate_text": case["candidate"], "source_quotation": case["quote"],
                           "source_context": case["source"]}
        assert "OLD_SOURCE_A_MUST_NOT_PROVE_THIS_NEW_EVENT" not in prompt


@pytest.mark.parametrize("quote,expected", [
    ("Vent A is closed.", "It is not confirmed that Vent A is closed.\n"),
    ("Vent A is closed.", "Vent A is closed. This statement is only a tentative hypothesis.\n"),
])
def test_support_audit_sees_negation_or_qualifiers_outside_the_selected_quote(monkeypatch, quote, expected):
    candidate = ADDED.replace(FACT, quote)
    files = {B: {"text": "Other topic before.\n" + expected + "Other topic after.\n"}}
    calls = _transport(monkeypatch, _verdict([_claim(quote, quote=quote)]), support=SUPPORT_ACCEPT | {
        "verdict": "uncertain", "supported": False, "reason": "Synthetic quotation qualification refusal."})
    with pytest.raises(inc.Refused, match="independent claim audit"):
        inc.validate_candidate("Glasshouse", _old(), [B], files, {}, candidate, [])
    assert [call[0] for call in calls] == [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM]
    assert calls[1][1]["source_quotation"] == quote
    assert calls[1][1]["source_context"] == files[B]["text"]


def test_multiline_evidence_anchor_keeps_the_complete_source_context(monkeypatch):
    quote = "A rain cover\nwas fitted."
    source = "Unrelated first line.\n[[Ядра/Glasshouse]] " + quote + " Done today.\nUnrelated last line.\n"
    calls = _transport(monkeypatch, _verdict([_claim(quote=quote)]))
    inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, ADDED, [])
    assert [call[0] for call in calls] == [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM]
    assert all(call[1]["source_quotation"] == quote and call[1]["source_context"] == source
               for call in calls[1:])


def test_repeated_literal_quote_is_refused_before_either_independent_stage(monkeypatch):
    source = f"[[Ядра/Glasshouse]] {FACT}\n[[Ядра/WaterTank]] {FACT}\n"
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": source}}, {}, ADDED, [])
    assert len(calls) == 1


def test_every_anchor_is_checked_before_the_first_support_or_topic_call(monkeypatch):
    second = "A synthetic drain was cleared."
    candidate = ADDED.replace("## Как пришли", f"- {second} [[{C}]]\n## Как пришли")
    calls = _transport(monkeypatch, _verdict([_claim(), _claim(second, quote=second, source=C)]))
    files = {B: {"text": FACT}, C: {"text": f"{second}\nOther object.\n{second}\n"}}
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc.validate_candidate("Glasshouse", _old(), [B, C], files, {}, candidate, [])
    assert [call[0] for call in calls] == [MAIN_SYSTEM]


def test_two_new_sources_can_prove_the_same_new_claim_with_separate_quotes(monkeypatch):
    second_quote = "A rain cover was fitted today."
    candidate = ADDED.replace(f"[[{B}]]", f"[[{B}]] [[{C}]]")
    claims = [_claim(), _claim(source=C, quote=second_quote)]
    calls = _transport(monkeypatch, _verdict(claims))
    inc.validate_candidate("Glasshouse", _old(), [B, C],
                           {B: {"text": FACT}, C: {"text": second_quote}}, {}, candidate, [])
    assert [call[1]["source_quotation"] for call in calls[1:]] == [FACT, FACT, second_quote, second_quote]
    assert [call[0] for call in calls] == [MAIN_SYSTEM, inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM,
                                         inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM]


def test_all_claim_audits_share_the_largest_required_window(monkeypatch):
    second_quote = "A rain cover was fitted today."
    candidate = ADDED.replace(f"[[{B}]]", f"[[{B}]] [[{C}]]")
    claims = [_claim(), _claim(source=C, quote=second_quote)]
    long_source = "Unrelated qualification. " * 900 + second_quote
    configs = []
    calls = _transport(monkeypatch, _verdict(claims), configs=configs)
    inc.validate_candidate("Glasshouse", _old(), [B, C],
                           {B: {"text": FACT}, C: {"text": long_source}}, {}, candidate, [])
    assert [config["llm"]["num_ctx"] for config in configs[1:]] == [16384] * 4
    assert [call[1]["source_context"] for call in calls[1:]] == [FACT, FACT, long_source, long_source]


def test_repeating_a_text_source_pair_is_refused_even_with_a_different_quote(monkeypatch):
    second_quote = "A rain cover was fitted today."
    calls = _transport(monkeypatch, _verdict([_claim(), _claim(quote=second_quote)]))
    with pytest.raises(inc.Refused, match="duplicate candidate claim"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": FACT + " " + second_quote}}, {}, ADDED, [])
    assert len(calls) == 1


def test_a_citation_to_another_accepted_source_does_not_supply_its_contribution(monkeypatch):
    candidate = ADDED.replace(f"[[{B}]]", f"[[{B}]] [[{C}]]")
    calls = _transport(monkeypatch, _verdict())
    with pytest.raises(inc.Refused, match="contribution"):
        inc.validate_candidate("Glasshouse", _old(), [B, C],
                               {B: {"text": FACT}, C: {"text": FACT}}, {}, candidate, [])
    assert len(calls) == 1


def test_no_new_sources_and_no_new_text_needs_no_claim_audit(monkeypatch):
    calls = _transport(monkeypatch, _verdict([]))
    inc.validate_candidate("Glasshouse", _old(), [], {}, {}, BODY, [])
    assert len(calls) == 1


@pytest.fixture
def writer_world(world, tmp_path, monkeypatch):
    _, graph, path = world
    monkeypatch.setattr(night, "_root", lambda: tmp_path)
    monkeypatch.setattr(night.graphs, "load_config", lambda: {})
    monkeypatch.setattr(night, "generator_label", lambda config: "")
    return graph, path


@pytest.mark.parametrize("stage,failure", [("main", "no-contribution"),
    ("support", "reject"), ("support", "uncertain"), ("support", "timeout"), ("support", "malformed"),
    ("topic", "reject"), ("topic", "uncertain"), ("topic", "timeout"), ("topic", "malformed")])
def test_writer_keeps_body_membership_index_and_backups_and_retries_either_stage_failure(
        writer_world, monkeypatch, stage, failure):
    graph, path = writer_world
    _new(graph)
    before = path.read_bytes()
    dossier.rebuild_index(graph, schema=CHAROITE)
    index_before = {name: (path.parent / name).read_bytes() for name in (dossier.INDEX_JSON, dossier.INDEX_MD)}
    calls = []
    def complete(prompt, config, *, system, **kwargs):
        calls.append(system)
        if system == SOURCE_SYSTEM:
            return json.dumps({"verdict": "relevant", "reason": "Synthetic supported source.", "quote": FACT})
        if system == GENERATE_SYSTEM:
            body = dossier.load_dossier(path).parsed.body
            body = body + f"\n[[{B}]]" if failure == "no-contribution" else body.replace(
                "## Как пришли", f"- {FACT} [[{B}]]\n## Как пришли")
            return json.dumps({"body": body, "changes": []})
        if system in (inc.SUPPORT_AUDIT_SYSTEM, inc.TOPIC_AUDIT_SYSTEM):
            current_stage = "support" if system == inc.SUPPORT_AUDIT_SYSTEM else "topic"
            response = _stage_response(current_stage)
            if current_stage == stage:
                if failure == "timeout":
                    raise TimeoutError("Synthetic independent audit timeout.")
                if failure == "malformed":
                    return "not JSON"
                response["verdict"] = failure
                response["supported" if stage == "support" else "relevant"] = False
            return json.dumps(response)
        assert system == MAIN_SYSTEM
        return json.dumps(_verdict([] if failure == "no-contribution" else [_claim()]))
    monkeypatch.setattr(inc, "local_complete", complete)
    attempt = [GENERATE_SYSTEM, MAIN_SYSTEM]
    if stage != "main":
        attempt.append(inc.SUPPORT_AUDIT_SYSTEM)
    if stage == "topic":
        attempt.append(inc.TOPIC_AUDIT_SYSTEM)
    # Refused candidates get the existing bounded correction attempt. Transport
    # timeouts propagate to the writer; the next night must still retry them.
    expected = [SOURCE_SYSTEM] + attempt * (1 if failure == "timeout" else 2)
    for _ in range(2):
        calls.clear()
        result = night.run(graph, LOCAL, full=False, dry=False, limit=None)
        assert result["собрано"] == 0 and result["отказы"] == 1
        assert calls == expected
        assert path.read_bytes() == before
        assert dossier.composition(path.read_text(), schema=CHAROITE) == [A]
        assert frontmatter.list_field(path.read_text(), dossier.REJECTED_FIELD) == []
        assert frontmatter.list_field(path.read_text(), dossier.REJECTION_VERSIONS_FIELD) == []
        assert {name: (path.parent / name).read_bytes() for name in index_before} == index_before
        assert not (path.parent / ".backup").exists()


@pytest.mark.parametrize("attribute", ["cloud_ready", "engine", "base", "_fell_back_local"])
def test_missing_supported_transport_attribute_is_an_explicit_refusal(monkeypatch, attribute):
    import llm
    class MissingAttribute:
        def __init__(self, config):
            self.cloud_ready, self.engine, self.base, self._fell_back_local = False, "ollama", "http://127.0.0.1:11434", False
            delattr(self, attribute)
        def stream(self, *args, **kwargs):
            pytest.fail("Unsupported adapter reached the transport.")
    monkeypatch.setattr(llm, "LLM", MissingAttribute)
    with pytest.raises(inc.Refused, match="missing transport attributes"):
        inc.local_complete("source", LOCAL, system="judge")


@pytest.mark.parametrize("cloud_ready,engine,base", [(True, "ollama", "http://127.0.0.1:11434"),
                                                    (False, "cloud", "http://127.0.0.1:11434"),
                                                    (False, "ollama", "https://example.invalid")])
def test_actual_adapter_transport_is_checked_again_after_local_factory(monkeypatch, cloud_ready, engine, base):
    import llm
    class WrongTransport:
        def __init__(self, config):
            self.cloud_ready, self.engine, self.base, self._fell_back_local = cloud_ready, engine, base, False
        def stream(self, *args, **kwargs):
            pytest.fail("Unexpected transport was used.")
    monkeypatch.setattr(llm, "LLM", WrongTransport)
    with pytest.raises(inc.Refused):
        inc.local_complete("source", LOCAL, system="judge")


def test_stage_config_boundaries_and_auto_stepping(monkeypatch):
    longest = max(len(inc.SUPPORT_AUDIT_SYSTEM), len(inc.TOPIC_AUDIT_SYSTEM))
    # Odd boundary 11873 chars -> ceil(11873 / 2) = 5937 + 2000 + 256 = 8193 -> 16384.
    assert inc._audit_config({"llm": {"num_ctx": 8192}}, "x" * (11873 - longest))["llm"]["num_ctx"] == 16384
    # Exact 32768 boundary: (32768 - 2000 - 256) * 2 = 61024 -> 32768; +1 -> Refused(CONTEXT).
    assert inc._audit_config({"llm": {"num_ctx": 8192}}, "x" * (61024 - longest))["llm"]["num_ctx"] == 32768
    with pytest.raises(inc.Refused, match="maximum context budget") as over:
        inc._audit_config({"llm": {"num_ctx": 8192}}, "x" * (61025 - longest))
    assert over.value.reason == inc.telemetry.Reason.CONTEXT
    # Explicit small (< 8192) and negative windows are preserved for admission to refuse.
    assert inc._stage_config({"llm": {"num_ctx": 4096}}, 10, "x" * 14000, 350)["llm"]["num_ctx"] == 4096
    assert inc._stage_config({"llm": {"num_ctx": -1}}, 10, "x", 350)["llm"]["num_ctx"] == -1
    # Explicit large (> 32768) operator windows are preserved on non-audit stages up to their exact budget.
    max_64k_prompt = "x" * ((65536 - 350 - inc.CONTEXT_RESERVE) * inc.ADMISSION_CHARS_PER_TOKEN - 10)
    assert inc._stage_config({"llm": {"num_ctx": 65536}}, 10, "x", 350)["llm"]["num_ctx"] == 65536
    assert inc._stage_config({"llm": {"num_ctx": 65536}}, 10, max_64k_prompt, 350)["llm"]["num_ctx"] == 65536
    with pytest.raises(inc.Refused, match="maximum context budget") as over_64k:
        inc._stage_config({"llm": {"num_ctx": 65536}}, 10, max_64k_prompt + "x", 350)
    assert over_64k.value.reason == inc.telemetry.Reason.CONTEXT
    # Non-ollama configs pass through untouched so local_config enforces the local-only gate.
    cloud_cfg = {"llm": {"engine": "cloud", "model": "invented"}}
    assert inc._stage_config(cloud_cfg, 10, "x" * 70000, 350) is cloud_cfg
    with pytest.raises(inc.Refused, match="Ollama"):
        inc.source_relevance("Glasshouse", _old(), B, {"text": FACT}, cloud_cfg)
    # Malformed, non-finite float or boolean num_ctx refuses at preflight with Reason.PREFLIGHT.
    for bad in (True, False, "not-an-int", [], float("inf"), float("-inf"), float("nan")):
        with pytest.raises(inc.Refused, match="invalid configured context window") as bad_exc:
            inc._stage_config({"llm": {"num_ctx": bad}}, 10, "x", 350)
        assert bad_exc.value.reason == inc.telemetry.Reason.PREFLIGHT


def test_all_dossier_stages_auto_step_default_8192_window_and_skip_context_retry(monkeypatch):
    long_text = ("Подробная запись наблюдения по теплице. " * 380) + FACT
    files = {B: {"text": long_text}}
    cfg = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "invented", "num_ctx": 8192}}
    seen = []

    def complete(prompt, config, *, system, **kwargs):
        seen.append((system, config["llm"]["num_ctx"]))
        if system == SOURCE_SYSTEM:
            return json.dumps({"verdict": "relevant", "reason": "Supported topic fact.", "quote": FACT})
        if system == GENERATE_SYSTEM:
            return json.dumps({"body": ADDED, "changes": []})
        if system == MAIN_SYSTEM:
            return json.dumps(_verdict())
        if system == inc.SUPPORT_AUDIT_SYSTEM:
            return json.dumps(SUPPORT_ACCEPT)
        if system == inc.TOPIC_AUDIT_SYSTEM:
            return json.dumps(TOPIC_ACCEPT)
        assert system == dossier.gen_call()["system"]
        return ADDED

    monkeypatch.setattr(inc, "local_complete", complete)
    assert inc.source_relevance("Glasshouse", _old(), B, files[B], cfg) is True
    assert inc.append("Glasshouse", _old(), [B], files, cfg) == ADDED
    assert night.generate("Glasshouse", [B], {B: {**files[B], "kind": "Встречи"}}, cfg) == ADDED
    assert cfg["llm"]["num_ctx"] == 8192
    assert [ctx for _, ctx in seen] == [16384, 16384, 16384, 16384, 16384, 16384]

    # When downstream validation fails due to context budget, append must NOT burn a second attempt.
    gen_calls = []

    def overflow_downstream(prompt, config, *, system, **kwargs):
        gen_calls.append(system)
        return json.dumps({"body": ADDED, "changes": []})

    monkeypatch.setattr(inc, "local_complete", overflow_downstream)
    monkeypatch.setattr(
        inc, "validate_candidate",
        lambda *a, **k: (_ for _ in ()).throw(
            inc.Refused("complete dossier input exceeds the maximum context budget",
                        reason=inc.telemetry.Reason.CONTEXT)
        ),
    )
    with pytest.raises(inc.Refused, match="maximum context budget"):
        inc.append("Glasshouse", _old(), [B], {B: {"text": FACT}}, cfg)
    assert gen_calls == [GENERATE_SYSTEM]


def test_wikilink_display_quotes_work_across_stages_without_splicing_or_ambiguity(monkeypatch):
    table_source = "Шапка таблицы\n| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\nХвост\n"
    plain_quote = "| Glasshouse | A rain cover was fitted. |"
    raw_quote = "| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |"
    unescaped_quote = "| [[Ядра/Glasshouse|Glasshouse]] | A rain cover was fitted. |"
    # Plain display quote, verbatim raw wikilink quote, and dropped-backslash table wikilink quote all pass.
    for quote in (plain_quote, raw_quote, unescaped_quote):
        monkeypatch.setattr(
            inc, "local_complete",
            lambda *a, q=quote, **k: json.dumps({"verdict": "relevant", "reason": "Table row fact.", "quote": q}),
        )
        assert inc.source_relevance("Glasshouse", _old(), B, {"text": table_source}, {}) is True
    # Stem without alias ("[[Ядра/Glasshouse]]") also resolves to "Glasshouse".
    monkeypatch.setattr(
        inc, "local_complete",
        lambda *a, **k: json.dumps({"verdict": "relevant", "reason": "Stem quote.", "quote": "Тема Glasshouse обновлена."}),
    )
    assert inc.source_relevance("Glasshouse", _old(), B, {"text": "Тема [[Ядра/Glasshouse]] обновлена."}, {}) is True
    # Whitespace-only quote, empty/anchor wikilinks, unclosed [[ across newline, and fabricated targets fail.
    for bad_source, bad_quote in [
        ("A rain cover was fitted.", "   "),
        ("A [[Цель| ]] B", "A  B"),
        ("A [[Раздел/Имя\\| ]] B", "A  B"),
        ("A [[#Раздел]] B", "A  B"),
        ("A [[]] B", "A  B"),
        ("Первая [[незакрытая\nвторая]] строка", "Первая строка"),
        ("A Glasshouse B", "A [[Выдуманная/Glasshouse|Glasshouse]] B"),
    ]:
        monkeypatch.setattr(
            inc, "local_complete",
            lambda *a, q=bad_quote, **k: json.dumps({"verdict": "relevant", "reason": "Bad quote.", "quote": q}),
        )
        with pytest.raises(inc.Refused, match="uncertain or unsupported"):
            inc.source_relevance("Glasshouse", _old(), B, {"text": bad_source}, {})

    # Downstream validate_candidate (claims + changes + _quote_context) accepts unique plain and unescaped table quotes...
    for valid_quote in (plain_quote, unescaped_quote):
        calls = _transport(monkeypatch, _verdict([_claim(quote=valid_quote)]))
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": table_source}}, {}, ADDED, [])
        assert len(calls) == 3
    # ...and still refuses when two different wikilinks collapse to the same plain quote!
    ambiguous_table = (
        "| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\n"
        "| [[Архив/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\n"
    )
    _transport(monkeypatch, _verdict([_claim(quote=plain_quote)]))
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": ambiguous_table}}, {}, ADDED, [])
    # Distinct target wikilink quotes (both raw \| and unescaped |) on the two-row table remain unambiguous and pass!
    for specific_quote in (raw_quote, unescaped_quote):
        _transport(monkeypatch, _verdict([_claim(quote=specific_quote)]))
        inc.validate_candidate("Glasshouse", _old(), [B], {B: {"text": ambiguous_table}}, {}, ADDED, [])

    # Direct projection invariants: bytes inside hidden blocks stay verbatim (never stripped to empty).
    hidden_only = "Решили %%не%% ставить `[[Ядра/Glasshouse\\|крышу]]`.\n```md\n[[X|y]]\n```"
    assert inc._plain_wikilinks(hidden_only) == hidden_only
    assert inc._unescape_wikilinks(
        "`| [[Ядра/Glasshouse\\|Glasshouse]] |` и [[Ядра/Glasshouse\\|Glasshouse]]"
    ) == "`| [[Ядра/Glasshouse\\|Glasshouse]] |` и [[Ядра/Glasshouse|Glasshouse]]"
    # Splicing across Obsidian %%...%% comments or inline code is refused as missing evidence.
    for spliced_src, spliced_quote in (
        ("Решили %%не%% ставить крышу.", "Решили  ставить крышу."),
        ("A `[[X|y]]` B", "A  B"),
    ):
        with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
            inc._quote_context(spliced_src, spliced_quote)
    # Identical text inside and outside a fenced block is refused as ambiguous!
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc._quote_context("Glasshouse fitted.\n```\nGlasshouse fitted.\n```\n", "Glasshouse fitted.")

    # Direct predicate contracts: index-0 match, empty quote, raw folder substring, code/comment exclusions,
    # and mixed raw + wikilink ambiguity.
    assert inc._has_source_quote("| [[Ядра/Glasshouse\\|Glasshouse]] | ok |", "| Glasshouse | ok |") is True
    assert inc._has_source_quote("abc", "") is False
    with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
        inc._quote_context("abc", "")
    # Verbatim raw target path without "[[" still resolves when plain projection hides it behind an alias.
    assert inc._has_source_quote("Ссылка [[Ядра/Glasshouse|Витрина]] подтверждена.", "Ядра/Glasshouse") is True
    assert inc._quote_context("Ссылка [[Ядра/Glasshouse|Витрина]] подтверждена.", "Ядра/Glasshouse") == (
        "Ссылка [[Ядра/Glasshouse|Витрина]] подтверждена."
    )
    # Non-rendered wikilinks inside fenced code, inline code, or Obsidian %% comments are NOT stripped or unescaped.
    for non_rendered in (
        "```markdown\n| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\n```\n",
        "Пример `| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |` в коде.",
        "Комментарий %% | [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. | %% скрыт.",
    ):
        for candidate_quote in (plain_quote, unescaped_quote):
            assert inc._has_source_quote(non_rendered, candidate_quote) is False
            with pytest.raises(inc.Refused, match="lacks exact new-source evidence"):
                inc._quote_context(non_rendered, candidate_quote)
    # Mixed plain/unescaped/escaped duplicates and inline-code + rendered duplicates all refuse as ambiguous!
    mixed_table = (
        "| Glasshouse | A rain cover was fitted. |\n"
        "| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\n"
    )
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc._quote_context(mixed_table, plain_quote)
    escaped_and_unescaped = (
        "| [[Ядра/Glasshouse\\|Glasshouse]] | A rain cover was fitted. |\n"
        "| [[Ядра/Glasshouse|Glasshouse]] | A rain cover was fitted. |\n"
    )
    for dup_quote in (raw_quote, unescaped_quote):
        with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
            inc._quote_context(escaped_and_unescaped, dup_quote)
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc._quote_context("Код `[[Ядра/Glasshouse]]` и ссылка [[Ядра/Glasshouse]].", "[[Ядра/Glasshouse]]")
    # Truncated wikilink prefix/suffix quotes are judged via the raw branch (1 unique hit; 2 rows -> ambiguous).
    for partial_quote in ("[[Ядра/Glasshouse\\|Glasshouse", "Ядра/Glasshouse\\|Glasshouse]] | A rain"):
        assert inc._quote_hits(table_source, partial_quote) == 1
        assert inc._quote_context(table_source, partial_quote) == table_source
    with pytest.raises(inc.Refused, match="ambiguous claim evidence"):
        inc._quote_context(table_source + table_source, "[[Ядра/Glasshouse\\|Glasshouse")

    # Stage prompt contracts: open topic questions stay eligible for ## Открыто, and no prompt tail emits bare \|.
    captured_prompts = []

    def capture_prompt(prompt, config, *, system, **kwargs):
        captured_prompts.append((system, prompt.split("\n")[-1]))
        if system == SOURCE_SYSTEM:
            return json.dumps({"verdict": "relevant", "reason": "Open question.", "quote": plain_quote})
        if system == GENERATE_SYSTEM:
            return json.dumps({"body": ADDED, "changes": []})
        if system == MAIN_SYSTEM:
            return json.dumps(_verdict([_claim(quote=plain_quote)]))
        if system == inc.SUPPORT_AUDIT_SYSTEM:
            return json.dumps(SUPPORT_ACCEPT)
        assert system == inc.TOPIC_AUDIT_SYSTEM
        return json.dumps(TOPIC_ACCEPT)

    monkeypatch.setattr(inc, "local_complete", capture_prompt)
    assert inc.source_relevance("Glasshouse", _old(), B, {"text": table_source}, {}) is True
    assert inc.append("Glasshouse", _old(), [B], {B: {"text": table_source}}, {}) == ADDED
    tails_by_system = dict(captured_prompts[:3])
    assert "open question" in tails_by_system[SOURCE_SYSTEM]
    for stage_system in (SOURCE_SYSTEM, GENERATE_SYSTEM, MAIN_SYSTEM):
        assert "visible link text" in tails_by_system[stage_system]
        assert "valid JSON escaping" in tails_by_system[stage_system]
        assert r"\|" not in tails_by_system[stage_system]
