"""Запись после конца звонка не уходит из машины владельца (№733).

Три писателя файлов встречи — папка архива (`plan_materials`), «Документация»
графа (`copy_to_vault_docs`) и облачная ревизия (`cloud_enrich_context`) — идут
через одно правило `meeting_stamp.publishable`: файла `_после_звонка` нет ни в
одном, а у такой встречи — и подсказок с живым черновиком."""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import graph_updater  # noqa: E402
import meeting_archive as ma  # noqa: E402

KEY = "2026-10-17_2000_Планёрка"
SECRET = "что у нас на ужин"


def _meeting(tdir: pathlib.Path) -> None:
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{KEY}.md").write_text("# Встреча\n\n**Владелец** [20:00]:\nДо связи.\n", encoding="utf-8")
    (tdir / f"{KEY}_minutes.md").write_text("# Минутки\n", encoding="utf-8")
    (tdir / f"{KEY}_hints.md").write_text(f"подсказка: {SECRET}\n", encoding="utf-8")
    (tdir / f"{KEY}_после_звонка.md").write_text(
        f"# После звонка\n\n**Владелец** [20:02]:\n{SECRET}\n", encoding="utf-8")


def test_archive_plan_has_no_after_call_record_nor_live_hints(tmp_path):
    _meeting(tmp_path)
    plan = ma.plan_materials(tmp_path, KEY, None)
    names = {p.name for p in plan.values()}
    assert names == {f"{KEY}.md", f"{KEY}_minutes.md"}
    assert plan["Стенограмма.md"].name == f"{KEY}.md"


def test_vault_docs_copy_has_no_after_call_record(tmp_path):
    tdir, graph = tmp_path / "t", tmp_path / "g"
    _meeting(tdir)
    (graph / "Документация").mkdir(parents=True)
    vdocs = graph_updater.copy_to_vault_docs(tdir / f"{KEY}.md", graph, log=lambda _m: None)
    names = sorted(p.name for p in vdocs.iterdir())
    assert names == [f"{KEY}.md", f"{KEY}_minutes.md"]
    assert all(SECRET not in p.read_text(encoding="utf-8") for p in vdocs.iterdir())


def test_cloud_context_has_no_after_call_text(tmp_path):
    _meeting(tmp_path)
    text, names = graph_updater.cloud_enrich_context(tmp_path, KEY)
    assert SECRET not in text
    assert names == [f"{KEY}.md", f"{KEY}_minutes.md"]
