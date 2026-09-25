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
import meeting_stamp  # noqa: E402
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
def _no_live_model(модель_не_отвечает):
    """Саммари без модели: архивация не должна ходить в живой сервер.

    Прежняя подмена `ma._gen_summary` с №314 не срабатывала — архиватор зовёт
    `summary_pass`, и тесты ходили в `/api/chat` (№376)."""
    return модель_не_отвечает


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


def test_second_archive_pass_without_changes_writes_nothing(tmp_path, _no_live_model):
    graph, tdir = _meeting(tmp_path)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка").folder
    # сценарий «модели нет» проверен, только если модель спрашивали — и о
    # материалах этой встречи (круг 1 по PR №624, Opus M2)
    assert any("## Решения\n- да" in п for п in _no_live_model), _no_live_model
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


# --- круг 2 по коду (№361) ------------------------------------------------------

def test_copy_takes_source_permissions_like_copy2(tmp_path):
    """Копия — производная источника: права от него, как у `copy2` (критика
    Sonnet круга 2)."""
    src, dst = tmp_path / "src.md", tmp_path / "dst.md"
    src.write_text("новое\n", encoding="utf-8")
    dst.write_text("старое\n", encoding="utf-8")
    src.chmod(0o640)
    dst.chmod(0o600)
    safe_write.copy_if_changed(src, dst)
    assert (dst.stat().st_mode & 0o777) == 0o640


def test_topic_twin_does_not_rewrite_the_folder_on_every_pass(tmp_path):
    """Двойник по теме («…_Бюджет_MVP») под тем же ключом «…_Бюджет»: два
    источника на одно имя переписывали путь дважды на каждом проходе
    (Important Opus круга 2). Побеждает точное имя, второй проход тих."""
    graph, tdir = _meeting(tmp_path)
    key = "2026-08-03_1130_Планёрка"
    (tdir / f"{key}_Итоги.md").write_text("стенограмма двойника\n", encoding="utf-8")
    # «_Alpha» сортируется раньше точного «_minutes»: без правила «точное главнее»
    # победил бы двойник
    (tdir / f"{key}_Alpha_minutes.md").write_text("минутки двойника\n", encoding="utf-8")
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка", files_key=key).folder
    assert (folder / "Стенограмма.md").read_text(encoding="utf-8") == "стенограмма\n"
    assert (folder / "Минутки.md").read_text(encoding="utf-8") == "## Решения\n- да\n"
    before = {p.name: _sig(p) for p in folder.iterdir() if p.is_file()}
    ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка", files_key=key)
    after = {p.name: _sig(p) for p in folder.iterdir() if p.is_file()}
    assert before == after


def test_debrief_is_planned_once(tmp_path):
    """Разбор, найденный и по ключу, и как `expected_debrief`, в плане один
    (Minor Sonnet круга 2)."""
    graph, tdir = _meeting(tmp_path)
    key = "2026-08-03_1130_Планёрка"
    debrief = tdir / f"{key}_разбор.md"
    debrief.write_text("разбор\n", encoding="utf-8")
    plan = ma.plan_materials(tdir, key, debrief)
    assert plan["Разбор.md"] == debrief
    assert list(plan).count("Разбор.md") == 1


def test_title_comes_from_the_stem():
    """Одно правило названия для всех вызывающих (Minor Opus круга 2)."""
    assert meeting_stamp.title_from_stem("2026-08-03_1130_Бюджет_MVP") == "Бюджет MVP"
    assert meeting_stamp.title_from_stem("2026-07-15_140030") == ""       # секунды темой не становятся
    assert meeting_stamp.title_from_stem("2026-07-15_1400") == ""
    assert meeting_stamp.title_from_stem("заметка") == ""


def test_unreadable_copy_is_neither_parked_nor_called_different(copies_graph, monkeypatch, capsys):
    """Нечитаемая копия — не «отличается» (Minor Opus круга 2)."""
    graph, d, _ = copies_graph
    blocked = d / "Минутки 2.md"
    blocked.chmod(0o000)
    try:
        found = {c.copy.name: c.same for c in ma.conflict_copies(graph)}
        assert found["Минутки 2.md"] is None
        _run(monkeypatch, "--graph", str(graph), "--apply-copies")
        assert blocked.exists()
        assert "не прочитаны (1)" in capsys.readouterr().out
    finally:
        blocked.chmod(0o644)


def test_copy_replaced_while_parking_is_left_and_not_logged(copies_graph, tmp_path, monkeypatch):
    """iCloud подменил копию, пока её копировали в резерв: удалять нельзя, в
    резерве не та версия (Important Opus круга 2)."""
    graph, d, _ = copies_graph
    found = [c for c in ma.conflict_copies(graph) if c.copy.name == "Минутки 2.md"][0]
    real_copy2 = dg.shutil.copy2

    def copy_then_icloud_replaces(src, dst, *a, **k):
        real_copy2(src, dst, *a, **k)
        tmp = Path(src).with_name("подмена.tmp")
        tmp.write_text("серверная версия\n", encoding="utf-8")
        os.replace(tmp, src)                     # новый inode — как докачка iCloud

    monkeypatch.setattr(dg.shutil, "copy2", copy_then_icloud_replaces)
    dest = tmp_path / "резерв"
    dest.mkdir()
    with (dest / "manifest.tsv").open("a", encoding="utf-8") as mf:
        status = dg.park_copy(graph, found, dest, mf)
    assert status.startswith("изменился")
    assert (d / "Минутки 2.md").read_text(encoding="utf-8") == "серверная версия\n"
    assert not list(dest.rglob("Минутки 2.md")), "в резерве осталась не та версия"
    assert (dest / "manifest.tsv").read_text(encoding="utf-8") == ""


def test_copy_replaced_right_before_unlink_is_kept(copies_graph, tmp_path):
    graph, d, _ = copies_graph
    found = [c for c in ma.conflict_copies(graph) if c.copy.name == "Минутки 2.md"][0]

    class ManifestThatRaces:
        """Файл подменяют сразу после записи строки — перед самым unlink."""
        def __init__(self):
            self.lines: list[str] = []
        def write(self, s):
            self.lines.append(s)
            if len(self.lines) == 1:
                tmp = d / "подмена.tmp"
                tmp.write_text("серверная версия\n", encoding="utf-8")
                os.replace(tmp, d / "Минутки 2.md")
        def flush(self):
            pass

    dest = tmp_path / "резерв"
    dest.mkdir()
    mf = ManifestThatRaces()
    status = dg.park_copy(graph, found, dest, mf)
    assert "копия оставлена" in status
    assert (d / "Минутки 2.md").read_text(encoding="utf-8") == "серверная версия\n"
    assert any(line.startswith("# оставлена:") for line in mf.lines)


def test_all_graphs_cleans_every_graph(tmp_path, monkeypatch):
    """Перечень графов у уборки тот же, что у доктора (Important Opus круга 2)."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(dg, "_root", lambda: data_root)
    monkeypatch.setattr(dg, "_cfg", lambda: {})
    graphs_ = []
    for name in ("работа", "личное"):
        g = tmp_path / name
        d = g / ma.ARCHIVE_DIR / "2026-09-03 16-05 — Встреча"
        d.mkdir(parents=True)
        (d / "Минутки.md").write_text("м\n", encoding="utf-8")
        (d / "Минутки 2.md").write_text("м\n", encoding="utf-8")
        graphs_.append((g, d))
    monkeypatch.setattr(dg.graphs, "all_graphs", lambda marker: [g for g, _ in graphs_])
    _run(monkeypatch, "--all-graphs", "--apply-copies")
    assert all(not (d / "Минутки 2.md").exists() for _, d in graphs_)


def test_doctor_warns_only_when_copies_grow(tmp_path):
    """Оставленные человеку копии не горят каждую ночь — сигнал на рост
    (критика Opus круга 2)."""
    import graph_doctor
    graph = tmp_path / "граф"
    for sub in ("Люди", "Системы", "Ядра", "Встречи"):
        (graph / sub).mkdir(parents=True)
    d = _archive_folder(graph)
    (d / "Минутки.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 2.md").write_text("м\n", encoding="utf-8")

    def warned(prev):
        rep = graph_doctor.inspect(graph, prev_copies=prev)
        return [w for w in rep["warnings"] if "конфликтных копий" in w]

    assert warned(None), "первый отчёт молчит о копиях"
    assert not warned(1), "то же число копий горит каждую ночь"
    grew = warned(0)
    assert grew and "(было 0)" in grew[0]


# --- выжившие мутанты CI-мутатора по PR #611 ------------------------------------

def test_opener_is_executable(tmp_path):
    """Ярлык из Finder открывает Obsidian, только если он исполняемый."""
    graph, tdir = _meeting(tmp_path)
    folder = ma.archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка",
                                files_key="2026-08-03_1130_Планёрка").folder
    assert ((folder / "Открыть в Obsidian.command").stat().st_mode & 0o777) == 0o755


def test_same_bytes_is_a_strict_bool(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("абв", encoding="utf-8")
    assert safe_write._same_bytes(p, b"x") is False          # другой размер
    assert safe_write._same_bytes(p, "абв".encode()) is True
    assert safe_write._same_bytes(tmp_path / "нет.md", b"x") is False


def test_park_copies_reports_what_it_moved(copies_graph, monkeypatch, capsys):
    graph, d, _ = copies_graph
    _run(monkeypatch, "--graph", str(graph), "--apply-copies")
    out = capsys.readouterr().out
    assert "убрано конфликтных копий «Имя N»: 1 из 1" in out
    assert "не удалось" not in out


def test_main_without_graph_is_a_quiet_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dg, "_root", lambda: tmp_path)          # нет config.yaml — графа нет
    monkeypatch.setattr(sys, "argv", ["dedup_graph.py"])
    assert dg.main() == 0
    assert "граф не найден" in capsys.readouterr().out


def _doctor_graph(tmp_path: Path) -> Path:
    graph = tmp_path / "граф"
    for sub in ("Люди", "Системы", "Ядра", "Встречи"):
        (graph / sub).mkdir(parents=True)
    d = _archive_folder(graph)
    (d / "Минутки.md").write_text("м\n", encoding="utf-8")
    (d / "Минутки 2.md").write_text("м\n", encoding="utf-8")
    return graph


def test_doctor_counts_copies_by_name_without_reading(tmp_path, monkeypatch):
    """Доктор идёт дважды за ночь по всем графам: считает по именам, байты не
    читает — иначе выкачивал бы из iCloud выгруженные файлы."""
    import graph_doctor
    graph = _doctor_graph(tmp_path)

    def must_not_read(*a, **k):
        raise AssertionError("доктор читает содержимое копий")

    monkeypatch.setattr(ma, "_same_content", must_not_read)
    assert graph_doctor.inspect(graph)["conflict_copies"] == 1


def test_doctor_main_remembers_the_count_between_runs(tmp_path, monkeypatch, capsys):
    """Через main, как зовёт ночь: первый отчёт предупреждает, второй с тем же
    числом копий молчит — прошлое число берётся из отчёта по умолчанию."""
    import graph_doctor
    graph = _doctor_graph(tmp_path)
    report = tmp_path / "logs" / "graph_doctor.json"
    monkeypatch.setattr(graph_doctor, "report_path", lambda: report)
    monkeypatch.setattr(sys, "argv", ["graph_doctor.py", "--graph", str(graph)])
    graph_doctor.main()
    first = capsys.readouterr().out
    graph_doctor.main()
    second = capsys.readouterr().out
    assert "конфликтных копий" in first
    assert "конфликтных копий" not in second
    d = graph / ma.ARCHIVE_DIR / "2026-09-03 16-05 — Расчёт"
    (d / "Минутки 3.md").write_text("м\n", encoding="utf-8")
    graph_doctor.main()
    third = capsys.readouterr().out
    assert "конфликтных копий «Имя N» в документах встреч: 2 (было 1)" in third


def test_revision_delivery_names_the_archive_folder(tmp_path):
    """Лог доставки называет папку архива — по нему разбирают, куда легла ревизия."""
    import cloud_review
    import io
    graph = tmp_path / "граф"
    (graph / ma.ARCHIVE_DIR).mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Документация" / "Стенограммы встреч").mkdir(parents=True)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-07-15_1400_Платёжный_провайдер.md"
    transcript.write_text("стенограмма\n", encoding="utf-8")
    rev = tdir / "2026-07-15_1400_Платёжный_провайдер_ревизия_claude.md"
    rev.write_text("# Ревизия\n", encoding="utf-8")
    buf = io.StringIO()
    cloud_review.deliver_review(rev, transcript, graph, "2026-07-15_1400", buf)
    assert "ревизия доставлена: архив 2026-07-15 14-00 — Платёжный провайдер" in buf.getvalue()


def test_vault_docs_copy_writes_only_changes(tmp_path):
    import graph_updater
    graph = tmp_path / "граф"
    (graph / "Документация").mkdir(parents=True)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    tpath = tdir / "2026-08-03_1130_Планёрка.md"
    tpath.write_text("стенограмма\n", encoding="utf-8")
    (tdir / "2026-08-03_1130_Планёрка_minutes.md").write_text("минутки\n", encoding="utf-8")
    vdocs = graph_updater.copy_to_vault_docs(tpath, graph)
    assert vdocs == graph / "Документация" / "Стенограммы встреч"
    copied = vdocs / "2026-08-03_1130_Планёрка_minutes.md"
    assert copied.read_text(encoding="utf-8") == "минутки\n"
    before = _sig(copied)
    graph_updater.copy_to_vault_docs(tpath, graph)
    assert _sig(copied) == before
    assert graph_updater.copy_to_vault_docs(tpath, tmp_path / "без-документации") is None
