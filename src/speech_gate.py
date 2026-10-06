"""Энергетический гейт речи — одна формула для хаба захвата и пересборки (№586).

Чанк «речевой», когда его RMS в дБFS выше `audio.vad_energy_db`. Это не модель
речи: фон конференц-приложения проходит гейт на 47–99 % чанков звонка (замер
02.10, 11 звонков), уведомление — одним-тремя чанками. Поэтому признак звонка
судит не отдельный чанк, а их плотность в окне (`owner_voice.call_from_gate`).

Хаб режет живой поток на чанки `chunk_seconds` с шагом `chunk_seconds −
overlap_seconds` и зовёт `chunk_is_speech` на каждом; пересборка режет запись тем
же разрезом от нуля (`speech_starts`). Формула одна, результат — приближённо
один: фаза разреза хаба зависит от момента открытия канала и его перезапусков,
поэтому запас несёт порог, а не равенство счёта (вход №586, круг 1).

Модуль без захвата и без конфига — только numpy: его берут и хаб, и пересборка.
"""
from __future__ import annotations

import numpy as np


def chunk_is_speech(chunk: np.ndarray, vad_db: float) -> bool:
    """RMS чанка в дБFS выше порога."""
    rms = float(np.sqrt(np.mean(chunk ** 2)) + 1e-9)
    return 20 * np.log10(rms) > vad_db


def settings(audio: dict) -> tuple[float, float, float]:
    """Разрез и порог из раздела `audio` конфига: (chunk_s, overlap_s, vad_db).

    Один читатель на хаб и пересборку: ключ, переименованный в одном месте,
    не разведёт живой счёт с пересборочным молча.
    """
    return (float(audio["chunk_seconds"]), float(audio["overlap_seconds"]),
            float(audio["vad_energy_db"]))


def step_seconds(chunk_s: float, overlap_s: float) -> float:
    """Шаг разреза: новые секунды звука, которые приносит каждый чанк."""
    return chunk_s - overlap_s


def speech_starts(x: np.ndarray, sr: int, chunk_s: float, overlap_s: float,
                  vad_db: float) -> list[float]:
    """Моменты начала (с от начала записи) чанков, прошедших гейт.

    Разрез как у хаба: окно `chunk_s`, шаг `step_seconds`; хвост короче окна
    не судится — хаб тоже ждёт полного чанка.
    """
    need = int(sr * chunk_s)
    step = int(sr * step_seconds(chunk_s, overlap_s))
    if need <= 0 or step <= 0:
        return []
    return [i / sr for i in range(0, len(x) - need + 1, step)
            if chunk_is_speech(x[i:i + need], vad_db)]


def zero_runs(x: np.ndarray, sr: int, min_s: float,
              block_s: float = 0.1) -> list[tuple[float, float]]:
    """Серии подряд идущих блоков по `block_s`, где ВСЕ отсчёты точно ноль.

    Точный ноль — не «тихо», а отсутствие записи: канал собеседников,
    переставший писаться посреди звонка, отдаёт цифровые нули. Один ненулевой
    отсчёт в блоке рвёт серию (щелчок, шум нарезки). В выход — серии длиной не
    меньше `min_s`, в секундах от начала записи; серия, дотянувшаяся до конца
    записи, тоже. Неполный последний блок не считается вовсе: как и у гейта,
    хвост короче окна не судится.

    Не знает ни про пересборку, ни про каналы: тем же разрезом хабу нужны
    нули записи в карточке №654.
    """
    width = int(sr * block_s)
    if width <= 0 or len(x) == 0:
        return []
    out: list[tuple[float, float]] = []
    run_start: int | None = None
    i = 0
    while i + width <= len(x):
        if np.any(x[i:i + width]):
            if run_start is not None:
                if (i - run_start) / sr >= min_s:
                    out.append((run_start / sr, i / sr))
                run_start = None
        elif run_start is None:
            run_start = i
        i += width
    if run_start is not None and (i - run_start) / sr >= min_s:
        out.append((run_start / sr, i / sr))
    return out
