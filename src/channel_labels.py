"""Метки каналов и владелец — одна точка правды (партия D-П2, фаза 1).

До 30.08 «кто говорит / кто владелец» решали три источника: сырая метка
канала в `AudioHub.SPEAKER`, локальная `mic_label` демона (обнулялась при
коллизии имени с нейтральной меткой) и `user_name`, перечитанный в трёх
местах. Они расходились: при имени «Собеседник 2» чанк микрофона был
`is_mic=True` по сырой метке (счётчики секунд владельца копились), а
подпись — `is_mic=False` по обнулённой копии: две правды в одной встрече
(аудит 30.08, DS). Теперь всё это — один неизменяемый объект, собранный
один раз из конфига; демон и захват спрашивают его, а не сравнивают строки.
"""
from __future__ import annotations

from dataclasses import dataclass

import owner_voice
import speaker_names

NEUTRAL_MIC = "Я"
NEUTRAL_OTHER = "Собеседник"


def mic_label_for(cfg: dict, other: str = NEUTRAL_OTHER) -> str:
    """Сырая метка микрофонного канала: имя из настроек, если оно не сливается
    с нейтральной меткой собеседников; иначе «Я». Единственное правило —
    его зовут и захват (AudioHub), и демон."""
    own = (cfg.get("sufler", {}).get("user_name") or "").strip()
    if own and not owner_voice.collides_with_neutral(own, other):
        return own
    return NEUTRAL_MIC


@dataclass(frozen=True)
class ChannelLabels:
    mic_raw: str          # метка канала микрофона в стенограмме («Я» или имя)
    other: str            # метка системного канала («Собеседник»)
    owner_name: str       # имя из настроек как написано (для сверки по словам)
    mic_signed: str       # чем подписывать владельца; пусто — подпись выключена

    @classmethod
    def from_config(cls, cfg: dict, *, other: str = NEUTRAL_OTHER) -> "ChannelLabels":
        owner = (cfg.get("sufler", {}).get("user_name") or "").strip()
        mic = mic_label_for(cfg, other)
        # Две разные причины не подписывать: коллизия имени с нейтральной
        # меткой — подпись выключена (иначе реплики владельца склеились бы с
        # чужими); пустое имя — подпись «Я», как и было до партии: иначе
        # владелец в микрофоне становился «Собеседник N», и гейт ⚡ отвечал на
        # его собственные вопросы (DS r1 по #459, Critical).
        signed = "" if (owner and mic != owner) else mic
        return cls(mic_raw=mic, other=other, owner_name=owner, mic_signed=signed)

    @classmethod
    def from_capture(cls, cfg: dict, *, mic_raw: str, other: str) -> "ChannelLabels":
        """Из меток, которые захват уже выбрал: демон не пересчитывает правило
        рядом, а берёт факт — расхождение с AudioHub невозможно по построению
        (luna r2 по #459)."""
        owner = (cfg.get("sufler", {}).get("user_name") or "").strip()
        signed = "" if (owner and mic_raw != owner) else mic_raw
        return cls(mic_raw=mic_raw, other=other, owner_name=owner, mic_signed=signed)

    @property
    def collision(self) -> bool:
        """Имя задано, но подписывать им нельзя (совпало с нейтральной меткой)."""
        return bool(self.owner_name) and self.mic_raw != self.owner_name

    def is_mic(self, label: str) -> bool:
        """Канал микрофона — по сырой метке, всегда: это устройство, не имя."""
        return bool(self.mic_raw) and label == self.mic_raw

    def is_owner_line(self, name: str) -> bool:
        """Реплика владельца? Метка своего канала — владелец по определению;
        имя, сливающееся с нейтральной меткой, в сверку по словам не пускаем
        («Собеседник 2» делал бы владельцем каждого «Собеседник N»)."""
        if self.is_mic(name):
            return True
        if owner_voice.collides_with_neutral(self.owner_name, self.other):
            return False
        return speaker_names.is_owner(name, self.owner_name)

    def signed_for(self, voice: int | None, speaker: str, *, heard, neutral: str) -> str:
        """Подпись куска речи: имя владельца — только на микрофоне, только
        если голос среди голосов владельца; иначе нейтральная метка."""
        return owner_voice.label_for(voice, is_mic=self.is_mic(speaker), heard=heard,
                                     owner_label=self.mic_signed, other_label=self.other,
                                     neutral=neutral)
