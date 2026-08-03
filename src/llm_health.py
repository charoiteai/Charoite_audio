"""Живость локальной LLM: проба, перезапуск, ожидание.

Ollama умеет вставать так, что снаружи она выглядит здоровой: HTTP-сервер
отвечает, `/api/tags` возвращает список моделей мгновенно, модель числится
загруженной — а инференс стоит. `llama-server` на нуле процента, запрос висит
до таймаута и уходит с ReadTimeout.

03.08 это стоило разбора встречи целиком: запрос к модели провисел десять
минут, `graph_updater` упал, а вместе с ним не выполнилось НИЧЕГО из
пост-процессинга — ни заметки встречи, ни ядер, ни разбора, ни архивной папки.
Со стороны это выглядело так, будто программа «перестала раскладывать встречи
по папкам»: молчание вместо результата, без единой заметной ошибки.

Отсюда два правила, которые держит этот модуль:

1. **Проверять до, а не выяснять после.** Дешёвая проба генерацией стоит
   секунду на живой модели и ловит вставший инференс до того, как в него уйдёт
   стенограмма и следом десять минут ожидания.
2. **Чинить, а не рапортовать.** Вставшую локальную Ollama перезапускаем сами:
   человек в этот момент занят встречей, а не чтением логов.

Перезапуск делается ТОЛЬКО для loopback-адреса. Чужая машина в `llm.base_url` —
не наша собственность: там может быть общий сервер, и «починка» означала бы
уронить его соседям.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import requests

import privacy

# Проба должна пережить холодный старт: 23-гигабайтная модель поднимается с
# диска десятки секунд, и принять это за поломку значило бы перезапускать
# Ollama ровно тогда, когда она честно работает.
PROBE_TIMEOUT = 120

# Сколько ждём, пока перезапущенная Ollama начнёт отвечать.
RESTART_WAIT = 180

# Пауза между пробами в ожидании после перезапуска.
RESTART_POLL = 5

MAC_APP = Path("/Applications/Ollama.app")


def is_local(cfg: dict) -> bool:
    """Живёт ли LLM на этой машине — то есть вправе ли мы её трогать."""
    try:
        url = privacy.llm_base_url(cfg)
    except RuntimeError:
        return False
    return privacy.is_loopback_url(url)


def probe(cfg: dict, timeout: float = PROBE_TIMEOUT) -> bool:
    """Отвечает ли модель хоть чем-нибудь.

    Именно генерация, а не `/api/tags`: у вставшей Ollama список моделей
    отдаётся мгновенно, и проба по нему говорит «здорова» ровно в том случае,
    который мы ловим.
    """
    try:
        r = requests.post(
            privacy.llm_base_url(cfg) + "/api/generate",
            json={
                "model": cfg["llm"]["model"],
                "prompt": "ok",
                "stream": False,
                "think": False,
                "options": {"num_predict": 1},
            },
            timeout=timeout,
        )
    except (requests.RequestException, RuntimeError, KeyError):
        return False
    return r.status_code == 200


def listener_path(url: str) -> str | None:
    """Путь к процессу, который реально слушает порт LLM.

    Без этого вопроса перезапуск бьёт мимо. На машине владельца стоят ОБА
    способа запуска — `/Applications/Ollama.app` и brew-сервис, — а порт держит
    brew. Эвристика «есть Ollama.app → перезапускаем приложение» убивала бы
    GUI, не трогая того, кто завис, и рапортовала бы об успехе: первый живой
    тест сторожа прошёл именно так — ложноположительно.
    """
    port = urllib.parse.urlsplit(url).port or 11434
    try:
        out = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-Fn"],
                             capture_output=True, text=True, timeout=10).stdout
        pids = [ln[1:] for ln in out.splitlines() if ln.startswith("p")]
        if not pids:
            return None
        return subprocess.run(["ps", "-o", "comm=", "-p", pids[0]],
                              capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def restart_commands(system: str, listener: str | None, has_app: bool,
                     has_brew: bool, has_systemctl: bool) -> list[list[str]]:
    """Чем перезапускать Ollama на этой системе.

    Главный сигнал — кто держит порт, а не что установлено: обе установки
    прекрасно уживаются на одной машине, и лечить надо ту, что отвечает на
    запросы. Наличие файлов — только запасной путь, когда владельца порта
    выяснить не вышло (например, сервер уже упал).
    """
    if listener:
        if "Ollama.app" in listener:
            return _gui_restart()
        if "homebrew" in listener or "Cellar" in listener:
            return [["brew", "services", "restart", "ollama"]]
    if system == "Darwin" and has_app:
        return _gui_restart()
    if has_brew:
        return [["brew", "services", "restart", "ollama"]]
    if has_systemctl:
        return [["systemctl", "--user", "restart", "ollama"]]
    return []


def _gui_restart() -> list[list[str]]:
    # pkill по пути внутрь бандла, а не по слову «ollama»: иначе под нож
    # попадает и сам вызывающий процесс, если он запущен из папки проекта.
    return [["pkill", "-f", "Ollama.app/Contents"], ["open", "-a", "Ollama"]]


def _restart(cfg: dict, log: Callable[[str], None]) -> bool:
    try:
        listener = listener_path(privacy.llm_base_url(cfg))
    except RuntimeError:
        listener = None
    cmds = restart_commands(
        platform.system(),
        listener,
        MAC_APP.exists(),
        shutil.which("brew") is not None,
        shutil.which("systemctl") is not None,
    )
    if not cmds:
        log("LLM: перезапустить нечем — ни Ollama.app, ни brew, ни systemctl")
        return False
    log(f"LLM: перезапуск через {' '.join(cmds[-1])}"
        + (f" (порт держит {listener})" if listener else " (владелец порта не определён)"))
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=False, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            # pkill возвращает 1, когда убивать нечего, — это не ошибка;
            # а вот отсутствующий бинарь стоит назвать вслух.
            log(f"LLM: {' '.join(cmd)} не выполнилась ({type(e).__name__}: {e})")
            return False
    return True


def ensure_alive(cfg: dict, log: Callable[[str], None] = print,
                 wait: float = RESTART_WAIT) -> bool:
    """Убедиться, что модель отвечает; вставшую локальную — перезапустить.

    Возвращает False честно: вызывающий должен знать, что дальше идти незачем,
    а не выяснять это через таймаут на длинном запросе.
    """
    if probe(cfg):
        return True
    if not is_local(cfg):
        log("LLM не отвечает, но адрес не локальный — перезапуск не наше дело")
        return False
    log("LLM не отвечает на пробу — перезапускаю Ollama")
    if not _restart(cfg, log):
        return False

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(RESTART_POLL)
        if probe(cfg, timeout=min(60, wait)):
            log("LLM ожила после перезапуска")
            return True
    log(f"LLM не ответила за {int(wait)} с после перезапуска")
    return False
