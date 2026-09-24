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

import contextlib
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import re
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
    task_line.OTHER: f"- [/] {TASK}",
}
SETTLED = [task_line.DONE, task_line.CLOSED, task_line.RETURNED, task_line.OTHER]
# Формы пункта, которые узнаёт хоть один переписчик: маркер × пробелы после него ×
# символ в ящике (Opus C1 и I1, Sonnet M1 круга 1 по коду: у normalize был свой
# регэксп «уже чекбокс», и «+ [x]» становилось «- [ ] [x]»)
MARKERS = ["-", "*", "+", "•", "–", "—", "⁃", "‣", "▪", "1.", "1)"]
SEPARATORS = [" ", "  ", "\t"]
# «[i]», «[b]», «[p]» — буквенные отметки тем Obsidian: символ в ящике бывает и буквой
BOXES = {" ": task_line.OPEN, "x": task_line.DONE, "X": task_line.DONE, "-": task_line.CLOSED,
         "/": task_line.OTHER, ">": task_line.OTHER, "!": task_line.OTHER, "i": task_line.OTHER}


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


@pytest.mark.parametrize("line", ["просто текст", "", f"- {TASK}", f"1. {TASK}", FORMS[task_line.DONE]])
def test_canonical_returns_a_line_without_a_box_or_already_canonical_as_is(line):
    # вызывающие зовут canonical только для строк с ящиком, и договор «без ящика — как есть»
    # держит только этот тест (мутант «return line → return None» выживал, CI #616)
    assert task_line.canonical(line) == line


@pytest.mark.parametrize("box", list(BOXES))
@pytest.mark.parametrize("sep", SEPARATORS)
@pytest.mark.parametrize("marker", MARKERS)
def test_every_list_form_keeps_its_status_through_normalize(marker, sep, box):
    # префикс — к виду вкладки «- [c] », ящик и тело как были (Opus M1 круга 2: «1. [ ] …»
    # оставался как есть, и вкладка задачу не видела)
    line = f"{marker}{sep}[{box}] {TASK}"
    assert task_line.status(line) == BOXES[box]
    doc = minutes(line)
    out = action_items.normalize(doc)
    expected = line if marker in ("-", "*") and sep == " " else f"- [{box}] {TASK}"
    assert expected in out.split("\n"), out
    assert task_line.status_changes(doc, out) == []


def test_normalize_leaves_a_line_its_grammar_misses_instead_of_mangling_it():
    # «- - [x] …» — вложенный пункт в одну строку: Obsidian видит отмеченную задачу, грамматика —
    # нет. _to_checkbox сделал бы «- [ ] [x] …»; гейт статусов на каждой строке normalize
    # оставляет её как была (Opus, критика 1 круга 2: демон писал бы порчу молча)
    line = "- - [x] **Коля** — отчёт"
    out = action_items.normalize(minutes(line))
    assert line in out.split("\n"), out
    assert "[ ] [x]" not in out


def test_status_changes_names_a_changed_status_and_the_mangled_signature():
    # строка 5 — только подпись порчи: до правки грамматика ящика не видит («**[x]» внутри
    # жирного), после — видит открытую задачу; изменения статуса нет, порча есть
    before = ("## Поручения\n- [x] **Коля** — отчёт\n- [-] **Петя** — звонок\n**Оля** — сверить\n"
              "- **[x] Вера** — счёт\n")
    after = ("## Поручения\n- [ ] **Коля** — отчёт\n- [ ] [-] Петя — звонок\n- [ ] **Оля** — сверить\n"
             "- [ ] [x] Вера — счёт\n")
    assert task_line.status("- **[x] Вера** — счёт") is None
    assert [i for i, _, _ in task_line.status_changes(before, after)] == [2, 3, 5]


def test_status_changes_refuses_to_guess_when_lines_do_not_match_one_to_one():
    with pytest.raises(ValueError):
        task_line.status_changes("## Поручения\n- [x] **Коля** — отчёт", "## Поручения")


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


@pytest.mark.parametrize("form", list(FORMS))
def test_bridge_does_not_readd_a_paraphrase_of_a_known_item(form):
    # ревизия пересказывает поручение другими словами: узнаёт его только сравнение
    # исполнителя и слов дела, и исполнителя строки надо найти в любом статусе
    doc = minutes(FORMS[form])
    out, added = review_bridge.merge_into_minutes(doc, ["**Коля** — подготовить отчёт по продажам к 30.09"])
    assert added == 0 and out == doc


@pytest.mark.parametrize("form", SETTLED)
def test_bridge_does_not_withdraw_a_settled_item(form):
    doc = minutes(FORMS[form])
    out, moved = review_bridge.withdraw_from_minutes(doc, [(TASK, "не звучало")])
    assert moved == 0 and out == doc


@pytest.mark.parametrize("line", [FORMS[f] for f in SETTLED] + [f"- [i] {TASK}"])
def test_bridge_does_not_hand_a_settled_items_withdrawal_to_its_open_neighbour(line):
    # поставленный пункт отсеивался до сравнения, и снятие «подготовить отчёт» уезжало
    # с открытым соседом «…по бюджету»: он подходил вложением (Opus I2 круга 1 по коду).
    # Буква в ящике — ещё и проверка ключа: он обязан снимать ящик любого состояния
    mine = line.replace(TASK, "**Коля** — подготовить отчёт")
    doc = minutes(f"{mine}\n- [ ] **Коля** — подготовить отчёт по бюджету")
    dropped: list[str] = []
    out, moved = review_bridge.withdraw_from_minutes(
        doc, [("**Коля** — подготовить отчёт", "не звучало")], dropped=dropped)
    assert moved == 0 and out == doc
    assert any("со статусом" in d for d in dropped), dropped


@pytest.mark.parametrize("marker", MARKERS)
def test_bridge_knows_a_settled_item_under_any_list_marker(marker):
    # свой узкий маркер моста не узнавал «1. [x] **Коля** — …»: снятие уходило соседу, а
    # merge дописывал снятое открытым (Opus I2 круга 2)
    mine = f"{marker} [x] **Коля** — подготовить отчёт"
    doc = minutes(f"{mine}\n- [ ] **Коля** — подготовить отчёт по бюджету")
    dropped: list[str] = []
    out, moved = review_bridge.withdraw_from_minutes(
        doc, [("**Коля** — подготовить отчёт", "не звучало")], dropped=dropped)
    assert moved == 0 and out == doc
    # причина — узнанный поставленный пункт, а не «подходит к двум»: ключ обязан снять маркер
    assert any("со статусом" in d for d in dropped), dropped
    closed = minutes(f"{marker} [-] **Коля** — подготовить отчёт _(снято по сроку 24.09)_")
    out, added = review_bridge.merge_into_minutes(closed, ["**Коля** — подготовить отчёт"])
    assert added == 0 and out == closed
    # пересказ точным ключом не узнать — только исполнителем, и его тоже надо найти за маркером
    told = minutes(f"{marker} [-] {TASK} _(снято по сроку 24.09)_")
    out, added = review_bridge.merge_into_minutes(told, ["**Коля** — подготовить отчёт по продажам к 30.09"])
    assert added == 0 and out == told


def test_bridge_withdraws_the_open_twin_of_a_settled_item():
    # одинаковые строки, одна снята, другая открыта: поставленную снимать нельзя, так что
    # открытая — единственный ход, а не догадка (Sonnet I2 круга 2: до правки она снималась)
    closed = f"- [-] {TASK} _(снято по сроку 24.09)_"
    doc = minutes(f"{closed}\n- [ ] {TASK}")
    out, moved = review_bridge.withdraw_from_minutes(doc, [(TASK, "не звучало")])
    lines = out.split("\n")
    assert moved == 1 and closed in lines
    assert f"- [ ] {TASK}" not in lines


def test_bridge_still_withdraws_an_open_item():
    out, moved = review_bridge.withdraw_from_minutes(minutes(FORMS[task_line.OPEN]), [(TASK, "не звучало")])
    assert moved == 1 and FORMS[task_line.OPEN] not in out.split("\n")


def test_bridge_does_not_take_a_wrapped_line_for_an_item():
    # перенос чужого пункта — не пункт, даже когда его слова совпали со снятием: иначе
    # снятие «подходит к двум» и не снимает ничего (мутант «and → or» в отборе строк, CI #616)
    wrap = "Коля подготовить отчёт"
    doc = minutes(f"- [ ] **Вера** — передать Коле\n{wrap}\n- [ ] **Коля** — подготовить отчёт")
    dropped: list[str] = []
    out, moved = review_bridge.withdraw_from_minutes(
        doc, [("**Коля** — подготовить отчёт", "не звучало")], dropped=dropped)
    lines = out.split("\n")
    assert moved == 1 and dropped == [], dropped
    section = lines[:lines.index("**Оля** — сверить цифры")]
    assert "- [ ] **Коля** — подготовить отчёт" not in section
    assert section[-2:] == ["- [ ] **Вера** — передать Коле", wrap]


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
    original = note.read_text(encoding="utf-8")
    r = _fix(graph, env, "--apply")
    assert r.returncode == 0, r.stderr
    lines = note.read_text(encoding="utf-8").split("\n")
    for line in kept:
        assert line in lines, lines
    assert "- [ ] **Оля** — сверить цифры" in lines, lines
    # оригинал и манифест — в копиях графа, вне синхронизируемой папки
    [manifest] = list(data.rglob("manifest.tsv"))
    rows = manifest.read_text(encoding="utf-8").splitlines()
    assert rows[1].split("\t")[0] == "Встречи/2026-09-01 Минутки.md", rows
    assert (manifest.parent / "Встречи" / "2026-09-01 Минутки.md").read_text(encoding="utf-8") == original
    assert not str(manifest).startswith(str(graph))
    # суммы — от оригинала и от того, что лежит в графе: по ним откат узнаёт, что файл
    # после правки никто не трогал (мутант «_sha → None» выживал, CI #616)
    _, was, now = rows[1].split("\t")
    assert was == hashlib.sha256(original.encode("utf-8")).hexdigest()
    assert now == hashlib.sha256(note.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


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
    # замок берём точкой входа соседа, а не формулой скрипта: разойдутся — тест покраснеет
    # (Opus M2 круга 1 по коду)
    sys.path.insert(0, str(ROOT / "scripts"))
    import cloud_review
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
    with cloud_review.graph_lock(graph, 1) as taken:
        assert taken
        assert fix_action_items.main() == 1
    assert note.read_text(encoding="utf-8") == text


def test_fix_action_items_names_the_real_lock_wait(tmp_path, monkeypatch, capsys):
    # минуты — из той же константы, что и ожидание (мутант «// → *» выживал, CI #616);
    # замок подменён занятым, чтобы не ждать пять минут настоящего
    fix, _, (note,) = _script_run(tmp_path, monkeypatch, ("Минутки.md", "## Поручения\n*   **Оля** — сверить цифры\n"))
    monkeypatch.setattr(fix, "_graph_lock", lambda graph, root: contextlib.nullcontext(False))
    assert fix.main() == 1
    assert f"замок графа занят дольше {fix.LOCK_WAIT // 60} мин" in capsys.readouterr().err


def test_fix_action_items_keeps_an_edit_made_between_its_read_and_its_write(tmp_path, monkeypatch):
    # сосед (контроль задач, отметка в приложении) правит файл после того, как скрипт его
    # прочёл: запись через rewrite_file перечитывает файл, а «tmp + replace» затирал правку
    sys.path.insert(0, str(ROOT / "scripts"))
    import fix_action_items
    graph = tmp_path / "граф"
    graph.mkdir()
    data = tmp_path / "данные"
    (data / "logs").mkdir(parents=True)
    note = graph / "Минутки.md"
    note.write_text("## Поручения\n*   **Оля** — сверить цифры\n- [ ] **Петя** — позвонить\n", encoding="utf-8")
    real = fix_action_items.normalize
    calls = []

    def racing(text):
        if not calls:      # первое чтение — сухое; сосед успевает отметить задачу
            note.write_text(text.replace("- [ ] **Петя**", "- [x] **Петя**"), encoding="utf-8")
        calls.append(1)
        return real(text)

    monkeypatch.setattr(fix_action_items, "normalize", racing)
    monkeypatch.setenv("CHAROITE_ROOT", str(data))
    monkeypatch.setattr(sys, "argv", ["fix_action_items.py", "--graph", str(graph), "--apply"])
    assert fix_action_items.main() == 0
    lines = note.read_text(encoding="utf-8").split("\n")
    assert "- [x] **Петя** — позвонить" in lines, lines
    assert "- [ ] **Оля** — сверить цифры" in lines, lines


def _script_run(tmp_path, monkeypatch, *files: tuple[str, str]):
    sys.path.insert(0, str(ROOT / "scripts"))
    import fix_action_items
    graph = tmp_path / "граф"
    graph.mkdir()
    data = tmp_path / "данные"
    (data / "logs").mkdir(parents=True)
    notes = []
    for name, text in files:
        note = graph / name
        note.write_text(text, encoding="utf-8")
        notes.append(note)
    monkeypatch.setenv("CHAROITE_ROOT", str(data))
    monkeypatch.setattr(sys, "argv", ["fix_action_items.py", "--graph", str(graph), "--apply"])
    return fix_action_items, data, notes


def test_fix_action_items_refuses_a_file_whose_status_the_transform_would_change(tmp_path, monkeypatch, capsys):
    # гейт статусов не зависит от грамматики переписчика: преобразование, которое
    # открыло бы выполненное, не пишет файл, а называет строку (Opus I3 круга 1 по коду)
    text = "## Поручения\n- [x] **Коля** — отчёт\n*   **Оля** — сверить цифры\n"
    fix, _, (bad, good) = _script_run(tmp_path, monkeypatch, ("1 Минутки.md", text),
                                      ("2 Минутки.md", "## Поручения\n*   **Петя** — позвонить\n"))
    real = fix.normalize
    monkeypatch.setattr(fix, "normalize", lambda t: real(t).replace("- [x] **Коля**", "- [ ] **Коля**"))
    assert fix.main() == 1
    assert bad.read_text(encoding="utf-8") == text
    assert "- [ ] **Петя** — позвонить" in good.read_text(encoding="utf-8").split("\n")
    assert "1 Минутки.md: статус изменился бы — 2: «- [x] **Коля** — отчёт»" in capsys.readouterr().err


def test_fix_action_items_checks_statuses_on_the_text_it_actually_rewrites(tmp_path, monkeypatch, capsys):
    # сосед отмечает задачу между сухим чтением и записью: преобразование, которое её
    # открыло бы, упирается в гейт на перечитанном тексте, а не только на сухом
    fix, _, (note,) = _script_run(tmp_path, monkeypatch, (
        "Минутки.md", "## Поручения\n*   **Оля** — сверить цифры\n- [ ] **Петя** — позвонить\n"))
    real = fix.normalize
    calls = []

    def racing(text):
        if not calls:
            note.write_text(text.replace("- [ ] **Петя**", "- [x] **Петя**"), encoding="utf-8")
        calls.append(1)
        return real(text).replace("- [x] **Петя**", "- [ ] **Петя**")

    monkeypatch.setattr(fix, "normalize", racing)
    assert fix.main() == 1
    assert "- [x] **Петя** — позвонить" in note.read_text(encoding="utf-8").split("\n")
    assert "статус изменился бы" in capsys.readouterr().err


def test_fix_action_items_goes_on_after_a_lost_race_and_says_so(tmp_path, monkeypatch, capsys):
    # чужая запись посреди правки — отказ этого файла, а не трассировка посреди графа
    # (Sonnet C1 = Opus M1 круга 1 по коду)
    fix, _, (lost, good) = _script_run(tmp_path, monkeypatch,
                                       ("1 Минутки.md", "## Поручения\n*   **Оля** — сверить цифры\n"),
                                       ("2 Минутки.md", "## Поручения\n*   **Петя** — позвонить\n"))
    real = fix.safe_write.rewrite_file

    def flaky(path, transform, what):
        if path.name == "1 Минутки.md":
            raise fix.safe_write.LostRace(path, what, kind=fix.safe_write.LostRace.CHANGED)
        return real(path, transform, what)

    monkeypatch.setattr(fix.safe_write, "rewrite_file", flaky)
    assert fix.main() == 1
    assert "*   **Оля** — сверить цифры" in lost.read_text(encoding="utf-8").split("\n")
    assert "- [ ] **Петя** — позвонить" in good.read_text(encoding="utf-8").split("\n")
    out, err = capsys.readouterr()
    assert "исправлено: 1" in out
    assert "1 Минутки.md" in err


def test_fix_action_items_reports_what_it_wrote_not_what_it_planned(tmp_path, monkeypatch, capsys):
    # сосед привёл файл к формату сам между сухим чтением и записью: записывать нечего,
    # и отчёт это говорит, а не повторяет план сухого чтения (Sonnet I1 круга 1 по коду)
    fix, data, (note,) = _script_run(tmp_path, monkeypatch, (
        "Минутки.md", "## Поручения\n*   **Оля** — сверить цифры\n"))
    real = fix.normalize
    calls = []

    def racing(text):
        if not calls:
            note.write_text(real(text), encoding="utf-8")
        calls.append(1)
        return real(text)

    monkeypatch.setattr(fix, "normalize", racing)
    assert fix.main() == 0
    assert "исправлено: 0" in capsys.readouterr().out
    assert not list(data.rglob("manifest.tsv"))


def test_fix_action_items_leaves_no_orphan_copy_when_the_write_is_lost(tmp_path, monkeypatch, capsys):
    # настоящий rewrite_file: обе попытки кладут копию и проигрывают запись — копия без
    # строки манифеста осталась бы сиротой (Sonnet I1 = Opus M2 круга 2)
    fix, data, (note,) = _script_run(tmp_path, monkeypatch, (
        "Минутки.md", "## Поручения\n*   **Оля** — сверить цифры\n"))
    monkeypatch.setattr(fix.safe_write, "write_text", lambda *a, **k: False)
    assert fix.main() == 1
    assert note.read_text(encoding="utf-8") == "## Поручения\n*   **Оля** — сверить цифры\n"
    assert [q for q in data.rglob("Минутки.md") if "fix_action_items" in q.parts] == []
    assert not list(data.rglob("manifest.tsv"))


def test_fix_action_items_lays_the_copy_before_it_writes(tmp_path, monkeypatch):
    # порядок «копия, потом запись»: перенос копии после записи оставлял бы окно, где файл
    # уже заменён, а оригинала нет (Opus M3 круга 2)
    original = "## Поручения\n*   **Оля** — сверить цифры\n"
    fix, data, (note,) = _script_run(tmp_path, monkeypatch, ("Минутки.md", original))
    real = fix.safe_write.write_text
    seen = []

    def checking(path, text, **kw):
        seen.append([q.read_text(encoding="utf-8") for q in data.rglob(path.name)
                     if "fix_action_items" in q.parts])
        return real(path, text, **kw)

    monkeypatch.setattr(fix.safe_write, "write_text", checking)
    assert fix.main() == 0
    assert seen == [[original]]


def test_fix_action_items_refuses_a_transform_that_is_not_one_to_one(tmp_path, monkeypatch, capsys):
    # ValueError гейта статусов — отказ этого файла, как у остальных проверок, а не трассировка
    # посреди графа (Sonnet, критика 2 круга 2)
    text = "## Поручения\n*   **Оля** — сверить цифры\n"
    fix, _, (note,) = _script_run(tmp_path, monkeypatch, ("Минутки.md", text))
    monkeypatch.setattr(fix, "normalize", lambda t: t.split("\n", 1)[1])
    assert fix.main() == 1
    assert note.read_text(encoding="utf-8") == text
    # своя строка, а не «статус изменился бы — 0: «<начало файла>»…»: статусы здесь не сверялись
    assert capsys.readouterr().err.splitlines() == [
        "не тронуто: Минутки.md: преобразование не один к одному: строк 3 → 2"
        " — статусы не сверить, файл не тронут"]


def test_fix_action_items_dry_run_reports_and_writes_nothing(tmp_path, monkeypatch, capsys):
    # сухой прогон не берёт замок и не пишет, но честно считает (мутант «замок сухого
    # прогона не взят» выживал: сухой прогон шёл только подпроцессом без проверок, CI #616)
    text = "## Поручения\n*   **Оля** — сверить цифры\n"
    fix, data, (note,) = _script_run(tmp_path, monkeypatch, ("Минутки.md", text))
    monkeypatch.setattr(sys, "argv", ["fix_action_items.py", "--graph", str(note.parent)])
    assert fix.main() == 0
    out = capsys.readouterr().out
    assert "будет исправлено: 1" in out and "добавьте --apply" in out
    assert note.read_text(encoding="utf-8") == text
    assert not list(data.rglob("manifest.tsv"))


def test_fix_action_items_names_three_changed_lines_and_counts_the_rest(tmp_path, monkeypatch):
    fix, _, _ = _script_run(tmp_path, monkeypatch)
    rel = pathlib.Path("Минутки.md")

    def changes(n):
        return [(i, f"- [x] п{i}", f"- [ ] п{i}") for i in range(1, n + 1)]

    three = fix._status_note(rel, fix.StatusChanged(changes(3)))
    assert "3: «- [x] п3» → «- [ ] п3»" in three and "и ещё" not in three
    four = fix._status_note(rel, fix.StatusChanged(changes(4)))
    assert four.endswith("; 3: «- [x] п3» → «- [ ] п3» и ещё 1") and "4: «" not in four


@pytest.mark.parametrize("files, tail", [(20, None), (21, "не тронуто: и ещё 1")])
def test_fix_action_items_names_twenty_refusals_and_counts_the_rest(tmp_path, monkeypatch, capsys, files, tail):
    text = "## Поручения\n- [x] **Коля** — отчёт\n*   **Оля** — сверить цифры\n"
    fix, _, notes = _script_run(tmp_path, monkeypatch,
                                *[(f"{i:02d} Минутки.md", text) for i in range(files)])
    real = fix.normalize
    monkeypatch.setattr(fix, "normalize", lambda t: real(t).replace("- [x] **Коля**", "- [ ] **Коля**"))
    assert fix.main() == 1
    err = capsys.readouterr().err.splitlines()
    assert len([ln for ln in err if "статус изменился бы" in ln]) == 20
    assert [ln for ln in err if "и ещё" in ln and "статус" not in ln] == ([tail] if tail else [])
    assert all(q.read_text(encoding="utf-8") == text for q in notes)


# Общая таблица форм строки поручения: её же читают тесты Swift (Mac, iOS) и Kotlin
# (Android), поэтому ожидания лежат в JSON, а не в коде теста (№366, шаг 2).
TABLE = json.loads((ROOT / "tests" / "fixtures" / "task_lines.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", TABLE, ids=range(len(TABLE)))
def test_every_form_in_the_shared_table(row):
    line = row["line"]
    assert task_line.status(line) == row["status"]
    assert task_line.canonical(line) == row["canonical"]
    assert task_line.key(line) == row["key"]
    rec = task_line.parse(line)
    if row["status"] is None:
        assert rec is None
        return
    assert rec.status == row["status"] and rec.box == row["box"]
    assert rec.assignee == row["assignee"] and rec.text == row["text"] and rec.control == row["control"]
    assert rec.fields == {k: datetime.date.fromisoformat(v) for k, v in row["fields"].items()}
    assert task_line.render(rec) == row["render"]


def test_the_table_covers_every_form_the_module_knows():
    # новая форма в модуле без строки в таблице — красный тест здесь, а не тихий
    # разнобой с клиентами на Swift и Kotlin
    lines = [r["line"] for r in TABLE]
    assert {r["status"] for r in TABLE} == {*task_line.SETTLED, task_line.OPEN, None}
    assert {kind for r in TABLE if r["fields"] for kind in r["fields"]} == set(task_line.FIELDS)
    assert any(r["control"] and r["status"] == task_line.RETURNED for r in TABLE)
    for marker in ["-", "*", "+", "•", "–", "1.", "1)"]:
        assert any(ln.lstrip().startswith(f"{marker}") and task_line.status(ln) for ln in lines), marker
    assert any(re.search(r"[a-z]{3}", r["key"]) for r in TABLE), "английская строка"
    assert any(re.search(r"[一-鿿]", r["key"]) for r in TABLE), "китайская строка"


@pytest.mark.parametrize("row", [r for r in TABLE if r["render"] is not None], ids=lambda r: r["line"][:40])
def test_render_of_a_parsed_line_is_canonical_and_stable(row):
    # render выдаёт каноничную строку, а для каноничной строки разбор и вывод — тождество
    out = row["render"]
    assert task_line.render(task_line.parse(out)) == out
    # вывод теряет только форму маркера: всё остальное в записи доживает до строки
    assert task_line.parse(out) == dataclasses.replace(task_line.parse(row["line"]), marker="-")


def test_render_keeps_a_canonical_line_as_is():
    canonical = [r["line"] for r in TABLE if r["render"] == r["line"]]
    assert len(canonical) >= 20
    for line in canonical:
        assert task_line.render(task_line.parse(line)) == line


def test_fields_are_dates():
    rec = task_line.parse("- [x] **Участник А** — отчёт 📅 2026-10-01 ✅ 2026-09-24")
    assert rec.fields == {"due": datetime.date(2026, 10, 1), "done": datetime.date(2026, 9, 24)}


@pytest.mark.parametrize("box", [" ", "x", "X", "-", "/"])
def test_one_task_with_and_without_fields_in_any_status_has_one_key(box):
    bare = "**Участник А** — подготовить отчёт"
    base = task_line.key(bare)
    assert base == "участник а подготовить отчёт"
    for tail in ["", " 📅 2026-10-01", " ✅ 2026-09-24", " ❌ 2026-09-24",
                 " _(снято по сроку 24.09)_ 📅 2026-10-01 ❌ 2026-09-24"]:
        assert task_line.key(f"- [{box}] {bare}{tail}") == base
