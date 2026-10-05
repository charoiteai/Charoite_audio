"""Бенч памяти по профилям демона (№629 ч. 2, P5/P7): `--profile answer|live|expand`
мерит тот же шов `brain.search` + `brain.pack`, что демон, и тот же бюджет блока;
компаньон дольше порога чата — вне сравнения; откат облака — вне сравнения."""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import brain  # noqa: E402
import memory_bench as mb  # noqa: E402
from charoite_graph import graph_search  # noqa: E402

V = graph_search.Verdict
DEMO = ROOT / "demo" / "graph"


def _res(status, blocks=(), **kw):
    return graph_search.Result(list(blocks), len(blocks), status=status, **kw)


@pytest.fixture
def no_bypass(monkeypatch):
    """Бенч профиля не ходит в свой поиск режима raw: только через фасад `brain`."""
    def boom(*a, **kw):
        raise AssertionError("бенч профиля искал мимо brain.search")
    monkeypatch.setattr(mb, "search", boom)
    monkeypatch.setattr(mb, "search_legacy", boom)
    monkeypatch.setattr(mb, "search_brain", boom)


def test_stats_goes_only_through_brain_search(monkeypatch, no_bypass):
    """Каждый вопрос эталона — один вызов `brain.search` с профилем; плюс прогрев."""
    calls = []
    real = brain.search

    def spy(profile, query, **kw):
        calls.append((profile.name, query))
        return real(profile, query, **kw)

    monkeypatch.setattr(brain, "search", spy)
    cases = [{"q": "кто отвечает за релиз", "must": ["Олег"], "cat": "fact"},
             {"q": "когда созвон", "must": []}]
    out, model = mb.run_profile(brain.ANSWER, DEMO, mb.build_embedder({}), cases, stats=True, cfg={})
    assert [c for c in calls if c[1] != "прогрев векторизатора"] == [("answer", c["q"]) for c in cases]
    assert len(out) == 2 and model == ""


def _fake_search(monkeypatch, result):
    monkeypatch.setattr(brain, "search", lambda profile, q, **kw: result)
    monkeypatch.setattr(mb, "warm_profile", lambda *a, **kw: None)


def test_fact_cut_by_budget_is_named_so(monkeypatch, no_bypass):
    """Факт был в выдаче, бюджет блока его отрезал — «срезано бюджетом», не «не выдано»."""
    r = _res(V.CONFIDENT, blocks=("ш" * 2500, "ИСКОМОЕ слово"))
    _fake_search(monkeypatch, r)
    out, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["искомое"]}], stats=True, cfg={})
    assert out[0].ok is False and out[0].why == "срезано бюджетом"
    out, _ = mb.run_profile(brain.EXPAND, DEMO, None, [{"q": "?", "must": ["искомое"]}], stats=True, cfg={})
    assert out[0].ok is True, "бюджет раскрытия 3000 вмещает оба фрагмента"
    out, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["нет такого"]}], stats=True, cfg={})
    assert out[0].why == "не выдано"
    _fake_search(monkeypatch, _res(V.EMPTY))
    out, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["x"]}], stats=True, cfg={})
    assert out[0].why == "поиск пуст" and out[0].status == "empty"


def test_header_words_do_not_count_as_a_fact(monkeypatch, no_bypass):
    """Слова шапки («СОВПАДЕНИЯ СЛАБЫЕ», пропущенные папки) фактом не считаются."""
    _fake_search(monkeypatch, _res(V.WEAK, blocks=("пусто",), skipped=("архив",)))
    out, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["архив"]}], stats=True, cfg={})
    assert out[0].ok is False


def test_latest_reports_positions_of_new_and_stale(monkeypatch, no_bypass):
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("было 5 млн", "стало 7 млн")))
    case = {"q": "?", "must": ["7 млн"], "stale": ["5 млн"], "cat": "latest"}
    out, _ = mb.run_profile(brain.ANSWER, DEMO, None, [case], stats=True, cfg={})
    q = out[0]
    assert q.ok and q.old_pos is not None and q.new_pos is not None and q.old_pos < q.new_pos
    assert q.sem_used is False and q.cat == "latest"


class FakeLLM:
    small = "qwen-small"

    def __init__(self, cfg, fall=False):
        self.fall, self._fell_back_local, self.seen = fall, False, []

    def effective_model(self, model):
        return f"eff:{model}"

    def stream(self, prompt, **kw):
        self.seen.append((prompt, kw))
        if self.fall:
            self._fell_back_local = True
        yield "ответ: 7 млн"


def test_synth_uses_profile_kwargs_and_effective_model(monkeypatch, no_bypass):
    llm = FakeLLM({})
    monkeypatch.setattr(mb, "LLM", lambda cfg: llm)
    monkeypatch.setenv("CHAROITE_ONE_MODEL", "1")
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("стало 7 млн",)))
    out, model = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "сколько", "must": ["7 млн"]}],
                                stats=False, cfg={})
    assert model == "eff:qwen-small" and out[0].ok and out[0].comparable
    prompt, kw = llm.seen[0]
    assert kw == {"temperature": 0.0, "model": "qwen-small", "num_predict": 220}
    assert prompt == brain.answer_prompt("сколько", brain.pack(brain.ANSWER, _res(V.CONFIDENT, blocks=("стало 7 млн",))).text, "")
    assert "CHAROITE_ONE_MODEL" not in mb.os.environ


def test_cloud_fallback_takes_the_question_out_of_comparison(monkeypatch, no_bypass):
    monkeypatch.setattr(mb, "LLM", lambda cfg: FakeLLM(cfg, fall=True))
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("стало 7 млн",)))
    out, model = mb.run_profile(brain.EXPAND, DEMO, None, [{"q": "тема", "must": ["7 млн"]}],
                                stats=False, cfg={})
    assert out[0].comparable is False and model == "fallback:?"


def test_live_never_synthesizes(monkeypatch, no_bypass):
    monkeypatch.setattr(mb, "LLM", lambda cfg: pytest.fail("live не зовёт модель"))
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("x",)))
    out, model = mb.run_profile(brain.LIVE, DEMO, None, [{"q": "?", "must": ["x"]}], stats=False, cfg={})
    assert out[0].ok and model == ""


def test_companion_over_the_chat_deadline_is_out_of_comparison(monkeypatch):
    monkeypatch.setattr(mb, "require_brain", lambda *a, **kw: None)
    hits = iter([mb.BrainHit(mb.BRAIN_ALIVE, "есть Олег", False, 1.0),
                 mb.BrainHit(mb.BRAIN_ALIVE, "есть Олег", True, mb.COMPANION_DEADLINE_S + 0.5)])
    seen = []

    def fake(graph, q, **kw):
        seen.append(kw)
        return next(hits)

    monkeypatch.setattr(mb, "search_brain", fake)
    out = mb.run_companion(DEMO, [{"q": "a", "must": ["Олег"]}, {"q": "b", "must": ["Олег"]}], demo=False)
    assert [q.comparable for q in out] == [True, False]
    assert [q.status for q in out] == ["ok", "weak"]
    assert seen[0] == {"limit": 8, "snippet": 800, "timeout": 25}


def test_percentile_is_nearest_rank():
    assert mb.percentile([3.0, 1.0, 2.0, 4.0], 0.5) == 2.0
    assert mb.percentile([3.0, 1.0, 2.0, 4.0], 0.95) == 4.0
    assert mb.percentile([], 0.5) == 0.0


def test_brain_and_legacy_flags_belong_to_raw(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["memory_bench.py", "--profile", "answer", "--brain"])
    with pytest.raises(SystemExit):
        mb.main()
    assert "raw" in capsys.readouterr().err


def test_profiles_match_brain():
    assert set(mb.PROFILE_CHOICES) == set(brain.PROFILES) | {"raw", "companion"}
