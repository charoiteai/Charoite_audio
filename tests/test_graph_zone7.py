"""Граф после аудита зон 13.09 (зона 7, DS + GLM, карточка №245).

- строка MOC и строки слияния проверялись подстрокой: минутный штамп — префикс
  посекундного, вторая встреча той же минуты выпадала из оглавления;
- один нечитаемый узел роняла весь графовый этап встречи;
- ссылка «## Ядра» в заметке встречи уходила в одноимённый узел другого типа;
- строка хроники ядра вставлялась по первому вхождению подстроки «## Хроника»;
- указатель папки считал ссылки из прозы, когда секции «## Встречи» нет;
- на тему с двумя ядрами-кандидатами заводилось третье.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import graph_updater as gu  # noqa: E402

LINK = "Встречи/2026-09-13_1200"


def _graph(tmp_path: Path) -> Path:
    g = tmp_path / "Граф"
    for d in ("Встречи", "Люди", "Системы", "Ядра"):
        (g / d).mkdir(parents=True)
    return g


def test_moc_line_is_checked_by_link_boundary_not_substring(tmp_path):
    moc = tmp_path / "_MOC.md"
    moc.write_text("# MOC\n\n## 🗓 Встречи\n- [[Встречи/2026-09-13_113012|Первая]] — тема\n",
                   encoding="utf-8")
    assert gu.append_moc_line(moc, "Встречи/2026-09-13_1130", "- [[Встречи/2026-09-13_1130|Вторая]] — тема") is True
    text = moc.read_text(encoding="utf-8")
    assert "[[Встречи/2026-09-13_1130|Вторая]]" in text and "[[Встречи/2026-09-13_113012|Первая]]" in text
    assert gu.append_moc_line(moc, "Встречи/2026-09-13_113012", "- [[Встречи/2026-09-13_113012|Первая]] — дубль") is False
    assert text == moc.read_text(encoding="utf-8")


def test_unreadable_node_is_skipped_with_a_journal_line_not_a_crash(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    monkeypatch.setattr(gu, "ROOT", tmp_path)
    (g / "Люди" / "Иван.md").write_bytes(b"---\ntype: person\n---\n# \xff\xfe \x80\n")
    gu.upsert_entity(g, "Люди", "Иван", "человек", "", LINK, "сказал")
    assert (g / "Люди" / "Иван.md").read_bytes().startswith(b"---\ntype: person")
    journal = (tmp_path / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "узел не прочитан: Люди/Иван" in journal

    (g / "Ядра" / "Тема.md").write_bytes((b"# \xff\n" + "## Статус\nx\n".encode("utf-8")))
    gu.upsert_core(g, {"имя": "Тема", "статус": "новый"}, LINK, "2026-09-13_1200")
    assert (g / "Ядра" / "Тема.md").read_bytes() == (b"# \xff\n" + "## Статус\nx\n".encode("utf-8"))

    (g / "Ядра" / "Живое.md").write_text("# Живое\n## Статус\nв работе\n", encoding="utf-8")
    gu.rebuild_cores_moc(g)
    idx = (g / "Ядра" / "_ЯДРА.md").read_text(encoding="utf-8")
    assert "[[Ядра/Живое|Живое]] — в работе" in idx and "Тема" not in idx


def test_core_link_in_meeting_note_prefers_the_core_over_a_namesake_system(tmp_path):
    g = _graph(tmp_path)
    (g / "Системы" / "Витрина.md").write_text("# Витрина\n", encoding="utf-8")
    assert gu.canon_link(g, "Витрина", "Ядра") == "[[Системы/Витрина|Витрина]]", "ядра нет — прежняя политика"
    (g / "Ядра" / "Витрина.md").write_text("# Витрина\n## Статус\nx\n## Хроника\n", encoding="utf-8")
    assert gu.canon_link(g, "Витрина", "Ядра") == "[[Ядра/Витрина|Витрина]]"
    assert gu.canon_link(g, "Витрина", "Системы") == "[[Системы/Витрина|Витрина]]"


def test_chronicle_line_goes_under_the_heading_not_the_first_substring(tmp_path):
    g = _graph(tmp_path)
    core = g / "Ядра" / "Тема.md"
    core.write_text("---\ntype: ядро\nописание: см. ## Хроника ниже\n---\n# Тема\n\n"
                    "## Статус\nстарый _(обновлено 2026-09-01)_\n\n## Хроника\n- [[Встречи/2026-09-01_1000]]\n",
                    encoding="utf-8")
    gu.upsert_core(g, {"имя": "Тема", "статус": "новый", "обновление": "решили"}, LINK, "2026-09-13_1200")
    text = core.read_text(encoding="utf-8")
    assert "описание: см. ## Хроника ниже\n---" in text, "строка хроники вклинилась в шапку"
    assert "## Хроника\n- [[Встречи/2026-09-13_1200]] — решили" in text
    assert text.count("## Хроника") == 2


def test_folder_index_counts_only_the_meetings_section(tmp_path):
    g = _graph(tmp_path)
    (g / "Люди" / "Анна.md").write_text("# Анна\n\n## Встречи\n- [[Встречи/2026-09-01_1000]]", encoding="utf-8")
    (g / "Люди" / "Борис.md").write_text("# Борис\nВ прозе [[Встречи/2026-08-01_1000]] и [[Встречи/2026-08-02_1000]].\n",
                                        encoding="utf-8")
    gu.rebuild_folder_index(g, "Люди")
    idx = (g / "Люди" / "_ЛЮДИ.md").read_text(encoding="utf-8")
    assert "| [[Люди/Анна\\|Анна]] | 1 | 2026-09-01 |" in idx, idx
    assert "| [[Люди/Борис\\|Борис]] | 0 | — |" in idx, idx


def test_ambiguous_core_name_does_not_spawn_a_third_core(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    monkeypatch.setattr(gu, "ROOT", tmp_path)
    for n in ("Пилот проект 2026", "Пилот проект 2027"):
        (g / "Ядра" / f"{n}.md").write_text(f"# {n}\n## Статус\nидёт\n## Хроника\n", encoding="utf-8")
    gu.upsert_core(g, {"имя": "Пилот проект", "статус": "новый"}, LINK, "2026-09-13_1200")
    assert not (g / "Ядра" / "Пилот проект.md").exists(), "третье ядро на ту же тему"
    journal = (tmp_path / "logs" / "graph_unlinked.log").read_text(encoding="utf-8")
    assert "ядро неоднозначно: Пилот проект" in journal
    gu.upsert_core(g, {"имя": "Новая тема", "статус": "новый"}, LINK, "2026-09-13_1200")
    assert (g / "Ядра" / "Новая тема.md").exists(), "однозначно новая тема заводится как раньше"
