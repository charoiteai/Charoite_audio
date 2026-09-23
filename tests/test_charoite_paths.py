"""Рабочий корень: код в поставке, данные у человека.

С вложенным в приложение python-контуром код переехал внутрь подписанного
бандла — писать туда записи и стенограммы нельзя. CHAROITE_ROOT разводит
код и данные; запуск из репозитория обязан ничего не заметить.

Вторую половину договора — «за КОДОМ ходят не через корень данных» — знал
только Swift-слой, и это стоило дорого (аудит 0.46.0, P0-2 и P0-8):
26 мест в python строили путь к соседнему модулю как `ROOT / "src" / …`.
В репозитории оба корня совпадают, поэтому дефект был невидим; во вложенной
установке `src/` в папке данных нет — и `_recover_orphans` молча спавнил
несуществующий файл, потомок умирал с кодом 2 в DEVNULL, встреча вечно
висела в «recovering», а через `record_keep_days` ретеншн добивал её запись.

Поэтому тесты ниже идут парой: поведенческий (пути, по которым продукт
реально спавнит, обязаны существовать при перенесённых данных) и сторож по
разбору кода (никто не строит путь к `src/`/`scripts/` от корня данных).
Одного поведенческого мало: скрипты нельзя импортировать без сайд-эффектов.
"""
import ast
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map  # noqa: E402 — формы путей берутся у модели раскладки, не литералами

#: Откуда код зовёт канон: плоский модуль, скрипт и модуль пакета после переезда
#: (фаза 1). Пути строит модель раскладки: поменяется форма — поменяется и то,
#: что здесь проверяется (входной круг №331, Opus I4).
ФОРМЫ_ВЫЗЫВАЮЩЕГО = {
    "плоский модуль": f"{layout_map.FLAT_DIR}/audio.py",
    "скрипт": "scripts/doctor.py",
    "модуль пакета": (f"{layout_map.DIST_DIR}/charoite-graph/{layout_map.FLAT_DIR}/"
                      "charoite_graph/graphs.py"),
}

#: Спросить оба корня у канона копии, а не у того, что уже импортирован прогоном.
_СПРОСИТЬ_КОРНИ = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import charoite_paths as c\n"
    "print(c.code_root(sys.argv[2]))\n"
    "print(c.resolve_root(sys.argv[2]))\n"
    "os.environ.pop('CHAROITE_ROOT')\n"
    "print(c.resolve_root(sys.argv[2]))\n"
)


def _установка_с_каноном(корень: pathlib.Path) -> pathlib.Path:
    """Дерево кода, в котором канон лежит на своём месте: `<корень>/src/charoite_paths.py`.

    Рядом — `exit_codes`, который канон берёт лениво на отказе двери входа.
    Корень кода один на процесс и берётся у канона, поэтому догадку «корень
    данных = корень кода» можно спросить только у копии в мнимом дереве:
    канон прогона ответил бы настоящим checkout, а называть его корнем тесту
    нельзя даже ради проверки (сторож изоляции прав).
    """
    (корень / "src").mkdir(parents=True, exist_ok=True)
    for имя in ("charoite_paths.py", "exit_codes.py"):
        shutil.copy2(ROOT / "src" / имя, корень / "src" / имя)
    return корень


def _вход_в_установке(tmp_path, тело: str, *, root: str | None = None,
                      где: str = "scripts") -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """Точка входа `entry.py` мнимой установки как ПРОЦЕСС: (прогон, корень установки)."""
    установка = _установка_с_каноном(tmp_path / "установка")
    вход = установка / где / "entry.py"          # имя файла видно в рецепте отказа
    вход.parent.mkdir(parents=True, exist_ok=True)
    вход.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(установка / 'src')!r})\n"
        "import charoite_paths\n" + тело,
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    if root is not None:
        env["CHAROITE_ROOT"] = root
    прогон = subprocess.run([sys.executable, str(вход)], capture_output=True, text=True,
                            env=env, cwd=tmp_path, timeout=60)
    return прогон, установка.resolve()


@pytest.mark.parametrize("форма", sorted(ФОРМЫ_ВЫЗЫВАЮЩЕГО))
def test_корень_кода_один_из_любой_формы_вызывающего(tmp_path, форма):
    """Корень кода — там, где лежит канон, из какой бы формы его ни спросили.

    Меряется на чужом дереве, и ожидание строит фикстура, а не формула
    проверяемого кода: пока обе стороны считали `parent.parent`, перенос модуля
    на ступень двигал их вместе и тест оставался зелёным на сломанном каноне
    (GLM C3 по №327). Модуль пакета лежит на две ступени глубже плоского:
    прежний подъём от файла вызывающего дал бы корнем кода `packages/<дист>/`,
    а корнем данных — `packages/<дист>/src/` (входной круг №331, обе головы).

    Три ответа одного процесса: корень кода при заданной переменной (код её не
    слушает), корень данных при ней же (слушает) и третий ответ канона без
    переменной — тот же корень кода, а не своя копия подъёма.
    """
    rel = ФОРМЫ_ВЫЗЫВАЮЩЕГО[форма]
    if форма != "скрипт":
        assert layout_map.form(rel).role == "module", f"{rel}: модель раскладки не видит здесь модуль"
    установка = _установка_с_каноном(tmp_path / "установка")
    файл = установка / rel
    файл.parent.mkdir(parents=True, exist_ok=True)
    файл.touch()
    данные = tmp_path / "данные"
    out = subprocess.run(
        [sys.executable, "-c", _СПРОСИТЬ_КОРНИ, str(установка / "src"), str(файл)],
        env=dict(os.environ, CHAROITE_ROOT=str(данные)),
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-600:]
    код, данных, без_переменной = (pathlib.Path(s) for s in out.stdout.splitlines())
    assert код == установка.resolve(), f"{форма}: корень кода ушёл от места канона"
    assert данных == данные.resolve(), f"{форма}: переменная обязана задавать корень данных"
    assert без_переменной == установка.resolve(), f"{форма}: третий ответ — не корень кода"


@pytest.mark.корень_называет_тест
def test_файл_чужого_дерева_корня_кода_не_получает(tmp_path):
    """Процесс, собранный из двух деревьев, — отказ, а не пути в чужую копию.

    Корень кода один на процесс и берётся у канона; файл вызывающего только
    сверяется. Лежит он вне дерева канона — значит, `sys.path` собран из двух
    копий, и соседние модули нашлись бы не рядом с вызывающим (входной круг
    №331, Opus, критика решения 2). Третий ответ `resolve_root` идёт через ту
    же сверку: своего подъёма у него больше нет.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    чужой = tmp_path / "другая-копия" / "src" / "audio.py"
    with pytest.raises(RuntimeError, match="вне дерева кода"):
        charoite_paths.code_root(str(чужой))
    with pytest.raises(RuntimeError, match="вне дерева кода"):
        charoite_paths.resolve_root(str(чужой))


def test_канон_не_на_своём_месте_отказывает_на_импорте(tmp_path):
    """Переезд канона без правки его места — отказ на импорте, а не съехавший корень.

    Корень кода выводится из места канона. Фаза core перевода в пакеты сдвинет
    сам канон в `packages/<дист>/src/<пакет>/`, и тот же вывод без правки
    `_CANON_IN_CODE_ROOT` молча дал бы корнем кода `packages/<дист>/src/`
    (входной круг №331, Opus I1 и «Как чинить»).
    """
    пакет = (tmp_path / layout_map.DIST_DIR / "charoite-core" / layout_map.FLAT_DIR
             / "charoite_core")
    пакет.mkdir(parents=True)
    shutil.copy2(ROOT / "src" / "charoite_paths.py", пакет / "charoite_paths.py")
    out = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import charoite_paths",
         str(пакет)],
        capture_output=True, text=True, timeout=60)
    assert out.returncode != 0, "канон вне своего места импортировался и вывел корень"
    assert "RuntimeError" in out.stderr and "_CANON_IN_CODE_ROOT" in out.stderr, out.stderr[-600:]


@pytest.mark.корень_называет_тест
def test_переменная_переопределяет_корень(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import resolve_root
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    assert resolve_root(str(ROOT / "src" / "audio.py")) == tmp_path.resolve()


@pytest.mark.корень_называет_тест
def test_пустая_переменная_считается_незаданной(monkeypatch):
    """`CHAROITE_ROOT=` в окружении иначе увёл бы все пути в текущий каталог —
    записи встречи оказались бы там, откуда запустили приложение."""
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import resolve_root
    monkeypatch.setenv("CHAROITE_ROOT", "   ")
    assert resolve_root(str(ROOT / "src" / "audio.py")) == ROOT


def test_модули_демона_уважают_переменную(tmp_path):
    """Сквозная проверка: не только функция, но и модули, которые пишут
    записи и стенограммы. Проверяем в отдельном процессе — модули кэшируются
    импортом, и подмена переменной внутри теста ничего бы не изменила."""
    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    code = (
        "import sys; sys.path.insert(0, 'src')\n"
        "import audio, daemon, meeting_archive\n"
        "print(audio._root()); print(daemon._root()); print(meeting_archive._root())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    for line in out.stdout.strip().splitlines():
        assert pathlib.Path(line) == tmp_path.resolve(), f"{line} мимо CHAROITE_ROOT"


def _root_reaches_code(tree: ast.AST) -> list[str]:
    """Места, где путь к КОДУ строится от корня ДАННЫХ: `ROOT / "src"`."""
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        left, right = node.left, node.right
        if (isinstance(left, ast.Name) and left.id == "ROOT"
                and isinstance(right, ast.Constant)
                and right.value in ("src", "scripts")):
            bad.append(f'строка {node.lineno}: ROOT / "{right.value}"')
    return bad


def test_за_кодом_никто_не_ходит_через_корень_данных():
    """Сторож класса, а не одного места.

    Смотрит на выражение в разборе кода, а не на подстроку в файле: аудит
    0.45.0 показал, что сторож, проверяющий наличие слов, пропускает
    обезвреженный вызов. Здесь пропустить нечего — либо путь построен от
    корня данных, либо нет.
    """
    offenders = {}
    for folder in ("src", "scripts"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            found = _root_reaches_code(ast.parse(path.read_text(encoding="utf-8")))
            if found:
                offenders[str(path.relative_to(ROOT))] = found
    assert not offenders, (
        "путь к коду строится от корня данных — во вложенной установке этого "
        f"файла там нет: {offenders}. Берите CODE (code_root), не ROOT")


def _пути_от_корня_кода(tree: ast.Module) -> list[tuple[int, str]]:
    """Пути из одних констант, построенные от корня КОДА: `(строка, путь)`.

    Корень кода — вызов `code_root(...)`, константа `CODE_ROOT` (под любым
    псевдонимом импорта — имена берёт тот же разбор, что у сторожа раскладки)
    и имя, которому в файле присвоено одно из них. `(code or CODE_ROOT) / …` —
    тоже от корня кода: без аргумента путь ведёт именно туда. Сегмент из
    f-строки или переменной разбором не проверить — такой путь пропускается,
    это честная граница сторожа.
    """
    вызовы = layout_map._canon_names(tree, ("code_root",))
    константы = layout_map._canon_names(tree, ("CODE_ROOT",))
    связанные: set[str] = set()

    def корень(e: ast.expr) -> bool:
        if isinstance(e, ast.Call):
            return ast.unparse(e.func) in вызовы
        if isinstance(e, (ast.Name, ast.Attribute)):
            return ast.unparse(e) in константы | связанные
        if isinstance(e, ast.BoolOp):
            return any(корень(v) for v in e.values)
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and корень(node.value):
            связанные |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    пути = []
    for node in ast.walk(tree):
        части, база = [], node
        while (isinstance(база, ast.BinOp) and isinstance(база.op, ast.Div)
               and isinstance(база.right, ast.Constant) and isinstance(база.right.value, str)):
            части.append(база.right.value)
            база = база.left
        if части and корень(база):
            пути.append((node.lineno, "/".join(reversed(части))))
    return пути


def test_разбор_путей_от_корня_кода_видит_все_формы():
    """Сторож ниже зелёный и тогда, когда просто ничего не нашёл, — поэтому формы
    записи проверяются на пробе, а не только на отсутствии красного."""
    проба = "\n".join([
        "import charoite_paths as cp",
        "from charoite_paths import code_root as где_код, CODE_ROOT, resolve_root",
        "CODE = где_код(__file__)",
        "ROOT = resolve_root(__file__)",
        'A = CODE / "scripts" / "a.py"',
        'B = (code or CODE_ROOT) / "config" / "b.yaml"',
        'C = cp.CODE_ROOT / "c"',
        'D = cp.code_root(__file__) / "d"',
        'E = CODE / "src" / f"{имя}.py"',
        'F = ROOT / "logs"',
    ])
    найдено = {путь for _, путь in _пути_от_корня_кода(ast.parse(проба))}
    assert найдено == {"scripts", "scripts/a.py", "config", "config/b.yaml", "c", "d", "src"}


def test_пути_от_корня_кода_ведут_в_существующие_файлы():
    """Путь к коду, собранный из констант, обязан вести в файл этого дерева.

    Корень кода проверяли, а сегменты после него — никто. Так #607 потерял
    сегмент `scripts`: импорт внешней записи звал `CODE / "import_meeting.py"`,
    файла в корне нет, ребёнок падал с кодом 2, и каждый файл из папки импорта
    уходил в метку ошибки, а тест с подменённым `run_child` оставался зелёным
    (входной круг №331, Opus C2). После переезда модулей в пакеты (фаза 1) тот
    же сторож покраснеет на каждом `CODE / "src" / "<переехавший>.py"`.

    Области — из объявления сторожа раскладки: пакеты попадут под проверку, как
    только появятся.
    """
    нет, всего = {}, 0
    for область in layout_map.PYTHON_AREAS:
        for path in sorted((ROOT / область).rglob("*.py")):
            for строка, путь in _пути_от_корня_кода(ast.parse(path.read_text(encoding="utf-8"))):
                всего += 1
                if not (ROOT / путь).exists():
                    нет.setdefault(str(path.relative_to(ROOT)), []).append(f"строка {строка}: {путь}")
    assert всего, "сторож не нашёл ни одного пути от корня кода — разбор сломан"
    assert not нет, f"пути от корня кода ведут в пустоту: {нет}"


def test_ночные_скрипты_пишут_в_корень_данных(tmp_path):
    """Конфиг и отметки ночного цикла — у человека, а не в бандле.

    Оба места нашлись ревью 19.08 в одной ветке: конфиг графа вёл к
    config.yaml рядом с кодом (во вложенной установке его там нет — ночь
    читала бы дефолты и игнорировала выключатели профиля), а
    `tier3_cores.STAMPS` писал отметку прогона в read-only бандл — то есть
    падал бы PermissionError на первой же ночи. Проверяем в отдельном
    процессе: отметка ночи и конфиг графа считаются НА ВЫЗОВЕ (снапшот на импорте
    снят правилом №338), и оба обязаны лечь в корень ДАННЫХ.
    """
    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    code = (
        "import sys; sys.path.insert(0, 'src'); sys.path.insert(0, 'scripts')\n"
        "import graphs, tier3_cores\n"
        "print(graphs.config_path()); print(tier3_cores.stamps_path())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    config, stamps = out.stdout.strip().splitlines()
    assert pathlib.Path(config) == tmp_path.resolve() / "config" / "config.yaml"
    assert pathlib.Path(stamps).parent == tmp_path.resolve() / "logs"


# ---------------------------------------------------------------- №327: кто хозяин корня

@pytest.mark.корень_называет_тест
def test_действующий_корень_не_переименовывается_молча(tmp_path, monkeypatch):
    """Корень, от которого уже отведены пути, — не переименовывают.

    Действующим его делает не только сеттер: `CHAROITE_ROOT` ставит
    приложение ещё до запуска, и от неё уже посчитаны корни в модулях,
    которые импортировались раньше. Сравнивать только со своей записью
    `_given` мало: при пустой записи второй корень проходил молча, и процесс
    получал два (круг 2 по коду №327, DS C2).

    Осознанная смена — через дверь `forget_data_root`, а не через второй
    вызов: всё, что отведено от прежнего корня, после неё недостоверно, и
    называется это честно.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    из_переменной, другой = tmp_path / "переменная", tmp_path / "другой"
    monkeypatch.setenv("CHAROITE_ROOT", str(из_переменной))
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == из_переменной.resolve()
    with pytest.raises(RuntimeError):
        charoite_paths.use_data_root(другой)
    assert charoite_paths.use_data_root(из_переменной) == из_переменной.resolve()   # то же — молча
    # осознанная смена — одной операцией: окна «корня нет вовсе» не бывает
    assert charoite_paths.use_data_root(другой, replace=True) == другой.resolve()
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == другой.resolve()


@pytest.mark.корень_называет_тест
def test_названный_корень_уезжает_детям_в_окружение(tmp_path):
    """Дети считают корень сами — и обязаны получить тот же ответ.

    Ночные скрипты, индексатор и облачный воркер запускаются отдельными
    процессами и строят корень по `CHAROITE_ROOT`. Назови корень только
    внутри процесса — ребёнок судил бы о живой встрече по чужому
    `logs/daemon.lock` (круг 1 по коду №327, DS I2).
    """
    sys.path.insert(0, str(ROOT / "src"))
    названный = tmp_path / "данные-человека"
    код = (
        f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "import charoite_paths, os, subprocess, sys\n"
        f"charoite_paths.use_data_root({str(названный)!r})\n"
        "print(subprocess.run([sys.executable, '-c',"
        " 'import os; print(os.environ.get(\"CHAROITE_ROOT\", \"(нет)\"))'],"
        " capture_output=True, text=True).stdout.strip())\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    out = subprocess.run([sys.executable, "-c", код], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert pathlib.Path(out.stdout.strip()) == названный.resolve()


@pytest.mark.parametrize("пусто", ["", "   ", None, pathlib.Path(""), ".", pathlib.Path(".")])
@pytest.mark.корень_называет_тест
def test_пустой_корень_отказ_а_не_текущий_каталог(tmp_path, monkeypatch, пусто):
    """Пустое поле настроек — не «здесь», а отсутствие ответа.

    `Path("").resolve()` — это каталог, из которого запустили процесс, а
    `Path(" ")` — подкаталог с пробелом в имени. Обе догадки уводят записи и
    граф туда, откуда запустили: дефект карточки №36 вернулся бы через новую
    дверь (круг 1 по коду №327, DS C1 и GLM M4).

    `Path("")` проверяется отдельной строкой параметров: он равен
    `PosixPath('.')` и истинен, поэтому до гейта доезжал как непустой «.» и
    проходил его (круг 2 по коду №327, GLM I1).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        charoite_paths.use_data_root(пусто)
    assert charoite_paths._given is None


@pytest.mark.корень_называет_тест
def test_второй_корень_отказ_а_не_тихая_перезапись(tmp_path):
    """Корень называют один раз: объекты, собранные до, пишут по старому пути.

    Поиск графа кэширует каталог векторов в конструкторе. Перезапиши корень
    посреди работы — писатель остался бы в одном каталоге, а читатель ушёл в
    другой; это тот же класс, что №36 (круг 1 по коду №327, обе головы).
    Повтор с тем же значением безвреден и разрешён: точка входа может
    назвать корень дважды по одному и тому же конфигу.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    первый = charoite_paths.use_data_root(tmp_path / "первый")
    assert charoite_paths.use_data_root(tmp_path / "первый") == первый   # повтор — молча
    with pytest.raises(RuntimeError):
        charoite_paths.use_data_root(tmp_path / "второй")
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == первый


@pytest.mark.корень_называет_тест
def test_названный_корень_не_отменяется_переменной_посреди_работы(tmp_path, monkeypatch):
    """Решение точки входа сильнее окружения — и остаётся сильнее потом.

    Корень уезжает в `CHAROITE_ROOT` ради детей процесса, но канал этот
    общий: переменную вправе переписать кто угодно — чужая библиотека, тест,
    соседний код. Названный корень так отменяться не должен, иначе половина
    процесса продолжит писать по старому пути, а половина уйдёт по новому
    (мутация круга 2 по №327: без этой ветки защита держалась только на
    окружении и ни одним тестом не проверялась).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    названный = charoite_paths.use_data_root(tmp_path / "названный")
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path / "перетёртый"))
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == названный


@pytest.mark.корень_называет_тест
def test_забыть_корень_возвращает_мир_в_состояние_до_вызова(tmp_path):
    """Дверь сброса отзывает публикацию: как будто её и не было.

    Прямой свидетель самой двери, а не вывод из чужого сценария: в круге 3
    этой мутации был назначен тест, который её увидеть не мог, — и точечный
    прогон объявил бы мутацию мёртвой (DS C1 круга 3).

    Снимать заодно и переменную нельзя: между «забыть» и «назвать заново»
    процесс и его дети отвечали бы корнем КОДА, а в бандловой установке это
    подписанная read-only папка (DS I1 круга 3).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    названный = charoite_paths.use_data_root(tmp_path / "данные")
    assert os.environ.get("CHAROITE_ROOT") == str(названный)   # пока назван — дети знают
    charoite_paths.forget_data_root()
    # публикация отозвана: мир вернулся в состояние до вызова — переменной не
    # было, и её снова нет; процесс корня не знает и волен назвать заново
    assert os.environ.get("CHAROITE_ROOT") is None
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) != названный
    другой = charoite_paths.use_data_root(tmp_path / "другой")
    assert другой == (tmp_path / "другой").resolve()


@pytest.mark.корень_называет_тест
def test_сброс_возвращает_решение_приложения_а_не_корень_кода(tmp_path, monkeypatch):
    """Боевой порядок: приложение назвало корень, точка входа повторила его.

    Приложение ставит `CHAROITE_ROOT` ДО старта python, демон читает её и
    зовёт `use_data_root` с тем же путём — после нормализации значения
    совпадают дословно. Отличать «своё эхо» от «решения приложения» по тексту
    в таком порядке невозможно: попытка это делать выбрасывала решение
    владельца, и процесс после сброса уходил на корень КОДА — в бандле на
    подписанную read-only папку (круг 5 по коду №327, DS C1).

    Дверь отзывает публикацию, а не маскирует значение: в переменной снова
    то, что было до неё, и процесс продолжает отвечать корнем приложения.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    от_приложения = (tmp_path / "от-приложения").resolve()
    monkeypatch.setenv("CHAROITE_ROOT", str(от_приложения))          # до старта
    charoite_paths.use_data_root(от_приложения)                      # точка входа повторила
    charoite_paths.forget_data_root()
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == от_приложения
    assert os.environ.get("CHAROITE_ROOT") == str(от_приложения)
    with pytest.raises(RuntimeError):                                # назвал приложение — не нам менять
        charoite_paths.use_data_root(tmp_path / "другой")
    assert charoite_paths.use_data_root(tmp_path / "другой", replace=True) \
        == (tmp_path / "другой").resolve()


@pytest.mark.корень_называет_тест
def test_чужая_переменная_без_публикации_остаётся_сильнее(tmp_path, monkeypatch):
    """Сбрасывать нечего — решение приложения не трогаем вовсе."""
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    от_приложения = tmp_path / "от-приложения"
    monkeypatch.setenv("CHAROITE_ROOT", str(от_приложения))
    charoite_paths.forget_data_root()
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == от_приложения.resolve()
    with pytest.raises(RuntimeError):
        charoite_paths.use_data_root(tmp_path / "другой")


@pytest.mark.корень_называет_тест
def test_переходы_пары_опубликовать_отозвать(tmp_path):
    """Таблица состояний двери целиком, а не по одной ветке.

    Мутатор такие переходы не порождает (он меняет операторы, а не удаляет
    присваивания), и на отсутствии `_published = None` весь набор оставался
    зелёным: повторная публикация переставала запоминать, что лежало в
    переменной, и второй отзыв уносил чужое значение — тот же класс «чужое
    пропало» (DS I4 круга 7).

    Заодно закреплён исход «переменную переписал третий»: дверь свою запись
    не возвращает, а процесс продолжает отвечать чужим значением — это
    выбор, а не побочный эффект (DS I3 круга 7).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths as cp
    аудио = str(ROOT / "src" / "audio.py")

    # 1. публикация на пустом окружении → отзыв возвращает пустоту
    cp.use_data_root(tmp_path / "п1")
    cp.forget_data_root()
    assert os.environ.get("CHAROITE_ROOT") is None

    # 2. третий положил своё → публикация → отзыв возвращает ЕГО значение
    os.environ["CHAROITE_ROOT"] = str(tmp_path / "третий")
    cp.use_data_root(tmp_path / "п2", replace=True)
    assert os.environ["CHAROITE_ROOT"] == str((tmp_path / "п2").resolve())
    cp.forget_data_root()
    assert os.environ["CHAROITE_ROOT"] == str(tmp_path / "третий")

    # 3. после отзыва процесс отвечает тем, что лежит в переменной
    assert cp.resolve_root(аудио) == (tmp_path / "третий").resolve()

    # 4. и назвать свой корень поверх чужого нельзя без replace
    with pytest.raises(RuntimeError):
        cp.use_data_root(tmp_path / "п3")


@pytest.mark.корень_называет_тест
def test_отзыв_не_трогает_чужую_запись_в_переменной(tmp_path):
    """Между публикацией и отзывом в переменную мог записать кто-то третий.

    Отзыв возвращает СВОЮ запись, а не откатывает переменную вслепую: иначе
    чужое значение исчезало бы вовсе (когда до публикации переменной не было)
    или подменялось прежним. Класс «чужая запись пропала» внесла бы сама
    правка отзыва (круг 6 по коду №327, DS I1).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    charoite_paths.use_data_root(tmp_path / "наш")
    os.environ["CHAROITE_ROOT"] = str(tmp_path / "от-третьего")   # чужая запись
    charoite_paths.forget_data_root()
    assert os.environ.get("CHAROITE_ROOT") == str(tmp_path / "от-третьего")


@pytest.mark.корень_называет_тест
def test_вход_без_явного_источника_получает_отказ_а_не_догадку(monkeypatch):
    """Точка входа обязана НАЗВАТЬ корень, а не переспросить канон.

    `use_data_root(resolve_root(__file__))` выглядел как называние, но был
    узакониванием догадки: ответ третьего пункта канона ложился в `_given` и
    публиковался в окружение — дети читали догадку как решение владельца, а
    `_given` сильнее переменной, поэтому никакой позднейший отказ до такого
    процесса уже не добирался (входной круг по №332, DS C1).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths

    monkeypatch.delenv("CHAROITE_ROOT", raising=False)
    charoite_paths.forget_data_root()
    with pytest.raises(charoite_paths.RootNotNamed) as отказ:
        charoite_paths.require_data_root(__file__)
    # в отказе — рецепт, а не только диагноз: читает его человек у терминала
    assert "CHAROITE_ROOT" in str(отказ.value) and "не назван" in str(отказ.value)


@pytest.mark.корень_называет_тест
def test_вход_берёт_корень_из_окружения_и_публикует_его(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths

    данные = tmp_path / "данные"
    данные.mkdir()
    charoite_paths.forget_data_root()
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))

    названный = charoite_paths.require_data_root(__file__)

    assert названный == данные.resolve()
    assert charoite_paths.resolve_root(__file__) == данные.resolve(), "корень не назван процессу"
    # повторный вызов входа в том же процессе отдаёт ТОТ ЖЕ корень, а не None:
    # мутант `return _given → return None` пережил прогон — этой строки не было
    # (мутатор на диапазоне ветки, 22.09)
    assert charoite_paths.require_data_root(__file__) == данные.resolve()


@pytest.mark.корень_называет_тест
def test_догадка_доступна_только_названной_вслух(tmp_path):
    """`guess_from_code=True` — то же самое, но видно в строке вызова.

    Ручной прогон из checkout остаётся возможным; отличие в том, что намерение
    угадать написано у вызывающего и попадает в отчёт гейта, а не прячется
    третьим ответом библиотеки. Мнимое дерево и отдельный процесс — см.
    `_установка_с_каноном`.
    """
    прогон, установка = _вход_в_установке(
        tmp_path, "print(charoite_paths.require_data_root(__file__, guess_from_code=True))\n",
        где="src")
    assert прогон.returncode == 0, прогон.stderr[-400:]
    assert прогон.stdout.strip() == str(установка)


def test_демон_называет_корень_и_отказ_виден_снаружи(tmp_path):
    """Точка входа проверяется КАК ПРОЦЕСС, а не чтением исходника.

    Без этого теста возврат `daemon.py` к прежнему `use_data_root(_root())`
    проходил бы молча: pytest зелёный, гейт раскладки зелёный (он смотрит
    места `__file__`, а не вызов называния). Дефект, ради которого сделана
    правка, возвращался одним Edit-ом без единого сигнала (круг 1 по коду
    №332, DS I2).

    Меряются оба канала отказа сразу: код выхода (его читают launchd и любой
    скрипт) и строка статуса с `error: True` (её рисует приложение). Первая
    редакция заявляла код 2, которого не было: `main()` стоял голым вызовом,
    и процесс выходил нулём (обе головы круга 1).
    """
    import json
    import subprocess

    окружение = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    окружение["PATH"] = os.environ.get("PATH", "")
    прогон = subprocess.run(
        [sys.executable, str(ROOT / "src" / "daemon.py")],
        cwd=ROOT, env=окружение, capture_output=True, text=True, timeout=120)

    sys.path.insert(0, str(ROOT / "src"))
    import exit_codes

    assert прогон.returncode == exit_codes.EXIT_ROOT_UNNAMED, (
        f"демон без корня обязан выйти кодом {exit_codes.EXIT_ROOT_UNNAMED}, "
        f"а вышел {прогон.returncode}: launchd с KeepAlive и `&&`-скрипт иначе видят успех")
    # ищем СВОЁ событие среди строк, а не берём первую: чужой баннер на stdout
    # (апгрейд зависимости, отладочная печать в цепочке импортов) красил бы
    # тест `JSONDecodeError` без единого дефекта демона (круг 2, GLM Minor 3)
    события = []
    for строка in прогон.stdout.splitlines():
        try:
            события.append(json.loads(строка))
        except json.JSONDecodeError:
            continue
    отказы = [e for e in события if e.get("type") == "status" and e.get("error") is True]
    assert отказы, (
        f"отказ не пришёл типом, который приложение рисует красным; "
        f"на stdout было: {прогон.stdout[:400]!r}")
    assert "CHAROITE_ROOT" in отказы[0]["text"], "в отказе нет рецепта"
    # причина — ЗНАЧЕНИЕМ: по ней приложение решает, что повтор бесполезен.
    # Без этой строки удаление `reason` из `emit_error` оставляло бы зелёными
    # и pytest, и swift test (круг 3 по коду №332, GLM I1).
    # Сверяем с НАБОРОМ ПРИЁМНИКА, а не с третьей копией литерала в тесте:
    # иначе переименование причины на одной стороне с правкой «своего» теста
    # оставляло оба набора зелёными, а в бою отказ уходил в неизвестные — три
    # перезапуска вместо рецепта (круг 5, DS I1; приём тот же, что в
    # test_toggle_status.py — тест провода читает оба конца)
    import re
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services"
             / "SuflerEndOfRecording.swift").read_text(encoding="utf-8")
    m = re.search(r"fatalReasons:\s*Set<String>\s*=\s*\[([^\]]*)\]", swift)
    assert m, "в SuflerEndOfRecording.swift нет набора fatalReasons — контракт провода потерян"
    known = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert отказы[0].get("reason") in known, (
        f"причина {отказы[0].get('reason')!r} неизвестна приложению ({sorted(known)}): "
        f"отказ уйдёт в три перезапуска вместо рецепта")


@pytest.mark.корень_называет_тест
def test_чужая_догадка_не_принимается_входом_за_решение_владельца(tmp_path):
    """«Корень уже есть в процессе» — не то же самое, что «корень назвали».

    Ранний возврат `_given`, добавленный кругом 1, отдавал корень, названный
    КЕМ УГОДНО: библиотекой, обвязкой, повторным вызовом. Провенанс нигде не
    хранился, и «точка входа обязана назвать корень» тихо превращалось в
    «в процессе уже есть корень» (круг 2 по коду №332, DS C2).
    """
    прогон, установка = _вход_в_установке(tmp_path, (
        # кто-то до входа вывел корень догадкой — ровно прежний дефект
        "charoite_paths.require_data_root(__file__, guess_from_code=True)\n"
        "try:\n"
        "    charoite_paths.require_data_root(__file__)\n"     # вход догадку не разрешал
        "except charoite_paths.RootNotNamed as отказ:\n"
        "    print('отказ', 'догадкой' in str(отказ))\n"
        "else:\n"
        "    print('без отказа')\n"
        # а вход, который догадку РАЗРЕШИЛ, её и получает — без отказа. Без этой
        # ветки мутант `and → or` в условии отказа выживал: оба теста были про
        # «отказ», ни один — про «не отказ» (мутатор на диапазоне ветки, 22.09)
        "print(charoite_paths.require_data_root(__file__, guess_from_code=True))\n"),
        где="src")
    assert прогон.returncode == 0, прогон.stderr[-400:]
    assert прогон.stdout.splitlines() == ["отказ True", str(установка)]


# --- дверь точки входа (№340) -----------------------------------------------
# Входам без своего канала к приложению отказ нужен кодом, а не исключением:
# приложение, launchd и проба приёмки читают код ПРОЦЕССА и строку с рецептом,
# а трейсбек `RootNotNamed` выходил бы кодом 1 — «упало что-то», а не «корень не
# назван». Поэтому дверь проверяется отдельным процессом: в тестовом процессе
# корень уже назвала обвязка, и отказа там не бывает по построению.

def _door(tmp_path, *, root: str | None, guess: bool = False) -> subprocess.CompletedProcess:
    прогон, _ = _вход_в_установке(
        tmp_path, f"print(charoite_paths.name_data_root_or_exit(__file__, guess_from_code={guess!r}))\n",
        root=root)
    return прогон


def test_the_entry_door_refuses_with_the_code_and_a_recipe(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    import exit_codes
    прогон = _door(tmp_path, root=None)
    assert прогон.returncode == exit_codes.EXIT_ROOT_UNNAMED, (прогон.returncode, прогон.stderr[-300:])
    assert "CHAROITE_ROOT" in прогон.stderr, "рецепт обязан назвать переменную"
    assert прогон.stderr.count("entry.py") == 1, (
        "имя входа — ровно один раз: рецепт конструктора его уже называет, "
        f"префикс поверх давал дубль (мутатор, №340): {прогон.stderr!r}")
    assert "Traceback" not in прогон.stderr, "отказ — строкой, а не трейсбеком"
    assert прогон.stdout == "", "без корня вход не должен успеть ничего сделать"


def test_the_entry_door_passes_a_named_root_through(tmp_path):
    данные = tmp_path / "данные"
    данные.mkdir()
    прогон = _door(tmp_path, root=str(данные))
    assert прогон.returncode == 0, прогон.stderr[-300:]
    assert прогон.stdout.strip() == str(данные.resolve())


def test_the_entry_door_names_a_guess_only_when_asked(tmp_path):
    """Догадка по положению файла — только выписанная в вызове, как у конструктора:
    без переменной и с guess_from_code=True вход работает на корне кода (корень
    мнимой установки, где лежит её канон), а не отказывает."""
    прогон = _door(tmp_path, root=None, guess=True)
    assert прогон.returncode == 0, прогон.stderr[-300:]
    assert прогон.stdout.strip() == str((tmp_path / "установка").resolve())
