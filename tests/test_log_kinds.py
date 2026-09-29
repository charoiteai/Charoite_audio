"""Реестр имён `logs/` — один на писателей, ретеншн и «Забыть» (№514 PR 2).

Пока виды журналов жили тремя копиями (f-строки писателей, глобы ретеншна, таблица
«Забыть»), каждый новый журнал правил все три, и копии расходились: «Забыть» искало
журналы повтора минутой, а писатель называл их полным стемом. Эти тесты держат
обещания реестра: имя даёт только он, читатели строятся из него, а общий журнал и
замок живут по своим правилам.
"""
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import charoite_paths as cp  # noqa: E402
import forget_meeting as forget  # noqa: E402
import meeting_stamp  # noqa: E402

ROOT = pathlib.Path("/data")
STEM = "2026-08-04_120305"


def test_meeting_logs_are_named_by_the_rule_of_their_kind():
    """Имя каждого журнала встречи — то, которое «Забыть» ищет своим правилом."""
    logs = ROOT / "logs"
    assert cp.meeting_log(ROOT, "graph", stem=f"{STEM}_Тема") == logs / "graph_2026-08-04_1203.log"
    assert cp.meeting_log(ROOT, "retry", stem=f"{STEM}_Тема") == logs / f"retry_{STEM}_Тема.log"
    assert cp.meeting_log(ROOT, "recover", stem=STEM) == logs / f"recover_{STEM}.log"
    assert cp.meeting_log(ROOT, "cloud_review", key="2026-08-04_1203") == \
        logs / "cloud_review_2026-08-04_1203.log"
    assert cp.meeting_log(ROOT, "nemotron_live", stem=STEM, suffix=".jsonl") == \
        logs / f"nemotron_live_{STEM}.jsonl"
    assert cp.meeting_log(ROOT, "nemotron_live", stem=STEM, suffix=".err") == \
        logs / f"nemotron_live_{STEM}.err"


@pytest.mark.parametrize("stem", ["2026-08-04_120305", "2026-08-04_120305-1",
                                  "2026-08-04_1203_Тема", "2026-08-04_1203"])
def test_minute_rule_is_the_minute_forget_searches(stem):
    """Правило `minute` режет `stem[:15]` — ровно минута штампа, которой «Забыть» ищет
    общий журнал минуты: голый посекундный, с суффиксом коллизии, с темой, четырёхзначный."""
    minute = meeting_stamp.minute_of(meeting_stamp.stamp_of(stem))
    assert cp.meeting_log(ROOT, "graph", stem=stem).name == f"graph_{minute}.log"


def test_shared_logs_and_locks_are_named_by_log_path():
    logs = ROOT / "logs"
    assert cp.log_path(ROOT, "graph_unlinked") == logs / "graph_unlinked.log"
    assert cp.log_path(ROOT, "graph_unlinked", suffix=".old") == logs / "graph_unlinked.old"
    assert cp.log_path(ROOT, "import_prune", "Inbox") == logs / "import_prune-Inbox.log"
    assert cp.log_path(ROOT, "rebuild_pid", STEM) == logs / f"rebuild-{STEM}.pid"


@pytest.mark.parametrize("call", [
    lambda: cp.meeting_log(ROOT, "nosuch", stem=STEM),                    # вида нет в реестре
    lambda: cp.meeting_log(ROOT, "graph", stem=STEM, suffix=".txt"),       # чужой суффикс
    lambda: cp.meeting_log(ROOT, "graph_unlinked", stem=STEM),             # не журнал встречи
    lambda: cp.meeting_log(ROOT, "cloud_review", stem=STEM),               # ключ графа — key=
    lambda: cp.meeting_log(ROOT, "cloud_review", key=STEM, stem=STEM),     # и то, и другое
    lambda: cp.meeting_log(ROOT, "cloud_review"),                          # ни того, ни другого
    lambda: cp.meeting_log(ROOT, "retry"),
    lambda: cp.meeting_log(ROOT, "retry", key=STEM),                       # стем — stem=
    lambda: cp.meeting_log(ROOT, "retry", stem=STEM, key=STEM),
    lambda: cp.log_path(ROOT, "retry", STEM),                              # журнал встречи
    lambda: cp.log_path(ROOT, "graph_unlinked", "x"),                      # имя целиком
    lambda: cp.log_path(ROOT, "import_prune"),                             # нужна часть
    lambda: cp.log_path(ROOT, "nosuch", "x"),
])
def test_a_log_outside_the_registry_or_its_rule_is_refused(call):
    with pytest.raises(ValueError):
        call()


def test_every_meeting_rule_is_one_forget_knows():
    """Метка правила штампа — данные реестра, толкует её «Забыть»: вид с меткой, которой нет
    в его `by_rule`, упал бы `KeyError` только в «Забыть»."""
    rules = {e.rule for e in cp.LOG_KINDS.values() if e.role == "meeting"}
    assert rules == set(cp.LOG_STAMP_RULES)
    assert all(e.rule is None for e in cp.LOG_KINDS.values() if e.role != "meeting")
    assert {e.stem for e in forget.MEETING_LOGS} == \
        {e.stem for e in cp.LOG_KINDS.values() if e.role == "meeting"}


def test_forget_plan_knows_every_rule(tmp_path):
    """План «Забыть» толкует каждую метку реестра: вид с новой меткой ронял бы план."""
    root = tmp_path / "repo"
    (root / "transcripts").mkdir(parents=True)
    (root / "transcripts" / f"{STEM}.md").write_text("# Встреча\n", encoding="utf-8")
    (root / "logs").mkdir()
    for entry in cp.LOG_KINDS.values():
        if entry.role == "meeting":
            path = (cp.meeting_log(root, _kind(entry), key=STEM) if entry.rule == "graph_key"
                    else cp.meeting_log(root, _kind(entry), stem=STEM))
            path.write_text("имена: Мария Соколова\n", encoding="utf-8")
    graph = tmp_path / "vault" / "Работа"
    (graph / "Встречи").mkdir(parents=True)
    plan = forget.plan(STEM, root, graph)
    found = {f.name for f in plan.delete if f.parent == root / "logs"}
    assert found == {f.name for f in (root / "logs").iterdir()}


def _kind(entry: cp.LogKind) -> str:
    return next(k for k, e in cp.LOG_KINDS.items() if e is entry)


# Имена, которые живут в logs/ рядом с журналами и под ретеншн попадать не должны.
NOT_SWEPT = ["graph_doctor.json", "daemon.lock", "rebuild.lock", "mutation.lock",
             "nightly.json", f"rebuild-{STEM}.pid", "lexicon_candidates.md"]


def test_retention_globs_miss_locks_and_non_journals(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in NOT_SWEPT:
        (logs / name).write_text("x", encoding="utf-8")
    swept = [p for e in cp.LOG_KINDS.values() for g in cp.sweep_globs(e) for p in logs.glob(g)]
    assert swept == []
    assert cp.sweep_globs(cp.LOG_KINDS["rebuild_pid"]) == ()


def test_retention_sweeps_every_kind_of_the_registry(tmp_path):
    """Ретеншн стирает старые журналы всех видов с глобом — включая ротацию общего журнала
    графовых решений (.old, которую не убирал никто) и вывод уборки импорта; замок
    пересборки и не-журналы остаются, свежий журнал — тоже."""
    import daemon as d

    logs = tmp_path / "logs"
    logs.mkdir()
    old = [cp.meeting_log(tmp_path, "graph", stem=STEM), cp.meeting_log(tmp_path, "retry", stem=STEM),
           cp.meeting_log(tmp_path, "recover", stem=STEM),
           cp.meeting_log(tmp_path, "cloud_review", key=STEM),
           cp.meeting_log(tmp_path, "nemotron_live", stem=STEM, suffix=".jsonl"),
           cp.meeting_log(tmp_path, "nemotron_live", stem=STEM, suffix=".err"),
           cp.log_path(tmp_path, "graph_unlinked"), cp.log_path(tmp_path, "graph_unlinked", suffix=".old"),
           cp.log_path(tmp_path, "import_prune", "Inbox")]
    kept = [logs / name for name in NOT_SWEPT]
    for f in [*old, *kept]:
        f.write_text("имена: Мария Соколова", encoding="utf-8")
    stale = old[0].stat().st_mtime - 30 * 86400
    for f in [*old, *kept]:
        os.utime(f, (stale, stale))
    fresh = cp.meeting_log(tmp_path, "retry", stem="2026-09-29_120000")
    fresh.write_text("сегодня", encoding="utf-8")

    cp.use_data_root(tmp_path, replace=True)
    d._prune_graph_logs({"audio": {"record_keep_days": 2}})

    assert [f.name for f in old if f.exists()] == []
    assert [f.name for f in kept if not f.exists()] == []
    assert fresh.exists()
