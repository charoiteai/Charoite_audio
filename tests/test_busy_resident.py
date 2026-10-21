"""Модели в памяти Ollama как занятость машины (№595): перепись `/api/ps`,
а не объявления клиентов. 02.10 и 18.10 сторож отвечал «тихо», пока 32b/35b
держали память; 21.10 16:11 — 35b 22,5 ГБ, своп 9,0 из 10,2 ГБ, сторож rc=0.

Настоящий сокет нужен: таймаут, не-200 и не-JSON — это и есть логика сигнала."""
import contextlib
import datetime as dt
import http.server
import json
import pathlib
import sys
import threading
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import busy_signals  # noqa: E402

pytestmark = pytest.mark.сеть_разрешена

GB = 10**9


@contextlib.contextmanager
def _fake_ollama(status=200, body=None, delay=0.0, short=0, drip=0.0, chunked=False):
    """Подставной сервер `/api/ps` на 127.0.0.1:<свободный порт>. `short` —
    объявить на столько байт больше, чем отдать: ответ оборвётся. `chunked` — тело
    кусками `Transfer-Encoding: chunked`, как Go отдаёт ответ длиннее ~2 КБ, и один
    кусок двумя записями с паузой."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(delay)
            payload = (body if isinstance(body, bytes) else json.dumps(body).encode()) if body is not None else b""
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i in range(0, len(payload), 4096):
                        part = payload[i:i + 4096]
                        half = len(part) // 2
                        self.wfile.write(f"{len(part):x}\r\n".encode() + part[:half])
                        self.wfile.flush()
                        time.sleep(0.05)
                        self.wfile.write(part[half:] + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                self.send_header("Content-Length", str(len(payload) + short))
                self.end_headers()
                if drip:
                    for i in range(len(payload)):
                        self.wfile.write(payload[i:i + 1])
                        self.wfile.flush()
                        time.sleep(drip)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # клиент ушёл по таймауту — так и задумано

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _config(root, base_url, extra=""):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "config.yaml").write_text(f"llm:\n  base_url: {base_url}\n{extra}", encoding="utf-8")


def _model(name, size, expires=None):
    return {"name": name, "model": name, "size": size, "size_vram": size, "expires_at": expires,
            "context_length": 8192, "digest": "x", "details": {}}


@pytest.fixture
def ram_64gb(monkeypatch):
    monkeypatch.setattr(busy_signals, "_physical_bytes", lambda: 64 * 2**30)


def test_resident_big_model_makes_machine_busy(tmp_path, ram_64gb):
    """Опыт 21.10 16:11: 35b держит 22,5 ГБ, сторож отвечал «тихо» — теперь занято
    со словами и командой, которая освобождает память."""
    expires = "2026-10-21T17:39:24.402754+03:00"
    until = busy_signals._until_words(expires)
    with _fake_ollama(body={"models": [_model("qwen3.6:35b-mlx", 22_493_923_824, expires)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert busy == [f"локальные модели держат память (qwen3.6:35b-mlx 22,5 ГБ {until}; "
                        "всего 22,5 ГБ при пороге 11,5 ГБ) — освободить сразу: "
                        "ollama stop qwen3.6:35b-mlx"]
        assert busy_signals.machine_busy(tmp_path, count_resident=False) == []


MSK = dt.timezone(dt.timedelta(hours=3))


@pytest.mark.parametrize("expires, words", [
    ("2026-10-21T17:39:24.402754+03:00", "до 17:39"),
    ("2026-10-21T14:39:24Z", "до 17:39"),           # UTC → местное
    ("2026-10-21T17:39:24.402754123+03:00", "до 17:39"),  # Go: девять знаков дроби
    ("9999-12-31T23:59:59-12:00", ""),               # за краем datetime — OverflowError
    ("2026-10-22T09:15:00+03:00", "до 22.10 09:15"),  # другой день — с датой
    ("2318-02-01T00:00:00+03:00", "без срока"),       # keep_alive -1: год далеко вперёд
    ("не дата", ""),
    (None, ""),
])
def test_until_words_local_time(expires, words):
    """Срок — по местному времени: срез `[11:16]` показывал бы часы чужого пояса."""
    now = dt.datetime(2026, 10, 21, 16, 50, tzinfo=MSK)
    assert busy_signals._until_words(expires, now) == words


def test_two_models_sum_over_the_share(tmp_path, ram_64gb):
    """Порог — по сумме: две модели по 6 ГБ вместе выше 1/6 от 64 ГБ (11,5 ГБ)."""
    with _fake_ollama(body={"models": [_model("a", 6 * GB), _model("b", 7 * GB)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and "(b 7,0 ГБ, a 6,0 ГБ; всего 13,0 ГБ" in busy[0]
        assert busy[0].endswith("ollama stop b")


def test_chunked_answer_is_read_whole(tmp_path, ram_64gb):
    """Ответ длиннее ~2 КБ Ollama (Go) шлёт кусками без Content-Length: проверка
    обрыва по `length` его не трогает, а кусок, пришедший двумя записями, — не
    обрыв (DS M1 проверки правок Opus по №595)."""
    models = [_model(f"m{i}", GB // 10) for i in range(40)] + [_model("qwen3.6:35b", 22 * GB)]
    with _fake_ollama(body={"models": models}, chunked=True) as url:
        _config(tmp_path, url)
        got, problem = busy_signals.resident_models(tmp_path)
        assert problem is None and len(got) == 41
        assert busy_signals.machine_busy(tmp_path)[0].endswith("ollama stop qwen3.6:35b")


def test_floor_on_small_machine(tmp_path, monkeypatch):
    """16 ГиБ: 1/6 — 2,9 ГБ, и 4b (~3 ГБ) держала бы прогоны. Пол 8 ГБ: 4b — свободно, 9 ГБ — занято."""
    monkeypatch.setattr(busy_signals, "_physical_bytes", lambda: 16 * 2**30)
    with _fake_ollama(body={"models": [_model("gemma4:e4b", 3_100_000_000)]}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []
    with _fake_ollama(body={"models": [_model("qwen:14b", 9 * GB)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and "при пороге 8,0 ГБ)" in busy[0]


def test_half_memory_floor_on_8gb(tmp_path, monkeypatch):
    """8 ГиБ: пол не выше половины памяти (4,3 ГБ) — модель 7 ГБ там уже своп, 4b (3,1 ГБ) — нет."""
    monkeypatch.setattr(busy_signals, "_physical_bytes", lambda: 8 * 2**30)
    with _fake_ollama(body={"models": [_model("qwen:7b", 7 * GB)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and "при пороге 4,3 ГБ)" in busy[0]
    with _fake_ollama(body={"models": [_model("gemma4:e4b", 3_100_000_000)]}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []


def test_memory_size_unknown(tmp_path, monkeypatch):
    """sysconf не ответил: большая модель — «не читается», а не падение; без моделей объём не нужен."""
    monkeypatch.setattr(busy_signals, "_physical_bytes", lambda: None)
    with _fake_ollama(body={"models": [_model("big", 20 * GB)]}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == [
            "память моделей не читается (объём памяти машины не определён)"]
    with _fake_ollama(body={"models": [_model("bge-m3", 1_200_000_000)]}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []
    with _fake_ollama(body={"models": []}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []


def test_physical_bytes_sysconf_failure(monkeypatch):
    def nope(name):
        raise ValueError(name)
    monkeypatch.setattr(busy_signals.os, "sysconf", nope)
    assert busy_signals._physical_bytes() is None


def test_mlx_engine_still_counts_ollama(tmp_path, ram_64gb):
    """mlx-server: у движка свой `mlx_base_url`, а `llm.base_url` — по-прежнему Ollama, где
    живут эмбеддер, стенды и ручной `ollama run` (Opus по №595). Модель самого mlx-server
    здесь не видна (долг №615)."""
    with _fake_ollama(body={"models": [_model("qwen3.6:35b-mlx", 22_493_923_824)]}) as url:
        _config(tmp_path, url, "  engine: mlx-server\n  mlx_base_url: http://127.0.0.1:8080\n")
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and busy[0].startswith("локальные модели держат память (qwen3.6:35b-mlx")


@pytest.mark.parametrize("base_url", ["localhost:11434", "http://[::1", "http://0.0.0.0:11434"])
def test_address_grammar_is_a_config_problem(tmp_path, monkeypatch, base_url):
    """Адрес без схемы, неразборчивый адрес и 0.0.0.0 («слушать всё» вместо адреса) —
    поломка конфига (установщик откажет кодом 1), а не «не наша память» и не «свободно»
    (DS I3 проверки правок Opus по №595)."""
    def boom(*a, **k):
        raise AssertionError("спросили битый адрес")
    monkeypatch.setattr(busy_signals.net, "open_url", boom)
    _config(tmp_path, f'"{base_url}"')
    problem = busy_signals.resident_config_problem(tmp_path)
    assert problem and problem.startswith("конфиг ") and "llm.base_url" in problem
    assert busy_signals.machine_busy(tmp_path)[0].endswith("— почините config/config.yaml")


def test_small_resident_model_is_not_busy(tmp_path, ram_64gb):
    """Эмбеддер ночи (bge-m3 ~1,2 ГБ) машину не занимает: ночь 04.10 держала бы прогоны впустую."""
    with _fake_ollama(body={"models": [_model("bge-m3:latest", 1_200_000_000)]}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []
    with _fake_ollama(body={"models": []}) as url:
        _config(tmp_path, url)
        assert busy_signals.machine_busy(tmp_path) == []


def test_ollama_not_listening_is_free(tmp_path, ram_64gb):
    """Сервер не слушает — моделей нет, память свободна (CI без Ollama не краснеет)."""
    with _fake_ollama(body={"models": []}) as url:
        pass  # порт освобождён — слушателя нет
    _config(tmp_path, url)
    assert busy_signals.resident_models(tmp_path) == ([], None)
    assert busy_signals.machine_busy(tmp_path) == []


@pytest.mark.parametrize("status, body, words", [
    (200, {"nomodels": []}, "нет списка models"),
    (200, {"models": [{"name": "x"}]}, "нет размера"),
    (200, {"models": [{"name": "x", "size": "22"}]}, "нет размера"),
    (200, b"not json", "JSONDecodeError"),
    (500, {"error": "boom"}, "HTTP 500"),
])
def test_unreadable_census_is_busy(tmp_path, ram_64gb, status, body, words):
    """Ответ есть, а прочесть нельзя — «не читается»: сломанный сигнал не свободная машина."""
    with _fake_ollama(status=status, body=body) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and busy[0].startswith("память моделей не читается (") and words in busy[0]


def test_cut_answer_is_busy(tmp_path, ram_64gb):
    """Ответ оборвался посреди тела — IncompleteRead (HTTPException), не падение сторожа."""
    with _fake_ollama(body={"models": []}, short=100) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and "IncompleteRead" in busy[0] and busy[0].endswith("проверьте Ollama")


def test_dripping_server_is_cut_at_the_deadline(tmp_path, ram_64gb, monkeypatch):
    """Тело по байту раз в 0,1 с — таймаут сокета не срабатывает ни разу, а дедлайн
    на всю перепись срабатывает: «не читается» за ~0,5 с, а не за минуты."""
    monkeypatch.setattr(busy_signals, "RESIDENT_TIMEOUT_S", 0.5)
    with _fake_ollama(body={"models": [_model("x", 1)] * 20}, drip=0.1) as url:
        _config(tmp_path, url)
        t0 = time.monotonic()
        models, problem = busy_signals.resident_models(tmp_path)
        assert time.monotonic() - t0 < 2.0
        assert models == [] and problem.endswith("TimeoutError")


def test_oversized_answer_is_unreadable(tmp_path, ram_64gb, monkeypatch):
    monkeypatch.setattr(busy_signals, "RESIDENT_MAX_BYTES", 10)
    with _fake_ollama(body={"models": []}) as url:
        _config(tmp_path, url)
        models, problem = busy_signals.resident_models(tmp_path)
        assert models == [] and problem.endswith("ValueError")


def test_hanging_server_is_busy_within_timeout(tmp_path, ram_64gb, monkeypatch):
    monkeypatch.setattr(busy_signals, "RESIDENT_TIMEOUT_S", 0.3)
    with _fake_ollama(body={"models": []}, delay=2.0) as url:
        _config(tmp_path, url)
        t0 = time.monotonic()
        busy = busy_signals.machine_busy(tmp_path)
        assert time.monotonic() - t0 < 1.5
        assert len(busy) == 1 and busy[0].startswith("память моделей не читается (")


def test_no_own_config_never_asks_the_network(tmp_path, monkeypatch):
    """Голый каталог (тесты, CI) — без фолбэка на пример: иначе тест спросил бы
    живую Ollama владельца на :11434 и покраснел при резидентной 35b."""
    def boom(*a, **k):
        raise AssertionError("сеть без своего конфига")
    monkeypatch.setattr(busy_signals.net, "open_url", boom)
    assert busy_signals.resident_models(tmp_path) == ([], None)
    assert busy_signals.machine_busy(tmp_path) == []


def test_foreign_or_refused_address_is_not_our_memory(tmp_path, monkeypatch):
    """Чужая машина — о нашей памяти ничего не говорит; запрещённый адрес — тоже."""
    def boom(*a, **k):
        raise AssertionError("спросили чужой сервер")
    monkeypatch.setattr(busy_signals.net, "open_url", boom)
    _config(tmp_path, "http://192.168.1.20:11434", "  allow_remote: true\n")
    assert busy_signals.resident_models(tmp_path) == ([], None)
    _config(tmp_path, "http://192.168.1.20:11434")   # без allow_remote privacy отказывает
    assert busy_signals.resident_models(tmp_path) == ([], None)


def test_broken_config_is_busy(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("llm: [unclosed\n", encoding="utf-8")
    busy = busy_signals.machine_busy(tmp_path)
    assert len(busy) == 1 and busy[0].startswith("память моделей не читается (конфиг ")


def test_sensor_crash_is_busy_not_free(tmp_path, monkeypatch):
    """Сенсор тотален: мутатор зовёт его напрямую, без обёртки `machine_busy`."""
    monkeypatch.setattr(busy_signals, "resident_models", lambda root: 1 / 0)
    reason = "память моделей не читается (ZeroDivisionError) — проверьте Ollama"
    assert busy_signals.resident_busy_reason(tmp_path) == reason
    assert busy_signals.machine_busy(tmp_path) == [reason]
    assert busy_signals.machine_busy(tmp_path, count_resident=False) == []


def test_scalar_llm_section_is_unreadable_not_crash(tmp_path):
    """`llm: ollama` скаляром — не падение и не «Ollama сломана»: причина называет конфиг."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("llm: ollama\n", encoding="utf-8")
    reason = busy_signals.resident_busy_reason(tmp_path)
    assert reason.startswith("память моделей не читается (конфиг ") and "раздел llm не словарь" in reason
    assert reason.endswith("— почините config/config.yaml"), reason


@pytest.mark.parametrize("llm", ["[]", "false", "0", "''"])
def test_falsy_llm_section_is_broken_not_default(tmp_path, monkeypatch, llm):
    """`llm: []`/`false`/`0`/`''` — чат с таким конфигом не стартует (`l["model"]`), а сенсор
    не вправе подставить умолчание 127.0.0.1:11434, которого владелец не задавал."""
    def boom(*a, **k):
        raise AssertionError("спросили адрес по умолчанию")
    monkeypatch.setattr(busy_signals.net, "open_url", boom)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(f"llm: {llm}\n", encoding="utf-8")
    assert "раздел llm не словарь" in busy_signals.resident_config_problem(tmp_path)
    assert busy_signals.resident_models(tmp_path)[0] == []


@pytest.mark.parametrize("extra", ["  engine: cloud\n", "  engine: cloud\nsufler:\n  cloud_engine: true\n"])
def test_cloud_engine_still_counts_local_ollama(tmp_path, ram_64gb, extra):
    """`engine: cloud` — не повод молчать: без тумблера, под рубильником и в запасном пути
    чат сидит на Ollama по `llm.base_url` (№595 out4, Critical DeepSeek)."""
    with _fake_ollama(body={"models": [_model("qwen3.6:35b-mlx", 22_493_923_824)]}) as url:
        _config(tmp_path, url, extra)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and busy[0].startswith("локальные модели держат память (qwen3.6:35b-mlx")


def test_unknown_engine_is_unreadable_not_free(tmp_path, monkeypatch):
    """Опечатка в движке — сломанный конфиг, а не «сигнала нет»: Ollama держит память при любом движке."""
    def boom(*a, **k):
        raise AssertionError("спросили сеть при неизвестном движке")
    monkeypatch.setattr(busy_signals.net, "open_url", boom)
    _config(tmp_path, "http://127.0.0.1:11434", "  engine: ollam\n")
    models, problem = busy_signals.resident_models(tmp_path)
    assert models == [] and problem and "неизвестный движок" in problem


def test_no_census_endpoint_is_no_signal(tmp_path, ram_64gb):
    """404 на `/api/ps` — по адресу не Ollama (mlx_lm.server без `engine` в конфиге): сигнала нет,
    а не вечное «занято»."""
    with _fake_ollama(status=404, body={"error": "not found"}) as url:
        _config(tmp_path, url)
        assert busy_signals.resident_models(tmp_path) == ([], None)
        assert busy_signals.machine_busy(tmp_path) == []
