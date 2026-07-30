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


def _joined(cmd: list[str]) -> str:
    return " ".join(cmd)


def test_without_the_edit_toggle_there_is_no_write_permission():
    """Главный инвариант: согласие на разбор не даёт права на запись."""
    text = _joined(_command(READ_ONLY))
    for tool in WRITE_TOOLS:
        assert tool not in text, f"read-only команда содержит {tool}: {text}"


def test_read_only_command_still_lets_the_model_read():
    """Отобрать запись — не значит сломать разбор: чтение остаётся."""
    text = _joined(_command(READ_ONLY))
    for tool in ("Read", "Grep", "Glob"):
        assert tool in text, f"в read-only режиме нет {tool}"


def test_edit_mode_works_when_both_toggles_are_explicit():
    text = _joined(_command(FULL))
    assert "Edit" in text and "Write" in text, text
    assert "acceptEdits" in text, "разрешённый режим потерял автоприём правок"


def test_the_right_comes_from_privacy_not_from_a_neighbouring_key():
    """Мусор в значении — не разрешение, как и везде в privacy.py."""
    for value in ("true", 1, "yes", [], None, "false"):
        cfg = {"sufler": {"cloud_enrich": True, "cloud_edit_graph": value}}
        text = _joined(_command(cfg))
        assert "Write" not in text, f"значение {value!r} выдало право записи"


def test_kill_switch_takes_the_write_permission_away():
    """«Этот запуск строго офлайн» — сильнее любого «да» в конфиге."""
    for switch in ("CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD"):
        text = _joined(_command(FULL, env={switch: "1"}))
        assert "Write" not in text and "acceptEdits" not in text, switch


def test_dangerous_tools_stay_forbidden_in_both_modes():
    for cfg in (READ_ONLY, FULL):
        text = _joined(_command(cfg))
        for tool in ("Bash", "WebFetch", "WebSearch", "Task"):
            assert tool in text, f"{tool} должен оставаться в запрете: {text}"


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
