"""Минутки после пересборки — не черновик и без «Собеседник N» (№146).

Встреча 08:45 31.08: стенограмма пересобрана, имена подставлены, а минутки
рядом остались с шапкой «черновик, встреча идёт» и участниками «Собеседник 2,
Собеседник 4» — человек читал устаревший документ с ярлыками. Перештамповка
точечная: маркер и метки; ручные правки человека не перегенерируются.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import rebuild_transcript  # noqa: E402
import transcript  # noqa: E402


def _make(tmp_path, minutes: str):
    live = tmp_path / "2026-08-31_0845.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    mpath = tmp_path / "2026-08-31_0845_minutes.md"
    mpath.write_text(minutes, encoding="utf-8")
    return live, mpath


def test_marker_removed_and_labels_become_names(tmp_path):
    live, mpath = _make(
        tmp_path,
        transcript.MINUTES_DRAFT_MARK + "\n# Минутки\n\n"
        "Участники: Ян, Собеседник 2, Собеседник 4\n\n"
        "Поручения:\n- [ ] **Собеседник 2** — прислать смету\n")
    rebuild_transcript.restamp_minutes(
        live, {"Собеседник 2": "Инга", "Собеседник 4": "Марк"})
    out = mpath.read_text(encoding="utf-8")
    assert transcript.MINUTES_DRAFT_MARK not in out
    assert "Инга" in out and "Марк" in out
    assert "Собеседник 2" not in out and "Собеседник 4" not in out
    assert "- [ ] **Инга** — прислать смету" in out


def test_label_boundary_respects_digits(tmp_path):
    """«Собеседник 2» не должен красить «Собеседник 22»."""
    live, mpath = _make(
        tmp_path, "Участники: Собеседник 2, Собеседник 22\n")
    rebuild_transcript.restamp_minutes(live, {"Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Инга, Собеседник 22" in out, out


def test_idempotent_and_quiet_without_minutes(tmp_path):
    """Повторная пересборка (retry_unfinished) не должна ни падать без
    минуток, ни переписывать уже перештампованный файл."""
    live = tmp_path / "no_minutes.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    rebuild_transcript.restamp_minutes(live, {"Собеседник 2": "Инга"})  # файла нет — тихо

    live2, mpath = _make(tmp_path, "# Минутки\nУчастники: Инга\n")
    before = mpath.stat().st_mtime_ns
    rebuild_transcript.restamp_minutes(live2, {"Собеседник 2": "Инга"})
    assert mpath.stat().st_mtime_ns == before, "нечего менять — файл не трогается"


def test_owner_label_is_never_substituted(tmp_path):
    """names может нести и метку владельца («Я» при пустом user_name —
    names_by_time скорит все сегменты, №147): без фильтра re.sub без границы
    слова переписал бы каждое «Я» и каждое «Яблоко» документа (DS Critical
    по #464). Подменяются только нейтральные «Собеседник N»."""
    live, mpath = _make(
        tmp_path,
        "# Минутки\n\nЯ отвечаю за релиз. Январь — срок Яна. Яблочный пирог — Собеседник 2.\n")
    rebuild_transcript.restamp_minutes(
        live, {"Я": "Имярек", "Ян": "Имярек2", "Собеседник 2": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Я отвечаю за релиз. Январь — срок Яна." in out, out
    assert "Яблочный пирог — Инга." in out, out
    assert "Имярек" not in out
