"""Память демона (src/brain.py): граф из конфига, формат выдачи, сбой — исключением.

До №250 здесь жил HTTP-клиент сервера памяти (:8100) и тест держал его контракт
живым сервером. Теперь память — индекс графа в процессе демона
(src/graph_search.py); контракт для трёх контуров демона — значение: состояние
полем `status` (Verdict), фрагменты для модели, текст для человека (круг 3 по
#577: маркеры в строке разбирались префиксом и ломались), а сбой — исключение,
деградацию каждый контур выбирает сам.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import brain  # noqa: E402
import graph_search  # noqa: E402
from model_seam import Embedder  # noqa: E402


def _graph(root: pathlib.Path, name: str, marker: str) -> pathlib.Path:
    g = root / "Графы" / name
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "Релиз.md").write_text(f"# Релиз\nРешили по релизу: {marker}\n\n## Встречи\n", encoding="utf-8")
    return g


def test_vault_search_reads_the_configured_graph_only(tmp_path, monkeypatch):
    # graphs.graph_dir читает env ДО конфига — машинные переменные не
    # должны решать исход теста (круг-1 по #405, DS)
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.setattr(brain, "_shared", {})
    mine = _graph(tmp_path, "проект", "МОЙ_МАРКЕР")
    _graph(tmp_path, "соседний", "ЧУЖОЙ_МАРКЕР")
    cfg = {"sufler": {"graph_dir": str(mine)}}
    with pytest.raises(brain.MemoryNotReady):
        brain.vault_search(cfg, "что решили по релизу", limit=3, snippet_chars=700, timeout=5)   # не прогрет
    mem = brain.warm(cfg)
    assert mem is not None and mem.size == 1
    out = brain.vault_search(cfg, "что решили по релизу", limit=3, snippet_chars=700, timeout=5)
    # без кэша векторов семантики нет — выдача честно помечена полем, но не пуста
    assert out.status is brain.Verdict.UNVERIFIED and "• Системы/Релиз.md" in out.fragments and "⚠" not in out.fragments
    assert out.text.startswith("⚠ Совпадения не проверены семантикой") and "• Системы/Релиз.md" in out.text
    assert "МОЙ_МАРКЕР" in out.fragments and "ЧУЖОЙ_МАРКЕР" not in out.text, "соседние графы в ответы не попадают"
    none = brain.vault_search(cfg, "qqqzzz", limit=3, snippet_chars=700, timeout=5)
    assert none.empty and none.status is brain.Verdict.UNVERIFIED, "пусто без семантики — не доказанное отсутствие (GLM C1 r3)"


def test_unconfigured_graph_raises(monkeypatch):
    # Сбой — исключением: деградация у каждого контура своя (узлы графа,
    # молчание, пустая память) — память её не выбирает за вызывающего.
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.setattr(brain, "_shared", {})
    with pytest.raises(brain.MemoryUnavailable):
        brain.vault_search({"sufler": {}}, "вопрос", limit=1, snippet_chars=100, timeout=0.3)
    assert issubclass(brain.MemoryUnavailable, RuntimeError) and issubclass(brain.MemoryNotReady, RuntimeError)
    assert brain.warm({"sufler": {}}) is None


def _res(status, blocks=(), dossiers=()):
    return graph_search.Result(list(blocks), len(blocks), status, dossiers=list(dossiers), query="q")


@pytest.mark.parametrize("status", list(brain.Verdict))
@pytest.mark.parametrize("shape", ["blocks", "dossiers", "empty"])
def test_memory_block_policy_is_one_table_for_every_status_and_shape(status, shape):
    """Политика «что честно сказать» — таблица фасада, а не if/elif контуров:
    шапка по статусу, EMPTY — без фрагментов, пусто и без узлов — '' (DS r4, критика 2)."""
    r = _res(status, blocks=["• a.md\n  факт"] if shape == "blocks" else (),
             dossiers=["📁 Досье «т»\n  сводка"] if shape == "dossiers" else ())
    block = brain.memory_block(r, budget=500)
    if shape == "empty" or status is brain.Verdict.EMPTY:
        assert block == "" and brain.memory_block(r, nodes="узел: статус", budget=500).startswith("Из узлов графа проекта:")
    else:
        assert block.startswith(brain.LEAD[status]) and r.fragments in block and "⚠" not in block
        if status is brain.Verdict.CONFIDENT:
            assert "СЛАБЫЕ" not in block and "НЕ ПРОВЕРЕНО" not in block
    assert brain.absence_note(r) == brain.ABSENCE[status]
    assert (status is brain.Verdict.EMPTY) == (brain.absence_note(r) == "пусто"), "«пусто» — только по проверенной выдаче (DS C1 r4)"
    assert set(brain.LEAD) == set(brain.ABSENCE) == set(brain.Verdict)
    # причина «не проверено» — поле результата, не таблица: «модель занята» при неполном кэше — ложь (GLM M2 r5)
    r.reason = "кэш собран не весь"
    assert brain.absence_note(r) == f"{brain.ABSENCE[status]} (кэш собран не весь)"
    assert brain.memory_block(None, nodes="узел", budget=100) == "Из узлов графа проекта:\nузел"


def test_memory_block_splits_the_budget_between_nodes_and_fragments():
    """Узлы первыми под общий кап съедали архив целиком (DS I1 r4): бюджет делится
    по долям, остаток одного уходит другому."""
    r = _res(brain.Verdict.UNVERIFIED, blocks=["• a.md\n  " + "ж" * 3000])
    nodes = "ъ" * 3000
    block = brain.memory_block(r, nodes=nodes, budget=2600)
    assert len(block) <= 2600 + 2 and block.startswith("Из узлов графа проекта:")
    assert brain.LEAD[brain.Verdict.UNVERIFIED] in block and block.count("ж") >= 1000, "фрагменты не съедены узлами"
    assert block.count("ъ") <= int(2600 * brain.NODES_SHARE)
    short_nodes = brain.memory_block(r, nodes="ъ" * 100, budget=2600)
    assert short_nodes.count("ж") > 2000, "остаток бюджета узлов уходит фрагментам"
    only_nodes = brain.memory_block(None, nodes=nodes, budget=1000)
    assert only_nodes.startswith("Из узлов графа проекта:") and len(only_nodes) == 1000 and "vault" not in only_nodes
    assert brain.memory_block(None, budget=1000) == ""
    # уверенная выдача: главные — фрагменты, узлам — только остаток (GLM M3 r5)
    sure = _res(brain.Verdict.CONFIDENT, blocks=["• a.md\n  " + "ж" * 3000])
    assert brain.memory_block(sure, nodes=nodes, budget=2600).count("ъ") == 0
    assert brain.memory_block(sure, nodes=nodes, budget=2600).count("ж") >= 2500
    short_sure = _res(brain.Verdict.CONFIDENT, blocks=["• a.md\n  " + "ж" * 500])
    assert 1500 < brain.memory_block(short_sure, nodes=nodes, budget=2600).count("ъ") <= 2100


def test_scope_note_names_what_was_not_read_and_stays_silent_when_it_was():
    """Оговорка про непрочитанное — только при отрицательном ответе и без досье.

    Сборщик сводок по темам обходит ВЕСЬ граф, архив включительно, поэтому при
    досье в выдаче «архив не читался» было бы ложью в обратную сторону (DS,
    входной круг по №295). При уверенном ответе оговорка не нужна вовсе."""
    skipped = ("Встречи-архив", "Документация/Стенограммы встреч")
    weak = _res(brain.Verdict.WEAK)
    weak.skipped = skipped
    note = brain.scope_note(weak)
    assert "Встречи-архив" in note and "фрагменты" in note, note
    assert "не читал" not in note, "оговорка про фрагменты: сводки по темам собраны по всему графу"
    assert brain.scope_note(weak) in brain.absence_note(weak), "оговорка не дошла до статуса нити"

    sure = _res(brain.Verdict.CONFIDENT)
    sure.skipped = skipped
    assert brain.scope_note(sure) == "", "уверенный ответ не нуждается в оговорке"

    # досье оговорку НЕ глушат: замер 17.09 — они есть в 29 неуверенных ответах
    # из 30, и правка молчала бы почти всегда
    with_dossier = _res(brain.Verdict.WEAK, dossiers=["📁 Досье «т»\n  сводка"])
    with_dossier.skipped = skipped
    assert brain.scope_note(with_dossier) == note
    assert brain.scope_note(_res(brain.Verdict.WEAK)) == "", "нечего исключать — молчим"

    # файл, который не открылся, тоже остался за индексом (DS, круг 2)
    unread = _res(brain.Verdict.EMPTY)
    unread.unread = 3
    assert "не открылось файлов: 3" in brain.scope_note(unread)


def test_no_policy_line_claims_the_unread_archive_was_checked():
    """Ни одна строка фасада не утверждает проверку архива: индекс его не читает.

    Замер 17.09 на рабочем графе: 11 506 файлов вне индекса против 3 283 в нём,
    1 945 файлов архива несут строки решений. До правки таблица говорила
    «скорее всего в архиве ответа нет»."""
    for table_name, table in (("LEAD", brain.LEAD), ("ABSENCE", brain.ABSENCE)):
        for status, text in table.items():
            assert "в архиве" not in text, f"{table_name}[{status}]: {text}"


@pytest.mark.parametrize("status", list(brain.Verdict))
def test_absence_only_is_a_table_not_an_if_in_the_contour(status):
    """«Подавать нечего» — политика по вердикту, и живёт она рядом с таблицами.

    Слабый ответ со сводкой по теме уходит в пересказ, а не в «почти ничего»:
    сводка собрана обходом всего графа и сама является свидетельством (GLM,
    круг 2 по №295). Раньше это условие стояло рукописным if в контуре."""
    empty = _res(status)
    assert brain.absence_only(empty) is True, "без блоков и сводок подавать нечего"

    with_dossier = _res(status, dossiers=["📁 Досье «т»\n  сводка"])
    assert brain.absence_only(with_dossier) is False, "сводка — свидетельство, не пустота"

    with_blocks = _res(status, blocks=["• a.md\n  факт"])
    assert brain.absence_only(with_blocks) is (status is not brain.Verdict.UNVERIFIED)


def _embedder(model: str = "test-fake") -> Embedder:
    """Способность целиком — функция и имя модели: имя подписывает индекс и кэш векторов."""
    return Embedder(lambda texts: [[1.0, 0.0] for _ in texts], model)


# Индекс на процесс — у приложения, а не у модуля графа: shared() переехала сюда вместе
# со своим тестом (Opus M2 круга 1 по коду №365 — тест в тестах графа тянул brain за
# пределы замыкания пакета).
def test_shared_index_is_one_per_graph(tmp_path, monkeypatch):
    g = _graph(tmp_path, "проект", "М")
    monkeypatch.setattr(brain, "_shared", {})
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    a = brain.shared({"sufler": {"graph_dir": str(g)}}, graph_dir=g, embedder=_embedder())
    b = brain.shared({}, graph_dir=g, embedder=_embedder())
    assert a is b
    assert brain.shared({"sufler": {}}, graph_dir=None, embedder=_embedder()) is None, \
        "граф не настроен — индекса нет"
    # модель — часть личности индекса: под её именем подписаны и векторы в памяти,
    # и кэш на диске. Пока ключом был только путь, второй позвавший получал чужой
    # векторизатор молча, а свой передать уже не мог (круг 2 по №321, DS C1 / GLM C1)
    other = brain.shared({}, graph_dir=g, embedder=_embedder("other-model"))
    assert other is not a, "другая модель — другой индекс, а не тихая подмена"
    assert other.cache_key() != a.cache_key()
