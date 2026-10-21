"""Общий вход механических шагов минуток (№732).

Четыре пути записи минуток — черновик и «Протокол» демона, MCP, пересборка —
держали свои цепочки: черновик и MCP без сверки фактов, пересборка без скраба
пути машины. Теперь шаги и их порядок — `meeting_source.finish_minutes`.
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import channel_trace  # noqa: E402
import fact_check  # noqa: E402
import meeting_source  # noqa: E402

SPEECH = (
    "[10:32] Анна: по TASK 3278 вопросы есть?\n"
    "[10:35] Борис: срок перенесли на 15.08\n"
    "[10:38] Анна: Борис, подготовь отчёт к пятнице\n"
)
NOTE = channel_trace.SUMMARY_MARK + ": канал собеседников 10:40–10:52 (12 мин)"
DOC = (
    "# Минутки\n\n## Решения\n- TASK 3278 закрыть к 15.08\n- TASK 3242 на препроде\n\n"
    "## Поручения\n- **Борис** — подготовить отчёт — пятница\n"
    "- **Виктор** — проверить стенд — 20.09\n"
)
PEOPLE = {"Анна", "Борис"}


def _finish(doc=DOC, speech=SPEECH, note=NOTE):
    return meeting_source.finish_minutes(
        doc, meeting_source.live(speech, note, began_at=None), owner="Анна", participants=PEOPLE, lang="ru")


#: байты прежней цепочки «Протокола» демона (annotate → normalize → finalize →
#: with_note → скраб писателя), сняты на c7665328 до переноса шагов во вход
OLD_CHAIN_BYTES = (
    "# Минутки\n\n## Решения\n- TASK 3278 закрыть к 15.08\n- TASK 3242 на препроде\n\n"
    "## Поручения\n- [ ] **Борис** — подготовить отчёт — пятница\n"
    "- ⚠ не участник (Виктор): **Виктор** — проверить стенд — 20.09\n\n---\n"
    "⚠️ Нет в стенограмме, проверьте: 20.09, TASK 3242\n\n"
    "📋 запись неполная: канал собеседников 10:40–10:52 (12 мин)\n"
)


def test_same_bytes_as_the_old_final_chain():
    """Новая цепочка на замороженном документе даёт байты прежней цепочки,
    снятые литералом с базы: перенос шагов во вход поведения не меняет, и
    правка самих шагов не прячется за эталоном из тех же функций (DS M3)."""
    assert _finish() == OLD_CHAIN_BYTES
    assert "TASK 3242" in OLD_CHAIN_BYTES.split(fact_check.FOOTNOTE_MARK)[1], "выдуманный номер — в сноске"
    assert "[ ]" in OLD_CHAIN_BYTES, "поручения — чекбоксами окна «Задачи»"


def test_finish_is_idempotent():
    """Повторный вызов по готовому документу (пересборка поверх черновика)
    не дописывает вторую сноску и вторую строку оговорки."""
    once = _finish()
    assert _finish(once) == once
    assert once.count(fact_check.FOOTNOTE_MARK) == 1 and once.count(NOTE) == 1


def test_annotate_twice_keeps_one_footnote():
    """Факты сноски по построению не в стенограмме: без снятия прежней
    сноски второй вызов дописывал второй блок (Sonnet I2 по №732)."""
    once = fact_check.annotate(DOC, SPEECH)
    assert fact_check.annotate(once, SPEECH) == once


def test_fact_from_the_middle_of_a_long_meeting_is_not_flagged():
    """Черновик режет речь окном для модели, а сверка — по всей речи: дата
    из срезанной середины не выдумка (Sonnet I1 по №732)."""
    middle = "[11:00] Борис: релиз 23.09\n"
    speech = "x" * 5_000 + "\n" + middle + "y" * 20_000
    out = _finish("# Минутки\n- релиз 23.09\n", speech=speech, note=None)
    assert fact_check.FOOTNOTE_MARK not in out


def test_finish_scrubs_the_machine_path():
    """Пересборка писала документ модели без скраба (Sonnet I4 по №732):
    скраб — последний шаг входа, у всех путей."""
    home = str(pathlib.Path.home())
    out = _finish(DOC + f"\nЛог: {home}/charoite/logs/x.log\n")
    assert home + "/" not in out


def test_footnote_does_not_reach_extraction_prompts():
    """Сноска о выдуманных фактах — служебный хвост, не содержание: в промпт
    графа и разбора она несла бы те самые выдумки (Sonnet I3 по №732)."""
    body = meeting_source.without_trace(_finish())
    assert fact_check.FOOTNOTE_MARK not in body and NOTE not in body
    assert "TASK 3278" in body



def test_text_after_the_footnote_survives_extraction():
    """Снимается строка сноски, а не всё от неё до конца: `review_bridge`
    дописывает «Поручения» в конец, после сноски (№732, выход DS C1)."""
    body = ("# Минутки\n## Решения\n- смета\n\n---\n" + fact_check.FOOTNOTE_MARK
            + " TASK 3242\n\n## Поручения\n- [ ] **Борис** — подготовить отчёт\n")
    out = meeting_source.without_trace(body)
    assert fact_check.FOOTNOTE_MARK not in out and "TASK 3242" not in out
    assert "## Поручения" in out and "**Борис**" in out


def test_every_footnote_is_removed_and_annotate_converges():
    """Две сноски прежнего писателя снимаются обе: иначе первая уходит в
    извлечение содержанием, а `annotate` не сходится к одной (DS I3)."""
    f = "\n\n---\n" + fact_check.FOOTNOTE_MARK + " TASK 3242\n"
    doc = "# M\n- TASK 3278\n- TASK 3242\n" + f + f
    assert fact_check.FOOTNOTE_MARK not in fact_check.without_footnote(doc)
    once = fact_check.annotate(doc, SPEECH)
    assert once.count(fact_check.FOOTNOTE_MARK) == 1, "выдумка в теле — сноска ровно одна"
    assert fact_check.annotate(once, SPEECH) == once


def test_note_written_in_the_middle_moves_to_the_end():
    """Модель переписала оговорку дословно посреди текста: вход снимает её
    строку и ставит одну машинную в конец, повторный проход те же байты
    (№732, выход 2: Sonnet M3, DS M6)."""
    doc = DOC.replace("## Поручения", NOTE + "\n\n## Поручения")
    once = _finish(doc)
    assert once.count(NOTE) == 1
    assert once.rstrip("\n").endswith(NOTE)
    assert _finish(once) == once

def _function_source(path: pathlib.Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} не найдена в {path.name}")


def test_every_minutes_path_goes_through_the_entry():
    """Каждый путь записи минуток зовёт вход и не держит своих шагов: второй
    путь со своей цепочкой — тот дефект, который №732 закрывает."""
    paths = [
        (ROOT / "src" / "daemon.py", "minutes_loop"),
        (ROOT / "src" / "daemon.py", "_do_summary"),
        (ROOT / "src" / "mcp_server.py", "sufler_make_minutes"),
        (ROOT / "src" / "rebuild_transcript.py", "finalize_minutes"),
    ]
    for path, name in paths:
        body = _function_source(path, name)
        assert "meeting_source.finish_minutes(" in body, f"{name}: мимо входа минуток"
        for step in ("fact_check.annotate(", "without_footnote(", "action_items.normalize(",
                     "finalize_assignees(", "with_note(", "without_note(",
                     "scrub_local_paths(", "with_date(", "without_date("):
            assert step not in body, f"{name}: свой шаг {step} мимо входа"
        # начало встречи доезжает до входа на каждом пути: источник входа —
        # `of()` по файлу или `live(…, began_at=began_of(…))` (№738, Haiku I4
        # входа); по дереву, не по подстроке (выход out1, Sonnet M2 = Haiku M2)
        for source in _entry_sources(path, name):
            assert _carries_start(source), f"{name}: источник без начала встречи"


def _entry_sources(path: pathlib.Path, name: str) -> list[ast.expr]:
    """Источник каждого вызова `finish_minutes` в функции: выражение второго
    аргумента, а имя — значение его присваивания в той же функции."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    assigned = {t.id: n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "finish_minutes"]
    assert calls, f"{name}: нет вызова входа"
    out = []
    for call in calls:
        arg = call.args[1] if len(call.args) > 1 else next(k.value for k in call.keywords if k.arg == "source")
        out.append(assigned.get(arg.id, arg) if isinstance(arg, ast.Name) else arg)
    return out


def _carries_start(source: ast.expr) -> bool:
    if not (isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute)):
        return False
    if source.func.attr == "of":
        return True
    began = next((k.value for k in source.keywords if k.arg == "began_at"), None)
    return (source.func.attr == "live" and isinstance(began, ast.Call)
            and isinstance(began.func, ast.Attribute) and began.func.attr == "began_of")


def test_app_tail_marks_mirror_the_python_writers():
    """Приложение пропускает строки служебного хвоста минуток по своим копиям
    констант: писатели — `channel_trace` и `fact_check`, копия обязана
    совпадать буква в букву, иначе хвост снова уйдёт пунктом в раздел."""
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services" / "MeetingMinutes.swift").read_text(encoding="utf-8")
    line = next(x for x in swift.splitlines() if "static let tailMarks" in x)
    assert f'"{channel_trace.SUMMARY_MARK}"' in line
    assert f'"{fact_check.FOOTNOTE_MARK}"' in line


def test_app_ignores_every_date_label_the_entry_writes():
    """Строку даты пишет вход (`meeting_source.DATE_LABEL`), приложение её
    пропускает по своему списку меток; метка без звёзд и двоеточия обязана в
    нём быть (выход №738, DS M2: «date/time» в Swift не было)."""
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services" / "MeetingMinutes.swift").read_text(encoding="utf-8")
    line = next(x for x in swift.splitlines() if ".ignored)" in x)
    for label in meeting_source.DATE_LABEL.values():
        assert f'"{label.strip("*:：").lower()}"' in line, label
