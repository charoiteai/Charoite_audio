"""Product contract tests for the local sequential grounded feed runner (№859/№1009)."""

from __future__ import annotations

import contextlib
import json
import threading
from types import SimpleNamespace

import pytest

import cancellable_http
import privacy
from feed_contract import parse_feed
from grounded_feed.actor_core import ActorCore, ActorRefused
from grounded_feed.local_runner import (
    LocalFeedRunner,
    LocalLoopbackAuthority,
    build_grounded_answer_prompt,
    local_feed_context,
    local_feed_system,
    normalize_local_feed_raw,
)
from grounded_feed.runtime import SourceProducerCutoff, bind_product_source
from grounded_feed.session import GroundedFeedSession
from llm import LLM
from transcript import Transcript


@contextlib.contextmanager
def _always_slot(_name: str, timeout: float = 0.0, **_kw):  # noqa: ARG001
    yield True


def _fake_llm(*, base: str = "http://127.0.0.1:11434", engine: str = "ollama",
              cloud_ready: bool = False, model: str = "qwen3.6:35b-mlx",
              mlx_model: str = "", feed_model=None, lang: str = "ru",
              feed_fn=None):
    def _require_local(requested=None):
        if engine != "ollama" or cloud_ready or not privacy.is_loopback_url(base):
            raise privacy.PrivacyRefused("loopback Ollama required")
        tag = LLM.validate_local_model_tag(model if requested is None else requested)
        return tag + ":local"

    return SimpleNamespace(
        base=base,
        engine=engine,
        cloud_ready=cloud_ready,
        model=model,
        mlx_model=mlx_model,
        lang=lang,
        _cfg={"sufler": {"feed_model": feed_model}},
        require_local_ollama_model=_require_local,
        feed=feed_fn or (lambda *_a, **_kw: (c for c in ('{"none":true}',))),
    )


def test_local_loopback_authority_enforces_strict_loopback_and_unforgeable_local_selector():
    ok = LocalLoopbackAuthority.verify(_fake_llm())
    assert ok.verified is True
    assert ok.model == "qwen3.6:35b-mlx:local"
    assert ok.display_model == "qwen3.6:35b-mlx"

    forged = LocalLoopbackAuthority("ollama", "https://evil.example.com", "qwen3.6:35b-mlx:local")
    assert forged.verified is False

    for bad in (
        _fake_llm(cloud_ready=True),
        _fake_llm(base="https://api.example.com"),
        _fake_llm(base="http://192.168.1.10:11434"),
        _fake_llm(engine="cloud"),
        _fake_llm(engine="mlx-server", mlx_model="qwen-a", feed_model="qwen-b"),
    ):
        with pytest.raises(ValueError):
            LocalLoopbackAuthority.verify(bad)

    for cloud_tag in ("gemma4:cloud", "qwen3-cloud", "qwen3:local:local", "qwen3:local:cloud"):
        with pytest.raises(privacy.PrivacyRefused):
            LocalLoopbackAuthority.verify(_fake_llm(feed_model=cloud_tag))

    # Default ActorCore (InstalledNative / source-only) rejects local commits even with valid authority
    native_actor = ActorCore()
    with pytest.raises(ActorRefused, match="local speech authority not enabled"):
        native_actor.commit_local_speech_window(
            ok, window_id="w1", items=[], expected_cursor=(0, 0), next_cursor=(1, 0),
        )


def test_llm_grounded_answer_and_feed_enforce_local_selector_and_reject_cloud_tags(monkeypatch):
    client = LLM({"llm": {"model": "qwen3.6:35b-mlx"},
                  "sufler": {"language": "ru", "role": "Ты ассистент."}})
    with pytest.raises(ValueError, match="cancel_event"):
        list(client.grounded_answer("Вопрос?", system="sys", cancel_event=None))

    monkeypatch.setattr(client, "base", "https://remote.example.com")
    with pytest.raises(ValueError, match="local model server"):
        list(client.grounded_answer("Вопрос?", system="sys", cancel_event=threading.Event()))

    monkeypatch.setattr(client, "base", "http://127.0.0.1:11434")
    monkeypatch.setattr(client, "_ollama_metadata", lambda ep: (200, {"version": "0.34.4"}))

    for bad_tag in ("gemma4:cloud", "model-cloud"):
        with pytest.raises(privacy.PrivacyRefused):
            list(client.grounded_answer("Вопрос?", system="sys", model=bad_tag,
                                        cancel_event=threading.Event()))
        with pytest.raises(privacy.PrivacyRefused):
            list(client.feed("Речь", "{}", cancel_event=threading.Event(), model=bad_tag))

    captured = []

    @contextlib.contextmanager
    def fake_stream(url, payload, *_, **kwargs):
        captured.append((url, payload, kwargs))

        class Resp:
            status_code = 200

            def iter_lines(self):
                yield b'{"message":{"content":"7 "},"done":false}'
                yield b'{"message":{"content":"\\u043c\\u0438\\u043d\\u0443\\u0442."},"done":true,"done_reason":"stop"}'

        yield Resp()

    monkeypatch.setattr(client, "_open_stream", fake_stream)
    out = "".join(client.grounded_answer("Сколько?", system="sys-ru",
                                         cancel_event=threading.Event()))
    assert out == "7 минут."
    assert captured[0][1]["model"] == "qwen3.6:35b-mlx:local"
    assert captured[0][1]["think"] is False
    assert captured[0][2]["method"] == "grounded_answer"


def test_local_speech_batch_dedup_topic_pinning_adjacency_and_atomicity(tmp_path):
    events = []
    session = GroundedFeedSession(events.append)
    tr = Transcript(tmp_path)
    actor = bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                                source_only_notice=False, local_product_authority=True)
    auth = LocalLoopbackAuthority.verify(_fake_llm())

    tr.add("Обсуждаем лимиты. Кто подпишет финальный чеклист?", speaker="Ольга")
    session.ingest_source_once(limit=16)

    w1 = parse_feed(json.dumps({
        "items": [{"topic": "Лимиты и запуск",
                   "text": "Согласованы лимиты к запуску.",
                   "question": "Кто подпишет финальный чеклист?"}]
    }, ensure_ascii=False))
    r1 = actor.commit_local_speech_window(
        auth, window_id="win_1", items=w1, expected_cursor=(0, 0), next_cursor=(1, 0),
    )
    assert r1["committed"] is True and r1["added_blocks"] == 2
    # Idempotent retry of same window_id does not duplicate blocks
    dup = actor.commit_local_speech_window(
        auth, window_id="win_1", items=w1, expected_cursor=(0, 0), next_cursor=(1, 0),
    )
    assert dup["idempotent"] is True and dup["added_blocks"] == 0

    # Window 2: another topic + Case 2 regression (model repeats open question without answer)
    tr.add("Нагрузочный стенд переносим на четверг.", speaker="Максим")
    session.ingest_source_once(limit=16)
    w2 = parse_feed(json.dumps({
        "items": [{"topic": "Нагрузочный стенд",
                   "text": "Нагрузочный стенд перенесён на четверг.",
                   "question": "Кто подпишет финальный чеклист ?"}]
    }, ensure_ascii=False))
    r2 = actor.commit_local_speech_window(
        auth, window_id="win_2", items=w2, expected_cursor=(1, 0), next_cursor=(2, 0),
    )
    assert r2["added_blocks"] == 1  # paragraph added, duplicate open question suppressed!

    # Invalid answers_q rejected atomically: zero cursor advance, zero partial paragraph commit
    tr.add("Финальный чеклист подпишет Илья после ревью безопасности.", speaker="Ольга")
    session.ingest_source_once(limit=16)
    bad = parse_feed(json.dumps({
        "items": [
            {"topic": "Лимиты и запуск", "text": "Не должно сохраниться при отказе."},
            {"topic": "Лимиты и запуск", "answers_q": "q99", "answer": "Илья."},
        ]
    }, ensure_ascii=False))
    before_version, before_blocks = actor.snapshot()
    with pytest.raises(ActorRefused):
        actor.commit_local_speech_window(
            auth, window_id="win_bad", items=bad, expected_cursor=(2, 0), next_cursor=(3, 0),
        )
    assert actor.snapshot() == (before_version, before_blocks)
    assert actor.semantic.state.local_speech_cursor == (2, 0)

    # Window 3: valid answers_q="q1" from a later topic window; answer inherits q1's topic_id/label!
    w3 = parse_feed(json.dumps({
        "items": [{"topic": "ЛИМИТЫ И ЗАПУСК!",
                   "answers_q": "q1",
                   "answer": "Финальный чеклист подпишет Илья после ревью безопасности."}]
    }, ensure_ascii=False))
    r3 = actor.commit_local_speech_window(
        auth, window_id="win_3", items=w3, expected_cursor=(2, 0), next_cursor=(3, 0),
    )
    assert r3["committed"] is True and r3["added_blocks"] == 1
    assert not actor._canvas.is_open("q1")

    proj = session.projection_blocks()
    kinds = [(row["kind"], row.get("question_id"), row.get("answer_to"), row["topic_label"])
             for row in proj]
    # Answer sits directly under q1 with identical topic_label (Swift TypedFeedBlock contract)
    assert kinds == [
        ("paragraph", None, None, "Лимиты и запуск"),
        ("question", "q1", None, "Лимиты и запуск"),
        ("answer", "q1", "q1", "Лимиты и запуск"),
        ("paragraph", None, None, "Нагрузочный стенд"),
    ]
    ctx = json.loads(local_feed_context(actor.snapshot()[1]))
    assert ctx["open_questions"] == []
    assert "topic_id" not in json.dumps(ctx, ensure_ascii=False)

    export_file = tmp_path / "out.grounded.json"
    assert session.close(export_path=export_file, session_epoch="s1", request_id="close-s1")
    saved = json.loads(export_file.read_text(encoding="utf-8"))
    assert saved["snapshot"]["complete"] is True
    local_meta = saved["snapshot"]["semantic"]["local_speech"]
    assert local_meta["admission_mode"] == "local_singlepass"
    assert local_meta["cursor"] == [3, 0]
    assert local_meta["window_ids"] == ["win_1", "win_2", "win_3"]


def test_local_runner_preemption_pause_and_manual_token_atomicity(tmp_path):
    events = []
    session = GroundedFeedSession(events.append)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)

    entered = threading.Event()
    enabled_state = {"on": True}

    def blocking_feed(_speech, _ctx, *, cancel_event, system=None, model=None):
        assert system and "topic_id" in system
        assert model == "qwen3.6:35b-mlx:local"
        entered.set()
        assert cancel_event.wait(2.0), "cancel_event was not signalled on preemption"
        raise cancellable_http.StreamCancelled("preempted")

    llm = _fake_llm(feed_fn=blocking_feed)
    auth = LocalLoopbackAuthority.verify(llm)
    runner = LocalFeedRunner(
        session._actor, tr, llm, auth,
        slot=_always_slot, stop=stop, priority=priority,
        enabled=lambda: enabled_state["on"], interval_s=15.0, retry_s=0.01,
    )
    session.attach_local_runner(runner)

    tr.add("Пилот стартует в пятницу, окно работ в 22:00.", speaker="Ведущий")
    worker_out = {}

    def run_step():
        worker_out["result"] = session.step()

    t = threading.Thread(target=run_step)
    t.start()
    assert entered.wait(1.5)

    # Manual generation cancels active LocalFeedRunner stream and invalidates window_epoch
    session.manual_request("manual-1", "Первый вопрос?", topic_label="Вопрос")
    tok1 = session.begin_manual_generation("manual-1")
    t.join(timeout=1.5)
    assert not t.is_alive()
    assert worker_out["result"]["status"] == "cancelled"
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)

    # Superseding manual-1 with manual-2 blocks stale commit_manual_answer(tok1) under lock
    session.manual_request("manual-2", "Второй вопрос?", topic_label="Вопрос")
    tok2 = session.begin_manual_generation("manual-2")
    assert tok1.cancel_event.is_set() and not tok2.cancel_event.is_set()
    assert session.commit_manual_answer(tok1, "Устаревший ответ 1") is None
    committed2 = session.commit_manual_answer(tok2, "Точный ответ 2")
    assert committed2 is not None and committed2.text == "Точный ответ 2"
    assert session.end_manual_generation(tok1) is False
    assert session.end_manual_generation(tok2) is True
    proj_texts = [b["text"] for b in session.projection_blocks()]
    assert "Устаревший ответ 1" not in proj_texts and "Точный ответ 2" in proj_texts

    # Turning feed toggle off mid-stream refuses commit and preserves speech cursor (Sol I3)
    def toggle_off_during_stream(_speech, _ctx, *, cancel_event, system=None, model=None):  # noqa: ARG001
        enabled_state["on"] = False
        session.pause()
        assert cancel_event.is_set()
        return (c for c in ('{"items":[{"topic":"Пилот","text":"Не должно записаться на паузе."}]}',))

    runner.llm = _fake_llm(feed_fn=toggle_off_during_stream)
    runner._next_due = 0.0
    paused_out = runner.run_next("step-pause", force=True)
    assert paused_out["status"] == "cancelled"
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)

    # Stale window_epoch after stream completion (preempted before lock acquisition) is refused
    enabled_state["on"] = True
    session.resume()
    stale_epoch = session._actor.begin_local_speech_window()
    session.manual_request("manual-3", "Третий вопрос?", topic_label="Вопрос")
    stale_items = parse_feed('{"items":[{"topic":"Пилот","text":"Поздний коммит."}]}')
    with pytest.raises(ActorRefused, match="superseded"):
        session._actor.commit_local_speech_window(
            auth, window_id="w_stale", items=stale_items,
            expected_cursor=(0, 0), next_cursor=(1, 0), window_epoch=stale_epoch,
        )
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)

    sys_p, usr_p = build_grounded_answer_prompt("ru", "Во сколько окно?", "", tr.tail(500))
    assert "22:00" in usr_p and "Чароит" in sys_p
    assert "topic_id" in local_feed_system("ru")


def test_local_runner_propagates_cleanup_incomplete(tmp_path):
    session = GroundedFeedSession(lambda _e: None)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)
    tr.add("Проверка карантина слота.", speaker="Ведущий")
    session.ingest_source_once(limit=16)

    def poison_feed(*_a, **_kw):
        raise cancellable_http.CleanupIncomplete(object())

    llm = _fake_llm(feed_fn=poison_feed)
    runner = LocalFeedRunner(
        session._actor, tr, llm, LocalLoopbackAuthority.verify(llm),
        slot=_always_slot, stop=stop, priority=priority,
        enabled=lambda: True, interval_s=0.0, retry_s=0.05,
    )
    with pytest.raises(cancellable_http.CleanupIncomplete):
        runner.run_next("step-poison", force=True)
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)


def test_normalize_local_feed_raw_strips_only_unambiguous_nulls_and_echoes():
    q_block = SimpleNamespace(
        kind="question", question_id="q2", manual_id=None,
        text="Сколько займёт восстановление после отката?",
    )
    raw_echo = json.dumps({
        "items": [{
            "topic": "Пилот",
            "text": None,
            "question": "Сколько займёт восстановление после отката?",
            "answers_q": "q2",
            "answer": "Восстановление заняло 7 минут.",
        }]
    }, ensure_ascii=False)
    items = parse_feed(normalize_local_feed_raw(raw_echo, [q_block]))
    assert len(items) == 1
    assert items[0].answers_q == "q2" and items[0].question == ""

    # Ambiguous answers_q + DIFFERENT question must still be rejected by parse_feed
    raw_ambiguous = json.dumps({
        "items": [{
            "topic": "Пилот",
            "question": "По какой метрике оценим пилот?",
            "answers_q": "q2",
            "answer": "Доля ошибок не выше 1%.",
        }]
    }, ensure_ascii=False)
    with pytest.raises(ValueError, match="answer reference"):
        parse_feed(normalize_local_feed_raw(raw_ambiguous, [q_block]))


def test_warmup_accepts_explicit_feed_model(monkeypatch):
    client = LLM({
        "llm": {"model": "qwen3.6:35b-mlx"},
        "sufler": {"language": "ru", "role": "Ты ассистент.", "feed_model": "qwen3.8:27b-mlx"},
    })
    seen = {}

    def fake_stream(prompt, model=None, **_kw):
        seen["prompt"] = prompt
        seen["model"] = model
        yield "готов"

    monkeypatch.setattr(client, "stream", fake_stream)
    client.warmup(model="qwen3.8:27b-mlx")
    assert seen["model"] == "qwen3.8:27b-mlx"


def test_auto_feed_pause_cancels_speech_but_keeps_manual_thread_live(tmp_path):
    emitted = []
    session = GroundedFeedSession(emitted.append)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)

    def pause_auto_during_stream(_speech, _ctx, *, cancel_event, system=None, model=None):  # noqa: ARG001
        session.set_auto_feed_enabled(False)
        assert cancel_event.is_set()
        return (c for c in ('{"items":[{"topic":"Пилот","text":"Не должно записаться."}]}',))

    llm = _fake_llm(feed_fn=pause_auto_during_stream)
    auth = LocalLoopbackAuthority.verify(llm)
    runner = LocalFeedRunner(
        session._actor, tr, llm, auth,
        slot=_always_slot, stop=stop, priority=priority,
        enabled=lambda: session._auto_feed_enabled,
        interval_s=0.0, retry_s=0.0,
    )
    session.attach_local_runner(runner)

    tr.add("Пилот назначен на пятницу в 22:00.", speaker="Ведущий")
    session.ingest_source_once(limit=16)
    paused_out = runner.run_next("step-auto-off", force=True)
    assert paused_out["status"] == "cancelled"
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)
    assert session.publisher.paused is False
    assert session.step() is None

    # Manual Q/A must still publish thread frames immediately while auto-feed is off
    before_threads = len([e for e in emitted if e["type"] == "thread"])
    session.manual_request("manual-live", "Когда старт пилота?", topic_label="Вопрос")
    tok = session.begin_manual_generation("manual-live")
    committed = session.commit_manual_answer(tok, "Старт в пятницу в 22:00.")
    assert committed is not None
    assert session.end_manual_generation(tok) is True
    after_threads = [e for e in emitted if e["type"] == "thread"]
    assert len(after_threads) >= before_threads + 2
    assert "Старт в пятницу в 22:00." in after_threads[-1]["text"]

    # Re-enabling auto-feed resumes automatic speech commits from cursor (0, 0)
    runner.llm = _fake_llm(feed_fn=lambda *_a, **_kw: (
        c for c in ('{"items":[{"topic":"Пилот","text":"Пилот назначен на пятницу в 22:00."}]}',)
    ))
    session.set_auto_feed_enabled(True)
    resumed_out = session.step()
    assert resumed_out["status"] == "committed"
    assert session._actor.semantic.state.local_speech_cursor == (1, 0)


def test_cancel_live_generation_blocks_manual_commit_and_overlong_request_recovers(tmp_path):
    session = GroundedFeedSession(lambda _e: None)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)
    llm = _fake_llm()
    runner = LocalFeedRunner(
        session._actor, tr, llm, LocalLoopbackAuthority.verify(llm),
        slot=_always_slot, stop=stop, priority=priority,
        enabled=lambda: True, interval_s=0.0, retry_s=0.0,
    )
    session.attach_local_runner(runner)

    session.manual_request("manual-c1", "Первый вопрос?", topic_label="Вопрос")
    tok = session.begin_manual_generation("manual-c1")
    session.cancel_live_generation(timeout_s=0.0)
    assert tok.cancel_event.is_set()
    assert session.commit_manual_answer(tok, "Поздний ответ") is None
    with pytest.raises(ActorRefused, match="cancelled"):
        session._actor.manual_answer("manual-c1", "Прямой поздний ответ",
                                     cancel_event=tok.cancel_event)
    assert session.end_manual_generation(tok) is False

    # Overlong UTF-8 question (> 8000 bytes) refuses cleanly without poisoning session
    with pytest.raises(ActorRefused, match="manual question"):
        session.manual_request("manual-overlong", "я" * 4500, topic_label="Вопрос")
    qid = session.manual_request("manual-ok", "Короткий вопрос?", topic_label="Вопрос")
    assert qid.startswith("q:manual:")
    snap = tmp_path / "export.json"
    assert session.close(export_path=snap, session_epoch="t", request_id="r") is True


def test_verify_or_defer_and_ensure_verified_lifecycle(tmp_path):
    with pytest.raises(ValueError, match="requires an LLM"):
        LocalLoopbackAuthority.verify(None)

    no_contract = SimpleNamespace(
        base="http://127.0.0.1:11434", engine="ollama", cloud_ready=False,
        model="qwen3.6:35b-mlx", _cfg={}, require_local_ollama_model="not_callable",
    )
    with pytest.raises(privacy.PrivacyRefused, match="missing local Ollama contract"):
        LocalLoopbackAuthority.verify_or_defer(no_contract)

    for empty_llm in (
        _fake_llm(model="   ", feed_model=""),
        _fake_llm(engine="mlx-server", mlx_model="  "),
    ):
        with pytest.raises(ValueError, match="empty"):
            LocalLoopbackAuthority.verify_or_defer(empty_llm)

    with pytest.raises(privacy.PrivacyRefused):
        LocalLoopbackAuthority.verify_or_defer(_fake_llm(feed_model="gemma4:cloud"))
    with pytest.raises(ValueError):
        LocalLoopbackAuthority.verify_or_defer(_fake_llm(cloud_ready=True))

    mlx_ok = LocalLoopbackAuthority.verify(
        _fake_llm(engine="mlx-server", mlx_model="qwen3.6-mlx", feed_model="qwen3.6-mlx")
    )
    assert mlx_ok.verified is True
    assert mlx_ok.pending_server is False
    assert mlx_ok.admitted is True
    assert mlx_ok.model == "qwen3.6-mlx"
    assert mlx_ok.display_model == "qwen3.6-mlx"

    for bad_ret in (123, "qwen3", ":local", "qwen:local:local", "qwen:cloud:local", "qwen-cloud:local"):
        bad_adapter = _fake_llm()
        bad_adapter.require_local_ollama_model = lambda _r=None, v=bad_ret: v
        with pytest.raises(privacy.PrivacyRefused, match="verified local model"):
            LocalLoopbackAuthority.verify(bad_adapter)
        with pytest.raises(privacy.PrivacyRefused, match="verified local model"):
            LocalLoopbackAuthority.verify_or_defer(bad_adapter)

    forged = LocalLoopbackAuthority("ollama", "http://127.0.0.1:11434", "qwen3.6:35b-mlx:local")
    assert forged.verified is False and forged.pending_server is False and forged.admitted is False
    with pytest.raises(ValueError, match="unadmitted"):
        forged.ensure_verified(_fake_llm())

    server_up = {"up": False}

    def flaky_require(requested=None):
        if not server_up["up"]:
            raise OSError("connection refused")
        return LLM.validate_local_model_tag(requested or "qwen3.6:35b-mlx") + ":local"

    llm = _fake_llm(
        feed_fn=lambda *_a, **_kw: (c for c in ('{"items":[{"topic":"Старт","text":"Сервер ожил."}]}',))
    )
    llm.require_local_ollama_model = flaky_require

    deferred, err = LocalLoopbackAuthority.verify_or_defer(llm)
    assert isinstance(err, OSError)
    assert deferred.verified is False
    assert deferred.pending_server is True
    assert deferred.admitted is True
    assert deferred.model == "qwen3.6:35b-mlx:local"
    assert deferred.display_model == "qwen3.6:35b-mlx"

    emitted = []
    session = GroundedFeedSession(emitted.append)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)

    with pytest.raises(ValueError, match="admitted"):
        LocalFeedRunner(session._actor, tr, llm, forged,
                        slot=_always_slot, stop=stop, priority=priority, enabled=lambda: True)

    now = {"t": 100.0}
    runner = LocalFeedRunner(
        session._actor, tr, llm, deferred,
        slot=_always_slot, stop=stop, priority=priority, enabled=lambda: True,
        clock=lambda: now["t"],
    )
    assert runner.interval_s == 15.0 and runner.retry_s == 2.0
    session.attach_local_runner(runner)

    tr.add("Обсудили перенос окна релиза на субботу.", speaker="Ирина")
    session.ingest_source_once(limit=16)

    down_res = runner.run_next("step-down", force=True)
    assert down_res == {"status": "unavailable", "reason": "OSError"}
    assert runner._next_due == 115.0
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)
    with pytest.raises(ValueError, match="verified"):
        session.adopt_local_authority(deferred)

    server_up["up"] = True
    verified_auth = deferred.ensure_verified(llm)
    session.adopt_local_authority(verified_auth)
    assert runner.authority is verified_auth
    runner.authority = deferred  # verify runner self-promotes on step as well
    up_res = session.step()  # _next_due > 100 -> waiting unless clock advances
    assert up_res == {"status": "waiting"}
    now["t"] = 116.0
    up_res = session.step()
    assert up_res["status"] == "committed"
    assert runner.authority.verified is True
    assert runner.authority.ensure_verified(llm) is runner.authority
    assert any(e["type"] == "thread" and "Сервер ожил." in e["text"] for e in emitted)


def test_poison_window_skip_after_max_rejections_and_cursor_advance(tmp_path):
    emitted = []
    session = GroundedFeedSession(emitted.append)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)

    outputs = iter([
        "не json 1",
        '{"items":[{"topic":"Сбой","answers_q":"q999","answer":"нет такого"}]}',
        "не json 3",
        '{"items":[{"topic":"План","text":"Второе окно прошло чисто."}]}',
    ])
    now = {"t": 10.0}
    llm = _fake_llm(lang="en", feed_fn=lambda *_a, **_kw: (c for c in (next(outputs),)))
    runner = LocalFeedRunner(
        session._actor, tr, llm, LocalLoopbackAuthority.verify(llm),
        slot=_always_slot, stop=stop, priority=priority, enabled=lambda: True,
        interval_s=12.0, retry_s=2.0, clock=lambda: now["t"],
    )
    session.attach_local_runner(runner)

    tr.add("Первое битое окно реплик.", speaker="Аня")
    session.ingest_source_once(limit=16)

    r1 = runner.run_next("p1", force=True)
    assert r1 == {"status": "rejected", "reason": "JSONDecodeError", "attempt": 1}
    assert runner._next_due == 22.0
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)

    r2 = runner.run_next("p2", force=True)
    assert r2 == {"status": "rejected", "reason": "ActorRefused", "attempt": 2}
    assert session._actor.semantic.state.local_speech_cursor == (0, 0)

    # Queue second portion before 3rd failure on cursor (0,0)->(1,0) isn't ingested yet;
    # test caught-up skip first, then ingest second portion and commit!
    r3 = session.step()  # waiting since now=10 < 22
    assert r3 == {"status": "waiting"}
    now["t"] = 25.0
    r3 = session.step()
    assert r3["status"] == "skipped" and r3["skipped"] is True
    assert runner._next_due == 37.0
    assert session._actor.semantic.state.local_speech_cursor == (1, 0)
    proj = session.projection_blocks()
    assert any(b["kind"] == "system" and "Skipped one live-feed window" in b["text"] for b in proj)
    assert any(e["type"] == "thread" for e in emitted)

    tr.add("Второе чистое окно реплик.", speaker="Борис")
    now["t"] = 40.0
    r4 = session.step()
    assert r4["status"] == "committed"
    assert session._actor.semantic.state.local_speech_cursor == (2, 0)
    assert any(b["kind"] == "paragraph" and b["text"] == "Второе окно прошло чисто."
               for b in session.projection_blocks())

    # Live speech keeps growing between rejections on cursor (2, 0): counter does NOT reset
    # and skip advances only MAX_POISON_SKIP_CHARS instead of swallowing the entire backlog.
    seen_chunks: list[str] = []
    grow_outputs = iter([
        "сбой 1",
        "сбой 2",
        "сбой 3",
        '{"items":[{"topic":"Хвост","text":"Остаток живой речи сохранён."}]}',
    ])
    runner.llm = _fake_llm(
        lang="ru",
        feed_fn=lambda text, *_a, **_kw: (seen_chunks.append(text) or (c for c in (next(grow_outputs),))),
    )
    tr.add("Ядовитое начало " + ("фраза " * 260), speaker="Аня")
    session.ingest_source_once(limit=16)
    g1 = runner.run_next("g1", force=True)
    assert g1 == {"status": "rejected", "reason": "JSONDecodeError", "attempt": 1}

    tr.add("Новая реплика во время встречи 1 " + ("деталь " * 200), speaker="Борис")
    session.ingest_source_once(limit=16)
    g2 = runner.run_next("g2", force=True)
    assert g2 == {"status": "rejected", "reason": "JSONDecodeError", "attempt": 2}
    assert len(seen_chunks[-1]) <= LocalFeedRunner.MAX_POISON_SKIP_CHARS

    tr.add("Новая реплика во время встречи 2 — хвост решения.", speaker="Аня")
    session.ingest_source_once(limit=16)
    g3 = runner.run_next("g3", force=True)
    assert g3["status"] == "skipped" and g3["skipped"] is True
    skipped_cursor = session._actor.semantic.state.local_speech_cursor
    assert skipped_cursor > (2, 0)
    assert skipped_cursor[0] < len(session._actor.semantic.state.portions)
    assert runner._next_due == 40.5

    g4 = runner.run_next("g4", force=True)
    assert g4["status"] == "committed"
    assert session._actor.semantic.state.local_speech_cursor == (
        len(session._actor.semantic.state.portions), 0
    )
    assert any(b["kind"] == "paragraph" and b["text"] == "Остаток живой речи сохранён."
               for b in session.projection_blocks())


def test_local_runner_scheduling_branches_and_prompt_normalizer(tmp_path):
    session = GroundedFeedSession(lambda _e: None)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)

    now = {"t": 50.0}
    slot_ok = {"ok": True}

    @contextlib.contextmanager
    def gated_slot(_name: str, **_kw):
        yield slot_ok["ok"]

    llm = _fake_llm(lang="ru")
    runner = LocalFeedRunner(
        session._actor, tr, llm, LocalLoopbackAuthority.verify(llm),
        slot=gated_slot, stop=stop, priority=priority, enabled=lambda: True,
        interval_s=10.0, retry_s=3.0, clock=lambda: now["t"],
    )
    assert runner.shutdown_confirmed() is False
    runner.cancel_active()  # safe no-op when idle

    assert runner.run_next("idle1", force=True) == {"status": "idle"}
    assert runner._next_due == 60.0

    tr.add("Первая длинная часть " + ("слово " * 1600), speaker="А")
    tr.add("Вторая часть после среза.", speaker="Б")
    session.ingest_source_once(limit=16)

    priority.set()
    assert runner.run_next("prio1", force=True) == {"status": "yielded"}
    priority.clear()

    slot_ok["ok"] = False
    assert runner.run_next("busy1", force=True) == {"status": "busy"}
    assert runner._next_due == 53.0
    slot_ok["ok"] = True

    def err_feed(*_a, **_kw):
        raise RuntimeError("boom")

    runner.llm = _fake_llm(lang="ru", feed_fn=err_feed)
    assert runner.run_next("fail1", force=True) == {"status": "failed", "reason": "RuntimeError"}
    assert runner._next_due == 60.0

    # Commit first sliced window while portion 2 is still pending -> _next_due = now + 0.5
    runner.llm = _fake_llm(
        lang="ru",
        feed_fn=lambda *_a, **_kw: (c for c in ('{"items":[{"topic":"Ч1","text":"Срез 1."}]}',)),
    )
    c1 = runner.run_next("c1", force=True)
    assert c1["status"] == "committed"
    assert runner._next_due == 50.5

    # Skip with Russian notice & more_pending -> _next_due = now + 0.5
    tr.add("Третья длинная часть " + ("слово " * 1600), speaker="В")
    tr.add("Четвёртая часть после среза.", speaker="Г")
    session.ingest_source_once(limit=16)
    runner.llm = _fake_llm(lang="ru", feed_fn=lambda *_a, **_kw: (c for c in ("bad-json",)))
    cur_before = session._actor.semantic.state.local_speech_cursor
    for i in range(2):
        assert runner.run_next(f"rj{i}", force=True)["attempt"] == i + 1
    sk = runner.run_next("rj2", force=True)
    assert sk["status"] == "skipped" and runner._next_due == 50.5
    assert session._actor.semantic.state.local_speech_cursor != cur_before
    assert any("Пропущен фрагмент живой нити" in b["text"] for b in session.projection_blocks())

    stop.set()
    assert runner.run_next("stopped", force=True) == {"status": "paused"}
    stop.clear()
    assert runner.cancel_and_join() is True
    assert runner.shutdown_confirmed() is True
    assert runner.run_next("retired", force=True) == {"status": "paused"}

    # Prompt builder (ru / zh / en, empty + sliced >3500 / >3000)
    for lang, empty_tok in (("ru", "Стенограмма пока пуста."),
                            ("zh", "逐字稿暂时为空。"),
                            ("en", "Transcript is currently empty.")):
        s_empty, u_empty = build_grounded_answer_prompt(lang, "  Q?  ", "  ", "  ", " ")
        assert s_empty and empty_tok in u_empty and "Q?" in u_empty
        long_feed = "HEAD_DROP_" + ("F" * 3500)
        long_tail = "TAIL_DROP_" + ("S" * 3000)
        _, u_full = build_grounded_answer_prompt(lang, "Q?", long_feed, long_tail, "MEM_OK")
        assert "MEM_OK" in u_full
        assert "HEAD_DROP_" not in u_full and ("F" * 100) in u_full
        assert "TAIL_DROP_" not in u_full and ("S" * 100) in u_full
        assert empty_tok not in u_full

    # normalize_local_feed_raw branches
    fenced = '```json\n{"items":[{"topic":"Тема","text":null,"question":"Вопрос?"}]}\n```'
    norm_fenced = normalize_local_feed_raw(fenced)
    assert "Тема" in norm_fenced and "null" not in norm_fenced
    for untouched in (
        '```json\n{"items":[{"topic":"Тема","text":null}]}',
        "{broken",
        "[]",
        '{"none":true}',
        '{"items":"bad"}',
        '{"items":[123]}',
        '{"items":[{"topic":"Тема","text":"Без изменений."}]}',
    ):
        assert normalize_local_feed_raw(untouched) is untouched

    manual_q = SimpleNamespace(kind="question", question_id="q1", manual_id="m1", text="Ручной?")
    raw_manual_echo = json.dumps({
        "items": [{"topic": "Т", "question": "Ручной?", "answers_q": "q1", "answer": "Да."}]
    }, ensure_ascii=False)
    assert normalize_local_feed_raw(raw_manual_echo, [manual_q]) is raw_manual_echo


def test_llm_critical_doors_ollama_cache_mlx_and_ndjson(monkeypatch):
    for invalid_tag in (None, 42, "", "qwen 3", "qwen\t3", "qwen\x07", "gemma:CLOUD",
                        "qwen:cloud:local", "qwen:local:local", "qwen:local:v2",
                        ":local", "qwen-CLOUD"):
        with pytest.raises(privacy.PrivacyRefused):
            LLM.validate_local_model_tag(invalid_tag)
    assert LLM.validate_local_model_tag("qwen3.6:35b-mlx") == "qwen3.6:35b-mlx"
    assert LLM.validate_local_model_tag("qwen3.6:35b-mlx:LOCAL") == "qwen3.6:35b-mlx"

    client = LLM({"llm": {"model": "qwen3.6:35b-mlx"},
                  "sufler": {"language": "ru", "role": "Ассистент."}})
    version_calls = {"n": 0, "resp": (200, {"version": "0.34.4+mlx.1"})}

    def fake_meta(ep):
        assert ep == "version"
        version_calls["n"] += 1
        val = version_calls["resp"]
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(client, "_ollama_metadata", fake_meta)
    assert client.require_local_ollama_model("qwen3.6:35b-mlx") == "qwen3.6:35b-mlx:local"
    assert client.require_local_ollama_model("qwen3.8:27b-mlx") == "qwen3.8:27b-mlx:local"
    assert version_calls["n"] == 1  # cached for same loopback base!

    client.base = "http://127.0.0.1:11435"
    assert client.require_local_ollama_model("qwen3.6:35b-mlx") == "qwen3.6:35b-mlx:local"
    assert version_calls["n"] == 2  # base changed -> re-verified!

    for bad_meta in (
        ValueError("bad json"),
        (503, {"version": "0.34.4"}),
        (200, {"error": "down"}),
        (200, "not-a-dict"),
        (200, {"version": "0.34.3"}),
        (200, {"version": "0.34.4-rc1"}),
    ):
        client._verified_ollama_local_base = None
        version_calls["resp"] = bad_meta
        with pytest.raises(RuntimeError):
            client.require_local_ollama_model("qwen3.6:35b-mlx")

    version_calls["resp"] = (200, {"version": "0.34.4"})
    client._verified_ollama_local_base = client.base

    def make_stream(lines):
        @contextlib.contextmanager
        def _cm(url, payload, busy_wait, **kwargs):
            seen.append((url, payload, busy_wait, kwargs))

            class Resp:
                status_code = 200

                def iter_lines(self_inner):
                    yield from lines

            yield Resp()

        return _cm

    # Ollama NDJSON branches for feed & grounded_answer
    seen = []
    monkeypatch.setattr(client, "_open_stream", make_stream([
        b"",
        b'{"message":{"content":"{\\"none\\":true}"},"done":false}',
        b'{"message":{"content":""},"done":true,"done_reason":"stop"}',
    ]))
    assert "".join(client.feed("Речь", "{}", cancel_event=threading.Event())) == '{"none":true}'
    assert seen[-1][0].endswith("/api/chat")
    assert seen[-1][1]["stream"] is True and seen[-1][1]["think"] is False
    assert seen[-1][1]["options"]["num_predict"] == 600

    for bad_feed_lines in (
        [b'{"error":"oom"}'],
        [b'{"message":{"content":"x"},"done":true,"done_reason":"length"}'],
        [b'{"message":{"content":"x"},"done":true,"done_reason":"unload"}'],
        [b'{"message":{"content":"x"},"done":false}'],
    ):
        monkeypatch.setattr(client, "_open_stream", make_stream(bad_feed_lines))
        with pytest.raises(Exception):
            list(client.feed("Речь", "{}", cancel_event=threading.Event()))

    seen.clear()
    monkeypatch.setattr(client, "_open_stream", make_stream([
        b"",
        '{"message":{"content":"Ок"},"done":true,"done_reason":"length"}'.encode("utf-8"),
    ]))
    assert "".join(client.grounded_answer(
        "Вопрос?", system="sys", cancel_event=threading.Event(), temperature=0.25
    )) == "Ок"
    assert seen[-1][1]["stream"] is True and seen[-1][1]["think"] is False
    assert seen[-1][1]["options"]["num_predict"] == 400
    assert seen[-1][1]["options"]["temperature"] == 0.25

    for bad_ans_lines in (
        [b'{"error":"oom"}'],
        [b'{"message":{"content":"x"},"done":true,"done_reason":"unload"}'],
        [b'{"message":{"content":"x"},"done":false}'],
    ):
        monkeypatch.setattr(client, "_open_stream", make_stream(bad_ans_lines))
        with pytest.raises(Exception):
            list(client.grounded_answer("Вопрос?", system="sys", cancel_event=threading.Event()))

    # MLX SSE branches for feed & grounded_answer
    mlx_client = LLM({
        "llm": {"engine": "mlx-server", "model": "qwen3.6:35b-mlx",
                "mlx_base_url": "http://127.0.0.1:8080", "mlx_model": "qwen-mlx"},
        "sufler": {"language": "ru", "role": "Ассистент."},
    })
    with pytest.raises(ValueError, match="does not match"):
        list(mlx_client.feed("Речь", "{}", cancel_event=threading.Event(), model="other"))
    with pytest.raises(ValueError, match="does not match"):
        list(mlx_client.grounded_answer("В?", system="s", cancel_event=threading.Event(), model="other"))

    seen.clear()
    monkeypatch.setattr(mlx_client, "_open_stream", make_stream([
        b"",
        b": keepalive",
        b'data: {"choices":[{"delta":{"content":"{\\"none\\":true}"},"finish_reason":null}]}',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        b"data: [DONE]",
    ]))
    assert "".join(mlx_client.feed("Речь", "{}", cancel_event=threading.Event())) == '{"none":true}'
    assert seen[-1][0].endswith("/v1/chat/completions")
    assert seen[-1][1]["stream"] is True

    for bad_mlx_feed in (
        [b"data: [DONE]"],
        [b'data: {"error":"bad"}'],
        [b'data: {"choices":[]}'],
        [b'data: {"choices":[{"delta":{"content":123}}]}'],
        [b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}'],
        [b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"content_filter"}]}'],
        [b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}'],
    ):
        monkeypatch.setattr(mlx_client, "_open_stream", make_stream(bad_mlx_feed))
        with pytest.raises(Exception):
            list(mlx_client.feed("Речь", "{}", cancel_event=threading.Event()))

    seen.clear()
    monkeypatch.setattr(mlx_client, "_open_stream", make_stream([
        b"",
        b": keepalive",
        'data: {"choices":[{"delta":{"content":"Ответ"},"finish_reason":"length"}]}'.encode("utf-8"),
        b"data: [DONE]",
    ]))
    assert "".join(mlx_client.grounded_answer(
        "В?", system="s", cancel_event=threading.Event()
    )) == "Ответ"
    assert seen[-1][1]["stream"] is True and seen[-1][1]["max_tokens"] == 400

    for bad_mlx_ans in (
        [b"data: [DONE]"],
        [b'data: {"error":"bad"}'],
        [b'data: {"choices":[]}'],
        [b'data: {"choices":[{"delta":{"content":123}}]}'],
        [b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"content_filter"}]}'],
        [b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}'],
    ):
        monkeypatch.setattr(mlx_client, "_open_stream", make_stream(bad_mlx_ans))
        with pytest.raises(Exception):
            list(mlx_client.grounded_answer("В?", system="s", cancel_event=threading.Event()))


def test_cancel_combinators_authority_context_runner_and_llm_boundaries(tmp_path, monkeypatch, _сеть_закрыта):
    import dataclasses
    from feed_contract import SpeechCursor
    from grounded_feed.actor_core import ActorStopped
    from grounded_feed.local_runner import (
        _DisabledCancelEvent,
        combine_cancel,
        slice_ingested_speech,
    )

    # _DisabledCancelEvent & _CombinedCancelEvent boundaries
    assert _DisabledCancelEvent(lambda: True).is_set() is False
    assert _DisabledCancelEvent(lambda: False).is_set() is True

    preset = threading.Event()
    preset.set()
    assert combine_cancel(preset).wait(0.0) is True

    unset = threading.Event()
    assert combine_cancel(unset).wait(0.0) is False
    assert combine_cancel(unset).wait(-0.5) is False
    assert combine_cancel().wait(0.02) is False

    flag = {"set": False}
    no_wait_ev = SimpleNamespace(is_set=lambda: flag["set"])
    assert combine_cancel(no_wait_ev).wait(0.02) is False
    timer = threading.Timer(0.01, lambda: flag.update(set=True))
    timer.start()
    try:
        assert combine_cancel(no_wait_ev).wait(0.3) is True
        assert combine_cancel(no_wait_ev).wait(None) is True
    finally:
        timer.cancel()

    # LocalLoopbackAuthority frozen, repr, forged equality, missing cloud_ready attribute
    bare_llm = SimpleNamespace(
        engine="ollama",
        base="http://127.0.0.1:11434",
        model="qwen3.6:35b-mlx",
        _cfg={},
        require_local_ollama_model=lambda t: t + ":local",
    )
    ok_auth = LocalLoopbackAuthority.verify(bare_llm)
    assert ok_auth.verified is True
    assert "_proof" not in repr(ok_auth)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ok_auth.model = "other"  # type: ignore[misc]
    forged_same = LocalLoopbackAuthority(ok_auth.engine, ok_auth.base, ok_auth.model)
    assert forged_same != ok_auth

    # local_feed_context filters, caps, exact max_chars, and step-by-step trimming
    def blk(serial, kind, *, text="x", topic="Тема", qid=None, manual_id=None, pos=0, sys_after=0):
        return SimpleNamespace(
            position=pos, system_after=sys_after, serial=serial,
            kind=kind, text=text, topic_label=topic,
            question_id=qid, manual_id=manual_id,
        )

    assert local_feed_context([]) == '{"open_questions": [], "known_topics": []}'

    q_only_raw = local_feed_context([blk(1, "question", text="Открытый?", topic="Пилот", qid="q1")])
    assert "Пилот" in q_only_raw
    assert json.loads(q_only_raw) == {
        "open_questions": [{"id": "q1", "topic": "Пилот", "question": "Открытый?"}],
        "known_topics": [],
    }
    t_only_raw = local_feed_context([blk(1, "paragraph", text="Факт 1", topic="Пилот")])
    assert json.loads(t_only_raw) == {
        "open_questions": [],
        "known_topics": [{"topic": "Пилот", "facts": ["Факт 1"]}],
    }

    # Filter edge cases: system, manual_id!=None, q:manual:, empty topic_label, non-answer with qid, answer with qid=None
    mixed_blocks = [
        blk(1, "system", text="sys", topic="Sys"),
        blk(2, "paragraph", text="manual para", topic="ManualT", manual_id="m1"),
        blk(3, "question", text="manual q?", topic="ManualT", qid="q:manual:1"),
        blk(4, "paragraph", text="no topic", topic=""),
        blk(5, "question", text="Остался открытым?", topic="Т1", qid="q1"),
        blk(6, "paragraph", text="Не ответ", topic="Т1", qid="q1"),
        blk(7, "answer", text="Сирота", topic="Т1", qid=None),
    ]
    mixed_parsed = json.loads(local_feed_context(mixed_blocks))
    assert [q["id"] for q in mixed_parsed["open_questions"]] == ["q1"]
    assert mixed_parsed["known_topics"] == [{"topic": "Т1", "facts": ["Не ответ"]}]

    # Caps: 14 questions -> last 12; 10 topics -> last 8; 6 facts -> last 4
    many_blocks = (
        [blk(i, "question", text=f"Q{i}?", topic="Т", qid=f"q{i}") for i in range(1, 15)]
        + [blk(100 + t * 10 + f, "paragraph", text=f"F{f}", topic=f"T{t}")
           for t in range(1, 11) for f in range(1, 7)]
    )
    capped = json.loads(local_feed_context(many_blocks, max_chars=9000))
    assert [q["id"] for q in capped["open_questions"]] == [f"q{i}" for i in range(3, 15)]
    assert [t["topic"] for t in capped["known_topics"]] == [f"T{i}" for i in range(3, 11)]
    assert capped["known_topics"][0]["facts"] == ["F3", "F4", "F5", "F6"]

    # Exact len(raw) == max_chars boundary
    exact_raw = local_feed_context([blk(1, "paragraph", text="Точно", topic="Т")])
    assert local_feed_context([blk(1, "paragraph", text="Точно", topic="Т")], max_chars=len(exact_raw)) == exact_raw

    # Trim step 1: drop oldest fact in first topic when len(facts) > 1
    two_facts = [blk(1, "paragraph", text="Первый длинный факт", topic="Т"),
                 blk(2, "paragraph", text="Второй", topic="Т")]
    one_fact_len = len(local_feed_context([two_facts[1]]))
    trimmed_fact = json.loads(local_feed_context(two_facts, max_chars=one_fact_len))
    assert trimmed_fact["known_topics"] == [{"topic": "Т", "facts": ["Второй"]}]

    # Trim step 2: drop oldest topic when len(kept_topics) > 1
    two_topics = [blk(1, "paragraph", text="Старая тема", topic="Т1"),
                  blk(2, "paragraph", text="Новая", topic="Т2")]
    t2_only_len = len(local_feed_context([two_topics[1]]))
    trimmed_topic = json.loads(local_feed_context(two_topics, max_chars=t2_only_len))
    assert trimmed_topic["known_topics"] == [{"topic": "Т2", "facts": ["Новая"]}]

    # Trim step 3: clear last topic while keeping question
    q_and_t = [blk(1, "question", text="В?", topic="Т", qid="q1"),
               blk(2, "paragraph", text="Длинный факт темы", topic="Т")]
    q1_only_len = len(local_feed_context([q_and_t[0]]))
    trimmed_clear_t = json.loads(local_feed_context(q_and_t, max_chars=q1_only_len))
    assert trimmed_clear_t == {"open_questions": [{"id": "q1", "topic": "Т", "question": "В?"}], "known_topics": []}

    # Trim step 4: drop older question when len(kept_q) > 1
    two_qs = [blk(1, "question", text="Первый длинный вопрос?", topic="Т", qid="q1"),
              blk(2, "question", text="В2?", topic="Т", qid="q2")]
    q2_only_len = len(local_feed_context([two_qs[1]]))
    trimmed_q = json.loads(local_feed_context(two_qs, max_chars=q2_only_len))
    assert [q["id"] for q in trimmed_q["open_questions"]] == ["q2"]

    # Trim step 5: single oversized Cyrillic question sliced without ASCII escaping
    huge_q = [blk(1, "question", text="я" * 200, topic="Т", qid="q1")]
    sliced_q = local_feed_context(huge_q, max_chars=80)
    assert len(sliced_q) == 80 and "я" in sliced_q

    # slice_ingested_speech cursor check & display_name
    with pytest.raises(ValueError, match="speech cursor"):
        slice_ingested_speech([], {}, None)  # type: ignore[arg-type]
    p_stub = SimpleNamespace(timestamp="10:00", speaker_id="spk1", body_utf8="Привет мир")
    sliced = slice_ingested_speech([p_stub], {"spk1": "raw"}, SpeechCursor(0, 0),
                                   display_name=lambda s: s.upper())
    assert sliced.text == "[10:00] RAW: Привет мир"

    # normalize_local_feed_raw half-fenced inputs left untouched for parse_feed to reject
    for half_fenced in (
        '12345678{"items":[{"topic":"Т","text":null}]}\n```',
        '```json\n{"items":[{"topic":"Т","text":null}]}',
    ):
        assert normalize_local_feed_raw(half_fenced) == half_fenced

    # LocalFeedRunner boundaries
    session = GroundedFeedSession(lambda _e: None)
    tr = Transcript(tmp_path)
    stop = threading.Event()
    priority = threading.Event()
    bind_product_source(session, tr, SourceProducerCutoff(lambda _t: True),
                        source_only_notice=False, local_product_authority=True)
    now = [100.0]
    slot_calls = []

    @contextlib.contextmanager
    def spy_slot(name: str, *, timeout: float = 0.0, quiet: bool = False):
        slot_calls.append((name, timeout, quiet))
        yield True

    seen_chunks = []

    def ok_feed(speech, _ctx, **_kw):
        seen_chunks.append(speech)
        return (c for c in ('{"items":[{"topic":"Пилот","text":"Принято."}]}',))

    llm = _fake_llm(feed_fn=ok_feed)
    runner = LocalFeedRunner(
        session._actor, tr, llm, LocalLoopbackAuthority.verify(llm),
        slot=spy_slot, stop=stop, priority=priority,
        enabled=lambda: True, interval_s=15.0, retry_s=2.0,
        clock=lambda: now[0],
    )

    # now == _next_due runs (returns idle, not waiting)
    runner._next_due = 100.0
    assert runner.run_next("r-idle") == {"status": "idle"}
    assert runner._next_due == 115.0

    # _step_done cleared -> busy
    runner._next_due = 100.0
    runner._step_done.clear()
    assert runner.run_next("r-busy") == {"status": "busy"}
    runner._step_done.set()

    # state.paused alone and state.stopped alone -> paused (even if begin_local_speech_window succeeds)
    orig_begin = session._actor.begin_local_speech_window
    orig_semantic = session._actor.semantic
    monkeypatch.setattr(session._actor, "begin_local_speech_window", lambda: 1)
    monkeypatch.setattr(
        session._actor, "semantic",
        SimpleNamespace(state=dataclasses.replace(orig_semantic.state, paused=True, stopped=False)),
    )
    assert runner.run_next("r-paused", force=True) == {"status": "paused"}
    monkeypatch.setattr(
        session._actor, "semantic",
        SimpleNamespace(state=dataclasses.replace(orig_semantic.state, paused=False, stopped=True)),
    )
    assert runner.run_next("r-stopped", force=True) == {"status": "paused"}
    monkeypatch.setattr(session._actor, "semantic", orig_semantic)

    # begin_local_speech_window raising ActorRefused -> paused
    monkeypatch.setattr(session._actor, "begin_local_speech_window",
                        lambda: (_ for _ in ()).throw(ActorRefused("no")))
    assert runner.run_next("r-begin-refused", force=True) == {"status": "paused"}
    monkeypatch.setattr(session._actor, "begin_local_speech_window", orig_begin)

    # Fresh window with > 1500 chars uses 9000 limit even if _rejected_cursor==(0,0) with count==0
    tr.add("слово " * 350, speaker="Ведущий")
    tr.add("Вторая реплика следом.", speaker="Ольга", head=False)
    session.ingest_source_once(limit=16)
    runner._rejected_cursor = (0, 0)
    runner._rejected_count = 0
    out_commit = runner.run_next("reqA", force=True)
    assert out_commit["status"] == "committed"
    assert session._actor.semantic.export()["local_speech"]["window_ids"] == ["reqA_w1"]
    assert len(seen_chunks[-1]) > 1800
    assert slot_calls[-1] == ("живая нить", 0.2, True)
    # All 2 portions fit in 9000 -> caught up -> _next_due == now + interval_s
    assert runner._next_due == 115.0
    assert runner._active_cancel is None

    # More pending after partial window -> _next_due == now + 0.5
    tr.add("длинное " * 1300, speaker="Максим", head=False)
    session.ingest_source_once(limit=16)
    out_partial = runner.run_next("reqB", force=True)
    assert out_partial["status"] == "committed"
    assert runner._next_due == 100.5

    # Signal or _retired set during slot acquisition -> yielded + retry_s
    @contextlib.contextmanager
    def retire_in_slot(_name: str, **_kw):
        runner._retired = True
        yield True

    runner._slot = retire_in_slot
    assert runner.run_next("req-retire-slot", force=True) == {"status": "yielded"}
    assert runner._next_due == 102.0
    runner._retired = False

    @contextlib.contextmanager
    def priority_in_slot(_name: str, **_kw):
        priority.set()
        yield True

    runner._slot = priority_in_slot
    assert runner.run_next("req-prio-slot", force=True) == {"status": "yielded"}
    assert runner._next_due == 102.0
    priority.clear()
    runner._slot = spy_slot

    # StreamCancelled -> retry_s; _retired set during feed (without signal) -> cancelled + retry_s
    runner.llm = _fake_llm(feed_fn=lambda *_a, **_kw: (_ for _ in ()).throw(cancellable_http.StreamCancelled()))
    assert runner.run_next("req-sc", force=True) == {"status": "cancelled"}
    assert runner._next_due == 102.0

    replacement_cancel = threading.Event()

    def retire_during_feed(*_a, **_kw):
        runner._retired = True
        runner._active_cancel = replacement_cancel
        return (c for c in ('{"none":true}',))

    runner.llm = _fake_llm(feed_fn=retire_during_feed)
    assert runner.run_next("req-retire-feed", force=True) == {"status": "cancelled"}
    assert runner._next_due == 102.0
    assert runner._active_cancel is replacement_cancel
    runner._active_cancel = None
    runner._retired = False

    # commit_local_speech_window raising ActorStopped or superseded ActorRefused -> cancelled + retry_s
    runner.llm = _fake_llm(feed_fn=ok_feed)
    orig_commit = session._actor.commit_local_speech_window
    for commit_exc in (ActorStopped("stopped"), ActorRefused("window superseded")):
        monkeypatch.setattr(session._actor, "commit_local_speech_window",
                            lambda *_a, _e=commit_exc, **_kw: (_ for _ in ()).throw(_e))
        assert runner.run_next("req-c-exc", force=True) == {"status": "cancelled"}
        assert runner._next_due == 102.0
    monkeypatch.setattr(session._actor, "commit_local_speech_window", orig_commit)

    # _reject_or_skip fallback when bounded_pair == expected_cursor, skip_id sequence, and ActorStopped on skip
    cur_end = (len(session._actor.semantic.state.portions), 0)
    runner._rejected_cursor = cur_end
    runner._rejected_count = 2
    monkeypatch.setattr(session._actor, "skip_local_speech_window",
                        lambda *_a, **_kw: (_ for _ in ()).throw(ActorStopped("halted")))
    assert runner._reject_or_skip("rskip", cur_end, cur_end, 1, "ValueError") == {"status": "cancelled"}
    assert runner._next_due == 102.0

    skip_seen = {}

    def fake_skip(_auth, *, window_id, expected_cursor, next_cursor, notice, window_epoch):  # noqa: ARG001
        skip_seen["id"] = window_id
        skip_seen["next"] = next_cursor
        return {"skipped": True}

    monkeypatch.setattr(session._actor, "skip_local_speech_window", fake_skip)
    runner._rejected_cursor = cur_end
    runner._rejected_count = 2
    res_skip = runner._reject_or_skip("rskip2", cur_end, (99, 0), 1, "ValueError")
    assert res_skip["status"] == "skipped"
    assert skip_seen["next"] == (99, 0)
    assert skip_seen["id"].startswith("rskip2_skip")
    assert runner._next_due == 115.0  # skip_cursor[0] >= len(portions) -> interval_s

    assert runner.cancel_and_join() is True
    assert runner.shutdown_confirmed() is True

    # LLM feed/grounded_answer/_LeasedStream/_open_stream remaining critical gates
    c_llm = LLM({
        "llm": {"model": "qwen-ollama-main", "mlx_model": "qwen-mlx-unused"},
        "sufler": {"language": "ru", "role": "Ассистент."},
    })
    monkeypatch.setattr(c_llm, "cloud_ready", True)
    with pytest.raises(ValueError, match="local model server"):
        list(c_llm.feed("Речь", "{}", cancel_event=threading.Event()))
    monkeypatch.setattr(c_llm, "cloud_ready", False)
    monkeypatch.setattr(c_llm, "base", "https://evil.example.com")
    with pytest.raises(ValueError, match="local model server"):
        list(c_llm.feed("Речь", "{}", cancel_event=threading.Event()))
    monkeypatch.setattr(c_llm, "base", "http://127.0.0.1:11434")

    captured_llm = []

    @contextlib.contextmanager
    def capture_open(url, payload, busy_wait, **kw):
        captured_llm.append((url, payload, busy_wait, kw))
        yield SimpleNamespace(
            status_code=200,
            iter_lines=lambda: iter([b'{"message":{"content":"{\\"none\\":true}"},"done":true,"done_reason":"stop"}']),
        )

    monkeypatch.setattr(c_llm, "_open_stream", capture_open)
    assert "".join(c_llm.feed("Речь", "{}", cancel_event=threading.Event())) == '{"none":true}'
    assert captured_llm[-1][1]["model"] == "qwen-ollama-main"
    assert captured_llm[-1][2] == 2.0

    # grounded_answer with empty _cfg={} and MLX matching model + think=False
    c_llm._cfg = {}
    monkeypatch.setattr(c_llm, "require_local_ollama_model", lambda t: t + ":local")
    assert "".join(c_llm.grounded_answer("В?", system="s", cancel_event=threading.Event())) == '{"none":true}'

    mlx_c = LLM({
        "llm": {"engine": "mlx-server", "model": "qwen-ollama",
                "mlx_base_url": "http://127.0.0.1:8080", "mlx_model": "qwen-mlx"},
        "sufler": {"language": "ru", "role": "Ассистент."},
    })

    @contextlib.contextmanager
    def capture_mlx(url, payload, busy_wait, **kw):
        captured_llm.append((url, payload, busy_wait, kw))
        yield SimpleNamespace(
            status_code=200,
            iter_lines=lambda: iter([
                b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
                b"data: [DONE]",
            ]),
        )

    monkeypatch.setattr(mlx_c, "_open_stream", capture_mlx)
    assert "".join(mlx_c.grounded_answer(
        "В?", system="s", model="qwen-mlx", cancel_event=threading.Event()
    )) == "ok"
    assert captured_llm[-1][1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured_llm[-1][2] == 2.0

    # _LeasedStream quarantine prevents premature lease release; body exception records error outcome
    lease_exits = []
    quarantined_calls = []

    class BadExitResp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            raise cancellable_http.CleanupIncomplete(self)

        def quarantine(self, lease, call):
            quarantined_calls.append((lease, call))

    lease_stub = SimpleNamespace(__exit__=lambda *e: lease_exits.append(e))
    call_stub = SimpleNamespace(done_s=None, finish=lambda o: lease_exits.append(("finish", o)))
    with pytest.raises(cancellable_http.CleanupIncomplete):
        with LLM._LeasedStream(BadExitResp(), lease_stub, call_stub):
            pass
    assert len(quarantined_calls) == 1
    assert lease_exits == []

    body_outcomes = []
    ok_resp = SimpleNamespace(close=lambda: body_outcomes.append("closed_r"))
    call_err = SimpleNamespace(done_s=None, finish=lambda o: body_outcomes.append(("finish", o)))
    with pytest.raises(RuntimeError, match="boom"):
        with LLM._LeasedStream(ok_resp, SimpleNamespace(__exit__=lambda *_e: None), call_err):
            raise RuntimeError("boom")
    assert "closed_r" in body_outcomes
    assert body_outcomes[-1][0] == "finish" and body_outcomes[-1][1] != "closed"

    # _open_stream default timeout==300 and stream=True when cancel_event is None
    post_seen = {}

    def fake_post(url, **kw):
        post_seen.update(url=url, **kw)
        return SimpleNamespace(status_code=200, close=lambda: None)

    _сеть_закрыта[("POST", "http://127.0.0.1:11434/api/chat")] = fake_post
    fake_lease = SimpleNamespace(__exit__=lambda *_e: None)
    monkeypatch.setattr(c_llm, "_lease", lambda *_a: SimpleNamespace(__enter__=lambda: fake_lease))
    leased = LLM._open_stream(c_llm, "http://127.0.0.1:11434/api/chat", {"model": "m"}, 0.0)
    assert post_seen["stream"] is True
    assert post_seen["timeout"] == 300
    leased.close()




