"""Память по графу без сервера: поиск для живых подсказок и вопросов.

Демон на встрече спрашивал память у отдельного сервера (:8100) и без него
деградировал до узлов графа по стемам. Сервер выключается (решение 12.09):
прошлые договорённости демон находит сам — по файлам графа проекта, локально,
в бюджете живого контура (мгновенный ответ — 2,5 с).

Устройство. Индекс — файлы графа без исключений обхода схемы хранилища (у
Чароита — архив встреч и копии стенограмм: они дублируют заметки и втрое
тяжелее всего остального): текст,
нормализованный текст, дата (из имени YYYY-MM-DD, иначе mtime), входящие
[[ссылки]]. Обновляется по mtime, повторный обход — фоном. Лексика: слова
запроса → стемы (graph_nodes.stem — тот же стеммер, что у узлов и у
приложения) и биграммы иероглифов; BM25-lite с IDF (насыщение частоты, слово
в пути файла ×3), корень покрытия запроса, свежесть (полураспад 90 дней), хаб
по входящим ссылкам, демпфер меток диаризации и сырых стенограмм. Семантика:
bge-m3-вектор на файл (шапка 12 000 знаков) через дверь векторов
(`charoite_graph.embed_door`, Ollama /api/embed), кэш в
data/ по mtime; на встрече считается только вектор запроса — файлы
индексируются вне записи (после встречи, ночью), пока вектора нет, файл
участвует лексически. Векторы — по блокам, не по файлам: файл режется по
заголовкам, длинные секции — по абзацам, каждый блок несёт хлебную крошку
«Файл → H1 → H2» (как поиск приложения: один вектор на файл по первым
12 000 знаков терял 63 % содержимого — решения встреч живут в конце, а
Ollama молча режет вход bge-m3 до 2048 токенов — 5–6 тысяч знаков, замер
26.09). Счёт файла — лучший блок.
Слияние — RRF: ранги вместо несравнимых счётов, файл в
обоих списках складывает вклады. Досье по теме — первыми: сводка уже собрана,
восстанавливать её из фрагментов не надо. Разнообразие: одна встреча (заметка,
стенограмма, подсказки) не съедает все слоты. Один переход по [[ссылкам]] из
найденного узла: «что решил X» находит узел Люди/X, а решение живёт в заметке
встречи. Гейт честности: оба сигнала слабые → «⚠ …», и потребитель говорит «в
прочитанной части почти ничего», а не сочиняет и не выдаёт непрочитанное за
проверенное — архив встреч и копии стенограмм в индекс не входят, и что именно
осталось за границей, несёт `Result.skipped`.

Референсы дизайна: гибрид BM25 + вектор с временным слоем по Obsidian-графу,
важность узла по степени (LightRAG), RRF с временным слоем (Zep/Graphiti).
"""
from __future__ import annotations

import array
import dataclasses
import datetime as dt
import enum
import hashlib
import json
import math
import operator
import os
import pathlib
import re
import threading
import types
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence

from charoite_graph import dossier  # noqa: E402
from charoite_graph import frontmatter  # noqa: E402
from charoite_graph import graph_nodes  # noqa: E402
from charoite_graph.graph_schema import PLAIN, GraphSchema  # noqa: E402
from charoite_graph.model_seam import Embedder  # noqa: E402
from charoite_graph import redirects  # noqa: E402
from charoite_graph import safe_write  # noqa: E402
from charoite_graph import text_norm  # noqa: E402
import uuid  # noqa: E402

# Роль документа — ставится ОДИН раз при чтении и живёт в `Doc`; потребители
# (голоса, обход, переходы, отбор слотов) читают поле, а не строку пути.
# Раньше каждый выводил своё правило из фрагмента пути — заглушки в цикле
# голосов, `_`-указатели только в корне при обходе и по имени в переходах,
# досье — нигде: 16 % голосов графа отдавали сводки, пересказавшие тех, за кого
# голосуют (входной круг DS и GLM по №296, 18.09)
PRIMARY = "primary"        # заметка встречи, узел, документ — первичное знание
DOSSIER = "dossier"        # ночная сводка по теме: цель ссылок и секция «📁», не голос и не слот
SERVICE = "service"        # указатель/кандидаты/отчёт: вне индекса, но в охвате
BM25_B = 0.5               # нормализация длины: узел на 280 КБ не должен матчить всё подряд
HUB_CAP = 1.5              # потолок буста хаба: владелец графа упомянут в каждой встрече
REFRESH_S = 60.0           # свежесть обхода: чаще смысла нет, граф пишется после встреч
CHUNK_CHARS = 4_000        # блок для эмбеддера: ниже потолка Ollama — 2048 токенов (замер 26.09: самый длинный блок 1587)
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
MAX_STUB_HOPS = 64         # длина цепочки слияний: 64 подряд под одним именем не бывает
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
_PLACEHOLDER_RX = re.compile(r"^(?:собеседник|участник|спикер|speaker|participant)(?: ?\d+)?(?: .*)?$")
# Иероглифы пробелами не разделяются — «слова» из них не нарезать: берём
# скользящие биграммы (支付服务商 → 支付, 付服, 服务, 务商) — стандартный приём
# для языков без пробелов; блоки — как в бенче памяти.
CJK = (r"一-鿿㐀-䶿豈-﫿぀-ヿｦ-ﾟ가-힯"
       r"\U00020000-\U0002ee5f\U0002f800-\U0002fa1f\U00030000-\U000323af")
_CJK_RUN = re.compile(f"[{CJK}]+")
_HEADING_RX = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*$", re.M)


#: Регистр, ё→е, полноширинные латиница и цифры → обычные — тот же объект, что
#: `text_norm.fold`: одна форма сравнения путей, имён и слов на поиск и схему
#: хранилища. Форму Unicode здесь отдельно не приводят: NFC меняет ДЛИНУ
#: разложенного текста, а `snippet` по совпадению длин решает, из чего резать
#: фрагмент, и NFD-заметка уехала бы в выдачу строчными буквами (DS, круг 2 по
#: №291). Текст приводится к NFC один раз при чтении файла, в `_walk`.
norm = text_norm.fold


def norm_text(s: str) -> str:
    return " ".join(norm(s).split())


def doc_role(rel: str, schema: GraphSchema) -> str:
    """Роль документа по относительному пути — единственное место, где она
    выводится, по предикатам схемы хранилища. Служебный: имя файла со служебным
    префиксом в ЛЮБОЙ папке (то же правило, что у `dossier.scan`) и индекс досье
    в папке досье. Досье: прочий файл внутри папки досье. Иначе первичный.

    Индекс досье (`dossier.INDEX_MD`) пишет сам пакет, и служебный он по своему
    имени, а не по префиксу схемы: у Чароита его имя начинается со служебного «_»,
    но у хранилища без такого префикса индекс стал бы документом досье — это
    нашёл оборот схемы (№422, PR B)."""
    name = rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if schema.is_service_name(name):
        return SERVICE
    if schema.is_dossier(rel):
        return SERVICE if norm(name) == norm(dossier.INDEX_MD) else DOSSIER
    return PRIMARY


def cjk_grams(text: str) -> list[str]:
    grams: list[str] = []
    for run in _CJK_RUN.findall(text):
        grams += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return grams


def needles(query: str) -> tuple[list[str], list[str]]:
    """Иглы запроса: стемы слов и биграммы иероглифов, каждая по одному разу
    (повтор удваивал бы вклад в счёт). Пересечься списки не могут: слова — из
    латиницы, кириллицы и цифр, биграммы — только из иероглифов."""
    query = unicodedata.normalize("NFC", query).translate(text_norm.FULLWIDTH)   # ＹｕＰａｙ — слово, а не пропуск;
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
    WEAK = "weak"                 # проверила: слабы оба сигнала — в ПРОЧИТАННОЙ части почти ничего
    UNVERIFIED = "unverified"     # семантика не отработала или кэш собран не весь — по словам, не проверено
    EMPTY = "empty"               # ничего, и это проверено — в прочитанной части


def verdict(cov: float, sim: float, sem_used: bool, sem_share: float = 1.0) -> Verdict:
    """Вердикт по свидетельствам: без семантики уверенности нет вовсе — одна
    лексика на большом графе не отличает вопрос от ловушки (замер 17.09); с ней
    сильный любой из сигналов — уверенно (правило приложения), слабы оба — «почти
    ничего», но только если проверена достаточная доля ИНДЕКСА (SEM_SHARE_MIN).

    Доля считается по индексу, а не по графу, и индекс — не весь граф: архив
    встреч и копии стенограмм у Чароита исключены схемой. Поэтому ни один вердикт
    не вправе говорить «в архиве нет»; что осталось непрочитанным, несёт
    `Result.skipped`, а слова об этом собирает фасад (замер 17.09: 11 506
    файлов вне индекса против 3 283 в нём, DS и GLM, входной круг по №295)."""
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
    всплывает в любой выдаче только за счёт степени.

    Перекалибровка после №292 не понадобилась, хотя круг её и требовал: ключ
    связи стал путём, и на потолке буста осталось 462 узла против 467 — тот же
    набор хабов. Доля выросла (10 % → 17 %) только потому, что упал
    знаменатель: ключи-имена плодили узлы, которых в графе нет (4 458 → 2 697)."""
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


def raw_dampener(rel: str, schema: GraphSchema) -> float:
    """×0,75 сырью по схеме (стенограммы, живые логи): дистиллят при равной
    релевантности выше."""
    return 0.75 if schema.is_raw(rel) else 1.0


def placeholder_factor(base: str) -> float:
    """Узел-метка диаризации («Собеседник 3») — не человек и не хаб: ×0,2."""
    return 0.2 if _PLACEHOLDER_RX.match(base or "") else 1.0


def meeting_key(rel: str) -> str | None:
    """Ключ встречи из пути (дата[_время]): одна встреча живёт 3–4 файлами."""
    m = _MEETING_RX.search(rel)
    return (m.group(1) + (m.group(2) or "")) if m else None


def stub_base(text: str) -> str:
    """Файл — заглушка-редирект после слияния узлов? Тогда КЛЮЧ канона, иначе "".

    Обход и так держит текст в руках, поэтому распознание стоит один разбор на
    ИЗМЕНЁННЫЙ файл, а не на запрос. Замер 17.09 на рабочем графе: 404 заглушки
    с целью из 3199 файлов, 1048 входящих ссылок вели на них вместо канонов."""
    if not (redirects.is_redirect_stub(text) or redirects.is_merged(text)):
        return ""
    target = redirects.stub_target(text)
    if not target:
        return ""
    # путь как написан, а не лист: цель стрелки — тот же ключ связи, что и цель
    # [[ссылки]] (DS и GLM, входной круг по №292)
    t = target.split("|")[0].strip().strip("/ ")
    return norm_text(t.removesuffix(".md"))


def owner_key(d: Doc) -> tuple[float, str]:
    """Ключ «кто представляет ключ»: свежайший, при равной дате — меньший путь.

    Один ключ на все места, где ключ достаётся одному из нескольких файлов —
    и по имени, и по пути. Тай-брейк по пути обязателен: дата узла это mtime,
    а его двигают `git checkout`, копия графа целиком и синк облака, поэтому
    равные даты у тёзок штатны. Без второго ключа хозяин решался порядком
    обхода каталога, а без первого — алфавитом папки (DS, круги 4 и 5 по №291;
    круг 2 по №292 поймал тот же недетерминизм, вернувшийся на путях)."""
    return (-d.date_ts, d.rel)


def wiki_targets(text: str) -> set[str]:
    """Цели [[ссылок]] как НАПИСАНЫ: с папкой, если автор её назвал.

    Папку раньше срезали здесь же, и однозначность, которую автор дал руками,
    терялась на входе. Разрешение цели в документ — `LinkCatalog`, там же и
    правило голой ссылки."""
    out: set[str] = set()
    for target in _WIKILINK_RX.findall(text):
        t = target.strip().removesuffix(".md").strip("/ ")
        if t:
            out.add(norm_text(t))
    return out


class LinkCatalog:
    """Единственный ответ на вопрос «куда ведёт ссылка» для всего поиска.

    Три потребителя — голос входящей ссылки, переход по ссылке из найденного
    узла, подмена заглушки в выдаче — раньше строили свои карты и каждый вносил
    своё правило выбора хозяина ключа. Каждый круг находил очередного
    потребителя, который разошёлся с остальными: `_indeg` (№291), разворот
    заглушки (круг 2 по №292). Каталог строится ОДИН раз на снимок, и карты — его
    приватные поля, поэтому недетерминированного хозяина ключа снаружи не
    достать (схождение DS и GLM, круг 2 по №292). Наружу два уровня: `named`
    отвечает про написанное и может вернуть указатель, `live` — только живой
    документ.

    Ключ связи — нормализованный путь без расширения. Нормализация схлопывает
    регистр и ё/е, поэтому `Ядра/Отчёт.md` и `Ядра/Отчет.md` — два РАЗНЫХ файла
    с одним ключом; хозяина такой коллизии выбирает `owner_key`, а не порядок
    обхода каталога."""

    def __init__(self, docs: Iterable[Doc]) -> None:
        docs = list(docs)      # два прохода по снимку: генератор исчерпался бы на
                               # первом и вернул «ссылки никуда» вместо ошибки (DS r5)
        self._by_path = _owned(docs, lambda d: d.key)
        self._names = _owned(docs, lambda d: d.base, stubs_last=True)
        self._canon = self._build_canon(docs)

    @property
    def canon(self) -> dict[str, str]:
        """Ключ заглушки → ключ живого канона. Наружу — только для отчётов."""
        return dict(self._canon)

    def named(self, target: str) -> Doc | None:
        """Какой документ НАЗВАН этой целью. Может быть заглушкой.

        Папка названа — берём её путь и ничего не угадываем: путь назван, а
        файла нет — промах, иначе однофамилец перехватывает объявленную цель.
        Цель голая — идём по имени, где живой бьёт заглушку (DS и GLM, круг 1
        по №292).

        Замер 17.09: путь как написан есть у 41 390 папочных ссылок из 41 409,
        по имени пришлось бы резолвить 3, не нашлось ни так ни так 16 — фолбэк
        нужен, но он редкий."""
        hit = self._by_path.get(target)
        if hit is not None:
            return hit
        return None if "/" in target else self._names.get(target)

    def live(self, target: str) -> Doc | None:
        """Какой ЖИВОЙ документ стоит за целью. Заглушку не вернёт никогда.

        То, что нужно всем потребителям. Разворот здесь, а не у каждого из них:
        каждый делал его по-своему, и на голых целях промахивался. Если за
        заглушкой снова заглушка — цепочка оборвана (взаимные стрелки, мёртвое
        звено), и честный ответ «никуда», а не следующее звено: иначе голос
        уходил мёртвому файлу — ровно дефект, который закрывал №291
        (Critical DS, круг 2 по №292)."""
        hit = self.named(target)
        if hit is None or not hit.stub_to:
            return hit
        # куда ведёт заглушка, уже посчитано по её КЛЮЧУ: стрелка бывает звеном
        # цепочки, ключ указывает на её конец
        end = self.named(self._canon.get(hit.key, hit.stub_to))
        return None if end is None or end.stub_to else end

    def instead_of_stub(self, stub: Doc) -> Doc | None:
        """Кого показать вместо заглушки, попавшей в выдачу.

        Сначала СТРЕЛКА через карту канонов, и только потом имя. Обратный
        порядок давал однофамильца вместо объявленной цели (DS, круг 1 по
        №292). Живой тёзка — последняя ступень: заглушка с мёртвой стрелкой всё
        же про своё имя, и показать по нему живой файл лучше, чем ничего.

        Карта канонов отвечает про ХОЗЯИНА ключа, поэтому спрашивать её можно
        только за него: при коллизии (`Ядра/Ёлка.md` и `Ядра/Елка.md` — один
        ключ) второй файл в выдаче находится по своему тексту, и ответ хозяина
        показал бы ему чужую цель. Не хозяин — идём по собственной стрелке
        (Important DS, круг 3 по №292)."""
        owned = self._by_path.get(stub.key) is stub
        hit = self.live(self._canon.get(stub.key, stub.stub_to) if owned else stub.stub_to)
        if hit is None:
            twin = self._names.get(stub.base)
            hit = twin if twin is not None and not twin.stub_to else None
        # производное — не замена заглушке в слоте: стрелка на сводку (или тёзка в
        # «Досье/») протаскивала бы досье в «Найдено в графе» мимо разреза
        # первичных (выходной круг GLM I1 по №296). Гейт здесь, в резолвере:
        # каждый будущий потребитель получает его даром
        return hit if hit is not None and hit.role == PRIMARY else None

    def _build_canon(self, docs: Sequence[Doc]) -> dict[str, str]:
        """Ключ заглушки → ключ живого канона, цепочки развёрнуты, циклы прочь.

        Ссылка на слитый узел должна считаться ссылкой на канон: иначе буст
        хаба достаётся мёртвому файлу, а переход через него отбрасывается
        фильтром узлов и ответ молча обедает.

        ДОЛГ, названный вслух (вердикты DS и GLM 17.09): это ВТОРАЯ реализация
        правила «куда ведёт заглушка»; первая — `graph_updater.follow_stubs`,
        она ходит по путям и читает диск. Свести их — отдельная карточка."""
        cands = _owned([d for d in docs if d.stub_to], lambda d: d.key)

        def resolve(target: str, seen: frozenset[str]) -> str | None:
            """Живой канон за цепочкой заглушек или None.

            Потолок глубины — против данных, а не кода: цепочка длиннее предела
            рекурсии уронила бы весь обход (DS, круг 6 по №291)."""
            hit = self.named(target)      # `live` тут нельзя: карта ровно сейчас
            if hit is None:               # и строится, цепочку идём сами
                return None               # цель не дожила до индекса
            if not hit.stub_to:
                return hit.key            # живой файл — конец цепочки
            if hit.stub_to in seen or len(seen) > MAX_STUB_HOPS:
                # `seen` заряжена ключом заглушки И её стрелкой, поэтому она же
                # отсекает самопетлю в любой записи: `# X → [[X]]` голой строкой,
                # путём, через коллизию ё/е. Отдельная сверка «стрелка ведёт в
                # себя» была бы мёртвым кодом — перебор всех сочетаний пути и
                # стрелки дал 0 расхождений (проверка круга 2 по №292)
                return None               # цикл, самопетля или слишком длинная цепь
            return resolve(hit.stub_to, seen | {hit.stub_to})

        out: dict[str, str] = {}
        for key, stub in cands.items():
            # идём ПО СТРЕЛКЕ заглушки, а не ищем однофамильца: ключ-путь почти
            # всегда уникален, и кандидат на каждом звене ровно один (№292)
            found = resolve(stub.stub_to, frozenset({key, stub.stub_to}))
            if found is not None and found != key:
                out[key] = found
        return out


@dataclasses.dataclass(frozen=True, slots=True)
class Generation:
    """Поколение индекса: документы, голоса и каталог связей ОДНИМ значением.

    Три отдельных поля публиковались тремя присваиваниями, и корректность
    держалась на том, что все три строки попали в один захват замка —
    соглашение в голове, а не инвариант. Пока они были врозь, поиск успевал
    увидеть новые документы со старым каталогом и падал на цели, которой в его
    снимке уже нет. Одно поле делает такой рассинхрон невыразимым, а проверку —
    структурной, а не гоночной (Critical DS, круги 3 и 4 по №292).

    Охват обхода (`skipped`, `unread`) живёт здесь же: пока он был отдельными
    полями, ответ мог соединить документы одного поколения с честностью
    другого — «найдено в этом снимке» и «столько-то не открылось» из разных
    обходов. Частичный тип — то же соглашение в голове, только с убедительным
    именем (Critical DS, круг 5). Словари заворачиваются в неизменяемый вид:
    значение, которое отдаётся читателю без копии, не должно быть мутируемым."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "docs", types.MappingProxyType(dict(self.docs)))
        object.__setattr__(self, "indeg", types.MappingProxyType(dict(self.indeg)))
        object.__setattr__(self, "dossiers", types.MappingProxyType(dict(self.dossiers)))

    docs: Mapping[str, Doc]
    indeg: Mapping[str, int]
    catalog: LinkCatalog
    skipped: tuple[str, ...] = ()   # что обход РЕАЛЬНО отсёк (см. `_walk`)
    unread: int = 0                 # файлы, которые не открылись (права, битая ссылка)
    service: int = 0                # служебные файлы вне индекса — тоже часть охвата (№295)
    # Разрезы по роли — готовые, а не фильтр у каждого потребителя: слоты и
    # голоса берут `primary`, секция «📁» — `dossiers` по ключу документа (тот
    # же `Doc.key`, что у ссылок: имя на диске знает только обход, и путь из
    # темы индекса не собирается строкой). Нефильтрованного списка для этих
    # решений нет — «забыл про роль» невыразимо (выходной круг DS/GLM по №296)
    primary: tuple[Doc, ...] = ()
    dossiers: Mapping[str, Doc] = dataclasses.field(default_factory=dict)


def _owned(docs: Iterable[Doc], key: Callable[[Doc], str], *,
           stubs_last: bool = False) -> dict[str, Doc]:
    """Ключ → документ, который за него отвечает. Выбор детерминирован.

    Плоское `{key(d): d for d in docs}` отдавало ключ последнему в обходе
    каталога — тому самому недетерминизму, ради которого писался `owner_key`
    (Critical DS и GLM, круг 2 по №292: `Ядра/Отчёт.md` и `Ядра/Отчет.md`
    дают один ключ, победитель менялся между перезапусками демона).

    `stubs_last` — для карты имён: живой файл всегда бьёт заглушку, иначе
    стрелка мёртвого дубля перехватывает голую ссылку у живого тёзки. Для карты
    путей приоритета нет: путь — это один файл, спорить не о чем."""
    out: dict[str, Doc] = {}
    for d in docs:
        k = key(d)
        cur = out.get(k)
        if cur is None or _rank(d, stubs_last) < _rank(cur, stubs_last):
            out[k] = d
    return out


def _rank(d: Doc, stubs_last: bool) -> tuple[bool, float, str]:
    return (bool(d.stub_to) if stubs_last else False, *owner_key(d))

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
    base: str          # нормализованное имя файла без расширения — цель ГОЛОЙ [[ссылки]]
    body: str = ""     # текст без YAML-шапки — для фрагментов выдачи (шапка модели не нужна)
    stub_to: str = ""  # заглушка-редирект: КЛЮЧ канона, куда она ведёт (иначе пусто)
    _: dataclasses.KW_ONLY
    #: Схема хранилища — только на время конструирования: из неё выводится роль,
    #: а сам документ её не хранит.
    schema: dataclasses.InitVar[GraphSchema]
    #: PRIMARY / DOSSIER / SERVICE — ВЫВОДИТСЯ из пути одним предикатом и только
    #: так: переданная роль позволила бы оснастке подсунуть чужую мимо правила —
    #: тот же класс «каждый выводит своё правило» через дверь тестов (выходной
    #: круг GLM по №296). `rel` после конструирования не меняется.
    role: str = dataclasses.field(init=False)

    def __post_init__(self, schema: GraphSchema) -> None:
        self.role = doc_role(self.rel, schema)
        # имя и стрелка нормализуются В ДОКУМЕНТЕ: обход их нормализует, а
        # оснастка тестов передавала сырыми — и тест «отчёт» против ключа
        # «отчет» проходил случайно, мимо продакшн-инварианта. Инвариант
        # «ключ, база и стрелка живут в одном пространстве имён» держит сам
        # документ, иначе его держать некому (GLM, круги 1 и 2 по №292)
        self.base = norm_text(self.base)
        self.stub_to = norm_text(self.stub_to)

    @property
    def key(self) -> str:
        """Ключ связи: нормализованный путь без расширения.

        Ссылка, назвавшая папку, обязана резолвиться однозначно — автор уже дал
        эту однозначность, а ключ-имя её терял. Замер 17.09 на рабочем графе:
        41 409 папочных ссылок из узлов, у 5 708 имя носят несколько файлов, и
        3 532 сегодня резолвятся НЕ в названный файл — систематически в сводку
        вместо ядра, потому что сводки пересобираются каждую ночь и всегда
        свежее (DS и GLM, входной круг по №292).

        Что правка сделала с выдачей (30 запросов, топ-5): лексическое покрытие
        запроса не сдвинулось вовсе — 0,989 и до и после, все перестановки идут
        между файлами с ПОЛНЫМ покрытием. Различает их только источник: сводок
        в выдаче стало вдвое меньше (34 → 17 слотов), ядер и систем больше
        (47 → 61), медиана возраста 10 → 9 дней. Голос вернулся тому, на кого
        ссылались, и это отвечает на «улучшение или регресс»: спор шёл не о
        релевантности, а о том, кого показывать при равной релевантности."""
        return norm_text(self.rel.removesuffix(".md"))


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
    skipped: tuple[str, ...] = ()   # области графа ВНЕ индекса: их не читали, и ответ не вправе о них судить
    unread: int = 0                 # файлы, до которых обход дошёл, но не смог прочитать
    service: int = 0                # служебные указатели, сознательно оставленные вне индекса
    # пути показанного (`rel` от корня графа) в порядке показа: досье, блоки, переходы.
    # Источники — поле, а не разбор «• путь» из `blocks`: маркеры в тексте никто не
    # разбирает (входные круги 1–2 по №323 PR 2, I6)
    sources: tuple[str, ...] = ()

    @property
    def low_conf(self) -> bool:
        return self.status in (Verdict.WEAK, Verdict.UNVERIFIED)

    @property
    def why_low(self) -> str:
        """Причина «⚠» человеку: без семантики — не «нет», а «не проверено».

        Про непрочитанные области судить нельзя: раньше здесь стояло «похоже, в
        архиве об этом почти ничего нет», хотя архив встреч в индекс не входит
        вовсе (замер 17.09: 78 % файлов графа). Что именно не читалось — в
        `skipped`, словами это разворачивает фасад."""
        if self.status is Verdict.UNVERIFIED:
            return f"Совпадения не проверены семантикой ({self.reason}) — найденное по словам, доверять с оглядкой"
        if self.status is Verdict.WEAK:
            return "В прочитанной части графа об этом почти ничего нет (слабые совпадения)"
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

    def as_dict(self) -> dict:
        """Выдача словарём — одна проекция для CLI и будущих входов пакета (MCP №520).
        `ready` в проекции обязателен: непрогретый индекс несёт статус по умолчанию
        `empty`, и без него пустая папка читалась бы как «ничего не найдено»
        (входной круг 1 по №323 PR 2, C2). Охват — `skipped`, `unread`, `service`:
        ответ не вправе судить о том, чего не читал."""
        return {"ready": self.ready, "status": self.status.value, "reason": self.reason, "text": self.text,
                "sources": list(self.sources), "total": self.total, "skipped": list(self.skipped),
                "unread": self.unread, "service": self.service}


def _dot(a, b) -> float:
    return math.sumprod(a, b) if hasattr(math, "sumprod") else sum(map(operator.mul, a, b))


def _unit(vec: Sequence[float]) -> array.array:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", (x / n for x in vec))


class GraphSearch:
    """Индекс одного графа и поиск по нему. Один экземпляр на процесс, граф и
    модель эмбеддингов (см. brain.shared()); обновление индекса и поиск — из разных
    потоков. Окружения приложения индекс не читает: каталог кэша векторов
    (`data_dir`) передаёт вызывающий, у Чароита — graphs.search_cache_dir()
    (№365: модуль графа на импорте и в конструкторе не берёт ничего вне своих
    параметров). Роли путей — узлы, досье, служебное, сырьё, исключения обхода —
    из схемы хранилища (`schema`); без неё — простое хранилище `PLAIN`, без
    ролей. Чароит строит индекс только через `graphs.open_search` со своей
    схемой — мимо неё конструктор не пускает сторож раскладки (`ENV_SEAMS`).

    Владение: `_gen` — снимок индекса целиком (документы, голоса, каталог
    связей, охват обхода). После инициализации его пишет ТОЛЬКО `_publish` —
    одним присваиванием под `_lock`, производной от ОСНОВЫ, которую
    вызывающий снял под тем же замком и передал явно. Публикация поле не
    читает, а основу СВЕРЯЕТ с текущим поколением: устаревшая — ошибка, не
    молчаливый откат. Так «решение по одному снимку, запись другого» внутри
    одного вызова не выразить, а публикация поверх чужой правки не проходит
    (три круга подряд ловили первое в разных ветках `_walk` — Critical DS,
    круги 5–7 по №292; второе назвал круг 8). Обход запускается только из
    `refresh`, сериализацию обходов держит его `_scan_lock`. Второй писатель
    — скажем, досыпка одной свежей заметки без полного обхода — сегодня не
    существует; появится — идёт через `_publish` со своей основой, и если
    обход успел опубликоваться раньше, получит ошибку и снимет основу заново,
    а не сотрёт чужое.

    Читатель берёт `gen = self._gen` ОДИН раз и дальше работает с ним, не
    трогая `self`: замок ему не нужен, потому что значение неизменяемо, а
    ссылка меняется атомарно. Второе обращение к полю в одном действии
    возвращает дефект круга 3 — половины ответа окажутся из разных поколений
    (Critical и Important DS, круги 3–7 по №292).

    `_vecs` пишут `load_vectors`/`embed_pending`/`_walk`
    (уборка исчезнувших) — тоже под `_lock`. Кэш векторов на диске: манифест с
    именем неизменяемого блоба (запись — новый блоб, потом манифест через
    tmp+replace, старые блобы стираются после) — читатель никогда не видит
    полузаписанной пары; писателей сериализует flock рядом с манифестом.
    """

    def __init__(self, graph_dir: pathlib.Path, *,
                 embedder: Embedder,
                 data_dir: pathlib.Path | None,
                 schema: GraphSchema = PLAIN,
                 now: Callable[[], float] = time.time) -> None:
        self.graph = pathlib.Path(graph_dir)
        # Роли путей — только из схемы (№422): у пакета своих имён папок нет.
        # Умолчание — объявленное значение `PLAIN`, а не `None`: внешнему
        # пользователю хранилище без ролей, Чароиту — `CHAROITE` через open_search.
        self.schema = schema
        self.exclude = schema.exclude_dirs
        self._now = now
        self._embedder = embedder
        # Причина отказа — СЛОВА источника, а не бит. Политика говорит «нельзя»
        # по трём разным настройкам, и совет про allow_remote снимает только
        # одну из них: на рубильнике офлайна и на открытом http наружу владелец
        # правил бы ручку, которая ничего не меняет. Слова у способности уже
        # есть — их надо донести, а не заменить константой (круг 4, GLM C1).
        self._refusal = embedder.refused
        self._gen = Generation({}, {}, LinkCatalog([]))   # снимок публикуется одним присваиванием
        self._refreshed_at = 0.0
        self._lock = threading.RLock()       # индекс и векторы
        self._scan_lock = threading.Lock()   # один обход за раз
        self._vecs: dict[str, tuple[float, list[array.array]]] = {}   # путь → (mtime, векторы блоков)
        # Сколько блоков у файла при ТЕКУЩЕЙ схеме: путь → (mtime, потолок, число).
        # Нарезка считается один раз на версию файла, а не на каждую сверку кэша.
        self._block_counts: dict[str, tuple[float, int, int]] = {}
        # `data_dir=None` — кэша нет: векторы не читаются и не пишутся (лексический поиск
        # CLI без адреса модели), а не «несуществующий путь», который создал бы первый
        # писатель (входной круг 2 по №323 PR 2, M3)
        base = None if data_dir is None else pathlib.Path(data_dir)
        # имя кэша — по пути графа, не по имени папки: два графа «Работа» в разных
        # vault-ах дрались бы за один файл (круг 1 по #577, GLM M6)
        tag = hashlib.sha256(str(self.graph.resolve()).encode("utf-8")).hexdigest()[:8]   # имя файла по пути, не подпись; sha256 — чтобы CI не спорил
        self._vec_manifest = None if base is None else base / "graph_search" / f"{self.graph.name}-{tag}.json"
        self._vecs_loaded = False
        self._vecs_tried_at: float | None = None   # None — не пробовали: часы могут считать от нуля (DS M3 r3)
        self._manifest_seen = 0.0   # mtime манифеста при последней загрузке: чужая запись — перечитать
        self.note = ""              # последнее «почему не сделали» для CLI и журнала

    # ---------------------------------------------------------------- индекс
    @property
    def ready(self) -> bool:
        return bool(self._gen.docs)

    @property
    def size(self) -> int:
        return len(self._gen.docs)

    def fingerprint(self) -> str:
        """Отпечаток снимка индекса: число файлов и sha256 по отсортированным
        (путь, mtime). Бенч памяти пишет его рядом с итогом, чтобы тревога
        отличала «изменился код» от «изменился граф» (№629 ч. 2, P6)."""
        docs = self._gen.docs.values()
        h = hashlib.sha256()
        for rel, mtime in sorted((d.rel, d.mtime) for d in docs):
            h.update(f"{rel}\0{mtime!r}\n".encode())
        return f"{len(docs)}:{h.hexdigest()[:16]}"

    def vote_stats(self) -> dict[str, int]:
        """Статистика поколения для калибровки хаба: кто в индексе по ролям,
        сколько голосов и целей, сколько целей на потолке буста, и сколько
        голосов НЕ отдают досье — с потолком, который был бы при их голосах.
        Свойство снимка, а не сценария бенча: одна цифра «до/после» для бенча,
        доктора и калибровки порогов (входной круг DS и GLM по №296)."""
        gen = self._gen
        docs = list(gen.docs.values())
        by_role = {PRIMARY: 0, DOSSIER: 0}
        for d in docs:
            by_role[d.role] = by_role.get(d.role, 0) + 1
        dossier_votes: dict[str, int] = {}
        for d in docs:
            if d.role != DOSSIER or d.stub_to:
                continue
            for target in wiki_targets(d.text):
                hit = gen.catalog.live(target)
                if hit is not None:
                    dossier_votes[hit.key] = dossier_votes.get(hit.key, 0) + 1
        at_cap = sum(1 for n in gen.indeg.values() if hub_factor(n) >= HUB_CAP)
        merged = dict(gen.indeg)
        for k, n in dossier_votes.items():
            merged[k] = merged.get(k, 0) + n
        return {"primary": by_role.get(PRIMARY, 0), "dossier": by_role.get(DOSSIER, 0),
                "service": gen.service, "votes": sum(gen.indeg.values()), "targets": len(gen.indeg),
                "at_cap": at_cap, "dossier_votes": sum(dossier_votes.values()),
                "cap_if_dossier_voted": sum(1 for n in merged.values() if hub_factor(n) >= HUB_CAP)}

    @property
    def vectors(self) -> int:
        return len(self._vecs)

    def _fresh(self) -> bool:
        return self._now() - self._refreshed_at < REFRESH_S

    def cache_key(self) -> str:
        """Всё, что определяет содержимое кэша, кроме файлов: модель эмбеддингов и
        правила нарезки. Сменилось — кэш холодный целиком (круг 2 по #577, DS I2).

        Имя берётся у шва, а не у конфига: считает векторы он, ему и подписывать.
        Пока имя выводилось здесь отдельно, подменённый векторизатор писал чужое
        пространство под привычным именем — и кэш врал молча."""
        model = self._embedder.model
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

    def _publish(self, base: Generation, gone: Iterable[str] = (), **changes: object) -> None:
        """Единственная запись `_gen` после инициализации: производная от
        `base` одним присваиванием под `_lock`. Основа — обязательный
        аргумент без умолчания: обход снимает её под замком первой строкой и
        отдаёт сюда. Публикация поле не перечитывает, но сверяет: основа
        обязана быть текущим поколением, иначе это запись поверх правки,
        которой вызывающий не видел, — ошибка, а не молчаливый откат
        (Important DS, круг 8 по №292). `gone` — пути исчезнувших файлов: их
        векторы уходят тем же замком, что документы (DS M3 / GLM M10). Что
        это сторожит — `tests/test_graph_search.py`, тест на форму."""
        with self._lock:
            if base is not self._gen:
                raise RuntimeError("публикация поколения от устаревшей основы")
            for p in gone:
                self._vecs.pop(p, None)
                self._block_counts.pop(p, None)
            self._gen = dataclasses.replace(base, **changes)

    def _walk(self) -> None:
        seen: set[str] = set()
        fresh: dict[str, Doc] = {}
        with self._lock:           # основа обхода — единственное чтение поля в обходе,
            current = self._gen    # под замком; обе публикации ниже — производные от неё
                                   # (Critical DS, круги 6–7 по №292)
        root = str(self.graph)
        # что отсечено ФАКТИЧЕСКИ, а не что записано в политике: на графе без
        # архивной папки оговорка про непрочитанное соврала бы, а на графе с
        # другими именами папок архив попал бы в индекс, и она соврала бы в
        # обратную сторону (DS, круг по №295)
        skipped: set[str] = set()
        unread = 0
        service = 0
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            keep = []
            for d in dirnames:
                if d.startswith("."):
                    continue
                rel = f"{rel_dir}/{d}" if rel_dir else d
                if self.schema.excluded(rel):
                    skipped.add(rel)
                else:
                    keep.append(d)
            dirnames[:] = keep
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                rel = f"{rel_dir}/{fn}" if rel_dir else fn
                if doc_role(rel, self.schema) == SERVICE:
                    # указатель на всех людей конкурировал с заметками о людях по
                    # любому имени; вне индекса — но не молча: он в охвате ответа
                    service += 1
                    continue
                path = os.path.join(dirpath, fn)
                seen.add(path)
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    unread += 1     # права, битая ссылка, сорванный синк — файл вне индекса
                    continue
                cached = current.docs.get(path)
                if cached is not None and cached.mtime == mtime:
                    continue
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        # одна форма Unicode на весь конвейер: имя файла от macOS
                        # приходит разложенным, текст заметки — собранным
                        text = unicodedata.normalize("NFC", fh.read())
                except OSError:
                    unread += 1     # тот же класс: ответ не вправе считать его проверенным
                    continue
                body = frontmatter.split(text)[1]     # по контракту не бросает: (None, text) без шапки
                fresh[path] = Doc(path, rel, mtime, text, norm(text), file_date_ts(rel, mtime),
                                  norm_text(os.path.splitext(fn)[0]), body, stub_base(text),
                                  schema=self.schema)
        scope = (tuple(sorted(skipped)), unread, service)
        gone = [p for p in current.docs if p not in seen]
        if not fresh and not gone:
            # граф не изменился, но охват мог: файл стал нечитаемым, папка
            # архива появилась. Публикуем то же поколение с новым охватом —
            # порознь они уезжать не должны (Critical DS, круг 5). Основа —
            # та же `current`, что и у основного пути: раньше эта ветка читала
            # поле заново, и решение принималось по одному снимку, а
            # записывался другой (Important DS, круг 7)
            self._publish(current, skipped=scope[0], unread=scope[1], service=scope[2])
            return
        # поколение собирается в СТОРОНЕ и публикуется одним присваиванием:
        # раньше документы уезжали в мир первым замком, а каталог и голоса —
        # вторым, и всю секунду между ними поиск видел новые документы со
        # старым каталогом. Заглушка подменялась на канон прошлого
        # поколения, которого в снимке читателя уже нет, — `by_rel[rel]`
        # ронял поиск с KeyError прямо на встрече (Critical DS, круг 3)
        dropped = set(gone)
        docs = {p: d for p, d in current.docs.items() if p not in dropped}
        docs.update(fresh)
        snapshot = list(docs.values())
        # обход 28 МБ текста — вне замка: под ним каждый поиск встречи ждал бы (GLM M5)
        catalog = LinkCatalog(snapshot)
        primary = tuple(d for d in snapshot if d.role == PRIMARY)
        dossiers = {d.key: d for d in snapshot if d.role == DOSSIER}
        indeg: dict[str, int] = {}
        for d in primary:
            # голосуют только первичные: единственная ссылка заглушки — служебная
            # стрелка на канон, а сводка досье пересказывает тех, за кого голосует —
            # буст хаба доставался тому, кого она сама и упомянула (№296)
            if d.stub_to:
                continue
            for target in wiki_targets(d.text):
                hit = catalog.live(target)
                if hit is None:
                    continue          # цели нет или цепочка оборвана — голос некому отдать
                indeg[hit.key] = indeg.get(hit.key, 0) + 1
        self._publish(current, gone=gone, docs=docs, indeg=indeg, catalog=catalog,
                      primary=primary, dossiers=dossiers,
                      skipped=scope[0], unread=scope[1], service=scope[2])

    # --------------------------------------------------------------- векторы
    def _embed(self, texts: list[str], timeout: float) -> list[list[float]]:
        """Векторы через шов. Транспорт лёг — отдаём пусто и идём лексикой.

        Ловим ровно `OSError`: всё, чем requests сообщает о сети (отказ
        соединения, таймаут, оборванный ответ), наследует именно его. Ошибка
        проводки — не той сигнатуры векторизатор, опечатка в имени поля — это
        `TypeError`/`AttributeError`, и она обязана долететь до человека.
        Широкий `except` здесь означал бы недели подсказок без семантики, в
        которых ни один тест не покраснеет: «сервер занят» и «я сломал шов»
        выглядят для вызывающего одинаково.
        """
        try:
            return self._embedder.run(texts, timeout)
        except OSError as exc:
            # Вид отказа запоминаем: для контура это один исход «векторов нет»,
            # а владельцу нужны разные слова — «сервер занят» и «вы запретили
            # этот адрес» ведут чинить разное.
            if getattr(exc, "policy", False) and not self._refusal:
                self._refusal = str(exc)
            return []

    def load_vectors(self) -> int:
        """Кэш векторов с диска: манифест (путь, mtime, число блоков, имя блоба) +
        неизменяемый плоский float32. Неудача не защёлкивается — повтор не чаще
        VEC_RETRY_S: защёлка гасила семантику на всю встречу после одного
        совпадения с писателем (круг 1 по #577, DS I1 / GLM I1)."""
        if self._vec_manifest is None:
            return len(self._vecs)
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

    def _read_cache(self, retry: bool):
        """Пара «манифест → блоб» и mtime прочитанного манифеста. Блоб исчез между
        чтением манифеста и открытием (писатель опубликовал новое поколение) — один
        немедленный повтор по свежему манифесту (GLM I1 r2). Ключ кэша не совпал —
        кэш холодный. None — не прочитан."""
        try:
            mtime = self._vec_manifest.stat().st_mtime
            manifest = json.loads(self._vec_manifest.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("key") != self.cache_key():
                return None
            dim, entries = manifest["dim"], manifest["files"]
            if type(dim) is not int or dim <= 0 or not isinstance(entries, list):
                return None
            checked = []
            for entry in entries:
                if not isinstance(entry, list):
                    return None
                path, stamp, n = entry
                if (not isinstance(path, str) or not path or type(n) is not int or n <= 0
                        or isinstance(stamp, bool) or not isinstance(stamp, (int, float))):
                    return None
                stamp = float(stamp)
                if not math.isfinite(stamp):
                    return None
                checked.append((path, stamp, n))
            entries = checked
            flat = array.array("f")
            with open(self._vec_manifest.with_name(str(manifest["blob"])), "rb") as fh:
                flat.frombytes(fh.read())
        except FileNotFoundError:
            return self._read_cache(retry=False) if retry else None
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return None
        if len(flat) != dim * sum(n for _, _, n in entries):
            return None
        # Конечность блоба — ОДНОЙ суммой, а не поэлементным `all(isfinite)`:
        # для `array("f")` это равносильно. Значения — float32, сумма идёт в
        # double и переполниться не может; NaN и ±inf до суммы доходят, а
        # +inf + (−inf) даёт NaN. Замер на кэше владельца (17 655 808 float32,
        # 70,6 МБ, Python 3.12.13, лучшее из трёх): поэлементно 0,663 с, суммой
        # 0,149 с. Эквивалентность держится на формате "f": для блоба float64
        # сумма могла бы переполниться, и кэш ложно стал бы холодным.
        if not math.isfinite(sum(flat)):
            return None
        return entries, flat, dim, mtime

    def save_vectors(self) -> None:
        if self._vec_manifest is None:
            return
        with self._lock:
            items = [(p, m, vs) for p, (m, vs) in self._vecs.items() if p in self._gen.docs and vs]
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
            prior = json.loads(self._vec_manifest.read_text(encoding="utf-8"))
            if isinstance(prior, dict) and isinstance(prior.get("blob"), str):
                previous = prior["blob"]
        except (OSError, ValueError):
            pass
        blob = self._vec_manifest.with_name(f"{stem}.{uuid.uuid4().hex[:12]}.f32")   # уникально по построению, не по часам (DS I3 r2)
        tmp = blob.with_name(blob.name + f".tmp{os.getpid()}")
        with open(tmp, "wb") as fh:
            fh.write(flat.tobytes())
        tmp.replace(blob)
        # манифест несёт пути заметок — только владельцу, и при перезаписи тоже: без
        # `mode` старый 0644 переносился бы на новый файл (входной круг 1 по №323 PR 2, I7)
        safe_write.write_text(self._vec_manifest, json.dumps(
            {"dim": dim, "key": self.cache_key(), "blob": blob.name,
             "files": [[p, m, len(vs)] for p, m, vs in items]}, ensure_ascii=False), mode=0o600)
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

    def _blocks(self, d: Doc) -> list[str]:
        """Блоки файла для эмбеддера — одна нарезка на доиндексацию и сверку кэша.
        Потолок блоков зависит от роли файла, а роль — от схемы хранилища."""
        limit = MAX_CHUNKS_NODE if self.schema.is_node_path(d.rel) else MAX_CHUNKS
        return chunks(pathlib.PurePosixPath(d.rel).stem, d.text, limit=limit) or [d.text[:CHUNK_CHARS]]

    def _expected_blocks(self, d: Doc) -> int:
        """Число блоков файла при текущей схеме — с памятью на версию файла."""
        limit = MAX_CHUNKS_NODE if self.schema.is_node_path(d.rel) else MAX_CHUNKS
        seen = self._block_counts.get(d.path)
        if seen is not None and seen[:2] == (d.mtime, limit):
            return seen[2]
        n = len(self._blocks(d))
        self._block_counts[d.path] = (d.mtime, limit, n)
        return n

    def pending_vectors(self, gen: Generation | None = None) -> list[str]:
        """Файлы без свежего вектора. `gen` — поколение вызывающего, если он уже
        его взял: иначе список и тексты приедут из разных снимков.

        Свежий — тот же mtime И то же число блоков, что даёт нарезка при текущей
        схеме: потолок блоков зависит от роли файла (узел или нет), и смена схемы
        при том же mtime иначе оставляла бы в кэше векторы чужой нарезки молча
        (входной круг 16 по №422, M2). `cache_key()` схему не подписывает — у
        владельца с постоянной схемой кэш не холодеет."""
        self.load_vectors()
        gen = gen or self._gen
        with self._lock:
            # векторы положены тем, кто участвует в семантике — первичным: сводки
            # досье в слоты и переходы не идут, их косинусы никто не читал бы, а
            # 256 файлов переэмбеддивались бы после каждой ночи (DS M3 / GLM M4)
            have = {p: (mt, len(v)) for p, (mt, v) in self._vecs.items()}
        # пустой файл не свидетель (то же правило, что у семантики в `search`): в очереди
        # он ждал бы вектора вечно, и `index` отвечал бы «собрано не всё» на каждом запуске
        # (выходной круг 1 по №323 PR 2, M1)
        return [d.path for d in gen.primary
                if d.low.strip() and have.get(d.path) != (d.mtime, self._expected_blocks(d))]

    def coverage_gaps(self) -> list[str]:
        """Чего текущее поколение индекса не читало — словами, тем же правилом, что у выдачи."""
        gen = self._gen
        return coverage_gaps(Result([], 0, skipped=gen.skipped, unread=gen.unread, service=gen.service))

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
        if self._vec_manifest is None:
            self.note = "кэша векторов нет (data_dir не задан) — векторы не пишем"
            return 0
        lock = None
        try:
            # только POSIX: модуль грузится и там, где fcntl нет, а без замка векторов не
            # пишем — отказ тот же, что у тома без flock, а не трассировка (Opus M1 круга
            # 1 по коду №365)
            import fcntl
            self._vec_manifest.parent.mkdir(parents=True, exist_ok=True)
            lock = open(self._vec_manifest.with_suffix(".lock"), "a+")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            self.note = "векторы уже собирает другой процесс"
            return 0
        except (OSError, ImportError) as exc:
            # каталог недоступен, том без flock, EMFILE, платформа без fcntl: без замка два
            # писателя стёрли бы блоб друг друга уборкой — не пишем вовсе (DS I2 / GLM M4 r3)
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
            gen = self._gen          # одно чтение на всё действие: список и тексты
            for p in self.pending_vectors(gen):   # обязаны быть одного поколения
                d = gen.docs.get(p)
                if d is None:
                    continue
                parts = self._blocks(d)
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
                # Причина — та же строка, что видит владелец в выдаче: два канала об
                # одном состоянии не должны спорить. Заметка винила сервер там, где
                # отказала настройка (круг 5 по №321, GLM I1).
                self.note = self._refusal or \
                    "сервер эмбеддингов не ответил — недобранное дособерём позже"
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
               semantic: bool = True, embed_timeout: float = 6.0,
               dossiers: bool = True) -> Result:
        """Выдача по запросу. Индекс не прогрет — Result(ready=False): у
        вызывающего своя деградация (узлы графа, молчание). Протухший индекс
        обновляется фоном, ответ — по текущему. `dossiers=False` — ось бенча:
        сводки тем не ищутся вовсе (`_dossier_blocks` не зовётся), секция и
        вердикт считаются без них; файлы досье остаются ролью DOSSIER и в слоты
        первичной выдачи не идут, как и всегда."""
        if not self.ready:
            gen = self._gen
            return Result([], 0, ready=False, query=query, skipped=gen.skipped, unread=gen.unread,
                          service=gen.service)
        if not self._fresh():
            threading.Thread(target=self.refresh, daemon=True, name="graph-search-refresh").start()
        gen = self._gen          # одно поле — одно поколение: документы, голоса и
        all_docs = list(gen.docs.values())   # каталог не могут разъехаться по построению
        # слоты выдачи — только первичным (разрез поколения): сводка досье идёт
        # своей секцией «📁», а в «Найдено в графе» вытесняла бы заметку, которую
        # сама пересказала; целью ссылок и переходов она остаётся (`by_rel` — по всем)
        docs = list(gen.primary)
        indeg = gen.indeg
        catalog = gen.catalog
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
        # иглы от редкой к частой; пуст ровно тогда, когда пуст `keys`
        rare_first: list[str] = []
        by_rel: dict[str, Doc] = {d.rel: d for d in all_docs}
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
                score *= hub_factor(indeg.get(d.key, 0)) * placeholder_factor(d.base) * raw_dampener(d.rel, self.schema)
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
        sem_share = 0.0     # доля файлов ИНДЕКСА с актуальными векторами: свидетель «проверено
        # столько-то из прочитанного», но не свидетель по графу целиком (№295)
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
                    sem.append((sim * recency_factor(d.date_ts, now) * raw_dampener(d.rel, self.schema) * placeholder_factor(d.base), d.rel))

        if dossiers:
            dossier_pairs, dossier_cov = self._dossier_blocks(query, snippet_chars, gen)
        else:
            # ось бенча: сводки тем выключены — ни секции, ни свидетельства в вердикте
            dossier_pairs, dossier_cov = [], 0.0
        dossier_blocks = [block for _, block in dossier_pairs]
        dossier_rels = tuple(rel for rel, _ in dossier_pairs)
        # вердикт — функция ВСЕГО, что несёт Result: досье — такое же лексическое
        # свидетельство (доля ключей темы в запросе), без него статус говорил «пусто»
        # при непустой сводке, и контуры домысливали по-своему (DS I2 / I3 r4)
        status = verdict(max(best_cov, dossier_cov), best_sim, sem_used, sem_share)   # подстрока — способ поиска, не уровень свидетельства (GLM M4 r2)
        if status is not Verdict.UNVERIFIED:
            reason = ""
        elif self._refusal:
            reason = self._refusal        # слова того, кто отказал, а не наша догадка
        elif sem_used or not self.vectors:
            # Либо семантика была, но покрытия не хватило, либо кэша нет вовсе
            # и шов не спрашивали ни разу. Сказать во втором случае «модель
            # занята» — утверждение о мире, которого мы не проверяли: Ollama
            # жива, к ней просто не обращались (круг 4, DS C1).
            reason = REASON_CACHE
        else:
            reason = REASON_EMBED
        if not lex and not sem:
            # пусто по словам и по векторам — доказанное отсутствие только с проверенной
            # семантикой и без досье; без неё «ничего не найдено» читалось как факт (GLM C1 r3)
            if status is Verdict.WEAK and not dossier_blocks:
                status = Verdict.EMPTY
            return Result([], 0, status, dossiers=dossier_blocks, sem_used=sem_used, query=query,
                          reason=reason, skipped=gen.skipped, unread=gen.unread, service=gen.service,
                          sources=dossier_rels)
        low_conf = status is not Verdict.CONFIDENT
        fused = rrf_merge([[r for _, r in sorted(lex, key=lambda x: -x[0])],
                           [r for _, r in sorted(sem, key=lambda x: -x[0])]], weights=[1.0, 0.7])
        fused = _swap_stubs(fused, by_rel, catalog)
        picked = diversify([(s, r) for r, s in fused], limit)
        blocks: list[str] = []
        shown: list[str] = []
        for rel in picked:
            d = by_rel[rel]
            frag = self._fragment(d, rx, snippet_chars, rare_first)
            blocks.append(f"• {rel}\n  {frag}")
            shown.append(rel)
        total = len(fused)
        if not low_conf:
            hops = self._hops(shown, by_rel, catalog, keys, rx, snippet_chars, rare_first, max(1, limit // 2))
            blocks += [block for _, block in hops]
            shown += [rel for rel, _ in hops]
            total += len(hops)
        return Result(blocks, total, status, dossiers=dossier_blocks, sem_used=sem_used, query=query,
                      reason=reason, skipped=gen.skipped, unread=gen.unread, service=gen.service,
                      sources=dossier_rels + tuple(shown))

    def _dossier_blocks(self, query: str, snippet_chars: int, gen: Generation,
                        limit: int = 2) -> tuple[list[tuple[str, str]], float]:
        """Готовые сводки по теме — ПЕРЕД фрагментами: индекс лексический, без
        моделей. -> (блоки, лучшая доля ключей темы в запросе — в вердикт как покрытие).

        Индекс тем (`Досье/_index.json`) читается с диска — сознательно, кэш
        снят (входной круг GLM по №296). Тело сводки — из поколения по карте
        `gen.dossiers` (ключ — `Doc.key`, нормализованный путь без расширения):
        имя на диске знает только обход, а тема из JSON в путь не склеивается —
        ни `../` вне папки, ни расхождение регистра или формы Unicode между
        темой и файлом (выходной круг DS I1 / GLM M3). Сводки, которой в снимке
        ещё нет, в ответе нет — до следующего обхода. Блоки — парами (`rel`
        сводки, текст): путь идёт в `Result.sources`."""
        if self.schema.dossier_dir is None:
            return [], 0.0              # у хранилища нет роли досье — секции нет
        folder = self.graph / self.schema.dossier_dir
        try:
            entries = dossier.lookup(folder, query, limit=limit)
        except (OSError, ValueError, KeyError, TypeError):   # битый индекс — без секции, не без ответа
            return [], 0.0
        out: list[tuple[str, str]] = []
        best = 0.0
        for e in entries:
            if e.get("счёт", 0) < 0.3:
                continue
            d = gen.dossiers.get(norm_text(f"{self.schema.dossier_dir}/{e['тема']}"))
            if d is None:
                continue
            head = " ".join((d.body or d.text)[:snippet_chars * 3].split())
            out.append((d.rel, f"📁 Досье «{e['тема']}»\n  {head}"))
            best = max(best, min(1.0, float(e.get("счёт", 0))))
        return out, best

    def _fragment(self, d: Doc, rx: re.Pattern, chars: int, rare: Sequence[str]) -> str:
        """Фрагмент документа для выдачи и для перехода — одно правило на оба места:
        текст без YAML-шапки, у дистиллята окно шире (`snippet(dense=…)`), у сырья
        по схеме — обычное."""
        return _frag_or_head(d.body or d.text, rx, chars, rare, dense=not self.schema.is_raw(d.rel))

    def _hops(self, shown: list[str], by_rel: dict[str, Doc], catalog: LinkCatalog, keys: list[str],
              rx: re.Pattern, snippet_chars: int, rare_first: Sequence[str], limit: int) -> list[tuple[str, str]]:
        """Один переход по [[ссылкам]] из найденных узлов: заметки со стемами
        запроса ВНЕ имени узла (покрытие × свежесть), при голом имени — самые
        свежие; тёзки в разных папках — один кандидат; по одному слоту на узел,
        потом добор — первый узел не съедает бюджет.

        Ссылка на слитый узел ведёт к канону: иначе свежайшим кандидатом под
        базой оказывается заглушка-редирект, её отбрасывает фильтр узлов, и
        переход пропадает молча (замер 17.09: 40 переходов из 27 663 целей —
        столько доезжает до выдачи после всех фильтров и лимитов). Переходы —
        парами (`rel` цели, блок): путь идёт в `Result.sources`."""
        nodes = [r for r in shown if self.schema.is_node_path(r)]
        if not nodes or limit <= 0:
            return []
        out: list[tuple[str, str]] = []
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
                    hit = catalog.live(base)
                    for d in ([hit] if hit is not None else []):
                        if d.rel in seen or self.schema.is_node_path(d.rel) or d.role != PRIMARY:
                            continue      # переход — к первичной заметке, не к узлу и не к сводке
                        matched = sum(1 for k in other if k in d.low)
                        if other and not matched:
                            continue
                        cov = matched / len(other) if other else 1.0
                        cands.append((cov * recency_factor(d.date_ts, self._now()) * raw_dampener(d.rel, self.schema), d, matched))
                cands.sort(key=lambda x: (x[0], x[1].rel), reverse=True)
                for _s, d, _m in cands[:min(per_node, limit - len(out))]:
                    frag = self._fragment(d, rx, snippet_chars, rare_first)
                    seen.add(d.rel)
                    out.append((d.rel, f"• {d.rel}\n  ↳ по ссылке из {node_rel}\n  {frag}"))
                    if len(out) >= limit:
                        return out
        return out


def _swap_stubs(hits: Sequence[tuple[str, float]], by_rel: dict[str, Doc],
                catalog: LinkCatalog) -> list[tuple[str, float]]:
    """Заглушка-редирект в выдаче → канон, на который она указывает.

    Заглушка — не документ, а указатель: блок показал бы одну стрелку вместо
    содержания. Выбросить её тоже нельзя — старое имя ищут («как это раньше
    называли»), поэтому её ранг достаётся канону. Подмена идёт ДО отбора: иначе
    совпавший с уже показанным канон терял слот, и выдача молча становилась
    короче на строку (замер 17.09 на рабочем графе, запрос про статус человека)."""
    if not any(by_rel[rel].stub_to for rel, _ in hits):
        return list(hits)
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for rel, score in hits:
        d = by_rel[rel]
        if d.stub_to:
            target = catalog.instead_of_stub(d)
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
    body = frontmatter.split(text)[1]
    return " ".join(body[:chars].split())


class NotReady(RuntimeError):
    """Индекс ещё прогревается — контур деградирует по-своему и пробует позже."""


class Unavailable(RuntimeError):
    """Памяти по графу не будет: граф не настроен."""


def coverage_gaps(result: Result) -> list[str]:
    """Чего индекс НЕ читал — словами, из одного места: фасад `render`, шапка блока
    памяти и статус нити (`brain.scope_note`) собирали одну мысль тремя циклами,
    и новое поле охвата дошло до человека, но не до модели (выходной круг DS I2 /
    GLM I2 по №296). Порядок — как весит: области, нечитаемые, служебные."""
    gaps = list(result.skipped)
    if result.unread:
        gaps.append(f"не открылось файлов: {result.unread}")
    if result.service:
        gaps.append(f"служебных файлов вне индекса: {result.service}")
    return gaps


def render(result: Result, query: str | None = None, where: str = "графе") -> str:
    """Текст выдачи человеку (CLI, журнал, тесты формата) в том же виде, что
    отдавал сервер памяти: «⚠ …» первой строкой, досье, шапка «Найдено…»,
    «Ничего не найдено по «…»». Контуры демона и бенч читают Result.status и
    Result.fragments — маркеры здесь никто не разбирает (круг 3 по #577)."""
    query = result.query if query is None else query
    if not result.ready:
        return ""
    # «пусто» — сильнейшее утверждение модуля, и непрочитанное весит в нём
    # больше всего: слабая форма оговорку получила, сильная оставалась без неё
    # (GLM, круг по №295)
    gaps = coverage_gaps(result)
    tail = f" (искали без: {', '.join(gaps)})" if gaps else ""
    if result.empty:
        if result.status is Verdict.UNVERIFIED:
            return (f"⚠ По словам ничего не нашлось по «{query}» в {where}{tail}, семантикой не проверено "
                    f"({result.reason}) — не считать доказанным отсутствием")
        return f"Ничего не найдено по «{query}» в {where}{tail}"
    parts = list(result.dossiers)
    if result.blocks:
        if parts:
            parts.append("— — — ниже отдельные фрагменты графа — — —")
        parts.append(f"Найдено в {where} ({len(result.blocks)} из {result.total}):\n\n" + "\n\n".join(result.blocks))
    # иначе — только сводка: «Найдено (0 из 0)» под ней врало бы
    warn = f"⚠ {result.why_low}. Ниже найденное:\n" if result.low_conf else ""   # первой строкой, ПЕРЕД досье (DS C1 r3)
    return warn + "\n\n".join(parts)

