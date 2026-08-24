"""Клиент brain (:8100): тело запроса, folder из конфига, текст ответа.

Партия D-П3 (#402): три ручных POST в daemon слиты в src/brain.py; тест
держит контракт клиента живым HTTP-сервером, без моков requests.
"""
import http.server
import json
import pathlib
import sys
import threading

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import brain  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen = {"path": self.path, "body": body}
        payload = json.dumps({"text": "• Встречи/2026-08-21_1202.md\n  …ответ…"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *a):  # тишина в тестовом выводе
        pass


def test_vault_search_contract(tmp_path, monkeypatch):
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(brain, "BASE",
                            f"http://127.0.0.1:{srv.server_port}")
        graph = tmp_path / "Графы" / "проект"
        graph.mkdir(parents=True)
        cfg = {"sufler": {"graph_dir": str(graph)}}
        out = brain.vault_search(cfg, "что решили по релизу",
                                 limit=3, snippet_chars=700, timeout=5)
        assert out.startswith("• Встречи/")
        body = _Handler.seen["body"]
        assert _Handler.seen["path"] == "/vault_search"
        assert body == {"query": "что решили по релизу", "limit": 3,
                        "folder": "проект", "snippet_chars": 700}
    finally:
        srv.shutdown()


def test_network_failure_propagates(monkeypatch):
    # Сбой — исключением: деградация у каждого контура своя (узлы графа,
    # молчание, пустая память) — клиент её не выбирает за вызывающего.
    monkeypatch.setattr(brain, "BASE", "http://127.0.0.1:1")
    import pytest, requests
    with pytest.raises(requests.exceptions.RequestException):
        brain.vault_search({}, "вопрос", limit=1, snippet_chars=100,
                           timeout=0.3)
