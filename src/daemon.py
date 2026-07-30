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
from collections import deque

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import requests  # noqa: E402
import yaml  # noqa: E402

import action_items  # noqa: E402
import fact_check  # noqa: E402
import privacy  # noqa: E402
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
    """Есть ли в реплике вопрос — не обязательно в самом конце.

    Замер по 1989 репликам (23.07): 250 реплик содержали «?» НЕ последним
    символом, и 237 из них прежний детектор пропускал — 43% всех вопросов
    встречи оставались без подсказки. Живая речь не заканчивается вопросом:
    «Да, есть. Можно я вопросик задам? Всем привет» — знак в середине, дальше
    обычный текст. STT (GigaAM) ставит «?» по интонации, поэтому наличие знака
    где угодно в реплике — надёжный и мгновенный признак.

    Спорный случай один: реплика начинается вопросным словом, но знака нет
    («когда мы вставляли партицию…» — придаточное, не вопрос). Раньше здесь
    рос чёрный список «как бы / когда мы / что бы», что прямо запрещено
    правилом проекта (никакого хардкода паттернов). Замер показал: таких
    случаев ~1 на встречу — решает их модель через ask_question_model(),
    вызывается редко и латентность некритична.
    """
    if "?" in text:
        return True
    words = text.strip().lower().split()
    if not words:
        return False
    starts_like_q = words[0] in _Q_START or " ".join(words[:2]) in _Q_PAIRS
    if not starts_like_q:
        return False
    # спорно: вопросное слово без «?». Спрашиваем модель, а не список паразитов.
    return ask_question_model(text)


def ask_question_model(text: str) -> bool:
    """Спорную реплику (вопросное слово, но без «?») классифицирует модель.

    AI-first вместо чёрного списка союзов: модель понимает, что «когда мы
    вставляли» — придаточное, а «когда релиз» — вопрос. Лёгкая qwen3.5:4b,
    num_predict 3, температура 0 — ответ за ~0.4с. Сеть недоступна или таймаут
    — консервативно считаем вопросом (лучше лишняя подсказка, чем пропуск).
    """
    try:
        r = requests.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": "qwen3.5:4b", "stream": False, "think": False,
                "options": {"num_ctx": 2048, "num_predict": 3, "temperature": 0},
                "messages": [
                    {"role": "system", "content":
                     "Реплика с рабочей встречи начинается с вопросного слова, но без «?». "
                     "Это настоящий ВОПРОС, на который собеседник ждёт ответа, "
                     "или придаточное предложение внутри утверждения?\n"
                     "«Когда мы вставляли партицию, были ключи» — придаточное, ответь: нет.\n"
                     "«Когда релиз» — вопрос, ответь: да.\n"
                     "Ответь одним словом: да или нет."},
                    {"role": "user", "content": text},
                ],
            },
            timeout=5,
        )
        ans = r.json().get("message", {}).get("content", "").strip().lower()
        return ans.startswith("да")
    except Exception:  # noqa: BLE001 — модель недоступна: не глотаем вопрос
        return True

_out_lock = threading.Lock()
# Общий стоп: emit ставит его, когда приложение закрыло свой конец пайпа.
# Ссылка на событие main появляется при старте — до неё emit просто молчит.
_stop_event: threading.Event | None = None


def emit(obj: dict):
    """Событие в приложение. Смерть читателя — повод завершиться штатно.

    Раньше исключение отсюда летело в вызывающий поток. Если это был stt_loop
    (единственное место, где emit стоял вне try), поток умирал молча: главный
    цикл продолжал слать hb, watchdog приложения видел «демон жив», а
    транскрипция стояла. Ровно этот профиль наблюдался 20.07 — «встреча шла,
    транскрипция молча стояла 20 минут».
    """
    try:
        with _out_lock:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        # Приложение закрылось или перезапустилось: доигрывать некому.
        # Ставим общий стоп — main дойдёт до finally и сохранит встречу.
        if _stop_event is not None:
            _stop_event.set()


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


def start_brief(cfg: dict) -> str:
    """Компактный бриф последней встречи для стартовой карточки подсказок.

    Файловый парс без моделей: старт должен быть мгновенным. Только основной
    граф — соседние сферы vault (личные) в рабочий бриф не подмешиваются.
    """
    gdir = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser()
    meetings = sorted((gdir / "Встречи").glob("*.md")) if (gdir / "Встречи").exists() else []
    if not meetings:
        return ""
    last = meetings[-1]
    text = last.read_text(encoding="utf-8", errors="ignore")
    title = last.stem
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    lines: list[str] = [f"📚 Прошлая встреча: {title}"]
    if "## Решения" in text:
        sec = text.split("## Решения", 1)[1].split("\n## ", 1)[0]
        dots = [ln.strip() for ln in sec.splitlines() if ln.strip().startswith("- ")][:4]
        if dots:
            lines.append("Договорились:")
            lines += [f"  {d}" for d in dots]
    open_tasks = text.count("- [ ]")
    if open_tasks:
        lines.append(f"Открытых задач: {open_tasks}")
    return "\n".join(lines)[:900] + "\n"


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


def _prune_graph_logs(cfg: dict) -> None:
    """Логи графа стареют по тому же сроку, что и записи.

    На каждую встречу создавался logs/graph_<штамп>.log, и не удалял их никто.
    В них попадают имена участников, названия ядер и куски цитат — то есть
    содержимое встреч, на которое ретеншн record_keep_days не распространялся.
    За год это тысячи файлов с личными данными в каталоге, про который никто
    не помнит.
    """
    logs = ROOT / "logs"
    if not logs.is_dir():
        return
    keep_days = float(cfg.get("audio", {}).get("record_keep_days", 2))
    cutoff = time.time() - max(keep_days, 1) * 86400
    for old in logs.glob("graph_*.log"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def _recover_orphans(cfg: dict, current_stamp: str) -> None:
    """Добить встречи, оборванные аварийно.

    SIGKILL (watchdog приложения на 12-й секунде, OOM, паника) не исполняет
    finally — значит rebuild_transcript не запускался, и остаются сырые .pcm
    без стенограммы, минуток, графа и архивной папки. Ни один старт и ни одна
    ночная джоба этого не замечали, а через record_keep_days запись удалялась
    вместе с последним шансом восстановить встречу.

    Здесь мы, наоборот, запускаем пересборку для каждой чужой записи —
    ровно то, что сделал бы штатный стоп.
    """
    _prune_graph_logs(cfg)
    rec_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    if not rec_dir.is_dir():
        return
    stamps = sorted({p.stem.rsplit("_", 1)[0] for p in rec_dir.glob("*.pcm")})
    for stamp in stamps:
        if stamp == current_stamp:
            continue                      # наша встреча, она только началась
        live = tdir / f"{stamp}.md"
        if not live.exists():
            continue                      # без стенограммы пересобирать нечего
        emit({"type": "status",
              "text": f"Догоняю прерванную встречу {stamp} — пересборка фоном"})
        subprocess.Popen(
            ["nice", "-n", "10", sys.executable,
             str(ROOT / "src" / "rebuild_transcript.py"), str(live)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )


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
    # env-override для тестов: стенограммы в песочницу, не в боевую папку
    tdir = os.environ.get("SUFLER_TRANSCRIPTS_DIR")
    tr = Transcript(pathlib.Path(tdir) if tdir else ROOT / cfg["log"]["transcripts_dir"])
    # Штамп записи — тот же, что у стенограммы: rebuild_transcript ищет .wav
    # по имени .md, и разъехавшиеся на границе минуты штампы означали молча
    # пропущенную финальную пересборку.
    hub = AudioHub(cfg, stamp=tr.stamp)
    hub.on_status = lambda t: emit({"type": "status", "text": t})
    # Встречи, оборванные аварийно, добиваем ДО чистки — иначе ретеншн
    # удалит единственную запись раньше, чем кто-то её пересоберёт.
    _recover_orphans(cfg, tr.stamp)
    # Ретеншн аудио не должен зависеть от того, началась ли новая встреча:
    # раньше чистка жила внутри _open_sinks, поэтому при record: false или
    # недельном простое записи лежали дольше обещанного в PRIVACY.
    try:
        AudioHub.prune_recordings(
            ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings"),
            cfg["audio"].get("record_keep_days", 2),
        )
    except Exception as e:  # noqa: BLE001 — уборка не повод не начать встречу
        print(f"чистка записей: {e}", file=sys.stderr, flush=True)
    system_base = llm.system   # без памяти: живой контекст пересобирает поверх
    graph_ctx = load_graph_context(cfg)
    if graph_ctx:
        llm.system += f"\n\nПамять прошлых встреч (из графа проекта):\n{graph_ctx}"
        emit({"type": "status", "text": f"Граф подключён к промптам ({len(graph_ctx)} зн. памяти)"})
    # архив ГЛАЗАМИ с первой секунды: карточка подсказок пустует до первой
    # генерации — кладём туда бриф последней встречи (тема, решения, задачи)
    brief = start_brief(cfg)
    if brief:
        emit({"type": "hint", "text": brief})
        emit({"type": "hint_done"})
        append_hint(tr.path, "стартовый бриф (архив)", brief)   # аудит: бриф был
    # канон имён: узлы Люди/ графа — чтобы «Андрюха/Света/Полин» подписывались
    # каноничной формой, а не плодили дубли узлов
    _people_dir = pathlib.Path(str(cfg["sufler"].get("graph_dir", ""))).expanduser() / "Люди"
    known_people = sorted(q.stem for q in _people_dir.glob("*.md")) if _people_dir.exists() else []
    known_first = sorted({n.split()[0] for n in known_people if n and not n.startswith("Собеседник")})
    threading.Thread(target=llm.warmup, daemon=True).start()
    emit({"type": "status", "text": f"Слушаю: {' + '.join(hub.sources)} · LLM: {llm.resolve_model()}"})

    stop = threading.Event()
    global _stop_event
    _stop_event = stop          # emit сможет остановить нас при обрыве пайпа
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

    # Дедуп тезисов по СМЫСЛУ. Модель каждые 40с смотрит свежий фрагмент и на
    # длинной встрече переоткрывает уже сказанное другими словами — контекст в
    # 800 знаков этого не лечит. Подстрочное сравнение дубли-перефразы не
    # ловит, эмбеддинги путают «согласован»/«не согласован», поэтому NLI:
    # дубль = двустороннее следование (nli.py, ONNX, без torch). Дорогой
    # инференс (~0.2с) прикрыт дешёвым difflib-префильтром — в NLI идут только
    # словесно похожие кандидаты. Нет модели на диске — дедуп молча выключен.
    recent_theses: deque[str] = deque(maxlen=16)

    def is_dup_thesis(line: str) -> bool:
        import difflib as _dl

        import nli
        if not nli.is_available():
            return False
        text = line.lstrip("📌💎💭 ").strip()
        if len(text) < 12:
            return False
        words = text.lower().split()
        for prev in reversed(recent_theses):
            # 0.3, не выше: перефраз с аббревиатурами («2 RPS» ↔ «2 запроса в
            # секунду») словесно далёк, и жадный порог отрезал бы его до NLI
            if _dl.SequenceMatcher(None, words, prev.lower().split()).ratio() < 0.3:
                continue  # совсем далёкие пары NLI не беспокоят
            # 0.8 — осторожный режим: лучше редкий повтор в ленте, чем
            # потерянный тезис с новым фактом
            if nli.is_duplicate(text, prev, threshold=0.8):
                print(f"тезис-дубль отсеян: «{text[:80]}» ≈ «{prev[:80]}»", flush=True)
                return True
        recent_theses.append(text)
        return False

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
                    if line.startswith(("📌", "💎", "💭")):
                        if is_dup_thesis(line):
                            continue
                        emit({"type": "thesis", "text": line})
                        tr.note(line)
                    else:
                        emit({"type": "thesis", "text": line})
            except Exception as e:  # noqa: BLE001
                emit({"type": "status", "text": f"мышление: {e}"})

    hint_lock = threading.Lock()   # подсказки/минутки на 26b — по одной за раз
    manual_evt = threading.Event()  # ручной запрос прерывает авто-генерацию
    max_ctx = int(cfg["llm"]["max_context_chars"])
    quiet = bool(cfg["sufler"].get("quiet", True))
    instant_on = bool(cfg["sufler"].get("instant", True))
    auto_model = llm.small if quiet else None  # тихий режим: весь фон без 26b
    instant_evt = threading.Event()
    cloud_live = privacy.cloud_live_enabled(cfg)  # молчание конфига = «нет», см. src/privacy.py
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

    _hint_gen = [0]   # поколение подсказки: облачное уточнение к устаревшей — в файл, не в UI

    def gen_hint(header: str | None = None, manual: bool = False, model: str | None = None):
        if manual:
            manual_evt.set()  # сигнал авто-генерации уступить
        _hint_gen[0] += 1
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
            if parts and not parts[-1].endswith("⏸"):
                _cloud_refine_hint(tail, "".join(parts))

    _refine_last = {"len": 0}

    def _cloud_refine_hint(tail: str, local_hint: str):
        """Лестница и для подсказок: локальная мгновенно → Haiku доуточняет.

        Уточнение падает в облачную ленту того же окна (тот же путь, что
        ответы на вопросы) — hint-карточку перезапишет следующая подсказка,
        а лента остаётся. Выключатель отдельный от cloud_live: подсказки
        стреляют часто, и это постоянный поток стенограммы в облако.
        """
        if not (cfg["sufler"].get("cloud_hints", False)
                and cloud_live and toggles["cloud"]):
            return
        if len(tail) - _refine_last["len"] < 400:
            return   # разговор не набежал — Haiku скажет то же самое
        _refine_last["len"] = len(tail)

        def cloud_hint_refine():
            claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
            model = cfg["sufler"].get("cloud_hints_model", "claude-haiku-4-5")
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            env.update(load_claude_proxy_env())
            short = model.split("-")[1] if model.count("-") else model
            my_gen = _hint_gen[0]
            try:
                r = subprocess.run(
                    [claude_bin, "-p",
                     "Рабочая встреча. Последние реплики:\n" + tail + "\n\n"
                     "Локальная модель уже дала подсказку:\n" + local_hint[:1200] +
                     "\n\nДай УТОЧНЕНИЕ: 2-4 коротких пункта — что локальная "
                     "подсказка упустила или где неточна. Только новое, не "
                     "пересказывай её. ЧЕСТНОСТЬ ВАЖНЕЕ УВЕРЕННОСТИ: факты "
                     "встречи бери только из реплик. Русский, без преамбул.",
                     "--model", model],
                    capture_output=True, text=True, timeout=60, env=env)
                out = (r.stdout or "").strip()
            except Exception as e:  # noqa: BLE001
                out = f"[{short}: {e}]"
            if not out:
                return
            append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] уточнение ({short})", out)
            # подсказки и облако — ЕДИНЫЙ поток: уточнение дописывается в ту же
            # карточку. Если пользователь уже запросил новую подсказку (ручной
            # сброс буфера) — устаревшее уточнение остаётся только в файле
            if _hint_gen[0] == my_gen:
                emit({"type": "hint", "text": f"\n\n☁️ {short}: {out}"})
                emit({"type": "hint_done"})

        threading.Thread(target=cloud_hint_refine, daemon=True).start()

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

        Выключатель спрашиваем ЗДЕСЬ, а не только у вызывающего: сюда ведут
        две дороги — авто-детект вопроса (fire_question) и ручная команда
        `cloud` из stdin (кнопка «Claude», ⌘⇧⏎). Вторая проверку обходила,
        и нажатие отправляло стенограмму при cloud_live: false.
        """
        claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
        model = cfg["sufler"].get("cloud_live_model", "claude-sonnet-5")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update(load_claude_proxy_env())  # без прокси из GUI-запуска — 403 по региону
        while not stop.is_set():
            if not cloud_evt.wait(timeout=0.5):
                continue
            cloud_evt.clear()
            if not cloud_live:
                # молча отказать — значит оставить человека гадать, почему кнопка
                # не работает; поэтому статус, а не пустой continue
                emit({"type": "status", "text": "облако выключено: sufler.cloud_live"})
                continue
            if not toggles["cloud"]:
                emit({"type": "status", "text": "облако выключено тумблером"})
                continue
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
                     # Контекст в промпте — ТОЛЬКО роль, без тем. Раньше здесь был
                     # перечень рабочих тем — и на тонкой стенограмме (начало
                     # встречи, «можно начинать?») модель брала повестку ИЗ ПРОМПТА
                     # и уверенно выдавала её как реальную повестку встречи.
                     "Рабочая встреча, пользователь владелец — техлид. "
                     "Последние реплики:\n" + tail + "\n\n"
                     "Собеседник задал вопрос (последняя реплика). Дай владельцу ГОТОВЫЙ ответ "
                     "от первого лица: 3-5 предложений, по делу, по-русски. "
                     "ЧЕСТНОСТЬ ВАЖНЕЕ УВЕРЕННОСТИ: отвечай только тем, что следует из "
                     "стенограммы или общих знаний; конкретные факты встречи (повестку, "
                     "цифры, статусы задач) НЕ выдумывай — если их нет в репликах, так и "
                     "скажи («по повестке уточню») или отвечай без них. "
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
        # Авто-бриф в начале встречи: как только по первым репликам понятна
        # тема — один раз вытащить контекст из архива (топ-ядра: статус +
        # когда обсуждалось). Собирается из ГОТОВЫХ строк файлов, без LLM:
        # мгновенно и нечему галлюцинировать. Окно попыток — до 3000 знаков
        # стенограммы: small talk в начале сигнала не даёт, дальше тему
        # подхватит обычное дежавю.
        brief_on = bool(cfg["sufler"].get("meeting_brief", True))
        brief_done = not brief_on
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

                if not brief_done and len(full) >= 600:
                    # порог мягче обычного (margin/2): бриф — обзор, не точечное
                    # «уже обсуждалось», и в начале встречи сигнал ещё слабый
                    picks = [(s, p) for s, p in scored[:2]
                             if s - mid >= margin / 2 and p.stem not in shown]
                    if picks:
                        brief_done = True
                        lines = ["⏮ Контекст к встрече из архива:"]
                        for _s, p in picks:
                            btxt = p.read_text(encoding="utf-8")
                            bm = re.search(r"## Статус\n(.+)", btxt)
                            bst = re.sub(r"_\(.*?\)_", "", bm.group(1)).strip() if bm else ""
                            bdates = sorted({d for d in re.findall(
                                r"\[\[Встречи/(\d{4}-\d{2}-\d{2})_\d{4}", btxt)})
                            bwhen = ", ".join(d[8:10] + "." + d[5:7] for d in bdates[-2:]) or "ранее"
                            lines.append(f"• {p.stem} — {bst or 'без статуса'} (обсуждалось {bwhen})")
                            shown.add(p.stem)
                        line = "\n".join(lines)
                        emit({"type": "thesis", "text": line})
                        tr.note(line)
                        continue
                    if len(full) > 3000:
                        brief_done = True  # тема так и не совпала с архивом — молчим

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
                            "говорит НЕ Ольга). Говорящий получает имя только если: "
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
                            if name and known_first and name not in known_first:
                                pref = name.casefold()[:4]
                                hit = [k for k in known_first if k.casefold().startswith(pref)]
                                if len(hit) == 1:
                                    name = hit[0]   # Полин→Полина, Андрюх→Андрей
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
                # folder: искать в ГРАФЕ проекта, не по всему Obsidian-vault —
                # соседние личные папки не должны попадать в ответы на встрече
                _folder = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser().name
                v = _rq.post("http://127.0.0.1:8100/vault_search",
                             json={"query": question, "limit": 4, "folder": _folder,
                                   "snippet_chars": 600}, timeout=2.5).json().get("text", "")
                if v and "не найдено" not in v.lower():
                    # «⚠» — гейт уверенности brain: совпадения слабые, модель
                    # обязана честно сказать «в архиве нет», а не сочинять
                    if v.startswith("⚠"):
                        extra = ("\n\nИз графа и документов (vault) — СОВПАДЕНИЯ "
                                 "СЛАБЫЕ, скорее всего в архиве ответа нет:\n" + v[:2000])
                    else:
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
                # сверка номеров задач и дат со стенограммой: выдуманный
                # номер внешне неотличим от настоящего, но в тексте его нет
                doc = fact_check.annotate("".join(chunks), tr.full())
                # Поручения — в формат, который видит окно «Задачи». Модель
                # формулирует их правильно (имя, суть, срок), но заворачивает
                # в свой markdown и теряет скобки чекбокса: на рабочем графе
                # из 138 файлов минуток чекбоксы нашлись в двух, и окно задач
                # стояло пустым при живых поручениях на каждой встрече.
                doc = action_items.normalize(doc)
                mpath.write_text(doc, encoding="utf-8")
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

    def live_context_loop():
        """Архив подтягивается ПО ТЕМЕ идущей встречи, а не «две последние».

        Стартовый load_graph_context слеп: тема встречи проявляется в первые
        минуты разговора. Здесь: хвост стенограммы → small-модель выжимает
        тему и термины → vault_search по графу → блок «Память прошлых
        встреч» в системном промпте пересобирается. Подсказки, instant и
        ответы на вопросы начинают видеть старые договорённости по теме.
        """
        if not cfg["sufler"].get("live_context", True):
            return
        interval = int(cfg["sufler"].get("live_context_interval", 600))
        seen_bytes = 0
        first = True
        while not stop.is_set():
            stop.wait(75 if first else interval)
            if stop.is_set():
                return
            first = False
            tail = tr.tail(2500)
            if len(tail) < 400:
                continue
            try:  # прирост стенограммы мал — тема не менялась, не дёргаемся
                size = tr.path.stat().st_size
            except OSError:
                continue
            if size - seen_bytes < 1500:
                continue
            seen_bytes = size
            query = ""
            try:
                with hint_lock:   # не толкаться с подсказкой на одной модели
                    query = "".join(llm.stream(
                        "Стенограмма идущей встречи (хвост):\n\n" + tail +
                        "\n\nНазови тему встречи и 6-8 ключевых терминов, "
                        "систем, имён. ОДНОЙ строкой, через запятую, без "
                        "пояснений.",
                        model=llm.small,
                        system="Ты выжимаешь поисковый запрос из стенограммы. "
                               "Отвечай одной короткой строкой.")).strip()[:300]
            except Exception:  # noqa: BLE001
                pass
            if not query:
                query = tail[-400:]
            try:
                import requests as _rq
                _folder = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser().name
                v = _rq.post("http://127.0.0.1:8100/vault_search",
                             json={"query": query, "limit": 4, "folder": _folder,
                                   "snippet_chars": 500}, timeout=6).json().get("text", "")
            except Exception:  # noqa: BLE001
                continue   # brain лежит — остаёмся на стартовом контексте
            if not v or v.startswith("⚠") or "не найдено" in v.lower():
                continue   # запрос мимо архива — не портим то, что есть
            llm.system = (system_base +
                          "\n\nПамять прошлых встреч (подобрано по теме идущей "
                          "встречи; договорённости и решения оттуда можно "
                          "упоминать как прошлые):\n" + v[:2600])
            topic = query.split(",")[0][:60]
            emit({"type": "status", "text": f"🧠 Контекст по теме «{topic}»: архив подтянут"})

    threads = [threading.Thread(target=f, daemon=True) for f in (
        stt_loop, think_loop, auto_hint_loop, instant_loop, cloud_loop,
        fast_trigger_loop, deja_vu_loop, dialog_markup_loop, name_loop,
        minutes_loop, deep_loop, live_context_loop, stdin_loop,
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
