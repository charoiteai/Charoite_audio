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


def test_port_owner_beats_what_is_installed():
    """Обе установки уживаются на одной машине — лечить надо ту, что отвечает.

    У владельца стоят и `/Applications/Ollama.app`, и brew-сервис, а порт
    держит brew. Эвристика «есть приложение → перезапускаем приложение» убила
    бы GUI, не тронув зависшего, и отрапортовала бы об успехе: первый живой
    тест сторожа прошёл ровно так — ложноположительно.
    """
    cmds = llm_health.restart_commands(
        "Darwin", listener="/opt/homebrew/Cellar/ollama/0.20.0/bin/ollama",
        has_app=True, has_brew=True, has_systemctl=False)
    assert cmds == [["brew", "services", "restart", "ollama"]]


def test_gui_owner_gets_gui_restart():
    cmds = llm_health.restart_commands(
        "Darwin", listener="/Applications/Ollama.app/Contents/Resources/ollama",
        has_app=True, has_brew=True, has_systemctl=False)
    assert any("open" in c for c in cmds[-1])
    assert not any("brew" in part for c in cmds for part in c)


def test_pkill_pattern_does_not_hit_the_caller():
    """pkill по слову «ollama» убил бы и вызывающий процесс.

    graph_updater запускается из папки проекта, и его командная строка вполне
    может содержать это слово — бить надо строго по пути внутрь бандла.
    """
    cmds = llm_health.restart_commands("Darwin", listener=None, has_app=True,
                                       has_brew=False, has_systemctl=False)
    pattern = cmds[0][-1]
    assert "Ollama.app" in pattern and "/" in pattern


def test_dead_server_falls_back_to_what_is_installed():
    # сервер уже упал — владельца порта не спросить, остаётся эвристика
    cmds = llm_health.restart_commands("Darwin", listener=None, has_app=False,
                                       has_brew=True, has_systemctl=False)
    assert cmds == [["brew", "services", "restart", "ollama"]]


def test_linux_uses_systemctl():
    cmds = llm_health.restart_commands("Linux", listener=None, has_app=False,
                                       has_brew=False, has_systemctl=True)
    assert cmds == [["systemctl", "--user", "restart", "ollama"]]


def test_nothing_to_restart_is_empty_not_guess():
    assert llm_health.restart_commands("Linux", None, False, False, False) == []


def test_listener_lookup_uses_the_configured_port(monkeypatch):
    seen: list[list[str]] = []

    class _Done:
        stdout = ""

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return _Done()

    monkeypatch.setattr(llm_health.subprocess, "run", fake_run)
    llm_health.listener_path("http://localhost:11500")
    assert any("-i:11500" in part for cmd in seen for part in cmd)


def test_listener_lookup_survives_missing_lsof(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("lsof")

    monkeypatch.setattr(llm_health.subprocess, "run", boom)
    assert llm_health.listener_path("http://localhost:11434") is None


def test_alive_model_is_not_restarted(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: True)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log: pytest.fail("здоровую модель трогать нельзя"))
    assert llm_health.ensure_alive(LOCAL, log=lambda m: None) is True


def test_remote_stall_is_reported_not_fixed(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log: pytest.fail("чужой сервер не наш"))
    said: list[str] = []
    assert llm_health.ensure_alive(REMOTE, log=said.append) is False
    assert any("не локальный" in m for m in said)


def test_stalled_local_model_gets_restarted_and_rechecked(monkeypatch):
    calls = {"probe": 0, "restart": 0}

    def probe(cfg, timeout=None):
        calls["probe"] += 1
        return calls["restart"] > 0        # оживает только после перезапуска

    def restart(cfg, log):
        calls["restart"] += 1
        return True

    monkeypatch.setattr(llm_health, "probe", probe)
    monkeypatch.setattr(llm_health, "_restart", restart)
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    assert llm_health.ensure_alive(LOCAL, log=lambda m: None) is True
    assert calls["restart"] == 1


def test_hopeless_restart_gives_up_and_says_so(monkeypatch):
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: False)
    monkeypatch.setattr(llm_health, "_restart", lambda cfg, log: True)
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


# ---------------------------------------------------------- занята ≠ встала
# Ollama 0.32 с MLX-раннером на занятой модели отвечает 503 за ~250 мс (факт
# 18.08). Раньше проба считала это смертью и ensure_alive перезапускала сервер —
# убивая ту генерацию, которая его и занимала (граф 12.08 трижды ронял Ollama
# под соседней пересборкой).


def test_probe_reports_busy_on_503_not_dead(monkeypatch):
    monkeypatch.setattr(llm_health.requests, "post", lambda *a, **kw: _Resp(503))
    assert llm_health.probe(LOCAL) == llm_health.BUSY
    monkeypatch.setattr(llm_health.requests, "post", lambda *a, **kw: _Resp(429))
    assert llm_health.probe(LOCAL) == llm_health.BUSY


def test_busy_model_is_waited_for_not_restarted(monkeypatch):
    answers = iter([llm_health.BUSY, llm_health.BUSY, True])
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: next(answers))
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log: pytest.fail("занятую модель перезапускать нельзя"))
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    said: list[str] = []
    assert llm_health.ensure_alive(LOCAL, log=said.append) is True
    assert any("занята" in m for m in said) and any("освободилась" in m for m in said)


def test_still_busy_after_wait_is_alive_not_broken(monkeypatch):
    """Не дождались — сервер всё равно жив: очередь отстоит вызывающий."""
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.BUSY)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log: pytest.fail("занятую модель перезапускать нельзя"))
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    said: list[str] = []
    assert llm_health.ensure_alive(LOCAL, log=said.append, wait=1) is True
    assert any("всё ещё занята" in m for m in said)
