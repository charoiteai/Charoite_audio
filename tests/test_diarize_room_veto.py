"""Склейка осколков под запретом — микрофон комнаты (№565).

29.09 очная на шестерых: одиночная связь склейки свела 30 кластеров в одну
метку, внутри группы были пары 0.13 — по калибровке 14.08 это разные люди.
Запрет не даёт слить группы, между которыми есть пара ниже VETO_BELOW. Без
запроса запрета склейка прежняя: её зовут запасной sherpa канала собеседников и
CLI.
"""
from __future__ import annotations

import math
import random
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import diarize as D  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

SR = 16000
SEG_S = 10.0


def _groups(parent: dict[int, int]) -> set[frozenset[int]]:
    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    out: dict[int, set[int]] = {}
    for k in parent:
        out.setdefault(find(k), set()).add(k)
    return {frozenset(g) for g in out.values()}


def _sim(vecs: dict[int, np.ndarray]) -> dict[tuple[int, int], float]:
    ks = sorted(vecs)
    return {(a, b): float(np.dot(vecs[a], vecs[b])) for i, a in enumerate(ks) for b in ks[i + 1:]}


def _chain_of_six() -> dict[int, np.ndarray]:
    """Шесть голосов по два кластера. Внутри голоса 0.70; второй кластер голоса
    тянется к первому кластеру следующего мостом 0.61 — выше порога склейки. Все
    прочие пары между голосами ниже запрета (0 и 0.427)."""
    dim = 12
    e = np.eye(dim)
    vecs = {}
    c = math.sqrt(1 - 0.70 ** 2 - 0.61 ** 2)
    for i in range(6):
        vecs[2 * i] = e[i]
        tail = 0.61 * e[i + 1] + c * e[6 + i] if i < 5 else math.sqrt(1 - 0.70 ** 2) * e[6 + i]
        vecs[2 * i + 1] = 0.70 * e[i] + tail
    return vecs


def _one_voice() -> dict[int, np.ndarray]:
    """Один голос с разбросом тембра: четыре кластера на углах 0°, 30°, 55°, 60°.
    Попарно 0.50–1.00 — ни одной пары ниже запрета, цепочка ≥ 0.60 связная."""
    return {k: np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])
            for k, a in enumerate((0, 30, 55, 60))}


# ------------------------------------------------------------- link_clusters


def test_the_chain_of_six_is_one_voice_without_the_veto_and_six_with_it():
    vecs = _chain_of_six()
    sim = _sim(vecs)
    assert _groups(D.link_clusters(list(vecs), sim, 0.60)) == {frozenset(vecs)}
    assert _groups(D.link_clusters(list(vecs), sim, 0.60, D.VETO_BELOW)) == {
        frozenset({2 * i, 2 * i + 1}) for i in range(6)}


def test_one_voice_with_a_spread_timbre_stays_one_voice_under_the_veto():
    vecs = _one_voice()
    sim = _sim(vecs)
    assert min(sim.values()) >= D.VETO_BELOW
    with_veto = _groups(D.link_clusters(list(vecs), sim, 0.60, D.VETO_BELOW))
    assert with_veto == _groups(D.link_clusters(list(vecs), sim, 0.60)) == {frozenset(vecs)}


@pytest.mark.parametrize("seed", range(20))
def test_a_veto_that_never_fires_gives_the_union_find_partition(seed):
    """Агломерация по убыванию пар при запрете, который не срабатывает, обязана
    дать те же компоненты, что прежний union-find (вход №565, r3, GLM M1)."""
    rng = random.Random(seed)
    vecs = {}
    for k in range(rng.randint(3, 14)):
        v = np.array([rng.gauss(0, 1) for _ in range(3)])
        vecs[k] = v / np.linalg.norm(v)
    sim = _sim(vecs)
    assert _groups(D.link_clusters(list(vecs), sim, 0.60, veto=-1.0)) == \
        _groups(D.link_clusters(list(vecs), sim, 0.60))


def test_a_nan_similarity_merges_nothing_under_the_veto():
    """Эмбеддинг с нулевой нормой даёт NaN: без запрета такая пара не сливается
    (NaN >= порога — ложь), с запретом — тоже, и порядок пар не ломается (выход №565,
    r1b, Sonnet M1)."""
    sim = {(0, 1): float("nan"), (0, 2): 0.3, (1, 2): 0.3}
    singles = {frozenset({0}), frozenset({1}), frozenset({2})}
    assert _groups(D.link_clusters([0, 1, 2], sim, 0.60, D.VETO_BELOW)) == singles
    assert _groups(D.link_clusters([0, 1, 2], sim, 0.60)) == singles


@pytest.mark.parametrize("veto", [None, D.VETO_BELOW])
def test_a_pair_exactly_at_the_threshold_merges(veto):
    """Порог склейки включительный в обоих режимах: пара ровно 0.60 — один голос."""
    assert _groups(D.link_clusters([0, 1], {(0, 1): 0.60}, 0.60, veto)) == {frozenset({0, 1})}


def test_a_worst_pair_exactly_at_the_veto_does_not_ban():
    """Запрет — строго ниже VETO_BELOW: худшая пара ровно на границе слияние не запрещает."""
    sim = {(0, 1): 0.9, (1, 2): 0.65, (0, 2): D.VETO_BELOW}
    assert _groups(D.link_clusters([0, 1, 2], sim, 0.60, D.VETO_BELOW)) == {frozenset({0, 1, 2})}


def test_the_bans_are_counted_in_the_log(capsys):
    """Счёт запретов — в журнал пересборки: по нему калибруют порог на поле (выход №565, r1)."""
    vecs = _chain_of_six()
    D.link_clusters(list(vecs), _sim(vecs), 0.60, D.VETO_BELOW)
    assert "запрещено слияний 5 " in capsys.readouterr().out


def test_no_bans_no_log_line(capsys):
    vecs = _one_voice()
    D.link_clusters(list(vecs), _sim(vecs), 0.60, D.VETO_BELOW)
    assert "запрещено" not in capsys.readouterr().out


def test_the_veto_is_checked_against_every_member_of_both_groups():
    """Запрет по худшей паре между группами, а не по мосту: мост 0.9, а третий член
    группы с чужаком — 0.2."""
    sim = {(0, 1): 0.95, (1, 2): 0.9, (0, 2): 0.2}
    assert _groups(D.link_clusters([0, 1, 2], sim, 0.60, D.VETO_BELOW)) == {
        frozenset({0, 1}), frozenset({2})}


@pytest.mark.parametrize("worst,groups", [
    (D.VETO_BELOW - 0.001, {frozenset({0, 1}), frozenset({2})}),
    (D.VETO_BELOW + 0.001, {frozenset({0, 1, 2})}),
])
def test_the_veto_boundary_is_the_worst_pair_between_groups(worst, groups):
    """Граница запрета — по худшей паре: чуть ниже VETO_BELOW мост 0.65 не сливает,
    чуть выше — сливает."""
    sim = {(0, 1): 0.9, (1, 2): 0.65, (0, 2): worst}
    assert _groups(D.link_clusters([0, 1, 2], sim, 0.60, D.VETO_BELOW)) == groups


# --------------------------------------------- боевой путь: diarize() → _merge_shards


@pytest.fixture
def room(monkeypatch):
    """sherpa без моделей: диаризатор отдаёт кластеры цепочки, экстрактор узнаёт
    кластер по уровню сигнала в куске и отдаёт его вектор."""
    vecs = _chain_of_six()
    segs = []
    audio = np.zeros(int(len(vecs) * 2 * SEG_S * SR), dtype=np.float32)
    t = 0.0
    for k in sorted(vecs):
        for _ in range(2):
            segs.append((t, t + SEG_S, k))
            audio[int(t * SR):int((t + SEG_S) * SR)] = (k + 1) / 100.0
            t += SEG_S

    class _Seg:
        def __init__(self, s, e, k):
            self.start, self.end, self.speaker = s, e, k

    class _Result(list):
        def sort_by_start_time(self):
            return self

    class _Diar:
        sample_rate = SR

        def __init__(self, cfg):
            pass

        def process(self, a):
            return _Result([_Seg(*x) for x in segs])

    class _Stream:
        def accept_waveform(self, sr, samples):
            self.k = int(round(float(samples[0]) * 100)) - 1

        def input_finished(self):
            pass

    class _Extractor:
        def __init__(self, cfg):
            pass

        def create_stream(self):
            return _Stream()

        def is_ready(self, st):
            return True

        def compute(self, st):
            return list(vecs[st.k])

    fake = types.SimpleNamespace(FastClusteringConfig=lambda **kw: kw,
                                 OfflineSpeakerDiarizationConfig=lambda **kw: kw,
                                 OfflineSpeakerDiarization=_Diar,
                                 SpeakerEmbeddingExtractor=_Extractor)
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    monkeypatch.setattr(D.sherpa_config, "segmentation_config", lambda *a, **k: None)
    monkeypatch.setattr(D.sherpa_config, "embedding_config", lambda *a, **k: None)
    return audio


def test_the_room_door_splits_the_chain_the_default_merges_it(room):
    """Настоящий путь пересборки: diarize_channel → diarize(-1) → _merge_shards.
    Без запрета — одна метка на шестерых (29.09), с запретом — шесть."""
    merged = rt.diarize_channel(room, SR)
    assert len({k for *_, k in merged}) == 1
    split = rt.diarize_channel(room, SR, veto=D.VETO_BELOW)
    assert len({k for *_, k in split}) == 6


def test_a_veto_with_a_hint_is_refused_not_dropped(room):
    """Подсказка числом и запрет не смешиваются: после числа склейки нет, и запрет
    был бы молча выброшен — вызов с обоими отвергается (выход №565, r1)."""
    with pytest.raises(ValueError):
        D.diarize(room, SR, num_speakers=6, veto=D.VETO_BELOW)
    with pytest.raises(ValueError):
        D.diarize(room, SR, num_speakers=1, veto=D.VETO_BELOW)
    assert len({k for *_, k in D.diarize(room, SR, num_speakers=6)}) == 12


# --------------------------------------------------------------------- mic_plan


MIC_S = 600.0


@pytest.mark.parametrize("meta,silent,call_s,plan", [
    ({"speakers": 7}, True, MIC_S, (None, D.VETO_BELOW)),         # комната
    ({"speakers": 7}, True, 0.8 * MIC_S, (None, D.VETO_BELOW)),   # граница доли
    ({"speakers": 7}, True, 0.79 * MIC_S, (7, None)),             # канал умер посреди встречи
    ({"speakers": 7}, True, None, (7, None)),                     # записи канала нет — №559
    ({"speakers": 7}, False, MIC_S, (None, None)),                # звонок
    ({"speakers": 2}, True, MIC_S, (None, None)),                 # живых мало — авто, как было
    ({"speakers": 13}, True, MIC_S, (None, None)),                # живых сверх диапазона
    ({}, True, MIC_S, (None, None)),                              # счёта нет
])
def test_mic_plan_cells(meta, silent, call_s, plan):
    assert rt.mic_plan(meta, silent, call_s, MIC_S) == plan


@pytest.mark.parametrize("n", [0, -1])
def test_auto_mode_takes_the_veto_whatever_the_auto_number(room, n):
    """0 и -1 — авто: запрет принимается и доходит до склейки (граница отказа — > 0)."""
    assert len({k for *_, k in D.diarize(room, SR, num_speakers=n, veto=D.VETO_BELOW)}) == 6
