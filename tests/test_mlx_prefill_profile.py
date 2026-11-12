"""Startup intent is validated before health/restart effects; healthy servers stay untouched."""

import sys

import pytest

import llm_health


def config(**llm):
    return {"llm": {"engine": "mlx-server", "mlx_model": "synthetic",
                    "mlx_base_url": "http://127.0.0.1:8859", **llm}, "sufler": {}}


def forbidden(*_, **__):
    raise AssertionError("side effect before configuration validation")


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 128., "128"])
@pytest.mark.parametrize("entry", ["probe", "ensure_alive", "force_restart", "_restart_mlx"])
def test_bad_active_profile_raises_before_any_side_effect(monkeypatch, _сеть_закрыта, value, entry):
    for name in ("busy_with_ours", "_spare", "_mlx_listener_pid", "secure_dir", "trim_log"):
        monkeypatch.setattr(llm_health, name, forbidden)
    monkeypatch.setattr(llm_health.subprocess, "Popen", forbidden)
    cfg = config(mlx_prefill_step_size=value)
    _сеть_закрыта[("POST", "http://127.0.0.1:8859/v1/chat/completions")] = forbidden
    with pytest.raises(ValueError, match="mlx_prefill_step_size"):
        if entry == "probe":
            llm_health.probe(cfg)
        else:
            getattr(llm_health, entry)(cfg, forbidden)


@pytest.mark.parametrize("feed,override,expected", [
    (False, {}, []), (True, {}, ["--prefill-step-size", "128"]),
    (False, {"mlx_prefill_step_size": 64}, ["--prefill-step-size", "64"]),
    (True, {"mlx_prefill_step_size": 256}, ["--prefill-step-size", "256"]),
])
def test_actual_launcher_argv_preserves_absent_classic_and_forwards_profile(monkeypatch, tmp_path, feed, override, expected):
    cfg = config(**override)
    cfg["sufler"]["live_feed"] = feed
    monkeypatch.setattr(llm_health, "_root", lambda: tmp_path)
    monkeypatch.setattr(llm_health, "_spare", lambda *a, **kw: False)
    monkeypatch.setattr(llm_health, "_mlx_listener_pid", lambda _: None)
    seen = []
    monkeypatch.setattr(llm_health.subprocess, "Popen", lambda args, **kw: seen.append((args, kw)))
    assert llm_health._restart_mlx(cfg, lambda _: None)
    assert seen[0][0] == [sys.executable, "-m", "mlx_lm", "server", "--model", "synthetic", "--port", "8859"] + expected
    assert seen[0][1]["start_new_session"] is True


def test_healthy_external_server_does_not_restart_when_profile_intent_changes(monkeypatch):
    cfg = config(mlx_prefill_step_size=128)
    monkeypatch.setattr(llm_health, "probe", lambda *a, **kw: True)
    for name in ("busy_with_ours", "_spare", "_mlx_listener_pid", "_restart_mlx", "secure_dir"):
        monkeypatch.setattr(llm_health, name, forbidden)
    assert llm_health.ensure_alive(cfg, forbidden)


@pytest.mark.parametrize("engine", ["ollama", "cloud"])
def test_inactive_mlx_key_does_not_change_other_effective_health_paths(monkeypatch, engine):
    cfg = config(mlx_prefill_step_size=None)
    cfg["llm"].update(engine=engine, model="synthetic")
    monkeypatch.setattr(llm_health, "probe", lambda *a, **kw: True)
    assert llm_health.ensure_alive(cfg, forbidden)
    monkeypatch.setattr(llm_health, "busy_with_ours", lambda *a, **kw: [])
    seen = []
    monkeypatch.setattr(llm_health, "_restart", lambda cfg, log, **kw: seen.append(kw) or True)
    assert llm_health.force_restart(cfg, forbidden)
    assert seen == [{"force": True}]


def test_valid_profile_keeps_spare_lease_policy_before_any_launch(monkeypatch):
    cfg = config(mlx_prefill_step_size=128)
    calls = []
    monkeypatch.setattr(llm_health, "_spare", lambda cfg, log, **kw: calls.append(kw) or True)
    monkeypatch.setattr(llm_health, "_mlx_listener_pid", forbidden)
    monkeypatch.setattr(llm_health.subprocess, "Popen", forbidden)
    assert not llm_health._restart_mlx(cfg, forbidden)
    assert calls == [{"force": False}]


@pytest.mark.parametrize("engine", ["ollama", "cloud"])
def test_direct_non_mlx_probe_keeps_unused_profile_key_inactive(monkeypatch, _сеть_закрыта, engine):
    cfg = config(mlx_prefill_step_size=None)
    cfg["llm"].update(engine=engine, model="synthetic")
    monkeypatch.setattr(llm_health.privacy, "cloud_engine_active", lambda _: False)
    monkeypatch.setattr(llm_health, "chat_keep_alive", lambda *a: 0)
    seen = []
    class Response:
        status_code = 200
    _сеть_закрыта[("POST", llm_health.privacy.llm_base_url(cfg) + "/api/generate")] = lambda url, **kw: seen.append(url) or Response()
    assert llm_health.probe(cfg)
    assert seen[0].endswith("/api/generate")
