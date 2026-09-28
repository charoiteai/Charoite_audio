"""Вид отказа двери модели — значением у места отказа, строка демона — по виду (№454).

Причина отказа — поле `LLMHTTPError.kind`, его ставит место отказа (`_fail`), а не
читатель по числу или тексту: дверь сама делает 503 из «облако недоступно», и по
статусу его не отличить от очереди сервера. Здесь три слоя:

- каталог путей отказа: каждое место `_fail` в клиенте провоцируется настоящим путём
  (ответ транспорта, оборванный поток, молчание шлюза), и вид сверяется с таблицей;
  перепись вызовов `_fail` по AST держит таблицу полной;
- `failure_kind` — вид любого исключения по типу;
- `daemon.short_error` — таблица «вид → фраза» без подстрок.
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import sys
from collections import Counter

import pytest
import requests

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm  # noqa: E402

LOCAL = {
    "llm": {"model": "тест-модель", "small_model": "тест-мелкая", "num_ctx": 8192, "temperature": 0.4},
    "sufler": {"role": "тестовая роль", "embed_model": "тест-эмбеддер"},
}


def _cloud(tmp_path, *, fallback_local: bool) -> llm.LLM:
    key = tmp_path / "llm_key"
    key.write_text("secret-key\n", encoding="utf-8")
    return llm.LLM({
        "llm": {"engine": "cloud", "model": "local:4b", "cloud_base_url": "https://gw.example.com/v1",
                "cloud_model": "cloud-model", "cloud_key_file": str(key),
                "cloud_fallback_local": fallback_local},
        "sufler": {"cloud_engine": True, "role": "ты помощник"},
    })


class _Resp:
    """Ответ транспорта, каким его видит дверь: статус, строки потока, тело."""

    def __init__(self, status: int = 200, lines=(), text: str = ""):
        self.status_code = status
        self._lines = list(lines)
        self.text = text

    def iter_lines(self):
        yield from self._lines

    def json(self):
        return json.loads(self.text)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stream_lines(monkeypatch, client: llm.LLM, lines) -> None:
    monkeypatch.setattr(client, "_open_stream", lambda *a, **kw: _Resp(200, lines))


def _raised(call) -> llm.LLMHTTPError:
    with pytest.raises(llm.LLMHTTPError) as err:
        call()
    return err.value


# ---------------------------------------------------------------- каталог путей отказа

def _p_stream_error(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b'{"error": "boom"}'])
    return _raised(lambda: list(c.stream("?")))


def _p_stream_unterminated(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b'{"message": {"content": "a"}}'])
    return _raised(lambda: list(c.stream("?")))


def _p_ndjson_form(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, ['{"message": "строкой"}'.encode()])
    return _raised(lambda: list(c.stream("?")))


def _p_json_not_json(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b"<html>"])
    return _raised(lambda: list(c.stream("?")))


def _p_json_not_object(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b"[1, 2]"])
    return _raised(lambda: list(c.stream("?")))


def _p_open_stream(status):
    def provoke(tmp_path, monkeypatch, сеть):
        # ответ сервера — маршрутом сторожа сети, а не подменой `llm.requests`:
        # весь `_open_stream` с его ретраями остаётся в пути
        def ответ(url, **k):
            r = requests.Response()
            r.status_code = status
            r._content = "отказ".encode("utf-8")
            return r

        сеть[("POST", "http://x/api/chat")] = ответ
        c = llm.LLM(LOCAL)
        return _raised(lambda: c._open_stream("http://x/api/chat", {}, 0.0))
    return provoke


def _p_messages_error(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b'{"error": "boom"}'])
    return _raised(lambda: list(c.stream_messages([{"role": "user", "content": "?"}])))


def _p_messages_unterminated(tmp_path, monkeypatch, сеть=None):
    c = llm.LLM(LOCAL)
    _stream_lines(monkeypatch, c, [b'{"message": {"content": "a"}}'])
    return _raised(lambda: list(c.stream_messages([{"role": "user", "content": "?"}])))


def _p_stream_cloud(tmp_path, monkeypatch, сеть=None):
    c = _cloud(tmp_path, fallback_local=False)

    def down(*a, **kw):
        raise requests.ConnectionError("нет сети")
        yield  # pragma: no cover — генератор

    monkeypatch.setattr(c, "_sse", down)
    return _raised(lambda: list(c._stream_cloud([{"role": "user", "content": "?"}], num_predict=None,
                                                temperature=None, busy_wait=0.0)))


def _p_sse(lines, **kw):
    def provoke(tmp_path, monkeypatch, сеть=None):
        c = _cloud(tmp_path, fallback_local=True)
        clock = {"now": 0.0}
        monkeypatch.setattr(llm.time, "monotonic", lambda: clock["now"])

        def ticking():
            for line in lines:
                clock["now"] += 10
                yield line

        monkeypatch.setattr(c, "_open_stream", lambda *a, **k: _Resp(200, ticking()))
        return _raised(lambda: list(c._sse("u", {}, 0.0, **kw)))
    return provoke


def _p_complete_cloud(tmp_path, monkeypatch, сеть=None):
    c = _cloud(tmp_path, fallback_local=False)

    def down(*a, **kw):
        raise requests.ConnectionError("нет сети")

    monkeypatch.setattr(c, "_post_busy", down)
    return _raised(lambda: c.complete("?"))


def _p_checked_body(status, text):
    def provoke(tmp_path, monkeypatch, сеть=None):
        c = llm.LLM(LOCAL)
        return _raised(lambda: c._checked_body(_Resp(status, text=text)))
    return provoke


#: (функция клиента, провокация, ожидаемый вид, начало причины). Строка на каждое
#: место `_fail`; у мест со статусом сервера — по строке на каждую ветку статуса.
CATALOG = [
    ("stream", _p_stream_error, "broken", "boom"),
    ("stream", _p_stream_unterminated, "broken", "стрим оборван"),
    ("_ndjson_chunk", _p_ndjson_form, "broken", "неожиданная форма"),
    ("_json_line", _p_json_not_json, "broken", "не-JSON"),
    ("_json_line", _p_json_not_object, "broken", "неожиданная форма"),
    ("_open_stream", _p_open_stream(404), "http", "отказ"),
    ("_open_stream", _p_open_stream(503), "queue", "отказ"),
    ("stream_messages", _p_messages_error, "broken", "boom"),
    ("stream_messages", _p_messages_unterminated, "broken", "стрим оборван"),
    ("_stream_cloud", _p_stream_cloud, "unavailable", "облако недоступно"),
    ("_sse", _p_sse([b""] * 5, first_token=30.0), "broken", "шлюз не отдал"),
    ("_sse", _p_sse([b'data: {"error": "boom"}']), "broken", "boom"),
    ("_sse", _p_sse([b"data: [1, 2]"]), "broken", "неожиданная форма"),
    ("_sse", _p_sse([b'data: {"choices": [{"delta": {"content": "a"}}]}']), "broken", "стрим оборван"),
    ("complete", _p_complete_cloud, "unavailable", "облако недоступно"),
    ("_checked_body", _p_checked_body(500, "сервер упал"), "http", "сервер упал"),
    ("_checked_body", _p_checked_body(429, "лимит"), "queue", "лимит"),
    ("_checked_body", _p_checked_body(200, '{"error": "boom"}'), "broken", "boom"),
]


@pytest.mark.parametrize("функция, провокация, вид, причина", CATALOG,
                         ids=[f"{row[0]}-{i}" for i, row in enumerate(CATALOG)])
def test_каждый_путь_отказа_даёт_свой_вид(tmp_path, monkeypatch, _сеть_закрыта, функция, провокация, вид, причина):
    """Путь проходится до исключения: вид — тот, что в каталоге, причина — этого места."""
    err = провокация(tmp_path, monkeypatch, _сеть_закрыта)
    assert err.kind == вид, (функция, err.status, err.detail)
    assert причина in err.detail, (функция, err.detail)


def _fail_sites() -> Counter:
    """Вызовы `self._fail(` в классе клиента — по функции-владельцу."""
    tree = ast.parse(inspect.getsource(llm.LLM))
    sites: Counter = Counter()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_fail"
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"):
                    sites[fn.name] += 1
    return sites


def test_каталог_покрывает_каждое_место_отказа():
    """Новое место `_fail` без строки каталога — красное, строка без места — тоже:
    перепись по AST против каталога. У мест со статусом сервера строк больше, чем
    мест, — по ветке на статус; остальные — строка на место."""
    sites = _fail_sites()
    rows = Counter(row[0] for row in CATALOG)
    per_status = {"_open_stream": 1, "_checked_body": 1}   # у _checked_body второе место — поле error
    expected_rows = Counter({fn: n + per_status.get(fn, 0) for fn, n in sites.items()})
    assert rows == expected_rows, (dict(sites), dict(rows))
    assert sum(sites.values()) == 16


def test_явный_вид_только_у_синтетических_отказов():
    """Статус не отражает вид только у двух мест — синтетических 503 «облако
    недоступно»; у остальных вид выводится из статуса, и явный `kind=` там — ложь."""
    tree = ast.parse(inspect.getsource(llm.LLM))
    explicit = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "_fail":
            for kw in n.keywords:
                if kw.arg == "kind":
                    explicit.append(ast.literal_eval(kw.value))
    assert explicit == ["unavailable", "unavailable"]


# ---------------------------------------------------------------- вид по статусу и по типу

@pytest.mark.parametrize("статус, вид", [
    (429, "queue"), (502, "queue"), (503, "queue"), (200, "broken"), (404, "http"), (500, "http"),
])
def test_вид_по_статусу(статус, вид):
    assert llm.status_kind(статус) == вид
    assert llm.LLMHTTPError(статус, "x").kind == вид


def test_вид_вне_словаря_отказ():
    with pytest.raises(ValueError, match="вне словаря"):
        llm.LLMHTTPError(503, "x", kind="занято")
    assert llm.LLMHTTPError(503, "x", kind="unavailable").kind == "unavailable"


def test_занятость_читается_у_двери_на_вызове(monkeypatch):
    """Подмена правила у двери меняет и ответ функции, и вид из статуса."""
    monkeypatch.setattr(llm, "BUSY_STATUSES", frozenset({500}))
    assert llm.is_busy_status(500) and not llm.is_busy_status(503)
    assert llm.status_kind(500) == "queue" and llm.status_kind(503) == "http"


@pytest.mark.parametrize("исключение, вид", [
    (llm.LLMHTTPError(503, "x"), "queue"),
    (llm.LLMHTTPError(503, "x", kind="unavailable"), "unavailable"),
    (requests.ConnectionError("x"), "unreachable"),
    (requests.ConnectTimeout("x"), "unreachable"),
    (requests.ReadTimeout("x"), "timeout"),
    (requests.HTTPError("503 Server Error"), "other"),
    (ConnectionRefusedError("диск"), "other"),
    (RuntimeError("upstream 503"), "other"),
], ids=["статус-очереди", "синтетический", "соединение", "таймаут-соединения", "таймаут-чтения",
        "чужой-http", "отказ-диска", "текст-с-числом"])
def test_вид_исключения_по_типу(исключение, вид):
    """По типу, без текста: «503» в чужой ошибке не делает её очередью, отказ
    соединения с диском — не транспорт модели."""
    assert llm.failure_kind(исключение) == вид


# ---------------------------------------------------------------- строка демона

def _daemon():
    import daemon
    return daemon


@pytest.mark.parametrize("исключение, строка", [
    (llm.LLMHTTPError(503, "x"), "модель занята"),
    (llm.LLMHTTPError(503, "облако недоступно (x), локальный запас выключен", kind="unavailable"),
     "облако недоступно"),
    (requests.ConnectionError("x"), "сервер модели не отвечает"),
    (requests.ReadTimeout("x"), "модель не ответила вовремя"),
], ids=["очередь", "облако", "соединение", "таймаут"])
def test_short_error_фраза_по_виду(исключение, строка):
    assert _daemon().short_error(исключение) == строка


@pytest.mark.parametrize("исключение", [
    llm.LLMHTTPError(500, "upstream 502 bad gateway"),
    llm.LLMHTTPError(200, "стрим оборван без завершения"),
    ConnectionRefusedError("диск iCloud"),
    ConnectionResetError("том"),
    RuntimeError("порт 5030"),
], ids=["500-с-числом-в-причине", "битый-ответ", "отказ-диска", "сброс-тома", "число-в-тексте"])
def test_short_error_прочее_строкой_с_типом(исключение):
    """Без фразы — тип и начало текста, как раньше; ни «занята», ни «не отвечает».
    Один `except` минуток (`daemon.py`, модель и диск разом) даёт по источнику
    разные строки: транспорт модели — фразой, `OSError` диска — строкой с типом."""
    s = _daemon().short_error(исключение)
    assert s.startswith(type(исключение).__name__ + ": "), s
    assert s not in _daemon().SHORT_ERROR_PHRASES.values()


def test_short_error_не_ищет_подстрок():
    """Строку решает вид, а не текст: в теле функции нет ни одного `in` — ни по тексту
    ошибки, ни по имени типа, и дешёвый приём «дописать подстроку» сюда не вернётся.
    Имя типа остаётся только в строке ошибки без фразы."""
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(_daemon())))
              if isinstance(n, ast.FunctionDef) and n.name == "short_error")
    compares = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)
                and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops)]
    assert compares == []


# ---------------------------------------------------------------- читатели отказа

REPO = SRC.parent

#: Кто ловит `LLMHTTPError` — (файл, функция) и что берёт из отказа. Новый перехват
#: без строки здесь — красный: решение «читать вид или статус» принимается при ревью,
#: а не молча (входной круг 4 по №454, «Как чинить»).
READERS = {
    ("src/graph_updater.py", "_extract"): "вид: очередь и недоступность — «повтор подберёт встречу»",
    ("src/llm.py", "_stream_cloud"): "статус: 4xx шлюза запасом не лечится — решение самой двери",
    ("src/llm.py", "warmup"): "статус: текст про ключ и имя модели",
    ("src/llm.py", "complete"): "статус и тело: запас облака и дверь строгого JSON",
    ("src/mcp_server.py", "sufler_make_minutes"): "статус и причина — в тексте отказа",
    ("src/retro_fill.py", "gen"): "строка ошибки целиком",
}


def _readers() -> set[tuple[str, str]]:
    found = set()
    for path in sorted([*(REPO / "src").rglob("*.py"), *(REPO / "scripts").glob("*.py")]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.ExceptHandler) and n.type is not None:
                    types_ = n.type.elts if isinstance(n.type, ast.Tuple) else [n.type]
                    if any(ast.unparse(t).endswith("LLMHTTPError") for t in types_):
                        found.add((path.relative_to(REPO).as_posix(), fn.name))
    return found


def test_перечень_читателей_отказа_совпадает_с_кодом():
    """Перехваты `LLMHTTPError` по всему коду — против перечня в обе стороны: новый
    читатель без строки и строка без читателя — красные."""
    assert _readers() == set(READERS)


@pytest.mark.parametrize("исключение, позже", [
    (llm.LLMHTTPError(503, "x"), True),
    (llm.LLMHTTPError(503, "x", kind="unavailable"), True),
    (llm.LLMHTTPError(404, "x"), False),
    (llm.LLMHTTPError(200, "x"), False),
    (requests.ConnectionError("x"), False),
    (RuntimeError("503"), False),
], ids=["очередь", "облако", "404", "битый", "соединение", "чужое"])
def test_повторить_позже_решает_дверь(исключение, позже):
    """«Повтор подберёт» — очередь и недоступное облако; решает дверь одним
    предикатом, читатель не держит свой кортеж видов."""
    assert llm.is_retry_later(исключение) is позже
    assert llm.RETRY_LATER_KINDS <= set(llm.FAILURE_KINDS)
