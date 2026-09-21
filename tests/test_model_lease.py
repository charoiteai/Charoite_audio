"""Аренда модели у клиента (№264): перезапуск сервера щадит живую работу.

Инцидент 12.08: грейс 180 с спасал очередь из одной генерации; разбор графа —
очередь из нескольких, проба не пробивалась, и перезапуск после грейса убивал
работу, ради которой грейс завели. Входной круг DS и GLM (18.09) отверг «файл
на pid» (многопоточный демон) и «pid + mtime + протухание» (велосипед, от
которого проект отказался в busy_signals): здесь аренда — flock-файл на
ВЫЗОВ, смерть процесса снимает её ядром, а живость — скользящий deadline.
Выходной круг тех же голов добавил протокол публикации: файл никогда не виден
без замка и никогда не переписывается на месте; читатель судит по замку.
"""
from __future__ import annotations

import ast
import fcntl
import json
import os
import pathlib
import select
import subprocess
import sys
import textwrap
import time

import pytest
import charoite_paths

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import llm  # noqa: E402
import llm_health  # noqa: E402
import model_lease  # noqa: E402

LOCAL = {"llm": {"base_url": "http://localhost:11434", "model": "qwen3.6:35b-a3b"}}
SRV = "http://localhost:11434"


def _locked(path: pathlib.Path) -> bool:
    """Держит ли кто-то flock на файле (пробуем взять сами)."""
    with path.open("r") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


class _Clock:
    """Часы аренды под контролем теста: реальное время за микросекунды не сдвинуть на минуты."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def time(self) -> float:
        return self.t


# ---------------------------------------------------------------- писатель

def test_a_lease_is_held_by_flock_and_vanishes_with_the_call(tmp_path):
    """Аренда — файл под LOCK_EX на время вызова: читатель видит её живой,
    после выхода из with файла нет."""
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="complete",
                           read_timeout=120) as lease:
        assert lease.path is not None and lease.path.exists() and _locked(lease.path)
        assert oct(lease.path.stat().st_mode & 0o777) == "0o600"
        live = model_lease.live(tmp_path, server=SRV)
        assert len(live) == 1 and live[0]["pid"] == os.getpid() and not live[0]["stalled"]
        assert model_lease.live(tmp_path, server="http://other:1") == [], "аренда на другой сервер — не наша"
        path = lease.path
    assert not path.exists()
    assert model_lease.live(tmp_path) == []


def test_two_calls_in_one_process_do_not_clobber_each_other(tmp_path):
    """Файл на ВЫЗОВ, не на pid: демон гоняет прогрев, подсказки и облачную
    доводку из разных нитей одного процесса. План «<pid>.json» дал бы
    затирание и удаление чужой аренды в finally первого вызова (Critical DS и
    GLM входного круга)."""
    a = model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream").__enter__()
    b = model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="complete", read_timeout=60).__enter__()
    assert a.path != b.path and len(model_lease.live(tmp_path, server=SRV)) == 2
    a.__exit__(None, None, None)
    assert len(model_lease.live(tmp_path, server=SRV)) == 1, "вторая генерация жива, хотя первая кончилась"
    b.__exit__(None, None, None)
    assert model_lease.live(tmp_path, server=SRV) == []


def test_a_lease_is_never_visible_without_its_lock(tmp_path, monkeypatch):
    """Протокол публикации (C1 DS / I2 GLM выходного круга): файл появляется
    под именем `*.json` только через rename уже запертого inode. Раньше
    `open("w")` публиковал имя за один syscall до flock — читатель в этом окне
    видел «сироту» и удалял живую аренду."""
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        src, dst = pathlib.Path(src), pathlib.Path(dst)
        assert not src.name.endswith(".json"), "временное имя не должно попадать под маску читателя"
        assert list(pathlib.Path(dst).parent.glob(".*.json")) == [], "скрытые файлы — только .tmp"
        assert _locked(src), "перед публикацией inode уже заперт"
        seen.append(dst.exists())
        real_replace(src, dst)

    monkeypatch.setattr(model_lease.os, "replace", spy)
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream") as st:
        assert seen == [False], "первая публикация — новое имя"
        st._written = 0.0
        st.progress()
        assert seen == [False, True], "прогресс — подмена целиком поверх имени, не запись на месте"
        assert _locked(st.path), "замок на имени не прерывался"
        assert [p.name for p in tmp_path.rglob(".*.tmp")] == [], "временных файлов после публикации нет"
    assert model_lease.live(tmp_path) == []


def test_progress_never_rewrites_in_place(tmp_path, monkeypatch):
    """Читатель без замка читает файл, пока писатель пишет: запись на месте
    (seek/truncate/dump) давала полупустой JSON и «аренды нет» ровно в
    момент решения (C2 DS / I1 GLM). Каждая публикация — новый inode."""
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream") as st:
        ino1 = st.path.stat().st_ino
        st._written = 0.0
        st.progress()
        assert st.path.stat().st_ino != ino1, "прогресс опубликован новым inode"
        assert json.loads(st.path.read_text())["deadline"] == st.deadline, "на диске — целый JSON"


def test_orphan_files_are_not_alive_and_the_writer_sweeps_them(tmp_path, monkeypatch):
    """Процесс умер с SIGKILL — файл остался, flock снят ядром. Читатель такую
    аренду не считает (даже если pid переиспользован — наш собственный pid в
    файле) и НЕ удаляет: путь решения о kill без прав уничтожителя (критика 2
    GLM). Убирает писатель при следующем взятии аренды — и только старых:
    молодой незапертый файл — возможное окно чужой публикации."""
    monkeypatch.setattr(model_lease, "_last_sweep", 0.0)     # уборка троттлится на процесс
    d = model_lease.lease_dir(tmp_path)
    d.mkdir(parents=True)
    body = json.dumps({"pid": os.getpid(), "server": SRV, "engine": "ollama", "kind": "stream",
                       "started": time.time(), "deadline": time.time() + 900})
    old = d / f"{os.getpid()}-deadbeef.json"
    old.write_text(body)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    young = d / f"{os.getpid()}-cafe0001.json"
    young.write_text(body)
    stale_tmp = d / ".1-aaaa.json.tmp"
    stale_tmp.write_text("{")
    os.utime(stale_tmp, (time.time() - 3600, time.time() - 3600))
    assert model_lease.live(tmp_path, server=SRV) == [], "без замка — не живые"
    assert old.exists() and young.exists(), "читатель ничего не удаляет"
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream"):
        assert not old.exists() and not stale_tmp.exists(), "писатель убрал старых сирот"
        assert young.exists(), "молодой незапертый файл не тронут"


def test_a_dead_process_releases_its_lease(tmp_path):
    """Аренду держит другой процесс: жива; процесс убит — читатель видит
    свободный flock. Ядро, не таймер."""
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
        ready, _, _ = select.select([proc.stdout], [], [], 30)
        assert ready, "дочерний процесс не взял аренду за 30 с"
        assert proc.stdout.readline().strip() == "held"
        assert len(model_lease.live(tmp_path, server="s")) == 1
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert model_lease.live(tmp_path, server="s") == [], "смерть процесса снимает аренду без таймера"


# ------------------------------------------------------------- живость

def test_stall_threshold_comes_from_the_request_read_timeout():
    """Порог зависания — из read-таймаута того же запроса, не третья
    константа рядом с двумя таймаутами транспорта (I1 DS, критика 1 GLM):
    транспорт сам обрывает молчание дольше таймаута, читатель смотрит чуть
    выше — иначе живой префилл документа (молчит до таймаута) сочли бы
    зависанием. Подняли DOC_STREAM_TIMEOUT — порог поднялся сам."""
    for read in (45.0, 120.0, 300.0, 600.0):
        assert model_lease.stall_for(read) > read
    assert model_lease.stall_for(None) > llm.DOC_STREAM_TIMEOUT[1], "без таймаута — не ниже документного стрима"
    st = model_lease.Lease("/nonexistent", server=SRV, engine="ollama", kind="stream",
                           read_timeout=llm.DOC_STREAM_TIMEOUT[1])
    assert st.stall > llm.DOC_STREAM_TIMEOUT[1]


def test_deadline_is_the_only_time_criterion_and_it_rolls_on_bytes(tmp_path, monkeypatch):
    """Стрим: deadline = последний байт + порог; молчание дольше — висит, её не
    щадят; байт пришёл — deadline уехал. Не-стрим: прогресса не наблюдаем,
    deadline = старт + порог (read-таймаут + запас): дольше живёт лишь запрос,
    которому транспорт что-то читает. `started + timeout` для стрима — ложь
    (таймаут между байтами), поэтому второго критерия нет (C2 GLM, I4 DS)."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream", read_timeout=300) as st:
        assert st.deadline == clock.t + st.stall
        clock.t += st.stall - 1
        assert not model_lease.live(tmp_path, server=SRV)[0]["stalled"], "префилл молчит, но порог не вышел"
        clock.t += 2
        assert model_lease.live(tmp_path, server=SRV)[0]["stalled"], "молчит дольше порога — висит"
        st.progress()                                      # байт пришёл
        assert st.deadline == clock.t + st.stall
        assert not model_lease.live(tmp_path, server=SRV)[0]["stalled"], "байт катит deadline вперёд"
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="complete", read_timeout=45) as c:
        assert c.deadline == c.started + c.stall and c.stall > 45
        clock.t += c.stall + 1
        assert model_lease.live(tmp_path, server=SRV)[0]["stalled"], "не-стрим дольше своего порога — висит"


def test_progress_publications_are_throttled(tmp_path, monkeypatch):
    """Читатель смотрит раз в RESTART_POLL = 5 с — публиковать каждый чанк
    незачем: 50 чанков за 10 с — не больше трёх публикаций, deadline в памяти
    катится на каждом, на диск уезжает при первой после паузы."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    published = []
    real_replace = model_lease.os.replace
    monkeypatch.setattr(model_lease.os, "replace", lambda a, b: (published.append(1), real_replace(a, b))[1])
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream") as st:
        for _ in range(50):
            clock.t += 0.2
            st.progress()
        assert st.deadline == clock.t + st.stall
        on_disk = json.loads(st.path.read_text())["deadline"]
        assert st.deadline - on_disk <= model_lease.PROGRESS_EVERY_S + 0.2, "диск отстаёт не больше окна троттлинга"
    assert 2 <= len(published) <= 4, f"пятьдесят чанков за десять секунд дали {len(published)} публикаций"


# -------------------------------------------------------------- читатель

def test_the_lock_decides_liveness_and_content_only_decides_stalled(tmp_path):
    """Инвариант читателя (I1 GLM / C2 DS): замок держат → владелец жив, даже
    если содержимое не читается (полупустой файл под рукой писателя); тогда
    deadline неизвестен и аренда НЕ висит — ложная «живая» стоит один цикл
    грейса, ложная «мёртвая» — убитую генерацию."""
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream") as st:
        st.path.write_text('{"pid": 123, "ser')          # порванная запись под нашим же замком
        live = model_lease.live(tmp_path, server=SRV)
        assert len(live) == 1 and not live[0]["stalled"] and live[0]["unreadable"]
        assert model_lease.live(tmp_path, server="https://gateway.example/v1"), \
            "сервер не прочитан — фильтр по адресу отсеивать живого владельца не вправе"
        st.path.write_text("[1, 2]")                      # не dict — тоже «живая, без полей»
        assert len(model_lease.live(tmp_path, server=SRV)) == 1
        assert "pid ?" in model_lease.describe(model_lease.live(tmp_path, server=SRV))


def test_reader_rereads_a_file_replaced_under_its_hand(tmp_path, monkeypatch):
    """Читатель открыл старый inode, а писатель тем временем опубликовал новый:
    судить по прежнему содержимому нельзя — сверка inode и перечитывание
    (M3 GLM: фантом соседа-читателя — тот же класс)."""
    with model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream") as st:
        real_load = model_lease.json.load
        swapped = []

        def load_then_swap(f):
            info = real_load(f)
            if not swapped:
                swapped.append(1)
                st._written = 0.0
                st.deadline = 42.0                        # новая публикация с другим deadline
                st._publish()
            return info

        monkeypatch.setattr(model_lease.json, "load", load_then_swap)
        live = model_lease.live(tmp_path, server=SRV, now=0.0)
        assert live and live[0]["deadline"] == 42.0, "прочитано свежее содержимое, не подменённое"


def test_broken_json_orphans_and_missing_dir_do_not_break_the_reader(tmp_path):
    assert model_lease.live(tmp_path) == []
    d = model_lease.lease_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "1-garbage.json").write_text("{not json")       # без замка — сирота, не считается
    assert model_lease.live(tmp_path) == []


def test_reader_failure_is_reported_once_and_does_not_block_restart(tmp_path, monkeypatch):
    """Сенсор недоступен (каталог не читается, ошибка внутри читателя) — это не
    «никого»: одна строка в лог, перезапуск решается как прежде. Молча
    выключенная защита неотличима от честной пустоты (I3 DS / M2 GLM)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(llm_health, "_sensor_reported", False)
    monkeypatch.setattr(llm_health, "_sensor_worked", False)     # ветка «не работал ни разу»: флаги — на процесс
    monkeypatch.setattr(model_lease, "live", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("нет доступа")))
    said: list[str] = []
    assert llm_health.busy_with_ours(LOCAL, log=said.append) is None
    assert llm_health.busy_with_ours(LOCAL, log=said.append) is None
    assert len(said) == 1 and "не прочитались" in said[0] and "PermissionError" in said[0]
    assert llm_health._spare(LOCAL, said.append, force=False) is False, "без сенсора — перезапуск как прежде"


# --------------------------------------------------------- шов стрима

class _FakeResponse:
    """Ответ requests для стрима: контекстный менеджер с iter_lines и close."""

    status_code = 200

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def iter_lines(self):
        yield from self._lines


def _leased(tmp_path, lines, *, read_timeout=300):
    lease = model_lease.Lease(tmp_path, server=SRV, engine="ollama", kind="stream",
                              read_timeout=read_timeout).__enter__()
    return llm.LLM._LeasedStream(_FakeResponse(lines), lease), lease


def test_leased_stream_releases_the_lease_with_the_response(tmp_path):
    """Опасное направление — не утечка (GC снимет flock), а прокси, который НЕ
    отдаёт аренду: демон держал бы замок сам у себя вечно, и `_spare`
    откладывал бы все перезапуски до смерти процесса (I3 GLM)."""
    proxy, lease = _leased(tmp_path, [b'{"a":1}', b'{"b":2}'])
    with proxy as r:
        assert r.status_code == 200, "остальное делегируется ответу"
        assert list(r.iter_lines()) == [b'{"a":1}', b'{"b":2}']
        assert model_lease.live(tmp_path, server=SRV), "внутри with аренда жива"
    assert model_lease.live(tmp_path, server=SRV) == [] and proxy._r.closed
    proxy, lease = _leased(tmp_path, [b'{"a":1}', b'{"b":2}', b'{"c":3}'])
    with proxy as r:
        for line in r.iter_lines():
            break                                          # выход на середине стрима
    assert model_lease.live(tmp_path, server=SRV) == [], "выход из with на середине — аренда снята"
    proxy, lease = _leased(tmp_path, [])
    proxy.close()
    assert model_lease.live(tmp_path, server=SRV) == [] and proxy._r.closed, "close() снимает аренду"


def test_leased_stream_counts_only_payload_lines_as_progress(tmp_path, monkeypatch):
    """`: keepalive` и пустое `data:` — «жив и молчит»: шлюз держал бы аренду
    вечно, а stalled не наступал бы никогда (I1 DS). NDJSON-объект и `data:`
    с телом — прогресс."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    proxy, lease = _leased(tmp_path, [b": keepalive 1/1", b"", b"data:", b"data: ", b'{"tok":1}',
                                      b'data: {"choices":[]}', b"data: [DONE]"])
    seen = []
    with proxy as r:
        for line in r.iter_lines():
            clock.t += 100
            seen.append((line, lease.deadline))
    started = lease.started
    deadlines = [d for _, d in seen]
    assert deadlines[0] == deadlines[1] == deadlines[2] == deadlines[3] == started + lease.stall, \
        "комментарии и пустые data: deadline не двигают"
    assert deadlines[4] > deadlines[3] and deadlines[5] > deadlines[4] and deadlines[6] > deadlines[5]
    assert llm.LLM._is_payload(b'{"x":1}') and llm.LLM._is_payload(b"data: {}") and llm.LLM._is_payload(b"data:[DONE]")
    assert not llm.LLM._is_payload(b": ping") and not llm.LLM._is_payload(b"data:") and not llm.LLM._is_payload(b"  ")


def test_transport_seams_are_the_only_way_to_the_model():
    """Структурный сторож: любое обращение к `requests` в llm.py — только в
    двух швах с арендой (_open_stream, _post_busy) и двух названных
    исключениях (embed — 0,2 с, убить не жалко; проба llm_health.probe — тот,
    кто спрашивает). Ловится и `requests.request`, и `requests.Session` (M2 DS)."""
    allowed = {"_open_stream", "_post_busy", "embed",
               "_models_available"}       # GET списка моделей — метаданные, не генерация
    tree = ast.parse((REPO / "src" / "llm.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "requests" \
                    and node.func.attr not in {"RequestException", "ConnectionError", "Timeout", "HTTPError"}:
                assert fn.name in allowed, f"requests.{node.func.attr} мимо швов с арендой: {fn.name}"
    src = (REPO / "src" / "llm.py").read_text(encoding="utf-8")
    assert "Session(" not in src and "http.client" not in src, "сессии и сырой HTTP — мимо аренды"


# ------------------------------------------------------ решающий llm_health

def _lease_file(tmp_path, *, server=SRV, stalled=False):
    """Живая (или висящая) аренда чужого процесса: файл под нашим же flock."""
    lease = model_lease.Lease(tmp_path, server=server, engine="ollama", kind="stream").__enter__()
    if stalled:
        lease.deadline = time.time() - 1
        lease._written = 0.0
        lease._publish()
    return lease


def test_slow_server_with_a_live_lease_is_not_restarted(tmp_path, monkeypatch):
    """Грейс истёк, проба SLOW — но у нашего процесса живая аренда: сервер
    занят длинной работой, а не завис. Перезапуска нет, вызывающий идёт в
    очередь (True). Раньше здесь тикала константа и убивала работу (12.08)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.SLOW)
    monkeypatch.setattr(llm_health, "_restart",
                        lambda cfg, log, **kw: pytest.fail("живую работу перезапуском не убивают"))
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    lease = _lease_file(tmp_path)
    try:
        said: list[str] = []
        assert llm_health.ensure_alive(LOCAL, log=said.append, wait=0.05) is True
        assert any("занята живой работой" in m and f"pid {os.getpid()}" in m for m in said), said
        assert not any(SRV in m for m in said), "адрес сервера в лог не идёт"
    finally:
        lease.__exit__(None, None, None)


def test_slow_server_with_only_a_stalled_lease_is_restarted(tmp_path, monkeypatch):
    """Аренда есть, но прогресса нет дольше порога — это зависание, перезапуск как прежде."""
    charoite_paths.use_data_root(tmp_path, replace=True)
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
    charoite_paths.use_data_root(tmp_path, replace=True)
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
    наследует защиту; force — ручной аварийный перезапуск, и у него есть
    вызывающий (`force_restart` ← doctor --restart-llm), а не только тесты
    (I2 DS / M4 GLM)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    ran = []
    monkeypatch.setattr(llm_health.subprocess, "run", lambda *a, **kw: ran.append(a[0]))
    monkeypatch.setattr(llm_health, "listener_path", lambda url: None)
    monkeypatch.setattr(llm_health, "restart_commands", lambda *a, **kw: [["true"]])
    lease = _lease_file(tmp_path)
    try:
        said: list[str] = []
        assert llm_health._restart(LOCAL, said.append) is False and not ran
        assert any("отложен" in m for m in said)
        assert llm_health.force_restart(LOCAL, said.append) is True and ran
        assert any("поверх живой работы" in m and f"pid {os.getpid()}" in m for m in said)
    finally:
        lease.__exit__(None, None, None)
    ran.clear()
    assert llm_health._restart(LOCAL, lambda m: None) is True and ran, "без аренд — перезапуск как прежде"
    doctor = (REPO / "scripts" / "doctor.py").read_text(encoding="utf-8")
    assert "force_restart(" in doctor and "--restart-llm" in doctor, "ручной выход должен быть у человека, не только в тестах"


# ------------------------------------------------------ круг 2 (DS I1–I3, M2)

REMOTE = {"llm": {"base_url": "http://192.168.1.50:11434", "model": "qwen3.6:35b-a3b", "allow_remote": True}}


def test_restart_never_touches_a_server_that_is_not_ours(tmp_path, monkeypatch):
    """Инвариант модуля «перезапуск — только для loopback» держался единственным
    вызывающим (ensure_alive); `--restart-llm` на облачной или удалённой
    установке убил бы ЛОКАЛЬНУЮ Ollama с эмбеддером и доложил об успехе (круг 2
    DS I1). Запрет — в самом перезапуске, его наследует и force."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    ran = []
    monkeypatch.setattr(llm_health.subprocess, "run", lambda *a, **kw: ran.append(a[0]))
    monkeypatch.setattr(llm_health, "listener_path", lambda url: None)
    monkeypatch.setattr(llm_health, "restart_commands", lambda *a, **kw: [["true"]])
    said: list[str] = []
    assert llm_health._restart(REMOTE, said.append) is False
    assert llm_health._restart(REMOTE, said.append, force=True) is False
    assert llm_health.force_restart(REMOTE, said.append) is False
    assert not ran and all("не локальный" in m for m in said) and len(said) == 3


def test_writer_failure_is_reported_once(tmp_path, monkeypatch, capsys):
    """Каталог аренд не пишется — генерация идёт без защиты, а читатель видит
    честно пустой каталог и молчит. Говорит писатель: одна строка на процесс
    (круг 2 DS I2); `selfcheck` называет причину до встречи."""
    monkeypatch.setattr(model_lease, "_writer_reported", False)
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        with model_lease.Lease(blocked, server=SRV, engine="e", kind="stream") as a:
            assert a.path is None, "аренды нет, но генерация не упала"
        with model_lease.Lease(blocked, server=SRV, engine="e", kind="stream"):
            pass
        err = capsys.readouterr().err
        assert err.count("аренды модели не пишутся") == 1 and "PermissionError" in err
        assert model_lease.selfcheck(blocked), "самопроверка должна назвать причину"
        assert model_lease.selfcheck(tmp_path) == "", "годный каталог — пустая причина"
        assert model_lease.live(tmp_path, server="selfcheck") == [], "самопроверка за собой убрала"
    finally:
        blocked.chmod(0o700)


def test_a_filesystem_without_flock_drops_the_record_not_the_sensor(tmp_path, monkeypatch):
    """ENOTSUP от flock на одной записи не роняет `live()` целиком: иначе один
    файл на ФС без замков выключал бы защиту для всех аренд разом (круг 2 DS
    I3). Запись не судится — не считается; сенсор жив."""
    with model_lease.Lease(tmp_path, server=SRV, engine="e", kind="stream") as st:
        real = model_lease.fcntl.flock
        odd = st.path.with_name("999-nolock.json")
        odd.write_text(st.path.read_text())

        def flock(f, op):
            if pathlib.Path(f.name) == odd:
                raise OSError(45, "Operation not supported")
            return real(f, op)

        monkeypatch.setattr(model_lease.fcntl, "flock", flock)
        live = model_lease.live(tmp_path, server=SRV)
        assert [x["path"] for x in live] == [str(st.path)], "своя аренда видна, чужая без замка не судится"
        charoite_paths.use_data_root(tmp_path, replace=True)
        assert llm_health.busy_with_ours(LOCAL) is not None, "сенсор не упал"


def test_restart_deferred_for_a_fresh_lease_means_queue_not_failure(tmp_path, monkeypatch):
    """Аренда появилась между проверкой SLOW-ветки и kill: `_restart` отказал —
    это очередь за живой работой (True, как при BUSY), а не «не оживили»
    (круг 2 DS M2)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(llm_health, "probe", lambda cfg, timeout=None: llm_health.SLOW)
    monkeypatch.setattr(llm_health.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm_health, "listener_path", lambda url: None)
    monkeypatch.setattr(llm_health, "restart_commands", lambda *a, **kw: [["true"]])
    monkeypatch.setattr(llm_health.subprocess, "run", lambda *a, **kw: pytest.fail("kill под живой арендой"))
    calls = {"n": 0}
    real = llm_health.busy_with_ours
    holder: list = []

    def busy(cfg, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            holder.append(_lease_file(tmp_path))       # аренда появилась сразу после первой проверки
            return []
        return real(cfg, **kw)

    monkeypatch.setattr(llm_health, "busy_with_ours", busy)
    try:
        said: list[str] = []
        assert llm_health.ensure_alive(LOCAL, log=said.append, wait=0.05) is True
        assert any("отложен ради живой работы" in m for m in said)
    finally:
        for h in holder:
            h.__exit__(None, None, None)


def test_a_sensor_that_worked_and_broke_holds_the_restart(tmp_path, monkeypatch):
    """Разовый отказ сенсора до первого успеха — fail-open (как до аренд); отказ
    ПОСЛЕ успешных чтений — каталог снесён, права сменились — держит перезапуск
    до ручного --restart-llm: иначе каждый SLOW кончался бы перезапуском под
    живую работу, системно и без голоса в логе (круг 2 GLM, критика 1)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(llm_health, "_sensor_reported", False)
    monkeypatch.setattr(llm_health, "_sensor_worked", False)
    said: list[str] = []
    assert llm_health.busy_with_ours(LOCAL, log=said.append) == [] and llm_health._sensor_worked
    monkeypatch.setattr(model_lease, "live", lambda *a, **kw: (_ for _ in ()).throw(PermissionError("снесли")))
    assert llm_health.busy_with_ours(LOCAL, log=said.append) is None
    assert any("работал и перестал" in m for m in said)
    assert llm_health._spare(LOCAL, said.append, force=False) is True, "слепой сенсор после успеха — не убиваем"
    assert llm_health._spare(LOCAL, said.append, force=True) is False, "ручной выход остаётся"


def test_orphan_sweep_is_throttled_per_process(tmp_path, monkeypatch):
    """Уборка сирот — не чаще раза в ORPHAN_GRACE_S на процесс: на горячем пути
    каждого POST и ретрая она умножалась бы на частоту попыток ровно тогда,
    когда машина задыхается (круг 2 GLM, критика 2)."""
    clock = _Clock()
    monkeypatch.setattr(model_lease, "time", clock)
    monkeypatch.setattr(model_lease, "_last_sweep", 0.0)
    d = model_lease.lease_dir(tmp_path)
    d.mkdir(parents=True)

    def orphan(name):
        p = d / name
        p.write_text("{}")
        os.utime(p, (clock.t - 3600, clock.t - 3600))
        return p

    first = orphan("1-aaaa0001.json")
    with model_lease.Lease(tmp_path, server=SRV, engine="e", kind="stream"):
        assert not first.exists(), "первая аренда убрала сироту"
    second = orphan("1-aaaa0002.json")
    clock.t += 10
    with model_lease.Lease(tmp_path, server=SRV, engine="e", kind="stream"):
        assert second.exists(), "через 10 с уборка не повторяется"
    clock.t += model_lease.ORPHAN_GRACE_S
    with model_lease.Lease(tmp_path, server=SRV, engine="e", kind="stream"):
        assert not second.exists(), "после окна — убрала"
