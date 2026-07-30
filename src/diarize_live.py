"""Живая диаризация собеседников: несколько голосов в одном BlackHole-канале.

На каждый речевой чанк канала — ERes2Net-эмбеддинг (модель уже в models/diar,
~20-50мс на CPU) → косинус к центроидам известных голосов: похож — тот же
голос (центроид дообучается), нет — новый. Стенограмма получает метки
«Собеседник 1/2/3» — абзацы по говорящим вместо слитной каши.

Консервативно: короткий/тихий чанк или неуверенность → None, демон оставляет
общую метку «Собеседник» — хуже текущего поведения не становится. Имена
голосам сопоставляет оффлайн-диаризация после встречи (*_спикеры.md).
"""
from __future__ import annotations

import pathlib

import numpy as np


def tracker_kind(seg_model: pathlib.Path, emb_model: pathlib.Path) -> str | None:
    """Каким трекером работать: «segments», «chunks» или никаким.

    Сегментация меняет качество разительно (замер на синтетическом диалоге:
    DER 0.246 против 0.725 и четыре голоса против одного), но без неё режим по
    чанкам остаётся рабочим — просто слабее. Поэтому выбор, а не отказ.
    """
    if not emb_model.exists():
        return None
    return "segments" if seg_model.exists() else "chunks"


def availability_note(enabled: bool, model_path: pathlib.Path,
                      seg_path: pathlib.Path | None = None) -> str | None:
    """Что сказать пользователю про живую диаризацию. None — она работает.

    Модели в поставку не входят, а `live_diarize` включён по умолчанию:
    «модели нет» — это состояние сразу после установки, а не авария. Молча
    отдать метки по каналам вместо обещанных «Собеседник 1/2/…» хуже, чем
    сказать вслух: человек видит слитную кашу и не знает, чинить ему что-то
    или так и задумано.

    Отдельно про упрощённый режим: без модели сегментации трекер работает по
    трёхсекундным чанкам и на границах реплик путает голоса. Это не поломка,
    но и не то, что обещано, — значит человек должен знать.
    """
    if not enabled:
        return ("живая диаризация выключена в конфиге (sufler.live_diarize) — "
                "метки пойдут по каналам")
    if not model_path.exists():
        return ("живой диаризации нет: не найден models/diar/embedding.onnx — "
                "метки пойдут по каналам, где взять модель: docs/DIARIZATION.md")
    if seg_path is not None and not seg_path.exists():
        return ("живая диаризация в упрощённом режиме: нет "
                "models/diar/segmentation.onnx, голоса на границах реплик будут "
                "путаться — поставить: scripts/get_models.py --segmentation")
    return None


class SpeakerTracker:
    def __init__(self, model_path: pathlib.Path, sample_rate: int = 16000,
                 threshold: float = 0.45, min_sec: float = 1.2, max_speakers: int = 8,
                 sticky: float = 0.15):
        import sherpa_onnx
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=1))
        self.sr = sample_rate
        self.threshold = threshold
        self.sticky = sticky            # гистерезис: инерция текущего голоса
        self.min_samples = int(min_sec * sample_rate)
        self.max_speakers = max_speakers
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []
        self._last: int | None = None   # последний выданный номер (инерция)
        self._cand: np.ndarray | None = None  # чужой чанк, ждущий подтверждения

    def _embed(self, chunk: np.ndarray) -> np.ndarray | None:
        s = self._ex.create_stream()
        s.accept_waveform(self.sr, chunk)
        s.input_finished()
        if not self._ex.is_ready(s):
            return None
        emb = np.asarray(self._ex.compute(s), dtype=np.float32)
        n = float(np.linalg.norm(emb))
        return emb / n if n > 0 else None

    def _update(self, i: int, emb: np.ndarray):
        k = self._counts[i]  # скользящий центроид: голос «дообучается» по ходу
        c = (self._centroids[i] * k + emb) / (k + 1)
        self._centroids[i] = c / float(np.linalg.norm(c))
        self._counts[i] += 1

    def label(self, chunk: np.ndarray) -> int | None:
        """Номер голоса (1..N); None — только пока ни один голос не установлен.

        Шумные 3с-чанки мигали метками (1↔2) и рвали абзац одного человека
        на куски — теперь: инерция текущего голоса (порог-sticky), смена или
        новый голос только по двум согласным чанкам, короткий кусок =
        продолжение текущего.
        """
        if len(chunk) < self.min_samples:
            return self._last
        emb = self._embed(chunk)
        if emb is None:
            return self._last
        if not self._centroids:  # первый голос встречи не задерживаем
            self._centroids.append(emb)
            self._counts.append(1)
            self._last = 1
            return 1
        sims = [float(np.dot(emb, c)) for c in self._centroids]
        cur = (self._last - 1) if self._last else None
        cur_sim = sims[cur] if cur is not None else -1.0
        best = int(np.argmax(sims))
        # 1) текущий голос уверенно узнан — продолжаем и дообучаем
        if cur_sim >= self.threshold:
            self._update(cur, emb)
            self._cand = None
            return self._last
        # 2) ОТНОСИТЕЛЬНАЯ смена: другой голос заметно ближе текущего. Абсолютные
        #    пороги плывут между звонком (чужие ≤0.16) и очной комнатой через один
        #    микрофон (чужие до ~0.43, свои от ~0.29 — зоны перекрываются); дельта
        #    к текущему от акустики канала не зависит
        if best != cur and sims[best] >= 0.35 and sims[best] - max(cur_sim, 0.0) >= 0.12:
            self._update(best, emb)
            self._last = best + 1
            self._cand = None
            return self._last
        # 3) серая зона продолжения — тянем текущего без дообучения центроида
        if cur_sim >= self.threshold - self.sticky:
            self._cand = None
            return self._last
        # 4) все далеко: новый голос только по двум взаимно согласным чанкам
        #    (сырой-к-сырому у одного голоса ≥~0.45) — шумный одиночный кусок
        #    не плодит фантомов и не рвёт абзац
        if self._cand is not None and float(np.dot(emb, self._cand)) >= 0.45:
            if len(self._centroids) < self.max_speakers:
                c = emb + self._cand
                c /= float(np.linalg.norm(c))
                self._centroids.append(c)
                self._counts.append(2)
                self._last = len(self._centroids)
            self._cand = None
            return self._last
        self._cand = emb
        return self._last

    @property
    def voices(self) -> int:
        return len(self._centroids)


class SegmentTracker:
    """Метка голоса для чанка через куски речи, а не через три секунды целиком.

    Прежний SpeakerTracker считал эмбеддинг со всего чанка. На границе реплик в
    чанк попадает конец фразы одного человека и начало фразы другого, эмбеддинг
    выходит смешанным, косинус ко всем центроидам — средним, и трекер залипает
    на первом голосе: замер на синтетическом диалоге из четырёх голосов дал
    DER 0.725 при ОДНОМ найденном голосе. Порог на это не влияет — от 0.25 до
    0.55 результат одинаковый.

    Здесь та же модель сегментации, что уже работает в проходе после встречи,
    находит внутри чанка куски речи; эмбеддинг считается по куску. На той же
    фикстуре — DER 0.246 и все четыре голоса.

    Метка на чанк остаётся одна: у чанка один текст от STT, поэтому берётся
    говорящий, занявший в нём больше времени. Отдавать метки по кускам можно
    будет, когда по кускам пойдёт и распознавание, — это следующий шаг, и он
    стоит ещё вдвое меньше ошибок (замер: DER 0.090).

    Биометрия не хранится: центроиды живут в объекте, объект — во встрече.
    """

    def __init__(self, seg_model: pathlib.Path, emb_model: pathlib.Path,
                 sample_rate: int = 16000, threshold: float = 0.62,
                 min_segment: float = 0.4, min_new: float = 0.8,
                 max_speakers: int = 8):
        import sherpa_onnx

        self.sr = sample_rate
        # Порог 0.62 — из замера: 0.55 склеивает разных людей (DER 0.569),
        # 0.7 плодит лишние голоса. Между 0.6 и 0.65 результат стабилен.
        self.threshold = threshold
        self.min_segment = min_segment
        # Новый голос заводим только по куску подлиннее: на полусекундном
        # «угу» эмбеддинг слишком шумный, чтобы объявлять нового человека.
        self.min_new = min_new
        self.max_speakers = max_speakers
        self._diar = sherpa_onnx.OfflineSpeakerDiarization(
            sherpa_onnx.OfflineSpeakerDiarizationConfig(
                segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                    pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                        model=str(seg_model))),
                embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                    model=str(emb_model)),
                clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1,
                                                            threshold=0.8),
                min_duration_on=0.3,
                min_duration_off=0.5))
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model)))
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []
        self._last: int | None = None

    def _embed(self, piece: np.ndarray) -> np.ndarray | None:
        stream = self._ex.create_stream()
        stream.accept_waveform(self.sr, piece)
        stream.input_finished()
        if not self._ex.is_ready(stream):
            return None
        emb = np.asarray(self._ex.compute(stream), dtype=np.float32)
        n = float(np.linalg.norm(emb))
        return emb / n if n > 0 else None

    def _assign(self, emb: np.ndarray, seconds: float) -> int | None:
        sims = [float(np.dot(emb, c)) for c in self._centroids]
        best = int(np.argmax(sims)) if sims else -1
        if best >= 0 and sims[best] >= self.threshold:
            k = self._counts[best]
            centroid = (self._centroids[best] * k + emb) / (k + 1)
            self._centroids[best] = centroid / float(np.linalg.norm(centroid))
            self._counts[best] += 1
            return best
        if seconds >= self.min_new and len(self._centroids) < self.max_speakers:
            self._centroids.append(emb)
            self._counts.append(1)
            return len(self._centroids) - 1
        return None

    def label(self, chunk: np.ndarray) -> int | None:
        """Номер голоса (1..N) для чанка; None — пока сказать нечего.

        Контракт тот же, что у SpeakerTracker: демон не переписывается.
        """
        if chunk is None or len(chunk) < int(self.min_segment * self.sr):
            return self._last
        try:
            segments = self._diar.process(chunk).sort_by_start_time()
        except Exception:  # noqa: BLE001 — диаризация вспомогательна
            return self._last

        talk: dict[int, float] = {}
        for seg in segments:
            a, b = int(seg.start * self.sr), int(seg.end * self.sr)
            piece = chunk[a:b]
            seconds = (b - a) / self.sr
            if seconds < self.min_segment:
                continue
            emb = self._embed(piece)
            if emb is None:
                continue
            who = self._assign(emb, seconds)
            if who is not None:
                talk[who] = talk.get(who, 0.0) + seconds
        if not talk:
            return self._last
        # у чанка один текст — значит и метка одна: чей голос звучал дольше
        self._last = max(talk, key=lambda k: talk[k]) + 1
        return self._last

    @property
    def voices(self) -> int:
        return len(self._centroids)
