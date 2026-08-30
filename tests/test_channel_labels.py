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


def test_no_name_means_mic_is_owner_but_nothing_is_signed():
    lb = ChannelLabels.from_config(_cfg(""))
    assert lb.mic_raw == "Я" and lb.mic_signed == "" and not lb.collision
    assert lb.is_owner_line("Я") and not lb.is_owner_line("Иван")
    assert lb.signed_for(1, "Я", heard=owner_voice.Heard(), neutral="Собеседник 1") == "Собеседник 1"


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
    assert src.count("chan.is_mic(") >= 5 and "chan.is_owner_line(" in src and "chan.signed_for(" in src
