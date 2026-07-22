"""Суфлёр-демон для UI: события NDJSON в stdout, команды из stdin.

События:  {"type":"status","text":...} | {"type":"transcript","ts":"HH:MM:SS","text":...}
          {"type":"thesis","text":...}  | {"type":"hint","text":...} | {"type":"hint_done"}
Команды (stdin, по строке): hint | summary | stop
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import math
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

import requests
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from audio import AudioHub  # noqa: E402
from llm import LLM  # noqa: E402
from main import NOISE, Transcript  # noqa: E402
from stt import STT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
THESIS_EVERY = 40.0     # автотезисы: раз в N секунд по новым фразам
HINT_EVERY = 75.0       # автоподсказки: не чаще, чем раз в N секунд
HINT_MIN_NEW = 220      # и только если накопилось столько новых знаков разговора

# Гейт мгновенного ответа. «?» ставит сама GigaAM (нейро-пунктуация) — это основной
# AI-сигнал; стартовые слова лишь страхуют, когда STT не дорисовала знак.
# Классификатор здесь не вариант: цена — лишние секунды задержки на каждой реплике.
_Q_START = {
    "как", "что", "чем", "почему", "зачем", "сколько", "когда", "кто", "куда",
    "где", "какой", "какая", "какие", "каким", "какую", "расскажи", "расскажите",
    "объясни", "объясните", "опиши", "опишите", "поясни", "поясните", "можешь", "можете",
}
_Q_PAIRS = {"есть ли", "правда ли", "верно ли", "был ли", "будет ли", "а вы", "а ты"}


def looks_question(text: str) -> bool:
    if text.rstrip().endswith("?"):
        return True
    words = text.strip().lower().split()
    return bool(words) and (words[0] in _Q_START or " ".join(words[:2]) in _Q_PAIRS)

_out_lock = threading.Lock()


def emit(obj: dict):
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def append_hint(tr_path: pathlib.Path, header: str, body: str):
    """Дозапись в _hints.md. Полный диск/недоступная папка не должны молча
    убивать вечный тред (open стоял вне try в трёх контурах)."""
    try:
        hpath = tr_path.with_name(tr_path.stem + "_hints.md")
        with hpath.open("a", encoding="utf-8") as f:
            f.write(f"\n## {header}\n{body}\n")
    except Exception as e:  # noqa: BLE001
        emit({"type": "status", "text": f"запись подсказок: {e}"})


def load_claude_proxy_env() -> dict:
    """Прокси из ~/.claude/settings.json (env-секция).

    Демон из desktop-приложения стартует без shell-окружения, а `--setting-sources ""`
    отрезает env настроек — headless `claude -p` шёл к api.anthropic.com напрямую
    и ловил 403 Request not allowed (регион). Подкладываем прокси явно.
    """
    try:
        s = json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return {k: v for k, v in s.get("env", {}).items() if "proxy" in k.lower()}
    except Exception:  # noqa: BLE001
        return {}


def load_graph_context(cfg: dict) -> str:
    """Память прошлых встреч из Obsidian-графа: MOC + две последние встречи."""
    gdir = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser()
    limit = int(cfg["sufler"].get("graph_context_chars", 2500))
    if not gdir.exists():
        return ""
    parts: list[str] = []
    moc = gdir / "_MOC.md"
    if moc.exists():
        parts.append(moc.read_text(encoding="utf-8")[:1200])
    meetings = sorted((gdir / "Встречи").glob("*.md")) if (gdir / "Встречи").exists() else []
    for m in meetings[-2:]:
        parts.append(m.read_text(encoding="utf-8")[:900])
    return "\n---\n".join(parts)[:limit]


def main():
    # single-instance: второй демон устроил бы битую стенограмму (один .tmp-путь)
    (ROOT / "logs").mkdir(exist_ok=True)
    lockf = open(ROOT / "logs" / "daemon.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        emit({"type": "status", "text": "⚠️ Суфлёр уже слушает в другом окне — второй запуск отменён"})
        return
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    emit({"type": "status", "text": "Загружаю модели…"})
    stt = STT(cfg)
    llm = LLM(cfg)
    hub = AudioHub(cfg)
    hub.on_status = lambda t: emit({"type": "status", "text": t})
    # env-override для тестов: стенограммы в песочницу, не в боевую папку
    tdir = os.environ.get("SUFLER_TRANSCRIPTS_DIR")
    tr = Transcript(pathlib.Path(tdir) if tdir else ROOT / cfg["log"]["transcripts_dir"])
    graph_ctx = load_graph_context(cfg)
    if graph_ctx:
        llm.system += f"\n\nПамять прошлых встреч (из графа проекта):\n{graph_ctx}"
        emit({"type": "status", "text": f"Граф подключён к промптам ({len(graph_ctx)} зн. памяти)"})
    threading.Thread(target=llm.warmup, daemon=True).start()
    emit({"type": "status", "text": f"Слушаю: {' + '.join(hub.sources)} · LLM: {llm.resolve_model()}"})

    stop = threading.Event()
    # SIGTERM (Swift terminate по грейсу) → штатный стоп с finally, а не убийство
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    hub.start()

    # Живая диаризация ОБОИХ каналов: звонок кладёт чужие голоса в BlackHole,
    # очная встреча — все голоса в микрофон. Трекер один, метки по маппингу:
    # первый голос из mic = владелец (его микрофон), остальные — «Собеседник N».
    spk_tracker = None
    voice_names: dict[int, str] = {}
    if bool(cfg["sufler"].get("live_diarize", True)):
        try:
            from diarize_live import SpeakerTracker
            emb_model = ROOT / "models" / "diar" / "embedding.onnx"
            if emb_model.exists():
                spk_tracker = SpeakerTracker(
                    emb_model, sample_rate=hub.sr,
                    threshold=float(cfg["sufler"].get("live_diarize_threshold", 0.45)))
                emit({"type": "status", "text": "👥 живая диаризация голосов включена"})
        except Exception as e:  # noqa: BLE001 — диаризация вспомогательна
            emit({"type": "status", "text": f"живая диаризация недоступна: {e}"})

    def voice_label(channel_speaker: str, chunk) -> str:
        """Метка голоса для чанка. Живая разметка НЕ угадывает владельца (решение
        20.07: «первый голос mic» ловил лектора из видео, «доминирование» тоже
        ошибалось) — все голоса нейтральные «Собеседник N». Имена расставляют
        name_loop (из разговора) и финальная пересборка записи после Стопа."""
        if spk_tracker is None:
            return channel_speaker  # без трекера канальные метки честны в звонке
        try:
            n = spk_tracker.label(chunk)
        except Exception:  # noqa: BLE001
            return channel_speaker
        if n is None:
            return channel_speaker
        name = voice_names.get(n)
        if name is None:
            name = f"Собеседник {len(voice_names) + 1}"
            voice_names[n] = name
        return name

    def stt_loop():
        while not stop.is_set():
            batch = hub.pull_labeled()
            if not batch:
                time.sleep(0.1)
                continue
            for speaker, chunk in batch:
                try:
                    text = stt.transcribe(chunk, hub.sr)
                except Exception as e:  # noqa: BLE001
                    emit({"type": "status", "text": f"STT: {e}"})
                    continue
                if not text or text.lower().strip(" .!») ") in NOISE:
                    continue
                speaker = voice_label(speaker, chunk)
                try:
                    added = tr.add(text, speaker=speaker)
                except Exception as e:  # noqa: BLE001 — стенограмма не должна убивать STT-тред
                    emit({"type": "status", "text": f"стенограмма: {e}"})
                    continue
                if added:  # полностью съеденные дедупом не эмитим
                    disp = tr.display_name(speaker)
                    emit({
                        "type": "transcript",
                        "ts": f"{dt.datetime.now():%H:%M:%S}",
                        "speaker": disp,   # UI клеит куски одного голоса в абзац
                        "plain": added,
                        "text": f"{disp}: {added}",  # совместимость со старым UI
                    })
                    # режим собеседования: вопрос с той стороны → мгновенный ответ.
                    # startswith: живая диаризация метит «Собеседник N» — строгое
                    # равенство оставляло ⚡/☁️ мёртвыми всю встречу
                    if instant_on and toggles["hints"] and speaker.startswith("Собеседник") \
                            and looks_question(added):
                        fire_question(added)

    THINK_SYSTEM = (
        "Ты — второй мозг владельца на рабочей встрече: думаешь вместе с ним по живой стенограмме. "
        "Из НОВОГО фрагмента выдели только по-настоящему ценное, каждое с новой строки со строгим префиксом:\n"
        "📌 — контрольная точка: решение, договорённость, срок, поручение (кто/что/когда)\n"
        "💎 — ценная информация: цифра, имя, обещание, условие, риск\n"
        "💭 — твоя мысль (максимум одна): противоречие со сказанным ранее, упущенный вопрос, скрытый риск\n"
        "ИГНОРИРУЙ фоновое медиа: радио, телевизор, ролики, новости, политика, "
        "реклама — всё, что явно не разговор присутствующих о работе. Из такого "
        "фрагмента тезисы не делай (21.07: «поручение» из новостного эфира "
        "попало в контрольные точки).\n"
        "Телеграфно, по-русски. Если ничего ценного не прозвучало — ответь ровно: NONE"
    )

    def think_loop():
        """Ко-мышление: КТ, ценные факты и мысли модели по ходу встречи."""
        seen = 0
        context_tail = ""
        while not stop.is_set():
            time.sleep(THESIS_EVERY)
            if not toggles["theses"]:
                continue
            full = tr.full()
            fresh = full[seen:]
            if len(fresh) < 120:  # мало нового — не гонять модель
                continue
            seen = len(full)
            try:
                out = "".join(
                    llm.stream(
                        (f"Контекст (уже обработано):\n{context_tail}\n\n" if context_tail else "")
                        + f"НОВЫЙ фрагмент стенограммы:\n{fresh}",
                        model=cfg["sufler"].get("think_model", llm.small),
                        system=THINK_SYSTEM,
                    )
                )
                context_tail = fresh[-800:]
                if "NONE" in out and len(out.strip()) < 12:
                    continue
                for line in out.strip().splitlines():
                    line = line.strip()
                    if not line or line == "NONE":
                        continue
                    emit({"type": "thesis", "text": line})
                    if line.startswith(("📌", "💎", "💭")):
                        tr.note(line)
            except Exception as e:  # noqa: BLE001
                emit({"type": "status", "text": f"мышление: {e}"})

    hint_lock = threading.Lock()   # подсказки/минутки на 26b — по одной за раз
    manual_evt = threading.Event()  # ручной запрос прерывает авто-генерацию
    max_ctx = int(cfg["llm"]["max_context_chars"])
    quiet = bool(cfg["sufler"].get("quiet", True))
    instant_on = bool(cfg["sufler"].get("instant", True))
    auto_model = llm.small if quiet else None  # тихий режим: весь фон без 26b
    instant_evt = threading.Event()
    cloud_live = bool(cfg["sufler"].get("cloud_live", True)) and not os.environ.get("SUFLER_NO_CLOUD")
    cloud_evt = threading.Event()
    _last_fire = [0.0]
    _cloud_last = {"t": 0.0, "words": set()}
    _pending_q = {"text": ""}  # последний детектированный вопрос — панели показывают его над ответом
    # живые тумблеры UI (`set hints|theses|cloud on|off`): выключенные контуры
    # молчат до обратного включения; дефолты хранит и присылает приложение
    toggles = {"hints": True, "theses": True, "cloud": True}

    def fire_question(q: str = ""):
        """Один вопрос = один ⚡/☁️: fast_trigger и stt_loop не дублируют друг друга."""
        now = time.time()
        if now - _last_fire[0] < 8:
            return
        _last_fire[0] = now
        if q.strip():  # панели показывают, НА ЧТО отвечают — без этого ответ висел без вопроса
            _pending_q["text"] = " ".join(q.split())[:200]
        instant_evt.set()
        if not (cloud_live and toggles["cloud"]):
            return
        # Дедуп облака по содержанию вопроса: переформулировка/повтор той же фразы
        # (partial → финал STT, «то есть…») давала второй ответ Haiku на тот же
        # вопрос — Haiku отвечает 15-20с, временной дебаунс 8с это не ловил.
        words = set(re.findall(r"[а-яёa-z0-9]{3,}", q.lower()))
        prev = _cloud_last["words"]
        same = words and prev and len(words & prev) / len(words | prev) > 0.5
        if same and now - _cloud_last["t"] < 60:
            return
        if words:  # пустой вопрос («ну а вы?») не должен продлевать чужое окно дедупа
            _cloud_last["t"], _cloud_last["words"] = now, words
        cloud_evt.set()
    if quiet:
        emit({"type": "status", "text": f"🔇 тихий режим: фон на {llm.small}, 26b — только точечно"})

    def gen_hint(header: str | None = None, manual: bool = False, model: str | None = None):
        if manual:
            manual_evt.set()  # сигнал авто-генерации уступить
        with hint_lock:
            if manual:
                manual_evt.clear()
            tail = tr.tail(max_ctx)
            if not tail:
                emit({"type": "hint", "text": "Стенограмма пока пуста."})
                emit({"type": "hint_done"})
                return
            if header:
                emit({"type": "hint", "text": header})
            parts: list[str] = []
            try:
                for tok in llm.hint(tail, model=model):
                    if not manual and manual_evt.is_set():
                        emit({"type": "hint", "text": " …⏸"})
                        parts.append(" …⏸")
                        break  # уступаем ручному запросу
                    emit({"type": "hint", "text": tok})
                    parts.append(tok)
            except Exception as e:  # noqa: BLE001
                emit({"type": "hint", "text": f"[LLM: {e}]"})
            emit({"type": "hint_done"})
            if parts:  # подсказки тоже сохраняем — лог полного разговора
                kind = "ручная" if manual else "авто"
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] подсказка ({kind})", "".join(parts))

    def auto_hint_loop():
        """Подсказки в реальном времени: сами, по мере накопления разговора."""
        seen = 0
        while not stop.is_set():
            time.sleep(HINT_EVERY)
            if not toggles["hints"]:
                continue
            full = tr.full()
            if len(full) - seen < HINT_MIN_NEW:
                continue  # разговор не набежал — молчим
            seen = len(full)
            try:
                gen_hint(header=f"\n\n━━ авто {dt.datetime.now():%H:%M} ━━\n", model=auto_model)
            except Exception as e:  # noqa: BLE001 — единственный поток без своего try:
                # сбой вне внутреннего try gen_hint (например, запись подсказки в
                # файл на недоступном iCloud) убивал поток НАВСЕГДА, а heartbeat
                # главного треда продолжал идти — UI считал, что всё живо
                emit({"type": "status", "text": f"авто-подсказка сорвалась: {e}"})

    def instant_loop():
        """Режим собеседования: вопрос от собеседника → готовый ответ без задержки.

        Лёгкая модель: первые слова через ~2-3с после конца фразы, кулер молчит.
        """
        while not stop.is_set():
            if not instant_evt.wait(timeout=0.5):
                continue
            instant_evt.clear()
            manual_evt.set()  # авто-подсказка уступает мгновенному ответу
            with hint_lock:
                manual_evt.clear()
                tail = tr.tail(1600)
                if not tail:
                    continue
                q = _pending_q["text"]
                head = f"❓ {q}" if q else "ответ на вопрос"
                emit({"type": "hint", "text": f"\n\n⚡ {dt.datetime.now():%H:%M:%S} — {head}\n"})
                parts: list[str] = []
                try:
                    for tok in llm.instant(tail):
                        emit({"type": "hint", "text": tok})
                        parts.append(tok)
                except Exception as e:  # noqa: BLE001
                    emit({"type": "hint", "text": f"[LLM: {e}]"})
                emit({"type": "hint_done"})
                if parts:
                    label = f"⚡ ответ на: {q[:120]}" if q else "⚡ мгновенный ответ"
                    append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] {label}", "".join(parts))

    def cloud_loop():
        """Лестница live: параллельно локальному ⚡ — ответ Claude Sonnet в свою панель.

        Headless `claude -p` по подписке Max (API-ключ вырезан из env).
        Локальный ответ приходит за ~2-3с, Sonnet догоняет глубже за ~10-20с.
        """
        claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
        model = cfg["sufler"].get("cloud_live_model", "claude-sonnet-5")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update(load_claude_proxy_env())  # без прокси из GUI-запуска — 403 по региону
        while not stop.is_set():
            if not cloud_evt.wait(timeout=0.5):
                continue
            cloud_evt.clear()
            tail = tr.tail(2200)
            if not tail:
                continue
            q = _pending_q["text"]
            short = model.split("-")[1] if model.count("-") else model  # claude-haiku-… → haiku
            think = f"☁️ {dt.datetime.now():%H:%M:%S} {short} думает" + (f" над: ❓ {q[:120]}" if q else "…")
            emit({"type": "cloud_start", "text": think})
            try:
                r = subprocess.run(
                    [claude_bin, "-p",
                     "Рабочая встреча , пользователь владелец — техлид (контекст: проект, "
                     "витрины данных, Airflow, GPU). Последние реплики:\n" + tail + "\n\n"
                     "Собеседник задал вопрос (последняя реплика). Дай владельцу ГОТОВЫЙ ответ "
                     "от первого лица: 3-5 предложений, уверенно, по делу, по-русски. "
                     "Только текст ответа — без преамбул, без markdown-заголовков.",
                     "--model", model,
                     "--disallowedTools", "Bash,Read,Write,Edit,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,AskUserQuestion,TodoWrite",
                     # без пользовательских hooks/MCP: внешний хук на каждый промпт
                     # лезет в Ollama, занятую instant-ответом → вызов висел (паттерн claude-mem)
                     "--setting-sources", "", "--strict-mcp-config"],
                    capture_output=True, text=True, timeout=90, env=env,
                    stdin=subprocess.DEVNULL,  # иначе claude наследует fifo демона и ждёт EOF вечно
                )
                out = (r.stdout or "").strip() or f"[claude: {(r.stderr or 'пустой ответ')[:150]}]"
            except subprocess.TimeoutExpired:
                out = "[cloud: таймаут 90с]"
            except Exception as e:  # noqa: BLE001
                out = f"[cloud: {e}]"
            if q:  # ответ в панели начинается с вопроса, на который отвечает
                out = f"❓ {q}\n\n{out}"
            emit({"type": "cloud", "text": out})
            emit({"type": "cloud_done"})
            label = f"☁️ {model} — на: {q[:120]}" if q else f"☁️ {model}"
            append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] {label}", out)

    def fast_trigger_loop():
        """Быстрый триггер вопросов через gigastt-стрим: partial ~0.8с вместо чанка 3с.

        ТОЛЬКО детект вопроса (текст стриминга хуже нашего STT и в стенограмму не идёт):
        финалы канала Собеседника → looks_question → ⚡/☁️ на ~2.5с раньше.
        Если локальный gigastt-сервер не поднят — тихо работаем по-старому.
        Обрыв WebSocket (keepalive-таймаут под CPU-голоданием 20.07) раньше выключал
        триггер до конца встречи — теперь реконнект с backoff.
        """
        if not (instant_on or cloud_live) or not bool(cfg["sufler"].get("fast_trigger", True)):
            return
        try:
            import requests as _rq
            _rq.get("http://127.0.0.1:9876/health", timeout=2).raise_for_status()
            from websockets.sync.client import connect as ws_connect
        except Exception:
            return  # сервера/библиотеки нет — обычный путь через чанки
        import queue as _q
        frame_q: _q.Queue = _q.Queue(maxsize=300)

        def _tap(src, part):
            if src == "blackhole" and not frame_q.full():
                frame_q.put(part)

        hub.on_frame = _tap
        emit({"type": "status", "text": "⚡ быстрый триггер вопросов: gigastt-стрим подключён"})
        delay = 5.0
        while not stop.is_set():
            opened = time.time()
            try:
                # дренаж: за backoff в очереди скапливается до 75с УСТАРЕВШЕГО звука —
                # новый коннект стрелял бы ложными ⚡ по давно прозвучавшим вопросам
                while not frame_q.empty():
                    try:
                        frame_q.get_nowait()
                    except _q.Empty:
                        break
                with ws_connect("ws://127.0.0.1:9876/v1/ws", max_size=None) as ws:
                    ws.recv()  # {"type":"ready"}
                    ws.send(json.dumps({"type": "configure", "sample_rate": hub.sr}))

                    def sender(ws=ws):
                        import numpy as _np
                        while not stop.is_set():
                            try:
                                part = frame_q.get(timeout=0.3)
                            except _q.Empty:
                                continue
                            try:
                                ws.send((_np.clip(part, -1, 1) * 32767).astype("<i2").tobytes())
                            except Exception:  # noqa: BLE001 — сокет умер, reader переподключит
                                return
                        try:
                            ws.close()
                        except Exception:  # noqa: BLE001
                            pass

                    threading.Thread(target=sender, daemon=True).start()
                    recent = ""
                    for msg in ws:
                        if stop.is_set():
                            return
                        if isinstance(msg, bytes):
                            continue
                        d = json.loads(msg)
                        if d.get("type") != "final":
                            continue
                        recent = (recent + " " + (d.get("text") or "")).strip()[-160:]
                        if looks_question(recent):
                            fire_question(recent)
                            recent = ""
            except Exception as e:  # noqa: BLE001
                if stop.is_set():
                    return
                if time.time() - opened > 120:
                    delay = 5.0  # сессия жила долго — обрыв разовый, backoff с нуля
                emit({"type": "status", "text":
                      f"быстрый триггер: обрыв ({type(e).__name__}), реконнект через {int(delay)}с"})
                if stop.wait(delay):
                    return
                delay = min(delay * 2, 60.0)

    def deja_vu_loop():
        """⏮ Предиктивные точки: тема разговора уже обсуждалась раньше —
        показываем, когда и с каким статусом. Источник — Ядра графа (сквозные
        темы с хроникой встреч). Каждое ядро показывается раз за встречу.

        Совпадение ищем ПО СМЫСЛУ (эмбеддинги bge-m3 через Ollama, модель уже
        стоит для графа), а не по обрубкам слов: прежний матч по основам искал
        «бюджет» в тексте буквально и не видел, что «урезали финансирование
        GPU» — это ядро «Бюджетирование GPU ресурсов». Векторы ядер считаются
        один раз (18 ядер ≈ 1.8с) и живут в памяти; на каждом проходе считается
        только вектор свежего фрагмента (≈0.2с).

        Порог — ОТНОСИТЕЛЬНЫЙ: bge-m3 даёт узкий разброс косинусов (замер 22.07
        на живом графе: 0.33…0.45), поэтому абсолютная отсечка не работает.
        Берём лидера, только если он заметно оторвался от медианы.
        """
        if not bool(cfg["sufler"].get("deja_vu", True)):
            return
        gdir = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser()
        cores_dir = gdir / "Ядра"
        emb_model = cfg["sufler"].get("embed_model", "bge-m3:latest")
        margin = float(cfg["sufler"].get("deja_vu_margin", 0.04))
        shown: set[str] = set()
        seen_len = 0
        vecs: dict[str, list[float]] = {}  # ядро → вектор (кэш на всю встречу)

        def embed(texts: list[str]) -> list[list[float]]:
            # 20с, не 120: эмбеддинг занимает ~0.2с, и если Ollama занят тяжёлой
            # генерацией — лучше пропустить проход дежавю, чем держать поток
            # заблокированным две минуты
            r = requests.post(cfg["llm"]["base_url"].rstrip("/") + "/api/embed",
                              json={"model": emb_model, "input": texts}, timeout=20)
            return r.json().get("embeddings", []) or []

        def cosine(a: list[float], b: list[float]) -> float:
            num = sum(x * y for x, y in zip(a, b))
            den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
            return num / den if den else 0.0

        while not stop.is_set():
            time.sleep(45)
            if not toggles["theses"] or not cores_dir.exists():
                continue
            full = tr.full()
            fresh = full[seen_len:]
            if len(fresh) < 300:
                continue
            seen_len = len(full)
            try:
                cores = [p for p in sorted(cores_dir.glob("*.md"))
                         if not p.name.startswith("_")]
                if not cores:
                    continue
                # прогреваем кэш векторов один раз (и добираем новые ядра)
                fresh_cores = [p for p in cores if p.stem not in vecs]
                if fresh_cores:
                    payload = []
                    for p in fresh_cores:
                        txt = p.read_text(encoding="utf-8")
                        m = re.search(r"## Статус\n(.+)", txt)
                        st = re.sub(r"_\(.*?\)_", "", m.group(1)).strip() if m else ""
                        payload.append(f"{p.stem}. {st}"[:400])
                    got = embed(payload)
                    for p, v in zip(fresh_cores, got):
                        vecs[p.stem] = v
                qv = embed([" ".join(fresh[-1500:].split())])
                if not qv:
                    continue
                scored = sorted(((cosine(qv[0], vecs[p.stem]), p) for p in cores
                                 if p.stem in vecs), key=lambda x: -x[0])
                if len(scored) < 3:
                    continue
                mid = scored[len(scored) // 2][0]  # медиана как «фон» разговора
                top_score, top = scored[0]
                if top.stem in shown or top_score - mid < margin:
                    continue
                text = top.read_text(encoding="utf-8")
                m = re.search(r"## Статус\n(.+)", text)
                dates = sorted({d for d in re.findall(
                    r"\[\[Встречи/(\d{4}-\d{2}-\d{2})_\d{4}", text)})
                when = ", ".join(d[8:10] + "." + d[5:7] for d in dates[-2:]) or "ранее"
                st = re.sub(r"_\(.*?\)_", "", m.group(1)).strip() if m else ""
                shown.add(top.stem)
                line = f"⏮ {top.stem} — уже обсуждалось {when}." + (f" Статус: {st}" if st else "")
                emit({"type": "thesis", "text": line})
                tr.note(line)
            except Exception:  # noqa: BLE001 — дежавю вспомогательно
                pass

    def dialog_markup_loop():
        """Семантические реплики в живом окне: e4b расставляет диалоговые «—»
        внутри отлежавшегося абзаца. Акустика одного микрофона не видит смену
        голоса в комнате — смысл текста видит («— А ты что думаешь?»).
        Слова не меняются (проверка кодом), файл финально пересоберёт rebuild."""
        if not bool(cfg["sufler"].get("live_dialog_markup", True)):
            return
        seen: set[tuple[int, int]] = set()
        norm = lambda t: re.findall(r"[а-яёa-z0-9]+", t.lower())
        while not stop.is_set():
            time.sleep(6)
            blk = tr.last_block()
            if blk is None:
                continue
            idx, t1, spk, text = blk
            key = (idx, len(text))
            # ждём паузу в блоке (реплика закончилась) и достаточно текста
            if key in seen or len(text) < 320 or "\n— " in text \
                    or (dt.datetime.now() - t1).total_seconds() < 6:
                continue
            seen.add(key)
            try:
                out = "".join(llm.stream(
                    f"Фрагмент живой стенограммы (в нём могли слиться реплики РАЗНЫХ "
                    f"говорящих):\n{text}\n\n"
                    "Разбей на реплики диалога: каждая реплика с новой строки, "
                    "начинается с «— ». СЛОВА НЕ МЕНЯЙ, ничего не добавляй и не "
                    "удаляй. Если это одна реплика одного человека — верни текст "
                    "без изменений.",
                    # разметке нужна дословность: бенч 21.07 — qwen3.5:4b чуть
                    # правит слова (валидация режет), gemma держит их точно
                    model=cfg["sufler"].get("markup_model", llm.small),
                    system="Ты расставляешь границы реплик в стенограмме. Слова неприкосновенны.",
                    num_predict=900,
                )).strip()
                # валидация: слова обязаны совпасть — e4b не имеет права переписывать
                if not out or norm(out) != norm(text) or out.count("— ") < 2:
                    continue
                if tr.update_block_text(idx, text, out):
                    emit({"type": "transcript_markup", "speaker": tr.display_name(spk),
                          "text": out})
            except Exception:  # noqa: BLE001 — разметка вспомогательна
                pass

    def name_loop():
        """Опознание людей из разговора, всю встречу (каждые ~90с).

        Живая замена меток: «Собеседник N» → имя, как только оно определено
        НАДЁЖНО (человек представился, или к нему обратились и он ответил).
        Меняется задним числом стенограмма (rename_speaker), лента приложения
        (событие rename) и все будущие реплики (voice_names). владельца не
        угадываем (решение 20.07). Без диаризации — старый одиночный режим.
        """
        named = False
        listed: list[str] = []
        renamed: dict[str, str] = {}  # «Собеседник 1» → «Дмитрий»
        # владельца не подписываем: его голос определяется каналом микрофона,
        # а не разговором. Пусто в конфиге — проверка просто не сработает.
        owner_name = (cfg["sufler"].get("user_name") or "").strip().lower()
        while not stop.is_set():
            time.sleep(90)
            sample = tr.tail(3000)
            if sample.count("Собеседник") < 2 and not listed:
                continue  # та сторона ещё толком не говорила
            try:
                if spk_tracker is not None:
                    # мультиспикер: qwen сопоставляет имена меткам, JSON + гварды
                    labels = sorted(set(re.findall(r"Собеседник \d+", sample)) - set(renamed))
                    if labels:
                        out = "".join(llm.stream(
                            f"Стенограмма (метки говорящих условные):\n{sample}\n\n"
                            "Определи ИМЕНА говорящих. КРИТИЧНО: имя внутри реплики — "
                            "почти всегда ОБРАЩЕНИЕ к ДРУГОМУ человеку («Саш, а ты…» "
                            "говорит НЕ Саша). Говорящий получает имя только если: "
                            "(а) он сам представился («это Таня», «меня зовут…»), или "
                            "(б) к нему обратились по имени В ЧУЖОЙ реплике и он ответил "
                            "СЛЕДУЮЩЕЙ репликой. Имя — в именительном падеже (Таня, не "
                            "Тань). Не путай с названиями компаний и междометиями. "
                            'Верни ТОЛЬКО JSON вида {"Собеседник 1": "Имя"} — лишь метки, '
                            "в которых УВЕРЕН. Не уверен ни в ком — верни {}.",
                            model=cfg["sufler"].get("think_model", llm.small),
                            system="Ты сопоставляешь имена говорящим по стенограмме. Только JSON.",
                        ))
                        # берём ПОСЛЕДНИЙ плоский {...}: жадный \{.*\} склеивал
                        # два объекта в один невалидный кусок, если модель добавляла прозу
                        cands = re.findall(r"\{[^{}]*\}", out, re.DOTALL)
                        try:
                            pairs = json.loads(cands[-1]) if cands else {}
                        except ValueError:
                            pairs = {}
                        for label, name in pairs.items():
                            name = str(name).strip().strip(".,!«»\"").capitalize()
                            # гвард «обращение ≠ говорящий»: если имя звучит ТОЛЬКО в
                            # репликах самой метки и это не самопредставление — отказ
                            # («Саш, ну а кто…» помечало говорящего Сашей — 21.07)
                            own_only = False
                            if name:
                                lines_with = [ln for ln in sample.splitlines()
                                              if name.lower() in ln.lower()]
                                # формат tail: «[HH:MM] Собеседник N: текст» — метка
                                # НЕ в начале строки, старый startswith(label+":") был мёртв
                                own = [ln for ln in lines_with
                                       if re.search(rf"\]\s*{re.escape(label)}:", ln)]
                                intro = re.search(
                                    rf"(это|я|меня зовут)\s+{re.escape(name)}", sample, re.I)
                                own_only = bool(lines_with) and len(own) == len(lines_with) \
                                    and not intro
                            if (label in labels and name and name.replace("-", "").isalpha()
                                    and 3 <= len(name) <= 15 and name.lower() != owner_name
                                    and name.lower() in sample.lower() and not own_only
                                    and name not in renamed.values()):
                                renamed[label] = name
                                tr.rename_speaker(label, name)
                                for vid, vname in list(voice_names.items()):
                                    if vname == label:
                                        voice_names[vid] = name
                                emit({"type": "rename", "from": label, "to": name})
                                emit({"type": "status", "text": f"👤 {label} → {name}"})
                elif not named:
                    out = "".join(llm.stream(
                        f"Стенограмма встречи:\n{sample}\n\n"
                        "С той стороны говорит ОДИН человек? Если да и его имя явно "
                        "прозвучало (представился или к нему обращались) — ответь ТОЛЬКО "
                        "именем, одним словом. Если людей несколько или имя не звучало — "
                        "ответь ровно NONE.",
                        model=llm.small,
                        system="Ты определяешь имя говорящего по стенограмме. Одно слово или NONE.",
                    ))
                    name = out.strip().split()[0].strip(".,!«»\"") if out.strip() else ""
                    if (name and name.upper() != "NONE" and name.lower() != owner_name
                            and name.replace("-", "").isalpha() and 2 <= len(name) <= 15):
                        tr.rename_speaker("Собеседник", name.capitalize())
                        emit({"type": "rename", "from": "Собеседник", "to": name.capitalize()})
                        emit({"type": "status", "text": f"👤 Собеседник опознан: {name.capitalize()}"})
                        named = True
                        continue
                out = "".join(llm.stream(
                    f"Стенограмма встречи:\n{sample}\n\n"
                    "Перечисли ИМЕНА людей, которые реально звучали в разговоре "
                    "(участники, к кому обращались, кто упоминался как присутствующий). "
                    "Только имена через запятую, без пояснений. Если имён не было — NONE.",
                    model=llm.small,
                    system="Ты извлекаешь имена из стенограммы. Только список через запятую или NONE.",
                ))
                raw = out.strip().splitlines()[0] if out.strip() else ""
                if raw and "NONE" not in raw.upper():
                    names = [n.strip(" .«»\"") for n in raw.split(",")]
                    names = [n for n in names if n and n.replace("-", "").replace(" ", "").isalpha()
                             and 2 <= len(n) <= 25][:12]
                    if names and set(names) != set(listed):
                        listed = names
                        tr.set_participants(names)
                        emit({"type": "status", "text": f"👥 Звучали: {', '.join(names)}"})
            except Exception:  # noqa: BLE001
                pass

    def minutes_loop():
        """Живые минутки: черновик _minutes.md дорабатывается по ходу встречи.

        Идёт на лёгкой модели ПАРАЛЛЕЛЬНО подсказкам (другая модель Ollama).
        Финальную версию делает кнопка «Протокол» (26b).
        """
        seen = 0
        mpath = tr.path.with_name(tr.path.stem + "_minutes.md")
        while not stop.is_set():
            time.sleep(150)
            # кнопка «Протокол» пишет ФИНАЛЬНЫЕ минутки (26b) без маркера черновика —
            # авточерновик лёгкой модели не должен их затирать
            if mpath.exists() and not mpath.read_text(encoding="utf-8").startswith("<!-- черновик"):
                continue
            full = tr.full()
            if len(full) - seen < 400:
                continue
            seen = len(full)
            try:
                out = "".join(
                    llm.stream(
                        f"Стенограмма встречи (идёт, реплики по спикерам):\n\n{full}\n\n"
                        "Обнови ЧЕРНОВИК минуток (markdown): участники (из контекста), "
                        "темы, решения, поручения списком «- **Кто** — что — срок», "
                        "открытые вопросы. Только факты.",
                        model=cfg["sufler"].get("think_model", llm.small),
                        system="Ты секретарь встречи. Черновик минуток по-русски, сухо. "
                               "БЕЗ markdown-таблиц (|…|) — только списки «- …»: "
                               "таблицы нечитаемы в plain-тексте.",
                    )
                )
                if out.strip():
                    mpath.write_text("<!-- черновик, встреча идёт -->\n" + out, encoding="utf-8")
                    emit({"type": "status", "text": f"🗒 минутки-черновик обновлены ({dt.datetime.now():%H:%M})"})
            except Exception as e:  # noqa: BLE001
                emit({"type": "status", "text": f"минутки: {e}"})

    def deep_loop():
        """Глубокая проработка: 26b пересматривает заметки быстрой модели.

        Раз в ~5 минут: подтверждает/уточняет/отбрасывает 📌💎💭 от e4b,
        связывает с памятью графа, выдаёт до 5 строк «🔬 …».
        """
        seen_notes = 0
        while not stop.is_set():
            time.sleep(600 if quiet else 300)  # в тихом режиме 26b фоном — реже
            if not toggles["theses"]:
                continue
            notes = tr.notes()
            if len(notes) - seen_notes < 3:
                continue  # мало новых заметок — глубокому нечего пересматривать
            seen_notes = len(notes)
            if manual_evt.is_set():
                continue  # ручной запрос ждёт lock — не занимаем 26b на минуту
            with hint_lock:  # 26b — не сталкиваться с подсказчиком
                try:
                    out = ""
                    for tok in llm.stream(
                        f"Хвост стенограммы:\n{tr.tail(4000)}\n\n"
                        f"Заметки быстрой модели (сырые):\n" + "\n".join(notes[-20:]) + "\n\n"
                        "Пересмотри глубоко: подтверди главное, отбрось шум, найди связи "
                        "с памятью прошлых встреч, стратегические следствия. "
                        "До 5 строк, каждая с префиксом «🔬 ». Если добавить нечего — NONE.",
                        system=llm.system,
                        think=True,  # глубокому контуру думать положено (раз в ~10 мин)
                    ):
                        if manual_evt.is_set():
                            break  # ⌘⏎ во время deep — уступаем, не держим lock
                        out += tok
                    for line in out.strip().splitlines():
                        line = line.strip()
                        if line and line != "NONE" and line.startswith("🔬"):
                            emit({"type": "thesis", "text": line})
                            tr.note(line)
                except Exception as e:  # noqa: BLE001
                    emit({"type": "status", "text": f"глубокий контур: {e}"})

    def gen_answer(question: str):
        """Вопрос пользователя из UI: ответ по живой стенограмме + графу/vault.

        Скорость — приоритет: владелец спрашивает ПОСРЕДИ встречи. Отклик в панель
        мгновенно (до lock), vault не дольше 2.5с, ответ каплен 220 токенами
        на лёгкой модели — итого первые слова ~1-2с, полный ответ ~4-6с.
        """
        emit({"type": "hint", "text": f"\n\n❓ {question}\n"})
        manual_evt.set()  # авто-контуры уступают
        with hint_lock:
            manual_evt.clear()
            extra = ""
            try:  # граф и документы через brain Чароита (если поднят)
                import requests as _rq
                v = _rq.post("http://127.0.0.1:8100/vault_search",
                             json={"query": question, "limit": 4}, timeout=2.5).json().get("text", "")
                if v and "не найдено" not in v.lower():
                    extra = "\n\nИз графа и документов (vault):\n" + v[:2000]
            except Exception:  # noqa: BLE001
                pass
            parts: list[str] = []
            try:
                for tok in llm.stream(
                    f"=== ИСТОЧНИК 1: живая стенограмма ТЕКУЩЕЙ встречи (хвост) ===\n"
                    f"{tr.tail(3000)}\n"
                    f"{'=== ИСТОЧНИК 2: память прошлых встреч и документы ===' + extra if extra else ''}\n\n"
                    f"Вопрос пользователя: {question}\n"
                    "Приоритет источников СТРОГО: 1) сначала ищи ответ в ТЕКУЩЕЙ "
                    "стенограмме — если он там есть, отвечай только по ней; 2) нет в "
                    "стенограмме — возьми из памяти и документов; 3) нет нигде — "
                    "ответь из общих знаний с пометкой «(из общих знаний)». "
                    "Кратко, по-русски, не выдумывай.",
                    model=llm.small,
                    num_predict=220,
                ):
                    emit({"type": "hint", "text": tok})
                    parts.append(tok)
            except Exception as e:  # noqa: BLE001
                emit({"type": "hint", "text": f"[LLM: {e}]"})
            emit({"type": "hint_done"})
            if parts:
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ❓ {question}", "".join(parts))

    def _do_summary():
        with hint_lock:
            chunks: list[str] = []
            try:
                for tok in llm.minutes(tr.full() or "(пусто)"):
                    chunks.append(tok)
                    emit({"type": "hint", "text": tok})
            except Exception as e:  # noqa: BLE001
                emit({"type": "hint", "text": f"[LLM: {e}]"})
            emit({"type": "hint_done"})
            if chunks:  # минутки — отдельным файлом рядом со стенограммой
                mpath = tr.path.with_name(tr.path.stem + "_minutes.md")
                mpath.write_text("".join(chunks), encoding="utf-8")
                emit({"type": "status", "text": f"Минутки: {mpath}"})

    def stdin_loop():
        # команды — в отдельных потоках: синхронная генерация (минутки 26b — минуты!)
        # блокировала чтение stop → Swift терминейтил демона без finally (потеря графа)
        for raw in sys.stdin:
            cmd = raw.strip().lower()
            if cmd == "stop":
                stop.set()
                return
            if cmd == "hint":
                threading.Thread(target=gen_hint, kwargs={"manual": True}, daemon=True).start()
            elif cmd.startswith("ask "):
                q = raw.strip()[4:].strip()
                if q:
                    threading.Thread(target=gen_answer, args=(q,), daemon=True).start()
            elif cmd == "cloud":
                cloud_evt.set()  # ручной запрос облачного ответа
            elif cmd == "summary":
                threading.Thread(target=_do_summary, daemon=True).start()
            elif cmd.startswith("set "):
                parts = cmd.split()
                if len(parts) == 3 and parts[1] in toggles and parts[2] in ("on", "off"):
                    toggles[parts[1]] = parts[2] == "on"
                    ru = {"hints": "подсказки", "theses": "тезисы", "cloud": "Claude"}
                    state = "включены" if toggles[parts[1]] else "выключены"
                    emit({"type": "status", "text": f"⚙️ {ru[parts[1]]} {state}"})
        stop.set()  # stdin закрылся — родитель умер

    threads = [threading.Thread(target=f, daemon=True) for f in (
        stt_loop, think_loop, auto_hint_loop, instant_loop, cloud_loop,
        fast_trigger_loop, deja_vu_loop, dialog_markup_loop, name_loop,
        minutes_loop, deep_loop, stdin_loop,
    )]
    for t in threads:
        t.start()
    try:
        last_hb = 0.0
        while not stop.is_set():
            time.sleep(0.3)
            # heartbeat для watchdog UI: главный тред жив → hb каждые 30с;
            # тишина 100с при живом процессе = зависание, UI перезапустит демон
            if time.time() - last_hb > 30:
                last_hb = time.time()
                emit({"type": "hb"})
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        # Пересборка финальной стенограммы + граф — ПЕРВЫМ делом (Popen мгновенен,
        # живёт в своей сессии и переживает terminate от Swift; часовая встреча
        # 17.07 потерялась именно на этом). rebuild сам ждёт финализацию записей
        # (до 45с, при SIGKILL демона добивает .pcm сам) и по завершении зовёт
        # graph_updater по уже ЧИСТОЙ стенограмме; записей нет — просто граф по живой.
        # NB: никаких локальных `import subprocess` здесь — локальный импорт в main()
        # делает имя локальным для ВСЕГО скоупа и ломает cloud_loop (NameError).
        try:
            # живые данные — пересборке: сколько голосов реально звучало и какие
            # имена опознаны за встречу. Без них rebuild кластеризует аудио с нуля
            # (21.07: живьём 8 голосов и 4 имени → в финале 14 безымянных) и
            # заново гадает имена, выбрасывая всё, что демон выяснил за час.
            pathlib.Path(str(tr.path) + ".live.json").write_text(
                json.dumps({"speakers": len(voice_names), "names": tr.names()},
                           ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001 — подсказка вспомогательна, не рушим финал
            pass
        try:
            gstamp = pathlib.Path(tr.path).stem[:15]
            glog = open(ROOT / "logs" / f"graph_{gstamp}.log", "w")  # не DEVNULL: молчаливые падения графа
            subprocess.Popen(
                ["nice", "-n", "10", sys.executable,
                 str(pathlib.Path(__file__).parent / "rebuild_transcript.py"), str(tr.path)],
                start_new_session=True, stdout=glog, stderr=subprocess.STDOUT,
            )
            emit({"type": "status", "text": "Финальная стенограмма и граф: фоном (~2-4 мин)"})
        except Exception:
            pass
        hub.stop()  # финализирует записи .pcm → .wav — их и ждёт rebuild
        emit({"type": "status", "text": f"Стенограмма: {tr.path}"})


if __name__ == "__main__":
    main()
