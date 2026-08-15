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

Синтетика. Записей встреч в репозитории нет и быть не может — это чужие
разговоры. Поэтому фикстура собирается на месте из системного синтезатора
речи macOS: несколько разных голосов произносят реплики, они склеиваются в
диалог с паузами, и разметка «кто когда говорил» известна точно, потому что мы
сами её и составили.

Честная оговорка: синтезированные голоса чище живых, у них нет перебивания,
эха и шума комнаты. Такой бенч — нижняя планка: движок, который путает
говорящих ЗДЕСЬ, на живой встрече будет хуже. Обратное неверно, и цифры отсюда
нельзя выдавать за качество на реальных встречах.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SR = 16000
FRAME = 0.01            # шаг сетки при подсчёте DER, 10 мс — стандарт
FIXTURE = ROOT / "data" / "diar_bench"

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


def _say(voice: str, text: str, dest: pathlib.Path) -> None:
    aiff = dest.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(aiff), str(dest)], check=True,
                   capture_output=True)
    aiff.unlink(missing_ok=True)


def make_fixture(folder: pathlib.Path = FIXTURE) -> pathlib.Path:
    """Собрать синтетический диалог и точную разметку к нему."""
    folder.mkdir(parents=True, exist_ok=True)
    pieces, truth, cursor = [], [], 0.0
    silence = np.zeros(int(PAUSE * SR), dtype=np.float32)
    for i, (voice, text) in enumerate(DIALOG):
        part = folder / f"_part{i}.wav"
        _say(voice, text, part)
        audio, sr = sf.read(part, dtype="float32")
        part.unlink(missing_ok=True)
        if sr != SR:
            raise SystemExit(f"ожидали {SR} Гц, получили {sr}")
        pieces += [audio, silence]
        truth.append({"start": round(cursor, 3),
                      "end": round(cursor + len(audio) / SR, 3),
                      "speaker": voice})
        cursor += len(audio) / SR + PAUSE

    wav = folder / "dialog.wav"
    sf.write(wav, np.concatenate(pieces), SR)
    (folder / "truth.json").write_text(
        json.dumps({"audio": wav.name, "sample_rate": SR, "segments": truth},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    speakers = sorted({s["speaker"] for s in truth})
    print(f"фикстура: {wav} · {cursor:.1f}с · {len(truth)} реплик · "
          f"{len(speakers)} голоса: {', '.join(speakers)}")
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
    missed = sum(1 for r, s in zip(ref, sys_) if r and not s)
    false = sum(1 for r, s in zip(ref, sys_) if s and not r)
    confusion = sum(1 for r, s in zip(ref, sys_)
                    if r and s and mapping.get(s) != r)
    return {
        "der": (missed + false + confusion) / speech if speech else 0.0,
        "missed": missed / speech if speech else 0.0,
        "false_alarm": false / speech if speech else 0.0,
        "confusion": confusion / speech if speech else 0.0,
        "speakers_ref": len({r for r in ref if r}),
        "speakers_hyp": len({s for s in sys_ if s}),
    }


def _live_tracker(sr: int, legacy: bool, cfg_threshold: float):
    """Тот же трекер, что поднимает демон: по кускам речи или по чанкам."""
    from diarize_live import SegmentTracker, SpeakerTracker

    emb = ROOT / "models" / "diar" / "embedding.onnx"
    seg = ROOT / "models" / "diar" / "segmentation.onnx"
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

    seg = ROOT / "models" / "diar" / "segmentation.onnx"
    emb = ROOT / "models" / "diar" / "embedding.onnx"
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


def report(name: str, scores: dict) -> None:
    print(f"  {name:<10} DER {scores['der']:.3f}  "
          f"(пропущено {scores['missed']:.3f} · лишнее {scores['false_alarm']:.3f} · "
          f"путаница {scores['confusion']:.3f}) · "
          f"голосов {scores['speakers_hyp']} из {scores['speakers_ref']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--make", action="store_true", help="собрать синтетику и выйти")
    ap.add_argument("--fixture", type=pathlib.Path, default=FIXTURE)
    ap.add_argument("--engine",
                    choices=("live", "live-split", "live-legacy", "sherpa",
                             "both", "all"),
                    default="both",
                    help="live — метка на чанк, live-split — позиционная "
                         "раскладка по кускам, live-legacy — прежний трекер "
                         "по чанкам, sherpa — проход после встречи")
    ap.add_argument("--overlap", action="store_true",
                    help="продакшен-нарезка: чанк 3.0 с шагом 2.5 (перекрытие "
                         "0.5), как режет audio.py")
    ap.add_argument("--speakers", type=int, default=-1,
                    help="сколько голосов ждать (-1 — решает кластеризация)")
    args = ap.parse_args()

    if args.make:
        make_fixture(args.fixture)
        return 0

    truth_file = args.fixture / "truth.json"
    if not truth_file.exists():
        print(f"нет фикстуры ({truth_file}) — соберите: "
              f".venv/bin/python scripts/diar_bench.py --make", file=sys.stderr)
        return 1
    data = json.loads(truth_file.read_text(encoding="utf-8"))
    wav = args.fixture / data["audio"]
    audio, sr = sf.read(wav, dtype="float32")
    total = len(audio) / sr
    print(f"{wav.name}: {total:.1f}с, голосов в разметке: "
          f"{len({s['speaker'] for s in data['segments']})}\n")

    if args.engine in ("live", "both", "all"):
        hyp = run_live(wav, overlap=args.overlap)
        report("live", der(data["segments"], hyp, total))
        print(f"             смен говорящего: {switches(hyp)}")
    if args.engine in ("live-split", "all"):
        hyp = run_live(wav, split=True, overlap=args.overlap)
        report("live-split", der(data["segments"], hyp, total))
        print(f"             смен говорящего: {switches(hyp)}")
    if args.engine in ("live-legacy", "all"):
        report("live-legacy",
               der(data["segments"], run_live(wav, legacy=True,
                                              overlap=args.overlap), total))
    if args.engine in ("sherpa", "both", "all"):
        report("sherpa", der(data["segments"], run_sherpa(wav, args.speakers), total))
    print("\nDER — доля времени речи, подписанная неверно. Меньше лучше.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
