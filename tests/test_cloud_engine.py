"""Облачный движок чата (llm.engine: cloud) — рубильник, ключ, запас.

Это второй путь стенограммы наружу после слоя Claude, и главный вопрос к
нему не «работает ли», а «может ли включиться молча».
"""
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm  # noqa: E402
import privacy  # noqa: E402


def _cfg(**llm_extra):
    base = {
        "llm": {"engine": "cloud", "model": "local:4b",
                "cloud_base_url": "https://gw.example.com/v1",
                "cloud_model": "cloud-model", **llm_extra},
        "sufler": {"cloud_engine": True, "role": "ты помощник"},
    }
    return base


def _with_key(tmp_path, cfg, key="secret-key"):
    f = tmp_path / "llm_key"
    f.write_text(key + "\n", encoding="utf-8")
    cfg["llm"]["cloud_key_file"] = str(f)
    return cfg


def test_engine_needs_both_the_address_and_the_permission(tmp_path):
    """Один ключ без другого оставляет локальную модель — молча не включаемся."""
    cfg = _with_key(tmp_path, _cfg())
    cfg["sufler"]["cloud_engine"] = False
    client = llm.LLM(cfg)
    assert client.engine == "ollama", "облако включилось без разрешения"
    assert client.cloud_ready is False

    cfg["sufler"]["cloud_engine"] = True
    assert llm.LLM(cfg).cloud_ready is True


def test_kill_switch_beats_the_config(tmp_path, monkeypatch):
    """Рубильник сильнее конфига: «запустить офлайн» — одна переменная."""
    cfg = _with_key(tmp_path, _cfg())
    monkeypatch.setenv("CHAROITE_NO_CLOUD", "1")
    client = llm.LLM(cfg)
    assert client.cloud_ready is False and client.engine == "ollama"


def test_missing_key_is_loud_not_silent(tmp_path):
    """Нет ключа — ошибка при старте, а не тихая работа на локальной."""
    cfg = _cfg(cloud_key_file=str(tmp_path / "нет-такого"))
    with pytest.raises(RuntimeError, match="ключ"):
        llm.LLM(cfg)


def test_plain_http_gateway_is_refused(tmp_path):
    """По http ключ уходит открытым текстом — адрес не принимаем."""
    cfg = _with_key(tmp_path, _cfg(cloud_base_url="http://gw.example.com/v1"))
    with pytest.raises(RuntimeError, match="https"):
        llm.LLM(cfg)


def test_key_never_appears_in_the_payload(tmp_path):
    """Ключ живёт только в заголовке: тело запроса уходит в логи и отчёты."""
    client = llm.LLM(_with_key(tmp_path, _cfg()))
    payload = client._cloud_payload([{"role": "user", "content": "привет"}],
                                    num_predict=100, temperature=0.2, stream=True)
    assert "secret-key" not in repr(payload)
    assert client._auth() == {"headers": {"Authorization": "Bearer secret-key"}}
    assert payload["model"] == "cloud-model"
    # chat_template_kwargs — поле mlx_lm.server; чужой шлюз ответит на него 400
    assert "chat_template_kwargs" not in payload


def test_local_engines_send_no_authorization():
    """Локальный сервер не должен получать чужой заголовок ни при каких условиях."""
    client = llm.LLM({"llm": {"model": "local:4b"}, "sufler": {"role": "ты помощник"}})
    assert client._auth() == {}, "локальный вызов обязан идти без лишних аргументов"


def test_privacy_lists_the_toggle():
    assert "cloud_engine" in privacy.KEYS
    assert privacy.cloud_engine_enabled({"sufler": {"cloud_engine": True}}, {}) is True
    # строка «true» разрешением не считается — как и у остальных ключей
    assert privacy.cloud_engine_enabled({"sufler": {"cloud_engine": "true"}}, {}) is False
