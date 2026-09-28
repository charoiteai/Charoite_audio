"""Lock-файл встроенного контура не должен отставать от pyproject.

Сборка бандла ставит зависимости из `requirements-runtime.lock` с
`--require-hashes`: в подписанное приложение, которое уезжает всем
пользователям, попадает ровно то, что зафиксировано в репозитории, а не
то, что лежало на PyPI в минуту сборки (аудит 16.08).

У такой схемы одна цена: добавили пакет в `pyproject.toml`, забыли
пересобрать lock — сборка падает с невнятным «no hash». Эти тесты
превращают её в понятное «пересоберите lock», и до того, как упадёт CI.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-runtime.lock"
INPUT = ROOT / "requirements-runtime.in"


def _lock_names() -> set[str]:
    """Имена пакетов верхнего уровня из lock (без транзитивных отметок)."""
    from lock_runtime_deps import dist_name

    return {dist_name(m.group(1)) for line in LOCK.read_text(encoding="utf-8").splitlines()
            if (m := re.match(r"^([A-Za-z0-9][\w.\-]*)==", line))}


def _dep_names(specs) -> set[str]:
    """Проекция имени — одна на проект, `lock_runtime_deps.dist_name` (№446)."""
    from lock_runtime_deps import dist_name

    return {dist_name(s) for s in specs}


def test_lock_существует_и_с_хешами():
    assert LOCK.exists(), (
        "нет requirements-runtime.lock — соберите: "
        ".venv/bin/python scripts/lock_runtime_deps.py")
    text = LOCK.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text, "lock без хешей не защищает ни от чего"
    pins = re.findall(r"^([A-Za-z0-9][\w.\-]*)==", text, re.M)
    assert len(pins) > 10, "в lock подозрительно мало пакетов"


def test_каждый_пакет_в_lock_имеет_хеш():
    """Пакет без хеша `pip --require-hashes` не примет — сборка встанет."""
    lines = LOCK.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"^[A-Za-z0-9][\w.\-]*==", line):
            continue
        tail = " ".join(lines[i:i + 40])
        pkg = line.split("==")[0]
        assert "--hash=sha256:" in tail, f"{pkg} в lock без хеша"


def test_lock_не_отстал_от_pyproject():
    """Новая зависимость в манифесте обязана попасть и в lock."""
    from lock_runtime_deps import runtime_deps

    missing = _dep_names(runtime_deps()) - _lock_names()
    assert not missing, (
        f"нет в lock: {sorted(missing)} — пересоберите: "
        ".venv/bin/python scripts/lock_runtime_deps.py")


def test_вход_lock_совпадает_с_манифестом():
    """`requirements-runtime.in` генерируется — руками его не правят."""
    from lock_runtime_deps import runtime_deps

    if not INPUT.exists():
        return  # .in не обязателен в дереве: его пересоздаёт генератор
    listed = {ln.strip() for ln in INPUT.read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")}
    assert _dep_names(listed) == _dep_names(runtime_deps()), (
        "requirements-runtime.in разъехался с pyproject.toml")


def test_сборка_бандла_требует_хеши():
    """Сторож: возврат к установке из диапазонов не должен пройти тихо."""
    script = (ROOT / "scripts/build_embedded_python.sh").read_text(encoding="utf-8")
    assert "--require-hashes" in script, (
        "сборка снова ставит пакеты без проверки хешей")
    assert "requirements-runtime.lock" in script
    assert "/tmp/charoite-runtime-deps.txt" not in script, (
        "вернулся предсказуемый путь в общем /tmp")


def test_релизные_workflow_пиннуты_по_sha():
    """Два workflow с contents: write — самая дорогая цель для угона тега.

    Угон мутабельного тега action (прецедент tj-actions/changed-files,
    март 2025) в workflow с правом записи отдаёт релизные ассеты, то есть
    код всем пользователям апдейтера.
    """
    for name in ("release-app.yml", "release-please.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"uses:\s*([\w.\-]+/[\w.\-/]+)@(\S+)", text):
            action, ref = m.group(1), m.group(2)
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{name}: {action}@{ref} — нужен SHA-пин, а не тег")


def test_сборка_не_докачивает_pip_мимо_lock():
    """`pip install --upgrade pip` перед установкой из lock тянул свежий pip
    с PyPI без пина и без хешей — прямо в подписанный бандл, в обход той
    самой гарантии «ровно пиннутые артефакты» (второе мнение по #325,
    16.08). Комментарии не считаются — только команды."""
    script = (ROOT / "scripts/build_embedded_python.sh").read_text(encoding="utf-8")
    commands = [line for line in script.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    assert not [line for line in commands if "--upgrade pip" in line], (
        "сборка снова обновляет pip мимо lock-файла")


def test_проекция_имени_требования_по_pep_508_и_503():
    from lock_runtime_deps import dist_name

    for spec, name in [("PyYAML>=6", "pyyaml"), ("zope.interface", "zope-interface"),
                       ('a_b[c]; python_version<"3.12"', "a-b"), ("  Foo__Bar.baz==1", "foo-bar-baz"),
                       ("x", "x")]:
        assert dist_name(spec) == name, spec
    for bad in ("", "  ", ">=1", "-x"):
        try:
            dist_name(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} принят как требование")


def test_вход_lock_это_проекция_объявления_строка_в_строку():
    """`runtime_deps` — проекция `declared_deps` (№446): вход lock, записанный
    генератором, совпадает с ней строка в строку, а сырое объявление хранит
    маркеры и пресеты, которые проекция снимает."""
    from lock_runtime_deps import SKIP, declared_deps, runtime_deps

    if INPUT.exists():
        listed = [ln.strip() for ln in INPUT.read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
        assert runtime_deps() == listed
    raw = declared_deps()
    assert any(";" in d for d in raw) and any(d.startswith(SKIP) for d in raw), raw
    assert all(";" not in d for d in runtime_deps())
