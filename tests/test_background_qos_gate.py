"""Гейт фонового режима процесса в обвязке (№817).

Полный набор под `taskpolicy -c background` шёл 24–41 мин против 3 и давал 4–12
ложных падений таймаутами: потолки времени рассчитаны на обычные ядра. Обвязка
отказывает такому прогону до сборки — кодом `EXIT_BACKGROUND_QOS` и причиной.
Проба — настоящий `taskpolicy` над отдельным pytest-процессом с обвязкой:
подставной датчик проверил бы только ветку, а не то, что зажим виден из
`pytest_configure` контроллера.
"""
import os
import pathlib
import sys
import tomllib
import types

import pytest

import conftest
import exit_codes
import machine_power

TASKPOLICY = "/usr/sbin/taskpolicy"
ПРОБА = """
    def test_ok():
        pass
"""

есть_taskpolicy = pytest.mark.skipif(
    sys.platform != "darwin" or not os.access(TASKPOLICY, os.X_OK),
    reason="зажим процесса ставит только taskpolicy macOS")


@есть_taskpolicy
@pytest.mark.parametrize("режим", [("-c", "background"), ("-c", "maintenance"), ("-b",)],
                         ids=["background", "maintenance", "darwin-фон"])
def test_прогон_под_зажимом_отказывает_с_причиной(внутренний_pytest, режим):
    result = внутренний_pytest(ПРОБА, обвязка=True, префикс=(TASKPOLICY, *режим))
    out = result.stdout + result.stderr
    assert result.returncode == exit_codes.EXIT_BACKGROUND_QOS, out
    assert "фоновом режиме процесса" in out and "без taskpolicy" in out, out
    assert "passed" not in out, out          # набор не начинался


@есть_taskpolicy
def test_utility_не_зажим(внутренний_pytest):
    """`-c utility` — замер 28.10: скорость обычная; отказ здесь был бы ложным."""
    result = внутренний_pytest(ПРОБА, обвязка=True, префикс=(TASKPOLICY, "-c", "utility"))
    out = result.stdout + result.stderr
    assert result.returncode == 0 and "1 passed" in out, out


def test_обычный_режим_проходит_через_обвязку(внутренний_pytest):
    """Контроль пробы: та же проба без зажима зелёная и с тестом — значит, отказ
    выше дала обвязка, а не слепота пробы (код 5 «нет тестов»)."""
    result = внутренний_pytest(ПРОБА, обвязка=True)
    out = result.stdout + result.stderr
    assert result.returncode == 0 and "1 passed" in out, out


def test_решение_гейта_зажим_даёт_exit_17(monkeypatch):
    """Решение обвязки без настоящего taskpolicy — идёт и на Linux CI (out1 DS-I3)."""
    monkeypatch.setattr(machine_power, "background_throttle", lambda: "QoS BACKGROUND (taskpolicy -c background)")
    with pytest.raises(pytest.exit.Exception) as e:
        conftest.отказ_под_зажимом(types.SimpleNamespace())
    assert e.value.returncode == exit_codes.EXIT_BACKGROUND_QOS
    assert "QoS BACKGROUND" in e.value.msg and machine_power.BACKGROUND_REMEDY in e.value.msg


def test_решение_гейта_без_зажима_молчит(monkeypatch):
    monkeypatch.setattr(machine_power, "background_throttle", lambda: None)
    assert conftest.отказ_под_зажимом(types.SimpleNamespace()) is None


def test_воркер_xdist_датчик_не_спрашивает(monkeypatch):
    """Воркер наследует зажим контроллера: ответ уже дан, второй опрос — лишний."""
    def датчик():
        raise AssertionError("воркер не спрашивает датчик")
    monkeypatch.setattr(machine_power, "background_throttle", датчик)
    assert conftest.отказ_под_зажимом(types.SimpleNamespace(workerinput={})) is None


def test_гейт_стоит_первым_в_pytest_configure(monkeypatch):
    """До снятия `CHAROITE_ROOT` и временного корня: отказ ничего не меняет в
    окружении. Поведением, а не порядком строк исходника (out2 S-M4, DS-M4, H-M1)."""
    monkeypatch.setattr(machine_power, "background_throttle", lambda: "QoS BACKGROUND (taskpolicy -c background)")
    monkeypatch.setenv("CHAROITE_ROOT", "/корень-владельца")
    # регрессия не портит сессию воркера: побочный эффект после гейта — громкий отказ (out3 S-M3 = DS-M6)
    monkeypatch.setattr(conftest.tempfile, "mkdtemp", lambda **k: pytest.fail("гейт ниже временного корня"))
    корень = conftest.КОРЕНЬ_СЕССИИ
    with pytest.raises(pytest.exit.Exception) as e:
        conftest.pytest_configure(types.SimpleNamespace())
    assert e.value.returncode == exit_codes.EXIT_BACKGROUND_QOS
    assert os.environ["CHAROITE_ROOT"] == "/корень-владельца" and conftest.КОРЕНЬ_СЕССИИ is корень


def test_потолок_внутреннего_pytest_ниже_внешнего():
    """90 с ниже `timeout` pyproject: иначе два сигнала гонятся (out1 DS-M4)."""
    with open(pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml", "rb") as f:
        внешний = tomllib.load(f)["tool"]["pytest"]["ini_options"]["timeout"]
    assert conftest.ПОТОЛОК_ВНУТРЕННЕГО_С < int(внешний)
