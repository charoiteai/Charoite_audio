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
import time

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import dossier  # noqa: E402
import graph_search as gs  # noqa: E402


_SYNONYMS = {"поставщик": "провайдер", "поставщика": "провайдер", "gateway": "шлюз"}   # «семантика» подделки: синоним — то же слово


def fake_embed(texts: list[str], timeout: float) -> list[list[float]]:
    """Мешок слов → 512 измерений по хешу слова (коллизии редки); одинаковые слова
    и синонимы из таблицы — близкие векторы. Ровно то, чего лексика не умеет."""
    out = []
    for t in texts:
        v = [0.0] * 512
        for w in (_SYNONYMS.get(x, x) for x in re.findall(r"\w+", gs.norm(t))):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 512] += 1.0
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
    """Индекс с векторами блоков: без семантики гейт всегда «⚠» (замер 17.09), так
    что уверенная выдача, переходы и досье проверяются с подделкой эмбеддинга."""
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed, **kw)
    s.refresh(force=True)
    s.embed_pending()
    return s


def _rels(result: gs.Result) -> list[str]:
    return [b.split("\n")[0][2:].strip() for b in result.blocks]


def test_needles_stems_stop_words_cjk_and_fullwidth():
    words, grams = gs.needles("Что решили по платёжному шлюзу и ＹｕＰａｙ 支付服务商?")
    assert "платежн" in words and "шлюз" in words and "yupay" in words
    assert "что" not in words and "решили" not in words
    assert grams == ["支付", "付服", "服务", "务商"]
    # частотный шум — одним ситом с досье, двухбуквенные служебные — тоже; отрицание
    # иглой не делаем: подстрока «не» есть почти в каждом файле (круг 3 по #577, DS M1/M2, GLM M2)
    assert gs.needles("что уже там сделали, ок, та тема")[0] == ["сдела", "тема"]
    assert gs.needles("почему не согласовали доступ")[0] == ["почем", "согласова", "доступ"]


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
    r = s.search("платёжный шлюз", limit=6)
    rels = _rels(r)
    assert rels[0] == "Системы/Платёжный шлюз.md", "слово в имени файла — сильнейший сигнал"
    # в графе из девяти файлов метка всё равно попадает в выдачу — но не выше настоящих
    # узла и документа: демпфер ×0,2 действует и на лексику, и на семантику
    assert "Люди/Собеседник 3.md" not in rels[:2], "метка диаризации с 24 упоминаниями — не хаб"
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
    # демпфер метки действует и на семантику: запрос-синоним («gateway» лексика не
    # видит) — файл-метка с сотней повторов слова, семантически ближайший, не выше
    # настоящего узла системы
    (s.graph / "Люди" / "Собеседник 7.md").write_text("# Собеседник 7\n" + "шлюз " * 300 + "\n", encoding="utf-8")
    s.refresh(force=True)
    s.embed_pending()
    sem_only = s.search("gateway", limit=4)
    top = _rels(sem_only)
    assert sem_only.sem_used and "Люди/Собеседник 7.md" in top
    assert top.index("Системы/Платёжный шлюз.md") < top.index("Люди/Собеседник 7.md"), top
    assert gs.bm25_lite(300, 0, 1.0, len_norm=13.0) < gs.bm25_lite(300, 0, 1.0, len_norm=1.0)


def test_one_meeting_does_not_eat_all_slots_and_hop_follows_links_from_a_node(tmp_path):
    s = _search(tmp_path)
    rels = _rels(s.search("интеграция шлюза срок", limit=3, semantic=False))
    assert len({gs.meeting_key(r) for r in rels}) >= 2, f"три грани одной встречи заняли все слоты: {rels}"
    # «что решил Иван» — запрос из одного имени: узел находится, а решение живёт в
    # заметке встречи по ссылке из него — самой свежей, раз других слов в запросе нет
    r = s.search("что решил Иван Мироненко", limit=1)
    assert _rels(r) == ["Люди/Иван Мироненко.md", "Встречи/2026-08-01_1000.md"], r.blocks
    assert "↳ по ссылке из Люди/Иван Мироненко.md" in r.blocks[1] and "15 августа" in r.blocks[1]
    assert r.total >= len(r.blocks)


def test_honesty_gate_and_render_markers(tmp_path):
    s = _search(tmp_path)
    off = s.search("рецепт борща со сметаной для шлюза", limit=3)   # одно слово из графа, семантика мимо
    assert off.sem_used and off.low_conf and off.blocks
    assert gs.render(off, "рецепт борща").startswith("⚠ Похоже, в архиве об этом почти ничего нет")
    none = s.search("qqqzzz")
    assert none.empty and none.status is gs.Verdict.EMPTY
    assert gs.render(none, "qqqzzz") == "Ничего не найдено по «qqqzzz» в графе" == none.text
    # пусто без семантики — не доказанное отсутствие: «⚠» и своя причина (GLM C1 r3)
    blind = s.search("qqqzzz", semantic=False)
    assert blind.empty and blind.status is gs.Verdict.UNVERIFIED and blind.text.startswith("⚠ По словам ничего не нашлось")
    assert "не считать доказанным" in blind.text and "не найдено" not in blind.text.lower()
    good = s.search("платёжный шлюз", limit=2)
    text = gs.render(good, "платёжный шлюз")
    assert text.startswith("Найдено в графе (") and "• Системы/Платёжный шлюз.md\n  " in text
    # без семантики уверенности нет: та же выдача, но с «⚠» и своей причиной
    lex = s.search("платёжный шлюз", limit=2, semantic=False)
    assert lex.blocks and lex.low_conf and not lex.sem_used and "не проверены" in lex.why_low
    assert lex.status is gs.Verdict.UNVERIFIED and off.status is gs.Verdict.WEAK and good.status is gs.Verdict.CONFIDENT
    assert gs.render(lex, "платёжный шлюз").startswith("⚠ Совпадения не проверены семантикой")
    assert gs.render(gs.Result([], 0, ready=False), "x") == ""
    # модели — фрагменты без шапки и маркеров: «⚠» в промпте читается как отказ (DS I3 r3)
    assert "⚠" not in lex.fragments and "Найдено" not in lex.fragments and "• Системы/Платёжный шлюз.md" in lex.fragments


def test_dossier_comes_first(tmp_path):
    s = _search(tmp_path)
    folder = s.graph / dossier.DOSSIER_DIR
    folder.mkdir()
    (folder / "Платёжный шлюз.md").write_text("---\ntype: досье\n---\n# Платёжный шлюз\n## Состояние\nпилот до сентября, провайдер ЮPay\n",
                                              encoding="utf-8")
    dossier.write_index(folder, [{"тема": "Платёжный шлюз", "ключи": ["платежн", "шлюз", "провайдер"],
                                  "источников": 3, "собрано": "2026-08-02"}])
    r = s.search("что с платёжным шлюзом", limit=2)
    assert r.dossiers and r.dossiers[0].startswith("📁 Досье «Платёжный шлюз»") and "ЮPay" in r.dossiers[0]
    text = gs.render(r, "что с платёжным шлюзом")
    assert text.index("📁 Досье") < text.index("Найдено в графе")
    # досье есть, фрагментов нет — только сводка, без «Найдено (0 из 0)» под ней
    only = gs.render(gs.Result([], 0, gs.Verdict.CONFIDENT, dossiers=r.dossiers), "что с платёжным шлюзом")
    assert only.startswith("📁 Досье") and "Найдено" not in only
    # без семантики «⚠» стоит ПЕРВОЙ строкой, перед досье: досье глушили гейт потребителей
    # по префиксу — а состояние теперь и вовсе полем, не строкой (DS C1 r3)
    blind = s.search("что с платёжным шлюзом", limit=2, semantic=False)
    assert blind.dossiers and blind.blocks and blind.status is gs.Verdict.UNVERIFIED
    assert blind.text.startswith("⚠ Совпадения не проверены") and blind.text.index("📁 Досье") < blind.text.index("Найдено в графе")
    assert blind.fragments.startswith("📁 Досье") and "⚠" not in blind.fragments
    # досье — свидетельство в вердикте: ключ темы, которого нет в текстах, даёт сводку без
    # блоков — и статус по ней, а не «пусто»; один ключ из двух — слабо, но не EMPTY (DS I2/I3 r4)
    dossier.write_index(folder, [{"тема": "Платёжный шлюз", "ключи": ["zzzключ", "qqqключ"],
                                  "источников": 3, "собрано": "2026-08-02"}])
    both = s.search("zzzключ qqqключ", limit=2)
    assert not both.blocks and both.dossiers and both.status is gs.Verdict.CONFIDENT and both.text.startswith("📁 Досье")
    one = s.search("zzzключ wwwслово", limit=2)   # покрытие запроса ключами темы 1 из 2
    assert not one.blocks and one.dossiers and one.status is gs.Verdict.WEAK and not one.empty
    assert s.search("zzzключ qqqключ", limit=2, semantic=False).status is gs.Verdict.UNVERIFIED
    assert s.search("zzzключ", limit=2, semantic=False).status is gs.Verdict.UNVERIFIED


def test_semantic_layer_uses_cached_vectors_and_survives_without_embeddings(tmp_path):
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed)
    s.refresh(force=True)
    assert s.pending_vectors() and s.embed_pending(budget_s=0) == 0, "нулевой бюджет — ни одного вызова"
    n = s.embed_pending()
    assert n == s.size and not s.pending_vectors()
    manifest = json.loads(s._vec_manifest.read_text(encoding="utf-8"))
    blocks = sum(nn for _, _, nn in manifest["files"])
    blob = s._vec_manifest.with_name(manifest["blob"])
    assert manifest["dim"] == 512 and len(manifest["files"]) == n and blob.stat().st_size == blocks * 512 * 4
    assert blocks > n, "у узла с секциями — несколько блоков, не один вектор на файл"
    assert s._vec_manifest.name.startswith("Работа-") and len(s._vec_manifest.stem.split("-")[-1]) == 8, "имя кэша — по пути графа, не по имени папки"
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
    assert gs.chunks("x", "---\na: 1\n---\n# T\nкоротко\n") == ["x → T\nкоротко"], "короткая секция — тоже блок, не мусор"
    # 20 секций по 500 знаков: не первые 12, а начало и конец — «## Решения» в хвосте не теряется (DS I3)
    many = "\n\n".join(f"## С{i}\n" + f"секция {i} " + "слово " * 90 for i in range(20)) + "\n\n## Решения\n- 📌 ждём ответ до пятницы\n"
    parts = gs.chunks("x", many, chars=4000, limit=12)
    assert len(parts) == 12 and parts[0].startswith("x → С0\n") and "секция 19" in parts[-1]
    assert "Решения: - 📌 ждём ответ до пятницы" in parts[-1], "короткая последняя секция приклеена к соседу (GLM M7)"
    assert not any("секция 6 " in p for p in parts), "середина ушла, края остались"


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
    r = brain.vault_search(cfg, "платёжный шлюз", limit=2, snippet_chars=200, timeout=2.5)
    # шов отдаёт значение: состояние полем, фрагменты — модели, текст — человеку (круг 3 по #577)
    assert r.status is brain.Verdict.UNVERIFIED and "Системы/Платёжный шлюз.md" in r.fragments and "⚠" not in r.fragments
    assert r.text.startswith("⚠ Совпадения не проверены семантикой") and "Системы/Платёжный шлюз.md" in r.text
    none = brain.vault_search(cfg, "qqqzzz", limit=2, snippet_chars=200, timeout=2.5)
    assert none.empty and none.status is brain.Verdict.UNVERIFIED and none.text.startswith("⚠ По словам ничего не нашлось")


@pytest.mark.parametrize("cov, sim, sem_used, share, expected", [
    (1.0, 0.0, False, 1.0, gs.Verdict.UNVERIFIED),   # без семантики уверенности нет — даже при полном покрытии (замер 17.09)
    (0.5, 0.0, False, 1.0, gs.Verdict.UNVERIFIED),   # «одно из двух» без семантики — тем более
    (0.5, 0.3, True, 1.0, gs.Verdict.WEAK),          # одно из двух и слабый косинус — «в архиве нет» (DS C1 круга 2)
    (0.6, 0.3, True, 1.0, gs.Verdict.WEAK),          # оба слабые
    (0.5, 0.3, True, 0.5, gs.Verdict.UNVERIFIED),    # ...но кэш собран наполовину — «нет» не доказано (DS критика 2 r3)
    (0.5, 0.3, True, gs.SEM_SHARE_MIN, gs.Verdict.WEAK),          # граница порога закреплена (DS M4 r4)
    (0.5, 0.3, True, gs.SEM_SHARE_MIN - 0.01, gs.Verdict.UNVERIFIED),
    (0.6, 0.6, True, 0.1, gs.Verdict.CONFIDENT),     # семантика уверена — доля кэша не спорит с найденным
    (2 / 3, 0.1, True, 1.0, gs.Verdict.CONFIDENT),   # две иглы из трёх (0,6667) — правило приложения: порог 0,66, не 0,67
    (0.8, 0.1, True, 1.0, gs.Verdict.CONFIDENT),     # лексика уверена
])
def test_verdict_is_a_function_of_available_evidence(cov, sim, sem_used, share, expected):
    assert gs.verdict(cov, sim, sem_used, share) is expected


def test_gate_without_semantics_is_unverified_and_synonym_semantics_is_confident(tmp_path):
    """Ollama занята (эмбеддинг не отдал вектор) — найденное по словам отдаётся, но
    как «не проверено», при любом покрытии (замер 17.09); с семантикой по синониму
    выдача уверенная при нулевом покрытии; бессмысленный запрос — пусто или «⚠»."""
    calls = {"embed": 0}
    def busy(texts, timeout):
        calls["embed"] += 1
        return []
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=busy)
    s.refresh(force=True)
    r = s.search("интеграцию платёжного шлюза ведёт Иван до пятницы срок", limit=3)
    assert not r.sem_used and r.status is gs.Verdict.UNVERIFIED and r.blocks, "нашли по словам, но не подтвердили — «⚠» с причиной"
    assert "не проверены" in r.why_low and s.search("рецепт борща со сметаной для шлюза", limit=3).status is gs.Verdict.UNVERIFIED
    # семантика есть: синоним даёт уверенность без единого общего слова
    s2 = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed)
    s2.refresh(force=True)
    (s.graph / "Системы" / "Поставщик.md").write_text("# Поставщик\nпоставщик платежей: договор подписан\n", encoding="utf-8")
    s2.refresh(force=True)
    s2.embed_pending()
    r2 = s2.search("провайдер", limit=3)
    assert r2.sem_used and not r2.low_conf and "Системы/Поставщик.md" in _rels(r2)
    off = s2.search("рецепт борща со сметаной", limit=3)
    assert off.empty or off.low_conf, "ни слова, ни синонима — пусто или «⚠», не уверенный ответ"
    # аббревиатуры из двух заглавных — иглы; одни стоп-слова — подстрока и «⚠» (DS I4)
    (s.graph / "Системы" / "ИИ помощник.md").write_text("# ИИ помощник\nИИ-помощник по данным, пилот\n", encoding="utf-8")
    s2.refresh(force=True)
    assert gs.needles("что по ИИ")[0] == ["ии"] and gs.needles("что как где")[0] == []
    assert gs.needles("тз по интеграции")[0] == ["тз", "интеграц"], "строчная аббревиатура — тоже термин (DS I6 / GLM M3 r2)"
    assert _rels(s2.search("что по ИИ", limit=2, semantic=False))[0] == "Системы/ИИ помощник.md"
    (s.graph / "Системы" / "Стоп.md").write_text("# Стоп\nкак где что — фраза из одних служебных слов\n", encoding="utf-8")
    s2.refresh(force=True)
    s2.embed_pending()
    only_stop = s2.search("как где что", limit=2)
    assert only_stop.blocks and not only_stop.low_conf, "точное совпадение фразы — свидетельство, не «⚠» (GLM M4 r2)"


def test_vector_cache_survives_races_and_stale_entries(tmp_path):
    """Кэш: неудачная загрузка не защёлкивается (повтор по часам), блоб неизменяем
    и сменяется поколением, векторы переписанного файла не участвуют, вектор
    исчезнувшего файла уходит из памяти, второй писатель уступает по локу."""
    clock = {"t": 1_700_000_000.0}
    now = lambda: clock["t"]  # noqa: E731
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed, now=now)
    s.refresh(force=True)
    n = s.embed_pending()
    manifest = s._vec_manifest
    first_blob = manifest.with_name(json.loads(manifest.read_text(encoding="utf-8"))["blob"])
    # битый манифест: 0 векторов, но не навсегда — починили, часы ушли — загрузилось
    good = manifest.read_text(encoding="utf-8")
    manifest.write_text("{", encoding="utf-8")
    s2 = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed, now=now)
    s2.refresh(force=True)
    assert s2.load_vectors() == 0
    manifest.write_text(good, encoding="utf-8")
    assert s2.load_vectors() == 0, "повтор не раньше VEC_RETRY_S"
    clock["t"] += gs.VEC_RETRY_S + 1
    assert s2.load_vectors() == n
    # файл переписан после загрузки — его старые блоки семантика не использует (GLM M3)
    node = s.graph / "Системы" / "Платёжный шлюз.md"
    node.write_text("# Платёжный шлюз\nновый текст без прежних слов\n", encoding="utf-8")
    os.utime(node, (clock["t"] + 5, clock["t"] + 5))
    s2.refresh(force=True)
    assert "Системы/Платёжный шлюз.md" not in _rels(s2.search("пилот сентября провайдер", limit=6)), \
        "переписанный файл ищется по свежему тексту, а не по старым векторам"
    # новое сохранение — новый блоб (имя уникально и на застывших часах), предыдущее
    # поколение живёт (читатель без лока может держать его манифест), позапрошлое стёрто
    assert s2.embed_pending() >= 1
    second_blob = manifest.with_name(json.loads(manifest.read_text(encoding="utf-8"))["blob"])
    assert second_blob != first_blob and second_blob.exists() and first_blob.exists()
    (s.graph / "Системы" / "Третий.md").write_text("# Третий\nещё один узел с достаточно длинным текстом для блока\n", encoding="utf-8")
    s2.refresh(force=True)
    assert s2.embed_pending() >= 1
    third_blob = manifest.with_name(json.loads(manifest.read_text(encoding="utf-8"))["blob"])
    assert third_blob.exists() and second_blob.exists() and first_blob.exists(), \
        "позапрошлое поколение моложе BLOB_GRACE_S — не трогаем: читатель мог прочитать его манифест секунды назад (DS I2 r3)"
    os.utime(first_blob, (clock["t"] - gs.BLOB_GRACE_S - 1,) * 2)
    # два сохранения на застывших часах — разные блобы: имя поколения не из времени (DS I3 r2)
    s2.save_vectors()
    fourth_blob = manifest.with_name(json.loads(manifest.read_text(encoding="utf-8"))["blob"])
    assert fourth_blob != third_blob and fourth_blob.exists() and third_blob.exists() and not first_blob.exists()
    # чужая запись (ночь, апдейтер): манифест новее — экземпляр перечитывает, а не живёт старым
    fresh = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed, now=now)
    fresh.refresh(force=True)
    assert fresh.load_vectors() >= 1 and str(s.graph / "Системы" / "Третий.md") in fresh._vecs
    # ... и уже загрузивший экземпляр видит чужую запись по mtime манифеста, а не живёт старым (GLM M6 r2)
    (s.graph / "Системы" / "Четвёртый.md").write_text("# Четвёртый\nещё узел с достаточно длинным текстом для блока\n", encoding="utf-8")
    other = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed, now=now)
    other.refresh(force=True)
    clock["t"] += 2
    assert other.embed_pending() >= 1
    os.utime(manifest, (clock["t"] + 3, clock["t"] + 3))
    fresh.refresh(force=True)
    fresh.search("четвёртый узел", limit=2)   # через поиск, не через load_vectors: короткое замыкание по непустым векторам гасило перечитывание (GLM I1 r3)
    assert str(s.graph / "Системы" / "Четвёртый.md") in fresh._vecs, "новый манифест на диске — перечитан без перезапуска"
    # ключ кэша: сменилась модель или нарезка — кэш холодный целиком (DS I2 r2)
    cold = gs.GraphSearch(s.graph, {"sufler": {"embed_model": "other-model"}}, data_dir=tmp_path / "data", embed=fake_embed, now=now)
    cold.refresh(force=True)
    assert cold.load_vectors() == 0 and len(cold.pending_vectors()) == cold.size
    # файл исчез — вектор уходит из памяти вместе с ним (DS M3)
    node.unlink()
    s2.refresh(force=True)
    assert str(node) not in s2._vecs
    # второй писатель при удерживаемом локе уступает
    import fcntl
    holder = open(manifest.with_suffix(".lock"), "a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (s.graph / "Системы" / "Ещё.md").write_text("# Ещё\nдостаточно длинный текст для блока и вектора здесь\n", encoding="utf-8")
    s2.refresh(force=True)
    assert s2.embed_pending() == 0 and "другой процесс" in s2.note
    holder.close()
    assert s2.embed_pending() == 1
    # сервер эмбеддингов не ответил — причина названа (DS I5 / GLM M5 r2)
    mute = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=lambda t, to: [], now=now)
    mute.refresh(force=True)
    (s.graph / "Системы" / "Новый узел.md").write_text("# Новый узел\nдостаточно длинный текст для блока и вектора\n", encoding="utf-8")
    mute.refresh(force=True)
    assert mute.embed_pending() == 0 and "не ответил" in mute.note


def test_indexing_stops_on_live_recording_and_respects_the_budget(tmp_path):
    """Живая запись проверяется перед каждой пачкой, а не на входе; бюджет —
    потолок и для таймаута пачки; нулевой бюджет — ни одного вызова."""
    clock = {"t": 1_700_000_000.0}
    calls: list[float] = []
    def embed(texts, timeout):
        calls.append(timeout)
        clock["t"] += 10.0
        return fake_embed(texts, timeout)
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=embed, now=lambda: clock["t"])
    s.refresh(force=True)
    assert s.embed_pending(budget_s=0) == 0 and calls == [] and "бюджет" in s.note
    live = {"on": False}
    def stop():
        if calls:                       # первая пачка прошла — дальше «Старт» нажат
            live["on"] = True
        return live["on"]
    done = s.embed_pending(batch=4, budget_s=100, timeout=60, should_stop=stop)
    assert len(calls) == 1 and "живая запись" in s.note and done < s.size
    calls.clear()
    s.embed_pending(batch=4, budget_s=25, timeout=60)
    assert len(calls) >= 2 and calls[0] == 25.0 and calls[1] == 15.0 and all(0 < t <= 25 for t in calls), \
        f"таймаут пачки — остаток бюджета, не 60: {calls}"



def test_metacharacters_in_graph_name_do_not_break_generation_cleanup(tmp_path):
    """Имя папки графа с метасимволами ломало glob уборки — блобы копились (DS M7 r2)."""
    g = tmp_path / "Граф [тест]"
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "Узел.md").write_text("# Узел\nдостаточно длинный текст для блока и вектора здесь\n", encoding="utf-8")
    s = gs.GraphSearch(g, {}, data_dir=tmp_path / "data", embed=fake_embed)
    s.refresh(force=True)
    aged = time.time() - gs.BLOB_GRACE_S - 1
    for i in range(3):
        (g / "Системы" / f"У{i}.md").write_text(f"# У{i}\nещё один достаточно длинный текст для блока и вектора\n", encoding="utf-8")
        s.refresh(force=True)
        assert s.embed_pending() >= 1
        for b in (tmp_path / "data" / "graph_search").iterdir():   # уборка стирает только старые (DS I2 r3)
            if b.name.endswith(".f32"):
                os.utime(b, (aged, aged))
    blobs = [p for p in (tmp_path / "data" / "graph_search").iterdir() if p.name.endswith(".f32")]
    assert len(blobs) == 2, f"текущее и предыдущее поколение, не больше: {[b.name for b in blobs]}"


def test_reader_retries_when_writer_publishes_between_manifest_and_blob(tmp_path):
    """Блоб исчез между чтением манифеста и открытием — один повтор по свежему манифесту (GLM I1 r2)."""
    s = _search(tmp_path)
    manifest = s._vec_manifest
    stale = json.loads(manifest.read_text(encoding="utf-8"))
    r = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed)
    r.refresh(force=True)
    real_read = pathlib.Path.read_text
    state = {"n": 0}
    def flaky_read(self, *a, **kw):
        text = real_read(self, *a, **kw)
        if self == manifest and state["n"] == 0:
            state["n"] += 1
            # писатель успел: старый манифест в руках читателя указывает на стёртый блоб
            (s.graph / "Системы" / "Ещё.md").write_text("# Ещё\nдостаточно длинный текст для блока и вектора здесь\n", encoding="utf-8")
            s.refresh(force=True); s.embed_pending(); s.refresh(force=True); s.embed_pending()
            manifest.with_name(stale["blob"]).unlink(missing_ok=True)
            return json.dumps(stale)
        return text
    import unittest.mock as um
    with um.patch.object(pathlib.Path, "read_text", flaky_read):
        assert r.load_vectors() >= 1, "повтор по свежему манифесту, а не 30 секунд без семантики"
    assert r._manifest_seen == manifest.stat().st_mtime, "запомнен mtime ПРОЧИТАННОГО манифеста, не старого (GLM M3 r3)"


def test_semantic_fallback_fragment_skips_frontmatter(tmp_path):
    """Файл, найденный семантикой без единой иглы, показывается телом, не YAML-шапкой (DS M9 r2)."""
    s = _search(tmp_path)
    (s.graph / "Системы" / "Поставщик.md").write_text("---\ntype: система\ntags: [встречи]\n---\n# Поставщик\nпоставщик платежей: договор подписан\n", encoding="utf-8")
    s.refresh(force=True)
    s.embed_pending()
    r = s.search("провайдер", limit=4)
    block = next(b for b in r.blocks if b.startswith("• Системы/Поставщик.md"))
    assert "type: система" not in block and "поставщик платежей" in block


def test_node_files_keep_twice_as_many_chunks(tmp_path):
    """Узел человека с длинной историей: лимит блоков вдвое выше, чем у заметки —
    середина истории не исчезает целиком (GLM r2, критика 1)."""
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed)
    long = "# Иван\n" + "\n\n".join(f"## Встреча {i}\n" + f"встреча {i} " + "слово " * 90 for i in range(40))
    (s.graph / "Люди" / "Иван Долгий.md").write_text(long, encoding="utf-8")
    (s.graph / "Встречи" / "2026-08-09_1000.md").write_text(long, encoding="utf-8")
    s.refresh(force=True)
    s.embed_pending()
    node_vecs = s._vecs[str(s.graph / "Люди" / "Иван Долгий.md")][1]
    note_vecs = s._vecs[str(s.graph / "Встречи" / "2026-08-09_1000.md")][1]
    assert len(node_vecs) == gs.MAX_CHUNKS_NODE and len(note_vecs) == gs.MAX_CHUNKS


def test_weak_verdict_needs_a_mostly_vectorised_cache(tmp_path):
    """«В архиве нет» — вердикт о проверенном архиве: пока векторы есть меньше чем у
    SEM_SHARE_MIN файлов, слабый лучший косинус говорит о векторизованной части, а не
    об архиве — выдача «не проверена», а не «слабая» (DS критика 2 r3)."""
    s = _search(tmp_path)
    off = s.search("рецепт борща со сметаной для шлюза", limit=3)
    assert off.status is gs.Verdict.WEAK
    extra = s.size // 4 + 1   # новые файлы без векторов: доля size/(size+extra) чуть ниже порога
    for i in range(extra):
        (s.graph / "Встречи" / f"2026-08-1{i % 9}_{1000 + i}.md").write_text(
            f"# Встреча {i}\nобсуждали шлюз и сроки, пункт {i}\n", encoding="utf-8")
    s.refresh(force=True)
    assert s.vectors / s.size < gs.SEM_SHARE_MIN
    half = s.search("рецепт борща со сметаной для шлюза", limit=3)
    assert half.sem_used and half.status is gs.Verdict.UNVERIFIED and "не проверены" in half.why_low
    assert half.reason == gs.REASON_CACHE and gs.REASON_CACHE in half.why_low, "причина — кэш, не «модель занята» (GLM M2 r5)"
    assert s.search("qqqzzz", limit=2, semantic=False).reason == gs.REASON_EMBED
    # пустые файлы векторов не получат никогда — в знаменателе доли им не место (DS M2 r4)
    s.embed_pending()
    for i in range(s.size // 4 + 1):
        (s.graph / "Встречи" / f"2026-08-0{i % 9}_{2000 + i}.md").write_text("", encoding="utf-8")
    s.refresh(force=True)
    assert s.search("рецепт борща со сметаной для шлюза", limit=3).status is gs.Verdict.WEAK
    # ...и в числителе им не место: пустой файл с вектором-заглушкой считал бы архив
    # проверенным при четырёх непустых без векторов (12 из 16 = 0,75 — «не проверено»)
    s.embed_pending()
    for i in range(4):
        (s.graph / "Встречи" / f"2026-08-2{i}_{3000 + i}.md").write_text(
            f"# Встреча {i}\nещё тема без векторов, пункт {i}\n", encoding="utf-8")
    s.refresh(force=True)
    assert s.search("рецепт борща со сметаной для шлюза", limit=3).status is gs.Verdict.UNVERIFIED
    for p in list(s.graph.glob("Встречи/2026-08-0*_2*.md")) + list(s.graph.glob("Встречи/2026-08-2*_3*.md")):
        p.unlink()
    s.refresh(force=True)
    assert s.search("платёжный шлюз", limit=2).status is gs.Verdict.CONFIDENT, "сильный сигнал доля кэша не отменяет"
    s.embed_pending()
    assert s.search("рецепт борща со сметаной для шлюза", limit=3).status is gs.Verdict.WEAK


def test_key_change_in_live_process_drops_vectors_before_search_and_indexing(tmp_path):
    """Модель эмбеддингов сменилась в конфиге живого процесса — векторы в памяти
    считаны другой моделью, косинус с ними — шум: сброс до поиска и до сборки (DS I1 r3)."""
    s = _search(tmp_path)
    assert s.vectors and s.search("платёжный шлюз", limit=2).status is gs.Verdict.CONFIDENT
    s.cfg["sufler"] = {"embed_model": "other-model"}
    r = s.search("платёжный шлюз", limit=2)
    assert s.vectors == 0 and not r.sem_used and r.status is gs.Verdict.UNVERIFIED
    assert len(s.pending_vectors()) == s.size and s.embed_pending() == s.size
    assert json.loads(s._vec_manifest.read_text(encoding="utf-8"))["key"] == s.cache_key()
    assert s.search("платёжный шлюз", limit=2).status is gs.Verdict.CONFIDENT
    # штамп неудачи сбрасывается вместе с векторами: чужой ключ на диске → поиск (штамп) →
    # смена модели → свой кэш собран → поиск сразу с векторами, а не через VEC_RETRY_S (DS M1 r4)
    clock = {"t": 1_700_000_000.0}
    t = gs.GraphSearch(s.graph, {"sufler": {"embed_model": "third"}}, data_dir=tmp_path / "data",
                       embed=fake_embed, now=lambda: clock["t"])
    t.refresh(force=True)
    assert not t.search("платёжный шлюз", limit=2).sem_used and t._vecs_tried_at is not None
    t.cfg["sufler"] = {"embed_model": "other-model"}     # на диске — кэш под этот ключ
    assert t.search("платёжный шлюз", limit=2).status is gs.Verdict.CONFIDENT


def test_without_a_lock_nothing_is_written(tmp_path, monkeypatch):
    """Замок не взялся (том без flock, EMFILE): два писателя без замка стёрли бы блоб
    друг друга уборкой — не пишем вовсе, причина в note (DS I2 / GLM M4 r3)."""
    import fcntl
    s = gs.GraphSearch(_graph(tmp_path), {}, data_dir=tmp_path / "data", embed=fake_embed)
    s.refresh(force=True)
    monkeypatch.setattr(fcntl, "flock", lambda *a: (_ for _ in ()).throw(OSError(1, "Operation not permitted")))
    assert s.embed_pending() == 0 and "без замка" in s.note and not s._vec_manifest.exists()
    monkeypatch.undo()
    assert s.embed_pending() == s.size and s._vec_manifest.exists()


def test_first_cache_read_is_not_throttled_by_a_zero_clock_and_foreign_key_is_not_reread_each_search(tmp_path):
    """Штамп неудачи — None, а не 0.0: часы от нуля давали «недавно пробовали» на первом
    же чтении (DS M3 r3). Чужой ключ на диске у прогретого экземпляра — одна неудача и
    пауза VEC_RETRY_S, а не чтение манифеста каждым поиском (GLM M3 r3)."""
    s = _search(tmp_path)
    zero = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed, now=lambda: 0.0)
    zero.refresh(force=True)
    assert zero.load_vectors() == s.vectors, "первое чтение — сразу, без оглядки на часы"
    clock = {"t": 1_700_000_000.0}
    warm = gs.GraphSearch(s.graph, {}, data_dir=tmp_path / "data", embed=fake_embed, now=lambda: clock["t"])
    warm.refresh(force=True)
    assert warm.load_vectors() == s.vectors
    foreign = json.loads(s._vec_manifest.read_text(encoding="utf-8"))
    foreign["key"] = "другая-модель|chunks9"
    s._vec_manifest.write_text(json.dumps(foreign), encoding="utf-8")
    os.utime(s._vec_manifest, (clock["t"] + 10, clock["t"] + 10))
    reads = {"n": 0}
    real_read = pathlib.Path.read_text
    def counting_read(self, *a, **kw):
        if self == s._vec_manifest:
            reads["n"] += 1
        return real_read(self, *a, **kw)
    import unittest.mock as um
    with um.patch.object(pathlib.Path, "read_text", counting_read):
        assert warm.search("платёжный шлюз", limit=2).status is gs.Verdict.CONFIDENT, "свои векторы под свой ключ остаются"
        warm.search("платёжный шлюз", limit=2)
        warm.search("платёжный шлюз", limit=2)
    assert reads["n"] == 1, "чужой ключ — одна попытка, дальше пауза, а не чтение манифеста каждым поиском"
    clock["t"] += gs.VEC_RETRY_S + 1
    with um.patch.object(pathlib.Path, "read_text", counting_read):
        warm.search("платёжный шлюз", limit=2)
    assert reads["n"] == 2
