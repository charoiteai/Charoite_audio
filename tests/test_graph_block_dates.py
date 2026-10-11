"""Дата блока для свежести (№633): механизм даты.

Дата блока берётся схемой графа, без нового регэкспа: цель ссылки — встреча по
`is_meeting_link`, дата — `name_date`; плюс две машинные формы без ссылки
(«_(обновлено …)_» ядра и «_(последнее упоминание: …)_» узла) одним шаблоном.
Кэш дат — на версию файла (mtime + потолок блоков). Без моделей и сети.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from charoite_graph import graph_search as gs  # noqa: E402
from charoite_graph.model_seam import Embedder  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import graph_updater  # noqa: E402


def _embed(texts, timeout):   # векторов нет: механике даты они не нужны
    return []


def _searcher(tmp_path) -> gs.GraphSearch:
    return gs.GraphSearch(tmp_path / "g", embedder=Embedder(_embed, "t"), data_dir=None, schema=CHAROITE)


def _doc(rel, date_ts=0.0, text="", mtime=0.0) -> gs.Doc:
    return gs.Doc("/x/" + rel, rel, mtime, text, gs.norm(text), date_ts,
                  pathlib.PurePosixPath(rel).stem, gs.frontmatter.split(text)[1], "", schema=CHAROITE)


def _ts(iso: str) -> float:
    return gs._ts_from_iso(iso)


# ---------------------------------------------------------------- В: дата блока
@pytest.mark.parametrize("block,expected", [
    ("[[Встречи/2026-03-02_1000]]", "2026-03-02"),              # папка встреч с датой
    ("[[2026-03-02]]", "2026-03-02"),                           # без папки
    ("[[Встречи/2026-03-02_1000|алиас]]", "2026-03-02"),        # алиас после «|»
    ("[[Встречи/2026-03-02]] и [[Встречи/2026-08-01]]", "2026-08-01"),  # максимум
    ("[[Встречи/2026-02-30]]", None),                           # невалидный день
    ("[[Системы/Платёжный шлюз]]", None),                       # не встреча
    ("[[Архив/2026-08-01]]", "2026-08-01"),                     # дата в чужой папке
    ("[[Meetings/2026-01-02]]", "2026-01-02"),                  # англ. папка встреч
    ("[[Встречи/план без даты]]", None),                        # папка встреч без даты
    ("[[ВСТРЕЧИ/2026-05-05]]", "2026-05-05"),                   # заглавные буквы
    ("[[Папка/2026-0", None),                                   # обрезанная ссылка
    ("[[Проект 2026-01-01 план]]", "2026-01-01"),               # дата внутри цели — принято
    ("_(обновлено 2026-09-12)_", "2026-09-12"),                 # штамп ядра
    ("_(последнее упоминание: 2026-09-29)_", "2026-09-29"),     # штамп узла
])
def test_block_date_reads_links_and_stamps(block, expected):
    assert gs.block_date(block, CHAROITE) == (None if expected is None else _ts(expected))


def test_the_chronicle_line_is_dated_by_its_own_link():
    """Строка «_(было: … → стало: …)_» датируется ссылкой строки — отдельного
    разбора её грамматики в поиске нет."""
    text = "# Иван\nСтарое описание.\n"
    out, event = graph_updater._supersede_description(
        text, "Новое описание", "Встречи/2026-09-30_1000")
    assert event is not None
    line = next(ln for ln in out.splitlines() if "было:" in ln)
    assert gs.block_date(line, CHAROITE) == _ts("2026-09-30")


# ------------------------------------------------------------ Г: дата блока файла
def test_file_without_dated_blocks_falls_back_to_the_file_date():
    d = _doc("Документация/заметка.md", date_ts=111.0)
    dates = gs.file_block_dates(d, CHAROITE, ["без даты", "тоже"])
    assert [x.ts for x in dates] == [111.0, 111.0]


def test_undated_block_inherits_the_max_of_dated_blocks():
    d = _doc("Люди/Иван.md", date_ts=1.0)
    blocks = ["[[Встречи/2026-03-02]] и [[Встречи/2026-07-05]]", "блок без даты"]
    dates = gs.file_block_dates(d, CHAROITE, blocks)
    assert dates[0].ts == _ts("2026-07-05")
    assert dates[1].ts == _ts("2026-07-05")


def test_the_inherited_date_is_the_maximum_not_the_median():
    """Медиана ставила текущее описание на март при хронике июля и штампе
    сентября: унаследованная дата — максимум датированных блоков, не середина."""
    d = _doc("Люди/Иван.md", date_ts=1.0)
    blocks = ["[[Встречи/2026-03-02]] март", "[[Встречи/2026-07-05]] июль",
              "_(обновлено 2026-09-12)_ сентябрь", "блок без даты"]
    dates = gs.file_block_dates(d, CHAROITE, blocks)
    assert dates[3].ts == _ts("2026-09-12"), "максимум (сентябрь), не медиана (июль)"


def test_node_head_with_a_stamp_is_dated_by_it_and_without_inherits():
    d = _doc("Люди/Иван.md", date_ts=1.0)
    with_stamp = ["# Иван\n\n_(последнее упоминание: 2026-09-29)_",
                  "хроника [[Встречи/2026-07-01]]", "блок без даты"]
    dates = gs.file_block_dates(d, CHAROITE, with_stamp)
    assert dates[0].ts == _ts("2026-09-29")
    assert dates[1].ts == _ts("2026-07-01"), "у блока своя дата — берётся она"
    assert dates[2].ts == _ts("2026-09-29"), "без штампа — максимум датированных блоков"
    without = ["# Иван\n\nтекст без штампа", "старый блок [[Встречи/2026-07-01]]"]
    assert gs.file_block_dates(d, CHAROITE, without)[0].ts == _ts("2026-07-01")


def test_fallback_block_of_an_empty_body_carries_the_file_date(tmp_path):
    s = _searcher(tmp_path)
    d = _doc("Документация/пусто.md", date_ts=777.0, text="")
    dates = s._doc_block_dates(d)
    assert len(dates) == 1 and dates[0].ts == 777.0


def test_meeting_with_a_date_in_the_name_ignores_inner_links():
    d = _doc("Встречи/2026-09-30_1000.md", date_ts=_ts("2026-09-30"))
    blocks = ["[[Встречи/2026-03-02]] старое", "[[Встречи/2099-01-01]] будущее"]
    dates = gs.file_block_dates(d, CHAROITE, blocks)
    assert all(x.ts == _ts("2026-09-30") for x in dates)


def test_a_future_date_never_ranks_above_the_present():
    now = _ts("2026-10-10")
    future = gs.block_date("[[Встречи/2099-01-01]]", CHAROITE)
    assert gs.recency_factor(future, now) == 1.0


def test_a_node_with_a_long_history_keeps_the_head_at_least_as_fresh_as_the_chronicle():
    """Дата головы ≥ даты хроники: восьмидесяти встречам и трём вытеснениям
    середину не отдают максимуму хвоста."""
    links = "\n".join(f"- [[Встречи/2026-0{(i % 9) + 1}-{i % 28 + 1:02d}_{1000 + i}]]" for i in range(80))
    chronicle = "\n".join(
        f"- [[Встречи/2026-07-{d:02d}_0900]] — _(было: «x» → стало: «y»)_" for d in (1, 2, 3))
    text = (f"# Иван\n\n_(последнее упоминание: 2026-09-29)_\n\n"
            f"## Хроника\n{chronicle}\n\n## Встречи\n{links}\n")
    d = _doc("Люди/Иван.md", date_ts=1.0, text=text)
    dates = gs.file_block_dates(d, CHAROITE, gs.chunks("Иван", text, limit=gs.MAX_CHUNKS_NODE))
    assert dates[0].ts >= gs.block_date(chronicle, CHAROITE)
    assert dates[0].ts == _ts("2026-09-29")


# -------------------------------------------------------------- Е: кэш дат блоков
def test_block_dates_are_recomputed_only_when_the_file_version_changes(tmp_path, monkeypatch):
    s = _searcher(tmp_path)
    calls = {"n": 0}
    real = gs.chunks

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(gs, "chunks", counting)
    d = _doc("Люди/Иван.md", mtime=10.0, text="# Иван\n\n## Встречи\n- [[Встречи/2026-03-02]]\n")
    first = s._doc_block_dates(d)
    assert s._doc_block_dates(d) == first and calls["n"] == 1, "повтор — из кэша, нарезка не считается"
    d.mtime = 11.0
    s._doc_block_dates(d)
    assert calls["n"] == 2, "смена mtime — новая версия файла"
    monkeypatch.setattr(s, "_block_limit", lambda _d: gs.MAX_CHUNKS)
    s._doc_block_dates(d)
    assert calls["n"] == 3, "смена потолка блоков — другая нарезка"


# ------------------------------------------------ будущая дата — не дата факта
def test_a_planned_meeting_in_the_future_is_not_a_date_of_the_block(tmp_path):
    """«Следующая встреча: [[Встречи/ГГГГ-ММ-ДД]]» — план, не факт: ни метки, ни
    наследования соседним блоком. Граница — сутки после «сейчас»."""
    now = _ts("2026-10-10")
    s = gs.GraphSearch(tmp_path / "g", embedder=Embedder(_embed, "t"), data_dir=None,
                       schema=CHAROITE, now=lambda: now)
    text = ("# Дорожная карта\n\n## План\n" + "пункт плана. " * 200
            + "\nСледующая встреча: [[Встречи/2026-12-15]]\n\n"
            "## Итоги\n- [[Встречи/2026-09-01_1000]] — итог квартала. " + "текст. " * 200 + "\n")
    d = _doc("Люди/Дорожная карта.md", date_ts=5.0, text=text, mtime=1.0)
    dates = s._doc_block_dates(d)
    assert len(dates) >= 2
    assert all(x.ts != _ts("2026-12-15") for x in dates), "будущая дата не стала датой блока"
    plan = next(x for x, blk in zip(dates, gs.chunks("Дорожная карта", text, limit=gs.MAX_CHUNKS))
                if "2026-12-15" in blk)
    assert plan.ts == _ts("2026-09-01"), "блок плана унаследовал факт, а не план"
    tomorrow = gs.block_date("[[Встречи/2026-10-11]]", CHAROITE)
    assert gs._block_date_marked("[[Встречи/2026-10-11]]", CHAROITE, now + 86400).ts == tomorrow
    assert gs._block_date_marked("[[Встречи/2026-10-12]]", CHAROITE, now + 86400) is None
    assert gs._block_date_marked("_(обновлено 2026-12-01)_", CHAROITE, now + 86400) is None


def test_a_planned_meeting_puts_no_label_on_the_fragment(tmp_path):
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация", "Заметки"):
        (g / d).mkdir(parents=True, exist_ok=True)
    (g / "Заметки" / "Дорожная карта.md").write_text(
        "# Дорожная карта\nСледующая встреча: [[Встречи/2026-12-15]] — обсудим дорожную карту\n",
        encoding="utf-8")
    s = gs.GraphSearch(g, embedder=Embedder(_embed, "t"), data_dir=None, schema=CHAROITE,
                       now=lambda: _ts("2026-10-10"))
    s.refresh(force=True)
    block = next(b for b in s.search("дорожную карту", limit=3, semantic=False).blocks
                 if b.startswith("• Заметки/Дорожная карта.md"))
    assert "[встреча" not in block


# ------------------------------------- наследование «не верить mtime» — только узлам
def test_only_a_node_inherits_the_freshest_date_of_its_blocks():
    """У узла mtime переписывает каждая встреча — блок без даты берёт максимум
    датированных. У не-узла mtime надёжен: блок без даты идёт по дате файла."""
    blocks = ["# Решение\n## Итог\n- [[Встречи/2025-03-02_1000]] — старое\n",
              "# Решение\n## Обзор\nбез дат\n"]
    node = _doc("Люди/Иван.md", date_ts=_ts("2026-10-09"))
    plain = _doc("Документация/Решение по шлюзу.md", date_ts=_ts("2026-10-09"))
    assert gs.file_block_dates(node, CHAROITE, blocks)[1].ts == _ts("2025-03-02")
    got = gs.file_block_dates(plain, CHAROITE, blocks)
    assert got[1].ts == _ts("2026-10-09"), "не-узел: дата файла"
    assert got[0].ts == _ts("2025-03-02"), "размеченный блок не-узла датируется ссылкой"


def test_a_plain_document_with_an_old_link_keeps_its_place(tmp_path, monkeypatch):
    """Не-узел со свежим mtime и ссылкой на старую встречу в соседней секции: искомое
    в секции без дат — позиция та же, что на `main`, при обоих значениях флага."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    (g / "Документация" / "Решение по шлюзу.md").write_text(
        "# Решение по шлюзу\n## История\n- [[Встречи/2025-03-02_1000]] — обсуждали давно. "
        + "справка. " * 600
        + "\n## Решение\nшлюз Омега принят, срок май\n", encoding="utf-8")
    (g / "Встречи" / "2026-09-01_1000.md").write_text("# Омега\nшлюз Омега обсуждали, срок май\n",
                                                       encoding="utf-8")
    now = _ts("2026-10-10")
    os_utime = os.utime
    os_utime(g / "Документация" / "Решение по шлюзу.md", (now - 3600, now - 3600))
    pos = {}
    for flag in (True, False):
        monkeypatch.setattr(gs, "BLOCK_DATES", flag)
        s = gs.GraphSearch(g, embedder=Embedder(_embed, "t"), data_dir=None, schema=CHAROITE, now=lambda: now)
        s.refresh(force=True)
        rels = [b.split("\n")[0][2:] for b in s.search("шлюз Омега срок май", limit=4, semantic=False).blocks]
        pos[flag] = rels.index("Документация/Решение по шлюзу.md")
    assert pos[True] == pos[False]
