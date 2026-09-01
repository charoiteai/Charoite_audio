"""Когда пора остановить запись саму: тишина или потолок длительности.

17.08 запись шла 18 часов 25 минут: встреча кончилась, «Стоп» никто не нажал,
и ноутбук всю ночь писал пустую комнату, гонял STT и держал модель. Данные не
потерялись, но пересборка такой записи потом на час заняла тяжёлую модель и
уронила подсказки живой встречи (факт 18.08).

Правила консервативные — цена ложного стопа выше цены лишнего часа записи:

1. **Речи не было ни разу** (`no_speech_s`, 5 минут): включили и ушли. Самый
   безопасный случай: останавливать нечего, кроме тишины.
2. **Тишина после разговора** (`silence_s`, 15 минут): люди говорили и
   замолчали. Порог втрое больше, потому что в живой встрече пауза на чтение
   документа, тихое демо и раздумье — норма.
3. **Тишина, когда собеседников не было слышно** (`alone_s`, 10 минут):
   системный канал молчал всю запись, говорил только владелец. Пятнадцать
   минут тут — перестраховка: если второй стороны нет, запись почти наверняка
   забыта, а не «пауза на чтение документа». Но и не пять: очную встречу канал
   не отличает от диктофона, а пауза на чтение документа в комнате — норма
   (ревью 20.08, DeepSeek I2). Десять — середина, которую видно и можно
   подвинуть.
4. **Потолок длительности** (`max_s`, 6 часов): страховка от «забыл
   выключить». Записанное к этому моменту уже сохранено и разобрано, а
   продолжить можно новой кнопкой.

⚠️ **Граница правила 3 — очная встреча.** Признак «собеседников нет» берётся
из КАНАЛА (системный звук молчал), и очный разговор, где все сидят в одной
комнате и попадают в микрофон, для него выглядит так же, как диктофон. Порог
поэтому не равен `no_speech_s`, а задаётся отдельно и поднимается одной
строкой конфига: тем, у кого встречи очные, `alone_minutes: 15` возвращает
прежнее поведение.

Различать «один голос» и «двое и больше» по ДИАРИЗАЦИИ мы пробовали и
отказались (ревью 18.08, DeepSeek): число голосов у нас — это число
диаризационных МЕТОК, а не людей. Правило 3 стоит на другом признаке —
канальном: системный звук либо нёс речь, либо нет, и это известно точно, без
моделей. В очной встрече без моделей диаризации все реплики идут одной меткой
канала, и живой разговор двоих выглядел бы как «один голос» — то есть получал
бы самый агрессивный порог. Надёжно известно только одно: звучала речь или
нет, — на этом и стоим.

Тишина считается по РАСПОЗНАННОЙ речи, а не по громкости: энергетический гейт
срабатывает на кулер, клавиатуру и шум улицы, и «тишины» не наступало бы
никогда. Обратная сторона честно названа в доках: громкая фоновая речь
(телевизор, радио) для нас — разговор, её ловит только потолок длительности.

Возраст записи меряется по СТЕННЫМ часам, а тишина — по монотонным: сон
ноутбука не должен ни съедать потолок, ни считаться тишиной в комнате.

Решение принимает чистая функция `decide()`, состояние контура (предупредили,
попросили) держит `Watch` — обе проверяются тестами без часов и потоков.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

# Секунды. Мягкие дефолты: включено, но срабатывает только на явном забытии.
DEFAULTS = {
    "enabled": True,
    "no_speech_minutes": 5.0,   # речи не было ни разу с начала записи
    "silence_minutes": 15.0,    # тишина после того, как разговор был
    "alone_minutes": 10.0,      # то же, но собеседников не было слышно ни разу
    "max_hours": 6.0,           # потолок длительности (0 — без потолка)
    "warn_seconds": 60.0,       # предупреждение перед стопом
    "min_minutes": 2.0,         # раньше этого не трогаем запись вовсе
    "farewell_seconds": 60.0,   # тишина после ОДНОГО прощания (0 — выкл)
}

NO_SPEECH = "no_speech"
SILENCE = "silence"
ALONE = "alone"
LIMIT = "limit"
FAREWELL = "farewell"

# Прощание в живой стенограмме — сигнал конца встречи (запрос владельца
# 01.09: разговор кончился, а запись ждала штатные 15 минут тишины).
# Детект детерминированный и нарочно узкий: цена ложного стопа — оборванная
# запись, которую не переснять.
#
# Реплика считается прощальной, если она КОРОТКАЯ (до 6 слов) и либо
# оканчивается многословной прощальной формой («до свидания», «всем пока»),
# либо целиком состоит из прощальных слов («ну всё, пока»). Одиночное
# «пока» в длинной реплике — союз («пока не забыл») и не матчится.
# СИЛЬНЫЕ хвосты: их достаточно в конце короткой реплики. Слабые формы
# («всем спасибо», «хорошего дня», «пока» с чем угодно) проходят только
# путём «вся реплика из прощальных слов»: замер по шести неделям живых
# стенограмм дал 9 срабатываний ПОСРЕДИ встречи (уход одного участника,
# «Ну, а пока» на 23-й реплике из 266) — с ними стоп по одиночному
# прощанию рвал бы живые встречи; после ужесточения осталось 3, все с
# малым вредом, при 18 ловимых настоящих финалах (GLM r1 по #471,
# advisory-замер).
_FAREWELL_TAILS = (
    "до свидания", "досвидания", "до связи", "до встречи", "до созвона",
    "до завтра", "до понедельника",
    # en/zh-пресеты: те же правила, свои формы
    "goodbye", "bye everyone", "see you", "take care",
    "再见", "拜拜", "回聊",
)
_FAREWELL_WORDS = frozenset(
    "пока пока-пока всем ну всё все давай давайте спасибо счастливо "
    "услышимся увидимся ладно тогда ага угу да bye bye-bye thanks ok okay "
    "再见 拜拜".split())


def farewell_words(text: str) -> frozenset[str]:
    """Нормализованные слова реплики — для дедупа шва/эха в демоне."""
    return frozenset(re.findall(r"[\w-]+", text.lower().replace("ё", "е")))


def is_farewell(text: str) -> bool:
    """Прощальная ли реплика. Работает по added-тексту после дедупа."""
    words = re.findall(r"[\w-]+", text.lower().replace("ё", "е"))
    if not words or len(words) > 6:
        return False
    if "через" in words or "спустя" in words \
            or " in " in f" {' '.join(words)} ":
        # «Через/спустя пять минут увидимся» — перерыв, не конец встречи
        # (живой смоук по 20 тысячам реплик нашёл ровно этот класс).
        return False
    plain = " ".join(words)
    if any(plain.endswith(t) for t in _FAREWELL_TAILS):
        return True
    # «ну всё, пока» — все слова прощальные и среди них есть само прощание
    core = {"пока", "пока-пока", "счастливо", "увидимся", "услышимся",
            "bye", "bye-bye", "再见", "拜拜"}
    return bool(core & set(words)) and \
        all(w in _FAREWELL_WORDS for w in words)


def _falsy(value) -> bool:
    """`false`, `"false"`, `"нет"`, `0` — одинаково «выключено».

    YAML отдаёт строку, если значение в кавычках; молча включённый автостоп
    при `autostop: "false"` — ровно тот сюрприз, за которым потом ходят в код.
    """
    if value is False or value is None:
        return True
    return str(value).strip().lower() in {"false", "no", "off", "нет", "0"}


@dataclass(frozen=True)
class Limits:
    enabled: bool = True
    no_speech_s: float = 300.0
    silence_s: float = 900.0
    #: Тишина, когда системный канал молчал всю запись (собеседников не слышно).
    alone_s: float = 600.0
    max_s: float = 21_600.0
    warn_s: float = 60.0
    min_s: float = 120.0
    #: Тишина после одного прощания; два прощания подряд — стоп без ожидания.
    farewell_s: float = 60.0
    #: farewell_seconds задан в конфиге руками: просьба работает и там, где
    #: базовые правила тишины выключены (прецедент alone, ревью 20.08).
    farewell_explicit: bool = False

    @property
    def any_rule(self) -> bool:
        return self.enabled and (self.no_speech_s > 0 or self.silence_s > 0
                                 or self.alone_s > 0 or self.max_s > 0
                                 or self.farewell_s > 0)


def limits_from_cfg(cfg: dict) -> Limits:
    """Настройки из `sufler.autostop` (пусто = дефолты выше).

    Ноль в любом пороге выключает своё правило и только своё:
    `no_speech_minutes: 0` — не трогать запись, в которой никто не говорил,
    `silence_minutes: 0` — не считать тишину после разговора,
    `max_hours: 0` — не ограничивать длительность.
    """
    raw = (cfg.get("sufler") or {}).get("autostop", {})
    if _falsy(raw):             # `autostop: false` — короткая форма выключения
        return Limits(enabled=False)
    if not isinstance(raw, dict):   # `autostop: true`, мусор — дефолты
        raw = {}

    def num(key: str) -> float:
        value = raw.get(key, DEFAULTS[key])
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    no_speech = num("no_speech_minutes") * 60
    silence = num("silence_minutes") * 60
    alone = num("alone_minutes") * 60
    base = Limits(
        enabled=not _falsy(raw.get("enabled", DEFAULTS["enabled"])),
        no_speech_s=no_speech,
        # Порог после разговора не может быть строже «речи не было»: иначе
        # перепутанные местами значения молча резали бы живой разговор раньше
        # пустой комнаты.
        silence_s=max(silence, no_speech) if silence else 0.0,
        # Тот же порядок, что у silence_s: одинокая запись не должна резаться
        # раньше пустой комнаты, а ноль по-прежнему выключает своё правило.
        # Дефолтное правило не воскресает само: конфиг, где тишину выключили
        # (`silence_minutes: 0`), не должен молча получить новое включённое
        # правило (ревью 20.08, DeepSeek I3). Но ЯВНО заданный `alone_minutes`
        # — это просьба, и глушить её нулём соседнего ключа значит оставить
        # человека вообще без автостопа по тишине (второй круг, DeepSeek I1).
        alone_s=(max(alone, no_speech)
                 if (alone and (silence or "alone_minutes" in raw)) else 0.0),
        max_s=num("max_hours") * 3600,
        warn_s=num("warn_seconds"),
        min_s=num("min_minutes") * 60,
        farewell_s=0.0,
    )
    # Дефолтное правило не воскресает в мёртвом конфиге (тот же дух, что
    # у alone: ревью 20.08, DeepSeek I3): владелец, выключивший ВСЁ
    # нулями, не должен молча получить новое правило. Явно заданный
    # farewell_seconds — просьба, работает всегда. Живость смотрим по
    # ИТОГОВЫМ правилам (сырое alone_minutes имеет ненулевой дефолт).
    alive = (base.no_speech_s > 0 or base.silence_s > 0
             or base.alone_s > 0 or base.max_s > 0)
    if "farewell_seconds" in raw or alive:
        return replace(base, farewell_s=num("farewell_seconds"),
                       farewell_explicit="farewell_seconds" in raw)
    return base


@dataclass(frozen=True)
class Decision:
    action: str = ""      # "" | "warn" | "stop" | "resumed"
    reason: str = ""      # NO_SPEECH | SILENCE | ALONE | LIMIT
    text: str = ""        # строка человеку
    seconds_left: float = 0.0

    def __bool__(self) -> bool:
        return bool(self.action)


def _minutes(seconds: float) -> str:
    # Не меньше минуты: «через 0 минут остановлю запись» — это не срок,
    # а недоразумение (ревью 18.08).
    m = max(1, int(round(seconds / 60)))
    if m % 10 == 1 and m % 100 != 11:
        return f"{m} минуту"
    if m % 10 in (2, 3, 4) and m % 100 not in (12, 13, 14):
        return f"{m} минуты"
    return f"{m} минут"


def _seconds_ru(seconds: float) -> str:
    """«45 секунд» для коротких прощальных сроков: минутная гранулярность
    превращала бы 60-секундное окно в «через 1 минуту» на любом остатке."""
    n = max(5, int(round(seconds / 5)) * 5)
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} секунду"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} секунды"
    return f"{n} секунд"


def _hours(seconds: float) -> str:
    h = seconds / 3600
    return f"{h:.0f}" if abs(h - round(h)) < 0.05 else f"{h:.1f}"


def decide(*, age_s: float, quiet_s: float, spoke: bool, limits: Limits,
           alone: bool = False, farewells: int = 0) -> Decision:
    """Пора ли предупредить или остановить запись.

    age_s — возраст записи по стенным часам; quiet_s — сколько длится тишина
    (по монотонным; если речи не было ни разу — с начала записи);
    spoke — звучала ли распознанная речь за эту запись хоть раз;
    alone — за всю запись системный канал не нёс речи ни разу, то есть
    собеседников слышно не было (признак канальный, диаризация не нужна);
    farewells — сколько прощальных реплик идёт ПОДРЯД в хвосте стенограммы
    (обычная реплика сбрасывает в ноль). Одно прощание срезает порог тишины
    до farewell_s; два и больше — обмен прощаниями, стоп без ожидания
    (риск двойного «пока» посреди встречи взят владельцем явно, 01.09).
    """
    if not limits.any_rule:
        return Decision()
    if age_s < limits.min_s:
        return Decision()   # первые минуты не трогаем: запись только началась

    # Потолок длительности идёт первым: он безусловен, а тишина обсуждаема.
    if limits.max_s > 0:
        left = limits.max_s - age_s
        if left <= 0:
            return Decision("stop", LIMIT,
                            f"запись идёт {_hours(limits.max_s)} ч — останавливаю "
                            "(потолок длительности)")
        if left <= limits.warn_s:
            return Decision("warn", LIMIT,
                            f"через {_minutes(left)} остановлю запись: "
                            f"идёт {_hours(limits.max_s)} ч", left)

    if spoke and farewells >= 2 and limits.farewell_s > 0:
        return Decision("stop", FAREWELL,
                        "прощание прозвучало дважды — останавливаю запись")
    reason = SILENCE if spoke else NO_SPEECH
    if not spoke:
        threshold = limits.no_speech_s
    elif alone and limits.alone_s > 0:
        # Говорил только владелец: пятнадцать минут silence_s рассчитаны на
        # паузу в разговоре, которого тут не было (правило 3, по умолчанию
        # 10 минут — см. DEFAULTS["alone_minutes"]).
        reason = ALONE
        threshold = limits.alone_s
    else:
        threshold = limits.silence_s
    # Прощание СРЕЗАЕТ включённый порог тишины, но не воскрешает
    # выключенный (GLM r1 по #471: silence_minutes: 0 — это «не
    # останавливай после молчания», и дефолтное прощание обязано это
    # уважать). Явно заданный farewell_seconds — просьба, работает и при
    # выключенной тишине.
    if spoke and farewells == 1 and limits.farewell_s > 0 \
            and (limits.farewell_s < threshold
                 or (threshold <= 0 and limits.farewell_explicit)):
        # Одно прощание: не стоп, а короткое ожидание — «всем пока»
        # уходящего с середины созвона не должно рвать запись.
        reason, threshold = FAREWELL, limits.farewell_s
    if threshold > 0:
        left = threshold - quiet_s
        # Причина видна человеку: стоп на десятой минуте вместо пятнадцатой
        # без объяснения выглядит поломкой (ревью 20.08, DeepSeek M3).
        alone_note = ", собеседников не слышно" if reason == ALONE else ""
        if left <= 0:
            heard = ("прощание и тишина — останавливаю запись"
                     if reason == FAREWELL else
                     (f"тишина {_minutes(quiet_s)}{alone_note} — останавливаю "
                      "запись" if spoke
                      else "речи не было с начала записи — останавливаю запись"))
            return Decision("stop", reason, heard)
        if left <= limits.warn_s:
            if reason == FAREWELL:
                return Decision("warn", reason,
                                "услышал прощание — через "
                                f"{_seconds_ru(left)} остановлю запись; "
                                "скажите что-нибудь, чтобы продолжить", left)
            heard = (f"тишина {_minutes(quiet_s)}{alone_note}" if spoke
                     else "речи не было с начала записи")
            return Decision("warn", reason,
                            f"{heard}; через {_minutes(left)} остановлю запись — "
                            "скажите что-нибудь, чтобы продолжить", left)
    return Decision()


class FarewellStreak:
    """Счётчик прощаний подряд — с дедупом шва и межканального эха.

    Шов чанков STT и эхо одной фразы в двух каналах удваивают ОДНУ
    реплику (дедуп транскрипта не режет перекрытие в одно слово — DS r1
    по #471): прощание, чьи слова входят в предыдущее прощальное (или
    наоборот) и пришедшее в пределах echo_s, вторым не считается.
    Спорный обмен («всем пока» → «пока» за 2 секунды) уходит в мягкий
    путь одиночного прощания, а не в мгновенный стоп."""

    def __init__(self, echo_s: float = 5.0):
        self.echo_s = echo_s
        self.count = 0
        self._words: frozenset[str] = frozenset()
        self._at = 0.0

    def feed(self, text: str, now: float) -> int:
        """Учесть реплику из стенограммы; вернуть текущую серию прощаний."""
        if not is_farewell(text):
            self.count = 0
            return 0
        fw = farewell_words(text)
        echo = (self.count > 0 and now - self._at < self.echo_s
                and (fw <= self._words or self._words <= fw))
        if not echo:
            self.count += 1
        self._words, self._at = fw, now
        return self.count


class Watch:
    """Состояние контура автостопа: предупредили, попросили, ждём.

    Отдельно от `decide()`, потому что состояние — это и есть то, где живут
    ошибки: повтор предупреждения каждые пять секунд, «замолчавший навсегда»
    автостоп после неудачной попытки, просьба остановиться после того, как
    человек уже нажал «Стоп».
    """

    def __init__(self, limits: Limits, mute_s: float = 1800.0):
        self.limits = limits
        self.mute_s = mute_s
        self.warned = ""
        self.asked_at: float | None = None

    def tick(self, *, now: float, age_s: float, quiet_s: float, spoke: bool,
             last_speech_at: float | None = None, alone: bool = False,
             farewells: int = 0) -> Decision:
        """Что делать на этом такте. now/last_speech_at — монотонные секунды."""
        if self.asked_at is not None:
            # Просили остановиться и не дождались ответа. Молчим mute_s, но
            # возобновившийся разговор снимает паузу сразу: иначе «поговорили
            # три минуты и ушли» осталось бы без автостопа ещё на полчаса.
            if last_speech_at is not None and last_speech_at > self.asked_at:
                self.asked_at = None
            elif now - self.asked_at < self.mute_s:
                return Decision()
            else:
                self.asked_at = None

        d = decide(age_s=age_s, quiet_s=quiet_s, spoke=spoke, limits=self.limits,
                   alone=alone, farewells=farewells)
        if d.action == "warn":
            if self.warned == d.reason:
                return Decision()      # уже предупредили — не повторяемся
            self.warned = d.reason
            return d
        if d.action == "stop":
            self.warned = ""
            self.asked_at = now
            return d
        if self.warned:
            # Потолок длительности речь не отменяет: сказать «автостоп отменён»
            # там — обмануть (ревью 18.08, GLM). Предупреждение о потолке
            # просто перевзведётся за минуту до стопа.
            was, self.warned = self.warned, ""
            if was != LIMIT:
                return Decision("resumed", was, "автостоп отменён — снова слышу разговор")
        return Decision()
