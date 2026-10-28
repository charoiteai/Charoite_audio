"""Гейт фонового режима процесса в обвязке (№817).

Полный набор под `taskpolicy -c background` шёл 24–41 мин против 3 и давал 4–12
ложных падений таймаутами: потолки времени рассчитаны на обычные ядра. Обвязка
отказывает такому прогону до сборки — кодом `EXIT_BACKGROUND_QOS` и причиной.
Проба — настоящий `taskpolicy` над отдельным pytest-процессом с обвязкой:
подставной датчик проверил бы только ветку, а не то, что зажим виден из
`pytest_configure` контроллера.
"""
import os
import sys

import pytest

import exit_codes

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
