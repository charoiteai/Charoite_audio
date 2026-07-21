"""STT-абстракция: mlx-whisper (дефолт) или parakeet-mlx — оба локальные, Apple Silicon."""
from __future__ import annotations

import numpy as np


class STT:
    def __init__(self, cfg: dict):
        s = cfg["stt"]
        self.backend = s["backend"]
        self.language = s.get("language", "ru")
        if self.backend == "gigaam":
            import onnx_asr  # русский SOTA (Сбер, MIT)

            # CPU EP принудительно: CoreML-провайдер на GigaAM падает в рантайме,
            # а CPU на M1 Max даёт RTF ~28x — запас двадцатикратный (замер 16.07.2026)
            self._model = onnx_asr.load_model(
                s.get("gigaam_model", "gigaam-v3-e2e-rnnt"),
                providers=["CPUExecutionProvider"],
            )
        elif self.backend == "parakeet":
            from parakeet_mlx import from_pretrained

            self._model = from_pretrained(s["parakeet_model"])
        elif self.backend == "mlx_whisper":
            import mlx_whisper  # ленивый импорт: тяжёлый

            self._mod = mlx_whisper
            self.model = s["whisper_model"]
        else:
            raise ValueError(f"неизвестный stt.backend: {self.backend}")

    def transcribe(self, audio: np.ndarray, samplerate: int) -> str:
        """float32 mono 16kHz → текст. Пустую/шумовую отдачу чистим снаружи."""
        if self.backend == "gigaam":
            return (self._model.recognize(audio, sample_rate=samplerate) or "").strip()
        if self.backend == "parakeet":
            # parakeet_mlx.transcribe принимает только путь к файлу → временный wav
            import tempfile
            import wave as _wave

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            try:  # finally с самого начала: сбой записи wav не должен копить tmp-файлы
                with _wave.open(tmp, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(samplerate)
                    w.writeframes((audio * 32767).astype("int16").tobytes())
                res = self._model.transcribe(tmp)
                return (getattr(res, "text", None) or str(res)).strip()
            finally:
                import os

                os.unlink(tmp)
        out = self._mod.transcribe(
            audio,
            path_or_hf_repo=self.model,
            language=self.language,
            fp16=True,
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
        )
        return (out.get("text") or "").strip()
