"""Страж обезличивания смотрел только диф — и пропускал то, что уже в main.

Публичный репозиторий собирается из приватной сборки, поэтому маркеры (личные
имена, внутренние системы, пути автора) вычищаются перед публикацией. Проверял
это `scripts/check_private_markers.py` по `git diff --cached`: строка, попавшая
в main ДО того, как маркер внесли в список, оставалась в публичном дереве
навсегда — страж её не видит ни при одном следующем коммите.

Так и было: полный проход по `git ls-files` тем же списком нашёл две живые
строки в docstring-ах. Сами строки здесь привести нельзя — тест бы заблокировал
собственный коммит, и это лучшее доказательство, что механизм работает.

Список маркеров приватен и лежит вне git, поэтому тесты работают на подставном
списке через переменную `CHAROITE_MARKERS`: проверяется механизм, а не
содержимое чужой тайны.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import check_private_markers as guard  # noqa: E402

FAKE = ("ВнутренняяСистемаЫЪ", "ЗАО")


def test_short_markers_match_by_word_boundary():
    """Аббревиатура не должна находиться внутри обычного слова.

    Иначе страж врёт, а тому, кто получил ложную блокировку, проще начать его
    обходить, чем разбираться.
    """
    pattern = guard.build_pattern(list(FAKE))
    assert pattern.search("подписал ЗАО и уехал")
    assert not pattern.search("Мазаофилия — не аббревиатура")


def test_scan_finds_a_marker_that_is_already_committed(tmp_path):
    """Главный пробел: маркер лежит в файле давно, в дифе его нет."""
    old = tmp_path / "src" / "legacy.py"
    old.parent.mkdir(parents=True)
    old.write_text("# запуск на ВнутренняяСистемаЫЪ\nX = 1\n", encoding="utf-8")
    clean = tmp_path / "src" / "fine.py"
    clean.write_text("X = 2\n", encoding="utf-8")

    hits = guard.scan_files(guard.build_pattern(list(FAKE)), [old, clean])
    assert hits == ["src/legacy.py:1"] or hits == [f"{old}:1"], hits


def test_scan_reports_place_without_quoting_the_marker(tmp_path):
    """В отчёт идёт место, а не строка: логи CI и вывод в чужом терминале не
    должны становиться ещё одной копией того, что мы прячем."""
    f = tmp_path / "leak.md"
    f.write_text("ничего\nтут ЗАО живёт\n", encoding="utf-8")
    for hit in guard.scan_files(guard.build_pattern(list(FAKE)), [f]):
        assert "ЗАО" not in hit, f"страж процитировал маркер: {hit}"
        assert hit.endswith(":2"), hit


def test_binary_and_missing_files_do_not_break_the_scan(tmp_path):
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe not text at all")
    missing = tmp_path / "deleted.py"
    assert guard.scan_files(guard.build_pattern(list(FAKE)), [binary, missing]) == []


def test_the_tree_of_this_repository_is_clean():
    """Ради этого всё и делается: в опубликованном дереве маркеров нет.

    Без доступного списка (CI, свежий клон контрибьютора) проверка нечестна —
    тогда тест сообщает, что проверить нечем, и не притворяется зелёным по
    существу.
    """
    path = guard.markers_path()
    if not path.exists():
        import pytest
        pytest.skip(f"список маркеров недоступен ({path}) — проверять нечем")
    markers = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
    hits = guard.scan_files(guard.build_pattern(markers), guard.tracked_files())
    assert not hits, ("приватные маркеры в опубликованном дереве: "
                      + ", ".join(hits))
