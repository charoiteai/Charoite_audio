"""Свежесть последнего вопроса и метка микрофона в живом контуре (хвосты 20.08, круг по #455)."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import daemon  # noqa: E402


def test_stale_question_is_not_answered_as_a_question():
    """Через 25 с разговор ушёл: облако и ⚡ отвечают по хвосту, панель не
    рисует «над: ❓ …», промпт не требует «ответить на вопрос»."""
    store = {"text": "когда релиз?", "at": 100.0}
    assert daemon.fresh_question(store, 110.0) == "когда релиз?"
    assert daemon.fresh_question(store, 100.0 + daemon.PENDING_Q_TTL) == ""
    assert daemon.fresh_question({"text": "", "at": 0.0}, 5.0) == ""
    assert daemon.fresh_question({"text": "x", "at": 0.0}, 5.0) == "x", "свежий вопрос сразу после старта"


def test_cloud_prompt_asks_for_an_answer_only_when_there_is_a_question():
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    block = src[src.index("Собеседник задал вопрос"):src.index("ЧЕСТНОСТЬ ВАЖНЕЕ УВЕРЕННОСТИ")]
    assert "if q else" in block, "промпт облака не зависит от наличия вопроса"
    assert "(последняя реплика)" not in src, "старая безусловная формулировка вернулась"


def test_mic_channel_is_recognised_by_the_raw_label_everywhere():
    """`mic_label` обнуляется при коллизии с нейтральной меткой, и сравнение с
    ним считало микрофон чужим каналом — в четырёх местах, одно из которых
    первый круг пропустил (GLM по #455). Признак канала — сырая метка хаба."""
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    bad = re.findall(r"is_mic=\w+ == mic_label", src)
    assert not bad, bad
    assert src.count("is_mic=chan.is_mic(") >= 5, "признак канала — один объект ChannelLabels (D-П2)"


def test_dialog_markup_takes_the_hint_lock_quietly_and_obeys_the_toggle():
    """Разметка была единственным LLM-контуром вне арбитра: 900 токенов каждые
    6 с держали модель, пока подсказчик и ⚡ ждали (аудит 30.08, DS I1)."""
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    body = src[src.index("def dialog_markup_loop"):src.index("def name_loop")]
    assert 'toggles["hints"]' in body and 'hint_slot("разметка", timeout=1.0, quiet=True)' in body
    assert body.count("manual_evt.is_set()") == 3, "до замка, после замка и посреди стрима (GLM)"
    assert "seen.discard(key)" in body and "hint_lock.acquire" not in body, "контракт арбитра — в hint_slot"
    assert "for tok in llm.stream(" in body and "break" in body, "стрим уступает ручному вопросу"


def test_minutes_draft_and_final_share_a_lock_and_replace_atomically():
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    draft = src[src.index("def minutes_loop"):src.index("def _do_summary")]
    final = src[src.index("def _do_summary"):src.index("def stdin_loop")]
    assert "with minutes_lock:" in draft and "with minutes_lock:" in final
    assert 'mpath.write_text("<!-- черновик' not in draft, "черновик — только через tmp+replace"
    # Гейт переехал в общий safe_write (DS+GLM по #465): та же гарантия
    # «tmp+replace и stat-сверка» теперь обязана идти через expect-снимок.
    assert "safe_write.write_text(" in draft and "expect=before" in draft, \
        "чужой финал из другого процесса не затирается (GLM; протокол — общий safe_write)"
    assert "safe_write.stat_snapshot(mpath)" in draft, "снимок — одним stat, до проверки маркера"


def test_pending_question_is_replaced_as_a_whole():
    """Читатель берёт снимок словаря: ни «новый text + старый at», ни наоборот."""
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    assert '_pending_q[0] = {"text":' in src
    assert "nonlocal_pending" not in src and ".update(text=" not in src


def test_fast_trigger_reports_dropped_frames():
    src = pathlib.Path(daemon.__file__).read_text(encoding="utf-8")
    tap = src[src.index("def _tap(src, part)"):src.index("hub.on_frame = _tap")]
    assert "drops.dropped()" in tap and "target=emit_error" in tap, "сказать — не из аудио-потока (luna r1)"
    assert "is_alive()" in tap, "один глашатай за раз (DS r2)"
    assert "put_nowait" in tap and "frame_q.full()" not in tap, "без TOCTOU full()+put (GLM)"
    assert "emit_error(msg)" not in tap
