#!/usr/bin/env python3
"""Сломать код и убедиться, что тесты это заметили.

Единственная метрика, которая отвечает на вопрос «а тест вообще держит
что-нибудь». Покрытие отвечает на другой — «строка исполнилась»: тест без
единой проверки покрывает её на сто процентов и остаётся зелёным. Здесь мы
возвращаем в код дефект и требуем, чтобы прогон покраснел. Это ровно та
ручная практика — «откати фикс, тест обязан упасть», — только сама.

Почему не mutmut. Он копирует дерево в свою папку и исполняет мутанта
оттуда, а наши тесты в семи файлах запускают код подпроцессом по пути от
корня РЕПОЗИТОРИЯ — подпроцесс возьмёт немутантный оригинал, и мутант
«выживет» не потому, что тест плох, а потому, что до него не дошли. Здесь
мутация кладётся в отдельную копию репозитория — локальный клон на SHA
головы диапазона — и тесты гоняются оттуда же: подпроцессы видят тот же
мутантный код, что и импорт. Не `git worktree`: его реестр живёт в общем
`.git`, и параллельные `add`/`remove` соседних прогонов падали (№460).

Мутируем ТОЛЬКО строки, изменённые в заданном диапазоне: полный прогон по
`src/audio.py` — это тысячи мутантов и часы, а по хункам диффа — минуты.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# Мутации, которые дают сигнал. Строк и сообщений не трогаем: их переделка
# почти всегда «выживает» и тонет в отчёте шумом, а смысла в ней нет.
CMP_SWAP = {ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Lt: ast.LtE, ast.LtE: ast.Lt,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
            ast.Is: ast.IsNot, ast.IsNot: ast.Is,
            ast.In: ast.NotIn, ast.NotIn: ast.In}
BOOL_SWAP = {ast.And: ast.Or, ast.Or: ast.And}
# Арифметика — там, где живут ошибки на единицу: размеры чанков, перехлёст,
# индексы, окна. Без них мутатор не трогает целый класс кода, в котором
# «тесты зелёные, а баг живёт» (ревью 20.08, DeepSeek).
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add,
            ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
            ast.Div: ast.Mult}


class Mutation:
    """Одна поломка одного узла.

    Описание дописывает отрезок узла (`Eq → NotEq @4-10`) здесь и только здесь:
    в строке может стоять два одинаковых оператора (`(a == b) == c`), и без
    отрезка в отчёте они неразличимы. Вызывающий отдаёт описание без него,
    чистое описание — `bare()`: тестам и сводкам не приходится резать строку.
    Дамп узла-цели запоминается сейчас: `applied` сверяет им, что `apply` нашёл
    на этом месте тот же узел, а не соседа с теми же координатами (№386).
    """

    def __init__(self, path: pathlib.Path, node, what: str, change, *,
                 qualname: str = "", key: str = ""):
        self.path, self.line, self._bare, self.change = path, node.lineno, what, change
        self.what = f"{what} @{node.col_offset}-{node.end_col_offset}"
        self.kind, self.span = type(node), _span(node)
        self.target = ast.dump(node)
        #: Личность мутанта без номера строки (№469): путь, qualname области,
        #: описание, канонический текст узла и порядковый номер среди одинаковых.
        #: По ней — порядок выборки, журнал прогона и `--resume`.
        self.qualname, self.key = qualname, key
        #: Лежит ли в критичной зоне (`layout.json`, `mutation_critical`) — ставит `plan_for`
        self.critical = False

    @property
    def rank(self) -> str:
        """Место в очереди выборки: хеш личности, без сида — один на CI и локально."""
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()

    def bare(self) -> str:
        return self._bare

    def locate(self, tree: ast.AST):
        """Узел того же вида на том же отрезке — или None."""
        for n in ast.walk(tree):
            if _same(n, self):
                return n
        return None

    def apply(self, tree: ast.AST):
        """Сломать дерево на месте; вернуть узел, чей отрезок режет `patch_source`."""
        n = self.locate(tree)
        return None if n is None else self.change(tree, n)

    def __str__(self) -> str:
        try:
            shown = self.path.relative_to(pathlib.Path.cwd())
        except ValueError:
            shown = self.path
        return f"{shown}:{self.line}: {self.what}"


def split_range(rng: str) -> tuple[str, str, str]:
    """Диапазон git → (левый конец, разделитель, правый конец). Грамматика
    одна на мутатор: разделители `...` и `..`, пустой конец с любой стороны —
    `HEAD`, как читает git (`git rev-parse ..X` — это `HEAD..X`). Без
    разделителя — одна ревизия: левого конца нет, разделитель пуст."""
    for sep in ("...", ".."):
        if sep in rng:
            left, right = (part.strip() for part in rng.split(sep, 1))
            return left or "HEAD", sep, right or "HEAD"
    return "", "", rng.strip() or "HEAD"


def head_of(rng: str) -> str:
    """Правый конец диапазона — та ревизия, чей КОД мы ломаем.

    Без этого копия поднималась от текущего HEAD, а номера строк брались из
    чужого диапазона: мутации ложились мимо — в комментарии и пустые места,
    и «выжившими» объявлялось то, чего в коде нет. Поймано на первом же
    живом прогоне.
    """
    return split_range(rng)[2]


class PreparationError(Exception):
    """Подготовка прогона не удалась — диапазон не разрешился, копия не
    собралась. По канону исходов это «сломалась сама проверка» (код 1), а не
    «план был, не судился ни один» (9): до плана дело не дошло или копии нет."""


def resolve_range(root: pathlib.Path, rng: str) -> str:
    """Тот же диапазон, где каждый конец — SHA коммита. Зовётся ОДИН раз, до
    плана: имя ревизии, разрешённое трижды (дифф, чтение файлов плана, копия),
    при сдвиге HEAD между ними давало план одного коммита и копию другого
    (входной круг 4 по №460)."""
    left, sep, right = split_range(rng)

    def sha(end: str) -> str:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", f"{end}^{{commit}}"],
                           capture_output=True, text=True)
        if r.returncode:
            raise PreparationError(f"ревизия {end!r} не разрешилась — {r.stderr.strip()}")
        return r.stdout.strip()

    if not sep:
        return sha(right)
    if sep == "...":
        # Дифф `A...B` зависит только от общего предка: он и уходит в ключ прогона,
        # факты и зоны базы. Кончик A сдвигается от `git fetch` и между стартами
        # шардов CI — ключ и факты расходились бы при той же выборке (выходной
        # круг 1 по №469, Sonnet I1)
        base = subprocess.run(["git", "-C", str(root), "merge-base", sha(left), sha(right)],
                              capture_output=True, text=True)
        if base.returncode:
            raise PreparationError(f"у {left!r} и {right!r} нет общего предка — "
                                   f"{base.stderr.strip()}")
        return f"{base.stdout.strip()}{sep}{sha(right)}"
    return f"{sha(left)}{sep}{sha(right)}"


def copy_tree(root: pathlib.Path, sha: str, tmp: pathlib.Path) -> pathlib.Path:
    """Копия репозитория для мутантов: локальный клон `root` в `tmp/tree` и
    отсоединённый checkout на `sha`. В репозиторий-источник мутатор не пишет
    ничего: клон читает его объекты (жёсткие ссылки на своём диске) и пишет
    только в свой каталог; уборка — удалением `tmp`. Опыты №460: клон —
    0,28 с против 0,15–0,19 у `worktree add`, 1440 параллельных клонов без
    единой ошибки, источник-worktree и неглубокий источник клонируются."""
    work = tmp / "tree"
    for step, cmd in (("clone", ["git", "clone", "-q", "--no-checkout", str(root), str(work)]),
                      ("checkout", ["git", "-C", str(work), "checkout", "-q", "--detach", sha])):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise PreparationError(f"копия {work}: git {step} — {r.stderr.strip()}")
    return work


# Области «нашего python» — у сторожа раскладки, не свой литерал `src/`: PR только
# по `scripts/` давал job без единого мутанта и зелёный (входной круг №339, DS I7)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import layout_map  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
# Путь от __file__, а не от корня данных: мутатор неотделим от репозитория —
# копию для мутантов он клонирует из него же, и до вызова канона корня ещё не дошёл.
# «Два корня в одном процессе» (GLM I3, круг 5) здесь не расходятся: второго
# сценария, где скрипт лежит отдельно от src/, попросту нет.
from exit_codes import EXIT_NOTHING_TO_CHECK, EXIT_PARTIAL, EXIT_UNJUDGED, EXIT_UNMUTABLE  # noqa: E402
# `outcome` — по имени модуля, не `from …`: список читателей кодов в тестах
# (`tests/test_exit_codes.py`) выведен из from-импортов EXIT_*, и лишнее имя
# разошлось бы с ним. Слово исхода берём тем же классификатором, что и все.
import exit_codes  # noqa: E402
MUTATION_AREAS = layout_map.PYTHON_AREAS


@dataclasses.dataclass
class ScanReport:
    """Что нашёл разбор одного файла: мутанты и то, из чего их не вышло."""
    mutations: list
    lines_constant: int = 0     # строки диапазона, снятые как константы модуля
    nodes: int = 0              # узлы AST на оставшихся строках диапазона
    #: Почему файл не разобрался (пусто — разобрался): ломать в нём было что, а
    #: план о нём молчал бы — `plan_for` считает его непрочитанным (выходной круг 2
    #: по №441, DS C1).
    unparsed: str = ""


@dataclasses.dataclass
class ScanTotals:
    """Счётчики плана по всему диапазону. Без них пустой план не отличал
    «строки — комментарии» от «код есть, операторов нет» и от «файл не
    прочитался»: 25.09 правка условия дала ноль мутантов, и CI позеленел с
    «мутировать было нечего» (№386)."""
    files_in: int = 0
    lines_in: int = 0
    lines_constant: int = 0
    nodes: int = 0
    files_unreadable: int = 0
    #: Какие файлы не судились и почему — строки «путь — причина». Счётчик выше —
    #: их число; оба растут только в `_unreadable`: красный `partial` без имени
    #: файла заставлял автора PR угадывать (выходной круг 3 по №441, DS M1).
    unreadable: list[str] = dataclasses.field(default_factory=list)
    #: Размер ВЫБОРКИ до среза шардом: знаменатель «0 из P» и сверка слияния
    #: шардов `ΣM == P`. Без него пустой шард неотличим от «в диапазоне нечего».
    #: Выборка — это план (№469): не судилось из выборки — неполнота, а то, что
    #: выборка не взяла из полного плана, — политика, а не срез.
    planned: int = 0
    #: Полный план до выборки и сколько в нём и в выборке критичных мутантов
    full: int = 0
    critical_full: int = 0
    critical_sampled: int = 0
    #: Список критичных зон — дайджест объединения base ∪ head и заметка, если в
    #: какой-то ревизии его нет; шарды одного прогона обязаны назвать один дайджест
    zones: str = ""
    zones_note: str = ""
    #: Дайджест ключей выборки (до шарда) и ключ прогона (`run_key`)
    sample: str = ""
    run: str = ""
    #: (K, N) шарда, если он назван; None — план целиком. Из него берётся M
    #: шарда для машинной строки; соседи по N восстанавливают покрытие.
    shard: tuple[int, int] | None = None


#: Версия формата машинной строки шарда и журнала прогона. Строка без неё или с
#: другой — вердикт красный с причиной: старые артефакты судить по новым правилам нельзя.
FACTS_VERSION = 2


@dataclasses.dataclass
class Facts:
    """Факты прогона или шарда — единственное, что пишет исполнитель (№469).

    Слово исхода и код — производные, их считает один судья `verdict_code`; шард
    ничего не классифицирует сам. Прежде машинная строка несла только слово, и
    судья не отличал «выжили некритичные» от «выжили некритичные, а бюджет оборвал
    доли» (входной круг 2 по №469, Sonnet C1). `M` — доля этого прогона, `P` —
    выборка, `full` — полный план. `survivors` — выжившие: ключ, путь, строка,
    описание, критичность."""
    K: int = 1
    N: int = 1
    M: int = 0
    P: int = 0
    full: int = 0
    tested: int = 0
    skipped: int = 0
    unread: int = 0
    nodes: int = 0
    lines_in: int = 0
    broken: str = ""
    broken_rc: int = 0
    aborted: str = ""
    critical_full: int = 0
    critical_sampled: int = 0
    zones: str = ""
    zones_note: str = ""
    base: str = ""
    head: str = ""
    #: Ключ прогона — имя журнала рассуждённых и аргумент `--resume`
    run: str = ""
    survivors: list = dataclasses.field(default_factory=list)

    @property
    def critical_survivors(self) -> list:
        return [s for s in self.survivors if s["critical"]]


def survivor_row(m: Mutation, root: pathlib.Path) -> dict:
    """Выживший для фактов: ключ, путь от корня, строка, описание, зона."""
    try:
        rel = m.path.relative_to(root).as_posix()
    except ValueError:
        rel = m.path.as_posix()
    return {"key": m.key, "path": rel, "line": m.line, "what": m.what, "critical": m.critical}


def verdict_code(f: Facts) -> int:
    """Исход одним значением — единственный судья (№455, №469): одиночного прогона,
    шарда и свода шардов (`merge_shards` зовёт его на сложенных фактах).

    1 — выжил мутант в критичной зоне или сломалась сама проверка (красная база —
    её код 2, копия не собралась — 1); `EXIT_UNJUDGED` — была доля, не судился ни
    один; `EXIT_PARTIAL` — судили не всю долю (бюджет, встреча, не применилось,
    файл не прочитан); `EXIT_NOTHING_TO_CHECK` / `EXIT_UNMUTABLE` — доля пуста;
    0 — доля судилась целиком, критичных выживших нет. Выжившие вне критичных зон
    исход не меняют: их список — в отчёте (решение владельца 30.09).

    Функция от состояния, а не лестница `if` в конце `main`: в круге 3 по №441
    такая лестница спрашивала «ничего не судилось» раньше полноты (DS C1 = GLM 1).
    Критичный выживший — раньше полноты: он красный при любой полноте."""
    if f.broken:
        return f.broken_rc or 1
    if f.critical_survivors:
        return 1
    if f.M and not f.tested:
        return EXIT_UNJUDGED
    # Непрочитанный файл (нет в ревизии, не utf-8, не разобрался) — неполнота при
    # любом плане: его строки не судились, а пустой план из-за него — не «нечего» (№386).
    if f.unread:
        return EXIT_PARTIAL
    if f.M == 0:
        # Пустая доля непустой выборки — «нечего» этого шарда, соседи судят своё.
        # «Мутировать нечего» — только когда в строках есть код: правка одного
        # комментария иначе давала то же слово, что и слепое пятно операторов (#630).
        return EXIT_UNMUTABLE if f.nodes and not f.P else EXIT_NOTHING_TO_CHECK
    if f.tested < f.M or f.skipped:
        return EXIT_PARTIAL
    return 0


#: Вид журнала рассуждённых в реестре `charoite_paths.LOG_KINDS`
RUN_LOG_KIND = "mutation_run"


def run_key(rng: str, max_n: int | None, timeout: int, sample: str) -> str:
    """Ключ прогона: разрешённый диапазон (SHA), выборка, таймаут, версия формата.
    `--jobs`, `--budget-s`, `--force` в него не входят — они меняют, сколько
    успели, а не что судили. Правка теста сдвигает HEAD — и ключ: «убит» по
    старым тестам на новые не переносится (входные круги 1–2 по №469)."""
    raw = json.dumps({"v": FACTS_VERSION, "range": rng, "max": max_n, "timeout": timeout,
                      "sample": sample}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _manifest(rng: str, args: argparse.Namespace, totals: ScanTotals) -> dict:
    return {"manifest": FACTS_VERSION, "range": rng, "max": args.max,
            "timeout": args.timeout, "sample": totals.sample}


def journal_add(path: pathlib.Path, row: dict) -> None:
    """Строка журнала — одним `write` в режиме добавления: параллельные доли пишут
    каждая в свой файл, но возобновление с другим N может дописывать в чужой."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def journal_read(data_root: pathlib.Path, key: str) -> tuple[list[dict], dict[str, dict]]:
    """Все файлы журнала прогона `key` (любое N долей): манифесты и исходы по
    ключу мутанта. Оборванная последняя строка (SIGKILL посреди записи) — не
    ошибка: мутант просто не рассуждён."""
    import charoite_paths  # noqa: E402
    first = charoite_paths.log_path(data_root, RUN_LOG_KIND, part=f"{key}-1", suffix=".jsonl")
    manifests: list[dict] = []
    done: dict[str, dict] = {}
    for path in sorted(first.parent.glob(f"{first.name[:-len('1.jsonl')]}*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if "manifest" in row:
                manifests.append(row)
            elif isinstance(row.get("key"), str) and row.get("outcome") in \
                    ("killed", "survived", "skipped"):
                done[row["key"]] = row
    return manifests, done


def journal_open(data_root: pathlib.Path, totals: ScanTotals, k: int, rng: str,
                 args: argparse.Namespace) -> dict[str, dict]:
    """Открыть журнал доли и вернуть рассуждённых прежним прогоном.

    Без `--resume` журнал доли начинается заново (манифест первой строкой). С
    `--resume` ключ обязан совпасть с ключом этой выборки, а журнал — найтись:
    иначе отказ с названием разошедшейся части, а не «чисто» по пустоте."""
    import charoite_paths  # noqa: E402
    path = charoite_paths.log_path(data_root, RUN_LOG_KIND, part=f"{totals.run}-{k}",
                                   suffix=".jsonl")
    manifest = _manifest(rng, args, totals)
    if not args.resume:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
        return {}
    manifests, done = journal_read(data_root, args.resume)
    if not manifests:
        raise PreparationError(f"журнал прогона {args.resume} не найден или истёк "
                               f"(ретеншн logs/) — запусти прогон без --resume")
    if args.resume != totals.run:
        old = manifests[0]
        diff = [name for name in ("range", "max", "timeout", "sample")
                if old.get(name) != manifest[name]]
        what = {"range": "диапазон (новый коммит)", "max": "--max", "timeout": "--timeout",
                "sample": "выборка (код в области изменился)"}
        raise PreparationError(f"ключ {args.resume} — прогон другой выборки: разошлись "
                               + (", ".join(what[d] for d in diff) or "версия формата")
                               + f"; ключ этой выборки — {totals.run}")
    if not path.exists():
        journal_add(path, manifest)
    return done


def busy_guard(args) -> bool:
    """Действует ли гвард занятости машины. Снимает его только `--force` —
    человек сознательно идёт поверх встречи. Отдельного флага для CI нет и не
    нужно: без данных владельца `machine_busy` пуст сам (замер 22.09 на голом
    каталоге), а флаг «владельца здесь нет» на машине владельца вёл себя как
    `--force` без единого слова (круг 1 по коду №339, DS I4 = GLM I3). Один
    предикат на обе точки гварда: две копии выражения пережили мутацию (#605)."""
    return not args.force


def changed_lines(root: pathlib.Path, rng: str) -> dict[pathlib.Path, set[int]]:
    """Строки, добавленные в диапазоне, по файлам областей нашего python."""
    out = subprocess.run(["git", "diff", "--unified=0", rng, "--", *MUTATION_AREAS],
                         cwd=root, capture_output=True, text=True, check=True).stdout
    result: dict[pathlib.Path, set[int]] = {}
    cur: pathlib.Path | None = None
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = root / line[6:]
            result.setdefault(cur, set())
        elif line.startswith("@@") and cur is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                result[cur].update(range(start, start + count))
    return {p: ls for p, ls in result.items() if ls and p.suffix == ".py"}


# Пары «оператор + ПРАВЫЙ операнд», где подмена оператора тождественна для
# любых чисел. Список короткий намеренно, каждое исключение куплено разбором
# (ревью 20.08, круги 3 и 4):
#   — `x * 1` → `x // 1` НЕ тождество: для 2.5 выйдет 2.0 вместо 2.5;
#   — константа СЛЕВА меняет всё: `0 + n` → `0 - n` переворачивает знак;
#   — `x - 1` → `x + 1` — ошибка на двойку, тот самый ценный класс.
_NEUTRAL = {(ast.Div, 1), (ast.Add, 0), (ast.Sub, 0)}


def _neutral(node: ast.BinOp) -> bool:
    """`x / 1`, `x + 0`, `x - 0`: подмена оператора здесь ничего не меняет.

    Только правый операнд: при левой константе порядок операндов сохраняется,
    а смысл — нет.
    """
    right = node.right
    if not isinstance(right, ast.Constant) or isinstance(right.value, bool):
        return False
    # И `0.0`/`1.0` тоже: `x + 0.0` — то же тождество, что и `x + 0`, а
    # проверка на int их пропускала мимо фильтра (ревью 20.08, круг 4:
    # DeepSeek и локальная голова независимо).
    if not isinstance(right.value, (int, float)):
        return False
    return (type(node.op), int(right.value)) in _NEUTRAL and right.value in (0, 1)


def _module_constants(tree: ast.Module) -> set[int]:
    """Строки с константами уровня модуля.

    Их мутация почти всегда эквивалентна: тест читает ту же константу, что и
    код (`ov.MIN_MIC_SECONDS`), и остаётся зелёным при любом её значении.
    Такие выжившие неотличимы в отчёте от настоящих дыр, а их в проекте
    десятки — гейт «ноль выживших» стал бы недостижим (ревью 20.08, DeepSeek).
    """
    out: set[int] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for n in ast.walk(node.value):
                if isinstance(n, ast.Constant):
                    out.add(getattr(n, "lineno", -1))
    return out


#: Чем `ast.parse` отказывает: `SyntaxError`, NUL-байт в тексте (`ValueError`),
#: патологическая вложенность (`RecursionError`, `MemoryError`). Одно место на
#: «не разбирается»: прежде `scan` ловил только `SyntaxError`, и NUL-байт ронял
#: шард трассировкой до первой записи отчёта (выходной круг 3 по №441, DS I1).
PARSE_ERRORS = (SyntaxError, ValueError, RecursionError, MemoryError)


def parse_source(text: str) -> tuple[ast.Module | None, str]:
    """Текст → дерево и пустая причина, или `None` и причина отказа разбора."""
    try:
        return ast.parse(text), ""
    except PARSE_ERRORS as e:
        return None, f"{type(e).__name__}: {getattr(e, 'msg', None) or e}"


def _candidate(node: ast.AST):
    """Что можно сделать с узлом: (описание, поломка) или None."""
    if isinstance(node, ast.Compare) and node.ops:
        op = type(node.ops[0])
        if op in CMP_SWAP:
            return f"{op.__name__} → {CMP_SWAP[op].__name__}", _swap_cmp
    elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAP:
        return (f"{type(node.op).__name__} → {BOOL_SWAP[type(node.op)].__name__}",
                _swap_bool)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        # `if not apply or not same:` → `if not apply:` / `elif not same:`
        # дала ноль мутантов: отрицание не ломалось вовсе (№386). Обратного
        # оператора («добавить not») нет: он удваивает план, а класс ошибок
        # тот же — перевёрнутое условие.
        return "not X → X", _drop_not
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return f"{node.value} → {not node.value}", _swap_const(not node.value)
        if isinstance(node.value, (int, float)) and node.value not in (0,):
            return f"{node.value} → 0", _swap_const(0)
    elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP and not _neutral(node):
        return (f"{type(node.op).__name__} → {BIN_SWAP[type(node.op)].__name__}",
                _swap_bin)
    elif isinstance(node, ast.Return) and node.value is not None \
            and not (isinstance(node.value, ast.Constant) and node.value.value is None):
        # `return None` → `return None` — мутант-тождество, в отчёте он
        # неотличим от настоящей дыры (прогон партии D, 22.08)
        return "return X → return None", _drop_return
    return None


def canon(node: ast.AST) -> str:
    """Канонический текст узла для ключа мутанта: имя типа и поля, без позиций,
    без `None` и пустых списков. Не `ast.dump`: его формат меняется между версиями
    Python (3.13 опускает пустые поля), а ключ уходит из процесса — в журнал
    прогона и в выборку, одинаковую для CI (3.12) и машины владельца (входной круг
    1 по №469, Sonnet C2)."""
    parts = [type(node).__name__]
    for name, value in ast.iter_fields(node):
        # `None` опускаем, кроме значения константы: `x == None` — не пустое место
        if (value is None and not (isinstance(node, ast.Constant) and name == "value")) \
                or (isinstance(value, list) and not value):
            continue
        if isinstance(value, ast.AST):
            parts.append(f"{name}={canon(value)}")
        elif isinstance(value, list):
            parts.append(name + "=[" + ",".join(canon(v) if isinstance(v, ast.AST) else repr(v)
                                                 for v in value) + "]")
        else:
            parts.append(f"{name}={type(value).__name__}:{value!r}")
    return "(" + " ".join(parts) + ")"


def scan(path: pathlib.Path, lines: set[int], source: str | None = None,
         rel: str | None = None) -> ScanReport:
    """Что можно сломать в этих строках — и сколько там было из чего ломать.

    Ключ мутанта считается по ВСЕМ кандидатам файла, до фильтра по строкам
    диапазона и констант модуля: порядковый номер среди одинаковых (qualname,
    описание, текст) иначе зависел бы от того, какую из одинаковых строк правил
    коммит, и `--resume` записал бы чужой исход на другой код (входной круг 1 по
    №469, Sonnet I1). `rel` — путь от корня репозитория для ключа."""
    tree, why = parse_source(source if source is not None else path.read_text(encoding="utf-8"))
    if tree is None:
        return ScanReport([], unparsed=why)
    rel = rel if rel is not None else path.name
    try:
        return _scan_tree(tree, path, lines, rel)
    except PARSE_ERRORS as e:
        # Обход и канон рекурсивны: разобравшийся, но слишком глубокий файл —
        # «не прочитан», а не трассировка до первой записи фактов (выходной круг 1
        # по №469, Sonnet M1)
        return ScanReport([], unparsed=f"{type(e).__name__}: {e}")


def _scan_tree(tree: ast.Module, path: pathlib.Path, lines: set[int], rel: str) -> ScanReport:
    consts = _module_constants(tree)
    report = ScanReport([], lines_constant=len(lines & consts))
    lines = lines - consts
    seen: dict[tuple[str, str, str], int] = {}
    for node, qual in layout_map.scoped_nodes(tree):
        ln = getattr(node, "lineno", None)
        mine = ln is not None and ln in lines
        if mine:
            report.nodes += 1
        got = _candidate(node)
        if got is None:
            continue
        what, change = got
        text = hashlib.sha256(canon(node).encode("utf-8")).hexdigest()[:16]
        n = seen[(qual, what, text)] = seen.get((qual, what, text), -1) + 1
        if mine:
            report.mutations.append(Mutation(path, node, what, change, qualname=qual,
                                             key=f"{rel}::{qual}::{what}::{text}#{n}"))
    return report


#: Потолок выборки на задачу (решение владельца 30.09, №469) и пол некритичным:
#: критичные мутанты берутся первыми, но PR-рефакторинг критичной зоны не оставляет
#: остальные файлы с нулём навсегда; неиспользованный пол возвращается критичным.
SAMPLE_MAX = 60
NONCRITICAL_FLOOR = 15
#: Где лежит список зон в ревизии — то же имя, что у сторожа раскладки
ZONES_REL = layout_map.LAYOUT.relative_to(layout_map.REPO).as_posix()


def zones_for(root: pathlib.Path, rng: str) -> tuple[frozenset[str], str, str]:
    """Критичные зоны диапазона: объединение `mutation_critical` базы и головы.

    Одна база не держит модуль, который PR сам вносит в список; одна голова даёт
    PR снять модуль и тут же позеленеть его выжившими. Объединение: добавление
    действует сразу, снятие — после мержа (входной круг 2 по №469, Sonnet C2 =
    GLM C2). Ревизии без файла или без ключа — пустой вклад и заметка, а не отказ:
    первый PR №469 сам вводит ключ. Возвращает (записи, дайджест, заметка)."""
    left, _, right = split_range(rng)
    entries: set[str] = set()
    notes: list[str] = []
    for rev in ([left] if left else []) + [right]:
        r = subprocess.run(["git", "show", f"{rev}:{ZONES_REL}"], cwd=root, capture_output=True)
        data = {}
        if not r.returncode:
            try:
                data = json.loads(r.stdout.decode("utf-8"))
            except ValueError:
                raise PreparationError(f"{ZONES_REL} в {rev[:12]} не разбирается")
        got = data.get(layout_map.ZONE_KEYS[0]) if isinstance(data, dict) else None
        if not isinstance(got, dict):
            notes.append(f"в {rev[:12]} списка зон нет")
            continue
        entries.update(got)
    digest = hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()[:12]
    return frozenset(entries), digest, "; ".join(notes)


def _round_robin(muts: list[Mutation], n: int) -> list[Mutation]:
    """До `n` мутантов поровну по файлам: очередь файла — по `rank`, файлы — по пути."""
    by_file: dict[pathlib.Path, list[Mutation]] = {}
    for m in sorted(muts, key=lambda m: (str(m.path), m.rank)):
        by_file.setdefault(m.path, []).append(m)
    queues = [by_file[p] for p in sorted(by_file, key=str)]
    picked: list[Mutation] = []
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop(0))
    return picked


def select(plan: list[Mutation], max_n: int | None) -> list[Mutation]:
    """Выборка: критичные первыми, некритичным пол, внутри слоя — поровну по
    файлам, внутри файла — по хешу личности (`rank`). Какие мутанты взяты, от
    номеров строк не зависит; порядок результата — порядок плана."""
    if max_n is None or len(plan) <= max_n:
        return list(plan)
    crit = [m for m in plan if m.critical]
    rest = [m for m in plan if not m.critical]
    floor = min(NONCRITICAL_FLOOR, max_n // 4, len(rest))
    took = _round_robin(crit, max_n - floor)
    took += _round_robin(rest, max_n - len(took))
    picked = {id(m) for m in took}
    return [m for m in plan if id(m) in picked]


def plan_for(root: pathlib.Path, rng: str, shard: tuple[int, int] | None = None,
             max_n: int | None = None) -> tuple[list[Mutation], ScanTotals]:
    """Выборка мутантов по диапазону и счётчики того, из чего она собрана.

    Область — код продукта (`layout_map.mutation_area` по ревизии головы): `src/` и
    скрипты, которые запускает продукт. Выборка (`select`, до `max_n`) — это план:
    шард `(K, N)` берёт индексы `i % N == K-1` уже из неё, иначе доли одного
    прогона судили бы разные выборки. `totals.planned` — размер выборки (P),
    `totals.full` — полный план.
    """
    zones, digest, note = zones_for(root, rng)
    changed = changed_lines(root, rng)
    inv = None
    if changed:
        try:
            inv = layout_map.inventory(root, rev=head_of(rng))
        except layout_map.LayoutError as e:
            raise PreparationError(f"область мутатора не собралась: {e}")
    area = layout_map.mutation_area(inv) if inv is not None else set()

    def ours(path: pathlib.Path) -> bool:
        # Вне области — только то, что ревизия знает и разобрала: файл, которого
        # в ревизии нет или который не разобрался, остаётся в плане и становится
        # «не прочитан» — иначе «не прочитал» снова превратилось бы в «нечего» (№386)
        rel = path.relative_to(root).as_posix()
        info = inv.files.get(rel)
        return rel in area or info is None or info.tree is None
    targets = {p: ls for p, ls in changed.items() if ours(p)}
    totals = ScanTotals(files_in=len(targets),
                        lines_in=sum(len(ls) for ls in targets.values()))

    def _unreadable(rel: pathlib.Path, why: str) -> None:
        totals.files_unreadable += 1
        totals.unreadable.append(f"{rel} — {why}")
    rev = head_of(rng)
    plan: list[Mutation] = []
    for path, lines in sorted(targets.items()):
        rel = path.relative_to(root)
        # Разбираем ту версию файла, которую и будем ломать: рабочее дерево
        # может стоять на другой ветке, и номера строк не совпадут.
        # Байты и явный utf-8, а не `text=True`: тот декодирует кодировкой
        # локали, и под C-локалью кириллица в исходнике роняла бы разбор.
        blob = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=root,
                              capture_output=True)
        if blob.returncode:
            # Не молча: выпавший файл превращал «не прочитал» в «нечего» (№386)
            _unreadable(rel, f"нет в ревизии {rev}")
            continue
        try:
            source = blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            # `git show` прочитал, но это не наш текст: та же неполнота, что и
            # выпавший файл, а не трассировка посреди плана (DS M1 круга 1 по #630)
            _unreadable(rel, "не utf-8")
            continue
        report = scan(path, lines, source, rel=rel.as_posix())
        if report.unparsed:
            # Третья нога той же неполноты: прочитали, а разобрать нельзя. Без
            # счётчика пустой план из такого файла выходил «мутировать нечего» —
            # зелёным (выходной круг 2 по №441, DS C1).
            _unreadable(rel, f"не разбирается ({report.unparsed})")
            continue
        totals.lines_constant += report.lines_constant
        totals.nodes += report.nodes
        for m in report.mutations:
            m.critical = layout_map.in_zone(zones, rel.as_posix(), m.qualname)
        plan.extend(report.mutations)
    totals.full = len(plan)
    totals.critical_full = sum(m.critical for m in plan)
    plan = select(plan, max_n)
    totals.planned = len(plan)
    totals.critical_sampled = sum(m.critical for m in plan)
    totals.zones, totals.zones_note = digest, note
    totals.sample = hashlib.sha256("\n".join(sorted(m.key for m in plan)).encode("utf-8")).hexdigest()
    if shard is not None:
        k, n = shard
        totals.shard = (k, n)
        plan = [m for i, m in enumerate(plan) if i % n == k - 1]
    return plan, totals


def _replace_node(tree: ast.AST, old: ast.AST, new: ast.AST) -> None:
    """Поставить `new` на место `old` у его родителя; позиции — от `old`.

    Позиции нужны `patch_source`: он режет текст по отрезку возвращённого
    узла, и у операнда `not` этот отрезок обязан быть отрезком всего `not X`.
    """
    ast.copy_location(new, old)
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if value is old:
                setattr(parent, field, new)
                return
            if isinstance(value, list):
                for i, v in enumerate(value):
                    if v is old:
                        value[i] = new
                        return


def _swap_cmp(tree, n):
    n.ops = [CMP_SWAP[type(n.ops[0])]()] + list(n.ops[1:])
    return n


def _drop_not(tree, n):
    _replace_node(tree, n, n.operand)
    return n.operand


def _swap_bin(tree, n):
    n.op = BIN_SWAP[type(n.op)]()
    return n


def _swap_bool(tree, n):
    n.op = BOOL_SWAP[type(n.op)]()
    return n


def _swap_const(value):
    def change(tree, n):
        n.value = value
        return n
    return change


def _drop_return(tree, n):
    n.value = None
    return n


class _Parens:
    """Узел, вставляемый в скобках: вторая попытка `applied`. Позиции — узла."""

    def __init__(self, node):
        self.node = node
        self.lineno, self.col_offset = node.lineno, node.col_offset
        self.end_lineno, self.end_col_offset = node.end_lineno, node.end_col_offset


def patch_source(text: str, node: ast.AST | _Parens) -> str | None:
    """Заменить в тексте ровно один узел, не трогая остальной файл.

    Раньше файл переписывался целиком через `ast.unparse`: тот выбрасывает
    комментарии и перевыпускает литералы в своих кавычках. Тест, который
    проверяет ИСХОДНИК по тексту (у нас такой есть), падал на мутантном файле
    из-за переформатирования — и все мутанты модуля отчитывались «убит»
    независимо от мутации (ревью 20.08, DeepSeek).

    Осознанное ограничение: ВНУТРИ заменяемого узла форматирование всё равно
    перевыпускается — `res["точность"]` станет `res['точность']`. Гнаться за
    побайтовой точностью внутри узла значило бы вырезать позиции оператора
    руками (в дереве их нет) ради случая, когда текстовый тест читает строку
    из самого мутируемого выражения. Такого у нас нет; появится — доработаем.
    """
    lines = text.splitlines(keepends=True)
    start, end = getattr(node, "lineno", None), getattr(node, "end_lineno", None)
    col, end_col = getattr(node, "col_offset", None), getattr(node, "end_col_offset", None)
    if None in (start, end, col, end_col) or end > len(lines):
        return None
    # По БАЙТАМ: `ast` отдаёт col_offset в utf-8 байтах, а срез строки идёт
    # по символам. На кириллице счёт расходится, хвост уезжает за конец узла
    # и файл становится синтаксически битым — мутант «убит» из-за поломки, а
    # не из-за мутации. Проект русскоязычный, промах был бы массовым
    # (ревью 20.08, круг 3, DeepSeek). Границы токенов всегда на границе
    # символов, поэтому decode не оборвётся.
    head = ("".join(lines[:start - 1])
            + lines[start - 1].encode("utf-8")[:col].decode("utf-8"))
    tail = (lines[end - 1].encode("utf-8")[end_col:].decode("utf-8")
            + "".join(lines[end:]))
    try:
        piece = (f"({ast.unparse(node.node)})" if isinstance(node, _Parens)
                 else ast.unparse(node))
    except Exception:                            # noqa: BLE001
        return None
    return head + piece + tail


def _span(node) -> tuple:
    return (getattr(node, "lineno", None), getattr(node, "col_offset", None),
            getattr(node, "end_lineno", None), getattr(node, "end_col_offset", None))


def _same(n: ast.AST, mut: Mutation) -> bool:
    """Тот ли это узел: вид и ПОЛНЫЙ отрезок. Пары «строка, колонка» мало — у
    `a == b == c` в скобках, `a + b + c`, `a and b or c` внешний и внутренний
    узел начинаются в одной точке и различаются только концом."""
    return type(n) is mut.kind and _span(n) == mut.span


def applied(mut: Mutation, source: str) -> tuple[str | None, str]:
    """Текст мутанта — или None и причина словами.

    Прежде годность значила «текст изменился», и непарсящийся мутант уходил в
    копию дерева, прогон падал на `SyntaxError`, а мутант засчитывался «убит» —
    ложь того же класса, что «нечего мутировать» при нуле мутантов (№386).
    Теперь мутант годен, только если его текст разбирается в то же дерево,
    что и сломанное `apply` (позиции в дамп не входят). Не сошлось — одна
    попытка в скобках: операнд бывает ниже по приоритету, чем место вставки
    (`x and not (a or b)` → `x and (a or b)`). Частных правил под операторы нет.
    """
    tree, bad = parse_source(source)
    if tree is None:
        return None, f"исходник не разбирается ({bad})"
    found = mut.locate(tree)
    if found is None:
        return None, "узел не нашёлся на своём отрезке"
    if ast.dump(found) != mut.target:
        return None, "на месте узла другой"
    before = ast.dump(tree)
    node = mut.change(tree, found)
    want = ast.dump(tree)
    if want == before:
        # Тождество: без этой проверки попытка в скобках превращала `True`
        # в `(True)` — текст «изменился», дерево сошлось, мутант «годен»
        return None, "мутация не меняет дерево"
    why = "текст мутанта не совпал с мутированным деревом"
    for piece in (node, _Parens(node)):
        text = patch_source(source, piece)
        if text is None:
            why = "замена не собралась"
            continue
        if text == source:
            why = "замена не изменила текст"
            continue
        got_tree, _ = parse_source(text)
        if got_tree is None:
            why = "текст мутанта не разбирается"
            continue
        if ast.dump(got_tree) == want:
            return text, ""
        why = "текст мутанта не совпал с мутированным деревом"
    return None, why


def tests_for(root: pathlib.Path, module: pathlib.Path) -> list[str]:
    """Тесты, которые вообще могут заметить поломку в этом модуле.

    Гоняем не весь набор: полный прогон на каждого мутанта — это часы.
    Ищем по имени модуля в тексте тестов; не нашли — берём весь набор,
    честно и медленно, потому что «не нашли» не значит «не проверяют».
    """
    name = re.escape(module.stem)
    # По ИМПОРТУ, а не по любому вхождению имени: поиск подстрокой цеплял
    # файлы, где имя модуля просто упомянуто в строке или комментарии. Форма
    # пакета (`src/<пакет>/…`) добавила точечные хвосты: модуль приходит как
    # `import пакет.модуль`, `from пакет.модуль import …` или
    # `from пакет import модуль` — все три написания обязаны находиться (№424).
    imported = re.compile(
        rf"^\s*(?:import\s+(?:[\w.]+\.)?{name}\b"
        rf"|from\s+(?:[\w.]+\.)*{name}(?:\.\w+)*\s+import"
        rf"|from\s+[\w.]+\s+import\s+[^#\n]*\b{name}\b)", re.M)
    # Часть модулей живёт только через подпроцесс (CLI-вход): импорта нет, а
    # тест их гоняет. Без этого весь набор шёл бы на каждого мутанта — часы
    # вместо минут (ревью 20.08, DeepSeek).
    # Рядом с запуском, а не просто где-то в тексте: имя модуля в
    # комментарии тянуло за собой лишний файл (ревью 20.08, локальная).
    spawned = re.compile(
        rf"(?:subprocess\.\w+|Popen|check_call|check_output|run)\s*\("
        rf"[^)]*['\"][^'\"]*{name}\.py['\"]", re.S)
    # Загрузка по пути, без import: `spec_from_file_location("X", …)` и хелпер
    # `_load("X")`. Так тесты берут свежую копию модуля, у которого изменяемое
    # состояние на уровне модуля (ночная ревизия досье: FAILED_STEPS, CLI_DOWN),
    # — переписывать их на import нельзя. Без этого модуль с тестами судился
    # всем набором: в CI около 3 мин на мутанта, а локально база не укладывалась
    # в лимит, и прогон отказывал целиком (№356). Лишнее совпадение только
    # добавит тестов в подмножество — ошибка в безопасную сторону. Строка,
    # где до вызова стоит `#`, — закомментированная загрузка, не загрузка: она
    # увела бы модуль от запасного «весь набор» в файл, который его не
    # исполняет (Sonnet, круг 1 по №356).
    loaded = re.compile(
        rf"^[^#\n]*(?:spec_from_file_location|\b_load)\s*\(\s*['\"]{name}['\"]",
        re.M)
    hits = [str(p.relative_to(root))
            for p in sorted((root / "tests").rglob("test_*.py"))
            if imported.search(t := p.read_text(encoding="utf-8"))
            or spawned.search(t) or loaded.search(t)]
    # Пусто — не значит «никто не проверяет»: модуль мог приехать через
    # чужой импорт. Берём весь набор: честно и медленно лучше, чем быстро
    # и мимо.
    return hits or ["tests"]


#: Худший прогон набора — во столько раз дольше `--timeout`: потолок теста в
#: pytest один, а тестов в наборе много. Этот же множитель держит бюджет прогона
#: и потолок шага мутатора в CI (`tests/test_workflows.py`, №395).
WORST_RUN_FACTOR = 4


def run_tests(cwd: pathlib.Path, targets: list[str], timeout: int) -> bool:
    """True — прогон зелёный (мутант выжил, тесты его не заметили)."""
    # Без байткода. Python сверяет .pyc с исходником по mtime (секунды) и
    # размеру: два мутанта одной длины, записанные в одну секунду, для него
    # один файл — второй исполняется байткодом первого, и вердикт достаётся
    # чужому коду. Так 21.08 «выжил» мутант 6.0→0 в stt_runtime, под который
    # тест написан и который вручную падает. Врёт в обе стороны: убитый сосед
    # прикрывает выжившего и наоборот. Переменная окружения, а не только
    # `-B`: тесты запускают демон подпроцессом, ему тоже нельзя писать .pyc.
    # Корень данных мутатора набору не передаётся: дверь канона опубликовала
    # его в окружение (№440), а набор называет свой. Снимает его и conftest, но
    # мутант самой обвязки открыл бы тестам живые данные владельца (выходной
    # круг 1 по №440, M2).
    env = {**{k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"},
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        # Без `-x`: он останавливал прогон на первой ошибке, и упавший по
        # окружению тест выдавал бы «мутант убит» независимо от мутации.
        # `-p no:xdist`: прогон мутанта — один процесс. Полные прогоны идут с
        # `-n` (№453), и `-n` из `addopts` или `PYTEST_ADDOPTS` молча сделал бы
        # параллельным каждый прогон мутанта; с выключенным плагином такой `-n` —
        # громкий отказ разбора (код 4), база краснеет, мутатор останавливается.
        r = subprocess.run([sys.executable, "-B", "-m", "pytest", *targets, "-q",
                            "-p", "no:cacheprovider", "-p", "no:xdist", "--timeout", str(timeout)],
                           cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout * WORST_RUN_FACTOR)
    except subprocess.TimeoutExpired:
        return False          # завис — считаем убитым: поведение изменилось
    return r.returncode == 0


# Причина в отчёте, пока прогон идёт: оборвёт раннер — на диске останется она
RUNNING = "прогон не дошёл до конца плана"
#: Остановка до первого мутанта: база ещё идёт или покраснела. Отчёт пишется и
#: тогда — база с худшим случаем 4 × --timeout на набор бывает самым длинным этапом
#: прогона, и job, оборванный на ней, раньше не оставлял отчёта вовсе (DeepSeek по
#: PR #640, та же дыра в #637).
BASE_RUNNING = "базовый прогон не закончен"
BASE_RED = "база красная"
#: Копия дерева не собралась (клон или checkout): план есть, отчёт и машинная
#: строка пишутся, как у красной базы; исход — «сломалась сама проверка».
COPY_FAILED = "копия дерева не собралась"

# Шов часов: бюджет прогона тесты судят подменённым временем, а не сном
clock = time.monotonic


def suite_key(work: pathlib.Path, module: pathlib.Path) -> tuple[str, ...]:
    """Набор тестов модуля как ключ длительностей. Одна функция на обе точки:
    базовый прогон пишет длительность под этим ключом, мутант читает её под
    ним же — разойдись они, оценки у мутанта не нашлось бы (№395). Шов набора
    по-прежнему один — `tests_for`."""
    return tuple(tests_for(work, module))


def timed_run(cwd: pathlib.Path, suite: tuple[str, ...], timeout: int) -> tuple[bool, float]:
    """`run_tests` с замером: (исход, секунды). Обёртка, а не новая подпись
    `run_tests`: её подменяют тесты."""
    t0 = clock()
    ok = run_tests(cwd, list(suite), timeout)
    return ok, clock() - t0


def fits(budget_s: float | None, started: float, need: float) -> bool:
    """Хватит ли остатка бюджета на `need` секунд. Без бюджета — всегда."""
    return budget_s is None or budget_s - (clock() - started) >= need


def render_report(f: Facts, skipped: list | None = None,
                  unreadable: list[str] | None = None) -> str:
    """Отчёт о проверенном к этому моменту. Пишется после каждого мутанта: job,
    оборванный раннером на потолке, прежде уносил с собой и отчёт — тот писался
    один раз в конце (№395). Выборка названа своей строкой, а не «срезом»: не
    взятое выборкой — политика (№469), не судившееся из доли — неполнота."""
    skipped = skipped or []
    untried = f.M - f.skipped - f.tested
    aborted = f.aborted or "сбой"
    head = f"Проверено мутантов: {f.tested} из {f.M}"
    if untried:
        head += f" (остановка: {aborted})"
    crit = f.critical_survivors
    head += f", выжило: {len(f.survivors)}"
    if f.survivors:
        head += f" (в критичных зонах: {len(crit)})"
    lines = [head,
             f"Выборка: {f.P} из {f.full} мутантов плана; критичных в плане {f.critical_full}, "
             f"в выборке {f.critical_sampled}"
             + (f" — {f.zones_note}" if f.zones_note else "")]
    if untried:
        lines.append(f"НЕ СУДИЛОСЬ: {untried} (прервано: {aborted}) — "
                     "это НЕ значит «там всё хорошо».")
    if f.skipped:
        lines.append(f"НЕ ПРИМЕНИЛОСЬ: {f.skipped} — результат неполон.")
    if f.unread:
        lines.append(f"НЕ ПРОЧИТАНО файлов: {f.unread} — их строки не судились.")
        lines += [f"  НЕ ПРОЧИТАН {entry}" for entry in unreadable or []]
    for m, why_not in skipped:
        lines.append(f"  НЕ ПРИМЕНИЛОСЬ {m} — {why_not}")
    for row in sorted(f.survivors, key=lambda r: (not r["critical"], r["path"], r["line"])):
        zone = "критичная зона — держит мерж" if row["critical"] else "вне критичных зон"
        lines.append(f"  ВЫЖИЛ {row['path']}:{row['line']}: {row['what']} — {zone}")
    if f.survivors:
        lines.append("")
        lines.append("Выживший мутант — это изменение поведения, которого не "
                     "заметил ни один тест. Либо тест на это место есть, но "
                     "он ничего не держит, либо места в тестах нет вовсе.")
    return "\n".join(lines)


def _max_arg(value: str) -> int | None:
    """`--max N` — потолок мутантов; `--max all` — без среза (None).

    Потолок CI поднимается до полного плана шарда: план четырёх шардов при
    `--max all` судится целиком, а не первыми шестьюдесятью.
    """
    if value == "all":
        return None
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r}: целое число или all")
    if n < 1:
        # Ноль при непустом плане давал пустую выборку и зелёное «нечего» (входной
        # круг 2 по №469, Sonnet I2): выборка из ничего — не проверка
        raise argparse.ArgumentTypeError("выборка — хотя бы один мутант (или all)")
    return n


def _shard_arg(value: str) -> tuple[int, int]:
    """`--shard K/N` — доля плана `i % N == K-1`; неверное значение — ошибка аргумента.

    Разбор — типом argparse, как у `--max`: отказ с usage и кодом 2 до git-корня,
    дерева и лока. Не код 5: он значит «не назван корень данных», и проба
    контракта запуска обязана отличать его от опечатки во флаге
    (`exit_codes.EXIT_ROOT_UNNAMED`; выходной круг 1 по №441, DS M5).
    """
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", value)
    if m is None:
        raise argparse.ArgumentTypeError(f"{value!r}: ожидается K/N")
    k, n = int(m.group(1)), int(m.group(2))
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError(f"{value!r}: нужно 1 ≤ K ≤ N")
    return (k, n)


def _resume_arg(value: str) -> str:
    """`--resume КЛЮЧ` — 16 шестнадцатеричных знаков, как печатает прогон: ключ идёт
    в имя файла и в глоб журнала, `*` или `../` читали бы чужое (Sonnet M4)."""
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        raise argparse.ArgumentTypeError(f"{value!r}: ключ прогона — 16 знаков 0-9a-f")
    return value


#: Потолок `--jobs`. Замер №444 B (27.09, машина владельца): четыре доли разом на
#: одном `.git` — 59 с против 193 с последовательно (3,3×), выживших 0 = 0; четыре
#: полных pytest разом — 266–274 с против 253 с у одного. Потолок прогона мутанта —
#: WORST_RUN_FACTOR × `--timeout` (480 с при умолчании), и нагрузка четырёх долей до
#: него не доводит. Больше четырёх не мерено.
JOBS_MAX = 4


def _jobs_arg(value: str) -> int:
    """`--jobs N` — сколько долей плана гнать параллельно; 1 ≤ N ≤ JOBS_MAX."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r}: целое число от 1 до {JOBS_MAX}")
    if not 1 <= n <= JOBS_MAX:
        raise argparse.ArgumentTypeError(f"{value!r}: нужно 1 ≤ N ≤ {JOBS_MAX}")
    return n


#: Флаги, которые родитель `--jobs` пересылает долям (`child_argv`), и флаги только
#: родителя — доли им он чеканит сам. Каждый флаг парсера стоит ровно в одном списке,
#: тест держит это: новый флаг без решения «пересылать ли» краснеет, а не теряется
#: у долей молча.
CHILD_FORWARDED = ("range", "timeout", "budget_s", "force", "max", "resume")
PARENT_ONLY = ("jobs", "shard", "report", "merge_shards")


def check_pair(ap: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Сочетания флагов, которых разбор по одному не видит, — отказ кодом 2, как у
    ошибки аргумента, и до любого раннего возврата (`--merge-shards`).

    `--jobs N > 1` режет план на доли сам: с `--shard` вышла бы доля доли, а её
    грамматика `K/N` не выражает (круг 4 по №444 B); со слиянием сводить нечего.
    Конечный `--max` с долями сочетается: выборка — это план, каждая доля строит
    ту же выборку и берёт из неё свои индексы (№469)."""
    if args.jobs == 1:
        return
    if args.shard is not None:
        ap.error("--jobs N > 1 делит план на доли сам — --shard с ним не сочетается")
    if args.merge_shards is not None:
        ap.error("--jobs N > 1 гоняет доли, а --merge-shards сводит готовые — выбери одно")


#: Отказ замка — нейтральный: другой мутатор больше не помеха (замок разделяемый);
#: не взят он, только если ретраи против чужой пробы кончились или ФС без flock.
LOCK_REFUSED = ("замок мутатора не взят (ретраи против чужой пробы кончились или ФС без flock) — "
                "повтори запуск")
#: Доля родителем не бывает. Долю узнаём по `--shard`: его ставит ей `child_argv`, а
#: родителю `--shard` запрещён (`check_pair`), — отдельной метки не нужно, а метка в
#: окружении ушла бы в pytest каждого прогона доли. Без этой проверки мутант входа
#: (`jobs > 1` → `>= 1`) делал каждую долю родителем: цепочка мутаторов в новых
#: сессиях росла мимо убийства прогона, и доля CI умирала (PR #667: код 143, 60 сирот).
SHARE_REFUSED = "доля (--shard) не запускает долей — родителем бывает только прогон без --shard"


#: Суффикс машинной строки шарда рядом с отчётом: `<report>` + он. Одно имя на троих —
#: писателя (`write_artifacts`), судью (`_shard_rows`) и шаг выгрузки артефакта в CI;
#: сторож workflow собирает глоб артефакта из этой константы (выходной круг 1 по №441,
#: DS I3: переименование суффикса иначе красило бы вердикт на каждом PR).
SHARD_LINE_SUFFIX = ".json"


def shard_line_path(report: pathlib.Path) -> pathlib.Path:
    """Где лежит машинная строка шарда при отчёте `report` — одно правило имени для
    писателя и для сторожа выгрузки артефакта в CI."""
    return report.with_name(report.name + SHARD_LINE_SUFFIX)


def write_artifacts(report: pathlib.Path | None, text: str, f: Facts, rc: int) -> None:
    """Отчёт и машинная строка шарда рядом с ним — одна точка записи.

    Отчёт пишется после каждого мутанта (job, оборванный на потолке, уносил
    бы его с собой), и там же кладётся `<report>.json`: факты прогона (`Facts`),
    версия формата и слово исхода — для чтения человеком; судья CI слово не
    читает, он считает его заново по фактам (№469). Без `--report` класть некуда."""
    if not report:
        return
    report.parent.mkdir(parents=True, exist_ok=True)
    _replace_text(report, text + "\n")
    _replace_text(shard_line_path(report),
                  json.dumps({"v": FACTS_VERSION, **dataclasses.asdict(f),
                              "word": exit_codes.outcome(rc)}, ensure_ascii=False) + "\n")


def _replace_text(path: pathlib.Path, text: str) -> None:
    """Запись целиком или никак: остановка между усечением и записью оставляла
    пустой файл фактов, и судья читал его как нечитаемый (выходной круг 1 по №469,
    Sonnet M2)."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _shard_rows(directory: pathlib.Path) -> tuple[list[Facts], str]:
    """Факты шардов из каталога и причина, если какой-то файл не прочесть.

    Обходим рекурсивно: CI раскладывает артефакты шардов по своим подкаталогам.
    Нечитаемый файл не стирает прочитанные: сводка показывает шарды, которые
    отработали, рядом с причиной красного (выходной круг 2 по №441, DS M2). Файл
    старого формата (без `v` или с другой версией) — нечитаемый: судить его по
    новым правилам нельзя."""
    rows: list[Facts] = []
    bad: list[str] = []
    names = {f.name for f in dataclasses.fields(Facts)}
    for path in sorted(directory.rglob("*" + SHARD_LINE_SUFFIX)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Версия и ПОЛНЫЙ набор полей: у `Facts` все поля с умолчаниями, и строка
            # без `survivors` читалась бы как чистая (выходной круг 1 по №469, Sonnet M3)
            if data.get("v") != FACTS_VERSION or not names <= set(data):
                raise ValueError("формат")
            rows.append(Facts(**{k: v for k, v in data.items() if k in names}))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            bad.append(str(path))
    return rows, (f"нечитаемый файл шарда: {', '.join(bad)}" if bad else "")


def fold(rows: list[Facts]) -> Facts:
    """Сложить факты шардов одного прогона в факты всего прогона."""
    first = rows[0]
    return Facts(K=1, N=1, M=sum(r.M for r in rows), P=first.P, full=first.full,
                 tested=sum(r.tested for r in rows), skipped=sum(r.skipped for r in rows),
                 unread=max(r.unread for r in rows), nodes=first.nodes, lines_in=first.lines_in,
                 broken="; ".join(f"шард {r.K}: {r.broken}" for r in rows if r.broken),
                 broken_rc=max(r.broken_rc for r in rows),
                 aborted="; ".join(f"шард {r.K}: {r.aborted}" for r in rows if r.aborted),
                 critical_full=first.critical_full, critical_sampled=first.critical_sampled,
                 zones=first.zones, zones_note=first.zones_note, base=first.base, head=first.head,
                 run=first.run,
                 survivors=[s for r in rows for s in r.survivors])


def merge_shards(directory: pathlib.Path, report: pathlib.Path | None = None) -> int:
    """Свести факты шардов в один вердикт — код возврата CI (0/1).

    Таблица вердикта — здесь одна; CONTRIBUTING на неё ссылается:

    | состояние | исход | код |
    |---|---|---|
    | нечитаемый файл или старый формат, файлов не N, разные N, повтор или пропуск K, ΣM ≠ P, разные выборка, зоны или ревизии | «шарды не покрыли план» | 1 |
    | выжил мутант в критичной зоне, красная база или сбой подготовки | «держит мерж» | 1 |
    | выборка судилась не вся (бюджет, встреча, не применилось, не прочитан файл) | «неполный исход» | 1 |
    | выборка пуста, в строках нет кода | заметка «мутировать нечего» | 0 |
    | выборка пуста, код есть, операторов нет | предупреждение «слепое пятно мутатора» | 0 |
    | выборка судилась вся, критичных выживших нет | «шарды чисты» + список выживших вне критичных зон | 0 |

    Слово шарда не читается: исход считает `verdict_code` на сложенных фактах —
    тот же судья, что у одиночного прогона (№455, №469)."""
    directory = pathlib.Path(directory)
    rows, why = _shard_rows(directory)
    lines: list[str] = []
    problem = why
    if not problem and not rows:
        problem = "ни одного файла шарда"
    if not problem:
        ns = {r.N for r in rows}
        if len(ns) != 1:
            problem = "разные N"
        else:
            n = ns.pop()
            same = {(r.P, r.full, r.zones, r.base, r.head, r.run) for r in rows}
            if len(rows) != n:
                problem = f"файлов {len(rows)}, а шардов {n}"
            elif sorted(r.K for r in rows) != list(range(1, n + 1)):
                problem = "повтор или пропуск K"
            elif len(same) != 1:
                problem = "шарды судили разные выборки (P, план, зоны или ревизии разошлись)"
            elif sum(r.M for r in rows) != rows[0].P:
                problem = "ΣM ≠ P"
    for r in sorted(rows, key=lambda r: r.K):
        lines.append(f"  шард {r.K} из {r.N}: M={r.M}, судилось {r.tested} — "
                     f"{exit_codes.outcome(verdict_code(r))}")
    lines.append(f"итог: шардов {len(rows)}, M={sum(r.M for r in rows)}, "
                 f"P={rows[0].P if rows else 0}")
    if problem:
        lines.insert(0, f"шарды не покрыли план: {problem}")
        code = 1
    else:
        f = fold(rows)
        rc = verdict_code(f)
        noncrit = len(f.survivors) - len(f.critical_survivors)
        if rc == 0:
            head = f"мутация: шарды чисты — судилась вся выборка ({f.P} из {f.full}), " \
                   "критичных выживших нет"
            if noncrit:
                head += f"; выжили вне критичных зон: {noncrit} — списком ниже, мерж не держат"
            code = 0
        elif rc == EXIT_NOTHING_TO_CHECK:
            head, code = "мутировать нечего", 0
        elif rc == EXIT_UNMUTABLE:
            head, code = ("строки в диапазоне есть, а мутировать в них нечего — "
                          "слепое пятно мутатора, см. отчёты шардов"), 0
        elif f.broken:
            head, code = f"сломалась сама проверка: {f.broken}", 1
        elif f.critical_survivors:
            head, code = (f"держит мерж: выжили мутанты в критичных зонах — "
                          f"{len(f.critical_survivors)}"), 1
        else:
            unfinished = [r for r in rows if verdict_code(r) not in
                          (0, EXIT_NOTHING_TO_CHECK, EXIT_UNMUTABLE)]
            head = "шарды дали неполный исход: " + ", ".join(
                f"шард {r.K}: {exit_codes.outcome(verdict_code(r))}" for r in unfinished)
            code = 1
        lines.insert(0, head)
        lines.append(f"выборка {f.P} из {f.full}, судилось {f.tested}; "
                     f"критичных в плане {f.critical_full}, в выборке {f.critical_sampled}; "
                     f"зоны {f.zones or '—'}" + (f" ({f.zones_note})" if f.zones_note else ""))
        for row in sorted(f.survivors, key=lambda r: (not r["critical"], r["path"], r["line"])):
            zone = "критичная зона — держит мерж" if row["critical"] else "вне критичных зон"
            lines.append(f"  ВЫЖИЛ {row['path']}:{row['line']}: {row['what']} — {zone}")
    text = "\n".join(lines)
    print(text)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text + "\n", encoding="utf-8")
    return code


#: Сколько ждать доли после SIGINT родителя, прежде чем добить их группы SIGKILL:
#: доле нужно время на свой `finally` — отпустить замок и убрать копию.
CHILD_STOP_GRACE_S = 30


class _Stopped(Exception):
    """Родитель `--jobs` получил SIGINT или SIGTERM."""

    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum


def _raise_stopped(signum, frame):
    """Обработчик остановки: ПЕРВЫМ делом глушит оба сигнала, потом поднимает
    `_Stopped`. Глушить в `except` вызывающего поздно: второй Ctrl-C или повторный
    SIGTERM в зазоре до этих строк поднимал второй `_Stopped` изнутри `except`, и
    доли оставались без остановки (выходной круг 1 по №444 B, I1). Вложенный вызов
    обработчика в его же первых строках даёт тот же один `_Stopped`."""
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal.SIG_IGN)
    raise _Stopped(signum)


def child_argv(args: argparse.Namespace, k: int, n: int, logs: pathlib.Path,
               rng: str) -> list[str]:
    """Команда доли `k` из `n` — обычный мутатор этого же скрипта. Диапазон — SHA,
    разрешённые родителем один раз: доли судят один коммит, сдвиг HEAD их не
    разводит. Отчёт и машинная строка доли — в каталоге журналов родителя. Путь
    скрипта — от корня кода канона: доля обязана быть этим же деревом кода."""
    import charoite_paths  # noqa: E402
    script = charoite_paths.code_root(__file__) / "scripts" / "mutate_check.py"
    argv = [sys.executable, str(script), "--range", rng, "--timeout", str(args.timeout)]
    if args.budget_s is not None:
        argv += ["--budget-s", str(args.budget_s)]
    if args.force:
        argv.append("--force")
    if args.resume:
        argv += ["--resume", args.resume]
    # Тот же `--max`, что у родителя: выборка — это план, доли берут индексы из неё
    # (№469); `all` у доли при конечном потолке судил бы полный план вместо выборки
    return argv + ["--max", "all" if args.max is None else str(args.max),
                   "--shard", f"{k}/{n}", "--report", str(logs / f"{k}.txt")]


def signal_group(p: subprocess.Popen, sig: int) -> bool:
    """Сигнал группе доли; False — группу, где остался живой процесс, достать не
    удалось.

    Замер 30.09 (macOS, python 3.12): `killpg` группы, в которой остался только
    непожатый зомби-лидер, отвечает `PermissionError`; после `poll()` тот же вызов —
    `ProcessLookupError`. Так 30.09 родитель упал на SIGTERM: одна доля закончила,
    родитель ждал соседнюю, и остановка застала первую зомби. Лечение — пожать и
    повторить. Проверки «лидер жив» перед сигналом нет: лидер доли может выйти, а
    pytest его группы — жить (входной круг 2 по №469, Sonnet I6)."""
    for attempt in range(2):
        try:
            os.killpg(p.pid, sig)
            return True
        except ProcessLookupError:
            return True                   # группы нет — останавливать некого
        except PermissionError:
            if attempt:
                return False
            p.poll()                      # пожать зомби-лидера и повторить
    return False


def stop_children(procs: list[subprocess.Popen], grace: float = CHILD_STOP_GRACE_S) -> list[int]:
    """Остановить доли: каждой группе SIGINT — у доли это KeyboardInterrupt и её
    `finally` (замок, копия), — ждать всех до `grace` секунд, оставшимся — SIGKILL
    всей группе: pytest внутри доли живёт в той же группе. Возвращает номера долей
    (с 1), чью группу не удалось достать, — их называет строка вердикта."""
    failed: set[int] = set()
    for k, p in enumerate(procs, 1):
        if not signal_group(p, signal.SIGINT):
            failed.add(k)
    deadline = time.monotonic() + grace
    for p in procs:
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=max(0.0, deadline - time.monotonic()))
    for k, p in enumerate(procs, 1):
        if signal_group(p, signal.SIGKILL):
            failed.discard(k)
        else:
            failed.add(k)
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=grace)
    return sorted(failed)


def stopped_line(logs: pathlib.Path, signum: int, failed: list[int]) -> str:
    """Строка вердикта остановленного родителя: «прервано: N из M» по фактам долей
    (они пишутся после каждого мутанта) и как продолжить."""
    rows, _ = _shard_rows(logs)
    tested = sum(r.tested for r in rows)
    total = rows[0].P if rows else 0
    run = rows[0].run if rows else ""
    line = f"прервано сигналом {signum}: {tested} из {total}"
    if run:
        line += f" — продолжить: --resume {run}"
    if failed:
        line += "; не добиты доли: " + ", ".join(map(str, failed))
    return line


def run_jobs(args: argparse.Namespace, rng: str, data_root: pathlib.Path) -> int:
    """Родитель `--jobs N`: N обычных мутаторов-долей `--shard k/N` с тем же
    `--max` параллельно; исход — только `merge_shards` по их машинным строкам, код —
    политика CI 0/1 (канон слов долей — №455).

    Родитель держит свой разделяемый замок мутатора всю жизнь: ночь и сторож видят
    «мутация идёт» и в зазоре между стартами долей. Доли — обычные мутаторы: свой
    гвард старта, свой замок, своя копия, свой бюджет и потолки прогонов; у
    родителя своего потолка нет. Каталог журналов не удаляется: в отчётах долей —
    выжившие, их текст родитель не разбирает.
    """
    if args.shard is not None:
        print(SHARE_REFUSED)
        return 2
    # Журнал продолжения — до долей: иначе каждая доля отказывала бы в своём
    # журнале, а родитель печатал «ни одного файла шарда» вместо причины (выходной
    # круг 1 по №469, Sonnet I4). Ключ выборки сверяют сами доли; их отказ — ниже.
    if args.resume and not journal_read(data_root, args.resume)[0]:
        print(f"подготовка не удалась: журнал прогона {args.resume} не найден или истёк "
              f"(ретеншн logs/) — запусти прогон без --resume")
        return 1
    import busy_signals  # noqa: E402
    lock = busy_signals.MutationLock(data_root)
    if not lock.acquire():
        print(LOCK_REFUSED)
        return 3
    procs: list[subprocess.Popen] = []
    try:
        logs = pathlib.Path(tempfile.mkdtemp(prefix="mutate-jobs-"))
        n = args.jobs
        print(f"доли: {n}, журналы и отчёты — {logs}")
        # Обработчики — ДО запуска долей: exec сбрасывает перехваченный сигнал в
        # SIG_DFL, а игнорируемый наследуется. Родитель, запущенный из фона с
        # SIGINT = SIG_IGN, иначе раздал бы долям игнор, и SIGINT остановки не
        # дошёл бы до их `finally`.
        prev = {s: signal.signal(s, _raise_stopped) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            for k in range(1, n + 1):
                with open(logs / f"{k}.log", "wb") as log:
                    procs.append(subprocess.Popen(child_argv(args, k, n, logs, rng),
                                                  stdout=log, stderr=subprocess.STDOUT,
                                                  env=env, start_new_session=True))
                print(f"  доля {k}/{n}: pid {procs[-1].pid}")
            for p in procs:
                p.wait()
        except _Stopped as stop:
            print(f"⏹ сигнал {stop.signum} — останавливаю доли: SIGINT, через "
                  f"{CHILD_STOP_GRACE_S} с — SIGKILL")
            failed = stop_children(procs)
            print(f"доли остановлены, журналы — {logs}")
            # Итог не теряется: доли пишут факты после каждого мутанта, журнал
            # рассуждённых — в корне данных. Код — сигнальный (128+signum): его
            # читают фоновые обёртки; «сколько успели» — строкой (№469)
            print(stopped_line(logs, stop.signum, failed))
            return 128 + stop.signum
        except BaseException:
            stop_children(procs)
            raise
        finally:
            for s, h in prev.items():
                signal.signal(s, h)
    finally:
        lock.release()
    code = merge_shards(logs, args.report)
    # Отказ подготовки доли (чужой ключ, копия не собралась) живёт в её журнале —
    # поднять его в вывод родителя, а не оставить только «шарды не покрыли план»
    for k in range(1, len(procs) + 1):
        with contextlib.suppress(OSError):
            for line in (logs / f"{k}.log").read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("подготовка не удалась"):
                    print(f"  доля {k}: {line}")
    for k, p in enumerate(procs, 1):
        print(f"  доля {k}: код {p.returncode}, журнал {logs / f'{k}.log'}, "
              f"отчёт {logs / f'{k}.txt'}")
    return code


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", default="origin/main...HEAD",
                    help="диапазон git, чьи строки мутируем")
    ap.add_argument("--max", type=_max_arg, default=SAMPLE_MAX,
                    help="выборка: до N мутантов на диапазон — критичные зоны первыми, "
                         "поровну по файлам, по хешу личности мутанта; «all» — весь план")
    ap.add_argument("--timeout", type=int, default=120, help="секунд на прогон")
    ap.add_argument("--report", type=pathlib.Path, help="куда сложить отчёт")
    ap.add_argument("--shard", type=_shard_arg, default=None,
                    help="доля выборки K/N (1 ≤ K ≤ N): берёт мутантов с индексом "
                         "i %% N == K-1 из выборки --max")
    ap.add_argument("--merge-shards", type=pathlib.Path, default=None,
                    help="каталог с машинными строками шардов (*.json): свести их "
                         "вердиктом вместо прогона")
    ap.add_argument("--force", action="store_true",
                    help="стартовать, даже если машина занята встречей, разбором "
                         "или ночным циклом; другие мутаторы помехой не считаются и без него")
    ap.add_argument("--budget-s", type=float, default=None,
                    help="секунд на весь прогон от старта; не хватает на следующий "
                         "набор — остановка с отчётом о проверенном (--force не снимает)")
    ap.add_argument("--jobs", type=_jobs_arg, default=1,
                    help=f"гнать выборку N долями параллельно (1 ≤ N ≤ {JOBS_MAX}): "
                         "родитель запускает N обычных мутаторов с --shard k/N и тем же "
                         "--max и сводит их --merge-shards. Параллельные прогоны разрешены: "
                         "замок мутатора разделяемый, а четыре доли на одном .git нашли "
                         "тех же выживших втрое быстрее (замер №444 B)")
    ap.add_argument("--resume", type=_resume_arg, default=None, metavar="КЛЮЧ",
                    help="продолжить прерванный прогон: судить только мутантов, которых "
                         "нет в журнале рассуждённых logs/mutation_run-<КЛЮЧ>-*.jsonl; "
                         "ключ печатает каждый прогон")
    return ap


def main(argv: list[str]) -> int:
    # Бюджет считается от старта процесса: подготовка дерева и база входят в него
    started = clock()
    ap = build_parser()
    args = ap.parse_args(argv[1:])
    check_pair(ap, args)

    # Слияние шардов — ПЕРВЫМ: ему не нужны ни git-корень, ни гвард занятости,
    # ни лок. Отдельный job CI сходится сюда на голом каталоге артефактов вне
    # git-дерева, и любой шаг до этого был бы лишним отказом.
    if args.merge_shards is not None:
        return merge_shards(args.merge_shards, args.report)

    # Свой `src/` уже первым в sys.path (вставка у импорта модуля): канон и
    # сигналы занятости берутся из дерева самого скрипта, а не из git-корня
    # текущего каталога. Вторая вставка собирала процесс из двух деревьев —
    # мутатор одного клона, запущенный из другого, падал на сверке корня
    # кода трассировкой (круг 1 по коду №331, Opus M2).
    import busy_signals  # noqa: E402
    import charoite_paths  # noqa: E402
    # Корень ДАННЫХ мутатор НАЗЫВАЕТ, а не угадывает (№440): лок демона
    # владельца, ночь и лок мутации живут в корне данных, а не в дереве, из
    # которого запущен прогон. Угадывание (`resolve_root`) из рабочего дерева
    # без переменной отвечало самим деревом — гвард «идёт встреча» смотрел
    # мимо лока демона, а лок мутатора ложился не туда, где его ждёт ночь.
    # Дверь канона: без `CHAROITE_ROOT` — отказ её текстом и её кодом, до
    # плана и лока. Слиянию шардов корень не нужен — оно выше.
    data_root = charoite_paths.name_data_root_or_exit(__file__)
    print(f"корень данных: {data_root}")

    shard = args.shard            # неверное значение отвергнуто разбором аргументов

    root = pathlib.Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                       capture_output=True, text=True,
                                       check=True).stdout.strip())
    # Диапазон — в SHA один раз, здесь: дифф, чтение файлов плана и копия
    # получают только разрешённое, и сдвиг HEAD посреди прогона не разводит
    # план и копию по разным коммитам (№460). Слова человека (`args.range`)
    # остаются для печати.
    try:
        rng = resolve_range(root, args.range)
    except PreparationError as e:
        print(f"подготовка не удалась: {e}")
        return 1
    print(f"диапазон: {args.range} = {rng}")
    # Координация с живым контуром (ночь 23→24.08: мутатор делил qwen35b со
    # встречей и с ночным циклом — 35 ReadTimeout по 300 с у досье, прогон
    # оборван руками в 10:28). Правила: (1) на старте машина занята встречей
    # или разбором — честный отказ, не тихая толкотня; (2) на время прогона
    # лежит logs/mutation.lock — ночь (wait_for_idle) видит нас и ждёт;
    # (3) началась живая встреча — прерываемся между мутантами.
    # Один вызов гварда старта на все роли — одиночный прогон, родитель `--jobs` и
    # его доли: другой мутатор помехой не считается (замок разделяемый, №444 B).
    if busy_guard(args):
        busy = busy_signals.machine_busy(data_root, count_mutation=False)
        if busy:
            print(f"машина занята ({', '.join(busy)}) — мутатор не стартует "
                  "(--force, чтобы настоять)")
            return 3
    if args.jobs > 1:
        return run_jobs(args, rng, data_root)
    try:
        plan, totals = plan_for(root, rng, shard, args.max)
    except PreparationError as e:
        print(f"подготовка не удалась: {e}")
        return 1
    totals.run = run_key(rng, args.max, args.timeout, totals.sample)
    k, n = totals.shard or (1, 1)
    left, _, right = split_range(rng)
    facts = Facts(K=k, N=n, M=len(plan), P=totals.planned, full=totals.full,
                  unread=totals.files_unreadable, nodes=totals.nodes, lines_in=totals.lines_in,
                  critical_full=totals.critical_full, critical_sampled=totals.critical_sampled,
                  zones=totals.zones, zones_note=totals.zones_note, base=left, head=right,
                  run=totals.run)
    if totals.zones_note:
        print(f"зоны мутатора: {totals.zones_note}")
    if not plan:
        code = verdict_code(facts)
        # Машинная строка — на ЛЮБОМ выходе прогона с шардом: без неё вердикт
        # видит «ни одного файла шарда» и краснеет на каждом PR без python-строк
        # (выходной круг 1 по №441, DS C1). P = 0 — диагноз всего диапазона,
        # одинаковый у всех шардов; P > 0 при пустой доле — «нечего» ЭТОГО
        # шарда: его мутантов нет, соседи судят свою часть.
        if shard is not None:
            write_artifacts(args.report, render_report(facts, unreadable=totals.unreadable),
                            facts, code)
            if totals.planned:
                print(f"шард {k} из {n}: 0 из {totals.planned} — нечего")
                return code
        if code == EXIT_NOTHING_TO_CHECK and not totals.lines_in:
            print(f"В {args.range} нет изменённых строк в коде продукта — ломать нечего.")
        elif code == EXIT_NOTHING_TO_CHECK:
            print(f"В изменённых строках нет кода — ломать нечего: файлов {totals.files_in}, "
                  f"строк {totals.lines_in}, из них констант модуля {totals.lines_constant}, "
                  f"узлов AST {totals.nodes}.")
        elif code == EXIT_UNMUTABLE:
            print(f"Изменённые строки не содержат ничего мутируемого: файлов {totals.files_in}, "
                  f"строк {totals.lines_in}, из них констант модуля {totals.lines_constant}, "
                  f"узлов AST {totals.nodes}, не прочитано файлов {totals.files_unreadable}.")
        else:
            print(f"План пуст, но проверено не всё: не прочитано файлов "
                  f"{totals.files_unreadable} из {totals.files_in} — это НЕ «нечего мутировать».")
            for entry in totals.unreadable:
                print(f"  НЕ ПРОЧИТАН {entry}")
        return code

    print(f"Мутантов к проверке: {len(plan)} (выборка {totals.planned} из {totals.full}; "
          f"критичных в плане {totals.critical_full}, в выборке {totals.critical_sampled})")
    print(f"ключ прогона: {totals.run} — продолжить прерванный: --resume {totals.run}")
    # Журнал рассуждённых — в корне данных, до лока и копии: `--resume` с чужим
    # ключом или без журнала — отказ до любой работы
    try:
        done = journal_open(data_root, totals, k, rng, args)
    except PreparationError as e:
        print(f"подготовка не удалась: {e}")
        return 1
    import charoite_paths  # noqa: E402
    journal = charoite_paths.log_path(data_root, RUN_LOG_KIND, part=f"{totals.run}-{k}",
                                      suffix=".jsonl")

    # Лок — ДО подготовки копии: отказ не должен оставлять за собой каталог
    # копии (круг-2 по PR #399, DS Minor).
    lock = busy_signals.MutationLock(data_root)
    if not lock.acquire():
        print(LOCK_REFUSED)
        return 3
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutate-"))
    skipped: list[tuple[Mutation, str]] = []
    durations: dict[tuple[str, ...], float] = {}
    # Рассуждённые прежним прогоном того же ключа — исходы из журнала, не заново
    todo: list[Mutation] = []
    for mut in plan:
        row = done.get(mut.key)
        if row is None:
            todo.append(mut)
        elif row["outcome"] == "skipped":
            skipped.append((mut, row.get("why", "не применилось в прежнем прогоне")))
            facts.skipped += 1
        else:
            facts.tested += 1
            if row["outcome"] == "survived":
                facts.survivors.append(survivor_row(mut, root))
    if len(todo) < len(plan):
        print(f"из журнала прогона: {len(plan) - len(todo)} рассуждены, судим {len(todo)}")

    def save(reason: str, rc: int | None = None) -> None:
        facts.aborted = reason
        write_artifacts(args.report, render_report(facts, skipped, totals.unreadable),
                        facts, verdict_code(facts) if rc is None else rc)

    def note(mut: Mutation, outcome: str, seconds: float = 0.0, why: str = "") -> None:
        journal_add(journal, {"key": mut.key, "outcome": outcome, "s": round(seconds, 1),
                              **({"why": why} if why else {})})

    aborted = ""
    # Факты — на диск до копии и базы: остановка в первые минуты (база бывает
    # самым длинным этапом) иначе не оставляла бы ни счёта, ни ключа прогона
    save(BASE_RUNNING)
    try:
        # Копия — внутри `try`: провал клона или checkout отпускает лок и убирает
        # каталог в `finally`, а отчёт и машинная строка ложатся, как у красной
        # базы. Код — «сломалась сама проверка», не «план был, не судился» (№460).
        try:
            work = copy_tree(root, head_of(rng), tmp)
        except PreparationError as e:
            print(f"подготовка не удалась: {e}")
            facts.broken, facts.broken_rc = COPY_FAILED, 1
            save(COPY_FAILED)
            return 1
        # СНАЧАЛА чистый прогон. В отдельном дереве нет файлов из .gitignore —
        # ни моделей, ни конфига, ни данных, — и тесты там могут быть красными
        # сами по себе. Тогда КАЖДЫЙ мутант считается убитым, отчёт говорит
        # «выжило 0», и гейт проходит, не проверив ничего: инструмент врёт в
        # самую опасную сторону (ревью 20.08, DeepSeek).
        # Проверяем КАЖДОЕ подмножество, на котором будет судиться мутант, а
        # не только их объединение: тест, зелёный в общей куче, в одиночку
        # может падать — и тогда мутанты его модуля «убиты» без участия
        # мутации (ревью 20.08, DeepSeek).
        subsets = {suite_key(work, m.path) for m in todo}
        # Свежая копия байткода не содержит, но запрет записи не мешает
        # ЧТЕНИЮ уже лежащего .pyc — на всякий случай выметаем.
        for cache in work.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        print(f"Базовый прогон (без мутаций), наборов: {len(subsets)}…")
        # Явный цикл, а не включение: из него выходят по бюджету. Набор ещё не
        # мерили — берём худший случай, потолок `run_tests` (4 × --timeout).
        broken: list[tuple[str, ...]] = []
        for ts in sorted(subsets):
            if not fits(args.budget_s, started, WORST_RUN_FACTOR * args.timeout):
                aborted = "бюджет"
                print(f"⏹ бюджет — базовый прогон не уложится, мутанты не "
                      f"запускаются ({len(durations)}/{len(subsets)} наборов)")
                break
            ok, durations[ts] = timed_run(work, ts, args.timeout)
            if not ok:
                broken.append(ts)
            save(BASE_RUNNING)
        if broken:
            print("\nБАЗА КРАСНАЯ: без единой мутации падают наборы:")
            for ts in broken:
                print("  " + " ".join(ts))
            print("В отдельном дереве нет того, что лежит в .gitignore "
                  "(модели, конфиг, данные).\nМутанты этих модулей "
                  "засчитались бы убитыми — считать их бессмысленно.")
            facts.broken, facts.broken_rc = BASE_RED, 2
            save(BASE_RED)
            return 2
        for i, mut in enumerate([] if aborted else todo, 1):
            suite = suite_key(work, mut.path)
            # Бюджет — вне гварда занятости: `--force` идёт поверх встречи, но
            # не поверх потолка job. Оценка — замер этого набора в базе; таймаут
            # мутанта под остаток НЕ урезается: истёкший прогон считается
            # «убит», и урезание дало бы ложные убийства (№395).
            if not fits(args.budget_s, started, durations[suite]):
                aborted = "бюджет"
            # Живой контур и ночь важнее метрики: началась запись или ночной
            # цикл — прерываемся между мутантами (круг-1, DS: координация
            # была однонаправленной — ночь ждала нас, мы ночь не видели).
            elif busy_guard(args):
                if busy_signals.live_recording(data_root):
                    aborted = "живая встреча"
                elif busy_signals.night_running(data_root):
                    aborted = "ночной цикл"
            if aborted:
                print(f"⏹ {aborted} — прерываюсь ({facts.tested}/{len(plan)} "
                      "проверено, остальное не судилось)")
                break
            rel = mut.path.relative_to(root)
            target = work / rel
            original = target.read_text(encoding="utf-8")
            mutated, why = applied(mut, original)
            if mutated is None:
                # Не применилась — узел не тот, текст не собрался, дерево
                # не сошлось. Молчать нельзя: «0 выживших» из-за того, что
                # ничего не ломали, читается как «всё проверено»; а битый
                # текст в дереве дал бы «убит» без участия мутации.
                skipped.append((mut, why))
                facts.skipped += 1
                note(mut, "skipped", why=why)
                print(f"  [{i}/{len(todo)}] НЕ ПРИМЕНИЛОСЬ: {mut} — {why}")
                save(RUNNING)
                continue
            target.write_text(mutated, encoding="utf-8")
            try:
                alive, spent = timed_run(work, suite, args.timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            facts.tested += 1
            mark = "ВЫЖИЛ" if alive else "убит"
            zone = " (критичная зона)" if alive and mut.critical else ""
            print(f"  [{i}/{len(todo)}] {mark}: {mut}{zone}")
            if alive:
                facts.survivors.append(survivor_row(mut, root))
            note(mut, "survived" if alive else "killed", spent)
            # На диске всегда проверенное к этому моменту: оборвёт раннер —
            # отчёт скажет, сколько успели, а не пропадёт целиком
            save(RUNNING)
    finally:
        lock.release()
        shutil.rmtree(tmp, ignore_errors=True)

    save(aborted)
    print("\n" + render_report(facts, skipped, totals.unreadable))
    return verdict_code(facts)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
