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

Проба пакета графа (№365, №427) судится с ДВУХ сторон. Положительная проба
(`test_the_graph_package_runs_without_the_app`) идёт по НАСТОЯЩЕМУ артефакту:
сессионная фикстура собирает колесо `charoite-graph` офлайн во временной копии
раскладки, тест распаковывает его и сверяет имена `*.py` в архиве с планом
`layout_map.package_files` (`artifact_name` — путь репозитория ↔ имя в колесе).
Отрицательные пробы судят САМУ пробу, а не артефакт: им сборка не нужна, и они
работают на копии каталога `src/<пакет>/` целиком (`_copy_package`), которую
портят по месту.
"""
from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from typing import NamedTuple

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402
import lock_runtime_deps as lrd  # noqa: E402
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


#: Один инвентарь репозитория на модуль тестов: план контрактов и план копии
#: пакета читают его, а не обходят дерево заново. Обход — секунда, а этот файл
#: мутатор гоняет на каждого мутанта `scripts/layout_map.py`, и job мутации
#: упирался в свой потолок (PR №625).
INV = lm.inventory()


def _plan() -> list[tuple[str, str]]:
    return [(rel, mode) for rel, mode, _why in lm.run_plan(lm.load_layout(), lm.executables(INV))
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
    (tmp_path / "src" / "door.py").write_text("from charoite_paths import name_data_root_or_exit\n" + guard
                                              + "    name_data_root_or_exit(__file__)\n", encoding="utf-8")
    (tmp_path / "src" / "both.py").write_text("import charoite_paths\n" + guard + "    charoite_paths.require_data_root(__file__)\n"
                                              + "    charoite_paths.name_data_root_or_exit(__file__)\n", encoding="utf-8")
    (tmp_path / "src" / "c.py").write_text(guard + "    print(1)\n", encoding="utf-8")
    (tmp_path / "src" / "imp.py").write_text("import argparse\n" + guard + "    print(argparse)\n", encoding="utf-8")
    (tmp_path / "scripts" / "d.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    inv = lm.inventory(tmp_path)
    got = {rel: lm.derive_run_contract(rel, inv.files[rel]) for rel in lm.executables(inv)}
    assert {rel: (c and c["mode"]) for rel, c in got.items()} == {
        "src/a.py": "help", "src/b.py": "refuse", "src/ab.py": "help+refuse", "src/c.py": None,
        "src/imp.py": None, "scripts/d.sh": None, "src/door.py": "refuse", "src/both.py": "refuse"}
    # подпись называет то, чем вход называет корень, — дверь и конструктор различимы (№340, круг 2)
    assert got["src/door.py"]["why"] == "по коду: дверь корня"
    assert got["src/b.py"]["why"] == "по коду: конструктор корня"
    assert got["src/both.py"]["why"] == "по коду: конструктор корня и дверь корня"
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



# ---------------------------------------------------------------- проба пакета графа
#
# Пакет поиска по графу ставится без приложения (№365): его модули — замыкание
# объявленных входов (`package_entries` в артефакте), план даёт
# `layout_map.package_files`, а не список здесь. Статический гейт окружения по
# слою видит формы в тексте; проба — поведение: отдельный процесс, окружение
# приложения ведёт в ловушку. Положительная проба идёт по СОБРАННОМУ колесу
# (№427): сессионная фикстура `wheel_path` собирает его офлайн в копии раскладки,
# тест распаковывает архив и сверяет имена модулей с планом; отрицательные пробы
# судят саму пробу на копии каталога `src/<пакет>/` целиком — им сборка не нужна.

#: Тяжёлые зависимости приложения, которых пакету не нужно. Протечка меряется по
#: `sys.modules` после импорта и поиска, а не по ошибке импорта: здесь они
#: установлены, и ошибка отличила бы «нет пакета» от «лишний импорт» только случайно.
APP_ONLY_DEPS = ("sounddevice", "onnxruntime", "sherpa_onnx", "onnx_asr", "numpy", "requests",
                 "websockets", "mcp", "tokenizers", "soundfile", "rich")
#: Переменные окружения приложения, которые проба отравляет каталогом-ловушкой.
POISONED_ENV = ("HOME", "CHAROITE_ROOT", "SUFLER_GRAPH_DIR", "CHAROITE_GRAPH_DIR", "TMPDIR")
#: Изоляция — тем же механизмом, что проба готовности приложения
#: (`tests/test_setup_probe_isolated.py`): SAFEPATH и снятые пути импорта. Не `-I`:
#: проект его запретил — он тянет `-E` и глушит PYTHONPYCACHEPREFIX.
ISOLATION_ENV = {"PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
ISOLATION_DROP = ("PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX")
#: Сборка колеса пакета графа (№427) — одна команда, рядом с изоляцией выше:
#: тот же интерпретатор (`sys.executable`), офлайн и без изоляции сборки
#: (setuptools уже стоит в venv по пину из CI). `-w` берёт каталог вывода
#: последним аргументом — его подставляет фикстура.
WHEEL_BUILD = ("-m", "pip", "wheel", "--no-build-isolation", "--no-deps", "--no-index", "--no-cache-dir", "-w")
#: Что снимает окружение сборки — своя таблица, рядом с `ISOLATION_DROP`, у
#: записи — причина. `PIP_*` по префиксу, прокси — в обоих регистрах (сверка
#: регистронезависима): переменная pip задаёт индекс или уводит через прокси в
#: сеть, а сборка обязана быть офлайн и воспроизводимой.
WHEEL_ENV_DROP = (
    ("PIP_*", "переменные pip задают индекс, кэш и требования — сборка их не слушает"),
    ("HTTP_PROXY", "прокси уводит pip в сеть — сборка обязана быть офлайн"),
    ("HTTPS_PROXY", "прокси уводит pip в сеть — сборка обязана быть офлайн"),
    ("ALL_PROXY", "прокси уводит pip в сеть — сборка обязана быть офлайн"),
)
#: Потолок сборки колеса: pip готовит метаданные и пакует — дольше пробника
#: входа, но всё ещё секунды; общий TIMEOUT (30 с) на холодном кэше тесен.
#: Сборка и проба живут в бюджете ОДНОГО теста (`timeout` в pyproject):
#: pytest-timeout считает и сессионную фикстуру (выходной круг 1 по #657, DS M4).
#: Сумма с потолком пробы оставляет запас на распаковку и копии — отношение
#: держит тест, а не проза (круг 2, DS I2).
WHEEL_TIMEOUT = 70
#: Файл CI — тот же путь, откуда `scripts/preflight.sh` читает `RUFF_VERSION`:
#: пин setuptools для сборки берётся из его секции `env`.
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
#: Чего копия для пробы и сборки не берёт. Байткод — всегда: проба правит
#: копию по месту, а готовый `.pyc` с перенесённым mtime исполнился бы вместо
#: правки (выходной круг 1 по #657, DS M6). Следы ручной сборки — только в
#: каталоге дистрибутива (в пакете `build/` мог бы быть и модулем); те же имена
#: не видит git (`.gitignore`).
PACKAGE_IGNORE = ("__pycache__", "*.pyc")
BUILD_LEFTOVERS = ("build", "dist", "*.egg-info")
DIST_IGNORE = PACKAGE_IGNORE + BUILD_LEFTOVERS
#: Утечка каждой снимаемой переменной — СВОИМ признаком: значение у родителя и то,
#: чем проба обязана его показать, `(проблемы, признаки раннера, ловушка) → bool`.
#: «Проба красная» признаком не считается: любая чужая причина падения прятала бы,
#: что переменная не при чём (Minor DS круга 3 по PR №625). Признаки сняты опытом:
#: - PYTHONPATH — каталоги пути импорта интерпретатор читает до хука, поэтому ловит
#:   не ловушка, а модуль продукта, взятый мимо копии;
#: - PYTHONHOME — стандартная библиотека в ловушке, интерпретатор не доходит до
#:   раннера, и признаков изоляции нет вовсе;
#: - PYTHONPYCACHEPREFIX — раннер печатает префикс до хука, а хук видит чтение
#:   готового .pyc по нему как чтение ловушки.
LEAK_SIGNS = {
    "PYTHONPATH": (str(ROOT / "src"), lambda got, out, trap: got == [
        f"пакет загрузил {(ROOT / 'src' / 'task_line.py').resolve()} мимо своей копии"]),
    "PYTHONHOME": ("{trap}", lambda got, out, trap: bool(got) and out == {}),
    "PYTHONPYCACHEPREFIX": ("{trap}", lambda got, out, trap: out.get("pycache_prefix") == trap
                            and len(got) == 1 and "чтение ловушки" in got[0]),
}
#: Код выхода, которым аудит-хук валит пробу. Хук не бросает исключение, а
#: выходит сразу: `except Exception` в коде пакета проглотил бы исключение молча.
AUDIT_EXIT = 97
#: События аудита, которые меняют ФС мимо `open`, → номера аргументов, куда они
#: пишут. Источник копии, цель ссылки и длина — не запись: копия графа в кэш законна,
#: а проверка источника валила бы её (Minor головы круга 1 по PR №625). Своего
#: события у `os.replace` нет — CPython поднимает `os.rename`, и прежняя запись
#: `os.replace` не срабатывала никогда (найдено случаем самопроверки ниже).
MUTATIONS = {"os.mkdir": (0,), "os.remove": (0,), "os.rmdir": (0,), "os.rename": (0, 1),
             "os.truncate": (0,), "os.link": (1,), "os.symlink": (1,), "os.chmod": (0,), "os.utime": (0,),
             "shutil.rmtree": (0,), "shutil.copyfile": (1,)}
#: Флаги `open`, которые делают открытие записью. Именами, а не маской: у каждого
#: свой случай самопроверки (Critical головы круга 2 по PR №625 — любой флаг
#: убирался из маски без красного). Режим встроенного `open` не проверяется
#: отдельно: событие аудита несёт флаги и для него.
WRITE_FLAG_NAMES = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC")

#: Утверждённая копия таблиц пробы — по заданию №365, а не по самим таблицам:
#: случаи самопроверки строятся из таблиц, и сужение таблицы сужало случаи молча
#: (Critical головы круга 2 по PR №625). Тот же приём, что `APPROVED_KINDS` и
#: `APPROVED_ENV_READ_FORMS` в `tests/test_import_boundaries.py`.
APPROVED_PROBE = {
    "POISONED_ENV": ("HOME", "CHAROITE_ROOT", "SUFLER_GRAPH_DIR", "CHAROITE_GRAPH_DIR", "TMPDIR"),
    "ISOLATION_ENV": {"PYTHONSAFEPATH": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    "ISOLATION_DROP": ("PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX"),
    "WRITE_FLAG_NAMES": ("O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"),
}

#: Раннер пробы — отдельным процессом. Путь к копии пакета вставляет он сам, а не
#: окружение: с SAFEPATH каталог скрипта в sys.path не попадает.
#:
#: Векторизатор — детерминированная подделка, а не отказ: с отказом индекс векторов
#: не строился, кэш не писался, и правило «запись вне data_dir» на настоящем пакете
#: не срабатывало ни разу — манифест, уведённый в /var/tmp, проходил пробу зелёным
#: (Critical головы круга 1 по PR №625). Второй экземпляр читает кэш с диска: запись
#: и чтение проходят оба.
PROBE_RUNNER = r"""
import os, sys   # уже загружены интерпретатором; всё прочее — после хука, импорт идёт мимо ловушки

# признаки изоляции — первой строкой, ДО хука: процесс, который хук уронит, их
# всё равно отдаст. Префикс кэша байткода не пишет (DONTWRITEBYTECODE), но импорт
# читает по нему готовый .pyc — это и видит хук как чтение ловушки
print("isolation=" + repr({"pycache_prefix": sys.pycache_prefix}), flush=True)

PKG, DATA, GRAPH, TRAP, QUERY = sys.argv[1:6]
TRAP_REAL, DATA_REAL = os.path.realpath(TRAP), os.path.realpath(DATA)
WRITE_FLAGS = 0
for _name in """ + repr(WRITE_FLAG_NAMES) + r""":
    WRITE_FLAGS |= getattr(os, _name)
MUTATIONS = """ + repr(MUTATIONS) + r"""


def inside(path, root):
    try:
        real = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    return real == root or real.startswith(root + os.sep)


def fail(what):
    sys.stderr.write(f"АУДИТ: {what}\n")
    sys.stderr.flush()
    os._exit(""" + str(AUDIT_EXIT) + r""")


def hook(event, args):
    if event == "open":
        path, _, flags = (tuple(args) + (None, None))[:3]
        if path is None or isinstance(path, int):
            return
        if inside(path, TRAP_REAL):
            fail(f"чтение ловушки {path}")
        if (flags or 0) & WRITE_FLAGS and not inside(path, DATA_REAL):
            fail(f"запись вне data_dir: {path}")
    elif event in ("os.listdir", "os.scandir"):
        if args and args[0] is not None and inside(args[0], TRAP_REAL):
            fail(f"обход ловушки {args[0]}")
    elif event in MUTATIONS:
        for i in MUTATIONS[event]:
            path = args[i] if i < len(args) else None
            if isinstance(path, (str, bytes, os.PathLike)) and not inside(path, DATA_REAL):
                fail(f"{event} вне data_dir: {path}")


sys.addaudithook(hook)
import json, pathlib
sys.path.insert(0, PKG)
PATH_BEFORE = list(sys.path)
import codecs, importlib, re
# Что тянет КАЖДЫЙ __init__ копии — окном на свой импорт. Список — из самой копии,
# родитель раньше ребёнка: импорт подпакета исполняет и `__init__` родителя, и его
# ноша иначе легла бы на подпакет (круг 3 по #650, DS M2 и критика 2). Интерпретатор
# сам грузит ровно одно — кодек объявленной кодировки; он загружается ДО окна, и
# окно обязано быть пустым целиком, без фильтра по именам (DS I1).
PKG_ROOT = pathlib.Path(PKG)
INITS = sorted((".".join(p.relative_to(PKG_ROOT).parent.parts) for p in PKG_ROOT.rglob("__init__.py")),
               key=lambda n: (n.count("."), n))
PULLED_BY_INIT = {}
for name in INITS:
    with open(PKG_ROOT.joinpath(*name.split("."), "__init__.py"), "rb") as f:
        cookie = re.search(rb"coding[:=]\s*([-\w.]+)", f.readline() + f.readline())
    if cookie:
        try:
            codecs.lookup(cookie.group(1).decode("ascii"))
        except LookupError:
            pass    # неизвестную кодировку назовёт сам импорт
    before = set(sys.modules)
    importlib.import_module(name)
    PULLED_BY_INIT[name] = sorted(set(sys.modules) - before - {name})
# Каждый модуль копии — под ловушкой, не только то, что потянет вход: второй вход
# (дверь векторов, №323 PR 1) иначе не загружался бы вовсе. Обход — каталоги пакетов
# верхнего уровня копии (`INITS` без точки), не её корень: там лежат корневые модули
# отрицательной фикстуры (вход 2, I1); после цикла `INITS`, чтобы окно `__init__`
# не считало их ношей.
for top in [n for n in INITS if "." not in n]:
    for f in sorted(PKG_ROOT.joinpath(top).rglob("*.py")):
        if f.name != "__init__.py":
            importlib.import_module(".".join(f.relative_to(PKG_ROOT).with_suffix("").parts))
from charoite_graph import graph_search, model_seam


def door_post(url, payload, timeout):
    return 200, json.dumps({"embeddings": [[1.0, float(len(t))] for t in payload["input"]]})


# Дверь векторов — поведением, если её файл есть в копии (по файлу, а не по ImportError:
# сломанный импорт двери иначе молча выпал бы из проверки, вход 3 Q3)
DOOR = {}
if PKG_ROOT.joinpath("charoite_graph", "embed_door.py").is_file():
    from charoite_graph import embed_door
    DOOR = {"vectors": embed_door.embedder("http://127.0.0.1:9", "проба-дверь", post=door_post).run(["а", "бб"], 5)}


def vectors(texts, timeout):
    return [[1.0 + t.count(c) for c in "аеиоуртнс"] for t in texts]


def open_search():
    return graph_search.GraphSearch(pathlib.Path(GRAPH), data_dir=pathlib.Path(DATA),
                                    embedder=model_seam.Embedder(vectors, "проба-векторы"))


search = open_search()
search.refresh(force=True)
embedded = search.embed_pending()
again = open_search()
again.refresh(force=True)
loaded = again.load_vectors()
result = again.search(QUERY)
# Командная строка — последним шагом (её кэш — свой подкаталог DATA, манифест шагов
# выше она не трогает; входной круг 2 по №323 PR 2, I3). Точка входа — из
# `entry_points.txt` распакованного колеса, а не импорт по имени: гейт колеса судит
# артефакт (I5 входа r2 хвоста). У копий без метаданных — модуль по файлу.
CLI = {}
if PKG_ROOT.joinpath("charoite_graph", "cli.py").is_file():
    import contextlib, importlib.metadata, io, stat, urllib.request
    eps = [ep for dist in importlib.metadata.distributions(path=[PKG]) for ep in dist.entry_points
           if ep.group == "console_scripts"]
    if eps:
        cli_main, CLI["entry"] = eps[0].load(), [f"{ep.name}={ep.value}" for ep in eps]
    else:
        from charoite_graph.cli import main as cli_main
        CLI["entry"] = None
    CLI_DATA = pathlib.Path(DATA) / "cli"

    def cli_run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main(argv)
        return code, buf.getvalue()

    def tree():
        return sorted(str(p.relative_to(DATA)) for p in pathlib.Path(DATA).rglob("*"))

    class Answer:
        def __init__(self, body):
            self.status, self.body = 200, body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    def fake_urlopen(request, timeout=None):
        texts = json.loads(request.data)["input"]
        return Answer(json.dumps({"embeddings": [[1.0 + t.count(c) for c in "аеиоуртнс"] for t in texts]}).encode())

    # маска вызывающего — заведомо слабая: у разработчика с 077 CLI без своей маски
    # проходил бы проверку режимов молча (выходной круг 1 по №323 PR 2, M2)
    os.umask(0o022)
    before = tree()
    code, text = cli_run(["search", GRAPH, QUERY, "--json"])
    CLI.update(search_code=code, search=json.loads(text) if code == 0 else text,
               search_created=sorted(set(tree()) - set(before)))
    urllib.request.urlopen = fake_urlopen
    import importlib.util      # тот же вопрос «есть ли дверь в копии», что у DOOR выше (там — по файлу); по наличию файла, а не по ImportError: сломанная дверь упала бы, а не выпала из проверки
    if importlib.util.find_spec("charoite_graph.embed_door") is not None:
        import charoite_graph.embed_door as _door      # в копии пробы двери может не быть
        _door.open_url = fake_urlopen      # шов двери: адрес на этой машине идёт через net.open_url (№525)
    model = ["--model-url", "http://127.0.0.1:9", "--model", "проба-cli", "--data-dir", str(CLI_DATA)]
    CLI["index_code"], CLI["index_out"] = cli_run(["index", GRAPH, *model])
    code, text = cli_run(["search", GRAPH, QUERY, "--json", *model])
    CLI.update(model_code=code, model_search=json.loads(text) if code == 0 else text)
    CLI["modes"] = {str(p.relative_to(DATA)): stat.S_IMODE(p.stat().st_mode)
                    for p in [CLI_DATA, *CLI_DATA.rglob("*")] if p.exists()}

print(json.dumps({"ready": result.ready, "total": result.total, "text": result.text, "cli": CLI,
                  "embedded": embedded, "loaded": loaded, "path_before": PATH_BEFORE, "path_after": sys.path,
                  "pulled_by_init": PULLED_BY_INIT, "inits": INITS, "door": DOOR,
                  "cache": sorted(str(p.relative_to(DATA)) for p in pathlib.Path(DATA).rglob("*") if p.is_file()),
                  "modules": sorted(sys.modules),
                  "files": sorted(os.path.realpath(m.__file__) for m in list(sys.modules.values())
                                  if getattr(m, "__file__", None))}, ensure_ascii=False))
"""


def _isolated_env(parent: dict[str, str], drop: tuple[str, ...]) -> dict[str, str]:
    """Окружение подпроцесса пробы и примера README: родитель минус `drop`, плюс
    `ISOLATION_ENV`. Чистое применение таблиц — своей политики нет: что снимать,
    решает вызывающий (самопроверки утечек зовут пробу с `drop=()`), отравление
    добавляет только проба (№446)."""
    env = {k: v for k, v in parent.items() if k not in drop}
    env.update(ISOLATION_ENV)
    return env


#: Что дверь векторов пакета обязана отдать под пробой на поддельном транспорте раннера
#: (`door_post`: вектор [1, длина текста]) для текстов «а» и «бб».
DOOR_VECTORS = [[1.0, 1.0], [1.0, 2.0]]


def run_package_probe(pkg: pathlib.Path, graph: pathlib.Path, query: str, work: pathlib.Path, *,
                      app_deps: tuple[str, ...] = APP_ONLY_DEPS, outside: tuple[str, ...] = (),
                      outer: dict[str, str] | None = None, drop: tuple[str, ...] = ISOLATION_DROP,
                      timeout: int = TIMEOUT) -> tuple[list[str], dict]:
    """Прогнать пакет из каталога `pkg`: вход импортируется отдельным процессом с
    отравленным окружением, индекс строится по `graph`, кэш — только в `data_dir`.
    `app_deps` — зависимости приложения, `outside` — модули продукта вне замыкания
    входа: у двух протечек разный диагноз. `outer` — что стояло в окружении
    родителя до изоляции (значение `{trap}` — путь ловушки), `drop` — что изоляция
    снимает. Что тянет каждый `__init__` копии, раннер меряет сам — список берёт из
    копии. Расхождения строками (пусто — принят) и выдача раннера; признаки
    изоляции (`pycache_prefix`) — и когда процесс упал."""
    trap, data, cwd = work / "ловушка", work / "data", work / "cwd"
    for d in (trap, data, cwd):
        d.mkdir(parents=True)
    (trap / "config.yaml").write_text("ловушка: читать нельзя\n", encoding="utf-8")
    runner = work / "probe_runner.py"
    runner.write_text(PROBE_RUNNER, encoding="utf-8")
    parent = {**os.environ, **{k: v.replace("{trap}", str(trap)) for k, v in (outer or {}).items()}}
    env = _isolated_env(parent, drop)
    env.update({k: str(trap) for k in POISONED_ENV})
    r = _run([sys.executable, str(runner), str(pkg), str(data), str(graph), str(trap), query],
             cwd, env, timeout)
    lines = r.stdout.strip().splitlines()
    head = ast.literal_eval(lines[0].removeprefix("isolation=")) if lines[:1] and lines[0].startswith("isolation=") else {}
    if r.returncode != 0:
        return [f"проба пакета: код {r.returncode} — {_first_line(r)}"], head
    out = {**head, **json.loads(lines[-1])}
    loaded = set(out["modules"])
    problems = [f"пакет импортировал {m}: зависимость приложения протекла в пакет" for m in sorted(loaded & set(app_deps))]
    # сосед по продукту — другой диагноз: замыкание входа расширилось, и публичной
    # поверхностью молча стал лишний модуль (Minor DeepSeek по PR №625)
    problems += [f"модуль {m} вне замыкания входов пакета загрузился в пакет" for m in sorted(loaded & set(outside))]
    # код продукта из репозитория, а не всё под корнем: у сопровождающего `.venv/`
    # лежит в репозитории, и yaml оттуда — законная зависимость пакета
    product = [(ROOT / d).resolve() for d in (lm.FLAT_DIR, "scripts")]
    problems += [f"пакет загрузил {f} мимо своей копии" for f in out["files"]
                 if any(pathlib.Path(f).is_relative_to(d) for d in product)]
    # путь импорта — поведением, при любом написании: грамматика гейта видит
    # `sys.path` только по имени, `S = sys; S.path.append(…)` она пропускает
    # (Critical головы круга 2 по PR №625 — граница грамматики, а не новая эвристика)
    if out["path_after"] != out["path_before"]:
        problems.append(f"пакет правит sys.path: было {out['path_before']}, стало {out['path_after']}")
    # `import charoite_graph` — только сам пакет: его `__init__` исполняется при импорте
    # любого члена, и всё, что он тянет, едет в каждый импорт. Статическое правило
    # («__init__ пуст от импортов») стоит в гейте раскладки, здесь — поведение
    # (выходной круг 1 по #650, DS I2)
    for name, pulled in sorted((out.get("pulled_by_init") or {}).items()):
        if pulled:
            problems.append(f"import {name} тянет {', '.join(pulled)} — __init__ пакета обязан быть пустым")
    door = out.get("door") or {}
    if door and door.get("vectors") != DOOR_VECTORS:
        problems.append(f"дверь векторов пакета не дала векторов под пробой: {door.get('vectors')!r}")
    problems += _cli_problems(out.get("cli") or {})
    return problems, out


def _cli_problems(cli: dict) -> list[str]:
    """Что командная строка пакета обязана под пробой (№323 PR 2): лексический поиск без
    адреса отвечает прогретым индексом и ничего не создаёт; `index` собирает векторы;
    кэш — только владельцу (маска процесса на время работы входа). Пусто — CLI нет."""
    if not cli:
        return []
    out = []
    search = cli.get("search")
    if cli.get("search_code") != 0 or not isinstance(search, dict) or not search.get("ready"):
        out.append(f"командная строка: search без адреса модели — код {cli.get('search_code')}, ответ {search!r}")
    if cli.get("search_created"):
        out.append(f"командная строка: search без --data-dir создал {cli['search_created']}")
    if cli.get("index_code") != 0:
        out.append(f"командная строка: index — код {cli.get('index_code')}, вывод {cli.get('index_out')!r}")
    if not any(rel.endswith(".json") for rel in cli.get("modes") or {}):
        out.append(f"командная строка: index не оставил манифеста кэша: {sorted(cli.get('modes') or {})}")
    model = cli.get("model_search")
    if cli.get("model_code") != 0 or not isinstance(model, dict) or model.get("status") == "unverified":
        out.append(f"командная строка: search с моделью не проверен семантикой — код {cli.get('model_code')}, "
                   f"ответ {model!r}")
    loose = {rel: oct(mode) for rel, mode in (cli.get("modes") or {}).items() if mode & 0o077}
    if loose:
        out.append(f"командная строка: кэш доступен не только владельцу: {loose}")
    return out


def _ci_env(name: str, workflow: pathlib.Path = CI_WORKFLOW) -> str:
    """Значение `name` из секции `env` файла CI — тот же путь и тот же разбор,
    что у `scripts/preflight.sh` для `RUFF_VERSION`: пин setuptools живёт там, а
    не отдельной константой теста, которая переживёт смену пина молча.

    Только строка: без кавычек YAML читает `2.10` числом 2.1, и рецепт назвал бы
    версию, которой нет (выходной круг 1 по #657, DS M5)."""
    value = yaml.safe_load(workflow.read_text(encoding="utf-8"))["env"][name]
    if not isinstance(value, str):
        pytest.fail(f"{name} в env {workflow.name} — не строка ({value!r}): пин пишется в кавычках")
    return value


def _installed_setuptools() -> str | None:
    """Версия setuptools текущего интерпретатора; нет пакета — None."""
    try:
        import setuptools
    except ImportError:
        return None
    return setuptools.__version__


def _setuptools_recipe(pin: str) -> str:
    """Команда установки пина — одна на тест и человека, который читает отказ."""
    return f"{sys.executable} -m pip install setuptools=={pin}"


def _setuptools_problem(pin: str, have: str | None) -> str | None:
    """Расхождение версии setuptools с пином — строкой с рецептом, иначе None.
    Пропуска нет: без setuptools нужной версии колесо офлайн не собрать, и отказ
    обязан назвать точную команду, а не «поставьте setuptools» (№427)."""
    if have == pin:
        return None
    сейчас = "не установлен" if have is None else f"версия {have}"
    return (f"сборке колеса нужен setuptools {pin} (пин из env файла CI), а в {sys.executable} он {сейчас} — "
            f"поставьте: {_setuptools_recipe(pin)}")


def _wheel_env(parent: dict[str, str]) -> dict[str, str]:
    """Окружение сборки — копия родительского без `WHEEL_ENV_DROP`. Запись
    таблицы с `*` — префикс, без `*` — точное имя; сверка регистронезависима,
    поэтому прокси и `pip_*` ловятся в обоих регистрах, как и требует таблица."""
    def dropped(name: str) -> bool:
        upper = name.upper()
        return any((pattern.endswith("*") and upper.startswith(pattern[:-1].upper()))
                   or (not pattern.endswith("*") and upper == pattern.upper())
                   for pattern, _why in WHEEL_ENV_DROP)
    return {k: v for k, v in parent.items() if not dropped(k)}


def _sandbox_copy(src: pathlib.Path, dst: pathlib.Path, ignore: tuple[str, ...]) -> None:
    """Записываемая копия дерева — одна на сборку колеса и отрицательные пробы.

    `copyfile`, а не `copy2`: режим источника не переносится. Каталоги
    `copytree` всё равно получает с режимом источника (`copystat`), поэтому
    после копии им добавляется запись владельцу: из дерева без права записи
    копия выходила только на чтение, и сборка с пробами краснели как дефект
    пакета (выходной круг 1 по #657, DS C1)."""
    shutil.copytree(src, dst, copy_function=shutil.copyfile, ignore=shutil.ignore_patterns(*ignore))
    for path in (dst, *dst.rglob("*")):
        if path.is_dir():
            path.chmod(path.stat().st_mode | 0o700)


def _copy_layout(src: pathlib.Path, work: pathlib.Path, package: str) -> None:
    """Копия раскладки для сборки колеса: каталог дистрибутива и каталог пакета
    лежат в `work` так же, как в `src`, поэтому `package-dir` разрешается в саму
    копию. Следы ручной сборки по README (`BUILD_LEFTOVERS`) не копируются:
    сборка без изоляции подхватила бы старый `build/lib`, и сверка плана краснела
    бы лишним модулем не по делу (опыт 27.09 по #657)."""
    _sandbox_copy(src / "packages" / "charoite-graph", work / "packages" / "charoite-graph", DIST_IGNORE)
    _sandbox_copy(src / lm.FLAT_DIR / package, work / lm.FLAT_DIR / package, PACKAGE_IGNORE)


@pytest.fixture(scope="session")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Колесо `charoite-graph`, собранное офлайн в СВОЕЙ временной копии
    раскладки: `packages/charoite-graph/` (метаданные) и каталог
    `src/charoite_graph/` целиком лежат рядом так же, как в репозитории, поэтому
    `package-dir = "../../src/charoite_graph"` разрешается в саму копию. Мусор
    `build/` и `egg-info` остаётся там же и в дерево не попадает.

    Путь один на сессию и не меняется (общий артефакт только читают), а сама
    сборка идёт без изоляции сборки и офлайн, потому что setuptools стоит в venv
    по пину из `env` файла CI; версия не та — `pytest.fail` с рецептом, без
    `skip`: пропущенная сборка молча выключила бы и сверку плана."""
    pin = _ci_env("SETUPTOOLS_VERSION")
    if (problem := _setuptools_problem(pin, _installed_setuptools())):
        pytest.fail(problem)
    work = tmp_path_factory.mktemp("wheel")
    _copy_layout(ROOT, work, lm.load_layout()["package"])
    out = work / "dist"
    out.mkdir()
    start = time.time()
    r = _run([sys.executable, *WHEEL_BUILD, str(out), "packages/charoite-graph"],
             work, _wheel_env(os.environ), WHEEL_TIMEOUT)
    if r.returncode != 0:
        pytest.fail(f"сборка колеса не прошла: код {r.returncode} — {_first_line(r)}")
    wheels = list(out.glob("*.whl"))
    if len(wheels) != 1:
        pytest.fail(f"в {out} ожидали одно колесо, нашли {len(wheels)}: {[w.name for w in wheels]}")
    built = wheels[0]
    if built.stat().st_mtime < start:
        pytest.fail(f"{built.name} старше начала сборки — колесо не собрано этим прогоном")
    # общий артефакт сессии неизменяем: порчу проверяют на копии архива, а не тут
    os.chmod(built, 0o444)
    return built


def _wheel_plan_problems(built: pathlib.Path, declared: Declared | None = None) -> list[str]:
    """Сверка плана пробы с НАСТОЯЩИМ артефактом: имена `*.py` из колеса против
    `artifact_name` каждого файла `package_files`, в обе стороны. Списком строк —
    чтобы и честная сборка, и порченая копия судились одним кодом, а расхождение
    называло лишние и недостающие модули, а не только факт неравенства (№427).

    Всё, что не модуль, судится тоже: кроме модулей плана в колесе может лежать
    только каталог метаданных СВОЕГО дистрибутива `<имя>-<версия>.dist-info`
    (имя и версия — из того же `pyproject.toml`, по которому колесо собрано;
    круг 2 по #657, DS I1) и стандартный `*.data/`. Сверка по одним `*.py` была
    слепа к данным пакета и к любому лишнему файлу (круг 1, DS I2); что объявлять
    данными — №446."""
    want = {lm.artifact_name(rel) for rel in lm.package_files(INV, lm.load_layout())}
    with zipfile.ZipFile(built) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
    got = {name for name in names if name.endswith(".py")}
    tops = {name.split("/", 1)[0] for name in names}
    meta = {top for top in tops if top.endswith(".dist-info")}
    чужое = sorted(name for name in names if not name.endswith(".py")
                   and not name.split("/", 1)[0].endswith((".dist-info", ".data")))
    избыток = sorted(got - want)
    недостача = sorted(want - got)
    out = []
    if избыток:
        out.append("в артефакте лишние модули: " + ", ".join(избыток))
    if недостача:
        out.append("в артефакте нет модулей плана: " + ", ".join(недостача))
    if чужое:
        out.append("в артефакте лишние файлы (объявить данными или убрать): " + ", ".join(чужое))
    own = _dist_info_dir(declared)
    if meta != {own}:
        out.append(f"в артефакте метаданные не своего дистрибутива: ждали {own}, нашли {sorted(meta)}")
    return out


#: Объявление дистрибутива пакета графа. Его пути (`readme`, `license-files`) по
#: PEP 621/639 разрешаются от каталога объявления.
PACKAGE_PYPROJECT = ROOT / "packages" / "charoite-graph" / "pyproject.toml"


class Declared(NamedTuple):
    """Объявление дистрибутива значением: таблица `[project]` и каталог, от которого
    разрешаются её пути. Для гейта колеса объявление читает только `_declared` (№446)."""
    project: dict
    base: pathlib.Path


def _declared(pyproject: pathlib.Path = PACKAGE_PYPROJECT) -> Declared:
    return Declared(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"], pyproject.parent)


def _dist_info_dir(declared: Declared | None = None) -> str:
    """Имя каталога метаданных колеса по объявлению дистрибутива: `-` в имени —
    `_`, как пишет его инструмент сборки (PEP 427). Без аргумента — объявление
    репозитория (прямые вызовы теста порчи плана); гейт передаёт своё явно."""
    project = (declared or _declared()).project
    return f"{project['name'].replace('-', '_')}-{project['version']}.dist-info"


class Line(NamedTuple):
    """Вид строки гейта колеса: шаблон; читает ли вид METADATA или архив своего
    `dist-info` (каталога нет — вид не вычисляется: о нём уже сказала сверка плана);
    виды, от которых он зависит (любой дал строку — этот не вычисляется)."""
    template: str
    needs_dist_info: bool
    depends_on: tuple[str, ...] = ()


#: Строки гейта «артефакт против объявления» (№446), по виду на проверку. Порядок —
#: топологический относительно `depends_on`: гейт идёт по словарю, и вид считается
#: после тех, от кого зависит. Поля METADATA сравниваются строкой как есть:
#: каноническую форму пишет инструмент сборки, объявление обязано быть в ней —
#: расхождение формы гейт называет, а не прощает. Строка начинается с того, что править.
WHEEL_LINES: dict[str, Line] = {
    "wheel_name": Line("сборка: имя файла колеса {got} не начинается с {want}", False),
    "name": Line("объявление или сборка: Name в METADATA {got!r}, в объявлении {want!r}", True),
    "version": Line("объявление или сборка: Version в METADATA {got!r}, в объявлении {want!r}", True),
    "requires_python": Line("объявление или сборка: Requires-Python в METADATA {got!r}, в объявлении {want!r}", True),
    "license_expression": Line("объявление или сборка: License-Expression в METADATA {got!r}, в объявлении {want!r}",
                               True),
    "requires_dist": Line("объявление: Requires-Dist в METADATA {got}, в dependencies {want} — строки сравниваются как есть",
                          True),
    "description": Line("README пакета: тело METADATA не равно файлу {path}", True),
    "example": Line("README пакета: {why}", True),
    "license_pattern": Line("объявление: license-files {got!r} — шаблон, а гейт судит литералы", False),
    "license_source": Line("каталог дистрибутива: объявленного файла лицензии {path} нет", False, ("license_pattern",)),
    "license_files": Line("сборка: License-File в METADATA {got}, в license-files {want}", True, ("license_pattern",)),
    "license_archive": Line("сборка: в колесе нет {path}", True, ("license_pattern",)),
    "license_bytes": Line("сборка: {path} в колесе не равен файлу {source}", True,
                          ("license_pattern", "license_source", "license_archive")),
    "duplicate_dependency": Line("объявление: {deps} — одно имя дистрибутива записано дважды", False),
    "missing_module": Line("окружение: у стороннего модуля {module} ({files}) нет дистрибутива", False),
    "undeclared_import": Line("объявление: импорт {module} ({files}) ведёт к {dists}, в dependencies их нет", False,
                              ("duplicate_dependency", "missing_module")),
    # Судится зависимость, дистрибутив которой стоит в окружении: тогда известны его
    # модули, и «к нему не ведёт ни один импорт» — факт. Не стоит — судить нечем, и
    # зависимость молчит сама по себе, а не вся проверка из-за чужого модуля
    # (выходной круг 1 по №446).
    "unused_dependency": Line("объявление: зависимость {dep} — к ней не ведёт ни один импорт пакета", False,
                              ("duplicate_dependency",)),
    "root_missing": Line("корневой манифест: зависимости пакета {dep} в нём нет", False, ("duplicate_dependency",)),
    "root_differs": Line("корневой манифест: {dep} в пакете, {root} в корне — строки обязаны совпадать", False,
                         ("duplicate_dependency", "root_missing")),
}


def _package_imports() -> dict[str, set[str]]:
    """Сторонние модули пакета → файлы, где они импортированы: первый сегмент имени
    вне стандартной библиотеки и вне самого пакета. Грамматика импорта и деревья —
    у инвентаря раскладки (`lm.imports_of`, `INV.files[rel].tree`)."""
    layout = lm.load_layout()
    out: dict[str, set[str]] = {}
    for rel in lm.package_files(INV, layout):
        for name in lm.imports_of(rel, INV.files[rel].tree):
            top = name.split(".")[0]
            if top not in sys.stdlib_module_names and top != layout["package"]:
                out.setdefault(top, set()).add(rel)
    return out


def _readme_example(body: str) -> str:
    """Код примера из описания артефакта: раздел от строки `## Пример` до следующей
    строки `## ` или до конца текста, в нём ровно один блок от строки ```python до
    строки ```. Иначе — `ValueError` с причиной словами."""
    lines = body.splitlines()
    if "## Пример" not in lines:
        raise ValueError("в описании нет раздела «## Пример»")
    section = lines[lines.index("## Пример") + 1:]
    section = section[:next((i for i, line in enumerate(section) if line.startswith("## ")), len(section))]
    blocks: list[str] = []
    current: list[str] | None = None
    for line in section:
        if current is None and line.strip() == "```python":
            current = []
        elif current is not None and line.strip() == "```":
            blocks.append("\n".join(current) + "\n")
            current = None
        elif current is not None:
            current.append(line)
    if current is not None:
        raise ValueError("в разделе «Пример» блок ```python не закрыт")
    if len(blocks) != 1:
        raise ValueError(f"в разделе «Пример» блоков ```python {len(blocks)}, ждали один")
    return blocks[0]


def _artifact_metadata(wheel: pathlib.Path, name: str):
    """METADATA колеса стандартным читателем: колесо — zip на пути поиска
    `importlib.metadata`, дистрибутив находится по имени каталога `dist-info`, текст —
    UTF-8. Байты через `email.parser` дали бы суррогаты вместо русского тела (опыт №446)."""
    found = list(importlib.metadata.distributions(name=name, path=[str(wheel)]))
    return found[0].metadata if len(found) == 1 else None


class _GateInput(NamedTuple):
    wheel: pathlib.Path
    own: str
    members: frozenset[str]
    licenses: dict[str, bytes]
    md: object
    declared: Declared
    imports: dict[str, set[str]]
    dists: dict[str, list[str]]
    root: list[str]


def _field(meta_key: str, project_key: str):
    def check(g: _GateInput) -> list[dict]:
        got, want = g.md.get(meta_key), g.declared.project.get(project_key)
        return [] if got == want else [{"got": got, "want": want}]
    return check


def _license_paths(g: _GateInput) -> list[str]:
    return list(g.declared.project.get("license-files", []))


def _readme_path(g: _GateInput) -> pathlib.Path:
    readme = g.declared.project.get("readme", "")
    return g.declared.base / (readme["file"] if isinstance(readme, dict) else readme)


def _declared_names(g: _GateInput) -> dict[str, str]:
    return {lrd.dist_name(d): d for d in g.declared.project.get("dependencies", [])}


def _check_example(g: _GateInput) -> list[dict]:
    try:
        _readme_example(g.md.get_payload())
    except ValueError as e:
        return [{"why": str(e)}]
    return []


def _check_description(g: _GateInput) -> list[dict]:
    path = _readme_path(g)
    text = path.read_text(encoding="utf-8") if path.is_file() else None
    return [] if g.md.get_payload() == text else [{"path": path.name}]


def _check_used(g: _GateInput) -> list[dict]:
    installed = {lrd.dist_name(d) for dists in g.dists.values() for d in dists}
    used = {lrd.dist_name(d) for dists in (g.dists.get(m, []) for m in g.imports) for d in dists}
    return [{"dep": dep} for name, dep in sorted(_declared_names(g).items()) if name in installed and name not in used]


def _check_duplicates(g: _GateInput) -> list[dict]:
    """Две строки `dependencies` с одним нормализованным именем: словарь имён молча
    оставил бы одну, и остальные виды судили бы не всё объявление."""
    by_name: dict[str, list[str]] = {}
    for dep in g.declared.project.get("dependencies", []):
        by_name.setdefault(lrd.dist_name(dep), []).append(dep)
    return [{"deps": ", ".join(deps)} for _, deps in sorted(by_name.items()) if len(deps) > 1]


def _check_undeclared(g: _GateInput) -> list[dict]:
    declared = set(_declared_names(g))
    return [{"module": m, "files": ", ".join(sorted(files)), "dists": ", ".join(sorted(g.dists[m]))}
            for m, files in sorted(g.imports.items())
            if g.dists.get(m) and not {lrd.dist_name(d) for d in g.dists[m]} & declared]


def _root_by_name(g: _GateInput) -> dict[str, str]:
    return {lrd.dist_name(r): r for r in g.root}


#: Проверка на каждый вид таблицы: вход гейта → подстановки строк (пусто — чисто).
_CHECKS = {
    "wheel_name": lambda g: [] if g.wheel.name.startswith(g.own.removesuffix(".dist-info") + "-")
    else [{"got": g.wheel.name, "want": g.own.removesuffix(".dist-info") + "-"}],
    "name": _field("Name", "name"),
    "version": _field("Version", "version"),
    "requires_python": _field("Requires-Python", "requires-python"),
    "license_expression": _field("License-Expression", "license"),
    "requires_dist": lambda g: [] if sorted(g.md.get_all("Requires-Dist") or []) == sorted(
        g.declared.project.get("dependencies", [])) else [{"got": sorted(g.md.get_all("Requires-Dist") or []),
                                                            "want": sorted(g.declared.project.get("dependencies", []))}],
    "description": _check_description,
    "example": _check_example,
    "license_pattern": lambda g: [{"got": f} for f in _license_paths(g) if any(c in f for c in "*?[")],
    "license_source": lambda g: [{"path": f} for f in _license_paths(g) if not (g.declared.base / f).is_file()],
    "license_files": lambda g: [] if sorted(g.md.get_all("License-File") or []) == sorted(_license_paths(g))
    else [{"got": sorted(g.md.get_all("License-File") or []), "want": sorted(_license_paths(g))}],
    "license_archive": lambda g: [{"path": f"{g.own}/licenses/{f}"} for f in _license_paths(g)
                                  if f"{g.own}/licenses/{f}" not in g.members],
    "license_bytes": lambda g: [{"path": f"{g.own}/licenses/{f}", "source": f} for f in _license_paths(g)
                                if g.licenses.get(f"{g.own}/licenses/{f}") != (g.declared.base / f).read_bytes()],
    "duplicate_dependency": _check_duplicates,
    "missing_module": lambda g: [{"module": m, "files": ", ".join(sorted(files))}
                                 for m, files in sorted(g.imports.items()) if not g.dists.get(m)],
    "undeclared_import": _check_undeclared,
    "unused_dependency": _check_used,
    "root_missing": lambda g: [{"dep": dep} for name, dep in sorted(_declared_names(g).items())
                               if name not in _root_by_name(g)],
    "root_differs": lambda g: [{"dep": dep, "root": _root_by_name(g)[name]}
                               for name, dep in sorted(_declared_names(g).items())
                               if name in _root_by_name(g) and _root_by_name(g)[name] != dep],
}


def wheel_problems(wheel: pathlib.Path, declared: Declared | None = None, **inputs) -> list[str]:
    """Гейт «артефакт против объявления» (№446): строками, пусто — принят."""
    return [line for _, line in _wheel_findings(wheel, declared, **inputs)]


def _wheel_findings(wheel: pathlib.Path, declared: Declared | None = None, *,
                    imports: dict[str, set[str]] | None = None, dists: dict[str, list[str]] | None = None,
                    root: list[str] | None = None) -> list[tuple[str, str]]:
    """Находки гейта парами (вид, строка); вид сверки плана — `plan`. Сначала
    сверка плана `_wheel_plan_problems`, потом виды таблицы `WHEEL_LINES` по порядку.
    Объявление резолвится здесь один раз и идёт во все вызовы явно; входы — значения
    (по умолчанию — настоящие источники: импорты пакета по инвентарю, дистрибутивы
    окружения тестов, объявление корневого манифеста), чтобы отрицательные опыты
    подменяли ровно одно."""
    declared = declared or _declared()
    own = _dist_info_dir(declared)
    with zipfile.ZipFile(wheel) as archive:
        members = frozenset(archive.namelist())
        licenses = {m: archive.read(m) for m in members if m.startswith(own + "/licenses/")}
    has_own = any(m.startswith(own + "/") for m in members)
    g = _GateInput(wheel, own, members, licenses,
                   _artifact_metadata(wheel, declared.project["name"]) if has_own else None, declared,
                   _package_imports() if imports is None else imports,
                   importlib.metadata.packages_distributions() if dists is None else dists,
                   lrd.declared_deps() if root is None else root)
    out = [("plan", line) for line in _wheel_plan_problems(wheel, declared)]
    fired: set[str] = set()
    for kind, line in WHEEL_LINES.items():
        if (line.needs_dist_info and g.md is None) or fired & set(line.depends_on):
            continue
        for fields in _CHECKS[kind](g):
            out.append((kind, line.template.format(**fields)))
            fired.add(kind)
    return out


def _copy_package(dest: pathlib.Path) -> None:
    """Копия КАТАЛОГА пакета целиком (`src/<пакет>/`) в `dest/<пакет>/` — не
    файлы плана `package_files`: отрицательные пробы судят ПРОБУ и портят копию
    по месту, а план и его сверка с колесом тут ни при чём. Форма та же, что у
    репозитория, поэтому раннер зовёт `from charoite_graph import …` без правок."""
    package = lm.load_layout()["package"]
    dest.mkdir()
    _sandbox_copy(ROOT / lm.FLAT_DIR / package, dest / package, PACKAGE_IGNORE)


@pytest.mark.xdist_group("wheel")
def test_the_graph_package_runs_without_the_app(tmp_path: pathlib.Path, wheel_path: pathlib.Path) -> None:
    """Пакет поиска — это замыкание его входов (`package_entries`) и ничего больше, и едет он в
    колесе ровно этими модулями: распакованный артефакт отдельно от репозитория
    строит индекс по демо-графу, пишет кэш векторов и читает его обратно, находит
    узел — не прочитав ни одной переменной приложения (HOME, корень, каталог
    графа, TMPDIR — ловушка) и не написав ничего вне своего `data_dir`. План пробы
    (`package_files`) сверяется с именами `*.py` в колесе в обе стороны, а не
    доверяется каталогу репозитория."""
    layout = lm.load_layout()
    pkg = tmp_path / "pkg"
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(pkg)
    plan = lm.package_files(INV, layout)
    расхождения = _wheel_plan_problems(wheel_path)
    assert not расхождения, "\n".join(расхождения)
    graph = tmp_path / "work" / "Демо"
    shutil.copytree(ROOT / "demo" / "graph", graph)
    modules = {lm.module_of(rel) for rel in plan}
    # импортировано обязано быть замыкание входа; план шире на `__init__` подпакетов,
    # которых вход может не звать (выходной круг 2 по #650, DS M6)
    entries = lm.package_entries(layout)
    closure = lm.package_union(lm.import_graph(INV), entries)
    assert set(entries) <= closure and closure <= modules
    others = tuple(sorted(lm.modules(INV) - modules))
    problems, out = run_package_probe(pkg, graph, "платёжный шлюз", tmp_path / "work", outside=others)
    # раннер мерил ровно `__init__` плана — список из распакованного артефакта сходится
    assert set(out["inits"]) == lm.package_inits(INV, layout), out["inits"]
    assert not problems, "\n".join(problems)
    assert out["ready"] and out["total"], f"индекс по демо-графу пуст: {out}"
    assert "Платёжный шлюз" in out["text"], f"поиск не нашёл узел демо-графа: {out['text'][:300]}"
    assert modules <= set(out["modules"]), "проба импортирует не все модули плана"
    assert out["door"] == {"vectors": DOOR_VECTORS}, f"дверь векторов не проверена поведением: {out['door']}"
    # командная строка — точкой входа из метаданных колеса, а не импортом по имени (№323 PR 2)
    cli = out["cli"]
    assert cli["entry"] == ["charoite-graph=charoite_graph.cli:main"], cli.get("entry")
    assert cli["search"]["status"] == "unverified" and cli["search"]["sources"], cli["search"]
    assert cli["model_code"] == 0 and cli["model_search"]["status"] != "unverified", cli["model_search"]
    assert any(rel.endswith(".json") for rel in cli["modes"]), cli["modes"]
    # путь записи пройден: векторы собраны, кэш лёг в data_dir и прочитан вторым экземпляром
    assert out["embedded"] > 0 and out["loaded"] == out["embedded"], out
    assert any(c.startswith("graph_search/") and c.endswith(".json") for c in out["cache"]), out["cache"]


@pytest.mark.xdist_group("wheel")
def test_the_wheel_plan_check_reds_on_a_corrupt_artifact(tmp_path: pathlib.Path,
                                                         wheel_path: pathlib.Path) -> None:
    """Сверка плана проверена порчей: колесо без модуля — недостача, колесо с
    лишним `.py` — избыток, каждое строкой с именем. Портим копию архива в своём
    `tmp_path`; сессионное колесо только читается, поэтому порча его не трогает."""
    assert _wheel_plan_problems(wheel_path) == [], "предпосылка: честное колесо сходится с планом"
    plan = lm.package_files(INV, lm.load_layout())
    victim = lm.artifact_name(next(rel for rel in plan if not rel.endswith("__init__.py")))

    def rebuild(target: pathlib.Path, *, drop: str | None = None, extra: str | None = None) -> pathlib.Path:
        with zipfile.ZipFile(wheel_path) as src, zipfile.ZipFile(target, "w") as dst:
            for item in src.infolist():
                if item.filename != drop:
                    dst.writestr(item, src.read(item.filename))
            if extra is not None:
                dst.writestr(extra, "")
        return target

    got = _wheel_plan_problems(rebuild(tmp_path / "short.whl", drop=victim))
    assert got and "нет модулей плана" in got[0] and victim in got[0], got
    got = _wheel_plan_problems(rebuild(tmp_path / "extra.whl", extra="charoite_graph/лишний.py"))
    assert got and "лишние модули" in got[0] and "charoite_graph/лишний.py" in got[0], got
    got = _wheel_plan_problems(rebuild(tmp_path / "data.whl", extra="charoite_graph/стоп-слова.txt"))
    assert got == ["в артефакте лишние файлы (объявить данными или убрать): charoite_graph/стоп-слова.txt"], got
    got = _wheel_plan_problems(rebuild(tmp_path / "meta.whl", extra="чужой-0.1.dist-info/METADATA"))
    assert len(got) == 1 and "не своего дистрибутива" in got[0] and "чужой-0.1.dist-info" in got[0], got
    own = _dist_info_dir()
    with zipfile.ZipFile(wheel_path) as src, zipfile.ZipFile(tmp_path / "swap.whl", "w") as dst:
        for item in src.infolist():
            dst.writestr(item.filename.replace(own, "другой-0.1.dist-info", 1), src.read(item.filename))
    got = _wheel_plan_problems(tmp_path / "swap.whl")
    assert len(got) == 1 and "ждали " + own in got[0], got
    assert _wheel_plan_problems(rebuild(tmp_path / "std.whl", extra="charoite_graph-0.1.0.data/scripts/x")) == []


@pytest.mark.xdist_group("wheel")
def test_the_wheel_matches_its_declaration(wheel_path: pathlib.Path) -> None:
    """Честное колесо проходит гейт «артефакт против объявления» целиком (№446)."""
    problems = wheel_problems(wheel_path)
    assert not problems, "\n".join(problems)


def test_the_package_license_is_the_root_license() -> None:
    """Копия лицензии в каталоге дистрибутива — тот же текст, что в корне: `..` в
    `license-files` PEP 639 запрещает, а без файла setuptools собирает колесо молча и
    без лицензии (опыт №446)."""
    assert (PACKAGE_PYPROJECT.parent / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def _corrupt_copy(wheel: pathlib.Path, where: pathlib.Path, *, name: str | None = None, edit=None,
                  drop: tuple[str, ...] = (), add: dict[str, bytes] | None = None) -> pathlib.Path:
    """Копия архива в свой каталог `where`: имя — `name` или имя самого артефакта;
    `edit(член, байты) -> байты` правит члены, `drop` снимает, `add` добавляет."""
    where.mkdir(parents=True)
    target = where / (name or wheel.name)
    with zipfile.ZipFile(wheel) as src, zipfile.ZipFile(target, "w") as dst:
        for item in src.infolist():
            if item.filename in drop:
                continue
            data = src.read(item.filename)
            dst.writestr(item, edit(item.filename, data) if edit else data)
        for member, data in (add or {}).items():
            dst.writestr(member, data)
    return target


def _metadata(transform):
    """Правка METADATA своего `dist-info` текстом UTF-8."""
    def edit(member: str, data: bytes) -> bytes:
        return transform(data.decode("utf-8")).encode("utf-8") if member.endswith(".dist-info/METADATA") else data
    return edit


def _declared_copy(where: pathlib.Path, transform=None, *, without_license: bool = False) -> Declared:
    """Каталог дистрибутива целиком (рядом README и LICENSE) и объявление из копии."""
    dst = where / "packages" / "charoite-graph"
    _sandbox_copy(PACKAGE_PYPROJECT.parent, dst, DIST_IGNORE)
    pyproject = dst / "pyproject.toml"
    if transform:
        pyproject.write_text(transform(pyproject.read_text(encoding="utf-8")), encoding="utf-8")
    if without_license:
        (dst / "LICENSE").unlink()
    return _declared(pyproject)


def _own_license(wheel: pathlib.Path) -> str:
    return f"{_dist_info_dir()}/licenses/LICENSE"


#: Случаи порчи: что испорчено → входы гейта → ожидаемое множество видов. Каждый вид
#: таблицы `WHEEL_LINES` получает свой случай (самопроверка ниже).
CORRUPT_CASES = {
    "чужое имя файла": (lambda w, d: {"wheel": _corrupt_copy(w, d, name="charoite_graph-9.9.9-py3-none-any.whl")},
                        {"wheel_name"}),
    "Name": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("Name: charoite-graph\n", "Name: чужой\n", 1)))}, {"name"}),
    "Version": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("Version: 0.1.0\n", "Version: 9.9.9\n", 1)))}, {"version"}),
    "License-Expression": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("License-Expression: Apache-2.0\n", "License-Expression: MIT\n", 1)))},
                           {"license_expression"}),
    "без Requires-Python": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("Requires-Python: >=3.11\n", "", 1)))}, {"requires_python"}),
    "лишний Requires-Dist": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("Requires-Dist: pyyaml>=6.0\n", "Requires-Dist: pyyaml>=6.0\nRequires-Dist: rich>=13.0\n",
                            1)))}, {"requires_dist"}),
    "без License-File": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("License-File: LICENSE\n", "", 1)))}, {"license_files"}),
    "тело без примера": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("\n## Пример\n", "\n## Образец\n", 1)))}, {"example", "description"}),
    "два блока примера": (lambda w, d: {"wheel": _corrupt_copy(w, d, edit=_metadata(
        lambda s: s.replace("    print(result.text)\n```\n", "    print(result.text)\n```\n\n```python\npass\n```\n",
                            1)))}, {"example", "description"}),
    "архив без лицензии": (lambda w, d: {"wheel": _corrupt_copy(w, d, drop=(_own_license(w),))}, {"license_archive"}),
    "чужие байты лицензии": (lambda w, d: {"wheel": _corrupt_copy(
        w, d, edit=lambda m, data: b"x\n" if m == _own_license(w) else data)}, {"license_bytes"}),
    "объявление без pyyaml": (lambda w, d: {"wheel": w, "declared": _declared_copy(
        d, lambda s: s.replace('dependencies = ["pyyaml>=6.0"]', "dependencies = []", 1))},
                              {"undeclared_import", "requires_dist"}),
    "лишняя зависимость": (lambda w, d: {"wheel": w, "declared": _declared_copy(
        d, lambda s: s.replace('dependencies = ["pyyaml>=6.0"]', 'dependencies = ["pyyaml>=6.0", "rich>=13.0"]', 1))},
                           {"unused_dependency", "requires_dist"}),
    "шаблон лицензии": (lambda w, d: {"wheel": w, "declared": _declared_copy(
        d, lambda s: s.replace('license-files = ["LICENSE"]', 'license-files = ["LICEN[CS]E"]', 1))},
                        {"license_pattern"}),
    "нет файла лицензии": (lambda w, d: {"wheel": w, "declared": _declared_copy(d, without_license=True)},
                           {"license_source"}),
    "модуль вне окружения": (lambda w, d: {"wheel": w, "dists": {
        k: v for k, v in importlib.metadata.packages_distributions().items() if "PyYAML" not in v}}, {"missing_module"}),
    "дубль зависимости": (lambda w, d: {"wheel": w, "declared": _declared_copy(
        d, lambda s: s.replace('dependencies = ["pyyaml>=6.0"]', 'dependencies = ["pyyaml>=6.0", "PyYAML>=6"]', 1))},
                          {"duplicate_dependency", "requires_dist"}),
    "лишняя зависимость вне окружения": (lambda w, d: {"wheel": w, "declared": _declared_copy(
        d, lambda s: s.replace('dependencies = ["pyyaml>=6.0"]', 'dependencies = ["pyyaml>=6.0", "not-installed-dist>=1"]', 1)),
        "root": lrd.declared_deps() + ["not-installed-dist>=1"]}, {"requires_dist"}),
    "корень без pyyaml": (lambda w, d: {"wheel": w, "root": [
        r for r in lrd.declared_deps() if lrd.dist_name(r) != "pyyaml"]}, {"root_missing"}),
    "корень с маркером": (lambda w, d: {"wheel": w, "root": [
        "pyyaml>=6.0 ; python_version < '3.13'" if lrd.dist_name(r) == "pyyaml" else r for r in lrd.declared_deps()]},
                          {"root_differs"}),
}


def test_the_wheel_gate_tables_agree() -> None:
    """Самопроверка таблиц: у каждого вида — проверка и случай порчи; порядок видов
    топологический (вид после всех, от кого зависит); ни один случай не ждёт вид
    вместе с видом, от которого тот зависит."""
    assert set(_CHECKS) == set(WHEEL_LINES)
    assert set().union(*(kinds for _, kinds in CORRUPT_CASES.values())) == set(WHEEL_LINES)
    order = list(WHEEL_LINES)
    for kind, line in WHEEL_LINES.items():
        assert all(order.index(dep) < order.index(kind) for dep in line.depends_on), kind
    for case, (_, kinds) in CORRUPT_CASES.items():
        assert not any(set(WHEEL_LINES[k].depends_on) & kinds for k in kinds), case


@pytest.mark.xdist_group("wheel")
@pytest.mark.parametrize("case", list(CORRUPT_CASES))
def test_the_wheel_gate_reds_on_each_corruption(case: str, tmp_path: pathlib.Path, wheel_path: pathlib.Path) -> None:
    build, kinds = CORRUPT_CASES[case]
    kwargs = build(wheel_path, tmp_path / "случай")
    got = _wheel_findings(kwargs.pop("wheel"), **kwargs)
    assert {kind for kind, _ in got} == kinds, got


@pytest.mark.xdist_group("wheel")
def test_the_wheel_gate_is_silent_about_metadata_without_its_dist_info(tmp_path: pathlib.Path,
                                                                      wheel_path: pathlib.Path) -> None:
    """Свой `dist-info` переименован: о нём говорит сверка плана — ровно одна строка,
    виды METADATA и архива не вычисляются."""
    own = _dist_info_dir()
    with zipfile.ZipFile(wheel_path) as src:
        moved = {m.replace(own, "другой-0.1.dist-info", 1): src.read(m) for m in src.namelist() if m.startswith(own)}
    swap = _corrupt_copy(wheel_path, tmp_path / "swap", drop=tuple(m.replace("другой-0.1.dist-info", own, 1)
                                                                   for m in moved), add=moved)
    got = wheel_problems(swap)
    assert got == _wheel_plan_problems(swap) and len(got) == 1, got


@pytest.mark.parametrize("body, why", [
    ("# t\n\n## Пример\n\n```python\nx = 1\n```\n", None),
    ("# t\n\n## Пример\n\n```python\nx = 1\n```\n\n## Дальше\n\n```python\ny = 2\n```\n", None),
    ("# t\n\n## Образец\n", "нет раздела"),
    ("# t\n\n## Пример\n\n```python\nx = 1\n```\n```python\ny = 2\n```\n", "блоков ```python 2"),
    ("# t\n\n## Пример\n\nтекст\n", "блоков ```python 0"),
    ("# t\n\n## Пример\n\n```python\nx = 1\n", "не закрыт"),
])
def test_the_readme_example_is_one_block_in_its_section(body: str, why: str | None) -> None:
    """Раздел — до следующей `## ` или до конца текста; блок в нём ровно один."""
    if why is None:
        assert _readme_example(body) == "x = 1\n"
    else:
        with pytest.raises(ValueError, match=why):
            _readme_example(body)


#: Раннер примера README — отдельным процессом: путь к распакованному колесу он
#: вставляет сам (с `PYTHONSAFEPATH` каталог скрипта в `sys.path` не попадает),
#: пример исполняется как главный модуль.
EXAMPLE_RUNNER = "import runpy, sys\nsys.path.insert(0, sys.argv[1])\nrunpy.run_path(sys.argv[2], run_name='__main__')\n"


def run_readme_example(pkg: pathlib.Path, code: str, work: pathlib.Path) -> subprocess.CompletedProcess:
    """Пример — в окружении изоляции без отравления: `TMPDIR` наследуется, пример
    пишет только во временный каталог, который сам и создаёт."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "example.py").write_text(code, encoding="utf-8")
    (work / "runner.py").write_text(EXAMPLE_RUNNER, encoding="utf-8")
    return _run([sys.executable, str(work / "runner.py"), str(pkg), str(work / "example.py")], work,
                _isolated_env(dict(os.environ), ISOLATION_DROP), TIMEOUT)


@pytest.mark.xdist_group("wheel")
def test_the_readme_example_runs_from_the_wheel(tmp_path: pathlib.Path, wheel_path: pathlib.Path) -> None:
    """Пример из описания АРТЕФАКТА (не из файла репозитория) исполняется на
    распакованном колесе: статус `confident` — семантика сработала (без векторов
    поиск не бывает уверенным), и узел найден. Судим по статусу, а не по маркеру
    «⚠» в тексте: маркеры в тексте никто не разбирает."""
    md = _artifact_metadata(wheel_path, _declared().project["name"])
    code = _readme_example(md.get_payload())
    pkg = tmp_path / "pkg"
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(pkg)
    r = run_readme_example(pkg, code, tmp_path / "work")
    assert r.returncode == 0, f"пример README упал: {r.stderr[-2000:]}"
    lines = r.stdout.splitlines()
    assert lines and lines[0] == "confident", r.stdout
    assert "Платёжный шлюз" in r.stdout, r.stdout


def test_the_example_runner_reports_a_broken_example(tmp_path: pathlib.Path) -> None:
    r = run_readme_example(tmp_path / "pkg", "raise RuntimeError('сломано')\n", tmp_path / "work")
    assert r.returncode != 0 and "RuntimeError: сломано" in r.stderr, (r.returncode, r.stderr[-500:])


def _wheel_group_problems(sources: dict[str, str]) -> list[str]:
    """Под `--dist loadgroup` тесты колеса идут в одном воркере, и колесо
    собирается один раз (№453). Параметр `wheel_path` бывает только у функций
    `test_*` — промежуточная фикстура увела бы своих потребителей из-под
    сторожа, — и у каждой такой функции метка `xdist_group("wheel")`.
    `usefixtures`/`getfixturevalue` с `wheel_path` запрещены: их параметром не
    увидеть. Строкой на нарушение: путь, функция, что не так."""
    out = []
    for rel, text in sorted(sources.items()):
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                if name in ("usefixtures", "getfixturevalue") and any(
                        isinstance(a, ast.Constant) and a.value == "wheel_path" for a in node.args):
                    out.append(f"{rel}:{node.lineno}: {name}(\"wheel_path\") — только параметром функции test_*")
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name == "wheel_path":
                continue
            if "wheel_path" not in {a.arg for a in node.args.args + node.args.kwonlyargs}:
                continue
            if not node.name.startswith("test"):
                out.append(f"{rel}:{node.lineno}: {node.name} берёт wheel_path, но не тест — фикстура-посредник")
                continue
            # имя группы — позиционно или `name=`: pytest принимает обе формы
            marked = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "xdist_group"
                         and [getattr(a, "value", None) for a in d.args]
                         + [getattr(k.value, "value", None) for k in d.keywords if k.arg == "name"] == ["wheel"]
                         for d in node.decorator_list)
            if not marked:
                out.append(f"{rel}:{node.lineno}: {node.name} без метки xdist_group(\"wheel\")")
    return out


def test_every_wheel_test_runs_in_the_wheel_group() -> None:
    tests = ROOT / "tests"
    sources = {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8") for p in sorted(tests.rglob("*.py"))}
    problems = _wheel_group_problems(sources)
    assert not problems, "\n".join(problems)


def test_the_wheel_group_guard_reds_on_each_form() -> None:
    """Сторож группы — на синтетике: без метки, с чужой группой, фикстура-посредник,
    `usefixtures` и `getfixturevalue`; с меткой — чисто."""
    good = '@pytest.mark.xdist_group("wheel")\ndef test_a(wheel_path):\n    pass\n'
    assert _wheel_group_problems({"t.py": good}) == []
    named = '@pytest.mark.xdist_group(name="wheel")\ndef test_a(wheel_path):\n    pass\n'
    assert _wheel_group_problems({"t.py": named}) == []
    cases = {
        "def test_a(wheel_path):\n    pass\n": "без метки",
        '@pytest.mark.xdist_group("other")\ndef test_a(wheel_path):\n    pass\n': "без метки",
        "def built(wheel_path):\n    return wheel_path\n": "фикстура-посредник",
        '@pytest.mark.usefixtures("wheel_path")\ndef test_a():\n    pass\n': "usefixtures",
        'def test_a(request):\n    request.getfixturevalue("wheel_path")\n': "getfixturevalue",
    }
    for source, word in cases.items():
        got = _wheel_group_problems({"t.py": source})
        assert len(got) == 1 and word in got[0], (source, got)


def test_the_sandbox_copy_is_writable_from_a_read_only_tree(tmp_path: pathlib.Path) -> None:
    """Копия для сборки и проб записываема, даже когда источник только на чтение
    (клон круга, смонтированный каталог): в каждый каталог копии можно писать,
    скопированный файл — перезаписать. Байткод не копируется."""
    src = tmp_path / "src"
    (src / "sub" / "__pycache__").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "sub" / "m.py").write_text("x = 1\n", encoding="utf-8")
    (src / "sub" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\0")
    (src / "stale.pyc").write_bytes(b"\0")
    dirs = [src, src / "sub", src / "sub" / "__pycache__"]
    for path in (*src.rglob("*"), src):
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        dst = tmp_path / "dst"
        _sandbox_copy(src, dst, PACKAGE_IGNORE)
    finally:
        for path in dirs:
            path.chmod(0o755)
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*")) == ["__init__.py", "sub", "sub/m.py"]
    for directory in (dst, dst / "sub"):
        (directory / "новый.py").write_text("", encoding="utf-8")
    (dst / "sub" / "m.py").write_text("x = 2\n", encoding="utf-8")


def test_the_wheel_build_and_the_probe_fit_one_test_budget() -> None:
    """Сборка (в фикстуре) и проба идут в бюджете одного теста: их сумма строго
    меньше потолка теста из pyproject — иначе наружу выходит «потолок 120 с», а
    не имя виновника (круг 2 по #657, DS I2)."""
    ceiling = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]["timeout"]
    assert WHEEL_TIMEOUT + TIMEOUT < ceiling, (WHEEL_TIMEOUT, TIMEOUT, ceiling)


def test_the_ci_pin_must_be_a_quoted_string(tmp_path: pathlib.Path) -> None:
    """Пин без кавычек YAML читает числом (`2.10` → 2.1) — отказ, а не рецепт
    на версию, которой нет."""
    workflow = tmp_path / "ci.yml"
    workflow.write_text('env:\n  GOOD: "2.10"\n  BAD: 2.10\n', encoding="utf-8")
    assert _ci_env("GOOD", workflow) == "2.10"
    with pytest.raises(pytest.fail.Exception, match="не строка"):
        _ci_env("BAD", workflow)


def test_the_layout_copy_leaves_manual_build_leftovers_behind(tmp_path: pathlib.Path) -> None:
    """Старые `build/`, `dist/` и `*.egg-info` ручной сборки в каталоге
    дистрибутива в копию для колеса не едут, метаданные и пакет — едут."""
    src = tmp_path / "src_root"
    dist = src / "packages" / "charoite-graph"
    (dist / "build" / "lib" / "charoite_graph").mkdir(parents=True)
    (dist / "build" / "lib" / "charoite_graph" / "старый.py").write_text("x = 1\n", encoding="utf-8")
    (dist / "dist").mkdir()
    (dist / "charoite_graph.egg-info").mkdir()
    (dist / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (src / lm.FLAT_DIR / "pkg").mkdir(parents=True)
    (src / lm.FLAT_DIR / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    _copy_layout(src, work, "pkg")
    copied = sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))
    assert copied == ["packages", "packages/charoite-graph", "packages/charoite-graph/pyproject.toml",
                      lm.FLAT_DIR, f"{lm.FLAT_DIR}/pkg", f"{lm.FLAT_DIR}/pkg/__init__.py"], copied


def test_the_wheel_env_drops_pip_and_proxies_in_both_registers() -> None:
    """Каждая запись `WHEEL_ENV_DROP` снимает свою переменную, а незнакомая —
    остаётся: `PIP_*` по префиксу, прокси обоих регистров. Самопроверка — новая
    запись без своей переменной краснеет здесь, а не тихо не снимается."""
    parent = {"PATH": "/usr/bin", "HOME": "/h",
              "PIP_INDEX_URL": "http://127.0.0.1:1/", "PIP_CACHE_DIR": "/c", "pip_index_url": "http://127.0.0.1:1/",
              "HTTP_PROXY": "http://127.0.0.1:1/", "http_proxy": "http://127.0.0.1:1/",
              "HTTPS_PROXY": "http://127.0.0.1:1/", "https_proxy": "http://127.0.0.1:1/",
              "ALL_PROXY": "http://127.0.0.1:1/", "all_proxy": "http://127.0.0.1:1/"}
    assert _wheel_env(parent) == {"PATH": "/usr/bin", "HOME": "/h"}, _wheel_env(parent)
    for pattern, why in WHEEL_ENV_DROP:
        name = "PIP_INDEX_URL" if pattern.endswith("*") else pattern
        assert why.strip(), f"{pattern}: запись без причины"
        for sample in (name, name.lower()):
            assert sample not in _wheel_env({**parent, sample: "x"}), f"{pattern}: не снимает {sample}"
    assert set(_wheel_env(parent)) == {"PATH", "HOME"}, "снято лишнее — таблица шире, чем заявлено"


def test_the_setuptools_pin_is_read_from_the_ci_env_and_names_the_recipe() -> None:
    """Пин читается из `env` файла CI (как `RUFF_VERSION` у preflight), а отказ
    называет точную команду: и когда setuptools нет, и когда версия другая.
    Пропуска нет — иначе выключенной оказалась бы и сборка, и сверка плана."""
    pin = _ci_env("SETUPTOOLS_VERSION")
    assert pin and pin[0].isdigit(), pin
    assert _setuptools_problem(pin, pin) is None, "совпавшая версия — не проблема"
    нет = _setuptools_problem(pin, None)
    другая = _setuptools_problem(pin, "0.0.0")
    for problem in (нет, другая):
        assert problem and pin in problem and sys.executable in problem, problem
    assert "не установлен" in нет and "версия 0.0.0" in другая, (нет, другая)
    assert _setuptools_recipe(pin) == f"{sys.executable} -m pip install setuptools=={pin}"


def test_the_probe_sees_what_the_package_init_pulls(tmp_path: pathlib.Path) -> None:
    """Отрицательный опыт признака `pulled_by_init`: копия пакета с импортом члена
    в `__init__` — строка пробы. Без признака «`import charoite_graph` не тянет ни
    yaml, ни членов» держалось бы одним статическим правилом гейта раскладки
    (выходной круг 1 по #650, DS I2)."""
    _copy_package(tmp_path / "pkg")
    init = tmp_path / "pkg" / "charoite_graph" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + "from charoite_graph import frontmatter\n",
                    encoding="utf-8")
    graph = tmp_path / "work" / "Демо"
    shutil.copytree(ROOT / "demo" / "graph", graph)
    problems, _ = run_package_probe(tmp_path / "pkg", graph, "платёжный шлюз", tmp_path / "work")
    assert any(p.startswith("import charoite_graph тянет") and "charoite_graph.frontmatter" in p
               for p in problems), problems


def test_the_probe_measures_every_init_and_blames_only_the_package(tmp_path: pathlib.Path) -> None:
    """Признак пробы меряет каждый `__init__` копии, а не только корень: подпакет,
    тянущий `yaml` динамически (`importlib`), и подпакет с `__import__("csv")` —
    строки; AST-правило гейта такие импорты не видит (выходной круг 2 по #650,
    DS I3; круг 3, DS I1 — стандартная библиотека тоже вина пакета). Кодек
    объявленной кодировки грузит интерпретатор, а не пакет: он загружается до окна
    и строкой не становится (DS M5)."""
    _copy_package(tmp_path / "pkg")
    root_init = tmp_path / "pkg" / "charoite_graph" / "__init__.py"
    root_init.write_bytes(b"# -*- coding: cp1251 -*-\n" + root_init.read_bytes())
    for name, body in (("sub", 'import importlib\nimportlib.import_module("yaml")\n'),
                       ("sub2", '__import__("csv")\n')):
        (tmp_path / "pkg" / "charoite_graph" / name).mkdir()
        (tmp_path / "pkg" / "charoite_graph" / name / "__init__.py").write_text(body, encoding="utf-8")
    graph = tmp_path / "work" / "Демо"
    shutil.copytree(ROOT / "demo" / "graph", graph)
    problems, out = run_package_probe(tmp_path / "pkg", graph, "платёжный шлюз", tmp_path / "work")
    assert out["inits"] == ["charoite_graph", "charoite_graph.sub", "charoite_graph.sub2"], out["inits"]
    assert any(p.startswith("import charoite_graph.sub тянет") and "yaml" in p for p in problems), problems
    assert any(p.startswith("import charoite_graph.sub2 тянет") and " csv " in p for p in problems), problems
    assert not any(p.startswith("import charoite_graph тянет") for p in problems), problems


#: Как каждое событие из `MUTATIONS` выглядит в коде пакета: `V` — каталог-жертва
#: вне `data_dir` (в нём файл `f` и каталог `d`), `self.data` — сам `data_dir`.
MUTATION_CASES = {
    "os.mkdir": "os.mkdir(V / 'новый')",
    "os.remove": "os.remove(V / 'f')",
    "os.rmdir": "os.rmdir(V / 'd')",
    "os.rename": "os.rename(V / 'f', V / 'g')",
    "os.truncate": "os.truncate(V / 'f', 0)",
    "os.link": "os.link(self.data / 'кэш', V / 'l')",
    "os.symlink": "os.symlink(self.data / 'кэш', V / 's')",
    "os.chmod": "os.chmod(V / 'f', 0o600)",
    "os.utime": "os.utime(V / 'f')",
    "shutil.rmtree": "shutil.rmtree(V / 'd')",
    "shutil.copyfile": "shutil.copyfile(self.data / 'кэш', V / 'c')",
}


#: События с двумя путями → код, где вне `data_dir` ТОЛЬКО аргумент с этим номером.
MUTATION_SIDES = {
    "os.rename": ("os.rename(V / 'f', self.data / 'g')", "os.rename(self.data / 'кэш', V / 'g')"),
    "os.link": ("os.link(V / 'f', self.data / 'l')", "os.link(self.data / 'кэш', V / 'l')"),
    "os.symlink": ("os.symlink(V / 'f', self.data / 's')", "os.symlink(self.data / 'кэш', V / 's')"),
    "shutil.copyfile": ("shutil.copyfile(V / 'f', self.data / 'c')", "shutil.copyfile(self.data / 'кэш', V / 'c')"),
}
#: Утверждённые номера записываемых аргументов у этих событий: переименование
#: меняет оба места, ссылка и копия пишут только цель.
APPROVED_MUTATION_SIDES = {"os.rename": (0, 1), "os.link": (1,), "os.symlink": (1,), "shutil.copyfile": (1,)}


def test_the_package_probe_catches_what_it_guards(tmp_path: pathlib.Path) -> None:
    """Проба проверена «дырявыми» пакетами — и случаи строятся из её же таблиц:
    каждая отравленная переменная, каждое событие мутации ФС, каждая снятая
    переменная изоляции. Новый элемент таблицы без своего случая красит тест
    (Critical головы круга 1 по PR №625: `POISONED_ENV = ("HOME",)`, выключенные
    флаги `os.open`, `MUTATIONS` и обход ловушки проходили самопроверку зелёными).
    Честный пакет — пусто. Без этого проба была бы утверждением, которое никто
    не исполняет."""
    graph = tmp_path / "граф"
    graph.mkdir()
    template = ("import os, pathlib, shutil, tempfile\n"
                "try:\n"                # в копии его нет; найден — значит путь импорта протёк
                "    import task_line\n"
                "except ImportError:\n"
                "    pass\n"
                "V = pathlib.Path({victims!r})\n"
                "class GraphSearch:\n"
                "    once = True\n"      # раннер открывает два экземпляра — дыра срабатывает раз
                "    def __init__(self, graph, *, data_dir, embedder):\n"
                "        self.data = data_dir\n"
                "    def refresh(self, force=False):\n"
                "        (self.data / 'кэш').write_text('x')\n"
                "        if GraphSearch.once:\n"
                "            GraphSearch.once = False\n"
                "            {extra}\n"
                "    def embed_pending(self):\n"
                "        return 1\n"
                "    def load_vectors(self):\n"
                "        return 1\n"
                "    def search(self, q):\n"
                "        import types\n"
                "        return types.SimpleNamespace(ready=True, total=1, text=q)\n")

    def run(name: str, extra: str, *, door: str | None = None, member: str | None = None,
            cli: str | None = None, **kw) -> tuple[list[str], dict]:
        victims = tmp_path / name / "жертва"
        (victims / "d").mkdir(parents=True)
        (victims / "f").write_text("x", encoding="utf-8")
        pkg = tmp_path / name / "pkg"
        pkg.mkdir(parents=True)
        # копия повторяет форму раскладки: раннер зовёт `from charoite_graph import …`
        (pkg / "charoite_graph").mkdir()
        (pkg / "charoite_graph" / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "charoite_graph" / "graph_search.py").write_text(
            template.format(victims=str(victims), extra=extra), encoding="utf-8")
        shutil.copyfile(ROOT / "src" / "charoite_graph" / "model_seam.py",
                        pkg / "charoite_graph" / "model_seam.py")
        if door is not None:
            (pkg / "charoite_graph" / "embed_door.py").write_text(door, encoding="utf-8")
        if member is not None:
            (pkg / "charoite_graph" / "член_вне_входа.py").write_text(member, encoding="utf-8")
        if cli is not None:
            (pkg / "charoite_graph" / "cli.py").write_text(cli, encoding="utf-8")
        (pkg / "лишний_модуль.py").write_text("", encoding="utf-8")
        (pkg / "соседний_модуль.py").write_text("", encoding="utf-8")
        return run_package_probe(pkg, graph, "запрос", tmp_path / name / "work",
                                 app_deps=("лишний_модуль",), outside=("соседний_модуль",), **kw)

    def probe(name: str, extra: str, **kw) -> list[str]:
        return run(name, extra, **kw)[0]

    assert {"POISONED_ENV": POISONED_ENV, "ISOLATION_ENV": ISOLATION_ENV, "ISOLATION_DROP": ISOLATION_DROP,
            "WRITE_FLAG_NAMES": WRITE_FLAG_NAMES} == APPROVED_PROBE, "таблицы пробы — политика, снимок обязателен"
    assert probe("честный", "pass") == []
    # дверь векторов в копии проверяется поведением: пустая дверь — строка пробы, честная —
    # чисто и с результатом; без файла двери проверки нет (вход 4 PR 1 №323, I1)
    empty_door = ("from charoite_graph.model_seam import Embedder\n"
                  "def embedder(base_url, model, **kw):\n"
                  "    return Embedder(lambda texts, timeout: [], model)\n")
    honest_door = ("import json\n"
                   "from charoite_graph.model_seam import Embedder\n"
                   "def embedder(base_url, model, *, post, **kw):\n"
                   "    return Embedder(lambda texts, timeout: json.loads(post(base_url, {'input': texts}, timeout)[1])"
                   "['embeddings'], model)\n")
    got, _ = run("дверь пуста", "pass", door=empty_door)
    assert any("дверь векторов пакета не дала векторов" in line for line in got), got
    got, out = run("дверь честная", "pass", door=honest_door)
    assert got == [] and out["door"] == {"vectors": DOOR_VECTORS}, (got, out.get("door"))
    assert run("без двери", "pass")[1]["door"] == {}, "без файла двери проверки нет"
    # командная строка в копии — поведением (№323 PR 2): кэш по маске вызывающего и файл,
    # созданный лексическим поиском, — каждый своей строкой; честная — чисто, без файла — проверки нет
    cli_template = ("import os, pathlib\n"
                    "def main(argv):\n"
                    "    {mask}\n"
                    "    if argv[0] == 'index':\n"
                    "        d = pathlib.Path(argv[argv.index('--data-dir') + 1])\n"
                    "        d.mkdir(parents=True, exist_ok=True)\n"
                    "        (d / 'm.json').write_text('{{}}')\n"
                    "        return 0\n"
                    "    if '--data-dir' not in argv:\n"
                    "        {leak}\n"
                    "    status = 'confident' if '--data-dir' in argv else 'unverified'\n"
                    "    print('{{\"ready\": true, \"status\": \"%s\", \"sources\": [\"a\"]}}' % status)\n"
                    "    return 0\n")
    honest_cli = cli_template.format(mask="os.umask(0o077)", leak="pass")
    got, out = run("строка честная", "pass", cli=honest_cli)
    assert got == [] and out["cli"]["entry"] is None and out["cli"]["index_code"] == 0, (got, out.get("cli"))
    got = probe("строка по маске", "pass", cli=cli_template.format(mask="os.umask(0o022)", leak="pass"))
    assert len(got) == 1 and "кэш доступен не только владельцу" in got[0], got
    # без своей маски — маска раннера 022, а не маска того, кто запустил pytest (M2 выхода 1)
    got = probe("строка без маски", "pass", cli=cli_template.format(mask="pass", leak="pass"))
    assert len(got) == 1 and "кэш доступен не только владельцу" in got[0], got
    got = probe("строка пишет при поиске", "pass", cli=cli_template.format(
        mask="os.umask(0o077)", leak="(pathlib.Path.cwd().parent / 'data' / 'след').write_text('x')"))
    assert len(got) == 1 and "search без --data-dir создал" in got[0], got
    assert run("без строки", "pass")[1]["cli"] == {}, "без файла командной строки проверки нет"
    got = probe("строка без кэша", "pass", cli=cli_template.format(mask="os.umask(0o077)", leak="pass")
                .replace("(d / 'm.json').write_text('{}')", "pass"))
    assert len(got) == 1 and "не оставил манифеста" in got[0], got
    # член пакета, которого не тянет ни один вход, тоже грузится под ловушкой: раннер
    # обходит каталог пакета копии, а не только замыкание входа (вход 4 хвоста, C1)
    got = probe("член вне входа", "pass",
                member="import os, pathlib\n(pathlib.Path(os.environ['HOME']) / 'config.yaml').read_text()\n")
    assert got and "чтение ловушки" in got[0], got
    # копия ИЗ-вне В data_dir законна: источник копии не запись
    assert probe("копия внутрь", "shutil.copyfile(V / 'f', self.data / 'копия')") == []
    assert "чтение ловушки" in probe("домашний", "(pathlib.Path.home() / 'config.yaml').read_text()")[0]
    for var in POISONED_ENV:
        got = probe(f"env {var}", f"(pathlib.Path(os.environ[{var!r}]) / 'config.yaml').read_text()")
        assert got and "чтение ловушки" in got[0], f"{var} не ведёт в ловушку: {got}"
    assert "чтение ловушки" in probe("tmp", "tempfile.mkstemp()")[0], "TMPDIR читается неявно, tempfile"
    assert "обход ловушки" in probe("listdir", "os.listdir(os.environ['HOME'])")[0]
    assert "обход ловушки" in probe("scandir", "list(os.scandir(os.environ['HOME']))")[0]
    assert "запись вне data_dir" in probe("запись", "(self.data.parent / 'мимо').write_text('x')")[0]
    assert "запись вне data_dir" in probe(
        "os.open", "os.close(os.open(str(self.data.parent / 'мимо'), os.O_WRONLY | os.O_CREAT, 0o644))")[0]
    # каждый флаг записи — сам по себе, на существующем файле вне data_dir
    for flag in APPROVED_PROBE["WRITE_FLAG_NAMES"]:
        got = probe(f"флаг {flag}", f"os.close(os.open(str(V / 'f'), os.{flag}))")
        assert got and "запись вне data_dir" in got[0], f"{flag}: {got}"
    assert probe("чтение вне", "os.close(os.open(str(V / 'f'), os.O_RDONLY)); (V / 'f').read_text()") == [], \
        "чтение вне data_dir и вне ловушки — не запись"
    assert set(MUTATION_CASES) == set(MUTATIONS), "у события мутации нет своего случая — или случай без события"
    for event, code in MUTATION_CASES.items():
        got = probe(f"мутация {event}", code)
        assert got and f"{event} вне data_dir" in got[0], f"{event}: {got}"
    # у событий с двумя путями — каждый аргумент отдельно вне data_dir: записываемый
    # красит, незаписываемый — нет (Important головы круга 2 по PR №625: `os.rename`
    # с одним индексом выпускал данные из data_dir, `os.link` с двумя валил законную ссылку)
    assert set(MUTATION_SIDES) == {e for e, idx in APPROVED_MUTATION_SIDES.items()}
    for event, sides in MUTATION_SIDES.items():
        for i, code in enumerate(sides):
            got = probe(f"сторона {event} {i}", code)
            if i in APPROVED_MUTATION_SIDES[event]:
                assert got and f"{event} вне data_dir" in got[0], f"{event}[{i}] вне data_dir не пойман: {got}"
            else:
                assert got == [], f"{event}[{i}] — не запись, а проба красная: {got}"
    assert {e: MUTATIONS[e] for e in APPROVED_MUTATION_SIDES} == APPROVED_MUTATION_SIDES
    assert "os.rename вне data_dir" in probe("os.replace", "os.replace(V / 'f', V / 'g')")[0], \
        "os.replace приходит событием os.rename"
    assert probe("протечка", "import лишний_модуль") == [
        "пакет импортировал лишний_модуль: зависимость приложения протекла в пакет"]
    assert probe("сосед", "import соседний_модуль") == [
        "модуль соседний_модуль вне замыкания входов пакета загрузился в пакет"]
    got = probe("мимо копии", f"import sys; sys.path.append({str(ROOT / 'src')!r}); import task_line")
    assert f"пакет загрузил {(ROOT / 'src' / 'task_line.py').resolve()} мимо своей копии" in got
    # правка пути импорта в написании, которого грамматика гейта не знает
    got = probe("путь импорта", "import sys as s0; S = s0; S.path.append('/opt/чужое')")
    assert len(got) == 1 and got[0].startswith("пакет правит sys.path") and "/opt/чужое" in got[0], got
    # изоляция: каждая снимаемая переменная, стоявшая у родителя, видна своим признаком
    # (`LEAK_SIGNS`) и снята — со снятием признака нет, проба пуста. Путь ловушки в
    # строке проблемы не сверяется: _first_line режет строку до 160 знаков, и на macOS
    # длинный путь временного каталога (/private/var/folders/…) обрезался раньше
    # «work/ловушка»; путь сверяет признак раннера
    assert set(LEAK_SIGNS) == set(ISOLATION_DROP), "у снимаемой переменной нет своего случая"
    got, out = run("честная изоляция", "pass")
    assert got == [] and out["pycache_prefix"] is None, f"у честной пробы признаки утечки: {got}, {out}"
    for var, (value, sign) in LEAK_SIGNS.items():
        got, out = run(f"утечка {var}", "pass", outer={var: value}, drop=())
        trap = str(tmp_path / f"утечка {var}" / "work" / "ловушка")
        assert sign(got, out, trap), f"{var} у родителя не виден своим признаком: {got}, {out}"
        got, out = run(f"снято {var}", "pass", outer={var: value})
        trap = str(tmp_path / f"снято {var}" / "work" / "ловушка")
        assert got == [] and not sign(got, out, trap), f"{var} не снимается изоляцией: {got}, {out}"
