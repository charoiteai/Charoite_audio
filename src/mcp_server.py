"""MCP-сервер суфлёра: даёт Claude Code живой доступ к встрече.

Инструменты читают файлы transcripts/ (пишутся атомарно) и Ollama —
работают независимо от того, запущен ли демон из UI.
Запуск, пути — от корня репозитория:
  CHAROITE_ROOT=/путь/к/данным .venv/bin/python src/mcp_server.py
Команду регистрации в клиенте MCP и блок для его конфига даёт `_recipe()` —
тот же текст приходит владельцу в отказе инструмента, когда корень не назван.
"""
from __future__ import annotations

import functools
import json
import os
import pathlib
import shlex
import subprocess
import sys

import requests
import action_items
import transcript

import live_gate
import live_sidecar
import meeting_source
import meeting_stamp
from config_loader import fingerprint, load_user_or_example
from llm import LLM, LLMHTTPError, forget_fit

# pyproject разрешает mcp>=1.0, а в 2.0 класс переехал: FastMCP из
# mcp.server.fastmcp стал MCPServer в mcp.server. Оба дают .tool() и .run(),
# то есть весь файл ниже работает одинаково — расходится только имя импорта.
# Без этого «pip install -r» проходил, а сервер падал на первой же строке.
try:
    from mcp.server.fastmcp import FastMCP          # mcp 1.x
except ModuleNotFoundError:  # pragma: no cover — ветка зависит от версии пакета
    from mcp.server import MCPServer as FastMCP     # mcp 2.x

from charoite_paths import RootNotNamed, code_root, harden_umask, require_data_root


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень: половина процесса жила в названном корне,
    половина — в выведенном из положения файла, и расхождение было немым
    (замер 21.09, №329).

    Спрашиваем `require_data_root`, а не `resolve_root`: третий ответ
    `resolve_root` — корень КОДА, выведенный из положения файла, и клиент MCP,
    зарегистрировавший сервер без `CHAROITE_ROOT`, молча работал бы по
    догадке. Без названного корня — `RootNotNamed` этого вызова; в ответ
    инструмента его превращает `_tool` (№336).
    """
    return require_data_root(__file__)


CODE = code_root(__file__)


def _transcripts() -> pathlib.Path:
    """Каталог стенограмм — производная корня данных, считается на вызове (№329)."""
    return _root() / "transcripts"


_cfg_кэш: tuple[tuple, dict] | None = None


def _cfg() -> dict:
    """Конфиг владельца — по корню НА ВЫЗОВЕ, через общий загрузчик, с кэшем.

    Читать файл своими руками этот модуль не вправе: правило «нет своего —
    возьми пример из поставки» живёт в `config_loader.load_user_or_example`, и
    им пользуются семь других модулей. Своё чтение здесь молча подменяло
    отсутствующий конфиг пустым словарём, а пустой словарь — константой модели
    на 23 ГБ, от которой поставка сознательно ушла (в примере `qwen3.5:4b`,
    круг 5 по коду №329, DS C1/I4).

    Кэш отличается от снимка наличием канала отзыва — здесь это отпечаток от
    загрузчика (`config_loader.fingerprint`): какие файлы он охватывает, решает
    тот, кто их выбирает. Без кэша один `sufler_make_minutes` читал
    файл четыре раза, и чтения могли лечь по разные стороны правки конфига
    приложением (DS M1 = GLM Minor 5); с ключом по одному корню правка вообще
    не долетала бы до перезапуска сервера.

    `or {}` — политика вызывающего: загрузчик сознательно отдаёт `None` за
    пустой файл, а битый YAML — исключением, и обе вещи остаются видимыми.
    """
    global _cfg_кэш
    корень = _root()
    отпечаток = fingerprint(корень)
    if _cfg_кэш is None or _cfg_кэш[0] != отпечаток:
        _cfg_кэш = (отпечаток, load_user_or_example(корень) or {})
    return _cfg_кэш[1]


def _llm_cfg() -> dict:
    """Раздел `llm` конфига — на вызове, а не на импорте.

    Раньше конфиг снимался на импорте (`_CFG = _cfg()`), то есть по корню,
    который никто ещё не назвал: сервер регистрируется через `claude mcp add`
    без окружения, и на импорте канон отвечал положением файла. Гейт этого не
    видел — снимок шёл через СВОЙ хелпер (круг по решению №332, DS C3;
    четвёртая форма в `ROOT_SHAPES` теперь его ловит).

    Имя модели отсюда не подменяется: своего дефолта у модуля нет, его требует
    `LLM.__init__` (`l["model"]`), и ни один другой потребитель конфига такой
    константы не держит.
    """
    return _cfg().get("llm") or {}


def _client() -> LLM:
    """LLM-клиент по конфигу владельца или по примеру из поставки.

    `sufler.role` у движка обязателен, а в примере он может быть пуст —
    только этот ключ и достраивается. Модель приходит из конфига как есть:
    подстановка своего дефолта здесь однажды увела свежую установку на
    23-гигабайтную модель мимо примера (круг 5 по коду №329, DS I4).
    """
    base = dict(_cfg())
    base["llm"] = _llm_cfg()
    base["sufler"] = {"role": "", **(base.get("sufler") or {})}
    return LLM(base)

mcp = FastMCP("sufler")


#: Плейсхолдер корня данных в рецепте — тот же, что в шапке модуля и в отказе канона.
DATA_PLACEHOLDER = "/путь/к/данным"


def _registration() -> tuple[list[str], dict]:
    """Регистрация сервера одной записью: команда клиента и блок его конфига.

    Обе половины рецепта собираются из одних и тех же частей, а команда — через
    `shlex.join`, а не склейкой строк: путь к интерпретатору или к серверу с
    пробелом (`My Projects`) иначе ломал вставку в терминал, а команда, в которой
    выпал `-e`, выглядела бы целой (DS C1/I1 круга 1 по PR №628).

    Путь к коду сервер знает сам (`CODE` — корень кода от `__file__` через
    канон, интерпретатор — этого процесса), а путь к данным — нет: его знает
    только владелец, поэтому в рецепте плейсхолдер, а не догадка.
    """
    сервер = str(CODE / "src" / "mcp_server.py")
    env = {"CHAROITE_ROOT": DATA_PLACEHOLDER}
    argv = ["claude", "mcp", "add", "sufler",
            *(часть for ключ, значение in env.items() for часть in ("-e", f"{ключ}={значение}")),
            "--", sys.executable, сервер]
    блок = {"mcpServers": {"sufler": {"command": sys.executable, "args": [сервер], "env": env}}}
    return argv, блок


def _recipe() -> str:
    """Как зарегистрировать сервер с корнем данных — одно место для шапки и отказа."""
    argv, блок = _registration()
    return (
        f"Зарегистрируйте сервер заново, назвав корень данных (вместо {DATA_PLACEHOLDER}):\n"
        f"  {shlex.join(argv)}\n"
        "Другим клиентам MCP — блок в их конфиг:\n"
        + json.dumps(блок, ensure_ascii=False, indent=2))


def _tool(fn):
    """Регистратор инструментов: без корня данных — отказ с рецептом, а не работа.

    Отказ приходит ОТВЕТОМ инструмента, а не падением процесса: клиент MCP
    stderr упавшего сервера владельцу не показывает, и дверь
    `name_data_root_or_exit` (stderr и код выхода) осталась бы невидимой.
    Корень спрашивается на каждом вызове — снимка на импорте нет (№329), и
    отказ случается раньше тела: `sufler_update_graph` без корня не запускает
    ребёнка, которому иначе досталась бы догадка (№336).

    `functools.wraps` обязателен: схему параметров MCP строит по сигнатуре, и
    без него `max_chars` у `sufler_live_transcript` превратился бы в
    `*args, **kwargs`.
    """
    @functools.wraps(fn)
    def с_корнем(*args, **kwargs):
        try:
            _root()
        except RootNotNamed as отказ:
            return f"{fn.__name__} не выполнен: {отказ}\n\n{_recipe()}"
        return fn(*args, **kwargs)
    return mcp.tool()(с_корнем)


# Производные файлы, которые пишутся ПОЗЖЕ стенограммы и не должны считаться
# «последней». Список НЕ свой: формат хвостов живёт в meeting_stamp, и этот
# кортеж уже отставал от него (не знал _live/_debrief/_спикеры — «последней
# встречей» мог оказаться файл разбора; аудит 0.46.0).
_DERIVED = tuple(f"{s}.md" for s in meeting_stamp.AUX_SUFFIXES)


def _latest(pattern: str = "*.md") -> pathlib.Path | None:
    files = []
    for p in _transcripts().glob(pattern):
        if p.name.endswith(_DERIVED):
            continue
        try:
            files.append((p.stat().st_mtime, p))
        except OSError:
            continue        # ретеншн демона убрал файл между glob и stat (аудит 13.09, GLM M3)
    return max(files)[1] if files else None


GRAPH_UPDATE_TIMEOUT = 20 * 60   # худший ensure_alive при SLOW ≈ 10 мин до первого куска разбора (GLM M5)


@_tool
def sufler_status() -> str:
    """Статус суфлёра: идёт ли встреча, какой файл стенограммы, размер."""
    # только процесс python с этим скриптом: голый «src/daemon.py» совпадал с редактором,
    # в котором открыт файл, и статус врал «работает» (аудит 13.09, GLM M4); шаблон — в live_gate
    running = bool(live_gate.daemon_process())
    f = _latest()
    if not f:
        return f"Демон: {'работает' if running else 'остановлен'}. Стенограмм нет."
    return (
        f"Демон: {'работает — встреча идёт' if running else 'остановлен'}.\n"
        f"Последняя стенограмма: {f.name} ({f.stat().st_size} байт, "
        f"обновлена {f.stat().st_mtime:.0f})"
    )


@_tool
def sufler_live_transcript(max_chars: int = 6000) -> str:
    """Живая стенограмма текущей/последней встречи (хвост, реплики по спикерам)."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    max_chars = max(1, int(max_chars))       # 0 давал срез body[0:] — всю стенограмму (GLM M4)
    text = f.read_text(encoding="utf-8")
    # граница заметок — канон transcript.notes_start: самодельный split("---") резал
    # стенограмму на первом же «---» внутри сказанного (аудит 13.09, DS M4)
    body = text[:transcript.notes_start(text)]
    return f"[{f.name}]\n" + (body[-max_chars:] if len(body) > max_chars else body)


@_tool
def sufler_notes() -> str:
    """Ко-мышление встречи: 📌 контрольные точки, 💎 ценные факты, 💭 мысли модели."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    text = f.read_text(encoding="utf-8")
    if f"## {transcript.NOTES_TITLE}" not in text:
        return "Заметок ко-мышления пока нет."
    return text.split(f"## {transcript.NOTES_TITLE}", 1)[1].strip()


@_tool
def sufler_make_minutes() -> str:
    """Сгенерировать минутки последней встречи локальной моделью и сохранить файлом."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    transcript = f.read_text(encoding="utf-8")
    # источник — речь + оговорка о записи (№317): раньше этот путь отдавал модели
    # ВЕСЬ файл с хвостом «Ко-мышления», и след канала читался как сказанное
    source = meeting_source.of(f, transcript)
    mpath = f.with_name(f.stem + "_minutes.md")
    # Ни статус, ни непустоту раньше никто не проверял: удалённая или
    # переименованная модель давала 404, `.get("message", {})` превращал ошибку
    # в пустую строку, и она безусловно ложилась ПОВЕРХ готовых минуток — а
    # инструмент отвечал «Минутки сохранены». Дальше пустышку подхватывал
    # архив, и документ встречи пропадал до ручного повторного прогона.
    # Пустой ответ модели — это неудача, а не новые минутки.
    # сборка клиента — под теми же обработчиками, что и генерация: битый YAML
    # и конфиг без `llm.model` иначе уходили владельцу голым исключением MCP
    # («MCP error: 'model'»), мимо всех веток «минутки НЕ тронуты»
    # (круг 6 по коду №329, DS I2 = GLM I1)
    try:
        client = _client()
    except Exception as e:                    # noqa: BLE001 — причина важнее типа, она в тексте
        return (f"конфиг не прочитан ({type(e).__name__}: {e}) — минутки НЕ тронуты "
                f"({mpath.name}). Проверьте {_root()}/config/config.yaml: "
                f"нужен раздел llm с ключом model")
    try:
        # длинная встреча — через ту же свёртку частей, что у демона: иначе Ollama
        # молча обрезала промпт, и минутки без первого часа ложились поверх полных
        # (аудит 13.09, GLM I1)
        fitted = client.fit(source.speech)
        out = client.complete(
            f"Стенограмма:\n\n{fitted}\n\n"
            + client.recording_block(source.recording_note)   # блок снаружи речи, после свёртки
            + "Составь минутки: дата, участники, темы, решения, поручения списком «- **Кто** — что — срок» (только участникам; дела для отсутствующих — в открытые вопросы), открытые вопросы, риски. Только факты. ЖЁСТКИЙ ЛИМИТ: не длиннее 900 знаков, максимум 3 пункта в разделе, каждый — одна строка.",
            system="Ты секретарь встречи. Пишешь точные, сухие минутки по-русски, markdown. Оформляешь всё списками «- …» с жирным ключом.",
            # модель — назначенная конфигом, а не выбранная лестницей: без
            # этого минутки молча писались бы fallback-моделью, если основная
            # не скачана (круг 6 по коду №329, DS I1)
            model=client.document_model(),
            think=None,   # умолчание модели — как исторически у этого инструмента
            num_ctx=8192, num_predict=420,
            timeout=600)
    except LLMHTTPError as e:
        return (f"модель ответила {e.status} — минутки НЕ тронуты "
                f"({mpath.name}). Проверьте настройку модели: {e.detail[:200]}")
    except ValueError:
        return f"модель вернула не JSON — минутки НЕ тронуты ({mpath.name})"
    except requests.RequestException as e:
        return (f"модель недоступна ({type(e).__name__}) — минутки НЕ тронуты "
                f"({mpath.name})")
    if not out:
        return f"Модель вернула пустой ответ — минутки НЕ тронуты ({mpath.name})"
    # Поручения — в чекбоксы ДО записи: это был третий путь записи минуток
    # (после авто-черновика и ручного «Протокола»), и единственный без
    # normalize — задачи из таких минуток не попадали в окно «Задачи» (№141).
    out = action_items.normalize(out)
    _sufler = _cfg().get("sufler") or {}
    # владелец — одним написанием, потом пометка «не участник» — как в демоне:
    # иначе пересборка этим путём возвращала бы «**Марку**» поверх «**Марк**»
    # (Important GLM r1 по #536); порядок — контракт finalize_assignees
    user_name = str(_sufler.get("user_name") or "")
    out = action_items.finalize_assignees(
        out, action_items.participants_of(transcript, owner=user_name), user_name,
        lang=str(_sufler.get("language") or "ru"))
    out = meeting_source.with_note(out, source.recording_note)   # строка итога — механически
    # Через временное имя: обрыв посреди write_text оставлял бы усечённые
    # минутки ПОВЕРХ готовых — тот же класс, что у .wav в pcm_to_wav.
    tmp = mpath.with_name(mpath.name + f".tmp{os.getpid()}")
    try:
        tmp.write_text(out, encoding="utf-8")
        tmp.replace(mpath)
    finally:
        tmp.unlink(missing_ok=True)   # после replace его нет; страховка на обрыв
    # Минутки на диске — повтор уже не нужен, и сводки частей этой встречи из
    # памяти процесса уходят сразу, не дожидаясь 30 минут (№265, PRIVACY)
    forget_fit(source.speech)
    # Паспорт производной (№309): машинные минутки без него читались пересборкой
    # как UNKNOWN; с ним они STALE ровно тогда, когда речь или оговорка
    # изменились. Хеш источника — тем же объектом, что у пересборки.
    tail = "" if live_sidecar.attest(f, "minutes", out, source.sha()) else (
        "\n(паспорт производной не записан: сайдкар стенограммы неоднозначен)")
    return f"Минутки сохранены: {mpath}{tail}\n\n{out[:2000]}"


@_tool
def sufler_hints() -> str:
    """Сохранённые подсказки последней встречи (авто и ручные)."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    h = f.with_name(f.stem + "_hints.md")
    return h.read_text(encoding="utf-8")[-4000:] if h.exists() else "Подсказок пока нет."


@_tool
def sufler_update_graph() -> str:
    """Обновить Obsidian-граф по последней встрече (сущности, связи, решения)."""
    try:
        r = subprocess.run(
            [sys.executable, str(CODE / "src" / "graph_updater.py")],
            capture_output=True, text=True, timeout=GRAPH_UPDATE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # сырой краш инструмента вместо ответа; отцеплённый облачный воркер
        # graph_updater переживёт, локальный прогон остаётся без отчёта (GLM M5)
        return (f"разбор не уложился в {GRAPH_UPDATE_TIMEOUT // 60} мин и прерван — повторите позже "
                "или запустите graph_updater.py вручную")
    return (r.stdout + r.stderr).strip() or "готово"


if __name__ == "__main__":
    # 0600 на всём, что пишет сервер: минутки несут содержимое встречи, а без
    # umask tmp+replace оставлял их 0644 — единственный писатель данных встреч
    # без этой строки (GLM I7 по #464; в daemon/rebuild/main она давно есть).
    # Здесь, а не на импорте: модуль импортируют тесты, umask — состояние
    # процесса, и сайд-эффект на import ронял чужие проверки прав.
    harden_umask()
    mcp.run()
