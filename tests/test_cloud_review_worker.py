"""Фоновый разбор никто не проверял, а правки графа шли без бэкапа.

Две находки аудита, обе про то, что происходит ПОСЛЕ запуска облака.

CHR-AUD-004. `Popen` уходил в фон без таймаута и без проверки кода возврата.
Сообщение «Claude запущен фоном» означало ровно одно: процесс стартовал. Если
он падал, упирался в лимит или отвечал обрывком, в папке встречи оставался
пустой или недописанный файл ревизии — с виду настоящий. Человек узнавал об
этом, только открыв лог, то есть обычно никогда.

CHR-AUD-003. В режиме записи модель правила граф напрямую. Бэкап и границы
того, что можно трогать, existовали только в тексте промпта — то есть держались
на послушании модели, тогда как PRIVACY обещает бэкап перед каждой правкой.

Отсюда воркер: он ждёт процесс с таймаутом, проверяет код возврата и то, что
ответ похож на ревизию, публикует файл атомарно — и в режиме записи снимает
бэкап графа до запуска, а после сверяет, что тронуто только разрешённое.

Границы намеренно узкие. Модель дообогащает граф — узлы, ядра, заметки встреч.
Стенограммы, минутки и раздел «## Правки автора» неприкосновенны: это то, что
написал человек или записала машина с его слов, и облаку там делать нечего.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import cloud_review  # noqa: E402


def _graph(tmp: pathlib.Path) -> pathlib.Path:
    graph = tmp / "Работа"
    (graph / "Ядра").mkdir(parents=True)
    (graph / "Встречи").mkdir()
    (graph / "Документация" / "Стенограммы встреч").mkdir(parents=True)
    (graph / "Ядра" / "Платёжный провайдер.md").write_text(
        "# Ядро\n## Статус\nРешено\n\n## Правки автора\n\nруками написанное\n",
        encoding="utf-8")
    (graph / "Встречи" / "2026-07-15_1400.md").write_text("# Встреча\n", encoding="utf-8")
    (graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1400.md").write_text(
        "стенограмма\n", encoding="utf-8")
    return graph


def test_allowed_paths_cover_the_graph_and_nothing_outside(tmp_path):
    graph = _graph(tmp_path)
    assert cloud_review.may_write(graph / "Ядра" / "Платёжный провайдер.md", graph)
    assert cloud_review.may_write(graph / "Встречи" / "2026-07-15_1400.md", graph)
    # за пределами графа — никогда, даже если путь выглядит похоже
    assert not cloud_review.may_write(tmp_path / "config.yaml", graph)
    assert not cloud_review.may_write(graph.parent / "Дневник" / "2026-07-15.md", graph)


def test_snapshot_and_obsidian_folders_are_off_limits(tmp_path):
    """Скрытые каталоги графа — снимки (.cloud_backup, .forget_backup,
    Ядра/.tier3_backup) и служебное Obsidian: писать туда облаку нельзя
    (аудит DeepSeek 16.08)."""
    graph = _graph(tmp_path)
    for hidden in (graph / cloud_review.BACKUP_DIR / "2026-07-14_0300" / "Ядра" / "Х.md",
                   graph / ".forget_backup" / "2026-07-15_1400" / "Х.md",
                   graph / "Ядра" / ".tier3_backup" / "2026-07-14" / "Х.md",
                   graph / ".obsidian" / "workspace.json"):
        assert not cloud_review.may_write(hidden, graph), hidden


def test_hidden_paths_are_watched_and_restored(tmp_path):
    """Запрет без сверки — не запрет: snapshot и бэкап раньше пропускали
    dot-пути, а `Edit(/**)` их не исключает — правка .obsidian или снимка
    tier3 была невидимой и необратимой (Codex, Critical 22.08)."""
    graph = _graph(tmp_path)
    plugin = graph / ".obsidian" / "plugins" / "x" / "main.js"
    old_core = graph / "Ядра" / ".tier3_backup" / "2026-07-14" / "Старое.md"
    for f, text in ((graph / ".obsidian" / "app.json", "{}\n"),
                    (old_core, "# Старое\nисходник до слияния\n")):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    before = cloud_review.snapshot(graph)
    assert str(old_core.resolve()) in before, "снимок не видит скрытые пути"
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    assert (backup / "Ядра" / ".tier3_backup" / "2026-07-14" / "Старое.md").exists()
    # исключения: свой же старый снимок внутри графа и живые окна Obsidian
    legacy = graph / cloud_review.BACKUP_DIR / "2026-07-14_0300" / "x.md"
    legacy.parent.mkdir(parents=True); legacy.write_text("x", encoding="utf-8")
    ws = graph / ".obsidian" / "workspace.json"
    ws.write_text("{}", encoding="utf-8")
    now = cloud_review.snapshot(graph)
    assert str(legacy.resolve()) not in now and str(ws.resolve()) not in now
    assert str((graph / ".obsidian" / "app.json").resolve()) in now

    plugin.parent.mkdir(parents=True)
    plugin.write_text("alert(1)", encoding="utf-8")           # создан облаком
    old_core.write_text("# Старое\nпереписано облаком\n", encoding="utf-8")
    qdir = tmp_path / "q"
    v = cloud_review.enforce_boundaries(before, graph, backup, qdir)
    assert not plugin.exists() and "main.js" in v.removed
    assert (qdir / ".obsidian" / "plugins" / "x" / "main.js").read_text(encoding="utf-8") == "alert(1)"
    assert old_core.read_text(encoding="utf-8") == "# Старое\nисходник до слияния\n"
    assert "Старое.md" in v.reverted


def test_missing_graph_runs_text_only_instead_of_exposing_transcripts(
        tmp_path, monkeypatch):
    """Wiring: edit=true не выдаёт файловые tools fallback-папке."""
    stamp = "2026-07-15_1400"
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("чужой текст встречи\n", encoding="utf-8")
    rev = transcripts / f"{stamp}_ревизия.md"
    log = tmp_path / "cloud.log"
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs["cwd"]
        kwargs["stdout"].write(
            "- **Решение:** оставить граф закрытым\n"
            "- **Поручение:** проверить настройку\n"
            "- **Риск:** файловый доступ не выдавался\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    code = cloud_review.run(stamp, transcript, tmp_path / "missing-graph",
                            rev, log, cfg)

    assert code == 0
    cmd = captured["cmd"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd
    assert captured["cwd"] == str(transcripts)
    assert "только текст (граф недоступен)" in log.read_text(encoding="utf-8")


def test_transcripts_inside_the_graph_are_untouchable(tmp_path):
    """Копии стенограмм лежат в графе, но правит их конвейер, а не облако."""
    graph = _graph(tmp_path)
    doc = graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1400.md"
    assert not cloud_review.may_write(doc, graph)


def test_author_section_changes_are_rejected(tmp_path):
    """«## Правки автора» — то, что человек написал руками."""
    graph = _graph(tmp_path)
    core = graph / "Ядра" / "Платёжный провайдер.md"
    before = core.read_text(encoding="utf-8")
    after = before.replace("руками написанное", "переписанное облаком")
    assert cloud_review.author_section_changed(before, after)
    ok = before.replace("Решено", "Решено — ЮPay")
    assert not cloud_review.author_section_changed(before, ok)


def test_snapshot_notices_added_changed_and_untouched(tmp_path):
    graph = _graph(tmp_path)
    before = cloud_review.snapshot(graph)
    (graph / "Ядра" / "Новое.md").write_text("# Новое\n", encoding="utf-8")
    (graph / "Встречи" / "2026-07-15_1400.md").write_text("# Встреча\nправка\n",
                                                          encoding="utf-8")
    changed = cloud_review.changed_since(before, graph)
    names = {p.name for p in changed}
    assert names == {"Новое.md", "2026-07-15_1400.md"}, names


def test_backup_restores_a_file_the_cloud_should_not_have_touched(tmp_path):
    graph = _graph(tmp_path)
    doc = graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1400.md"
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    doc.write_text("облако переписало стенограмму", encoding="utf-8")
    restored = cloud_review.restore(doc, graph, backup)
    assert restored, "файл не восстановлен"
    assert doc.read_text(encoding="utf-8") == "стенограмма\n"


def test_archive_folders_are_untouchable(tmp_path):
    """Саммари и минутки в архиве — та же категория, что копии стенограмм."""
    graph = _graph(tmp_path)
    arch = graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    arch.mkdir(parents=True)
    assert not cloud_review.may_write(arch / "Минутки.md", graph)


def test_deleted_file_is_seen_and_restored(tmp_path):
    """Удаление — тоже правка: diff только по живым файлам его не видел."""
    graph = _graph(tmp_path)
    core = graph / "Ядра" / "Платёжный провайдер.md"
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    core.unlink()
    assert core.resolve() in {p.resolve() for p in
                              cloud_review.changed_since(before, graph)}, \
        "удалённый файл невидим для сверки"
    v = cloud_review.enforce_boundaries(before, graph, backup, tmp_path / "q")
    assert core.exists(), "удалённый узел не восстановлен"
    assert core.name in v.deleted and not v.removed and not v.reverted


def test_deleting_a_node_without_author_section_is_a_violation(tmp_path):
    """Удаление — нарушение всегда, не только при пропавших «Правках автора».

    У ядер, людей и систем в проде этого раздела нет, и их стирание
    проходило как «правка» и считалось в «правок графа: N» (Sonnet, Codex
    22.08). Переименование — тоже удаление плюс создание: старый файл
    возвращается, новый остаётся, в логе — отдельное слово.
    """
    graph = _graph(tmp_path)
    person = graph / "Люди" / "Иванов.md"
    person.parent.mkdir()
    person.write_text("# Иванов\n- **Роль:** аналитик\n", encoding="utf-8")
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    person.rename(graph / "Люди" / "Иванов И.md")           # «переименовал»
    v = cloud_review.enforce_boundaries(before, graph, backup, tmp_path / "q")
    assert person.read_text(encoding="utf-8") == "# Иванов\n- **Роль:** аналитик\n"
    assert v.deleted == ["Иванов.md"] and (graph / "Люди" / "Иванов И.md").exists()
    line = cloud_review._verdict_line(v, tmp_path / "q")
    assert "УДАЛЕНО облаком, восстановлено: Иванов.md" in line


def test_rewriting_a_node_from_scratch_is_reverted_but_a_redirect_stub_is_not(tmp_path):
    """Облако дообогащает узлы, а не сочиняет их заново: если от старого текста
    не осталось и трети строк — это переписывание, файл возвращается, а
    версия облака ждёт в карантине. Единственная форма «убрать узел» —
    заглушка-перенаправление, как у tier3 при слиянии дублей."""
    graph = _graph(tmp_path)
    core = graph / "Ядра" / "Платёжный провайдер.md"
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    core.write_text(body, encoding="utf-8")
    dup = graph / "Ядра" / "Провайдер платежей.md"
    dup.write_text(body.replace("Ядро", "Дубль"), encoding="utf-8")
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    core.write_text("# Ядро\nкороткое резюме облака\n", encoding="utf-8")
    dup.write_text("# Провайдер платежей → [[Ядра/Платёжный провайдер]]\n\n"
                   "Дубль. Смерджен.\n", encoding="utf-8")
    v = cloud_review.enforce_boundaries(before, graph, backup, tmp_path / "q")
    assert core.read_text(encoding="utf-8") == body, "переписанное ядро не возвращено"
    assert v.rewritten == ["Платёжный провайдер.md"]
    assert (tmp_path / "q" / "Ядра" / "Платёжный провайдер.md").read_text(
        encoding="utf-8") == "# Ядро\nкороткое резюме облака\n"
    assert dup.read_text(encoding="utf-8").startswith("# Провайдер платежей → [[")
    # дописанные факты и смена статуса — не переписывание
    assert cloud_review.retention(body, body.replace("Решено", "В работе") + "- факт 9\n") > 0.8


def test_created_in_protected_dir_is_removed_not_ignored(tmp_path):
    """Файл, созданный облаком там, где писать нельзя, убирается, а не прощается.

    Откатывать нечего — копии в бэкапе нет, и раньше `if bad and restore(...)`
    на этом молча заканчивался: нарушение оставалось на диске и не попадало в
    лог. Запрет, который действует только на существовавшие до запуска файлы,
    запретом не является.
    """
    graph = _graph(tmp_path)
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    fake = graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1400_v2.md"
    fake.write_text("переписанная стенограмма\n", encoding="utf-8")
    qdir = tmp_path / "q"
    v = cloud_review.enforce_boundaries(before, graph, backup, qdir)
    assert not fake.exists(), "созданный в защищённой папке файл остался"
    assert fake.name in v.removed and v.touched == 1 and not v.reverted
    # не стёрт, а отложен: правка за те же полчаса могла быть и человеческой (№40)
    assert (qdir / "Документация" / "Стенограммы встреч" / fake.name).read_text(
        encoding="utf-8") == "переписанная стенограмма\n"


def test_non_markdown_files_are_covered_too(tmp_path):
    """Граница стережёт граф, а не расширение .md."""
    graph = _graph(tmp_path)
    data = graph / "Документация" / "Стенограммы встреч" / "запись.vtt"
    data.write_text("WEBVTT\n", encoding="utf-8")
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    data.write_text("WEBVTT\nоблако дописало\n", encoding="utf-8")
    v = cloud_review.enforce_boundaries(before, graph, backup, tmp_path / "q")
    assert data.read_text(encoding="utf-8") == "WEBVTT\n", \
        "правка не-markdown файла в защищённой папке не откачена"
    assert data.name in v.reverted


def test_invalid_answer_rolls_back_even_allowed_edits(tmp_path, monkeypatch):
    """Без отчёта правки графа — неизвестной степени готовности: слияние
    могло дойти до середины. Код ≠ 0 или обрывок — откат всего, версии
    облака в карантине (Codex, Critical 22.08)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст встречи\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"
    core = graph / "Ядра" / "Платёжный провайдер.md"
    original = core.read_text(encoding="utf-8")
    monkeypatch.setattr(cloud_review, "ROOT", tmp_path / "data")

    class Result:
        returncode = 1

    def fake_run(cmd, **kwargs):
        core.write_text(original.replace("Решено", "Решено — ЮPay"), encoding="utf-8")
        (graph / "Люди" / "Новый.md").parent.mkdir(exist_ok=True)
        (graph / "Люди" / "Новый.md").write_text("# Новый\n", encoding="utf-8")
        kwargs["stdout"].write("Ошибка: rate limit\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 1
    assert core.read_text(encoding="utf-8") == original, "разрешённая правка не откачена"
    assert not (graph / "Люди" / "Новый.md").exists()
    q = cloud_review.quarantine_root(graph) / stamp
    assert (q / "Люди" / "Новый.md").exists() and "ЮPay" in (
        q / "Ядра" / "Платёжный провайдер.md").read_text(encoding="utf-8")
    text = log.read_text(encoding="utf-8")
    assert "ответ невалиден — все правки графа (2) откачены" in text


def test_graph_lock_serialises_workers_and_degrades_to_read_only(tmp_path, monkeypatch):
    """Второй воркер того же графа ждёт первого; не дождался — чтение.

    Раньше он ротировал живой снимок соседа, и тот пропускал сверку: любые
    правки проходили без проверки (Codex, Critical 22.08)."""
    import fcntl
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "ROOT", tmp_path / "data")
    with cloud_review.graph_lock(graph, wait=0) as first:
        assert first is True
        with cloud_review.graph_lock(graph, wait=0.2) as second:
            assert second is False
    # замок отпущен вместе с дескриптором — третий берёт сразу
    with cloud_review.graph_lock(graph, wait=0) as third:
        assert third is True

    # а run() под чужим замком уходит на чтение: ни Edit, ни снимка
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст встречи\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"
    captured = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        kwargs["stdout"].write(
            "- **Решение:** оставить граф закрытым\n"
            "- **Поручение:** проверить настройку\n"
            "- **Риск:** файловый доступ не выдавался\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review, "LOCK_WAIT", 0.2)
    lock_file = cloud_review.backup_root(graph).parent / "cloud.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_file.open("w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
        assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 0
    finally:
        fd.close()
    cmd = captured["cmd"]
    assert "Edit" in cmd[cmd.index("--disallowedTools"):], "под чужим замком выдан Edit"
    assert not (cloud_review.backup_root(graph) / stamp).exists()
    assert "только чтение графа" in log.read_text(encoding="utf-8")


def test_quarantine_keeps_only_recent_runs(tmp_path):
    root = tmp_path / "q"
    for s in ("2026-07-01_1000", "2026-07-02_1000", "2026-07-03_1000"):
        (root / s).mkdir(parents=True)
    (root / "cloud.lock").write_text("", encoding="utf-8")
    cloud_review.rotate_quarantine(root, keep=2)
    assert sorted(p.name for p in root.iterdir()) == [
        "2026-07-02_1000", "2026-07-03_1000", "cloud.lock"]


def test_report_must_look_like_a_review(tmp_path):
    """Пустой или обрезанный ответ не должен публиковаться как ревизия."""
    assert not cloud_review.looks_like_report("")
    assert not cloud_review.looks_like_report("   \n\n")
    assert not cloud_review.looks_like_report("Ошибка: rate limit")
    good = "- **Решение:** взяли ЮPay\n- **Поручение:** договор до 22.07\n" * 2
    assert cloud_review.looks_like_report(good)


def test_publish_is_atomic_and_keeps_a_partial_answer(tmp_path):
    """Обрыв не должен оставлять файл-обманку, но и терять текст не надо."""
    rev = tmp_path / "ревизия.md"
    tmp = tmp_path / "ревизия.md.part"
    tmp.write_text("обрывок", encoding="utf-8")
    published = cloud_review.publish(tmp, rev, ok=False)
    assert not published
    assert not rev.exists(), "недоделанная ревизия опубликована как готовая"
    assert (tmp_path / "ревизия.md.partial").exists(), "текст ответа потерян"

    tmp.write_text("- **Решение:** ок\n" * 5, encoding="utf-8")
    assert cloud_review.publish(tmp, rev, ok=True)
    assert rev.read_text(encoding="utf-8").startswith("- **Решение:**")
    assert not tmp.exists()


def test_published_review_reaches_the_archive_and_the_vault(tmp_path, monkeypatch):
    """Ревизия облака публиковалась позже архива и копий в Документацию, а
    повторно их никто не собирал — в read-only режиме она оставалась в
    transcripts/, невидимой ни в Finder-архиве, ни в графе (аудит GLM 17.08)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    (graph / "Встречи-архив").mkdir()
    (graph / "Документация" / "Стенограммы встреч").mkdir(parents=True, exist_ok=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    transcript = transcripts / f"{stamp}_Платёжный_провайдер.md"
    transcript.write_text("стенограмма\n", encoding="utf-8")
    rev = transcripts / f"{stamp}_Платёжный_провайдер_ревизия_claude.md"
    log = tmp_path / "cloud.log"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write("# Ревизия\n\n- **Решение:** ЮPay\n- **Поручение:** договор\n- **Риск:** сроки\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    import meeting_archive
    monkeypatch.setattr(meeting_archive, "_gen_summary", lambda *a, **k: None)  # без локальной модели
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}}
    code = cloud_review.run(stamp, transcript, graph, rev, log, cfg)

    assert code == 0 and rev.exists()
    folder = graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    assert (folder / "Ревизия Claude.md").exists(), "ревизия не доехала до архива встречи"
    assert (folder / "Стенограмма.md").exists()
    assert (graph / "Документация" / "Стенограммы встреч" / rev.name).exists(), \
        "ревизия не доехала до Документации"
    assert "ревизия доставлена" in log.read_text(encoding="utf-8")


def test_review_of_an_untitled_meeting_lands_in_its_own_folder(tmp_path, monkeypatch):
    """Посекундная встреча без темы: остаток стема «30» — секунды, а не тема
    (ревью 17.08); ревизия названа минутным штампом и в папку кладётся явно."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    (graph / "Встречи-архив").mkdir()
    existing = graph / "Встречи-архив" / "2026-07-15 14-00 — встреча"
    existing.mkdir()
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    transcript = transcripts / f"{stamp}30.md"
    transcript.write_text("стенограмма\n", encoding="utf-8")
    rev = transcripts / f"{stamp}_ревизия_claude.md"
    log = tmp_path / "cloud.log"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write("# Ревизия\n\n- **Решение:** да\n- **Поручение:** нет\n- **Риск:** есть\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    import meeting_archive
    monkeypatch.setattr(meeting_archive, "_gen_summary", lambda *a, **k: None)
    code = cloud_review.run(stamp, transcript, graph, rev, log,
                            {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}})

    assert code == 0
    assert not (graph / "Встречи-архив" / "2026-07-15 14-00 — 30").exists(), "секунды стали темой папки"
    assert (existing / "Ревизия Claude.md").exists(), "ревизия не легла в папку встречи"
