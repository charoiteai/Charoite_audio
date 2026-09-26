"""№314: саммари архива — производная с паспортом (вид `summary`) в сайдкаре
стенограммы. Источник — канон материалов (обрезки минуток/тезисов/разбора/
хвоста стенограммы, список решений, оговорка о записи), не речь: речь после
встречи не меняется, а минутки меняет ревизия — замер 19.09: 74 из 298
саммари боевого архива были старше своих минуток. Единственный писатель
производных с паспортом — `live_sidecar.write_derivative`; механическая
перезапись (переименование встречи) — `live_sidecar.retouch`.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

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
    state = ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD)
    # возвращается состояние ПОСЛЕ записи — от шва, не «то, что было» (Important
    # DS и GLM выходного круга: манифест называл свежее саммари missing/stale)
    assert state == live_sidecar.FRESH and len(calls) == 1
    out = folder / "Саммари.md"
    assert out.exists() and "Первое" in out.read_text(encoding="utf-8")
    meta = live_sidecar.read(live)
    assert live_sidecar.valid_sha(meta["summary_sha256"]) and live_sidecar.valid_sha(meta["summary_source_sha256"])
    # тот же источник — FRESH, модель не зовётся ни живой, ни ретро-политикой
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.AUTO) == live_sidecar.FRESH
    assert len(calls) == 1
    # ревизия переписала минутки — STALE, пересборка, прежняя версия в .prev/ у стенограммы
    (folder / "Минутки.md").write_text("## Решения\n1. Решение отменено ревизией.\n", encoding="utf-8")
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.AUTO) == live_sidecar.FRESH
    assert len(calls) == 2
    prev = live_sidecar.prev_path(live, out)
    assert prev.exists() and prev.parent.name == ".prev" and prev.parent.parent == live.parent
    # правка руками — HUMAN, не трогается ни одной политикой
    out.write_text(out.read_text(encoding="utf-8") + "\nМоя правка.\n", encoding="utf-8")
    (folder / "Минутки.md").write_text("## Решения\n1. Ещё одно.\n", encoding="utf-8")
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.HUMAN
    assert len(calls) == 2 and "Моя правка." in out.read_text(encoding="utf-8")
    # и явная пересборка — тоже: REBUILD строит UNKNOWN, но не «перепиши человека»
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.HUMAN
    assert len(calls) == 2


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
    canon = src[src.index("def summary_source_sha("):src.index("SUMMARY_HEAD = ")]
    assert "_config_lang" not in canon and "SUMMARY_SECTIONS" not in canon and "_history_context" not in canon
    calls: list = []
    _fake_model(monkeypatch, calls)
    ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD, recording_note=NOTE)
    prompt = calls[0]
    assert NOTE in prompt and prompt.index("</материалы>") < prompt.index(NOTE), "блок факта — после материалов"
    text = (folder / "Саммари.md").read_text(encoding="utf-8")
    assert text.count(channel_trace.SUMMARY_MARK) == 1


def test_legacy_summary_is_adopted_only_by_the_explicit_command_and_by_the_whole_canon(tmp_path, monkeypatch):
    """Схождение DS и GLM выходного круга: присвоение легаси по mtime в живом
    пути — третий смысл паспорта, и оно замораживало саммари без оговорки о
    записи (Critical GLM). Живой путь UNKNOWN не присваивает — строит по
    политике; присваивает только `adopt_summary` (retro_fill --summary=adopt),
    по всему канону: материалы не новее, документ наш по структуре, оговорка
    пуста или уже в тексте. Присвоенное помечено `summary_adopted`."""
    folder, live = _folder(tmp_path)
    out = folder / "Саммари.md"
    legacy = "---\ntype: саммари\nдата: 2026-09-19\n---\n\n# Саммари — старое\n\nСуть: было.\n"
    out.write_text(legacy, encoding="utf-8")
    old = time.time() - 3600
    for name in ("Минутки.md", "Стенограмма.md"):
        os.utime(folder / name, (old, old))
    calls: list = []
    _fake_model(monkeypatch, calls)
    # живой путь и обход (одна политика): UNKNOWN не присваивается и не строится —
    # незнание о легаси не повод переписывать его моделью на первом касании
    # (критика DS и GLM круга 2, схождение)
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.AUTO) == live_sidecar.UNKNOWN
    assert calls == []
    assert "summary_adopted" not in (live_sidecar.read(live) or {})
    # REBUILD сначала присваивает исправное (материалы не новее) — без модели
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    assert calls == [] and "было" in out.read_text(encoding="utf-8") and live_sidecar.read(live)["summary_adopted"]
    # протухшее (материалы новее) REBUILD строит моделью
    (folder / "Минутки.md").write_text("## Решения\n1. Свежее решение.\n", encoding="utf-8")
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    assert len(calls) == 1 and "было" not in out.read_text(encoding="utf-8")
    # обход UNKNOWN не трогает (№309)
    folder2 = tmp_path / "2026-09-19 11-00 — Другая"
    folder2.mkdir()
    (folder2 / "Минутки.md").write_text("## Решения\n1. Первое решение принято.\n", encoding="utf-8")
    (folder2 / "Стенограмма.md").write_text("# Встреча\n\n[11:00:00] Иван: начнём\n", encoding="utf-8")
    (folder2 / "Саммари.md").write_text(legacy, encoding="utf-8")
    for name in ("Минутки.md", "Стенограмма.md"):
        os.utime(folder2 / name, (old, old))
    live2 = tmp_path / "2026-09-19_1100.md"
    live2.write_text("# Встреча\n", encoding="utf-8")
    assert ma._gen_summary(folder2, live2, mode=ma.SummaryMode.AUTO) == live_sidecar.UNKNOWN
    assert len(calls) == 1
    # явное присвоение согласованного легаси — без модели, с отметкой
    assert ma.adopt_summary(folder2, live2, None) == "присвоено"
    meta = live_sidecar.read(live2)
    assert meta["summary_adopted"].startswith("2026") and "было" in (folder2 / "Саммари.md").read_text(encoding="utf-8")
    assert ma.summary_state(folder2, live2, None) == live_sidecar.FRESH
    assert ma.adopt_summary(folder2, live2, None) == "уже с паспортом (fresh)"
    assert len(calls) == 1
    # отказы — по всему канону, причина словами
    folder3 = tmp_path / "2026-09-19 12-00 — Третья"
    folder3.mkdir()
    (folder3 / "Минутки.md").write_text("## Решения\n1. Свежее.\n", encoding="utf-8")
    (folder3 / "Саммари.md").write_text(legacy, encoding="utf-8")
    os.utime(folder3 / "Саммари.md", (old, old))
    live3 = tmp_path / "2026-09-19_1200.md"
    live3.write_text("# Встреча\n", encoding="utf-8")
    assert ma.adopt_summary(folder3, live3, None) == "материалы новее: Минутки.md"
    os.utime(folder3 / "Минутки.md", (old - 10, old - 10))
    assert ma.adopt_summary(folder3, live3, NOTE) == "без оговорки о записи", "Critical GLM: паспорт без ноты замёрз бы"
    (folder3 / "Саммари.md").write_text(legacy + "\n" + NOTE + "\n", encoding="utf-8")
    os.utime(folder3 / "Саммари.md", (old, old))
    assert ma.adopt_summary(folder3, live3, NOTE) == "присвоено", "оговорка уже в тексте — канон полный"
    (folder3 / "Саммари.md").write_text("# Чужой файл\n", encoding="utf-8")
    live4 = tmp_path / "2026-09-19_1300.md"
    live4.write_text("# Встреча\n", encoding="utf-8")
    assert ma.adopt_summary(folder3, live4, None) == "не наш документ"
    (folder3 / "Саммари.md").write_text("", encoding="utf-8")
    assert ma.adopt_summary(folder3, live4, None) == "файл пуст", "пустой файл — MISSING, не легаси (слова — Minor GLM круга 2)"
    (folder3 / "Саммари.md").unlink()
    assert ma.adopt_summary(folder3, live4, None) == "файла нет"
    assert "summary_adopted" not in (live_sidecar.read(live4) or {})


def test_legacy_summary_adopted_over_a_marked_canon_survives_the_owner_unmarking(tmp_path, monkeypatch):
    """Саммари без паспорта при каноне с отметками (№392): присвоение берёт хеш
    нормализованного канона, и снятая владельцем отметка паспорт не старит. Без
    нормализации хеш присвоения нёс бы «[x]», срок и пометку контроля — и первый же
    возврат пункта во вкладке «Задачи» гнал бы модель пересобирать саммари
    (Important DS круга 1 по PR #641: AUTO-тест от отката правки не краснел)."""
    marked = ("## Решения\n1. Первое решение принято.\n\n## Поручения\n"
              "- [x] **Аня** — подготовить отчёт 📅 2026-10-01 _(снято по сроку 24.09)_\n")
    folder, live = _folder(tmp_path, minutes=marked)
    out = folder / "Саммари.md"
    out.write_text("---\ntype: саммари\nдата: 2026-09-19\n---\n\n# Саммари — старое\n\nСуть: было.\n",
                   encoding="utf-8")
    old = time.time() - 3600
    for name in ("Минутки.md", "Стенограмма.md"):
        os.utime(folder / name, (old, old))
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD) == live_sidecar.FRESH
    assert calls == [] and live_sidecar.read(live)["summary_adopted"], "присвоено без модели"
    (folder / "Минутки.md").write_text(
        marked.replace("- [x] **Аня** — подготовить отчёт 📅 2026-10-01 _(снято по сроку 24.09)_",
                       "- [ ] **Аня** — подготовить отчёт"), encoding="utf-8")
    assert ma.summary_state(folder, live, None) == live_sidecar.FRESH
    assert ma._gen_summary(folder, live, mode=ma.SummaryMode.AUTO) == live_sidecar.FRESH
    assert calls == [] and "было" in out.read_text(encoding="utf-8")


def test_empty_derivative_is_missing_not_attestable(tmp_path):
    """Critical DS выходного круга: пустой файл — след оборванной записи; до
    паспорта `_gen_summary` проверял `st_size > 0`, с паспортом пустое саммари
    аттестовалось бы FRESH навсегда. Правило — у единственного оракула, для
    всех видов."""
    p = tmp_path / "Саммари.md"
    p.write_text("", encoding="utf-8")
    src = live_sidecar.sha("канон")
    assert live_sidecar.derivative_state(p, {}, "summary", src) == live_sidecar.MISSING
    meta = {"summary_sha256": live_sidecar.sha(""), "summary_source_sha256": src}
    assert live_sidecar.derivative_state(p, meta, "summary", src) == live_sidecar.MISSING, "паспорт на пустоту не спасает"
    assert live_sidecar.derivative_state(p, meta, "minutes", src) == live_sidecar.MISSING
    live = tmp_path / "2026-09-19_1000.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    assert live_sidecar.adopt(live, "summary", p, src) == "состояние missing, присваивать нечего"


def test_write_derivative_returns_the_state_after_writing(tmp_path):
    """Important DS и GLM выходного круга: шов знает исход записи — он и отдаёт
    состояние; вызывающий не пересобирает знание сам."""
    live = tmp_path / "2026-09-19_1000.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    out = tmp_path / "Саммари.md"
    src = live_sidecar.sha("канон")
    assert live_sidecar.write_derivative(live, out, "summary", "тело", src, log=lambda m: None).state == live_sidecar.FRESH
    wrote = live_sidecar.write_derivative(live, out, "summary", "тело 2", src, log=lambda m: None)
    assert (wrote.state, wrote.refused, wrote.written) == (live_sidecar.FRESH, None, True)
    # гонка: файл изменился под рукой между решением и записью — None, байты человека целы
    import safe_write
    real = safe_write.write_text
    def racing(path, body, **kw):
        if path == out and "expect" in kw:
            out.write_text("правка человека", encoding="utf-8")
        return real(path, body, **kw)
    import unittest.mock as um
    with um.patch.object(safe_write, "write_text", racing):
        wrote = live_sidecar.write_derivative(live, out, "summary", "тело 3", src, log=lambda m: None)
    assert (wrote.state, wrote.refused) == (None, live_sidecar.WriteOutcome.RACE)
    assert out.read_text(encoding="utf-8") == "правка человека"
    # прежняя версия не сохранилась (.prev — обычный файл): файл не тронут, причина PREV,
    # не «файл менялся под рукой» (Important DS круга 4)
    prev_dir = live_sidecar.prev_path(live, out).parent
    import shutil
    shutil.rmtree(prev_dir, ignore_errors=True)
    prev_dir.write_text("x", encoding="utf-8")
    wrote = live_sidecar.write_derivative(live, out, "summary", "тело 4", src, log=lambda m: None)
    assert (wrote.state, wrote.refused) == (None, live_sidecar.WriteOutcome.PREV)
    assert out.read_text(encoding="utf-8") == "правка человека"
    prev_dir.unlink()
    # причины MISSING — у оракула, одним stat
    assert live_sidecar.missing_reason(tmp_path / "нет.md") == "файла нет"
    (tmp_path / "пусто.md").write_text("", encoding="utf-8")
    assert live_sidecar.missing_reason(tmp_path / "пусто.md") == "файл пуст"


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
    assert live_sidecar.write_derivative(ghost, out, "summary", "тело", "a" * 64, log=lambda m: None).state == live_sidecar.UNKNOWN
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
    ma._gen_summary(folder, live, mode=ma.SummaryMode.REBUILD)
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
    # карта имя → вид — у владельца паспортов, не копия в скрипте (критика GLM
    # выходного круга). Что rename_meeting идёт через retouch для паспортных
    # файлов, пиннит поведение, а не написание: подмена retouch считает вызовы
    # (`test_canon_passport.test_rename_retouches_passport_files_only_with_a_transcript`, №366)
    rn = (ROOT / "scripts" / "rename_meeting.py").read_text(encoding="utf-8")
    assert '"Саммари.md": "summary"' not in rn and live_sidecar.ARCHIVE_KINDS["Саммари.md"] == "summary"
    # гейт expect: файл изменился под рукой между чтением и записью — отказ, правка цела
    def racing(text):
        out.write_text(text + "\nещё правка\n", encoding="utf-8")
        return text.replace("рукой", "руками")
    assert live_sidecar.retouch(live, "summary", out, racing) is False
    assert "ещё правка" in out.read_text(encoding="utf-8") and "руками" not in out.read_text(encoding="utf-8")


def test_archive_meeting_threads_the_mode_and_the_manifest_carries_no_copy_of_the_state(tmp_path, monkeypatch):
    """Режим прохода — значение перечисления от CLI до шва (Critical DS и GLM
    круга 3: пустая политика ложна, `policy or DEFAULT` на шве подменял
    «не строить ничего» дефолтом). Гейт — отрицательный: на швах записи нет
    подстановки политики по истинности. Одна политика на живой путь и обход
    (MISSING/STALE), UNKNOWN — только REBUILD. Состояния саммари в манифесте
    НЕТ: копия расходилась с паспортом после каждой записи и обнулялась
    переименованием (Important DS и GLM круга 1)."""
    src = (ROOT / "src" / "meeting_archive.py").read_text(encoding="utf-8")
    assert "policy or " not in src and "policy=policy" not in src, "политика не пересекает шов как множество"
    # дефолт режима живёт в сигнатуре, и на швах его не подставляют по истинности;
    # сигнатура читается как сигнатура, а не как строка исходника (она растёт: №361)
    import inspect
    assert inspect.signature(ma.archive_meeting).parameters["mode"].default is ma.SummaryMode.AUTO
    assert " or SummaryMode" not in src
    assert "summary_state=" not in src and '"summary_state"' not in src
    for name in ("graph_updater.py", "cloud_review.py"):
        text = (ROOT / ("src" if name == "graph_updater.py" else "scripts") / name).read_text(encoding="utf-8")
        assert "SUMMARY_POLICY" not in text and "SummaryMode" not in text, "живой путь идёт режимом по умолчанию"
    assert ma.SummaryMode.AUTO.policy == ma.SUMMARY_POLICY == live_sidecar.POLICY_RETRO
    assert ma.SummaryMode.REBUILD.policy == live_sidecar.POLICY_LIVE - {live_sidecar.FRESH}
    assert ma.SummaryMode.ADOPT.policy == frozenset() and not ma.SummaryMode.AUTO.adopts
    assert live_sidecar.UNKNOWN not in ma.SUMMARY_POLICY
    assert ma.SummaryMode("adopt") is ma.SummaryMode.ADOPT
    folder, live = _folder(tmp_path)
    assert "summary_state" not in ma.build_manifest(folder, "2026-09-19_1000", "Тема")
    assert ma.summary_state(folder, live, None) == live_sidecar.MISSING
    calls: list = []
    _fake_model(monkeypatch, calls)
    ma._gen_summary(folder, live)
    assert ma.summary_state(folder, live, None) == live_sidecar.FRESH
    assert ma.summary_state(tmp_path / "пусто", live, None) is None


def test_summary_pass_returns_the_outcome_as_a_value(tmp_path, monkeypatch):
    """Critical DS и Important GLM круга 2: исход одного прохода — значение
    (№277), а не состояние, из которого отчёт выводит «собрано/пропущено» задним
    числом. Таблица исходов по состояниям и политикам."""
    O = ma.SummaryOutcome
    folder, live = _folder(tmp_path)
    calls: list = []
    _fake_model(monkeypatch, calls)
    assert ma.summary_pass(tmp_path / "нет", live, None).action == O.NONE
    # MISSING: политика по умолчанию строит
    o = ma.summary_pass(folder, live, None)
    assert (o.action, o.state, o.made, o.line()) == (O.BUILT, live_sidecar.FRESH, True, "саммари")
    # FRESH: не трогаем ни одной политикой
    o = ma.summary_pass(folder, live, None, mode=ma.SummaryMode.REBUILD)
    assert (o.action, o.state, o.line()) == (O.KEPT, live_sidecar.FRESH, "саммари fresh") and len(calls) == 1
    # STALE: строим
    (folder / "Минутки.md").write_text("## Решения\n1. Иначе.\n", encoding="utf-8")
    assert ma.summary_pass(folder, live, None).action == O.BUILT and len(calls) == 2
    # HUMAN: KEPT даже при rebuild и adopt
    out = folder / "Саммари.md"
    out.write_text(out.read_text(encoding="utf-8") + "\nправка\n", encoding="utf-8")
    o = ma.summary_pass(folder, live, None, mode=ma.SummaryMode.REBUILD)
    assert (o.action, o.state) == (O.KEPT, live_sidecar.HUMAN) and len(calls) == 2
    # UNKNOWN: по умолчанию SKIPPED; adopt с негодным легаси — SKIPPED с причиной; rebuild — BUILT
    folder2 = tmp_path / "2026-09-19 11-00 — Другая"
    folder2.mkdir()
    (folder2 / "Минутки.md").write_text("## Решения\n1. Свежее.\n", encoding="utf-8")
    (folder2 / "Саммари.md").write_text("# чужой\n", encoding="utf-8")
    live2 = tmp_path / "2026-09-19_1100.md"
    live2.write_text("# Встреча\n", encoding="utf-8")
    o = ma.summary_pass(folder2, live2, None)
    assert (o.action, o.state, o.line()) == (O.SKIPPED, live_sidecar.UNKNOWN, "саммари unknown")
    o = ma.summary_pass(folder2, live2, None, mode=ma.SummaryMode.ADOPT)
    assert (o.action, o.reason, o.line()) == (O.SKIPPED, "не наш документ", "саммари unknown: не наш документ")
    assert len(calls) == 2, "adopt не платит модели"
    # ADOPT на MISSING — тоже без модели, причина словами (Important DS круга 3)
    (folder2 / "Саммари.md").unlink()
    o = ma.summary_pass(folder2, live2, None, mode=ma.SummaryMode.ADOPT)
    assert (o.action, o.state, o.reason) == (O.SKIPPED, live_sidecar.MISSING, "файла нет") and len(calls) == 2
    (folder2 / "Саммари.md").write_text("", encoding="utf-8")
    assert ma.summary_pass(folder2, live2, None, mode=ma.SummaryMode.ADOPT).reason == "файл пуст" and len(calls) == 2
    (folder2 / "Саммари.md").write_text("# чужой\n", encoding="utf-8")
    o = ma.summary_pass(folder2, live2, None, mode=ma.SummaryMode.REBUILD)
    assert (o.action, o.state) == (O.BUILT, live_sidecar.FRESH) and len(calls) == 3
    # FAILED: модель молчит — исход FAILED, состояние честное (MISSING)
    folder3 = tmp_path / "2026-09-19 12-00 — Третья"
    folder3.mkdir()
    (folder3 / "Минутки.md").write_text("## Решения\n1. Что-то.\n", encoding="utf-8")
    live3 = tmp_path / "2026-09-19_1200.md"
    live3.write_text("# Встреча\n", encoding="utf-8")
    _fake_model(monkeypatch, calls, answer="")
    o = ma.summary_pass(folder3, live3, None)
    assert (o.action, o.state, o.made, o.reason) == (O.FAILED, live_sidecar.MISSING, False, "модель не ответила")
    assert o.line() == "саммари — модель не ответила"
    # REFUSED: файл появился под рукой за время генерации — повтор бессмыслен, различаем (критика GLM круга 3)
    _fake_model(monkeypatch, calls)
    import safe_write
    real = safe_write.write_text
    out3 = folder3 / "Саммари.md"
    def racing(path, body, **kw):
        if path == out3 and "expect" in kw:
            out3.write_text("правка человека", encoding="utf-8")
        return real(path, body, **kw)
    monkeypatch.setattr(safe_write, "write_text", racing)
    o = ma.summary_pass(folder3, live3, None)
    assert o.action == O.REFUSED and o.state == live_sidecar.UNKNOWN and "отклонена" in o.reason
    assert out3.read_text(encoding="utf-8") == "правка человека"
    # сбой сохранения прежней версии — FAILED (повтор имеет смысл), файл не тронут (Important DS круга 4)
    monkeypatch.setattr(safe_write, "write_text", real)
    out3.unlink()
    prev_dir = live_sidecar.prev_path(live3, out3).parent
    prev_dir.mkdir(exist_ok=True)
    out3.write_text("старое машинное", encoding="utf-8")
    live_sidecar.attest(live3, "summary", "старое машинное", "1" * 64)          # STALE: паспорт на другой канон
    import shutil
    shutil.rmtree(prev_dir)
    prev_dir.write_text("x", encoding="utf-8")
    o = ma.summary_pass(folder3, live3, None)
    assert (o.action, o.state) == (O.FAILED, live_sidecar.STALE) and "прежняя версия" in o.reason
    assert out3.read_text(encoding="utf-8") == "старое машинное"
    prev_dir.unlink()
    # голая строка режима нормализуется на границе шва, мусор — ValueError (Minor DS круга 4)
    assert ma.summary_pass(folder3, live3, None, mode="adopt").action == O.SKIPPED
    with pytest.raises(ValueError):
        ma.summary_pass(folder3, live3, None, mode="all")


def test_archive_meeting_returns_the_summary_outcome_and_adopts_after_copying_materials(tmp_path, monkeypatch):
    """Important DS круга 2: присвоение по папке другого резолвера и ДО
    обновления копий материалов давало паспорт на старый канон — модель всё
    равно работала, а отчёт молчал. Теперь присвоение внутри `archive_meeting`
    после копий; исход — в `Archived.summary`."""
    import os
    import time
    graph = tmp_path / "graph"
    (graph / ma.ARCHIVE_DIR).mkdir(parents=True)
    (graph / "Встречи").mkdir()
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-09-19_1000_Тема.md"
    live.write_text("# Встреча 2026-09-19_1000 — Тема\n\n[10:00:00] Иван: начнём\n", encoding="utf-8")
    (tdir / "2026-09-19_1000_minutes.md").write_text("## Решения\n1. Первое решение принято.\n", encoding="utf-8")
    # легаси-папка со старым форматом имени и саммари старше материалов; копия минуток
    # в папке ОТСТАЛА от tdir — канон после копирования другой
    legacy = graph / ma.ARCHIVE_DIR / "2026-09-19 — Тема"
    legacy.mkdir()
    (legacy / "Минутки.md").write_text("## Решения\n1. Старое.\n", encoding="utf-8")
    (legacy / "Саммари.md").write_text("---\ntype: саммари\n---\n\n# Саммари — старое\n\nбыло\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(legacy / "Минутки.md", (old - 10, old - 10))
    os.utime(legacy / "Саммари.md", (old, old))
    calls: list = []
    _fake_model(monkeypatch, calls)
    res = ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem,
                             mode=ma.SummaryMode.ADOPT)
    assert isinstance(res, ma.Archived) and res.folder.name == "2026-09-19 10-00 — Тема"
    # копия минуток обновилась ДО присвоения → минутки новее саммари → отказ, модель не звалась
    assert res.summary.action == ma.SummaryOutcome.SKIPPED and res.summary.reason.startswith("материалы новее")
    assert calls == [] and "было" in (res.folder / "Саммари.md").read_text(encoding="utf-8")
    # ADOPT через НАСТОЯЩИЙ archive_meeting на STALE и на MISSING — модель молчит (Critical
    # DS и GLM круга 3: `policy or DEFAULT` строил MISSING/STALE в режиме adopt)
    live_sidecar.attest(live, "summary", (res.folder / "Саммари.md").read_text(encoding="utf-8"), "0" * 64)  # наши байты, канон другой → STALE
    res = ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem, mode=ma.SummaryMode.ADOPT)
    assert (res.summary.action, res.summary.state) == (ma.SummaryOutcome.SKIPPED, live_sidecar.STALE) and calls == []
    (res.folder / "Саммари.md").unlink()
    res = ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem, mode=ma.SummaryMode.ADOPT)
    assert (res.summary.action, res.summary.reason) == (ma.SummaryOutcome.SKIPPED, "файла нет") and calls == []
    # rebuild: присвоить нечего — строим моделью, исход BUILT
    res = ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem,
                             mode=ma.SummaryMode.REBUILD)
    assert res.summary.action == ma.SummaryOutcome.BUILT and len(calls) == 1
    # по умолчанию: FRESH → KEPT, исключённая встреча → None
    assert ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem).summary.action == ma.SummaryOutcome.KEPT
    (graph / ma.ARCHIVE_DIR / "_исключено.md").write_text("2026-09-19_1000 — тест\n", encoding="utf-8")
    assert ma.archive_meeting(graph, tdir, "2026-09-19_1000", "Тема", files_key=live.stem) is None
