"""Ночная ревизия ядер обязана укладываться в ночь.

13.08 прогон стартовал в 04:18 и в 09:00 всё ещё шёл: граф дорос до 293
файлов ядер, полный проход квадратичен, а каждую кандидатскую пару судит
NLI в один поток. Досье, дедуп и утренний бриф стоят в nightly.sh ПОСЛЕ
ревизии — человек в девять утра читал вчерашний _Сегодня.md.

Инкрементальный режим (--since-last) судит только ядра, изменившиеся с
прошлого прогона, — revise(only_names=...) это умеет с самого начала.
Тесты закрепляют три свойства, на которых режим держится:

1) фокус собирается по времени изменения файла, служебные `_`-файлы в него
   не попадают;
2) отметка НЕ двигается после несостоявшегося прогона (нет NLI-модели,
   лежит Ollama) — иначе ядра, которые ревизия должна была разобрать,
   выпадают из фокуса навсегда;
3) отметка берётся на начале прогона, а не в конце: встреча могла обновить
   ядро, пока шла ревизия, и метка «конец» потеряла бы эту правку.

NLI и Ollama здесь не поднимаются — revise подменяется целиком.
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402
import tier3  # noqa: E402
import tier3_cores  # noqa: E402

def fake_embedder(vectors=None):
    """Векторизатор-подделка: заданные векторы или по одному на ядро."""
    from model_seam import Embedder

    def run(texts, timeout):
        return vectors if vectors is not None else [[1.0, 0.0] for _ in texts]

    return Embedder(run, "test-fake")


def fake_judge(p=0.0, ready=True, refused="", entail=None):
    """Судья-подделка. По умолчанию доступен, готов и ничего не подтверждает.

    Отдельный `entail` — чтобы проверять отказ судьи посреди прогона: он
    бросает, а не возвращает ноль, и пара обязана вернуться в фокус.
    """
    from model_seam import Judge

    return Judge(lambda: ready, entail or (lambda a, b: p), refused)


EMPTY = {"dups": [], "nests": [], "border": [], "log": [],
         "pending_merges": [], "skipped": [], "ran": True}


def _open() -> bool:
    """Ночное окно открыто: гейт вызывающего, который модулю графа передают параметром."""
    return True


def _graph(tmp_path: pathlib.Path, *names: str) -> pathlib.Path:
    graph = tmp_path / "Граф"
    (graph / "Ядра").mkdir(parents=True)
    for n in names:
        (graph / "Ядра" / f"{n}.md").write_text("## Статус\nживо\n", encoding="utf-8")
    return graph


def test_the_revision_asks_the_seam_and_does_not_name_the_model(monkeypatch):
    """Ревизия не знает ни имени модели, ни транспорта — только шов.

    Пришпиленное «bge-m3» заставляло её считать векторы моделью, которой на
    машине может не быть; потом имя переехало в канон, а теперь и канона она не
    видит: чем считать — решение того, кто собирал пару (кусок 2б по №321).
    """
    seen = {}

    def run(texts, timeout):
        seen["texts"], seen["timeout"] = texts, timeout
        return [[1.0, 0.0] for _ in texts]

    from model_seam import Embedder
    tier3._embed_all([{"repr": "ядро"}], Embedder(run, "чем-угодно"))
    assert seen["texts"] == ["ядро"] and seen["timeout"] == 120
    assert "llm" not in dir(tier3) and "nli" not in dir(tier3), \
        "ревизия больше не импортирует слой моделей"

def test_changed_since_takes_only_fresh_cores(tmp_path):
    graph = _graph(tmp_path, "Старое", "Свежее", "_служебное")
    old = graph / "Ядра" / "Старое.md"
    import os
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    fresh = tier3.changed_since(graph / "Ядра", time.time() - 60)

    assert fresh == ["Свежее"], f"в фокус попало лишнее: {fresh}"


def test_missing_folder_is_not_a_crash(tmp_path):
    assert tier3.changed_since(tmp_path / "нет-такой-папки", 0) == []


def test_incremental_run_judges_only_fresh_cores(tmp_path, monkeypatch, capsys):
    graph = _graph(tmp_path, "Старое", "Свежее")
    import os
    os.utime(graph / "Ядра" / "Старое.md", (time.time() - 3600,) * 2)
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    (tmp_path / "stamps.json").write_text(
        json.dumps({str(graph): time.time() - 60}), encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tier3, "revise",
                        lambda g, only_names=None, **kw: seen.update(
                            only=only_names) or dict(EMPTY))

    tier3_cores.run(graph, apply=False, mark=True, since_last=True)

    assert seen["only"] == ["Свежее"], seen
    assert "инкремент" in capsys.readouterr().out


def test_first_run_without_stamp_is_full(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "Первое", "Второе")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "нет.json": p)
    seen = {}
    monkeypatch.setattr(tier3, "revise",
                        lambda g, only_names=None, **kw: seen.update(
                            only=only_names) or dict(EMPTY))

    tier3_cores.run(graph, apply=False, mark=True, since_last=True)

    assert seen["only"] is None, "без отметки прогон обязан быть полным"


def test_stamp_does_not_move_after_a_run_that_did_not_happen(tmp_path, monkeypatch):
    """Нет NLI-модели или лежит Ollama — revise возвращает пустой результат.

    Он неотличим от «чисто» по спискам находок, поэтому revise отдельно
    сообщает ran. Сдвинутая отметка после такого прогона тихо вычёркивает
    ядра из фокуса — ошибка, которую в логе не видно вообще.
    """
    graph = _graph(tmp_path, "Ядро")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    monkeypatch.setattr(tier3, "revise",
                        lambda g, only_names=None, **kw: dict(EMPTY, ran=False))

    tier3_cores.run(graph, apply=False, mark=True, since_last=True)

    assert not (tmp_path / "stamps.json").exists(), "отметка сдвинулась вхолостую"


def test_stamp_is_taken_before_the_run_not_after(tmp_path, monkeypatch):
    """Ревизия идёт часами; встреча за это время обновляет ядро.

    С отметкой «конец прогона» такая правка не попадёт в фокус никогда —
    её mtime окажется старше отметки.
    """
    graph = _graph(tmp_path, "Ядро")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    started = time.time()

    def slow(g, only_names=None, **kw):
        time.sleep(0.2)
        return dict(EMPTY)

    monkeypatch.setattr(tier3, "revise", slow)
    tier3_cores.run(graph, apply=False, mark=True, since_last=True)

    stamp = json.loads((tmp_path / "stamps.json").read_text())[str(graph)]
    assert stamp < started + 0.2, "отметка взята после прогона — правки встреч потеряются"


def test_revise_reports_that_it_ran(tmp_path, monkeypatch):
    """ran отличает «чисто» от «ревизия не состоялась»."""
    graph = _graph(tmp_path, "Одно", "Другое")
    assert tier3.revise(graph, embedder=fake_embedder(),
                        judge=fake_judge(refused="нет модели"), may_continue=_open)["ran"] is False


def test_the_factory_builds_a_judge_that_refuses_loudly(monkeypatch):
    """Сама фабрика, а не только контракт: нет модели — отказ, а не ноль.

    Прошлые тесты проверяли, что ревизия правильно обходится с отказавшим
    судьёй. Этот проверяет, что судья действительно отказывает: подмена
    `entail_prob` нулём в реализации осталась бы незамеченной (мутация круга
    2б).
    """
    import nli

    monkeypatch.setattr(nli, "is_available", lambda: False)
    j = nli.judge()
    assert j.refused, "дешёвая фаза посчитана при сборке и названа словами"
    assert j.ready() is False
    with pytest.raises(OSError):        # SeamTransportError — его подкласс
        j.entail("а", "б")

    # вторая ветка: файлы на месте, но сессия не собралась (битый ONNX) —
    # раньше здесь возвращался ноль, неотличимый от честного «не следует»
    monkeypatch.setattr(nli, "is_available", lambda: True)
    monkeypatch.setattr(nli, "_load", lambda: None)
    monkeypatch.setattr(nli, "_session", None)
    with pytest.raises(OSError):
        nli.judge().entail("а", "б")


def test_a_broken_seam_is_not_a_lying_ollama(tmp_path):
    """Шов не той формы обязан долететь до человека, а не стать «лежит Ollama».

    `except` вокруг эмбеддингов сужен до транспортного отказа намеренно: пока
    он ловил всё, ночник годами печатал бы «ревизия не состоялась — лежит
    Ollama» на сломанном коде (круг 2 по 2б, GLM C2).
    """
    from model_seam import Embedder

    graph = _graph(tmp_path, "Одно", "Другое")
    wrong = Embedder(lambda texts: [], "шов-без-таймаута")   # забыли параметр
    with pytest.raises(TypeError):
        tier3.revise(graph, embedder=wrong, judge=fake_judge(), may_continue=_open)


def test_a_judge_that_goes_deaf_mid_run_returns_the_pair_to_focus(tmp_path):
    """Судья отказал посреди прогона — пара не судилась, и это видно.

    Раньше `entail_prob` при пропавшей модели отдавал 0.0, неотличимый от
    честного «не следует»: прогон считался состоявшимся, отметка инкремента
    уезжала вперёд, а пара не возвращалась в фокус уже никогда (аудит 17.08 и
    круг 2 по куску 2б, обе головы независимо). Теперь отказ — исключение, и
    имена попадают в `failed_names`, которые ночь вернёт адресно.
    """
    graph = _graph(tmp_path, "Одно", "Другое")

    def deaf(a, b):
        from model_seam import SeamTransportError
        raise SeamTransportError("судья исчез посреди прогона")

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0], [1.0, 0.0]]),
                     judge=fake_judge(entail=deaf), may_continue=_open)
    assert r["failed"] == 1 and r["failed_names"] == {"Одно", "Другое"}
    assert r["dups"] == [], "ничего не слил вслепую"
    assert r["ran"] is False, \
        "ни одна пара не судилась — прогон не состоялся, и отметка не двигается"


def test_ran_is_false_when_the_judge_did_not_come(tmp_path, monkeypatch):
    """Файлы NLI на месте, но сессия не собралась (битый ONNX): entail_prob
    тихо отдаёт 0.0, суд «ничего не находит» — раньше ran=True двигал отметку
    --since-last, и свежие ядра навсегда выпадали из инкремента
    (аудит DeepSeek 17.08)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    assert tier3.revise(graph, embedder=fake_embedder(),
                        judge=fake_judge(ready=False), may_continue=_open)["ran"] is False


def test_incomplete_embeddings_do_not_crash_and_do_not_count_as_a_run(tmp_path, monkeypatch):
    """llm.embed при ошибке сервера отдаёт `[]` — раньше IndexError валил CLI
    ночи (аудит DeepSeek 17.08); теперь — «прогон не состоялся»."""
    graph = _graph(tmp_path, "Одно", "Другое")
    r = tier3.revise(graph, embedder=fake_embedder([]), judge=fake_judge(), may_continue=_open)
    assert r["ran"] is False and r["dups"] == []


def test_full_run_is_not_marked_stopped(tmp_path, monkeypatch):
    """Состоявшийся полный прогон — ran=True, stopped=False: иначе
    tier3_cores никогда не сдвинет отметку --since-last и каждая ночь
    пересуживает всё с нуля (мутационный прогон 21.08)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    # Одинаковые эмбеддинги: пара проходит префильтр, суд реально идёт по
    # циклу и спрашивает ночное окно; с ортогональными пара отсекалась до
    # цикла и stopped=False держалось инициализацией, а не прогоном
    # (ревью 22.08: Sonnet 5 и DeepSeek независимо).
    asked = []

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0], [1.0, 0.0]]),
                     judge=fake_judge(), may_continue=lambda: asked.append(1) or True)
    assert asked, "суд не дошёл до цикла пар — тест держал бы инициализацию"
    assert r["ran"] is True and r["stopped"] is False


def test_run_cut_by_the_night_ceiling_is_marked_stopped(tmp_path, monkeypatch):
    """Обрыв потолком ночи — stopped=True: недосмотренные ядра уходят в
    unjudged_names, и следующая ночь продолжает с места обрыва."""
    graph = _graph(tmp_path, "Одно", "Другое")
    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0], [1.0, 0.0]]),
                     judge=fake_judge(), may_continue=lambda: False)
    assert r["ran"] is True and r["stopped"] is True


# ── Исход значением и итог по всем графам (№358) ─────────────────────────
def test_a_graph_with_one_core_is_nothing_to_do_not_a_failure(tmp_path, monkeypatch, capsys):
    """Одно ядро — судить нечего. Раньше это был тот же ran=False, что у
    лежащей Ollama, и граф печатал «нет NLI-модели» (входной круг, Codex I4)."""
    graph = _graph(tmp_path, "Одно")
    r = tier3.revise(graph, embedder=fake_embedder(), judge=fake_judge(), may_continue=_open)
    assert r["status"] == "no_work" and r["ran"] is False

    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    monkeypatch.setattr(tier3_cores.graphs, "load_config", lambda: {})
    monkeypatch.setattr(tier3_cores.nli, "judge", lambda: fake_judge())
    monkeypatch.setattr(tier3_cores.llm, "embedder", lambda cfg, **kw: fake_embedder())
    assert tier3_cores.run(graph, apply=False, mark=True) == "no_work"
    out = capsys.readouterr().out
    assert "ревизовать нечего" in out and "не состоялась" not in out, out


@pytest.mark.parametrize("embedder, judge, expect", [
    (fake_embedder([]), fake_judge(), "эмбеддер не дал векторов"),
    (fake_embedder(), fake_judge(refused="нет файлов модели"), "судья отказал: нет файлов модели"),
    (fake_embedder(), fake_judge(ready=False), "NLI-модель не поднялась"),
])
def test_a_revision_that_did_not_happen_names_why(tmp_path, embedder, judge, expect):
    """Общая фраза «нет NLI-модели, лежит Ollama или пустые эмбеддинги» месяц
    стояла над HTTP 400 от эмбеддера: причину называет ревизия (№358)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    r = tier3.revise(graph, embedder=embedder, judge=judge, may_continue=_open)
    assert r["status"] == "unavailable" and expect in r["reason"], r["reason"]


def test_the_night_prints_the_reason_not_a_guess(tmp_path, monkeypatch, capsys):
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    monkeypatch.setattr(tier3, "revise", lambda g, only_names=None, **kw: dict(
        EMPTY, ran=False, status="unavailable", reason="эмбеддер не дал векторов на 2 ядер"))
    assert tier3_cores.run(graph, apply=False, mark=True) == "unavailable"
    out = capsys.readouterr().out
    assert "эмбеддер не дал векторов" in out and "лежит Ollama" not in out, out


def _main_over(monkeypatch, tmp_path, outcomes: dict, night_over_after: int | None = None):
    """main --all-graphs над подменёнными графами: каждый граф отдаёт свой исход."""
    graphs_ = [tmp_path / name for name in outcomes]
    calls = []
    monkeypatch.setattr(tier3_cores.graphs, "all_graphs", lambda sub: graphs_)
    monkeypatch.setattr(tier3_cores, "run",
                        lambda g, *a, **k: calls.append(g.name) or outcomes[g.name])
    monkeypatch.setattr(tier3_cores.live_gate, "night_is_over",
                        lambda: night_over_after is not None and len(calls) >= night_over_after)
    monkeypatch.setattr(sys, "argv", ["tier3_cores.py", "--all-graphs"])
    return tier3_cores.main()


def test_one_failing_graph_is_not_hidden_by_another_passing(tmp_path, monkeypatch):
    """`any(ran)` давал 0, когда малый граф проходил, а основной — нет: ночь
    месяц не видела, что ревизия основного графа не идёт (Codex C1)."""
    assert _main_over(monkeypatch, tmp_path, {"Основной": "unavailable", "Малый": "complete"}) == 2


@pytest.mark.parametrize("outcomes, over_after, code", [
    ({"А": "complete", "Б": "no_work"}, None, 0),
    ({"А": "complete", "Б": "stopped"}, None, 4),
    ({"А": "complete", "Б": "complete"}, 1, 4),     # потолок ночи оборвал очередь графов
    ({"А": "stopped", "Б": "unavailable"}, None, 2),
])
def test_exit_code_speaks_for_every_graph(tmp_path, monkeypatch, outcomes, over_after, code):
    assert _main_over(monkeypatch, tmp_path, outcomes, over_after) == code


def test_pending_names_are_kept_whole(tmp_path, monkeypatch):
    """Обрезка до 200 обещала, что остальные вернутся «как свежие», но отметка
    уже ушла вперёд — 201-е имя не возвращалось никогда (Codex I3)."""
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    names = {f"Ядро {i:03d}" for i in range(250)}
    tier3_cores._save_stamp(tmp_path / "Граф", time.time(), names)
    assert set(tier3_cores._pending(tmp_path / "Граф")) == names


# ── Очередь, которая не помещается в ночь, обязана сходиться (круг 1 по коду) ─
def _window_for(pairs_allowed: int, monkeypatch):
    """Ночное окно, которое закрывается после `pairs_allowed` судимых пар: ставится
    в ночь (tier3_cores спрашивает live_gate) и отдаётся гейтом прямому вызову revise."""
    спрошено = []

    def window(*a, **k):
        спрошено.append(1)
        return len(спрошено) <= pairs_allowed

    monkeypatch.setattr(tier3_cores.live_gate, "night_window_open", window)
    return window


def test_a_queue_longer_than_the_night_converges(tmp_path, monkeypatch):
    """При обрыве каждая ночь начинала с тех же верхних пар — хвост не судился
    никогда (Opus I2 = Sonnet I). Досуженные пары оборванной ночи не судятся
    снова, пока их ядра не менялись: каждая ночь добавляет новые пары."""
    names = ["А", "Б", "В", "Г", "Д"]
    graph = _graph(tmp_path, *names)
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    monkeypatch.setattr(tier3_cores.graphs, "load_config", lambda: {})
    monkeypatch.setattr(tier3_cores.llm, "embedder", lambda cfg, **kw: fake_embedder([[1.0, 0.0]] * 5))
    судимые: list = []

    def entail(a, b):
        судимые.append(frozenset((a, b)))
        return 0.0

    monkeypatch.setattr(tier3_cores.nli, "judge", lambda: fake_judge(entail=entail))
    исходы = []
    for ночь in range(1, 10):
        _window_for(3, monkeypatch)
        исходы.append(tier3_cores.run(graph, apply=False, mark=False, since_last=True))
        if исходы[-1] == "complete":
            break
    assert исходы[-1] == "complete", "очередь длиннее ночи не сошлась: %s" % исходы
    reprs = {c["repr"] for c in tier3.load_cores(graph / "Ядра")}
    все_пары = {frozenset((x, y)) for x in reprs for y in reprs if x != y}
    assert все_пары <= set(судимые), "за ночи досмотрены не все пары"
    assert len(исходы) <= 5, "каждая ночь обязана добавлять новые пары: %s" % исходы
    assert tier3_cores._judged(graph) == set(), "после полного досмотра память пар не снята"


def test_judged_pairs_of_a_changed_core_are_judged_again(tmp_path, monkeypatch):
    """Досуженная прошлой ночью пара, ядро которой с тех пор изменилось, судится
    снова: память пар годна только для неизменных ядер."""
    graph = _graph(tmp_path, "А", "Б", "В")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    import os
    old = time.time() - 3600
    for n in ("А", "Б", "В"):
        os.utime(graph / "Ядра" / f"{n}.md", (old, old))
    tier3_cores._save_stamp(graph, old + 60, {"В"}, {frozenset(("А", "Б")), frozenset(("А", "В"))})
    (graph / "Ядра" / "Б.md").write_text("## Статус\nизменилось\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(tier3, "revise", lambda g, only_names=None, skip_pairs=frozenset(), **kw: (
        seen.update(skip=set(skip_pairs)), dict(EMPTY))[1])
    tier3_cores.run(graph, apply=False, mark=True, since_last=True)
    assert seen["skip"] == {frozenset(("А", "В"))}, seen


def test_a_stopped_run_moves_the_stamp_and_keeps_the_debt(tmp_path, monkeypatch, capsys):
    """При обрыве отметка идёт вперёд, долг — в списке ожидания; раньше отметка
    стояла, и следующая ночь начинала с тех же пар."""
    graph = _graph(tmp_path, "А", "Б")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    monkeypatch.setattr(tier3, "revise", lambda g, only_names=None, **kw: dict(
        EMPTY, ran=True, stopped=True, status="stopped", failed_names={"А"},
        unjudged_names={"Б"}))
    assert tier3_cores.run(graph, apply=False, mark=True) == "stopped"
    assert set(tier3_cores._pending(graph)) == {"А", "Б"}
    assert str(graph) in json.loads((tmp_path / "stamps.json").read_text())
    out = capsys.readouterr().out
    assert "чисто" not in out and "до потолка ночи находок нет" in out, out


def test_a_judge_failing_every_pair_is_named_even_when_the_night_ends(tmp_path, monkeypatch):
    """Отказ судьи на всех парах важнее обрыва — но и обрыв назван в причине."""
    graph = _graph(tmp_path, "А", "Б", "В")
    window = _window_for(1, monkeypatch)

    def deaf(a, b):
        raise RuntimeError("сессия NLI умерла")

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0]] * 3), judge=fake_judge(entail=deaf),
                     may_continue=window)
    assert r["status"] == "unavailable" and "потолок ночи" in r["reason"], r["reason"]


def test_a_pair_the_judge_refused_is_not_remembered_as_judged(tmp_path, monkeypatch):
    """В память досуженных попадают только пары, по которым судья ответил:
    отказавшая пара обязана судиться следующей ночью снова."""
    graph = _graph(tmp_path, "А", "Б", "В")
    reprs = sorted(c["repr"] for c in tier3.load_cores(graph / "Ядра"))
    плохая = frozenset(reprs[:2])

    def entail(a, b):
        if frozenset((a, b)) == плохая:
            raise RuntimeError("сессия NLI моргнула")
        return 0.0

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0]] * 3), judge=fake_judge(entail=entail), may_continue=_open)
    имена = {c["repr"]: c["name"] for c in tier3.load_cores(graph / "Ядра")}
    плохая_по_именам = frozenset(имена[x] for x in плохая)
    assert r["failed"] == 1 and плохая_по_именам not in r["judged_pairs"], r["judged_pairs"]
    assert len(r["judged_pairs"]) == 2


# ── Добор по CI-мутатору #610: каждый исход и каждый путь main ───────────────
def test_a_revision_with_findings_reports_its_outcome(tmp_path, monkeypatch, capsys):
    graph = _graph(tmp_path, "А", "Б")
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    найдено = dict(EMPTY, dups=["«А» ↔? «Б» 0.90/0.90 (пометка)"])
    monkeypatch.setattr(tier3, "revise", lambda g, **kw: dict(найдено))
    assert tier3_cores.run(graph, apply=False, mark=True) == "complete"
    monkeypatch.setattr(tier3, "revise", lambda g, **kw: dict(найдено, stopped=True, unjudged_names={"Б"}))
    assert tier3_cores.run(graph, apply=False, mark=True) == "stopped"
    assert "ДУБЛИ" in capsys.readouterr().out


def test_run_without_since_last_judges_the_whole_graph(tmp_path, monkeypatch):
    """По умолчанию прогон полный: отметка есть, свежих ядер нет, а суд идёт."""
    graph = _graph(tmp_path, "А", "Б")
    import os
    old = time.time() - 3600
    for n in ("А", "Б"):
        os.utime(graph / "Ядра" / f"{n}.md", (old, old))
    monkeypatch.setattr(tier3_cores, "stamps_path", lambda p=tmp_path / "stamps.json": p)
    tier3_cores._save_stamp(graph, old + 60)
    зовы = []
    monkeypatch.setattr(tier3, "revise", lambda g, only_names=None, **kw: (зовы.append(only_names), dict(EMPTY))[1])
    tier3_cores.run(graph, apply=False, mark=True)
    assert зовы == [None], "без since_last прогон обязан быть полным: %s" % зовы


def test_single_graph_main_speaks_through_the_same_exit_code(tmp_path, monkeypatch):
    graph = _graph(tmp_path, "А", "Б")
    monkeypatch.setattr(sys, "argv", ["tier3_cores.py", "--graph", str(graph)])
    for исход, код in (("complete", 0), ("no_work", 0), ("unavailable", 2), ("stopped", 4)):
        monkeypatch.setattr(tier3_cores, "run", lambda g, *a, _и=исход, **k: _и)
        assert tier3_cores.main() == код, исход
    assert tier3_cores.exit_code(["complete"]) == 0, "без обрыва очереди исход полного прогона — 0"


def test_no_folder_and_a_refusing_transport_are_named(tmp_path):
    from model_seam import Embedder, SeamTransportError
    r = tier3.revise(tmp_path / "Нет графа", embedder=fake_embedder(), judge=fake_judge(), may_continue=_open)
    assert r["status"] == "no_work" and "Ядра" in r["reason"]

    def refuse(texts, timeout):
        raise SeamTransportError("соединение отклонено")

    graph = _graph(tmp_path, "А", "Б")
    r = tier3.revise(graph, embedder=Embedder(refuse, "test"), judge=fake_judge(), may_continue=_open)
    assert r["status"] == "unavailable" and "соединение отклонено" in r["reason"], r["reason"]
