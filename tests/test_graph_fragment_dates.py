"""Дата во фрагменте выдачи (№633): фрагмент из блока-победителя, метки даты нет.

Фрагмент режется из блока-победителя (`snippet` по тексту блока), а не окном по
всему телу: окно склеило бы конец одного блока и начало другого, и одна дата на
такой фрагмент ложна. Метки даты во фрагменте нет ни у блока, ни у перехода, ни у
досье. Без моделей и сети.
"""
from __future__ import annotations

import hashlib
import math
import os
import pathlib
import re
import sys

import pytest

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
def test_fragment_comes_from_the_winning_block_and_has_no_date_label(tmp_path):
    s = _kira_graph(tmp_path)
    r = s.search("последнее решение по проекту Альфа", limit=4, semantic=False)
    block = _block_for(r, "Люди/Кира.md")
    lines = block.split("\n")
    assert lines[0] == "• Люди/Кира.md", "первая строка — путь, префикс её не трогает"
    assert "[встреча" not in block and "[упомянуто" not in block, "метки даты во фрагменте нет"
    assert "новое решение" in lines[1] and "старое решение" not in lines[1], \
        "фрагмент целиком из блока-победителя, не окно через границу блоков"
    assert " → " not in lines[1], "хлебная крошка «Файл → H1 → H2» во фрагмент не попадает, как на `main`"


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


def test_the_last_seen_stamp_prints_no_label(tmp_path):
    """Штамп «последнее упоминание» даёт блоку дату для ранга, но метки нет."""
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
    assert "[встреча" not in block and "[упомянуто" not in block


def test_the_main_window_is_kept_when_the_winner_block_has_no_needles(tmp_path):
    """Победитель есть, но игл в его тексте нет — фрагмент как на `main`, без метки."""
    s = _kira_graph(tmp_path)
    d = s._gen.docs[str(s.graph / "Люди" / "Кира.md")]
    main = s._fragment(d, re.compile("несуществующая_игла"), 500, [])
    frag = s._block_fragment(d, 0, main, ["несуществующая_игла"],
                             re.compile("несуществующая_игла"), 500, [])
    assert frag == main


def _synonym_graph(tmp_path, first_block: str) -> gs.GraphSearch:
    """Узел из двух длинных блоков: «семантика» подделки выбирает блок с синонимом
    (`провайдер` = `поставщик`), а иглы запроса лежат в другом блоке (№780)."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Встречи-архив", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Системы", "Шлюз.md",
           "# Шлюз\n## Связи\n" + first_block + " " + "провайдер " * 60
           + "\n## Расчёты\nдоговор подписан, оплата прошла. " + "склад приёмка " * 60 + "\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    return s


def _spy_winner(s, monkeypatch) -> list:
    """Номера блоков, с которыми зван `_block_fragment`, — проверка, что фикстура
    действительно идёт веткой семантического победителя."""
    wins: list = []
    real = s._block_fragment
    monkeypatch.setattr(s, "_block_fragment",
                        lambda d, win, *a: wins.append(s._blocks(d)[win]) or real(d, win, *a))
    return wins


def test_a_semantic_winner_with_fewer_needles_gives_way_to_the_main_window(tmp_path, monkeypatch):
    """Победитель семантики (синоним и одна игла) слабее окна основного тела (две
    иглы): показано окно основного тела. До №780 показывался блок синонима, и игла
    окна пропадала из выдачи — минус три вопроса бенча answer после №633."""
    s = _synonym_graph(tmp_path, "договор")
    wins = _spy_winner(s, monkeypatch)
    r = s.search("поставщик договор оплата", limit=3)
    assert r.sem_used
    assert wins and "→ Связи" in wins[0], "победитель — блок синонима"
    frag = r.blocks[0].split("\n", 1)[1]
    assert "оплата" in frag and "договор" in frag


def test_a_semantic_winner_no_weaker_than_the_window_is_shown(tmp_path, monkeypatch):
    """Окно блока-победителя несёт не меньше игл, чем окно основного тела, — показан
    блок победителя (его дата ранжирует файл), а не окно основного тела."""
    s = _synonym_graph(tmp_path, "договор и оплата")
    wins = _spy_winner(s, monkeypatch)
    r = s.search("поставщик договор оплата", limit=3)
    assert r.sem_used and wins and "→ Связи" in wins[0]
    frag = r.blocks[0].split("\n", 1)[1]
    assert "провайдер" in frag and "склад" not in frag


def test_a_short_node_is_shown_whole_without_a_label(tmp_path):
    """Тело в пределах окна фрагмента показывается целиком, метки даты нет."""
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
    assert "[встреча" not in block and "[упомянуто" not in block
    assert "решение принято" in lines[1]


# ------------------------------------------- Р2: единица даты — строка списка
_FILL = "подробности обсуждения и сопутствующие обстоятельства дела " * 15


def _list_node_graph(tmp_path, long_lines: bool) -> gs.GraphSearch:
    """Узел человека с секцией `## Встречи`: строки разных дат в ОДНОМ блоке. Свежая
    встреча рядом — узел и она должны разойтись по дате строки, а не по максимуму блока."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    fill = _FILL if long_lines else ""
    _write(g / "Люди", "Иван.md",
           "# Иван\n## Встречи\n"
           f"- [[Встречи/2026-03-02_1000]] — Иван отвечает за выплаты. {fill}\n"
           f"- [[Встречи/2026-05-10_1000]] — Иван ведёт реестр лицензий. {fill}\n"
           f"- [[Встречи/2026-09-30_1000]] — Иван перешёл в проект Гамма. {fill}\n")
    _write(g / "Встречи", "2026-09-30_1000.md",
           "# Гамма\nУчастники: [[Люди/Иван]]\nИван перешёл в проект Гамма\n")
    _write(g / "Встречи", "2026-03-02_1000.md",
           "# Выплаты\nУчастники: [[Люди/Иван]]\nИван отвечает за выплаты\n")
    os.utime(g / "Люди" / "Иван.md", (NOW - 86400, NOW - 86400))
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    return s


def test_a_list_node_is_ranked_by_the_date_of_its_block(tmp_path):
    """Единица даты для ранга — блок: узел со списком встреч в одной секции получает
    максимум блока (09-30) при любой строке запроса (строчная дата — в долгах зоны)."""
    s = _list_node_graph(tmp_path, long_lines=False)
    d = s._gen.docs[str(s.graph / "Люди" / "Иван.md")]
    for q, want in (("кто отвечает за выплаты", "2026-09-30"), ("Иван перешёл в проект Гамма", "2026-09-30")):
        words, grams = gs.needles(q)
        rx = re.compile("|".join(re.escape(k) for k in words + grams))
        win, _ = s._lex_winner(d, words + grams, rx)
        assert s._block_ts(d, win) == gs._ts_from_iso(want), q


def test_a_short_list_node_shown_whole_has_no_label(tmp_path):
    """Узел из строк трёх дат показан целиком — метки нет."""
    s = _list_node_graph(tmp_path, long_lines=False)
    r = s.search("кто отвечает за выплаты", limit=4, semantic=False)
    block = _block_for(r, "Люди/Иван.md")
    assert "[встреча" not in block and "[упомянуто" not in block


def test_a_window_with_one_line_has_no_label(tmp_path):
    """Длинный узел: окно вокруг иглы показывает одну строку списка — метки нет."""
    s = _list_node_graph(tmp_path, long_lines=True)
    r = s.search("кто отвечает за выплаты", limit=4, semantic=False)
    line = _block_for(r, "Люди/Иван.md").split("\n")[1]
    assert not line.startswith("  ["), line[:120]
    assert "перешёл в проект Гамма" not in line


# ------------------------------------------------------------------- досье
def test_the_dossier_has_no_label_with_or_without_a_link_in_the_slice(tmp_path):
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
    assert r.dossiers and "[встреча" not in r.dossiers[0] and "[упомянуто" not in r.dossiers[0]
    (folder / "Тема.md").write_text(far, encoding="utf-8")
    s.refresh(force=True)
    r2 = s.search("термин", limit=2)
    assert r2.dossiers and "[встреча" not in r2.dossiers[0] and "[упомянуто" not in r2.dossiers[0]


# ------------------------------------------------------------------- метка у перехода
def test_a_hop_has_no_label_on_the_third_line(tmp_path):
    s = _kira_graph(tmp_path)
    r = s.search("Кира", limit=1)         # только узел в слотах — переход к встрече
    hop = next((b for b in r.blocks if "↳ по ссылке из" in b), None)
    assert hop is not None, "переход из узла Кира на встречу"
    lines = hop.split("\n")
    assert lines[0].startswith("• ") and lines[1].startswith("  ↳ по ссылке из ")
    assert not lines[2].startswith("  [")


# --------------------------------------------------- Е: чужая длина вектора
#: Эталон, снятый на базе 51497705 (код до №633) на том же графе и тех же запросах:
#: выдача и вердикт при векторе чужой нарезки. Не вывод кода под проверкой.
_BASE_FRAGMENT = ("# Поставщик ## Статус поставщик платежей подписан договор ## Заметки "
                  + " ".join(["наполнитель."] * 30))


def _foreign_slicing_graph(tmp_path) -> gs.GraphSearch:
    """Файл и три соседа; у файла в кэше один вектор вместо двух (чужая нарезка при
    том же mtime). Соседи нужны, чтобы доля проверенного (`sem_share`) была видна."""
    g = tmp_path / "Работа"
    for d in ("Системы", "Люди", "Встречи", "Ядра", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g / "Системы", "Поставщик.md",
           "# Поставщик\n## Статус\nпоставщик платежей подписан договор\n"
           + "## Заметки\n" + "наполнитель. " * 30 + "\n")
    for i in range(3):
        _write(g / "Системы", f"Склад{i}.md", f"# Склад{i}\n## Статус\nскладской учёт и приёмка {i}\n")
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    path = str(s.graph / "Системы" / "Поставщик.md")
    mt, vs = s._vecs[path]
    assert len(vs) == 2
    s._vecs[path] = (mt, vs[:1])          # чужая нарезка: длина ≠ _expected_blocks
    return s


@pytest.mark.parametrize("flag", [True, False])
@pytest.mark.parametrize("query", ["провайдер", "платежи подписан"])
def test_a_vector_of_another_slicing_stays_a_witness_as_on_main(tmp_path, monkeypatch, flag, query):
    """Вектор другой длины, чем у текущей нарезки, номера блока не даёт, но файл
    остаётся свидетелем семантики, как на базе: вердикт, `sem_share`, состав выдачи
    и фрагмент те же при любом значении флага; метки нет."""
    s = _foreign_slicing_graph(tmp_path)
    monkeypatch.setattr(gs, "BLOCK_DATES", flag)
    r = s.search(query, limit=3)
    assert r.status == gs.Verdict.CONFIDENT and r.sem_used and r.reason == ""
    assert r.blocks == [f"• Системы/Поставщик.md\n  {_BASE_FRAGMENT}"]


def _rels(result: gs.Result) -> list[str]:
    return [b.split("\n")[0][2:].strip() for b in result.blocks]


# --------------------------------------------------------------- Ж: флаг отката
def test_the_rollback_flag_leaves_the_output_as_main(tmp_path, monkeypatch):
    """Флаг выключен — ни метки, ни блока-победителя: механизм не зовётся вовсе."""
    s = _kira_graph(tmp_path)
    monkeypatch.setattr(gs, "BLOCK_DATES", False)
    touched = []
    for name in ("_block_fragment", "_lex_winner", "_doc_block_dates", "_expected_blocks"):
        real = getattr(s, name)
        monkeypatch.setattr(s, name, lambda *a, _n=name, _r=real, **kw: touched.append(_n) or _r(*a, **kw))
    for q in ("последнее решение по проекту Альфа", "Кира", "старое решение"):
        text = gs.render(s.search(q, limit=4), q)
        assert "[встреча" not in text and "[упомянуто" not in text
        assert s.search(q, limit=4).blocks == s.search(q, limit=4).blocks, "выдача детерминирована"
    assert touched == [], "при выключенном флаге механизм дат не трогается"


#: sha256 выдачи (блоки и вердикт трёх запросов `_kira_graph`, JSON с ensure_ascii=False),
#: снятой на базе 51497705 — коде до №633. Эталон снят на базе, а не выводом кода под проверкой.
_KIRA_BASE_SHA = "9f19e92fce16512c2672e63344fbbcc1aed8effa98b25afb74a4453be8c0099e"


def test_the_rollback_flag_gives_the_output_of_the_base_byte_for_byte(tmp_path, monkeypatch):
    import json
    s = _kira_graph(tmp_path)
    monkeypatch.setattr(gs, "BLOCK_DATES", False)
    out = {}
    for q in ("последнее решение по проекту Альфа", "Кира", "старое решение"):
        r = s.search(q, limit=4)
        out[q] = [r.blocks, str(r.status)]
    got = json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(got).hexdigest() == _KIRA_BASE_SHA, "выдача при выключенном флаге разошлась с базой"


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


def test_a_list_node_ranks_above_an_old_meeting_by_the_date_of_its_block(tmp_path):
    """Узел со списком встреч в одном блоке датируется самой поздней датой строк (09-30),
    поэтому стоит выше давней встречи 03-02 («давний факт не выше свежего» для такого узла
    не обещано, строчная дата — в долгах зоны). mtime узла старше встречи: порядок даёт
    только дата блока, при `BLOCK_DATES=False` тест падает. Дата блока утверждается и прямо:
    порядок держится на зазоре множителя свежести, его сдвинут веса."""
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / d).mkdir(parents=True, exist_ok=True)
    _write(g, "Люди/Иван.md",
           "# Иван\n## Встречи\n- [[Встречи/2026-03-02_1000]] — Иван отвечает за выплаты.\n"
           "- [[Встречи/2026-05-10_1000]] — Иван ведёт реестр лицензий.\n"
           "- [[Встречи/2026-09-30_1000]] — Иван перешёл в проект Гамма.\n")
    _write(g, "Встречи/2026-03-02_1000.md", "# Выплаты\nУчастники: [[Люди/Иван]]\nИван отвечает за выплаты\n")
    os.utime(g / "Люди/Иван.md", (NOW - 300 * 86400, NOW - 300 * 86400))
    s = _searcher(tmp_path)
    s.refresh(force=True)
    r = s.search("кто отвечает за выплаты", limit=4, semantic=False)
    rels = _rels(r)
    assert rels.index("Люди/Иван.md") < rels.index("Встречи/2026-03-02_1000.md")
    d = s._gen.docs[str(g / "Люди" / "Иван.md")]
    words, grams = gs.needles("кто отвечает за выплаты")
    win, _ = s._lex_winner(d, words + grams, re.compile("|".join(re.escape(k) for k in words + grams)))
    assert s._block_ts(d, win) == gs._ts_from_iso("2026-09-30")


def test_a_short_section_joins_the_block_before_it_and_dates_it(tmp_path):
    """Секция короче 40 знаков входит в блок предыдущей и датирует его своей датой: давнее
    решение (03-02) получает дату свежей встречи (09-30). Ограничение нарезки закреплено
    как есть (не сливать датированную секцию — долг зоны); с длинной секцией блоков два."""
    g = tmp_path / "Работа"
    for sub in ("Люди", "Системы", "Ядра", "Встречи", "Документация"):
        (g / sub).mkdir(parents=True, exist_ok=True)
    old = "# Кира\n## Решения\n- [[Встречи/2026-03-02_1000]] — проект Альфа: старое решение по срокам.\n"
    q = "старое решение по срокам"
    for news, want in (("## Новости\n- [[Встречи/2026-09-30_1000]]\n", ["2026-09-30"]),
                       ("## Новости\n- [[Встречи/2026-09-30_1000]] — проект Гамма: Кира ведёт поставки "
                        "и отвечает за реестр лицензий.\n", ["2026-03-02", "2026-09-30"])):
        _write(g, "Люди/Кира.md", old + news)
        os.utime(g / "Люди/Кира.md", (NOW - 86400, NOW - 86400 * len(want)))
        s = _searcher(tmp_path)
        s.refresh(force=True)
        d = s._gen.docs[str(g / "Люди" / "Кира.md")]
        assert [gs._ts_from_iso(w) for w in want] == [b.ts for b in s._doc_block_dates(d)], want
        words, grams = gs.needles(q)
        win, _ = s._lex_winner(d, words + grams, re.compile("|".join(re.escape(k) for k in words + grams)))
        assert s._block_ts(d, win) == gs._ts_from_iso(want[0]), want
