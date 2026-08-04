"""У облака четыре тумблера, а у стенограммы было пять дверей.

`llm.base_url` — адрес, на который уходит КАЖДЫЙ локальный запрос: подсказки,
разбор встречи, имена говорящих, эмбеддинги дежавю. Восемь мест читали его из
конфига напрямую, ни одно не спрашивало privacy.py, и адрес чужой машины
превращал «всё локально» в отправку стенограмм по сети — при выключенном
облаке и даже под CHAROITE_NO_CLOUD. А docs/MODELS.md прямо советовал так
делать («point llm.base_url at another machine or a cloud endpoint»), то есть
документация рекомендовала то, что PRIVACY.md обещает не делать.

Теперь адрес выдаёт privacy.llm_base_url, и правило то же, что у тумблеров:
loopback — всегда можно; всё остальное — только явное `llm.allow_remote: true`
и никогда под рубильником. Здесь это закреплено поведенчески и структурно:
последний тест не даёт завести девятого читателя мимо privacy.
"""
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import privacy  # noqa: E402

NO_ENV: dict[str, str] = {}
KILL = {"CHAROITE_NO_CLOUD": "1"}


def test_loopback_is_always_allowed():
    for url in ("http://localhost:11434", "http://127.0.0.1:11434",
                "http://127.1.2.3:11434", "http://[::1]:11434"):
        cfg = {"llm": {"base_url": url}}
        assert privacy.llm_base_url(cfg, NO_ENV) == url.rstrip("/")
        # рубильник запрещает выход НАРУЖУ; локальная Ollama — не наружа
        assert privacy.llm_base_url(cfg, KILL) == url.rstrip("/")


def test_missing_url_falls_back_to_local_default():
    assert privacy.llm_base_url({}, NO_ENV) == privacy.DEFAULT_LLM_URL
    assert privacy.llm_base_url({"llm": {}}, NO_ENV) == privacy.DEFAULT_LLM_URL


def test_remote_needs_explicit_permission():
    for url in ("http://192.168.1.20:11434", "http://mac-mini.local:11434",
                "https://api.example.com"):
        with pytest.raises(RuntimeError):
            privacy.llm_base_url({"llm": {"base_url": url}}, NO_ENV)


def test_permission_is_strictly_boolean_true():
    """Как и облачные ключи: строка, единица и прочий мусор — не разрешение."""
    url = "http://192.168.1.20:11434"
    for junk in ("true", "yes", 1, "on", "", None, 0):
        cfg = {"llm": {"base_url": url, "allow_remote": junk}}
        with pytest.raises(RuntimeError):
            privacy.llm_base_url(cfg, NO_ENV)


def test_remote_allowed_with_explicit_flag():
    cfg = {"llm": {"base_url": "http://192.168.1.20:11434/", "allow_remote": True}}
    assert privacy.llm_base_url(cfg, NO_ENV) == "http://192.168.1.20:11434"


def test_kill_switch_beats_allow_remote():
    """CHAROITE_NO_CLOUD означает «ничего не уходит», а не «кроме Ollama»."""
    cfg = {"llm": {"base_url": "http://192.168.1.20:11434", "allow_remote": True}}
    for switch in privacy.KILL_SWITCHES:
        with pytest.raises(RuntimeError):
            privacy.llm_base_url(cfg, {switch: "1"})


def test_no_reader_bypasses_privacy():
    """Структурный сторож: адрес LLM выдаёт только privacy.py.

    Прямое чтение ключа — это ровно та дыра, которая закрыта: новый код,
    взявший cfg["llm"]["base_url"] сам, снова обойдёт и allow_remote, и
    рубильник.

    Сторож обходит РЕКУРСИВНО и `src/`, и `scripts/`. Раньше здесь стояло
    `(REPO / "src").glob("*.py")`, а докстринг обещал «src/ целиком» —
    и то, и другое было неправдой: подпапки не просматривались вовсе, а
    `scripts/doctor.py` спокойно читал base_url напрямую и посылал запрос
    по этому адресу. Сторож этого не видел и своим зелёным цветом закрывал
    вопрос. Обещание в докстринге и обход в коде должны совпадать буквально,
    иначе первым сломается доверие к самому сторожу.
    """
    direct = re.compile(r"""\[\s*["']base_url["']\s*\]|\.get\(\s*["']base_url["']""")
    offenders = []
    scanned = 0
    for root in ("src", "scripts"):
        for path in sorted((REPO / root).rglob("*.py")):
            if path.name == "privacy.py":
                continue
            scanned += 1
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if direct.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    assert scanned > 1, "сторож не нашёл файлов для проверки — обход сломан"
    assert not offenders, (
        "llm.base_url читается мимо privacy.llm_base_url:\n" + "\n".join(offenders))
