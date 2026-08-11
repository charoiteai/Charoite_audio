#!/usr/bin/env python3
"""Финальная стенограмма встречи: пересборка из ПОЛНОЙ записи после Стопа.

Живая лента режет звук 3с-чанками и решает мгновенно — это потолок качества.
Здесь — глобально, как MacWhisper: сегментация голосов по всей записи, STT по
сегментам, имена из разговора. Козырь, которого у MacWhisper нет: каналы
записаны РАЗДЕЛЬНО — голос из mic-канала с максимальным временем = владелец
(его микрофон), собеседники звонка живут в blackhole-канале.

Конвейер: daemon (Стоп) → rebuild_transcript.py <live.md>
  1) <stamp>.md пересобран из записей (живой черновик сохранён в _live.md)
  2) дальше обычный путь: graph_updater по уже чистому файлу
Записей нет/короткие → просто graph_updater по live (как раньше).
Демона убили до финализации записей — .pcm конвертируется здесь же.

Запуск руками: .venv/bin/python src/rebuild_transcript.py transcripts/<stamp>.md
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import wave

from charoite_paths import code_root, resolve_root

ROOT = resolve_root(__file__)
CODE = code_root(__file__)
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import numpy as np  # noqa: E402
import yaml  # noqa: E402

import meeting_stamp  # noqa: E402
import privacy  # noqa: E402
from diarize import diarize  # noqa: E402 — pyannote-сегментация + эмбеддинги, весь файл
from main import NOISE, Transcript  # noqa: E402
from graph_updater import EXIT_NO_SPEECH  # noqa: E402
from meeting_processing import MeetingStatusStore, find_meeting_note  # noqa: E402
from stt import STT  # noqa: E402

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
    """.pcm → .wav через временное имя, как это делает и сам демон.

    Прямая запись в целевой .wav означала вот что: обрыв посреди конвертации
    (kill, полный диск, паника) оставляет усечённый .wav, а `wait_recording`
    при следующем заходе видит его и принимает за готовый — финальная
    стенограмма собирается из огрызка, хотя целый .pcm ещё лежал рядом.
    Час чужой встречи не переснять, поэтому tmp + переименование.

    Имя временного файла с pid: две пересборки одной встречи (спавн
    восстановления и ручной запуск) не должны писать в один буфер. И оно
    намеренно НЕ совпадает с голым `.wav.part` демона — тот суффикс означает
    «идёт штатная финализация», занимать его посторонним писателем нельзя.
    При этом ретеншн узнаёт его наравне с прочими (`meeting_stamp`): иначе
    обрыв конвертации оставлял бы на диске полный WAV встречи навсегда.
    """
    out = pcm.with_suffix(".wav")
    tmp = out.with_name(f"{out.name}.part{os.getpid()}")
    try:
        with wave.open(str(tmp), "wb") as w, pcm.open("rb") as f:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            while chunk := f.read(1 << 20):
                w.writeframes(chunk)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)   # после replace его уже нет; страховка на обрыв
    pcm.unlink(missing_ok=True)
    return out


def wait_recording(rec_dir: pathlib.Path, stamp: str, label: str, sr: int) -> pathlib.Path | None:
    """Ждём финализацию канала демоном; после SIGKILL добиваем .pcm сами.

    Признак «демон бросил запись» — живой процесс, а не возраст файла.
    Раньше здесь стояло `mtime старше 10 секунд`: у трёхчасовой встречи
    (345 МБ на канал) демон физически не успевал сконвертировать оба канала
    за это время, mtime у .pcm замирал в момент stop() — и мы начинали писать
    в тот же .wav параллельно ему. Два писателя давали кашу из перемежающихся
    блоков, после чего оба делали unlink исходника: финальная стенограмма
    собиралась из битого звука, а восстановить было уже нечего.

    Обратная сторона того же признака: `.part` от УБИТОГО демона — не работа,
    а огрызок, и ждать его бессмысленно. Раньше он держал канал вечно (ждём
    45 секунд, потом отказываемся трогать .pcm «потому что кто-то пишет»), и
    удалять его было некому: чистка сметала только .pcm и .wav. Итог — звонок
    без реплик собеседника в финальной стенограмме, а через record_keep_days
    и .pcm уходил (аудит 0.46.0, P0-3). Живой демон и мёртвый демон различаются
    локом, поэтому решение принимается по нему, а не по наличию файла.
    """
    # stamp уже разрешён ОДИН РАЗ на пару каналов в rebuild(). Делать это
    # здесь по label нельзя: две встречи в одну минуту способны отдать mic
    # одной и blackhole другой, после чего стенограмма смешает два разговора.
    name = meeting_stamp.recording_path
    wav, pcm = name(rec_dir, stamp, label, "wav"), name(rec_dir, stamp, label, "pcm")
    part = name(rec_dir, stamp, label, "wav.part")

    def drop_stale_part() -> None:
        if part.exists():
            log(f"{label}: осиротевший {part.name} от убитого демона — убираю")
            part.unlink(missing_ok=True)

    deadline = time.time() + WAIT_WAV_S
    while time.time() < deadline:
        if wav.exists():
            return wav
        alive = _daemon_alive()
        if part.exists() and alive:
            time.sleep(2)          # демон сейчас конвертирует — не мешаем
            continue
        if pcm.exists() and not alive:
            drop_stale_part()
            log(f"{label}: демон мёртв и не финализировал — конвертирую .pcm сам")
            return pcm_to_wav(pcm, sr)
        time.sleep(2)
    if wav.exists():
        return wav
    # Вышло время. Осиротевший .part сюда не доберётся: цикл выше убирает его
    # на первом же заходе, где демон оказался мёртв. Значит, если .part всё
    # ещё на месте — его пишет живой демон, и трогать .pcm нельзя.
    if pcm.exists() and not part.exists():
        log(f"{label}: ожидание истекло — конвертирую .pcm сам")
        return pcm_to_wav(pcm, sr)
    return None


def _daemon_alive() -> bool:
    """Держит ли кто-то лок демона. Пока держит — записи финализирует он."""
    lock = ROOT / "logs" / "daemon.lock"
    if not lock.exists():
        return False
    try:
        with lock.open("r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False      # взяли лок — значит демона нет
    except OSError:
        return True       # лок занят — демон жив


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


def diarize_channel(audio: np.ndarray, sr: int, min_len: float = 1.0,
                    num_speakers: int = -1) -> list[tuple[float, float, int]]:
    """Сегменты (start, end, cluster) канала; короче min_len — отброшены.

    num_speakers > 0 — жёстко фиксирует число кластеров. Подсказку даёт живая
    сессия: авто-режим на моно-миксе плодит осколки (21.07: 14 «голосов» на
    встрече, где живьём их было 8).
    """
    try:
        return [(s, e, k) for s, e, k in diarize(audio, sr, num_speakers=num_speakers)
                if e - s >= min_len]
    except Exception as e:  # noqa: BLE001
        log(f"диаризация канала не удалась: {e}")
        return []


def overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, a[1] - a[0])


def name_speakers(cfg: dict, lines: list[tuple[str, str]]) -> dict[str, str]:
    """qwen: «Собеседник N» ↔ имена из разговора; владельца не трогаем."""
    import requests
    _owner = ((cfg.get("sufler") or {}).get("user_name") or "").strip().lower()
    sample = "\n".join(f"[{spk}] {text}" for spk, text in lines if text)[:7000]
    try:
        r = requests.post(
            privacy.llm_base_url(cfg) + "/api/chat",
            json={"model": cfg["llm"]["model"], "stream": False, "think": False,
                  "format": "json",
                  "options": {"num_ctx": 8192},
                  "messages": [
                      {"role": "system", "content": (
                          "По репликам определи ЛИЧНЫЕ имена говорящих (Сергей, Юля). "
                          "КРИТИЧНО: имя внутри реплики — почти всегда ОБРАЩЕНИЕ к ДРУГОМУ "
                          "(«Саш, а ты…» говорит НЕ Саша; Саша — тот, кто отвечает следом). "
                          "Имя присваивается говорящему только если он представился сам или "
                          "ответил сразу после обращения к нему. Имена — в именительном "
                          "падеже (Таня, не Тань). Обращения («коллеги», «ребята»), "
                          "должности, названия компаний и междометия именем НЕ являются — "
                          "для них «?». Верни СТРОГО JSON {\"Собеседник 1\":\"Имя\","
                          "\"Собеседник 2\":\"?\"} — «?» если имя не звучало. Не выдумывай.")},
                      {"role": "user", "content": sample},
                  ]},
            timeout=240,
        )
        data = json.loads(r.json().get("message", {}).get("content", "{}"))
        return {k: v.strip() for k, v in data.items()
                if isinstance(v, str) and v.strip() and v.strip() != "?"
                and k.startswith("Собеседник")
                and v.strip().lower() != _owner}  # владелец уже определён каналом
    except Exception as e:  # noqa: BLE001
        log(f"имена: не удалось ({e})")
        return {}


def live_meta(live: pathlib.Path) -> dict:
    """Данные живой сессии рядом со стенограммой: число голосов и опознанные имена.

    Пишет демон при завершении встречи (<стенограмма>.live.json). Нет файла —
    работаем как раньше: авто-кластеризация и определение имён по репликам.
    """
    try:
        p = live.with_name(live.name + ".live.json")
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001 — подсказка не обязательна
        log(f"live.json не прочитан: {e}")
    return {}


def names_by_time(live_text: str, base, segments: list[tuple[float, float, str]],
                  allowed: set[str]) -> dict[str, str]:
    """Переносит имена из живой стенограммы на метки пересборки ПО ВРЕМЕНИ.

    Метки живой сессии и пересборки — разные кластеризации, поэтому переносить
    «Собеседник 1» → «Собеседник 1» нельзя (приклеит имя не тому). Сопоставляем
    по пересечению интервалов: у какой метки больше всего совпадений по времени
    с репликами живого «Алексея» — та и Алексей. Имя достаётся одной метке.
    """
    import datetime as _dt
    spans: list[tuple[float, float, str]] = []
    for m in re.finditer(r"^\*\*(.+?)\*\* \[(\d{2}:\d{2})(?:–(\d{2}:\d{2}))?\]:", live_text, re.M):
        spk, t1, t2 = m.group(1).strip(), m.group(2), m.group(3)
        # только имена, которые демон РЕАЛЬНО опознал за встречу (allowed из
        # live.json). Иначе в имена попадают служебные потоки, пишущие в тот же
        # файл под своей меткой (21.07: «Темы» — 27 блоков наравне со спикерами)
        if spk not in allowed:
            continue
        def _sec(hhmm: str) -> float:
            h, mi = (int(x) for x in hhmm.split(":"))
            d = _dt.datetime.combine(base.date(), _dt.time(h, mi))
            if d < base - _dt.timedelta(hours=12):
                d += _dt.timedelta(days=1)  # встреча через полночь
            return (d - base).total_seconds()
        s = _sec(t1)
        e = _sec(t2) + 60 if t2 else s + 60  # у живых блоков точность до минуты
        spans.append((s, e, spk))
    if not spans:
        return {}
    score: dict[str, dict[str, float]] = {}
    for s, e, spk in segments:
        for ls, le, name in spans:
            ov = min(e, le) - max(s, ls)
            if ov > 0:
                score.setdefault(spk, {})[name] = score.setdefault(spk, {}).get(name, 0.0) + ov
    # жадно: сначала самые уверенные пары, каждое имя — только одной метке
    pairs = sorted(((v, lbl, nm) for lbl, d in score.items() for nm, v in d.items()),
                   reverse=True)
    out: dict[str, str] = {}
    used: set[str] = set()
    for _v, lbl, nm in pairs:
        if lbl not in out and nm not in used:
            out[lbl] = nm
            used.add(nm)
    return out


def rebuild(live: pathlib.Path, cfg: dict) -> pathlib.Path | None:
    # Штамп берём целиком: демон называет записи ИМЕНЕМ СТЕНОГРАММЫ (daemon
    # передаёт tr.stamp в AudioHub), поэтому любая обрезка здесь означает
    # поиск файла, которого не существует. Срез [:15] отбрасывал секунды и с
    # 28.07 не находил ни одной записи — ни одна встреча не пересобиралась.
    # stamp_of отделяет главный файл (в т.ч. уже с темой — так приходит retry
    # из приложения) от производных: пересборка по «_разбор.md» перезаписала
    # бы стенограмму разбором.
    stamp = meeting_stamp.stamp_of(live.stem)
    if stamp is None:
        return None
    meta = live_meta(live)
    sr_cfg = int(cfg["audio"]["samplerate"])
    rec_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    if os.environ.get("SUFLER_RECORDINGS_DIR"):
        rec_dir = pathlib.Path(os.environ["SUFLER_RECORDINGS_DIR"])

    # Retry знает минутное имя с темой, записи — посекундный штамп демона.
    # Разрешаем его один раз сразу для обоих каналов: если кандидаты не
    # совпали, не берём ни один вместо склейки двух разных встреч.
    recording_stamp = meeting_stamp.resolve_stamp(rec_dir, stamp)
    base = meeting_stamp.started_at(recording_stamp)
    if base is None:
        return None

    # Столбим записи свежим mtime. Ретеншн щадит только штампы, о которых
    # знает демон (_recover_orphans на его старте), а сюда приходит и retry из
    # приложения — по встрече любого возраста: «позавчерашняя ошибка так же…».
    # Пока мы ждём канал (до 45 с на каждый), демон новой встречи успевает
    # провести чистку — и запись старше record_keep_days исчезает из-под ног:
    # тот же исход, что у P0-1, только через другой вход. Канала связи с
    # демоном у нас нет, а возраст файла — ровно тот язык, на котором ретеншн
    # принимает решения; touch честно продлевает жизнь на keep_days от старта
    # пересборки.
    for _label in meeting_stamp.RECORDING_LABELS:
        for _ext in ("pcm", "wav", "wav.part"):
            _p = meeting_stamp.recording_path(rec_dir, recording_stamp, _label, _ext)
            try:
                if _p.exists():
                    os.utime(_p)
            except OSError:
                pass          # не продлили — ретеншн решит по старому mtime

    mic_p = wait_recording(rec_dir, recording_stamp, "mic", sr_cfg)
    bh_p = wait_recording(rec_dir, recording_stamp, "blackhole", sr_cfg)
    if mic_p is None and bh_p is None:
        log("записей нет — оставляю живую стенограмму")
        return None

    segments: list[tuple[float, float, str]] = []  # (start, end, метка)
    chan: dict[str, str] = {}  # метка → канал-источник звука
    next_n = 1

    def merge_dwarfs(segs: list[tuple[float, float, int]],
                     min_dur: float) -> list[tuple[float, float, int]]:
        """Кластеры-карлики (< min_dur суммарно) — осколки кластеризации
        (встреча 21.07: 23 «голоса» в канале звонка): вливаем их сегменты
        во временно ближайший крупный кластер — текст не теряется."""
        durs: dict[int, float] = {}
        for s, e, k in segs:
            durs[k] = durs.get(k, 0.0) + (e - s)
        big = {k for k, d in durs.items() if d >= min_dur} or set(durs)
        bigsegs = [t for t in segs if t[2] in big]
        if not bigsegs:
            return segs

        def nearest_big(s: float, e: float) -> int:
            return min(bigsegs, key=lambda x: min(abs(x[0] - e), abs(s - x[1])))[2]
        return [(s, e, k if k in big else nearest_big(s, e)) for s, e, k in segs]

    if bh_p is not None:
        bh, sr = load_wav(bh_p)
        if len(bh) > sr * 20:
            # в звонке участники говорят подолгу — кластер короче 25с почти
            # наверняка осколок чьего-то голоса, не отдельный человек
            # сколько голосов слышала живая сессия — жёсткая подсказка кластеризации;
            # без неё авто-режим дробит голоса на осколки (14 «людей» вместо 8)
            hint = int(meta.get("speakers") or 0)
            bh_segs = merge_dwarfs(
                diarize_channel(bh, sr, num_speakers=hint if 1 < hint <= 12 else -1), 25.0)
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
            mic_segs = merge_dwarfs(mic_segs, 10.0)
            durs = {}
            for s, e, k in mic_segs:
                durs[k] = durs.get(k, 0.0) + (e - s)
            # голос с максимальным суммарным временем в СВОЁМ микрофоне = владелец
            owner_voice = max(durs, key=durs.get) if durs else None
            mapping = {}
            for s, e, k in mic_segs:
                if k not in mapping:
                    if k == owner_voice:
                        mapping[k] = "владелец"
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

    # часы:минуты от реального начала встречи — base посчитан из штампа выше
    import datetime as dt

    # Имена: сперва переносим добытые ЖИВОЙ сессией (демон проверял их всю
    # встречу — самопредставления, ответы после обращения), сопоставляя по
    # времени. Затем qwen досматривает только те метки, которым имя не досталось.
    allowed = {v for v in (meta.get("names") or {}).values() if isinstance(v, str) and v.strip()}
    names = names_by_time(live.read_text(encoding="utf-8"), base,
                          [(s, e, spk) for s, e, spk, _ in lines], allowed) if allowed else {}
    if names:
        log("имена из живой сессии: " + ", ".join(f"{k}→{v}" for k, v in names.items()))
    rest = {spk for _, _, spk, _ in lines} - set(names)
    if rest:
        guessed = name_speakers(cfg, [(spk, txt) for _, _, spk, txt in lines if spk in rest])
        for k, v in guessed.items():
            if k in rest and v not in names.values():  # одно имя — одной метке
                names[k] = v
        if guessed:
            log("имена от модели: " + ", ".join(f"{k}→{v}" for k, v in guessed.items()))
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


def retry_unfinished(status: MeetingStatusStore) -> None:
    """Догнать встречи, которые не доехали до готовности.

    Зовётся в конце удачной обработки — момент, когда точно известно, что
    конвейер жив, а LLM отвечает. 03.08 разбор упал на вставшей модели, и
    встреча пролежала необработанной полдня: повторять её было некому, а
    снаружи это выглядело как «программа перестала раскладывать по папкам».

    Повторы идут по одному и без рекурсии: очередь разбирается за столько
    удачных встреч, сколько в ней хвостов, — зато ни две модели разом в
    памяти, ни лавина процессов после недельного простоя.
    """
    try:
        pending = status.unfinished()
    except Exception as e:  # noqa: BLE001 — подбор не должен ронять удачную встречу
        log(f"подбор незавершённых не удался ({type(e).__name__}: {e})")
        return
    if not pending:
        return
    target = pathlib.Path(pending[0]["transcript_path"])
    log(f"повтор незавершённой встречи: {target.name} "
        f"(в очереди {len(pending)}, попытка {int(pending[0].get('attempts', 0)) + 1})")
    env = dict(os.environ, CHAROITE_NO_RETRY="1")
    subprocess.Popen(
        ["nice", "-n", "10", sys.executable, str(pathlib.Path(__file__)), str(target)],
        start_new_session=True, env=env,
        stdout=open(ROOT / "logs" / f"retry_{target.stem[:15]}.log", "w"),
        stderr=subprocess.STDOUT,
    )


def main():
    live = pathlib.Path(sys.argv[1]).expanduser()
    if not live.exists():
        sys.exit(f"нет файла: {live}")
    status = MeetingStatusStore(ROOT)
    pipeline_started = time.time()

    def publish(method, *args):
        try:
            return method(*args)
        except Exception as e:  # noqa: BLE001 — статус не должен ломать сам пайплайн
            log(f"статус обработки не записан ({type(e).__name__}: {e})")
            return None

    publish(status.processing, live, "waiting_for_audio")
    try:
        cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
        publish(status.processing, live, "rebuilding_transcript")
        try:
            rebuild(live, cfg)
        except Exception as e:  # noqa: BLE001 — граф важнее идеальной пересборки
            log(f"пересборка не удалась ({type(e).__name__}: {e}) — граф по живой версии")
        publish(status.processing, live, "updating_graph")
        result = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).parent / "graph_updater.py"), str(live)],
            check=False,
        )
        if result.returncode == EXIT_NO_SPEECH:
            # Тишину повторять бессмысленно: статус честно говорит, что речи
            # в записи нет, и подбор незавершённых сюда больше не вернётся.
            log("в записи нет речи — граф не трогаем")
            publish(status.no_speech, live)
            return
        if result.returncode:
            raise RuntimeError(f"graph_updater завершился с кодом {result.returncode}")
        note = find_meeting_note(cfg, live, newer_than=pipeline_started - 2)
        if note is None:
            raise RuntimeError("заметка встречи не создана")
        if not status.has_transcript(live):
            raise RuntimeError("финальная стенограмма не найдена")
        publish(status.ready, live, note)
        # Своя встреча готова — значит конвейер жив и LLM отвечает. Лучший
        # момент вернуться к тем, кому в прошлый раз не повезло.
        if os.environ.get("CHAROITE_NO_RETRY") != "1":
            retry_unfinished(status)
    except Exception as e:  # noqa: BLE001 — статус ошибки обязан пережить процесс
        log(f"обработка не завершена ({type(e).__name__}: {e})")
        publish(status.failed, live, f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
