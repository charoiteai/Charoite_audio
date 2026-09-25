"""№262: «откуда эта запись» — ключ `transcript_origin` в сайдкаре стенограммы
и дедуп повторов импорта по нему.

Ключ пишет импорт один раз, в общем хвосте трёх веток (аудио, субтитры,
текст); повтор и родитель-сканер его не пишут. Дедуп читает ключ у
кандидатов со своим сайдкаром, нет ключа — прежний разбор шапки; правило
совпадения пары (имя, размер) одно для обоих.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import channel_trace  # noqa: E402
import charoite_paths  # noqa: E402
import import_meeting as im  # noqa: E402
import live_sidecar  # noqa: E402
import transcript_origin  # noqa: E402

VTT = """WEBVTT

00:00:03.000 --> 00:00:06.000
<v Участник А>Начнём с плана.

00:00:07.000 --> 00:00:12.000
<v Участник Б>Ядра/Тема — на следующей неделе.
"""
DAY, HHMM = "2026-09-05", "1200"
STAMP = f"{DAY}_{HHMM}"


def _sidecar(tpath: pathlib.Path) -> pathlib.Path:
    return tpath.with_name(tpath.name + ".live.json")


def _put_origin(tpath: pathlib.Path, raw) -> None:
    """Ключ в собственный сайдкар стенограммы — сырым значением (для мусора)."""
    _sidecar(tpath).write_text(json.dumps({transcript_origin.SIDECAR_KEY: raw}), encoding="utf-8")


def _origin(name: str, size: int, kind: str = "text") -> str:
    return json.dumps({"name": name, "size": size, "kind": kind})


@pytest.fixture
def run_import(tmp_path, monkeypatch):
    """Прямой импорт через main() с подменой дочерних процессов: транскрибация
    пишет стенограмму сама (как `transcribe_file`), граф и хвост — «успех»."""
    root = tmp_path / "root"
    tdir = root / "transcripts"
    tdir.mkdir(parents=True)
    calls: list[list[str]] = []

    class Ok:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        if str(cmd[1]).endswith("transcribe_file.py"):
            src, hhmm, day = pathlib.Path(cmd[2]), cmd[3], cmd[4]
            (tdir / f"{day}_{hhmm}.md").write_text(
                f"# Встреча {day}_{hhmm} — запись {src.name}\n\n[12:00:01] Участник А: начнём\n",
                encoding="utf-8")
        return Ok()

    monkeypatch.setattr(im.subprocess, "run", fake_run)
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(im, "_cfg", lambda: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(im, "find_meeting_note", lambda cfg, t, **kw: None)

    def run(src: pathlib.Path) -> pathlib.Path:
        monkeypatch.setattr(sys, "argv", ["import_meeting.py", str(src),
                                          "--date", DAY, "--time", HHMM])
        before = os.umask(0o022)
        os.umask(before)
        try:
            im.main()
        finally:
            os.umask(before)
        return tdir

    run.calls = calls
    return run


@pytest.mark.parametrize("name, body, kind", [
    ("Recording.m4a", b"\0" * 2048, "audio"),
    ("zoom.vtt", VTT.encode("utf-8"), "subs"),
    ("Заметки.txt", ("Участник А говорит о плане. " * 20).encode("utf-8"), "text"),
], ids=["audio", "subs", "text"])
def test_новая_встреча_любой_ветки_получает_ключ_с_именем_размером_и_видом(
        tmp_path, run_import, name, body, kind):
    src = tmp_path / name
    src.write_bytes(body)
    tdir = run_import(src)
    tpath = tdir / f"{STAMP}.md"
    assert tpath.exists()
    assert transcript_origin.of(tpath) == (name, len(body), kind)
    raw = json.loads(_sidecar(tpath).read_text(encoding="utf-8"))[transcript_origin.SIDECAR_KEY]
    assert json.loads(raw) == {"name": name, "size": len(body), "kind": kind}, \
        "одна запись {name, size, kind}, без штампа — он живёт ключом `stamp`"


def test_повтор_не_трогает_сайдкар_старой_встречи_и_не_пишет_новой(tmp_path, run_import, capsys):
    """Повтор узнан по шапке (ключ у старой встречи снят руками): писатель в
    ветке повтора записал бы ключ СТАРОЙ стенограмме."""
    src = tmp_path / "zoom.vtt"
    src.write_text(VTT, encoding="utf-8")
    tdir = run_import(src)
    tpath = tdir / f"{STAMP}.md"
    _sidecar(tpath).write_text(json.dumps({"stamp": "x"}), encoding="utf-8")
    before = sorted(p.name for p in tdir.iterdir())

    run_import(src)

    assert "повтор не нужен" in capsys.readouterr().out
    assert json.loads(_sidecar(tpath).read_text(encoding="utf-8")) == {"stamp": "x"}
    assert sorted(p.name for p in tdir.iterdir()) == before, "новой стенограммы и сайдкара нет"


def test_отказ_сайдкара_не_роняет_импорт_а_говорит_строкой(tmp_path, run_import, monkeypatch, capsys):
    src = tmp_path / "zoom.vtt"
    src.write_text(VTT, encoding="utf-8")
    monkeypatch.setattr(live_sidecar, "remember", lambda *a, **kw: False)
    tdir = run_import(src)
    out = capsys.readouterr().out
    assert "откуда запись не записано" in out
    assert transcript_origin.of(tdir / f"{STAMP}.md") is None
    assert any(c[1].endswith("graph_updater.py") for c in run_import.calls), "импорт дошёл до графа"


def test_ключ_узнаёт_повтор_у_стенограммы_без_хвоста_шапки(tmp_path):
    tdir = tmp_path
    tpath = tdir / f"{STAMP}_Тема.md"
    tpath.write_text(f"# Встреча {STAMP} — Тема\n\nтекст\n", encoding="utf-8")
    _put_origin(tpath, _origin("Recording.m4a", 4096, "audio"))

    assert im.find_repeat(tdir, STAMP, "Recording.m4a", 4096) == (tpath, True)
    assert im.find_repeat_anywhere(tdir, "Recording.m4a", 4096) == tpath
    assert im.import_stamp(tdir, STAMP, "Recording.m4a", "17", 4096) == (STAMP, tpath)
    # другая запись с тем же именем: ключ говорит «не она», минута занята
    assert im.find_repeat(tdir, STAMP, "Recording.m4a", 5000) == (None, True)
    assert im.find_repeat_anywhere(tdir, "Recording.m4a", 5000) is None
    assert im.import_stamp(tdir, STAMP, "Recording.m4a", "17", 5000) == (f"{STAMP}17", None)


def test_ключ_сильнее_шапки(tmp_path):
    """Ключ есть — шапку не читаем: иначе было бы два ответа на один вопрос."""
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text(f"# Встреча {STAMP} — импорт zoom.vtt (100 Б)\n", encoding="utf-8")
    _put_origin(tpath, _origin("другой.vtt", 7, "subs"))
    assert im.find_repeat(tmp_path, STAMP, "zoom.vtt", 100) == (None, True)
    assert im.find_repeat_anywhere(tmp_path, "zoom.vtt", 100) is None
    assert im.find_repeat(tmp_path, STAMP, "другой.vtt", 7) == (tpath, True)


def test_стенограмма_без_сайдкара_узнаётся_по_шапке_как_раньше(tmp_path):
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text(f"# Встреча {STAMP} — импорт zoom.vtt (100 Б)\n", encoding="utf-8")
    assert im.find_repeat(tmp_path, STAMP, "zoom.vtt", 100) == (tpath, True)
    assert im.find_repeat_anywhere(tmp_path, "zoom.vtt", 100) == tpath
    assert im.find_repeat(tmp_path, STAMP, "zoom.vtt", 101) == (None, True)


@pytest.mark.parametrize("raw", [
    "не json",
    json.dumps(["zoom.vtt", 100, "subs"]),
    json.dumps({"size": 100, "kind": "subs"}),
    json.dumps({"name": "", "size": 100, "kind": "subs"}),
    json.dumps({"name": 5, "size": 100, "kind": "subs"}),
    json.dumps({"name": "zoom.vtt", "size": "100", "kind": "subs"}),
    json.dumps({"name": "zoom.vtt", "size": True, "kind": "subs"}),
    json.dumps({"name": "zoom.vtt", "size": -1, "kind": "subs"}),
    json.dumps({"name": "zoom.vtt", "size": 100, "kind": "video"}),
    {"name": "zoom.vtt", "size": 100, "kind": "subs"},      # не строкой — чужой тип
])
def test_мусор_в_ключе_это_ключа_нет_и_разбор_шапки(tmp_path, raw):
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text(f"# Встреча {STAMP} — импорт zoom.vtt (100 Б)\n", encoding="utf-8")
    _put_origin(tpath, raw)
    assert transcript_origin.of(tpath) is None
    # шапка говорит «другая запись» — мусорный ключ не склеивает её с «zoom2.vtt»
    assert im.find_repeat(tmp_path, STAMP, "zoom.vtt", 100) == (tpath, True)
    assert im.find_repeat(tmp_path, STAMP, "zoom2.vtt", 100) == (None, True)
    assert im.find_repeat_anywhere(tmp_path, "zoom.vtt", 100) == tpath


def test_битый_сайдкар_и_нулевой_размер(tmp_path):
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text("# Встреча\n", encoding="utf-8")
    _sidecar(tpath).write_text("{оборван", encoding="utf-8")
    assert transcript_origin.of(tpath) is None
    _put_origin(tpath, _origin("пустой.txt", 0))
    assert transcript_origin.of(tpath) == ("пустой.txt", 0, "text"), "ноль байт — размер, не мусор"


PAIRS = [("zoom.vtt", 100), ("zoom.vtt", None), ("zoom.vtt", 101), ("zoom2.vtt", 100),
         ("zoom2.vtt", None), ("zoom.vt", 100)]


@pytest.mark.parametrize("call", PAIRS)
def test_ключ_и_шапка_отвечают_одинаково_на_одних_парах(tmp_path, call):
    by_key, by_head = tmp_path / "key", tmp_path / "head"
    by_key.mkdir()
    by_head.mkdir()
    k = by_key / f"{STAMP}.md"
    k.write_text(f"# Встреча {STAMP}\n", encoding="utf-8")
    _put_origin(k, _origin("zoom.vtt", 100, "subs"))
    (by_head / f"{STAMP}.md").write_text(f"# Встреча {STAMP} — импорт zoom.vtt (100 Б)\n",
                                         encoding="utf-8")
    name, size = call
    key_minute = im.find_repeat(by_key, STAMP, name, size)[0] is not None
    head_minute = im.find_repeat(by_head, STAMP, name, size)[0] is not None
    assert key_minute == head_minute == im.same_origin(("zoom.vtt", 100), call)
    key_any = im.find_repeat_anywhere(by_key, name, size) is not None
    head_any = im.find_repeat_anywhere(by_head, name, size) is not None
    assert key_any == head_any, "без размера у вызова — None у обоих"
    assert key_any == (size is not None and im.same_origin(("zoom.vtt", 100), call))


def test_правило_пары_одно(tmp_path):
    assert im.same_origin(("a.m4a", 5), ("a.m4a", 5))
    assert im.same_origin(("a.m4a", None), ("a.m4a", 5))
    assert im.same_origin(("a.m4a", 5), ("a.m4a", None))
    assert not im.same_origin(("a.m4a", 5), ("a.m4a", 6))
    assert not im.same_origin(("a.m4a", None), ("b.m4a", None))
    # шапка без размера (до 23.08) — размер неизвестен, как раньше
    assert im.same_source("# Встреча x — импорт a.m4a", "a.m4a", 5)
    assert not im.same_source("# Встреча x — импорт a.m4a (6 Б)", "a.m4a", 5)


def test_ключ_читается_только_у_кандидата_со_своим_сайдкаром(tmp_path, monkeypatch):
    """Кандидат без своего сайдкара в списке каталога не читается: `read` ушёл
    бы в поиск наследия глобом на каждого кандидата."""
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text("# Встреча\n", encoding="utf-8")
    _put_origin(tpath, _origin("a.m4a", 5))
    assert transcript_origin.of(tpath, set()) is None
    assert transcript_origin.of(tpath, {tpath.name + ".live.json"}) == ("a.m4a", 5, "text")
    assert transcript_origin.sidecars_in(tmp_path) == {tpath.name + ".live.json"}
    assert transcript_origin.sidecars_in(tmp_path, "2026-09-06") == set()

    reads = []
    real = live_sidecar.read
    monkeypatch.setattr(live_sidecar, "read", lambda p, *a: (reads.append(p), real(p, *a))[1])
    other = tmp_path / f"{DAY}_1300.md"
    other.write_text("# Встреча\n", encoding="utf-8")
    im.find_repeat_anywhere(tmp_path, "a.m4a", 5)
    im.find_repeat(tmp_path, f"{DAY}_1300", "a.m4a", 5)
    assert other not in reads


def test_два_писателя_одного_сайдкара_не_затирают_друг_друга(tmp_path):
    tpath = tmp_path / f"{STAMP}.md"
    tpath.write_text("# Встреча\n", encoding="utf-8")
    events = [{"label": "phone", "kind": "stopped", "at": 1.0, "reason": "no_stop", "terminal": True}]
    assert live_sidecar.remember(tpath, channel_trace.SIDECAR_KEY, json.dumps(events))
    assert transcript_origin.remember(tpath, "a.m4a", 5, transcript_origin.AUDIO)
    assert channel_trace.events_of(tpath) == events
    more = events + [{"label": "mic", "kind": "gap", "at": 2.0, "stopped_at": 1.5}]
    assert live_sidecar.remember(tpath, channel_trace.SIDECAR_KEY, json.dumps(more))
    assert transcript_origin.of(tpath) == ("a.m4a", 5, "audio")
    assert channel_trace.events_of(tpath) == more


def test_вид_только_из_трёх(tmp_path):
    with pytest.raises(ValueError):
        transcript_origin.remember(tmp_path / "x.md", "a.mp4", 5, "video")
    assert not (tmp_path / "x.md.live.json").exists()


def test_ключ_не_занят_другим_писателем():
    """Имя ключа не совпадает ни с одним ключом, который уже пишут в сайдкар."""
    taken = {"stamp", channel_trace.SIDECAR_KEY, "transcript_sha256", "minutes_sha256",
             "speakers", "names"}
    assert transcript_origin.SIDECAR_KEY not in taken
    assert not transcript_origin.SIDECAR_KEY.endswith(("_sha256", "_source_sha256", "_adopted"))
