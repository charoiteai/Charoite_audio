"""Свежесть по дате лучшего блока, а не по mtime файла (№633, коммит 2).

Узел переписывает каждая новая встреча, поэтому mtime узла близок к «сейчас» при
любом возрасте факта, и `recency_factor` был ~1,0 — давний факт в узле вставал
впереди свежей встречи. Теперь балл трёх мест поиска (лексика в двух ветвях и
семантика) берёт дату блока-победителя. Без моделей и сети (подделка эмбеддинга).
"""
from __future__ import annotations

import hashlib
import math
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from charoite_graph import graph_search as gs  # noqa: E402
from charoite_graph.model_seam import Embedder  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402

NOW = gs._ts_from_iso("2026-10-10")
_SYN = {"финальное": "последнее"}


def _feat(texts, timeout):
    out = []
    for t in texts:
        v = [0.0] * 512
        for w in re.findall(r"\w+", gs.norm(t)):
            w = _SYN.get(w, w)
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 512] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _rels(result: gs.Result) -> list[str]:
    return [b.split("\n")[0][2:].strip() for b in result.blocks]


def _graph(tmp_path, node_text: str) -> gs.GraphSearch:
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Встречи-архив", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    (g / "Люди" / "Кира.md").write_text(node_text, encoding="utf-8")
    (g / "Встречи" / "2026-09-30_1000.md").write_text(
        "# Сроки\nУчастники: [[Люди/Кира]]\nРешили: проект Альфа — новое решение, срок май\n", encoding="utf-8")
    (g / "Встречи" / "2026-03-02_1000.md").write_text(
        "# Старое\nУчастники: [[Люди/Кира]]\nРешили: проект Альфа — старое решение\n", encoding="utf-8")
    os.utime(g / "Люди" / "Кира.md", (NOW - 86400, NOW - 86400))     # mtime узла — вчера
    s = gs.GraphSearch(g, embedder=Embedder(_feat, "t"), data_dir=tmp_path / "data",
                       schema=CHAROITE, now=lambda: NOW)
    s.refresh(force=True)
    s.embed_pending()
    return s


_Q = "последнее решение по проекту Альфа"
_OLD = "## Решения\n- [[Встречи/2026-03-02_1000]] — проект Альфа: старое решение. " + "наполнитель. " * 20
_NEW = "## Новости\n- [[Встречи/2026-09-30_1000]] — проект Альфа, финальное решение. " + "наполнитель. " * 20


def test_a_node_with_an_old_fact_goes_behind_the_fresh_meeting(tmp_path):
    """А/Б: узел с датированными блоками и mtime вчера — позади встречи 2026-09-30.

    Лексический победитель запроса — старый блок (покрытие равно, раньше в файле),
    поэтому дата узла — 2026-03-02, а не mtime. На `main` узел был бы первым."""
    s = _graph(tmp_path, "# Кира\n" + _OLD + "\n" + _NEW + "\n")
    rels = _rels(s.search(_Q, limit=4, semantic=False))
    assert rels[0] == "Встречи/2026-09-30_1000.md"
    assert rels.index("Встречи/2026-09-30_1000.md") < rels.index("Люди/Кира.md")


def test_a_node_with_only_old_blocks_goes_behind_the_fresh_meeting(tmp_path):
    """Б: узел только со старыми блоками и mtime вчера — позади свежей встречи."""
    s = _graph(tmp_path, "# Кира\n" + _OLD + "\n")
    rels = _rels(s.search(_Q, limit=4, semantic=False))
    assert rels[0] == "Встречи/2026-09-30_1000.md"
    assert rels.index("Встречи/2026-09-30_1000.md") < rels.index("Люди/Кира.md")


def test_the_fresh_meeting_wins_with_semantics_on_too(tmp_path):
    """Семантическая ветвь считает свежесть по дате блока-победителя так же."""
    s = _graph(tmp_path, "# Кира\n" + _OLD + "\n" + _NEW + "\n")
    rels = _rels(s.search(_Q, limit=4))
    assert rels[0] == "Встречи/2026-09-30_1000.md"


def test_ranking_falls_back_to_the_file_date_without_a_winner(tmp_path):
    """Номер блока вне нарезки (победителя нет) — дата файла, как на `main`."""
    s = _graph(tmp_path, "# Кира\n" + _OLD + "\n" + _NEW + "\n")
    d = s._gen.docs[str(s.graph / "Люди" / "Кира.md")]
    assert s._block_ts(d, None) is None
    assert s._block_ts(d, 99) is None
    assert s._block_ts(d, 0) == gs.block_date(_OLD, CHAROITE)


def test_the_query_winners_date_is_not_written_into_the_doc(tmp_path):
    """Дата блока — локальная структура поиска, не поле `Doc`: `date_ts` не
    меняется, `owner_key` остаётся на нём."""
    s = _graph(tmp_path, "# Кира\n" + _OLD + "\n" + _NEW + "\n")
    before = {rel: d.date_ts for rel, d in s._gen.docs.items()}
    s.search(_Q, limit=4)
    assert {rel: d.date_ts for rel, d in s._gen.docs.items()} == before
