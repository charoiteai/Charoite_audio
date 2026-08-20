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
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

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
    def __init__(self, path: pathlib.Path, line: int, what: str, apply):
        self.path, self.line, self.what, self.apply = path, line, what, apply

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


def changed_lines(root: pathlib.Path, rng: str) -> dict[pathlib.Path, set[int]]:
    """Строки, добавленные в диапазоне, по файлам src/."""
    out = subprocess.run(["git", "diff", "--unified=0", rng, "--", "src/"],
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


# Пары «оператор + операнд», где подмена оператора не меняет смысла.
# Шире фильтровать нельзя: `x - 1` → `x + 1` — это ошибка на двойку, то есть
# ровно тот класс, ради которого арифметику и добавляли. Первая версия
# душила его вместе с настоящим шумом (ревью 20.08, круг 3, DeepSeek).
_NEUTRAL = {(ast.Mult, 1), (ast.Div, 1), (ast.FloorDiv, 1),
            (ast.Add, 0), (ast.Sub, 0)}


def _neutral(node: ast.BinOp) -> bool:
    """`x * 1`, `x + 0`: подмена оператора здесь ничего не меняет."""
    for side in (node.left, node.right):
        if (isinstance(side, ast.Constant)
                and isinstance(side.value, int)
                and not isinstance(side.value, bool)
                and (type(node.op), side.value) in _NEUTRAL):
            return True
    return False


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


def mutations_for(path: pathlib.Path, lines: set[int],
                  source: str | None = None) -> list[Mutation]:
    """Что можно сломать в этих строках."""
    try:
        tree = ast.parse(source if source is not None
                         else path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    lines = lines - _module_constants(tree)
    found: list[Mutation] = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or ln not in lines:
            continue
        if isinstance(node, ast.Compare) and node.ops:
            op = type(node.ops[0])
            if op in CMP_SWAP:
                found.append(Mutation(path, ln,
                                      f"{op.__name__} → {CMP_SWAP[op].__name__}",
                                      _swap_cmp(node)))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAP:
            found.append(Mutation(path, ln, f"{type(node.op).__name__} → "
                                            f"{BOOL_SWAP[type(node.op)].__name__}",
                                  _swap_bool(node)))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                found.append(Mutation(path, ln, f"{node.value} → {not node.value}",
                                      _swap_const(node, not node.value)))
            elif isinstance(node.value, (int, float)) and node.value not in (0,):
                found.append(Mutation(path, ln, f"{node.value} → 0",
                                      _swap_const(node, 0)))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP \
                and not _neutral(node):
            found.append(Mutation(path, ln, f"{type(node.op).__name__} → "
                                            f"{BIN_SWAP[type(node.op)].__name__}",
                                  _swap_bin(node)))
        elif isinstance(node, ast.Return) and node.value is not None:
            found.append(Mutation(path, ln, "return X → return None",
                                  _drop_return(node)))
    return found


def _swap_cmp(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare) and _same(n, target):
                n.ops = [CMP_SWAP[type(n.ops[0])]()] + list(n.ops[1:])
                return n
        return None
    return apply


def _swap_bin(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.BinOp) and _same(n, target):
                n.op = BIN_SWAP[type(n.op)]()
                return n
        return None
    return apply


def _swap_bool(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.BoolOp) and _same(n, target):
                n.op = BOOL_SWAP[type(n.op)]()
                return n
        return None
    return apply


def _swap_const(target, value):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and _same(n, target):
                n.value = value
                return n
        return None
    return apply


def _drop_return(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Return) and _same(n, target):
                n.value = None
                return n
        return None
    return apply


def patch_source(text: str, node: ast.AST) -> str | None:
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
        piece = ast.unparse(node)
    except Exception:                            # noqa: BLE001
        return None
    return head + piece + tail


def _same(a, b) -> bool:
    return (getattr(a, "lineno", -1) == getattr(b, "lineno", -2)
            and getattr(a, "col_offset", -1) == getattr(b, "col_offset", -2))


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
    hits = [str(p.relative_to(root))
            for p in sorted((root / "tests").rglob("test_*.py"))
            if imported.search(t := p.read_text(encoding="utf-8"))
            or spawned.search(t)]
    # Пусто — не значит «никто не проверяет»: модуль мог приехать через
    # чужой импорт. Берём весь набор: честно и медленно лучше, чем быстро
    # и мимо.
    return hits or ["tests"]


def run_tests(cwd: pathlib.Path, targets: list[str], timeout: int) -> bool:
    """True — прогон зелёный (мутант выжил, тесты его не заметили)."""
    try:
        # Без `-x`: он останавливал прогон на первой ошибке, и упавший по
        # окружению тест выдавал бы «мутант убит» независимо от мутации.
        r = subprocess.run([sys.executable, "-m", "pytest", *targets, "-q",
                            "-p", "no:cacheprovider", "--timeout", str(timeout)],
                           cwd=cwd, capture_output=True, text=True,
                           timeout=timeout * 4)
    except subprocess.TimeoutExpired:
        return False          # завис — считаем убитым: поведение изменилось
    return r.returncode == 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--range", default="origin/main...HEAD",
                    help="диапазон git, чьи строки мутируем")
    ap.add_argument("--max", type=int, default=60,
                    help="потолок мутантов (срезанное объявляется вслух)")
    ap.add_argument("--timeout", type=int, default=120, help="секунд на прогон")
    ap.add_argument("--report", type=pathlib.Path, help="куда сложить отчёт")
    args = ap.parse_args(argv[1:])

    root = pathlib.Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                       capture_output=True, text=True,
                                       check=True).stdout.strip())
    targets = changed_lines(root, args.range)
    if not targets:
        print(f"В {args.range} нет изменённых строк в src/ — ломать нечего.")
        return 0

    rev = head_of(args.range)
    plan: list[Mutation] = []
    for path, lines in sorted(targets.items()):
        rel = path.relative_to(root)
        # Разбираем ту версию файла, которую и будем ломать: рабочее дерево
        # может стоять на другой ветке, и номера строк не совпадут.
        blob = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=root,
                              capture_output=True, text=True)
        if blob.returncode:
            continue
        plan.extend(mutations_for(path, lines, source=blob.stdout))
    if not plan:
        print(f"Изменённые строки не содержат ничего мутируемого "
              f"(файлов: {len(targets)}).")
        return 0

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

    # Убитый на полпути прогон оставляет зарегистрированное дерево; без
    # уборки git будет считать его живым и мешать следующим запускам.
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutate-"))
    work = tmp / "tree"
    rev = head_of(args.range)
    subprocess.run(["git", "worktree", "add", "--detach", str(work), rev],
                   cwd=root, capture_output=True, check=True)
    survivors: list[Mutation] = []
    skipped: list[Mutation] = []
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
        subsets = {tuple(tests_for(work, m.path)) for m in plan}
        print(f"Базовый прогон (без мутаций), наборов: {len(subsets)}…")
        broken = [ts for ts in sorted(subsets)
                  if not run_tests(work, list(ts), args.timeout)]
        if broken:
            print("\nБАЗА КРАСНАЯ: без единой мутации падают наборы:")
            for ts in broken:
                print("  " + " ".join(ts))
            print("В отдельном дереве нет того, что лежит в .gitignore "
                  "(модели, конфиг, данные).\nМутанты этих модулей "
                  "засчитались бы убитыми — считать их бессмысленно.")
            return 2
        for i, mut in enumerate(plan, 1):
            rel = mut.path.relative_to(root)
            target = work / rel
            original = target.read_text(encoding="utf-8")
            tree = ast.parse(original)
            node = mut.apply(tree)
            mutated = patch_source(original, node) if node is not None else None
            if mutated is None or mutated == original:
                # Не применилась — узел не нашёлся или замена ничего не дала:
                # расхождение версий файла. Молчать нельзя: «0 выживших»
                # из-за того, что ничего не ломали, читается как «всё
                # проверено».
                skipped.append(mut)
                continue
            target.write_text(mutated, encoding="utf-8")
            try:
                alive = run_tests(work, tests_for(work, mut.path), args.timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            mark = "ВЫЖИЛ" if alive else "убит"
            print(f"  [{i}/{len(plan)}] {mark}: {mut}")
            if alive:
                survivors.append(mut)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(work)],
                       cwd=root, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)

    lines = [f"Проверено мутантов: {len(plan) - len(skipped)}, "
             f"выжило: {len(survivors)}"]
    if skipped:
        lines.append(f"НЕ ПРИМЕНИЛОСЬ: {len(skipped)} — версия файла разошлась "
                     f"с диапазоном, результат неполон.")
    if dropped:
        lines.append(f"Не проверено из-за потолка: {dropped}. "
                     f"Это НЕ значит «там всё хорошо».")
    for s in survivors:
        lines.append(f"  ВЫЖИЛ {s}")
    if survivors:
        lines.append("")
        lines.append("Выживший мутант — это изменение поведения, которого не "
                     "заметил ни один тест. Либо тест на это место есть, но "
                     "он ничего не держит, либо места в тестах нет вовсе.")
    report = "\n".join(lines)
    print("\n" + report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report + "\n", encoding="utf-8")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
