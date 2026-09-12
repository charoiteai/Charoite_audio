"""Память Чароита после облачной ревизии — по заметке встречи после неё (№237)."""
import sys
import time
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
    # облако переформатировало: уровень заголовка и отступ списка — не повод
    # «не видеть» решений ни до, ни после (DS r1 M3 по #545)
    assert g.note_decisions("### Решения\n  - 📌 одно\n  - ⛔ два\n### Связи\n- 📌 не решение\n") == ["одно"]
    # подраздел внутри решений — не конец раздела (DS r2 I2 по #545)
    assert g.note_decisions("## Решения\n### По бюджету\n- 📌 Утвердили бюджет\n## Связи\n- 📌 нет\n") == ["Утвердили бюджет"]
    assert g._note_head(AFTER.replace("## Участники", "### Участники").replace("## Темы", "### Темы"))[1:] == (
        [{"имя": "Иван"}, {"имя": "Анна"}], ["Оценки и бюджет", "Бюджет на следующий год"]), "уровень заголовка — мягко (GLM r2 M1)"
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


def test_resend_keeps_a_debt_when_remember_fails_after_forget_and_pays_it_next_time(tmp_path):
    """DS r1 I3 по #545: brain забыл встречу, а /remember упал на эмбеддере —
    раньше память встречи оставалась пустой навсегда (на следующем проходе
    заметка уже правлена, «решения те же»). Теперь долг лежит рядом с
    отметкой, и следующий проход досылает."""
    note = tmp_path / "n.md"
    note.write_text(AFTER, encoding="utf-8")
    mark = tmp_path / "brain_sent" / "s.txt"
    mark.parent.mkdir()
    mark.write_text("sent 4/4\nid:x\n", encoding="utf-8")
    debt = mark.with_suffix(".pending")

    def half_dead(url, json, timeout):
        if url.endswith("/forget"):
            return _Resp()
        raise ConnectionError("embedder busy")

    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, post=half_dead)
    assert out.startswith("память Чароита НЕ переотправлена полностью (ушло 0 из 2)") and debt.exists(), out
    assert not mark.exists(), "отметки нет — повтор обработки тоже дошлёт всё"
    calls: list[str] = []
    out = g.resend_to_brain_after_review("s", note, AFTER, mark, post=lambda url, json, timeout: calls.append(url) or _Resp())
    assert out == "память Чароита переотправлена после ревизии: снято решений 0, ушло фактов 2", out
    assert calls[0].endswith("/forget") and len(calls) == 3 and not debt.exists()
    calls.clear()
    out = g.resend_to_brain_after_review("s", note, AFTER, mark, post=lambda *a, **k: calls.append(a) or _Resp())
    assert "без переотправки" in out and not calls, "долг оплачен, решения те же — тишина"
    # brain лёг на /forget — долг остаётся, отметка цела
    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, post=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert "долг записан" in out and debt.exists() and mark.exists()
    # долг гасит и обычный отправитель — повтор обработки (DS r2 M3): отметка полна → долг снят
    g.send_to_brain("s", "Планёрка команды", [{"имя": "Иван"}], ["тема"],
                    ["Пересчёт бюджета произойдёт на следующей неделе"], mark, post=lambda *a, **k: _Resp())
    assert not debt.exists()


def test_send_to_brain_owns_the_debt_and_any_review_run_pays_other_meetings(tmp_path):
    """Долг ставит и снимает тот, кто пишет отметку; долги чужих встреч
    гасит любой прогон ревизии (GLM r2 M2 и критика по #545)."""
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    mark = sent / "2026-09-01_1000.txt"

    def dead(url, json, timeout):
        raise ConnectionError("down")

    assert g.send_to_brain("2026-09-01_1000", "Тема", [], [], ["решение"], mark, post=dead) == 0
    assert (sent / "2026-09-01_1000.pending").exists() and not mark.exists(), "ничего не дошло — долг записан"
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / "2026-09-01_1000.md").write_text(BEFORE.replace("2026-09-11_1533", "2026-09-01_1000"), encoding="utf-8")
    (sent / "2026-09-02_1000.pending").touch()          # заметки нет — платить не по чему
    (sent / "2026-09-11_1533.pending").touch()          # своя встреча — не трогаем
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    assert g.pay_brain_debts(graph, sent, skip="2026-09-11_1533", post=ok) == [] and not calls, \
        "свежий долг — у живого отправителя, чужим не трогать (DS r3 I2)"
    later = lambda: time.time() + g.DEBT_MIN_AGE + 1  # noqa: E731
    lines = g.pay_brain_debts(graph, sent, skip="2026-09-11_1533", post=ok, now=later)
    assert lines == ["долг памяти 2026-09-01_1000: память Чароита переотправлена после ревизии: снято решений 0, ушло фактов 4",
                     "долг памяти 2026-09-02_1000: заметки встречи в графе нет — снят"], lines
    assert calls[0].endswith("/forget") and len(calls) == 5
    assert sorted(p.name for p in sent.glob("*.pending")) == ["2026-09-11_1533.pending"]
    assert mark.read_text(encoding="utf-8").startswith("sent 4/4\n")


def test_debt_is_not_settled_by_a_lookalike_mark_a_legacy_mark_or_a_failed_mark_write(tmp_path, monkeypatch):
    """DS r3 Critical: отметка с чужими ключами того же числа при мёртвом
    brain гасила долг без единого POST; DS r3 M4: легаси-отметка — не
    знание, а допущение; GLM r3 M1: упавшая запись отметки — долг живёт."""
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    mark = sent / "s.txt"
    mark.write_text("sent 2/2\nid:head\nid:deadbeefdeadbeef\n# Т\n", encoding="utf-8")
    dead = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))  # noqa: E731
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, post=dead) == 0
    assert (sent / "s.pending").exists(), "ничего не ушло — долг на месте"
    (sent / "s.pending").unlink()
    mark.write_text("тема\n", encoding="utf-8")            # отметка прежнего формата
    (sent / "s.pending").touch()
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, post=lambda *a, **k: _Resp()) == 0
    assert (sent / "s.pending").exists(), "легаси-отметка долг не гасит"
    mark.unlink()
    (sent / "s.pending").unlink()
    writes = []

    def flaky_write(path, text, **kw):
        writes.append(text)
        if len(writes) == 2:
            raise OSError(28, "ENOSPC")
        path.write_text(text, encoding="utf-8")

    monkeypatch.setattr(g.safe_write, "write_text", flaky_write)
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, post=lambda *a, **k: _Resp()) == 2, "оба POST прошли"
    assert (sent / "s.pending").exists() and mark.read_text(encoding="utf-8").startswith("sent 1/2\n"), \
        "второй факт в brain есть, в отметке нет — долг держит полную переотправку"


def test_sender_lock_keeps_a_foreign_payer_and_a_second_sender_out(tmp_path, monkeypatch):
    """DS r3 I2, GLM r3 I1: пока один процесс шлёт факты встречи, второй
    отправитель ждёт (и сдаётся), а плательщик чужих долгов не ждёт вовсе."""
    import fcntl
    monkeypatch.setattr(g, "SEND_LOCK_WAIT", 0.0)
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    mark = sent / "2026-09-01_1000.txt"
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / "2026-09-01_1000.md").write_text(BEFORE, encoding="utf-8")
    debt = sent / "2026-09-01_1000.pending"
    debt.touch()
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    with open(mark.with_suffix(".lock"), "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert g.send_to_brain("2026-09-01_1000", "Т", [], [], ["x"], mark, post=ok) == 0 and not calls
        out = g.resend_to_brain_after_review("2026-09-01_1000", graph / "Встречи" / "2026-09-01_1000.md", "", mark, post=ok)
        assert "другой процесс" in out and not calls and not mark.exists()
        before = debt.stat().st_mtime
        lines = g.pay_brain_debts(graph, sent, post=ok, now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
        assert lines == ["долг памяти 2026-09-01_1000: память Чароита не переотправлена: факты встречи шлёт другой процесс — позже"]
        assert not calls and debt.exists() and debt.stat().st_mtime >= before
    lines = g.pay_brain_debts(graph, sent, post=ok, now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
    assert lines[0].endswith("ушло фактов 4") and not debt.exists() and len(calls) == 5


def test_unpayable_debt_does_not_hold_the_queue(tmp_path):
    """DS r3 I3: неоплаченный долг уходит в конец очереди, слот достаётся следующему."""
    import os
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    for i, stamp in enumerate(("2026-09-01_1000", "2026-09-02_1000", "2026-09-03_1000")):
        (graph / "Встречи" / f"{stamp}.md").write_text(BEFORE.replace("2026-09-11_1533", stamp), encoding="utf-8")
        p = sent / f"{stamp}.pending"
        p.touch()
        os.utime(p, (1000 + i, 1000 + i))

    class Bad:
        def raise_for_status(self):
            raise RuntimeError("500")

    def post(url, json, timeout):
        if url.endswith("/forget") or "2026-09-01_1000" not in json.get("text", ""):
            return _Resp()
        return Bad()

    later = lambda: time.time() + g.DEBT_MIN_AGE + 1  # noqa: E731
    first = g.pay_brain_debts(graph, sent, limit=1, post=post, now=later)
    assert first[0].startswith("долг памяти 2026-09-01_1000: память Чароита НЕ переотправлена полностью") and (sent / "2026-09-01_1000.pending").exists()
    second = g.pay_brain_debts(graph, sent, limit=1, post=post, now=later)
    assert second[0].startswith("долг памяти 2026-09-02_1000: память Чароита переотправлена"), second
    third = g.pay_brain_debts(graph, sent, limit=1, post=post, now=later)
    assert third[0].startswith("долг памяти 2026-09-03_1000: память Чароита переотправлена"), third
