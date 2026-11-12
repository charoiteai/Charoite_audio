"""Local loopback product runner for the grounded sequential meeting feed.

Reads only speech already ingested through AcceptedSource -> ActorCore, invokes
the strictly local single-pass feed contract (think=False, loopback only, zero
cloud egress), and commits validated typed speech blocks atomically through
ActorCore.commit_local_speech_window.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field

import cancellable_http
import privacy
from feed_contract import (
    SpeechChunk,
    SpeechCursor,
    feed_system,
    parse_feed,
    slice_speech_entries,
)
from .actor_core import ActorRefused, ActorStopped


_CANCEL_POLL_S = 0.025
_DEFAULT_CONTEXT_CHARS = 3500
_MAX_OPEN_QUESTIONS = 12
_MAX_TOPIC_FACTS = 4
_MAX_KNOWN_TOPICS = 8
_DEFAULT_SPEECH_CHARS = 9000
_DEFAULT_INTERVAL_S = 15.0
_DEFAULT_RETRY_S = 2.0
_DEFAULT_JOIN_TIMEOUT_S = 2.0
_SLOT_ACQUIRE_TIMEOUT_S = 0.2
_CATCHUP_DELAY_S = 0.5


class _DisabledCancelEvent:
    """Signal cancellation as soon as an enabled() toggle predicate turns false."""

    def __init__(self, enabled_fn) -> None:
        self._enabled = enabled_fn

    def is_set(self) -> bool:
        return not bool(self._enabled())


class _CombinedCancelEvent:
    """OR-view over multiple stop/priority/manual/pause cancellation events."""

    def __init__(self, *events) -> None:
        self._events = tuple(ev for ev in events if ev is not None)

    def is_set(self) -> bool:
        return any(ev.is_set() for ev in self._events)

    def wait(self, timeout: float | None = None) -> bool:
        if self.is_set():
            return True
        primary = self._events[0] if self._events else None
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while not self.is_set():
            step = _CANCEL_POLL_S
            if deadline is not None:
                step = min(step, max(0.0, deadline - time.monotonic()))
                if not step:
                    return False
            if hasattr(primary, "wait"):
                primary.wait(step)
            else:
                time.sleep(step)
        return True


def combine_cancel(*events) -> _CombinedCancelEvent:
    return _CombinedCancelEvent(*events)


_AUTHORITY_PROOF = object()
_PENDING_SERVER_PROOF = object()


@dataclass(frozen=True)
class LocalLoopbackAuthority:
    """Explicit unforgeable runtime proof that endpoint and model are strictly local."""

    engine: str
    base: str
    model: str
    _proof: object = field(default=None, repr=False)

    @property
    def verified(self) -> bool:
        return self._proof is _AUTHORITY_PROOF

    @property
    def pending_server(self) -> bool:
        return self._proof is _PENDING_SERVER_PROOF

    @property
    def admitted(self) -> bool:
        return self._proof in (_AUTHORITY_PROOF, _PENDING_SERVER_PROOF)

    @property
    def display_model(self) -> str:
        return self.model[:-6] if self.model.lower().endswith(":local") else self.model

    @classmethod
    def _static_check(cls, llm) -> tuple[str, str, str]:
        if llm is None:
            raise ValueError("local loopback authority requires an LLM client")
        engine = getattr(llm, "engine", None)
        base = getattr(llm, "base", "")
        if (getattr(llm, "cloud_ready", False)
                or engine not in ("ollama", "mlx-server")
                or not isinstance(base, str)
                or not privacy.is_loopback_url(base)):
            raise ValueError("live grounded feed requires a strictly local loopback model server")
        cfg = getattr(llm, "_cfg", None) or {}
        feed_model = (cfg.get("sufler") or {}).get("feed_model")
        if engine == "mlx-server":
            mlx_model = getattr(llm, "mlx_model", None) or ""
            if feed_model and feed_model != mlx_model:
                raise ValueError("live feed model does not match the local MLX server")
            selected = mlx_model
            if not isinstance(selected, str) or not selected.strip():
                raise ValueError("local grounded feed model is empty")
            return engine, base, selected.strip()
        raw_model = feed_model or getattr(llm, "model", None) or ""
        if not isinstance(raw_model, str) or not raw_model.strip():
            raise ValueError("local grounded feed model is empty")
        req_fn = getattr(llm, "require_local_ollama_model", None)
        if not callable(req_fn):
            raise privacy.PrivacyRefused("unsupported adapter: missing local Ollama contract")
        from llm import LLM

        base_tag = LLM.validate_local_model_tag(raw_model.strip())
        return engine, base, base_tag

    @classmethod
    def verify(cls, llm) -> LocalLoopbackAuthority:
        engine, base, base_tag = cls._static_check(llm)
        if engine == "mlx-server":
            return cls(engine=engine, base=base, model=base_tag, _proof=_AUTHORITY_PROOF)
        selected = llm.require_local_ollama_model(base_tag)
        if (not isinstance(selected, str)
                or not selected.endswith(":local")
                or not selected[:-6]
                or selected[:-6].casefold().endswith((":local", ":cloud", "-cloud"))):
            raise privacy.PrivacyRefused("adapter did not return a verified local model reference")
        return cls(engine=engine, base=base, model=selected, _proof=_AUTHORITY_PROOF)

    @classmethod
    def verify_or_defer(cls, llm) -> tuple[LocalLoopbackAuthority, Exception | None]:
        engine, base, base_tag = cls._static_check(llm)
        try:
            return cls.verify(llm), None
        except privacy.PrivacyRefused:
            raise
        except (RuntimeError, OSError) as exc:
            return cls(engine=engine, base=base, model=base_tag + ":local",
                       _proof=_PENDING_SERVER_PROOF), exc

    def ensure_verified(self, llm) -> LocalLoopbackAuthority:
        if self.verified:
            return self
        if not self.pending_server:
            raise ValueError("unadmitted local authority")
        return self.verify(llm)


_LOCAL_RULES_EN = """
Additional strict rules:
- Never output topic_id or id, and never output null: use only string keys topic, text, question, answers_q, answer, omitting unused optional fields.
- If a question is already in open_questions: omit it while unanswered; when answered, emit only its exact ID in answers_q plus answer (without question; if another question also appears in the same speech, put that other question in a second item in items).
- If a new question (not in open_questions) and its answer appear in the same speech window, include both question and answer in that item. Always record an explicit refusal to take a task and its reason (in answer or text), never returning {"none":true}.
- Keep every concrete number, metric threshold, and agreed day/time (such as 22:00) in text or answer; put only genuine questions into question, never turning statements, conditions, or schedules into a question.
- When continuing a topic in known_topics, reuse its exact topic title."""

_LOCAL_RULES_RU = """
Дополнительные строгие правила:
- Никогда не выводи поля topic_id или id и никогда не пиши null: допустимы только строковые поля topic, text, question, answers_q, answer; пустые необязательные поля опускай.
- Если вопрос уже есть в open_questions: пока ответа нет — не повторяй его в question; когда ответ на него прозвучал — передай только его точный ID в answers_q и ответ в answer (без поля question; если в тех же репликах появился ещё и другой вопрос, оформи его вторым элементом items).
- Если новый вопрос (которого нет в open_questions) и ответ на него прозвучали в одном окне новых реплик — передай в этом элементе и question, и answer. Явный отказ взять задачу и его причину обязательно сохрани (в answer или в text), не возвращая {"none":true}.
- Сохраняй в text или answer все конкретные числа, пороги метрик и согласованные дни/часы (например, 22:00); в question клади только реальные вопросы, не превращая утверждения, условия или расписание в вопрос.
- Если реплики продолжают тему из known_topics, используй ТОЧНО тот же заголовок topic из known_topics."""

_LOCAL_RULES_ZH = """
额外严格规则：
- 严禁输出 topic_id 或 id，也严禁输出 null：只允许字符串字段 topic、text、question、answers_q、answer，未使用的可选字段直接省略。
- 若问题已在 open_questions 中：未回答前不要重复；回答该问题时只输出其确切 answers_q ID 和 answer（不要带 question；若同段发言另有其他问题，请放入 items 的第二个条目）。
- 若同一段新发言中同时出现不在 open_questions 中的新问题及其回答，在同一条目中同时输出 question 和 answer。明确拒绝接手任务及其原因必须记录在 answer 或 text 中，绝不能返回 {"none":true}。
- 在 text 或 answer 中保留所有具体数字、指标阈值与商定时间（如 22:00）；question 只放真正的问题，不要把陈述、条件或时间安排写成问题。
- 继续讨论 known_topics 中已有的主题时，直接复用该精确 topic 标题。"""


def local_feed_system(language: str) -> str:
    extra = {"ru": _LOCAL_RULES_RU, "zh": _LOCAL_RULES_ZH}.get(language, _LOCAL_RULES_EN)
    return feed_system(language) + extra


def local_feed_context(blocks, *, max_chars: int = _DEFAULT_CONTEXT_CHARS) -> str:
    """Bounded structured account for single-pass local feed without internal IDs."""
    if type(max_chars) is not int or max_chars < len('{"open_questions":[],"known_topics":[]}'):
        raise ValueError("context budget cannot fit the complete schema")
    ordered = sorted(blocks, key=lambda b: (b.position, b.system_after, b.serial))
    answered = {b.question_id for b in ordered if b.kind == "answer" and b.question_id}
    open_q: list[dict] = []
    topics_order: list[str] = []
    facts_by_topic: dict[str, list[str]] = {}
    for block in ordered:
        if block.kind == "system" or block.manual_id is not None:
            continue
        if (block.kind == "question" and block.question_id
                and block.question_id not in answered
                and not block.question_id.startswith("q:manual:")):
            open_q.append({"id": block.question_id,
                           "topic": block.topic_label,
                           "question": block.text})
        if block.kind == "paragraph" and block.topic_label:
            if block.topic_label not in facts_by_topic:
                topics_order.append(block.topic_label)
                facts_by_topic[block.topic_label] = []
            facts_by_topic[block.topic_label].append(block.text)
    total_facts = sum(len(facts) for facts in facts_by_topic.values())
    kept_q = open_q[-_MAX_OPEN_QUESTIONS:]
    kept_topics = [
        {"topic": t, "facts": facts_by_topic[t][-_MAX_TOPIC_FACTS:]}
        for t in topics_order[-_MAX_KNOWN_TOPICS:]
    ]
    while True:
        value = {"open_questions": kept_q, "known_topics": kept_topics}
        omitted_q = len(open_q) - len(kept_q)
        omitted_facts = total_facts - sum(len(t["facts"]) for t in kept_topics)
        if omitted_q:
            value["omitted_questions"] = omitted_q
        if omitted_facts:
            value["omitted_facts"] = omitted_facts
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(raw) <= max_chars:
            return raw
        if kept_topics:
            kept_topics[0]["facts"].pop(0)
            if not kept_topics[0]["facts"]:
                kept_topics.pop(0)
        elif kept_q:
            kept_q.pop(0)
        else:
            raise ValueError("context budget cannot fit omission counts")


def slice_ingested_speech(portions, speaker_labels: dict[str, str],
                          cursor: SpeechCursor, *, max_chars: int = _DEFAULT_SPEECH_CHARS,
                          display_name=None) -> SpeechChunk:
    """Slice bounded speech strictly from portions already ingested into SemanticState."""
    if not isinstance(cursor, SpeechCursor):
        raise ValueError("invalid speech cursor")
    entries = [
        (p.timestamp,
         display_name(raw) if callable(display_name) else raw,
         p.body_utf8)
        for p in portions
        for raw in (speaker_labels.get(p.speaker_id, p.speaker_id),)
    ]
    return slice_speech_entries(entries, cursor, max_chars=max_chars)


def build_grounded_answer_prompt(language: str, question: str, feed_text: str,
                                 speech_tail: str, memory_block: str = "") -> tuple[str, str]:
    """Concise strictly grounded prompt for manual Q/A inside the live thread."""
    if language == "ru":
        system = (
            "Ты локальный ассистент встречи Чароит. Отвечай ТОЛЬКО по приведённой живой нити, "
            "свежим репликам стенограммы и локальной памяти прошлых встреч (если передана). "
            "Пиши по-русски, кратко и предметно (1–4 предложения, точные цифры, сроки, статусы "
            "и имена из текста). Если данных в разговоре ещё нет — скажи об этом прямо одной фразой, "
            "ничего не додумывай."
        )
        parts = []
        if memory_block.strip():
            parts.append("Локальная память прошлых встреч:\n" + memory_block.strip())
        if feed_text.strip():
            parts.append("Живая нить встречи:\n" + feed_text.strip()[-3500:])
        if speech_tail.strip():
            parts.append("Свежие реплики стенограммы:\n" + speech_tail.strip()[-3000:])
        if not parts:
            parts.append("Стенограмма пока пуста.")
        parts.append(f"Вопрос: {question.strip()}")
        return system, "\n\n".join(parts)
    if language == "zh":
        system = (
            "你是本地会议助手。只能依据下方提供的实时纪要、最新逐字稿和本地历史记忆作答。"
            "用中文简洁回答（1–4句，保留精确数字、期限与结论）；若对话中尚无依据，请直接说明，切勿猜测。"
        )
        parts = []
        if memory_block.strip():
            parts.append("历史会议记忆：\n" + memory_block.strip())
        if feed_text.strip():
            parts.append("当前会议纪要：\n" + feed_text.strip()[-3500:])
        if speech_tail.strip():
            parts.append("最新发言：\n" + speech_tail.strip()[-3000:])
        if not parts:
            parts.append("逐字稿暂时为空。")
        parts.append(f"问题：{question.strip()}")
        return system, "\n\n".join(parts)
    system = (
        "You are a strictly local meeting assistant. Answer ONLY from the supplied live feed, "
        "recent transcript lines, and local past-meeting memory (if present). Reply concisely "
        "in 1–4 sentences with exact numbers, schedules, and statuses; if the meeting has not "
        "covered it yet, state that directly without guessing."
    )
    parts = []
    if memory_block.strip():
        parts.append("Local past-meeting memory:\n" + memory_block.strip())
    if feed_text.strip():
        parts.append("Current live feed:\n" + feed_text.strip()[-3500:])
    if speech_tail.strip():
        parts.append("Recent transcript:\n" + speech_tail.strip()[-3000:])
    if not parts:
        parts.append("Transcript is currently empty.")
    parts.append(f"Question: {question.strip()}")
    return system, "\n\n".join(parts)


def _norm_q_surface(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\r\n?.!,:;«»\"'—–-").lower()


def normalize_local_feed_raw(raw: str, blocks=()) -> str:
    """Strip unambiguous optional nulls or exact open_questions echoes before parse_feed."""
    stripped = raw.strip()
    body = (stripped[len("```json\n"):-len("\n```")]
            if stripped.startswith("```json\n") and stripped.endswith("\n```")
            else stripped)
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001 - leave malformed JSON to parse_feed
        return raw
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return raw
    open_q_norm = {
        b.question_id: _norm_q_surface(b.text)
        for b in blocks
        if getattr(b, "kind", None) == "question"
        and getattr(b, "question_id", None)
        and getattr(b, "manual_id", None) is None
    }
    optional_keys = {"text", "question", "answers_q", "answer"}
    cleaned_items = []
    changed = False
    for item in data["items"]:
        if not isinstance(item, dict):
            return raw
        new_item = {}
        for k, v in item.items():
            if k in optional_keys and v is None:
                changed = True
                continue
            new_item[k] = v
        aq = new_item.get("answers_q")
        q = new_item.get("question")
        if (isinstance(aq, str) and aq.strip() in open_q_norm
                and isinstance(q, str) and _norm_q_surface(q) == open_q_norm[aq.strip()]):
            new_item.pop("question", None)
            changed = True
        cleaned_items.append(new_item)
    if not changed:
        return raw
    return json.dumps({**data, "items": cleaned_items}, ensure_ascii=False)


class LocalFeedRunner:
    """Single-pass local feed worker obeying ActorCore worker cutoff & manual priority."""

    MAX_SPEECH_CHARS = 9000
    MAX_POISON_SKIP_CHARS = 1200
    MAX_WINDOW_REJECTIONS = 3

    def __init__(self, actor, transcript, llm, authority: LocalLoopbackAuthority, *,
                 slot, stop: threading.Event, priority: threading.Event, enabled,
                 interval_s: float = _DEFAULT_INTERVAL_S,
                 retry_s: float = _DEFAULT_RETRY_S,
                 clock=time.monotonic) -> None:
        if not isinstance(authority, LocalLoopbackAuthority) or not authority.admitted:
            raise ValueError("admitted LocalLoopbackAuthority required")
        self.actor = actor
        self.transcript = transcript
        self.llm = llm
        self.authority = authority
        self._slot = slot
        self._stop = stop
        self._priority = priority
        self._enabled = enabled
        self.interval_s = float(interval_s)
        self.retry_s = float(retry_s)
        self._clock = clock
        self._next_due = 0.0
        self._window_seq = 0
        self._rejected_cursor: tuple[int, int] | None = None
        self._rejected_count = 0
        self._state_lock = threading.Lock()
        self._step_done = threading.Event()
        self._step_done.set()
        self._active_cancel: threading.Event | None = None
        self._retired = False

    def cancel_active(self) -> None:
        with self._state_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()

    def cancel_and_join(self, timeout_s: float = _DEFAULT_JOIN_TIMEOUT_S) -> bool:
        with self._state_lock:
            self._retired = True
            if self._active_cancel is not None:
                self._active_cancel.set()
        return self._step_done.wait(max(0.0, float(timeout_s)))

    def shutdown_confirmed(self) -> bool:
        return self._retired and self._step_done.is_set()

    def _reject_or_skip(self, request_id: str, expected_cursor: tuple[int, int],
                        next_cursor: tuple[int, int], window_epoch: int,
                        reason_name: str) -> dict:
        if self._rejected_cursor == expected_cursor:
            self._rejected_count += 1
        else:
            self._rejected_cursor = expected_cursor
            self._rejected_count = 1
        if self._rejected_count < self.MAX_WINDOW_REJECTIONS:
            self._next_due = self._clock() + self.interval_s
            return {"status": "rejected", "reason": reason_name, "attempt": self._rejected_count}
        self._rejected_cursor = None
        self._rejected_count = 0
        state = self.actor.semantic.state
        bounded = slice_ingested_speech(
            state.portions, dict(state.speaker_labels), SpeechCursor(*expected_cursor),
            max_chars=self.MAX_POISON_SKIP_CHARS,
            display_name=getattr(self.transcript, "display_name", None),
        )
        bounded_pair = (bounded.next_cursor.portion, bounded.next_cursor.offset)
        skip_cursor = (
            min(next_cursor, bounded_pair)
            if bounded.body_chars
            else next_cursor
        )
        self._window_seq += 1
        skip_id = f"{request_id}_skip{self._window_seq}"
        notice = (
            "Пропущен фрагмент живой нити: ответ локальной модели не прошёл проверку формата."
            if getattr(self.llm, "lang", "ru") == "ru"
            else "Skipped one live-feed window: local model output failed format validation."
        )
        try:
            receipt = self.actor.skip_local_speech_window(
                self.authority,
                window_id=skip_id,
                expected_cursor=expected_cursor,
                next_cursor=skip_cursor,
                notice=notice,
                window_epoch=window_epoch,
            )
        except (ActorStopped, ActorRefused):
            self._next_due = self._clock() + self.retry_s
            return {"status": "cancelled"}
        more_pending = skip_cursor[0] < len(self.actor.semantic.state.portions)
        self._next_due = self._clock() + (_CATCHUP_DELAY_S if more_pending else self.interval_s)
        return {"status": "skipped", "reason": reason_name, **receipt}

    def run_next(self, request_id: str, *, force: bool = False) -> dict:
        if self._stop.is_set() or self._retired or not self._enabled():
            return {"status": "paused"}
        if self._priority.is_set():
            return {"status": "yielded"}
        now = self._clock()
        if not force and now < self._next_due:
            return {"status": "waiting"}
        with self._state_lock:
            if self._retired or not self._step_done.is_set():
                return {"status": "busy"}
            self._step_done.clear()
            local_cancel = threading.Event()
            self._active_cancel = local_cancel
        try:
            try:
                window_epoch = self.actor.begin_local_speech_window()
            except (ActorRefused, ActorStopped):
                return {"status": "paused"}
            state = self.actor.semantic.state
            if state.stopped or state.paused:
                return {"status": "paused"}
            expected_cursor = state.local_speech_cursor
            cursor = SpeechCursor(*expected_cursor)
            display = getattr(self.transcript, "display_name", None)
            window_limit = (
                self.MAX_POISON_SKIP_CHARS
                if self._rejected_cursor == expected_cursor and self._rejected_count > 0
                else self.MAX_SPEECH_CHARS
            )
            chunk = slice_ingested_speech(
                state.portions, dict(state.speaker_labels), cursor,
                max_chars=window_limit, display_name=display,
            )
            if not chunk.body_chars:
                self._next_due = self._clock() + self.interval_s
                return {"status": "idle"}
            blocks = self.actor.snapshot()[1]
            context = local_feed_context(blocks)
            sys_prompt = local_feed_system(getattr(self.llm, "lang", "ru"))
            disabled_ev = _DisabledCancelEvent(self._enabled)
            signal = combine_cancel(self._stop, self._priority, local_cancel, disabled_ev)
            with self._slot("живая нить", timeout=_SLOT_ACQUIRE_TIMEOUT_S, quiet=True) as got:
                if not got:
                    self._next_due = self._clock() + self.retry_s
                    return {"status": "busy"}
                if signal.is_set() or self._retired:
                    self._next_due = self._clock() + self.retry_s
                    return {"status": "yielded"}
                if not self.authority.verified:
                    try:
                        self.authority = self.authority.ensure_verified(self.llm)
                    except (RuntimeError, OSError) as exc:
                        self._next_due = self._clock() + self.interval_s
                        return {"status": "unavailable", "reason": type(exc).__name__}
                try:
                    with contextlib.closing(
                        self.llm.feed(
                            chunk.text, context, cancel_event=signal,
                            system=sys_prompt, model=self.authority.model,
                        )
                    ) as stream:
                        raw = "".join(stream)
                except cancellable_http.StreamCancelled:
                    self._next_due = self._clock() + self.retry_s
                    return {"status": "cancelled"}
                except cancellable_http.CleanupIncomplete:
                    raise
                except Exception as exc:  # noqa: BLE001 - retry same speech window cleanly
                    print(f"grounded local feed rejected: {type(exc).__name__}: {exc}",
                          file=sys.stderr)
                    self._next_due = self._clock() + self.interval_s
                    return {"status": "failed", "reason": type(exc).__name__}
            if signal.is_set() or self._retired:
                self._next_due = self._clock() + self.retry_s
                return {"status": "cancelled"}
            next_pair = (chunk.next_cursor.portion, chunk.next_cursor.offset)
            try:
                items = parse_feed(normalize_local_feed_raw(raw, blocks))
                self._window_seq += 1
                window_id = f"{request_id}_w{self._window_seq}"
                receipt = self.actor.commit_local_speech_window(
                    self.authority,
                    window_id=window_id,
                    items=items,
                    expected_cursor=expected_cursor,
                    next_cursor=next_pair,
                    window_epoch=window_epoch,
                )
            except (ActorStopped, ActorRefused) as exc:
                msg = str(exc)
                if isinstance(exc, ActorStopped) or any(
                        token in msg for token in ("paused", "superseded", "after stop")
                ):
                    self._next_due = self._clock() + self.retry_s
                    return {"status": "cancelled"}
                print(f"grounded local feed parse/commit rejected: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                return self._reject_or_skip(request_id, expected_cursor, next_pair,
                                            window_epoch, type(exc).__name__)
            except Exception as exc:  # noqa: BLE001 - unaccepted output never advances cursor
                print(f"grounded local feed parse/commit rejected: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                return self._reject_or_skip(request_id, expected_cursor, next_pair,
                                            window_epoch, type(exc).__name__)
            self._rejected_cursor = None
            self._rejected_count = 0
            more_pending = next_pair[0] < len(self.actor.semantic.state.portions)
            self._next_due = self._clock() + (_CATCHUP_DELAY_S if more_pending else self.interval_s)
            return {"status": "committed", **receipt}
        finally:
            with self._state_lock:
                if self._active_cancel is local_cancel:
                    self._active_cancel = None
                self._step_done.set()
