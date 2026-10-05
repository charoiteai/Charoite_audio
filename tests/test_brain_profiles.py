"""Профили потребителей памяти в `brain.py` (№629 ч. 2, P1): запись `Packed`,
шов `search` + `pack`, kwargs модели по синтезу профиля и сторож обхода фасада."""
from __future__ import annotations

import ast
import pathlib
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
                    "dossiers": True, "graph": pathlib.Path("/g"), "model": "bge"}


def test_search_carries_the_dossiers_axis_to_the_engine(monkeypatch):
    """Ось профиля — аргумент вызова: `ANSWER._replace(dossiers=False)` доезжает до
    `mem.search` как `dossiers=False`, а профили по умолчанию идут с `True`."""
    seen = {}

    class GS:
        def search(self, query, **kw):
            seen.update(kw, query=query)
            return _res(V.CONFIDENT, blocks=("x",))

    monkeypatch.setattr(brain, "shared", lambda *a, **kw: GS())
    monkeypatch.setattr(brain.llm, "embedder", lambda cfg: types.SimpleNamespace(model="bge"))
    brain.search(brain.ANSWER._replace(dossiers=False), "тема", cfg={})
    assert seen["dossiers"] is False
    seen.clear()
    brain.search(brain.LIVE, "тема", cfg={})
    assert seen["dossiers"] is True
    assert all(p.dossiers is True for p in (brain.ANSWER, brain.EXPAND, brain.LIVE))


def test_stream_kwargs_pass_only_what_the_profile_sets():
    llm = types.SimpleNamespace(small="s", model="m")
    assert brain.stream_kwargs(brain.ANSWER, llm) == {"model": "s", "num_predict": 220}
    assert brain.stream_kwargs(brain.EXPAND, llm) == {"model": "s", "system": brain.EXPAND_SYSTEM}
    with pytest.raises(ValueError):
        brain.stream_kwargs(brain.LIVE, llm)
    assert brain.ANSWER.synth.abstain in brain.answer_prompt("в", "", "хвост")


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
