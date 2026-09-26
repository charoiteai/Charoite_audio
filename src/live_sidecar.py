"""Сайдкар встречи `<посекундная стенограмма>.md.live.json` и хеши машинных записей.

Демон пишет сайдкар при стопе под посекундным именем стенограммы; накат
темы (graph_updater.retitle → migrate) и rename_meeting переносят пару
.md + сайдкар вместе, так что дальше он под прямым именем. Сайдкар под
посекундным именем при озаглавленной стенограмме — только наследие до
0.69.1. Здесь — единственное место, где решается, КАКОЙ
сайдкар принадлежит стенограмме, и пишутся хеши последней МАШИННОЙ
записи файлов (`transcript_sha256`, `minutes_sha256`, `minutes_source_sha256`):
совпадение с диском означает, что текста никто не касался. Контракт
`<вид>_source_sha256` — хеш ИСТОЧНИКА производной: того, что владелец вида
подал модели. У видов от речи (минутки, разбор, тезисы) это речь плюс
оговорка о неполной записи (`meeting_source.MeetingSource.sha()`, №317), не
голая речь; у саммари (№314) — канон материалов (обрезки минуток, тезисов,
разбора, хвоста стенограммы, список решений, оговорка), потому что речь после
встречи не меняется, а минутки меняет ревизия. Инвариант один на всех: писатель
паспорта и читатель свежести берут источник ОДНОЙ функцией (урок №317).
Вторая половина контракта — запись: `write_derivative` — единственный шов, через
который машина пишет производную и переставляет паспорт (снимок → `.prev/` →
гейт expect → attest), `retouch` — механическая перезапись байтов готового
файла (переименование встречи) с перестановкой хеша байтов при прежнем
источнике: до №314 rename_meeting правил `Саммари.md` голым `replace`, и с
паспортом файл замер бы в HUMAN навсегда (Critical DS и GLM входного круга).
Модуль лёгкий (без STT/диаризации), чтобы его звал и graph_updater (круг 1 по
#489: DS+GLM Critical — ретитл менял байты после снятия хеша), и
meeting_archive на голом python3.
"""
from __future__ import annotations

import hashlib
import json
import threading
import pathlib
import re

import datetime
import sys
import typing

import meeting_stamp
import safe_write

TAIL = ".md.live.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Состояние производной встречи по её паспорту в сайдкаре (№309). Паспорт —
# пара ключей `<вид>_sha256` (байты последней МАШИННОЙ записи) и
# `<вид>_source_sha256` (хеш источника: речь + оговорка о записи,
# `meeting_source.MeetingSource.sha()`). У минуток
# он был и раньше (`minutes_*`), у разбора и тезисов появляется здесь же.
MISSING = "missing"      # файла нет — собрать
FRESH = "fresh"          # машинная, источник тот же — модель не звать
STALE = "stale"          # машинная, речь изменилась — пересобрать (прежняя в .prev/)
HUMAN = "human"          # байты НЕ наши: правка руками ИЛИ машинная перештамповка без
#                          обновления хеша (55 из 69 сайдкаров минуток на 18.09 — вторая
#                          причина). Это консервативная граница «не трогать», а не
#                          свидетельство о человеке (Important DS выходного круга по №309)
UNKNOWN = "unknown"      # паспорта нет (старые артефакты, чужой писатель) или файл не
#                          прочитался — знания нет; не трогать, но и не считать
#                          человеческой: HUMAN — это знание, UNKNOWN — его отсутствие;
#                          схлопнуть их значило бы навсегда запереть старый корпус
#                          (232 из 302 встреч без сайдкара — входной круг по №309)

# Политика вызывающего: при каких состояниях производную СТРОИТЬ. Двум
# писателям она нужна разная, и раньше разница жила в двух `if` по разным
# модулям (критика GLM выходного круга по №309): живой путь после встречи
# освежает разбор всегда, кроме правленного руками — его источник речь + граф,
# и старый корпус без паспорта он должен обслуживать как прежде; ретро-обход
# трогает только то, что заведомо наше и устарело — массовый бэкфилл UNKNOWN
# запрещён решением входного круга (час модели, риск затереть правки).
POLICY_LIVE = frozenset({MISSING, STALE, FRESH, UNKNOWN})
POLICY_RETRO = frozenset({MISSING, STALE})


def wants_build(state: str, policy: frozenset[str]) -> bool:
    """Строить ли производную в состоянии `state` по политике вызывающего."""
    return state in policy


def derivative_state(path: pathlib.Path, meta: dict | None, kind: str, source_sha: str) -> str:
    """Что делать с производной `path` вида `kind` при текущей речи источника
    `source_sha` — единственное место, где производная решает о своей свежести.
    Раньше шесть писателей выводили её каждый своим способом: «если файла
    нет», «пишу всегда», по mtime, «если непустой» — и лишь минутки по хешу.

    Ошибка ввода-вывода знания не даёт: EACCES от редактора или бэкапа, EAGAIN,
    недописанный файл — UNKNOWN, как и у `exists()` выше, а не HUMAN, который
    навсегда останавливал бы живой путь «разбор правлен руками» (Important GLM
    выходного круга). Байты есть, но это не наш текст (не UTF-8) — HUMAN."""
    try:
        if not path.exists():
            return MISSING
        # пустой файл — след оборванной записи, не документ: собрать, а не
        # аттестовать пустоту (Critical DS выходного круга по №314: `_gen_summary`
        # до паспорта проверял `st_size > 0`, с паспортом проверка пропала)
        if path.stat().st_size == 0:
            return MISSING
    except OSError:
        return UNKNOWN
    meta = meta if isinstance(meta, dict) else {}
    recorded = valid_sha(meta.get(f"{kind}_sha256"))
    if recorded is None:
        return UNKNOWN
    try:
        current = sha(path.read_text(encoding="utf-8"))
    except OSError:
        return UNKNOWN                        # не прочиталось — ничего не доказано
    except UnicodeDecodeError:
        return HUMAN                          # байты есть, но не наш текст — не наш
    if current != recorded:
        return HUMAN
    return FRESH if valid_sha(meta.get(f"{kind}_source_sha256")) == source_sha else STALE


def attest(live: pathlib.Path, kind: str, file_text: str, source_sha: str,
           bare: str | None = None) -> bool:
    """Выдать производной паспорт после МАШИННОЙ записи: байты и речь источника.
    Обёртка над `remember` — сайдкара нет — создаст; неоднозначный — False."""
    return (remember(live, f"{kind}_sha256", sha(file_text), bare)
            and remember(live, f"{kind}_source_sha256", source_sha, bare))


def prev_path(live: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """Куда ложится прежняя версия производной: `.prev/` рядом со СТЕНОГРАММОЙ у
    всех видов — и у тезисов и саммари, чьи файлы живут в папке архива внутри
    графа: скрытый каталог в графе синкался бы iCloud и попадал под `_unhide`
    архива (Important DS выходного круга по №309). Файл из чужой папки получает
    префикс стема стенограммы — иначе «Тезисы.md» всех встреч легли бы в одно имя."""
    # имя чужой папки — от голого штампа, не от стема: ретитл меняет стем, и
    # каждое поколение получало бы своё имя навсегда (Minor GLM круга 2)
    bare = meeting_stamp.stamp_of(live.stem) or live.stem
    name = path.name if path.parent == live.parent else f"{bare}__{path.name}"
    return live.parent / ".prev" / name


class WriteOutcome(typing.NamedTuple):
    """Исход записи производной — значением, не `None` на три истории (Important
    DS круга 4 по №314): `state` — состояние после записи тем же оракулом
    (FRESH при удавшемся паспорте, UNKNOWN без владельца), None — запись не
    состоялась; `refused` — почему: RACE («файл менялся под рукой» — повтор
    бессмыслен) или PREV («прежняя версия не сохранена» — сбой диска, повтор
    имеет смысл)."""
    state: str | None
    refused: str | None = None

    RACE: typing.ClassVar[str] = "race"
    PREV: typing.ClassVar[str] = "prev"

    @property
    def written(self) -> bool:
        return self.state is not None


def write_derivative(live: pathlib.Path, path: pathlib.Path, kind: str, body: str,
                     source_sha: str, *, log=lambda msg: print(msg, file=sys.stderr)) -> WriteOutcome:
    """Записать производную и выдать ей паспорт — единственный машинный
    писатель производных с паспортом. Прежняя версия — в `.prev/` рядом со
    стенограммой (`prev_path`): уверенная, но неверная генерация не должна быть
    невозвратной. Запись под гейтом «файл не менялся под рукой»: минута
    генерации — окно для редактора; обрыв не оставит «готовый» битый файл.
    Паспорт — только живой стенограмме: сайдкар без владельца — сирота
    (Important DS входного круга по №314).

    Возвращает `WriteOutcome`: состояние производной ПОСЛЕ записи тем же
    оракулом `derivative_state` или причину отказа значением. Вызывающий не
    пересобирает знание сам и не гадает, какая ветка отказала (Important DS и
    GLM круга 1; Important DS круга 4)."""
    before = safe_write.stat_snapshot(path)
    if before is not None:
        try:
            prev = prev_path(live, path)
            prev.parent.mkdir(exist_ok=True)
            safe_write.write_text(prev, path.read_text(encoding="utf-8"))
        except OSError as e:
            log(f"прежняя версия {path.name} не сохранена ({e}) — не перезаписываю")
            return WriteOutcome(None, WriteOutcome.PREV)
    if not safe_write.write_text(path, body, expect=before, expect_absent=before is None):
        log(f"{path.name} изменился под рукой — не перезаписываю")
        return WriteOutcome(None, WriteOutcome.RACE)
    if not live.is_file():
        log(f"паспорт {kind} не записан: стенограммы {live.name} нет — сайдкар был бы сиротой")
        return WriteOutcome(UNKNOWN)
    if not attest(live, kind, body, source_sha):
        log(f"паспорт {kind} не записан — следующая пересборка сочтёт файл чужим")
    return WriteOutcome(derivative_state(path, read(live), kind, source_sha))


def missing_reason(path: pathlib.Path) -> str:
    """Почему производная MISSING — словами, одним `stat` у оракула: «файла
    нет», «файл пуст», «файл не читается». Оракул различает состояния, но не
    причины; причину читатель не должен добирать вторым чтением диска мимо
    него (Important DS круга 4 по №314)."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return "файла нет"
    except OSError:
        return "файл не читается"
    return "файл пуст" if size == 0 else "файл есть"


ADOPT_OK = None      # исход присвоения: None — присвоено, иначе причина строкой


def adopted_stamp() -> str:
    """Значение отметки `<вид>_adopted` — одно на всех, кто признаёт легаси
    своим: `adopt` и раскладка канона минуток (№366) пишут её одинаково."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def adopt(live: pathlib.Path, kind: str, path: pathlib.Path, source_sha: str) -> str | None:
    """Присвоить производную без паспорта (UNKNOWN): паспорт на ТЕКУЩИЕ байты и
    текущий источник плюс отметка `<вид>_adopted` с датой — читатель паспорта
    видит, что это не машинная запись, а признание легаси (критика GLM
    выходного круга по №314). Годится ли файл — решает вызывающий по своему
    канону; здесь только гейт «паспорта нет, файл есть и непуст» и запись.
    Живому пути присвоение запрещено: UNKNOWN там либо строится политикой,
    либо остаётся незнанием (схождение DS и GLM выходного круга).

    Возвращает None, если присвоено, иначе причину словами (№277 «причина как
    значение»; Minor DS круга 3: одна выдуманная причина на четыре отказа).
    Гейт снимка защищает не файл (его adopt не пишет), а паспорт: байты,
    изменившиеся между чтением и записью, получили бы паспорт на прежний
    текст и на следующем чтении стали HUMAN (Minor DS круга 2). Три ключа —
    одним слиянием (Minor DS и GLM круга 2)."""
    meta = read(live) or {}
    state = derivative_state(path, meta, kind, source_sha)
    if state != UNKNOWN:
        return f"состояние {state}, присваивать нечего"
    if not live.is_file():
        return "стенограммы нет"
    before = safe_write.stat_snapshot(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "файл не читается"
    if before is None or safe_write.stat_snapshot(path) != before:
        return "файл менялся под рукой"
    if not merge(live, {f"{kind}_sha256": sha(text), f"{kind}_source_sha256": source_sha,
                        f"{kind}_adopted": adopted_stamp()}):
        return "сайдкар не записался"
    return ADOPT_OK


# Файлы папки архива встречи, у которых есть паспорт в сайдкаре стенограммы:
# имя → вид. Одна карта на проект — переименование встречи (`rename_meeting`)
# берёт её отсюда, а не держит копию (критика GLM выходного круга по №314).
# Канон поручений встречи `Минутки.md` — тоже производная с паспортом (№366):
# раскладка переписывает его, только пока байты — её собственные.
ARCHIVE_KINDS = {"Саммари.md": "summary", "Тезисы.md": "theses", "Минутки.md": "canon_minutes"}


def retouch(live: pathlib.Path, kind: str, path: pathlib.Path, transform) -> bool:
    """Механическая перезапись готовой производной (замена имени папки при
    переименовании встречи): байты меняет машина, источник — нет, поэтому
    переставляется только `<вид>_sha256`. Файл без паспорта или правленный
    руками (HUMAN) переписывается как раньше, паспорт не трогается: чужое не
    присваиваем. `transform(text) -> text`; False — запись не сделана.
    Файл не в UTF-8 — тоже False, не исключение: правленый канон минуток бывает
    в чужой кодировке (№366), и переименование встречи не должно обрываться
    посреди применения. Правило одно для всех видов карты."""
    # снимок ДО чтения: правка редактора между чтением и записью не затирается —
    # тот же гейт, что у `write_derivative` (Minor DS и GLM выходного круга)
    before = safe_write.stat_snapshot(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    new_text = transform(text)
    if new_text == text:
        return True
    meta = read(live) or {}
    ours = valid_sha(meta.get(f"{kind}_sha256")) == sha(text)
    if not safe_write.write_text(path, new_text, expect=before):
        return False
    if ours and live.is_file():
        remember(live, f"{kind}_sha256", sha(new_text))
    return True


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
        found = main_with_key(tdir, parts[0])
        if found is not None:
            return found
        if parts[0] == meeting_stamp.minute_of(parts[0]):
            return minute_owner(tdir, parts[0])
        return None
    found = main_with_key(tdir, stamp)
    if found is not None:
        return found
    return minute_owner(tdir, meeting_stamp.minute_of(stamp))


def main_with_key(tdir: pathlib.Path, key: str) -> pathlib.Path | None:
    """Главный файл с этим ключом (посекундным или минутным) среди файлов
    самого ключа — посекундных соседок и «-N» отсекает files_with_stamp."""
    return _pick_main(meeting_stamp.files_with_stamp(tdir, key, suffix=".md"), key)


def minute_owner(tdir: pathlib.Path, minute: str) -> pathlib.Path | None:
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
    return meeting_stamp.sidecar_claims(sidecar, bare)


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
    value = (read_direct(live) or {}).get("stamp")
    return value if _seconds_stamp_of_minute(value, key) else None


def read_direct(live: pathlib.Path) -> dict | None:
    """Содержимое СОБСТВЕННОГО сайдкара стенограммы `<имя>.md.live.json`; None —
    файла нет, не JSON-объект или не читается.

    Без поиска наследия: `read` через `sidecar_for` при отсутствии своего файла
    находит сироту той же минуты и отдаёт её ключи как свои. Для хешей цена
    ошибки — «распознаём заново»; для фактов о самой встрече — чужой штамп
    (`exact_stamp`, GLM Critical r1 по #492) или чужой исходник импорта, и
    встреча принимается за повтор и не импортируется (DS I1 круга 1 по PR №629).
    """
    try:
        meta = json.loads(_direct(live).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


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


# Один замок на все read-modify-write сайдкара в процессе: с №234 у файла два
# писателя внутри демона (след канала из потока сторожа и стоп-слияние из
# главного), и без замка второй затирал бы правку первого между чтением и
# записью (Critical GLM выходного круга). Чужие процессы (пересборка,
# graph_updater) пишут после остановки демона — окно между ними закрывает
# порядок финализации, не этот замок.
_RMW_LOCK = threading.RLock()


def merge(live: pathlib.Path, updates: dict, bare: str | None = None) -> bool:
    """Записать несколько ключей одним слиянием (read-modify-write), не дампом
    всего файла: стоп-дамп демона одной строкой `json.dumps({...})` затирал бы
    всё, что записали во время встречи (`channel_events`, №234) — сайдкар с
    живым писателем обязан писаться только слиянием (Critical DS и GLM
    входного круга). Правила выбора файла — те же, что у `remember`. Одно
    чтение и одна запись на весь словарь: цикл `remember` по ключам оставлял
    паспорт без отметки при отказе на третьем ключе (Minor DS круга 3 по №314)."""
    with _RMW_LOCK:
        return _merge_locked(live, dict(updates), bare)


def remember(live: pathlib.Path, key: str, value: str, bare: str | None = None) -> bool:
    """Записать ключ в сайдкар; нет файла — создать (импортированные встречи и
    сироты без live.json иначе оставались без защиты — DS M4 / GLM M1).
    Неоднозначный сайдкар — не писать, вернуть False. Под замком — и выбор
    файла (усыновление легаси переименовывает): иначе два писателя могли бы
    переименовать по-разному (Minor DS круга 2 по №234)."""
    with _RMW_LOCK:
        return _merge_locked(live, {key: value}, bare)


def _merge_locked(live: pathlib.Path, updates: dict, bare: str | None) -> bool:
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
    meta.update(updates)
    try:
        safe_write.write_text(p, json.dumps(meta, ensure_ascii=False))
    except OSError:
        return False
    return True
