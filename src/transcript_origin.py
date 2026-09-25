"""Откуда стенограмма (№262): из какого файла импорта она сделана.

Единственный владелец ключа `transcript_origin` в сайдкаре стенограммы
`.md.live.json` — писатель и толерантный читатель в одном модуле, как у
`channel_trace` (пара расходилась бы молча, живи они порознь). Значение —
JSON-строкой (контракт `live_sidecar.remember` — строковые значения): одна
запись `{name, size, kind}`.

До ключа этот факт жил только в хвосте H1 «— импорт X (N Б)» и в сайдкаре
аудио в `done/`, который пишет родитель-сканер уже после выхода ребёнка.
Шапка — текст для человека: её правят руками, а у стенограммы из аудио она
«— запись …», не «— импорт …». Ключ — машинный факт рядом со стенограммой,
поэтому дедуп повторов
(`import_meeting.find_repeat`) читает сначала ключ, а шапку — только когда
ключа нет (встречи, импортированные до этой версии).

Штампа в записи нет: он уже лежит ключом `stamp`, его обновляют накат темы и
переименование, и вторая копия разошлась бы с ним. Какие расширения — какой
вид, решает импорт (там же выбирается ветка); здесь только имена видов.
"""
from __future__ import annotations

import json
import pathlib

import live_sidecar

SIDECAR_KEY = "transcript_origin"
AUDIO = "audio"
SUBS = "subs"
TEXT = "text"
KINDS = (AUDIO, SUBS, TEXT)


def remember(tpath: pathlib.Path, name: str, size: int, kind: str) -> bool:
    """Записать, из какого файла сделана стенограмма `tpath`. Слиянием, не
    дампом: в том же сайдкаре живут `channel_events`, `stamp` и паспорта
    производных. False — сайдкар неоднозначен (две встречи одной минуты без
    своих сайдкаров), ключа нет; дедуп тогда узнаёт повтор по шапке."""
    if kind not in KINDS:
        raise ValueError(f"вид исходника {kind!r} не из {KINDS}")
    value = json.dumps({"name": name, "size": size, "kind": kind}, ensure_ascii=False)
    return live_sidecar.remember(tpath, SIDECAR_KEY, value)


def of(tpath: pathlib.Path, sidecars: set[str]) -> tuple[str, int, str] | None:
    """(имя, размер, вид) из СОБСТВЕННОГО сайдкара стенограммы; None — ключа нет.

    Мусор и чужой тип — тоже «ключа нет»: дедуп уходит в разбор шапки, а не
    склеивает встречи по полуразобранной записи. Вид проверяется только на
    форму (непустая строка): дедупу он не нужен, а вид, который добавит
    будущий писатель, не должен стирать у старого читателя весь ключ (DS M1
    круга 1 по PR №629).

    `sidecars` — имена сайдкаров каталога, снятые вызывающим один раз на
    обход; обязательный, без умолчания. Кандидат без СВОЕГО сайдкара
    `<имя>.md.live.json` не читается вовсе, а читается только свой файл
    (`live_sidecar.read_direct`): поиск наследия нашёл бы сироту соседки той
    же минуты, и её исходник сделал бы новую встречу «повтором» — она не
    импортировалась бы (DS I1 круга 1 по PR №629)."""
    if tpath.name + ".live.json" not in sidecars:
        return None
    meta = live_sidecar.read_direct(tpath) or {}
    raw = meta.get(SIDECAR_KEY)
    if not isinstance(raw, str):
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    name, size, kind = rec.get("name"), rec.get("size"), rec.get("kind")
    # bool — подкласс int: `true` в размере — мусор, а не «1 байт»
    if (not isinstance(name, str) or not name or not isinstance(size, int)
            or isinstance(size, bool) or size < 0 or not isinstance(kind, str) or not kind):
        return None
    return name, size, kind


def sidecars_in(tdir: pathlib.Path, prefix: str = "") -> set[str]:
    """Имена сайдкаров стенограмм каталога (с общим началом `prefix`) — один
    `glob` на обход вместо проверки существования на каждого кандидата."""
    return {p.name for p in tdir.glob(f"{prefix}*{live_sidecar.TAIL}")}
