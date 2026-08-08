"""Рабочий корень: код в поставке, данные у человека.

С вложенным в приложение python-контуром код переехал внутрь подписанного
бандла — писать туда записи и стенограммы нельзя. CHAROITE_ROOT разводит
код и данные; запуск из репозитория обязан ничего не заметить.
"""
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
