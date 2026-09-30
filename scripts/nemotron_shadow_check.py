#!/usr/bin/env python3
"""Сверка журнала тени потокового Nemotron с финальной разметкой встречи (№478 B, часть 1).

Тень (`src/live_nemotron.py`) пишет журнал `nemotron_live_<штамп>.jsonl`: сегменты потока,
фронт разметки и исход каждого чанка распознавания. Этот скрипт отвечает по нему на три
вопроса постановки PR B («распознанный кусок ждёт метку потока с потолком»):

(а) сколько ждёт метка — четыре величины, а не одна:
    (а1) задержка метки в секундах ЗВУКА: `start0 + fed` первой строки `front`, чей фронт
         прошёл конец чанка, минус конец чанка. Это почти константа модели (буфер пресета
         плюс шаг ребёнка) — диагностика, не потолок. Чанки, закрытые только финальным
         фронтом (после конца звука), считаются отдельно;
    (а2) стоимость шага ребёнка по СТЕНЕ — из свидетеля двери прогона (`--timing`): от
         момента, когда шаг и предыдущий фронт готовы, до его фронта;
    (а4) `wait_s` и `behind_s` строк `chunk` как есть — для журналов живых звонков
         (в прогоне записи стена фиктивна).
    Время распознавания (а3) в журнале тени не живёт — его берут из журнала демона.
(б) матрица «слот потока × финальный голос» в секундах, с «нет голоса» и «нет слота»;
    чистота слотов и покрытие голосов по всей встрече и по окнам; префиксное соответствие
    слот → голос — ОРАКУЛЬНАЯ верхняя граница (использует финал прошлых окон, которого
    вживую нет), рядом — дробление голоса по слотам.
(в) трекер ERes2Net (файл прогона `--tracker`) ↔ поток ↔ финал: слагаемые DER против
    финала готовым `diar_bench.der` и согласие трекера с потоком. Финал — тот же Nemotron
    офлайн, поэтому это «согласие с итоговой разметкой», а не качество.

Прежде любой цифры — годность журнала (`validity`): штатный конец, ребёнок вышел сам,
ровно один финальный фронт, нулевые счётчики сбоев, фронт покрыл весь поданный звук, и
единица кадра сходится с поданным звуком той же проверкой, что у ребёнка
(`diarize_nemotron._check_front`). Негодный журнал — отказ, а не цифры.

    .venv/bin/python scripts/nemotron_shadow_check.py <журнал.jsonl> --final final.json \\
        [--tracker tracker.jsonl] [--timing timing.jsonl] [--meta meta.json] [--json]

Звука, текста реплик и имён здесь нет: только числа, слоты и метки голосов.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import math
import pathlib
import sys
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import charoite_paths  # noqa: E402
import diarize_nemotron  # noqa: E402  — только чистая проверка кадра, mlx не трогается
import live_nemotron  # noqa: E402

#: Пороги доли «задержка метки больше X секунд».
THRESHOLDS_S = (1.0, 2.0, 3.0, 5.0)
#: Окно для чистоты по времени и префиксного соответствия, секунды.
WINDOW_S = 300.0
#: Голос финала короче этого в покрытие не входит: осколок, а не человек.
MIN_VOICE_S = 30.0
#: Доля голоса в слоте, с которой слот считается его «куском» (дробление).
FRAGMENT_SHARE = 0.10
#: Сколько первых фронтов — прогрев модели, вне стоимости шага.
WARMUP_FRONTS = 5
#: Счётчики строки `end`, которые у годного прогона равны нулю.
ZERO_COUNTS = ("killed_after_grace", "killed_at_close", "front_malformed", "seg_malformed",
               "message_unknown", "message_out_of_state", "nonjson", "callback_errors")
#: Метка «нет голоса / нет слота» в матрице.
NONE = "—"


class Refused(Exception):
    """Журнал негоден: цифр нет."""


@dataclasses.dataclass
class Journal:
    ready: dict
    start0: int
    fronts: list[dict]
    segs: list[dict]
    chunks: list[dict]
    end: dict
    sr: int
    mems: list[dict] = dataclasses.field(default_factory=list)

    @property
    def frame_s(self) -> float:
        return float(self.ready["frame_s"])

    @property
    def step(self) -> int:
        return int(self.ready["step"])


def read_journal(lines: typing.Iterable[str]) -> Journal:
    """Строки журнала → `Journal`; без `header`, `ready`, `start` или `end` — отказ."""
    header = ready = start = end = None
    fronts, segs, chunks, mems = [], [], [], []
    last_type = None
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        obj = json.loads(raw)
        kind = obj.get("type")
        if kind == "header":
            if header is not None:
                raise Refused("два заголовка в журнале: два прогона в одном файле")
            header = obj
        elif kind == "ready":
            ready = obj
        elif kind == "start":
            start = obj
        elif kind == "front":
            fronts.append(obj)
        elif kind == "seg":
            segs.append(obj)
        elif kind == "chunk":
            chunks.append(obj)
        elif kind == "mem":
            mems.append(obj)
        elif kind == "end":
            if end is not None:
                raise Refused("две строки end в журнале")
            end = obj
        # строки чанков и памяти пишутся и после `end` (чанк, принятый после конца потока;
        # проверка давления из нити читателя) — «журнал не закончен» они не значат
        last_type = kind if kind not in ("chunk", "mem") else last_type
    missing = [n for n, v in (("header", header), ("ready", ready), ("start", start), ("end", end))
               if v is None]
    if missing:
        raise Refused(f"в журнале нет строк: {', '.join(missing)}")
    if last_type != "end":
        raise Refused(f"после end идут строки {last_type!r}: журнал не закончен")
    return Journal(ready=ready, start0=int(start["start0"]), fronts=fronts, segs=segs,
                   chunks=chunks, end=end, sr=int(header["sr"]), mems=mems)


# ------------------------------------------------------------------ годность


def check_frames(j: Journal) -> None:
    """Единица кадра против поданного звука — той же функцией, что у ребёнка. Каждая
    строка `front`: фронт не впереди поданного; финальная — покрыла весь поданный звук."""
    for f in j.fronts:
        try:
            diarize_nemotron._check_front(int(f["frames"]), int(f["fed"]), j.frame_s,
                                          final=bool(f.get("final")))
        except RuntimeError as e:
            raise Refused(f"кадр не сходится со звуком: {e}") from None


#: Исходы, которых в годном прогоне записи нет: чанк не дождался метки (у живого звонка —
#: законны, там тень умирает от давления; сверка прогона их не прощает).
LOST_OUTCOMES = (live_nemotron.TIMEOUT, live_nemotron.LATE, live_nemotron.DEAD_STREAM)


def validity(j: Journal, *, fed_expected: int | None = None, noted_expected: int | None = None) -> list[str]:
    """Почему журнал негоден (пусто — годен). `fed_expected` — сколько сэмплов прогон
    подал тени; фронт обязан их покрыть (ранний стоп иначе проходил бы как годный).
    `noted_expected` — сколько чанков канала собеседников прогон отдал тени: у каждого
    своя строка, и ни один не потерян (не дождался, опоздал, застал смерть потока)."""
    problems = []
    if noted_expected is not None:
        if len(j.chunks) != noted_expected:
            problems.append(f"строк chunk {len(j.chunks)}, прогон отдал тени {noted_expected}")
        lost = {o: n for o, n in collections.Counter(c["outcome"] for c in j.chunks).items()
                if o in LOST_OUTCOMES}
        if lost:
            problems.append(f"потерянные чанки: {lost}")
    if j.end.get("exit") != "ok":
        problems.append(f"ребёнок вышел не сам: exit={j.end.get('exit')!r} ({j.end.get('exit_reason', '')})")
    finals = [f for f in j.fronts if f.get("final")]
    if len(finals) != 1:
        problems.append(f"финальных фронтов {len(finals)}, нужен ровно один")
    counts = j.end.get("counts") or {}
    bad = {k: v for k, v in counts.items()
           if v and (k in ZERO_COUNTS or k.startswith("fault_"))}
    if bad:
        problems.append(f"счётчики сбоев не нулевые: {bad}")
    if not any(c["outcome"] == live_nemotron.LABELED for c in j.chunks):
        problems.append("ни одного чанка с меткой")
    if finals and fed_expected is not None and int(finals[0]["fed"]) != fed_expected:
        problems.append(f"фронт покрыл {finals[0]['fed']} сэмплов из поданных {fed_expected}")
    try:
        check_frames(j)
    except Refused as e:
        problems.append(str(e))
    return problems


# ------------------------------------------------------------------ распределения


def quantiles(xs: typing.Sequence[float]) -> dict:
    """p50/p90/p95/max и число — по ближайшему рангу; пусто — только n=0."""
    if not xs:
        return {"n": 0}
    s = sorted(xs)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, max(0, math.ceil(p * len(s)) - 1))], 3)
    return {"n": len(s), "p50": q(0.5), "p90": q(0.9), "p95": q(0.95), "max": round(s[-1], 3)}


def share_over(xs: typing.Sequence[float], thresholds=THRESHOLDS_S) -> dict:
    """Доля значений строго больше порога — на каждый порог."""
    return {f">{t:g}s": (round(sum(1 for x in xs if x > t) / len(xs), 4) if xs else None)
            for t in thresholds}


def label_lag(j: Journal) -> dict:
    """(а1) задержка метки в секундах звука: `start0 + fed` первой нефинальной строки
    `front`, чей фронт ≥ конца чанка, минус конец чанка. Только чанки с меткой; до потока
    и закрытые лишь финальным фронтом — отдельным счётом (журнал живого звонка 30.09:
    тень умерла на пятой минуте, 632 чанка `dead` иначе числились бы «финальными»)."""
    fronts = [f for f in j.fronts if not f.get("final")]
    lags, final_only, before = [], 0, 0
    i = 0
    for c in sorted(j.chunks, key=lambda c: c["end"]):
        if c["start"] < j.start0:
            before += 1
            continue
        if c["outcome"] != live_nemotron.LABELED:
            continue                    # мёртвые, по потолку, поздние — в счёте исходов, не здесь
        while i < len(fronts) and int(fronts[i]["front"]) < c["end"]:
            i += 1
        if i == len(fronts):
            final_only += 1
            continue
        lags.append((j.start0 + int(fronts[i]["fed"]) - c["end"]) / j.sr)
    return {"lag_s": quantiles(lags), "share": share_over(lags), "final_only": final_only,
            "before_stream": before, "_values": lags}


def memory(j: Journal) -> dict:
    """Цена ребёнка по памяти и давление машины — независимо от годности журнала: тень,
    умершая от давления, и есть предмет этого замера. `phys_mb` — текущий след процесса
    (его судит macOS), `rss_mb` — пик RSS, справка: буферов MLX в нём нет."""
    def col(key: str) -> list[float]:
        return [float(f[key]) for f in j.fronts if isinstance(f.get(key), (int, float))]
    out: dict[str, typing.Any] = {k: quantiles(col(k)) for k in
                                  ("phys_mb", "mlx_active_mb", "mlx_cache_mb", "mlx_peak_mb")}
    rss = col("rss_mb")
    out["rss_peak_mb"] = max(rss) if rss else None
    out["cache_limit_mb"] = j.ready.get("cache_limit_mb")
    out["cache_limit_prev_mb"] = j.ready.get("cache_limit_prev_mb")
    out["pressure_checks"] = dict(collections.Counter(int(m["pressure"]) for m in j.mems if "pressure" in m))
    swap = [float(m["swap_used_mb"]) for m in j.mems if isinstance(m.get("swap_used_mb"), (int, float))]
    out["swap_used_mb"] = {"first": swap[0], "max": max(swap)} if swap else None
    out["lived_s"] = round(float(j.end.get("t", 0.0)), 1)
    out["end_reason"] = j.end.get("reason")
    return out


def outcomes(j: Journal) -> dict:
    return dict(collections.Counter(c["outcome"] for c in j.chunks))


def live_waits(j: Journal) -> dict:
    """(а4) `wait_s` и `behind_s` строк `chunk` с меткой — как их записала тень."""
    labeled = [c for c in j.chunks if c["outcome"] == live_nemotron.LABELED]
    waits = [float(c["wait_s"]) for c in labeled]
    behind = [float(c["behind_s"]) for c in labeled if c.get("behind_s") is not None]
    return {"wait_s": quantiles(waits), "wait_share": share_over(waits),
            "behind_s": quantiles(behind)}


def step_cost(timing: typing.Iterable[dict], step: int, sr: int) -> dict:
    """(а2) стоимость шага ребёнка по стене из свидетеля двери прогона.

    Строки: `{"k": "write", "t", "sent"}` — блок ушёл в трубу (сэмплов всего), и
    `{"k": "front", "t", "fed"}` — фронт пришёл. Шаг, закрытый фронтом с `fed`, мог
    начаться, когда (1) его звук ушёл в трубу и (2) пришёл предыдущий фронт; стоимость —
    от позднего из двух до фронта. Первые `WARMUP_FRONTS` — прогрев, в выборку не идут;
    не идут и фронты не на границе шага (хвост) и финальный (`final`: это `close()` модели
    по всей встрече, а не шаг, — даже когда звук кратен шагу). Фронт раньше своего звука —
    отказ."""
    writes: list[tuple[float, int]] = []
    fronts: list[tuple[float, int, bool]] = []
    for row in timing:
        if row["k"] == "write":
            writes.append((float(row["t"]), int(row["sent"])))
        else:
            fronts.append((float(row["t"]), int(row["fed"]), bool(row.get("final"))))
    costs = []
    wi = 0
    prev_t = None
    for n, (t, fed, final) in enumerate(fronts):
        while wi < len(writes) and writes[wi][1] < fed:
            wi += 1
        if wi == len(writes):
            raise Refused(f"фронт с fed={fed} раньше, чем звук ушёл в трубу")
        ready_t = writes[wi][0] if prev_t is None else max(writes[wi][0], prev_t)
        if n >= WARMUP_FRONTS and fed % step == 0 and not final:
            costs.append(t - ready_t)
        prev_t = t
    q = quantiles(costs)
    return {"step_cost_s": q, "step_s": step / sr,
            "rtf_p50": round(step / sr / q["p50"], 1) if costs and q["p50"] > 0 else None}


# ------------------------------------------------------------------ интервалы


Interval = tuple[float, float, str]


def stream_segments(j: Journal) -> list[Interval]:
    """Сегменты потока в секундах оси записи: `seg` не перекрываются и не переиздаются."""
    return [(s["start"] / j.sr, s["end"] / j.sr, f"slot{s['slot']}") for s in j.segs]


def overlap_matrix(a: list[Interval], b: list[Interval], *,
                   lo: float = -math.inf, hi: float = math.inf) -> dict[tuple[str, str], float]:
    """Секунды совместного звучания меток `a` (строки) и `b` (столбцы) на [lo, hi).

    Разбивка по всем границам; внутри куска — множества меток каждой стороны. Речь `a`
    без `b` — столбец `NONE`, речь `b` без `a` — строка `NONE`. Перекрытия внутри одной
    стороны делят кусок поровну между её метками, чтобы секунды не двоились."""
    events: dict[float, list[tuple[int, str, int]]] = collections.defaultdict(list)
    for side, xs in ((0, a), (1, b)):
        for s, e, lab in xs:
            if e > s:
                events[s].append((side, lab, 1))
                events[e].append((side, lab, -1))
    active: tuple[collections.Counter, collections.Counter] = (collections.Counter(), collections.Counter())
    out: dict[tuple[str, str], float] = collections.defaultdict(float)
    xs = sorted(events)
    for x0, x1 in zip(xs, xs[1:]):
        for side, lab, d in events[x0]:
            active[side][lab] += d
        x0, x1 = max(x0, lo), min(x1, hi)
        if x1 <= x0:
            continue
        la = sorted(k for k, n in active[0].items() if n > 0)
        lb = sorted(k for k, n in active[1].items() if n > 0)
        if not la and not lb:
            continue
        la, lb = la or [NONE], lb or [NONE]
        share = (x1 - x0) / (len(la) * len(lb))
        for p in la:
            for q in lb:
                out[(p, q)] += share
    return dict(out)


def rows(m: dict) -> dict[str, dict[str, float]]:
    r: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for (p, q), v in m.items():
        r[p][q] = r[p].get(q, 0.0) + v
    return dict(r)


def purity(m: dict) -> dict[str, float]:
    """Чистота строки: доля лучшего столбца-голоса в секундах строки (без «нет голоса»
    в числителе, с ним — в знаменателе)."""
    out = {}
    for p, cols in rows(m).items():
        if p == NONE:
            continue
        total = sum(cols.values())
        best = max((v for q, v in cols.items() if q != NONE), default=0.0)
        out[p] = round(best / total, 4) if total else None
    return out


def coverage(m: dict, *, min_s: float = MIN_VOICE_S) -> dict[str, float]:
    """Покрытие голоса: доля его секунд у лучшего слота; голоса короче `min_s` — вне."""
    cols: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for (p, q), v in m.items():
        cols[q][p] = cols[q].get(p, 0.0) + v
    out = {}
    for q, ps in cols.items():
        total = sum(ps.values())
        if q == NONE or total < min_s:
            continue
        out[q] = round(max((v for p, v in ps.items() if p != NONE), default=0.0) / total, 4)
    return out


def fragmentation(m: dict, *, min_s: float = MIN_VOICE_S, share: float = FRAGMENT_SHARE) -> dict[str, int]:
    """Во скольких слотах лежит заметная (≥ `share`) доля голоса."""
    cols: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for (p, q), v in m.items():
        cols[q][p] = cols[q].get(p, 0.0) + v
    return {q: sum(1 for p, v in ps.items() if p != NONE and v >= share * sum(ps.values()))
            for q, ps in cols.items() if q != NONE and sum(ps.values()) >= min_s}


def prefix_mapping(a: list[Interval], b: list[Interval], *, total: float,
                   window: float = WINDOW_S) -> dict:
    """Оракульная верхняя граница соответствия слот → голос: для окна k слот получает голос,
    с которым больше всего звучал на окнах до k; доля секунд окна k (у слотов с назначением),
    где слот звучал с назначенным голосом. Вживую финала прошлых окон нет — это потолок."""
    acc = collections.defaultdict(lambda: collections.defaultdict(float))
    hit = seen = 0.0
    k = 0
    while k * window < total:
        lo, hi = k * window, min(total, (k + 1) * window)
        m = overlap_matrix(a, b, lo=lo, hi=hi)
        mapping = {p: max(qs, key=qs.get) for p, qs in acc.items() if qs}
        for (p, q), v in m.items():
            if p == NONE or p not in mapping:
                continue
            seen += v
            hit += v if q == mapping[p] else 0.0
        for (p, q), v in m.items():
            if p != NONE and q != NONE:
                acc[p][q] += v
        k += 1
    return {"oracle_upper_bound": round(hit / seen, 4) if seen else None, "seconds": round(seen, 1)}


def windowed_purity(a: list[Interval], b: list[Interval], *, total: float,
                    window: float = WINDOW_S) -> dict:
    """Чистота слотов по окнам — взвешенная секундами слотов в окне."""
    vals = []
    k = 0
    while k * window < total:
        m = overlap_matrix(a, b, lo=k * window, hi=min(total, (k + 1) * window))
        for p, cols in rows(m).items():
            if p == NONE:
                continue
            tot = sum(cols.values())
            best = max((v for q, v in cols.items() if q != NONE), default=0.0)
            vals.append((best, tot))
        k += 1
    tot = sum(t for _, t in vals)
    return {"weighted_purity": round(sum(b for b, _ in vals) / tot, 4) if tot else None}


# ------------------------------------------------------------------ трекер


def tracker_intervals(lines: typing.Iterable[dict], sr: int) -> list[Interval]:
    """Решения трекера по чанкам канала собеседников → интервалы на оси записи.

    Перекрытие соседних чанков (0,5 с) — за более поздним: интервалы чанка обрезаются
    началом следующего. Канальная метка при сбое раскладки и исключённые куски — вне
    (голоса нет)."""
    chunks = sorted(lines, key=lambda c: c["start"])
    out: list[Interval] = []
    for i, c in enumerate(chunks):
        cut = chunks[i + 1]["start"] if i + 1 < len(chunks) else math.inf
        for s, e, voice in c.get("intervals", []):
            if voice is None:
                continue
            e = min(e, cut)
            if e > s:
                out.append((s / sr, e / sr, f"v{voice}"))
    return out


def der(truth: list[Interval], hyp: list[Interval], total: float) -> dict:
    """Слагаемые DER гипотезы против финала — функцией бенча (та же метрика, что у №473)."""
    import diar_bench
    as_dicts = lambda xs: [{"start": s, "end": e, "speaker": lab} for s, e, lab in xs]  # noqa: E731
    return {k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in diar_bench.der(as_dicts(truth), as_dicts(hyp), total).items()}


# ------------------------------------------------------------------ отчёт


def anonymous(segments) -> list[tuple[float, float, str]]:
    """Отрезки финала с метками `f0..fN` по порядку первого появления: сверка обезличивает
    на входе, что бы ей ни дали, — метка из стенограммы с именами в отчёт не уходит."""
    names: dict[str, str] = {}
    return [(float(s), float(e), names.setdefault(str(lab), f"f{len(names)}")) for s, e, lab in segments]


def report(j: Journal, final: dict, *, tracker: list[dict] | None = None,
           timing: list[dict] | None = None, meta: dict | None = None) -> dict:
    """Всё, что считает сверка, — одним словарём; негодный журнал — `Refused`."""
    fed_expected = (meta or {}).get("fed_to_shadow")
    noted = ((meta or {}).get("chunks") or {}).get("tracker_lines")
    problems = validity(j, fed_expected=fed_expected, noted_expected=noted)
    if problems:
        raise Refused("; ".join(problems))
    total = float(final["duration_s"])
    fin = anonymous(final["segments"])
    if not fin:
        raise Refused("в финале нет отрезков")
    stream = stream_segments(j)
    m = overlap_matrix(stream, fin)
    lag = label_lag(j)
    lag.pop("_values")
    out = {
        # без сводки прогона покрытие поданного звука не проверено — так и сказано (живой журнал)
        "unchecked": [name for name, v in (("fed_coverage", fed_expected), ("chunk_count", noted))
                      if v is None],
        "memory": memory(j),
        "outcomes": outcomes(j),
        "a1_label_lag_audio": lag,
        "a4_live_waits": live_waits(j),
        "b_slots": {
            "slots": sorted({lab for _, _, lab in stream}),
            "voices_final": len({lab for _, _, lab in fin}),
            "matrix_s": {f"{p}|{q}": round(v, 1) for (p, q), v in sorted(m.items())},
            "purity": purity(m),
            "coverage": coverage(m),
            "fragmentation": fragmentation(m),
            "windowed": windowed_purity(stream, fin, total=total),
            "prefix": prefix_mapping(stream, fin, total=total),
        },
        "c_agreement_with_final": {"stream": der(fin, stream, total)},
    }
    if timing is not None:
        out["a2_step_cost_wall"] = step_cost(timing, j.step, j.sr)
    if tracker is not None:
        trk = tracker_intervals(tracker, j.sr)
        out["c_agreement_with_final"]["tracker"] = der(fin, trk, total)
        tm = overlap_matrix(trk, stream)
        out["c_tracker_vs_stream"] = {"purity_tracker_voice": purity(tm),
                                      "der_tracker_vs_stream": der(stream, trk, total),
                                      "paths": dict(collections.Counter(c["path"] for c in tracker))}
    if meta:
        out["run"] = {k: meta[k] for k in ("wall_s", "audio_s", "chunks", "handshake_s") if k in meta}
    return out


def _jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(argv: list[str] | None = None) -> int:
    charoite_paths.harden_umask()      # сверка ничего не пишет, но точка входа закрывает маску, как все
    ap = argparse.ArgumentParser(description="Сверка журнала тени Nemotron с финальной разметкой (№478 B)")
    ap.add_argument("journal", type=pathlib.Path, help="журнал тени nemotron_live_<штамп>.jsonl")
    ap.add_argument("--final", type=pathlib.Path, required=True,
                    help="финальные голоса канала собеседников: {duration_s, segments: [[s, e, метка]]}")
    ap.add_argument("--tracker", type=pathlib.Path, help="решения трекера по чанкам (файл прогона)")
    ap.add_argument("--timing", type=pathlib.Path, help="свидетель двери прогона (write/front по стене)")
    ap.add_argument("--meta", type=pathlib.Path, help="сводка прогона (сколько звука подано тени)")
    args = ap.parse_args(argv)
    try:
        j = read_journal(args.journal.read_text(encoding="utf-8").splitlines())
    except json.JSONDecodeError as e:
        print(f"журнал негоден: строка не JSON ({e})", file=sys.stderr)
        return 2
    except Refused as e:
        print(f"журнал негоден: {e}", file=sys.stderr)
        return 2
    try:
        out = report(j, json.loads(args.final.read_text(encoding="utf-8")),
                     tracker=_jsonl(args.tracker) if args.tracker else None,
                     timing=_jsonl(args.timing) if args.timing else None,
                     meta=json.loads(args.meta.read_text(encoding="utf-8")) if args.meta else None)
    except (Refused, ValueError) as e:        # ValueError — финал без речи у DER, битые файлы
        # память — и у негодного журнала: смерть тени от давления и есть её предмет
        print(json.dumps({"refused": str(e), "memory": memory(j)}, ensure_ascii=False, indent=1))
        print(f"журнал негоден: {e}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
