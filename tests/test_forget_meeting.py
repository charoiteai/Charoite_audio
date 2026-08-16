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


def test_forget_reaches_the_pipeline_status(tmp_path):
    """`logs/meeting-status/<стенограмма>.json` — путь к стенограмме с темой
    в имени, этап, текст ошибки; его же читает список «Недавние встречи».
    Забывание до него не доходило (второе мнение 16.08)."""
    root, graph = _world(tmp_path)
    status = root / "logs" / "meeting-status"
    status.mkdir(parents=True)
    mine = status / f"{STAMP}_Платёжный_провайдер.json"
    mine.write_text("{}", encoding="utf-8")
    theirs = status / f"{OTHER}_Каталог.json"
    theirs.write_text("{}", encoding="utf-8")

    plan = forget.plan(STAMP, root, graph)

    assert mine in plan.delete, "статус конвейера переживает забывание"
    assert theirs not in plan.delete


def test_forget_reaches_the_suffixed_copies_in_the_graph(tmp_path):
    """graph_updater кладёт в «Стенограммы встреч» все `{штамп}_*.md`
    (минутки, подсказки, живая нить), а не один `{штамп}.md`, — и то же
    самое лежит в каждом снимке .cloud_backup. Забывание удаляло только
    точное имя, то есть копии стенограммы жили дальше (второе мнение 16.08)."""
    root, graph = _world(tmp_path)
    docs = graph / "Документация" / "Стенограммы встреч"
    minutes = docs / f"{STAMP}_minutes.md"
    minutes.write_text("минутки\n", encoding="utf-8")
    other_minutes = docs / f"{OTHER}_minutes.md"
    other_minutes.write_text("минутки\n", encoding="utf-8")
    snap_docs = graph / ".cloud_backup" / "2026-07-16_0300" / "Документация" / "Стенограммы встреч"
    snap_docs.mkdir(parents=True)
    snap_copy = snap_docs / f"{STAMP}_hints.md"
    snap_copy.write_text("подсказки\n", encoding="utf-8")

    plan = forget.plan(STAMP, root, graph)

    assert minutes in plan.delete, "копия минуток в графе переживает забывание"
    assert snap_copy in plan.delete, "копия в снимке облачной ревизии переживает забывание"
    assert other_minutes not in plan.delete


def test_a_stamp_with_seconds_is_its_own_meeting(tmp_path):
    """Штампы с секундами (`_140030`) реальны. Срез в 15 знаков резал их до
    `_1400`, а глоб `{штамп}*` при забывании встречи 14:00 уносил и файлы
    встречи 14:00:30 (второе мнение 16.08)."""
    root, graph = _world(tmp_path)
    seconds = "2026-07-15_140030"
    twin = root / "transcripts" / f"{seconds}_Другая_тема.md"
    twin.write_text("# другая встреча\n", encoding="utf-8")
    (graph / "Встречи" / f"{seconds}.md").write_text("# узел\n", encoding="utf-8")

    known = forget.stamps(root, graph)
    assert seconds in known, "штамп с секундами обрезан"
    assert STAMP in known

    plan = forget.plan(STAMP, root, graph)
    assert twin not in plan.delete, "забывание 14:00 унесло встречу 14:00:30"
    assert (graph / "Встречи" / f"{seconds}.md") not in plan.delete

    twin_plan = forget.plan(seconds, root, graph)
    assert twin in twin_plan.delete
    assert (root / "transcripts" / f"{STAMP}.md") not in twin_plan.delete


def test_forget_reaches_the_retry_log(tmp_path):
    """`logs/retry_<штамп>.log` — stdout повторной пересборки с маппингом
    имён участников. Ни ретеншн, ни «забыть» его не видели (аудит DeepSeek 16.08)."""
    root, graph = _world(tmp_path)
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    mine = logs / f"retry_{STAMP}.log"
    mine.write_text("имена: Мария Соколова\n", encoding="utf-8")
    theirs = logs / f"retry_{OTHER}.log"
    theirs.write_text("имена: кто-то ещё\n", encoding="utf-8")

    plan = forget.plan(STAMP, root, graph)

    assert mine in plan.delete, "лог повторной пересборки переживает забывание"
    assert theirs not in plan.delete


def test_edited_nodes_keep_their_permissions(tmp_path):
    """Конвейер пишет граф под harden_umask (0600). Перезапись узла через
    write_text давала 0644 по umask вызывающего — поправленный узел
    становился читаемым для всех (аудит DeepSeek 16.08)."""
    import os
    import stat

    root, graph = _world(tmp_path)
    core = graph / "Ядра" / "Выбор платёжного провайдера.md"
    core.chmod(0o600)
    old_umask = os.umask(0o022)  # Finder-овский umask кнопки «Забыть»
    try:
        forget.apply(forget.plan(STAMP, root, graph), yes=True)
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(core.stat().st_mode) == 0o600


def test_forget_reaches_the_status_named_after_the_live_transcript(tmp_path):
    """Файл статуса назван по живой стенограмме с секундами, а штамп встречи
    после наката темы минутный — по имени с границей штампа его не найти;
    его выдаёт transcript_path внутри (найдено тестом rename 16.08)."""
    import json

    root, graph = _world(tmp_path)
    status = root / "logs" / "meeting-status"
    status.mkdir(parents=True)
    mine = status / f"{STAMP}30.json"          # 2026-07-15_140030 — живое имя
    mine.write_text(json.dumps({"transcript_path": str(
        root / "transcripts" / f"{STAMP}_Платёжный_провайдер.md")}), encoding="utf-8")
    theirs = status / f"{STAMP}45.json"        # соседняя встреча той же минуты
    theirs.write_text(json.dumps({"transcript_path": str(
        root / "transcripts" / f"{STAMP}45.md")}), encoding="utf-8")
    broken = status / "мусор.json"
    broken.write_text("не json", encoding="utf-8")

    plan = forget.plan(STAMP, root, graph)

    assert mine in plan.delete, "статус, названный по живой стенограмме, переживает забывание"
    assert theirs not in plan.delete
    assert broken not in plan.delete
