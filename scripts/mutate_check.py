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


def mutations_for(path: pathlib.Path, lines: set[int],
                  source: str | None = None) -> list[Mutation]:
    """Что можно сломать в этих строках."""
    try:
        tree = ast.parse(source if source is not None
                         else path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found: list[Mutation] = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or ln not in lines:
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in CMP_SWAP:
                found.append(Mutation(path, ln, f"{op.__name__} → {CMP_SWAP[op].__name__}",
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
        elif isinstance(node, ast.Return) and node.value is not None:
            found.append(Mutation(path, ln, "return X → return None",
                                  _drop_return(node)))
    return found


def _swap_cmp(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Compare) and _same(n, target):
                n.ops = [CMP_SWAP[type(n.ops[0])]()]
                return True
        return False
    return apply


def _swap_bool(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.BoolOp) and _same(n, target):
                n.op = BOOL_SWAP[type(n.op)]()
                return True
        return False
    return apply


def _swap_const(target, value):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and _same(n, target):
                n.value = value
                return True
        return False
    return apply


def _drop_return(target):
    def apply(tree):
        for n in ast.walk(tree):
            if isinstance(n, ast.Return) and _same(n, target):
                n.value = None
                return True
        return False
    return apply


def _same(a, b) -> bool:
    return (getattr(a, "lineno", -1) == getattr(b, "lineno", -2)
            and getattr(a, "col_offset", -1) == getattr(b, "col_offset", -2))


def tests_for(root: pathlib.Path, module: pathlib.Path) -> list[str]:
    """Тесты, которые вообще могут заметить поломку в этом модуле.

    Гоняем не весь набор: полный прогон на каждого мутанта — это часы.
    Ищем по имени модуля в тексте тестов; не нашли — берём весь набор,
    честно и медленно, потому что «не нашли» не значит «не проверяют».
    """
    name = module.stem
    hits = [str(p.relative_to(root)) for p in sorted((root / "tests").rglob("test_*.py"))
            if re.search(rf"\b{re.escape(name)}\b", p.read_text(encoding="utf-8"))]
    return hits or ["tests"]


def run_tests(cwd: pathlib.Path, targets: list[str], timeout: int) -> bool:
    """True — прогон зелёный (мутант выжил, тесты его не заметили)."""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", *targets, "-x", "-q",
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
        dropped = len(plan) - args.max
        plan = plan[:args.max]

    print(f"Мутантов к проверке: {len(plan)}"
          + (f" (СРЕЗАНО {dropped} — потолок --max={args.max})" if dropped else ""))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutate-"))
    work = tmp / "tree"
    rev = head_of(args.range)
    subprocess.run(["git", "worktree", "add", "--detach", str(work), rev],
                   cwd=root, capture_output=True, check=True)
    survivors: list[Mutation] = []
    try:
        for i, mut in enumerate(plan, 1):
            rel = mut.path.relative_to(root)
            target = work / rel
            original = target.read_text(encoding="utf-8")
            tree = ast.parse(original)
            if not mut.apply(tree):
                continue
            target.write_text(ast.unparse(ast.fix_missing_locations(tree)),
                              encoding="utf-8")
            try:
                alive = run_tests(work, tests_for(root, mut.path), args.timeout)
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

    lines = [f"Проверено мутантов: {len(plan)}, выжило: {len(survivors)}"]
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
        args.report.write_text(report + "\n", encoding="utf-8")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
