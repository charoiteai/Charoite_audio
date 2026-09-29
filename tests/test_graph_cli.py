"""Командная строка пакета графа (`charoite_graph.cli`, №323 PR 2).

Вход зовётся функцией `main(argv)` в процессе теста; транспорт двери векторов
подменяется на том же шве, что у сторожа сети, — `urllib.request.urlopen`.
Поведение колеса целиком (точка входа из `entry_points.txt`, ловушка окружения,
режимы кэша) держит проба пакета в `tests/test_entry_points_contract.py`.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import sys
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from charoite_graph import cli, graph_search, safe_write  # noqa: E402
from charoite_graph.model_seam import Embedder  # noqa: E402


class _Ответ:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _ollama(seen: list | None = None, *, dead: bool = False, no_empty: bool = False):
    """Подставной /api/embed: вектор из частот букв, как у примера README. `no_empty` —
    пустой текст в пачке сервер отвергает, как вход без токенов."""
    def urlopen(request, timeout=None):
        payload = json.loads(request.data)
        if seen is not None:
            seen.append(payload)
        if dead:
            raise ConnectionRefusedError("сервер не слушает")
        if no_empty and not all(t.strip() for t in payload["input"]):
            return _Ответ(400, b'{"error": "input is empty"}')
        vectors = [[1.0 + t.lower().count(c) for c in "аеиор"] for t in payload["input"]]
        return _Ответ(200, json.dumps({"embeddings": vectors}).encode("utf-8"))
    return urlopen


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    graph = tmp_path / "граф"
    graph.mkdir()
    (graph / "Платёжный шлюз.md").write_text("Шлюз принимает платежи и передаёт их в [[Биллинг]].\n",
                                            encoding="utf-8")
    (graph / "Биллинг.md").write_text("Биллинг выставляет счета.\n", encoding="utf-8")
    return graph


def _model(data: pathlib.Path) -> list[str]:
    return ["--model-url", "http://127.0.0.1:11434", "--model", "частоты", "--data-dir", str(data)]


def _files(root: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


def test_search_without_a_model_is_lexical_and_says_why(tmp_path, capsys):
    """Без адреса модели — лексика: статус «не проверено семантикой» с причиной-отказом,
    источники полем, а не разбором «•» из текста; ничего не пишется."""
    graph = _graph(tmp_path)
    before = _files(tmp_path)

    assert cli.main(["search", str(graph), "платежи", "--json"]) == cli.EXIT_OK

    out = capsys.readouterr()
    assert "Платёжный шлюз.md" in out.out, "JSON без экранирования: пути читаются глазами"
    got = json.loads(out.out)
    assert got["ready"] is True and got["status"] == "unverified", got
    assert cli.REFUSED in got["reason"] and cli.REFUSED in out.err
    assert got["sources"] and got["sources"][0] == "Платёжный шлюз.md", got["sources"]
    assert _files(tmp_path) == before, "search без --data-dir ничего не создаёт"


def test_index_then_search_is_confident_and_the_cache_is_private(tmp_path, capsys, monkeypatch):
    """`index` собирает векторы в `--data-dir`, `search` их читает — статус `confident`.
    Кэш — только владельцу: процесс ставит маску 0o077 на время работы и возвращает
    прежнюю; манифест пишется с режимом при создании."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama())
    graph, data = _graph(tmp_path), tmp_path / "кэш"
    old = os.umask(0o022)
    try:
        assert cli.main(["index", str(graph), *_model(data)]) == cli.EXIT_OK
        assert os.umask(0o022) == 0o022, "маска процесса возвращена"
    finally:
        os.umask(old)
    assert "векторы: 2 файлов, ожидают 0" in capsys.readouterr().out

    assert cli.main(["search", str(graph), "кто принимает платежи", "--json", *_model(data)]) == cli.EXIT_OK
    got = json.loads(capsys.readouterr().out)
    assert got["status"] == "confident" and "Платёжный шлюз.md" in got["sources"], got
    for p in [data, *data.rglob("*")]:
        assert stat.S_IMODE(p.stat().st_mode) & 0o077 == 0, f"{p}: {oct(p.stat().st_mode)}"


def test_index_with_a_dead_server_exits_one(tmp_path, capsys, monkeypatch):
    """Сервер не ответил — векторы собраны не все: код 1 и причина в stderr, а не
    «успех» с нулём файлов (входной круг 1 по №323 PR 2, I4)."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama(dead=True))
    graph = _graph(tmp_path)

    assert cli.main(["index", str(graph), *_model(tmp_path / "кэш")]) == cli.EXIT_LEFT

    out = capsys.readouterr()
    assert "ожидают 2" in out.out and "не ответил" in out.err, out   # причина — заметка индекса, а не общая фраза


@pytest.mark.parametrize("command", ["search", "index"])
def test_a_folder_without_notes_is_empty_not_nothing_found(tmp_path, capsys, monkeypatch, command):
    """Папка без заметок — «индекс пуст», код 3, у обеих команд: непрогретый `Result`
    несёт статус `empty`, и без этой ветки ответ читался бы «ничего не найдено»."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama())
    empty = tmp_path / "пусто"
    empty.mkdir()
    (empty / "картинка.png").write_bytes(b"\x89PNG")
    argv = ["search", str(empty), "платежи"] if command == "search" else ["index", str(empty),
                                                                          *_model(tmp_path / "кэш")]

    assert cli.main(argv) == cli.EXIT_EMPTY

    out = capsys.readouterr()
    assert out.out == "" and "индекс пуст" in out.err


@pytest.mark.parametrize("argv, why", [
    (["search", "{g}", "q", "--model-url", "http://127.0.0.1:1"], "требует --model и --data-dir"),
    (["search", "{g}", "q", "--model-url", "http://127.0.0.1:1", "--model", "м"], "требует --model и --data-dir"),
    (["search", "{g}", "q", "--data-dir", "{t}/кэш"], "только вместе с --model-url"),
    (["index", "{g}"], "нужен --model-url"),
    (["search", "{t}/нет", "q"], "не каталог"),
])
def test_bad_arguments_exit_two_and_touch_nothing(tmp_path, capsys, argv, why):
    graph = _graph(tmp_path)
    before = _files(tmp_path)

    code = cli.main([a.replace("{g}", str(graph)).replace("{t}", str(tmp_path)) for a in argv])

    assert code == cli.EXIT_USAGE and why in capsys.readouterr().err
    assert _files(tmp_path) == before


def test_argparse_exits_become_return_codes(capsys):
    """`main` — функция с кодом возврата: `SystemExit` argparse (справка, ошибка
    разбора) не пролетает к вызывающему."""
    assert cli.main(["--help"]) == 0
    assert "index" in capsys.readouterr().out
    assert cli.main(["нет-такой-команды"]) == cli.EXIT_USAGE
    assert cli.main([]) == cli.EXIT_USAGE, "команда обязательна"


@pytest.mark.parametrize("version, extra", [((3, 13, 9), {}), ((3, 14), {"color": False}), ((3, 14, 0), {"color": False})])
def test_color_is_off_only_where_argparse_has_it(version, extra):
    assert cli._no_color(version) == extra


def test_help_width_does_not_read_columns(monkeypatch, capsys):
    """Ширина справки — константа: `HelpFormatter` без неё читает `COLUMNS`."""
    monkeypatch.setenv("COLUMNS", "30")
    cli.main(["search", "--help"])
    wide = capsys.readouterr().out
    assert max(len(line) for line in wide.splitlines()) > 30, wide


def test_result_as_dict_carries_ready_and_coverage():
    """Проекция `Result` — вся: `ready` и охват, а не только статус и текст (C2 входа 1)."""
    cold = graph_search.Result([], 0, ready=False, skipped=("архив",), unread=2, service=1)
    got = cold.as_dict()
    assert got["ready"] is False and got["status"] == "empty"
    assert got["skipped"] == ["архив"] and got["unread"] == 2 and got["service"] == 1
    assert json.loads(json.dumps(got)) == got


def test_sources_follow_the_order_of_blocks_and_hops(tmp_path):
    """Источники — те же пути и в том же порядке, что блоки выдачи, переходы — следом."""
    graph = _graph(tmp_path)
    gs = graph_search.GraphSearch(graph, embedder=Embedder(lambda t, s: [], "м"), data_dir=None)
    gs.refresh(force=True)

    r = gs.search("шлюз платежи биллинг", semantic=False)

    assert r.sources, r
    heads = [b.split("\n", 1)[0].removeprefix("• ") for b in r.blocks]
    assert not r.dossiers and list(r.sources) == heads


def test_graph_search_without_a_data_dir_never_writes(tmp_path):
    """`data_dir=None` — кэша нет: чтение ноль, запись — отказ с заметкой, ни одного файла."""
    graph = _graph(tmp_path)
    gs = graph_search.GraphSearch(graph, embedder=Embedder(lambda t, s: [[1.0]] * len(t), "м"), data_dir=None)
    gs.refresh(force=True)
    before = _files(tmp_path)

    assert gs.load_vectors() == 0
    assert gs.embed_pending() == 0 and "data_dir" in gs.note
    gs.save_vectors()
    assert _files(tmp_path) == before


def test_write_text_mode_is_set_at_creation_not_carried_over(tmp_path):
    """`mode=` — режим нового файла при создании: старый 0644 не переживает перезапись.
    Без `mode` права старого файла переносятся, как всегда."""
    target = tmp_path / "манифест.json"
    target.write_text("старый", encoding="utf-8")
    target.chmod(0o644)

    assert safe_write.write_text(target, "новый", mode=0o600)
    assert target.read_text(encoding="utf-8") == "новый"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    target.chmod(0o640)       # не 0o644: при маске 022 новый файл и без переноса был бы 0o644
    assert safe_write.write_text(target, "ещё")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_write_text_mode_survives_an_orphan_tmp_of_the_same_pid(tmp_path):
    """Сирота прошлого обрыва с тем же pid не валит O_EXCL (M1 входа 2)."""
    target = tmp_path / "манифест.json"
    (tmp_path / f"манифест.json.tmp{os.getpid()}").write_text("сирота", encoding="utf-8")

    assert safe_write.write_text(target, "новый", mode=0o600)
    assert target.read_text(encoding="utf-8") == "новый"
    assert not (tmp_path / f"манифест.json.tmp{os.getpid()}").exists()


def test_reindex_tightens_an_old_world_readable_manifest(tmp_path, capsys, monkeypatch):
    """Манифест, оставшийся 0644 от библиотечного вызова по чужой маске, после `index`
    становится 0600: режим задаётся при создании, а не переносится со старого файла."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama())
    graph, data = _graph(tmp_path), tmp_path / "кэш"
    assert cli.main(["index", str(graph), *_model(data)]) == cli.EXIT_OK
    manifest = next((data / "graph_search").glob("*.json"))
    manifest.chmod(0o644)
    note = graph / "Биллинг.md"
    note.write_text("Биллинг выставляет счета и акты.\n", encoding="utf-8")
    os.utime(note, (1, 1))

    assert cli.main(["index", str(graph), *_model(data)]) == cli.EXIT_OK

    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert "Биллинг.md" in manifest.read_text(encoding="utf-8"), "пути в манифесте без экранирования"


@pytest.mark.parametrize("url", ["localhost:11434", "ftp://127.0.0.1:1"])
def test_a_bad_model_address_is_an_argument_error(tmp_path, capsys, url):
    """Адрес без http(s) — код 2 и строка, а не трассировка с кодом 1 (I1 выхода 1)."""
    graph = _graph(tmp_path)

    code = cli.main(["search", str(graph), "q", "--model-url", url, "--model", "м", "--data-dir", str(tmp_path / "к")])

    assert code == cli.EXIT_USAGE and "http(s)" in capsys.readouterr().err
    assert not (tmp_path / "к").exists()


def test_the_mask_window_is_held_under_the_lock(tmp_path, monkeypatch):
    """Маска — состояние процесса: всё окно от `umask` до возврата прежней идёт под
    замком модуля, иначе два `main` из потоков вернули бы друг другу чужую прежнюю
    маску (I2 выхода 1). Судим по замку внутри окна — порядок потоков не случаен."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama())
    graph = _graph(tmp_path)
    seen = {}
    real_open = cli._open

    def spy(args, embedder):
        seen["locked"], seen["mask"] = cli._UMASK_LOCK.locked(), os.umask(0o077)
        return real_open(args, embedder)

    monkeypatch.setattr(cli, "_open", spy)
    old = os.umask(0o022)
    try:
        assert cli.main(["index", str(graph), *_model(tmp_path / "кэш")]) == cli.EXIT_OK
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(old)
    assert seen == {"locked": True, "mask": cli.PRIVATE_UMASK}, seen


def test_an_empty_note_does_not_keep_index_pending(tmp_path, capsys, monkeypatch):
    """Пустая заметка не свидетель: `index` не ждёт для неё вектора вечно (M1 выхода 1),
    даже если сервер пустой вход отвергает и валит этим всю пачку."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama(no_empty=True))
    graph = _graph(tmp_path)
    (graph / "Без названия.md").write_text("", encoding="utf-8")

    assert cli.main(["index", str(graph), *_model(tmp_path / "кэш")]) == cli.EXIT_OK
    assert "ожидают 0" in capsys.readouterr().out


def test_index_names_what_it_did_not_read(tmp_path, capsys, monkeypatch):
    """Охват в выводе `index`: нечитаемая заметка названа, а не спрятана за «ожидают 0» (M3)."""
    monkeypatch.setattr(urllib.request, "urlopen", _ollama())
    graph = _graph(tmp_path)
    (graph / "битая.md").symlink_to(tmp_path / "нет-такого")

    assert cli.main(["index", str(graph), *_model(tmp_path / "кэш")]) == cli.EXIT_OK
    assert "не открылось файлов: 1" in capsys.readouterr().out


def test_search_walks_the_folder_whatever_the_clock_says(tmp_path, capsys, monkeypatch):
    """Обход в `_open` — принудительный: индекс с часами, для которых «только что
    обходили» (`now() < REFRESH_S` при `_refreshed_at = 0`), всё равно читает папку.
    Без `force` такой индекс остался бы пустым, и команда ответила бы кодом 3."""
    graph = _graph(tmp_path)
    real = cli.GraphSearch
    monkeypatch.setattr(cli, "GraphSearch", lambda *a, **kw: real(*a, now=lambda: 1.0, **kw))

    assert cli.main(["search", str(graph), "платежи", "--json"]) == cli.EXIT_OK
    assert "Платёжный шлюз.md" in json.loads(capsys.readouterr().out)["sources"]
