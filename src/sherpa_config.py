"""Одна фабрика конфигов sherpa-onnx для разметки голосов (№508).

Число потоков у sherpa-onnx по умолчанию — 1 (1.13.5), и разметка канала после
встречи шла одним ядром из восьми: микрофон 69-минутной встречи 29.09 — около
22 минут. Сколько потоков брать, решается здесь и в момент сборки конфига:

- живой путь (трекеры демона на встрече) — всегда 1: встреча важнее скорости;
- после встречи — 1, пока идёт живая запись (пересборка, уступившая встрече до
  потолка, продолжает в тесноте и не отнимает ядра у захвата и STT), иначе —
  наибольшее измеренное значение, не больше половины производительных ядер.

Измеренные значения — только те, для которых замер подтвердил те же метки, что
у одного потока (порог — в карточке №508). Неизмеренное число сюда не попадает:
порядок редукций onnxruntime зависит от числа потоков.

Число фиксируется на весь проход: конфиг собирается один раз в начале разметки, и
встреча, начавшаяся посреди многоминутного прохода, получит рядом с собой N потоков.
Уступка внутри прохода — окнами, между которыми можно ждать встречу, — механизм №510.

Конструкторы конфигов sherpa для сегментации и эмбеддингов вне этого модуля
запрещены сторожем (`tests/test_sherpa_config.py`): иначе новый конфиг снова
молча возьмёт умолчание библиотеки.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import live_gate  # noqa: E402

#: Живой путь: трекеры демона во время встречи.
LIVE = "live"
#: Разбор после встречи: пересборка, повтор, восстановление сирот, CLI.
POST = "post"

#: Числа потоков, допущенные к разбору после встречи: замер показал те же метки, что у
#: одного потока, а цена для живой встречи рядом — в пределах.
#:
#: Замер 29.09 (№508; sherpa-onnx 1.13.7, onnxruntime колеса 1.28.1, M1 Max): 20 минут
#: микрофона встречи и 20 минут канала собеседников с подсказкой 8 голосов — на 2 и 4
#: потоках сегменты побайтно те же, 0 из 101 001 и 0 из 96 356 кадров речи расходятся;
#: проход быстрее в 1,53 раза на 2 потоках и в 2,28 на 4.
#:
#: 4 в список не входит: проход, начатый до встречи, держит свои потоки до конца, и рядом
#: с ним живое распознавание (GigaAM, отрезки по 8 с) теряет 55 % на p95 при 4 потоках
#: под nice 10 и 9 % при 2. Четыре — когда проход научится уступать встрече посреди
#: работы (№510). Другая версия sherpa-onnx или onnxruntime — повод перемерить (№472).
MEASURED_THREADS: tuple[int, ...] = (1, 2)
#: Потолок опроса `sysctl`, секунды.
SYSCTL_TIMEOUT_S = 2


def performance_cores() -> int:
    """Производительные ядра машины: `hw.perflevel0.physicalcpu` на Apple Silicon.

    `os.cpu_count()` считает и энергоэффективные: на 4P+6E это 10, и половина
    от него заняла бы все производительные ядра. Нет ключа (Intel, Rosetta,
    не macOS) — половина логических ядер, но не меньше одного."""
    try:
        n = int(subprocess.check_output(["/usr/sbin/sysctl", "-n", "hw.perflevel0.physicalcpu"],
                                        stderr=subprocess.DEVNULL, timeout=SYSCTL_TIMEOUT_S))
        if n > 0:
            return n
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return max(1, (os.cpu_count() or 0) // 2)


def threads_for(kind: str, root: pathlib.Path | None = None) -> int:
    """Сколько потоков дать sherpa для этого вида работы — сейчас."""
    if kind == LIVE:
        return 1
    if kind != POST:
        raise ValueError(f"вид работы {kind!r} неизвестен; есть: {LIVE}, {POST}")
    if root is not None and live_gate.daemon_alive(root):
        return 1
    cap = performance_cores() // 2
    return max((n for n in MEASURED_THREADS if n <= cap), default=1)


def segmentation_config(model: pathlib.Path, *, kind: str, root: pathlib.Path | None = None):
    """Конфиг сегментации pyannote с явным числом потоков."""
    import sherpa_onnx
    return sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(model)),
        num_threads=threads_for(kind, root))


def embedding_config(model: pathlib.Path, *, kind: str, root: pathlib.Path | None = None):
    """Конфиг извлечения эмбеддингов голоса с явным числом потоков."""
    import sherpa_onnx
    return sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model),
                                                       num_threads=threads_for(kind, root))
