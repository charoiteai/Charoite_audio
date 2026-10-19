"""Одна очередь досье на все графы и бюджет шага по времени (№621)."""
from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import shutil

import pytest

import live_gate
from charoite_graph import dossier, safe_write
from charoite_schema import CHAROITE

_REAL_WINDOW = live_gate.night_window_open   # до подмены фикстурой `nd`

GOOD = ("## Сейчас\nновое тело\n## Как пришли\nт\n## Решено\nт\n"
        "## Открыто\nт\n## Кто в теме\nт")


def _script():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts/nightly_dossier.py"
    spec = importlib.util.spec_from_file_location("nightly_dossier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _graph(root: pathlib.Path, name: str, themes: dict[str, str | None]) -> pathlib.Path:
    """Граф с темами: значение — дата `собрано:` прежнего досье, None — досье нет."""
    graph = root / name
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    folder = graph / CHAROITE.dossier_dir
    folder.mkdir()
    for theme, built in themes.items():
        (graph / "Ядра" / f"{theme}.md").write_text(f"# {theme}\nядро\n", encoding="utf-8")
        for i in (1, 2):          # тема — ядро и не меньше двух источников (MIN_CLUSTER)
            (graph / "Встречи" / f"{theme}_{i}.md").write_text(
                f"# Встреча\n[[Ядра/{theme}]] источник\n", encoding="utf-8")
        if built:
            (folder / f"{theme}.md").write_text(
                f"---\nтема: {theme}\nотпечаток: старый\nсобрано: {built}\n---\n"
                f"# {theme}\n## Сейчас\nстарое тело\n## Правки автора\n\nрешение автора\n",
                encoding="utf-8")
    return graph


class _Clock:
    def __init__(self):
        self.t = 1000.0   # не ноль: «плюс вместо минус» у отсчёта от старта виден (мутанты #819)

    def __call__(self):
        return self.t


@pytest.fixture
def nd(monkeypatch):
    mod = _script()
    clock = _Clock()
    # потолок ночи разработчика или CI не протекает в тесты бюджета (выход out1 №749, Haiku M3)
    monkeypatch.delenv(mod.live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    monkeypatch.setattr(mod, "clock", clock)
    monkeypatch.setattr(mod.live_gate, "night_window_open", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_graph_lock", lambda *a: contextlib.nullcontext(True))
    mod.calls = []

    def generate(theme, members, files, c, **kw):
        mod.calls.append(theme)
        clock.t += 30
        return GOOD

    monkeypatch.setattr(mod, "generate", generate)
    mod.fake_clock = clock
    return mod


def _cand(nd, g, theme, cls, built="", size=1):
    return nd.Candidate(g, theme, ["x"] * size, "fp", cls, built,
                        dossier.DossierLoad("missing", None), None)


def test_small_graph_is_not_behind_every_new_theme_of_the_big_one(nd):
    """Вход in3 №621, DS C1: при классе старше круга первая тема малого графа
    стояла на позиции 600."""
    big = nd._GraphRun(pathlib.Path("Большой"), {}, {}, "2026-10-18", "s")
    small = nd._GraphRun(pathlib.Path("Малый"), {}, {}, "2026-10-18", "s")
    cands = [_cand(nd, big, f"т{i:03}", nd.NEW) for i in range(600)]
    cands += [_cand(nd, small, "старая", nd.CHANGED, "2026-07-30"),
              _cand(nd, small, "свежая", nd.CHANGED, "2026-08-25")]
    order = nd.queue_order(cands)
    assert [c.g.graph.name for c in order[:5]] == ["Большой", "Малый", "Большой", "Малый", "Большой"]
    assert [c.theme for c in order if c.g is small] == ["старая", "свежая"]
    assert len(order) == 602


def test_inside_a_graph_new_then_oldest_then_largest(nd):
    g = nd._GraphRun(pathlib.Path("Г"), {}, {}, "2026-10-18", "s")
    cands = [_cand(nd, g, "крупная", nd.CHANGED, "2026-08-01", size=9),
             _cand(nd, g, "мелкая", nd.CHANGED, "2026-08-01", size=2),
             _cand(nd, g, "давняя", nd.CHANGED, "2026-07-01", size=1),
             _cand(nd, g, "новая", nd.NEW, "", size=1),
             _cand(nd, g, "как есть", nd.SAME, "2026-06-01", size=50)]
    assert [c.theme for c in nd.queue_order(cands)] == ["новая", "давняя", "крупная", "мелкая", "как есть"]


def test_budget_stops_the_queue_and_keeps_the_index(nd, tmp_path, capsys):
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(6)})
    # 40 с на старте, дальше среднее 30 с: темы на 0, 30 и 60 с; на 90 с остаток 10 < 30
    r = nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=100)
    assert r["собрано"] == 3 and r["не_успели"] == 3 and r["пропущено"] == 3, r
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {
        f"тема{i}" for i in range(6)}
    assert "бюджет шага досье вышел" in capsys.readouterr().out


def test_full_without_budget_ignores_the_limit_and_takes_unchanged_themes(nd, tmp_path):
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(4)})
    nd.run(graph, {}, full=False, dry=False, limit=None)          # отпечатки совпали
    nd.calls.clear()
    r = nd.run(graph, {}, full=False, dry=False, limit=None)
    assert r["без_изменений"] == 4 and r["пропущено"] == 4 and nd.calls == []
    r = nd.run(graph, {}, full=True, dry=False, limit=0, budget_s=None)
    assert r["собрано"] == 4 and len(nd.calls) == 4, r


def test_full_with_a_budget_is_cut_and_the_rest_waits(nd, tmp_path, capsys):
    """№749: --full с бюджетом режется им же, остаток — следующей ночью; прежде
    `_drive` бюджет у --full выбрасывал, и пересборка шла до потолка ночи."""
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(6)})
    r = nd.run(graph, {}, full=True, dry=False, limit=None, budget_s=100)
    assert r["собрано"] == 3 and r["не_успели"] == 3, r
    assert "бюджет шага досье вышел" in capsys.readouterr().out


@pytest.mark.parametrize("given, full, expected", [
    (None, False, 2400.0),     # умолчание будней
    (None, True, None),        # ручная пересборка «всего» — без бюджета
    (0.0, True, 2400.0),       # ноль — забытое значение и у --full (вход in1 №749, DS M1)
    (-5.0, False, 2400.0),
    (7200.0, True, 7200.0),
    (float("nan"), True, 2400.0),     # nan и inf — не «никогда» и не «вечно» (выход out1, Haiku M2)
    (float("inf"), False, 2400.0),
])
def test_budget_is_resolved_once(nd, monkeypatch, given, full, expected):
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV, "1")   # потолок в бюджет не подмешивается
    assert nd.resolve_budget(given, full) == expected


class _Wall:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _main_full(nd, tmp_path, monkeypatch, graph, *extra):
    monkeypatch.setattr(nd, "_root", lambda: tmp_path)     # лока встречи нет — окно не ждёт
    monkeypatch.setattr(nd, "cfg", lambda: {})
    monkeypatch.setattr(nd.signal, "signal", lambda *a: None)
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--full", *extra])
    return nd.main()


def test_sunday_full_leaves_the_night_tail_its_time(nd, tmp_path, monkeypatch, capsys):
    """№749, сценарий ночи: потолок через «резерв хвоста + 100 с», --full без
    бюджета. Шаг обязан остановиться по бюджету, урезанному до потолка минус
    резерв, — а не дойти до потолка и отдать хвост ночи в «(поздно)». Окно
    ночи — настоящее (`night_window_open` с потолком в окружении, вход in1,
    DS I6)."""
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(6)})
    monkeypatch.setattr(nd.live_gate, "night_window_open", _REAL_WINDOW)
    wall = _Wall(1.0e9)
    monkeypatch.setattr(nd, "wall_clock", wall)
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV, str(wall.t + nd.live_gate.NIGHT_TAIL_S + 100))

    def generate(theme, members, files, c, **kw):
        nd.calls.append(theme)
        nd.fake_clock.t += 30
        wall.t += 30
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    assert _main_full(nd, tmp_path, monkeypatch, graph) == 0
    out = capsys.readouterr().out
    # 40 с на старте, дальше 30 с: темы на 0, 30 и 60 с; на 90 с остаток < 30
    assert len(nd.calls) == 3, nd.calls
    assert "до конца ночи — только время шагов после досье" in out
    assert "время ночного прогона вышло" not in out
    assert not nd.live_gate.night_is_over(wall)            # ночь ещё идёт — хвосту есть время


def test_sleep_mid_rebuild_does_not_eat_the_night_tail(nd, tmp_path, monkeypatch, capsys):
    """Выход out1 №749 (Sonnet I1, DS I1, Haiku I2): машина проспала посреди
    пересборки — монотонные часы шага стоят, стена ушла. Резерв хвоста,
    посчитанный один раз на старте, сон съедал: шаг шёл до самого потолка.
    Теперь он считается по стене на каждой теме."""
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(10)})
    wall = _Wall(1.0e9)
    until = wall.t + nd.live_gate.NIGHT_TAIL_S + 3600
    monkeypatch.setattr(nd, "wall_clock", wall)
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV, str(until))

    def generate(theme, members, files, c, **kw):
        nd.calls.append(theme)
        nd.fake_clock.t += 30
        wall.t += 30 + (3500 if len(nd.calls) == 2 else 0)   # после второй темы — сон 3500 с
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    assert _main_full(nd, tmp_path, monkeypatch, graph, "--budget-s", "7200") == 0
    # ночь до резерва: 3600 − 2×30 − 3500 = 40 ≥ 30 — третья тема, после неё 10 < 30
    assert len(nd.calls) == 3, nd.calls
    assert wall.t <= until - nd.live_gate.NIGHT_TAIL_S
    assert "до конца ночи — только время шагов после досье" in capsys.readouterr().out


def test_no_night_left_for_the_step_builds_nothing_and_writes_the_index(nd, tmp_path, monkeypatch, capsys):
    """Резерв хвоста съеден до старта шага: ни одной темы, индекс полный, в логе
    — что кончилась ночь, а не бюджет (выход out1 №749, DS I2, DS M1)."""
    import time
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(3)})
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV,
                       str(time.time() + nd.live_gate.NIGHT_TAIL_S - 1))
    assert _main_full(nd, tmp_path, monkeypatch, graph) == 0
    assert nd.calls == []
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {
        f"тема{i}" for i in range(3)}
    assert "до конца ночи — только время шагов после досье" in capsys.readouterr().out


def test_manual_full_without_a_ceiling_rebuilds_everything(nd, tmp_path, monkeypatch):
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(6)})
    monkeypatch.setattr(nd.live_gate, "night_window_open", _REAL_WINDOW)
    monkeypatch.delenv(nd.live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    assert _main_full(nd, tmp_path, monkeypatch, graph) == 0
    assert len(nd.calls) == 6, nd.calls


def test_meeting_wait_eats_the_budget_and_is_checked_after_the_gate(nd, tmp_path, monkeypatch, capsys):
    graph = _graph(tmp_path, "Г", {"одна": None, "другая": None})
    seen = []

    def window(*a, default=None, **k):
        seen.append(default)
        nd.fake_clock.t += 90          # ждали конца встречи
        return True

    monkeypatch.setattr(nd.live_gate, "night_window_open", window)
    r = nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=100)
    assert seen == [100] and nd.calls == [] and r["не_успели"] == 2, (seen, r)
    assert "пока ждали конца встречи" in capsys.readouterr().out


def test_busy_graph_does_not_burn_the_budget_on_each_theme(nd, tmp_path, monkeypatch):
    """Вход in3 №621, Haiku C1: замок ждал пять минут на каждую тему."""
    busy = _graph(tmp_path, "Занятый", {f"з{i}": None for i in range(5)})
    free = _graph(tmp_path, "Свободный", {f"с{i}": None for i in range(2)})
    monkeypatch.setattr(nd, "_graph_lock",
                        lambda graph, *a: contextlib.nullcontext(graph != busy))
    states = nd.run_all([busy, free], {}, full=False, dry=False).graphs
    r = {g.graph.name: g.result() for g in states}
    assert sum(t.startswith("з") for t in nd.calls) == 1, nd.calls
    assert r["Занятый"]["занят"] == 5 and r["Свободный"]["собрано"] == 2
    assert r["Занятый"]["пропущено"] == 5, r      # сводка считает и хвост (мутант #819)


def test_limit_is_per_run_not_per_graph(nd, tmp_path):
    a = _graph(tmp_path, "А", {"а1": None, "а2": None})
    b = _graph(tmp_path, "Б", {"б1": None, "б2": None})
    states = nd.run_all([a, b], {}, full=False, dry=False, limit=1).graphs
    assert sum(g.built for g in states) == 1
    assert sum(g.over_limit for g in states) == 3 and sum(g.skipped for g in states) == 3


def test_crash_mid_queue_still_writes_every_index(nd, tmp_path, monkeypatch):
    a = _graph(tmp_path, "А", {"а1": None, "а2": None})
    b = _graph(tmp_path, "Б", {"б1": "2026-07-20", "б2": None})
    real = safe_write.write_text
    writes = []

    def write(path, text, **kw):
        writes.append(path)
        if len(writes) == 2:
            raise PermissionError(13, "Permission denied")
        return real(path, text, **kw)

    monkeypatch.setattr(nd.safe_write, "write_text", write)
    with pytest.raises(PermissionError):
        nd.run_all([a, b], {}, full=False, dry=False)
    assert {e["тема"] for e in dossier.load_index(a / CHAROITE.dossier_dir)} == {"а1"}
    assert {e["тема"] for e in dossier.load_index(b / CHAROITE.dossier_dir)} == {"б1"}


def test_slow_failures_under_budget_still_exit_2(nd, tmp_path, monkeypatch):
    """Вход in3 №621, Sonnet I1: порог по числу тем (100 // 10) не срабатывал
    на пяти попытках, которые пускает бюджет."""
    graph = _graph(tmp_path, "Г", {f"тема{i:03}": None for i in range(100)})

    def boom(*a, **k):
        nd.fake_clock.t += 30
        raise TimeoutError("стрим молчит")

    monkeypatch.setattr(nd, "generate", boom)
    monkeypatch.setattr(nd, "cfg", lambda: {})
    monkeypatch.setattr(nd.signal, "signal", lambda *a: None)   # обработчик SIGTERM не остаётся в pytest
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--budget-s", "150"])
    assert nd.main() == 2


def test_author_edit_after_scan_is_read_before_the_build(nd, tmp_path, monkeypatch):
    """Вход in3 №621, DS M2: снимок скана живёт весь прогон."""
    graph = _graph(tmp_path, "Г", {"первая": None, "вторая": "2026-07-20"})
    path = graph / CHAROITE.dossier_dir / "вторая.md"

    def generate(theme, *a, **k):
        if theme == "первая":
            path.write_text(path.read_text(encoding="utf-8").replace(
                "решение автора", "правка после скана"), encoding="utf-8")
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    r = nd.run(graph, {}, full=False, dry=False, limit=None)
    assert r["собрано"] == 2 and r["досье_изменилось"] == 0, r
    assert "правка после скана" in path.read_text(encoding="utf-8")


def test_dry_plan_has_no_ceiling_and_names_the_graph(nd, tmp_path, capsys):
    a = _graph(tmp_path, "А", {f"а{i}": None for i in range(30)})
    b = _graph(tmp_path, "Б", {"б1": "2026-07-20"})
    states = nd.run_all([a, b], {}, full=False, dry=True, limit=1).graphs
    assert sum(g.built for g in states) == 31 and nd.calls == []
    plan = [line for line in capsys.readouterr().out.splitlines() if "[план]" in line]
    assert plan[1].split("· ")[0].endswith("Б ") and "(изменилось)" in plan[1]
    assert not (a / CHAROITE.dossier_dir / dossier.INDEX_JSON).exists()


def test_failed_scan_of_one_graph_does_not_cancel_the_others(nd, tmp_path, monkeypatch):
    """Выход out1 №621: общий скан без границы графа отменял работу всех графов."""
    good = _graph(tmp_path, "Живой", {"ж1": None})
    bad = _graph(tmp_path, "Сломанный", {"с1": None})
    real = dossier.scan

    def scan(graph, **kw):
        if graph == bad:
            raise UnicodeError("сломанный узел")
        return real(graph, **kw)

    monkeypatch.setattr(nd.dossier, "scan", scan)
    monkeypatch.setattr(nd.signal, "signal", lambda *a: None)
    monkeypatch.setattr(nd, "cfg", lambda: {})
    monkeypatch.setattr(nd, "all_graphs", lambda: [bad, good])
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--all-graphs"])
    assert nd.main() == 1
    assert nd.calls == ["ж1"]


def test_lock_wait_is_capped_by_the_budget_left(nd, tmp_path, monkeypatch):
    """Выход out1 №621, Sonnet I2: замок ждал пять минут сверх бюджета."""
    graph = _graph(tmp_path, "Г", {"одна": None})
    waits = []

    def lock(graph, wait=nd.LOCK_WAIT):
        waits.append(wait)
        return contextlib.nullcontext(True)

    monkeypatch.setattr(nd, "_graph_lock", lock)
    monkeypatch.delenv(nd.live_gate.NIGHTLY_UNTIL_ENV, raising=False)   # не окно ночи оболочки (Haiku out3 M2)
    nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=100)
    assert waits == [70.0], waits         # 100 с бюджета минус 30 с генерации
    # и ночь до резерва хвоста — у --full без бюджета тоже (№749)
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV,
                       str(__import__("time").time() + nd.live_gate.NIGHT_TAIL_S + 60))
    waits.clear()
    _graph(tmp_path, "Г2", {"вторая": None})
    nd.run(tmp_path / "Г2", {}, full=True, dry=False, limit=None)
    assert 0 < waits[0] <= 60, waits


def test_author_edit_between_check_and_write_is_not_overwritten(nd, tmp_path, monkeypatch):
    """Выход out2 №621, DS C1: правка между сверкой и replace затиралась."""
    graph = _graph(tmp_path, "Г", {"тема": "2026-07-20"})
    path = graph / CHAROITE.dossier_dir / "тема.md"
    real_backup = dossier.backup

    def backup(folder, stamp, p, **kw):
        out = real_backup(folder, stamp, p, **kw)
        p.write_text(p.read_text(encoding="utf-8") + "\nправка автора в окне\n", encoding="utf-8")
        return out

    monkeypatch.setattr(nd.dossier, "backup", backup)
    r = nd.run(graph, {}, full=False, dry=False, limit=None)
    assert r["собрано"] == 0 and r["досье_изменилось"] == 1 and r["пропущено"] == 1, r
    assert "правка автора в окне" in path.read_text(encoding="utf-8")
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {"тема"}


def test_need_is_the_generation_not_the_lock_wait(nd, tmp_path, monkeypatch):
    """Выход out2 №621, Haiku I1: ожидание взятого замка раздувало `need`."""
    graph = _graph(tmp_path, "Г", {f"т{i}": None for i in range(4)})

    @contextlib.contextmanager
    def slow_lock(graph, wait=nd.LOCK_WAIT):
        nd.fake_clock.t += 200          # сосед держал замок, но отпустил
        yield True

    monkeypatch.setattr(nd, "_graph_lock", slow_lock)
    # 30 с генерации + 200 с замка на тему; need по генерации = 30:
    # старт 0 → 230 → 460 → 690; при бюджете 700 четвёртая тема (690 + 30 > 700) не влезает
    r = nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=700)
    assert r["собрано"] == 3 and r["не_успели"] == 1, r


def test_unreadable_stat_of_a_present_dossier_does_not_write(nd, tmp_path, monkeypatch):
    """Выход out3 №621, DS I1: без снимка запись шла с «файла нет»."""
    graph = _graph(tmp_path, "Г", {"тема": "2026-07-20"})
    path = graph / CHAROITE.dossier_dir / "тема.md"
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(nd.safe_write, "stat_snapshot", lambda p: None)
    monkeypatch.setattr(nd.safe_write, "write_text", lambda *a, **k: pytest.fail("запись без снимка"))
    r = nd.run(graph, {}, full=False, dry=False, limit=None)
    assert r["собрано"] == 0 and r["досье_изменилось"] == 1 and r["пропущено"] == 1, r
    assert path.read_text(encoding="utf-8") == before


def test_zero_lock_wait_is_the_night_not_a_busy_graph(nd, tmp_path, monkeypatch):
    """Выход out3 №621, Sonnet M1: вышедшая ночь записывала граф в «занятые»."""
    graph = _graph(tmp_path, "Г", {f"т{i}": None for i in range(3)})
    monkeypatch.setattr(nd, "_graph_lock", lambda graph, wait=nd.LOCK_WAIT: contextlib.nullcontext(wait > 0))
    monkeypatch.setattr(nd.live_gate, "night_wait_cap", lambda default, **k: 0.0)
    states = nd.run_all([graph], {}, full=False, dry=False).graphs
    assert not states[0].busy


def test_silent_model_and_failed_scan_exit_4(nd, tmp_path, monkeypatch):
    """Выход out3 №621, Haiku I1: код 2 прятал упавший скан от статуса ночи."""
    good = _graph(tmp_path, "Живой", {f"ж{i}": None for i in range(3)})
    bad = _graph(tmp_path, "Сломанный", {"с1": None})
    real = dossier.scan

    def scan(graph, **kw):
        if graph == bad:
            raise UnicodeError("сломанный узел")
        return real(graph, **kw)

    def boom(*a, **k):
        raise TimeoutError("стрим молчит")

    monkeypatch.setattr(nd.dossier, "scan", scan)
    monkeypatch.setattr(nd, "generate", boom)
    monkeypatch.setattr(nd.signal, "signal", lambda *a: None)
    monkeypatch.setattr(nd, "cfg", lambda: {})
    monkeypatch.setattr(nd, "all_graphs", lambda: [bad, good])
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--all-graphs"])
    assert nd.main() == 4


def test_zero_budget_means_the_default(nd, tmp_path, monkeypatch):
    """Выход out3 №621, DS M4: --budget-s 0 давал холостой шаг с кодом 0."""
    graph = _graph(tmp_path, "Г", {"одна": None})
    monkeypatch.setattr(nd, "cfg", lambda: {})
    monkeypatch.setattr(nd.signal, "signal", lambda *a: None)
    monkeypatch.setattr(nd.sys, "argv", ["nightly_dossier.py", "--graph", str(graph), "--budget-s", "0"])
    assert nd.main() == 0 and nd.calls == ["одна"]


def test_meeting_gate_gets_the_budget_left(nd, tmp_path, monkeypatch):
    """Мутант #819: окно встречи получало бюджет плюс прошедшее."""
    graph = _graph(tmp_path, "Г", {"одна": None, "другая": None})
    seen = []
    monkeypatch.setattr(nd.live_gate, "night_window_open",
                        lambda *a, default=None, **k: seen.append(default) or True)
    nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=100)
    assert seen == [100, 70], seen


def test_finished_graph_index_is_written_before_the_run_ends(nd, tmp_path, monkeypatch):
    """Мутант #819: индекс готового графа ждал конца прогона — SIGKILL его терял."""
    small = _graph(tmp_path, "Малый", {"м1": None})
    big = _graph(tmp_path, "Большой", {f"б{i}": None for i in range(3)})
    seen = []
    real = nd.generate

    def generate(theme, *a, **k):
        if theme == "б2":
            seen.append({e["тема"] for e in dossier.load_index(small / CHAROITE.dossier_dir)})
        return real(theme, *a, **k)

    monkeypatch.setattr(nd, "generate", generate)
    nd.run_all([small, big], {}, full=False, dry=False)
    assert seen == [{"м1"}], seen


def test_retry_is_colder_than_the_first_attempt(nd, tmp_path, monkeypatch):
    """Мутант #819: температура первой попытки и повтора не проверялась."""
    graph = _graph(tmp_path, "Г", {"одна": None})
    temps = []

    def generate(theme, members, files, c, temperature=None, **kw):
        temps.append(temperature)
        return "не по формату" if len(temps) == 1 else GOOD

    monkeypatch.setattr(nd, "generate", generate)
    r = nd.run(graph, {}, full=False, dry=False, limit=None)
    assert temps == [0.2, 0.05] and r["собрано"] == 1, (temps, r)


def test_backup_line_only_when_a_dossier_was_replaced(nd, tmp_path, capsys):
    """Мутант #819: строка о копии печаталась бы у новой темы."""
    graph = _graph(tmp_path, "Г", {"старая": "2026-07-20", "новая": None})
    nd.run(graph, {}, full=False, dry=False, limit=None)
    lines = capsys.readouterr().out.splitlines()
    copies = [lines[i + 1] for i, l in enumerate(lines) if "копия прежнего досье:" in l]
    assert len(copies) == 1 and "· старая:" in copies[0], lines


@pytest.mark.parametrize("failures, attempts", [(0, 0), (1, 1)])
def test_attempts_count_refusals_before_the_model(nd, tmp_path, monkeypatch, failures, attempts):
    """Мутант #819: повреждённые «Правки автора» — не попытка, нечитаемое досье — попытка."""
    graph = _graph(tmp_path, "Г", {"одна": None})
    monkeypatch.setattr(nd, "_manual_for_rebuild", lambda theme, loaded: (False, None, failures))
    assert nd.run_all([graph], {}, full=False, dry=False).attempts == attempts


def test_a_model_call_is_an_attempt(nd, tmp_path):
    graph = _graph(tmp_path, "Г", {"одна": None, "другая": None})
    assert nd.run_all([graph], {}, full=False, dry=False).attempts == 2


def test_across_graphs_a_round_goes_new_then_oldest_then_largest(nd):
    """Мутант #819: ключ круга без класса ставил графы по имени."""
    gs = [nd._GraphRun(pathlib.Path(n), {}, {}, "2026-10-18", "s") for n in "АБВГ"]
    cands = [_cand(nd, gs[0], "изменённая", nd.CHANGED, "2026-08-01", size=1),
             _cand(nd, gs[1], "свежая", nd.CHANGED, "2026-09-01", size=9),
             _cand(nd, gs[2], "мелкая новая", nd.NEW, "", size=1),
             _cand(nd, gs[3], "крупная новая", nd.NEW, "", size=5)]
    assert [c.theme for c in nd.queue_order(cands)] == [
        "крупная новая", "мелкая новая", "изменённая", "свежая"]


def test_in_a_round_a_changed_theme_goes_before_an_older_unchanged_one(nd):
    """Выход out4 №621, DS M1: без класса в ключе старая неизменная (--full) обгоняла изменённую."""
    gs = [nd._GraphRun(pathlib.Path(n), {}, {}, "2026-10-18", "s") for n in "АБ"]
    cands = [_cand(nd, gs[0], "старая неизменная", nd.SAME, "2026-01-01", size=9),
             _cand(nd, gs[1], "изменённая", nd.CHANGED, "2026-09-01", size=1)]
    assert [c.theme for c in nd.queue_order(cands)] == ["изменённая", "старая неизменная"]


def test_index_is_written_once_per_graph(nd, tmp_path, monkeypatch):
    """Мутант #819: индекс графа писался и по `done`, и в `finally`."""
    graph = _graph(tmp_path, "Г", {"одна": None, "другая": None})
    writes = []
    real = dossier.write_index
    monkeypatch.setattr(nd.dossier, "write_index", lambda folder, entries: writes.append(folder) or real(folder, entries))
    nd.run(graph, {}, full=False, dry=False, limit=None)
    assert len(writes) == 1, writes


def test_closed_night_sends_the_whole_queue_to_tomorrow_once(nd, tmp_path, monkeypatch, capsys):
    graph = _graph(tmp_path, "Г", {"одна": None, "другая": None})
    monkeypatch.setattr(nd.live_gate, "night_window_open", lambda *a, **k: False)
    r = nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=100)
    assert nd.calls == [] and r["не_успели"] == 2 and r["пропущено"] == 2, r
    assert capsys.readouterr().out.count("время ночного прогона вышло") == 1


def _raise(*a, **k):
    raise OSError("диск")


_FAILED = {"refusal": "отказы", "changed": "досье_изменилось", "no_snapshot": "досье_изменилось",
           "backup": "отказы", "write": "досье_изменилось"}


@pytest.mark.parametrize("path", list(_FAILED))
def test_a_failed_build_still_costs_its_generation(nd, tmp_path, monkeypatch, path):
    """Мутанты #819 (`return gen_s` → `return None`): генерация без записи — тоже цена темы для `need`."""
    graph = _graph(tmp_path, "Г", {"т1": "2026-07-20", "т2": "2026-07-20"})

    def generate(theme, *a, **k):
        nd.calls.append(theme)
        nd.fake_clock.t += 100
        if path == "refusal":
            raise RuntimeError("модель молчит")
        if path == "changed":
            p = graph / CHAROITE.dossier_dir / f"{theme}.md"
            p.write_text(p.read_text(encoding="utf-8") + "\nправка\n", encoding="utf-8")
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    if path == "no_snapshot":
        monkeypatch.setattr(nd.safe_write, "stat_snapshot", lambda p: None)
    if path == "backup":
        monkeypatch.setattr(nd.dossier, "backup", _raise)
    if path == "write":
        monkeypatch.setattr(nd.safe_write, "write_text", lambda *a, **k: False)
    # первая тема — 100 с генерации; вторая с need 100 в остаток 50 не влезает, с NEED_START_S 40 влезла бы
    r = nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=150)
    assert len(nd.calls) == 1 and r["не_успели"] == 1, (nd.calls, r)
    # и тема действительно не собралась по своей причине (выход out4 №621, DS I1)
    assert r["собрано"] == 0 and r[_FAILED[path]] == 1, r


def test_a_build_that_found_the_graph_locked_is_an_attempt(nd, tmp_path, monkeypatch):
    """Мутант #819: тема, у которой модель отработала, а замок не дался, — попытка."""
    graph = _graph(tmp_path, "Г", {"одна": None})
    monkeypatch.setattr(nd, "_graph_lock", lambda graph, wait=nd.LOCK_WAIT: contextlib.nullcontext(False))
    monkeypatch.setattr(nd.live_gate, "night_wait_cap", lambda default, **k: default)
    r = nd.run_all([graph], {}, full=False, dry=False)
    assert r.attempts == 1 and r.graphs[0].locked == 1 and r.graphs[0].built == 0


def test_limit_counts_only_the_new_builds(nd, tmp_path):
    """Мутант #819 (`g.built - before` → `+`): лимит считает сборки этого прогона по одной."""
    graph = _graph(tmp_path, "Г", {f"т{i}": None for i in range(4)})
    states = nd.run_all([graph], {}, full=False, dry=False, limit=3).graphs
    assert states[0].built == 3 and states[0].over_limit == 1


def test_graph_gone_mid_run_does_not_stop_the_night(nd, tmp_path, monkeypatch):
    """Выход out4 №621, DS M2 / Haiku I1: `mkdir` без `parents` ронял весь прогон на пропавшем графе."""
    a = _graph(tmp_path, "А", {"а1": None, "а2": None})
    b = _graph(tmp_path, "Б", {"б1": None})

    def generate(theme, *x, **k):
        nd.calls.append(theme)
        if theme == "а1":
            shutil.rmtree(a)
        return GOOD

    monkeypatch.setattr(nd, "generate", generate)
    nd.run_all([a, b], {}, full=False, dry=False)
    assert "б1" in nd.calls


def test_cut_full_rebuild_continues_from_the_rest_next_sunday(nd, tmp_path, monkeypatch):
    """№749, вход in1 DS C3: урезанная полная пересборка не начинается заново,
    а следующим воскресеньем берёт ровно то, что не успела, — по `собрано`
    настоящих досье, а не по засеву теста."""
    import datetime as dt
    graph = _graph(tmp_path, "Г", {f"тема{i}": None for i in range(6)})

    def on(day):
        class _Day(dt.date):
            @classmethod
            def today(cls):
                return cls.fromisoformat(day)
        monkeypatch.setattr(nd, "date", _Day)

    on("2026-10-18")
    nd.run(graph, {}, full=False, dry=False, limit=None, budget_s=10**6)   # засев сборкой
    built = {}
    for day in ("2026-10-25", "2026-11-01"):
        on(day)
        nd.calls.clear()
        nd.run(graph, {}, full=True, dry=False, limit=None, budget_s=100)
        built[day] = list(nd.calls)
    assert len(built["2026-10-25"]) == 3 and len(built["2026-11-01"]) == 3, built
    assert set(built["2026-10-25"]) | set(built["2026-11-01"]) == {f"тема{i}" for i in range(6)}


def test_night_running_out_during_the_meeting_wait_stops_before_the_theme(nd, tmp_path, monkeypatch, capsys):
    """Выход out2 №749, DS M3: стена ушла внутри ожидания встречи за резерв
    хвоста — тему не начинаем, причина — ночь, а не бюджет."""
    graph = _graph(tmp_path, "Г", {f"тема{i}": "2026-07-20" for i in range(3)})
    wall = _Wall(1.0e9)
    monkeypatch.setattr(nd, "wall_clock", wall)
    monkeypatch.setenv(nd.live_gate.NIGHTLY_UNTIL_ENV, str(wall.t + nd.live_gate.NIGHT_TAIL_S + 600))

    def window(*a, **k):
        wall.t += 590              # ждали конца встречи, до резерва осталось 10 с
        return True

    monkeypatch.setattr(nd.live_gate, "night_window_open", window)
    r = nd.run(graph, {}, full=True, dry=False, limit=None, budget_s=7200)
    assert nd.calls == [] and r["не_успели"] == 3, r
    out = capsys.readouterr().out
    assert "до конца ночи — только время шагов после досье, пока ждали конца встречи" in out
    assert "без изменений — в следующую полную пересборку" in out
