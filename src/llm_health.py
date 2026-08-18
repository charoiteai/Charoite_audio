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

Движка два (privacy.llm_engine). У mlx-server симптоматика своя, но правила
те же: проба — генерацией одного токена через /v1/chat/completions, починка —
перезапуск. Супервизора у mlx-сервера нет (у Ollama это brew/launchd или
GUI-приложение), поэтому чинит его ЭТОТ модуль: убивает владельца порта и
поднимает `python -m mlx_lm server` дочерним процессом, лог —
logs/mlx_server.log. Загрузка 20+ ГБ весов занимает десятки секунд —
ожидание после перезапуска это учитывает.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import requests

import privacy
from charoite_paths import resolve_root
from llm import DEFAULT_MLX_MODEL

ROOT = resolve_root(__file__)

# Проба должна пережить холодный старт: 23-гигабайтная модель поднимается с
# диска десятки секунд, и принять это за поломку значило бы перезапускать
# Ollama ровно тогда, когда она честно работает.
PROBE_TIMEOUT = 120

# Сколько ждём, пока перезапущенная Ollama начнёт отвечать.
RESTART_WAIT = 180

# Пауза между пробами в ожидании после перезапуска.
RESTART_POLL = 5

MAC_APP = Path("/Applications/Ollama.app")


def _base_url(cfg: dict) -> str:
    """Адрес живого движка: у mlx-server свой порт и свой privacy-ключ."""
    if privacy.llm_engine(cfg) == "mlx-server":
        return privacy.mlx_base_url(cfg)
    return privacy.llm_base_url(cfg)


def is_local(cfg: dict) -> bool:
    """Живёт ли LLM на этой машине — то есть вправе ли мы её трогать."""
    try:
        url = _base_url(cfg)
    except RuntimeError:
        return False
    return privacy.is_loopback_url(url)


# Ответ пробы «сервер жив, но модель занята другим запросом»: Ollama 0.32 с
# MLX-раннером отдаёт 503 за четверть секунды вместо очереди (факт 18.08).
# Это НЕ повод перезапускать сервер — перезапуск убил бы ровно ту генерацию,
# которая его и занимает (так граф 12.08 трижды ронял Ollama под соседней
# пересборкой: «LLM не отвечает на пробу — перезапускаю»).
BUSY = "busy"
BUSY_STATUSES = (429, 502, 503)


def probe(cfg: dict, timeout: float = PROBE_TIMEOUT) -> bool | str:
    """Отвечает ли модель хоть чем-нибудь.

    Именно генерация, а не `/api/tags` (или `/v1/models` у mlx): у вставшего
    сервера список моделей отдаётся мгновенно, и проба по нему говорит
    «здорова» ровно в том случае, который мы ловим.

    True — ответила; False — не ответила (сеть, таймаут, HTTP-ошибка);
    BUSY — сервер жив, но модель занята (503/429): не чинить, а подождать.
    """
    try:
        if privacy.llm_engine(cfg) == "mlx-server":
            r = requests.post(
                privacy.mlx_base_url(cfg) + "/v1/chat/completions",
                json={
                    "model": str((cfg.get("llm") or {}).get("mlx_model")
                                 or DEFAULT_MLX_MODEL),
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "max_tokens": 1,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=timeout,
            )
        else:
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
    if r.status_code in BUSY_STATUSES:
        return BUSY
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


def _mlx_listener_pid(url: str) -> int | None:
    """PID процесса, который держит порт mlx-сервера (None — порт свободен)."""
    port = urllib.parse.urlsplit(url).port or 8080
    try:
        out = subprocess.run(["lsof", "-nP", f"-i:{port}", "-sTCP:LISTEN", "-Fp"],
                             capture_output=True, text=True, timeout=10).stdout
        pids = [int(ln[1:]) for ln in out.splitlines() if ln.startswith("p")]
        return pids[0] if pids else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _restart_mlx(cfg: dict, log: Callable[[str], None]) -> bool:
    """Перезапуск mlx_lm.server: снять владельца порта, поднять свой процесс.

    Убиваем ТОЛЬКО владельца порта из loopback-адреса конфига — это либо наш
    прежний сервер, либо то, что мешает его поднять; SIGTERM, пауза, SIGKILL.
    Новый сервер — отдельная сессия (переживает смерть демона), лог в
    logs/mlx_server.log: молча умерший сервер без лога — это снова «молчание
    вместо результата».
    """
    url = privacy.mlx_base_url(cfg)
    pid = _mlx_listener_pid(url)
    if pid is not None:
        log(f"mlx-server: снимаю владельца порта (pid {pid})")
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
            os.kill(pid, 0)          # ещё жив?
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass                     # умер сам — то, чего и добивались
    model = str((cfg.get("llm") or {}).get("mlx_model") or DEFAULT_MLX_MODEL)
    port = urllib.parse.urlsplit(url).port or 8080
    (ROOT / "logs").mkdir(exist_ok=True)
    logf = (ROOT / "logs" / "mlx_server.log").open("a")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "mlx_lm", "server",
             "--model", model, "--port", str(port)],
            start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"mlx-server: не запустился ({type(e).__name__}: {e})")
        return False
    finally:
        logf.close()                 # у потомка своя копия дескриптора
    log(f"mlx-server: поднимаю {model} на порту {port}")
    return True


def ensure_alive(cfg: dict, log: Callable[[str], None] = print,
                 wait: float = RESTART_WAIT) -> bool:
    """Убедиться, что модель отвечает; вставшую локальную — перезапустить.

    Возвращает False честно: вызывающий должен знать, что дальше идти незачем,
    а не выяснять это через таймаут на длинном запросе.

    Занятая модель (BUSY) — не поломка: ждём до `wait` секунд, пока она
    освободится, и НЕ перезапускаем. Не дождались — всё равно True: сервер
    жив, а очередь за занятой моделью вызывающий отстоит сам (busy_wait в
    llm.complete/stream).
    """
    state = probe(cfg)
    if state is True:
        return True
    if state == BUSY:
        log("LLM занята другим запросом — жду, не перезапускаю")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(RESTART_POLL)
            state = probe(cfg, timeout=min(60, wait))
            if state is True:
                log("LLM освободилась")
                return True
            if state is False:
                break          # была занята, а теперь молчит — дальше обычный путь
        else:
            log(f"LLM всё ещё занята после {int(wait)} с — иду в очередь за ней")
            return True
    if not is_local(cfg):
        log("LLM не отвечает, но адрес не локальный — перезапуск не наше дело")
        return False
    mlx = privacy.llm_engine(cfg) == "mlx-server"
    log("LLM не отвечает на пробу — перезапускаю "
        + ("mlx_lm.server" if mlx else "Ollama"))
    if not (_restart_mlx(cfg, log) if mlx else _restart(cfg, log)):
        return False

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(RESTART_POLL)
        if probe(cfg, timeout=min(60, wait)):   # True или BUSY — сервер поднялся
            log("LLM ожила после перезапуска")
            return True
    log(f"LLM не ответила за {int(wait)} с после перезапуска")
    return False
