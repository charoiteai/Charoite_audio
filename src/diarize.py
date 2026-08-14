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
import atexit
import shutil
import pathlib
import re
import subprocess
import sys
import tempfile
import wave

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import privacy  # noqa: E402
from stt import STT  # noqa: E402

from charoite_paths import resolve_root

ROOT = resolve_root(__file__)


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

SEG_MODEL = ROOT / "models" / "diar" / "segmentation.onnx"
EMB_MODEL = ROOT / "models" / "diar" / "embedding.onnx"



def _scratch_dir() -> pathlib.Path:
    """Временная папка, которая ГАРАНТИРОВАННО исчезнет вместе с процессом.

    Сюда кладётся 16-кГц WAV всей встречи (трёхчасовая — 345 МБ). Раньше это
    был просто mkdtemp без уборки: копия полного аудио оставалась в
    /var/folders до перезагрузки, а часто и дольше. Из неё извлекаются
    голосовые эмбеддинги, и про неё не знает ретеншн record_keep_days —
    то есть обещание «записи временны» обходилось незаметной копией.
    """
    d = pathlib.Path(tempfile.mkdtemp(prefix="charoite-"))
    atexit.register(shutil.rmtree, d, True)
    return d

def load_audio(src: pathlib.Path, channel: str) -> tuple[np.ndarray, int]:
    if src.suffix.lower() != ".wav":
        tmp = _scratch_dir() / "d.wav"
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
    segs = [(s.start, s.end, s.speaker) for s in result]
    if num_speakers <= 0:
        segs = _merge_shards(audio, sr, segs)
    return segs


MIN_SPEAKER_S = 30.0   # меньше этого суммарной речи — не участник, а осколок
WEAK_THRESHOLD = 0.50  # планка для приписывания осколка к ближайшему голосу
EMB_MIN_SEG_S = 1.0    # короче — эмбеддинг не снять, кластер слеп для склейки


def pool_voiceless(segs, min_seg: float = EMB_MIN_SEG_S) -> dict[int, int]:
    """Кластеры, с которых нечего снять, — в один общий голос.

    Склейка работает через эмбеддинги, а эмбеддинг требует хотя бы секунды
    непрерывной речи. Кластер, где нет ни одного такого куска, для склейки
    слеп: он не сравнивается ни с кем и доживает до стенограммы отдельным
    «собеседником». Порогами это не лечится — сравнивать просто нечего.

    Замер на очной встрече (65 минут, трое говорящих) показал масштаб: из
    74 кластеров 8 не имели ни одного сегмента длиннее секунды. Все вместе
    они наговорили 6.9 секунды из 1432 — полпроцента времени, обычно одна
    реплика на 0.6-1.0 с. Это «да», «угу», «согласен» поверх чужой речи.
    Живой участник в такое не укладывается, поэтому заводить под каждого
    свою метку — врать о числе людей на встрече.

    Сливаем их в один голос, а не растаскиваем по участникам: кто именно
    сказал «да», мы не знаем, и приписать конкретному человеку значило бы
    подменить автора. Общая метка честнее — она говорит «короткие реплики,
    голос не опознан».

    Возвращает {кластер: общий канон}; канон в ответе не встречается.
    """
    longest: dict[int, float] = {}
    for s, e, k in segs:
        longest[k] = max(longest.get(k, 0.0), e - s)
    voiceless = sorted(k for k, d in longest.items() if d < min_seg)
    if len(voiceless) < 2:
        return {}
    host = voiceless[0]
    return {k: host for k in voiceless[1:]}


def assign_shards(talk: dict[int, float], sim: dict[tuple[int, int], float],
                  min_speaker_s: float = MIN_SPEAKER_S,
                  weak_threshold: float = WEAK_THRESHOLD) -> dict[int, int]:
    """Куда приписать кластеры, слишком тихие для отдельного участника.

    13.08 встреча на троих дала тринадцать лишних «собеседников»: реальные
    участники держали 92% текста, а осколки — по 26-193 знака, то есть
    реплики в секунду-две («да», «угу», «согласен»). Строгий порог их не
    склеивает: с короткого сигнала эмбеддинг шумный и до 0.72 не дотягивает.

    Разрыв между участником и осколком — два порядка, поэтому правило
    безопасное: кто наговорил меньше `min_speaker_s`, тот не человек, а
    кусок чужого голоса. Такой кластер уходит к ближайшему по косинусу —
    но не любой ценой: ниже `weak_threshold` оставляем как есть, иначе
    приписали бы чужую реплику к первому попавшемуся.

    Возвращает {осколок: к кому приписать}; кластеры, которым пары не
    нашлось, в ответе отсутствуют.
    """
    strong = [k for k, t in talk.items() if t >= min_speaker_s]
    weak = [k for k, t in talk.items() if t < min_speaker_s]
    if not strong:
        # Все кластеры тихие — короткая запись или обмен репликами. Слипать
        # их между собой наугад опаснее, чем оставить как есть.
        return {}
    out: dict[int, int] = {}
    for w in weak:
        best, score = None, weak_threshold
        for s in strong:
            v = sim.get((w, s), sim.get((s, w), 0.0))
            if v > score:
                best, score = s, v
        if best is not None:
            out[w] = best
    return out


def _merge_shards(audio: np.ndarray, sr: int, segs, threshold: float = 0.60):
    """Осколки одного голоса → один кластер (очная встреча в один микрофон
    давала 30 «голосов» на четверых, 27.07). Средние эмбеддинги кластеров
    сравниваются по косинусу; ≥ threshold — это один человек.

    Порог измерен 14.08 на очной встрече (65 минут, один микрофон, трое
    говорящих). Попарная похожесть крупных кластеров разделилась начисто:
    куски одного голоса 0.68-0.89, разные люди 0.11-0.46. Между 0.46 и
    0.68 — пустая зона, и граница должна стоять там. Прежние 0.72 стояли
    ВНУТРИ диапазона своих: пара с похожестью 0.68 не склеивалась, и один
    человек уходил в стенограмму двумя «собеседниками».

    Биометрию НЕ храним (решение владельца 27.07): эмбеддинги живут только
    внутри этого вызова и выбрасываются.
    """
    import sherpa_onnx
    by: dict[int, list[tuple[float, float, float]]] = {}
    for s, e, k in segs:
        # Раньше порог был 2.0 с, и кластер из одних коротких реплик не
        # получал эмбеддинга вовсе — значит не участвовал в склейке и жил
        # отдельным «собеседником» до конца встречи. Секунды хватает на
        # шумный, но пригодный вектор; сравнение с ним идёт по мягкой планке.
        if e - s >= EMB_MIN_SEG_S:
            by.setdefault(k, []).append((e - s, s, e))
    if len(by) <= 1:
        return segs
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL)))
    embs: dict[int, np.ndarray] = {}
    for k, items in by.items():
        vecs = []
        for _d, s, e in sorted(items, reverse=True)[:5]:
            st = ex.create_stream()
            st.accept_waveform(sr, audio[int(s * sr):int(e * sr)])
            st.input_finished()
            if ex.is_ready(st):
                vecs.append(np.array(ex.compute(st)))
        if vecs:
            v = np.mean(vecs, axis=0)
            embs[k] = v / np.linalg.norm(v)

    parent = {k: k for k in by}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ks = sorted(embs)
    sim: dict[tuple[int, int], float] = {}
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            v = float(np.dot(embs[a], embs[b]))
            sim[(a, b)] = v
            if v >= threshold:
                parent[find(b)] = find(a)
    # канон группы — кластер с наибольшей суммарной речью (стабильные метки)
    talk = {k: sum(d for d, _s, _e in items) for k, items in by.items()}

    # Второй проход: кластеры, слишком тихие для отдельного участника,
    # уходят к ближайшему голосу по мягкой планке. Считаем речь по группам
    # после первого прохода — иначе два осколка одного человека выглядят
    # тихими порознь, хотя вместе это уже минута речи.
    group_talk: dict[int, float] = {}
    for k in by:
        group_talk[find(k)] = group_talk.get(find(k), 0.0) + talk[k]
    group_sim: dict[tuple[int, int], float] = {}
    for (a, b), v in sim.items():
        ga, gb = find(a), find(b)
        if ga != gb:
            key = (ga, gb)
            group_sim[key] = max(group_sim.get(key, 0.0), v)
    for weak, host in assign_shards(group_talk, group_sim).items():
        parent[find(weak)] = find(host)
    canon: dict[int, int] = {}
    for k in by:
        r = find(k)
        canon[r] = k if r not in canon or talk[k] > talk[canon[r]] else canon[r]
    remap = {k: canon[find(k)] for k in by}
    merged = len(by) - len(set(remap.values()))
    if merged:
        print(f"склейка осколков: {len(by)} кластеров → {len(set(remap.values()))}", flush=True)
    # Кластеры, слепые для склейки (ни одного куска длиннее секунды), в `by`
    # не попали и потому мимо всей логики выше. Их — в общий голос.
    voiceless = pool_voiceless(segs)
    if voiceless:
        print(f"короткие реплики без опознания: {len(voiceless) + 1} кластеров → один голос",
              flush=True)
        remap.update(voiceless)
    return [(s, e, remap.get(k, k)) for s, e, k in segs]


def name_speakers(cfg: dict, lines: list[tuple[str, float, float, str]]) -> dict[str, str]:
    """qwen сопоставляет Speaker N ↔ имена (обращения/представления в речи)."""
    import requests
    sample = "\n".join(f"[{spk}] {text}" for spk, _s, _e, text in lines[:80] if text)[:7000]
    try:
        r = requests.post(
            privacy.llm_base_url(cfg) + "/api/chat",
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
