"""Отказ адреса модели на старте виден человеку, а не трейсбеком.

С #699 грамматика адреса отвергает зону IPv6 и логин. До этой правки
`LLM(cfg)` в `daemon.main` бросал `PrivacyRefused`, его никто не ловил,
и процесс выходил кодом 1: в stdout только «Загружаю модели…», текст —
в stderr после `Traceback`. Канал тот же, что у «корень не назван»:
статус с `error` и причиной значением, код не 1.

Проба готовности спрашивает `privacy.model_address_error`: та же развилка,
что демон, но текстом, а не исключением. Панель по этому тексту решает
`SetupReadinessPolicy.refusedModelAddressCheck` — на данных, в Swift-тесте.
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


@pytest.mark.parametrize("url", [ZONE, LOGIN])
def test_проба_адреса_возвращает_текст_отказа(url):
    """Отказ — текст для панели, а не исключение и не чужая строка."""
    cfg = {"llm": {"base_url": url}}
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg, {})
    assert privacy.model_address_error(cfg, {}) == str(caught.value)


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
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg, env)
    assert privacy.model_address_error(cfg, env) == str(caught.value)


def test_проба_адреса_без_аргумента_видит_процесс(monkeypatch):
    """Проба зовёт функцию без окружения: тогда читается окружение процесса,
    а не пустой словарь. `env or {}` погасил бы рубильник."""
    cfg = {"llm": {"base_url": "https://gw.example/v1", "allow_remote": True}}
    monkeypatch.delenv("CHAROITE_NO_CLOUD", raising=False)
    monkeypatch.delenv("SUFLER_NO_CLOUD", raising=False)
    assert privacy.model_address_error(cfg) is None
    monkeypatch.setenv("CHAROITE_NO_CLOUD", "1")
    with pytest.raises(privacy.PrivacyRefused) as caught:
        privacy.chat_model_url(cfg)
    assert privacy.model_address_error(cfg) == str(caught.value)


def test_проба_адреса_не_прячет_сломанный_конфиг():
    """Последовательность из yaml — не отказ адреса. Широкий except превратил
    бы её в текст панели «адрес отвергнут»."""
    with pytest.raises(AttributeError):
        privacy.model_address_error(["не словарь"], {})
