#!/usr/bin/env python3
"""Забыть встречу целиком: стенограмма, запись, архив, узел графа, следы в Ядрах.

`record_keep_days` удаляет только аудио — остальное живёт вечно и в шести
местах сразу. Человеку, которому нужно убрать одну встречу (записал чужой
разговор, NDA, ошибка), приходилось вспоминать все шесть и не забыть ни
одного; Ядро после такой уборки оставалось со ссылкой в пустоту и с фактом,
пришедшим из удалённой встречи. Для продукта, который продаёт приватность,
удаление — такая же функция, как запись.

    .venv/bin/python scripts/forget_meeting.py 2026-07-15          # показать план
    .venv/bin/python scripts/forget_meeting.py 2026-07-15 --yes    # забыть
    .venv/bin/python scripts/forget_meeting.py 2026-07-15_1400 --yes --keep-graph

По умолчанию скрипт НИЧЕГО не удаляет: печатает, что собирается сделать.
Необратимое требует `--yes` — не потому, что так принято, а потому что
отменить это нельзя ничем.

Что удаляется:
    transcripts/<штамп>*            стенограмма и производные (минутки, разбор)
    transcripts/.prev/<штамп>*      версия до последней пересборки
    recordings/<штамп>*             запись, если ещё не истекла по ретеншну
    <граф>/Встречи/<штамп>.md       узел встречи
    <граф>/Встречи-архив/<папка>/   папка встречи со всеми документами
    <граф>/Документация/Стенограммы встреч/<штамп>.md
    <граф>/.cloud_backup/*/…        те же файлы встречи в снимках облачной
                                    ревизии: бэкап графа копирует его целиком,
                                    и без этой строки «забыть» оставляло бы
                                    стенограмму лежать в копиях рядом
    backups/<граф>-<хеш>/cloud_backup/  те же снимки в НОВОМ месте (вне
                                    iCloud, с 21.08) — и файлы, и строки хроники
    backups/<граф>-<хеш>/cloud_quarantine/<штамп>-*/  карантин разбора ЭТОЙ
                                    встречи целиком (версии облака, убранные
                                    сверкой), а в карантинах других встреч —
                                    файлы с этим штампом

Что правится (не удаляется):
    строки хроники в Ядрах, которые ссылались на эту встречу, — уходят вместе
    с фактом, который из неё пришёл: он держался на этой встрече и проверить
    его больше нечем;
    ссылки в связном тексте (Досье, узлы людей) — заменяются пометкой, чтобы
    не осталось ссылки в пустоту и было видно, почему тут пробел.

Перед каждой правкой выжившего узла — бэкап: удаляем встречу, а не чужие
заметки. Сами файлы встречи бэкапом НЕ дублируются: копия удалённого — это
не удаление, а перемещение, и человек имеет право понимать разницу.

Чего этот скрипт не может: достать копии, которые уже уехали в iCloud или
Time Machine, и вычистить чужие устройства. Об этом сказано в PRIVACY.md.
"""
from __future__ import annotations

import json
import argparse
import dataclasses
import os
import pathlib
import re
import shutil
import stat
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402
import live_sidecar  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import charoite_paths  # noqa: E402
import graphs  # noqa: E402
import meeting_stamp  # noqa: E402

ARCHIVE_DIR = "Встречи-архив"
MEETINGS_DIR = "Встречи"
DOCS_DIR = pathlib.Path("Документация") / "Стенограммы встреч"
BACKUP_DIR = ".forget_backup"
CLOUD_BACKUP_DIR = ".cloud_backup"      # снимки графа перед облачной правкой
CLOUD_QUARANTINE = "cloud_quarantine"   # версии облака, убранные сверкой (№88)
REMOVED_NOTE = "(встреча удалена)"


def _in_cloud_snapshot(path: pathlib.Path) -> bool:
    """Лежит ли файл внутри снимка облачной ревизии.

    Мест два: до 21.08 снимки жили в графе (`.cloud_backup`), теперь — в
    данных (`backups/<граф>-<хеш>/cloud_backup`); старые каталоги внешних
    установок переносятся один раз вручную (см. заметки релиза). Признак —
    принадлежность известным корням, а не имя сегмента: пользовательская
    папка `cloud_backup` в графе не должна лишать файлы страховочной копии
    (круг по PR #363: qwen + GLM + DeepSeek).
    """
    if CLOUD_BACKUP_DIR in path.parts:          # старое место, имя с точкой
        return True
    try:
        return path.resolve().is_relative_to(
            (ROOT / charoite_paths.BACKUPS_DIR).resolve())
    except (OSError, ValueError):
        return False


# «- [[Встречи/2026-07-15_1400]] — выбрали ЮPay» — строка хроники целиком.
_LINE = "- "


@dataclasses.dataclass
class Plan:
    """Что будет удалено и что переписано. Пустой план — забывать нечего."""

    stamp: str
    delete: list[pathlib.Path] = dataclasses.field(default_factory=list)
    edit: dict[pathlib.Path, str] = dataclasses.field(default_factory=dict)
    # Куда «забыть» не дотягивается, но человек должен знать (аудит 16.08,
    # п.1): копии в iCloud, Time Machine, у других участников.
    beyond_reach: list[str] = dataclasses.field(default_factory=list)
    # Ключи, под которыми факты встречи лежат в памяти Чароита (brain :8100):
    # ключ графа (минутный у владельца минуты, посекундный у соседки) и сам
    # штамп, если отличается. /forget у brain есть с 23.08 (карточка №41).
    brain_keys: list[str] = dataclasses.field(default_factory=list)
    # Как план решил, чьи посекундные файлы минуты: человек проверяет довод
    # ДО необратимого удаления (критика DS r4 по #499)
    notes: list[str] = dataclasses.field(default_factory=list)

    def describe(self) -> str:
        out = [f"Встреча {self.stamp}"]
        out += [f"  {line}" for line in self.notes]
        if not self.delete and not self.edit and not self.beyond_reach:
            return out[0] + ": следов не найдено — забывать нечего"
        if self.delete:
            out.append(f"  удалить ({len(self.delete)}):")
            out += [f"    {p}" for p in self.delete]
        if self.edit:
            out.append(f"  поправить ({len(self.edit)}):")
            out += [f"    {p}" for p in self.edit]
        if self.beyond_reach:
            out.append("  не дотянется:")
            out += [f"    {line}" for line in self.beyond_reach]
        return "\n".join(out)


def _link_re(stamp: str) -> re.Pattern[str]:
    """`[[Встречи/<штамп>]]` и `[[Встречи/<штамп>|любой алиас]]`."""
    return re.compile(rf"\[\[{MEETINGS_DIR}/{re.escape(stamp)}(?:\|[^\]]*)?\]\]")


def _graph_roots(graph: pathlib.Path | None) -> list[pathlib.Path]:
    """Граф из аргумента или все графы vault — встреча живёт в одном из них."""
    if graph is not None:
        return [graph]
    return graphs.all_graphs(MEETINGS_DIR) or graphs.all_graphs(ARCHIVE_DIR)


# Суффикс коллизии «-N» — часть штампа: «2026-08-21_125812-1» — другая встреча,
# не «…125812» (круг-1 по PR #388, Codex).
_STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{4,6}(?:-\d+)?)(?![\d-])")
STATUS_DIR = pathlib.Path("logs") / "meeting-status"


def _with_stamp(directory: pathlib.Path, stamp: str, *, prefix: str = "",
                suffix: str = "") -> list[pathlib.Path]:
    """Файлы этой встречи с границей штампа — правило живёт в meeting_stamp."""
    return meeting_stamp.files_with_stamp(directory, stamp, prefix=prefix,
                                          suffix=suffix)


_QUARANTINE_TIME_RE = re.compile(r"-\d{6,12}$")   # «-HHMMSS» или «-HHMMSSffffff»


def _quarantine_of(name: str, stamp: str) -> bool:
    """Каталог карантина `<стем>-<время>` принадлежит встрече штампа.

    `2026-07-15_1400-…` и `2026-07-15_1400_тема-…` — да; `2026-07-15_140030-…`
    (посекундная сестра той же минуты) — нет: после штампа стоит цифра.
    Суффикс времени проверяется именно как суффикс: дефис внутри темы —
    не разделитель (круг-5 по PR #381), а штамп должен быть полным.
    """
    if not _STAMP_RE.fullmatch(stamp) or not name.startswith(stamp):
        return False
    rest = name[len(stamp):]
    if rest[:1].isdigit() or not _QUARANTINE_TIME_RE.search(rest):
        return False
    return rest.startswith("-") or rest.startswith("_")


def _status_files(status_dir: pathlib.Path, stamp: str) -> list[pathlib.Path]:
    """Статусы конвейера этой встречи.

    Файл статуса назван по ЖИВОЙ стенограмме — с секундами
    (`2026-08-03_113012.json`), а штамп встречи после наката темы минутный
    (`2026-08-03_1130`): по одному имени с границей штампа такой файл не
    найти. Поэтому второй признак — `transcript_path` внутри: чей файл
    стенограммы он описывает (найдено тестом rename при переносе правила
    границы в meeting_stamp, 16.08).
    """
    if not status_dir.is_dir():
        return []
    import json
    from meeting_processing import find_final_transcript
    found = set(_with_stamp(status_dir, stamp, suffix=".json"))
    records: list[tuple[pathlib.Path, pathlib.Path]] = []       # (статус, сырой путь)
    for f in status_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue                     # не-словарь — мусор, не повод ронять forget (luna r3)
        tp = str(data.get("transcript_path") or "")
        if tp:
            records.append((f, pathlib.Path(tp)))
    # 1) по сырому имени — как и раньше
    for f, raw in records:
        if f not in found and meeting_stamp.belongs(raw.name, stamp):
            found.add(f)
    # 2) по тому, куда мёртвый путь резолвится сегодня: после retitle статус мог
    # остаться с голым посекундным путём (окно до следующей записи), а минутная
    # граница его не видит (luna r3). Но резолв мёртвого пути соседки падает на
    # файл владельца минуты — принимаем совпадение, только если этот файл ещё не
    # заявлен статусом, найденным по имени (DS r4).
    claimed: set[pathlib.Path] = set()
    for f, raw in records:
        if f in found:
            try:
                claimed.add(find_final_transcript(raw))
            except OSError:
                continue
    for f, raw in records:
        if f in found or raw.is_file():
            continue
        try:
            current = find_final_transcript(raw)
        except OSError:
            continue                     # демон переименовывает файл под ногами (DS r4)
        if current not in claimed and meeting_stamp.belongs(current.name, stamp):
            found.add(f)
    return sorted(found)


def stamps(root: pathlib.Path, graph: pathlib.Path | None = None) -> list[str]:
    """Штампы всех известных встреч: по стенограммам и по узлам графа.

    Смотреть только в transcripts/ мало: стенограмму могли удалить руками, а
    архив и узел остались — именно их человек и хочет убрать.
    """
    found: set[str] = set()
    for p in (root / "transcripts").glob("*.md"):
        # Срез в 15 знаков резал штампы с секундами (`_140030` → `_1400`):
        # такие файлы реальны, и «забыть» тогда искал не ту встречу.
        m = _STAMP_RE.match(p.stem)
        found.add(m.group(1) if m else p.stem)
    for g in _graph_roots(graph):
        for p in (g / MEETINGS_DIR).glob("*.md"):
            if not p.name.startswith("_"):
                found.add(p.stem)
    return sorted(s for s in found if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4,6}(?:-\d+)?", s))


def resolve(target: str, root: pathlib.Path,
            graph: pathlib.Path | None = None) -> list[str]:
    """Дата или штамп → список штампов встреч. Пусто — такой встречи нет."""
    target = target.strip().rstrip("/")
    known = stamps(root, graph)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        return [s for s in known if s.startswith(target)]
    return [s for s in known if s == target]


_DAY_FOLDER_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})(?:"
    r" (?P<hm>\d{2}-\d{2}(?:-\d{2})?(?:-\d+)?)"      # «2026-07-15 14-00[-30][-1] — Тема»
    r"|_(?P<raw>\d{4}(?:\d{2})?(?:-\d+)?)"          # «2026-07-15_1400[30][-1] — Тема»
    r")?[ \t]+—[ \t]+")                              # пробелы вокруг тире — любые


def _day_folders(arch: pathlib.Path, day: str) -> list[tuple[pathlib.Path, str | None]]:
    """Папки архива этого дня во всех трёх форматах имени с их временем в
    нормальном виде («14-00», «14-00-30», «14-00-30-1»); None — папка без
    времени («дата — тема»). Один разбор на все форматы: две правки подряд
    с перечислением форматов по месту дали два Critical подряд (круг-1 и
    круг-2 по PR #388) — правило теперь одно."""
    out = []
    for d in sorted(arch.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        m = _DAY_FOLDER_RE.match(d.name)
        if not m or m.group("day") != day:
            continue
        if m.group("hm"):
            out.append((d, m.group("hm")))
        elif m.group("raw"):
            out.append((d, meeting_stamp.archive_time(f"{day}_{m.group('raw')}")))
        else:
            out.append((d, None))
    return out


def _archive_folders(g: pathlib.Path, stamp: str) -> list[pathlib.Path]:
    """Папки архива этой встречи.

    Время сравнивается целиком и в нормальном виде для всех трёх форматов
    имени: минутный ключ не забирает папку посекундной соседки («14-00-12»),
    суффикс «-N» — отдельная встреча. Папка с манифестом чужой встречи
    (meeting_id другой) — не наша. Папка без времени («дата — тема») —
    только когда сомнений нет: она единственная за день.
    """
    arch = g / ARCHIVE_DIR
    if not arch.is_dir():
        return []
    day = stamp[:10]
    mine_time = meeting_stamp.archive_time(stamp)
    folders = _day_folders(arch, day)
    out = []
    for d, when in folders:
        owner = meeting_archive_id(d)
        if owner == stamp:            # манифест называет нас — формат имени не важен
            out.append(d)
            continue
        if when != mine_time or owner is not None:
            continue                  # другое время или чужая встреча по манифесту
        out.append(d)
    if out:
        return out
    if len(folders) == 1 and folders[0][1] is None:
        # Единственная папка без времени — наша, если манифест не говорит
        # обратного (круг-3 по PR #388, Codex).
        owner = meeting_archive_id(folders[0][0])
        if owner is None or owner == stamp:
            return [folders[0][0]]
    return []


def meeting_archive_id(folder: pathlib.Path) -> str | None:
    """meeting_id из манифеста папки архива; None — манифеста нет или бит."""
    try:
        return json.loads((folder / "meeting.meta.json").read_text(encoding="utf-8")).get("meeting_id")
    except (OSError, ValueError, AttributeError):
        return None


class _Ownership:
    """Какие штампы — этой встречи: сам `stamp`, а для минутного ключа — и
    посекундные штампы его минуты, если под ними нет ДРУГОЙ встречи.

    Приложение зовёт «забыть» минутным ключом, а демон и импорт именуют
    файлы посекундно: записи владельца минуты лежат как `<минутаСС>_mic.wav`,
    копия импорта — с посекундным штампом в сайдкаре (Critical DS r1 по
    #499). Но та же минута — штатное место соседки (демон после краха
    поднимается через две секунды), и «нет заметки в графе» её не отличает:
    заметка появляется после стопа, а файлы — раньше (Critical DS r2,
    Important GLM r2). Поэтому сначала ищется ТОЧНАЯ секунда владельца
    минуты — не угадыванием, а из уже существующих источников конвейера:
    ключ `stamp` прямого сайдкара минутно названного главного файла
    (live_sidecar.exact_stamp — слово демона), строка «Стенограмма:» заметки
    `Встречи/<минута>.md` (meeting_stamp.note_transcript_stamp — по ней же
    note_is_ours решает владение), а до наката темы, когда главный файл ещё
    голый посекундный, — правило graph_key: самый ранний голый посекундный
    без собственной заметки (Critical DS r3). Любая другая секунда минуты —
    наша, только если под ней нет ни главного файла стенограммы (голый —
    тоже улика: соседка до наката темы и остаток прерванного переименования
    по именам неразличимы, а удалять чужое нельзя), ни заметки в графе, ни
    незаконченной записи (`.pcm`, `.wav.part` — соседка пишется прямо сейчас
    или ждёт восстановления, Critical GLM r3), а копия импорта без единого
    следа в transcripts/ и recordings/ — ничья, не решить (критика GLM r3).
    Спорное остаётся на диске и называется вслух (`foreign`: штамп → почему).
    """

    def __init__(self, stamp: str, root: pathlib.Path, graph: pathlib.Path | None):
        self.stamp = stamp
        self.minute = meeting_stamp.minute_of(stamp) == stamp
        self.tdir = root / "transcripts"
        self.rec = root / "recordings"
        self.graph = graph
        self.foreign: dict[str, str] = {}
        self.unsure: str | None = None       # заметку минуты не прочитать — владение не решить
        self.how: str = ""                   # чем доказано владение точной секундой
        self.passport: dict[str, str] = {}   # штамп → файл-копия, по которому он признан своим
        self.exact: str | None = self._exact() if self.minute else None

    def _own_note(self, s: str) -> bool:
        return self.graph is not None and (self.graph / "Встречи" / f"{s}.md").is_file()

    def _note_stamp(self) -> str | None:
        """Секунда из строки «Стенограмма:» заметки минуты — только своей минуты."""
        if self.graph is None:
            return None
        note = self.graph / "Встречи" / f"{self.stamp}.md"
        if not note.is_file():
            return None
        try:
            its = meeting_stamp.note_transcript_stamp(note.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # Не «строки нет», а «не решить»: iCloud-заминка не должна отдавать
            # владение самому раннему файлу вопреки заметке (DS r4 M1); битая
            # кодировка (UnicodeDecodeError — ValueError) — то же «не решить»,
            # а не трейсбек на кнопке «Забыть» (GLM r5 M1)
            self.unsure = f"заметку минуты не прочитать ({e.__class__.__name__}) — владение не решить"
            return None
        if its and its != self.stamp and meeting_stamp.minute_of(its) == self.stamp:
            return its
        return None

    def _bare_mains(self) -> list[str]:
        """Голые посекундные главные файлы минуты — кандидаты во владельцы до
        наката темы; порядок — по штампу, самый ранний первый."""
        if not self.tdir.is_dir():
            return []
        out = []
        for f in self.tdir.glob(f"{self.stamp}*.md"):
            s = meeting_stamp.stamp_of(f.stem)
            if s and s == f.stem and s != self.stamp and meeting_stamp.minute_of(s) == self.stamp:
                out.append(s)
        return sorted(out)

    def _exact(self) -> str | None:
        live = live_sidecar.minute_owner(self.tdir, self.stamp) if self.tdir.is_dir() else None
        if live is not None:
            # минута уже названа темой: голые посекундные рядом — соседки или
            # остатки (правило graph_key), владельца выдаёт только сайдкар
            # или заметка
            exact = live_sidecar.exact_stamp(live)
            if exact:
                self.how = f"по сайдкару {live.name}"
                return exact
            its = self._note_stamp()
            if its:
                self.how = "по строке «Стенограмма:» заметки минуты"
            return its
        its = self._note_stamp()
        if its:
            self.how = "по строке «Стенограмма:» заметки минуты"
            return its
        if self.unsure:
            return None
        bare = [s for s in self._bare_mains() if not self._own_note(s)]
        if bare:
            self.how = "самый ранний голый посекундный файл минуты (правило graph_key)"
        return bare[0] if bare else None

    def owns(self, s: str, *, traced: bool = True) -> bool:
        """`traced` — штамп виден в transcripts/, recordings/ или .prev; копия
        импорта без следа решается только по точной секунде."""
        if s == self.stamp or (self.exact is not None and s == self.exact):
            return True
        if not self.minute or meeting_stamp.minute_of(s) != self.stamp:
            return False
        why = self._foreign_reason(s, traced)
        if why:
            self.foreign.setdefault(s, why)
            return False
        return True

    def _foreign_reason(self, s: str, traced: bool) -> str | None:
        if self.unsure:                  # заметку минуты не прочитать — всё посекундное замораживаем
            return self.unsure
        if live_sidecar.main_with_key(self.tdir, s) is not None or self._own_note(s):
            return ("под ним своя стенограмма или заметка — соседка (крэш-рестарт в ту же "
                    "минуту) или остаток прерванного переименования")
        if any(meeting_stamp.recording_unfinished(f.name)
               for f in meeting_stamp.files_with_stamp(self.rec, s)):
            return "под ним идёт запись или ждёт восстановления"
        if not traced:
            return "в transcripts/ и recordings/ следа нет — чья это копия, не решить"
        # Паспорт владельца до наката темы — копии его же стенограммы под
        # старым именем (`<s>_live.md`, `.prev/<s>.md`): ретитл переименовал
        # источник, копии остались. Копия write_final всегда несёт шапку
        # «# Встреча …» — файл без неё (чужая заметка, случайно названная
        # штампом) паспортом не считается (критика GLM r5). Одна запись без
        # стенограммы и заметки — не паспорт: у соседки она есть раньше
        # стенограммы (DS r4 I1); её удалит ретеншн, а план говорит вслух.
        for f in (meeting_stamp.files_with_stamp(self.tdir, s, suffix=".md")
                  + meeting_stamp.files_with_stamp(self.tdir / ".prev", s)):
            if meeting_stamp.first_line(f).startswith("# Встреча "):
                self.passport[s] = f.name
                return None
        return "под ним только запись без стенограммы и заметки — чья, не решить; аудио удалит ретеншн"


def plan(stamp: str, root: pathlib.Path,
         graph: pathlib.Path | None = None, keep_graph: bool = False,
         import_folder: pathlib.Path | None = None) -> Plan:
    """Собрать план: что удалить, что переписать. Ничего не меняет.

    `import_folder` — папка импорта приложения: копия аудио в её `done/` и
    сайдкар знают штамп встречи и обязаны уйти вместе с ней, иначе голоса
    участников переживают «забыть» до import_keep_days (аудит GLM 05.09).
    Путь знает только приложение — без него об этом говорим вслух.
    """
    p = Plan(stamp=stamp)

    # Приложение зовёт «забыть» минутным ключом, а демон и импорт именуют
    # файлы посекундно: правило владения — в _Ownership (Critical DS r1 и r2
    # по #499).
    own = _Ownership(stamp, root, graph)
    seen: set[str] = set()
    for folder in (root / "transcripts", root / "recordings", root / "transcripts" / ".prev"):
        for f in (folder.iterdir() if folder.is_dir() else ()):
            s = meeting_stamp.stamp_prefix(f.name)
            if s:
                seen.add(s)
    owned = [stamp] + [s for s in sorted(seen) if s != stamp and own.owns(s)]
    if own.exact:
        p.notes.append(f"владелец минуты — секунда {own.exact}: {own.how}")
    elif own.unsure:
        p.notes.append(f"посекундные файлы минуты не тронуты: {own.unsure}")
    if own.passport:
        p.notes.append("свои по копиям стенограммы под старым именем: "
                       + ", ".join(f"{s} ({name})" for s, name in sorted(own.passport.items())))

    for folder in ("transcripts", "recordings"):
        for s in owned:
            p.delete += _with_stamp(root / folder, s)
    # версия до пересборки (transcripts/.prev/<имя>) — тот же текст встречи:
    # скрытая папка не обходится глобом, забыть обязаны и её (GLM r1 по #456).
    # Копия названа именем файла НА МОМЕНТ пересборки: до наката темы — голый
    # посекундный штамп, который минутный глоб с границей не видит (DS r2);
    # поэтому — по каждому своему штампу (GLM r2 по #499), по именам
    # удаляемых стенограмм и по ключам статусов (там лежит штамп исходного
    # файла).
    prev_dir = root / "transcripts" / ".prev"
    for s in owned:
        p.delete += [f for f in _with_stamp(prev_dir, s) if f not in p.delete]
    if prev_dir.is_dir():
        names = {f.name for f in p.delete if f.parent == root / "transcripts"}
        p.delete += [f for f in prev_dir.iterdir() if f.name in names and f not in p.delete]
    # Сайдкар live.json переехавшей встречи минутный глоб выше уносит сам.
    # Сайдкар под посекундным именем (встречи до 0.69.1) — только тот, чей
    # владелец по штампу (live_sidecar.owner_of) уходит вместе с ней:
    # «единственный в минуте — мой» удалял сайдкар живой соседки (DS r3 по
    # #489), а без уборки сирота с именами участников переживала «забыть»
    # (GLM r3).
    tdir = root / "transcripts"
    if tdir.is_dir():
        gone = {f for f in p.delete if f.parent == tdir}
        minute = meeting_stamp.minute_of(stamp)
        for sc in tdir.glob("*.md.live.json"):
            if sc in p.delete:
                continue
            owner = live_sidecar.owner_of(sc)
            sc_stem = sc.name[:-len(".md.live.json")]
            parts = meeting_stamp.decompose(sc_stem)
            base = meeting_stamp.stamp_of(sc_stem) or (parts[0] if parts else None)
            # Свой — уходит; бесхозный ПОСЕКУНДНЫЙ сайдкар этой минуты —
            # мёртвый след (живая встреча в минуте всегда даёт владельца),
            # имена участников не должны его переживать (advisory DS r5).
            # Имена с темой без файла — владельца минуты, их решает owner_of
            # (Important DS r6).
            orphan = owner is None and base and base != meeting_stamp.minute_of(base) \
                and meeting_stamp.minute_of(base) == minute
            if owner in gone or orphan:
                p.delete.append(sc)

    # Копия импорта: done/<файл> и .<файл>.imported.json со штампом встречи
    gone_stamps = {meeting_stamp.stamp_of(f.stem) for f in p.delete
                   if f.parent == root / "transcripts"} - {None}
    gone_stamps.update(owned)
    named: set[str] = set()      # чужие штампы, уже названные в блоке импорта
    done_dir = pathlib.Path(import_folder).expanduser() / "done" if import_folder else None
    if done_dir is not None and done_dir.is_dir():
        minute = meeting_stamp.minute_of(stamp)
        for sc in sorted(done_dir.glob(".*.imported.json")):
            try:
                meta = json.loads(sc.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            sc_stamp = meta.get("stamp")
            if not isinstance(sc_stamp, str):
                continue
            # Копия с посекундным штампом нашей минуты — по тому же правилу
            # владения, что стенограммы и записи выше (Important GLM r2 по #499:
            # критерий один и только для минутной цели); без следа на диске —
            # только по точной секунде
            if not (sc_stamp in gone_stamps or own.owns(sc_stamp, traced=sc_stamp in seen)):
                if meeting_stamp.minute_of(sc_stamp) == minute:
                    # Та же минута, но не эта встреча — не трогаем, говорим вслух (GLM r1)
                    why = own.foreign.get(sc_stamp, "не эта встреча")
                    named.add(sc_stamp)
                    p.beyond_reach.append(f"в папке импорта лежит копия с штампом {sc_stamp}: "
                                          f"та же минута, но {why}; забыть отдельно по штампу "
                                          f"{sc_stamp}: {sc.name[1:-len('.imported.json')]}")
                continue
            owner = done_dir / sc.name[1:-len(".imported.json")]
            if owner.is_file() and owner not in p.delete:
                p.delete.append(owner)
            if sc not in p.delete:
                p.delete.append(sc)
    elif import_folder is None:
        p.beyond_reach.append("копия исходника в папке импорта (done/), если встреча "
                              "импортирована: путь знает приложение (--import-folder); "
                              "без него её удалит ретеншн import_keep_days")
    for s, why in own.foreign.items():
        if s in seen and s not in named:      # один голос на соседку (GLM/DS r3)
            p.beyond_reach.append(f"файлы с посекундным штампом {s} той же минуты: {why}; "
                                  f"забыть отдельно по штампу {s}")

    # Логи графа этой встречи: в logs/graph_<штамп>*.log попадают имена
    # участников и куски цитат — «забыть» обязано дойти и до них, иначе
    # содержимое встречи переживает саму встречу (аудит 0.46.0: «забыть»
    # не доходит до логов).
    logs = root / "logs"
    # Все три класса логов названы МИНУТНЫМ штампом (daemon: `stem[:15]`,
    # graph_updater: parse_stem, rebuild: `stem[:15]`), а штамп посекундной
    # встречи без темы — с секундами: по нему логи не находились и переживали
    # забывание (второе мнение DeepSeek по партии 16.08). Две встречи одной
    # минуты пишут в один и тот же лог — он общий, и удалить его при
    # забывании любой из них честнее, чем оставить.
    log_stamp = stamp[:15]
    # Вторая встреча той же минуты живёт под посекундным ключом — её
    # облачный лог и отметка brain названы им; минутный префикс с границей
    # штампа их не видит (живая проверка 23.08, карточка №39).
    log_stamps = [log_stamp] + ([stamp] if stamp != log_stamp else [])
    for ls_ in log_stamps:
        p.delete += _with_stamp(logs, ls_, prefix="graph_", suffix=".log")
    # Лог облачной ревизии называется иначе и потому переживал забывание:
    # внутри — имена файлов встречи (а тема встречи стоит в имени),
    # счётчики и stderr CLI (аудит 16.08).
    for ls_ in log_stamps:
        p.delete += _with_stamp(logs, ls_, prefix="cloud_review_", suffix=".log")
    # Лог повторной пересборки (retry_<штамп>.log): stdout rebuild_transcript
    # с маппингом имён участников и темой — третий класс, который ни ретеншн,
    # ни «забыть» не видели (аудит DeepSeek 16.08).
    for ls_ in log_stamps:
        p.delete += _with_stamp(logs, ls_, prefix="retry_", suffix=".log")
    # Отметка «факты встречи отправлены в память Чароита» (graph_updater):
    # без неё повторный разбор той же встречи после забывания молчал бы.
    # Отметка «факты отправлены» (logs/brain_sent/<ключ графа>.txt) и сами
    # факты в памяти-компаньоне (brain :8100) — по ключу графа, который
    # станет известен ниже, по узлу встречи; минутная отметка соседки той
    # же минуты — не наша (карточка №39).
    p.brain_keys = [stamp]
    # Статус конвейера (logs/meeting-status/<стенограмма>.json): путь к
    # стенограмме — с темой в имени, этап, текст ошибки; его же читает
    # список «Недавние встречи». Чистится сам через 14 дней, но «забыть»
    # обязано дойти сразу (второе мнение по #324–#328, 16.08).
    statuses = _status_files(root / STATUS_DIR, stamp)
    p.delete += statuses
    if prev_dir.is_dir():
        import json as _json
        for sf in statuses:
            try:
                data = _json.loads(sf.read_text(encoding="utf-8"))
                key = str(data.get("key") or "") if isinstance(data, dict) else ""
            except (OSError, ValueError):
                continue
            if key:
                p.delete += [f for f in _with_stamp(prev_dir, key) if f not in p.delete]

    if keep_graph:
        p.brain_keys = []          # граф остаётся — остаётся и память о встрече
        return p

    for g in _graph_roots(graph):
        # Узел — по ключу графа: у второй встречи той же минуты он посекундный,
        # у первой минутный; минутную заметку берём только если она наша
        # (meeting_stamp.find_note, карточка №39).
        node = meeting_stamp.find_note(g, stamp, root / "transcripts")
        link = _link_re(node.stem if node else stamp)
        if node is not None:
            p.delete.append(node)
            if node.stem not in p.brain_keys:
                p.brain_keys.append(node.stem)
        # graph_updater копирует в «Стенограммы встреч» все артефакты
        # `{штамп}_*.md` (минутки, подсказки, живая нить), а не один
        # `{штамп}.md` — иначе копии стенограммы переживали забывание.
        p.delete += _with_stamp(g / DOCS_DIR, stamp, suffix=".md")
        p.delete += _archive_folders(g, stamp)

        # Снимки облачной ревизии копируют граф целиком; срез теперь один,
        # но у установок до переноса каталогов может быть несколько — обходим
        # все, что найдём. «Забыть» обязано дойти и туда — иначе оно
        # переименовывает файл, а не убирает встречу.
        # Мест два: снимки уехали из графа в данные (21.08, чтобы iCloud не
        # гонял их в облако), но у установок, где перенос ещё не сделан, они
        # лежат по-старому внутри графа. Забывание обязано дойти до обоих —
        # молча пропустить старое место значит оставить встречу в копиях.
        for cloud in (charoite_paths.graph_backups(
                          g, CLOUD_BACKUP_DIR.lstrip("."), root=root),
                      g / CLOUD_BACKUP_DIR):
            if not cloud.is_dir():
                continue
            for snap in sorted(d for d in cloud.iterdir() if d.is_dir()):
                node_copy = snap / MEETINGS_DIR / f"{stamp}.md"
                if node_copy.exists():
                    p.delete.append(node_copy)
                p.delete += _with_stamp(snap / DOCS_DIR, stamp, suffix=".md")
                p.delete += _archive_folders(snap, stamp)

        # Карантин облачного разбора: каталог запуска ЭТОЙ встречи — целиком
        # (в нём версии облака, сделанные по её стенограмме), в карантинах
        # остальных запусков — файлы с её штампом (круг-1 по PR #381, Codex).
        # Каталог запуска назван стемом стенограммы (`<штамп>`, с секундами
        # или с темой после «_») плюс «-<время>». Граница — та же, что у всех
        # файлов встречи (meeting_stamp): после штампа не цифра, иначе
        # забывание минутной встречи уносило бы посекундную сестру (круг-3 и
        # круг-4 по PR #381). Для чужих запусков — точечно, по файлам со
        # штампом.
        quarantine = charoite_paths.graph_backups(g, CLOUD_QUARANTINE, root=root)
        if quarantine.is_dir():
            for run_dir in sorted(d for d in quarantine.iterdir() if d.is_dir()):
                if _quarantine_of(run_dir.name, stamp):
                    p.delete.append(run_dir)
                    continue
                node_copy = run_dir / MEETINGS_DIR / f"{stamp}.md"
                if node_copy.exists():
                    p.delete.append(node_copy)
                p.delete += _with_stamp(run_dir / DOCS_DIR, stamp, suffix=".md")
                p.delete += _archive_folders(run_dir, stamp)

        # Файлы внутри удаляемой папки править не нужно и нельзя: папка уйдёт
        # целиком, а запись в неё после удаления — FileNotFoundError.
        doomed = {q.resolve() for q in p.delete}
        doomed_dirs = [q.resolve() for q in p.delete if q.is_dir()]
        # Хроники правим и в СНИМКАХ нового места: rglob по графу их больше
        # не видит (снимки уехали из графа), а строка «[[Встречи/…]] — …»
        # в копии ядра — такой же след встречи, как в самом ядре (круг по
        # PR #363, GLM+DeepSeek: старое место правилось, новое — нет).
        snap = charoite_paths.graph_backups(
            g, CLOUD_BACKUP_DIR.lstrip("."), root=root)
        editable = sorted(g.rglob("*.md")) + (
            sorted(snap.rglob("*.md")) if snap.is_dir() else [])
        for f in editable:
            real = f.resolve()
            if real in doomed or BACKUP_DIR in f.parts:
                continue
            if any(real.is_relative_to(d) for d in doomed_dirs):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not link.search(text):
                continue
            fixed = _strip_meeting(text, link)
            if fixed != text:
                p.edit[f] = fixed
    # Сами факты (тема, участники, решения) живут в памяти-компаньоне
    # (brain :8100): забываем их там по ключу, а не говорим «вне
    # досягаемости» (аудит 16.08 п.1 → карточка №41). Отметка brain_sent
    # названа ключом графа; без отметки тоже пробуем — отметки появились
    # позже фактов.
    for key in p.brain_keys:
        p.delete += _with_stamp(logs / "brain_sent", key, suffix=".txt")
    return p


def _strip_meeting(text: str, link: re.Pattern[str]) -> str:
    """Убрать следы встречи из текста узла, который остаётся жить.

    Строка-пункт, ссылающаяся на встречу, — это её след целиком (хроника
    Ядра: «- [[Встречи/…]] — выбрали ЮPay»), и уходит она вместе с фактом.
    Ссылка внутри фразы заменяется пометкой: удалить полфразы было бы
    хуже, чем оставить видимый пробел.
    """
    out = []
    for line in text.splitlines():
        if not link.search(line):
            out.append(line)
            continue
        if line.lstrip().startswith(_LINE):
            continue    # пункт списка = запись о встрече
        out.append(link.sub(REMOVED_NOTE, line))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _backup(path: pathlib.Path, stamp: str) -> None:
    """Копия узла до правки — рядом с графом, в скрытой папке."""
    for parent in path.parents:
        if (parent / MEETINGS_DIR).is_dir() or (parent / ARCHIVE_DIR).is_dir():
            dest = parent / BACKUP_DIR / stamp / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            return


def apply(p: Plan, yes: bool = False) -> bool:
    """Выполнить план. Без yes — только показать; вернёт False, что не делал."""
    print(p.describe())
    if not p.delete and not p.edit:
        return False
    if not yes:
        print("\nничего не сделано. Повторите с --yes, если это то, что нужно:\n"
              "  удаление стенограмм, записи, папки архива и узла встречи необратимо")
        return False

    left = []
    for path in p.delete:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.lexists(path):
                path.unlink()
        except OSError:
            pass
        if os.path.lexists(path):
            left.append(path)      # права/занятый том — сказать, а не «забыто»
    if left:
        print("НЕ удалено (проверь права и повтори): "
              + ", ".join(str(q) for q in left))
    for path, text in p.edit.items():
        # копии внутри снимка правим (там те же строки хроники), но бэкап
        # бэкапа не снимаем: он воскресил бы то, что человек забывает.
        # Имён два: старое `.cloud_backup` внутри графа и новое `cloud_backup`
        # в данных — снимки уехали из iCloud 21.08.
        if not _in_cloud_snapshot(path):
            _backup(path, p.stamp)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        # Права узла — как были: конвейер пишет граф под harden_umask (0600),
        # а write_text давал 0644 по umask вызывающего — поправленный узел
        # становился читаемым для всех (аудит DeepSeek 16.08).
        try:
            tmp.chmod(stat.S_IMODE(path.stat().st_mode))
        except OSError:
            pass
        tmp.replace(path)
    print(f"\nзабыто: удалено {len(p.delete) - len(left)}, поправлено {len(p.edit)}"
          f" (копии поправленных — в {BACKUP_DIR}/{p.stamp})")
    for key in p.brain_keys:
        print(f"  память Чароита: {brain_forget(key)}")
    print("Что осталось вне досягаемости: копии в iCloud и бэкапах Time Machine,"
          " файлы у других участников — см. PRIVACY.md")
    for line in p.beyond_reach:
        print(f"  и ещё: {line}")
    return True


BRAIN = "http://127.0.0.1:8100"


def brain_forget(key: str) -> str:
    """POST /forget в память Чароита; строка для человека, не исключение.

    brain может быть выключен — «забыть» файлы от этого не зависит, но
    молчать нельзя: человек должен знать, что память не чищена и как
    повторить (раньше это место честно говорило «/forget у неё нет»).
    """
    try:
        import requests
        r = requests.post(f"{BRAIN}/forget", json={"meeting": key}, timeout=30)
        text = (r.json() or {}).get("text", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
        if r.status_code == 200:
            return text or f"забыто: {key}"
        return f"отказ ({r.status_code}): {text[:160]}"
    except Exception as e:  # noqa: BLE001 — brain выключен или не отвечает
        return (f"недоступна ({type(e).__name__}) — факты встречи {key} остались; "
                f"повторить, когда brain поднимется: curl -X POST {BRAIN}/forget "
                f"-H 'content-type: application/json' -d '{{\"meeting\":\"{key}\"}}'")


def main() -> int:
    # Как все точки записи конвейера: новые файлы (копии в .forget_backup,
    # временные .tmp) — только владельцу. Скрипт запускает и кнопка «Забыть»
    # в приложении, а его umask — 022 из Finder.
    charoite_paths.harden_umask()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="дата ГГГГ-ММ-ДД или штамп ГГГГ-ММ-ДД_ЧЧММ")
    ap.add_argument("--yes", action="store_true", help="выполнить (без него — только план)")
    ap.add_argument("--graph", type=pathlib.Path, default=None,
                    help="конкретный граф (по умолчанию — все графы vault)")
    ap.add_argument("--import-folder", type=pathlib.Path, default=None,
                    help="папка импорта приложения: копия аудио в done/ уходит вместе со встречей")
    ap.add_argument("--keep-graph", action="store_true",
                    help="только стенограмма и запись; граф не трогать")
    args = ap.parse_args()

    graph = args.graph.expanduser() if args.graph else None
    found = resolve(args.target, ROOT, graph)
    if not found:
        print(f"встреча «{args.target}» не найдена. Известные: "
              + (", ".join(stamps(ROOT, graph)[-5:]) or "ни одной"))
        return 1
    if len(found) > 1 and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.target):
        print(f"неоднозначно: {', '.join(found)}")
        return 1

    if len(found) > 1:
        print(f"за {args.target} встреч несколько: {', '.join(found)}\n")
    done = False
    for stamp in found:
        done |= apply(plan(stamp, ROOT, graph, keep_graph=args.keep_graph,
                           import_folder=args.import_folder), yes=args.yes)
        print()
    return 0 if (done or not args.yes) else 1


if __name__ == "__main__":
    sys.exit(main())
