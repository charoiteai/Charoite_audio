"""№366 (шаг 3, закрывает №370): канон минуток `Минутки.md` в папке архива
встречи — производная с паспортом (вид `canon_minutes`), как саммари и тезисы.

До правки каждая раскладка клала в канон байты машинного источника и стирала
отметки человека. Теперь раскладка переписывает канон, только пока его байты —
те, что она записала сама; любая чужая правка делает канон HUMAN, и такой канон
не трогается. Копия минуток в «Документации» — байты канона.
"""
from __future__ import annotations

import collections
import io
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import charoite_paths  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_archive as ma  # noqa: E402
import safe_write  # noqa: E402

KEY = "2026-09-20_1000_Тема"
STAMP = "2026-09-20_1000"
FOLDER = "2026-09-20 10-00 — Тема"
MINUTES = "## Решения\n- Ядра/Тема: берём план\n\n## Поручения\n- [ ] Участник А: отчёт\n- [ ] Участник Б: смета\n"
SPEECH = "# Встреча 2026-09-20_1000 — Тема\n\n[10:00:00] Участник А: начнём\n"
T0 = 1_700_000_000 * 10**9
CO = ma.CanonOutcome


@pytest.fixture(autouse=True)
def _no_live_model(модель_не_отвечает):
    """Саммари архива без модели: архивация в тестах канона не ходит в сервер."""
    return модель_не_отвечает


def _sig(p: pathlib.Path) -> tuple[int, int]:
    st = p.stat()
    return (st.st_ino, st.st_mtime_ns)


def _sidecar(main: pathlib.Path) -> pathlib.Path:
    return main.with_name(main.name + ".live.json")


def _meeting(tmp_path, minutes: str | bytes = MINUTES, *, transcript: bool = True):
    """Источник минуток, путь канона и стенограмма (владелец паспорта)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir(exist_ok=True)
    main = tdir / f"{KEY}.md"
    if transcript:
        main.write_text(SPEECH, encoding="utf-8")
    src = tdir / f"{KEY}_minutes.md"
    src.write_bytes(minutes.encode("utf-8") if isinstance(minutes, str) else minutes)
    os.utime(src, ns=(T0, T0))
    folder = tmp_path / "папка"
    folder.mkdir(exist_ok=True)
    return src, folder / "Минутки.md", main


def _state(canon, main, source_text):
    return live_sidecar.derivative_state(canon, live_sidecar.read(main) or {}, "canon_minutes",
                                         live_sidecar.sha(source_text))


# --- раскладка: создание, тишина, обновление ---------------------------------

def test_missing_canon_is_created_with_source_bytes_and_times_then_left_alone(tmp_path):
    src, canon, main = _meeting(tmp_path)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason, o.source) == (CO.CREATED, live_sidecar.FRESH, None, src)
    assert canon.read_bytes() == src.read_bytes()
    assert canon.stat().st_mtime_ns == T0, "свежесть материалов саммари читается по mtime"
    before = _sig(canon)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.UNCHANGED, live_sidecar.FRESH)
    assert _sig(canon) == before, "равный текст переписан — конфликтные копии iCloud (№361)"


@pytest.mark.parametrize("raw", [b"a\r\nb\r\n", b"a\rb\r"], ids=["crlf", "cr"])
def test_source_line_endings_are_read_like_the_oracle(tmp_path, raw):
    """Critical DS круга 13: `bytes.decode` не трогает переводы строк, и паспорт
    источника с CRLF не воспроизводился оракулом — каждый проход кончался бы
    присвоением и записью сайдкара."""
    src, canon, main = _meeting(tmp_path, raw)
    assert ma.lay_canon(src, canon, main).action == CO.CREATED
    assert canon.read_bytes() == b"a\nb\n"
    sc = _sidecar(main)
    before = (sc.read_bytes(), sc.stat().st_mtime_ns)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.UNCHANGED, live_sidecar.FRESH)
    assert (sc.read_bytes(), sc.stat().st_mtime_ns) == before, "сайдкар переписан на тихом проходе"


def test_legacy_crlf_canon_equal_to_the_source_is_adopted(tmp_path):
    src, canon, main = _meeting(tmp_path, "a\nb\n")
    canon.write_bytes(b"a\r\nb\r\n")
    before = _sig(canon)
    assert ma.lay_canon(src, canon, main).action == CO.ADOPTED
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.UNCHANGED, live_sidecar.FRESH)
    assert _sig(canon) == before


def test_our_canon_follows_a_changed_source(tmp_path):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    new = MINUTES + "- [ ] Участник А: новое поручение\n"
    src.write_text(new, encoding="utf-8")
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.UPDATED, live_sidecar.FRESH)
    assert canon.read_text(encoding="utf-8") == new
    assert _state(canon, main, new) == live_sidecar.FRESH


# --- правки человека не стираются --------------------------------------------

@pytest.mark.parametrize("edit", [
    lambda t: t.replace("- [ ] Участник А", "- [x] Участник А"),
    lambda t: t.replace("смета", "смета до пятницы"),
    lambda t: t.replace("- [ ] Участник Б: смета\n", ""),
], ids=["отметка", "текст", "удалённая строка"])
def test_edited_canon_is_kept_and_named(tmp_path, edit):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    canon.write_text(edit(MINUTES), encoding="utf-8")
    src.write_text(MINUTES + "- [ ] Участник Б: ревизия добавила\n", encoding="utf-8")
    before = (canon.read_bytes(), _sig(canon))
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.KEPT, live_sidecar.HUMAN, "канон правлен не раскладкой")
    assert o.differing > 0
    assert (canon.read_bytes(), _sig(canon)) == before
    assert "kept" in o.line() and f"строк расходится с машинной версией: {o.differing}" in o.line()


def test_differing_counts_lines_with_multiplicity():
    assert ma._differing("a\nb\n", "a\nb\n") == 0
    assert ma._differing("a\nb\n", "a\nc\n") == 2
    assert ma._differing("a\na\n", "a\n") == 1


# --- легаси без паспорта ------------------------------------------------------

def test_legacy_canon_equal_to_the_source_gets_a_passport_on_the_compared_text(tmp_path):
    src, canon, main = _meeting(tmp_path)
    canon.write_text(MINUTES, encoding="utf-8")
    before = _sig(canon)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.ADOPTED, live_sidecar.FRESH)
    meta = live_sidecar.read(main)
    assert meta["canon_minutes_sha256"] == live_sidecar.sha(MINUTES)
    assert meta["canon_minutes_source_sha256"] == live_sidecar.sha(MINUTES)
    assert meta["canon_minutes_adopted"].startswith("20")
    assert _sig(canon) == before, "присвоение файл не пишет"


def test_legacy_canon_that_differs_is_kept_without_a_passport(tmp_path):
    src, canon, main = _meeting(tmp_path)
    canon.write_text(MINUTES.replace("[ ]", "[x]"), encoding="utf-8")
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.KEPT, live_sidecar.UNKNOWN,
                                             "паспорта нет, канон отличается от источника")
    assert o.differing == 4
    assert "canon_minutes_sha256" not in (live_sidecar.read(main) or {})


def test_edit_between_reading_and_the_snapshot_check_gets_no_passport(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    canon.write_text(MINUTES, encoding="utf-8")
    real = pathlib.Path.read_text
    fired = []

    def reading(self, *a, **k):
        text = real(self, *a, **k)
        if self == canon and not fired:
            fired.append(1)
            with open(canon, "a", encoding="utf-8") as fh:
                fh.write("- [x] Участник Б: правка под рукой\n")
        return text
    monkeypatch.setattr(pathlib.Path, "read_text", reading)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.UNCHANGED, "файл менялся под рукой")
    assert "canon_minutes_sha256" not in (live_sidecar.read(main) or {})
    monkeypatch.undo()
    o = ma.lay_canon(src, canon, main)
    assert o.action == CO.KEPT and "правка под рукой" in canon.read_text(encoding="utf-8")


def test_edit_after_the_snapshot_check_turns_the_canon_human(tmp_path, monkeypatch):
    """Паспорт — на сравнённый текст, не на то, что лежит к моменту записи
    сайдкара: правка в этом окне на следующем проходе видна как HUMAN."""
    src, canon, main = _meeting(tmp_path)
    canon.write_text(MINUTES, encoding="utf-8")
    real = live_sidecar.merge

    def merging(live, updates, bare=None):
        with open(canon, "a", encoding="utf-8") as fh:
            fh.write("- [x] Участник Б: поздняя правка\n")
        return real(live, updates, bare)
    monkeypatch.setattr(live_sidecar, "merge", merging)
    assert ma.lay_canon(src, canon, main).action == CO.ADOPTED
    monkeypatch.undo()
    assert live_sidecar.read(main)["canon_minutes_sha256"] == live_sidecar.sha(MINUTES)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.KEPT, live_sidecar.HUMAN)
    assert "поздняя правка" in canon.read_text(encoding="utf-8")


def test_canon_edited_back_to_the_source_is_adopted(tmp_path):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    new = MINUTES.replace("[ ]", "[x]")
    src.write_text(new, encoding="utf-8")
    canon.write_text(new, encoding="utf-8")          # человек сделал то же, что ревизия
    assert _state(canon, main, new) == live_sidecar.HUMAN
    assert ma.lay_canon(src, canon, main).action == CO.ADOPTED
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.UNCHANGED, live_sidecar.FRESH)


def test_fresh_passport_on_a_different_text_is_not_rewritten(tmp_path):
    """FRESH при несовпадающем тексте штатно не бывает (его забирает правило
    равного текста), но сайдкар мог написать кто угодно: FRESH не пишется."""
    src, canon, main = _meeting(tmp_path)
    canon.write_text("своё\n", encoding="utf-8")
    live_sidecar.merge(main, {"canon_minutes_sha256": live_sidecar.sha("своё\n"),
                              "canon_minutes_source_sha256": live_sidecar.sha(MINUTES)})
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.UNCHANGED, live_sidecar.FRESH, None)
    assert canon.read_text(encoding="utf-8") == "своё\n"


# --- чтение не удалось --------------------------------------------------------

def test_canon_read_failure_is_failed_before_the_oracle(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    real = pathlib.Path.read_text

    def denied(self, *a, **k):
        if self == canon:
            raise PermissionError(13, "нет доступа")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", denied)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.FAILED, None, "канон не прочитался")


def test_oracle_unknown_with_a_passport_is_failed(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    src.write_text(MINUTES + "- [ ] ещё\n", encoding="utf-8")
    before = canon.read_bytes()
    monkeypatch.setattr(live_sidecar, "derivative_state", lambda *a, **k: live_sidecar.UNKNOWN)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.FAILED, "канон не прочитался")
    assert canon.read_bytes() == before


def test_stat_failure_on_an_existing_canon_is_failed(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    src.write_text(MINUTES + "- [ ] ещё\n", encoding="utf-8")
    before = canon.read_bytes()
    real = safe_write.stat_snapshot
    monkeypatch.setattr(safe_write, "stat_snapshot", lambda p: None if p == canon else real(p))
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.FAILED, live_sidecar.STALE, "канон не прочитался")
    assert canon.read_bytes() == before


def test_source_not_in_utf8_is_failed(tmp_path):
    src, canon, main = _meeting(tmp_path, MINUTES.encode("cp1251"))
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.FAILED, None, "источник не прочитался")
    assert not canon.exists()


# --- без стенограммы паспорт не пишется ---------------------------------------

def test_without_transcript_the_canon_is_created_but_no_sidecar_appears(tmp_path):
    src, canon, main = _meeting(tmp_path, transcript=False)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.CREATED, "создан без паспорта: стенограммы нет")
    assert not _sidecar(main).exists()
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.UNCHANGED, "стенограммы нет — паспорт некуда записать")
    assert not _sidecar(main).exists()
    assert canon.read_text(encoding="utf-8") == MINUTES


def test_stale_canon_without_transcript_is_not_rewritten(tmp_path):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    main.unlink()                                   # сайдкар пережил стенограмму
    assert _sidecar(main).exists()
    src.write_text(MINUTES + "- [ ] ещё\n", encoding="utf-8")
    before = canon.read_bytes()
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.UNCHANGED, live_sidecar.STALE,
                                             "стенограммы нет — канон не обновлён")
    assert canon.read_bytes() == before


# --- пустые файлы ------------------------------------------------------------

def test_empty_canon_with_a_passport_is_rewritten_under_the_gate(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    canon.write_text("", encoding="utf-8")
    snap = safe_write.stat_snapshot(canon)
    seen = []
    real = safe_write.write_text

    def spy(path, text, **kw):
        seen.append(kw)
        return real(path, text, **kw)
    monkeypatch.setattr(safe_write, "write_text", spy)
    o = ma.lay_canon(src, canon, main)
    assert o.action == CO.CREATED and canon.read_text(encoding="utf-8") == MINUTES
    assert seen[0]["expect"] == snap and not seen[0].get("expect_absent")


def test_empty_source_writes_nothing(tmp_path):
    src, canon, main = _meeting(tmp_path, "")
    for _ in (1, 2):
        o = ma.lay_canon(src, canon, main)
        assert (o.action, o.reason) == (CO.UNCHANGED, "источник пуст")
        assert not canon.exists()
    canon.write_text("старое\n", encoding="utf-8")
    before = _sig(canon)
    for _ in (1, 2):
        assert ma.lay_canon(src, canon, main).action == CO.UNCHANGED
        assert _sig(canon) == before


# --- паспорт не записался ----------------------------------------------------

def test_failed_attest_is_said_and_healed_by_the_next_pass(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    monkeypatch.setattr(live_sidecar, "attest", lambda *a, **k: False)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.CREATED, "паспорт не записан")
    # живой путь обязан это показать: иначе канон молча замер бы при смене источника
    assert o.alarming
    monkeypatch.undo()
    before = _sig(canon)
    merges = []
    real = live_sidecar.merge
    monkeypatch.setattr(live_sidecar, "merge", lambda *a, **k: merges.append(1) or real(*a, **k))
    o = ma.lay_canon(src, canon, main)
    assert o.action == CO.ADOPTED and merges == [1]
    assert live_sidecar.read(main)["canon_minutes_adopted"]
    assert _sig(canon) == before


def test_failed_attest_on_update_is_said(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    src.write_text(MINUTES + "- [ ] ещё\n", encoding="utf-8")
    monkeypatch.setattr(live_sidecar, "attest", lambda *a, **k: False)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.UPDATED, "паспорт не записан")
    assert o.alarming


def test_failed_merge_on_adoption_is_said(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    canon.write_text(MINUTES, encoding="utf-8")
    monkeypatch.setattr(live_sidecar, "merge", lambda *a, **k: False)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.reason) == (CO.UNCHANGED, "паспорт не записан")
    assert o.alarming


# --- гонки -------------------------------------------------------------------

def test_file_appearing_before_the_write_is_not_overwritten(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    real = live_sidecar.derivative_state

    def racing(path, *a, **k):
        state = real(path, *a, **k)
        path.write_text("чужой текст\n", encoding="utf-8")
        return state
    monkeypatch.setattr(live_sidecar, "derivative_state", racing)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state, o.reason) == (CO.REFUSED, live_sidecar.MISSING, "файл менялся под рукой")
    assert canon.read_text(encoding="utf-8") == "чужой текст\n"
    assert "canon_minutes_sha256" not in (live_sidecar.read(main) or {})


def test_canon_changed_after_the_state_is_not_overwritten(tmp_path, monkeypatch):
    src, canon, main = _meeting(tmp_path)
    ma.lay_canon(src, canon, main)
    src.write_text(MINUTES + "- [ ] ещё\n", encoding="utf-8")
    real = live_sidecar.derivative_state

    def racing(path, *a, **k):
        state = real(path, *a, **k)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("- [x] правка в окне\n")
        return state
    monkeypatch.setattr(live_sidecar, "derivative_state", racing)
    o = ma.lay_canon(src, canon, main)
    assert (o.action, o.state) == (CO.REFUSED, live_sidecar.STALE)
    assert "правка в окне" in canon.read_text(encoding="utf-8")


def test_source_is_read_once(tmp_path, monkeypatch):
    """Источник, подменённый после чтения: канон получил первую версию и
    паспорт на неё же — следующий проход видит STALE, не HUMAN."""
    src, canon, main = _meeting(tmp_path)
    second = MINUTES + "- [ ] подменено\n"
    real = os.fstat
    fired = []

    def swapping(fd):
        st = real(fd)
        if not fired:
            fired.append(1)
            tmp = src.with_name("подмена.tmp")
            tmp.write_text(second, encoding="utf-8")
            os.replace(tmp, src)
        return st
    monkeypatch.setattr(ma.os, "fstat", swapping)
    assert ma.lay_canon(src, canon, main).action == CO.CREATED
    monkeypatch.undo()
    assert canon.read_text(encoding="utf-8") == MINUTES
    assert _state(canon, main, second) == live_sidecar.STALE


# --- архивация целиком --------------------------------------------------------

def _world(tmp_path, minutes: str | bytes = MINUTES, *, docs: bool = False):
    graph = tmp_path / "граф"
    (graph / ma.ARCHIVE_DIR).mkdir(parents=True)
    (graph / "Встречи").mkdir()
    if docs:
        (graph / "Документация").mkdir()
    src, _canon, main = _meeting(tmp_path, minutes)
    return graph, src.parent, src, main


def _archive(graph, tdir):
    return ma.archive_meeting(graph, tdir, STAMP, "Тема", files_key=KEY)


def test_archive_reports_the_canon_and_keeps_the_human_edit(tmp_path):
    graph, tdir, src, main = _world(tmp_path)
    a = _archive(graph, tdir)
    canon = a.folder / "Минутки.md"
    assert (a.canon.action, a.canon.source) == (CO.CREATED, src)
    assert canon.read_bytes() == src.read_bytes()
    canon.write_text(MINUTES.replace("[ ]", "[x]"), encoding="utf-8")
    src.write_text(MINUTES + "- [ ] ревизия\n", encoding="utf-8")
    a = _archive(graph, tdir)
    assert a.canon.action == CO.KEPT and "[x]" in canon.read_text(encoding="utf-8")


def test_archive_without_minutes_has_no_canon(tmp_path):
    graph, tdir, src, main = _world(tmp_path)
    src.unlink()
    assert _archive(graph, tdir).canon is None


def test_archive_does_not_lay_the_canon_by_copy(tmp_path, monkeypatch):
    """Сторож поведения: канон не пишется копией байтов источника."""
    graph, tdir, src, main = _world(tmp_path)
    real = safe_write.copy_if_changed

    def no_canon_copy(s, d):
        if d.name == "Минутки.md":
            raise AssertionError("канон минуток разложен копией байтов")
        return real(s, d)
    monkeypatch.setattr(safe_write, "copy_if_changed", no_canon_copy)
    a = _archive(graph, tdir)
    assert a.canon.action == CO.CREATED and (a.folder / "Минутки.md").exists()


def _cp1251_canon(graph, tdir, main, with_passport):
    """Канон в cp1251 и прежнее саммари с паспортом."""
    folder = graph / ma.ARCHIVE_DIR / FOLDER
    if with_passport:
        _archive(graph, tdir)
        assert live_sidecar.valid_sha(live_sidecar.read(main)["canon_minutes_sha256"])
    folder.mkdir(exist_ok=True)
    (folder / "Минутки.md").write_bytes(MINUTES.replace("[ ]", "[x]").encode("cp1251"))
    summary = "---\ntype: саммари\n---\n# Саммари\n"
    (folder / "Саммари.md").write_text(summary, encoding="utf-8")
    live_sidecar.merge(main, {"summary_sha256": live_sidecar.sha(summary),
                              "summary_source_sha256": "a" * 64})
    return folder, summary


@pytest.mark.parametrize("with_passport", [True, False], ids=["с паспортом", "без паспорта"])
def test_canon_not_in_utf8_does_not_break_the_archive(tmp_path, capsys, with_passport):
    graph, tdir, src, main = _world(tmp_path)
    folder, summary = _cp1251_canon(graph, tdir, main, with_passport)
    raw = (folder / "Минутки.md").read_bytes()
    a = _archive(graph, tdir)
    assert (a.canon.action, a.canon.reason, a.canon.differing) == (CO.KEPT, "канон не в UTF-8", 0)
    assert "строк" not in a.canon.line()
    assert (a.summary.action, a.summary.reason) == (ma.SummaryOutcome.FAILED, "материал не читается: Минутки.md")
    assert (folder / "Минутки.md").read_bytes() == raw, "раскладка байты не конвертирует"
    assert (folder / "Саммари.md").read_text(encoding="utf-8") == summary
    meta = live_sidecar.read(main)
    assert (meta["summary_sha256"], meta["summary_source_sha256"]) == (live_sidecar.sha(summary), "a" * 64)
    assert (folder / "meeting.meta.json").exists(), "архивация дошла до конца"
    with pytest.raises(ma.MaterialUnreadable) as e:
        ma.decisions_of(folder)
    assert e.value.name == "Минутки.md"
    assert ma.summary_state(folder, main, None) == live_sidecar.UNKNOWN
    import protocol
    capsys.readouterr()
    text = protocol.build(folder)
    assert "Минутки.md не читается — пропущен" in capsys.readouterr().err
    assert text.startswith("# Протокол встречи — Тема")


def test_canon_os_error_leaves_the_summary_passport_alone(tmp_path, monkeypatch):
    graph, tdir, src, main = _world(tmp_path)
    a = _archive(graph, tdir)
    canon = a.folder / "Минутки.md"
    live_sidecar.merge(main, {"summary_source_sha256": "b" * 64})
    (a.folder / "meeting.meta.json").unlink()
    real = pathlib.Path.read_text

    def denied(self, *args, **kw):
        if self == canon:
            raise PermissionError(13, "нет доступа")
        return real(self, *args, **kw)
    monkeypatch.setattr(pathlib.Path, "read_text", denied)
    a = _archive(graph, tdir)
    assert (a.canon.action, a.canon.reason) == (CO.FAILED, "канон не прочитался")
    assert (a.summary.action, a.summary.reason) == (ma.SummaryOutcome.FAILED, "материал не читается: Минутки.md")
    assert live_sidecar.read(main)["summary_source_sha256"] == "b" * 64
    assert (a.folder / "meeting.meta.json").exists()


def test_protocol_reads_the_rest_when_minutes_are_unreadable(tmp_path, capsys):
    import protocol
    folder = tmp_path / FOLDER
    folder.mkdir()
    (folder / "Саммари.md").write_text("## Решили\n- берём план\n", encoding="utf-8")
    (folder / "Минутки.md").write_bytes("## Поручения\n- отчёт\n".encode("cp1251"))
    text = protocol.build(folder)
    assert "берём план" in text and "Минутки.md не читается — пропущен" in capsys.readouterr().err


def test_migrate_all_prints_the_canon_summary(tmp_path, capsys):
    graph, tdir, src, main = _world(tmp_path)
    main.write_text(SPEECH + "[10:01:00] Участник Б: продолжим\n" * 20, encoding="utf-8")
    assert ma.migrate_all(graph, tdir) == 1
    assert "архив: канон минуток — created 1" in capsys.readouterr().out
    ma.migrate_all(graph, tdir)
    assert "архив: канон минуток — unchanged 1" in capsys.readouterr().out


def test_migrate_all_skips_excluded_meetings_and_meetings_without_minutes(tmp_path, capsys):
    graph, tdir, src, main = _world(tmp_path)
    src.unlink()
    main.write_text(SPEECH + "[10:01:00] Участник Б: продолжим\n" * 20, encoding="utf-8")
    other = tdir / "2026-09-21_1100_Другая.md"
    other.write_text(SPEECH + "[11:01:00] Участник Б: продолжим\n" * 20, encoding="utf-8")
    (graph / ma.ARCHIVE_DIR / "_исключено.md").write_text("2026-09-21_1100 — тест звука\n", encoding="utf-8")
    assert ma.migrate_all(graph, tdir) == 2
    assert "архив: канон минуток — раскладок не было" in capsys.readouterr().out


def test_canon_outcome_is_a_value():
    o = CO(CO.KEPT, live_sidecar.HUMAN)
    with pytest.raises(AttributeError):
        o.action = CO.UPDATED  # type: ignore[misc]


def test_canon_tally_line_without_minutes():
    assert ma.canon_tally_line(collections.Counter()) == "канон минуток — раскладок не было"


# --- печать живого пути и «Документация» ------------------------------------

def _review(tdir):
    rev = tdir / f"{KEY}_ревизия_claude.md"
    rev.write_text("# Ревизия\n", encoding="utf-8")
    return rev


def _deliver(graph, tdir, main):
    import cloud_review
    buf = io.StringIO()
    cloud_review.deliver_review(_review(tdir), main, graph, STAMP, buf)
    return buf.getvalue()


def test_pipeline_and_delivery_say_why_the_summary_and_the_canon_were_left(tmp_path, capsys):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path)
    _cp1251_canon(graph, tdir, main, with_passport=True)
    graph_updater.archive_and_publish(main, graph, STAMP)
    out = capsys.readouterr().out
    assert "саммари — материал не читается: Минутки.md" in out
    assert "канон минуток kept: канон не в UTF-8" in out
    log = _deliver(graph, tdir, main)
    assert "саммари — материал не читается: Минутки.md" in log
    assert "канон минуток kept: канон не в UTF-8" in log


def test_quiet_pass_prints_no_alarms(tmp_path, capsys):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path)
    graph_updater.archive_and_publish(main, graph, STAMP)
    out = capsys.readouterr().out
    assert "канон минуток" not in out and "архив встречи: " + FOLDER in out


def _vdocs(graph):
    return graph / "Документация" / "Стенограммы встреч"


def test_docs_copy_is_the_canon_through_the_pipeline(tmp_path, capsys):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    graph_updater.archive_and_publish(main, graph, STAMP)
    canon = graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"
    copy = _vdocs(graph) / src.name
    assert copy.read_bytes() == canon.read_bytes()
    out = capsys.readouterr().out
    assert "не среди файлов встречи" not in out and "копия из источника" not in out
    canon.write_bytes(MINUTES.replace("[ ]", "[x]").encode("cp1251"))
    src.write_text(MINUTES + "- [ ] ревизия\n", encoding="utf-8")
    graph_updater.archive_and_publish(main, graph, STAMP)
    assert copy.read_bytes() == canon.read_bytes(), "копия расходится с каноном"


def test_delivery_writes_copy_reasons_to_the_review_log(tmp_path, monkeypatch, capsys):
    """Причины копии «Документации» на доставке ревизии — в её лог, а не в stdout
    воркера, где их никто не читает (Minor DS выходного круга по №366)."""
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    monkeypatch.setattr(graph_updater, "canon_of", lambda archived: (tmp_path / "нет.md", src))
    log = _deliver(graph, tdir, main)
    assert "[cloud-review] канона минуток" in log and "копия минуток из источника" in log
    assert "копия минуток из источника" not in capsys.readouterr().out


def test_docs_copy_is_the_canon_through_the_review_delivery(tmp_path):
    graph, tdir, src, main = _world(tmp_path, docs=True)
    log = _deliver(graph, tdir, main)
    assert ", vault" in log, "доставка создаёт «Стенограммы встреч», как конвейер"
    canon = graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"
    canon.write_text(MINUTES.replace("[ ]", "[x]"), encoding="utf-8")
    src.write_text(MINUTES + "- [ ] ревизия\n", encoding="utf-8")
    _deliver(graph, tdir, main)
    assert (_vdocs(graph) / src.name).read_bytes() == canon.read_bytes()
    assert (_vdocs(graph) / f"{KEY}_ревизия_claude.md").read_text(encoding="utf-8") == "# Ревизия\n"


def test_delivery_without_docs_still_delivers(tmp_path):
    graph, tdir, src, main = _world(tmp_path)
    log = _deliver(graph, tdir, main)
    assert f"ревизия доставлена: архив {FOLDER}\n" in log and "vault" not in log


def test_delivery_does_not_copy_a_review_not_in_utf8(tmp_path):
    import cloud_review
    graph, tdir, src, main = _world(tmp_path, docs=True)
    rev = tdir / f"{KEY}_ревизия_claude.md"
    rev.write_bytes("# Ревизия\n".encode("cp1251"))
    buf = io.StringIO()
    cloud_review.deliver_review(rev, main, graph, STAMP, buf)
    assert "ревизия доставлена" in buf.getvalue()
    assert not (_vdocs(graph) / rev.name).exists()
    assert (_vdocs(graph) / src.name).exists()


@pytest.mark.parametrize("run", ["pipeline", "delivery"])
def test_docs_minutes_are_written_from_the_canon(tmp_path, monkeypatch, run):
    """Сторож поведения: откуда пишется `<стем>_minutes.md` в «Документации»."""
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    sources = []
    real = safe_write.copy_if_changed

    def spy(s, d):
        if d.parent == _vdocs(graph) and d.name == src.name:
            sources.append(s)
        return real(s, d)
    monkeypatch.setattr(safe_write, "copy_if_changed", spy)
    if run == "pipeline":
        graph_updater.archive_and_publish(main, graph, STAMP)
    else:
        _deliver(graph, tdir, main)
    assert sources == [graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"]


def test_unreadable_canon_keeps_the_previous_docs_copy(tmp_path, monkeypatch, capsys):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    graph_updater.archive_and_publish(main, graph, STAMP)
    copy = _vdocs(graph) / src.name
    before = copy.read_bytes()
    canon = graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"
    canon.write_text(MINUTES.replace("[ ]", "[x]"), encoding="utf-8")
    real = safe_write.copy_if_changed

    def failing(s, d):
        if s == canon:
            raise OSError(5, "ввод-вывод")
        return real(s, d)
    monkeypatch.setattr(safe_write, "copy_if_changed", failing)
    graph_updater.archive_and_publish(main, graph, STAMP)
    assert copy.read_bytes() == before
    out = capsys.readouterr().out
    assert f"копия {src.name} не обновлена" in out and "артефакты скопированы в vault" in out


def test_failed_archive_copies_the_source(tmp_path, monkeypatch, capsys):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)

    def broken(*a, **k):
        raise RuntimeError("архив сломан")
    monkeypatch.setattr(ma, "archive_meeting", broken)
    graph_updater.archive_and_publish(main, graph, STAMP)
    assert (_vdocs(graph) / src.name).read_bytes() == src.read_bytes()
    out = capsys.readouterr().out
    assert "архив встречи не удался" in out and f"{src.name} — копия из источника" in out
    assert f"{main.name} — копия из источника" not in out, "причина — только у минуток"


def test_topic_twin_is_copied_from_itself(tmp_path):
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    twin = tdir / f"{KEY}_Alpha_minutes.md"
    twin.write_text("минутки двойника\n", encoding="utf-8")
    graph_updater.archive_and_publish(main, graph, STAMP)
    canon = graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"
    canon.write_text(MINUTES.replace("[ ]", "[x]"), encoding="utf-8")
    graph_updater.archive_and_publish(main, graph, STAMP)
    assert (_vdocs(graph) / twin.name).read_bytes() == twin.read_bytes()
    assert (_vdocs(graph) / src.name).read_bytes() == canon.read_bytes()


def test_twin_as_the_canon_source_is_copied_from_the_canon(tmp_path, capsys):
    """Точного имени нет — источник канона двойник по теме; копия всё равно
    равна канону. Канон без файла на диске — копия из источника, причина в лог."""
    import graph_updater
    graph, tdir, src, main = _world(tmp_path, docs=True)
    twin = src.with_name(f"{KEY}_Alpha_minutes.md")
    src.rename(twin)
    canon = tmp_path / "канон.md"
    canon.write_text("канон\n", encoding="utf-8")
    vdocs = graph_updater.copy_to_vault_docs(main, graph, canon, twin)
    assert (vdocs / twin.name).read_text(encoding="utf-8") == "канон\n"
    graph_updater.copy_to_vault_docs(main, graph, tmp_path / "нет.md", twin)
    assert (vdocs / twin.name).read_bytes() == twin.read_bytes()
    assert "нет — копия минуток из источника" in capsys.readouterr().out
    graph_updater.copy_to_vault_docs(main, graph, canon, tdir / "чужой_minutes.md")
    assert "не среди файлов встречи" in capsys.readouterr().out


# --- переименование встречи --------------------------------------------------

@pytest.fixture
def renamed(tmp_path):
    """Встреча в архиве с каноном, который ссылается на свою папку по имени."""
    import rename_meeting as rm
    graph, tdir, src, main = _world(tmp_path, MINUTES + f"\nПодробнее: [[{ma.ARCHIVE_DIR}/{FOLDER}/Минутки]]\n")
    _archive(graph, tdir)
    (graph / ma.ARCHIVE_DIR / FOLDER / "Граф-копия.md").write_text(f"ссылка {FOLDER}\n", encoding="utf-8")
    charoite_paths.use_data_root(tmp_path, replace=True)
    pretty, slug = rm.pretty_and_slug("Новая тема")

    def run():
        rm.apply(rm.plan(graph, tdir, STAMP, pretty, slug), graph, STAMP, pretty)
        return graph / ma.ARCHIVE_DIR / "2026-09-20 10-00 — Новая тема"
    return graph, tdir, src, main, run


def test_rename_keeps_the_canon_passport_fresh(renamed):
    graph, tdir, src, main, run = renamed
    folder = run()
    canon = folder / "Минутки.md"
    assert "Новая тема" in canon.read_text(encoding="utf-8")
    new_main = tdir / "2026-09-20_1000_Новая_тема.md"
    new_src = tdir / "2026-09-20_1000_Новая_тема_minutes.md"
    assert _state(canon, new_main, new_src.read_text(encoding="utf-8")) == live_sidecar.FRESH


def test_rename_rewrites_the_folder_name_in_an_edited_canon_and_keeps_it_human(renamed):
    """Правленый канон (HUMAN) переименование переписывает механически: имя папки в
    ссылке меняется, отметка человека цела, паспорт не переставляется — канон
    остаётся правленым. Правило одно для всех видов карты, как у саммари
    (облачная голова выходного круга по №366)."""
    graph, tdir, src, main, run = renamed
    old = graph / ma.ARCHIVE_DIR / FOLDER / "Минутки.md"
    old.write_text(old.read_text(encoding="utf-8").replace("[ ] Участник А", "[x] Участник А"), encoding="utf-8")
    folder = run()
    canon = folder / "Минутки.md"
    text = canon.read_text(encoding="utf-8")
    assert "[x] Участник А" in text and folder.name in text and FOLDER not in text
    new_main = tdir / "2026-09-20_1000_Новая_тема.md"
    new_src = tdir / "2026-09-20_1000_Новая_тема_minutes.md"
    assert _state(canon, new_main, new_src.read_text(encoding="utf-8")) == live_sidecar.HUMAN


@pytest.mark.parametrize("transcript", [True, False], ids=["со стенограммой", "без стенограммы"])
def test_rename_survives_a_canon_not_in_utf8(renamed, capsys, transcript):
    graph, tdir, src, main, run = renamed
    if not transcript:
        main.unlink()
        _sidecar(main).unlink()
    old = graph / ma.ARCHIVE_DIR / FOLDER
    raw = f"ссылка {FOLDER}\n".encode("cp1251")
    (old / "Минутки.md").write_bytes(raw)
    folder = run()
    assert "Минутки.md: имя папки не заменено" in capsys.readouterr().out
    assert (folder / "Минутки.md").read_bytes() == raw
    assert (folder / "Граф-копия.md").read_text(encoding="utf-8") == "ссылка 2026-09-20 10-00 — Новая тема\n"


def test_rename_without_transcript_keeps_an_edit_made_during_the_rewrite(renamed, monkeypatch):
    graph, tdir, src, main, run = renamed
    main.unlink()
    _sidecar(main).unlink()
    real = pathlib.Path.read_text
    fired = []

    def reading(self, *a, **k):
        text = real(self, *a, **k)
        if self.name == "Минутки.md" and self.parent.name.endswith("Новая тема") and not fired:
            fired.append(1)
            with open(self, "a", encoding="utf-8") as fh:
                fh.write("- [x] Участник Б: правка во время переименования\n")
        return text
    monkeypatch.setattr(pathlib.Path, "read_text", reading)
    folder = run()
    text = (folder / "Минутки.md").read_text(encoding="utf-8")
    assert fired and "правка во время переименования" in text and "Новая тема" in text


@pytest.mark.parametrize("transcript", [True, False], ids=["со стенограммой", "без стенограммы"])
def test_rename_retouches_passport_files_only_with_a_transcript(renamed, monkeypatch, transcript):
    """Поведенческий сторож вместо разбора исходника `rename_meeting`: файлы с
    видом в `ARCHIVE_KINDS` идут через `retouch`, и только если есть
    владелец паспорта — стенограмма."""
    graph, tdir, src, main, run = renamed
    folder = graph / ma.ARCHIVE_DIR / FOLDER
    (folder / "Саммари.md").write_text(f"# Саммари — {FOLDER}\n", encoding="utf-8")
    if not transcript:
        main.unlink()
        _sidecar(main).unlink()
    calls = []
    real = live_sidecar.retouch
    monkeypatch.setattr(live_sidecar, "retouch",
                        lambda live, kind, f, t: calls.append((kind, f.name)) or real(live, kind, f, t))
    new = run()
    if transcript:
        assert sorted(calls) == [("canon_minutes", "Минутки.md"), ("summary", "Саммари.md")]
    else:
        assert calls == []
    assert "Новая тема" in (new / "Саммари.md").read_text(encoding="utf-8")
    assert "Новая тема" in (new / "Минутки.md").read_text(encoding="utf-8")


# --- массовые проходы: строка канона ----------------------------------------

@pytest.mark.parametrize("explicit", [False, True], ids=["обход", "явный путь"])
def test_retro_fill_prints_the_canon_line(tmp_path, monkeypatch, capsys, explicit):
    """Только печать сводки в `main` — и в обходе, и с явным путём. Счётчик
    `process` здесь подменён; его проводку с настоящим `process` держит
    `tests/test_derivative_passport.py` (Minor DS выходного круга по №366)."""
    import retro_fill
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / f"{KEY}.md"
    live.write_text(SPEECH + "[10:01:00] Участник Б: продолжим\n" * 30, encoding="utf-8")
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(retro_fill, "load_user_or_example", lambda root: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(retro_fill.graphs, "graph_dir", lambda cfg: tmp_path / "graph")
    monkeypatch.setattr(retro_fill, "harden_umask", lambda: None)

    def fake(f, cfg, graph, tdir_, summary=None, tally=None, canon_tally=None, *, unhide=True):
        canon_tally[CO.KEPT] += 1
        return []
    monkeypatch.setattr(retro_fill, "process", fake)
    retro_fill.main([str(live)] if explicit else [])
    assert "ретро: канон минуток — kept 1" in capsys.readouterr().out


# --- safe_write: времена -----------------------------------------------------

def test_write_text_sets_times_only_when_asked(tmp_path):
    p = tmp_path / "x.md"
    assert safe_write.write_text(p, "a\n", times=(T0, T0))
    assert p.stat().st_mtime_ns == T0
    assert safe_write.write_text(p, "b\n")
    assert p.stat().st_mtime_ns != T0
