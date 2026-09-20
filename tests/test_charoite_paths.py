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
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_без_переменной_корень_прежний(tmp_path):
    """Корень без переменной — папка НАД модулем, и это меряется на чужом дереве.

    Ожидание строит фикстура, а не та же формула `parent.parent`, что и
    проверяемый код: пока обе стороны считались одинаково, перенос модуля на
    ступень двигал их вместе и тест оставался зелёным на сломанном каноне
    (GLM C3 по №327).
    """
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import resolve_root
    корень = tmp_path / "установка"
    (корень / "src").mkdir(parents=True)
    fake = корень / "src" / "audio.py"
    fake.touch()
    assert resolve_root(str(fake)) == корень.resolve()


def test_переменная_переопределяет_корень(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import resolve_root
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    assert resolve_root(str(ROOT / "src" / "audio.py")) == tmp_path.resolve()


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
        "print(audio.ROOT); print(daemon.ROOT); print(meeting_archive.ROOT)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    for line in out.stdout.strip().splitlines():
        assert pathlib.Path(line) == tmp_path.resolve(), f"{line} мимо CHAROITE_ROOT"


def test_корень_кода_переменную_не_слушает(tmp_path, monkeypatch):
    """Данные переносятся, код — нет. `src/` лежит там, где лежит."""
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import code_root
    поставка = tmp_path / "поставка"
    (поставка / "src").mkdir(parents=True)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path / "данные"))
    assert code_root(str(поставка / "src" / "audio.py")) == поставка.resolve()


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


def test_ночные_скрипты_пишут_в_корень_данных(tmp_path):
    """Конфиг и отметки ночного цикла — у человека, а не в бандле.

    Оба места нашлись ревью 19.08 в одной ветке: конфиг графа вёл к
    config.yaml рядом с кодом (во вложенной установке его там нет — ночь
    читала бы дефолты и игнорировала выключатели профиля), а
    `tier3_cores.STAMPS` писал отметку прогона в read-only бандл — то есть
    падал бы PermissionError на первой же ночи. Проверяем в отдельном
    процессе: отметка ночи по-прежнему считается на импорте, а конфиг графа
    с №327 — на вызове, и оба обязаны лечь в корень ДАННЫХ.
    """
    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    code = (
        "import sys; sys.path.insert(0, 'src'); sys.path.insert(0, 'scripts')\n"
        "import graphs, tier3_cores\n"
        "print(graphs.config_path()); print(tier3_cores.STAMPS)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    config, stamps = out.stdout.strip().splitlines()
    assert pathlib.Path(config) == tmp_path.resolve() / "config" / "config.yaml"
    assert pathlib.Path(stamps).parent == tmp_path.resolve() / "logs"


# ---------------------------------------------------------------- №327: кто хозяин корня

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


def test_названный_корень_уезжает_детям_в_окружение(tmp_path):
    """Дети считают корень сами — и обязаны получить тот же ответ.

    Ночные скрипты, индексатор и облачный воркер запускаются отдельными
    процессами и строят корень по `CHAROITE_ROOT`. Назови корень только
    внутри процесса — ребёнок судил бы о живой встрече по чужому
    `logs/daemon.lock` (круг 1 по коду №327, DS I2).
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
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


def test_забыть_корень_не_оставляет_детей_без_корня(tmp_path):
    """Дверь сброса снимает названный корень, но не значение для детей.

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
    charoite_paths.forget_data_root()
    assert os.environ.get("CHAROITE_ROOT") == str(названный)   # дети не осиротели
    # но за ПРОЦЕСС своё эхо больше не отвечает: забыли — значит забыли
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) != названный
    другой = charoite_paths.use_data_root(tmp_path / "другой")  # и назвать можно заново
    assert другой == (tmp_path / "другой").resolve()


def test_чужая_переменная_после_сброса_остаётся_сильнее(tmp_path, monkeypatch):
    """Эхо канона сбрасывается, решение приложения — нет.

    Переменная служит двум делам: вход от приложения и канал для детей.
    Отличать одно от другого обязан канон — иначе либо сброс не работает
    (своё эхо отвечает за процесс), либо теряется решение владельца.
    """
    sys.path.insert(0, str(ROOT / "src"))
    import charoite_paths
    от_приложения = tmp_path / "от-приложения"
    monkeypatch.setenv("CHAROITE_ROOT", str(от_приложения))
    charoite_paths.forget_data_root()                           # сбрасывать нечего
    assert charoite_paths.resolve_root(str(ROOT / "src" / "audio.py")) == от_приложения.resolve()
    with pytest.raises(RuntimeError):
        charoite_paths.use_data_root(tmp_path / "другой")
