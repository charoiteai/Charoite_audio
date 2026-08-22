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

import tier3  # noqa: E402
import tier3_cores  # noqa: E402

EMPTY = {"dups": [], "nests": [], "border": [], "log": [],
         "pending_merges": [], "skipped": [], "ran": True}


def _graph(tmp_path: pathlib.Path, *names: str) -> pathlib.Path:
    graph = tmp_path / "Граф"
    (graph / "Ядра").mkdir(parents=True)
    for n in names:
        (graph / "Ядра" / f"{n}.md").write_text("## Статус\nживо\n", encoding="utf-8")
    return graph


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
    monkeypatch.setattr(tier3_cores, "STAMPS", tmp_path / "stamps.json")
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
    monkeypatch.setattr(tier3_cores, "STAMPS", tmp_path / "нет.json")
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
    monkeypatch.setattr(tier3_cores, "STAMPS", tmp_path / "stamps.json")
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
    monkeypatch.setattr(tier3_cores, "STAMPS", tmp_path / "stamps.json")
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
    monkeypatch.setattr(tier3.nli, "is_available", lambda: False)

    assert tier3.revise(graph)["ran"] is False


def test_ran_is_false_when_the_judge_did_not_come(tmp_path, monkeypatch):
    """Файлы NLI на месте, но сессия не собралась (битый ONNX): entail_prob
    тихо отдаёт 0.0, суд «ничего не находит» — раньше ran=True двигал отметку
    --since-last, и свежие ядра навсегда выпадали из инкремента
    (аудит DeepSeek 17.08)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3.nli, "is_available", lambda: True)
    monkeypatch.setattr(tier3.nli, "ready", lambda: False)
    monkeypatch.setattr(tier3, "_embed_all", lambda cores, cfg: [[1.0, 0.0]] * len(cores))

    assert tier3.revise(graph)["ran"] is False


def test_incomplete_embeddings_do_not_crash_and_do_not_count_as_a_run(tmp_path, monkeypatch):
    """llm.embed при ошибке сервера отдаёт `[]` — раньше IndexError валил CLI
    ночи (аудит DeepSeek 17.08); теперь — «прогон не состоялся»."""
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3.nli, "is_available", lambda: True)
    monkeypatch.setattr(tier3.nli, "ready", lambda: True)
    monkeypatch.setattr(tier3, "_embed_all", lambda cores, cfg: [])

    r = tier3.revise(graph)
    assert r["ran"] is False and r["dups"] == []


def test_full_run_is_not_marked_stopped(tmp_path, monkeypatch):
    """Состоявшийся полный прогон — ran=True, stopped=False: иначе
    tier3_cores никогда не сдвинет отметку --since-last и каждая ночь
    пересуживает всё с нуля (мутационный прогон 21.08)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3.nli, "is_available", lambda: True)
    monkeypatch.setattr(tier3.nli, "ready", lambda: True)
    monkeypatch.setattr(tier3, "_embed_all", lambda cores, cfg: [[1.0, 0.0], [0.0, 1.0]])
    monkeypatch.setattr(tier3.live_gate, "night_is_over", lambda: False)

    r = tier3.revise(graph)
    assert r["ran"] is True and r["stopped"] is False


def test_run_cut_by_the_night_ceiling_is_marked_stopped(tmp_path, monkeypatch):
    """Обрыв потолком ночи — stopped=True: недосуженные ядра остаются в
    инкременте на следующую ночь, отметка не двигается."""
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3.nli, "is_available", lambda: True)
    monkeypatch.setattr(tier3.nli, "ready", lambda: True)
    monkeypatch.setattr(tier3, "_embed_all", lambda cores, cfg: [[1.0, 0.0], [1.0, 0.0]])
    monkeypatch.setattr(tier3.live_gate, "night_is_over", lambda: True)

    r = tier3.revise(graph)
    assert r["ran"] is True and r["stopped"] is True
