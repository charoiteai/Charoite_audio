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


def test_gateway_error_never_carries_the_key(tmp_path):
    """Ключ не должен доехать до карточки подсказки и файла стенограммы.

    OpenAI-совместимые шлюзы возвращают его эхом («Incorrect API key
    provided: sk-…»), а тело ошибки едет в LLMHTTPError.detail → в статус,
    в hints-файл и в ответ MCP (круг-1 DS, Critical).
    """
    client = llm.LLM(_with_key(tmp_path, _cfg()))

    class _Resp:
        status_code = 401
        text = "Incorrect API key provided: secret-key. Check your key."

        def json(self): return {}

    with pytest.raises(llm.LLMHTTPError) as err:
        client._checked_body(_Resp())
    assert "secret-key" not in str(err.value)
    assert "secret-key" not in err.value.detail
    assert "***" in err.value.detail


def test_fallback_engine_follows_the_local_setup(tmp_path):
    """Запас идёт на тот локальный движок, который у человека реально есть."""
    plain = llm.LLM(_with_key(tmp_path, _cfg()))
    assert plain.fallback_engine == "ollama"

    with_mlx = llm.LLM(_with_key(tmp_path, _cfg(mlx_model="mlx-community/Qwen")))
    assert with_mlx.fallback_engine == "mlx-server", \
        "у кого локальная модель на mlx_lm.server, запас в пустой Ollama мёртв"

    explicit = llm.LLM(_with_key(tmp_path, _cfg(mlx_model="mlx-community/Qwen",
                                                cloud_fallback_engine="ollama")))
    assert explicit.fallback_engine == "ollama"


def test_broken_gateway_stream_falls_back_before_the_first_token(tmp_path, monkeypatch):
    """Битый SSE — такой же повод уйти на локальную, как и сеть."""
    client = llm.LLM(_with_key(tmp_path, _cfg()))

    def broken(*_a, **_kw):
        yield from ()
        raise ValueError("Expecting value: line 1 column 1")

    monkeypatch.setattr(client, "_sse", broken)
    monkeypatch.setattr(llm, "LLM", lambda cfg: _LocalStub())
    assert "".join(client._stream_cloud([{"role": "user", "content": "?"}],
                                        num_predict=None, temperature=None,
                                        busy_wait=1.0)) == "локальный ответ"


class _LocalStub:
    """Локальный клиент, каким его видит фолбэк."""

    def stream_messages(self, messages, **_kw):
        yield "локальный ответ"


def test_health_probe_does_not_restart_anything_for_cloud(tmp_path, monkeypatch):
    """Облако не «встало» — перезапускать нечего, а разбор не блокируем.

    Проба, ушедшая в локальную Ollama, роняла разбор ВСЕХ встреч на
    облачной установке: сервис перезапускался, ответа не было, граф не
    обновлялся (круг-1: GLM Critical, DS Important).
    """
    import llm_health

    cfg = _with_key(tmp_path, _cfg())
    monkeypatch.setattr(llm_health, "probe", lambda *_a, **_kw: False)
    calls = []
    monkeypatch.setattr(llm_health, "_restart", lambda *a, **kw: calls.append("ollama"))
    monkeypatch.setattr(llm_health, "_restart_mlx", lambda *a, **kw: calls.append("mlx"))

    said = []
    assert llm_health.ensure_alive(cfg, log=said.append, wait=0.1) is True
    assert not calls, "перезапустили локальный сервер из-за облачной пробы"
    assert any("шлюз" in line for line in said)
    assert llm_health.is_local(cfg) is False


def test_no_raw_error_can_be_raised_inside_the_client():
    """Внутри клиента ошибку создаёт только _fail — она всегда маскирует ключ.

    Круг-1 закрыл одну точку утечки, круг-2 нашёл вторую (ошибка внутри
    200-стрима). Третьей быть не должно: сторож ловит любой прямой
    `raise LLMHTTPError(` в классе, а не конкретное место.
    """
    source = (SRC / "llm.py").read_text(encoding="utf-8")
    body = source[source.index("class LLM:"):]
    assert "raise LLMHTTPError(" not in body, \
        "ошибка создаётся в обход _fail — ключ снова доедет до экрана"
    assert body.count("raise self._fail(") >= 8


def test_error_inside_a_200_stream_is_masked_too(tmp_path):
    """Прокси отвечает 200 и шлёт «error» в теле — ключ там тоже эхом."""
    client = llm.LLM(_with_key(tmp_path, _cfg()))
    err = client._fail(200, "Incorrect API key provided: secret-key")
    assert "secret-key" not in err.detail and "***" in err.detail


def test_probe_keeps_the_key_when_the_toggle_is_off(tmp_path, monkeypatch):
    """Без разрешения к шлюзу не ходят вообще — включая пробу здоровья."""
    import llm_health

    cfg = _with_key(tmp_path, _cfg())
    cfg["sufler"]["cloud_engine"] = False
    sent = []
    monkeypatch.setattr(llm_health.requests, "post",
                        lambda url, **kw: sent.append(url) or _NoResp())
    llm_health.probe(cfg, timeout=1)
    assert all("gw.example.com" not in url for url in sent), \
        "проба ушла на шлюз с ключом, хотя тумблер выключен"


class _NoResp:
    status_code = 500

    def json(self): return {}


def _sse_lines(*lines):
    """Ответ шлюза, каким его видит _sse."""

    class _R:
        status_code = 200

        def iter_lines(self):
            yield from lines

        def __enter__(self): return self

        def __exit__(self, *a): return False

    return _R()


def test_silent_gateway_is_cut_off_but_a_thinking_one_is_not(tmp_path, monkeypatch):
    """Тишина рвётся быстро, keepalive — терпится дольше.

    Проверять дедлайн снаружи бесполезно: keepalive-строки уходят через
    continue, наружу управление не возвращается (круг-3 DS, I1), а рвать
    думающий шлюз на тридцатой секунде — молча подменить модель (I3).
    """
    client = llm.LLM(_with_key(tmp_path, _cfg()))
    clock = {"now": 0.0}
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["now"])

    def silent(*_a, **_kw):
        for _ in range(5):
            clock["now"] += 10          # ни байта
            yield b""

    monkeypatch.setattr(client, "_open_stream", lambda *a, **kw: _sse_lines(*silent()))
    with pytest.raises(llm.LLMHTTPError) as err:
        list(client._sse("u", {}, 1.0, first_token=30.0))
    assert err.value.status == 200 and "ни одного токена" in err.value.detail

    clock["now"] = 0.0
    keepalive = ([b": keepalive"] * 5
                 + [b'data: {"choices":[{"delta":{"content":"ok"}}]}', b"data: [DONE]"])

    def ticking():
        for line in keepalive:
            clock["now"] += 10          # активность есть, ответа пока нет
            yield line

    monkeypatch.setattr(client, "_open_stream", lambda *a, **kw: _sse_lines(*ticking()))
    assert "".join(client._sse("u", {}, 1.0, first_token=30.0)) == "ok", \
        "думающий шлюз оборван раньше времени"


def test_fallback_reason_carries_the_gateway_text(tmp_path, monkeypatch):
    """«Insufficient balance» не должен теряться при уходе на локальную."""
    client = llm.LLM(_with_key(tmp_path, _cfg()))

    def refuse(*_a, **_kw):
        yield from ()
        raise client._fail(200, "Your account balance is insufficient")

    said = []
    monkeypatch.setattr(client, "_sse", refuse)
    monkeypatch.setattr(llm, "LLM", lambda cfg: _LocalStub())
    monkeypatch.setattr(llm.sys, "stderr", type("S", (), {
        "write": lambda self, t: said.append(t), "flush": lambda self: None})())
    list(client._stream_cloud([{"role": "user", "content": "?"}],
                              num_predict=None, temperature=None, busy_wait=1.0))
    assert any("insufficient" in t.lower() for t in said), said


def test_effective_cloud_needs_both_keys():
    """Диагностика обязана спрашивать ту же пару, что и клиент."""
    assert privacy.cloud_engine_active(
        {"llm": {"engine": "cloud"}, "sufler": {"cloud_engine": True}}, {}) is True
    assert privacy.cloud_engine_active(
        {"llm": {"engine": "cloud"}, "sufler": {}}, {}) is False
    assert privacy.cloud_engine_active(
        {"llm": {}, "sufler": {"cloud_engine": True}}, {}) is False
