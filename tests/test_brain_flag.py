"""Флаг записи во внешнюю память — `sufler.brain`.

Внешняя память — сервер `127.0.0.1:8100`, который Чароит не ставит: он есть только у того,
кто поставил его сам. Запись в него (факты встреч, голосовые заметки, дневник) идёт, только
если её включили; выключенная не оставляет ни замка, ни долга. Стирание и переименование
уже записанного флаг не гасит: факты лежат на сервере, пока их не удалят.

Проверка — записывающим шпионом, а не падающей подменой: сторож сети в conftest бросает
AssertionError, пути внешней памяти ловят Exception, и «не упало» ничего не доказывало бы
(входной круг, Opus C4).
"""
from __future__ import annotations

import io
import os
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import graph_updater  # noqa: E402
import install_profile  # noqa: E402

STAMP = "2026-07-15_1400"


class _Ok:
    status_code = 200
    headers = {"content-type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"text": "ok"}


def _spy():
    calls: list[tuple[str, dict]] = []

    def post(url, json=None, timeout=None, **kw):
        calls.append((url, json))
        return _Ok()

    return calls, post


@pytest.mark.parametrize("cfg, on", [
    ({}, False),
    ({"sufler": {}}, False),
    ({"sufler": {"brain": False}}, False),
    ({"sufler": {"brain": "false"}}, False),
    ({"sufler": {"brain": True}}, True),
    ({"sufler": {"brain": "true"}}, True),
])
def test_the_external_memory_is_written_only_when_turned_on(cfg, on):
    # сервер есть только у того, кто поставил его сам: у остальных запись копила бы замок и
    # долг на каждую встречу (входной круг, Opus C3)
    assert install_profile.brain_enabled(cfg) is on


@pytest.mark.parametrize("cfg, explicit", [
    ({"sufler": {"brain": False}}, True),
    ({"sufler": {"brain": "нет"}}, True),
    ({"sufler": {"brain": True}}, True),
    ({"sufler": {}}, False),
    ({}, False),
    # ключ без значения и мусор — не «false»: строка «(sufler.brain: false)» соврала бы
    # (круг 1 по коду, Opus M1)
    ({"sufler": {"brain": None}}, False),
    ({"sufler": {"brain": "мусор"}}, False),
    # пустой YAML, не словарь и «sufler:» без значения — тоже конфиг, падать нельзя (Opus C1)
    (None, False),
    (["список"], False),
    ({"sufler": None}, False),
    ({"sufler": ["список"]}, False),
])
def test_the_off_line_is_said_only_when_the_key_is_set(cfg, explicit):
    assert install_profile.brain_explicit(cfg) is explicit
    assert install_profile.brain_enabled(cfg) is (explicit and install_profile.flag(cfg, "brain", False))


def _meeting(tmp_path):
    sent = tmp_path / "logs" / "brain_sent"
    facts = (STAMP, "Платёжный провайдер", [{"имя": "Мария"}], ["провайдер"], ["выбрали ЮPay"])
    return facts, sent / f"{STAMP}.txt"


def test_a_disabled_flag_sends_nothing_and_leaves_no_lock_or_debt(tmp_path):
    calls, post = _spy()
    facts, mark = _meeting(tmp_path)
    assert graph_updater.send_to_brain(*facts, mark, post=post, enabled=False) == 0
    assert calls == []
    assert not mark.parent.exists() or sorted(mark.parent.iterdir()) == [], \
        "выключенная запись не ставит ни замка, ни долга"


def test_an_enabled_flag_still_sends_the_facts(tmp_path):
    calls, post = _spy()
    facts, mark = _meeting(tmp_path)
    assert graph_updater.send_to_brain(*facts, mark, post=post, enabled=True) > 0
    assert calls and all(url.endswith("/remember") for url, _ in calls)


def test_the_writer_has_to_be_told_whether_the_memory_is_on(tmp_path):
    # решение вызывающего обязательное: кто не решил, получает TypeError, а не запись
    facts, mark = _meeting(tmp_path)
    with pytest.raises(TypeError):
        graph_updater.send_to_brain(*facts, mark)


def test_review_resend_and_debts_stay_still_when_disabled(tmp_path):
    calls, post = _spy()
    sent = tmp_path / "logs" / "brain_sent"
    sent.mkdir(parents=True)
    (sent / "2026-07-10_0900.pending").touch()
    note = tmp_path / "граф" / "Встречи" / f"{STAMP}.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Встреча\n\n## Решения\n- выбрали ЮPay\n", encoding="utf-8")
    assert graph_updater.resend_to_brain_after_review(
        STAMP, note, "", sent / f"{STAMP}.txt", post=post, enabled=False) is None
    assert graph_updater.pay_brain_debts(tmp_path / "граф", sent, enabled=False, post=post,
                                         now=lambda: 10 ** 10) == []
    assert calls == []
    assert sorted(p.name for p in sent.iterdir()) == ["2026-07-10_0900.pending"], \
        "долг остаётся списком неотправленного"


def test_the_review_worker_pays_no_debts_when_disabled(tmp_path):
    import cloud_review
    sent = cloud_review._root() / "logs" / "brain_sent"
    sent.mkdir(parents=True)
    debt = sent / "2026-07-10_0900.pending"
    debt.touch()
    # долг созревший, а заметки встречи в графе нет: включённая оплата сняла бы его строкой
    # в лог — выключенная не трогает ни долга, ни лога (свежий долг молчал бы при любом флаге,
    # и мутант «долги платятся всегда» выживал)
    os.utime(debt, (0, 0))
    log = tmp_path / "ревизия.log"
    # флаг — из конфига прогона, читает его сам воркер (Opus I1 круга 1 по коду)
    cloud_review._pay_brain_debts(STAMP, tmp_path / "граф", log, {"sufler": {"brain": False}})
    assert not log.exists(), "выключенная память не пишет в лог ревизии ни строки"
    assert debt.exists(), "долг остаётся списком неотправленного"
    cloud_review._pay_brain_debts(STAMP, tmp_path / "граф", log, {"sufler": {"brain": True}})
    assert "заметки встречи в графе нет" in log.read_text(encoding="utf-8"), \
        "та же сцена при включённой — оплата идёт: без этого тест выше ничего не доказывает"


@pytest.mark.parametrize("cfg, sends, said", [
    ({"sufler": {"brain": False}}, False, True),
    ({}, False, False),
    ({"sufler": {"brain": True}}, True, False),
])
def test_the_meeting_step_reads_the_flag_itself(tmp_path, monkeypatch, capsys, cfg, sends, said):
    # шаг разбора «факты → внешняя память» сам читает флаг из конфига разбора: мутант «флаг на
    # месте вызова всегда да» выживал (круг 1 по коду, Opus I1)
    calls, post = _spy()
    monkeypatch.setattr(graph_updater.requests, "post", post)
    monkeypatch.setattr(graph_updater, "_root", lambda: tmp_path)
    facts, _ = _meeting(tmp_path)
    n = graph_updater.meeting_facts_to_brain(cfg, *facts)
    assert bool(calls) is sends and (n > 0) is sends
    sent_dir = tmp_path / "logs" / "brain_sent"
    assert sends or not sent_dir.exists() or sorted(sent_dir.iterdir()) == []
    assert ("внешняя память выключена" in capsys.readouterr().out) is said


@pytest.mark.parametrize("cfg, sends", [({"sufler": {"brain": False}}, False), ({"sufler": {"brain": True}}, True)])
def test_the_review_worker_resends_only_when_turned_on(tmp_path, monkeypatch, cfg, sends):
    # переотправку после ревизии воркер решает по конфигу прогона сам (Opus I1 круга 1)
    import cloud_review
    calls, post = _spy()
    monkeypatch.setattr(graph_updater.requests, "post", post)
    note = tmp_path / "граф" / "Встречи" / f"{STAMP}.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Встреча\n\n## Решения\n- 📌 выбрали ЮPay\n", encoding="utf-8")
    said = cloud_review._resend_to_brain(STAMP, note, "", cfg)
    assert bool(calls) is sends
    assert (said is None) is not sends
    # строка лога ревизии целиком: префикс воркера и перевод строки
    assert said is None or (said.startswith("[cloud-review] память Чароита") and said.endswith("\n"))


def _dictate(monkeypatch, tmp_path, *argv: str, on: bool, text: str, timeouts: list | None = None):
    import dictate_note
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(tmp_path / "граф"))
    monkeypatch.setenv("SUFLER_DIARY_DIR", str(tmp_path / "Дневник"))
    monkeypatch.setenv("SUFLER_TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    конфиг = {"sufler": {"diary_dir": "", "brain": on}, "stt": {"backend": "gigaam"}}
    monkeypatch.setattr(dictate_note, "cfg", lambda: конфиг)
    monkeypatch.setattr(dictate_note, "_llm", lambda: types.SimpleNamespace(
        complete=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("модели нет"))))
    calls, post = _spy()
    if timeouts is not None:
        spy = post

        def post(url, json=None, timeout=None, **kw):
            timeouts.append(timeout)
            return spy(url, json=json, timeout=timeout, **kw)
    monkeypatch.setattr(dictate_note.requests, "post", post)
    monkeypatch.setattr(dictate_note.sys, "argv", ["dictate_note.py", *argv])
    monkeypatch.setattr(dictate_note.sys, "stdin", io.StringIO(text))
    dictate_note.main()
    return calls


@pytest.mark.parametrize("on", [False, True])
def test_voice_notes_and_the_diary_reach_the_memory_only_when_turned_on(tmp_path, monkeypatch, capfd, on):
    # два писателя, которых постановка не видела: голосовая заметка и дневник шли в память
    # прямым запросом без отметки и замка (входной круг, обе головы)
    (tmp_path / "transcripts").mkdir()
    note = _dictate(monkeypatch, tmp_path, "--text", on=on, text="проверить счётчики")
    diary = _dictate(monkeypatch, tmp_path, "--diary", "--text", on=on, text="мысль про запуск")
    assert [c[1]["category"] for c in note + diary] == (["voice_note", "diary"] if on else [])
    capfd.readouterr()


def test_the_memory_gets_the_text_and_a_short_timeout(tmp_path, monkeypatch, capfd):
    # включённая запись несёт сам текст (с потолком), и зависший сервер не держит диктовку
    # (мутатор по всему диапазону №249: срез текста и таймаут не проверял ни один тест)
    (tmp_path / "transcripts").mkdir()
    seen: list = []
    long = "мысль " * 80
    for argv in (("--text",), ("--diary", "--text")):
        calls = _dictate(monkeypatch, tmp_path, *argv, on=True, text=long,
                         timeouts=seen)
        [(url, payload)] = calls
        # тело, а не только заголовок: в заголовке заметки — три первых слова
        assert payload["text"].count("мысль") > 10 and len(payload["text"]) < len(long)
    assert len(seen) == 2 and all(0 < t <= 10 for t in seen)
    capfd.readouterr()
