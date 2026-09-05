"""Импорт встреч: парсер vtt/srt и сборка стенограммы конвейера."""
import os
import pathlib
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_meeting as im  # noqa: E402
from import_meeting import (  # noqa: E402
    WAV_SETTLE_SECONDS,
    clean_date,
    clean_time,
    parse_subs,
    postponed_files,
    scan_candidates,
    subs_to_transcript,
    wav_complete,
)

VTT = """WEBVTT

00:00:03.000 --> 00:00:06.000
<v Maria Sokolova>Начнём с провайдера.

00:00:07.000 --> 00:00:12.000
<v Igor>FastPay дешевле, но онбординг шесть недель.

00:14:02.000 --> 00:14:04.000
Igor: Тогда берём YuPay.
"""

SRT = """1
00:00:03,000 --> 00:00:06,000
Мария: Начнём с провайдера.

2
00:00:07,500 --> 00:00:12,000
Просто реплика без спикера.
"""


def _wav(declared_data: int, actual_data: int) -> bytes:
    """Минимальный канонический WAV: заголовок объявляет одно, файл содержит другое."""
    return (
        b"RIFF"
        + struct.pack("<I", 36 + declared_data)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", declared_data)
        + bytes(actual_data)
    )


def test_incomplete_wav_is_not_ready(tmp_path):
    wav = tmp_path / "android_meeting.wav"
    wav.write_bytes(_wav(declared_data=32_000, actual_data=8_000))

    assert not wav_complete(wav)
    assert scan_candidates(tmp_path) == []


def test_complete_wav_becomes_scan_candidate(tmp_path):
    wav = tmp_path / "android_meeting.wav"
    wav.write_bytes(_wav(declared_data=32_000, actual_data=32_000))

    assert wav_complete(wav)
    assert scan_candidates(tmp_path) == [wav]


def test_scan_ignores_invalid_wav_and_supported_directories(tmp_path):
    (tmp_path / "broken.wav").write_bytes(b"not a wave")
    (tmp_path / "folder.wav").mkdir()
    note = tmp_path / "meeting.txt"
    note.write_text("готовая стенограмма", encoding="utf-8")

    assert scan_candidates(tmp_path) == [note]


def test_vtt_speakers_and_times():
    entries = parse_subs(VTT)
    assert ("00:00", "Maria Sokolova", "Начнём с провайдера.") in entries
    assert any(sp == "Igor" and "YuPay" in txt for _, sp, txt in entries)
    assert entries[-1][0] == "00:14", "таймкод часа:минуты из блока"


def test_srt_plain_lines_kept():
    entries = parse_subs(SRT)
    assert ("00:00", "Мария", "Начнём с провайдера.") in entries
    assert any(sp == "" and "без спикера" in txt for _, sp, txt in entries)


def test_transcript_format_matches_pipeline():
    body = subs_to_transcript(parse_subs(VTT), "2026-07-15_1400", "zoom.vtt")
    assert body.startswith("# Встреча 2026-07-15_1400 — импорт zoom.vtt")
    assert "**Maria Sokolova** [00:00]:" in body
    assert "**Igor** [00:14]:" in body


def test_streaming_header_does_not_freeze_file_forever(tmp_path):
    """Заголовок без честного размера — не повод потерять запись навсегда.

    Потоковые писатели (ffmpeg в pipe, часть диктофонов) оставляют в RIFF
    либо ноль, либо 0xFFFFFFFF. Данные при этом целы, и до проверки такие
    файлы импортировались годами. Судить по такому заголовку нельзя —
    значит и запирать файл в папке импорта на веки нельзя тоже.
    """
    streaming = _wav(declared_data=32_000, actual_data=32_000)
    unknown = tmp_path / "unknown_size.wav"
    unknown.write_bytes(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + streaming[8:])
    zeroed = tmp_path / "zero_size.wav"
    zeroed.write_bytes(b"RIFF" + struct.pack("<I", 0) + streaming[8:])

    # ...но и не в секунду появления: пока писатель ещё пишет, импорт
    # забрал бы половину записи. Готов — когда РАЗМЕР не менялся
    # WAV_SETTLE_SECONDS между сканами (сайдкар .<имя>.import-seen).
    assert not wav_complete(unknown) and not wav_complete(zeroed)
    assert sorted(postponed_files(tmp_path)) == [unknown, zeroed]
    # mtime никакой роли не играет: ни touch от синка, ни часы в будущем
    future = time.time() + 600
    os.utime(unknown, (future, future))
    _age_markers(tmp_path, WAV_SETTLE_SECONDS + 1)
    assert wav_complete(unknown)
    assert wav_complete(zeroed)
    assert sorted(scan_candidates(tmp_path)) == [unknown, zeroed]
    # прямой импорт выбранного руками файла — без ожидания
    fresh = tmp_path / "fresh.wav"
    fresh.write_bytes(b"RIFF" + struct.pack("<I", 0) + streaming[8:])
    assert wav_complete(fresh, settle=False)


def _age_markers(folder, seconds: float) -> None:
    """Сдвинуть время первого наблюдения в сайдкарах в прошлое."""
    for m in folder.glob(".*.import-seen"):
        size, seen = m.read_text(encoding="ascii").split()
        m.write_text(f"{size} {float(seen) - seconds:.0f}\n", encoding="ascii")


def test_growing_unknown_size_wav_resets_the_clock(tmp_path):
    """Размер вырос между сканами — писатель жив, отсчёт заново; старый
    mtime, сохранённый провайдером, этого не скроет (круг-1, Codex)."""
    streaming = _wav(declared_data=32_000, actual_data=32_000)
    f = tmp_path / "grow.wav"
    f.write_bytes(b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + streaming[8:])
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert not wav_complete(f)
    _age_markers(tmp_path, WAV_SETTLE_SECONDS + 1)
    with f.open("ab") as fh:
        fh.write(b"\0" * 4000)
    os.utime(f, (old, old))
    assert not wav_complete(f), "рост размера обязан сбросить отсчёт"
    _age_markers(tmp_path, WAV_SETTLE_SECONDS + 1)
    assert wav_complete(f)


def test_scan_refuses_symlinked_done_folder(tmp_path):
    """done/ как симлинк увёл бы импортированные файлы в чужую папку, а
    битая ссылка роняла скан на mkdir (круг-1, Codex)."""
    import subprocess as sp
    folder = tmp_path / "import"
    folder.mkdir()
    (folder / "done").symlink_to(tmp_path / "elsewhere")
    r = sp.run([sys.executable, str(ROOT / "scripts" / "import_meeting.py"),
                "--scan", "--", str(folder)], capture_output=True, text=True)
    assert r.returncode != 0
    assert "обычный каталог" in (r.stderr + r.stdout)


def test_double_dash_is_honoured_by_the_parser():
    """Не только текст Swift-файла, но и сам argparse: путь с дефиса после
    `--` — позиционный аргумент (круг-1, Sonnet)."""
    from import_meeting import build_parser
    ns = build_parser().parse_args(["--scan", "--", "-x"])
    assert ns.scan is True and ns.file == "-x"


def test_symlink_in_import_folder_is_skipped(tmp_path):
    """Симлинк в папке импорта втягивал бы произвольный файл в граф и в
    LLM-конвейер: is_file() разыменовывает ссылку (аудит 16.08, п.3)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("чужой файл", encoding="utf-8")
    folder = tmp_path / "import"
    folder.mkdir()
    (folder / "link.txt").symlink_to(secret)
    real_wav = folder / "real.wav"
    real_wav.write_bytes(_wav(declared_data=32_000, actual_data=32_000))
    (folder / "link.wav").symlink_to(real_wav)

    assert scan_candidates(folder) == [real_wav]
    assert postponed_files(folder) == []


def test_app_passes_folder_after_double_dash():
    """Путь папки выбирает человек: имя с дефиса argparse прочёл бы как
    флаг (аудит 16.08, п.5) — приложение ставит `--` перед путём."""
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services"
             / "ImportService.swift").read_text(encoding="utf-8")
    # --scan (с необязательным --settle-all между) … "--", папка — в обоих запусках
    assert '"--scan"] + (settleAll ? ["--settle-all"] : []) + ["--", folder.path]' in swift
    assert '"--prune", "--", folder.path' in swift


def test_scan_reports_files_it_postponed(tmp_path):
    """Молчание про отложенный файл человек читает как «Чароит меня потерял»."""
    growing = tmp_path / "android_meeting.wav"
    growing.write_bytes(_wav(declared_data=32_000, actual_data=8_000))

    postponed = postponed_files(tmp_path)

    assert postponed == [growing]


# --- время и дата встречи из того, что ввёл человек -------------------------
#
# Справка говорила «ЧЧММ», и значение уходило в имя файла как есть. Человек
# пишет время привычно — `--time 08:44`, — и на диске появлялась стенограмма
# `2026-08-03_08:44.md`: штамп уже не четырёхзначный, а двоеточие в имени
# ломает и разбор имени, и инструменты, которые с этим файлом работают.

def test_time_with_colon_becomes_a_stamp():
    assert clean_time("08:44") == "0844"


def test_time_without_leading_zero_survives():
    assert clean_time("8:44") == "0844"


def test_plain_hhmm_passes_through():
    assert clean_time("0844") == "0844"


def test_stray_punctuation_does_not_make_the_time_wrong():
    # «844:» — это те же 08:44 с промахом по клавише, а не ошибка ввода
    assert clean_time("844:") == "0844"


def test_impossible_time_is_refused():
    import pytest
    for bad in ("2599", "0899", "штука", "123456", ""):
        with pytest.raises(SystemExit):
            clean_time(bad)


def test_date_separator_is_up_to_the_human():
    assert clean_date("2026-08-03") == "2026-08-03"
    assert clean_date("2026.08.03") == "2026-08-03"
    assert clean_date("20260803") == "2026-08-03"


def test_impossible_date_is_refused():
    import pytest
    for bad in ("2026-13-03", "2026-02-31", "03.08.26", "вчера"):
        with pytest.raises(SystemExit):
            clean_date(bad)


def test_повтор_импорта_та_же_запись_а_не_та_же_минута(tmp_path):
    """Повтор — та же запись, и код 0; соседка в той же минуте — не повтор.

    Скан переносит файл в done/ только при нулевом коде: выход строкой
    (= код 1) оставлял файл в папке импорта навсегда — три записи с
    телефона молотились каждые две минуты. Проверка «та же минута = повтор»
    глотала вторую запись той же минуты (карточка №41), а проверка только
    по голому `<stamp>.md` не видела titled-встреч. Решение — import_stamp,
    по шапке «— импорт <файл>»; каталог — временный, реальные данные
    тест не трогает.
    """
    import import_meeting as im

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    minute = "2026-08-05_1334"
    (tdir / f"{minute}_Тема.md").write_text(
        f"# Встреча {minute} — Тема — импорт запись.m4a\n\nтекст\n", encoding="utf-8")
    # та же запись → повтор (titled-файл виден)
    stamp, already = im.import_stamp(tdir, minute, "запись.m4a", "17")
    assert already is not None and already.name == f"{minute}_Тема.md"
    # другая запись той же минуты → посекундный штамп, не повтор
    stamp, already = im.import_stamp(tdir, minute, "другая.m4a", "17")
    assert (stamp, already) == (f"{minute}17", None)
    (tdir / f"{minute}17_Другая.md").write_text(
        f"# Встреча {minute}17 — Другая — импорт другая.m4a\n", encoding="utf-8")
    assert im.import_stamp(tdir, minute, "другая.m4a", "17")[1].name == f"{minute}17_Другая.md"
    # третья запись с теми же секундами (время от человека → «00») — суффикс
    (tdir / f"{minute}00_Третья.md").write_text(
        f"# Встреча {minute}00 — Третья — импорт третья.m4a\n", encoding="utf-8")
    assert im.import_stamp(tdir, minute, "четвёртая.m4a", "00")[0] == f"{minute}00-1"
    # свободная минута — минутный штамп, как всегда
    assert im.import_stamp(tdir, "2026-08-05_1400", "x.m4a", "05") == ("2026-08-05_1400", None)

def test_source_goes_to_the_folder_of_its_own_meeting(tmp_path):
    """Две встречи в день: исходник второй ложился в папку первой (глоб по
    дате брал первую папку дня), а при занятом имени молча не копировался
    (аудит DeepSeek 16.08)."""
    from import_meeting import archive_folder_for

    graph = tmp_path / "vault" / "Работа"
    first = graph / "Встречи-архив" / "2026-08-03 11-30 — Планёрка"
    second = graph / "Встречи-архив" / "2026-08-03 14-00 — Ретро"
    other_graph = tmp_path / "vault" / "Личное" / "Встречи-архив" / "2026-08-03 09-00 — Врач"
    for d in (first, second, other_graph):
        d.mkdir(parents=True)

    assert archive_folder_for(graph, "2026-08-03_1400") == second
    assert archive_folder_for(graph, "2026-08-03_1130") == first
    assert archive_folder_for(graph, "2026-08-03_0900") == other_graph
    assert archive_folder_for(graph, "2026-08-03_1700") is None


def test_seen_marker_is_owner_only(tmp_path):
    """Сайдкар покоя создаётся 0600 явно, а не по umask вызывающего."""
    streaming = _wav(declared_data=32_000, actual_data=32_000)
    f = tmp_path / "m.wav"
    f.write_bytes(b"RIFF" + struct.pack("<I", 0) + streaming[8:])
    assert not wav_complete(f)
    marker = tmp_path / ".m.wav.import-seen"
    assert marker.exists() and (marker.stat().st_mode & 0o777) == 0o600


def test_import_title_goes_through_the_slug_guard():
    """Импорт строил имя мимо guard_slug — «Демо live» давал файл, который
    stamp_of считает копией `_live` (DS r4 по #455)."""
    import meeting_stamp
    import import_meeting as im
    assert im.title_slug("Демо live") == "Демо-live"
    assert im.title_slug("Разбор") == "Разбор-встреча"
    assert im.title_slug("Отчёт по задачам") == "Отчёт_по_задачам"
    assert meeting_stamp.stamp_of("2026-08-29_1200_" + im.title_slug("Демо live")) == "2026-08-29_1200"


# ---- №166: метки сбоя, сайдкары done/, ретеншн копий ---------------------

def _run_scan(monkeypatch, folder, *extra):
    """Скан через main(). main() зовёт harden_umask() — umask ПРОЦЕССА
    pytest менялся на 077, и test_private_permissions (ждёт 0755 у каталога
    установок) падал следом (CI по #496). Возвращаем umask как был."""
    import import_meeting as im
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", "--scan", *extra, "--", str(folder)])
    before = os.umask(0)
    os.umask(before)
    try:
        _main_keeping_umask()
    finally:
        os.umask(before)


def test_failed_import_is_marked_kept_and_not_rescanned(tmp_path, monkeypatch):
    import pytest

    """Сбой: файл на месте, метка ошибки с хвостом вывода, следующий скан
    его не берёт (раньше STT гонялся по тому же файлу каждые две минуты);
    --retry-failed снимает метку и пробует ещё раз."""
    import json

    import import_meeting as im

    bad = tmp_path / "bad.txt"
    bad.write_text("x" * 300, encoding="utf-8")
    calls = []

    class Failed:
        returncode = 1
        stdout = "что-то пошло\nтранскрибация не удалась\n"
        stderr = ""

    monkeypatch.setattr(im.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Failed())[1])
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {"import_keep_days": 2}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    im._seen_marker(bad).write_text("300 1\n", encoding="ascii")
    with pytest.raises(SystemExit) as failed_scan:
        _run_scan(monkeypatch, tmp_path)
    assert failed_scan.value.code == 1, "сбой файла — ненулевой код скана (DS r1)"
    assert im._seen_marker(bad).exists(), "покой размера уже доказан — маркер остаётся (r2)"
    assert bad.exists(), "при ошибке файл не удаляется и не переносится"
    marker = im.error_marker(bad)
    meta = json.loads(marker.read_text(encoding="utf-8"))
    assert meta["code"] == 1 and meta["message"] == "транскрибация не удалась"
    assert scan_candidates(tmp_path) == [], "сбойный файл ждёт «Повторить»"
    n = len(calls)
    _run_scan(monkeypatch, tmp_path)
    assert len(calls) == n, "без снятой метки повторного прогона быть не должно"
    with pytest.raises(SystemExit):
        _run_scan(monkeypatch, tmp_path, "--retry-failed")
    assert len(calls) == n + 1 and marker.exists(), "повтор — и снова метка, раз снова сбой"


def test_successful_import_lands_in_done_with_sidecar(tmp_path, monkeypatch):
    """Успех: файл в done/ под свободным именем, рядом сайдкар с моментом
    импорта, сроком удаления и итогом ребёнка (штамп, стенограмма)."""
    import json

    import import_meeting as im

    src = tmp_path / "Recording.txt"
    src.write_text("y" * 300, encoding="utf-8")
    (tmp_path / "done").mkdir()
    (tmp_path / "done" / "Recording.txt").write_text("старая копия", encoding="utf-8")

    def fake_child(cmd, **kw):
        result = pathlib.Path(cmd[cmd.index("--result-json") + 1])
        im._write_json(result, {"kind": "meeting", "stamp": "2026-09-05_1200",
                                "transcript": "/t/2026-09-05_1200.md",
                                "archive_source": "/arch/Исходник.txt"})

        class Ok:
            returncode = 0
            stdout = "готово: встреча 2026-09-05_1200 в архиве и графе\n"
            stderr = ""
        return Ok()

    monkeypatch.setattr(im.subprocess, "run", fake_child)
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {"import_keep_days": "1.5"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    before = time.time()
    _run_scan(monkeypatch, tmp_path)
    moved = tmp_path / "done" / "Recording-1.txt"
    assert moved.exists() and not src.exists(), "чужую копию с тем же именем не затираем"
    meta = json.loads(im.imported_sidecar(moved).read_text(encoding="utf-8"))
    assert meta["stamp"] == "2026-09-05_1200" and meta["source"] == "Recording.txt"
    assert meta["keep_days"] == 1.5
    assert before <= meta["imported_at"] <= time.time()
    assert abs(meta["delete_after"] - (meta["imported_at"] + 1.5 * 86400)) < 1
    assert not (tmp_path / ".Recording.txt.import-result.json").exists()


def test_prune_done_removes_expired_copies_and_archive_audio(tmp_path):
    """Ретеншн done/: срок — от импорта (сайдкар), не от mtime; вместе с
    копией уходит аудио-«Исходник» в архиве; текстовый исходник в архиве и
    неистёкшие копии остаются; корень папки не трогаем никогда."""
    import import_meeting as im

    done = tmp_path / "done"
    done.mkdir()
    arch = tmp_path / "archive"
    arch.mkdir()
    now = time.time()

    def put(name, delete_after, archive):
        f = done / name
        f.write_bytes(b"\0" * 10)
        old = now - 30 * 86400        # mtime старый — ретеншн на него не смотрит
        os.utime(f, (old, old))
        im._write_json(im.imported_sidecar(f), {"imported_at": now - 3 * 86400,
                                                "delete_after": delete_after,
                                                "stamp": "s", "archive_source": archive})
        return f

    audio_src = arch / "Исходник.m4a"
    audio_src.write_bytes(b"a")
    text_src = arch / "Исходник.vtt"
    text_src.write_text("WEBVTT", encoding="utf-8")
    foreign = arch / "Чужой.m4a"
    foreign.write_bytes(b"f")
    expired_audio = put("a.m4a", now - 60, str(audio_src))
    expired_text = put("b.vtt", now - 60, str(text_src))
    fresh = put("c.m4a", now + 3600, str(foreign))
    stray = put("d.m4a", now - 60, str(foreign))     # чужое имя в архиве — не наше
    root = tmp_path / "root.m4a"
    root.write_bytes(b"r")
    os.utime(root, (now - 30 * 86400, now - 30 * 86400))

    removed = im.prune_done(tmp_path, 2, now=now)
    assert expired_audio in removed and audio_src in removed
    assert expired_text in removed and text_src.exists(), "текстовый исходник — не голос"
    assert stray in removed and foreign.exists(), "в архиве удаляем только «Исходник…»"
    assert fresh.exists() and im.imported_sidecar(fresh).exists()
    assert root.exists(), "корень папки импорта — не зона ретеншна"
    assert not im.imported_sidecar(expired_audio).exists()


def test_prune_legacy_done_files_get_their_days_from_the_first_sweep(tmp_path):
    """Файл в done/ без сайдкара (импорт до этой версии): первый проход
    даёт ему сайдкар «увидели сейчас», удаление — через keep_days с этого
    момента, независимо от ctime/mtime (GLM r1 по #496: ставка на ctime
    при rename не обещана POSIX)."""
    import json

    import import_meeting as im

    done = tmp_path / "done"
    done.mkdir()
    legacy = done / "old.m4a"
    legacy.write_bytes(b"\0")
    old = time.time() - 30 * 86400
    os.utime(legacy, (old, old))
    now = time.time()
    assert im.prune_done(tmp_path, 2, now=now) == []
    assert legacy.exists()
    meta = json.loads(im.imported_sidecar(legacy).read_text(encoding="utf-8"))
    assert meta["legacy"] is True and meta["imported_at"] == now
    assert im.prune_done(tmp_path, 2, now=now + 2 * 86400 - 1) == []
    assert im.prune_done(tmp_path, 2, now=now + 2 * 86400 + 1) == [legacy]
    assert not legacy.exists() and not im.imported_sidecar(legacy).exists()


def test_prune_finds_archive_source_by_stamp_when_sidecar_lacks_it(tmp_path):
    """Ребёнок упал после cp в архив и до отчёта (или это повтор без копии):
    исходник ищем по штампу в графе тем же поиском, что импорт."""
    import import_meeting as im

    graph = tmp_path / "vault" / "Работа"
    folder = graph / "Встречи-архив" / "2026-09-05 12-00 — Тема"
    folder.mkdir(parents=True)
    src = folder / "Исходник.m4a"
    src.write_bytes(b"a")
    done = tmp_path / "done"
    done.mkdir()
    copy = done / "rec.m4a"
    copy.write_bytes(b"c")
    now = time.time()
    im._write_json(im.imported_sidecar(copy), {"imported_at": now - 9, "delete_after": now - 1,
                                               "stamp": "2026-09-05_1200"})
    removed = im.prune_done(tmp_path, 2, now=now, graph=graph)
    assert copy in removed and src in removed and not src.exists()


def test_orphan_error_marker_does_not_haunt_the_next_file(tmp_path, monkeypatch):
    """Сбойный файл убрали руками, метка осталась; новый файл с тем же
    именем не должен считаться сбойным (GLM r1 по #496)."""
    import import_meeting as im

    ghost = tmp_path / "Recording.txt"
    im._write_json(im.error_marker(ghost), {"code": 1, "message": "старый сбой"})
    fresh = tmp_path / "Recording.txt"
    fresh.write_text("z" * 300, encoding="utf-8")
    # метка живёт рядом с файлом того же имени — это ЕЩЁ не сирота
    assert scan_candidates(tmp_path) == []
    fresh.unlink()
    calls = []

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(im.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Ok())[1])
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    _run_scan(monkeypatch, tmp_path)               # скан без файла — сирота-метка убрана
    assert not im.error_marker(ghost).exists()
    fresh.write_text("z" * 300, encoding="utf-8")
    _run_scan(monkeypatch, tmp_path)
    assert len(calls) == 1, "новый файл с именем старого сбоя импортируется"


def test_one_broken_file_does_not_stop_the_queue(tmp_path, monkeypatch):
    """OSError на переносе одного файла — пропуск, остальные идут, уборка
    в конце выполняется (GLM r1 по #496)."""
    import import_meeting as im

    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    for f in (a, b):
        f.write_text("q" * 300, encoding="utf-8")

    class Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(im.subprocess, "run", lambda cmd, **kw: Ok())
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {"import_keep_days": 0}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    real_rename = pathlib.Path.rename

    def flaky(self, target):
        if self.name == "a.txt":
            raise OSError("диск полон")
        return real_rename(self, target)

    monkeypatch.setattr(pathlib.Path, "rename", flaky)
    import pytest
    with pytest.raises(SystemExit) as partly:
        _run_scan(monkeypatch, tmp_path)
    assert partly.value.code == 1, "часть очереди не прошла — код 1"
    assert a.exists(), "сбойный перенос — файл на месте"
    assert not b.exists(), "второй файл обработан несмотря на первый"
    assert not (tmp_path / "done" / "b.txt").exists(), "уборка в конце скана (keep 0) прошла"
    orphan = im.imported_sidecar(tmp_path / "done" / "a.txt")
    assert orphan.exists(), "свежий сайдкар (моложе минуты) уборка щадит — живой мог быть до переноса"
    im.prune_done(tmp_path, 0, now=time.time() + 120)
    assert not orphan.exists(), "сайдкар, записанный до сорвавшегося переноса, убран как сирота"


def test_settle_all_holds_any_fresh_file_but_not_explicit_scans(tmp_path):
    """Тик слежения: чужая копия m4a/txt в папку не атомарна — файл обязан
    30 с не менять размер; кнопка «Обработать сейчас» и скан после дропа
    (копии вкладки опубликованы через .part) берут сразу (r3 по #496)."""
    import import_meeting as im

    f = tmp_path / "Recording.m4a"
    f.write_bytes(b"\0" * 1000)
    assert im.scan_candidates(tmp_path) == [f], "без флага — как раньше, сразу"
    assert im.scan_candidates(tmp_path, settle_all=True) == []
    assert im.postponed_files(tmp_path, settle_all=True) == [f]
    _age_markers(tmp_path, WAV_SETTLE_SECONDS + 1)
    assert im.scan_candidates(tmp_path, settle_all=True) == [f]
    with f.open("ab") as fh:
        fh.write(b"\0" * 10)
    assert im.scan_candidates(tmp_path, settle_all=True) == [], "рост размера — отсчёт заново"


def test_scan_prints_machine_marker_for_postponed_files(tmp_path, monkeypatch, capsys):
    """Приложение догоняет отложенные файлы по маркеру `postponed=N`, а не по
    человеческой фразе (GLM r5 по #496)."""
    import import_meeting as im

    (tmp_path / "Recording.m4a").write_bytes(b"\0" * 100)
    monkeypatch.setattr(im, "_cfg", lambda: {"audio": {}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    _run_scan(monkeypatch, tmp_path, "--settle-all")
    out = capsys.readouterr().out
    assert "postponed=1" in out and "отложен до следующего скана" in out


def test_sweep_removes_only_old_temporaries(tmp_path):
    """Сироты .part и отчётов ребёнка после краха уходят по возрасту; живая
    копия (свежая) и чужие файлы — нет (GLM/DS r3 по #496)."""
    import import_meeting as im

    old_part = tmp_path / ".Recording.m4a.a1b2c3d4.part"
    old_part.write_bytes(b"x")
    fresh_part = tmp_path / ".Live.m4a.ffffffff.part"
    fresh_part.write_bytes(b"y")
    old_result = tmp_path / ".Recording.m4a.import-result.json"
    old_result.write_text("{}", encoding="utf-8")
    other = tmp_path / ".Recording.m4a.import-error"
    other.write_text("{}", encoding="utf-8")
    ago = time.time() - im.TEMP_ORPHAN_AGE - 5
    for p in (old_part, old_result):
        os.utime(p, (ago, ago))
    removed = im.sweep_temporaries(tmp_path)
    assert sorted(removed) == sorted([old_part, old_result])
    assert fresh_part.exists() and other.exists()


def test_orphan_sidecar_sweep_spares_the_young(tmp_path):
    """Сайдкар пишется до переноса: параллельный --prune не должен съесть
    живой сайдкар моложе минуты (r3 по #496)."""
    import import_meeting as im

    done = tmp_path / "done"
    done.mkdir()
    young = im.imported_sidecar(done / "a.m4a")
    im._write_json(young, {"imported_at": 1, "delete_after": 2})
    old = im.imported_sidecar(done / "b.m4a")
    im._write_json(old, {"imported_at": 1, "delete_after": 2})
    ago = time.time() - 120
    os.utime(old, (ago, ago))
    im.prune_done(tmp_path, 2)
    assert young.exists() and not old.exists()


def test_import_keep_days_is_forgiving_but_not_negative(capsys):
    import pytest

    import import_meeting as im

    assert im.import_keep_days({"audio": {"import_keep_days": "1.5"}}) == 1.5
    assert im.import_keep_days({}) == im.IMPORT_KEEP_DAYS_DEFAULT
    assert im.import_keep_days({"audio": {"import_keep_days": "два"}}) == im.IMPORT_KEEP_DAYS_DEFAULT
    assert "непонятное" in capsys.readouterr().out
    assert im.import_keep_days({"audio": {"import_keep_days": "1e999"}}) == im.IMPORT_KEEP_DAYS_DEFAULT
    assert im.import_keep_days({"audio": {"record_keep_days": 7}}) == 7, "без своего срока — как у записей"
    assert im.import_keep_days({"audio": {"import_keep_days": None, "record_keep_days": 5}}) == 5, \
        "пустой ключ в YAML — тоже каскад (GLM r2)"
    assert im.import_keep_days({"audio": {"import_keep_days": 5}}, override="0") == 0
    with pytest.raises(SystemExit):
        im.import_keep_days({"audio": {"import_keep_days": -1}})


def _main_keeping_umask() -> None:
    """im.main() в процессе тестов: harden_umask() менял umask процесса, и
    следующий тест (test_private_permissions) видел чужие права (CI 05.09)."""
    before = os.umask(0o022)
    os.umask(before)
    try:
        _main_keeping_umask()
    finally:
        os.umask(before)


def test_direct_import_publishes_a_meeting_status(tmp_path, monkeypatch, capsys):
    """Поле 05.09: две записи с телефона были разобраны и разложены в граф,
    но в списке встреч приложения их не было — импорт не писал статус
    встречи. Теперь: прогресс на графе, «готово» со ссылкой на заметку, а
    повтор импорта — тоже «готово» (идемпотентно)."""
    import json

    root = tmp_path / "root"
    (root / "transcripts").mkdir(parents=True)
    src = tmp_path / "Заметки.txt"
    src.write_text("х" * 400, encoding="utf-8")
    note = tmp_path / "graph" / "Встречи" / "2026-09-05_1200.md"
    note.parent.mkdir(parents=True)
    note.write_text("# встреча\n", encoding="utf-8")

    class Ok:
        returncode = 0
        stdout = stderr = ""
    calls = []
    monkeypatch.setattr(im.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), Ok())[1])
    monkeypatch.setattr(im, "ROOT", root)
    monkeypatch.setattr(im, "_cfg", lambda: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(im, "find_meeting_note", lambda cfg, t, **kw: note)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", str(src), "--date", "2026-09-05", "--time", "12:00"])
    _main_keeping_umask()

    status = root / "logs" / "meeting-status" / "2026-09-05_1200.json"
    assert status.exists(), sorted((root / "logs").rglob("*")) if (root / "logs").exists() else "нет статуса"
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["state"] == "ready" and data["note_path"] == str(note.resolve())
    assert data["transcript_path"].endswith("2026-09-05_1200.md")
    assert any("graph_updater.py" in str(c) for c in calls)

    # повтор той же записи: встреча уже есть — статус остаётся «готово»
    status.unlink()
    _main_keeping_umask()
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "ready"
    assert "повтор не нужен" in capsys.readouterr().out


def test_direct_import_publishes_failed_when_graph_updater_crashes(tmp_path, monkeypatch):
    """Critical GLM r1 по #502: любой ненулевой код graph_updater, кроме «нет
    речи» и «нет разбора», — ошибка статуса, а не «готово»."""
    import json

    root = tmp_path / "root"
    (root / "transcripts").mkdir(parents=True)
    src = tmp_path / "Заметки.txt"
    src.write_text("х" * 400, encoding="utf-8")

    class Crash:
        returncode = 1
        stdout = stderr = ""
    monkeypatch.setattr(im.subprocess, "run", lambda cmd, **kw: Crash())
    monkeypatch.setattr(im, "ROOT", root)
    monkeypatch.setattr(im, "_cfg", lambda: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", str(src), "--date", "2026-09-05", "--time", "12:00"])
    _main_keeping_umask()

    data = json.loads((root / "logs" / "meeting-status" / "2026-09-05_1200.json").read_text(encoding="utf-8"))
    assert data["state"] == "error" and "с кодом 1" in data["error"]


def test_direct_import_publishes_failed_when_the_tail_dies(tmp_path, monkeypatch):
    """Критика GLM r2 по #502: граф собрался, а retro_fill (минутки/разбор)
    умер по сигналу — статус «ошибка» словами «сигналом 9», не «готово»."""
    import json

    root = tmp_path / "root"
    (root / "transcripts").mkdir(parents=True)
    src = tmp_path / "Заметки.txt"
    src.write_text("х" * 400, encoding="utf-8")

    class Result:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = self.stderr = ""

    def run(cmd, **kw):
        return Result(-9 if any("retro_fill.py" in str(c) for c in cmd) else 0)
    monkeypatch.setattr(im.subprocess, "run", run)
    monkeypatch.setattr(im, "ROOT", root)
    monkeypatch.setattr(im, "_cfg", lambda: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(im.graphs, "graph_dir", lambda cfg: None)
    monkeypatch.setattr(sys, "argv", ["import_meeting.py", str(src), "--date", "2026-09-05", "--time", "12:00"])
    _main_keeping_umask()

    data = json.loads((root / "logs" / "meeting-status" / "2026-09-05_1200.json").read_text(encoding="utf-8"))
    assert data["state"] == "error" and "retro_fill" in data["error"] and "сигналом 9" in data["error"]
