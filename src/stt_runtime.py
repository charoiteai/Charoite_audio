"""Small, deterministic policies for keeping live STT ahead of audio.

The runtime code lives in ``daemon.py``; only the decision is here so its
hysteresis can be tested without loading audio devices or inference models.
"""
from __future__ import annotations

import math


def should_shed_diarization(*, backlog_seconds: float, active: bool,
                            chunk_seconds: float) -> bool:
    """Whether live positional diarization should yield to transcription.

    Enter after two chunks (never below six seconds) and recover only after
    the queue falls below half a chunk.  Hysteresis prevents switching modes
    on every pull.  Non-finite telemetry fails safe toward the cheaper path.
    The recording on disk is independent and remains untouched.
    """
    if not math.isfinite(backlog_seconds):
        return True
    backlog_seconds = max(0.0, backlog_seconds)
    chunk_seconds = max(0.1, chunk_seconds)
    enter = max(6.0, 2.0 * chunk_seconds)
    recover = max(1.0, 0.5 * chunk_seconds)
    if active:
        return backlog_seconds > recover
    return backlog_seconds >= enter


def use_positional_split(*, lagging: bool, has_split: bool) -> bool:
    """Whether this chunk goes through live positional diarization.

    Kept as a pure function so both branches of the daemon's job planning
    are pinned by behavioural tests: a survived ``and``/``or`` mutation in
    the inline condition meant "diarization silently off forever" while
    every string-based assertion stayed green (review 21.08, GLM).
    """
    return has_split and not lagging


# --- Решения живого контура, вынесенные из замыканий daemon.main() ---------
#
# stt_loop, report_progress и главный цикл heartbeat живут ВНУТРИ main():
# unit-тест до них не дотягивается, и мутационный прогон 21.08 показал это
# цифрой — 53 мутанта из 53 в daemon.py выжили, при том что в вынесенных
# решениях (should_shed_diarization, use_positional_split) убито 14 из 15.
# Здесь — те решения, чья поломка стоит дорого: разоружённый аудио-watchdog
# приложения, застрявшая машина разгрузки, замолчавший пульс, ложный «догнал».
# Каждая функция — чистая, с инвариантом в tests/test_stt_runtime.py.

#: Метка «канал вместо голоса» для STT-задачи при разгрузке: n=-1 означает
#: «подпиши каналом захвата», а 0 — валидный индекс голоса (чужая реплика
#: ушла бы голосу 0 — ревью 21.08, DeepSeek).
CHANNEL_LABEL_ONLY = -1


def stage_age(now: float, at: float) -> float:
    """Сколько STT сидит в текущем этапе. Никогда не отрицательное: часы
    монотонные, но отметка могла встать позже снимка."""
    return max(0.0, now - at)


def input_age_value(input_age: float | None) -> float | None:
    """Возраст входного звука для телеметрии: None остаётся None.

    Приложение двигает lastAudioInputAt только по числу (SuflerService);
    «всегда null» здесь означает выключенную навсегда ветку «аудиопоток
    замер → перезапуск» в PipelineWatchdog.
    """
    if input_age is None:
        return None
    return round(float(input_age), 2)


def progress_throttled(*, force: bool, now: float, last: float, every: float) -> bool:
    """Глушить ли пульс stt_progress: принудительный — никогда, остальные —
    чаще `every` секунд. Замолчавший пульс через 100 с перезапускает демон
    посреди встречи."""
    return not force and now - last < every


def lag_log_due(*, lagging: bool, now: float, last: float, every: float) -> bool:
    """Писать ли строку state=lagging в stderr: только при отставании и не
    чаще `every` секунд."""
    return lagging and now - last >= every


def lag_transition(prev: bool, next_: bool) -> bool:
    """Сменилось ли состояние разгрузки. Без этого машина гистерезиса не
    переключается, буфер упирается в потолок и режет живую ленту."""
    return prev != next_


def live_input_young_enough(input_age: float | None, chunk_s: float) -> bool:
    """«✅ STT догнал живой звук» — только при живом входе: мёртвый вход
    (None) или кадры старше чанка — это «жду кадров», а не «догнал»."""
    return input_age is not None and float(input_age) < chunk_s


def has_split_tracker(tracker: object) -> bool:
    """Есть ли у трекера позиционная раскладка. Инлайновая версия переживала
    мутацию IsNot→Is как «диаризация выключена навсегда»."""
    return tracker is not None and hasattr(tracker, "split")


def diarization_plan(*, lagging: bool, has_split: bool) -> str:
    """Какой веткой идёт чанк: 'plain' — без трекера, 'shed' — трекер есть,
    но очередь растёт (одна STT-задача с канальной меткой), 'diarize' —
    позиционная раскладка."""
    if not has_split:
        return "plain"
    return "diarize" if use_positional_split(lagging=lagging, has_split=has_split) else "shed"


def heartbeat_due(*, now: float, last: float, every: float = 30.0) -> bool:
    """Главный hb для watchdog UI — раз в `every` секунд, не каждый тик."""
    return now - last > every


def stall_log_due(*, stage_age_s: float, now: float, last: float,
                  threshold: float = 30.0, every: float = 30.0) -> bool:
    """Строка state=stalled: этап висит дольше порога, и не чаще `every`."""
    return stage_age_s >= threshold and now - last >= every


def is_recording_failure(status_text: str) -> bool:
    """Красить ли статус как отказ: смерть записи на диск — не серая строка.

    Подстрока, не префикс: сообщения _note_drop начинаются с «⚠️ подсказки
    отстают», и префикс красил 2 из 4 (круг 3, GLM + DeepSeek).
    """
    return "ЗАПИСЬ НА ДИСК" in status_text
