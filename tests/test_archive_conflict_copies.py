"""Конфликтные копии «Имя N» в архиве встреч (№361).

В iCloud-графе 23.09 лежало 7594 копии «Минутки 2.md … Минутки 12.md». Наш код
таких имён не создаёт; копии нашлись ровно у файлов, которые архиватор писал
`copy2` поверх существующего пути на каждом проходе, даже без изменений.
Здесь закреплено поведение, а не написание: писатели графа не трогают
неизменённое и не пишут на месте, копии находит один детектор, убирает второе
правило ночного дедупа со своим разрешением, сигналит доктор.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import dedup_graph as dg  # noqa: E402
import meeting_archive as ma  # noqa: E402
import safe_write  # noqa: E402


def _sig(p: Path) -> tuple[int, int]:
    st = p.stat()
    return (st.st_ino, st.st_mtime_ns)


# --- safe_write: не трогать неизменённое, не писать на месте -----------------

def test_write_text_if_changed_leaves_same_text_untouched(tmp_path):
    p = tmp_path / "Граф.md"
    p.write_text("текст\n", encoding="utf-8")
    before = _sig(p)
    assert safe_write.write_text_if_changed(p, "текст\n") is False
    assert _sig(p) == before, "тот же текст переписан — iCloud получил бы новую версию"


def test_write_text_if_changed_replaces_changed_text_not_in_place(tmp_path):
    p = tmp_path / "Граф.md"
    p.write_text("старый\n", encoding="utf-8")
    ino = p.stat().st_ino
    assert safe_write.write_text_if_changed(p, "новый\n") is True
    assert p.read_text(encoding="utf-8") == "новый\n"
    assert p.stat().st_ino != ino, "запись на месте: у копий в архиве была именно она"


def test_copy_if_changed_skips_identical_bytes(tmp_path):
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("минутки\n", encoding="utf-8")
    dst.write_text("минутки\n", encoding="utf-8")
    before = _sig(dst)
    assert safe_write.copy_if_changed(src, dst) is False
    assert _sig(dst) == before


def test_copy_if_changed_takes_source_times_and_replaces(tmp_path):
    """Свежесть архива читают по mtime (`summary_adoptable`): копия получает
    время источника, как было с `copy2`, но уже не на месте."""
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("новые минутки\n", encoding="utf-8")
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dst.write_text("старые\n", encoding="utf-8")
    ino = dst.stat().st_ino
    assert safe_write.copy_if_changed(src, dst) is True
    assert dst.read_text(encoding="utf-8") == "новые минутки\n"
    assert dst.stat().st_mtime == pytest.approx(1_700_000_000)
    assert dst.stat().st_ino != ino, "копия записана на месте"


def test_copy_if_changed_creates_missing_target(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("x\n", encoding="utf-8")
    dst = tmp_path / "папка" / "dst.md"
    assert safe_write.copy_if_changed(src, dst) is True
    assert dst.read_text(encoding="utf-8") == "x\n"
    assert not list(dst.parent.glob("*.tmp*")), "временный файл остался в графе"


# --- архиватор: повторный проход без новостей папку не трогает ---------------

@pytest.fixture(autouse=True)
def _no_live_model(monkeypatch):
    """Саммари без модели: архивация не должна ходить в живой сервер."""
    monkeypatch.setattr(ma, "_gen_summary", lambda *a, **k: None)


def _meeting(tmp_path: Path) -> tuple[Path, Path]:
    graph = tmp_path / "граф"
    (graph / ma.ARCHIVE_DIR).mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Встречи" / "2026-08-03_1130.md").write_text("# встреча\n", encoding="utf-8")
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-03_1130_Планёрка.md").write_text("стенограмма\n", encoding="utf-8")
    (tdir / "2026-08-03_1130_Планёрка_minutes.md").write_text("## Решения\n- да\n", encoding="utf-8")
    (tdir / "2026-08-03_1130_Планёрка_hints.md").write_text("подсказки\n", encoding="utf-8")
    return graph, tdir


def test_second_archive_pass_without_changes_writes_nothing(tmp_path):
    graph, tdir = _meeting(tmp_path)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка").folder
    files = sorted(p for p in folder.iterdir() if p.is_file())
    files.append(graph / ma.ARCHIVE_DIR / "_ОГЛАВЛЕНИЕ.md")
    before = {p.name: _sig(p) for p in files}
    assert {"Стенограмма.md", "Минутки.md", "Граф.md", "meeting.meta.json",
            "Открыть в Obsidian.command"} <= set(before)

    ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                       files_key="2026-08-03_1130_Планёрка")

    changed = [p.name for p in files if _sig(p) != before[p.name]]
    assert not changed, f"повторный проход без изменений переписал: {changed}"
    assert not list(folder.glob("* [0-9].*")), "в папке появились копии «Имя N»"


def test_changed_material_is_the_only_file_rewritten(tmp_path):
    graph, tdir = _meeting(tmp_path)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка").folder
    hints_before = _sig(folder / "Подсказки и ответы.md")
    (tdir / "2026-08-03_1130_Планёрка.md").write_text("стенограмма, исправленная\n", encoding="utf-8")

    ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                       files_key="2026-08-03_1130_Планёрка")

    assert (folder / "Стенограмма.md").read_text(encoding="utf-8") == "стенограмма, исправленная\n"
    assert (folder / "Стенограмма.md").stat().st_mtime == \
        pytest.approx((tdir / "2026-08-03_1130_Планёрка.md").stat().st_mtime)
    assert _sig(folder / "Подсказки и ответы.md") == hints_before


def test_manifest_is_not_rewritten_for_a_new_timestamp_only(tmp_path):
    graph, tdir = _meeting(tmp_path)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка").folder
    meta = folder / "meeting.meta.json"
    before = _sig(meta)
    ma._write_manifest(folder, "2026-08-03_1130", "Планёрка")
    assert _sig(meta) == before, "манифест переписан ради одного updated_at"


def test_extra_source_is_written_once_and_wins(tmp_path, monkeypatch):
    """Ревизию в папку кладёт только архиватор: источник, названный
    вызывающим, главнее найденного по ключу, и путь пишется один раз."""
    graph, tdir = _meeting(tmp_path)
    (tdir / "2026-08-03_1130_Планёрка_ревизия_claude.md").write_text("по ключу\n", encoding="utf-8")
    named = tdir / "2026-08-03_1130_ревизия_claude.md"
    named.write_text("названная\n", encoding="utf-8")
    calls: list[str] = []
    real = safe_write.copy_if_changed

    def spy(src, dst):
        calls.append(dst.name)
        return real(src, dst)

    monkeypatch.setattr(safe_write, "copy_if_changed", spy)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка",
                                extra={"Ревизия Claude.md": named}).folder
    assert (folder / "Ревизия Claude.md").read_text(encoding="utf-8") == "названная\n"
    assert calls.count("Ревизия Claude.md") == 1, calls


# --- детектор: только документы встреч и только имена архива -----------------

def _archive_folder(graph: Path) -> Path:
    d = graph / ma.ARCHIVE_DIR / "2026-09-03 16-05 — Расчёт"
    d.mkdir(parents=True)
    return d


def test_conflict_copies_finds_archive_and_docs_copies_only(tmp_path):
    graph = tmp_path
    d = _archive_folder(graph)
    (d / "Минутки.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 2.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 3.md").write_text("иначе\n", encoding="utf-8")
    (d / "Открыть в Obsidian.command").write_text("open\n", encoding="utf-8")
    (d / "Открыть в Obsidian 2.command").write_text("open\n", encoding="utf-8")
    (d / "Заметка.md").write_text("з\n", encoding="utf-8")          # не имя архива
    (d / "Заметка 2.md").write_text("з\n", encoding="utf-8")
    (d / "Минутки 1.md").write_text("м\n", encoding="utf-8")        # iCloud «1» не создаёт
    docs = graph / "Документация" / "Стенограммы встреч"
    docs.mkdir(parents=True)
    (docs / "2026-09-03_1605_minutes.md").write_text("д\n", encoding="utf-8")
    (docs / "2026-09-03_1605_minutes 2.md").write_text("д\n", encoding="utf-8")
    nodes = graph / "Системы"
    nodes.mkdir()
    (nodes / "Спринт.md").write_text("узел\n", encoding="utf-8")
    (nodes / "Спринт 2.md").write_text("узел\n", encoding="utf-8")  # узел с числом — законный
    parked = graph / ma.ARCHIVE_DIR / "_дубли" / "папка"
    parked.mkdir(parents=True)
    (parked / "Минутки.md").write_text("м\n", encoding="utf-8")
    (parked / "Минутки 2.md").write_text("м\n", encoding="utf-8")

    found = {c.copy.name: c.same for c in ma.conflict_copies(graph)}

    assert found == {"Минутки 2.md": True, "Минутки 3.md": False,
                     "Открыть в Obsidian 2.command": True, "2026-09-03_1605_minutes 2.md": True}
    assert all(c.same is None for c in ma.conflict_copies(graph, compare=False))


def test_conflict_copies_skip_symlinks(tmp_path):
    d = _archive_folder(tmp_path)
    (d / "Минутки.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 2.md").symlink_to(d / "Минутки.md")
    assert ma.conflict_copies(tmp_path) == []


# --- второе правило ночного дедупа -------------------------------------------

@pytest.fixture()
def copies_graph(tmp_path, monkeypatch):
    """Граф с одной побайтной копией, одной отличающейся и парой от 4 КБ для
    первого правила; резерв — в корне данных теста, вне графа."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(dg, "_root", lambda: data_root)
    monkeypatch.setattr(dg, "_cfg", lambda: {})
    graph = tmp_path / "граф"
    d = _archive_folder(graph)
    (d / "Минутки.md").write_text("минутки\n", encoding="utf-8")
    (d / "Минутки 2.md").write_text("минутки\n", encoding="utf-8")
    (d / "Разбор.md").write_text("разбор\n", encoding="utf-8")
    (d / "Разбор 2.md").write_text("разбор, другая версия\n", encoding="utf-8")
    big = "# стенограмма\n" + "реплика. " * 800
    docs = graph / "Документация" / "Стенограммы встреч"
    docs.mkdir(parents=True)
    (docs / "2026-09-03_1605.md").write_text(big, encoding="utf-8")
    (d / "Стенограмма.md").write_text(big, encoding="utf-8")
    return graph, d, data_root


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["dedup_graph.py", *argv])
    return dg.main()


def test_dry_run_moves_nothing(copies_graph, monkeypatch, capsys):
    graph, d, _ = copies_graph
    _run(monkeypatch, "--graph", str(graph))
    assert (d / "Минутки 2.md").exists()
    assert "будут убраны с --apply-copies" in capsys.readouterr().out


def test_apply_copies_parks_identical_copy_outside_the_graph(copies_graph, monkeypatch):
    graph, d, data_root = copies_graph
    _run(monkeypatch, "--graph", str(graph), "--apply-copies")

    assert not (d / "Минутки 2.md").exists()
    assert (d / "Минутки.md").read_text(encoding="utf-8") == "минутки\n"
    assert (d / "Разбор 2.md").exists(), "отличающуюся копию решает человек"
    parked = list(data_root.rglob("Минутки 2.md"))
    assert len(parked) == 1 and parked[0].read_text(encoding="utf-8") == "минутки\n"
    assert graph not in parked[0].parents, "резерв внутри графа — его читает вкладка «Задачи»"
    manifest = next(data_root.rglob("manifest.tsv"))
    assert "Минутки 2.md" in manifest.read_text(encoding="utf-8")


def test_apply_copies_does_not_link_and_apply_does_not_park(copies_graph, monkeypatch):
    """Два правила — два разрешения (Critical Opus входного круга №361)."""
    graph, d, _ = copies_graph
    docs_copy = graph / "Документация" / "Стенограммы встреч" / "2026-09-03_1605.md"
    _run(monkeypatch, "--graph", str(graph), "--apply-copies")
    assert docs_copy.stat().st_ino != (d / "Стенограмма.md").stat().st_ino, \
        "--apply-copies включил жёсткие ссылки"

    (d / "Минутки 2.md").write_text("минутки\n", encoding="utf-8")
    _run(monkeypatch, "--graph", str(graph), "--apply")
    assert (d / "Минутки 2.md").exists(), "--apply убрал копию без своего разрешения"


def test_config_keys_are_separate_and_strict(copies_graph, monkeypatch):
    graph, d, _ = copies_graph
    for value, allowed in [(True, True), (False, False), ("true", False), (1, False), (None, False)]:
        monkeypatch.setattr(dg, "_cfg", lambda v=value: {"sufler": {"dedup_copies": v}})
        assert dg._allowed_by_config("dedup_copies") is allowed
        assert dg._allowed_by_config("dedup_files") is False, "ключ копий открыл ссылки"
    monkeypatch.setattr(dg, "_cfg", lambda: {"sufler": {"dedup_copies": True}})
    _run(monkeypatch, "--graph", str(graph))
    assert not (d / "Минутки 2.md").exists()


def test_hardlinked_copy_is_parked_as_an_independent_file(copies_graph, monkeypatch):
    """Перенос `rename` сохранил бы inode: правка живого оригинала меняла бы
    резерв (Important Opus входного круга №361)."""
    graph, d, data_root = copies_graph
    (d / "Минутки 2.md").unlink()
    os.link(d / "Минутки.md", d / "Минутки 2.md")
    _run(monkeypatch, "--graph", str(graph), "--apply-copies")
    parked = next(data_root.rglob("Минутки 2.md"))
    assert parked.stat().st_ino != (d / "Минутки.md").stat().st_ino
    (d / "Минутки.md").write_text("правка оригинала\n", encoding="utf-8")
    assert parked.read_text(encoding="utf-8") == "минутки\n"


def test_copy_changed_after_scan_is_left_in_place(copies_graph, tmp_path):
    graph, d, _ = copies_graph
    found = [c for c in ma.conflict_copies(graph) if c.copy.name == "Минутки 2.md"][0]
    (d / "Минутки 2.md").write_text("успели поправить\n", encoding="utf-8")
    dest = tmp_path / "резерв"
    dest.mkdir()
    with (dest / "manifest.tsv").open("a", encoding="utf-8") as mf:
        status = dg.park_copy(graph, found, dest, mf)
    assert status.startswith("изменился")
    assert (d / "Минутки 2.md").read_text(encoding="utf-8") == "успели поправить\n"


# --- ночь и доктор -----------------------------------------------------------

def test_nightly_never_hardcodes_any_apply():
    """Ночь не решает за человека: ни одна строка запуска дедупа не несёт
    `--apply*`. Смотрим все некомментарные строки, а не первую попавшуюся
    (Important Opus входного круга №361)."""
    nightly = (ROOT / "scripts" / "nightly.sh").read_text(encoding="utf-8")
    runs = [ln for ln in nightly.splitlines()
            if "dedup_graph.py" in ln and not ln.lstrip().startswith("#")]
    assert runs, "шаг дедупа пропал из ночи"
    assert not [ln for ln in runs if "--apply" in ln], runs


def test_nightly_runs_dedup_before_heavy_steps_and_without_cutoff():
    """Шаг стоит секунды, а в конце ночи его 12 ночей подряд срезал потолок."""
    nightly = (ROOT / "scripts" / "nightly.sh").read_text(encoding="utf-8")
    assert nightly.index('step "dedup graph files"') < nightly.index('step "tier3 cores"')
    assert 'skip_late "дедупликация' not in nightly


def test_doctor_warns_about_conflict_copies(tmp_path):
    import graph_doctor
    graph = tmp_path / "граф"
    for sub in ("Люди", "Системы", "Ядра", "Встречи"):
        (graph / sub).mkdir(parents=True)
    d = _archive_folder(graph)
    (d / "Минутки.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 2.md").write_text("м\n", encoding="utf-8")
    rep = graph_doctor.inspect(graph)
    assert rep["conflict_copies"] == 1
    assert any("конфликтных копий" in w for w in rep["warnings"])
