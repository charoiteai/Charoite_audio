"""Дедупликация файлов графа жёсткими ссылками.

Конвейер кладёт документы встречи дважды: оригинал в «Документация», копию —
в «Встречи-архив» для Finder. На рабочем графе это 214 групп и 37% объёма:
лишняя синхронизация iCloud и лишний вес на iPhone. Копии связываются, но
обе дороги остаются рабочими — папку для Finder никто не отменял.
"""
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dedup_graph as d  # noqa: E402

BODY = "# Встреча\n" + ("Обсудили платёжного провайдера. " * 400)


def _graph(tmp_path):
    orig = tmp_path / "Документация" / "Стенограммы встреч"
    arch = tmp_path / "Встречи-архив" / "2026-07-24 09-11 — Постман"
    orig.mkdir(parents=True)
    arch.mkdir(parents=True)
    a = orig / "2026-07-24_0911_hints.md"
    b = arch / "Подсказки и ответы.md"
    a.write_text(BODY, encoding="utf-8")
    b.write_text(BODY, encoding="utf-8")
    return a, b


def test_copy_is_linked_to_original(tmp_path, capsys):
    a, b = _graph(tmp_path)
    assert a.stat().st_ino != b.stat().st_ino

    sys.argv = ["dedup_graph", "--graph", str(tmp_path), "--apply"]
    d.main()

    assert a.stat().st_ino == b.stat().st_ino, "копия не связана с оригиналом"
    assert b.read_text(encoding="utf-8") == BODY, "содержимое копии изменилось"


def test_edit_through_either_path_is_visible(tmp_path):
    a, b = _graph(tmp_path)
    sys.argv = ["dedup_graph", "--graph", str(tmp_path), "--apply"]
    d.main()

    b.write_text(BODY + "\nдописано в архиве\n", encoding="utf-8")
    assert "дописано в архиве" in a.read_text(encoding="utf-8"), \
        "правка через архивный путь не видна в оригинале"


def test_dry_run_changes_nothing(tmp_path):
    a, b = _graph(tmp_path)
    sys.argv = ["dedup_graph", "--graph", str(tmp_path)]
    d.main()
    assert a.stat().st_ino != b.stat().st_ino, "сухой прогон связал файлы"


def test_second_run_is_idempotent(tmp_path, capsys):
    _graph(tmp_path)
    sys.argv = ["dedup_graph", "--graph", str(tmp_path), "--apply"]
    d.main()
    capsys.readouterr()
    d.main()
    out = capsys.readouterr().out
    assert "связано: 0" in out, f"повторный прогон делает лишнюю работу: {out}"


def test_original_is_never_the_archive_copy(tmp_path):
    """Оригиналом считается файл вне архива — архив производен."""
    a, b = _graph(tmp_path)
    assert d.pick_original([b, a]) == a
    assert d.pick_original([a, b]) == a


def test_small_files_are_left_alone(tmp_path):
    """Мелочь не трогаем: выигрыш меньше, чем путаница от жёстких ссылок."""
    orig = tmp_path / "Люди"
    orig.mkdir()
    a = orig / "Дмитрий.md"
    b = orig / "Дмитрий (копия).md"
    a.write_text("# Дмитрий\nаналитик", encoding="utf-8")
    b.write_text("# Дмитрий\nаналитик", encoding="utf-8")

    sys.argv = ["dedup_graph", "--graph", str(tmp_path), "--apply"]
    d.main()

    assert a.stat().st_ino != b.stat().st_ino


def test_nightly_does_not_hardcode_apply():
    """Ночная джоба не решает за человека — то же правило, что у tier3.

    Право на правку графа берётся из конфига (sufler.dedup_files), а не из
    строки запуска: launchd в 04:15 не должен необратимо менять файлы во всех
    графах vault у пользователя, который никакого разрешения не давал.
    """
    nightly = (SCRIPTS / "nightly.sh").read_text(encoding="utf-8")
    dedup_line = next(ln for ln in nightly.splitlines() if "dedup_graph.py" in ln)
    assert "--apply" not in dedup_line, \
        "дедуп в nightly.sh с захардкоженным --apply — право должно браться из конфига"


def test_config_switch_is_strictly_true(tmp_path, monkeypatch):
    """«false», пустое значение и мусор разрешением не считаются."""
    for value, allowed in [(True, True), (False, False), ("true", False),
                           ("", False), (1, False), (None, False)]:
        monkeypatch.setattr(d, "_cfg", lambda v=value: {"sufler": {"dedup_files": v}})
        assert d._allowed_by_config() is allowed, f"значение {value!r} трактовано неверно"
