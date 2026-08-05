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
import threading
from dataclasses import dataclass, field

# Знаки строк. Один символ вместо слова: на встрече читают боковым зрением, и
# «⚑» отличается от «-» быстрее, чем «Решение:» от «Обсуждение:».
TOPIC = "●"
SAY = "-"
DECISION = "⚑"
QUESTION = "?"
ARCHIVE = "⏮"
THOUGHT = "💭"
ANSWER = "⚡"

KINDS = {SAY: "say", DECISION: "decision", QUESTION: "question",
         ARCHIVE: "archive", THOUGHT: "thought", ANSWER: "answer"}

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


# Служебные префиксы разбора — не имена: «Почему: …» это поле, не голос.
_NOT_SPEAKERS = {"почему", "было", "стало", "открыто", "решили", "итог",
                 "вывод", "важно", "уточнение", "why", "was", "open", "decided"}
_SPEAKER_RE = re.compile(r"^([^:—-]{1,32}?):\s+(.+)$", re.S)


def split_speaker(text: str) -> tuple[str, str]:
    """«Собеседник 4: поток упал» → («Собеседник 4», «поток упал»).

    Имя — короткий префикс до двоеточия, максимум три слова; служебные
    поля («Почему:», «Открыто:») именем не считаются. Всё остальное —
    текст без изменений: дедупу и правкам облака имя только мешало.
    """
    m = _SPEAKER_RE.match(text.strip())
    if not m:
        return "", text.strip()
    name, rest = m.group(1).strip(), m.group(2).strip()
    if not name or len(name.split()) > 3:
        return "", text.strip()
    if name.lower() in _NOT_SPEAKERS:
        return "", text.strip()
    return name, rest


def _norm(text: str) -> str:
    """Строка для сравнения: без разметки, регистра и пунктуации."""
    text = re.sub(r"[*_`]", "", text.lower())
    return re.sub(r"[^\w\s]", " ", text).strip()


@dataclass
class Line:
    kind: str
    text: str
    at: str = ""
    speaker: str = ""

    def render(self, show_speaker: bool = True) -> str:
        mark = next((m for m, k in KINDS.items() if k == self.kind), SAY)
        pad = "    " if self.kind == "say" else "  "
        stamp = f"    {self.at}" if self.at and self.kind == "decision" else ""
        head = f"{self.speaker}: " if self.speaker and show_speaker else ""
        return f"{pad}{mark} {head}{self.text}{stamp}"


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
        # Имя говорящего — только при смене голоса. «Собеседник 4:» на каждой
        # строке читается как протокол допроса; разговор идёт поступательно,
        # и глазу хватает имени в момент передачи слова.
        parts = [head]
        prev_speaker = None
        for ln in self.lines:
            if ln.kind == "say" and ln.speaker:
                parts.append(ln.render(show_speaker=ln.speaker != prev_speaker))
                prev_speaker = ln.speaker
            else:
                parts.append(ln.render())
        return "\n".join(parts)


class Thread:
    """Живая нить встречи. Растёт, не переписывается.

    Потокобезопасна: в нить пишут два потока демона — thread_loop
    (ingest по приросту разговора) и expand_topic (⏮ по клавише), — и оба
    вне hint_lock: тот держится минутами при генерации минуток, и разбор
    темы под ним ждал бы их конца. Свой RLock дешевле и точнее: без него
    два потока, одновременно прошедшие knows(), протаскивали дубль, а
    render() читал темы посреди чужого open_topic.
    """

    def __init__(self, live_topics: int = LIVE_TOPICS) -> None:
        self.topics: list[Topic] = []
        self.live_topics = live_topics
        self._mutex = threading.RLock()

    # --- наполнение ---------------------------------------------------------

    def open_topic(self, title: str, at: str = "") -> Topic:
        """Новая тема — или та же самая, если модель назвала её иначе.

        «Обновление ОС» и «Обновление операционной системы» — одна тема; заводить
        под неё второй якорь значит разорвать нить пополам ровно там, где человек
        следит за одной мыслью.
        """
        title = title.strip()
        with self._mutex:
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
        speaker = ""
        if kind == "say":
            speaker, text = split_speaker(text)
        with self._mutex:
            if not self.topics:
                self.open_topic("Разговор", at)
            topic = self.topics[-1]
            if self.knows(text):
                return False
            topic.lines.append(Line(kind=kind, text=text, at=at, speaker=speaker))
            return True

    def knows(self, text: str) -> bool:
        """Уже есть в нити? Сравниваем со всеми строками, а не только с
        последними: модель возвращается к теме через десять минут и повторяет
        вывод, к которому уже приходила."""
        probe = _norm(text)
        if not probe:
            return True
        with self._mutex:
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
        # Весь разбор под одним захватом: ответ модели должен лечь в нить
        # целиком, а не вперемешку со строками параллельного ⏮-разбора.
        with self._mutex:
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

    def add_answer(self, question: str, answer: str) -> bool:
        """Вопрос и ответ — двумя строками текущей темы, а не отдельной лентой.

        До этого ответы жили в своей панели и перекрывали нить: автоответ
        приходил раз в полминуты, панель не пустела никогда, и полотно
        разговора человек не видел вообще. Теперь ответ — такая же строка
        нити, как реплика: «? вопрос» и под ней «⚡ ответ».
        """
        question = " ".join(question.split())
        answer = " ".join(answer.split())
        if not answer:
            return False
        with self._mutex:
            if question and not self.knows(question):
                self.add("question", question)
            return self.add("answer", answer)

    def add_thesis(self, line: str) -> bool:
        """Тезис 📌/💭 — строкой нити, чтобы читать одно полотно, а не два.

        Знак сохраняется: 📌 остаётся контрольной точкой (в нити это
        решение — тот же вес), 💭 — мыслью модели.
        """
        line = line.strip()
        if not line:
            return False
        mark, _, rest = line.partition(" ")
        rest = rest.strip(" *")
        if not rest:
            return False
        kind = "decision" if mark == "📌" else "thought"
        return self.add(kind, rest)

    def add_archive(self, topic_title: str, lines: list[str]) -> int:
        """Строки «что было раньше» (⏮) — в названную тему, не в хвост нити.

        Разбор просят по конкретной теме; если модель успела открыть новую,
        дописывать архив в неё значило бы приклеить прошлое чужой темы.
        Темы нет в нити — открываем её: просьба «что было по X» сама по себе
        делает X темой разговора. Дедуп тот же, что у обычных строк.
        """
        topic_title = topic_title.strip()
        with self._mutex:
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

    def apply_edits(self, edits: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Правки облака ложатся В строки нити, а не отдельным блоком под ней.

        Отдельный блок «☁️ уточнения» человек должен был сам сопоставить с
        нитью — двойное чтение во время живого разговора. Теперь облако
        возвращает пары «строка → как точнее», строка правится на месте, а
        изменённые слова выделены ==так==: видно, ЧТО именно поменяла модель,
        не выходя из полотна. Возвращает применённые пары (для файла-лога:
        аудит «было → стало» живёт там, не на экране).
        """
        applied: list[tuple[str, str]] = []
        with self._mutex:
            for old, new in edits:
                _, old_body = split_speaker(old.strip().lstrip("-⚑?⏮💭⚡● "))
                new_speaker, new_body = split_speaker(new.strip().lstrip("-⚑?⏮💭⚡● "))
                probe = _norm(old_body)
                if not probe or not new_body:
                    continue
                line = self._find_line(probe)
                if line is None or _norm(line.text) == _norm(new_body):
                    continue
                was = line.render(show_speaker=bool(line.speaker)).strip()
                line.text = _mark_diff(line.text, new_body)
                if new_speaker and not line.speaker:
                    line.speaker = new_speaker
                applied.append((was, line.text))
        return applied

    def _find_line(self, probe: str) -> Line | None:
        """Строка нити под правку: ищем с конца — свежее правится чаще."""
        for topic in reversed(self.topics):
            for line in reversed(topic.lines):
                clean = _norm(re.sub(r"==", "", line.text))
                if _same_norm(probe, clean):
                    return line
        return None

    @property
    def last_topic_title(self) -> str:
        with self._mutex:
            return self.topics[-1].title if self.topics else ""

    def as_context(self, topics: int = 2) -> str:
        """Что показать модели как «уже собрано».

        Не вся нить: длинная встреча выест контекст, а дописывать надо к концу.
        Последние темы целиком — этого хватает, чтобы не повторяться.
        """
        with self._mutex:
            return "\n".join(t.render() for t in self.topics[-topics:])

    # --- показ --------------------------------------------------------------

    def render(self) -> str:
        """Нить для экрана: последние темы целиком, ранние — свёрнуты."""
        with self._mutex:
            if not self.topics:
                return ""
            cut = max(0, len(self.topics) - self.live_topics)
            parts = [t.render(full=False) for t in self.topics[:cut]]
            parts += [t.render(full=True) for t in self.topics[cut:]]
            return "\n\n".join(p for p in parts if p)

    def full(self) -> str:
        """Нить целиком — для файла встречи, там сворачивать нечего."""
        with self._mutex:
            return "\n\n".join(t.render(full=True) for t in self.topics)

    @property
    def size(self) -> int:
        with self._mutex:
            return sum(len(t.lines) for t in self.topics)


def parse_edits(out: str, limit: int = 4) -> list[tuple[str, str]]:
    """Ответ облака-ревизора → пары (старая строка, новая строка).

    Формат одной правки: «FIX: <старая> => <новая>». Всё, что не легло в
    формат, отбрасывается молча: ревизор, как и остальные модели, любит
    преамбулы. NONE — «всё точно», и это нормальный ответ.
    """
    edits: list[tuple[str, str]] = []
    for raw in out.splitlines():
        line = raw.strip().lstrip("-• ")
        if not line or line.upper().startswith("NONE"):
            continue
        if line.upper().startswith("FIX:"):
            line = line[4:].strip()
        old, sep, new = line.partition(" => ")
        if not sep:
            continue
        old, new = old.strip(), new.strip()
        if old and new:
            edits.append((old, new))
    return edits[:limit]


def _mark_diff(old: str, new: str) -> str:
    """Новая строка с ==выделением== того, что изменилось против старой.

    Сравнение по словам: посимвольный diff на живом тексте рвёт слова
    пополам. Выделение несёт смысл «это внесло облако», поэтому одинаковые
    куски остаются как были — глаз ловит только вставки и замены.
    """
    old_words = re.sub(r"==", "", old).split()
    new_words = new.split()
    sm = difflib.SequenceMatcher(None,
                                 [w.lower() for w in old_words],
                                 [w.lower() for w in new_words])
    out: list[str] = []
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if j1 == j2:
            continue
        chunk = " ".join(new_words[j1:j2])
        out.append(chunk if op == "equal" else f"=={chunk}==")
    marked = " ".join(out)
    # слившиеся соседние выделения — в одно, чтобы не рябило
    return marked.replace("== ==", " ")


def parse_archive_facts(out: str, limit: int = 3) -> list[str]:
    """Ответ модели ⏮-разбора → чистые строки фактов.

    Модель, воспитанная на NONE-протоколах, отвечает NONE и здесь — такую
    строку нельзя класть в нить как факт прошлых встреч. Маркеры списков
    срезаются, пустое отбрасывается, больше limit не берём: ⏮ — это
    2-3 хвоста по теме, а не второй архив.
    """
    lines = (ln.strip(" -•*\t") for ln in out.splitlines())
    return [ln for ln in lines
            if ln and not ln.upper().startswith("NONE")][:limit]


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
