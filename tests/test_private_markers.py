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
import unicodedata

import pytest

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
    # Настоящий PNG: длина чанка IHDR даёт NUL в первых байтах — git считает его двоичным.
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe not text at all")
    missing = tmp_path / "deleted.py"
    assert guard.scan_files(guard.build_pattern(list(FAKE)), [binary, missing]) == []


def test_picture_signature_without_nul_is_not_media_for_the_tree(tmp_path, monkeypatch):
    """Подпись в начале без NUL — текст в чужой кодировке, которого никто не
    прочёл: отказ с хешем блоба (№570, выход out2)."""
    monkeypatch.setenv("HOME", str(tmp_path))     # допуски владельца тесту не судья (out4 M1)
    fake = tmp_path / "logo.png"
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + "ВнутренняяСистемаЫЪ".encode("cp1251"))
    hits = guard.scan_files(guard.build_pattern(list(FAKE)), [fake])
    assert len(hits) == 1 and guard.NOT_UTF8 in hits[0] and "blob_allow.txt" in hits[0], hits


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
    markers = guard.load_markers(path)
    hits = guard.scan_files(guard.build_pattern(markers), guard.tracked_files())
    assert not hits, ("приватные маркеры в опубликованном дереве: "
                      + ", ".join(hits))


def test_personal_path_pattern_catches_a_capitalised_user_name():
    """Путь с именем пользователя с заглавной — самый частый формат утечки — не
    ловился: шаблон требовал строчную первую букву (GLM M6)."""
    import re
    pat = re.compile(guard.PUBLIC_PATTERNS["личный путь"])
    # образцы собраны из кусков, чтобы страж не ловил сам тест  # приватный-образец
    assert pat.search("cd /Users/" + "Vasilii" + "/Project/charoite")
    assert pat.search("файл /Users/" + "Василий" + "/Desktop/x.md")
    assert pat.search("/Users/" + "vasilii" + "/x/")
    assert not pat.search("/Users/user/x/") and not pat.search("/Users/ПУТЬ/x/")


@pytest.mark.parametrize("marker", ["qzx", "абв", "qzxy"])
@pytest.mark.parametrize("left,right,expected", [
    (" ", " ", True), ("-", "-", True), ("(", ")", True),
    ("x", "", False), ("", "x", False), ("я", "", False),
    ("", "я", False), ("7", "", False), ("", "7", False),
    ("_", "", False), ("", "_", False),
])
def test_protocol_short_boundaries_through_loader_and_scanner(
    tmp_path, marker, left, right, expected,
):
    """The runbook promises Python Unicode boundaries, including digits/_ ."""
    markers = tmp_path / "markers.txt"
    markers.write_text(marker + "\n", encoding="utf-8")
    document = tmp_path / "document.txt"
    document.write_text(left + marker.upper() + right, encoding="utf-8")
    pattern = guard.build_pattern(guard.load_markers(markers))
    assert bool(guard.scan_files(pattern, [document])) is expected


@pytest.mark.parametrize("marker_form", ["NFC", "NFD"])
@pytest.mark.parametrize("text_form", ["NFC", "NFD"])
@pytest.mark.parametrize("marker", ["йё", "йёqzx"])
def test_protocol_normalizes_both_marker_and_text(tmp_path, marker_form, text_form, marker):
    markers = tmp_path / "markers.txt"
    markers.write_text(unicodedata.normalize(marker_form, marker), encoding="utf-8")
    document = tmp_path / "document.txt"
    document.write_text(unicodedata.normalize(text_form, marker.upper()), encoding="utf-8")
    pattern = guard.build_pattern(guard.load_markers(markers))
    assert guard.scan_files(pattern, [document]) == [f"{document}:1"]


@pytest.mark.parametrize("marker,text,expected", [
    ("q.z", "q.z", True), ("q.z", "qaz", False),
    ("q|z", "q|z", True), ("q|z", "q", False),
    ("qzxyz", "preQZXYZpost", True),
    # Boundary syntax is literal Python \b, even for punctuation endpoints.
    ("q+", " q+ ", False), ("q+", " q+x", True),
])
def test_protocol_literals_and_long_substrings(tmp_path, marker, text, expected):
    markers = tmp_path / "markers.txt"
    markers.write_text(marker, encoding="utf-8")
    document = tmp_path / "document.txt"
    document.write_text(text, encoding="utf-8")
    pattern = guard.build_pattern(guard.load_markers(markers))
    assert bool(guard.scan_files(pattern, [document])) is expected
