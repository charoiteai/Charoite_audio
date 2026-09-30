#!/usr/bin/env python3
"""Прогон записи встречи через тень потокового Nemotron — без звонка (№478 B, часть 1).

Тень (`src/live_nemotron.py`) собирает цифры для PR B только на живом звонке. Этот скрипт
даёт ей тот же вход из записи: живые объекты, а не копии их логики.

- Хаб — настоящий `AudioHub` (запись на диск выключена) с двумя захватами, `blackhole` и
  `mic`; кадры обоих каналов из их wav блоками по `--block` через `_consume` — тот же путь
  кадра, что у захвата (буфер STT и слушатели кадров). Каналы подаются в ногу и
  дополнены тишиной до равной длины: срез собеседников есть в каждом `pull_placed`, где
  есть срез микрофона, и правило эха по стенным часам (`_sys_speech_until`) не
  срабатывает при любом темпе.
- Трекер — `SegmentTracker` той же фабрикой, что у демона, общий на оба канала. Ветка
  чанка — зеркало цикла STT демона (`daemon.py`, выбор плана → `guarded_split` →
  `jobs_for` → `diarized_state` → `note_chunk`), без распознавания: STT в прогоне нет,
  очередь не растёт, план всегда «раскладка».
- Тень — настоящий `live_nemotron.start()` с корнем прогона: журнал ложится в
  `<выход>/logs/`, веса и окружение движка — по ссылкам на корень данных владельца.
  На время вызова `Shadow.begin` получает `spawn=` — обёртку двери-свидетеля: она пишет
  по стене каждую запись блока в трубу и каждый фронт (`timing.jsonl`, одни часы) и даёт
  прогону темп: подано тени минус съедено ребёнком — не больше `--lead` секунд звука.
- Первые `--preroll` секунд подаются до того, как тень слушает хаб: она их не видит,
  чанки с ними получают `before_stream`, начало потока не кратно шагу ребёнка — как в бою.

Выход — свежий каталог (`--out`, по умолчанию в кэше пользователя, вне репозитория):
`logs/nemotron_live_<штамп>.jsonl` (журнал тени), `tracker.jsonl` (что решил трекер по
каждому чанку канала собеседников, интервалы на оси записи), `timing.jsonl`, `meta.json`.
Звука и текста там нет. Сверка — `scripts/nemotron_shadow_check.py`.

    CHAROITE_ROOT=<корень данных> .venv/bin/python scripts/nemotron_shadow_replay.py <штамп>

Тяжёлый: вторая копия модели и трекер. Запускать, когда машина свободна
(`quiet_guard.sh && …`).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import sys
import threading
import time
import types
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import charoite_paths  # noqa: E402

#: Сколько звука тень может держать поданным, но не съеденным ребёнком (< `QUEUE_CAP_S`).
LEAD_S = 4.0
#: Звук до рукопожатия: тень его не видит, начало потока не кратно шагу ребёнка.
PREROLL_S = 0.3
#: Блок подачи в хаб.
BLOCK_S = 0.1
#: Каналы хаба в прогоне; тень слушает только первый.
LABELS = ("blackhole", "mic")
#: Ожидание конца тени после стопа, секунды: хвост очереди плюс отсрочка убийства.
END_WAIT_S = 180.0


class Refused(Exception):
    """Прогон невозможен или негоден — с причиной."""


def default_out(stamp: str) -> pathlib.Path:
    base = pathlib.Path(os.path.expanduser("~/Library/Caches/charoite-478b"))
    return base / stamp / time.strftime("%Y%m%d-%H%M%S")


def read_channel(path: pathlib.Path, sr: int):
    """Моно s16 wav нужной частоты → float32 [-1, 1)."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != sr or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise Refused(f"{path.name}: нужен моно s16 {sr} Гц")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def pad_equal(*channels):
    """Каналы, дополненные тишиной до длины самого длинного."""
    import numpy as np
    n = max(len(c) for c in channels)
    return [np.concatenate([c, np.zeros(n - len(c), dtype=np.float32)]) for c in channels]


def expected_cuts(n: int, sr: int, chunk_s: float, overlap_s: float) -> int:
    """Сколько срезов хаб сделает из `n` сэмплов канала (`_cut_placed`: срез длиной
    `need`, в буфере остаётся `keep`)."""
    need, keep = int(sr * chunk_s), int(sr * overlap_s)
    return 0 if n < need else (n - keep) // (need - keep)


class Witness:
    """Свидетель двери: пишет по одним часам запись блока в трубу и приход фронта,
    держит «съедено ребёнком» для темпа. Обёртка прозрачна по исключениям: запись в
    трубу — первой, книжка — после успеха, всё брошенное `write` уходит писателю тени
    как есть (на этом стоит её путь смерти)."""

    def __init__(self, path: pathlib.Path, clock=time.monotonic):
        self._f = open(path, "w", buffering=1, encoding="utf-8")
        self._clock = clock
        self._lock = threading.Condition()
        self.sent = 0
        self.fed = 0

    def _row(self, row: dict) -> None:
        try:
            self._f.write(json.dumps(row) + "\n")
        except (OSError, ValueError):
            pass                        # книжка не важнее звука: свидетель молчит, прогон идёт

    def wrote(self, samples: int) -> None:
        with self._lock:
            self.sent += samples
            self._row({"k": "write", "t": round(self._clock(), 6), "sent": self.sent})

    def front(self, message: dict) -> None:
        if message.get("type") != "front":
            return
        with self._lock:
            self.fed = int(message.get("fed", self.fed))
            self._row({"k": "front", "t": round(self._clock(), 6), "fed": self.fed})
            self._lock.notify_all()

    def wait_fed(self, at_least: int, timeout: float) -> bool:
        with self._lock:
            return self._lock.wait_for(lambda: self.fed >= at_least, timeout)

    def close(self) -> None:
        self._f.close()


class _StreamProxy:
    """Процесс ребёнка, чья запись видна свидетелю; всё прочее — как у настоящего."""

    def __init__(self, stream, witness: Witness):
        self._stream, self._witness = stream, witness

    def write(self, data: bytes):
        result = self._stream.write(data)
        self._witness.wrote(len(data) // 2)
        return result

    def __getattr__(self, name):
        return getattr(self._stream, name)


def witness_spawn(witness: Witness, spawn):
    """Дверь `spawn_stream` со свидетелем: сообщения — сначала свидетелю, потом тени;
    процесс — в обёртке записи."""
    def wrapped(python, script, args, **kw):
        on_message = kw["on_message"]

        def tapped(message: dict) -> None:
            witness.front(message)
            on_message(message)
        kw["on_message"] = tapped
        stream, out = spawn(python, script, args, **kw)
        return (_StreamProxy(stream, witness) if stream is not None else None), out
    return wrapped


def run_root(out: pathlib.Path, data_root: pathlib.Path) -> pathlib.Path:
    """Корень прогона: свежий каталог, `logs/` свой, веса и окружение движка — ссылками."""
    if out.exists():
        raise Refused(f"каталог выхода уже есть: {out} — журнал открыт на дозапись, прогоны смешались бы")
    (out / "logs").mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)
    for name in ("models", "engines"):
        target = data_root / name
        if target.exists():
            (out / name).symlink_to(target, target_is_directory=True)
    return out


def chunk_decision(tracker, placed, *, stt_runtime, jobs_for, diarized_state):
    """Зеркало ветки чанка демона без STT: (состояние для тени, путь, интервалы на оси).

    Путь: `split_failed` — раскладка упала (канальная метка); `excluded` — вся речь
    исключена политикой (`pieces == []`); `pieces` — окна по голосам; `whole` — чанк
    целиком одним голосом (`pieces is None`, голос `main`, может быть None)."""
    plan = stt_runtime.diarization_plan(lagging=False,
                                        has_split=stt_runtime.has_split_tracker(tracker))
    if plan != "diarize":
        raise Refused(f"план чанка {plan!r}: в прогоне ждали раскладку трекером")
    res, split_failed = stt_runtime.guarded_split(tracker, placed.chunk, placed.speaker)
    jobs = jobs_for(res, placed.chunk)
    state = diarized_state(split_failed, jobs)
    start = int(placed.start)
    if split_failed:
        return state, "split_failed", []
    if res.pieces is not None and not res.pieces:
        return state, "excluded", []
    if res.pieces:
        return state, "pieces", [(start + p.raw_start, start + p.raw_end, int(p.voice)) for p in res.pieces]
    return state, "whole", [(start, start + len(placed.chunk), res.main)]


def replay(stamp: str, *, data_root: pathlib.Path, out: pathlib.Path, lead_s: float = LEAD_S,
           preroll_s: float = PREROLL_S, block_s: float = BLOCK_S, say=None,
           memory=None) -> dict:
    """Прогнать запись встречи `stamp`; вернуть сводку (`meta.json`)."""
    import audio
    import config_loader
    import diarize_live
    import foreign_python
    import live_nemotron
    import meeting_stamp
    import stt_runtime

    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    cfg = copy.deepcopy(config_loader.load_user_or_example(data_root))
    cfg.setdefault("sufler", {})["live_nemotron"] = live_nemotron.SHADOW
    cfg.setdefault("audio", {})["record"] = False
    sr = int(cfg["audio"]["samplerate"])
    rec_dir = data_root / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    paths = [meeting_stamp.recording_path(rec_dir, stamp, label, "wav") for label in LABELS]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        raise Refused(f"нет записей: {', '.join(missing)}")
    bh, mic = pad_equal(*(read_channel(p, sr) for p in paths))

    models = data_root / charoite_paths.MODELS_DIR / "diar"
    seg_model, emb_model = models / "segmentation.onnx", models / "embedding.onnx"
    if diarize_live.tracker_kind(seg_model, emb_model) != "segments":
        raise Refused("нет моделей трекера по кускам речи (models/diar)")
    root = run_root(out, data_root)

    hub = audio.AudioHub(cfg, stamp=stamp, captures=[])
    captures = [types.SimpleNamespace(label=label) for label in LABELS]
    hub._register_captures(captures)
    tracker = diarize_live.SegmentTracker(seg_model, emb_model, sample_rate=hub.sr,
                                          step_s=max(0.5, hub.chunk_s - hub.overlap_s))

    witness = Witness(root / "timing.jsonl")
    original_begin = live_nemotron.Shadow.begin

    def begin(self, **kw):
        kw.setdefault("spawn", witness_spawn(witness, foreign_python.spawn_stream))
        return original_begin(self, **kw)

    live_nemotron.Shadow.begin = begin
    try:
        shadow = live_nemotron.start(cfg, root=root, stamp=stamp, sr=hub.sr, labels=LABELS, say=say,
                                     **({"memory": memory} if memory is not None else {}))
    finally:
        live_nemotron.Shadow.begin = original_begin
    if not isinstance(shadow, live_nemotron.Shadow):
        raise Refused("тень не поднялась (причина — строкой выше)")
    t_begin = time.monotonic()

    trk = open(root / "tracker.jsonl", "w", buffering=1, encoding="utf-8")
    block = int(sr * block_s)
    counts = {"placed": 0, "tracker_lines": 0}

    def feed(lo: int, hi: int) -> None:
        for cap, samples in zip(captures, (bh, mic)):
            hub._consume(cap, samples[lo:hi])
        for placed in hub.pull_placed():
            counts["placed"] += 1
            state, path, intervals = chunk_decision(
                tracker, placed, stt_runtime=stt_runtime, jobs_for=diarize_live.jobs_for,
                diarized_state=live_nemotron.diarized_state)
            shadow.note_chunk(placed, state)
            if placed.seq[0] == live_nemotron.CHANNEL:
                counts["tracker_lines"] += 1
                trk.write(json.dumps({"chunk": placed.seq[1], "start": int(placed.start),
                                      "end": int(placed.start) + len(placed.chunk), "state": state,
                                      "path": path, "intervals": intervals}) + "\n")

    pos = 0
    pre = int(sr * preroll_s)
    while pos < pre:                                  # до рукопожатия: тень ещё не слушает
        feed(pos, min(pre, pos + block))
        pos = min(pre, pos + block)
    deadline = time.monotonic() + live_nemotron.HANDSHAKE_S + 10
    while shadow.state == live_nemotron.STARTING and time.monotonic() < deadline:
        time.sleep(0.1)
    if shadow.state != live_nemotron.LIVE:
        raise Refused(f"тень не вышла в поток: {shadow.state} ({shadow.reason})")
    # слушатель — после предзвука и рукопожатия: поток начинается ровно с `pre`, как бы
    # быстро ни поднялся ребёнок (в бою начало потока — первый блок после рукопожатия)
    shadow.attach(hub)
    handshake_s = round(time.monotonic() - t_begin, 1)
    start0 = pos
    lead = int(sr * lead_s)
    n = len(bh)
    t_feed = time.monotonic()
    while pos < n:
        if shadow.state != live_nemotron.LIVE:
            raise Refused(f"тень умерла посреди прогона: {shadow.reason}")
        if (pos - start0) - witness.fed > lead:
            witness.wait_fed(pos - start0 - lead, timeout=1.0)
            continue
        feed(pos, min(n, pos + block))
        pos = min(n, pos + block)
    feed_wall = time.monotonic() - t_feed
    shadow.stop()
    deadline = time.monotonic() + END_WAIT_S
    while shadow.state != live_nemotron.DEAD and time.monotonic() < deadline:
        time.sleep(0.2)
    trk.close()
    witness.close()
    cuts = {label: hub.chunk_no.get(label, -1) + 1 for label in LABELS}
    expect = expected_cuts(n, sr, hub.chunk_s, hub.overlap_s)
    if len(set(cuts.values())) != 1 or cuts[LABELS[0]] != expect:
        raise Refused(f"срезов каналов {cuts}, ожидали по {expect}: каналы разошлись")
    if shadow.state != live_nemotron.DEAD:
        raise Refused(f"тень не закончилась за {END_WAIT_S:.0f} с после стопа")
    meta = {"stamp": stamp, "sr": sr, "audio_s": round(n / sr, 1), "start0": start0,
            "fed_to_shadow": n - start0, "cuts_per_channel": expect, "chunks": counts,
            "handshake_s": handshake_s, "wall_s": round(feed_wall, 1),
            "speed_x": round((n - start0) / sr / feed_wall, 1) if feed_wall > 0 else None,
            "lead_s": lead_s, "block_s": block_s, "preroll_s": preroll_s,
            "journal": str(charoite_paths.meeting_log(root, "nemotron_live", stem=stamp, suffix=".jsonl")
                           .relative_to(root))}
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Прогон записи через тень потокового Nemotron (№478 B)")
    ap.add_argument("stamp", help="штамп записи: <штамп>_blackhole.wav и <штамп>_mic.wav в recordings/")
    ap.add_argument("--out", type=pathlib.Path, help="свежий каталог выхода (по умолчанию — кэш пользователя)")
    ap.add_argument("--lead", type=float, default=LEAD_S, help="секунд звука впереди ребёнка")
    ap.add_argument("--preroll", type=float, default=PREROLL_S, help="секунд звука до рукопожатия")
    args = ap.parse_args(argv)
    data_root = charoite_paths.name_data_root_or_exit(__file__)
    out = args.out or default_out(args.stamp)
    try:
        meta = replay(args.stamp, data_root=data_root, out=out, lead_s=args.lead, preroll_s=args.preroll)
    except Refused as e:
        print(f"прогон не состоялся: {e}", file=sys.stderr)
        return 2
    print(json.dumps({**meta, "out": str(out)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
