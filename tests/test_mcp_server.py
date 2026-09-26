"""MCP-сервер должен подниматься на той версии mcp, которую ставит pip.

pyproject разрешает `mcp>=1.0`, а в 2.0 класс переехал: `FastMCP` из
`mcp.server.fastmcp` стал `MCPServer` в `mcp.server`. У нового пользователя
установка проходила успешно, а сервер падал на импорте — то есть проверять
надо не «объявлена ли зависимость», а «поднимается ли сервер здесь и сейчас».

Импорт намеренно прямой, без importorskip: пропущенный тест выглядит как
зелёный и молчит ровно в том случае, ради которого написан.
"""

from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm  # noqa: E402
import charoite_paths  # noqa: E402
import mcp_server  # noqa: E402


def test_server_is_up():
    assert mcp_server.mcp is not None


def test_server_keeps_the_api_the_file_relies_on():
    server = mcp_server.mcp
    # Эти два метода одинаковы в обеих ветках API — на них и держится файл.
    assert callable(getattr(server, "tool", None))
    assert callable(getattr(server, "run", None))


# ── Аудит 13.09, зона 4 ──────────────────────────────────────────────────

import subprocess  # noqa: E402

import pytest  # noqa: E402


def _transcripts(tmp_path, monkeypatch, name="2026-09-13_1200.md", text="# Встреча\nтело\n"):
    tdir = tmp_path / "transcripts"          # производная корня, а не подменяемая константа
    tdir.mkdir()
    (tdir / name).write_text(text, encoding="utf-8")
    charoite_paths.use_data_root(tmp_path, replace=True)
    return tdir


def test_latest_skips_a_file_removed_between_glob_and_stat(tmp_path, monkeypatch):
    """Ретеншн демона убирает файл между glob и stat — FileNotFoundError валил
    любой MCP-инструмент (GLM M3)."""
    tdir = _transcripts(tmp_path, monkeypatch)
    (tdir / "2026-09-13_1300.md").write_text("x", encoding="utf-8")
    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if self.name == "2026-09-13_1300.md":
            raise FileNotFoundError(2, "gone", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", stat)
    assert mcp_server._latest().name == "2026-09-13_1200.md"


def test_live_transcript_keeps_dashes_inside_speech(tmp_path, monkeypatch):
    """Самодельный split("---") резал стенограмму на первом «---» в сказанном;
    граница заметок — transcript.notes_start (DS M4)."""
    head = mcp_server.transcript.NOTES_HEAD
    _transcripts(tmp_path, monkeypatch,
                 text="# Встреча\nсказали --- и продолжили\nещё реплика\n" + head + "заметка модели\n")
    out = mcp_server.sufler_live_transcript()
    assert "и продолжили" in out and "ещё реплика" in out
    assert "заметка модели" not in out
    tail = mcp_server.sufler_live_transcript(max_chars=0)     # 0 давал всю стенограмму (GLM M4)
    assert tail.startswith("[") and len(tail.split("\n", 1)[1]) == 1


def test_status_asks_the_shared_daemon_process_sign(tmp_path, monkeypatch):
    """Статус спрашивает процесс демона у live_gate — тот же ответ, что у
    сторожа миграции; таблица шаблона — в tests/test_live_gate.py."""
    _transcripts(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server.live_gate, "daemon_process", lambda: "4242 python src/daemon.py")
    assert mcp_server.sufler_status().startswith("Демон: работает")
    monkeypatch.setattr(mcp_server.live_gate, "daemon_process", lambda: "")
    assert mcp_server.sufler_status().startswith("Демон: остановлен")


def test_make_minutes_fits_a_long_transcript_like_the_daemon(tmp_path, monkeypatch):
    """Инструмент собирал промпт с полной стенограммой при num_ctx 8192: Ollama
    молча обрезала начало, и усечённые минутки ложились поверх полных (GLM I1)."""
    tdir = _transcripts(tmp_path, monkeypatch, text="# Встреча\n" + "реплика\n" * 100)
    seen = {}

    class Fake:
        lang = "ru"
        recording_block = llm.LLM.recording_block
        document_model = llm.LLM.document_model          # боевая, а не заглушка: подмена модели должна быть видна
        engine, model, mlx_model = "ollama", "проба", ""

        def fit(self, transcript):
            return "[сжато: сводки частей]"

        def complete(self, prompt, **kw):
            seen["prompt"] = prompt
            return "- **Кто** — что — срок"

    monkeypatch.setattr(mcp_server, "_client", lambda: Fake())
    out = mcp_server.sufler_make_minutes()
    assert "[сжато: сводки частей]" in seen["prompt"] and "реплика\nреплика" not in seen["prompt"]
    assert "Минутки сохранены" in out and (tdir / "2026-09-13_1200_minutes.md").exists()


def test_update_graph_timeout_is_a_message_not_a_crash(tmp_path, monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=mcp_server.GRAPH_UPDATE_TIMEOUT)

    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    out = mcp_server.sufler_update_graph()
    assert "20 мин" in out and "прерван" in out


def test_правка_конфига_видна_без_перезапуска_сервера(tmp_path, monkeypatch):
    """Владелец сменил модель в приложении — следующий вызов инструмента знает.

    Сервер живёт столько же, сколько сессия Claude Code: сутками. Кэш конфига с
    ключом по одному корню сделал бы правку видимой только после перезапуска —
    поэтому в ключе есть время правки файла. Проверяется именно это свойство:
    кэш без канала отзыва — тот же снимок, от которого избавлена вся №329
    (круг 6 по коду, регрессия моей же правки круга 5).
    """
    конфиг = tmp_path / "config" / "config.yaml"
    конфиг.parent.mkdir(parents=True)
    конфиг.write_text("llm:\n  model: первая\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "_cfg_кэш", None)

    assert mcp_server._llm_cfg()["model"] == "первая"
    старое = конфиг.stat().st_mtime
    конфиг.write_text("llm:\n  model: вторая\n", encoding="utf-8")
    os.utime(конфиг, (старое + 5, старое + 5))    # не полагаемся на разрешение часов ФС

    assert mcp_server._llm_cfg()["model"] == "вторая", (
        "конфиг перечитан не был — кэш стал снимком")


def test_конфиг_читается_один_раз_на_вызов_инструмента(tmp_path, monkeypatch):
    """Четыре чтения одного файла могли лечь по разные стороны правки."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text(
        "llm:\n  model: проба\nsufler:\n  role: р\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "_cfg_кэш", None)
    чтений = []
    настоящий = mcp_server.load_user_or_example
    monkeypatch.setattr(mcp_server, "load_user_or_example",
                        lambda к, **kw: (чтений.append(к), настоящий(к, **kw))[1])

    mcp_server._client()

    assert len(чтений) == 1, f"конфиг прочитан {len(чтений)} раза за одну сборку клиента"


def test_конфиг_без_модели_объясняет_отказ_а_не_падает(tmp_path, monkeypatch):
    """Владелец видит, какой файл чинить, а не «MCP error: 'model'».

    Сборка клиента стояла выше `try`, и три отказа конфига — битый YAML,
    пустой файл, конфиг без `llm.model` — летели наружу сырым исключением
    мимо всех веток «минутки НЕ тронуты» (круг 6 по коду №329, DS I2 = GLM I1).
    """
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-09-13_1200.md").write_text("речь", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("llm:\n  engine: ollama\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_root", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "_cfg_кэш", None)

    out = mcp_server.sufler_make_minutes()

    assert "минутки НЕ тронуты" in out and "config.yaml" in out and "model" in out
    assert not (tdir / "2026-09-13_1200_minutes.md").exists(), "пустышка легла на диск"


@pytest.mark.корень_называет_тест
def test_mcp_without_a_root_starts_and_exits_clean_on_eof(tmp_path):
    """Клиент MCP может запустить сервер без CHAROITE_ROOT. Старт от этого не
    зависит: корень спрашивается на вызове инструмента, и отказ приходит ошибкой
    инструмента (`isError`, реестр ниже), а не падением процесса — stderr
    упавшего сервера клиент владельцу не показывает (№336). Процесс с закрытым вводом:
    stdio-сервер на EOF выходит нулём и без трейсбека."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    прогон = subprocess.run([sys.executable, str(ROOT / "src" / "mcp_server.py")],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            env=env, cwd=tmp_path, timeout=60)
    assert прогон.returncode == 0, прогон.stderr[-400:]
    assert "Traceback" not in прогон.stderr, прогон.stderr[-400:]


# ── №336: без корня — ошибка инструмента с рецептом ─────────────────────

import asyncio  # noqa: E402


def _схема(инструмент) -> dict:
    """Схема параметров элемента `list_tools()`: 1.x — `inputSchema`, 2.x — `input_schema`."""
    схема = getattr(инструмент, "input_schema", None)
    return схема if схема is not None else инструмент.inputSchema


def _текст(ответ) -> str:
    """Текст ответа `call_tool`: 1.x — пара (список TextContent, словарь),
    2.x — `CallToolResult` с полем `content`."""
    содержимое = ответ.content if hasattr(ответ, "content") else ответ[0]
    return "".join(часть.text for часть in содержимое)


def _ошибка(ответ) -> bool:
    """Отказ инструмента от рабочего ответа: 1.x — `isError`, 2.x — `is_error`."""
    return bool(getattr(ответ, "isError", getattr(ответ, "is_error", None)))


async def _вызвать(имя: str):
    """Зов инструмента путём клиента MCP в том же процессе — как это видит владелец.

    Прямой `mcp_server.mcp.call_tool` для этого не годится: `isError` ставит
    обработчик сервера, а сам `call_tool` на 2.x исключение пробрасывает, и
    отказа в ответе не видно ни на одной ветке. 2.x принимает объект сервера
    прямо (`mcp.Client`), 1.x — сессию поверх низкоуровневого сервера, который
    FastMCP прячет в `_mcp_server` (в 2.x такого поля нет — по нему и развилка).
    """
    if not hasattr(mcp_server.mcp, "_mcp_server"):
        import mcp
        async with mcp.Client(mcp_server.mcp) as клиент:
            return await клиент.call_tool(имя, {})
    from mcp.shared.memory import create_connected_server_and_client_session
    async with create_connected_server_and_client_session(
            mcp_server.mcp._mcp_server) as сессия:
        return await сессия.call_tool(имя, {})


def _инструменты() -> list:
    return asyncio.run(mcp_server.mcp.list_tools())


def test_the_answer_helpers_read_the_installed_mcp():
    """Помощники выше — единственное, что в реестре зависит от версии `mcp`;
    на установленной они обязаны читать схему. Развилка `_вызвать` (есть ли
    `_mcp_server`) и развилка импорта продукта обязаны называть одну версию:
    два независимых детектора одной развилки иначе разошлись бы молча
    (Important DeepSeek по PR #639). Ответ читают тесты ниже."""
    схема = {и.name: _схема(и) for и in _инструменты()}["sufler_live_transcript"]
    assert isinstance(схема, dict) and "properties" in схема
    ветка_1x = mcp_server.FastMCP.__module__.startswith("mcp.server.fastmcp")
    assert hasattr(mcp_server.mcp, "_mcp_server") == ветка_1x, mcp_server.FastMCP.__module__


@pytest.mark.корень_называет_тест
def test_without_a_root_every_tool_answers_with_the_recipe(monkeypatch):
    """Реестр по поведению, путём клиента MCP — то, что видит владелец. Без
    корня ни один инструмент не работает по догадке (корню кода): каждый
    отвечает ОШИБКОЙ инструмента (`isError`) с причиной и рецептом, и отказ
    приходит раньше модели и ребёнка (№336). Текст не сравниваем целиком:
    обёртки версий mcp ставят перед ним свой префикс."""
    monkeypatch.delenv("CHAROITE_ROOT", raising=False)

    def не_звать(*a, **k):
        raise AssertionError("инструмент без корня дошёл до работы")

    monkeypatch.setattr(mcp_server, "_client", не_звать)
    monkeypatch.setattr(mcp_server.subprocess, "run", не_звать)
    имена = {и.name for и in _инструменты()}
    # клиент владельца помнит инструменты по именам — счёт их не сторожит (DS M2 круга 1 по PR №628)
    assert имена == {"sufler_status", "sufler_live_transcript", "sufler_notes",
                     "sufler_make_minutes", "sufler_hints", "sufler_update_graph"}, имена
    for имя in sorted(имена):
        ответ = asyncio.run(_вызвать(имя))
        текст = _текст(ответ)
        assert _ошибка(ответ), (имя, текст[:300])
        assert "корень данных не назван" in текст, (имя, текст[:300])
        assert _команда(текст) == mcp_server._registration()[0], (имя, текст[:300])


def test_with_a_root_a_tool_answers_without_error():
    """С названным корнем (его даёт обвязка) тот же путь отвечает рабочим
    ответом, а не ошибкой: отказ по `isError` нормальную работу не задевает —
    `sufler_live_transcript` в пустом корне говорит «Стенограмм нет.»."""
    ответ = asyncio.run(_вызвать("sufler_live_transcript"))
    assert not _ошибка(ответ), _текст(ответ)[:300]
    assert _текст(ответ) == "Стенограмм нет."


def _команда(рецепт: str) -> list[str]:
    """Команда регистрации из текста рецепта — так, как её разберёт оболочка."""
    import shlex
    строки = [s.strip() for s in рецепт.splitlines() if s.strip().startswith("claude ")]
    assert len(строки) == 1, рецепт
    return shlex.split(строки[0])


def test_the_recipe_command_and_json_block_say_the_same():
    """Обе половины рецепта — одна регистрация. Команду владелец вставляет в
    терминал: она разбирается оболочкой ровно в argv, где после `-e` стоит
    `CHAROITE_ROOT`, а после `--` — тот же интерпретатор и сервер, что в JSON.
    Путь с пробелом команду не ломает (DS C1/I1 круга 1 по PR №628)."""
    import json
    argv, блок = mcp_server._registration()
    сервер = блок["mcpServers"]["sufler"]
    assert argv[:4] == ["claude", "mcp", "add", "sufler"], argv
    assert argv[argv.index("--") + 1:] == [сервер["command"], *сервер["args"]], argv
    env = dict(argv[i + 1].split("=", 1) for i, часть in enumerate(argv) if часть == "-e")
    assert env == сервер["env"] == {"CHAROITE_ROOT": mcp_server.DATA_PLACEHOLDER}
    assert сервер["args"] == [str(ROOT / "src" / "mcp_server.py")]
    рецепт = mcp_server._recipe()
    assert _команда(рецепт) == argv
    # JSON владелец вклеивает руками: он разбирается, читается без \u-экранов и
    # лежит по ключу на строку (мутатор: ensure_ascii и indent)
    текст = json.dumps(блок, ensure_ascii=False, indent=2)
    assert текст in рецепт and json.loads(текст) == блок
    assert f'"CHAROITE_ROOT": "{mcp_server.DATA_PLACEHOLDER}"' in текст, текст
    assert '\n  "mcpServers": {\n    "sufler": {' in текст, текст


def test_the_recipe_command_survives_a_path_with_spaces(monkeypatch):
    """Интерпретатор в каталоге с пробелом — обычное дело на macOS; склейка
    строк отдавала бы оболочке лишние слова вместо одного пути."""
    monkeypatch.setattr(mcp_server.sys, "executable", "/opt/My Apps/py env/bin/python")
    argv = _команда(mcp_server._recipe())
    assert argv[argv.index("--") + 1] == "/opt/My Apps/py env/bin/python", argv


def test_the_tool_schema_keeps_its_parameters():
    """Регистратор не прячет сигнатуру: без `functools.wraps` у
    `sufler_live_transcript` вместо `max_chars` были бы `*args, **kwargs`."""
    схема = {и.name: _схема(и) for и in _инструменты()}["sufler_live_transcript"]
    assert "max_chars" in схема["properties"], схема


def _minutes_with_cached_digests(tmp_path, monkeypatch, answer):
    """Сводки речи этой встречи и чужой лежат в кэше; минутки идут с ответом
    `answer` (строка или исключение)."""
    import meeting_source

    text = "# Встреча\n" + "реплика\n" * 100
    tdir = _transcripts(tmp_path, monkeypatch, text=text)
    speech = meeting_source.of(tdir / "2026-09-13_1200.md", text).speech
    llm._fit_cache_put((llm._fit_speech_id(speech), "ollama"), "сводки этой встречи")
    llm._fit_cache_put((llm._fit_speech_id("чужая встреча"), "ollama"), "сводки чужой")

    class Fake:
        lang = "ru"
        recording_block = llm.LLM.recording_block
        document_model = llm.LLM.document_model
        engine, model, mlx_model = "ollama", "проба", ""

        def fit(self, transcript):
            return "[сжато]"

        def complete(self, prompt, **kw):
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(mcp_server, "_client", lambda: Fake())
    return mcp_server.sufler_make_minutes(), speech


def test_saved_minutes_drop_the_digests_of_that_meeting_at_once(tmp_path, monkeypatch):
    """Повтор после успеха не нужен: сводки этой встречи уходят из памяти
    сразу, не дожидаясь 30 минут; чужие остаются (PRIVACY, №265)."""
    out, speech = _minutes_with_cached_digests(tmp_path, monkeypatch, "- **Кто** — что — срок")
    assert "Минутки сохранены" in out
    assert [k[0] for k in llm._fit_cache] == [llm._fit_speech_id("чужая встреча")]


@pytest.mark.parametrize("answer", ["", llm.LLMHTTPError(503, "занято")])
def test_failed_minutes_keep_the_digests_for_the_retry(tmp_path, monkeypatch, answer):
    """Упала главная генерация — ради повтора кэш и заведён: сводки остаются."""
    out, speech = _minutes_with_cached_digests(tmp_path, monkeypatch, answer)
    assert "НЕ тронуты" in out
    assert (llm._fit_speech_id(speech), "ollama") in llm._fit_cache
