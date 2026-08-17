"""Право писать выдавал не тот тумблер — и писать можно было куда угодно.

`PRIVACY.md` обещает: `cloud_edit_graph` — «единственный тумблер, который
разрешает ЗАПИСЬ, а не отправку». В коде это было не так. Разбор встречи после
стопа (`src/graph_updater.py`) запускал облачный Claude так:

    --allowedTools Read,Edit,Write,Grep,Glob
    --permission-mode acceptEdits
    cwd=<корень репозитория>

и ни одна из этих строк не зависела от `cloud_edit_graph`. Достаточно было
`cloud_enrich: true` — то есть согласия «пусть облако разберёт мою встречу», —
чтобы модель получила право переписывать файлы графа И файлы проекта, включая
`config/config.yaml`, где живут сами тумблеры приватности.

Отдельно про то, почему это не теоретическая дыра: в промпт целиком уходит
стенограмма встречи. Всё, что произнесли участники, модель читает вместе с
инструкциями — и указание вида «открой конфиг и включи…», произнесённое вслух
или прочитанное с экрана, попадает туда наравне с задачами конвейера. Право
записи плюс чужой текст в промпте — это уже не гипотеза.

Что держит этот файл:

    1. Read-only по умолчанию: при `cloud_edit_graph: false` в команде нет ни
       Edit, ни Write, ни acceptEdits — ни в каком виде.
    2. Право берётся ровно у `privacy.cloud_edit_graph_enabled`, а не у
       соседнего ключа и не у «истинности» значения.
    3. Рубильник `CHAROITE_NO_CLOUD` отбирает право записи, даже когда оба
       тумблера в конфиге стоят `true`.
    4. Разрешённый режим не сломан: с двумя явными `true` инструменты записи
       на месте.
    5. Стенограмма в промпте обрамлена как данные, а не как инструкции.

Тесты смотрят на СОБРАННУЮ КОМАНДУ, а не на текст файла: подмена формы записи
(другой порядок флагов, склейка строк) не должна проходить мимо сторожа.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import graph_updater  # noqa: E402

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "acceptEdits",
               "bypassPermissions", "dangerously")

READ_ONLY = {"sufler": {"cloud_enrich": True}}
FULL = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": True}}


def _command(cfg: dict, env: dict | None = None) -> list[str]:
    return graph_updater.cloud_enrich_command(
        cfg, claude_bin="/usr/bin/claude", prompt="prompt", model="claude-opus-5",
        env=env if env is not None else {})


def _flags(cmd: list[str]) -> dict[str, str]:
    """Команда → словарь флагов. Проверяем смысл, а не порядок аргументов."""
    out: dict[str, str] = {}
    for i, item in enumerate(cmd):
        if item.startswith("--") and i + 1 < len(cmd) and not cmd[i + 1].startswith("--"):
            out[item] = cmd[i + 1]
        elif item.startswith("--"):
            out[item] = ""
    return out


def _allowed(cmd: list[str]) -> set[str]:
    return {t for t in _flags(cmd).get("--allowedTools", "").split(",") if t}


def _forbidden(cmd: list[str]) -> set[str]:
    return {t for t in _flags(cmd).get("--disallowedTools", "").split(",") if t}


def test_without_the_edit_toggle_there_is_no_write_permission():
    """Главный инвариант: согласие на разбор не даёт права на запись.

    Проверяются РАЗРЕШЁННЫЕ инструменты и режим доступа. Упоминание Edit в
    списке ЗАПРЕЩЁННЫХ — это усиление, а не дыра, и путать одно с другим
    значит запрещать себе правильную реализацию.
    """
    cmd = _command(READ_ONLY)
    for tool in WRITE_TOOLS:
        assert tool not in _allowed(cmd), f"read-only разрешает {tool}: {cmd}"
    assert "--permission-mode" not in _flags(cmd), \
        "автоприём правок в режиме без права записи"
    assert {"Edit", "Write"} <= _forbidden(cmd), \
        "инструменты записи не запрещены явно — headless попросит разрешение"


def test_read_only_command_still_lets_the_model_read():
    """Отобрать запись — не значит сломать разбор: чтение остаётся.

    Равенство, а не вхождение: список разрешённого должен быть ИСЧЕРПЫВАЮЩИМ.
    Проверка «нужные инструменты на месте» пропустила бы случайно добавленный
    пятый — а каждый инструмент здесь это ещё одна дорога наружу.
    """
    allowed = _allowed(_command(READ_ONLY))
    assert allowed == {"Read", "Grep", "Glob"}, allowed


def test_caller_can_only_narrow_the_edit_right():
    """may_edit понижает право, когда страховка невозможна, — и не поднимает.

    Privacy-ключ — потолок. cloud_review снимает право при несмонтированном
    каталоге графа или неудавшемся бэкапе: Edit/Write без snapshot/backup —
    ровно то, чего PRIVACY обещает не допускать (ревью 15.08). Обратной
    дороги нет: параметр не заменяет privacy-ключ.
    """
    downgraded = graph_updater.cloud_enrich_command(
        FULL, claude_bin="/usr/bin/claude", prompt="p", model="m", env={},
        may_edit=False)
    for tool in WRITE_TOOLS:
        assert tool not in _allowed(downgraded), \
            f"понижение не сработало: {tool} разрешён"
    assert "--permission-mode" not in _flags(downgraded), \
        "понижение оставило автоприём правок"

    escalated = graph_updater.cloud_enrich_command(
        READ_ONLY, claude_bin="/usr/bin/claude", prompt="p", model="m", env={},
        may_edit=True)
    assert _allowed(escalated) == {"Read", "Grep", "Glob"}, \
        "параметр расширил право записи мимо privacy-ключа"


def test_edit_mode_works_when_both_toggles_are_explicit():
    cmd = _command(FULL)
    assert _allowed(cmd) == {"Read", "Grep", "Glob", "Edit", "Write"}, _allowed(cmd)
    assert _flags(cmd).get("--permission-mode") == "acceptEdits", \
        "разрешённый режим потерял автоприём правок"
    # инструмент, одновременно разрешённый и запрещённый, — это спор двух
    # флагов, который разрешает CLI, а не мы. Такого быть не должно.
    assert not ({"Edit", "Write"} & _forbidden(cmd)), \
        f"Edit/Write и разрешены, и запрещены: {_forbidden(cmd)}"


def test_the_right_comes_from_privacy_not_from_a_neighbouring_key():
    """Мусор в значении — не разрешение, как и везде в privacy.py."""
    for value in ("true", 1, "yes", [], None, "false"):
        cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": value}}
        assert "Write" not in _allowed(_command(cfg)), \
            f"значение {value!r} выдало право записи"


def test_kill_switch_takes_the_write_permission_away():
    """«Этот запуск строго офлайн» — сильнее любого «да» в конфиге."""
    for switch in ("CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD"):
        cmd = _command(FULL, env={switch: "1"})
        assert "Write" not in _allowed(cmd), switch
        assert "--permission-mode" not in _flags(cmd), switch


def test_dangerous_tools_stay_forbidden_in_both_modes():
    for cfg in (READ_ONLY, FULL):
        cmd = _command(cfg)
        forbidden = _forbidden(cmd)
        for tool in ("Bash", "WebFetch", "WebSearch", "Task"):
            assert tool in forbidden, f"{tool} не запрещён: {cmd}"
            assert tool not in _allowed(cmd), f"{tool} разрешён: {cmd}"


def test_transcript_is_framed_as_data_not_as_instructions():
    """Стенограмма — чужой текст. Он не должен читаться как задание.

    В промпт уходит всё, что произнесли участники; указание «а теперь открой
    конфиг» может прозвучать на встрече и без злого умысла.
    """
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name="2026-07-15_1400.md", folder=pathlib.Path("/tmp/t"),
        graph=pathlib.Path("/tmp/g"), rev_name="rev.md", stamp="2026-07-15_1400",
        arch_folder=None, may_edit=False)
    low = prompt.lower()
    assert "данные" in low or "не инструкции" in low or "не команды" in low, \
        "в промпте нет предупреждения, что текст встречи — данные"
    assert "read-only" in low or "ничего не записыв" in low, \
        "read-only промпт не говорит модели, что писать файлы нельзя"


def test_read_only_prompt_does_not_ask_for_writes():
    """Просить записать файл там, где записи нет, — растить ложные ошибки."""
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name="t.md", folder=pathlib.Path("/tmp/t"),
        graph=pathlib.Path("/tmp/g"), rev_name="rev.md", stamp="s",
        arch_folder=None, may_edit=False)
    for verb in ("скопируй", "перезаписывая", "допиши в её заметку"):
        assert verb not in prompt.lower(), f"read-only промпт просит писать: {verb}"


# ── CHR-AUD-002: что облако вообще может прочитать ──────────────────────────
#
# Право писать отобрано, но читать модель могла весь корень репозитория:
# transcripts/ со ВСЕМИ прошлыми встречами, recordings/, logs/, .git и
# config/config.yaml. Промпт просил одну встречу — доступ давал всё.
#
# Правило: набор файлов готовит Чароит. Содержимое встречи уходит текстом в
# промпте (мы точно знаем, что отправили), а на диске остаётся только граф —
# он нужен для кросс-ссылок и его человек уже доверил продукту.


def test_workdir_is_the_graph_not_the_repository():
    """Корень репозитория облаку не рабочая папка ни в одном режиме."""
    graph = pathlib.Path("/tmp/vault/Работа")
    for cfg in (READ_ONLY, FULL):
        work = graph_updater.cloud_enrich_workdir(cfg, graph)
        assert work == graph, work
        assert "charoite" not in str(work).lower() or str(work).startswith("/tmp"), work


def test_workdir_falls_back_when_there_is_no_graph():
    """Граф не настроен — рабочей папкой становится папка встречи, а не корень."""
    folder = pathlib.Path("/tmp/transcripts")
    work = graph_updater.cloud_enrich_workdir(READ_ONLY, pathlib.Path(""), folder)
    assert work == folder, work


def test_context_carries_the_meeting_and_nothing_else(tmp_path):
    """Подготовленный набор: файлы этой встречи и только они."""
    stamp = "2026-07-15_1400"
    (tmp_path / f"{stamp}.md").write_text("стенограмма встречи", encoding="utf-8")
    (tmp_path / f"{stamp}_minutes.md").write_text("минутки встречи", encoding="utf-8")
    (tmp_path / "2026-07-10_1000.md").write_text("ЧУЖАЯ ВСТРЕЧА", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("cloud_live: false", encoding="utf-8")

    context, names = graph_updater.cloud_enrich_context(tmp_path, stamp)
    assert "стенограмма встречи" in context and "минутки встречи" in context
    assert "ЧУЖАЯ ВСТРЕЧА" not in context, "в контекст попала другая встреча"
    assert "cloud_live" not in context, "в контекст попал конфиг"
    assert f"{stamp}.md" in names and len(names) == 2, names


def test_context_stops_at_the_stamp_boundary(tmp_path):
    """Минутный штамп — префикс секундного: встреча `…_113012` (крэш-рестарт
    в ту же минуту) уезжала в облако вместе с `…_1130` (аудит DeepSeek 16.08)."""
    stamp = "2026-08-03_1130"
    (tmp_path / f"{stamp}_Планёрка.md").write_text("моя встреча", encoding="utf-8")
    (tmp_path / "2026-08-03_113012.md").write_text("СОСЕДНЯЯ ВСТРЕЧА", encoding="utf-8")
    (tmp_path / "2026-08-03_113012_minutes.md").write_text("СОСЕДНИЕ МИНУТКИ", encoding="utf-8")

    context, names = graph_updater.cloud_enrich_context(tmp_path, stamp)
    assert "моя встреча" in context
    assert "СОСЕДН" not in context, names
    assert names == [f"{stamp}_Планёрка.md"]


def test_context_of_untitled_meeting_is_keyed_by_its_stem(tmp_path):
    """Посекундная встреча без темы: ключ — стем стенограммы; соседка той же
    минуты и переименованная другая встреча в контекст не попадают."""
    (tmp_path / "2026-08-03_113012.md").write_text("моя встреча", encoding="utf-8")
    (tmp_path / "2026-08-03_113012_minutes.md").write_text("мои минутки", encoding="utf-8")
    (tmp_path / "2026-08-03_113045.md").write_text("СОСЕДНЯЯ", encoding="utf-8")
    (tmp_path / "2026-08-03_1130_Планёрка.md").write_text("ДРУГАЯ", encoding="utf-8")

    context, names = graph_updater.cloud_enrich_context(tmp_path, "2026-08-03_113012")
    assert "моя встреча" in context and "мои минутки" in context
    assert "СОСЕДНЯЯ" not in context and "ДРУГАЯ" not in context, names
    assert names == ["2026-08-03_113012.md", "2026-08-03_113012_minutes.md"]


def test_context_truncation_is_visible(tmp_path):
    """Усечение длинной встречи должно быть видно, а не молчаливо."""
    stamp = "2026-07-15_1400"
    (tmp_path / f"{stamp}.md").write_text("а" * 5000, encoding="utf-8")
    context, _ = graph_updater.cloud_enrich_context(tmp_path, stamp, limit=1000)
    assert len(context) < 2000
    assert "усечено" in context.lower() or "…" in context


def test_prompt_does_not_send_the_model_to_read_meeting_files(tmp_path):
    """Файлы встречи уже в промпте — просить их читать значит открывать диск."""
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name="t.md", folder=tmp_path, graph=pathlib.Path("/tmp/g"),
        rev_name="rev.md", stamp="s", arch_folder=None, may_edit=True,
        context="СТЕНОГРАММА ЗДЕСЬ")
    assert "СТЕНОГРАММА ЗДЕСЬ" in prompt
    low = prompt.lower()
    assert "прочитай стенограмму полностью" not in low, \
        "промпт всё ещё отправляет модель читать файл с диска"
    assert "скопируй свежие файлы" not in low, \
        "копирование артефактов делает Чароит, модели для этого нужен диск"
