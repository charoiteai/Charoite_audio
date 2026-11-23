"""Targeted regression and boundary tests for refined_debrief quote spans, schemas, and retries (#1102)."""
import json
from types import SimpleNamespace

import pytest

import debrief_links as links
import debrief_refinement as ref


def test_find_quote_spans_normalizes_case_yo_nfd_dashes_quotes_whitespace_and_header_stars():
    source = (
        "**Участник A** [00:01:02 – 00:01:30]:\n"
        "Нужна двухфазная схема — е\u0308мкость «проверим»   завтра.\n"
    )
    assert ref._find_quote_spans("нужна двухфазная схема - емкость \"проверим\" завтра.", source)
    spans = ref._find_quote_spans("Участник A [00:01:02 - 00:01:30]: Нужна двухфазная схема", source)
    assert len(spans) == 1
    s, e = spans[0]
    assert source[s:e].startswith("Участник A** [00:01:02")
    assert source[s:e].endswith("Нужна двухфазная схема")
    # Single-line quote keeping ** around speaker header also matches
    spans_bold = ref._find_quote_spans("**Участник A** [00:01:02 – 00:01:30]: Нужна двухфазная схема", source)
    assert spans_bold == spans


def test_find_quote_spans_handles_casefold_expansion_exact_source_offsets():
    source = "ﬁрма гарантирует срок, и ß-тест подтверждает результат."
    spans = ref._find_quote_spans("fiрма гарантирует срок", source)
    assert len(spans) == 1
    assert source[spans[0][0]:spans[0][1]] == "ﬁрма гарантирует срок"
    spans2 = ref._find_quote_spans("ss-тест подтверждает", source)
    assert len(spans2) == 1
    assert source[spans2[0][0]:spans2[0][1]] == "ß-тест подтверждает"


def test_find_quote_spans_never_hides_case_variant_duplicate():
    source = "Нужна двухфазная схема.\nДругой: да, нужна двухфазная схема."
    spans = ref._find_quote_spans("нужна двухфазная схема", source)
    assert len(spans) == 2
    rows = [{"text": "Предложили схему.", "quotes": ["нужна двухфазная схема"], "uncertain": False}]
    with pytest.raises(ref._AmbiguousAnchors):
        ref._check_order(rows, source)


def test_header_only_quote_rejected_unless_paired_with_speech_quote():
    source = "**Участник A** [00:06:01 – 00:06:45]:\nНужна двухфазная схема для релиза."
    header_only = json.dumps({"paragraphs": [{
        "text": "Выступил участник.",
        "quotes": ["Участник A [00:06:01 – 00:06:45]:"],
        "uncertain": False,
    }]})
    with pytest.raises(ref._UnsupportedQuote) as exc_info:
        ref._paragraphs(header_only, source)
    assert exc_info.value.details == [(0, "header_only", "Участник A [00:06:01 – 00:06:45]:")]

    paired = json.dumps({"paragraphs": [{
        "text": "Участник предложил двухфазную схему.",
        "quotes": ["Участник A [00:06:01 – 00:06:45]:", "нужна двухфазная схема для релиза"],
        "uncertain": False,
    }]})
    assert len(ref._paragraphs(paired, source)) == 1

    # Regular speech ending with a timestamp in brackets must NOT be treated as header_only
    speech_with_time = "Созвон перенесли на [15:00]"
    valid_time = json.dumps({"paragraphs": [{
        "text": "Созвон перенесли на 15:00.",
        "quotes": [speech_with_time],
        "uncertain": False,
    }]})
    assert len(ref._paragraphs(valid_time, speech_with_time)) == 1


def test_merge_repair_uses_projected_source_spans_not_quote_length():
    h1 = "**Участник A** [00:01]:\nПервая важная реплика."
    h2 = "**Участник B** [00:02]:\nВторая важная реплика."
    window = {"context_before": "", "context_after": "", "text": f"{h1}\n{h2}"}
    kept = [{"text": "Первая тема.", "quotes": ["Участник A [00:01]: Первая важная реплика."], "uncertain": False}]
    changes = [{"text": "Вторая тема.", "quotes": ["вторая важная реплика."], "uncertain": False}]
    merged = ref._merge_repair(kept, changes, window)
    assert [row["text"] for row in merged] == ["Первая тема.", "Вторая тема."]


def test_debrief_links_selected_matches_normalized_anchor_inside_verbatim_extra():
    source = "**Участник A** [00:01]:\nНужна двухфазная схема для релиза сервиса."
    window = {"context_before": "", "context_after": "", "text": source}
    paragraph = {"quotes": ["нужна двухфазная схема"]}
    value = {
        "early_indices": [0],
        "early_quotes": ["Нужна двухфазная схема для релиза сервиса."],
    }
    assert links._selected(value, "early", paragraph, window, "supported") is None

    unrelated = {
        "early_indices": [0],
        "early_quotes": ["Участник A [00:01]:"],
    }
    with pytest.raises(ref.UnverifiedReply, match="link_paragraph_anchor"):
        links._selected(unrelated, "early", paragraph, window, "supported")


def test_window_audit_accepts_normalized_repair_and_correction_quotes():
    source = "**Участник A** [00:18:36 – 00:19:20]:\nТогда в этот релиз миграцию не тащим."
    verdict = {
        "paragraphs": [{"index": 0, "supported": True}],
        "coverage_complete": False,
        "corrections_resolved": True,
        "has_cross_window_correction": True,
        "correction_quotes": ["тогда в этот релиз миграцию не тащим"],
        "repair_quotes": ["Участник A [00:18:36 - 00:19:20]: Тогда в этот релиз миграцию не тащим."],
    }
    corrections, feedback = ref._window_audit(json.dumps(verdict), 1, source)
    assert len(corrections) == 1
    assert feedback is not None


def test_draft_retries_model_json_once_without_mutating_system_or_prompt():
    speech = "Релиз перенесли на пятницу после проверки нагрузочного стенда."
    prompt = json.dumps({"final_speech": speech, "live_thread": [], "previous_debrief": ""})
    seen = []
    replies = iter([
        '{\n"changes": [\n{\ntext": "broken json"}\n]\n}',
        json.dumps({"changes": [{"text": "Релиз перенесли на пятницу.", "quotes": [speech], "uncertain": False}]}),
    ])

    def call(p, s, pr):
        seen.append((p, s, pr))
        return next(replies)

    rows = ref._draft(prompt, ref.WINDOW_REPAIR_SYSTEM, 2000, speech, call)
    assert len(rows) == 1
    assert seen[0] == seen[1]
    assert seen[0][1] == ref.WINDOW_REPAIR_SYSTEM


def test_unsupported_quote_retry_passes_bounded_quote_feedback_and_respects_budget():
    speech = "Релиз перенесли на пятницу после проверки нагрузочного стенда."
    prompt = json.dumps({"final_speech": speech, "live_thread": [], "previous_debrief": ""})
    seen = []
    replies = iter([
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли.",
            "quotes": ["Релиз перенесли... после проверки"],
            "uncertain": False,
        }]}),
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли на пятницу.",
            "quotes": [speech],
            "uncertain": False,
        }]}),
    ])

    def call(p, s, pr):
        seen.append((p, s))
        return next(replies)

    rows = ref._draft(prompt, ref.SYSTEM, 3000, speech, call)
    assert len(rows) == 1
    retried = json.loads(seen[1][0])
    assert retried.pop("quote_feedback") == {
        "reason": "unsupported_quote",
        "examples": [{"index": 0, "kind": "ellipsis", "quote_excerpt": "Релиз перенесли... после проверки"}],
    }
    assert retried == json.loads(seen[0][0])
    assert "p0:ellipsis" in seen[1][1]
    assert "Релиз перенесли..." not in seen[1][1]

    # When budget allows short suffix only, quote_feedback is omitted and not referenced in system.
    seen_mid = []
    replies_mid = iter([
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли.",
            "quotes": ["Релиз перенесли... после проверки"],
            "uncertain": False,
        }]}),
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли на пятницу.",
            "quotes": [speech],
            "uncertain": False,
        }]}),
    ])
    mid_budget = len(prompt) + len(ref.SYSTEM) + 250
    ref._draft(prompt, ref.SYSTEM, 3000, speech,
               lambda p, s, pr: (seen_mid.append((p, s)), next(replies_mid))[1],
               max_input=mid_budget)
    assert seen_mid[1][0] == prompt
    assert len(seen_mid[1][0]) + len(seen_mid[1][1]) <= mid_budget
    assert "quote_feedback" not in seen_mid[1][1]

    # When budget is tighter than minimal suffix, neither prompt nor system exceeds max_input.
    seen_tight = []
    replies_tight = iter([
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли.",
            "quotes": ["Релиз перенесли... после проверки"],
            "uncertain": False,
        }]}),
        json.dumps({"paragraphs": [{
            "text": "Релиз перенесли на пятницу.",
            "quotes": [speech],
            "uncertain": False,
        }]}),
    ])
    tight_budget = len(prompt) + len(ref.SYSTEM) + 50
    ref._draft(prompt, ref.SYSTEM, 3000, speech,
               lambda p, s, pr: (seen_tight.append((p, s)), next(replies_tight))[1],
               max_input=tight_budget)
    assert seen_tight[1][0] == prompt
    assert seen_tight[1][1] == ref.SYSTEM
    assert len(seen_tight[1][0]) + len(seen_tight[1][1]) <= tight_budget


def test_output_schema_covers_all_debrief_systems_and_refuses_bad_prompt(monkeypatch):
    import llm

    calls = []

    class Client:
        num_ctx = 8192

        def __init__(self, cfg):
            pass

        def grounded_answer(self, prompt, **kwargs):
            calls.append(kwargs)
            return iter(["{}"])

    monkeypatch.setattr(llm, "LLM", Client)
    monkeypatch.setattr(ref.LocalLoopbackAuthority, "verify", lambda client: SimpleNamespace(model="local-model"))
    cfg = {"llm": {"engine": "ollama"}}

    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._draft_schema(min_items=1)
    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.SYSTEM + " retry", 500)
    assert calls[-1]["output_schema"] == ref._draft_schema(min_items=1)

    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._draft_schema(min_items=0)
    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_SYSTEM + " retry", 500)
    assert calls[-1]["output_schema"] == ref._draft_schema(min_items=0)

    ref.local_request(cfg, json.dumps({"paragraphs": [{}]}), ref.AUDIT_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._short_audit_schema(1)

    ref.local_request(cfg, json.dumps({"early_paragraphs": []}), links.DISCOVER_SYSTEM, 500)
    assert calls[-1]["output_schema"]["required"] == ["checked_ids", "relations"]

    ref.local_request(cfg, json.dumps({"early_paragraphs": []}), links.DISCOVERY_AUDIT_SYSTEM, 500)
    assert calls[-1]["output_schema"]["required"] == ["checked_ids", "status", "missing_relations", "uncertain_ids"]

    ref.local_request(cfg, json.dumps({}), links.ANCHOR_AUDIT_SYSTEM, 100)
    assert calls[-1]["output_schema"]["required"] == ["verdict"]

    pair_payload = {
        "relation": {"early_id": "e:p0", "late_id": "l:p0", "kind": "clarifies"},
        "early_anchors": [{"index": 0, "quote": "Первая уникальная опора речи."}],
        "late_anchors": [{"index": 0, "quote": "Вторая уникальная опора речи."}],
    }
    ref.local_request(cfg, json.dumps(pair_payload), links.PAIR_AUDIT_SYSTEM, 500)
    assert calls[-1]["output_schema"]["properties"]["kind"] == {"const": "clarifies"}

    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_REPAIR_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._repair_schema()
    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_REPAIR_SYSTEM + " retry", 500)
    assert calls[-1]["output_schema"] == ref._repair_schema()

    with pytest.raises(ref.RefinementRefused, match="schema_input"):
        ref.local_request(cfg, "{not json", ref.AUDIT_SYSTEM, 500)

    with pytest.raises(ref.RefinementRefused, match="audit_input"):
        ref.local_request(cfg, json.dumps({"paragraphs": "bad"}), ref.AUDIT_SYSTEM, 500)

    with pytest.raises(ref.RefinementRefused, match="window_audit_input"):
        ref.local_request(cfg, json.dumps({"paragraphs": "bad"}), ref.WINDOW_AUDIT_SYSTEM, 500)

    with pytest.raises(ref.RefinementRefused, match="pair_audit_input"):
        ref.local_request(cfg, json.dumps({}), links.PAIR_AUDIT_SYSTEM, 500)


def test_progress_reports_closed_reason_for_short_and_windowed_paths():
    speech = "Релиз перенесли на пятницу после проверки нагрузочного стенда."
    events = []
    answers = iter([
        json.dumps({"paragraphs": [{"text": "Релиз перенесли.", "quotes": [speech], "uncertain": True}]}),
        json.dumps({"paragraphs": [{"index": 0, "supported": True}],
                    "coverage_complete": True, "corrections_resolved": True}),
    ])
    ref.refine(speech, [], "", {}, request=lambda *a: next(answers), progress=events.append)
    win = [e for e in events if e["phase"] == "source_window"]
    assert len(win) == 1 and win[0]["verbatim"] is True and win[0]["reason"] == "uncertain"

    events2 = []
    answers2 = iter([
        json.dumps({"paragraphs": [{"text": "Релиз.", "quotes": ["Выдуманная цитата которой нет."], "uncertain": False}]}),
        json.dumps({"paragraphs": [{"text": "Релиз.", "quotes": ["Выдуманная цитата которой нет."], "uncertain": False}]}),
    ])
    ref.refine(speech, [], "", {}, request=lambda *a: next(answers2), progress=events2.append)
    win2 = [e for e in events2 if e["phase"] == "source_window"]
    assert len(win2) == 1 and win2[0]["verbatim"] is True and win2[0]["reason"] == "unsupported_quote"


def test_normalization_cache_boundaries_and_failure_kinds():
    # Leading/trailing whitespace must not inject stray spaces; cache must reuse normalized source.
    norm, _ = ref._normalize_with_spans("   Нужна   схема   ")
    assert norm == "нужна схема"

    src = "**Участник A** [00:01:02 – 00:01:30]:\nНужна двухфазная схема для релиза."
    ref._normalized_source.cache_clear()
    first = ref._normalized_source(src)
    second = ref._normalized_source(src)
    assert first == second
    assert ref._normalized_source.cache_info().hits == 1

    # Exact header boundary match (qs == hs and qe == he) counts as header_only.
    assert ref._is_header_only_spans([(0, 10)], [(0, 10)]) is True
    assert ref._is_header_only_spans([(0, 10)], []) is False

    # Non-string inputs must return empty list (not None) and classify as too_short.
    assert ref._find_quote_spans(None, src) == []
    assert ref._find_quote_spans("нужна двухфазная схема", None) == []
    assert ref._quote_failure_kind(None) == "too_short"
    assert ref._quote_failure_kind("а" * 11) == "too_short"
    assert ref._quote_failure_kind("а" * 12) == "not_in_source"
    assert ref._quote_failure_kind("а" * 12 + "...") == "ellipsis"


def test_paragraphs_caps_quote_errors_at_four_for_missing_and_header_only():
    header = "**Участник A** [00:01:02 – 00:01:30]:"
    speech = f"{header}\nРелиз подтверждён на вторник после проверки стенда."
    five_missing = json.dumps({"paragraphs": [
        {"text": f"Пункт {i}.", "quotes": [f"Выдуманная цитата номер {i} отсутствует."], "uncertain": False}
        for i in range(5)
    ]})
    with pytest.raises(ref._UnsupportedQuote) as exc_missing:
        ref._paragraphs(five_missing, speech)
    assert len(exc_missing.value.details) == 4

    five_headers = json.dumps({"paragraphs": [
        {"text": f"Пункт {i}.", "quotes": ["Участник A [00:01:02 – 00:01:30]:"], "uncertain": False}
        for i in range(5)
    ]})
    with pytest.raises(ref._UnsupportedQuote) as exc_headers:
        ref._paragraphs(five_headers, speech)
    assert len(exc_headers.value.details) == 4
    assert all(kind == "header_only" for _, kind, _ in exc_headers.value.details)


def test_exact_schema_constraints_for_draft_audit_and_relations():
    s0 = ref._draft_schema()
    assert s0["additionalProperties"] is False
    assert "minItems" not in s0["properties"]["paragraphs"]
    row = s0["properties"]["paragraphs"]["items"]
    assert row["additionalProperties"] is False
    assert row["properties"]["text"]["minLength"] == 1
    assert row["properties"]["quotes"]["minItems"] == 1
    assert row["properties"]["quotes"]["items"]["minLength"] == 12
    assert ref._draft_schema(min_items=1)["properties"]["paragraphs"]["minItems"] == 1

    sa = ref._short_audit_schema(2)
    assert sa["additionalProperties"] is False
    check = sa["properties"]["paragraphs"]["items"]
    assert check["additionalProperties"] is False
    assert check["properties"]["index"]["maximum"] == 1
    assert ref._short_audit_schema(0)["properties"]["paragraphs"]["items"]["properties"]["index"]["maximum"] == 0

    rel = links._relation_item_schema()
    assert rel["additionalProperties"] is False


def test_quote_feedback_and_fit_retry_exact_length_boundaries():
    base = [{"index": i, "kind": "not_in_source", "quote_excerpt": "а" * 140} for i in range(4)]
    pad = 800 - len(ref._dump(base))
    assert pad > 0
    details = [
        (0, "not_in_source" + "x" * pad, "а" * 150),
        (1, "not_in_source", "а" * 150),
        (2, "not_in_source", "а" * 150),
        (3, "not_in_source", "а" * 150),
    ]
    fb = ref._quote_feedback(details)
    assert len(ref._dump(fb["examples"])) == 800
    assert len(fb["examples"][0]["quote_excerpt"]) == 140

    p, sys_s, cand, full_s, min_s = "prompt", "system", "prompt+feedback", " full-suffix", " min"
    exact_full = len(cand) + len(sys_s) + len(full_s)
    assert ref._fit_retry(p, sys_s, cand, full_s, min_s, exact_full) == (cand, sys_s + full_s)
    exact_min = len(p) + len(sys_s) + len(min_s)
    assert ref._fit_retry(p, sys_s, cand, full_s, min_s, exact_min) == (p, sys_s + min_s)


def test_refine_short_budget_prevents_retry_from_overflowing_into_windowed():
    cfg = {"llm": {"num_ctx": 8192}}
    short_budget = (8192 - 3000 - ref.CONTEXT_RESERVE) * ref.CHARS_PER_TOKEN
    anchor = "Ключевое решение перенести релиз на вторник."
    base_prompt = json.dumps({"final_speech": anchor, "live_thread": [], "previous_debrief": ""}, ensure_ascii=False)
    pad = short_budget - len(base_prompt) - len(ref.SYSTEM) - 100
    assert pad > 0
    speech = anchor + " " + ("б" * (pad - 1))
    seen = []
    answers = iter([
        json.dumps({"paragraphs": [{"text": "Релиз.", "quotes": ["Выдуманная фраза которой нет."], "uncertain": False}]}),
        json.dumps({"paragraphs": [{"text": "Релиз перенесли на вторник.", "quotes": [anchor], "uncertain": False}]}),
        json.dumps({"paragraphs": [{"index": 0, "supported": True}],
                    "coverage_complete": True, "corrections_resolved": True}),
    ])

    def req(_cfg, p, s, _pr):
        seen.append((p, s))
        return next(answers)

    out = ref.refine(speech, [], "", cfg, request=req)
    assert "Релиз перенесли на вторник." in out
    assert len(seen) == 3
    assert len(seen[1][0]) + len(seen[1][1]) <= short_budget
    assert "quote_feedback" not in seen[1][1]
