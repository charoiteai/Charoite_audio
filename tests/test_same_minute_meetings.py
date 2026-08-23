"""Две встречи одной минуты (крэш-рестарт демона) живут в графе порознь.

Демон после краха поднимается через две секунды — внутри той же минуты.
Стенограммы и записи уже различались секундами, но граф ключевал встречу
МИНУТОЙ: вторая затирала заметку первой, архив переименовывал её папку под
новую тему, «забыть» и переименование брали чужую заметку (аудит 16–17.08,
карточка №39). Правило одно — meeting_stamp.graph_key: минута у владельца
(самая ранняя в минуте), секунды у соседки; архив, поиск заметки, forget и
rename ходят через него.
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import meeting_stamp as ms  # noqa: E402
import meeting_processing as mp  # noqa: E402
from meeting_archive import ARCHIVE_DIR, _folders_for  # noqa: E402
import forget_meeting as forget  # noqa: E402
import rename_meeting as rm  # noqa: E402

MIN = "2026-08-21_1258"
FIRST, SECOND = "2026-08-21_125810", "2026-08-21_125812"


def _transcripts(tmp_path, *stems):
    tdir = tmp_path / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (tdir / f"{s}.md").write_text(f"# Встреча {s}\n", encoding="utf-8")
    return tdir


def test_owner_is_the_earliest_and_the_neighbour_keeps_seconds(tmp_path):
    tdir = _transcripts(tmp_path, FIRST, SECOND)
    assert ms.graph_key(tdir, FIRST) == MIN
    assert ms.graph_key(tdir, SECOND) == SECOND
    # порядок обработки не важен: вторая, разобранная первой, тоже получает секунды
    assert ms.graph_key(tdir, SECOND) == ms.graph_key(tdir, SECOND)


def test_key_survives_retitle_and_retry(tmp_path):
    tdir = _transcripts(tmp_path, FIRST, SECOND)
    (tdir / f"{FIRST}.md").rename(tdir / f"{MIN}_Первая.md")          # владелец с темой
    (tdir / f"{SECOND}.md").rename(tdir / f"{SECOND}_Вторая.md")      # соседка с темой
    assert ms.graph_key(tdir, f"{MIN}_Первая") == MIN
    assert ms.graph_key(tdir, f"{SECOND}_Вторая") == SECOND
    # ретрай соседки до наката темы, когда владелец уже назван минутой
    (tdir / f"{SECOND}_Вторая.md").rename(tdir / f"{SECOND}.md")
    assert ms.graph_key(tdir, SECOND) == SECOND


def test_legacy_minute_stamp_and_single_meeting_keep_minute(tmp_path):
    tdir = _transcripts(tmp_path, "2026-07-10_1130", SECOND)
    assert ms.graph_key(tdir, "2026-07-10_1130") == "2026-07-10_1130"
    assert ms.graph_key(tdir, SECOND) == MIN                            # одна в минуте


def test_existing_note_outweighs_the_directory(tmp_path):
    tdir = _transcripts(tmp_path, SECOND)                                # соседку забыли
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / f"{SECOND}.md").write_text("# b", encoding="utf-8")
    assert ms.graph_key(tdir, SECOND, graph) == SECOND
    # минутная заметка чужой встречи (по строке «Стенограмма:») — тоже в секунды
    (graph / "Встречи" / f"{SECOND}.md").unlink()
    (graph / "Встречи" / f"{MIN}.md").write_text(
        f"# a\n\nСтенограмма: `{tdir / FIRST}.md`\n", encoding="utf-8")
    assert ms.graph_key(tdir, SECOND, graph) == SECOND
    # своя минутная заметка (ретрай владельца) — минута
    (graph / "Встречи" / f"{MIN}.md").write_text(
        f"# a\n\nСтенограмма: `{tdir / SECOND}.md`\n", encoding="utf-8")
    assert ms.graph_key(tdir, SECOND, graph) == MIN


def test_find_note_does_not_take_the_neighbours_note(tmp_path):
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    note = graph / "Встречи" / f"{MIN}.md"
    note.write_text(f"# a\n\nСтенограмма: `/t/{FIRST}_Первая.md`\n", encoding="utf-8")
    assert ms.find_note(graph, FIRST) == note
    assert ms.find_note(graph, SECOND) is None
    own = graph / "Встречи" / f"{SECOND}.md"
    own.write_text("# b", encoding="utf-8")
    assert ms.find_note(graph, SECOND) == own
    # владелец, уже названный минутой: в строке нет секунд — спорить нечем
    note.write_text(f"# a\n\nСтенограмма: `/t/{MIN}_Первая.md`\n", encoding="utf-8")
    assert ms.find_note(graph, FIRST) == note


def test_archive_folders_are_separate_and_not_hijacked(tmp_path):
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    arch.mkdir(parents=True)
    first = arch / "2026-08-21 12-58 — Первая"
    first.mkdir()
    (first / "meeting.meta.json").write_text(json.dumps({"meeting_id": MIN}), encoding="utf-8")
    second = arch / "2026-08-21 12-58-12 — Вторая"
    second.mkdir()
    (second / "meeting.meta.json").write_text(json.dumps({"meeting_id": SECOND}), encoding="utf-8")
    assert _folders_for(graph, MIN) == [first]
    assert _folders_for(graph, SECOND) == [second]
    assert ms.archive_time(SECOND) == "12-58-12" and ms.archive_time(MIN) == "12-58"
    # forget и rename видят те же границы
    assert forget._archive_folders(graph, MIN) == [first]
    assert forget._archive_folders(graph, SECOND) == [second]
    assert rm.archive_folder(graph, MIN) == first
    assert rm.archive_folder(graph, SECOND) == second


def test_find_meeting_note_uses_the_current_file_and_key(tmp_path, monkeypatch):
    tdir = _transcripts(tmp_path, FIRST, SECOND)
    graph = tmp_path / "vault" / "Работа"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / f"{MIN}.md").write_text(
        f"# a\n\nСтенограмма: `{tdir / MIN}_Первая.md`\n", encoding="utf-8")
    (graph / "Встречи" / f"{SECOND}.md").write_text("# b", encoding="utf-8")
    (tdir / f"{FIRST}.md").rename(tdir / f"{MIN}_Первая.md")
    (tdir / f"{SECOND}.md").rename(tdir / f"{SECOND}_Вторая.md")
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    cfg = {"sufler": {"graph_dir": str(graph)}}
    # статус знает старые (голые) имена — как retry из приложения
    assert mp.find_meeting_note(cfg, tdir / f"{FIRST}.md") == (graph / "Встречи" / f"{MIN}.md").resolve()
    assert mp.find_meeting_note(cfg, tdir / f"{SECOND}.md") == (graph / "Встречи" / f"{SECOND}.md").resolve()
    assert mp.find_final_transcript(tdir / f"{SECOND}.md").name == f"{SECOND}_Вторая.md"
    assert mp.find_final_transcript(tdir / f"{FIRST}.md").name == f"{MIN}_Первая.md"


def test_forget_second_meeting_leaves_the_first_untouched(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    tdir = _transcripts(root, FIRST, SECOND)
    (root / "recordings").mkdir()
    (root / "logs").mkdir()
    graph = tmp_path / "vault" / "Работа"
    (graph / "Встречи").mkdir(parents=True)
    first_note = graph / "Встречи" / f"{MIN}.md"
    first_note.write_text(f"# a\n\nСтенограмма: `{tdir / FIRST}.md`\n", encoding="utf-8")
    second_note = graph / "Встречи" / f"{SECOND}.md"
    second_note.write_text("# b", encoding="utf-8")
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 12-58 — Первая").mkdir(parents=True)
    (arch / "2026-08-21 12-58 — Первая" / "meeting.meta.json").write_text(
        json.dumps({"meeting_id": MIN}), encoding="utf-8")
    (arch / "2026-08-21 12-58-12 — Вторая").mkdir()
    monkeypatch.setattr(forget, "ROOT", root, raising=False)
    p = forget.plan(SECOND, root, graph)
    doomed = {d.name for d in p.delete}
    assert f"{SECOND}.md" in doomed and "2026-08-21 12-58-12 — Вторая" in doomed
    assert f"{FIRST}.md" not in doomed and f"{MIN}.md" not in doomed
    assert "2026-08-21 12-58 — Первая" not in doomed


def test_unprocessed_neighbour_does_not_claim_the_owners_note(tmp_path):
    """Сценарий DeepSeek (круг-1 по PR #388): A разобрана и названа минутой,
    B записана, но не разобрана; «забыть B» по посекундному штампу не должно
    брать заметку A — владелец минуты тот, чей файл назван минутой."""
    tdir = _transcripts(tmp_path, SECOND)
    (tdir / f"{MIN}_Первая.md").write_text("# a", encoding="utf-8")
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    note = graph / "Встречи" / f"{MIN}.md"
    note.write_text(f"# a\n\nСтенограмма: `{tdir / MIN}_Первая.md`\n", encoding="utf-8")
    assert ms.find_note(graph, SECOND, tdir) is None
    assert ms.find_note(graph, FIRST, tdir) == note          # у владельца файла с секундами нет
    # заметка без строки (наследие): то же правило
    note.write_text("# a\n", encoding="utf-8")
    assert ms.find_note(graph, SECOND, tdir) is None
    assert ms.find_note(graph, FIRST, tdir) == note
    assert ms.find_note(graph, MIN, tdir) == note


def test_import_repeat_is_recognised_for_audio_header_too(tmp_path):
    import import_meeting as im
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / f"{MIN}.md").write_text(f"# Встреча {MIN} — запись Recording.m4a\n\n**Голос**\n", encoding="utf-8")
    assert im.import_stamp(tdir, MIN, "Recording.m4a", "10")[1] is not None
    stamp, already = im.import_stamp(tdir, MIN, "Recording 2.m4a", "40")
    assert (stamp, already) == (f"{MIN}40", None)


def test_same_file_name_but_different_recording_is_not_a_repeat(tmp_path):
    """Диктофон экспортирует всё как Recording.m4a (Sonnet, круг-1): повтор —
    то же имя И тот же размер; другой размер — другая запись, секунды."""
    import import_meeting as im
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / f"{MIN}_Планёрка.md").write_text(
        f"# Встреча {MIN} — Планёрка — импорт Recording.m4a (1000 Б)\n", encoding="utf-8")
    assert im.import_stamp(tdir, MIN, "Recording.m4a", "41", 1000)[1] is not None   # та же
    assert im.import_stamp(tdir, MIN, "Recording.m4a", "41", 2000) == (f"{MIN}41", None)
    # шапка до 23.08 без размера — сравнение только по имени, как раньше
    (tdir / f"{MIN}_Планёрка.md").write_text(
        f"# Встреча {MIN} — Планёрка — импорт Recording.m4a\n", encoding="utf-8")
    assert im.import_stamp(tdir, MIN, "Recording.m4a", "41", 2000)[1] is not None
    assert im.source_mark("a.m4a", 5) == "a.m4a (5 Б)" and im.source_mark("a.m4a", None) == "a.m4a"
    assert im.same_source("# Встреча x — запись a b.m4a (7 Б)", "a b.m4a", 7)
    assert not im.same_source("# Встреча x — запись a b.m4a (7 Б)", "a b.m4a", 8)


def test_rename_of_the_owner_leaves_the_neighbours_files_alone(tmp_path):
    """Codex, круг-1 по PR #388: переименование минутного владельца брало
    «…125812.md»/«…125812_hints.md» соседки как свои и «занимало» ими целевое
    имя. Файлы соседки не трогаются; бесхозные посекундные производные без
    главного файла — по-прежнему владельца."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (tdir / f"{MIN}_Первая.md").write_text("# a", encoding="utf-8")
    (tdir / f"{SECOND}.md").write_text("# b", encoding="utf-8")
    (tdir / f"{SECOND}_hints.md").write_text("подсказки b", encoding="utf-8")
    (tdir / f"{MIN}30_hints.md").write_text("бесхозные подсказки владельца", encoding="utf-8")
    pretty, slug = rm.pretty_and_slug("Новая")
    p = rm.plan(graph, tdir, MIN, pretty, slug)
    moved = {old.name: new.name for old, new in p["moves"]}
    assert moved == {f"{MIN}_Первая.md": f"{MIN}_Новая.md",
                     f"{MIN}30_hints.md": f"{MIN}_Новая_hints.md"}
    # а соседку можно переименовать отдельно, её же посекундным ключом
    p2 = rm.plan(graph, tdir, SECOND, *rm.pretty_and_slug("Вторая"))
    assert {o.name for o, _ in p2["moves"]} == {f"{SECOND}.md", f"{SECOND}_hints.md"}


def test_collision_suffix_is_part_of_the_identity(tmp_path):
    """«…125812-1» — другая встреча, не «…125812» (Codex, круг-1 по PR #388):
    своя папка архива, свои файлы у forget/archive, свой штамп в списке."""
    assert ms.archive_time(f"{SECOND}-1") == "12-58-12-1"
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    for n in (f"{SECOND}.md", f"{SECOND}_minutes.md", f"{SECOND}-1.md", f"{SECOND}-1_minutes.md"):
        (tdir / n).write_text("x", encoding="utf-8")
    assert {p.name for p in ms.files_with_stamp(tdir, SECOND, suffix=".md")} == {f"{SECOND}.md", f"{SECOND}_minutes.md"}
    assert {p.name for p in ms.files_with_stamp(tdir, f"{SECOND}-1", suffix=".md")} == {f"{SECOND}-1.md", f"{SECOND}-1_minutes.md"}
    root = tmp_path
    (root / "recordings").mkdir()
    assert f"{SECOND}-1" in forget.stamps(root, tmp_path / "graph")
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 12-58-12 — A").mkdir(parents=True)
    (arch / "2026-08-21 12-58-12-1 — B").mkdir()
    assert [d.name for d in _folders_for(graph, SECOND)] == ["2026-08-21 12-58-12 — A"]
    assert [d.name for d in _folders_for(graph, f"{SECOND}-1")] == ["2026-08-21 12-58-12-1 — B"]


def test_legacy_date_folder_of_another_meeting_survives_forget(tmp_path):
    """DeepSeek, круг-2 по PR #388: папка «дата — тема» ДРУГОЙ встречи того же
    дня — сомнение, её не трогаем; единственная за день — наша."""
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 12-58 — A").mkdir(parents=True)
    (arch / "2026-08-21 — Совещание").mkdir()
    assert [d.name for d in forget._archive_folders(graph, MIN)] == ["2026-08-21 12-58 — A"]
    (arch / "2026-08-21 12-58 — A").rmdir()
    assert [d.name for d in forget._archive_folders(graph, MIN)] == ["2026-08-21 — Совещание"]
    (arch / "2026-08-21 — Другое").mkdir()
    assert forget._archive_folders(graph, MIN) == []          # две legacy — обе под сомнением


def test_day_folders_in_all_three_formats_and_the_dateless_rule(tmp_path):
    """Codex/Sonnet, круг-2 по PR #388: «дата_время — тема» и вторая папка
    «дата — тема» — сомнение; папка без времени — только единственная за день."""
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 — Безвременная").mkdir(parents=True)
    (arch / "2026-08-21_1400 — Другая").mkdir()
    assert forget._archive_folders(graph, MIN) == []                       # есть другая встреча дня
    assert [d.name for d in forget._archive_folders(graph, "2026-08-21_1400")] == ["2026-08-21_1400 — Другая"]
    (arch / "2026-08-21_1400 — Другая").rmdir()
    assert [d.name for d in forget._archive_folders(graph, MIN)] == ["2026-08-21 — Безвременная"]
    (arch / "2026-08-21 — Вечерняя").mkdir()
    assert forget._archive_folders(graph, "2026-08-21_0900") == []         # две без времени — обе под сомнением
    # посекундная и с суффиксом в старом формате имени
    (arch / "2026-08-21_125812 — Вторая").mkdir()
    (arch / "2026-08-21_125812-1 — Третья").mkdir()
    assert [d.name for d in forget._archive_folders(graph, SECOND)] == ["2026-08-21_125812 — Вторая"]
    assert [d.name for d in forget._archive_folders(graph, f"{SECOND}-1")] == ["2026-08-21_125812-1 — Третья"]


def test_same_source_with_size_like_tail_in_the_name():
    import import_meeting as im
    assert im.same_source("# Встреча x — запись memo (7 Б).m4a", "memo (7 Б).m4a", 100)      # шапка без размера
    assert im.same_source("# Встреча x — запись memo (7 Б).m4a (100 Б)", "memo (7 Б).m4a", 100)
    assert not im.same_source("# Встреча x — запись memo (7 Б).m4a (100 Б)", "memo (7 Б).m4a", 101)
    assert not im.same_source("# Встреча x — импорт other.m4a (100 Б)", "memo.m4a", 100)


def test_orphan_derivative_with_a_graph_trace_stays_with_the_neighbour(tmp_path):
    """Sonnet, круг-2: главный файл соседки унесли, а «…125812_hints.md»
    остался — при заметке соседки в графе он не переходит владельцу."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (tdir / f"{MIN}_Первая.md").write_text("# a", encoding="utf-8")
    (tdir / f"{SECOND}_hints.md").write_text("подсказки соседки", encoding="utf-8")
    (graph / "Встречи" / f"{SECOND}.md").write_text("# b", encoding="utf-8")
    p = rm.plan(graph, tdir, MIN, *rm.pretty_and_slug("Новая"))
    assert {o.name for o, _ in p["moves"]} == {f"{MIN}_Первая.md"}


def test_dateless_folder_with_foreign_manifest_and_odd_spacing(tmp_path):
    """Codex, круг-3 по PR #388: единственная папка «дата — тема» с чужим
    meeting_id — не наша; лишние пробелы вокруг тире не прячут папку."""
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 — Чужая").mkdir(parents=True)
    (arch / "2026-08-21 — Чужая" / "meeting.meta.json").write_text(
        json.dumps({"meeting_id": "2026-08-21_1400"}), encoding="utf-8")
    assert forget._archive_folders(graph, MIN) == []
    assert [d.name for d in forget._archive_folders(graph, "2026-08-21_1400")] == ["2026-08-21 — Чужая"]
    (arch / "2026-08-21 12-58  —  Тема").mkdir()
    assert [d.name for d in forget._archive_folders(graph, MIN)] == ["2026-08-21 12-58  —  Тема"]


def test_manifest_owner_wins_regardless_of_folder_name(tmp_path):
    """DeepSeek, круг-3: две папки «дата — тема», у одной манифест с нашим
    meeting_id — её забываем, вторую нет."""
    graph = tmp_path / "graph"
    arch = graph / ARCHIVE_DIR
    (arch / "2026-08-21 — Наша").mkdir(parents=True)
    (arch / "2026-08-21 — Наша" / "meeting.meta.json").write_text(json.dumps({"meeting_id": MIN}), encoding="utf-8")
    (arch / "2026-08-21 — Другая").mkdir()
    assert [d.name for d in forget._archive_folders(graph, MIN)] == ["2026-08-21 — Наша"]
