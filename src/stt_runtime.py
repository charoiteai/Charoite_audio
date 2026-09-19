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


def stage_is_stalled(*, stage_age_s: float, threshold: float = 30.0) -> bool:
    """Застрял ли текущий этап STT достаточно надолго, чтобы показать это.

    Один вердикт идёт и в heartbeat приложения, и в rate-limited stderr:
    разные сравнения на границе 30 секунд снова дали бы две правды.
    """
    return stage_age_s >= threshold


def stall_log_due(*, stage_age_s: float, now: float, last: float,
                  threshold: float = 30.0, every: float = 30.0) -> bool:
    """Строка state=stalled: этап висит дольше порога, и не чаще `every`."""
    return (stage_is_stalled(stage_age_s=stage_age_s, threshold=threshold)
            and now - last >= every)


# Тексты предупреждений про ВСЮ встречу (держатся на экране до конца записи,
# Swift `stickyStatus`). Маркер — одна константа: audio.py собирает из неё
# текст, иначе три рукописные копии строки расходились бы молча (GLM r1 по
# #538). С №310 маркеры — только текст для глаз: липкость и окраску статуса
# несёт сам `Status`, построенный писателем, а не подстрока у читателя.
MIC_ONLY_WARNING = "СОБЕСЕДНИКОВ В ЗАПИСИ НЕ БУДЕТ"
OWNER_MIC_LOST = "ВАШЕГО МИКРОФОНА В ЗАПИСИ НЕТ"
RECORDING_EMPTY = "ЗАПИСЬ ПУСТА"
MIC_BACK_NOTICE = "СОБЕСЕДНИКИ СНОВА В ЗАПИСИ"
OWNER_MIC_BACK = "ВАШ МИКРОФОН СНОВА В ЗАПИСИ"

# Темы липких слоёв на проводе (№310): приложение держит слой на тему и
# снимает ровно свою; `sticky: true/false` рядом с темой — совместимость с
# приложением, читающим только бит (входной круг DS и GLM по №310).
TOPIC_CHANNEL = "channel_loss"   # потеря/возврат канала записи (хаб)
TOPIC_DISK = "disk"              # отказ записи на диск (error, не липкое: его держит heartbeat)


class Status(str):
    """Строка статуса демона с намерением писателя: `sticky` — предупреждение
    про всю встречу (True ставит слой, False снимает, None — обычный статус),
    `error` — окраска отказа, `topic` — тема слоя на проводе. Подкласс `str`:
    все потребители текста (CLI `main.py`, уведомление, тесты со строками)
    работают как прежде; демон читает поля и не гадает по подстрокам
    (Critical 3 GLM и Critical 1 DS входного круга по №310)."""
    sticky: bool | None
    error: bool
    topic: str | None

    def __new__(cls, text: str, *, sticky: bool | None = None, error: bool = False,
                topic: str | None = None):
        obj = super().__new__(cls, text)
        obj.sticky = sticky
        obj.error = error
        obj.topic = topic
        return obj


def as_status(msg) -> "Status":
    """Любой текст статуса — в `Status`: голая строка = обычный статус."""
    return msg if isinstance(msg, Status) else Status(str(msg))


def status_event(msg) -> dict:
    """Единственное место, где статус становится JSON для приложения (№310).
    `error` — всегда (обычный статус снимает красный, контракт Swift `consume`),
    `sticky` и `topic` — только когда писатель их поставил: статус без ключа
    `sticky` липкий слой не трогает (№228); тема идёт и без липкости."""
    st = as_status(msg)
    ev = {"type": "status", "text": str(st), "error": bool(st.error)}
    if st.sticky is not None:
        ev["sticky"] = bool(st.sticky)
    if st.topic:
        # что писатель поставил, то и на проводе: тема отказа диска без
        # липкости молча выбрасывалась (Minor DS и GLM выходного круга)
        ev["topic"] = st.topic
    return ev


def realtime_factor(audio_s: float, transcription_ms: float) -> float | None:
    """Во сколько раз быстрее реального времени идёт распознавание.

    Без этой цифры «транскрипция 3200 мс» не говорит ничего: непонятно,
    медленная модель или большой кусок. Паспорт gigaam-v3 на этой машине —
    28× (17,6 с звука за 0,63 с, замер 16.07); отставание при RTF около
    единицы означает, что модель работает не в том режиме, и лечится это
    профилированием, а не придерживанием соседей по нагрузке (№105).

    None — когда считать не из чего: цикл без звука или без замера.
    """
    if audio_s <= 0 or transcription_ms <= 0:
        return None
    return round(audio_s / (transcription_ms / 1000.0), 2)


def seam_for_rows(prev_label: str | None,
                  rows: list[tuple[str, bool]]) -> list[tuple[bool, str | None]]:
    """Что передать стенограмме по каждому куску одного чанка канала.

    rows — (метка, кусок из головы чанка?). Зона шва с предыдущим чанком —
    только голова чанка; остальные (второй голос того же чанка) звук с
    соседом не делят и шва не получают. Голову называет раскладка (первый
    кусок по звуку), а не место в списке: пустой первый кусок не делает
    головой второй (luna, круг-2). `seam_with` — прежняя метка канала, если
    она сменилась (лаг → здоровый, канал → голос): DeepSeek/luna по #452 —
    раздача шва каждому куску резала второго человека как шов.
    """
    out: list[tuple[bool, str | None]] = []
    for label, head in rows:
        seam = prev_label if head and prev_label and prev_label != label else None
        out.append((head, seam))
    return out


def next_channel_label(prev_label: str | None, added_labels: list[str]) -> str | None:
    """Метка, под которой канал писал последний раз: хвост чанка — у последнего
    реально добавленного куска; ничего не добавлено — прежняя."""
    return added_labels[-1] if added_labels else prev_label



_JSON_SCALARS = (bool, int, float, str, type(None))


def snapshot_fields(health: dict) -> dict:
    """Поля снапшота здоровья, годные в NDJSON-событие: скаляры и словари
    скаляров. Снапшот уезжает в `stt_progress` целиком, чтобы новый датчик не
    терялся в рукописном белом списке (pump_alive — круг 1 DS I2 по №311), но
    `emit` ловит только BrokenPipe/ValueError, и несериализуемое значение
    (set, Path, numpy-скаляр) убило бы поток STT TypeError'ом (круг 2 DS I4).
    Граница сериализации — здесь, одной функцией, а не белым списком."""
    out: dict = {}
    for key, value in health.items():
        if isinstance(value, _JSON_SCALARS):
            out[key] = value
        elif isinstance(value, dict) and all(
                isinstance(v, _JSON_SCALARS) or (isinstance(v, dict) and all(isinstance(x, _JSON_SCALARS) for x in v.values()))
                for v in value.values()):
            out[key] = value
    return out


def progress_event(health: dict, *, lagging: bool, stage: str, stage_age: float,
                   last_cycle_ms: float, last_diarization_ms: float, last_transcription_ms: float,
                   last_audio_s: float, total_stt_calls: int, total_audio_s: float,
                   total_transcription_ms: float, shortest_piece_s: float) -> dict:
    """Событие `stt_progress`: снапшот здоровья целиком (после гейта) и поверх —
    именные поля с приведением типов (`input_age_seconds` через
    input_age_value, округления). Одна функция вместо литерала в демоне: тест
    проверяет результат, а не текст исходника (круг 2 DS M1 по №311)."""
    return {
        **snapshot_fields(health),
        "type": "stt_progress",
        "state": "lagging" if lagging else "healthy",
        "stage": stage,
        "stage_age_seconds": round(stage_age, 2),
        "backlog_seconds": round(float(health["backlog_seconds"]), 2),
        "input_age_seconds": input_age_value(health["input_age_seconds"]),
        "cycle_ms": round(last_cycle_ms),
        "diarization_ms": round(last_diarization_ms),
        "transcription_ms": round(last_transcription_ms),
        "audio_s": round(last_audio_s, 2),
        "calls_total": total_stt_calls,
        "audio_s_total": round(total_audio_s, 1),
        "shortest_s": round(shortest_piece_s, 2),
        "rtf_total": realtime_factor(total_audio_s, total_transcription_ms),
        "rtf": realtime_factor(last_audio_s, last_transcription_ms),
        "recording_ok": health["recording_ok"],
        "channels": health["channels"],
    }
