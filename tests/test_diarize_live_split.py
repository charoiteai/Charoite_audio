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


def _tracker(max_speakers: int = 8, mic_channel: str | None = None) -> SegmentTracker:
    t = object.__new__(SegmentTracker)
    if mic_channel is not None:
        t.mic_channel = mic_channel
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


# ---------- №573: у микрофона свои места ----------

V_D = np.array([0.0, 0.0, -1.0])


def _fill_call(t: SegmentTracker, vectors) -> None:
    """Канал собеседников заводит по голосу на чанк, пока квота пускает."""
    for v in vectors:
        _wire(t, {(0.1, 1.3): v})
        t.split(_chunk(), channel="blackhole")


def test_the_mic_keeps_its_own_slots_after_the_call_took_all_of_theirs():
    """Звонок занял все места собеседников — голос микрофона всё равно получает
    номер, и его окно доходит до заданий STT (на микрофоне окно без голоса
    выпадает: №571). Без квоты — 2,7 % речи владельца на записи 10:32."""
    from diarize_live import jobs_for
    t = _tracker(max_speakers=2, mic_channel="mic")
    _fill_call(t, (V_A, V_B))
    assert t.voices == 2
    _wire(t, {(0.1, 1.3): V_C})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 3
    jobs = jobs_for(res, _chunk(), channel_label_neutral=False)
    assert jobs and [n for _piece, n, _raw in jobs] == [3]


def test_the_call_does_not_grow_past_its_quota():
    """Квота микрофона не расширяет места собеседников: третий голос звонка при
    квоте 2 — кандидат без места, даже когда у микрофона места свободны."""
    t = _tracker(max_speakers=2, mic_channel="mic")
    _fill_call(t, (V_A, V_B, V_C))
    assert t.voices == 2
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_D})
    res = t.split(_chunk(), channel="blackhole")
    assert t.voices == 2
    assert [p.voice for p in res.pieces] == [1, None]


def test_the_mic_does_not_grow_past_its_quota():
    t = _tracker(max_speakers=1, mic_channel="mic")
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="mic")
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert [p.voice for p in res.pieces] == [1, None]


def test_echo_in_the_mic_still_lands_on_the_call_voice():
    """Сопоставление идёт по всему списку и с квотой: эхо собеседника в микрофоне
    получает его номер, а не заводит голос микрофона — на этом держится
    эхо-сверка owner_voice по номерам."""
    t = _tracker(max_speakers=2, mic_channel="mic")
    _fill_call(t, (V_A,))
    _wire(t, {(0.1, 1.3): V_A})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1 and res.main == 1


def test_without_the_mic_label_the_limit_is_shared_as_before():
    """Без метки микрофона (бенч, label()) — один общий лимит."""
    t = _tracker(max_speakers=2)
    _fill_call(t, (V_A, V_B))
    _wire(t, {(0.1, 1.3): V_C})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 2
    assert res.pieces is not None and [p.voice for p in res.pieces] == [None]


def test_mic_founded_voices_are_per_tracker():
    """Множество голосов микрофона — своё у каждого трекера (значение по умолчанию
    на классе неизменяемое)."""
    a, b = _tracker(max_speakers=2, mic_channel="mic"), _tracker(max_speakers=2, mic_channel="mic")
    _wire(a, {(0.1, 1.3): V_A})
    a.split(_chunk(), channel="mic")
    assert a._mic_founded == {0} and b._mic_founded == frozenset()


def test_tracker_numbers_stay_below_stream_labels(monkeypatch):
    """Номера трекера с квотой — до 2·max_speakers; метки потока начинаются с
    STREAM_VOICE_BASE, и встреча номеров спутала бы имена голосов."""
    import diarize_live
    import types
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.SimpleNamespace())
    with pytest.raises(ValueError):
        diarize_live.SegmentTracker(pathlib.Path("seg"), pathlib.Path("emb"),
                                    max_speakers=diarize_live.STREAM_VOICE_BASE // 2)


def test_candidate_rejected_by_limit_blocks_fullchunk():
    """Крупный кандидат, отвергнутый лимитом слотов, — всё ещё чужая речь:
    не фолбэк целого чанка, а своё окно без голоса (№571: раньше окно
    выбрасывалось вместе со словами)."""
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="mic")           # A занял единственный слот
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="mic")
    assert t.voices == 1
    assert res.pieces is not None
    assert [p.voice for p in res.pieces] == [1, None]


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
    for neutral in (True, False):   # ветки без куска без голоса канал не различают
        kw = {"channel_label_neutral": neutral}
        assert jobs_for(SplitResult([], 1), chunk, **kw) is None     # skip
        channel = jobs_for(SplitResult(None, None), chunk, **kw)
        assert len(channel) == 1 and channel[0][1] == -1             # канал
        full = jobs_for(SplitResult(None, 2), chunk, **kw)
        assert len(full) == 1 and full[0][1] == 2                    # полный чанк
        crash = jobs_for(None, chunk, **kw)
        assert crash[0][1] == -1                                     # упавший split
        p = Piece(SR, 2 * SR, 3, SR + 100, 2 * SR - 100)
        win = jobs_for(SplitResult([p], 3), chunk, **kw)
        assert win[0][1] == 3 and len(win[0][0]) == SR               # окно
        assert len(win[0][2]) == SR - 200                            # raw для питча


def test_jobs_for_voiceless_piece_follows_channel():
    """№571: кусок без голоса (кандидат без места) распознаётся под меткой
    канала там, где она нейтральна, и выпадает на микрофоне — там метка
    канала есть подпись владельца (круг 1 по №571)."""
    from diarize_live import SplitResult, jobs_for
    from stt_runtime import CHANNEL_LABEL_ONLY
    chunk = _chunk()
    known = Piece(0, SR, 2, 0, SR)
    stranger = Piece(SR + SR // 2, 3 * SR, None, SR + SR // 2, 3 * SR)
    both = SplitResult([known, stranger], 2)
    heard = jobs_for(both, chunk, channel_label_neutral=True)
    assert [n for _p, n, _r in heard] == [2, CHANNEL_LABEL_ONLY]
    assert len(heard[1][0]) == 3 * SR - (SR + SR // 2)
    mic = jobs_for(both, chunk, channel_label_neutral=False)
    assert [n for _p, n, _r in mic] == [2]
    alone = SplitResult([stranger], None)
    assert jobs_for(alone, chunk, channel_label_neutral=False) is None
    only = jobs_for(alone, chunk, channel_label_neutral=True)
    assert [n for _p, n, _r in only] == [CHANNEL_LABEL_ONLY]
    assert len(only[0][0]) < CHUNK          # окно, не целый чанк


def _voices(res):
    return [p.voice for p in res.pieces] if res.pieces else res.pieces


def test_third_voice_words_reach_stt_when_slots_are_full():
    """Опровергающий опыт №571 на синтетике: два места, три голоса. Слова
    третьего обязаны попасть в задания STT канала собеседников — раньше чанк
    уходил в пропуск целиком."""
    from diarize_live import jobs_for
    from stt_runtime import CHANNEL_LABEL_ONLY
    t = _tracker(max_speakers=2)
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    t.split(_chunk(), channel="blackhole")          # A и B заняли оба места
    assert t.voices == 2
    _wire(t, {(0.2, 2.2): V_C})                     # говорит только третий
    res = t.split(_chunk(), channel="blackhole")
    assert t.voices == 2                            # места не прибавилось
    assert res.pieces is not None and res.pieces != []
    assert _voices(res) == [None]
    jobs = jobs_for(res, _chunk(), channel_label_neutral=True)
    assert jobs is not None
    assert [n for _p, n, _r in jobs] == [CHANNEL_LABEL_ONLY]
    piece, _n, _raw = jobs[0]
    # окно накрывает речь третьего голоса, а не весь чанк
    assert piece[0] <= 0.2 * SR and piece[-1] >= 2.2 * SR - 1
    assert len(piece) < CHUNK
    # на микрофоне тот же чанк — пропуск, как до №571
    assert jobs_for(res, _chunk(), channel_label_neutral=False) is None


def test_voiceless_chunk_is_never_whole_chunk_under_main():
    """Чанк из одного окна без места не уходит целиком под прошлый голос
    канала (запрещённый фолбэк 15.08): кусок, а не pieces is None."""
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 2.9): V_A})
    t.split(_chunk(), channel="blackhole")          # last канала = 1
    _wire(t, {(0.3, 2.0): V_B})
    res = t.split(_chunk(), channel="blackhole")
    assert res.pieces is not None and _voices(res) == [None]
    assert res.main == 1                            # метка канала не менялась


def test_no_diarize_job_carries_none_or_zero_voice():
    """Голос задания — CHANNEL_LABEL_ONLY или 1..N: None демон читает как
    «спроси трекер ещё раз» (учит центроиды тем же звуком дважды), 0 —
    валидный голос, в который упирается ловушка -1 + 1."""
    from diarize_live import jobs_for
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 1.3): V_A})
    t.split(_chunk(), channel="blackhole")
    _wire(t, {(0.1, 1.3): V_A, (1.5, 2.7): V_B})
    res = t.split(_chunk(), channel="blackhole")
    for neutral in (True, False):
        for _piece, n, _raw in jobs_for(res, _chunk(), channel_label_neutral=neutral) or []:
            assert n is not None and n != 0


def test_empty_plan_only_for_hold_or_micro_never_for_missing_slot():
    """Пустой план (пропуск чанка) — только когда вся речь придержана или
    микро-куски; кандидат без места на канале собеседников пустого плана не
    даёт никогда (решение по кругу 2 №571 вместо поля причины)."""
    from diarize_live import jobs_for
    t = _tracker(max_speakers=1)
    _wire(t, {(0.1, 2.9): V_A})
    t.split(_chunk(), channel="blackhole")
    _wire(t, {(0.3, 2.0): V_B})                     # кандидат без места
    res = t.split(_chunk(), channel="blackhole")
    assert res.pieces != []
    assert jobs_for(res, _chunk(), channel_label_neutral=True) is not None
    _wire(t, {(2.5, 2.98): V_A})                    # только придержка
    assert t.split(_chunk(), channel="blackhole").pieces == []
    _wire(t, {(0.5, 1.2): V_A})                     # только микро-кусок (< min_stt)
    assert t.split(_chunk(), channel="blackhole").pieces == []


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


# ---------- поток Nemotron (№478 B): тот же итог раскладки, другой источник голоса ----------

from diarize_live import (STREAM_VOICE_BASE, SplitResult, StreamVoices,  # noqa: E402
                          recon_shares, stream_layout, stream_split, tracker_spans,
                          tracker_speech, uncovered, with_recon)
from diarize_live import jobs_for  # noqa: E402
from stt_runtime import CHANNEL_LABEL_ONLY  # noqa: E402

KNOWN = {1: V_A, 2: V_B, 3: V_C}

#: Одна таблица случаев для трекера и потока: (сегменты (начало, конец, голос) в секундах).
#: Все голоса известны трекеру, все куски не короче min_segment — вход обоих одинаков.
TWIN_CASES = [
    [(0.1, 1.3, 1), (1.5, 2.7, 2)],              # два голоса — окна
    [(0.2, 2.2, 1)],                             # один голос на весь чанк — целиком
    [(2.55, 2.99, 1)],                           # придержанный хвост — не распознавать
    [(0.1, 1.6, 1), (1.9, 2.4, 2)],              # микро-кусок второго — окна, не целиком
    [(0.1, 0.8, 1)],                             # короче min_stt — исключено
    [(0.1, 1.4, 1), (1.5, 2.4, 1), (2.55, 2.99, 2)],  # один голос + придержка другого
    [(0.1, 1.2, 2), (1.3, 2.4, 1)],              # равные доли: главный — кто раньше
    [(0.1, 2.4, 1), (0.9, 1.4, 3)],              # вложенный чужак
]


@pytest.mark.parametrize("raw", TWIN_CASES)
def test_the_stream_split_settles_like_the_tracker_on_one_table(raw):
    """Близнец: хвост `split` и `stream_split` — один контракт `settle`; на одном входе итог
    совпадает (окна, голоса, главный), расхождение копий ловится здесь, а не на встрече."""
    t = _tracker()
    t._centroids = [KNOWN[v] / np.linalg.norm(KNOWN[v]) for v in (1, 2, 3)]
    t._counts = [10.0, 10.0, 10.0]
    _wire(t, {(s, e): KNOWN[v] for s, e, v in raw})
    got_tracker = t.split(_chunk(), channel="bh")
    got_stream = stream_split([(int(s * SR) / SR, int(e * SR) / SR, v) for s, e, v in raw],
                              CHUNK, SR, step_s=2.5)
    assert got_stream == got_tracker


def test_the_stream_split_of_one_covering_voice_is_the_whole_chunk_under_it():
    res = stream_split([(0.2, 2.2, STREAM_VOICE_BASE)], CHUNK, SR, step_s=2.5)
    assert res == SplitResult(None, STREAM_VOICE_BASE)


def _sv():
    return StreamVoices(sr=SR, gap_s=60.0)


def _plan(sv, segs, *, tracker=SplitResult(None, 1), tracker_jobs=None, origin=0):
    chunk = _chunk()
    jobs = tracker_jobs if tracker_jobs is not None else [(chunk, 1, chunk)]
    return sv.plan(segs, origin=origin, chunk=chunk, tracker=tracker, tracker_jobs=jobs,
                   neutral=True, step_s=2.5)


def test_a_stream_slot_keeps_its_label_while_it_keeps_talking():
    sv = _sv()
    a, _ = _plan(sv, [(0, int(2.2 * SR), 0)])
    b, _ = _plan(sv, [(10 * SR, int(12.2 * SR), 0)], origin=10 * SR)
    assert a[0][1] == b[0][1] == STREAM_VOICE_BASE


def test_a_slot_silent_longer_than_the_gap_gets_a_new_label():
    """Движок отдал слот другому человеку: имя прежнего не наследуется (r2 Sonnet I1)."""
    sv = _sv()
    a, _ = _plan(sv, [(0, int(2.2 * SR), 0)])
    b, _ = _plan(sv, [(int(62.3 * SR), int(64.5 * SR), 0)], origin=int(62.3 * SR))
    c, _ = _plan(sv, [(int(66 * SR), int(68.2 * SR), 0)], origin=int(66 * SR))
    assert (a[0][1], b[0][1], c[0][1]) == (STREAM_VOICE_BASE, STREAM_VOICE_BASE + 1, STREAM_VOICE_BASE + 1)


def test_a_gap_of_exactly_the_threshold_keeps_the_label():
    sv = _sv()
    _plan(sv, [(0, 2 * SR, 0)])
    b, _ = _plan(sv, [(62 * SR, 64 * SR, 0)], origin=62 * SR)
    assert b[0][1] == STREAM_VOICE_BASE


def test_stream_pieces_carry_the_tracker_voice_that_overlaps_them_most_for_the_echo_check():
    sv = _sv()
    tracker = SplitResult([Piece(0, SR, 4, 0, SR), Piece(SR, 3 * SR, 2, SR, 3 * SR)], 2)
    jobs, fields = _plan(sv, [(0, int(2.9 * SR), 5)], tracker=tracker)
    ((piece, label, _raw, shares),) = jobs
    assert label == STREAM_VOICE_BASE
    assert [v for v, _ in shares] == [2, 4], "каждый голос трекера в куске получает свою долю (I3)"
    assert shares[0][1] == pytest.approx(2 / 3, abs=1e-3) and shares[1][1] == pytest.approx(1 / 3, abs=1e-3), "чанк целиком: доли от чанка"
    assert {k: fields[k] for k in ("source", "pieces", "no_recon", "recon_agree")} == {
        "source": "stream", "pieces": 1, "no_recon": 0, "recon_agree": 0}
    assert fields["lost_s"] == 0, "чанк целиком покрывает и 0,1 с за окном потока"
    jobs, fields = _plan(sv, [(0, int(2.9 * SR), 5)], tracker=tracker)
    assert fields["recon_agree"] == 1, "тот же голос трекера уже связан с той же меткой"


@pytest.mark.parametrize("tracker", [None, SplitResult([], 1), SplitResult(None, None),
                                     SplitResult([Piece(0, SR, None, 0, SR)], None)])
def test_a_stream_piece_without_a_tracker_voice_does_not_feed_the_echo_check(tracker):
    sv = _sv()
    jobs, fields = _plan(sv, [(0, int(2.2 * SR), 0)], tracker=tracker)
    assert jobs[0][3] == () and fields["no_recon"] == 1


def test_a_whole_chunk_tracker_result_gives_its_main_voice_as_the_recon_number():
    assert tracker_spans(SplitResult(None, 3), CHUNK) == [(0, CHUNK, 3)]
    assert recon_shares([(0, 10, 1), (5, 30, 2)], 0, 20) == ((2, 0.75), (1, 0.5))
    assert recon_shares([(0, 10, 1), (10, 20, 2)], 0, 20) == ((1, 0.5), (2, 0.5)), "при равенстве — первый"
    assert recon_shares([(0, 10, 1)], 10, 20) == (), "касание — не пересечение"


def test_a_tracker_fallback_names_pieces_by_the_linked_stream_label():
    """Поток не успел: кусок трекера подписывается меткой потока, связанной с его голосом, —
    иначе тот же человек на соседнем куске получил бы второе имя (r2 Sonnet I5)."""
    sv = _sv()
    _plan(sv, [(0, int(2.2 * SR), 0)], tracker=SplitResult(None, 2))
    chunk = _chunk()
    jobs, fields = sv.plan(None, origin=0, chunk=chunk, tracker=None,
                           tracker_jobs=[(chunk, 2, chunk), (chunk, 5, chunk),
                                         (chunk, CHANNEL_LABEL_ONLY, None)],
                           neutral=True, step_s=2.5)
    assert [(j[1], j[3]) for j in jobs] == [(STREAM_VOICE_BASE, ((2, 1.0),)), (5, ((5, 1.0),)),
                                           (CHANNEL_LABEL_ONLY, ())]
    assert fields == {"source": "tracker"}


def test_a_labelled_chunk_where_the_stream_hears_nothing_goes_to_the_tracker():
    jobs, fields = _plan(_sv(), [])
    assert [(j[1], j[3]) for j in jobs] == [(1, ((1, 1.0),))], "задания трекера, один голос на подпись и сверку"
    assert fields == {"source": "tracker", "fallback": "no_speech"}


def test_a_stream_chunk_with_all_speech_held_back_is_skipped_only_if_the_tracker_held_it_too():
    """Придержанный хвост потока принесёт следующий чанк; но если трекер тот же звук
    распознал бы сейчас, он и распознаётся — покрытие `on` не уже покрытия `off`."""
    jobs, fields = _plan(_sv(), [(int(2.55 * SR), int(2.99 * SR), 0)], tracker=SplitResult([], 1))
    assert jobs is None and fields["source"] == "stream" and fields["pieces"] == 0
    jobs, fields = _plan(_sv(), [(int(2.55 * SR), int(2.99 * SR), 0)], tracker=SplitResult(None, 1))
    ((piece, label, _raw, _shares),) = jobs
    assert len(piece) == CHUNK and label == 1 and fields["tracker_pieces"] == 1


def test_stream_segments_are_clipped_to_the_chunk():
    sv = _sv()
    jobs, _ = _plan(sv, [(5 * SR, 12 * SR, 0), (20 * SR, 21 * SR, 1)], origin=10 * SR,
                    tracker=SplitResult([Piece(0, 2 * SR, 1, 0, 2 * SR)], 1))
    ((piece, label, raw, _recon),) = jobs
    assert len(piece) == CHUNK and label == STREAM_VOICE_BASE, "сегмент за чанком не заводит метку"


def test_tracker_jobs_keep_one_number_for_label_and_recon():
    chunk = _chunk()
    assert with_recon(None) is None
    assert with_recon([(chunk, 3, None), (chunk, CHANNEL_LABEL_ONLY, None)]) == [
        (chunk, 3, None, ((3, 1.0),)), (chunk, CHANNEL_LABEL_ONLY, None, ())]


def test_speech_the_tracker_left_without_a_voice_gets_a_stream_label_not_the_channel_label():
    """Кусок кандидата без места (№571) в режиме `on` подписывается меткой потока: под голой
    меткой канала подряд идущие такие куски склеились бы в абзац одного «Собеседника», хотя
    говорили разные люди (advisory выходного круга №571)."""
    sv = _sv()
    chunk = _chunk()
    tracker = SplitResult([Piece(0, CHUNK, None, 0, CHUNK)], None)
    tracker_jobs = [(chunk, CHANNEL_LABEL_ONLY, chunk)]
    jobs, _ = sv.plan([(0, int(1.4 * SR), 0), (int(1.5 * SR), int(2.7 * SR), 1)], origin=0, chunk=chunk,
                      tracker=tracker, tracker_jobs=tracker_jobs, neutral=True, step_s=2.5)
    assert [j[1] for j in jobs] == [STREAM_VOICE_BASE, STREAM_VOICE_BASE + 1]
    assert all(j[3] == () for j in jobs), "голоса трекера нет — сверку не кормит"


def test_speech_the_stream_did_not_label_goes_to_the_tracker_not_under_the_stream_label():
    """Поток разметил одну реплику, а трекер слышит ещё одну: чанк не распознаётся целиком под
    меткой первого — слова второго ушли бы ему (выходной круг 1 №478 B, I2), — и вторая
    реплика не теряется, а идёт своим заданием трекера (выходной круг 2, I1)."""
    tracker = SplitResult([Piece(0, int(1.4 * SR), 1, 0, int(1.4 * SR)),
                           Piece(int(1.6 * SR), int(2.8 * SR), None, int(1.6 * SR), int(2.8 * SR))], 1)
    jobs, fields = _plan(_sv(), [(0, int(1.4 * SR), 0)], tracker=tracker)
    (first, second) = jobs
    assert first[1] == STREAM_VOICE_BASE and len(first[0]) < CHUNK, "окно реплики, а не чанк целиком"
    assert second[1] == CHANNEL_LABEL_ONLY and len(second[0]) == int(1.2 * SR) and second[3] == ()
    assert fields["pieces"] == 1 and fields["tracker_pieces"] == 1
    whole = stream_split([(0.0, 1.4, STREAM_VOICE_BASE)], CHUNK, SR, step_s=2.5)
    assert whole.pieces is None, "без речи мимо потока тот же вход — чанк целиком"


def test_tracker_speech_is_what_the_tracker_alone_would_transcribe_in_every_state():
    chunk = _chunk()
    for res, neutral in [(None, True), (SplitResult(None, 3), True), (SplitResult(None, None), True),
                         (SplitResult([], 1), True),
                         (SplitResult([Piece(0, SR, 2, 100, SR - 100), Piece(SR, 2 * SR, None, SR, 2 * SR)], 2), True),
                         (SplitResult([Piece(0, SR, 2, 100, SR - 100), Piece(SR, 2 * SR, None, SR, 2 * SR)], 2), False)]:
        jobs = jobs_for(res, chunk, channel_label_neutral=neutral) or []
        speech = tracker_speech(res, CHUNK, neutral=neutral)
        assert len(speech) == len(jobs), "одно правило с jobs_for: столько же отрезков, сколько заданий"
        for (start, end, voice), (_piece, n, _raw) in zip(speech, jobs):
            assert (voice if voice is not None else CHANNEL_LABEL_ONLY) == n
    assert tracker_speech(SplitResult(None, 3), CHUNK, neutral=True) == [(0, CHUNK, 3)]
    assert tracker_speech(SplitResult([], 1), CHUNK, neutral=True) == []


def test_uncovered_speech_splits_into_parts_and_lost_samples():
    speech = [(0, 2 * SR, 1)]
    half = SR // 2
    assert uncovered(speech, [(0, int(1.6 * SR))], half) == ([], int(0.4 * SR))
    assert uncovered(speech, [(0, int(1.5 * SR))], half) == ([(int(1.5 * SR), 2 * SR, 1)], 0)
    assert uncovered(speech, [(int(0.5 * SR), SR)], half) == ([(0, half, 1), (SR, 2 * SR, 1)], 0)
    assert uncovered(speech, [(0, SR), (int(0.8 * SR), int(1.9 * SR))], half) == ([], int(0.1 * SR)), (
        "перекрытые покрытия покрывают вместе")
    assert uncovered([], [(0, SR)], half) == ([], 0)


def test_a_tracker_voice_touching_a_stream_piece_is_not_its_share():
    assert recon_shares([(0, 5, 1), (5, 100, 2)], 0, 100) == ((2, 0.95),), "5 % куска — касание, не голос"


def test_a_stream_segment_wider_than_the_chunk_keeps_its_label_in_the_next_chunk():
    sv = _sv()
    a, _ = _plan(sv, [(-SR, 4 * SR, 0)])                               # шире чанка с обеих сторон
    b, _ = _plan(sv, [(-SR, 7 * SR, 0)], origin=int(2.5 * SR))        # тот же человек дальше
    assert a[0][1] == b[0][1] == STREAM_VOICE_BASE


def test_a_stream_window_does_not_pad_into_speech_a_tracker_job_transcribes():
    """Трекер слышит [0; 2,0], поток разметил [0; 1,0]: звук 1,0–2,0 уходит заданием трекера,
    и окно потока не расширяется в него запасом — иначе 1,0–1,25 с распознавалось бы дважды
    под разными людьми (выходной круг GLM по №478 B, I2)."""
    tracker = SplitResult([Piece(0, 2 * SR, 1, 0, 2 * SR)], 1)
    jobs, _ = _plan(_sv(), [(0, SR, 0)], tracker=tracker)
    (stream_job, tracker_job) = jobs
    assert stream_job[1] == STREAM_VOICE_BASE and len(stream_job[0]) == SR, "запас окна потока — не дальше 1,0 с"
    assert tracker_job[1] == STREAM_VOICE_BASE and len(tracker_job[0]) == SR, (
        "голос трекера уже связан с меткой потока этого же куска — та же подпись")
    alone = stream_split([(0.0, 1.0, STREAM_VOICE_BASE)], CHUNK, SR, step_s=2.5, unknown_speech=True)
    assert alone.pieces[0].end > SR, "без барьера тот же вход падит окно за 1,0 с"


# ---------- сохранение речи режима `on` (финальный Opus по №478 B, C1) ----------

def _covered_by_jobs(res, extra, n):
    """Звук всего, что уйдёт в STT: окна потока с запасом (или чанк целиком) и задания трекера."""
    stream = ([(0, n)] if res.pieces is None else [(p.start, p.end) for p in res.pieces]) \
        if (jobs_for(res, np.zeros(n, dtype=np.float32), channel_label_neutral=True) is not None) else []
    return stream + [(a, b) for a, b, _v in extra]


@pytest.mark.parametrize("tracker", [
    SplitResult(None, 1),                                                           # (а) чанк целиком
    SplitResult([Piece(int(0.2 * SR), int(1.5 * SR), 1, int(0.2 * SR), int(1.5 * SR))], 1),  # (б)
])
def test_a_micro_stream_segment_does_not_swallow_speech_the_tracker_would_transcribe(tracker):
    """Воспроизведения финального Opus: поток разметил 0,2–0,9 с (короче окна STT) — звук
    0,2–0,9 с обязан попасть в задание, как попал бы в режиме `off`."""
    jobs, fields = _plan(_sv(), [(int(0.2 * SR), int(0.9 * SR), 0)], tracker=tracker)
    assert jobs is not None, "чанк не пропадает целиком"
    speech = tracker_speech(tracker, CHUNK, neutral=True)
    total = sum(e - s for s, e, _v in speech)
    assert sum(len(j[2]) for j in jobs if j[2] is not None) >= total - int(UNLABELLED * SR)
    assert fields["tracker_pieces"] >= 1 and fields["lost_s"] == 0


UNLABELLED = 0.4


def _random_case(rng):
    n = CHUNK
    kind = rng.integers(0, 5)
    if kind == 0:
        tracker = SplitResult(None, int(rng.integers(1, 4)))
    elif kind == 1:
        tracker = None
    elif kind == 2:
        tracker = SplitResult([], 1)
    else:
        pieces, t = [], int(rng.integers(0, SR // 2))
        while t < n - SR // 4:
            length = int(rng.integers(SR // 4, SR * 2))
            end = min(n, t + length)
            voice = None if rng.random() < 0.2 else int(rng.integers(1, 4))
            pieces.append(Piece(t, end, voice, t, end))
            t = end + int(rng.integers(0, SR // 2))
        tracker = SplitResult(pieces, 1)
    segs, t = [], int(rng.integers(0, SR))
    while t < n:
        length = int(rng.integers(SR // 5, SR * 2))
        segs.append((t, min(n + SR, t + length), int(rng.integers(0, 3))))
        t += length + int(rng.integers(0, SR))
    return tracker, segs


def test_stream_layout_keeps_every_sample_of_tracker_speech_in_exactly_one_job():
    """Свойство на сгенерированных раскладках трекера и потока: речь трекера (что распознал
    бы `off`) целиком лежит в заданиях, кроме остатков короче порога — они и есть `lost_s`;
    звук задания трекера не распознаётся ещё и окном потока (ни потери, ни дубля)."""
    rng = np.random.default_rng(478)
    for _ in range(400):
        tracker, segs = _random_case(rng)
        neutral = bool(rng.random() < 0.7)
        sv = _sv()
        speech = tracker_speech(tracker, CHUNK, neutral=neutral)
        bounded = tracker is not None and tracker.pieces is not None
        raw = [(s / SR, min(e, CHUNK) / SR, sv._label(slot, s, min(e, CHUNK))) for s, e, slot in segs
               if min(e, CHUNK) > s]
        res, extra, lost = stream_layout(raw, speech, CHUNK, SR, step_s=2.5, bounded=bounded)
        windows = plan_pieces(sorted(raw), CHUNK, SR, step_s=2.5)[0]
        if not bounded:
            if windows:
                assert extra == [] and lost == 0, "без границ речи трекера окна потока решают сами"
            else:
                assert [(a, b) for a, b, _v in extra] == [(0, CHUNK)], "окон нет — чанк целиком трекеру"
            continue
        covered = _covered_by_jobs(res, extra, CHUNK)
        rest, _ = uncovered(speech, covered, 1)
        assert all(e - st < UNLABELLED * SR for st, e, _v in rest), "речь трекера длиннее порога вне заданий"
        assert sum(e - st for st, e, _v in rest) == lost, "всё непокрытое — ровно lost_s"
        if extra:
            assert res.pieces is not None, "рядом с заданием трекера чанк не идёт целиком под меткой потока"
        stt = [] if not res.pieces else [(pc.start, pc.end) for pc in res.pieces]
        for a2, b2, _v in extra:
            for s2, e2 in stt:
                assert min(b2, e2) - max(a2, s2) <= 0, "звук задания трекера распознаётся ещё и окном потока"


def test_a_tracker_edge_shorter_than_the_threshold_is_counted_as_lost():
    """Два голоса потока — чанк идёт окнами; край речи трекера за окнами короче порога
    не распознаётся отдельно и виден полем lost_s."""
    tracker = SplitResult([Piece(0, 3 * SR, 1, 0, 3 * SR)], 1)
    jobs, fields = _plan(_sv(), [(int(0.35 * SR), int(1.4 * SR), 0), (int(1.6 * SR), int(2.7 * SR), 1)],
                         tracker=tracker)
    assert [j[1] for j in jobs] == [STREAM_VOICE_BASE, STREAM_VOICE_BASE + 1]
    assert fields["lost_s"] == pytest.approx(0.1 + 0.05, abs=0.002), "за запасом окон: 0–0,1 с и 2,95–3,0 с"
    assert "tracker_pieces" not in fields


def test_a_whole_chunk_tracker_result_does_not_split_off_the_tail_of_a_stream_window():
    """Чанк трекера целиком границ речи не несёт: окно потока распознаёт чанк одним заданием,
    а хвост и тишина не уходят отдельными кусками короче секунды (круг проверки правок Opus,
    Sonnet I1)."""
    jobs, fields = _plan(_sv(), [(0, int(2.2 * SR), 0)], tracker=SplitResult(None, 1))
    ((piece, label, _raw, _shares),) = jobs
    assert len(piece) == CHUNK and label == STREAM_VOICE_BASE and "tracker_pieces" not in fields
    jobs, fields = _plan(_sv(), [(0, int(1.2 * SR), 0), (int(1.8 * SR), int(3.0 * SR), 0)], tracker=None)
    assert "tracker_pieces" not in fields, "пауза в речи одного голоса — не задание трекера (GLM M2)"


def test_lost_speech_is_measured_against_the_padded_windows_that_reach_stt():
    """Запас окна несёт звук: речь трекера под запасом не потеряна (Sonnet I2)."""
    tracker = SplitResult([Piece(0, 3 * SR, 1, 0, 3 * SR)], 1)
    _jobs, fields = _plan(_sv(), [(int(0.1 * SR), int(1.3 * SR), 0), (int(1.5 * SR), int(2.7 * SR), 1)],
                          tracker=tracker)
    assert fields["lost_s"] <= 0.3 + 1e-3, "0,1 с до первого окна — под его запасом"


def test_the_daemon_builds_its_tracker_with_the_mic_label():
    """Квота мест микрофона держится на сборке трекера в демоне: фабрика live_tracker
    с меткой микрофона из ChannelLabels, собранных раньше трекера (иначе NameError
    уйдёт в широкий except диаризации и выключит её целиком). Демон — одна функция
    main() на тысячи строк, поэтому проверка по AST его текста (выход r1 по №573)."""
    import ast
    tree = ast.parse((REPO / "src" / "daemon.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = [getattr(c.func, "id", getattr(c.func, "attr", None)) for c in calls]
    assert "SegmentTracker" not in names, "трекер живой встречи — только фабрикой live_tracker"
    made = [c for c, name in zip(calls, names) if name == "live_tracker"]
    assert len(made) == 1
    mic = {k.arg: ast.unparse(k.value) for k in made[0].keywords}.get("mic_channel")
    assert mic == "chan.mic_raw"
    chan_at = min(n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assign)
                  and "ChannelLabels.from_capture" in ast.unparse(n.value))
    assert chan_at < made[0].lineno, "ChannelLabels собираются раньше трекера"
