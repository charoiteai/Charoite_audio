"""Нить встречи: то, что человек читает, пока разговор идёт.

Прежняя подсказка перегенерировалась целиком каждые несколько минут по
последним двум минутам разговора. Отсюда всё остальное: она повторяла уже
сказанное (за встречу 03.08 лог подсказок вырос до 68 КБ, в основном одно и то
же разными словами), срабатывала по таймеру даже когда ничего не изменилось, и
не различала вес — «поговорили о погоде» и «решили чинить механизм» выглядели
одинаково. Читать её во время разговора тяжело не потому, что плохо написано, а
потому что каждый раз надо заново понять, что изменилось.

Здесь другой принцип, взятый из практики прогрессивных заметок
(arXiv:2510.06677): нить РАСТЁТ. Модель получает уже собранное и дописывает
только новое; не появилось нового — не пишем ничего. Темы служат якорями, под
ними копятся строки четырёх видов, и вид виден глазом:

    ● Партиции цеховых таблиц                10:34
        Коля: механизм не нарезал → поток упал
      ⚑ решили: чинить механизм, не генератор     10:41
      ⏮ 30.07: мяч у ОБД, дата разбора не назначена
      ? кто принимает риски рассинхрона

Модуль без зависимостей: ни LLM, ни файлов, ни сети — только структура и
правила. Так его можно проверить тестом, а не глазами на живой встрече.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

# Знаки строк. Один символ вместо слова: на встрече читают боковым зрением, и
# «⚑» отличается от «-» быстрее, чем «Решение:» от «Обсуждение:».
TOPIC = "●"
SAY = "-"
DECISION = "⚑"
QUESTION = "?"
ARCHIVE = "⏮"
THOUGHT = "💭"

KINDS = {SAY: "say", DECISION: "decision", QUESTION: "question",
         ARCHIVE: "archive", THOUGHT: "thought"}

# Насколько строки должны совпасть, чтобы считаться повтором. 0.82 — компромисс
# из наблюдений: модель переписывает одну мысль другими словами чаще, чем
# рождает две действительно похожих.
SAME_ENOUGH = 0.82

# У заголовков тем правило строже. Они короткие, и посимвольная похожесть на
# такой длине врёт: «Тема 0» и «Тема 1» отличаются одним знаком из шести — по
# общему порогу это один и тот же якорь, и вся нить схлопывается в первую тему.
SAME_TITLE = 0.9
TITLE_MIN_LEN = 15

# То же и для реплик: «поток упал» против «поток встал» — похожесть 0.85 при
# противоположном смысле. Ниже этой длины сверяем только точное совпадение.
LINE_MIN_LEN = 24

# Сколько тем держим на экране целиком. Остальные сворачиваются в заголовок:
# «нить» перестаёт быть нитью, когда её надо прокручивать.
LIVE_TOPICS = 3


def _norm(text: str) -> str:
    """Строка для сравнения: без разметки, регистра и пунктуации."""
    text = re.sub(r"[*_`]", "", text.lower())
    return re.sub(r"[^\w\s]", " ", text).strip()


@dataclass
class Line:
    kind: str
    text: str
    at: str = ""

    def render(self) -> str:
        mark = next((m for m, k in KINDS.items() if k == self.kind), SAY)
        pad = "    " if self.kind == "say" else "  "
        stamp = f"    {self.at}" if self.at and self.kind == "decision" else ""
        return f"{pad}{mark} {self.text}{stamp}"


@dataclass
class Topic:
    title: str
    at: str = ""
    lines: list[Line] = field(default_factory=list)

    def render(self, full: bool = True) -> str:
        head = f"{TOPIC} {self.title}" + (f"    {self.at}" if self.at else "")
        if not full:
            # свёрнутая тема: заголовок и счётчик, чтобы было видно, что там
            # что-то было, но глаз не спотыкался
            return f"{head}  ({len(self.lines)})" if self.lines else head
        return "\n".join([head, *(ln.render() for ln in self.lines)])


class Thread:
    """Живая нить встречи. Растёт, не переписывается."""

    def __init__(self, live_topics: int = LIVE_TOPICS) -> None:
        self.topics: list[Topic] = []
        self.live_topics = live_topics

    # --- наполнение ---------------------------------------------------------

    def open_topic(self, title: str, at: str = "") -> Topic:
        """Новая тема — или та же самая, если модель назвала её иначе.

        «Обновление ОС» и «Обновление операционной системы» — одна тема; заводить
        под неё второй якорь значит разорвать нить пополам ровно там, где человек
        следит за одной мыслью.
        """
        title = title.strip()
        if self.topics and _same_title(self.topics[-1].title, title):
            return self.topics[-1]
        topic = Topic(title=title, at=at)
        self.topics.append(topic)
        return topic

    def add(self, kind: str, text: str, at: str = "") -> bool:
        """Строка в текущую тему. False — если это повтор уже сказанного."""
        text = text.strip()
        if not text:
            return False
        if not self.topics:
            self.open_topic("Разговор", at)
        topic = self.topics[-1]
        if self.knows(text):
            return False
        topic.lines.append(Line(kind=kind, text=text, at=at))
        return True

    def knows(self, text: str) -> bool:
        """Уже есть в нити? Сравниваем со всеми строками, а не только с
        последними: модель возвращается к теме через десять минут и повторяет
        вывод, к которому уже приходила."""
        probe = _norm(text)
        if not probe:
            return True
        for topic in self.topics:
            for line in topic.lines:
                if _same_norm(probe, _norm(line.text)):
                    return True
        return False

    # --- обмен с моделью ----------------------------------------------------

    def ingest(self, answer: str, at: str = "") -> int:
        """Разобрать ответ модели и дописать новое. Возвращает число строк.

        Ответ читается построчно по ведущему знаку. Всё, что знака не имеет,
        отбрасывается: модель любит добавить «Вот что нового:», и такие строки
        в нити выглядят как реплика участника.
        """
        added = 0
        for raw in answer.splitlines():
            line = raw.strip()
            if not line or line.upper().startswith("NONE"):
                continue
            mark, _, rest = line.partition(" ")
            rest = rest.strip(" *")
            if not rest:
                continue
            if mark == TOPIC:
                self.open_topic(rest, at)
                added += 1
            elif mark in KINDS:
                added += 1 if self.add(KINDS[mark], rest, at) else 0
        return added

    def add_archive(self, topic_title: str, lines: list[str]) -> int:
        """Строки «что было раньше» (⏮) — в названную тему, не в хвост нити.

        Разбор просят по конкретной теме; если модель успела открыть новую,
        дописывать архив в неё значило бы приклеить прошлое чужой темы.
        Темы нет в нити — открываем её: просьба «что было по X» сама по себе
        делает X темой разговора. Дедуп тот же, что у обычных строк.
        """
        topic_title = topic_title.strip()
        topic = next((t for t in reversed(self.topics)
                      if _same_title(t.title, topic_title)), None)
        if topic is None:
            topic = self.open_topic(topic_title or "Разговор")
        added = 0
        for text in lines:
            text = text.strip()
            if not text or self.knows(text):
                continue
            topic.lines.append(Line(kind="archive", text=text))
            added += 1
        return added

    @property
    def last_topic_title(self) -> str:
        return self.topics[-1].title if self.topics else ""

    def as_context(self, topics: int = 2) -> str:
        """Что показать модели как «уже собрано».

        Не вся нить: длинная встреча выест контекст, а дописывать надо к концу.
        Последние темы целиком — этого хватает, чтобы не повторяться.
        """
        return "\n".join(t.render() for t in self.topics[-topics:])

    # --- показ --------------------------------------------------------------

    def render(self) -> str:
        """Нить для экрана: последние темы целиком, ранние — свёрнуты."""
        if not self.topics:
            return ""
        cut = max(0, len(self.topics) - self.live_topics)
        parts = [t.render(full=False) for t in self.topics[:cut]]
        parts += [t.render(full=True) for t in self.topics[cut:]]
        return "\n\n".join(p for p in parts if p)

    def full(self) -> str:
        """Нить целиком — для файла встречи, там сворачивать нечего."""
        return "\n\n".join(t.render(full=True) for t in self.topics)

    @property
    def size(self) -> int:
        return sum(len(t.lines) for t in self.topics)


def _same_title(a: str, b: str) -> bool:
    """Один ли это якорь нити.

    Короткие заголовки нельзя мерить той же меркой, что реплики: на длине в
    шесть букв один изменённый знак даёт похожесть 0.83, и две разные темы
    становятся одной. Поэтому: точное совпадение или вхождение — всегда; нечёткое
    сравнение — только на заголовках, где ему есть за что зацепиться.
    """
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y or x in y or y in x:
        return True
    if min(len(x), len(y)) < TITLE_MIN_LEN:
        return False
    return difflib.SequenceMatcher(None, x, y).ratio() >= SAME_TITLE


def _same_norm(a: str, b: str) -> bool:
    """Одна ли это мысль, сказанная дважды.

    Нечёткое сравнение включается только там, где строке есть чем отличаться.
    На коротких фразах посимвольная похожесть врёт в обе стороны: «поток упал»
    и «поток встал» — 0.85, то есть по общему порогу это «повтор», хотя смысл
    противоположный.

    Посимвольного сравнения мало: на длинной встрече модель возвращается к
    мысли и пересказывает её другим порядком слов — «сообщил указание перейти
    на 1.8 до конца года» против «сообщил требование сверху переключиться на
    версию 1.8 до конца года». Для difflib это разные строки, для человека —
    одна и та же, прочитанная дважды. Поэтому вторым заходом сверяем по словам.
    """
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    if min(len(a), len(b)) < LINE_MIN_LEN:
        return False
    if difflib.SequenceMatcher(None, a, b).ratio() >= SAME_ENOUGH:
        return True
    return _same_words(a, b)


# Служебные слова не несут смысла и раздувают пересечение: без них «сообщил
# требование» и «сообщил указание» разойдутся, а с ними — сольются.
_STOP = {"и", "в", "на", "с", "по", "не", "что", "как", "для", "до", "из", "за",
         "то", "же", "бы", "ли", "или", "а", "но", "у", "о", "об", "при", "от"}


def _same_words(a: str, b: str, ratio: float = 0.72) -> bool:
    """Пересечение значимых слов — от короткой строки."""
    wa = {w for w in a.split() if len(w) > 2 and w not in _STOP}
    wb = {w for w in b.split() if len(w) > 2 and w not in _STOP}
    if len(wa) < 4 or len(wb) < 4:
        return False          # на трёх словах совпадение случайно
    return len(wa & wb) / min(len(wa), len(wb)) >= ratio
