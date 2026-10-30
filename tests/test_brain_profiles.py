"""Профили потребителей памяти в `brain.py` (№629 ч. 2, P1): запись `Packed`,
шов `search` + `pack`, kwargs модели по синтезу профиля и сторож обхода фасада."""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import brain  # noqa: E402
import layout_map as lm  # noqa: E402
from charoite_graph import graph_search  # noqa: E402

V = graph_search.Verdict


def _res(status, blocks=(), dossiers=(), **kw):
    return graph_search.Result(list(blocks), len(blocks), status=status, dossiers=list(dossiers), **kw)


def test_packed_fields_are_the_parts_of_the_text():
    """`text` собран из `lead`, `nodes` и `body`; срезанные бюджетом фрагменты —
    в `cut`, вошедшие целиком — нет. Слова шапки в `body` не попадают."""
    r = _res(V.WEAK, blocks=("а" * 300, "б" * 300, "в" * 300), skipped=("архив",))
    p = brain._memory_block(r, budget=500)
    assert p.text == f"{p.lead}\n{p.body}"
    assert p.lead.startswith("Из графа и документов (vault) — СОВПАДЕНИЯ СЛАБЫЕ") and "архив" in p.lead
    assert p.body.startswith("а" * 300) and "СЛАБЫЕ" not in p.body
    assert p.cut == ("б" * 300, "в" * 300), "второй срезан частью, третий целиком"
    assert p.nodes == ""
    whole = brain._memory_block(r, budget=10_000)
    assert whole.cut == () and whole.body == r.fragments


def test_packed_nodes_and_fragments_share_the_budget():
    r = _res(V.UNVERIFIED, blocks=("ж" * 3000,), reason="Ollama занята")
    p = brain._memory_block(r, nodes="ъ" * 3000, budget=2600)
    assert p.text == f"{brain.NODES_HEAD}{p.nodes}\n\n{p.lead}\n{p.body}"
    assert len(p.text) == 2600, "разделитель узлов и фрагментов — внутри бюджета"
    assert set(p.nodes) == {"ъ"} and set(p.body) == {"ж"}
    assert p.cut == ("ж" * 3000,)
    only_nodes = brain._memory_block(None, nodes="узел", budget=100)
    assert only_nodes == brain.Packed(f"{brain.NODES_HEAD}узел", "", "узел", "", ())
    assert brain._memory_block(_res(V.EMPTY), budget=100) == brain.Packed("")


def test_dossiers_count_as_fragments_in_cut():
    r = _res(V.CONFIDENT, blocks=("б" * 50,), dossiers=("д" * 50,))
    p = brain._memory_block(r, budget=len(brain.LEAD[V.CONFIDENT]) + 1 + 60)
    assert p.body.startswith("д" * 50) and p.cut == ("б" * 50,)


def test_pack_takes_the_profile_budget():
    r = _res(V.CONFIDENT, blocks=("ф" * 5000,))
    for prof in brain.PROFILES.values():
        assert len(brain.pack(prof, r).text) == prof.budget


def test_search_with_a_graph_requires_an_embedder(tmp_path):
    with pytest.raises(ValueError, match="векторизатор"):
        brain.search(brain.ANSWER, "вопрос", graph=tmp_path)


def test_search_without_a_source_is_an_error():
    """Без `cfg` и без `graph` памяти взяться неоткуда: ошибка вызова, а не
    «граф не настроен», которое демон проглотил бы молча."""
    with pytest.raises(ValueError, match="источника памяти нет"):
        brain.search(brain.ANSWER, "вопрос")


def test_search_passes_the_profile_numbers_to_the_engine(monkeypatch):
    seen = {}

    class GS:
        def search(self, query, **kw):
            seen.update(kw, query=query)
            return _res(V.CONFIDENT, blocks=("x",))

    def shared(cfg, graph_dir=None, *, embedder):
        seen["graph"], seen["model"] = graph_dir, embedder.model
        return GS()

    monkeypatch.setattr(brain, "shared", shared)
    emb = types.SimpleNamespace(model="bge")
    brain.search(brain.EXPAND, "тема", graph=pathlib.Path("/g"), embedder=emb)
    assert seen == {"query": "тема", "limit": 3, "snippet_chars": 700, "embed_timeout": 4.0,
                    "dossiers": True, "dossier_limit": 1, "dossier_chars": 450,
                    "graph": pathlib.Path("/g"), "model": "bge"}


def test_search_carries_the_dossiers_axis_to_the_engine(monkeypatch):
    """Ось профиля — аргумент вызова: `LIVE._replace(dossiers=False)` доезжает до
    `mem.search` как `dossiers=False`, а профиль с включённой осью идёт с `True`."""
    seen = {}

    class GS:
        def search(self, query, **kw):
            seen.update(kw, query=query)
            return _res(V.CONFIDENT, blocks=("x",))

    monkeypatch.setattr(brain, "shared", lambda *a, **kw: GS())
    monkeypatch.setattr(brain.llm, "embedder", lambda cfg: types.SimpleNamespace(model="bge"))
    brain.search(brain.LIVE._replace(dossiers=False), "тема", cfg={})
    assert seen["dossiers"] is False
    seen.clear()
    brain.search(brain.LIVE, "тема", cfg={})
    assert seen["dossiers"] is True


def test_profiles_dossiers_axis_is_a_conscious_table():
    """Ось досье — решение по каждому профилю, а не умолчание: замер 06.10 на 37
    вопросах владельца (seed 0) — ответ с досье находит 8, без досье 16, сводки
    тем съедали блок прежнего бюджета, 2000 знаков. Таблица целиком, чтобы профиль
    без решения (добавленный молча) уронил тест."""
    assert {p.name: p.dossiers for p in brain.PROFILES.values()} == {
        "answer": False, "live": True, "expand": True}


def test_answer_profile_looks_no_dossiers_up(monkeypatch):
    """`ANSWER` — сам, без `_replace`: движок получает `dossiers=False`, иначе
    замеренный выигрыш не доезжает до владельца."""
    seen = {}

    class GS:
        def search(self, query, **kw):
            seen.update(kw, query=query)
            return _res(V.EMPTY)

    monkeypatch.setattr(brain, "shared", lambda *a, **kw: GS())
    monkeypatch.setattr(brain.llm, "embedder", lambda cfg: types.SimpleNamespace(model="bge"))
    brain.search(brain.ANSWER, "вопрос", cfg={})
    assert seen["dossiers"] is False


def test_answer_prompt_without_a_block_has_no_source_two_but_keeps_abstention():
    """Пустой блок (`EMPTY` у `ANSWER`) — в промпте нет «ИСТОЧНИК 2», но указание
    о пометке воздержания стоит всегда: ответ без памяти обязан сказать, что он из
    общих знаний (№641)."""
    prompt = brain.answer_prompt("что решили?", "", "хвост стенограммы")
    assert "ИСТОЧНИК 2" not in prompt
    assert brain.ANSWER.synth.abstain in prompt


def test_stream_kwargs_pass_only_what_the_profile_sets():
    llm = types.SimpleNamespace(small="s", model="m")
    assert brain.stream_kwargs(brain.ANSWER, llm) == {"model": "s", "num_predict": 220}
    assert brain.stream_kwargs(brain.EXPAND, llm) == {"model": "s", "system": brain.EXPAND_SYSTEM}
    with pytest.raises(ValueError):
        brain.stream_kwargs(brain.LIVE, llm)


_REL = ("Проекты/Чароит/Встречи/2026/Октябрь/подпапка/ещё/вложенная/папка/"
        + "очень_длинное_имя_заметки_" * 3 + "заметка.md")
_NODE_REL = ("Проекты/Чароит/Люди/Отдел/Команда/ещё/одна/вложенная/папка/"
             + "длинный_путь_узла_" * 3 + "узел.md")
_SKIPPED = ("архив встреч за прошлые годы, копии стенограмм и звук",
            "личные дневники владельца вне рабочего графа")


def _worst_fragment(profile: brain.Profile = brain.ANSWER) -> str:
    """Худший одиночный фрагмент поиска профиля: строит сам `graph_search.snippet`
    на плотном тексте длиннее порога «короткий файл целиком». Игла запроса стоит
    дальше 150 знаков от начала, редкая игла — вне первого окна с запасом текста с
    обеих сторон, так что второй фрагмент строится даже при росте `snippet_chars`
    (ветка `chars >= 800`, `graph_search.py`). Размер даёт `snippet`, не тест."""
    needle, rare = "релиз", "уникальныйфакт"
    text = "п" * 400 + needle + "о" * 2500 + rare + "р" * 9000
    assert len(text) >= 10_000
    return graph_search.snippet(text, re.compile(re.escape(needle)),
                                profile.snippet_chars, [rare], dense=True)


def test_answer_budget_holds_the_largest_block_the_search_can_build():
    """Наибольший одиночный блок, который может выдать поиск `ANSWER`, вместе с
    самой длинной шапкой помещается в бюджет профиля целиком, а бюджет на знак
    меньше — режет. Обе формы блока: прямая находка `UNVERIFIED` (самая длинная
    шапка, к ней дописана оговорка охвата) и переход по ссылке `CONFIDENT`.
    Пути и охват — не короче, чем у графа владельца (замер 06.10: пути p99 60,
    максимум 90 знаков; оговорка охвата 120, шапка UNVERIFIED с ней — 246)."""
    frag = _worst_fragment()
    assert "… …" in frag, "построен не худший случай: второго фрагмента нет"

    rel, node_rel, skipped = _REL, _NODE_REL, _SKIPPED
    assert len(rel) >= 100 and len(node_rel) >= 100, "пути короче графа владельца"
    assert all(len(s) >= 40 for s in skipped), "оговорка охвата короче графа владельца"

    forms = {
        "прямая находка": _res(V.UNVERIFIED, blocks=(f"• {rel}\n  {frag}",),
                                skipped=skipped, service=42),
        "переход по ссылке": _res(
            V.CONFIDENT, blocks=(f"• {rel}\n  ↳ по ссылке из {node_rel}\n  {frag}",)),
    }
    for label, res in forms.items():
        packed = brain.pack(brain.ANSWER, res)
        assert packed.cut == (), f"{label}: блок не помещается в бюджет целиком"
        assert res.blocks[0] in packed.text, f"{label}: блок входит не целиком"
        tight = brain.pack(brain.ANSWER._replace(budget=len(packed.text) - 1), res)
        assert tight.cut, f"{label}: бюджет на знак меньше не срезал тот же блок"


def _band(profile: brain.Profile) -> list[str]:
    """Полоса досье профиля в худшем виде: столько тем, сколько берёт профиль,
    имя темы длиннее графа владельца (замер 30.10: 922 темы, имя ≤ 41 знака),
    тело длиннее любого потолка. Блок строит `graph_search.dossier_block`."""
    theme = "очень длинное имя темы досье, длиннее любого в графе владельца"
    assert len(theme) >= 60
    return [graph_search.dossier_block(theme, "слово " * 2000, profile.snippet_chars, profile.dossier_chars)
            for _ in range(profile.dossier_limit)]


def _lanes_fit(profile: brain.Profile) -> dict[str, bool]:
    """Помещается ли лучший блок профиля после полосы досье: на уверенной выдаче
    без узлов — целиком, на неуверенной с узлами (живой контекст, `nodes=fallback`)
    — шапка блока и первые `snippet_chars` знаков выдержки (попадание стоит в
    первых 150, хвост окна до `snippet_chars × 1,5 + 150` — нет: бенч с узлами — долг
    №637). Шапка выдачи — самая длинная (UNVERIFIED с оговоркой охвата). Запас на
    30.10: окно с узлами — 51 знак у expand и 15 у live, целиком — 495 и 921."""
    frag = _worst_fragment(profile)
    block = f"• {_REL}\n  ↳ по ссылке из {_NODE_REL}\n  {frag}"
    head = block[:block.index(frag) + profile.snippet_chars]
    band = _band(profile)
    sure = brain.pack(profile, _res(V.CONFIDENT, blocks=(block,), dossiers=band, skipped=_SKIPPED))
    loose = brain.pack(profile, _res(V.UNVERIFIED, blocks=(block,), dossiers=band, skipped=_SKIPPED,
                                     service=42), nodes="у" * 5000)
    return {"целиком": block in sure.body and not sure.cut, "окно с узлами": head in loose.body}


def test_dossier_band_leaves_the_best_block_in_the_budget():
    """Инвариант полос по `PROFILES` (№855): у профиля с досье полоса сводок не
    вытесняет лучший блок выдачи. Замер 30.10: две сводки по 3·snippet шли первыми
    и срезали блоки — expand 12 против 20 без досье. Профиль с досье задаёт полосу
    сам; таблица — целиком, чтобы новый профиль без решения уронил тест."""
    with_dossiers = {p.name: p for p in brain.PROFILES.values() if p.dossiers}
    assert set(with_dossiers) == {"expand", "live"}
    for name, prof in with_dossiers.items():
        assert prof.dossier_limit >= 1 and prof.dossier_chars is not None, f"{name}: полоса не задана"
        assert all(len(b) <= prof.dossier_chars for b in _band(prof)), f"{name}: блок досье длиннее потолка"
        assert _lanes_fit(prof) == {"целиком": True, "окно с узлами": True}, name


def test_packer_gives_fragments_exactly_the_fragment_share():
    """`fragment_share` — та доля, что упаковщик отдаёт фрагментам, когда и узлы, и
    фрагменты длиннее бюджета: при неуверенной выдаче ровно она; при уверенной узлы
    берут только остаток, его нет — разделителя нет, фрагментам весь бюджет, не
    меньше доли (выходной круг №855)."""
    for status in (V.CONFIDENT, V.UNVERIFIED, V.WEAK):
        p = brain._memory_block(_res(status, blocks=("ж" * 5000,)), nodes="у" * 5000, budget=2600)
        got, share = len(p.text) - p.text.index(p.lead), brain.fragment_share(2600, status, with_nodes=True)
        assert len(p.text) == 2600, status
        assert got == (2600 if status is V.CONFIDENT else share) and got >= share, status
    p = brain._memory_block(_res(V.CONFIDENT, blocks=("ж" * 5000,)), nodes="у" * 100, budget=2600)
    assert not p.nodes and not p.text.startswith("\n") and len(p.text) == 2600


def test_dossier_band_of_the_old_numbers_cuts_the_best_block():
    """Тот же тест на прежней полосе (две темы, тело 3·snippet) падает: инвариант
    ловит то, что мерил замер, а не проходит на любых числах."""
    for prof in (brain.EXPAND, brain.LIVE):
        old = prof._replace(dossier_limit=2, dossier_chars=None)
        assert _lanes_fit(old)["целиком"] is False, prof.name


def _code(src: str) -> lm.FileInfo:
    return lm.FileInfo(kind="code", haystacks=(), tree=ast.parse(src), executable=None)


def test_seam_guards_private_memory_primitives_outside_brain():
    """Вызов `_vault_search` или `_memory_block` вне `brain.py` — красный гейт
    раскладки; сам фасад зовёт их голым именем, и это живая ссылка шва."""
    files = {
        "src/daemon_like.py": "import brain\nbrain._memory_block(None, budget=1)\n",
        "scripts/bench_like.py": "from brain import _vault_search\n_vault_search({}, 'q', limit=1, snippet_chars=1, timeout=1)\n",
        "src/brain.py": "def _vault_search(): pass\ndef _memory_block(): pass\n"
                        "def search(): return _vault_search()\ndef pack(): return _memory_block()\n",
    }
    inv = lm.Inventory(files={rel: _code(src) for rel, src in files.items()}, problems=[])
    calls = lm.seam_calls(inv)
    assert calls["src/brain.py"] == {"_vault_search": [3], "_memory_block": [4]}
    said = lm.seam_problems(calls)
    assert any(line.startswith("src/daemon_like.py:2 зовёт brain._memory_block") for line in said), said
    assert any(line.startswith("scripts/bench_like.py:2 зовёт brain._vault_search") for line in said), said
    assert not any(line.startswith("src/brain.py") for line in said)


def test_own_refs_count_only_for_the_seam_owner():
    """Голое имя шва в чужом модуле с тем же хвостом — не ссылка владельца."""
    index = lm.seam_tables()[0]
    tree = ast.parse("GraphSearch()\n")
    assert lm._own_refs(tree, "src/charoite_graph/graph_search.py", index) == {}
    assert lm._own_refs(ast.parse("_memory_block()\n"), "src/brain.py", index) == {"_memory_block": [1]}
