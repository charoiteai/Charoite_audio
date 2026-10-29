"""Замер качества разбора обязан ловить выдуманное, а не считать строки.

Конфиг переехал на MLX-сборку по замеру скорости; «кто точнее» проверялось
на одной минутке. Инструмент сравнения (scripts/bench_extract.py) отвечает на
этот вопрос двумя объективными метриками поверх содержательного чтения:
цитаты ядер и отметки времени должны находиться в стенограмме.

Тесты держат именно их: метрика, которая засчитывает выдуманную цитату,
хуже отсутствующей — она даёт ложную уверенность в модели.
"""
from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_extract as be  # noqa: E402
import charoite_paths  # noqa: E402

TRANSCRIPT = """# Встреча 2026-08-12_1532

**Саш** [15:37]:
Около пятидесяти человеко-дней ещё было сверху, могу прислать в оффлайне.

**Коля** [15:41]:
Мне дешевле восемь лет платить им по одиннадцать человеко-дней в год.
"""


def core(quote: str = "", stamp: str = "") -> dict:
    return {"ядра": [{"имя": "Оценка перехода", "цитата": quote, "время": stamp}]}


def test_verbatim_quote_counts():
    hit, total = be.quote_hits(core("Около пятидесяти человеко-дней ещё было сверху"),
                               TRANSCRIPT)
    assert (hit, total) == (1, 1)


def test_paraphrase_close_enough_counts():
    # Граф ищет якорь скользящим окном с порогом 0.75 и вписывает ОРИГИНАЛЬНЫЙ
    # срез: пересказ, который так находится, основанием остаётся.
    hit, total = be.quote_hits(core("около пятидесяти человеко-дней было сверху"),
                               TRANSCRIPT)
    assert (hit, total) == (1, 1)


def test_invented_quote_does_not_count():
    hit, total = be.quote_hits(
        core("Бюджет утверждён советом директоров в понедельник"), TRANSCRIPT)
    assert (hit, total) == (0, 1), "выдуманная цитата засчитана как основание"


def test_missing_quotes_are_not_a_perfect_score():
    # Ядра без цитат — не «100% попаданий», а отсутствие данных: иначе модель,
    # молчащая про основания, обгоняет ту, что их приводит.
    assert be.quote_hits(core(), TRANSCRIPT) == (0, 0)
    assert be.share(0, 0) == "—"


def test_timestamps_are_checked_against_the_transcript():
    assert be.time_hits(core(stamp="15:37"), TRANSCRIPT) == (1, 1)
    assert be.time_hits(core(stamp="19:02"), TRANSCRIPT) == (0, 1)


def test_garbage_timestamp_is_not_counted_as_a_miss():
    # «примерно в середине» — не отметка времени; в знаменатель такое не идёт,
    # иначе метрика штрафует за формат ответа, а не за выдумку.
    assert be.time_hits(core(stamp="примерно в середине"), TRANSCRIPT) == (0, 0)


def test_service_files_are_not_taken_for_meetings(tmp_path, monkeypatch):
    folder = tmp_path / "transcripts"
    folder.mkdir()
    for name in ("2026-08-12_1532.md", "2026-08-12_1532_hints.md",
                 "2026-08-12_1532_minutes.md", "2026-08-12_1532_разбор.md",
                 "2026-08-12_1532_live.md", "2026-08-12_1532_ревизия_claude.md",
                 "2026-08-12_1532_после_звонка.md"):
        (folder / name).write_text("текст", encoding="utf-8")
    # корень подменяется публичной дверью канона: в процессе его уже назвала
    # обвязка, и переменную канон не услышал бы (сегодня)
    charoite_paths.use_data_root(tmp_path, replace=True)

    # now в будущем: файлы «остыли», иначе их отсечёт защита от идущей встречи
    found = be.meetings(10, now=pathlib.Path(folder / "2026-08-12_1532.md")
                        .stat().st_mtime + be.FRESH_S + 1)

    assert [p.name for p in found] == ["2026-08-12_1532.md"], \
        "в замер попали продукты модели вместо речи людей"


def test_meeting_still_being_written_is_skipped(tmp_path, monkeypatch):
    # Идущая встреча дописывается прямо во время замера: разбор половины
    # разговора сравнивает не модели, а то, кому досталось больше текста.
    folder = tmp_path / "transcripts"
    folder.mkdir()
    live = folder / "2026-08-13_1031.md"
    live.write_text("идёт прямо сейчас", encoding="utf-8")
    charoite_paths.use_data_root(tmp_path, replace=True)

    assert be.meetings(10, now=live.stat().st_mtime + 60) == []


def test_bench_does_not_inherit_the_owners_second_pass(tmp_path, monkeypatch):
    """Цифры пресетов — один проход: флаг `graph_second_pass: true` в конфиге владельца не должен
    молча удваивать колонку «сек»; второй проход — только явным `--second-pass` (n822-in1, I3)."""
    seen = []
    monkeypatch.setattr(be.gu, "extract",
                        lambda cfg, text: seen.append(cfg["sufler"]["graph_second_pass"]) or {})
    f = tmp_path / "m.md"
    f.write_text(TRANSCRIPT, encoding="utf-8")
    owner = {"llm": {"model": "x"}, "sufler": {"graph_second_pass": True, "language": "ru"}}
    be.measure(owner, "m", f)
    be.measure(owner, "m", f, second_pass=True)
    assert seen == [False, True]
    assert owner["sufler"]["graph_second_pass"] is True, "конфиг вызывающего не правится"


def test_bench_does_not_merge_decisions(tmp_path, monkeypatch):
    """Колонка «решения» — что нашла модель: склейка из конфига владельца в бенч не протекает (№824)."""
    seen = []
    monkeypatch.setattr(be.gu, "extract",
                        lambda cfg, text: seen.append(cfg["sufler"]["graph_decision_merge"]) or {})
    f = tmp_path / "m.md"
    f.write_text(TRANSCRIPT, encoding="utf-8")
    owner = {"llm": {"model": "x"}, "sufler": {"graph_decision_merge": True, "language": "ru"}}
    be.measure(owner, "m", f)
    assert seen == [False]
    assert owner["sufler"]["graph_decision_merge"] is True, "конфиг вызывающего не правится"
