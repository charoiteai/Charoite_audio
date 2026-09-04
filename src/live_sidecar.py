"""Сайдкар встречи `<посекундная стенограмма>.md.live.json` и хеши машинных записей.

Демон пишет сайдкар при стопе; накат темы (graph_updater.retitle) и
rename_meeting переименовывают только *.md, сайдкар остаётся под
посекундным именем. Здесь — единственное место, где решается, КАКОЙ
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
    сайдкар владельца минуты. Имя с темой — файл с этой темой; его нет
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
        # Имя с темой: файл с этой темой; его нет — переименование до
        # переноса пары, сайдкар владельца минуты (одна семантика с
        # _legacy — DS r6 по #489)
        if titled.exists():
            return titled
        parts = meeting_stamp.decompose(base)
        if not parts:
            return None
        # Главный файл с тем же ключом под другой темой — и посекундным, и
        # минутным (DS r7 по #489); для минутного ключа — ещё владелец
        # минуты с темой на служебное слово (шапка)
        for f in meeting_stamp.files_with_stamp(tdir, parts[0], suffix=".md"):
            if _is_main(f, parts[0]):
                return f
        if parts[0] == meeting_stamp.minute_of(parts[0]):
            return _minute_owner(tdir, parts[0])
        return None
    for f in meeting_stamp.files_with_stamp(tdir, stamp, suffix=".md"):
        if _is_main(f, stamp):
            return f
    return _minute_owner(tdir, meeting_stamp.minute_of(stamp))


def _minute_owner(tdir: pathlib.Path, minute: str) -> pathlib.Path | None:
    for f in sorted(tdir.glob(f"{minute}*.md")):
        if _main_of_minute(f, minute):
            return f
    return None


def _main_of_minute(path: pathlib.Path, minute: str) -> bool:
    """Главный файл владельца минуты: разбор имени даёт ключ минуты и это
    не производная — по stamp_of, а при теме на служебное слово
    («…_1200_Демо_live.md», до guard_slug) — по шапке (DS r5 по #489).
    Посекундную соседку с темой отсекает сам ключ минуты."""
    if not path.is_file():
        return False
    parts = meeting_stamp.decompose(path.stem)
    if not parts or parts[0] != minute:
        return False
    return meeting_stamp.stamp_of(path.stem) == minute or _is_main(path, minute)


def _is_main(path: pathlib.Path, stamp: str) -> bool:
    """Главный файл встречи с этим штампом — по имени, а если тема кончается
    служебным словом («…120030_Разбор.md» — stamp_of даёт None, DS r4 по
    #489) — по шапке «# Встреча », как отличает их и graph_updater. Копия
    живого черновика «<стем>_live.md» (её оставляет write_final) начинается
    с той же шапки — не главный, если рядом лежит файл без суффикса
    (GLM r6 по #489, как legacy_mains в rename_meeting)."""
    if not path.is_file() or path.suffix != ".md":
        return False
    if meeting_stamp.stamp_of(path.stem) == stamp:
        return True
    if _copy_of(path):
        return False
    try:
        with path.open("rb") as fh:
            return fh.read(200).decode("utf-8", errors="ignore").lstrip().startswith("# Встреча ")
    except OSError:
        return False


def _copy_of(path: pathlib.Path) -> bool:
    """«<стем>_live.md» при живом «<стем>.md» — копия, не встреча."""
    stem = path.stem
    for suffix in getattr(meeting_stamp, "AUX_SUFFIXES", ("_live", "_minutes", "_hints")):
        if stem.lower().endswith(suffix.lower()) and (path.parent / (stem[:-len(suffix)] + ".md")).exists():
            return True
    return False


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
        base_stamp = meeting_stamp.stamp_of(p.name[:-len(TAIL)])
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
