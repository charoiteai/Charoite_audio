"""Пути этой машины не остаются в текстах встречи (№504).

Домашний каталог и корень данных в каждом тесте лежат внутри tmp_path.
Литералов чужого домашнего каталога в файле нет: публичная проверка
форматов считает такой литерал личным путём.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import sys
import unicodedata
import urllib.parse

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import charoite_paths  # noqa: E402
import daemon  # noqa: E402
import import_meeting as im  # noqa: E402
import live_sidecar  # noqa: E402
import llm  # noqa: E402
import mcp_server  # noqa: E402
import meeting_archive as ma  # noqa: E402
import name_fixes as nf  # noqa: E402
import privacy  # noqa: E402
import rebuild_transcript as rt  # noqa: E402
import transcript  # noqa: E402

MARK = "‹данные Чароита›"


def _home(tmp_path, monkeypatch, name="дом"):
    home = tmp_path / name
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: home)
    return home


def _gone(text: str, needle: str) -> bool:
    nfd = unicodedata.normalize("NFD", needle)
    nfc = unicodedata.normalize("NFC", needle)
    real = os.path.realpath(needle)
    encoded = urllib.parse.quote(needle, safe="/")
    return not any(form and form in text for form in (needle, nfd, nfc, real, encoded))


def test_home_becomes_tilde(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    out, n = privacy.scrub_local_paths(f"см. {home}/a.md конец")
    assert out == "см. ~/a.md конец" and n == 1


def test_neighbor_with_longer_name_is_left_alone(tmp_path, monkeypatch):
    nest = tmp_path / "гнездо"
    nest.mkdir()
    home = nest / "u"
    home.mkdir()
    other = nest / "u2"
    other.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: home)
    out, n = privacy.scrub_local_paths(f"{other}/x и {home}/y")
    assert out == f"{other}/x и ~/y" and n == 1


def test_nfd_form_of_home_is_replaced(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "café")
    nfd = unicodedata.normalize("NFD", str(home))
    assert nfd != str(home)
    out, n = privacy.scrub_local_paths(nfd + "/a.md")
    assert out == "~/a.md" and n == 1 and nfd not in out


def test_percent_encoding_is_replaced_anywhere(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "café")
    encoded = urllib.parse.quote(str(home), safe="/")
    assert encoded != str(home)
    lowered = encoded.replace("%C3%A9", "%c3%a9")
    assert lowered != encoded
    text = f"файл file://{encoded}/a.md и рядом {lowered}/b.md"
    out, n = privacy.scrub_local_paths(text)
    assert out == "файл file://~/a.md и рядом ~/b.md" and n == 2
    broken = encoded.replace("%C3", "%C0", 1)
    assert broken != encoded
    stayed, n_stay = privacy.scrub_local_paths(broken + "/c.md")
    assert stayed == broken + "/c.md" and n_stay == 0


# Замер 05.10 (swift, macOS): `URL(fileURLWithPath:).absoluteString` не кодирует
# «!$&'()*+,;=:@~». Строки замера — фикстура, живого Swift в тесте нет:
# «/Volumes/Data (2)/charoite» → «file:///Volumes/Data%20(2)/charoite»,
# «/Users/u 2/данные» → «file:///Users/u%202/%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%B5».
# Корень данных теста обязан быть временным (сторож conftest), поэтому хвост
# пути из замера ставится под tmp_path; сам tmp_path кодировать нечему.
_SWIFT_TAILS = {
    "Data (2)/charoite": "Data%20(2)/charoite",
    "Data (2);x/charoite": "Data%20(2);x/charoite",
    "u 2/данные": "u%202/%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%B5",
}


def _swift_root(tmp_path, monkeypatch, tail: str) -> tuple[pathlib.Path, str]:
    _home(tmp_path, monkeypatch)
    assert re.fullmatch(r"[A-Za-z0-9/_.\-]+", str(tmp_path)), (
        f"tmp_path {tmp_path} сам требует процентной записи — хвост замера к нему не приставить")
    root = tmp_path / tail
    root.mkdir(parents=True)
    charoite_paths.use_data_root(root, replace=True)
    return root, f"file://{tmp_path}/{_SWIFT_TAILS[tail]}"


@pytest.mark.parametrize("tail", ["Data (2)/charoite", "Data (2);x/charoite"])
def test_file_url_from_swift_keeps_sub_delims_and_is_still_a_needle(tail, tmp_path, monkeypatch):
    """Корень данных с «(», «)» и «;» в имени: процентная форма Swift их не
    кодирует, а `quote(safe="/")` кодирует — нужна вторая форма иглы."""
    root, url = _swift_root(tmp_path, monkeypatch, tail)
    assert urllib.parse.quote(str(root), safe="/") not in url
    out, n = privacy.scrub_local_paths(f"см. {url}/x.md")
    assert out == f"см. file://{MARK}/x.md" and n == 1


def test_swift_and_quote_forms_coincide_without_sub_delims(tmp_path, monkeypatch):
    """«u 2/данные»: обе формы совпадают, процентная игла одна, путь снят."""
    root, url = _swift_root(tmp_path, monkeypatch, "u 2/данные")
    encoded = url.removeprefix("file://")
    percent = [form for form, kind, _r in privacy._needles() if kind == "percent"]
    assert percent.count(encoded) == 1
    out, n = privacy.scrub_local_paths(f"{url}/a.md")
    assert out == f"file://{MARK}/a.md" and n == 1


def test_realpath_through_symlink_is_a_needle(tmp_path, monkeypatch):
    target = tmp_path / "настоящий"
    target.mkdir()
    link = tmp_path / "ссылка"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: link)
    real = os.path.realpath(link)
    assert not real.startswith(str(link))
    out, n = privacy.scrub_local_paths(real + "/notes.md")
    assert out == "~/notes.md" and n == 1 and real not in out


def test_data_root_outside_home_becomes_the_mark(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    root = charoite_paths.resolve_root(privacy.__file__)
    assert not str(root).startswith(str(home))
    out, n = privacy.scrub_local_paths(str(root / "transcripts" / "a.md"))
    assert out == f"{MARK}/transcripts/a.md" and n == 1


def test_home_inside_data_root_stays_a_tilde(tmp_path, monkeypatch):
    """Корень данных — родитель домашнего. Короткая игла не должна оставить имя учётки."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    home = _home(tmp_path, monkeypatch)
    out, n = privacy.scrub_local_paths(str(home / "a.md"))
    assert out == "~/a.md" and n == 1 and MARK not in out and home.name not in out


def test_text_without_needles_is_unchanged_and_second_scrub_is_noop(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    plain = "просто текст без путей"
    assert privacy.scrub_local_paths(plain) == (plain, 0)
    once, n = privacy.scrub_local_paths(f"{home}/a.md и {MARK}")
    twice, n2 = privacy.scrub_local_paths(once)
    assert n == 1 and twice == once and n2 == 0


def _arm(monkeypatch, root, live):
    rec = root / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    mic = rec / f"{live.stem}_mic.wav"
    mic.write_bytes(b"")
    monkeypatch.setattr(rt, "wait_recording",
                        lambda _rec, _stamp, label, _sr: mic if label == "mic" else None)
    monkeypatch.setattr(rt, "load_wav", lambda _p: (np.zeros(16000 * 60, dtype=np.float32), 16000))
    monkeypatch.setattr(rt, "diarize_channel", lambda *_a, **_k: [(0.0, 20.0, 0), (25.0, 45.0, 0)])
    monkeypatch.setattr(rt, "STT", lambda _cfg: object())
    monkeypatch.setattr(rt, "stt_segment", lambda *_a, **_k: "реплика")
    monkeypatch.setattr(rt, "name_speakers",
                        lambda _cfg, _lines, **_k: rt.NamesOutcome({}, rt.NamesOutcome.SILENT))


def _draft(home: pathlib.Path, note: str) -> str:
    return ("# Черновик\nреплика\n" + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + f"\n> 10:30 📌 {note}{home}/file.md\n")


def _rebuild(tmp_path, monkeypatch, home, note, graph=None):
    root = tmp_path / "данные"
    tdir = root / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    live = tdir / "2026-08-20_143000.md"
    live.write_text(_draft(home, note), encoding="utf-8")
    _arm(monkeypatch, root, live)
    monkeypatch.setattr(rt, "_LEX_CACHE", [None])
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda *_a, **_k: graph)
    cfg = {"audio": {"samplerate": 16000}, "sufler": {"user_name": "Игорь"}}
    assert rt.rebuild(live, cfg) == live
    return live


def test_rebuild_drops_the_path_from_final_and_live_and_keeps_prev_raw(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live = _rebuild(tmp_path, monkeypatch, home, "смотри ")
    final = live.read_text(encoding="utf-8")
    assert _gone(final, str(home)) and "~/file.md" in final
    copied = (live.with_name(live.stem + "_live.md")).read_text(encoding="utf-8")
    assert _gone(copied, str(home)) and "~/file.md" in copied
    prev = (live.parent / ".prev" / live.name).read_text(encoding="utf-8")
    assert str(home) in prev
    meta = live_sidecar.read(live)
    assert meta["transcript_sha256"] == live_sidecar.sha(final)
    assert meta["transcript_sha256"] == hashlib.sha256(final.encode("utf-8")).hexdigest()


def test_lexicon_alias_of_the_home_directory_does_not_save_the_path(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "Гельский")
    graph = tmp_path / "граф"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "Вельский Ян.md").write_text(
        '---\ntype: person\naliases: ["Гельский"]\n---\n# Вельский Ян\n',
        encoding="utf-8")
    live = _rebuild(tmp_path, monkeypatch, home, "Гельский смотри ", graph)
    final = live.read_text(encoding="utf-8")
    assert _gone(final, str(home)) and "~/file.md" in final
    assert "Вельский" in final, "алиас сработал на слове вне пути — лексикон шёл после скраба"


def _minutes(tmp_path, home, name="2026-09-11_1533"):
    folder = tmp_path / "встреча"
    folder.mkdir()
    live = folder / f"{name}.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    mpath = folder / f"{name}_minutes.md"
    mpath.write_text(f"# Минутки\nсмотри {home}/a.md\n", encoding="utf-8")
    return live, mpath


def test_restamp_transcript_machine_file_drops_path_and_renames(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    raw = f"**Сергей** [15:33]:\nсмотри {home}/a.md\n"
    live.write_text(raw, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(raw))
    assert nf.restamp_transcript(live, {"Сергей": "Мария"}) == 1
    text = live.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "**Мария** [15:33]:" in text and "~/a.md" in text
    assert live_sidecar.read(live)["transcript_sha256"] == live_sidecar.sha(text)
    assert (live.parent / ".prev" / live.name).read_text(encoding="utf-8") == raw


def test_restamp_transcript_hand_edit_drops_path_and_keeps_the_hash(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    clean = "**Сергей** [15:33]:\nпривет\n"
    dirty = clean + f"правка {home}/a.md\n"
    live.write_text(dirty, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(clean))
    nf.restamp_transcript(live, {"Сергей": "Мария"})
    text = live.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "**Мария**" in text
    assert live_sidecar.read(live)["transcript_sha256"] == live_sidecar.sha(clean)


def _banner(*labels: str) -> str:
    return transcript.names_pending_line(transcript.NAMES_PENDING_NOTE, labels)


def _count_rewrites(monkeypatch) -> list:
    calls: list = []
    real = nf.review_bridge.rewrite_file

    def spy(path, transform, what):
        calls.append(path)
        return real(path, transform, what)

    monkeypatch.setattr(nf.review_bridge, "rewrite_file", spy)
    return calls


def test_restamp_transcript_machine_file_path_rename_and_banner_in_one_write(tmp_path, monkeypatch):
    """Машинный текст: путь снят, заголовок переименован, плашка (#501)
    пересчитана — одной записью; хеш сайдкара по байтам на диске."""
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    raw = (f"# Встреча\n\n{_banner('Собеседник 1', 'Собеседник 2')}\n\n"
           f"**Собеседник 1** [15:33]:\nсмотри {home}/a.md\n\n**Собеседник 2** [15:34]:\nда\n")
    live.write_text(raw, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(raw))
    calls = _count_rewrites(monkeypatch)

    assert nf.restamp_transcript(live, {"Собеседник 1": "Анна"}) == 1

    assert calls == [live]
    text = live.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text and "**Анна** [15:33]:" in text
    assert "безымянные: Собеседник 2" in text and "Собеседник 1" not in text
    assert live_sidecar.read(live)["transcript_sha256"] == hashlib.sha256(live.read_bytes()).hexdigest()
    assert (live.parent / ".prev" / live.name).read_text(encoding="utf-8") == raw


def test_restamp_transcript_hand_edit_keeps_the_banner_and_the_hash(tmp_path, monkeypatch):
    """Ручной текст: путь снят и заголовок переименован, но плашка прежняя,
    хеш сайдкара не сдвинут — иначе правка стала бы машинной."""
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    base = (f"# Встреча\n\n{_banner('Собеседник 1', 'Собеседник 2')}\n\n"
            "**Собеседник 1** [15:33]:\nа\n\n**Собеседник 2** [15:34]:\nб\n")
    live.write_text(base + f"правка руками {home}/a.md\n", encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(base))

    assert nf.restamp_transcript(live, {"Собеседник 1": "Анна"}) == 1

    text = live.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "**Анна** [15:33]:" in text
    assert "безымянные: Собеседник 1, Собеседник 2" in text
    assert live_sidecar.read(live)["transcript_sha256"] == live_sidecar.sha(base)


def test_restamp_transcript_machine_file_path_only_leaves_the_banner(tmp_path, monkeypatch):
    """Машинный текст без переименований: снимается только путь. Плашка,
    чей список разошёлся с заголовками, не переписывается — доправка
    идёт только при переименовании."""
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    stale = _banner("Собеседник 1", "Собеседник 2")
    raw = (f"# Встреча\n\n{stale}\n\n"
           f"**Собеседник 1** [15:33]:\nсмотри {home}/a.md\n")
    live.write_text(raw, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(raw))

    assert nf.restamp_transcript(live, {"Борис": "Анна"}) == 0

    text = live.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text
    assert stale in text
    assert live_sidecar.read(live)["transcript_sha256"] == hashlib.sha256(live.read_bytes()).hexdigest()


def test_name_fixes_restamp_minutes_rewrites_when_only_the_path_changes(tmp_path, monkeypatch):
    """Только путь: файл переписан, а .prev/ не сдвинут — прошлое поколение
    байт в байт цело, как на базе, где .prev писался лишь при правке."""
    home = _home(tmp_path, monkeypatch)
    live, mpath = _minutes(tmp_path, home)
    prev = mpath.parent / ".prev" / mpath.name
    assert nf.restamp_minutes(live, {}) is False
    assert mpath.read_text(encoding="utf-8") == "# Минутки\nсмотри ~/a.md\n"
    assert not prev.exists(), ".prev создан ради одного скраба пути"

    previous = "# Минутки\r\nпрошлое поколение\n".encode("utf-8")
    prev.parent.mkdir(exist_ok=True)
    prev.write_bytes(previous)
    mpath.write_text(f"# Минутки\nсмотри {home}/b.md\n", encoding="utf-8")
    assert nf.restamp_minutes(live, {}) is False
    assert mpath.read_text(encoding="utf-8") == "# Минутки\nсмотри ~/b.md\n"
    assert prev.read_bytes() == previous


def test_name_fixes_restamp_minutes_rename_still_keeps_prev(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, mpath = _minutes(tmp_path, home)
    raw = "**Участники:** Сергей\n" + f"смотри {home}/a.md\n"
    mpath.write_text(raw, encoding="utf-8")
    assert nf.restamp_minutes(live, {"Сергей": "Мария"}) is True
    text = mpath.read_text(encoding="utf-8")
    assert "Мария" in text and _gone(text, str(home))
    assert (mpath.parent / ".prev" / mpath.name).read_text(encoding="utf-8") == raw


def test_restamp_transcript_path_only_leaves_prev_untouched(tmp_path, monkeypatch):
    """Стенограмма без переименований: путь снят, хеш машинного текста
    обновлён по байтам на диске, а .prev/ прошлого поколения цел."""
    home = _home(tmp_path, monkeypatch)
    live, _mpath = _minutes(tmp_path, home)
    raw = f"**Сергей** [15:33]:\nсмотри {home}/a.md\n"
    live.write_text(raw, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(raw))
    prev = live.parent / ".prev" / live.name

    assert nf.restamp_transcript(live, {"Борис": "Анна"}) == 0
    assert not prev.exists(), ".prev создан ради одного скраба пути"

    previous = "**Сергей** [15:33]:\nпрошлое поколение\n".encode("utf-8")
    prev.parent.mkdir(exist_ok=True)
    prev.write_bytes(previous)
    again = f"**Сергей** [15:33]:\nсмотри {home}/b.md\n"
    live.write_text(again, encoding="utf-8")
    live_sidecar.remember(live, "transcript_sha256", live_sidecar.sha(again))

    assert nf.restamp_transcript(live, {"Борис": "Анна"}) == 0
    text = live.read_text(encoding="utf-8")
    assert text == "**Сергей** [15:33]:\nсмотри ~/b.md\n"
    assert live_sidecar.read(live)["transcript_sha256"] == hashlib.sha256(live.read_bytes()).hexdigest()
    assert prev.read_bytes() == previous


def test_rebuild_restamp_minutes_rewrites_when_only_the_path_changes(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, mpath = _minutes(tmp_path, home)
    assert rt.restamp_minutes(live, {}) is True
    text = mpath.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text


def test_canonize_file_empty_lexicon_still_drops_the_path(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _live, mpath = _minutes(tmp_path, home)
    graph = tmp_path / "пустой"
    (graph / "Люди").mkdir(parents=True)
    monkeypatch.setattr(rt, "_LEX_CACHE", [None])
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda *_a, **_k: graph)
    rt.canonize_file(mpath, {})
    text = mpath.read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text


def test_archive_canon_drops_the_path_and_second_pass_is_unchanged(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    main = tdir / "2026-09-20_1000_Тема.md"
    main.write_text("# Встреча\n", encoding="utf-8")
    src = tdir / "2026-09-20_1000_Тема_minutes.md"
    raw = f"## Решения\nсмотри {home}/a.md\n"
    src.write_text(raw, encoding="utf-8")
    canon = tmp_path / "папка" / "Минутки.md"
    canon.parent.mkdir()
    first = ma.lay_canon(src, canon, main)
    assert first.action == ma.CanonOutcome.CREATED
    stored = canon.read_text(encoding="utf-8")
    assert _gone(stored, str(home)) and "~/a.md" in stored
    meta = live_sidecar.read(main)
    assert meta["canon_minutes_sha256"] == live_sidecar.sha(stored)
    assert meta["canon_minutes_source_sha256"] == live_sidecar.sha(raw)
    second = ma.lay_canon(src, canon, main)
    assert second.action == ma.CanonOutcome.UNCHANGED
    assert canon.read_text(encoding="utf-8") == stored


def _run_import(tmp_path, monkeypatch, name, body: bytes, cfg=None):
    root = tmp_path / "root"
    tdir = root / "transcripts"
    tdir.mkdir(parents=True)
    src = tmp_path / name
    src.write_bytes(body)

    class Ok:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(im.subprocess, "run", lambda *_a, **_k: Ok())
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(im, "_cfg", lambda: cfg or {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda _cfg: None)
    monkeypatch.setattr(im, "find_meeting_note", lambda _cfg, _t, **_kw: None)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", str(src), "--date", "2026-09-05", "--time", "1200"])
    im.main()
    return tdir / "2026-09-05_1200.md"


def test_import_text_drops_the_path(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    body = (f"Участник А смотрит {home}/a.md. " + "план встречи подробно. " * 12).encode("utf-8")
    assert len(body) >= 200
    text = _run_import(tmp_path, monkeypatch, "Заметки.txt", body).read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text


def test_import_subs_drop_the_path(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    vtt = (
        "WEBVTT\n\n00:00:03.000 --> 00:00:06.000\n"
        f"<v Участник А>смотри {home}/a.md\n"
    )
    text = _run_import(tmp_path, monkeypatch, "zoom.vtt", vtt.encode("utf-8")).read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text


def test_import_vocabulary_does_not_save_the_path(tmp_path, monkeypatch):
    """Слово словаря, равное имени каталога, не должно переписать путь раньше иглы."""
    home = _home(tmp_path, monkeypatch, "Гельский")
    body = (f"Гельский смотрит {home}/a.md. " + "план встречи подробно. " * 12).encode("utf-8")
    cfg = {"log": {"transcripts_dir": "transcripts"},
           "sufler": {"vocabulary": {"Гельский": "Вельский"}}}
    text = _run_import(tmp_path, monkeypatch, "Заметки.txt", body, cfg).read_text(encoding="utf-8")
    assert _gone(text, str(home)) and "~/a.md" in text and "Вельский" in text


def _seed_prev(mpath: pathlib.Path, previous: str) -> pathlib.Path:
    prev = mpath.parent / ".prev" / mpath.name
    prev.parent.mkdir(exist_ok=True)
    prev.write_text(previous, encoding="utf-8")
    return prev


def test_canonize_file_keeps_previous_generation_on_one_alias(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _live, mpath = _minutes(tmp_path, home)
    mpath.write_text("# Минутки\nГельский сказал\n", encoding="utf-8")
    previous = "# Минутки\nпрошлое поколение\n"
    prev = _seed_prev(mpath, previous)
    graph = tmp_path / "граф"
    (graph / "Люди").mkdir(parents=True)
    (graph / "Люди" / "Вельский Ян.md").write_text(
        '---\ntype: person\naliases: ["Гельский"]\n---\n# Вельский Ян\n',
        encoding="utf-8")
    monkeypatch.setattr(rt, "_LEX_CACHE", [None])
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda *_a, **_k: graph)
    rt.canonize_file(mpath, {})
    assert prev.read_text(encoding="utf-8") == previous
    assert mpath.read_text(encoding="utf-8") == "# Минутки\nВельский сказал\n"


def test_canonize_file_keeps_previous_generation_on_a_path(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _live, mpath = _minutes(tmp_path, home)
    previous = "# Минутки\nпрошлое поколение\n"
    prev = _seed_prev(mpath, previous)
    graph = tmp_path / "пустой"
    (graph / "Люди").mkdir(parents=True)
    monkeypatch.setattr(rt, "_LEX_CACHE", [None])
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda *_a, **_k: graph)
    rt.canonize_file(mpath, {})
    assert prev.read_text(encoding="utf-8") == previous
    assert mpath.read_text(encoding="utf-8") == "# Минутки\nсмотри ~/a.md\n"


def test_rebuild_restamp_minutes_keeps_previous_generation(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, mpath = _minutes(tmp_path, home)
    previous = "# Минутки\nпрошлое поколение\n"
    prev = _seed_prev(mpath, previous)
    assert rt.restamp_minutes(live, {}) is True
    assert prev.read_text(encoding="utf-8") == previous
    assert mpath.read_text(encoding="utf-8") == "# Минутки\nсмотри ~/a.md\n"


def test_name_fixes_restamp_minutes_true_when_participants_change(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    live, mpath = _minutes(tmp_path, home)
    raw = f"# Минутки\n**Участники:** Сергей\nсмотри {home}/a.md\n"
    mpath.write_text(raw, encoding="utf-8")
    assert nf.restamp_minutes(live, {"Сергей": "Мария"}) is True
    assert mpath.read_text(encoding="utf-8") == "# Минутки\n**Участники:** Мария\nсмотри ~/a.md\n"
    assert (mpath.parent / ".prev" / mpath.name).read_text(encoding="utf-8") == raw


def test_append_hint_scrubs_header_and_body(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "café")
    tr = tmp_path / "2026-10-04_153300.md"
    tr.write_text("# Встреча\n", encoding="utf-8")
    daemon.append_hint(tr, f"заголовок {home}/a.md", f"тело {home}/b.md")
    text = tr.with_name(tr.stem + "_hints.md").read_text(encoding="utf-8")
    assert text == "\n## заголовок ~/a.md\nтело ~/b.md\n"
    assert _gone(text, str(home))


def test_transcript_note_scrubs_the_live_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "café")

    class Frozen(transcript.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 15, 33)

    monkeypatch.setattr(transcript.dt, "datetime", Frozen)
    monkeypatch.setattr(transcript.meeting_stamp, "now", lambda: "2026-10-04_153300")
    tr = transcript.Transcript(tmp_path / "стенограммы")
    tr.note(f"📌 смотри {home}/a.md")
    text = tr.path.read_text(encoding="utf-8")
    assert text == (
        "# Встреча 2026-10-04_153300\n"
        "\n"
        "---\n"
        "## Ко-мышление (📌 КТ · 💎 факты · 💭 мысли)\n"
        "> 15:33 📌 смотри ~/a.md\n"
    )
    assert _gone(text, str(home))


def test_home_stops_before_punctuation_and_spares_neighbors(tmp_path, monkeypatch):
    nest = tmp_path / "гнездо"
    nest.mkdir()
    home = nest / "u"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: home)
    h = str(home)
    blob = (
        f"{h}. {h}, \"{h}\" `{h}` {h} конец {h}.\n"
        f"{nest}/u2/x {nest}/u.old/x {nest}/u-x/x {nest}/u_x/x {nest}/uX/x"
    )
    out, n = privacy.scrub_local_paths(blob)
    assert out == (
        "~. ~, \"~\" `~` ~ конец ~.\n"
        f"{nest}/u2/x {nest}/u.old/x {nest}/u-x/x {nest}/u_x/x {nest}/uX/x"
    ) and n == 6
    assert privacy.scrub_local_paths(h) == ("~", 1)
    assert privacy.scrub_local_paths(h + ".") == ("~.", 1)
    assert privacy.scrub_local_paths(h + "\0x") == ("~\0x", 1)


def test_longer_data_root_wins_over_a_shorter_home_prefix(tmp_path, monkeypatch):
    nest = tmp_path / "гнездо"
    nest.mkdir()
    home = nest / "u"
    home.mkdir()
    root = nest / "u 2" / "данные"
    root.mkdir(parents=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: home)
    charoite_paths.use_data_root(root, replace=True)
    out, n = privacy.scrub_local_paths(str(root / "файл.md"))
    assert out == f"{MARK}/файл.md" and n == 1


def test_data_root_symlink_and_its_realpath_become_the_mark(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    real = tmp_path / "вне" / "настоящие"
    real.mkdir(parents=True)
    link = tmp_path / "вне" / "ссылка"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(charoite_paths, "resolve_root", lambda _marker: link)
    real_s = os.path.realpath(link)
    assert str(link) != real_s
    out, n = privacy.scrub_local_paths(f"{link}/a.md и {real_s}/a.md")
    assert out == f"{MARK}/a.md и {MARK}/a.md" and n == 2


def test_root_inside_home_is_covered_by_the_tilde(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    root = home / "данные"
    root.mkdir()
    charoite_paths.use_data_root(root, replace=True)
    out, n = privacy.scrub_local_paths(str(root / "a.md"))
    assert out == "~/данные/a.md" and n == 1 and MARK not in out


def test_root_under_home_realpath_stays_a_tilde(tmp_path, monkeypatch):
    real_home = tmp_path / "настоящий-дом"
    real_home.mkdir()
    link = tmp_path / "дом-ссылка"
    link.symlink_to(real_home, target_is_directory=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: link)
    root = real_home / "данные"
    root.mkdir()
    charoite_paths.use_data_root(root, replace=True)
    out, n = privacy.scrub_local_paths(str(root / "a.md"))
    assert out == "~/данные/a.md" and n == 1 and MARK not in out
    assert str(link) != os.path.realpath(link)


def test_filesystem_root_is_not_a_needle(monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: pathlib.Path("/"))
    assert privacy.scrub_local_paths("смотри / tmp") == ("смотри / tmp", 0)


def test_two_character_home_is_a_needle(monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: pathlib.Path("/a"))
    assert privacy.scrub_local_paths("/a/b") == ("~/b", 1)


def test_minutes_draft_scrubs_and_puts_the_marker_first(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, "café")
    out = daemon.minutes_draft(f"смотри {home}/a.md\nи всё")
    assert out == transcript.MINUTES_DRAFT_MARK + "\nсмотри ~/a.md\nи всё"
    assert _gone(out, str(home))


def test_rewrite_meeting_text_hook_only_for_machine_text_with_edits(tmp_path, monkeypatch):
    """Контракт крюка без знания о плашке: машинный текст с правкой — вызван;
    правки нет или текст ручной — не вызван."""
    home = _home(tmp_path, monkeypatch)
    calls: list[str] = []

    def fixup(text: str) -> str:
        calls.append(text)
        return text + "доправка\n"

    def run(raw: str, owned: bool, edit_n: int) -> str:
        path = tmp_path / f"t{len(list(tmp_path.glob('t*.md')))}.md"
        path.write_text(raw, encoding="utf-8")
        live_sidecar.remember(path, "transcript_sha256", live_sidecar.sha(raw if owned else "чужое"))
        nf.rewrite_meeting_text(path, lambda t: (t.replace("а", "б") if edit_n else t, edit_n),
                                live=path, sha_key="transcript_sha256", what="тест",
                                keep_prev=False, machine_fixup=fixup)
        return path.read_text(encoding="utf-8")

    assert run(f"а {home}/x\n", owned=True, edit_n=1) == "б ~/x\nдоправка\n"
    assert calls == ["б ~/x\n"]
    assert run(f"а {home}/x\n", owned=True, edit_n=0) == "а ~/x\n"
    assert run(f"а {home}/x\n", owned=False, edit_n=1) == "б ~/x\n"
    assert len(calls) == 1


def test_mcp_minutes_scrub_before_write_and_passport(tmp_path, monkeypatch):
    """Инструмент «Минутки»: путь, повторённый моделью, не ложится на диск;
    паспорт производной снят с байтов на диске."""
    home = _home(tmp_path, monkeypatch)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-13_1200.md"
    live.write_text("# Встреча\nреплика\n", encoding="utf-8")
    charoite_paths.use_data_root(tmp_path, replace=True)

    class Fake:
        lang = "ru"
        recording_block = llm.LLM.recording_block
        document_model = llm.LLM.document_model
        engine, model, mlx_model = "ollama", "проба", ""

        def fit(self, text):
            return text

        def complete(self, prompt, **kw):
            return f"- **Кто** — смотри {home}/a.md — срок"

    monkeypatch.setattr(mcp_server, "_client", lambda: Fake())
    out = mcp_server.sufler_make_minutes()

    mpath = tdir / "2026-09-13_1200_minutes.md"
    text = mpath.read_text(encoding="utf-8")
    assert "Минутки сохранены" in out and _gone(text, str(home)) and "~/a.md" in text
    assert live_sidecar.read(live)["minutes_sha256"] == hashlib.sha256(mpath.read_bytes()).hexdigest()


def test_write_minutes_scrubs_every_path_form_and_writes_atomically(tmp_path, monkeypatch):
    """Писатель финальных минуток: дом, его realpath и корень данных снимаются
    до диска, tmp не остаётся, ``note`` зовётся один раз байтами с диска."""
    real_home = tmp_path / "настоящий дом"
    real_home.mkdir()
    home = tmp_path / "дом"
    home.symlink_to(real_home, target_is_directory=True)
    monkeypatch.setattr(pathlib.Path, "home", lambda _cls=None: home)
    real_text = os.path.realpath(home)
    assert real_text != str(home)
    root = tmp_path / "корень"
    root.mkdir()
    charoite_paths.use_data_root(root, replace=True)
    assert charoite_paths.resolve_root(privacy.__file__) == root
    folder = tmp_path / "встреча"
    folder.mkdir()
    mpath = folder / "2026-10-05_1700_minutes.md"
    mpath.write_text("прежние минутки\n", encoding="utf-8")
    raw = f"# Минутки\nсм. {home}/a.md, {root}/b.md и {real_text}/c.md\n"
    notes: list[str] = []

    doc = daemon.write_minutes(mpath, raw, notes.append)

    on_disk = mpath.read_text(encoding="utf-8")
    assert on_disk == doc == f"# Минутки\nсм. ~/a.md, {MARK}/b.md и ~/c.md\n"
    for needle in (str(home), str(root), real_text):
        assert _gone(on_disk, needle), needle
    assert notes == [on_disk]
    assert sorted(p.name for p in folder.iterdir()) == [mpath.name]


def test_protocol_writes_minutes_only_through_write_minutes():
    """«Протокол» демона (``_do_summary`` — замыкание ``main()``, напрямую не
    вызвать) пишет минутки только писателем со скрабом внутри: ни своей
    записи файла, ни своего replace. Порядок строк здесь больше не важен —
    скраб у писателя. Второй будущий писатель в обход — Долг зоны №609."""
    src = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    body = src[src.index("    def _do_summary():"):]
    body = body[: body.index("\n    def ", 10)]
    assert body.count("write_minutes(mpath, doc, note_minutes_written)") == 1, body
    for writer in (".write_text(", ".write_bytes(", ".replace(", "open(", "safe_write."):
        assert writer not in body, f"«Протокол» пишет мимо write_minutes: {writer}"


def test_scrub_count_goes_to_stderr_not_the_daemon_stdout(tmp_path, monkeypatch, capsys):
    """stdout демона — построчный JSON для приложения: строка скраба идёт в stderr."""
    home = _home(tmp_path, monkeypatch)
    privacy.scrub_local_paths(f"{home}/a")
    captured = capsys.readouterr()
    assert captured.out == "" and "заменены: 1" in captured.err


def test_home_at_the_very_end_of_the_text_is_replaced(tmp_path, monkeypatch):
    """Игла кончается ровно на конце текста: цикл не выходит за его край."""
    home = _home(tmp_path, monkeypatch)
    assert privacy.scrub_local_paths(f"см. {home}") == ("см. ~", 1)
    assert privacy.scrub_local_paths(f"см. {home}/") == ("см. ~/", 1)


def test_text_without_a_slash_is_returned_as_is(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert privacy.scrub_local_paths("") == ("", 0)
    assert privacy.scrub_local_paths("без путей") == ("без путей", 0)


@pytest.mark.parametrize("home", ["/", "//", "///"])
def test_degenerate_home_is_not_a_needle(monkeypatch, home):
    """Дом без имени каталога иглой не становится: «//» в тексте — не путь машины."""
    monkeypatch.setenv("HOME", home)
    text = "ссылка https://x, a // b, конец //"
    assert privacy.scrub_local_paths(text) == (text, 0)


def _rt_minutes(tmp_path, body: str):
    live = tmp_path / "2026-09-11_1533.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    mpath = tmp_path / "2026-09-11_1533_minutes.md"
    mpath.write_text(body, encoding="utf-8")
    return live, mpath


def test_rebuild_restamp_minutes_logs_the_marker_only_when_it_was_there(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    live, mpath = _rt_minutes(tmp_path, f"{transcript.MINUTES_DRAFT_MARK}\n# Минутки\n")
    assert rt.restamp_minutes(live, {}) is True
    assert mpath.read_text(encoding="utf-8") == "# Минутки\n"
    assert "(снят маркер черновика)" in capsys.readouterr().out

    mpath.write_text(f"# Минутки\nсмотри {home}/a.md\n", encoding="utf-8")
    assert rt.restamp_minutes(live, {}) is True
    out = capsys.readouterr().out
    assert f"минутки перештампованы: {mpath.name}\n" in out and "маркер" not in out


def test_rebuild_restamp_minutes_refusals_answer_false(tmp_path, monkeypatch, capsys):
    """Каждый отказ записи — ровно False и своя строка лога; нет файла — тихо."""
    live, mpath = _rt_minutes(tmp_path, "# Минутки\n")
    LostRace = rt.safe_write.LostRace
    cases = [
        (LostRace(mpath, "x", LostRace.CHANGED), "меняются под пересборкой"),
        (LostRace(mpath, "x", LostRace.UNREACHABLE), "(не прочитались)"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "плохой байт"), "не прочитались): плохой байт"),
        (OSError("нет доступа"), "(не прочитались)"),
        (LostRace(mpath, "x", LostRace.GONE), "(stat не удался)"),
    ]
    for exc, said in cases:
        def boom(*_a, _e=exc, **_k):
            raise _e
        monkeypatch.setattr(rt.name_fixes, "rewrite_meeting_text", boom)
        assert rt.restamp_minutes(live, {}) is False
        assert said in capsys.readouterr().out
    # Без подмены: настоящий rewrite_file сам замечает, что файла нет.
    monkeypatch.undo()
    mpath.unlink()
    assert rt.restamp_minutes(live, {}) is False
    assert capsys.readouterr().out == ""

