"""№525: адрес на этой машине — только напрямую, мимо прокси окружения и системы.

Три слоя. Поведенческий: подставной прокси в окружении и настоящий сервер на
127.0.0.1 — сервер получил запрос, у прокси ноль соединений (и положительный
контроль: тот же вызов без правила прокси зовёт). Структурный: каждый вызов
`requests`, `urlopen`, websocket-`connect` в `src/**` и `scripts/**` идёт через
`proxies_for` / `open_url` / `proxy=None`. Swift: каждая сессия с
`connectionProxyDictionary = [:]` в той же функции либо в списке внешних.
"""
from __future__ import annotations

import ast
import http.server
import json
import pathlib
import re
import socket
import sys
import threading
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from charoite_graph import embed_door  # noqa: E402
from charoite_graph.net import is_loopback_host, open_url  # noqa: E402
import graph_updater  # noqa: E402
import llm  # noqa: E402
import llm_health  # noqa: E402
import privacy  # noqa: E402

PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")

pytestmark = pytest.mark.сеть_разрешена


@pytest.fixture(autouse=True)
def _ollama_маршруты():
    """Здесь сеть настоящая — нужен настоящий `LLM` без маршрутов сторожа (он отказывает под открытой сетью)."""
    yield None


# --- слой 1: правило адреса -------------------------------------------------

@pytest.mark.parametrize("host,want", [
    ("127.0.0.1", True), ("127.9.9.9", True), ("::1", True), ("localhost", True),
    ("LocalHost", True), ("localhost.", True), ("", False), (None, False),
    ("example.com", False), ("192.168.1.5", False), ("127.0.0.1.evil.com", False),
    ("localhost.evil.com", False),
])
def test_loopback_host_rule(host, want):
    assert is_loopback_host(host) is want


def test_proxies_for_only_switches_the_proxy_off_for_loopback():
    assert privacy.proxies_for("http://127.0.0.1:11434/api/chat") == {
        "proxies": {"http": None, "https": None, "all": None}, "allow_redirects": False}
    assert privacy.proxies_for("http://[::1]:8100/x")["allow_redirects"] is False
    assert privacy.proxies_for("https://api.example.com/v1") == {}
    assert privacy.proxies_for("http://192.168.0.7:11434") == {}


# --- слой 2: опыт с прокси и сервером ---------------------------------------

class _Proxy:
    """Слушающий сокет, который считает соединения: настоящий прокси не нужен."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.hits = 0
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _run(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.hits += 1
            conn.close()

    def close(self):
        self._stop = True
        self._t.join(2)
        self.sock.close()


class _Server:
    def __init__(self):
        outer = self
        self.requests: list[tuple[str, str]] = []

        class H(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                outer.requests.append((self.command, self.path))
                body = json.dumps({"ok": True, "embeddings": [[1.0]], "models": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def stand(monkeypatch):
    proxy, server = _Proxy(), _Server()
    for var in PROXY_VARS:
        monkeypatch.setenv(var, proxy.url)
    for var in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    yield proxy, server
    proxy.close()
    server.close()


def _cfg(base: str) -> dict:
    return {"llm": {"engine": "ollama", "base_url": base, "model": "тест-модель",
                    "small_model": "тест-мелкая", "num_ctx": 8192, "temperature": 0.4},
            "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"}}


def test_control_without_the_rule_the_request_reaches_the_proxy(stand):
    """Положительный контроль: подставной прокси настоящий, без правила запрос идёт к нему."""
    import requests
    proxy, server = stand
    try:
        requests.get(server.url + "/x", timeout=2)
    except requests.RequestException:
        pass
    assert proxy.hits >= 1 and server.requests == []


def test_control_urllib_without_the_rule_reaches_the_proxy(stand):
    proxy, server = stand
    try:
        urllib.request.urlopen(server.url + "/x", timeout=2)  # noqa: S310 — опыт с подставным прокси
    except OSError:
        pass
    assert proxy.hits >= 1 and server.requests == []


def _direct(stand):
    proxy, server = stand
    return proxy, server


def test_llm_embed_adapter_goes_direct(stand):
    proxy, server = stand
    assert llm._requests_post(server.url + "/api/embed", {"input": ["т"]}, 5)[0] == 200
    assert server.requests and proxy.hits == 0


def test_llm_models_list_goes_direct(stand):
    proxy, server = stand
    llm.LLM(_cfg(server.url))._models_available()
    assert ("GET", "/api/tags") in server.requests and proxy.hits == 0


def test_llm_complete_goes_direct(stand):
    proxy, server = stand
    try:
        llm.LLM(_cfg(server.url)).complete("в", model="м")
    except Exception:  # noqa: BLE001 — форма ответа сервера не важна: важно, куда ушёл запрос
        pass
    assert any(m == "POST" for m, _ in server.requests) and proxy.hits == 0


def test_llm_stream_goes_direct(stand):
    proxy, server = stand
    try:
        list(llm.LLM(_cfg(server.url)).stream("в", model="м"))
    except Exception:  # noqa: BLE001
        pass
    assert any(m == "POST" for m, _ in server.requests) and proxy.hits == 0


def test_health_probe_goes_direct(stand):
    proxy, server = stand
    llm_health.probe(_cfg(server.url), timeout=3)
    assert ("POST", "/api/generate") in server.requests and proxy.hits == 0


def test_strict_json_probe_goes_direct(stand):
    proxy, server = stand
    llm_health.strict_json(server.url, "м", timeout=3)
    assert ("POST", "/api/chat") in server.requests and proxy.hits == 0


def test_door_stdlib_transport_goes_direct(stand):
    proxy, server = stand
    assert embed_door.urllib_post(server.url + "/api/embed", {"input": ["т"]}, 5)[0] == 200
    assert server.requests and proxy.hits == 0


def test_open_url_goes_direct_and_refuses_an_off_host_redirect(stand):
    import http.server as hs
    proxy, server = stand
    with open_url(server.url + "/api/tags", timeout=3) as r:
        assert r.status == 200
    assert proxy.hits == 0

    class Redirect(hs.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = hs.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError):
            open_url(f"http://127.0.0.1:{httpd.server_address[1]}/", timeout=3)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert proxy.hits == 0


def test_memory_writer_default_post_goes_direct(stand):
    proxy, server = stand
    graph_updater._post_direct(server.url + "/forget", json={"meeting": "x"}, timeout=3)
    assert ("POST", "/forget") in server.requests and proxy.hits == 0


def test_the_audio_websocket_goes_direct(stand):
    """Звук быстрого триггера — websocket: `proxy=None` у клиента, иначе websockets 15+ берёт прокси окружения."""
    from websockets.sync.client import connect
    from websockets.sync.server import serve
    proxy, _ = stand
    got = []

    def handler(ws):
        got.append(ws.recv())

    with serve(handler, "127.0.0.1", 0) as srv:
        port = srv.socket.getsockname()[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with connect(f"ws://127.0.0.1:{port}/v1/ws", proxy=None) as ws:
            ws.send("кадр")
        srv.shutdown()
    assert got == ["кадр"] and proxy.hits == 0
    # контроль: без proxy=None клиент идёт к прокси окружения
    with serve(handler, "127.0.0.1", 0) as srv2:
        port2 = srv2.socket.getsockname()[1]
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            with connect(f"ws://127.0.0.1:{port2}/v1/ws", open_timeout=2):
                pass
        except Exception:  # noqa: BLE001 — прокси-заглушка рвёт соединение
            pass
        srv2.shutdown()
    assert proxy.hits >= 1, "контроль: без proxy=None websocket уходит на прокси окружения"


# --- слой 3: структурный сторож ---------------------------------------------

#: Внешний адрес константой — единственная законная причина ходить не через правило.
EXTERNAL = {
    "scripts/get_models.py": "скачивание весов с внешнего адреса (константа), не локальная модель",
}
REQUESTS_VERBS = {"get", "post", "put", "delete", "patch", "head", "request"}


def _py_files():
    for root in ("src", "scripts"):
        for p in sorted((REPO / root).rglob("*.py")):
            yield p


def _has_proxies_for(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr == "proxies_for")
        or (isinstance(n.func, ast.Name) and n.func.id == "proxies_for"))
        for n in ast.walk(node))


def transport_violations(rel: str, tree: ast.Module) -> list[str]:
    """Вызовы транспорта в файле мимо правила «loopback — напрямую»."""
    requests_names, ws_names = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "requests":
                    requests_names.add(a.asname or "requests")
                if a.name.split(".")[0] == "websockets":
                    ws_names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[0] == "websockets":
                for a in n.names:
                    if a.name == "connect":
                        ws_names.add(a.asname or a.name)
    out = []
    net_module = rel == "src/charoite_graph/net.py"
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        where = f"{rel}:{n.lineno}"
        attr = f.attr if isinstance(f, ast.Attribute) else None
        name = f.id if isinstance(f, ast.Name) else None
        root = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None
        if root in requests_names and attr == "Session":
            out.append(f"{where}: requests.Session — мимо правила")
        elif root in requests_names and attr in REQUESTS_VERBS:
            if not any(k.arg is None and _has_proxies_for(k.value) for k in n.keywords):
                out.append(f"{where}: requests.{attr} без **proxies_for(url)")
        elif attr == "urlopen" or name == "urlopen":
            if not net_module and rel not in EXTERNAL:
                out.append(f"{where}: urlopen мимо net.open_url")
        elif attr in {"build_opener", "install_opener"} or name in {"build_opener", "OpenerDirector"}:
            if not net_module:
                out.append(f"{where}: свой opener мимо net.open_url")
        elif (name in ws_names and name != "websockets") or (
                attr == "connect" and root in ws_names) or (
                attr == "connect" and isinstance(f.value, ast.Attribute)
                and _root_name(f.value) in ws_names):
            if not any(k.arg == "proxy" and isinstance(k.value, ast.Constant) and k.value.value is None
                       for k in n.keywords):
                out.append(f"{where}: websocket connect без proxy=None")
    return out


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def test_every_transport_call_in_src_and_scripts_goes_through_the_rule():
    bad = []
    for p in _py_files():
        rel = p.relative_to(REPO).as_posix()
        bad += transport_violations(rel, ast.parse(p.read_text(encoding="utf-8")))
    assert bad == [], "вызов транспорта мимо proxies_for / open_url / proxy=None:\n" + "\n".join(bad)


@pytest.mark.parametrize("src,want", [
    ("import requests\nrequests.post(u, json=1)\n", 1),
    ("import requests\nrequests.post(u, **privacy.proxies_for(u))\n", 0),
    ("import requests as _rq\n_rq.get(u, timeout=2, **proxies_for(u))\n", 0),
    ("import requests as _rq\n_rq.get(u)\n", 1),
    ("import requests\nrequests.Session()\n", 1),
    ("import urllib.request\nurllib.request.urlopen(u)\n", 1),
    ("from urllib.request import urlopen\nurlopen(u)\n", 1),
    ("import urllib.request\nurllib.request.build_opener()\n", 1),
    ("from websockets.sync.client import connect\nconnect(u)\n", 1),
    ("from websockets.sync.client import connect as c\nc(u, proxy=None)\n", 0),
    ("from websockets.sync.client import connect as c\nc(u, proxy=True)\n", 1),
    ("import websockets\nwebsockets.connect(u)\n", 1),
    ("import websockets.sync.client as w\nw.connect(u, proxy=None)\n", 0),
])
def test_the_transport_guard_sees_each_form(src, want):
    assert len(transport_violations("src/x.py", ast.parse(src))) == want


def test_the_guard_exception_list_is_real():
    for rel in EXTERNAL:
        assert (REPO / rel).is_file() and "urlopen" in (REPO / rel).read_text(encoding="utf-8"), rel


# --- Swift ------------------------------------------------------------------

#: Сессии на внешние адреса (GitHub): прокси системы там законен.
SWIFT_EXTERNAL = {
    "app/Sources/CharoiteApp/Services/UpdateService.swift": "релизы GitHub — внешний адрес",
    "app/Sources/CharoiteApp/Services/VersionStatusService.swift": "номер выпуска на GitHub — внешний адрес",
}
_SESSION = re.compile(r"URLSession\(|URLSession\.shared")
#: Объявление: `func`/`init`, либо свойство с модификатором (`private static let x = {…}()`);
#: локальный `let` без модификатора — не граница функции.
_DECL = re.compile(r"^\s*(?:@\w+\s+)*(?:(?:private|fileprivate|internal|public|static|final|nonisolated|override|lazy)\s+)*"
                   r"(?:(?:func|init)\b|(?:(?:private|fileprivate|internal|public|static|lazy)\s+)+(?:var|let)\b)")


def swift_session_violations(rel: str, text: str) -> list[str]:
    """Сессии без `connectionProxyDictionary = [:]` в той же функции (или блоке объявления)."""
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        code = line.split("//", 1)[0]
        if not _SESSION.search(code):
            continue
        start = i
        while start > 0 and not _DECL.match(lines[start]):
            start -= 1
        end = i + 1
        while end < len(lines) and not _DECL.match(lines[end]):
            end += 1
        if "connectionProxyDictionary = [:]" not in "\n".join(lines[start:end]):
            out.append(f"{rel}:{i + 1}: сессия без connectionProxyDictionary = [:] в той же функции")
    return out


def test_every_swift_session_is_direct_or_listed_as_external():
    bad = []
    for root in ("app/Sources", "app/Probes"):
        for p in sorted((REPO / root).rglob("*.swift")):
            rel = p.relative_to(REPO).as_posix()
            if rel in SWIFT_EXTERNAL:
                continue
            bad += swift_session_violations(rel, p.read_text(encoding="utf-8"))
    assert bad == [], "\n".join(bad)


def test_the_swift_external_list_is_real():
    for rel in SWIFT_EXTERNAL:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert _SESSION.search(text), rel
        assert "github" in text.lower(), rel


@pytest.mark.parametrize("src,want", [
    ("func a() {\n    let s = URLSession(configuration: cfg)\n}\n", 1),
    ("func a() {\n    cfg.connectionProxyDictionary = [:]\n    let s = URLSession(configuration: cfg)\n}\n", 0),
    ("func a() {\n    cfg.connectionProxyDictionary = [:]\n}\nfunc b() {\n    URLSession.shared.data(from: u)\n}\n", 1),
    ("private static let s: URLSession = {\n  cfg.connectionProxyDictionary = [:]\n  return URLSession(configuration: cfg)\n}()\n", 0),
    ("func a() {\n    // URLSession.shared в комментарии\n}\n", 0),
])
def test_the_swift_guard_sees_each_form(src, want):
    assert len(swift_session_violations("x.swift", src)) == want
