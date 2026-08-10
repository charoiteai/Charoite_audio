#!/usr/bin/env python3
"""Бенч распознавания: сколько знаков пришло не теми.

Качество памяти меряется (`memory_bench.py`), диаризации — тоже
(`diar_bench.py`), а распознавание до сих пор оценивалось на слух. Поэтому
«whisper на китайском хуже» было мнением, а выбор бэкенда — верой.

Здесь считается CER (character error rate) — доля символов, которые пришлось
бы вставить, удалить или заменить, чтобы получить эталон. Для китайского это
уместнее WER: слова там не разделены пробелами, и любое членение — уже
решение алгоритма, а не факт.

    .venv/bin/python scripts/stt_bench.py --backend sensevoice
    .venv/bin/python scripts/stt_bench.py --backend whisper --lang zh
    .venv/bin/python scripts/stt_bench.py --compare          # оба, одним заходом

Синтетика. Записей встреч в репозитории нет и быть не может — это чужие
разговоры. Фикстура собирается на месте системным синтезатором macOS: голос
читает фразы, эталон известен точно, потому что мы сами его и написали.

Честная оговорка, та же, что у diar_bench: синтезированная речь чище живой —
без перебиваний, эха и комнаты. Это нижняя планка, а не бенчмарк. Движок,
который путается ЗДЕСЬ, на живой встрече будет хуже; обратное неверно, и
выдавать эти цифры за качество на реальных встречах нельзя.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

# Фразы рабочей встречи, а не скороговорки: цифры, время, термины — то, на чём
# распознавание ломается и то, ради чего его вообще читают.
PHRASES_ZH = [
    "我们决定选择 YuPay，费率百分之二点八。",
    "玛丽亚在七月二十二日之前签好合同。",
    "下周一上午十点开会讨论集成方案。",
    "认证还没有通过，风险在于时间表。",
]
PHRASES_EN = [
    "We decided to go with YuPay, the fee is two point eight percent.",
    "Maria signs the contract before July twenty second.",
    "The next meeting is Monday at ten in the morning.",
]
VOICES = {"zh": "Eddy (Chinese (China mainland))", "en": "Samantha"}


def synth(text: str, lang: str, dest: pathlib.Path) -> bool:
    """Наговорить фразу системным синтезатором в 16 кГц mono wav."""
    aiff = dest.with_suffix(".aiff")
    voice = VOICES.get(lang, "Samantha")
    try:
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text],
                       check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(aiff), str(dest)], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"синтез не удался ({voice}): {e}")
        return False
    finally:
        aiff.unlink(missing_ok=True)
    return dest.exists()


def cer(reference: str, hypothesis: str) -> float:
    """Расстояние Левенштейна по символам, нормированное на длину эталона.

    Пунктуация и пробелы выброшены: STT расставляет их по-своему, и считать
    это ошибкой распознавания — завышать метрику на ровном месте.
    """
    import unicodedata

    def norm(s: str) -> str:
        return "".join(c.lower() for c in s
                       if not unicodedata.category(c).startswith(("P", "Z")))

    ref, hyp = norm(reference), norm(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def run(backend: str, lang: str, phrases: list[str]) -> tuple[float, int]:
    """Прогнать фразы через бэкенд. Возвращает средний CER и число фраз."""
    from stt import STT

    cfg = {"stt": {"backend": backend, "language": lang,
                   "whisper_model": "mlx-community/whisper-large-v3-turbo",
                   "gigaam_model": "gigaam-v3-e2e-rnnt",
                   "parakeet_model": "mlx-community/parakeet-tdt-0.6b-v3"},
           "sufler": {}}
    engine = STT(cfg)
    import numpy as np

    total, done = 0.0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, phrase in enumerate(phrases):
            wav = pathlib.Path(tmp) / f"p{i}.wav"
            if not synth(phrase, lang, wav):
                continue
            with wave.open(str(wav), "rb") as w:
                rate = w.getframerate()
                audio = np.frombuffer(w.readframes(w.getnframes()),
                                      dtype=np.int16).astype("float32") / 32768.0
            text = engine.transcribe(audio, rate)
            score = cer(phrase, text)
            total += score
            done += 1
            print(f"  CER {score:5.3f}  ← {text[:58]}")
    return (total / done if done else 1.0), done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", default="sensevoice",
                    help="sensevoice | whisper | parakeet | gigaam")
    ap.add_argument("--lang", default="zh", choices=sorted(VOICES),
                    help="язык фикстуры (zh — китайский, en — английский)")
    ap.add_argument("--compare", action="store_true",
                    help="прогнать sensevoice и whisper подряд и сравнить")
    args = ap.parse_args()

    phrases = PHRASES_ZH if args.lang == "zh" else PHRASES_EN
    backends = ["sensevoice", "whisper"] if args.compare else [args.backend]
    results: dict[str, float] = {}
    for backend in backends:
        print(f"\n{backend} · {args.lang} · {len(phrases)} фраз:")
        try:
            score, done = run(backend, args.lang, phrases)
        except Exception as e:  # noqa: BLE001 — бенч не должен падать трейсбеком
            print(f"  не запустился: {e}")
            continue
        if not done:
            print("  ни одной фразы не синтезировалось")
            continue
        results[backend] = score
        print(f"  средний CER: {score:.3f}")

    if len(results) > 1:
        best = min(results, key=results.get)
        print(f"\nлучше на этой фикстуре: {best} "
              f"({', '.join(f'{k} {v:.3f}' for k, v in results.items())})")
    print("\nСинтезированная речь чище живой: это нижняя планка, не бенчмарк.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
