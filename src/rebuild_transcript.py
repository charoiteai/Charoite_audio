#!/usr/bin/env python3
"""Финальная стенограмма встречи: пересборка из ПОЛНОЙ записи после Стопа.

Живая лента режет звук 3с-чанками и решает мгновенно — это потолок качества.
Здесь — глобально, как MacWhisper: сегментация голосов по всей записи, STT по
сегментам, имена из разговора. Козырь, которого у MacWhisper нет: каналы
записаны РАЗДЕЛЬНО — голос из mic-канала с максимальным временем = владелец (user_name)
(его микрофон), собеседники звонка живут в blackhole-канале.

Конвейер: daemon (Стоп) → rebuild_transcript.py <live.md>
  1) <stamp>.md пересобран из записей (живой черновик сохранён в _live.md)
  2) дальше обычный путь: graph_updater по уже чистому файлу
Записей нет/короткие → просто graph_updater по live (как раньше).
Демона убили до финализации записей — .pcm конвертируется здесь же.

Запуск руками: .venv/bin/python src/rebuild_transcript.py transcripts/<stamp>.md
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import wave

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from diarize import diarize  # noqa: E402 — pyannote-сегментация + эмбеддинги, весь файл
from main import NOISE, Transcript  # noqa: E402
from stt import STT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

try:  # имя владельца mic-канала — из конфига
    OWNER = (yaml.safe_load(_cfg_text(ROOT))["sufler"].get("user_name") or "Я").strip()
except Exception:  # noqa: BLE001
    OWNER = "Я"
SEG_S, OVERLAP_S = 25.0, 1.0
WAIT_WAV_S = 45  # демон финализирует .wav параллельно нашему старту


def log(msg: str):
    print(f"[rebuild] {msg}", flush=True)


def load_wav(p: pathlib.Path) -> tuple[np.ndarray, int]:
    with wave.open(str(p), "rb") as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return a.astype(np.float32) / 32768.0, sr


def pcm_to_wav(pcm: pathlib.Path, sr: int) -> pathlib.Path:
    out = pcm.with_suffix(".wav")
    with wave.open(str(out), "wb") as w, pcm.open("rb") as f:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        while chunk := f.read(1 << 20):
            w.writeframes(chunk)
    pcm.unlink(missing_ok=True)
    return out


def wait_recording(rec_dir: pathlib.Path, stamp: str, label: str, sr: int) -> pathlib.Path | None:
    """Ждём финализацию канала демоном; после SIGKILL добиваем .pcm сами."""
    wav, pcm = rec_dir / f"{stamp}_{label}.wav", rec_dir / f"{stamp}_{label}.pcm"
    deadline = time.time() + WAIT_WAV_S
    while time.time() < deadline:
        if wav.exists():
            return wav
        if pcm.exists() and time.time() - pcm.stat().st_mtime > 10:
            log(f"{label}: демон не финализировал — конвертирую .pcm сам")
            return pcm_to_wav(pcm, sr)
        time.sleep(2)
    return wav if wav.exists() else None


def stt_segment(stt: STT, audio: np.ndarray, sr: int) -> str:
    parts, prev = [], ""
    seg, ov = int(SEG_S * sr), int(OVERLAP_S * sr)
    for off in range(0, len(audio), seg - ov):
        piece = audio[off:off + seg]
        if len(piece) < sr * 0.6:
            break
        t = stt.transcribe(piece, sr).strip()
        if not t or t.lower().strip(" .!») ") in NOISE:
            continue
        t = Transcript._cut_overlap(prev, t) if prev else t
        if t:
            parts.append(t)
            prev = parts[-1]
    return " ".join(parts)


def diarize_channel(audio: np.ndarray, sr: int, min_len: float = 1.0) -> list[tuple[float, float, int]]:
    """Сегменты (start, end, cluster) канала; короче min_len — отброшены."""
    try:
        return [(s, e, k) for s, e, k in diarize(audio, sr) if e - s >= min_len]
    except Exception as e:  # noqa: BLE001
        log(f"диаризация канала не удалась: {e}")
        return []


def overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, a[1] - a[0])


def name_speakers(cfg: dict, lines: list[tuple[str, str]]) -> dict[str, str]:
    """qwen: «Собеседник N» ↔ имена из разговора; владельца не трогаем."""
    import requests
    sample = "\n".join(f"[{spk}] {text}" for spk, text in lines if text)[:7000]
    try:
        r = requests.post(
            cfg["llm"]["base_url"].rstrip("/") + "/api/chat",
            json={"model": cfg["llm"]["model"], "stream": False, "think": False,
                  "format": "json",
                  "options": {"num_ctx": 8192},
                  "messages": [
                      {"role": "system", "content": (
                          "По репликам определи ЛИЧНЫЕ имена говорящих (Сергей, Юля): кто "
                          "представился, к кому обращались. Обращения («коллеги», «ребята», "
                          "«друзья»), должности и роли именем НЕ являются — для них «?». "
                          "Верни СТРОГО JSON {\"Собеседник 1\":\"Имя\","
                          "\"Собеседник 2\":\"?\"} — «?» если имя не звучало. Не выдумывай.")},
                      {"role": "user", "content": sample},
                  ]},
            timeout=240,
        )
        data = json.loads(r.json().get("message", {}).get("content", "{}"))
        return {k: v.strip() for k, v in data.items()
                if isinstance(v, str) and v.strip() and v.strip() != "?"
                and k.startswith("Собеседник")
                and v.strip().lower() != OWNER.lower()}  # владелец уже определён каналом
    except Exception as e:  # noqa: BLE001
        log(f"имена: не удалось ({e})")
        return {}


def rebuild(live: pathlib.Path, cfg: dict) -> pathlib.Path | None:
    stamp = live.stem[:15]
    if not re.match(r"\d{4}-\d{2}-\d{2}_\d{4}", stamp):
        return None
    sr_cfg = int(cfg["audio"]["samplerate"])
    rec_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    if os.environ.get("SUFLER_RECORDINGS_DIR"):
        rec_dir = pathlib.Path(os.environ["SUFLER_RECORDINGS_DIR"])

    mic_p = wait_recording(rec_dir, stamp, "mic", sr_cfg)
    bh_p = wait_recording(rec_dir, stamp, "blackhole", sr_cfg)
    if mic_p is None and bh_p is None:
        log("записей нет — оставляю живую стенограмму")
        return None

    segments: list[tuple[float, float, str]] = []  # (start, end, метка)
    chan: dict[str, str] = {}  # метка → канал-источник звука
    next_n = 1

    if bh_p is not None:
        bh, sr = load_wav(bh_p)
        if len(bh) > sr * 20:
            bh_segs = diarize_channel(bh, sr)
            mapping: dict[int, str] = {}
            for s, e, k in bh_segs:
                if k not in mapping:
                    mapping[k] = f"Собеседник {next_n}"
                    chan[mapping[k]] = "bh"
                    next_n += 1
                segments.append((s, e, mapping[k]))
            log(f"blackhole: {len(bh_segs)} сегментов, голосов {len(mapping)}")

    if mic_p is not None:
        mic, sr = load_wav(mic_p)
        if len(mic) > sr * 20:
            mic_segs = diarize_channel(mic, sr)
            # эхо динамиков: mic-сегмент, накрытый blackhole-речью, выбрасываем
            bh_iv = [(s, e) for s, e, _ in segments]
            mic_segs = [t for t in mic_segs
                        if not any(overlap_frac((t[0], t[1]), iv) > 0.5 for iv in bh_iv)]
            durs: dict[int, float] = {}
            for s, e, k in mic_segs:
                durs[k] = durs.get(k, 0.0) + (e - s)
            # кластеры-карлики (<10с суммарно) — артефакты порога на комнатной
            # акустике (реальная встреча дала 28 «голосов»): вливаем их сегменты
            # во временно ближайший крупный кластер — текст не теряется
            big = {k for k, d in durs.items() if d >= 10.0} or set(durs)
            bigsegs = [t for t in mic_segs if t[2] in big]
            def nearest_big(s: float, e: float) -> int:
                return min(bigsegs, key=lambda x: min(abs(x[0] - e), abs(s - x[1])))[2]
            mic_segs = [(s, e, k if k in big else nearest_big(s, e)) for s, e, k in mic_segs]
            durs = {}
            for s, e, k in mic_segs:
                durs[k] = durs.get(k, 0.0) + (e - s)
            # голос с максимальным суммарным временем в СВОЁМ микрофоне = владелец
            anton = max(durs, key=durs.get) if durs else None
            mapping = {}
            for s, e, k in mic_segs:
                if k not in mapping:
                    if k == anton:
                        mapping[k] = OWNER
                    else:
                        mapping[k] = f"Собеседник {next_n}"
                        next_n += 1
                    chan[mapping[k]] = "mic"
                segments.append((s, e, mapping[k]))
            log(f"mic: {len(mic_segs)} сегментов, голосов {len(durs)} (владелец = самый долгий)")

    if not segments:
        log("сегментов не нашлось — оставляю живую стенограмму")
        return None
    segments.sort(key=lambda t: t[0])
    # склейка соседних кусков одного голоса (зазор < 2с) — цельные абзацы
    merged: list[list] = []
    for s, e, spk in segments:
        if merged and merged[-1][2] == spk and s - merged[-1][1] < 2.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, spk])
    log(f"итог: {len(merged)} абзацев")

    # STT по абзацам (какой канал брать — по метке)
    stt = STT(cfg)
    mic_a = load_wav(mic_p)[0] if mic_p is not None else None
    bh_a = load_wav(bh_p)[0] if bh_p is not None else None
    t0 = time.time()
    lines: list[tuple[float, float, str, str]] = []
    for s, e, spk in merged:
        src = mic_a if chan.get(spk, "mic") == "mic" else bh_a
        if src is None:
            src = mic_a if mic_a is not None else bh_a
        if src is None:
            continue
        text = stt_segment(stt, src[int(s * sr_cfg):int(e * sr_cfg)], sr_cfg)
        if text:
            lines.append((s, e, spk, text))
    log(f"STT: {len(lines)} абзацев за {time.time() - t0:.0f}с")
    if not lines:
        return None

    names = name_speakers(cfg, [(spk, txt) for _, _, spk, txt in lines])
    if names:
        log("имена: " + ", ".join(f"{k}→{v}" for k, v in names.items()))

    # часы:минуты от реального начала встречи (stamp)
    import datetime as dt
    base = dt.datetime.strptime(stamp, "%Y-%m-%d_%H%M")
    fmt = lambda sec: (base + dt.timedelta(seconds=sec)).strftime("%H:%M")
    body = [f"# Встреча {stamp}", ""]
    for s, e, spk, text in lines:
        spk = names.get(spk, spk)
        span = fmt(s) if fmt(s) == fmt(e) else f"{fmt(s)}–{fmt(e)}"
        body += [f"**{spk}** [{span}]:", text, ""]
    # ко-мышление из живого черновика — переносим
    live_text = live.read_text(encoding="utf-8")
    m = re.search(r"\n---\n## Ко-мышление.*", live_text, re.S)
    if m:
        body.append(m.group(0).lstrip("\n"))

    live_copy = live.with_name(live.stem + "_live.md")
    if not live_copy.exists():
        live_copy.write_text(live_text, encoding="utf-8")
    live.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
    log(f"финальная стенограмма записана: {live.name} (живой черновик → {live_copy.name})")
    return live


def main():
    live = pathlib.Path(sys.argv[1]).expanduser()
    if not live.exists():
        sys.exit(f"нет файла: {live}")
    cfg = yaml.safe_load(_cfg_text(ROOT))
    try:
        rebuild(live, cfg)
    except Exception as e:  # noqa: BLE001 — граф важнее идеальной пересборки
        log(f"пересборка не удалась ({type(e).__name__}: {e}) — граф по живой версии")
    subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "graph_updater.py"),
                    str(live)], check=False)


if __name__ == "__main__":
    main()
