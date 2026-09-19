"""№314: саммари архива — производная с паспортом (вид `summary`) в сайдкаре
стенограммы. Источник — канон материалов (обрезки минуток/тезисов/разбора/
хвоста стенограммы, список решений, оговорка о записи), не речь: речь после
встречи не меняется, а минутки меняет ревизия — замер 19.09: 74 из 298
саммари боевого архива были старше своих минуток. Единственный писатель
производных с паспортом — `live_sidecar.write_derivative`; механическая
перезапись (переименование встречи) — `live_sidecar.retouch`.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import channel_trace  # noqa: E402
import live_sidecar  # noqa: E402
import meeting_archive as ma  # noqa: E402
import retro_fill  # noqa: E402

NOTE = channel_trace.SUMMARY_MARK + ": без собеседников 10:00–10:05 (эпизодов 1, всего 5 мин)"


def _folder(tmp_path, minutes="## Решения\n1. Первое решение принято.\n"):
    folder = tmp_path / "2026-09-19 10-00 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text(minutes, encoding="utf-8")
    (folder / "Стенограмма.md").write_text("# Встреча\n\n[10:00:00] Иван: начнём\n", encoding="utf-8")
    live = tmp_path / "2026-09-19_1000_тема.md"
    live.write_text("# Встреча 2026-09-19_1000\n\n[10:00:00] Иван: начнём\n", encoding="utf-8")
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


def test_summary_is_built_once_then_fresh_until_materials_change(tmp_path, monkeypatch):
    folder, live = _folder(tmp_path)
    calls: list = []
    _fake_model(monkeypatch, calls)
    state = ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE)
    assert state == live_sidecar.MISSING and len(calls) == 1
    out = folder / "Саммари.md"
    assert out.exists() and "Первое" in out.read_text(encoding="utf-8")
    meta = live_sidecar.read(live)
    assert live_sidecar.valid_sha(meta["summary_sha256"]) and live_sidecar.valid_sha(meta["summary_source_sha256"])
    # тот же источник — FRESH, модель не зовётся ни живой, ни ретро-политикой
    assert ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE) == live_sidecar.FRESH
    assert ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_RETRO) == live_sidecar.FRESH
    assert len(calls) == 1
    # ревизия переписала минутки — STALE, пересборка, прежняя версия в .prev/ у стенограммы
    (folder / "Минутки.md").write_text("## Решения\n1. Решение отменено ревизией.\n", encoding="utf-8")
    assert ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_RETRO) == live_sidecar.STALE
    assert len(calls) == 2
    prev = live_sidecar.prev_path(live, out)
    assert prev.exists() and prev.parent.name == ".prev" and prev.parent.parent == live.parent
    # правка руками — HUMAN, не трогается ни одной политикой
    out.write_text(out.read_text(encoding="utf-8") + "\nМоя правка.\n", encoding="utf-8")
    (folder / "Минутки.md").write_text("## Решения\n1. Ещё одно.\n", encoding="utf-8")
    assert ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE) == live_sidecar.HUMAN
    assert len(calls) == 2 and "Моя правка." in out.read_text(encoding="utf-8")


def test_source_canon_is_inputs_not_prompt_words_and_note_reaches_prompt_and_document(tmp_path, monkeypatch):
    """Important DS / Minor GLM входного круга: в хеш — канон входов, не рендер
    промпта (слова шаблона и язык конфига не старят саммари); оговорка —
    блоком факта после материалов и строкой в документе (№317)."""
    folder, live = _folder(tmp_path)
    mats = ma.summary_materials(folder)
    decided = ma.decisions_of(folder)
    a = ma.summary_source_sha(mats, decided, None)
    assert a == ma.summary_source_sha(mats, decided, None), "детерминирован"
    assert a != ma.summary_source_sha(mats, decided, NOTE), "оговорка — часть источника"
    assert a != ma.summary_source_sha(mats, [], None), "список решений — часть источника"
    src = (ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8")
    canon = src[src.index("def summary_source_sha("):src.index("def _legacy_summary_consistent(")]
    assert "_config_lang" not in canon and "SUMMARY_SECTIONS" not in canon and "_history_context" not in canon
    calls: list = []
    _fake_model(monkeypatch, calls)
    ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE, recording_note=NOTE)
    prompt = calls[0]
    assert NOTE in prompt and prompt.index("</материалы>") < prompt.index(NOTE), "блок факта — после материалов"
    text = (folder / "Саммари.md").read_text(encoding="utf-8")
    assert text.count(channel_trace.SUMMARY_MARK) == 1


def test_legacy_summary_gets_a_passport_without_the_model_when_materials_are_older(tmp_path, monkeypatch):
    """Critical DS входного круга: у 298 саммари боевого архива паспортов нет;
    живая политика переписала бы моделью все — и правленные руками тоже.
    Согласованное легаси (материалы не новее саммари) аттестуется на текущие
    байты без модели; протухшее (минутки новее) остаётся UNKNOWN → живой путь
    пересоберёт, ретро — пропустит."""
    folder, live = _folder(tmp_path)
    out = folder / "Саммари.md"
    out.write_text("# Саммари — старое\n\nСуть: было.\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(folder / "Минутки.md", (old, old))
    os.utime(folder / "Стенограмма.md", (old, old))
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE) == live_sidecar.FRESH
    assert calls == [] and "было" in out.read_text(encoding="utf-8")
    assert live_sidecar.derivative_state(out, live_sidecar.read(live), "summary",
                                         ma.summary_source_sha(ma.summary_materials(folder),
                                                               ma.decisions_of(folder), None)) == live_sidecar.FRESH
    # протухшее легаси: минутки новее саммари
    folder2 = tmp_path / "2026-09-19 11-00 — Другая"
    folder2.mkdir()
    (folder2 / "Саммари.md").write_text("# Саммари — старое\n\nСуть: было.\n", encoding="utf-8")
    os.utime(folder2 / "Саммари.md", (old, old))
    (folder2 / "Минутки.md").write_text("## Решения\n1. Свежее.\n", encoding="utf-8")
    live2 = tmp_path / "2026-09-19_1100.md"
    live2.write_text("# Встреча\n", encoding="utf-8")
    assert ma._gen_summary(folder2, live2, policy=ma.SUMMARY_POLICY_RETRO) == live_sidecar.UNKNOWN
    assert calls == [], "ретро не бэкфиллит UNKNOWN (№309)"
    assert ma._gen_summary(folder2, live2, policy=ma.SUMMARY_POLICY_LIVE) == live_sidecar.UNKNOWN
    assert len(calls) == 1 and "Свежее" in calls[0], "живой путь пересобрал протухшее легаси"


def test_write_seam_lives_in_live_sidecar_and_refuses_orphan_passports(tmp_path):
    """Important DS/GLM входного круга: второго шва записи нет — retro_fill зовёт
    live_sidecar; паспорт пишется только живой стенограмме."""
    src = (ROOT / "src" / "retro_fill.py").read_text(encoding="utf-8")
    assert "live_sidecar.write_derivative(" in src and "safe_write.stat_snapshot(path)" not in src
    assert "def prev_path" in (ROOT / "src" / "live_sidecar.py").read_text(encoding="utf-8")
    assert retro_fill.prev_path(tmp_path / "a.md", tmp_path / "x" / "Тезисы.md") == \
        live_sidecar.prev_path(tmp_path / "a.md", tmp_path / "x" / "Тезисы.md")
    ghost = tmp_path / "нет.md"
    out = tmp_path / "Саммари.md"
    assert live_sidecar.write_derivative(ghost, out, "summary", "тело", "a" * 64, log=lambda m: None)
    assert out.read_text(encoding="utf-8") == "тело"
    assert not live_sidecar._direct(ghost).exists(), "сайдкар без владельца не создаётся"


def test_retouch_keeps_the_passport_alive_on_mechanical_rewrite(tmp_path, monkeypatch):
    """Critical DS и GLM входного круга: rename_meeting переписывал байты
    Саммари.md голым replace — с паспортом файл замер бы в HUMAN навсегда.
    retouch переставляет хеш байтов при прежнем источнике; чужое (HUMAN,
    без паспорта) переписывает, паспорт не присваивает."""
    folder, live = _folder(tmp_path)
    calls: list = []
    _fake_model(monkeypatch, calls)
    ma._gen_summary(folder, live, policy=ma.SUMMARY_POLICY_LIVE)
    out = folder / "Саммари.md"
    before = live_sidecar.read(live)["summary_sha256"]
    assert live_sidecar.retouch(live, "summary", out, lambda t: t.replace("Тема", "Новая тема"))
    after = live_sidecar.read(live)
    assert after["summary_sha256"] != before and after["summary_sha256"] == live_sidecar.sha(out.read_text(encoding="utf-8"))
    src_sha = ma.summary_source_sha(ma.summary_materials(folder), ma.decisions_of(folder), None)
    assert live_sidecar.derivative_state(out, after, "summary", src_sha) == live_sidecar.FRESH
    # HUMAN: байты не наши — перепишем текст, паспорт не тронем
    out.write_text(out.read_text(encoding="utf-8") + "\nручное\n", encoding="utf-8")
    assert live_sidecar.retouch(live, "summary", out, lambda t: t.replace("ручное", "рукой"))
    assert "рукой" in out.read_text(encoding="utf-8")
    assert live_sidecar.read(live)["summary_sha256"] == after["summary_sha256"]
    assert live_sidecar.derivative_state(out, live_sidecar.read(live), "summary", src_sha) == live_sidecar.HUMAN
    # rename_meeting идёт через retouch для паспортных файлов
    rn = (ROOT / "scripts" / "rename_meeting.py").read_text(encoding="utf-8")
    assert "live_sidecar.retouch(live, kind, f, swap)" in rn and '"Саммари.md": "summary"' in rn


def test_archive_meeting_threads_policy_and_records_summary_state(tmp_path, monkeypatch):
    """Политика — параметр archive_meeting (Critical GLM входного круга),
    манифест несёт summary_state (критика GLM / Important DS)."""
    src = (ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8")
    assert "policy: frozenset[str] | None = None" in src
    assert "policy=policy or SUMMARY_POLICY_RETRO" in src
    assert '"summary_state": summary_state' in src
    gu = (ROOT / "src" / "graph_updater.py").read_text(encoding="utf-8")
    cr = (ROOT / "scripts" / "cloud_review.py").read_text(encoding="utf-8")
    assert "policy=SUMMARY_POLICY_LIVE" in gu and "policy=SUMMARY_POLICY_LIVE" in cr
    assert live_sidecar.FRESH not in ma.SUMMARY_POLICY_LIVE, "FRESH-пересборка саммари — шум (критика GLM)"
    folder, _ = _folder(tmp_path)
    m = ma.build_manifest(folder, "2026-09-19_1000", "Тема", summary_state=live_sidecar.STALE)
    assert m["summary_state"] == live_sidecar.STALE
    assert ma.build_manifest(folder, "2026-09-19_1000", "Тема")["summary_state"] is None


def test_without_live_the_old_behaviour_stays(tmp_path, monkeypatch):
    """Тесты и миграция зовут `_gen_summary(folder)` без стенограммы — паспорта
    нет, поведение прежнее: собрать, если файла нет или он пуст."""
    folder, _ = _folder(tmp_path)
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder) is None and len(calls) == 1
    assert ma._gen_summary(folder) is None and len(calls) == 1, "непустой файл — не пересобирать"
    assert ma._gen_summary(folder, force=True) is None and len(calls) == 2
