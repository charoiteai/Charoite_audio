"""«Сказать один раз» в пакете графа: `charoite_graph.notices.Notices`.

Контракт у реестра пакета и у реестра процесса приложения (`src/once.py`) один, а
реализаций пока две (свести в одну — №524). Поэтому основные случаи гоняются по
параметру — модуль `once` и экземпляр `Notices()`: расхождение реализаций краснит
здесь, а не ждёт, пока его заметит человек (вход 4 PR 1 №323, критика 2). Отдельно —
то, что есть только у класса: два экземпляра не делят память.
"""
from __future__ import annotations

import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import once  # noqa: E402
from charoite_graph.notices import Notices  # noqa: E402


@pytest.fixture(params=["once", "Notices"])
def реестр(request):
    """Реестр под контрактом: модуль процесса приложения или свежий экземпляр пакета."""
    if request.param == "once":
        once.reset()
        yield once
        once.reset()
    else:
        yield Notices()


class Поток:
    """stderr-двойник: помнит строки и считает `flush`."""

    def __init__(self):
        self.wrote: list[str] = []
        self.flushed = 0

    def write(self, s):
        self.wrote.append(s)

    def flush(self):
        self.flushed += 1


def test_first_marks_the_key_and_reports_the_repeat(реестр):
    assert реестр.first(("т", "а")) is True
    assert реестр.first(("т", "а")) is False


def test_say_prints_once_and_forget_opens_the_episode_again(реестр, capsys):
    assert реестр.say(("т", "ключ"), "строка") is True
    assert реестр.say(("т", "ключ"), "строка") is False
    assert capsys.readouterr().err.count("строка") == 1
    реестр.forget(("т", "ключ"))
    assert реестр.say(("т", "ключ"), "строка") is True
    assert capsys.readouterr().err.count("строка") == 1


def test_reset_whole_and_by_namespace(реестр):
    реестр.first(("а", "ключ"))
    реестр.first(("б", "ключ"))
    реестр.reset("а")
    assert реестр.first(("а", "ключ")) is True, "пространство а не забыто"
    assert реестр.first(("б", "ключ")) is False, "чужое пространство задето"
    реестр.reset()
    assert реестр.first(("б", "ключ")) is True, "сброс всего не сработал"


def test_the_line_goes_to_the_given_stream_in_one_write_and_is_flushed(реестр):
    поток = Поток()
    assert реестр.say(("т", "строка"), "строка", поток) is True
    assert поток.wrote == ["строка\n"], "строка и перевод — одним write"
    assert поток.flushed >= 1, "строка осталась в буфере"


def test_the_default_stream_is_stderr_read_at_call_time(реестр, monkeypatch):
    поток = Поток()
    monkeypatch.setattr(sys, "stderr", поток)
    assert реестр.say(("т", "поздний"), "строка") is True
    assert поток.wrote == ["строка\n"], "stderr читается при вызове, а не при импорте"


def test_a_broken_write_does_not_raise_and_next_say_retries(реестр, capsys):
    class Ломается:
        def write(self, s):
            raise OSError("нет stderr")

        def flush(self):
            pass

    assert реестр.say(("т", "беда"), "строка", Ломается()) is False, "сломанный write уронил say"
    assert реестр.say(("т", "беда"), "строка") is True, "ключ не забыт после сбоя печати"
    assert "строка" in capsys.readouterr().err


def test_two_threads_on_one_key_print_one_line(реестр, capsys):
    старт = threading.Barrier(3)
    сказали: list[bool] = []
    замок = threading.Lock()

    def бежать():
        старт.wait()
        сказал = реестр.say(("т", "гонка"), "СТРОКА")
        with замок:
            сказали.append(сказал)

    нити = [threading.Thread(target=бежать) for _ in range(2)]
    for н in нити:
        н.start()
    старт.wait()
    for н in нити:
        н.join(5)
    assert sorted(сказали) == [False, True], сказали
    assert capsys.readouterr().err.count("СТРОКА") == 1


def test_a_slow_print_does_not_hold_another_forget(реестр):
    """Печать — после замка: пока один ключ висит в медленном `write`, `forget` чужого
    ключа не ждёт."""
    вошли, отпустить = threading.Event(), threading.Event()

    class Медленный(Поток):
        def write(self, s):
            вошли.set()
            отпустить.wait(5)
            super().write(s)

    нить = threading.Thread(target=реестр.say, args=(("т", "медленный"), "строка", Медленный()))
    нить.start()
    assert вошли.wait(2), "печать не началась"
    готово = threading.Event()
    threading.Thread(target=lambda: (реестр.forget(("т", "чужой")), готово.set()), daemon=True).start()
    assert готово.wait(2), "forget чужого ключа ждал медленную печать"
    отпустить.set()
    нить.join(5)


def test_two_registries_do_not_share_what_they_said():
    """Состояние — в экземпляре: дверь с `Notices()` не делит память ни с другой дверью,
    ни с реестром процесса (вход 3 PR 1 №323, M1)."""
    a, b, k = Notices(), Notices(), ("т", "к")
    assert a.first(k) is True
    assert b.first(k) is True, "два экземпляра делят набор ключей"
    a.forget(k)
    assert b.first(k) is False, "forget одного задел другой"
    assert a.first(k) is True, "забытый ключ не звучит снова"
    b.reset()
    assert a.first(k) is False, "reset одного задел другой"
    assert b.first(k) is True
