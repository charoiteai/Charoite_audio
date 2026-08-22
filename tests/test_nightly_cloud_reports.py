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


def _core(folder: pathlib.Path, name: str, size: int, mtime: float) -> pathlib.Path:
    import os
    p = folder / f"{name}.md"
    p.write_text("x" * size, encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def test_selection_prefers_fresh_and_never_cuts_a_core_in_half(tmp_path):
    """Разбор 22.08: sorted() по алфавиту и blob[:60_000] — при 161 свежем
    ядре в промпт попадали 20, всегда «А–В», последнее обрывком. Теперь —
    по свежести, бюджет по целым ядрам."""
    ncc = _load("nightly_claude_cores")
    a = _core(tmp_path, "Аврал", 3000, 1000)      # старое, первое по алфавиту
    b = _core(tmp_path, "Ядро Я", 3000, 3000)     # самое свежее, последнее по алфавиту
    c = _core(tmp_path, "Большое", 9000, 2000)    # не влезает
    d = _core(tmp_path, "Мелкое", 500, 1500)      # влезет после пропуска большого
    chosen, blob = ncc.select_cores([a, b, c, d], seen={}, budget=7000, index_text="ИНДЕКС")
    assert chosen == [b, d, a]                    # свежее первым, большое пропущено
    assert "## ЯДРО: Большое" not in blob
    assert blob.startswith("## ИНДЕКС\nИНДЕКС")
    for p in chosen:
        assert f"## ЯДРО: {p.stem}\n" + "x" * (p.stat().st_size) in blob, "ядро целиком"


def test_selection_rotates_by_what_changed_since_last_run(tmp_path):
    """Курсор: ядра, изменившиеся с прошлого прогона, идут первыми — иначе
    одни и те же свежие ядра ночь за ночью занимали бы весь бюджет."""
    ncc = _load("nightly_claude_cores")
    a = _core(tmp_path, "А", 1000, 5000)
    b = _core(tmp_path, "Б", 1000, 4000)
    seen = {"А": 5000.0}                          # А уже показывали в этом виде
    chosen, _ = ncc.select_cores([a, b], seen=seen, budget=1500)
    assert chosen == [b], "изменившееся с прошлого раза — первым, А не поместилось"
    chosen, _ = ncc.select_cores([a, b], seen={}, budget=2500)
    assert chosen == [a, b]                       # без курсора — по свежести
