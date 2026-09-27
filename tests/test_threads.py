"""Потоки продукта: реестр, имя и роль (№415).

Проверяется ровно то, на чём держится правило: поток заводится через
`threads.spawn`/`threads.timer`, у него есть имя и роль, роль и причина
`detached` видны реестру, а заглушки (объект без слабой ссылки, объект без
`is_alive`) не роняют вызов и не считаются живыми потоками продукта.
"""
import threading

import pytest

import threads


def test_spawn_заводит_именованный_поток_с_ролью():
    """Поток стартует, несёт имя и демон, зовёт цель с аргументами, и реестр
    помнит его роль."""
    видел: list = []
    thread = threads.spawn(видел.append, args=(7,), name="probe", role="console")
    try:
        thread.join(2.0)
        assert видел == [7], "цель не позвана с аргументами"
        assert thread.name == "probe", "имя потерялось"
        assert thread.daemon is True, "поток продукта обязан идти демоном"
        assert isinstance(thread, threading.Thread), "поток — сам threading.Thread, без обёртки"
        assert threads.describe(thread) == ("console", None)
    finally:
        thread.join(2.0)


def test_args_и_kwargs_доходят_до_цели():
    """`args` — кортеж, `kwargs` — отображение: оба доходят до цели как есть."""
    ran = threading.Event()
    got: dict = {}

    def target(a, b=None):
        got["a"], got["b"] = a, b
        ran.set()

    thread = threads.spawn(target, args=(1,), kwargs={"b": 2}, name="args", role="process")
    try:
        assert ran.wait(2.0), "цель не дошла до конца"
        assert got == {"a": 1, "b": 2}
    finally:
        thread.join(2.0)


@pytest.mark.parametrize("door", ["spawn", "timer"])
@pytest.mark.parametrize("name, role", [("", "console"), ("probe", "чужой")], ids=["без-имени", "чужая-роль"])
def test_отказ_двери_до_старта_и_без_регистрации(monkeypatch, door, name, role):
    """Имя и роль обязательны — отказ ДО старта: поток не построен, не запущен
    и в реестр не попал. Одного факта исключения мало: отказ после `start()`
    оставил бы бегущий незарегистрированный поток (выходной круг 1 по #658, DS M3)."""
    построено: list = []

    class Шпион:
        def __init__(self, *a, **kw):
            построено.append(self)
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(threads.threading, "Thread", Шпион)
    monkeypatch.setattr(threads.threading, "Timer", Шпион)
    before = list(threads._registry)
    with pytest.raises(ValueError):
        if door == "spawn":
            threads.spawn(lambda: None, name=name, role=role)
        else:
            threads.timer(1.0, lambda: None, name=name, role=role)
    assert построено == [], "поток построен до проверки имени и роли"
    assert list(threads._registry) == before, "отказанный поток попал в реестр"


def test_реестр_не_держит_поток_живым():
    """Ключ реестра слабый: отработавший поток уходит вместе с последней
    ссылкой, а не копится с замыканиями встречи (выходной круг 1 по #658, DS I3)."""
    import gc
    import weakref
    thread = threads.spawn(lambda: None, name="слабый", role="console")
    thread.join(2.0)
    ref = weakref.ref(thread)
    assert thread not in threads.ours()
    del thread
    gc.collect()
    assert ref() is None, "реестр держит отработавший поток"


def test_start_false_регистрирует_без_старта():
    """`start=False`: поток уже в реестре и с ролью, но ещё не бежит — ручку на
    него владелец кладёт до старта (выходной круг 1 по #658, DS M1)."""
    ran = threading.Event()
    thread = threads.spawn(ran.set, name="отложенный", role="audio", start=False)
    try:
        assert threads.describe(thread) == ("audio", None)
        assert not thread.is_alive() and not ran.is_set(), "поток стартовал без старта"
        thread.start()
        assert ran.wait(2.0)
    finally:
        if thread.is_alive() or thread.ident is not None:
            thread.join(2.0)


def test_у_пакета_поиска_один_свой_поток_и_он_назван():
    """Пакет поиска — отдельный дистрибутив, реестра приложения у него нет, и
    судья швов освобождает файл целиком. Поэтому поток в нём — один и
    перечислен: второй `threading.Thread` в файле краснит здесь, а не живёт
    амнистией файлу (выходной круг 1 по #658, DS I2)."""
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "src" / "charoite_graph" / "graph_search.py"
    calls = [n for n in ast.walk(ast.parse(src.read_text(encoding="utf-8")))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("Thread", "Timer")]
    names = [next((k.value.value for k in c.keywords if k.arg == "name"), None) for c in calls]
    assert names == ["graph-search-refresh"], names


def test_роли_перечислены_кортежем():
    """Список ролей — источник истины отказа; его читает и сторож.
    Кортеж, а не список: чужая правка не должна мутировать таблицу на месте."""
    assert threads.ROLES == ("meeting", "process", "audio", "dictate", "console")


def test_чужую_роль_зовут_по_имени():
    """Отказ называет роль, а не молчит числом: иначе опечатку не найти."""
    with pytest.raises(ValueError, match="console"):
        threads.spawn(lambda: None, name="probe", role="console2")


def test_timer_зовёт_функцию_и_идёт_демоном():
    """Отложенная уборка: таймер будит функцию, несёт имя и роль и не держит
    выход процессом (`daemon=True`)."""
    done = threading.Event()
    thread = threads.timer(0.01, done.set, name="sweep", role="process")
    try:
        assert done.wait(2.0), "таймер не сработал"
        assert thread.name == "sweep"
        assert thread.daemon is True, "таймер обязан идти демоном"
        assert threads.describe(thread) == ("process", None)
    finally:
        thread.join(2.0)


def test_detached_причина_видна_и_поток_жив_в_реестре():
    """`detached` — причина строкой при потоке, и живой поток виден `ours()`."""
    release = threading.Event()
    thread = threads.spawn(release.wait, name="sleepy", role="meeting",
                           detached="живёт до конца процесса")
    try:
        assert threads.describe(thread) == ("meeting", "живёт до конца процесса")
        assert thread in threads.ours(), "живой зарегистрированный поток обязан быть в ours()"
    finally:
        release.set()
        thread.join(2.0)


def test_мёртвый_поток_уходит_из_реестра():
    """`ours()` — живые: отработавший поток из ответа уходит."""
    thread = threads.spawn(lambda: None, name="short", role="console")
    thread.join(2.0)
    assert not thread.is_alive()
    assert thread not in threads.ours()


def test_незнакомый_поток_описан_как_none():
    """Голый `threading.Thread` мимо реестра — не поток продукта."""
    thread = threading.Thread(target=lambda: None, name="чужой")
    assert threads.describe(thread) is None


def test_объект_без_слабой_ссылки_не_роняет_вызов(monkeypatch):
    """Заглушка теста без weakref (со `__slots__`) регистрации не поддаётся —
    и это не ошибка: вызов обязан вернуть поток, а не упасть."""
    class Заглушка:
        __slots__ = ()
        started = False

        def start(self):
            type(self).started = True

    monkeypatch.setattr(threads.threading, "Thread", lambda *args, **kw: Заглушка())
    got = threads.spawn(lambda: None, name="stub", role="console")
    assert isinstance(got, Заглушка)
    assert got.started, "заглушку обязаны позвать start-ом"


def test_заглушка_без_is_alive_пропускается(monkeypatch):
    """Заглушка без `is_alive` в реестр попадает (слабая ссылка есть), но
    живым потоком не считается: спрашивать её нечем."""
    class Заглушка:
        def start(self):
            pass

    monkeypatch.setattr(threads.threading, "Thread", lambda *args, **kw: Заглушка())
    got = threads.spawn(lambda: None, name="stub-no-alive", role="console")
    assert got not in threads.ours()


def test_отказ_старта_летит_наружу(monkeypatch):
    """`RuntimeError` старта не глотается: его ловит вызывающий (у аудио —
    исход значением)."""
    class Бунт(threading.Thread):
        def start(self):
            raise RuntimeError("потоков нет")

    monkeypatch.setattr(threads.threading, "Thread", Бунт)
    with pytest.raises(RuntimeError, match="потоков нет"):
        threads.spawn(lambda: None, name="rebel", role="console")


class ЗаглушкаБезIsAlive:
    """Класс уровня модуля: слабая ссылка есть, `is_alive` нет — для реестра."""

    def start(self):
        pass


def test_описание_читает_словарь_под_замком(monkeypatch):
    """`describe` — вопрос к реестру, а не к потоку: у зарегистрированной
    заглушки без `is_alive` роль всё равно видна."""
    monkeypatch.setattr(threads.threading, "Thread", lambda *args, **kw: ЗаглушкаБезIsAlive())
    got = threads.spawn(lambda: None, name="described", role="dictate")
    assert threads.describe(got) == ("dictate", None)
