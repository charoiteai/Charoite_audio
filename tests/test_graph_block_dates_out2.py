"""Дата блока в поиске (№633), выходной круг out2: будущая дата в имени файла,
текст без дат без метки, победитель ничьей по совпавшей строке, потолок кандидатов."""
from __future__ import annotations

import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_graph_fragment_dates import NOW, _block_for, _rels, _searcher, _write, gs  # noqa: E402

_FILL = "подробности обсуждения и сопутствующие обстоятельства дела " * 15


def _mk(tmp_path, files, mtimes=None):
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Документация", "Заметки"):
        (g / d).mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        _write(g, rel, text)
    for rel, t in (mtimes or {}).items():
        os.utime(g / rel, (t, t))
    s = _searcher(tmp_path)
    s.refresh(force=True)
    s.embed_pending()
    return s


def test_a_file_name_date_prints_no_label_past_or_future(tmp_path):
    """Метки даты во фрагменте нет ни у прошедшего, ни у будущего имени файла (круги 1–3
    по №633: метка трижды врала про показанный текст, из PR снята)."""
    s = _mk(tmp_path, {
        "Встречи/2026-12-15_1000.md": "# Планирование\nУчастники: [[Люди/Иван]]\nпроект Омега запуск, планирование бюджета\n",
        "Встречи/2026-09-01_1000.md": "# Планирование\nУчастники: [[Люди/Иван]]\nпроект Омега запуск, планирование бюджета\n"})
    r = s.search("планирование проект Омега запуск", limit=4, semantic=False)
    assert "[встреча" not in _block_for(r, "Встречи/2026-12-15_1000.md")
    assert "[встреча" not in _block_for(r, "Встречи/2026-09-01_1000.md")


def test_a_window_without_any_date_on_a_mixed_block_has_no_label(tmp_path):
    """Окно упало на строку без ссылки в блоке со строками разных дат — метки нет."""
    node = ("# Иван\n## Встречи\n"
            f"- [[Встречи/2026-03-02_1000]] — Иван отвечает за выплаты. {_FILL} по договору. {_FILL} аренды помещения. {_FILL}\n"
            f"- [[Встречи/2026-09-30_1000]] — Иван перешёл в проект Гамма. {_FILL}\n"
            f"- Иван ведёт переговоры с Сигмой о лицензиях. {_FILL}\n")
    s = _mk(tmp_path, {"Люди/Иван.md": node}, {"Люди/Иван.md": NOW - 86400})
    r = s.search("выплаты договору аренды переговоры Сигмой", limit=3, semantic=False)
    line = _block_for(r, "Люди/Иван.md").split("\n")[1]
    assert "переговоры с Сигмой" in line and "[[" not in line, line[:200]
    assert "[встреча" not in line and "[упомянуто" not in line, line[:60]


def test_a_tie_between_blocks_is_won_by_the_fresher_block(tmp_path):
    """Равное покрытие, блок A датирован мартом, блок B — сентябрём: побеждает B, узел
    датируется 09-30 и идёт перед встречей 08-15."""
    fill = "подробности обсуждения и сопутствующие обстоятельства дела " * 6
    node = ("# Иван\n## Итоги\n"
            f"- [[Встречи/2026-03-02_1000]] — Иван отвечает за выплаты. {fill}\n"
            f"## Проект\n- [[Встречи/2026-09-30_1000]] — Иван отвечает за выплаты. {fill}\n")
    s = _mk(tmp_path, {"Люди/Иван.md": node,
                       "Встречи/2026-08-15_1000.md": "# Выплаты\nУчастники: [[Люди/Иван]]\nИван отвечает за выплаты\n"},
            {"Люди/Иван.md": NOW - 86400})
    d = s._gen.docs[str(s.graph / "Люди" / "Иван.md")]
    words, grams = gs.needles("кто отвечает за выплаты")
    keys = words + grams
    rx = re.compile("|".join(re.escape(k) for k in keys))
    win, _ = s._lex_winner(d, keys, rx)
    assert s._block_ts(d, win) == gs._ts_from_iso("2026-09-30")
    rels = _rels(s.search("кто отвечает за выплаты", limit=4, semantic=False))
    assert rels.index("Люди/Иван.md") < rels.index("Встречи/2026-08-15_1000.md")


def _stale_fact_graph(tmp_path):
    """Узел переписан только что (mtime = сегодня), но совпавшая строка — март;
    встреча октября с тем же фактом. По дате блока встреча первая, по mtime — узел."""
    fill = "подробности обсуждения и сопутствующие обстоятельства дела " * 6
    node = ("# Иван\n## Итоги\n"
            f"- [[Встречи/2026-03-02_1000]] — Иван отвечает за выплаты. {fill}\n")
    return _mk(tmp_path, {"Люди/Иван.md": node,
                          "Встречи/2026-10-05_1000.md": "# Выплаты\nУчастники: [[Люди/Иван]]\nИван отвечает за выплаты\n"},
               {"Люди/Иван.md": NOW})


def test_candidate_cap_leaves_files_beyond_it_on_the_file_date(tmp_path, monkeypatch):
    """Р6: за потолком K дата блока не считается — дата файла, как на main; в потолке — дата блока."""
    s = _stale_fact_graph(tmp_path)
    q = "кто отвечает за выплаты"
    with_blocks = s.search(q, limit=4, semantic=False)
    monkeypatch.setattr(gs, "BLOCK_DATE_CANDIDATES", 0)
    capped = s.search(q, limit=4, semantic=False)
    monkeypatch.setattr(gs, "BLOCK_DATES", False)
    base = s.search(q, limit=4, semantic=False)
    assert _rels(capped) == _rels(base)                       # за потолком — порядок как на main
    dated: list[str] = []
    real = s._block_ts
    monkeypatch.setattr(s, "_block_ts", lambda d, *a, **kw: dated.append(d.rel) or real(d, *a, **kw))
    monkeypatch.setattr(gs, "BLOCK_DATES", True)
    s.search(q, limit=4, semantic=False)
    assert dated == []                                        # потолок 0: дат блоков для оценки нет
    monkeypatch.setattr(gs, "BLOCK_DATE_CANDIDATES", 1)
    s.search(q, limit=4, semantic=False)
    assert len(dated) == 1                                    # потолок 1: дата блока — у одного файла
    assert "[встреча" not in _block_for(with_blocks, "Люди/Иван.md")          # метки нет и в потолке


def test_candidate_cap_does_not_depend_on_limit_or_walk_order(tmp_path, monkeypatch):
    """Р6 + №631: набор кандидатов — K лучших по баллу до свежести, ничья по пути;
    ни `limit`, ни порядок попаданий на него не влияют."""
    monkeypatch.setattr(gs, "BLOCK_DATE_CANDIDATES", 2)
    docs = [(1.0, type("D", (), {"rel": r})()) for r in ("в", "а", "б", "г")]
    assert gs.GraphSearch._dated_candidates(docs) == {"а", "б"}
    assert gs.GraphSearch._dated_candidates(docs[::-1]) == {"а", "б"}
    s = _stale_fact_graph(tmp_path)
    q = "кто отвечает за выплаты"
    assert _rels(s.search(q, limit=1, semantic=False))[:1] == _rels(s.search(q, limit=10, semantic=False))[:1]


def test_a_limit_does_not_change_the_order_with_a_cap_below_the_hit_count(tmp_path, monkeypatch):
    """Р6 + №631 (сверка out3 № 9): граф из шести файлов, K = 3 при шести попаданиях —
    порядок первых `n` не зависит от `limit` (в том числе когда limit < K). Мутация
    `dated = frozenset(sorted(dated)[:limit])` сдвигает K с `limit` и этот тест краснит."""
    monkeypatch.setattr(gs, "BLOCK_DATE_CANDIDATES", 3)
    fill = "подробности обсуждения и сопутствующие обстоятельства дела " * 6
    files, mtimes = {}, {}
    # K = три файла с лучшим баллом (Аня, Борис, Вера: слово чаще) — у них блок старый (март),
    # у остальных трёх даты нет и ранг по свежему mtime; Борис свежее всех по mtime
    for i, name in enumerate(("Аня", "Борис", "Вера")):
        files[f"Люди/{name}.md"] = f"# {name}\n## Итоги\n- [[Встречи/2026-03-0{i + 1}_1000]] — {name} отвечает за выплаты, выплаты, выплаты. {fill}\n"
    for name in ("Глеб", "Дина", "Егор"):
        files[f"Люди/{name}.md"] = f"# {name}\n## Итоги\n- {name} отвечает за выплаты. {fill}\n"
    for rel, off in (("Борис", 0), ("Глеб", 100), ("Дина", 200), ("Егор", 300), ("Аня", 400), ("Вера", 500)):
        mtimes[f"Люди/{rel}.md"] = NOW - off
    s = _mk(tmp_path, files, mtimes)
    q = "кто отвечает за выплаты"
    full = _rels(s.search(q, limit=10, semantic=False))
    assert len(full) == 6
    for n in (1, 2, 4):
        assert _rels(s.search(q, limit=n, semantic=False)) == full[:n], n


def test_a_future_file_name_date_ranks_as_on_main(tmp_path):
    """Дата в имени файла датирует файл, даже будущая: ранг как на `main` (множитель
    свежести 1,0, файл 2026-12-15 не ниже 2026-10-05) — закрепляет поведение, не обещание."""
    text = "# Планирование\nУчастники: [[Люди/Иван]]\nпроект Омега запуск, планирование бюджета\n"
    day = NOW - 86400
    s = _mk(tmp_path, {"Встречи/2026-12-15_1000.md": text, "Встречи/2026-10-05_1000.md": text},
            {"Встречи/2026-12-15_1000.md": day, "Встречи/2026-10-05_1000.md": day})
    assert gs.recency_factor(gs._ts_from_iso("2026-12-15"), NOW) == 1.0
    for sem in (False, True):
        r = s.search("планирование проект Омега запуск", limit=4, semantic=sem)
        rels = _rels(r)
        assert rels.index("Встречи/2026-12-15_1000.md") < rels.index("Встречи/2026-10-05_1000.md"), sem
