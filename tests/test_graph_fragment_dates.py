"""Дата во фрагменте выдачи (№633): фрагмент из блока-победителя и метка даты.

Фрагмент режется из блока-победителя (`snippet` по тексту блока), а не окном по
всему телу: окно склеило бы конец одного блока и начало другого, и одна дата на
такой фрагмент ложна. Метка — во второй строке блока, после `• путь`; у перехода
— третьей, после `↳ по ссылке из …`; у досье — после шапки. Без моделей и сети.
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
_SYN = {"поставщик": "провайдер"}          # «семантика» подделки: синоним — то же слово


def _fake_embed(texts, timeout):
    out = []
    for t in texts:
        v = [0.0] * 512
        for w in (w and _SYN.get(w, w) for w in re.findall(r"\w+", gs.norm(t))):
            if w:
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 512] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _searcher(tmp_path, **kw):
    kw.setdefault("schema", CHAROITE)
    kw.setdefault("now", lambda: NOW)
    s = gs.GraphSearch(tmp_path / "Работа", embedder=Embedder(_fake_embed, "t"),
                       data_dir=tmp_path / "data", **kw)
    return s


def _write(node_dir, name, text):
    (node_dir / name).write_text(text, encoding="utf-8")


def _kira_graph(tmp_path) -> gs.GraphSearch:
    """Узел человека с двумя длинными датированными блоками и встречи рядом."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Встречи-архив", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Люди", "Кира.md",
           "# Кира\n## Решения\n- [[Встречи/2026-03-02_1000]] — проект Альфа: старое решение по срокам. "
           + "Наполнитель старого блока. " * 20
           + "\n## Новости\n- [[Встречи/2026-09-30_1000]] — проект Альфа: новое решение, последнее слово. "
           + "Наполнитель нового блока. " * 20 + "\n")
    _write(g / "Встречи", "2026-09-30_1000.md",
           "# Сроки\nУчастники: [[Люди/Кира]]\nРешили: проект Альфа — новое решение, срок май\n")
    _write(g / "Встречи", "2026-03-02_1000.md",
           "# Старое\nУчастники: [[Люди/Кира]]\nРешили: проект Альфа — старое решение\n")
    node = g / "Люди" / "Кира.md"
    os.utime(node, (NOW - 86400, NOW - 86400))       # mtime узла — вчера
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    return s


def _block_for(result: gs.Result, rel: str) -> str:
    return next(b for b in result.blocks if b.startswith(f"• {rel}\n"))


# --------------------------------------------------------------- А/Д: фрагмент
def test_fragment_comes_from_the_winning_block_and_carries_its_date(tmp_path):
    s = _kira_graph(tmp_path)
    r = s.search("последнее решение по проекту Альфа", limit=4, semantic=False)
    block = _block_for(r, "Люди/Кира.md")
    lines = block.split("\n")
    assert lines[0] == "• Люди/Кира.md", "первая строка — путь, префикс её не трогает"
    assert lines[1].startswith("  [встреча 2026-09-30] "), "метка — во второй строке"
    assert "новое решение" in lines[1] and "старое решение" not in lines[1], \
        "фрагмент целиком из блока-победителя, не окно через границу блоков"


def test_a_block_without_a_date_has_no_prefix(tmp_path):
    """Узел без датированных блоков: дата — mtime файла, метки нет."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Системы", "Молоток.md",
           "# Молоток\n## Описание\n" + "термин Молоток и детали, " * 40 + "\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    r = s.search("термин Молоток детали", limit=3, semantic=False)
    block = _block_for(r, "Системы/Молоток.md")
    assert "[встреча" not in block and "[упомянуто" not in block


def test_the_last_seen_stamp_reads_upomnuto(tmp_path):
    """Штамп «последнее упоминание» — метка «упомянуто», а не «дата факта»."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Люди", "Пётр.md",
           "# Пётр\n## Статус\n_(последнее упоминание: 2026-09-29)_ проект Альфа: "
           + "последнее решение по срокам. " * 25 + "\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    r = s.search("последнее решение по проекту Альфа", limit=3, semantic=False)
    block = _block_for(r, "Люди/Пётр.md")
    assert block.split("\n")[1].startswith("  [упомянуто 2026-09-29] ")


def test_the_main_window_is_kept_when_the_winner_block_has_no_needles(tmp_path):
    """Победитель есть, но игл в его тексте нет — фрагмент как на `main`, без метки."""
    s = _kira_graph(tmp_path)
    d = s._gen.docs[str(s.graph / "Люди" / "Кира.md")]
    main = s._fragment(d, re.compile("несуществующая_игла"), 500, [])
    frag, bd = s._block_fragment(d, 0, main, ["несуществующая_игла"],
                                 re.compile("несуществующая_игла"), 500, [], False)
    assert frag == main and bd is None


def test_a_short_node_is_shown_whole_but_dated_by_the_winner(tmp_path):
    """Тело в пределах окна фрагмента показывается целиком; дату даёт победитель."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Встречи", "Ядра", "Системы", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Люди", "Нина.md",
           "# Нина\n## Решения\n- [[Встречи/2026-09-30_1000]] — проект Бета, решение принято\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    r = s.search("решение по проекту Бета", limit=3, semantic=False)
    block = _block_for(r, "Люди/Нина.md")
    lines = block.split("\n")
    assert lines[1].startswith("  [встреча 2026-09-30] ")
    assert "решение принято" in lines[1]


# ------------------------------------------------------------------- досье
def test_the_dossier_date_is_taken_from_the_shown_slice(tmp_path):
    from charoite_graph import dossier as dossier_mod
    s = _kira_graph(tmp_path)
    folder = s.graph / CHAROITE.dossier_dir
    folder.mkdir()
    near = "# Тема\nсм. [[Встречи/2026-09-30_1000]] и дальше\n"
    far = "# Тема\n" + "наполнитель без ссылок. " * 200 + "[[Встречи/2026-01-01_1000]]\n"
    (folder / "Тема.md").write_text(near, encoding="utf-8")
    dossier_mod.write_index(folder, [{"тема": "Тема", "ключи": ["термин"], "источников": 1,
                                      "собрано": "2026-09-30"}])
    s.refresh(force=True)
    r = s.search("термин", limit=2)
    assert r.dossiers and r.dossiers[0].split("\n")[1].startswith("  [встреча 2026-09-30] ")
    # ссылка после обрезки в 1500 знаков даты не даёт
    (folder / "Тема.md").write_text(far, encoding="utf-8")
    s.refresh(force=True)
    r2 = s.search("термин", limit=2)
    assert r2.dossiers and "[встреча" not in r2.dossiers[0] and "[упомянуто" not in r2.dossiers[0]


# ------------------------------------------------------------------- метка у перехода
def test_a_hop_carries_the_date_on_the_third_line(tmp_path):
    s = _kira_graph(tmp_path)
    r = s.search("Кира", limit=1)         # только узел в слотах — переход к встрече
    hop = next((b for b in r.blocks if "↳ по ссылке из" in b), None)
    assert hop is not None, "переход из узла Кира на встречу"
    lines = hop.split("\n")
    assert lines[0].startswith("• ") and lines[1].startswith("  ↳ по ссылке из ")
    assert lines[2].startswith("  [встреча 2026-")


# --------------------------------------------------- Е: чужая длина вектора
def test_a_vector_of_another_slicing_is_not_a_witness(tmp_path):
    """Вектор другой длины, чем у текущей нарезки, — не свидетель: файл, который
    находит только семантика, из выдачи уходит (индекс блока ничего не значит)."""
    g = tmp_path / "Работа"
    for d in ("Системы", "Люди", "Встречи", "Ядра", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Системы", "Поставщик.md",
           "# Поставщик\n## Статус\nпоставщик платежей подписан договор\n"
           + "## Заметки\n" + "наполнитель. " * 30 + "\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    assert _rels(s.search("провайдер", limit=3)) == ["Системы/Поставщик.md"]
    path = str(s.graph / "Системы" / "Поставщик.md")
    mt, vs = s._vecs[path]
    s._vecs[path] = (mt, vs[:1])          # чужая нарезка: длина ≠ _expected_blocks
    assert _rels(s.search("провайдер", limit=3)) == [], "вектор чужой длины — не свидетель"


def _rels(result: gs.Result) -> list[str]:
    return [b.split("\n")[0][2:].strip() for b in result.blocks]


# --------------------------------------------------------------- Ж: флаг отката
def test_the_rollback_flag_leaves_the_output_as_main(tmp_path, monkeypatch):
    """Флаг выключен — ни метки, ни блока-победителя: механизм не зовётся вовсе."""
    s = _kira_graph(tmp_path)
    monkeypatch.setattr(gs, "BLOCK_DATES", False)
    touched = []
    for name in ("_block_fragment", "_lex_winner", "_doc_block_dates"):
        real = getattr(s, name)
        monkeypatch.setattr(s, name, lambda *a, _n=name, _r=real, **kw: touched.append(_n) or _r(*a, **kw))
    for q in ("последнее решение по проекту Альфа", "Кира", "старое решение"):
        text = gs.render(s.search(q, limit=4), q)
        assert "[встреча" not in text and "[упомянуто" not in text
        assert s.search(q, limit=4).blocks == s.search(q, limit=4).blocks, "выдача детерминирована"
    assert touched == [], "при выключенном флаге механизм дат не трогается"


# ----------------------------------------------------------- З: owner_key/date_ts
def test_block_dates_do_not_touch_date_ts_or_the_owner_key(tmp_path):
    s = _kira_graph(tmp_path)
    before = {rel: d.date_ts for rel, d in s._gen.docs.items()}
    s.search("последнее решение по проекту Альфа", limit=4)
    assert {rel: d.date_ts for rel, d in s._gen.docs.items()} == before, "поиск не пишет Doc.date_ts"

    fresh = gs.Doc("", "Люди/Ёлка.md", 0.0, "", "", 200.0, "Ёлка", "", "", schema=CHAROITE)
    assert gs.owner_key(fresh) == (-200.0, "Люди/Ёлка.md")
    twins = gs._owned([gs.Doc("", "Ядра/Ёлка.md", 0.0, "", "", 200.0, "ёлка", "", "", schema=CHAROITE),
                       gs.Doc("", "Ядра/Елка.md", 0.0, "", "", 100.0, "елка", "", "", schema=CHAROITE)],
                      lambda d: d.key)
    assert len(twins) == 1, "ё/е — один ключ"
    assert next(iter(twins.values())).rel == "Ядра/Ёлка.md", "хозяин тёзок — свежайший по Doc.date_ts"
