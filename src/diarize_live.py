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
