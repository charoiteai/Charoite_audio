"""Паспорт производной встречи (№309): у минуток, разбора и тезисов есть
владелец и источник, и свежесть решается по ним — не «если файла нет», не
по mtime и не «пишу всегда».

Входной круг DS и GLM (18.09): у производной не было паспорта — он был только у
минуток и только в сайдкаре демона; шесть писателей выводили свежесть каждый
своим способом. Замер: минутки старше стенограммы по mtime у 204 из 302 встреч —
mtime двигают ретитл, ко-мышление, ревизия, критерий ложный. Хеш речи считался
с H1, и ретитл делал источник «другим» (DS C1); `finalize_minutes` источник на
главном пути не сверял (GLM C2); ретро писал минутки третьим конвейером (GLM C1).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import live_sidecar  # noqa: E402
import llm as llm_mod  # noqa: E402
import meeting_archive  # noqa: E402
import meeting_stamp  # noqa: E402
import rebuild_transcript  # noqa: E402
import retro_fill  # noqa: E402
import transcript  # noqa: E402

SPEECH = "**Инга** [10:21]:\nСмету пришлю к пятому, провайдер прежний.\n" * 30


@pytest.fixture(autouse=True)
def _no_live_model(monkeypatch):
    """`archive_meeting` собирает саммари моделью — тесты архива ходили в живой
    `requests.post` на локальный сервер (замер 19.09: 8 обращений из 5 тестов и на
    main, и после №314). Тест не платит модели и не зависит от того, поднята ли
    она: подделка отвечает готовым саммари."""
    import requests

    class _Resp:
        status_code = 200
        text = ""
        headers: dict = {}
        def json(self): return {"message": {"content": "**Суть** встреча.\n\n## Решили\n- **Пункт** — принят\n"}}
        def raise_for_status(self): pass

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())


def test_speech_of_ignores_the_title_and_the_notes_tail():
    """Речь источника — без H1 и без «Ко-мышления»: ретитл переписывает первую
    строку, и хеш с заголовком делал минутки собранными «по другой речи» на
    первом же ретитле (Critical DS)."""
    bare = "# Встреча 2026-09-02_1021\n" + SPEECH
    titled = "# Встреча 2026-09-02_1021 — Смета\n" + SPEECH
    with_notes = titled + transcript.NOTES_HEAD + "\n> 10:22 📌 мысль модели\n"
    speech = SPEECH.rstrip("\n")
    assert transcript.speech_of(bare) == transcript.speech_of(titled) == transcript.speech_of(with_notes) == speech
    assert transcript.speech_of(SPEECH) == speech, "без заголовка — весь текст"
    assert "_speech(" not in (REPO / "src" / "rebuild_transcript.py").read_text(encoding="utf-8"), \
        "у пересборки нет своего правила речи — только transcript.speech_of через MeetingSource"
    # хвостовые переводы строк — не речь: первая же заметка ко-мышления или след
    # канала (№234) иначе сдвигали бы хеш источника на один «\n»
    assert transcript.speech_of(titled + "\n\n") == transcript.speech_of(titled + transcript.NOTES_HEAD + "\n> 10:22 📌 x\n")


def test_derivative_state_table(tmp_path):
    """MISSING → собрать; FRESH → модель не звать; STALE → пересобрать; HUMAN —
    байты не машинные; UNKNOWN — паспорта нет: не знание о человеке, а его
    отсутствие, старый корпус не запирается как «человеческий»."""
    p = tmp_path / "x_minutes.md"
    src = live_sidecar.sha("речь")
    assert live_sidecar.derivative_state(p, {}, "minutes", src) == live_sidecar.MISSING
    p.write_text("машинный текст", encoding="utf-8")
    assert live_sidecar.derivative_state(p, {}, "minutes", src) == live_sidecar.UNKNOWN
    assert live_sidecar.derivative_state(p, None, "minutes", src) == live_sidecar.UNKNOWN
    meta = {"minutes_sha256": live_sidecar.sha("машинный текст"), "minutes_source_sha256": src}
    assert live_sidecar.derivative_state(p, meta, "minutes", src) == live_sidecar.FRESH
    assert live_sidecar.derivative_state(p, meta, "minutes", live_sidecar.sha("другая речь")) == live_sidecar.STALE
    p.write_text("машинный текст, правлен", encoding="utf-8")
    assert live_sidecar.derivative_state(p, meta, "minutes", src) == live_sidecar.HUMAN
    assert live_sidecar.derivative_state(p, {"minutes_sha256": "мусор"}, "minutes", src) == live_sidecar.UNKNOWN


def test_attest_writes_both_keys_and_creates_the_sidecar(tmp_path):
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n" + SPEECH, encoding="utf-8")
    assert live_sidecar.attest(live, "debrief", "текст разбора", live_sidecar.sha(SPEECH.rstrip("\n")))
    meta = live_sidecar.read(live)
    assert meta["debrief_sha256"] == live_sidecar.sha("текст разбора") and meta["debrief_source_sha256"] == live_sidecar.sha(SPEECH.rstrip("\n"))
    dpath = tmp_path / "x_разбор.md"
    dpath.write_text("текст разбора", encoding="utf-8")
    assert live_sidecar.derivative_state(dpath, meta, "debrief", live_sidecar.sha(SPEECH.rstrip("\n"))) == live_sidecar.FRESH


def test_debrief_path_follows_the_graph_key_like_graph_updater(tmp_path):
    """У посекундной стенограммы без темы retro_fill строил имя разбора от стема,
    graph_updater — от минутного ключа: два разбора на встречу (Critical DS)."""
    t = tmp_path / "2026-09-02_102112.md"
    t.write_text("x", encoding="utf-8")
    assert meeting_stamp.derivative_path(t, "debrief").name == "2026-09-02_1021_разбор.md"
    assert meeting_stamp.derivative_path(t, "minutes").name == "2026-09-02_102112_minutes.md", "минутки — по стему (демон)"
    titled = tmp_path / "2026-09-02_1021_Смета.md"
    titled.write_text("x", encoding="utf-8")
    assert meeting_stamp.derivative_path(titled, "debrief").name == "2026-09-02_1021_Смета_разбор.md"
    with pytest.raises(ValueError):
        meeting_stamp.derivative_path(t, "summary")


class _FakeLLM:
    calls: list[str] = []
    lang = "ru"

    def __init__(self, cfg):
        pass

    # настоящий блок об оговорке (тесты №317 ищут его в промпте); берётся до
    # подмены llm.LLM подделкой — иначе рекурсия
    recording_block = llm_mod.LLM.recording_block

    def minutes(self, text, recording_note=None):
        _FakeLLM.calls.append(("minutes", text))
        yield "# Минутки\n**Участники:** Инга\n## Поручения\n- [ ] **Инга** — смета — до 05.09\n"

    def complete(self, prompt, **kw):
        _FakeLLM.calls.append(("complete", prompt))
        return "## Раздел\n- пункт\n"


def _cfg(tmp_path):
    return {"llm": {"base_url": "http://127.0.0.1:11434", "model": "m"},
            "log": {"transcripts_dir": "transcripts"}, "sufler": {"user_name": "Владелец"}}


def _meeting(tmp_path, monkeypatch, *, text=None):
    import llm
    _FakeLLM.calls = []
    monkeypatch.setattr(llm, "LLM", _FakeLLM)
    monkeypatch.setattr(retro_fill, "LLM", _FakeLLM)          # имя привязано при импорте модуля
    monkeypatch.setattr(rebuild_transcript, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rebuild_transcript, "canonize_file", lambda *a, **k: None)
    monkeypatch.setattr(retro_fill, "archive_meeting", lambda *a, **k: None)   # архив — не предмет теста
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-02_1021.md"
    live.write_text(text if text is not None else "# Встреча 2026-09-02_1021\n" + SPEECH, encoding="utf-8")
    return live, tdir


def test_retro_fill_writes_missing_derivatives_with_passports(tmp_path, monkeypatch):
    live, tdir = _meeting(tmp_path, monkeypatch)
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert made == ["минутки", "разбор"]
    meta = live_sidecar.read(live)
    src = live_sidecar.sha(SPEECH.rstrip("\n"))
    for kind in ("minutes", "debrief"):
        assert meta[f"{kind}_source_sha256"] == src, kind
    mpath = live.with_name("2026-09-02_1021_minutes.md")
    assert live_sidecar.derivative_state(mpath, meta, "minutes", src) == live_sidecar.FRESH
    assert live_sidecar.derivative_state(live.with_name("2026-09-02_1021_разбор.md"), meta, "debrief", src) == live_sidecar.FRESH
    assert [k for k, _ in _FakeLLM.calls] == ["minutes", "complete"], "минутки — конвейером пересборки, разбор — промптом"
    assert all(transcript.NOTES_HEAD not in t for _, t in _FakeLLM.calls)


def test_fresh_derivatives_do_not_call_the_model_even_after_retitle(tmp_path, monkeypatch):
    """Повторный прогон без правок — ноль вызовов модели; ретитл (новый H1) —
    тоже ноль: заголовок не речь."""
    live, tdir = _meeting(tmp_path, monkeypatch)
    retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    _FakeLLM.calls = []
    assert retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir) == []
    assert _FakeLLM.calls == []
    live.write_text("# Встреча 2026-09-02_1021 — Смета\n" + SPEECH, encoding="utf-8")   # ретитл: другой H1, та же речь
    assert retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir) == [] and _FakeLLM.calls == []


def test_stale_machine_derivative_is_rebuilt_and_the_old_one_kept(tmp_path, monkeypatch):
    live, tdir = _meeting(tmp_path, monkeypatch)
    retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    dpath = live.with_name("2026-09-02_1021_разбор.md")
    old = dpath.read_text(encoding="utf-8")
    live.write_text("# Встреча\n" + SPEECH + "**Марк** [10:40]:\nДобавили пункт про сроки.\n", encoding="utf-8")
    _FakeLLM.calls = []
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert "разбор" in made and "минутки" in made
    assert (tdir / ".prev" / dpath.name).read_text(encoding="utf-8") == old, "прежняя версия сохранена"
    meta = live_sidecar.read(live)
    assert meta["debrief_source_sha256"] == live_sidecar.sha(transcript.speech_of(live.read_text(encoding="utf-8")))


def test_human_and_unknown_derivatives_are_left_alone(tmp_path, monkeypatch):
    """Правленная человеком — не трогаем никогда; без паспорта (старый корпус,
    чужой писатель) — не трогаем и модель не зовём: пересобрать её значило бы
    затереть единственную ручную правку, о которой мы не знаем."""
    live, tdir = _meeting(tmp_path, monkeypatch)
    retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    dpath = live.with_name("2026-09-02_1021_разбор.md")
    dpath.write_text(dpath.read_text(encoding="utf-8") + "\nМоя пометка руками.\n", encoding="utf-8")
    live.write_text("# Встреча\n" + SPEECH + "ещё речь\n", encoding="utf-8")
    _FakeLLM.calls = []
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert "разбор" not in made and "Моя пометка руками." in dpath.read_text(encoding="utf-8")
    assert all(k == "minutes" for k, _ in _FakeLLM.calls), "разбор человека модель не пересобирает"
    # без паспорта
    live2 = tdir / "2026-09-03_1100.md"
    live2.write_text("# Встреча\n" + SPEECH, encoding="utf-8")
    live2.with_name("2026-09-03_1100_minutes.md").write_text("старые минутки без паспорта", encoding="utf-8")
    live2.with_name("2026-09-03_1100_разбор.md").write_text("старый разбор без паспорта", encoding="utf-8")
    _FakeLLM.calls = []
    assert retro_fill.process(live2, _cfg(tmp_path), tmp_path / "graph", tdir) == []
    assert _FakeLLM.calls == [] and "старые минутки" in live2.with_name("2026-09-03_1100_minutes.md").read_text(encoding="utf-8")


def test_finalize_minutes_skips_the_model_when_the_source_is_unchanged(tmp_path, monkeypatch):
    """Главный путь пересборки: хеш источника раньше только писался — повторная
    пересборка без правок снова звала модель (Critical GLM)."""
    import llm
    _FakeLLM.calls = []
    monkeypatch.setattr(llm, "LLM", _FakeLLM)
    monkeypatch.setattr(rebuild_transcript, "_yield_to_live", lambda *a, **k: None)
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n" + SPEECH, encoding="utf-8")
    mpath = live.with_name("2026-09-02_1021_minutes.md")
    mpath.write_text("# Минутки машинные\n", encoding="utf-8")
    meta = {"minutes_sha256": live_sidecar.sha("# Минутки машинные\n"),
            "minutes_source_sha256": live_sidecar.sha(SPEECH.rstrip("\n"))}
    assert rebuild_transcript.finalize_minutes(live, "# Встреча\n" + SPEECH, meta, _cfg(tmp_path), {}) == "fresh"
    assert _FakeLLM.calls == []
    assert rebuild_transcript.finalize_minutes(live, "# Встреча\n" + SPEECH + "новое\n", meta, _cfg(tmp_path), {}) == "regenerated"
    assert len(_FakeLLM.calls) == 1


def test_archive_extras_do_not_overwrite_existing_theses_and_qa(tmp_path):
    """`_derive_extras` переписывал Тезисы.md цитатами ко-мышления при каждом
    архивировании — LLM-тезисы с паспортом затирались (Critical DS)."""
    folder = tmp_path / "meeting"
    folder.mkdir()
    (folder / "Стенограмма.md").write_text("> 10:21 📌 первый тезис\n", encoding="utf-8")
    (folder / "Тезисы.md").write_text("# Тезисы встречи\nLLM-тезисы с паспортом\n", encoding="utf-8")
    (folder / "Разбор.md").write_text("## Вопросы встречи и ответы\n- вопрос → ответ\n", encoding="utf-8")
    (folder / "Вопросы и ответы.md").write_text("готовые\n", encoding="utf-8")
    meeting_archive._derive_extras(folder)
    assert "LLM-тезисы с паспортом" in (folder / "Тезисы.md").read_text(encoding="utf-8")
    assert (folder / "Вопросы и ответы.md").read_text(encoding="utf-8") == "готовые\n"
    (folder / "Тезисы.md").unlink()
    meeting_archive._derive_extras(folder)
    assert "первый тезис" in (folder / "Тезисы.md").read_text(encoding="utf-8"), "нет файла — собрать из цитат"


def test_import_tail_runs_retro_fill_for_its_own_transcript_only():
    src = (REPO / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    assert 'str(CODE / "src" / "retro_fill.py"), str(tpath)]' in src
    gu = (REPO / "src" / "graph_updater.py").read_text(encoding="utf-8")
    i = gu.index("derivative_state(dpath")
    assert i < gu.index("debrief = llm_client.complete("), "владение разбора — до вызова модели"
    assert "expect=d_before" in gu and 'live_sidecar.attest(tpath, "debrief"' in gu


def test_the_debrief_name_has_one_formula_across_writers():
    """graph_updater строил имя разбора от темы, названной моделью В ЭТОМ прогоне,
    retro_fill и архив — от стема файла. У уже названной стенограммы повторный
    разбор с другой темой заводил второй файл (69 встреч из ~300 на боевом
    каталоге 18.09), паспорт писался на файл, которого retro_fill не искал, а
    в архив уезжал тот, что позже по алфавиту. Правило одно — `derivative_path`."""
    gu = (REPO / "src" / "graph_updater.py").read_text(encoding="utf-8")
    assert 'dpath = meeting_stamp.derivative_path(tpath, "debrief", graph)' in gu
    assert '_разбор.md"' not in gu, "второй формулы имени разбора в graph_updater быть не должно"
    for name in ("retro_fill.py", "meeting_archive.py", "rebuild_transcript.py"):
        src = (REPO / "src" / name).read_text(encoding="utf-8")
        assert 'f"{' not in src or '_разбор.md"' not in src.replace('("_разбор.md", "Разбор.md")', ""), name
    # уже названная стенограмма: тема в имени решает, а не новая тема модели
    titled = REPO / "transcripts" / "2026-09-02_1021_Смета.md"   # путь не читается — только имя
    assert meeting_stamp.derivative_path(titled, "debrief").name == "2026-09-02_1021_Смета_разбор.md"


def test_io_errors_are_ignorance_not_a_human_hand(tmp_path):
    """OSError при чтении давал HUMAN — транзиентный EACCES от редактора или
    бэкапа навсегда останавливал живой путь «разбор правлен руками» (Important
    GLM выходного круга). Байты есть, но не UTF-8 — вот это не наш текст."""
    body = "# Разбор\n- пункт\n"
    meta = {"debrief_sha256": live_sidecar.sha(body), "debrief_source_sha256": "a" * 64}
    p = tmp_path / "2026-09-02_1021_разбор.md"
    p.write_text(body, encoding="utf-8")
    assert live_sidecar.derivative_state(p, meta, "debrief", "a" * 64) == live_sidecar.FRESH
    p.chmod(0)
    try:
        if p.stat().st_uid != 0:      # root читает всё — гейт прав бессмысленен
            assert live_sidecar.derivative_state(p, meta, "debrief", "a" * 64) == live_sidecar.UNKNOWN
    finally:
        p.chmod(0o600)
    p.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert live_sidecar.derivative_state(p, meta, "debrief", "a" * 64) == live_sidecar.HUMAN


def test_build_policies_are_named_in_one_place_and_used_by_both_writers():
    """Живой путь освежает разбор и при FRESH, и при UNKNOWN (источник — речь +
    граф, старый корпус без паспорта обслуживается как прежде); ретро-обход
    строит только заведомо своё и устаревшее. Раньше разница жила в двух `if`
    по двум модулям (критика GLM выходного круга по №309)."""
    live, retro = live_sidecar.POLICY_LIVE, live_sidecar.POLICY_RETRO
    assert live_sidecar.wants_build(live_sidecar.HUMAN, live) is False
    assert live_sidecar.wants_build(live_sidecar.HUMAN, retro) is False
    assert live_sidecar.wants_build(live_sidecar.UNKNOWN, live) and not live_sidecar.wants_build(live_sidecar.UNKNOWN, retro)
    assert live_sidecar.wants_build(live_sidecar.FRESH, live) and not live_sidecar.wants_build(live_sidecar.FRESH, retro)
    for st in (live_sidecar.MISSING, live_sidecar.STALE):
        assert live_sidecar.wants_build(st, live) and live_sidecar.wants_build(st, retro)
    gu = (REPO / "src" / "graph_updater.py").read_text(encoding="utf-8")
    rf = (REPO / "src" / "retro_fill.py").read_text(encoding="utf-8")
    assert "live_sidecar.wants_build(d_state, live_sidecar.POLICY_LIVE)" in gu
    assert rf.count("live_sidecar.wants_build(state, live_sidecar.POLICY_RETRO)") == 3, "минутки, разбор, тезисы"
    assert "== live_sidecar.HUMAN" not in gu and "in (live_sidecar.MISSING, live_sidecar.STALE)" not in rf


def test_live_cothinking_theses_are_kept_and_the_model_is_not_paid_for_them(tmp_path, monkeypatch, capsys):
    """У встречи с живым ко-мышлением файл тезисов собирает архив из строк
    «> HH:MM 📌 …»; retro_fill за тезисы модель не зовёт — правило названо, а не
    выходит случайно из порядка «архив раньше проверки» (Critical DS выходного
    круга). Без живого ко-мышления — ретро-тезисы модели с паспортом."""
    live, tdir = _meeting(tmp_path, monkeypatch,
                          text="# Встреча 2026-09-02_1021\n" + SPEECH + "> 10:22 📌 смета к пятому\n")
    folder = tmp_path / "graph" / "Встречи-архив" / "2026-09-02_1021"
    folder.mkdir(parents=True)

    def fake_archive(graph, tdir_, stamp, slug, files_key=None, mode=None):
        (folder / "Стенограмма.md").write_text(live.read_text(encoding="utf-8"), encoding="utf-8")
        meeting_archive._derive_extras(folder)
        return meeting_archive.Archived(folder, meeting_archive.SummaryOutcome(meeting_archive.SummaryOutcome.NONE, None))
    monkeypatch.setattr(retro_fill, "archive_meeting", fake_archive)
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert "тезисы" not in made
    assert "смета к пятому" in (folder / "Тезисы.md").read_text(encoding="utf-8"), "живые тезисы собрал архив"
    assert all(retro_fill.THESES_PROMPT not in t for _, t in _FakeLLM.calls), "модель за тезисы не платила"
    assert "тезисы живые" in capsys.readouterr().out
    # без ко-мышления — ретро-тезисы с паспортом, прежняя версия — у стенограммы, не в графе
    live2 = tdir / "2026-09-03_1100.md"
    live2.write_text("# Встреча 2026-09-03_1100\n" + SPEECH, encoding="utf-8")
    folder2 = tmp_path / "graph" / "Встречи-архив" / "2026-09-03_1100"
    folder2.mkdir()
    monkeypatch.setattr(retro_fill, "archive_meeting", lambda *a, **k: meeting_archive.Archived(
        folder2, meeting_archive.SummaryOutcome(meeting_archive.SummaryOutcome.NONE, None)))
    _FakeLLM.calls = []
    assert "тезисы" in retro_fill.process(live2, _cfg(tmp_path), tmp_path / "graph", tdir)
    meta = live_sidecar.read(live2)
    assert live_sidecar.derivative_state(folder2 / "Тезисы.md", meta, "theses", live_sidecar.sha(SPEECH.rstrip("\n"))) == live_sidecar.FRESH
    live2.write_text("# Встреча 2026-09-03_1100\n" + SPEECH + "ещё речь\n", encoding="utf-8")
    old = (folder2 / "Тезисы.md").read_text(encoding="utf-8")
    assert "тезисы" in retro_fill.process(live2, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert (tdir / ".prev" / "2026-09-03_1100__Тезисы.md").read_text(encoding="utf-8") == old
    assert not (folder2 / ".prev").exists(), "скрытый каталог в графе синкался бы iCloud (Important DS)"


def test_the_report_line_names_what_was_skipped_instead_of_saying_full(tmp_path, monkeypatch, capsys):
    """«полная» печаталась и для HUMAN, и для UNKNOWN — «всё на месте» было
    неотличимо от «всё заперто» (Important DS выходного круга)."""
    live, tdir = _meeting(tmp_path, monkeypatch)
    live.with_name("2026-09-02_1021_minutes.md").write_text("старые минутки без паспорта", encoding="utf-8")
    live.with_name("2026-09-02_1021_разбор.md").write_text("старый разбор без паспорта", encoding="utf-8")
    assert retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir) == []
    out = capsys.readouterr().out
    assert "полная" not in out and "минутки unknown" in out and "разбор unknown" in out
    assert retro_fill.process(tdir / "без_штампа.md", _cfg(tmp_path), tmp_path / "graph", tdir) == []


def test_main_addresses_the_meeting_by_its_final_name_not_the_path_it_was_given(tmp_path, monkeypatch):
    """Хвост импорта отдаёт путь ДО ретитла: graph_updater в своём процессе уже
    переименовал файл под тему — хвост падал на `stat()` со стеком, импорт
    объявлялся проваленным (Critical GLM выходного круга). Путь вне каталога
    стенограмм — не встреча: архив завёл бы пустую папку в графе (Minor GLM)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    final = tdir / "2026-09-02_1021_Смета.md"
    final.write_text("# Встреча 2026-09-02_1021 — Смета\n" + SPEECH, encoding="utf-8")
    seen: list[pathlib.Path] = []
    monkeypatch.setattr(retro_fill, "ROOT", tmp_path)
    monkeypatch.setattr(retro_fill, "load_user_or_example", lambda root: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(retro_fill.graphs, "graph_dir", lambda cfg: tmp_path / "graph")
    monkeypatch.setattr(retro_fill, "harden_umask", lambda: None)
    monkeypatch.setattr(retro_fill, "process", lambda f, cfg, graph, tdir_, summary=None, tally=None: seen.append(f) or [])
    retro_fill.main([str(tdir / "2026-09-02_1021.md")])          # путь до ретитла
    assert seen == [final.resolve()]
    stray = tmp_path / "2026-09-05_1200.md"
    stray.write_text("# Встреча\n" + SPEECH, encoding="utf-8")
    seen.clear()
    with pytest.raises(SystemExit) as e:
        retro_fill.main([str(stray)])                             # вне каталога стенограмм — отказ, не «ready»
    assert seen == [] and "вне каталога" in str(e.value)
    with pytest.raises(SystemExit) as e:
        retro_fill.main([str(tdir / "2026-09-09_0900.md")])       # такой встречи нет
    assert "стенограммы нет" in str(e.value)
    # резолвер нашёл файл другой минуты (чужая встреча) — отказ со строкой, не тихая запись не по адресу
    monkeypatch.setattr(retro_fill, "find_final_transcript", lambda p: final.resolve())
    seen.clear()
    with pytest.raises(SystemExit) as e:
        retro_fill.main([str(tdir / "2026-09-02_1100.md")])
    assert seen == [] and "другой минуты" in str(e.value)


def test_a_read_error_is_unknown_even_where_chmod_cannot_prove_it(tmp_path, monkeypatch):
    """Гейт правами файловой системы под root молчит (Minor DS круга 2) — тот же
    отказ подставляется в шов чтения."""
    body = "# Разбор\n"
    p = tmp_path / "2026-09-02_1021_разбор.md"
    p.write_text(body, encoding="utf-8")
    meta = {"debrief_sha256": live_sidecar.sha(body), "debrief_source_sha256": "a" * 64}
    real = pathlib.Path.read_text

    def denied(self, *a, **k):
        if self == p:
            raise PermissionError(13, "нет доступа")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", denied)
    assert live_sidecar.derivative_state(p, meta, "debrief", "a" * 64) == live_sidecar.UNKNOWN


def test_a_silent_model_is_reported_not_hidden_behind_full(tmp_path, monkeypatch, capsys):
    """`gen` при отказе модели возвращал «» — ни в «собрано», ни в «пропущено»,
    строка говорила «полная» без разбора (Important GLM круга 2)."""
    live, tdir = _meeting(tmp_path, monkeypatch)
    monkeypatch.setattr(retro_fill, "gen", lambda *a, **k: "")
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert "разбор" not in made
    out = capsys.readouterr().out
    assert "разбор — модель не ответила" in out and "полная" not in out


def test_live_theses_are_judged_by_the_file_the_archive_made(tmp_path, monkeypatch):
    """Признак «живые» считался по стенограмме из tdir, а файл архив собирает
    из копии — если копию перебила легаси-производная без строк ко-мышления,
    файла нет, а отчёт говорил «тезисы живые» (Important DS круга 2)."""
    live, tdir = _meeting(tmp_path, monkeypatch,
                          text="# Встреча 2026-09-02_1021\n" + SPEECH + "> 10:22 📌 смета к пятому\n")
    folder = tmp_path / "graph" / "Встречи-архив" / "2026-09-02_1021"
    folder.mkdir(parents=True)
    none = meeting_archive.SummaryOutcome(meeting_archive.SummaryOutcome.NONE, None)
    monkeypatch.setattr(retro_fill, "archive_meeting",
                        lambda *a, **k: meeting_archive.Archived(folder, none))   # файла тезисов архив НЕ создал
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert "тезисы" in made and (folder / "Тезисы.md").exists(), "нет файла — к модели, как у встречи без живого контура"


def test_cothinking_lines_are_the_loop_s_own_not_any_quote_with_an_emoji():
    """Цитата из прежней сводки внутри разговора — не тезис (Minor DS круга 2)."""
    text = ("> 10:22 📌 смета к пятому\n"
            "> 💭 мысль без времени (старый формат)\n"
            "> он сказал: «в сводке было 📌 про смету» — цитата\n"
            "📌 не цитата вовсе\n")
    assert meeting_archive.cothinking_notes(text) == ["10:22 📌 смета к пятому", "💭 мысль без времени (старый формат)"]


def test_the_archive_takes_the_debrief_the_writers_own_not_the_alphabetical_twin(tmp_path):
    """Архив брал разбор по префиксу стема: у голой посекундной стенограммы разбор
    назван минутным ключом графа и под префикс не попадает, а старое имя от стема
    рядом — двойня, которая и уезжала в папку. Читатель — по тому же правилу, что
    писатели: `derivative_path` (критика DS круга 0 и GLM круга 2 по №309)."""
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    main = tdir / "2026-09-02_102113.md"                     # голая посекундная, минута свободна
    main.write_text("# Встреча 2026-09-02_102113\n" + SPEECH, encoding="utf-8")
    ours = meeting_stamp.derivative_path(main, "debrief", graph)
    assert ours.name == "2026-09-02_1021_разбор.md"
    ours.write_text("наш разбор\n", encoding="utf-8")
    twin = tdir / "2026-09-02_102113_разбор.md"              # старое имя от стема — под префиксом
    twin.write_text("двойня по стему\n", encoding="utf-8")
    folder = meeting_archive.archive_meeting(graph, tdir, "2026-09-02_1021", "", files_key=main.stem).folder
    assert (folder / "Разбор.md").read_text(encoding="utf-8") == "наш разбор\n"
    # ожидаемого нет — прежний порядок: что нашлось по префиксу
    ours.unlink()
    (folder / "Разбор.md").unlink()
    folder = meeting_archive.archive_meeting(graph, tdir, "2026-09-02_1021", "", files_key=main.stem).folder
    assert (folder / "Разбор.md").read_text(encoding="utf-8") == "двойня по стему\n"


def test_retro_fill_summary_flag_adopts_the_sound_legacy_first_and_rebuilds_the_rest(tmp_path, monkeypatch, capsys):
    """Critical DS выходного круга по №314: у 74 протухших легаси-саммари не было
    ни одного пути к пересборке, а состояние саммари в отчёте ретро не
    печаталось. Явный флаг: `--summary=adopt` — присвоить согласованное легаси
    без модели и ничего не строить, `--summary=rebuild` — присвоить, потом
    собрать остальное. Круг 2 (DS Critical, GLM Important): присвоение и
    сборка — внутри `archive_meeting` по одной папке и одному снимку канона,
    исход едет возвратом (`Archived.summary`), отчёт печатает его, а не
    выводит из состояния диска — «пропущено: саммари fresh» о только что
    пересобранном больше невозможно."""
    import os
    import time
    live, tdir = _meeting(tmp_path, monkeypatch)
    folder = tmp_path / "graph" / "Встречи-архив" / "2026-09-02 10-21 — Смета"
    folder.mkdir(parents=True)
    legacy = "---\ntype: саммари\nдата: 2026-09-02\n---\n\n# Саммари — старое\n\nСуть: было.\n"
    (folder / "Минутки.md").write_text("## Решения\n1. Первое.\n", encoding="utf-8")
    (folder / "Саммари.md").write_text(legacy, encoding="utf-8")
    old = time.time() - 3600
    os.utime(folder / "Минутки.md", (old, old))
    seen: list = []

    def fake_archive(graph, tdir_, stamp, slug, files_key=None, mode=None):
        # как настоящий: сначала копии материалов, потом один проход по саммари
        seen.append(mode)
        (folder / "Стенограмма.md").write_text(live.read_text(encoding="utf-8"), encoding="utf-8")
        os.utime(folder / "Стенограмма.md", (old, old))
        # режим пробрасывается как есть — фейк не повторяет выражений боевого кода
        # (круг 3: фейк с `policy or DEFAULT` воспроизводил баг, который должен ловить)
        outcome = meeting_archive.summary_pass(folder, live, None, mode=mode)
        return meeting_archive.Archived(folder, outcome)
    monkeypatch.setattr(retro_fill, "archive_meeting", fake_archive)
    monkeypatch.setattr(meeting_archive, "load_user_or_example", lambda root: _cfg(tmp_path), raising=False)
    # без флага: легаси UNKNOWN не трогается, состояние в отчёте есть
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir)
    assert seen == [meeting_archive.SummaryMode.AUTO] and "саммари" not in made
    out = capsys.readouterr().out
    assert "пропущено:" in out and "саммари unknown" in out
    assert "было" in (folder / "Саммари.md").read_text(encoding="utf-8")
    # adopt: паспорт без модели, отчёт «собрано: саммари присвоено» и ничего про «пропущено: саммари»
    n = len(_FakeLLM.calls)
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir, summary="adopt")
    assert seen[-1] is meeting_archive.SummaryMode.ADOPT
    assert "саммари присвоено" in made and len(_FakeLLM.calls) == n
    assert live_sidecar.read(live)["summary_adopted"] and "было" in (folder / "Саммари.md").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "собрано: саммари присвоено" in out and "пропущено: саммари" not in out
    # ревизия переписала минутки → STALE; rebuild собирает моделью, отчёт «собрано: саммари»
    (folder / "Минутки.md").write_text("## Решения\n1. Отменено ревизией.\n", encoding="utf-8")
    made = retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir, summary="rebuild")
    assert seen[-1] is meeting_archive.SummaryMode.REBUILD
    assert made.count("саммари") == 1 and "саммари присвоено" not in made
    assert "было" not in (folder / "Саммари.md").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "собрано:" in out and "саммари" in out.split("собрано:")[1].split(";")[0] and "пропущено: саммари" not in out
    # второй rebuild подряд — FRESH, «пропущено: саммари fresh», модель молчит
    n = len(_FakeLLM.calls)
    retro_fill.process(live, _cfg(tmp_path), tmp_path / "graph", tdir, summary="rebuild")
    assert len(_FakeLLM.calls) == n and "саммари fresh" in capsys.readouterr().out
    # CLI: флаг доходит до process, без флага — None
    calls: list = []
    monkeypatch.setattr(retro_fill, "ROOT", tmp_path)
    monkeypatch.setattr(retro_fill, "load_user_or_example", lambda root: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(retro_fill.graphs, "graph_dir", lambda cfg: tmp_path / "graph")
    monkeypatch.setattr(retro_fill, "harden_umask", lambda: None)
    monkeypatch.setattr(retro_fill, "process", lambda f, cfg, graph, tdir_, summary=None, tally=None: calls.append(summary) or [])
    retro_fill.main(["--summary=adopt", str(live)])
    retro_fill.main([str(live)])
    assert calls == ["adopt", None]
    with pytest.raises(SystemExit):
        retro_fill.main(["--summary=all", str(live)])
