"""Один реестр «уже сказали» на процесс: `once`.

Ключ — `(пространство, смысл)`. Здесь проверяется сам механизм: первый раз
говорим, повтор молчит, `forget` открывает эпизод заново, `reset` чистит всё
или одно пространство, чужой ключ и чужая печать друг другу не мешают, а
сломанный `write` не роняет работу и не запирает молчание навсегда.
"""
from __future__ import annotations

import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import once  # noqa: E402


class Поток:
    """stderr-двойник: помнит строки и считает `flush`."""

    def __init__(self):
        self.wrote: list[str] = []
        self.flushed = 0

    def write(self, s):
        self.wrote.append(s)

    def flush(self):
        self.flushed += 1


def test_first_marks_the_key_and_reports_the_repeat():
    once.reset()
    assert once.first(("т", "а")) is True
    assert once.first(("т", "а")) is False


def test_say_prints_once_and_forget_opens_the_episode_again(capsys):
    once.reset()
    assert once.say(("т", "ключ"), "строка") is True
    assert once.say(("т", "ключ"), "строка") is False
    assert capsys.readouterr().err.count("строка") == 1

    once.forget(("т", "ключ"))
    assert once.say(("т", "ключ"), "строка") is True
    assert capsys.readouterr().err.count("строка") == 1


def test_different_keys_are_independent(capsys):
    once.reset()
    assert once.say(("т", "певый"), "ПЕРВЫЙ") is True
    assert once.say(("т", "второй"), "ВТОРОЙ") is True
    err = capsys.readouterr().err
    assert "ПЕРВЫЙ" in err and "ВТОРОЙ" in err


def test_reset_whole_and_by_namespace():
    once.reset()
    once.first(("а", "ключ"))
    once.first(("б", "ключ"))

    once.reset("а")
    assert once.first(("а", "ключ")) is True, "пространство а не забыто"
    assert once.first(("б", "ключ")) is False, "чужое пространство задето"

    once.reset()
    assert once.first(("б", "ключ")) is True, "сброс всего не сработал"


def test_two_threads_on_one_key_print_one_line(capsys):
    """Решение и пометка — под одним замком: гонка на одном ключе даёт одну
    строку, а не две."""
    once.reset()
    старт = threading.Barrier(3)
    строки: list[bool] = []
    замок = threading.Lock()

    def бежать():
        старт.wait()
        сказал = once.say(("т", "гонка"), "СТРОКА")
        with замок:
            строки.append(сказал)

    нити = [threading.Thread(target=бежать) for _ in range(2)]
    for н in нити:
        н.start()
    старт.wait()
    for н in нити:
        н.join(5)
    assert sorted(строки) == [False, True], строки
    assert capsys.readouterr().err.count("СТРОКА") == 1


def test_a_slow_print_does_not_hold_another_forget(monkeypatch):
    """Печать идёт ПОСЛЕ выхода из замка: пока один ключ висит в медленном
    `write`, `forget` чужого ключа не ждёт."""
    once.reset()
    вошли = threading.Event()
    отпустить = threading.Event()

    class Медленный(Поток):
        def write(self, s):
            вошли.set()
            отпустить.wait(5)
            super().write(s)

    monkeypatch.setattr(once.sys, "stderr", Медленный())
    нить = threading.Thread(target=once.say, args=(("т", "медленный"), "строка"))
    нить.start()
    assert вошли.wait(2), "печать не началась"

    готово = threading.Event()
    threading.Thread(target=lambda: (once.forget(("т", "чужой")), готово.set()),
                     daemon=True).start()
    assert готово.wait(2), "forget чужого ключа ждал медленную печать"

    отпустить.set()
    нить.join(5)


def test_a_broken_write_does_not_raise_and_next_say_retries(monkeypatch, capsys):
    """Печать в `try/except`: строка о неполадке не роняет работу. Напечатать
    не удалось — ключ забыт, и следующая попытка говорит снова."""
    once.reset()
    настоящий = sys.stderr

    class Ломается:
        def write(self, s):
            raise OSError("нет stderr")

        def flush(self):
            pass

    monkeypatch.setattr(once.sys, "stderr", Ломается())
    assert once.say(("т", "беда"), "строка") is False, "сломанный write уронил say"

    monkeypatch.setattr(once.sys, "stderr", настоящий)
    assert once.say(("т", "беда"), "строка") is True, "ключ не забыт после сбоя печати"
    assert "строка" in capsys.readouterr().err


def test_say_flushes_stderr(monkeypatch):
    """Строка уходит владельцу сразу: на встрече между записью и чтением
    журнала живёт процесс, и невытолкнутый буфер — это молчание."""
    поток = Поток()
    once.reset()
    monkeypatch.setattr(once.sys, "stderr", поток)

    assert once.say(("т", "строка"), "строка") is True

    assert "".join(поток.wrote).strip() == "строка"
    assert поток.flushed >= 1, "строка осталась в буфере"


class ПоМедленнойЗаписи(Поток):
    """Двойник, который между двумя `write` одного `print` отдаёт процессор."""

    def write(self, s):
        self.wrote.append(s)
        threading.Event().wait(0.01)


def test_say_writes_the_line_in_one_call_so_threads_do_not_glue_lines():
    """Строка уходит одним `write` вместе с «\\n»: `print` писал текст и перевод
    строки двумя вызовами, и строки двух потоков склеивались (круг 1 по #652, DS I2)."""
    once.reset()
    поток = ПоМедленнойЗаписи()
    нити = [threading.Thread(target=once.say, args=((f"т{i}", i), f"строка {i}", поток)) for i in range(4)]
    for н in нити:
        н.start()
    for н in нити:
        н.join(5)
    assert sorted(поток.wrote) == [f"строка {i}\n" for i in range(4)], поток.wrote


def test_say_speaks_to_the_given_stream_and_keeps_the_forget_contract():
    """Сток — параметр: подсказка импорта уходит в stdout тем же `say`, и сбой
    печати в любом стоке забывает ключ (круг 1 по #652, DS M2)."""
    once.reset()

    class Сломан:
        def write(self, s):
            raise OSError("труба закрыта")

        def flush(self):
            pass

    assert once.say(("т", "к"), "раз", Сломан()) is False
    поток = Поток()
    assert once.say(("т", "к"), "два", поток) is True
    assert поток.wrote == ["два\n"] and поток.flushed == 1
