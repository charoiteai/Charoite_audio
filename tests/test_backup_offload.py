"""Снимки графа уезжают из iCloud и перестают дублировать байты.

Факт 21.08: граф живёт в хранилище Обсидиана, а `cloud_review` клал снимок
внутрь самого графа. Каждая облачная правка копировала граф целиком, и к
этому дню в iCloud лежало 48 122 служебных файла на 1.7 ГБ — из 58 260
файлов и 1.9 ГБ всего архива. Синхронизацию этого балласта система вела
круглосуточно (`fileproviderd` 127% CPU, плюс `bird`, `fseventsd`,
`filecoordination`), и живой записи не хватало процессора: стенограмма
отставала и рвалась.

Здесь проверяется, что снимок ложится к данным, а не в граф, и что копии
не платят за себя дважды: на APFS файлы берутся клоном (copy-on-write).
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import charoite_paths  # noqa: E402
import cloud_review  # noqa: E402


def _graph(tmp_path: pathlib.Path) -> pathlib.Path:
    g = tmp_path / "vault" / "рабочий_граф"
    (g / "Встречи").mkdir(parents=True)
    (g / "Ядра").mkdir()
    (g / "Встречи" / "2026-08-21_1103.md").write_text("узел встречи", encoding="utf-8")
    (g / "Ядра" / "Оплата.md").write_text("ядро темы", encoding="utf-8")
    return g


def test_snapshot_path_is_outside_the_graph(tmp_path):
    """Снимок обязан лежать в данных: в графе его синхронизирует iCloud."""
    g = _graph(tmp_path)
    dest = charoite_paths.graph_backups(g, "cloud_backup", root=tmp_path / "data")
    assert not dest.is_relative_to(g), "снимок остался внутри графа"
    assert dest.parts[-2:] == ("рабочий_граф", "cloud_backup"), dest


def test_snapshots_of_different_graphs_do_not_mix(tmp_path):
    a = charoite_paths.graph_backups(tmp_path / "рабочий_граф", root=tmp_path / "d")
    b = charoite_paths.graph_backups(tmp_path / "дом", root=tmp_path / "d")
    assert a != b


def test_backup_graph_writes_next_to_data(tmp_path, monkeypatch):
    """Полный снимок: файлы на месте, но в корне данных, а не в графе."""
    g = _graph(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(cloud_review, "ROOT", data)

    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    assert dest.is_relative_to(data), dest
    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"
    assert (dest / "Ядра" / "Оплата.md").exists()
    assert not (g / ".cloud_backup").exists(), "в графе не должно остаться снимков"


def test_snapshot_survives_rewrite_of_the_original(tmp_path, monkeypatch):
    """Клон — независимый файл: правка графа не должна протечь в снимок.

    Ради этого и взят `clonefile`, а не жёсткая ссылка: конвейер пишет через
    `tmp.replace()` (новый inode), но человек правит те же заметки руками в
    Обсидиане, и запись на месте не запрещена никем.
    """
    g = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "ROOT", tmp_path / "data")
    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    node = g / "Встречи" / "2026-08-21_1103.md"
    with node.open("w", encoding="utf-8") as f:      # именно на месте, не replace
        f.write("ПЕРЕПИСАНО ЧЕЛОВЕКОМ")

    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"


def test_clone_is_used_and_gives_an_independent_file(tmp_path):
    """Клон обязан отработать на APFS — иначе снимок опять платит за байты.

    Отличить клон от копии по `stat` нельзя: `st_blocks` у обоих одинаковые,
    разделение блоков видно только файловой системе. Проверяем прямо: вызов
    сказал «получилось», файл читается и живёт своим inode.
    """
    src = tmp_path / "a.md"
    src.write_text("строка стенограммы\n" * 20_000, encoding="utf-8")
    dst = tmp_path / "b.md"

    assert cloud_review._clone(src, dst) is True, "clonefile не отработал на этом томе"
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert dst.stat().st_ino != src.stat().st_ino


def test_copy_is_the_fallback_when_clone_fails(tmp_path, monkeypatch):
    """Не APFS, другой том, старая система — снимок всё равно полный."""
    g = _graph(tmp_path)
    monkeypatch.setattr(cloud_review, "ROOT", tmp_path / "data")
    monkeypatch.setattr(cloud_review, "_clone", lambda src, dst: False)

    dest = cloud_review.backup_graph(g, "2026-08-21_1200")

    assert (dest / "Встречи" / "2026-08-21_1103.md").read_text(encoding="utf-8") == "узел встречи"
    assert (dest / "Ядра" / "Оплата.md").read_text(encoding="utf-8") == "ядро темы"
