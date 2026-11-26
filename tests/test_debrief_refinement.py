"""Tests for lightweight live-thread debrief refinement and local transport gates."""
import contextlib
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

import debrief_refinement as ref
from grounded_feed.export import write_snapshot

STAMP = "2026-01-01_120001"
SPEECH = (
    "**Участник A** [12:00]:\n"
    "Проверим модуль Альфа и подготовим выпуск к 12 июня.\n\n"
    "**Участник B** [12:02]:\n"
    "Уточнение: выпуск переносим на 15 июня, тестирование беру на себя.\n"
)
THREAD = [
    {"kind": "paragraph", "text": "Проверят модуль Альфа, выпуск 12 июня.", "topic_label": "Выпуск"},
    {"kind": "paragraph", "text": "Выпуск перенесли на 15 июня.", "topic_label": "Выпуск"},
]


def export_payload(speech: str = SPEECH, blocks: list[dict] | None = None, *,
                   late_portions: list[dict] | None = None, source_tail: int | None = None) -> dict:
    source: dict = {
        "stopped": True,
        "portions": [{
            "id": "p1",
            "body_utf8": speech,
            "body_hash": hashlib.sha256(speech.encode("utf-8")).hexdigest(),
        }],
    }
    if late_portions is not None:
        source["late_portions"] = late_portions
    if source_tail is not None:
        source["source_tail"] = source_tail
    return {
        "version": 1,
        "session_epoch": STAMP,
        "request_id": f"close-{STAMP}",
        "snapshot": {
            "complete": True,
            "source_drain_failure": None,
            "producer_cutoff_failure": None,
            "source": source,
            "blocks": blocks if blocks is not None else [
                {"serial": 1, "kind": "paragraph", "text": "Выпуск 12 июня.", "manual_id": None}
            ],
        },
    }


def ok_reply(rows: list[dict]) -> str:
    return json.dumps({"paragraphs": rows}, ensure_ascii=False)


# --- Export / receipt / load_thread contracts ---

def test_receipt_survives_retitle_and_keeps_minimal_dict_shape(tmp_path):
    path = tmp_path / f"{STAMP}.md.grounded.json"
    write_snapshot(path, export_payload())
    binding = ref.receipt(path, STAMP)
    assert ref.load_thread(tmp_path / "2026-01-01_1200_topic.md", binding) == [
        {"kind": "paragraph", "text": "Выпуск 12 июня."}
    ]
    changed = export_payload()
    changed["snapshot"]["blocks"][0]["text"] = "Подмена"
    write_snapshot(path, changed)
    with pytest.raises(ref.RefinementRefused, match="export_changed"):
        ref.load_thread(tmp_path / "2026-01-01_1200_topic.md", binding)


def test_load_thread_preserves_optional_topic_position_and_source_tail(tmp_path):
    late_text = "Поздняя реплика после стопа."
    data = export_payload(
        blocks=[{
            "serial": 1,
            "kind": "paragraph",
            "text": "Выпуск 12 июня.",
            "manual_id": None,
            "topic_label": "  Релиз  ",
            "position": 42,
        }],
        late_portions=[{
            "id": "p2",
            "body_utf8": late_text,
            "body_hash": hashlib.sha256(late_text.encode("utf-8")).hexdigest(),
        }],
    )
    path = tmp_path / f"{STAMP}.md.grounded.json"
    write_snapshot(path, data)
    thread = ref.load_thread(tmp_path / "meeting.md", ref.receipt(path, STAMP))
    assert thread == [{
        "kind": "paragraph",
        "text": "Выпуск 12 июня.",
        "topic_label": "Релиз",
        "position": 42,
        "source_tail": len(SPEECH.encode("utf-8")) + len(late_text.encode("utf-8")),
    }]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(session_epoch="2026-01-01_120002"),
    lambda p: p["snapshot"].update(complete=False),
    lambda p: p["snapshot"]["source"].update(stopped=False),
    lambda p: p["snapshot"]["source"]["portions"][0].update(body_hash="0" * 64),
    lambda p: p["snapshot"].update(blocks=[]),
])
def test_wrong_incomplete_or_corrupted_export_refused(tmp_path, mutation):
    data = export_payload()
    mutation(data)
    path = tmp_path / "meeting.md.grounded.json"
    write_snapshot(path, data)
    with pytest.raises(ref.RefinementRefused):
        ref.receipt(path, STAMP)


@pytest.mark.parametrize("patch", [{"serial": True}, {"serial": "0"}, {"kind": "invented"}, {"text": 7}])
def test_export_rejects_each_independently_invalid_block_field(patch):
    data = export_payload()
    data["snapshot"]["blocks"][0].update(patch)
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(data, STAMP)


def test_export_rejects_nondictionary_and_duplicate_serial_blocks():
    data = export_payload()
    data["snapshot"]["blocks"] = [None]
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(data, STAMP)

    dup = export_payload()
    dup["snapshot"]["blocks"].append(dict(dup["snapshot"]["blocks"][0]))
    with pytest.raises(ref.RefinementRefused, match="export_block"):
        ref._validate_export(dup, STAMP)


def test_manual_and_system_blocks_are_excluded(tmp_path):
    data = export_payload()
    data["snapshot"]["blocks"].extend([
        {"serial": 2, "kind": "answer", "text": "Бюджет согласован.", "manual_id": "m1"},
        {"serial": 3, "kind": "system", "text": "Пауза записи.", "manual_id": None},
    ])
    path = tmp_path / "meeting.md.grounded.json"
    write_snapshot(path, data)
    thread = ref.load_thread(tmp_path / "meeting.md", ref.receipt(path, STAMP))
    assert len(thread) == 1
    dumped = json.dumps(thread, ensure_ascii=False)
    assert "Бюджет согласован" not in dumped
    assert "Пауза записи" not in dumped


@pytest.mark.parametrize("binding", [None, [], {}, {"status": "pending"}, {"status": False}])
def test_thread_binding_must_be_ready_before_reading_any_export(tmp_path, monkeypatch, binding):
    def forbidden_read(path):
        pytest.fail("an unready binding must not open an export")
    monkeypatch.setattr(ref, "read_snapshot", forbidden_read)
    with pytest.raises(ref.RefinementRefused, match="export_not_ready"):
        ref.load_thread(tmp_path / f"{STAMP}.md", binding)


@pytest.mark.parametrize("name", ["../meeting.md.grounded.json", "/tmp/meeting.md.grounded.json", "meeting.json"])
def test_export_binding_cannot_escape_parent(tmp_path, name):
    with pytest.raises(ref.RefinementRefused, match="export_binding"):
        ref.load_thread(tmp_path / "meeting.md", {"status": "ready", "file": name, "stamp": STAMP})


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
        authority, window_id="w1",
        items=parse_feed(json.dumps({"items": [{"topic": "Выпуск", "text": "Выпуск 12 июня."}]})),
        expected_cursor=(0, 0), next_cursor=(1, 0),
    )
    tr.add("Уточняю: выпуск 15 июня.", speaker="Участник A")
    tr.path = tr.path.rename(tmp_path / "renamed-capture.md")
    export = tmp_path / "renamed-capture.md.grounded.json"
    assert close_grounded_export(session, tr)
    binding = ref.receipt(export, tr.stamp)
    loaded = ref.load_thread(tmp_path / "renamed.md", binding)
    assert loaded[0]["text"] == "Выпуск 12 июня."
    assert loaded[0]["topic_label"] == "Выпуск"
    assert "position" in loaded[0] and "source_tail" in loaded[0]


# --- mutation_critical: local_request and strict JSON fallback ---

def fake_local(monkeypatch, *, context=1000, failure=None, configs=None):
    import llm
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


def test_cloud_and_remote_url_are_refused_before_constructing_client(monkeypatch):
    import llm
    def forbidden(*a, **kw):
        pytest.fail("constructed client for remote configuration")
    monkeypatch.setattr(llm, "LLM", forbidden)
    with pytest.raises(ref.RefinementRefused, match="local_model_required"):
        ref.local_request({"llm": {"engine": "cloud"}}, "speech", "system", 100)
    with pytest.raises(ref.RefinementRefused, match="local_model_required"):
        ref.local_request({"llm": {"engine": "ollama", "base_url": "https://example.com"}}, "speech", "system", 100)
    with pytest.raises(ref.RefinementRefused, match="local_model_required"):
        ref.local_request({"llm": {"engine": "ollama", "base_url": "https://example.com", "allow_remote": True}},
                          "speech", "system", 100)


def test_local_request_disables_cloud_fallback_and_remote_in_client_cfg(monkeypatch):
    seen_cfgs = []
    calls = fake_local(monkeypatch, context=8192, configs=seen_cfgs)
    cfg = {"llm": {"engine": "ollama", "cloud_fallback_local": True, "allow_remote": True}}
    assert ref.local_request(cfg, "p", ref.EDIT_SINGLE_SYSTEM, 200) == "{}"
    assert seen_cfgs[0]["llm"]["cloud_fallback_local"] is False
    assert seen_cfgs[0]["llm"]["allow_remote"] is False
    assert calls[0][1]["allow_length"] is False
    assert calls[0][1]["temperature"] == 0
    assert calls[0][1]["model"] == "synthetic-local"
    assert calls[0][1]["output_schema"] == ref._paragraphs_schema(min_items=1)


def test_local_context_admits_exact_limit_and_rejects_one_character_over(monkeypatch):
    calls = fake_local(monkeypatch, context=1000)
    cfg = {"llm": {"engine": "ollama"}}
    # num_ctx=1000, output=100, reserve=512 -> (1000 - 100 - 512) * 2 = 776 chars.
    assert ref.local_request(cfg, "p" * 775, "s", 100) == "{}"
    assert len(calls) == 1
    assert "output_schema" not in calls[0][1]
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref.local_request(cfg, "p" * 776, "s", 100)
    assert len(calls) == 1


@pytest.mark.parametrize("bad_ctx", [None, "8192", True, 612, 500])
def test_local_request_rejects_invalid_or_too_small_context(monkeypatch, bad_ctx):
    calls = fake_local(monkeypatch, context=bad_ctx)
    with pytest.raises(ref.RefinementRefused, match="complete_input_exceeds_context"):
        ref.local_request({"llm": {"engine": "ollama"}}, "p", "s", 100)
    assert calls == []


@pytest.mark.parametrize("system,expected", [
    (ref.EDIT_SINGLE_SYSTEM, ref._paragraphs_schema(min_items=1)),
    (ref.EDIT_BATCH_SYSTEM, ref._paragraphs_schema(min_items=0)),
    (ref.MERGE_SYSTEM, ref._paragraphs_schema(min_items=1)),
    ("custom", None),
])
def test_local_request_passes_expected_output_schema(monkeypatch, system, expected):
    calls = fake_local(monkeypatch, context=8192)
    ref.local_request({"llm": {"engine": "ollama"}}, "{}", system, 1000)
    if expected is None:
        assert "output_schema" not in calls[0][1]
    else:
        assert calls[0][1]["output_schema"] == expected


@pytest.mark.parametrize("status,detail,reason", [
    (400, "context window exceeded", "complete_input_exceeds_context"),
    (400, "context length limit", "complete_input_exceeds_context"),
    (400, "input length too big", "complete_input_exceeds_context"),
    (400, "unrelated provider failure", "local_request_failed"),
    (200, "stopped at token budget", "output_budget"),
    (200, "unrelated provider failure", "local_request_failed"),
    (503, '{"error":"busy"}', "local_request_failed"),
])
def test_only_explicit_provider_limits_authorize_split(monkeypatch, status, detail, reason):
    import llm
    calls = fake_local(monkeypatch, context=8192, failure=llm.LLMHTTPError(status, detail))
    with pytest.raises(ref.RefinementRefused, match="^" + reason + "$"):
        ref.local_request({"llm": {"engine": "ollama"}}, "prompt", "system", 100)
    assert len(calls) == 1


def test_local_request_wraps_unexpected_exception_without_leaking_text(monkeypatch):
    fake_local(monkeypatch, context=8192, failure=RuntimeError("секретная фраза встречи"))
    with pytest.raises(ref.RefinementRefused, match="^local_request_failed$") as exc_info:
        ref.local_request({"llm": {"engine": "ollama"}}, "prompt", "system", 100)
    assert "секретная" not in str(exc_info.value)


def test_grounded_answer_falls_back_when_structured_output_unavailable(monkeypatch):
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
        yield FakeResponse([b'{"message":{"content":"{\\"paragraphs\\":[]}"},"done":true,"done_reason":"stop"}'])

    client = llm.LLM({
        "llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen3.6:35b-mlx"},
        "sufler": {"language": "ru", "role": "test"},
    })
    monkeypatch.setattr(client, "require_local_ollama_model", lambda m: m)
    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open)
    schema = ref._paragraphs_schema(min_items=0)
    try:
        first = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        second = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
    finally:
        llm._strict_json_clear()
    assert first == '{"paragraphs":[]}'
    assert second == '{"paragraphs":[]}'
    assert len(payloads) == 3
    assert payloads[0]["format"] == schema
    assert "format" not in payloads[1] and "format" not in payloads[2]


def test_grounded_answer_http200_error_fallback_and_no_midstream_retry(monkeypatch):
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
    schema = ref._paragraphs_schema(min_items=0)

    payloads = []
    @contextlib.contextmanager
    def fake_open_200(self, url, payload, *args, **kwargs):
        payloads.append(dict(payload))
        if "format" in payload:
            yield FakeResponse([b'{"error":"structured output is unavailable"}'])
        else:
            yield FakeResponse([b'{"message":{"content":"{\\"paragraphs\\":[]}"},"done":true,"done_reason":"stop"}'])

    llm._strict_json_clear()
    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open_200)
    try:
        out = "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        assert out == '{"paragraphs":[]}'
        assert len(payloads) == 2 and "format" in payloads[0] and "format" not in payloads[1]
    finally:
        llm._strict_json_clear()

    payloads.clear()
    @contextlib.contextmanager
    def fake_open_midstream(self, url, payload, *args, **kwargs):
        payloads.append(dict(payload))
        yield FakeResponse([
            b'{"message":{"content":"{\\"paragraphs\\":"},"done":false}',
            b'{"error":"structured output is unavailable"}',
        ])

    monkeypatch.setattr(llm.LLM, "_open_stream", fake_open_midstream)
    try:
        with pytest.raises(llm.LLMHTTPError):
            "".join(client.grounded_answer("p", system="s", cancel_event=threading.Event(), output_schema=schema))
        assert len(payloads) == 1
    finally:
        llm._strict_json_clear()


def test_grounded_answer_rejects_invalid_output_schema():
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


# --- Lightweight single-pass, batching, coverage, splitting, merge, sanitization ---

def test_single_pass_edits_thread_omits_previous_and_byte_metadata():
    seen = []
    events = []
    thread = [
        {"kind": "paragraph", "text": "Проверят модуль Альфа.", "topic_label": "Выпуск",
         "position": 100, "source_tail": 500},
    ]

    def request(cfg, prompt, system, predict):
        seen.append((json.loads(prompt), system, predict))
        return ok_reply([{"topic": "Выпуск", "text": "Участник A и Участник B перенесли выпуск модуля Альфа на 15 июня."}])

    out = ref.refine(SPEECH, thread, "СТАРЫЙ ПРОСТЫННЫЙ РАЗБОР НЕ ДОЛЖЕН ПОПАСТЬ", {},
                     request=request, progress=events.append)
    assert len(seen) == 1
    payload, system, predict = seen[0]
    assert system == ref.EDIT_SINGLE_SYSTEM
    assert predict == ref.SINGLE_PREDICT
    assert payload["speakers"] == ["Участник A", "Участник B"]
    assert payload["live_thread"] == [{"kind": "paragraph", "text": "Проверят модуль Альфа.", "topic": "Выпуск"}]
    assert "СТАРЫЙ ПРОСТЫННЫЙ" not in json.dumps(payload, ensure_ascii=False)
    assert "position" not in json.dumps(payload, ensure_ascii=False)
    assert "source_tail" not in json.dumps(payload, ensure_ascii=False)
    assert out == "# Разбор встречи\n\n## Выпуск\n\nУчастник A и Участник B перенесли выпуск модуля Альфа на 15 июня.\n"
    assert [e["phase"] for e in events] == ["request", "source_window", "complete"]
    assert events[1]["source_windows"] == 1 and events[1]["reason"] == "verified"


def test_single_pass_retries_once_on_bad_json():
    calls = []
    def request(cfg, prompt, system, predict):
        calls.append(system)
        if len(calls) == 1:
            return "не json"
        return ok_reply([{"topic": "", "text": "Выпуск перенесли на 15 июня."}])

    out = ref.refine(SPEECH, THREAD, "", {}, request=request)
    assert len(calls) == 2
    assert calls[1].startswith(ref.EDIT_SINGLE_SYSTEM)
    assert "Выпуск перенесли на 15 июня." in out


@pytest.mark.parametrize("bad_thread", [[], "text", [None], [{"kind": "paragraph"}]])
def test_refine_rejects_invalid_or_empty_thread_and_previous(bad_thread):
    with pytest.raises(ref.RefinementRefused, match="candidate_input"):
        ref.refine(SPEECH, bad_thread, "", {}, request=lambda *a: ok_reply([]))
    with pytest.raises(ref.RefinementRefused, match="candidate_input"):
        ref.refine(SPEECH, THREAD, None, {}, request=lambda *a: ok_reply([]))
    with pytest.raises(ref.RefinementRefused, match="empty_final_speech"):
        ref.refine("   ", THREAD, "", {}, request=lambda *a: ok_reply([]))


def test_batched_path_covers_full_speech_including_head_and_late_portions():
    # Simulate a meeting with 60 turns (~24k chars), first block position > 0 and large late_portions tail.
    turns = [f"**Участник {i % 3}** [{i:02d}:00]:\nОбсудили блок номер {i} и конкретные параметры работы системы {i}. " + ("Подробность обсуждения. " * 12) + "\n"
             for i in range(60)]
    speech = "\n".join(turns)
    thread = [
        {"kind": "paragraph", "text": f"Тезис {k}: проверили параметры блока {k}.",
         "topic_label": f"Тема {k // 5}", "position": 5000 + k * 2500, "source_tail": 90000}
        for k in range(24)
    ]
    batch_speeches = []
    systems = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        systems.append(system)
        if system == ref.MERGE_SYSTEM:
            return ok_reply([{"topic": "Итог", "text": "Все блоки сведены в компактный разбор."}])
        batch_speeches.append(data["final_speech"])
        return ok_reply([{"topic": data["live_thread"][0].get("topic", ""), "text": f"Сводка батча {len(batch_speeches)}."}])

    out = ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=request)
    assert len(batch_speeches) >= 2
    assert systems[-1] == ref.MERGE_SYSTEM
    # First turn (before first position=5000) and very last turn (late_portions tail) MUST be covered.
    assert turns[0].strip() in batch_speeches[0]
    assert turns[-1].strip() in batch_speeches[-1]
    assert "Все блоки сведены в компактный разбор." in out


@pytest.mark.parametrize("positions,tail", [
    ([1_000_000, 1_000_000, 1_000_000], 1_500_000),   # 1 MB cap saturation
    ([500, 200, 900], 1000),                           # non-monotonic
    ([100, 200, 300], 0),                              # zero source_tail
    ([None, None, None], None),                        # absent position
])
def test_thread_end_ratios_falls_back_cleanly_on_degenerate_positions(positions, tail):
    thread = [{"kind": "paragraph", "text": f"Блок {i} текст", "position": p, "source_tail": tail}
              for i, p in enumerate(positions)]
    ratios = ref._thread_end_ratios(thread)
    assert len(ratios) == 3
    assert ratios == sorted(ratios)
    assert ratios[0] > 0 and ratios[-1] == pytest.approx(1.0)


def test_single_block_with_oversized_speech_splits_speech_and_sets_speaker_at_start():
    # One huge single-speaker turn exceeding context + 1 thread block -> must split speech slice and set speaker_at_start.
    long_body = "**Участник Альфа** [00:01]:\n" + ("Длинное описание задачи номер один. " * 450)
    thread = [{"kind": "paragraph", "text": "Обсудили длинную задачу номер один."}]
    seen_payloads = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system == ref.MERGE_SYSTEM:
            return ok_reply([{"topic": "Задача", "text": "Итог по длинной задаче."}])
        seen_payloads.append(data)
        return ok_reply([{"topic": "Задача", "text": f"Фрагмент {len(seen_payloads)}."}])

    out = ref.refine(long_body, thread, "", {"llm": {"num_ctx": 4096}}, request=request)
    assert len(seen_payloads) >= 2
    assert any(p.get("speaker_at_start") == "Участник Альфа" for p in seen_payloads[1:])
    assert "Итог по длинной задаче." in out


@pytest.mark.parametrize("split_reason", ["complete_input_exceeds_context", "output_budget"])
def test_adaptive_split_on_provider_limit_for_multiblock_batch(split_reason):
    speech = "\n".join(f"**Участник A** [00:{i:02d}]:\nРеплика {i} про систему и сроки.\n" for i in range(40))
    thread = [{"kind": "paragraph", "text": f"Запись {i} про систему."} for i in range(8)]
    tripped = {"done": False}
    batch_sizes = []

    def request(cfg, prompt, system, predict):
        data = json.loads(prompt)
        if system == ref.MERGE_SYSTEM:
            return ok_reply([{"topic": "Система", "text": "Связанный итог."}])
        if system == ref.EDIT_SINGLE_SYSTEM:
            raise ref.RefinementRefused(split_reason)
        size = len(data["live_thread"])
        batch_sizes.append(size)
        if not tripped["done"] and size > 1:
            tripped["done"] = True
            raise ref.RefinementRefused(split_reason)
        return ok_reply([{"topic": "Система", "text": f"Отредактировано {len(batch_sizes)}."}])

    out = ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=request)
    assert tripped["done"] is True
    assert min(batch_sizes) < max(batch_sizes)
    assert "Связанный итог." in out


def test_merge_fallback_on_budget_or_bad_reply_preserves_batch_results():
    speech = "\n".join(f"**Участник A** [00:{i:02d}]:\nРеплика {i} " + ("текст " * 60) for i in range(40))
    thread = [{"kind": "paragraph", "text": f"Пункт {i}."} for i in range(12)]
    merge_calls = []

    def request(cfg, prompt, system, predict):
        if system == ref.MERGE_SYSTEM:
            merge_calls.append(1)
            raise ref.RefinementRefused("output_budget")
        return ok_reply([{"topic": "План", "text": f"Отредактированный пункт {len(json.loads(prompt)['live_thread'])}_{len(prompt)}."}])

    out = ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=request)
    assert len(merge_calls) == 1
    assert "## План" in out
    assert "Отредактированный пункт" in out


@pytest.mark.parametrize("fatal_reason", ["request_deferred", "local_model_required", "local_request_failed"])
def test_fatal_errors_during_batch_or_merge_are_never_swallowed(fatal_reason):
    speech = "\n".join(f"**Участник A** [00:{i:02d}]:\nРеплика {i} " + ("текст " * 60) for i in range(40))
    thread = [{"kind": "paragraph", "text": f"Пункт {i}."} for i in range(12)]
    events = []

    def request(cfg, prompt, system, predict):
        if system == ref.MERGE_SYSTEM:
            raise ref.RefinementRefused(fatal_reason)
        return ok_reply([{"topic": "План", "text": f"Пункт {len(events)}."}])

    with pytest.raises(ref.RefinementRefused, match=fatal_reason):
        ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=request, progress=events.append)
    assert not any(e["phase"] == "complete" for e in events)


def test_before_request_false_defers_immediately_without_calling_model():
    called = []
    with pytest.raises(ref.RefinementRefused, match="request_deferred"):
        ref.refine(SPEECH, THREAD, "", {}, request=lambda *a: called.append(1), before_request=lambda: False)
    assert called == []


def test_batch_unverified_reply_falls_back_to_sanitized_thread_with_partial_note():
    from graph_updater import _DEBRIEF_WINDOW_REASONS

    speech = "\n".join(f"**Участник A** [00:{i:02d}]:\nРеплика {i} " + ("текст " * 60) for i in range(40))
    thread = [{"kind": "paragraph", "text": f"Исходный пункт {i}\nс переносом.", "topic_label": "Тема"}
              for i in range(12)]
    events = []
    batch_num = {"n": 0}

    def request(cfg, prompt, system, predict):
        if system == ref.MERGE_SYSTEM:
            raise ref.RefinementRefused("output_budget")
        batch_num["n"] += 1
        if batch_num["n"] in (1, 2):  # fail first batch + its retry
            return "not-json"
        return ok_reply([{"topic": "Тема", "text": f"Уточнённый фрагмент {batch_num['n']}."}])

    out = ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=request, progress=events.append)
    assert ref.PARTIAL_NOTE in out
    assert "Исходный пункт 0 с переносом." in out
    window_events = [e for e in events if e["phase"] == "source_window"]
    assert window_events[0]["reason"] == "model_json"
    assert all(e["reason"] in _DEBRIEF_WINDOW_REASONS for e in window_events)
    assert all(1 <= e["source_windows"] <= 1_000_000 for e in window_events)


def test_all_noise_batches_refuse_no_verified_conclusion():
    speech = "\n".join(f"**Участник A** [00:{i:02d}]:\nРеплика {i} " + ("текст " * 60) for i in range(40))
    thread = [{"kind": "paragraph", "text": f"Шум {i}."} for i in range(12)]
    with pytest.raises(ref.RefinementRefused, match="no_verified_conclusion"):
        ref.refine(speech, thread, "", {"llm": {"num_ctx": 8192}}, request=lambda *a: ok_reply([]))


# --- Sanitization, deduplication, and rendering ---

def test_duplicate_json_keys_refused():
    with pytest.raises(ref.UnverifiedReply, match="duplicate_json_key"):
        ref._json('{"paragraphs":[],"paragraphs":[]}')


@pytest.mark.parametrize("bad_row", [
    {"topic": "Тема", "text": "строка1\nстрока2"},
    {"topic": "Тема", "text": "текст\u2028разрыв"},
    {"topic": "Тема", "text": "текст\x07звонок"},
    {"topic": "Тема", "text": "код ```py внутри"},
    {"topic": "Тема", "text": "# Заголовок в теле"},
    {"topic": "Тема", "text": "> Цитата"},
    {"topic": "Тема", "text": "<script>x</script>"},
    {"topic": "Тема", "text": "**Из стенограммы**"},
    {"topic": "Плохая\nтема", "text": "Нормальный текст."},
    {"topic": "Тема ```", "text": "Нормальный текст."},
    {"topic": "<html/>", "text": "Нормальный текст."},
    {"topic": "Т" * 121, "text": "Нормальный текст."},
])
def test_parse_paragraphs_rejects_unsafe_text_and_topic(bad_row):
    with pytest.raises(ref.UnverifiedReply, match="paragraph_schema"):
        ref._parse_paragraphs(ok_reply([bad_row]))


def test_render_deduplicates_before_headings_and_merges_adjacent_topics():
    rows = [
        {"topic": "Релиз", "text": "Выпуск перенесли на 15 июня."},
        {"topic": "релиз ", "text": "Тестирование берёт Участник B."},
        {"topic": "Пустая после дедупа", "text": "Выпуск перенесли на 15 июня."},
        {"topic": "Мониторинг", "text": "Настроить алерт на задержку."},
    ]
    out = ref._render(rows)
    assert out == (
        "# Разбор встречи\n\n"
        "## Релиз\n\n"
        "Выпуск перенесли на 15 июня.\n\n"
        "Тестирование берёт Участник B.\n\n"
        "## Мониторинг\n\n"
        "Настроить алерт на задержку.\n"
    )
    assert "Пустая после дедупа" not in out


def test_clean_topic_strips_leading_hashes():
    parsed = ref._parse_paragraphs(ok_reply([{"topic": "##   Релиз  ", "text": "Готово."}]))
    assert parsed == [{"topic": "Релиз", "text": "Готово."}]
