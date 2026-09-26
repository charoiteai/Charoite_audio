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
мутация кладётся в отдельный git worktree и тесты гоняются оттуда же:
подпроцессы видят тот же мутантный код, что и импорт.

Мутируем ТОЛЬКО строки, изменённые в заданном диапазоне: полный прогон по
`src/audio.py` — это тысячи мутантов и часы, а по хункам диффа — минуты.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import os
import pathlib
import re
import shutil
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

    def __init__(self, path: pathlib.Path, node, what: str, change):
        self.path, self.line, self._bare, self.change = path, node.lineno, what, change
        self.what = f"{what} @{node.col_offset}-{node.end_col_offset}"
        self.kind, self.span = type(node), _span(node)
        self.target = ast.dump(node)

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


def head_of(rng: str) -> str:
    """Правый конец диапазона — та ревизия, чей КОД мы ломаем.

    Без этого worktree поднимался от текущего HEAD, а номера строк брались из
    чужого диапазона: мутации ложились мимо — в комментарии и пустые места,
    и «выжившими» объявлялось то, чего в коде нет. Поймано на первом же
    живом прогоне.
    """
    for sep in ("...", ".."):
        if sep in rng:
            right = rng.split(sep, 1)[1].strip()
            return right or "HEAD"
    return rng.strip() or "HEAD"


# Области «нашего python» — у сторожа раскладки, не свой литерал `src/`: PR только
# по `scripts/` давал job без единого мутанта и зелёный (входной круг №339, DS I7)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import layout_map  # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
# Путь от __file__, а не от корня данных: мутатор неотделим от репозитория —
# он делает git worktree из него же, и до вызова канона корня ещё не дошёл.
# «Два корня в одном процессе» (GLM I3, круг 5) здесь не расходятся: второго
# сценария, где скрипт лежит отдельно от src/, попросту нет.
from exit_codes import EXIT_NOTHING_TO_CHECK, EXIT_PARTIAL, EXIT_UNJUDGED, EXIT_UNMUTABLE  # noqa: E402
MUTATION_AREAS = layout_map.PYTHON_AREAS


@dataclasses.dataclass
class ScanReport:
    """Что нашёл разбор одного файла: мутанты и то, из чего их не вышло."""
    mutations: list
    lines_constant: int = 0     # строки диапазона, снятые как константы модуля
    nodes: int = 0              # узлы AST на оставшихся строках диапазона


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


def verdict_code(survivors: list, tested: int, planned: int, dropped: int, skipped: int,
                 totals: ScanTotals | None = None) -> int:
    """Исход прогона одним значением: 1 — найдены выжившие; `EXIT_NOTHING_TO_CHECK`
    — в изменённых строках нет кода (строк нет, или только комментарии и
    константы модуля); `EXIT_UNMUTABLE` — код в строках есть, а мутировать в нём
    нечего; `EXIT_UNJUDGED` — план был, не судился ни один мутант; `EXIT_PARTIAL`
    — судили не весь план (прервано встречей, срезано потолком, не применилось,
    файл не прочитался); 0 — проверен весь план, чисто.

    Функция от состояния, а не лестница `if` в конце `main`: в круге 3 такая
    лестница спрашивала `tested == 0` РАНЬШЕ полноты, и прогон, прерванный на
    первом же мутанте при плане из сорока, отвечал «проверять было нечего» —
    CI печатал это дословно. Обе головы круга 4 независимо (DS C1 = GLM 1).
    """
    totals = totals or ScanTotals()
    if survivors:
        return 1
    # План был, а не судился ни один мутант (бюджет съела база, прервано на первом,
    # ни один не применился) — не «проверено не всё», а «не проверено ничего»: под
    # `partial` CI пропускал это жёлтым (Important DeepSeek по PR #637).
    if planned and not tested:
        return EXIT_UNJUDGED
    # Непрочитанный файл — неполнота при любом плане: его строки не судились,
    # а пустой план из-за него — не «нечего» (№386).
    if totals.files_unreadable:
        return EXIT_PARTIAL
    if planned == 0:
        # Срез потолком оставляет пустой план, но проверять БЫЛО что: «нечего»
        # тут врёт ровно так же, как врал `tested == 0` в круге 4 (GLM I2).
        if dropped:
            return EXIT_PARTIAL
        # «Мутировать нечего» — только когда в строках есть код: правка одного
        # комментария или константы модуля иначе давала то же слово, что и
        # слепое пятно операторов, и слово переставало быть сигналом (критика
        # DS круга 1 по #630). Узлы считает `scan` по тем же строкам.
        return EXIT_UNMUTABLE if totals.nodes else EXIT_NOTHING_TO_CHECK
    if tested < planned or dropped or skipped:
        return EXIT_PARTIAL
    return 0


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


def scan(path: pathlib.Path, lines: set[int], source: str | None = None) -> ScanReport:
    """Что можно сломать в этих строках — и сколько там было из чего ломать."""
    try:
        tree = ast.parse(source if source is not None
                         else path.read_text(encoding="utf-8"))
    except SyntaxError:
        return ScanReport([])
    consts = _module_constants(tree)
    report = ScanReport([], lines_constant=len(lines & consts))
    lines = lines - consts
    found = report.mutations
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or ln not in lines:
            continue
        report.nodes += 1
        if isinstance(node, ast.Compare) and node.ops:
            op = type(node.ops[0])
            if op in CMP_SWAP:
                found.append(Mutation(path, node, f"{op.__name__} → {CMP_SWAP[op].__name__}",
                                      _swap_cmp))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAP:
            found.append(Mutation(path, node, f"{type(node.op).__name__} → "
                                              f"{BOOL_SWAP[type(node.op)].__name__}",
                                  _swap_bool))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            # `if not apply or not same:` → `if not apply:` / `elif not same:`
            # дала ноль мутантов: отрицание не ломалось вовсе (№386). Обратного
            # оператора («добавить not») нет: он удваивает план, а класс ошибок
            # тот же — перевёрнутое условие.
            found.append(Mutation(path, node, "not X → X", _drop_not))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                found.append(Mutation(path, node, f"{node.value} → {not node.value}",
                                      _swap_const(not node.value)))
            elif isinstance(node.value, (int, float)) and node.value not in (0,):
                found.append(Mutation(path, node, f"{node.value} → 0", _swap_const(0)))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP \
                and not _neutral(node):
            found.append(Mutation(path, node, f"{type(node.op).__name__} → "
                                              f"{BIN_SWAP[type(node.op)].__name__}",
                                  _swap_bin))
        elif isinstance(node, ast.Return) and node.value is not None \
                and not (isinstance(node.value, ast.Constant)
                         and node.value.value is None):
            # `return None` → `return None` — мутант-тождество, в отчёте он
            # неотличим от настоящей дыры (прогон партии D, 22.08)
            found.append(Mutation(path, node, "return X → return None", _drop_return))
    return report


def plan_for(root: pathlib.Path, rng: str) -> tuple[list[Mutation], ScanTotals]:
    """План мутантов по диапазону и счётчики того, из чего он собран."""
    targets = changed_lines(root, rng)
    totals = ScanTotals(files_in=len(targets),
                        lines_in=sum(len(ls) for ls in targets.values()))
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
            totals.files_unreadable += 1
            continue
        try:
            source = blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            # `git show` прочитал, но это не наш текст: та же неполнота, что и
            # выпавший файл, а не трассировка посреди плана (DS M1 круга 1 по #630)
            totals.files_unreadable += 1
            continue
        report = scan(path, lines, source)
        totals.lines_constant += report.lines_constant
        totals.nodes += report.nodes
        plan.extend(report.mutations)
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
    worktree, прогон падал на `SyntaxError`, а мутант засчитывался «убит» —
    ложь того же класса, что «нечего мутировать» при нуле мутантов (№386).
    Теперь мутант годен, только если его текст разбирается в то же дерево,
    что и сломанное `apply` (позиции в дамп не входят). Не сошлось — одна
    попытка в скобках: операнд бывает ниже по приоритету, чем место вставки
    (`x and not (a or b)` → `x and (a or b)`). Частных правил под операторы нет.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return None, f"исходник не разбирается ({e.msg})"
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
        try:
            got = ast.dump(ast.parse(text))
        except SyntaxError:
            why = "текст мутанта не разбирается"
            continue
        if got == want:
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
    # файлы, где имя модуля просто упомянуто в строке или комментарии.
    imported = re.compile(rf"^\s*(?:import\s+{name}\b|from\s+{name}\s+import)",
                          re.M)
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
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        # Без `-x`: он останавливал прогон на первой ошибке, и упавший по
        # окружению тест выдавал бы «мутант убит» независимо от мутации.
        r = subprocess.run([sys.executable, "-B", "-m", "pytest", *targets, "-q",
                            "-p", "no:cacheprovider", "--timeout", str(timeout)],
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


def render_report(tested: int, survivors: list, skipped: list, planned: int, dropped: int,
                  aborted: str, totals: ScanTotals) -> str:
    """Отчёт о проверенном к этому моменту. Пишется после каждого мутанта: job,
    оборванный раннером на потолке, прежде уносил с собой и отчёт — тот писался
    один раз в конце (№395)."""
    untried = planned - len(skipped) - tested
    aborted = aborted or "сбой"
    # «K из M» — из всего плана до среза: строка сама называет, куда делась разница
    why = [f"срезано --max: {dropped}"] if dropped else []
    if untried:
        why.append(f"остановка: {aborted}")
    lines = [f"Проверено мутантов: {tested} из {planned + dropped}"
             + (f" ({'; '.join(why)})" if why else "") + f", выжило: {len(survivors)}"]
    if untried:
        lines.append(f"НЕ СУДИЛОСЬ: {untried} (прервано: {aborted}) — "
                     "это НЕ значит «там всё хорошо».")
    if skipped:
        lines.append(f"НЕ ПРИМЕНИЛОСЬ: {len(skipped)} — результат неполон.")
    if totals.files_unreadable:
        lines.append(f"НЕ ПРОЧИТАНО файлов: {totals.files_unreadable} из {totals.files_in} "
                     f"— их строки не судились.")
    if dropped:
        lines.append(f"Не проверено из-за потолка: {dropped}. "
                     f"Это НЕ значит «там всё хорошо».")
    for m, why_not in skipped:
        lines.append(f"  НЕ ПРИМЕНИЛОСЬ {m} — {why_not}")
    for s in survivors:
        lines.append(f"  ВЫЖИЛ {s}")
    if survivors:
        lines.append("")
        lines.append("Выживший мутант — это изменение поведения, которого не "
                     "заметил ни один тест. Либо тест на это место есть, но "
                     "он ничего не держит, либо места в тестах нет вовсе.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    # Бюджет считается от старта процесса: подготовка дерева и база входят в него
    started = clock()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", default="origin/main...HEAD",
                    help="диапазон git, чьи строки мутируем")
    ap.add_argument("--max", type=int, default=60,
                    help="потолок мутантов (срезанное объявляется вслух)")
    ap.add_argument("--timeout", type=int, default=120, help="секунд на прогон")
    ap.add_argument("--report", type=pathlib.Path, help="куда сложить отчёт")
    ap.add_argument("--force", action="store_true",
                    help="стартовать, даже если машина занята встречей, разбором "
                         "или ночным циклом (чужой лок мутатора не обходится)")
    ap.add_argument("--budget-s", type=float, default=None,
                    help="секунд на весь прогон от старта; не хватает на следующий "
                         "набор — остановка с отчётом о проверенном (--force не снимает)")
    args = ap.parse_args(argv[1:])

    root = pathlib.Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                       capture_output=True, text=True,
                                       check=True).stdout.strip())
    # Координация с живым контуром (ночь 23→24.08: мутатор делил qwen35b со
    # встречей и с ночным циклом — 35 ReadTimeout по 300 с у досье, прогон
    # оборван руками в 10:28). Правила: (1) на старте машина занята встречей
    # или разбором — честный отказ, не тихая толкотня; (2) на время прогона
    # лежит logs/mutation.lock — ночь (wait_for_idle) видит нас и ждёт;
    # (3) началась живая встреча — прерываемся между мутантами.
    # Свой `src/` уже первым в sys.path (вставка у импорта модуля): канон и
    # сигналы занятости берутся из дерева самого скрипта, а не из git-корня
    # текущего каталога. Вторая вставка собирала процесс из двух деревьев —
    # мутатор одного клона, запущенный из другого, падал на сверке корня
    # кода трассировкой (круг 1 по коду №331, Opus M2).
    import busy_signals  # noqa: E402
    import charoite_paths  # noqa: E402
    # Корень ДАННЫХ — как у ночи: env или сам репо (вложенные установки). Канон
    # целиком, а не его пересказ: прежняя копия брала strip и expanduser, но
    # теряла resolve, и относительное значение переменной уводило лок в каталог,
    # зависящий от cwd — гвард «машина занята живой встречей» смотрел не туда и
    # молча пропускал старт. Запасной корень канона (`parent.parent` от скрипта
    # в `scripts/`) совпадает с git-корнем клона, так что «канон сюда не
    # подставляется» было моей ошибкой, а не свойством кода (обе головы
    # выходного круга №321). «~/charoite» лечил ещё круг-1 (DS Minor).
    data_root = charoite_paths.resolve_root(__file__)
    if busy_guard(args):
        busy = busy_signals.machine_busy(data_root)
        if busy:
            print(f"машина занята ({', '.join(busy)}) — мутатор не стартует "
                  "(--force, чтобы настоять)")
            return 3
    plan, totals = plan_for(root, args.range)
    if not plan:
        code = verdict_code([], 0, 0, 0, 0, totals)
        if code == EXIT_NOTHING_TO_CHECK and not totals.lines_in:
            print(f"В {args.range} нет изменённых строк в {' '.join(MUTATION_AREAS)} — ломать нечего.")
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
                  f"{totals.files_unreadable} из {totals.files_in} "
                  f"(ревизия {head_of(args.range)}) — это НЕ «нечего мутировать».")
        return code

    dropped = 0
    if len(plan) > args.max:
        # По кругу между файлами: срез подряд забирал всех мутантов одного
        # файла, а остальные не проверялись вовсе (ревью 20.08, локальная).
        by_file: dict[pathlib.Path, list[Mutation]] = {}
        for m in plan:
            by_file.setdefault(m.path, []).append(m)
        picked: list[Mutation] = []
        while len(picked) < args.max and any(by_file.values()):
            for queue in by_file.values():
                if queue and len(picked) < args.max:
                    picked.append(queue.pop(0))
        dropped = len(plan) - len(picked)
        plan = picked

    print(f"Мутантов к проверке: {len(plan)}"
          + (f" (СРЕЗАНО {dropped} — потолок --max={args.max})" if dropped else ""))

    # Лок — ДО подготовки дерева: отказ не должен оставлять сиротой
    # зарегистрированный worktree (круг-2 по PR #399, DS Minor).
    lock = busy_signals.MutationLock(data_root)
    if not lock.acquire():
        print("другой мутатор уже держит лок — не стартую (--force не поможет: "
              "два прогона на одной модели бессмысленны)")
        return 3
    # Убитый на полпути прогон оставляет зарегистрированное дерево; без
    # уборки git будет считать его живым и мешать следующим запускам.
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutate-"))
    work = tmp / "tree"
    rev = head_of(args.range)
    subprocess.run(["git", "worktree", "add", "--detach", str(work), rev],
                   cwd=root, capture_output=True, check=True)
    survivors: list[Mutation] = []
    skipped: list[tuple[Mutation, str]] = []
    tested = 0
    aborted = ""
    durations: dict[tuple[str, ...], float] = {}

    def save(reason: str) -> None:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(render_report(tested, survivors, skipped, len(plan), dropped,
                                                 reason, totals) + "\n", encoding="utf-8")
    try:
        # СНАЧАЛА чистый прогон. В отдельном дереве нет файлов из .gitignore —
        # ни моделей, ни конфига, ни данных, — и тесты там могут быть красными
        # сами по себе. Тогда КАЖДЫЙ мутант считается убитым, отчёт говорит
        # «выжило 0», и гейт проходит, не проверив ничего: инструмент врёт в
        # самую опасную сторону (ревью 20.08, DeepSeek).
        # Проверяем КАЖДОЕ подмножество, на котором будет судиться мутант, а
        # не только их объединение: тест, зелёный в общей куче, в одиночку
        # может падать — и тогда мутанты его модуля «убиты» без участия
        # мутации (ревью 20.08, DeepSeek).
        subsets = {suite_key(work, m.path) for m in plan}
        # Свежий worktree байткода не содержит, но запрет записи не мешает
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
            save(BASE_RED)
            return 2
        for i, mut in enumerate([] if aborted else plan, 1):
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
                print(f"⏹ {aborted} — прерываюсь ({tested}/{len(plan)} "
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
                print(f"  [{i}/{len(plan)}] НЕ ПРИМЕНИЛОСЬ: {mut} — {why}")
                save(RUNNING)
                continue
            target.write_text(mutated, encoding="utf-8")
            try:
                alive, _ = timed_run(work, suite, args.timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            tested += 1
            mark = "ВЫЖИЛ" if alive else "убит"
            print(f"  [{i}/{len(plan)}] {mark}: {mut}")
            if alive:
                survivors.append(mut)
            # На диске всегда проверенное к этому моменту: оборвёт раннер —
            # отчёт скажет, сколько успели, а не пропадёт целиком
            save(RUNNING)
    finally:
        lock.release()
        subprocess.run(["git", "worktree", "remove", "--force", str(work)],
                       cwd=root, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    report = render_report(tested, survivors, skipped, len(plan), dropped, aborted, totals)
    print("\n" + report)
    save(aborted)
    return verdict_code(survivors, tested, len(plan), dropped, len(skipped), totals)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
