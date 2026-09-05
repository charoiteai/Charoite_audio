"""Сайдкар встречи `<посекундная стенограмма>.md.live.json` и хеши машинных записей.

Демон пишет сайдкар при стопе под посекундным именем стенограммы; накат
темы (graph_updater.retitle → migrate) и rename_meeting переносят пару
.md + сайдкар вместе, так что дальше он под прямым именем. Сайдкар под
посекундным именем при озаглавленной стенограмме — только наследие до
0.69.1. Здесь — единственное место, где решается, КАКОЙ
сайдкар принадлежит стенограмме, и пишутся хеши последней МАШИННОЙ
записи файлов (`transcript_sha256`, `minutes_sha256`, `minutes_source_sha256`):
совпадение с диском означает, что текста никто не касался. Модуль лёгкий
(без STT/диаризации), чтобы его звал и graph_updater (круг 1 по #489:
DS+GLM Critical — ретитл менял байты после снятия хеша).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import meeting_stamp
import safe_write

TAIL = ".md.live.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def valid_sha(value) -> str | None:
    """Хеш из сайдкара или None, если там мусор (обрезанная строка, число):
    мусор не должен превращаться в вечный «правлено руками» (GLM M2)."""
    return value if isinstance(value, str) and _HEX64.match(value) else None


def _direct(live: pathlib.Path) -> pathlib.Path:
    return live.with_name(live.name + ".live.json")


def owner_of(sidecar: pathlib.Path) -> pathlib.Path | None:
    """Чья это стенограмма — по штампу в имени сайдкара, не по счёту.

    Посекундное имя («…120040.md.live.json»): владелец — главный файл с
    этим посекундным штампом («…120040_Повтор.md»), если он есть; иначе —
    главный файл минуты («…1200_Отчет.md»): так конвейер до 0.69.1 оставлял
    сайдкар владельца минуты. Имя с темой — файл с этой темой, если он
    встреча, а не производная (сайдкар, названный по копии «…_Демо_live.md»
    при живом «…_Демо.md», — источника, DS r9 по #489); его нет
    (переименование до переноса пары) — главный файл с тем же ключом под
    любой темой, а для минутного ключа — владелец минуты. Никого — None
    (сирота). Две встречи в минуту (крэш-рестарт) различаются здесь
    штампом, а не «единственный — мой» (Critical DS r3 по #489).
    """
    base = sidecar.name[:-len(TAIL)]
    tdir = sidecar.parent
    titled = tdir / (base + ".md")
    stamp = meeting_stamp.stamp_of(base)
    if stamp is None or stamp != base:
        if titled.is_file() and not _derivative(titled):
            return titled
        parts = meeting_stamp.decompose(base)
        if not parts:
            return None
        # Главный файл с тем же ключом под другой темой — и посекундным, и
        # минутным (DS r7 по #489); для минутного ключа — ещё владелец
        # минуты с темой на служебное слово (шапка)
        found = _main_with_key(tdir, parts[0])
        if found is not None:
            return found
        if parts[0] == meeting_stamp.minute_of(parts[0]):
            return _minute_owner(tdir, parts[0])
        return None
    found = _main_with_key(tdir, stamp)
    if found is not None:
        return found
    return _minute_owner(tdir, meeting_stamp.minute_of(stamp))


def _main_with_key(tdir: pathlib.Path, key: str) -> pathlib.Path | None:
    """Главный файл с этим ключом (посекундным или минутным) среди файлов
    самого ключа — посекундных соседок и «-N» отсекает files_with_stamp."""
    return _pick_main(meeting_stamp.files_with_stamp(tdir, key, suffix=".md"), key)


def _minute_owner(tdir: pathlib.Path, minute: str) -> pathlib.Path | None:
    """Владелец минуты среди файлов минутного ключа: посекундные имена
    («…120005_Тема») отсекает сам ключ разбора имени."""
    candidates = [f for f in sorted(tdir.glob(f"{minute}*.md"))
                  if (meeting_stamp.decompose(f.stem) or ("",))[0] == minute]
    return _pick_main(candidates, minute)


def _pick_main(candidates: list[pathlib.Path], key: str) -> pathlib.Path | None:
    """Ярусы: имя-свидетельство (stamp_of == ключ; голый файл рядом с
    озаглавленным — остаток прерванного переноса, текущий главный —
    озаглавленный, DS r10 M1), затем шапка «# Встреча » у файла с темой на
    служебное слово («…120030_Разбор.md» до guard_slug, DS r4) — но не у
    производной. Копия «…_live.md» с той же шапкой сортируется раньше
    кириллической темы (DS r8), а её источник ретитл переименовывает
    (DS r10 I1) — её отсекает _derivative, а не порядок обхода."""
    named = [f for f in candidates if f.is_file() and meeting_stamp.stamp_of(f.stem) == key]
    if named:
        return next((f for f in named if f.stem != key), named[0])
    for f in candidates:
        if _is_main(f, key):
            return f
    return None


def _is_main(path: pathlib.Path, key: str) -> bool:
    """Главный файл встречи с этим ключом: по имени, а если тема кончается
    служебным словом (stamp_of даёт None) — по шапке «# Встреча », как
    отличает их и graph_updater, и только если это не производная."""
    if not path.is_file() or path.suffix != ".md":
        return False
    if meeting_stamp.stamp_of(path.stem) == key:
        return True
    return _head(path).startswith("# Встреча ") and not _derivative(path)


def _head(path: pathlib.Path) -> str:
    """Шапка встречи — общее чтение meeting_stamp.first_line (DS r13 по #489)."""
    return meeting_stamp.first_line(path)


def _derivative(path: pathlib.Path) -> bool:
    """Производная, не встреча. «X_minutes.md», «X_разбор.md», … при живом
    «X.md» — производные от него. «X_live.md» — копия живого черновика (её
    оставляет write_final): при живом «X.md» — всегда; без него (источник
    переименован ретитлом — штатный порядок write_final → retitle — или
    rename_meeting; DS r10 I1 по #489) копию выдаёт шапка: голая
    («# Встреча <штамп>») или с прежней темой, тогда как у настоящей
    встречи с темой на «live» («Демо live» до guard_slug, DS r5) тема шапки
    и хвост имени — одни слова. Хвост сравнивается срезом по длине
    суффикса: lower() не обязан сохранять длину (DS r9 M3)."""
    stem = path.stem
    for suffix in meeting_stamp.AUX_SUFFIXES:
        if stem[-len(suffix):].lower() != suffix.lower():
            continue
        if (path.parent / (stem[:-len(suffix)] + ".md")).is_file():
            return True
        return suffix == "_live" and not _named_after_header(stem, _head(path))
    return False


def _named_after_header(stem: str, head: str) -> bool:
    """Общий признак meeting_stamp.named_after_header (DS r12 по #489)."""
    return meeting_stamp.named_after_header(stem, head)


def _legacy(live: pathlib.Path) -> list[pathlib.Path]:
    """Сайдкары этой стенограммы под другим именем: посекундным (встречи,
    озаглавленные до 0.69.1) или с прежней темой (переименование до
    того, как rename_meeting стал переносить сайдкар). Свои — те, чей
    owner_of == live."""
    parts = meeting_stamp.decompose(live.stem)
    stamp = meeting_stamp.stamp_of(live.stem) or (parts[0] if parts else live.stem)
    minute = meeting_stamp.minute_of(stamp)
    direct = _direct(live)
    out = []
    for p in live.parent.glob(f"{minute}*{TAIL}"):
        if p == direct:
            continue
        base = p.name[:-len(TAIL)]
        base_parts = meeting_stamp.decompose(base)
        # Имя с хвостом на служебное слово («…_Демо_live») stamp_of не
        # разбирает — ключ берётся разбором имени (DS r9 I2 по #489)
        base_stamp = meeting_stamp.stamp_of(base) or (base_parts[0] if base_parts else None)
        if base_stamp is None or meeting_stamp.minute_of(base_stamp) != minute:
            continue
        if owner_of(p) == live:
            out.append(p)
    return out


def sidecar_for(live: pathlib.Path, bare: str | None = None) -> pathlib.Path | None:
    """Сайдкар этой стенограммы или None, если он неоднозначен.

    `bare` — посекундный штамп, когда он известен вызывающему (ретитл знает
    его точно). Иначе: файл под своим именем; нет — единственный сайдкар
    той же минуты (наследие до 0.69.1); несколько — None: две встречи в
    одну минуту не должны обмениваться хешами (GLM Important по #489).
    Отсутствие файла — не ошибка: возвращается путь, по которому его
    создадут.
    """
    if bare:
        return live.with_name(bare + TAIL)
    direct = _direct(live)
    if direct.exists():
        return direct
    found = _legacy(live)
    if len(found) > 1:
        return None
    return found[0] if found else direct


def claims(sidecar: pathlib.Path, bare: str) -> bool:
    """Сайдкар под этим именем — нашей встречи с посекундным штампом `bare`?
    Единственное свидетельство — ключ `stamp` (пишут демон, накат темы,
    rename_meeting). Так отличают свой сайдкар, оставшийся под целевым
    именем после прерванного или откаченного переноса, от сироты соседки
    (GLM r1 по #494, I2): своему пара воссоединяется, чужой — отказ."""
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("stamp") == bare


def move(old_main: pathlib.Path, new_main: pathlib.Path) -> pathlib.Path:
    """Сайдкар переезжает вместе с переименованной стенограммой: дальше он
    под своим именем, без угадывания (advisory GLM r2 по #489). Зовут все
    переименователи: ретитл (migrate) напрямую, rename_meeting — парой в
    своём плане переносов. Целевое имя занято — оставляем как есть."""
    old = old_main.with_name(old_main.name + ".live.json")
    new = new_main.with_name(new_main.name + ".live.json")
    if old.exists() and not new.exists() and old != new:
        try:
            old.rename(new)
        except OSError:
            return old
    return new if new.exists() else old


def migrate(live: pathlib.Path, bare: str) -> pathlib.Path:
    """Ретитл: сайдкар «<bare>.md.live.json» → под новое имя файла."""
    return move(live.with_name(bare + ".md"), live)


def read(live: pathlib.Path, bare: str | None = None) -> dict | None:
    """Содержимое сайдкара; None — нет файла, не JSON-объект или неоднозначен."""
    p = sidecar_for(live, bare)
    if p is None or not p.exists():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def exact_stamp(live: pathlib.Path) -> str | None:
    """Посекундный штамп встречи с МИНУТНЫМ именем (после наката темы) —
    из ключа `stamp` её СОБСТВЕННОГО сайдкара, а не угадыванием по каталогу
    записей.

    Ключ пишут демон при стопе, накат темы и rename_meeting при
    переименовании — только для этой стенограммы, и переезжает он вместе с
    ней под прямое имя. Читается ТОЛЬКО прямой сайдкар: усыновление
    сайдкара-наследия через sidecar_for/owner_of построено для хешей, где
    цена ошибки — «распознаём заново», а здесь сирота удалённой соседки той
    же минуты (её .md стёрт руками, сайдкар остался) выдавала бы чужой штамп
    как точный — и по ключу, и по имени (GLM Critical r1 по #492). Годится
    только штамп той же минуты и с секундами; посекундной стенограмме
    уточнять нечего — None. Без ключа пересборка разрешает минуту глобом с
    проверкой владения (meeting_stamp.resolve_stamp).
    """
    key = meeting_stamp.stamp_of(live.stem)
    if key is None or meeting_stamp.minute_of(key) != key:
        return None
    direct = _direct(live)
    if not direct.exists():
        return None
    try:
        meta = json.loads(direct.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = meta.get("stamp") if isinstance(meta, dict) else None
    return value if _seconds_stamp_of_minute(value, key) else None


def _seconds_stamp_of_minute(value, key: str) -> bool:
    """Значение ключа `stamp` годится: строка, посекундный штамп (не минута,
    даже с суффиксом коллизии — DS M3 по main 05.09), той же минуты, и
    реальное время: регекс пропускает секунды 99, а started_at на них
    бросает ValueError и ронял бы пересборку (DS на Fireworks, M1)."""
    if not (isinstance(value, str) and meeting_stamp.stamp_of(value) == value
            and value != key and meeting_stamp.minute_of(value) == key
            and value[15:17].isdigit()):     # секунды на местах 15–16; «…1203-1» их не имеет
        return False
    try:
        return meeting_stamp.started_at(value) is not None
    except ValueError:
        return False


def remember(live: pathlib.Path, key: str, value: str, bare: str | None = None) -> bool:
    """Записать ключ в сайдкар; нет файла — создать (импортированные встречи и
    сироты без live.json иначе оставались без защиты — DS M4 / GLM M1).
    Неоднозначный сайдкар — не писать, вернуть False."""
    p = sidecar_for(live, bare)
    if p is None:
        return False
    if bare is None and p != _direct(live) and p.exists():
        # Свой сайдкар под старым именем — усыновить: дальше он под своим
        # именем. Писать в чужой нельзя (GLM Minor r2 по #489) — в кандидаты
        # попадают только те, чей owner_of == live
        if _direct(live).exists():
            return False   # кто-то создал свой за это время (GLM M3 r3)
        try:
            p.rename(_direct(live))
            p = _direct(live)
        except OSError:
            return False
    meta: dict = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError):
            return False
    meta[key] = value
    try:
        safe_write.write_text(p, json.dumps(meta, ensure_ascii=False))
    except OSError:
        return False
    return True
