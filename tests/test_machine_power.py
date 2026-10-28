"""Датчик режима энергосбережения (№783): разбор `pmset -g` и запуск."""
import subprocess

import pytest

import machine_power as mp

# Настоящий вывод `pmset -g` на машине разработки, 24.10.2026, на сети,
# режим «высокая производительность» (powermode 2, Ollama 71,6 ток/с).
REAL_AC_HIGH = """System-wide power settings:
Currently in use:
 standby              1
 Sleep On Power Button 1
 hibernatefile        /var/vm/sleepimage
 powernap             1
 networkoversleep     0
 disksleep            10
 sleep                0 (sleep prevented by caffeinate, powerd, caffeinate)
 hibernatemode        3
 ttyskeepawake        1
 displaysleep         0
 tcpkeepalive         1
 powermode            2
 womp                 1
"""

# СИНТЕТИКА: тот же вывод с `powermode 1`. Значение 1 видели в `pmset -g custom`
# и в plist (`LowPowerMode = 1`, 23.10, Ollama 29 ток/с), но не в `pmset -g`
# целиком. До мержа владелец снимает настоящий вывод в режиме энергосбережения
# на сети и заменяет эту фикстуру (условие DS C2 входного круга №783).
SYNTHETIC_AC_LOW = REAL_AC_HIGH.replace(" powermode            2", " powermode            1")


def test_настоящий_вывод_на_сети_не_энергосбережение():
    assert mp.parse_low_power(REAL_AC_HIGH) is False


def test_powermode_1_энергосбережение():
    assert mp.parse_low_power(SYNTHETIC_AC_LOW) is True


def test_авто_режим_не_энергосбережение():
    assert mp.parse_low_power(REAL_AC_HIGH.replace("powermode            2", "powermode            0")) is False


def test_узнанный_вывод_без_ключа_снимает_справку():
    """Профиль источника без `powermode` — не энергосбережение, а не «не знаю»:
    иначе поднятая справка висела бы до конца встречи (DS I1 входного круга)."""
    assert mp.parse_low_power(_without_powermode(REAL_AC_HIGH)) is False


def _without_powermode(text):
    return "\n".join(line for line in text.splitlines() if "powermode" not in line) + "\n"


@pytest.mark.parametrize("value, low", [("1", True), ("0", False)])
def test_lowpowermode_читается_без_powermode(value, low):
    """Вывод с ключом `lowpowermode` вместо `powermode` (Sonnet I1, DS M5 выхода)."""
    assert mp.parse_low_power(_without_powermode(REAL_AC_HIGH) + f" lowpowermode         {value}\n") is low


def test_powermode_важнее_lowpowermode():
    """Подстрока `powermode` в `lowpowermode` не путает ключи (Sonnet I2 входного круга)."""
    assert mp.parse_low_power(REAL_AC_HIGH + " lowpowermode         1\n") is False
    assert mp.parse_low_power(SYNTHETIC_AC_LOW + " lowpowermode         0\n") is True


@pytest.mark.parametrize("text", ["", "pmset: command failed", None, b"System-wide power settings:"])
def test_неузнанный_вывод_не_трогает_справку(text):
    assert mp.parse_low_power(text) is None


def test_два_разных_значения_не_трогают_справку():
    assert mp.parse_low_power(REAL_AC_HIGH + " powermode            1\n") is None
    two = _without_powermode(REAL_AC_HIGH) + " lowpowermode         1\n lowpowermode         0\n"
    assert mp.parse_low_power(two) is None


class _Res:
    def __init__(self, rc, out):
        self.returncode, self.stdout = rc, out


def test_запуск_pmset_полным_путём_без_stdin(monkeypatch):
    monkeypatch.setattr(mp.sys, "platform", "darwin")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return _Res(0, SYNTHETIC_AC_LOW)

    assert mp.read_low_power(run) is True
    assert seen["cmd"] == ["/usr/bin/pmset", "-g"]
    assert seen["stdin"] is subprocess.DEVNULL and seen["timeout"] == 2.0


@pytest.mark.parametrize("run", [
    lambda *a, **kw: _Res(1, REAL_AC_HIGH),
    lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("pmset", 2)),
    lambda *a, **kw: (_ for _ in ()).throw(OSError("нет pmset")),
])
def test_сбой_запуска_даёт_none(monkeypatch, run):
    monkeypatch.setattr(mp.sys, "platform", "darwin")
    assert mp.read_low_power(run) is None


def test_не_macos_даёт_none_без_запуска(monkeypatch):
    monkeypatch.setattr(mp.sys, "platform", "linux")

    def run(*a, **kw):
        raise AssertionError("pmset не зовут вне macOS")

    assert mp.read_low_power(run) is None


class _Lib:
    """Подставная libSystem: класс QoS и darwin-приоритет процесса."""

    def __init__(self, qos, prio):
        self.qos_class_self = lambda: qos
        self.getpriority = lambda which, who: prio if which == mp.PRIO_DARWIN_PROCESS else 0


@pytest.mark.parametrize("qos, prio, ждём", [
    (0x21, 0, None),                                         # обычный режим
    (0x11, 0, None),                                         # utility: замер 28.10 — не зажим
    (0x19, 0, None),                                         # user-initiated
    (0x09, 0, "QoS BACKGROUND (taskpolicy -c background)"),
    (0x05, 0, "QoS MAINTENANCE (taskpolicy -c maintenance)"),
    (0x21, 1, "darwin-фон (taskpolicy -b или setpriority PRIO_DARWIN_BG)"),       # ядро: 1 (опыт 28.10)
    (0x21, -1, None),                                        # getpriority не прочитан
    (0x09, 1, "QoS BACKGROUND (taskpolicy -c background)"),  # класс называется первым
])
def test_фоновый_режим_по_классу_и_приоритету(monkeypatch, qos, prio, ждём):
    monkeypatch.setattr(mp.sys, "platform", "darwin")
    assert mp.background_throttle(lambda path: _Lib(qos, prio)) == ждём


def test_фоновый_режим_вне_macos_не_грузит_библиотеку(monkeypatch):
    monkeypatch.setattr(mp.sys, "platform", "linux")

    def load(path):
        raise AssertionError("libSystem не грузят вне macOS")

    assert mp.background_throttle(load) is None


@pytest.mark.parametrize("load", [
    lambda path: (_ for _ in ()).throw(OSError("нет библиотеки")),
    lambda path: object(),                                   # нет нужных символов
])
def test_фоновый_режим_не_читается_даёт_none(monkeypatch, load):
    monkeypatch.setattr(mp.sys, "platform", "darwin")
    assert mp.background_throttle(load) is None
