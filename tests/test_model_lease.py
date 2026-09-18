"""Аренда модели у клиента (№264): перезапуск сервера щадит живую работу.

Инцидент 12.08: грейс 180 с спасал очередь из одной генерации; разбор графа —
очередь из нескольких, проба не пробивалась, и перезапуск после грейса убивал
работу, ради которой грейс завели. Входной круг DS и GLM (18.09) отверг «файл
на pid» (многопоточный демон) и «pid + mtime + протухание» (велосипед, от
которого проект отказался в busy_signals): здесь аренда — flock-файл на
ВЫЗОВ, смерть процесса снимает её ядром, а живость — скользящий deadline.
"""
from __future__ import annotations

import ast
import fcntl
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import llm  # noqa: E402
import llm_health  # noqa: E402
import model_lease  # noqa: E402

LOCAL = {"llm": {"base_url": "http://localhost:11434", "model": "qwen3.6:35b-a3b"}}


def test_a_lease_is_held_by_flock_and_vanishes_with_the_call(tmp_path):
    """Аренда — файл под LOCK_EX на время вызова: читатель видит её живой,
    после выхода из with файла нет."""
    with model_lease.Lease(tmp_path, server="http://localhost:11434", engine="ollama",
                           kind="complete", budget=120) as lease:
        assert lease.path is not None and lease.path.exists()
        with lease.path.open("r+") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        live = model_lease.live(tmp_path, server="http://localhost:11434")
        assert len(live) == 1 and live[0]["pid"] == os.getpid() and not live[0]["stalled"]
        assert model_lease.live(tmp_path, server="http://other:1") == [], "аренда на другой сервер — не наша"
    assert not lease.path or not lease.path.exists()
    assert model_lease.live(tmp_path) == []


def test_two_calls_in_one_process_do_not_clobber_each_other(tmp_path):
    """Файл на ВЫЗОВ, не на pid: демон гоняет прогрев, подсказки и облачную
    доводку из разных нитей одного процесса. Раньше план «<pid>.json» дал бы
    затирание и удаление чужой аренды в finally первого вызова (Critical DS и
    GLM входного круга)."""
    srv = "http://localhost:11434"
    a = model_lease.Lease(tmp_path, server=srv, engine="ollama", kind="stream").__enter__()
    b = model_lease.Lease(tmp_path, server=srv, engine="ollama", kind="complete", budget=60).__enter__()
    assert a.path != b.path and len(model_lease.live(tmp_path, server=srv)) == 2
    a.__exit__(None, None, None)
    assert len(model_lease.live(tmp_path, server=srv)) == 1, "вторая генерация жива, хотя первая кончилась"
    b.__exit__(None, None, None)
    assert model_lease.live(tmp_path, server=srv) == []


def test_orphan_and_reused_pid_are_not_alive(tmp_path):
    """Процесс умер с SIGKILL — файл остался, flock снят ядром: сирота убирается.
    Даже если pid уже переиспользован живым процессом (наш собственный pid в
    файле), без flock аренда не считается — pid-reuse не держит перезапуск
    (I3 GLM, C2 DS)."""
    d = model_lease.lease_dir(tmp_path)
    d.mkdir(parents=True)
    orphan = d / f"{os.getpid()}-deadbeef.json"          # «наш» pid, но лока нет
    orphan.write_text(json.dumps({"pid": os.getpid(), "server": "http://localhost:11434",
                                  "engine": "ollama", "kind": "stream",
                                  "started": time.time(), "deadline": time.time() + 900}))
    assert model_lease.live(tmp_path, server="http://localhost:11434") == []
    assert not orphan.exists(), "сирота убрана"


def test_a_dead_process_releases_its_lease(tmp_path):
    """Аренду держит другой процесс: жива; процесс убит — читатель видит
    свободный flock и убирает файл. Ядро, не таймер."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO / "src")!r})
        import model_lease, pathlib
        with model_lease.Lease(pathlib.Path({str(tmp_path)!r}), server="s", engine="e", kind="stream"):
            print("held", flush=True)
            time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        assert len(model_lease.live(tmp_path, server="s")) == 1
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert model_lease.live(tmp_path, server="s") == [], "смерть процесса снимает аренду без таймера"


class _Clock:
    """Часы аренды под контролем теста: реальное время за микросекунды не сдвинуть на минуты."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def time(self) -> float:
        return self.t


def test_stall_is_the_only_time_criterion_and_it_rolls_on_bytes(tmp_path, monkeypatch):
    """Не-стрим: deadline = бюджет запроса; стрим: начальный STALL_S (префилл
    молчит до DOC_STREAM_TIMEOUT), каждый байт катит его вперёд. Прошёл —
    работа висит, её не щадят. `started + timeout` для стрима — ложь
    (таймаут между байтами), поэтому второго критерия нет (C2 GLM, I4 DS)."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    srv = "http://localhost:11434"
    assert model_lease.STALL_S > llm.DOC_STREAM_TIMEOUT[1], \
        "живой префилл документа молчит до DOC_STREAM_TIMEOUT — порог зависания должен быть больше"
    with model_lease.Lease(tmp_path, server=srv, engine="ollama", kind="stream") as st:
        assert st.deadline == clock.t + model_lease.STALL_S
        clock.t += model_lease.STALL_S - 1
        assert not model_lease.live(tmp_path, server=srv)[0]["stalled"], "префилл молчит, но порог не вышел"
        clock.t += 2
        assert model_lease.live(tmp_path, server=srv)[0]["stalled"], "без байта дольше STALL_S — висит"
        st.progress()                                      # байт пришёл
        assert st.deadline == clock.t + model_lease.STALL_S
        assert not model_lease.live(tmp_path, server=srv)[0]["stalled"], "байт катит deadline вперёд"
    with model_lease.Lease(tmp_path, server=srv, engine="ollama", kind="complete", budget=45) as c:
        assert c.deadline == c.started + 45
        clock.t += 10
        c.progress()                                       # у не-стрима прогресса нет
        assert c.deadline == c.started + 45
        clock.t += 36
        assert model_lease.live(tmp_path, server=srv)[0]["stalled"], "не-стрим дольше бюджета — висит"


def test_progress_writes_are_throttled(tmp_path, monkeypatch):
    """Читатель смотрит раз в RESTART_POLL=5 с — писать на каждом чанке незачем:
    50 чанков за 10 с — не больше трёх записей, но deadline в памяти катится
    на каждом, а на диск уезжает при первой же записи после паузы."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    dumps = []
    real_dump = model_lease.json.dump
    monkeypatch.setattr(model_lease.json, "dump", lambda obj, f: (dumps.append(dict(obj)), real_dump(obj, f))[1])
    with model_lease.Lease(tmp_path, server="s", engine="e", kind="stream") as st:
        for _ in range(50):
            clock.t += 0.2
            st.progress()
        assert st.deadline == clock.t + model_lease.STALL_S
    assert 2 <= len(dumps) <= 4, f"пятьдесят чанков за десять секунд дали {len(dumps)} записей"
    assert dumps[-1]["deadline"] >= dumps[0]["deadline"] + 5, "записанный deadline не отстаёт больше окна троттлинга"


def test_broken_json_and_missing_dir_do_not_break_the_reader(tmp_path):
    assert model_lease.live(tmp_path) == []
    d = model_lease.lease_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "1-garbage.json").write_text("{not json")
    assert model_lease.live(tmp_path) == []


def test_transport_seams_are_the_only_way_to_the_model():
    """Структурный сторож: requests.post к модели — только в двух швах с
    арендой (_open_stream, _post_busy) и двух названных исключениях (embed —
    0,2 с, убить не жалко; проба llm_health.probe — тот, кто спрашивает).
    Новый вызов мимо швов молча вернул бы класс №264."""
    allowed = {"_open_stream", "_post_busy", "embed"}
    tree = ast.parse((REPO / "src" / "llm.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "post" and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "requests":
                assert fn.name in allowed, f"requests.post мимо швов с арендой: {fn.name}"


def _lease_file(tmp_path, *, server="http://localhost:11434", stalled=False):
    """Живая (или висящая) аренда чужого процесса: файл под нашим же flock."""
    lease = model_lease.Lease(tmp_path, server=server, engine="ollama", kind="stream").__enter__()
    if stalled:
        lease.deadline = time.time() - 1
        lease._write(force=True)
    return lease


def test_slow_server_with_a_live_lease_is_not_restarted(tmp_path, monkeypatch):
    """Грейс истёк, проба SLOW — но у нашего процесса живая аренда: сервер
    занят длинной работой, а не завис. Перезапуска нет, вызывающий идёт в
    очередь (True). Раньше здесь тикала константа и убивала работу (12.08)."""
    monkeypatch.setattr(llm_health, "ROOT", tmp_path)
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.SLOW)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log, **kw: pytest.fail("живую работу перезапуском не убивают"))
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    lease = _lease_file(tmp_path)
    try:
        said: list[str] = []
        assert llm_health.ensure_alive(LOCAL, log=said.append, wait=0.05) is True
        assert any("занята живой работой" in m and f"pid {os.getpid()}" in m for m in said), said
    finally:
        lease.__exit__(None, None, None)


def test_slow_server_with_only_a_stalled_lease_is_restarted(tmp_path, monkeypatch):
    """Аренда есть, но байтов нет дольше STALL_S — это зависание, перезапуск как прежде."""
    monkeypatch.setattr(llm_health, "ROOT", tmp_path)
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.SLOW)
    calls = []
    monkeypatch.setattr(llm_health, "_restart", lambda cfg, log, **kw: (calls.append(1), True)[1])
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    lease = _lease_file(tmp_path, stalled=True)
    try:
        llm_health.ensure_alive(LOCAL, log=lambda m: None, wait=0.05)
        assert calls, "висящая аренда не должна держать перезапуск"
    finally:
        lease.__exit__(None, None, None)


def test_lease_on_another_server_does_not_block_ours(tmp_path, monkeypatch):
    """Аренда облачного шлюза не держит перезапуск локальной Ollama (I6 DS)."""
    monkeypatch.setattr(llm_health, "ROOT", tmp_path)
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.SLOW)
    calls = []
    monkeypatch.setattr(llm_health, "_restart", lambda cfg, log, **kw: (calls.append(1), True)[1])
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    lease = _lease_file(tmp_path, server="https://gateway.example/v1")
    try:
        llm_health.ensure_alive(LOCAL, log=lambda m: None, wait=0.05)
        assert calls
    finally:
        lease.__exit__(None, None, None)


def test_restart_itself_refuses_over_a_live_lease_unless_forced(tmp_path, monkeypatch):
    """Страховка перед kill — в самом перезапуске: любой будущий путь к kill
    наследует защиту; force — ручной аварийный перезапуск."""
    monkeypatch.setattr(llm_health, "ROOT", tmp_path)
    ran = []
    monkeypatch.setattr(llm_health.subprocess, "run", lambda *a, **kw: ran.append(a[0]))
    monkeypatch.setattr(llm_health, "listener_path", lambda url: None)
    monkeypatch.setattr(llm_health, "restart_commands", lambda *a, **kw: [["true"]])
    lease = _lease_file(tmp_path)
    try:
        said: list[str] = []
        assert llm_health._restart(LOCAL, said.append) is False and not ran
        assert any("отложен" in m for m in said)
        assert llm_health._restart(LOCAL, said.append, force=True) is True and ran
    finally:
        lease.__exit__(None, None, None)
    ran.clear()
    assert llm_health._restart(LOCAL, lambda m: None) is True and ran, "без аренд — перезапуск как прежде"
