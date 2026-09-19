"""№316/№317: оговорка о неполной записи — факт о записи, не речь.

18.09 собеседников не было 40 минут: демон писал итог в хвост «Ко-мышления»
только на стопе (гибель демона — итога нет), а минутки и разбор получали либо
весь файл (MCP: след канала читался как сказанное), либо только речь (всё
остальное: протокол выходил внешне полным). Здесь один объект
`MeetingSource(speech, recording_note)`: речь режут свёртки, оговорка идёт
блоком снаружи тега после всех обрезок, в документ — механической строкой,
паспорт хеширует речь + оговорку, пересборка восстанавливает итог из сайдкара.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import channel_trace  # noqa: E402
import live_sidecar  # noqa: E402
import llm as llm_mod  # noqa: E402
import mcp_server  # noqa: E402
import meeting_source  # noqa: E402
import rebuild_transcript  # noqa: E402
import retro_fill  # noqa: E402
import transcript  # noqa: E402

BASE = 1_757_000_000.0
# один эпизод без собеседников: канал пропал (граница stopped_at), вернулся через 40 минут
EVENTS = [
    {"label": "blackhole", "kind": "lost", "at": BASE + 70, "stopped_at": BASE,
     "silent_s": None, "died": True, "cause": "hung", "reason": "нет кадров"},
    {"label": "blackhole", "kind": "back", "at": BASE + 2400, "stopped_at": BASE,
     "silent_s": 2400.0, "died": True, "cause": "hung", "reason": "нет кадров"},
]
NOTE = channel_trace.summary_of(EVENTS)
SPEECH = "**Инга** [10:21]:\nСмету пришлю к пятому, провайдер прежний.\n" * 30   # > MINUTES_MIN_CHARS


def _llm(lang: str = "ru") -> llm_mod.LLM:
    return llm_mod.LLM({"sufler": {"role": "роль", "language": lang},
                        "llm": {"base_url": "http://127.0.0.1:11434", "model": "m"}})


def _minutes_prompt(lang: str, text: str, note: str | None, fold: bool = False) -> str:
    client = _llm(lang)
    seen: dict = {}

    def fake_stream(prompt, **kw):
        seen["prompt"] = prompt
        return iter(())

    client.stream = fake_stream
    if fold:
        client._fit = lambda t: "[свёрнуто: сводки частей]"   # свёртка подменяет ВЕСЬ текст
    list(client.minutes(text, recording_note=note))
    return seen["prompt"]


@pytest.mark.parametrize("lang,close,tag", [("ru", "</стенограмма>", "<о_записи>"),
                                            ("en", "</transcript>", "<recording>"),
                                            ("zh", "</transcript>", "<recording>")])
def test_the_note_is_a_block_outside_the_transcript_tag_after_the_fold(lang, close, tag):
    """Оговорка внутри стенограммы уехала бы в сводку части или в отброшенную
    середину; снаружи тега и после `_fit` она доходит до модели дословно."""
    prompt = _minutes_prompt(lang, "реплика\n" * 5000, NOTE, fold=True)
    assert "[свёрнуто: сводки частей]" in prompt and "реплика\nреплика" not in prompt
    assert prompt.count(NOTE) == 1 and prompt.index(NOTE) > prompt.index(close)
    assert prompt.index(tag) > prompt.index(close), "блок — после закрывающего тега"
    assert "молчание" in prompt or "silence" in prompt or "沉默" in prompt, "правило «не согласие» — в блоке"
    plain = _minutes_prompt(lang, "реплика\n", None)
    assert tag not in plain and channel_trace.SUMMARY_MARK not in plain, "без оговорки блока нет"


def test_the_note_changes_the_source_hash_but_not_the_speech(tmp_path):
    live = tmp_path / "2026-09-02_1021.md"
    text = "# Встреча 2026-09-02_1021\n" + SPEECH
    live.write_text(text, encoding="utf-8")
    plain = meeting_source.of(live, text)
    assert plain.recording_note is None and plain.canon() == plain.speech == transcript.speech_of(text)
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    noted = meeting_source.of(live, text)
    assert noted.speech == plain.speech, "речь оговорка не трогает"
    assert noted.recording_note == NOTE and NOTE.startswith(channel_trace.SUMMARY_MARK)
    assert noted.sha() != plain.sha()
    assert noted.sha() == live_sidecar.sha(noted.speech + "\n\n" + NOTE)
    assert meeting_source.live(noted.speech, NOTE) == noted, "живой путь и путь по файлу — один объект"


class _FakeLLM:
    calls: list = []
    lang = "ru"
    recording_block = llm_mod.LLM.recording_block   # настоящий блок, взят до подмены llm.LLM

    def __init__(self, cfg):
        pass

    def minutes(self, text, recording_note=None):
        _FakeLLM.calls.append(("minutes", text, recording_note))
        yield "# Минутки\n**Участники:** Инга\n## Поручения\n- [ ] **Инга** — смета — до 05.09\n"

    def complete(self, prompt, **kw):
        _FakeLLM.calls.append(("complete", prompt, None))
        return "## Раздел\n- пункт\n"


def _quiet(monkeypatch):
    _FakeLLM.calls = []
    monkeypatch.setattr(llm_mod, "LLM", _FakeLLM)
    monkeypatch.setattr(retro_fill, "LLM", _FakeLLM)
    monkeypatch.setattr(rebuild_transcript, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rebuild_transcript, "canonize_file", lambda *a, **k: None)
    monkeypatch.setattr(retro_fill, "archive_meeting", lambda *a, **k: None)


def test_fresh_check_and_passport_read_the_same_source(tmp_path, monkeypatch):
    """Писатель паспорта и читатель свежести — одна функция: минутки без
    оговорки при появившейся оговорке устаревают ровно один раз."""
    _quiet(monkeypatch)
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    final = "# Встреча 2026-09-02_1021\n" + SPEECH
    minutes = "# Минутки\nстарые, но машинные\n"
    mpath = live.with_name("2026-09-02_1021_minutes.md")
    mpath.write_text(minutes, encoding="utf-8")
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    src = meeting_source.of(live, final)
    meta = {"minutes_sha256": live_sidecar.sha(minutes), "minutes_source_sha256": src.sha()}
    assert rebuild_transcript.finalize_minutes(live, final, meta, {}, {}) == "fresh"
    assert _FakeLLM.calls == [], "по той же речи и оговорке модель не зовут"

    meta["minutes_source_sha256"] = live_sidecar.sha(src.speech)   # паспорт до события канала
    assert rebuild_transcript.finalize_minutes(live, final, meta, {}, {}) == "regenerated"
    assert _FakeLLM.calls == [("minutes", src.speech, NOTE)], "модели — речь и оговорка раздельно"
    doc = mpath.read_text(encoding="utf-8")
    assert doc.count(NOTE) == 1, "строка итога — механически, один раз"

    rebuild_transcript.record_minutes_passport(live, mpath, "regenerated", final, {})
    meta2 = live_sidecar.read(live)
    assert meta2["minutes_source_sha256"] == src.sha() and meta2["minutes_sha256"] == live_sidecar.sha(doc)
    _FakeLLM.calls = []
    assert rebuild_transcript.finalize_minutes(live, final, meta2, {}, {}) == "fresh" and _FakeLLM.calls == []


def test_rebuild_restores_the_summary_from_the_sidecar_into_the_tail(tmp_path):
    """Итог по эпизодам — из сайдкара, не от демона на стопе (№316): при
    гибели демона строки нет, события есть. В хвост «Ко-мышления», не в речь;
    второй прогон не дублирует; живой хвост — дописывается, не удваивается."""
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    final = "# Встреча 2026-09-02_1021\n\n**Инга** [10:21]:\nречь\n"
    assert rebuild_transcript._with_recording_summary(live, final) == final, "событий нет — текст прежний"
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    stamp = channel_trace.summary_line(EVENTS)[:5]                      # «HH:MM» последнего события
    once = rebuild_transcript._with_recording_summary(live, final)
    assert once.count(NOTE) == 1 and transcript.NOTES_HEAD in once
    assert transcript.speech_of(once) == transcript.speech_of(final), "итог — в хвосте, не в речи"
    assert transcript.notes_of(once) == [f"{stamp} {NOTE}"]
    assert rebuild_transcript._with_recording_summary(live, once) == once, "второй прогон не дублирует"
    with_tail = (final.rstrip("\n") + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
                 + "\n> 10:30 📌 КТ: смета\n")
    got = rebuild_transcript._with_recording_summary(live, with_tail)
    assert got.count(transcript.NOTES_HEAD) == 1
    assert transcript.notes_of(got) == ["10:30 📌 КТ: смета", f"{stamp} {NOTE}"]
    # источник по восстановленному файлу видит оговорку — так же, как живой демон
    assert meeting_source.of(live, got).recording_note == NOTE


def test_rebuild_calls_the_restore_before_the_canon_and_the_write():
    src = (ROOT / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):src.index("def write_final(")]
    restore = fn.index("final_text = _with_recording_summary(live, final_text)")
    assert restore < fn.index("final_text = canonize(final_text, cfg)") < fn.index("write_final(live, final_text, live_text)")
    assert fn.index("body.append(m.group(0).lstrip") < restore, "перенос живого хвоста — раньше, итог дописывается в него"


def test_events_of_reads_the_json_string_the_writer_stores_and_drops_garbage(tmp_path):
    live = tmp_path / "2026-09-02_1021.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    assert channel_trace.events_of(live) == [] and channel_trace.recording_note(live) is None
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    assert channel_trace.events_of(live) == EVENTS
    assert channel_trace.recording_note(live) == NOTE
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, "{мусор")
    assert channel_trace.events_of(live) == [] and channel_trace.recording_note(live) is None
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps([1, {"kind": "lost"}]))
    assert channel_trace.events_of(live) == [], "список не из словарей — не события"


def test_with_note_is_mechanical_and_idempotent():
    assert meeting_source.with_note("# Минутки\n", None) == "# Минутки\n"
    once = meeting_source.with_note("# Минутки\n\n", NOTE)
    assert once == "# Минутки\n\n" + NOTE + "\n"
    assert meeting_source.with_note(once, NOTE) == once
    already = "# Минутки\n## Риски\n- " + NOTE + "\n"
    assert meeting_source.with_note(already, NOTE) == already, "модель сама вставила строку — не дублировать"


def test_notes_of_returns_the_tail_lines_without_the_quote_mark():
    text = ("# Встреча\nречь\n> цитата в речи\n" + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 10:30 📌 КТ\n> 10:31 💭 мысль\n")
    assert transcript.notes_of(text) == ["10:30 📌 КТ", "10:31 💭 мысль"]
    assert transcript.notes_of("# Встреча\nречь\n> цитата в речи\n") == []


def test_retro_fill_gives_the_model_speech_plus_note_and_stamps_the_passport(tmp_path, monkeypatch):
    _quiet(monkeypatch)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-02_1021.md"
    text = ("# Встреча 2026-09-02_1021\n" + SPEECH + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 10:30 📌 КТ: смета\n")
    live.write_text(text, encoding="utf-8")
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    cfg = {"llm": {"base_url": "http://127.0.0.1:11434", "model": "m"},
           "log": {"transcripts_dir": "transcripts"}, "sufler": {"user_name": "Владелец"}}
    made = retro_fill.process(live, cfg, tmp_path / "graph", tdir)
    assert "разбор" in " ".join(made)
    src = meeting_source.of(live, text)
    prompts = [p for k, p, _ in _FakeLLM.calls if k == "complete"]
    assert prompts, "разбор — промптом"
    for p in prompts:
        assert "📌 КТ: смета" not in p, "хвост «Ко-мышления» — не речь"
        assert p.count(NOTE) == 1 and p.index(NOTE) > p.index(src.speech[-40:]), "оговорка — после речи, блоком"
    assert _FakeLLM.calls[0] == ("minutes", src.speech, NOTE), "минутки — тем же источником"
    debrief = live.with_name("2026-09-02_1021_разбор.md").read_text(encoding="utf-8")
    assert debrief.count(NOTE) == 1
    meta = live_sidecar.read(live)
    assert meta["debrief_source_sha256"] == src.sha() and meta["minutes_source_sha256"] == src.sha()


def test_mcp_minutes_take_the_speech_the_note_and_leave_a_passport(tmp_path, monkeypatch):
    """MCP-минутки читали ВЕСЬ файл с хвостом: след канала уходил модели как
    сказанное. Теперь — речь в свёртку, оговорка блоком после неё, строка в
    документ, паспорт как у пересборки."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    f = tdir / "2026-09-13_1200.md"
    text = ("# Встреча\n" + "реплика\n" * 100 + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 12:30 📌 КТ: смета\n")
    f.write_text(text, encoding="utf-8")
    monkeypatch.setattr(mcp_server, "TRANSCRIPTS", tdir)
    assert live_sidecar.remember(f, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    seen: dict = {}

    class Fake:
        lang = "ru"
        recording_block = llm_mod.LLM.recording_block

        def fit(self, t):
            seen["fit"] = t
            return "[сжато: сводки частей]"

        def complete(self, prompt, **kw):
            seen["prompt"] = prompt
            return "- **Кто** — что — срок"

    monkeypatch.setattr(mcp_server, "_client", lambda: Fake())
    out = mcp_server.sufler_make_minutes()
    assert "📌 КТ: смета" not in seen["fit"] and "реплика" in seen["fit"], "в свёртку — только речь"
    assert seen["prompt"].count(NOTE) == 1 and seen["prompt"].index(NOTE) > seen["prompt"].index("[сжато: сводки частей]")
    doc = (tdir / "2026-09-13_1200_minutes.md").read_text(encoding="utf-8")
    assert doc.count(NOTE) == 1
    meta = live_sidecar.read(f)
    assert meta["minutes_sha256"] == live_sidecar.sha(doc)
    assert meta["minutes_source_sha256"] == meeting_source.of(f, text).sha()
    assert "Минутки сохранены" in out and "паспорт" not in out


def test_every_prompt_builder_puts_the_note_after_its_own_cut():
    """Пять сборщиков промпта режут вход по-своему (окно черновика, `_fit`,
    `debrief_excerpt`, `[:24000]`, обрезки материалов саммари); блок оговорки у
    каждого — после своей обрезки и снаружи речи, строка в документ — механически."""
    src = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    loop = src[src.index("def minutes_loop"):src.index("def gen_answer")]
    assert loop.index("full[-14_000:]") < loop.index("note = trace.summary()") < loop.index("llm.recording_block(note)") < loop.index("Обнови ЧЕРНОВИК")
    assert "meeting_source.with_note(out, note)" in loop
    summ = src[src.index("def _do_summary"):src.index("threading.Thread(target=_do_summary")]
    assert 'source = meeting_source.live(tr.full() or "(пусто)", trace.summary())' in summ
    assert "llm.minutes(source.speech, recording_note=source.recording_note)" in summ
    assert 'fact_check.annotate("".join(chunks), source.canon())' in summ, "сверка — по тому же множеству, что видела модель"
    assert "meeting_source.with_note(doc, source.recording_note)" in summ

    gu = (ROOT / "src" / "graph_updater.py").read_text(encoding="utf-8")
    block = gu[gu.index("source = meeting_source.of(tpath, file_text)"):gu.index("Составь разбор строго по разделам")]
    assert "source_sha = source.sha()" in block and "speech_sha" not in gu, "хеш источника — не «хеш речи» (GLM круга 2)"
    assert (block.index("debrief_excerpt(source.speech, limit=speech_limit") < block.index("+ cothinking_block")
            < block.index("+ minutes_block") < block.index("llm_client.recording_block(source.recording_note)"))
    assert "meeting_source.debrief_parts(" in block, "один бюджет на тело промпта — распределителем (DS/GLM круга 3)"
    assert "debrief_excerpt(context)" not in gu, "разбор больше не берёт окно от конца ФАЙЛА"
    assert gu.count("meeting_source.minutes_block(") == 1 and gu.count("meeting_source.debrief_parts(") == 1, \
        "минутки в оба промпта — хелпером meeting_source (извлечение напрямую, разбор через распределитель)"
    assert 'context += "\\n\\n[МИНУТКИ]\\n" + minutes_p.read_text' not in gu, "извлечение не клеит сырые минутки (Critical DS круга 2)"

    rf = (ROOT / "src" / "retro_fill.py").read_text(encoding="utf-8")
    gen = rf[rf.index("def gen("):rf.index("\ndef ", rf.index("def gen(") + 1)]
    assert gen.index("transcript[:24000]") < gen.index("client.recording_block(note)") < gen.index("+ task")
    assert rf.count("note=source.recording_note") == 2 and "source_sha = source.sha()" in rf and "speech_sha" not in rf
    assert rf.count("meeting_source.with_note(out, source.recording_note)") == 2, "строка итога — у разбора И тезисов (DS круга 2)"
    assert "meeting_source.of(f, text)" in rf and "meeting_source.of(f, text, bare)" not in rf, \
        "источник — по прямому сайдкару, штамп bare читателю не передаётся (Critical DS/GLM круга 1)"

    mcp = (ROOT / "src" / "mcp_server.py").read_text(encoding="utf-8")
    fn = mcp[mcp.index("def sufler_make_minutes"):mcp.index("def sufler_hints")]
    assert fn.index("client.fit(source.speech)") < fn.index("client.recording_block(source.recording_note)")
    assert "meeting_source.with_note(out, source.recording_note)" in fn
    assert 'live_sidecar.attest(f, "minutes", out, source.sha())' in fn

    rt = (ROOT / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    fin = rt[rt.index("def finalize_minutes("):rt.index("def _fallback_restamp(")]
    assert "source = meeting_source.of(live, final_text)" in fin and "source.matches(" in fin
    assert "meeting_source.of(live, edited).matches(" in rt, "второй читатель паспорта — тем же источником"
    assert "_speech(" not in rt, "хеш голой речи как источник производной — прохода мимо MeetingSource нет"
    gu2 = (ROOT / "src" / "graph_updater.py").read_text(encoding="utf-8")
    assert "debrief = meeting_source.with_note(debrief, source.recording_note)" in gu2, "живой разбор — со строкой итога"
    # пятый сборщик — саммари архива (№314): блок факта после материалов, строка в документ
    ma_src = (ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8")
    gen = ma_src[ma_src.index("def _gen_summary("):ma_src.index("def _unhide(")]
    assert gen.index("</материалы>") < gen.index("client.recording_block(recording_note)") < gen.index("Составь саммари")
    assert "text = meeting_source.with_note(text, recording_note)" in gen
    assert "context = meeting_source.content_of(" in gu2, "извлечение — по содержанию без следа записи"
    assert "LLM(cfg).minutes(speech, recording_note=source.recording_note)" in fin
    assert "fact_check.annotate(doc, source.canon())" in fin and "meeting_source.with_note(doc, source.recording_note)" in fin
    passport = rt[rt.index("def record_minutes_passport("):rt.index("\ndef ", rt.index("def record_minutes_passport(") + 1)]
    assert "meeting_source.of(live, final_text).sha()" in passport, "паспорт — тем же объектом, что fresh-проверка"


def test_a_titled_meeting_reads_the_same_sidecar_on_every_path(tmp_path, monkeypatch):
    """Critical DS и GLM круга 1: после наката темы сайдкар лежит под именем с
    темой; чтение по посекундному штампу `bare` видело пустоту, и ретро-обход
    считал хеш без оговорки, пока живой путь — с ней. Один сайдкар на всех."""
    _quiet(monkeypatch)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-02_1021_Смета.md"
    text = "# Встреча 2026-09-02_1021 — Смета\n" + SPEECH
    live.write_text(text, encoding="utf-8")
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    assert [p.name for p in tdir.glob("*.live.json")] == ["2026-09-02_1021_Смета.md.live.json"]
    src = meeting_source.of(live, text)
    assert src.recording_note == NOTE, "источник читает прямой сайдкар файла с темой"
    cfg = {"llm": {"base_url": "http://127.0.0.1:11434", "model": "m"},
           "log": {"transcripts_dir": "transcripts"}, "sufler": {"user_name": "Владелец"}}
    made = retro_fill.process(live, cfg, tmp_path / "graph", tdir)
    assert "разбор" in " ".join(made)
    meta = live_sidecar.read(live)
    assert meta["debrief_source_sha256"] == src.sha() == meta["minutes_source_sha256"]
    assert src.matches(meta["debrief_source_sha256"]) and not src.matches(live_sidecar.sha(src.speech))
    _FakeLLM.calls = []
    again = retro_fill.process(live, cfg, tmp_path / "graph", tdir)
    assert _FakeLLM.calls == [], f"второй прогон по тому же источнику модель не зовёт: {again}"


def test_content_for_extraction_keeps_theses_and_drops_the_recording_trace():
    """Important DS круга 1: след записи в хвосте (событие канала, итог) уходил
    в извлечение узлов графа как содержание встречи."""
    tail = (transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 10:30 📌 КТ: смета\n"
            + "> 10:32 ⚠️ системный звук (собеседники) пропал (10:31) — дальше запись без собеседников\n"
            + "> 10:40 ✅ системный звук (собеседники) снова пишется с 10:40; без собеседников было 9 мин\n"
            + f"> 11:00 {NOTE}\n")
    text = "# Встреча\n**Инга** [10:21]:\nречь про ⚠️ и 📋 в самой речи\n" + tail
    content = meeting_source.content_of(text)
    assert "речь про ⚠️ и 📋 в самой речи" in content and "📌 КТ: смета" in content
    assert "пропал (10:31)" not in content and "снова пишется" not in content and NOTE not in content
    assert transcript.speech_of(content) == transcript.speech_of(text), "речь не тронута"
    assert meeting_source.content_of("# Встреча\nречь\n") == "# Встреча\nречь\n"
    assert channel_trace.summary_line(EVENTS).endswith(NOTE) and channel_trace.summary_line([]) is None


def test_with_note_dedups_by_the_canonical_text_not_the_mark():
    """Критика DS круга 1: модель, пересказавшая оговорку своими словами с тем
    же значком, не должна отменять машинную строку с интервалами и суммой."""
    paraphrased = "# Минутки\n## Риски\n- " + channel_trace.SUMMARY_MARK + ": часть встречи без собеседников\n"
    out = meeting_source.with_note(paraphrased, NOTE)
    assert out.count(NOTE) == 1 and out.count(channel_trace.SUMMARY_MARK) == 2
    assert meeting_source.with_note(out, NOTE) == out


@pytest.mark.parametrize("kind,label,died", [(k, lbl, d) for k in ("lost", "back", "gap")
                                             for lbl in channel_trace.NAMES for d in (True, False)])
def test_every_rendered_trace_line_is_recognised_as_trace(kind, label, died):
    """Таблица kind × label × died: правило «что считать следом» строится из тех
    же фраз, что рендер, и любая переформулировка ломает этот тест, а не молча
    меняет извлечение (критика GLM круга 2)."""
    import types
    ev = types.SimpleNamespace(label=label, kind=kind, at=BASE + 300, stopped_at=BASE, silent_s=300.0,
                               died=died, cause="hung", reason="нет кадров")
    line = channel_trace.render(ev)
    assert line is not None
    assert channel_trace.is_trace_line(f"> 14:32 {line}") and channel_trace.is_trace_line(line)
    assert channel_trace.is_trace_line("> 15:00 " + NOTE)
    for thought in ("> 10:30 📌 КТ: смета", "10:31 💭 мысль", "⚠️ подсказки отстают", "**Инга** [10:21]:", "⏮ уже обсуждалось",
                    # фраза следа без имени канала — строка модели, не след (Important DS / Minor GLM круга 3)
                    "⚠️ Подрядчик пропал (не отвечает с пятницы) — риск срыва поставки",
                    "⚠️ Демо снова пишется с нуля — скрин перезаписан", "✅ Смета: пробел в записи закрыт"):
        assert not channel_trace.is_trace_line(thought), thought


def test_content_filter_touches_only_the_tail():
    """Important DS круга 2: строка речи, начинающаяся как след (правка руками),
    не должна выпадать из извлечения — фильтр работает по хвосту."""
    speech_line = "⚠️ ваш микрофон пропал (10:31) — сказал участник вслух"
    text = ("# Встреча\n**Инга** [10:21]:\n" + speech_line + "\n" + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 10:32 ⚠️ ваш микрофон пропал (10:31) — дальше запись без вашего голоса\n")
    content = meeting_source.content_of(text)
    assert speech_line in content and "дальше запись без вашего голоса" not in content


def test_minutes_block_for_prompts_drops_the_note_and_caps(tmp_path):
    """Critical DS круга 2: минутки клеились к контексту извлечения сырыми —
    строка итога, дописанная в документ механически, возвращалась темой."""
    mpath = tmp_path / "2026-09-02_1021_minutes.md"
    assert meeting_source.minutes_block(mpath, 6000) == ""
    body = "# Минутки\n## Решения\n- смета до пятого\n\n" + NOTE + "\n"
    mpath.write_text(body, encoding="utf-8")
    block = meeting_source.minutes_block(mpath, 6000)
    assert block.startswith("[МИНУТКИ]\n") and "смета до пятого" in block
    assert NOTE in body and channel_trace.SUMMARY_MARK not in block, "след записи — не содержание"
    mpath.write_text("# Минутки\n" + "- пункт\n" * 2000, encoding="utf-8")
    capped = meeting_source.minutes_block(mpath, 600)
    assert len(capped) < 800 and "середина минуток опущена" in capped


def test_the_edited_transcript_path_matches_the_passport_with_the_note(tmp_path, monkeypatch):
    """Читатель паспорта в ветке правленой стенограммы (GLM Critical круга 1):
    паспорт с оговоркой, правка не тронула речь — «ничего не меняю», без _finish."""
    monkeypatch.setattr(rebuild_transcript, "ROOT", tmp_path)
    monkeypatch.setattr(rebuild_transcript, "wait_recording", lambda *a, **k: None)
    (tmp_path / "logs").mkdir()
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-03_1200.md"
    edited = "# Встреча\n**Анна** [12:00]:\nправленый руками текст\n"
    live.write_text(edited, encoding="utf-8")
    assert live_sidecar.remember(live, channel_trace.SIDECAR_KEY, json.dumps(EVENTS))
    src = meeting_source.of(live, edited)
    assert src.recording_note == NOTE
    live_sidecar.merge(live, {"transcript_sha256": live_sidecar.sha("машинный текст"),
                              "minutes_source_sha256": src.sha()})
    monkeypatch.setattr(rebuild_transcript, "_finish", lambda *a, **k: pytest.fail("паспорт совпал — _finish не зовут"))
    assert rebuild_transcript.rebuild(live, {"audio": {"samplerate": 16000}, "log": {}}) == live
    # паспорт без оговорки (до №317) — путь «пересобираю по правленому тексту»
    live_sidecar.merge(live, {"minutes_source_sha256": live_sidecar.sha(src.speech)})
    called = []
    monkeypatch.setattr(rebuild_transcript, "_finish", lambda *a, **k: called.append(a))
    assert rebuild_transcript.rebuild(live, {"audio": {"samplerate": 16000}, "log": {}}) == live
    assert len(called) == 1


def test_theses_writers_add_the_note_line(tmp_path):
    """Important DS круга 2: паспорт тезисов говорит «собрано с оговоркой», а
    документ строки не имел; у живых тезисов архива оговорка — из хвоста копии."""
    import meeting_archive
    folder = tmp_path / "2026-09-02_1021 — Смета"
    folder.mkdir()
    (folder / "Стенограмма.md").write_text(
        "# Встреча\nречь\n" + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
        + "\n> 10:30 📌 КТ: смета\n> 11:00 " + NOTE + "\n", encoding="utf-8")
    meeting_archive._derive_extras(folder)
    theses = (folder / "Тезисы.md").read_text(encoding="utf-8")
    assert "- 10:30 📌 КТ: смета" in theses and theses.count(NOTE) == 1
    assert meeting_source.note_in_tail("нет строки\n") is None


def test_debrief_budget_holds_by_construction(tmp_path):
    """Important DS и GLM круга 3: пол у речи поверх трёх потолков ломал сумму;
    распределитель отдаёт речи не меньше минимума, минуткам — остаток."""
    mpath = tmp_path / "2026-09-02_1021_minutes.md"
    mpath.write_text("# Минутки\n" + "- пункт\n" * 2000, encoding="utf-8")   # 16 000 знаков черновика
    thoughts = [f"10:{i:02d} 📌 КТ номер {i}: " + "слово " * 20 for i in range(60)]  # > 2000 знаков тезисов
    parts = meeting_source.debrief_parts(mpath, thoughts, total=11000, speech_min=4000,
                                         minutes_cap=6000, cothinking_cap=2000)
    assert parts.speech_limit >= 4000
    assert parts.speech_limit + len(parts.cothinking_block) + len(parts.minutes_block) <= 11000
    assert "середина минуток опущена" in parts.minutes_block and "середина тезисов опущена" in parts.cothinking_block
    # короткая встреча без минуток и тезисов — вся речь в прежнее окно
    empty = meeting_source.debrief_parts(tmp_path / "нет.md", [], total=11000, speech_min=4000,
                                         minutes_cap=6000, cothinking_cap=2000)
    assert empty == (11000, "", "")


def test_note_in_tail_takes_the_last_summary_line_of_the_tail_only():
    """Minor DS и GLM круга 3: читатель строки итога — тем же правилом, что
    классификатор, по хвосту, последнюю (свежую) строку."""
    older = channel_trace.SUMMARY_MARK + ": без собеседников 10:00–10:05 (эпизодов 1, всего 5 мин)"
    text = ("# Встреча\nречь про строку " + channel_trace.SUMMARY_MARK + " в интерфейсе\n"
            + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX
            + "\n> 10:30 📌 КТ: показать " + channel_trace.SUMMARY_MARK + " красным\n"
            + "> 10:40 " + older + "\n> 11:00 " + NOTE + "\n")
    assert meeting_source.note_in_tail(text) == NOTE
    assert meeting_source.note_in_tail("# Встреча\nречь " + channel_trace.SUMMARY_MARK + "\n") is None


def test_archive_theses_take_the_note_from_the_sidecar_first(tmp_path, monkeypatch):
    """Критика GLM круга 3: факт о записи живёт в сайдкаре оригинала; хвост
    копии — запасной путь, если сайдкара нет."""
    import meeting_archive
    folder = tmp_path / "2026-09-02_1021 — Смета"
    folder.mkdir()
    (folder / "Стенограмма.md").write_text(
        "# Встреча\nречь\n" + transcript.NOTES_HEAD + transcript.NOTES_SUFFIX + "\n> 10:30 📌 КТ: смета\n",
        encoding="utf-8")
    meeting_archive._derive_extras(folder, recording_note=NOTE)
    assert (folder / "Тезисы.md").read_text(encoding="utf-8").count(NOTE) == 1
    src = (ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8")
    # оговорка читается один раз и раздаётся тезисам и саммари (Minor GLM входного круга по №314)
    assert "recording_note = channel_trace.recording_note(main)" in src
    assert "_derive_extras(folder, recording_note=recording_note)" in src
    assert src[src.index("summary_pass(folder, main, "):][:60].startswith("summary_pass(folder, main, recording_note,")
