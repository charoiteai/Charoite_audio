"""Бенч памяти по профилям демона (№629 ч. 2, P5/P7): `--profile answer|live|expand`
мерит тот же шов `brain.search` + `brain.pack`, что демон, и тот же бюджет блока;
компаньон дольше порога чата — вне сравнения; откат облака — вне сравнения."""
from __future__ import annotations

import pathlib
import sys
import types

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
    # Первый фрагмент длиннее бюджета `ANSWER` (3000 знаков, №652) — второй, с
    # фактом, не входит; у `EXPAND` тот же бюджет, но фрагменты короче.
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("ш" * 3500, "ИСКОМОЕ слово")))
    out, _, _ = mb.run_profile(brain.ANSWER, DEMO, None, [{"q": "?", "must": ["искомое"]}], stats=True, cfg={})
    assert out[0].ok is False and out[0].why == "срезано бюджетом"
    _fake_search(monkeypatch, _res(V.CONFIDENT, blocks=("ш" * 2000, "ИСКОМОЕ слово")))
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


def _rec(oks, sems=None, *, profile="answer", head="aaa", graph="5:ff", cats=None, whys=None,
         mode="stats", graph_dir="", dossiers=True):
    sems = sems or [True] * len(oks)
    out = [mb.Q(i + 1, (cats or ["fact"] * len(oks))[i], ok, sems[i], "confident",
                (whys or [""] * len(oks))[i]) for i, ok in enumerate(oks)]
    monkey_head[0] = head
    return mb.make_record(profile, mode, "", graph, CASES[:len(oks)], out, graph_dir,
                          dossiers=dossiers)


monkey_head = ["aaa"]
REAL_CODE_HEAD = mb.code_head


@pytest.fixture(autouse=True)
def _fixed_head(monkeypatch):
    """Версия кода — подменой."""
    monkeypatch.setattr(mb, "code_head", lambda: monkey_head[0])


def _alert(root):
    """Файл тревоги по профилю записи (ключ файла — `alert_key`, в тестах профиль однозначен)."""
    p = root / "logs" / "memory_bench_alert.json"
    if not p.exists():
        return None
    return {v["profile"] + ("" if v["mode"] == "stats" else "/" + v["mode"]): v
            for v in mb.json.loads(p.read_text(encoding="utf-8")).values()}


def _accept(root, rec, reason="", graph_dir=None, dossiers=True):
    """Значение оси в команде, а не в записи: команда без `--no-dossiers` — `True`."""
    mb.accept(root, rec["profile"], rec["mode"], rec["id"], reason,
              rec["graph_dir"] if graph_dir is None else graph_dir, dossiers)


def _accept_first(root, oks, **kw):
    rec = _rec(oks, **kw)
    mb.judge(root, rec)
    _accept(root, rec)
    return rec


def test_no_baseline_says_so_and_marks_the_watch_unarmed(tmp_path, capsys):
    """Без базы тревоги нет, но и молчания нет: бриф увидит «сторож не взведён» (DS I3 r1).
    Подсказка несёт id итога — принимается тот, что человек видел (Opus M1)."""
    rec = _rec([True] * 5)
    assert mb.judge(tmp_path, rec) is None
    said = capsys.readouterr().out
    assert f"итог {rec['id']} записан" in said
    assert f"--accept --run {rec['id']} --profile answer --stats" in said
    entry = _alert(tmp_path)["answer"]
    assert entry["state"] == "unmeasured" and "--accept --run " in entry["why"]


def test_two_regressions_with_same_sem_used_raise_the_alert(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    capsys.readouterr()
    alert = mb.judge(tmp_path, _rec([False, False, True, True, True], head="bbb",
                                    cats=["latest", "fact", "fact", "fact", "fact"],
                                    whys=["срезано бюджетом", "не выдано", "", "", ""]), tmp_path / "Граф")
    said = capsys.readouterr().out
    assert alert is not None and _alert(tmp_path)["answer"]["regressed"] == [
        {"n": 1, "cat": "latest", "why": "срезано бюджетом"}, {"n": 2, "cat": "fact", "why": "не выдано"}]
    assert "HEAD изменился: да; граф изменился: нет" in said
    assert "latest №1: срезано бюджетом" in said
    assert _alert(tmp_path)["answer"]["was"] == 5 and _alert(tmp_path)["answer"]["now"] == 3
    assert _alert(tmp_path)["answer"]["graph_dir"] == str(tmp_path / "Граф"), "бриф ищет тревогу своего графа"


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


def test_other_mode_does_not_lift_the_alert(tmp_path):
    """Тревога ключуется тем же ключом, что и база: ручной прогон synth без базы
    не снимает ночную тревогу stats (Sonnet I2 r1)."""
    _accept_first(tmp_path, [True] * 5)
    mb.judge(tmp_path, _rec([False, False, True, True, True]))
    mb.judge(tmp_path, _rec([True] * 5, mode="synth"))
    got = _alert(tmp_path)
    assert got["answer"]["state"] == "alert" and got["answer/synth"]["state"] == "unmeasured"


def test_run_that_compares_too_little_keeps_the_alert(tmp_path, capsys):
    """Векторизатор лёг на всех вопросах — сравнивать нечего: тревога стоит и
    помечена, а не снята как «чисто» (Sonnet I3, DS I1 r1)."""
    _accept_first(tmp_path, [True] * 5)
    mb.judge(tmp_path, _rec([False, False, True, True, True]))
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([False, False, True, True, True], sems=[False] * 5)) is None
    assert "не измерено: сравнимо 0 из 5" in capsys.readouterr().out
    entry = _alert(tmp_path)["answer"]
    assert entry["state"] == "alert" and entry["unmeasured"].startswith("сравнимо 0 из 5")
    mb.judge(tmp_path, _rec([True] * 5, sems=[True, True, False, False, False]))
    assert _alert(tmp_path)["answer"]["state"] == "alert", "2 из 5 сравнимы — меньше половины"
    mb.judge(tmp_path, _rec([True] * 5, sems=[True, True, True, False, False]))
    assert _alert(tmp_path) is None, "3 из 5 — сравнили, просадки нет: снята"


def test_baseline_is_per_graph(tmp_path, capsys):
    """База графа «А» не судит прогон графа «Б» (DS M4 r1)."""
    base_a = _accept_first(tmp_path, [True] * 5, graph_dir="/А")
    rec_b = _rec([True] * 5, graph_dir="/Б")
    mb.judge(tmp_path, rec_b)
    with pytest.raises(SystemExit, match="поле graph_dir не совпадает") as exc:
        _accept(tmp_path, rec_b, graph_dir="/В")
    assert "/Б" not in str(exc.value) and "/В" not in str(exc.value), "путь графа в текст отказа не идёт"
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([False] * 5, graph_dir="/Б")) is None
    assert "база не принята" in capsys.readouterr().out
    _accept(tmp_path, base_a)
    assert mb.judge(tmp_path, _rec([False] * 5, graph_dir="/А")) is not None


def test_question_key_covers_the_expected_facts():
    """Правка эталона под тем же вопросом — другой вопрос, а не ложное ✓→✗ (Sonnet M1 r1)."""
    base = {"q": "вопрос", "must": ["a"], "stale": ["b"]}
    assert mb.qid(base) == mb.qid(dict(base))
    assert mb.qid(base) != mb.qid({**base, "must": ["c"]})
    assert mb.qid(base) != mb.qid({**base, "stale": []})


def test_append_after_a_torn_line_keeps_the_new_record(tmp_path, capsys):
    """Оборванная запись без перевода строки не склеивается с новой (Sonnet M2 r1)."""
    _accept_first(tmp_path, [True] * 5)
    path = tmp_path / "logs" / "memory_bench_baseline.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "run", "ts"')
    mb.judge(tmp_path, _rec([True] * 5))
    kinds = [r["kind"] for r in mb.read_records(path)]
    assert kinds == ["run", "accept", "run"]


def test_fallback_model_is_not_a_baseline(tmp_path):
    rec = _rec([True] * 5, mode="synth")
    rec["model"] = "fallback:?"
    mb.judge(tmp_path, rec)
    with pytest.raises(SystemExit, match="откат модели"):
        _accept(tmp_path, rec)


def test_head_unknown_or_dirty_is_not_unchanged(monkeypatch):
    """«?» с «?» — не «HEAD не менялся»; правка без коммита видна (Sonnet M3 r1)."""
    assert mb.head_changed({"head": "?"}, {"head": "?"}) is None
    assert mb.head_changed({"head": "abc"}, {"head": "abc+dirty"}) is True
    assert mb.head_changed({"head": "abc"}, {"head": "abc"}) is False
    import subprocess
    answers = {"rev-parse": "abc123\n", "status": " M src/brain.py\n"}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: types.SimpleNamespace(
        returncode=0, stdout=answers[argv[1]]))
    assert REAL_CODE_HEAD() == "abc123+dirty"
    answers["status"] = ""
    assert REAL_CODE_HEAD() == "abc123"


def test_judge_holds_the_bench_lock(tmp_path, monkeypatch):
    """Чтение и запись журнала и тревоги — под замком: ночной и ручной прогоны
    не теряют ключи друг друга (DS I2 r1)."""
    seen = []
    real = mb.update_alert

    def probe(path, key, entry):
        lock = mb.log_path(tmp_path, "memory_bench_baseline").with_suffix(".lock")
        with lock.open("a") as f:
            seen.append(mb.file_locks.held_by_someone(f))
        real(path, key, entry)

    monkeypatch.setattr(mb, "update_alert", probe)
    mb.judge(tmp_path, _rec([True] * 5))
    assert seen == [True]
    assert not list((tmp_path / "logs").glob("*.tmp")), "временный файл тревоги не остаётся"


def test_baseline_is_per_profile_and_mode(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([False] * 5, profile="live")) is None
    assert "база не принята" in capsys.readouterr().out
    assert mb.judge(tmp_path, _rec([False] * 5, mode="synth")) is None
    assert "база не принята" in capsys.readouterr().out


def test_main_dispatches_accept(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(mb, "accept", lambda *a, **k: seen.append(a))
    monkeypatch.setattr(mb, "_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["memory_bench.py", "--accept", "--run", "r1", "--profile", "answer", "--stats", "--demo"])
    mb.main()
    assert seen and seen[0][1:4] == ("answer", "stats", "r1")


def test_changed_question_is_not_compared(tmp_path):
    _accept_first(tmp_path, [True] * 5)
    rec = _rec([False, False, True, True, True])
    for q in rec["questions"][:2]:
        q["id"] = "другой"
    assert mb.judge(tmp_path, rec) is None


def test_accept_takes_the_named_run_and_worse_needs_a_reason(tmp_path, capsys):
    with pytest.raises(SystemExit, match="в журнале нет"):
        mb.accept(tmp_path, "answer", "stats", "нет-такого", "")
    _accept_first(tmp_path, [True] * 5)
    worse = _rec([True, True, True, True, False])
    mb.judge(tmp_path, worse)
    with pytest.raises(SystemExit, match="--reason"):
        _accept(tmp_path, worse)
    _accept(tmp_path, worse, reason="вопрос 5 устарел")
    capsys.readouterr()
    mb.judge(tmp_path, _rec([True, True, True, True, False]))
    assert "было 4, стало 4" in capsys.readouterr().out
    lines = (tmp_path / "logs" / "memory_bench_baseline.jsonl").read_text(encoding="utf-8").splitlines()
    assert [mb.json.loads(x)["kind"] for x in lines] == ["run", "accept", "run", "accept", "run"]


def test_accept_takes_the_seen_run_not_the_latest(tmp_path, capsys):
    """Ночь дописала итог того же ключа после ручного прогона — принимается тот,
    чей id в команде, а не последний (Opus M1)."""
    _accept_first(tmp_path, [True] * 5)
    seen = _rec([True, True, True, False, False])
    mb.judge(tmp_path, seen)
    mb.judge(tmp_path, _rec([False, False, True, True, True]))     # ночь, тот же счёт 3/5
    _accept(tmp_path, seen, reason="принято вручную")
    accepted = [r for r in mb.read_records(tmp_path / "logs" / "memory_bench_baseline.jsonl")
                if r["kind"] == "accept"][-1]
    assert accepted["run"] == seen["id"]
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([True, True, True, False, False])) is None
    assert "✓→✗ 0" in capsys.readouterr().out


def test_accept_refuses_a_run_of_another_key(tmp_path):
    """Профиль, режим, граф итога — те, что в команде; иначе отказ с именем поля."""
    rec = _rec([True] * 5, graph_dir="/А")
    mb.judge(tmp_path, rec)
    for args, field in (((rec["id"], "live", "stats"), "profile"), ((rec["id"], "answer", "synth"), "mode")):
        with pytest.raises(SystemExit, match=f"поле {field} не совпадает"):
            mb.accept(tmp_path, args[1], args[2], args[0], "", "/А")
    with pytest.raises(SystemExit, match="поле graph_dir не совпадает"):
        mb.accept(tmp_path, "answer", "stats", rec["id"], "", "/Б")
    assert not [r for r in mb.read_records(tmp_path / "logs" / "memory_bench_baseline.jsonl")
                if r["kind"] == "accept"]


def test_run_key_covers_the_dossiers_axis():
    """Ось досье — часть ключа сравнения: запись без поля читается как «с досье»,
    `False` — другой ключ."""
    base = {"profile": "answer", "mode": "stats", "graph_dir": ""}
    assert mb.run_key(base) == mb.run_key({**base, "dossiers": True}), "старая запись — «с досье»"
    assert mb.run_key({**base, "seed": "0"}) == mb.run_key(base), "seed старой записи ключа не меняет (№631)"
    assert "seed" not in _rec([True]), "seed в запись не пишется (№631)"
    assert mb.run_key({**base, "dossiers": False}) != mb.run_key(base)


def test_alert_key_literals_of_a_base_record():
    """Строки ключа выписаны руками: запись формата базы (без поля) и её значения —
    те же ключи, что у базы 282a528f; `False` добавляет элемент с именем оси."""
    rec = _rec([True] * 5, graph_dir="/g")
    del rec["dossiers"]
    assert mb.alert_key(rec) == "answer|stats||/g"
    assert mb.alert_key({**rec, "dossiers": True}) == "answer|stats||/g"
    assert mb.alert_key({**rec, "dossiers": False}) == "answer|stats||/g|dossiers=False"


def test_accept_takes_a_base_format_record(tmp_path):
    """Итог без поля досье — формата базы; команда без `--no-dossiers` его принимает
    (запись читается «с досье»), а с `False` — отказ по тому же полю."""
    rec = _rec([True] * 5, graph_dir="/g")
    del rec["dossiers"]
    mb.judge(tmp_path, rec)
    with pytest.raises(SystemExit, match="поле dossiers не совпадает"):
        mb.accept(tmp_path, "answer", "stats", rec["id"], "", "/g", False)
    mb.accept(tmp_path, "answer", "stats", rec["id"], "", "/g", True)
    records = mb.read_records(tmp_path / "logs" / "memory_bench_baseline.jsonl")
    assert any(r.get("kind") == "accept" for r in records)
    assert mb.accepted_base(records, mb.run_key(rec))["id"] == rec["id"]


def _base_alert(tmp_path, key="answer|stats|0||"):
    """Тревога старого формата (до №631): третье поле ключа — слот seed."""
    path = tmp_path / "logs" / "memory_bench_alert.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": "2026-01-01T00:00:00", "run": "old000000001", "profile": "answer",
             "mode": "stats", "graph_dir": "", "state": "alert", "base_ts": "2026-01-01T00:00:00",
             "was": 5, "now": 3, "regressed": [], "head_changed": False, "graph_changed": False,
             "sem_diff": 0, "dirty": False}
    path.write_text(mb.json.dumps({key: entry}, ensure_ascii=False), encoding="utf-8")
    return path


def _alert_keys(path):
    """Ключи файла тревоги; файла нет (снят последний ключ) — пусто."""
    return mb.json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def test_base_format_alert_is_lifted_by_a_clean_judge(tmp_path):
    """Тревога старого формата лежит под ключом со слотом seed: прогон без просадки
    снимает её, а не оставляет висеть в брифе."""
    _accept_first(tmp_path, [True] * 5)
    path = _base_alert(tmp_path)
    assert mb.judge(tmp_path, _rec([True] * 5)) is None
    assert _alert_keys(path) == {}


def test_base_format_alert_is_lifted_by_accept(tmp_path):
    """`--accept` итога снимает и тревогу старого ключа со слотом seed."""
    rec = _rec([True] * 5)
    del rec["dossiers"]
    mb.append_record(tmp_path / "logs" / "memory_bench_baseline.jsonl", rec)
    path = _base_alert(tmp_path)
    mb.accept(tmp_path, "answer", "stats", rec["id"], "", "")
    assert _alert_keys(path) == {}


@pytest.mark.parametrize("seed", ["0", "", "random"])
def test_every_legacy_seed_slot_is_lifted(tmp_path, seed):
    _accept_first(tmp_path, [True] * 5)
    path = _base_alert(tmp_path, key=f"answer|stats|{seed}||")
    assert mb.judge(tmp_path, _rec([True] * 5)) is None
    assert _alert_keys(path) == {}


def test_regression_over_legacy_alert_leaves_one_new_key(tmp_path):
    _accept_first(tmp_path, [True] * 5)
    path = _base_alert(tmp_path)
    assert mb.judge(tmp_path, _rec([False, False, True, True, True])) is not None
    assert list(_alert_keys(path)) == ["answer|stats||"]


def test_unmeasured_over_legacy_alert_keeps_alert_and_migrates_key(tmp_path):
    path = _base_alert(tmp_path)
    mb.update_alert(path, "answer|stats||", {"state": "unmeasured", "why": "нет базы"})
    data = _alert_keys(path)
    assert list(data) == ["answer|stats||"]
    assert data["answer|stats||"]["state"] == "alert"
    assert data["answer|stats||"]["unmeasured"] == "нет базы"
    assert data["answer|stats||"]["run"] == "old000000001"


def _mixed_alert(tmp_path, cells):
    """Файл тревоги со смешанными ключами: `cells` — [(ключ, состояние, ts, run)]."""
    path = tmp_path / "logs" / "memory_bench_alert.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    for key, state, ts, run in cells:
        data[key] = {"ts": ts, "run": run, "profile": "answer", "mode": "stats",
                     "graph_dir": "", "state": state, "was": 5, "now": 3}
        if state == "unmeasured":
            data[key]["why"] = "старая причина"
    path.write_text(mb.json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


NEW, OLD = "answer|stats||", "answer|stats|0||"
T1, T8 = "2026-10-01T00:00:00", "2026-10-08T00:00:00"


@pytest.mark.parametrize("new_cell,old_cell,write,expect", [
    (("unmeasured", T8, "runN"), ("alert", T1, "runO"), "unmeasured", ("alert", "runO", "нет базы")),
    (("alert", T1, "runN"), ("unmeasured", T8, "runO"), "unmeasured", ("alert", "runN", "нет базы")),
    (("unmeasured", T1, "runN"), ("unmeasured", T8, "runO"), "unmeasured", ("unmeasured", None, None)),
    (("unmeasured", T8, "runN"), ("alert", T1, "runO"), None, None),
    (("unmeasured", T8, "runN"), ("alert", T1, "runO"), "alert", ("alert", "runW", None)),
    (("alert", T1, "runA"), ("alert", T8, "runB"), "unmeasured", ("alert", "runB", "нет базы")),
    (("alert", T8, "runA"), ("alert", T1, "runB"), "unmeasured", ("alert", "runA", "нет базы")),
])
def test_mixed_alert_file_keeps_one_best_entry(tmp_path, new_cell, old_cell, write, expect):
    path = _mixed_alert(tmp_path, [(NEW, new_cell[0], new_cell[1], new_cell[2]),
                                   (OLD, old_cell[0], old_cell[1], old_cell[2])])
    if write is None:
        entry = None
    elif write == "alert":
        entry = {"state": "alert", "ts": "2026-10-09T00:00:00", "run": "runW"}
    else:
        entry = {"state": "unmeasured", "why": "нет базы", "ts": "2026-10-09T00:00:00",
                 "run": "runW"}
    mb.update_alert(path, NEW, entry)
    data = _alert_keys(path)
    if expect is None:
        assert data == {}
        return
    assert list(data) == [NEW]
    state, run, unmeasured = expect
    assert data[NEW]["state"] == state
    if run is not None:
        assert data[NEW]["run"] == run
    if state == "alert":
        assert data[NEW].get("unmeasured") == unmeasured


def _legacy(key):
    """Ключ старого формата (до №631): слот seed третьим полем."""
    a, b, rest = key.split("|", 2)
    return f"{a}|{b}|0|{rest}"


def test_legacy_key_with_dossiers_tail_is_migrated(tmp_path):
    key = mb.alert_key(_rec([True] * 5, dossiers=False))
    path = _base_alert(tmp_path, key=_legacy(key))
    mb.update_alert(path, key, {"state": "unmeasured", "why": "нет базы"})
    data = _alert_keys(path)
    assert list(data) == [key]
    assert data[key]["state"] == "alert"


def test_neighbour_legacy_keys_of_other_graph_and_profile_are_untouched(tmp_path):
    key = mb.alert_key(_rec([True] * 5, graph_dir="/g"))
    other = mb.alert_key(_rec([True] * 5, graph_dir="/h"))
    live = mb.alert_key(_rec([True] * 5, profile="live", graph_dir="/g"))
    path = _base_alert(tmp_path, key=_legacy(other))
    data = _alert_keys(path)
    data[_legacy(live)] = dict(data[_legacy(other)])
    path.write_text(mb.json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mb.update_alert(path, key, {"state": "unmeasured", "why": "нет базы"})
    after = _alert_keys(path)
    assert set(after) == {_legacy(other), _legacy(live), key}


@pytest.mark.parametrize("entry", [None, {"state": "unmeasured", "why": "нет базы"}])
def test_non_string_state_in_file_does_not_crash_update_alert(tmp_path, entry):
    """Ручная правка файла (state списком) не роняет запись и снятие тревоги."""
    path = _mixed_alert(tmp_path, [(NEW, ["alert"], T8, "runN"), (OLD, {"x": 1}, T1, "runO")])
    mb.update_alert(path, NEW, entry)
    data = _alert_keys(path)
    assert OLD not in data
    assert (NEW in data) == (entry is not None)


def test_accept_keys_and_checks_the_dossiers_axis(tmp_path, capsys):
    """Принятие сверяет `dossiers` записи с командой; запись `accept` хранит поле,
    иначе `accepted_base` не найдёт её по ключу. База «с досье» не трогается."""
    on = _accept_first(tmp_path, [True] * 5)
    off = _rec([True] * 5, dossiers=False)
    mb.judge(tmp_path, off)
    capsys.readouterr()
    with pytest.raises(SystemExit, match="поле dossiers не совпадает"):
        _accept(tmp_path, off, dossiers=True)       # команда без --no-dossiers
    _accept(tmp_path, off, dossiers=False)
    records = mb.read_records(tmp_path / "logs" / "memory_bench_baseline.jsonl")
    assert mb.accepted_base(records, mb.run_key(off))["id"] == off["id"]
    assert mb.accepted_base(records, mb.run_key(on))["id"] == on["id"], "база «с досье» не тронута"
    acc = next(r for r in records if r["kind"] == "accept" and r["dossiers"] is False)
    assert acc["run"] == off["id"]


def test_hint_and_stamp_carry_the_dossiers_axis(tmp_path, capsys):
    rec = _rec([True] * 5, dossiers=False)
    mb.judge(tmp_path, rec)
    said = capsys.readouterr().out
    assert f"--accept --run {rec['id']} --profile answer --stats --no-dossiers" in said
    assert _alert(tmp_path)["answer"]["dossiers"] is False


def test_no_dossiers_only_for_daemon_profiles(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["memory_bench.py", "--profile", "raw", "--no-dossiers"])
    with pytest.raises(SystemExit):
        mb.main()
    assert "профилями демона" in capsys.readouterr().err


def test_no_dossiers_reaches_run_profile_and_record(monkeypatch, tmp_path):
    """`--no-dossiers` — профиль прогона без оси (`_replace`), и запись несёт `False`;
    ручной путь `_accept --no-dossiers` потом найдёт её по ключу. Через `live`:
    у `answer` ось выключена самим профилем, и тест там был бы пустым (№641)."""
    monkeypatch.setattr(mb, "_root", lambda: tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("sufler: {}\n", encoding="utf-8")
    (tmp_path / "config" / "memory_bench.yaml").write_text("- q: 'вопрос'\n", encoding="utf-8")
    monkeypatch.setattr(mb.graphs, "graph_dir", lambda cfg: tmp_path / "Граф")
    monkeypatch.setattr(mb, "build_embedder", lambda cfg: None)
    monkeypatch.setattr(mb, "resolve_lang", lambda *a, **k: "ru")
    seen: dict = {}
    monkeypatch.setattr(mb, "run_profile",
                        lambda profile, *a, **k: (seen.update(p=profile), [], "", "")[1:])
    monkeypatch.setattr(mb, "judge", lambda root, rec, graph=None: seen.update(rec=rec))
    monkeypatch.setattr(sys, "argv", ["memory_bench.py", "--profile", "live", "--stats",
                                      "--record", "--no-dossiers"])
    mb.main()
    assert seen["p"].dossiers is False and seen["rec"]["dossiers"] is False


def test_accept_lifts_the_alert_of_its_key(tmp_path, capsys):
    """Принятие снимает тревогу своего ключа: тревога не стареет, а ключ, который
    больше не гоняют, иначе висел бы вечно. Чужой ключ не трогается."""
    _accept_first(tmp_path, [True] * 5)
    _accept_first(tmp_path, [True] * 5, profile="live")
    low = _rec([False, False, True, True, True])
    mb.judge(tmp_path, low)
    mb.judge(tmp_path, _rec([False, False, True, True, True], profile="live"))
    assert set(_alert(tmp_path)) == {"answer", "live"}
    capsys.readouterr()
    _accept(tmp_path, low, reason="новая норма")
    assert "тревога ключа снята" in capsys.readouterr().out
    assert set(_alert(tmp_path)) == {"live"}


def test_alert_carries_the_run_that_raised_it(tmp_path):
    """Тревога несёт id итога, который её поднял; незамеренный прогон его не перетирает."""
    _accept_first(tmp_path, [True] * 5)
    low = _rec([False, False, True, True, True])
    mb.judge(tmp_path, low)
    assert _alert(tmp_path)["answer"]["run"] == low["id"]
    mb.judge(tmp_path, _rec([True] * 5, sems=[False] * 5))
    raised = _alert(tmp_path)["answer"]["ts"]
    later = _rec([True] * 5, sems=[False] * 5)
    later["ts"] = "2099-01-01T00:00:00"
    mb.judge(tmp_path, later)
    entry = _alert(tmp_path)["answer"]
    assert entry["state"] == "alert" and entry["unmeasured"] and entry["run"] == low["id"]
    assert entry["ts"] == raised, "ts — время последнего замера, несравнимый прогон его не двигает"


# ------------------------------------------------- грязный прогон против базы на другом коде (Opus M2)

def test_dirty_run_without_regression_does_not_lift_the_alert(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    mb.judge(tmp_path, _rec([False, False, True, True, True], head="xxx"))
    capsys.readouterr()
    assert mb.judge(tmp_path, _rec([True] * 5, head="xxx+dirty")) is None
    said = capsys.readouterr().out
    assert "прогон на незакоммиченном коде против базы на другом коде (xxx+dirty против aaa)" in said
    assert "тревога не снимается" in said
    entry = _alert(tmp_path)["answer"]
    assert entry["state"] == "alert" and entry["unmeasured"].startswith("прогон на незакоммиченном коде")
    assert "--accept --run " in entry["unmeasured"], "бриф говорит, чем взвести сторож"


def test_dirty_run_with_regression_against_other_code_raises_the_alert(tmp_path, capsys):
    """Грязный прогон тревогу поднять может — измерено то, что работает на машине; пометка
    говорит, что причину искать не в коммитах."""
    _accept_first(tmp_path, [True] * 5)
    capsys.readouterr()
    alert = mb.judge(tmp_path, _rec([False, False, True, True, True], head="aaa+dirty"))
    assert alert is not None and "прогон на незакоммиченном коде" in capsys.readouterr().out
    entry = _alert(tmp_path)["answer"]
    assert entry["state"] == "alert" and entry["dirty"] is True


def test_dirty_run_on_the_base_code_is_judged(tmp_path):
    """База принята на той же грязной установке — обычный суд: тупика после обновления нет."""
    _accept_first(tmp_path, [True] * 5, head="aaa+dirty")
    assert mb.judge(tmp_path, _rec([False, False, True, True, True], head="aaa+dirty")) is not None
    mb.judge(tmp_path, _rec([True] * 5, head="aaa+dirty"))
    assert _alert(tmp_path) is None


def test_clean_run_on_a_new_head_lifts_the_alert(tmp_path):
    """Коммит исправления снимает тревогу: снимать только «на том же head» значило бы
    никогда (сторож против правила «тот же head» из вердикта Opus)."""
    _accept_first(tmp_path, [True] * 5)
    mb.judge(tmp_path, _rec([False, False, True, True, True], head="bbb"))
    assert _alert(tmp_path)["answer"]["state"] == "alert"
    mb.judge(tmp_path, _rec([True] * 5, head="ccc"))
    assert _alert(tmp_path) is None


def test_broken_line_is_skipped_not_fatal(tmp_path, capsys):
    _accept_first(tmp_path, [True] * 5)
    path = tmp_path / "logs" / "memory_bench_baseline.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "run", "ts"\n')
    mb.judge(tmp_path, _rec([True] * 5))
    assert "битая строка" in capsys.readouterr().out


def test_record_flags_are_validated(monkeypatch, capsys):
    for argv in (["--record"], ["--profile", "companion", "--record"],
                 ["--profile", "answer", "--record", "--limit", "3"], ["--profile", "answer", "--reason", "x"],
                 ["--profile", "answer", "--accept"], ["--profile", "answer", "--run", "abc"]):
        monkeypatch.setattr(sys, "argv", ["memory_bench.py", *argv])
        with pytest.raises(SystemExit):
            mb.main()
        assert "usage" in capsys.readouterr().err
