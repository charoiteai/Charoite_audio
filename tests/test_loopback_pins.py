"""№525: швы к адресу на этой машине держат свои параметры — правило прокси и таймаут.

Мутатор диапазона судит и числа рядом с правкой: таймаут `30 → 0` и `stream=True → False`
не должны выживать. Здесь вызов каждого шва снимается подставным транспортом: адрес,
таймаут и правило прокси (`proxies_for`) сверяются вместе.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import urllib.request

import pytest
import requests

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from charoite_graph import net  # noqa: E402
import daemon  # noqa: E402
import llm_health  # noqa: E402

DIRECT = {"http": None, "https": None, "all": None}


class _Resp:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"
    content = b"{}"

    def json(self):
        return {"text": "ok"}

    def raise_for_status(self):
        return None

    def iter_lines(self):
        return iter(())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Rec:
    """Подставной `requests.post/get`: помнит адрес и параметры."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        return _Resp()


def _route(маршруты, метод: str, url: str, rec=None) -> _Rec:
    """Маршрут сторожа сети на полный адрес: обработчик получает `(url, **kwargs)` вызова."""
    rec = rec or _Rec()
    маршруты[(метод, url)] = rec
    return rec


def _direct(kw: dict) -> bool:
    return kw.get("proxies") == DIRECT and kw.get("allow_redirects") is False


def test_forget_reaches_the_memory_directly_with_its_timeout(_сеть_закрыта):
    import forget_meeting
    rec = _route(_сеть_закрыта, "POST", f"{forget_meeting.BRAIN}/forget")
    forget_meeting.brain_forget("2026-07-15_1400", sent=True, enabled=True, explicit=True)
    (url, kw), = rec.calls
    assert url == f"{forget_meeting.BRAIN}/forget" and kw["timeout"] == 30 and _direct(kw)


def test_rename_reaches_the_memory_directly_with_its_timeout(_сеть_закрыта):
    import rename_meeting
    rec = _route(_сеть_закрыта, "POST", f"{rename_meeting.BRAIN}/rename")
    rename_meeting.brain_rename("2026-07-15_1400", "Тема", sent=True, enabled=True, explicit=True)
    (url, kw), = rec.calls
    assert url == f"{rename_meeting.BRAIN}/rename" and kw["timeout"] == 60 and _direct(kw)


def test_bench_models_stream_goes_direct_and_streams(_сеть_закрыта):
    import bench_models
    rec = _route(_сеть_закрыта, "POST", f"{bench_models.BASE}/api/chat")
    bench_models.ask("m", "в", timeout=77)
    (url, kw), = rec.calls
    assert url == f"{bench_models.BASE}/api/chat" and kw["stream"] is True and kw["timeout"] == 77
    assert _direct(kw)


def test_bench_models_version_probe_goes_direct(monkeypatch, _сеть_закрыта):
    import bench_models
    rec = _Rec()

    def down(url, **kw):
        rec.calls.append((url, kw))
        raise requests.ConnectionError("нет сервера")

    _route(_сеть_закрыта, "GET", f"{bench_models.BASE}/api/version", down)
    monkeypatch.setattr(sys, "argv", ["bench_models.py", "m"])
    assert bench_models.main() == 1
    (url, kw), = rec.calls
    assert url == f"{bench_models.BASE}/api/version" and kw["timeout"] == 3 and _direct(kw)


def test_memory_bench_search_goes_through_open_url_with_its_timeout(monkeypatch, tmp_path):
    import memory_bench
    seen = {}

    def fake_open(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        return io.BytesIO(json.dumps({"text": "Ничего не найдено"}).encode())

    monkeypatch.setattr(net, "open_url", fake_open)
    graph = tmp_path / "граф"
    graph.mkdir()
    assert memory_bench.search_brain(graph, "вопрос")[0] == memory_bench.BRAIN_ALIVE
    assert seen == {"url": "http://127.0.0.1:8100/vault_search", "timeout": 25}


def test_doctor_asks_ollama_through_open_url_with_its_timeout(monkeypatch, capsys):
    import doctor
    seen = {}

    def fake_open(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return io.BytesIO(json.dumps({"models": []}).encode())

    monkeypatch.setattr(net, "open_url", fake_open)
    cfg = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": ""}, "sufler": {}}
    doctor.check_ollama(cfg)
    assert seen == {"url": "http://127.0.0.1:11434/api/tags", "timeout": 4}


def test_gigastt_health_goes_direct_with_its_timeout(_сеть_закрыта):
    rec = _route(_сеть_закрыта, "GET", daemon.GIGASTT_HEALTH)
    assert daemon.gigastt_alive() is True
    (url, kw), = rec.calls
    assert url == "http://127.0.0.1:9876/health" and kw["timeout"] == 2 and _direct(kw)

    def down(url, **kw):
        raise requests.ConnectionError("нет")

    _сеть_закрыта[("GET", daemon.GIGASTT_HEALTH)] = down
    assert daemon.gigastt_alive() is False


def test_gigastt_stream_client_needs_a_live_server(_сеть_закрыта):
    from websockets.sync.client import connect
    _route(_сеть_закрыта, "GET", daemon.GIGASTT_HEALTH)
    client = daemon.gigastt_stream_client()
    assert client.func is connect and client.keywords == {"proxy": None}

    def down(url, **kw):
        raise requests.ConnectionError("нет")

    _сеть_закрыта[("GET", daemon.GIGASTT_HEALTH)] = down
    with pytest.raises(daemon.GigasttUnavailable, match="не отвечает"):
        daemon.gigastt_stream_client()


def test_gigastt_stream_client_without_the_library(monkeypatch, _сеть_закрыта):
    _route(_сеть_закрыта, "GET", daemon.GIGASTT_HEALTH)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", None)
    with pytest.raises(daemon.GigasttUnavailable, match="websockets"):
        daemon.gigastt_stream_client()


def test_gigastt_websocket_address_is_loopback():
    assert daemon.GIGASTT_WS == "ws://127.0.0.1:9876/v1/ws"
    assert net.is_loopback_host("127.0.0.1")


def test_mlx_probe_path_and_direct(_сеть_закрыта):
    rec = _route(_сеть_закрыта, "POST", "http://127.0.0.1:8080/v1/chat/completions")
    cfg = {"llm": {"engine": "mlx-server", "mlx_base_url": "http://127.0.0.1:8080", "mlx_model": "m"}}
    llm_health.probe(cfg, timeout=5)
    (url, kw), = rec.calls
    assert url == "http://127.0.0.1:8080/v1/chat/completions" and kw["timeout"] == 5 and _direct(kw)


def test_cloud_probe_path_and_proxy_rule(monkeypatch, _сеть_закрыта):
    import llm
    import privacy
    rec = _route(_сеть_закрыта, "POST", "https://gw.example/v1/chat/completions")
    monkeypatch.setattr(privacy, "cloud_engine_active", lambda cfg: True)
    monkeypatch.setattr(privacy, "cloud_llm_url", lambda cfg: "https://gw.example/v1")
    monkeypatch.setattr(llm, "cloud_key", lambda cfg: "k")
    llm_health.probe({"llm": {"cloud_model": "cm"}}, timeout=5)
    (url, kw), = rec.calls
    assert url == "https://gw.example/v1/chat/completions" and kw["timeout"] == 5
    assert "proxies" not in kw, "внешний шлюз идёт по правилам прокси окружения"


def test_ollama_probe_path_and_direct(_сеть_закрыта):
    rec = _route(_сеть_закрыта, "POST", "http://127.0.0.1:11434/api/generate")
    cfg = {"llm": {"engine": "ollama", "base_url": "http://127.0.0.1:11434", "model": "m"}}
    llm_health.probe(cfg, timeout=5)
    (url, kw), = rec.calls
    assert url == "http://127.0.0.1:11434/api/generate" and _direct(kw)


def test_strict_json_probe_path_and_direct(_сеть_закрыта):
    rec = _route(_сеть_закрыта, "POST", "http://127.0.0.1:11434/api/chat")
    llm_health.strict_json("http://127.0.0.1:11434", "m", timeout=9)
    (url, kw), = rec.calls
    assert url == "http://127.0.0.1:11434/api/chat" and kw["timeout"] == 9 and _direct(kw)


# --- open_url: ветка для чужого адреса ---------------------------------------

def test_open_url_off_this_machine_uses_plain_urlopen(monkeypatch):
    sentinel = object()
    seen = {}

    def fake(request, timeout=None):
        seen["url"], seen["timeout"] = request, timeout
        return sentinel

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert net.open_url("https://example.com/x", timeout=7) is sentinel
    assert seen == {"url": "https://example.com/x", "timeout": 7}
