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


# --- цели лока (№474): бандл по умолчанию, движок Nemotron второй целью ---

NEM_LOCK = ROOT / "requirements-nemotron.lock"
NEM_INPUT = ROOT / "requirements-nemotron.in"


def test_without_arguments_the_bundle_lock_is_rebuilt_and_nothing_else(monkeypatch):
    """Поведение до таблицы целей: без аргументов — лок бандла (сборка и CI зовут так)."""
    import lock_runtime_deps as lrd
    calls = []
    monkeypatch.setattr(lrd.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setattr(lrd, "compile_lock", lambda target, uv: calls.append((target, uv)) or 0)
    assert lrd.main([]) == 0
    assert calls == [(lrd.TARGETS["runtime"], "/bin/uv")]
    assert (lrd.TARGETS["runtime"].input, lrd.TARGETS["runtime"].lock) == (INPUT, LOCK)
    assert lrd.main(["nemotron"]) == 0
    assert calls[-1] == (lrd.TARGETS["nemotron"], "/bin/uv")


def test_one_compile_command_for_every_target_with_the_platform_floor_only_where_named(monkeypatch, tmp_path):
    """Команда uv одна на все цели; минимальная macOS уходит в окружение uv и в шапку лока
    только у цели, которая её назвала (бандл — умолчание uv, как было)."""
    import lock_runtime_deps as lrd
    seen = []

    def run(cmd, cwd, env):
        seen.append((cmd, env.get("MACOSX_DEPLOYMENT_TARGET")))
        (tmp_path / cmd[cmd.index("-o") + 1]).write_text("# uv\na==1 \\\n    --hash=sha256:00\n", encoding="utf-8")
        return lrd.subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(lrd.subprocess, "run", run)
    monkeypatch.setenv("MACOSX_DEPLOYMENT_TARGET", "")
    for name, floor in (("engine", "14.0"), ("bundle", "")):
        target = lrd.Target(tmp_path / f"{name}.in", tmp_path / f"{name}.lock", lambda: ["a==1"],
                            source="# источник\n", why="# зачем\n", min_macos=floor)
        assert lrd.compile_lock(target, "/bin/uv") == 0
        assert target.input.read_text(encoding="utf-8") == "# источник\na==1\n"
        head = target.lock.read_text(encoding="utf-8").splitlines()[:3]
        assert head[1] == "# зачем" and (head[2] == f"# min-macos: {floor}") == bool(floor)
    assert [c for c, _ in seen] == [
        ["/bin/uv", "pip", "compile", f"{name}.in", "--generate-hashes", "--python-version",
         lrd.PYTHON_VERSION, "--python-platform", "macos", "-o", f"{name}.lock"]
        for name in ("engine", "bundle")]
    assert [floor for _, floor in seen] == ["14.0", ""]


def test_the_engine_lock_pins_the_version_the_engine_module_names():
    """Версию стыка знает модуль движка; вход и лок движка от неё не отстают."""
    import lock_runtime_deps as lrd
    import diarize_nemotron
    assert lrd.nemotron_deps() == [f"mlx-audio=={diarize_nemotron.MLX_AUDIO_VERSION}"]
    listed = [ln.strip() for ln in NEM_INPUT.read_text(encoding="utf-8").splitlines()
              if ln.strip() and not ln.startswith("#")]
    assert listed == lrd.nemotron_deps(), "requirements-nemotron.in разъехался с MLX_AUDIO_VERSION"
    text = NEM_LOCK.read_text(encoding="utf-8")
    assert re.search(rf"^mlx-audio=={re.escape(diarize_nemotron.MLX_AUDIO_VERSION)} \\", text, re.M), (
        "лок движка отстал — пересоберите: .venv/bin/python scripts/lock_runtime_deps.py nemotron")


def test_the_engine_lock_names_its_platform_and_hashes_every_package():
    """Шапку читает установщик (Python и min-macos) — из того, что ставит."""
    import lock_runtime_deps as lrd
    text = NEM_LOCK.read_text(encoding="utf-8")
    assert f"# min-macos: {lrd.TARGETS['nemotron'].min_macos}" in text.splitlines()[:8]
    assert f"--python-version {lrd.PYTHON_VERSION}" in text[:2000]
    packages = [ln for ln in text.splitlines() if re.match(r"^[A-Za-z0-9]", ln)]
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    assert packages and all("--hash=sha256:" in b for b in blocks if re.match(r"^[A-Za-z0-9]", b))
