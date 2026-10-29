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

    out = g.resend_to_brain_after_review("2026-09-11_1533", note, BEFORE, mark, enabled=True, post=post)
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
    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, enabled=True, post=lambda *a, **k: calls.append(a) or _Resp())
    assert "без переотправки" in out and not calls and mark.exists(), "облако правило заметку, но не решения — память не трогаем"
    note.write_text(AFTER, encoding="utf-8")

    def dead(url, json, timeout):
        raise ConnectionError("Connection refused")

    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, enabled=True, post=dead)
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

    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, enabled=True, post=half_dead)
    assert out.startswith("память Чароита НЕ переотправлена полностью (ушло 0 из 2)") and debt.exists(), out
    assert not mark.exists(), "отметки нет — повтор обработки тоже дошлёт всё"
    calls: list[str] = []
    out = g.resend_to_brain_after_review("s", note, AFTER, mark, enabled=True, post=lambda url, json, timeout: calls.append(url) or _Resp())
    assert out == "память Чароита переотправлена после ревизии: снято решений 0, ушло фактов 2", out
    assert calls[0].endswith("/forget") and len(calls) == 3 and not debt.exists()
    calls.clear()
    out = g.resend_to_brain_after_review("s", note, AFTER, mark, enabled=True, post=lambda *a, **k: calls.append(a) or _Resp())
    assert "без переотправки" in out and not calls, "долг оплачен, решения те же — тишина"
    # brain лёг на /forget — долг остаётся, отметка цела
    out = g.resend_to_brain_after_review("s", note, BEFORE, mark, enabled=True, post=lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    assert "долг записан" in out and debt.exists() and mark.exists()
    # повтор обработки с полной отметкой долг НЕ гасит: /forget не прошёл, снятое ревизией
    # решение в памяти, а покрытие ключей этого не видит (выход n615p8-out3, Haiku I-1;
    # раньше гасил — DS r2 M3, когда долг платила только ревизия). Платит pay_brain_debts.
    calls.clear()
    g.send_to_brain("s", "Планёрка команды", [{"имя": "Иван"}], ["тема"],
                    ["Пересчёт бюджета произойдёт на следующей неделе"], mark, enabled=True,
                    post=lambda *a, **k: calls.append(a) or _Resp())
    assert debt.exists() and not calls


def test_send_to_brain_owns_the_debt_and_any_review_run_pays_other_meetings(tmp_path):
    """Долг ставит и снимает тот, кто пишет отметку; долги чужих встреч
    гасит любой прогон ревизии (GLM r2 M2 и критика по #545)."""
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    mark = sent / "2026-09-01_1000.txt"

    def dead(url, json, timeout):
        raise ConnectionError("down")

    assert g.send_to_brain("2026-09-01_1000", "Тема", [], [], ["решение"], mark, enabled=True, post=dead) == 0
    assert (sent / "2026-09-01_1000.pending").exists() and not mark.exists(), "ничего не дошло — долг записан"
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / "2026-09-01_1000.md").write_text(BEFORE.replace("2026-09-11_1533", "2026-09-01_1000"), encoding="utf-8")
    (sent / "2026-09-02_1000.pending").write_text(g._graph_key(graph) + "\n")  # заметки нет — не по чему
    (sent / "2026-09-11_1533.pending").touch()          # своя встреча — не трогаем
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    assert g.pay_brain_debts(graph, sent, skip="2026-09-11_1533", enabled=True, post=ok) == [] and not calls, \
        "свежий долг — у живого отправителя, чужим не трогать (DS r3 I2)"
    later = lambda: time.time() + g.DEBT_MIN_AGE + 1  # noqa: E731
    lines = g.pay_brain_debts(graph, sent, skip="2026-09-11_1533", enabled=True, post=ok, now=later)
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
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, enabled=True, post=dead) == 0
    assert (sent / "s.pending").exists(), "ничего не ушло — долг на месте"
    (sent / "s.pending").unlink()
    mark.write_text("тема\n", encoding="utf-8")            # отметка прежнего формата
    (sent / "s.pending").touch()
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, enabled=True, post=lambda *a, **k: _Resp()) == 0
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
    assert g.send_to_brain("s", "Т", [], [], ["решение B"], mark, enabled=True, post=lambda *a, **k: _Resp()) == 2, "оба POST прошли"
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
        assert g.send_to_brain("2026-09-01_1000", "Т", [], [], ["x"], mark, enabled=True, post=ok) == 0 and not calls
        out = g.resend_to_brain_after_review("2026-09-01_1000", graph / "Встречи" / "2026-09-01_1000.md", "", mark, enabled=True, post=ok)
        assert "другой процесс" in out and not calls and not mark.exists()
        before = debt.stat().st_mtime
        lines = g.pay_brain_debts(graph, sent, enabled=True, post=ok, now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
        assert lines == ["долг памяти 2026-09-01_1000: память Чароита не переотправлена: факты встречи шлёт другой процесс — позже"]
        assert not calls and debt.exists() and debt.stat().st_mtime >= before
    lines = g.pay_brain_debts(graph, sent, enabled=True, post=ok, now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
    assert lines[0].endswith("ушло фактов 4") and not debt.exists() and len(calls) == 5
    # замка нет вовсе (ФС без flock) — это не «занято»: факты идут без замка (DS r4 I1)
    calls.clear()
    monkeypatch.setattr(g.fcntl, "flock", lambda *a, **k: (_ for _ in ()).throw(OSError(77, "ENOLCK")))
    assert g.send_to_brain("2026-09-05_1000", "Т", [], [], ["x"], sent / "2026-09-05_1000.txt", enabled=True, post=ok) == 2 and len(calls) == 2
    assert not (sent / "2026-09-05_1000.pending").exists()


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
    first = g.pay_brain_debts(graph, sent, limit=1, enabled=True, post=post, now=later)
    assert first[0].startswith("долг памяти 2026-09-01_1000: память Чароита НЕ переотправлена полностью") and (sent / "2026-09-01_1000.pending").exists()
    second = g.pay_brain_debts(graph, sent, limit=1, enabled=True, post=post, now=later)
    assert second[0].startswith("долг памяти 2026-09-02_1000: память Чароита переотправлена"), second
    third = g.pay_brain_debts(graph, sent, limit=1, enabled=True, post=post, now=later)
    assert third[0].startswith("долг памяти 2026-09-03_1000: память Чароита переотправлена"), third


def test_meeting_facts_carry_every_decision_participant_and_topic():
    """№615 п. 8: срез `decisions[:6]` молча терял хвост — на встречах 1–29.10 у 63 из
    104 решений больше шести. Состав факта — шапка и каждое уникальное решение; шапка
    называет всех участников и все темы (срезы `[:6]`/`[:4]` — тот же класс, вход in1 DS C1)."""
    people = [{"имя": f"Участник {i}"} for i in range(9)]
    topics = [f"тема {i}" for i in range(7)]
    for n in range(31):
        decisions = [f"решение номер {i}" for i in range(n)]
        keyed = g._meeting_facts("2026-10-28_1633", "Планёрка", people, topics, decisions + decisions[:2])
        assert len(keyed) == 1 + n, (n, len(keyed))
        assert [f["text"].rsplit(": ", 1)[1] for _, f in keyed[1:]] == decisions, "все решения, порядок заметки"
    head = keyed[0][1]["text"]
    assert all(p["имя"] in head for p in people) and all(t in head for t in topics), head


def test_resend_after_review_sends_the_tail_appended_by_the_review(tmp_path):
    """Облачная модель ревизии дописывает решения в конец раздела (в графе владельца —
    163 из 380 решений за шестым); переотправка по заметке уносит их все (№615 п. 8)."""
    tail = "".join(f"- 📌 _(ревизия, не записано авто)_ Дописанное решение {i}\n" for i in range(5))
    after = BEFORE.replace("\n## Связи", tail + "\n## Связи")
    note = tmp_path / "2026-09-11_1533.md"
    note.write_text(after, encoding="utf-8")
    mark = tmp_path / "brain_sent" / "2026-09-11_1533.txt"
    calls: list[tuple[str, dict]] = []
    out = g.resend_to_brain_after_review("2026-09-11_1533", note, BEFORE, mark, enabled=True,
                                         post=lambda url, json, timeout: calls.append((url, json)) or _Resp())
    sent = [j["text"] for u, j in calls if u.endswith("/remember")]
    assert out == "память Чароита переотправлена после ревизии: снято решений 0, ушло фактов 9", out
    assert len(sent) == 9 and sent[-1].endswith("Дописанное решение 4"), sent
    assert mark.read_text(encoding="utf-8").startswith("sent 9/9\n")


def test_rerun_of_a_meeting_sent_under_the_old_cut_sends_only_the_tail(tmp_path):
    """Встреча, отправленная при срезе `[:6]`: повтор обработки досылает хвост решений по
    ключам отметки, шапку (ключ `head`) второй раз не шлёт — не дубль (выход n615p8-out1)."""
    decisions = [f"решение номер {i}" for i in range(9)]
    keyed = g._meeting_facts("s", "Планёрка", [{"имя": "Иван"}], ["тема"], decisions)
    mark = tmp_path / "s.txt"
    mark.write_text("sent 7/7\n" + "".join(f"id:{k}\n" for k, _ in keyed[:7]) + "# Планёрка\n", encoding="utf-8")
    sent: list[str] = []
    n = g.send_to_brain("s", "Планёрка", [{"имя": "Иван"}], ["тема"], decisions, mark, enabled=True,
                        post=lambda url, json, timeout: sent.append(json["text"]) or _Resp())
    assert n == 3 and [t.rsplit(": ", 1)[1] for t in sent] == decisions[6:], sent
    assert mark.read_text(encoding="utf-8").startswith("sent 10/10\n")


def test_a_sender_turned_away_by_the_lock_leaves_a_debt(tmp_path, monkeypatch):
    """Выход n615p8-out2 (DS I2, Haiku I-1): с №615 п. 8 полная отправка держит замок
    отправителя дольше SEND_LOCK_WAIT, и второй отправитель уходил молча — его состав
    терялся. Теперь отказ оставляет долг, его платит pay_brain_debts по заметке."""
    import fcntl
    monkeypatch.setattr(g, "SEND_LOCK_WAIT", 0.0)
    mark = tmp_path / "brain_sent" / "2026-09-01_1000.txt"
    mark.parent.mkdir()
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    with open(mark.with_suffix(".lock"), "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert g.send_to_brain("2026-09-01_1000", "Т", [], [], ["x"], mark, enabled=True, post=ok) == 0
    assert not calls and mark.with_suffix(".pending").exists()


def test_a_marked_meeting_keeps_an_ordered_debt_and_a_debt_touched_mid_send_survives(tmp_path, monkeypatch):
    """Выход n615p8-out3. Haiku I-1: весь текущий состав уже в отметке — это не доказывает, что
    снятое ревизией решение ушло из памяти, долг остаётся для полной переотправки. Sonnet I1:
    долг, тронутый посреди отправки (отказ замка другому отправителю), её переживает.
    DS I2: отказ замка при полностью отмеченном составе долга не ставит."""
    import fcntl
    import os
    mark = tmp_path / "brain_sent" / "s.txt"
    mark.parent.mkdir()
    keys = [k for k, _ in g._meeting_facts("s", "Т", [], [], ["D1", "D2"])]
    mark.write_text("sent 3/3\n" + "".join(f"id:{k}\n" for k in keys) + "# Т\n", encoding="utf-8")
    debt = mark.with_suffix(".pending")
    debt.touch()
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    assert g.send_to_brain("s", "Т", [], [], ["D1"], mark, enabled=True, post=ok) == 0 and not calls
    assert debt.exists(), "покрытие ключей погасило заказ полной переотправки"

    mark.unlink()
    debt.unlink()

    def touched_mid_send(url, json, timeout):
        st = debt.stat()
        os.utime(debt, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        return _Resp()

    assert g.send_to_brain("s", "Т", [], [], ["D1", "D2"], mark, enabled=True, post=touched_mid_send) == 3
    assert debt.exists(), "долг, поставленный посреди отправки, снят ею"

    debt.unlink()
    monkeypatch.setattr(g, "SEND_LOCK_WAIT", 0.0)
    with open(mark.with_suffix(".lock"), "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert g.send_to_brain("s", "Т", [], [], ["D1", "D2"], mark, enabled=True, post=ok) == 0
    assert not debt.exists(), "досылать нечего — долг не нужен"


def test_debts_are_kept_when_the_graph_meetings_folder_is_gone(tmp_path):
    """Выход n615p8-out3, DS I3: том графа ушёл посреди прогона — «заметки нет» не значит
    «встречу забыли», долги не снимаются."""
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    debt = sent / "2026-09-01_1000.pending"
    debt.write_text(g._graph_key(tmp_path / "нет графа") + "\n")
    lines = g.pay_brain_debts(tmp_path / "нет графа", sent, enabled=True, post=lambda *a, **k: _Resp(),
                              now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
    assert debt.exists() and "нет каталога встреч" in lines[0]


def test_a_graph_pays_and_settles_only_its_own_debts(tmp_path):
    """Финальный Opus C1 по №615 п. 8: каталог отметок один на установку, а проект уводит встречу
    в соседний граф. Ревизия графа A снимала долг встречи графа B строкой «заметки нет» — снятое
    ревизией решение оставалось в памяти. Долг помнит корень своего графа: обработка A долг B
    не платит, не снимает и не трогает; платит и снимает его обработка B."""
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    a, b = tmp_path / "A", tmp_path / "B"
    for graph in (a, b):
        (graph / "Встречи").mkdir(parents=True)
    in_b = b / "Встречи" / "2026-09-03_1000.md"
    in_b.write_text(AFTER.replace("2026-09-11_1533", "2026-09-03_1000"), encoding="utf-8")
    g.owe_brain_resend(in_b, BEFORE, sent / "2026-09-03_1000.txt", enabled=True)
    g._set_debt(sent / "2026-09-04_1000.txt", b)               # заметку B уже убрали
    (sent / "2026-09-05_1000.pending").touch()                  # долг прежней версии, заметки нет нигде
    (a / "Встречи" / "2026-09-06_1000.md").write_text(BEFORE.replace("2026-09-11_1533", "2026-09-06_1000"),
                                                      encoding="utf-8")
    (sent / "2026-09-06_1000.pending").touch()                  # долг прежней версии по заметке A
    debts = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sent.glob("*.pending")}
    assert debts["2026-09-03_1000.pending"][1] == (g._graph_key(b) + "\n").encode(), "долг не помнит граф"
    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(json.get("meeting") or json.get("text")) or _Resp()  # noqa: E731
    later = lambda: time.time() + g.DEBT_MIN_AGE + 1  # noqa: E731

    lines = g.pay_brain_debts(a, sent, enabled=True, post=ok, now=later)
    assert [ln.split(":")[0] for ln in lines] == ["долг памяти 2026-09-06_1000"], lines
    assert calls and all("2026-09-06_1000" in c for c in calls), "граф A платил чужую встречу"
    left = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sent.glob("*.pending")}
    assert left == {k: v for k, v in debts.items() if k != "2026-09-06_1000.pending"}, \
        "обработка графа A тронула долг графа B или долг без графа"

    calls.clear()
    lines = g.pay_brain_debts(b, sent, enabled=True, post=ok, now=later)
    assert sorted(lines) == ["долг памяти 2026-09-03_1000: память Чароита переотправлена после ревизии: "
                             "снято решений 0, ушло фактов 2",
                             "долг памяти 2026-09-04_1000: заметки встречи в графе нет — снят"], lines
    assert sorted(p.name for p in sent.glob("*.pending")) == ["2026-09-05_1000.pending"], \
        "долг без графа и без заметки не снимает никто — чей он, неизвестно"


def test_every_debt_writer_names_the_graph_and_the_debt_follows_the_meeting(tmp_path, monkeypatch):
    """Отказ замка и отправка пишут в долг граф встречи. Проект увёл встречу в соседний граф —
    отправка переписывает владельца: долг за прежним графом тот снял бы строкой «заметки нет»,
    и недосланные факты не ушли бы никогда (n615p8-key, Haiku C1). Долг своей оборванной
    отправки повтор, дославший всё, гасит (n615p8-key, Sonnet C-1)."""
    import fcntl
    a, b = tmp_path / "A", tmp_path / "B"
    for graph in (a, b):
        (graph / "Встречи").mkdir(parents=True)
    mark = tmp_path / "brain_sent" / "s.txt"
    debt = mark.with_suffix(".pending")
    dead = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down"))  # noqa: E731
    assert g.send_to_brain("s", "Т", [], [], ["D1"], mark, enabled=True, post=dead, graph=a) == 0
    assert debt.read_bytes() == (g._graph_key(a) + "\n").encode(), "долг отправки без графа"
    debt.unlink()
    monkeypatch.setattr(g, "SEND_LOCK_WAIT", 0.0)
    mark.parent.mkdir(exist_ok=True)
    with open(mark.with_suffix(".lock"), "a+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert g.send_to_brain("s", "Т", [], [], ["D1"], mark, enabled=True, post=dead, graph=a) == 0
    assert debt.read_bytes() == (g._graph_key(a) + "\n").encode(), "долг при отказе замка без графа"

    sends = []

    def second_fails(url, json, timeout):
        sends.append(url)
        if len(sends) == 2:
            raise ConnectionError("down")
        return _Resp()

    assert g.send_to_brain("s", "Т", [], [], ["D1", "D2"], mark, enabled=True, post=second_fails, graph=b) == 1
    assert debt.read_bytes() == (g._graph_key(b) + "\n").encode(), "долг остался за прежним графом"
    (b / "Встречи" / "s.md").write_text("# Встреча s — Т\n\n## Решения\n- 📌 D1\n- 📌 D2\n", encoding="utf-8")
    later = lambda: time.time() + g.DEBT_MIN_AGE + 1  # noqa: E731
    assert g.pay_brain_debts(a, mark.parent, enabled=True, post=lambda *x, **k: _Resp(),
                             now=later) == [] and debt.exists(), "прежний граф снял долг встречи"

    calls: list[str] = []
    ok = lambda url, json, timeout: calls.append(url) or _Resp()  # noqa: E731
    assert g.send_to_brain("s", "Т", [], [], ["D1", "D2"], mark, enabled=True, post=ok, graph=b) == 2
    assert len(calls) == 2 and not debt.exists(), "дославший всё повтор не погасил долг"


def test_a_debt_led_away_mid_queue_or_left_unowned_by_a_failed_write_is_kept(tmp_path, monkeypatch):
    """n615p8-key2. Sonnet I-1: владелец читался при сборе очереди, а снималось позже — отправитель
    графа B успевал увести долг к себе, и граф A снимал его «заметки нет». Haiku C-1: долг отправителя
    не поставлен (OSError) — полное покрытие не гасит лежащий чужой заказ."""
    import os
    sent = tmp_path / "brain_sent"
    sent.mkdir()
    a, b = tmp_path / "A", tmp_path / "B"
    for graph in (a, b):
        (graph / "Встречи").mkdir(parents=True)
    (a / "Встречи" / "2026-09-01_1000.md").write_text(BEFORE.replace("2026-09-11_1533", "2026-09-01_1000"),
                                                      encoding="utf-8")
    first = g._set_debt(sent / "2026-09-01_1000.txt", a)
    led = sent / "2026-09-02_1000.pending"
    g._set_debt(led.with_suffix(".txt"), a)                     # встреча ушла в B, заметки в A нет
    os.utime(sent / "2026-09-01_1000.pending", ns=(first - 10**9, first - 10**9))

    def post(url, json, timeout):
        if url.endswith("/forget"):
            g._set_debt(led.with_suffix(".txt"), b)            # отправитель B посреди очереди A
        return _Resp()

    lines = g.pay_brain_debts(a, sent, enabled=True, post=post, now=lambda: time.time() + g.DEBT_MIN_AGE + 1)
    assert len(lines) == 1 and led.read_bytes() == (g._graph_key(b) + "\n").encode(), lines

    mark = sent / "s.txt"
    debt = mark.with_suffix(".pending")
    g._set_debt(mark, a)                                        # заказ /forget ревизии
    monkeypatch.setattr(g, "_set_debt", lambda *x, **k: None)
    assert g.send_to_brain("s", "Т", [], [], ["D1"], mark, enabled=True, post=lambda *x, **k: _Resp(), graph=a) == 2
    assert debt.exists(), "отправитель без своего долга снял чужой"
