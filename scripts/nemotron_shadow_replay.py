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
- Трекер — `SegmentTracker` той же фабрикой `live_tracker`, что у демона (шаг нарезки, квота мест микрофона), общий на оба канала. Ветка
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
import shutil
import sys
import threading
import time
import types
import typing
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
#: Сколько ждать, пока тень допишет журнал.
CLOSE_WAIT_S = 30.0
#: Отказ посреди прогона: столько ребёнку на выход, потом убийство.
ABORT_GRACE_S = 5.0
#: Ребёнок без фронта дольше этого при исчерпанном запасе подачи — завис, прогон — брак.
STALL_S = 60.0
#: Опрос состояния тени, секунды.
POLL_S = 0.1
#: Рукопожатие тень бросает сама через `live_nemotron.HANDSHAKE_S` (120 с); прогон ждёт с запасом.
HANDSHAKE_WAIT_S = 130.0
#: Отказ посреди прогона: отсрочка ребёнку и запас на строку `end`.
ABORT_WAIT_S = ABORT_GRACE_S + 5.0
#: Один заход ожидания фронта при исчерпанном запасе подачи, секунды.
FED_WAIT_S = 1.0
#: Знаки после запятой: часы свидетеля и секунды сводки.
CLOCK_DIGITS = 6
META_DIGITS = 1
#: Решение темпа: подавать, ждать фронт, ребёнок завис.
FEED, WAIT, STALL = "feed", "wait", "stall"


class Refused(Exception):
    """Прогон невозможен или негоден — с причиной."""


#: Единственное место выходов прогона: кэш пользователя, вне данных владельца и кода.
CACHE_BASE = pathlib.Path(os.path.expanduser("~/Library/Caches/charoite-478b"))


def _stderr(s: str) -> None:
    print(s, file=sys.stderr, flush=True)


def wait_for(done, timeout: float, *, poll: float = POLL_S, clock=time.monotonic, sleep=time.sleep) -> bool:
    """Ждать `done()` не дольше `timeout` секунд с опросом раз в `poll`; дождались ли."""
    deadline = clock() + timeout
    while not done():
        if clock() >= deadline:
            return False
        sleep(poll)
    return True


def pacing(pos: int, start0: int, fed: int, lead: int, now: float, front_at: float) -> tuple[str, int]:
    """Темп подачи: звук подан до `pos`, поток начат с `start0` — ребёнку подано `pos − start0`
    сэмплов, `fed` он съел, `lead` — запас впереди него.
    Запас есть — `FEED`; нет — `WAIT` до `fed >= цель`; фронта нет дольше `STALL_S` при
    исчерпанном запасе — `STALL` (сторож тени зависшего ребёнка не увидит: фронтов нет —
    нет и проверок)."""
    ahead = pos - start0
    if ahead - fed <= lead:
        return FEED, 0
    if now - front_at > STALL_S:
        return STALL, 0
    return WAIT, ahead - lead


def run_numbers(n: int, start0: int, sr: int, handshake_s: float, feed_wall: float) -> dict:
    """Производные цифры сводки прогона."""
    return {"audio_s": round(n / sr, META_DIGITS), "start0": start0, "fed_to_shadow": n - start0,
            "handshake_s": round(handshake_s, META_DIGITS), "wall_s": round(feed_wall, META_DIGITS),
            "speed_x": round((n - start0) / sr / feed_wall, META_DIGITS) if feed_wall > 0 else None}


def check_cuts(chunk_no: dict[str, int], labels: typing.Sequence[str], expect: int) -> None:
    """Каналы разрезаны в ногу и ровно по формуле хаба — иначе прогон брак. Срезов канала —
    номер его последнего чанка плюс один; канал, не разрезанный ни разу, — ноль."""
    cuts = {label: chunk_no.get(label, -1) + 1 for label in labels}
    if len(set(cuts.values())) != 1 or next(iter(cuts.values())) != expect:
        raise Refused(f"срезов каналов {cuts}, ожидали по {expect}: каналы разошлись")


def replay_config(cfg: dict, shadow_mode: str) -> dict:
    """Копия конфига владельца для прогона: тень включена, запись хаба выключена
    (прогон записей не пишет, даже при `record: true` у владельца)."""
    out = copy.deepcopy(cfg)
    out.setdefault("sufler", {})["live_nemotron"] = shadow_mode
    out.setdefault("audio", {})["record"] = False
    return out


def default_out(stamp: str) -> pathlib.Path:
    return CACHE_BASE / stamp / time.strftime("%Y%m%d-%H%M%S")


#: Пометка владельца каталога штампа в кэше: из какого каталога записей он прогнан.
OWNER = "owner.json"


def mark_owner(stamp_dir: pathlib.Path, rec_dir: pathlib.Path) -> None:
    (stamp_dir / OWNER).write_text(json.dumps({"rec_dir": str(rec_dir.resolve())}), encoding="utf-8")


def sweep_orphans(rec_dir: pathlib.Path) -> list[str]:
    """Производные встречи живут не дольше её записи: каталог штампа в кэше, прогнанный из
    этого же каталога записей (пометка `owner.json`), у которого записи уже нет (ретеншн,
    «Забыть»), удаляется целиком. Чужой корень, чужой каталог и всё без пометки не трогаются;
    сырой `.pcm` — тоже запись. `rmtree` по ссылкам не ходит."""
    import meeting_stamp
    gone: list[str] = []
    if not CACHE_BASE.is_dir():
        return gone
    mine = str(rec_dir.resolve())
    for entry in sorted(CACHE_BASE.iterdir()):
        marker = entry / OWNER
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:                             # нет пометки — не наш каталог
            owner = json.loads(marker.read_text(encoding="utf-8")).get("rec_dir")
        except (OSError, ValueError, AttributeError):
            continue
        if owner != mine or any(meeting_stamp.recording_path(rec_dir, entry.name, LABELS[0], ext).exists()
                                for ext in ("wav", "pcm")):
            continue
        shutil.rmtree(entry)
        gone.append(entry.name)
    return gone


def redact(text: str, stamp: str) -> str:
    """Штамп встречи и домашний каталог — маской: вывод прогона вставляют в отчёты."""
    return text.replace(stamp, "<штамп>").replace(os.path.expanduser("~"), "~")


def read_channel(path: pathlib.Path, sr: int):
    """Моно s16 wav нужной частоты → float32 [-1, 1)."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != sr or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise Refused(f"канал {path.stem.rsplit('_', 1)[-1]}: нужен моно s16 {sr} Гц")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def pad_equal(*channels):
    """Каналы, дополненные тишиной до длины самого длинного; канал нужной длины — как есть
    (часовая запись — сотни мегабайт, копировать её незачем)."""
    import numpy as np
    n = max(len(c) for c in channels)
    return [c if len(c) == n else np.pad(c, (0, n - len(c))) for c in channels]


def end_line(journal: pathlib.Path) -> dict:
    """Последняя строка `end` журнала тени; нет — пустой словарь. Тип — разбором строки:
    подстрока `"type": "end"` бывает и внутри свободного текста `reason` чужой строки."""
    ended: dict = {}
    for raw in journal.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            obj = json.loads(raw)
            if obj.get("type") == "end":
                ended = obj
    return ended


def allowed_out(out: pathlib.Path) -> None:
    """Выход прогона — только `<CACHE_BASE>/<штамп>/<каталог>`: белый список, а не перечень
    запретных корней (синхронизируемые каталоги, соседние клоны). Путь сравнивается после
    раскрытия ссылок: ссылка из кэша наружу раскрывается мимо базы и получает отказ."""
    base = CACHE_BASE.resolve()
    target = out.resolve()
    if base not in target.parents or target.parent == base:
        raise Refused("каталог выхода — только внутри кэша прогона (<кэш>/<штамп>/<каталог>)")


def refuse_inside(out: pathlib.Path, *roots: pathlib.Path) -> None:
    """Созданный каталог выхода не лежит внутри корня данных и корня кода — по идентичности
    файлов, а не по строкам: APFS не различает регистр, и другой регистр пути строковую
    проверку проходит (граница приватности)."""
    for root in roots:
        if not root.exists():
            continue
        for p in (out, *out.parents):
            if os.path.samefile(p, root):
                raise Refused("каталог выхода внутри данных владельца или кода")


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
            self._row({"k": "write", "t": round(self._clock(), CLOCK_DIGITS), "sent": self.sent})

    def front(self, message: dict) -> None:
        if message.get("type") != "front":
            return
        with self._lock:
            self.fed = int(message.get("fed", self.fed))
            row = {"k": "front", "t": round(self._clock(), CLOCK_DIGITS), "fed": self.fed}
            if message.get("final"):
                row["final"] = True           # финал — close() модели, не шаг: сверка его не считает
            self._row(row)
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
    процесс — в обёртке записи. Аргументы ребёнка — как у `start()`, лимит кэша MLX
    тоже: прогон меряет память в том же режиме, что в бою."""
    def wrapped(python, script, args, **kw):
        on_message = kw["on_message"]

        def tapped(message: dict) -> None:
            witness.front(message)
            on_message(message)
        kw["on_message"] = tapped
        stream, out = spawn(python, script, args, **kw)
        return (_StreamProxy(stream, witness) if stream is not None else None), out
    return wrapped


LINKS = ("models", "engines")


def run_root(out: pathlib.Path, data_root: pathlib.Path) -> pathlib.Path:
    """Корень прогона: свежий каталог в кэше, `logs/` свой, веса и окружение движка — ссылками.
    Место проверяется здесь, где каталог создаётся, а не у вызывающего."""
    allowed_out(out)
    if out.exists():
        raise Refused("каталог выхода уже есть — журнал открыт на дозапись, прогоны смешались бы")
    charoite_paths.secure_dir(out)                # каталоги данных — дверью канона, 0700 (класс №409)
    try:
        refuse_inside(out, data_root, charoite_paths.CODE_ROOT)
    except Refused:
        shutil.rmtree(out)
        raise
    charoite_paths.secure_dir(out / "logs")
    for name in LINKS:
        target = data_root / name
        if target.exists():
            (out / name).symlink_to(target)
    return out


def unlink_links(root: pathlib.Path) -> None:
    """Ссылки на веса и окружение нужны только живому ребёнку: оставленные в сохраняемом
    каталоге, они довели бы уборку по ссылкам до данных владельца."""
    for name in LINKS:
        (root / name).unlink(missing_ok=True)


def chunk_decision(tracker, placed, *, chan, stt_runtime, jobs_for, heard_pieces, diarized_state):
    """Зеркало ветки чанка демона без STT: (состояние для тени, путь, интервалы на оси).

    Путь: `split_failed` — раскладка упала (канальная метка); `excluded` — заданий STT
    нет (`jobs_for` отдал None: придержка, микро-куски или, на микрофоне, только куски без
    голоса); `pieces` — окна; `whole` — чанк целиком одним голосом (`pieces is None`, голос
    `main`, может быть None). Какие куски стали заданиями, решает то же правило, что у
    демона (`heard_pieces`, №571); кусок без голоса идёт в интервалы с голосом None —
    метка канала, как у сбоя раскладки. Признак канала спрашивается у `chan`
    (`ChannelLabels`, собранный как у демона): на микрофоне метка канала подписывает
    владельца."""
    plan = stt_runtime.diarization_plan(lagging=False,
                                        has_split=stt_runtime.has_split_tracker(tracker))
    if plan != "diarize":
        raise Refused(f"план чанка {plan!r}: в прогоне ждали раскладку трекером")
    res, split_failed = stt_runtime.guarded_split(tracker, placed.chunk, placed.speaker)
    neutral = chan.label_names_nobody(placed.speaker)
    jobs = jobs_for(res, placed.chunk, channel_label_neutral=neutral)
    state = diarized_state(split_failed, jobs)
    start = int(placed.start)
    if split_failed:
        return state, "split_failed", []
    if jobs is None:
        return state, "excluded", []
    if res.pieces:
        heard = heard_pieces(res, channel_label_neutral=neutral)
        return state, "pieces", [
            (start + p.raw_start, start + p.raw_end,
             None if n == stt_runtime.CHANNEL_LABEL_ONLY else int(n))
            for p, (_piece, n, _raw) in zip(heard, jobs)]
    return state, "whole", [(start, start + len(placed.chunk), res.main)]


def replay(stamp: str, *, data_root: pathlib.Path, out: pathlib.Path, lead_s: float = LEAD_S,
           preroll_s: float = PREROLL_S, block_s: float = BLOCK_S, say=None,
           memory=None) -> dict:
    """Прогнать запись встречи `stamp`; вернуть сводку (`meta.json`)."""
    import audio
    import channel_labels
    import config_loader
    import diarize_live
    import foreign_python
    import live_nemotron
    import meeting_stamp
    import stt_runtime

    # штамп встречи в строках тени — только маской: вывод прогона вставляют в отчёты
    tell = say or _stderr

    def say(s: str) -> None:
        tell(redact(s, stamp))
    cfg = replay_config(config_loader.load_user_or_example(data_root), live_nemotron.SHADOW)
    sr = int(cfg["audio"]["samplerate"])
    rec_dir = data_root / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    paths = [meeting_stamp.recording_path(rec_dir, stamp, label, "wav") for label in LABELS]
    missing = [label for label, p in zip(LABELS, paths) if not p.exists()]
    if missing:
        raise Refused(f"нет записей каналов: {', '.join(missing)}")
    sweep_orphans(rec_dir)
    bh, mic = pad_equal(*(read_channel(p, sr) for p in paths))

    models = data_root / charoite_paths.MODELS_DIR / "diar"
    seg_model, emb_model = models / "segmentation.onnx", models / "embedding.onnx"
    if diarize_live.tracker_kind(seg_model, emb_model) != "segments":
        raise Refused("нет моделей трекера по кускам речи (models/diar)")
    root = run_root(out, data_root)
    mark_owner(root.parent, rec_dir)

    hub = audio.AudioHub(cfg, stamp=stamp, captures=[])
    captures = [types.SimpleNamespace(label=label) for label in LABELS]
    hub._register_captures(captures)
    # метки каналов — тем же входом, что у демона: признак «метка никого не называет»
    # для кусков без голоса (№571) не собирается здесь своим правилом
    chan = channel_labels.ChannelLabels.from_capture(
        cfg, mic_raw=hub.SPEAKER["mic"], other=hub.SPEAKER["blackhole"])
    tracker = diarize_live.live_tracker(seg_model, emb_model, sample_rate=hub.sr, chunk_s=hub.chunk_s,
                                        overlap_s=hub.overlap_s, mic_channel=chan.mic_raw)

    witness = Witness(root / "timing.jsonl")
    original_begin = live_nemotron.Shadow.begin

    def begin(self, **kw):
        kw.setdefault("spawn", witness_spawn(witness, foreign_python.spawn_stream))
        return original_begin(self, **kw)

    trk = open(root / "tracker.jsonl", "w", buffering=1, encoding="utf-8")
    live_nemotron.Shadow.begin = begin
    try:
        shadow = live_nemotron.start(cfg, root=root, stamp=stamp, sr=hub.sr, labels=LABELS, say=say,
                                     **({"memory": memory} if memory is not None else {}))
    except BaseException:
        trk.close()
        witness.close()
        unlink_links(root)
        raise
    finally:
        live_nemotron.Shadow.begin = original_begin
    if not isinstance(shadow, live_nemotron.Shadow):
        trk.close()
        witness.close()
        unlink_links(root)
        raise Refused("тень не поднялась (причина — строкой выше)")
    t_begin = time.monotonic()

    block = int(sr * block_s)
    counts = {"placed": 0, "tracker_lines": 0}

    def feed(lo: int, hi: int) -> None:
        for cap, samples in zip(captures, (bh, mic)):
            hub._consume(cap, samples[lo:hi])
        for placed in hub.pull_placed():
            counts["placed"] += 1
            state, path, intervals = chunk_decision(
                tracker, placed, chan=chan, stt_runtime=stt_runtime, jobs_for=diarize_live.jobs_for,
                heard_pieces=diarize_live.heard_pieces,
                diarized_state=live_nemotron.diarized_state)
            shadow.note_chunk(placed, state)
            if placed.seq[0] == live_nemotron.CHANNEL:
                counts["tracker_lines"] += 1
                trk.write(json.dumps({"chunk": placed.seq[1], "start": int(placed.start),
                                      "end": int(placed.start) + len(placed.chunk), "state": state,
                                      "path": path, "intervals": intervals}) + "\n")

    dead = lambda: shadow.state == live_nemotron.DEAD  # noqa: E731
    stopped = False
    try:
        pos = 0
        pre = int(sr * preroll_s)
        while pos < pre:                                  # до рукопожатия: тень ещё не слушает
            feed(pos, min(pre, pos + block))
            pos = min(pre, pos + block)
        wait_for(lambda: shadow.state != live_nemotron.STARTING, HANDSHAKE_WAIT_S)
        if shadow.state != live_nemotron.LIVE:
            raise Refused(f"тень не вышла в поток: {shadow.state} ({shadow.reason})")
        # слушатель — после предзвука и рукопожатия: поток начинается ровно с `pre`, как бы
        # быстро ни поднялся ребёнок (в бою начало потока — первый блок после рукопожатия)
        shadow.attach(hub)
        handshake_s = time.monotonic() - t_begin
        start0 = pos
        lead = int(sr * lead_s)
        n = len(bh)
        t_feed = time.monotonic()
        last_fed, front_at = witness.fed, t_feed
        while pos < n:
            if shadow.state != live_nemotron.LIVE:
                raise Refused(f"тень умерла посреди прогона: {shadow.reason}")
            now = time.monotonic()
            if witness.fed != last_fed:
                last_fed, front_at = witness.fed, now
            step, target = pacing(pos, start0, witness.fed, lead, now, front_at)
            if step == STALL:
                raise Refused(f"ребёнок не выдал фронт за {STALL_S:.0f} с при поданном звуке")
            if step == WAIT:
                witness.wait_fed(target, timeout=FED_WAIT_S)
                continue
            feed(pos, min(n, pos + block))
            pos = min(n, pos + block)
        feed_wall = time.monotonic() - t_feed
        # отсрочка убийства — с запасом на хвост очереди: ребёнок, убитый по таймеру, — брак
        # прогона, а не цифры; `close` дописывает журнал (с №533 его пишет своя нить)
        shadow.stop(grace=END_WAIT_S)
        wait_for(dead, END_WAIT_S)
        shadow.close(timeout=CLOSE_WAIT_S)
        stopped = True
    finally:
        if not stopped:                  # отказ посреди прогона: ребёнка — остановить, журнал — дописать
            shadow.stop(grace=ABORT_GRACE_S)
            wait_for(dead, ABORT_WAIT_S)
            shadow.close(timeout=CLOSE_WAIT_S)
        trk.close()
        witness.close()
        unlink_links(root)
    expect = expected_cuts(n, sr, hub.chunk_s, hub.overlap_s)
    check_cuts(hub.chunk_no, LABELS, expect)
    if not dead():
        raise Refused(f"тень не закончилась за {END_WAIT_S:.0f} с после стопа")
    journal = charoite_paths.meeting_log(root, "nemotron_live", stem=stamp, suffix=".jsonl")
    ended = end_line(journal)
    if ended.get("ending") != live_nemotron.END_STOPPED or ended.get("exit") != "ok":
        raise Refused(f"тень кончилась не штатно: {ended.get('reason')!r}, ending={ended.get('ending')!r}, "
                      f"exit={ended.get('exit')!r}")
    meta = {"stamp": stamp, "sr": sr, **run_numbers(n, start0, sr, handshake_s, feed_wall),
            "cuts_per_channel": expect, "chunks": counts,
            "lead_s": lead_s, "block_s": block_s, "preroll_s": preroll_s,
            "cache_limit_mb": live_nemotron.CACHE_LIMIT_MB,
            "journal": str(journal.relative_to(root))}
    (root / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


#: Ключи сводки, которые печатаются: агрегаты, без штампа и путей.
PRINTED = ("sr", "audio_s", "start0", "fed_to_shadow", "cuts_per_channel", "chunks", "handshake_s",
           "wall_s", "speed_x", "lead_s", "block_s", "preroll_s", "cache_limit_mb")


def main(argv: list[str] | None = None) -> int:
    charoite_paths.harden_umask()      # журналы прогона — только владельцу
    # корень — до разбора аргументов: без названного корня вход отказывает своим кодом
    data_root = charoite_paths.name_data_root_or_exit(__file__)
    ap = argparse.ArgumentParser(description="Прогон записи через тень потокового Nemotron (№478 B)")
    ap.add_argument("stamp", help="штамп записи: <штамп>_blackhole.wav и <штамп>_mic.wav в recordings/")
    ap.add_argument("--out", type=pathlib.Path, help="свежий каталог выхода (по умолчанию — кэш пользователя)")
    ap.add_argument("--lead", type=float, default=LEAD_S, help="секунд звука впереди ребёнка")
    ap.add_argument("--preroll", type=float, default=PREROLL_S, help="секунд звука до рукопожатия")
    args = ap.parse_args(argv)
    out = args.out or default_out(args.stamp)
    try:
        meta = replay(args.stamp, data_root=data_root, out=out, lead_s=args.lead, preroll_s=args.preroll)
    except Refused as e:
        print(f"прогон не состоялся: {redact(str(e), args.stamp)}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — трейсбек несёт пути и штамп; причина — маской
        print(f"прогон упал: {type(e).__name__}: {redact(str(e), args.stamp)}", file=sys.stderr)
        return 1
    # в сводку — только агрегаты: штамп встречи и домашний путь сюда не попадают (её вставляют в отчёт)
    print(json.dumps({k: v for k, v in meta.items() if k in PRINTED}, indent=1))   # только числа
    return 0


if __name__ == "__main__":
    sys.exit(main())
