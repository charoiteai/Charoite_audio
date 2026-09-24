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

import json
import os
import pathlib
import shutil
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
# одного объявленного входа (`package_entry` в артефакте), план копирования даёт
# `layout_map.package_files`, а не список здесь. Статический гейт окружения по
# слою видит формы в тексте; проба — поведение: копия замыкания во временном
# каталоге, отдельный процесс, окружение приложения ведёт в ловушку.

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
#: Код выхода, которым аудит-хук валит пробу. Хук не бросает исключение, а
#: выходит сразу: `except Exception` в коде пакета проглотил бы исключение молча.
AUDIT_EXIT = 97

#: Раннер пробы — отдельным процессом. Путь к копии пакета вставляет он сам, а не
#: окружение: с SAFEPATH каталог скрипта в sys.path не попадает.
PROBE_RUNNER = r'''
import json, os, pathlib, sys

PKG, DATA, GRAPH, TRAP, QUERY = sys.argv[1:6]
TRAP_REAL, DATA_REAL = os.path.realpath(TRAP), os.path.realpath(DATA)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
MUTATIONS = ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.replace", "os.truncate",
             "os.link", "os.symlink", "os.chmod", "os.utime", "shutil.rmtree", "shutil.copyfile")


def inside(path, root):
    try:
        real = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return False
    return real == root or real.startswith(root + os.sep)


def fail(what):
    sys.stderr.write(f"АУДИТ: {what}\n")
    sys.stderr.flush()
    os._exit(''' + str(AUDIT_EXIT) + r''')


def hook(event, args):
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None))[:3]
        if path is None or isinstance(path, int):
            return
        if inside(path, TRAP_REAL):
            fail(f"чтение ловушки {path}")
        writes = bool(mode) and any(c in str(mode) for c in "wax+") or bool((flags or 0) & WRITE_FLAGS)
        if writes and not inside(path, DATA_REAL):
            fail(f"запись вне data_dir: {path}")
    elif event in ("os.listdir", "os.scandir"):
        if args and args[0] is not None and inside(args[0], TRAP_REAL):
            fail(f"обход ловушки {args[0]}")
    elif event in MUTATIONS:
        for path in args[:2]:
            if isinstance(path, (str, bytes, os.PathLike)) and not inside(path, DATA_REAL):
                fail(f"{event} вне data_dir: {path}")


sys.addaudithook(hook)
sys.path.insert(0, PKG)
import graph_search
import model_seam


def refuse(texts, timeout):
    raise model_seam.SeamTransportError("проба пакета: моделей нет", policy=True)


search = graph_search.GraphSearch(pathlib.Path(GRAPH), data_dir=pathlib.Path(DATA),
                                  embedder=model_seam.Embedder(refuse, model_seam.NO_MODEL, refused="проба пакета"))
search.refresh(force=True)
result = search.search(QUERY)
print(json.dumps({"ready": result.ready, "total": result.total, "text": result.text,
                  "modules": sorted(sys.modules),
                  "files": sorted(os.path.realpath(m.__file__) for m in list(sys.modules.values())
                                  if getattr(m, "__file__", None))}, ensure_ascii=False))
'''


def run_package_probe(pkg: pathlib.Path, graph: pathlib.Path, query: str, work: pathlib.Path, *,
                      forbidden: tuple[str, ...], timeout: int = TIMEOUT) -> tuple[list[str], dict]:
    """Прогнать пакет из каталога `pkg`: вход импортируется отдельным процессом с
    отравленным окружением, индекс строится по `graph`, кэш — только в `data_dir`.
    Расхождения строками (пусто — принят) и выдача раннера."""
    trap, data, cwd = work / "ловушка", work / "data", work / "cwd"
    for d in (trap, data, cwd):
        d.mkdir(parents=True)
    (trap / "config.yaml").write_text("ловушка: читать нельзя\n", encoding="utf-8")
    runner = work / "probe_runner.py"
    runner.write_text(PROBE_RUNNER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ISOLATION_DROP}
    env.update(ISOLATION_ENV)
    env.update({k: str(trap) for k in POISONED_ENV})
    r = _run([sys.executable, str(runner), str(pkg), str(data), str(graph), str(trap), query], cwd, env, timeout)
    if r.returncode != 0:
        return [f"проба пакета: код {r.returncode} — {_first_line(r)}"], {}
    out = json.loads(r.stdout.strip().splitlines()[-1])
    problems = [f"пакет импортировал {m}: зависимость приложения протекла в пакет"
                for m in sorted(set(out["modules"]) & set(forbidden))]
    problems += [f"пакет загрузил {f} мимо своей копии" for f in out["files"]
                 if pathlib.Path(f).is_relative_to(ROOT.resolve())]
    return problems, out


def _copy_package(dest: pathlib.Path) -> list[str]:
    """Копия замыкания входа по плану сторожа — плоско, имя модуля в имя файла."""
    rels = lm.package_files(lm.inventory(), lm.load_layout())
    dest.mkdir()
    for rel in rels:
        name = lm.module_of(rel)
        assert name is not None and "." not in name, f"{rel}: пакетная форма — копия пробы её ещё не знает"
        shutil.copyfile(ROOT / rel, dest / f"{name}.py")
    return rels


def test_the_graph_package_runs_without_the_app(tmp_path: pathlib.Path) -> None:
    """Пакет поиска — это замыкание `package_entry` и ничего больше: копия
    отдельно от репозитория строит индекс по демо-графу и находит узел, не
    прочитав ни одной переменной приложения (HOME, корень, каталог графа, TMPDIR —
    ловушка) и не написав ничего вне своего `data_dir`."""
    layout = lm.load_layout()
    rels = _copy_package(tmp_path / "pkg")
    graph = tmp_path / "work" / "Демо"
    shutil.copytree(ROOT / "demo" / "graph", graph)
    closure = {lm.module_of(rel) for rel in rels}
    assert layout["package_entry"] in closure
    others = tuple(sorted(lm.modules(lm.inventory()) - closure))
    problems, out = run_package_probe(tmp_path / "pkg", graph, "платёжный шлюз", tmp_path / "work",
                                      forbidden=APP_ONLY_DEPS + others)
    assert not problems, "\n".join(problems)
    assert out["ready"] and out["total"], f"индекс по демо-графу пуст: {out}"
    assert "Платёжный шлюз" in out["text"], f"поиск не нашёл узел демо-графа: {out['text'][:300]}"
    assert closure <= set(out["modules"]), "проба импортирует не весь пакет — план копирования шире нужного"


def test_the_package_probe_catches_what_it_guards(tmp_path: pathlib.Path) -> None:
    """Проба проверена «дырявыми» пакетами: чтение ловушки через HOME, запись вне
    `data_dir`, протечка зависимости по `sys.modules` — каждый даёт расхождение,
    честный пакет — пусто. Без этого проба была бы утверждением, которое никто
    не исполняет."""
    graph = tmp_path / "граф"
    graph.mkdir()
    template = ("import pathlib\n"
                "class GraphSearch:\n"
                "    def __init__(self, graph, *, data_dir, embedder):\n"
                "        self.data = data_dir\n"
                "    def refresh(self, force=False):\n"
                "        (self.data / 'кэш').write_text('x')\n"
                "        {extra}\n"
                "    def search(self, q):\n"
                "        import types\n"
                "        return types.SimpleNamespace(ready=True, total=1, text=q)\n")
    cases = {
        "честный": "pass",
        "домашний": "(pathlib.Path.home() / 'config.yaml').read_text()",
        "запись": "(self.data.parent / 'мимо').write_text('x')",
        "протечка": "import лишний_модуль",
    }
    got = {}
    for name, extra in cases.items():
        pkg = tmp_path / name / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "graph_search.py").write_text(template.replace("{extra}", extra), encoding="utf-8")
        shutil.copyfile(ROOT / "src" / "model_seam.py", pkg / "model_seam.py")
        (pkg / "лишний_модуль.py").write_text("", encoding="utf-8")
        got[name], _ = run_package_probe(pkg, graph, "запрос", tmp_path / name / "work",
                                         forbidden=("лишний_модуль",))
    assert got["честный"] == []
    assert "чтение ловушки" in got["домашний"][0]
    assert "запись вне data_dir" in got["запись"][0]
    assert "протекла" in got["протечка"][0]
