"""Согласованность жизненного цикла встречи: rename -> forget -> повтор.

Пер-модульные тесты (test_rename_meeting, test_forget_meeting,
test_archive_dedup) держат каждую операцию отдельно. Этот файл — швы:
встреча, которую переименовали, обязана остаться забываемой; забытая —
забытой при повторе; двойное переименование не плодит следов. Это
характеризационная сеть C-P0 (фаза 0): тесты фиксируют, как связка
ведёт себя СЕЙЧАС, чтобы фазы 1-2 меняли структуру на страховке.

Зафиксированные факты поведения (проверено прогоном 25.08):
- rename переименовывает главные файлы transcripts и архивную папку,
  а узел графа «Встречи/<штамп>.md» оставляет под прежним именем —
  тема правится внутри (заголовок/aliases);
- forget находит места встречи через meeting_stamp (граница штампа,
  archive_time), а не по точному имени файла — потому переименованная
  встреча остаётся забываемой без специальной поддержки.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import forget_meeting as forget  # noqa: E402
import rename_meeting as rename  # noqa: E402

STAMP = "2026-07-15_1400"
OTHER = "2026-07-16_1000"


def _world(tmp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    root, graph = tmp / "repo", tmp / "vault" / "Работа"
    (root / "transcripts").mkdir(parents=True)
    (root / "recordings").mkdir(parents=True)
    for stamp in (STAMP, OTHER):
        (root / "transcripts" / f"{stamp}.md").write_text(
            f"# Встреча {stamp}\n[14:00] Я: начнём.\n", encoding="utf-8")
        (root / "recordings" / f"{stamp}_mic.pcm").write_bytes(b"\x00\x01")
        (graph / "Встречи").mkdir(parents=True, exist_ok=True)
        (graph / "Встречи" / f"{stamp}.md").write_text(
            f"---\ntype: встреча\n---\n# {stamp}\n", encoding="utf-8")
        (graph / "Документация" / "Стенограммы встреч").mkdir(parents=True, exist_ok=True)
        (graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md").write_text(
            "копия\n", encoding="utf-8")
    arch = graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    arch.mkdir(parents=True)
    for name in ("Саммари.md", "Стенограмма.md"):
        (arch / name).write_text(f"[[Встречи/{STAMP}]]\n", encoding="utf-8")
    (graph / "Ядра").mkdir()
    (graph / "Ядра" / "Провайдер.md").write_text(
        f"---\ntype: ядро\n---\n## Хроника\n- [[Встречи/{STAMP}]] — выбрали\n",
        encoding="utf-8")
    return root, graph


def _rename(graph, tdir, title):
    pretty, slug = rename.pretty_and_slug(title)
    rename.apply(rename.plan(graph, tdir, STAMP, pretty, slug), graph, STAMP, pretty)


def test_переименованная_встреча_остаётся_забываемой(tmp_path):
    root, graph = _world(tmp_path)
    _rename(graph, root / "transcripts", "Новая тема")

    fp = forget.plan(STAMP, root, graph)
    doomed = {str(x) for x in fp.delete}
    # Узел живёт под ПРЕЖНИМ именем (rename правит содержимое, не имя) —
    # и forget его видит.
    assert str(graph / "Встречи" / f"{STAMP}.md") in doomed
    # Переименованные места — стенограмма с темой и архив с новым именем —
    # тоже в плане: forget ищет границей штампа, не точным именем.
    assert str(root / "transcripts" / f"{STAMP}_Новая_тема.md") in doomed
    assert any("Встречи-архив" in d and "Новая тема" in d for d in doomed), doomed


def test_забвение_идемпотентно_после_переименования(tmp_path):
    root, graph = _world(tmp_path)
    _rename(graph, root / "transcripts", "Тема")
    forget.apply(forget.plan(STAMP, root, graph), yes=True)

    second = forget.plan(STAMP, root, graph)
    assert not second.delete, "повторный forget нашёл что удалять: " + \
        ", ".join(str(x) for x in second.delete)
    # Соседка не задета ни одним из проходов.
    assert (graph / "Встречи" / f"{OTHER}.md").exists()
    assert (root / "transcripts" / f"{OTHER}.md").exists()


def test_двойное_переименование_не_плодит_следов(tmp_path):
    root, graph = _world(tmp_path)
    tdir = root / "transcripts"
    _rename(graph, tdir, "Первая тема")
    _rename(graph, tdir, "Вторая тема")

    mains = sorted(p.name for p in tdir.glob(f"{STAMP}*.md")
                   if "_minutes" not in p.name)
    assert mains == [f"{STAMP}_Вторая_тема.md"], mains
    arch = [p.name for p in (graph / "Встречи-архив").iterdir()
            if p.name.startswith("2026-07-15")]
    assert arch == ["2026-07-15 14-00 — Вторая тема"], arch
    # Узел один и под прежним именем; промежуточной темы не осталось нигде.
    nodes = [p.name for p in (graph / "Встречи").glob(f"{STAMP}*")]
    assert nodes == [f"{STAMP}.md"], nodes
    leftovers = [p for p in graph.rglob("*") if "Первая" in p.name]
    assert not leftovers, leftovers


def test_ключ_памяти_общий_у_rename_и_forget():
    """brain :8100 знает встречу по штампу: rename шлёт /rename тем же
    штампом, каким forget собирает brain_keys. Контракт по исходникам —
    живой HTTP в юнитах не нужен (стиль test_toggle_status)."""
    fm = (REPO / "scripts" / "forget_meeting.py").read_text(encoding="utf-8")
    rn = (REPO / "scripts" / "rename_meeting.py").read_text(encoding="utf-8")
    assert "brain_keys" in fm and "/forget" in fm
    assert "def brain_rename(stamp: str" in rn and "/rename" in rn
    # Обе операции меряют принадлежность файлов встрече через meeting_stamp
    # (граница штампа) — а не самодельным префиксом.
    assert "import meeting_stamp" in fm and "import meeting_stamp" in rn
