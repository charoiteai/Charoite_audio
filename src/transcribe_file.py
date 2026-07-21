"""Оффлайн-расшифровка аудиозаписи в стенограмму конвейера суфлёра.

Запуск: .venv/bin/python src/transcribe_file.py <файл.m4a|wav> [ЧЧММ]
m4a → wav 16кГц (afconvert, нативный macOS) → GigaAM сегментами 25с
с перекрытием → склейка с дедупом швов → transcripts/<дата>_<ЧЧММ>.md →
дальше обычный путь: graph_updater (тема, граф, разбор, Opus-ревизия).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys
import tempfile
import wave

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from main import NOISE, Transcript  # noqa: E402
from stt import STT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

SEG_S = 25.0
OVERLAP_S = 1.0


def to_wav16k(src: pathlib.Path, pcm_rate: int = 16000) -> pathlib.Path:
    if src.suffix.lower() == ".wav":
        return src
    if src.suffix.lower() == ".pcm":  # сырая запись AudioHub после крэша: s16le mono
        out = pathlib.Path(tempfile.mkdtemp()) / "rec.wav"
        with wave.open(str(out), "wb") as w, src.open("rb") as f:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(pcm_rate)  # из audio.samplerate конфига, не хардкод
            while chunk := f.read(1 << 20):
                w.writeframes(chunk)
        return out
    out = pathlib.Path(tempfile.mkdtemp()) / "rec.wav"
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(src), str(out)], check=True, capture_output=True)
    return out


def main():
    src = pathlib.Path(sys.argv[1]).expanduser()
    if not src.exists():
        sys.exit(f"нет файла: {src}")
    cfg = yaml.safe_load(_cfg_text(ROOT))
    stt = STT(cfg)
    wav = to_wav16k(src, pcm_rate=int(cfg["audio"]["samplerate"]))
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    dur = len(audio) / sr
    print(f"{src.name}: {dur/60:.1f} мин @ {sr} Гц")

    # штамп: время записи файла (когда встреча была), не время расшифровки
    mt = dt.datetime.fromtimestamp(src.stat().st_mtime)
    hhmm = sys.argv[2] if len(sys.argv) > 2 else f"{mt:%H%M}"
    stamp = f"{mt:%Y-%m-%d}_{hhmm}"

    out_dir = ROOT / cfg["log"]["transcripts_dir"]
    tpath = out_dir / f"{stamp}.md"
    parts: list[str] = []
    prev = ""
    seg = int(SEG_S * sr)
    ov = int(OVERLAP_S * sr)
    t0 = dt.datetime.now()
    for i, start in enumerate(range(0, len(audio), seg - ov)):
        chunk = audio[start:start + seg]
        if len(chunk) < sr:  # хвост меньше секунды
            break
        text = stt.transcribe(chunk, sr).strip()
        if not text or text.lower().strip(" .!») ") in NOISE:
            continue
        text = Transcript._cut_overlap(prev, text) if prev else text
        if text:
            parts.append(text)
            prev = parts[-1]
        done = min(start + seg, len(audio)) / len(audio)
        print(f"\r  {done*100:3.0f}% ({i+1} сегм.)", end="", flush=True)
    took = (dt.datetime.now() - t0).total_seconds()
    print(f"\nраспознано за {took:.0f}с (RTF {dur/max(took,0.1):.0f}x)")

    body = (f"# Встреча {stamp} — запись {src.name}\n\n"
            "**Голос** [запись, спикеры не разделены]:\n" + " ".join(parts) + "\n")
    tpath.write_text(body, encoding="utf-8")
    print(f"стенограмма: {tpath} ({len(body)} зн.)")
    print("дальше: .venv/bin/python src/graph_updater.py", tpath)


if __name__ == "__main__":
    main()
