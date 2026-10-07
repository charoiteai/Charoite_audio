#!/usr/bin/env python3
"""Бенч эмбеддингов: модели на трёх задачах Чароита и пороги под каждую.

Эмбеддинги в Чароите считают три контура, и у каждого свой порог, замеренный
на bge-m3: дежавю (отрыв лидера от медианы ядер, 0.04), ревизия ядер tier3
(косинус пары, 0.55) и поиск по графу (пол 0.35 и гейт честности 0.47).
Сменить модель — значит сменить разброс косинусов: те же числа у другой
модели значат другое. Бенч отвечает на два вопроса:

1. Какая модель лучше на задачах Чароита — метрики без порогов (top-1, MRR,
   AUC) на синтетическом наборе `config/embed_bench_demo.yaml` и, если граф
   настроен, на вашем графе со слабой разметкой из него самого.
2. Какие пороги поставить новой модели, чтобы контуры вели себя как на
   bge-m3: порог подбирается так, чтобы доля ложных срабатываний (дежавю на
   постороннем разговоре, ловушки поиска, шум ниже пола) и полнота дублей
   ревизии остались прежними. Итог — готовый кусок `config.yaml`.

Плюс скорость (мс на запрос дежавю, документов в секунду) и память модели
по `/api/ps` Ollama. Всё локально: тексты уходят только на адрес модели из
вашего конфига, через ту же политику адреса, что у демона.

    .venv/bin/python scripts/embed_bench.py                          # bge-m3 против embeddinggemma-2
    .venv/bin/python scripts/embed_bench.py --models bge-m3 embeddinggemma-2 --mrl 256
    .venv/bin/python scripts/embed_bench.py --no-graph --json /tmp/bench.json

Модели должны стоять в Ollama (`ollama pull bge-m3`, `ollama pull embeddinggemma-2`).
Машину под встречей бенч не грузит: занятая машина — отказ, `--force` — поверх.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import statistics
import sys
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import numpy as np  # noqa: E402
import requests  # noqa: E402
import yaml  # noqa: E402

import busy_signals  # noqa: E402
import exit_codes  # noqa: E402
import graphs  # noqa: E402
import llm  # noqa: E402
import privacy  # noqa: E402
import tier3  # noqa: E402
from charoite_graph.model_seam import (BGE_M3_THRESHOLDS, THRESHOLD_KEYS,  # noqa: E402
                                       SeamTransportError)
from charoite_graph.redirects import is_merged, stub_target  # noqa: E402
from charoite_paths import code_root, harden_umask, resolve_root  # noqa: E402

DEFAULT_MODELS = ("bge-m3:latest", "embeddinggemma-2")
#: Контур дежавю берёт последние 1500 знаков свежего разговора (daemon.deja_vu_loop).
DEJA_VU_WINDOW = 1500
#: Цели калибровки без эталона bge-m3 в прогоне: доли ложных срабатываний и
#: полнота дублей, которые считаем приемлемыми.
ABS_TARGETS = {"tier3_recall": 0.95, "trap_pass": 0.10, "floor_share": 0.10, "offtopic_trigger": 0.10}


# --------------------------------------------------------------- чистые функции
def unit(m: np.ndarray) -> np.ndarray:
    """Строки матрицы — единичной длины: косинус становится скалярным произведением."""
    m = np.asarray(m, dtype=np.float64)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def truncate(m: np.ndarray, dims: int) -> np.ndarray:
    """Матрёшка (MRL): первые `dims` координат и снова единичная длина."""
    return unit(np.asarray(m)[:, :dims])


def deja_vu_core_text(stem: str, text: str) -> str:
    """Текст ядра для дежавю — то же, что шлёт `daemon.warm_core_vectors`."""
    m = re.search(r"## Статус\n(.+)", text)
    st = re.sub(r"_\(.*?\)_", "", m.group(1)).strip() if m else ""
    return f"{stem}. {st}"[:400]


def margins(sims: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Для каждой строки косинусов «запрос × ядра»: лидер и отрыв от медианы.

    Медиана — как в `daemon.deja_vu_loop`: элемент `len // 2` списка по убыванию.
    """
    order = np.argsort(-sims, axis=1)
    top = order[:, 0]
    srt = np.take_along_axis(sims, order, axis=1)
    mid = srt[:, sims.shape[1] // 2]
    return top, srt[:, 0] - mid


def auc(pos, neg) -> float:
    """Вероятность, что случайный позитив выше случайного негатива (Манн — Уитни)."""
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    wins = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(wins / (len(pos) * len(neg)))


def rate(values, threshold: float) -> float:
    """Доля значений не ниже порога."""
    values = np.asarray(values, float)
    return float((values >= threshold).mean()) if len(values) else float("nan")


def match_rate(values, target: float) -> float:
    """Порог, при котором доля значений не ниже него — не больше `target`.

    Перенос рабочей точки: «у bge-m3 ловушки проходили в 10 % случаев» →
    порог новой модели, при котором проходит не больше 10 %. Порог берётся
    чуть выше k-го сверху значения, поэтому доля не превышает цели.
    """
    vals = np.sort(np.asarray(values, float))[::-1]
    if not len(vals):
        return float("nan")
    k = int(math.floor(target * len(vals) + 1e-9))
    if k >= len(vals):
        return round(float(vals[-1]), 3)
    return round(float(vals[k]) + 0.001, 3)


def keep_recall(values, recall: float) -> float:
    """Наибольший порог, при котором доля значений не ниже него — не меньше `recall`."""
    vals = np.sort(np.asarray(values, float))[::-1]
    if not len(vals):
        return float("nan")
    k = max(1, int(math.ceil(recall * len(vals) - 1e-9)))
    return math.floor(float(vals[k - 1]) * 1000) / 1000


# --------------------------------------------------------------- модели
class Model:
    """Модель эмбеддингов через дверь демона: тот же адрес, политика и профиль."""

    def __init__(self, cfg: dict, name: str) -> None:
        self.name = name
        self.embedder = llm.embedder(cfg, model=name, keep_alive="10m")
        self.profile = self.embedder.profile
        self.base = privacy.llm_base_url(cfg)
        self.seconds: dict[str, float] = {}

    def embed(self, texts: list[str], role: str, what: str) -> np.ndarray:
        """Векторы с разметкой профиля: `role` — query, document или pair."""
        mark = {"query": self.profile.query, "document": self.profile.document,
                "pair": self.profile.pair}[role]
        started = time.monotonic()
        got = self.embedder.run([mark(t) for t in texts], 900)
        self.seconds[what] = time.monotonic() - started
        if len(got) != len(texts):
            raise SeamTransportError(f"{self.name}: сервер не дал векторов ({what})")
        return unit(np.array(got))

    def latency_ms(self, text: str, tries: int = 10) -> float:
        """Медиана одного запроса дежавю (один короткий текст), модель уже в памяти."""
        self.embedder.run([self.profile.query(text)], 60)
        times = []
        for _ in range(tries):
            started = time.monotonic()
            self.embedder.run([self.profile.query(text)], 60)
            times.append((time.monotonic() - started) * 1000)
        return statistics.median(times)

    def memory(self) -> dict | None:
        """Сколько модель занимает по `/api/ps` Ollama; None — сервер не сказал."""
        url = self.base.rstrip("/") + "/api/ps"
        try:
            r = requests.get(url, timeout=10, **privacy.proxies_for(url))
            models = r.json().get("models", [])
        except (OSError, ValueError, AttributeError):
            return None
        want = self.name if ":" in self.name else self.name + ":latest"
        for m in models:
            if m.get("name") in (self.name, want) or m.get("model") in (self.name, want):
                return {"size_mb": round(m.get("size", 0) / 2**20), "vram_mb": round(m.get("size_vram", 0) / 2**20)}
        return None


# --------------------------------------------------------------- задачи
def synthetic_vectors(model: Model, data: dict) -> dict:
    """Все тексты синтетического набора — одним проходом на модель."""
    cores = data["cores"]
    v = {}
    v["dv_cores"] = model.embed([f"{c['name']}. {c['status']}"[:400] for c in cores], "document", "ядра дежавю")
    v["fragments"] = model.embed([" ".join(f["text"].split())[-DEJA_VU_WINDOW:] for f in data["fragments"]],
                                 "query", "реплики")
    v["off_topic"] = model.embed([" ".join(t.split())[-DEJA_VU_WINDOW:] for t in data["off_topic"]],
                                 "query", "посторонние реплики")
    v["pair_a"] = model.embed([p["a"] for p in data["pairs"]], "pair", "пары A")
    v["pair_b"] = model.embed([p["b"] for p in data["pairs"]], "pair", "пары B")
    v["docs"] = model.embed([f"{c['name']}\n## Статус\n{c['status']}\n## Суть\n{c['details']}" for c in cores],
                            "document", "документы поиска")
    v["queries"] = model.embed([q["q"] for q in data["queries"]], "query", "вопросы")
    v["traps"] = model.embed(list(data["traps"]), "query", "ловушки")
    return v


def evaluate(v: dict, data: dict) -> dict:
    """Метрики одной модели (одной размерности) на синтетическом наборе."""
    ids = [c["id"] for c in data["cores"]]
    index = {cid: i for i, cid in enumerate(ids)}
    out: dict = {}

    # дежавю: реплика → ядро, отрыв лидера от медианы
    sims = v["fragments"] @ v["dv_cores"].T
    gold = np.array([index[f["core"]] for f in data["fragments"]])
    top, on_margin = margins(sims)
    correct = top == gold
    ranks = (sims > sims[np.arange(len(gold)), gold][:, None]).sum(axis=1) + 1
    _top_off, off_margin = margins(v["off_topic"] @ v["dv_cores"].T)
    out["deja_vu"] = {"top1": float(correct.mean()), "mrr": float((1.0 / ranks).mean()),
                      "auc_on_vs_off": auc(on_margin[correct], off_margin),
                      "on_margin": on_margin.tolist(), "correct": correct.tolist(),
                      "off_margin": off_margin.tolist()}

    # ревизия ядер: косинус пар по меткам
    pc = (v["pair_a"] * v["pair_b"]).sum(axis=1)
    labels = np.array([p["label"] for p in data["pairs"]])
    dup, rel, unrel = pc[labels == "dup"], pc[labels == "related"], pc[labels == "unrelated"]
    out["tier3"] = {"auc_dup_vs_rest": auc(dup, np.concatenate([rel, unrel])),
                    "auc_dup_vs_related": auc(dup, rel),
                    "dup": dup.tolist(), "related": rel.tolist(), "unrelated": unrel.tolist()}

    # поиск: вопрос → документ ядра; ловушки — лучший косинус
    qs = v["queries"] @ v["docs"].T
    ts = v["traps"] @ v["docs"].T
    gold_sets = [{index[g] for g in q["gold"]} for q in data["queries"]]
    best_rank = []
    nongold = []
    for row, gs in zip(qs, gold_sets):
        order = list(np.argsort(-row))
        best_rank.append(min(order.index(g) for g in gs) + 1)
        nongold.extend(float(row[j]) for j in range(len(row)) if j not in gs)
    best_rank = np.array(best_rank)
    best_q, best_t = qs.max(axis=1), ts.max(axis=1)
    out["search"] = {"r1": float((best_rank == 1).mean()), "r5": float((best_rank <= 5).mean()),
                     "mrr": float((1.0 / best_rank).mean()), "auc_real_vs_trap": auc(best_q, best_t),
                     "best_real": best_q.tolist(), "best_trap": best_t.tolist(), "nongold": nongold}

    # разброс: косинусы всех пар документов — почему абсолютный порог не переносится
    dd = v["docs"] @ v["docs"].T
    tri = dd[np.triu_indices(len(dd), k=1)]
    out["spread"] = {"median": float(np.median(tri)), "p95": float(np.quantile(tri, 0.95))}
    return out


def operating_point(m: dict, t: dict) -> dict:
    """Как ведут себя контуры при порогах `t`: доли срабатываний на наборе."""
    dv, t3, s = m["deja_vu"], m["tier3"], m["search"]
    on, ok, off = np.array(dv["on_margin"]), np.array(dv["correct"]), np.array(dv["off_margin"])
    rest = np.array(t3["related"] + t3["unrelated"])
    fired = on >= t["deja_vu_margin"]
    return {
        "deja_vu_hit": float((fired & ok).mean()),
        "deja_vu_wrong": float((fired & ~ok).mean()),
        "deja_vu_offtopic": rate(off, t["deja_vu_margin"]),
        "tier3_recall": rate(t3["dup"], t["tier3_prefilter"]),
        "tier3_pass_rest": rate(rest, t["tier3_prefilter"]),
        "search_confident": rate(s["best_real"], t["search_low_sim"]),
        "search_trap_pass": rate(s["best_trap"], t["search_low_sim"]),
        "search_floor_share": rate(s["nongold"], t["search_sim_floor"]),
    }


def calibrate(m: dict, ref: dict | None) -> dict:
    """Пороги новой модели, переносящие рабочую точку bge-m3 (или цели ABS_TARGETS).

    Ревизия ядер — сохранить полноту дублей; поиск и дежавю — не поднять долю
    ложных срабатываний: ловушки над гейтом, шум над полом, дежавю на
    постороннем разговоре.
    """
    if ref is not None:
        point = operating_point(ref, BGE_M3_THRESHOLDS)
        goals = {"tier3_recall": point["tier3_recall"], "trap_pass": point["search_trap_pass"],
                 "floor_share": point["search_floor_share"], "offtopic_trigger": point["deja_vu_offtopic"]}
    else:
        goals = dict(ABS_TARGETS)
    return {
        "tier3_prefilter": keep_recall(m["tier3"]["dup"], goals["tier3_recall"]),
        "search_sim_floor": match_rate(m["search"]["nongold"], goals["floor_share"]),
        "search_low_sim": match_rate(m["search"]["best_trap"], goals["trap_pass"]),
        "deja_vu_margin": match_rate(m["deja_vu"]["off_margin"], goals["offtopic_trigger"]),
        "goals": goals,
    }


# --------------------------------------------------------------- ваш граф
def _clean_transcript(text: str) -> str:
    """Стенограмма → поток речи: без заголовков, меток времени и имён говорящих."""
    lines = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith(("#", ">", "---")):
            continue
        ln = re.sub(r"\[?\d{1,2}:\d{2}(?::\d{2})?\]?", " ", ln)
        ln = re.sub(r"^\s*(?:\*\*)?[^:*\n]{1,40}?(?:\*\*)?:\s", " ", ln)
        lines.append(ln)
    return " ".join(" ".join(lines).split())


def graph_corpus(graph: pathlib.Path, transcripts: pathlib.Path | None, limit_meetings: int) -> dict:
    """Слабая разметка из самого графа: ядра, пары-дубли и реплики встреч.

    * Дубли — пометки «возможный дубль» и слитые ядра (оригинал — из
      `.tier3_backup`). Они прошли отбор bge-m3 (косинус ≥ 0.55) — для
      сравнения моделей смещены в пользу bge-m3; годятся для проверки, что
      новая модель на своём пороге их не теряет.
    * Реплики — окна стенограмм; верные ядра — те, в чьей хронике эта встреча
      (разметку делал экстрактор, а не эмбеддинги, — без смещения к модели).
    """
    folder = graph / "Ядра"
    cores = tier3.load_cores(folder) if folder.is_dir() else []
    by_name = {c["name"]: c for c in cores}
    dup_pairs: set[tuple[str, str]] = set()
    for c in cores:
        for other in re.findall(r"возможный дубль: \[\[Ядра/([^\]|]+)", c["text"]):
            if other in by_name and other != c["name"]:
                dup_pairs.add(tuple(sorted((c["name"], other))))
    merged = []
    backups = sorted((folder / ".tier3_backup").glob("*/*.md")) if folder.is_dir() else []
    for p in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        text = p.read_text(encoding="utf-8")
        target = stub_target(text) if is_merged(text) else None
        target = target.split("|")[0].split("/")[-1].strip() if target else None
        if not target or target not in by_name:
            continue
        copies = [b for b in backups if b.stem == p.stem and not is_merged(b.read_text(encoding="utf-8"))]
        if copies:
            orig = copies[-1].read_text(encoding="utf-8")
            merged.append((tier3_repr(p.stem, orig), by_name[target]["repr"]))
    windows: list[tuple[str, set[str]]] = []
    if transcripts is not None and transcripts.is_dir():
        files = sorted(f for f in transcripts.glob("*.md")
                       if re.match(r"\d{4}-\d{2}-\d{2}_\d{4}", f.stem) and "_" not in f.stem[16:])
        for f in files[-limit_meetings:]:
            stamp = f.stem[:15]
            linked = {c["name"] for c in cores if any(stamp in m for m in c["meetings"])}
            if not linked:
                continue
            speech = _clean_transcript(f.read_text(encoding="utf-8"))
            for i in range(0, min(len(speech), 20 * DEJA_VU_WINDOW), DEJA_VU_WINDOW):
                chunk = speech[i:i + DEJA_VU_WINDOW]
                if len(chunk) >= 300:          # как контур: меньше 300 знаков он не сверяет
                    windows.append((chunk, linked))
    return {"cores": cores, "dup_pairs": sorted(dup_pairs), "merged": merged, "windows": windows}


def tier3_repr(stem: str, text: str) -> str:
    """repr ядра для ревизии — как в `tier3.load_cores`, по тексту из бэкапа."""
    def sect(title: str) -> str:
        m = re.search(rf"## {title}\n(.*?)(?=\n## |\Z)", text, re.S)
        return " ".join(m.group(1).split()) if m else ""
    essence = sect("Суть") or sect("Задача одной фразой") or tier3._plain(sect("Статус"))
    return f"{stem}. {essence}"[:tier3.REPR_LIMIT]


def evaluate_graph(model: Model, corpus: dict) -> dict:
    """Метрики модели на вашем графе: разброс пар, дубли, реплики встреч."""
    cores = corpus["cores"]
    names = [c["name"] for c in cores]
    pos = {n: i for i, n in enumerate(names)}
    pv = model.embed([c["repr"] for c in cores], "pair", "ядра графа (ревизия)")
    allc = pv @ pv.T
    tri = allc[np.triu_indices(len(allc), k=1)]
    out = {"cores": len(cores), "pair_median": float(np.median(tri)) if len(tri) else float("nan"),
           "pair_p99": float(np.quantile(tri, 0.99)) if len(tri) else float("nan"), "all_pairs": tri.tolist()}
    marked = [float(allc[pos[a], pos[b]]) for a, b in corpus["dup_pairs"]]
    if corpus["merged"]:
        ma = model.embed([a for a, _ in corpus["merged"]], "pair", "слитые ядра")
        mb = model.embed([b for _, b in corpus["merged"]], "pair", "их каноны")
        marked += (ma * mb).sum(axis=1).tolist()
    out["dup"] = marked
    if corpus["windows"]:
        dv = model.embed([deja_vu_core_text(c["name"], c["text"]) for c in cores],
                         "document", "ядра графа (дежавю)")
        wv = model.embed([w for w, _ in corpus["windows"]], "query", "окна стенограмм")
        sims = wv @ dv.T
        top, mar = margins(sims)
        hit = np.array([names[t] in linked for t, (_, linked) in zip(top, corpus["windows"])])
        out["windows"] = {"n": len(hit), "hit1": float(hit.mean()), "margin": mar.tolist(), "hit": hit.tolist()}
    return out


# --------------------------------------------------------------- отчёт
def _pct(x: float) -> str:
    return "—" if x != x else f"{100 * x:.0f}%"


def _num(x: float) -> str:
    return "—" if x != x else f"{x:.3f}"


def report(results: dict, ref_name: str | None) -> str:
    """Человекочитаемая сводка: качество, скорость, пороги, кусок конфига."""
    lines = ["", "## Качество без порогов (синтетический набор, русский)", "",
             "| вариант | дежавю top-1 | MRR | отрыв: тема vs постороннее (AUC) | дубли vs прочие (AUC) | "
             "дубли vs соседние задачи (AUC) | поиск R@1 | R@5 | MRR | вопрос vs ловушка (AUC) | разброс медиана / p95 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        m = r["metrics"]
        lines.append(f"| {name} | {_pct(m['deja_vu']['top1'])} | {_num(m['deja_vu']['mrr'])} | "
                     f"{_num(m['deja_vu']['auc_on_vs_off'])} | {_num(m['tier3']['auc_dup_vs_rest'])} | "
                     f"{_num(m['tier3']['auc_dup_vs_related'])} | {_pct(m['search']['r1'])} | "
                     f"{_pct(m['search']['r5'])} | {_num(m['search']['mrr'])} | "
                     f"{_num(m['search']['auc_real_vs_trap'])} | "
                     f"{_num(m['spread']['median'])} / {_num(m['spread']['p95'])} |")
    lines += ["", "## Скорость и память", "", "| модель | запрос дежавю, мс | документов/с | память Ollama |",
              "|---|---|---|---|"]
    for name, r in results.items():
        if r.get("variant_of"):
            continue
        perf = r["perf"]
        mem = perf.get("memory")
        mem_s = f"{mem['size_mb']} МБ (VRAM {mem['vram_mb']} МБ)" if mem else "—"
        lines.append(f"| {name} | {perf['latency_ms']:.0f} | {perf['docs_per_s']:.1f} | {mem_s} |")
    lines += ["", "## Пороги и как ведут себя контуры", ""]
    if ref_name:
        lines.append(f"Эталон — {ref_name} на своих порогах; остальным подобраны пороги с той же долей "
                     "ложных срабатываний и той же полнотой дублей.")
    else:
        lines.append(f"bge-m3 в прогоне нет — пороги подобраны под цели {ABS_TARGETS}.")
    lines += ["", "| вариант | порог ревизии | пол поиска | гейт поиска | отрыв дежавю | дежавю: верно / неверно / "
              "на постороннем | ревизия: полнота дублей / пропуск прочих | поиск: уверен / ловушки |",
              "|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        t, p = r["thresholds"], r["point"]
        lines.append(f"| {name} | {_num(t['tier3_prefilter'])} | {_num(t['search_sim_floor'])} | "
                     f"{_num(t['search_low_sim'])} | {_num(t['deja_vu_margin'])} | "
                     f"{_pct(p['deja_vu_hit'])} / {_pct(p['deja_vu_wrong'])} / {_pct(p['deja_vu_offtopic'])} | "
                     f"{_pct(p['tier3_recall'])} / {_pct(p['tier3_pass_rest'])} | "
                     f"{_pct(p['search_confident'])} / {_pct(p['search_trap_pass'])} |")
    graph_rows = [(n, r["graph"]) for n, r in results.items() if r.get("graph")]
    if graph_rows:
        lines += ["", "## Ваш граф (слабая разметка)", "",
                  "| вариант | ядер | косинус пар: медиана / p99 | пар на суд NLI при пороге | помеченные дубли "
                  "выше порога | окон стенограмм | дежавю top-1 по хронике | срабатываний при пороге (верно / "
                  "неверно) |", "|---|---|---|---|---|---|---|---|"]
        for name, g in graph_rows:
            t = results[name]["thresholds"]
            pairs = rate(g["all_pairs"], t["tier3_prefilter"])
            dup = rate(g["dup"], t["tier3_prefilter"]) if g["dup"] else float("nan")
            w = g.get("windows")
            if w:
                mar, hit = np.array(w["margin"]), np.array(w["hit"])
                fired = mar >= t["deja_vu_margin"]
                wins = f"{w['n']} | {_pct(w['hit1'])} | {_pct((fired & hit).mean())} / {_pct((fired & ~hit).mean())}"
            else:
                wins = "— | — | —"
            lines.append(f"| {name} | {g['cores']} | {_num(g['pair_median'])} / {_num(g['pair_p99'])} | "
                         f"{_pct(pairs)} | {_pct(dup)} ({len(g['dup'])}) | {wins} |")
        lines.append("")
        lines.append("Помеченные дубли графа отобраны ревизией на bge-m3 (косинус ≥ 0.55): для сравнения "
                     "моделей они смещены в её пользу, но показывают, не теряет ли новая модель уже найденное.")
    if any(r.get("variant_of") for r in results.values()):
        lines += ["", "Строки с @N — те же векторы, урезанные матрёшкой: размерность в конфиге пока не "
                  "задаётся, строка показывает, чего стоило бы урезание (кэш поиска в 3 раза меньше)."]
    for name, r in results.items():
        if r["family"] == "bge-m3" or r.get("variant_of"):
            continue
        t = r["thresholds"]
        snippet = {"sufler": {"embed_model": name,
                              "embed_thresholds": {k: t[k] for k in THRESHOLD_KEYS}}}
        lines += ["", f"### Кусок config.yaml для {name}", "", "```yaml",
                  yaml.safe_dump(snippet, allow_unicode=True, sort_keys=False).rstrip(), "```",
                  "Синтетический набор мал (45 ядер): пороги — отправная точка; строка «Ваш граф» "
                  "показывает, как они лягут на ваши данные."]
    return "\n".join(lines)


# --------------------------------------------------------------- точка входа
def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)


def main(argv: list[str] | None = None) -> int:
    harden_umask()   # цифры в --json — производная вашего графа: файл только владельцу
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS),
                    help="имена моделей Ollama; эталон для порогов — первая модель семейства bge-m3")
    ap.add_argument("--data", type=pathlib.Path,
                    default=code_root(__file__) / "config" / "embed_bench_demo.yaml",
                    help="синтетический набор (формат — в шапке файла)")
    ap.add_argument("--graph", type=pathlib.Path, default=None,
                    help="граф для слабой разметки; по умолчанию — sufler.graph_dir из конфига")
    ap.add_argument("--no-graph", action="store_true", help="только синтетический набор")
    ap.add_argument("--transcripts", type=pathlib.Path, default=None,
                    help="папка стенограмм; по умолчанию — log.transcripts_dir из конфига")
    ap.add_argument("--limit-meetings", type=int, default=40, help="сколько последних встреч брать")
    ap.add_argument("--mrl", type=int, action="append", default=[],
                    help="ещё строка с урезанием векторов до N измерений (для моделей с матрёшкой)")
    ap.add_argument("--json", type=pathlib.Path, default=None, help="записать все цифры в JSON")
    ap.add_argument("--force", action="store_true", help="мерить и на занятой машине")
    args = ap.parse_args(argv)

    root = _root()
    busy = busy_signals.machine_busy(root)
    if busy and not args.force:
        print(f"embed_bench: машина занята ({', '.join(busy)}) — бенч грузит Ollama двумя моделями; "
              f"запустите позже или с --force")
        return 1
    cfg_path = root / "config" / "config.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists()
           else {"llm": {"base_url": "http://127.0.0.1:11434"}, "sufler": {}, "log": {"transcripts_dir": "transcripts"}})
    data = yaml.safe_load(args.data.read_text(encoding="utf-8"))

    graph = None
    if not args.no_graph:
        graph = args.graph or graphs.graph_dir(cfg)
    transcripts = args.transcripts
    if transcripts is None and graph is not None:
        tdir = (cfg.get("log") or {}).get("transcripts_dir") or "transcripts"
        transcripts = root / tdir
    corpus = graph_corpus(graph, transcripts, args.limit_meetings) if graph is not None else None
    if corpus is not None and not corpus["cores"]:
        print(f"embed_bench: в {graph} нет ядер — разметка графа пропущена")
        corpus = None

    results: dict = {}
    ref_name = None
    for name in args.models:
        try:
            model = Model(cfg, name)
        except privacy.PrivacyRefused as exc:
            print(f"embed_bench: адрес модели запрещён настройкой: {exc}")
            return exit_codes.EXIT_PRIVACY_REFUSED
        if model.embedder.refused:
            print(f"embed_bench: {name}: {model.embedder.refused}")
            return exit_codes.EXIT_PRIVACY_REFUSED
        print(f"… {name}: синтетический набор", flush=True)
        try:
            vecs = synthetic_vectors(model, data)
            perf = {"latency_ms": model.latency_ms("ну короче по витрине опять сроки поехали"),
                    "docs_per_s": len(data["cores"]) / max(model.seconds["документы поиска"], 1e-6),
                    "memory": model.memory()}
            g = None
            if corpus is not None:
                print(f"… {name}: ваш граф ({len(corpus['cores'])} ядер, {len(corpus['windows'])} окон)", flush=True)
                g = evaluate_graph(model, corpus)
        except SeamTransportError as exc:
            print(f"embed_bench: {name} не отвечает: {exc} — модель стоит? `ollama pull {name}`")
            return exit_codes.EXIT_ENGINE_UNAVAILABLE
        dim = vecs["docs"].shape[1]
        variants = [(name, vecs, None)]
        if model.profile.family == "embeddinggemma":
            for d in sorted(set(args.mrl)):
                if d < dim:
                    variants.append((f"{name}@{d}", {k: truncate(v, d) for k, v in vecs.items()}, name))
        for vname, vv, parent in variants:
            results[vname] = {"family": model.profile.family, "dim": vv["docs"].shape[1],
                              "metrics": evaluate(vv, data), "perf": perf, "variant_of": parent,
                              "graph": g if parent is None else None}
        if ref_name is None and model.profile.family == "bge-m3":
            ref_name = name

    ref = results[ref_name]["metrics"] if ref_name else None
    for name, r in results.items():
        if r["family"] == "bge-m3" and not r.get("variant_of"):
            r["thresholds"] = dict(BGE_M3_THRESHOLDS)
        else:
            r["thresholds"] = calibrate(r["metrics"], ref)
        r["point"] = operating_point(r["metrics"], r["thresholds"])

    print(report(results, ref_name))
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nцифры: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
