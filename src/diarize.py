"""Диаризация записи встречи: кто из НЕСКОЛЬКИХ голосов что сказал, с именами.

Запуск: .venv/bin/python src/diarize.py <запись.wav|m4a> [--channel right] [ЧЧММ]

Конвейер: sherpa-onnx (pyannote-сегментация + eres2net-эмбеддинги, чистый ONNX,
всё локально) → сегменты Speaker N → GigaAM по сегментам → qwen сопоставляет
голосам имена из разговора → <stamp>_спикеры.md рядом со стенограммами.

Для будущих записей суфлёра: стерео L=владелец (его подписывать не надо),
R=собеседники (--channel right диаризует только их).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np
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

SEG_MODEL = ROOT / "models" / "diar" / "segmentation.onnx"
EMB_MODEL = ROOT / "models" / "diar" / "embedding.onnx"


def load_audio(src: pathlib.Path, channel: str) -> tuple[np.ndarray, int]:
    if src.suffix.lower() != ".wav":
        tmp = pathlib.Path(tempfile.mkdtemp()) / "d.wav"
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000",
                        str(src), str(tmp)], check=True, capture_output=True)
        src = tmp
    with wave.open(str(src), "rb") as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if nch > 1:
        raw = raw.reshape(-1, nch)
        idx = {"left": 0, "right": min(1, nch - 1)}.get(channel, 0)
        raw = raw[:, idx]
    audio = raw.astype(np.float32) / 32768.0
    if sr != 16000:  # грубый ресемпл, afconvert обычно уже дал 16к
        step = sr / 16000.0
        audio = audio[(np.arange(int(len(audio) / step)) * step).astype(int)]
        sr = 16000
    return audio, sr


def diarize(audio: np.ndarray, sr: int, num_speakers: int = -1, threshold: float = 0.8):
    import sherpa_onnx
    # threshold=0.55 на моно-миксе дал 119 «голосов» (каждый сегмент — новый).
    # Выше порог = агрессивнее слияние. Если знаешь число людей — задай num_speakers.
    clustering = (sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers)
                  if num_speakers > 0
                  else sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=threshold))
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEG_MODEL)),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL)),
        clustering=clustering,
        min_duration_on=0.6,
        min_duration_off=0.6,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    assert sd.sample_rate == sr, f"диаризатор ждёт {sd.sample_rate} Гц"
    print("диаризация…", flush=True)
    result = sd.process(audio).sort_by_start_time()
    return [(s.start, s.end, s.speaker) for s in result]


def name_speakers(cfg: dict, lines: list[tuple[str, float, float, str]]) -> dict[str, str]:
    """qwen сопоставляет Speaker N ↔ имена (обращения/представления в речи)."""
    import requests
    sample = "\n".join(f"[{spk}] {text}" for spk, _s, _e, text in lines[:80] if text)[:7000]
    try:
        r = requests.post(
            cfg["llm"]["base_url"].rstrip("/") + "/api/chat",
            json={"model": cfg["llm"]["model"], "stream": False, "think": False,
                  "format": "json",
                  "messages": [
                      {"role": "system", "content": (
                          "По репликам определи имена говорящих: кто как представился, "
                          "к кому как обращались. Верни СТРОГО JSON вида "
                          '{"speaker_0":"Имя","speaker_1":"?"} — «?» если имя не звучало. '
                          "Не выдумывай имён.")},
                      {"role": "user", "content": sample},
                  ]},
            timeout=180,
        )
        data = json.loads(r.json().get("message", {}).get("content", "{}"))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception as e:  # noqa: BLE001
        print(f"имена: не удалось ({e})")
        return {}


def main():
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    channel = "right" if ("--channel" in argv and "right" in argv) else "left"
    num_speakers = -1
    for a in argv:
        if a.startswith("--speakers="):
            num_speakers = int(a.split("=", 1)[1])
    src = pathlib.Path(args[0]).expanduser()
    if not src.exists():
        sys.exit(f"нет файла: {src}")
    cfg = yaml.safe_load(_cfg_text(ROOT))

    audio, sr = load_audio(src, channel)
    print(f"{src.name}: {len(audio)/sr/60:.1f} мин @ {sr} Гц, канал {channel}"
          + (f", спикеров задано: {num_speakers}" if num_speakers > 0 else ", число спикеров: авто"))
    segments = diarize(audio, sr, num_speakers=num_speakers)
    spk_ids = sorted({s for _, _, s in segments})
    print(f"сегментов: {len(segments)}, голосов: {len(spk_ids)}")

    stt = STT(cfg)
    lines: list[tuple[str, float, float, str]] = []
    t0 = dt.datetime.now()
    for start, end, spk in segments:
        if end - start < 1.0:
            continue
        chunk = audio[int(start * sr):int(end * sr)]
        text_parts = []
        seg_len = int(25 * sr)
        for off in range(0, len(chunk), seg_len):
            piece = chunk[off:off + seg_len]
            if len(piece) < sr * 0.6:
                break
            text_parts.append(stt.transcribe(piece, sr).strip())
        text = " ".join(p for p in text_parts if p)
        if text:
            lines.append((f"speaker_{spk}", start, end, text))
    print(f"STT сегментов: {(dt.datetime.now()-t0).total_seconds():.0f}с")

    names = name_speakers(cfg, lines)
    label = {sid: (names.get(sid) if names.get(sid) and names.get(sid) != "?" else sid.replace("speaker_", "Голос "))
             for sid in {l[0] for l in lines}}

    mt = dt.datetime.fromtimestamp(src.stat().st_mtime)
    arg = args[1] if len(args) > 1 else ""
    if re.match(r"^\d{4}-\d{2}-\d{2}_\d{4}$", arg):
        stamp = arg  # полный stamp от демона: mtime записи — конец встречи,
    else:            # у полуночной встречи дата разъехалась бы с артефактами
        stamp = f"{mt:%Y-%m-%d}_{arg or format(mt, '%H%M')}"
    out = ROOT / cfg["log"]["transcripts_dir"] / f"{stamp}_спикеры.md"
    body = [f"# Диаризация {stamp} — запись {src.name}",
            f"Голосов: {len(spk_ids)} · Имена: " + ", ".join(f"{k}→{v}" for k, v in label.items()), ""]
    for spk, start, end, text in lines:
        body.append(f"**{label[spk]}** [{int(start//60)}:{int(start%60):02d}–{int(end//60)}:{int(end%60):02d}]:")
        body.append(text)
        body.append("")
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"готово: {out}")


if __name__ == "__main__":
    main()
