"""Звательный падеж в графе: «Коль» — это Коля, а не второй человек.

Встреча 05.09 14:13: авто-проход отдал «Коль» (обращение «Коль, ты имеешь в
виду…») отдельной записью, и рядом с [[Люди/Коля]] появился [[Люди/Коль]].
Проход 3 find_canonical (подстрока) имена короче пяти букв не смотрит, а
префиксной склейки в графе нет вовсе — узел плодился молча.
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import graph_updater as g  # noqa: E402


def _person(graph: pathlib.Path, name: str) -> None:
    d = graph / "Люди"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\ntype: person\n---\n# {name}\n", encoding="utf-8")


def test_vocative_resolves_to_existing_person_node(tmp_path):
    """«Коль» → Люди/Коля, «Ань» → Люди/Аня; ссылка в заметке — на тот же узел."""
    _person(tmp_path, "Коля")
    _person(tmp_path, "Аня")
    assert g.find_canonical(tmp_path, "Коль", folder="Люди").stem == "Коля"
    assert g.find_canonical(tmp_path, "Ань", folder="Люди").stem == "Аня"
    assert g.canon_link(tmp_path, "Коль", "Люди") == "[[Люди/Коля|Коля]]"


def test_vocative_pass_is_people_only_and_exact(tmp_path):
    """Системы не трогаем; «Влад» к «Владе» не клеим; двух Коль не гадаем."""
    _person(tmp_path, "Влада")
    (tmp_path / "Системы").mkdir()
    (tmp_path / "Системы" / "Коля.md").write_text("---\ntype: system\n---\n# Коля\n", encoding="utf-8")
    assert g.find_canonical(tmp_path, "Влад", folder="Люди") is None
    assert g.find_canonical(tmp_path, "Коль", folder="Системы") is None
    assert g.find_canonical(tmp_path, "Коль", folder="Люди") is None   # узел Коля — в Системах, не человек


def test_alias_written_by_a_human_beats_the_vocative_guess(tmp_path):
    """Николай с aliases: [Коль] и рядом Коля: «Коль» — это Николай (проход 2б раньше 2в)."""
    _person(tmp_path, "Коля")
    (tmp_path / "Люди" / "Николай.md").write_text(
        "---\ntype: person\naliases: [\"Коль\"]\n---\n# Николай\n", encoding="utf-8")
    assert g.find_canonical(tmp_path, "Коль", folder="Люди").stem == "Николай"


def test_two_derived_forms_are_not_guessed(tmp_path):
    """«Иль» при узлах Илья и Иля — ни к кому; при одном Илье — к Илье."""
    _person(tmp_path, "Илья")
    assert g.find_canonical(tmp_path, "Иль", folder="Люди").stem == "Илья"
    _person(tmp_path, "Иля")
    assert g.find_canonical(tmp_path, "Иль", folder="Люди") is None


def test_merge_vocatives_joins_batch_entries():
    """Обе записи в одном разборе: звательная вливается в именительную, вклад не теряется."""
    people = [{"имя": "Коля", "роль": "процедурная линия", "вклад": "описание столбца с КПЭ"},
              {"имя": "Коль", "роль": "", "вклад": "уточнение про комментарии"},
              {"имя": "Саша", "вклад": "вторая ветка"}]
    out = g.merge_vocatives(people)
    assert [p["имя"] for p in out] == ["Коля", "Саша"]
    assert out[0]["роль"] == "процедурная линия"
    assert "описание столбца с КПЭ" in out[0]["вклад"] and "уточнение про комментарии" in out[0]["вклад"]


def test_merge_vocatives_keeps_role_and_does_not_drop_shorter_contribution():
    """Роль звательной записи переходит, если у именительной пустая; «уточнение» при
    «уточнение про комментарии» — не дубль (сравнение по равенству, не по подстроке)."""
    people = [{"имя": "Коля", "роль": "", "вклад": "уточнение про комментарии"},
              {"имя": "Коль", "роль": "процедурная линия", "вклад": "Уточнение"}]
    out = g.merge_vocatives(people)
    assert out[0]["роль"] == "процедурная линия"
    assert out[0]["вклад"] == "уточнение про комментарии; Уточнение"
    same = [{"имя": "Коля", "вклад": "рамка"}, {"имя": "Коль", "вклад": "  рамка "}]
    assert g.merge_vocatives(same)[0]["вклад"] == "рамка"


def test_merge_vocatives_keeps_lone_vocative():
    """Именительной формы в разборе нет — запись остаётся: find_canonical проверит граф."""
    people = [{"имя": "Коль", "вклад": "уточнение"}, {"имя": "Дмитрий", "вклад": "рамка"}]
    assert [p["имя"] for p in g.merge_vocatives(people)] == ["Коль", "Дмитрий"]


SPEECH = """# Встреча 2026-09-05_1413 — Отладка модели
Участники (звучали в разговоре): Андрей, Дмитрий

**Андрей** [14:15]:
Мариш сюда. Так, давай Андрей, тебе пришлю пример. Это к Саше Никитину вопрос?

**Собеседник 2** [14:16]:
Скорее к нам. Саша Никитин обновлял боевую часть, спросим Никитина.

**Аня** [14:17]:
Я тебе отправила только справочники.
"""


def test_background_voice_is_not_a_participant():
    """«Мариш» из бытового фона: не говорила и упомянута один раз — не узел.
    Аня говорила (метка), Никитин упомянут трижды — остаются."""
    people = [{"имя": "Мариш", "вклад": "предлагала тесты на валюты"},
              {"имя": "Аня", "вклад": "прислала справочники"},
              {"имя": "Саша Никитин", "вклад": "обновлял боевую часть"},
              {"имя": "Дмитрий", "вклад": "рамка"}]
    kept, noise = g.drop_background(people, SPEECH)
    assert noise == ["Мариш"], noise
    assert [p["имя"] for p in kept] == ["Аня", "Саша Никитин", "Дмитрий"]


def test_background_filter_is_conservative():
    """Пустая стенограмма или короткое имя — никого не выбрасываем; два упоминания — остаётся."""
    people = [{"имя": "Ян"}, {"имя": "Голенков"}]
    kept, noise = g.drop_background(people, "")
    assert noise == ["Голенков"] and [p["имя"] for p in kept] == ["Ян"]   # пусто — упоминаний нет
    kept, noise = g.drop_background(people, "**Я** [10:00]:\nГоленков сказал, что Голенков придёт.\n")
    assert noise == [] and len(kept) == 2

