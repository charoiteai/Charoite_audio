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


def test_logs_of_a_seconds_stamped_meeting_are_found_by_the_minute(tmp_path):
    """Логи названы минутным штампом (`stem[:15]`), а штамп посекундной
    встречи без темы — с секундами: по нему логи не находились и
    переживали забывание (второе мнение DeepSeek по партии 16.08)."""
    root, graph = _world(tmp_path)
    sec = f"{STAMP}30"                       # 2026-07-15_140030
    (root / "transcripts" / f"{sec}.md").write_text("стенограмма", encoding="utf-8")
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    mine = [logs / f"graph_{STAMP}.log", logs / f"cloud_review_{STAMP}.log",
            logs / f"retry_{STAMP}.log"]
    for f in mine:
        f.write_text("имена: Мария Соколова\n", encoding="utf-8")
    theirs = logs / f"graph_{OTHER}.log"
    theirs.write_text("другая", encoding="utf-8")

    plan = forget.plan(sec, root, graph)

    for f in mine:
        assert f in plan.delete, f"{f.name} переживает забывание посекундной встречи"
    assert theirs not in plan.delete

def test_new_place_snapshot_copies_are_forgotten_too(tmp_path, monkeypatch):
    """Снимки уехали из графа в данные (21.08) — «забыть» обязано дойти и
    туда: и файлы встречи, и строки хроники в копиях Ядер. Круг-2 по
    PR #363 (GLM): фикс был закорочен на глобальный ROOT и не имел ни
    одного теста, способного упасть."""
    import charoite_paths
    root, graph = _world(tmp_path)
    monkeypatch.setattr(forget, "ROOT", root)
    snap = charoite_paths.graph_backups(
        graph, "cloud_backup", root=root) / "2026-07-16_0300"
    (snap / "Встречи").mkdir(parents=True)
    (snap / "Встречи" / f"{STAMP}.md").write_text("копия узла", encoding="utf-8")
    (snap / "Ядра").mkdir()
    (snap / "Ядра" / "Тема.md").write_text(
        f"# Тема\n\n## Хроника\n- [[Встречи/{STAMP}]] — решение\n",
        encoding="utf-8")

    forget.apply(forget.plan(STAMP, root, graph), yes=True)

    assert not (snap / "Встречи" / f"{STAMP}.md").exists(), \
        "узел встречи остался в снимке нового места"
    core = (snap / "Ядра" / "Тема.md").read_text(encoding="utf-8")
    assert f"Встречи/{STAMP}" not in core, \
        "строка хроники осталась в копии Ядра нового места"



def test_forget_reaches_the_brain_by_key_and_reports_when_it_is_down(tmp_path, monkeypatch):
    """Тема и решения встречи уходят в память Чароита (brain :8100) под ключом
    встречи; с 23.08 у brain есть /forget, и план забывает факты там по
    ключу графа (отметка brain_sent) и по штампу — вместо прежнего «вне
    досягаемости» (карточка №41). brain выключен — не авария, но и не
    молчание: человек получает строку с тем, как повторить."""
    root, graph = _world(tmp_path)
    sent_dir = root / "logs" / "brain_sent"
    sent_dir.mkdir(parents=True, exist_ok=True)
    mark = sent_dir / f"{STAMP}.txt"
    mark.write_text("тема\n", encoding="utf-8")

    plan = forget.plan(STAMP, root, graph)
    assert mark in plan.delete
    assert plan.brain_keys == [STAMP]
    assert not any("brain" in line for line in plan.beyond_reach)

    calls = []

    class Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"text": "Забыто: PG OK: 2; Chroma удалено 2"}

    class FakeRequests:
        @staticmethod
        def post(url, json=None, timeout=None):
            calls.append((url, json))
            return Resp()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests)
    assert "Забыто" in forget.brain_forget(STAMP)
    assert calls == [(f"{forget.BRAIN}/forget", {"meeting": STAMP})]

    class Down:
        @staticmethod
        def post(url, json=None, timeout=None):
            raise ConnectionError("refused")

    monkeypatch.setitem(sys.modules, "requests", Down)
    msg = forget.brain_forget(STAMP)
    assert "недоступна" in msg and "/forget" in msg and STAMP in msg

    # у посекундной соседки ключей два: ключ графа из отметки и её штамп
    (sent_dir / "2026-07-15_140012.txt").write_text("тема\n", encoding="utf-8")
    (root / "transcripts" / "2026-07-15_140012.md").write_text("# Встреча\n", encoding="utf-8")
    second = forget.plan("2026-07-15_140012", root, graph)
    assert second.brain_keys == ["2026-07-15_140012"]


def test_cloud_quarantine_of_the_meeting_is_forgotten_too(tmp_path, monkeypatch):
    """Карантин разбора встречи — версии облака, убранные сверкой, — тот же
    след встречи, что и снимок: забывание обязано дойти и туда (круг-1 по
    PR #381, Codex Critical). Каталог запуска этой встречи — целиком, в
    карантинах других встреч — файлы с её штампом."""
    import charoite_paths
    root, graph = _world(tmp_path)
    monkeypatch.setattr(forget, "ROOT", root)
    q = charoite_paths.graph_backups(graph, "cloud_quarantine", root=root)
    mine = q / f"{STAMP}-101500"
    (mine / "Ядра").mkdir(parents=True)
    (mine / "Ядра" / "Тема.md").write_text("версия облака", encoding="utf-8")
    other = q / "2026-07-20_1500-090000"
    (other / "Встречи").mkdir(parents=True)
    (other / "Встречи" / f"{STAMP}.md").write_text("копия узла", encoding="utf-8")
    (other / "Ядра").mkdir()
    (other / "Ядра" / "Другая.md").write_text("чужая версия", encoding="utf-8")

    forget.apply(forget.plan(STAMP, root, graph), yes=True)

    assert not mine.exists(), "карантин запуска этой встречи остался"
    assert not (other / "Встречи" / f"{STAMP}.md").exists()
    assert (other / "Ядра" / "Другая.md").exists(), "чужой карантин тронут"


def test_quarantine_is_matched_by_exact_stem_not_minute_prefix(tmp_path, monkeypatch):
    """Каталог запуска назван точным стемом стенограммы: посекундная встреча
    находит свой, а сестринская встреча той же минуты остаётся нетронутой —
    минутный префикс сносил оба (круг-3 по PR #381, Codex + DS)."""
    import charoite_paths
    root, graph = _world(tmp_path)
    monkeypatch.setattr(forget, "ROOT", root)
    q = charoite_paths.graph_backups(graph, "cloud_quarantine", root=root)
    mine = q / f"{STAMP}30-101500123456"
    sibling = q / f"{STAMP}45-103000000000"
    for d in (mine, sibling):
        (d / "Ядра").mkdir(parents=True)
        (d / "Ядра" / "Тема.md").write_text("версия облака", encoding="utf-8")
    forget.apply(forget.plan(STAMP + "30", root, graph), yes=True)
    assert not mine.exists() and sibling.exists()


def test_quarantine_of_a_retitled_meeting_is_found_by_its_minute_stamp():
    """После наката темы стем — `<штамп>_тема`, а забыть просят по штампу
    (круг-4 по PR #381, Codex). Граница — как у всех файлов встречи:
    после штампа не цифра."""
    q = forget._quarantine_of
    assert q("2026-07-15_1400-101500123456", "2026-07-15_1400")
    assert q("2026-07-15_1400_тема-101500123456", "2026-07-15_1400")
    assert q("2026-07-15_140030-101500123456", "2026-07-15_140030")
    assert not q("2026-07-15_140030-101500123456", "2026-07-15_1400")
    assert not q("2026-07-15_1400-101500123456", "2026-07-15_140030")
    assert not q("2026-07-15_1400", "2026-07-15_1400")        # без времени — не наш формат
    # дефис внутри темы — не суффикс времени; неполный штамп — не штамп (круг-5)
    assert q("2026-07-15_1400_тема-с-дефисом-101500123456", "2026-07-15_1400")
    assert not q("2026-07-15_1400_тема-с-дефисом", "2026-07-15_1400")
    assert not q("2026-07-15_14_тема-101500", "2026-07-15_14")


def test_apply_reports_what_it_could_not_delete(tmp_path, monkeypatch, capsys):
    """PermissionError на одном пути не обрывает цикл и не прячется за
    «забыто» (круг-3 по PR #381, Codex)."""
    import os
    root, graph = _world(tmp_path)
    monkeypatch.setattr(forget, "ROOT", root)
    locked = tmp_path / "закрыто"; locked.mkdir()
    victim = locked / "x.md"; victim.write_text("x", encoding="utf-8")
    os.chmod(locked, 0o500)
    try:
        p = forget.plan(STAMP, root, graph)
        p.delete.insert(0, victim)
        forget.apply(p, yes=True)
    finally:
        os.chmod(locked, 0o700)
    out = capsys.readouterr().out
    assert "НЕ удалено" in out and "x.md" in out
    assert not (graph / "Встречи" / f"{STAMP}.md").exists(), "цикл оборвался на первом пути"


def test_plan_forgets_the_copy_before_the_last_rebuild(tmp_path):
    """transcripts/.prev/<имя> — тот же текст встречи; скрытую папку глоб не
    обходит, и «забыть» оставляло его навсегда (GLM r1 по #456; PRIVACY.md)."""
    root, graph = _world(tmp_path)
    prev = root / "transcripts" / ".prev" / f"{STAMP}.md"
    prev.parent.mkdir()
    prev.write_text("текст до пересборки", encoding="utf-8")
    plan = forget.plan(STAMP, root, graph)
    assert str(prev) in {str(p) for p in plan.delete}


def test_plan_forgets_the_prev_copy_named_by_the_original_seconds_stamp(tmp_path):
    """Копия до пересборки названа голым посекундным именем, встречу забывают
    по минуте: минутный глоб с границей её не видит — ключ берётся из статуса
    и из имён удаляемых стенограмм (DS r2 по #456)."""
    import json
    root, graph = _world(tmp_path)
    prev = root / "transcripts" / ".prev" / f"{STAMP}01.md"
    prev.parent.mkdir()
    prev.write_text("до пересборки", encoding="utf-8")
    sd = root / "logs" / "meeting-status"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / f"{STAMP}01.json").write_text(json.dumps({
        "meeting_id": f"{STAMP}01", "key": f"{STAMP}01", "state": "ready",
        "transcript_path": str(root / "transcripts" / f"{STAMP}.md")}), encoding="utf-8")
    plan = forget.plan(STAMP, root, graph)
    assert str(prev) in {str(p) for p in plan.delete}


def test_status_with_a_dead_raw_path_after_retitle_is_still_forgotten(tmp_path):
    """Окно между retitle и следующей записью: статус и .prev названы посекундно,
    transcript_path мёртвый голый — forget по минуте обязан найти их по
    резолву пути (luna r3 по #456); не-словарь в статусе не роняет план."""
    import json
    root, graph = _world(tmp_path)
    (root / "transcripts" / f"{STAMP}.md").rename(root / "transcripts" / f"{STAMP}_Тема.md")
    sd = root / "logs" / "meeting-status"
    sd.mkdir(parents=True, exist_ok=True)
    st = sd / f"{STAMP}01.json"
    st.write_text(json.dumps({"meeting_id": f"{STAMP}01", "key": f"{STAMP}01", "state": "processing",
                              "transcript_path": str(root / "transcripts" / f"{STAMP}01.md")}), encoding="utf-8")
    (sd / f"{STAMP}-мусор.json").write_text("[]", encoding="utf-8")
    prev = root / "transcripts" / ".prev" / f"{STAMP}01.md"
    prev.parent.mkdir()
    prev.write_text("до пересборки", encoding="utf-8")
    doomed = {str(p) for p in forget.plan(STAMP, root, graph).delete}
    assert str(st) in doomed and str(prev) in doomed


def test_dead_neighbour_status_is_not_claimed_by_the_owner_of_the_minute(tmp_path):
    """Соседка удалена руками, её статус с мёртвым путём резолвится в файл
    владельца минуты — forget владельца не должен считать его своим (DS r4);
    а собственный статус с мёртвым путём после retitle — по-прежнему находится."""
    import json
    root, graph = _world(tmp_path)
    (root / "transcripts" / f"{STAMP}.md").rename(root / "transcripts" / f"{STAMP}_Тема.md")
    sd = root / "logs" / "meeting-status"
    sd.mkdir(parents=True, exist_ok=True)
    own = sd / f"{STAMP}.json"          # найден по имени, путь живой
    own.write_text(json.dumps({"meeting_id": STAMP, "key": STAMP, "state": "ready",
                               "transcript_path": str(root / "transcripts" / f"{STAMP}_Тема.md")}), encoding="utf-8")
    neighbour = sd / f"{STAMP}45.json"  # мёртвый путь соседки резолвится в файл владельца
    neighbour.write_text(json.dumps({"meeting_id": f"{STAMP}45", "key": f"{STAMP}45", "state": "error",
                                     "transcript_path": str(root / "transcripts" / f"{STAMP}45.md")}), encoding="utf-8")
    doomed = {str(p) for p in forget.plan(STAMP, root, graph).delete}
    assert str(own) in doomed and str(neighbour) not in doomed
