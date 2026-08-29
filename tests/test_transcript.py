"""Characterization net for the live transcript aggregate.

The tests pin observable behavior before the aggregate moves farther away
from the legacy CLI.  They deliberately avoid the capture/STT loops: this
boundary owns text state and the Markdown projection only.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import transcript  # noqa: E402
from transcript import Transcript  # noqa: E402

STAMP = "2026-08-24_183015"


def make_transcript(tmp_path, monkeypatch) -> Transcript:
    monkeypatch.setattr(transcript.meeting_stamp, "now", lambda: STAMP)
    return Transcript(tmp_path / "transcripts")


def test_creation_is_durable_and_collision_safe(tmp_path, monkeypatch):
    first = make_transcript(tmp_path, monkeypatch)
    second = make_transcript(tmp_path, monkeypatch)

    assert first.path.name == f"{STAMP}.md"
    assert second.path.name == f"{STAMP}-1.md"
    assert first.path.read_text(encoding="utf-8") == f"# Встреча {STAMP}\n\n"
    assert not first.path.with_suffix(".tmp").exists()


def test_blocks_merge_by_speaker_but_not_across_channels(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)

    assert tr.add("Первый фрагмент", "mic") == "Первый фрагмент"
    assert tr.add("второй фрагмент", "mic") == "второй фрагмент"
    assert tr.add("Ответ собеседника", "blackhole") == "Ответ собеседника"

    lines = tr.full().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("mic: Первый фрагмент второй фрагмент")
    assert lines[1].endswith("blackhole: Ответ собеседника")


def test_seam_overlap_is_deduplicated_inside_one_channel(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)

    tr.add("мы обсудили бюджет на квартал", "mic")
    added = tr.add("бюджет на квартал и сроки запуска", "mic")

    assert added == "и сроки запуска"
    assert tr.full().count("бюджет на квартал") == 1
    assert tr.add("и сроки запуска", "mic") is None


def test_seam_across_label_change_on_the_same_channel(tmp_path, monkeypatch):
    """Лаг → здоровый: тот же канал, другая метка — шов всё равно один (№69).
    Чужой канал без `seam_with` не сверяется: повтор слов — речь."""
    tr = make_transcript(tmp_path, monkeypatch)
    tr.add("мы обсудили бюджет на квартал", "Собеседник")            # lagging: метка канала
    added = tr.add("бюджет на квартал и сроки запуска", "Собеседник 3",
                   seam_with="Собеседник")                            # здоровый: метка голоса
    assert added == "и сроки запуска"
    assert tr.full().count("бюджет на квартал") == 1
    assert tr.add("бюджет на квартал и сроки запуска", "mic") == "бюджет на квартал и сроки запуска"
    # переименование задним числом не ломает сверку: метка ищется через _names
    tr.add("решили перенести релиз", "Собеседник 3")
    tr.rename_speaker("Собеседник 3", "Алексей")
    assert tr.add("решили перенести релиз на среду", "Собеседник 4", seam_with="Собеседник 3") == "на среду"
    # и свой же шов переименованного голоса: ключи дедупа переезжают вместе с меткой
    tr.add("после обеда обсудим тесты", "Собеседник 5")
    tr.rename_speaker("Собеседник 5", "Мария")
    assert tr.add("обсудим тесты и релиз", "Собеседник 5") == "и релиз"


def test_seam_across_labels_expires_with_the_window(tmp_path, monkeypatch):
    import datetime as real_dt

    class Clock:
        current = real_dt.datetime(2026, 8, 29, 16, 40, 0)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(transcript.dt, "datetime", Clock)
    tr = make_transcript(tmp_path, monkeypatch)
    tr.add("мы обсудили бюджет на квартал", "Собеседник")
    Clock.current += real_dt.timedelta(seconds=transcript.Transcript.SEAM_WINDOW + 1)
    assert tr.add("бюджет на квартал и сроки запуска", "Собеседник 3",
                  seam_with="Собеседник") == "бюджет на квартал и сроки запуска", "старый чанк — не шов"


def test_split_gap_breaks_block_with_frozen_clock(tmp_path, monkeypatch):
    import datetime as real_dt

    class Clock:
        current = real_dt.datetime(2026, 8, 24, 18, 30, 15)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(transcript.dt, "datetime", Clock)
    tr = make_transcript(tmp_path, monkeypatch)

    tr.add("до паузы", "mic")
    Clock.current += real_dt.timedelta(seconds=transcript.Transcript.SPLIT_GAP - 1)
    tr.add("ещё тот же блок", "mic")
    Clock.current += real_dt.timedelta(seconds=transcript.Transcript.SPLIT_GAP + 1)
    tr.add("после паузы", "mic")
    Clock.current += real_dt.timedelta(seconds=transcript.Transcript.SPLIT_GAP)
    tr.add("ровно на границе", "mic")

    lines = tr.full().splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("mic: до паузы ещё тот же блок")
    assert lines[1].endswith("mic: после паузы")
    assert lines[2].endswith("mic: ровно на границе")


def test_rename_is_retroactive_and_applies_to_future_chunks(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)
    tr.add("До знакомства", "Собеседник 1")

    tr.rename_speaker("Собеседник 1", "Алексей")
    tr.add("После знакомства", "Собеседник 1")

    assert tr.display_name("Собеседник 1") == "Алексей"
    assert tr.names() == {"Собеседник 1": "Алексей"}
    assert "Собеседник 1" not in tr.full()
    assert tr.full().endswith("Алексей: До знакомства После знакомства")


def test_markdown_projection_keeps_participants_and_notes(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)
    tr.set_participants(["Анна", "Борис"])
    tr.add("Решение принято", "Анна")
    tr.note("📌 Проверить договор")

    rendered = tr.path.read_text(encoding="utf-8")
    assert "Участники (звучали в разговоре): Анна, Борис" in rendered
    assert "**Анна** [" in rendered
    assert "## Ко-мышление (📌 КТ · 💎 факты · 💭 мысли)" in rendered
    assert "> " in rendered and "📌 Проверить договор" in rendered


def test_tail_never_goes_empty_for_one_oversized_monologue(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)
    tr.add("слово " * 80, "Анна")

    tail = tr.tail(60)

    assert len(tail) == 60
    assert tail.endswith("слово ")


def test_snapshot_update_is_compare_and_swap(tmp_path, monkeypatch):
    tr = make_transcript(tmp_path, monkeypatch)
    tr.add("Черновой текст", "Анна")
    idx, _when, _speaker, old = tr.last_block()

    assert tr.update_block_text(idx, old, "Исправленный текст") is True
    assert tr.update_block_text(idx, old, "Устаревшая правка") is False
    assert tr.last() == "Исправленный текст"
    assert "Устаревшая правка" not in tr.path.read_text(encoding="utf-8")


def test_transcript_boundary_does_not_load_runtime_stack():
    code = """
import sys
sys.path.insert(0, 'src')
import transcript
for name in ('rich', 'audio', 'llm', 'stt'):
    assert name not in sys.modules, name
assert transcript.Transcript
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_modules_do_not_import_the_legacy_cli():
    offenders: list[str] = []
    for path in sorted(SRC.glob("*.py")):
        if path.name == "main.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ImportFrom) and node.module == "main"
               for node in ast.walk(tree)):
            offenders.append(path.name)

    assert offenders == [], f"runtime снова зависит от legacy main.py: {offenders}"
