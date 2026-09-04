"""Имя встречи: один формат штампа на весь конвейер.

Штамп рождается у стенограммы (`transcript.Transcript`), тем же именем демон
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


def belongs(name: str, stamp: str) -> bool:
    """Имя файла — этой встречи? Штамп в начале и граница за ним: не цифра
    (иначе «…1130» ловил бы «…113012») и не «-N» (суффикс коллизии соседки).
    То же правило, что в `files_with_stamp`; forget и rename звали его копиями."""
    if not name.startswith(stamp):
        return False
    rest = name[len(stamp):]
    return not rest[:1].isdigit() and not re.match(r"-\d", rest)


def decompose(name: str) -> tuple[str, str] | None:
    """Имя файла встречи → (штамп, хвост после него без ведущего «_»).

    «2026-08-04_1203_Отчет_minutes» → («2026-08-04_1203», «Отчет_minutes»),
    голый штамп — («…», «»); не наше имя — None. Единственный публичный разбор
    имени: чужие модули не должны держать свой регексп (DS r5 по #455).
    """
    m = _RE_TITLED.match(name)
    if not m:
        return None
    return m.group(1), m.group(3) or ""


def guard_slug(slug: str) -> str:
    """Тема в имени файла не должна кончаться служебным хвостом.

    «Демо live» давало `<штамп>_Демо_live` — для `stamp_of` это копия
    `_live`: встреча пропадала из списков, архива, rename и замка
    пересборки, хотя поиск главного файла по содержимому её находил (luna
    r3 по #455). Последнее слово такой темы крепится дефисом («Демо-live»),
    тема из одного служебного слова получает «-встреча». Имя с темой
    рождается в трёх местах — `graph_updater.theme_slug` (накат темы и
    производные), `rename_meeting.pretty_and_slug`, `import_meeting.title_slug`
    — и все три идут сюда (DS r4: импорт был мимо).
    """
    low = f"_{slug.lower()}"
    if not any(low.endswith(aux) for aux in AUX_SUFFIXES):
        return slug
    head, sep, last = slug.rpartition("_")
    return f"{head}-{last}" if sep else f"{slug}-встреча"


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


def named_after_header(stem: str, head: str) -> bool:
    """Тема шапки «# Встреча <штамп> — <тема>» и хвост имени — одни слова
    («Демо live» ↔ «Демо_live», «Демо-live» после guard_slug, «live» ↔
    «<штамп>_live»): файл со служебным хвостом в имени — встреча, а не
    производная. Один признак на live_sidecar и meeting_processing
    (DS r12 по #489: два резолвера расходились)."""
    parts = decompose(stem)
    theme = head.split(" — ", 1)[1] if " — " in head else ""
    if not parts or not parts[1] or not theme:
        return False
    return _words(theme) == _words(parts[1])


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def minute_of(stamp: str) -> str:
    """«2026-08-03_113012-1» → «2026-08-03_1130»; не штамп — как есть."""
    m = _RE.match(stamp)
    return m.group(1)[:15] if m else stamp


def graph_key(tdir: pathlib.Path, stem: str,
              graph: pathlib.Path | None = None) -> str:
    """Ключ встречи в графе: имя заметки `Встречи/<ключ>.md`, ссылка
    `[[Встречи/<ключ>]]`, папка архива, отметка brain.

    По умолчанию — минутный штамп: ссылки в графе читают люди, и «12:58»
    там уместнее «12:58:12». Посекундный штамп ключом становится только
    когда минута уже принадлежит ДРУГОЙ встрече: демон после краха
    поднимается через две секунды, то есть внутри той же минуты, и вторая
    встреча минутным ключом затирала заметку первой, а архив переименовывал
    её папку под себя (аудит 16–17.08, карточка №39). Владение минутой
    читается из transcripts/: главный файл «<минута>_<тема>.md» — владелец;
    среди голых посекундных владелец — самый ранний. Правило детерминировано
    состоянием каталога, поэтому повтор обработки даёт тот же ключ. Если
    передан граф, уже существующая заметка весит больше каталога: встреча,
    однажды записанная под посекундным ключом, остаётся под ним и после
    того, как соседку забыли, — иначе повтор разбора завёл бы ей вторую
    заметку под минутой.
    """
    bare = stamp_of(stem)
    if bare is None:
        return stem
    minute = minute_of(bare)
    if bare == minute:                 # встреча до 28.07: секунд не было
        return minute
    if stem != bare:                   # тема уже накатана: «<bare>_Тема» — ключ в имени
        return bare
    if graph is not None:
        notes = graph / "Встречи"
        if (notes / f"{bare}.md").is_file():
            return bare
        minute_note = notes / f"{minute}.md"
        if minute_note.is_file():
            try:
                if not note_is_ours(minute_note.read_text(encoding="utf-8"), bare, tdir):
                    return bare
            except OSError:
                pass
    for p in tdir.glob(f"{minute}*.md") if tdir.is_dir() else ():
        other = p.stem
        if other == stem:
            continue
        o_bare = stamp_of(other)
        if o_bare is None or minute_of(o_bare) != minute:
            continue
        if other != o_bare:            # чужой главный файл с темой
            if not other.startswith(o_bare + "_") or o_bare == minute:
                return bare            # он назван минутой — минута занята
            continue                   # назван секундами — минуту не держит
        if o_bare < bare:              # голая соседка раньше нас
            return bare
    return minute


_TRANSCRIPT_LINE = re.compile(r"^Стенограмма: `([^`]+)`", re.M)


def note_is_ours(note_text: str, stamp: str,
                 tdir: pathlib.Path | None = None) -> bool:
    """Заметка `Встречи/<минута>.md` принадлежит встрече со штампом `stamp`?

    Заметка кончается строкой «Стенограмма: `<путь>`»; по штампу в имени
    этого файла и решаем. Другая минута — чужая. Файл в строке посекундный —
    только точное совпадение: минутную заметку могла написать соседка той
    же минуты (крэш-рестарт), и «забыть»/переименовать по чужой заметке
    нельзя. Файл в строке назван минутой (владелец уже с темой): наш
    посекундный штамп чужой, если его собственный файл ещё лежит в
    transcripts/ — владелец минуты тот, чей файл так и назван, а наш —
    соседка; нет своего файла — мы и есть переименованный владелец.
    Строки нет (наследие): чужая только когда рядом и наш посекундный
    файл, и минутно названный владелец. Без transcripts/ спорить нечем —
    считаем своей (круг-1 по PR #388, DeepSeek и Codex).
    """
    m = _TRANSCRIPT_LINE.search(note_text)
    its = stamp_of(pathlib.Path(m.group(1)).stem) if m else None
    if its is not None and minute_of(its) != minute_of(stamp):
        return False
    if its is not None and its != minute_of(its):          # посекундный владелец
        return stamp == minute_of(stamp) or its == stamp
    if stamp == minute_of(stamp) or tdir is None or not tdir.is_dir():
        return True
    minute = minute_of(stamp)
    mains = [stamp_of(p.stem) for p in tdir.glob(f"{minute}*.md")]
    own = stamp in mains
    if its is not None:                                     # владелец назван минутой
        return not own
    return not (own and minute in mains)


def find_note(graph: pathlib.Path, stamp: str,
              tdir: pathlib.Path | None = None) -> pathlib.Path | None:
    """Заметка встречи по штампу (с секундами, если они известны).

    Кандидаты: посекундный ключ — он однозначен, затем минутный — с
    проверкой владения (`note_is_ours`). Это путь forget/rename, где есть
    только штамп; конвейер с живым файлом зовёт `graph_key`.
    """
    minute = minute_of(stamp)
    keys = [stamp, minute] if stamp != minute else [minute]
    for key in keys:
        note = graph / "Встречи" / f"{key}.md"
        if not note.is_file():
            continue
        if key == minute and stamp != minute:
            try:
                if not note_is_ours(note.read_text(encoding="utf-8"), stamp, tdir):
                    continue
            except OSError:
                continue
        return note
    return None


def archive_time(key: str) -> str:
    """Время в имени папки архива: «12-58» у минутного ключа, «12-58-12» у
    посекундного, «12-58-12-1» у ключа с суффиксом коллизии — две встречи
    одной минуты (и одной секунды) лежат в Finder рядом, но порознь."""
    m = _RE.match(key)
    if not m:
        return f"{key[11:13]}-{key[13:15]}"
    core = m.group(1)
    hhmm = f"{core[11:13]}-{core[13:15]}"
    if len(core) == 17:
        hhmm = f"{hhmm}-{core[15:17]}"
    return hhmm + key[len(core):]          # «-1» суффикса коллизии, если был


def files_with_stamp(directory: pathlib.Path, stamp: str, *, prefix: str = "",
                     suffix: str = "") -> list[pathlib.Path]:
    """Файлы «<prefix><штамп>…<suffix>» этой встречи — и только её.

    Штамп с секундами (`2026-07-15_140030`, 17 знаков) начинается с штампа
    без секунд (`2026-07-15_1400`): голый глоб `{stamp}*` при забывании
    первой встречи уносил и файлы второй, а при архивации и сборке облачного
    контекста подмешивал их к чужой встрече (крэш-рестарт в ту же минуту —
    штатный сценарий, аудит 16.08). Граница — после штампа не цифра.

    Единственное место, где живёт это правило: forget, archive и облачный
    контекст обязаны звать его, а не писать глоб заново.
    """
    if not directory.is_dir():
        return []
    out = []
    for f in directory.glob(f"{prefix}{stamp}*{suffix}"):
        rest = f.name[len(prefix) + len(stamp):]
        # Цифра — посекундная соседка, «-1» — суффикс коллизии другой встречи
        # (Transcript.__init__, импорт в занятую секунду): оба не наши.
        if rest[:1].isdigit() or re.match(r"-\d", rest) or not f.is_file():
            continue
        out.append(f)
    return sorted(out)


def recording_path(rec_dir: pathlib.Path, stamp: str, label: str,
                   ext: str) -> pathlib.Path:
    """Файл канала встречи: демон пишет по этому имени, пересборка по нему ищет.

    Обе стороны обязаны звать именно эту функцию — ровно её расхождение
    стоило проекту финальной пересборки всех встреч за неделю.
    """
    return rec_dir / f"{stamp}_{label}.{ext}"


#: Файл канала встречи: `<штамп>_<метка>.pcm|.wav`, плюс временные имена
#: конвертации — `.wav.part` у демона и `.wav.part<pid>` у пересборки.
_RE_RECORDING = re.compile(r"^(?P<stamp>.+)_(?P<label>[^_]+)\.(?:pcm|wav)(?:\.part\d*)?$")


def stamp_of_recording(name: str) -> str | None:
    """Штамп встречи по имени файла канала — обратная к `recording_path`.

    Нужна ретеншну дважды. Во-первых, чтобы не удалить запись встречи,
    которая прямо сейчас пересобирается: чистка обязана понимать, к какой
    встрече файл относится. Во-вторых, чтобы вообще понять, что перед ней
    запись, — и не оставить на диске навсегда временный файл конвертации,
    пережив который, полный несжатый WAV часовой встречи молча нарушил бы
    обещание PRIVACY об удалении через record_keep_days.

    Разбирать имя на месте вызывающий не вправе: формат живёт здесь, и ровно
    его расхождение уже дважды стоило проекту встреч.
    """
    m = _RE_RECORDING.match(name)
    if not m:
        return None
    head = m.group("stamp")
    return head if _RE.match(head) else None


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
    head = core.group(1)
    if len(head) != 15:
        # Штамп посекундный — секунда встречи известна ТОЧНО, приблизительное
        # имя искать незачем. Раньше сюда проваливался и он: своей записи нет
        # (ретеншн удалил или крэш не дал дописать), в минуте остаётся одна
        # чужая — и `len(found) == 1` объявлял её однозначной. Демон после
        # краха поднимается за две секунды, то есть внутри той же минуты:
        # разговор соседней встречи молча уезжал в чужую стенограмму, оттуда
        # в граф и в память, без единого вопроса человеку (ревью 20.08, GLM).
        return stamp
    minute = head[:15]
    found: set[str] = set()
    for label in labels:
        for ext in extensions:
            tail = f"_{label}.{ext}"
            for p in rec_dir.glob(f"{minute}*{tail}"):
                cand = p.name[: -len(tail)]
                if started_at(cand) is not None:
                    found.add(cand)
    return found.pop() if len(found) == 1 else stamp
