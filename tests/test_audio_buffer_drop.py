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


class Hub:
    """Голый буфер хаба: устройства и потоки для этой логики не нужны."""

    _append = audio.AudioHub._append
    _note_drop = audio.AudioHub._note_drop
    _say = audio.AudioHub._say
    BUF_CAP_S = audio.AudioHub.BUF_CAP_S
    _DROP_REPORT_S = audio.AudioHub._DROP_REPORT_S

    def __init__(self):
        self.sr = SR
        self._bufs = {"mic": np.zeros(0, dtype=np.float32)}
        self._drops = {}
        self._lock = threading.Lock()
        self.said = []
        self.on_status = self.said.append


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


def test_статус_не_спамит_на_каждом_чанке():
    """Отставание длится минутами — строка в ленте нужна одна, не сотня."""
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    for _ in range(50):
        hub._note_drop("mic", hub._append("mic", _sec(0.5)))

    assert len(hub.said) == 1, f"статусов {len(hub.said)} вместо одного"
    assert hub._drops["mic"][0] == pytest.approx(25.0), "счётчик потерь врёт"
