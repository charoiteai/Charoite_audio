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


def test_theses_and_minutes_drafts_take_the_hint_slot_quietly():
    for loop in ("think_loop", "minutes_loop"):
        body = _body(loop)
        assert "hint_slot(" in body and "quiet=True" in body, loop
        assert "if not got:" in body, loop


def test_theses_prompt_is_capped():
    body = _body("think_loop")
    assert "THINK_MAX_CHARS" in body and re.search(r"^THINK_MAX_CHARS = \d", SRC, re.M)


def test_empty_model_output_is_a_failed_hint():
    assert "модель вернула пустой ответ" in _body("gen_hint")


def test_cloud_refusal_is_not_written_to_the_audit():
    assert "if failure or not out or question_filter.is_refusal(out):" in _body("cloud_loop")


def test_deja_vu_spends_the_window_only_after_a_successful_embedding():
    body = _body("deja_vu_loop")
    assert body.index("qv = embed(") < body.index("seen_len = len(full)")


def test_name_loop_skips_a_tick_without_new_speech():
    assert "if grown == last_len:" in _body("name_loop")


def test_stream_read_timeout_is_two_minutes_not_five():
    llm = (pathlib.Path(__file__).resolve().parent.parent / "src" / "llm.py").read_text(encoding="utf-8")
    assert "STREAM_TIMEOUT = (10.0, 120.0)" in llm
    assert llm.count("timeout=STREAM_TIMEOUT") >= 1
