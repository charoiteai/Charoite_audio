#!/usr/bin/env python3
"""Контролируемый запуск облачного разбора встречи: с таймаутом и границами.

Раньше `graph_updater` запускал `claude` фоном и на этом заканчивал. Сообщение
«запущен фоном» означало ровно одно: процесс стартовал. Упал он, упёрся в лимит
или ответил обрывком — в папке встречи оставался пустой или недописанный файл
ревизии, с виду настоящий (CHR-AUD-004). А в режиме записи модель правила граф
напрямую, и бэкап с границами существовали только в тексте промпта, то есть
держались на её послушании (CHR-AUD-003).

Этот воркер запускается фоном вместо самого `claude` и доводит дело до конца:

    ждёт процесс с таймаутом → проверяет код возврата и то, что ответ похож на
    ревизию → публикует файл атомарно;
    в режиме записи снимает бэкап графа ДО запуска и после сверяет, что тронуто
    только разрешённое, а нарушения откатывает.

Границы узкие намеренно. Облако дообогащает граф: узлы, ядра, заметки встреч.
Стенограммы, минутки и раздел «## Правки автора» неприкосновенны — это то, что
написал человек или записала машина с его слов.

    .venv/bin/python scripts/cloud_review.py --stamp 2026-07-15_1400 \\
        --transcript transcripts/2026-07-15_1400.md --graph ~/Vault/Работа \\
        --rev transcripts/2026-07-15_1400_ревизия_claude.md
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import fcntl
import functools
import hashlib
import os
import re
import pathlib
import shutil
import subprocess
import sys
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import charoite_paths  # noqa: E402
import cloud  # noqa: E402
import graph_updater  # noqa: E402
import privacy  # noqa: E402

BACKUP_DIR = ".cloud_backup"
# Снимков держим ровно один — срез ТЕКУЩЕЙ правки (решение владельца 21.08:
# «хранить 1 срез»; десять полных копий графа не пригодились ни разу, а
# весили 1.7 ГБ и 48K файлов). Ротация — в backup_graph, без констант.
TIMEOUT = 30 * 60           # разбор длинной встречи идёт минуты, но не часы
MIN_REPORT = 60             # страховка от «ok» и пустой строки
# Замок графа: второй воркер того же графа (встречи ближе TIMEOUT) ждёт
# первого, а не ротирует его снимок; не дождался — работает на чтение.
LOCK_WAIT = TIMEOUT + 5 * 60
LOCK_POLL = 5
# Карантин: всё, что сверка убирает из графа, сначала копируется сюда —
# unlink'а у сверки больше нет (карточки №40 и №88). Держим десять последних.
QUARANTINE_KIND = "cloud_quarantine"
QUARANTINE_KEEP = 10
# Переписанный заново файл — нарушение: облако дообогащает узлы, а не
# сочиняет их заново. Порог — доля строк старого текста, уцелевших в новом;
# короткие файлы не меряем (там и одна правка — половина текста).
REWRITE_MIN_LINES = 6
REWRITE_KEEP = 1 / 3
# Единственная допустимая форма «удаления» — заглушка-перенаправление,
# как у tier3 при слиянии дублей: `# Имя → [[Папка/Канон]]` в первой строке
# заголовка. Сам файл остаётся, ссылки на него не ломаются.
_REDIRECT_RE = re.compile(r"^# .+? → \[\[[^\]]+\]\]", re.M)

# Что облако не трогает никогда, даже находясь внутри графа. Архив встречи —
# та же категория, что копии стенограмм: Саммари, Минутки, Стенограмма суфлёра
# и документы, разложенные конвейером. Докстринг выше обещал это всегда, а
# граница покрывала только одну папку из двух.
PROTECTED_DIRS = ("Документация/Стенограммы встреч", "Встречи-архив")
PROTECTED_SECTION = "## Правки автора"


def may_write(path: pathlib.Path, graph: pathlib.Path) -> bool:
    """Можно ли облаку менять этот файл.

    Внутри графа — да, кроме копий стенограмм: их кладёт конвейер, и правка
    там означала бы переписанную запись разговора. Вне графа — никогда:
    конфиг проекта, дневник по соседству и всё остальное облаку не
    принадлежит.
    """
    try:
        rel = path.resolve().relative_to(graph.resolve())
    except (ValueError, OSError):
        return False
    # Скрытые каталоги графа — снимки (.cloud_backup, .forget_backup,
    # Ядра/.tier3_backup) и служебное Obsidian (.obsidian, .trash). Снимок и
    # откат их не видят (snapshot пропускает dot-пути), значит и писать туда
    # облаку нельзя: правка бэкап-истории была бы незаметной и необратимой
    # (аудит DeepSeek 16.08).
    if any(part.startswith(".") for part in rel.parts):
        return False
    as_posix = rel.as_posix()
    return not any(as_posix.startswith(p) for p in PROTECTED_DIRS)


def author_section_changed(before: str, after: str) -> bool:
    """Тронут ли раздел «## Правки автора» — то, что человек писал руками."""
    def tail(text: str) -> str:
        _, sep, rest = text.partition(PROTECTED_SECTION)
        return rest if sep else ""
    return tail(before).strip() != tail(after).strip()


def is_redirect_stub(text: str) -> bool:
    """Заглушка после слияния: короткий файл, чей заголовок — стрелка на канон."""
    body = (text or "").strip()
    return bool(body) and len(body) <= 1200 and _REDIRECT_RE.search(body) is not None


def retention(old: str, new: str) -> float:
    """Какая доля содержательных строк старого текста уцелела в новом.

    Меряем по строкам, а не по знакам: правка статуса меняет одну строку,
    дописанные факты — добавляют, и обе оставляют старые строки на месте.
    Переписанный заново узел — это когда старых строк почти не осталось.
    """
    before = [ln.strip() for ln in old.splitlines() if ln.strip()]
    if not before:
        return 1.0
    after = {ln.strip() for ln in new.splitlines() if ln.strip()}
    return sum(1 for ln in before if ln in after) / len(before)


def _digest(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def graph_files(graph: pathlib.Path):
    """Все файлы графа, которые сторожит сверка, — скрытые ТОЖЕ.

    Раньше dot-пути (.obsidian, .forget_backup, Ядра/.tier3_backup,
    Досье/.backup) пропускались и снимком, и бэкапом, тогда как `Edit(/**)`
    их не исключает: правка там была невидимой и необратимой — ровно то,
    что may_write обещал не допускать (Codex, Critical 22.08). Два
    исключения: наш же старый снимок внутри графа (`.cloud_backup/`, до
    переезда 21.08) — десятки тысяч собственных копий, а не граф; и живое
    состояние окон Obsidian (`.obsidian/workspace*.json`) — его переписывает
    сам Obsidian каждую минуту, пока открыт, и «откат» такого файла был бы
    откатом чужой работы на каждом разборе. Плагины и настройки в
    `.obsidian/` под охраной остаются.
    """
    for p in graph.rglob("*"):
        if not p.is_file() or BACKUP_DIR in p.parts:
            continue
        if p.parent.name == ".obsidian" and p.name.startswith("workspace"):
            continue
        yield p


def snapshot(graph: pathlib.Path) -> dict[str, str]:
    """Хеши всех файлов графа. Дёшево: граф это текст.

    Именно всех, а не только *.md: граница «что тронуло облако» обязана
    видеть любой файл, иначе .txt или .canvas меняются и создаются мимо
    контроля — снимок по расширению охранял бы не граф, а формат.
    """
    return {str(p.resolve()): _digest(p) for p in graph_files(graph)}


def changed_since(before: dict[str, str], graph: pathlib.Path) -> list[pathlib.Path]:
    """Что изменилось, появилось или ИСЧЕЗЛО после запуска облака.

    Удалённые — обязательно: diff только по текущим файлам делает удаление
    невидимым, то есть стереть узел облаку было «можно» просто потому, что
    сравнивать становилось нечего.
    """
    now = snapshot(graph)
    touched = [pathlib.Path(k) for k, v in now.items() if before.get(k) != v]
    touched += [pathlib.Path(k) for k in before if k not in now]
    return touched


def backup_root(graph: pathlib.Path) -> pathlib.Path:
    """Каталог со снимками этого графа — в данных Чароита, не в графе."""
    # root — корень ДАННЫХ этой установки (CHAROITE_ROOT), а не папка кода:
    # у вложенной установки они разные, и снимки обязаны лечь к данным.
    return charoite_paths.graph_backups(graph, BACKUP_DIR.lstrip("."), root=ROOT)


def _clone(src: pathlib.Path, dst: pathlib.Path) -> bool:
    """Скопировать файл клоном APFS: copy-on-write, ноль байт на диске.

    Снимок берётся с графа целиком, а меняет облако единицы файлов — то есть
    девять десятых каждой копии байт в байт повторяют предыдущую. На APFS за
    это платить не нужно: `clonefile` делает независимый файл, который делит
    блоки с оригиналом, пока кто-то из двоих не изменится. Правка графа идёт
    через `tmp.replace()`, то есть создаёт новый inode, — снимок она не
    трогает даже теоретически. Жёсткие ссылки такой гарантии не дают: они бы
    протекли, запиши кто-нибудь заметку на месте.

    False — клон не вышел (не APFS, другой том, старая система): вызывающий
    делает обычную копию.
    """
    try:
        rc = _libsystem().clonefile(os.fsencode(str(src)), os.fsencode(str(dst)), 0)
    except (OSError, AttributeError):
        return False
    return rc == 0


@functools.lru_cache(maxsize=1)
def _libsystem():
    """libSystem с clonefile; None-безопасно — AttributeError поймает _clone."""
    return ctypes.CDLL("libSystem.dylib", use_errno=True)


def backup_graph(graph: pathlib.Path, stamp: str) -> pathlib.Path:
    """Копия файлов графа перед правкой. Обещание PRIVACY — кодом.

    Снимок лежит ВНЕ графа: граф живёт в iCloud, и полная копия на каждую
    правку превращалась в десятки тысяч файлов, которые система гоняла в
    облако вместо того, чтобы отдать процессор живой записи (21.08).
    """
    root = charoite_paths.secure_dir(backup_root(graph))
    dest = root / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for p in graph_files(graph):
        target = dest / p.resolve().relative_to(graph.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _clone(p, target):
            shutil.copy2(p, target)
    return dest


def rotate_snapshots(root: pathlib.Path, keep: pathlib.Path) -> None:
    """Один срез: удалить все снимки, кроме своего. Зовётся В КОНЦЕ run().

    В backup_graph ротации больше нет намеренно: два воркера одного графа
    (встречи ближе 30-минутного TIMEOUT) иначе съедали снимки друг друга —
    сортировка по имени убивала свежий при штампе новее, «все кроме dest» —
    чужой живой (круг-1 и круг-2 по PR #363: GLM + DeepSeek). Пока сверка
    границ воркера не закончилась, его снимок не трогает никто, включая
    соседей: каждый ротирует только ПОСЛЕ собственного enforce, а с замком
    графа (graph_lock) соседи вообще не пересекаются во времени.
    """
    if not root.is_dir():
        return
    for stale in root.iterdir():
        if stale == keep or not stale.is_dir():
            continue        # чужие файлы в каталоге — не наши, не трогаем
        shutil.rmtree(stale, ignore_errors=True)


def restore(path: pathlib.Path, graph: pathlib.Path, backup: pathlib.Path) -> bool:
    """Вернуть файл из бэкапа. False — копии не было (файл создан облаком)."""
    try:
        source = backup / path.resolve().relative_to(graph.resolve())
    except (ValueError, OSError):
        return False
    if not source.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, path)
    return True


def quarantine_root(graph: pathlib.Path) -> pathlib.Path:
    """Карантин — рядом со снимками, вне графа, те же права 0700."""
    return charoite_paths.graph_backups(graph, QUARANTINE_KIND, root=ROOT)


def quarantine(path: pathlib.Path, graph: pathlib.Path, qdir: pathlib.Path, *,
               move: bool) -> None:
    """Отложить версию облака в карантин: копией (файл будет восстановлен
    из бэкапа) или переносом (файла до запуска не было — убрать, не стирая).

    Сверка больше ничего не удаляет безвозвратно: её «нарушение» может быть
    и правкой человека, сделанной за те же полчаса (карточка №40), и
    полезной правкой, вернувшейся с обрывком ответа. Всё лежит в карантине,
    и человек решает сам.
    """
    try:
        rel = path.resolve().relative_to(graph.resolve())
    except (ValueError, OSError):
        rel = pathlib.Path(path.name)
    target = qdir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(path), str(target))
    else:
        shutil.copy2(path, target)


def rotate_quarantine(root: pathlib.Path, keep: int = QUARANTINE_KEEP) -> None:
    """Карантин не растёт бесконечно: десять последних запусков по штампу."""
    if not root.is_dir():
        return
    runs = sorted(p for p in root.iterdir() if p.is_dir())
    for stale in runs[:-keep] if keep > 0 else runs:
        shutil.rmtree(stale, ignore_errors=True)


@contextlib.contextmanager
def graph_lock(graph: pathlib.Path, wait: float | None = None):
    """Один воркер на граф: от снимка до ротации.

    Без замка два разбора одного графа (встречи ближе TIMEOUT) ротировали
    снимки друг друга: второй оставался без копии и пропускал сверку, то
    есть любые правки проходили без проверки (Codex, Critical 22.08).
    flock на файле рядом со снимками — вне графа, вне iCloud; закрытие
    дескриптора снимает замок и при аварийном выходе. Даёт True, если замок
    взят, False — если за `wait` секунд сосед не освободил граф.
    """
    wait = LOCK_WAIT if wait is None else wait   # константу можно подменить в тестах
    try:
        base = charoite_paths.secure_dir(backup_root(graph).parent)
        fd = os.open(base / "cloud.lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        # нет права на каталог данных — как и без бэкапа: только чтение
        print(f"замок графа не взять ({e}) — работаю на чтение")
        yield False
        return
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(min(LOCK_POLL, max(0.0, deadline - time.monotonic())))
        yield True
    finally:
        os.close(fd)


@dataclasses.dataclass
class Verdict:
    """Что сверка сделала с правками облака. Все списки — имена файлов."""
    touched: int = 0                 # всего правок (−1 — снимка не было, не судили)
    reverted: list[str] = dataclasses.field(default_factory=list)   # запрещённая правка → из бэкапа
    removed: list[str] = dataclasses.field(default_factory=list)    # создан где нельзя → в карантин
    deleted: list[str] = dataclasses.field(default_factory=list)    # стёрт облаком → из бэкапа
    rewritten: list[str] = dataclasses.field(default_factory=list)  # переписан заново → из бэкапа
    rolled_back: bool = False        # ответ невалиден — откачено всё

    @property
    def violations(self) -> int:
        return len(self.reverted) + len(self.removed) + len(self.deleted) + len(self.rewritten)


def judge(path: pathlib.Path, graph: pathlib.Path, old_text: str, new_text: str,
          existed: bool) -> str | None:
    """Почему эту правку нельзя оставить — или None, если можно.

    Порядок важен: место (защищённая папка, скрытый путь, вне графа) →
    удаление → авторский раздел → переписывание заново. Удаление — нарушение
    всегда: узел без «## Правки автора» раньше стирался как «правка», а
    переименование и слияние выглядели как удаление плюс создание (Codex,
    Sonnet 22.08). Единственная форма «убрать» — заглушка-перенаправление.
    """
    if not may_write(path, graph):
        return "protected"
    if existed and not path.exists():
        return "deleted"
    if existed and author_section_changed(old_text, new_text):
        return "author"
    if (existed and not is_redirect_stub(new_text)
            and sum(1 for ln in old_text.splitlines() if ln.strip()) >= REWRITE_MIN_LINES
            and retention(old_text, new_text) < REWRITE_KEEP):
        return "rewritten"
    return None


def enforce_boundaries(before: dict[str, str], graph: pathlib.Path,
                       backup: pathlib.Path, qdir: pathlib.Path | None = None,
                       *, rollback: bool = False) -> Verdict:
    """Сверить граф с состоянием до запуска и убрать запрещённое.

    Нарушение — это не только правка существующего файла: удаление тоже
    правка, а файл, СОЗДАННЫЙ облаком в защищённой папке, не имеет копии в
    бэкапе — его нельзя откатить, но обязаны убрать, иначе запрет действует
    только на то, что существовало до запуска. Убранное и откаченное уходит
    в карантин `qdir` (копия версии облака), не в никуда.

    rollback=True — ответ облака невалиден (таймаут, обрывок, код ≠ 0):
    откатывается ВСЁ, включая разрешённые правки. Без отчёта они — правки
    неизвестной степени готовности: слияние могло дойти до середины.
    """
    # Снимок мог исчезнуть между созданием и сверкой (внешнее удаление —
    # с замком графа соседний воркер этого больше не делает): без копии
    # «откат» превращается в unlink. Не судим.
    if not backup.is_dir():
        return Verdict(touched=-1)
    v = Verdict(rolled_back=rollback)
    touched = changed_since(before, graph)
    v.touched = len(touched)
    qdir = qdir or quarantine_root(graph) / backup.name
    for path in touched:
        key = str(path.resolve()) if path.exists() else str(path)
        existed = key in before
        try:
            old = backup / path.resolve().relative_to(graph.resolve())
        except (ValueError, OSError):
            old = backup / path.name        # вне графа — старой копии нет
        old_text = (old.read_text(encoding="utf-8", errors="ignore")
                    if old.exists() else "")
        new_text = (path.read_text(encoding="utf-8", errors="ignore")
                    if path.exists() else "")
        why = judge(path, graph, old_text, new_text, existed)
        if why is None and not rollback:
            continue
        if path.exists():
            # версия облака — в карантин; при восстановлении её не станет
            quarantine(path, graph, qdir, move=not old.exists())
        if restore(path, graph, backup):
            {"deleted": v.deleted, "rewritten": v.rewritten}.get(why, v.reverted).append(path.name)
        elif not path.exists():
            v.removed.append(path.name)     # создан где нельзя — уже в карантине
    return v


def looks_like_report(text: str) -> bool:
    """Похоже ли это на ревизию, а не на сообщение об ошибке или обрывок.

    Критерий по СТРУКТУРЕ, а не по числу знаков: короткая, но настоящая
    ревизия бывает («две правки и всё»), а вот однострочник — это всегда либо
    ошибка CLI («Ошибка: rate limit»), либо обрывок. Ревизия — перечень:
    минимум три непустые строки и хотя бы два пункта или заголовок.
    """
    body = (text or "").strip()
    if len(body) < MIN_REPORT:
        return False
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    bullets = sum(1 for ln in lines if ln.lstrip().startswith(("-", "*", "•")))
    return bullets >= 2 or any(ln.startswith("#") for ln in lines)


def publish(tmp: pathlib.Path, rev: pathlib.Path, ok: bool) -> bool:
    """Атомарная публикация. Не удалось — текст сохраняем как .partial.

    Пустой или недописанный файл на месте ревизии хуже отсутствия файла: он
    выглядит как готовый ответ, и человек читает обрывок, не зная об этом.
    """
    if not ok:
        if tmp.exists():
            tmp.replace(rev.with_suffix(rev.suffix + ".partial"))
        return False
    tmp.replace(rev)
    return True


def deliver_review(rev: pathlib.Path, transcript: pathlib.Path, graph: pathlib.Path,
                   stamp: str, lf) -> None:
    """Довезти ревизию туда, где человек её увидит: архив встречи и vault.

    Воркер публикует `<стем>_ревизия_claude.md` ПОЗЖЕ архива и копий в
    Документацию, а повторно их никто не собирал — в read-only режиме вывод
    облака оставался в служебном transcripts/, невидимом ни в Finder-архиве,
    ни в графе (аудит GLM 17.08). Архив идемпотентен: зовём его ещё раз с тем
    же ключом файлов, копию в Документацию кладём рядом с остальными.
    """
    try:
        from meeting_archive import archive_meeting
        slug = transcript.stem[len(stamp):].lstrip("_") if transcript.stem.startswith(stamp) else ""
        if slug[:1].isdigit():
            slug = ""   # остаток «30» у посекундного стема — секунды, не тема (ревью 17.08)
        folder = archive_meeting(graph, transcript.parent, stamp, slug.replace("_", " "),
                                 files_key=transcript.stem)
        # Имя ревизии строится от минутного штампа, а ключ файлов архива — от
        # стема стенограммы (у посекундной без темы они расходятся): кладём
        # копию в папку явно, а не надеемся на глоб.
        if folder is not None:
            shutil.copy2(rev, folder / "Ревизия Claude.md")
        vdocs = graph / "Документация" / "Стенограммы встреч"
        if vdocs.is_dir():
            shutil.copy2(rev, vdocs / rev.name)
        lf.write(f"[cloud-review] ревизия доставлена: архив {folder.name if folder else '—'}"
                 f"{', vault' if vdocs.is_dir() else ''}\n")
    except Exception as e:  # noqa: BLE001 — доставка не важнее самой ревизии
        lf.write(f"[cloud-review] ревизия не доставлена в архив: {e}\n")


def run(stamp: str, transcript: pathlib.Path, graph: pathlib.Path,
        rev: pathlib.Path, log: pathlib.Path, cfg: dict) -> int:
    # Разрешение спрашиваем ЗДЕСЬ, а не полагаемся на вызывающего: воркер —
    # отдельный процесс, и запустить его можно руками. Рубильник
    # CHAROITE_NO_CLOUD действует и на этом пути.
    if not privacy.cloud_enrich_enabled(cfg):
        print("облако выключено рубильником или конфигом — разбор не запускается")
        return 1
    graph_available = graph_updater.cloud_graph_available(graph)
    # Право править имеет смысл только вместе с узкой cwd=graph. При
    # отсутствующем/слишком широком графе команда уйдёт в text-only режим:
    # папка стенограмм не должна случайно получить файловые инструменты.
    may_edit = privacy.cloud_edit_graph_enabled(cfg) and graph_available
    # Право правки живо только вместе со страховкой: нет каталога графа
    # (несмонтированный iCloud-том) или не получился бэкап — режим чтения.
    # Раньше бэкап тихо пропускался, а Edit/Write всё равно выдавались —
    # ровно то, чего PRIVACY обещает не допускать (ревью 15.08). Понижаем
    # ДО сборки промпта и команды: и задание, и права должны совпадать.
    if may_edit and not graph.is_dir():
        print("право правки есть, а каталога графа нет — бэкап невозможен, "
              "работаю на чтение")
        may_edit = False
    # Замок графа держится от снимка до ротации: второй воркер того же
    # графа ждёт, а не ротирует чужой живой снимок. На чтение замок не нужен.
    with (graph_lock(graph) if may_edit else contextlib.nullcontext(True)) as locked:
        if may_edit and not locked:
            print(f"граф занят другим разбором дольше {LOCK_WAIT // 60} мин — "
                  "работаю на чтение")
            may_edit = False
        return _run_locked(stamp, transcript, graph, rev, log, cfg,
                           may_edit=may_edit, graph_available=graph_available)


def _run_locked(stamp: str, transcript: pathlib.Path, graph: pathlib.Path,
                rev: pathlib.Path, log: pathlib.Path, cfg: dict, *,
                may_edit: bool, graph_available: bool) -> int:
    # Выход в сеть — здесь, и рубильник спрашивается на самом выходе
    # (контракт tests/test_cloud_call_sites.py), а не только в run() выше.
    if not privacy.cloud_enrich_enabled(cfg):
        return 1
    before, backup = {}, None
    if may_edit:
        before = snapshot(graph)
        try:
            backup = backup_graph(graph, stamp)
        except OSError as e:
            print(f"бэкап графа не удался ({e}) — работаю на чтение")
            may_edit = False
    # Файлы встречи — по стему её стенограммы, а не по минутному штампу:
    # посекундная соседка той же минуты в контекст не попадает (аудит 16.08).
    context, sent = graph_updater.cloud_enrich_context(transcript.parent, transcript.stem)
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name=transcript.name, folder=transcript.parent, graph=graph,
        rev_name=rev.name, stamp=stamp, arch_folder=None, may_edit=may_edit,
        context=context)
    cmd = graph_updater.cloud_enrich_command(
        cfg, claude_bin=shutil.which("claude") or "/opt/homebrew/bin/claude",
        prompt=prompt, model=cloud.model(cfg, "cloud_model"), may_edit=may_edit,
        graph_available=graph_available)

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Прокси из settings.json — иначе из GUI-запуска без shell-окружения
    # `claude` идёт напрямую и получает «403 Request not allowed» (регион):
    # 21.08 так упали все разборы дня, пока демон с прокси работал.
    cloud.add_proxy(env)

    tmp = rev.with_suffix(rev.suffix + ".part")
    work_dir = graph_updater.cloud_enrich_workdir(cfg, graph, transcript.parent)
    mode = ("правка графа" if may_edit else
            "только чтение графа" if graph_available else
            "только текст (граф недоступен)")
    with tmp.open("w", encoding="utf-8") as out, log.open("a", encoding="utf-8") as lf:
        lf.write(f"[cloud-review] {stamp}: файлов в запросе {len(sent)} "
                 f"({', '.join(sent)}), {len(context)} знаков, "
                 f"режим {mode}\n")
        lf.flush()
        try:
            code = subprocess.run(cmd, cwd=str(work_dir), env=env,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=lf,
                                  timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            lf.write(f"[cloud-review] таймаут {TIMEOUT}с — разбор прерван\n")
            code = -1

    text = tmp.read_text(encoding="utf-8") if tmp.exists() else ""
    ok = code == 0 and looks_like_report(text)
    published = publish(tmp, rev, ok)

    with log.open("a", encoding="utf-8") as lf:
        if published:
            lf.write(f"[cloud-review] ревизия сохранена: {rev.name}\n")
        else:
            lf.write(f"[cloud-review] ревизия НЕ сохранена (код {code}, "
                     f"{len(text)} знаков) — см. {rev.name}.partial\n")
        if may_edit and backup is not None and not backup.exists():
            # Снимок исчез — с замком графа так может только внешняя рука.
            # Без копии enforce не откатывает, а УДАЛЯЕТ: restore видит
            # пустоту. Честнее не трогать файлы вовсе и сказать об этом громко.
            lf.write("[cloud-review] СНИМОК ИСЧЕЗ — границы не сверяю, "
                     "файлы не трогаю; проверь правки руками\n")
        elif may_edit and backup is not None:
            qdir = quarantine_root(graph) / stamp
            # Невалидный ответ — откат всего: без отчёта правки графа —
            # неизвестной степени готовности (слияние до середины).
            v = enforce_boundaries(before, graph, backup, qdir, rollback=not ok)
            lf.write(_verdict_line(v, qdir))
            if qdir.is_dir():
                rotate_quarantine(quarantine_root(graph))
        # Доставка — ПОСЛЕ сверки границ: архив и Документация — защищённые
        # папки, и созданную здесь копию сверка приняла бы за правку облака.
        if published:
            deliver_review(rev, transcript, graph, stamp, lf)
        if backup is not None:
            # ротация — самым последним: свой снимок жил до конца сверки
            rotate_snapshots(backup_root(graph), keep=backup)
    return 0 if published else 1


def _verdict_line(v: Verdict, qdir: pathlib.Path) -> str:
    """Одна строка лога про сверку — с отдельными словами для удаления и
    переписывания: «правок графа: 3» не говорило, что два узла стёрты."""
    if v.touched < 0:
        return "[cloud-review] снимка нет — границы не сверялись\n"
    if v.rolled_back:
        return (f"[cloud-review] ответ невалиден — все правки графа ({v.touched}) "
                f"откачены, копии облака в карантине {qdir}\n")
    parts = [f"[cloud-review] правок графа: {v.touched}"]
    if v.reverted:
        parts.append(f"откатано запрещённых: {', '.join(v.reverted)}")
    if v.deleted:
        parts.append(f"УДАЛЕНО облаком, восстановлено: {', '.join(v.deleted)}")
    if v.rewritten:
        parts.append(f"переписано заново, возвращено: {', '.join(v.rewritten)}")
    if v.removed:
        parts.append(f"созданных в защищённых — в карантин: {', '.join(v.removed)}")
    if v.violations:
        parts.append(f"версии облака в карантине {qdir}")
    return ", ".join(parts) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--transcript", type=pathlib.Path, required=True)
    ap.add_argument("--graph", type=pathlib.Path, required=True)
    ap.add_argument("--rev", type=pathlib.Path, required=True)
    ap.add_argument("--log", type=pathlib.Path, required=True)
    args = ap.parse_args()
    cfg = graph_updater.load_cfg()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    return run(args.stamp, args.transcript, args.graph, args.rev, args.log, cfg)


if __name__ == "__main__":
    sys.exit(main())
