"""Живой контур: что обещано в daemon.py по коду (аудит 13.09, зона 2).

Циклы демона — замыкания внутри main(), напрямую их не вызвать; проверяем
исходник тем же приёмом, что test_stt_runtime: обещание должно быть в теле
той функции, а не где-то в файле.
"""
from __future__ import annotations

import pathlib
import re

SRC = (pathlib.Path(__file__).resolve().parent.parent / "src" / "daemon.py").read_text(encoding="utf-8")


def _body(name: str) -> str:
    m = re.search(rf"^    def {name}\(", SRC, re.M)
    assert m, name
    nxt = re.search(r"^    def \w+\(", SRC[m.end():], re.M)
    return SRC[m.start():m.end() + (nxt.start() if nxt else len(SRC))]


def test_background_loops_take_the_hint_slot_quietly_and_yield_to_a_manual_request():
    for loop in ("think_loop", "minutes_loop", "name_loop"):
        body = _body(loop)
        assert "hint_slot(" in body and "quiet=True" in body, loop
        assert "if not got:" in body, loop
    # держатель замка обязан уступать ручному запросу — иначе «занято» через 45 с
    # (DS/GLM r1 I1 по #558); тезисы проверяют manual_evt сами, остальные — через _collect
    assert "manual_evt.is_set()" in _body("think_loop")
    for loop in ("minutes_loop", "name_loop"):
        assert "_collect(" in _body(loop) and "if out is None:" in _body(loop), loop
    assert "manual_evt.is_set()" in _body("_collect")
    # метка журнала — кадр того, кто позвал помощника, а не сам _collect (№807)
    assert "model_ledger.consumer(model_ledger.caller_name(1))" in _body("_collect")


def test_theses_backlog_is_consumed_head_first_not_dropped():
    body = _body("think_loop")
    assert "fresh = fresh[:THINK_MAX_CHARS]" in body and "consumed = seen + THINK_MAX_CHARS" in body
    assert "seen = consumed" in body


def test_theses_prompt_is_capped():
    body = _body("think_loop")
    assert "THINK_MAX_CHARS" in body and re.search(r"^THINK_MAX_CHARS = \d", SRC, re.M)


def test_empty_model_output_is_a_failed_hint():
    assert "модель вернула пустой ответ" in _body("gen_hint")


def test_name_loop_skips_a_tick_without_new_speech():
    assert "if grown == last_len:" in _body("name_loop")



def test_name_loop_logs_guard_refusals_once_per_meeting():
    """Отказ гварда в живом цикле имён — строка в журнал из обеих веток (№502): у
    мультиспикера через `speaker_names.settle` (там же решение по метке, его держат
    тесты имён), у одиночной ветки — прямо. Реестр «сказано» чистится до цикла: он
    живёт на процесс демона, и отказ новой встречи иначе молчал бы после такой же
    на прошлой."""
    body = _body("name_loop")
    assert body.count("speaker_names.settle(") == 1
    assert body.count("speaker_names.say_refusal(") == 1
    assert "speaker_names.forget_refusals()" in body.split("while not stop.is_set():")[0]

def test_stream_read_timeout_is_two_minutes_for_live_and_five_for_documents():
    llm = (pathlib.Path(__file__).resolve().parent.parent / "src" / "llm.py").read_text(encoding="utf-8")
    assert "STREAM_TIMEOUT = (10.0, 120.0)" in llm and "DOC_STREAM_TIMEOUT = (10.0, 300.0)" in llm
    # оба Ollama-стрима и mlx-путь берут потолок параметром (GLM r1 M2/M4 по #558)
    chat = 'with self._open_stream(f"{self.base}/api/chat", payload, busy_wait,\n                               timeout=timeout, method=_method) as r:'
    assert llm.count(chat) == 2, "оба Ollama-стрима (stream, stream_messages) берут потолок параметром"
    mlx = llm[llm.index("    def _stream_mlx("):llm.index("    def _sse(")]
    assert "timeout=timeout" in mlx
    doc = llm[llm.index("    def _doc_stream("):llm.index("    def _fit(")]
    assert 'kwargs.setdefault("timeout", DOC_STREAM_TIMEOUT)' in doc
    for name, nxt in (("summary", "_doc_stream"), ("minutes", None)):
        i = llm.index(f"    def {name}(")
        region = llm[i:llm.index(f"    def {nxt}(", i)] if nxt else llm[i:]
        assert "return self._doc_stream(" in region and "return self.stream(" not in region, name


def test_cloud_refusal_lands_in_the_audit_with_its_own_label():
    body = _body("cloud_loop")
    assert 'f"☁️ отказ {model}"' in body and "refusal = bool(out) and question_filter.is_refusal(out)" in body
    assert body.count("question_filter.is_refusal(") == 1


def test_auto_node_lookup_reads_speech_not_the_labelled_tail():
    """Авто-⏮ сверяет с узлами только речь (№776): в `tail` метка времени и подпись
    «Говорящий-N» давали цифру, по которой всплывали узлы с кодом, а подпись
    участника — его узел без упоминания."""
    body = _body("live_context_loop")
    call = body[body.index("node_index.lookup("):]
    call = call[:call.index(")") + 1]
    assert "tr.speech_tail(" in call and "tr.tail(" not in call, call


def test_minutes_draft_waits_a_long_tick_only_after_a_written_draft():
    # №808: черновик раз в 600 с, а не 150 — минуты основной модели и замок подсказок
    assert re.search(r"^MINUTES_EVERY = 600(\.0)?\b", SRC, re.M)
    assert re.search(r"^MINUTES_RETRY = 150(\.0)?\b", SRC, re.M)
    body = _body("minutes_loop")
    # сон — через stop: после стопа цикл не зовёт модель ради выброшенного черновика
    assert re.search(r"if stop\.wait\(MINUTES_EVERY if wrote else MINUTES_RETRY\):\s*return", body)
    assert "time.sleep(" not in body
    # длинный такт заслуживает только запись: флаг гасится на каждом пробуждении
    # и поднимается в одном месте — сразу за записью черновика (Sonnet I1 и Haiku I1 out1)
    loop = body.split("while not stop.is_set():", 1)
    assert re.search(r"\bwrote = False\b", loop[0])
    assert re.search(r"return\s+wrote = False\b", loop[1])
    raised = [m.start() for m in re.finditer(r"\bwrote = True\b", body)]
    assert len(raised) == 1
    assert re.search(r"note_minutes_written\(draft\)\s+wrote = True\b", body)
