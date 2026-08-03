"""Саммари не имеет права терять решения встречи.

В шаблоне раздела стояло «- **решение** — кто внедряет». У решений в минутках
исполнителя обычно нет: «признаны неподходящими», «отказ от эскалации»,
«вариант отложен». Модель не находила «кто внедряет» и писала «решений не
было» — поверх трёх записанных решений, лежащих в той же папке.

Из 63 встреч архива так врали восемь саммари. Обиднее всего, что это ровно тот
раздел, ради которого выжимку и открывают.

Прогон на четырёх встречах 03.08 (две с решениями, две без): с требованием
исполнителя — 2 из 4, без него — 4 из 4.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import meeting_archive  # noqa: E402


def test_decisions_section_does_not_demand_an_owner(tmp_path, monkeypatch):
    """Требование исполнителя было причиной, а не оформлением.

    Пока оно стоит в шаблоне, модель считает решением только то, у чего есть
    ответственный, — и теряет всё остальное.
    """
    folder = tmp_path / "2026-07-15 09-00 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text(
        "## Решения\n1. Текущие решения признаны неподходящими.\n"
        "2. Передача подрядчику отложена.\n", encoding="utf-8")

    captured: dict = {}

    class _Resp:
        def json(self): return {"message": {"content": ""}}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["user"] = json["messages"][-1]["content"]
        return _Resp()

    monkeypatch.setattr(meeting_archive.__dict__.get("requests", None) or __import__("requests"),
                        "post", fake_post)
    meeting_archive._gen_summary(folder, force=True)

    block = re.search(r"## Решили\n\((.*?)\)", captured.get("user", ""), re.S)
    assert block, "раздел «Решили» обязан быть в шаблоне"
    assert "кто внедряет" not in block.group(1), \
        "у решения в минутках исполнителя обычно нет — требовать его значит потерять раздел"


def test_empty_case_still_has_an_explicit_wording(tmp_path, monkeypatch):
    """Отсутствие решений тоже надо называть, иначе модель выдумает своё."""
    folder = tmp_path / "2026-07-15 09-00 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text("## Обсудили\n- всё\n", encoding="utf-8")

    captured: dict = {}

    class _Resp:
        def json(self): return {"message": {"content": ""}}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["user"] = json["messages"][-1]["content"]
        return _Resp()

    monkeypatch.setattr(__import__("requests"), "post", fake_post)
    meeting_archive._gen_summary(folder, force=True)

    assert "решений не было" in captured.get("user", "")


def test_materials_reach_the_model_decisions_first(tmp_path, monkeypatch):
    """Минутки идут первыми: решения лежат в них, а хвост промпта модель
    читает хуже начала."""
    folder = tmp_path / "2026-07-15 09-00 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text("## Решения\n1. Решили внедрять.\n", encoding="utf-8")
    (folder / "Стенограмма.md").write_text("долгий разговор\n" * 100, encoding="utf-8")

    captured: dict = {}

    class _Resp:
        def json(self): return {"message": {"content": ""}}

    def fake_post(url, json, timeout):  # noqa: A002
        captured["user"] = json["messages"][-1]["content"]
        return _Resp()

    monkeypatch.setattr(__import__("requests"), "post", fake_post)
    meeting_archive._gen_summary(folder, force=True)

    user = captured.get("user", "")
    assert user.index("=== Минутки.md ===") < user.index("=== Стенограмма.md ===")
    assert "Решили внедрять" in user


# --- что переживает обрезку --------------------------------------------------
#
# Лимит саммари держит код, а не промпт. Прежде при переполнении отбрасывался
# хвост текста — и «Поручения» гибли раньше обзорного «О чём говорили»: из
# выжимки встречи 15.07 пропало, кто что должен сделать, то есть ровно то, ради
# чего её открывают. Всплыло сразу после того, как раздел решений перестал быть
# пустым и занял место.

from meeting_archive import _trim_summary  # noqa: E402


def _summary(*, talk: int = 3, decided: int = 3, tasks: int = 2, open_q: int = 2) -> str:
    def block(head: str, n: int, text: str) -> str:
        return f"## {head}\n" + "\n".join(f"- **{text} {i}** — {'слово ' * 12}" for i in range(n))
    parts = ["**Суть одной строкой:** " + "слово " * 10]
    if talk: parts.append(block("О чём говорили", talk, "тема"))
    if decided: parts.append(block("Решили", decided, "решение"))
    if tasks: parts.append(block("Поручения", tasks, "Кто"))
    if open_q: parts.append(block("Открытые вопросы", open_q, "вопрос"))
    return "\n\n".join(parts)


def test_tasks_outlive_the_overview():
    """Кто что должен сделать важнее, чем перечень тем разговора."""
    out = _trim_summary(_summary())

    assert "Поручения" in out
    assert "Решили" in out


def test_open_questions_are_sacrificed_first():
    out = _trim_summary(_summary())
    assert "Открытые вопросы" not in out


def test_overview_goes_before_decisions_when_it_is_really_tight():
    # очень длинная встреча: жертвуем обзором, но не тем, что решили и кому делать
    out = _trim_summary(_summary(talk=3, decided=3, tasks=3, open_q=3), limit=420)

    assert "Решили" in out and "Поручения" in out
    assert "О чём говорили" not in out


def test_short_summary_is_left_alone():
    text = "**Суть одной строкой:** коротко\n\n## Решили\n- **раз** — два"
    assert _trim_summary(text) == text
