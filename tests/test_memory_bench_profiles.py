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
    out, model, _ = mb.run_profile(brain.ANSWER, DEMO, mb.build_embedder({}), cases, stats=True, cfg={})
    assert [c for c in calls if c[1] != "прогрев векторизатора"] == [("answer", c["q"]) for c in cases]
    assert len(out) == 2 and model == ""


def _fake_search(monkeypatch, result):
    monkeypatch.setattr(brain, "search", lambda profile, q, **kw: result)
    monkeypatch.setattr(mb, "warm_profile", lambda *a, **kw: None)


def test_fact_cut_by_budget_is_named_so(monkeypatch, no_bypass):
    """Факт был в выдаче, бюджет блока его отрезал — «срезано бюджетом», не «не выдано»."""
    r = _res(V.CONFIDENT, blocks=("ш" * 2500, "ИСКОМОЕ слово"))
    _fake_search(monkeypatch, r)
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["искомое"]}], stats=True, cfg={})
    assert out[0].ok is False and out[0].why == "срезано бюджетом"
    out, _, _ = mb.run_profile(brain.EXPAND, DEMO, None, [{"q": "?", "must": ["искомое"]}], stats=True, cfg={})
    assert out[0].ok is True, "бюджет раскрытия 3000 вмещает оба фрагмента"
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["нет такого"]}], stats=True, cfg={})
    assert out[0].why == "не выдано"
    _fake_search(monkeypatch, _res(V.EMPTY))
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["x"]}], stats=True, cfg={})
    assert out[0].why == "поиск пуст" and out[0].status == "empty"


def test_header_words_do_not_count_as_a_fact(monkeypatch, no_bypass):
    """Слова шапки («СОВПАДЕНИЯ СЛАБЫЕ», пропущенные папки) фактом не считаются."""
    _fake_search(monkeypatch, _res(V.WEAK, blocks=("пусто",), skipped=("архив",)))
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["архив"]}], stats=True, cfg={})
    assert out[0].ok is False


def test_latest_reports_positions_of_new_and_stale(monkeypatch, no_bypass):
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("было 5 млн", "стало 7 млн")))
    case = {"q": "?", "must": ["7 млн"], "stale": ["5 млн"], "cat": "latest"}
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [case], stats=True, cfg={})
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
    out, model, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "сколько", "must": ["7 млн"]}],
                                stats=False, cfg={})
    assert model == "eff:qwen-small" and out[0].ok and out[0].comparable
    prompt, kw = llm.seen[0]
    assert kw == {"temperature": 0.0, "model": "qwen-small", "num_predict": 220}
    assert prompt == brain.answer_prompt("сколько", brain.pack(brain.ANSWER, _res(V.CONFIDENT, blocks=("стало 7 млн",))).text, "")
    assert "CHAROITE_ONE_MODEL" not in mb.os.environ


def test_cloud_fallback_takes_the_question_out_of_comparison(monkeypatch, no_bypass):
    monkeypatch.setattr(mb, "LLM", lambda cfg: FakeLLM(cfg, fall=True))
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("стало 7 млн",)))
    out, model, _ = mb.run_profile(brain.EXPAND, DEMO, None, [{"q": "тема", "must": ["7 млн"]}],
                                stats=False, cfg={})
    assert out[0].comparable is False and model == "fallback:?"


def test_live_never_synthesizes(monkeypatch, no_bypass):
    monkeypatch.setattr(mb, "LLM", lambda cfg: pytest.fail("live не зовёт модель"))
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("x",)))
    out, model, _ = mb.run_profile(brain.LIVE, DEMO, None, [{"q": "?", "must": ["x"]}], stats=False, cfg={})
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


# ---------------------------------------------------------------- база и тревога (P6)

CASES = [{"q": f"вопрос {i}", "must": ["x"]} for i in range(5)]


def _rec(oks, sems=None, *, profile="answer", head="aaa", graph="5:ff", cats=None, whys=None):
    sems = sems or [True] * len(oks)
    out = [mb.Q(i + 1, (cats or ["fact"] * len(oks))[i], ok, sems[i], "confident",
                (whys or [""] * len(oks))[i]) for i, ok in enumerate(oks)]
    monkey_head[0] = head
    return mb.make_record(profile, "stats", "", graph, CASES[:len(oks)], out)


monkey_head = ["aaa"]


@pytest.fixture(autouse=True)
def _fixed_head(monkeypatch):
    monkeypatch.setattr(mb, "code_head", lambda: monkey_head[0])
    monkeypatch.setenv("PYTHONHASHSEED", "0")


def _alert(root):
    p = root / "logs" / "memory_bench_alert.json"
    return mb.json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _accept_first(root, oks, **kw):
    mb.judge(root, _rec(oks, **kw))
    mb.accept(root, kw.get("profile", "answer"), "stats", "")


def test_no_baseline_says_so_and_raises_nothing(tmp_path, capsys):
    assert mb.judge(tmp_path, _rec([True] * 5)) is None
    assert "база не принята" in capsys.readouterr().out
    assert _alert(tmp_path) is None


def test_two_regressions_with_same_sem_used_raise_the_alert(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    capsys.readouterr()
    alert = mb.judge(tmp_path, _rec([False, False, True, True, True], head="bbb",
                                    cats=["latest", "fact", "fact", "fact", "fact"],
                                    whys=["срезано бюджетом", "не выдано", "", "", ""]))
    said = capsys.readouterr().out
    assert alert is not None and _alert(tmp_path)["answer"]["regressed"] == [
        {"n": 1, "cat": "latest", "why": "срезано бюджетом"}, {"n": 2, "cat": "fact", "why": "не выдано"}]
    assert "HEAD изменился: да; граф изменился: нет" in said
    assert "latest №1: срезано бюджетом" in said
    assert _alert(tmp_path)["answer"]["was"] == 5 and _alert(tmp_path)["answer"]["now"] == 3


def test_one_regression_is_noise(tmp_path):
    _accept_first(tmp_path, [True] * 5)
    assert mb.judge(tmp_path, _rec([False, True, True, True, True])) is None
    assert _alert(tmp_path) is None


def test_sem_used_change_is_not_a_regression_but_is_reported(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([False, False, True, True, True],
                                   sems=[False, False, True, True, True])) is None
    assert "sem_used ≠ базы на 2 из 5" in capsys.readouterr().out
    assert _alert(tmp_path) is None


def test_clean_run_lifts_the_alert_of_its_profile_only(tmp_path):
    _accept_first(tmp_path, [True] * 5)
    _accept_first(tmp_path, [True] * 5, profile="live")
    mb.judge(tmp_path, _rec([False, False, True, True, True]))
    mb.judge(tmp_path, _rec([False, False, True, True, True], profile="live"))
    assert set(_alert(tmp_path)) == {"answer", "live"}
    mb.judge(tmp_path, _rec([True] * 5))
    assert set(_alert(tmp_path)) == {"live"}
    mb.judge(tmp_path, _rec([True] * 5, profile="live"))
    assert _alert(tmp_path) is None


def test_baseline_is_per_profile_mode_and_seed(tmp_path, monkeypatch, capsys):
    _accept_first(tmp_path, [True] * 5)
    monkeypatch.setenv("PYTHONHASHSEED", "7")
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([False] * 5)) is None
    assert "база не принята" in capsys.readouterr().out


def test_changed_question_is_not_compared(tmp_path):
    _accept_first(tmp_path, [True] * 5)
    rec = _rec([False, False, True, True, True])
    for q in rec["questions"][:2]:
        q["id"] = "другой"
    assert mb.judge(tmp_path, rec) is None


def test_accept_takes_the_last_run_and_worse_needs_a_reason(tmp_path, capsys):
    with pytest.raises(SystemExit, match="нечего принимать"):
        mb.accept(tmp_path, "answer", "stats", "")
    _accept_first(tmp_path, [True] * 5)
    mb.judge(tmp_path, _rec([True, True, True, True, False]))
    with pytest.raises(SystemExit, match="--reason"):
        mb.accept(tmp_path, "answer", "stats", "")
    mb.accept(tmp_path, "answer", "stats", "вопрос 5 устарел")
    capsys.readouterr()
    mb.judge(tmp_path, _rec([True, True, True, True, False]))
    assert "было 4, стало 4" in capsys.readouterr().out
    lines = (tmp_path / "logs" / "memory_bench_baseline.jsonl").read_text(encoding="utf-8").splitlines()
    assert [mb.json.loads(x)["kind"] for x in lines] == ["run", "accept", "run", "accept", "run"]


def test_broken_line_is_skipped_not_fatal(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    path = tmp_path / "logs" / "memory_bench_baseline.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "run", "ts"\n')
    mb.judge(tmp_path, _rec([True] * 5))
    assert "битая строка" in capsys.readouterr().out


def test_record_flags_are_validated(monkeypatch, capsys):
    for argv in (["--record"], ["--profile", "companion", "--record"],
                 ["--profile", "answer", "--record", "--limit", "3"], ["--profile", "answer", "--reason", "x"]):
        monkeypatch.setattr(sys, "argv", ["memory_bench.py", *argv])
        with pytest.raises(SystemExit):
            mb.main()
    capsys.readouterr()


def test_hash_seed_is_pinned_by_reexec(monkeypatch):
    seen = {}
    monkeypatch.setattr(mb.os, "execve", lambda exe, argv, env: seen.update(env=env, argv=argv))
    monkeypatch.delenv("PYTHONHASHSEED")
    mb.pin_hash_seed("raw")
    assert seen == {}
    mb.pin_hash_seed("answer")
    assert seen["env"]["PYTHONHASHSEED"] == "0" and seen["argv"][1].endswith("memory_bench.py")
    seen.clear()
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    mb.pin_hash_seed("answer")
    assert seen == {}
