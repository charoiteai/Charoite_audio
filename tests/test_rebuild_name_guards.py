"""Имена после пересборки проходят те же гварды доверия, что живое опознание.

Аудит зон 12.09 (зона 1): name_speakers принимал ответ модели почти на веру
— владелец отсекался только полной строкой user_name («Игорь» против
«Игорь Ветров» проходил), выдуманное имя не сверялось с текстом, обращение в
собственной реплике («Маш, ты смету видела?») делало говорящего Машей.
Уступка живой встрече шла без потолка — очередь пересборок парковалась на
всю чужую встречу. Ollama здесь не поднимается: подменён клиент llm.LLM.
"""
from __future__ import annotations

import json
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import llm as llm_mod  # noqa: E402
import rebuild_transcript as rt  # noqa: E402

CFG = {"llm": {"model": "тест"}, "sufler": {"user_name": "Игорь Ветров"}}


def _model(monkeypatch, answer: dict) -> None:
    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, *a, **k) -> str:
            return json.dumps(answer, ensure_ascii=False)
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)


def test_owner_first_name_is_not_given_to_a_participant(monkeypatch):
    """Р1: «Игорь» — это «Игорь Ветров», владелец; собеседнику не достаётся."""
    lines = [("Собеседник 2", "Игорь, привет, это Сергей"), ("Собеседник 1", "Привет, Сергей")]
    _model(monkeypatch, {"Собеседник 1": "Игорь", "Собеседник 2": "Сергей"})
    names, answered = rt.name_speakers(CFG, lines)
    assert names == {"Собеседник 2": "Сергей"}
    assert answered is True


def test_invented_name_is_refused(monkeypatch):
    """Р3: имени, которого в разговоре не было, не существует."""
    lines = [("Собеседник 1", "Смету посмотрим завтра"), ("Собеседник 2", "Хорошо")]
    _model(monkeypatch, {"Собеседник 1": "Виктор", "Собеседник 2": "?"})
    names, answered = rt.name_speakers(CFG, lines)
    assert names == {}
    assert answered is False, "всё предложенное отвергнуто — снаружи это молчание, плашка остаётся (критика DS по #551)"


def test_address_in_own_line_is_not_the_speaker(monkeypatch):
    """Р2: «Маш, ты смету видела?» говорит НЕ Маша."""
    lines = [("Собеседник 1", "Маш, ты смету видела?"), ("Собеседник 2", "Потом посмотрю")]
    _model(monkeypatch, {"Собеседник 1": "Маша"})
    assert rt.name_speakers(CFG, lines) == ({}, False)


def test_vocative_from_the_other_side_is_accepted_in_nominative(monkeypatch):
    """Обращение «Маш» с другой стороны — законный источник имени «Маша»:
    промпт просит именительный падеж, гвард не должен принимать его за выдумку."""
    lines = [("Собеседник 1", "Маш, ты смету видела?"), ("Собеседник 2", "Видела, завтра пришлю")]
    _model(monkeypatch, {"Собеседник 2": "Маша"})
    assert rt.name_speakers(CFG, lines) == ({"Собеседник 2": "Маша"}, True)


def test_junk_keys_and_values_are_ignored(monkeypatch):
    """Мусорные ключи и «?» — не предложения: модель честно сказала «имён нет»."""
    lines = [("Собеседник 1", "Привет, я Сергей")]
    _model(monkeypatch, {"Я": "Сергей", "Собеседник 1": 7, "Собеседник 9": " ", "Собеседник 3": "?"})
    assert rt.name_speakers(CFG, lines) == ({}, True)


def test_partial_acceptance_is_still_an_answer(monkeypatch):
    lines = [("Собеседник 1", "Привет, я Сергей"), ("Собеседник 2", "А я тут")]
    _model(monkeypatch, {"Собеседник 1": "Сергей", "Собеседник 2": "Виктор"})
    assert rt.name_speakers(CFG, lines) == ({"Собеседник 1": "Сергей"}, True)


def test_yield_to_live_has_a_cap(monkeypatch):
    """Очередь пересборок уже взята — уступка живой встрече не бесконечна."""
    seen: dict = {}

    def fake_yield(what, cap=None):
        seen.update(what=what, cap=cap)

    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, *a, **k) -> str:
            return "{}"
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", fake_yield)
    rt.name_speakers(CFG, [("Собеседник 1", "да")])
    assert seen == {"what": "имена", "cap": 600}


def test_sample_is_cut_on_a_line_boundary(monkeypatch):
    """Обрезок слова с заглавной на границе 7000 знаков («Лен» от
    «Ленинградское») в чужой реплике не должен становиться формой имени «Лена»
    (GLM I1 по #551): режем по границе строки, как tr.tail в демоне."""
    seen: dict = {}

    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, sample, *a, **k) -> str:
            seen["sample"] = sample
            return json.dumps({"Собеседник 1": "Лена"}, ensure_ascii=False)
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    filler = " ".join(["слово"] * 30)
    lines: list[tuple[str, str]] = [("Собеседник 1", "начнём")]
    while len("\n".join(rt._sample_line(s_, t) for s_, t in lines)) <= 6600:
        lines.append(("Собеседник 2", filler))
    joined = "\n".join(rt._sample_line(s_, t) for s_, t in lines)
    start = len(joined) + 1 + len(rt._sample_line("Собеседник 2", ""))
    pad = "x" * (7000 - start - 4)
    lines.append(("Собеседник 2", pad + " Ленинградское шоссе обсудили"))
    joined = "\n".join(rt._sample_line(s_, t) for s_, t in lines)
    assert joined[6997:7000] == "Лен", joined[6990:7005]
    names, answered = rt.name_speakers(CFG, lines)
    assert names == {} and answered is False, names       # единственное предложенное отвергнуто
    assert seen["sample"] == rt._cut_lines(joined, 7000)
    assert joined.startswith(seen["sample"] + "\n"), "обрезка не по границе строки"
    assert "Лен" not in seen["sample"]
    assert rt._cut_lines("a b\nc d\ne f", 5) == "a b" and rt._cut_lines("abc def ghi", 7) == "abc" \
        and rt._cut_lines("short", 10) == "short"


def test_non_object_json_is_not_an_answer(monkeypatch):
    """Массив или строка под json_format — тот же мусор, что молчание:
    плашка «имена не разобраны» обязана остаться (GLM M1 по #551)."""
    class _Fake:
        def __init__(self, cfg):
            pass

        def complete(self, *a, **k) -> str:
            return json.dumps(["Собеседник 1"])
    monkeypatch.setattr(llm_mod, "LLM", _Fake)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    assert rt.name_speakers(CFG, [("Собеседник 1", "да")]) == ({}, False)


def test_known_people_of_the_graph_fold_the_case(monkeypatch, tmp_path):
    """Правило 4 и в пересборке: «Полин» с чужой стороны при узле «Полина»
    записывается каноном, а не звательным падежом (GLM M2 по #551)."""
    people = tmp_path / "Люди"
    people.mkdir()
    (people / "Полина Иванова.md").write_text("# Полина\n", encoding="utf-8")
    (people / "Собеседник 3.md").write_text("# ?\n", encoding="utf-8")
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda cfg=None, **k: tmp_path)
    assert rt.known_first_names(CFG) == ("Полина",)
    lines = [("Собеседник 1", "Полин, привет, глянь смету"), ("Собеседник 2", "Привет, гляну")]
    _model(monkeypatch, {"Собеседник 2": "Полин"})
    assert rt.name_speakers(CFG, lines, known=rt.known_first_names(CFG)) == ({"Собеседник 2": "Полина"}, True)
    monkeypatch.setattr(rt.graphs, "graph_dir", lambda cfg=None, **k: None)
    assert rt.known_first_names(CFG) == ()
