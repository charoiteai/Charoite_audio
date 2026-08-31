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


def test_bare_channel_label_is_substituted_but_not_numbered_prefix(tmp_path):
    """Голый «Собеседник» — канальная метка установок без моделей диаризации
    (audio.SPEAKER): на них перештамповка не работала вовсе (GLM r2 I1).
    При этом голая метка не должна съедать префикс нумерованной."""
    live, mpath = _make(
        tmp_path, "Участники: Собеседник, Собеседник 2\n- [ ] **Собеседник** — прислать смету\n")
    rebuild_transcript.restamp_minutes(live, {"Собеседник": "Инга"})
    out = mpath.read_text(encoding="utf-8")
    assert "Участники: Инга, Собеседник 2" in out, out
    assert "- [ ] **Инга** — прислать смету" in out


def test_live_session_names_sanitizes_broken_live_json():
    """Битый live.json («names» — список, число вместо имени) не должен
    ронять перештамповку после уже записанной стенограммы (DS r2 M1)."""
    assert rebuild_transcript.live_session_names({"names": ["x"]}) == {}
    assert rebuild_transcript.live_session_names({}) == {}
    got = rebuild_transcript.live_session_names(
        {"names": {"Собеседник 2": "Инга", "Собеседник 3": 5, 4: "Марк", "Собеседник 5": "  "}})
    assert got == {"Собеседник 2": "Инга"}


def test_rebuild_wires_live_names_into_restamp():
    """Контракт на проводку: rebuild обязан отдавать в restamp именно словарь
    ЖИВОЙ сессии — откат на пересборочный `names` возвращал бы Critical со
    смешанной нумерацией при зелёных юнитах (GLM r2 M4)."""
    src = (SRC / "rebuild_transcript.py").read_text(encoding="utf-8")
    fn = src[src.index("def rebuild("):]
    assert "restamp_minutes(live, live_session_names(meta))" in fn, (
        "в restamp должны идти имена живой сессии (live.json), не пересборочные")
