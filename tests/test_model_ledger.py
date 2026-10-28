"""Журнал вызовов модели по потребителям (№807).

Строка на запрос — настоящим путём транспорта `llm.py`: ответ сервера отдаёт
маршрут сторожа сети, а не подмена `_open_stream` или `_post_busy`, иначе журнал,
который живёт именно в этих швах, в тесте не участвовал бы.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import statistics
import sys
import time

import pytest
import requests

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import charoite_paths  # noqa: E402
import jsonl_log  # noqa: E402
import llm  # noqa: E402
import model_ledger  # noqa: E402

LOCAL = {
    "llm": {"model": "тест-модель", "small_model": "тест-мелкая", "num_ctx": 8192, "temperature": 0.4},
    "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"},
}
MLX = {**LOCAL, "llm": {**LOCAL["llm"], "engine": "mlx-server", "mlx_model": "mlx-тест"}}
DONE = {"done": True, "prompt_eval_count": 120, "eval_count": 7,
        "total_duration": 2_000_000_000, "load_duration": 100_000_000}


@pytest.fixture(autouse=True)
def _чистый_учёт(monkeypatch):
    model_ledger._swept.clear()
    llm._strict_json_clear()
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)   # паузы «занято» — без ожидания
    yield
    model_ledger._swept.clear()
    llm._strict_json_clear()


def _logs() -> pathlib.Path:
    return charoite_paths.resolve_root(llm.__file__) / "logs"


def _records() -> list[dict]:
    out = []
    for p in sorted(_logs().glob("model_calls-*.jsonl")):
        out += jsonl_log.read_records(p)
    return out


def _stream(lines, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.raw = io.BytesIO(b"\n".join(lines) + b"\n")
    return r


def _body(obj, status: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = (obj if isinstance(obj, str) else json.dumps(obj)).encode("utf-8")
    return r


def _ndjson(*chunks: str, done: dict | None = DONE) -> list[bytes]:
    lines = [json.dumps({"message": {"content": c}}).encode() for c in chunks]
    return lines + ([json.dumps(done).encode()] if done is not None else [])


def test_stream_writes_one_line_with_server_numbers(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а", "б"))
    with model_ledger.consumer("тест.подсказка"):
        assert "".join(c.stream("ВОПРОС", model="m1")) == "аб"
    [rec] = _records()
    assert rec["consumer"] == "тест.подсказка" and rec["src"] == "tag"
    assert (rec["kind"], rec["method"], rec["engine"], rec["model"]) == ("stream", "stream", "ollama", "m1")
    assert rec["outcome"] == "ok"
    assert (rec["in_tok"], rec["out_tok"], rec["server_s"], rec["load_s"]) == (120, 7, 2.0, 0.1)
    assert rec["first_s"] is not None and rec["wall_s"] >= rec["first_s"]
    assert rec["queue_s"] == 0.0


def test_wrapper_names_the_method_and_untagged_names_the_caller(_сеть_закрыта):
    """Имя обёртки — явным `_method`: к первому next() её кадра нет (вход DS C4).
    Без метки — первый кадр вне транспорта с пометкой `untagged:` (вход S-I1)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.hint("хвост", model="m1"))
    [rec] = _records()
    assert rec["method"] == "hint"
    assert rec["src"] == "stack"
    assert rec["consumer"] == ("untagged:test_model_ledger."
                               "test_wrapper_names_the_method_and_untagged_names_the_caller")


def test_helper_label_names_who_called_the_helper(_сеть_закрыта):
    """Общий помощник (у демона — `_collect`) ставит метку того, кто его позвал:
    по стеку все его вызовы слились бы в одного потребителя (вход DS C3)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))

    def collect(tokens):
        with model_ledger.consumer(model_ledger.caller_name(1)):
            return "".join(tokens)

    def name_loop():
        return collect(c.stream("x", model="m1"))

    def minutes_loop():
        return collect(c.stream("y", model="m1"))

    name_loop()
    minutes_loop()
    assert [r["consumer"] for r in _records()] == ["test_model_ledger.name_loop",
                                                   "test_model_ledger.minutes_loop"]


def test_abandoned_stream_is_closed_not_ok(_сеть_закрыта):
    """Потребитель бросил стрим (уступил ручному запросу) — «closed», одна строка."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а", "б", "в"))
    gen = c.stream("x", model="m1")
    assert next(gen) == "а"
    gen.close()
    [rec] = _records()
    assert rec["outcome"] == "closed" and rec["in_tok"] is None


def test_cut_stream_is_a_failure(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а", done=None))
    with pytest.raises(llm.LLMHTTPError):
        "".join(c.stream("x", model="m1"))
    [rec] = _records()
    assert rec["outcome"] == "broken"


def test_refusal_before_200_still_leaves_a_line(_сеть_закрыта):
    """Отказ до принятого запроса стоил времени и тоже пишется (вход DS C1)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _body("нет такой модели", 404)
    with pytest.raises(llm.LLMHTTPError):
        "".join(c.stream("x", model="m1"))
    [rec] = _records()
    assert (rec["kind"], rec["outcome"]) == ("stream", "http")


def test_busy_pauses_are_queue_seconds(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    tries = []

    def ответ(url, **k):
        tries.append(1)
        return _body("busy", 503) if len(tries) == 1 else _stream(_ndjson("а"))

    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = ответ
    assert "".join(c.stream("x", model="m1", busy_wait=30)) == "а"
    [rec] = _records()
    assert rec["outcome"] == "ok" and rec["queue_s"] == round(llm.BUSY_BACKOFF[0], 3)


def test_lease_close_and_exit_write_once(_сеть_закрыта):
    """`close` и `__exit__` стрима зовут запись оба — строка одна (вход DS M1)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    with c._open_stream(f"{c.base}/api/chat", {"model": "m1"}, 0.0) as r:
        list(r.iter_lines())
        r.close()
    assert len(_records()) == 1


def test_complete_writes_tokens(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _body(
        {"message": {"content": "ответ"}, **DONE})
    with model_ledger.consumer("тест.разбор"):
        assert c.complete("СЕКРЕТНАЯ ФРАЗА", model="m1") == "ответ"
    [rec] = _records()
    assert (rec["kind"], rec["method"], rec["outcome"], rec["consumer"]) == (
        "complete", "complete", "ok", "тест.разбор")
    assert (rec["in_tok"], rec["out_tok"], rec["server_s"]) == (120, 7, 2.0)


def test_complete_failure_has_its_kind(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _body({"error": "boom"})
    with pytest.raises(llm.LLMHTTPError):
        c.complete("x", model="m1")
    [rec] = _records()
    assert rec["outcome"] == "http"


def test_strict_json_retry_is_two_lines(_сеть_закрыта):
    """Отказ строгого JSON и повтор без format — два POST, две строки."""
    c = llm.LLM(LOCAL)
    sent = []

    def ответ(url, json=None, **k):  # noqa: A002 — имя параметра из requests
        sent.append("format" in json)
        if json.get("format"):
            return _body({"error": "structured output is unavailable"})
        return _body({"message": {"content": "{}"}, **DONE})

    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = ответ
    assert c.complete("x", model="m1", json_format=True) == "{}"
    assert sent == [True, False]
    assert [r["outcome"] for r in _records()] == ["http", "ok"]


def test_mlx_stream_has_no_tokens(_сеть_закрыта):
    """mlx-server и облако без stream_options `usage` не шлют — там null (вход S-M2)."""
    c = llm.LLM(MLX)
    lines = [b'data: {"choices": [{"delta": {"content": "a"}}]}', b"data: [DONE]"]
    _сеть_закрыта[("POST", f"{c.base}/v1/chat/completions")] = lambda url, **k: _stream(lines)
    assert "".join(c.minutes("стенограмма")).startswith("a")
    [rec] = _records()
    assert (rec["engine"], rec["model"], rec["method"], rec["outcome"]) == (
        "mlx-server", "mlx-тест", "minutes", "ok")
    assert rec["in_tok"] is None and rec["server_s"] is None


def test_sse_usage_is_counted_when_sent(_сеть_закрыта):
    c = llm.LLM(MLX)
    lines = [b'data: {"choices": [{"delta": {"content": "a"}}]}',
             b'data: {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 3}}',
             b"data: [DONE]"]
    _сеть_закрыта[("POST", f"{c.base}/v1/chat/completions")] = lambda url, **k: _stream(lines)
    "".join(c.stream("x"))
    [rec] = _records()
    assert (rec["in_tok"], rec["out_tok"]) == (50, 3)


def test_keep_zero_turns_the_ledger_off(_сеть_закрыта):
    cfg = {**LOCAL, "llm": {**LOCAL["llm"], "ledger_keep_days": 0}}
    c = llm.LLM(cfg)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert list(_logs().glob("model_calls-*")) == []


@pytest.mark.parametrize("raw, want", [(None, 35), (7, 7), (0, 0), (-3, 0), ("10", 35),
                                       (True, 35), (float("nan"), 35), (2.9, 2)])
def test_keep_days_from_config(raw, want):
    cfg = {"llm": {} if raw is None else {"ledger_keep_days": raw}}
    assert model_ledger.keep_days(cfg) == want


def test_old_days_are_swept_by_the_name(tmp_path):
    """Срок — по дате в имени файла, не по mtime: общий ретеншн `logs/` стёр бы
    журнал через двое суток простоя (вход S-C1)."""
    logs = tmp_path / "logs"
    logs.mkdir()
    today = time.strftime("%Y-%m-%d")
    recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - 3 * 86400))
    for day in ("2020-01-01", recent):
        (logs / f"model_calls-{day}.jsonl").write_text("{}\n", encoding="utf-8")
        os.utime(logs / f"model_calls-{day}.jsonl", (1, 1))      # mtime не решает
    (logs / "model_calls-чужое.jsonl").write_text("{}\n", encoding="utf-8")
    model_ledger._sweep(logs, 35, today)
    assert sorted(p.name for p in logs.iterdir()) == sorted(
        [f"model_calls-{recent}.jsonl", "model_calls-чужое.jsonl"])


def test_write_failure_does_not_break_the_call(_сеть_закрыта, monkeypatch, capsys):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))

    def диск_полон(path, rec, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(model_ledger.jsonl_log, "append_record", диск_полон)
    monkeypatch.setattr(model_ledger.once, "_said", set())
    assert "".join(c.stream("x", model="m1")) == "а"
    assert "".join(c.stream("x", model="m1")) == "а"
    err = capsys.readouterr().err
    assert err.count("журнал вызовов модели не пишется") == 1


def test_line_has_no_prompt_and_file_is_private(_сеть_закрыта):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _body(
        {"message": {"content": "ОТВЕТ МОДЕЛИ"}, **DONE})
    c.complete("СЕКРЕТНАЯ ФРАЗА", system="РОЛЬ", model="m1")
    [path] = _logs().glob("model_calls-*.jsonl")
    text = path.read_text(encoding="utf-8")
    for leak in ("СЕКРЕТНАЯ", "РОЛЬ", "ОТВЕТ МОДЕЛИ"):
        assert leak not in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_torn_line_is_not_glued_to_the_next(tmp_path):
    path = tmp_path / "logs" / "j.jsonl"
    path.parent.mkdir()
    path.write_text('{"a": 1}\n{"b": ', encoding="utf-8")       # процесс умер посреди записи
    jsonl_log.append_record(path, {"c": 3})
    warned = []
    assert jsonl_log.read_records(path, warn=warned.append) == [{"a": 1}, {"c": 3}]
    assert len(warned) == 1


def test_write_cost_is_small(tmp_path):
    """Запись идёт в нити вызова после снятия аренды (вход S-I5): замер цены."""
    call = model_ledger.Call(tmp_path, LOCAL, engine="ollama", model="m1", kind="stream",
                             method="hint", skip=frozenset())
    took = []
    for _ in range(200):
        call._finished = False
        t = time.perf_counter()
        call.finish("ok")
        took.append(time.perf_counter() - t)
    med = statistics.median(took)
    print(f"запись строки журнала: медиана {med * 1e6:.0f} мкс")
    assert med < 0.005
