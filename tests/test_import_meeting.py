"""Импорт встреч: парсер vtt/srt и сборка стенограммы конвейера."""
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from import_meeting import (  # noqa: E402
    clean_date,
    clean_time,
    parse_subs,
    postponed_files,
    scan_candidates,
    source_of,
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

    assert wav_complete(unknown)
    assert wav_complete(zeroed)
    assert sorted(scan_candidates(tmp_path)) == [unknown, zeroed]


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


def test_повтор_импорта_успех_а_не_отказ(tmp_path):
    """Повтор обязан (1) вернуть 0 и (2) видеть titled-встречи.

    Скан переносит файл в done/ только при нулевом коде: выход строкой
    (= код 1) оставлял файл в папке импорта навсегда — три записи с
    телефона молотились каждые две минуты. А проверка по голому
    `<stamp>.md` не видела встреч, переименованных конвейером в
    `<stamp>_Тема.md`, и повторный импорт гонял по дублю полный
    LLM-конвейер (оба найдены 06.08 — второй как раз этим тестом).
    """
    import subprocess

    import pytest

    titled = next((p for p in sorted((ROOT / "transcripts").glob("2026-*_*.md"))
                   if len(p.name) > len("2026-08-03_1314.md")
                   and not (p.parent / (p.name[:15] + ".md")).exists()), None)
    if titled is None:
        pytest.skip("нет titled-встречи без голой пары — не на чем проверять")
    stamp = titled.name[:15]                          # 2026-08-05_1334
    src = tmp_path / "повтор.txt"
    src.write_text("х" * 300, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_meeting.py"), str(src),
         "--date", stamp[:10], "--time", stamp[11:13] + ":" + stamp[13:15]],
        capture_output=True, text=True, timeout=60)
    assert "повтор не нужен" in run.stdout, (
        f"titled-встреча {titled.name} не распознана как повтор:\n{run.stdout}")
    assert run.returncode == 0, (
        f"код {run.returncode}: повтор считается отказом, файл застрянет в импорте")


def _пачка(root, files):
    """Папка данных с конфигом и записями, приехавшими одним синком."""
    (root / "transcripts").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "config.yaml").write_text(
        "log: {transcripts_dir: transcripts}\nsufler: {graph_dir: ''}\n",
        encoding="utf-8")
    made = []
    for name, when in files:
        f = root / name
        f.write_text("х" * 300, encoding="utf-8")
        os.utime(f, (when, when))
        made.append(f)
    return made


def _импорт(root, f):
    import subprocess
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_meeting.py"), str(f)],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, CHAROITE_ROOT=str(root)))
    return r.stdout + r.stderr


def test_две_разные_записи_одной_минуты_обе_импортируются(tmp_path):
    """Две встречи, попавшие в одну секунду, — это две встречи.

    Раньше штамп импорта был поминутным, и вторая запись объявлялась
    повтором: код 0, файл уезжал в done/ как успешный, а встречи не
    оставалось нигде — ни в стенограммах, ни в графе, ни в архиве. Заметить
    это можно было только ручной сверкой done/ с архивом.

    Считаем не файлы, а ИСХОДНИКИ: одинаковое количество стенограмм получится
    и тогда, когда вторая встреча затёрла первую.
    """
    made = _пачка(tmp_path, [("первая.txt", 1_780_000_000),
                             ("вторая.txt", 1_780_000_000)])
    outs = [_импорт(tmp_path, f) for f in made]

    sources = sorted(filter(None, (source_of(p) for p in (tmp_path / "transcripts").glob("*.md"))))
    assert sources == ["вторая.txt", "первая.txt"], (
        f"встречи потеряны или затёрли друг друга: {sources}\n{outs[1]}")


def test_короткое_имя_не_считается_повтором_длинного(tmp_path):
    """`1.m4a` не должен опознаваться как повтор `11.m4a`.

    Диктофон и телефон нумеруют файлы именно так, а синк выдаёт им общий
    mtime. Сравнение подстрокой выглядит безобиднее точного и ломается ровно
    здесь: встреча из `1.txt` пропала бы совсем, а импорт отчитался бы
    успехом.
    """
    made = _пачка(tmp_path, [("11.txt", 1_780_000_000), ("1.txt", 1_780_000_000)])
    outs = [_импорт(tmp_path, f) for f in made]

    sources = sorted(filter(None, (source_of(p) for p in (tmp_path / "transcripts").glob("*.md"))))
    assert sources == ["1.txt", "11.txt"], (
        f"короткое имя проглочено как повтор длинного: {sources}\n{outs[1]}")


def test_повторный_импорт_после_развода_штампов_не_плодит_дубли(tmp_path):
    """Свою встречу надо искать среди ВСЕХ кандидатов минуты, а не в первом.

    После развода штампов в минуте лежит несколько встреч, и `-`/`.` в
    сортировке ставят вперёд не ту. Спросив только про первую, импорт каждый
    раз объявлял бы свою запись новой — и гонял бы полный LLM-конвейер по
    дублю на каждом скане.
    """
    made = _пачка(tmp_path, [("первая.txt", 1_780_000_000),
                             ("вторая.txt", 1_780_000_000)])
    for f in made:
        _импорт(tmp_path, f)
    before = sorted(p.name for p in (tmp_path / "transcripts").glob("*.md"))

    again = _импорт(tmp_path, made[0])          # тот же файл ещё раз

    after = sorted(p.name for p in (tmp_path / "transcripts").glob("*.md"))
    assert after == before, f"повтор наплодил дублей: было {before}, стало {after}\n{again}"
    assert "повтор не нужен" in again, again


def test_повтор_одного_и_того_же_файла_по_прежнему_повтор(tmp_path):
    """Секунды в штампе не должны сломать защиту от повторного импорта:
    у одного и того же файла mtime тот же, значит и штамп тот же."""
    import subprocess

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "log: {transcripts_dir: transcripts}\nsufler: {graph_dir: ''}\n",
        encoding="utf-8")
    f = tmp_path / "одна.txt"
    f.write_text("х" * 300, encoding="utf-8")
    os.utime(f, (1_780_000_000, 1_780_000_000))

    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    cmd = [sys.executable, str(ROOT / "scripts" / "import_meeting.py"), str(f)]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    again = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)

    assert "повтор не нужен" in again.stdout, (
        f"повторный импорт того же файла создал дубль:\n{again.stdout}")
    assert again.returncode == 0, "повтор считается отказом — файл застрянет в импорте"


def test_секунды_различают_записи_внутри_минуты(tmp_path):
    """Две записи в одну минуту получают разные штампы сами по себе.

    Это сценарий аудита в чистом виде: две выгрузки Zoom с разницей в
    секунды. Суффикс коллизии `-N` здесь не нужен и не должен появляться —
    он страховка для совсем уж одинакового mtime, а не основной механизм.
    """
    import subprocess

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "log: {transcripts_dir: transcripts}\nsufler: {graph_dir: ''}\n",
        encoding="utf-8")

    env = dict(os.environ, CHAROITE_ROOT=str(tmp_path))
    for name, when in (("первая.txt", 1_780_000_000), ("вторая.txt", 1_780_000_007)):
        f = tmp_path / name
        f.write_text("х" * 300, encoding="utf-8")
        os.utime(f, (when, when))                      # одна минута, разные секунды
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "import_meeting.py"), str(f)],
            capture_output=True, text=True, timeout=120, env=env)

    names = sorted(p.stem for p in tdir.glob("*.md"))
    assert len(names) == 2, f"вторая встреча потеряна: {names}"
    assert not any("-" in n.split("_")[-1] for n in names), (
        f"понадобился суффикс коллизии — значит штамп снова поминутный: {names}")
