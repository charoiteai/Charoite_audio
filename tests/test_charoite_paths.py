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

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_без_переменной_корень_прежний():
    sys.path.insert(0, str(ROOT / "src"))
    from charoite_paths import resolve_root
    fake = ROOT / "src" / "audio.py"
    assert resolve_root(str(fake)) == ROOT


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
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    assert code_root(str(ROOT / "src" / "audio.py")) == ROOT


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
