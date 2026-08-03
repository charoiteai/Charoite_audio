"""Вставшая Ollama выглядит здоровой — и это стоило разбора встречи.

03.08 запрос к модели провисел десять минут и ушёл с ReadTimeout: HTTP-сервер
отвечал, модель числилась загруженной, `llama-server` стоял на нуле процента.
`graph_updater` упал, а с ним не выполнился весь пост-процессинг — ни заметки
встречи, ни разбора, ни архивной папки. Снаружи это выглядело как «программа
перестала раскладывать встречи по папкам»: молчание вместо результата.

Здесь проверяется сторож: чем он пробует модель, кого вправе перезапускать
и чем именно перезапускает.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import llm_health  # noqa: E402
import privacy  # noqa: E402

LOCAL = {"llm": {"base_url": "http://localhost:11434", "model": "qwen3.6:35b-a3b"}}
REMOTE = {"llm": {"base_url": "http://192.168.1.50:11434", "model": "qwen3.6:35b-a3b",
                  "allow_remote": True}}


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status


def test_probe_asks_for_generation_not_tags(monkeypatch):
    """Проба обязана требовать генерацию.

    У вставшей Ollama `/api/tags` отдаётся мгновенно — проба по нему сказала бы
    «здорова» ровно в том случае, ради которого сторож и написан.
    """
    seen: dict = {}

    def fake_post(url, json, timeout):  # noqa: A002 — имя параметра из requests
        seen["url"] = url
        seen["json"] = json
        return _Resp()

    monkeypatch.setattr(llm_health.requests, "post", fake_post)
    assert llm_health.probe(LOCAL) is True
    assert seen["url"].endswith("/api/generate")
    assert seen["json"]["options"]["num_predict"] == 1, "проба должна быть дешёвой"


def test_probe_survives_dead_server(monkeypatch):
    def boom(*a, **kw):
        raise llm_health.requests.ConnectionError("no route")

    monkeypatch.setattr(llm_health.requests, "post", boom)
    assert llm_health.probe(LOCAL) is False


def test_probe_counts_http_error_as_dead(monkeypatch):
    monkeypatch.setattr(llm_health.requests, "post",
                        lambda *a, **kw: _Resp(500))
    assert llm_health.probe(LOCAL) is False


def test_local_url_is_ours_to_restart():
    assert llm_health.is_local(LOCAL) is True


def test_remote_url_is_not_ours_to_restart():
    """Чужая машина может обслуживать не только нас — ронять её нельзя."""
    assert llm_health.is_local(REMOTE) is False


def test_forbidden_remote_is_not_local():
    # адрес не наш и вдобавок запрещён политикой: перезапускать тем более нечего
    assert llm_health.is_local({"llm": {"base_url": "http://10.0.0.5:11434"}}) is False


def test_mac_app_restart_goes_through_gui():
    """На маке с Ollama.app единственный рабочий путь — GUI.

    `brew services` про приложение не знает и молча отрапортует успех, ничего
    не перезапустив, — а сторож решит, что починил.
    """
    cmds = llm_health.restart_commands("Darwin", has_app=True, has_brew=True,
                                       has_systemctl=False)
    assert any("open" in c for c in cmds[-1])
    assert not any("brew" in part for c in cmds for part in c)


def test_pkill_pattern_does_not_hit_the_caller():
    """pkill по слову «ollama» убил бы и вызывающий процесс.

    graph_updater запускается из папки проекта, и его командная строка вполне
    может содержать это слово — бить надо строго по пути внутрь бандла.
    """
    cmds = llm_health.restart_commands("Darwin", has_app=True, has_brew=False,
                                       has_systemctl=False)
    pattern = cmds[0][-1]
    assert "Ollama.app" in pattern and "/" in pattern


def test_brew_used_when_no_app():
    cmds = llm_health.restart_commands("Darwin", has_app=False, has_brew=True,
                                       has_systemctl=False)
    assert cmds == [["brew", "services", "restart", "ollama"]]


def test_linux_uses_systemctl():
    cmds = llm_health.restart_commands("Linux", has_app=False, has_brew=False,
                                       has_systemctl=True)
    assert cmds == [["systemctl", "--user", "restart", "ollama"]]


def test_nothing_to_restart_is_empty_not_guess():
    assert llm_health.restart_commands("Linux", False, False, False) == []


def test_alive_model_is_not_restarted(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: True)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda log: pytest.fail("здоровую модель трогать нельзя"))
    assert llm_health.ensure_alive(LOCAL, log=lambda m: None) is True


def test_remote_stall_is_reported_not_fixed(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda log: pytest.fail("чужой сервер не наш"))
    said: list[str] = []
    assert llm_health.ensure_alive(REMOTE, log=said.append) is False
    assert any("не локальный" in m for m in said)


def test_stalled_local_model_gets_restarted_and_rechecked(monkeypatch):
    calls = {"probe": 0, "restart": 0}

    def probe(cfg, timeout=None):
        calls["probe"] += 1
        return calls["restart"] > 0        # оживает только после перезапуска

    def restart(log):
        calls["restart"] += 1
        return True

    monkeypatch.setattr(llm_health, "probe", probe)
    monkeypatch.setattr(llm_health, "_restart", restart)
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    assert llm_health.ensure_alive(LOCAL, log=lambda m: None) is True
    assert calls["restart"] == 1


def test_hopeless_restart_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)
    monkeypatch.setattr(llm_health, "_restart", lambda log: True)
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    said: list[str] = []
    assert llm_health.ensure_alive(LOCAL, log=said.append, wait=10) is False
    assert any("не ответила" in m for m in said), "молчаливый отказ здесь недопустим"


def test_probe_timeout_survives_cold_start():
    """23-гигабайтная модель поднимается с диска десятки секунд.

    Короткий таймаут пробы означал бы перезапуск Ollama ровно в тот момент,
    когда она честно грузится, — и так по кругу.
    """
    assert llm_health.PROBE_TIMEOUT >= 60


def test_privacy_owns_the_locality_question():
    # сторож не должен заводить собственное представление о «нашей машине»
    assert privacy.is_loopback_url("http://127.0.0.1:11434") is True
    assert privacy.is_loopback_url("http://192.168.1.50:11434") is False
