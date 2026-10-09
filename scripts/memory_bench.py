#!/usr/bin/env python3
"""Мини-бенч памяти: эталонные вопросы по архиву → проверка фактов в ответе.

Регрессионные тесты, но для КАЧЕСТВА ПАМЯТИ, а не кода: пороги NLI, длина
сниппетов, промпты синтеза крутятся часто — и каждый твик может незаметно
уронить ответы по архиву. Бенч гоняет реальный RAG-контур (поиск по графу →
синтез локальной моделью) на вопросах из config/memory_bench.yaml и
проверяет, что обязательные факты (`must`) присутствуют в ответе.

Формат memory_bench.yaml:
    - q: "Что решили по доступу к API?"
      must: ["токен", "403"]

Сверка подстрокой: нормализация регистра и ё→е. Итог N/M и список провалов;
exit code 1, если провалов больше трети — заметная деградация.

    .venv/bin/python scripts/memory_bench.py            # весь бенч
    .venv/bin/python scripts/memory_bench.py --limit 3  # быстрый смок
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import math
import os
import pathlib
import re
import sys
import time
from typing import NamedTuple

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import brain  # noqa: E402
import file_locks  # noqa: E402
import graphs  # noqa: E402
import deps  # noqa: E402
from charoite_paths import code_root, harden_umask, log_path, resolve_root  # noqa: E402


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402
from llm import LLM, embedder as build_embedder  # noqa: E402

SNIPPET = 1200   # как в боевом RAG приложения
LIMIT_FILES = 5


# Полноширинный ASCII (U+FF01–U+FF5E) — обычный: китайские модели пишут «９月»
# и «９月１日» наравне с «9月», и строгая сверка давала ложный провал на верном
# ответе (ревью 19.08, второй круг).
FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}


def norm(s: str) -> str:
    return s.lower().replace("ё", "е").translate(FULLWIDTH)


# Иероглифы пробелами не разделяются, поэтому «слова» из них не нарезать:
# запрос 支付服务商最后定了哪一家？ давал ПУСТОЙ список слов, поиск скатывался
# на поиск всей фразы целиком и не находил ничего (замер 19.08: 0/3 на
# китайском демо-графе против 2/3 на английском). Берём скользящие биграммы —
# стандартный приём для языков без пробелов: 支付服务商 → 支付, 付服, 服务, 务商.
CJK = (r"\u4e00-\u9fff"      # китайский, основной блок
       r"\u3400-\u4dbf"      # расширение A
       r"\uf900-\ufaff"      # совместимость (иероглифы из старых кодировок)
       r"\u3040-\u30ff"      # японские каны
       r"\uff66-\uff9f"      # полуширинная катакана
       r"\uac00-\ud7af"      # корейский хангыль
       r"\U00020000-\U0002ee5f"      # расширения B–F и I
       r"\U0002f800-\U0002fa1f"      # совместимость, дополнение
       r"\U00030000-\U000323af")  # расширения G и H — отдельный остров


# Пробел рядом с иероглифом. Сжимать всю строку было нельзя: тогда
# contains("YuPay 支付", "Yu Pay 支付") давал True — латиница склеивалась заодно,
# и один случайный иероглиф рядом менял вердикт по совсем другому факту
# (ревью 19.08, второй круг).
# Пробелы и идеографический пробел U+3000 (в китайском тексте он обычный),
# но НЕ переносы строк: перенос — граница абзаца, склеивать через него
# значит выдавать «конец одной мысли + начало другой» за совпадение
# (ревью 20.08, локальная голова).
CJK_SPACE = re.compile(f"(?<=[{CJK}])[ \\t\\u3000]+|[ \\t\\u3000]+(?=[{CJK}])")


def needles(query: str, stop: set[str]) -> tuple[list[str], list[str]]:
    """Иглы запроса: слова и биграммы иероглифов, каждая по одному разу.

    Дедуп обязателен: повтор удваивал вклад иглы в счёт («服务服务商» даёт
    «服务» дважды), и файл с одной частой биграммой обгонял релевантный.
    Пересечься списки не могут по построению — слова собираются из латиницы,
    кириллицы и цифр, граммы только из иероглифов.
    """
    words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9_-]{3,}", query)
             if norm(w) not in stop]
    return list(dict.fromkeys(words)), list(dict.fromkeys(cjk_grams(query)))


def contains(needle: str, text: str) -> bool:
    """Есть ли ожидаемый факт в ответе.

    Прямое вхождение — как раньше. Для иероглифов добавлена сверка по сжатой
    форме: модель расставляет пробелы между знаками произвольно («9 月 1 日»
    против «9月1日»), в китайском они не значимы, и строгая сверка давала
    ложный провал на верном ответе (замер 19.08). Русский и английский путь
    не меняется: сжатие включается только когда в ожидании есть иероглифы.
    """
    if norm(needle) in norm(text):
        return True
    if re.search(f"[{CJK}]", needle):
        return CJK_SPACE.sub("", norm(needle)) in CJK_SPACE.sub("", norm(text))
    return False


def cjk_grams(query: str) -> list[str]:
    """Биграммы из иероглифических кусков запроса (китайский, японский, корейский)."""
    grams: list[str] = []
    for run in re.findall(f"[{CJK}]+", query):
        if len(run) == 1:
            grams.append(run)
        else:
            grams += [run[i:i + 2] for i in range(len(run) - 1)]
    return grams


# Промпт синтеза на языке кейсов. Раньше он был только русским, и на
# английском/китайском демо-графе модель отвечала по-русски: кейс с «September»
# падал не потому, что факт потерян, а потому, что в ответе стояло «1 сентября»
# (замер 19.08 — по одному ложному провалу на каждом нерусском графе). Бенч
# обязан мерить то, что увидит пользователь на СВОЁМ языке.
SYNTH = {
    "ru": (
        "Вопрос: {q}\n\nФрагменты из архива встреч:\n{found}\n\n"
        "Ответь на вопрос по фрагментам: кратко, с конкретными фактами "
        "(имена, числа, идентификаторы) из фрагментов. Ничего не выдумывай.",
        "Ты — ассистент по архиву рабочих встреч. Только факты из фрагментов.",
    ),
    "en": (
        "Question: {q}\n\nFragments from the meeting archive:\n{found}\n\n"
        "Answer the question from the fragments: briefly, with the concrete "
        "facts (names, numbers, identifiers) they contain. Invent nothing. "
        "Answer in English.",
        "You are an assistant over an archive of work meetings. "
        "Only facts from the fragments. Answer in English.",
    ),
    "zh": (
        "问题：{q}\n\n会议档案片段：\n{found}\n\n"
        "请根据片段回答问题：简洁，并给出片段中的具体事实"
        "（姓名、数字、编号）。不要编造。请用中文回答。",
        "你是会议档案助手。只使用片段中的事实，请用中文回答。",
    ),
}


def resolve_lang(cfg, *, demo_zh: bool, demo_en: bool, demo: bool) -> str:
    """Язык промпта синтеза.

    Каждый демо-флаг называет язык своего графа сам: спрашивать русский
    демо-граф английским промптом бессмысленно, даже если в конфиге стоит `en`.
    В обычном прогоне язык берётся ОТТУДА ЖЕ, откуда его берёт приложение —
    `sufler.language`. Иначе владелец нерусского vault получал русский промпт,
    ответ на русском и ложные провалы ночной джобы (ревью 19.08, DeepSeek).

    `cfg` бывает `None`: пустой config.yaml проходит `yaml.safe_load` молча,
    и обращение к нему падало бы AttributeError вместо честной работы по
    умолчанию (ревью 19.08, второй круг, локальная голова).
    """
    if demo_zh:
        return "zh"
    if demo_en:
        return "en"
    if demo:
        return "ru"
    value = ((cfg or {}).get("sufler") or {}).get("language", "ru")
    lang = str(value).strip().lower()
    return lang if lang in SYNTH else "ru"


# Исходы обращения к серверу памяти — одно значение с причиной вместо None на
# три случая: «лежит», «жив, но граф вне его vault» и «жив, пусто» раньше
# сливались, и отказ по --brain объяснялся бы неверно (входной круг DS по №296)
BRAIN_DEAD = "dead"          # :8100 не отвечает (выключен решением по №250)
BRAIN_FOREIGN = "foreign"    # сервер жив, но этого графа в его vault нет (демо, другой диск)
BRAIN_ALIVE = "alive"

# Чат приложения ждёт сервер-компаньон 4 с (ArchiveSearch.swift) и молча уходит
# в свой поиск на Swift; бенч ждёт дольше, чтобы напечатать задержку, но вопрос
# дольше порога в сравнение не берёт — в продукте ответ дал бы другой поиск
COMPANION_DEADLINE_S = 4.0
COMPANION_LIMIT, COMPANION_SNIPPET = 8, 800     # параметры чата приложения


class BrainHit(NamedTuple):
    """Ответ сервера памяти: исход, текст выдачи без шапки, «⚠» полем, задержка."""
    outcome: str
    text: str
    low_conf: bool = False
    elapsed: float = 0.0


def search_brain(graph: pathlib.Path, query: str, *, limit: int = LIMIT_FILES,
                 snippet: int = SNIPPET, timeout: float = 25) -> BrainHit:
    """Сервер памяти :8100 (`vault_search`). Сейчас это сервер-компаньон чата
    приложения у владельца; у демона его нет с №250.

    -> BrainHit: текст непустой только при BRAIN_ALIVE; пустая выдача живого
    сервера — ("alive", "").
    """
    import json
    import time
    import urllib.request

    from charoite_graph.net import open_url

    folder = graph.name  # стандартная раскладка: vault/<граф>/…
    # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
    req = urllib.request.Request(
        "http://127.0.0.1:8100/vault_search",
        data=json.dumps({"query": query, "folder": folder,
                         "limit": limit, "snippet_chars": snippet},
                        ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        # nosemgrep — адрес локального brain/Ollama из конфига, не внешний ввод
        with open_url(req, timeout=timeout) as resp:
            text = json.load(resp).get("text", "")
    except (OSError, ValueError):
        return BrainHit(BRAIN_DEAD, "", elapsed=time.monotonic() - t0)
    elapsed = time.monotonic() - t0
    if text.startswith("Ничего не найдено"):
        return BrainHit(BRAIN_ALIVE, "", elapsed=elapsed)
    if text.startswith("Папка не найдена") or text.startswith("Недопустимый путь"):
        return BrainHit(BRAIN_FOREIGN, "", elapsed=elapsed)
    low = text.startswith("⚠")
    # срезаем шапку «Найдено в vault (N из M):»
    _, _, body = text.partition("\n\n")
    return BrainHit(BRAIN_ALIVE, body or text, low, elapsed)


def require_brain(graph: pathlib.Path, demo: bool, flag: str = "--brain") -> None:
    """Сервер памяти — явный отказ с причиной, а не молчаливый уход на другой контур:
    иначе заголовок «память демона» печатался при любом выборе (№296)."""
    if demo:
        sys.exit(f"{flag} неприменим к демо-графу: его нет в vault сервера памяти")
    outcome = search_brain(graph, "проверка").outcome
    if outcome == BRAIN_DEAD:
        sys.exit(f"{flag}: сервер памяти :8100 не отвечает — у пользователей его нет, у демона "
                 "он выключен решением по №250; снимите флаг или поднимите сервер")
    if outcome == BRAIN_FOREIGN:
        sys.exit(f"{flag}: сервер памяти жив, но этого графа в его vault нет — флаг неприменим")


_INDEX: dict[str, object] = {}


def search(graph: pathlib.Path, query: str, cfg: dict | None = None) -> str:
    """Режим `raw` — прежний контур бенча (5 файлов × 1 200 знаков, свой SYNTH): демо
    en/zh и одно сравнение с базой 23/37. Потребителей демона мерят профили
    (`--profile answer|live|expand`), а не этот вход."""
    mem = _INDEX.get(str(graph))
    if mem is None:
        mem = _INDEX[str(graph)] = graphs.open_search(graph, build_embedder(cfg or {}))
        mem.refresh(force=True)
        mem.load_vectors()
    result = mem.search(query, limit=LIMIT_FILES, snippet_chars=SNIPPET)
    # в промпт синтеза — фрагменты без шапки и «⚠»: маркер модель читает как
    # указание отказаться, и провал поиска маскируется провалом синтеза (DS I3 r3)
    return "" if result.empty else result.fragments


def search_legacy(graph: pathlib.Path, query: str) -> str:
    """Прежний локальный фолбэк (до №250): слова → счёт файлов → сниппеты. Остался
    для сравнения «до/после» и как эталон иголок с иероглифами."""
    stop = {"что", "как", "где", "когда", "это", "нас", "есть", "про", "для",
            "или", "чем", "кто", "было", "быть", "по", "мы", "решили"}
    words, grams = needles(query, stop)
    words = words + grams
    # Гейт и скоринг обязаны смотреть на текст ОДИНАКОВО. Раньше rx искал по
    # сырому тексту, а счёт считался по norm() — после того, как norm научился
    # схлопывать полноширинные формы, запрос «ＹｕＰａｙ» выбрасывал файл с
    # «YuPay» ещё до скоринга, который его бы засчитал (ревью 20.08, DeepSeek).
    # Без re.I намеренно: обе стороны уже прошли norm() с lower(), и флаг был
    # единственным местом, где гейт структурно отличался от скоринга. Пока его
    # нет, гейт — буквально тот же предикат «norm(игла) в norm(тексте)»
    # (ревью 20.08, четвёртый круг).
    #
    # Цена замерена: IGNORECASE в Python шире, чем lower(), на двух парах —
    # греческая конечная сигма (ς против σ) и турецкая İ против i. Для них
    # гейт стал строже. Это осознанно: скоринг такие пары и раньше не
    # засчитывал, то есть файл проходил гейт и выводился со счётом 0.
    # Согласованность гейта и счёта важнее, чем совпадение, которое всё равно
    # не влияло на ранжирование.
    rx = (re.compile("|".join(re.escape(norm(w)) for w in words)) if words
          else re.compile(re.escape(norm(query))))
    scored: list[tuple[int, str]] = []
    for p in graph.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # norm() теперь считается для КАЖДОГО файла, а не только для прошедших
        # гейт: это принятая цена того, что гейт и скоринг смотрят на текст
        # одинаково. Не «оптимизировать» обратно на сырой текст — вернётся
        # расхождение, из-за которого полноширинный запрос терял файлы.
        low = norm(text)
        m = rx.search(low)
        if not m:
            continue
        # norm() почти всегда сохраняет длину, но lower() у отдельных символов
        # её меняет («İ» → два кодпоинта). Тогда позиции из low к оригиналу не
        # приложить — режем сниппет из нормализованной копии.
        source = text if len(low) == len(text) else low
        rel = str(p.relative_to(graph))
        score = sum(1 for w in words if norm(w) in low)
        # Буст за попадание в ПУТЬ у биграмм слабее: двух иероглифов слишком
        # мало, чтобы считать совпадение с именем файла осмысленным — частая
        # биграмма («现在», «我们») иначе перевешивает редкое точное слово
        # (ревью 19.08, DeepSeek).
        score += sum(3 for w in words if w not in grams and norm(w) in norm(rel))
        score += sum(1 for w in grams if norm(w) in norm(rel))
        start = max(0, m.start() - 150)
        frag = " ".join(source[start:m.end() + SNIPPET].split())
        scored.append((score, f"• {rel}\n  …{frag}…"))
    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(h for _, h in scored[:LIMIT_FILES])


PROFILE_CHOICES = ("raw", "answer", "live", "expand", "companion")


class Q(NamedTuple):
    """Итог вопроса эталона в профиле — то, что пишется в базу и сравнивается.

    `why` — причина провала: «срезано бюджетом» (факт был в выдаче, бюджет блока
    его отрезал), «не выдано», «поиск пуст», «синтез» (факт в блоке, модель его
    потеряла). `new_pos` / `old_pos` — позиция нового и старого значения в блоке
    у вопросов `latest` (диагностика порядка; None — нет в блоке). `comparable` —
    False, когда вопрос вне сравнения: компаньон дольше порога чата, ответ дал
    облачный откат на локальную модель."""
    n: int
    cat: str
    ok: bool
    sem_used: bool | None
    status: str
    why: str = ""
    new_pos: int | None = None
    old_pos: int | None = None
    comparable: bool = True


class Timings:
    """Задержки по этапам: поиск, упаковка, синтез (первый токен и целиком)."""

    STAGES = ("поиск", "упаковка", "синтез: первый токен", "синтез целиком")

    def __init__(self):
        self.data: dict[str, list[float]] = {s: [] for s in self.STAGES}

    def add(self, stage: str, seconds: float) -> None:
        self.data[stage].append(seconds)

    def lines(self) -> list[str]:
        out = []
        for stage, xs in self.data.items():
            if xs:
                out.append(f"  {stage}: p50 {percentile(xs, 0.5):.3f} с, p95 {percentile(xs, 0.95):.3f} с, n={len(xs)}")
        return out


def percentile(xs: list[float], q: float) -> float:
    """Ближайший ранг: на 37 вопросах интерполяция ничего не уточняет."""
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))] if s else 0.0


def first_pos(needles: list[str], text: str) -> int | None:
    """Позиция первой из игл в тексте по нормализованному виду; None — нет ни одной."""
    low = norm(text)
    found = [p for p in (low.find(norm(n)) for n in needles if n) if p >= 0]
    return min(found) if found else None


def warm_profile(profile: brain.Profile, graph: pathlib.Path, emb) -> str:
    """Прогрев до цикла: обход графа и векторы из кэша — тот же общий индекс, что
    потом спросит `brain.search`, — и один вектор запроса: холодная модель
    эмбеддингов иначе ложится задержкой на первый вопрос. Холодный старт —
    отдельной строкой, в p50/p95 он не входит."""
    t0 = time.perf_counter()
    mem = brain.warm({}, graph=graph, embedder=emb)
    t1 = time.perf_counter()
    try:
        brain.search(profile, "прогрев векторизатора", graph=graph, embedder=emb)
    except (brain.MemoryNotReady, brain.MemoryUnavailable) as exc:
        sys.exit(f"память по графу не готова после прогрева: {exc}")
    t2 = time.perf_counter()
    print(f"холодный старт: индекс {t1 - t0:.1f} с (файлов {mem.size}, с векторами {mem.vectors}), "
          f"первый вектор запроса {t2 - t1:.2f} с")
    return mem.fingerprint()


def retrieve(profile: brain.Profile, case: dict, n: int, graph: pathlib.Path, emb,
             timings: Timings) -> tuple[Q, brain.Packed, object]:
    """Выдача профиля тем же швом, что у демона: `brain.search` + `brain.pack`.

    Факт засчитан, если он в `Packed.body` (вошедшие фрагменты) или
    `Packed.nodes`: слова шапки и оговорки фактом не считаются (DS C2 r3). Узлов
    у бенча нет — `nodes` пуст (`nodes=none` в записи)."""
    t0 = time.perf_counter()
    result = brain.search(profile, case["q"], graph=graph, embedder=emb)
    t1 = time.perf_counter()
    packed = brain.pack(profile, result)
    timings.add("поиск", t1 - t0)
    timings.add("упаковка", time.perf_counter() - t1)
    must = case.get("must") or []
    seen = packed.body + "\n" + packed.nodes
    missing = [m for m in must if not contains(m, seen)]
    why = ""
    if missing:
        if result.empty:
            why = "поиск пуст"
        elif all(contains(m, result.fragments) for m in missing):
            why = "срезано бюджетом"
        else:
            why = "не выдано"
    new_pos = old_pos = None
    if case.get("cat") == "latest":
        new_pos, old_pos = first_pos(must, packed.body), first_pos(case.get("stale") or [], packed.body)
    q = Q(n, str(case.get("cat") or "?"), not missing, result.sem_used, result.status.value,
          why, new_pos, old_pos)
    return q, packed, result


def synth(profile: brain.Profile, llm, question: str, packed: brain.Packed,
          timings: Timings) -> tuple[str, bool]:
    """Синтез моделью профиля. Хвост живой стенограммы пуст: бенч мерит долю
    памяти в ответе. Температура 0 — регрессия, не творчество (у демона — из
    конфига). -> (ответ, ушло ли облако на локальный запас)."""
    prompt = (brain.answer_prompt(question, packed.text, "") if profile.name == "answer"
              else brain.expand_prompt(question, packed.text))
    llm._fell_back_local = False
    t0 = time.perf_counter()
    first = None
    parts: list[str] = []
    for tok in llm.stream(prompt, temperature=0.0, **brain.stream_kwargs(profile, llm)):
        if first is None:
            first = time.perf_counter() - t0
        parts.append(tok)
    total = time.perf_counter() - t0
    timings.add("синтез: первый токен", total if first is None else first)
    timings.add("синтез целиком", total)
    return "".join(parts), bool(llm._fell_back_local)


def describe(profile: brain.Profile) -> str:
    return (f"поиск {profile.limit}×{profile.snippet_chars}, таймаут {profile.timeout} с "
            f"(вектор запроса до {max(0.5, min(6.0, profile.timeout / 2))} с), бюджет {profile.budget}"
            + ("" if profile.dossiers else ", без досье"))


def run_profile(profile: brain.Profile, graph: pathlib.Path, emb, cases: list[dict], *,
                stats: bool, cfg: dict) -> tuple[list[Q], str, str]:
    """Профиль потребителя демона: выдача без модели (`--stats`, и всегда у `live`)
    или с синтезом моделью профиля. -> (итоги по вопросам, фактическая модель или '',
    отпечаток графа)."""
    print(f"профиль {profile.name}: {describe(profile)}")
    if profile.name == "live":
        print("  фрагменты без узлов (nodes=none) — верхняя оценка для не-CONFIDENT: в продукте "
              f"узлам графа достаётся до {int(brain.NODES_SHARE * 100)} % бюджета (№634)")
    do_synth = not stats and profile.synth is not None
    llm = model = None
    if do_synth:
        if os.environ.pop("CHAROITE_ONE_MODEL", None):
            print("  CHAROITE_ONE_MODEL снят для бенча: демон днём идёт на своей малой модели")
        llm = LLM(cfg)
        model = llm.effective_model(brain.stream_kwargs(profile, llm)["model"])
        print(f"  синтез: модель {model} (роль {profile.synth.role}), хвост стенограммы пуст — "
              "доля памяти в ответе")
    graph_fp = warm_profile(profile, graph, emb)
    timings = Timings()
    out: list[Q] = []
    fallback = 0
    for i, case in enumerate(cases, 1):
        try:
            q, packed, result = retrieve(profile, case, i, graph, emb, timings)
        except (brain.MemoryNotReady, brain.MemoryUnavailable) as exc:
            sys.exit(f"[{i}/{len(cases)}] память по графу отпала посреди прогона ({exc}) — итог не сравним")
        mark = "✓" if q.ok else "✗"
        line = f"[{i}/{len(cases)}] {mark} {q.cat} {q.status} sem={'да' if q.sem_used else 'нет'}"
        if q.cat == "latest":
            line += f" новое@{q.new_pos} старое@{q.old_pos}"
        if q.why:
            line += f" — {q.why}"
        if do_synth:
            answer, fell = synth(profile, llm, case["q"], packed, timings)
            missing = [m for m in case.get("must") or [] if not contains(m, answer)]
            stage = "" if not missing else ("ПОИСК" if not q.ok else "синтез")
            q = q._replace(ok=not missing, why=q.why or ("синтез" if missing else ""),
                           comparable=not fell)
            fallback += fell
            line += f" | синтез {'✓' if not missing else '✗ (этап: ' + stage + ')'}"
            if fell:
                line += " (облако ушло на локальный запас — вне сравнения)"
        print(line)
        out.append(q)
    total = len(out)
    passed = sum(q.ok for q in out if q.comparable)
    print(f"\nИТОГ {profile.name}{'' if do_synth else ' (выдача без модели)'}: {passed}/{total - fallback}")
    if profile.name == "expand" and do_synth:
        sure = [q for q in out if q.status == brain.Verdict.CONFIDENT.value]
        rest = [q for q in out if q.status != brain.Verdict.CONFIDENT.value]
        print(f"  CONFIDENT (в продукте путь модели): {sum(q.ok for q in sure)}/{len(sure)}")
        print(f"  прочие (в продукте сначала узлы графа; синтез справочно): "
              f"{sum(q.ok for q in rest)}/{len(rest)}")
    print("задержки по этапам:")
    for ln in timings.lines():
        print(ln)
    if fallback:
        model = "fallback:?"
    return out, model or "", graph_fp


def run_companion(graph: pathlib.Path, cases: list[dict], demo: bool) -> list[Q]:
    """Сервер-компаньон чата приложения — справочно, вне ночи.

    Есть только у владельца и только пока жив сервер :8100; у пользователей чат
    идёт в поиск на Swift (его мерит P2 плана памяти). Один запрос с потолком
    25 с: дольше порога чата (4 с) вопрос вне сравнения — в продукте ответ дал бы
    Swift-поиск. Упаковка на стороне сервера, бюджета у бенча нет."""
    require_brain(graph, demo, "--profile companion")
    print(f"профиль companion (engine=companion): {COMPANION_LIMIT}×{COMPANION_SNIPPET}, "
          f"порог чата {COMPANION_DEADLINE_S:.0f} с; у пользователей чат идёт в Swift-поиск, его мерит P2")
    timings = Timings()
    out: list[Q] = []
    for i, case in enumerate(cases, 1):
        hit = search_brain(graph, case["q"], limit=COMPANION_LIMIT, snippet=COMPANION_SNIPPET, timeout=25)
        if hit.outcome != BRAIN_ALIVE:
            sys.exit(f"--profile companion: сервер памяти отпал посреди прогона ({hit.outcome}) — итог не сравним")
        timings.add("поиск", hit.elapsed)
        must = case.get("must") or []
        ok = all(contains(m, hit.text) for m in must)
        comparable = hit.elapsed <= COMPANION_DEADLINE_S
        q = Q(i, str(case.get("cat") or "?"), ok, None, "weak" if hit.low_conf else "ok",
              "" if ok else ("поиск пуст" if not hit.text else "не выдано"), comparable=comparable)
        note = "" if comparable else " — дольше порога: в продукте ушёл бы в Swift-поиск, вне сравнения"
        print(f"[{i}/{len(cases)}] {'✓' if ok else '✗'} {q.cat} {'⚠' if hit.low_conf else ''}"
              f" {hit.elapsed:.2f} с{note}")
        out.append(q)
    comp = [q for q in out if q.comparable]
    print(f"\nИТОГ companion: {sum(q.ok for q in comp)}/{len(comp)}, вне сравнения {len(out) - len(comp)}")
    print("задержки по этапам:")
    for ln in timings.lines():
        print(ln)
    return out


def raw_stats(graph: pathlib.Path, cfg: dict, demo: bool, cases: list[dict]) -> None:
    """Калибровка гейта честности (круги 1–2 по #577) в режиме `raw`: распределение
    сигналов на своих вопросах, без модели. Пороги — в graph_search.py."""
    from charoite_graph import graph_search
    # пустой конфиг фабрика читает как «моделей нет» и отдаёт пустой
    # векторизатор: демо меряет лексику, не ходя в сеть
    mem = graphs.open_search(graph, build_embedder(cfg if not demo else {}))
    mem.refresh(force=True)
    mem.load_vectors()
    print(f"файлов {mem.size}, с векторами {mem.vectors}; пороги sim<{graph_search.LOW_SIM} и cov<{graph_search.LOW_COV}")
    v = mem.vote_stats()
    print(f"роли: первичных {v['primary']}, досье {v['dossier']}, служебных вне индекса {v['service']}; "
          f"голосов {v['votes']} за {v['targets']} целей, на потолке хаба {v['at_cap']}; "
          f"не голосуют (досье): {v['dossier_votes']} голосов, которые сняли бы потолок ещё у {v['cap_if_dossier_voted'] - v['at_cap']}")
    for i, case in enumerate(cases, 1):
        r = mem.search(case["q"], limit=LIMIT_FILES, snippet_chars=SNIPPET)
        hit = "; ".join(b.split("\n")[0][2:] for b in r.blocks[:3])
        print(f"[{i}/{len(cases)}] {'⚠' if r.low_conf else '✓'} {r.status.value} sem={'да' if r.sem_used else 'нет'} {case['q']} → {hit}")


def run_raw(args, graph: pathlib.Path, cfg: dict, cases: list[dict], lang: str) -> None:
    """Режим `raw` — как было до профилей: 5 × 1 200, свой SYNTH, флаги --brain и --legacy."""
    llm = LLM(cfg)
    passed, failures = 0, []
    # по умолчанию — память демона (src/charoite_graph/graph_search.py): то, что видит владелец
    # на встрече; сервер и прежний фолбэк — только по флагам, для сравнения
    print("контур поиска: " + ("сервер памяти :8100" if args.brain else
                               "прежний локальный фолбэк" if args.legacy else "память демона (graph_search)"))
    for i, case in enumerate(cases, 1):
        q, must = case["q"], case.get("must", [])
        if args.brain:
            hit = search_brain(graph, q)
            if hit.outcome != BRAIN_ALIVE:
                sys.exit(f"--brain: сервер памяти отпал посреди прогона ({hit.outcome}) — итог не сравним")
            found = hit.text
        else:
            found = search_legacy(graph, q) if args.legacy else search(graph, q, cfg if not args.demo else None)
        if not found:
            failures.append((q, must, "поиск ничего не нашёл"))
            print(f"[{i}/{len(cases)}] ✗ {q} — поиск пуст")
            continue
        # диагностика: чей провал — ПОИСКА (факт не в выдаче) или СИНТЕЗА
        # (факт в выдаче, LLM не включил в ответ). Лечатся по-разному.
        retr_missing = [m for m in must if not contains(m, found)]
        prompt_tpl, system_msg = SYNTH[lang]
        answer = "".join(llm.stream(
            prompt_tpl.format(q=q, found=found),
            system=system_msg,
            temperature=0.0,  # бенч — регрессия, не творчество: убираем флап
        ))
        missing = [m for m in must if not contains(m, answer)]
        if missing:
            stage = "ПОИСК" if retr_missing else "синтез"
            failures.append((q, missing, f"[{stage}] " + answer[:160]))
            print(f"[{i}/{len(cases)}] ✗ {q} — нет: {missing} (этап: {stage})")
        else:
            passed += 1
            note = f" (в выдаче не было: {retr_missing})" if retr_missing else ""
            print(f"[{i}/{len(cases)}] ✓ {q}{note}")

    total = len(cases)
    print(f"\nИТОГ: {passed}/{total}")
    for q, missing, ctx in failures:
        print(f"  ✗ «{q}»: не найдено {missing}\n    ответ: {ctx}…")
    if total and passed < total * 2 / 3:
        sys.exit(1)   # деградация больше трети — сигнал при ручном прогоне


# --------------------------------------------------------------------- база и тревога ночи
# Итог профиля дописывается строкой в logs/memory_bench_baseline.jsonl; принятая
# база — отдельная строка `accept` со ссылкой на итог (принимается явной командой,
# по id итога). Тревога — два и больше вопросов ✓→✗ против принятой базы
# того же профиля и режима (у синтеза — и модели), только среди вопросов с
# тем же sem_used: сменившийся векторизатор — другой опыт, не регресс поиска.

ALERT_MIN = 2           # один вопрос — шум (сдвиг меньше 2–3 вопросов на 37)


def code_head() -> str:
    """Версия кода бенча: HEAD, с «+dirty» при правках отслеживаемых файлов (иначе
    правка без коммита читалась бы как «HEAD не менялся»); не git-клон — «?»."""
    import subprocess

    def git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, timeout=10,
                                  cwd=code_root(__file__))
        except OSError:
            return None
    out = git("rev-parse", "--short=12", "HEAD")
    if out is None or out.returncode != 0 or not out.stdout.strip():
        return "?"
    dirty = git("status", "--porcelain", "--untracked-files=no")
    return out.stdout.strip() + ("+dirty" if dirty is None or dirty.stdout.strip() else "")


def head_changed(base: dict, rec: dict) -> bool | None:
    """None — судить не по чему: у одной из записей версия кода неизвестна."""
    a, b = base.get("head", "?"), rec.get("head", "?")
    return None if "?" in (a, b) else a != b


def qid(case: dict) -> str:
    """Ключ вопроса в записи — хеш вопроса вместе с эталоном, не сам текст: вопрос
    или ожидаемые факты могли смениться под тем же номером, такой вопрос не сравнивается."""
    import hashlib
    key = json.dumps([case.get("q", ""), case.get("must") or [], case.get("stale") or []],
                     ensure_ascii=False, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def make_record(profile: str, mode: str, model: str, graph_fp: str, cases: list[dict],
                out: list[Q], graph_dir: str = "", dossiers: bool = True) -> dict:
    import uuid
    # id — ссылка принятия на итог: время с точностью до секунды у двух прогонов совпадает
    return {"kind": "run", "id": uuid.uuid4().hex[:12], "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "profile": profile, "mode": mode, "head": code_head(),
            "model": model if mode == "synth" else None, "graph": graph_fp, "graph_dir": graph_dir,
            "dossiers": dossiers,
            "questions": [{"id": qid(c), "n": q.n, "cat": q.cat, "ok": q.ok, "sem": q.sem_used,
                           "status": q.status, "why": q.why, "new": q.new_pos, "old": q.old_pos,
                           "cmp": q.comparable} for c, q in zip(cases, out)]}


# Оси, вошедшие в ключ после первой версии журнала. Запись без поля читается
# значением по умолчанию. В ключ ось входит только отклонением от умолчания,
# поэтому ключи старых записей, принятых баз и тревог не меняются.
ADDED_AXES = {"dossiers": True}


def axis(rec: dict, name: str) -> bool:
    """Значение поздней оси записи: поле, а без поля — умолчание из `ADDED_AXES`."""
    return rec.get(name, ADDED_AXES[name])


def run_key(rec: dict) -> tuple:
    """Что обязано совпасть, чтобы сравнение имело смысл; им же ключуется тревога.
    Поздние оси входят в ключ только отклонением от умолчания, поэтому строки ключей
    записей до их появления не меняются."""
    head = (rec.get("profile"), rec.get("mode"),
            rec.get("model") if rec.get("mode") == "synth" else None, rec.get("graph_dir", ""))
    return head + tuple(f"{name}={axis(rec, name)}" for name in ADDED_AXES
                        if axis(rec, name) != ADDED_AXES[name])


def alert_key(rec: dict) -> str:
    return "|".join("" if x is None else str(x) for x in run_key(rec))


@contextlib.contextmanager
def bench_lock(root: pathlib.Path):
    """Журнал базы и файл тревоги — разделяемое состояние: ночной прогон и ручной
    `--record`/`--accept` читают и пишут их под одним замком."""
    path = log_path(root, "memory_bench_baseline").with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        if not file_locks.acquire_exclusive(f, attempts=50, pause=0.1):
            sys.exit("журнал бенча памяти занят другим прогоном дольше 5 с — запись не сделана")
        yield


def read_records(path: pathlib.Path) -> list[dict]:
    """Строки журнала; битая строка (оборванная запись) пропускается с предупреждением."""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            print(f"⚠ {path.name}:{i}: битая строка пропущена")
    return out


def append_record(path: pathlib.Path, rec: dict) -> None:
    """Оборванная прошлая запись (без перевода строки) не склеивается с новой:
    иначе `read_records` выбросил бы обе."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lead = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            lead = "" if f.read(1) == b"\n" else "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(lead + json.dumps(rec, ensure_ascii=False) + "\n")


def accepted_base(records: list[dict], key: tuple) -> dict | None:
    """Итог, на который указывает последнее принятие с этим ключом."""
    runs = {r.get("id"): r for r in records if r.get("kind") == "run" and run_key(r) == key}
    for r in reversed(records):
        if r.get("kind") == "accept" and run_key(r) == key and r.get("run") in runs:
            return runs[r["run"]]
    return None


def passed(rec: dict) -> int:
    return sum(1 for q in rec["questions"] if q["ok"] and q.get("cmp", True))


class Diff(NamedTuple):
    regressed: list[dict]   # ✓→✗ при том же sem_used, оба сравнимы
    sem_diff: int           # sem_used ≠ базы
    total: int              # вопросов, сопоставленных с базой по ключу


def compare(base: dict, cur: dict) -> Diff:
    prev = {q["id"]: q for q in base["questions"]}
    regressed, sem_diff, total = [], 0, 0
    for q in cur["questions"]:
        b = prev.get(q["id"])
        if b is None:
            continue
        total += 1
        if b.get("sem") != q.get("sem"):
            sem_diff += 1
            continue
        if b["ok"] and not q["ok"] and b.get("cmp", True) and q.get("cmp", True):
            regressed.append(q)
    return Diff(regressed, sem_diff, total)


def update_alert(path: pathlib.Path, key: str, entry: dict | None) -> None:
    """Файл тревоги для утреннего брифа: ключ — ключ сравнения (`alert_key`).
    `None` — сравнили, просадки нет: ключ снят, пустой файл удаляется. Состояние
    `unmeasured` (базы нет, сравнимых вопросов мало, грязный прогон против базы на
    другом коде) стоявшую тревогу не снимает, а помечает: прогон, который ничего не
    сравнил, не свидетельствует «чисто». `run` и `ts` тревоги остаются от итога, что её
    поднял: `ts` — время последнего замера, бриф считает от него возраст тревоги.
    Ключи старого формата (до №631 третьим полем шёл слот seed: "0", "" или "random")
    подтягиваются к новому: состояние берётся оттуда, если под новым ключом его нет, а
    старый ключ удаляется при любой записи — иначе он висел бы в брифе вечно.
    Вызывать под `bench_lock`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    parts = key.split("|", 2)
    legacy = [f"{parts[0]}|{parts[1]}|{seed}|{parts[2]}" for seed in ("0", "", "random")] \
        if len(parts) == 3 else []
    prev = data.get(key)
    for old in legacy:
        old_entry = data.pop(old, None)
        if prev is None and old_entry is not None:
            prev = old_entry
    if prev is not None:
        data[key] = prev
    if entry is None:
        data.pop(key, None)
    elif entry["state"] == "unmeasured" and isinstance(prev, dict) and prev.get("state") == "alert":
        data[key] = {**prev, "unmeasured": entry["why"]}
    else:
        data[key] = entry
    if data:
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=1))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    elif path.exists():
        path.unlink()


def judge(root: pathlib.Path, rec: dict, graph: pathlib.Path | None = None) -> dict | None:
    """Запись итога и сверка с принятой базой. `graph` — для утреннего брифа: тревога
    видна в брифе того графа, по которому мерили. Три исхода: тревога, чисто (ключ
    снят), не измерено (базы нет или сравнимых вопросов меньше половины). -> тревога или None."""
    with bench_lock(root):
        return _judge(root, rec, graph)


def _judge(root: pathlib.Path, rec: dict, graph: pathlib.Path | None) -> dict | None:
    base_path, alert_path = log_path(root, "memory_bench_baseline"), log_path(root, "memory_bench_alert")
    records = read_records(base_path)
    append_record(base_path, rec)
    base = accepted_base(records, run_key(rec))
    stamp = {"ts": rec["ts"], "run": rec["id"], "profile": rec["profile"], "mode": rec["mode"],
             "dossiers": axis(rec, "dossiers"),
             "graph_dir": str(graph) if graph is not None else ""}
    print(f"итог {rec['id']} записан")
    hint = (f"--accept --run {rec['id']} --profile {rec['profile']}"
            f"{' --stats' if rec['mode'] == 'stats' else ''}"
            f"{' --no-dossiers' if axis(rec, 'dossiers') is False else ''}")
    if base is None:
        print(f"база не принята ({rec['profile']}, {rec['mode']}): примите итог командой {hint}")
        update_alert(alert_path, alert_key(rec),
                     {**stamp, "state": "unmeasured", "why": f"база не принята ({hint})"})
        return None
    # правка без коммита против базы на другом коде: поднять тревогу такой прогон может —
    # измерено то, что работает на этой машине; снять — нет: код, который пойдёт в
    # работу, им не проверен
    dirty_other = rec["head"].endswith("+dirty") and rec["head"] != base.get("head")
    d = compare(base, rec)
    print(f"против базы от {base['ts']}: было {passed(base)}, стало {passed(rec)}; ✓→✗ {len(d.regressed)}")
    if d.sem_diff:
        print(f"⚠ sem_used ≠ базы на {d.sem_diff} из {d.total} — эти вопросы не сравниваются "
              "(векторизатор был в одном прогоне и не был в другом)")
    comparable = d.total - d.sem_diff
    alert = None
    if len(d.regressed) >= ALERT_MIN:
        h_changed = head_changed(base, rec)
        graph_changed = base.get("graph") != rec["graph"]
        alert = {**stamp, "state": "alert", "base_ts": base["ts"], "was": passed(base), "now": passed(rec),
                 "regressed": [{"n": q["n"], "cat": q["cat"], "why": q["why"]} for q in d.regressed],
                 "head_changed": h_changed, "graph_changed": graph_changed, "sem_diff": d.sem_diff,
                 "dirty": dirty_other}
        print(f"ТРЕВОГА: {len(d.regressed)} вопросов ✓→✗ при том же sem_used"
              + ("; прогон на незакоммиченном коде" if dirty_other else ""))
        for q in d.regressed:
            print(f"  №{q['n']} {q['cat']}" + (f" — {q['why']}" if q["why"] else ""))
        print(f"HEAD изменился: {yes_no(h_changed)}; граф изменился: {yes_no(graph_changed)}")
        for q in d.regressed:
            if q["cat"] == "latest":
                print(f"  latest №{q['n']}: {q['why'] or 'не выдано'} (новое@{q['new']}, старое@{q['old']})")
        update_alert(alert_path, alert_key(rec), alert)
    elif comparable < max(ALERT_MIN, d.total // 2 + d.total % 2):
        why = f"сравнимо {comparable} из {d.total} вопросов (sem_used ≠ базы)"
        print(f"не измерено: {why} — тревога не снимается")
        update_alert(alert_path, alert_key(rec), {**stamp, "state": "unmeasured", "why": why})
    elif dirty_other:
        print(f"просадки нет, но прогон на незакоммиченном коде против базы на другом коде "
              f"({rec['head']} против {base.get('head', '?')}) — тревога не снимается; "
              f"взвести сторож на этом коде — {hint}")
        update_alert(alert_path, alert_key(rec),
                     {**stamp, "state": "unmeasured",
                      "why": f"прогон на незакоммиченном коде против базы на другом коде ({hint})"})
    else:
        update_alert(alert_path, alert_key(rec), None)
    return alert


def yes_no(v: bool | None) -> str:
    return "неизвестно" if v is None else "да" if v else "нет"


def accept(root: pathlib.Path, profile: str, mode: str, run_id: str, reason: str,
           graph_dir: str = "", dossiers: bool = True) -> None:
    """Принять базой итог `run_id` — тот, что человек видел (`judge` печатает id), а не
    последний в журнале. Итог хуже прежней базы — только с причиной (`--reason`): молча
    опущенная планка — не база. Итог, прошедший через откат модели (`fallback:?`),
    базой не годится: модель неизвестна. Тревога ключа снимается: следующий прогон
    судит против новой базы, а ключ, который больше не гоняют, не висит вечно."""
    with bench_lock(root):
        _accept(root, profile, mode, run_id, reason, graph_dir, dossiers)


def _accept(root: pathlib.Path, profile: str, mode: str, run_id: str, reason: str, graph_dir: str,
            dossiers: bool = True) -> None:
    path = log_path(root, "memory_bench_baseline")
    records = read_records(path)
    rec = next((r for r in records if r.get("kind") == "run" and r.get("id") == run_id), None)
    if rec is None:
        sys.exit(f"итога {run_id} в журнале нет — id печатает прогон с --record")
    # значение пути графа в текст не идёт: только имя поля
    for field, want in (("profile", profile), ("mode", mode), ("graph_dir", graph_dir)):
        if rec.get(field, "") != want:
            sys.exit(f"итог {run_id} не того ключа: поле {field} не совпадает с командой — не принят")
    if axis(rec, "dossiers") != dossiers:
        sys.exit(f"итог {run_id} не того ключа: поле dossiers не совпадает с командой — не принят")
    if str(rec.get("model") or "").startswith("fallback:"):
        sys.exit(f"итог {run_id} прошёл через откат модели (fallback:?) — базой не годится, повторите прогон")
    prev = accepted_base(records, run_key(rec))
    if prev is not None and passed(rec) < passed(prev) and not reason:
        sys.exit(f"итог {passed(rec)} хуже принятой базы {passed(prev)} — принять можно только с --reason")
    append_record(path, {"kind": "accept", "ts": dt.datetime.now().isoformat(timespec="seconds"),
                         "run": rec["id"], "profile": profile, "mode": mode,
                         "model": rec.get("model"), "graph_dir": graph_dir, "reason": reason,
                         "dossiers": dossiers})
    print(f"база {profile}/{mode} принята: итог {rec['id']} от {rec['ts']}, {passed(rec)}/{len(rec['questions'])}"
          + (f" — причина: {reason}" if reason else ""))
    update_alert(log_path(root, "memory_bench_alert"), alert_key(rec), None)
    print("тревога ключа снята, следующий прогон судит против новой базы")


def main() -> None:
    harden_umask()   # кэш векторов памяти хранит блоки текста графа — только владельцу (№385)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="только первые N вопросов")
    ap.add_argument("--profile", choices=PROFILE_CHOICES, default="raw",
                    help="чей путь мерить: answer / live / expand — потребители демона через профиль "
                         "brain.py; companion — сервер-компаньон чата (справочно); raw — прежний контур")
    ap.add_argument("--demo", action="store_true",
                    help="демо-граф из репозитория вместо вашего: проверка контура без встреч")
    ap.add_argument("--demo-en", action="store_true",
                    help="английский демо-граф (demo/graph_en) и английские кейсы")
    ap.add_argument("--demo-zh", action="store_true",
                    help="китайский демо-граф (demo/graph_zh) и китайские кейсы")
    ap.add_argument("--brain", action="store_true",
                    help="raw: искать через сервер памяти :8100 (сравнение с прежним контуром)")
    ap.add_argument("--legacy", action="store_true",
                    help="raw: прежний локальный фолбэк вместо src/charoite_graph/graph_search.py (сравнение до/после)")
    ap.add_argument("--stats", action="store_true",
                    help="без синтеза: с --profile answer|live|expand — выдача профиля, ночной сигнал; "
                         "raw — покрытие, косинус и вердикт гейта для калибровки порогов")
    ap.add_argument("--no-dossiers", action="store_true",
                    help="с --profile answer|live|expand: профиль без оси досье — сводки тем выключены "
                         "(замер вклада досье; поле входит в ключ сравнения и тревоги). "
                         "У answer досье выключены самим профилем: на прогон answer флаг не влияет, "
                         "но при --accept его итога обязателен — подсказка его печатает")
    ap.add_argument("--record", action="store_true",
                    help="с --profile answer|live|expand: дописать итог в logs/memory_bench_baseline.jsonl "
                         "и сверить с принятой базой (тревога — logs/memory_bench_alert.json)")
    ap.add_argument("--accept", action="store_true",
                    help="с --run ID: принять итог базой (без прогона); id печатает прогон с --record")
    ap.add_argument("--run", default="", metavar="ID", help="с --accept: id итога, который принимается")
    ap.add_argument("--reason", default="", help="с --accept: причина, если итог хуже прежней базы")
    args = ap.parse_args()
    if (args.brain or args.legacy) and args.profile != "raw":
        ap.error("--brain и --legacy — флаги режима raw")
    if args.no_dossiers and args.profile not in brain.PROFILES:
        ap.error("--no-dossiers — только с профилями демона: answer, live, expand "
                 "(у raw и companion оси досье нет)")
    if (args.record or args.accept) and args.profile not in brain.PROFILES:
        ap.error("--record и --accept — для профилей демона: answer, live, expand")
    if args.record and (args.limit or args.demo or args.demo_en or args.demo_zh):
        ap.error("--record пишет базу по полному эталону владельца: без --limit и демо")
    if args.reason and not args.accept:
        ap.error("--reason — только вместе с --accept")
    if args.accept != bool(args.run):
        ap.error("--accept и --run ID — только вместе: принимается итог, который вы видели")
    mode = "stats" if args.stats or args.profile == "live" else "synth"

    cfg_path = _root() / "config" / "config.yaml"
    if not cfg_path.exists() and (args.demo or args.demo_en or args.demo_zh):
        # демо-режим работает и до настройки: дефолтная модель Ollama
        cfg = {"llm": {"base_url": "http://127.0.0.1:11434", "model": "qwen3.5:4b"},
               "sufler": {"role": "Ассистент по архиву встреч."}}
    elif not cfg_path.exists():
        # Свежий клон репозитория: config.yaml личный и в git не лежит.
        # Раньше здесь падал FileNotFoundError, и ночная джоба показывала
        # трейсбек вместо «не настроено». Не настроено — не поломка.
        print(f"Чароит не настроен: нет {cfg_path.name}. "
              f"Скопируйте config.example.yaml и заполните свои значения, "
              f"либо запустите бенч на демо-графе: --demo")
        return
    else:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    lang = resolve_lang(cfg, demo_zh=args.demo_zh, demo_en=args.demo_en, demo=args.demo)
    if args.demo_zh:
        args.demo = True
        graph = _root() / "demo" / "graph_zh"
        bench_file = _root() / "config" / "memory_bench_demo_zh.yaml"
    elif args.demo_en:
        args.demo = True
        graph = _root() / "demo" / "graph_en"
        bench_file = _root() / "config" / "memory_bench_demo_en.yaml"
    elif args.demo:
        graph = _root() / "demo" / "graph"
        bench_file = _root() / "config" / "memory_bench_demo.yaml"
    else:
        graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
        bench_file = _root() / "config" / "memory_bench.yaml"  # см. memory_bench.example.yaml
    if args.accept:
        accept(_root(), args.profile, mode, args.run, args.reason, str(graph), not args.no_dossiers)
        return
    if not bench_file.exists():
        # Не настроен — не то же самое, что провален. Раньше здесь был выход с
        # ошибкой, и ночная джоба каждую ночь печатала «БЕНЧ ПАМЯТИ ПРОСЕЛ» у
        # всех, кто бенч не заводил. Вечно горящее предупреждение приучает не
        # смотреть на предупреждения — той же болезнью болел CI до аудита.
        print(f"бенч памяти не настроен: нет {bench_file.name}. "
              f"Чтобы включить — скопируйте {bench_file.with_name('memory_bench.example.yaml').name} "
              f"и впишите свои вопросы")
        return
    cases = yaml.safe_load(bench_file.read_text(encoding="utf-8")) or []
    if args.limit:
        cases = cases[:args.limit]

    if args.profile == "companion":
        run_companion(graph, cases, args.demo)
        return
    if args.profile != "raw":
        # пустой конфиг фабрика читает как «моделей нет»: демо меряет лексику, не ходя в сеть
        emb = build_embedder(cfg if not args.demo else {})
        profile = brain.PROFILES[args.profile]
        if args.no_dossiers:
            profile = profile._replace(dossiers=False)
        out, model, graph_fp = run_profile(profile, graph, emb, cases,
                                           stats=args.stats, cfg=cfg)
        if args.record:
            judge(_root(), make_record(args.profile, mode, model, graph_fp, cases, out, str(graph),
                                       dossiers=profile.dossiers), graph)
        return
    if args.brain:
        require_brain(graph, args.demo)     # до любой ветки: --brain --stats тоже не должен мерить молча другой контур
    if args.stats:
        raw_stats(graph, cfg, args.demo, cases)
        return
    run_raw(args, graph, cfg, cases, lang)


if __name__ == "__main__":
    main()
