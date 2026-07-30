"""Живая диаризация сваливала всех в один голос — и это измерено.

Бенч на синтетическом диалоге из четырёх голосов (32 с) показал у живого
трекера DER 0.725 при **одном** найденном голосе: три четверти времени
разговора подписаны неверно. Порог тут ни при чём — от 0.25 до 0.55 результат
не меняется.

Причина в устройстве. Речь режется по таймеру: чанк три секунды, эмбеддинг
считается со всего чанка. На границе реплик в один чанк попадает конец фразы
одного человека и начало фразы другого, эмбеддинг выходит смешанным, косинус
ко всем центроидам получается средним — и трекер залипает на первом голосе.

Лечится не порогом, а границами: та же модель сегментации, что уже работает в
проходе после встречи, находит внутри чанка куски речи, и эмбеддинг считается
по КУСКУ, а не по трём секундам вперемешку. На той же фикстуре это даёт
DER 0.246 и все четыре голоса.

Контракт трекера сохранён: `label(chunk)` возвращает номер голоса или None,
демон не переписывается. Метка на чанк остаётся одна — по говорящему, который
занял в нём больше времени, потому что и текст у чанка один.
"""
import pathlib
import sys

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import diarize_live  # noqa: E402

SEG = REPO / "models" / "diar" / "segmentation.onnx"
EMB = REPO / "models" / "diar" / "embedding.onnx"
FIXTURE = REPO / "data" / "diar_bench"

needs_models = pytest.mark.skipif(
    not (SEG.exists() and EMB.exists()),
    reason="нет моделей — scripts/get_models.py --diar --segmentation")


def test_tracker_choice_prefers_segments_when_the_model_is_there(tmp_path):
    """Есть сегментация — работаем по кускам речи; нет — старый режим."""
    seg, emb = tmp_path / "segmentation.onnx", tmp_path / "embedding.onnx"
    assert diarize_live.tracker_kind(seg, emb) is None, "без моделей трекера нет"
    emb.write_bytes(b"x")
    assert diarize_live.tracker_kind(seg, emb) == "chunks", \
        "без модели сегментации остаётся прежний режим по чанкам"
    seg.write_bytes(b"x")
    assert diarize_live.tracker_kind(seg, emb) == "segments"


def test_note_explains_the_weaker_mode(tmp_path):
    """Упрощённый режим — не молча: человек должен знать, что метки слабее."""
    emb = tmp_path / "embedding.onnx"
    emb.write_bytes(b"x")
    note = diarize_live.availability_note(True, emb, tmp_path / "segmentation.onnx")
    assert note and "segmentation" in note, note
    assert diarize_live.availability_note(True, emb, emb) is None, \
        "обе модели на месте — сообщать не о чем"


@needs_models
def test_segment_tracker_keeps_the_tracker_contract():
    tracker = diarize_live.SegmentTracker(SEG, EMB, sample_rate=16000)
    rng = np.random.default_rng(0)
    assert tracker.label(rng.normal(0, 0.01, 16000).astype(np.float32)) in (None, 1)
    assert tracker.voices >= 0


@needs_models
@pytest.mark.skipif(not (FIXTURE / "truth.json").exists(),
                    reason="нет фикстуры — scripts/diar_bench.py --make")
def test_live_diarization_beats_the_old_tracker_on_the_fixture():
    """Главный тест: на том же диалоге ошибок должно стать заметно меньше.

    Порог 0.4 — с большим запасом: измеренное значение 0.246 против 0.725 у
    прежнего трекера. Если правка порогов или модели уронит качество обратно,
    это будет видно здесь, а не на живой встрече.
    """
    import json

    import soundfile as sf

    sys.path.insert(0, str(REPO / "scripts"))
    import diar_bench

    data = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
    wav = FIXTURE / data["audio"]
    audio, sr = sf.read(wav, dtype="float32")

    tracker = diarize_live.SegmentTracker(SEG, EMB, sample_rate=sr)
    step, hyp = int(3.0 * sr), []
    for i in range(0, len(audio), step):
        chunk = audio[i:i + step]
        if len(chunk) < int(0.5 * sr):
            break
        n = tracker.label(chunk)
        if n is not None:
            hyp.append({"start": i / sr, "end": (i + len(chunk)) / sr,
                        "speaker": f"voice{n}"})

    scores = diar_bench.der(data["segments"], hyp, len(audio) / sr)
    assert scores["der"] < 0.4, f"живая диаризация деградировала: {scores}"
    assert scores["speakers_hyp"] >= 3, \
        f"снова сваливает голоса в один: нашёл {scores['speakers_hyp']} из 4"
