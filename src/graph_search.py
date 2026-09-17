"""Память по графу без сервера: поиск для живых подсказок и вопросов.

Демон на встрече спрашивал память у отдельного сервера (:8100) и без него
деградировал до узлов графа по стемам. Сервер выключается (решение 12.09):
прошлые договорённости демон находит сам — по файлам графа проекта, локально,
в бюджете живого контура (мгновенный ответ — 2,5 с).

Устройство. Индекс — файлы графа без архива встреч и копий документации
(EXCLUDE_DIRS: они дублируют заметки и втрое тяжелее всего остального): текст,
нормализованный текст, дата (из имени YYYY-MM-DD, иначе mtime), входящие
[[ссылки]]. Обновляется по mtime, повторный обход — фоном. Лексика: слова
запроса → стемы (graph_nodes.stem — тот же стеммер, что у узлов и у
приложения) и биграммы иероглифов; BM25-lite с IDF (насыщение частоты, слово
в пути файла ×3), корень покрытия запроса, свежесть (полураспад 90 дней), хаб
по входящим ссылкам, демпфер меток диаризации и сырых стенограмм. Семантика:
bge-m3-вектор на файл (шапка 12 000 знаков) через Ollama /api/embed, кэш в
data/ по mtime; на встрече считается только вектор запроса — файлы
индексируются вне записи (после встречи, ночью), пока вектора нет, файл
участвует лексически. Векторы — по блокам, не по файлам: файл режется по
заголовкам, длинные секции — по абзацам, каждый блок несёт хлебную крошку
«Файл → H1 → H2» (как поиск приложения: один вектор на файл по первым
12 000 знаков терял 63 % содержимого — решения встреч живут в конце, а
Ollama режет вход bge-m3 около 12 300 знаков). Счёт файла — лучший блок.
Слияние — RRF: ранги вместо несравнимых счётов, файл в
обоих списках складывает вклады. Досье по теме — первыми: сводка уже собрана,
восстанавливать её из фрагментов не надо. Разнообразие: одна встреча (заметка,
стенограмма, подсказки) не съедает все слоты. Один переход по [[ссылкам]] из
найденного узла: «что решил X» находит узел Люди/X, а решение живёт в заметке
встречи. Гейт честности: оба сигнала слабые → «⚠ …», и потребитель говорит
«в архиве нет», а не сочиняет.

Референсы дизайна: гибрид BM25 + вектор с временным слоем по Obsidian-графу,
важность узла по степени (LightRAG), RRF с временным слоем (Zep/Graphiti).
"""
from __future__ import annotations

import array
import dataclasses
import datetime as dt
import enum
import fcntl
import hashlib
import json
import math
import operator
import os
import pathlib
import re
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dossier  # noqa: E402
import frontmatter  # noqa: E402
import graph_nodes  # noqa: E402
import graphs  # noqa: E402
import redirects  # noqa: E402
import llm as _llm  # noqa: E402
import safe_write  # noqa: E402
import uuid  # noqa: E402

# Копии стенограмм и архив встреч: дублируют заметки встреч и узлы, но весят
# втрое больше всего графа — без них холодный обход укладывается в секунды.
# Пути относительно корня графа; сами документы (тезисы, концепции, ресёрчи)
# в «Документации» остаются — исключена только папка копий стенограмм.
EXCLUDE_DIRS = ("Встречи-архив", "Документация/Стенограммы встреч")
# Служебные файлы в корне графа (указатели, кандидаты, отчёты ревизий) — не память
_SERVICE_PREFIXES = ("_", "Служебное_")
BM25_B = 0.5               # нормализация длины: узел на 280 КБ не должен матчить всё подряд
HUB_CAP = 1.5              # потолок буста хаба: владелец графа упомянут в каждой встрече
NODE_DIRS = tuple(graph_nodes.norm(d) for d in graph_nodes.NODE_FOLDERS)
REFRESH_S = 60.0           # свежесть обхода: чаще смысла нет, граф пишется после встреч
CHUNK_CHARS = 4_000        # блок для эмбеддера: заведомо ниже потолка Ollama (~12 300 знаков)
MAX_CHUNKS = 12            # на файл: у узла новые встречи сверху — первые блоки самые свежие
EMBED_BATCH = 16
SIM_FLOOR = 0.35           # ниже — семантический шум, в список не берём
# Гейт честности «⚠» — правило проекта, то же, что у поиска приложения
# (ArchiveSearch: bestSim < 0.47 && bestCov < 0.66; 0,66 — «две иглы из трёх»):
# слабы ОБА сигнала. Без семантики (Ollama занята — на встрече это норма) гейт
# НЕ судит по одной лексике: замер 17.09 на рабочем графе (3 160 файлов) — ловушки
# «бюджет релиза», «витрина обуви», «риски погоды» дают 2 совпадения из 2 с той же
# массой IDF, что настоящие вопросы; ни доля, ни редкость слов их не разделяют
# (круг 1 DS C1 → круг 2 DS C1: любой порог по доле уверен где-то зря). Поэтому
# без семантики выдача идёт с «⚠» и своей причиной — потребитель подаёт её модели
# как ненадёжную или уходит к узлам, но не принимает за память. Порог 0,47 под
# «лучший блок из многих» перекрывается (ловушки 0,46–0,62, вопросы 0,49–0,74) —
# калибровка на размеченном наборе — отдельная карточка, не угадывание здесь.
LOW_SIM, LOW_COV = 0.47, 0.66
# «В архиве нет» — вердикт о проверенном архиве: пока векторы есть меньше чем у
# этой доли файлов индекса (кэш собирается вне встреч, по бюджету), низкий лучший
# косинус говорит о векторизованной части, а не об архиве — выдача «не проверена»,
# а не «слабая» (круг 3 по #577, DS критика 2).
SEM_SHARE_MIN = 0.8
VEC_RETRY_S = 30.0         # неудачная загрузка кэша не защёлкивается: повтор не чаще
BLOB_GRACE_S = 300.0       # блоб вне текущего и предыдущего поколения стирается, только когда старше: читатель мог прочитать манифест секунды назад
CHUNK_VERSION = 3          # правила нарезки — часть ключа кэша: сменились — кэш холодный (DS I2 r2).
# 3 — текст приводится к NFC при чтении, значит блоки NFD-заметок другие (GLM I2 r3)
MAX_CHUNKS_NODE = 24       # узлы (Люди/Системы/…): история длиннее, середина ценнее (GLM r2, критика 1)
HALFLIFE_DAYS = 90.0
# Частотный шум — один список на проект (dossier его уже держит: ключи тем и иглы
# запроса режутся одним ситом, круг 3 по #577, DS M2); здесь — вопросительные слова
# и двухбуквенные служебные, остальные двухбуквенные — термины («тз», «ии», «бд»).
# Отрицания «не»/«ни» остаются служебными: как подстроки они есть почти в каждом
# файле («нет», «неделя»), IDF≈0 — в ранг не вносят ничего, а покрытие завышают всем;
# «согласовано»/«не согласовано» различит фразовый поиск, не стоп-лист.
_STOP = dossier._STOP | {
    "что", "как", "где", "когда", "это", "нас", "наш", "наша", "наши", "есть",
    "про", "для", "или", "чем", "кто", "было", "быть", "графе", "граф", "мы",
    "решили", "the", "and", "what", "who", "how", "did", "for", "with",
    "по", "на", "из", "за", "от", "до", "не", "ни", "но", "же", "ли", "бы", "то",
    "та", "те", "ту", "вы", "ты", "он", "их", "им", "ей", "ею", "ее", "со", "во", "об",
    "ко", "уж", "да", "ну", "ах", "ох", "ок", "эм", "эй",
    "of", "to", "in", "on", "at", "by", "is", "it", "as", "or", "an",
    "be", "we", "do", "if", "so", "no", "up", "us", "my", "me", "he", "ok"}
_WORD_RX = re.compile(r"[А-Яа-яЁёA-Za-z0-9_-]{2,}")
_DATE_RX = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_MEETING_RX = re.compile(r"(20\d{2}-\d{2}-\d{2})[_ ]?(\d{4})?")
_WIKILINK_RX = re.compile(r"\[\[([^\]|#\n]+)")
_RAW_RX = re.compile(r"стенограмм|_live\.md$|transcript", re.IGNORECASE)
_PLACEHOLDER_RX = re.compile(r"^(?:собеседник|участник|спикер|speaker|participant)(?: ?\d+)?(?: .*)?$")
# Иероглифы пробелами не разделяются — «слова» из них не нарезать: берём
# скользящие биграммы (支付服务商 → 支付, 付服, 服务, 务商) — стандартный приём
# для языков без пробелов; блоки — как в бенче памяти.
CJK = (r"一-鿿㐀-䶿豈-﫿぀-ヿｦ-ﾟ가-힯"
       r"\U00020000-\U0002ee5f\U0002f800-\U0002fa1f\U00030000-\U000323af")
_CJK_RUN = re.compile(f"[{CJK}]+")
_FULLWIDTH = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}   # ＹｕＰａｙ → YuPay
_HEADING_RX = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.M)


def norm(s: str) -> str:
    """Регистр, ё→е, полноширинные латиница и цифры → обычные.

    Форму Unicode приводит `graph_nodes.norm` — одна нормализация на проект.
    Здесь её повторять нельзя: NFC меняет ДЛИНУ разложенного текста, а
    `snippet` по совпадению длин решает, из чего резать фрагмент, и NFD-заметка
    уехала бы в выдачу строчными буквами (DS, круг 2 по №291). Текст приводится
    к NFC один раз при чтении файла, в `_walk`."""
    return graph_nodes.norm(s).translate(_FULLWIDTH)


def norm_text(s: str) -> str:
    return " ".join(norm(s).split())


def cjk_grams(text: str) -> list[str]:
    grams: list[str] = []
    for run in _CJK_RUN.findall(text):
        grams += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return grams


def needles(query: str) -> tuple[list[str], list[str]]:
    """Иглы запроса: стемы слов и биграммы иероглифов, каждая по одному разу
    (повтор удваивал бы вклад в счёт). Пересечься списки не могут: слова — из
    латиницы, кириллицы и цифр, биграммы — только из иероглифов."""
    query = unicodedata.normalize("NFC", query).translate(_FULLWIDTH)   # ＹｕＰａｙ — слово, а не пропуск;
    # форма — до разрезки: разложенный «май» дал бы иглу «ми», а она подстрокой
    # ловит «ками» и «милионер» (GLM, круг 3 по №291)
    # двухбуквенные слова — термины («ИИ», «тз», «БД», «v2»), кроме служебных из
    # _STOP: отсев по регистру терял строчные аббревиатуры (круг 2 по #577, DS I6 / GLM M3)
    words = [graph_nodes.stem(w) for w in _WORD_RX.findall(query) if norm(w) not in _STOP]
    return list(dict.fromkeys(norm(w) for w in words if w)), list(dict.fromkeys(cjk_grams(query)))


REASON_EMBED = "модель эмбеддингов занята или не ответила"
REASON_CACHE = "кэш векторов собран не весь"


class Verdict(str, enum.Enum):
    """Состояние выдачи — одно значение для всех потребителей (демон, бенч, CLI).
    До круга 3 по #577 оно жило в строке с «⚠»/«не найдено», и три контура
    разбирали её префиксом: досье перед «⚠» глушили гейт, а «Ничего не найдено»
    без семантики читалось как доказанное отсутствие (DS C1 / GLM C1)."""
    CONFIDENT = "confident"       # семантика проверила, совпадения сильные
    WEAK = "weak"                 # проверила: слабы оба сигнала — скорее всего в архиве нет
    UNVERIFIED = "unverified"     # семантика не отработала или кэш собран не весь — по словам, не проверено
    EMPTY = "empty"               # ничего, и это проверено


def verdict(cov: float, sim: float, sem_used: bool, sem_share: float = 1.0) -> Verdict:
    """Вердикт по свидетельствам: без семантики уверенности нет вовсе — одна
    лексика на большом графе не отличает вопрос от ловушки (замер 17.09); с ней
    сильный любой из сигналов — уверенно (правило приложения), слабы оба — «в
    архиве нет», но только если проверена достаточная доля архива (SEM_SHARE_MIN)."""
    if not sem_used:
        return Verdict.UNVERIFIED
    if cov >= LOW_COV or sim >= LOW_SIM:
        return Verdict.CONFIDENT
    if sem_share < SEM_SHARE_MIN:
        return Verdict.UNVERIFIED
    return Verdict.WEAK


def file_date_ts(rel: str, mtime: float) -> float:
    """Дата файла: YYYY-MM-DD из имени (встречи, дневники) точнее mtime облака."""
    m = _DATE_RX.search(rel)
    if m:
        try:
            return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                               tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return mtime


def recency_factor(ts: float | None, now: float | None = None) -> float:
    """1,0 сегодня → 0,5 на бесконечности; None (вечные узлы) — нейтрально."""
    if ts is None:
        return 1.0
    age = max(0.0, ((now or time.time()) - ts) / 86400.0)
    return 0.5 + 0.5 * 2.0 ** (-age / HALFLIFE_DAYS)


def hub_factor(in_degree: int) -> float:
    """Логарифм входящих ссылок с потолком: хаб выше свежесозданного узла, но
    не давит точный текст, а узел владельца (ссылка из каждой встречи) не
    всплывает в любой выдаче только за счёт степени."""
    return min(HUB_CAP, 1.0 + 0.15 * math.log1p(max(0, in_degree)))


def idf(doc_freq: int, n_docs: int) -> float:
    return math.log1p(max(0, n_docs - doc_freq + 1) / (doc_freq + 1))


def bm25_lite(text_hits: int, path_hits: int, weight: float, path_weight: float = 3.0,
              len_norm: float = 1.0) -> float:
    """Насыщение частоты логарифмом, частота — на нормализованную длину
    документа (BM25, b = BM25_B): узел-хаб на сотни строк встреч собирает
    любое слово запроса, и без нормализации он обгонял бы заметку, где
    вопрос действительно обсуждали. Слово в пути файла — сильнейший сигнал (×3)."""
    score = weight * (1.0 + math.log1p(text_hits / max(len_norm, 1e-9))) if text_hits else 0.0
    return score + path_weight * weight * path_hits


def coverage_factor(matched: int, total: int) -> float:
    """√(покрытие): файл со всеми словами запроса заметно выше частичного, но
    частичные не выкидываются — STT в стенограммах теряет термины."""
    return 1.0 if total <= 0 else (max(0, matched) / total) ** 0.5


def raw_dampener(rel: str) -> float:
    """×0,75 сырым стенограммам: дистиллят при равной релевантности выше."""
    return 0.75 if _RAW_RX.search(rel) else 1.0


def placeholder_factor(base: str) -> float:
    """Узел-метка диаризации («Собеседник 3») — не человек и не хаб: ×0,2."""
    return 0.2 if _PLACEHOLDER_RX.match(base or "") else 1.0


def meeting_key(rel: str) -> str | None:
    """Ключ встречи из пути (дата[_время]): одна встреча живёт 3–4 файлами."""
    m = _MEETING_RX.search(rel)
    return (m.group(1) + (m.group(2) or "")) if m else None


def is_node_path(rel: str) -> bool:
    parts = norm(rel).replace("\\", "/").split("/")
    return len(parts) >= 2 and parts[-2] in NODE_DIRS and not parts[-1].startswith("_")


def stub_base(text: str) -> str:
    """Файл — заглушка-редирект после слияния узлов? Тогда база канона, иначе "".

    Обход и так держит текст в руках, поэтому распознание стоит один разбор на
    ИЗМЕНЁННЫЙ файл, а не на запрос. Замер 17.09 на рабочем графе: 404 заглушки
    с целью из 3199 файлов, 1048 входящих ссылок вели на них вместо канонов."""
    if not (redirects.is_redirect_stub(text) or redirects.is_merged(text)):
        return ""
    target = redirects.stub_target(text)
    if not target:
        return ""
    leaf = pathlib.PurePosixPath(target.split("|")[0].strip()).name
    return norm_text(leaf[:-3] if leaf.casefold().endswith(".md") else leaf)


def canon_bases(docs: Iterable[Doc]) -> dict[str, str]:
    """База заглушки → база живого канона, цепочки развёрнуты, циклы отброшены.

    Ссылка на слитый узел должна считаться ссылкой на канон: иначе буст хаба
    достаётся мёртвому файлу, а переход через него отбрасывается фильтром узлов
    и ответ молча обедает.

    ДОЛГ, названный вслух (вердикты DS и GLM 17.09). Это ВТОРАЯ реализация
    правила «куда ведёт заглушка»: первая — `graph_updater.follow_stubs`, она
    ходит по путям и читает диск. `graph_links.LinkResolver` тут ни при чём —
    он резолвит «как Obsidian», то есть В саму заглушку, и снимок принимает
    (`notes=`), так что «ему нужен диск» — неверное обоснование, его тут не
    было. Карта живёт по базам без папки, потому что по базам ключуется весь
    поиск (`wiki_targets`, `_indeg`, `by_base`), и переезд на пути меняет их
    разом — это №292. Известная цена промедления: ссылка, назвавшая папку
    явно, здесь неотличима от голой.

    Имя переписывается, только если ЖИВОГО файла с таким именем нет вовсе.
    Однофамилец бывает не дублем: `Ядра/Отчёт по аварии` слит в другое ядро, а
    `Досье/Отчёт по аварии` — живая сводка по той же теме, и ссылка ведёт к ней
    (замер 17.09: без этой оговорки правка отнимала 4 перехода, давая 43)."""
    live = live_owners(docs)
    # «# X → [[X]]» сюда не попадает: ни живой файл, ни звено цепочки. В живых она
    # собирала бы на себя чужие ссылки, в звеньях — вытесняла настоящий редирект
    # той же базы (DS, круг 2 по №291)
    cands: dict[str, list[Doc]] = {}
    for d in docs:
        if d.stub_to and d.stub_to != d.base:
            cands.setdefault(d.base, []).append(d)
    # звено цепочки — по СТАБИЛЬНОМУ представителю базы: mtime двигают git checkout,
    # копия графа и синк облака, а путь не двигается ничем (DS, круг 4)
    link = {base: min(ds, key=lambda d: d.rel).stub_to for base, ds in cands.items()}

    out: dict[str, str] = {}
    for base, ds in cands.items():
        if base in live:   # под этим именем есть и живой файл — ссылка про него
            continue
        for d in sorted(ds, key=lambda x: x.rel):
            seen = {base}
            cur = d.stub_to
            while cur not in live and cur in link and cur not in seen:
                seen.add(cur)
                cur = link[cur]
            if cur not in seen and cur in live:
                out[base] = cur   # первый кандидат, чья цепочка кончилась живым
                break
            # иначе цикл, самопетля или оборванная стрелка: пробуем следующий
            # редирект той же базы, а не отдаём имя мёртвой цели (DS, круг 4)
    return out


def live_owners(docs: Iterable[Doc]) -> dict[str, Doc]:
    """База → живой документ под этим именем, выбор детерминированный.

    Свежайший, при равной дате — меньший путь. Тай-брейк обязателен: у узлов
    дата берётся из mtime, а его двигают `git checkout`, копия графа целиком и
    синк облака, — одинаковые даты у тёзок штатны, и без второго ключа хозяин
    имени решался порядком обхода каталога (DS, круг 4 по №291)."""
    out: dict[str, Doc] = {}
    for d in docs:
        if d.stub_to:
            continue
        cur = out.get(d.base)
        if cur is None or (d.date_ts, cur.rel) > (cur.date_ts, d.rel):
            out[d.base] = d
    return out


def name_owner(base: str, stub_to: str, live: dict[str, Doc], canon: dict[str, str]) -> Doc | None:
    """Кто отвечает за это имя: живой тёзка, иначе канон за стрелкой заглушки.

    Одно правило на всех потребителей. Приоритет тёзки тот же, что в
    `canon_bases`: есть под именем живой файл — имя про него, и стрелка
    мёртвого дубля его не перебивает. Круг 2 по №291 поймал, как два
    экземпляра этого правила в одном файле разошлись: подмена в выдаче
    отдавала слот живой цели стрелки, а переходы — тёзке."""
    return live.get(base) or live.get(canon.get(base, stub_to))


def wiki_targets(text: str) -> set[str]:
    """Цели [[ссылок]] → базовые имена узлов (без папки и текста ссылки)."""
    out: set[str] = set()
    for target in _WIKILINK_RX.findall(text):
        base = target.strip().split("/")[-1].strip()
        if base:
            out.add(norm_text(base))
    return out


def rrf_merge(ranked: Sequence[Sequence[str]], weights: Sequence[float] | None = None,
              k: float = 60.0) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: позиции в своих списках → единый ранк."""
    weights = list(weights or [1.0] * len(ranked))
    fused: dict[str, float] = {}
    for w, lst in zip(weights, ranked):
        for rank, key in enumerate(lst):
            fused[key] = fused.get(key, 0.0) + w / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: -kv[1])


def diversify(scored: list[tuple[float, str]], limit: int, penalty: float = 0.45) -> list[str]:
    """Жадный отбор: повторная встреча в выдаче — счёт ×penalty за каждый дубль."""
    pool = sorted(scored, key=lambda x: -x[0])
    picked: list[str] = []
    used: dict[str, int] = {}
    while pool and len(picked) < limit:
        best_i, best_eff = 0, -1.0
        for i, (score, rel) in enumerate(pool):
            key = meeting_key(rel)
            eff = score * (penalty ** used.get(key, 0) if key else 1.0)
            if eff > best_eff:
                best_i, best_eff = i, eff
        _, rel = pool.pop(best_i)
        key = meeting_key(rel)
        if key:
            used[key] = used.get(key, 0) + 1
        picked.append(rel)
    return picked


def snippet(text: str, rx: re.Pattern, chars: int, rare_first: Sequence[str], dense: bool = False) -> str:
    """Окно вокруг самого информативного места: максимум РАЗНЫХ игл запроса в
    окне (первое совпадение часто в шапке, ответ — в середине). Короткий файл
    (узел) — целиком; плотный дистиллят — окно ×1,5; длинный запрос (≥ 800) —
    второй фрагмент вокруг редчайшей иглы вне окна."""
    if dense:
        chars += chars // 2
    if len(text) <= chars + chars // 2:
        return " ".join(text.split())
    low = norm(text)
    matches = list(rx.finditer(low))[:60]
    if not matches:
        return ""
    best = matches[0]
    if rare_first and len(matches) > 1:
        best_kinds = -1
        for m in matches:
            window = low[m.start():m.start() + chars]
            kinds = sum(1 for s in rare_first if s in window)
            if kinds > best_kinds:
                best_kinds, best = kinds, m
    # lower() у отдельных символов меняет длину — тогда режем из нормализованной копии
    source = text if len(low) == len(text) else low
    start, end = max(0, best.start() - 150), best.start() + chars
    frag = " ".join(source[start:end].split())
    if chars >= 800:
        for stem in rare_first:
            pos = low.find(stem)
            while pos != -1 and start <= pos < end:
                pos = low.find(stem, end)
            if pos != -1:
                s2 = max(0, pos - chars // 4)
                return f"…{frag}… …{' '.join(source[s2:pos + chars // 4].split())}…"
    return f"…{frag}…"


def chunks(stem: str, text: str, chars: int = CHUNK_CHARS, limit: int = MAX_CHUNKS) -> list[str]:
    """Блоки файла для эмбеддера: по заголовкам markdown, длинные секции — по
    абзацам до `chars`, сплошной абзац — по длине; каждый блок с хлебной
    крошкой «Файл → H1 → H2» — «ну да, давайте так» сам по себе не значит
    ничего. Секция короче 40 знаков не выбрасывается, а приклеивается к
    соседнему блоку («## Решения» из одной строки — самое ценное). Не больше
    `limit` блоков на файл: половина с начала (у узла новые встречи сверху) и
    половина с конца (у заметки решения в хвосте) — срез по хвосту терял бы
    именно их (круг 1 по #577, DS I3 / GLM M7); у узлов лимит вдвое больше
    (MAX_CHUNKS_NODE): середина их истории — то, о чём спрашивают через полгода."""
    body = frontmatter.split(text)[1]
    sections: list[tuple[str, str]] = []      # (крошка, текст секции)
    crumbs = {1: "", 2: "", 3: ""}
    pos = 0
    heads = list(_HEADING_RX.finditer(body))
    def crumb() -> str:
        return " → ".join(x for x in (stem, crumbs[1], crumbs[2], crumbs[3]) if x)
    for i, m in enumerate(heads):
        sections.append((crumb(), body[pos:m.start()]))
        level = len(m.group(1))
        crumbs[level] = m.group(2).strip()
        for deeper in range(level + 1, 4):
            crumbs[deeper] = ""
        pos = m.end()
    sections.append((crumb(), body[pos:]))
    out: list[str] = []
    for crumb_text, sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) < 40:
            if out and len(out[-1]) + len(sec) + 1 <= chars + 80:
                out[-1] = f"{out[-1]}\n{crumb_text.rsplit(' → ', 1)[-1]}: {sec}"
            else:
                out.append(f"{crumb_text}\n{sec}")
            continue
        pieces: list[str] = []
        if len(sec) <= chars:
            pieces.append(sec)
        else:
            buf = ""
            for para in re.split(r"\n\s*\n", sec):
                para = para.strip()
                if not para:
                    continue
                while len(para) > chars:                 # сплошной абзац — по длине
                    if buf:
                        pieces.append(buf)
                        buf = ""
                    pieces.append(para[:chars])
                    para = para[chars:]
                if buf and len(buf) + len(para) + 2 > chars:
                    pieces.append(buf)
                    buf = para
                else:
                    buf = f"{buf}\n\n{para}" if buf else para
            if buf:
                pieces.append(buf)
        for piece in pieces:
            out.append(f"{crumb_text}\n{piece}")
    if len(out) > limit:
        head = limit // 2
        out = out[:head] + out[len(out) - (limit - head):]
    return out


@dataclasses.dataclass
class Doc:
    path: str
    rel: str
    mtime: float
    text: str
    low: str
    date_ts: float
    base: str          # нормализованное имя файла без расширения — цель [[ссылок]]
    body: str = ""     # текст без YAML-шапки — для фрагментов выдачи (шапка модели не нужна)
    stub_to: str = ""  # заглушка-редирект: база канона, куда она ведёт (иначе пусто)


@dataclasses.dataclass
class Result:
    """Выдача как значение: потребитель судит по `status`, модели отдаёт
    `fragments`, человеку — `text`; маркеры в тексте никто не разбирает."""
    blocks: list[str]
    total: int
    status: Verdict = Verdict.EMPTY
    ready: bool = True
    dossiers: list[str] = dataclasses.field(default_factory=list)
    sem_used: bool = False      # семантика посчиталась (вектор запроса получен)
    query: str = ""
    reason: str = ""            # почему UNVERIFIED — одно поле, три рендера (why_low, render, статус нити; GLM M2 r5)

    @property
    def low_conf(self) -> bool:
        return self.status in (Verdict.WEAK, Verdict.UNVERIFIED)

    @property
    def why_low(self) -> str:
        """Причина «⚠» человеку: без семантики — не «в архиве нет», а «не проверено»."""
        if self.status is Verdict.UNVERIFIED:
            return f"Совпадения не проверены семантикой ({self.reason}) — найденное по словам, доверять с оглядкой"
        if self.status is Verdict.WEAK:
            return "Похоже, в архиве об этом почти ничего нет (слабые совпадения)"
        return ""

    @property
    def empty(self) -> bool:
        return not self.blocks and not self.dossiers

    @property
    def fragments(self) -> str:
        """Досье и фрагменты без шапки и маркеров — в промпт: «⚠ …» в промпте
        модель читает как указание отказаться (круг 3 по #577, DS I3)."""
        return "\n\n".join(self.dossiers + self.blocks)

    @property
    def text(self) -> str:
        return render(self, self.query)


def _dot(a, b) -> float:
    return math.sumprod(a, b) if hasattr(math, "sumprod") else sum(map(operator.mul, a, b))


def _unit(vec: Sequence[float]) -> array.array:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", (x / n for x in vec))


class GraphSearch:
    """Индекс одного графа и поиск по нему. Один экземпляр на процесс и граф
    (см. shared()); обновление индекса и поиск — из разных потоков.

    Владение: `_docs` пишет только `_walk` (обходчики сериализует `_scan_lock`),
    подмена и чистка — под `_lock`; читатели берут снимок под `_lock` и дальше
    работают со списком. `_vecs` пишут `load_vectors`/`embed_pending`/`_walk`
    (уборка исчезнувших) — тоже под `_lock`. Кэш векторов на диске: манифест с
    именем неизменяемого блоба (запись — новый блоб, потом манифест через
    tmp+replace, старые блобы стираются после) — читатель никогда не видит
    полузаписанной пары; писателей сериализует flock рядом с манифестом.
    """

    def __init__(self, graph_dir: pathlib.Path, cfg: dict | None = None, *,
                 data_dir: pathlib.Path | None = None,
                 exclude: Iterable[str] = EXCLUDE_DIRS,
                 embed: Callable[[list[str], float], list[list[float]]] | None = None,
                 now: Callable[[], float] = time.time) -> None:
        self.graph = pathlib.Path(graph_dir)
        self.cfg = cfg or {}
        self.exclude = tuple(exclude)
        self._now = now
        self._embed_fn = embed
        self._docs: dict[str, Doc] = {}
        self._indeg: dict[str, int] = {}
        self._canon: dict[str, str] = {}     # база заглушки → база канона (см. canon_bases)
        self._refreshed_at = 0.0
        self._lock = threading.RLock()       # индекс и векторы
        self._scan_lock = threading.Lock()   # один обход за раз
        self._vecs: dict[str, tuple[float, list[array.array]]] = {}   # путь → (mtime, векторы блоков)
        base = pathlib.Path(data_dir) if data_dir else graphs.DATA_ROOT / "data"
        # имя кэша — по пути графа, не по имени папки: два графа «Работа» в разных
        # vault-ах дрались бы за один файл (круг 1 по #577, GLM M6)
        tag = hashlib.sha256(str(self.graph.resolve()).encode("utf-8")).hexdigest()[:8]   # имя файла по пути, не подпись; sha256 — чтобы CI не спорил
        self._vec_manifest = base / "graph_search" / f"{self.graph.name}-{tag}.json"
        self._vecs_loaded = False
        self._vecs_tried_at: float | None = None   # None — не пробовали: часы могут считать от нуля (DS M3 r3)
        self._vecs_key: str | None = None          # ключ, под который собраны векторы в памяти
        self._manifest_seen = 0.0   # mtime манифеста при последней загрузке: чужая запись — перечитать
        self.note = ""              # последнее «почему не сделали» для CLI и журнала

    # ---------------------------------------------------------------- индекс
    @property
    def ready(self) -> bool:
        return bool(self._docs)

    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def vectors(self) -> int:
        return len(self._vecs)

    def _fresh(self) -> bool:
        return self._now() - self._refreshed_at < REFRESH_S

    def cache_key(self) -> str:
        """Всё, что определяет содержимое кэша, кроме файлов: модель эмбеддингов и
        правила нарезки. Сменилось — кэш холодный целиком (круг 2 по #577, DS I2)."""
        model = str((self.cfg.get("sufler") or {}).get("embed_model", "bge-m3:latest"))
        return f"{model}|chunks{CHUNK_VERSION}|{CHUNK_CHARS}|{MAX_CHUNKS}|{MAX_CHUNKS_NODE}"

    def refresh(self, force: bool = False) -> bool:
        """Обход графа по mtime: новые и изменённые файлы перечитываются,
        исчезнувшие уходят. Свежий индекс без force не трогается; параллельный
        обход не дублируется — второй вызов уходит сразу. -> обход был ли."""
        if not force and self._fresh():
            return False
        if not self._scan_lock.acquire(blocking=force):
            return False
        try:
            self._walk()
            self._refreshed_at = self._now()
            return True
        finally:
            self._scan_lock.release()

    def _walk(self) -> None:
        seen: set[str] = set()
        fresh: dict[str, Doc] = {}
        changed = False
        root = str(self.graph)
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and (f"{rel_dir}/{d}" if rel_dir else d) not in self.exclude]
            for fn in filenames:
                if not fn.endswith(".md") or (not rel_dir and fn.startswith(_SERVICE_PREFIXES)):
                    continue
                path = os.path.join(dirpath, fn)
                seen.add(path)
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    continue
                cached = self._docs.get(path)
                if cached is not None and cached.mtime == mtime:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        # одна форма Unicode на весь конвейер: имя файла от macOS
                        # приходит разложенным, текст заметки — собранным
                        text = unicodedata.normalize("NFC", fh.read())
                except OSError:
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                try:
                    body = frontmatter.split(text)[1]
                except ValueError:
                    body = text
                fresh[path] = Doc(path, rel, mtime, text, norm(text), file_date_ts(rel, mtime),
                                  norm_text(os.path.splitext(fn)[0]), body, stub_base(text))
                changed = True
        gone = [p for p in self._docs if p not in seen]
        if not fresh and not gone:
            return
        with self._lock:
            for p in gone:
                self._docs.pop(p, None)
                self._vecs.pop(p, None)          # вектор исчезнувшего файла — вместе с ним (DS M3 / GLM M10)
            self._docs.update(fresh)
            snapshot = list(self._docs.values())
        if changed or gone:
            # входящие ссылки — по снимку вне замка: обход 28 МБ текста под замком
            # заставлял бы каждый поиск встречи ждать (GLM M5)
            canon = canon_bases(snapshot)
            indeg: dict[str, int] = {}
            for d in snapshot:
                if d.stub_to:        # единственная ссылка заглушки — служебная стрелка на канон
                    continue
                for target in wiki_targets(d.text):
                    target = canon.get(target, target)   # ссылка на слитый узел — ссылка на канон
                    indeg[target] = indeg.get(target, 0) + 1
            with self._lock:
                self._indeg = indeg
                self._canon = canon

    # --------------------------------------------------------------- векторы
    def _embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        if self._embed_fn is not None:
            return self._embed_fn(texts, timeout)
        if not self.cfg:
            return []
        try:
            return _llm.embed(self.cfg, texts, keep_alive="30m", timeout=timeout)
        except Exception:  # noqa: BLE001 — сервер занят или лежит: лексика и без него
            return []

    def load_vectors(self) -> int:
        """Кэш векторов с диска: манифест (путь, mtime, число блоков, имя блоба) +
        неизменяемый плоский float32. Неудача не защёлкивается — повтор не чаще
        VEC_RETRY_S: защёлка гасила семантику на всю встречу после одного
        совпадения с писателем (круг 1 по #577, DS I1 / GLM I1)."""
        self._drop_foreign_vectors()
        try:
            seen = self._vec_manifest.stat().st_mtime
        except OSError:
            seen = 0.0
        if self._vecs_loaded and seen == self._manifest_seen:
            return len(self._vecs)          # чужая запись (ночь, апдейтер) — манифест новее, перечитаем (GLM M6 r2)
        if self._vecs_tried_at is not None and self._now() - self._vecs_tried_at < VEC_RETRY_S:
            return len(self._vecs)          # и после загрузки тоже: чужой ключ на диске иначе читался бы каждым поиском (GLM M3 r3)
        loaded = self._read_cache(retry=True)
        if loaded is None:
            self._vecs_tried_at = self._now()   # штамп — только на настоящую неудачу, не на гонку с уборкой (DS I4 r2)
            return len(self._vecs)
        entries, flat, dim, seen = loaded       # mtime прочитанного манифеста: после повтора — свежего (GLM M3 r3)
        got: dict[str, tuple[float, list[array.array]]] = {}
        off = 0
        for path, mtime, n in entries:
            got[path] = (float(mtime), [flat[off + i * dim:off + (i + 1) * dim] for i in range(int(n))])
            off += int(n) * dim
        with self._lock:
            for path, entry in got.items():
                cur = self._vecs.get(path)
                if cur is None or cur[0] < entry[0]:
                    self._vecs[path] = entry       # свежее по mtime главнее, откуда бы ни пришло
            self._vecs_loaded = True
            self._vecs_tried_at = None
            self._manifest_seen = seen
            return len(self._vecs)

    def _drop_foreign_vectors(self) -> None:
        """Ключ кэша сменился в живом процессе (модель эмбеддингов в конфиге) —
        векторы в памяти считаны другой моделью, косинус с вектором запроса новой
        — шум, а не свидетельство (DS I1 r3). Сброс до чтения кэша и до сборки.
        Сегодня это страховка: демон читает cfg один раз на старте и не мутирует
        его, чужой кэш на диске под старым ключом сюда не проходит; триггер
        станет боевым с хот-релоадом конфига (GLM r5, критика 2)."""
        key = self.cache_key()
        with self._lock:
            if self._vecs_key not in (None, key):
                self._reset_vectors()
            self._vecs_key = key

    def _reset_vectors(self) -> None:
        """До холодного состояния — все поля памяти о кэше разом: штамп неудачи
        поштучно забывали, и после смены ключа поиск до VEC_RETRY_S шёл без
        векторов при готовом кэше на диске (DS M1 r4)."""
        with self._lock:
            self._vecs.clear()
            self._vecs_loaded = False
            self._manifest_seen = 0.0
            self._vecs_tried_at = None

    def _read_cache(self, retry: bool):
        """Пара «манифест → блоб» и mtime прочитанного манифеста. Блоб исчез между
        чтением манифеста и открытием (писатель опубликовал новое поколение) — один
        немедленный повтор по свежему манифесту (GLM I1 r2). Ключ кэша не совпал —
        кэш холодный. None — не прочитан."""
        try:
            mtime = self._vec_manifest.stat().st_mtime
            manifest = json.loads(self._vec_manifest.read_text(encoding="utf-8"))
            if manifest.get("key") != self.cache_key():
                return None
            dim, entries = int(manifest["dim"]), manifest["files"]
            flat = array.array("f")
            with open(self._vec_manifest.with_name(str(manifest["blob"])), "rb") as fh:
                flat.frombytes(fh.read())
        except FileNotFoundError:
            return self._read_cache(retry=False) if retry else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if len(flat) != dim * sum(int(n) for _, _, n in entries):
            return None
        return entries, flat, dim, mtime

    def save_vectors(self) -> None:
        with self._lock:
            items = [(p, m, vs) for p, (m, vs) in self._vecs.items() if p in self._docs and vs]
        if not items:
            return
        dim = len(items[0][2][0])
        flat = array.array("f")
        for _, _, vs in items:
            for v in vs:
                flat.extend(v)
        self._vec_manifest.parent.mkdir(parents=True, exist_ok=True)
        # блоб неизменяем и именован поколением: сначала он, потом манифест (tmp +
        # replace) — читатель видит либо старую пару, либо новую; прежние блобы
        # стираются последними
        stem = self._vec_manifest.stem
        previous = None
        try:
            previous = json.loads(self._vec_manifest.read_text(encoding="utf-8")).get("blob")
        except (OSError, ValueError):
            pass
        blob = self._vec_manifest.with_name(f"{stem}.{uuid.uuid4().hex[:12]}.f32")   # уникально по построению, не по часам (DS I3 r2)
        tmp = blob.with_name(blob.name + f".tmp{os.getpid()}")
        with open(tmp, "wb") as fh:
            fh.write(flat.tobytes())
        tmp.replace(blob)
        safe_write.write_text(self._vec_manifest, json.dumps(
            {"dim": dim, "key": self.cache_key(), "blob": blob.name,
             "files": [[p, m, len(vs)] for p, m, vs in items]}, ensure_ascii=False))
        # уборка поколений: текущее и предыдущее живут — читатель без лока может
        # держать в руках прошлый манифест (DS I4 / GLM I1 r2); остальные — только
        # старше BLOB_GRACE_S: окно читателя перекрывается временем, а не удачей, и
        # замок писателей — оптимизация, не условие корректности (DS I2 r3);
        # сравнение имён строковое — метасимволы в имени графа ломали glob (DS M7 r2)
        keep = {blob.name, previous}
        stale_before = self._now() - BLOB_GRACE_S
        for old in self._vec_manifest.parent.iterdir():
            if old.name.startswith(f"{stem}.") and old.name.endswith(".f32") and old.name not in keep:
                try:
                    if old.stat().st_mtime < stale_before:
                        old.unlink()
                except OSError:
                    pass

    def pending_vectors(self) -> list[str]:
        self.load_vectors()
        with self._lock:
            return [p for p, d in self._docs.items() if self._vecs.get(p, (None, None))[0] != d.mtime]

    def embed_pending(self, budget_s: float | None = None, batch: int = EMBED_BATCH,
                      timeout: float = 60.0, should_stop: Callable[[], bool] | None = None) -> int:
        """Доиндексация файлов с изменившимся mtime — по блокам, пачками, с
        потолком по времени. Вызывать ВНЕ живой записи: сотни файлов — минуты
        работы модели эмбеддингов, на встрече они отняли бы слот у подсказок;
        `should_stop` (живая запись началась) спрашивается перед КАЖДОЙ пачкой,
        а таймаут пачки не длиннее остатка бюджета — проверка на входе давала
        окно в минуты (круг 1 по #577, DS I2). Писателей сериализует flock рядом
        с манифестом: занято — выходим, self.note скажет; замок не взялся — не
        пишем вовсе. Файл готов, когда есть векторы всех его блоков; сервер не
        ответил — останавливаемся, недобранное дособерём в следующий раз.
        -> сколько файлов получили векторы."""
        self.note = ""
        lock = None
        try:
            self._vec_manifest.parent.mkdir(parents=True, exist_ok=True)
            lock = open(self._vec_manifest.with_suffix(".lock"), "a+")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            self.note = "векторы уже собирает другой процесс"
            return 0
        except OSError as exc:
            # каталог недоступен, том без flock, EMFILE: без замка два писателя стёрли
            # бы блоб друг друга уборкой — не пишем вовсе (DS I2 / GLM M4 r3)
            if lock is not None:
                lock.close()
            self.note = f"без замка не пишем: {exc}"
            return 0
        try:
            return self._embed_pending(budget_s, batch, timeout, should_stop)
        finally:
            if lock is not None:
                lock.close()

    def _embed_pending(self, budget_s, batch, timeout, should_stop) -> int:
        started = self._now()
        done = 0
        queue: list[tuple[str, float, int, int, str]] = []   # путь, mtime, номер блока, всего, текст
        with self._lock:
            for p in self.pending_vectors():
                d = self._docs.get(p)
                if d is None:
                    continue
                limit = MAX_CHUNKS_NODE if is_node_path(d.rel) else MAX_CHUNKS
                parts = chunks(pathlib.PurePosixPath(d.rel).stem, d.text, limit=limit) or [d.text[:CHUNK_CHARS]]
                queue += [(p, d.mtime, i, len(parts), t) for i, t in enumerate(parts)]
        got: dict[str, tuple[float, int, dict[int, array.array]]] = {}
        for i in range(0, len(queue), batch):
            left = None if budget_s is None else budget_s - (self._now() - started)
            if left is not None and left <= 0:
                self.note = "бюджет времени исчерпан"
                break
            if should_stop is not None and should_stop():
                self.note = "началась живая запись — векторы доберём позже"
                break
            part = queue[i:i + batch]
            embs = self._embed([t for _, _, _, _, t in part], timeout if left is None else max(1.0, min(timeout, left)))
            if len(embs) != len(part):
                self.note = "сервер эмбеддингов не ответил — недобранное дособерём позже"   # (DS I5 / GLM M5 r2)
                break
            for (p, mtime, idx, total, _), emb in zip(part, embs):
                if not emb:
                    continue
                got.setdefault(p, (mtime, total, {}))[2][idx] = _unit(emb)
                mt, tot, have = got[p]
                if len(have) == tot:
                    with self._lock:
                        self._vecs[p] = (mt, [have[j] for j in range(tot)])
                    done += 1
            if done and (i // batch) % 40 == 39:
                self.save_vectors()      # длинная индексация: обрыв не теряет собранное
        if done:
            self.save_vectors()
        return done

    # ---------------------------------------------------------------- поиск
    def search(self, query: str, *, limit: int = 4, snippet_chars: int = 500,
               semantic: bool = True, embed_timeout: float = 6.0) -> Result:
        """Выдача по запросу. Индекс не прогрет — Result(ready=False): у
        вызывающего своя деградация (узлы графа, молчание). Протухший индекс
        обновляется фоном, ответ — по текущему."""
        if not self.ready:
            return Result([], 0, ready=False, query=query)
        if not self._fresh():
            threading.Thread(target=self.refresh, daemon=True, name="graph-search-refresh").start()
        with self._lock:
            docs = list(self._docs.values())
            indeg = dict(self._indeg)
            canon = dict(self._canon)
        avg_len = max(1.0, sum(len(d.low) for d in docs) / max(1, len(docs)))
        words, grams = needles(query)
        keys = words + grams
        # игл нет (одни стоп-слова): ищем фразу целиком подстрокой — точное
        # совпадение фразы и есть свидетельство, промах даст пустую выдачу
        pattern = "|".join(re.escape(k) for k in keys) if keys else re.escape(norm(query.strip()))
        rx = re.compile(pattern or "$^")
        now = self._now()

        # ----- лексика
        lex: list[tuple[float, str]] = []
        best_cov = 0.0
        rare_first: list[str] = []
        by_rel: dict[str, Doc] = {d.rel: d for d in docs}
        if keys:
            hits: list[tuple[Doc, list[int], list[int]]] = []
            for d in docs:
                rel_norm = norm(d.rel)
                t = [d.low.count(k) for k in keys]
                p = [rel_norm.count(k) for k in keys]
                if any(t) or any(p):
                    hits.append((d, t, p))
            n_docs = max(1, len(docs))
            idfs = [idf(sum(1 for _, t, p in hits if t[i] or p[i]), n_docs) for i in range(len(keys))]
            rare_first = [k for _, k in sorted(zip(idfs, keys), reverse=True)]
            for d, t, p in hits:
                score = 0.0
                len_norm = 1.0 - BM25_B + BM25_B * len(d.low) / avg_len
                for i, k in enumerate(keys):
                    # биграмма иероглифов в пути — слабый сигнал: двух знаков мало
                    score += bm25_lite(t[i], p[i], idfs[i], path_weight=1.0 if k in grams else 3.0,
                                       len_norm=len_norm)
                matched = sum(1 for i in range(len(keys)) if t[i] or p[i])
                best_cov = max(best_cov, matched / len(keys))
                score *= coverage_factor(matched, len(keys)) * recency_factor(d.date_ts, now)
                score *= hub_factor(indeg.get(d.base, 0)) * placeholder_factor(d.base) * raw_dampener(d.rel)
                lex.append((score, d.rel))
        else:
            for d in docs:
                if rx.search(d.low):
                    best_cov = 1.0
                    lex.append((recency_factor(d.date_ts, now), d.rel))

        # ----- семантика: вектор запроса против кэша векторов файлов
        sem: list[tuple[float, str]] = []
        best_sim = 0.0
        sem_used = False
        sem_share = 0.0     # доля файлов индекса с актуальными векторами — свидетель «в архиве нет»
        # кэш сверяется каждый раз: stat манифеста дёшев, а чужую запись (ночь,
        # апдейтер) короткое замыкание по непустым векторам не видело (GLM I1 r3)
        if semantic and self.load_vectors():
            qv = self._embed([query], embed_timeout)
            if qv and qv[0]:
                sem_used = True
                q = _unit(qv[0])
                with self._lock:
                    vecs = list(self._vecs.items())
                paths = {d.path: d for d in docs}
                sims = []
                checked = 0
                for path, (mt, vs) in vecs:
                    d = paths.get(path)
                    if d is None or mt != d.mtime or not d.low.strip():   # переписан — старые блоки не свидетели (GLM M3); пустой — не свидетель
                        continue
                    checked += 1
                    sim = max((_dot(q, v) for v in vs if len(v) == len(q)), default=0.0)   # лучший блок файла
                    if sim >= SIM_FLOOR:
                        sims.append((sim, d))
                # знаменатель — файлы, которым векторы вообще положены: пустой файл
                # ждёт вектора вечно и держал бы долю ниже порога (DS M2 r4)
                sem_share = checked / max(1, sum(1 for d in docs if d.low.strip()))
                sims.sort(key=lambda x: -x[0])
                best_sim = sims[0][0] if sims else 0.0
                for sim, d in sims[:max(limit * 4, 20)]:
                    # те же демпферы, что у лексики: метка диаризации с сотней упоминаний
                    # темы не должна всплывать через вектор, раз не всплывает через слова
                    sem.append((sim * recency_factor(d.date_ts, now) * raw_dampener(d.rel) * placeholder_factor(d.base), d.rel))

        dossiers, dossier_cov = self._dossier_blocks(query, snippet_chars)
        # вердикт — функция ВСЕГО, что несёт Result: досье — такое же лексическое
        # свидетельство (доля ключей темы в запросе), без него статус говорил «пусто»
        # при непустой сводке, и контуры домысливали по-своему (DS I2 / I3 r4)
        status = verdict(max(best_cov, dossier_cov), best_sim, sem_used, sem_share)   # подстрока — способ поиска, не уровень свидетельства (GLM M4 r2)
        reason = "" if status is not Verdict.UNVERIFIED else (REASON_CACHE if sem_used else REASON_EMBED)
        if not lex and not sem:
            # пусто по словам и по векторам — доказанное отсутствие только с проверенной
            # семантикой и без досье; без неё «ничего не найдено» читалось как факт (GLM C1 r3)
            if status is Verdict.WEAK and not dossiers:
                status = Verdict.EMPTY
            return Result([], 0, status, dossiers=dossiers, sem_used=sem_used, query=query, reason=reason)
        low_conf = status is not Verdict.CONFIDENT
        fused = rrf_merge([[r for _, r in sorted(lex, key=lambda x: -x[0])],
                           [r for _, r in sorted(sem, key=lambda x: -x[0])]], weights=[1.0, 0.7])
        fused = _swap_stubs(fused, by_rel, docs, canon)
        picked = diversify([(s, r) for r, s in fused], limit)
        blocks: list[str] = []
        shown: list[str] = []
        for rel in picked:
            d = by_rel[rel]
            frag = _frag_or_head(d.body or d.text, rx, snippet_chars, rare_first or keys, dense=raw_dampener(rel) == 1.0)
            blocks.append(f"• {rel}\n  {frag}")
            shown.append(rel)
        total = len(fused)
        if not low_conf:
            hops = self._hops(shown, by_rel, canon, keys, rx, snippet_chars, rare_first or keys, max(1, limit // 2))
            blocks += hops
            total += len(hops)
        return Result(blocks, total, status, dossiers=dossiers, sem_used=sem_used, query=query, reason=reason)

    def _dossier_blocks(self, query: str, snippet_chars: int, limit: int = 2) -> tuple[list[str], float]:
        """Готовые сводки по теме — ПЕРЕД фрагментами: индекс лексический, без
        моделей. -> (блоки, лучшая доля ключей темы в запросе — в вердикт как покрытие)."""
        folder = self.graph / dossier.DOSSIER_DIR
        try:
            entries = dossier.lookup(folder, query, limit=limit)
        except Exception:  # noqa: BLE001 — досье вспомогательны
            return [], 0.0
        out: list[str] = []
        best = 0.0
        for e in entries:
            if e.get("счёт", 0) < 0.3:
                continue
            p = folder / f"{e['тема']}.md"
            try:
                body = frontmatter.split(unicodedata.normalize("NFC", p.read_text(encoding="utf-8")))[1]
            except (OSError, ValueError):
                continue
            head = " ".join(body[:snippet_chars * 3].split())
            out.append(f"📁 Досье «{e['тема']}»\n  {head}")
            best = max(best, min(1.0, float(e.get("счёт", 0))))
        return out, best

    def _hops(self, shown: list[str], by_rel: dict[str, Doc], canon: dict[str, str], keys: list[str],
              rx: re.Pattern, snippet_chars: int, rare_first: Sequence[str], limit: int) -> list[str]:
        """Один переход по [[ссылкам]] из найденных узлов: заметки со стемами
        запроса ВНЕ имени узла (покрытие × свежесть), при голом имени — самые
        свежие; тёзки в разных папках — один кандидат; по одному слоту на узел,
        потом добор — первый узел не съедает бюджет.

        Ссылка на слитый узел ведёт к канону: иначе свежайшим кандидатом под
        базой оказывается заглушка-редирект, её отбрасывает фильтр узлов, и
        переход пропадает молча (замер 17.09: 40 переходов из 27 663 целей —
        столько доезжает до выдачи после всех фильтров и лимитов)."""
        nodes = [r for r in shown if is_node_path(r)]
        if not nodes or limit <= 0:
            return []
        by_base: dict[str, list[Doc]] = {}
        for d in by_rel.values():
            if d.stub_to:            # заглушка — не кандидат: за ней стоит канон
                continue
            by_base.setdefault(canon.get(d.base, d.base), []).append(d)
        out: list[str] = []
        seen = set(shown)
        for per_node in (1, limit):
            for node_rel in nodes:
                if len(out) >= limit:
                    return out
                node = by_rel[node_rel]
                name_stems = set(needles(pathlib.PurePosixPath(node_rel).stem)[0])

                def _is_name(s: str) -> bool:
                    return any(s == n or (s.startswith(n) and len(s) - len(n) <= 2)
                               or (n.startswith(s) and len(n) - len(s) <= 2) for n in name_stems)

                other = [k for k in keys if not _is_name(k)]
                cands: list[tuple[float, Doc, int]] = []
                for base in wiki_targets(node.text):
                    best = sorted(by_base.get(canon.get(base, base), ()), key=lambda d: -d.date_ts)[:1]
                    for d in best:
                        if d.rel in seen or is_node_path(d.rel) or d.rel.split("/")[-1].startswith("_"):
                            continue
                        matched = sum(1 for k in other if k in d.low)
                        if other and not matched:
                            continue
                        cov = matched / len(other) if other else 1.0
                        cands.append((cov * recency_factor(d.date_ts, self._now()) * raw_dampener(d.rel), d, matched))
                cands.sort(key=lambda x: (x[0], x[1].rel), reverse=True)
                for _s, d, _m in cands[:min(per_node, limit - len(out))]:
                    frag = _frag_or_head(d.body or d.text, rx, snippet_chars, rare_first, dense=raw_dampener(d.rel) == 1.0)
                    seen.add(d.rel)
                    out.append(f"• {d.rel}\n  ↳ по ссылке из {node_rel}\n  {frag}")
                    if len(out) >= limit:
                        return out
        return out


def _swap_stubs(hits: Sequence[tuple[str, float]], by_rel: dict[str, Doc],
                docs: Sequence[Doc], canon: dict[str, str]) -> list[tuple[str, float]]:
    """Заглушка-редирект в выдаче → канон, на который она указывает.

    Заглушка — не документ, а указатель: блок показал бы одну стрелку вместо
    содержания. Выбросить её тоже нельзя — старое имя ищут («как это раньше
    называли»), поэтому её ранг достаётся канону. Подмена идёт ДО отбора: иначе
    совпавший с уже показанным канон терял слот, и выдача молча становилась
    короче на строку (замер 17.09 на рабочем графе, запрос про статус человека)."""
    if not any(by_rel[rel].stub_to for rel, _ in hits):
        return list(hits)
    live = live_owners(docs)
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for rel, score in hits:
        d = by_rel[rel]
        if d.stub_to:
            target = name_owner(d.base, d.stub_to, live, canon)
            if target is None:        # канон не дожил до индекса — показывать нечего
                continue
            rel = target.rel
        if rel in seen:
            continue
        seen.add(rel)
        out.append((rel, score))
    return out


def _frag_or_head(text: str, rx: re.Pattern, chars: int, rare_first: Sequence[str], dense: bool) -> str:
    """Окно вокруг игл, а если игл в тексте нет (файл пришёл семантикой или по
    ссылке) — начало ТЕЛА файла без YAML-шапки (DS M5, DS M9 r2)."""
    frag = snippet(text, rx, chars, rare_first, dense=dense)
    if frag:
        return frag
    try:
        body = frontmatter.split(text)[1]
    except ValueError:
        body = text
    return " ".join(body[:chars].split())


class NotReady(RuntimeError):
    """Индекс ещё прогревается — контур деградирует по-своему и пробует позже."""


class Unavailable(RuntimeError):
    """Памяти по графу не будет: граф не настроен."""


def render(result: Result, query: str | None = None, where: str = "графе") -> str:
    """Текст выдачи человеку (CLI, журнал, тесты формата) в том же виде, что
    отдавал сервер памяти: «⚠ …» первой строкой, досье, шапка «Найдено…»,
    «Ничего не найдено по «…»». Контуры демона и бенч читают Result.status и
    Result.fragments — маркеры здесь никто не разбирает (круг 3 по #577)."""
    query = result.query if query is None else query
    if not result.ready:
        return ""
    if result.empty:
        if result.status is Verdict.UNVERIFIED:
            return (f"⚠ По словам ничего не нашлось по «{query}» в {where}, семантикой не проверено "
                    f"({result.reason}) — не считать доказанным отсутствием")
        return f"Ничего не найдено по «{query}» в {where}"
    parts = list(result.dossiers)
    if result.blocks:
        if parts:
            parts.append("— — — ниже отдельные фрагменты графа — — —")
        parts.append(f"Найдено в {where} ({len(result.blocks)} из {result.total}):\n\n" + "\n\n".join(result.blocks))
    # иначе — только сводка: «Найдено (0 из 0)» под ней врало бы
    warn = f"⚠ {result.why_low}. Ниже найденное:\n" if result.low_conf else ""   # первой строкой, ПЕРЕД досье (DS C1 r3)
    return warn + "\n\n".join(parts)


_shared: dict[str, GraphSearch] = {}
_shared_lock = threading.Lock()


def shared(cfg: dict, graph_dir: pathlib.Path | None = None) -> GraphSearch | None:
    """Один индекс на процесс и граф; None — граф не настроен."""
    gdir = graph_dir or graphs.graph_dir(cfg)
    if gdir is None:
        return None
    key = str(gdir)
    with _shared_lock:
        gs = _shared.get(key)
        if gs is None:
            gs = _shared[key] = GraphSearch(gdir, cfg)
        return gs
