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
import json
import math
import operator
import os
import pathlib
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dossier  # noqa: E402
import frontmatter  # noqa: E402
import graph_nodes  # noqa: E402
import graphs  # noqa: E402
import llm as _llm  # noqa: E402

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
LOW_SIM, LOW_COV = 0.47, 0.67   # гейт честности: слабы ОБА сигнала — «⚠»
HALFLIFE_DAYS = 90.0
_STOP = {"что", "как", "где", "когда", "это", "нас", "наш", "наша", "наши", "есть",
         "про", "для", "или", "чем", "кто", "было", "быть", "графе", "граф", "мы",
         "решили", "the", "and", "what", "who", "how", "did", "for", "with"}
_WORD_RX = re.compile(r"[А-Яа-яЁёA-Za-z0-9_-]{3,}")
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
    """Регистр, ё→е, полноширинные латиница и цифры → обычные."""
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
    query = query.translate(_FULLWIDTH)      # ＹｕＰａｙ — слово, а не пропуск
    words = [graph_nodes.stem(w) for w in _WORD_RX.findall(query) if norm(w) not in _STOP]
    return list(dict.fromkeys(norm(w) for w in words if w)), list(dict.fromkeys(cjk_grams(query)))


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
    ничего. Не больше `limit` блоков на файл."""
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
        if len(sec) < 40:
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
            if len(out) >= limit:
                return out
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


@dataclasses.dataclass
class Result:
    blocks: list[str]
    total: int
    low_conf: bool
    ready: bool = True
    dossiers: list[str] = dataclasses.field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.blocks and not self.dossiers


def _dot(a, b) -> float:
    return math.sumprod(a, b) if hasattr(math, "sumprod") else sum(map(operator.mul, a, b))


def _unit(vec: Sequence[float]) -> array.array:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array.array("f", (x / n for x in vec))


class GraphSearch:
    """Индекс одного графа и поиск по нему. Один экземпляр на процесс и граф
    (см. shared()); обновление индекса и поиск — из разных потоков."""

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
        self._refreshed_at = 0.0
        self._lock = threading.RLock()       # индекс и векторы
        self._scan_lock = threading.Lock()   # один обход за раз
        self._vecs: dict[str, tuple[float, list[array.array]]] = {}   # путь → (mtime, векторы блоков)
        base = pathlib.Path(data_dir) if data_dir else graphs.DATA_ROOT / "data"
        self._vec_manifest = base / "graph_search" / f"{self.graph.name}.json"
        self._vec_file = self._vec_manifest.with_suffix(".f32")
        self._vecs_loaded = False

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
                        text = fh.read()
                except OSError:
                    continue
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                fresh[path] = Doc(path, rel, mtime, text, norm(text), file_date_ts(rel, mtime),
                                  norm_text(os.path.splitext(fn)[0]))
                changed = True
        gone = [p for p in self._docs if p not in seen]
        if not fresh and not gone:
            return
        with self._lock:
            for p in gone:
                self._docs.pop(p, None)
            self._docs.update(fresh)
            if changed or gone:
                indeg: dict[str, int] = {}
                for d in self._docs.values():
                    for target in wiki_targets(d.text):
                        indeg[target] = indeg.get(target, 0) + 1
                self._indeg = indeg

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
        """Кэш векторов с диска: манифест (путь, mtime, число блоков) + плоский float32."""
        if self._vecs_loaded:
            return len(self._vecs)
        self._vecs_loaded = True
        try:
            manifest = json.loads(self._vec_manifest.read_text(encoding="utf-8"))
            dim, entries = int(manifest["dim"]), manifest["files"]
            flat = array.array("f")
            with open(self._vec_file, "rb") as fh:
                flat.frombytes(fh.read())
            if len(flat) != dim * sum(int(n) for _, _, n in entries):
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            return 0
        with self._lock:
            off = 0
            for path, mtime, n in entries:
                vecs = [flat[off + i * dim:off + (i + 1) * dim] for i in range(int(n))]
                off += int(n) * dim
                self._vecs[path] = (float(mtime), vecs)
        return len(self._vecs)

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
        tmp = self._vec_file.with_name(self._vec_file.name + f".tmp{os.getpid()}")
        with open(tmp, "wb") as fh:
            fh.write(flat.tobytes())
        tmp.replace(self._vec_file)
        self._vec_manifest.write_text(json.dumps(
            {"dim": dim, "files": [[p, m, len(vs)] for p, m, vs in items]}, ensure_ascii=False), encoding="utf-8")

    def pending_vectors(self) -> list[str]:
        self.load_vectors()
        with self._lock:
            return [p for p, d in self._docs.items() if self._vecs.get(p, (None, None))[0] != d.mtime]

    def embed_pending(self, budget_s: float | None = None, batch: int = EMBED_BATCH,
                      timeout: float = 60.0) -> int:
        """Доиндексация файлов с изменившимся mtime — по блокам, пачками, с
        потолком по времени. Вызывать ВНЕ живой записи: сотни файлов — минуты
        работы модели эмбеддингов, на встрече они отняли бы слот у подсказок.
        Файл готов, когда есть векторы всех его блоков; сервер не ответил —
        останавливаемся, недобранное дособерём в следующий раз. -> сколько
        файлов получили векторы."""
        started = self._now()
        done = 0
        queue: list[tuple[str, float, int, int, str]] = []   # путь, mtime, номер блока, всего, текст
        with self._lock:
            for p in self.pending_vectors():
                d = self._docs.get(p)
                if d is None:
                    continue
                parts = chunks(pathlib.PurePosixPath(d.rel).stem, d.text) or [d.text[:CHUNK_CHARS]]
                queue += [(p, d.mtime, i, len(parts), t) for i, t in enumerate(parts)]
        got: dict[str, tuple[float, int, dict[int, array.array]]] = {}
        for i in range(0, len(queue), batch):
            if budget_s is not None and self._now() - started > budget_s:
                break
            part = queue[i:i + batch]
            embs = self._embed([t for _, _, _, _, t in part], timeout)
            if len(embs) != len(part):
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
            return Result([], 0, False, ready=False)
        if not self._fresh():
            threading.Thread(target=self.refresh, daemon=True, name="graph-search-refresh").start()
        with self._lock:
            docs = list(self._docs.values())
            indeg = dict(self._indeg)
        avg_len = max(1.0, sum(len(d.low) for d in docs) / max(1, len(docs)))
        words, grams = needles(query)
        keys = words + grams
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
        if semantic and (self._vecs or self.load_vectors()):
            qv = self._embed([query], embed_timeout)
            if qv and qv[0]:
                q = _unit(qv[0])
                with self._lock:
                    vecs = list(self._vecs.items())
                paths = {d.path: d for d in docs}
                sims = []
                for path, (_m, vs) in vecs:
                    d = paths.get(path)
                    if d is None:
                        continue
                    sim = max((_dot(q, v) for v in vs if len(v) == len(q)), default=0.0)   # лучший блок файла
                    if sim >= SIM_FLOOR:
                        sims.append((sim, d))
                sims.sort(key=lambda x: -x[0])
                best_sim = sims[0][0] if sims else 0.0
                for sim, d in sims[:max(limit * 4, 20)]:
                    sem.append((sim * recency_factor(d.date_ts, now) * raw_dampener(d.rel), d.rel))

        dossiers = self._dossier_blocks(query, snippet_chars)
        if not lex and not sem:
            return Result([], 0, False, dossiers=dossiers)
        low_conf = best_sim < LOW_SIM and best_cov < LOW_COV
        fused = rrf_merge([[r for _, r in sorted(lex, key=lambda x: -x[0])],
                           [r for _, r in sorted(sem, key=lambda x: -x[0])]], weights=[1.0, 0.7])
        picked = diversify([(s, r) for r, s in fused], limit)
        blocks: list[str] = []
        shown: list[str] = []
        for rel in picked:
            d = by_rel[rel]
            frag = snippet(d.text, rx, snippet_chars, rare_first or keys, dense=raw_dampener(rel) == 1.0) \
                or " ".join(d.text[:snippet_chars].split())
            blocks.append(f"• {rel}\n  {frag}")
            shown.append(rel)
        total = len(fused)
        if not low_conf:
            hops = self._hops(shown, by_rel, keys, rx, snippet_chars, rare_first or keys, max(1, limit // 2))
            blocks += hops
            total += len(hops)
        return Result(blocks, total, low_conf, dossiers=dossiers)

    def _dossier_blocks(self, query: str, snippet_chars: int, limit: int = 2) -> list[str]:
        """Готовые сводки по теме — ПЕРЕД фрагментами: индекс лексический, без моделей."""
        folder = self.graph / dossier.DOSSIER_DIR
        try:
            entries = dossier.lookup(folder, query, limit=limit)
        except Exception:  # noqa: BLE001 — досье вспомогательны
            return []
        out: list[str] = []
        for e in entries:
            if e.get("счёт", 0) < 0.3:
                continue
            p = folder / f"{e['тема']}.md"
            try:
                body = frontmatter.split(p.read_text(encoding="utf-8"))[1]
            except (OSError, ValueError):
                continue
            head = " ".join(body[:snippet_chars * 3].split())
            out.append(f"📁 Досье «{e['тема']}»\n  {head}")
        return out

    def _hops(self, shown: list[str], by_rel: dict[str, Doc], keys: list[str], rx: re.Pattern,
              snippet_chars: int, rare_first: Sequence[str], limit: int) -> list[str]:
        """Один переход по [[ссылкам]] из найденных узлов: заметки со стемами
        запроса ВНЕ имени узла (покрытие × свежесть), при голом имени — самые
        свежие; тёзки в разных папках — один кандидат; по одному слоту на узел,
        потом добор — первый узел не съедает бюджет."""
        nodes = [r for r in shown if is_node_path(r)]
        if not nodes or limit <= 0:
            return []
        by_base: dict[str, list[Doc]] = {}
        for d in by_rel.values():
            by_base.setdefault(d.base, []).append(d)
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
                    best = sorted(by_base.get(base, ()), key=lambda d: -d.date_ts)[:1]
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
                    frag = snippet(d.text, rx, snippet_chars, rare_first, dense=raw_dampener(d.rel) == 1.0)
                    if not frag:
                        continue
                    seen.add(d.rel)
                    out.append(f"• {d.rel}\n  ↳ по ссылке из {node_rel}\n  {frag}")
                    if len(out) >= limit:
                        return out
        return out


def render(result: Result, query: str, where: str = "графе") -> str:
    """Текст выдачи в том же виде, что отдавал сервер памяти: шапка «Найдено…»,
    «⚠» при слабых совпадениях, «Ничего не найдено по «…»» — потребители
    (мгновенный ответ, дежавю, глубокий контур, бенч) читают эти маркеры."""
    if not result.ready:
        return ""
    if result.empty:
        return f"Ничего не найдено по «{query}» в {where}"
    header = f"Найдено в {where} ({len(result.blocks)} из {result.total}):"
    if result.low_conf:
        header = "⚠ Похоже, в архиве об этом почти ничего нет (слабые совпадения). Ниже ближайшее найденное:\n" + header
    body = header + "\n\n" + "\n\n".join(result.blocks) if result.blocks else header
    if result.dossiers:
        body = "\n\n".join(result.dossiers) + "\n\n— — — ниже отдельные фрагменты графа — — —\n\n" + body
    return body


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
