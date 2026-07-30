"""Единственное место, где решается, можно ли доверять опознанному имени.

Опознание работает в двух режимах: с моделью голосов («Собеседник 1/2/…») и
без неё, по канальным меткам. Проверки доверия писались отдельно для каждого,
и разошлись: гвард против «Саш, ну а кто…» (обращение принималось за имя
говорящего) появился только в мультиспикерном режиме, а безмодельный —
поведение ПО УМОЛЧАНИЮ, потому что ERes2Net в поставку не входит, — остался с
одним промптом к лёгкой модели.

Цена ошибки здесь выше цены пропуска: `rename_speaker` переписывает метку
задним числом по всей встрече, минутки и граф наследуют её молча, поручение
уходит не тому человеку. «Собеседник 2» честен, неверное имя — врёт.

Правила, которые обязаны действовать в обоих режимах:

    1. Имя владельца не достаётся собеседнику. Сравнение — по СЛОВАМ
       `user_name`, а не со всей строкой: в конфиге просят «ваше имя», и там
       обычно стоит имя с фамилией.
    2. Имя, которое звучит только в репликах самой метки и не является
       представлением, — это обращение к кому-то другому, а не имя говорящего.
    3. Имени, которого нет в стенограмме, не существует: модель его выдумала.
    4. Падежи приводятся к известным людям графа («Полин» → «Полина»), чтобы
       в графе не появился узел в звательном падеже.

Модуль намеренно без зависимостей: чистые строки, поэтому проверяется тестами
без звука, PortAudio и запущенной модели.
"""
from __future__ import annotations

import re

# Планка длины. Ниже трёх — мусор от лёгкой модели («Ок», «Да»); выше
# пятнадцати — не имя, а склеенная фраза. Безмодельный режим раньше пускал
# двухбуквенные, и это была единственная разница между ветками не в пользу
# осторожности.
MIN_LEN = 3
MAX_LEN = 15

# По этому префиксу имя из разговора склеивается с известным человеком графа:
# «Полин» → «Полина», «Андрюх» → «Андрей». Тем же префиксом узнаётся владелец,
# названный уменьшительно.
PREFIX = 4

# Самопредставление: только оно оправдывает имя, прозвучавшее исключительно в
# собственных репликах говорящего.
_INTRO = r"(это|я|меня\s+зовут)\s+"


def _clean(raw: str) -> str:
    """Обрезка пунктуации и кавычек, единый регистр имени."""
    return str(raw or "").strip().strip(".,!?:;«»\"'()").capitalize()


def _words(full_name: str) -> list[str]:
    return [w for w in re.split(r"[\s,]+", (full_name or "").casefold()) if w]


def is_owner(name: str, owner_name: str) -> bool:
    """Это владелец под другим написанием?

    Сравниваем со всеми словами `sufler.user_name`: «Игорь» — это «Игорь
    Ветров», а не новый участник встречи. Уменьшительные ловим префиксом — то
    же правило, которым «Полин» приводится к «Полина», только с обратным
    знаком: похоже на владельца — не присваиваем никому.
    """
    if not name or not owner_name:
        return False
    low = name.casefold()
    for word in _words(owner_name):
        if low == word:
            return True
        short = min(PREFIX, len(low), len(word))
        if short >= PREFIX and low[:short] == word[:short]:
            return True
    return False


def _own_lines_only(name: str, sample: str, label: str) -> bool:
    """Имя звучит ТОЛЬКО в репликах самой метки и это не представление.

    Формат хвоста стенограммы — «[ЧЧ:ММ] метка: текст», метка не в начале
    строки, поэтому ищем «] метка:», а не `startswith`.
    """
    low = name.casefold()
    lines_with = [ln for ln in sample.splitlines() if low in ln.casefold()]
    if not lines_with:
        return False
    own = [ln for ln in lines_with if re.search(rf"\]\s*{re.escape(label)}\s*:", ln)]
    if len(own) != len(lines_with):
        return False    # имя звучало и с другой стороны — законный источник
    return not re.search(_INTRO + re.escape(name), sample, re.I)


def trustworthy_name(raw: str, *, sample: str, label: str,
                     owner_name: str = "", known: tuple[str, ...] | list[str] = (),
                     ) -> str | None:
    """Имя, которому можно доверять, или None — с одинаковой строгостью в
    обоих режимах опознания.

    raw    — что предложила модель (может быть мусором и «NONE»)
    sample — хвост стенограммы, по которому она решала
    label  — метка говорящего, которую собираемся заменить
    owner_name — `sufler.user_name`, целиком, как в конфиге
    known  — имена людей графа для приведения падежей
    """
    name = _clean(raw)
    if not name or name.upper() == "NONE":
        return None
    if not name.replace("-", "").isalpha():
        return None
    if not (MIN_LEN <= len(name) <= MAX_LEN):
        return None
    if name.casefold() == label.casefold() or name.casefold().startswith("собеседник"):
        return None
    if name.casefold() not in sample.casefold():
        return None    # модель выдумала имя, которого в разговоре не было

    # падежи — по известным людям графа, до проверки владельца: «Игорёк» из
    # разговора должен сначала стать «Игорь», чтобы владелец узнался.
    if known and name not in known:
        low = name.casefold()
        hit = [k for k in known if k.casefold().startswith(low[:PREFIX])]
        if len(hit) == 1:
            name = hit[0]

    if is_owner(name, owner_name):
        return None
    if _own_lines_only(name, sample, label):
        return None
    return name
