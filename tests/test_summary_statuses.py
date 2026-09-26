"""№392: свежесть саммари встречи не зависит от статусов поручений.

Владелец и ночной контроль ставят в каноне минуток отметки «[x]»/«[-]», пометку
контроля «_(снято …)_» и поля плагина. Это учёт после встречи, а не её содержание:
нормализация источника — `task_line.without_statuses` — снимает хвост и символ
ящика ДО обрезки по `SUMMARY_CAPS`, поэтому отмеченный пункт не старит саммари и
не гонит модель на пересборку ради учёта. Тезисы, разбор и стенограмма — как есть.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import live_sidecar  # noqa: E402
import meeting_archive as ma  # noqa: E402


def _folder(tmp_path, minutes="## Решения\n- Первое решение принято.\n"):
    folder = tmp_path / "2026-09-24 10-00 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text(minutes, encoding="utf-8")
    (folder / "Стенограмма.md").write_text("# Встреча\n\n[10:00:00] Иван: начнём\n", encoding="utf-8")
    live = tmp_path / "2026-09-24_1000_тема.md"
    live.write_text("# Встреча 2026-09-24_1000\n\n[10:00:00] Иван: начнём\n", encoding="utf-8")
    return folder, live


def _fake_model(monkeypatch, calls: list, answer="**Суть** встреча.\n\n## Решили\n- **Первое** — принято\n"):
    class _Resp:
        status_code = 200
        text = ""
        headers: dict = {}

        def __init__(self, body): self._b = body
        def json(self): return {"message": {"content": self._b}}
        def raise_for_status(self): pass

    def fake_post(url, json, timeout):  # noqa: A002
        calls.append(json["messages"][-1]["content"])
        return _Resp(answer)

    monkeypatch.setattr(__import__("requests"), "post", fake_post)


TASK_BOX = "## Поручения\n- [ ] **Аня** — подготовить отчёт\n"
TASK_MARKED = "## Поручения\n- [x] **Аня** — подготовить отчёт 📅 2026-10-01 _(снято по сроку 24.09)_\n"


def test_owner_marks_control_and_due_do_not_age_the_summary(tmp_path, monkeypatch):
    minutes = "## Решения\n- Утвердить бюджет.\n\n" + TASK_BOX
    folder, live = _folder(tmp_path, minutes=minutes)
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    assert len(calls) == 1
    # владелец отметил пункт и контроль приписал срок: содержание не менялось
    (folder / "Минутки.md").write_text(minutes.replace(TASK_BOX, TASK_MARKED), encoding="utf-8")
    assert ma.summary_state(folder, live, None) == live_sidecar.FRESH
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.AUTO) == live_sidecar.FRESH
    assert len(calls) == 1, "учёт не повод звать модель"


def test_editing_the_text_of_an_item_ages_the_summary(tmp_path, monkeypatch):
    minutes = "## Решения\n- Утвердить бюджет.\n\n" + TASK_BOX
    folder, live = _folder(tmp_path, minutes=minutes)
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    (folder / "Минутки.md").write_text(minutes.replace("подготовить отчёт", "сверить цифры"), encoding="utf-8")
    assert ma.summary_state(folder, live, None) == live_sidecar.STALE


def test_a_field_appended_at_the_cap_boundary_does_not_change_the_source(tmp_path):
    # нормализация — ДО обрезки: поле у самой границы 3500 знаков снимается и не
    # сдвигает то, что попало в обрезку
    folder = tmp_path / "f"
    folder.mkdir()
    head = "ф" * 3468 + "\n- [ ] **Аня** — отчёт"          # 3490 знаков, до потолка
    marked = head + " 📅 2026-10-01"                       # поле пересекает 3500
    assert len(head) == 3490 and len(marked) > 3500
    assert head[:3500] != marked[:3500], "без нормализации обрезка увидела бы другое"
    (folder / "Минутки.md").write_text(head, encoding="utf-8")
    before = ma.summary_source_sha(ma.summary_materials(folder), ma.decisions_of(folder), None)
    (folder / "Минутки.md").write_text(marked, encoding="utf-8")
    after = ma.summary_source_sha(ma.summary_materials(folder), ma.decisions_of(folder), None)
    assert before == after


def test_a_passport_built_from_status_free_machine_text_stays_fresh(tmp_path):
    # легаси-паспорта собраны по машинному тексту без статусов: там нормализация —
    # тождество, и хеш совпадает — паспорт FRESH без пересборки
    folder, live = _folder(tmp_path)
    raw = []
    for name, cap in ma.SUMMARY_CAPS:
        p = folder / name
        if p.exists():
            text = p.read_text(encoding="utf-8")
            raw.append((name, text[-cap:] if name == "Стенограмма.md" else text[:cap]))
    assert raw == ma.summary_materials(folder), "нормализация машинного текста — тождество"
    src_sha = ma.summary_source_sha(raw, ma.decisions_of(folder), None)
    out = folder / "Саммари.md"
    out.write_text("---\ntype: саммари\n---\nстарое\n", encoding="utf-8")
    assert live_sidecar.attest(live, "summary", out.read_text(encoding="utf-8"), src_sha)
    assert ma.summary_state(folder, live, None) == live_sidecar.FRESH


def test_decisions_taken_from_the_minutes_are_normalized(tmp_path):
    f = tmp_path / "f"
    f.mkdir()
    (f / "Минутки.md").write_text(
        "## Решения\n- [x] Утвердить бюджет 📅 2026-10-01\n- Вернуть срок.\n", encoding="utf-8")
    # ящик — форма поручения, не решения: без него, иначе «[x]» владельца доезжал бы
    # до «Саммари.md» открытым «[ ]» (DeepSeek по PR #641)
    assert ma.decisions_of(f) == ["Утвердить бюджет", "Вернуть срок."]


def test_forced_decisions_carry_no_open_box(tmp_path, monkeypatch):
    """Запасной путь «решений не было» кладёт решения из минуток в документ — без
    ящика: отметка владельца не превращается в открытую задачу в «Саммари.md»."""
    folder, live = _folder(tmp_path, minutes="## Решения\n- [x] Утвердить бюджет\n")
    calls: list = []
    _fake_model(monkeypatch, calls, answer="**Суть** встреча.\n\n## Решили\n- решений не было\n")
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    text = (folder / "Саммари.md").read_text(encoding="utf-8")
    assert "- Утвердить бюджет\n" in text, text
    assert "[ ]" not in text and "[x]" not in text, text


def test_decisions_from_the_debrief_are_taken_as_is(tmp_path):
    # «Разбор.md» не нормализуется: «[x]» в его тексте — содержание, не ящик минуток
    f = tmp_path / "f"
    f.mkdir()
    (f / "Минутки.md").write_text("## Обсудили\n- всякое\n", encoding="utf-8")
    (f / "Разбор.md").write_text("## Решения\n- [x] Так и оставить\n", encoding="utf-8")
    assert ma.decisions_of(f) == ["[x] Так и оставить"]


def test_a_footnote_definition_in_the_minutes_is_kept(tmp_path):
    folder, _ = _folder(tmp_path, minutes="## Решения\n- решение\n\n[1]: Источник, 12.09\n")
    assert dict(ma.summary_materials(folder))["Минутки.md"] == "## Решения\n- решение\n\n[1]: Источник, 12.09\n"


def test_a_footnote_reference_in_the_theses_is_kept(tmp_path):
    folder, _ = _folder(tmp_path)
    theses = "## Тезисы\n- вывод [1] и [2]\n\n[1]: Иванов, 12.09\n"
    (folder / "Тезисы.md").write_text(theses, encoding="utf-8")
    assert dict(ma.summary_materials(folder))["Тезисы.md"] == theses


def test_the_prompt_sees_the_normalized_materials(tmp_path, monkeypatch):
    minutes = "## Решения\n- Утвердить бюджет.\n\n" + TASK_MARKED
    folder, live = _folder(tmp_path, minutes=minutes)
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    materials = calls[0].split("</материалы>")[0]
    assert "[x]" not in materials and "📅" not in materials and "_(снято" not in materials
    assert "- [ ] **Аня** — подготовить отчёт" in materials, "модель видит то, что хешируется"


def test_auto_does_not_build_a_passportless_summary_with_a_marked_canon(tmp_path, monkeypatch):
    # политика саммари без паспорта — №406, не этот PR: AUTO её не строит, но и не
    # принимает отметку в каноне за новое содержание
    folder, live = _folder(tmp_path, minutes="## Решения\n- Утвердить.\n\n" + TASK_MARKED)
    (folder / "Саммари.md").write_text("---\ntype: саммари\n---\nстарое\n", encoding="utf-8")
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma.summary_state(folder, live, None) == live_sidecar.UNKNOWN
    outcome = ma.summary_pass(folder, live, None, mode=ma.SummaryMode.AUTO)
    assert outcome.state == live_sidecar.UNKNOWN
    assert "unknown" in (outcome.line() or ""), "строка отчёта называет состояние (Important DS по PR #641)"
    assert calls == [], "без паспорта модель не зовётся"


def test_decisions_in_returns_an_empty_list_without_a_section():
    # пустой список, а не None: `decided()` складывает решения снимка, и None на
    # месте списка прошёл бы по истинности мимо этой поломки
    assert ma._decisions_in("## Обсудили\n- всякое\n") == []
    assert ma._decisions_in("## Решения\n- Одно.\n") == ["Одно."]


def test_materials_cap_the_minutes_from_the_start_and_the_transcript_from_the_end(tmp_path):
    # у стенограммы важнее конец (итоги), у минуток — начало; с коротким текстом
    # это неразличимо, поэтому берём заведомо длинные материалы
    folder = tmp_path / "f"
    folder.mkdir()
    minutes = "Н" + "м" * 3600 + "\nКОНЕЦ_МИНУТОК"
    transcript = "НАЧАЛО_СТЕНОГРАММЫ\n" + "с" * 5000
    (folder / "Минутки.md").write_text(minutes, encoding="utf-8")
    (folder / "Стенограмма.md").write_text(transcript, encoding="utf-8")
    mats = dict(ma.summary_materials(folder))
    assert mats["Минутки.md"] == minutes[:3500]
    assert mats["Стенограмма.md"] == transcript[-4000:]
