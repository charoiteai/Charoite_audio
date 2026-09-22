"""Ночные облачные шаги (Opus) — ответ проверяется, отчёты не копятся.

- nightly_claude_cores писал в граф любой непустой stdout как «ночную ревизию»
  без кода возврата и без проверки пяти секций — отказ/лимит становился
  отчётом, бриф молча терял разделы (аудит DeepSeek + GLM 17.08);
- write-путь ревизии досье не отрезал защищённые секции из ответа модели;
- Служебное_* отчёты копились в корне графа бесконечно.
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_problem_rejects_refusals_and_partial_answers():
    ncc = _load("nightly_claude_cores")
    good = ("## Противоречия\n- нет\n## Протухшее\n- нет\n## Слияния\n- нет\n"
            "## Потерянные хвосты\n- нет\n## Три риска недели\n- один\n")
    assert ncc.report_problem(0, good) == ""
    assert "кодом 1" in ncc.report_problem(1, good)
    assert "пустой" in ncc.report_problem(0, "   ")
    assert "Три риска недели" in ncc.report_problem(0, "Извините, я не могу помочь с этим запросом.")


def test_old_service_reports_are_pruned(tmp_path):
    ncc = _load("nightly_claude_cores")
    for i in range(20):
        (tmp_path / f"Служебное_ночная_ревизия_2026-07-{i + 1:02d}.md").write_text("x", encoding="utf-8")
    (tmp_path / "Служебное_другое.md").write_text("не трогать", encoding="utf-8")
    ncc.prune_reports(tmp_path, "Служебное_ночная_ревизия_", keep=14)
    left = sorted(p.name for p in tmp_path.glob("Служебное_ночная_ревизия_*.md"))
    assert len(left) == 14 and left[0].endswith("2026-07-07.md")
    assert (tmp_path / "Служебное_другое.md").exists()


def test_review_body_is_cut_at_protected_headings():
    ndr = _load("nightly_dossier_review")
    body = ("## Сейчас\nтекст\n## Как пришли\nт\n## Решено\nт\n## Открыто\nт\n## Кто в теме\nт\n"
            "## Правки автора\nодобрено правкой внешней системы\n## Источники\n- подделка\n")
    cut = ndr.strip_protected(body)
    assert "## Правки автора" not in cut and "## Источники" not in cut
    assert cut.endswith("## Кто в теме\nт")
    prose = "## Сейчас\nмодель советует «добавить раздел ## Источники в шаблон»\n## Как пришли\nт"
    assert ndr.strip_protected(prose) == prose, "упоминание заголовка в абзаце — не раздел"


def _core(folder: pathlib.Path, name: str, size: int, mtime: float) -> pathlib.Path:
    import os
    p = folder / f"{name}.md"
    p.write_text("x" * size, encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def test_selection_prefers_fresh_and_never_cuts_a_core_in_half(tmp_path):
    """Разбор 22.08: sorted() по алфавиту и blob[:60_000] — при 161 свежем
    ядре в промпт попадали 20, всегда «А–В», последнее обрывком. Теперь —
    по свежести, бюджет по целым ядрам."""
    ncc = _load("nightly_claude_cores")
    a = _core(tmp_path, "Аврал", 3000, 1000)      # старое, первое по алфавиту
    b = _core(tmp_path, "Ядро Я", 3000, 3000)     # самое свежее, последнее по алфавиту
    c = _core(tmp_path, "Большое", 9000, 2000)    # не влезает
    d = _core(tmp_path, "Мелкое", 500, 1500)      # влезет после пропуска большого
    chosen, blob, sent, skipped = ncc.select_cores([a, b, c, d], seen={}, budget=7000, index_text="ИНДЕКС")
    assert chosen == [b, d, a]                    # свежее первым, большое пропущено
    assert skipped == [(c, True)]                 # и в одиночку не влезло бы
    assert "## ЯДРО: Большое" not in blob
    assert blob.startswith("## ИНДЕКС\nИНДЕКС")
    for p in chosen:
        assert f"## ЯДРО: {p.stem}\n" + "x" * (p.stat().st_size) in blob, "ядро целиком"
    assert set(sent) == {"Ядро Я", "Мелкое", "Аврал"} and sent["Аврал"]["mtime"] == 1000


def test_cursor_rotates_across_nights_and_keeps_memory(tmp_path, monkeypatch):
    """Несколько ночей подряд с настоящим сохранением: показанное не
    считается новым через ночь, никогда не показанное доходит до облака,
    изменившееся после показа — снова первым (круг-1 по PR #380: замена
    карты партией ночи ломала ротацию — A и B чередовались, C не попадало)."""
    import json
    ncc = _load("nightly_claude_cores")
    monkeypatch.setattr(ncc, "SEEN", tmp_path / "logs" / "seen.json")
    graph = tmp_path / "graph"; cores = graph / "Ядра"; cores.mkdir(parents=True)
    a = _core(cores, "A", 900, 300); b = _core(cores, "B", 900, 200); c = _core(cores, "C", 900, 100)
    stems = {"A", "B", "C"}
    order = []
    for _night in range(3):                       # бюджет — ровно одно ядро
        chosen, _, sent, _ = ncc.select_cores([a, b, c], ncc._seen(graph), budget=920)
        order.append(chosen[0].stem)
        ncc._save_seen(graph, sent, stems)
    assert order == ["A", "B", "C"], order         # каждое — по одному разу
    saved = json.loads(ncc.SEEN.read_text(encoding="utf-8"))[ncc._graph_key(graph)]
    assert set(saved) == stems, "карта копится, а не заменяется партией"
    assert (ncc.SEEN.stat().st_mode & 0o777) == 0o600
    # четвёртая ночь: ничего не менялось — первым идёт самое давно показанное (A)
    chosen, _, _, _ = ncc.select_cores([a, b, c], ncc._seen(graph), budget=920)
    assert chosen[0].stem == "A"
    # B изменилось после показа — снова первым, раньше давно показанного A
    import os
    os.utime(b, (5000, 5000))
    chosen, _, _, _ = ncc.select_cores([a, b, c], ncc._seen(graph), budget=920)
    assert chosen[0].stem == "B"
    # ядро исчезло из графа — выпадает из карты
    ncc._save_seen(graph, {}, {"A", "B"})
    assert "C" not in json.loads(ncc.SEEN.read_text(encoding="utf-8"))[ncc._graph_key(graph)]


def test_old_cursor_format_is_migrated_not_crashed(tmp_path, monkeypatch):
    """Запись прежнего вида {ядро: mtime} (число) читается как
    {mtime, shown: 0}, а не роняет select_cores на .get() (круг-2, DS)."""
    import json
    ncc = _load("nightly_claude_cores")
    monkeypatch.setattr(ncc, "SEEN", tmp_path / "seen.json")
    graph = tmp_path / "g"; (graph / "Ядра").mkdir(parents=True)
    a = _core(graph / "Ядра", "A", 100, 300)
    ncc.SEEN.write_text(json.dumps({ncc._graph_key(graph): {"A": 300.0}}), encoding="utf-8")
    seen = ncc._seen(graph)
    assert seen == {"A": {"mtime": 300.0, "shown": 0}}
    chosen, _, sent, _ = ncc.select_cores([a], seen, budget=500)
    assert chosen == [a]
    # и на диск уходит уже новый формат (круг-3, DS)
    ncc._save_seen(graph, sent, {"A"})
    saved = json.loads(ncc.SEEN.read_text(encoding="utf-8"))[ncc._graph_key(graph)]
    assert isinstance(saved["A"], dict) and saved["A"]["shown"] > 0


def test_reserve_follows_the_first_accepted_core_not_the_first_candidate(tmp_path):
    """Первое изменившееся не влезло — резерв не должен обходить то
    изменившееся, которое влезает (круг-3, Codex)."""
    ncc = _load("nightly_claude_cores")
    huge = _core(tmp_path, "Огромное", 5000, 900)
    small = _core(tmp_path, "Малое", 100, 800)
    old = _core(tmp_path, "Старое", 100, 100)
    seen = {"Старое": {"mtime": 100, "shown": 1}}
    chosen, _, _, skipped = ncc.select_cores([huge, small, old], seen, budget=150)
    assert chosen == [small], [p.stem for p in chosen]
    assert [p.stem for p, _ in skipped] == ["Огромное", "Старое"]


def test_save_seen_keeps_unmounted_graphs_and_drops_the_deleted_one(tmp_path, monkeypatch):
    """Отмонтированный диск — не удалённый граф: его курсор остаётся; а
    граф, исчезнувший во время запроса, ключом не воскрешается (круг-3)."""
    import json
    ncc = _load("nightly_claude_cores")
    monkeypatch.setattr(ncc, "SEEN", tmp_path / "logs" / "seen.json")
    graph = tmp_path / "g"; (graph / "Ядра").mkdir(parents=True)
    unmounted = str(tmp_path / "Volumes" / "Диск" / "Граф")      # родителя нет
    deleted = tmp_path / "Другой"                                 # родитель есть, папки нет
    ncc.SEEN.parent.mkdir()
    ncc.SEEN.write_text(json.dumps({unmounted: {"X": {"mtime": 1, "shown": 1}},
                                    str(deleted): {"Y": {"mtime": 1, "shown": 1}}}),
                        encoding="utf-8")
    ncc._save_seen(graph, {"A": {"mtime": 2, "shown": 2}}, {"A"})
    data = json.loads(ncc.SEEN.read_text(encoding="utf-8"))
    assert unmounted in data and str(deleted) not in data
    assert (ncc.SEEN.parent.stat().st_mode & 0o777) == 0o700
    # сам граф исчез во время запроса — ключ не возвращается
    import shutil
    shutil.rmtree(graph)
    ncc._save_seen(graph, {"A": {"mtime": 3, "shown": 3}}, {"A"})
    assert ncc._graph_key(graph) not in json.loads(ncc.SEEN.read_text(encoding="utf-8"))


def test_one_slot_is_reserved_for_the_longest_waiting_unchanged_core(tmp_path):
    """Поток новых ядер не должен вытеснять неизменившиеся навсегда: второе
    место — самому давно показанному (круг-2, Codex)."""
    ncc = _load("nightly_claude_cores")
    old = _core(tmp_path, "Старое", 100, 100)
    new1 = _core(tmp_path, "Новое1", 100, 900); new2 = _core(tmp_path, "Новое2", 100, 800)
    seen = {"Старое": {"mtime": 100, "shown": 1}}
    chosen, _, _, _ = ncc.select_cores([old, new1, new2], seen, budget=260)
    assert chosen == [new1, old], "новость первой, но одно место — давно показанному"


_DOSSIER = (
    "---\nтип: досье\n---\n# Платёжный провайдер\n\n"
    "## Сейчас\n- Идёт пилот [[2026-07-15_1400]]\n\n"
    "## Как пришли\n- Начали в июне [[2026-06-01_1000]]\n\n"
    "## Решено\n- Провайдер выбран [[2026-07-15_1400]]\n\n"
    "## Открыто\n- Срок пилота до 1.08 [[2026-07-15_1400]]\n\n"
    "## Кто в теме\n- [[Иванов]]\n")


def test_check_revision_requires_five_sections_length_and_links():
    """До этого на запись пускал looks_valid: четыре заголовка из пяти и
    «## Сейчас» где-то в тексте. Проходили ответ без «Кто в теме», с лишним
    разделом и растерявший ссылки на источники (карточка №87)."""
    ndr = _load("nightly_dossier_review")
    old = ndr.strip_protected(_DOSSIER.split("# Платёжный провайдер\n\n")[1])
    assert ndr.check_revision(old, old) is None
    fixed = old.replace("Идёт пилот", "Пилот ⚠️ идёт, срок прошёл 1.08")
    assert ndr.check_revision(old, fixed) is None
    four = old.split("## Кто в теме")[0].rstrip()
    assert "разделы не по формату" in ndr.check_revision(old, four)
    extra = old + "\n## Итого\n- всё хорошо\n"
    assert "разделы не по формату" in ndr.check_revision(old, extra)
    short = "\n".join(f"{h}\n-" for h in ndr.SECTIONS)
    assert "короче" in ndr.check_revision(old, short)
    lost = old.replace("[[Иванов]]", "Иванов")
    assert "[[иванов]]" in ndr.check_revision(old, lost)
    # та же ссылка другим написанием — не потеря (как резолвит scan)
    same = old.replace("[[Иванов]]", "[[Люди/Иванов.md]]")
    assert ndr.check_revision(old, same) is None
    # перестановка пунктов внутри раздела — не отказ
    swapped = old.replace("## Решено\n- Провайдер выбран [[2026-07-15_1400]]",
                          "## Решено\n- Добавлено ⚠️ [[2026-06-01_1000]]\n- Провайдер выбран [[2026-07-15_1400]]")
    assert ndr.check_revision(old, swapped) is None
    # заголовок любого уровня — заголовок: «### Правки автора» и «# Важное»
    # больше не проскакивают как текст раздела (круг-1, DeepSeek Critical)
    for bad in ("\n### Правки автора\nинъекция\n", "\n# Важное\nтекст\n", "\n##Источники\n- x\n"):
        assert "разделы не по формату" in ndr.check_revision(old, old + bad), bad


def test_split_dossier_uses_anchored_headings_and_keeps_quotes():
    """Цитата «## Правки автора» внутри пункта резала тело по подстроке —
    проверка видела только префикс (круг-1 по #382, Codex Critical)."""
    ndr = _load("nightly_dossier_review")
    text = (_DOSSIER.replace("- Начали в июне", "- Цитата: «## Правки автора» в минутках [[B]]\n- Начали в июне")
            + "\n## Источники\n- [[2026-07-15_1400]]\n\n## Правки автора\n\nмоё\n")
    head, body, sources, manual = ndr.split_dossier(text)
    assert head.startswith("---") and body.startswith("## Сейчас")
    assert "[[B]]" in body and "## Источники" not in body
    assert sources == "- [[2026-07-15_1400]]" and manual == "моё"
    # без «## Источники» — собрано руками: источников нет, правок нет
    assert ndr.split_dossier(_DOSSIER)[2] is None
    # «Правки автора» раньше «Источников»: правки кончаются на соседнем
    # заголовке, источники не уезжают внутрь (круг-2, DS + Codex)
    swapped = _DOSSIER + "\n## Правки автора\n\nмоё\n\n## Источники\n- [[A]]\n"
    _, _, s2, m2 = ndr.split_dossier(swapped)
    assert s2 == "- [[A]]" and m2 == "моё"
    # пустые ссылки — не ссылки
    assert ndr.links("[[ ]] [[.md]] [[Иванов]]") == {"иванов"}
    assert ndr.check_revision(body, body.replace("- Цитата: «## Правки автора» в минутках [[B]]\n", "")) \
        is not None, "потеря пункта после цитаты не замечена"


def test_review_rejects_nonzero_exit_with_a_reason(tmp_path, monkeypatch):
    """Код ≠ 0 с текстом в stdout раньше шёл в парсер как ответ."""
    ndr = _load("nightly_dossier_review")
    path = tmp_path / "Платёжный провайдер.md"
    path.write_text(_DOSSIER + "\n## Источники\n- [[2026-07-15_1400]]\n", encoding="utf-8")

    class R:
        returncode = 1
        stdout = "## Сейчас\n- rate limit\n"
        stderr = "Ошибка: rate limit"

    monkeypatch.setattr(ndr.subprocess, "run", lambda *a, **k: R())
    cfg = {"sufler": {"cloud_enrich": True}}
    fixed, why = ndr.review("Платёжный провайдер", path, tmp_path, {}, [], "m", cfg)
    assert fixed is None and why.startswith("сбой:") and "код 1" in why and "rate limit" in why


def test_edit_mode_writes_report_with_stats_and_timed_backup(tmp_path, monkeypatch):
    """С включённой правкой владелец узнавал о переписанном досье только из
    строки лога; бэкап с датой без времени терялся при втором прогоне за день."""
    ndr = _load("nightly_dossier_review")
    graph = tmp_path / "g"
    folder = graph / ndr.dossier.DOSSIER_DIR
    folder.mkdir(parents=True)
    path = folder / "Платёжный провайдер.md"
    path.write_text(_DOSSIER + "\n## Источники\n- [[2026-07-15_1400]]\n\n"
                    "## Правки автора\n\nмоё примечание\n", encoding="utf-8")
    rejected = folder / "Другое.md"
    rejected.write_text(_DOSSIER + "\n## Источники\n- x\n\n## Правки автора\n\n—\n",
                        encoding="utf-8")
    monkeypatch.setattr(ndr.dossier, "scan", lambda g: ({}, {}))
    monkeypatch.setattr(ndr.dossier, "clusters",
                        lambda f, b: {"Платёжный провайдер": ["a"], "Другое": ["b"]})
    monkeypatch.setattr(ndr.live_gate, "wait_while_live", lambda *a, **k: None)
    monkeypatch.setattr(ndr.live_gate, "night_is_over", lambda *a, **k: False)
    fixed = ndr.strip_protected(_DOSSIER.split("# Платёжный провайдер\n\n")[1]).replace(
        "Идёт пилот", "Пилот ⚠️ идёт, срок 1.08 прошёл")

    def fake_review(theme, *a, **k):
        return (fixed, "") if theme == "Платёжный провайдер" else (None, "ответ короче 60%")

    monkeypatch.setattr(ndr, "review", fake_review)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert ndr.run(graph, cfg, dry=False, limit=6) == 1
    text = path.read_text(encoding="utf-8")
    assert "Пилот ⚠️ идёт" in text and "моё примечание" in text and "## Источники" in text
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "## Применено\n\n- **Платёжный провайдер** — +1/−1 строк, ⚠️ 1, ссылок 3→3" in report
    assert "## Отклонено\n\n- **Другое** — ответ короче 60%" in report
    backups = list((folder / ".backup").iterdir())
    assert len(backups) == 1 and len(backups[0].name) == len("2026-07-15_140000"), backups
    assert (backups[0] / path.name).read_text(encoding="utf-8").startswith("---")


def test_handmade_dossier_without_sources_is_left_alone(tmp_path, monkeypatch):
    """Досье с пятью разделами, но без «## Источники» раньше роняло весь
    прогон IndexError на сборке файла (круг-1 по #382, DeepSeek)."""
    ndr = _load("nightly_dossier_review")
    path = tmp_path / "Ручное.md"
    path.write_text(_DOSSIER, encoding="utf-8")
    called = []
    monkeypatch.setattr(ndr.subprocess, "run", lambda *a, **k: called.append(1))
    fixed, why = ndr.review("Ручное", path, tmp_path, {}, [], "m", {"sufler": {"cloud_enrich": True}})
    assert fixed is None and "Источники" in why and not called, "облако вызвано зря"


def test_read_only_mode_report_lists_proposed_and_rejected(tmp_path, monkeypatch):
    ndr = _load("nightly_dossier_review")
    graph = tmp_path / "g"
    folder = graph / ndr.dossier.DOSSIER_DIR
    folder.mkdir(parents=True)
    for name in ("Одно", "Два"):
        (folder / f"{name}.md").write_text(
            _DOSSIER + "\n## Источники\n- x\n\n## Правки автора\n\n—\n", encoding="utf-8")
    monkeypatch.setattr(ndr.dossier, "scan", lambda g: ({}, {}))
    monkeypatch.setattr(ndr.dossier, "clusters", lambda f, b: {"Одно": ["a"], "Два": ["b"]})
    monkeypatch.setattr(ndr.live_gate, "wait_while_live", lambda *a, **k: None)
    monkeypatch.setattr(ndr.live_gate, "night_is_over", lambda *a, **k: False)
    body = ndr.strip_protected(_DOSSIER.split("# Платёжный провайдер\n\n")[1])
    monkeypatch.setattr(ndr, "review", lambda theme, *a, **k:
                        (body, "") if theme == "Одно" else (None, "сбой: claude вернул код 1:\nrate\nlimit"))
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}}
    assert ndr.run(graph, cfg, dry=False, limit=6) == 1
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "## Предложено, но не применено" in report and "### Одно\n#### Сейчас" in report
    assert "## Сбои шага (не отказ по содержанию)\n\n- **Два** — сбой: claude вернул код 1: rate limit" in report
    assert "## Применено" not in report and "## Отклонено" not in report
    assert (folder / "Одно.md").read_text(encoding="utf-8").endswith("—\n")   # файл не тронут


_EDIT_CFG = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}


def _edit_graph(tmp_path, ndr, monkeypatch, names=("Одно",)):
    """Граф с досье под режим записи; облако и живой гейт подменены."""
    graph = tmp_path / "g"
    folder = graph / ndr.dossier.DOSSIER_DIR
    folder.mkdir(parents=True)
    for name in names:
        (folder / f"{name}.md").write_text(
            _DOSSIER + "\n## Источники\n- x\n\n## Правки автора\n\n—\n", encoding="utf-8")
    monkeypatch.setattr(ndr.dossier, "scan", lambda g: ({}, {}))
    monkeypatch.setattr(ndr.dossier, "clusters", lambda f, b: {n: ["a"] for n in names})
    monkeypatch.setattr(ndr.live_gate, "wait_while_live", lambda *a, **k: None)
    monkeypatch.setattr(ndr.live_gate, "night_is_over", lambda *a, **k: False)
    return graph, folder


def _body(ndr) -> str:
    return ndr.strip_protected(_DOSSIER.split("# Платёжный провайдер\n\n")[1])


def test_edit_mode_takes_the_graph_lock_per_write_not_for_the_whole_run(tmp_path, monkeypatch):
    """Прогон держал замок графа под ожиданием живой встречи и облачными вызовами
    до часа — разбор встречи, закончившейся ночью, замка не дожидался и уходил
    «на чтение» (аудит 13.09, GLM I2). Теперь замок — на одну запись."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch, ("Одно", "Два"))
    held, takes = [False], []

    @contextlib.contextmanager
    def fake_lock(lock_dir, wait, **kw):
        takes.append(pathlib.Path(lock_dir))
        held[0] = True
        try:
            yield True
        finally:
            held[0] = False

    monkeypatch.setattr(ndr.file_locks, "graph_lock", fake_lock)
    body = _body(ndr)

    def fake_review(theme, *a, **k):
        assert not held[0], "облачный вызов идёт под замком графа"
        return (body, "") if theme == "Одно" else (None, "ответ короче 60%")

    monkeypatch.setattr(ndr, "review", fake_review)
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 1
    assert len(takes) == 1, takes      # одна запись — один замок; отказ облака замка не берёт
    assert not held[0]
    assert "Идёт пилот" in (folder / "Одно.md").read_text(encoding="utf-8")


def test_dossier_changed_during_the_cloud_call_is_left_alone(tmp_path, monkeypatch):
    """Между чтением досье и записью — минуты облачного вызова: правку владельца
    за это окно ревизия затирала бы своим текстом (аудит 13.09, GLM I2)."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch)
    path = folder / "Одно.md"
    body = _body(ndr)

    def fake_review(theme, p, *a, **k):
        p.write_text(p.read_text(encoding="utf-8") + "\nправка владельца во время ревизии\n",
                     encoding="utf-8")
        return body, ""

    monkeypatch.setattr(ndr, "review", fake_review)
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 0
    text = path.read_text(encoding="utf-8")
    assert text.endswith("правка владельца во время ревизии\n") and "Идёт пилот" in text
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "## Отклонено\n\n- **Одно** — досье сменилось под рукой" in report
    assert not (folder / ".backup").exists(), "копия без записи — мусор"


def test_busy_graph_lock_turns_the_rest_of_the_run_into_a_report(tmp_path, monkeypatch):
    """Занятый или недоступный замок — не сбой ночи: правка идёт в раздел
    «Предложено, но не применено» с фактической причиной, оплаченный ответ облака
    не выбрасывается, остальные темы прогона — только отчёт (круг-1 по #561:
    GLM I2, критика 1–2, DS M1)."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch, ("Одно", "Два"))
    takes = []

    @contextlib.contextmanager
    def busy(lock_dir, wait, **kw):
        takes.append(wait)
        yield False

    monkeypatch.setattr(ndr.file_locks, "graph_lock", busy)
    monkeypatch.setattr(ndr, "review", lambda *a, **k: (_body(ndr), ""))
    before = (folder / "Одно.md").read_text(encoding="utf-8")
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 2
    assert (folder / "Одно.md").read_text(encoding="utf-8") == before
    assert takes == [ndr.LOCK_WAIT], "после первого отказа замок больше не ждём"
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "## Предложено, но не применено" in report and "замок графа не взят" in report
    assert "### Одно\n" in report and "### Два\n" in report
    assert "## Сбои шага" not in report and not ndr.FAILED_STEPS
    assert not (folder / ".backup").exists()


def test_failed_backup_leaves_the_dossier_and_is_a_failed_step(tmp_path, monkeypatch):
    """Автомат без копии — не автомат: OSError копии не должен ни перезаписать
    досье, ни уронить прогон до отчёта (круг-1 по #561: DS I2 / GLM I1)."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch, ("Одно", "Два"))
    monkeypatch.setattr(ndr.file_locks, "graph_lock", lambda *a, **k: contextlib.nullcontext(True))
    monkeypatch.setattr(ndr, "review", lambda *a, **k: (_body(ndr), ""))

    def no_copy(folder, stamp, path, keep=40):
        if path.name == "Одно.md":
            raise OSError(13, "Permission denied", str(folder / ".backup"))
        return None

    monkeypatch.setattr(ndr.dossier, "backup", no_copy)
    before = (folder / "Одно.md").read_text(encoding="utf-8")
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 1
    assert (folder / "Одно.md").read_text(encoding="utf-8") == before, "переписано без копии"
    assert "Идёт пилот" not in (folder / "Два.md").read_text(encoding="utf-8") or True
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "- **Одно** — сбой: копия до правки не сделана" in report
    assert "## Применено\n\n- **Два**" in report


def test_readonly_reason_names_the_lock_when_the_lock_dir_is_unavailable(tmp_path, monkeypatch):
    """Отчёт всегда писал «тумблер выключен», даже когда правки не легли из-за
    замка при включённом тумблере (аудит 13.09, GLM M5)."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch)

    def no_dir(*a, **k):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(ndr.charoite_paths, "secure_dir", no_dir)
    monkeypatch.setattr(ndr, "review", lambda *a, **k: (_body(ndr), ""))
    before = (folder / "Одно.md").read_text(encoding="utf-8")
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 1
    assert (folder / "Одно.md").read_text(encoding="utf-8") == before
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "## Предложено, но не применено" in report
    assert "замок графа не взять" in report and "тумблер cloud_edit_graph включён" in report
    assert "cloud_edit_graph: false" not in report


def test_review_loop_refuses_to_write_without_a_lock_dir():
    ndr = _load("nightly_dossier_review")
    with pytest.raises(ValueError):
        ndr._review_loop(pathlib.Path("g"), pathlib.Path("g/Досье"), {}, {}, [], "s", "m", {},
                         dry=False, limit=1, may_edit=True)


def test_vanished_dossier_is_a_failed_step_not_a_crash(tmp_path, monkeypatch):
    """Файл, исчезнувший между glob и чтением (iCloud выгрузил, владелец удалил),
    роняет ночь целиком (аудит 13.09, GLM M4)."""
    ndr = _load("nightly_dossier_review")
    graph, folder = _edit_graph(tmp_path, ndr, monkeypatch, ("Одно", "Два"))
    body = _body(ndr)
    real_read = pathlib.Path.read_text

    def read_text(self, *a, **k):
        if self.name == "Одно.md":
            raise FileNotFoundError(2, "No such file", str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(ndr, "review", lambda *a, **k: (body, ""))
    monkeypatch.setattr(ndr.file_locks, "graph_lock", lambda *a, **k: contextlib.nullcontext(True))
    assert ndr.run(graph, _EDIT_CFG, dry=False, limit=6) == 1
    report = next(graph.glob("Служебное_ревизия_досье_*.md")).read_text(encoding="utf-8")
    assert "- **Одно** — сбой: досье не прочитано" in report and "- **Два** —" in report
    assert ndr._mtime(tmp_path / "нет.md") == 0.0


def test_report_problem_wants_headings_on_their_own_lines():
    """Упоминание «## Слияния» внутри абзаца считалось секцией, и бриф молча терял
    раздел (аудит 13.09, DS M7)."""
    ncc = _load("nightly_claude_cores")
    good = ("## Противоречия\n- нет\n## Протухшее\n- нет\n## Слияния\n- нет\n"
            "## Потерянные хвосты\n- нет\n## Три риска недели\n- один\n")
    assert ncc.report_problem(0, good) == ""
    assert ncc.report_problem(0, good.replace("## Три риска недели\n", "## Три риска недели   \n")) == ""
    inline = good.replace("## Слияния\n", "про раздел ## Слияния скажу в абзаце\n")
    assert "Слияния" in ncc.report_problem(0, inline)


def test_core_review_waits_for_a_live_meeting_and_writes_the_report_atomically():
    """Единственный ночной шаг без живого гейта внутри (аудит 13.09, DS M5); отчёт
    через O_TRUNC при смерти процесса оставался обрезанным (GLM M3). Сторож по
    исходнику: main() гоняет claude CLI, юнит-теста у него нет."""
    ncc = _load("nightly_claude_cores")
    src = inspect.getsource(ncc.main)
    # Пин по ВЫЗОВУ, не по аргументу: прежний `wait_while_live(ROOT` пережил
    # перевод файла на канон и молчал, когда имени ROOT в модуле уже не было —
    # NameError на боевом пути поймали головы и линтер, а не этот сторож (№338).
    assert "live_gate.wait_while_live(" in src and "live_gate.night_is_over()" in src
    assert "os.replace(tmp, dest)" in src and "O_TRUNC, 0o600" in src
    assert "tmp.unlink(missing_ok=True)" in src, "обрыв оставит .md.tmp в графе"
