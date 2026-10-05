"""Установщик окружения движка (№474): что он проверяет, что копирует, как меняет каталог.

Сеть, pip и настоящий интерпретатор здесь подменены: установка целиком — прогон на
Mac с сетью (сверка в PR). Здесь держится то, что ломается тихо: какой лок читается,
что уходит в копию интерпретатора, что остаётся на диске при отказе посреди установки.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import textwrap
import wave

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import install_engine as ie  # noqa: E402
import diarize_nemotron as nem  # noqa: E402
import foreign_python as fp  # noqa: E402
import get_models  # noqa: E402


def _raise(exc):
    """Шаг, который падает заданным исключением — подмена install/check в main."""
    def step(*_a, **_k):
        raise exc
    return step


def _lock(tmp_path: pathlib.Path, head: str) -> pathlib.Path:
    p = tmp_path / "requirements-nemotron.lock"
    p.write_text(head + "mlx-audio==0.5.6 \\\n    --hash=sha256:00\n", encoding="utf-8")
    return p


def test_the_platform_comes_from_the_lock_that_is_installed():
    """Версия Python и min-macos — из шапки настоящего лока, а не из констант установщика."""
    assert ie.lock_platform(ie.ENGINES["nemotron"].lock) == ("3.12", "14.0")


@pytest.mark.parametrize("head", [
    "# min-macos: 14.0\n",                                                     # нет команды uv
    "#    uv pip compile x.in --generate-hashes --python-version 3.12 -o x.lock\n",  # нет min-macos
])
def test_a_lock_without_its_platform_is_refused(tmp_path, head):
    with pytest.raises(ie.Refused, match="min-macos"):
        ie.lock_platform(_lock(tmp_path, head))
    with pytest.raises(ie.Refused, match="lock_runtime_deps.py nemotron"):
        ie.lock_platform(tmp_path / "нет.lock")


@pytest.fixture
def machine(monkeypatch):
    """Годная машина: Apple Silicon, macOS 15, Python 3.12, python-build-standalone."""
    monkeypatch.setattr(ie.sys, "platform", "darwin")
    monkeypatch.setattr(ie.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(ie.platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64"))
    monkeypatch.setattr(ie.sys, "version_info", (3, 12, 13, "final", 0))
    monkeypatch.setattr(ie.sysconfig, "get_config_var", lambda name: "/install" if name == "prefix" else None)
    return monkeypatch


LOCK_HEAD = "# min-macos: 14.0\n#    uv pip compile x.in --generate-hashes --python-version 3.12 -o x.lock\n"


@pytest.mark.parametrize("mac", ["14.0", "14.0.1", "26.0"])
def test_a_fitting_machine_passes(machine, tmp_path, mac):
    """Граница включительно: macOS ровно той версии, что в шапке лока, годится."""
    machine.setattr(ie.platform, "mac_ver", lambda: (mac, ("", "", ""), "arm64"))
    assert ie.check_machine(_lock(tmp_path, LOCK_HEAD)) is None


@pytest.mark.parametrize("patch,why", [
    (lambda m: m.setattr(ie.platform, "machine", lambda: "x86_64"), "Apple Silicon"),
    (lambda m: m.setattr(ie.platform, "mac_ver", lambda: ("13.6", ("", "", ""), "arm64")),
     "macOS 13.6, а колёса mlx собраны начиная с 14.0"),
    (lambda m: m.setattr(ie.platform, "mac_ver", lambda: ("", ("", "", ""), "")), r"macOS \?, а колёса"),
    (lambda m: m.setattr(ie.sys, "version_info", (3, 13, 1, "final", 0)), "3.13"),
    (lambda m: m.setattr(ie.sysconfig, "get_config_var", lambda n: "/opt/homebrew/opt/python@3.12"),
     "python-build-standalone"),
])
def test_an_unfitting_machine_is_refused_with_the_reason(machine, tmp_path, patch, why):
    patch(machine)
    with pytest.raises(ie.Refused, match=why):
        ie.check_machine(_lock(tmp_path, LOCK_HEAD))


def test_the_copy_keeps_pip_and_drops_the_app_packages(tmp_path):
    site = tmp_path / "lib" / "python3.12" / "site-packages"
    ignore = ie.copy_ignore(site)
    names = ["pip", "pip-26.2.1.dist-info", "numpy", "numpy-2.5.2.dist-info", "_cffi_backend.so", "pipx"]
    assert ignore(str(site), names) == {"numpy", "numpy-2.5.2.dist-info", "_cffi_backend.so", "pipx"}
    assert ignore(str(tmp_path / "lib" / "python3.12"), ["site-packages", "os.py"]) == set()


def test_the_copy_is_a_real_tree_without_app_packages(tmp_path, monkeypatch):
    base = tmp_path / "base"
    site = base / "lib" / "python3.12" / "site-packages"
    (site / "pip").mkdir(parents=True)
    (site / "numpy").mkdir()
    (base / "bin").mkdir()
    (base / "bin" / "python3.12").write_text("#", encoding="utf-8")
    (base / "bin" / "python3").symlink_to("python3.12")
    monkeypatch.setattr(ie.sys, "base_prefix", str(base))
    python = ie.copy_interpreter(tmp_path / "copy")
    assert python == tmp_path / "copy" / "bin" / "python3" and python.is_symlink()
    assert sorted(p.name for p in (tmp_path / "copy" / "lib" / "python3.12" / "site-packages").iterdir()) == ["pip"]


def test_the_swap_replaces_the_old_env_and_leaves_nothing_behind(tmp_path):
    home = tmp_path / "engines" / "nemotron"
    home.mkdir(parents=True)
    (home / "old").write_text("", encoding="utf-8")
    new = tmp_path / "engines" / ".nemotron.new-1"
    new.mkdir()
    (new / "new").write_text("", encoding="utf-8")
    ie.swap_in(new, home)
    assert [p.name for p in home.iterdir()] == ["new"]
    assert sorted(p.name for p in home.parent.iterdir()) == ["nemotron"]


def test_a_failed_swap_brings_the_old_env_back(tmp_path, monkeypatch):
    home = tmp_path / "engines" / "nemotron"
    home.mkdir(parents=True)
    (home / "old").write_text("", encoding="utf-8")
    new = tmp_path / "engines" / ".nemotron.new-1"
    new.mkdir()
    real, calls = os.rename, []

    def rename(src, dst):
        calls.append(pathlib.Path(dst).name)
        if len(calls) == 2:
            raise OSError("диск")
        real(src, dst)
    monkeypatch.setattr(ie.os, "rename", rename)
    with pytest.raises(OSError):
        ie.swap_in(new, home)
    assert [p.name for p in home.iterdir()] == ["old"]


def _spec(tmp_path, events, *, probe_ok=True):
    """Движок с подменёнными шагами: копия — каталог с python3, pip и веса — отметки."""
    def copy(dest):
        (dest / "bin").mkdir(parents=True)
        (dest / "bin" / "python3").write_text("#", encoding="utf-8")
        events.append("copy")
        return dest / "bin" / "python3"

    def diarize(setting, wav, *, root, timeout):
        events.append(("probe", pathlib.Path(setting).name, wav.name, root))
        return fp.Outcome(fp.OK, payload=[]) if probe_ok else fp.Outcome(fp.FAILED, reason="упал")
    lock = _lock(tmp_path, "#    uv pip compile x.in --generate-hashes --python-version 3.12 -o x.lock\n"
                           "# min-macos: 14.0\n")
    return copy, ie.EngineSpec(
        lock=lock, home=nem.engine_dir, python=nem.engine_python, weights=nem.model_dir,
        fetch_weights=lambda dest: events.append(("weights", dest)),
        probe=lambda *a, **k: pytest.fail("установка не зовёт пробу --probe"), diarize=diarize)


def test_install_copies_installs_fetches_probes_then_swaps(tmp_path, monkeypatch, capsys):
    events = []
    copy, spec = _spec(tmp_path, events)
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    monkeypatch.setattr(ie, "check_machine", lambda lock: events.append(("check", lock)))
    monkeypatch.setattr(ie, "copy_interpreter", copy)
    monkeypatch.setattr(ie, "pip_install", lambda python, lock: events.append(("pip", python.name, lock)))
    monkeypatch.setattr(ie, "ensure_voice_set", lambda root: events.append(("voice", root)))
    assert ie.install("nemotron", tmp_path) == 0
    # порядок: набор голосов sherpa → окружение → веса → проба → замена
    assert events == [("check", spec.lock), ("voice", tmp_path), "copy",
                      ("pip", "python3", spec.lock),
                      ("weights", nem.model_dir(tmp_path)), ("probe", "python3", "probe.wav", tmp_path)]
    assert nem.engine_python(tmp_path).is_file()
    assert sorted(p.name for p in nem.engine_dir(tmp_path).iterdir()) == ["python"]   # проба не осталась
    assert sorted(p.name for p in nem.engine_dir(tmp_path).parent.iterdir()) == [".nemotron.install.lock", "nemotron"]
    assert "sufler.diarize_backend: nemotron" in capsys.readouterr().out, "финал называет шаг включения"


def test_a_failed_probe_keeps_the_old_env_and_cleans_the_staging(tmp_path, monkeypatch):
    home = nem.engine_dir(tmp_path)
    home.mkdir(parents=True)
    (home / "прежнее").write_text("", encoding="utf-8")
    events = []
    copy, spec = _spec(tmp_path, events, probe_ok=False)
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    monkeypatch.setattr(ie, "check_machine", lambda lock: None)
    monkeypatch.setattr(ie, "copy_interpreter", copy)
    monkeypatch.setattr(ie, "pip_install", lambda python, lock: None)
    monkeypatch.setattr(ie, "ensure_voice_set", lambda root: None)
    with pytest.raises(ie.Refused, match="проба не прошла"):
        ie.install("nemotron", tmp_path)
    assert [p.name for p in home.iterdir()] == ["прежнее"]
    assert sorted(p.name for p in home.parent.iterdir()) == [".nemotron.install.lock", "nemotron"]


def _mocked_steps(tmp_path, monkeypatch, events):
    copy, spec = _spec(tmp_path, events)
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    monkeypatch.setattr(ie, "check_machine", lambda lock: None)
    monkeypatch.setattr(ie, "copy_interpreter", copy)
    monkeypatch.setattr(ie, "pip_install", lambda python, lock: None)
    monkeypatch.setattr(ie, "ensure_voice_set", lambda root: None)


def test_leftovers_of_an_interrupted_install_are_swept(tmp_path, monkeypatch):
    """Закрытый терминал посреди pip оставляет `.new-*` (finally не выполняется на SIGHUP):
    следующая установка убирает его, как и `.old-*` при живом окружении."""
    home = nem.engine_dir(tmp_path)
    home.mkdir(parents=True)
    (home.parent / ".nemotron.new-99" / "python").mkdir(parents=True)
    (home.parent / ".nemotron.old-98").mkdir()
    _mocked_steps(tmp_path, monkeypatch, [])
    assert ie.install("nemotron", tmp_path) == 0
    assert sorted(p.name for p in home.parent.iterdir()) == [".nemotron.install.lock", "nemotron"]


def test_the_old_env_comes_back_when_the_swap_was_cut_between_renames(tmp_path):
    """Процесс умер между двумя переименованиями `swap_in`: каталога нет, прежнее — в `.old-*`."""
    home = nem.engine_dir(tmp_path)
    old = home.with_name(".nemotron.old-98")
    old.mkdir(parents=True)
    (old / "прежнее").write_text("", encoding="utf-8")
    ie.sweep(home)
    assert [p.name for p in home.iterdir()] == ["прежнее"] and not old.exists()


def test_a_second_install_is_busy_not_a_plain_refusal(tmp_path, monkeypatch):
    """Замок установки занят — «занято» до любой работы: временный каталог соседки цел,
    а исход отличим снаружи своим кодом (`EXIT_INSTALL_BUSY`)."""
    events = []
    _mocked_steps(tmp_path, monkeypatch, events)
    home = nem.engine_dir(tmp_path)
    home.parent.mkdir(parents=True)
    neighbour = home.with_name(".nemotron.new-7")
    neighbour.mkdir()
    with ie.one_install(home):
        with pytest.raises(ie.InstallBusy, match="другая установка"):
            ie.install("nemotron", tmp_path)
    assert events == [] and neighbour.is_dir()


def test_check_reports_without_the_network(tmp_path, monkeypatch, capsys):
    events = []
    _, spec = _spec(tmp_path, events)
    probe = []
    spec = ie.EngineSpec(**{**spec.__dict__, "probe": lambda setting, *, root: probe.append((setting, root)) or
                            fp.Outcome(fp.OK, payload={"mlx_audio": "0.5.6"})})
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    assert ie.check("nemotron", tmp_path) == 1
    assert "окружения движка нет" in capsys.readouterr().out and probe == []
    nem.engine_python(tmp_path).parent.mkdir(parents=True)
    nem.engine_python(tmp_path).write_text("#", encoding="utf-8")
    assert ie.check("nemotron", tmp_path) == 0
    assert "mlx-audio 0.5.6" in capsys.readouterr().out and probe == [("", tmp_path)]


def test_main_answers_help_before_any_work(capsys):
    with pytest.raises(SystemExit) as e:
        ie.main(["--help"])
    assert e.value.code == 0 and "--check" in capsys.readouterr().out


def test_the_engine_table_cannot_be_changed_in_place():
    """Таблица движков — общая на процесс: правка поля в одном вызове меняла бы лок или
    раскладку всем следующим."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        ie.ENGINES["nemotron"].lock = pathlib.Path("/чужой.lock")


def _fake_python(tmp_path: pathlib.Path, code: int) -> pathlib.Path:
    """«Интерпретатор», который пишет свои аргументы в args.txt, настройки pip, которые
    видит, — в env.txt и выходит с кодом `code`."""
    exe = tmp_path / "python3"
    exe.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/args.txt"\n'
                   f'printf "%s\\n" "$PIP_CONFIG_FILE" "$NETRC" "$VIRTUAL_ENV" "$PIP_PYTHON" > "{tmp_path}/env.txt"\n'
                   f'exit {code}\n',
                   encoding="utf-8")
    exe.chmod(0o755)
    return exe


def test_pip_installs_exactly_the_lock_with_hashes(tmp_path, monkeypatch):
    lock = tmp_path / "x.lock"
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "чужой-venv"))
    monkeypatch.setenv("PIP_PYTHON", str(tmp_path / "чужой-python"))
    assert ie.pip_install(_fake_python(tmp_path, 0), lock) is None
    args = (tmp_path / "args.txt").read_text(encoding="utf-8").split()
    assert args[:3] == ["-I", "-m", "pip"] and args[-2:] == ["-r", str(lock)]
    assert {"install", "--isolated", "--require-hashes", "--no-deps"} <= set(args)
    # через дверь pip (№484): ни файлов настроек pip, ни ~/.netrc, ни venv и PIP_PYTHON родителя
    assert (tmp_path / "env.txt").read_text(encoding="utf-8").splitlines() == [os.devnull, os.devnull, "", ""]


def test_a_failed_pip_is_refused_with_its_code(tmp_path):
    with pytest.raises(ie.Refused, match="код 3") as refused:
        ie.pip_install(_fake_python(tmp_path, 3), tmp_path / "x.lock")
    assert "не читаются намеренно" in str(refused.value)   # причина: настройки pip человека выключены нарочно


@pytest.mark.parametrize("seconds,duration", [(None, 1.0), (0.5, 0.5)])
def test_the_probe_is_silence_of_the_asked_length(tmp_path, seconds, duration):
    """Проба — тишина формата пересборки: моно, 16 бит, частота движка; по умолчанию секунда."""
    path = ie.silence(tmp_path / "p.wav") if seconds is None else ie.silence(tmp_path / "p.wav", seconds)
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, nem.SAMPLE_RATE)
        assert w.getnframes() == round(nem.SAMPLE_RATE * duration)


def _stubborn_rmtree(monkeypatch):
    """rmtree, который не может удалить: без ignore_errors — ошибка, с ним — тихо."""
    def rmtree(path, ignore_errors=False, **kw):
        if not ignore_errors:
            raise OSError(f"занят: {path}")
    monkeypatch.setattr(ie.shutil, "rmtree", rmtree)


def test_an_old_env_that_cannot_be_removed_does_not_undo_the_swap(tmp_path, monkeypatch):
    """Уборка прежнего окружения — после замены: её сбой не повод объявлять установку упавшей."""
    home = tmp_path / "engines" / "nemotron"
    home.mkdir(parents=True)
    new = tmp_path / "engines" / ".nemotron.new-1"
    (new / "new").mkdir(parents=True)
    _stubborn_rmtree(monkeypatch)
    ie.swap_in(new, home)
    assert [p.name for p in home.iterdir()] == ["new"]


def test_a_leftover_that_cannot_be_removed_does_not_stop_the_install(tmp_path, monkeypatch):
    home = nem.engine_dir(tmp_path)
    home.mkdir(parents=True)
    (home.parent / ".nemotron.new-99").mkdir()
    (home.parent / ".nemotron.old-98").mkdir()
    _stubborn_rmtree(monkeypatch)
    ie.sweep(home)
    assert home.is_dir()


def test_install_creates_a_data_root_that_is_not_there_yet(tmp_path, monkeypatch):
    root = tmp_path / "новый корень"
    _mocked_steps(tmp_path, monkeypatch, [])
    assert ie.install("nemotron", root) == 0 and nem.engine_python(root).is_file()


def test_check_says_when_the_env_is_there_but_the_engine_cannot_work(tmp_path, monkeypatch, capsys):
    events = []
    _, spec = _spec(tmp_path, events)
    spec = ie.EngineSpec(**{**spec.__dict__, "probe": lambda setting, *, root:
                            fp.Outcome(fp.UNAVAILABLE, reason="нет каталога весов")})
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    nem.engine_python(tmp_path).parent.mkdir(parents=True)
    nem.engine_python(tmp_path).write_text("#", encoding="utf-8")
    assert ie.check("nemotron", tmp_path) == 1
    assert "движку нечем работать (unavailable): нет каталога весов" in capsys.readouterr().out


@pytest.mark.parametrize("argv,called", [(["nemotron"], "install"), (["nemotron", "--check"], "check")])
def test_main_returns_the_code_of_the_step_it_ran(tmp_path, monkeypatch, argv, called):
    monkeypatch.setattr(ie, "harden_umask", lambda: None)
    monkeypatch.setattr(ie, "name_data_root_or_exit", lambda _file: tmp_path)
    monkeypatch.setattr(ie, "install", lambda name, root: 7 if (name, root) == ("nemotron", tmp_path) else 0)
    monkeypatch.setattr(ie, "check", lambda name, root: 5 if (name, root) == ("nemotron", tmp_path) else 0)
    assert ie.main(argv) == {"install": 7, "check": 5}[called]


def test_main_turns_a_refusal_into_code_one_and_a_reason(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ie, "harden_umask", lambda: None)
    monkeypatch.setattr(ie, "name_data_root_or_exit", lambda _file: tmp_path)

    def refuse(name, root):
        raise ie.Refused("машина не та")
    monkeypatch.setattr(ie, "install", refuse)
    assert ie.main(["nemotron"]) == 1
    assert "не поставлено: машина не та" in capsys.readouterr().err


def test_the_network_lines_reach_a_pipe_before_pip_writes(tmp_path):
    """В канале (`| tee`, лог) строки «сеть: …» доходят до читателя раньше вывода pip, который
    пишет в тот же дескриптор мимо буфера Python. Без строчной буферизации stdout они приходили
    после всего вывода pip (выкатка №474, 29.09; №481)."""
    driver = textwrap.dedent(f"""
        import os, pathlib, sys
        sys.path[:0] = [{str(ROOT / "scripts")!r}, {str(ROOT / "src")!r}]
        import foreign_python as fp
        import install_engine as ie

        def copy(dest):
            (dest / "bin").mkdir(parents=True)
            (dest / "bin" / "python3").write_text("#", encoding="utf-8")
            return dest / "bin" / "python3"

        ie.check_machine = lambda lock: None
        ie.copy_interpreter = copy
        ie.ensure_voice_set = lambda root: None
        ie.pip_install = lambda python, lock: os.write(1, b"PIP\\n")   # как pip: мимо буфера Python
        spec = ie.ENGINES["nemotron"]
        ie.ENGINES["nemotron"] = ie.EngineSpec(**{{**spec.__dict__, "fetch_weights": lambda dest: None,
                                                   "diarize": lambda *a, **k: fp.Outcome(fp.OK, payload=[])}})
        ie.name_data_root_or_exit = lambda _file: pathlib.Path({str(tmp_path)!r})
        sys.exit(ie.main(["nemotron"]))
    """)
    # Окружение задаёт тест, а не раннер: белый список, а не «всё, кроме PYTHONUNBUFFERED». Доли мутатора
    # ставят PYTHONUNBUFFERED=1 (mutate_check), и с ним тест не отличал правку от её отсутствия; любая
    # другая переменная раннера, меняющая вывод, так же не просочится (круг 2 по коду №481).
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    out = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines.index("сеть:") < lines.index("PIP"), lines


def test_check_reads_the_lock_like_install(tmp_path, monkeypatch, capsys):
    """`--check` читает лок тем же `lock_platform`, что установка: бандл без лока
    отвечал «окружения нет — поставьте», а установка по этой команде падала на
    «нет лока» (входной круг 1 по №489, C3)."""
    events = []
    _, spec = _spec(tmp_path, events)
    spec = ie.EngineSpec(**{**spec.__dict__, "lock": tmp_path / "нет.lock"})
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    with pytest.raises(ie.Refused, match="нет лока"):
        ie.check("nemotron", tmp_path)


def _staged_bundle(tmp_path: pathlib.Path) -> pathlib.Path:
    """Код, разложенный тем же `app/stage_code.sh`, что зовёт сборка, — в дерево формы бандла."""
    code = tmp_path / "Мой Mac" / "Charoite.app" / "Contents" / "Resources" / "charoite"
    subprocess.run(["bash", str(ROOT / "app" / "stage_code.sh"), str(code)], check=True, timeout=120)
    return code


def _listing(tree: pathlib.Path) -> set[str]:
    return {str(p.relative_to(tree)) for p in tree.rglob("*")}


def _check_from(code: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(code / "scripts" / "install_engine.py"), "nemotron", "--check"],
                          capture_output=True, text=True, timeout=120, env=env)


def test_the_bundle_code_installs_nothing_into_the_bundle(tmp_path):
    """Установщик из кода бандла (№489): без корня и с корнем внутри `.app` — отказ двери
    кодом `EXIT_ROOT_UNNAMED` с рецептом, и в бандле не появляется ничего; с корнем снаружи
    `--check` проходит лок (он теперь едет в бандле) и доходит до «окружения нет»."""
    sys.path.insert(0, str(ROOT / "src"))
    import exit_codes
    code = _staged_bundle(tmp_path)
    app = code.parents[2]
    before = _listing(app)
    # Байткод не выключается окружением теста: приложение в /Applications принадлежит
    # человеку, и `__pycache__` из запуска в Терминале ложится в бандл — след считается
    # весь (предрелизный прогон 0.88.1, Opus C1: прежний тест прятал ровно это).
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "дом")}
    for root in (None, str(code), str(app)):
        out = _check_from(code, env if root is None else {**env, "CHAROITE_ROOT": root})
        assert out.returncode == exit_codes.EXIT_ROOT_UNNAMED, (root, out.returncode, out.stderr[-300:])
        assert "CHAROITE_ROOT" in out.stderr and "Traceback" not in out.stderr, out.stderr[-300:]
    assert _listing(app) == before, "отказ оставил след внутри бандла"
    data = tmp_path / "Application Support" / "Charoite"
    data.mkdir(parents=True)
    out = _check_from(code, {**env, "CHAROITE_ROOT": str(data)})
    assert out.returncode == 1 and "окружения движка нет" in out.stdout, (out.stdout, out.stderr[-300:])
    assert f"CHAROITE_ROOT={shlex.quote(str(data.resolve()))}" in out.stdout
    assert _listing(app) == before


def test_the_staged_bundle_carries_the_lock_the_installer_reads(tmp_path):
    """Дифференциал к тесту выше: тот же `--check` из того же дерева без лока отказывает
    «нет лока» — значит, зелёный прогон выше держится именно на локе в бандле."""
    code = _staged_bundle(tmp_path)
    (code / "requirements-nemotron.lock").unlink()
    data = tmp_path / "данные"
    data.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "дом"), "CHAROITE_ROOT": str(data)}
    out = _check_from(code, env)
    assert out.returncode == 1 and "нет лока" in out.stderr, (out.stdout, out.stderr[-300:])


def test_the_doctor_from_the_bundle_writes_no_bytecode_into_it(tmp_path):
    """Доктор python бандла из Терминала — без PYTHONPYCACHEPREFIX приложения: его
    импорты не оставляют `__pycache__` в `.app` (Opus C1, 0.88.1)."""
    code = _staged_bundle(tmp_path)
    app = code.parents[2]
    before = _listing(app)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "дом"),
           "CHAROITE_ROOT": str(tmp_path / "данные")}
    (tmp_path / "данные").mkdir()
    out = subprocess.run([sys.executable, str(code / "scripts" / "doctor.py"), "--help"],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr[-300:]
    assert _listing(app) == before, sorted(_listing(app) - before)


# ---- Исход установщика: занято, отмена, отказ (№622 B2) ----------------------


def _main_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ie, "harden_umask", lambda: None)
    monkeypatch.setattr(ie, "enter_process_group", lambda env=None: False)
    monkeypatch.setattr(ie, "name_data_root_or_exit", lambda _file: tmp_path)


def test_the_cancel_handler_raises_a_base_exception():
    """Отмена — `BaseException`, не `SystemExit`: `get_models` ловит SystemExit вокруг
    загрузки набора, и отмена на этом шаге прочиталась бы ошибкой загрузки (код 1)."""
    with pytest.raises(ie.InstallCancelled):
        ie._cancel(signal.SIGTERM, None)
    assert issubclass(ie.InstallCancelled, BaseException)
    assert not issubclass(ie.InstallCancelled, Exception)


def test_main_translates_cancellation_into_its_code(tmp_path, monkeypatch, capsys):
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "install", _raise(ie.InstallCancelled("получен сигнал 15")))
    assert ie.main(["nemotron"]) == ie.EXIT_INSTALL_CANCELLED
    assert "отменено: получен сигнал 15" in capsys.readouterr().err


def test_main_translates_a_keyboard_interrupt_into_cancellation(tmp_path, monkeypatch, capsys):
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "install", _raise(KeyboardInterrupt()))
    assert ie.main(["nemotron"]) == ie.EXIT_INSTALL_CANCELLED
    assert "отменено:" in capsys.readouterr().err


def test_a_signal_during_the_voice_set_cancels_with_its_code(tmp_path, monkeypatch, capsys):
    """Отмена на шаге набора голосов: обработчик бросает `InstallCancelled` мимо
    `except SystemExit` загрузчика, и `main` отдаёт код отмены, а не 1."""
    _mocked_steps(tmp_path, monkeypatch, [])
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "ensure_voice_set", lambda root: signal.raise_signal(signal.SIGTERM))
    assert ie.main(["nemotron"]) == ie.EXIT_INSTALL_CANCELLED
    assert "отменено:" in capsys.readouterr().err


def test_install_does_not_start_while_the_machine_is_busy(tmp_path, monkeypatch):
    """Непустой `busy_now` — «занято» до любой работы; каталог установки даже не создаётся."""
    monkeypatch.setattr(ie, "busy_now", lambda root: ["живая запись"])
    with pytest.raises(ie.InstallBusy, match="живая запись"):
        ie.install("nemotron", tmp_path)
    assert not nem.engine_dir(tmp_path).exists()


def test_main_translates_busy_into_its_code(tmp_path, monkeypatch, capsys):
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "install", _raise(ie.InstallBusy("идёт разбор встречи")))
    assert ie.main(["nemotron"]) == ie.EXIT_INSTALL_BUSY
    assert "занято: идёт разбор встречи" in capsys.readouterr().err


def test_a_volume_without_flock_is_a_refusal_with_code_one(tmp_path, monkeypatch):
    """Том без flock — не «занято»: отказ со своим текстом и кодом 1."""
    monkeypatch.setattr(ie.file_locks, "acquire_outcome", lambda f, *a, **k: ie.file_locks.NO_FLOCK)
    home = nem.engine_dir(tmp_path)
    home.parent.mkdir(parents=True)
    with pytest.raises(ie.Refused, match="flock"):
        with ie.one_install(home):
            pass


def test_a_get_models_refusal_becomes_code_one(tmp_path, monkeypatch, capsys):
    """`SystemExit` с текстом из `get_models.download` — «не поставлено» кодом 1."""
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "install", _raise(SystemExit("не скачалось: сеть")))
    assert ie.main(["nemotron"]) == 1
    assert "не поставлено: не скачалось: сеть" in capsys.readouterr().err


def test_the_voice_set_is_checked_and_fetched_in_process(tmp_path, monkeypatch):
    """Набор голосов sherpa ставится функциями `get_models` в этом же процессе: годное
    не трогаем, недостающее качаем."""
    calls = []
    monkeypatch.setattr(ie.get_models, "check",
                        lambda dest, min_bytes: None if dest.name == "embedding.onnx" else "нет")
    monkeypatch.setattr(ie.get_models, "download",
                        lambda url, dest, size, sha256="": calls.append((dest.name, url)))
    ie.ensure_voice_set(tmp_path)
    assert calls == [("segmentation.onnx", get_models.SEGMENTATION[get_models.SEG_DEFAULT].url)]


def test_the_license_is_printed_in_the_network_block(tmp_path, monkeypatch, capsys):
    """Лицензия весов — строка в блоке сети, до соединения (и до вывода pip)."""
    _mocked_steps(tmp_path, monkeypatch, [])
    ie.install("nemotron", tmp_path)
    out = capsys.readouterr().out
    assert get_models.NEMOTRON_LICENSE[0] in out and get_models.NEMOTRON_LICENSE[1] in out
    assert out.index("сеть:") < out.index(get_models.NEMOTRON_LICENSE[0])


# ---- Группа процессов (№622 B2) ---------------------------------------------


def test_the_process_group_changes_only_on_the_app_signal(monkeypatch):
    calls = []
    monkeypatch.setattr(ie.os, "setpgid", lambda *a: calls.append(a))
    assert ie.enter_process_group({}) is False
    assert calls == [], "группа сменилась без признака от приложения — Ctrl-C не дойдёт"
    assert ie.enter_process_group({ie.NEW_PGROUP_ENV: "1"}) is True
    assert calls == [(0, 0)]


# ---- --plan: одна строка JSON без сети (№622 B2) -----------------------------


def test_plan_is_one_json_line_even_when_the_machine_is_refused(tmp_path, monkeypatch, capsys):
    _main_root(tmp_path, monkeypatch)
    monkeypatch.setattr(ie, "check_machine", _raise(ie.Refused("движок только на Apple Silicon")))
    assert ie.main(["nemotron", "--plan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, (lines, "отказ машины обязан не ломать JSON")
    payload = json.loads(lines[0])
    assert payload["machine"] == {"ok": False, "reason": "движок только на Apple Silicon"}
    assert len(payload["network"]) == 2
    assert payload["sizes_mb"]["environment"] > 0 and payload["sizes_mb"]["weights"] > 0
    assert payload["sizes_mb"]["voice_set"] > 0
    assert payload["license"]["name"] and payload["license"]["url"].startswith("https://")
    assert payload["engine"]["backend"] in ("sherpa", "nemotron")


# ---- Откат замены на любом исключении (№622 B2) ------------------------------


def test_a_signal_between_the_renames_brings_the_old_env_back(tmp_path, monkeypatch):
    """Откат — на любом исключении, а не только OSError: Ctrl-C между двумя
    переименованиями иначе оставил бы окружение в `.old`, а `home` пустым."""
    home = tmp_path / "engines" / "nemotron"
    home.mkdir(parents=True)
    (home / "old").write_text("", encoding="utf-8")
    new = tmp_path / "engines" / ".nemotron.new-1"
    new.mkdir()
    (new / "new").write_text("", encoding="utf-8")
    real, calls = os.rename, []

    def rename(src, dst):
        calls.append(pathlib.Path(dst).name)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        real(src, dst)
    monkeypatch.setattr(ie.os, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        ie.swap_in(new, home)
    assert [p.name for p in home.iterdir()] == ["old"]
