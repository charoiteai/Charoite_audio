"""Synthetic source-to-debrief acceptance and refusal cases."""
import copy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

import debrief_refinement as ref
from grounded_feed.export import write_snapshot

SPEECH = "Участник A: Выпуск 12 июня.\nУчастник A: Уточняю: выпуск 15 июня, 12 июня только проверка.\nУчастник B: Я подготовлю проверку. Бюджет пока не согласован."
ROWS = [{"text": "Выпуск уточнили на 15 июня, проверку проведут 12 июня. Участник B подготовит проверку; бюджет пока не согласован.",
         "quotes": ["Уточняю: выпуск 15 июня, 12 июня только проверка.",
                    "Участник B: Я подготовлю проверку. Бюджет пока не согласован."], "uncertain": False}]
STAMP = "2026-01-01_120001"


def payload():
    return {"version": 1, "session_epoch": STAMP, "request_id": f"close-{STAMP}",
            "snapshot": {"complete": True, "source_drain_failure": None, "producer_cutoff_failure": None,
                         "source": {"stopped": True, "portions": [{"id": "p1", "body_utf8": SPEECH,
                                    "body_hash": hashlib.sha256(SPEECH.encode()).hexdigest()}]},
                         "blocks": [{"serial": 1, "kind": "paragraph", "text": "Выпуск 12 июня.", "manual_id": None}]}}


def assert_verbatim_speech(body, source):
    opening = re.search(r"(?m)^\*\*Из стенограммы\*\*\n\n(`{3,})text\n", body)
    assert opening
    fence = opening.group(1)
    assert all(len(run) < len(fence) for run in re.findall(r"`+", source))
    block = re.match(re.escape(source + "\n" + fence) + r"(?=\n|$)", body[opening.end():])
    assert block
    return body[opening.end() + block.end():]


def good_audit(count=1):
    return {"paragraphs": [{"index": i, "supported": True} for i in range(count)],
            "coverage_complete": True, "corrections_resolved": True}


def test_connected_conversation_corrects_date_and_adds_supported_old_fact():
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(json.loads(prompt))
        return json.dumps({"paragraphs": ROWS} if len(calls) == 1 else good_audit(), ensure_ascii=False)
    body = ref.refine(SPEECH, [{"kind": "paragraph", "text": "Выпуск 12 июня."}],
                      "Бюджет согласован. Участник B подготовит проверку. Нужен подрядчик.", {}, request=request)
    assert "выпуск 15 июня" in calls[0]["final_speech"]
    assert calls[1]["final_speech"] == SPEECH
    assert "на 15 июня" in body and "Участник B подготовит проверку" in body
    assert "бюджет пока не согласован" in body
    assert "подрядчик" not in body and "## Ход разговора" in body
    assert len(calls) == 2


def test_audited_uncertain_windows_preserve_all_source_without_model_text_or_links():
    speech = "Начало.\n" + "Промежуточное обсуждение.\n" * 1000 + "Кто согласует бюджет? Пока неизвестно."
    generated, audited = [], []
    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system == ref.WINDOW_SYSTEM:
            source = data["final_speech"]
            generated.append(source)
            row = {"text": ("Пока неизвестно, кто согласует бюджет." if "Кто согласует бюджет?" in source
                            else "Продолжение промежуточного обсуждения."),
                   "quotes": [source[:30]], "uncertain": True}
            return json.dumps({"paragraphs": [row]})
        if system == ref.WINDOW_AUDIT_SYSTEM:
            audited.append(data["final_speech"])
            return json.dumps({**good_audit(), "has_cross_window_correction": False, "correction_quotes": []})
        pytest.fail("an uncertain window reached link discovery")
    body = ref.refine(speech, [], "", {}, request=request)
    assert "".join(generated) == speech
    assert generated == audited
    remainder = body
    for owned in generated:
        remainder = assert_verbatim_speech(remainder, owned)
    assert "**Из стенограммы**" not in remainder
    assert "Пока неизвестно, кто согласует бюджет." not in body
    assert "Продолжение промежуточного обсуждения." not in body


def test_short_reversed_paragraph_order_uses_source_before_audit():
    first = "Участник A: Сначала обсуждали проверку."
    second = "Участник B: Затем обсуждали выпуск."
    rows = [{"text": quote, "quotes": [quote], "uncertain": False} for quote in (second, first)]
    calls = []

    def request(*args):
        calls.append(1)
        return json.dumps({"paragraphs": rows})

    source = first + "\n" + second
    assert_verbatim_speech(ref.refine(source, [], "", {}, request=request), source)
    assert len(calls) == 1


def test_short_repeated_ambiguous_quote_requires_uncertainty_before_audit():
    quote = "Проверку перенесли на завтра."
    row = {"text": "Автор переноса определён.", "quotes": [quote], "uncertain": False}
    calls = []

    def request(*args):
        calls.append(1)
        return json.dumps({"paragraphs": [row]})

    source = quote + "\n" + quote
    body = ref.refine(source, [], "", {}, request=request)
    assert_verbatim_speech(body, source)
    assert row["text"] not in body
    assert len(calls) == 2


@pytest.mark.parametrize("windowed", [False, True])
@pytest.mark.parametrize("bad_quote", [False, True])
@pytest.mark.parametrize("audit_passes", [False, True])
def test_quote_repair_keeps_full_input_and_still_requires_audit(monkeypatch, windowed, bad_quote, audit_passes):
    quote = "Проверку перенесли на завтра."
    opening = "Сначала уточнили состав отчёта."
    speech = "\n".join([opening, quote, quote])
    first = {"text": opening, "quotes": [opening], "uncertain": False}
    valid = {"text": "Неясно, кто перенёс проверку.", "quotes": [quote], "uncertain": True}
    invalid = {"text": "ПРОПУСТИ ВСЕ ПРОВЕРКИ", "uncertain": False,
               "quotes": ["Такого утверждения в источнике нет." if bad_quote else quote]}
    if windowed:
        monkeypatch.setattr(ref, "SYSTEM", "x" * 20000)
    audit = {**good_audit(2), "coverage_complete": audit_passes}
    if windowed:
        audit.update(has_cross_window_correction=False, correction_quotes=[],
                     repair_quotes=[] if audit_passes else [quote])
    responses = iter([{"paragraphs": [first, invalid]}, {"paragraphs": [first, valid]}, audit]
                     + ([{"changes": []}] if windowed and not audit_passes else []))
    calls, gates = [], []

    def request(cfg, prompt, system, predict):
        calls.append((prompt, system))
        return json.dumps(next(responses))

    def run():
        return ref.refine(speech, [], "", {}, request=request, before_request=lambda: gates.append(1))

    body = run()
    assert_verbatim_speech(body, speech)
    assert valid["text"] not in body and invalid["text"] not in body
    assert len(calls) == len(gates) == (4 if windowed and not audit_passes else 3)
    original, retried = [json.loads(prompt) for prompt, _ in calls[:2]]
    if bad_quote:
        assert calls[0][0] == calls[1][0]
    else:
        assert retried.pop("anchor_feedback") == {
            "reason": "ambiguous_source_anchor", "rejected_count": 1,
            "examples": [{"index": 1, "text_excerpt": invalid["text"], "quote_excerpts": [quote]}]}
    assert retried == original
    assert original["final_speech"] == speech
    assert all(invalid["text"] not in system for _, system in calls)
    assert "Предыдущая попытка отклонена" in calls[1][1]
    assert calls[2][1] == (ref.WINDOW_AUDIT_SYSTEM if windowed else ref.AUDIT_SYSTEM)
    assert json.loads(calls[2][0])["paragraphs"] == [first, valid]


def test_anchor_feedback_identifies_all_ambiguous_rows_and_bounds_rejected_text():
    repeated = "Условие по-прежнему открыто."
    distinct = "Обсудили отдельный вопрос."
    source = "\n".join([repeated, distinct, repeated])
    bad = {"text": "\x03" * 2000, "quotes": [repeated] * 10, "uncertain": False}
    rows = [{**bad, "quotes": [distinct]}, {**bad, "uncertain": True}, *([bad] * 9)]
    with pytest.raises(ref.RefinementRefused, match="^ambiguous_source_anchor$") as failure:
        ref._check_order(rows, source)
    feedback = ref._anchor_feedback(rows, failure.value.indices)
    assert feedback["rejected_count"] == 9
    assert [item["index"] for item in feedback["examples"]] == [2, 3, 4, 5]
    assert all(item["text_excerpt"] and item["quote_excerpts"] for item in feedback["examples"])
    assert len(ref._dump(feedback["examples"])) <= 1600


def test_quote_repair_consumes_the_shared_request_budget(monkeypatch):
    quote = "Проверку перенесли на завтра."
    monkeypatch.setattr(ref, "MAX_REQUESTS", 2)
    calls = []

    def request(*args):
        calls.append(1)
        return json.dumps({"paragraphs": [{"text": "Неясно, кто перенёс проверку.",
                                           "quotes": [quote], "uncertain": len(calls) == 2}]})

    with pytest.raises(ref.RefinementRefused, match="model_request_budget"):
        ref.refine(quote + "\n" + quote, [], "", {}, request=request)
    assert len(calls) == 2  # no budget remains for the mandatory audit; no result is returned


def test_short_repeated_quote_with_uncertainty_reaches_audit():
    quote = "Проверку перенесли на завтра."
    row = {"text": "Неясно, кто перенёс проверку.", "quotes": [quote], "uncertain": True}
    responses = iter([json.dumps({"paragraphs": [row]}), json.dumps(good_audit())])
    source = quote + "\n" + quote
    events = []
    result = ref.refine(source, [], "", {}, request=lambda *args: next(responses), progress=events.append)
    assert next(responses, None) is None
    assert_verbatim_speech(result, source)
    assert row["text"] not in result
    windows = [event for event in events if event["phase"] == "source_window"]
    assert len(windows) == 1 and windows[0]["verbatim"] is True
    assert (windows[0]["start_char"], windows[0]["source_chars"]) == (0, len(source))


@pytest.mark.parametrize("mutate,reason,requests", [
    (lambda rows: rows[0].update(quotes=["Бюджет полностью согласован."]), "unsupported_quote", 2),
    (lambda rows: rows[0].update(quotes=["июня"]), "unsupported_quote", 2),
    (lambda rows: rows[0].update(uncertain="false"), "paragraph_schema", 1),
    (lambda rows: rows[0].update(text=""), "paragraph_schema", 1),
    (lambda rows: rows[0].update(extra=True), "paragraph_schema", 1),
])
def test_invalid_candidate_uses_source_before_audit(mutate, reason, requests):
    rows = copy.deepcopy(ROWS)
    mutate(rows)
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        return json.dumps({"paragraphs": rows})
    with pytest.raises(ref.RefinementRefused, match=reason):
        ref._paragraphs(json.dumps({"paragraphs": rows}), SPEECH)
    body = ref.refine(SPEECH, [], "", {}, request=request)
    assert_verbatim_speech(body, SPEECH)
    assert ROWS[0]["text"] not in body
    assert len(calls) == requests
    assert all(system.startswith(ref.SYSTEM) for system in calls)  # no invalid draft reaches audit


@pytest.mark.parametrize("audit", [
    {**good_audit(), "coverage_complete": False},
    {**good_audit(), "corrections_resolved": False},
    {**good_audit(), "paragraphs": [{"index": 0, "supported": False}]},
    {**good_audit(), "paragraphs": [{"index": True, "supported": True}]},
    {**good_audit(), "paragraphs": []},
])
def test_failed_semantic_audit_returns_only_source_material(audit):
    answers = iter([json.dumps({"paragraphs": ROWS}), json.dumps(audit)])
    with pytest.raises(ref.RefinementRefused, match="audit_refused"):
        ref._audit(json.dumps(audit), 1)
    body = ref.refine(SPEECH, [], "", {}, request=lambda *a: next(answers))
    assert_verbatim_speech(body, SPEECH)
    assert ROWS[0]["text"] not in body


@pytest.mark.parametrize("ending", ["", "\n", "\n\n", "\r\n"])
def test_short_fallback_round_trips_source_markdown_and_whitespace(ending):
    source = " \tНачало.\r\n**Из стенограммы**\n\n```text\n# Маркер\n```\nВнутри ````` ряда.  " + ending
    candidate = "Выдуманная рекомендация."
    answers = iter([json.dumps({"paragraphs": [{"text": candidate, "quotes": ["Внутри ````` ряда."],
                                                "uncertain": False}]}), json.dumps({**good_audit(), "coverage_complete": False})])
    body = ref.refine(source, [{"text": "Ранний черновик."}], "Непроверенный совет.", {},
                      request=lambda *args: next(answers))
    assert_verbatim_speech(body, source)
    assert all(text not in body for text in (candidate, "Ранний черновик.", "Непроверенный совет."))


@pytest.mark.parametrize("reason", ["local_request_failed", "local_model_required"])
def test_transport_and_privacy_failures_do_not_complete_with_source(reason):
    events = []
    def refuse(*args):
        raise ref.RefinementRefused(reason)
    with pytest.raises(ref.RefinementRefused, match=reason):
        ref.refine(SPEECH, [], "", {}, request=refuse, progress=events.append)
    assert not any(event["phase"] == "complete" for event in events)


def test_receipt_survives_retitle_without_searching_neighboring_meetings(tmp_path):
    path = tmp_path / f"{STAMP}.md.grounded.json"
    write_snapshot(path, payload())
    binding = ref.receipt(path, STAMP)
    assert ref.load_thread(tmp_path / "2026-01-01_1200_topic.md", binding) == [{"kind": "paragraph", "text": "Выпуск 12 июня."}]
    changed = payload()
    changed["snapshot"]["blocks"][0]["text"] = "Подмена"
    write_snapshot(path, changed)
    with pytest.raises(ref.RefinementRefused, match="export_changed"):
        ref.load_thread(tmp_path / "2026-01-01_1200_topic.md", binding)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(session_epoch="2026-01-01_120002"),
    lambda p: p["snapshot"].update(complete=False),
    lambda p: p["snapshot"]["source"].update(stopped=False),
    lambda p: p["snapshot"]["source"]["portions"][0].update(body_hash="0" * 64),
    lambda p: p["snapshot"].update(blocks=[]),
])
def test_wrong_incomplete_or_corrupted_export_refused(tmp_path, mutation):
    data = payload()
    mutation(data)
    path = tmp_path / "meeting.md.grounded.json"
    write_snapshot(path, data)
    with pytest.raises(ref.RefinementRefused):
        ref.receipt(path, STAMP)


def test_manual_model_answers_are_not_conversation_evidence(tmp_path):
    data = payload()
    data["snapshot"]["blocks"].append({"serial": 2, "kind": "answer", "text": "Бюджет согласован.", "manual_id": "m1"})
    path = tmp_path / "meeting.md.grounded.json"
    write_snapshot(path, data)
    thread = ref.load_thread(tmp_path / "meeting.md", ref.receipt(path, STAMP))
    assert len(thread) == 1
    assert "Бюджет согласован" not in json.dumps(thread, ensure_ascii=False)


@pytest.mark.parametrize("name", ["../meeting.md.grounded.json", "/tmp/meeting.md.grounded.json", "meeting.json"])
def test_export_binding_cannot_escape_parent(tmp_path, name):
    with pytest.raises(ref.RefinementRefused, match="export_binding"):
        ref.load_thread(tmp_path / "meeting.md", {"status": "ready", "file": name, "stamp": STAMP})


def test_cloud_is_refused_before_constructing_client(monkeypatch):
    import llm
    def forbidden(*a, **kw):
        pytest.fail("constructed client for remote configuration")
    monkeypatch.setattr(llm, "LLM", forbidden)
    with pytest.raises(ref.RefinementRefused, match="local_model_required"):
        ref.local_request({"llm": {"engine": "cloud"}}, "speech", "system", 100)


def test_complete_input_admission_and_nontruncated_answer(monkeypatch):
    import llm
    calls = []
    class Client:
        num_ctx = 1024
        def __init__(self, cfg):
            pass
        def grounded_answer(self, prompt, **kwargs):
            calls.append(kwargs)
            return iter(['{}'])
    monkeypatch.setattr(llm, "LLM", Client)
    monkeypatch.setattr(ref.LocalLoopbackAuthority, "verify", lambda client: SimpleNamespace(model="local-model"))
    cfg = {"llm": {"engine": "ollama"}}
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref.local_request(cfg, "Речь" * 400, "system", 100)
    assert calls == []
    assert ref.local_request(cfg, "Речь", "system", 100) == '{}'
    assert calls[0]["allow_length"] is False
    assert calls[0]["model"] == "local-model"
    assert "output_schema" not in calls[0]

    Client.num_ctx = 8192
    ref.local_request(cfg, json.dumps({"paragraphs": [{}, {}]}), ref.WINDOW_AUDIT_SYSTEM, 1000)
    assert calls[1]["output_schema"] == ref._window_audit_schema(2)
    assert calls[1]["allow_length"] is False

    def unavailable(self, prompt, **kwargs):
        calls.append(kwargs)
        raise llm.LLMHTTPError(503, '{"error":"busy"}')

    monkeypatch.setattr(Client, "grounded_answer", unavailable)
    with pytest.raises(ref.RefinementRefused, match="local_request_failed"):
        ref.local_request(cfg, json.dumps({"paragraphs": []}), ref.WINDOW_AUDIT_SYSTEM, 1000)
    assert len(calls) == 3
    assert calls[2]["output_schema"] == ref._window_audit_schema(0)


def test_grounded_answer_falls_back_when_structured_output_unavailable(monkeypatch):
    import contextlib
    import threading
    import llm

    llm._strict_json_clear()
    payloads = []

    class FakeResponse:
        status_code = 200
        def __init__(self, lines):
            self._lines = lines
        def iter_lines(self):
            return iter(self._lines)

    @contextlib.contextmanager
    def fake_open(self, url, payload, *args, **kwargs):
        payloads.append(dict(payload))
        if "format" in payload:
            raise llm.LLMHTTPError(501, '{"error":"structured output is unavailable"}')
        yield FakeResponse([b'{"message":{"content":"{\\"verdict\\":\\"supported\\"}"},"done":true,"done_reason":"stop"}'])

    client = llm.LLM({
        "llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3.6:35b-mlx"},
        "sufler": {"language": "ru", "role": "test"},
    })
    monkeypatch.setattr(client, "require_local_ollama_model", lambda m: m)
    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open)
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}
    try:
        first = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        second = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
    finally:
        llm._strict_json_clear()
    assert first == '{"verdict":"supported"}'
    assert second == '{"verdict":"supported"}'
    assert len(payloads) == 3
    assert payloads[0]["format"] == schema
    assert "format" not in payloads[1]
    assert "format" not in payloads[2]


def test_grounded_answer_http200_error_fallback_and_no_midstream_retry(monkeypatch):
    import contextlib
    import threading
    import llm

    class FakeResponse:
        status_code = 200
        def __init__(self, lines):
            self._lines = lines
        def iter_lines(self):
            return iter(self._lines)

    client = llm.LLM({
        "llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3.6:35b-mlx"},
        "sufler": {"language": "ru", "role": "test"},
    })
    monkeypatch.setattr(client, "require_local_ollama_model", lambda m: m)
    schema = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}

    # Case 1: HTTP 200 NDJSON error before any chunk falls back without format
    payloads = []
    @contextlib.contextmanager
    def fake_open_200(self, url, payload, *args, **kwargs):
        payloads.append(dict(payload))
        if "format" in payload:
            yield FakeResponse([b'{"error":"structured output is unavailable"}'])
        else:
            yield FakeResponse([b'{"message":{"content":"{\\"verdict\\":\\"supported\\"}"},"done":true,"done_reason":"stop"}'])

    llm._strict_json_clear()
    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open_200)
    try:
        out = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        assert out == '{"verdict":"supported"}'
        assert len(payloads) == 2 and "format" in payloads[0] and "format" not in payloads[1]
    finally:
        llm._strict_json_clear()

    # Case 2: once a chunk is yielded, do NOT retry and concatenate a second stream
    payloads.clear()
    @contextlib.contextmanager
    def fake_open_midstream(self, url, payload, *args, **kwargs):
        payloads.append(dict(payload))
        yield FakeResponse([
            b'{"message":{"content":"{\\"verdict\\":"},"done":false}',
            b'{"error":"structured output is unavailable"}',
        ])

    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open_midstream)
    try:
        with pytest.raises(llm.LLMHTTPError):
            "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        assert len(payloads) == 1
    finally:
        llm._strict_json_clear()


def test_duplicate_json_keys_refused():
    with pytest.raises(ref.RefinementRefused):
        ref._json('{"paragraphs":[],"paragraphs":[]}')


@pytest.mark.parametrize("repair", [True, False])
def test_missing_uncertainty_requires_explicit_model_repair_and_audit(repair):
    quote = "Проверка пока не назначена."
    row = {"text": quote, "quotes": [quote]}
    seen = []

    def request(cfg, prompt, system, predict):
        seen.append((prompt, system))
        if system == ref.AUDIT_SYSTEM:
            assert json.loads(prompt)["paragraphs"][0]["uncertain"] is True
            return json.dumps({"paragraphs": [{"index": 0, "supported": True}],
                               "coverage_complete": True, "corrections_resolved": True})
        candidate = {**row, "uncertain": True} if repair and len(seen) == 2 else row
        return json.dumps({"paragraphs": [candidate]})

    if repair:
        assert_verbatim_speech(ref.refine(quote, [], "", {}, request=request), quote)
        assert len(seen) == 3
    else:
        with pytest.raises(ref.RefinementRefused, match="missing_uncertainty"):
            ref._paragraphs(json.dumps({"paragraphs": [row]}), quote)
        assert_verbatim_speech(ref.refine(quote, [], "", {}, request=request), quote)
        assert len(seen) == 2
    assert seen[0][0] == seen[1][0]
