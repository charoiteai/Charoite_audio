"""Досье — этаж между поиском и графом.

Задача. На вопрос «что в итоге по этой теме» поиск сегодня отдаёт десяток
разрозненных кусков, и модель каждый раз собирает из них ответ заново. Досье —
готовая сводка по теме: состояние, хроника, решённое, открытое — со ссылками на
узлы-источники. Поиск сначала смотрит в индекс досье и только потом лезет в граф.

Почему так, а не иначе (по ресёрчу практик 2025-2026):

* **GraphRAG (Microsoft)** строит community summaries кластеризацией Leiden по
  всему графу. Идея сводки верная — ею и живёт этот модуль. Полная пересборка
  каждую ночь не берётся: на графе в тысячу файлов это десятки минут работы
  локальной модели ради нескольких изменившихся тем.
* **LightRAG** — отсюда главное: инкрементальное обновление без пересборки всего
  и двухуровневый поиск (сущности + темы). Кластер пересобирается, только если
  изменился хоть один его источник; сравнение по хешу состава и mtime.
* **RAPTOR** строит дерево рекурсивных абстракций. Для markdown-графа лишний
  уровень: иерархия уже задана [[backlink]]-ами, и её достаточно.
* **Graphiti/Zep** — оттуда идея, что у сводки есть срок годности и инвалидирует
  её событие (новая встреча по теме), а не расписание.

Кластер здесь — ядро-хаб плюс всё, что на него ссылается: соседние ядра, встречи,
документы. Тема кластера = имя хаба. Это дёшево (обход backlink-ов, без Leiden) и
даёт ровно те границы, которые человек уже провёл руками, расставляя ссылки.

Полный прогон (`--full`) — только по руке, после смены промпта, лимитов или
модели: состав кластера входит в отпечаток, так что сдвиг границы и без него
делает тему изменившейся, а пересказ тех же источников новых фактов не даёт и
стирает облачную ревизию тела (№809, замер .backup 25.10). Что генератор
сменился, видно по полю `вход:` (№842): метка, а не триггер пересборки.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import pathlib
import re
import shutil
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Literal
from charoite_graph import frontmatter, graph_names
from charoite_graph.graph_schema import GraphSchema, name_date
from charoite_graph.redirects import is_merged as _is_merged, stub_target as _stub_target   # локальная `redirects: dict` в scan() перекрыла бы модуль

# Сколько источников максимум уходит в один запрос к модели. Больше — сводка
# начинает терять детали, а генерация упирается в контекст.
MAX_SOURCES = 14
# Кластеры меньше этого не заводим: два файла — это не тема, это пара заметок.
MIN_CLUSTER = 3
# Потолок текста одного источника в промпте.
SRC_CHARS = 2500
# Всего на промпт.
PROMPT_CHARS = 28_000

INDEX_MD = "_ИНДЕКС.md"
INDEX_JSON = "_index.json"

_STOP = {
    "или", "если", "как", "что", "это", "для", "при", "над", "под", "про",
    "все", "весь", "вся", "его", "её", "их", "them", "the", "and", "for",
    "быть", "было", "были", "есть", "нет", "уже", "ещё", "еще", "так", "там",
    "тот", "эта", "этот", "они", "она", "оно", "мы", "вы", "он",
}


def _norm(s: str) -> str:
    """Нижний регистр, ё→е, без диакритики — общий ключ для сравнения."""
    s = unicodedata.normalize("NFKD", s.lower().replace("ё", "е"))
    return "".join(c for c in s if not unicodedata.combining(c))


def _stem(w: str) -> str:
    """Грубое отсечение русских окончаний. Полноценный стеммер тут избыточен:
    ключи нужны для отбора кандидатов, точность добирает семантика."""
    for suf in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
                "ах", "ях", "ов", "ев", "ый", "ий", "ая", "яя", "ое", "ее",
                "ы", "и", "а", "я", "о", "е", "у", "ю"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def keywords(text: str, limit: int = 24) -> list[str]:
    """Ключи темы: самые частые значимые основы. Ими индекс и матчится.

    Коды ошибок и версий (403, 999, 38-12) сохраняем: по ним ищут чаще, чем
    по словам. Имена файлов-стенограмм отбрасываем — как ключ они бесполезны,
    а место в списке занимают.
    """
    # CJK — в классе символов: без него у китайской встречи ключи пусты и
    # досье не ищется вовсе (хвост аудита 20.08, GLM)
    # CJK — от одного иероглифа; кириллица/латиница — от трёх, иначе «не», «на»
    # заняли бы верх частотности (DS по #455)
    words: list[str] = []
    for tok in re.findall(r"[\u4e00-\u9fff]+|[а-яa-z0-9][а-яa-z0-9_.-]{2,}", _norm(text)):
        # CJK без пробелов приходит одним «словом» на всё предложение — такой
        # ключ длиннее 24 знаков выпадал целиком, и у китайской встречи ключей
        # не оставалось: режем на биграммы, как CJK-индексы (luna по #455)
        if "\u4e00" <= tok[0] <= "\u9fff" and len(tok) > 2:
            words.extend(tok[i:i + 2] for i in range(len(tok) - 1))
        else:
            words.append(tok)
    freq: dict[str, int] = defaultdict(int)
    for w in words:
        if w in _STOP or len(w) > 24:
            continue
        # «2026-07-24_0911_настроика_postman_ревизия_claude» и подобное
        if w.count("_") >= 2 or re.match(r"^\d{4}-\d{2}-\d{2}", w):
            continue
        freq[w if w.isdigit() else _stem(w)] += 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _match(qkey: str, key: str) -> bool:
    """Совпадение ключей: точное или по префиксу.

    «qwen» должен находить «qwen3-32b», «токен» — «токена». Без этого индекс
    промахивается на самых частых запросах.
    """
    if qkey == key:
        return True
    short, long = (qkey, key) if len(qkey) <= len(key) else (key, qkey)
    return len(short) >= 4 and long.startswith(short)


# ─────────────────────────── чтение графа ───────────────────────────

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")


def cites(text: str | None, keys: list[str]) -> bool:
    """Ссылается ли текст на узел под одним из ключей — или на его производный
    файл `<ключ>_…` (минутки, разбор). Грамматика ссылки — `LINK_RE`, та же, что
    у `scan`: третья рукописная регулярка рядом с ней пропускала `\\|` таблиц,
    которыми пишет тела ревизия (выход n841-out4 Haiku C1, Sonnet M2, DS M3).
    Цель — `graph_names.link_target_stem`, своей нормализации нет (out5 Haiku C1)."""
    targets = {graph_names.link_target_stem(m.group(1)) for m in LINK_RE.finditer(text or "")}
    return any(t == k or t.startswith(k + "_") for t in targets for k in keys)


def _title(p: pathlib.Path) -> str:
    return p.stem


@dataclass(frozen=True)
class GraphSnapshot:
    """Один обход графа: файлы, обратные ссылки, стрелки заглушек alias → канон и
    имена всех заглушек слияния. Одиночная стрелка ведёт к канону, и судится канон
    (`_canon`): забыт канон — ушла встреча. Заглушка без однозначной цели (развилка,
    без цели) — не уход, её цели не судим: три круга подряд их разбор давал ложный
    уход (out2 C2, out3 C1, I1); такой уход остаётся «забыть встречу» (часть 2).
    Удалённая под замком заглушка даёт skip (записи нет) и уходит со следующим снимком;
    отпечаток её удаления не видит, поэтому ночь части 2 зовёт `plan` для каждой темы,
    а не только для сменивших отпечаток (out5 Haiku C1). Удалён файл заглушки с живым
    каноном — стрелки нет, имя из состава уходит; писатель части 2 пишет состав каноном
    (out6 Haiku I1)."""
    files: dict[str, dict]
    backlinks: dict[str, set[str]]
    redirects: dict[str, str]
    stubs: frozenset[str] = frozenset()


def scan(graph: pathlib.Path, *, schema: GraphSchema,
         strict: bool = False) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """Все .md графа → карта файлов и обратные ссылки (`scan_snapshot` без стрелок)."""
    snap = scan_snapshot(graph, schema=schema, strict=strict)
    return snap.files, snap.backlinks


def _strict_source_paths(graph: pathlib.Path, schema: GraphSchema) -> Iterable[pathlib.Path]:
    """Обход для публикации: ошибка перечисления не превращается в пустую папку."""
    def failed(error: OSError) -> None:
        raise error

    for top, directories, names in os.walk(graph, onerror=failed):
        folder = pathlib.Path(top)
        directories[:] = [name for name in directories if not name.startswith(".")
                          and not schema.is_dossier((folder / name / "_").relative_to(graph).as_posix())]
        for name in names:
            if name.endswith(".md"):
                yield folder / name


def scan_snapshot(graph: pathlib.Path, *, schema: GraphSchema, strict: bool = False) -> GraphSnapshot:
    """Все .md графа → карта файлов и обратные ссылки.

    Возвращает (files, backlinks): files[title] = {path, text, mtime, kind},
    backlinks[title] = множество тех, кто на него ссылается. Роли папок — из
    схемы хранилища: какие файлы служебные, где сами досье, какая папка — ядра.
    Архив встреч читается: исключения схемы — правило обхода поиска, не досье.
    `strict` передаёт ошибки чтения вызывающему вместо неполного снимка.
    """
    files: dict[str, dict] = {}
    backlinks: dict[str, set[str]] = defaultdict(set)
    # alias → канон: заглушки сами не темы, но входящие ссылки на них живые
    redirects: dict[str, str] = {}
    stubs: set[str] = set()

    paths = _strict_source_paths(graph, schema) if strict else graph.rglob("*.md")
    for p in paths:
        rel = p.relative_to(graph)
        # служебное, бэкапы и сами досье в кластеры не берём
        if any(part.startswith(".") for part in rel.parts):
            continue
        if schema.is_dossier(rel.as_posix()) or schema.is_service_name(p.name):
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            if strict:
                raise
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            print(f"  ⚠️ файл графа не в UTF-8: {p} — читаю с заменой")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # Redirect-заглушка после tier3-слияния — не тема: она сохраняет
        # входящие ссылки заметок и ссылку на канон, кластер вокруг неё
        # «жив», и ночь собирала досье по мёртвому дублю рядом с каноном,
        # тратя на него запросы облака (аудит GLM 17.08). tier3.load_cores и
        # morning_brief её уже пропускают — теперь и здесь.
        if _is_merged(text):
            # Сама заглушка — не тема, но ссылки НА НЕЁ из заметок живые:
            # встреча, сославшаяся на старое имя, пропадала из кластера
            # канона, и досье собиралось без неё (аудит графа 26.08, Codex).
            # Запоминаем стрелку и переносим входящие ниже, после скана.
            # цель — из первой строки заголовка (redirects.stub_target), а не
            # первая [[ссылка]] в файле: та могла быть из frontmatter (luna I5)
            target = _stub_target(text)
            stubs.add(_title(p))
            if target:
                redirects[_title(p)] = target.rsplit("/", 1)[-1].removesuffix(".md").strip()
            continue
        t = _title(p)
        kind = rel.parts[0] if len(rel.parts) > 1 else "Прочее"
        if t in files:
            # Два файла с одним именем в разных папках (Люди/CRM.md и
            # Системы/CRM.md): раньше второй молча затирал первый, и какой
            # именно выживет, зависело от порядка обхода каталога. Выбор
            # теперь детерминирован — ядро важнее (кластеры строятся вокруг
            # ядер), при равенстве побеждает более содержательный файл — и
            # виден в логе (аудит графа 26.08, Codex).
            old_meta = files[t]
            # rel в ключе — иначе при равном ранге выигрывал тот, кого
            # rglob вернул первым, и состав графа зависел от порядка обхода.
            rank = lambda meta: (schema.is_core_folder(meta["kind"]), len(meta["text"]),
                                 meta["rel"])
            new_meta = {"path": p, "rel": str(rel), "text": text,
                        "mtime": p.stat().st_mtime, "kind": kind}
            keep, drop = ((new_meta, old_meta) if rank(new_meta) > rank(old_meta)
                          else (old_meta, new_meta))
            print(f"досье: имя «{t}» занято дважды — беру {keep['rel']}, "
                  f"пропускаю {drop['rel']}")
            files[t] = keep
            continue
        files[t] = {"path": p, "rel": str(rel), "text": text,
                    "mtime": p.stat().st_mtime, "kind": kind}
    # Ссылки разбираем ВТОРЫМ проходом, когда состав files уже устоялся: при
    # дублирующемся имени первый проход успевал записать ссылки проигравшего
    # файла под общим титулом, и кластер тянул за собой связи, которых у
    # выжившего нет (круг-2 по PR #438, DS Minor 5).
    for t, meta in files.items():
        for m in LINK_RE.finditer(meta["text"]):
            target = m.group(1).split("/")[-1].strip()
            if target and target != t:
                backlinks[target].add(t)

    # Входящие ссылки с заглушек — канону. Цепочку A→B→C проходим до конца,
    # visited держит кольцо A→B→A от вечного цикла.
    for alias, first in redirects.items():
        if alias in files:
            # Имя занято живым узлом (tier3 слил «Ядра/CRM», а конвейер завёл
            # «Системы/CRM»): входящие принадлежат ему, а не старой заглушке.
            # Иначе система теряла все ссылки, а ядро обрастало чужими —
            # молча (круг-3 по PR #438, GLM Important 3).
            continue
        canon, seen = first, {alias}
        # Останавливаемся на живом узле: в цепочке A→B→C, где B — и заглушка,
        # и живой файл-тёзка, ссылки A уходили мимо B прямо к C. Та же
        # политика, что у головы цепочки (круг-4 по PR #438, DS Important 3).
        while canon in redirects and canon not in files and canon not in seen:
            seen.add(canon)
            canon = redirects[canon]
        who = backlinks.pop(alias, set())
        if who and canon in files:
            backlinks[canon] |= who - {canon}
    return GraphSnapshot(files, dict(backlinks), redirects, frozenset(stubs))


def meeting_date(meta: dict, *, schema: GraphSchema) -> str | None:
    """Дата встречи из её пути — только для папки встреч схемы, иначе None.

    Единственное место, где досье решает «встреча и её дата»: `is_meeting_folder`
    узнаёт и архивную папку, `name_date` достаёт дату из ближайшего сегмента.
    Участник без `rel` или без `kind` — не встреча: поля берём через `.get`.
    """
    if not schema.is_meeting_folder(meta.get("kind", "")):
        return None
    return name_date(meta.get("rel", ""))


def clusters(files: dict[str, dict], backlinks: dict[str, set[str]], *,
             schema: GraphSchema, min_size: int = MIN_CLUSTER) -> dict[str, list[str]]:
    """Тема → список источников. Хаб — ядро, на которое ссылаются больше всего.

    Кластер строится вокруг ядра: само ядро, всё что на него ссылается, и ядра,
    на которые ссылается оно само. Это 1-hop окрестность — ровно та связность,
    которую человек уже выразил ссылками.
    """
    out: dict[str, list[str]] = {}
    for title, meta in files.items():
        if not schema.is_core_folder(meta["kind"]):
            continue
        inbound = backlinks.get(title, set())
        outbound = {m.group(1).split("/")[-1].strip()
                    for m in LINK_RE.finditer(meta["text"])}
        members = {title} | inbound | {o for o in outbound if o in files}
        # Узел-метка «Собеседник 3» в Люди — склейка разных людей, не источник
        # темы: ни в промпт, ни в «Источники», ни в счёт (n815-in3 DS I2). Порог
        # размера — по ссылкам до фильтра: иначе тема из ядра, встречи и узла-метки
        # выпадала из очереди с меткой в досье навсегда (n815-out1 I4)
        linked = {m for m in members if m in files}
        members = {m for m in linked
                   if not (schema.is_person_folder(files[m]["kind"])
                           and graph_names.is_placeholder_node(m))}
        if len(linked) >= min_size:
            # Хаб — первым: build_prompt берёт первые MAX_SOURCES участников,
            # и в кластере из 14+ встреч само ядро темы (её «Статус» и «Суть»)
            # в промпт не попадало вовсе — сводка собиралась без главного
            # источника, и страдали ровно самые большие темы (аудит графа
            # 26.08, GLM). Дальше встречи с датой — от новой к старой, затем
            # встречи без даты и прочие — по имени. Три списка, а не один ключ:
            # у встречи без даты цифровое имя иначе встало бы впереди дат.
            dated, undated, others = [], [], []
            for m in members - {title}:
                if schema.is_meeting_folder(files[m]["kind"]):
                    d = meeting_date(files[m], schema=schema)
                    if d:
                        dated.append((d, m))
                    else:
                        undated.append(m)
                else:
                    others.append(m)
            dated.sort(reverse=True)                  # дата и имя — от новой/большой
            undated.sort()
            others.sort()
            out[title] = [title] + [m for _, m in dated] + undated + others
    return out


def fingerprint(members: list[str], files: dict[str, dict]) -> str:
    """Отпечаток состава: состав + время правки каждого источника.

    Совпал с записанным в досье — тема не менялась, пересобирать нечего.
    Именно это делает ночной проход дешёвым: работа идёт только по изменившимся.
    """
    h = hashlib.sha256()
    for m in sorted(members):
        meta = files.get(m)
        if not meta:
            continue
        h.update(m.encode("utf-8"))
        # Микросекунды, а не целые секунды: правка, легшая в ту же секунду,
        # что и предыдущий скан, давала прежний отпечаток — «тема не менялась»,
        # и досье не пересобиралось никогда (аудит графа 26.08, Codex).
        h.update(f"{meta['mtime']:.6f}".encode())
    return h.hexdigest()[:16]


def fingerprint_on_disk(members: list[str], files: dict[str, dict]) -> str:
    """Тот же отпечаток, но время правки — с диска сейчас, а не из снимка скана.

    Скан ночи и ревизии живёт часами: источник, удалённый или поправленный после
    него («забыть встречу» посреди прогона), снимок не видит, и тело с пересказом
    ушедшей встречи сходило за собранное по составу. Удалённый член выпадает,
    правленный меняет время (круг n841-opus-fix: Sonnet C1–C2, DeepSeek C1 и I2).
    Совпал с `fingerprint` по снимку — тексты снимка те же, что на диске.
    """
    now = {}
    for m in members:
        meta = files.get(m)
        if not meta:
            continue
        try:
            now[m] = {**meta, "mtime": pathlib.Path(meta["path"]).stat().st_mtime}
        except OSError:
            continue
    return fingerprint(members, now)


# ─────────────────────────── генерация ───────────────────────────

# Инструкция стоит ПОСЛЕ источников намеренно: на длинном контексте требования
# из начала промпта вытесняются, и модель сползает в диалог («Принято, готов
# работать. Что дальше?»). Замер 29.07: инструкция сверху — ответ-диалог,
# инструкция снизу с якорем первой строки — формат соблюдён.
PROMPT = """Ниже — узлы графа встреч по теме «{theme}»: ядра, встречи, документы.

ИСТОЧНИКИ:
{sources}

────────────────────────────────────────
ЗАДАНИЕ

Собери из источников выше одну фактическую сводку по теме «{theme}».

Требования:
- Только то, что есть в источниках. Ничего не додумывать и не обобщать.
- В конце каждого пункта — ссылка на источник в виде [[Имя файла]].
- Нет данных на раздел — поставь «—». Раздел не выдумывать.
- Числа, имена, названия систем — дословно из источников.
- {unlabeled_rule}
- Русский язык, короткие фразы, без вводных оборотов.

Запрещено: обращаться к читателю, спрашивать «что дальше», предлагать варианты
работы, писать «принято», «готов», «如果 нужно». Это документ, а не переписка.

Ответ начни СРАЗУ со строки «## Сейчас» и заполни ровно эти пять разделов:

## Сейчас
Три-пять строк: состояние темы на сегодня.

## Как пришли
Хроника по датам, от старого к новому, до восьми строк.

## Решено
Принятые решения, каждое с датой и ссылкой.

## Открыто
Незакрытое: вопросы, блокеры, невыполненные поручения.

## Кто в теме
Люди и роли, по строке на человека.
"""

# Признак того, что модель выдала документ, а не реплику в чат.
VALID_RE = re.compile(r"^\s*##\s*Сейчас", re.M)


def looks_valid(body: str) -> bool:
    """Брак ловим до записи: без этого в граф попадают «Принято, что дальше?»."""
    if not VALID_RE.search(body or ""):
        return False
    have = sum(1 for h in ("## Сейчас", "## Как пришли", "## Решено",
                           "## Открыто", "## Кто в теме") if h in body)
    return have >= 4


def trim_to_format(body: str) -> str:
    """Отрезает всё до первого «## Сейчас» — модель любит предисловия."""
    m = VALID_RE.search(body or "")
    return body[m.start():].strip() if m else (body or "").strip()


def source_text(meta: dict) -> str:
    """Текст узла для модели — один на сборку досье и ревизию: без шапки, без
    меток деятелем (`graph_names.unlabel_speakers`), не длиннее `SRC_CHARS`.
    Чистка — до среза, в памяти: файл графа не меняется (№815)."""
    # BOM и пустые строки до шапки: прежний срез их терпел (n815-out1 M6); сам
    # `frontmatter.split` строг — им же пишется шапка на запись
    _, body = frontmatter.split((meta.get("text") or "").lstrip("\ufeff\n\r "))
    return graph_names.unlabel_speakers(body.strip())[:SRC_CHARS]


def build_prompt(theme: str, members: list[str], files: dict[str, dict], *,
                 schema: GraphSchema) -> str:
    """Промпт темы: отбор по бюджету, подача — ядро и встречи от старой к новой.

    Состав входа от перестановки не зависит: бюджет считается на исходном
    порядке `members[:MAX_SOURCES]`, и только отобранное переупорядочивается.
    Блок ядра (участник с именем темы) идёт первым, датированные встречи — по
    дате от старой к новой, встречи без даты и прочие — в порядке отбора.
    """
    selected: list[tuple[str, dict, str]] = []
    total = 0
    for m in members[:MAX_SOURCES]:
        meta = files.get(m)
        if not meta:
            continue
        block = f"### [[{m}]] ({meta['kind']})\n{source_text(meta)}\n"
        if total + len(block) > PROMPT_CHARS:
            break
        selected.append((m, meta, block))
        total += len(block)

    core: list[str] = []
    dated: list[tuple[str, str, str]] = []
    undated: list[str] = []
    others: list[str] = []
    for m, meta, block in selected:
        if m == theme:
            core.append(block)
        elif schema.is_meeting_folder(meta["kind"]):
            d = meeting_date(meta, schema=schema)
            if d:
                dated.append((d, m, block))
            else:
                undated.append(block)
        else:
            others.append(block)
    # Подача датированных — точный обратный порядок отбора в clusters:
    # там дата и имя идут по убыванию, здесь — по возрастанию.
    dated.sort(key=lambda t: (t[0], t[1]))
    parts = core + [t[2] for t in dated] + undated + others
    return PROMPT.format(theme=theme, sources="\n".join(parts),
                         unlabeled_rule=graph_names.UNLABELED_RULE)


# Остальное, что решает тело досье, кроме источников: вызов модели ночи.
GEN_SYSTEM = ("Ты составляешь фактические справки по документам. "
              "Отвечаешь только текстом справки в заданном формате, "
              "без обращений к читателю и без вопросов.")
GEN_PREDICT = 1800
GEN_THINK = False
GEN_TEMPERATURES = (0.2, 0.05)      # вторая попытка чуть холоднее
INPUT_FIELD = "вход"


def gen_call() -> dict:
    """Параметры вызова модели ночи, кроме промпта, модели и температуры: их
    берёт и `generate`, и `generator_input` — литерал в вызове мимо метки не
    пройдёт (n842-out1, Haiku I2)."""
    return {"system": GEN_SYSTEM, "num_predict": GEN_PREDICT, "think": GEN_THINK}


def generator_input(model: str, num_ctx: int | None = None) -> str:
    """Отпечаток генератора тела (`вход:` в шапке, №842): промпт, лимиты отбора
    и ответа, модель и её окно (`num_ctx`: Ollama молча режет промпт по нему,
    n842-out1 Haiku I1). Отпечаток состава этого не видит: после снятия
    воскресного `--full` (№809) досье с прежними источниками новым генератором
    не собиралось никогда. Метка, а не триггер: пересборка всех тем по смене
    промпта сжала бы наращённые тела и стёрла ревизию (n843-in2, критика 2) —
    ночь показывает счёт, пересборка — решение владельца. Код отбора и разбора
    ответа (`build_prompt`, `trim_to_format`, `looks_valid`) сюда не входит."""
    raw = json.dumps([PROMPT, graph_names.UNLABELED_RULE, gen_call(), MAX_SOURCES,
                      SRC_CHARS, PROMPT_CHARS, list(GEN_TEMPERATURES), model, num_ctx],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def read_input(loaded: DossierLoad) -> str:
    """`вход:` из шапки; `""` — поля нет (досье до №842 или собрано руками).
    Читает шапку, а не окно файла: строка «вход: …» в теле — не метка (out1 M1)."""
    return frontmatter.scalar_field(loaded.text or "", INPUT_FIELD)


def render(theme: str, body: str, members: list[str], files: dict[str, dict],
           fp: str, today: str, gen: str = "", *, author_payload: str | None = None) -> str:
    """Готовый файл досье: frontmatter для машины, текст для человека.

    `author_payload` — точное содержимое после строки авторского заголовка,
    включая пустые строки. None создаёт прежний маркер; "" сохраняет пустоту.
    """
    kinds: dict[str, list[str]] = defaultdict(list)
    for m in members:
        if m in files:
            kinds[files[m]["kind"]].append(m)

    keys = keywords(theme + " " + " ".join(members) + " " + body)
    fm = [
        "---",
        "type: досье",
        f"тема: {theme}",
        f"собрано: {today}",
        f"отпечаток: {fp}",
        *([f"{INPUT_FIELD}: {gen}"] if gen else []),
        f"источников: {len(members)}",
        "ключи: [" + ", ".join(keys) + "]",
        "tags: [досье, авто]",
        "---",
        "",
        f"# Досье: {theme}",
        "",
        "> Собрано автоматически ночным проходом. Правки руками сохранятся:",
        "> при пересборке раздел «Правки автора» не трогается.",
        "",
        strip_protected(body).strip(),
        "",
        "## Источники",
    ]
    for kind in sorted(kinds):
        links = " · ".join(f"[[{m}]]" for m in sorted(kinds[kind]))
        fm.append(f"- **{kind}:** {links}")
    fm += ["", "## Правки автора", ""]
    return "\n".join(fm) + ("\n—\n" if author_payload is None else author_payload)


BACKUP_DIR = ".backup"       # копии досье до пересборки и ревизии: `Досье/.backup/<штамп>/`


def backup(folder: pathlib.Path, stamp: str, path: pathlib.Path, keep: int = 40) -> pathlib.Path | None:
    """Копия досье перед перезаписью — в `Досье/.backup/<штамп>/`, старые
    каталоги сверх `keep` убираются. Один бэкап на пересборку и ревизию:
    автомат без копии — не автомат, а рулетка (правило tier3); пересборка
    до 13.09 копии не делала и стирала правки облачной ревизии (аудит 13.09,
    GLM C1). Нет файла — копировать нечего. OSError копии — наружу: без копии
    вызывающий не перезаписывает (круг-1 по #561, DS I2 / GLM I1). `keep` 40:
    каталог общий у пересборки и ревизии, штатная ночь — два штампа (GLM M3)."""
    if not path.exists():
        return None
    bdir = folder / BACKUP_DIR / stamp
    bdir.mkdir(parents=True, exist_ok=True)
    dst = bdir / path.name
    shutil.copy2(path, dst)
    try:
        backups = sorted((folder / BACKUP_DIR).iterdir(), reverse=True)
    except OSError:
        return dst
    for old in backups[keep:]:
        # и файлы тоже: случайный tmp в .backup/ иначе жил бы вечно (GLM M4 по #561)
        if old.is_dir():
            shutil.rmtree(old, ignore_errors=True)
        else:
            old.unlink(missing_ok=True)
    return dst


def preserve_manual(old_text: str) -> str | None:
    """Ручные правки переживают пересборку — иначе досье никто не будет править."""
    kept = DossierLoad("ok", old_text).parsed.manual
    return kept if kept and kept != "—" else None


PROTECTED_HEADINGS = ("## Правки автора", "## Источники")


# Заголовок с отступом, любым числом решёток и без пробела тоже: «### Правки автора» и
# «##Правки автора» строка из стенограммы подсунет так же легко, как «## »,
# а старый регэксп их пропускал (круг-1 по PR #382, DeepSeek Critical).
_PROTECTED_RE = re.compile(r"(?m)^[ \t]*#+\s*(?:Правки автора|Источники)\s*$")


def strip_protected(body: str) -> str:
    """Тело ревизии без защищённых секций — их пишет конвейер, не модель.

    Режем по СТРОКЕ-заголовку, а не по подстроке: упоминание «## Источники»
    внутри абзаца — не раздел (ревью 17.08).
    """
    return _PROTECTED_RE.split(body, maxsplit=1)[0].rstrip()


def split_dossier(text: str) -> tuple[str, str, str | None, str | None]:
    """(шапка до «## Сейчас», тело пяти разделов, источники, правки автора).

    Всё — по якорным заголовкам-строкам, не по подстрокам: цитата
    «## Правки автора» в пункте резала тело пополам, и проверка видела
    только префикс (круг-1 по PR #382, Codex Critical). Источники — None,
    если раздела нет: такое досье собрано руками, и трогать его нельзя.
    """
    parsed = DossierLoad("ok", text).parsed
    return parsed.head, parsed.body, parsed.sources, parsed.manual


def labeled_body(text: str) -> bool | None:
    """В теле досье (пять разделов, без «Источников» и «Правок автора») действует
    метка диаризации? Один предикат на доктора графа и ночную очередь — иначе
    лента считала бы одно, а ночь чинила другое (n815-in3 DS C2)."""
    parsed = DossierLoad("ok", text).parsed
    return graph_names.has_speaker_actor(parsed.body) if parsed.complete else None


REVISION_FIELD = "ревизия"


_REVISION_VALUE = re.compile(r"\d{4}-\d{2}-\d{2}")


def revision(text: str | None) -> str:
    """Дата облачной ревизии тела из шапки (`ревизия:`); `""` — поля нет. Держит
    только дата, как её пишет ревизия: `~`, `нет`, `false` не замораживают (Opus M2).
    Знак замены в значении тоже держит: повреждение даты не снимает защиту
    проверенного тела от пересборки."""
    v = frontmatter.scalar_field(text or "", REVISION_FIELD)
    return v if _REVISION_VALUE.fullmatch(v) or "\ufffd" in v else ""


def with_revision(text: str, date: str) -> str:
    """Поставить дату ревизии в шапку: заменой поля, без второй строки."""
    return frontmatter.with_field(text, REVISION_FIELD, date)


def without_revision(text: str) -> str:
    """Снять поле ревизии: тело снова отдаётся ночной пересборке. Зовёт
    «забыть встречу» у досье, которое на неё ссылается: пересказ не вправе
    пережить источник (вход n841-in1 S-C1, выход out3 DS C2 и Haiku C1)."""
    return frontmatter.without_field(text, REVISION_FIELD)


_BODY_TITLES = ("Сейчас", "Как пришли", "Решено", "Открыто", "Кто в теме")
_SECTION_LINE = re.compile(r"[ \t]*(?:(#+)[ \t]*(Источники|Правки автора)|(##)[ \t]*(Сейчас|Как пришли|Решено|Открыто|Кто в теме))[ \t]*")
# Deletion compatibility only: the old expressions also saw fenced lines and
# allowed whitespace across physical lines. Their false boundaries still veto
# deletion; they never supply sections to readers or authorize a write.
_LEGACY_DELETE_PROTECTED = re.compile(r"(?m)^#{1,6}\s*(Источники|Правки автора)\s*$")
_FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_ENVELOPE = re.compile(r"\n# Досье: [^\n]+\n\n> Собрано автоматически ночным проходом\. Правки руками сохранятся:\n> при пересборке раздел «Правки автора» не трогается\.\n\n")


@dataclass(frozen=True)
class DossierSection:
    title: str
    start: int
    content: int
    end: int


@dataclass(frozen=True)
class DossierParse:
    """Normalized spans are read projections, never write permission by themselves.

    Every byte-equivalent character belongs to head/body or a section/remainder;
    all boundaries remain available even when a duplicate makes projection unsafe.
    """
    head: str
    body: str
    sources: str | None
    manual: str | None
    sections: tuple[DossierSection, ...]
    body_span: tuple[int, int] | None
    complete: bool
    reason: str
    protected: bool
    remainder: str
    legacy_delete_veto: bool


def _parse_dossier(text: str) -> DossierParse:
    fm, envelope = frontmatter.split(text.removeprefix("\ufeff"))
    prefix = len(text) - len(envelope)
    found: list[tuple[str, int, int]] = []
    fence: tuple[str, int] | None = None
    offset = 0
    # Scan physical LF lines, not splitlines(): Unicode separators are text.
    for line in re.findall(r"[^\n]*\n|[^\n]+$", text):
        if fm is not None and offset < prefix:
            offset += len(line)
            continue
        # Offsets must include LF even in unsupported-newline read projections.
        physical = line.rstrip("\n")
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}[ \t]*", physical):
                fence = None
        elif not re.match(r" {0,3}>", physical):
            opening = _FENCE_OPEN.fullmatch(physical)
            if opening and (opening[1][0] != "`" or "`" not in opening[2]):
                fence = (opening[1][0], len(opening[1]))
            else:
                m = _SECTION_LINE.fullmatch(physical)
                if m:
                    found.append((m[2] or m[4], offset, offset + len(line)))
        offset += len(line)
    sections = tuple(DossierSection(title, start, content, found[i + 1][1] if i + 1 < len(found) else len(text))
                     for i, (title, start, content) in enumerate(found))
    protected = [s for s in sections if s.title in ("Источники", "Правки автора")]
    bodies = [s for s in sections if s.title == "Сейчас"]
    start = bodies[0].start if bodies else None
    end = next((s.start for s in protected if start is not None and s.start > start), len(text))
    body_end = len(text[:end].rstrip())
    span = (start, body_end) if start is not None else None
    head = text[:start] if start is not None else text
    body = text[start:body_end] if start is not None else ""

    def payload(title):
        matches = [s for s in protected if s.title == title]
        if len(matches) != 1:
            return None
        s = matches[0]
        # Ordinary Markdown headings in author text belong to the author too.
        stop = next((p.start for p in protected if p.start > s.start), len(text))
        return text[s.content:stop].strip()

    sources, manual = payload("Источники"), payload("Правки автора")
    env = _ENVELOPE.match(envelope) if fm is not None else None
    have_fingerprint = fm is not None and frontmatter.has_field(text, "отпечаток")
    complete = bool(env and have_fingerprint and start == prefix + env.end()
                    and [s.title for s in protected] == ["Источники", "Правки автора"]
                    and [s.title for s in sections if s.start < protected[0].start] == list(_BODY_TITLES)
                    and not any(s.title in _BODY_TITLES for s in sections
                                if protected[0].start < s.start < protected[1].start))
    reason = ""
    if fence:
        reason = "незакрытый блок кода"
    elif protected and not complete:
        titles = [s.title for s in protected]
        if len(set(titles)) != len(titles):
            detail = "повтор защищённого заголовка"
        elif set(titles) != {"Источники", "Правки автора"}:
            detail = "неполный набор защищённых разделов"
        elif titles != ["Источники", "Правки автора"]:
            detail = "обратный порядок защищённых разделов"
        elif not env or start != prefix + env.end():
            detail = "неизвестная внешняя оболочка или остаток перед телом"
        elif not have_fingerprint:
            detail = "нет поля отпечатка в закрытой шапке"
        elif [s.title for s in sections if s.start < protected[0].start] == list(_BODY_TITLES):
            detail = "заголовок тела внутри раздела Источники"
        else:
            detail = "неполный или нарушенный порядок пяти разделов тела"
        reason = f"неоднозначная структура защищённых разделов: {detail}"
    known = complete and not fence
    # Unknown input remains available in full, independently of read projections.
    admitted = {(s.start, s.title) for s in protected}
    legacy_body = VALID_RE.search(text)
    legacy_start = legacy_body.start() + legacy_body[0].index("#") if legacy_body else None
    legacy_delete_veto = (legacy_start != start or any(
        (m.start(), m[1]) not in admitted for m in _LEGACY_DELETE_PROTECTED.finditer(text)))
    return DossierParse(head, body, sources, manual, sections, span, known, reason,
                        bool(protected), "" if known else text, legacy_delete_veto)


@dataclass(frozen=True)
class DossierLoad:
    state: Literal["ok", "damaged", "unreadable", "missing"]
    text: str | None = None
    raw: bytes | None = field(default=None, repr=False)
    parsed: DossierParse = field(init=False)
    newline: str | None = field(init=False)

    def __post_init__(self):
        original = self.raw.decode("utf-8", errors="replace") if self.raw is not None else (self.text or "")
        supported = not any(c in original for c in "\x85\u2028\u2029")
        rest = original.replace("\r\n", "")
        supported = supported and "\r" not in rest and not ("\r\n" in original and "\n" in rest)
        object.__setattr__(self, "newline", ("\r\n" if "\r\n" in original else "\n") if supported else None)
        normalized = original.replace("\r\n", "\n").replace("\r", "\n")
        object.__setattr__(self, "text", normalized if self.text is not None or self.raw is not None else None)
        object.__setattr__(self, "parsed", _parse_dossier(normalized))

    def refusal(self, operation: Literal["rebuild", "review", "edit", "forget"]) -> str:
        if self.state == "unreadable":
            return "прежнее досье не прочитано"
        if self.newline is None:
            return "неподдерживаемые переводы строк"
        if self.parsed.reason:
            return self.parsed.reason
        if operation in ("review", "edit") and not self.parsed.complete:
            return "нет полного тела и защищённых разделов досье"
        if operation == "edit" and self.raw is None:
            return "нет исходного снимка байтов"
        if self.state == "damaged":
            if operation != "rebuild" or not self.parsed.complete or self.raw is None:
                return "Правки автора повреждены или не найдены"
            # Only the raw author suffix establishes damage versus literal U+FFFD.
            author = next(s for s in self.parsed.sections if s.title == "Правки автора")
            lines = self.raw.splitlines(keepends=True)
            count = 0
            for i, line in enumerate(lines):
                if count == author.start:
                    try:
                        b"".join(lines[i:]).decode("utf-8")
                    except UnicodeDecodeError:
                        return "Правки автора повреждены"
                    break
                count += len(line.decode("utf-8", errors="replace").replace("\r\n", "\n"))
            else:
                return "граница Правок автора не подтверждена"
        return ""

    def replace_body(self, body: str, revision_date: str | None = None) -> str:
        """Exact outside-body bytes through the existing UTF-8 CAS writer only."""
        why = self.refusal("edit")
        if why or self.raw is None or self.state != "ok":
            raise ValueError(why or "нет исходного снимка байтов")
        raw_text = self.raw.decode("utf-8")
        if raw_text.replace("\r\n", "\n") != self.text:
            raise ValueError("снимок байтов не соответствует тексту")
        start, end = self.parsed.body_span
        prefix = self.text[:start].replace("\n", self.newline)
        suffix = self.text[end:].replace("\n", self.newline)
        text = prefix + body.strip().replace("\r\n", "\n").replace("\n", self.newline) + suffix
        if revision_date is not None:
            text = frontmatter.with_scalar_preserving(text, REVISION_FIELD, revision_date)
        candidate = DossierLoad("ok", text)
        if not candidate.parsed.complete or candidate.refusal("review"):
            raise ValueError(candidate.parsed.reason or "ответ не образует полное досье")
        return text


def load_dossier(path: pathlib.Path) -> DossierLoad:
    """Один снимок байтов: строго декодируем ВЕСЬ файл, затем допускаем замену.

    Повреждение в конце тела тоже повреждение, даже если шапка цела.
    Отсутствие и ошибка доступа различаются; пустой файл — прочитан успешно.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return DossierLoad("missing")
    except OSError:
        return DossierLoad("unreadable")
    state: Literal["ok", "damaged"] = "ok"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        state = "damaged"
        text = raw.decode("utf-8", errors="replace")
    # Сохраняем перевод строк, который раньше делал Path.read_text.
    return DossierLoad(state, text, raw)


def read_fingerprint(source: pathlib.Path | DossierLoad) -> str:
    loaded = load_dossier(source) if isinstance(source, pathlib.Path) else source
    return frontmatter.scalar_field(loaded.text or "", "отпечаток")


def kept_from_night(text: str | None) -> str:
    """Почему тело досье — не голый вывод ночи; пусто — вывод ночи без руки
    человека, ночь соберёт тему заново из графа. Признаки `render` — отпечаток в
    шапке, раздел «Источники» и раздел «Правки автора» (его `render` пишет
    всегда: нет раздела — его снял человек, out1 Sonnet I1); рука человека —
    непустые «Правки автора», поле `ревизия:`, метка в теле. Правку тела без
    этих следов не видно — поэтому «забыть» удаляет досье с копией. Переводы
    строк — как у `load_dossier`: CRLF не прячет правки автора (вход n845-in1
    DS C1); заголовок раздела — любого уровня, как у `split_dossier`."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    loaded = DossierLoad("ok", text)
    sources, manual = loaded.parsed.sources, loaded.parsed.manual
    # Keep the old 600-character visibility exclusion for deletion. The shared
    # header reader is more precise; recognizing more metadata grants no new
    # eligibility to forget a stored file.
    if not read_fingerprint(loaded) or not re.search(r"^отпечаток:\s*\S+", t[:600], re.M):
        return "нет отпечатка ночи"
    if sources is None or manual is None:
        return "нет раздела «Источники» или «Правки автора»"
    if manual not in ("", "—"):
        return "есть правки автора"
    if revision(t):
        return "тело правила облачная ревизия"
    if loaded.parsed.complete and graph_names.has_speaker_actor(loaded.parsed.body):
        return "в теле метка диаризации"
    if why := loaded.refusal("forget"):
        return why
    if not loaded.parsed.complete:
        return "нет полного тела досье ночи"
    for section in loaded.parsed.sections:
        if section.title in ("Источники", "Правки автора"):
            line = t[section.start:section.content]
            hashes = len(line) - len(line.lstrip("#"))
            if not 1 <= hashes <= 6:
                return "защищённый заголовок вне прежнего допуска удаления"
    if loaded.parsed.legacy_delete_veto:
        return "прежние границы разделов удерживают досье от удаления"
    return ""


def in_sync(loaded: DossierLoad, fp: str) -> bool:
    """Тело собрано по нынешнему составу источников: ночи — SAME, ревизии — можно
    править и ставить `ревизия:` (тело, отставшее от «забыть встречу», не замораживается;
    финальный круг n841 Opus C1)."""
    return loaded.state != "missing" and read_fingerprint(loaded) == fp



# ─────────────────────────── состав и план ночи ───────────────────────────

COMPOSITION_FIELD = "состав"       # встречи, по которым собрано тело
REJECTED_FIELD = "не_принято"      # встречи, чью добавку отверг гейт: в «новые» не возвращаются
GROW_LIMIT = 12_000                # тело длиннее после добавки — пересобрать, а не дописывать
ADD_CHARS = 1_300                  # замер пилота 30.10: столько добавляет одна встреча

Action = Literal["skip", "add", "rebuild", "hold"]
Reason = Literal["new", "forced", "unreadable", "unparsed", "no_composition", "gone", "same",
                 "labeled", "grown", "damaged", "appendable"]


@dataclass(frozen=True)
class Plan:
    action: Action
    reason: Reason
    new: tuple[str, ...] = ()
    gone: tuple[str, ...] = ()


def _source_meetings(sources: str, schema: GraphSchema) -> list[str]:
    """Встречи из раздела «Источники» в виде `render`: `- **Вид:** [[a]] · [[b]]`."""
    out = []
    for line in sources.splitlines():
        m = re.match(r"\s*-\s*\*\*([^*]+):\*\*", line)
        if m and schema.is_meeting_folder(m.group(1).strip()):
            out += [graph_names.link_target_stem(x.group(1)) for x in LINK_RE.finditer(line)]
    return [x for x in out if x]


def composition(text: str | None, *, schema: GraphSchema) -> list[str] | None:
    """Встречи, по которым собрано тело: поле `состав:`, без поля — встречи раздела
    «Источники» (досье, собранные до поля). None — состав не читается: досье без
    «Источников» собрано руками (n843-in3 I3), как и `состав:` без значения (out1 Sonnet I1)."""
    if text and frontmatter.has_field(text, COMPOSITION_FIELD):
        names = frontmatter.list_field(text, COMPOSITION_FIELD)
        # пустой состав пишется только `[]`; пустое значение, `null`, комментарий —
        # не «встреч нет», а «не читается»: иначе все встречи темы стали бы новыми (out2 Haiku I2)
        if not names and not re.fullmatch(r"\[\s*\](?:\s+#.*)?", frontmatter.scalar_field(text, COMPOSITION_FIELD)):
            return None
        return [graph_names.link_target_stem(n) for n in names]
    sources = split_dossier(text or "")[2]
    return None if sources is None else _source_meetings(sources, schema)


def with_composition(text: str, meetings: list[str]) -> str:
    """Записать состав тела; зовёт только та запись, что и так пишет досье (n843-in3 M5)."""
    return frontmatter.set_list_field(text, COMPOSITION_FIELD, sorted(meetings))


def _canon(name: str, snap: GraphSnapshot) -> str:
    """Имя встречи → живой узел: цепочка заглушек до живого файла, кольцо не вешает."""
    seen = {name}
    while name not in snap.files and name in snap.redirects:
        name = snap.redirects[name]
        if name in seen:
            break
        seen.add(name)
    return name


def membership_delta(composed: list[str], rejected: list[str], members: list[str],
                     snap: GraphSnapshot, *, schema: GraphSchema,
                     on_disk: bool) -> tuple[list[str], list[str]]:
    """(новые встречи темы, ушедшие встречи состава). Ушла — файла нет в снимке, при
    `on_disk=True` (сверка под замком) и на диске, или живая встреча выпала из темы:
    ссылку на ядро сняли, тело её пересказывать не вправе (out1 Haiku I2). Редко: за
    неделю состав кластера сменился у 92 тем из 906, у остальных 814 правки ядер и общих
    узлов встреч не уводили (замер №842, 30.10). Заглушки — по `GraphSnapshot`:
    одиночная стрелка судится по канону, развилка, заглушка без цели и кольцо — не уход.
    Живые узлы других видов уходом не считаются; имя без файла и без заглушки — уход
    любого вида: состав пишет только встречи (out5 Haiku M1). Новое ядро без новой
    встречи — не новое (n843-in2 C1, I5)."""
    had = {_canon(n, snap) for n in composed}
    skip = had | {_canon(n, snap) for n in rejected}
    meetings = {m for m in members
                if m in snap.files and schema.is_meeting_folder(snap.files[m].get("kind", ""))}
    new = sorted(meetings - skip)
    def left(n: str) -> bool:
        if n in snap.files:     # живой файл раньше одноимённой заглушки (out2 Haiku I1)
            if on_disk and not snap.files[n]["path"].exists():
                return True
            return n not in meetings and schema.is_meeting_folder(snap.files[n].get("kind", ""))
        return n not in snap.stubs
    return new, sorted(n for n in had if left(n))


def plan(loaded: DossierLoad, members: list[str], snap: GraphSnapshot, *, schema: GraphSchema,
         on_disk: bool, force: bool = False) -> Plan:
    """Судьба тела досье этой ночью — одно решение на сборку, сверку под замком и
    ревизию (до него — пять мест по отпечатку с mtime, n843-in2). Первая подходящая
    строка:

    - файла нет или он пуст — rebuild;
    - не читается, тело без «## Сейчас» или без читаемого состава (досье руками) —
      hold: писать в тело неизвестного устройства нельзя (n843-in3 I3, out1 Sonnet I3);
    - ушла встреча состава — rebuild, с ревизией hold (снимает «забыть» или владелец);
    - `force` (`--full`) — rebuild, кроме тела с ревизией или меткой (n815-out7 I3);
    - новых встреч нет — skip: смена текста прежних членов тело не трогает;
    - новые есть: метка в теле — rebuild (n843-in3 I1), тело выросло бы за
      `GROW_LIMIT` — rebuild (I8), с ревизией оба — hold; иначе add, а у
      повреждённого файла (UTF-8 с заменой) — rebuild, с ревизией hold: дописка
      вернула бы в тело знаки замены (out1 Haiku C1). «Правки автора» на rebuild
      держит ночь (`preserve_manual`), её разбор заголовка — №856, до вызова `plan`.
    """
    if loaded.state == "missing":
        return Plan("rebuild", "new")
    if loaded.state == "unreadable":
        return Plan("hold", "unreadable")
    text = loaded.text or ""
    if not text.removeprefix("\ufeff").strip():
        return Plan("rebuild", "new")
    held = bool(revision(text))
    composed = composition(text, schema=schema)
    if composed is None:
        return Plan("hold", "no_composition")
    rejected = [graph_names.link_target_stem(n) for n in frontmatter.list_field(text, REJECTED_FIELD)]
    new, gone = membership_delta(composed, rejected, members, snap, schema=schema, on_disk=on_disk)
    # ушедшее идёт в план и при hold: отчёт обязан назвать забытое (out2 Haiku C3)
    if not loaded.parsed.complete or loaded.newline is None:
        return Plan("hold", "unparsed", tuple(new), tuple(gone))
    if gone:
        return Plan("hold" if held else "rebuild", "gone", tuple(new), tuple(gone))
    labeled = labeled_body(text)
    if force and not held and not labeled:
        return Plan("rebuild", "forced", tuple(new))
    if not new:
        return Plan("skip", "same")
    if labeled:
        return Plan("hold" if held else "rebuild", "labeled", tuple(new))
    if len(split_dossier(text)[1]) + ADD_CHARS * len(new) > GROW_LIMIT:
        return Plan("hold" if held else "rebuild", "grown", tuple(new))
    if loaded.state == "damaged":
        return Plan("hold" if held else "rebuild", "damaged", tuple(new))
    return Plan("add", "appendable", tuple(new))


# ─────────────────────────── индекс ───────────────────────────

def rebuild_index(graph: pathlib.Path, *, schema: GraphSchema,
                  read: Callable[[pathlib.Path], DossierLoad] = load_dossier) -> None:
    """Один актуальный обход графа для индекса; вызывающий держит его замок.

    Набор тем и fallback берутся из того же свежего scan/clusters, а не из
    старого JSON или очереди генерации. Досье вне кластеров остаются вне индекса.
    Ошибка чтения источника или досье прерывает сборку до публикации индексов.
    """
    if schema.dossier_dir is None:
        return
    folder = graph / schema.dossier_dir
    if not folder.resolve().is_relative_to(graph.resolve()):
        raise OSError(f"папка досье вне графа: {folder}")
    files, backlinks = scan(graph, schema=schema, strict=True)
    current = clusters(files, backlinks, schema=schema)
    fallback = {theme: {"источников": len(members),
                        "отпечаток": fingerprint(members, files),
                        "источники": " ".join(members)}
                for theme, members in current.items()}
    refresh_index(folder, current, read=read, fallback=fallback, strict=True)


def refresh_index(folder: pathlib.Path, themes: Iterable[str], *,
                  read: Callable[[pathlib.Path], DossierLoad] = load_dossier,
                  fallback: dict[str, dict] | None = None, strict: bool = False) -> None:
    """Оба индекса из текущих досье; вызывающий держит замок графа.

    Пути берутся с диска, не из JSON. Пустой набор тоже публикуется, а
    заменённый файл не наследует отпечаток и ключи старого тела.
    """
    wanted = set(themes)
    entries = []
    if strict:
        with os.scandir(folder) as items:
            paths = sorted(pathlib.Path(item.path) for item in items if item.name.endswith(".md"))
    else:
        paths = sorted(folder.glob("*.md"))
    for path in paths:
        theme = path.stem
        if path.name == INDEX_MD or theme not in wanted:
            continue
        loaded = read(path)
        if strict and loaded.state == "unreadable":
            raise OSError(f"досье недоступно для чтения: {path}")
        if loaded.state == "missing":
            continue
        defaults = (fallback or {}).get(theme, {})
        text = loaded.text or ""
        _, body = frontmatter.split(text)
        assembled = frontmatter.scalar_field(text, "собрано")
        try:
            assembled = date.fromisoformat(assembled).isoformat()
        except (TypeError, ValueError):
            assembled = date.today().isoformat()
        try:
            count = max(0, int(frontmatter.scalar_field(text, "источников")))
        except (TypeError, ValueError):
            count = defaults.get("источников", 0)
        keys = list(dict.fromkeys(keywords(theme + " " + defaults.get("источники", "")) + keywords(body)))[:24]
        entries.append({"тема": theme, "файл": f"{folder.name}/{path.name}",
                        "источников": count, "собрано": assembled,
                        "отпечаток": read_fingerprint(loaded) or defaults.get("отпечаток", ""),
                        "ключи": keys})
    write_index(folder, entries)


def write_index(folder: pathlib.Path, entries: list[dict]) -> None:
    """Два файла: json для поиска, md для человека и для grep.

    Индекс намеренно плоский и лексический: чтобы найти тему, не нужно ни
    эмбеддингов, ни запущенной Ollama. Семантика добирается уже поверх.
    """
    folder.mkdir(parents=True, exist_ok=True)
    # Прошлый прогон могли убить между write и replace: имя с pid больше не
    # перезаписывается следующим, и мусор жил бы в синхронизируемой папке
    # вечно (круг-4 по PR #438, DS Important 2).
    # Только ЧУЖОЕ И СТАРОЕ: сосед прямо сейчас может держать свой tmp между
    # write и replace, и снос уронил бы ему весь ночной проход
    # (круг-5 по PR #438, GLM Important 2).
    cutoff = time.time() - 3600
    for stale in list(folder.glob(f"{INDEX_JSON}.*.tmp")) + \
            list(folder.glob(f"{INDEX_MD}.*.tmp")):
        try:
            if stale.stat().st_mtime > cutoff:
                continue
            stale.unlink()
        except OSError:
            pass
    entries = sorted(entries, key=lambda e: e["тема"].lower())

    # Атомарная публикация каждого файла не заменяет общий замок графа:
    # refresh_index вызывается под ним и собирает актуальные метаданные.
    # Без tmp+replace обрыв на середине дал бы битый json.
    # Имя с pid: общий tmp давал двум одновременным прогонам смешать
    # байты и атомарно установить битый json — а load_index глотает
    # JSONDecodeError, и поиск сутки не видит ни одного досье
    # (круг-3 по PR #438, GLM Important 4).
    tmp = folder / f"{INDEX_JSON}.{os.getpid()}.tmp"
    # Эксклюзивное создание не идёт по подложенной ссылке. При занятом имени
    # не удаляем чужой tmp: в finally попадает только созданный нами файл.
    stream = tmp.open("x", encoding="utf-8")
    try:
        with stream:
            stream.write(json.dumps({"версия": 1, "обновлён": date.today().isoformat(),
                                     "досье": entries}, ensure_ascii=False, indent=" "))
        tmp.replace(folder / INDEX_JSON)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()

    lines = [
        "---", "type: индекс-досье", f"обновлён: {date.today().isoformat()}",
        f"досье: {len(entries)}", "tags: [досье, индекс]", "---", "",
        "# Индекс досье", "",
        "Список готовых сводок по темам. Поиск смотрит сюда **первым**:",
        "нашлась тема — ответ собирается из досье, а в граф идём только за деталями.",
        "",
        "| Тема | Источников | Обновлено | Ключи |",
        "|---|---|---|---|",
    ]
    for e in entries:
        keys = ", ".join(e["ключи"][:8])
        lines.append(f"| [[{folder.name}/{e['тема']}\\|{e['тема']}]] "
                     f"| {e['источников']} | {e['собрано']} | {keys} |")
    lines += ["", "## Как этим пользоваться", "",
              "1. Ищем тему по ключам в таблице выше.",
              "2. Открываем досье — там состояние, хроника, решения, открытые вопросы.",
              "3. За подробностями идём по ссылкам из раздела «Источники».", ""]
    tmp_md = folder / f"{INDEX_MD}.{os.getpid()}.tmp"
    stream = tmp_md.open("x", encoding="utf-8")
    try:
        with stream:
            stream.write("\n".join(lines))
        tmp_md.replace(folder / INDEX_MD)
    finally:
        with contextlib.suppress(OSError):
            tmp_md.unlink()


def load_index(folder: pathlib.Path) -> list[dict]:
    """Повреждённый производный индекс — без досье, но с поиском по графу.

    Проверяем поля поиска до нормализации: корректный JSON ещё не означает
    корректный индекс. Отказываем целиком, не меняя файл на диске. Отсутствие
    необязательных ключей и дополнительные метаданные совместимы со старым индексом.
    """
    p = folder / INDEX_JSON
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    entries = payload.get("досье", [])
    if not isinstance(entries, list):
        return []
    for entry in entries:
        if not isinstance(entry, dict):
            return []
        theme = entry.get("тема")
        if not isinstance(theme, str) or not theme.strip():
            return []
        keys = entry.get("ключи", [])
        if not isinstance(keys, list):
            return []
        if any(not isinstance(key, str) or not key.strip() for key in keys):
            return []
    # индекс прежней версии хранит цельные CJK-последовательности; запрос теперь
    # биграммный — нормализуем ключи при чтении, а не ослабляем сравнение
    # (luna r2, критика GLM r3): свежий индекс это не трогает
    def _cjk_long(k: str) -> bool:
        return len(k) > 2 and "\u4e00" <= k[0] <= "\u9fff"
    for e in entries:
        keys = e.get("ключи")
        if keys and any(_cjk_long(k) for k in keys):
            e["ключи"] = list(dict.fromkeys(
                bg for k in keys
                for bg in ([k[i:i + 2] for i in range(len(k) - 1)] if _cjk_long(k) else [k])))
    return entries


def lookup(folder: pathlib.Path, query: str, limit: int | None = 3) -> list[dict]:
    """Подбор досье под запрос — по пересечению основ. Без сети и без моделей.
    `limit=None` — все совпадения по убыванию счёта.

    Возвращает записи индекса с полем «счёт»; пусто — темы нет, работает
    обычный поиск по графу.
    """
    qkeys = keywords(query, limit=14)
    if not qkeys:
        return []
    hits = []
    for e in load_index(folder):
        keys = e.get("ключи", [])
        matched = {q for q in qkeys if any(_match(q, k) for k in keys)}
        if not matched:
            continue
        # доля запроса, покрытая темой, плюс вес за прямое упоминание темы
        score = len(matched) / len(qkeys)
        if _norm(e["тема"]) in _norm(query):
            score += 0.5
        hits.append({**e, "счёт": round(score, 3), "совпало": sorted(matched)})
    return sorted(hits, key=lambda e: -e["счёт"])[:limit]
