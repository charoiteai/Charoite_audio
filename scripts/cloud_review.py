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
import collections
import contextlib
import ctypes
import dataclasses
import datetime as dt
import functools
import hashlib
import io
import os
import re
import pathlib
import shutil
import subprocess
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import charoite_paths  # noqa: E402
import safe_write  # noqa: E402
import cloud  # noqa: E402
import file_locks  # noqa: E402
import graph_updater
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
# Сколько path-rules запрета влезает в команду: граф с тысячей симлинков —
# не граф, а чужая файловая система; такому — только чтение.
DENY_MAX = 200
# Переписанный заново файл — нарушение: облако дообогащает узлы, а не
# сочиняет их заново. Порог — доля строк старого текста, уцелевших в новом;
# короткие файлы не меряем (там и одна правка — половина текста).
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


REWRITE_MIN_LINES = 6
REWRITE_KEEP = 1 / 3
def is_redirect_stub(text: str) -> bool:
    """Заглушка после слияния: короткий файл, чей ПЕРВЫЙ заголовок (после
    frontmatter) — стрелка на канон. Стрелка где-то в середине переписанного
    узла заглушкой не делает (круг-1 по PR #381, Codex + DeepSeek)."""
    body = (text or "").replace("\r\n", "\n").strip()
    if not body or len(body) > 1200:
        return False
    body = re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.S)   # frontmatter
    # перед заголовком допустимы только пустые строки и HTML-комментарии:
    # проза или код-фенс перед стрелкой — уже не заглушка (круг-3 по #381)
    body = re.sub(r"\A(?:\s*<!--.*?-->\s*)*", "", body, flags=re.S).lstrip()
    first = body.split("\n", 1)[0].strip()
    return _REDIRECT_RE.fullmatch(first) is not None

def retention(old: str, new: str) -> float:
    """Какая доля содержательных строк старого текста уцелела в новом.

    Меряем по строкам, а не по знакам: правка статуса меняет одну строку,
    дописанные факты — добавляют, и обе оставляют старые строки на месте.
    Переписанный заново узел — это когда старых строк почти не осталось.
    Строки сравниваются без разметки (смена формата — не переписывание) и
    с кратностью (одна уцелевшая строка не засчитывается за четыре
    одинаковых старых).
    """
    before = collections.Counter(n for ln in old.splitlines() if (n := _norm(ln)))
    if not before:
        return 1.0
    after = collections.Counter(n for ln in new.splitlines() if (n := _norm(ln)))
    kept = sum((before & after).values())
    return kept / sum(before.values())


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
    # Граница сегмента обязательна: «Встречи-архив-2023/» и
    # «Встречи-архив.md» — не защищённая папка, а голый startswith объявлял
    # их ею и откатывал законную правку в карантин (аудит облака, DS M3).
    return not any(as_posix == p.rstrip("/") or as_posix.startswith(p.rstrip("/") + "/")
                   for p in PROTECTED_DIRS)


def author_section_changed(before: str, after: str) -> bool:
    """Тронут ли раздел «## Правки автора» — то, что человек писал руками."""
    def tail(text: str) -> str:
        _, sep, rest = text.partition(PROTECTED_SECTION)
        return rest if sep else ""
    return tail(before).strip() != tail(after).strip()


def _norm(line: str) -> str:
    """Строка без разметки и пунктуации: «Роль: аналитик» и «- **Роль:**
    аналитик» — одно и то же содержание, а не переписывание."""
    return " ".join(re.sub(r"[\W_]+", " ", line.lower()).split())


def _digest(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# Внутри `.obsidian` под охраной только то, что исполняется или включает
# исполняемое: плагины, CSS-сниппеты и списки включённых плагинов.
# Остальное — состояние окон и настроек, которое сам Obsidian переписывает,
# пока открыт: откат такого файла на каждом разборе был бы откатом чужой
# работы (круг-1 по PR #381, DeepSeek).
#
# `data.json` плагина был исключением по той же причине — Obsidian пишет
# туда сам. Исключение снято (GLM, круг-12): у Templater в data.json лежит
# папка стартовых шаблонов, то есть это настройка, которая ИСПОЛНЯЕТСЯ при
# запуске. Файл был невидим для снимка целиком — ни отката, ни строки в
# логе. Шум от собственных правок Obsidian здесь дешевле пропущенного
# запуска чужого JS.
_OBSIDIAN_GUARDED = ("community-plugins.json", "core-plugins.json")  # сравнение через casefold


def obsidian_guarded(rel: pathlib.Path) -> bool:
    # casefold на всех сравнениях: каталог, созданный как `.obsidian/Plugins`,
    # на APFS ложится в тот же `plugins`, но в обходе приходит своим именем —
    # и регистрозависимый фильтр выбрасывал бы его из снимка ещё до проверки
    # зоны исполнения (Codex, круг-12).
    parts = tuple(p.casefold() for p in rel.parts)
    if len(parts) < 2 or parts[0] != ".obsidian":
        return True
    if parts[1] == "plugins":
        return True
    return parts[1] == "snippets" or (
        len(parts) == 2 and rel.name.casefold() in _OBSIDIAN_GUARDED)


def graph_files(graph: pathlib.Path):
    """Все файлы графа, которые сторожит сверка, — скрытые ТОЖЕ.

    Раньше dot-пути (.obsidian, .forget_backup, Ядра/.tier3_backup,
    Досье/.backup) пропускались и снимком, и бэкапом, тогда как `Edit(/**)`
    их не исключает: правка там была невидимой и необратимой — ровно то,
    что may_write обещал не допускать (Codex, Critical 22.08). Исключения:
    наш же старый снимок внутри графа (`.cloud_backup/`, до переезда 21.08)
    — десятки тысяч собственных копий, а не граф; живое состояние Obsidian
    (см. obsidian_guarded); и симлинки — цель может лежать вне графа, и
    «откат» такого файла был бы правкой чужого места. Всё исключённое здесь
    закрыто для записи первым слоем — deny-правилами CLI (deny_paths).
    """
    root = graph.resolve()
    for p in graph.rglob("*"):
        if p.is_symlink() or not p.is_file() or BACKUP_DIR in p.parts:
            continue
        try:
            rel = p.resolve().relative_to(root)
        except (ValueError, OSError):
            continue                       # вышел за граф (симлинк выше по пути)
        if not obsidian_guarded(rel):
            continue
        yield p


def deny_paths(graph: pathlib.Path, *,
               symlinks_only: bool = False) -> list[tuple[str, bool]]:
    """Что закрыть правилами CLI: (относительный путь, каталог?).

    `symlinks_only=True` — только симлинки: им закрывают и ЧТЕНИЕ, потому
    что цель лежит вне графа, а инъекция из стенограммы попросит именно
    такой путь. Живая проверка 26.08 (claude 2.x): CLI сам резолвит симлинк
    и под `dontAsk` отклоняет чтение наружу даже с `Read(/**)` — правило
    ниже второй пояс, чтобы граница не держалась на одном лишь поведении
    внешнего CLI (аудит облака 26.08, DS I1: находка не воспроизвелась,
    страховка осталась).

    Защищённые папки; каждый скрытый каталог и файл (внутрь скрытого
    каталога не спускаемся — правило с `/**` закрывает его целиком); каждый
    симлинк, каталог он или файл: rglob симлинк-каталог не обходит, снимок
    его содержимого не видит, а `Edit(/**)` записать туда позволял (круг-1
    по PR #381, Codex Critical).
    """
    out = [] if symlinks_only else [(d, True) for d in PROTECTED_DIRS]
    root = graph.resolve()
    unreadable: list[str] = []
    for dirpath, dirnames, filenames in os.walk(graph, onerror=lambda e: unreadable.append(str(e))):
        base = pathlib.Path(dirpath)
        try:
            rel_base = base.resolve().relative_to(root)
        except (ValueError, OSError):
            dirnames[:] = []
            continue
        keep = []
        for d in sorted(dirnames):
            rel = rel_base / d
            is_link = (base / d).is_symlink()
            if d.startswith(".") or is_link:
                if is_link or not symlinks_only:
                    out.append((rel.as_posix(), True))  # и не спускаемся внутрь
            else:
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            is_link = (base / f).is_symlink()
            if (f.startswith(".") or is_link) and (is_link or not symlinks_only):
                out.append(((rel_base / f).as_posix(), False))
    if unreadable:
        # Нечитаемый подкаталог — неполный список запретов И неполный снимок
        # (rglob молча пропускает то же место): правке в таком графе не
        # место (круг-2 по PR #381, Codex).
        raise OSError(f"нечитаемые каталоги графа: {unreadable[0]}")
    return out


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
    if dest.exists():
        # Повтор с тем же штампом: старый снимок хранил бы файлы, которых в
        # графе уже нет, и restore воскрешал бы их (круг-1 по PR #381, Codex).
        shutil.rmtree(dest, ignore_errors=True)
        if dest.exists():
            raise OSError(f"старый снимок {dest.name} не удаляется — не пересоздаю")
    dest.mkdir(parents=True, exist_ok=True)
    for p in graph_files(graph):
        target = dest / p.resolve().relative_to(graph.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _clone(p, target):
            shutil.copy2(p, target)
    return dest


def rotate_snapshots(root: pathlib.Path, *keep: pathlib.Path) -> None:
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
        if stale in keep or not stale.is_dir():
            continue        # чужие файлы в каталоге — не наши, не трогаем
        shutil.rmtree(stale, ignore_errors=True)


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
    charoite_paths.secure_dir(qdir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(path), str(target))
    else:
        shutil.copy2(path, target)


def rotate_quarantine(root: pathlib.Path, keep: int = QUARANTINE_KEEP,
                      current: pathlib.Path | None = None) -> None:
    """Карантин не растёт бесконечно: десять последних запусков по штампу.
    Свой каталог не трогается, даже если штамп старой встречи сортируется
    в хвост (круг-2 по PR #381, DeepSeek)."""
    if not root.is_dir():
        return
    runs = sorted(p for p in root.iterdir() if p.is_dir() and p != current)
    for stale in runs[:-keep] if keep > 0 else runs:
        shutil.rmtree(stale, ignore_errors=True)


def graph_lock(graph: pathlib.Path, wait: float | None = None):
    """Замок графа для этого разбора — общий с ночной ревизией досье.

    Реализация переехала в src/file_locks.py: тот же `cloud.lock` берёт
    и nightly_dossier_review, иначе два пишущих контура сверяют правки
    друг друга (аудит облака 26.08, GLM I3). Здесь остаётся адрес
    каталога снимков этого графа и наш дефолт ожидания.
    """
    wait = LOCK_WAIT if wait is None else wait   # константу можно подменить в тестах
    try:
        base = charoite_paths.secure_dir(backup_root(graph).parent)
    except OSError as e:
        # нет права на каталог данных — как и без бэкапа: только чтение
        print(f"замок графа не взять ({e}) — работаю на чтение")
        return contextlib.nullcontext(False)
    return file_locks.graph_lock(base, wait, poll=LOCK_POLL)


@dataclasses.dataclass
class Verdict:
    """Что сверка сделала с правками облака. Все списки — имена файлов.
    При rolled_back те же списки значат «откачено», а не «нарушение»."""
    touched: int = 0                 # всего правок облака в песочнице
    applied: list[str] = dataclasses.field(default_factory=list)    # перенесено в граф
    conflicts: list[str] = dataclasses.field(default_factory=list)  # конвейер успел раньше
    reverted: list[str] = dataclasses.field(default_factory=list)   # запрещено правилами
    removed: list[str] = dataclasses.field(default_factory=list)    # служебная зона → в карантин
    deleted: list[str] = dataclasses.field(default_factory=list)    # облако стёрло — в графе оставлено
    failed: list[str] = dataclasses.field(default_factory=list)     # перенос не смог (OSError)
    rolled_back: bool = False        # ответ невалиден — откачено всё

    @property
    def violations(self) -> int:
        return len(self.reverted) + len(self.removed) + len(self.deleted)


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
    # Не признак авторства, а смысловое ограничение задачи: облако
    # дообогащает узлы, а не сочиняет их заново. В песочнице авторство
    # известно, но «переписал целиком» остаётся плохой правкой — и раньше
    # круга-1 по #447 я снёс эту ветку вместе с признаками зря (DS, I2).
    # Единственная допустимая форма «убрать узел» — заглушка-
    # перенаправление, как у tier3 при слиянии дублей.
    if (existed and not is_redirect_stub(new_text)
            and sum(1 for ln in old_text.splitlines() if ln.strip()) >= REWRITE_MIN_LINES
            and retention(old_text, new_text) < REWRITE_KEEP):
        return "rewritten"
    return None


def _read(p: pathlib.Path) -> str:
    """Текст файла или пустая строка: в графе бинарников нет, а битую
    кодировку заменяем, а не роняем на ней перенос."""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def edits_in_copy(before: dict[str, str], copy: pathlib.Path,
                  graph: pathlib.Path) -> list[pathlib.Path]:
    """Что облако сделало в своей копии: пути ОТНОСИТЕЛЬНО корня графа.

    Отдельная функция, а не `changed_since`: та сравнивает снимок с тем же
    каталогом, из которого он снят, и ключи в ней — абсолютные пути. Дать
    ей копию значит объявить изменённым КАЖДЫЙ файл графа — корни разные,
    ни один ключ не совпадает. Живой прогон 28.08 показал ровно это: одна
    правка, а «изменилось 2».

    Возвращаются относительные пути: файл может существовать в копии, в
    графе, или уже нигде — вызывающий строит из rel обе стороны сам.
    """
    root, croot = graph.resolve(), copy.resolve()
    now: dict[str, str] = {}
    for c in graph_files(copy):
        try:
            now[c.resolve().relative_to(croot).as_posix()] = _digest(c)
        except (ValueError, OSError):
            continue
    was = {}
    for key, digest in before.items():
        try:
            was[pathlib.Path(key).relative_to(root).as_posix()] = digest
        except ValueError:
            continue
    rels = [r for r, d in now.items() if was.get(r) != d]
    rels += [r for r in was if r not in now]      # облако удалило файл
    return [pathlib.Path(r) for r in sorted(set(rels))]


def apply_from_copy(before: dict[str, str], copy: pathlib.Path,
                    graph: pathlib.Path, qdir: pathlib.Path,
                    *, backup: pathlib.Path, valid: bool) -> Verdict:
    """Перенести правки облака из его копии в настоящий граф.

    Облако работает в копии (cwd CLI = снимок вне iCloud), поэтому авторство
    известно ПО ПОСТРОЕНИЮ: всё, чем копия отличается от `before`, сделало
    облако — конвейер в копию не пишет. Шесть отброшенных признаков и
    шестнадцать кругов PR #439 существовали только потому, что облако и
    конвейер писали в один каталог (№120).

    Принятый риск (DS, круг-1 по #447): между сравнением хеша и
    `tmp.replace` внутри safe_write есть окно в миллисекунды — доклейка
    конвейера, попавшая ровно туда, будет затёрта без следа в conflicts.
    Замок графа конвейер не берёт, POSIX-атомарного «сравнить-и-заменить»
    для файлов нет. Окно на порядки уже прежнего получасового (весь разбор
    до #447), цена промаха — одна доклейка, восстановимая из ревизии;
    строить вокруг этого ручной CAS со вскрытием safe_write — дороже риска.

    Невалидный ответ: настоящий граф не тронут вовсе — копия просто
    выбрасывается, откат исчезает как класс. Валидный: каждая правка идёт
    через прежние проверки (may_write, зоны исполнения, авторский раздел), а
    конфликт решается в пользу конвейера — если файл графа разошёлся со
    снимком, его писала живая работа, и правка облака уходит в карантин с
    именем в логе, а не поверх чужого.
    """
    v = Verdict(rolled_back=not valid)
    edits = edits_in_copy(before, copy, graph)
    v.touched = len(edits)
    root = graph.resolve()
    for rel in edits:
        cpath, gpath = copy / rel, graph / rel
        name = rel.as_posix()
        try:
            if not valid:
                # Граф не тронут — откатывать нечего. Но что наработало
                # облако, человеку видеть полезно: копия уедет с ротацией
                # снимков, поэтому её правки складываем в карантин.
                if cpath.is_file():
                    quarantine(cpath, copy, qdir, move=False)
                    v.reverted.append(name)
                else:
                    # стёртого в копии нет — класть в карантин нечего, и
                    # врать «лежит в карантине» лог не должен (DS, M3)
                    v.deleted.append(name)
                continue
            key = str(root / rel)
            existed = key in before
            # Старый текст берём из СНИМКА, не из графа: файл в графе мог
            # уже уехать под конвейером, и сравнение его с самим собой
            # молча пропускало чужую правку (живой прогон 28.08).
            old = _read(backup / rel) if existed else ""
            new = _read(cpath) if cpath.is_file() else None
            if new is None:                       # облако стёрло файл
                # Удаление НЕ переносим: у облака нет причин стирать чужое,
                # а восстановление стёртого — это и был откат, из-за
                # которого №119 унёс артефакты соседней встречи.
                v.deleted.append(name)
                continue
            why = judge(gpath, graph, old, new, existed)
            if why is not None:
                quarantine(cpath, copy, qdir, move=False)
                (v.removed if not existed else v.reverted).append(name)
                continue
            if ((existed and _digest(gpath) != before[key])
                    or (not existed and gpath.exists())):
                # Конвейер успел, пока облако думало: дописал существующий
                # файл (минутки, хроника ядра) или СОЗДАЛ новый узел — при
                # разборе соседней встречи upsert_entity заводит людей и
                # системы, не беря замка. Живая работа побеждает в обоих
                # случаях: правка облака — в карантин. Без второй половины
                # условия новый файл был единственным местом, где побеждало
                # облако (DS, круг-1 по #447). Она же ловит запись сквозь
                # симлинк-каталог графа в существующий файл: резолв уводит
                # путь из-под точки, но цель-то существует.
                quarantine(cpath, copy, qdir, move=False)
                v.conflicts.append(name)
                continue
            safe_write.write_text(gpath, new)
            v.applied.append(name)
        except OSError:
            v.failed.append(name)
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
    # Не дождавшийся работает на чтение и НЕ доставляет ревизию в граф —
    # сосед мог бы принять его файлы за правки облака (круг-1 по #381).
    deliver = graph_available
    with contextlib.ExitStack() as stack:
        # Замок нужен ВСЕГДА, когда мы пишем в граф. Доставка ревизии пишет
        # в защищённые папки и при may_edit=False (cloud_edit_graph
        # выключен) — без замка сосед-воркер видел эти файлы как правку
        # облака и убирал в карантин (аудит облака, DS M4).
        if (may_edit or deliver) and not stack.enter_context(graph_lock(graph)):
            print(f"граф занят другим разбором дольше {LOCK_WAIT // 60} мин — "
                  "работаю на чтение")
            may_edit = deliver = False
        return _run_locked(stamp, transcript, graph, rev, log, cfg,
                           may_edit=may_edit, graph_available=graph_available,
                           deliver=deliver, unlock=stack.close)


def _run_locked(stamp: str, transcript: pathlib.Path, graph: pathlib.Path,
                rev: pathlib.Path, log: pathlib.Path, cfg: dict, *,
                may_edit: bool, graph_available: bool, deliver: bool = True,
                unlock=lambda: None) -> int:
    # Выход в сеть — здесь, и рубильник спрашивается на самом выходе
    # (контракт tests/test_cloud_call_sites.py), а не только в run() выше.
    if not privacy.cloud_enrich_enabled(cfg):
        return 1
    # Граф могли отмонтировать, пока ждали замок: с graph_available=True
    # команда получила бы Edit при cwd=папка стенограмм (круг-2 по PR #381,
    # Codex Critical). Перепроверяем уже под замком.
    if graph_available and not graph_updater.cloud_graph_available(graph):
        print("граф исчез, пока ждали замок — работаю только текстом")
        graph_available = may_edit = deliver = False
        unlock()
    before, backup, denied, links = {}, None, [], []
    if graph_available:
        # Симлинки закрываем для ЧТЕНИЯ в любом режиме: без правки графа
        # deny-правил не было вовсе (аудит облака 26.08). Нечитаемый угол
        # графа здесь не авария: правку он и так запретит ниже.
        try:
            links = deny_paths(graph, symlinks_only=True)
        except OSError as e:
            print(f"симлинки графа не перечислить ({e}) — правило чтения не ставлю")
    if may_edit:
        try:
            denied = deny_paths(graph)   # пересчитаются по копии ниже
            if len(denied) > DENY_MAX:
                raise OSError(f"в графе {len(denied)} скрытых путей и симлинков — "
                              f"больше {DENY_MAX}, правила не влезут")
        except OSError as e:
            # понижение отпускает замок и выключает доставку: без замка
            # сосед принял бы нашу доставку за правки облака (круг-2, DS)
            print(f"{e} — работаю на чтение")
            may_edit = deliver = False
            denied = []
            unlock()
    cloud_pen = None
    if may_edit:
        before = snapshot(graph)
        try:
            backup = backup_graph(graph, stamp)
            # №120: облако пишет в ПЕСОЧНИЦУ — вторую копию графа, cwd его
            # CLI. Конвейер в неё не пишет, поэтому «чей файл» больше не
            # угадывается признаками: авторство известно по построению.
            # Снимок остаётся отдельным и нетронутым — это эталон «как
            # было»: по нему сверяется текст и ловится правка конвейера,
            # успевшая в граф, пока облако думало. Две копии на APFS
            # стоят почти нуля: clonefile делит блоки до первой записи.
            cloud_pen = backup_graph(graph, f"{stamp}-облако")
            denied = deny_paths(cloud_pen)
        except OSError as e:
            print(f"бэкап графа не удался ({e}) — работаю на чтение")
            may_edit = deliver = False
            cloud_pen = None
            unlock()                   # замок нужен только правящему
    # Файлы встречи — по стему её стенограммы, а не по минутному штампу:
    # посекундная соседка той же минуты в контекст не попадает (аудит 16.08).
    context, sent = graph_updater.cloud_enrich_context(transcript.parent, transcript.stem)
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name=transcript.name, folder=transcript.parent,
        graph=cloud_pen if may_edit and cloud_pen is not None else graph,
        rev_name=rev.name, stamp=stamp, arch_folder=None, may_edit=may_edit,
        context=context)
    cmd = graph_updater.cloud_enrich_command(
        cfg, claude_bin=cloud.claude_bin(),
        prompt=prompt, model=cloud.model(cfg, "cloud_model"), may_edit=may_edit,
        graph_available=graph_available, deny_paths=denied,
        symlink_paths=links)

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    # Прокси из settings.json — иначе из GUI-запуска без shell-окружения
    # `claude` идёт напрямую и получает «403 Request not allowed» (регион):
    # 21.08 так упали все разборы дня, пока демон с прокси работал.
    cloud.add_proxy(env)

    tmp = rev.with_suffix(rev.suffix + ".part")
    work_dir = graph_updater.cloud_enrich_workdir(
        cfg, cloud_pen if may_edit and cloud_pen is not None else graph,
        transcript.parent)
    mode = ("правка графа" if may_edit else
            "только чтение графа" if graph_available else
            "только текст (граф недоступен)")
    head = (f"[cloud-review] {stamp}: файлов в запросе {len(sent)} "
            f"({', '.join(sent)}), {len(context)} знаков, режим {mode}"
            + (f", закрыто для записи путей: {len(denied)}" if may_edit else "") + "\n")
    with tmp.open("w", encoding="utf-8") as out, contextlib.ExitStack() as files:
        # .part открывается первым: не откроется — лог и не нужен (и не течёт)
        try:
            lf = files.enter_context(log.open("a", encoding="utf-8"))
        except OSError as e:
            # лог — каталог или без прав: разбор важнее лога, stderr — в никуда
            print(f"лог недоступен ({e}): {head}", end="")
            lf = files.enter_context(open(os.devnull, "w", encoding="utf-8"))
        lf.write(head)
        lf.flush()
        try:
            code = subprocess.run(cmd, cwd=str(work_dir), env=env,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=lf,
                                  timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            lf.write(f"[cloud-review] таймаут {TIMEOUT}с — разбор прерван\n")
            code = -1
        except OSError as e:
            # CLI не установлен/PATH пуст/нет прав: ENOENT улетал наверх МИМО
            # finally со сверкой и ротацией — снимок оставался сиротой, лог
            # обрывался на первой строке, stderr воркера уходил в DEVNULL, и
            # встреча просто оставалась без ревизии без единого следа
            # (аудит облака 26.08, DS I2 + GLM I2).
            lf.write(f"[cloud-review] claude не запустился ({e})\n")
            code = -1

    ok = published = checked = False
    lines: list[str] = []          # лог пишется в конце, best-effort
    try:
        # Битый stdout (не UTF-8, исчезнувший файл) — это невалидный ответ,
        # а не исключение поверх сверки: сверка идёт в finally всегда.
        try:
            text = tmp.read_text(encoding="utf-8", errors="replace") if tmp.exists() else ""
        except OSError:
            text = ""
        ok = code == 0 and looks_like_report(text)
        published = publish(tmp, rev, ok)
        lines.append(f"[cloud-review] ревизия сохранена: {rev.name}\n" if published else
                     f"[cloud-review] ревизия НЕ сохранена (код {code}, "
                     f"{len(text)} знаков) — см. {rev.name}.partial\n")
    finally:
        # Сверка — раньше всего и без зависимости от лога: падение log.open
        # (права, ENOSPC, EMFILE) не должно обходить откат (круг-2, DS+Codex).
        if may_edit and backup is not None and not backup.is_dir():
            # Снимок исчез — с замком графа так может только внешняя рука.
            # Без копии enforce не откатывает, а УДАЛЯЕТ: restore видит
            # пустоту. Честнее не трогать файлы и сказать громко.
            lines.append("[cloud-review] СНИМОК ИСЧЕЗ — границы не сверяю, "
                         "файлы не трогаю; проверь правки руками\n")
        elif may_edit and backup is not None:
            # Карантин — по ТОЧНОМУ стему стенограммы (с секундами, если они
            # есть) и времени до микросекунд: две встречи одной минуты и два
            # запуска одной встречи не делят каталог, а forget_meeting
            # находит свой без префиксных догадок (круг-3 по #381).
            qdir = quarantine_root(graph) / f"{transcript.stem}-{dt.datetime.now():%H%M%S%f}"
            try:
                # №120: облако писало в копию, и перенос — единственный
                # момент, когда его правки касаются настоящего графа.
                # Невалидный ответ не откатывается — он просто не
                # переносится: граф не был тронут вовсе.
                v = apply_from_copy(before, cloud_pen, graph, qdir,
                                    backup=backup, valid=ok and published)
                checked = v.touched >= 0 and not v.failed
                lines.append(_verdict_line(v, qdir))
                # Песочница отработала — убираем. При падении переноса она
                # ОСТАЁТСЯ: там работа облака целиком, и человеку есть на
                # что посмотреть, когда автоматика развела руками.
                shutil.rmtree(cloud_pen, ignore_errors=True)
            except Exception as e:  # noqa: BLE001 — сказать и дойти до ротации
                lines.append(f"[cloud-review] ПЕРЕНОС УПАЛ ({e}) — правки облака "
                             f"остались в снимке, граф цел\n")
            try:
                if qdir.is_dir():
                    rotate_quarantine(quarantine_root(graph), current=qdir)
            except OSError as e:
                lines.append(f"[cloud-review] ротация карантина не удалась ({e})\n")
        # Доставка — ПОСЛЕ сверки границ и только после ПОЛНОЙ сверки: архив
        # и Документация — защищённые папки, и созданную здесь копию сверка
        # приняла бы за правку облака. Без графа (text-only) доставлять
        # некуда, без замка — нельзя (сосед сверяет). От лога не зависит.
        if published and deliver and (not may_edit or checked):
            buf = io.StringIO()
            deliver_review(rev, transcript, graph, stamp, buf)
            lines.append(buf.getvalue())
        try:
            with log.open("a", encoding="utf-8") as lf:
                lf.writelines(lines)
        except OSError as e:
            print(f"лог недоступен ({e}): " + "".join(lines))
        if backup is not None:
            # ротация — самым последним: свой снимок жил до конца сверки
            rotate_snapshots(backup_root(graph),
                             *(p for p in (backup, cloud_pen) if p))
    if may_edit and backup is not None and not checked:
        return 1                   # ревизия, может, и есть, но граф не сверен
    return 0 if published else 1


def _verdict_line(v: Verdict, qdir: pathlib.Path) -> str:
    """Одна строка лога про сверку — с отдельными словами для удаления и
    переписывания: «правок графа: 3» не говорило, что два узла стёрты."""
    if v.touched < 0:
        return "[cloud-review] снимка нет — границы не сверялись\n"
    if v.rolled_back:
        # Откат исчез как класс: облако работало в песочнице, граф не
        # менялся. Человеку важно другое — что именно оно успело наработать
        # и где эту работу посмотреть.
        tail = (f"; ПЕРЕНОС НЕ СМОГ: {', '.join(v.failed)}" if v.failed else "")
        what = (f"; в карантине: {', '.join(v.reverted)}" if v.reverted else "")
        gone = (f"; стёрто облаком в копии, в графе цело: {', '.join(v.deleted)}"
                if v.deleted else "")
        return (f"[cloud-review] ответ невалиден — граф не тронут; правок "
                f"облака {v.touched}, карантин {qdir}{what}{gone}{tail}\n")
    parts = [f"[cloud-review] правок облака: {v.touched}"]
    if v.applied:
        parts.append(f"перенесено в граф: {', '.join(v.applied)}")
    if v.conflicts:
        # Не авария, а нормальная развязка: конвейер писал в тот же файл,
        # пока облако думало. Живая работа осталась, версия облака — рядом.
        parts.append(f"конвейер успел раньше, версия облака в карантине: "
                     f"{', '.join(v.conflicts)}")
    if v.reverted:
        parts.append(f"запрещено правилами, не перенесено: {', '.join(v.reverted)}")
    if v.deleted:
        parts.append(f"облако стёрло — в графе ОСТАВЛЕНО: {', '.join(v.deleted)}")
    if v.removed:
        parts.append(f"создано в служебной зоне — в карантин: {', '.join(v.removed)}")
    if v.failed:
        parts.append(f"ПЕРЕНОС НЕ СМОГ (ошибка диска/прав): {', '.join(v.failed)}")
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
    charoite_paths.harden_umask()      # лог, .partial, карантин — 0600/0700
    cfg = graph_updater.load_cfg()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    return run(args.stamp, args.transcript, args.graph, args.rev, args.log, cfg)


if __name__ == "__main__":
    sys.exit(main())
