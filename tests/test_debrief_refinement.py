"""Synthetic source-to-debrief acceptance and refusal cases."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import debrief_links as links
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
    assert "подрядчик" not in body and "## Заключение" in body
    assert len(calls) == 2


def test_audited_uncertain_windows_preserve_marked_text_without_links():
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
        if system == links.CONCLUSION_SYSTEM:
            unique = {row["text"]: row for row in data["verified"]}
            return json.dumps({"paragraphs": [{k: row[k] for k in ("text", "quotes", "uncertain")}
                                               for row in unique.values()]})
        if system == links.CONCLUSION_AUDIT_SYSTEM:
            return json.dumps(good_audit(len(data["paragraphs"])))
        pytest.fail("an uncertain window reached link discovery")
    body = ref.refine(speech, [], "", {}, request=request)
    assert "".join(generated) == speech
    assert generated == audited
    assert "**Из стенограммы**" not in body
    assert body.count("**Требует уточнения.**") == 2
    assert "**Требует уточнения.** Пока неизвестно, кто согласует бюджет." in body
    assert "Продолжение промежуточного обсуждения." in body


@pytest.mark.parametrize("long_meeting", [False, True])
def test_mixed_verified_paragraphs_keep_one_local_uncertainty_marker(long_meeting):
    quotes = ["Срок проверки перенесли на вторник.", "Собеседник 1 обещал прислать отчёт.",
              "Публикацию назначили на пятницу."]
    texts = ["Проверка пройдёт во вторник.", "Отчёт обещали прислать. Имя говорящего не установлено.",
             "Публикация состоится в пятницу."]
    rows = [{"text": text, "quotes": [quote], "uncertain": i == 1}
            for i, (text, quote) in enumerate(zip(texts, quotes))]
    speech = "".join(q.ljust(ref.WINDOW_CHARS - 1) + "\n" for q in quotes) if long_meeting else "\n".join(quotes)
    events = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system in (ref.SYSTEM, ref.WINDOW_SYSTEM):
            items = [row for row in rows if row["quotes"][0] in data["final_speech"]]
            return json.dumps({"paragraphs": items})
        if system in (ref.AUDIT_SYSTEM, ref.WINDOW_AUDIT_SYSTEM):
            verdict = good_audit(len(data["paragraphs"]))
            if system == ref.WINDOW_AUDIT_SYSTEM:
                verdict.update(has_cross_window_correction=False, correction_quotes=[])
            return json.dumps(verdict)
        if system == links.CONCLUSION_SYSTEM:
            return json.dumps({"paragraphs": rows})
        if system == links.CONCLUSION_AUDIT_SYSTEM:
            return json.dumps(good_audit(len(rows)))
        ids = [item["id"] for item in data["early_paragraphs"]]
        if system == links.DISCOVER_SYSTEM:
            return json.dumps({"checked_ids": ids, "relations": []})
        assert system == links.DISCOVERY_AUDIT_SYSTEM
        return json.dumps({"checked_ids": ids, "status": "complete", "missing_relations": [], "uncertain_ids": []})

    body = ref.refine(speech, [], "", {}, request=request, progress=events.append)
    assert [line for line in body.splitlines() if any(text in line for text in texts)] == [
        texts[0], "**Требует уточнения.** " + texts[1], texts[2]]
    assert "**Из стенограммы**" not in body
    windows = [e for e in events if e["phase"] == "source_window"]
    assert len(windows) == (3 if long_meeting else 1)
    assert sum(e["reason"] == "verified_with_uncertainty" for e in windows) == 1


def test_failed_short_audit_reaches_window_repair_and_second_audit():
    quote = "Срок проверки перенесли на пятницу."
    wrong = {"text": "Срок проверки перенесли на среду.", "quotes": [quote], "uncertain": False}
    fixed = {**wrong, "text": "Проверка пройдёт в пятницу."}
    rejection = {**good_audit(), "paragraphs": [{"index": 0, "supported": False}]}
    window_audit = {"has_cross_window_correction": False, "correction_quotes": []}
    replies = iter([(ref.SYSTEM, {"paragraphs": [wrong]}), (ref.AUDIT_SYSTEM, rejection),
                    (ref.WINDOW_SYSTEM, {"paragraphs": [wrong]}),
                    (ref.WINDOW_AUDIT_SYSTEM, {**rejection, **window_audit, "repair_quotes": [quote]}),
                    (ref.WINDOW_REPAIR_SYSTEM, {"changes": [fixed]}),
                    (ref.WINDOW_AUDIT_SYSTEM, {**good_audit(), **window_audit, "repair_quotes": []})])

    def request(cfg, prompt, system, predict):
        expected_system, reply = next(replies)
        assert system == expected_system
        assert json.loads(prompt)["final_speech"] == quote
        return json.dumps(reply)

    body = ref.refine(quote, [], "", {}, request=request)
    assert fixed["text"] in body and wrong["text"] not in body and "**Из стенограммы**" not in body
    assert next(replies, None) is None


def test_short_reversed_paragraph_order_uses_source_before_audit():
    first = "Участник A: Сначала обсуждали проверку."
    second = "Участник B: Затем обсуждали выпуск."
    rows = [{"text": quote, "quotes": [quote], "uncertain": False} for quote in (second, first)]
    calls = []

    def request(*args):
        calls.append(1)
        return json.dumps({"paragraphs": rows})

    source = first + "\n" + second
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(source, [], "", {}, request=request)
    assert len(calls) == 2  # both short and window drafts are rejected before audit


def test_short_repeated_ambiguous_quote_requires_uncertainty_before_audit():
    quote = "Проверку перенесли на завтра."
    row = {"text": "Автор переноса определён.", "quotes": [quote], "uncertain": False}
    calls = []

    def request(*args):
        calls.append(1)
        return json.dumps({"paragraphs": [row]})

    source = quote + "\n" + quote
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(source, [], "", {}, request=request)
    assert len(calls) == 4  # draft and targeted retry on both paths


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
    replies = [{"paragraphs": [first, invalid]}, {"paragraphs": [first, valid]}, audit]
    if not audit_passes:
        if not windowed:
            replies.extend([{"paragraphs": [first, valid]},
                            {**audit, "has_cross_window_correction": False,
                             "correction_quotes": [], "repair_quotes": [quote]}])
        replies.append({"changes": []})
    responses = iter(replies)
    calls, gates = [], []

    def request(cfg, prompt, system, predict):
        calls.append((prompt, system))
        return json.dumps(next(responses))

    def run():
        return ref.refine(speech, [], "", {}, request=request, before_request=lambda: gates.append(1))

    body = run()
    if audit_passes:
        assert first["text"] in body
        assert f"**Требует уточнения.** {valid['text']}" in body
    else:
        assert ref.PARTIAL_NOTE in body and "**Из стенограммы**" not in body
        assert first["text"] in body and valid["text"] in body
    assert invalid["text"] not in body
    assert len(calls) == len(gates) == len(replies)
    original, retried = [json.loads(prompt) for prompt, _ in calls[:2]]
    if bad_quote:
        assert retried.pop("quote_feedback") == {
            "reason": "unsupported_quote",
            "examples": [{"index": 1, "kind": "not_in_source",
                          "quote_excerpt": "Такого утверждения в источнике нет."}]}
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
    assert "**Из стенограммы**" not in result
    assert f"**Требует уточнения.** {row['text']}" in result
    windows = [event for event in events if event["phase"] == "source_window"]
    assert len(windows) == 1 and windows[0]["verbatim"] is False
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
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(SPEECH, [], "", {}, request=request)
    assert len(calls) == 2 * requests
    assert calls[requests] == ref.WINDOW_SYSTEM
    assert all(system.startswith(ref.SYSTEM) for system in calls)  # no invalid draft reaches audit


@pytest.mark.parametrize("audit", [
    {**good_audit(), "coverage_complete": False},
    {**good_audit(), "corrections_resolved": False},
    {**good_audit(), "paragraphs": [{"index": 0, "supported": False}]},
    {**good_audit(), "paragraphs": [{"index": True, "supported": True}]},
    {**good_audit(), "paragraphs": []},
])
def test_failed_semantic_audit_and_unverified_retry_refuse_conclusion(audit):
    # The window retry independently rejects a draft with invalid evidence.
    bad = {**ROWS[0], "quotes": ["Этих слов в исходной речи нет."]}
    answers = iter(map(json.dumps, [{"paragraphs": ROWS}, audit, {"paragraphs": [bad]}, {"paragraphs": [bad]}]))
    with pytest.raises(ref.RefinementRefused, match="audit_refused"):
        ref._audit(json.dumps(audit), 1)
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(SPEECH, [], "", {}, request=lambda *a: next(answers))


@pytest.mark.parametrize("ending", ["", "\n", "\n\n", "\r\n"])
def test_short_refusal_never_publishes_source_markdown_or_draft(ending):
    source = " \tНачало.\r\n**Из стенограммы**\n\n```text\n# Маркер\n```\nВнутри ````` ряда.  " + ending
    candidate = "Выдуманная рекомендация."
    row = {"text": candidate, "quotes": ["Внутри ````` ряда."], "uncertain": False}
    audit = {**good_audit(), "paragraphs": [{"index": 0, "supported": False}]}
    answers = iter(map(json.dumps, [{"paragraphs": [row]}, audit, {"paragraphs": [row]},
                                    {**audit, "has_cross_window_correction": False, "correction_quotes": [],
                                     "repair_quotes": row["quotes"]}, {"changes": []},
                                    {**good_audit(0), "has_cross_window_correction": False, "correction_quotes": []}]))
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(source, [{"text": "Ранний черновик."}], "Непроверенный совет.", {},
                   request=lambda *args: next(answers))



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


def test_grounded_answer_rejects_invalid_output_schema():
    import threading
    import llm

    mlx_client = llm.LLM({
        "llm": {"engine": "mlx-server", "mlx_base_url": "http://127.0.0.1:8080", "mlx_model": "m", "model": "m"},
        "sufler": {"language": "ru", "role": "test"},
    })
    with pytest.raises(ValueError, match="output schema"):
        list(mlx_client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema={"type": "object"}))

    ollama_client = llm.LLM({
        "llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "m"},
        "sufler": {"language": "ru", "role": "test"},
    })
    with pytest.raises(ValueError, match="output schema"):
        list(ollama_client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema="json"))


def test_real_stopped_feed_receipt_with_unprocessed_final_speech(tmp_path):
    from daemon import close_grounded_export
    from feed_contract import parse_feed
    from grounded_feed.local_runner import LocalLoopbackAuthority
    from grounded_feed.runtime import SourceProducerCutoff, bind_product_source
    from grounded_feed.session import GroundedFeedSession
    from transcript import Transcript
    session = GroundedFeedSession(lambda event: None)
    tr = Transcript(tmp_path)
    actor = bind_product_source(session, tr, SourceProducerCutoff(lambda timeout: True),
                                source_only_notice=False, local_product_authority=True)
    client = SimpleNamespace(engine="mlx-server", base="http://127.0.0.1:8080", cloud_ready=False,
                             mlx_model="test-model", _cfg={})
    authority = LocalLoopbackAuthority.verify(client)
    tr.add("Выпуск 12 июня.", speaker="Участник A")
    session.ingest_source_once(limit=16)
    actor.commit_local_speech_window(
        authority, window_id="w1", items=parse_feed(json.dumps({"items": [{"topic": "Выпуск", "text": "Выпуск 12 июня."}]})),
        expected_cursor=(0, 0), next_cursor=(1, 0))
    tr.add("Уточняю: выпуск 15 июня.", speaker="Участник A")
    # The display filename can differ from the capture epoch used by the Stop receipt.
    tr.path = tr.path.rename(tmp_path / "renamed-capture.md")
    export = tmp_path / "renamed-capture.md.grounded.json"
    assert close_grounded_export(session, tr)
    binding = ref.receipt(export, tr.stamp)
    assert ref.load_thread(tmp_path / "renamed.md", binding)[0]["text"] == "Выпуск 12 июня."
    # Semantic completeness is not assumed: refinement must still see final speech.
    source = json.loads(export.read_text())["snapshot"]["source"]
    all_speech = source["portions"] + source["late_portions"]
    assert "15 июня" in all_speech[-1]["body_utf8"]

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
        body = ref.refine(quote, [], "", {}, request=request)
        assert f"**Требует уточнения.** {quote}" in body
        assert len(seen) == 3
    else:
        with pytest.raises(ref.RefinementRefused, match="missing_uncertainty"):
            ref._paragraphs(json.dumps({"paragraphs": [row]}), quote)
        with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
            ref.refine(quote, [], "", {}, request=request)
        assert len(seen) == 4
        assert seen[2][1] == ref.WINDOW_SYSTEM
    assert seen[0][0] == seen[1][0]
