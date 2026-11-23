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

    # When budget is tight, quote_feedback is omitted instead of overflowing context.
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
    ref._draft(prompt, ref.SYSTEM, 3000, speech,
               lambda p, s, pr: (seen_tight.append((p, s)), next(replies_tight))[1],
               max_input=len(prompt) + len(ref.SYSTEM) + 50)
    assert seen_tight[1][0] == prompt


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
    assert calls[-1]["output_schema"] == ref._draft_schema()

    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._draft_schema()

    ref.local_request(cfg, json.dumps({"paragraphs": [{}]}), ref.AUDIT_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._short_audit_schema(1)

    ref.local_request(cfg, json.dumps({"early_paragraphs": []}), links.DISCOVER_SYSTEM, 500)
    assert calls[-1]["output_schema"]["required"] == ["checked_ids", "relations"]

    ref.local_request(cfg, json.dumps({"early_paragraphs": []}), links.DISCOVERY_AUDIT_SYSTEM, 500)
    assert calls[-1]["output_schema"]["required"] == ["checked_ids", "status", "missing_relations", "uncertain_ids"]

    ref.local_request(cfg, json.dumps({"final_speech": "x"}), ref.WINDOW_REPAIR_SYSTEM, 500)
    assert calls[-1]["output_schema"] == ref._repair_schema()

    with pytest.raises(ref.RefinementRefused, match="schema_input"):
        ref.local_request(cfg, "{not json", ref.AUDIT_SYSTEM, 500)

    with pytest.raises(ref.RefinementRefused, match="audit_input"):
        ref.local_request(cfg, json.dumps({"paragraphs": "bad"}), ref.AUDIT_SYSTEM, 500)


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
