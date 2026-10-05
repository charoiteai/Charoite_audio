"""Устранимый отказ пересборки виден человеку — код в статусе встречи (№500, часть A).

До №500 `main()` после `rebuild()` всегда писал «Готово»: записи не готовы за
45 с или пересборка упала — граф строился по живому черновику, а человек не
знал, что результат не пересобран и что его можно пересобрать кнопкой.
"""
import json
import pathlib
import subprocess
import sys

import pytest
import charoite_paths

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import live_sidecar  # noqa: E402
import meeting_stamp  # noqa: E402
import rebuild_transcript as rt  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

SHA = "a" * 64
RS = rt.RebuildSkipped


@pytest.fixture
def root(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "transcripts").mkdir()
    charoite_paths.use_data_root(tmp_path, replace=True)
    return tmp_path


def _live(root: pathlib.Path, meta: dict | None = None, stamp: str = "2026-08-12_153201") -> pathlib.Path:
    live = root / "transcripts" / f"{stamp}.md"
    live.write_text("# Встреча\nживой черновик\n", encoding="utf-8")
    if meta is not None:
        live.with_name(live.name + ".live.json").write_text(json.dumps(meta), encoding="utf-8")
    return live


# --- rebuild(): записи не готовы -------------------------------------------------

def test_records_not_ready_is_a_skipped_outcome(tmp_path, monkeypatch):
    """Файлы каналов под минутой есть, ни один не готов за ожидание — устранимый
    отказ с кодом, а не немой None: повтор после конвертации демоном его снимет."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-08-04_120314.md"
    live.write_text("# Встреча 2026-08-04_120314\n", encoding="utf-8")
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-04_120314", "mic", "pcm").write_bytes(b"\0" * 32)
    lines: list[str] = []
    monkeypatch.setattr(rt, "wait_recording", lambda *_a: None)
    monkeypatch.setattr(rt, "log", lines.append)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(rec))

    got = rt.rebuild(live, {"audio": {"samplerate": 16000}})

    assert got == RS(RS.RECORDING_NOT_READY) and isinstance(got, RS)
    assert "записи не готовы — оставляю живую стенограмму" in lines, lines


def test_only_a_neighbours_files_under_the_minute_is_a_deterministic_refusal(tmp_path, monkeypatch):
    """Под минутой лежат файлы соседки, под своим штампом — ничего (свои смёл
    ретеншн): ждать нечего, повтор даст то же — немой None, без кода и кнопки
    (Sonnet M1 выходного круга)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-08-04_120314.md"
    live.write_text("# Встреча 2026-08-04_120314\n", encoding="utf-8")
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-04_120345", "mic", "wav").write_bytes(b"RIFF")
    monkeypatch.setattr(rt.meeting_stamp, "resolve_stamp", lambda *_a, **_k: "2026-08-04_120314")
    waited: list[str] = []
    monkeypatch.setattr(rt, "wait_recording", lambda _d, _s, label, _c: waited.append(label))
    lines: list[str] = []
    monkeypatch.setattr(rt, "log", lines.append)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(rec))

    assert rt.rebuild(live, {"audio": {"samplerate": 16000}}) is None
    # ждать нечего — и не ждём: 2×45 с под rebuild.lock были бы пустыми (DS M3 круга 2)
    assert waited == [], waited
    assert "записей нет — оставляю живую стенограмму" in lines, lines


def test_unlistable_own_files_mean_wait_not_refuse(tmp_path, monkeypatch):
    """Ошибка stat своего файла — «не знаем, есть ли»: ждём и, не дождавшись, даём
    устранимый код, а не немой None и не `failed` (Sonnet M2 круга 2)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-08-04_120314.md"
    live.write_text("# Встреча 2026-08-04_120314\n", encoding="utf-8")
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-04_120314", "mic", "pcm").write_bytes(b"\0" * 32)
    real = pathlib.Path.exists

    def refuse(self, *a, **k):
        if self.parent == rec:
            raise PermissionError(13, "EACCES")
        return real(self, *a, **k)

    monkeypatch.setattr(rt.meeting_stamp, "resolve_stamp", lambda *_a, **_k: "2026-08-04_120314")
    monkeypatch.setattr(pathlib.Path, "exists", refuse)
    waited: list[str] = []
    monkeypatch.setattr(rt, "wait_recording", lambda _d, _s, label, _c: waited.append(label))
    monkeypatch.setattr(rt, "log", lambda _m: None)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(rec))

    got = rt.rebuild(live, {"audio": {"samplerate": 16000}})

    assert got == RS(RS.RECORDING_NOT_READY) and isinstance(got, RS)
    assert waited == ["mic", "blackhole"], waited


def test_skipped_is_falsy_like_the_old_none():
    for reason in (RS.RECORDING_NOT_READY, RS.CHANNEL_LOST, RS.FAILED):
        assert not RS(reason)
        assert RS(reason).reason == reason
    assert (RS.RECORDING_NOT_READY, RS.CHANNEL_LOST, RS.FAILED) == (
        "recording_not_ready", "channel_lost", "failed")
    assert RS._fields == ("reason",), "коды — константы класса, не поля кортежа"


# --- live_sidecar.machine_final ---------------------------------------------------

def test_machine_final_valid_hash(root):
    assert live_sidecar.machine_final(_live(root, {"transcript_sha256": SHA})) is True


def test_machine_final_no_hash(root):
    assert live_sidecar.machine_final(_live(root, {"stamp": "2026-08-12_153201"})) is False


def test_machine_final_no_sidecar(root):
    assert live_sidecar.machine_final(_live(root)) is False


def test_machine_final_garbage_hash(root):
    assert live_sidecar.machine_final(_live(root, {"transcript_sha256": "abc"})) is False
    assert live_sidecar.machine_final(_live(root, {"transcript_sha256": 123})) is False


@pytest.mark.parametrize("raw", [b"{\xd0 not json", b"\xff\xfe\x00garbage", b"[]", b"\"x\"", b"42"],
                         ids=["broken-json", "not-utf8", "list", "string", "number"])
def test_machine_final_unreadable_content_is_unknown(root, raw):
    """Сайдкар не читается как объект — был ли в нём хеш, не знаем: метки нет.
    Ложная пометка у встречи с записанным финалом хуже пропущенной (DS C1)."""
    live = _live(root)
    live.with_name(live.name + ".live.json").write_bytes(raw)
    assert live_sidecar.machine_final(live) is True


def test_machine_final_is_total(root, monkeypatch):
    """Любое исключение чтения — «не знаем», а не падение `main()` в `failed` (DS I1)."""
    live = _live(root, {"transcript_sha256": SHA})

    def deep(*_a, **_k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(live_sidecar.json, "loads", deep)
    assert live_sidecar.machine_final(live) is True


def test_machine_final_unknown_leaves_a_trace(root, capsys):
    """«Не знаем» пишет причину в stderr: иначе пропавшая пометка не
    диагностируется (Sonnet M1, DS M2 круга 2)."""
    live = _live(root)
    live.with_name(live.name + ".live.json").write_bytes(b"\xff\xfe")
    assert live_sidecar.machine_final(live) is True
    err = capsys.readouterr().err
    assert "не прочитан" in err and "UnicodeDecodeError" in err, err


def test_machine_final_ambiguous_sidecar_is_unknown(root):
    """Две встречи в одну минуту, своего сайдкара нет — чей хеш, не знаем: метки нет."""
    live = _live(root, stamp="2026-08-12_1532")
    tdir = root / "transcripts"
    for bare in ("2026-08-12_153201", "2026-08-12_153245"):
        (tdir / f"{bare}{live_sidecar.TAIL}").write_text(json.dumps({"stamp": bare}), encoding="utf-8")
    assert live_sidecar.sidecar_for(live) is None
    assert live_sidecar.machine_final(live) is True


def test_machine_final_oserror_is_unknown(root, monkeypatch):
    live = _live(root, {"stamp": "x"})

    def boom(*_a, **_k):
        raise PermissionError(13, "EACCES")

    monkeypatch.setattr(live_sidecar, "sidecar_for", boom)
    assert live_sidecar.machine_final(live) is True


def test_machine_final_unreadable_sidecar_is_unknown(root, monkeypatch):
    live = _live(root, {"transcript_sha256": SHA})
    real = pathlib.Path.read_text

    def refuse(self, *a, **k):
        if self.name.endswith(live_sidecar.TAIL):
            raise OSError(5, "EIO")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", refuse)
    assert live_sidecar.machine_final(live) is True


# --- main(): исход → статус ---------------------------------------------------------

def _run_main(root, monkeypatch, live, rebuild):
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "config.yaml").write_text("sufler:\n  graph: false\n", encoding="utf-8")
    calls: list[tuple] = []

    class Status:
        def __init__(self, *a, **k):
            pass

        def processing(self, *a):
            pass

        def ready(self, *a):
            calls.append(("ready", *a))

        def failed(self, *a):
            calls.append(("failed", *a))

        def has_transcript(self, _live):
            return True

    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)])
    monkeypatch.setattr(rt, "MeetingStatusStore", Status)
    monkeypatch.setattr(rt, "_take_rebuild_queue", lambda: None)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rt, "rebuild", rebuild)
    monkeypatch.setattr(rt, "log", lambda m: None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0))
    monkeypatch.setenv("CHAROITE_NO_RETRY", "1")
    rt.main()
    assert [c[0] for c in calls] == ["ready"], calls
    assert calls[0][1] == live and calls[0][2] is None
    return calls[0][3]


def _raises(*_a):
    raise RuntimeError("диаризация упала")


@pytest.mark.parametrize("meta, rebuild, expected", [
    ({}, lambda live, cfg: live, None),
    (None, lambda live, cfg: RS(RS.RECORDING_NOT_READY), RS.RECORDING_NOT_READY),
    ({"stamp": "x"}, lambda live, cfg: RS(RS.CHANNEL_LOST), RS.CHANNEL_LOST),
    ({"transcript_sha256": SHA}, lambda live, cfg: RS(RS.RECORDING_NOT_READY), None),
    (None, _raises, RS.FAILED),
    ({"transcript_sha256": SHA}, _raises, None),
    (None, lambda live, cfg: None, None),
], ids=["path", "skipped-no-hash", "channel-lost-no-hash", "skipped-machine-final",
        "exception-no-hash", "exception-after-write-final", "deterministic-none"])
def test_main_maps_rebuild_outcome_to_status(root, monkeypatch, meta, rebuild, expected):
    live = _live(root, meta)
    assert _run_main(root, monkeypatch, live, rebuild) == expected


def test_main_ambiguous_sidecar_no_mark(root, monkeypatch):
    live = _live(root, stamp="2026-08-12_1532")
    for bare in ("2026-08-12_153201", "2026-08-12_153245"):
        (root / "transcripts" / f"{bare}{live_sidecar.TAIL}").write_text("{}", encoding="utf-8")
    assert _run_main(root, monkeypatch, live, lambda live, cfg: RS(RS.FAILED)) is None


def test_main_sidecar_oserror_ready_without_mark(root, monkeypatch):
    live = _live(root)

    def boom(*_a, **_k):
        raise OSError(5, "EIO")

    monkeypatch.setattr(live_sidecar, "sidecar_for", boom)
    assert _run_main(root, monkeypatch, live, lambda live, cfg: RS(RS.RECORDING_NOT_READY)) is None


def test_main_mark_is_decided_before_the_graph_step(root, monkeypatch):
    """Шаг графа пишет хеш (ретитл обновляет его, перештамповка тоже) — решение о
    метке уже принято по состоянию до него: прогон без финала остаётся непересобранным."""
    live = _live(root)
    sidecar = live.with_name(live.name + ".live.json")

    def graph(argv, **k):
        sidecar.write_text(json.dumps({"transcript_sha256": SHA}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text("sufler:\n  graph: false\n", encoding="utf-8")
    seen: list[tuple] = []

    class Status:
        def __init__(self, *a, **k):
            pass

        def processing(self, *a):
            pass

        def ready(self, *a):
            seen.append(a)

        def has_transcript(self, _live):
            return True

    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(live)])
    monkeypatch.setattr(rt, "MeetingStatusStore", Status)
    monkeypatch.setattr(rt, "_take_rebuild_queue", lambda: None)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rt, "rebuild", lambda live, cfg: RS(RS.RECORDING_NOT_READY))
    monkeypatch.setattr(rt, "log", lambda m: None)
    monkeypatch.setattr(subprocess, "run", graph)
    monkeypatch.setenv("CHAROITE_NO_RETRY", "1")
    rt.main()
    assert seen == [(live, None, RS.RECORDING_NOT_READY)]


# --- статус встречи ---------------------------------------------------------------

def test_ready_writes_the_skip_code_only_when_given(root):
    live = _live(root)
    store = MeetingStatusStore(root, now=lambda: 10.0)
    data = json.loads(store.ready(live, None, RS.RECORDING_NOT_READY).read_text(encoding="utf-8"))
    assert data["state"] == "ready" and data["rebuild_skipped"] == "recording_not_ready"
    data = json.loads(store.ready(live, None).read_text(encoding="utf-8"))
    assert data["state"] == "ready" and "rebuild_skipped" not in data


def test_typical_duration_skips_unfinished_rebuilds(root):
    store = MeetingStatusStore(root)
    status = store.directory
    status.mkdir(parents=True)
    for i, span in enumerate((100, 200, 300)):
        (status / f"m{i}.json").write_text(json.dumps(
            {"state": "ready", "started_at": 0, "updated_at": span}), encoding="utf-8")
    for i in range(3):
        (status / f"s{i}.json").write_text(json.dumps(
            {"state": "ready", "started_at": 0, "updated_at": 5, "rebuild_skipped": "failed"}),
            encoding="utf-8")
    assert store.typical_duration() == 200
