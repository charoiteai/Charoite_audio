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
    assert src.count('is_mic=channel_speaker == hub.SPEAKER.get("mic", "")') == 2
