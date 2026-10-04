"""Отказ адреса модели на старте виден человеку, а не трейсбеком.

С #699 грамматика адреса отвергает зону IPv6 и логин. До этой правки
`LLM(cfg)` в `daemon.main` бросал `PrivacyRefused`, его никто не ловил,
и процесс выходил кодом 1: в stdout только «Загружаю модели…», текст —
в stderr после `Traceback`. Канал тот же, что у «корень не назван»:
статус с `error` и причиной значением, код не 1.

Проба готовности спрашивает `privacy.model_address_error`: та же развилка,
что демон, но текстом, а не исключением. Панель по этому тексту решает
`SetupReadinessPolicy.refusedModelAddressCheck` — на данных, в Swift-тесте.

Скрипт пробы исполняется из Swift-файла (сырая строка после `let script`):
Swift здесь не собирается, а подстрока в исходнике ничего не сторожит.
Сбой блока адреса не пишет `config_error`.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import exit_codes  # noqa: E402
import privacy  # noqa: E402

ZONE = "http://[fe80::1%en0]:11434"
LOGIN = "http://user:secret@127.0.0.1:11434"
CLOUD = "https://gw.example/v1"
LOOPBACK = "http://127.0.0.1:11434"

# Полные фразы отказа — литералы, не `str` исключения из `chat_model_url`:
# подмена тела той функции двигала бы обе стороны равенства.
_ZONE_TEXT = (
    "llm.base_url = http://[fe80::1%en0]:11434: "
    "адрес 'http://[fe80::1%en0]:11434': authority вне белой грамматики "
    "(имя или IPv6 в скобках, порт цифрами)"
)
_LOGIN_TEXT = (
    "llm.base_url = http://user:secret@127.0.0.1:11434: "
    "адрес 'http://user:secret@127.0.0.1:11434': authority вне белой грамматики "
    "(имя или IPv6 в скобках, порт цифрами)"
)
_KILL_TEXT = (
    "llm.base_url = https://gw.example/v1 указывает не на эту машину, "
    "а рубильник CHAROITE_NO_CLOUD запрещает любой выход наружу"
)
_YAML_ERROR = (
    "ParserError: while parsing a flow node\n"
    "expected the node content, but found '<stream end>'\n"
    "  in \"<unicode string>\", line 2, column 1:\n"
    "    \n"
    "    ^"
)
_LIST_ERROR = "AttributeError: 'list' object has no attribute 'get'"

_OLD_PRIVACY = "# сборка без privacy.model_address_error\n"
_BROKEN_IMPORT = "raise ImportError('старый контур без зависимости')\n"
_EAGER_PRIVACY = (
    "def model_address_error(cfg, env=None):\n"
    "    return 'ОТКАЗ-АДРЕСА'\n"
)

_PROBE_DROP = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONPYCACHEPREFIX",
    "CHAROITE_NO_CLOUD",
    "SUFLER_NO_CLOUD",
)

_DROP = (
    "CHAROITE_ROOT",
    "CHAROITE_NO_CLOUD",
    "SUFLER_NO_CLOUD",
    "CHAROITE_GRAPH_DIR",
    "SUFLER_GRAPH_DIR",
)

_DRIVER = (
    "import sys, types, runpy\n"
    "stt = types.ModuleType('stt')\n"
    "class STT:\n"
    "    def __init__(self, cfg):\n"
    "        print('STT_BUILT', file=sys.stderr, flush=True)\n"
    "stt.STT = STT\n"
    "sys.modules['stt'] = stt\n"
    "sys.argv = [sys.argv[1]]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def _yaml(url: str) -> str:
    quoted = json.dumps(url)
    return (
        "llm:\n"
        f"  base_url: {quoted}\n"
        "  model: probe\n"
        "sufler:\n"
        "  role: probe\n"
        "stt:\n"
        "  backend: gigaam\n"
        "audio:\n"
        "  device: auto\n"
        "  samplerate: 16000\n"
    )


def _events(stdout: str) -> list[dict]:
    out = []
    for line in stdout.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_умолчание_это_ollama_на_этой_машине():
    """`==` у mlx, заменённый на `!=`, отдал бы адрес mlx молчащему ollama."""
    assert privacy.chat_model_url({"llm": {}}, {}) == privacy.DEFAULT_LLM_URL


def test_mlx_называет_свой_адрес():
    assert privacy.chat_model_url({"llm": {"engine": "mlx-server"}}, {}) == privacy.DEFAULT_MLX_URL
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url({"llm": {"engine": "mlx-server", "mlx_base_url": ZONE}}, {})
    assert "llm.mlx_base_url" in str(caught.value)
    assert ZONE in str(caught.value)


def test_включённое_облако_берёт_свой_адрес():
    """Плохой локальный адрес при включённом облаке не должен решаться."""
    cfg = {
        "llm": {"engine": "cloud", "base_url": ZONE, "cloud_base_url": CLOUD},
        "sufler": {"cloud_engine": True},
    }
    assert privacy.chat_model_url(cfg, {}) == CLOUD


def test_выключенное_облако_спрашивает_локальный_адрес():
    """`and` → `or` в развилке отдал бы шлюз при выключенном тумблере."""
    cfg = {
        "llm": {"engine": "cloud", "base_url": ZONE, "cloud_base_url": CLOUD},
        "sufler": {"cloud_engine": False},
    }
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg, {})
    text = str(caught.value)
    assert "llm.base_url" in text
    assert "cloud_base_url" not in text
    assert ZONE in text


def test_включённое_облако_называет_свой_ключ():
    cfg = {
        "llm": {"engine": "cloud", "cloud_base_url": "http://user@gw.example/v1"},
        "sufler": {"cloud_engine": True},
    }
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg, {})
    assert "llm.cloud_base_url" in str(caught.value)


def test_неизвестный_движок_это_отказ():
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url({"llm": {"engine": "torch"}}, {})
    assert "неизвестный движок" in str(caught.value)


@pytest.mark.parametrize("url", [ZONE, LOGIN])
def test_грамматика_не_легализуется_allow_remote(url):
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url({"llm": {"base_url": url, "allow_remote": True}}, {})
    text = str(caught.value)
    assert text.startswith("llm.base_url")
    assert url in text
    assert "белой грамматики" in text


def test_конструктор_llm_не_глотает_отказ_грамматики():
    import llm

    cfg = {"llm": {"base_url": ZONE, "model": "проба"}, "sufler": {"role": "проба"}}
    with pytest.raises(privacy.PrivacyRefused) as caught:
        llm.LLM(cfg)
    assert ZONE in str(caught.value)
    assert "llm.base_url" in str(caught.value)


def test_конструктор_не_берёт_облачный_адрес_мимо_развилки(tmp_path, monkeypatch):
    """Облачная ветка не зовёт `cloud_llm_url` сама: `self.base` ставит
    только `chat_model_url`. Ранний вызов перезаписывался двадцатью строками
    ниже, а его подмена не должна менять адрес."""
    import llm

    key = tmp_path / "key"
    key.write_text("k\n", encoding="utf-8")
    key.chmod(0o600)
    cfg = {
        "llm": {
            "engine": "cloud",
            "model": "local",
            "cloud_base_url": CLOUD,
            "cloud_model": "m",
            "cloud_key_file": str(key),
        },
        "sufler": {"cloud_engine": True, "role": "роль"},
    }
    called = []

    def early(cfg, env=None):
        called.append(cfg)
        return "https://early.example/v1"

    monkeypatch.setattr(privacy, "cloud_llm_url", early)
    monkeypatch.setattr(privacy, "chat_model_url", lambda cfg, env=None: "https://fork.example/v1")
    client = llm.LLM(cfg)
    assert client.base == "https://fork.example/v1"
    assert client.cloud_ready is True
    assert called == []


@pytest.mark.parametrize("url", [ZONE, LOGIN])
def test_демон_называет_отказ_адреса_и_не_грузит_веса(tmp_path, url):
    """Процесс с таким адресом в конфиге: код 11, статус-ошибка, без трейсбека.

    Заглушка STT печатает `STT_BUILT`. На базе (до лова) процесс доходил до
    `LLM(cfg)` и умирал кодом 1 с `Traceback`. После правки проверка стоит
    до `STT(cfg)`, поэтому метки заглушки нет.
    """
    root = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.yaml").write_text(_yaml(url), encoding="utf-8")
    cfg = yaml.safe_load((root / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["llm"]["base_url"] == url
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg, {})
    expected = str(caught.value)

    driver = tmp_path / "drive.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in _DROP}
    env["CHAROITE_ROOT"] = str(root)
    env["PATH"] = os.environ.get("PATH", "")
    proc = subprocess.run(
        [sys.executable, str(driver), str(ROOT / "src" / "daemon.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=100,
    )

    assert proc.returncode == exit_codes.EXIT_PRIVACY_REFUSED, (
        f"код {proc.returncode}, stdout={proc.stdout[-400:]!r}, stderr={proc.stderr[-800:]!r}")
    assert "Traceback" not in proc.stderr, proc.stderr[-800:]
    assert "STT_BUILT" not in proc.stderr, proc.stderr[-800:]
    assert "Загружаю модели" not in proc.stdout
    assert expected in proc.stderr
    отказы = [e for e in _events(proc.stdout) if e.get("type") == "status" and e.get("error") is True]
    assert отказы, proc.stdout[-400:]
    assert отказы[0]["text"] == expected
    # Причина — значением. Что приложение не поднимает запись заново,
    # решает SuflerService.restartDecision на этой строке; проверка —
    # Swift-тест на данных, здесь Swift не исполняется.
    assert отказы[0].get("reason") == "privacy_refused"


@pytest.mark.parametrize("url,expected", [(ZONE, _ZONE_TEXT), (LOGIN, _LOGIN_TEXT)])
def test_проба_адреса_возвращает_текст_отказа(url, expected):
    """Отказ — текст для панели, литерал, а не `str` исключения из той же развилки."""
    text = privacy.model_address_error({"llm": {"base_url": url}}, {})
    assert text == expected
    assert text is not None


def test_проба_адреса_молчит_когда_адрес_принят():
    """Принятый адрес — не строка отказа. Плохой локальный при включённом
    облаке не должен всплыть: решается адрес шлюза."""
    assert privacy.model_address_error({"llm": {}}, {}) is None
    облако = {
        "llm": {"engine": "cloud", "base_url": ZONE, "cloud_base_url": CLOUD},
        "sufler": {"cloud_engine": True},
    }
    assert privacy.model_address_error(облако, {}) is None


def test_проба_адреса_передаёт_окружение():
    """Рубильник в переданном окружении меняет вердикт. Потеря аргумента
    оставила бы чужой https «принятым»."""
    cfg = {"llm": {"base_url": "https://gw.example/v1", "allow_remote": True}}
    assert privacy.model_address_error(cfg, {}) is None
    env = {"CHAROITE_NO_CLOUD": "1"}
    assert privacy.model_address_error(cfg, env) == _KILL_TEXT


def test_проба_адреса_без_аргумента_видит_процесс(monkeypatch):
    """Проба зовёт функцию без окружения: тогда читается окружение процесса,
    а не пустой словарь. `env or {}` погасил бы рубильник."""
    cfg = {"llm": {"base_url": "https://gw.example/v1", "allow_remote": True}}
    monkeypatch.delenv("CHAROITE_NO_CLOUD", raising=False)
    monkeypatch.delenv("SUFLER_NO_CLOUD", raising=False)
    assert privacy.model_address_error(cfg) is None
    monkeypatch.setenv("CHAROITE_NO_CLOUD", "1")
    assert privacy.model_address_error(cfg) == _KILL_TEXT


def test_проба_адреса_не_прячет_сломанный_конфиг():
    """Последовательность из yaml — не отказ адреса. Широкий except превратил
    бы её в текст панели «адрес отвергнут»."""
    with pytest.raises(AttributeError):
        privacy.model_address_error(["не словарь"], {})


def _intact(base_url: str | None = None, *, model: str | None = "probe") -> str:
    lines = [
        "audio:",
        "  device: auto",
        "  samplerate: 16000",
        "stt:",
        "  backend: gigaam",
        "llm:",
    ]
    if model is not None:
        lines.append(f"  model: {json.dumps(model)}")
    if base_url is not None:
        lines.append(f"  base_url: {json.dumps(base_url)}")
    return "\n".join(lines) + "\n"


def _data(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    root = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    (root / "config" / "config.yaml").write_text(text, encoding="utf-8")
    return root


def _code(tmp_path: pathlib.Path, source: str) -> pathlib.Path:
    code = tmp_path / "code"
    (code / "src").mkdir(parents=True)
    (code / "src" / "privacy.py").write_text(source, encoding="utf-8")
    return code


def _probe_script() -> str:
    path = ROOT / "app/Sources/CharoiteApp/Services/SetupReadinessService.swift"
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().endswith('let script = #"""'))
    end = next(i for i, line in enumerate(lines) if i > start and line.strip() == '"""#')
    return "\n".join(lines[start + 1:end]) + "\n"


def _probe(code_root: pathlib.Path, data_root: pathlib.Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _PROBE_DROP}
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", _probe_script(), str(code_root)],
        cwd=data_root, env=env, capture_output=True, text=True, timeout=60,
    )
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or not lines:
        raise AssertionError(
            f"код {proc.returncode}, stdout={proc.stdout[-400:]!r}, stderr={proc.stderr[-800:]!r}")
    return json.loads(lines[-1])


def test_проба_старого_privacy_не_портит_целый_конфиг(tmp_path):
    """Нет `model_address_error` — не «исправьте config.yaml».

    `AttributeError` старого корня кода раньше становился `config_error`
    и блокировал старт, хотя yaml цел.
    """
    body = _probe(_code(tmp_path, _OLD_PRIVACY), _data(tmp_path, _intact()))
    assert body["config_error"] is None
    assert body["address_error"] is None


def test_проба_сломанного_импорта_privacy_молчит(tmp_path):
    """`ImportError` при импорте privacy не становится ошибкой конфига."""
    body = _probe(_code(tmp_path, _BROKEN_IMPORT), _data(tmp_path, _intact()))
    assert body["config_error"] is None
    assert body["address_error"] is None


def test_проба_старого_privacy_сохраняет_missing(tmp_path):
    """Сбой адреса не перетирает уже найденное `missing: llm.model`."""
    text = _intact(model=None)
    body = _probe(_code(tmp_path, _OLD_PRIVACY), _data(tmp_path, text))
    assert body["config_error"] == "missing: llm.model"
    assert body["address_error"] is None


def test_проба_репозитория_называет_отказ_зоны(tmp_path):
    """Контроль стенда: живой privacy, зона — текст отказа, конфиг цел."""
    body = _probe(ROOT, _data(tmp_path, _intact(ZONE)))
    assert body["config_error"] is None
    assert body["address_error"] == _ZONE_TEXT


def test_проба_репозитория_принимает_loopback(tmp_path):
    body = _probe(ROOT, _data(tmp_path, _intact(LOOPBACK)))
    assert body["config_error"] is None
    assert body["address_error"] is None


def test_проба_репозитория_битый_yaml_не_адрес(tmp_path):
    body = _probe(ROOT, _data(tmp_path, "llm: [\n"))
    assert body["config_error"] == _YAML_ERROR
    assert body["address_error"] is None


def test_проба_верх_не_словарь_не_спрашивает_адрес(tmp_path):
    """Список наверху — не отказ адреса, даже если privacy ответил бы текстом."""
    body = _probe(_code(tmp_path, _EAGER_PRIVACY), _data(tmp_path, "- не словарь\n"))
    assert body["config_error"] == _LIST_ERROR
    assert body["address_error"] is None


def test_проба_при_missing_ключе_называет_отказ(tmp_path):
    """Прочитанный словарь с дыркой в ключе всё равно спрашивает адрес."""
    body = _probe(ROOT, _data(tmp_path, _intact(ZONE, model=None)))
    assert body["config_error"] == "missing: llm.model"
    assert body["address_error"] == _ZONE_TEXT
