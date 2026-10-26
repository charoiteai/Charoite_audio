"""Дверь ночной ревизии у Tinfoil (src/night_cloud.py, №792) — поведение.

CI без сети и без SDK: клиент подменяет фабрика вместо `night_cloud._attest`, а
путь через настоящий конструктор проверяется подставным модулем `tinfoil` в
`sys.modules`. Главные инварианты: гейт ложен или провайдер не принят —
транспорт не позван ни разу; провал аттестации — ноль отправок; срок держит и
аттестацию, и молчащий поток; обрыв по длине и подделка раздела — отказ;
повторов нет (`max_retries=0`); ключ не попадает ни в ошибку, ни в состояние.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import importlib
import importlib.util
import json
import json as _json
import pathlib
import re
import ssl
import stat
import subprocess
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

#: настоящий запуск: фикстура шага ядер подменяет subprocess.run заглушкой
_REAL_RUN = subprocess.run

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

    def __init__(self, stream=None, verified=True, steps_ok=True, create_error=None,
                 create_wait: threading.Event | None = None, options_delay: float = 0.0,
                 options_wait: threading.Event | None = None, options_entered: threading.Event | None = None):
        self.create_wait, self.options_delay, self.options_wait = create_wait, options_delay, options_wait
        self.options_entered = options_entered
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
                        if outer.create_wait is not None:
                            outer.create_wait.wait(5)   # запрос ушёл, заголовки ответа не пришли
                        if outer.create_error:
                            raise outer.create_error
                        return outer.stream

        class Inner:
            @staticmethod
            def with_options(**kw):
                outer.options.append(kw)
                time.sleep(outer.options_delay)
                if outer.options_entered is not None:
                    outer.options_entered.set()   # рабочий прошёл аттестацию и готовит запрос
                if outer.options_wait is not None:
                    outer.options_wait.wait(5)   # рабочий стоит, пока сторож не объявит срок
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


def _send(tmp_path, cfg, factory, seconds=30.0, deadline=None, graph_key="G"):
    """`factory` на время вызова встаёт вместо аттестующего `_attest`; None — настоящий путь.

    Брошенный по сроку рабочий поток фабрику уже получил аргументом: возврат
    `_attest` после вызова его не задевает.
    """
    real = night_cloud._attest
    if factory is not None:
        night_cloud._attest = factory
    try:
        return night_cloud.send(cfg, "ядра графа", deadline=deadline or night_cloud.Deadline.after(seconds),
                                state_path=tmp_path / "logs" / "night_cloud_state.json", graph_key=graph_key)
    finally:
        night_cloud._attest = real


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
    # отказ — тоже исход ночи: бриф видит его, а не прошлую попытку (out2 H-I1)
    attempt = _state(tmp_path)["last_attempt"]
    assert attempt["outcome"] == "refused"
    # факт отправки и текст причины — только в исключении и строке шага (решения по out4, out5)
    assert "sent" not in attempt and "reason" not in attempt


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


def test_step_line_carries_no_home_path(tmp_path):
    """Текст причины уходит только в строку шага — и там без пути с именем учётки (out1 DS-I3)."""
    home_key = pathlib.Path.home() / ".config" / "charoite" / "__нет_ключа_792__"
    cfg = _cfg(tmp_path, cloud_night_tinfoil_key_path=str(home_key))
    with pytest.raises(night_cloud.ProviderUnavailable) as ei:
        _send(tmp_path, cfg, Factory())
    assert str(pathlib.Path.home()) not in str(ei.value)
    assert "~/.config/charoite/__нет_ключа_792__" in str(ei.value)


_HOME_FILE = str(pathlib.Path.home() / "Library" / "__792__" / "cert.pem")
_ACCOUNT = pathlib.Path.home().name


def _no_home(text: str) -> None:
    assert str(pathlib.Path.home()) not in text and "__792__" in text


@pytest.mark.parametrize("error", [
    ValueError(f"no such file: {_HOME_FILE}"),                       # недоступен
    ssl.SSLCertVerificationError(f"cafile {_HOME_FILE}"),            # недоступен: публичный TLS
    type("_PinMismatchError", (ValueError,), {"__module__": "tinfoil.client"})(f"pin {_HOME_FILE}"),  # аттестация
])
def test_provider_cause_carries_no_home_path(tmp_path, error):
    with pytest.raises(night_cloud.NightCloudError) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(error=error))
    _no_home(str(ei.value))


@pytest.mark.parametrize("pad", [170, 180, 185, 190, 200, 260])
def test_long_cause_keeps_no_fragment_of_the_account(tmp_path, pad):
    """Обрезка раньше чистки оставляла «/Users/<обрывок имени>» (out5 C): чистка — на
    целом тексте, потолок — после неё."""
    error = ValueError("x" * pad + " " + _HOME_FILE)
    with pytest.raises(night_cloud.ProviderUnavailable) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(error=error))
    assert _ACCOUNT[:4] not in str(ei.value).replace("x", "")


def test_long_cause_is_capped_after_the_scrub(tmp_path):
    """Тело ответа шлюза может цитировать запрос — строка шага с потолком, и
    путь у самой границы вычищен до обрезки (out6 S-M2, DS-M1)."""
    limit = night_cloud._CAUSE_LIMIT
    error = ValueError("x" * (limit - 40) + " " + _HOME_FILE + " " + "y" * 50_000)
    with pytest.raises(night_cloud.ProviderUnavailable) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(error=error))
    text = str(ei.value)
    assert len(text) < limit + 100 and "обрезано" in text, len(text)
    assert _ACCOUNT[:4] not in text.replace("x", "").replace("y", "")
    assert night_cloud._cause("коротко") == "коротко"


def test_unexpected_error_cause_carries_no_home_path(tmp_path, monkeypatch):
    def broken(cfg, name):
        raise RuntimeError(f"bad config {_HOME_FILE}")

    monkeypatch.setattr(night_cloud.cloud, "model", broken)
    with pytest.raises(night_cloud.ProviderUnavailable) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory())
    _no_home(str(ei.value))


def test_refusal_cause_carries_no_home_path(tmp_path, monkeypatch):
    def refuse(cfg):
        raise privacy.PrivacyRefused(f"политика из {_HOME_FILE}")

    monkeypatch.setattr(night_cloud.privacy, "night_provider", refuse)
    with pytest.raises(privacy.PrivacyRefused) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory())
    _no_home(str(ei.value))


def test_failing_scrub_hides_the_cause_and_keeps_the_class(tmp_path, monkeypatch):
    """Чистка путей упала (нет HOME) — причина скрыта, класс и исход те же (out5 DS-M3)."""
    def no_home(*a, **k):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(night_cloud.privacy, "scrub_local_paths", no_home)
    with pytest.raises(night_cloud.ProviderUnavailable) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(error=ValueError(f"x {_HOME_FILE}")))
    assert "причина скрыта" in str(ei.value) and "__792__" not in str(ei.value)
    assert _state(tmp_path)["last_attempt"]["outcome"] == "unavailable"


# ── форма файла состояния: механизм на класс «строка в граф» (решение главной по out5) ──

#: Лист файла состояния — только такая строка: без пробелов, слешей, кириллицы.
_LEAF = re.compile(r"[A-Za-z0-9._:+-]{0,80}")


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    else:
        yield node


def _assert_state_in_form(tmp_path) -> dict:
    raw = (tmp_path / "logs" / "night_cloud_state.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    for entry in data.values():
        assert set(entry) <= {"last_attempt", "last_accepted", "previous_accepted", "fingerprint_change",
                              "last_attestation_failure"}   # новый раздел — решением, не сам (out8 DS-M1)
        for leaf in _leaves(entry):
            if isinstance(leaf, str):
                assert _LEAF.fullmatch(leaf), f"строка вне формы: {leaf!r}"
            else:
                assert leaf is None or isinstance(leaf, (bool, int)), leaf
        assert "reason" not in entry.get("last_attempt", {})
    return data["G"]


def _api_status(code, text):
    return type("APIStatusError", (Exception,), {"status_code": code})(text)


@pytest.mark.parametrize("cfg_extra,factory,outcome", [
    ({"cloud_enrich": False}, lambda: Factory(), "refused"),
    ({"cloud_night_tinfoil_key_path": _HOME_FILE}, lambda: Factory(), "unavailable"),
    ({}, lambda: Factory(error=ValueError(f"SNP measurement mismatch {_HOME_FILE}")), "unavailable"),
    ({}, lambda: Factory(error=ssl.SSLCertVerificationError(f"cafile {_HOME_FILE}")), "unavailable"),
    ({}, lambda: Factory(error=type("_PinMismatchError", (ValueError,), {"__module__": "tinfoil.client"})(
        f"pin {_HOME_FILE}")), "attestation"),
    ({}, lambda: Factory(client=FakeClient(verified=False)), "attestation"),
    ({}, lambda: Factory(client=FakeClient(create_error=_api_status(503, f"busy {_HOME_FILE}"))), "unavailable"),
    ({}, lambda: Factory(client=FakeClient(stream=FakeStream([_chunk("## Противоречия\n"),
                                                              ConnectionResetError(_HOME_FILE)]))), "unavailable"),
    ({}, lambda: Factory(client=FakeClient(stream=FakeStream([_chunk("a"), _chunk(None, "length")]))), "rejected"),
    ({"cloud_night_tinfoil_model": f"model with spaces {_HOME_FILE}"}, lambda: Factory(), "delivered"),
])
def test_state_after_every_outcome_holds_no_string_outside_its_form(tmp_path, cfg_extra, factory, outcome):
    """После любого исхода в файле только перечисления, коды, отпечатки и метки
    времени — «нет строк вне формы», а не «нет пути» (решение главной по out5)."""
    try:
        _send(tmp_path, _cfg(tmp_path, **cfg_extra), factory())
    except (night_cloud.NightCloudError, privacy.PrivacyRefused):
        pass
    entry = _assert_state_in_form(tmp_path)
    assert entry["last_attempt"]["outcome"] == outcome


def test_state_keeps_http_status_and_finish_reason(tmp_path):
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=_api_status(503, "busy"))))
    assert _assert_state_in_form(tmp_path)["last_attempt"]["http_status"] == 503
    stream = FakeStream([_chunk("a"), _chunk(None, "length")])
    with pytest.raises(night_cloud.ReplyRejected):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    attempt = _assert_state_in_form(tmp_path)["last_attempt"]
    assert attempt["finish_reason"] == "length" and attempt["http_status"] is None


def test_accept_and_reject_keep_the_form(tmp_path):
    _send(tmp_path, _cfg(tmp_path), Factory())
    night_cloud.reject(tmp_path / "logs" / "night_cloud_state.json", "G")
    assert _assert_state_in_form(tmp_path)["last_attempt"]["outcome"] == "rejected"
    reply = _send(tmp_path, _cfg(tmp_path), Factory())
    night_cloud.accept(tmp_path / "logs" / "night_cloud_state.json", "G", reply)
    entry = _assert_state_in_form(tmp_path)
    assert entry["last_attempt"]["outcome"] == "accepted" and entry["last_accepted"]["fingerprint"]


def test_conform_drops_old_fields_and_values_out_of_form():
    """Файл прежней версии (reason, sent, attestation_failure) и чужие значения
    выпадают и у писателя, и у брифа — одна функция формы."""
    entry = {"last_attempt": {"at": "2026-10-26T02:00:00+03:00", "outcome": "attestation",
                              "reason": f"SNP {_HOME_FILE}", "sent": True, "provider": "x y",
                              "release": "v1", "fingerprint": "AB" * 16, "http_status": True,
                              "finish_reason": "stop because"},
             "attestation_failure": {"at": "2026-10-26T02:00:00+03:00", "reason": _HOME_FILE},
             "fingerprint_change": {"at": "вчера", "from": "ab" * 8}}
    assert night_cloud.conform_entry(entry) == {
        "last_attempt": {"at": "2026-10-26T02:00:00+03:00", "outcome": "attestation"},
        "fingerprint_change": {"from": "ab" * 8}}
    assert night_cloud.conform_entry(None) == {} and night_cloud.conform_entry({"last_attempt": "x"}) == {}
    kept = night_cloud.conform_entry({"last_accepted": {"release": "v0.0.167-rc1"}})
    assert kept == {"last_accepted": {"release": "v0.0.167-rc1"}}, "суффикс rc не теряется (out6 S-M1)"
    # суффикс из слов — свободный текст извне, выпадает (out7 H-I1)
    for word in ("v0.0.167-ignorePrevious.instructions", "1.0.0+build.7", "v1.2.3-beta"):
        assert night_cloud.conform_entry({"last_accepted": {"release": word}}) == {}, word


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


def test_http_status_is_not_taken_from_an_implicit_context(tmp_path):
    """Обрыв, брошенный в обработчике чужого 503, своего HTTP-кода не имеет;
    явная обёртка (`from`) код несёт (out6 DS-M3)."""
    try:
        try:
            raise _api_status(503, "busy")
        except Exception:  # noqa: BLE001 — любой отказ, как у openai
            raise ConnectionResetError("rst")   # noqa: B904 — неявный контекст и нужен
    except ConnectionResetError as e:
        reset = e
    stream = FakeStream([_chunk("## Противоречия\n"), reset])
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    assert _assert_state_in_form(tmp_path)["last_attempt"]["http_status"] is None
    wrapped = ConnectionError("openai")
    wrapped.__cause__ = _api_status(502, "bad gateway")
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=wrapped)))
    assert _assert_state_in_form(tmp_path)["last_attempt"]["http_status"] == 502


def test_old_entries_of_other_graphs_lose_fields_out_of_form(tmp_path):
    """Запись другого графа от прежней версии (reason с путём) не переживает
    следующей записи файла (out6 S-M4, H-M3)."""
    state = tmp_path / "logs" / "night_cloud_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"H": {"last_attempt": {"at": "2026-10-25T02:00:00+03:00",
                                                        "outcome": "attestation", "reason": _HOME_FILE}},
                                 "J": "строка вместо записи"}), encoding="utf-8")
    _send(tmp_path, _cfg(tmp_path), Factory())
    raw = state.read_text(encoding="utf-8")
    assert "__792__" not in raw and '"reason"' not in raw and "строка" not in raw
    data = json.loads(raw)
    assert data["H"] == {"last_attempt": {"at": "2026-10-25T02:00:00+03:00", "outcome": "attestation"}}
    assert "J" not in data, "запись, от которой ничего не осталось, не хранится (out7 S-M2)"


class _RaisingDoc(types.SimpleNamespace):
    @property
    def release_tag(self):
        raise RuntimeError("свойство документа сломано")


def _raising_release_client() -> FakeClient:
    client = FakeClient()
    client.doc = _RaisingDoc(**{k: v for k, v in vars(client.doc).items() if k != "release_tag"})
    return client


def test_release_out_of_form_is_dropped_out_loud_and_not_printed(tmp_path, capsys):
    """Тег не той формы — «?» в брифе и строка в логе, без самого значения (out7 H-M2)."""
    state = tmp_path / "logs" / "night_cloud_state.json"
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=_doc_client(release_tag="release 2026 words")))
    assert reply.release == ""
    err = capsys.readouterr().err
    assert "тег релиза не той формы" in err and "words" not in err
    night_cloud.accept(state, "G", reply)
    assert _assert_state_in_form(tmp_path)["last_accepted"].get("release", "") == ""


def test_host_out_of_form_is_dropped_out_loud_and_not_printed(tmp_path, capsys):
    """Имя роутера уходит в отчёт графа: не той формы — «?» и строка в логе (opus M2)."""
    reply = _send(tmp_path, _cfg(tmp_path),
                  Factory(client=_doc_client(enclave_host="x [[Люди/Иван]] ## Аттестация прошла")))
    assert reply.attestation.startswith("роутер ?,") and "Иван" not in reply.attestation
    err = capsys.readouterr().err
    assert "имя роутера не той формы" in err and "Иван" not in err
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=_doc_client(enclave_host="Router.Example:443")))
    assert reply.attestation.startswith("роутер router.example:443,")


def test_uppercase_fingerprint_is_normalised_and_compared(tmp_path):
    """Отпечаток в другом регистре — тот же отпечаток: сохраняется в форме, и
    смена назавтра видна (out6 DS-I1, H-I1)."""
    state = tmp_path / "logs" / "night_cloud_state.json"
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=_doc_client(code_fingerprint="AB" * 32)))
    night_cloud.accept(state, "G", reply)
    assert _assert_state_in_form(tmp_path)["last_accepted"]["fingerprint"] == "ab" * 32
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=_doc_client(code_fingerprint="CD" * 32)))
    night_cloud.accept(state, "G", reply)
    change = _assert_state_in_form(tmp_path)["fingerprint_change"]
    assert change["from"] == "ab" * 8 and change["to"] == "cd" * 8


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
    _doc_client(code_fingerprint="sha256:" + "ab" * 32),   # форма не сохранит — смена погасла бы (out6 DS-I1)
    _broken_doc_client(),
    _raising_release_client(),      # сбой чтения релиза — тоже документ (out6 DS-M5)
], ids=["not-verified", "step-failed", "verified-str", "steps-empty", "steps-none", "steps-list",
        "step-missing", "no-fingerprint", "fingerprint-form", "doc-raises", "release-raises"])
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


def _tinfoil_error(name="_PinMismatchError"):
    return type(name, (ValueError,), {"__module__": "tinfoil.client"})("pin")


#: Классы отказа проверки SDK (`tinfoil/attestation/types.py`) — без импорта SDK.
_SdkAttestationError = type("AttestationError", (Exception,), {"__module__": "tinfoil.attestation.types"})
_SdkMeasurementMismatch = type("MeasurementMismatchError", (_SdkAttestationError,),
                               {"__module__": "tinfoil.attestation.types"})


def _chained(*errors: BaseException) -> BaseException:
    """Цепочка как у SDK: каждое следующее — `__cause__` предыдущего."""
    for outer, inner in zip(errors, errors[1:]):
        outer.__cause__ = inner
    return errors[0]


def _report_error(platform="sev", module=None):
    """Провал отчёта железа SDK 0.15.0: класс не наследник AttestationError."""
    name = {"sev": "SevAttestationError", "tdx": "TdxAttestationError"}[platform]
    module = module or f"tinfoil.attestation.attestation_{platform}"
    return type(name, (Exception,), {"__module__": module})("Attestation verification failed!")


#: requests.ConnectionError и HTTPError в SDK — наследники OSError (опыт e_sev_chain 26.10).
_RequestsConnectionError = type("ConnectionError", (OSError,), {"__module__": "requests.exceptions"})


def _http_error(code: int) -> OSError:
    """requests.HTTPError после raise_for_status: ответ службы ключей с кодом."""
    err = type("HTTPError", (OSError,), {"__module__": "requests.exceptions"})(f"{code} Client Error")
    err.response = types.SimpleNamespace(status_code=code)
    return err


def _vcek_chain(platform: str, cause: BaseException) -> BaseException:
    return _chained(ValueError(f"{platform} attestation verification failed"), _report_error(platform.lower()),
                    ValueError("Failed to fetch certificate"), cause)


def _public_tls_failure() -> BaseException:
    """Сбой сертификата на публичном запросе конструктора — цепочка как в опыте
    с пустым набором CA (26.10): ValueError ← URLError ← SSLCertVerificationError,
    звенья — неявным контекстом, как у urllib и SDK."""
    import urllib.error
    try:
        try:
            try:
                raise ssl.SSLCertVerificationError(1, "certificate verify failed: Hostname mismatch")
            except OSError as err:
                raise urllib.error.URLError(err)
        except urllib.error.URLError as u:
            raise ValueError(f"Failed to fetch router addresses: {u}")
    except ValueError as e:
        return e


@pytest.mark.parametrize("error,cls", [
    # голый сбой сертификата — сеть: портал Wi-Fi, прокси, часы (opus I1, out8)
    (ssl.SSLError("CERTIFICATE_VERIFY_FAILED"), night_cloud.ProviderUnavailable),
    (ssl.SSLCertVerificationError("self-signed"), night_cloud.ProviderUnavailable),
    (_public_tls_failure(), night_cloud.ProviderUnavailable),
    (_chained(ValueError("SEV attestation verification failed"), _report_error("sev"),
              ValueError("Failed to fetch VCEK certificate"), ssl.SSLCertVerificationError("KDS")),
     night_cloud.ProviderUnavailable),
    # закреплённый ключ энклава не тот — аттестация на любой стадии (out1 S-I3, H-I2)
    (_tinfoil_error(), night_cloud.AttestationFailed),
    # отказ проверки SDK: релиз не тот в режиме Sigstore (опыт wrong_repo, out5)
    (_SdkMeasurementMismatch("measurement mismatch"), night_cloud.AttestationFailed),
    (_SdkAttestationError("bad bundle"), night_cloud.AttestationFailed),
    # отчёт железа не прошёл (подпись, измерение) — аттестация; цепочка как у SDK:
    # ValueError ← SevAttestationError (опыт e_sev_chain 26.10, out6)
    (_chained(ValueError("SEV attestation verification failed"), _report_error("sev")),
     night_cloud.AttestationFailed),
    (_chained(ValueError("TDX attestation verification failed"), _report_error("tdx")),
     night_cloud.AttestationFailed),
    # тот же класс SDK, но причина — сеть к KDS или коллатералу: недоступен
    (_chained(ValueError("SEV attestation verification failed"), _report_error("sev"),
              ValueError("Failed to fetch VCEK certificate"), _RequestsConnectionError("KDS")),
     night_cloud.ProviderUnavailable),
    (_chained(ValueError("TDX attestation verification failed"), _report_error("tdx"),
              type("CollateralError", (Exception,), {})("HTTP GET failed"), TimeoutError("read")),
     night_cloud.ProviderUnavailable),
    # имя класса не из модуля SDK — не аттестация
    (_chained(ValueError("x"), _report_error("sev", module="otherlib.sev")), night_cloud.ProviderUnavailable),
    # ответ службы ключей пришёл: 4xx — по данным отчёта (выдуманный chip_id), это провал
    # отчёта; 5xx, 408, 429 — служба занята (out7 S-I1)
    (_vcek_chain("SEV", _http_error(404)), night_cloud.AttestationFailed),
    (_vcek_chain("TDX", _http_error(400)), night_cloud.AttestationFailed),
    (_vcek_chain("SEV", _http_error(503)), night_cloud.ProviderUnavailable),
    (_vcek_chain("SEV", _http_error(429)), night_cloud.ProviderUnavailable),
    # служба не пустила (прокси, WAF, подписка) — не ответ об отчёте: белый список 400/404 (out8 I1)
    (_vcek_chain("SEV", _http_error(403)), night_cloud.ProviderUnavailable),
    (_vcek_chain("TDX", _http_error(401)), night_cloud.ProviderUnavailable),
    (_vcek_chain("SEV", _http_error(407)), night_cloud.ProviderUnavailable),
    # ввод-вывод без ответа (кэш на диске) — недоступен; ответ не читается — аттестация (out7 DS-I1)
    (_vcek_chain("SEV", FileNotFoundError("cache")), night_cloud.ProviderUnavailable),
    (_vcek_chain("SEV", _json.JSONDecodeError("Expecting value", "", 0)), night_cloud.AttestationFailed),
    # тот же класс не из SDK — не аттестация: решает модуль, а не имя
    (type("AttestationError", (Exception,), {"__module__": "otherlib.errors"})("x"),
     night_cloud.ProviderUnavailable),
    # прочее — недоступен, и голый ValueError SDK о несовпадении замера тоже:
    # своих эвристик сверх правила SDK нет (решение главной по out4, п. 2);
    # отправки в обоих классах нет — конструктор не вернул клиента
    (ValueError("SNP measurement mismatch"), night_cloud.ProviderUnavailable),
    (type("MeasurementMismatchError", (Exception,), {})("code differs"), night_cloud.ProviderUnavailable),
    (ConnectionError("refused"), night_cloud.ProviderUnavailable),
    (ValueError("Failed to fetch router addresses: <urlopen error>"), night_cloud.ProviderUnavailable),
    (type("SSLError", (ConnectionError,), {})("certificate verify failed"), night_cloud.ProviderUnavailable),
    (_json.JSONDecodeError("Expecting value", "", 0), night_cloud.ProviderUnavailable),
    (ModuleNotFoundError("No module named 'sigstore'"), night_cloud.ProviderUnavailable),
])
def test_constructor_errors_are_classified(tmp_path, monkeypatch, error, cls):
    """Два класса по правилам SDK; что бы ни случилось в конструкторе — `create` нет."""
    _fake_tinfoil(monkeypatch, error=error)
    with pytest.raises(cls) as ei:
        _send(tmp_path, _cfg(tmp_path), None)
    assert type(ei.value) is cls and ei.value.sent is False
    # слово «аттестация» — только у класса аттестации (out5 DS-M2)
    assert str(ei.value).startswith("аттестация") is (cls is night_cloud.AttestationFailed)


def test_attesting_constructor_asks_the_gate_itself(tmp_path, monkeypatch):
    """`_attest` — выход SDK в реестре: гейт свой, не только у send (out1 S-M2)."""
    built = []
    mod = types.ModuleType("tinfoil")
    mod.TinfoilAI = lambda api_key: built.append(api_key)
    monkeypatch.setitem(sys.modules, "tinfoil", mod)
    with pytest.raises(privacy.PrivacyRefused):
        night_cloud._attest(_cfg(tmp_path, cloud_enrich=False), KEY)
    assert not built


def _ehbp_tls_failure() -> BaseException:
    """Сбой публичного TLS на create в режиме SDK по умолчанию (ehbp) — цепочка
    из опыта e_create_ca 26.10: APIConnectionError ← ConnectError ← ConnectError
    ← SSLCertVerificationError."""
    connect = type("ConnectError", (Exception,), {"__module__": "httpx"})
    return _chained(type("APIConnectionError", (Exception,), {"__module__": "openai"})("Connection error."),
                    connect("x"), connect("y"), ssl.SSLCertVerificationError(1, "certificate verify failed"))


@pytest.mark.parametrize("error,cls", [
    (_tinfoil_error(), night_cloud.AttestationFailed),     # закреплённый ключ энклава не тот
    # голый сбой сертификата и на create — публичный TLS, не подмена (out8, опыт e_create_ca)
    (ssl.SSLCertVerificationError("self-signed"), night_cloud.ProviderUnavailable),
    (ssl.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"), night_cloud.ProviderUnavailable),
    (_ehbp_tls_failure(), night_cloud.ProviderUnavailable),
    # обрыв TLS при чтении заголовков — ввод-вывод, не подмена (правило SDK, out3 S-I1, DS-I2)
    (ssl.SSLError("bad record mac"), night_cloud.ProviderUnavailable),
    (ssl.SSLEOFError("eof"), night_cloud.ProviderUnavailable),
    (type("APIStatusError", (Exception,), {"status_code": 503})("busy"), night_cloud.ProviderUnavailable),
])
def test_chat_stage_errors_are_classified(tmp_path, error, cls):
    """Отказ на `create`: отправка уже начата — `sent` в исключении, класс — по тому же правилу."""
    with pytest.raises(cls) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=error)))
    assert type(ei.value) is cls and ei.value.sent is True


@pytest.mark.parametrize("error,cls,outcome", [
    (ssl.SSLError("bad record mac"), night_cloud.ProviderUnavailable, "unavailable"),
    (ConnectionResetError("rst"), night_cloud.ProviderUnavailable, "unavailable"),
    # правило SDK одно на все стадии: несовпадение ключа посреди ответа — аттестация (out3 H-C1)
    (_tinfoil_error(), night_cloud.AttestationFailed, "attestation"),
])
def test_errors_while_the_reply_streams(tmp_path, error, cls, outcome):
    """Ответ уже идёт — обрыв посреди потока не «ничего не отправлено» (out2 C2)."""
    stream = FakeStream([_chunk("## Противоречия\n"), error])
    with pytest.raises(cls) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    assert ei.value.sent is True and stream.closed
    assert _state(tmp_path)["last_attempt"]["outcome"] == outcome


def test_class_follows_the_sdk_cause_chain():
    """Цепочка — как у SDK: `__cause__ or __context__`, и `from None` её не прячет
    (`_is_certificate_error` 0.15.0; решение главной по out5)."""
    pin = _tinfoil_error
    try:
        try:
            raise pin()
        except ValueError:
            raise ValueError("HTTP 503") from None
    except ValueError as e:
        hidden = e
    assert night_cloud._classify(hidden) is night_cloud.AttestationFailed
    try:
        try:
            raise pin()
        except ValueError:
            raise ValueError("ошибка в обработчике")   # неявный контекст — тоже цепочка SDK
    except ValueError as e:
        implicit = e
    assert night_cloud._classify(implicit) is night_cloud.AttestationFailed
    assert night_cloud._classify(ValueError("HTTP 503")) is night_cloud.ProviderUnavailable
    explicit = _chained(ConnectionError("httpx: connect"), pin())   # явная причина — решает
    assert night_cloud._classify(explicit) is night_cloud.AttestationFailed
    # голый сбой сертификата в цепочке — сеть на любой стадии (out8)
    assert night_cloud._classify(_chained(ConnectionError("c"), ssl.SSLCertVerificationError("x"))) \
        is night_cloud.ProviderUnavailable


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
        with pytest.raises(night_cloud.DeadlineExpired, match="во время аттестации") as ei:
            _send(tmp_path, _cfg(tmp_path), factory, seconds=0.2)
        # имя, по которому тесты ждут рабочего, — имя живого потока двери (out7 DS-M4)
        assert any(t.name == night_cloud.EXCHANGE_THREAD for t in threading.enumerate())
    finally:
        release.set()
    assert time.monotonic() - started < 3
    _join_exchange()   # брошенный рабочий проснулся и вышел: отправить он уже не вправе
    assert not factory.client.creates
    assert ei.value.sent is False
    assert _state(tmp_path)["last_attempt"]["outcome"] == "deadline"


def _join_exchange(timeout: float = 5.0) -> None:
    """Дождаться выхода рабочих двери: «create не вызван» проверяется, когда
    рабочий уже вышел, а не после паузы — медленный планировщик не прячет
    мутанта (out6 S-I1, H-I2)."""
    for t in threading.enumerate():
        if t.name == night_cloud.EXCHANGE_THREAD:
            t.join(timeout)
            assert not t.is_alive(), "рабочий двери не вышел — проверка отправки ничего не доказывает"


def _split_clock(seconds: float, hold: threading.Event | None = None):
    """Срок, который истекает только у сторожа: в главном потоке часы настоящие,
    в рабочем стоят на старте — остановить отправку рабочего может только
    отмена сторожа, не его собственная проверка срока.

    `hold` взведён — первый вызов часов рабочим после `armed` (он внутри
    перехода к отправке) отмечает `inside` и ждёт `hold` до 2 с. Часы сторожа,
    пока рабочий этого теста жив, ждут `inside`: срок объявляется, когда рабочий
    уже внутри перехода, по событию, а не по запасу времени (out5 DS-I1). Без
    `hold` событие `inside` ставит сам тест (например, из `with_options`)."""
    main, start = threading.get_ident(), time.monotonic()
    before = {t.ident for t in threading.enumerate()}
    armed, inside = [], threading.Event()

    def clock():
        if threading.get_ident() == main:
            if any(t.name == night_cloud.EXCHANGE_THREAD and t.ident not in before
                   for t in threading.enumerate()):
                inside.wait(3)
            return time.monotonic()
        if hold is not None and armed and not armed.pop():
            inside.set()
            hold.wait(2)
        return start

    return night_cloud.Deadline(start + seconds, clock), armed, inside


def test_deadline_between_attestation_and_create_sends_nothing(tmp_path):
    """Сторож объявил срок, пока рабочий готовил запрос: `create` после этого
    не начинается — его не пускает отмена под замком, а не своя проверка срока
    рабочего (out2 C1, out4 Important 5 — сторож поведением, не текстом)."""
    release = threading.Event()
    deadline, _, inside = _split_clock(0.3)
    client = FakeClient(options_wait=release, options_entered=inside)
    try:
        with pytest.raises(night_cloud.DeadlineExpired, match="до отправки") as ei:
            _send(tmp_path, _cfg(tmp_path), Factory(client=client), deadline=deadline)
    finally:
        release.set()   # срок объявлен — рабочий идёт к замку отправки
    _join_exchange()   # рабочий дошёл до замка отправки, увидел отмену и вышел
    assert not client.creates, "запрос ушёл после того, как сторож объявил срок"
    assert ei.value.sent is False


def test_send_flag_and_cancel_meet_under_one_lock(tmp_path):
    """Рабочий застрял внутри перехода к отправке, сторож в это время объявил срок.
    Под одним замком исход один из двух: сторож дождался и знает, что запрос ушёл,
    или отправки нет. «Ничего не отправлено» при начатом `create` — нарушение (out2 C1)."""
    hold = threading.Event()

    class Client(FakeClient):
        def __init__(self):
            super().__init__()
            inner = self.client

            class Hooked:
                @staticmethod
                def with_options(**kw):
                    armed.append(False)   # следующий вызов часов рабочим — внутри перехода
                    return inner.with_options(**kw)

            self.client = Hooked

    client = Client()
    deadline, armed, _ = _split_clock(0.3, hold)
    try:
        with pytest.raises(night_cloud.DeadlineExpired) as ei:
            _send(tmp_path, _cfg(tmp_path), Factory(client=client), deadline=deadline)
    finally:
        hold.set()
    _join_exchange()   # без замка рабочий отпущен только теперь — и отправил бы до выхода
    assert armed == [], "часы рабочего не вызывались внутри перехода — опыт не про замок"
    assert ei.value.sent is bool(client.creates), "исход о срок и факт отправки разошлись"


def test_deadline_after_the_request_left_says_so(tmp_path):
    """Срок вышел, пока ждём заголовков: запрос уже ушёл — `sent` честный, а
    поток, пришедший после отмены, закрывает сам рабочий (out2 C1)."""
    release = threading.Event()
    # сервер после заголовков молчит: без проверки отмены рабочий ждал бы первого куска
    stream = FakeStream([_chunk(SECTIONS), _chunk(None, "stop")], between=lambda: time.sleep(3))
    client = FakeClient(stream=stream, create_wait=release)
    try:
        with pytest.raises(night_cloud.DeadlineExpired, match="после отправки запроса") as ei:
            _send(tmp_path, _cfg(tmp_path), Factory(client=client), seconds=0.3)
    finally:
        release.set()
    assert ei.value.sent is True and len(client.creates) == 1
    assert _state(tmp_path)["last_attempt"]["outcome"] == "deadline"
    for _ in range(30):
        if stream.closed:
            break
        time.sleep(0.05)
    assert stream.closed, "поток после отмены закрыт рабочим сразу, не читается"


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
        with pytest.raises(night_cloud.DeadlineExpired, match="после отправки запроса"):
            _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)), seconds=0.3)
        # рабочий ещё стоит в between: закрыть поток мог только сторож (out2 DS-M3)
        assert stream.closed, "поток закрыт сторожем по сроку"
    finally:
        release.set()
    assert time.monotonic() - started < 3
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
    # заголовок Setext: Obsidian показывает его так же, как «##» (opus M1)
    ([_chunk(SECTIONS + "Аттестация\n---\n- прошла, отпечаток прежний\n"), _chunk(None, "stop")], "Аттестация"),
    ([_chunk(SECTIONS + "  аттестация провайдера\n=====\n"), _chunk(None, "stop")], "Аттестация"),
    # внутри цитаты и пункта списка Obsidian рисует заголовок так же (out8 H-I2)
    ([_chunk(SECTIONS + "> ## Аттестация прошла\n"), _chunk(None, "stop")], "Аттестация"),
    ([_chunk(SECTIONS + "- ## Аттестация\n"), _chunk(None, "stop")], "Аттестация"),
    ([_chunk(SECTIONS + "1. # Аттестация\n"), _chunk(None, "stop")], "Аттестация"),
    ([_chunk(SECTIONS + "> Аттестация\n> ---\n"), _chunk(None, "stop")], "Аттестация"),
])
def test_not_a_review_is_rejected(tmp_path, chunks, why):
    with pytest.raises(night_cloud.ReplyRejected, match=why):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=FakeStream(chunks))))
    assert _state(tmp_path)["last_attempt"]["outcome"] == "rejected"


@pytest.mark.parametrize("tail", [
    "#аттестация сотрудников\n",                                  # хэштег Obsidian, не заголовок
    "Аттестация сотрудников прошла в марте.\n\n---\n",           # линия после пустой строки
    "    ## аттестация в блоке кода\n",                           # отступ 4 — код
])
def test_the_word_in_ordinary_markup_is_not_a_forged_heading(tmp_path, tail):
    """Слово «аттестация» в обычной разметке ревизию не отвергает (out8 S-M1, H-M1)."""
    reply = _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(
        stream=FakeStream([_chunk(SECTIONS + tail), _chunk(None, "stop")]))))
    assert tail.strip() in reply.text


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
    assert dt.datetime.fromisoformat(entry["last_attempt"]["at"]).tzinfo is not None, \
        "метка времени с зоной — бриф судит по давности"
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
    night_cloud.reject(blocker / "state.json", "G")
    assert capsys.readouterr().out.count("не записан") == 2


@pytest.mark.parametrize("fail", [RuntimeError("журнал"), TypeError("json"), OSError("диск")])
def test_state_write_failure_never_replaces_the_outcome(tmp_path, monkeypatch, capsys, fail):
    """Запись исхода стоит в finally: любой её сбой не подменяет класс (out2 DS-M1)."""
    def broken(*a, **k):
        raise fail

    monkeypatch.setattr(night_cloud, "_update_state", broken)
    assert _send(tmp_path, _cfg(tmp_path), Factory()).text.startswith("## Противоречия")
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(create_error=ConnectionResetError())))
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
        _send(tmp_path, _cfg(tmp_path), Factory(), graph_key=key)
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


@pytest.mark.parametrize("cls,code", [
    (night_cloud.AttestationFailed, EXIT_ATTESTATION_FAILED),
    (night_cloud.ProviderUnavailable, EXIT_PROVIDER_UNAVAILABLE),
    (night_cloud.DeadlineExpired, 0),
])
@pytest.mark.parametrize("sent,said", [(False, "ничего не отправлено"), (True, "запрос мог уйти")])
def test_cores_step_says_whether_the_request_left(cores_step, monkeypatch, capsys, cls, code, sent, said):
    """Факт отправки — только в строке шага; «ничего не отправлено» — только когда
    отправка не начиналась (out2 C2; решение главной по out4, п. 1)."""
    error = cls("TLS")
    error.sent = sent

    def fail(*a, **k):
        raise error

    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", fail)
    assert _exit_code(cores_step.ncc.main) == code
    out = capsys.readouterr().err   # причина и факт отправки — в stderr шага, лог ночи
    assert said in out and ("ничего не отправлено" in out) is (not sent)


def test_cores_step_wrong_release_is_code_14_and_sends_nothing(cores_step, monkeypatch, capsys):
    """Релиз роутера не тот (SDK: MeasurementMismatchError, режим Sigstore) —
    код 14 «аттестация», ни одного `create` (опыт wrong_repo, out5)."""
    factory = Factory(error=_SdkMeasurementMismatch("measurement mismatch"))
    monkeypatch.setattr(cores_step.ncc.night_cloud, "_attest", factory)
    assert _exit_code(cores_step.ncc.main) == EXIT_ATTESTATION_FAILED
    assert factory.calls == 1 and not factory.client.creates
    assert "ничего не отправлено" in capsys.readouterr().err


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
    assert set(kw) == {"deadline", "state_path", "graph_key"}
    assert accepted[0][0] == state and accepted[0][1] == str(cores_step.graph.resolve())
    (rep,) = cores_step.graph.glob("Служебное_ночная_ревизия_*.md")
    text = rep.read_text(encoding="utf-8")
    head = text.split("---\n")[1]
    assert "провайдер: tinfoil" in head and "модель: deepseek-v4-1-flash" in head
    assert "аттестация: роутер r, релиз v0.0.167, отпечаток ab" in head, "в одну строку"
    assert accepted and accepted[0][2] is reply


def test_cores_step_rejects_reply_without_sections_and_records_it(cores_step, monkeypatch, capsys):
    reply = night_cloud.Reply(text="просто текст", provider="tinfoil", model="m", attestation="a")
    rejected = []
    monkeypatch.setattr(cores_step.ncc.night_cloud, "send", lambda *a, **k: reply)
    monkeypatch.setattr(cores_step.ncc.night_cloud, "accept", lambda *a: pytest.fail("принят без секций"))
    monkeypatch.setattr(cores_step.ncc.night_cloud, "reject", lambda *a: rejected.append(a))
    assert _exit_code(cores_step.ncc.main) == 2
    assert rejected and len(rejected[0]) == 2, "в состояние — только исход, без текста"
    assert "нет секций" in capsys.readouterr().out


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


def test_wall_clock_end_of_night_stops_the_send(tmp_path, monkeypatch):
    """Монотонный срок во сне стоит: проснулись после конца ночи — не отправляем (opus M3)."""
    monkeypatch.setenv("CHAROITE_NIGHTLY_UNTIL", str(time.time() - 60))
    client = FakeClient()
    with pytest.raises(night_cloud.DeadlineExpired) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(client=client))
    assert ei.value.sent is False and client.creates == []
    assert _state(tmp_path)["last_attempt"]["outcome"] == "deadline"
    monkeypatch.setenv("CHAROITE_NIGHTLY_UNTIL", str(time.time() + 3600))
    _send(tmp_path, _cfg(tmp_path), Factory(client=client))
    assert len(client.creates) == 1


def test_wall_clock_end_of_night_stops_the_stream(tmp_path, monkeypatch):
    """Сон посреди ответа: стенной потолок проверяется и в потоке (out8 DS-M4)."""
    monkeypatch.setenv("CHAROITE_NIGHTLY_UNTIL", str(time.time() + 3600))
    def night_ends():
        monkeypatch.setenv("CHAROITE_NIGHTLY_UNTIL", str(time.time() - 60))
    stream = FakeStream([_chunk("## Противоречия\n"), _chunk(SECTIONS), _chunk(None, "stop")], between=night_ends)
    with pytest.raises(night_cloud.DeadlineExpired) as ei:
        _send(tmp_path, _cfg(tmp_path), Factory(client=FakeClient(stream=stream)))
    assert ei.value.sent is True and stream.closed


def test_report_service_status_reaches_the_state(tmp_path):
    """Код ответа службы ключей (requests `response.status_code`) — в состоянии (out8 DS-M3)."""
    with pytest.raises(night_cloud.AttestationFailed):
        _send(tmp_path, _cfg(tmp_path), Factory(error=_vcek_chain("SEV", _http_error(404))))
    assert _state(tmp_path)["last_attempt"]["http_status"] == 404


def test_attestation_failure_outlives_the_next_attempt(tmp_path):
    """Провал аттестации — отдельный раздел: следующая попытка его не стирает (opus I2)."""
    with pytest.raises(night_cloud.AttestationFailed):
        _send(tmp_path, _cfg(tmp_path), Factory(error=_SdkMeasurementMismatch("x")))
    first = _assert_state_in_form(tmp_path)["last_attestation_failure"]
    assert first["at"] == _state(tmp_path)["last_attempt"]["at"]
    state = tmp_path / "logs" / "night_cloud_state.json"
    night_cloud.accept(state, "G", _send(tmp_path, _cfg(tmp_path), Factory()))
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(error=ConnectionError("refused")))
    entry = _assert_state_in_form(tmp_path)
    assert entry["last_attestation_failure"] == first
    assert entry["last_attempt"]["outcome"] == "unavailable"
    # другой исход раздела не заводит вовсе
    with pytest.raises(night_cloud.ProviderUnavailable):
        _send(tmp_path, _cfg(tmp_path), Factory(error=ConnectionError("refused")), graph_key="H")
    data = json.loads((tmp_path / "logs" / "night_cloud_state.json").read_text(encoding="utf-8"))
    assert "last_attestation_failure" not in data["H"]


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
                                                "reason": f"SNP measurement mismatch {_HOME_FILE}",
                                                "http_status": 403, "accepted": False}})
    (line,) = mb._night_cloud(graph, NOW)
    assert "аттестация провайдера НЕ прошла" in line and "HTTP 403" in line
    assert "mismatch" not in line and "__792__" not in line, "текст причины в бриф не идёт (out5)"
    # поле, которое бриф печатает, но не той формы, — выпадает и при чтении (одна форма на оба конца)
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "provider": f"tinfoil {_HOME_FILE}",
                                                "outcome": "unavailable", "accepted": False},
                               "fingerprint_change": {"at": _at(9), "from_release": _HOME_FILE,
                                                      "to_release": "v0.0.167", "from": "cd" * 8, "to": "ab" * 8}})
    lines = mb._night_cloud(graph, NOW)
    assert len(lines) == 2 and not any("__792__" in x for x in lines), lines
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "delivered", "accepted": False}})
    (line,) = mb._night_cloud(graph, NOW)
    assert "не подтверждён" in line and "исход" not in line, "доставлено — не провал (out1 DS-M6)"
    _write_state(root, graph, {"last_attempt": {"at": _at(40), "outcome": "attestation", "accepted": False}})
    assert mb._night_cloud(graph, NOW) == [], "старше 36 ч — не сегодняшняя ночь"
    change = {"at": _at(50), "from_release": "v0.0.166", "to_release": "v0.0.167", "from": "cd" * 8, "to": "ab" * 8}
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "accepted", "accepted": True},
                               "fingerprint_change": change})
    (line,) = mb._night_cloud(graph, NOW)
    assert "сменился" in line and "v0.0.166" in line and "v0.0.167" in line, "пропущенный бриф не теряет смену"
    change["at"] = _at(100)
    _write_state(root, graph, {"last_attempt": {"at": _at(9), "outcome": "accepted", "accepted": True},
                               "fingerprint_change": change})
    assert mb._night_cloud(graph, NOW) == [], "без свежей смены и сбоя — молчим"


def test_brief_keeps_an_attestation_failure_for_the_change_window(tmp_path):
    """Сигнал подмены живёт 72 ч, как смена отпечатка, и не дублирует строку попытки (opus I2)."""
    root = tmp_path / "root"
    charoite_paths.use_data_root(root, replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()
    failure = {"at": _at(30), "date": "2026-10-25", "fingerprint": "ab" * 32, "release": "v0.0.167",
               "http_status": None, "reason": f"text {_HOME_FILE}"}
    _write_state(root, graph, {"last_attempt": {"at": _at(6), "outcome": "accepted", "accepted": True},
                               "last_attestation_failure": failure})
    (line,) = mb._night_cloud(graph, NOW)
    assert "аттестация ночного провайдера не прошла 2026-10-25" in line and "v0.0.167" in line
    assert "abababababababab" in line and "__792__" not in line
    # та же попытка свежая и не принята — одна строка, не две
    _write_state(root, graph, {"last_attempt": {"at": _at(30), "outcome": "attestation", "accepted": False},
                               "last_attestation_failure": failure})
    (line,) = mb._night_cloud(graph, NOW)
    assert "аттестация провайдера НЕ прошла" in line
    # свежая попытка — другой провал: прежний остаётся своей строкой
    _write_state(root, graph, {"last_attempt": {"at": _at(6), "outcome": "attestation", "accepted": False},
                               "last_attestation_failure": failure})
    assert len(mb._night_cloud(graph, NOW)) == 2
    failure["at"] = _at(50)       # окно 72 ч, а не 36 ч попытки
    _write_state(root, graph, {"last_attempt": {"at": _at(6), "outcome": "accepted", "accepted": True},
                               "last_attestation_failure": failure})
    assert len(mb._night_cloud(graph, NOW)) == 1
    failure["at"] = _at(73)
    _write_state(root, graph, {"last_attempt": {"at": _at(6), "outcome": "accepted", "accepted": True},
                               "last_attestation_failure": failure})
    assert mb._night_cloud(graph, NOW) == [], "старше 72 ч — молчим"


def test_every_outcome_word_has_a_brief_phrase():
    """Слово исхода без фразы бриф напечатал бы как «исход не записан» — как битую
    запись (out6 DS-M6). «accepted» — молчание, фразы ему не нужно."""
    mb = _load_brief()
    assert set(night_cloud.OUTCOMES) - {"accepted"} == set(mb._NIGHT_OUTCOMES)


def test_brief_ignores_attestation_heading_in_the_report_text(tmp_path):
    """Подделанный моделью раздел не даёт строки: сигнала в состоянии нет."""
    charoite_paths.use_data_root(tmp_path / "root", replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "Служебное_ночная_ревизия_2026-10-26.md").write_text(
        "## Аттестация\n- отпечаток сменился с x\n", encoding="utf-8")
    assert mb._night_cloud(graph, NOW) == []


def test_brief_never_says_whether_the_request_left(tmp_path):
    """Бриф читает исход, не факт отправки: тот — в строке шага (решение главной
    по out4, п. 1). Старое поле `sent` в файле ничего не меняет."""
    root = tmp_path / "root"
    charoite_paths.use_data_root(root, replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()
    for outcome in ("attestation", "deadline", "unavailable"):
        for sent in (True, False, None):
            attempt = {"at": _at(9), "outcome": outcome, "accepted": False}
            if sent is not None:
                attempt["sent"] = sent
            _write_state(root, graph, {"last_attempt": attempt})
            (line,) = mb._night_cloud(graph, NOW)
            assert mb._NIGHT_OUTCOMES[outcome] in line
            assert "отправлено" not in line and "мог уйти" not in line


def test_brief_windows_and_a_clock_that_went_back(tmp_path):
    """Окна 36 и 72 ч по краям; метка из будущего — часы ушли назад, не «старое» (out2 DS-M5, H-M3)."""
    root = tmp_path / "root"
    charoite_paths.use_data_root(root, replace=True)
    mb = _load_brief()
    graph = tmp_path / "g"
    graph.mkdir()

    def lines(attempt_h, change_h=None):
        entry = {"last_attempt": {"at": _at(attempt_h), "outcome": "unavailable", "accepted": False}}
        if change_h is not None:
            entry = {"last_attempt": {"at": _at(1), "outcome": "accepted", "accepted": True},
                     "fingerprint_change": {"at": _at(change_h), "from": "a", "to": "b"}}
        _write_state(root, graph, entry)
        return mb._night_cloud(graph, NOW)

    assert len(lines(35.5)) == 1 and lines(36.5) == []
    assert len(lines(-2)) == 1, "метка на 2 ч впереди часов — всё равно сегодняшняя ночь"
    assert len(lines(0, change_h=71.5)) == 1 and lines(0, change_h=72.5) == []
    assert len(lines(0, change_h=-1)) == 1
    assert lines(-25) == [] and lines(0, change_h=-500) == [], "метка далеко впереди — сбой часов, не вечная тревога"


# ── доктор: провайдер ночи ──

def _doctor(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import doctor
    monkeypatch.setattr(doctor, "issues", 0)
    return doctor


def test_doctor_names_stray_night_keys_and_refuses_a_typo(tmp_path, monkeypatch, capsys):
    """Опечатка в имени ключа = ключа нет = ночь молча в Anthropic (out1 H-I3)."""
    doctor = _doctor(monkeypatch)
    doctor.check_night_provider({"sufler": {"cloud_night_provdier": "tinfoil", "cloud_night_provider": "Tinfoil",
                                            "cloud_night_tinfoil_model": "m"},
                                 "cloud_night_provider": "tinfoil"})
    out = capsys.readouterr().out
    assert "незнакомые ключи ночного провайдера: cloud_night_provdier, cloud_night_provider (вне sufler)" in out
    assert "cloud_night_tinfoil_model" not in out.split("незнакомые")[1].split("\n")[0], "ключ из DEFAULTS знаком"
    assert "провайдер ночи не принят" in out


def test_doctor_warns_about_a_key_without_the_provider(tmp_path, monkeypatch, capsys):
    doctor = _doctor(monkeypatch)
    key = tmp_path / "tinfoil_key"
    key.write_text("k", encoding="utf-8")
    doctor.check_night_provider({"sufler": {"cloud_night_tinfoil_key_path": str(key)}})
    out = capsys.readouterr().out
    assert "есть ключ Tinfoil" in out and "незнакомые" not in out


# ── мутанты CI #857: границы, запись состояния, ветка anthropic шага ядер ──

class _Log:
    """Поток лога ночи: строка видна читателю только после flush — как у
    stdout/stderr, перенаправленных в файл лога."""

    def __init__(self):
        self.pending, self.seen = "", ""

    def write(self, s):
        self.pending += s
        return len(s)

    def flush(self):
        self.seen, self.pending = self.seen + self.pending, ""


def _chain(n: int, last: BaseException) -> BaseException:
    """Цепочка `__cause__` из n исключений; `last` — n-е звено."""
    errs = [ValueError(f"звено {i}") for i in range(n - 1)] + [last]
    for a, b in zip(errs, errs[1:]):
        a.__cause__ = b
    return errs[0]


def _fixed(remaining: float) -> night_cloud.Deadline:
    """Срок, у которого остаток не меняется: часы стоят."""
    return night_cloud.Deadline(at=100.0 + remaining, clock=lambda: 100.0)


def test_door_error_raised_outside_send_claims_nothing_sent():
    for cls in (night_cloud.AttestationFailed, night_cloud.ProviderUnavailable,
                night_cloud.ReplyRejected, night_cloud.DeadlineExpired):
        assert cls("x").sent is False


def test_deadline_and_reply_cannot_be_changed_after_creation():
    deadline = night_cloud.Deadline.after(5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        deadline.at = 0.0
    reply = night_cloud.Reply(text="t", provider="tinfoil", model="m", attestation="a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        reply.text = "другой"


def test_cause_is_cut_only_past_the_limit_and_counts_the_cut():
    limit = night_cloud._CAUSE_LIMIT
    exact = "я" * limit
    assert night_cloud._cause(exact) == exact
    assert night_cloud._cause("я" * (limit + 7)) == "я" * limit + "… (обрезано 7 зн.)"


def test_status_and_cause_walks_stop_at_sixteen_links():
    class Coded(Exception):
        status_code = 503

    assert night_cloud._http_status(_chain(16, Coded())) == 503
    assert night_cloud._http_status(_chain(17, Coded())) is None
    assert night_cloud._classify(_chain(16, _tinfoil_error())) is night_cloud.AttestationFailed
    assert night_cloud._classify(_chain(17, _tinfoil_error())) is night_cloud.ProviderUnavailable


@pytest.mark.parametrize("code,want", [(100, 100), (599, 599), (99, None), (600, None), (True, None)])
def test_http_code_counts_only_inside_the_http_range(code, want):
    err = Exception("ответ")
    err.status_code = code
    assert night_cloud._status_of(err) == want
    kept = night_cloud.conform_entry({"last_attempt": {"http_status": code}})
    assert kept.get("last_attempt", {}).get("http_status") == want


def test_document_without_security_verified_is_not_attested():
    client = FakeClient()
    del client.doc.security_verified
    with pytest.raises(night_cloud.AttestationFailed, match="security_verified"):
        night_cloud._verified(client)


def test_attestation_reason_names_the_missing_and_the_failed_step():
    client = FakeClient()
    del client.doc.steps["verify_code"]
    with pytest.raises(night_cloud.AttestationFailed, match="verify_code"):
        night_cloud._verified(client)
    with pytest.raises(night_cloud.AttestationFailed, match="compare_measurements"):
        night_cloud._verified(FakeClient(steps_ok=False))


def test_attestation_line_names_router_release_and_fingerprint_head():
    line, fingerprint, release = night_cloud._verified(FakeClient())
    assert line.startswith(f"роутер router.example, релиз v0.0.167, отпечаток {'ab' * 8}, "), line
    assert (fingerprint, release) == ("ab" * 32, "v0.0.167")
    line, _, _ = night_cloud._verified(_doc_client(enclave_host="", release_tag=""))
    assert line.startswith(f"роутер ?, релиз ?, отпечаток {'ab' * 8}, "), line


@pytest.mark.parametrize("change,said", [
    ({"enclave_host": "Router Bad!"}, "имя роутера не той формы"),
    ({"release_tag": "v1 beta"}, "тег релиза не той формы"),
])
def test_out_of_form_router_or_release_line_reaches_the_log(monkeypatch, change, said):
    """По одному полю на случай: flush соседней строки не выносит в лог чужую."""
    log = _Log()
    monkeypatch.setattr(sys, "stderr", log)
    night_cloud._verified(_doc_client(**change))
    assert said in log.seen and log.pending == ""


def test_state_file_of_another_shape_reads_as_empty(tmp_path):
    state = tmp_path / "logs" / "night_cloud_state.json"
    state.parent.mkdir()
    state.write_text("[1, 2]", encoding="utf-8")
    assert night_cloud.read_entry(state, "G") == {}
    _send(tmp_path, _cfg(tmp_path), Factory())
    assert _state(tmp_path)["last_attempt"]["outcome"] == "delivered"


def test_state_is_written_where_no_folder_exists_yet_without_noise(tmp_path, capsys):
    state = tmp_path / "новый корень" / "logs" / "night_cloud_state.json"
    real = night_cloud._attest
    night_cloud._attest = Factory()
    try:
        reply = night_cloud.send(_cfg(tmp_path), "ядра графа", deadline=night_cloud.Deadline.after(30),
                                 state_path=state, graph_key="G")
    finally:
        night_cloud._attest = real
    night_cloud.accept(state, "G", reply)
    entry = night_cloud.read_entry(state, "G")
    assert entry["last_accepted"]["fingerprint"] == "ab" * 32 and entry["last_attempt"]["accepted"] is True
    assert "не записан" not in capsys.readouterr().out


def test_state_file_is_private_from_its_first_byte(tmp_path, monkeypatch):
    seen = []
    real = night_cloud.os.fchmod

    def fchmod(fd, mode):
        seen.append(stat.S_IMODE(night_cloud.os.fstat(fd).st_mode))
        real(fd, mode)

    monkeypatch.setattr(night_cloud.os, "fchmod", fchmod)
    _send(tmp_path, _cfg(tmp_path), Factory())
    assert seen and all(m & 0o077 == 0 and m & 0o600 == 0o600 for m in seen), [oct(m) for m in seen]


def test_state_file_keeps_graph_names_readable(tmp_path):
    _send(tmp_path, _cfg(tmp_path), Factory(), graph_key="Граф встреч")
    raw = (tmp_path / "logs" / "night_cloud_state.json").read_text(encoding="utf-8")
    assert '\n "Граф встреч": {\n  "last_attempt"' in raw, raw[:200]


def test_busy_state_lock_says_so_in_the_log(tmp_path, monkeypatch):
    log = _Log()
    monkeypatch.setattr(night_cloud.file_locks, "acquire_exclusive", lambda *a, **k: False)
    monkeypatch.setattr(sys, "stdout", log)
    night_cloud.reject(tmp_path / "logs" / "night_cloud_state.json", "G")
    assert "занят — исход не записан" in log.seen


def test_failed_state_write_says_so_in_the_log(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise OSError("диск")

    log = _Log()
    monkeypatch.setattr(night_cloud, "_update_state", broken)
    monkeypatch.setattr(sys, "stdout", log)
    night_cloud.reject(tmp_path / "logs" / "night_cloud_state.json", "G")
    assert "не записан: OSError" in log.seen


def test_rejected_reply_is_recorded_as_not_accepted(tmp_path):
    _send(tmp_path, _cfg(tmp_path), Factory())
    night_cloud.reject(tmp_path / "logs" / "night_cloud_state.json", "G")
    attempt = _state(tmp_path)["last_attempt"]
    assert attempt["outcome"] == "rejected" and attempt["accepted"] is False


def test_attempt_date_is_the_day_of_its_stamp(tmp_path):
    _send(tmp_path, _cfg(tmp_path), Factory())
    attempt = _state(tmp_path)["last_attempt"]
    assert attempt["date"] == attempt["at"][:10] and len(attempt["date"]) == 10


def test_read_timeout_has_a_floor_when_the_step_is_almost_over(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(night_cloud, "_attest", Factory(client=client))
    box = {"lock": threading.Lock()}
    night_cloud._exchange(_cfg(tmp_path), KEY, "p", "m", "max", _fixed(1e-6), box)
    assert client.options[0]["timeout"] == 0.001 and client.options[0]["max_retries"] == 0


def test_exchange_with_no_time_left_stops_before_the_request(tmp_path, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(night_cloud, "_attest", Factory(client=client))
    box = {"lock": threading.Lock()}
    with pytest.raises(night_cloud.DeadlineExpired):
        night_cloud._exchange(_cfg(tmp_path), KEY, "p", "m", "max", _fixed(0.0), box)
    assert not client.creates and not box.get("sent")


def test_send_with_no_time_left_does_not_attest(tmp_path):
    factory = Factory()
    with pytest.raises(night_cloud.DeadlineExpired):
        _send(tmp_path, _cfg(tmp_path), factory, deadline=_fixed(0.0))
    _join_exchange()
    assert factory.calls == 0
    assert _state(tmp_path)["last_attempt"]["outcome"] == "deadline"


@pytest.mark.parametrize("left,want", [(7.0, 7.0), (None, None)])
def test_cores_step_deadline_is_what_is_left_of_the_night(cores_step, monkeypatch, left, want):
    ncc = cores_step.ncc
    sends = []

    def fake_send(*a, **k):
        sends.append(k["deadline"].remaining())
        raise night_cloud.DeadlineExpired("стоп")

    monkeypatch.setattr(ncc.live_gate, "step_left", lambda *a, **k: left)
    monkeypatch.setattr(ncc.night_cloud, "send", fake_send)
    assert _exit_code(ncc.main) == 0
    want = ncc.STEP_BUDGET_S if want is None else want
    assert want - 1 < sends[0] <= want


def _anthropic_step(cores_step, monkeypatch, tmp_path, script: str):
    """Шаг ядер на ветке anthropic с настоящим подпроцессом: CLI — скрипт."""
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    cli.chmod(0o755)
    cores_step.cfg["sufler"]["cloud_night_provider"] = "anthropic"
    ncc = cores_step.ncc
    monkeypatch.setattr(ncc.cloud, "claude_bin_checked", lambda *a, **k: None)
    monkeypatch.setattr(ncc.cloud, "claude_bin", lambda *a, **k: str(cli))
    monkeypatch.setattr(ncc.subprocess, "run", _REAL_RUN)
    return ncc


def test_cores_step_anthropic_writes_the_cli_report_without_the_api_key(cores_step, monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-не-для-ночи")
    ncc = _anthropic_step(cores_step, monkeypatch, tmp_path,
                          '[ -n "$ANTHROPIC_API_KEY" ] && { echo "ключ API в окружении" >&2; exit 1; }\n'
                          f"cat <<'R'\n{SECTIONS}R\n")
    assert _exit_code(ncc.main) == 0
    (rep,) = cores_step.graph.glob("Служебное_ночная_ревизия_*.md")
    assert SECTIONS.strip() in rep.read_text(encoding="utf-8")


def test_cores_step_anthropic_failure_names_the_cli_reason(cores_step, monkeypatch, tmp_path, capsys):
    ncc = _anthropic_step(cores_step, monkeypatch, tmp_path, 'echo "лимит исчерпан" >&2\nexit 1\n')
    assert _exit_code(ncc.main) == 2
    out = capsys.readouterr().out
    assert "ревизия не принята" in out and "лимит исчерпан" in out
    assert not list(cores_step.graph.glob("Служебное_ночная_ревизия_*.md"))


def test_cores_step_anthropic_infrastructure_codes(cores_step, monkeypatch, tmp_path):
    ncc = _anthropic_step(cores_step, monkeypatch, tmp_path, "exit 0\n")

    def no_cli(*a, **k):
        raise ncc.cloud.CloudCLIUnavailable("нет ответа на --version")

    monkeypatch.setattr(ncc.cloud, "claude_bin_checked", no_cli)
    assert _exit_code(ncc.main) == 3, "CLI не отвечает — код 3"
    monkeypatch.setattr(ncc.cloud, "claude_bin_checked", lambda *a, **k: None)
    monkeypatch.setattr(ncc.cloud, "claude_bin", lambda *a, **k: str(tmp_path / "нет_такого"))
    assert _exit_code(ncc.main) == 3, "бинарник не запустился — код 3"

    def crash(*a, **k):
        raise RuntimeError("авария")

    monkeypatch.setattr(ncc.subprocess, "run", crash)
    assert _exit_code(ncc.main) == 2, "авария облака — код 2"
