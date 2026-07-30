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
