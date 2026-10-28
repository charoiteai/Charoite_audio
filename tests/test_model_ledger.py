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
import urllib3

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
def _чистый_учёт(monkeypatch, tmp_path):
    # свой config.yaml корня теста: иначе срок пришёл бы из примера поставки в корне
    # кода — скрытый участник прогона (выход n807-out5, Haiku M2 = DS M2)
    if charoite_paths.resolve_root(llm.__file__) != tmp_path / "данные":   # до записи config.yaml (выход n807-out6, DS I4)
        pytest.fail("корень данных теста не tmp_path — config.yaml не пишу")
    _owner_config("llm: {}\n")
    model_ledger._swept.clear()
    model_ledger._terms.clear()
    llm._strict_json_clear()
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)   # паузы «занято» — без ожидания
    yield
    model_ledger._swept.clear()
    model_ledger._terms.clear()
    llm._strict_json_clear()


def _logs() -> pathlib.Path:
    return charoite_paths.resolve_root(llm.__file__) / "logs"


def _owner_config(text: str, stamp: float | None = None) -> pathlib.Path:
    """`config.yaml` корня данных теста (`tmp_path/данные`): срок журнала — только отсюда."""
    p = charoite_paths.resolve_root(llm.__file__) / "config" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if stamp is not None:
        os.utime(p, (stamp, stamp))
    return p


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days_ago * 86400))


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
    r._content_consumed = True          # `close()` не ищет `raw`, которого у тела нет
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
    with pytest.raises(llm.LLMHTTPError) as err:
        c.complete("x", model="m1")
    [rec] = _records()
    assert rec["outcome"] == err.value.kind == llm.failure_kind(err.value)


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
    first, second = _records()
    assert first["outcome"] not in ("ok", "closed") and second["outcome"] == "ok"


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
    """`0` в config.yaml корня выключает журнал, хотя в словаре клиента ключа нет."""
    _owner_config("llm:\n  ledger_keep_days: 0\n")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert list(_logs().glob("model_calls-*")) == []


@pytest.mark.parametrize("raw, want", [(None, 35), (7, 7), (7.0, 7), (0, 0), (-3, None), (-1, None),
                                       (0.5, None), (2.9, None), ("10", None), (True, None),
                                       (float("nan"), None), (10**400, 36500)])
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


@pytest.mark.parametrize("term", [35, 0])
def test_write_cost_is_small(tmp_path, term):
    """Запись идёт в нити вызова после снятия аренды (вход S-I5): замер цены. При `0`
    уборка идёт на каждом вызове — `listdir` каталога с 500 чужими именами (выход n807-out6, DS I8)."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(f"llm:\n  ledger_keep_days: {term}\n")
    (tmp_path / "logs").mkdir()
    for i in range(500):
        (tmp_path / "logs" / f"чужое-{i}.log").write_text("")
    call = model_ledger.Call(tmp_path, engine="ollama", model="m1", kind="stream",
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


def test_complete_without_label_names_the_caller(_сеть_закрыта):
    """Не-стрим без метки — тот, кто позвал `complete`, а не кадр `contextlib`
    (выход n807-out1, DS C1: прежде `untagged:contextlib._GeneratorContextManager.__enter__`)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _body(
        {"message": {"content": "ответ"}, **DONE})

    def minutes_tool():
        return c.complete("x", model="m1")

    minutes_tool()
    [rec] = _records()
    assert rec["consumer"] == "untagged:test_model_ledger.minutes_tool" and rec["src"] == "stack"


def test_first_chunk_is_text_not_protocol(_сеть_закрыта):
    """Ролевая дельта SSE — строка протокола, не первый кусок ответа."""
    c = llm.LLM(MLX)
    lines = [b'data: {"choices": [{"delta": {"role": "assistant"}}]}', b"data: [DONE]"]
    _сеть_закрыта[("POST", f"{c.base}/v1/chat/completions")] = lambda url, **k: _stream(lines)
    assert "".join(c.stream("x")) == ""
    [rec] = _records()
    assert rec["first_s"] is None and rec["outcome"] == "ok"


@pytest.mark.parametrize("engine", ["ollama", "mlx"])
def test_answer_cut_by_the_token_ceiling_is_marked(_сеть_закрыта, engine):
    """Упёрся в `num_predict` — «ok», но с `cut`: недописанный документ не равен полному."""
    if engine == "ollama":
        c = llm.LLM(LOCAL)
        url, lines = f"{c.base}/api/chat", _ndjson("а", done={**DONE, "done_reason": "length"})
    else:
        c = llm.LLM(MLX)
        url = f"{c.base}/v1/chat/completions"
        lines = [b'data: {"choices": [{"delta": {"content": "a"}, "finish_reason": "length"}]}', b"data: [DONE]"]
    _сеть_закрыта[("POST", url)] = lambda u, **k: _stream(lines)
    "".join(c.stream("x", model="m1"))
    [rec] = _records()
    assert rec["outcome"] == "ok" and rec["cut"] == "length"


def test_ledger_off_removes_earlier_days(_сеть_закрыта):
    """Выключенный журнал не оставляет прежних дней без механизма удаления (выход n807-out1)."""
    logs = _logs()
    logs.mkdir(parents=True, exist_ok=True)
    old = logs / f"model_calls-{time.strftime('%Y-%m-%d')}.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    _owner_config("llm:\n  ledger_keep_days: 0\n")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert list(logs.glob("model_calls-*")) == []


def test_line_survives_a_failing_lease_release(_сеть_закрыта, monkeypatch):
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))

    def отказ(self, *exc):
        raise OSError("flock")

    monkeypatch.setattr(llm.model_lease.Lease, "__exit__", отказ)
    with pytest.raises(OSError):
        "".join(c.stream("x", model="m1"))
    assert len(_records()) == 1


def test_short_write_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_log.os, "write", lambda fd, data: len(data) - 1)
    with pytest.raises(OSError):
        jsonl_log.append_record(tmp_path / "j.jsonl", {"модель": "м"})


def test_torn_multibyte_char_breaks_one_line_not_the_file(tmp_path):
    path = tmp_path / "j.jsonl"
    jsonl_log.append_record(path, {"модель": "первая"})
    raw = json.dumps({"модель": "вторая"}, ensure_ascii=False).encode()
    with open(path, "ab") as f:
        f.write(raw[:13])                                       # обрыв посреди знака
    with pytest.raises(UnicodeDecodeError):
        raw[:13].decode()                                       # и правда посреди: проверка не вакуумна
    jsonl_log.append_record(path, {"модель": "третья"})
    warned = []
    got = jsonl_log.read_records(path, warn=warned.append)
    assert got == [{"модель": "первая"}, {"модель": "третья"}] and len(warned) == 1


@pytest.mark.parametrize("doc", [None, "строкой", ["a"], {"llm": "строкой"}, {"llm": [1]},
                                 {"ledger_keep_days": 400}, {"ledger_keep_days": 400, "llm": {}}])
def test_keep_days_of_a_config_not_understood_is_unknown(doc):
    """Пустой файл, не словарь, `llm:` без словаря — «не поняли», а не 35 (выход n807-out5)."""
    assert model_ledger.keep_days(doc) is None


def test_missing_key_is_the_default():
    """`llm:` без значения — пустой раздел, как у остального продукта (выход n807-out6, DS I2)."""
    assert model_ledger.keep_days({}) == 35
    assert model_ledger.keep_days({"llm": {}}) == 35
    assert model_ledger.keep_days({"llm": None}) == 35


def test_warmup_is_its_own_consumer(_сеть_закрыта):
    """Прогрев — прямая цель нити: без метки стек назвал бы потребителем
    `threading.Thread.run` (выход n807-out2, DS I1)."""
    import threading
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("готов"))
    t = threading.Thread(target=c.warmup)
    t.start()
    t.join(10)
    [rec] = _records()
    assert (rec["consumer"], rec["src"], rec["method"]) == ("llm.warmup", "tag", "warmup")


def test_huge_server_numbers_do_not_break_the_answer(_сеть_закрыта):
    """Огромное целое в теле шлюза — `null` в строке, а не OverflowError
    в конце уже доставленного ответа (выход n807-out2, DS M2)."""
    c = llm.LLM(LOCAL)
    done = {**DONE, "prompt_eval_count": 10**400, "total_duration": 10**400}
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(
        [json.dumps({"message": {"content": "а"}}).encode(), json.dumps(done).encode()])
    with model_ledger.consumer("тест"):
        assert "".join(c.stream("ВОПРОС", model="m1")) == "а"
    [rec] = _records()
    assert rec["outcome"] == "ok" and rec["server_s"] is None and rec["in_tok"] is None
    assert rec["out_tok"] == 7


def test_warmup_at_start_applies_the_term(_сеть_закрыта):
    """Срок с диска действует с прогрева на старте демона и консоли, а не с первой
    подсказки встречи (выход n807-out3, Haiku I1; срок владельца — выход n807-out5, DS M2)."""
    _owner_config("llm:\n  ledger_keep_days: 400\n")
    old = _logs() / "model_calls-2000-01-01.jsonl"
    kept = _logs() / f"model_calls-{_day(60)}.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}\n")
    kept.write_text("{}\n")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("готов"))
    c.warmup()
    assert not old.exists() and kept.exists()
    assert (_logs().stat().st_mode & 0o777) == 0o700


def test_warmup_honours_the_switch_off(_сеть_закрыта):
    _owner_config("llm:\n  ledger_keep_days: 0\n")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("готов"))
    c.warmup()
    assert not list(_logs().glob("model_calls-*"))


@pytest.mark.parametrize("raw", ["0", False, -1, 0.5, 0.0, -0.0])
def test_unreadable_term_is_said_once(raw, capsys, monkeypatch):
    monkeypatch.setattr(model_ledger.once, "_said", set())
    assert model_ledger.keep_days({"llm": {"ledger_keep_days": raw}}) is None
    assert model_ledger.keep_days({"llm": {"ledger_keep_days": raw}}) is None
    assert capsys.readouterr().err.count("ledger_keep_days") == 1


@pytest.mark.parametrize("n, want", [(10**12, 10**12), (10**12 + 1, None), (0, 0), (-1, None)])
def test_token_count_bounds(n, want):
    assert model_ledger._tokens(n) == want


def test_sweep_survives_a_failed_chmod(tmp_path, monkeypatch):
    """Сбой прав каталога не отменяет уборку дня и не пишет «журнал не пишется»
    после записанной строки (выход n807-out4)."""
    old = tmp_path / "model_calls-2000-01-01.jsonl"
    old.write_text("{}\n")
    monkeypatch.setattr(model_ledger.charoite_paths, "secure_dir",
                        lambda p: (_ for _ in ()).throw(OSError("нет прав")))
    model_ledger._sweep(tmp_path, 35, "2026-10-28")
    assert not old.exists()


def test_owner_term_beats_the_callers_config(_сеть_закрыта):
    """Словарь, собранный процессом руками (`scripts/runtime_profile.py`), срок
    владельца не сокращает: 400 в config.yaml — файл 60-дневной давности жив
    (финальный Opus, I1)."""
    _owner_config("llm:\n  ledger_keep_days: 400\n")
    old = _logs() / f"model_calls-{_day(60)}.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}\n")
    c = llm.LLM({**LOCAL, "llm": {**LOCAL["llm"], "ledger_keep_days": 1}})   # срок вызывающего — не довод
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert old.exists()
    assert len(_records()) == 2          # пустая строка старого файла и строка этого вызова


def test_switch_off_reaches_a_live_client(_сеть_закрыта):
    """Правка config.yaml действует у живого клиента со следующего вызова —
    демону не нужен перезапуск (кэш по отпечатку конфига)."""
    cfg = _owner_config("llm:\n  ledger_keep_days: 35\n", stamp=1_000_000)
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert len(_records()) == 1
    _owner_config("llm:\n  ledger_keep_days: 0\n", stamp=2_000_000)
    "".join(c.stream("x", model="m1"))
    assert list(_logs().glob("model_calls-*")) == []
    assert cfg.exists()


def test_unread_config_writes_but_does_not_sweep(_сеть_закрыта, capsys, monkeypatch):
    """Конфиг корня не разобран — срок владельца неизвестен: строка пишется,
    прежние дни не стираются, причина — одной строкой."""
    monkeypatch.setattr(model_ledger.once, "_said", set())
    _owner_config("llm: [не закрыт\n")
    old = _logs() / "model_calls-2000-01-01.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    "".join(c.stream("x", model="m1"))
    assert old.exists() and len(_records()) == 2
    assert capsys.readouterr().err.count("config.yaml не прочитан") == 1


def test_name_with_a_newline_is_not_ours(tmp_path):
    """`$` совпадал перед завершающим `\\n` — чужой файл стирался (финальный Opus, M1)."""
    odd = tmp_path / "model_calls-2000-01-01.jsonl\n"
    odd.write_text("")
    model_ledger._sweep(tmp_path, 35, "2026-10-28")
    assert odd.exists()


class _ReadTimeoutRaw(io.BytesIO):
    """`raw` urllib3: первая строка ответа, потом сервер молчит дольше таймаута чтения."""

    def stream(self, amt=None, decode_content=None):
        yield _ndjson("а", done=None)[0] + b"\n"
        raise urllib3.exceptions.ReadTimeoutError(None, "/api/chat", "Read timed out.")


def test_read_timeout_mid_stream_is_a_timeout(_сеть_закрыта):
    """`requests` заворачивает таймаут чтения в `ConnectionError` — в журнале это
    `timeout`, а не «недоступен» (финальный Opus, M2)."""
    def answer(url, **k):
        r = requests.Response()
        r.status_code = 200
        r.raw = _ReadTimeoutRaw()
        return r
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = answer
    with pytest.raises(Exception):
        "".join(c.stream("x", model="m1"))
    [rec] = _records()
    assert rec["outcome"] == "timeout" and rec["first_s"] is not None


@pytest.mark.parametrize("text", ["", "- a\n", "llm: строкой\n", "ledger_keep_days: 400\n",
                                  "llm:\n  ledger_keep_days: много\n"])
def test_config_not_understood_sweeps_nothing(_сеть_закрыта, text, capsys, monkeypatch):
    """Обнулённый или непонятый config.yaml — не «владелец сказал 35»: строка пишется,
    60-дневный файл жив, причина — одной строкой (выход n807-out5, три головы)."""
    monkeypatch.setattr(model_ledger.once, "_said", set())
    _owner_config(text)
    kept = _logs() / f"model_calls-{_day(60)}.jsonl"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert kept.exists() and len(_records()) == 1
    assert "прежние дни не стираются" in capsys.readouterr().err


def test_switch_off_twice_in_a_day_removes_the_day_between(_сеть_закрыта):
    """0 → 35 → 0 в один день: строка, записанная при 35, стирается вторым 0 (выход n807-out5)."""
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    for stamp, term in ((1_000_000, 0), (2_000_000, 35), (3_000_000, 0)):
        _owner_config(f"llm:\n  ledger_keep_days: {term}\n", stamp=stamp)
        "".join(c.stream("x", model="m1"))
        assert bool(list(_logs().glob("model_calls-*"))) == bool(term)


def test_edit_with_the_same_mtime_is_seen(_сеть_закрыта):
    """`cp -p` с тем же временем: отпечаток видит правку по размеру и inode —
    кэш не держит срок 1 после правки на 400 (выход n807-out5, Haiku I1)."""
    _owner_config("llm:\n  ledger_keep_days: 1\n", stamp=1_000_000)
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    _owner_config("llm:\n  ledger_keep_days: 400\n", stamp=1_000_000)
    kept = _logs() / f"model_calls-{_day(60)}.jsonl"
    kept.write_text("")
    model_ledger._swept.clear()          # следующий день процесса
    "".join(c.stream("x", model="m1"))
    assert kept.exists()


def test_failing_fingerprint_keeps_the_line(_сеть_закрыта, monkeypatch, capsys):
    """`term` тотален: сбой отпечатка — «срок неизвестен», а не потерянная строка (выход n807-out5, DS M3)."""
    monkeypatch.setattr(model_ledger.once, "_said", set())
    monkeypatch.setattr(model_ledger.config_loader, "fingerprint",
                        lambda root: (_ for _ in ()).throw(RuntimeError("сбой")))
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    assert len(_records()) == 1
    assert "не пишется" not in capsys.readouterr().err


def _own_config_unseen(how: str) -> None:
    own = charoite_paths.resolve_root(llm.__file__) / "config" / "config.yaml"
    if how == "нет":
        own.unlink()
    elif how == "битая ссылка":
        own.unlink()
        own.symlink_to(own.parent / "нет-такого.yaml")
    elif how == "нет прав":
        own.parent.chmod(0)


@pytest.mark.parametrize("how", ["нет", "битая ссылка", "нет прав"])
def test_unseen_own_config_never_lends_the_example_term(_сеть_закрыта, how):
    """Своего config.yaml не видно — загрузчик взял бы пример поставки с его 35;
    пример не слово владельца: строка пишется, 60-дневный файл жив (выход n807-out6,
    DS C1 = Haiku C1). Срок владельца 400 — сначала виден, потом пропадает."""
    _owner_config("llm:\n  ledger_keep_days: 400\n")
    kept = _logs() / f"model_calls-{_day(60)}.jsonl"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("")
    c = llm.LLM(LOCAL)
    _сеть_закрыта[("POST", f"{c.base}/api/chat")] = lambda url, **k: _stream(_ndjson("а"))
    "".join(c.stream("x", model="m1"))
    model_ledger._swept.clear()          # следующий день процесса — уборка снова
    config_dir = charoite_paths.resolve_root(llm.__file__) / "config"
    try:
        _own_config_unseen(how)
        "".join(c.stream("x", model="m1"))
    finally:
        config_dir.chmod(0o755)
    assert kept.exists() and len(_records()) == 2
