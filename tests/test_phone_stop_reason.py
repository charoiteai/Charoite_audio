"""№200: причина остановки записи на телефоне доезжает до Mac и становится
следом записи — событием сайдкара, строкой хвоста и оговоркой производных.

Контракт с компаньоном: рядом с аудио лежит манифест `<файл>.json`
(`Recorder.StopRecord`): kind stop|rotate, reason из закрытого списка, at
(ISO 8601), seconds, series, finalized_ok. Здесь — сторона Mac:
`channel_trace.phone_event` конвертирует, слова и итог — там же; импорт
пишет сайдкар до графа и хвоста, везёт манифест в done/ парой с аудио и
убирает его с аудио же.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import channel_trace  # noqa: E402
import import_meeting as im  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_source  # noqa: E402
import transcript  # noqa: E402

AT = "2026-09-07T16:02:37Z"
HM = dt.datetime.fromtimestamp(channel_trace._epoch(AT)).strftime("%H:%M")


def _manifest(kind="stop", reason="no_stop", **extra) -> dict:
    return {"kind": kind, "reason": reason, "at": AT, "version": 1, **extra}


def test_ручной_стоп_и_мусор_событий_не_дают():
    """Critical DS и GLM входного круга: `kind: end` делал каждую нормально
    остановленную встречу «неполной». `user` — не факт о неполноте, мусор —
    не факт вовсе."""
    assert channel_trace.phone_event(_manifest(reason="user")) is None
    assert channel_trace.phone_event(_manifest(kind="rotate", reason="user")) is None
    assert channel_trace.phone_event("junk") is None
    assert channel_trace.phone_event({"kind": "stop"}) is None
    assert channel_trace.phone_event({"kind": "pause", "reason": "stalled", "at": AT}) is None
    assert channel_trace.summary_of([]) is None


def test_невольная_остановка_входит_в_итог_терминальная_и_ротация_без_утверждения_будущего():
    """Critical DS выходного круга: манифест ротации обещает продолжение в момент
    закрытия файла, а состоится ли оно (старт после звонка мог не подняться),
    телефон не знает — последний сегмент серии оставался бы «полным». Итог —
    для обеих невольных остановок; формулировка ротации будущего не утверждает."""
    cut = channel_trace.phone_event(_manifest("stop", "no_stop"))
    assert cut == {"label": "phone", "kind": "stopped", "at": channel_trace._epoch(AT),
                   "reason": "no_stop", "terminal": True}
    note = channel_trace.summary_of([cut])
    assert note.startswith(channel_trace.SUMMARY_MARK)
    assert f"запись на телефоне оборвана {HM} (стоп не зафиксирован" in note
    assert "phone" not in note, "в русской строке — имя канала из NAMES, не метка"

    rot = channel_trace.phone_event(_manifest("rotate", "call_no_resume", seconds=1200.5,
                                              series="iphone_2026-09-07_190237", finalized_ok=False))
    assert rot["terminal"] is False and rot["series"] == "iphone_2026-09-07_190237"
    rnote = channel_trace.summary_of([rot])
    assert rnote == (f"{channel_trace.SUMMARY_MARK}: запись на телефоне прервана {HM} (микрофон не вернулся "
                     "после звонка, файл не финализирован) — продолжение, если запись возобновилась, "
                     "следующим файлом")
    assert "закрыта" not in rnote and "продолжена" not in rnote, "будущее не утверждается"
    assert channel_trace.render_phone(rot) == "⏹ " + rnote[len(channel_trace.SUMMARY_MARK) + 2:]
    # эпизодом отсутствия канала событие телефона не является
    assert channel_trace.episodes_of([cut, rot]) == []


def test_строки_телефона_узнаёт_классификатор_а_речь_модели_нет():
    """Урок круга 3 по №317: классификатор строится из тех же фраз, что рендер."""
    for m in (_manifest("stop", "no_stop"), _manifest("rotate", "media_reset"),
              _manifest("stop", "encode_error", finalized_ok=False), _manifest("stop", "stalled")):
        ev = channel_trace.phone_event(m)
        assert channel_trace.is_trace_line(channel_trace.render_phone(ev)), m
        assert channel_trace.is_trace_line("> 19:02 " + channel_trace.render_phone(ev)), m
        assert channel_trace.is_trace_line(channel_trace.summary_line([ev])), m
    assert not channel_trace.is_trace_line("⏹ Подрядчик прервана сделка 19:02 (устно)")
    assert not channel_trace.is_trace_line("запись на телефоне оборвана 19:02 — сказал Иван")


def test_неизвестная_причина_и_битое_время_не_роняют_и_не_выдумывают():
    ev = channel_trace.phone_event({"kind": "stop", "reason": "future_reason", "at": "вчера"})
    assert ev["at"] is None and ev["reason"] == "future_reason"
    assert channel_trace.render_phone(ev) == "⏹ запись на телефоне оборвана время неизвестно (future_reason)"
    assert channel_trace.summary_line([ev]) == channel_trace.summary_of([ev]), "без момента — без штампа"
    assert channel_trace._epoch(1700000000) == 1700000000.0
    assert channel_trace._epoch(None) is None


def test_итог_из_событий_канала_и_телефона_в_одной_строке():
    events = [{"label": "blackhole", "kind": "lost", "at": 1000.0, "stopped_at": 940.0, "died": True},
              {"label": "blackhole", "kind": "back", "at": 1240.0, "stopped_at": 940.0, "silent_s": 300.0},
              channel_trace.phone_event(_manifest("stop", "media_reset"))]
    note = channel_trace.summary_of(events)
    assert "без собеседников" in note and "запись на телефоне оборвана" in note
    assert note.count(channel_trace.SUMMARY_MARK) == 1


def test_импорт_пишет_событие_в_сайдкар_и_хвост_до_графа(tmp_path, monkeypatch):
    """Important DS входного круга: паспорт минуток хеширует речь вместе с
    оговоркой — событие должно лечь ДО graph_updater и retro_fill, иначе первая
    генерация без оговорки и вторая с ней. Ручной стоп — ни сайдкара, ни строки."""
    src = tmp_path / "iphone_2026-09-07_190237.caf"
    src.write_bytes(b"\0" * 4096)
    im.phone_manifest(src).write_text(json.dumps(_manifest("stop", "no_stop", seconds=1237.0)),
                                      encoding="utf-8")
    tpath = tmp_path / "2026-09-07_1902_тема.md"
    body = "# Встреча 2026-09-07_1902\n\n[19:02:37] Иван: начнём\n[19:05:10] Пётр: да\n"
    tpath.write_text(body, encoding="utf-8")

    ev = im.note_phone_stop(src, tpath)
    assert ev["reason"] == "no_stop" and ev["seconds"] == 1237.0
    stored = channel_trace.events_of(tpath)
    assert stored == [ev], "сайдкар — JSON-строкой, читатель events_of её понимает"
    text = tpath.read_text(encoding="utf-8")
    # в документ идёт СТРОКА ИТОГА — её узнают note_in_tail, пересборка и meeting_source
    # (Important DS выходного круга); строка события — только в лог
    assert transcript.NOTES_HEAD in text and channel_trace.SUMMARY_MARK in text
    assert "запись на телефоне оборвана" in text and "⏹" not in text
    assert meeting_source.note_in_tail(text) and meeting_source.note_in_tail(text).startswith(channel_trace.SUMMARY_MARK)
    assert transcript.speech_of(text) == transcript.speech_of(body), "хвост не двигает речь"
    # производная получает оговорку тем же путём, что №317
    source = meeting_source.of(tpath, text)
    assert source.recording_note and source.recording_note.startswith(channel_trace.SUMMARY_MARK)
    # второй прогон — без дубля строки и без второго события
    im.note_phone_stop(src, tpath)
    assert tpath.read_text(encoding="utf-8").count(channel_trace.SUMMARY_MARK) == 1
    assert channel_trace.events_of(tpath) == [ev]
    # события канала, уже лежащие в сайдкаре, не затираются — слияние
    other = tmp_path / "with_channels.caf"
    other.write_bytes(b"\0" * 4096)
    im.phone_manifest(other).write_text(json.dumps(_manifest("rotate", "stalled")), encoding="utf-8")
    t3 = tmp_path / "2026-09-07_2100.md"
    t3.write_text(body, encoding="utf-8")
    ch = {"label": "blackhole", "kind": "gap", "at": 1240.0, "stopped_at": 940.0, "silent_s": 300.0}
    assert live_sidecar.remember(t3, channel_trace.SIDECAR_KEY, json.dumps([ch]))
    im.note_phone_stop(other, t3)
    kinds = [e["kind"] for e in channel_trace.events_of(t3)]
    assert kinds == ["gap", "stopped"]
    assert "без собеседников" in t3.read_text(encoding="utf-8") and "прервана" in t3.read_text(encoding="utf-8")

    manual = tmp_path / "manual.caf"
    manual.write_bytes(b"\0" * 4096)
    im.phone_manifest(manual).write_text(json.dumps(_manifest("stop", "user")), encoding="utf-8")
    t2 = tmp_path / "2026-09-07_2000.md"
    t2.write_text(body, encoding="utf-8")
    assert im.note_phone_stop(manual, t2) is None
    assert channel_trace.events_of(t2) == [] and transcript.NOTES_HEAD not in t2.read_text(encoding="utf-8")
    assert im.note_phone_stop(tmp_path / "nomanifest.caf", t2) is None


def test_событие_ложится_до_графа_и_до_ветки_пустой_записи():
    """Структурно: вызов стоит после финального tpath и до graph_updater;
    ветка EXIT_NO_SPEECH — ниже (Important DS: огрызок с причиной — и есть факт).
    Повтор импорта пишет событие в уже существующую стенограмму (Minor GLM), а
    голосовая заметка хотя бы печатает причину (Important DS выходного круга)."""
    text = (ROOT / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    main = text[text.index("def main("):]
    call = main.index("note_phone_stop(src, tpath)")
    assert main.index("tpath = titled") < call < main.index('"graph_updater.py"')
    assert call < main.index("EXIT_NO_SPEECH:")
    repeat = main.index("note_phone_stop(src, already)")
    assert repeat < main.index('"repeat": True')
    note = main.index("phone_event_for(src)")
    assert note < main.index("import_voice_note(src, diary=")
    assert "reconcile_manifests(folder)" in main and "sweep_manifests" not in text


def test_скан_везёт_манифест_в_done_парой_под_уникальным_именем(tmp_path, monkeypatch):
    """Important GLM и DS входного круга: манифест, оставшийся в корне, достался
    бы следующему файлу с тем же именем; пара переезжает под именем аудио ПОСЛЕ
    уникализации."""
    src = tmp_path / "Recording.txt"
    src.write_text("y" * 300, encoding="utf-8")
    im.phone_manifest(src).write_text(json.dumps(_manifest()), encoding="utf-8")
    (tmp_path / "done").mkdir()
    (tmp_path / "done" / "Recording.txt").write_text("старая копия", encoding="utf-8")

    def fake_child(cmd, **kw):
        result = pathlib.Path(cmd[cmd.index("--result-json") + 1])
        im._write_json(result, {"kind": "meeting", "stamp": "2026-09-05_1200",
                                "transcript": "/t/2026-09-05_1200.md", "archive_source": None})

        class Ok:
            returncode = 0
            stdout = "готово\n"
            stderr = ""
        return Ok()

    monkeypatch.setattr(im, "run_child", fake_child)
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {"import_keep_days": "1.5"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", "--scan", "--", str(tmp_path)])
    before = os.umask(0)
    os.umask(before)
    try:
        im.main()
    finally:
        os.umask(before)
    moved = tmp_path / "done" / "Recording-1.txt"
    assert moved.exists() and im.phone_manifest(moved).exists()
    assert not im.phone_manifest(src).exists(), "манифест в корне — чужому файлу"
    assert not im.phone_manifest(tmp_path / "done" / "Recording.txt").exists()


def test_ретеншн_удаляет_манифест_с_аудио_а_сироты_убирает_сверка(tmp_path):
    done = tmp_path / "done"
    done.mkdir()
    now = time.time()
    f = done / "a.m4a"
    f.write_bytes(b"\0" * 10)
    im._write_json(im.imported_sidecar(f), {"imported_at": now - 3 * 86400, "delete_after": now - 60,
                                            "stamp": "s", "archive_source": None})
    im.phone_manifest(f).write_text("{}", encoding="utf-8")
    removed = im.prune_done(tmp_path, 2, now=now)
    assert f in removed and not im.phone_manifest(f).exists(), "манифест уходит с аудио"
    assert not im.imported_sidecar(im.phone_manifest(f)).exists(), "манифест — не копия со своим сроком"

    orphan = done / "gone.caf.json"
    orphan.write_text("{}", encoding="utf-8")
    os.utime(orphan, (now - 3600, now - 3600))
    young_orphan = done / "coming.caf.json"
    young_orphan.write_text("{}", encoding="utf-8")
    assert im.prune_done(tmp_path, 2, now=now) == [], "ретеншн о сиротах манифеста не знает"
    gone = im.reconcile_manifests(tmp_path, now=now)
    assert orphan in gone and not orphan.exists(), "манифест без аудио в done/ — мусор"
    assert young_orphan.exists(), "моложе минуты — второй уборщик мог не успеть"


def test_сверка_пар_воссоединяет_опоздавший_манифест_и_дописывает_событие(tmp_path):
    """Important GLM выходного круга: порядок СКАЧИВАНИЯ iCloud не гарантирован —
    манифест, приехавший после того, как аудио уехало в done/, оставался в корне и
    через час выметался. Теперь он догоняет аудио по полю `source` сайдкара
    импорта (имя ДО уникализации), а событие догоняет стенограмму."""
    done = tmp_path / "done"
    done.mkdir()
    now = time.time()
    body = "# Встреча 2026-09-07_1902\n\n[19:02:37] Иван: начнём\n"
    tpath = tmp_path / "2026-09-07_1902_тема.md"
    tpath.write_text(body, encoding="utf-8")
    moved = done / "iphone_2026-09-07_190237-1.caf"     # уехало под уникальным именем
    moved.write_bytes(b"\0" * 4096)
    im._write_json(im.imported_sidecar(moved), {"source": "iphone_2026-09-07_190237.caf",
                                                "transcript": str(tpath), "imported_at": now,
                                                "delete_after": now + 86400})
    late = tmp_path / "iphone_2026-09-07_190237.caf.json"
    late.write_text(json.dumps(_manifest("stop", "call_no_resume")), encoding="utf-8")
    touched = im.reconcile_manifests(tmp_path, now=now)
    assert not late.exists() and im.phone_manifest(moved).exists(), "пара воссоединена в done/"
    assert im.phone_manifest(moved) in touched
    events = channel_trace.events_of(tpath)
    assert [e["reason"] for e in events] == ["call_no_resume"], "событие догнало стенограмму"
    assert channel_trace.SUMMARY_MARK in tpath.read_text(encoding="utf-8")
    # повторная сверка ничего не делает
    assert im.reconcile_manifests(tmp_path, now=now) == []
    assert channel_trace.events_of(tpath) == events

    # манифест без аудио и без владельца в done/: ждёт час, потом уходит
    waiting = tmp_path / "late.caf.json"
    waiting.write_text("{}", encoding="utf-8")
    hidden = tmp_path / ".x.caf.imported.json"
    hidden.write_text("{}", encoding="utf-8")
    paired = tmp_path / "here.caf"
    paired.write_bytes(b"\0")
    im.phone_manifest(paired).write_text("{}", encoding="utf-8")
    assert im.reconcile_manifests(tmp_path, now=now) == [], "свежий манифест без аудио — ещё едет"
    assert im.reconcile_manifests(tmp_path, now=now + 2 * 3600) == [waiting]
    assert hidden.exists() and im.phone_manifest(paired).exists()
    assert im.manifest_owner(hidden) is None and im.manifest_owner(tmp_path / "notes.json") is None
    assert im.manifest_owner(waiting) == tmp_path / "late.caf"
    # стенограмма исчезла или путь протух — сверка молчит, не падает
    (done / "iphone_x.caf").write_bytes(b"\0")
    im._write_json(im.imported_sidecar(done / "iphone_x.caf"), {"source": "iphone_x.caf",
                                                                 "transcript": str(tmp_path / "нет.md")})
    im.phone_manifest(done / "iphone_x.caf").write_text(json.dumps(_manifest()), encoding="utf-8")
    assert im.reconcile_manifests(tmp_path, now=now) == []


def test_недописанные_part_компаньона_убираются_свипом(tmp_path):
    """Minor DS и GLM выходного круга: `.part` телефона не скрытые."""
    now = time.time()
    stale_audio = tmp_path / "iphone_a.caf.part"
    stale_audio.write_bytes(b"\0")
    stale_manifest = tmp_path / "iphone_a.caf.json.part"
    stale_manifest.write_text("{", encoding="utf-8")
    assert im.sweep_temporaries(tmp_path, now=now) == [], "свежая публикация — не мусор"
    removed = im.sweep_temporaries(tmp_path, now=now + 2 * 3600)
    assert set(removed) == {stale_audio, stale_manifest}


def test_хвост_пишет_один_писатель_с_гейтом_expect_а_сайдкар_источник(tmp_path, monkeypatch):
    """Important DS и GLM круга 2: сверка дописывала хвост чтением→записью без гейта,
    а пересборка пишет тот же файл из другого процесса — её финал откатился бы.
    Теперь строку итога пишет один `channel_trace.sync_tail` поверх
    `safe_write.rewrite_file`; пересборка зовёт то же правило `tail_with_summary`."""
    import rebuild_transcript
    import safe_write
    body = "# Встреча 2026-09-07_1902\n\n[19:02:37] Иван: начнём\n"
    tpath = tmp_path / "2026-09-07_1902.md"
    tpath.write_text(body, encoding="utf-8")
    src = tmp_path / "iphone_a.caf"
    src.write_bytes(b"\0" * 4096)
    im.phone_manifest(src).write_text(json.dumps(_manifest("stop", "stalled")), encoding="utf-8")

    # чужая запись между чтением и записью — гейт не даёт затереть её
    real_write = safe_write.write_text
    calls = {"n": 0}

    def racing_write(path, text, *args, **kwargs):
        if pathlib.Path(path) == tpath:                    # сайдкар пишется тем же хелпером — не он
            calls["n"] += 1
            if calls["n"] == 1:
                real_write(path, body + "[19:10:00] Пётр: правка пересборки\n")   # чужой финал лёг раньше нас
        return real_write(path, text, *args, **kwargs)

    monkeypatch.setattr(safe_write, "write_text", racing_write)
    ev = im.note_phone_stop(src, tpath)
    assert ev is not None, "сайдкар — источник, он записан"
    final = tpath.read_text(encoding="utf-8")
    assert "правка пересборки" in final, "чужой финал не откатился"
    assert final.count(channel_trace.SUMMARY_MARK) == 1, "вторая попытка дописала итог поверх свежего текста"
    monkeypatch.setattr(safe_write, "write_text", real_write)

    # пересборка — то же правило: строка уже стоит → без дубля; итог по большему списку — ниже
    assert rebuild_transcript._with_recording_summary(tpath, final) == final
    text2, n = channel_trace.tail_with_summary(final, channel_trace.events_of(tpath))
    assert (text2, n) == (final, 0)
    more = channel_trace.events_of(tpath) + [{"label": "blackhole", "kind": "gap", "at": 1240.0,
                                              "stopped_at": 940.0, "silent_s": 300.0}]
    text3, n3 = channel_trace.tail_with_summary(final, more)
    assert n3 == 1 and text3.count(channel_trace.SUMMARY_MARK) == 2
    assert meeting_source.note_in_tail(text3) == channel_trace.summary_of(more), "читатель берёт последний итог"
    # структурно: у строки итога в проекте один писатель — tail_with_summary
    for name in ("scripts/import_meeting.py", "src/rebuild_transcript.py"):
        code = (ROOT / name).read_text(encoding="utf-8")
        assert "append_note(" not in code, name

    # неоднозначный сайдкар: ничего не записано → None, лог не врёт «догнала»
    ambiguous = tmp_path / "amb"
    ambiguous.mkdir()
    a1 = ambiguous / "2026-09-07_1902.md"
    a2 = ambiguous / "2026-09-07_1902_тема.md"
    a1.write_text(body, encoding="utf-8")
    a2.write_text(body, encoding="utf-8")
    live_sidecar.remember(a1, "x", "1")
    live_sidecar.remember(a2, "x", "1")
    if live_sidecar.sidecar_for(a1) is None:
        assert im.note_phone_stop(src, a1) is None
        assert channel_trace.SUMMARY_MARK not in a1.read_text(encoding="utf-8")


def test_сверка_не_приклеивает_манифест_к_одноимённой_чужой_записи_и_не_повторяет_ручной_стоп(tmp_path, capsys):
    """Minor DS круга 2: прямое имя в done/ — только по полю `source`; Minor GLM:
    ручной стоп не переобсуждается каждый скан."""
    done = tmp_path / "done"
    done.mkdir()
    now = time.time()
    foreign = done / "Recording.caf"                 # одноимённая чужая запись, приехавшая раньше
    foreign.write_bytes(b"\0" * 10)
    im._write_json(im.imported_sidecar(foreign), {"source": "Other.caf", "transcript": str(tmp_path / "o.md")})
    late = tmp_path / "Recording.caf.json"
    late.write_text(json.dumps(_manifest()), encoding="utf-8")
    assert im.reconcile_manifests(tmp_path, now=now) == []
    assert late.exists() and not im.phone_manifest(foreign).exists(), "имя совпало, источник — нет"

    manual = done / "manual.caf"
    manual.write_bytes(b"\0" * 10)
    t = tmp_path / "m.md"
    t.write_text("# Встреча\n", encoding="utf-8")
    im._write_json(im.imported_sidecar(manual), {"source": "manual.caf", "transcript": str(t)})
    im.phone_manifest(manual).write_text(json.dumps(_manifest("stop", "user")), encoding="utf-8")
    capsys.readouterr()
    im.reconcile_manifests(tmp_path, now=now)
    im.reconcile_manifests(tmp_path, now=now)
    assert "вручную" not in capsys.readouterr().out, "решённое не переобсуждается"


def test_словарь_причин_совпадает_с_enum_компаньона():
    """Причина — значение из Swift-enum; текст — в channel_trace. Новая причина
    без текста показалась бы человеку сырым идентификатором."""
    swift = (ROOT / "app-ios" / "Sources" / "CharoiteiOS" / "Recorder+StopRecord.swift").read_text(encoding="utf-8")
    enum = swift[swift.index("enum StopReason"):swift.index("func text(terminal: Bool)")]
    import re
    raws = set()
    for line in enum.splitlines():
        m = re.match(r"\s*case (\w+)(?: = \"([a-z_]+)\")?", line)
        if m:
            raws.add(m.group(2) or m.group(1))
    assert raws == set(channel_trace.PHONE_REASONS) | {"user"}, raws
    assert 'static func sidecar(for audio: URL) -> URL' in (
        ROOT / "app-ios" / "Sources" / "CharoiteiOS" / "Inbox.swift").read_text(encoding="utf-8")
    assert f'audio.appendingPathExtension("{channel_trace.MANIFEST_SUFFIX[1:]}")' in (
        ROOT / "app-ios" / "Sources" / "CharoiteiOS" / "Inbox.swift").read_text(encoding="utf-8")
