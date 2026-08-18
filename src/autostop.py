"""Когда пора остановить запись саму: тишина или потолок длительности.

17.08 запись шла 18 часов 25 минут: встреча кончилась, «Стоп» никто не нажал,
и ноутбук всю ночь писал пустую комнату, гонял STT и держал модель. Данные не
потерялись, но пересборка такой записи потом на час заняла тяжёлую модель и
уронила подсказки живой встречи (факт 18.08).

Два правила, оба консервативные — цена ложного стопа выше цены лишнего часа
записи:

1. **Тишина.** Считаем НЕ по громкости: энергетический гейт срабатывает на
   кулер, клавиатуру и шум улицы, и «тишины» не наступает никогда. Считаем по
   распознанной речи — по тому же тексту, что попадает в стенограмму.
   Порог зависит от того, встреча это или забытая запись: звучал один голос
   (или ни одного) — пяти минут хватает; звучали двое и больше — это разговор
   людей, где пауза на чтение документа или демо без звука нормальна, и порог
   втрое больше.
2. **Потолок длительности.** Ни одна встреча не идёт шесть часов; если идёт —
   человек нажмёт «Слушать встречу» ещё раз, а записанное уже сохранено и
   разобрано. Потолок нужен именно как страховка от «забыл выключить».

Перед стопом — предупреждение (`warn_s` секунд): любая речь снимает его.
Решение принимает чистая функция `decide()`, без часов и потоков, — её и
проверяют тесты.
"""
from __future__ import annotations

from dataclasses import dataclass

# Секунды. Мягкие дефолты: включено, но срабатывает только на явном забытии.
DEFAULTS = {
    "enabled": True,
    "silence_minutes": 5.0,          # один голос за запись (или тишина с начала)
    "meeting_silence_minutes": 15.0,  # звучали двое и больше — это встреча
    "max_hours": 6.0,                 # потолок длительности (0 — без потолка)
    "warn_seconds": 60.0,             # предупреждение перед стопом
    "min_minutes": 2.0,               # раньше этого не трогаем запись вовсе
}

SILENCE = "silence"
LIMIT = "limit"


@dataclass(frozen=True)
class Limits:
    enabled: bool = True
    silence_s: float = 300.0
    meeting_silence_s: float = 900.0
    max_s: float = 21_600.0
    warn_s: float = 60.0
    min_s: float = 120.0

    @property
    def any_rule(self) -> bool:
        return self.enabled and (self.silence_s > 0 or self.max_s > 0)


def limits_from_cfg(cfg: dict) -> Limits:
    """Настройки из `sufler.autostop` (пусто = дефолты выше).

    Ноль в любом пороге выключает своё правило: `silence_minutes: 0` — не
    останавливать по тишине, `max_hours: 0` — не ограничивать длительность.
    """
    raw = (cfg.get("sufler") or {}).get("autostop", {})
    if raw is False:            # `autostop: false` — короткая форма выключения
        return Limits(enabled=False)
    if not isinstance(raw, dict):   # `autostop: true`, мусор — дефолты
        raw = {}

    def num(key: str) -> float:
        value = raw.get(key, DEFAULTS[key])
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    silence = num("silence_minutes") * 60
    meeting = num("meeting_silence_minutes") * 60
    return Limits(
        enabled=bool(raw.get("enabled", DEFAULTS["enabled"])),
        silence_s=silence,
        # Порог встречи не может быть строже одиночного: иначе перепутанные
        # местами значения молча резали бы живой разговор раньше пустой комнаты.
        meeting_silence_s=max(meeting, silence),
        max_s=num("max_hours") * 3600,
        warn_s=num("warn_seconds"),
        min_s=num("min_minutes") * 60,
    )


@dataclass(frozen=True)
class Decision:
    action: str = ""      # "" | "warn" | "stop"
    reason: str = ""      # SILENCE | LIMIT
    text: str = ""        # строка человеку
    seconds_left: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.action)


def _minutes(seconds: float) -> str:
    m = int(round(seconds / 60))
    if m % 10 == 1 and m % 100 != 11:
        return f"{m} минуту"
    if m % 10 in (2, 3, 4) and m % 100 not in (12, 13, 14):
        return f"{m} минуты"
    return f"{m} минут"


def _hours(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.0f}" if abs(h - round(h)) < 0.05 else f"{h:.1f}"


def decide(*, now: float, started_at: float, last_speech_at: float | None,
           voices: int, limits: Limits) -> Decision:
    """Пора ли предупредить или остановить запись.

    now/started_at/last_speech_at — монотонные секунды. `last_speech_at=None`
    означает «речи не было ни разу»: отсчёт тишины идёт от начала записи.
    `voices` — сколько разных голосов звучало за запись (0, 1, 2, …).
    """
    if not limits.any_rule:
        return Decision()
    age = now - started_at
    if age < limits.min_s:
        return Decision()   # первые минуты не трогаем: запись только началась

    # Потолок длительности идёт первым: он безусловен, а тишина обсуждаема.
    if limits.max_s > 0:
        left = limits.max_s - age
        if left <= 0:
            return Decision("stop", LIMIT,
                            f"запись идёт {_hours(limits.max_s)} ч — останавливаю "
                            "(потолок длительности)")
        if left <= limits.warn_s:
            return Decision("warn", LIMIT,
                            f"через {_minutes(left)} остановлю запись: "
                            f"идёт {_hours(limits.max_s)} ч", left)

    if limits.silence_s > 0:
        # Настоящая встреча (звучали двое и больше) переживает паузу на чтение
        # документа или демо без звука; забытая запись с одним голосом — нет.
        threshold = limits.meeting_silence_s if voices >= 2 else limits.silence_s
        quiet_since = last_speech_at if last_speech_at is not None else started_at
        quiet = now - quiet_since
        left = threshold - quiet
        if left <= 0:
            heard = ("речи не было с начала записи" if last_speech_at is None
                     else f"тишина {_minutes(quiet)}")
            return Decision("stop", SILENCE, f"{heard} — останавливаю запись")
        if left <= limits.warn_s:
            return Decision("warn", SILENCE,
                            f"тишина {_minutes(quiet)}; через {_minutes(left)} "
                            "остановлю запись — скажите что-нибудь, чтобы продолжить",
                            left)
    return Decision()
