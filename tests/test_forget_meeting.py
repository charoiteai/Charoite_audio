"""Встречу нужно уметь забыть — иначе обещание приватности половинчато.

`record_keep_days` удаляет только аудио. Всё остальное живёт вечно и в шести
разных местах: стенограмма и её производные в `transcripts/`, папка встречи в
`Встречи-архив/` (Саммари, Минутки, Разбор, Вопросы-ответы, Стенограмма), узел
встречи в `Встречи/`, копия стенограммы в `Документация/Стенограммы встреч/`,
строки хроники в Ядрах и ссылки в Досье и узлах людей.

Человек, которому нужно убрать одну встречу (чужой разговор, NDA, ошибка
записи), должен вспомнить все шесть и не забыть ни одного, а Ядро после этого
остаётся со ссылкой в пустоту и с фактами из удалённой встречи в разделе
хроники. Это не то, что можно оставлять на аккуратность руками.

Требования, которые держит этот файл:

    1. План видит ВСЕ места, где живёт встреча, и не задевает соседние.
    2. Без явного согласия не удаляется ничего: необратимая операция
       по умолчанию только показывает, что собирается сделать.
    3. Строка хроники, которая ссылается на забытую встречу, уходит целиком —
       вместе с фактом, который из неё пришёл. Ссылка в связном тексте
       заменяется пометкой, а не оставляет `[[Встречи/…]]` в пустоту.
    4. Узлы, которые остаются жить, перед правкой бэкапятся: удаляем встречу,
       а не чужие заметки.
    5. Повторный запуск — не авария: забывать больше нечего.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import forget_meeting as forget  # noqa: E402

STAMP = "2026-07-15_1400"
OTHER = "2026-07-16_1000"


def _world(tmp: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Мир как после двух встреч: репозиторий и граф."""
    root, graph = tmp / "repo", tmp / "vault" / "Работа"
    (root / "transcripts").mkdir(parents=True)
    (root / "recordings").mkdir(parents=True)
    for stamp in (STAMP, OTHER):
        (root / "transcripts" / f"{stamp}.md").write_text(
            f"# Встреча {stamp}\n[14:00] Я: начнём.\n", encoding="utf-8")
        (root / "transcripts" / f"{stamp}_minutes.md").write_text("# Минутки\n", encoding="utf-8")
        (root / "recordings" / f"{stamp}_mic.pcm").write_bytes(b"\x00\x01")
        (graph / "Встречи").mkdir(parents=True, exist_ok=True)
        (graph / "Встречи" / f"{stamp}.md").write_text(
            f"---\ntype: встреча\n---\n# {stamp}\n[[Люди/Мария Соколова]]\n", encoding="utf-8")
        (graph / "Документация" / "Стенограммы встреч").mkdir(parents=True, exist_ok=True)
        (graph / "Документация" / "Стенограммы встреч" / f"{stamp}.md").write_text(
            "копия стенограммы\n", encoding="utf-8")

    arch = graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    arch.mkdir(parents=True)
    for name in ("Саммари.md", "Минутки.md", "Стенограмма.md", "Граф.md"):
        (arch / name).write_text(f"{name} встречи\n[[Встречи/{STAMP}]]\n", encoding="utf-8")
    other_arch = graph / "Встречи-архив" / "2026-07-16 10-00 — Каталог"
    other_arch.mkdir()
    (other_arch / "Саммари.md").write_text(f"[[Встречи/{OTHER}]]\n", encoding="utf-8")

    (graph / "Ядра").mkdir()
    (graph / "Ядра" / "Выбор платёжного провайдера.md").write_text(
        "---\ntype: ядро\n---\n# Выбор платёжного провайдера\n\n"
        "## Статус\nРешено — ЮPay _(обновлено 2026-07-17)_\n\n"
        "## Хроника\n"
        f"- [[Встречи/{OTHER}]] — сверили сроки\n"
        f"- [[Встречи/{STAMP}]] — выбрали ЮPay: 2.8% против 2.1%\n",
        encoding="utf-8")

    (graph / "Досье").mkdir()
    (graph / "Досье" / "Платёжный провайдер.md").write_text(
        "## Сейчас\nВыбран ЮPay, решение принято на встрече "
        f"[[Встречи/{STAMP}|15 июля]].\n", encoding="utf-8")

    (graph / "Люди").mkdir()
    (graph / "Люди" / "Мария Соколова.md").write_text(
        f"# Мария Соколова\n- участвовала: [[Встречи/{STAMP}]], [[Встречи/{OTHER}]]\n",
        encoding="utf-8")
    return root, graph


def test_plan_finds_every_place_the_meeting_lives(tmp_path):
    root, graph = _world(tmp_path)
    plan = forget.plan(STAMP, root, graph)
    doomed = {str(p) for p in plan.delete}

    must_go = (
        root / "transcripts" / f"{STAMP}.md",
        root / "transcripts" / f"{STAMP}_minutes.md",
        root / "recordings" / f"{STAMP}_mic.pcm",
        graph / "Встречи" / f"{STAMP}.md",
        graph / "Документация" / "Стенограммы встреч" / f"{STAMP}.md",
        graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер",
    )
    for path in must_go:
        assert str(path) in doomed, f"план не видит {path}"

    edited = {str(p) for p in plan.edit}
    assert str(graph / "Ядра" / "Выбор платёжного провайдера.md") in edited
    assert str(graph / "Досье" / "Платёжный провайдер.md") in edited
    assert str(graph / "Люди" / "Мария Соколова.md") in edited


def test_plan_does_not_touch_the_neighbouring_meeting(tmp_path):
    """Соседняя встреча того же графа остаётся нетронутой — вся."""
    root, graph = _world(tmp_path)
    plan = forget.plan(STAMP, root, graph)
    for path in (str(p) for p in list(plan.delete) + list(plan.edit)):
        assert OTHER not in path, f"задета соседняя встреча: {path}"


def test_nothing_disappears_without_explicit_consent(tmp_path):
    """По умолчанию — только показать. Необратимое не делается «за компанию»."""
    root, graph = _world(tmp_path)
    plan = forget.plan(STAMP, root, graph)
    forget.apply(plan, yes=False)
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert (graph / "Встречи" / f"{STAMP}.md").exists()


def test_forget_removes_the_meeting_and_its_traces(tmp_path):
    root, graph = _world(tmp_path)
    forget.apply(forget.plan(STAMP, root, graph), yes=True)

    assert not (root / "transcripts" / f"{STAMP}.md").exists()
    assert not (root / "transcripts" / f"{STAMP}_minutes.md").exists()
    assert not (root / "recordings" / f"{STAMP}_mic.pcm").exists()
    assert not (graph / "Встречи" / f"{STAMP}.md").exists()
    assert not (graph / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер").exists()

    # соседняя встреча цела
    assert (root / "transcripts" / f"{OTHER}.md").exists()
    assert (graph / "Встречи-архив" / "2026-07-16 10-00 — Каталог").exists()


def test_chronicle_line_leaves_with_the_meeting(tmp_path):
    """Строка хроники — это и есть след встречи: уходит вместе с фактом."""
    root, graph = _world(tmp_path)
    forget.apply(forget.plan(STAMP, root, graph), yes=True)
    core = (graph / "Ядра" / "Выбор платёжного провайдера.md").read_text(encoding="utf-8")
    assert "ЮPay: 2.8% против 2.1%" not in core, "факт из забытой встречи остался в Ядре"
    assert f"Встречи/{STAMP}" not in core, "ссылка в пустоту осталась"
    assert "сверили сроки" in core, "строка про ДРУГУЮ встречу пострадала"
    assert "## Статус" in core and "Решено — ЮPay" in core, "Ядро потеряло свои разделы"


def test_link_inside_a_sentence_becomes_a_note_not_a_dangling_link(tmp_path):
    root, graph = _world(tmp_path)
    forget.apply(forget.plan(STAMP, root, graph), yes=True)
    dossier = (graph / "Досье" / "Платёжный провайдер.md").read_text(encoding="utf-8")
    assert f"Встречи/{STAMP}" not in dossier
    assert "Выбран ЮPay" in dossier, "текст досье не должен рассыпаться"
    assert forget.REMOVED_NOTE in dossier, "человек должен понимать, почему тут пробел"


def test_surviving_nodes_are_backed_up_before_editing(tmp_path):
    """Удаляем встречу, а не чужие заметки: правка Ядра обратима."""
    root, graph = _world(tmp_path)
    before = (graph / "Ядра" / "Выбор платёжного провайдера.md").read_text(encoding="utf-8")
    forget.apply(forget.plan(STAMP, root, graph), yes=True)
    backups = list((graph / forget.BACKUP_DIR).rglob("Выбор платёжного провайдера.md"))
    assert backups, "нет бэкапа отредактированного Ядра"
    assert backups[0].read_text(encoding="utf-8") == before


def _cloud_snapshot(graph: pathlib.Path, stamp: str) -> pathlib.Path:
    """Снимок графа, как его делает cloud_review перед облачной правкой."""
    snap = graph / forget.CLOUD_BACKUP_DIR / "2026-07-20_0300"
    (snap / "Встречи").mkdir(parents=True)
    (snap / "Встречи" / f"{stamp}.md").write_text("# копия узла\n", encoding="utf-8")
    (snap / "Документация" / "Стенограммы встреч").mkdir(parents=True)
    (snap / "Документация" / "Стенограммы встреч" / f"{stamp}.md").write_text(
        "копия стенограммы\n", encoding="utf-8")
    arch = snap / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер"
    arch.mkdir(parents=True)
    (arch / "Минутки.md").write_text("минутки\n", encoding="utf-8")
    (snap / "Ядра").mkdir()
    (snap / "Ядра" / "Выбор платёжного провайдера.md").write_text(
        f"## Хроника\n- [[Встречи/{stamp}]] — выбрали ЮPay\n", encoding="utf-8")
    return snap


def test_cloud_backup_copies_are_forgotten_too(tmp_path):
    """Бэкап облачной ревизии копирует граф целиком — «забыть» обязано дойти.

    Иначе стенограмма и узел встречи остаются лежать в `.cloud_backup/` до
    десяти копий, и «встречу можно забыть целиком» после первой же облачной
    правки графа — неправда.
    """
    root, graph = _world(tmp_path)
    snap = _cloud_snapshot(graph, STAMP)
    forget.apply(forget.plan(STAMP, root, graph), yes=True)

    assert not (snap / "Встречи" / f"{STAMP}.md").exists(), \
        "узел встречи остался в снимке облачной ревизии"
    assert not (snap / "Документация" / "Стенограммы встреч" / f"{STAMP}.md").exists(), \
        "копия стенограммы осталась в снимке облачной ревизии"
    assert not (snap / "Встречи-архив" / "2026-07-15 14-00 — Платёжный провайдер").exists(), \
        "архив встречи остался в снимке облачной ревизии"
    core = (snap / "Ядра" / "Выбор платёжного провайдера.md").read_text(encoding="utf-8")
    assert f"Встречи/{STAMP}" not in core, "строка хроники осталась в копии Ядра"
    # бэкап бэкапа не снимается: в .forget_backup — только копия ЖИВОГО Ядра
    # (полного, со «## Статус»), а не отдельная копия файла из .cloud_backup
    buried = list((graph / forget.BACKUP_DIR).rglob("Выбор платёжного провайдера.md"))
    assert len(buried) == 1, f"ожидался один бэкап Ядра, найдено {len(buried)}"
    assert "## Статус" in buried[0].read_text(encoding="utf-8"), \
        "в .forget_backup попала копия из .cloud_backup вместо живого узла"


def test_running_twice_is_not_a_failure(tmp_path):
    root, graph = _world(tmp_path)
    forget.apply(forget.plan(STAMP, root, graph), yes=True)
    second = forget.plan(STAMP, root, graph)
    assert not second.delete and not second.edit, "второй проход нашёл, что забывать"
    forget.apply(second, yes=True)   # и не падает


def test_a_date_resolves_to_the_meetings_of_that_day(tmp_path):
    root, graph = _world(tmp_path)
    assert forget.resolve("2026-07-15", root, graph) == [STAMP]
    assert forget.resolve(STAMP, root, graph) == [STAMP]
    assert forget.resolve("2026-01-01", root, graph) == []


def test_meeting_known_only_to_the_graph_is_still_found(tmp_path):
    """Стенограмму могли уже удалить руками — встреча всё равно забывается."""
    root, graph = _world(tmp_path)
    for p in (root / "transcripts").glob(f"{STAMP}*"):
        p.unlink()
    assert forget.resolve("2026-07-15", root, graph) == [STAMP]
    plan = forget.plan(STAMP, root, graph)
    assert plan.delete, "план пуст, хотя узел встречи и архив на месте"
