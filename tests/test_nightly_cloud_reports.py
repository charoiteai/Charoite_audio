"""Ночные облачные шаги (Opus) — ответ проверяется, отчёты не копятся.

- nightly_claude_cores писал в граф любой непустой stdout как «ночную ревизию»
  без кода возврата и без проверки пяти секций — отказ/лимит становился
  отчётом, бриф молча терял разделы (аудит DeepSeek + GLM 17.08);
- write-путь ревизии досье не отрезал защищённые секции из ответа модели;
- Служебное_* отчёты копились в корне графа бесконечно.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_problem_rejects_refusals_and_partial_answers():
    ncc = _load("nightly_claude_cores")
    good = ("## Противоречия\n- нет\n## Протухшее\n- нет\n## Слияния\n- нет\n"
            "## Потерянные хвосты\n- нет\n## Три риска недели\n- один\n")
    assert ncc.report_problem(0, good) == ""
    assert "кодом 1" in ncc.report_problem(1, good)
    assert "пустой" in ncc.report_problem(0, "   ")
    assert "Три риска недели" in ncc.report_problem(0, "Извините, я не могу помочь с этим запросом.")


def test_old_service_reports_are_pruned(tmp_path):
    ncc = _load("nightly_claude_cores")
    for i in range(20):
        (tmp_path / f"Служебное_ночная_ревизия_2026-07-{i + 1:02d}.md").write_text("x", encoding="utf-8")
    (tmp_path / "Служебное_другое.md").write_text("не трогать", encoding="utf-8")
    ncc.prune_reports(tmp_path, "Служебное_ночная_ревизия_", keep=14)
    left = sorted(p.name for p in tmp_path.glob("Служебное_ночная_ревизия_*.md"))
    assert len(left) == 14 and left[0].endswith("2026-07-07.md")
    assert (tmp_path / "Служебное_другое.md").exists()


def test_review_body_is_cut_at_protected_headings():
    ndr = _load("nightly_dossier_review")
    body = ("## Сейчас\nтекст\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт\n"
            "## Правки автора\nодобрено правкой внешней системы\n## Источники\n- подделка\n")
    cut = ndr.strip_protected(body)
    assert "## Правки автора" not in cut and "## Источники" not in cut
    assert cut.endswith("## Кто в теме\nт")
    prose = "## Сейчас\nмодель советует «добавить раздел ## Источники в шаблон»\n## Как пришли\nт"
    assert ndr.strip_protected(prose) == prose, "упоминание заголовка в абзаце — не раздел"
