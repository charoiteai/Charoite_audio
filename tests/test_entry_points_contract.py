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
import graph_search
import model_seam


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
                  "cache": sorted(str(p.relative_to(DATA)) for p in pathlib.Path(DATA).rglob("*") if p.is_file()),
                  "modules": sorted(sys.modules),
                  "files": sorted(os.path.realpath(m.__file__) for m in list(sys.modules.values())
                                  if getattr(m, "__file__", None))}, ensure_ascii=False))
"""


def run_package_probe(pkg: pathlib.Path, graph: pathlib.Path, query: str, work: pathlib.Path, *,
                      forbidden: tuple[str, ...], outer: dict[str, str] | None = None,
                      drop: tuple[str, ...] = ISOLATION_DROP, timeout: int = TIMEOUT) -> tuple[list[str], dict]:
    """Прогнать пакет из каталога `pkg`: вход импортируется отдельным процессом с
    отравленным окружением, индекс строится по `graph`, кэш — только в `data_dir`.
    `outer` — что стояло в окружении родителя до изоляции (значение `{trap}` —
    путь ловушки), `drop` — что изоляция снимает. Расхождения строками (пусто —
    принят) и выдача раннера."""
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
    r = _run([sys.executable, str(runner), str(pkg), str(data), str(graph), str(trap), query], cwd, env, timeout)
    if r.returncode != 0:
        return [f"проба пакета: код {r.returncode} — {_first_line(r)}"], {}
    out = json.loads(r.stdout.strip().splitlines()[-1])
    problems = [f"пакет импортировал {m}: зависимость приложения протекла в пакет"
                for m in sorted(set(out["modules"]) & set(forbidden))]
    # код продукта из репозитория, а не всё под корнем: у сопровождающего `.venv/`
    # лежит в репозитории, и yaml оттуда — законная зависимость пакета
    product = [(ROOT / d).resolve() for d in (lm.FLAT_DIR, lm.DIST_DIR, "scripts")]
    problems += [f"пакет загрузил {f} мимо своей копии" for f in out["files"]
                 if any(pathlib.Path(f).is_relative_to(d) for d in product)]
    # путь импорта — поведением, при любом написании: грамматика гейта видит
    # `sys.path` только по имени, `S = sys; S.path.append(…)` она пропускает
    # (Critical головы круга 2 по PR №625 — граница грамматики, а не новая эвристика)
    if out["path_after"] != out["path_before"]:
        problems.append(f"пакет правит sys.path: было {out['path_before']}, стало {out['path_after']}")
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
    отдельно от репозитория строит индекс по демо-графу, пишет кэш векторов и
    читает его обратно, находит узел — не прочитав ни одной переменной приложения
    (HOME, корень, каталог графа, TMPDIR — ловушка) и не написав ничего вне своего
    `data_dir`."""
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
    # путь записи пройден: векторы собраны, кэш лёг в data_dir и прочитан вторым экземпляром
    assert out["embedded"] > 0 and out["loaded"] == out["embedded"], out
    assert any(c.startswith("graph_search/") and c.endswith(".json") for c in out["cache"]), out["cache"]


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

    def probe(name: str, extra: str, **kw) -> list[str]:
        victims = tmp_path / name / "жертва"
        (victims / "d").mkdir(parents=True)
        (victims / "f").write_text("x", encoding="utf-8")
        pkg = tmp_path / name / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "graph_search.py").write_text(template.format(victims=str(victims), extra=extra), encoding="utf-8")
        shutil.copyfile(ROOT / "src" / "model_seam.py", pkg / "model_seam.py")
        (pkg / "лишний_модуль.py").write_text("", encoding="utf-8")
        return run_package_probe(pkg, graph, "запрос", tmp_path / name / "work",
                                 forbidden=("лишний_модуль",), **kw)[0]

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
    assert "протекла" in probe("протечка", "import лишний_модуль")[0]
    got = probe("мимо копии", f"import sys; sys.path.append({str(ROOT / 'src')!r}); import task_line")
    assert f"пакет загрузил {(ROOT / 'src' / 'task_line.py').resolve()} мимо своей копии" in got
    # правка пути импорта в написании, которого грамматика гейта не знает
    got = probe("путь импорта", "import sys as s0; S = s0; S.path.append('/opt/чужое')")
    assert len(got) == 1 and got[0].startswith("пакет правит sys.path") and "/opt/чужое" in got[0], got
    # изоляция: каждая снимаемая переменная, стоявшая у родителя, опасна (без снятия
    # проба красная) и снята (со снятием — пусто). Значение у каждой своё: каталоги
    # пути импорта интерпретатор читает ещё до хука, поэтому PYTHONPATH ловится не
    # ловушкой, а модулем продукта, взятым мимо копии.
    leaks = {"PYTHONPATH": str(ROOT / "src"), "PYTHONHOME": "{trap}", "PYTHONPYCACHEPREFIX": "{trap}"}
    assert set(leaks) == set(ISOLATION_DROP), "у снимаемой переменной нет своего случая"
    for var, value in leaks.items():
        leaked = probe(f"утечка {var}", "pass", outer={var: value}, drop=())
        assert leaked, f"{var} у родителя ничего не ломает — случай изоляции ничего не проверяет"
        assert probe(f"снято {var}", "pass", outer={var: value}) == [], f"{var} не снимается изоляцией"
