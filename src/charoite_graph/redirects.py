"""Заглушки-редиректы после слияния узлов — один детектор для всех слоёв.

После слияния дубль остаётся файлом «`# Имя → [[Папка/Канон]]` … Дубль.
Смерджен» — так пишет tier3. Облако помечает свои слияния своими словами
(«⚠️ **Дубль слит.**»), и три независимые проверки буквальной строки
«Дубль. Смерджен» (dossier.scan, tier3.load_cores, graph_updater.
resolve_core_path) такие заглушки принимали за живые узлы: досье собирало
кластер вокруг мёртвого дубля, а свежий «## Статус» мог уехать в заглушку
(аудит графа 28.08, Sonnet Important 5). cloud_review узнавал заглушки по
СТРУКТУРЕ — первому заголовку со стрелкой; теперь так узнают все.

Ручные ревизии писали заглушки ещё десятком форм — «# X → см. [[…]]»,
«➡️ Сведён в [[…]]», «Дубль. Канонический узел — [[…]]» — и детектор их не
узнавал: ночная ревизия судила мёртвые ядра как живые, а разбор встреч
дописывал в них хронику (замер №777 по 14 графам, 23.10: 258 таких файлов,
10 выросли больше 4000 знаков). Читаем все исторические формы (`stub_info`),
пишем одну (`is_redirect_stub` — ворота записи облака — остаётся узким).
"""
from __future__ import annotations

import dataclasses
import re

from charoite_graph import frontmatter

MERGED_MARKER = "Дубль. Смерджен"
MERGED_TAG = "дубль-слит"
REDIRECT_RE = re.compile(r"^# .+? (?:→|->|⇒) \[\[[^\]]+\]\]", re.M)
STUB_TARGET_RE = re.compile(r"(?:→|->|⇒) \[\[([^\]]+)\]\]")
STUB_MAX = 4000     # тело «чистого указателя»; длиннее — узел с телом

_LINK = r"\[\[[^\]]+\]\]"
_DECOR = r"(?:\s|⛔|⚠️|⚠|🔴|\*\*)*"
_ARROW = r"(?:→|->|⇒|➡️|➡)"
# Стрелка в первом заголовке ведёт ПРЯМО на ссылку: между ними только декор и
# «см.». Живое ядро «# Тема → фиксация даты у [[Системы/X]]» — слова между
# стрелкой и ссылкой — не заглушка (DS C3, n777-in1).
_HEAD_RE = re.compile(
    rf"# .+?{_DECOR}{_ARROW}{_DECOR}(?:см\.?\s*)?(?P<links>{_LINK}(?:\s*(?:·|,|и)\s*{_LINK})*){_DECOR}"
    rf"|# .+?\s—{_DECOR}дубль,?\s*см\.?\s*(?P<links2>{_LINK})")
_HEAD_ARROW_RE = re.compile(rf"^# .*(?:{_ARROW}|\bдубль\b)", re.I)
# Заголовок, который сам говорит «указатель»: «см.» или «дубль» рядом со строгой
# стрелкой. Живое ядро так не называют, и выросшая заглушка с таким заголовком
# мертва при любой длине (n777-out1: DS C2 / Sonnet I2, «# X ⛔ дубль → [[L]]» —
# 10 713 знаков без тега).
_STRONG_HEAD_RE = re.compile(r"(?i)(?<!\w)(?:см\.|дубль(?!\w))")
# Ведущая строка-пометка: с НАЧАЛА строки (после декора) — ровно те формы, что
# писали ревизии (замер n777-out1 по 14 графам): «Дубль.», «Дубль,», «Дубль слит /
# сведён», «Дубль-человек…», «Дубль [[L]],», и «Сведён(о) в / редиректом на /
# Смерджен в» ПРЯМО перед ссылкой. Канон о влитом дубле пишет иначе: «Сведён в этот
# узел дубль [[X]]», «Дубль «Старый» [[X]] сведён сюда», «Сюда сведён дубль» — слова
# между глаголом и ссылкой, и он остаётся живым (DS C1 / Sonnet I5).
_LEAD_DECOR = r"(?:[>\s*_]|⛔|➡️|➡|⚠️|⚠|🔴|🔀)*"
_LEAD_RE = re.compile(
    rf"^{_LEAD_DECOR}(?:Дубль(?:[.,]|\s+(?:слит|свед[её]н)\b|-\w|\s+{_LINK},)"
    rf"|(?:Свед[её]н[оа]?\s+(?:в|редиректом\s+на)|Смерджен\s+в)\s+(?:\*\*)?\[\[)")
# Слово слияния в первых строках — подтверждение тега, не признак сам по себе.
_MERGE_WORD_RE = re.compile(r"(?i)\bдубль\b|свед[её]н|смерджен|редирект|перенес[её]н")
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclasses.dataclass(frozen=True)
class StubInfo:
    """Итог одного разбора: слит ли узел и куда ведёт.

    `targets` — все разные цели из заголовка или ведущей строки; `target` —
    единственная из них. Несколько целей («→ см. [[A]] · [[B]]») — развилка
    смыслов: узел мёртв, но канона нет, писать некуда."""
    merged: bool
    targets: tuple[str, ...] = ()

    @property
    def target(self) -> str | None:
        return self.targets[0] if len(self.targets) == 1 else None


_LIVE = StubInfo(False)


def stub_body(text: str) -> str:
    """Тело файла без frontmatter и ведущих HTML-комментариев — общая
    нормализация для is_redirect_stub и stub_target, чтобы стрелка в
    YAML-поле не притворялась редиректом (GLM, круг-4 M2)."""
    body = (text or "").replace("\r\n", "\n").strip()
    body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.S)
    return re.sub(r"\A(?:\s*<!--.*?-->\s*)*", "", body, flags=re.S).lstrip()


def _md(link: str) -> str:
    target = link.split("|", 1)[0].split("#", 1)[0].strip()
    return target if target.endswith(".md") else f"{target}.md"


def _targets(fragment: str) -> tuple[str, ...]:
    out: list[str] = []
    for link in _LINK_RE.findall(fragment):
        t = _md(link)
        if t.casefold() not in (x.casefold() for x in out):
            out.append(t)
    return tuple(out)


def _tagged(text: str) -> bool:
    # подстрока — дешёвый предохранитель: YAML шапки разбираем только у тех, где
    # тег вообще встречается (детектор зовут на каждый файл графа)
    return MERGED_TAG in text and MERGED_TAG in frontmatter.list_field(text, "tags")


def stub_info(text: str) -> StubInfo:
    """Один разбор на все формы заглушки (№777).

    Признаки по надёжности:
    - пометка tier3 «Дубль. Смерджен» где угодно — как прежде;
    - строгая стрелка в первом заголовке («# X → [[L]]», «# X ⛔ → см. [[L]] · [[M]]»,
      «# X — ⛔ дубль, см. [[L]]») — у тела до STUB_MAX; длиннее — со вторым
      признаком (тег, ведущая строка, «см.»/«дубль» в заголовке): голая стрелка
      одна не отличает узел, который люди напишут завтра (Sonnet I2, n777-in1);
    - ведущая строка-пометка со ссылкой в трёх первых строках под заголовком — до
      STUB_MAX или с тегом;
    - тег `дубль-слит` — со стрелкой в заголовке или словом слияния в первых
      строках: тег ставили руками, и голый тег узел не хоронит (Sonnet I3 / DS I3).
    Цель — из строгого заголовка, иначе из ведущей строки."""
    body = stub_body(text)
    if not body:
        return _LIVE
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    head = lines[0] if lines[0].startswith("# ") else ""
    under = lines[1:4] if head else lines[:3]
    short = len(body) <= STUB_MAX
    m = _HEAD_RE.fullmatch(head) if head else None
    lead = next((ln for ln in under if _LEAD_RE.match(ln)), None)
    tagged = _tagged(text)
    head_targets = _targets(m.group("links") or m.group("links2")) if m else ()
    lead_targets = _targets(lead) if lead else ()
    if m and (short or tagged or lead is not None or _STRONG_HEAD_RE.search(head)):
        return StubInfo(True, head_targets)
    if lead is not None and lead_targets and (short or tagged):
        return StubInfo(True, lead_targets)
    if MERGED_MARKER in text:
        return StubInfo(True, head_targets or lead_targets)
    if tagged and ((head and _HEAD_ARROW_RE.match(head)) or any(_MERGE_WORD_RE.search(ln) for ln in under)):
        return StubInfo(True, head_targets or lead_targets or _targets(head))
    return _LIVE


def stub_target(text: str) -> str | None:
    """Куда указывает заглушка-редирект: `→ [[Папка/Канон]]` → «Папка/Канон.md».

    Цель берём из ТЕЛА заглушки после среза frontmatter, не по сырому тексту:
    стрелка-ссылка в YAML-поле (note, related) увела бы цель мимо канона, и
    легитимная заглушка ушла бы в карантин (DS, круг-4). Узкая форма — по той
    же первой строке, которую валидирует is_redirect_stub; остальные формы —
    тем же разбором, что узнаёт заглушку (stub_info): иначе «→ см. [[L]]»
    узнавалась бы, а цель — нет, и запись уходила бы в саму заглушку (n777-in1 C1).
    Развилка («# X → [[A]] · [[B]]») — None и в узкой форме: иначе первая ссылка
    молча становилась каноном, и встреча уходила в A мимо кандидатов (n777-out1,
    Haiku C1 / DS I3 / Sonnet I1)."""
    if len(stub_info(text).targets) > 1:
        return None
    first = stub_body(text).split("\n", 1)[0]
    m = STUB_TARGET_RE.search(first)
    if m:
        return _md(m.group(1))
    return stub_info(text).target


def is_redirect_stub(text: str) -> bool:
    """Заглушка после слияния: короткий файл, чей ПЕРВЫЙ заголовок (после
    frontmatter) — стрелка на канон. Стрелка где-то в середине переписанного
    узла заглушкой не делает (круг-1 по PR #381, Codex + DeepSeek). Узкая
    форма — ворота записи облака (cloud_review): читаем широко, пишем узко."""
    body = stub_body(text)
    if not body or len(body) > STUB_MAX:   # длину мерим ПОСЛЕ frontmatter; облачная
        # заглушка с перечнем слитых фактов длиннее 1200 (DS, круг-1 по #448 M8)
        return False
    first = body.split("\n", 1)[0].strip()
    return REDIRECT_RE.fullmatch(first) is not None


def is_merged(text: str) -> bool:
    """Узел уже сведён в канон: пометка tier3, узкая заглушка или любая
    историческая форма (stub_info)."""
    return MERGED_MARKER in text or is_redirect_stub(text) or stub_info(text).merged


def is_pointer(text: str) -> bool:
    """Заглушка — чистый указатель (тело до STUB_MAX), а не выросший узел.

    Выросшие заглушки несут факты, дописанные разбором встреч ПОСЛЕ слияния,
    пока детектор их не узнавал; в каноне этих фактов нет. Поиск подменяет хит
    заглушки каноном только у указателя — иначе эти факты пропали бы из выдачи
    до переноса тел (DS I4, n777-in1)."""
    return len(stub_body(text)) <= STUB_MAX
