"""Какая облачная модель работает на каком шаге. Одно место вместо литералов.

Разрешение на облако выдаёт src/privacy.py, здесь — только выбор модели, когда
разрешение уже есть. Разделение не косметическое: у тумблера цена — приватность,
у модели — качество и время ответа, и путать их не стоит.

Дефолты были разбросаны по точкам выхода и разъехались: у одного ключа
`cloud_model` в графе стоял claude-opus-4-8, а в двух ночных скриптах —
claude-opus-5; `cloud_live_model` в примере конфига обещал Haiku, а код без
ключа брал Sonnet, то есть другую модель другого класса. Пользователь с
урезанным конфигом получал сочетание, которого нет ни в одном документе.

Правило: дефолт один, он совпадает с config.example*.yaml (tests/
test_cloud_model_defaults.py держит это), и точки выхода спрашивают его здесь.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time

# Ключ конфига → модель по умолчанию. Меняется ВМЕСТЕ с примерами конфига,
# иначе тест валится: пример — это документация, а не пожелание.
DEFAULTS = {
    # разбор встречи после стопа и ночные ревизии графа: самая сильная модель,
    # работает не в темпе разговора, поэтому время ответа терпимо
    "cloud_model": "claude-opus-5",
    # ответ в темпе разговора: важнее скорость, чем глубина
    "cloud_live_model": "claude-haiku-4-5",
    # уточнение подсказок: то же, но чаще — дешёвая и быстрая
    "cloud_hints_model": "claude-haiku-4-5",
}


def model(cfg: dict, key: str) -> str:
    """Модель для шага: из конфига, а если там пусто — задокументированный дефолт.

    Неизвестный ключ — ошибка вызывающего, а не повод молча вернуть None:
    опечатка в имени ключа иначе увела бы шаг на чужую модель.
    """
    if key not in DEFAULTS:
        raise KeyError(f"{key}: неизвестный ключ модели, известны {sorted(DEFAULTS)}")
    value = (cfg.get("sufler") or {}).get(key)
    return str(value).strip() if str(value or "").strip() else DEFAULTS[key]


# Инструменты, запрещённые вызову «только текст». Такой вызов получает весь
# материал В ПРОМПТЕ и обязан вернуть текст — ни чтения файлов, ни шелла, ни
# сети ему не положено: стенограмма встречи и тексты досье — это чужие слова,
# и инъекция из них не должна дотягиваться до инструментов («прочитай
# ~/.ssh/… и вставь в ответ»). Список запретительный и полный, потому что
# неразрешённый инструмент в headless — вечный пермишен-запрос, а он
# самоограничен таймаутом; --setting-sources "" при этом обязателен:
# без него на headless-вызов действуют пользовательские allowlist'ы из
# ~/.claude/settings.json, и «запрещено по умолчанию» превращается в
# «разрешено хозяином машины» (аудит 14.08).
TEXT_ONLY_DENIED = ("Bash", "Read", "Write", "Edit", "Grep", "Glob",
                    "WebFetch", "WebSearch", "Task", "NotebookEdit",
                    "AskUserQuestion", "TodoWrite",
                    # пути к файлам/командам в обход основной шестёрки:
                    # скиллы исполняют шелл, BashOutput читает фоновые
                    # процессы (ревью 15.08 — списка не хватало); MCP —
                    # любые внешние серверы из пользовательских настроек
                    "Skill", "SlashCommand", "BashOutput", "KillShell", "mcp__*")


def claude_bin() -> str:
    """Путь к Claude CLI: PATH, иначе задокументированный Homebrew-путь.

    GUI-запуск macOS не наследует Homebrew PATH — отсюда абсолютный
    fallback (формулировка из #407). Пять точек выхода держали свою копию `shutil.which("claude") or
    "/opt/homebrew/bin/claude"` — партия D-П4 карты оздоровления сводит
    их сюда. Fallback не проверяется на существование нарочно: точка
    выхода получает честный ENOENT от subprocess с понятным путём в
    ошибке, а не молчаливую замену поведения.
    """
    return shutil.which("claude") or "/opt/homebrew/bin/claude"


class CloudCLIUnavailable(RuntimeError):
    """Claude CLI не запускается: битый симлинк, обновление под ногами, нет бинарника."""


def probe_claude(path: str, timeout: float = 30) -> str | None:
    """`claude --version` одним вызовом: строка версии или None."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


# Пауза перед второй попыткой зонда: обновление CLI меняет бинарник на секунды
RETRY_PAUSE = 60.0


def claude_bin_checked(*, retries: int = 1, pause: float | None = None) -> str:
    """Путь к живому Claude CLI — или CloudCLIUnavailable.

    Ночь 05.09: обновление CLI подменило бинарник под ногами, `claude_bin()`
    отдал путь, exec упал с «Exec format error», и семь тем ревизии молча
    легли в отчёт как «сбой» при коде 0 (аудит GLM/DS по main 05.09). Зонд
    `--version` до основного цикла: не отвечает — пауза и вторая попытка
    (обновление короткое), потом честный отказ, который ночь обязана
    показать кодом возврата, а не строкой в служебном файле.
    """
    candidates: list[str] = []
    for c in (shutil.which("claude"), "/opt/homebrew/bin/claude"):
        if c and c not in candidates:
            candidates.append(c)
    last = "кандидатов нет"
    for attempt in range(retries + 1):
        for c in candidates:
            if probe_claude(c):
                return c
            last = c
        if attempt < retries:
            time.sleep(RETRY_PAUSE if pause is None else pause)
    raise CloudCLIUnavailable(f"Claude CLI не отвечает на --version: {last}")


def text_only_args() -> list[str]:
    """Флаги изоляции headless `claude -p`, которому положен только текст.

    Единая точка вместо копий списка по вызовам: у трёх контуров (ревизия
    нити, глубокий ответ, ночные ревизии) списки запретов уже расходились.
    Контракт стерегут tests/test_cloud_isolation.py.
    """
    return [  # --allowedTools не ограничивает видимый набор инструментов.
            # Пустой --tools убирает все built-in, mcp__* ниже — все MCP.
            "--tools", "",
            "--disallowedTools", *TEXT_ONLY_DENIED,
            # Всё, что не выдано явно, headless-вызов отклоняет, а не ждёт
            # невозможного интерактивного подтверждения.
            "--permission-mode", "dontAsk",
            # без пользовательских hooks/MCP: внешний хук на каждый промпт
            # не даёт headless-процессу завершиться (паттерн claude-mem)
            "--setting-sources", "", "--strict-mcp-config"]


# Только известные прокси-переменные: «proxy в имени» пропускал бы
# PROXY_PASSWORD и подобное в окружение headless-вызова (круг-1 по PR #379).
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def proxy_env(settings: pathlib.Path | None = None) -> dict:
    """Прокси из ~/.claude/settings.json (env-секция) для headless `claude -p`.

    Процесс из desktop-приложения стартует без shell-окружения, а
    `--setting-sources ""` отрезает env настроек — вызов шёл к
    api.anthropic.com напрямую и ловил «403 Request not allowed» (регион).
    Одна точка вместо трёх копий: у разбора встречи (cloud_review.py) своей
    копии не было, и 21.08 все шесть разборов дня упали с 403, пока демон и
    ночные скрипты с той же сети работали. Возвращает только PROXY_KEYS (в
    любом регистре); остальное из env-секции сюда не попадает.
    """
    path = settings or (pathlib.Path.home() / ".claude" / "settings.json")
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in s.get("env", {}).items() if k.upper() in PROXY_KEYS}
    except Exception:  # noqa: BLE001 — нет файла/битый JSON: просто без прокси
        return {}


def add_proxy(env: dict, settings: pathlib.Path | None = None) -> None:
    """Дописать в окружение headless-вызова прокси из настроек — только как запас.

    Мутирует на месте: AST-сторож tests/test_cloud_call_sites.py требует,
    чтобы каждое присваивание `env` было фильтром без ANTHROPIC_API_KEY, —
    перепривязка обошла бы его. Прокси, уже пришедший из окружения (ручной
    прогон из shell с рабочим VPN-прокси), важнее записи в settings.json:
    раньше `env.update` молча перетирал его. Сравнение имён — без учёта
    регистра: NO_PROXY в окружении и no_proxy в настройках — одна
    переменная, второй копии с другим значением ребёнку не достаётся.
    """
    present = {k.upper() for k in env}
    for k, v in proxy_env(settings).items():
        if k.upper() not in present:
            env[k] = v
            present.add(k.upper())
