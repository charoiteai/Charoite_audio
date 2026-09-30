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
import hashlib
import live_sidecar
import json
import meeting_source
import channel_trace
import os
import pathlib
import re
from charoite_graph import safe_write
import transcript
import subprocess
import sys
import time
import typing
import wave

from charoite_paths import code_root, harden_umask, log_path, meeting_log, resolve_root


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень: половина процесса жила в названном корне,
    половина — в выведенном из положения файла, и расхождение было немым
    (замер 21.09, №329).
    """
    return resolve_root(__file__)
CODE = code_root(__file__)
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import numpy as np  # noqa: E402
import yaml  # noqa: E402

import install_profile  # noqa: E402
import action_items  # noqa: E402
import fact_check  # noqa: E402
import channel_labels  # noqa: E402
import speaker_names  # noqa: E402
import graphs  # noqa: E402
import lexicon  # noqa: E402
import owner_voice as owner_voice_rules  # noqa: E402
import live_gate  # noqa: E402
import meeting_stamp  # noqa: E402
from diarize import diarize  # noqa: E402 — pyannote-сегментация + эмбеддинги, весь файл
import diarize_nemotron  # noqa: E402 — Nemotron процессом чужого интерпретатора (№473)
from exit_codes import EXIT_NO_GRAPH, EXIT_NO_SPEECH  # noqa: E402
from meeting_processing import MeetingStatusStore, find_meeting_note  # noqa: E402
from stt import STT  # noqa: E402
from transcript import Transcript, is_noise  # noqa: E402

SEG_S, OVERLAP_S = 25.0, 1.0
WAIT_WAV_S = 45  # демон финализирует .wav параллельно нашему старту
# Строка в шапке стенограммы, когда имена не разобраны. Живёт в самом файле, а
# не только в логе: человек открывает стенограмму, а не logs/, и «Собеседник
# 1..5» без объяснения читается как «программа не умеет». Причин две, и у
# каждой свой текст (№499): одна — общее начало, по нему names_pending узнаёт
# обе плашки и стенограммы, записанные до разделения.
NAMES_PENDING_PREFIX = "> ⚠️ Имена участников не определены"
# Модель молчала (пустой ответ, не-JSON, исключение): повтор, когда модель
# свободна, — честный совет.
NAMES_PENDING_NOTE = (
    f"{NAMES_PENDING_PREFIX}: модель не ответила на разборе. "
    "Метки остались «Собеседник N» — пересоберите встречу, когда модель "
    "свободна (кнопка «Пересобрать» или src/rebuild_transcript.py)."
)
# Модель ответила, но гварды доверия отвергли каждое имя: не звучало в
# разговоре, обращение к другому, владелец. Пересборка на том же тексте
# упрётся в те же гварды, поэтому совета «пересобрать» здесь нет (29.09: оба
# отказа на боевой встрече были верными, а плашка звала пересобрать).
NAMES_REJECTED_NOTE = (
    f"{NAMES_PENDING_PREFIX}: модель ответила, но ни одно из предложенных "
    "имён ({proposed}) не прошло проверку — не звучало в разговоре, "
    "обращение к другому, имя владельца или не имя. Метки остались "
    "«Собеседник N» — впишите имена вручную."
)


#: Строка в шапке стенограммы, когда голоса собеседников размечены не тем
#: движком, что выбран в настройках: сам откат виден в файле, а не только в
#: logs/ (как у NAMES_PENDING_NOTE). В `{reason}` идут только закреплённые
#: фразы без путей машины: стенограмму пересылают людям (№495).
ENGINE_FALLBACK_NOTE = "> ⚠️ Голоса собеседников размечены запасным движком (sherpa): {reason}."
#: Строка в шапке, когда разметка микрофона без звонка свела всех в одну метку,
#: а живая сессия слышала несколько голосов (№559): имя одного человека на всех
#: было бы ложью, поэтому метка остаётся нейтральной. Своя строка, не
#: NAMES_PENDING_PREFIX: совет «пересобрать» тут не поможет — пересборка
#: упрётся в ту же разметку.
MIC_COLLAPSED_NOTE = ("> ⚠️ Голоса в микрофоне слились в одну метку, хотя живая стенограмма "
                      "слышала {live} — имена не присвоены, впишите их вручную.")
#: Причина в шапке, когда Nemotron не разметил. Сырой отказ движка — путь
#: интерпретатора, OSError, последняя строка stderr с путями весов и рецептом
#: `hf download --local-dir …` — несёт имя учётки и уходит только в журнал
#: разбора; человеку, которому переслали стенограмму, диагноз не нужен, а
#: владельцу его покажут журнал и доктор (№495).
ENGINE_REFUSED_REASON = ("Nemotron не разметил голоса — причина в журнале разбора (logs/), "
                         "проверка — доктор (scripts/doctor.py)")
#: Движки разметки канала собеседников (`sufler.diarize_backend`).
DIARIZE_BACKENDS = ("sherpa", "nemotron")
#: Потолок Nemotron: запуск интерпретатора и загрузка весов плюс десятая доля
#: длительности записи (замер 28.09 с запуском процесса и загрузкой модели:
#: 41 минута — 5 с, 20 минут — 3 с, 8 минут — 2 с).
NEMOTRON_TIMEOUT_S = 60.0
#: Доля записи, которую один-единственный отрезок движка не может покрыть,
#: оставаясь правдоподобным ответом (см. `call_channel_engine`).
DEGENERATE_SHARE = 0.9


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
    return live_gate.daemon_alive(_root())


def _yield_to_live(what: str, cap: float | None = None) -> None:
    """Пока идёт живая встреча, тяжёлую модель не трогаем — см. live_gate.

    `cap` — потолок ожидания: внутри уже взятой очереди пересборок
    (rebuild.lock) бесконечная уступка парковала бы очередь на всю чужую
    встречу; после потолка вызов идёт в тесноте, с логом (GLM r2 по #483).
    """
    live_gate.wait_while_live(_root(), log, what=what, poll=10, cap=cap)


def _take_rebuild_queue():
    """Одна пересборка за раз на машину: лок logs/rebuild.lock до конца процесса.

    Без него Стоп встречи запускал две пересборки разом: сирота, отпущенная
    гейтом живой встречи, просыпалась в ту же секунду, что и свежая — два
    полных конвейера STT+диаризация+модель параллельно, тот самый залп,
    от которого строилась цепочка сирот (12.08; ревью 18.08). Ждём молча
    не дольше секунды, дальше — с записью в лог.
    """
    path = _root() / "logs" / "rebuild.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path.open("a")
    except OSError as e:
        log(f"очередь пересборок недоступна ({type(e).__name__}) — иду без неё")
        return None
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except BlockingIOError:
        log("другая пересборка ещё идёт — жду своей очереди")
    except OSError:
        return f          # flock не поддержан (сетевой том) — не блокируемся
    started = time.time()
    fcntl.flock(f, fcntl.LOCK_EX)     # блокирующе: очередь, а не отказ
    log(f"очередь пересборок подошла (ждал {int(time.time() - started)} с)")
    return f


def stt_segment(stt: STT, audio: np.ndarray, sr: int) -> str:
    parts, prev = [], ""
    seg, ov = int(SEG_S * sr), int(OVERLAP_S * sr)
    for off in range(0, len(audio), seg - ov):
        piece = audio[off:off + seg]
        if len(piece) < sr * 0.6:
            break
        t = stt.transcribe(piece, sr).strip()
        if not t or is_noise(t):
            continue
        t = Transcript._cut_overlap(prev, t) if prev else t
        if t:
            parts.append(t)
            prev = parts[-1]
    return " ".join(parts)


#: Отрезок разметки короче этого (с) выбрасывается до разбора голосов — у любого
#: движка: осколок не несёт реплики, а своё STT на полсекунды звука — шум.
MIN_SEGMENT_S = 1.0


def diarize_channel(audio: np.ndarray, sr: int, min_len: float = MIN_SEGMENT_S,
                    num_speakers: int = -1,
                    merge_shards: bool | None = None) -> list[tuple[float, float, int]] | None:
    """Сегменты (start, end, cluster) канала; короче min_len — отброшены.

    num_speakers > 0 — число кластеров из живой сессии: авто-режим на моно-миксе
    плодит осколки (21.07: 14 «голосов» на встрече, где живьём их было 8).
    merge_shards=True — после подсказки ещё склейка осколков: число становится
    верхней границей (см. diarize.diarize; микрофон очной встречи, №559).
    Сбой разметки — None, как «канал не размечали» (контракт None/[] — в
    докстринге resolve_channel_segments): пустой список значил бы «речи нет»,
    а канал собеседников, размеченный пустым, — это молчание (№559).
    """
    kw = {} if merge_shards is None else {"merge_shards": merge_shards}
    try:
        return [(s, e, k) for s, e, k in diarize(audio, sr, num_speakers=num_speakers, **kw)
                if e - s >= min_len]
    except Exception as e:  # noqa: BLE001
        log(f"диаризация канала не удалась: {e}")
        return None


def call_channel_engine(cfg: dict, wav: pathlib.Path,
                        duration_s: float) -> tuple[list[tuple[float, float, int]] | None, str]:
    """Разметка канала собеседников выбранным движком, если это не sherpa.

    `(сегменты, "")` — разметил Nemotron; `(None, причина)` — выбранный движок
    не разметил, размечает sherpa, а причина уходит в шапку стенограммы: при
    отказе Nemotron — закреплённая фраза ENGINE_REFUSED_REASON (сырой отказ с
    путями машины — только в журнал, №495), в остальных случаях — своя фраза
    без путей;
    `(None, "")` — выбран sherpa. Отрезки короче MIN_SEGMENT_S отбрасываются, как
    у sherpa в `diarize_channel`.
    """
    sufler = cfg.get("sufler") or {}
    backend = str(sufler.get("diarize_backend") or "sherpa").strip().lower()
    if backend == "sherpa":
        return None, ""
    if backend not in DIARIZE_BACKENDS:
        reason = f"движок {backend!r} неизвестен (sufler.diarize_backend: {', '.join(DIARIZE_BACKENDS)})"
        log(reason)
        return None, reason
    t0 = time.time()
    # Раскладку движка (веса, установленное окружение) знает его модуль: отсюда
    # уходят только настройка и корень данных (№474).
    out = diarize_nemotron.diarize_in_env(
        str(sufler.get("nemotron_python") or ""), wav,
        root=_root(), timeout=NEMOTRON_TIMEOUT_S + 0.1 * duration_s)
    if not out.ok:
        log(f"Nemotron не разметил голоса ({out.kind}: {out.reason}) — размечает sherpa")
        return None, ENGINE_REFUSED_REASON
    segs = [(s, e, k) for s, e, k in out.payload if e - s >= MIN_SEGMENT_S]
    log(f"Nemotron: {len(segs)} сегментов из {len(out.payload)} за {time.time() - t0:.0f} с")
    if not segs:
        # Успех без единого отрезка на записи длиннее 20 с — не «собеседники
        # молчали», а отказ движка в другой форме (не тот канал, частота,
        # дрейф API): канал собеседников иначе молча пустел бы целиком
        # (выходной круг 1 по №473, C1). Размечает sherpa, шапка говорит почему.
        return None, f"Nemotron — ни одного отрезка на {duration_s:.0f} с записи"
    if len(segs) == 1 and segs[0][1] - segs[0][0] >= DEGENERATE_SHARE * duration_s:
        # Один отрезок почти на всю запись — тоже вырожденный ответ: живая речь
        # рвётся паузами, а склейка Nemotron паузы не заклеивает (MERGE_GAP_S = 0).
        # Звонок один на один — это много отрезков одного голоса, не один (круг 2, M1).
        return None, f"Nemotron — один отрезок на всю запись ({duration_s:.0f} с)"
    return segs, ""


def overlap_frac(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(1e-6, a[1] - a[0])


#: Карлик канала собеседников: голос, набравший за встречу меньше этого (с), —
#: осколок кластеризации, а не отдельный человек. В звонке участники говорят
#: подолгу, и кластер короче 25 с почти наверняка кусок чьего-то голоса
#: (встреча 21.07: 23 «голоса» в канале звонка при 8 людях).
BH_DWARF_S = 25.0
#: Карлик микрофона. Порог с первой версии пересборки (21.07), отдельного
#: замера у него нет.
MIC_DWARF_S = 10.0
#: Карлик канала собеседников, когда голоса размечает Nemotron (№473). Sherpa
#: дробит голос на осколки, Nemotron — нет: замер 28.09 на пяти записях — голоса
#: с 10–22 с речи за встречу по ревизиям живые люди с одной-двумя репликами,
#: 2–4 с — шум. Микрофон остаётся на MIC_DWARF_S. Пересчёт после недели — №476.
NEMOTRON_BH_DWARF_S = 5.0
#: Реплика не длиннее этого (с) целиком внутри чужой отходит объемлющему голосу
#: и не режет чужую фразу на куски (см. `disjoint`).
NESTED_MIN_S = 1.0


def disjoint(segs: list[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
    """Разметка канала без перекрытий: каждый момент — у одного голоса.

    Перекрытие двух отрезков давало два STT одного звука (входной круг 3 по №473,
    C2). Правила для пары: частичное пересечение делится по середине; реплика
    целиком внутри чужой и длиннее NESTED_MIN_S режет объемлющую («до / она /
    после»); не длиннее — её время отходит объемлющей. Авторство короткой
    реплики теряется, как у слитого карлика, звук — нет: STT идёт по интервалу
    объемлющего отрезка, который её содержит (круг 5, I4). Объединение
    интервалов не меняется — эхо-фильтр микрофона не теряет окон (круг 4, C2).
    Несколько пересечений разбираются вставкой по началу, при равном начале —
    длинный раньше: короткие, начавшиеся вместе с ним, вырезаются из него как
    вложенные, и каждый голос сохраняет свою часть (обратный порядок поглощал
    средний голос целиком). Выход отсортирован по началу.
    """
    out: list[tuple[float, float, int]] = []
    for x in sorted(segs, key=lambda t: (t[0], t[0] - t[1])):
        if x[1] > x[0]:
            out = _insert(out, x)
    return out


def _insert(out: list[tuple[float, float, int]],
            x: tuple[float, float, int]) -> list[tuple[float, float, int]]:
    """Вставить `x` в отсортированную разметку без перекрытий по правилам `disjoint`."""
    s, e, k = x
    before = [o for o in out if o[1] <= s]
    after = [o for o in out if o[0] >= e]
    over = [o for o in out if o[1] > s and o[0] < e]
    if not over:
        return before + [x] + after
    first = over[0]
    if first[0] <= s and e <= first[1]:             # x целиком внутри чужого отрезка
        if e - s <= NESTED_MIN_S:
            return out
        parts = [(first[0], s, first[2]), x, (e, first[1], first[2])]
        return before + [p for p in parts if p[1] > p[0]] + after
    pieces: list[tuple[float, float, int]] = []
    cur = s                                          # начало ещё не размещённой части x
    for os_, oe, ok in over:
        if os_ < s:                                  # начался раньше x, кончается внутри
            mid = (s + oe) / 2
            pieces.append((os_, mid, ok))
            cur = mid
        elif oe <= e:                                # целиком внутри x
            if oe - os_ <= NESTED_MIN_S:
                continue                             # его время отходит x
            pieces += [(cur, os_, k), (os_, oe, ok)]
            cur = oe
        else:                                        # начался внутри x, кончается позже
            mid = (os_ + e) / 2
            pieces += [(cur, mid, k), (mid, oe, ok)]
            cur = oe
    pieces.append((cur, e, k))          # хвост x; пустой или вывернутый отсеет фильтр
    return before + [p for p in pieces if p[1] > p[0]] + after


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


def resolve_channel_segments(
        bh_raw: list[tuple[float, float, int]] | None,
        mic_raw: list[tuple[float, float, int]] | None, *,
        owner_label: str,
        bh_dwarf_s: float = BH_DWARF_S,
) -> tuple[list[tuple[float, float, str]], dict[str, str]]:
    """Сырые сегменты двух каналов → отрезки (start, end, метка) и канал-источник
    каждой метки («bh» / «mic»: по нему распознавание берёт звук).

    `bh_raw` / `mic_raw` — (start, end, номер кластера) от движка разметки;
    None — канал не размечали (записи нет, она короче 20 с или разметка не
    удалась), [] — размечали, речи нет. Ввода-вывода
    здесь нет: звук читает и движок зовёт `rebuild()`. Порядок — системный
    канал, затем микрофон: эхо в микрофоне отсекается по отрезкам собеседников,
    а «Собеседник N» микрофона продолжает нумерацию звонка. `owner_label` —
    подпись владельца из настроек (`ChannelLabels.mic_signed`); пустая —
    владельца не подписываем. Канал собеседников при любом движке сперва
    лишается перекрытий (`disjoint`), потом карликов (`bh_dwarf_s` — порог
    движка: BH_DWARF_S у sherpa, NEMOTRON_BH_DWARF_S у Nemotron).
    """
    segments: list[tuple[float, float, str]] = []  # (start, end, метка)
    chan: dict[str, str] = {}  # метка → канал-источник звука
    next_n = 1

    # Объявляем ДО ветки: на mic-only машине (нет BlackHole или не выдано
    # разрешение на системный звук) блок ниже не выполняется, а `bh_segs`
    # читается дальше в `call=bool(bh_segs)`. Без объявления там NameError,
    # который `main()` глотает как «пересборка не удалась» — и встреча молча
    # остаётся без разбора по голосам, распознавания по абзацам и имён, а
    # через record_keep_days запись удаляется и вернуть качество уже нечем
    # (ревью 20.08, GLM).
    bh_segs: list[tuple[float, float, int]] = []
    if bh_raw is not None:
        bh_segs = merge_dwarfs(disjoint(bh_raw), bh_dwarf_s)
        mapping: dict[int, str] = {}
        for s, e, k in bh_segs:
            if k not in mapping:
                mapping[k] = f"Собеседник {next_n}"
                chan[mapping[k]] = "bh"
                next_n += 1
            segments.append((s, e, mapping[k]))
        log(f"blackhole: {len(bh_segs)} сегментов, голосов {len(mapping)}")

    if mic_raw is not None:
        # Эхо динамиков: mic-сегмент, больше чем наполовину накрытый речью
        # собеседников, выбрасываем. Доля — по ОБЪЕДИНЕНИЮ их отрезков (они уже без
        # перекрытий, `disjoint`), а не по одному: Nemotron режет реплику на куски,
        # и эхо поверх трёх кусков по 30 % каждый проходило как живая речь. Замер
        # 28.09, встреча 8 минут: по одному отрезку в микрофоне оставалось 69 %
        # речи против 17.5 % у sherpa, по объединению — 17.8 % (№473).
        bh_iv = [(s, e) for s, e, _ in segments]
        mic_segs = [t for t in mic_raw
                    if not sum(overlap_frac((t[0], t[1]), iv) for iv in bh_iv) > 0.5]
        mic_segs = merge_dwarfs(mic_segs, MIC_DWARF_S)
        durs: dict[int, float] = {}
        for s, e, k in mic_segs:
            durs[k] = durs.get(k, 0.0) + (e - s)
        # Владелец — тот, чей голос ЯВНО преобладает в своём микрофоне.
        # Просто «самый долгий» брать нельзя: это ровно то доминирование,
        # от которого отказались 20.07, и в гибридной встрече (коллега
        # рядом говорит дольше) имя владельца досталось бы коллеге —
        # причём теперь настоящее имя, а не безобидный литерал
        # «владелец» (ревью 19.08, DeepSeek). Пороги — те же, что у
        # живой ленты, чтобы финал не переписывал её решение.
        # Пересборка — НЕЗАВИСИМАЯ переоценка по всей записи, а не
        # повтор решения живой ленты: там скользящее окно и инерция,
        # здесь сырые суммы за встречу. Для звонка, где владелец один
        # в микрофоне, обе дают одно и то же; на встрече с переломом
        # формата могут разойтись — и финал, у которого есть вся
        # запись, честнее (ревью 19.08, второй круг).
        #
        # Эхо здесь отсекает геометрия (пересечения с сегментами
        # системной дорожки убраны выше), а не совпадение номеров
        # голосов: нумерация двух дорожек независима.
        heard = owner_voice_rules.Heard(mic=dict(durs), call=bool(bh_segs))
        owner_voice = owner_voice_rules.owner_voice(heard)
        # Имя — из настроек: живая лента подписывает владельца именем из
        # того же ключа, и финальная стенограмма обязана говорить то же
        # самое. Раньше здесь стоял литерал, и после Стопа человек в
        # своей же встрече переименовывался. Пустое имя — «Я», как в
        # хабе захвата (audio.py), а не «владелец»: иначе безымянный
        # переименовывается по-прежнему.
        # Правило одно с живой лентой и захватом — ChannelLabels (D-П2):
        # имя из настроек, пустое — «Я»; имя, совпавшее с нейтральной
        # меткой («Собеседник 2»), подписью быть не может — по метке
        # выбирается дорожка для распознавания, и реплики удалённого
        # собеседника поехали бы из микрофонного аудио (ревью 19.08).
        if not owner_label:
            owner_voice = None
        mapping = {}
        for s, e, k in mic_segs:
            if k not in mapping:
                if k == owner_voice:
                    mapping[k] = owner_label
                else:
                    mapping[k] = f"Собеседник {next_n}"
                    next_n += 1
                chan[mapping[k]] = "mic"
            segments.append((s, e, mapping[k]))
        log(f"mic: {len(mic_segs)} сегментов, голосов {len(durs)}, "
            f"владелец: {'по преобладанию' if owner_voice is not None else 'не назначен'}")
    return segments, chan


def paragraphs(segments: list[tuple[float, float, str]], gap: float = 2.0) -> list[list]:
    """Склейка соседних кусков одного голоса (зазор < gap) — цельные абзацы.

    Вход сортируется по началу устойчиво: при равном начале отрезок звонка
    остаётся раньше микрофонного, как их вернул `resolve_channel_segments`.
    """
    merged: list[list] = []
    for s, e, spk in sorted(segments, key=lambda t: t[0]):
        if merged and merged[-1][2] == spk and s - merged[-1][1] < gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, spk])
    return merged


def _cut_lines(text: str, limit: int) -> str:
    """Первые `limit` знаков, но по границе строки (или слова, если строка
    одна): обрезок слова с заглавной («Лен» от «Ленинградское») стал бы для
    гвардов доверия формой имени «Лена» (GLM I1 по #551)."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    sep = "\n" if "\n" in head else " "
    return head.rsplit(sep, 1)[0] if sep in head else head


def known_first_names(cfg: dict) -> tuple[str, ...]:
    """Первые имена людей графа — для приведения падежей (правило 4
    speaker_names), тем же путём, что у живого опознания в демоне."""
    try:
        root = graphs.graph_dir(cfg)
        people = root / "Люди" if root else None
        if people is None or not people.is_dir():
            return ()
        return tuple(sorted({q.stem.split()[0] for q in people.glob("*.md")
                             if q.stem.strip() and not q.stem.startswith("Собеседник")}))
    except OSError:
        return ()


def _sample_line(spk: str, text: str) -> str:
    """Строка sample для модели и гвардов — «[·] метка: текст»: формат хвоста
    живой стенограммы («] метка:» читают гварды), без номера в скобках —
    номер подталкивал бы модель ключевать ответ им, а не меткой (DS M4 по #551)."""
    return f"[·] {spk}: {text}"


class NamesOutcome(typing.NamedTuple):
    """Итог разбора имён моделью — значением, не bool «ответила ли» (№499).

    `outcome` — ANSWERED (ответ годен, даже если имён в разговоре не звучало),
    SILENT (молчание, не-JSON, исключение) или REJECTED (модель предложила
    `proposed` имён, гварды отвергли все). У двух последних своя плашка в
    шапке: совет «пересобрать» честен только для молчания.
    """
    names: dict[str, str]
    outcome: str
    proposed: int = 0

    # Без аннотации: в теле NamedTuple аннотированное имя — поле кортежа (№477).
    ANSWERED = "answered"
    SILENT = "silent"
    REJECTED = "rejected"


def name_speakers(cfg: dict, lines: list[tuple[str, str]],
                  known: tuple[str, ...] = ()) -> NamesOutcome:
    """qwen: «Собеседник N» ↔ имена из разговора; владельца не трогаем.

    Возвращает NamesOutcome: имена и исход. Исход — не педантизм: пустой
    словарь означает и «имён в разговоре не звучало», и «модель молчала, ответ
    не разобрался». 12.08 случилось второе, стенограмма ушла с «Собеседник
    1..5», а прогон записался успешным — та же тихая деградация, которую
    чинили в ночных досье. Различаем: первое нормально, второе стоит показать.
    Ответ, которым нельзя воспользоваться, — тоже не годный ответ: если гварды
    отвергли всё, что предложила модель, плашка «имена не определены» обязана
    остаться (критика DS по #551) — но со своей причиной, не «модель молчала»
    (№499).

    Ответ модели — кандидат, не приговор: каждое имя проходит те же гварды
    доверия, что живое опознание в демоне (speaker_names.trustworthy_name):
    владелец по словам `user_name`, а не по полной строке («Игорь» против
    «Игорь Ветров»); имя, которого в разговоре не слышно, — выдумано;
    имя только в собственных репликах метки без представления — обращение
    к другому. До аудита зон 12.09 (зона 1) пересборка верила модели почти
    на слово — и цена ошибки здесь та же: метка переписывается по всей
    стенограмме, минутки и граф наследуют её молча.
    """
    import llm
    user_name = str((cfg.get("sufler") or {}).get("user_name") or "")
    # Формат хвоста живой стенограммы — «[…] метка: текст»: его читают
    # гварды доверия («] метка:» — реплика самой метки). Времени у строк
    # пересборки тут нет, в скобках — номер реплики.
    sample = _cut_lines("\n".join(_sample_line(spk, text) for spk, text in lines if text), 7000)
    # Уступка живой встрече — с потолком: очередь пересборок (rebuild.lock)
    # уже взята, бесконечное ожидание парковало бы её на всю чужую встречу
    # (тот же потолок, что у минуток).
    _yield_to_live("имена", cap=600)
    try:
        raw = llm.LLM(cfg).complete(
            sample,
            system=(
                "По репликам определи ЛИЧНЫЕ имена говорящих (Сергей, Юля). "
                "КРИТИЧНО: имя внутри реплики — почти всегда ОБРАЩЕНИЕ к ДРУГОМУ "
                "(«Саш, а ты…» говорит НЕ Саша; Саша — тот, кто отвечает следом). "
                "Имя присваивается говорящему только если он представился сам или "
                "ответил сразу после обращения к нему. Имена — в именительном "
                "падеже (Таня, не Тань). Обращения («коллеги», «ребята»), "
                "должности, названия компаний и междометия именем НЕ являются — "
                "для них «?». Верни СТРОГО JSON {\"Собеседник 1\":\"Имя\","
                "\"Собеседник 2\":\"?\"} — «?» если имя не звучало. Не выдумывай."),
            model=cfg["llm"]["model"], json_format=True, think=False,
            num_ctx=8192, timeout=240)
        # Ответ разбираем терпимо к прозе и ```-заборам: сборка сервера без
        # грамматики отвечает текстом, и объект в нём ещё надо найти.
        data = llm.parse_json_block(raw)
        if data is None:
            log("имена: не удалось ("
                + (f"модель ответила не-JSON ({len(raw)} знаков)" if raw
                   else "пустой ответ") + ")")
            return NamesOutcome({}, NamesOutcome.SILENT)
    except Exception as e:  # noqa: BLE001
        log(f"имена: не удалось ({e})")
        return NamesOutcome({}, NamesOutcome.SILENT)
    names: dict[str, str] = {}
    proposed = 0
    for k, v in data.items():   # parse_json_block отдаёт только объект или None — None уже выше
        if not (isinstance(k, str) and k.startswith("Собеседник") and isinstance(v, str)):
            continue
        if v.strip() in ("", "?"):
            continue
        verdict = speaker_names.judge_name(v, sample=sample, label=k, owner_name=user_name,
                                           known=known)
        if verdict.name:
            proposed += 1
            names[k] = verdict.name
        elif speaker_names.is_refusal(verdict):
            # какое правило отказало — в журнал; шапка остаётся общей (№502). «NONE» —
            # ответ «имени нет», не предложенное имя: плашку «отвергнуты» не зовёт
            proposed += 1
            log(f"имена: {speaker_names.refusal_line(k, verdict)}")
    # Годный ответ — объект, и хоть чем-то из него можно воспользоваться (массив
    # или строка под json_format — тот же мусор, что молчание, GLM M1 по #551:
    # parse_json_block отдаёт на них None, и это молчание выше). Всё предложенное
    # отвергнуто — отдельный исход со своей плашкой (№499). Владелец определён
    # каналом и в ответе не ждётся.
    if proposed and not names:
        log(f"имена: модель предложила {proposed}, гварды отвергли все — плашка «имена не определены» с этой причиной")
        return NamesOutcome({}, NamesOutcome.REJECTED, proposed)
    return NamesOutcome(names, NamesOutcome.ANSWERED, proposed)


def live_meta(live: pathlib.Path) -> dict:
    """Данные живой сессии рядом со стенограммой: число голосов и опознанные имена.

    Пишет демон при завершении встречи (<стенограмма>.live.json). Нет файла —
    работаем как раньше: авто-кластеризация и определение имён по репликам.
    """
    try:
        p = live_meta_path(live)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001 — подсказка не обязательна
        log(f"live.json не прочитан: {e}")
    return {}


def live_meta_path(live: pathlib.Path) -> pathlib.Path:
    """Где лежит live.json этой встречи — единый резолвер live_sidecar:
    свой файл, иначе единственный сайдкар минуты (наследие до 0.69.1),
    неоднозначно — путь под своим именем (его нет → мета пустая, имена
    соседки не подставляются; GLM advisory r2 по #489)."""
    p = live_sidecar.sidecar_for(live)
    return p if p is not None else live.with_name(live.name + ".live.json")


def live_session_names(meta: dict) -> dict[str, str]:
    """Имена живой сессии из live.json — с санитайзом формы.

    live.json может быть битым или правленным руками («names»: список, число
    вместо имени): без фильтра исключение вылетало бы ПОСЛЕ write_final и
    хоронило перештамповку, а прогон записывался как «пересборка не удалась»
    при живой стенограмме (DS r2 M1 / GLM r2 M3 по #464).
    """
    d = meta.get("names")
    if not isinstance(d, dict):
        return {}
    return {k: v.strip() for k, v in d.items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()}


#: Сколько голосов живой сессии пересборка вообще готова принять за правду:
#: больше — правленый руками или битый сайдкар, числу не верим.
MAX_LIVE_SPEAKERS = 60
#: Подсказка числа голосов кластеризатору: вне этого диапазона — авто-режим.
HINT_RANGE = (2, 12)
#: Микрофону подсказка идёт с этого числа: на двоих авто справляется, а
#: монолог, раздробленный живым трекером на 2 метки, не режем (№559).
MIC_HINT_MIN = 3


def speakers_count(meta: dict) -> int | None:
    """Сколько голосов слышала живая сессия (`speakers` в live.json) — с санитайзом
    формы, как у live_session_names: целое (bool — нет, float — только целый) от
    1 до MAX_LIVE_SPEAKERS, иначе None. Строка из правленого руками сайдкара —
    тоже None: `int(...)` на мусоре ронял бы всю пересборку (№559)."""
    v = meta.get("speakers") if isinstance(meta, dict) else None
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, int) or not 1 <= v <= MAX_LIVE_SPEAKERS:
        return None
    return v


def speakers_hint(meta: dict) -> int | None:
    """Число голосов для кластеризатора: speakers_count в HINT_RANGE, иначе None."""
    n = speakers_count(meta)
    return n if n is not None and HINT_RANGE[0] <= n <= HINT_RANGE[1] else None


def mic_hint(meta: dict, call_silent: bool) -> int | None:
    """Подсказка числа голосов микрофону: только когда канал собеседников молчит
    (очная встреча, машина без системного звука) и живых голосов не меньше
    MIC_HINT_MIN. Со звонком `speakers` считает оба канала — число для
    микрофона неизвестно, там авто, как было."""
    n = speakers_hint(meta)
    return n if call_silent and n is not None and n >= MIC_HINT_MIN else None


def collapsed_mic_labels(chan: dict[str, str], live_count: int | None,
                         call_silent: bool) -> set[str]:
    """Нейтральная метка микрофона, в которую разметка свела всех, — когда её
    одну не отличить от слияния людей: звонка нет, нейтральная метка микрофона
    ровно одна, а живая сессия слышала не меньше MIC_HINT_MIN голосов. Имя
    одного живого участника на такой метке — ложь о всех остальных (встреча
    29.09: 93 реплики шестерых под одним именем), поэтому ей не дают имени ни
    перенос по времени, ни модель. Две и больше меток — не слияние: очная
    встреча на двоих с живым трекером, насчитавшим шесть, остаётся с именами."""
    if not call_silent or live_count is None or live_count < MIC_HINT_MIN:
        return set()
    neutral = {lbl for lbl, c in chan.items()
               if c == "mic" and channel_labels.is_neutral_label(lbl)}
    return neutral if len(neutral) == 1 else set()


def minutes_names(meta: dict) -> dict[str, str]:
    """Имена для перештамповки минуток — по тому, ЧЬЕЙ нумерацией они написаны.

    Минутки живой сессии (черновик демона, «Протокол» по кнопке) написаны
    метками живой сессии — к ним подходят имена из live.json. Минутки,
    собранные прошлой пересборкой из финальной стенограммы (в сайдкаре лежит
    `minutes_source_sha256`), написаны нумерацией пересборки: имена там уже
    стоят в тексте, а оставшиеся «Собеседник N» — другие люди, чем
    одноимённые метки живой сессии. Штамповать их именами live.json значило
    бы приклеить имя не тому (аудит зон 12.09, зона 1) — остаются нейтральные.
    Мусор вместо хеша не считается признаком пересборки: прежнее поведение.
    Признак — хеш, а не отдельная отметка (критика DS по #551): отметка писалась
    бы тем же best-effort сайдкаром и падала бы вместе с ним; отказ записи
    _finish объявляет вслух, а не тихо.
    """
    if not isinstance(meta, dict):
        return {}
    if live_sidecar.valid_sha(meta.get("minutes_source_sha256")):
        return {}
    return live_session_names(meta)


def names_by_time(live_text: str, base, segments: list[tuple[float, float, str]],
                  allowed: set[str], exclude: set[str] = frozenset()) -> dict[str, str]:
    """Переносит имена из живой стенограммы на метки пересборки ПО ВРЕМЕНИ.

    Метки живой сессии и пересборки — разные кластеризации, поэтому переносить
    «Собеседник 1» → «Собеседник 1» нельзя (приклеит имя не тому). Сопоставляем
    по пересечению интервалов: у какой метки больше всего совпадений по времени
    с репликами живого «Алексея» — та и Алексей. Имя достаётся одной метке.
    `exclude` — метки, которым имени не дают вовсе (collapsed_mic_labels).
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
        # Метка владельца («Я» или имя из настроек) в скоринг не входит:
        # владелец говорит больше всех, и живое имя, звучавшее во время его
        # реплик (обращение к нему же), уходило бы ЕГО метке — а строка
        # `spk = names.get(spk, spk)` дальше переименовала бы абзацы
        # владельца в чужое имя в финальной стенограмме (№147, класс
        # Critical DS по #464). Владелец уже подписан каналом; живые имена —
        # только нейтральным меткам пересборки.
        if (not channel_labels.is_neutral_label(spk) or spk == channel_labels.NEUTRAL_OTHER
                or spk in exclude):
            continue   # владельца, голую канальную метку и слитую метку не скорим
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
    # Стенограмму правили руками после последней пересборки (хеш, снятый
    # write_final, не совпал): STT заново затёр бы правки в .prev, а минутки
    # собрались бы по машинному тексту (№131). Правленый финал — канон:
    # заново только производное. Без хеша (старые встречи, первая
    # пересборка живого черновика) — как прежде, распознаём.
    edited = human_edited_transcript(live, meta)
    if edited is not None:
        # тем же источником (речь + оговорка), что писатель паспорта: сравнение
        # с голой речью после появления оговорки не совпадало никогда (№317)
        if meeting_source.of(live, edited).matches((meta or {}).get("minutes_source_sha256")):
            log("стенограмма правлена руками, минутки уже собраны по этому тексту — "
                "ничего не меняю")
            return live
        log("стенограмма правлена руками после пересборки — STT пропущен, "
            "минутки пересобираю по правленому тексту")
        _finish(live, edited, meta, cfg)
        return live
    sr_cfg = int(cfg["audio"]["samplerate"])
    rec_dir = _root() / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    if os.environ.get("SUFLER_RECORDINGS_DIR"):
        rec_dir = pathlib.Path(os.environ["SUFLER_RECORDINGS_DIR"])

    # Retry знает минутное имя с темой, записи — посекундный штамп демона.
    # Точный штамп — из сайдкара (демон пишет его при стопе, накат темы —
    # при переименовании); без него разрешаем по минуте один раз сразу для
    # обоих каналов, и единственный кандидат с собственной стенограммой —
    # соседка, а не мы (№164). Кандидаты не совпали — не берём ни один
    # вместо склейки двух разных встреч.
    exact = live_sidecar.exact_stamp(live)
    if exact:
        log(f"штамп записей из сайдкара: {exact}")
    recording_stamp = meeting_stamp.resolve_stamp(rec_dir, stamp, exact=exact, tdir=live.parent)
    if exact is None and recording_stamp == stamp and meeting_stamp.minute_of(stamp) == stamp \
            and not any(meeting_stamp.recording_path(rec_dir, stamp, lab, ext).exists()
                        for lab in meeting_stamp.RECORDING_LABELS for ext in meeting_stamp.RECORDING_EXTS):
        # Отказ по минуте — событие, а не тишина (DS r1 по #492): дальше
        # ожидание под минутным именем, и «записей нет» без этой строки
        # выглядело бы как отсутствие записей, а не как отвод чужих. Запись,
        # названная самой минутой (демон до 28.07), — не отказ (DS r2 M3).
        log("точного штампа нет, запись по минуте однозначно не разрешена "
            "(нет кандидатов, две встречи в минуту или у кандидата своя стенограмма)")
        # Ждать нечего: под минутным именем записи нет и не появится (демон с
        # 28.07 пишет посекундно), посекундные кандидаты отведены — 2×45 с
        # ожидания были бы пустыми (GLM M5 по main 05.09)
        log("записей нет — оставляю живую стенограмму")
        return None
    # Под минутой нет ни одного файла канала — ждать нечего ни при точном
    # штампе (записи сметены ретеншном, импорт без записей), ни при
    # секундном: 2×45 с под rebuild.lock были пустыми (аудит GLM/DS 05.09).
    # У живой встречи .pcm/.wav.part под минутой уже лежит — её не задеваем.
    if not rec_dir.is_dir() or not meeting_stamp.recordings_under_minute(
            rec_dir, meeting_stamp.minute_of(recording_stamp)):
        log("записей нет — оставляю живую стенограмму")
        return None
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
        for _ext in meeting_stamp.RECORDING_EXTS:
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

    # Сырые сегменты каналов: None — канал не размечали (записи нет, она
    # короче 20 с или разметка не удалась), [] — размечали, речи не нашли. Разметку по голосам и
    # эхо решает resolve_channel_segments; здесь — только звук и движок.
    bh_raw: list[tuple[float, float, int]] | None = None
    mic_raw: list[tuple[float, float, int]] | None = None
    bh_dwarf_s = BH_DWARF_S
    engine_note = ""               # почему голоса собеседников размечены запасным движком
    if bh_p is not None:
        bh, sr = load_wav(bh_p)
        if len(bh) > sr * 20:
            # Уступка встрече — перед каждой тяжёлой разметкой, а не только на
            # входе в очередь: пересборка, простоявшая за соседней, о начавшейся
            # встрече не знает, а разметка канала — минуты работы (№508).
            _yield_to_live("разметка голосов собеседников", cap=600)
            bh_raw, engine_note = call_channel_engine(cfg, bh_p, len(bh) / sr)
            if bh_raw is not None:
                bh_dwarf_s = NEMOTRON_BH_DWARF_S
            else:
                # сколько голосов слышала живая сессия — жёсткая подсказка кластеризации;
                # без неё авто-режим дробит голоса на осколки (14 «людей» вместо 8)
                bh_raw = diarize_channel(bh, sr, num_speakers=speakers_hint(meta) or -1)
    # Канал собеседников молчит: записи нет, она не размечалась (короче 20 с,
    # сбой) или размечена пустой. Одно значение на подсказку микрофону и на
    # вердикт слияния ниже — два места не расходятся (№559, вход r3 GLM I2).
    call_silent = not bh_raw
    silence = ("канала собеседников нет" if bh_p is None else
               "канал собеседников не размечен" if bh_raw is None else
               "канал собеседников размечен пустым")
    if mic_p is not None:
        mic, sr = load_wav(mic_p)
        if len(mic) > sr * 20:
            _yield_to_live("разметка голосов микрофона", cap=600)
            # Очная встреча: в микрофоне вся комната, а авто-режим сцепляет разных
            # людей в один голос (29.09: 30 кластеров → 1, №565). Число живой
            # сессии идёт микрофону верхней границей: со склейкой осколков после,
            # иначе живой трекер, дробящий один голос, нарезал бы монолог (№559).
            hint = mic_hint(meta, call_silent)
            if hint is not None:
                log(f"mic: подсказка голосов {hint} ({silence}), склейка осколков после")
                mic_raw = diarize_channel(mic, sr, num_speakers=hint, merge_shards=True)
                if not mic_raw:
                    log("mic: разметка с подсказкой ничего не дала — повторяю без подсказки")
                    mic_raw = diarize_channel(mic, sr)
            else:
                mic_raw = diarize_channel(mic, sr)
    # Подпись владельца читается из настроек, только когда микрофон размечен:
    # без микрофона пересборка конфиг здесь не читала и не читает.
    owner_label = (channel_labels.ChannelLabels.from_config(cfg).mic_signed
                   if mic_raw is not None else "")
    segments, chan = resolve_channel_segments(bh_raw, mic_raw, owner_label=owner_label,
                                              bh_dwarf_s=bh_dwarf_s)
    if not segments:
        log("сегментов не нашлось — оставляю живую стенограмму")
        return None
    live_count = speakers_count(meta)
    collapsed = collapsed_mic_labels(chan, live_count, call_silent)
    if collapsed:
        log(f"⚠️ разметка микрофона свела всех в одну метку при живых голосах {live_count} "
            f"({silence}) — имена ей не переносятся и модели не отдаются")
    merged = paragraphs(segments)
    log(f"итог: {len(merged)} абзацев")

    # STT по абзацам (какой канал брать — по метке)
    _yield_to_live("распознавание", cap=600)
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
    allowed = set(live_session_names(meta).values())
    names = names_by_time(live.read_text(encoding="utf-8"), base,
                          [(s, e, spk) for s, e, spk, _ in lines], allowed,
                          exclude=collapsed) if allowed else {}
    if names:
        log("имена из живой сессии: " + ", ".join(f"{k}→{v}" for k, v in names.items()))
    # Безымянными считаются только НЕЙТРАЛЬНЫЕ метки: владелец имя по
    # построению не получает (фильтр скоринга выше), и без этого отсева он
    # вечно сидел бы в rest — холостой вызов модели на каждой пересборке, а
    # при её молчании ложная плашка «имена не определены» на полностью
    # названной встрече (DS+GLM I1 по #465).
    neutral = {spk for _, _, spk, _ in lines
               if channel_labels.is_neutral_label(spk)}
    # Слитая метка микрофона модели не отдаётся: она назвала бы её по
    # самопредставлению любого из тех, кто под ней (№559, вход r1 C2).
    rest = neutral - set(names) - collapsed
    naming = NamesOutcome({}, NamesOutcome.ANSWERED)
    if rest:
        naming = name_speakers(
            cfg, [(spk, txt) for _, _, spk, txt in lines if spk in rest],
            known=known_first_names(cfg))
        guessed = naming.names
        for k, v in guessed.items():
            if k in rest and v not in names.values():  # одно имя — одной метке
                names[k] = v
        if guessed:
            log("имена от модели: " + ", ".join(f"{k}→{v}" for k, v in guessed.items()))
    # Молчащая модель или отвергнутые гвардами имена + оставшиеся безымянные
    # метки = потеря, которую человеку надо видеть в самом файле; причина — своя
    # у каждого исхода (№499). Пустой ответ модели при полностью названных
    # участниках ничего не стоит: помечаем только когда потеря видна в файле.
    unnamed = neutral - set(names) - collapsed
    pending_note = None
    if unnamed and naming.outcome == NamesOutcome.SILENT:
        pending_note = NAMES_PENDING_NOTE
        log(f"⚠️ имена не разобраны: модель молчала, безымянных меток {len(unnamed)}")
    elif unnamed and naming.outcome == NamesOutcome.REJECTED:
        pending_note = NAMES_REJECTED_NOTE.format(proposed=naming.proposed)
        log(f"⚠️ имена не разобраны: модель предложила {naming.proposed}, гварды отвергли все, "
            f"безымянных меток {len(unnamed)}")
    fmt = lambda sec: (base + dt.timedelta(seconds=sec)).strftime("%H:%M")
    body = [f"# Встреча {stamp}", ""]
    if pending_note:
        body += [pending_note, ""]
    if collapsed:
        body += [MIC_COLLAPSED_NOTE.format(live=live_count), ""]
    if engine_note:
        body += [ENGINE_FALLBACK_NOTE.format(reason=engine_note), ""]
    for s, e, spk, text in lines:
        spk = names.get(spk, spk)
        span = fmt(s) if fmt(s) == fmt(e) else f"{fmt(s)}–{fmt(e)}"
        body += [f"**{spk}** [{span}]:", text, ""]
    # ко-мышление из живого черновика — переносим
    live_text = live.read_text(encoding="utf-8")
    m = re.search(rf"\n---\n## {transcript.NOTES_TITLE}.*", live_text, re.S)
    if m:
        body.append(m.group(0).lstrip("\n"))

    final_text = "\n".join(body).rstrip() + "\n"
    final_text = _with_recording_summary(live, final_text)
    # Канон написаний из графа (№149): «Гельского» → «Вельского»,
    # «крам» → «КРАМ» — только по подтверждённым алиасам узлов; похожие
    # слова без алиаса не трогаются, а уходят в отчёт-кандидаты.
    final_text = canonize(final_text, cfg)
    write_final(live, final_text, live_text)
    _finish(live, final_text, meta, cfg)
    return live


def human_edited_transcript(live: pathlib.Path, meta: dict) -> str | None:
    """Текст стенограммы, если после последней машинной записи её правили руками.

    Признак — хеш из сайдкара (`transcript_sha256`; его снимают write_final и
    graph_updater.retitle) не совпал с файлом. Нет хеша, мусор вместо него,
    сайдкар неоднозначен (две встречи в одну минуту), файл не читается или
    задан CHAROITE_FORCE_STT=1 — None: распознаём как прежде, ничего не
    «охраняем» вслепую.
    """
    if os.environ.get("CHAROITE_FORCE_STT") == "1":
        log("CHAROITE_FORCE_STT задан — распознаю заново, правки уйдут в .prev")
        return None
    if live_sidecar.sidecar_for(live) is None:
        log("два сайдкара одной минуты — хеш стенограммы не применяю, распознаю заново")
        return None
    expected = live_sidecar.valid_sha(meta.get("transcript_sha256") if isinstance(meta, dict) else None)
    if not expected:
        return None
    try:
        current = live.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        log(f"стенограмма не прочиталась ({e}) — распознаю заново")
        return None
    return current if _sha(current) != expected else None


def _finish(live: pathlib.Path, final_text: str, meta: dict, cfg: dict) -> None:
    """Производное от финальной стенограммы: минутки и их хеш."""
    # Минутки: нетронутый автотекст — заново по финальной стенограмме; правленный
    # руками — только перештамповать (маркер и имена той нумерации, которой
    # минутки написаны: живой сессии — из live.json, прошлой пересборки —
    # никаких; пересборочный `names` живёт в другом пространстве номеров и
    # клеил бы имя не тому — GLM Critical по #464, аудит зон 12.09).
    outcome = finalize_minutes(live, final_text, meta, cfg, minutes_names(meta))
    mpath = meeting_stamp.derivative_path(live, "minutes")   # одно правило имени на всех писателей (№309)
    record_minutes_passport(live, mpath, outcome, final_text, cfg)


_LEX_CACHE: list = [None]


def _lexicon_for(cfg: dict) -> "lexicon.Lexicon | None":
    """Лексикон один на процесс пересборки: узлы читаются один раз."""
    if _LEX_CACHE[0] is None:
        root = graphs.graph_dir(cfg)
        if root is None:
            return None
        try:
            lex = lexicon.load(root)
        except Exception as e:  # noqa: BLE001 — улучшатель не роняет пересборку
            log(f"лексикон не собрался: {e}")
            return None
        # Частично собранный лексикон не должен быть неотличим от пустого
        # (GLM-9 по #469): пропуски и снятые из-за неоднозначности правила
        # видны по одной строке лога.
        tail = ""
        if lex.skipped_nodes or lex.foreign_stem or lex.shared_alias:
            tail = (f", пропущено узлов {lex.skipped_nodes}, алиас=чужая"
                    f" фамилия {lex.foreign_stem}, общий алиас {lex.shared_alias}")
        log(f"лексикон: правил {len(lex.by_stem)}+{len(lex.by_word)}{tail}")
        _LEX_CACHE[0] = lex
    return _LEX_CACHE[0]


def canonize(text: str, cfg: dict) -> str:
    """Применить канон графа к тексту; кандидатов — в logs/lexicon_candidates.md."""
    lex = _lexicon_for(cfg)
    # Лексикон, у которого все правила снялись конфликтами, «пуст» для
    # замен, но снятия обязаны дойти до отчёта — иначе они невидимы.
    if lex is None or (lex.empty() and not lex.dropped_stems):
        return text
    fixed, replaced = lexicon.apply(text, lex)
    if replaced:
        top = ", ".join(sorted(set(replaced))[:6])
        log(f"лексикон: замен {len(replaced)} ({top})")
    cand = lexicon.candidates(text, lex)
    # Снятые из-за общего алиаса основы — тоже строки отчёта: молчаливое
    # снятие невидимо человеку, а канал кандидатов советовал бы добавить
    # алиас, который уже конфликтует (advisory GLM r3). Пишутся и при
    # пустых кандидатах текста; дедуп общий.
    for st, nodes_ in sorted(lex.dropped_stems.items()):
        cand.append(f"- алиас с основой «{st}» у узлов {nodes_} — "
                    "правило снято, уточните узлы")
    if cand:
        try:
            out = _root() / "logs" / "lexicon_candidates.md"
            # Повторные пересборки одной встречи не дописывают те же
            # строки (GLM-8 по #469): дубли отсекаются по содержимому,
            # разросшийся отчёт теряет старую половину, не новую.
            # Битый UTF-8 (обрыв старого append, ручная правка) не роняет
            # пересборку (GLM-3 круга 2) и НЕ глушит канал навсегда
            # (DS r3): битый хвост усекается до последней валидной
            # границы блока — отчёт самовосстанавливается, с логом.
            before = safe_write.stat_snapshot(out)
            raw = out.read_bytes() if out.exists() else b""
            try:
                old = raw.decode("utf-8")
            except UnicodeDecodeError as e:
                cut = raw.rfind(b"\n## ", 0, e.start)
                old = raw[:cut + 1 if cut >= 0 else 0].decode("utf-8", "ignore")
                safe_write.write_text(out, old, expect=before)
                before = safe_write.stat_snapshot(out)
                log("лексикон: отчёт кандидатов был битым — усечён до валидной границы")
            if len(old) > 200_000:
                # ротация: свежая половина, срез — по границе блока «## »;
                # запись атомарная (safe_write), обрыв не оставит огрызок.
                # expect: ручная правка между чтением и записью не должна
                # молча теряться (DS r3 Minor) — при гонке пропускаем ход.
                half = old[len(old) // 2:]
                cut = half.find("\n## ")
                if cut < 0:
                    # нет границы блока — хотя бы не рвать строку (GLM r3)
                    cut = half.find("\n")
                old = half[cut + 1:] if cut >= 0 else half
                if not safe_write.write_text(out, old, expect=before):
                    log("лексикон: отчёт изменился под рукой — ротация отложена")
                    return fixed
            # fresh — по УЖЕ урезанному old: кандидат из выброшенной
            # половины не должен пропадать из отчёта (DS-4 круга 2).
            # Цена: он всплывёт под свежей датой блока — осознанно, дедуп
            # по содержимому строк важнее точной метки первого показа.
            fresh = [c for c in cand if c not in old]
            if fresh:
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("a", encoding="utf-8") as f:
                    f.write(f"\n## {time.strftime('%Y-%m-%d %H:%M')}\n"
                            + "\n".join(fresh) + "\n")
                log(f"лексикон: кандидатов в отчёт — {len(fresh)}")
        except OSError as e:
            log(f"лексикон: отчёт кандидатов недоступен ({e.__class__.__name__})")
    return fixed


def canonize_file(path: pathlib.Path, cfg: dict) -> None:
    """Канон для готового файла (минутки) — с гейтом потери обновления.

    Проигранная гонка (mcp-«Минутки», редактор) не молчит, а перечитывает
    и пробует ещё раз — та же схема, что у restamp_minutes (DS M1 по
    #469); после второй неудачи — громкая строка в лог, канон догонит
    следующая пересборка.
    """
    lex = _lexicon_for(cfg)
    if lex is None or lex.empty():
        return
    for attempt in (1, 2):
        if not path.exists():
            return
        before = safe_write.stat_snapshot(path)
        if before is None:
            return
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        fixed, replaced = lexicon.apply(text, lex)
        if not replaced or fixed == text:
            return
        if safe_write.write_text(path, fixed, expect=before):
            log(f"лексикон в минутках: замен {len(replaced)}")
            return
        log(f"лексикон в минутках: файл изменился под рукой (попытка {attempt})")


def _with_recording_summary(live: pathlib.Path, final_text: str) -> str:
    """Итог по эпизодам канала — из сайдкара, не только от демона на стопе
    (№316): при SIGKILL демона строки итога нет, а события в сайдкаре — все.
    Та же строка, что писал бы демон (`channel_trace.summary_of`), в хвост
    «Ко-мышления» через владельца формата хвоста (`transcript.append_note`):
    без секции строка ушла бы в речь и дублировалась на каждом прогоне
    (Critical DS входного круга). Уже есть — не дублировать; неоднозначный
    сайдкар (две встречи в минуту) — событий нет, и об этом говорим."""
    events = channel_trace.events_of(live)
    if not channel_trace.summary_line(events):
        if live_sidecar.sidecar_for(live) is None:
            log("сайдкар неоднозначен — итог по каналам не восстановлен")
        return final_text
    # одно правило дописывания на всех писателей хвоста — импорт, сверку пар и
    # пересборку (Important DS и GLM круга 2 по №200)
    text, n = channel_trace.tail_with_summary(final_text, events)
    if n:
        log("итог по каналам записи восстановлен из сайдкара")
    return text


def write_final(live: pathlib.Path, text: str, live_text: str) -> pathlib.Path:
    """Записать финальную стенограмму, сохранив то, что было до неё.

    `_live.md` — живой черновик, пишется один раз и навсегда. Но карточка
    советует «исправьте стенограмму и пересоберите», а вторая пересборка
    заново распознаёт аудио — правки человека исчезали без копии (№131).
    Версия ДО каждой пересборки уходит в скрытую `.prev/<имя>` (одно
    поколение): скрытая папка — не новый суффикс, списки и Swift её не видят.
    """
    live_copy = live.with_name(live.stem + "_live.md")
    if not live_copy.exists():
        safe_write.write_text(live_copy, live_text)
    prev_dir = live.parent / ".prev"
    prev_dir.mkdir(exist_ok=True)
    safe_write.write_text(prev_dir / live.name, live_text)
    safe_write.write_text(live, text)
    log(f"финальная стенограмма записана: {live.name} (живой черновик → {live_copy.name}, "
        f"версия до пересборки → .prev/{live.name})")
    # Хеш — по тем байтам, что ушли на диск: следующая пересборка отличит
    # свой текст от правленного руками (№131).
    _remember_sha(live, "transcript_sha256", _sha(text))
    return live


# Порог, ниже которого минутки не пересобираем, — тот же, с которого демон
# начинает живой черновик (minutes_loop: «< 400 знаков — рано»).
MINUTES_MIN_CHARS = 400


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _remember_minutes_sha(live: pathlib.Path, sha: str) -> None:
    """Хеш машинных минуток — в live.json, чтобы следующая пересборка тоже
    видела в них автотекст, а не правку руками. Зовётся из rebuild() после
    канонизации — по байтам, которые реально лежат на диске."""
    _remember_sha(live, "minutes_sha256", sha)


def _remember_sha(live: pathlib.Path, key: str, sha: str) -> bool:
    """Хеш последней МАШИННОЙ записи файла — в сайдкар под своим ключом
    (`minutes_sha256`, `transcript_sha256`, `minutes_source_sha256`):
    совпадение с диском означает, что текста никто не касался. Нет
    сайдкара — создаётся; неоднозначен — не пишем (False)."""
    ok = live_sidecar.remember(live, key, sha)
    if not ok:
        log(f"хеш {key} не записан: сайдкар неоднозначен или не пишется")
    return ok


def record_minutes_passport(live: pathlib.Path, mpath: pathlib.Path, outcome: str,
                            final_text: str, cfg: dict) -> None:
    """Канонизация и паспорт минуток после `finalize_minutes` — одна тройка
    «запись → канон → хеши» для пересборки и ретро-прогона (ретро писал
    минутки без канона и без паспорта — DS M7 входного круга по №309).

    Хеш — по байтам, которые ЛЕЖАТ на диске после канона, и только когда файл
    машинный. Раньше он писался до canonize_file (лексикон менял байты —
    вторая пересборка видела «правку руками»), а после транзиентного отказа
    модели перештампованный файл терял признак автотекста навсегда (DS r1
    Imp-1/Imp-2 по #483). Хеш речи-источника — только после настоящей
    регенерации: перештамповка не собирала минутки из этого текста (DS I1 /
    GLM I2 r2); источник — речь плюс оговорка о записи, одним объектом
    `meeting_source.MeetingSource` (№317)."""
    canonize_file(mpath, cfg)
    if outcome == "human":
        return
    try:
        _remember_minutes_sha(live, _sha(mpath.read_text(encoding="utf-8")))
        # источник — речь + оговорка о записи, тем же объектом, что читает
        # fresh-проверка в finalize_minutes: писатель и читатель физически не
        # могут разъехаться (Critical DS / Important GLM входного круга по №317)
        if outcome == "regenerated" and not _remember_sha(live, "minutes_source_sha256",
                                                          meeting_source.of(live, final_text).sha()):
            # Без этого хеша minutes_names сочтёт минутки живыми и следующая
            # перештамповка возьмёт имена live.json на нумерацию пересборки
            # (DS I3 по #551): сказать громко, пока минутки нетронуты
            log("⚠️ минутки пересобраны, а признак их нумерации не записан — "
                "следующая пересборка может перештамповать их именами живой сессии; "
                "проверьте сайдкар .live.json")
    except (OSError, UnicodeDecodeError) as e:
        log(f"хеш минуток не снят ({e}) — следующая пересборка их не тронет")


def finalize_minutes(live: pathlib.Path, final_text: str, meta: dict, cfg: dict,
                     live_names: dict[str, str]) -> str:
    """Минутки после пересборки: пересобрать по финалу или перештамповать.

    Живой черновик пишется лёгкой моделью по голове и хвосту НЕЗАКОНЧЕННОЙ
    встречи, с метками «Собеседник N» и без финального STT. Перештамповка
    (№146) лечила шапку и метки, но не содержание: 02.09 10:21 финальные
    минутки утверждали «ИИ для перевода» и раздавали поручения «Собеседнику
    6», пока стенограмма рядом была верной и с именами. Круг по #464 отверг
    регенерацию из-за цены LLM-вызова и риска затереть ручные правки; цена
    замерена — 13 с на 10 000 знаков местной моделью, а ручные правки
    отсекает хеш: демон кладёт в live.json sha256 последней СВОЕЙ записи
    минуток (черновик или «Протокол»), и совпадение с файлом означает, что
    текста никто не касался. Не совпало (правили в редакторе, писал mcp
    «Минутки», демон старее этой правки) — только перештамповка, как прежде.
    Нет минуток вовсе (короткая встреча, упавший поток) — собираем, если
    стенограмма длиннее порога черновика.

    Возвращает исход: «regenerated» — собраны заново из final_text,
    «restamped» — машинный файл лишь перештампован (по нему rebuild() после
    канона снимает хеш, но источник минуток не запоминает — DS I1 / GLM I2
    r2 по #489), «human» — правленный руками, его хеш не трогаем.
    Перед регенерацией прежняя версия уходит в .prev/ рядом со стенограммой
    (одно поколение): уверенная, но неверная генерация не должна быть
    невозвратной (advisory DS r1 по #483).
    """
    mpath = live.with_name(live.stem + "_minutes.md")
    before = safe_write.stat_snapshot(mpath)
    current: str | None = None
    if before is not None:
        try:
            current = mpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            # не-UTF8 файл из редактора ронял бы всю пересборку после
            # записи финала (GLM Minor-6 по #483)
            log(f"минутки не прочитались ({e}) — перештамповка")
            restamp_minutes(live, live_names)
            return "human"
    elif mpath.exists():
        log("минутки не пересобраны (stat не удался) — перештамповка")
        restamp_minutes(live, live_names)
        return "human"
    expected = meta.get("minutes_sha256") if isinstance(meta, dict) else None
    owned = current is None or (bool(expected) and _sha(current) == expected)
    if not owned:
        log("минутки правлены не демоном (или без хеша в live.json) — перештамповка")
        restamp_minutes(live, live_names)
        return "human"
    # Модели — только речь: хвост «Ко-мышление» (📌/💎/💭 живого контура) —
    # мысли модели, не сказанное вслух; кнопка «Протокол» получает tr.full()
    # без него, и здесь так же (GLM Imp-4 по #483). Порог — только при
    # создании с нуля: существующий черновик сам доказывает, что встреча
    # короткой не была, а финал бывает короче живого текста (эхо-фильтр
    # микрофона; GLM Minor-5).
    source = meeting_source.of(live, final_text)
    speech = source.speech
    if current is not None and source.matches(
            meta.get("minutes_source_sha256") if isinstance(meta, dict) else None):
        # машинные минутки уже собраны по этой самой речи: повторный клик без
        # правок не должен перегенерировать протокол — обещание стояло в
        # комментарии при записи хеша, а читал хеш только путь правленой
        # стенограммы (Critical GLM входного круга по №309)
        log("минутки уже собраны по этой речи — модель не зову")
        return "fresh"
    if len(speech) < MINUTES_MIN_CHARS:
        # Замена содержательного черновика регенератом из пустого промпта
        # хуже, чем создание с нуля (advisory GLM r2): короткий финал —
        # прежнее поведение №146, перештамповка.
        log(f"минутки не пересобраны: речи короче {MINUTES_MIN_CHARS} знаков")
        return "restamped" if _fallback_restamp(mpath, before, current, live, live_names) else "human"
    from llm import LLM
    # Тяжёлый вызов не спорит с идущей встречей (GLM Imp-2), но и очередь
    # пересборок не паркует на час: потолок 10 минут.
    _yield_to_live("минутки", cap=600)
    t0 = time.monotonic()
    try:
        doc = "".join(LLM(cfg).minutes(speech, recording_note=source.recording_note)).strip()
    except Exception as e:  # noqa: BLE001 — модель лежит: не терять прежние минутки
        log(f"минутки не пересобраны ({type(e).__name__}: {e}) — перештамповка")
        return "restamped" if _fallback_restamp(mpath, before, current, live, live_names) else "human"
    if not doc:
        log("минутки не пересобраны (модель промолчала) — перештамповка")
        return "restamped" if _fallback_restamp(mpath, before, current, live, live_names) else "human"
    # Те же два шага, что у кнопки «Протокол»: сверка номеров и дат со
    # стенограммой и чекбоксы в формат окна «Задачи».
    # сверка фактов — по тому же множеству, что видела модель (речь + оговорка):
    # иначе «всего 140 мин» из оговорки уходило бы в сноску как выдумка (DS I5)
    doc = action_items.normalize(fact_check.annotate(doc, source.canon()))
    # строка о неполной записи — механически, не поручением модели (критика GLM)
    doc = meeting_source.with_note(doc, source.recording_note)
    # Поручение тому, кого на встрече не было, — пометка, не задача (05.09:
    # минутки приписали поручение упомянутому, а не присутствующему).
    # Владелец — одним написанием ДО пометки (порядок — контракт функции):
    # пересборка — четвёртый путь записи минуток, без него она возвращала бы
    # «**Марку**» поверх «**Марк**» (№188, круг 2 по #536).
    sufler = cfg.get("sufler") or {}
    user_name = str(sufler.get("user_name") or "")
    doc = action_items.finalize_assignees(
        doc, action_items.participants_of(speech, owner=user_name), user_name,
        lang=str(sufler.get("language") or "ru"))
    if current is not None:
        prev_dir = live.parent / ".prev"
        prev_dir.mkdir(exist_ok=True)
        safe_write.write_text(prev_dir / mpath.name, current)
    # Гейт в обе стороны: существовавший файл — тем же снимком, отсутствовавший
    # — «его по-прежнему нет»: mcp «Минутки» или редактор за время генерации
    # могли создать документ (GLM Critical по #483).
    if not safe_write.write_text(mpath, doc, expect=before, expect_absent=before is None):
        log("минутки сменились под пересборкой — оставлены как есть")
        return "human"
    log(f"минутки пересобраны по финальной стенограмме: {mpath.name} "
        f"({len(final_text)} знаков → {len(doc)}, {time.monotonic() - t0:.0f}с"
        + (", прежняя версия → .prev/" if current is not None else "") + ")")
    return "regenerated"


def _fallback_restamp(mpath: pathlib.Path, before, current: str | None,
                      live: pathlib.Path, live_names: dict[str, str]) -> bool:
    """Отказ регенерации: перештамповать и сказать, машинный ли файл.

    Машинным файл остаётся, только если он тот же, что finalize_minutes
    прочла (снимок не сменился), и перештамповка его записала или
    оставила байт-в-байт. Минуток не было, файл подменил чужой процесс
    за время ожидания/вызова, перештамповка проиграла гонку — False:
    иначе хеш снимался бы с чужих байтов, и следующая пересборка
    регенерировала бы поверх них (DS r2 Imp-1, GLM r2 Imp-1 по #483).
    """
    if current is None or safe_write.stat_snapshot(mpath) != before:
        if current is not None:
            log("минутки сменились под пересборкой — оставлены как есть")
        return False
    return restamp_minutes(live, live_names)


def restamp_minutes(live: pathlib.Path, live_names: dict[str, str]) -> bool:
    """Минутки после пересборки: снять маркер черновика, подставить имена.

    Возвращает True, если файл после вызова — те байты, за которые она
    отвечает: записала сама или менять было нечего; False — нет файла,
    не прочитался, гонка проиграна дважды.

    Минутки пишет демон по ходу встречи; автофинализации нет, и после
    пересборки человек читал документ с шапкой «черновик, встреча идёт» и
    участниками «Собеседник 2, Собеседник 4», хотя стенограмма рядом уже
    финальная и с именами (№146, встреча 08:45 31.08). Этот путь — для
    минуток, которых касались руки или чужой процесс (finalize_minutes):
    их не перегенерируем, перештамповка точечная — маркер и метки.

    `live_names` — словарь ЖИВОЙ сессии (live.json: «Собеседник N» → имя):
    его метки — та же нумерация, которой написаны минутки. Имена, доугаданные
    пересборкой по СВОИМ меткам, сюда не идут — без выравнивания нумераций
    это подстановка наугад (GLM Critical по #464).
    """
    mpath = live.with_name(live.stem + "_minutes.md")
    # Гейт потери обновления + одна повторная попытка: пересборка — отдельный
    # процесс, minutes_lock демона её не видит, и в окно read→write мог лечь
    # чужой финал (mcp «Минутки», редактор). Fail-closed без ретрая возвращал
    # бы №146 навсегда — файл оставался черновиком для UI (GLM r2 по #464).
    for attempt in (1, 2):
        before = safe_write.stat_snapshot(mpath)
        if before is None:
            # Нет минуток — штатная тишина; отказ по правам — вслух (GLM M6).
            if mpath.exists():
                log("минутки не перештампованы (stat не удался)")
            return False
        try:
            text = mpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            log(f"минутки не перештампованы (не прочитались): {e}")
            return False
        fixed = text
        for line in (transcript.MINUTES_DRAFT_MARK + "\n", transcript.MINUTES_DRAFT_MARK):
            fixed = fixed.replace(line, "", 1)
        for label, name in live_names.items():
            # Только нейтральные метки — включая голый «Собеседник» канала
            # без диаризации (audio.SPEAKER; GLM r2 I1). Любой другой ключ
            # (метка владельца «Я», короткое имя) без границ слова переписал
            # бы живой текст — «Ясно» → «Имясно» (DS Critical по #464).
            # (?!\s*\d): голая метка не съедает префикс «Собеседник 2»;
            # (?!\w) держит «Собеседник 22» от «Собеседник 2» и «Январь» от
            # «Ян»; имя — литералом, не шаблоном замены.
            if not name or not channel_labels.is_neutral_label(label):
                continue
            fixed = re.sub(r"(?<!\w)" + re.escape(label) + r"(?!\s*\d)(?!\w)",
                           lambda _m, n=name: n, fixed)
        if fixed == text:
            return True
        if safe_write.write_text(mpath, fixed, expect=before):
            break
        if attempt == 1:
            log("минутки сменились под пересборкой — повторный заход")
            continue
        log("минутки меняются под пересборкой — перештамповка пропущена")
        return False
    log(f"минутки перештампованы: {mpath.name}"
        + (f" (имена: {', '.join(f'{k}→{v}' for k, v in live_names.items())})"
           if live_names else " (снят маркер черновика)"))
    return True


def names_pending(live: pathlib.Path) -> bool:
    """Осталась ли в стенограмме пометка «имена не определены» — любой из двух
    причин: ищем общее начало плашки, его же несут стенограммы до №499."""
    try:
        return NAMES_PENDING_PREFIX in live.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — статус не должен ломать пайплайн
        return False


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
    target = pathlib.Path(pending[0]["transcript_path"])   # запись очереди проверил unfinished()
    log(f"повтор незавершённой встречи: {target.name} "
        f"(в очереди {len(pending)}, попытка {int(pending[0].get('attempts', 0)) + 1})")
    # Живую встречу родитель не ждёт: ребёнок уступает ей сам, до очереди пересборок.
    # Готовый родитель, ждущий часами, держал отметку «пересборка идёт» своей встречи
    # (выходной круг 1 по №514, критика). Повтор попутен: любой сбой его запуска —
    # строка, а не трейсбек готовой пересборки (там же, M1).
    try:
        _spawn_retry(target)
    except Exception as e:  # noqa: BLE001 — своя встреча уже готова, повтор её не судит
        log(f"повтор {target.name} не запустился ({type(e).__name__}: {e})")


def _spawn_retry(target: pathlib.Path) -> None:
    """Запустить пересборку `target` в своей сессии, с журналом повтора, если он открылся."""
    env = dict(os.environ, CHAROITE_NO_RETRY="1")
    # по полному имени файла, не по 15 знакам: две встречи одной минуты
    # (и две минутные встречи прежних версий) писали в один лог, и второй
    # спавн усекал лог первого (аудит 30.08, GLM; DS r1)
    journal = meeting_log(_root(), "retry", stem=target.stem)
    name = journal.name
    try:
        out = open(journal, "w")
    except OSError as e:
        # журнал — не повод не повторять: без него повтор идёт молча (№514)
        log(f"журнал повтора {name} не открылся ({type(e).__name__}: {e}) — повтор без журнала")
        out = subprocess.DEVNULL
    try:
        subprocess.Popen(
            ["nice", "-n", "10", sys.executable, str(CODE / "src" / "rebuild_transcript.py"), str(target)],
            start_new_session=True, env=env, stdout=out, stderr=subprocess.STDOUT,
        )
    finally:
        if out is not subprocess.DEVNULL:
            out.close()   # у ребёнка своя копия дескриптора


def _pid_file(stamp: str) -> pathlib.Path:
    return log_path(_root(), "rebuild_pid", stamp)


_RUNNING_LOCKS: list = []   # открытые pid-файлы под flock (иначе GC закроет и снимет замок)


class RunningElsewhere(RuntimeError):
    """Отметку не взять: замок держит другой прогон — второй заход запрещён."""


def running_elsewhere(live: pathlib.Path) -> int | None:
    """Pid живой пересборки этой же встречи, если она уже идёт.

    12.08: обновление приложения посреди разбора дало два прогона на одну
    встречу. Первый осиротел (его демона закрыли), но продолжил работать;
    новый демон при старте увидел прерванную встречу и запустил пересборку
    заново — он проверяет ЛОК ДЕМОНА, а осиротевшая пересборка лока не
    держит вовсе. Два процесса по 100% CPU диаризовали одно и то же и
    дрались за финальный файл: чей `replace` последний, того и результат.

    Отметка живёт весь прогон, а не отдельный его шаг. Пробовать опознать
    работу по временному `<имя>.wav.part<pid>` бесполезно: он существует
    только в окне конвертации, а дубль случился на диаризации — то есть
    ровно там, где такого файла уже нет.

    Признак один — flock на pid-файле: живой прогон держит его от
    `mark_running` до выхода, а снимает замок сама ОС, когда процесс
    умирает. Поэтому мёртвая отметка (машину выключили посреди пересборки)
    ничего не запрещает — её перепишет следующий прогон. Живость по pid
    (`kill 0`, старт процесса из `ps`) и уборка чужих файлов здесь
    намеренно отсутствуют: pid переиспользуется, а unlink по имени под
    чужим свежим замком снимал бы отметку живого прогона (круг по #455).
    Отметки версий без замка не распознаются — разово, при обновлении.
    """
    stamp = meeting_stamp.stamp_of(live.stem)
    if not stamp:
        return None
    f = _pid_file(stamp)
    try:
        with f.open("r") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raw = fh.read().strip()
                return int(raw) if raw.isdigit() else -1   # -1: держатель есть, pid не прочитать
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        return None
    except OSError as e:
        # том без flock (SMB/NFS/FUSE): работаем без защиты, но не молча —
        # иначе двойной прогон 12.08 вернулся бы незаметно (DS r2 по #455)
        log(f"замок пересборки недоступен ({e}): защита от двойного прогона снята")
        return None
    return None


def mark_running(live: pathlib.Path) -> pathlib.Path | None:
    """Отметить, что пересборка этой встречи идёт под нашим pid.

    Файл после выхода не снимается: замок отпускает ОС, а unlink по имени в
    окне выхода снимал бы отметку прогона, стартовавшего следом (DS r2 по
    #455). Цена — файл в несколько байт на встречу в logs/.
    """
    stamp = meeting_stamp.stamp_of(live.stem)
    if not stamp:
        return None
    f = _pid_file(stamp)
    fh = None
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        fh = f.open("a")           # не «w»: усечение до замка стирало pid живого прогона (DS, Critical)
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise RunningElsewhere("замок пересборки держит другой процесс")
        fh.truncate(0)
        fh.seek(0)
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError as e:
        if fh is not None:
            fh.close()             # том без flock: дескриптор не держим (GLM r3)
        log(f"отметка пересборки не взята ({e}): защита от двойного прогона снята")
        return None
    _RUNNING_LOCKS.append(fh)   # держим открытым — замок живёт, пока жив процесс
    return f


def main():
    harden_umask()  # данные встреч — только владельцу (аудит 16.08)
    live = pathlib.Path(sys.argv[1]).expanduser()
    if not live.exists():
        sys.exit(f"нет файла: {live}")
    # Второй прогон той же встречи — не помощь, а вред: та же работа вдвое,
    # та же память вдвое и гонка за финальный файл.
    busy = running_elsewhere(live)
    if busy:
        log(f"пересборка этой встречи уже идёт (pid {busy}) — выхожу")
        return
    try:
        mark = mark_running(live)
    except RunningElsewhere as e:   # проскочили проверку одновременно: решает замок (DS по #455)
        log(f"пересборка этой встречи уже идёт ({e}) — выхожу")
        return
    status = MeetingStatusStore(_root())
    pipeline_started = time.time()
    if mark is None:
        log("пересборка идёт без отметки — второй прогон этой встречи не будет отклонён")

    def publish(method, *args):
        try:
            return method(*args)
        except Exception as e:  # noqa: BLE001 — статус не должен ломать сам пайплайн
            log(f"статус обработки не записан ({type(e).__name__}: {e})")
            return None

    publish(status.processing, live, "waiting_for_audio")
    # Живая встреча важнее пересборки целиком, включая STT и диаризацию:
    # 18.08 пересборка держала модель, а Whisper — GPU, пока шла встреча.
    _yield_to_live("пересборка")
    queue = _take_rebuild_queue()   # держим до выхода процесса — это и есть очередь
    try:
        cfg = yaml.safe_load((_root() / "config" / "config.yaml").read_text(encoding="utf-8"))
        publish(status.processing, live, "rebuilding_transcript")
        try:
            rebuild(live, cfg)
        except Exception as e:  # noqa: BLE001 — граф важнее идеальной пересборки
            log(f"пересборка не удалась ({type(e).__name__}: {e}) — граф по живой версии")
        # Профиль мог выключить узлы графа (`sufler.graph: false`). Сам
        # graph_updater при этом всё равно нужен: архив встречи, копии в
        # vault и post_meeting_hook живут там же и от модели не зависят.
        graph_on = install_profile.graph_enabled(cfg)
        if not graph_on:
            log("граф выключен профилем (sufler.graph: false) — "
                "узлы не строим, архив и копии собираем")
        publish(status.processing, live, "updating_graph")
        _yield_to_live("разбор графа")   # graph_updater ждёт и сам — здесь ради честного лога
        result = subprocess.run(
            [sys.executable, str(CODE / "src" / "graph_updater.py"), str(live)],
            check=False,
        )
        if result.returncode == EXIT_NO_SPEECH:
            # Тишину повторять бессмысленно: статус честно говорит, что речи
            # в записи нет, и подбор незавершённых сюда больше не вернётся.
            log("в записи нет речи — граф не трогаем")
            publish(status.no_speech, live)
            return
        if result.returncode == EXIT_NO_GRAPH:
            # Архив, копии и хук собраны, а узлов графа нет: статус — ошибка,
            # чтобы retry_unfinished вернулся, когда модель оживёт.
            raise RuntimeError("модель не дала разбор — граф не обновлён "
                               "(архив встречи собран, повторим позже)")
        if result.returncode:
            raise RuntimeError(f"graph_updater завершился с кодом {result.returncode}")
        # Заметку встречи создаёт разбор в узлы: выключен профилем — её нет
        # и быть не должно, и требовать её значило бы ронять готовую встречу.
        note = find_meeting_note(cfg, live, newer_than=pipeline_started - 2) if graph_on else None
        if graph_on and note is None:
            raise RuntimeError("заметка встречи не создана")
        if not status.has_transcript(live):
            raise RuntimeError("финальная стенограмма не найдена")
        # Пометку читаем из готового файла, а не носим флагом через пайплайн:
        # так она честна и после падения пересборки (граф пошёл по живой
        # версии — пометки нет), и после повторного прогона, где стенограмма
        # переписывается целиком и метка исчезает сама, если имена нашлись.
        publish(status.ready, live, note, names_pending(live))
    except Exception as e:  # noqa: BLE001 — статус ошибки обязан пережить процесс
        log(f"обработка не завершена ({type(e).__name__}: {e})")
        publish(status.failed, live, f"{type(e).__name__}: {e}")
        raise
    finally:
        if queue is not None:
            queue.close()   # отпускаем очередь: следующая пересборка может стартовать
    # Своя встреча готова — значит конвейер жив и LLM отвечает. Лучший момент
    # вернуться к тем, кому в прошлый раз не повезло. Повтор — после очереди и
    # вне перехвата статуса: его сбой не красит готовую встречу в «ошибку», а
    # ожидание чужой встречи не держит очередь пересборок (круги 1–3 по №514);
    # ребёнок берёт очередь сам.
    if os.environ.get("CHAROITE_NO_RETRY") != "1":
        retry_unfinished(status)


if __name__ == "__main__":
    # корень данных называет тот, кто запускает: приложение и демон передают
    # CHAROITE_ROOT, ручной запуск без него получает рецепт и код 5 (№340)
    from charoite_paths import name_data_root_or_exit
    name_data_root_or_exit(__file__)
    main()
