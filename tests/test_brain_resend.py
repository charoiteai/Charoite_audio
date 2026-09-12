"""Память Чароита после облачной ревизии — по заметке встречи после неё (№237)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import graph_updater as g  # noqa: E402

BEFORE = """---
type: встреча
---
# Встреча 2026-09-11_1533 — Планёрка команды

## Темы
- Оценки и бюджет
- Бюджет на следующий год

## Участники
- ✅ [[Люди/Иван|Иван]] — **под меткой «Олег»** (ревизия L4): ведущий
- [[Люди/Анна|Анна]] — участница
- Собеседник 3 — ⚠️ не установлен
- 📌 Упомянуты, отсутствуют: [[Люди/Мария|Мария]] (сводка руками), [[Люди/Ольга|Ольга]]
- ⛔ «Марина», «Алиса» из минуток — не участники

## Решения
- 📌 Оценки по проекту и четвёртому этапу укладываются в бюджет
- 📌 Пересчёт бюджета произойдёт на следующей неделе
- 📌 Единый токен для всех команд

## Связи
- [[Люди/Анна|Анна]] → [[Люди/Ольга|Ольга]]: консультант
"""

AFTER = BEFORE.replace(
    "- 📌 Оценки по проекту и четвёртому этапу укладываются в бюджет",
    "- ⛔ Оценки по проекту и четвёртому этапу укладываются в бюджет _(ревизия: речь о третьем этапе)_",
).replace("- 📌 Единый токен для всех команд", "- ⛔ Единый токен для всех команд _(ревизия: шутка про бюджет ИИ)_")


class _Resp:
    def raise_for_status(self):
        pass


def test_note_decisions_skip_withdrawn_and_head_reads_only_participants():
    assert g.note_decisions(BEFORE) == ["Оценки по проекту и четвёртому этапу укладываются в бюджет",
                                        "Пересчёт бюджета произойдёт на следующей неделе",
                                        "Единый токен для всех команд"]
    assert g.note_decisions(AFTER) == ["Пересчёт бюджета произойдёт на следующей неделе"]
    assert g.note_decisions("# Встреча x\n\n## Темы\n- a\n") == []
    title, people, topics = g._note_head(AFTER)
    assert title == "Планёрка команды"
    assert [p["имя"] for p in people] == ["Иван", "Анна"], "упомянутые и «не участники» ссылки несут, участниками не начинаются"
    assert topics == ["Оценки и бюджет", "Бюджет на следующий год"]


def test_resend_forgets_meeting_and_sends_the_note_after_review(tmp_path):
    note = tmp_path / "2026-09-11_1533.md"
    note.write_text(AFTER, encoding="utf-8")
    mark = tmp_path / "brain_sent" / "2026-09-11_1533.txt"
    mark.parent.mkdir()
    mark.write_text("sent 4/4\nid:aaaa\nid:head\n# Планёрка команды\n", encoding="utf-8")
    calls: list[tuple[str, dict]] = []

    def post(url, json, timeout):
        calls.append((url, json))
        return _Resp()

    out = g.resend_to_brain_after_review("2026-09-11_1533", note, BEFORE, mark, post=post)
    assert out == "память Чароита переотправлена после ревизии: снято решений 2, ушло фактов 2", out
    assert calls[0] == (f"{g.BRAIN}/forget", {"meeting": "2026-09-11_1533"})
    sent = [j for u, j in calls[1:] if u.endswith("/remember")]
    assert len(sent) == 2 and sent[0]["text"].startswith("Встреча 2026-09-11_1533 «Планёрка команды» (Иван, Анна)")
    assert sent[1]["category"] == "decision" and "Пересчёт бюджета" in sent[1]["text"]
    assert not any("четвёртому" in j["text"] or "Единый токен" in j["text"] for j in sent), "снятые решения в память не идут"
    assert mark.read_text(encoding="utf-8").startswith("sent 2/2\n"), "отметка отправки переписана по новому составу"


def test_resend_is_a_no_op_when_decisions_did_not_change_and_survives_a_dead_brain(tmp_path):
    note = tmp_path / "n.md"
    note.write_text(BEFORE, encoding="utf-8")
    mark = tmp_path / "m.txt"
    mark.write_text("sent 4/4\nid:x\n", encoding="utf-8")
    calls = []
    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, post=lambda *a, **k: calls.append(a) or _Resp())
    assert "без переотправки" in out and not calls and mark.exists(), "облако правило заметку, но не решения — память не трогаем"
    note.write_text(AFTER, encoding="utf-8")

    def dead(url, json, timeout):
        raise ConnectionError("Connection refused")

    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, post=dead)
    assert out.startswith("память Чароита не переотправлена (brain:") and mark.exists(), "brain лежит — отметка цела, ревизия не роняется"
