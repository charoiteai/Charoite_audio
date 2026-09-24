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


def test_the_off_line_is_said_only_when_the_key_is_set():
    assert install_profile.brain_explicit({"sufler": {"brain": False}})
    assert not install_profile.brain_explicit({"sufler": {}})
    assert not install_profile.brain_explicit({})


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
    (sent / "2026-07-10_0900.pending").touch()
    log = tmp_path / "ревизия.log"
    cloud_review._pay_brain_debts(STAMP, tmp_path / "граф", log, enabled=False)
    assert not log.exists(), "выключенная память не пишет в лог ревизии ни строки"


def _dictate(monkeypatch, tmp_path, *argv: str, on: bool, text: str):
    import dictate_note
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(tmp_path / "граф"))
    monkeypatch.setenv("SUFLER_DIARY_DIR", str(tmp_path / "Дневник"))
    monkeypatch.setenv("SUFLER_TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    конфиг = {"sufler": {"diary_dir": "", "brain": on}, "stt": {"backend": "gigaam"}}
    monkeypatch.setattr(dictate_note, "cfg", lambda: конфиг)
    monkeypatch.setattr(dictate_note, "_llm", lambda: types.SimpleNamespace(
        complete=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("модели нет"))))
    calls, post = _spy()
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
