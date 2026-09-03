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
import json
import os
import pathlib
import re
import safe_write
import transcript
import subprocess
import sys
import time
import wave

from charoite_paths import code_root, harden_umask, resolve_root

ROOT = resolve_root(__file__)
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
import graphs  # noqa: E402
import lexicon  # noqa: E402
import owner_voice as owner_voice_rules  # noqa: E402
import live_gate  # noqa: E402
import meeting_stamp  # noqa: E402
from diarize import diarize  # noqa: E402 — pyannote-сегментация + эмбеддинги, весь файл
from graph_updater import EXIT_NO_GRAPH, EXIT_NO_SPEECH  # noqa: E402
from meeting_processing import MeetingStatusStore, find_meeting_note  # noqa: E402
from stt import STT  # noqa: E402
from transcript import NOISE, Transcript  # noqa: E402

SEG_S, OVERLAP_S = 25.0, 1.0
WAIT_WAV_S = 45  # демон финализирует .wav параллельно нашему старту
# Строка в шапке стенограммы, когда имена не разобраны из-за молчащей модели.
# Живёт в самом файле, а не только в логе: человек открывает стенограмму, а не
# logs/, и «Собеседник 1..5» без объяснения читается как «программа не умеет».
NAMES_PENDING_NOTE = (
    "> ⚠️ Имена участников не определены: модель не ответила на разборе. "
    "Метки остались «Собеседник N» — пересоберите встречу, когда модель "
    "свободна (кнопка «Пересобрать» или src/rebuild_transcript.py)."
)


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
    return live_gate.daemon_alive(ROOT)


def _yield_to_live(what: str, cap: float | None = None) -> None:
    """Пока идёт живая встреча, тяжёлую модель не трогаем — см. live_gate.

    `cap` — потолок ожидания: внутри уже взятой очереди пересборок
    (rebuild.lock) бесконечная уступка парковала бы очередь на всю чужую
    встречу; после потолка вызов идёт в тесноте, с логом (GLM r2 по #483).
    """
    live_gate.wait_while_live(ROOT, log, what=what, poll=10, cap=cap)


def _take_rebuild_queue():
    """Одна пересборка за раз на машину: лок logs/rebuild.lock до конца процесса.

    Без него Стоп встречи запускал две пересборки разом: сирота, отпущенная
    гейтом живой встречи, просыпалась в ту же секунду, что и свежая — два
    полных конвейера STT+диаризация+модель параллельно, тот самый залп,
    от которого строилась цепочка сирот (12.08; ревью 18.08). Ждём молча
    не дольше секунды, дальше — с записью в лог.
    """
    path = ROOT / "logs" / "rebuild.lock"
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


def name_speakers(cfg: dict, lines: list[tuple[str, str]]) -> tuple[dict[str, str], bool]:
    """qwen: «Собеседник N» ↔ имена из разговора; владельца не трогаем.

    Возвращает (имена, ответила ли модель). Второе — не педантизм: пустой
    словарь означает и «имён в разговоре не звучало», и «модель молчала, ответ
    не разобрался». 12.08 случилось второе, стенограмма ушла с «Собеседник
    1..5», а прогон записался успешным — та же тихая деградация, которую
    чинили в ночных досье. Различаем: первое нормально, второе стоит показать.
    """
    from llm import LLM
    _owner = ((cfg.get("sufler") or {}).get("user_name") or "").strip().lower()
    sample = "\n".join(f"[{spk}] {text}" for spk, text in lines if text)[:7000]
    _yield_to_live("имена")
    try:
        raw = LLM(cfg).complete(
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
        data = json.loads(raw or "{}")
        return {k: v.strip() for k, v in data.items()
                if isinstance(v, str) and v.strip() and v.strip() != "?"
                and k.startswith("Собеседник")
                and v.strip().lower() != _owner}, True  # владелец определён каналом
    except Exception as e:  # noqa: BLE001
        log(f"имена: не удалось ({e})")
        return {}, False


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
    """Где лежит live.json этой встречи.

    Демон пишет его как <посекундная стенограмма>.md.live.json. Накат темы
    (graph_updater.retitle) и rename_meeting переименовывают только *.md —
    сайдкар остаётся «2026-09-02_102112.md.live.json» рядом с
    «2026-09-02_1021_Тема.md», и вторая пересборка (кнопка «Пересобрать»)
    его не находила: терялись имена живой сессии, а с ними и хеш минуток
    (advisory DS r2 по #483). Нет файла под своим именем — ищем по
    минутному штампу; кандидатов несколько — берём самый свежий.
    """
    direct = live.with_name(live.name + ".live.json")
    if direct.exists():
        return direct
    stamp = meeting_stamp.stamp_of(live.stem) or live.stem
    minute = meeting_stamp.minute_of(stamp)
    tail = ".md.live.json"
    found = [p for p in live.parent.glob(f"{minute}*{tail}")
             if meeting_stamp.minute_of(p.name[:-len(tail)]) == minute]
    if not found:
        return direct
    return max(found, key=lambda p: p.stat().st_mtime)


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
        # Метка владельца («Я» или имя из настроек) в скоринг не входит:
        # владелец говорит больше всех, и живое имя, звучавшее во время его
        # реплик (обращение к нему же), уходило бы ЕГО метке — а строка
        # `spk = names.get(spk, spk)` дальше переименовала бы абзацы
        # владельца в чужое имя в финальной стенограмме (№147, класс
        # Critical DS по #464). Владелец уже подписан каналом; живые имена —
        # только нейтральным меткам пересборки.
        if not channel_labels.is_neutral_label(spk) or spk == channel_labels.NEUTRAL_OTHER:
            continue   # владельца и голую канальную метку не скорим
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
        log("стенограмма правлена руками после пересборки — STT пропущен, "
            "минутки пересобираю по правленому тексту")
        _finish(live, edited, meta, cfg)
        return live
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

    # Объявляем ДО ветки: на mic-only машине (нет BlackHole или не выдано
    # разрешение на системный звук) блок ниже не выполняется, а `bh_segs`
    # читается дальше в `call=bool(bh_segs)`. Без объявления там NameError,
    # который `main()` глотает как «пересборка не удалась» — и встреча молча
    # остаётся без разбора по голосам, распознавания по абзацам и имён, а
    # через record_keep_days запись удаляется и вернуть качество уже нечем
    # (ревью 20.08, GLM).
    bh_segs: list[tuple[float, float, int]] = []
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
            owner_label = channel_labels.ChannelLabels.from_config(cfg).mic_signed
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
    allowed = set(live_session_names(meta).values())
    names = names_by_time(live.read_text(encoding="utf-8"), base,
                          [(s, e, spk) for s, e, spk, _ in lines], allowed) if allowed else {}
    if names:
        log("имена из живой сессии: " + ", ".join(f"{k}→{v}" for k, v in names.items()))
    # Безымянными считаются только НЕЙТРАЛЬНЫЕ метки: владелец имя по
    # построению не получает (фильтр скоринга выше), и без этого отсева он
    # вечно сидел бы в rest — холостой вызов модели на каждой пересборке, а
    # при её молчании ложная плашка «имена не определены» на полностью
    # названной встрече (DS+GLM I1 по #465).
    neutral = {spk for _, _, spk, _ in lines
               if channel_labels.is_neutral_label(spk)}
    rest = neutral - set(names)
    model_answered = True
    if rest:
        guessed, model_answered = name_speakers(
            cfg, [(spk, txt) for _, _, spk, txt in lines if spk in rest])
        for k, v in guessed.items():
            if k in rest and v not in names.values():  # одно имя — одной метке
                names[k] = v
        if guessed:
            log("имена от модели: " + ", ".join(f"{k}→{v}" for k, v in guessed.items()))
    # Молчащая модель + оставшиеся безымянные метки = встреча, которую стоит
    # пересобрать. Пустой ответ модели при полностью названных участниках
    # ничего не стоит: помечаем только когда потеря видна в самом файле.
    unnamed = neutral - set(names)
    names_pending = not model_answered and bool(unnamed)
    if names_pending:
        log(f"⚠️ имена не разобраны: модель молчала, безымянных меток {len(unnamed)}")
    fmt = lambda sec: (base + dt.timedelta(seconds=sec)).strftime("%H:%M")
    body = [f"# Встреча {stamp}", ""]
    if names_pending:
        body += [NAMES_PENDING_NOTE, ""]
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
    # Канон написаний из графа (№149): «Гельского» → «Вельского»,
    # «крам» → «КРАМ» — только по подтверждённым алиасам узлов; похожие
    # слова без алиаса не трогаются, а уходят в отчёт-кандидаты.
    final_text = canonize(final_text, cfg)
    write_final(live, final_text, live_text)
    _finish(live, final_text, meta, cfg)
    return live


def human_edited_transcript(live: pathlib.Path, meta: dict) -> str | None:
    """Текст стенограммы, если после последней пересборки её правили руками.

    Признак — хеш из live.json (`transcript_sha256`, снимает write_final) не
    совпал с файлом. Нет хеша или файл не читается — None: распознаём как
    прежде, ничего не «охраняем» вслепую.
    """
    expected = meta.get("transcript_sha256") if isinstance(meta, dict) else None
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
    # руками — только перештамповать (маркер и имена ЖИВОЙ сессии: их метки —
    # та же нумерация, которой минутки написаны; пересборочный `names` живёт в
    # другом пространстве номеров и клеил бы имя не тому — GLM Critical по #464).
    machine_owned = finalize_minutes(live, final_text, meta, cfg, live_session_names(meta))
    mpath = live.with_name(live.stem + "_minutes.md")
    canonize_file(mpath, cfg)
    # Хеш — по байтам, которые ЛЕЖАТ на диске после канона, и только когда
    # файл машинный. Раньше он писался до canonize_file (лексикон менял
    # байты — вторая пересборка видела «правку руками»), а после
    # транзиентного отказа модели перештампованный файл терял признак
    # автотекста навсегда (DS r1 Imp-1/Imp-2 по #483).
    if machine_owned:
        try:
            _remember_minutes_sha(live, _sha(mpath.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            log(f"хеш минуток не снят ({e}) — следующая пересборка их не тронет")


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
            out = ROOT / "logs" / "lexicon_candidates.md"
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


def _remember_sha(live: pathlib.Path, key: str, sha: str) -> None:
    """Хеш последней МАШИННОЙ записи файла — в live.json под своим ключом
    (`minutes_sha256`, `transcript_sha256`): совпадение с диском означает,
    что текста никто не касался."""
    p = live_meta_path(live)
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return
        meta[key] = sha
        safe_write.write_text(p, json.dumps(meta, ensure_ascii=False))
    except (OSError, ValueError) as e:
        log(f"хеш {key} не записан в live.json: {e}")


def finalize_minutes(live: pathlib.Path, final_text: str, meta: dict, cfg: dict,
                     live_names: dict[str, str]) -> bool:
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

    Возвращает, машинный ли файл после этого шага (регенерирован или
    перештампован при нетронутом автотексте): по нему rebuild() после
    канона снимает хеш. Правленный руками файл — False, его хеш не трогаем.
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
            return False
    elif mpath.exists():
        log("минутки не пересобраны (stat не удался) — перештамповка")
        restamp_minutes(live, live_names)
        return False
    expected = meta.get("minutes_sha256") if isinstance(meta, dict) else None
    owned = current is None or (bool(expected) and _sha(current) == expected)
    if not owned:
        log("минутки правлены не демоном (или без хеша в live.json) — перештамповка")
        restamp_minutes(live, live_names)
        return False
    # Модели — только речь: хвост «Ко-мышление» (📌/💎/💭 живого контура) —
    # мысли модели, не сказанное вслух; кнопка «Протокол» получает tr.full()
    # без него, и здесь так же (GLM Imp-4 по #483). Порог — только при
    # создании с нуля: существующий черновик сам доказывает, что встреча
    # короткой не была, а финал бывает короче живого текста (эхо-фильтр
    # микрофона; GLM Minor-5).
    speech = re.split(re.escape(transcript.NOTES_HEAD), final_text, maxsplit=1)[0]
    if len(speech) < MINUTES_MIN_CHARS:
        # Замена содержательного черновика регенератом из пустого промпта
        # хуже, чем создание с нуля (advisory GLM r2): короткий финал —
        # прежнее поведение №146, перештамповка.
        log(f"минутки не пересобраны: речи короче {MINUTES_MIN_CHARS} знаков")
        return _fallback_restamp(mpath, before, current, live, live_names)
    from llm import LLM
    # Тяжёлый вызов не спорит с идущей встречей (GLM Imp-2), но и очередь
    # пересборок не паркует на час: потолок 10 минут.
    _yield_to_live("минутки", cap=600)
    t0 = time.monotonic()
    try:
        doc = "".join(LLM(cfg).minutes(speech)).strip()
    except Exception as e:  # noqa: BLE001 — модель лежит: не терять прежние минутки
        log(f"минутки не пересобраны ({type(e).__name__}: {e}) — перештамповка")
        return _fallback_restamp(mpath, before, current, live, live_names)
    if not doc:
        log("минутки не пересобраны (модель промолчала) — перештамповка")
        return _fallback_restamp(mpath, before, current, live, live_names)
    # Те же два шага, что у кнопки «Протокол»: сверка номеров и дат со
    # стенограммой и чекбоксы в формат окна «Задачи».
    doc = action_items.normalize(fact_check.annotate(doc, speech))
    if current is not None:
        prev_dir = live.parent / ".prev"
        prev_dir.mkdir(exist_ok=True)
        safe_write.write_text(prev_dir / mpath.name, current)
    # Гейт в обе стороны: существовавший файл — тем же снимком, отсутствовавший
    # — «его по-прежнему нет»: mcp «Минутки» или редактор за время генерации
    # могли создать документ (GLM Critical по #483).
    if not safe_write.write_text(mpath, doc, expect=before, expect_absent=before is None):
        log("минутки сменились под пересборкой — оставлены как есть")
        return False
    log(f"минутки пересобраны по финальной стенограмме: {mpath.name} "
        f"({len(final_text)} знаков → {len(doc)}, {time.monotonic() - t0:.0f}с"
        + (", прежняя версия → .prev/" if current is not None else "") + ")")
    return True


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
    """Осталась ли в стенограмме пометка «имена не определены»."""
    try:
        return NAMES_PENDING_NOTE in live.read_text(encoding="utf-8")
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
    _yield_to_live("повтор незавершённой")
    target = pathlib.Path(pending[0]["transcript_path"])
    log(f"повтор незавершённой встречи: {target.name} "
        f"(в очереди {len(pending)}, попытка {int(pending[0].get('attempts', 0)) + 1})")
    env = dict(os.environ, CHAROITE_NO_RETRY="1")
    subprocess.Popen(
        ["nice", "-n", "10", sys.executable, str(pathlib.Path(__file__)), str(target)],
        start_new_session=True, env=env,
        # по полному имени файла, не по 15 знакам: две встречи одной минуты
        # (и две минутные встречи прежних версий) писали в один лог, и второй
        # спавн усекал лог первого (аудит 30.08, GLM; DS r1)
        stdout=open(ROOT / "logs" / f"retry_{target.stem}.log", "w"),
        stderr=subprocess.STDOUT,
    )


def _pid_file(stamp: str) -> pathlib.Path:
    return ROOT / "logs" / f"rebuild-{stamp}.pid"


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
    status = MeetingStatusStore(ROOT)
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
        cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
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
            [sys.executable, str(pathlib.Path(__file__).parent / "graph_updater.py"), str(live)],
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
        # Своя встреча готова — значит конвейер жив и LLM отвечает. Лучший
        # момент вернуться к тем, кому в прошлый раз не повезло.
        if os.environ.get("CHAROITE_NO_RETRY") != "1":
            retry_unfinished(status)
    except Exception as e:  # noqa: BLE001 — статус ошибки обязан пережить процесс
        log(f"обработка не завершена ({type(e).__name__}: {e})")
        publish(status.failed, live, f"{type(e).__name__}: {e}")
        raise
    finally:
        if queue is not None:
            queue.close()   # отпускаем очередь: следующая пересборка может стартовать


if __name__ == "__main__":
    main()
