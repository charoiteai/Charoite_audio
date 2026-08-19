"""Чьи это слова: владельца или собеседника — решает КАНАЛ захвата.

Микрофон — аппаратный вход владельца, системный звук — то, что говорят
удалённые собеседники. Это не догадка о голосе, а устройство записи, и
именно этим подход отличается от провала 20.07, когда владельца пытались
угадать ВНУТРИ микрофонного канала: «первый голос» ловил лектора из видео,
«доминирование» ошибалось тоже.

Опираться на канал напрямую всё же нельзя — есть три дыры, и все три
закрываются здесь:

1. **Очная встреча.** Динамик молчит, в микрофон говорят все, кто в
   комнате. Различать некого — имя не ставится никому. Признак звонка:
   системный канал хоть раз нёс речь.
2. **Эхо.** Голос собеседника из динамиков попадает в микрофон.
   Подавление в `audio.py` глушит одновременную речь, но не тихий чанк и
   не длинную реверберацию. Второй эшелон здесь: голос, который слышен и
   в системном канале, владельцем быть не может. Центроиды у трекера
   общие для обоих каналов, поэтому эхо получает тот же номер голоса, что
   и оригинал.
3. **Гибрид.** Владелец в комнате не один: коллега рядом попадает в тот же
   микрофон. Имя ставится только тому голосу, который в микрофоне явно
   преобладает; нет преобладания — все остаются нейтральными.

Счётчики живут в памяти встречи и умирают вместе с процессом: секунды речи
по номеру голоса — это производное от голоса, и обещание «ничего
голосового на диск» распространяется и на них.
"""
from __future__ import annotations

import dataclasses
import math

#: Сколько секунд речи в микрофоне нужно накопить, прежде чем вообще решать.
#: На двух фразах «преобладание» — это шум, а не факт.
MIN_MIC_SECONDS = 15.0

#: Какую долю микрофонной речи обязан занимать голос владельца.
MIN_SHARE = 0.6

#: И насколько он должен опережать второго. Без отрыва два собеседника
#: в комнате по очереди становились бы «владельцем».
MIN_LEAD = 0.15

#: Сколько речи в системном канале достаточно, чтобы счесть голос чужим.
#: Не ноль: короткий всплеск может быть шумом нарезки, а не голосом.
ECHO_SECONDS = 1.0

#: Окно, за которое считается преобладание. Экспоненциальное затухание:
#: формат встречи меняется по ходу (коллега подсел, ушёл), и решение обязано
#: следовать за этим, а не за первыми минутами.
WINDOW_S = 180.0


@dataclasses.dataclass
class Heard:
    """Сколько речи каждый голос дал в каждый канал — в пределах встречи.

    На диск не попадает и в сериализуемые объекты не кладётся.
    """
    mic: dict[int, float] = dataclasses.field(default_factory=dict)
    bh: dict[int, float] = dataclasses.field(default_factory=dict)
    #: Нёс ли системный канал речь хоть раз: признак звонка.
    call: bool = False
    #: Момент последнего затухания. Именно None, а не 0.0: нулевая отметка
    #: времени — законное значение (тесты, монотонные часы с нуля), и
    #: `if not self._last` съедал бы первое затухание молча.
    _last: float | None = None

    def note(self, voice: int | None, seconds: float, *, is_mic: bool,
             now: float | None = None) -> None:
        """Отметить кусок речи. `voice=None` — трекер не определился."""
        if not is_mic:
            self.call = True
        if voice is None or seconds <= 0:
            return
        if now is not None:
            self._decay(now)
        acc = self.mic if is_mic else self.bh
        acc[voice] = acc.get(voice, 0.0) + seconds

    def _decay(self, now: float) -> None:
        if self._last is None:
            self._last = now
            return
        dt = now - self._last
        if dt <= 0:
            return
        self._last = now
        factor = math.exp(-dt / WINDOW_S)
        for acc in (self.mic, self.bh):
            for voice in list(acc):
                acc[voice] *= factor
                if acc[voice] < 0.01:       # осевший до нуля голос не держим
                    del acc[voice]


def owner_voice(heard: Heard, *, min_seconds: float = MIN_MIC_SECONDS,
                min_share: float = MIN_SHARE, min_lead: float = MIN_LEAD,
                echo_seconds: float = ECHO_SECONDS) -> int | None:
    """Номер голоса владельца — или None, если решать не на чем.

    None здесь означает «оставить нейтральные метки», а не «ошибка». Цена
    ошибки несимметрична: приписать владельцу чужие слова хуже, чем не
    подписать его собственные, — неверная метка переписывает встречу задним
    числом, и минутки с графом наследуют её молча.
    """
    if not heard.call:
        return None                     # очная встреча: различать некого
    total = sum(heard.mic.values())
    if total < min_seconds:
        return None                     # рано решать
    # Голоса, слышные и в системном канале, — это эхо собеседников.
    candidates = {v: s for v, s in heard.mic.items()
                  if heard.bh.get(v, 0.0) <= echo_seconds}
    if not candidates:
        return None
    ranked = sorted(candidates.items(), key=lambda kv: -kv[1])
    voice, seconds = ranked[0]
    share = seconds / total
    second = ranked[1][1] / total if len(ranked) > 1 else 0.0
    if share < min_share or share - second < min_lead:
        return None                     # в микрофоне не один человек
    return voice


def label_for(voice: int | None, *, is_mic: bool, heard: Heard,
              owner_label: str, other_label: str,
              neutral: str) -> str:
    """Метка для куска речи.

    `owner_label` — метка микрофонного канала (имя из настроек), `other_label`
    — метка системного, `neutral` — нейтральное «Собеседник N» от трекера.

    Совпадение меток каналов (в настройках написали «Собеседник») делает
    признак канала бессмысленным — тогда владельца не подписываем вовсе.
    """
    if not is_mic or owner_label == other_label or not owner_label:
        return neutral
    return owner_label if voice is not None and voice == owner_voice(heard) else neutral
