"""Имя владельца ставится по каналу — и только там, где канал честен.

Цена ошибки несимметрична: приписать владельцу чужие слова хуже, чем не
подписать его собственные. Неверная метка переписывает встречу задним
числом, минутки и граф наследуют её молча, поручение уходит не тому. Поэтому
почти все проверки ниже — про случаи, когда имя ставить НЕЛЬЗЯ.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import owner_voice as ov  # noqa: E402

OWNER, OTHER = "Игорь Ветров", "Собеседник"


def _call(mic: dict[int, float], bh: dict[int, float] | None = None) -> ov.Heard:
    """Звонок: в системном канале была речь."""
    heard = ov.Heard()
    for voice, seconds in (bh or {1: 30.0}).items():
        heard.note(voice, seconds, is_mic=False)
    for voice, seconds in mic.items():
        heard.note(voice, seconds, is_mic=True)
    return heard


def test_звонок_один_голос_в_микрофоне_это_владелец():
    """Главный случай: удалённая встреча, владелец говорит в свой микрофон."""
    heard = _call(mic={7: 40.0})
    assert ov.owner_voice(heard) == 7
    assert ov.label_for(7, is_mic=True, heard=heard, owner_label=OWNER,
                        other_label=OTHER, neutral="Собеседник 2") == OWNER


def test_собеседники_остаются_собеседниками():
    """Метка системного канала не становится именем владельца никогда."""
    heard = _call(mic={7: 40.0})
    assert ov.label_for(1, is_mic=False, heard=heard, owner_label=OWNER,
                        other_label=OTHER, neutral="Собеседник 1") == "Собеседник 1"


def test_очная_встреча_не_подписывает_никого():
    """Динамик молчит — значит в микрофоне вся комната, и различать некого.
    Ровно тот случай, ради которого 20.07 отказались от угадывания."""
    heard = ov.Heard()
    heard.note(7, 120.0, is_mic=True)
    assert heard.call is False
    assert ov.owner_voice(heard) is None
    assert ov.label_for(7, is_mic=True, heard=heard, owner_label=OWNER,
                        other_label=OTHER, neutral="Собеседник 1") == "Собеседник 1"


def test_эхо_собеседника_не_получает_имя_владельца():
    """Голос из динамиков попадает в микрофон: подавление в audio.py глушит
    одновременную речь, но не тихий чанк и не длинную реверберацию. Голос,
    слышный в ОБОИХ каналах, — это собеседник, а не владелец."""
    heard = _call(mic={3: 50.0}, bh={3: 40.0})   # тот же номер голоса в обоих
    assert ov.owner_voice(heard) is None


def test_эхо_не_мешает_если_владелец_говорит_больше():
    """Смешанный случай: в микрофоне и владелец, и эхо собеседника."""
    heard = _call(mic={7: 60.0, 3: 5.0}, bh={3: 40.0})
    assert ov.owner_voice(heard) == 7, "эхо отбрасывается, владелец остаётся"


def test_коллега_рядом_оставляет_метки_нейтральными():
    """Гибрид: часть людей в комнате, часть на связи. В микрофон говорят
    двое — приписать реплики одному из них нельзя."""
    heard = _call(mic={7: 40.0, 8: 35.0})
    assert ov.owner_voice(heard) is None


def test_редкая_реплика_соседа_не_ломает_подпись():
    """Один вопрос коллеги за встречу не должен лишать владельца имени."""
    heard = _call(mic={7: 90.0, 8: 6.0})
    assert ov.owner_voice(heard) == 7


def test_рано_решать_пока_речи_мало():
    """На двух фразах «преобладание» — это шум нарезки, а не факт."""
    heard = _call(mic={7: 8.0})
    assert ov.owner_voice(heard) is None


def test_имя_не_задано_подписывать_нечем():
    heard = _call(mic={7: 40.0})
    assert ov.label_for(7, is_mic=True, heard=heard, owner_label="",
                        other_label=OTHER, neutral="Собеседник 1") == "Собеседник 1"


def test_совпадение_меток_каналов_обесценивает_признак():
    """В настройках написали «Собеседник» — метки каналов совпали, и канал
    больше ничего не различает. Молчать честнее, чем подписать наугад."""
    heard = _call(mic={7: 40.0})
    assert ov.label_for(7, is_mic=True, heard=heard, owner_label="Собеседник",
                        other_label="Собеседник", neutral="Собеседник 1") == "Собеседник 1"


def test_неопознанный_голос_остаётся_нейтральным():
    heard = _call(mic={7: 40.0})
    assert ov.label_for(None, is_mic=True, heard=heard, owner_label=OWNER,
                        other_label=OTHER, neutral="Собеседник 1") == "Собеседник 1"


def test_счётчики_забывают_старое():
    """Формат встречи меняется: коллега подсел и ушёл. Решение обязано
    следовать за этим, а не за первыми минутами."""
    heard = ov.Heard()
    heard.note(1, 30.0, is_mic=False)
    heard.note(8, 60.0, is_mic=True, now=0.0)         # коллега говорил в начале
    heard.note(7, 60.0, is_mic=True, now=600.0)       # десять минут спустя — владелец
    assert ov.owner_voice(heard) == 7


def test_смена_говорящего_не_переписывает_прошлое_мгновенно():
    """Обратный край того же: пока перевес не устоялся, метки нейтральны."""
    heard = ov.Heard()
    heard.note(1, 30.0, is_mic=False)
    heard.note(7, 60.0, is_mic=True, now=0.0)
    heard.note(8, 30.0, is_mic=True, now=60.0)
    assert ov.owner_voice(heard) is None, "двое говорят сопоставимо — молчим"
