"""Дверь ночной ревизии у Tinfoil (src/night_cloud.py, №792) — поведение.

CI без сети и без SDK: клиент подменяет фабрика транспорта (`transport=`), а
путь через настоящий конструктор проверяется подставным модулем `tinfoil` в
`sys.modules`. Главные инварианты: гейт ложен или провайдер не принят —
транспорт не позван ни разу; провал аттестации — ноль отправок; срок держит и
аттестацию, и молчащий поток; обрыв по длине и подделка раздела — отказ;
повторов нет (`max_retries=0`); ключ не попадает ни в ошибку, ни в состояние.
"""
from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import json
import json as _json
import pathlib
import ssl
import sys
import threading
import time
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import charoite_paths  # noqa: E402
import night_cloud  # noqa: E402
import privacy  # noqa: E402
from exit_codes import (EXIT_ATTESTATION_FAILED, EXIT_PRIVACY_REFUSED,  # noqa: E402
                        EXIT_PROVIDER_UNAVAILABLE)

KEY = "tf-SECRET-key-0123456789"
SECTIONS = ("## Противоречия\n- нет\n## Протухшее\n- нет\n## Слияния\n- нет\n"
            "## Потерянные хвосты\n- нет\n## Три риска недели\n- нет\n")


def _cfg(tmp_path, **sufler) -> dict:
    key = tmp_path / "tinfoil_key"
    if not key.exists():
        key.write_text(KEY + "\n", encoding="utf-8")
        key.chmod(0o600)
    base = {"cloud_enrich": True, "cloud_night_provider": "tinfoil",
            "cloud_night_tinfoil_key_path": str(key)}
    base.update(sufler)
    return {"sufler": base}


def _chunk(text=None, finish=None):
    delta = types.SimpleNamespace(content=text)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta, finish_reason=finish)])


class FakeStream:
    def __init__(self, chunks, between=None, close_error=None):
        self.chunks, self.between, self.closed = chunks, between, False
        self.close_error = close_error

    def __iter__(self):
        for c in self.chunks:
            if self.between:
                self.between()
            if isinstance(c, BaseException):
                raise c
            yield c

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeClient:
    """Клиент SDK: документ аттестации и `client.with_options(...).chat.completions.create`."""

    def __init__(self, stream=None, verified=True, steps_ok=True, create_error=None):
        self.stream = stream if stream is not None else FakeStream([_chunk(SECTIONS), _chunk(None, "stop")])
        self.options: list[dict] = []
        self.creates: list[dict] = []
        self.create_error = create_error
        status = "success" if steps_ok else "failed"
        self.doc = types.SimpleNamespace(
            security_verified=verified, code_fingerprint="ab" * 32, release_tag="v0.0.167",
            enclave_host="router.example",
            steps={s: types.SimpleNamespace(status=status if s == "compare_measurements" else "success")
                   for s in ("fetch_digest", "verify_code", "verify_enclave", "compare_measurements")})
        outer = self

        class Api:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        outer.creates.append(kw)
                        if outer.create_error:
                            raise outer.create_error
                        return outer.stream

        class Inner:
            @staticmethod
            def with_options(**kw):
                outer.options.append(kw)
                return Api

        self.client = Inner

    def get_verification_document(self):
        return self.doc


class Factory:
    def __init__(self, client=None, error=None, block: threading.Event | None = None):
        self.client, self.error, self.block = client or FakeClient(), error, block
        self.calls = 0

    def __call__(self, cfg, key):
        self.calls += 1
        if self.block is not None:
            self.block.wait(5)
        if self.error is not None:
            raise self.error
        return self.client


def _send(tmp_path, cfg, factory, seconds=30.0, deadline=None):
    return night_cloud.send(cfg, "ядра графа", deadline=deadline or night_cloud.Deadline.after(seconds),
                            state_path=tmp_path / "logs" / "night_cloud_state.json",
                            graph_key="G", transport=factory)


def _state(tmp_path) -> dict:
    return json.loads((tmp_path / "logs" / "night_cloud_state.json").read_text(encoding="utf-8"))["G"]


# ── гейт и провайдер: транспорт не позван ни разу ──

@pytest.mark.parametrize("sufler", [
    {"cloud_enrich": False},
    {"cloud_enrich": "true"},
    {"cloud_night_provider": None},
    {"cloud_night_provider": ""},
    {"cloud_night_provider": False},
    {"cloud_night_provider": "Tinfoil"},
    {"cloud_night_provider": " tinfoil"},
    {"cloud_night_provider": "anthropic"},
])
def test_gate_or_provider_refusal_never_touches_the_transport(tmp_path, sufler):
    factory = Factory()
    with pytest.raises(privacy.PrivacyRefused):
        _send(tmp_path, _cfg(tmp_path, **sufler), factory)
    assert factory.calls == 0
    assert not factory.client.creates


def test_absent_provider_is_anthropic_and_the_door_refuses(tmp_path):
    cfg = _cfg(tmp_path)
    del cfg["sufler"]["cloud_night_provider"]
    factory = Factory()
    with pytest.raises(privacy.PrivacyRefused):
        _send(tmp_path, cfg, factory)
    assert factory.calls == 0


def test_kill_switch_wins_over_the_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAROITE_NO_CLOUD", "1")
    factory = Factory()
    with pytest.raises(privacy.PrivacyRefused):
        _send(tmp_path, _cfg(tmp_path), factory)
    assert factory.calls == 0


# ── ключ ──

def test_missing_key_is_unavailable_and_nothing_is_built(tmp_path):
    cfg = _cfg(tmp_path, cloud_night_tinfoil_key_path=str(tmp_path / "нет_ключа"))
    factory = Factory()
    with pytest.raises(night_cloud.ProviderUnavailable, match="нет ключа"):
        _send(tmp_path, cfg, factory)
    assert factory.calls == 0
    assert _state(tmp_path)["last_attempt"]["outcome"] == "unavailable"


def test_state_reason_carries_no_home_path(tmp_path):
    """Причину читает бриф, бриф пишется в граф: путь с именем учётки — нет (out1 DS-I3)."""
    home_key = pathlib.Path.home() / ".config" / "charoite" / "__нет_ключа_792__"
    cfg = _cfg(tmp_path, cloud_night_tinfoil_key_path=str(home_key))
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, cfg, Factory())
    reason = _state(tmp_path)["last_attempt"]["reason"]
    assert str(pathlib.Path.home()) not in reason and "~/.config/charoite/__нет_ключа_792__" in reason


def test_key_never_leaks_into_errors_state_or_stderr(tmp_path, capsys):
    """Контракт llm.cloud_key, повторённый для двери (in3 K2)."""
    cfg = _cfg(tmp_path)
    (tmp_path / "tinfoil_key").chmod(0o644)
    factory = Factory(error=RuntimeError(f"401 for Bearer {KEY}"))
    with pytest.raises(night_cloud.NightCloudError) as e:
        _send(tmp_path, cfg, factory)
    err = capsys.readouterr().err
    assert "chmod 600" in err, "права шире 600 — строка в stderr"
    state_text = (tmp_path / "logs" / "night_cloud_state.json").read_text(encoding="utf-8")
    for where in (str(e.value), err, state_text):
        assert KEY not in where


# ── аттестация ──

def test_failed_attestation_sends_nothing(tmp_path):
    factory = Factory(error=night_cloud.AttestationFailed("SNP measurement mismatch"))
    with pytest.raises(night_cloud.AttestationFailed):
        _send(tmp_path, _cfg(tmp_path), factory)
    assert not factory.client.creates
    assert _state(tmp_path)["last_attempt"]["outcome"] == "attestation"


def _doc_client(**change) -> FakeClient:
    client = FakeClient()
    for k, v in change.items():
        setattr(client.doc, k, v)
    return client


def _broken_doc_client() -> FakeClient:
    client = FakeClient()

    def boom():
        raise RuntimeError("документ не читается")

    client.get_verification_document = boom
    return client


@pytest.mark.parametrize("client", [
    FakeClient(verified=False),
    FakeClient(steps_ok=False),
    _doc_client(security_verified="true"),
    _doc_client(steps={}),          # «все шаги success» на пустом множестве (out1 S-I1)
    _doc_client(steps=None),
    _doc_client(steps=[]),          # не словарь — не «сеть», а аттестация (out1 DS-I1)
    _doc_client(steps={s: types.SimpleNamespace(status="success")
                       for s in ("fetch_digest", "verify_code", "verify_enclave")}),
    _doc_client(code_fingerprint=""),
    _broken_doc_client(),
], ids=["not-verified", "step-failed", "verified-str", "steps-empty", "steps-none", "steps-list",
        "step-missing", "no-fingerprint", "doc-raises"])
def test_unverified_document_is_an_attestation_failure(tmp_path, client):
    with pytest.raises(night_cloud.AttestationFailed):
        _send(tmp_path, _cfg(tmp_path), Factory(client=client))
    assert not client.creates
    assert _state(tmp_path)["last_attempt"]["outcome"] == "attestation"


def _fake_tinfoil(monkeypatch, error=None, client=None):
    mod = types.ModuleType("tinfoil")

    def TinfoilAI(api_key):   # noqa: N802 — имя класса SDK
        if error is not None:
            raise error
        return client or FakeClient()

    mod.TinfoilAI = TinfoilAI
    monkeypatch.setitem(sys.modules, "tinfoil", mod)


@pytest.mark.parametrize("error,cls", [
    (ValueError("SNP measurement mismatch"), night_cloud.AttestationFailed),
    (type("MeasurementMismatchError", (Exception,), {})("code differs"), night_cloud.AttestationFailed),
    (ConnectionError("refused"), night_cloud.ProviderUnavailable),
    (ValueError("Failed to fetch router addresses: <urlopen error>"), night_cloud.ProviderUnavailable),
    # TLS не сошёлся — проверить нельзя: громко, не «сеть» (out1 S-I3, H-I2)
    (ssl.SSLError("CERTIFICATE_VERIFY_FAILED"), night_cloud.AttestationFailed),
    (type("SSLError", (ConnectionError,), {})("requests: bad handshake"), night_cloud.AttestationFailed),
    # битый JSON роутера и пакет внутри SDK — недоступность, не подмена
    (_json.JSONDecodeError("Expecting value", "", 0), night_cloud.ProviderUnavailable),
    (ModuleNotFoundError("No module named 'sigstore'"), night_cloud.ProviderUnavailable),
])
def test_constructor_errors_are_classified(tmp_path, monkeypatch, error, cls):
    """Сеть — недоступность; подпись и отпечаток — аттестация (in3 S-I3)."""
    _fake_tinfoil(monkeypatch, error=error)
    with pytest.raises(cls):
        _send(tmp_path, _cfg(tmp_path), None)


def test_attesting_constructor_asks_the_gate_itself(tmp_path, monkeypatch):
    """`_attest` — выход SDK в реестре: гейт свой, не только у send (out1 S-M2)."""
    built = []
    mod = types.ModuleType("tinfoil")
    mod.TinfoilAI = lambda api_key: built.append(api_key)
    monkeypatch.setitem(sys.modules, "tinfoil", mod)
    with pytest.raises(privacy.PrivacyRefused):
        night_cloud._attest(_cfg(tmp_path, cloud_enrich=False), KEY)
    assert not built


def _tinfoil_error(name="_PinMismatchError"):
    return type(name, (ValueError,), {"__module__": "tinfoil.client"})("pin")


@pytest.mark.parametrize("error,cls", [
    (_tinfoil_error(), night_cloud.AttestationFailed),     # закреплённый ключ энклава не тот
    (ssl.SSLError("bad cert"), night_cloud.AttestationFailed),
    (type("APIStatusError", (Exception,), {"status_code": 503})("busy"), night_cloud.ProviderUnavailable),
])
def test_chat_stage_errors_are_classified(tmp_path, error, cls):
    with pytest.raises(cls):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=error)))


def test_real_constructor_path_sends_through_the_attested_client(tmp_path, monkeypatch):
    client = FakeClient()
    _fake_tinfoil(monkeypatch, client=client)
    reply = _send(tmp_path, _cfg(tmp_path), None)
    assert reply.text.startswith("## Противоречия") and len(client.creates) == 1


def test_missing_sdk_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "tinfoil", None)
    with pytest.raises(night_cloud.ProviderUnavailable, match="tinfoil"):
        _send(tmp_path, _cfg(tmp_path), None)


def test_door_imports_without_any_sdk_package(monkeypatch):
    """Верхний уровень двери не тянет SDK: CI и машина без extra (in3 K6)."""
    for name in ("tinfoil", "openai", "httpx"):
        monkeypatch.setitem(sys.modules, name, None)
    spec = importlib.util.spec_from_file_location("night_cloud_probe", REPO / "src" / "night_cloud.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclass ищет свой модуль в sys.modules при создании класса
    monkeypatch.setitem(sys.modules, "night_cloud_probe", mod)
    spec.loader.exec_module(mod)
    assert mod.send


# ── срок ──

def test_deadline_cuts_a_hanging_attestation_and_sends_nothing(tmp_path):
    release = threading.Event()
    factory = Factory(block=release)
    started = time.monotonic()
    try:
        with pytest.raises(night_cloud.DeadlineExpired):
            _send(tmp_path, _cfg(tmp_path), factory, seconds=0.2)
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert not factory.client.creates
    assert _state(tmp_path)["last_attempt"]["outcome"] == "deadline"


def test_deadline_is_checked_on_every_chunk_of_a_slow_stream(tmp_path):
    now = [0.0]
    deadline = night_cloud.Deadline.after(100.0, clock=lambda: now[0])

    def tick():
        now[0] += 60.0      # каждый кусок «приходит» через минуту

    stream = FakeStream([_chunk("## Противоречия\n"), _chunk("- a\n"), _chunk("- b\n"), _chunk(None, "stop")],
                        between=tick)
    client = FakeClient(stream=stream)
    with pytest.raises(night_cloud.DeadlineExpired):
        _send(tmp_path, _cfg(tmp_path), Factory(client=client), deadline=deadline)
    assert stream.closed, "поток закрыт по сроку"


def test_deadline_cuts_a_stream_that_only_sends_keepalive(tmp_path):
    """Сервер молчит между событиями (только `: ping`): цикл не просыпается,
    срок держит поток-сторож и закрывает поток (out1 S-I2)."""
    release = threading.Event()
    stream = FakeStream([_chunk("## Противоречия\n"), _chunk(None, "stop")],
                        between=lambda: release.wait(5))
    started = time.monotonic()
    try:
        with pytest.raises(night_cloud.DeadlineExpired, match="посреди ответа"):
            _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)), seconds=0.3)
    finally:
        release.set()
    assert time.monotonic() - started < 3
    assert stream.closed, "поток закрыт сторожем по сроку"
    entry = _state(tmp_path)["last_attempt"]
    assert entry["outcome"] == "deadline" and entry["fingerprint"] == "ab" * 32


def test_failing_close_does_not_decide_the_outcome(tmp_path):
    """Уборка не решает исход (out1 DS-M2)."""
    stream = FakeStream([_chunk(SECTIONS), _chunk(None, "stop")], close_error=RuntimeError("close"))
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    assert reply.text.startswith("## Противоречия") and stream.closed


def test_expired_deadline_before_send_sends_nothing(tmp_path):
    now = [0.0]
    deadline = night_cloud.Deadline.after(1.0, clock=lambda: now[0])
    client = FakeClient()

    class Late(Factory):
        def __call__(self, cfg, key):
            now[0] = 5.0
            return super().__call__(cfg, key)

    with pytest.raises(night_cloud.DeadlineExpired):
        _send(tmp_path, _cfg(tmp_path), Late(client=client), deadline=deadline)
    assert not client.creates


def test_read_timeout_is_capped_and_retries_are_off(tmp_path):
    client = FakeClient()
    _send(tmp_path, _cfg(tmp_path), Factory(client=client), seconds=500)
    (opts,) = client.options
    assert opts["max_retries"] == 0
    assert 0 < opts["timeout"] <= night_cloud.READ_TIMEOUT_S
    (kw,) = client.creates
    assert kw["stream"] is True and kw["reasoning_effort"] == "max"
    assert kw["model"] == "deepseek-v4-1-flash" and kw["max_tokens"] == night_cloud.MAX_TOKENS


def test_effort_and_model_come_from_the_config(tmp_path):
    client = FakeClient()
    cfg = _cfg(tmp_path, cloud_night_tinfoil_effort="xhigh", cloud_night_tinfoil_model="m-1")
    reply = _send(tmp_path, cfg, Factory(client=client))
    assert client.creates[0]["reasoning_effort"] == "xhigh" and client.creates[0]["model"] == "m-1"
    assert reply.model == "m-1" and reply.provider == "tinfoil"


def test_unknown_effort_falls_back_to_max_out_loud(tmp_path, capsys):
    client = FakeClient()
    _send(tmp_path, _cfg(tmp_path, cloud_night_tinfoil_effort="medium"), Factory(client=client))
    assert client.creates[0]["reasoning_effort"] == "max"
    assert "medium" in capsys.readouterr().err


# ── ответ ──

@pytest.mark.parametrize("chunks,why", [
    ([_chunk("", None), _chunk(None, "length")], "finish_reason=length"),
    ([_chunk(SECTIONS), _chunk(None, "length")], "finish_reason=length"),
    ([_chunk(SECTIONS)], "finish_reason=None"),       # чистый обрыв потока без finish (out1 S-M2)
    ([_chunk("   "), _chunk(None, "stop")], "пустой"),
    ([_chunk(SECTIONS + "## Аттестация\n- отпечаток не менялся\n"), _chunk(None, "stop")], "Аттестация"),
    ([_chunk(SECTIONS + "###  аттестация ночного провайдера\n- ок\n"), _chunk(None, "stop")], "Аттестация"),
])
def test_not_a_review_is_rejected(tmp_path, chunks, why):
    with pytest.raises(night_cloud.ReplyRejected, match=why):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=FakeStream(chunks))))
    assert _state(tmp_path)["last_attempt"]["outcome"] == "rejected"


def test_error_mid_stream_is_classified_and_recorded(tmp_path):
    """Обрыв посреди потока — недоступность, а исход всё равно записан (in3 S-I4)."""
    err = type("APIConnectionError", (Exception,), {})("connection reset")
    stream = FakeStream([_chunk("## Противоречия\n"), err])
    with pytest.raises(night_cloud.ProviderUnavailable, match="APIConnectionError"):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    assert stream.closed
    assert _state(tmp_path)["last_attempt"]["outcome"] == "unavailable"


def test_http_error_on_create_names_the_status(tmp_path):
    err = type("BadRequestError", (Exception,), {"status_code": 400})("effort")
    with pytest.raises(night_cloud.ProviderUnavailable, match="HTTP 400"):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=err)))


# ── состояние ──

def test_accepted_reply_records_fingerprint_and_the_change(tmp_path):
    state = tmp_path / "logs" / "night_cloud_state.json"
    reply = _send(tmp_path, _cfg(tmp_path), Factory())
    entry = _state(tmp_path)
    assert entry["last_attempt"]["outcome"] == "delivered" and entry["last_attempt"]["accepted"] is False
    assert entry["last_attempt"]["at"], "метка времени с зоной — бриф судит по давности"
    assert "last_accepted" not in entry, "принятым ответ становится у вызывающего, после проверки секций"
    old = {"date": "2000-01-01", "fingerprint": "cd" * 32, "release": "v0.0.1", "model": "m"}
    data = json.loads(state.read_text(encoding="utf-8"))
    data["G"]["last_accepted"] = old
    state.write_text(json.dumps(data), encoding="utf-8")
    night_cloud.accept(state, "G", reply)
    entry = _state(tmp_path)
    assert entry["previous_accepted"] == old
    assert entry["last_accepted"]["fingerprint"] == "ab" * 32
    assert entry["last_accepted"]["date"] == dt.date.today().isoformat()
    assert entry["last_attempt"]["accepted"] is True and entry["last_attempt"]["outcome"] == "accepted"
    change = entry["fingerprint_change"]
    assert change["from_release"] == "v0.0.1" and change["to_release"] == "v0.0.167" and change["at"]
    assert (state.stat().st_mode & 0o777) == 0o600
    # тот же отпечаток назавтра не стирает запись о смене (out1 H-M2)
    night_cloud.accept(state, "G", reply)
    assert _state(tmp_path)["fingerprint_change"] == change


def test_accept_and_reject_survive_an_unwritable_state(tmp_path, capsys):
    """Сбой записи состояния не выглядит сбоем ревизии (out1 S-M3)."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    reply = night_cloud.Reply(text="t", provider="tinfoil", model="m", attestation="a")
    night_cloud.accept(blocker / "state.json", "G", reply)
    night_cloud.reject(blocker / "state.json", "G", "нет секций")
    assert capsys.readouterr().out.count("не записан") == 2


def test_broken_state_file_is_a_first_run(tmp_path):
    state = tmp_path / "logs" / "night_cloud_state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{обрыв", encoding="utf-8")
    _send(tmp_path, _cfg(tmp_path), Factory())
    assert _state(tmp_path)["last_attempt"]["outcome"] == "delivered"


def test_state_is_kept_per_graph(tmp_path):
    state = tmp_path / "logs" / "night_cloud_state.json"
    for key in ("G1", "G2"):
        night_cloud.send(_cfg(tmp_path), "p", deadline=night_cloud.Deadline.after(30), state_path=state,
                         graph_key=key, transport=Factory())
    assert set(json.loads(state.read_text(encoding="utf-8"))) == {"G1", "G2"}


# ── шаг ядер: коды выхода и развилка провайдера ──

def _load_cores():
    spec = importlib.util.spec_from_file_location("nightly_claude_cores", REPO / "scripts" / "nightly_claude_cores.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def cores_step(tmp_path, monkeypatch):
    charoite_paths.use_data_root(tmp_path / "root", replace=True)
    ncc = _load_cores()
    graph = tmp_path / "graph"
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Ядра" / "Тема.md").write_text("# Тема\n- [[Встречи/x]]\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(ncc, "_cfg", lambda: cfg)
    monkeypatch.setattr(ncc.graphs, "graph_dir", lambda *a, **k: graph)
    monkeypatch.setattr(ncc.live_gate, "night_window_open", lambda *a, **k: True)
    probes = []

    def no_cli(*a, **k):
        probes.append(1)
        raise AssertionError("при tinfoil зонд CLI Claude не нужен")

    monkeypatch.setattr(ncc.cloud, "claude_bin_checked", no_cli)
    monkeypatch.setattr(ncc.subprocess, "run", no_cli)
    monkeypatch.setattr(sys, "argv", ["nightly_claude_cores.py"])
    return types.SimpleNamespace(ncc=ncc, graph=graph, cfg=cfg, probes=probes)


def _exit_code(fn) -> int:
    try:
        fn()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


@pytest.mark.parametrize("error,code", [
    (privacy.PrivacyRefused("нет"), EXIT_PRIVACY_REFUSED),
    (night_cloud.AttestationFailed("подмена"), EXIT_ATTESTATION_FAILED),
    (night_cloud.ProviderUnavailable("сеть"), EXIT_PROVIDER_UNAVAILABLE),
    (night_cloud.ReplyRejected("length"), 2),
    (night_cloud.DeadlineExpired("ночь"), 0),
])
def test_cores_step_maps_door_outcomes_to_exit_codes(cores_step, monkeypatch, error, code):
    def fail(*a, **k):
        raise error

    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", fail)
    assert _exit_code(cores_step.ncc.main) == code
    assert not cores_step.probes
    assert not list(cores_step.graph.glob("Служебное_ночная_ревизия_*.md"))


def test_cores_step_provider_typo_is_a_policy_refusal_before_anything(cores_step, monkeypatch):
    cores_step.cfg["sufler"]["cloud_night_provider"] = "Tinfoil"
    sent = []
    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", lambda *a, **k: sent.append(1))
    assert _exit_code(cores_step.ncc.main) == EXIT_PRIVACY_REFUSED
    assert not sent and not cores_step.probes


def test_cores_step_writes_report_with_attestation_in_frontmatter_and_accepts(cores_step, monkeypatch):
    reply = night_cloud.Reply(text=SECTIONS, provider="tinfoil", model="deepseek-v4-1-flash",
                              attestation="роутер r, релиз v0.0.167,\nотпечаток ab", fingerprint="ab" * 32,
                              release="v0.0.167")
    accepted, sends = [], []
    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", lambda *a, **k: sends.append(k) or reply)
    monkeypatch.setattr(cores_step.ncc.night_cloud, "accept", lambda *a: accepted.append(a))
    assert _exit_code(cores_step.ncc.main) == 0
    (kw,) = sends
    state = charoite_paths.resolve_root(cores_step.ncc.__file__) / "logs" / "night_cloud_state.json"
    assert kw["state_path"] == state and kw["graph_key"] == str(cores_step.graph.resolve())
    assert "transport" not in kw
    assert accepted[0][0] == state and accepted[0][1] == str(cores_step.graph.resolve())
    (rep,) = cores_step.graph.glob("Служебное_ночная_ревизия_*.md")
    text = rep.read_text(encoding="utf-8")
    head = text.split("---\n")[1]
    assert "провайдер: tinfoil" in head and "модель: deepseek-v4-1-flash" in head
    assert "аттестация: роутер r, релиз v0.0.167, отпечаток ab" in head, "в одну строку"
    assert accepted and accepted[0][2] is reply


def test_cores_step_rejects_reply_without_sections_and_records_it(cores_step, monkeypatch):
    reply = night_cloud.Reply(text="просто текст", provider="tinfoil", model="m", attestation="a")
    rejected = []
    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", lambda *a, **k: reply)
    monkeypatch.setattr(cores_step.ncc.night_cloud, "accept", lambda *a: pytest.fail("принят без секций"))
    monkeypatch.setattr(cores_step.ncc.night_cloud, "reject", lambda *a: rejected.append(a))
    assert _exit_code(cores_step.ncc.main) == 2
    assert rejected and "нет секций" in rejected[0][2]


# ── досье при tinfoil ──

def _load_dossier():
    spec = importlib.util.spec_from_file_location("nightly_dossier_review", REPO / "scripts" / "nightly_dossier_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("provider,code", [("tinfoil", 16), ("Tinfoil", EXIT_PRIVACY_REFUSED)])
def test_dossier_main_does_not_send_under_tinfoil_or_typo(tmp_path, monkeypatch, provider, code):
    ndr = _load_dossier()
    monkeypatch.setattr(ndr, "_cfg", lambda: {"sufler": {"cloud_enrich": True, "cloud_night_provider": provider}})
    monkeypatch.setattr(ndr.cloud, "claude_bin_checked", lambda *a, **k: pytest.fail("зонд до развилки"))
    monkeypatch.setattr(ndr.subprocess, "run", lambda *a, **k: pytest.fail("досье ушли в облако"))
    monkeypatch.setattr(sys, "argv", ["nightly_dossier_review.py", "--all-graphs"])
    assert ndr.main() == code


@pytest.mark.parametrize("provider", ["tinfoil", "Tinfoil", None])
def test_dossier_review_exit_refuses_non_anthropic_provider(tmp_path, monkeypatch, provider):
    """Сетевой выход review() сам спрашивает провайдера (in3 DS-I7)."""
    ndr = _load_dossier()
    monkeypatch.setattr(ndr.subprocess, "run", lambda *a, **k: pytest.fail("досье ушли в облако"))
    path = tmp_path / "Тема.md"
    path.write_text("# Тема\n\n## Источники\n- [[2026-07-15_1400]]\n", encoding="utf-8")
    cfg = {"sufler": {"cloud_enrich": True, "cloud_night_provider": provider}}
    fixed, why = ndr.review("Тема", path, tmp_path, {}, [], "m", cfg)
    assert fixed is None and why.startswith("сбой:")


# ── утренний бриф: аттестация только из состояния ──

def _load_brief():
    spec = importlib.util.spec_from_file_location("morning_brief", REPO / "scripts" / "morning_brief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_state(root: pathlib.Path, graph: pathlib.Path, entry: dict) -> None:
    path = root / "logs" / "night_cloud_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({str(graph.resolve()): entry}), encoding="utf-8")


NOW = dt.datetime(2026, 10, 26, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))


def _at(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


def test_brief_reports_failed_attempt_and_changed_fingerprint_from_state(tmp_path):
    root = tmp_path / "root"
    charoite_paths.use_data_root(root, replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()
    # попытка до полуночи, бриф наутро — та же ночь (out1 S-I5, DS-I4)
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "provider": "tinfoil", "outcome": "attestation",
                                                "reason": "SNP measurement mismatch", "accepted": False}})
    (line,) = mb._night_cloud(graph, NOW)
    assert "аттестация провайдера НЕ прошла" in line and "mismatch" in line
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "delivered", "accepted": False}})
    (line,) = mb._night_cloud(graph, NOW)
    assert "не подтверждён" in line and "исход" not in line, "доставлено — не провал (out1 DS-M6)"
    _write_state(root, graph, {"last_attempt": {"at": _at(40), "outcome": "attestation", "accepted": False}})
    assert mb._night_cloud(graph, NOW) == [], "старше 36 ч — не сегодняшняя ночь"
    change = {"at": _at(50), "from_release": "v1", "to_release": "v2", "from": "cd" * 8, "to": "ab" * 8}
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "accepted", "accepted": True},
                               "fingerprint_change": change})
    (line,) = mb._night_cloud(graph, NOW)
    assert "сменился" in line and "v1" in line and "v2" in line, "пропущенный бриф не теряет смену"
    change["at"] = _at(100)
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "accepted", "accepted": True},
                               "fingerprint_change": change})
    assert mb._night_cloud(graph, NOW) == [], "без свежей смены и сбоя — молчим"


def test_brief_ignores_attestation_heading_in_the_report_text(tmp_path):
    """Подделанный моделью раздел не даёт строки: сигнала в состоянии нет."""
    charoite_paths.use_data_root(tmp_path / "root", replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "Служебное_ночная_ревизия_2026-10-26.md").write_text(
        "## Аттестация\n- отпечаток сменился с x\n", encoding="utf-8")
    assert mb._night_cloud(graph, NOW) == []
