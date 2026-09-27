"""Решающий гейт в тени: вердикт рядом с ⚡, ни слова разговора в логе.

Гейт — исследовательский шаг (docs/research/decision-gate.md): он ничего не
решает за демона, но строка тени обязана быть честной — иначе замер, по
которому решат включать гейт, будет замером шума.
"""

from __future__ import annotations

import math
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import decision_gate as dg  # noqa: E402


# --- конфиг --------------------------------------------------------------------

@pytest.mark.parametrize("cfg", [
    {}, {"sufler": None}, {"sufler": {}},
    {"sufler": {"decision_gate_shadow": False}},
    {"sufler": {"decision_gate_shadow": "true"}},   # строка — не решение владельца
    {"sufler": {"decision_gate_shadow": 1}},
])
def test_shadow_is_off_unless_config_says_literal_true(cfg):
    assert dg.shadow_enabled(cfg) is False


def test_shadow_turns_on_with_literal_true():
    assert dg.shadow_enabled({"sufler": {"decision_gate_shadow": True}}) is True


# --- чистые решения --------------------------------------------------------------

def test_normalize_turns_scores_into_distribution():
    probs = dg.normalize({"ask": 0.6, "skip": 0.2})
    assert probs == pytest.approx({"ask": 0.75, "skip": 0.25})
    # ноль у одной гипотезы — законная уверенность, а не поломка
    assert dg.normalize({"ask": 0.0, "skip": 0.4}) == {"ask": 0.0, "skip": 1.0}


@pytest.mark.parametrize("scores", [
    {"ask": 0.0, "skip": 0.0},          # судить не по чему — не 0.5/0.5
    {"ask": -0.1, "skip": 0.5},
    {"ask": float("nan"), "skip": 0.5},
    {"ask": True, "skip": 0.5},
])
def test_normalize_refuses_what_is_not_a_probability(scores):
    with pytest.raises(ValueError):
        dg.normalize(scores)


def test_zero_shot_asks_nli_about_each_label_hypothesis():
    calls = []

    def entail(premise, hypothesis):
        calls.append((premise, hypothesis))
        return 0.9 if hypothesis == "H-ask" else 0.1

    probs = dg.zero_shot_probs("Когда релиз?", entail, {"ask": "H-ask", "skip": "H-skip"})
    assert probs == pytest.approx({"ask": 0.9, "skip": 0.1})
    # реплика — посылка, гипотеза — описание метки; перепутать их значит
    # спрашивать «следует ли реплика из описания»
    assert sorted(calls) == [("Когда релиз?", "H-ask"), ("Когда релиз?", "H-skip")]


def test_zero_shot_refuses_hypotheses_without_every_label():
    with pytest.raises(ValueError, match="skip"):
        dg.zero_shot_probs("x", lambda a, b: 0.5, {"ask": "H"})


def test_calibrated_softens_with_temperature_and_keeps_order():
    sharp = dg.calibrated([2.0, 0.0], 1.0)
    soft = dg.calibrated([2.0, 0.0], 2.0)
    assert sharp[0] == pytest.approx(math.exp(2) / (math.exp(2) + 1))
    assert soft[0] == pytest.approx(math.exp(1) / (math.exp(1) + 1))
    assert 0.5 < soft[0] < sharp[0]
    assert sum(soft) == pytest.approx(1.0)


def test_calibrated_survives_huge_logits():
    assert dg.calibrated([1000.0, 0.0], 1.0) == pytest.approx([1.0, 0.0])


@pytest.mark.parametrize("t", [0.0, -1.0, float("inf"), float("nan")])
def test_calibrated_refuses_bad_temperature(t):
    with pytest.raises(ValueError):
        dg.calibrated([1.0, 0.0], t)


def _fixed(probs):
    return dg.Decider("fake", lambda _text: probs)


def test_decide_picks_argmax_and_measures_latency():
    ticks = iter([10.0, 10.25])
    v = dg.decide(_fixed({"ask": 0.2, "skip": 0.8}), "ну", clock=lambda: next(ticks))
    assert (v.label, v.confidence, v.backend) == ("skip", 0.8, "fake")
    assert v.ms == pytest.approx(250.0)


def test_decide_tie_goes_to_ask_so_the_gate_fails_open():
    assert dg.decide(_fixed({"skip": 0.5, "ask": 0.5}), "?").label == "ask"


def test_decide_refuses_foreign_labels():
    with pytest.raises(ValueError, match="метки"):
        dg.decide(_fixed({"ask": 0.5, "maybe": 0.5}), "x")


def _v(label, p):
    return dg.Verdict(label, p, {}, "fake", 1.0)


def test_cascade_decides_exactly_at_threshold_and_escalates_below():
    assert dg.cascade(_v("skip", 0.9), 0.9) == "skip"
    assert dg.cascade(_v("skip", 0.8999), 0.9) == "escalate"
    assert dg.cascade(_v("ask", 0.95), 0.9) == "ask"
    assert dg.cascade(None, 0.9) == "escalate"
    assert dg.cascade(_v("skip", 1.0), 1.0) == "skip"


@pytest.mark.parametrize("tau", [0.5, 0.2, 1.01, True, float("nan")])
def test_cascade_refuses_threshold_that_is_not_a_cascade(tau):
    with pytest.raises(ValueError):
        dg.cascade(_v("skip", 0.99), tau)


def test_outcome_refusal_wins_over_text_and_empty_is_failed():
    assert dg.outcome_of("Не вижу вопроса, уточните", refusal=True) == "refusal"
    assert dg.outcome_of("Релиз в пятницу.", refusal=False) == "answered"
    assert dg.outcome_of("  \n", refusal=False) == "failed"
    assert dg.outcome_of("", refusal=False) == "failed"


# --- обученная голова ------------------------------------------------------------

def test_head_meta_reads_order_temperature_and_length():
    labels, t, max_len = dg.read_head_meta('{"labels": ["skip", "ask"], "temperature": 1.7, "max_len": 128}')
    assert (labels, t, max_len) == (("skip", "ask"), 1.7, 128)
    assert dg.read_head_meta('{"labels": ["ask", "skip"]}')[1:] == (1.0, 256)
    assert dg.read_head_meta('{"labels": ["ask", "skip"], "max_len": 8}')[2] == 8   # нижняя граница законна


@pytest.mark.parametrize("raw", [
    '{"labels": ["ask"]}',
    '{"labels": ["ask", "ask"]}',
    '{"labels": ["ask", "no"]}',
    '{"labels": ["ask", "skip"], "temperature": 0}',
    '{"labels": ["ask", "skip"], "temperature": true}',
    '{"labels": ["ask", "skip"], "max_len": 4}',
    '{"labels": ["ask", "skip"], "max_len": 64.0}',
])
def test_head_meta_refuses_what_would_invert_or_break_the_gate(raw):
    with pytest.raises(ValueError):
        dg.read_head_meta(raw)


class _Input:
    def __init__(self, name):
        self.name = name


class _Session:
    def __init__(self, logits, inputs=("input_ids", "attention_mask")):
        self.logits = logits
        self.inputs = inputs
        self.fed = None

    def get_inputs(self):
        return [_Input(n) for n in self.inputs]

    def run(self, _outputs, feed):
        self.fed = feed
        return [[self.logits]]


class _Encoding:
    ids = [1, 2, 3]
    attention_mask = [1, 1, 1]
    type_ids = [0, 0, 0]


class _Tokenizer:
    def encode(self, _text):
        return _Encoding()


def test_onnx_head_feeds_only_declared_inputs():
    """ModernBERT-экспорт не объявляет token_type_ids: лишний вход — отказ сессии."""
    session = _Session([0.0, 0.0])
    dg.OnnxHead(session, _Tokenizer(), ("ask", "skip"))("Когда релиз?")
    assert set(session.fed) == {"input_ids", "attention_mask"}
    assert session.fed["input_ids"].tolist() == [[1, 2, 3]]


def test_onnx_head_maps_logits_by_declared_order_with_temperature():
    head = dg.OnnxHead(_Session([2.0, 0.0]), _Tokenizer(), ("skip", "ask"), temperature=2.0)
    probs = head("ну")
    # первый логит — skip, как объявил labels.json, а не как лежит LABELS
    assert probs["skip"] == pytest.approx(math.exp(1) / (math.exp(1) + 1))
    assert probs["ask"] == pytest.approx(1 - probs["skip"])


def test_onnx_head_refuses_logits_count_mismatch():
    with pytest.raises(ValueError, match="логитов"):
        dg.OnnxHead(_Session([1.0, 2.0, 3.0]), _Tokenizer(), ("ask", "skip"))("x")


# --- фабрика решателя ------------------------------------------------------------

class _Judge:
    def __init__(self, refused="", value=0.7):
        self.refused = refused
        self.value = value
        self.calls = 0

    def entail(self, premise, hypothesis):
        self.calls += 1
        return self.value if "вопрос" in hypothesis else 1 - self.value


def _head_files(d: Path, which=dg.HEAD_FILES):
    d.mkdir(parents=True, exist_ok=True)
    for f in which:
        (d / f).write_text("{}", encoding="utf-8")


def test_decider_prefers_trained_head_and_loads_it_lazily(tmp_path, monkeypatch):
    head = tmp_path / "question_gate"
    _head_files(head)
    loads = []
    monkeypatch.setattr(dg, "load_head", lambda d: loads.append(d) or (lambda t: {"ask": 1.0, "skip": 0.0}))
    dec = dg.decider(head_dir=head, judge=lambda: pytest.fail("NLI не нужен, когда есть голова"))
    assert dec.name == "head:question_gate" and not dec.refused
    assert loads == []                     # старт демона не платит за загрузку
    assert dec.probs("x") == {"ask": 1.0, "skip": 0.0}
    dec.probs("y")
    assert loads == [head]                 # один раз на процесс


def test_decider_without_complete_head_falls_back_to_nli(tmp_path):
    head = tmp_path / "question_gate"
    _head_files(head, which=("model.onnx", "labels.json"))   # без токенизатора
    judge = _Judge(value=0.8)
    dec = dg.decider(head_dir=head, judge=lambda: judge,
                     hypotheses={"ask": "есть вопрос", "skip": "обрывок"})
    assert dec.name == "nli-zero-shot"
    assert dec.probs("Когда релиз?") == pytest.approx({"ask": 0.8, "skip": 0.2})
    assert judge.calls == 2


def test_decider_refuses_honestly_without_any_model(tmp_path):
    dec = dg.decider(head_dir=tmp_path / "нет", judge=lambda: _Judge(refused="нет NLI"))
    assert dec.refused and "гейт в тени выключен" in dec.refused
    with pytest.raises(RuntimeError):
        dec.probs("x")


def test_lazy_remembers_a_failed_load():
    calls = []

    def load():
        calls.append(1)
        raise OSError("битый файл")

    lazy = dg._Lazy(load)
    for _ in range(3):
        with pytest.raises(RuntimeError, match="OSError"):
            lazy("x")
    assert calls == [1]


# --- строка тени ---------------------------------------------------------------

def test_shadow_line_round_trips_and_carries_nothing_from_the_question():
    """Строка тени — только состояние: ни слов вопроса, ни отпечатка текста. Хеш
    короткой реплики подбирался перебором (выходной круг 1 по #651, DS I1)."""
    v = dg.Verdict("skip", 0.9314, {}, "nli-zero-shot", 412.4)
    line = dg.shadow_line(v, "refusal")
    assert "q=" not in line
    rec = dg.parse_shadow_line("2026-09-27 10:15:02 " + line)   # префикс лога не мешает
    assert rec == {"backend": "nli-zero-shot", "label": "skip", "p": 0.931, "ms": 412.0,
                   "outcome": "refusal"}


def test_shadow_line_without_verdict_names_the_reason():
    rec = dg.parse_shadow_line(dg.shadow_line(None, "answered", "error:OSError"))
    assert rec == {"verdict": "none", "reason": "error:OSError", "outcome": "answered"}


def test_old_lines_with_a_question_hash_still_parse():
    """Строки прежнего формата (с `q=`) в err-логе остаются замером, а не мусором."""
    rec = dg.parse_shadow_line("gate-shadow: backend=x label=skip p=0.9 ms=1 outcome=refusal q=ab12")
    assert (rec["label"], rec["outcome"]) == ("skip", "refusal")


@pytest.mark.parametrize("line", [
    "hint-pulse: on=True new=10",
    "gate-shadow: backend=x label=skip p=high ms=1 outcome=refusal",
    "gate-shadow: backend=x label=maybe p=0.9 ms=1 outcome=refusal",
    "gate-shadow: backend=x label=skip p=0.9 ms=1 outcome=weird",
])
def test_foreign_or_broken_lines_are_not_shadow_records(line):
    assert dg.parse_shadow_line(line) is None


def test_shadow_line_refuses_unknown_outcome():
    with pytest.raises(ValueError):
        dg.shadow_line(None, "ok")


# --- тень одного ⚡ -------------------------------------------------------------

class _Log(list):
    def __call__(self, line):
        self.append(line)


def test_shadow_does_not_wait_for_a_slow_decider_and_writes_once():
    """⚡ не ждёт решателя: исход запоминается, строку пишет пришедший вторым."""
    release = threading.Event()

    def slow(_text):
        release.wait(5)
        return {"ask": 0.1, "skip": 0.9}

    log = _Log()
    run = dg.Shadow(dg.Decider("slow", slow), log).start("С какого бы?")
    run.finish("refusal")
    assert log == []                       # решатель ещё думает — ⚡ уже ушёл дальше
    release.set()
    run.join(5)
    run.finish("answered")                 # второй исход не перезаписывает первый
    run._write()                           # и поздний второй писатель — тоже: гонка пишет строку раз
    assert len(log) == 1
    rec = dg.parse_shadow_line(log[0])
    assert (rec["label"], rec["outcome"]) == ("skip", "refusal")


def test_shadow_written_by_finish_when_decider_was_first():
    log = _Log()
    run = dg.Shadow(_fixed({"ask": 0.8, "skip": 0.2}), log).start("Когда релиз?")
    run.join(5)
    assert log == []                       # без исхода писать нечего
    run.finish("answered")
    assert [dg.parse_shadow_line(x)["outcome"] for x in log] == ["answered"]


def test_shadow_decider_failure_becomes_reason_not_crash():
    def broken(_text):
        raise KeyError("x")

    log = _Log()
    run = dg.Shadow(dg.Decider("broken", broken), log).start("Когда релиз?")
    run.finish("answered")
    run.join(5)
    assert dg.parse_shadow_line(log[0])["reason"] == "error:KeyError"


@pytest.mark.parametrize("error", [OSError("stderr закрыт"), ValueError("I/O operation on closed file"),
                                   AttributeError("'NoneType' object has no attribute 'write'")])
def test_shadow_survives_any_broken_sink(error):
    """Сток тени (stderr) отказывает не только `OSError`: закрытый файл — это
    `ValueError`, снятый `sys.stderr` на финализации — `AttributeError`. Ни один
    из них не выходит в поток ⚡ (выходной круг 1 по #651, DS C1)."""
    tried = []

    def broken(line):
        tried.append(line)
        raise error

    run = dg.Shadow(_fixed({"ask": 0.8, "skip": 0.2}), broken).start("Когда релиз?")
    run.join(5)
    run.finish("answered")                 # не бросает
    assert len(tried) == 1 and dg.parse_shadow_line(tried[0])["outcome"] == "answered"


def test_unknown_outcome_does_not_crash_the_instant_answer():
    """Шов тотальный: исход не из `OUTCOMES` не роняет поток ⚡, а пишется как
    «failed» — что ответила модель, тени неизвестно (выходной круг 1 по #651)."""
    log = _Log()
    run = dg.Shadow(_fixed({"ask": 0.8, "skip": 0.2}), log).start("Когда релиз?")
    run.join(5)
    run.finish("ok")
    assert [dg.parse_shadow_line(x)["outcome"] for x in log] == ["failed"]


def test_thread_start_refusal_becomes_a_reason_not_a_crash(monkeypatch):
    """Отказ старта потока (лимит потоков процесса, память) — строка тени с
    причиной `start:<тип>`, а не исключение в потоке ⚡ (DS C1)."""
    import threading

    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    log = _Log()
    run = dg.Shadow(_fixed({"ask": 0.8, "skip": 0.2}), log).start("Когда релиз?")
    run.finish("answered")
    assert [dg.parse_shadow_line(x) for x in log] == [
        {"verdict": "none", "reason": "start:RuntimeError", "outcome": "answered"}]


def test_one_shadow_in_flight_the_next_question_is_counted_as_busy():
    """Зависший решатель не копит потоки: пока прошлый вердикт не готов, новый
    вопрос получает строку `reason=busy`, и пропуск виден в замере, а не
    растворяется тишиной (выходной круг 1 по #651, DS I3)."""
    release = threading.Event()
    calls = []

    def hung(_text):
        calls.append(1)
        release.wait(5)
        return {"ask": 0.1, "skip": 0.9}

    log = _Log()
    shadow = dg.Shadow(dg.Decider("hung", hung), log)
    first = shadow.start("Когда релиз?")
    second = shadow.start("А сроки?")
    second.finish("answered")
    second.join(0)                         # у пропуска потока нет — ждать нечего, и это не ошибка
    assert [dg.parse_shadow_line(x)["reason"] for x in log] == ["busy"]
    release.set()
    first.join(5)
    first.finish("refusal")
    third = shadow.start("И бюджет?")      # прошлый решил — новый снова судится
    third.join(5)
    third.finish("answered")
    assert len(calls) == 2 and [dg.parse_shadow_line(x).get("label") for x in log[1:]] == ["skip", "skip"]


def test_join_asks_the_thread_only_when_it_started():
    """`join` зовёт поток решателя, только если он есть и уже стартовал
    (`ident` не None); без потока это молчание, а не `AttributeError` (№415:
    поток заводит `threads.spawn` в `start`, а не конструктор)."""
    log = _Log()
    run = dg.ShadowRun("Когда релиз?", dg.Decider("d", lambda _t: {"ask": 1.0, "skip": 0.0}), log)
    called = []

    class _Поток:
        ident = 42

        def join(self, timeout=None):
            called.append(timeout)

    run._thread = _Поток()
    run.join(3)
    assert called == [3], "у стартовавшего потока join обязан дождаться его"
    run._thread = None
    run.join(3)                            # потока нет — ждать нечего, и это не ошибка
    assert called == [3], "join без потока не смеет звать чужой объект"


@pytest.mark.parametrize("question, dec", [
    ("Когда релиз?", None),
    ("Когда релиз?", dg.Decider("none", lambda t: {}, refused="нет модели")),
    ("", None),
])
def test_no_shadow_when_nobody_to_judge(question, dec):
    """Судить нечем — прогон-пустышка: `finish` и `join` ничего не делают и не
    пишут, поэтому цикл ⚡ зовёт их без условия."""
    log = _Log()
    run = dg.Shadow(dec, log).start(question)
    assert run is dg.IDLE and run.decided
    run.finish("answered")
    run.join(0)
    assert log == []


@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_is_a_named_skip_not_silence(question):
    """⚡ отвечает и без вопроса — тень пишет пропуск `no-question`, и бенч видит
    его в знаменателе, а не теряет (выходной круг 3 по #651, DS M3)."""
    log = _Log()
    decided = []
    run = dg.Shadow(dg.Decider("fake", lambda t: decided.append(t) or {"ask": 1.0, "skip": 0.0}),
                    log).start(question)
    assert run is not dg.IDLE and run.decided
    run.finish("answered")
    assert decided == [], "решатель на пустом вопросе не зовётся"
    assert len(log) == 1
    rec = dg.parse_shadow_line(log[0])
    assert rec["verdict"] == "none" and rec["reason"] == "no-question" and rec["outcome"] == "answered"


def test_the_idle_run_holds_no_state():
    """`IDLE` один на процесс — записать в него нельзя, общий объект не протечёт
    от вопроса к вопросу (DS M1)."""
    with pytest.raises(AttributeError):
        dg.IDLE.question = "Когда релиз?"


def test_default_head_dir_follows_the_data_root():
    import charoite_paths

    d = dg.default_head_dir()
    assert d == charoite_paths.resolve_root(dg.__file__) / "models" / "decision" / "question_gate"


def test_load_head_wires_meta_truncation_and_session(tmp_path, monkeypatch):
    import onnxruntime
    import tokenizers

    (tmp_path / "labels.json").write_text(
        '{"labels": ["skip", "ask"], "temperature": 2.0, "max_len": 64}', encoding="utf-8")
    seen = {}

    class Tok(_Tokenizer):
        @classmethod
        def from_file(cls, path):
            seen["tokenizer"] = path
            return cls()

        def enable_truncation(self, max_length):
            seen["max_length"] = max_length

    def session(path, opts, providers):
        seen["model"], seen["threads"], seen["providers"] = path, opts.intra_op_num_threads, providers
        return _Session([2.0, 0.0])

    monkeypatch.setattr(tokenizers, "Tokenizer", Tok)
    monkeypatch.setattr(onnxruntime, "InferenceSession", session)
    head = dg.load_head(tmp_path)
    assert seen == {"tokenizer": str(tmp_path / "tokenizer.json"), "max_length": 64,
                    "model": str(tmp_path / "model.onnx"), "threads": 2,
                    "providers": ["CPUExecutionProvider"]}
    probs = head("Когда релиз?")
    assert probs["skip"] == pytest.approx(math.exp(1) / (math.exp(1) + 1))


def test_verdict_and_decider_are_values():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        _v("skip", 0.9).label = "ask"
    with pytest.raises(dataclasses.FrozenInstanceError):
        _fixed({"ask": 1.0, "skip": 0.0}).refused = "x"


def test_shadow_thread_never_holds_the_daemon_on_stop():
    """Зависший решатель не должен держать процесс демона после «Стоп»."""
    release = threading.Event()
    run = dg.Shadow(dg.Decider("slow", lambda t: release.wait(5) and {}), _Log()).start("Когда релиз?")
    try:
        assert run._thread.daemon
    finally:
        release.set()
        run.join(5)


def test_decider_repr_names_it_without_the_function():
    assert repr(_fixed({"ask": 1.0, "skip": 0.0})) == "Decider(name='fake', refused='')"


def test_a_run_stuck_longer_than_the_answer_window_is_named_hung():
    """Возраст прогона в полёте — в причине пропуска: до окна ответа ⚡ — `busy`,
    дольше — `hung:<секунды>`. «Медленно» и «встал» различимы по строке, а не
    глазами по счёту причин (выходной круг 2 по #651, DS I2)."""
    release = threading.Event()
    now = [0.0]
    log = _Log()
    shadow = dg.Shadow(dg.Decider("hung", lambda t: release.wait(5) and {}), log, clock=lambda: now[0])
    first = shadow.start("Когда релиз?")
    now[0] = 10.0
    shadow.start("А сроки?").finish("answered")
    now[0] = dg.HUNG_S + 55.0
    shadow.start("И бюджет?").finish("answered")
    assert [dg.parse_shadow_line(x)["reason"] for x in log] == ["busy", f"hung:{dg.HUNG_S + 55.0:.0f}s"]
    release.set()
    first.join(5)


def test_a_base_exception_in_the_decider_does_not_leave_the_run_in_flight(monkeypatch):
    """Прогон отмечается решённым и на `BaseException` — иначе «в полёте» навсегда,
    и тень до конца встречи писала бы одни пропуски (круг 2 по #651, DS I2)."""
    class Остановлен(BaseException):
        pass

    def stop(_text):
        raise Остановлен()

    monkeypatch.setattr(threading, "excepthook", lambda args: None)   # поток падает — это ожидаемо
    log = _Log()
    shadow = dg.Shadow(dg.Decider("stop", stop), log)
    run = shadow.start("Когда релиз?")
    run.join(5)
    assert run.decided
    run.finish("answered")
    assert dg.parse_shadow_line(log[0])["reason"] == "error:BaseException"


@pytest.mark.parametrize("instant_on, cfg", [(False, {"sufler": {"decision_gate_shadow": True}}),
                                             (True, {"sufler": {}}),
                                             (True, {"sufler": {"decision_gate_shadow": "true"}})])
def test_shadow_for_stays_idle_when_off(monkeypatch, instant_on, cfg):
    """Выключен ⚡ или тень (только буквальное `true` включает) — решатель не
    строится, строки на старте нет, каждый вопрос получает пустышку. Прежде эти
    условия жили в демоне, и мутации CI их не видели (круг 2 по #651)."""
    built = []
    monkeypatch.setattr(dg, "decider", lambda: built.append(1) or _fixed({"ask": 1.0, "skip": 0.0}))
    log = _Log()
    shadow = dg.shadow_for(cfg, instant_on, log)
    assert built == [] and log == [] and not shadow.active
    assert shadow.start("Когда релиз?") is dg.IDLE


@pytest.mark.parametrize("dec, line", [(dg.Decider("nli-zero-shot", lambda t: {}), "гейт в тени: nli-zero-shot"),
                                       (dg.Decider("none", lambda t: {}, refused="нет модели"), "гейт в тени: нет модели")])
def test_shadow_for_names_its_decider_once_at_start(monkeypatch, dec, line):
    """Включено — одна строка на старте: какой решатель поднялся или почему его нет."""
    monkeypatch.setattr(dg, "decider", lambda: dec)
    log = _Log()
    dg.shadow_for({"sufler": {"decision_gate_shadow": True}}, True, log)
    assert log == [line]


def test_default_sink_is_stderr_flushed_at_once(monkeypatch):
    """Строки тени по умолчанию идут в stderr и сразу сбрасываются: err-лог
    демона читают по ходу встречи, буфер держал бы их до выхода."""
    class Поток:
        def __init__(self):
            self.wrote, self.flushed = [], 0

        def write(self, s):
            self.wrote.append(s)

        def flush(self):
            self.flushed += 1

    поток = Поток()
    monkeypatch.setattr(sys, "stderr", поток)
    monkeypatch.setattr(dg, "decider", lambda: dg.Decider("fake", lambda t: {}))
    dg.shadow_for({"sufler": {"decision_gate_shadow": True}}, True)
    # одним write: из двух потоков строки иначе склеиваются (DS M2 круга 3)
    assert поток.wrote == ["гейт в тени: fake\n"] and поток.flushed >= 1


def test_a_broken_sink_at_start_does_not_stop_the_daemon(monkeypatch):
    """Сток сломан уже на старте — строка о решателе теряется, демон идёт дальше."""
    def broken(_line):
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(dg, "decider", lambda: dg.Decider("fake", lambda t: {}))
    shadow = dg.shadow_for({"sufler": {"decision_gate_shadow": True}}, True, broken)
    assert shadow.active
