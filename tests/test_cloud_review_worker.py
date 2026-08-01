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
    # в удалённом узле был раздел «## Правки автора» — его пропажа нарушение
    reverted, removed, _ = cloud_review.enforce_boundaries(before, graph, backup)
    assert core.exists(), "удалённый узел с правками автора не восстановлен"
    assert core.name in reverted and not removed


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
    reverted, removed, touched = cloud_review.enforce_boundaries(before, graph, backup)
    assert not fake.exists(), "созданный в защищённой папке файл остался"
    assert fake.name in removed and touched == 1 and not reverted


def test_non_markdown_files_are_covered_too(tmp_path):
    """Граница стережёт граф, а не расширение .md."""
    graph = _graph(tmp_path)
    data = graph / "Документация" / "Стенограммы встреч" / "запись.vtt"
    data.write_text("WEBVTT\n", encoding="utf-8")
    before = cloud_review.snapshot(graph)
    backup = cloud_review.backup_graph(graph, "2026-07-15_1400")
    data.write_text("WEBVTT\nоблако дописало\n", encoding="utf-8")
    reverted, removed, _ = cloud_review.enforce_boundaries(before, graph, backup)
    assert data.read_text(encoding="utf-8") == "WEBVTT\n", \
        "правка не-markdown файла в защищённой папке не откачена"
    assert data.name in reverted


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
