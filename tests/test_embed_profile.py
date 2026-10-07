"""Профиль модели эмбеддингов: префиксы задачи и пороги едут вместе с именем.

Пороги дежавю, ревизии ядер и поиска замерены на bge-m3. Модель берётся из
одного ключа `sufler.embed_model` на все три контура, поэтому смена модели без
профиля молча применяла бы числа bge-m3 к чужому разбросу косинусов, а
EmbeddingGemma считала бы векторы без префиксов, на которых обучена. Тесты
держат три обещания: bge-m3 не меняется ни в одном байте (ни векторы, ни ключ
кэша, ни пороги), у EmbeddingGemma запрос и документ размечаются по-разному на
каждом контуре, и пороги владельца доходят до каждого контура.
"""
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from charoite_graph import graph_search as gs  # noqa: E402
from charoite_graph.model_seam import (BGE_M3_THRESHOLDS, Embedder, Judge,  # noqa: E402
                                       embed_profile)
from charoite_schema import CHAROITE  # noqa: E402

GEMMA_QUERY = "task: search result | query: "
GEMMA_DOC = "title: none | text: "
GEMMA_PAIR = "task: sentence similarity | query: "
ALL_FOUR = {"tier3_prefilter": 0.61, "search_sim_floor": 0.2, "search_low_sim": 0.33, "deja_vu_margin": 0.11}


# ------------------------------------------------------------ сам профиль
@pytest.mark.parametrize("name", ["bge-m3", "bge-m3:latest", "BGE-M3:567m"])
def test_bge_m3_keeps_bare_text_and_its_own_thresholds(name):
    p = embed_profile(name)
    assert p.family == "bge-m3" and p.calibrated
    assert p.query("x") == p.document("x") == p.pair("x") == "x"
    assert (p.tier3_prefilter, p.search_sim_floor, p.search_low_sim, p.deja_vu_margin) == \
        (0.55, 0.35, 0.47, 0.04)
    assert p.cache_tag() == "", "у bge-m3 подпись префиксов пуста — ключ кэша прежний"


@pytest.mark.parametrize("name", ["embeddinggemma-2", "embeddinggemma:300m", "embeddinggemma-2:latest"])
def test_gemma_family_marks_query_document_and_pair_differently(name):
    p = embed_profile(name)
    assert p.family == "embeddinggemma"
    assert p.query("а") == GEMMA_QUERY + "а"
    assert p.document("а") == GEMMA_DOC + "а"
    assert p.pair("а") == GEMMA_PAIR + "а"
    assert p.cache_tag(), "префиксы меняют векторы — подпись кэша непустая"
    assert not p.calibrated, "пороги bge-m3 у чужой модели — не замер"


def test_unknown_model_gets_bare_text_and_is_not_calibrated():
    p = embed_profile("nomic-embed-text")
    assert p.family == "" and p.query("x") == "x" and not p.calibrated
    assert p.tier3_prefilter == BGE_M3_THRESHOLDS["tier3_prefilter"]


def test_owner_thresholds_apply_and_only_a_full_set_counts_as_calibrated():
    full = embed_profile("embeddinggemma-2", ALL_FOUR)
    assert full.calibrated
    assert (full.tier3_prefilter, full.search_sim_floor, full.search_low_sim, full.deja_vu_margin) == \
        (0.61, 0.2, 0.33, 0.11)
    partial = embed_profile("embeddinggemma-2", {"tier3_prefilter": 0.61})
    assert partial.tier3_prefilter == 0.61 and not partial.calibrated, \
        "три порога из четырёх — всё ещё числа bge-m3 у чужой модели"
    own = embed_profile("bge-m3", {"deja_vu_margin": 0.06})
    assert own.calibrated and own.deja_vu_margin == 0.06


@pytest.mark.parametrize("junk", [
    {"tier3_prefiltr": 0.1},            # опечатка в ключе
    {"tier3_prefilter": True},          # булево — не число
    {"tier3_prefilter": "много"},
    {"tier3_prefilter": float("nan")},
    {"tier3_prefilter": 1.5},           # не косинус
    {"tier3_prefilter": None},
])
def test_junk_thresholds_are_dropped_not_turned_into_zero(junk):
    p = embed_profile("bge-m3", junk)
    assert p.tier3_prefilter == 0.55


def test_embedder_without_profile_gets_the_profile_of_its_name():
    e = Embedder(lambda t, s: [], "embeddinggemma-2")
    assert e.profile.document("x") == GEMMA_DOC + "x"
    explicit = embed_profile("embeddinggemma-2", ALL_FOUR)
    assert Embedder(lambda t, s: [], "embeddinggemma-2", profile=explicit).profile is explicit


# ------------------------------------------------------------ поиск по графу
def _graph(tmp_path):
    g = tmp_path / "Работа"
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "Шлюз.md").write_text("# Шлюз\nпровайдер выбран, пилот до сентября\n", encoding="utf-8")
    return g


def _recording(model, profile=None):
    """Векторизатор, запоминающий тексты; вектор — постоянный (косинус 1)."""
    seen: list[str] = []

    def run(texts, timeout):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    return Embedder(run, model, profile=profile), seen


def test_bge_m3_search_cache_key_is_byte_for_byte_the_old_one(tmp_path):
    e, _ = _recording("bge-m3:latest")
    s = gs.GraphSearch(_graph(tmp_path), embedder=e, data_dir=tmp_path / "d", schema=CHAROITE)
    assert s.cache_key() == (f"bge-m3:latest|chunks{gs.CHUNK_VERSION}|{gs.CHUNK_CHARS}|"
                             f"{gs.MAX_CHUNKS}|{gs.MAX_CHUNKS_NODE}"), \
        "ключ сменился — каждая установка пересобрала бы кэш векторов"
    g, _ = _recording("embeddinggemma-2")
    other = gs.GraphSearch(_graph(tmp_path / "2"), embedder=g, data_dir=tmp_path / "d2", schema=CHAROITE)
    assert other.cache_key() != s.cache_key().replace("bge-m3:latest", "embeddinggemma-2")


def test_gemma_search_marks_blocks_as_documents_and_the_question_as_query(tmp_path):
    e, seen = _recording("embeddinggemma-2")
    s = gs.GraphSearch(_graph(tmp_path), embedder=e, data_dir=tmp_path / "d", schema=CHAROITE)
    s.refresh(force=True)
    s.embed_pending()
    assert seen and all(t.startswith(GEMMA_DOC) for t in seen), seen
    seen.clear()
    s.search("кого выбрали провайдером?")
    assert seen == [GEMMA_QUERY + "кого выбрали провайдером?"]


def test_bge_m3_search_sends_bare_text(tmp_path):
    e, seen = _recording("bge-m3:latest")
    s = gs.GraphSearch(_graph(tmp_path), embedder=e, data_dir=tmp_path / "d", schema=CHAROITE)
    s.refresh(force=True)
    s.embed_pending()
    s.search("провайдер")
    assert seen[-1] == "провайдер" and not any(t.startswith(("task:", "title:")) for t in seen)


def test_search_floor_comes_from_the_profile(tmp_path):
    """Косинус 1.0 проходит любой пол, кроме пола выше единицы — его профиль не
    примет; поэтому меряем на векторе с косинусом 0.6 к запросу."""
    def run(texts, timeout):
        return [[1.0, 0.0] if t.startswith(GEMMA_QUERY) else [0.6, 0.8] for t in texts]

    def hits(floor):
        prof = embed_profile("embeddinggemma-2", {**ALL_FOUR, "search_sim_floor": floor})
        d = tmp_path / f"f{floor}"
        s = gs.GraphSearch(_graph(d), embedder=Embedder(run, "embeddinggemma-2", profile=prof),
                           data_dir=d / "data", schema=CHAROITE)
        s.refresh(force=True)
        s.embed_pending()
        return s.search("qqqzzz")

    assert hits(0.5).blocks, "косинус 0.6 выше пола 0.5 — блок найден по смыслу"
    assert not hits(0.7).blocks, "пол 0.7 из профиля отрезал косинус 0.6"


@pytest.mark.parametrize("low_sim, status", [(0.55, gs.Verdict.CONFIDENT), (0.65, gs.Verdict.WEAK)])
def test_search_gate_comes_from_the_profile(tmp_path, low_sim, status):
    """Слов запроса в графе нет (покрытие 0), косинус лучшего блока 0.6: вердикт
    решает гейт косинуса — и это гейт модели из профиля, а не 0.47 bge-m3."""
    def run(texts, timeout):
        return [[1.0, 0.0] if t.startswith(GEMMA_QUERY) else [0.6, 0.8] for t in texts]

    prof = embed_profile("embeddinggemma-2", {**ALL_FOUR, "search_sim_floor": 0.5, "search_low_sim": low_sim})
    s = gs.GraphSearch(_graph(tmp_path), embedder=Embedder(run, "embeddinggemma-2", profile=prof),
                       data_dir=tmp_path / "data", schema=CHAROITE)
    s.refresh(force=True)
    s.embed_pending()
    assert s.search("qqqzzz").status is status


def test_verdict_gate_takes_the_model_threshold():
    assert gs.verdict(0.0, 0.40, True) is gs.Verdict.WEAK, "0.40 ниже гейта bge-m3"
    assert gs.verdict(0.0, 0.40, True, low_sim=0.33) is gs.Verdict.CONFIDENT


# ------------------------------------------------------------ ревизия ядер
def _cores(tmp_path, *names):
    g = tmp_path / "Работа"
    (g / "Ядра").mkdir(parents=True)
    for n in names:
        (g / "Ядра" / f"{n}.md").write_text(f"# {n}\n\n## Статус\nидёт работа над «{n}»\n", encoding="utf-8")
    return g


@pytest.mark.parametrize("prefilter, judged", [(0.55, 0), (0.45, 1)])
def test_tier3_prefilter_comes_from_the_profile(tmp_path, prefilter, judged):
    import tier3

    graph = _cores(tmp_path, "Одно", "Другое")
    seen: list[str] = []
    calls: list[tuple] = []

    def run(texts, timeout):
        seen.extend(texts)
        return [[1.0, 0.0], [0.5, math.sqrt(0.75)]]        # косинус 0.5

    prof = embed_profile("embeddinggemma-2", {**ALL_FOUR, "tier3_prefilter": prefilter})
    judge = Judge(lambda: True, lambda a, b: (calls.append((a, b)), 0.0)[1])
    tier3.revise(graph, embedder=Embedder(run, "embeddinggemma-2", profile=prof),
                 judge=judge, may_continue=lambda: True)
    assert all(t.startswith(GEMMA_PAIR) for t in seen), seen
    assert len(calls) // 2 == judged or (judged == 0 and not calls), \
        f"косинус 0.5 при пороге {prefilter}: судимых пар {judged}, а суд звали {len(calls)} раз"


def test_tier3_on_bge_m3_sends_bare_repr(tmp_path):
    import tier3

    graph = _cores(tmp_path, "Одно", "Другое")
    seen: list[str] = []

    def run(texts, timeout):
        seen.extend(texts)
        return [[1.0, 0.0], [0.0, 1.0]]

    tier3.revise(graph, embedder=Embedder(run, "bge-m3:latest"), judge=Judge(lambda: True, lambda a, b: 0.0),
                 may_continue=lambda: True)
    assert sorted(seen) == ["Другое. идёт работа над «Другое»", "Одно. идёт работа над «Одно»"]


# ------------------------------------------------------------ дежавю и слой моделей
def _cfg(**sufler):
    return {"llm": {"model": "тест-модель", "small_model": "тест-мелкая", "num_ctx": 8192, "temperature": 0.4},
            "sufler": {"role": "тестовая роль", **sufler}}


@pytest.mark.parametrize("sufler, margin", [
    ({}, 0.04),                                                                  # bge-m3 по умолчанию
    ({"deja_vu_margin": 0.07}, 0.07),                                            # старый ключ владельца
    ({"embed_model": "embeddinggemma-2", "deja_vu_margin": 0.04,
      "embed_thresholds": ALL_FOUR}, 0.11),                                      # замер сильнее шаблона
    ({"embed_model": "embeddinggemma-2", "embed_thresholds": {"deja_vu_margin": 0.2}}, 0.2),
    ({"embed_model": "embeddinggemma-2"}, 0.04),                                 # не замерено: bge-m3
])
def test_deja_vu_margin_precedence(sufler, margin):
    import daemon

    assert daemon.deja_vu_margin(_cfg(**sufler)) == pytest.approx(margin)


def test_uncalibrated_model_is_named_once(capsys):
    import llm
    import once

    once.forget(("embed", ("uncalibrated", "embeddinggemma-2")))
    cfg = _cfg(embed_model="embeddinggemma-2")
    llm.embed_model_profile(cfg, "embeddinggemma-2")
    llm.embed_model_profile(cfg, "embeddinggemma-2")
    err = capsys.readouterr().err
    assert err.count("пороги не замерены для embeddinggemma-2") == 1, err
    llm.embed_model_profile(_cfg(embed_model="embeddinggemma-2", embed_thresholds=ALL_FOUR), "embeddinggemma-2")
    llm.embed_model_profile(_cfg(), "bge-m3:latest")
    assert "не замерены" not in capsys.readouterr().err


def test_llm_embedder_carries_owner_thresholds():
    import llm

    e = llm.embedder(_cfg(embed_model="embeddinggemma-2", embed_thresholds=ALL_FOUR))
    assert e.model == "embeddinggemma-2" and e.profile.tier3_prefilter == 0.61 and e.profile.calibrated


@pytest.mark.parametrize("query, prefix", [(True, GEMMA_QUERY), (False, GEMMA_DOC)])
def test_deja_vu_marks_the_fragment_as_query_and_cores_as_documents(_ollama_маршруты, query, prefix):
    import daemon
    import llm
    import requests

    seen: dict = {}

    def ответ(url, **k):
        seen.update(k.get("json") or {})
        r = requests.Response()
        r.status_code = 200
        r._content = json.dumps({"embeddings": [[1.0, 0.0]]}).encode("utf-8")
        return r

    cfg = _cfg(embed_model="embeddinggemma-2", embed_thresholds=ALL_FOUR)
    _ollama_маршруты.сценарий_эмбеддингов(ответ)
    llm.LLM(cfg)
    assert daemon.deja_vu_embed(cfg, ["т"], query=query) == [[1.0, 0.0]]
    assert seen["input"] == [prefix + "т"], seen


# ------------------------------------------------------------ бенч: чистые функции
def test_bench_core_text_is_what_the_live_contour_sends(tmp_path):
    import daemon
    import embed_bench

    p = tmp_path / "Ядро.md"
    body = "# Ядро\n\n## Статус\nидёт _(обновлено 2026-08-20)_ " + "я" * 600 + "\n"
    p.write_text(body, encoding="utf-8")
    sent: list[str] = []
    daemon.warm_core_vectors([p], {}, lambda payload: (sent.extend(payload), [[1.0]] * len(payload))[1])
    assert sent == [embed_bench.deja_vu_core_text("Ядро", body)], \
        "бенч мерит дежавю не на том тексте, что шлёт живой контур"


def test_bench_margin_uses_the_same_median_as_the_live_contour():
    import embed_bench

    sims = np.array([[0.9, 0.5, 0.4, 0.3]])        # по убыванию: медиана — элемент len // 2 = 0.4
    top, margin = embed_bench.margins(sims)
    assert top[0] == 0 and margin[0] == pytest.approx(0.5)


def test_bench_match_rate_keeps_false_triggers_at_the_target():
    import embed_bench

    values = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]
    t = embed_bench.match_rate(values, 0.2)
    assert embed_bench.rate(values, t) <= 0.2 and embed_bench.rate(values, t - 0.02) > 0.2
    assert embed_bench.match_rate(values, 0.0) > 0.9, "цель 0 % — порог выше всех значений"


def test_bench_keep_recall_is_the_highest_threshold_with_that_recall():
    import embed_bench

    dups = [0.9, 0.8, 0.7, 0.6]
    t = embed_bench.keep_recall(dups, 0.75)
    assert embed_bench.rate(dups, t) >= 0.75 and embed_bench.rate(dups, t + 0.01) < 0.75


def test_bench_auc_orders_and_ties():
    import embed_bench

    assert embed_bench.auc([1, 2], [0, 0]) == 1.0
    assert embed_bench.auc([0], [1]) == 0.0
    assert embed_bench.auc([1], [1]) == 0.5


def test_bench_dataset_is_consistent():
    """Набор бенча — его ссылки на ядра обязаны существовать: битая ссылка
    роняла бы прогон у владельца, а не здесь."""
    import yaml

    data = yaml.safe_load((pathlib.Path(__file__).resolve().parent.parent / "config"
                           / "embed_bench_demo.yaml").read_text(encoding="utf-8"))
    ids = {c["id"] for c in data["cores"]}
    assert len(ids) == len(data["cores"])
    assert {f["core"] for f in data["fragments"]} <= ids
    assert all(set(q["gold"]) <= ids for q in data["queries"])
    assert {p["label"] for p in data["pairs"]} == {"dup", "related", "unrelated"}
    assert data["off_topic"] and data["traps"]
