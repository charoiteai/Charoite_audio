"""Характеризация трёх потребителей памяти демона (№629 ч. 2 + №632, P4).

Ответ на вопрос (`gen_answer`), раскрытие темы (`expand_topic`) и живой контекст
(`live_context_loop`) — замыкания внутри `daemon.main()`, напрямую их не позвать.
Тест берёт их исходник из `daemon.py` по AST и исполняет в пространстве имён
модуля демона, где окружение замыкания — подделки с журналом: поиск по графу
(ниже фасада памяти — `brain.shared` и движок `GraphSearch.search`), модель,
нить, стенограмма, арбитр `hint_slot`, `emit`. Журнал каждого сценария —
упорядоченный список событий: аргументы поиска и `embed_timeout` на уровне движка,
текст и все kwargs `llm.stream` вместе с `llm.system` в момент вызова, присваивания
`llm.system`, `emit` и `emit_error` с текстами, `hint_slot` с аргументами,
`manual_evt`, `append_hint`, изменения нити и исключение наружу.

Снимок записан на коде ДО выноса профилей в `brain.py`
(`tests/fixtures/memory_profiles_characterization.json`) и обязан совпасть байт в
байт после выноса: перенос чисел, модели, промптов и веток не меняет ни одного
наблюдаемого события. Перезаписать снимок — `python tests/<этот файл> --write`;
это смена поведения демона, и она требует причины в PR.
"""
from __future__ import annotations

import ast
import contextlib
import datetime as real_dt
import json
import pathlib
import sys
import textwrap
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import brain  # noqa: E402
import daemon  # noqa: E402
import llm as llm_module  # noqa: E402
from charoite_graph import graph_search  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "memory_profiles_characterization.json"
V = graph_search.Verdict


def _closure_source(name: str) -> str:
    """Исходник вложенной функции `main()` демона — с отступом первой строки."""
    source = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    fn = next(node for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.FunctionDef) and node.name == name)
    return textwrap.dedent(ast.get_source_segment(source, fn, padded=True))


# ------------------------------------------------------------------ подделки


class Boom(RuntimeError):
    pass


class BoomResult(graph_search.Result):
    """Выдача, на которой падает упаковка: `fragments` бросает. Поиск поле не
    читает, упаковка — читает, поэтому исключение рождается внутри `pack`, а не
    в поиске и не в подмене фасада."""

    @property
    def fragments(self) -> str:
        raise Boom("упаковка упала")


class Journal(list):
    def add(self, *event):
        self.append(list(event))


class FakeGS:
    def __init__(self, log: Journal, result):
        self.log, self.result = log, result

    def search(self, query, **kwargs):
        self.log.add("engine.search", query, dict(sorted(kwargs.items())))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeLLM:
    def __init__(self, log: Journal, replies):
        self.__dict__["log"] = log
        self.__dict__["replies"] = list(replies)
        self.__dict__["_system"] = "SYS0"
        self.small = "small-model"
        self.model = "main-model"

    def __setattr__(self, key, value):
        if key == "system":
            self.log.add("llm.system=", value)
            self.__dict__["_system"] = value
        else:
            self.__dict__[key] = value

    @property
    def system(self):
        return self.__dict__["_system"]

    def stream(self, prompt, **kwargs):
        # system вызова по правилу настоящего `LLM.stream`: `system or self.system`
        # (сборка сообщения `system` в `src/llm.py`); иначе журнал не показывает,
        # что уходит в модель
        self.log.add("llm.stream", prompt, dict(sorted(kwargs.items())),
                     kwargs.get("system") or self.system)
        reply = self.replies.pop(0) if self.replies else ["ok"]
        if isinstance(reply, BaseException):
            raise reply
        yield from reply


class FakeEvent:
    def __init__(self, log: Journal, name: str):
        self.log, self.name, self.flag = log, name, False

    def set(self):
        self.log.add(f"{self.name}.set")
        self.flag = True

    def clear(self):
        self.log.add(f"{self.name}.clear")
        self.flag = False

    def is_set(self):
        return self.flag


class FakeStop:
    """Ровно `ticks` тактов цикла: два вопроса `is_set` на такт."""

    def __init__(self, log: Journal, ticks: int):
        self.log, self.left = log, 2 * ticks

    def is_set(self):
        self.left -= 1
        return self.left < 0

    def wait(self, t):
        self.log.add("stop.wait", t)


class FakeTranscript:
    def __init__(self, log: Journal, size: int = 10_000, tail_len: int = 500):
        self.log, self.tail_len = log, tail_len
        self.path = types.SimpleNamespace(stat=lambda: types.SimpleNamespace(st_size=size))

    def tail(self, n):
        self.log.add("tr.tail", n)
        return ("хвост стенограммы " * 400)[:min(n, self.tail_len)]


class FakeThread:
    def __init__(self, log: Journal, added=(2,), last_topic_title="Тема из нити"):
        self.log, self.added = log, list(added)
        self.last_topic_title = last_topic_title

    def add_archive(self, name, lines, **kwargs):
        self.log.add("thread.add_archive", name, list(lines), dict(sorted(kwargs.items())))
        return self.added.pop(0) if self.added else 0

    def render(self):
        self.log.add("thread.render")
        return "НИТЬ"

    def full(self):
        return "НИТЬ ЦЕЛИКОМ"


class FakeNode:
    def __init__(self, name):
        self.name = name


class FakeNodeIndex:
    def __init__(self, log: Journal, found=("Узел А",), lines=("строка узла 1", "строка узла 2")):
        self.log, self.found, self.lines = log, [FakeNode(n) for n in found], list(lines)

    def refresh(self):
        self.log.add("nodes.refresh")

    def lookup(self, text, **kwargs):
        kw = {k: (sorted(v) if isinstance(v, set) else v) for k, v in kwargs.items()}
        self.log.add("nodes.lookup", text[:40], dict(sorted(kw.items())))
        return list(self.found)

    def digest(self, node, **kwargs):
        self.log.add("nodes.digest", node.name, dict(sorted(kwargs.items())))
        return list(self.lines)


class FixedDT:
    class datetime:
        @staticmethod
        def now():
            return real_dt.datetime(2026, 10, 5, 12, 34)


def _result(status, *, blocks=("блок первый " * 30, "блок второй " * 30), dossiers=(),
            reason="", skipped=(), sem_used=True, cls=graph_search.Result):
    return cls(list(blocks), len(blocks), status=status, dossiers=list(dossiers),
               reason=reason, skipped=tuple(skipped), sem_used=sem_used)


NOT_READY = graph_search.Result([], 0, ready=False)
UNAVAILABLE = object()      # shared() отвечает None — граф не настроен


class Harness:
    """Пространство имён замыканий: модуль демона плюс подделки окружения."""

    def __init__(self, monkeypatch, *, result, replies=(), got=True, nodes=True,
                 thread_added=(2,), last_topic_title="Тема из нити", size=10_000,
                 tail_len=500, ticks=1, lock_free=True, node_found=("Узел А",)):
        self.log = log = Journal()

        daemon_cfg = {"sufler": {"live_context": True, "live_context_interval": 600}, "llm": {}}

        def shared(cfg, graph_dir=None, *, embedder):
            if cfg is not daemon_cfg:   # демон обязан отдать свой конфиг, иначе граф не найден (r1, I1)
                log.add("brain.shared: не cfg демона", cfg)
            log.add("brain.shared", graph_dir, embedder.model)
            return None if result is UNAVAILABLE else FakeGS(log, result)

        def embedder(cfg, **kw):
            if cfg is not daemon_cfg:
                log.add("llm.embedder: не cfg демона", cfg)
            return types.SimpleNamespace(model="bge-test")

        monkeypatch.setattr(brain, "shared", shared)
        monkeypatch.setattr(llm_module, "embedder", embedder)

        @contextlib.contextmanager
        def hint_slot(who, timeout=240.0, *, clear_manual_on_busy=False, quiet=False):
            log.add("hint_slot", who, timeout, clear_manual_on_busy, quiet)
            try:
                yield got
            finally:
                log.add("hint_slot.exit", who)

        class Lock:
            def acquire(self, blocking=True):
                log.add("expand_lock.acquire", blocking)
                return lock_free

            def release(self):
                log.add("expand_lock.release")

        self.llm = FakeLLM(log, replies)
        ns = dict(vars(daemon))
        ns.update(
            cfg=daemon_cfg,
            llm=self.llm,
            tr=FakeTranscript(log, size=size, tail_len=tail_len),
            thread=FakeThread(log, thread_added, last_topic_title),
            node_index=FakeNodeIndex(log, node_found) if nodes else None,
            hint_slot=hint_slot,
            manual_evt=FakeEvent(log, "manual_evt"),
            stop=FakeStop(log, ticks),
            expand_lock=Lock(),
            system_base="БАЗА РОЛИ",
            voice_names={"S1": "Анна"},
            emit=lambda obj: log.add("emit", obj),
            emit_error=lambda text, reason="": log.add("emit_error", text, reason),
            append_hint=lambda path, header, body: log.add("append_hint", header, body),
            dt=FixedDT,
        )
        for name in ("gen_answer", "expand_topic", "live_context_loop"):
            exec(compile(_closure_source(name), f"<daemon.{name}>", "exec"), ns)  # noqa: S102
        self.ns = ns

    def call(self, name, *args):
        try:
            self.ns[name](*args)
        except Exception as exc:  # noqa: BLE001 — исключение наружу — тоже наблюдаемое
            self.log.add("raised", type(exc).__name__, str(exc))
        return self.log


CONF = dict(result=_result(V.CONFIDENT))
WEAK_SCOPE = dict(result=_result(V.WEAK, skipped=("архив встреч",)))
WEAK_BARE = dict(result=_result(V.WEAK, blocks=()))
UNVERIFIED = dict(result=_result(V.UNVERIFIED, reason="Ollama занята", sem_used=False))
EMPTY = dict(result=_result(V.EMPTY, blocks=()))
BOOM = dict(result=_result(V.CONFIDENT, cls=BoomResult))
# Три фрагмента по ~1 600 знаков — больше любого бюджета блока (3 000 / 2 600):
# иначе журнал не видит, где потребитель режет блок, и сдвиг бюджета на
# единицу проходит зелёным (опровергающий опыт №629 ч. 2)
LONG = tuple(f"фрагмент {i}: " + "длинный текст встречи " * 70 for i in (1, 2, 3))
OVER = dict(result=_result(V.CONFIDENT, blocks=LONG))
OVER_WEAK = dict(result=_result(V.WEAK, blocks=LONG))

#: сценарий → (функция, аргументы, настройки подделок)
SCENARIOS: dict[str, tuple[str, tuple, dict]] = {
    # ответ на вопрос
    "answer.confident": ("gen_answer", ("что решили по релизу?",), dict(CONF, replies=[["Ре", "лиз"]])),
    "answer.weak_scope": ("gen_answer", ("кто ведёт?",), WEAK_SCOPE),
    "answer.unverified": ("gen_answer", ("когда?",), UNVERIFIED),
    "answer.empty": ("gen_answer", ("что?",), EMPTY),
    "answer.not_ready": ("gen_answer", ("что?",), dict(result=NOT_READY)),
    "answer.unavailable": ("gen_answer", ("что?",), dict(result=UNAVAILABLE)),
    "answer.engine_raises": ("gen_answer", ("что?",), dict(result=Boom("движок упал"))),
    "answer.busy": ("gen_answer", ("что?",), dict(CONF, got=False)),
    "answer.stream_fails": ("gen_answer", ("что?",), dict(CONF, replies=[Boom("модель легла")])),
    "answer.pack_raises": ("gen_answer", ("что?",), BOOM),
    "answer.over_budget": ("gen_answer", ("что решили?",), dict(OVER, replies=[["ок"]])),
    # раскрытие темы
    "expand.confident": ("expand_topic", ("Релиз",), dict(CONF, replies=[["- факт 1\n- факт 2"]])),
    "expand.confident_nothing_new": ("expand_topic", ("Релиз",), dict(CONF, thread_added=(0,))),
    "expand.title_from_thread": ("expand_topic", ("  ",), CONF),
    "expand.empty_thread": ("expand_topic", ("",), dict(CONF, last_topic_title="")),
    "expand.busy_lock": ("expand_topic", ("Релиз",), dict(CONF, lock_free=False)),
    "expand.not_ready_nodes": ("expand_topic", ("Релиз",), dict(result=NOT_READY)),
    "expand.not_ready_no_nodes": ("expand_topic", ("Релиз",), dict(result=NOT_READY, thread_added=(0,))),
    "expand.unavailable_no_nodes": ("expand_topic", ("Релиз",), dict(result=UNAVAILABLE, nodes=False)),
    "expand.weak_nodes": ("expand_topic", ("Релиз",), WEAK_SCOPE),
    "expand.weak_absence": ("expand_topic", ("Релиз",), dict(WEAK_BARE, node_found=())),
    "expand.empty_absence": ("expand_topic", ("Релиз",), dict(EMPTY, nodes=False)),
    "expand.unverified_model": ("expand_topic", ("Релиз",), dict(UNVERIFIED, node_found=())),
    "expand.slot_busy": ("expand_topic", ("Релиз",), dict(CONF, got=False)),
    "expand.stream_fails": ("expand_topic", ("Релиз",), dict(CONF, replies=[Boom("модель легла")])),
    "expand.pack_raises": ("expand_topic", ("Релиз",), BOOM),
    "expand.over_budget": ("expand_topic", ("Релиз",), dict(OVER, replies=[["- факт"]])),
    # живой контекст
    "live.confident": ("live_context_loop", (), dict(CONF, replies=[["релиз, шлюз, Анна"]])),
    "live.weak_with_nodes": ("live_context_loop", (), dict(WEAK_SCOPE, replies=[["релиз"]])),
    "live.not_ready_nodes": ("live_context_loop", (), dict(result=NOT_READY, replies=[["релиз"]])),
    "live.empty_block": ("live_context_loop", (), dict(result=NOT_READY, node_found=(), replies=[["релиз"]])),
    "live.query_fallback": ("live_context_loop", (), dict(CONF, replies=[["  "]])),
    "live.slot_busy": ("live_context_loop", (), dict(CONF, got=False)),
    "live.short_tail": ("live_context_loop", (), dict(CONF, tail_len=100)),
    "live.small_growth": ("live_context_loop", (), dict(CONF, size=1000)),
    "live.pack_raises": ("live_context_loop", (), dict(BOOM, replies=[["релиз"]])),
    "live.over_budget": ("live_context_loop", (), dict(OVER, replies=[["релиз"]])),
    "live.over_budget_nodes": ("live_context_loop", (), dict(OVER_WEAK, replies=[["релиз"]])),
}


def run_scenario(monkeypatch, name: str) -> list:
    func, args, opts = SCENARIOS[name]
    return Harness(monkeypatch, **opts).call(func, *args)


def run_live_then_answer(monkeypatch) -> list:
    """Такт живого → ответ: такт записал блок памяти в общий `llm.system` для
    авто-подсказки, а ответ идёт с ролью без памяти (№652) — в журнале видно и
    то, и другое."""
    h = Harness(monkeypatch, result=_result(V.CONFIDENT), replies=[["релиз"], ["ответ"]])
    h.call("live_context_loop")
    return h.call("gen_answer", "а что по релизу?")


def _normalize(log) -> list:
    return json.loads(json.dumps(log, ensure_ascii=False, default=str))


def snapshot(monkeypatch) -> dict:
    out = {name: _normalize(run_scenario(monkeypatch, name)) for name in SCENARIOS}
    out["live_then_answer"] = _normalize(run_live_then_answer(monkeypatch))
    return out


@pytest.mark.parametrize("name", [*SCENARIOS, "live_then_answer"])
def test_memory_consumers_match_the_snapshot_byte_for_byte(monkeypatch, name):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = (run_live_then_answer(monkeypatch) if name == "live_then_answer"
           else run_scenario(monkeypatch, name))
    assert _normalize(got) == golden[name]


def test_snapshot_covers_every_scenario_and_observes_the_seams(monkeypatch):
    """Снимок не пуст и видит то, ради чего снят: таймаут вектора на уровне
    движка у каждого потребителя, kwargs модели, присваивание `llm.system`,
    исключение упаковки наружу у раскрытия и живого и его отсутствие у ответа.

    Последние два утверждения — на СВЕЖЕМ прогоне `live_then_answer`, а не на
    снимке: сторожем поведения ответа снимок быть не может (правка `gen_answer`
    его не меняет), а поведение меняют `daemon.py` и заглушка."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == {*SCENARIOS, "live_then_answer"}

    def engine_timeouts(name):
        return [e[2]["embed_timeout"] for e in golden[name] if e[0] == "engine.search"]

    assert engine_timeouts("answer.confident") == [1.25]
    assert engine_timeouts("expand.confident") == [4.0]
    assert engine_timeouts("live.confident") == [3.0]
    assert any(e[0] == "llm.system=" for e in golden["live.confident"])
    assert not any(e[0] == "llm.system=" for e in golden["live.empty_block"])
    assert golden["expand.pack_raises"][-1][:2] == ["raised", "Boom"]
    assert golden["live.pack_raises"][-1][:2] == ["raised", "Boom"]
    assert not any(e[0] == "raised" for e in golden["answer.pack_raises"])

    live = _normalize(run_live_then_answer(monkeypatch))
    # Положительный контроль: такт живого записал память в общее поле — его
    # значение длиннее роли (иначе «ответ видит блок прошлого такта» было бы
    # пусто: startswith верен и с памятью, и без неё).
    systems = [e for e in live if e[0] == "llm.system="]
    assert systems, "такт живого не записал память в общее llm.system"
    assert len(systems[-1][1]) > len("БАЗА РОЛИ"), "блок памяти такта — длиннее роли"
    # Ответ идёт ровно с ролью без памяти (№652): равенство, не startswith.
    streams = [e for e in live if e[0] == "llm.stream"]
    assert streams[-1][3] == "БАЗА РОЛИ", "ответ идёт с ролью без памяти"


if __name__ == "__main__" and "--write" in sys.argv:
    mp = pytest.MonkeyPatch()
    try:
        GOLDEN.write_text(json.dumps(snapshot(mp), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    finally:
        mp.undo()
    print(f"снимок записан: {GOLDEN}")
