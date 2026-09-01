"""Канон написаний из графа: фамилии и аббревиатуры STT приводятся к узлам.

За одно утро одна фамилия прожила четыре жизни: «Вельский» (канон),
«Ельский» (имя узла до сверки), «Гельского» (STT в стенограмме — из-за
этого потерялось единственное именное поручение встречи), «Мельский».
Узел графа при этом ЗНАЛ девять вариантов в `aliases:` — не хватало только
кода, который применит это знание к тексту.

Правила (решение Антона 01.09 «проверка при сборке, похожее — менять»):
- Канон — ТОЛЬКО граф: имя узла Люди/Системы и его `aliases:` во
  frontmatter. Никакого отдельного словаря: справочник правится там же,
  где живёт узел, и виден человеку.
- Замена — детерминированная и только по подтверждённым алиасам: основа
  алиаса меняется на основу канона, падежное окончание сохраняется
  («Гельск-ого» → «Вельск-ого»). Аббревиатуры — целым словом с
  регистром канона («крам» → «КРАМ»).
- «Похожее, но не алиас» — НЕ заменяется: слово с Заглавной на редакции
  ≤2 от канона уходит в отчёт-кандидаты; если контекст вокруг него
  пересекается с узлом (поле `отдел:` или тело — «Вельский + 2-я
  линия»), кандидат помечен уверенным — человек подтверждает алиас одним
  движением. Автозапись в узел — после обкатки, не в этой версии.
- LLM к правке терминов не подпускается; фонетики в первой версии нет.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

import frontmatter

#: Каталоги графа, чьи узлы дают канон. Встречи/Досье — потребители, не источник.
SOURCE_DIRS = ("Люди", "Системы")

#: Окончания фамилий/прилагательных: срезаются у алиаса и канона, чтобы
#: заменить основу и сохранить падеж («…ский/…ского/…скому» — одна основа).
_ENDINGS = ("ский", "ского", "скому", "ским", "ском", "ская", "ской", "скую",
            "ий", "ый", "ой", "его", "ого", "ему", "ому", "им", "ым", "ом",
            "ая", "яя", "ую", "юю", "а", "я", "у", "ю", "е", "и", "ы", "й")


def _stem_name(word: str) -> str:
    """Основа склоняемого слова-имени: без окончания, минимум 4 буквы."""
    w = word.lower()
    for e in _ENDINGS:
        if len(w) - len(e) >= 4 and w.endswith(e):
            return w[: -len(e)]
    return w


def _is_abbrev(word: str) -> bool:
    """Аббревиатура: 3–6 букв капсом (СДПР, РДС, БМПЛ). Двухбуквенные —
    слишком опасны: узел «ВО» на живом смоуке канонизировал предлог «во»
    по всей стенограмме."""
    return 3 <= len(word) <= 6 and word.isupper() and word.isalpha()


@dataclasses.dataclass
class Rule:
    canon: str        # каноническое слово как в узле («Вельский», «КРАМ»)
    node: str         # имя узла-источника (для отчёта)
    stem: str         # основа канона для склоняемых; для аббревиатур = слово
    abbrev: bool


@dataclasses.dataclass
class Lexicon:
    #: alias-основа → правило (склоняемые) / alias-слово lower → правило (аббревиатуры)
    by_stem: dict[str, Rule]
    by_word: dict[str, Rule]
    #: канон-слово → (узел, слова контекста: `отдел:` + тело) — для кандидатов
    context: dict[str, tuple[str, set[str]]]

    def empty(self) -> bool:
        return not (self.by_stem or self.by_word)


def _ctx_stem(w: str) -> str:
    """Грубая основа для сверки контекста: «мониторингу/мониторинг»,
    «второй/вторая», «линии/линия» должны совпадать."""
    base = w.rstrip("аеёиоуыэюяй")
    return base if len(base) >= 4 else w


def _context_words(text: str) -> set[str]:
    return {_ctx_stem(w) for w in re.findall(r"[а-яёa-z0-9-]{4,}", text.lower())}


def load(graph_root: pathlib.Path) -> Lexicon:
    """Собрать канон из узлов графа. Ошибки чтения молча пропускают узел:
    лексикон — улучшатель, он не смеет ронять пересборку."""
    by_stem: dict[str, Rule] = {}
    by_word: dict[str, Rule] = {}
    context: dict[str, tuple[str, set[str]]] = {}
    for d in SOURCE_DIRS:
        base = graph_root / d
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.md")):
            if p.name.startswith("_"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            node = p.stem
            data = frontmatter.parse(text, p.name) or {}
            dept = str(data.get("отдел") or "")
            aliases = frontmatter.aliases(text, p.name)
            canon_words = [w for w in re.findall(r"[\w-]+", node) if len(w) >= 2]
            # ПРАВИЛА ЗАМЕН — только фамилии (узлы Люди/) и капс-аббревиатуры.
            # Тематические узлы Систем («Проблема с хостами» с алиасом
            # «хосты») на первом же живом смоуке породили «хост→хостам» по
            # всей стенограмме — нарицательные в замены не допускаются,
            # им остаётся роль контекста для кандидатов.
            person = d == "Люди"
            # каноны-персоны и аббревиатуры — в контекст кандидатов
            for cw in canon_words:
                if person and cw[:1].isupper() and len(cw) >= 4 or _is_abbrev(cw):
                    context[cw.lower()] = (node, _context_words(dept + " " + text[:1500]))
            for alias in aliases:
                a_words = alias.split()
                if len(a_words) != 1:
                    continue          # многословные алиасы — не в этой версии
                aw = a_words[0]
                # цель замены: капс-аббревиатура узла ЛИБО (для персон)
                # ФАМИЛИЯ — слово имени узла с самой длинной основой
                # («Вельский Ян» → «Вельский», «Анна Николаева» →
                # «Николаева»). Префикс-сверку алиаса с целью не делаем:
                # алиас в узле уже подтверждён, а настоящие STT-искажения
                # расходятся со второй буквы (Гельский/Вельский —
                # живой смоук это и поймал).
                target = next((cw for cw in canon_words if _is_abbrev(cw)
                               and aw.lower() == cw.lower()), None)
                if target is None and person and aw[:1].isupper():
                    # Цель — слово имени узла с максимальным ОБЩИМ СУФФИКСОМ
                    # основ (порог 3): настоящие STT-искажения фамилии делят
                    # хвост («гельск/вельск» → «ельск», «ельск/вельск» →
                    # «ельск»), а имя с фамилией суффикса не делят — «Марк»
                    # не станет «Ветровым», а фамилия — именем
                    # (живой смоук поймал обе пары). Нет суффикса — алиас
                    # остаётся поисковым синонимом узла без замены.
                    a_st = _stem_name(aw)
                    best, best_len = None, 2
                    for cw in canon_words:
                        if not cw[:1].isupper() or len(_stem_name(cw)) < 4:
                            continue
                        c_st = _stem_name(cw)
                        n = 0
                        while (n < min(len(a_st), len(c_st))
                               and a_st[-1 - n] == c_st[-1 - n]):
                            n += 1
                        if n > best_len:
                            best, best_len = cw, n
                    target = best
                if not target or aw.lower() == target.lower():
                    continue
                if _is_abbrev(target):
                    by_word[aw.lower()] = Rule(target, node, target, True)
                elif aw[:1].isupper():   # алиас-фамилия пишется с Заглавной
                    st = _stem_name(aw)
                    if len(st) >= 4 and st != _stem_name(target):
                        by_stem[st] = Rule(target, node, _stem_name(target), False)
            # аббревиатура-узел канонизирует и своё же строчное написание
            for cw in canon_words:
                if _is_abbrev(cw):
                    by_word.setdefault(cw.lower(), Rule(cw, node, cw, True))
    return Lexicon(by_stem, by_word, context)


def apply(text: str, lex: Lexicon) -> tuple[str, list[str]]:
    """Привести текст к канону. Возвращает (текст, список замен «было→стало»)."""
    if lex.empty():
        return text, []
    replaced: list[str] = []

    def fix_word(m: re.Match[str]) -> str:
        w = m.group(0)
        low = w.lower()
        rule = lex.by_word.get(low)
        if rule and w != rule.canon:
            replaced.append(f"{w}→{rule.canon}")
            return rule.canon
        st = _stem_name(low)
        rule = lex.by_stem.get(st)
        if rule and not rule.abbrev:
            ending = low[len(st):]
            fixed = rule.stem + ending
            # регистр как у оригинала: имена в тексте с Заглавной
            fixed = fixed.capitalize() if w[:1].isupper() else fixed
            if fixed != w:
                replaced.append(f"{w}→{fixed}")
                return fixed
        return w

    out = re.sub(r"[\w-]+", fix_word, text)
    return out, replaced


def candidates(text: str, lex: Lexicon, window: int = 30) -> list[str]:
    """Похожие на канон слова, которых нет в алиасах, — строки отчёта.

    Уверенность даёт контекст (дополнение Антона 01.09): слова вокруг
    кандидата пересекаются с полем `отдел:`/телом узла — помечаем ✔.
    """
    words = re.findall(r"[\w-]+", text)
    lows = [w.lower() for w in words]
    out: list[str] = []
    seen: set[str] = set()
    for i, w in enumerate(words):
        if not w[:1].isupper() or len(w) < 6 or w.lower() in seen:
            continue
        st = _stem_name(w.lower())
        if st in lex.by_stem:
            continue                    # уже алиас — заменится
        for cl, (node, ctx) in lex.context.items():
            if len(cl) < 6 or abs(len(cl) - len(w)) > 2:
                continue
            if _distance(w.lower(), cl) <= 2 and w.lower() != cl:
                around = {_ctx_stem(x) for x in lows[max(0, i - window):i + window]
                          if len(x) >= 4}
                sure = len(around & ctx) >= 2
                mark = "✔ контекст" if sure else "?"
                out.append(f"- {w} ~ {cl} (узел {node}) {mark}")
                seen.add(w.lower())
                break
    return out


def _distance(a: str, b: str) -> int:
    """Левенштейн без зависимостей: строки короткие, вызовов немного."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
