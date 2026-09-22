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
                        judge=fake_judge(refused="нет модели"))["ran"] is False


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
        tier3.revise(graph, embedder=wrong, judge=fake_judge())


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
                     judge=fake_judge(entail=deaf))
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
                        judge=fake_judge(ready=False))["ran"] is False


def test_incomplete_embeddings_do_not_crash_and_do_not_count_as_a_run(tmp_path, monkeypatch):
    """llm.embed при ошибке сервера отдаёт `[]` — раньше IndexError валил CLI
    ночи (аудит DeepSeek 17.08); теперь — «прогон не состоялся»."""
    graph = _graph(tmp_path, "Одно", "Другое")
    r = tier3.revise(graph, embedder=fake_embedder([]), judge=fake_judge())
    assert r["ran"] is False and r["dups"] == []


def test_full_run_is_not_marked_stopped(tmp_path, monkeypatch):
    """Состоявшийся полный прогон — ran=True, stopped=False: иначе
    tier3_cores никогда не сдвинет отметку --since-last и каждая ночь
    пересуживает всё с нуля (мутационный прогон 21.08)."""
    graph = _graph(tmp_path, "Одно", "Другое")
    # Одинаковые эмбеддинги: пара проходит префильтр, суд реально идёт по
    # циклу и спрашивает потолок ночи; с ортогональными пара отсекалась до
    # цикла и stopped=False держалось инициализацией, а не прогоном
    # (ревью 22.08: Sonnet 5 и DeepSeek независимо).
    asked = []
    monkeypatch.setattr(tier3.live_gate, "night_is_over", lambda: asked.append(1) or False)

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0], [1.0, 0.0]]),
                     judge=fake_judge())
    assert asked, "суд не дошёл до цикла пар — тест держал бы инициализацию"
    assert r["ran"] is True and r["stopped"] is False


def test_run_cut_by_the_night_ceiling_is_marked_stopped(tmp_path, monkeypatch):
    """Обрыв потолком ночи — stopped=True: недосуженные ядра остаются в
    инкременте на следующую ночь, отметка не двигается."""
    graph = _graph(tmp_path, "Одно", "Другое")
    monkeypatch.setattr(tier3.live_gate, "night_is_over", lambda: True)

    r = tier3.revise(graph, embedder=fake_embedder([[1.0, 0.0], [1.0, 0.0]]),
                     judge=fake_judge())
    assert r["ran"] is True and r["stopped"] is True
