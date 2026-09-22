"""Точки входа принимаются ПРОГОНОМ, а не чтением.

Каждый исполняемый файл из реестра сторожа раскладки (`layout_map.executables`)
запускается процессом по объявленному контракту (`run_contracts` в
`docs/design/layout.json`): `help` — `--help` с изолированным корнем данных
выходит 0 (argparse разбирает аргументы раньше любой работы); `refuse` — запуск
без названного корня отказывает кодом `exit_codes.EXIT_ROOT_UNNAMED` и печатает
рецепт (№332); `none` — безопасного пробника нет, вход не запускается, причина
стоит в карте как долг вслух.

Повод — пять Critical подряд по №332 одного класса: «отказ уходит кодом 2»,
«событие видно снаружи» — утверждения о поведении процесса, не проверенные
запуском процесса. Входной круг №339 (22.09): обе головы независимо — реестр
«что исполняемо» уже есть у `layout_map`, значит и «чем это проверить» живёт
рядом, а сама проверка — pytest-тест, который гоняют и CI, и мутатор; первый
черновик держал её bash-шагом в preflight и утверждал код отказа, которого в
базе ветки не было.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402
import charoite_paths  # noqa: E402
import exit_codes  # noqa: E402

TIMEOUT = 30


def _run(cmd: list[str], cwd: pathlib.Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"не завершился за {timeout} с")


def _first_line(r: subprocess.CompletedProcess) -> str:
    lines = (r.stderr.strip() or r.stdout.strip()).splitlines()
    return lines[0][:160] if lines else "(вывода нет)"


def run_contract(repo: pathlib.Path, rel: str, mode: str, data_root: pathlib.Path, *,
                 python: str = sys.executable, timeout: int = TIMEOUT) -> list[str]:
    """Прогнать один вход по контракту; список расхождений (пусто — принят).

    `help` идёт с изолированным корнем данных, `refuse` — со снятой переменной:
    отказ обязан прийти кодом `EXIT_ROOT_UNNAMED` и с рецептом, что передать.
    Неизвестный режим — тоже расхождение, а не тихий пропуск."""
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    problems: list[str] = []
    for part in mode.split("+"):
        if part == "help":
            r = _run([python, str(repo / rel), "--help"], repo, {**env, "CHAROITE_ROOT": str(data_root)}, timeout)
            if r.returncode != 0:
                problems.append(f"{rel} --help: код {r.returncode}, ожидали 0 — {_first_line(r)}")
            elif "usage" not in (r.stdout + r.stderr).lower():
                # Ноль без справки — это «argv проигнорирован и работа сделана», а не
                # разбор аргументов: три входа прошли пробу, не зная про --help вовсе
                # (замер 22.09). argparse печатает usage всегда — его и требуем.
                problems.append(f"{rel} --help: вышел 0, но справки (usage) не напечатал — "
                                f"аргумент проигнорирован, работа выполнена: {_first_line(r)}")
        elif part == "refuse":
            r = _run([python, str(repo / rel)], repo, env, timeout)
            if r.returncode != exit_codes.EXIT_ROOT_UNNAMED:
                problems.append(f"{rel} без корня: код {r.returncode}, ожидали "
                                f"{exit_codes.EXIT_ROOT_UNNAMED} — {_first_line(r)}")
            elif "CHAROITE_ROOT" not in r.stderr + r.stdout:
                problems.append(f"{rel} без корня: отказал кодом, но рецепта (CHAROITE_ROOT=…) в выводе нет")
        elif part != "none":
            problems.append(f"{rel}: неизвестный режим {part!r}")
    return problems


def _plan() -> list[tuple[str, str]]:
    inv = lm.inventory()
    return [(rel, mode) for rel, mode, _why in lm.run_plan(lm.load_layout(), lm.executables(inv))
            if mode != "none"]


PLAN = _plan()


@pytest.mark.parametrize("rel,mode", PLAN, ids=[rel for rel, _ in PLAN])
def test_every_entry_point_keeps_its_run_contract(rel: str, mode: str, tmp_path: pathlib.Path) -> None:
    """Контракт из карты — исполнением: код возврата и первая строка вывода, не
    комментарий в исходнике."""
    root = tmp_path / "root"
    root.mkdir()
    problems = run_contract(ROOT, rel, mode, root)
    assert not problems, "\n".join(problems)


def test_the_probe_reports_an_entry_that_breaks_its_contract(tmp_path: pathlib.Path) -> None:
    """Сама проба проверена «дырявыми» входами: отказ кодом 0, отказ без рецепта,
    `--help` кодом 3, зависание, составной контракт, неизвестный режим — всё
    расхождения; честные входы — пусто. Без этого гейт был бы вторым набором
    утверждений, которые никто не исполняет (вопрос 6 входного круга №339)."""
    repo, root = tmp_path / "repo", tmp_path / "root"
    repo.mkdir()
    root.mkdir()
    code = exit_codes.EXIT_ROOT_UNNAMED
    (repo / "leaky.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (repo / "mute.py").write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
    (repo / "honest.py").write_text("import sys\nprint('передайте CHAROITE_ROOT=/данные', file=sys.stderr)\n"
                                    f"sys.exit({code})\n", encoding="utf-8")
    (repo / "helpless.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    (repo / "helpful.py").write_text("import argparse\nargparse.ArgumentParser().parse_args()\n", encoding="utf-8")
    (repo / "hang.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    assert "ожидали" in run_contract(repo, "leaky.py", "refuse", root)[0]
    assert "рецепта" in run_contract(repo, "mute.py", "refuse", root)[0]
    assert run_contract(repo, "honest.py", "refuse", root) == []
    assert "ожидали 0" in run_contract(repo, "helpless.py", "help", root)[0]
    assert run_contract(repo, "helpful.py", "help", root) == []
    assert run_contract(repo, "helpful.py", "help+refuse", root), "составной контракт держит обе половины"
    assert "не завершился" in run_contract(repo, "hang.py", "help", root, timeout=1)[0]
    assert "неизвестный режим" in run_contract(repo, "helpful.py", "smoke", root)[0]


def test_the_registry_names_the_canon_constructor() -> None:
    """Литерал имени конструктора в сторожe сверен с каноном: переименование в
    `charoite_paths` без правки реестра краснеет здесь, а не молчит (третья
    копия литерала — урок круга 5 по №332)."""
    assert set(lm.ROOT_CONSTRUCTORS) == {charoite_paths.require_data_root.__name__,
                                        charoite_paths.name_data_root_or_exit.__name__}


def test_the_default_contract_is_derived_from_the_code(tmp_path: pathlib.Path) -> None:
    """Умолчание по коду — только то, что проба докажет: вызов `parse_args` →
    help (импорта argparse мало), конструктор корня → refuse, оба → help+refuse;
    ничего — None: `none` пишет человек с карточкой, shell — тоже None."""
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    guard = "if __name__ == '__main__':\n"
    (tmp_path / "src" / "a.py").write_text("import argparse\n" + guard + "    argparse.ArgumentParser().parse_args()\n",
                                           encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("import charoite_paths\n" + guard + "    charoite_paths.require_data_root(__file__)\n",
                                           encoding="utf-8")
    (tmp_path / "src" / "ab.py").write_text("import argparse\nfrom charoite_paths import require_data_root\n" + guard
                                            + "    require_data_root(__file__)\n    argparse.ArgumentParser().parse_args()\n",
                                            encoding="utf-8")
    (tmp_path / "src" / "c.py").write_text(guard + "    print(1)\n", encoding="utf-8")
    (tmp_path / "src" / "imp.py").write_text("import argparse\n" + guard + "    print(argparse)\n", encoding="utf-8")
    (tmp_path / "scripts" / "d.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    got = {rel: lm.derive_run_contract(rel, inv.files[rel]) for rel in lm.executables(inv)}
    assert {rel: (c and c["mode"]) for rel, c in got.items()} == {
        "src/a.py": "help", "src/b.py": "refuse", "src/ab.py": "help+refuse", "src/c.py": None,
        "src/imp.py": None, "scripts/d.sh": None}
    assert all(c["why"].startswith("по коду: ") for c in got.values() if c), "умолчание машины подписано"
    # битый .py: дерева нет — умолчания нет, а не падение на обходе (мутант `or → and` пережил CI #605)
    (tmp_path / "src" / "broken.py").write_text("def (\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    assert lm.derive_run_contract("src/broken.py", inv.files["src/broken.py"]) is None


def test_the_acceptance_has_something_to_run() -> None:
    """Пустой план у parametrize — пропуск, не красное (DS I3 = GLM I1): реестр,
    который дал ноль проб, обязан краснеть здесь, а не исчезать из отчёта."""
    assert PLAN, "реестр контрактов не дал ни одного входа для прогона — приёмка пуста"


def test_the_plan_lists_only_executables_with_a_contract() -> None:
    """План — пересечение контрактов и реестра: контракт без файла в плане не
    появляется (мутант `in → not in` пережил CI по #605), порядок — по пути."""
    layout = {"run_contracts": {"src/b.py": {"mode": "help", "why": ""},
                                "src/a.py": {"mode": "refuse", "why": "по коду"},
                                "src/gone.py": {"mode": "help", "why": ""}}}
    execs = {"src/a.py": "python с гвардом __main__", "src/b.py": "python с гвардом __main__",
             "src/nocontract.py": "python с гвардом __main__"}
    assert lm.run_plan(layout, execs) == [("src/a.py", "refuse", "по коду"), ("src/b.py", "help", "")]

