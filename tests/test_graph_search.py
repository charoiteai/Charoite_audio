"""Память по графу без сервера (№250): индекс по mtime, ранжирование, семантика
с кэшем векторов, переход по ссылкам, досье, гейт честности, формат выдачи.

Модуль без демона и сети: эмбеддинги подменяются детерминированной
функцией, граф — tmp. Три вопроса демо-бенча репозитория проверяются как
retrieval — факт обязан быть в найденном тексте без участия модели.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import dossier  # noqa: E402
import graph_search as gs  # noqa: E402


_SYNONYMS = {"поставщик": "провайдер", "поставщика": "провайдер"}   # «семантика» подделки: синоним — то же слово


def fake_embed(texts: list[str], timeout: float) -> list[list[float]]:
    """Мешок слов → 64 измерения по хешу слова; одинаковые слова и синонимы из
    таблицы — близкие векторы. Ровно то, чего лексика не умеет."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in (_SYNONYMS.get(x, x) for x in re.findall(r"\w+", gs.norm(t))):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    g = tmp_path / "Работа"
    for d in ("Люди", "Системы", "Ядра", "Встречи", "Встречи-архив", "Документация/Стенограммы встреч",
              "Документация", ".obsidian"):
        (g / d).mkdir(parents=True, exist_ok=True)
    (g / "Люди" / "Иван Мироненко.md").write_text(
        "# Иван Мироненко\nВедёт интеграцию платёжного шлюза.\n\n## Встречи\n"
        "- [[Встречи/2026-08-01_1000]] — взял интеграцию на себя\n"
        "- [[Встречи/2026-07-30_1400]] — обещал согласовать доступ\n", encoding="utf-8")
    (g / "Люди" / "Собеседник 3.md").write_text(
        "# Собеседник 3\nплатёжный шлюз платёжный шлюз платёжный шлюз\n\n## Встречи\n"
        + "".join(f"- [[Встречи/2026-06-{i:02d}_1000]] — платёжный шлюз\n" for i in range(1, 25)), encoding="utf-8")
    (g / "Системы" / "Платёжный шлюз.md").write_text(
        "# Платёжный шлюз\nСтатус: пилот до сентября, провайдер ЮPay.\n\n## Встречи\n"
        "- [[Встречи/2026-08-02_1500]]\n", encoding="utf-8")
    (g / "Встречи" / "2026-08-01_1000.md").write_text(
        "# Интеграция\nУчастники: [[Люди/Иван Мироненко]]\nРешили: интеграцию платёжного шлюза ведёт Иван, "
        "срок — 15 августа, токен через шлюз авторизации.\n", encoding="utf-8")
    (g / "Встречи" / "2026-07-30_1400.md").write_text(
        "# Доступы\nУчастники: [[Люди/Иван Мироненко]]\nИван обещал согласовать доступ к API до пятницы.\n",
        encoding="utf-8")
    (g / "Встречи" / "2026-08-01_1000_стенограмма.md").write_text(
        "стенограмма: интеграцию платёжного шлюза ведёт Иван, срок 15 августа, повторяем срок 15 августа\n",
        encoding="utf-8")
    (g / "Встречи" / "2026-08-01_1000_hints.md").write_text(
        "подсказки: интеграция платёжного шлюза, Иван, срок 15 августа\n", encoding="utf-8")
    (g / "Встречи-архив" / "2026-01-01_старое.md").write_text("платёжный шлюз архив АРХИВНЫЙ_МАРКЕР\n", encoding="utf-8")
    (g / "Документация" / "Стенограммы встреч" / "копия.md").write_text("платёжный шлюз КОПИЯ_МАРКЕР\n", encoding="utf-8")
    (g / "Документация" / "Концепция шлюза.md").write_text("# Концепция\nплатёжный шлюз: ДОКУМЕНТ_МАРКЕР\n", encoding="utf-8")
    (g / ".obsidian" / "workspace.md").write_text("платёжный шлюз СКРЫТЫЙ_МАРКЕР\n", encoding="utf-8")
    (g / "_MOC.md").write_text("# MOC\nплатёжный шлюз [[Системы/Платёжный шлюз]]\n", encoding="utf-8")
    (g / "Служебное_ревизия.md").write_text("платёжный шлюз СЛУЖЕБНЫЙ_МАРКЕР\n", encoding="utf-8")
    return g


def _search(tmp_path, **kw) -> gs.GraphSearch:
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed, **kw)
    s.refresh(force=True)
    return s


def _rels(result: gs.Result) -> list[str]:
    return [b.split("\n")[0][2:].strip() for b in result.blocks]


def test_needles_stems_stop_words_cjk_and_fullwidth():
    words, grams = gs.needles("Что решили по платёжному шлюзу и ＹｕＰａｙ 支付服务商?")
    assert "платежн" in words and "шлюз" in words and "yupay" in words
    assert "что" not in words and "решили" not in words
    assert grams == ["支付", "付服", "服务", "务商"]


def test_index_skips_archive_transcript_copies_hidden_and_service_files(tmp_path):
    s = _search(tmp_path)
    rels = {d.rel for d in s._docs.values()}
    assert "Встречи-архив/2026-01-01_старое.md" not in rels
    assert "Документация/Стенограммы встреч/копия.md" not in rels
    assert "Документация/Концепция шлюза.md" in rels, "сами документы остаются, исключены только копии стенограмм"
    assert not any(r.startswith(".obsidian") for r in rels)
    assert "_MOC.md" not in rels and "Служебное_ревизия.md" not in rels
    text = gs.render(s.search("платёжный шлюз", limit=20, semantic=False), "платёжный шлюз")
    for marker in ("АРХИВНЫЙ_МАРКЕР", "КОПИЯ_МАРКЕР", "СКРЫТЫЙ_МАРКЕР", "СЛУЖЕБНЫЙ_МАРКЕР"):
        assert marker not in text


def test_refresh_follows_mtime_and_removals(tmp_path):
    clock = {"t": 1_000_000.0}
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed, now=lambda: clock["t"])
    assert not s.ready and not s.search("шлюз").ready
    s.refresh(force=True)
    assert s.ready and s.size == 8
    new = s.graph / "Системы" / "Новая.md"
    new.write_text("# Новая\nуникальный_терм_нового_узла\n", encoding="utf-8")
    assert s.refresh() is False, "свежий индекс без force не обходится"
    clock["t"] += gs.REFRESH_S + 1
    assert s.refresh() is True and any(d.rel == "Системы/Новая.md" for d in s._docs.values())
    node = s.graph / "Системы" / "Платёжный шлюз.md"
    node.write_text(node.read_text(encoding="utf-8") + "\nдописанный_терм\n", encoding="utf-8")
    os.utime(node, (clock["t"] + 5, clock["t"] + 5))
    new.unlink()
    s.refresh(force=True)
    assert _rels(s.search("дописанный_терм", semantic=False)) == ["Системы/Платёжный шлюз.md"]
    assert not any(d.rel == "Системы/Новая.md" for d in s._docs.values())


def test_ranking_prefers_path_coverage_recency_and_damps_hubs_raw_and_placeholders(tmp_path):
    s = _search(tmp_path)
    r = s.search("платёжный шлюз", limit=6, semantic=False)
    rels = _rels(r)
    assert rels[0] == "Системы/Платёжный шлюз.md", "слово в имени файла — сильнейший сигнал"
    assert "Люди/Собеседник 3.md" not in rels[:3], "метка диаризации с 24 упоминаниями — не хаб"
    r2 = _rels(s.search("срок интеграции 15 августа", limit=6, semantic=False))
    assert r2.index("Встречи/2026-08-01_1000.md") < r2.index("Встречи/2026-08-01_1000_стенограмма.md"), \
        "заметка встречи выше сырой стенограммы при равной релевантности"
    assert not r.low_conf
    # нормализация длины: те же слова один раз в узле на 8 000 знаков чужого текста —
    # ниже заметки, где о них и шла речь (без неё узел владельца на 280 КБ, где
    # встречается любое слово, всплывал по любому запросу — замер на рабочем графе)
    (s.graph / "Люди" / "Болтун.md").write_text("# Болтун\nинтеграция срок 15 августа.\n" + "слово " * 1600 + "\n", encoding="utf-8")
    s.refresh(force=True)
    r3 = _rels(s.search("срок интеграции 15 августа", limit=6, semantic=False))
    assert r3.index("Встречи/2026-08-01_1000.md") < r3.index("Люди/Болтун.md"), r3
    # свежесть: два файла с одинаковым текстом — новее выше
    (s.graph / "Встречи" / "2025-01-01_1000.md").write_text("# Старое\nуникальный_факт_ретро\n", encoding="utf-8")
    (s.graph / "Встречи" / "2026-08-05_1000.md").write_text("# Новое\nуникальный_факт_ретро\n", encoding="utf-8")
    s.refresh(force=True)
    assert _rels(s.search("уникальный_факт_ретро", semantic=False))[0] == "Встречи/2026-08-05_1000.md"
    assert gs.hub_factor(10_000) == gs.HUB_CAP and gs.placeholder_factor("собеседник 3") == 0.2
    assert gs.bm25_lite(300, 0, 1.0, len_norm=13.0) < gs.bm25_lite(300, 0, 1.0, len_norm=1.0)


def test_one_meeting_does_not_eat_all_slots_and_hop_follows_links_from_a_node(tmp_path):
    s = _search(tmp_path)
    rels = _rels(s.search("интеграция шлюза срок", limit=3, semantic=False))
    assert len({gs.meeting_key(r) for r in rels}) >= 2, f"три грани одной встречи заняли все слоты: {rels}"
    # «что решил Иван» — запрос из одного имени: узел находится, а решение живёт в
    # заметке встречи по ссылке из него — самой свежей, раз других слов в запросе нет
    r = s.search("что решил Иван Мироненко", limit=1, semantic=False)
    assert _rels(r) == ["Люди/Иван Мироненко.md", "Встречи/2026-08-01_1000.md"], r.blocks
    assert "↳ по ссылке из Люди/Иван Мироненко.md" in r.blocks[1] and "15 августа" in r.blocks[1]
    assert r.total >= len(r.blocks)


def test_honesty_gate_and_render_markers(tmp_path):
    s = _search(tmp_path)
    off = s.search("рецепт борща со сметаной для шлюза", limit=3, semantic=False)   # одно слово из графа
    assert off.low_conf and off.blocks
    assert gs.render(off, "рецепт борща").startswith("⚠ Похоже, в архиве об этом почти ничего нет")
    none = s.search("qqqzzz", semantic=False)
    assert none.empty and gs.render(none, "qqqzzz") == "Ничего не найдено по «qqqzzz» в графе"
    good = s.search("платёжный шлюз", limit=2, semantic=False)
    text = gs.render(good, "платёжный шлюз")
    assert text.startswith("Найдено в графе (") and "• Системы/Платёжный шлюз.md\n  " in text
    assert gs.render(gs.Result([], 0, False, ready=False), "x") == ""


def test_dossier_comes_first(tmp_path):
    s = _search(tmp_path)
    folder = s.graph / dossier.DOSSIER_DIR
    folder.mkdir()
    (folder / "Платёжный шлюз.md").write_text("---\ntype: досье\n---\n# Платёжный шлюз\n## Состояние\nпилот до сентября, провайдер ЮPay\n",
                                              encoding="utf-8")
    dossier.write_index(folder, [{"тема": "Платёжный шлюз", "ключи": ["платежн", "шлюз", "провайдер"],
                                  "источников": 3, "собрано": "2026-08-02"}])
    r = s.search("что с платёжным шлюзом", limit=2, semantic=False)
    assert r.dossiers and r.dossiers[0].startswith("📁 Досье «Платёжный шлюз»") and "ЮPay" in r.dossiers[0]
    text = gs.render(r, "что с платёжным шлюзом")
    assert text.index("📁 Досье") < text.index("Найдено в графе")


def test_semantic_layer_uses_cached_vectors_and_survives_without_embeddings(tmp_path):
    s = _search(tmp_path)
    assert s.pending_vectors() and s.embed_pending(budget_s=0) == 0, "нулевой бюджет — ни одного вызова"
    n = s.embed_pending()
    assert n == s.size and not s.pending_vectors()
    manifest = json.loads(s._vec_manifest.read_text(encoding="utf-8"))
    blocks = sum(nn for _, _, nn in manifest["files"])
    assert manifest["dim"] == 64 and len(manifest["files"]) == n and s._vec_file.stat().st_size == blocks * 64 * 4
    assert blocks > n, "у узла с секциями — несколько блоков, не один вектор на файл"
    # новый экземпляр читает кэш с диска; изменившийся файл снова ждёт вектора
    s2 = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed)
    s2.refresh(force=True)
    assert s2.load_vectors() == n and not s2.pending_vectors()
    node = s.graph / "Системы" / "Платёжный шлюз.md"
    node.write_text(node.read_text(encoding="utf-8") + "\nещё строка\n", encoding="utf-8")
    os.utime(node, (node.stat().st_mtime + 10,) * 2)
    s2.refresh(force=True)
    assert [pathlib.Path(p).name for p in s2.pending_vectors()] == ["Платёжный шлюз.md"]
    # файл, которого лексика не видит (в нём синоним, а не слово запроса), поднимает семантика
    (s.graph / "Системы" / "Поставщик.md").write_text("# Поставщик\nпоставщик платежей: договор подписан\n", encoding="utf-8")
    s2.refresh(force=True)
    s2.embed_pending()
    assert "Системы/Поставщик.md" not in _rels(s2.search("провайдер", limit=4, semantic=False))
    assert "Системы/Поставщик.md" in _rels(s2.search("провайдер", limit=4)), "синоним — только через вектор"
    # сервер эмбеддингов не ответил — чистая лексика, без падения
    s3 = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=lambda t, to: [])
    s3.refresh(force=True)
    assert s3.embed_pending() == 0
    assert _rels(s3.search("платёжный шлюз", limit=2))[0] == "Системы/Платёжный шлюз.md"


def test_chunks_follow_headings_with_breadcrumbs_and_a_cap():
    """Блоки по заголовкам с крошкой «Файл → H1 → H2»; длинная секция — по
    абзацам; сплошной текст — по длине; не больше MAX_CHUNKS на файл; решение
    в конце длинной стенограммы попадает в свой блок, а не за границу шапки."""
    text = ("---\ntype: встреча\n---\n# Интеграция\nвступление " * 1 + "x" * 60 +
            "\n\n## Обсуждение\n" + "\n\n".join(f"абзац {i} " + "слово " * 200 for i in range(6)) +
            "\n\n## Решения\nРешили: провайдер ЮPay, срок 1 сентября.\n" +
            "\n### Детали\n" + "б" * 9000 + "\n")
    parts = gs.chunks("2026-08-01_1000", text, chars=3000, limit=50)
    assert parts[0].startswith("2026-08-01_1000 → Интеграция\n") and "вступление" in parts[0]
    assert any(p.startswith("2026-08-01_1000 → Интеграция → Обсуждение\n") for p in parts)
    assert any(p.startswith("2026-08-01_1000 → Интеграция → Решения\n") and "ЮPay" in p for p in parts)
    assert any(p.startswith("2026-08-01_1000 → Интеграция → Решения → Детали\n") for p in parts)
    assert all(len(p) <= 3000 + 80 for p in parts), [len(p) for p in parts]
    assert len(gs.chunks("x", "# T\n" + "\n\n".join("абзац " + "слово " * 500 for _ in range(40)), chars=2000, limit=5)) == 5
    assert gs.chunks("x", "---\na: 1\n---\n# T\nкоротко\n") == []


def test_shared_index_is_one_per_graph(tmp_path, monkeypatch):
    g = _graph(tmp_path)
    monkeypatch.setattr(gs, "_shared", {})
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    a = gs.shared({"sufler": {"graph_dir": str(g)}}, graph_dir=g)
    b = gs.shared({}, graph_dir=g)
    assert a is b
    assert gs.shared({"sufler": {}}, graph_dir=None) is None, "граф не настроен — индекса нет"


@pytest.mark.parametrize("graph, bench", [("demo/graph", "config/memory_bench_demo.yaml"),
                                          ("demo/graph_en", "config/memory_bench_demo_en.yaml"),
                                          ("demo/graph_zh", "config/memory_bench_demo_zh.yaml")])
def test_demo_bench_facts_are_retrieved_without_a_model(tmp_path, graph, bench):
    """Три вопроса демо-бенча: обязательный факт — в найденном тексте (retrieval),
    синтез моделью здесь не при чём. Тот же контур, что у подсказок на встрече."""
    s = gs.GraphSearch(REPO / graph, {}, data_dir=tmp_path / "data", embed=lambda t, to: [])
    s.refresh(force=True)
    cases = yaml.safe_load((REPO / bench).read_text(encoding="utf-8"))
    for case in cases:
        text = gs.render(s.search(case["q"], limit=4, snippet_chars=400), case["q"])
        low = gs.norm(text).replace(" ", "")
        for must in case["must"]:
            assert gs.norm(must).replace(" ", "") in low, f"{graph}: «{case['q']}» → нет «{must}»:\n{text[:600]}"


def test_brain_facade_raises_until_warm_and_then_renders(tmp_path, monkeypatch):
    """Фасад демона: непрогретый индекс — исключение (у контуров своя деградация),
    после прогрева — текст в формате прежнего сервера памяти."""
    import brain
    g = _graph(tmp_path)
    monkeypatch.setattr(gs, "_shared", {})
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    cfg = {"sufler": {"graph_dir": str(g)}}
    with pytest.raises(RuntimeError):
        brain.vault_search(cfg, "платёжный шлюз", limit=2, snippet_chars=200, timeout=2.5)
    with pytest.raises(RuntimeError):
        brain.vault_search({"sufler": {}}, "платёжный шлюз", limit=2, snippet_chars=200, timeout=2.5)
    mem = brain.warm(cfg)
    assert mem is not None and mem.ready and brain.warm({"sufler": {}}) is None
    text = brain.vault_search(cfg, "платёжный шлюз", limit=2, snippet_chars=200, timeout=2.5)
    assert text.startswith("Найдено в графе (") and "Системы/Платёжный шлюз.md" in text
    assert "не найдено" in brain.vault_search(cfg, "qqqzzz", limit=2, snippet_chars=200, timeout=2.5).lower()
