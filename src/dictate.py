#!/usr/bin/env python3
"""Локальная диктовка: пишем микрофон, пока родитель держит stdin, — EOF =
стоп → GigaAM → текст в stdout. Всё на устройстве (наш ответ Wispr Flow,
который обрабатывает голос только в облаке).

Управление из Swift: Popen(...); закрыл stdin → получил распознанный текст.
STT грузится ПАРАЛЛЕЛЬНО записи — после отпускания хоткея остаётся только
распознавание (RTF 28x → ~0.5с на фразу).
"""
from __future__ import annotations

import pathlib
import sys
import threading

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from stt import STT  # noqa: E402

from charoite_paths import resolve_root
from config_loader import load_user_or_example

ROOT = resolve_root(__file__)

SR = 16000


def main():
    cfg = load_user_or_example(ROOT)
    frames: list[np.ndarray] = []
    stt_box: dict = {}
    t = threading.Thread(target=lambda: stt_box.update(stt=STT(cfg)), daemon=True)
    t.start()  # модель греется, пока человек говорит

    stream = sd.InputStream(
        samplerate=SR, channels=1, dtype="float32",
        callback=lambda data, *_: frames.append(data[:, 0].copy()),
    )
    stream.start()
    print("REC", file=sys.stderr, flush=True)  # сигнал Swift: запись пошла
    sys.stdin.buffer.read()  # ждём EOF — родитель отпустил хоткей
    stream.stop()
    stream.close()

    if not frames:
        return
    audio = np.concatenate(frames)
    if len(audio) < SR * 0.4:  # случайное нажатие
        return
    t.join(timeout=15)
    stt = stt_box.get("stt") or STT(cfg)
    text = stt.transcribe(audio, SR).strip()
    if text:
        print(text, flush=True)


if __name__ == "__main__":
    main()
