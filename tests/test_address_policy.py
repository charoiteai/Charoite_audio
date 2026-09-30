"""Политика адреса модели — одна функция на пакет и приложение (№522).

`charoite_graph.address_policy.guard_model_url` решает, можно ли слать тексты на
адрес; `privacy._guarded_url` переводит конфиг и рубильник в её аргументы, а
отказ — в свой текст; фабрика пакета `embed_door.ollama_embedder` зовёт её же.
Здесь закреплены: таблица вердиктов, тексты приложения байт в байт (как до
переезда), сторож «вердикт политики проходит через privacy как есть» и запрет
приложению звать фабрику пакета — у неё нет рубильника.
"""
from __future__ import annotations

import pathlib
import ast
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import privacy  # noqa: E402
import charoite_graph.address_policy as address_policy  # noqa: E402
from charoite_graph import cli, embed_door  # noqa: E402
from charoite_graph.address_policy import AddressRefused, guard_model_url  # noqa: E402


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """Резолв имён — только подменой: имя не резолвится, пока тест не скажет иначе."""
    def getaddrinfo(host, *a, **k):
        raise OSError("нет такого имени")
    monkeypatch.setattr(address_policy.socket, "getaddrinfo", getaddrinfo)
    address_policy._resolves_private.cache_clear()
    yield
    address_policy._resolves_private.cache_clear()


# (адрес, allow_remote, offline) → вид отказа или None (адрес проходит)
TABLE = [
    ("http://127.0.0.1:11434", False, False, None),
    ("https://127.0.0.1:11434", False, False, None),
    ("http://localhost:11434/", False, False, None),
    ("http://LOCALHOST.:11434", False, False, None),
    ("http://[::1]:11434", False, False, None),
    ("http://127.0.0.1:11434", False, True, None),           # рубильник не трогает эту машину
    ("http://127.0.0.1@evil.example:11434", False, False, "ambiguous"),
    ("http://evil.example\\@127.0.0.1:11434", True, False, "ambiguous"),
    ("http://[::1", False, False, "ambiguous"),
    ("file:///etc/passwd", True, False, "scheme"),
    ("ftp://127.0.0.1:1", True, False, "scheme"),           # схема — и для loopback
    ("127.0.0.1:11434", False, False, "scheme"),
    ("http://0.0.0.0:11434", False, False, "remote"),        # 0.0.0.0 — не loopback
    ("http://0.0.0.0:11434", True, False, None),             # но «частный» по ipaddress: http своей сети
    ("https://api.example.com", False, False, "remote"),
    ("https://api.example.com", True, False, None),
    ("https://api.example.com", "true", False, "remote"),     # строка — не разрешение
    ("https://api.example.com", 1, False, "remote"),
    ("https://api.example.com", True, True, "offline"),       # рубильник сильнее разрешения
    ("http://8.8.8.8:11434", True, False, "cleartext"),       # открытый http наружу
    ("http://llm.example.com:11434", True, False, "cleartext"),
    ("http://192.168.1.20:11434", False, False, "remote"),
    ("http://192.168.1.20:11434", True, False, None),
    ("http://ollama.local:11434", True, False, "cleartext"),  # имя не резолвится — не своя сеть
    ("http://чужой-хост:11434", False, False, "cleartext"),
    ("http://[::ffff:8.8.8.8]:11434", True, False, "cleartext"),     # IPv4 в одежде IPv6 — не своя сеть
    ("http://[::ffff:192.168.1.2]:11434", True, False, None),
]


@pytest.mark.parametrize("url, allow, offline, kind", TABLE)
def test_verdicts(url, allow, offline, kind):
    if kind is None:
        assert guard_model_url(url, allow_remote=allow, offline=offline) == url.rstrip("/")
    else:
        with pytest.raises(AddressRefused) as e:
            guard_model_url(url, allow_remote=allow, offline=offline)
        assert e.value.kind == kind and isinstance(e.value, ValueError)


def test_a_home_name_is_own_network_only_when_it_resolves_there(monkeypatch):
    """Тот же резолвящий предикат, что был у приложения: пакет не держит второй политики."""
    for ip, ok in (("192.168.1.7", True), ("8.8.8.8", False)):
        monkeypatch.setattr(address_policy.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))])
        address_policy._resolves_private.cache_clear()
        for url in ("http://ollama.local:11434", "http://studio:11434"):
            if ok:
                assert guard_model_url(url, allow_remote=True) == url
            else:
                with pytest.raises(AddressRefused, match="https"):
                    guard_model_url(url, allow_remote=True)


def test_dns_is_asked_only_for_cleartext_to_a_name(monkeypatch):
    """https, отказ по рубильнику и «нет разрешения» по IP резолва не делают."""
    asked = []
    monkeypatch.setattr(address_policy.socket, "getaddrinfo", lambda host, *a, **k: asked.append(host) or [])
    for url, allow, offline in (("https://studio", True, False), ("http://studio", True, True),
                                ("http://192.168.1.2", False, False)):
        try:
            guard_model_url(url, allow_remote=allow, offline=offline)
        except AddressRefused:
            pass
    assert asked == []


def test_a_mapped_public_address_is_not_own_network_on_any_python():
    """Старый stdlib считал весь ::ffff:0:0/96 частным; предикат разворачивает адрес сам."""
    import ipaddress

    class OldStdlibMapped:          # так ::ffff:8.8.8.8 видел Python без делегирования (опыт: 3.9)
        ipv4_mapped = ipaddress.IPv4Address("8.8.8.8")
        is_private, is_link_local, is_loopback = True, False, False
    assert address_policy._ip_private(OldStdlibMapped()) is False


def test_remote_is_refused_by_default():
    """Умолчание `allow_remote` — «нет»: вызов без аргумента не разрешает чужой адрес."""
    for url in ("https://api.example.com", "http://192.168.1.20:11434"):
        with pytest.raises(AddressRefused) as e:
            guard_model_url(url)
        assert e.value.kind == "remote"


def test_no_host_is_not_own_network():
    for host in (None, ""):
        assert address_policy.is_private_host(host) is False
    with pytest.raises(AddressRefused):        # «http:///x» — authority пуста: не своя сеть
        guard_model_url("http:///api", allow_remote=True)


def test_own_network_answers_are_strict_booleans(monkeypatch):
    """Предикат отвечает `True`/`False`, а не «что-то ложное»: ответ уходит в условия
    потребителей, и `None` там однажды станет «не проверено»."""
    assert address_policy.is_private_host("llm.example.com") is False       # имя с точкой вне домашних
    assert address_policy.is_private_host("studio") is False                # не резолвится (autouse)
    assert address_policy._resolves_private("studio") is False


def test_a_resolver_answer_that_is_not_an_address_is_not_own_network(monkeypatch):
    monkeypatch.setattr(address_policy.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("не-адрес", 0))])
    address_policy._resolves_private.cache_clear()
    assert address_policy._resolves_private("studio") is False
    with pytest.raises(AddressRefused) as e:
        guard_model_url("http://studio:11434", allow_remote=True)
    assert e.value.kind == "cleartext"


def test_a_name_is_resolved_once_per_process(monkeypatch):
    """Кэш резолва: сборка векторизатора не ходит в DNS заново на каждый вызов."""
    asked = []

    def getaddrinfo(host, *a, **k):
        asked.append(host)
        return [(2, 1, 6, "", ("192.168.1.7", 0))]
    monkeypatch.setattr(address_policy.socket, "getaddrinfo", getaddrinfo)
    address_policy._resolves_private.cache_clear()
    for _ in range(3):
        guard_model_url("http://studio:11434", allow_remote=True)
    assert asked == ["studio"]


def test_unknown_kind_is_a_wiring_error():
    with pytest.raises(ValueError, match="неизвестный вид"):
        AddressRefused("другое", "http://x")


# ── Приложение: тексты владельца — как до переезда, байт в байт ─────────

TEXTS = [
    ({"base_url": "http://[::1"}, {}, "llm.base_url = http://[::1: Invalid IPv6 URL"),
    ({"base_url": "http://evil.example\\@127.0.0.1:11434"}, {},
     "llm.base_url = http://evil.example\\@127.0.0.1:11434: адрес 'http://evil.example\\\\@127.0.0.1:11434': "
     "authority вне белой грамматики (имя или IPv6 в скобках, порт цифрами)"),
    ({"base_url": "ftp://127.0.0.1:1"}, {}, "llm.base_url = ftp://127.0.0.1:1: схема «ftp» не поддерживается, нужен http(s)"),
    ({"base_url": "127.0.0.1:11434"}, {}, "llm.base_url = 127.0.0.1:11434: схема «—» не поддерживается, нужен http(s)"),
    ({"base_url": "http://192.168.1.2:11434", "allow_remote": True}, {"CHAROITE_NO_CLOUD": "1", "SUFLER_NO_CLOUD": "1"},
     "llm.base_url = http://192.168.1.2:11434 указывает не на эту машину, а рубильник "
     "CHAROITE_NO_CLOUD/SUFLER_NO_CLOUD запрещает любой выход наружу"),
    ({"base_url": "http://192.168.1.2:11434", "allow_remote": True}, {"SUFLER_NO_CLOUD": "1"},
     "llm.base_url = http://192.168.1.2:11434 указывает не на эту машину, а рубильник "
     "SUFLER_NO_CLOUD запрещает любой выход наружу"),
    ({"base_url": "http://8.8.8.8:11434", "allow_remote": True}, {},
     "llm.base_url = http://8.8.8.8:11434 — адрес вне своей сети по открытому http: стенограмма ушла бы по сети "
     "открытым текстом. Для удалённого адреса нужен https (llm.allow_remote этого не снимает)"),
    ({"base_url": "https://api.example.com/"}, {},
     "llm.base_url = https://api.example.com/ указывает не на эту машину. Чароит локальный по умолчанию: чтобы слать "
     "запросы на другой адрес, поставьте в config.yaml явное llm.allow_remote: true"),
    ({"mlx_base_url": "https://api.example.com"}, {},
     "llm.mlx_base_url = https://api.example.com указывает не на эту машину. Чароит локальный по умолчанию: чтобы "
     "слать запросы на другой адрес, поставьте в config.yaml явное llm.allow_remote: true"),
]


@pytest.mark.parametrize("llm, env, text", TEXTS)
def test_app_refusals_keep_their_words(llm, env, text):
    read = privacy.mlx_base_url if "mlx_base_url" in llm else privacy.llm_base_url
    with pytest.raises(privacy.PrivacyRefused) as e:
        read({"llm": llm}, env)
    assert str(e.value) == text


# ── Сторож: решает только политика пакета ───────────────────────────────

APP_CASES = [
    ({"base_url": "ftp://127.0.0.1:1"}, {}),
    ({"base_url": "http://8.8.8.8:11434", "allow_remote": True}, {}),
    ({"base_url": "http://192.168.1.2:11434"}, {"CHAROITE_NO_CLOUD": "1"}),
    ({"base_url": "https://api.example.com", "allow_remote": "true"}, {}),
    ({"base_url": "http://[::1"}, {}),
    ({"mlx_base_url": "file:///x", "allow_remote": True}, {"SUFLER_NO_CLOUD": "1"}),
    ({}, {}),
    ({"base_url": "http://192.168.1.2:11434"}, {"CHAROITE_NO_CLOUD": ""}),     # пустая — не взведён
]


@pytest.mark.parametrize("llm, env", APP_CASES)
def test_privacy_passes_the_policy_verdict_through_as_is(monkeypatch, llm, env):
    """Подменённая политика пропускает всё — privacy обязана вернуть её ответ как есть.

    Своя ветка решения в `_guarded_url` (копия проверки схемы, рубильника,
    открытого http) превратила бы хоть один из этих случаев в отказ.
    """
    seen = []

    def fake(url, *, allow_remote=False, offline=False):
        seen.append((url, allow_remote, offline))
        return "http://решила-политика"
    monkeypatch.setattr(address_policy, "guard_model_url", fake)
    read = privacy.mlx_base_url if "mlx_base_url" in llm else privacy.llm_base_url
    default = privacy.DEFAULT_MLX_URL if read is privacy.mlx_base_url else privacy.DEFAULT_LLM_URL

    assert read({"llm": llm}, env) == "http://решила-политика"
    key = "mlx_base_url" if read is privacy.mlx_base_url else "base_url"
    armed = any(env.get(k) for k in privacy.KILL_SWITCHES)
    assert seen == [(llm.get(key) or default, llm.get("allow_remote"), armed)]


@pytest.mark.parametrize("kind", address_policy.KINDS)
def test_every_policy_refusal_becomes_privacy_refused(monkeypatch, kind):
    def fake(url, **k):
        raise AddressRefused(kind, url, scheme="gopher", detail="разбор")
    monkeypatch.setattr(address_policy, "guard_model_url", fake)
    with pytest.raises(privacy.PrivacyRefused) as e:
        privacy.llm_base_url({"llm": {"base_url": "http://x:1"}}, {"CHAROITE_NO_CLOUD": "1"})
    assert str(e.value).startswith("llm.base_url = http://x:1")


def test_one_default_address():
    assert privacy.DEFAULT_LLM_URL is address_policy.DEFAULT_OLLAMA_URL


# ── Фабрика пакета ──────────────────────────────────────────────────────

def _post(seen):
    def post(url, payload, timeout):
        seen.append((url, payload))
        return 200, '{"embeddings": [[1.0, 2.0]]}'
    return post


def test_factory_builds_on_this_machine_without_keep_alive_by_default():
    seen = []
    e = embed_door.ollama_embedder("bge-m3", post=_post(seen))
    assert e.model == "bge-m3" and e.run(["т"], 5) == [[1.0, 2.0]]
    url, payload = seen[0]
    assert url == address_policy.DEFAULT_OLLAMA_URL + "/api/embed" and "keep_alive" not in payload


@pytest.mark.parametrize("url", ["http://чужой-хост:11434", "https://api.example.com", "http://192.168.1.2:11434"])
def test_factory_refuses_another_machine_without_permission(url):
    seen = []
    with pytest.raises(AddressRefused):
        embed_door.ollama_embedder("m", url=url, post=_post(seen))
    assert seen == []


def test_factory_goes_to_another_machine_only_when_allowed():
    seen = []
    e = embed_door.ollama_embedder("m", url="https://api.example.com/", allow_remote=True,
                                   keep_alive="10m", post=_post(seen))
    e.run(["т"], 5)
    assert seen[0][0] == "https://api.example.com/api/embed" and seen[0][1]["keep_alive"] == "10m"


def test_factory_asks_the_one_policy(monkeypatch):
    monkeypatch.setattr(address_policy, "guard_model_url", lambda url, **k: "http://решила-политика")
    seen = []
    embed_door.ollama_embedder("m", url="ftp://куда-угодно", post=_post(seen)).run(["т"], 5)
    assert seen[0][0] == "http://решила-политика/api/embed"


# ── Командная строка ────────────────────────────────────────────────────

def _search(tmp_path, *flags):
    graph = tmp_path / "граф"
    graph.mkdir(exist_ok=True)
    (graph / "a.md").write_text("текст\n", encoding="utf-8")
    return cli.main(["search", str(graph), "q", "--model", "м", "--data-dir", str(tmp_path / "к"), *flags])


def test_cli_refuses_another_machine_and_names_the_flag(tmp_path, capsys):
    code = _search(tmp_path, "--model-url", "https://api.example.com")
    err = capsys.readouterr().err
    expected = f"charoite-graph: {AddressRefused('remote', 'https://api.example.com')} — разрешите флагом --allow-remote"
    assert code == cli.EXIT_USAGE and err.strip() == expected
    assert "allow_remote" not in err.replace("--allow-remote", ""), "слово библиотеки вместо флага команды"
    assert not (tmp_path / "к").exists()


def test_cli_cleartext_is_refused_even_with_the_flag(tmp_path, capsys):
    code = _search(tmp_path, "--model-url", "http://чужой-хост:11434", "--allow-remote")
    err = capsys.readouterr().err
    assert code == cli.EXIT_USAGE and "https" in err and "--allow-remote этого не снимает" in err
    assert "allow_remote" not in err.replace("--allow-remote", ""), "слово библиотеки вместо флага команды"


def test_cli_flag_lets_the_factory_through(tmp_path, monkeypatch):
    got = {}

    def factory(model, *, url, allow_remote):
        got.update(model=model, url=url, allow_remote=allow_remote)
        raise AddressRefused("remote", url)      # дальше сборки не идём: проверяем, что флаг дошёл
    monkeypatch.setattr(embed_door, "ollama_embedder", factory)
    assert _search(tmp_path, "--model-url", "https://api.example.com", "--allow-remote") == cli.EXIT_USAGE
    assert got == {"model": "м", "url": "https://api.example.com", "allow_remote": True}


def test_cli_allow_remote_needs_a_model_url(tmp_path, capsys):
    graph = tmp_path / "г"
    graph.mkdir()
    assert cli.main(["search", str(graph), "q", "--allow-remote"]) == cli.EXIT_USAGE
    assert "--allow-remote" in capsys.readouterr().err


# ── Приложение не зовёт фабрику пакета ──────────────────────────────────

_DOOR = "charoite_graph.embed_door"
_BUILDERS = {"embedder", "ollama_embedder"}


def _builds_embedder(source: str, rel: str) -> list[int]:
    """Строки, где код собирает векторизатор мимо `llm.embedder`, — по AST, а не по написанию.

    Ловит любую форму доступа к сборщикам двери (`embedder`, `ollama_embedder`):
    `from charoite_graph.embed_door import embedder` (и `*`), атрибут на имени,
    которое связано с модулем двери (`import charoite_graph.embed_door as ed`,
    `from charoite_graph import embed_door`), цепочку `charoite_graph.embed_door.x`
    и строку-имя сборщика (`getattr(ed, "ollama_embedder")`). В `src/llm.py`
    доступ законен только внутри `def embedder` — там адрес выдаёт privacy.
    """
    tree = ast.parse(source)
    door_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == _DOOR:
                    door_names.add(a.asname or "charoite_graph")
        elif isinstance(node, ast.ImportFrom) and node.module == "charoite_graph":
            for a in node.names:
                if a.name == "embed_door":
                    door_names.add(a.asname or "embed_door")
    allowed: set[int] = set()
    if rel == "src/llm.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "embedder":
                allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    def door_expr(e) -> bool:
        if isinstance(e, ast.Name):
            return e.id in door_names and e.id != "charoite_graph"
        return (isinstance(e, ast.Attribute) and e.attr == "embed_door"
                and isinstance(e.value, ast.Name) and e.value.id == "charoite_graph")

    hits = []
    for node in ast.walk(tree):
        bad = False
        if isinstance(node, ast.ImportFrom) and node.module == _DOOR:
            bad = any(a.name in _BUILDERS | {"*"} for a in node.names)
        elif isinstance(node, ast.Attribute) and node.attr in _BUILDERS:
            bad = door_expr(node.value)
        elif isinstance(node, ast.Constant) and node.value == "ollama_embedder":
            bad = True
        if bad and node.lineno not in allowed:
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("source", [
    "from charoite_graph.embed_door import embedder\nembedder(u, 'm')",
    "from charoite_graph.embed_door import ollama_embedder as f",
    "from charoite_graph.embed_door import *",
    "import charoite_graph.embed_door as ed\ned.embedder(u, 'm')",
    "from charoite_graph import embed_door\nembed_door.embedder(u, 'm')",
    "from charoite_graph import embed_door as door\ndoor.ollama_embedder('m')",
    "import charoite_graph.embed_door\ncharoite_graph.embed_door.embedder(u, 'm')",
    "import charoite_graph.embed_door as ed\ngetattr(ed, 'ollama_embedder')('m')",
])
def test_the_guard_sees_every_way_to_reach_a_builder(source):
    assert _builds_embedder(source, "src/daemon.py"), source


def test_the_guard_lets_the_door_helpers_and_llm_embedder_through():
    helpers = "import charoite_graph.embed_door as ed\nn = ed.EMBED_BATCH_TEXTS\ned.refusal_line('x')"
    assert _builds_embedder(helpers, "src/daemon.py") == []
    llm = ("import charoite_graph.embed_door as embed_door\n"
           "def embedder(cfg):\n    return embed_door.embedder(u, 'm')\n"
           "def other(cfg):\n    return embed_door.embedder(cfg['x'], 'm')\n")
    assert _builds_embedder(llm, "src/llm.py") == [5]


def test_the_app_builds_embedders_only_through_llm():
    """У фабрики нет рубильника, у двери — политики: код приложения, собравший
    векторизатор сам, обошёл бы CHAROITE_NO_CLOUD и llm.allow_remote.
    Приложение собирает его только в `llm.embedder` (адрес — privacy)."""
    offenders, scanned = [], 0
    for root in ("src", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if rel.startswith("src/charoite_graph/"):
                continue
            scanned += 1
            offenders += [f"{rel}:{n}" for n in _builds_embedder(path.read_text(encoding="utf-8"), rel)]
    assert scanned > 10, "сторож не нашёл файлов — обход сломан"
    assert not offenders, "векторизатор собран мимо llm.embedder:\n" + "\n".join(offenders)
