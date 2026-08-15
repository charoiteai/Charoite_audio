"""Позиционная раскладка чанка (ревью 15.08): окна STT по голосам.

Проверяется логика, из-за которой раскладку вообще заводили, и ловушки двух
раундов дизайн-ревью: перекрытие чанков (0.5 с) повторяет лишь хвост, поэтому
придержка сегмента, начавшегося раньше зоны перекрытия, была бы потерей
реплики; pad-окна разных голосов не должны пересекаться (один звук — два
автора); фолбэк на STT целого чанка запрещён, когда политика исключила куски;
дообучение центроидов — транзакцией, придержанное не учит; мёртвый кандидат
не съедает слот лимита; общий _last не переползает между каналами.

plan_pieces — чистая функция, тестируется впрямую. SegmentTracker собирается
без ONNX-моделей: конструктор обходится, сегментация и эмбеддер — подставные.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

np = pytest.importorskip("numpy")

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from diarize_live import Piece, SegmentTracker, plan_pieces  # noqa: E402

SR = 16000
CHUNK = 3 * SR  # три секунды, как в проде


# ---------- plan_pieces: чистая логика окон ----------

def test_clipped_tail_inside_overlap_is_deferred():
    """Обрезан правым краем И живёт целиком в зоне перекрытия → придержать."""
    windows, deferred, kept = plan_pieces([(2.6, 2.98, 1)], CHUNK, SR)
    assert windows == [] and deferred is True and kept == []


def test_natural_end_near_edge_is_not_deferred():
    """Естественно закончившаяся реплика 2.0–2.9 не придерживается: следующий
    чанк повторит только последние полсекунды, придержка стала бы потерей."""
    windows, deferred, kept = plan_pieces([(1.5, 2.9, 1)], CHUNK, SR)
    assert deferred is False and kept == [(1.5, 2.9, 1)]
    assert len(windows) == 1


def test_clipped_long_segment_is_released_cut():
    """Сегмент, начавшийся до зоны перекрытия и обрезанный краем, выпускается
    обрезанным: полсекундный дубль на стыке дешевле потерянных слов."""
    windows, deferred, _ = plan_pieces([(1.0, 2.99, 1)], CHUNK, SR)
    assert deferred is False and len(windows) == 1


def test_reply_spanning_two_production_chunks_is_not_lost():
    """Реплика 1.6–2.9 глазами двух продакшен-чанков (шаг 2.5): в первом она
    выпускается, во втором её хвост 0–0.4 меньше min_stt и окна не получает —
    но реплика уже не потеряна."""
    first, deferred1, _ = plan_pieces([(1.6, 2.9, 1)], CHUNK, SR)
    assert deferred1 is False and len(first) == 1
    second, _, _ = plan_pieces([(0.0, 0.4, 1)], CHUNK, SR)
    assert second == []


def test_neighbours_of_same_voice_merge():
    raw = [(0.1, 0.9, 1), (1.1, 2.0, 1)]  # зазор 0.2 < gap
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    assert len(windows) == 1
    a, b, voice, rs, re_ = windows[0]
    assert voice == 1 and a <= 0.1 and b >= 2.0
    assert rs == 0.1 and re_ == 2.0


def test_micro_piece_gets_no_own_window_and_no_neighbour():
    """Полсекундное «да» чужим голосом не подписывается никому."""
    raw = [(0.1, 1.6, 1), (1.9, 2.2, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    assert [w[2] for w in windows] == [1]


def test_pad_windows_of_different_voices_do_not_overlap():
    """Запас по краям не заходит за середину зазора с куском другого голоса —
    иначе один и тот же звук распознаётся дважды под разными людьми."""
    raw = [(0.1, 1.4, 1), (1.5, 2.7, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    assert len(windows) == 2
    (a1, b1, v1, *_), (a2, b2, v2, *_) = windows
    assert v1 != v2
    assert b1 <= a2 + 1e-9
    assert b1 == pytest.approx(1.45)  # середина зазора, не 1.4+0.25


def test_pad_does_not_cover_foreign_micro_piece():
    """Микро-кусок чужого не получает окна, но и не попадает в чужой pad."""
    raw = [(0.1, 1.6, 1), (1.9, 2.2, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    a, b, voice, *_ = windows[0]
    assert b <= (1.6 + 1.9) / 2 + 1e-9


def test_pad_does_not_leak_past_chunk():
    windows, _, _ = plan_pieces([(0.05, 1.4, 1)], CHUNK, SR)
    a, b, *_ = windows[0]
    assert a == 0.0 and b <= CHUNK / SR


def test_unassigned_segments_are_ignored():
    windows, _, kept = plan_pieces([(0.1, 1.5, None)], CHUNK, SR)
    assert windows == [] and kept == []


# ---------- SegmentTracker.split: транзакционность и каналы ----------

class _Seg:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _FakeDiar:
    def __init__(self):
        self.plan: list[_Seg] = []
        self.fail = False

    def process(self, chunk):
        if self.fail:
            raise RuntimeError("onnx umer")
        return self

    def sort_by_start_time(self):
        return list(self.plan)


def _tracker(max_speakers: int = 8) -> SegmentTracker:
    t = object.__new__(SegmentTracker)
    t.sr = SR
    t.threshold = 0.62
    t.min_segment = 0.4
    t.min_new = 0.8
    t.max_speakers = max_speakers
    t.min_stt = 1.0
    t.step_s = 2.5
    t.new_glue = 0.45
    t._diar = _FakeDiar()
    t._centroids = []
    t._counts = []
    t._last_by_channel = {}
    t._embed = lambda piece: None  # заменяется в тестах через _wire
    return t


def _chunk() -> np.ndarray:
    """Чанк-линейка: значение сэмпла равно его позиции — мок эмбеддера
    узнаёт кусок по первому сэмплу, а не по длине (куски бывают равные)."""
    return np.arange(CHUNK, dtype=np.float32)


def _wire(t: SegmentTracker, plan: dict[tuple[float, float], np.ndarray]):
    t._diar.plan = [_Seg(a, b) for a, b in plan]

    def emb_for(piece):
        start = float(piece[0]) / SR
        for (a, b), v in plan.items():
            if abs(a - start) < 0.01:
                vv = v / np.linalg.norm(v)
                return vv.astype(np.float32)
        raise AssertionError(f"кусок не из плана: start={start}")
    t._embed = emb_for


V_A = np.array([1.0, 0.0, 0.0])
V_B = np.array([0.0, 1.0, 0.0])
V_C = np.array([0.0, 0.0, 1.0])


def test_two_big_voices_split_into_pieces():
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="mic")
    assert res.pieces is not None and len(res.pieces) == 2
    assert {p.voice for p in res.pieces} == {1, 2}
    assert t.voices == 2


def test_repeat_of_known_voice_is_recognised_not_duplicated():
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    chunk = _chunk()
    t.split(chunk, channel="mic")
    _wire(t, {(0.2, 1.4): V_A})
    res = t.split(chunk, channel="mic")
    assert res.pieces is None and res.main == 1
    assert t.voices == 2  # третий не завёлся


def test_two_pieces_of_one_stranger_make_one_voice():
    """Незнакомец, дважды заговоривший в одном чанке, — один голос, не два."""
    t = _tracker()
    _wire(t, {(0.1, 1.1): V_A, (1.4, 2.4): V_A * 0.9 + 0.1})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert res.pieces is None and res.main == 1  # голос один, чанк покрыт


def test_big_known_plus_foreign_micro_yields_single_window_not_fullchunk():
    """Крупный A + чужое микро: наружу окно A, а НЕ фолбэк целого чанка —
    иначе STT целого чанка подписал бы слова B главному (ревью 15.08)."""
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    t.split(_chunk(), channel="mic")          # выучили A и B
    _wire(t, {(0.1, 1.6): V_A, (1.9, 2.4): V_B})  # B теперь микро (0.5 с)
    res = t.split(_chunk(), channel="mic")
    assert res.pieces is not None and len(res.pieces) == 1
    assert res.pieces[0].voice == 1


def test_deferred_only_chunk_is_skip_not_fullchunk():
    """Только придержанный хвост: [] — не распознавать, а не полный чанк."""
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="mic")           # last = 1
    _wire(t, {(2.5, 2.98): V_A})
    res = t.split(_chunk(), channel="mic")
    assert res.pieces == [] and res.main == 1  # skip, метка не менялась


def test_unknown_tail_at_edge_is_failopen_and_creates_no_voice():
    """Короткий хвост незнакомца у края (< min_new) назначения не получает:
    это не «исключено политикой», а «раскладка ничего не знает» — честный
    fail-open без заведения голоса."""
    t = _tracker()
    _wire(t, {(2.5, 2.98): V_A})
    res = t.split(_chunk(), channel="mic")
    assert res.pieces is None and res.main is None
    assert t.voices == 0


def test_alive_candidate_learns_only_from_windowed_pieces():
    """Центроид нового голоса взвешен длительностью ТОЛЬКО оконных кусков:
    вес равен секундам окна, хвосты и шум в него не входят."""
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A, (2.5, 2.98): V_A})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert res.main == 1
    assert t._counts[0] == pytest.approx(1.2, abs=0.01)  # только 0.1–1.3


def test_dead_candidate_does_not_eat_speaker_slot():
    """Кандидат «≥min_new, но <min_stt» умирает без окна и не должен занять
    последний слот лимита раньше живого крупного (ревью 15.08)."""
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 0.99): V_A,   # кандидат без окна (0.89 с < min_stt)
              (1.2, 2.4): V_B})   # живой крупный
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert res.pieces is not None and res.pieces[0].voice == 1
    assert float(np.dot(t._centroids[0], V_B / np.linalg.norm(V_B))) > 0.99


def test_different_strangers_at_low_similarity_stay_apart():
    """Два незнакомца с похожестью ~0.40 (чужие ≤0.43 по замеру) не клеятся."""
    t = _tracker()
    v2 = V_A * 0.4 + V_B * np.sqrt(1 - 0.16)
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): v2})
    t.split(_chunk(), channel="mic")
    assert t.voices == 2


def test_diar_failure_leaves_centroids_untouched():
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A})
    chunk = _chunk()
    t.split(chunk, channel="mic")
    counts_before = list(t._counts)
    t._diar.fail = True
    res = t.split(chunk, channel="mic")
    assert res.pieces is None and res.main == 1  # last канала, не обучение
    assert t._counts == counts_before


def test_last_is_per_channel():
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A})
    chunk = _chunk()
    t.split(chunk, channel="blackhole")
    t._diar.plan = []  # в микрофоне тишина без сегментов
    res = t.split(chunk, channel="mic")
    assert res.main is None  # чужой last не наследуется


def test_piece_carries_raw_bounds_inside_padded_window():
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="mic")
    for p in res.pieces:
        assert isinstance(p, Piece)
        assert 0 <= p.start <= p.raw_start < p.raw_end <= p.end <= CHUNK


# ---------- находки второго раунда ревью 15.08 ----------

def test_unknown_short_stranger_blocks_fullchunk():
    """Короткий НОВЫЙ незнакомец (voice=None) рядом с крупным известным: наружу
    окно известного, не фолбэк целого чанка — иначе STT съест слова незнакомца
    и подпишет их главному."""
    t = _tracker()
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="mic")           # выучили A
    _wire(t, {(0.1, 1.6): V_A, (1.9, 2.4): V_C})  # C: 0.5 с, < min_new → None
    res = t.split(_chunk(), channel="mic")
    assert res.pieces is not None and len(res.pieces) == 1
    assert res.pieces[0].voice == 1
    assert t.voices == 1  # незнакомец голос не завёл


def test_candidate_rejected_by_limit_blocks_fullchunk():
    """Крупный кандидат, отвергнутый лимитом слотов, — всё ещё чужая речь:
    наружу окно известного голоса, не фолбэк целого чанка."""
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="mic")           # A занял единственный слот
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert res.pieces is not None
    assert [p.voice for p in res.pieces] == [1]


def test_overlapping_raw_segments_split_disputed_zone():
    """Пересекающиеся сегменты двух голосов делят спорную зону пополам:
    STT-окна не пересекаются, один звук не уходит двум авторам."""
    raw = [(0.1, 1.6, 1), (1.4, 2.7, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    assert len(windows) == 2
    (a1, b1, *_), (a2, b2, *_) = windows
    assert b1 <= a2 + 1e-9
    assert b1 == pytest.approx(1.5)  # середина пересечения 1.4–1.6


def test_glue_boundaries_follow_measured_ranges():
    """Границы склейки кандидатов — по замеру: 0.43 врозь, 0.45+ вместе."""
    def stranger_pair(sim):
        t = _tracker()
        v2 = V_A * sim + V_B * float(np.sqrt(1 - sim * sim))
        _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): v2})
        t.split(_chunk(), channel="mic")
        return t.voices

    assert stranger_pair(0.43) == 2   # чужие не клеятся
    assert stranger_pair(0.46) == 1   # свои клеятся


def test_jobs_for_tristate():
    """Стык «раскладка → демон»: пропуск, окна, полный чанк, канальная метка."""
    from diarize_live import SplitResult, jobs_for
    chunk = _chunk()
    assert jobs_for(SplitResult([], 1), chunk) is None          # skip
    assert jobs_for(SplitResult(None, None), chunk) == [(chunk, -1, None)] \
        or jobs_for(SplitResult(None, None), chunk)[0][1] == -1  # канал
    full = jobs_for(SplitResult(None, 2), chunk)
    assert len(full) == 1 and full[0][1] == 2                    # полный чанк
    crash = jobs_for(None, chunk)
    assert crash[0][1] == -1                                     # упавший split
    p = Piece(SR, 2 * SR, 3, SR + 100, 2 * SR - 100)
    win = jobs_for(SplitResult([p], 3), chunk)
    assert win[0][1] == 3 and len(win[0][0]) == SR               # окно
    assert len(win[0][2]) == SR - 200                            # raw для питча


# ---------- находки третьего раунда ревью 15.08 ----------

def test_nested_interjection_does_not_kill_monologue():
    """Короткое чужое «угу» внутри длинного монолога режет его на две части,
    а не схлопывает всё окно (блокер раунда 3)."""
    raw = [(0.1, 2.7, 1), (1.3, 1.7, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    mine = [w for w in windows if w[2] == 1]
    assert len(mine) == 2
    (a1, b1, *_), (a2, b2, *_) = mine
    assert b1 <= 1.3 + 1e-9 and a2 >= 1.7 - 1e-9


def test_nested_interjection_short_leftover_is_dropped():
    """Остаток монолога короче min_stt после разреза окном не становится."""
    raw = [(0.1, 2.7, 1), (0.6, 1.0, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    mine = [w for w in windows if w[2] == 1]
    assert len(mine) == 1 and mine[0][3] >= 1.0 - 1e-9  # осталась правая часть


def test_deferred_stranger_still_bars_padding():
    """Придержанный кусок — барьер для чужого pad: его звук в чанке есть."""
    raw = [(0.5, 2.4, 1), (2.5, 2.98, 2)]
    windows, deferred, _ = plan_pieces(raw, CHUNK, SR)
    assert deferred is True
    a, b, *_ = windows[0]
    assert b <= (2.4 + 2.5) / 2 + 1e-9  # pad не залез в придержанного


def test_raw_bounds_are_trimmed_by_midpoint():
    """После деления спорной зоны сырые границы (для питча) тоже подрезаны."""
    raw = [(0.1, 1.6, 1), (1.4, 2.7, 2)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    (_, b1, _, _, re1), (a2, _, _, rs2, _) = windows
    assert re1 <= b1 + 1e-9 and re1 <= 1.5 + 1e-9
    assert rs2 >= a2 - 1e-9 and rs2 >= 1.5 - 1e-9


def test_new_monologue_with_known_interjection_survives():
    """Блокер раунда 4: новый монолог, разрезанный известной вставкой, жив —
    кандидат материализуется по пересечению с окнами, а не по вложению."""
    t = _tracker()
    _wire(t, {(1.0, 2.2): V_B})
    t.split(_chunk(), channel="mic")          # выучили B
    _wire(t, {(0.1, 2.7): V_A, (1.3, 1.7): V_B})  # новый A с «угу» B внутри
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 2                       # A завёлся
    assert res.pieces, "монолог не должен теряться"
    a_windows = [p for p in res.pieces if p.voice == 2]
    assert len(a_windows) == 2                 # две части вокруг вставки


def test_nested_noise_does_not_collapse_monologue():
    """Вложенный шум (None) внутри монолога не схлопывает его окно двойным
    midpoint-сдвигом (блокер раунда 5): окно живёт, шум остаётся внутри."""
    raw = [(0.1, 2.7, 1), (1.3, 1.7, None)]
    windows, _, _ = plan_pieces(raw, CHUNK, SR)
    assert len(windows) == 1
    a, b, voice, *_ = windows[0]
    assert voice == 1 and b - a > 2.0
