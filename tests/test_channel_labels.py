"""D-П2: метки каналов и владелец — одна точка (характеризационные пинны)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import channel_labels  # noqa: E402
import owner_voice  # noqa: E402
from channel_labels import ChannelLabels  # noqa: E402


def _cfg(name):
    return {"sufler": {"user_name": name}}


def test_plain_name_signs_the_owner_and_is_the_mic_label():
    lb = ChannelLabels.from_config(_cfg("Иван Петров"))
    assert lb.mic_raw == "Иван Петров" and lb.mic_signed == "Иван Петров" and not lb.collision
    assert lb.is_mic("Иван Петров") and not lb.is_mic("Собеседник 1")
    assert lb.is_owner_line("Иван Петров") and lb.is_owner_line("Иван") and not lb.is_owner_line("Собеседник 1")


def test_collision_with_the_neutral_label_keeps_the_raw_mic_and_switches_the_signature_off():
    """Имя «Собеседник 2»: чанк микрофона по-прежнему микрофон (счётчики
    владельца копятся), но подписи нет — раньше две правды в одной встрече."""
    lb = ChannelLabels.from_config(_cfg("Собеседник 2"))
    assert lb.mic_raw == "Я" and lb.mic_signed == "" and lb.collision
    assert lb.is_mic("Я") and not lb.is_mic("Собеседник 2")
    assert lb.is_owner_line("Я"), "своя метка канала — владелец по определению"
    assert not lb.is_owner_line("Собеседник 1"), "«Собеседник 2» не делает владельцем каждого «Собеседник N»"
    heard = owner_voice.Heard()
    assert lb.signed_for(1, "Я", heard=heard, neutral="Собеседник 3") == "Собеседник 3"


def test_no_name_signs_the_owner_as_ya_like_before(monkeypatch):
    lb = ChannelLabels.from_config(_cfg(""))
    assert lb.mic_raw == "Я" and lb.mic_signed == "Я" and not lb.collision, "пустое имя — подпись «Я», как до партии"
    assert lb.is_owner_line("Я") and not lb.is_owner_line("Иван")
    heard = owner_voice.Heard()
    assert lb.signed_for(1, "Я", heard=heard, neutral="Собеседник 1") == "Собеседник 1", "голос не среди владельцев — нейтраль"
    monkeypatch.setattr(owner_voice, "owner_voices", lambda heard, **k: {1})   # голос 1 — владелец
    assert lb.signed_for(1, "Я", heard=heard, neutral="Собеседник 1") == "Я", "владелец в микрофоне подписан «Я» — иначе гейт ⚡ отвечал на его вопросы (DS r1)"
    assert lb.signed_for(1, "Собеседник", heard=heard, neutral="Собеседник 1") == "Собеседник 1", "чужой канал — нейтраль"


def test_mic_label_rule_is_shared_with_the_capture():
    """AudioHub и демон зовут одно правило — иначе метка захвата и метка
    подписи расходились после старта (ревью 19.08, седьмой круг)."""
    assert channel_labels.mic_label_for(_cfg("Собеседник")) == "Я"
    assert channel_labels.mic_label_for(_cfg("Мария")) == "Мария"
    src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "audio.py").read_text(encoding="utf-8")
    assert "channel_labels.mic_label_for(" in src


def test_daemon_asks_the_labels_instead_of_comparing_strings():
    src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "daemon.py").read_text(encoding="utf-8")
    assert 'hub.SPEAKER.get("mic", "")' not in src, "сырая метка сравнивается только внутри ChannelLabels"
    assert "mic_label = " not in src, "локальной копии метки больше нет — она расходилась с захватом"
    assert src.count("chan.is_mic(") >= 6 and "chan.is_owner_line(" in src and "chan.signed_for(" in src
    gate = src[src.index("режим собеседования: вопрос с той стороны"):src.index("fire_question(added)")]
    assert "not chan.is_mic(speaker)" in gate, "сырой канал микрофона — никогда не вопрос собеседника (DS r1)"


def test_offline_rebuild_shares_the_signature_rule():
    """Офлайн-пересборка держала свою копию «имя или „Я“» с отдельной проверкой
    коллизии (критика GLM r1): теперь ChannelLabels.mic_signed — и там."""
    src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    assert "channel_labels.ChannelLabels.from_config(cfg).mic_signed" in src
    assert 'or "Я"' not in src, "литерала «Я» в правиле подписи больше нет"
