"""Порядок plan/apply и публикации: только синтетические дочерние процессы."""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import select
import socket
import subprocess
import sys
import tempfile
import time
import types

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import charoite_paths  # noqa: E402
from charoite_graph import dossier, frontmatter, safe_write  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import file_locks  # noqa: E402
import forget_meeting as forget  # noqa: E402
import nightly_dossier as night  # noqa: E402

STAMP = "2030-01-01_1000"
OTHER = "2030-01-02_1000"
THIRD = "2030-01-03_1000"
THEME = "Synthetic topic"
OLD_TOKEN = "originaltoken"
NEW_TOKEN = "replacementtoken"
WAIT_S = 10
LOCK_WAIT_S = 0.15


def _body(token=OLD_TOKEN):
    return (f"## Сейчас\n{token} [[Встречи/{STAMP}]]\n"
            "## Как пришли\nСинтетическое обсуждение.\n"
            "## Решено\nСинтетическое решение.\n"
            "## Открыто\nСинтетический вопрос.\n"
            "## Кто в теме\nСинтетическая роль.\n")


def _path(graph):
    return graph / CHAROITE.dossier_dir / f"{THEME}.md"


def _world(tmp_path, *, second_graph=False):
    root = tmp_path / "data"
    (root / "transcripts").mkdir(parents=True)
    (root / "recordings").mkdir()
    (root / "transcripts" / f"{STAMP}.md").write_text("Synthetic transcript.\n", encoding="utf-8")
    graphs = [tmp_path / "graph-a"]
    if second_graph:
        graphs.append(tmp_path / "graph-b")
    for i, graph in enumerate(graphs):
        (graph / "Ядра").mkdir(parents=True)
        (graph / "Встречи").mkdir()
        (graph / CHAROITE.dossier_dir).mkdir()
        (graph / "Ядра" / f"{THEME}.md").write_text("# Synthetic core\n", encoding="utf-8")
        for stamp in ((STAMP, OTHER, THIRD) if i == 0 else (OTHER, THIRD)):
            (graph / "Встречи" / f"{stamp}.md").write_text(
                f"# Synthetic meeting\n[[Ядра/{THEME}]]\n", encoding="utf-8")
    return root, graphs


def _seed_indexes(graph, *, dossier_file=True):
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)[THEME]
    fp = dossier.fingerprint(members, files)
    if dossier_file:
        _path(graph).write_text(dossier.render(THEME, _body(), members, files, fp, "2030-01-04"),
                                encoding="utf-8")
    entry = {"тема": THEME, "файл": f"{CHAROITE.dossier_dir}/{THEME}.md",
             "источников": len(members), "собрано": "2030-01-04", "отпечаток": fp,
             "ключи": dossier.keywords(THEME + " " + _body())}
    dossier.write_index(graph / CHAROITE.dossier_dir, [entry])
    assert all((graph / CHAROITE.dossier_dir / name).exists()
               for name in (dossier.INDEX_JSON, dossier.INDEX_MD))


class _Child:
    """JSON-барьеры через pipe; stdout продукта уходит в отдельный журнал."""

    def __init__(self, role, root, graphs):
        self.log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.command = [sys.executable, "-u", str(pathlib.Path(__file__).resolve()),
                        role, str(root), *(str(g) for g in graphs)]
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.log, bufsize=0, cwd=REPO)
        self.buffer = b""

    def __enter__(self):
        return self

    def send(self, command):
        self.proc.stdin.write((json.dumps({"command": command}) + "\n").encode())
        self.proc.stdin.flush()

    def receive(self, event):
        deadline = time.monotonic() + WAIT_S
        while b"\n" not in self.buffer:
            ready, _, _ = select.select([self.proc.stdout], [], [], max(0, deadline - time.monotonic()))
            assert ready, f"нет барьера {event}; {self.diagnostic()}"
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            assert chunk, f"дочерний процесс завершился до {event}; {self.diagnostic()}"
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        value = json.loads(line)
        assert value["event"] == event, value
        return value

    def diagnostic(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def __exit__(self, kind, value, tb):
        try:
            if self.proc.poll() is None:
                self.send("quit")
            try:
                self.proc.wait(timeout=WAIT_S)
            except subprocess.TimeoutExpired:
                # Цель — только этот созданный тестом процесс с полной известной командой.
                assert self.proc.args == self.command
                self.proc.terminate()
                self.proc.wait(timeout=WAIT_S)
            if kind is None:
                assert self.proc.returncode == 0, self.diagnostic()
        finally:
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.log.close()


def _apply(child, *, taken=True):
    child.send("apply")
    child.receive("lock-attempt")
    assert child.receive("locked")["taken"] is taken
    if taken:
        child.send("release")
    return child.receive("applied")


def _build(child, *, taken=True):
    child.send("build")
    child.receive("lock-attempt")
    assert child.receive("locked")["taken"] is taken
    if taken:
        child.send("release")
    return child.receive("built")


def _publish(child, command):
    child.send(command)
    child.receive("lock-attempt")
    assert child.receive("locked")["taken"]
    child.send("release")
    return child.receive("published")


def _index(child):
    child.send("index")
    child.receive("lock-attempt")
    assert child.receive("locked")["taken"]
    child.send("release")
    return child.receive("indexed")


def test_new_dossier_after_plan_does_not_survive_successful_forget(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    with _Child("forget", root, graphs) as f, _Child("night", root, graphs) as n:
        assert str(graph) in f.receive("planned")["selected"]
        n.receive("scanned")
        assert _build(n)["built"] == 1
        assert dossier.cites(_path(graph).read_text(encoding="utf-8"), [STAMP])
        result = _apply(f)
        assert result["done"] and not result["refused"], result
        assert not (graph / "Встречи" / f"{STAMP}.md").exists()
        assert not _path(graph).exists(), "успешный forget оставил созданное после plan досье"


def test_stale_index_does_not_keep_a_removed_dossier_in_either_existing_index(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    with _Child("night", root, graphs) as n, _Child("forget", root, graphs) as f:
        n.receive("scanned")
        f.receive("planned")
        assert _apply(f)["done"]
        assert not _path(graph).exists()
        assert dossier.load_index(graph / CHAROITE.dossier_dir) == []
        assert THEME not in (graph / CHAROITE.dossier_dir / dossier.INDEX_MD).read_text(encoding="utf-8")
        _index(n)
        entries = dossier.load_index(graph / CHAROITE.dossier_dir)
        md = (graph / CHAROITE.dossier_dir / dossier.INDEX_MD).read_text(encoding="utf-8")
        assert (entries == [], THEME not in md) == (True, True), "в обоих индексах осталась ложная тема"


def test_index_reloads_metadata_when_same_dossier_path_is_deleted_and_recreated(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph, dossier_file=False)
    with _Child("night", root, graphs) as n, _Child("publisher", root, graphs) as p:
        n.receive("scanned")
        p.receive("ready")
        assert _build(n)["built"] == 1
        new = _publish(p, "recreate")
        assert _path(graph).exists() and NEW_TOKEN in _path(graph).read_text(encoding="utf-8")
        _index(n)
        entry = dossier.load_index(graph / CHAROITE.dossier_dir)[0]
        assert (entry["отпечаток"], NEW_TOKEN in entry["ключи"], OLD_TOKEN in entry["ключи"]) == (
            new["fingerprint"], True, False), "существование пути скрыло замену тела и метаданных"


def probe_stronger_publisher_contract_outside_nightly_scope(tmp_path):
    """Архивный диагностический опыт; не регрессия обязательного контракта night."""
    root, graphs = _world(tmp_path, second_graph=True)
    with _Child("forget", root, graphs) as f, _Child("publisher", root, [graphs[1]]) as p:
        assert f.receive("planned")["selected"] == [str(graphs[0])]
        p.receive("ready")
        _publish(p, "publish")
        result = _apply(f)
        remaining = dossier.cites(_path(graphs[1]).read_text(encoding="utf-8"), [STAMP])
        # Допустим безопасный отказ до изменений либо успешная очистка нового графа.
        assert (result["refused"] and (graphs[0] / "Встречи" / f"{STAMP}.md").exists()) or (
            result["done"] and not remaining), "новый релевантный граф не попал в границу плана"


def test_replanning_does_not_accept_a_changed_existing_dossier(tmp_path):
    root, graphs = _world(tmp_path)
    _seed_indexes(graphs[0])
    with _Child("forget", root, graphs) as f, _Child("publisher", root, graphs) as p:
        f.receive("planned")
        p.receive("ready")
        _publish(p, "recreate")
        result = _apply(f)
        assert result["refused"] and not result["done"], result
        assert (root / "transcripts" / f"{STAMP}.md").exists()
        assert (graphs[0] / "Встречи" / f"{STAMP}.md").exists()
        assert NEW_TOKEN in _path(graphs[0]).read_text(encoding="utf-8")


def test_night_holding_the_real_lock_makes_forget_refuse_without_mutation(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    with _Child("night", root, graphs) as n, _Child("forget", root, graphs) as f:
        n.receive("scanned")
        f.receive("planned")
        n.send("build")
        n.receive("lock-attempt")
        assert n.receive("locked")["taken"]
        result = _apply(f, taken=False)
        assert not result["done"] and result["refused"]
        assert (graph / "Встречи" / f"{STAMP}.md").exists()
        assert (root / "transcripts" / f"{STAMP}.md").exists()
        n.send("release")
        assert n.receive("built")["built"] == 1


def test_forget_holding_the_real_lock_makes_night_skip_publication(tmp_path):
    root, graphs = _world(tmp_path)
    with _Child("forget", root, graphs) as f, _Child("night", root, graphs) as n:
        f.receive("planned")
        n.receive("scanned")
        f.send("apply")
        f.receive("lock-attempt")
        assert f.receive("locked")["taken"]
        result = _build(n, taken=False)
        assert result["built"] == 0 and result["locked"] == 1
        assert not _path(graphs[0]).exists()
        f.send("release")
        assert f.receive("applied")["done"]


def test_old_night_snapshot_is_rejected_after_successful_forget(tmp_path):
    root, graphs = _world(tmp_path)
    with _Child("night", root, graphs) as n, _Child("forget", root, graphs) as f:
        n.receive("scanned")
        f.receive("planned")
        assert _apply(f)["done"]
        result = _build(n)
        assert result["built"] == 0 and result["changed"] == 1, result
        assert not _path(graphs[0]).exists(), "проверка отпечатка вернула забытый источник"


def test_index_publication_respects_a_forget_lock_and_keeps_the_existing_indexes(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    before = {name: (graph / CHAROITE.dossier_dir / name).read_bytes()
              for name in (dossier.INDEX_JSON, dossier.INDEX_MD)}
    with _Child("forget", root, graphs) as f, _Child("night", root, graphs) as n:
        f.receive("planned")
        n.receive("scanned")
        f.send("apply")
        f.receive("lock-attempt")
        assert f.receive("locked")["taken"]
        n.send("index")
        n.receive("lock-attempt")
        assert not n.receive("locked")["taken"]
        n.receive("indexed")
        assert all((graph / CHAROITE.dossier_dir / name).read_bytes() == data
                   for name, data in before.items())
        f.send("release")
        assert f.receive("applied")["done"]
        assert dossier.load_index(graph / CHAROITE.dossier_dir) == []


def _worker(role, root, graphs):
    protocol = sys.stdout

    def send(event, **data):
        print(json.dumps({"event": event, **data}), file=protocol, flush=True)

    def receive(expected=None):
        message = json.loads(sys.stdin.readline())["command"]
        if message == "quit":
            raise SystemExit(0)
        assert expected is None or message == expected, message
        return message

    def denied(*args, **kwargs):
        raise AssertionError("network/model access is forbidden in this synthetic worker")

    charoite_paths.use_data_root(root, replace=True)
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    sys.modules["requests"] = types.SimpleNamespace(post=denied)
    sys.modules["llm"] = types.SimpleNamespace(LLM=denied)
    forget._graph_roots = lambda explicit: [explicit] if explicit is not None else graphs
    forget.brain_forget = lambda *args, **kwargs: ""
    night.generate = lambda *args, **kwargs: _body()
    real_file_lock = file_locks.graph_lock

    @contextlib.contextmanager
    def observed_file_lock(lock_dir, wait, **kwargs):
        send("lock-attempt")
        with real_file_lock(lock_dir, LOCK_WAIT_S, poll=0.01) as taken:
            send("locked", taken=taken)
            if taken:
                receive("release")
            yield taken

    with contextlib.redirect_stdout(sys.stderr):
        if role == "forget":
            p = forget.plan(STAMP, root, graph=graphs[0] if len(graphs) == 1 else None)
            acts = [*p.delete, *p.edit]
            selected = sorted(str(g) for d, g in p.lock_dirs.items()
                              if any(forget._under(q, g, d) for q in acts))
            send("planned", selected=selected)
            file_locks.graph_lock = observed_file_lock
            while receive() == "apply":
                done = forget.apply(p, yes=True)
                send("applied", done=done, refused=p.refused)
        else:
            file_locks.graph_lock = observed_file_lock
            graph = graphs[0]
            if role == "night":
                g, candidates = night._scan(graph, True, "2030-01-04", "synthetic-night")
                send("scanned", candidates=len(candidates))
                while True:
                    command = receive()
                    if command == "build":
                        assert len(candidates) == 1, len(candidates)
                        night._build(candidates[0], {})
                        send("built", built=g.built, changed=g.changed, locked=g.locked)
                    elif command == "index":
                        g.write_index(False)
                        send("indexed")
                    else:
                        raise AssertionError(command)
            else:
                # Сотрудничающий писатель проверяет расширение набора графов и
                # delete/recreate; это не подмена ночной проверки источников.
                assert role == "publisher"
                send("ready")
                while True:
                    command = receive()
                    assert command in ("publish", "recreate")
                    with night._graph_lock(graph) as taken:
                        assert taken
                        path = _path(graph)
                        if command == "recreate":
                            path.unlink()
                        files, _ = dossier.scan(graph, schema=CHAROITE)
                        members = sorted(files)
                        fp = "replacement-fingerprint"
                        text = dossier.render(THEME, _body(NEW_TOKEN), members, files, fp, "2030-01-05")
                        assert safe_write.write_text(path, text, expect_absent=True)
                    send("published", fingerprint=frontmatter.scalar_field(text, "отпечаток"))


if __name__ == "__main__":
    _worker(sys.argv[1], pathlib.Path(sys.argv[2]), [pathlib.Path(p) for p in sys.argv[3:]])
