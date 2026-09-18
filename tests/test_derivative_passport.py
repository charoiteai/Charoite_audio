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
import meeting_archive  # noqa: E402
import meeting_stamp  # noqa: E402
import rebuild_transcript  # noqa: E402
import retro_fill  # noqa: E402
import transcript  # noqa: E402

SPEECH = "**Инга** [10:21]:\nСмету пришлю к пятому, провайдер прежний.\n" * 30


def test_speech_of_ignores_the_title_and_the_notes_tail():
    """Речь источника — без H1 и без «Ко-мышления»: ретитл переписывает первую
    строку, и хеш с заголовком делал минутки собранными «по другой речи» на
    первом же ретитле (Critical DS)."""
    bare = "# Встреча 2026-09-02_1021\n" + SPEECH
    titled = "# Встреча 2026-09-02_1021 — Смета\n" + SPEECH
    with_notes = titled + transcript.NOTES_HEAD + "\n> 10:22 📌 мысль модели\n"
    assert transcript.speech_of(bare) == transcript.speech_of(titled) == transcript.speech_of(with_notes) == SPEECH
    assert transcript.speech_of(SPEECH) == SPEECH, "без заголовка — весь текст"
    assert rebuild_transcript._speech(with_notes) == SPEECH, "пересборка живёт тем же правилом"


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
    assert live_sidecar.attest(live, "debrief", "текст разбора", live_sidecar.sha(SPEECH))
    meta = live_sidecar.read(live)
    assert meta["debrief_sha256"] == live_sidecar.sha("текст разбора") and meta["debrief_source_sha256"] == live_sidecar.sha(SPEECH)
    dpath = tmp_path / "x_разбор.md"
    dpath.write_text("текст разбора", encoding="utf-8")
    assert live_sidecar.derivative_state(dpath, meta, "debrief", live_sidecar.sha(SPEECH)) == live_sidecar.FRESH


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

    def __init__(self, cfg):
        pass

    def minutes(self, text):
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
    src = live_sidecar.sha(SPEECH)
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
            "minutes_source_sha256": live_sidecar.sha(SPEECH)}
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
    assert i < gu.index("debrief = LLM(cfg).complete("), "владение разбора — до вызова модели"
    assert "expect=d_before" in gu and 'live_sidecar.attest(tpath, "debrief"' in gu
