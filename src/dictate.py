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
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from stt import STT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

SR = 16000


def main():
    cfg = yaml.safe_load(_cfg_text(ROOT))
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
