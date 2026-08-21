"""Что считать вопросом, ради которого стоит будить модель.

Живая стенограмма режется на реплики по паузам, и STT щедро ставит «?» по
интонации. В панель 04.08 приходило подряд: «Что?», «С какого бы?», «Дром
ирир? Да.» — на каждый обрывок уходил вызов локальной модели И облачной, а
в ответ приходило «не вижу вопроса, уточните» на четыре строки. Полотно
разговора тонуло в этой воде, облако жгло деньги на пустоту.

Здесь только структурные проверки — длина, повтор, доля значимых слов.
Никаких списков «плохих» фраз: они устаревают на первой же встрече, а
правило проекта прямо запрещает хардкод паттернов вместо понимания.
"""

from __future__ import annotations

import difflib
import re

# GigaAM's neural punctuation is the primary signal: a question mark may sit
# in the middle of a live utterance, because the speaker keeps talking after
# the question.  Opening words are a conservative fallback when punctuation
# is missing.  This check deliberately stays local and deterministic: it runs
# in the sole STT consumer and may never wait for Ollama or the network.
_QUESTION_START = {
    "как", "что", "чем", "почему", "зачем", "сколько", "когда", "кто",
    "куда", "где", "какой", "какая", "какие", "каким", "какую", "расскажи",
    "расскажите", "объясни", "объясните", "опиши", "опишите", "поясни",
    "поясните", "можешь", "можете",
}
_QUESTION_PAIRS = {
    "есть ли", "правда ли", "верно ли", "был ли", "будет ли", "а вы", "а ты",
}


def looks_question(text: str) -> bool:
    """Fast, fail-open question candidate check for the live STT path.

    A false positive only schedules a hint that ``is_worth_asking`` may still
    reject.  A model call here is worse: a busy local model stalls recognition
    for every ambiguous utterance and lets the audio backlog grow.
    """
    if "?" in text:
        return True
    words = text.strip().lower().split()
    if not words:
        return False
    return words[0] in _QUESTION_START or " ".join(words[:2]) in _QUESTION_PAIRS

# Короче трёх значимых слов вопрос не несёт предмета: «Что?», «А он?»,
# «С какого бы?» — это переспрос внутри чужой реплики, а не запрос к нам.
MIN_MEANINGFUL = 3

# Слова-склейки живой речи. Не «чёрный список тем», а служебная часть речи:
# она не добавляет вопросу предмета, сколько её ни повторяй.
_GLUE = {
    "а", "и", "но", "же", "ли", "бы", "то", "вот", "ну", "как", "так", "уж",
    "это", "эт", "там", "тут", "здесь", "да", "нет", "не", "ни", "что", "чё",
    "кто", "где", "когда", "куда", "чем", "чём", "кого", "кому", "чего",
    "он", "она", "оно", "они", "мы", "вы", "я", "ты", "их", "его", "её",
    "у", "в", "с", "к", "о", "об", "на", "по", "за", "из", "до", "от", "при",
}


def meaningful_words(text: str) -> list[str]:
    """Слова, которые несут предмет вопроса."""
    words = re.findall(r"[\w-]+", text.lower())
    return [w for w in words if len(w) > 2 and w not in _GLUE]


def is_worth_asking(text: str, previous: str = "") -> bool:
    """Стоит ли будить модель ради этой реплики.

    Отсекаем три случая, на которых модель заведомо ответит пустотой:
    предмета нет (обрывок), это повтор только что заданного вопроса,
    реплика пустая.
    """
    text = " ".join(text.split())
    if not text:
        return False
    if len(meaningful_words(text)) < MIN_MEANINGFUL:
        return False
    if previous:
        a, b = text.lower(), " ".join(previous.split()).lower()
        if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85:
            return False
    return True


# Модель, не увидев вопроса, отвечает связным абзацем «уточните, пожалуйста».
# Это не ответ, а вежливый отказ: в нить он не идёт. Ловим по смысловой
# связке «не вижу/не понял + вопрос», а не по конкретной формулировке.
_REFUSAL = re.compile(
    r"(не\s+вижу|не\s+нахожу|не\s+понял|неясен|не\s+ясен|не\s+могу\s+определить)"
    r"[^.]{0,60}(вопрос|запрос)"
    r"|(уточните|переформулируйте|повторите)[^.]{0,40}вопрос",
    re.I,
)


def is_refusal(answer: str) -> bool:
    """Ответ, в котором модель призналась, что вопроса не нашла."""
    return bool(_REFUSAL.search(answer or ""))


def squeeze(answer: str, max_lines: int = 2, max_chars: int = 260) -> str:
    """Ответ для нити: суть без разгона.

    В полотне ответ соседствует с репликами разговора, и абзац на десять
    строк рвёт чтение сильнее, чем отсутствие ответа. Два предложения —
    это ровно «что ответить вслух»; продолжение вроде «дальше идут детали
    по бюджету и регламенту» на встрече не читают, а в файл подсказок
    ответ всё равно ложится целиком.
    """
    text = " ".join((answer or "").split())
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    for p in parts:
        if len(out) >= max_lines:
            break
        if sum(len(x) for x in out) + len(p) > max_chars and out:
            break
        out.append(p)
    return " ".join(out)
