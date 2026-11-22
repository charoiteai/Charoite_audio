"""Synthetic evidence boundaries; tests never inspect implementation source."""
import json

import pytest

import debrief_links as links
import debrief_refinement as ref


@pytest.mark.parametrize("owned", [11, 12, 13])
def test_owned_quote_overlap_counts_exact_twelve_character_boundary(owned):
    quote = "ABCDEFGHIJKLMNOPQRSTUVWX"
    window = {"context_before": quote[:-owned], "text": quote[-owned:], "context_after": ""}
    assert links._owned_occurrences(quote, window) == (1 if owned >= 12 else 0)
    assert ref._owned_anchor(quote, window) is (owned >= 12)


@pytest.mark.parametrize("side", ["before", "after"])
def test_neighbor_only_quote_cannot_establish_an_owned_occurrence(side):
    quote = "abcdefghijkl"
    window = {"text": "Другая собственная речь.", "context_" + side: quote}
    assert links._owned_occurrences(quote, window) == 0
    assert not ref._owned_anchor(quote, window)


def test_repeated_twelve_character_anchor_cannot_be_published_as_unique():
    quote = "abcdefghijkl"
    late_quote = "mnopqrstuvwx"
    unique = "ABCDEFGHIJKL"
    early_window = {"text": quote + "\n" + quote + "\n" + unique}
    late_window = {"text": late_quote}
    early = {"text": "Повторённая реплика с отдельной уникальной опорой.",
             "quotes": [quote, unique], "uncertain": False}
    late = {"text": "Поздняя реплика.", "quotes": [late_quote], "uncertain": False}
    relation = {"early_id": "early:p0", "late_id": "late:p0", "kind": "clarifies", "status": "candidate"}
    focused = []

    def call(prompt, system, predict):
        if system == links.ANCHOR_AUDIT_SYSTEM:
            focused.append(1)
            return json.dumps({"verdict": "supported"})
        assert system == links.PAIR_AUDIT_SYSTEM
        return json.dumps({**{key: relation[key] for key in ("early_id", "late_id", "kind")},
            "verdict": "supported", "early_indices": [0], "late_indices": [0],
            "early_quotes": [], "late_quotes": []})

    assert links._owned_occurrences(quote, early_window) == 2
    with pytest.raises(ref.RefinementRefused, match="link_ambiguous_source_anchor"):
        links._verify(relation, (early, early_window), (late, late_window), call)
    assert focused == []


@pytest.mark.parametrize("size", [11, 12, 13])
def test_source_evidence_requires_at_least_twelve_trimmed_characters(size):
    quote = "x" * size
    row = {"quotes": [quote]}
    value = {"early_indices": [0], "early_quotes": []}
    window = {"text": quote}
    if size < 12:
        with pytest.raises(ref.RefinementRefused, match="link_source_quotes"):
            links._selected(value, "early", row, window, "supported")
    else:
        assert links._selected(value, "early", row, window, "supported") is None


def test_padding_does_not_satisfy_owned_evidence_length():
    quote = "  abcdefghijk  "
    window = {"text": quote}
    assert links._owned_occurrences(quote, window) == 0
    assert not ref._owned_anchor(quote, window)


def export_payload():
    import hashlib
    speech = "Сначала обсуждали проверку макета."
    return {"version": 1, "session_epoch": "2026-01-01_120001", "request_id": "close-2026-01-01_120001",
            "snapshot": {"complete": True, "source_drain_failure": None, "producer_cutoff_failure": None,
                         "source": {"stopped": True, "portions": [{"id": "p0", "body_utf8": speech,
                             "body_hash": hashlib.sha256(speech.encode()).hexdigest()}]},
                         "blocks": [{"serial": 0, "kind": "paragraph", "text": speech}]}}


@pytest.mark.parametrize("patch", [{"serial": True}, {"serial": "0"}, {"kind": "invented"}, {"text": 7}])
def test_export_rejects_each_independently_invalid_block_field(patch):
    payload = export_payload()
    payload["snapshot"]["blocks"][0].update(patch)
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(payload, "2026-01-01_120001")


def test_export_rejects_nondictionary_block_with_closed_reason():
    payload = export_payload()
    payload["snapshot"]["blocks"] = [None]
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(payload, "2026-01-01_120001")


def test_export_rejects_duplicate_serial_even_when_every_other_field_is_valid():
    payload = export_payload()
    payload["snapshot"]["blocks"].append(dict(payload["snapshot"]["blocks"][0]))
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(payload, "2026-01-01_120001")


@pytest.mark.parametrize("binding", [None, [], {}, {"status": "pending"}, {"status": False}])
def test_thread_binding_must_be_ready_before_reading_any_export(tmp_path, monkeypatch, binding):
    def forbidden_read(path):
        pytest.fail("an unready binding must not open an export")
    monkeypatch.setattr(ref, "read_snapshot", forbidden_read)
    with pytest.raises(ref.RefinementRefused, match="export_not_ready"):
        ref.load_thread(tmp_path / "2026-01-01_120001.md", binding)


def test_empty_draft_is_rejected_by_default_but_explicit_noise_window_is_allowed():
    raw = json.dumps({"paragraphs": []})
    with pytest.raises(ref.RefinementRefused, match="paragraph_schema"):
        ref._paragraphs(raw, "")
    assert ref._paragraphs(raw, "", allow_empty=True) == []


def fake_local(monkeypatch, *, context=1000, failure=None, configs=None):
    import llm
    from types import SimpleNamespace
    calls = []
    class Client:
        num_ctx = context
        def __init__(self, cfg):
            if configs is not None:
                configs.append(cfg)
        def grounded_answer(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            if failure is not None:
                raise failure
            return iter(["{}"])
    monkeypatch.setattr(llm, "LLM", Client)
    monkeypatch.setattr(ref.LocalLoopbackAuthority, "verify", lambda client: SimpleNamespace(model="synthetic-local"))
    return calls


def test_local_context_admits_exact_limit_and_rejects_one_character_over(monkeypatch):
    calls = fake_local(monkeypatch)
    cfg = {"llm": {"engine": "ollama"}}
    # Contract: num_ctx=1000, output=100, reserve=512, two chars/token.
    assert ref.local_request(cfg, "p" * 775, "s", 100) == "{}"
    assert len(calls) == 1
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref.local_request(cfg, "p" * 776, "s", 100)
    assert len(calls) == 1


@pytest.mark.parametrize("status,detail,reason", [
    (400, "context window exceeded", "complete_input_exceeds_context"),
    (400, "unrelated provider failure", "local_request_failed"),
    (200, "stopped at token budget", "output_budget"),
    (200, "unrelated provider failure", "local_request_failed"),
])
def test_only_explicit_provider_limits_authorize_lossless_split(monkeypatch, status, detail, reason):
    import llm
    calls = fake_local(monkeypatch, failure=llm.LLMHTTPError(status, detail))
    with pytest.raises(ref.RefinementRefused, match="^" + reason + "$"):
        ref.local_request({"llm": {"engine": "ollama"}}, "prompt", "system", 100)
    assert len(calls) == 1


@pytest.mark.parametrize("speech,size", [("ab\ncd", 5), ("ab cd", 5), ("abcde", 5), ("abc", 8)])
def test_complete_final_window_is_not_split_again(speech, size):
    windows = ref._windows(speech, size)
    assert [window["text"] for window in windows] == [speech]
    assert [(window["start"], window["end"]) for window in windows] == [(0, len(speech))]


@pytest.mark.parametrize("speech", ["\na", " a", "\n a"])
def test_single_character_windows_preserve_every_character(speech):
    windows = ref._windows(speech, 1)
    assert [window["text"] for window in windows] == list(speech)
    assert all(window["end"] - window["start"] == 1 for window in windows)


def test_neighbor_metadata_includes_header_ending_at_first_owned_character(monkeypatch):
    monkeypatch.setattr(ref, "NEIGHBOR_CHARS", 0)
    header = "**Участник A** [00:01]:"
    speech = header + "\nДальнейшая собственная речь."
    start = len(header) - 1
    window = {"start": start, "end": len(speech), "text": speech[start:]}
    result = ref._with_neighbors(speech, window)
    assert result["context_speaker"] == "Участник A"
    assert result["speaker_at_start"] == "Участник A"


def test_neighbor_context_at_start_is_not_marked_clipped():
    speech = "Собственная речь без соседнего начала."
    result = ref._with_neighbors(speech, {"start": 0, "end": len(speech), "text": speech})
    assert result["context_clipped_left"] is False
    assert result["context_clipped_right"] is False
    assert ref._owned_anchor("Отсутствующая цитата.", result) is False


def test_lexical_candidate_exactly_fills_available_hint_budget():
    candidate = "word " + "x" * 1395
    result = ref._candidates("word", [{"text": candidate}, {"text": "word"}], "")
    assert result == {"live_thread": [candidate], "previous_debrief": []}


@pytest.mark.parametrize("size", [11, 12])
def test_correction_evidence_minimum_is_independent_of_membership(size):
    quote = "x" * size
    verdict = {"paragraphs": [], "coverage_complete": True, "corrections_resolved": True,
               "has_cross_window_correction": True, "correction_quotes": [quote], "repair_quotes": []}
    if size < 12:
        with pytest.raises(ref.RefinementRefused, match="unsupported_correction_quote"):
            ref._window_audit(json.dumps(verdict), 0, quote)
    else:
        assert ref._window_audit(json.dumps(verdict), 0, quote) == ([quote], None)


def test_ambiguous_feedback_limits_each_quote_excerpt_before_total_budget():
    rows = [{"text": "t" * 200, "quotes": ["q" * 200] * 3}]
    feedback = ref._anchor_feedback(rows, [0])
    assert [len(q) for q in feedback["examples"][0]["quote_excerpts"]] == [100, 100, 100]


def test_feedback_exact_serialized_budget_is_not_needlessly_shortened():
    examples = [{"index": i, "text_excerpt": "t" * 200, "quote_excerpts": ["q" * 100] * 2}
                for i in range(4)]
    # Tune synthetic content to the public serialized cap, independently of
    # implementation control flow; clipping must begin only above 1600 chars.
    for example in examples:
        while len(ref._dump(examples)) > 1600 and len(example["text_excerpt"]) > 1:
            example["text_excerpt"] = example["text_excerpt"][:-1]
    assert len(ref._dump(examples)) == 1600
    rows = [{"text": e["text_excerpt"], "quotes": e["quote_excerpts"]} for e in examples]
    assert ref._anchor_feedback(rows, [0, 1, 2, 3])["examples"] == examples


def test_adjacent_repaired_spans_are_ordered_without_being_treated_as_overlap():
    first, second = "abcdefghijkl", "mnopqrstuvwx"
    kept = [{"text": "Первый предмет.", "quotes": [first], "uncertain": False}]
    added = [{"text": "Второй предмет.", "quotes": [second], "uncertain": False}]
    window = {"text": first + second, "context_before": "", "context_after": ""}
    assert ref._merge_repair(kept, added, window) == kept + added


def test_window_with_correction_keeps_verified_paragraph_and_generation_budget():
    quote = "Срок теперь уточнили после проверки."
    row = {"text": quote, "quotes": [quote], "uncertain": False}
    window = {"text": quote, "context_before": "", "context_after": ""}
    calls = []
    def call(prompt, system, predict):
        calls.append((system, predict))
        if system == ref.WINDOW_SYSTEM:
            return json.dumps({"paragraphs": [row]})
        assert system == ref.WINDOW_AUDIT_SYSTEM
        return json.dumps({"paragraphs": [{"index": 0, "supported": True}],
            "coverage_complete": True, "corrections_resolved": True,
            "has_cross_window_correction": True, "correction_quotes": [quote], "repair_quotes": []})
    assert ref._refine_window(window, [], "", call) == [row]
    assert calls == [(ref.WINDOW_SYSTEM, 2000), (ref.WINDOW_AUDIT_SYSTEM, 1000)]


@pytest.mark.parametrize("thread,previous", [([], None), (None, ""), ([{"text": 4}], ""), ([None], "")])
def test_each_invalid_candidate_input_refuses_before_any_model_request(thread, previous):
    def forbidden(*args):
        pytest.fail("invalid candidates must not reach a model")
    with pytest.raises(ref.RefinementRefused, match="candidate_input"):
        ref.refine("Собственная речь для проверки.", thread, previous, {}, request=forbidden)


def test_refinement_context_requires_room_beyond_output_and_reserve():
    def forbidden(*args):
        pytest.fail("invalid context must not reach a model")
    with pytest.raises(ref.RefinementRefused, match="invalid_context_budget"):
        ref.refine("Собственная речь для проверки.", [], "", {"llm": {"num_ctx": 3512}}, request=forbidden)


def test_shared_call_accepts_exact_short_path_input_limit():
    speech = "Проверку макета проведут после обсуждения."
    data = {"final_speech": speech, "live_thread": [], "previous_debrief": ""}
    room = 9360 - len(ref.SYSTEM) - len(json.dumps(data, ensure_ascii=False))
    assert room > 0
    previous = "x" * room
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        if system == ref.SYSTEM:
            assert predict == 3000
            assert len(prompt) + len(system) == 9360
            return json.dumps({"paragraphs": [{"text": speech, "quotes": [speech], "uncertain": False}]})
        assert system == ref.AUDIT_SYSTEM
        assert predict == 1000
        return json.dumps({"paragraphs": [{"index": 0, "supported": True}],
                           "coverage_complete": True, "corrections_resolved": True})
    events = []
    ref.refine(speech, [], previous, {"llm": {"num_ctx": 8192}}, request=request, progress=events.append)
    assert calls == [ref.SYSTEM, ref.AUDIT_SYSTEM]
    assert events[-1]["phase"] == "complete" and events[-1]["source_windows"] == 1
    assert events[-1]["coverage_complete"] is True
    assert [event["source_windows"] for event in events if event["phase"] == "source_window"] == [1]
    assert all(event["semantic_proof"] is False for event in events)


def test_split_windows_recompute_speaker_from_canonical_speech(monkeypatch):
    speech = "**Участник A** [00:01]:\n" + "x" * 1200
    accepted = []
    def refine_window(window, thread, previous, call):
        if len(window["text"]) > 700:
            raise ref.RefinementRefused("complete_input_exceeds_context")
        accepted.append(window)
        return []
    monkeypatch.setattr(ref, "_refine_window", refine_window)
    ref._windowed(speech, [], "", lambda *args: pytest.fail("no model expected"), None)
    assert len(accepted) == 2
    assert "".join(window["text"] for window in accepted) == speech
    assert accepted[1]["speaker_at_start"] == "Участник A"


@pytest.mark.parametrize("field", ["portions", "late_portions"])
def test_export_requires_each_source_portion_collection_to_be_a_list(field):
    payload = export_payload()
    payload["snapshot"]["source"][field] = None
    with pytest.raises(ref.RefinementRefused, match="export_portions"):
        ref._validate_export(payload, "2026-01-01_120001")


def test_oversized_mandatory_audit_refuses_transport_and_returns_window_fallback(monkeypatch):
    speech = "Проверку макета проведут после обсуждения."
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        assert system == ref.SYSTEM, "an oversized audit must not reach transport"
        return json.dumps({"paragraphs": [{"text": "t" * 14000, "quotes": [speech], "uncertain": False}]})
    fallback = []
    def windowed(actual_speech, thread, previous, call, progress):
        fallback.append(actual_speech)
        return "synthetic window fallback"
    monkeypatch.setattr(ref, "_windowed", windowed)
    assert ref.refine(speech, [], "", {}, request=request) == "synthetic window fallback"
    assert calls == [ref.SYSTEM] and fallback == [speech]


def test_local_request_rejects_context_with_only_output_and_reserve_even_for_empty_input(monkeypatch):
    calls = fake_local(monkeypatch, context=612)
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref.local_request({"llm": {"engine": "ollama"}}, "", "", 100)
    assert calls == []


@pytest.mark.parametrize("blocks", [1, {"serial": 0}, "invalid blocks"])
def test_nonlist_export_blocks_are_rejected_with_closed_reason(blocks):
    payload = export_payload()
    payload["snapshot"]["blocks"] = blocks
    with pytest.raises(ref.RefinementRefused, match="export_empty"):
        ref._validate_export(payload, "2026-01-01_120001")


def test_draft_top_level_is_closed_even_with_otherwise_valid_paragraphs():
    row = {"text": "Короткая точная реплика.", "quotes": ["abcdefghijkl"], "uncertain": False}
    with pytest.raises(ref.RefinementRefused, match="paragraph_schema"):
        ref._paragraphs(json.dumps({"paragraphs": [row], "extra": True}), "abcdefghijkl")
    assert ref._paragraphs(json.dumps({"paragraphs": [row]}), "abcdefghijkl") == [row]


def test_repair_wire_schema_requires_nonempty_text_and_runtime_rejects_empty_text():
    row_schema = ref._repair_schema()["properties"]["changes"]["items"]
    assert row_schema["properties"]["text"]["minLength"] == 1
    raw = json.dumps({"changes": [{"text": "", "quotes": ["abcdefghijkl"], "uncertain": False}]})
    with pytest.raises(ref.RefinementRefused, match="paragraph_schema"):
        ref._paragraphs(raw, "abcdefghijkl", field="changes", allow_empty=True)


def test_local_request_forces_cloud_fallback_off_even_when_caller_enabled_it(monkeypatch):
    configs = []
    fake_local(monkeypatch, configs=configs)
    cfg = {"llm": {"engine": "ollama", "cloud_fallback_local": True, "allow_remote": True}}
    assert ref.local_request(cfg, "prompt", "system", 100) == "{}"
    assert len(configs) == 1
    assert configs[0]["llm"]["cloud_fallback_local"] is False
    assert configs[0]["llm"]["allow_remote"] is False
    assert cfg["llm"]["cloud_fallback_local"] is True


def test_window_starting_exactly_at_speaker_header_keeps_that_speaker():
    speech = "**Участник A** [00:01]:\nДальнейшая речь."
    windows = ref._windows(speech, len(speech))
    assert len(windows) == 1
    assert windows[0]["start"] == 0
    assert windows[0]["speaker_at_start"] == "Участник A"


def test_word_boundary_is_selected_from_lower_half_of_window():
    windows = ref._windows("abc de fghij", 8)
    assert windows[0]["text"] == "abc de "
    assert windows[0]["end"] == 7
    assert "".join(window["text"] for window in windows) == "abc de fghij"


@pytest.mark.parametrize("size", [11, 12])
def test_repair_evidence_requires_twelve_characters_even_when_exact(size):
    quote = "x" * size
    verdict = {"paragraphs": [], "coverage_complete": False, "corrections_resolved": True,
               "has_cross_window_correction": False, "correction_quotes": [], "repair_quotes": [quote]}
    if size < 12:
        with pytest.raises(ref.RefinementRefused, match="invalid_repair_evidence"):
            ref._window_audit(json.dumps(verdict), 0, quote)
    else:
        corrections, feedback = ref._window_audit(json.dumps(verdict), 0, quote)
        assert corrections == [] and feedback["repair_quotes"] == [quote]


def test_window_audit_wire_schema_requires_minimum_length_on_both_evidence_fields():
    for branch in ref._window_audit_schema(1)["anyOf"]:
        for field in ("correction_quotes", "repair_quotes"):
            assert branch["properties"][field]["items"]["minLength"] == 12


@pytest.mark.parametrize("structured", [False, True])
def test_structured_unavailability_requires_both_schema_request_and_explicit_provider_reason(monkeypatch, structured):
    import llm
    detail = "unrelated provider failure" if structured else "structured output is unavailable"
    fake_local(monkeypatch, context=8192, failure=llm.LLMHTTPError(400, detail))
    system = ref.WINDOW_AUDIT_SYSTEM if structured else "system"
    prompt = json.dumps({"paragraphs": []}) if structured else "prompt"
    with pytest.raises(ref.RefinementRefused, match="^local_request_failed$"):
        ref.local_request({"llm": {"engine": "ollama"}}, prompt, system, 100)


def test_semantic_repair_uses_full_generation_budget_and_requires_second_audit():
    first, second = "abcdefghijklmnop", "qrstuvwxyzABCDEF"
    row = {"text": "Первый предмет.", "quotes": [first], "uncertain": False}
    addition = {"text": "Второй предмет.", "quotes": [second], "uncertain": False}
    window = {"text": first + "\n" + second, "context_before": "", "context_after": ""}
    seen, audits = [], []
    def call(prompt, system, predict):
        seen.append((system, predict))
        if system == ref.WINDOW_SYSTEM:
            return json.dumps({"paragraphs": [row]})
        if system == ref.WINDOW_REPAIR_SYSTEM:
            return json.dumps({"changes": [addition]})
        assert system == ref.WINDOW_AUDIT_SYSTEM
        data = json.loads(prompt)
        audits.append(data["paragraphs"])
        rejected = len(audits) == 1
        return json.dumps({"paragraphs": [{"index": i, "supported": True} for i in range(len(data["paragraphs"]))],
            "coverage_complete": not rejected, "corrections_resolved": True,
            "has_cross_window_correction": False, "correction_quotes": [],
            "repair_quotes": [second] if rejected else []})
    assert ref._refine_window(window, [], "", call) == [row, addition]
    assert audits == [[row], [row, addition]]
    assert seen == [(ref.WINDOW_SYSTEM, 2000), (ref.WINDOW_AUDIT_SYSTEM, 1000),
                    (ref.WINDOW_REPAIR_SYSTEM, 2000), (ref.WINDOW_AUDIT_SYSTEM, 1000)]


def test_split_budget_includes_already_pending_windows(monkeypatch):
    monkeypatch.setattr(ref, "WINDOW_CHARS", 600)
    monkeypatch.setattr(ref, "MAX_WINDOWS", 2)
    events = []
    def refine_window(window, thread, previous, call):
        if len(window["text"]) > 400:
            raise ref.RefinementRefused("complete_input_exceeds_context")
        return []
    monkeypatch.setattr(ref, "_refine_window", refine_window)
    with pytest.raises(ref.RefinementRefused, match="source_window_budget"):
        ref._windowed("x" * 1000, [], "", lambda *a: pytest.fail("no model expected"), events.append)
    assert not any(event["phase"] == "complete" for event in events)


def test_exact_512_character_window_can_split_and_progress_never_claims_semantic_proof(monkeypatch):
    monkeypatch.setattr(ref, "MAX_WINDOWS", 2)
    accepted, events = [], []
    def refine_window(window, thread, previous, call):
        if len(window["text"]) > 256:
            raise ref.RefinementRefused("complete_input_exceeds_context")
        accepted.append(window["text"])
        return []
    monkeypatch.setattr(ref, "_refine_window", refine_window)
    body = ref._windowed("x" * 512, [], "", lambda *a: pytest.fail("no model expected"), events.append)
    assert isinstance(body, str)
    assert accepted == ["x" * 256, "x" * 256]
    assert events[-1]["phase"] == "complete" and events[-1]["source_windows"] == 2
    assert all(event["semantic_proof"] is False for event in events)


@pytest.mark.parametrize("speech", [None, {}, "", "   "])
def test_invalid_or_empty_final_speech_refuses_before_any_request(speech):
    def forbidden(*args):
        pytest.fail("empty speech must not reach a model")
    with pytest.raises(ref.RefinementRefused, match="empty_final_speech"):
        ref.refine(speech, [], "", {}, request=forbidden)


def test_unicode_audit_exactly_at_shared_admission_limit_does_not_fall_back():
    speech = "Проверку макета проведут после обсуждения."
    row = {"text": "", "quotes": [speech], "uncertain": False}
    audit_input = {"final_speech": speech, "paragraphs": [row]}
    room = 13360 - len(ref.AUDIT_SYSTEM) - len(json.dumps(audit_input, ensure_ascii=False))
    assert room > 0
    row["text"] = "t" * room
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        if system == ref.SYSTEM:
            return json.dumps({"paragraphs": [row]})
        assert system == ref.AUDIT_SYSTEM
        assert predict == 1000
        assert len(prompt) + len(system) == 13360
        return json.dumps({"paragraphs": [{"index": 0, "supported": True}],
                           "coverage_complete": True, "corrections_resolved": True})
    body = ref.refine(speech, [], "", {"llm": {"num_ctx": 8192}}, request=request)
    assert row["text"] in body
    assert calls == [ref.SYSTEM, ref.AUDIT_SYSTEM]


def test_short_neighbor_overlap_does_not_expand_a_repair_span_over_kept_material():
    own = "bcdefghijklZmnopqrstuvwx"
    window = {"text": own, "context_before": "a", "context_after": ""}
    kept = {"text": "Первый предмет.", "quotes": [own[:12]], "uncertain": False}
    change = {"text": "Второй предмет.", "quotes": ["abcdefghijkl", own[12:]], "uncertain": False}
    assert ref._merge_repair([kept], [change], window) == [kept, change]


def test_shared_request_counter_is_monotonic_and_stops_before_mandatory_audit(monkeypatch):
    monkeypatch.setattr(ref, "MAX_REQUESTS", 1)
    speech = "Проверку макета проведут после обсуждения."
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        assert system == ref.SYSTEM, "the exhausted budget must stop before audit transport"
        return json.dumps({"paragraphs": [{"text": speech, "quotes": [speech], "uncertain": False}]})
    with pytest.raises(ref.RefinementRefused, match="model_request_budget"):
        ref.refine(speech, [], "", {}, request=request)
    assert calls == [ref.SYSTEM]


def test_candidate_budget_prefers_more_relevant_text_over_earlier_same_source_kind():
    weak = "word " + "x" * 1395
    strong = "word extra " + "y" * 1389
    assert len(weak) == len(strong) == 1400
    result = ref._candidates("word extra", [{"text": weak}, {"text": strong}], "")
    assert result == {"live_thread": [strong], "previous_debrief": []}


def test_sub_512_character_context_refusal_is_terminal_without_tiny_split(monkeypatch):
    seen = []
    def refine_window(window, thread, previous, call):
        seen.append(len(window["text"]))
        if len(window["text"]) > 300:
            raise ref.RefinementRefused("complete_input_exceeds_context")
        return []
    monkeypatch.setattr(ref, "_refine_window", refine_window)
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref._windowed("x" * 511, [], "", lambda *a: pytest.fail("no model expected"), None)
    assert seen == [511]


def test_source_ledger_rejects_changed_bytes_even_when_offsets_remain_contiguous(monkeypatch):
    def corrupt_slice(window, thread, previous, call):
        window["text"] = "x" * len(window["text"])
        return []
    monkeypatch.setattr(ref, "_refine_window", corrupt_slice)
    events = []
    with pytest.raises(ref.RefinementRefused, match="source_coverage_gap"):
        ref._windowed("Собственная каноническая речь.", [], "", lambda *a: pytest.fail("no model expected"), events.append)
    assert not any(event["phase"] == "complete" for event in events)


@pytest.mark.parametrize("recursive", [False, True])
@pytest.mark.parametrize("neighbor_chars", [0, 400])
def test_refine_window_receives_new_speaker_at_exact_header_boundary(monkeypatch, recursive, neighbor_chars):
    first_header = "**Участник A** [00:01]:"
    second_header = "**Участник B** [00:02]:"
    first = first_header + "\n" + "a" * (512 - len(first_header) - 2) + "\n"
    second = second_header + "\n" + "b" * (512 - len(second_header) - 1)
    speech = first + second
    assert len(first) == len(second) == 512
    monkeypatch.setattr(ref, "WINDOW_CHARS", 1024 if recursive else 512)
    monkeypatch.setattr(ref, "NEIGHBOR_CHARS", neighbor_chars)
    actual_refine_window = ref._refine_window
    received, generation_inputs = [], []

    def observe_window(window, thread, previous, call):
        received.append(dict(window))
        return actual_refine_window(window, thread, previous, call)

    def call(prompt, system, predict):
        data = json.loads(prompt)
        if system == ref.WINDOW_SYSTEM:
            generation_inputs.append(data)
            if recursive and len(data["final_speech"]) > 512:
                raise ref.RefinementRefused("complete_input_exceeds_context")
            return json.dumps({"paragraphs": []})
        assert system == ref.WINDOW_AUDIT_SYSTEM
        return json.dumps({"paragraphs": [], "coverage_complete": True, "corrections_resolved": True,
            "has_cross_window_correction": False, "correction_quotes": [], "repair_quotes": []})

    monkeypatch.setattr(ref, "_refine_window", observe_window)
    ref._windowed(speech, [], "", call, None)
    target = next(window for window in received if window["start"] == 512)
    assert target["text"].startswith(second_header)
    assert target["speaker_at_start"] == "Участник B"
    assert target["context_speaker"] == ("Участник B" if neighbor_chars == 0 else "Участник A")
    model_input = next(data for data in generation_inputs if data["source_window"]["start"] == 512)
    assert model_input["source_window"]["speaker_at_start"] == "Участник B"
    assert model_input["source_window"]["context_speaker"] == target["context_speaker"]
    leaves = [window["text"] for window in received if len(window["text"]) == 512]
    assert "".join(leaves) == speech


@pytest.mark.parametrize("size", [0, -10])
def test_windows_refuses_non_positive_size(size):
    with pytest.raises(ValueError, match="positive"):
        ref._windows("Участник A: Привет.", size)
