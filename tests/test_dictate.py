"""Диктовка по хоткею (`src/dictate.py`): прогрев STT дожидается на любом выходе.

Прогрев идёт потоком, пока человек говорит. Брошенный посреди нативного init
(onnxruntime/GigaAM) поток ронял процесс на выходе SIGABRT (№174, #499/#503) —
поэтому `join` стоит в `finally`, а не только на пути распознавания (выходной
круг 1 по #658, DS I1).
"""
import io

import pytest

import dictate


class _Поток:
    def __init__(self):
        self.joined: list = []

    def join(self, timeout=None):
        self.joined.append(timeout)


class _Микрофон:
    def __init__(self, **kw):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def поток(monkeypatch):
    t = _Поток()
    monkeypatch.setattr(dictate.threads, "spawn", lambda *a, **kw: t)
    monkeypatch.setattr(dictate.sd, "InputStream", _Микрофон)
    monkeypatch.setattr(dictate, "load_user_or_example", lambda root: {})
    monkeypatch.setattr(dictate.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    return t


def test_без_кадров_прогрев_дожидается(поток):
    """Хоткей отпущен, кадров нет — ранний выход, но прогрев дождан."""
    dictate.main()
    assert поток.joined, "ранний выход бросил поток прогрева"
    assert all(t is None or t > 0 for t in поток.joined), f"ожидание без срока ожидания: {поток.joined}"


def test_ошибка_записи_тоже_дожидается_прогрева(поток, monkeypatch):
    """Упала запись — исключение летит наружу, а прогрев всё равно дождан."""
    def сломан(**kw):
        raise RuntimeError("микрофон занят")

    monkeypatch.setattr(dictate.sd, "InputStream", сломан)
    with pytest.raises(RuntimeError, match="микрофон занят"):
        dictate.main()
    assert поток.joined, "исключение бросило поток прогрева"
