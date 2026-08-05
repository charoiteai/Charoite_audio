"""Имя встречи: один формат штампа на весь конвейер.

Штамп рождается у стенограммы (`main.Transcript`), тем же именем демон
называет записи каналов (`audio.AudioHub`, штамп ему передаёт daemon), по
нему же `rebuild_transcript` эти записи потом ищет. Пока знание о формате
жило в трёх местах порознь, оно разъезжалось — и оба раза молча:

* до 28.07 два независимых `datetime.now()` на границе минуты давали
  `..._1359.md` и `..._1400_mic.pcm`; лечили передачей штампа в AudioHub;
* с 28.07 штамп получил секунды (защита от затирания встречи при
  автоперезапуске демона), а срез `live.stem[:15]` в пересборке остался
  прежним — пересборка стала искать `..._1831_mic.wav`, когда на диске
  лежит `..._183145_mic.wav`. Регексп обрезок пропускал, поэтому ранний
  выход не срабатывал: конвейер честно ждал 45 секунд, писал «записей
  нет — оставляю живую стенограмму» и отдавал в граф черновик из чанков.
  Через `record_keep_days` ретеншн удалял целые каналы, и восстанавливать
  было уже нечего.

Правило: формат имени знает этот модуль, остальные его зовут.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re

FMT = "%Y-%m-%d_%H%M%S"
_FMT_LEGACY = "%Y-%m-%d_%H%M"

# Секунды опциональны только ради встреч, записанных до 28.07: их штамп был
# пятнадцатизначным. `-N` — суффикс коллизии из Transcript.__init__.
_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2})?)(?:-\d+)?$")

# Хвосты производных файлов конвейера. Список согласован с
# meeting_processing._AUX_SUFFIXES и rename_meeting.SUFFIXES: расхождение
# уже стоило темы «… разбор» в списке встреч (инцидент 04.08).
AUX_SUFFIXES = ("_minutes", "_hints", "_live", "_debrief",
                "_разбор", "_ревизия_claude", "_спикеры")
RECORDING_LABELS = ("mic", "blackhole")

# Главный файл встречи после наката темы: «2026-08-04_1203_Отчет_по_задачам».
# Ровно такие имена шлёт retry из приложения (transcript_path статуса).
_RE_TITLED = re.compile(
    r"((\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2})?)(?:-\d+)?)(?:_(.+))?$")


def now() -> str:
    """Штамп новой встречи. Секунды не косметика: автоперезапуск демона
    поднимается через 2 секунды, то есть почти всегда внутри той же минуты."""
    return dt.datetime.now().strftime(FMT)


def started_at(stamp: str) -> dt.datetime | None:
    """Момент начала встречи по штампу. None — имя не наше, дальше незачем."""
    m = _RE.match(stamp)
    if not m:
        return None
    core = m.group(1)
    return dt.datetime.strptime(core, FMT if len(core) == 17 else _FMT_LEGACY)


def stamp_of(name: str) -> str | None:
    """Штамп встречи из имени ГЛАВНОГО файла — живого или уже с темой.

    Retry из приложения приходит по transcript_path статуса, а там файл
    после наката темы: «2026-08-04_1203_Отчет_по_задачам». Производные
    (`_разбор`, `_minutes`, …) — не встречи: пересобирать по ним нельзя,
    иначе разбор перезапишет стенограмму.
    """
    m = _RE_TITLED.match(name)
    if not m:
        return None
    tail = m.group(3)
    if tail:
        low = f"_{tail.lower()}"
        if any(low.endswith(aux) for aux in AUX_SUFFIXES):
            return None
    return m.group(1)


def recording_path(rec_dir: pathlib.Path, stamp: str, label: str,
                   ext: str) -> pathlib.Path:
    """Файл канала встречи: демон пишет по этому имени, пересборка по нему ищет.

    Обе стороны обязаны звать именно эту функцию — ровно её расхождение
    стоило проекту финальной пересборки всех встреч за неделю.
    """
    return rec_dir / f"{stamp}_{label}.{ext}"


def resolve_stamp(rec_dir: pathlib.Path, stamp: str,
                  labels: tuple[str, ...] = RECORDING_LABELS) -> str:
    """Единый штамп, под которым лежат каналы одной встречи.

    Демон называет записи ПОСЕКУНДНЫМ штампом стенограммы, а retry из
    приложения знает только минутное имя после наката темы («…_1203»):
    точного файла с таким именем на диске нет — ищем по минутному префиксу.

    Критично смотреть на все каналы одним проходом. Если отдельно разрешить
    mic и blackhole, две встречи в одну минуту могут дать по одному каналу и
    пересборка склеит разговоры разных людей. Поэтому возвращаем найденный
    штамп, только когда объединение кандидатов всех каналов однозначно.
    Иначе оставляем исходное имя: лучше честно не пересобрать, чем смешать
    две встречи в одном документе.
    """
    extensions = ("wav", "pcm", "wav.part")
    for label in labels:
        for ext in extensions:
            if recording_path(rec_dir, stamp, label, ext).exists():
                # Хотя другой канал может принадлежать иному посекундному
                # кандидату, общий точный штамп безопасен: wait_recording
                # будет искать оба канала только под ним и чужой не возьмёт.
                return stamp
    core = _RE.match(stamp)
    if core is None or not rec_dir.is_dir():
        return stamp
    minute = core.group(1)[:15]
    found: set[str] = set()
    for label in labels:
        for ext in extensions:
            tail = f"_{label}.{ext}"
            for p in rec_dir.glob(f"{minute}*{tail}"):
                cand = p.name[: -len(tail)]
                if started_at(cand) is not None:
                    found.add(cand)
    return found.pop() if len(found) == 1 else stamp
