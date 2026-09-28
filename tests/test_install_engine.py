"""Установщик окружения движка (№474): что он проверяет, что копирует, как меняет каталог.

Сеть, pip и настоящий интерпретатор здесь подменены: установка целиком — прогон на
Mac с сетью (сверка в PR). Здесь держится то, что ломается тихо: какой лок читается,
что уходит в копию интерпретатора, что остаётся на диске при отказе посреди установки.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import install_engine as ie  # noqa: E402
import diarize_nemotron as nem  # noqa: E402
import foreign_python as fp  # noqa: E402


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
    (lambda m: m.setattr(ie.platform, "mac_ver", lambda: ("13.6", ("", "", ""), "arm64")), "14.0"),
    (lambda m: m.setattr(ie.platform, "mac_ver", lambda: ("", ("", "", ""), "")), "14.0"),
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
    return copy, ie.EngineSpec(
        lock=tmp_path / "x.lock", home=nem.engine_dir, python=nem.engine_python, weights=nem.model_dir,
        fetch_weights=lambda dest: events.append(("weights", dest)),
        probe=lambda *a, **k: pytest.fail("установка не зовёт пробу --probe"), diarize=diarize)


def test_install_copies_installs_fetches_probes_then_swaps(tmp_path, monkeypatch):
    events = []
    copy, spec = _spec(tmp_path, events)
    monkeypatch.setitem(ie.ENGINES, "nemotron", spec)
    monkeypatch.setattr(ie, "check_machine", lambda lock: events.append(("check", lock)))
    monkeypatch.setattr(ie, "copy_interpreter", copy)
    monkeypatch.setattr(ie, "pip_install", lambda python, lock: events.append(("pip", python.name, lock)))
    assert ie.install("nemotron", tmp_path) == 0
    assert events == [("check", spec.lock), "copy", ("pip", "python3", spec.lock),
                      ("weights", nem.model_dir(tmp_path)), ("probe", "python3", "probe.wav", tmp_path)]
    assert nem.engine_python(tmp_path).is_file()
    assert sorted(p.name for p in nem.engine_dir(tmp_path).iterdir()) == ["python"]   # проба не осталась
    assert sorted(p.name for p in nem.engine_dir(tmp_path).parent.iterdir()) == [".nemotron.install.lock", "nemotron"]


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


def test_a_second_install_is_refused_while_one_runs(tmp_path, monkeypatch):
    """Замок установки занят — отказ до любой работы: временный каталог соседки цел."""
    events = []
    _mocked_steps(tmp_path, monkeypatch, events)
    home = nem.engine_dir(tmp_path)
    home.parent.mkdir(parents=True)
    neighbour = home.with_name(".nemotron.new-7")
    neighbour.mkdir()
    with ie.one_install(home):
        with pytest.raises(ie.Refused, match="замок установки"):
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
