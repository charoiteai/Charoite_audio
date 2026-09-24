"""Грамматика статуса строки поручения — одна на всех переписчиков (№366).

Контроль задач снимает старое поручение строкой «- [-] … _(снято по сроку ДД.ММ)_»,
человек отмечает выполненное «[x]» или возвращает снятое в «[ ]», оставив пометку.
Пока грамматику знала только часть кода, normalize превращал снятое в
«- [ ] [-] Коля — …», мост ревизии дописывал его заново открытым, а разовая правка
fix_action_items на боевом графе переоткрыла бы 574 снятых строки в 179 файлах
(сухой прогон 24.09). Таблица ниже: каждый переписчик × каждая форма статуса —
статус, поставленный человеком или контролем, не меняется.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import action_items  # noqa: E402
import review_bridge  # noqa: E402
import task_line  # noqa: E402

TASK = "**Коля** — подготовить отчёт по продажам — до 30.09"
FORMS = {
    task_line.OPEN: f"- [ ] {TASK}",
    task_line.DONE: f"- [x] {TASK}",
    task_line.CLOSED: f"- [-] {TASK} _(снято по сроку 24.09)_",
    task_line.RETURNED: f"- [ ] {TASK} _(снято по сроку 24.09)_",
}
SETTLED = [task_line.DONE, task_line.CLOSED, task_line.RETURNED]


def minutes(line: str) -> str:
    return f"# Встреча\n\n## Поручения\n{line}\n**Оля** — сверить цифры\n\n## Риски\n- нет\n"


@pytest.mark.parametrize("form, line", [
    *FORMS.items(),
    (task_line.DONE, f"* [X] {TASK}"),
    (task_line.CLOSED, f"- [-] {TASK} _(снято: нет исполнителя 24.09)_"),
    (task_line.RETURNED, f"- [ ] {TASK} _(снято: давно, срок не разобран 24.09)_"),
    (task_line.OPEN, f"- [ ] {TASK} _(снято ревизией: не звучало)_"),     # не пометка контроля
    (None, f"- {TASK}"),
    (None, "просто текст"),
])
def test_status_names_every_form(form, line):
    assert task_line.status(line) == form


@pytest.mark.parametrize("form", list(FORMS))
def test_normalize_keeps_every_status(form):
    doc = minutes(FORMS[form])
    out = action_items.normalize(doc)
    assert FORMS[form] in out.split("\n"), out
    assert "[ ] [" not in out


@pytest.mark.parametrize("form", SETTLED)
def test_outsider_mark_keeps_a_settled_status(form):
    # Коли на встрече не было: открытое поручение становится пометкой «не участник»,
    # а выполненное, снятое и возвращённое остаются как есть
    doc = minutes(FORMS[form])
    out = action_items.flag_outsiders(doc, {"оля", "петя"})
    assert FORMS[form] in out.split("\n"), out


def test_outsider_mark_still_flags_an_open_item():
    out = action_items.flag_outsiders(minutes(FORMS[task_line.OPEN]), {"оля", "петя"})
    assert FORMS[task_line.OPEN] not in out.split("\n")
    assert action_items.OUTSIDER_MARK in out


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("task", [TASK, "**Коля** — отчёт"])
def test_bridge_does_not_readd_a_known_item(form, task):
    # короткое поручение нечётким сравнением не узнаётся: пометка контроля в ключе
    # («снято по сроку») перевешивает одно общее слово — узнаёт только ключ без неё
    line = FORMS[form].replace(TASK, task)
    doc = minutes(line)
    out, added = review_bridge.merge_into_minutes(doc, [task])
    assert added == 0 and out == doc


@pytest.mark.parametrize("form", SETTLED)
def test_bridge_does_not_withdraw_a_settled_item(form):
    doc = minutes(FORMS[form])
    out, moved = review_bridge.withdraw_from_minutes(doc, [(TASK, "не звучало")])
    assert moved == 0 and out == doc


def test_bridge_still_withdraws_an_open_item():
    out, moved = review_bridge.withdraw_from_minutes(minutes(FORMS[task_line.OPEN]), [(TASK, "не звучало")])
    assert moved == 1 and FORMS[task_line.OPEN] not in out.split("\n")


def _fix(graph: pathlib.Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "fix_action_items.py"),
                           "--graph", str(graph), *args],
                          env=env, capture_output=True, text=True, timeout=60)


def test_fix_action_items_keeps_statuses_and_fixes_prose(tmp_path):
    graph = tmp_path / "граф"
    (graph / "Встречи").mkdir(parents=True)
    data = tmp_path / "данные"
    (data / "logs").mkdir(parents=True)
    kept = [FORMS[f] for f in FORMS]
    note = graph / "Встречи" / "2026-09-01 Минутки.md"
    note.write_text("# Встреча\n\n## Поручения\n" + "\n".join(kept)
                    + "\n*   **Оля** — сверить цифры\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    env["CHAROITE_ROOT"] = str(data)
    r = _fix(graph, env, "--apply")
    assert r.returncode == 0, r.stderr
    lines = note.read_text(encoding="utf-8").split("\n")
    for line in kept:
        assert line in lines, lines
    assert "- [ ] **Оля** — сверить цифры" in lines, lines


def test_fix_action_items_refuses_to_write_without_a_named_data_root(tmp_path):
    graph = tmp_path / "граф"
    graph.mkdir()
    note = graph / "Минутки.md"
    note.write_text("## Поручения\n*   **Оля** — сверить цифры\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    r = _fix(graph, env, "--apply")
    assert r.returncode == 2 and "CHAROITE_ROOT" in r.stderr
    assert note.read_text(encoding="utf-8") == "## Поручения\n*   **Оля** — сверить цифры\n"


def test_fix_action_items_does_not_write_while_the_graph_lock_is_held(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import charoite_paths
    import file_locks
    import fix_action_items
    graph = tmp_path / "граф"
    graph.mkdir()
    data = tmp_path / "данные"
    (data / "logs").mkdir(parents=True)
    note = graph / "Минутки.md"
    text = "## Поручения\n*   **Оля** — сверить цифры\n"
    note.write_text(text, encoding="utf-8")
    monkeypatch.setenv("CHAROITE_ROOT", str(data))
    monkeypatch.setattr(fix_action_items, "LOCK_WAIT", 0.2)
    monkeypatch.setattr(sys, "argv", ["fix_action_items.py", "--graph", str(graph), "--apply"])
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_backups(graph, "cloud_backup", root=data).parent)
    with file_locks.graph_lock(lock_dir, 1) as taken:
        assert taken
        assert fix_action_items.main() == 1
    assert note.read_text(encoding="utf-8") == text
