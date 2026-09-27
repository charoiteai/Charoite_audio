#!/usr/bin/env python3
"""Бенч диаризации: сколько времени приписано не тому голосу.

Качество памяти меряется (`memory_bench.py`), качество распознавания речи и
разделения голосов — нет. Поэтому «путает говорящих» до сих пор было мнением,
а не числом, и любая правка порогов проверялась на слух.

Здесь считается DER (diarization error rate) — доля времени речи, в которой
система ошиблась: пропустила речь, услышала её в тишине или отдала не тому
говорящему. Меньше — лучше; 0.15 значит «каждая седьмая секунда разговора
подписана неверно».

    .venv/bin/python scripts/diar_bench.py --make      # собрать синтетику
    .venv/bin/python scripts/diar_bench.py             # померить оба движка
    .venv/bin/python scripts/diar_bench.py --engine sherpa

Nemotron 3 Diarization (эксперимент, `src/diarize_nemotron.py`) — только
Apple Silicon, пакет и веса ставятся руками, рецепт скрипт печатает сам:

    .venv/bin/python scripts/diar_bench.py --make --crosstalk   # с перебиваниями
    .venv/bin/python scripts/diar_bench.py --crosstalk --engine compare

Своя запись вместо синтетики: `--wav` (моно 16 кГц, например канал
собеседников из recordings/) и, если есть, `--truth` — ручная разметка
фрагмента в Audacity (File → Export → Labels), RTTM или JSON бенча. Без
разметки DER не считается — печатается сводка по движкам, а `--labels` пишет
их гипотезы дорожками меток Audacity, чтобы расхождения можно было послушать.

Синтетика. Записей встреч в репозитории нет и быть не может — это чужие
разговоры. Поэтому фикстура собирается на месте из системного синтезатора
речи macOS: несколько разных голосов произносят реплики, они склеиваются в
диалог с паузами, и разметка «кто когда говорил» известна точно, потому что мы
сами её и составили.

Честная оговорка: синтезированные голоса чище живых, у них нет перебивания,
эха и шума комнаты. Такой бенч — нижняя планка: движок, который путает
говорящих ЗДЕСЬ, на живой встрече будет хуже. Обратное неверно, и цифры отсюда
нельзя выдавать за качество на реальных встречах.

Перебивания (`--crosstalk`) — отдельная фикстура: часть реплик начинается до
конца предыдущей, и в разметке два голоса звучат одновременно. DER тогда
считается по NIST: в кадре может быть несколько голосов, недобор — пропуск,
перебор — лишнее. Когда перекрытий нет ни в разметке, ни в гипотезе, это ровно
прежняя метрика (подробно — докстринг `der_overlap`).
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import deps  # noqa: E402
from charoite_paths import harden_umask, resolve_root  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import diarize_nemotron as nemotron  # noqa: E402  mlx внутри — лениво, только в прогоне

SR = 16000
FRAME = 0.01            # шаг сетки при подсчёте DER, 10 мс — стандарт


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)


def _fixture(crosstalk: bool = False) -> pathlib.Path:
    """Фикстура живёт в корне данных, рядом с записями, а не с кодом."""
    return _root() / "data" / ("diar_bench_crosstalk" if crosstalk else "diar_bench")

# Реплики диалога: (голос macOS, текст). Голоса выбраны контрастными по
# тембру — мужские и женские, разные семейства синтеза.
DIALOG = [
    ("Milena", "Коллеги, начнём. Что у нас по платёжному провайдеру?"),
    ("Fred", "We compared two vendors last week and the numbers are ready."),
    ("Samantha", "The integration takes two weeks, the fee is two point eight percent."),
    ("Milena", "А что со сроками сертификации? Это блокер для запуска."),
    ("Ralph", "Certification usually takes about a month, sometimes longer."),
    ("Fred", "I would start the paperwork this week to be safe."),
    ("Milena", "Хорошо, тогда решаем так: берём первого поставщика."),
    ("Samantha", "Agreed. I will prepare the contract by Friday."),
]
PAUSE = 0.4             # тишина между репликами

#: Перебивания для `--crosstalk`: номер реплики → на сколько секунд она
#: начинается раньше конца предыдущей. Ради этого случая и сравнивают
#: Nemotron: конвейер «кусок речи → эмбеддинг» видит в перекрытии одного
#: говорящего, а второй пропадает или смешивается с первым.
CROSSTALK = {2: 1.0, 4: 1.5, 5: 0.8, 7: 1.2}


def layout_turns(lengths: list[int], pause: int,
                 overlaps: dict[int, int]) -> list[int]:
    """Начала реплик на общей шкале, в сэмплах.

    Без перекрытий — ровно прежняя склейка: реплика, пауза, следующая.
    overlaps[i] — на сколько сэмплов реплика i залезает на уже звучащую речь;
    начать раньше предыдущей реплики она не может, порядок диалога сохраняется.
    Следующая реплика без перекрытия ждёт, пока замолчат все.
    """
    starts: list[int] = []
    busy_until = 0
    for i, length in enumerate(lengths):
        if not starts:
            start = 0
        elif i in overlaps:
            start = max(starts[-1], busy_until - overlaps[i])
        else:
            start = busy_until + pause
        starts.append(start)
        busy_until = max(busy_until, start + length)
    return starts


def mix_turns(parts: list[np.ndarray], starts: list[int], tail: int) -> np.ndarray:
    """Сложить реплики на шкале; в перекрытии голоса суммируются.

    Громкость снижается, только если сумма вышла за шкалу: без перекрытий
    результат совпадает со склейкой бит в бит.
    """
    total = max(s + len(p) for p, s in zip(parts, starts)) + tail
    out = np.zeros(total, dtype=np.float32)
    for part, start in zip(parts, starts):
        out[start:start + len(part)] += part
    peak = float(np.max(np.abs(out))) if total else 0.0
    if peak > 1.0:
        out *= 0.99 / peak
    return out


#: Эталон, в котором нет ни кадра речи: пустой файл, одни метки-точки, JSON без
#: отрезков. DER на нём не «идеален», а не определён (выходной круг 1 по #648, DS C1).
NO_SPEECH = "в эталоне нет ни одного кадра речи — DER не определён"


def speech_frames(truth: list[dict], total: float) -> int:
    """Кадры сетки записи, где звучит хоть один голос эталона. Предикат «есть ли
    речь» тот же, что у метрик: ноль здесь — ноль и у них (выходной круг 2 по #648,
    DS C1). Счёт — не их знаменатель: у `der_overlap` это кадро-голоса, и на
    разметке с перекрытиями он больше. Отрезок целиком вне 0…total кадров не даёт,
    заходящий за край — только своей частью внутри (выходной круг 3, DS M1)."""
    return sum(1 for c in _grid_sets(truth, total) if c)


def has_overlap(segments: list[dict]) -> bool:
    """Звучат ли где-нибудь два голоса одновременно (дольше кадра сетки)."""
    ordered = sorted(segments, key=lambda s: s["start"])
    busy_until, who = float("-inf"), None
    for seg in ordered:
        if seg["start"] < busy_until - FRAME and seg["speaker"] != who:
            return True
        if seg["end"] > busy_until:
            busy_until, who = seg["end"], seg["speaker"]
    return False


def _say(voice: str, text: str, dest: pathlib.Path) -> None:
    aiff = dest.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(aiff), str(dest)], check=True,
                   capture_output=True)
    aiff.unlink(missing_ok=True)


def make_fixture(folder: pathlib.Path | None = None,
                 crosstalk: bool = False) -> pathlib.Path:
    """Собрать синтетический диалог и точную разметку к нему."""
    folder = folder or _fixture(crosstalk)
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (voice, text) in enumerate(DIALOG):
        part = folder / f"_part{i}.wav"
        _say(voice, text, part)
        audio, sr = sf.read(part, dtype="float32")
        part.unlink(missing_ok=True)
        if sr != SR:
            raise SystemExit(f"ожидали {SR} Гц, получили {sr}")
        parts.append(audio)

    overlaps = {i: int(sec * SR) for i, sec in CROSSTALK.items()} if crosstalk else {}
    pause = int(PAUSE * SR)
    starts = layout_turns([len(p) for p in parts], pause, overlaps)
    mixed = mix_turns(parts, starts, tail=pause)
    truth = [{"start": round(s / SR, 3), "end": round((s + len(p)) / SR, 3),
              "speaker": voice}
             for (voice, _text), p, s in zip(DIALOG, parts, starts)]

    wav = folder / "dialog.wav"
    sf.write(wav, mixed, SR)
    (folder / "truth.json").write_text(
        json.dumps({"audio": wav.name, "sample_rate": SR, "segments": truth},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    speakers = sorted({s["speaker"] for s in truth})
    print(f"фикстура: {wav} · {len(mixed) / SR:.1f}с · {len(truth)} реплик · "
          f"{len(speakers)} голоса: {', '.join(speakers)}"
          + (f" · перебиваний: {len(overlaps)}" if overlaps else ""))
    return wav


def _grid(segments: list[dict], total: float) -> list[str | None]:
    """Разметка → метка на каждый кадр сетки (None — тишина)."""
    cells: list[str | None] = [None] * int(total / FRAME + 0.5)
    for seg in segments:
        a, b = int(seg["start"] / FRAME), int(seg["end"] / FRAME + 0.5)
        for i in range(max(0, a), min(len(cells), b)):
            cells[i] = str(seg["speaker"])
    return cells


def der(truth: list[dict], hyp: list[dict], total: float) -> dict:
    """DER и его слагаемые.

    Метки гипотезы («speaker 0/1/2») сопоставляются с эталонными жадно, по
    наибольшему перекрытию: диаризация не обязана угадывать ИМЕНА, она обязана
    отличать людей друг от друга.
    """
    ref, sys_ = _grid(truth, total), _grid(hyp, total)
    pairs: dict[tuple[str, str], int] = {}
    for r, s in zip(ref, sys_):
        if r and s:
            pairs[(r, s)] = pairs.get((r, s), 0) + 1

    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for (r, s), _n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        if s not in mapping and r not in taken:
            mapping[s] = r
            taken.add(r)

    speech = sum(1 for r in ref if r)
    if not speech:
        raise ValueError(NO_SPEECH)
    missed = sum(1 for r, s in zip(ref, sys_) if r and not s)
    false = sum(1 for r, s in zip(ref, sys_) if s and not r)
    confusion = sum(1 for r, s in zip(ref, sys_)
                    if r and s and mapping.get(s) != r)
    return {
        "der": (missed + false + confusion) / speech,
        "missed": missed / speech,
        "false_alarm": false / speech,
        "confusion": confusion / speech,
        "speakers_ref": len({r for r in ref if r}),
        "speakers_hyp": len({s for s in sys_ if s}),
    }


def _grid_sets(segments: list[dict], total: float) -> list[frozenset[str]]:
    """Разметка → множество голосов на каждый кадр (пустое — тишина)."""
    cells: list[set[str]] = [set() for _ in range(int(total / FRAME + 0.5))]
    for seg in segments:
        a, b = int(seg["start"] / FRAME), int(seg["end"] / FRAME + 0.5)
        for i in range(max(0, a), min(len(cells), b)):
            cells[i].add(str(seg["speaker"]))
    return [frozenset(c) for c in cells]


def der_overlap(truth: list[dict], hyp: list[dict], total: float, *,
                hyp_single: bool = False) -> dict:
    """DER по NIST: в кадре может звучать несколько голосов.

    В каждом кадре эталон R и гипотеза S — множества. Пропуск —
    max(0, |R|−|S|), лишнее — max(0, |S|−|R|), путаница — min(|R|,|S|) минус
    верно сопоставленные. Сопоставление меток то же, что в `der`: жадное, по
    наибольшему совместному времени, один к одному. Когда ни в разметке, ни в
    гипотезе нет перекрытий, это ровно `der` — тесты держат равенство. Гипотеза
    с собственными перекрытиями (sherpa) судится по NIST и может дать больше
    `der`, который клал её на сетку «последний побеждает» (круг 1 по #648, DS I1).
    Эталон без речи — не «DER 0», а отказ: делить не на что (DS C1).

    hyp_single — движок по контракту даёт один голос на момент (живой
    трекер): его окна перекрываются из-за нарезки чанков, а не потому, что он
    услышал двоих. Такую гипотезу кладём на сетку как раньше, последний
    побеждает, — иначе нарезка засчиталась бы движку как «лишняя речь».
    """
    ref = _grid_sets(truth, total)
    if hyp_single:
        sys_ = [frozenset([s]) if s else frozenset() for s in _grid(hyp, total)]
    else:
        sys_ = _grid_sets(hyp, total)

    pairs: dict[tuple[str, str], int] = {}
    for rs, ss in zip(ref, sys_):
        for r in rs:
            for s in ss:
                pairs[(r, s)] = pairs.get((r, s), 0) + 1
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for (r, s), _n in sorted(pairs.items(), key=lambda kv: -kv[1]):
        if s not in mapping and r not in taken:
            mapping[s] = r
            taken.add(r)

    speech = missed = false = confusion = 0
    for rs, ss in zip(ref, sys_):
        speech += len(rs)
        missed += max(0, len(rs) - len(ss))
        false += max(0, len(ss) - len(rs))
        hit = sum(1 for s in ss if mapping.get(s) in rs)
        confusion += min(len(rs), len(ss)) - hit
    if not speech:
        raise ValueError(NO_SPEECH)
    return {
        "der": (missed + false + confusion) / speech,
        "missed": missed / speech,
        "false_alarm": false / speech,
        "confusion": confusion / speech,
        "speakers_ref": len(set().union(*ref)) if ref else 0,
        "speakers_hyp": len(set().union(*sys_)) if sys_ else 0,
        "overlap_ref_s": sum(1 for rs in ref if len(rs) > 1) * FRAME,
        "overlap_hyp_s": sum(1 for ss in sys_ if len(ss) > 1) * FRAME,
    }


def _live_tracker(sr: int, legacy: bool, cfg_threshold: float):
    """Тот же трекер, что поднимает демон: по кускам речи или по чанкам."""
    from diarize_live import SegmentTracker, SpeakerTracker

    emb = _root() / "models" / "diar" / "embedding.onnx"
    seg = _root() / "models" / "diar" / "segmentation.onnx"
    if not emb.exists():
        raise SystemExit("нет models/diar/embedding.onnx — "
                         ".venv/bin/python scripts/get_models.py --diar")
    if legacy or not seg.exists():
        return SpeakerTracker(emb, sample_rate=sr, threshold=cfg_threshold)
    return SegmentTracker(seg, emb, sample_rate=sr)


def run_live(wav: pathlib.Path, cfg_threshold: float = 0.45,
             legacy: bool = False, split: bool = False,
             overlap: bool = False) -> list[dict]:
    """Живой режим целиком: чанки по 3 секунды, как их получает демон.

    overlap — продакшен-нарезка (шаг 2.5 с при чанке 3.0, audio.py): без неё
    бенч льстил себе, потому что не видел сегментов, разрезанных границей и
    приходящих дважды. split — позиционная раскладка (ревью 15.08): гипотеза
    строится по кускам-окнам внутри чанка, а не одной меткой на чанк.
    """
    audio, sr = sf.read(wav, dtype="float32")
    tracker = _live_tracker(sr, legacy, cfg_threshold)
    need = int(3.0 * sr)
    step = int(2.5 * sr) if overlap else need
    out: list[dict] = []
    for i in range(0, len(audio), step):
        chunk = audio[i:i + need]
        if len(chunk) < int(0.3 * sr):
            break
        if split and hasattr(tracker, "split"):
            res = tracker.split(chunk)
            # тот же трёхсостоянный контракт, что у демона: [] — чанк
            # пропускается, а не красится main целиком (бенч, красивший
            # skip-чанки, завышал качество — ревью 15.08 ×2)
            if res.pieces is not None and not res.pieces:
                continue
            if res.pieces:
                for p in res.pieces:
                    out.append({"start": (i + p.start) / sr,
                                "end": (i + p.end) / sr,
                                "speaker": f"voice{p.voice}"})
                continue
            if res.main is None:
                # как в демоне: fail-open с канальной меткой, а не пропуск
                out.append({"start": i / sr, "end": (i + len(chunk)) / sr,
                            "speaker": "channel"})
                continue
            n = res.main
        else:
            n = tracker.label(chunk)
        if n is None:
            continue
        out.append({"start": i / sr, "end": (i + len(chunk)) / sr,
                    "speaker": f"voice{n}"})
    return out


def switches(events: list[dict]) -> int:
    """Смены говорящего в гипотезе — метрика «мельтешения» стенограммы."""
    order = [e["speaker"] for e in sorted(events, key=lambda e: e["start"])]
    return sum(1 for a, b in zip(order, order[1:]) if a != b)


def run_sherpa(wav: pathlib.Path, num_speakers: int = -1) -> list[dict]:
    """Полная диаризация sherpa-onnx: сегментация pyannote + наш эмбеддер."""
    import sherpa_onnx

    seg = _root() / "models" / "diar" / "segmentation.onnx"
    emb = _root() / "models" / "diar" / "embedding.onnx"
    for path, flag in ((seg, "--segmentation"), (emb, "--diar")):
        if not path.exists():
            raise SystemExit(f"нет {path} — "
                             f".venv/bin/python scripts/get_models.py {flag}")
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    diar = sherpa_onnx.OfflineSpeakerDiarization(config)
    audio, sr = sf.read(wav, dtype="float32")
    if sr != diar.sample_rate:
        raise SystemExit(f"модель ждёт {diar.sample_rate} Гц, у файла {sr}")
    return [{"start": s.start, "end": s.end, "speaker": f"spk{s.speaker}"}
            for s in diar.process(audio).sort_by_start_time()]




def _nemotron(model_dir: pathlib.Path | None, preset: str):
    try:
        return nemotron.load_model(model_dir or nemotron.model_dir(_root()), preset)
    except nemotron.ModelUnavailable as e:
        raise SystemExit(f"nemotron: {e}") from None


def run_nemotron(wav: pathlib.Path, model_dir: pathlib.Path | None = None) -> list[dict]:
    """Nemotron по всему файлу, пресет offline (буфер 30.4 с) — потолок модели."""
    model = _nemotron(model_dir, "offline")
    audio, sr = sf.read(wav, dtype="float32")
    return nemotron.diarize_file(model, audio, sr)


def run_nemotron_live(wav: pathlib.Path, model_dir: pathlib.Path | None = None,
                      preset: str = "low", step: float = 0.5) -> list[dict]:
    """Nemotron так, как его увидел бы демон: поток блоками по step секунд.

    От нарезки входа ответ не зависит (кэш голосов переносится между блоками),
    от пресета — зависит: чем короче правый контекст, тем меньше задержка и
    тем меньше модель знает о продолжении фразы.
    """
    model = _nemotron(model_dir, preset)
    audio, sr = sf.read(wav, dtype="float32")
    if sr != nemotron.SAMPLE_RATE:
        raise SystemExit(f"поток ждёт {nemotron.SAMPLE_RATE} Гц, у файла {sr}")
    stream = nemotron.NemotronStream(model)
    n = int(step * sr)
    out: list[dict] = []
    for i in range(0, len(audio), n):
        out += stream.feed(audio[i:i + n])
    out += stream.close()
    return nemotron.merge_same_speaker(out)


def _mark(start: float, end: float, who: str, where: str) -> dict | None:
    """Одна запись эталона → отрезок, `None` для метки-точки или ValueError с местом.

    Проверка одна для JSON и текстовых форматов — трактовка записи у двух входов не
    разъезжается. NaN прежде молча выбрасывал запись, бесконечность роняла охрану
    трассировкой `OverflowError`, а вывернутый отрезок (конец раньше начала) молча
    укорачивал эталон (выходной круг 3 по #648, DS M2/M3). Метка-точка — Ctrl+B в
    Audacity без выделения — по-прежнему не речь и пропускается.
    """
    if not (math.isfinite(start) and math.isfinite(end)):
        raise ValueError(f"{where}: время — не конечное число ({start}, {end})")
    if not who:
        raise ValueError(f"{where}: нет имени говорящего")
    if end < start:
        raise ValueError(f"{where}: конец раньше начала ({start} → {end})")
    if end == start:
        return None
    return {"start": start, "end": end, "speaker": who}


def read_truth(path: pathlib.Path) -> list[dict]:
    """Эталон своей записи: JSON бенча, RTTM или метки Audacity.

    Метки Audacity — самый короткий путь к эталону на живой встрече: выделить
    реплику, Ctrl+B, имя; File → Export → Labels. Строки, начинающиеся с «\\»,
    Audacity пишет для частотного выделения — они пропускаются. Любая беда файла —
    ValueError с файлом в тексте: чтение, JSON, запись с номером.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: разметка не читается ({type(e).__name__}: {e})") from None
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"{path}: не JSON ({e})") from None
        if isinstance(data, dict) and "segments" not in data:
            # дверь чтения эталона отказывает одним типом с файлом в тексте, как у
            # текстовых форматов ниже, а не трассировкой KeyError (круг 1 по #648, DS M3)
            raise ValueError(f"{path}: в JSON бенча нет ключа segments")
        records = data["segments"] if isinstance(data, dict) else data
        if not isinstance(records, list):
            raise ValueError(f"{path}: segments — не список отрезков")
        out: list[dict] = []
        for n, rec in enumerate(records, 1):
            # каждая запись — как строка текстового формата: число, число, имя; битая —
            # отказ с номером до прогона движков, а не KeyError в метрике после (круг 2, DS I3)
            try:
                start, end = (float(rec[k]) for k in ("start", "end"))
                who = str(rec["speaker"]).strip()
            except (TypeError, KeyError, ValueError) as e:
                raise ValueError(f"{path}: отрезок №{n} — ждали start, end и speaker ({e!r})") from None
            mark = _mark(start, end, who, f"{path}: отрезок №{n}")
            if mark:
                out.append(mark)
        return out
    out: list[dict] = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("\\"):
            continue
        head = line.split()
        try:
            if head[0] == "SPEAKER":        # RTTM: тип файл канал начало длит. … кто
                start, end, who = float(head[3]), float(head[3]) + float(head[4]), head[7]
            else:
                cols = line.split("\t") if "\t" in line else line.split(maxsplit=2)
                start, end = (float(c.replace(",", ".")) for c in cols[:2])
                who = cols[2].strip()
        except (IndexError, ValueError) as e:
            raise ValueError(f"{path}:{n}: ждали «начало, конец, кто говорит» "
                             f"(метки Audacity) или строку RTTM — {e}") from None
        mark = _mark(start, end, who, f"{path}:{n}")
        if mark:
            out.append(mark)
    return out


def _sound(wav: pathlib.Path):
    """`sf.info` записи или ValueError с файлом: битая запись — строка, а не трассировка."""
    try:
        return sf.info(str(wav))
    except (sf.LibsndfileError, OSError) as e:
        raise ValueError(f"{wav}: запись не читается ({type(e).__name__}: {e})") from None


def reference(*, wav: pathlib.Path | None, truth: pathlib.Path | None,
              fixture: pathlib.Path, crosstalk: bool = False
              ) -> tuple[pathlib.Path, float, list[dict] | None]:
    """Запись, её длительность и эталон — готовой тройкой, или ValueError с причиной.

    Одна дверь на оба входа — своя запись (`--wav`, `--truth`) и фикстура. Прежде
    отказ превращался в строку у вызывающего, и ветка фикстуры его не ловила:
    битая `truth.json` давала трассировку, та же разметка через `--truth` — строку
    (выходной круг 3 по #648, DS I1). Здесь же — приговор «в эталоне нет речи»:
    эталон нельзя получить, не пройдя охрану.
    """
    if wav is None:
        truth = fixture / "truth.json"
        if not truth.exists():
            flag = " --crosstalk" if crosstalk else ""
            raise ValueError(f"нет фикстуры ({truth}) — соберите: "
                             f".venv/bin/python scripts/diar_bench.py --make{flag}")
        try:
            data = json.loads(truth.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{truth}: фикстура не читается ({type(e).__name__}: {e})") from None
        if not isinstance(data, dict) or not isinstance(data.get("audio"), str):
            raise ValueError(f"{truth}: в фикстуре нет имени записи (ключ audio) — "
                             f"пересоберите: .venv/bin/python scripts/diar_bench.py --make")
        wav = fixture / data["audio"]
    elif not wav.is_file():
        raise ValueError(f"нет файла {wav}")
    info = _sound(wav)
    if info.channels != 1 or info.samplerate != SR:
        raise ValueError(f"{wav.name}: {info.channels} кан. × {info.samplerate} Гц, а нужен моно "
                         f"{SR} Гц — перегнать: afconvert -f WAVE -d LEI16@{SR} -c 1 "
                         f"{wav} {wav.with_name(wav.stem + '_16k.wav')}")
    if truth is not None and not truth.is_file():
        raise ValueError(f"нет файла разметки {truth}")       # DS M5 круга 2
    marks = read_truth(truth) if truth is not None else None
    total = info.frames / info.samplerate
    if marks is not None and not speech_frames(marks, total):
        # пустой эталон — отказ до прогона, а не «DER 0.000» и не трассировка метрики
        # после движков: предикат тот же, что у метрики (DS C1 кругов 1 и 2)
        raise ValueError(f"{truth.name}: {NO_SPEECH} (отрезки вне 0–{total:.1f} с, метки-точки "
                         f"и строки частотного выделения Audacity речью не считаются)")
    return wav, total, marks


def write_labels(segments: list[dict], path: pathlib.Path) -> None:
    """Гипотеза движка → дорожка меток Audacity (File → Import → Labels)."""
    lines = [f"{s['start']:.3f}\t{s['end']:.3f}\t{s['speaker']}"
             for s in sorted(segments, key=lambda s: (s["start"], str(s["speaker"])))]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def summary(hyp: list[dict], total: float, hyp_single: bool = False) -> dict:
    """Что движок нашёл, когда эталона нет: голоса, речь, перекрытия, смены."""
    if hyp_single:
        grid = [frozenset([s]) if s else frozenset() for s in _grid(hyp, total)]
    else:
        grid = _grid_sets(hyp, total)
    return {
        "voices": len(set().union(*grid)) if grid else 0,
        "speech_s": sum(1 for c in grid if c) * FRAME,
        "overlap_s": sum(1 for c in grid if len(c) > 1) * FRAME,
        "switches": switches(hyp),
    }


def _timing(elapsed: float, total: float) -> str:
    return f"время {elapsed:.1f} с (RTF {elapsed / total:.2f})" if total else ""


def report(name: str, scores: dict, hyp: list[dict] | None = None,
           elapsed: float = 0.0, total: float = 0.0) -> None:
    print(f"  {name:<14} DER {scores['der']:.3f}  "
          f"(пропущено {scores['missed']:.3f} · лишнее {scores['false_alarm']:.3f} · "
          f"путаница {scores['confusion']:.3f}) · "
          f"голосов {scores['speakers_hyp']} из {scores['speakers_ref']}")
    extra = [f"смен говорящего {switches(hyp)}"] if hyp is not None else []
    if scores.get("overlap_ref_s") or scores.get("overlap_hyp_s"):
        extra.append(f"перекрытие: в разметке {scores['overlap_ref_s']:.1f} с, "
                     f"у движка {scores['overlap_hyp_s']:.1f} с")
    if total:
        extra.append(_timing(elapsed, total))
    if extra:
        print(f"  {'':<14} {' · '.join(extra)}")


def report_blind(name: str, summ: dict, elapsed: float, total: float) -> None:
    print(f"  {name:<14} голосов {summ['voices']} · речь {summ['speech_s']:.1f} с · "
          f"перекрытие {summ['overlap_s']:.1f} с · смен говорящего {summ['switches']}")
    print(f"  {'':<14} {_timing(elapsed, total)}")


#: Группы движков для --engine. «both» и «all» — прежние наборы; «compare» —
#: очная ставка: лучший живой режим и проход после встречи против Nemotron.
GROUPS = {
    "both": ("live", "sherpa"),
    "all": ("live", "live-split", "live-legacy", "sherpa"),
    "compare": ("live-split", "sherpa", "nemotron", "nemotron-live"),
}
ENGINE_NAMES = ("live", "live-split", "live-legacy", "sherpa", "nemotron", "nemotron-live")


def _engines(args: argparse.Namespace) -> dict:
    """Движок → (прогон по wav, одна метка на момент?).

    Второе поле — контракт движка для перекрытий, см. der_overlap(hyp_single=…):
    живой трекер по построению называет одного говорящего на чанк.
    """
    return {
        "live": (lambda wav: run_live(wav, overlap=args.overlap), True),
        "live-split": (lambda wav: run_live(wav, split=True, overlap=args.overlap), True),
        "live-legacy": (lambda wav: run_live(wav, legacy=True, overlap=args.overlap), True),
        "sherpa": (lambda wav: run_sherpa(wav, args.speakers), False),
        "nemotron": (lambda wav: run_nemotron(wav, args.nemotron_model), False),
        "nemotron-live": (lambda wav: run_nemotron_live(
            wav, args.nemotron_model, args.nemotron_preset), False),
    }


def main() -> int:
    harden_umask()   # фикстура бенча ложится в data/ корня данных — только владельцу
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--make", action="store_true", help="собрать синтетику и выйти")
    ap.add_argument("--crosstalk", action="store_true",
                    help="фикстура с перебиваниями: часть реплик звучит "
                         "одновременно (data/diar_bench_crosstalk)")
    ap.add_argument("--fixture", type=pathlib.Path, default=None)
    ap.add_argument("--wav", type=pathlib.Path, default=None,
                    help="своя запись вместо фикстуры: моно 16 кГц "
                         "(например, канал собеседников из recordings/)")
    ap.add_argument("--truth", type=pathlib.Path, default=None,
                    help="разметка к --wav: метки Audacity (.txt), RTTM или JSON бенча")
    ap.add_argument("--labels", type=pathlib.Path, default=None,
                    help="каталог: записать гипотезу каждого движка дорожкой "
                         "меток Audacity (<движок>.txt)")
    ap.add_argument("--engine", choices=ENGINE_NAMES + tuple(GROUPS), default="both",
                    help="live — метка на чанк, live-split — позиционная "
                         "раскладка по кускам, live-legacy — прежний трекер "
                         "по чанкам, sherpa — проход после встречи, nemotron — "
                         "Nemotron 3 Diarization по всему файлу, nemotron-live — "
                         "он же потоком; группы: both, all, compare")
    ap.add_argument("--overlap", action="store_true",
                    help="продакшен-нарезка: чанк 3.0 с шагом 2.5 (перекрытие "
                         "0.5), как режет audio.py")
    ap.add_argument("--speakers", type=int, default=-1,
                    help="сколько голосов ждать (-1 — решает кластеризация)")
    ap.add_argument("--nemotron-model", type=pathlib.Path, default=None,
                    help="каталог весов Nemotron (по умолчанию models/diar/nemotron "
                         "в корне данных)")
    ap.add_argument("--nemotron-preset", choices=nemotron.PRESETS, default="low",
                    help="задержка потока nemotron-live: low — 1.04 с, very_low — "
                         "0.64 с, ultra_low — 0.32 с, offline — 30.4 с")
    args = ap.parse_args()
    if args.truth and not args.wav:
        ap.error("--truth — разметка к своей записи, нужен и --wav")
    if args.make and args.wav:
        ap.error("--make собирает синтетику, --wav — своя запись: одно из двух")

    if args.make:
        make_fixture(args.fixture or _fixture(args.crosstalk), crosstalk=args.crosstalk)
        return 0

    names = GROUPS.get(args.engine, (args.engine,))
    if args.engine == "compare" and not args.overlap:
        # очная ставка решает встраивание Nemotron — живой движок в ней режется как в
        # демоне, а не встык, который льстит себе (выходной круг 1 по #648, DS I5)
        args.overlap = True
        print("compare: живые движки — нарезкой демона (--overlap)")
    if any(n.startswith("nemotron") for n in names):
        problem = nemotron.availability(args.nemotron_model or nemotron.model_dir(_root()))
        if problem:
            print(f"nemotron: {problem}", file=sys.stderr)
            return 1

    try:
        wav, total, truth = reference(wav=args.wav, truth=args.truth,
                                      fixture=args.fixture or _fixture(args.crosstalk),
                                      crosstalk=args.crosstalk)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    if truth is None:
        print(f"{wav.name}: {total:.1f}с, разметки нет — DER не считается\n")
    else:
        crosstalk = sum(1 for c in _grid_sets(truth, total) if len(c) > 1) * FRAME
        print(f"{wav.name}: {total:.1f}с, голосов в разметке: "
              f"{len({s['speaker'] for s in truth})}"
              + (f", одновременной речи {crosstalk:.1f}с" if crosstalk else "") + "\n")

    if args.labels:
        args.labels.mkdir(parents=True, exist_ok=True)
    engines = _engines(args)
    for name in names:
        run, single = engines[name]
        started = time.perf_counter()
        hyp = run(wav)
        elapsed = time.perf_counter() - started
        if truth is None:
            report_blind(name, summary(hyp, total, single), elapsed, total)
        else:
            report(name, der_overlap(truth, hyp, total, hyp_single=single),
                   hyp, elapsed, total)
        if args.labels:
            write_labels(hyp, args.labels / f"{name}.txt")
    if truth is not None:
        print("\nDER — доля времени речи, подписанная неверно. Меньше лучше.")
    print("Время — с загрузкой модели; RTF — время / длительность записи.")
    if args.labels:
        print(f"Метки для Audacity: {args.labels}/<движок>.txt (File → Import → Labels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
