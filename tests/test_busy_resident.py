"""Модели в памяти Ollama как занятость машины (№595): перепись `/api/ps`,
а не объявления клиентов. 02.10 и 18.10 сторож отвечал «тихо», пока 32b/35b
держали память; 21.10 16:11 — 35b 22,5 ГБ, своп 9,0 из 10,2 ГБ, сторож rc=0.

Настоящий сокет нужен: таймаут, не-200 и не-JSON — это и есть логика сигнала."""
import contextlib
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
def _fake_ollama(status=200, body=None, delay=0.0):
    """Подставной сервер `/api/ps` на 127.0.0.1:<свободный порт>."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(delay)
            payload = (body if isinstance(body, bytes) else json.dumps(body).encode()) if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

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


def _model(name, size, expires="2026-10-21T17:39:24.402754+03:00"):
    return {"name": name, "model": name, "size": size, "size_vram": size, "expires_at": expires,
            "context_length": 8192, "digest": "x", "details": {}}


@pytest.fixture
def ram_64gb(monkeypatch):
    monkeypatch.setattr(busy_signals, "_physical_bytes", lambda: 64 * 2**30)


def test_resident_big_model_makes_machine_busy(tmp_path, ram_64gb):
    """Опыт 21.10 16:11: 35b держит 22,5 ГБ, сторож отвечал «тихо» — теперь занято со словами."""
    with _fake_ollama(body={"models": [_model("qwen3.6:35b-mlx", 22_493_923_824)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1
        assert busy[0].startswith("локальные модели держат память (")
        assert "qwen3.6:35b-mlx 22,5 ГБ до 17:39" in busy[0]
        assert "всего 22,5 ГБ при пороге 11,5 ГБ" in busy[0]
        assert busy_signals.machine_busy(tmp_path, count_resident=False) == []


def test_two_models_sum_over_the_share(tmp_path, ram_64gb):
    """Порог — по сумме: две модели по 6 ГБ вместе выше 1/6 от 64 ГБ (11,5 ГБ)."""
    with _fake_ollama(body={"models": [_model("a", 6 * GB), _model("b", 6 * GB, expires=None)]}) as url:
        _config(tmp_path, url)
        busy = busy_signals.machine_busy(tmp_path)
        assert len(busy) == 1 and "a 6,0 ГБ до 17:39, b 6,0 ГБ;" in busy[0]


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
    monkeypatch.setattr(busy_signals, "resident_busy_reason", lambda root: 1 / 0)
    assert busy_signals.machine_busy(tmp_path) == ["память моделей не читается (ZeroDivisionError)"]
    assert busy_signals.machine_busy(tmp_path, count_resident=False) == []
