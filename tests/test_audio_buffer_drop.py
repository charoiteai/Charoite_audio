"""Буфер живого распознавания не должен молча съедать полминуты речи.

20.08, ревью DeepSeek: при переполнении буфер урезался до ПОЛОВИНЫ потолка —
`buf = buf[-(cap // 2):]`. Один медленный чанк (а классификатор спорных
вопросов ходит к модели прямо в горячем цикле) выбрасывал ~30 секунд чужой
речи. Ни лога, ни статуса: на диск звук пишется отдельным sink, поэтому
офлайн-пересборка его возвращала, а живая лента шла кусками — жалоба
«переводит кусками, не всю речь в онлайне».
"""
import pathlib
import sys
import threading

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import audio  # noqa: E402

SR = 16000


def Hub(recording: bool = True):
    """Настоящий AudioHub без __init__: устройства и потоки этой логике не
    нужны, а методы берём те же, что работают на встрече."""
    hub = audio.AudioHub.__new__(audio.AudioHub)
    hub.sr = SR
    hub._bufs = {"mic": np.zeros(0, dtype=np.float32)}
    hub._drops = {}
    hub._sinks = {"mic": object()} if recording else {}
    hub._lock = threading.Lock()
    hub.said = []
    hub.on_status = hub.said.append
    return hub


@pytest.fixture
def clock(monkeypatch):
    """Часы под контролем: иначе тест окна отчёта зависит от того, успела ли
    машина прокрутить цикл за 30 секунд (флейк на занятом раннере)."""
    now = [1000.0]
    monkeypatch.setattr(audio.time, "time", lambda: now[0])
    return now


def _sec(n: float) -> np.ndarray:
    return np.ones(int(SR * n), dtype=np.float32)


def test_буфер_не_переполняется():
    hub = Hub()
    for _ in range(200):                      # 200 секунд при потолке 60
        hub._append("mic", _sec(1.0))

    assert len(hub._bufs["mic"]) <= SR * hub.BUF_CAP_S


def test_теряется_ровно_излишек_а_не_полминуты():
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))   # буфер полон ровно по потолок

    dropped = hub._append("mic", _sec(2.0))

    assert dropped == pytest.approx(2.0), (
        f"выброшено {dropped:.1f}с вместо 2с — вернулся сброс половины буфера")
    assert len(hub._bufs["mic"]) == SR * hub.BUF_CAP_S


def test_пока_есть_место_ничего_не_теряется():
    hub = Hub()
    assert hub._append("mic", _sec(59.0)) == 0.0
    assert hub._append("mic", _sec(1.0)) == 0.0


def test_потеря_звука_не_остаётся_молчаливой():
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(5.0)))

    assert hub.said, "человек обязан узнать, что живой звук потерян"
    assert "5" in hub.said[0], f"в статусе нет объёма потери: {hub.said[0]}"


def test_статус_не_спамит_на_каждом_чанке(clock):
    """Отставание длится минутами — строка в ленте нужна одна, не сотня."""
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    for _ in range(50):
        clock[0] += 0.5                      # полминуты окна не набирается
        hub._note_drop("mic", hub._append("mic", _sec(0.5)))

    assert len(hub.said) == 1, f"статусов {len(hub.said)} вместо одного"
    assert hub._drops["mic"][2] == pytest.approx(25.0), "итог потерь врёт"
    assert hub._drops["mic"][0] > 0, "интервальный счётчик обнулился не вовремя"


def test_кусок_длиннее_потолка_не_раздувает_буфер():
    """Аномально длинный кусок обязан обрезаться сам, а не оставлять буфер
    выше лимита с недосчитанной потерей (ревью 20.08: локальная и DeepSeek)."""
    hub = Hub()
    dropped = hub._append("mic", _sec(hub.BUF_CAP_S + 10))

    assert len(hub._bufs["mic"]) == SR * hub.BUF_CAP_S
    assert dropped == pytest.approx(10.0), f"потеря посчитана как {dropped:.1f}с"


def test_статус_говорит_свежую_потерю_а_не_сумму_с_начала(clock):
    """Копящаяся сумма не даёт понять, отстаём ли ПРЯМО сейчас."""
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(5.0)))
    clock[0] += hub._DROP_REPORT_S + 1            # окно отчёта прошло
    hub._note_drop("mic", hub._append("mic", _sec(3.0)))

    assert len(hub.said) == 2
    assert "3с" in hub.said[1], f"вторая строка врёт про свежую потерю: {hub.said[1]}"
    assert "8с" in hub.said[1], f"итог за встречу потерян: {hub.said[1]}"
    assert hub._drops["mic"][0] == pytest.approx(0.0), "интервал не сброшен"


def test_без_записи_на_диск_статус_не_утешает():
    """`record: false`, отказ открытия файла, смерть sink на полном диске —
    во всех трёх случаях выброшенный звук не вернуть ничем. Обещать человеку
    полную стенограмму в этот момент хуже, чем молчать (ревью 20.08, GLM)."""
    hub = Hub(recording=False)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(4.0)))

    assert hub.said, "потеря звука обязана быть озвучена и без записи"
    assert "не вернуть" in hub.said[0], f"статус утешает впустую: {hub.said[0]}"
    assert "стенограмма будет полной" not in hub.said[0]


def test_с_записью_на_диск_статус_успокаивает():
    hub = Hub(recording=True)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(4.0)))

    assert "стенограмма будет полной" in hub.said[0]
