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
    # п.1): тема и решения встречи уходят в память Чароита (brain :8100,
    # /remember), а /forget у неё нет.
    beyond_reach: list[str] = dataclasses.field(default_factory=list)

    def describe(self) -> str:
        out = [f"Встреча {self.stamp}"]
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


_STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{4,6})(?!\d)")
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
    found = set(_with_stamp(status_dir, stamp, suffix=".json"))
    for f in status_dir.glob("*.json"):
        if f in found:
            continue
        try:
            tp = json.loads(f.read_text(encoding="utf-8")).get("transcript_path", "")
        except (OSError, ValueError, AttributeError):
            continue
        name = pathlib.Path(str(tp)).name
        rest = name[len(stamp):]
        if name.startswith(stamp) and not rest[:1].isdigit():
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
    return sorted(s for s in found if re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4,6}", s))


def resolve(target: str, root: pathlib.Path,
            graph: pathlib.Path | None = None) -> list[str]:
    """Дата или штамп → список штампов встреч. Пусто — такой встречи нет."""
    target = target.strip().rstrip("/")
    known = stamps(root, graph)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", target):
        return [s for s in known if s.startswith(target)]
    return [s for s in known if s == target]


def _archive_folders(g: pathlib.Path, stamp: str) -> list[pathlib.Path]:
    """Папка встречи в архиве: имя несёт дату и время, а форматов было три."""
    arch = g / ARCHIVE_DIR
    if not arch.is_dir():
        return []
    day, hhmm = stamp[:10], f"{stamp[11:13]}-{stamp[13:15]}"
    out = []
    for d in sorted(arch.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        # «2026-07-15 14-00 — Тема» (текущий), «2026-07-15_1400 — Тема» и
        # «2026-07-15 — Тема» (наследие meeting_archive.py)
        if d.name.startswith(f"{day} {hhmm} ") or d.name.startswith(f"{stamp} "):
            out.append(d)
        elif d.name.startswith(f"{day} —") and not _other_time(arch, day, hhmm):
            out.append(d)
    return out


def _other_time(arch: pathlib.Path, day: str, hhmm: str) -> bool:
    """Есть ли в архиве папка этого дня с ДРУГИМ временем.

    Старый формат «дата — тема» не различает встречи внутри дня, поэтому
    трогать его можно только когда сомнений нет: одна встреча за день.
    """
    return any(d.is_dir() and d.name.startswith(f"{day} ") and not d.name.startswith(f"{day} {hhmm} ")
               for d in arch.iterdir())


def plan(stamp: str, root: pathlib.Path,
         graph: pathlib.Path | None = None, keep_graph: bool = False) -> Plan:
    """Собрать план: что удалить, что переписать. Ничего не меняет."""
    p = Plan(stamp=stamp)

    for folder in ("transcripts", "recordings"):
        p.delete += _with_stamp(root / folder, stamp)

    # Логи графа этой встречи: в logs/graph_<штамп>*.log попадают имена
    # участников и куски цитат — «забыть» обязано дойти и до них, иначе
    # содержимое встречи переживает саму встречу (аудит 0.46.0: «забыть»
    # не доходит до логов). Исходник в папке импорта done/ сюда не входит:
    # её путь знает только вызов --scan, у скрипта его нет — см. README.
    logs = root / "logs"
    # Все три класса логов названы МИНУТНЫМ штампом (daemon: `stem[:15]`,
    # graph_updater: parse_stem, rebuild: `stem[:15]`), а штамп посекундной
    # встречи без темы — с секундами: по нему логи не находились и переживали
    # забывание (второе мнение DeepSeek по партии 16.08). Две встречи одной
    # минуты пишут в один и тот же лог — он общий, и удалить его при
    # забывании любой из них честнее, чем оставить.
    log_stamp = stamp[:15]
    p.delete += _with_stamp(logs, log_stamp, prefix="graph_", suffix=".log")
    # Лог облачной ревизии называется иначе и потому переживал забывание:
    # внутри — имена файлов встречи (а тема встречи стоит в имени),
    # счётчики и stderr CLI (аудит 16.08).
    p.delete += _with_stamp(logs, log_stamp, prefix="cloud_review_", suffix=".log")
    # Лог повторной пересборки (retry_<штамп>.log): stdout rebuild_transcript
    # с маппингом имён участников и темой — третий класс, который ни ретеншн,
    # ни «забыть» не видели (аудит DeepSeek 16.08).
    p.delete += _with_stamp(logs, log_stamp, prefix="retry_", suffix=".log")
    # Отметка «факты встречи отправлены в память Чароита» (graph_updater):
    # без неё повторный разбор той же встречи после забывания молчал бы.
    sent = _with_stamp(logs / "brain_sent", log_stamp, suffix=".txt")
    p.delete += sent
    if sent:
        # Сами факты (тема, участники, решения) живут в памяти-компаньоне
        # (brain :8100), у которой нет /forget: сказать вслух, а не делать
        # вид, что забыто всё (аудит 16.08, п.1; как для iCloud в PRIVACY).
        p.beyond_reach.append(
            "память Чароита (brain :8100): тема, участники и решения встречи "
            "отправлены туда при разборе; /forget у неё нет — чистить её "
            "инструментами самой памяти")
    # Статус конвейера (logs/meeting-status/<стенограмма>.json): путь к
    # стенограмме — с темой в имени, этап, текст ошибки; его же читает
    # список «Недавние встречи». Чистится сам через 14 дней, но «забыть»
    # обязано дойти сразу (второе мнение по #324–#328, 16.08).
    p.delete += _status_files(root / STATUS_DIR, stamp)

    if keep_graph:
        return p

    link = _link_re(stamp)
    for g in _graph_roots(graph):
        node = g / MEETINGS_DIR / f"{stamp}.md"
        if node.exists():
            p.delete.append(node)
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
    print("Что осталось вне досягаемости: копии в iCloud и бэкапах Time Machine,"
          " файлы у других участников — см. PRIVACY.md")
    for line in p.beyond_reach:
        print(f"  и ещё: {line}")
    return True


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
        done |= apply(plan(stamp, ROOT, graph, keep_graph=args.keep_graph), yes=args.yes)
        print()
    return 0 if (done or not args.yes) else 1


if __name__ == "__main__":
    sys.exit(main())
