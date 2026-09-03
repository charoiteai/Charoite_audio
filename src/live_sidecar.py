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


def sidecar_for(live: pathlib.Path, bare: str | None = None) -> pathlib.Path | None:
    """Сайдкар этой стенограммы или None, если он неоднозначен.

    `bare` — посекундный штамп, когда он известен вызывающему (ретитл знает
    его точно). Иначе: файл под своим именем; нет — единственный сайдкар
    той же минуты; несколько — None: две встречи в одну минуту не должны
    обмениваться хешами (GLM Important по #489). Отсутствие файла — не
    ошибка: возвращается путь, по которому его создадут.
    """
    if bare:
        return live.with_name(bare + TAIL)
    direct = live.with_name(live.name + ".live.json")
    if direct.exists():
        return direct
    stamp = meeting_stamp.stamp_of(live.stem) or live.stem
    minute = meeting_stamp.minute_of(stamp)
    found = [p for p in live.parent.glob(f"{minute}*{TAIL}")
             if meeting_stamp.minute_of(p.name[:-len(TAIL)]) == minute]
    if len(found) > 1:
        return None
    return found[0] if found else direct


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
