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

import action_items  # noqa: E402
import channel_trace  # noqa: E402
import fact_check  # noqa: E402
import meeting_source  # noqa: E402
import privacy  # noqa: E402

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
        doc, meeting_source.live(speech, note), owner="Анна", participants=PEOPLE, lang="ru")


def test_same_bytes_as_the_old_final_chain():
    """Новая цепочка на замороженном документе даёт байты прежней цепочки
    «Протокола» демона (annotate → normalize → finalize → with_note) и
    скраба писателя: перенос шагов в вход поведения не меняет."""
    src = meeting_source.live(SPEECH, NOTE)
    old = fact_check.annotate(DOC, src.canon())
    old = action_items.normalize(old)
    old = action_items.finalize_assignees(old, PEOPLE, "Анна", lang="ru")
    old = meeting_source.with_note(old, NOTE)
    old, _n = privacy.scrub_local_paths(old)
    assert _finish() == old
    assert "TASK 3242" in old.split(fact_check.FOOTNOTE_MARK)[1], "выдуманный номер — в сноске"
    assert "[ ]" in old, "поручения — чекбоксами окна «Задачи»"


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
        for step in ("fact_check.annotate(", "action_items.normalize(",
                     "finalize_assignees(", "with_note("):
            assert step not in body, f"{name}: свой шаг {step} мимо входа"


def test_app_tail_marks_mirror_the_python_writers():
    """Приложение обрывает разбор минуток на служебном хвосте по своим копиям
    констант: писатели — `channel_trace` и `fact_check`, копия обязана
    совпадать буква в букву, иначе хвост снова уйдёт пунктом в раздел."""
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services" / "MeetingMinutes.swift").read_text(encoding="utf-8")
    line = next(x for x in swift.splitlines() if "static let tailMarks" in x)
    assert f'"{channel_trace.SUMMARY_MARK}"' in line
    assert f'"{fact_check.FOOTNOTE_MARK}"' in line
