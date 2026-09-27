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
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile

import pytest
import yaml

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
# одного объявленного входа (`package_entry` в артефакте), план даёт
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
WHEEL_TIMEOUT = 180
#: Файл CI — тот же путь, откуда `scripts/preflight.sh` читает `RUFF_VERSION`:
#: пин setuptools для сборки берётся из его секции `env`.
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
#: Следы ручной сборки колеса в каталоге дистрибутива — копия раскладки для
#: фикстуры их не берёт; те же имена не видит git (`.gitignore`).
BUILD_LEFTOVERS = ("build", "dist", "*.egg-info")
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
from charoite_graph import graph_search, model_seam


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
print(json.dumps({"ready": result.ready, "total": result.total, "text": result.text,
                  "embedded": embedded, "loaded": loaded, "path_before": PATH_BEFORE, "path_after": sys.path,
                  "pulled_by_init": PULLED_BY_INIT, "inits": INITS,
                  "cache": sorted(str(p.relative_to(DATA)) for p in pathlib.Path(DATA).rglob("*") if p.is_file()),
                  "modules": sorted(sys.modules),
                  "files": sorted(os.path.realpath(m.__file__) for m in list(sys.modules.values())
                                  if getattr(m, "__file__", None))}, ensure_ascii=False))
"""


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
    env = {k: v for k, v in parent.items() if k not in drop}
    env.update(ISOLATION_ENV)
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
    problems += [f"модуль {m} вне замыкания package_entry загрузился в пакет" for m in sorted(loaded & set(outside))]
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
    return problems, out


def _ci_env(name: str) -> str:
    """Значение `name` из секции `env` файла CI — тот же путь и тот же разбор,
    что у `scripts/preflight.sh` для `RUFF_VERSION`: пин setuptools живёт там, а
    не отдельной константой теста, которая переживёт смену пина молча."""
    return str(yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["env"][name])


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


def _copy_layout(src: pathlib.Path, work: pathlib.Path, package: str) -> None:
    """Копия раскладки для сборки колеса: каталог дистрибутива и каталог пакета
    лежат в `work` так же, как в `src`, поэтому `package-dir` разрешается в саму
    копию. Следы ручной сборки по README (`BUILD_LEFTOVERS`) не копируются:
    сборка без изоляции подхватила бы старый `build/lib`, и сверка плана краснела
    бы лишним модулем не по делу (опыт 27.09 по #657)."""
    (work / "packages").mkdir()
    shutil.copytree(src / "packages" / "charoite-graph", work / "packages" / "charoite-graph",
                    ignore=shutil.ignore_patterns(*BUILD_LEFTOVERS))
    shutil.copytree(src / lm.FLAT_DIR / package, work / lm.FLAT_DIR / package)


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


def _wheel_plan_problems(built: pathlib.Path) -> list[str]:
    """Сверка плана пробы с НАСТОЯЩИМ артефактом: имена `*.py` из колеса против
    `artifact_name` каждого файла `package_files`, в обе стороны. Списком строк —
    чтобы и честная сборка, и порченая копия судились одним кодом, а расхождение
    называло лишние и недостающие модули, а не только факт неравенства (№427)."""
    want = {lm.artifact_name(rel) for rel in lm.package_files(INV, lm.load_layout())}
    with zipfile.ZipFile(built) as archive:
        got = {name for name in archive.namelist() if name.endswith(".py")}
    избыток = sorted(got - want)
    недостача = sorted(want - got)
    out = []
    if избыток:
        out.append("в артефакте лишние модули: " + ", ".join(избыток))
    if недостача:
        out.append("в артефакте нет модулей плана: " + ", ".join(недостача))
    return out


def _copy_package(dest: pathlib.Path) -> None:
    """Копия КАТАЛОГА пакета целиком (`src/<пакет>/`) в `dest/<пакет>/` — не
    файлы плана `package_files`: отрицательные пробы судят ПРОБУ и портят копию
    по месту, а план и его сверка с колесом тут ни при чём. Форма та же, что у
    репозитория, поэтому раннер зовёт `from charoite_graph import …` без правок."""
    package = lm.load_layout()["package"]
    dest.mkdir()
    shutil.copytree(ROOT / lm.FLAT_DIR / package, dest / package)


def test_the_graph_package_runs_without_the_app(tmp_path: pathlib.Path, wheel_path: pathlib.Path) -> None:
    """Пакет поиска — это замыкание `package_entry` и ничего больше, и едет он в
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
    closure = lm.package_closure(lm.import_graph(INV), layout["package_entry"])
    assert layout["package_entry"] in closure and closure <= modules
    others = tuple(sorted(lm.modules(INV) - modules))
    problems, out = run_package_probe(pkg, graph, "платёжный шлюз", tmp_path / "work", outside=others)
    # раннер мерил ровно `__init__` плана — список из распакованного артефакта сходится
    assert set(out["inits"]) == lm.package_inits(INV, layout), out["inits"]
    assert not problems, "\n".join(problems)
    assert out["ready"] and out["total"], f"индекс по демо-графу пуст: {out}"
    assert "Платёжный шлюз" in out["text"], f"поиск не нашёл узел демо-графа: {out['text'][:300]}"
    assert closure <= set(out["modules"]), "проба импортирует не всё замыкание входа"
    # путь записи пройден: векторы собраны, кэш лёг в data_dir и прочитан вторым экземпляром
    assert out["embedded"] > 0 and out["loaded"] == out["embedded"], out
    assert any(c.startswith("graph_search/") and c.endswith(".json") for c in out["cache"]), out["cache"]


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

    def run(name: str, extra: str, **kw) -> tuple[list[str], dict]:
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
        (pkg / "лишний_модуль.py").write_text("", encoding="utf-8")
        (pkg / "соседний_модуль.py").write_text("", encoding="utf-8")
        return run_package_probe(pkg, graph, "запрос", tmp_path / name / "work",
                                 app_deps=("лишний_модуль",), outside=("соседний_модуль",), **kw)

    def probe(name: str, extra: str, **kw) -> list[str]:
        return run(name, extra, **kw)[0]

    assert {"POISONED_ENV": POISONED_ENV, "ISOLATION_ENV": ISOLATION_ENV, "ISOLATION_DROP": ISOLATION_DROP,
            "WRITE_FLAG_NAMES": WRITE_FLAG_NAMES} == APPROVED_PROBE, "таблицы пробы — политика, снимок обязателен"
    assert probe("честный", "pass") == []
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
        "модуль соседний_модуль вне замыкания package_entry загрузился в пакет"]
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
