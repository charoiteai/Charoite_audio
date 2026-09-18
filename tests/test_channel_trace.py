"""След события канала в документах встречи (№234): пропажа и возврат канала
собеседников или микрофона видны не только в статусе UI и capture.log, но и в
сайдкаре, хвосте стенограммы и нити — по структурному событию хаба из одной
точки, с истинными границами по последнему кадру.

Входной круг DS и GLM (18.09): за 14–18.09 у 12 записей из 36 были потери
системного звука (2…42 строк на запись, флап до 21 цикла), оживление не
оставляло следа ни в одном файле; `Loss.since` — момент крика, а не тишины
(детекция опаздывает на 66–96 с); тихий перезапуск не давал события вовсе;
стоп-дамп сайдкара одной строкой стирал бы всё, что записано во время встречи.
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import tempfile
import time

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import audio as a  # noqa: E402
import channel_trace  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_thread  # noqa: E402
import transcript  # noqa: E402


class _Cap:
    def __init__(self, label):
        self.label = label
        self.q = queue.Queue()

    def stop(self):
        pass


def _hub(*labels):
    cfg = {"audio": {"samplerate": 16000, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                     "vad_energy_db": -45.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    return a.AudioHub(cfg, captures=[_Cap(lbl) for lbl in labels])


def _quiet(monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: None)
    monkeypatch.setattr(a, "ROOT", pathlib.Path(tempfile.mkdtemp()))


def test_loss_and_return_are_one_event_each_with_the_true_silence_boundary(monkeypatch):
    """Граница дыры — последний кадр, не момент крика: крик опаздывает на порог
    сторожа и рестарты (Critical GLM). Смена фазы того же эпизода — не событие."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    t0 = time.time()
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = t0 - 70   # звук встал 70 с назад
    hub._announce_losses({"blackhole": a.Loss("рестарт не удался", retriable=True, died=True)})
    hub._announce_losses({"blackhole": a.Loss("перезапуск завис", retriable=False, died=True)})  # смена фазы
    assert [e.kind for e in got] == [a.CH_LOST], "второй крик того же эпизода событием не является"
    ev = got[0]
    assert ev.label == "blackhole" and ev.died and ev.cause == "restart_failed"
    assert abs(ev.stopped_at - (t0 - 70)) < 1e-6 and ev.stopped_at < ev.at
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = time.time()   # кадр пришёл
    hub._announce_back("blackhole", 5.0)              # сторож насчитал 5 с от крика
    back = got[-1]
    assert back.kind == a.CH_BACK and back.silent_s >= 69, "длительность — от истинной границы, не от крика"
    assert hub.channel_log == got, "журнал хаба — тот же список"


def test_a_quiet_restart_is_a_gap_event_and_stop_closes_open_episodes(monkeypatch):
    """Рестарт удался с первой попытки — крика нет, дыра ≥ порога была (Critical
    GLM); канал, не вернувшийся до конца, закрывается остановкой один раз."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    hub._last_frame["mic"] = hub._real_frame["mic"] = now - 40
    monkeypatch.setattr(hub, "_restart_guarded", lambda c: None)   # удачный перезапуск
    hub._sweep(now, {})
    gaps = [e for e in got if e.kind == a.CH_GAP]
    assert len(gaps) == 1 and gaps[0].label == "mic" and gaps[0].cause == "restarted"
    assert abs(gaps[0].silent_s - 40) < 1 and abs(gaps[0].stopped_at - (now - 40)) < 1
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = now - 100
    hub._announce_losses({"blackhole": a.Loss("умер", retriable=False, died=True)})
    hub.end_channel_episodes()
    hub.end_channel_episodes()                        # идемпотентно
    ends = [e for e in got if e.kind == a.CH_END]
    assert len(ends) == 1 and ends[0].cause == "stop" and ends[0].silent_s >= 100


def test_a_channel_missing_from_the_start_is_not_said_to_have_disappeared(monkeypatch):
    _quiet(monkeypatch)
    hub = _hub("mic")
    got = []
    hub.on_channel = got.append
    hub._announce_loss("blackhole", "канал не открылся при старте: нет устройства", died=False)
    ev = got[0]
    assert ev.kind == a.CH_LOST and not ev.died and ev.cause == "start_error" and ev.stopped_at is None
    text = channel_trace.render(ev)
    assert "не захвачен с начала" in text and "пропал" not in text


def test_a_failing_subscriber_does_not_break_the_recording(monkeypatch):
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")

    def boom(ev):
        raise RuntimeError("подписчик упал")
    hub.on_channel = boom
    hub._announce_losses({"mic": a.Loss("умер", retriable=False, died=True)})
    assert "mic" in hub._lost and len(hub.channel_log) == 1


def _events(*specs):
    """(kind, label, stopped_at, at, silent_s, died) → ChannelEvent."""
    return [a.ChannelEvent(label=lbl, kind=kind, at=at, stopped_at=st, silent_s=sil, died=died,
                           cause="hung", reason="причина") for kind, lbl, st, at, sil, died in specs]


def test_the_trace_lands_in_the_sidecar_the_tail_and_the_thread(tmp_path):
    live = tmp_path / "2026-09-02_102113.md"
    notes: list[str] = []
    thread = meeting_thread.Thread()
    trace = channel_trace.ChannelTrace(live, notes.append, thread.add_system, bare="2026-09-02_102113")
    base = 1_800_000_000.0
    lost, back = _events(("lost", "blackhole", base, base + 70, None, True),
                         ("back", "blackhole", base, base + 190, 190.0, True))
    trace.on_event(lost)
    trace.on_event(back)
    meta = live_sidecar.read(live, "2026-09-02_102113")
    events = json.loads(meta[channel_trace.SIDECAR_KEY])
    assert [e["kind"] for e in events] == ["lost", "back"] and events[0]["stopped_at"] == base
    assert len(notes) == 2 and "пропал" in notes[0] and "снова пишется" in notes[1] and "3 мин" in notes[1]
    rendered = thread.render()
    assert "пропал (" in rendered and "снова пишется" in rendered, "обе строки в нити — дедуп событий не касается"
    assert "⚠️ ⚠️" not in rendered, "знак события — в тексте, рендер второй не добавляет"


def test_the_thread_keeps_two_identical_system_lines_and_the_cloud_cannot_edit_them():
    thread = meeting_thread.Thread()
    text = "⚠️ системный звук (собеседники) пропал в 14:32 — дальше запись без собеседников"
    assert thread.add_system(text, "14:33") and thread.add_system(text, "14:50")
    assert thread.render().count(text) == 2, "два события — две строки (knows не применяется)"
    applied = thread.apply_edits([(text, "системный звук вернулся")])
    assert applied == [] and thread.render().count(text) == 2, "облако системные строки не переписывает"
    # знак system — только в таблице рендера: ingest ответа модели «⚠️ …» строкой system не становится
    assert meeting_thread.WARN not in meeting_thread.KINDS
    thread.ingest("⚠️ якобы системная строка от модели")
    assert not any(l.kind == meeting_thread.SYSTEM and "якобы" in l.text for t in thread.topics for l in t.lines)


def test_flapping_is_capped_in_the_documents_but_complete_in_the_sidecar(tmp_path):
    """До 21 цикла за встречу (замер 14–18.09): в хвост и нить — не больше
    LINES_MAX строк на канал, в сайдкар — все события; порога по длительности
    нет — короткий по отчёту эпизод и есть реальная дыра (критика DS и GLM)."""
    live = tmp_path / "2026-09-02_1021.md"
    notes: list[str] = []
    trace = channel_trace.ChannelTrace(live, notes.append, None, bare="2026-09-02_1021")
    base = 1_800_000_000.0
    for i in range(10):
        trace.on_event(_events(("lost", "blackhole", base + i * 60, base + i * 60 + 5, None, True))[0])
        trace.on_event(_events(("back", "blackhole", base + i * 60, base + i * 60 + 20, 20.0, True))[0])
    events = json.loads(live_sidecar.read(live, "2026-09-02_1021")[channel_trace.SIDECAR_KEY])
    assert len(events) == 20 and len(notes) == 2 * channel_trace.LINES_MAX, "потолок — по эпизодам: пара lost+back на эпизод"
    assert "около 20 с" in notes[1], "короткий эпизод виден, а не свёрнут порогом"
    summary = trace.close()
    assert summary.startswith("📋 запись неполная") and "эпизодов 10" in summary and "и ещё 2" in summary
    assert notes[-1] == summary


def test_the_summary_closes_an_open_episode_with_the_end_of_recording(tmp_path):
    live = tmp_path / "2026-09-02_1021.md"
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_1021")
    base = 1_800_000_000.0
    trace.on_event(_events(("lost", "mic", base, base + 60, None, True))[0])
    assert "до конца записи" in trace.summary()
    trace.on_event(_events(("end", "mic", base, base + 600, 600.0, True))[0])
    s = trace.summary()
    assert "10 мин" in s and "до конца записи" not in s and "без вашего голоса" in s


def test_the_tail_note_does_not_move_the_speech_hash(tmp_path):
    """Строка следа живёт в хвосте «Ко-мышления»: хеш речи производных (№309) от
    неё не двигается, пересборка хвост переносит."""
    tr = transcript.Transcript(tmp_path)              # штамп — текущее время, путь — tr.path
    tr.add("Смету пришлём к пятому, провайдер прежний.", speaker="Собеседник")
    before = transcript.speech_of(tr.path.read_text(encoding="utf-8"))
    tr.note("⚠️ системный звук (собеседники) пропал в 14:32 — дальше запись без собеседников")
    after_text = tr.path.read_text(encoding="utf-8")
    assert transcript.speech_of(after_text) == before
    assert "пропал в 14:32" in after_text and transcript.NOTES_HEAD in after_text


def test_merge_keeps_what_the_meeting_already_wrote(tmp_path):
    """Стоп-дамп демона одной строкой стирал бы `channel_events` (Critical DS и
    GLM): сайдкар с живым писателем пишется только слиянием."""
    live = tmp_path / "2026-09-02_102113.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_102113")
    trace.on_event(_events(("lost", "blackhole", 1.0, 2.0, None, True))[0])
    assert live_sidecar.merge(live, {"speakers": 2, "names": {"Собеседник 1": "Инга"},
                                     "minutes_sha256": "a" * 64, "stamp": "2026-09-02_102113"},
                              bare="2026-09-02_102113")
    meta = json.loads((tmp_path / "2026-09-02_102113.md.live.json").read_text(encoding="utf-8"))
    assert meta["speakers"] == 2 and meta["names"] == {"Собеседник 1": "Инга"} and meta["stamp"] == "2026-09-02_102113"
    assert json.loads(meta[channel_trace.SIDECAR_KEY])[0]["kind"] == "lost", "след встречи пережил стоп"


def test_the_daemon_wires_the_trace_and_closes_it_before_spawning_the_rebuild():
    src = (REPO / "src" / "daemon.py").read_text(encoding="utf-8")
    assert "hub.on_channel = trace.on_event" in src
    fin = src[src.index("hub.end_channel_episodes()"):]
    assert fin.index("trace.close()") < fin.index("live_sidecar.merge(") < fin.index('"rebuild_transcript.py"')
    assert 'json.dumps({"speakers"' not in src, "дамп сайдкара одной строкой снят — только слияние"


def test_two_sidecar_writers_in_one_process_do_not_lose_each_other_s_keys(tmp_path):
    """Стоп-слияние в главном потоке против следа канала из потока сторожа —
    оба read-modify-write; без общего замка второй затирал бы правку первого
    (Critical GLM выходного круга)."""
    import threading
    live = tmp_path / "2026-09-02_102113.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_102113")
    base = 1_800_000_000.0
    stop = threading.Event()
    n = {"events": 0}

    # чередование форсируется швом записи, а не расписанием планировщика (Minor DS
    # и GLM круга 2): первая запись потока событий замирает между «прочитал» и
    # «записал», пока главный поток не попробует слияние; без замка слияние
    # пройдёт в этот зазор и будет затёрто, с замком — дождётся своей очереди
    real_write = live_sidecar.safe_write.write_text
    in_gap, main_tried = threading.Event(), threading.Event()
    writer_thread = {"id": None}

    def hooked(path, text, **kw):
        if threading.get_ident() == writer_thread["id"] and not in_gap.is_set():
            in_gap.set()
            main_tried.wait(1.5)
        return real_write(path, text, **kw)
    monkeypatch_target = live_sidecar.safe_write
    monkeypatch_target.write_text = hooked
    try:
        def writer():
            writer_thread["id"] = threading.get_ident()
            while not stop.is_set():
                trace.on_event(_events(("gap", "blackhole", base + n["events"], base + n["events"] + 1, 1.0, True))[0])
                n["events"] += 1
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        assert in_gap.wait(3), "поток событий не дошёл до записи"
        main_tried.set()                                   # главный идёт в слияние ровно в зазор
        for i in range(30):
            assert live_sidecar.merge(live, {"speakers": i, "stamp": "2026-09-02_102113"}, bare="2026-09-02_102113")
        stop.set()
        t.join(5)
    finally:
        monkeypatch_target.write_text = real_write
    meta = json.loads((tmp_path / "2026-09-02_102113.md.live.json").read_text(encoding="utf-8"))
    assert n["events"] >= 1
    assert meta["speakers"] == 29 and meta["stamp"] == "2026-09-02_102113", "ключи стоп-слияния целы"
    assert len(json.loads(meta[channel_trace.SIDECAR_KEY])) == n["events"], "ни одно событие не затёрто слиянием"


def test_events_after_the_trace_is_closed_are_dropped_not_duplicated(monkeypatch):
    """После end сторож ещё жив до stop() хаба: его «вернулся» дал бы фантомный
    второй эпизод, а строка легла бы после итога (Important GLM 2 / DS 3)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = now - 100
    hub._announce_losses({"blackhole": a.Loss("умер", retriable=True, died=True)})
    hub.end_channel_episodes()
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = time.time()
    hub._announce_back("blackhole", 3.0)                 # рестарт вернулся после закрытия
    hub._last_frame["mic"] = hub._real_frame["mic"] = time.time() - 40
    monkeypatch.setattr(hub, "_restart_guarded", lambda c: None)
    hub._sweep(time.time(), {})                            # и тихий перезапуск соседа
    assert [e.kind for e in got] == [a.CH_LOST, a.CH_END], "после закрытия следа событий нет"
    assert [e.kind for e in hub.channel_log] == [a.CH_LOST, a.CH_END]


def test_the_hole_boundary_is_the_real_frame_not_the_restart_stamp(monkeypatch):
    """Удачный рестарт ставит `_last_frame = now` без единого кадра (анти-шторм);
    граница дыры от него уезжала бы вперёд, а первый кусок тишины выпадал
    (Important DS выходного круга). Наложившиеся окна итог сливает."""
    _quiet(monkeypatch)
    hub = _hub("mic")
    got = []
    hub.on_channel = got.append
    clock = [1_800_000_000.0]
    monkeypatch.setattr(a.time, "time", lambda: clock[0])       # часы под контролем: рестарт ставит time.time()
    t0 = clock[0]
    hub._last_frame["mic"] = hub._real_frame["mic"] = t0        # последний настоящий кадр
    monkeypatch.setattr(hub, "_restart_guarded", lambda c: None)
    clock[0] = t0 + 40
    hub._sweep(clock[0], {})                                    # gap 1: 40 с тишины, рестарт «удался»
    clock[0] = t0 + 75
    hub._sweep(clock[0], {})                                    # анти-шторм пройден, поток всё ещё мёртв
    gaps = [e for e in got if e.kind == a.CH_GAP]
    assert len(gaps) == 2 and all(abs(g.stopped_at - t0) < 1 for g in gaps), "обе дыры отсчитаны от настоящего кадра"
    live = pathlib.Path(tempfile.mkdtemp()) / "2026-09-02_1021.md"
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_1021")
    for g in gaps:
        trace.on_event(g)
    eps = trace.episodes()
    assert len(eps) == 1 and eps[0].revived and abs(eps[0].end - eps[0].start - 75) < 2, "одно окно, не два наложившихся"
    assert "эпизодов 1" in trace.summary()


def test_a_failing_sidecar_is_reported_once_and_the_summary_reflects_the_last_write(tmp_path, monkeypatch, capsys):
    """Отказ записи — одна строка stderr; итог говорит о ПОСЛЕДНЕЙ записи: список
    пишется целиком, и удавшаяся запись после разового отказа значит, что в
    сайдкаре всё (Important DS круга 2). Слова — читателя протокола (GLM)."""
    live = tmp_path / "2026-09-02_1021.md"
    outcome = {"ok": False}
    monkeypatch.setattr(live_sidecar, "remember", lambda *a, **k: outcome["ok"])
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_1021")
    trace.on_event(_events(("lost", "mic", 1.0, 2.0, None, True))[0])
    assert trace.persist_failed and trace.summary().endswith("пометки о пропусках могли сохраниться не полностью")
    trace.on_event(_events(("gap", "blackhole", 5.0, 40.0, 35.0, True))[0])   # второй отказ — строки нет
    outcome["ok"] = True
    trace.on_event(_events(("end", "mic", 1.0, 60.0, 59.0, True))[0])        # удалось — весь список лёг
    err = capsys.readouterr().err
    assert err.count("не пишется") == 1
    assert not trace.persist_failed and "могли сохраниться" not in trace.summary()
    assert "сайдкар" not in trace.summary(), "жаргон кодовой базы в протокол не идёт"


def test_the_trace_owner_ignores_events_after_close(tmp_path):
    """Гейт у владельца, не только у излучателя: второй подписчик или новый
    эмиттер не обойдут закрытие следа молча (критика GLM круга 2)."""
    live = tmp_path / "2026-09-02_1021.md"
    notes: list[str] = []
    trace = channel_trace.ChannelTrace(live, notes.append, None, bare="2026-09-02_1021")
    trace.on_event(_events(("lost", "mic", 1.0, 2.0, None, True))[0])
    assert trace.close().startswith("📋")
    trace.on_event(_events(("back", "mic", 1.0, 90.0, 89.0, True))[0])
    assert len(trace.events) == 1 and notes[-1].startswith("📋"), "после итога событий в документах нет"


def test_a_channel_without_frames_has_no_invented_boundary(tmp_path, monkeypatch):
    """Без настоящих кадров граница неизвестна — ни в строке, ни в итоге не
    подставляется момент крика или штамп старта (Important DS круга 2 ×2)."""
    _quiet(monkeypatch)
    hub = _hub("blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    hub._last_frame["blackhole"] = now - 40                 # штамп старта, кадров не было
    monkeypatch.setattr(hub, "_restart_guarded", lambda c: None)
    hub._sweep(now, {})
    assert got[-1].kind == a.CH_GAP and got[-1].stopped_at is None and abs(got[-1].silent_s - 40) < 1
    live = tmp_path / "2026-09-02_1021.md"
    trace = channel_trace.ChannelTrace(live, None, None, bare="2026-09-02_1021")
    trace.on_event(_events(("lost", "blackhole", None, 100.0, None, True))[0])
    trace.on_event(_events(("end", "blackhole", None, 600.0, None, True))[0])
    s = trace.summary()
    assert "время неизвестно" in s and "без учёта эпизодов с неизвестной границей" in s
    assert trace.episodes()[0].start is None


def test_producers_are_silent_after_the_trace_is_closed(monkeypatch):
    """Гейт — на производителе: после закрытия `_announce_back` не шлёт «снова
    пишется» в статус и не трогает реестр, `_announce_losses` не кричит
    (Important DS круга 2 / Minor GLM)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    said: list[str] = []
    hub.on_status = said.append
    now = time.time()
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = now - 100
    hub._announce_losses({"blackhole": a.Loss("умер", retriable=True, died=True)})
    hub.end_channel_episodes()
    said.clear()
    assert hub._announce_back("blackhole", 3.0) == "" and said == [] and "blackhole" in hub._lost
    hub._announce_losses({"mic": a.Loss("умер", retriable=False, died=True)})
    assert "mic" not in hub._lost and said == []


def test_episode_contract_is_named_not_positional():
    ep = channel_trace.Episode("mic", 1.0, None, False)
    assert ep.label == "mic" and ep.end is None and ep.revived is False
    closed_by_stop = channel_trace.Episode("mic", 1.0, 5.0, False)
    revived = channel_trace.Episode("mic", 1.0, 5.0, True)
    assert not closed_by_stop.revived and revived.revived
