"""Суфлёр-демон для UI: события NDJSON в stdout, команды из stdin.

События:  {"type":"status","text":...} | {"type":"transcript","ts":"HH:MM:SS","text":...}
          {"type":"thesis","text":...}  | {"type":"hint","text":...,"manual":bool}
          {"type":"hint_done","manual":bool} — manual: стрим запрошен человеком
          (вопрос/⌘⏎/протокол), не авто-циклом; панель по нему решает, что
          вправе гасить карточку (старый UI поле просто не читает)
Команды (stdin, по строке): hint | summary | expand [тема] | stop
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
import stat
import subprocess
import sys
import threading
import time
from collections import deque

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402

import action_items  # noqa: E402
import autostop as autostop_rules  # noqa: E402
import cloud  # noqa: E402
import install_profile  # noqa: E402
import fact_check  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402
from meeting_thread import Thread as MeetingThread  # noqa: E402
import privacy  # noqa: E402
import question_filter  # noqa: E402
import speaker_names  # noqa: E402
import thesis_rules  # noqa: E402
import voice_pitch  # noqa: E402
from audio import AudioHub  # noqa: E402
from llm import LLM, embed as llm_embed  # noqa: E402
from main import NOISE, Transcript  # noqa: E402
from stt import STT  # noqa: E402

import meeting_stamp  # noqa: E402

from charoite_paths import (
    code_root,
    harden_existing,
    harden_umask,
    resolve_root,
    secure_dir,
)

ROOT = resolve_root(__file__)      # данные пользователя
CODE = code_root(__file__)         # src/ и scripts/ — рядом с этим файлом
THESIS_EVERY = 40.0     # автотезисы: раз в N секунд по новым фразам
HINT_EVERY = 75.0       # автоподсказки: не чаще, чем раз в N секунд
HINT_MIN_NEW = 220      # и только если накопилось столько новых знаков разговора
HINT_RETRY = 20.0       # сорвалась (модель занята) — следующая попытка раньше, а не через 75 с
THREAD_TICK = 30.0      # нить встречи: как часто СМОТРИМ, набежал ли разговор
THREAD_MIN_NEW = 900    # и сколько новых знаков нужно, чтобы позвать модель

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


# Клиент модели для классификатора спорных реплик. Ставит main(): до него
# консервативный ответ «вопрос» (см. ask_question_model).
_llm_client: LLM | None = None


def ask_question_model(text: str) -> bool:
    """Спорную реплику (вопросное слово, но без «?») классифицирует модель.

    AI-first вместо чёрного списка союзов: модель понимает, что «когда мы
    вставляли» — придаточное, а «когда релиз» — вопрос. Лёгкая модель из
    конфига (llm.small_model), num_predict 3, температура 0 — ответ за ~0.4с.
    Сеть недоступна или таймаут — консервативно считаем вопросом (лучше
    лишняя подсказка, чем пропуск). До main() клиента ещё нет — тот же
    консервативный ответ.
    """
    if _llm_client is None:
        return True
    try:
        ans = _llm_client.complete(
            text,
            system=(
                "Реплика с рабочей встречи начинается с вопросного слова, но без «?». "
                "Это настоящий ВОПРОС, на который собеседник ждёт ответа, "
                "или придаточное предложение внутри утверждения?\n"
                "«Когда мы вставляли партицию, были ключи» — придаточное, ответь: нет.\n"
                "«Когда релиз» — вопрос, ответь: да.\n"
                "Ответь одним словом: да или нет."),
            model=_llm_client.small, think=False,
            num_ctx=2048, num_predict=3, temperature=0,
            timeout=5, busy_wait=0)   # горячий путь STT: занята — сразу «вопрос», не ждём
        return ans.lower().startswith("да")
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


def quiet_loop_warn(tag: str):
    """Голос для вспомогательного контура, который раньше молчал.

    deja_vu/разметка/имена глотали ЛЮБОЕ исключение через pass: отказ
    name_loop (имена не появятся всю встречу) снаружи неотличим от «имён
    не звучало» — тот же класс тихой деградации, что чинили в пересборке
    (names_pending, аудит 14.08). Совсем убрать except нельзя — контуры
    вспомогательные и не должны ронять встречу; поэтому статус. Эмитим при
    СМЕНЕ текста ошибки, а не каждый проход 40-90-секундного цикла: одна
    и та же беда (модель встала) сыпала бы статусами до конца встречи.
    """
    last: dict[str, str | None] = {"msg": None}

    def warn(e: Exception) -> None:
        msg = f"{tag}: {type(e).__name__}: {e}"
        if msg != last["msg"]:
            last["msg"] = msg
            emit({"type": "status", "text": f"{msg} — контур продолжает"})

    return warn


_hint_file_lock = threading.Lock()   # шесть контуров пишут в один файл


def append_hint(tr_path: pathlib.Path, header: str, body: str):
    """Дозапись в _hints.md. Полный диск/недоступная папка не должны молча
    убивать вечный тред (open стоял вне try в трёх контурах)."""
    try:
        hpath = tr_path.with_name(tr_path.stem + "_hints.md")
        with _hint_file_lock, hpath.open("a", encoding="utf-8") as f:
            f.write(f"\n## {header}\n{body}\n")
    except Exception as e:  # noqa: BLE001
        emit({"type": "status", "text": f"запись подсказок: {e}"})


def short_error(e: BaseException) -> str:
    """Ошибка модели одной строкой для человека: «занята», «не отвечает», а не
    стек requests с URL и портом на пол-экрана."""
    s = str(e)
    if "503" in s or "429" in s or "502" in s:
        return "модель занята"
    if "Connection" in type(e).__name__ or "Max retries" in s or "refused" in s:
        return "сервер модели не отвечает"
    if "Timeout" in type(e).__name__ or "timed out" in s:
        return "модель не ответила вовремя"
    return f"{type(e).__name__}: {s[:80]}"


_last_error: dict[str, float] = {}
_ERROR_REPEAT_S = 300.0


def emit_error(text: str):
    """Статус о сбое: приложение красит его как отказ, обычный статус — нет.

    Один и тот же текст не чаще раза в пять минут: с мёртвой моделью нить
    ретраит каждый тик и слала бы «модель занята» до конца встречи (тот же
    приём, что quiet_loop_warn — «на смене текста»).
    """
    now = time.monotonic()
    if now - _last_error.get(text, -_ERROR_REPEAT_S) < _ERROR_REPEAT_S:
        return
    _last_error[text] = now
    emit({"type": "status", "text": text, "error": True})


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
    try:
        text = last.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        # Файл графа может быть нечитаем ровно на старте: iCloud отдал
        # placeholder, права, битый диск. Бриф — украшение, а не условие
        # запуска: демон обязан подняться и без него (аудит 0.46.0).
        return ""
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
    # cloud_review_*.log — тот же класс: имена файлов встречи (тема стоит в
    # имени), счётчики и stderr CLI. Имя другое, поэтому ретеншн его не
    # видел (аудит 16.08). retry_*.log — stdout повторной пересборки:
    # маппинг имён участников, тема, stderr LLM — тоже жил вечно
    # (аудит DeepSeek 16.08).
    for old in [*logs.glob("graph_*.log"), *logs.glob("cloud_review_*.log"),
                *logs.glob("retry_*.log")]:
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def _recover_orphans(cfg: dict, current_stamp: str) -> set[str]:
    """Добить встречи, оборванные аварийно. Возвращает штампы, которые
    пересобираются прямо сейчас, — их записи ретеншну трогать нельзя.

    SIGKILL (watchdog приложения на 12-й секунде, OOM, паника) не исполняет
    finally — значит rebuild_transcript не запускался, и остаются сырые .pcm
    без стенограммы, минуток, графа и архивной папки. Ни один старт и ни одна
    ночная джоба этого не замечали, а через record_keep_days запись удалялась
    вместе с последним шансом восстановить встречу.

    Здесь мы, наоборот, запускаем пересборку для каждой чужой записи —
    ровно то, что сделал бы штатный стоп. Пересборки идут ПО ОДНОЙ, цепочкой
    в фоновом потоке (см. _rebuild_orphans_sequentially): параллельный залп
    Popen по всем сиротам после аварийных выходных — это несколько
    одновременных diarize+STT+LLM, ровно тот memory-thrash, что 12.08
    положил сервер (аудит 14.08).

    Почему возвращаем множество, а не просто спавним. Комментарий в `main`
    обещал «добиваем ДО чистки», но `Popen` — это запуск, а не завершение:
    ретеншн в том же потоке успевал удалить .pcm за миллисекунды, пока
    потомок ещё грузил интерпретатор. Гонка была не вероятностной, а
    выигранной заранее — пересборка по конструкции не трогает .pcm, пока жив
    лок демона, то есть ждёт своих 45 секунд, а prune к тому моменту давно
    отработал. Сценарий: краш в пятницу, старт в понедельник, записи старше
    record_keep_days — восстановление объявлено, а восстанавливать уже нечего
    (аудит 0.46.0, P0-1).
    """
    _prune_graph_logs(cfg)
    recovering: set[str] = set()
    pending: list[pathlib.Path] = []   # очередь цепочки, в порядке штампов
    rec_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
    tdir = ROOT / cfg["log"]["transcripts_dir"]
    if not rec_dir.is_dir():
        return recovering
    # Формат имени знает meeting_stamp: rsplit здесь был бы четвёртым местом
    # со своим знанием формата — а расхождение формата уже дважды стоило
    # проекту встреч (см. докстринг meeting_stamp).
    stamps = sorted({s for p in rec_dir.glob("*.pcm")
                     if (s := meeting_stamp.stamp_of_recording(p.name))})
    for stamp in stamps:
        if stamp == current_stamp:
            continue                      # наша встреча, она только началась
        live = tdir / f"{stamp}.md"
        if not live.exists():
            continue                      # без стенограммы пересобирать нечего
        # Помечаем ДО запуска, а не после: даже неудавшийся спавн означает, что
        # встреча ждёт восстановления, и удалять её звук нельзя тем более.
        recovering.add(stamp)
        # Осиротевшие .wav.part прежнего демона убираем МЫ, а не пересборка.
        # Пересборка отличает живого писателя от мёртвого по локу демона — но
        # после автоперезапуска (watchdog поднимает нас за 2 секунды) лок
        # держим уже мы, и для неё «демон жив», хотя автор .part убит. Она
        # честно не мешала бы ему до таймаута, а затем отказывалась бы трогать
        # .pcm — канал терялся бы навсегда, причём его же штамп мы только что
        # защитили от ретеншна: файлы зависали бессрочно. Мы — единственные,
        # кто ЗНАЕТ, что прежний писатель мёртв: лок в наших руках.
        for label in meeting_stamp.RECORDING_LABELS:
            stale = meeting_stamp.recording_path(rec_dir, stamp, label, "wav.part")
            try:
                if stale.exists():
                    emit({"type": "status",
                          "text": f"Убираю осиротевший {stale.name} прежнего демона"})
                    stale.unlink(missing_ok=True)
            except OSError:
                pass                      # не смогли убрать — пересборка дождётся таймаута
        pending.append(live)
    if pending:
        _start_orphan_chain(pending)
    return recovering


def _start_orphan_chain(lives: list[pathlib.Path]) -> None:
    """Фоновый поток цепочки: старт демона не ждёт часовых пересборок."""
    threading.Thread(target=_rebuild_orphans_sequentially, args=(lives,),
                     daemon=True, name="orphan-rebuild-chain").start()


def _rebuild_orphans_sequentially(lives: list[pathlib.Path]) -> None:
    """Пересборка сирот по одной: следующая стартует после завершения текущей.

    Одна пересборка держит модель и память, остальные ждут своей очереди —
    вместо залпа параллельных процессов (memory-thrash класса 12.08).
    Статус «recovering» ставится непосредственно перед запуском, а не всем
    сразу: иначе retry_unfinished из завершившейся пересборки увидел бы
    очередь «незавершённых» и запустил бы дубль (гвард running_elsewhere
    его отсёк бы, но и провоцировать гонку незачем). Демон умер посреди
    цепочки — не страшно: .pcm и стенограммы живы, protect-набор уже
    отработал на этот запуск, следующий старт найдёт хвост заново.
    """
    statuses = MeetingStatusStore(ROOT)
    for live in lives:
        emit({"type": "status",
              "text": f"Догоняю прерванную встречу {live.stem} — пересборка фоном"})
        try:
            statuses.processing(live, "recovering")
        except Exception:  # статус вспомогателен; запись всё равно восстанавливаем
            pass
        try:
            # start_new_session: пересборка переживает смерть демона (как и
            # раньше); run вместо Popen — это и есть очередь.
            subprocess.run(
                ["nice", "-n", "10", sys.executable,
                 str(CODE / "src" / "rebuild_transcript.py"), str(live)],
                start_new_session=True,
                stdin=subprocess.DEVNULL,   # командный пайп приложения — не его дело
                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                check=False,
            )
        except Exception as e:  # noqa: BLE001 — восстановление должно быть видимым
            statuses.failed(live, f"не удалось запустить восстановление: {e}")


def main():
    # Стенограмма — и есть чувствительные данные продукта: пишем её и всё
    # остальное закрытым от других учёток машины (аудит 16.08). Разово
    # чиним уже созданное — установки до правки лежат с правами 0644.
    harden_umask()
    harden_existing(ROOT)
    # single-instance: второй демон устроил бы битую стенограмму (один .tmp-путь)
    secure_dir(ROOT / "logs")
    lockf = open(ROOT / "logs" / "daemon.lock", "w")
    # Несколько попыток: фоновые проверяющие (пересборка, ночь — live_gate)
    # держат разделяемый лок микросекунды, и одна неудачная попытка в это
    # окно отменяла бы встречу с ложным «уже слушает в другом окне». Второй
    # живой демон держит лок постоянно — его пять попыток не пропустят.
    for attempt in range(5):
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if attempt == 4:
                emit({"type": "status", "text": "⚠️ Суфлёр уже слушает в другом окне — второй запуск отменён"})
                return
            time.sleep(0.1)
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    emit({"type": "status", "text": "Загружаю модели…"})
    stt = STT(cfg)
    llm = LLM(cfg)
    global _llm_client
    _llm_client = llm  # классификатору спорных реплик (ask_question_model)
    # env-override для тестов: стенограммы в песочницу, не в боевую папку
    tdir = os.environ.get("SUFLER_TRANSCRIPTS_DIR")
    tr = Transcript(pathlib.Path(tdir) if tdir else ROOT / cfg["log"]["transcripts_dir"])
    # Нить встречи живёт весь разговор: её дописывают, а не пересобирают.
    thread = MeetingThread()
    # Штамп записи — тот же, что у стенограммы: rebuild_transcript ищет .wav
    # по имени .md, и разъехавшиеся на границе минуты штампы означали молча
    # пропущенную финальную пересборку.
    hub = AudioHub(cfg, stamp=tr.stamp)
    hub.on_status = lambda t: emit({"type": "status", "text": t})
    # Встречи, оборванные аварийно, запускаем ДО чистки и говорим ретеншну их
    # не трогать: «до» тут не про порядок строк, а про то, что запись обязана
    # дожить до конца пересборки. Порядка строк было мало — Popen возвращается
    # мгновенно, и чистка успевала съесть .pcm первой.
    hub.protect_stamps = _recover_orphans(cfg, tr.stamp)
    # Ретеншн аудио не должен зависеть от того, началась ли новая встреча:
    # раньше чистка жила внутри _open_sinks, поэтому при record: false или
    # недельном простое записи лежали дольше обещанного в PRIVACY.
    try:
        keep_days = cfg["audio"].get("record_keep_days", 2)
        held = AudioHub.prune_recordings(
            ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings"),
            keep_days,
            protect=hub.protect_stamps,
        )
        # Сырые потоки приложения (data/sck/*, tap_stream.raw) жили вне
        # ретеншна: краш оставлял полное аудио встречи навсегда (аудит 16.08).
        dropped = AudioHub.prune_stream_files(ROOT / "data", keep_days)
        if dropped:
            emit({"type": "status",
                  "text": f"Убраны сырые потоки прошлых встреч: {dropped}"})
        if held:
            # Вслух: задержка сверх обещанного в PRIVACY срока — это исключение,
            # и человек должен видеть, что оно случилось и почему.
            emit({"type": "status",
                  "text": f"Ретеншн придержал {held} записей: встречи ещё "
                          "восстанавливаются"})
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
        # manual: панель различает стримы — авто-контент (бриф, автоподсказки)
        # уступает место и гаснет с нитью, ручной ответ живёт до крестика
        emit({"type": "hint", "text": brief, "manual": False})
        emit({"type": "hint_done", "manual": False})
        append_hint(tr.path, "стартовый бриф (архив)", brief)   # аудит: бриф был
    # канон имён: узлы Люди/ графа — чтобы «Андрюха/Света/Полин» подписывались
    # каноничной формой, а не плодили дубли узлов
    _people_dir = pathlib.Path(str(cfg["sufler"].get("graph_dir", ""))).expanduser() / "Люди"
    known_people = sorted(q.stem for q in _people_dir.glob("*.md")) if _people_dir.exists() else []
    known_first = sorted({n.split()[0] for n in known_people if n and not n.startswith("Собеседник")})
    # сверка разговора с узлами графа (ревью 15.08): старые договорённости
    # находятся локально, без brain-сервера — индекс живёт в памяти демона
    node_index = None
    try:
        _gdir = pathlib.Path(str(cfg["sufler"].get("graph_dir", ""))).expanduser()
        if _gdir.exists():
            import graph_nodes
            node_index = graph_nodes.NodeIndex(_gdir)
            node_index.refresh()
            emit({"type": "status",
                  "text": f"Сверка с узлами графа: {node_index.size} узлов"})
    except Exception as e:  # noqa: BLE001 — сверка вспомогательна
        print(f"узлы графа: {e}", file=sys.stderr, flush=True)
    threading.Thread(target=llm.warmup, daemon=True).start()
    emit({"type": "status", "text": f"Слушаю: {' + '.join(hub.sources)} · LLM: {llm.resolve_model()}"})

    stop = threading.Event()
    # Живая речь для автостопа: когда в стенограмму последний раз лёг НОВЫЙ
    # текст и звучала ли речь за эту запись вообще. По распознанному тексту, а
    # не по громкости: энергетический гейт срабатывает на кулер и клавиатуру, и
    # «тишины» не наступало бы никогда (см. src/autostop.py).
    heard = {"at": None, "spoke": False}
    global _stop_event
    _stop_event = stop          # emit сможет остановить нас при обрыве пайпа
    # SIGTERM (Swift terminate по грейсу) → штатный стоп с finally, а не убийство
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    hub.start()
    # Возраст записи — по СТЕННЫМ часам (потолок длительности обязан быть
    # шестью часами, а не шестью часами бодрствования: монотонные часы macOS
    # во сне стоят), тишина — по монотонным (сон — не тишина в комнате).
    record_started = time.monotonic()
    record_started_wall = time.time()

    # Живая диаризация ОБОИХ каналов: звонок кладёт чужие голоса в BlackHole,
    # очная встреча — все голоса в микрофон. Трекер один, метки по маппингу:
    # первый голос из mic = владелец (его микрофон), остальные — «Собеседник N».
    spk_tracker = None
    voice_names: dict[int, str] = {}
    diarize_on = bool(cfg["sufler"].get("live_diarize", True))
    emb_model = ROOT / "models" / "diar" / "embedding.onnx"
    seg_model = ROOT / "models" / "diar" / "segmentation.onnx"
    try:
        from diarize_live import (SegmentTracker, SpeakerTracker,
                                  availability_note, jobs_for, tracker_kind)
        # сначала честный ответ: почему диаризации не будет или почему она
        # будет хуже обещанной. Модели в поставку не входят, и раньше этот
        # случай проходил вообще без сообщения
        note = availability_note(diarize_on, emb_model, seg_model)
        if note:
            emit({"type": "status", "text": note})
        kind = tracker_kind(seg_model, emb_model) if diarize_on else None
        if kind == "segments":
            # эмбеддинг по кускам речи, а не по трёхсекундному чанку: на
            # границе реплик чанк смешивает голоса, и трекер залипал на первом
            # (замер: DER 0.725 и один голос из четырёх против 0.246 и всех)
            spk_tracker = SegmentTracker(
                seg_model, emb_model, sample_rate=hub.sr,
                # шаг нарезки чанков берётся из конфига хаба, а не константой:
                # от него зависит правило придержки на правой границе
                step_s=max(0.5, hub.chunk_s - hub.overlap_s))
            emit({"type": "status", "text": "👥 живая диаризация голосов включена"})
        elif kind == "chunks":
            spk_tracker = SpeakerTracker(
                emb_model, sample_rate=hub.sr,
                threshold=float(cfg["sufler"].get("live_diarize_threshold", 0.45)))
            emit({"type": "status", "text": "👥 живая диаризация: упрощённый режим"})
    except Exception as e:  # noqa: BLE001 — диаризация вспомогательна
        emit({"type": "status", "text": f"живая диаризация недоступна: {e}"})

    # Частоты основного тона по метке говорящего — только для того, чтобы не
    # назвать басовитого собеседника Анной (см. src/voice_pitch.py). Живут в
    # памяти встречи и умирают вместе с процессом, как и эмбеддинги голосов;
    # в стенограмму, граф и документы регистр голоса не попадает никогда.
    voice_f0: dict[str, list[float]] = {}

    def _note_pitch(label: str, chunk) -> None:
        try:
            f0 = voice_pitch.estimate_f0(chunk, hub.sr)
        except Exception:  # noqa: BLE001 — подсказка вспомогательна
            return
        if f0:
            vals = voice_f0.setdefault(label, [])
            vals.append(f0)
            del vals[:-40]      # держим последние — голос за встречу не меняется

    def _voice_name(n: int) -> str:
        """Имя нейтрального голоса по номеру трекера (с заведением нового)."""
        name = voice_names.get(n)
        if name is None:
            name = f"Собеседник {len(voice_names) + 1}"
            voice_names[n] = name
        return name

    def voice_label(channel_speaker: str, chunk) -> str:
        """Метка голоса для чанка. Живая разметка НЕ угадывает владельца (решение
        20.07: «первый голос mic» ловил лектора из видео, «доминирование» тоже
        ошибалось) — все голоса нейтральные «Собеседник N». Имена расставляют
        name_loop (из разговора) и финальная пересборка записи после Стопа."""
        if spk_tracker is None:
            _note_pitch(channel_speaker, chunk)
            return channel_speaker  # без трекера канальные метки честны в звонке
        try:
            n = spk_tracker.label(chunk)
        except Exception:  # noqa: BLE001
            return channel_speaker
        if n is None:
            return channel_speaker
        name = _voice_name(n)
        _note_pitch(name, chunk)
        return name

    # Имя владельца — для гейта мгновенных ответов: ⚡ стреляет по вопросу
    # «той стороны», а владелец отсекается пословной сверкой user_name.
    owner_name = (cfg["sufler"].get("user_name") or "").strip()

    # Голоса вспомогательных контуров: их отказ больше не немой (см. quiet_loop_warn)
    warn_deja = quiet_loop_warn("дежавю")
    warn_markup = quiet_loop_warn("разметка реплик")
    warn_names = quiet_loop_warn("имена")

    def stt_loop():
        while not stop.is_set():
            try:
                batch = hub.pull_labeled()
            except Exception as e:  # noqa: BLE001 — смерть этого потока «тихая»:
                # heartbeat живёт, а стенограмма просто молчит (профиль 20.07);
                # в трёх соседних местах это уже чинили, вызов остался вне try
                emit({"type": "status", "text": f"звук: {e} — поток STT живёт"})
                time.sleep(1)
                continue
            if not batch:
                time.sleep(0.1)
                continue
            for speaker, chunk in batch:
                # Позиционная раскладка (ревью 15.08): если в чанке говорили
                # несколько человек кусками от секунды, распознаём по кускам —
                # текст на границе реплик перестаёт уходить чужому голосу
                # (замер сегментного пути: DER 0.246 → 0.090). Раскладка идёт
                # ДО STT, потому что решает, что именно распознавать.
                # Контракт трекера трёхсостоянный: None-pieces — распознавать
                # чанк целиком (раскладка не сказала ничего или голос один и
                # покрыт весь чанк); [] — не распознавать вовсе (вся речь
                # исключена политикой: придержанный хвост, микро-куски —
                # STT целого чанка подписал бы их слова не тому); окна —
                # распознавать по окнам. Голос в jobs — номером: имя
                # заводится только после непустого текста, чтобы пустое
                # распознавание не плодило «Собеседника-призрака».
                jobs: list[tuple[object, int | None, object | None]] | None
                if spk_tracker is not None and hasattr(spk_tracker, "split"):
                    try:
                        res = spk_tracker.split(chunk, channel=speaker)
                    except Exception:  # noqa: BLE001 — диаризация вспомогательна
                        res = None  # jobs_for даст канальную метку без
                        # voice_label: повторный вызов трекера учил бы
                        # центроиды тем же звуком дважды (ревью 15.08 ×2)
                    jobs = jobs_for(res, chunk)
                    if jobs is None:
                        continue  # вся речь чанка исключена — пропуск
                else:
                    jobs = [(chunk, None, None)]  # None: метку решит voice_label

                # сначала все распознавания, потом все добавления: упавший STT
                # посреди чанка не оставляет в стенограмме половину с дублем
                # при откате (замечание ревью 15.08)
                rows: list[tuple[str, str]] = []
                pitch_best: dict[str, tuple[int, object]] = {}
                for piece, n, raw_piece in jobs:
                    try:
                        text = stt.transcribe(piece, hub.sr)
                    except Exception as e:  # noqa: BLE001
                        emit({"type": "status", "text": f"STT: {e}"})
                        continue
                    if not text or text.lower().strip(" .!») ") in NOISE:
                        continue
                    if n is None:
                        name = voice_label(speaker, piece)
                    elif n < 0:
                        name = speaker
                        _note_pitch(name, piece)
                    else:
                        name = _voice_name(n)
                        # высота голоса — по самому длинному сырому куску
                        # каждого голоса, без pad-запаса: в запас попадает
                        # сосед (ревью 15.08)
                        cand = raw_piece if raw_piece is not None else piece
                        best = pitch_best.get(name)
                        if best is None or len(cand) > best[0]:
                            pitch_best[name] = (len(cand), cand)
                    rows.append((name, text))
                for name, (_n, cand) in pitch_best.items():
                    _note_pitch(name, cand)
                for name, text in rows:
                    try:
                        added = tr.add(text, speaker=name)
                    except Exception as e:  # noqa: BLE001 — стенограмма не должна убивать STT-тред
                        emit({"type": "status", "text": f"стенограмма: {e}"})
                        continue
                    if not added:  # полностью съеденные дедупом не эмитим
                        continue
                    # Речь для автостопа отмечаем ЗДЕСЬ, после дедупа: на шуме
                    # STT повторяет одну и ту же фантомную фразу, дедуп её
                    # съедает — но таймер тишины она сбрасывала бы, и в шумной
                    # пустой комнате автостоп не сработал бы никогда (ревью
                    # 18.08, GLM).
                    heard["at"] = time.monotonic()
                    heard["spoke"] = True
                    disp = tr.display_name(name)
                    emit({
                        "type": "transcript",
                        "ts": f"{dt.datetime.now():%H:%M:%S}",
                        "speaker": disp,   # UI клеит куски одного голоса в абзац
                        "plain": added,
                        "text": f"{disp}: {added}",  # совместимость со старым UI
                    })
                    # режим собеседования: вопрос с той стороны → мгновенный ответ.
                    # «Не владелец» вместо startswith(«Собеседник»): строгое
                    # равенство оставляло ⚡/☁️ мёртвыми всю встречу, а startswith
                    # глушил их с момента, когда name_loop опознавал собеседника
                    # по имени (аудит 14.08). Сверка — speaker_names.is_counterpart.
                    if instant_on and toggles["hints"] \
                            and speaker_names.is_counterpart(name, owner_name) \
                            and looks_question(added):
                        fire_question(added)

    # Промпт и фильтр тезисов — в src/thesis_rules.py (тестируются без аудио).
    # 💎 убран 03.08: факты ведёт нить, тезисам остались 📌 и 💭.

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
        text = thesis_rules.strip_mark(line)
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
            try:
                parts: list[str] = []
                yielded = False
                for tok in llm.stream(
                        (f"Контекст (уже обработано):\n{context_tail}\n\n" if context_tail else "")
                        + f"НОВЫЙ фрагмент стенограммы:\n{fresh}",
                        model=cfg["sufler"].get("think_model", llm.small),
                        system=thesis_rules.THINK_SYSTEM,
                ):
                    if manual_evt.is_set():
                        yielded = True   # человек задал вопрос — тяжёлая модель ему нужнее
                        break
                    parts.append(tok)
                if yielded:
                    continue   # seen не двигаем: фрагмент разберём следующим тиком
                seen = len(full)
                out = "".join(parts)
                context_tail = fresh[-800:]
                # Строки без живого префикса отбрасываются целиком: вступления
                # («Вот что важно:») и отставной 💎 в ленте выглядели репликами.
                for line in thesis_rules.parse(out):
                    if is_dup_thesis(line):
                        continue
                    # Тезис — строка нити: одно полотно вместо двух панелей,
                    # между которыми человек метался глазами на встрече.
                    if thread.add_thesis(line):
                        emit({"type": "thread", "text": thread.render()})
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
    cloud_live = privacy.cloud_live_enabled(cfg)  # молчание конфига = «нет», см. src/privacy.py
    cloud_hints = privacy.cloud_hints_enabled(cfg)  # свой ключ ПОВЕРХ cloud_live
    cloud_evt = threading.Event()
    _last_fire = [0.0]
    _cloud_last = {"t": 0.0, "words": set()}
    _pending_q = {"text": ""}  # последний детектированный вопрос — панели показывают его над ответом
    # живые тумблеры UI (`set hints|theses|cloud on|off`): выключенные контуры
    # молчат до обратного включения; дефолты хранит и присылает приложение
    toggles = {"hints": True, "theses": True, "cloud": True}

    def fire_question(q: str = ""):
        """Один вопрос = один ⚡/☁️: fast_trigger и stt_loop не дублируют друг друга.

        Обрывок вопросом не считается. STT ставит «?» по интонации, и в
        панель шли «Что?», «С какого бы?» — на каждый уходил вызов локальной
        модели И облачной, а в ответ приходило «уточните вопрос» на четыре
        строки (04.08). Проверка структурная, без списков фраз —
        src/question_filter.py.
        """
        if not toggles["hints"]:
            return   # тумблер «Подсказки» выключен: ни ⚡, ни ☁️ — из любого триггера
        now = time.time()
        if now - _last_fire[0] < 8:
            return
        if q.strip() and not question_filter.is_worth_asking(q, _pending_q["text"]):
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

    def gen_hint(header: str | None = None, manual: bool = False,
                 model: str | None = None) -> bool:
        """Одна подсказка. Возвращает True, если подсказка дошла до конца (не сорвалась и не уступила).

        Сбой модели — статус, а не текст подсказки. Раньше `[LLM: 503 …]`
        уходил событием hint: заголовок «━━ авто ━━» уже сбросил карточку, и
        ошибка ЗАМЕНЯЛА последнюю хорошую подсказку — 18.08 панель 45 минут
        показывала стек-текст вместо конспекта. Теперь заголовок эмитится
        с первым настоящим токеном, при сбое карточка не трогается, а человеку
        (ручной запрос — он ждёт) приходит короткое «модель занята».
        """
        if manual:
            manual_evt.set()  # сигнал авто-генерации уступить
        with hint_lock:
            if manual:
                manual_evt.clear()
            tail = tr.tail(max_ctx)
            if not tail:
                emit({"type": "hint", "text": "Стенограмма пока пуста.", "manual": manual})
                emit({"type": "hint_done", "manual": manual})
                return True
            parts: list[str] = []
            failed: Exception | None = None
            yielded = False
            try:
                for tok in llm.hint(tail, model=model):
                    if not manual and manual_evt.is_set():
                        yielded = True
                        if parts:   # уже начали показывать — честно оборвать на экране
                            emit({"type": "hint", "text": " …⏸", "manual": manual})
                            parts.append(" …⏸")
                        break  # уступаем ручному запросу
                    if header is not None and not parts:
                        emit({"type": "hint", "text": header, "manual": manual})
                    emit({"type": "hint", "text": tok, "manual": manual})
                    parts.append(tok)
            except Exception as e:  # noqa: BLE001
                failed = e
                emit_error(f"{'подсказка' if manual else 'авто-подсказка'}: {short_error(e)}")
                if manual:   # человек ждёт ответа: короткая строка в карточку
                    emit({"type": "hint", "text": f"\n⚠ {short_error(e)} — попробуйте ещё раз",
                          "manual": True})
                elif parts:  # авто оборвалась после токенов: обрезок не должен выглядеть целым
                    emit({"type": "hint", "text": " …⚠", "manual": False})
                    parts.append(" …⚠")
            emit({"type": "hint_done", "manual": manual})
            if parts:  # подсказки тоже сохраняем — лог полного разговора
                kind = "ручная" if manual else "авто"
                if failed is not None:
                    kind += f", сорвалась: {short_error(failed)}"
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] подсказка ({kind})", "".join(parts))
            return failed is None and not yielded   # уступила — вернёмся к этому куску раньше


    _refine_last = {"len": 0}

    def _cloud_refine_thread():
        """Облако правит нить, а не комментирует её сбоку.

        Раньше уточнение Haiku падало отдельным блоком «☁️ …» под подсказкой —
        человек сам сопоставлял его с полотном (двойное чтение на живой
        встрече; решение 05.08). Теперь ревизор получает нить и свежие
        реплики, возвращает пары «FIX: строка => точнее» — строки правятся
        на месте, изменённые слова выделены ==так==. «Было → стало» целиком
        уходит в файл-лог: аудит не на экране. Тумблер прежний (cloud_hints):
        это тот же постоянный поток стенограммы в облако.
        """
        if not (cloud_hints and toggles["cloud"]):
            return
        tail = tr.tail(max_ctx)
        # Прирост меряем по ПОЛНОЙ стенограмме: хвост обрезан до max_ctx, и
        # после насыщения его длина не растёт — ревизия срабатывала один раз
        # в начале встречи и больше никогда (аудит 18.08 ×3).
        grown = len(tr.full())
        if grown - _refine_last["len"] < 1200:
            return   # разговор не набежал — ревизору не на чем ловить неточности
        woven = thread.as_context(topics=2)
        if not woven.strip():
            return   # нити ещё нет — счётчик не двигаем, иначе первая ревизия уедет вдвое дальше
        _refine_last["len"] = grown

        def cloud_thread_refine():
            # Проверка дублирует внешний cheap-gate намеренно: именно эта
            # функция запускает процесс, а значит сама держит privacy-границу.
            # Перенос/ручной вызов вложенной функции не должен сделать её
            # сетевым выходом без рубильника.
            if not privacy.cloud_hints_enabled(cfg):
                return
            claude_bin = shutil.which("claude") or "/opt/homebrew/bin/claude"
            model = cloud.model(cfg, "cloud_hints_model")
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            env.update(load_claude_proxy_env())
            short = model.split("-")[1] if model.count("-") else model
            try:
                r = subprocess.run(
                    [claude_bin, "-p",
                     "Рабочая встреча. Нить (конспект на экране):\n" + woven +
                     "\n\nСвежие реплики:\n" + tail[-3500:] +
                     "\n\nНайди строки нити, которые НЕТОЧНЫ по репликам: "
                     "перепутан факт, статус, стадия работы, акцент. На каждую "
                     "неточность — ровно одна строка ответа:\n"
                     "FIX: <строка нити как есть> => <исправленная, до 14 слов>\n"
                     "Только правки существующих строк, ничего нового не добавляй, "
                     "не комментируй. Всё точно — ответь ровно: NONE",
                     # нить и реплики — чужие слова в промпте: инструментов
                     # этому вызову не положено (см. cloud.text_only_args)
                     "--model", model, *cloud.text_only_args()],
                    # stdin=DEVNULL обязателен: без него потомок наследует
                    # командный пайп от приложения. Claude на унаследованном
                    # fifo ждёт EOF (см. соседний вызов ниже, там это уже
                    # учтено) — ревизия висит до таймаута и молча гибнет, а
                    # выпитые из пайпа байты — это команды UI, которых демон
                    # уже не увидит.
                    stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=60, env=env)
                out = (r.stdout or "").strip()
            except Exception:  # noqa: BLE001 — ревизия не критична, тишина честнее
                return
            from meeting_thread import parse_edits
            applied = thread.apply_edits(parse_edits(out))
            if not applied:
                return
            log = "\n".join(f"было: {a}\nстало: {b}" for a, b in applied)
            append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ревизия нити ({short}, {len(applied)})", log)
            emit({"type": "thread", "text": thread.render()})

        threading.Thread(target=cloud_thread_refine, daemon=True).start()

    def thread_loop():
        """Нить встречи: растёт по мере разговора, не переписывается заново.

        Отличие от подсказки принципиальное. Подсказка каждый раз сочиняет
        конспект последних двух минут — и повторяет уже сказанное: за встречу
        03.08 лог подсказок вырос до 68 КБ, в основном пересказом. Здесь модель
        видит уже собранную нить и дописывает только новое; не появилось нового
        — возвращает NONE, и на экране ничего не дёргается.

        Зовём не по часам, а по приросту разговора: молчание в переговорной не
        рождает новых строк, сколько ни жди.
        """
        seen = 0
        quiet_rounds = 0
        while not stop.is_set():
            time.sleep(THREAD_TICK)
            if not toggles["hints"]:
                continue
            full = tr.full()
            # Порог растёт после пустых заходов: если разговор идёт, а нового в
            # нём нет (обсуждают одно и то же по кругу), дёргать модель каждую
            # минуту незачем — она снова ответит NONE.
            need = THREAD_MIN_NEW * (1 + min(quiet_rounds, 3))
            if len(full) - seen < need:
                continue
            tail = tr.tail(3500)
            if len(tail) < 400:
                seen = len(full)
                continue
            try:
                with hint_lock:
                    parts: list[str] = []
                    yielded = False
                    for tok in llm.thread(tail, thread.as_context(), model=auto_model):
                        if manual_evt.is_set():
                            yielded = True   # ручной вопрос важнее: бросаем, кусок дождётся
                            break
                        parts.append(tok)
                if yielded:
                    continue   # seen не двигаем — этот кусок разговора разберём в следующий тик
                answer = "".join(parts)
                # seen — только ПОСЛЕ удачного ответа: сбой модели раньше
                # выкидывал кусок из нити навсегда (нить дописывает лишь
                # новое, повторного покрытия, как у подсказок, у неё нет)
                seen = len(full)
                added = thread.ingest(answer, at=f"{dt.datetime.now():%H:%M}")
                quiet_rounds = 0 if added else quiet_rounds + 1
                if added:
                    emit({"type": "thread", "text": thread.render()})
                    append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] нить (+{added})",
                                thread.full())
                    _cloud_refine_thread()
            except Exception as e:  # noqa: BLE001 — поток не должен умирать молча
                emit_error(f"нить встречи: {short_error(e)}")

    expand_lock = threading.Lock()

    def expand_topic(title: str = ""):
        """⏮ по клавише: что было по теме раньше — из графа прямо в нить.

        Живой контекст подтягивает архив в системный промпт молча. Здесь
        наоборот: человек явно просит хвосты по теме, и ответ дописывается в
        нить строками ⏮ — туда, где он его ждёт, а не в отдельное окно.
        Без названия разбирается текущая (последняя) тема нити.
        """
        if not expand_lock.acquire(blocking=False):
            emit({"type": "status", "text": "⏮ прошлый разбор ещё выполняется"})
            return
        try:
            emit({"type": "expand_started"})
            title = title.strip() or thread.last_topic_title
            if not title:
                emit({"type": "status", "text": "⏮ нить пуста — разбирать нечего"})
                return
            def _nodes_direct() -> int:
                """Фолбэк ручного ⏮ (ревью 15.08): узлы графа отвечают и без
                brain-сервера — их история читается напрямую из файлов."""
                if node_index is None:
                    return 0
                try:
                    node_index.refresh()
                    found = node_index.lookup(title, strict=False, limit=1)
                except Exception:  # noqa: BLE001
                    return 0
                if not found:
                    return 0
                lines = node_index.digest(found[0], with_name=False)
                return thread.add_archive(found[0].name, lines)

            try:
                import requests as _rq
                _folder = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser().name
                v = _rq.post("http://127.0.0.1:8100/vault_search",
                             json={"query": title, "limit": 3, "folder": _folder,
                                   "snippet_chars": 700}, timeout=8).json().get("text", "")
            except Exception:  # noqa: BLE001 — brain лежит: сначала узлы, потом честный статус
                added = _nodes_direct()
                if added:
                    emit({"type": "thread", "text": thread.render()})
                    append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ⏮ {title} (узлы)",
                                thread.full())
                else:
                    emit({"type": "status", "text": "⏮ архив недоступен (brain не отвечает)"})
                return
            if not v or v.startswith("⚠") or "не найдено" in v.lower():
                added = _nodes_direct()
                if added:
                    emit({"type": "thread", "text": thread.render()})
                    append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ⏮ {title} (узлы)",
                                thread.full())
                else:
                    emit({"type": "status", "text": f"⏮ в архиве по «{title}» пусто"})
                return
            try:
                with hint_lock:   # не толкаться с подсказкой на одной модели
                    out = "".join(llm.stream(
                        f"Выдержки из архива прошлых встреч по теме «{title}»:\n\n{v}\n\n"
                        "Выпиши 2-3 самых важных факта прошлых встреч по этой теме: "
                        "решение, статус, кто ведёт — с датой, если она видна. "
                        "По строке на факт, без вступлений и нумерации.",
                        model=llm.small,
                        system="Ты сжимаешь архив встреч в короткие факты. "
                               "Отвечай только строками фактов."))
            except Exception as e:  # noqa: BLE001
                emit({"type": "status", "text": f"⏮ разбор сорвался: {e}"})
                return
            from meeting_thread import parse_archive_facts
            added = thread.add_archive(title, parse_archive_facts(out))
            if added:
                emit({"type": "thread", "text": thread.render()})
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ⏮ {title}",
                            thread.full())
            else:
                emit({"type": "status", "text": f"⏮ по «{title}» нового не нашлось"})
        finally:
            try:
                emit({"type": "expand_done"})
            finally:
                expand_lock.release()

    def auto_hint_loop():
        """Подсказки в реальном времени: сами, по мере накопления разговора.

        Сорвалась (модель занята) — следующая попытка через HINT_RETRY, а не
        через полный интервал, и без требования новых HINT_MIN_NEW знаков:
        разговор, на котором сорвались, ещё не законспектирован.
        """
        seen = 0
        wait = HINT_EVERY
        while not stop.is_set():
            time.sleep(wait)
            wait = HINT_EVERY
            if not toggles["hints"]:
                continue
            full = tr.full()
            if len(full) - seen < HINT_MIN_NEW:
                continue  # разговор не набежал — молчим
            try:
                if gen_hint(header=f"\n\n━━ авто {dt.datetime.now():%H:%M} ━━\n", model=auto_model):
                    seen = len(full)
                else:
                    wait = HINT_RETRY
            except Exception as e:  # noqa: BLE001 — единственный поток без своего try:
                # сбой вне внутреннего try gen_hint (например, запись подсказки в
                # файл на недоступном iCloud) убивал поток НАВСЕГДА, а heartbeat
                # главного треда продолжал идти — UI считал, что всё живо
                emit_error(f"авто-подсказка сорвалась: {e}")

    def instant_loop():
        """Режим собеседования: вопрос от собеседника → готовый ответ без задержки.

        Лёгкая модель: первые слова через ~2-3с после конца фразы, кулер молчит.
        """
        while not stop.is_set():
            if not instant_evt.wait(timeout=0.5):
                continue
            instant_evt.clear()
            manual_evt.set()  # авто-подсказка уступает мгновенному ответу
            # Снимок (вопрос, хвост) берётся ЗДЕСЬ и не перечитывается под
            # локом: пока поток ждал hint_lock, мог прийти второй вопрос — и
            # модель отвечала бы на него с узлами первого, а ответ ложился
            # под чужим вопросом в нить и лог (ревью 15.08 ×3).
            q = _pending_q["text"]
            tail = tr.tail(1600)
            # сверка вопроса с узлами графа — ДО hint_lock и вне STT-пути:
            # файловый лукап не смеет держать ни распознавание, ни очередь
            # подсказок. Явный вопрос — чувствительный режим. Опознанные
            # спикеры — только реально прозвучавшие на ЭТОЙ встрече: весь
            # список людей графа делал бы «опознанным» любого (ревью ×3).
            nodes_block = ""
            if node_index is not None and q:
                try:
                    node_index.refresh()
                    found = node_index.lookup(q, strict=False,
                                              known_names=set(voice_names.values()),
                                              limit=2)
                    nodes_block = "\n".join(
                        ln for n in found for ln in node_index.digest(n))[:600]
                except Exception:  # noqa: BLE001
                    nodes_block = ""
            with hint_lock:
                manual_evt.clear()
                if not tail:
                    continue
                emit({"type": "status", "text": f"⚡ отвечаю: {q[:60]}" if q else "⚡ отвечаю"})
                parts: list[str] = []
                try:
                    for tok in llm.instant(tail, nodes=nodes_block):
                        parts.append(tok)
                except Exception as e:  # noqa: BLE001
                    emit_error(f"⚡ ответ не собрался: {short_error(e)}")
                answer = "".join(parts)
                # Отказ модели («вопроса не вижу, уточните») — не ответ, и в
                # полотно он не идёт: раньше такие абзацы занимали пол-панели.
                if answer and not question_filter.is_refusal(answer):
                    if thread.add_answer(q, question_filter.squeeze(answer)):
                        emit({"type": "thread", "text": thread.render()})
                    label = f"⚡ ответ на: {q[:120]}" if q else "⚡ мгновенный ответ"
                    append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] {label}", answer)

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
        model = cloud.model(cfg, "cloud_live_model")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.update(load_claude_proxy_env())  # без прокси из GUI-запуска — 403 по региону
        while not stop.is_set():
            if not cloud_evt.wait(timeout=0.5):
                continue
            cloud_evt.clear()
            # Спрашиваем privacy непосредственно перед сетевым выходом, а не
            # доверяем снимку из main: сторож ниже доказывает связь именно с
            # subprocess.run, и ручная команда не может обойти рубильник.
            if not privacy.cloud_live_enabled(cfg):
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
            # «думает над ❓…» жило в полотне и дублировало вопрос, который
            # печатала панель ответа. Это служебная строка — её место в статусе.
            emit({"type": "status", "text": think})
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
                     # изоляция «только текст» — единый контракт всех
                     # headless-вызовов (список запретов жил здесь копией)
                     *cloud.text_only_args()],
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
            if out and not question_filter.is_refusal(out):
                if thread.add_answer(q, question_filter.squeeze(out, max_lines=3, max_chars=380)):
                    emit({"type": "thread", "text": thread.render()})
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
        # Через install_profile, а не bool(): приложение пишет значения
        # строкой в кавычках, и bool("false") == True — контур остался бы
        # включённым вместе с эмбеддером на 1.2 ГБ (ревью 19.08).
        if not install_profile.deja_vu_enabled(cfg):
            return
        # Пустой graph_dir — это Path("."), и «Ядра» искались бы в рабочем
        # каталоге демона: случайная папка с таким именем подсунула бы живой
        # встрече чужие темы (третий круг, DeepSeek).
        graph_raw = str(cfg["sufler"].get("graph_dir", "") or "").strip()
        if not graph_raw:
            return
        gdir = pathlib.Path(graph_raw).expanduser()
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
            return llm_embed(cfg, texts, model=emb_model, timeout=20)

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
                        # в НИТЬ, не в мёртвый канал: события type:"thesis"
                        # с 04.08 падали в массив, который UI не читает.
                        # into_current: тема-якорь «Контекст…» стала бы
                        # последней и магнитила живые строки (ревью 15.08 ×2);
                        # имена ядер уже внутри строк брифа
                        if thread.add_archive(
                                "", [ln.lstrip("• ") for ln in lines[1:]],
                                into_current=True):
                            emit({"type": "thread", "text": thread.render()})
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
                # точечное дежавю — в текущую тему нити, как сверка узлов:
                # своя тема с именем ядра приклеивала бы живые строки к нему
                if thread.add_archive(
                        top.stem,
                        [f"{top.stem} · уже обсуждалось {when}."
                         + (f" Статус: {st}" if st else "")],
                        into_current=True):
                    emit({"type": "thread", "text": thread.render()})
                tr.note(line)
            except Exception as e:  # noqa: BLE001 — дежавю вспомогательно
                warn_deja(e)

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
            except Exception as e:  # noqa: BLE001 — разметка вспомогательна
                warn_markup(e)

    def _median_f0(label: str) -> float | None:
        """Медианная частота основного тона этой метки за встречу.

        Медиана, а не среднее: смех и вопросительная интонация задирают
        отдельные чанки, но не голос человека.
        """
        vals = voice_f0.get(label) or []
        if len(vals) < voice_pitch.MIN_CHUNKS:
            return None
        return float(sorted(vals)[len(vals) // 2])

    _gender_cache: dict[str, str] = {}

    def name_gender(raw_name: str) -> str | None:
        """Мужское имя, женское или подходит обоим. None — не смогли решить.

        Спрашиваем лёгкую модель, а не список имён: списком «Саша», «Женя» и
        «Валя» не разложить, а уменьшительных в живой речи больше, чем полных.
        Ответ кэшируется на встречу — имён на встрече единицы.
        """
        name = (raw_name or "").strip().capitalize()
        if not name:
            return None
        if name in _gender_cache:
            return _gender_cache[name]
        try:
            out = "".join(llm.stream(
                f"Имя «{name}». Оно мужское, женское или подходит обоим? "
                "Ответь ОДНИМ словом: male, female или unisex. "
                "Сомневаешься — unisex.",
                model=llm.small,
                system="Ты отвечаешь одним словом: male, female или unisex.",
            )).strip().lower()
        except Exception:  # noqa: BLE001 — подсказка вспомогательна
            return None
        verdict = next((w for w in ("female", "male", "unisex") if w in out), None)
        if verdict:
            _gender_cache[name] = verdict
        return verdict

    def name_loop():
        """Опознание людей из разговора, всю встречу (каждые ~90с).

        Живая замена меток: «Собеседник N» → имя, как только оно определено
        НАДЁЖНО (человек представился, или к нему обратились и он ответил).
        Меняется задним числом стенограмма (rename_speaker), лента приложения
        (событие rename) и все будущие реплики (voice_names). владельца не
        угадываем (решение 20.07). Без диаризации — старый одиночный режим.

        Поверх текстовых проверок стоит гейт по голосу: если регистр голоса
        метки и род имени уверенно противоречат («Анна» у баса), имя
        отклоняется и метка остаётся честным «Собеседник N».
        """
        named = False
        listed: list[str] = []
        renamed: dict[str, str] = {}  # «Собеседник 1» → «Дмитрий»
        # владельца не подписываем: его голос определяется каналом микрофона,
        # а не разговором. Пусто в конфиге — проверка просто не сработает.
        # Строка идёт в speaker_names целиком: сравнение по словам, потому что
        # в user_name обычно имя И фамилия, а модель предлагает одно имя.
        owner_name = (cfg["sufler"].get("user_name") or "").strip()
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
                        for label, raw_name in pairs.items():
                            # все гварды доверия — в одном месте (src/speaker_names.py),
                            # чтобы безмодельная ветка ниже не расходилась с этой:
                            # владелец по словам user_name, «обращение ≠ говорящий»,
                            # выдуманные имена, падежи по людям графа
                            name = speaker_names.trustworthy_name(
                                raw_name, sample=sample, label=label,
                                owner_name=owner_name, known=tuple(known_first),
                                voice=voice_pitch.register(_median_f0(label)),
                                name_gender=name_gender(str(raw_name)))
                            if (label in labels and name
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
                    raw_name = out.strip().split()[0] if out.strip() else ""
                    # та же проверка доверия, что в мультиспикерной ветке выше:
                    # эта ветка работает БЕЗ модели голосов, то есть по умолчанию,
                    # и раньше была слабее — гварда «обращение ≠ говорящий» в ней
                    # не было вовсе, а владелец узнавался только по полной строке
                    name = speaker_names.trustworthy_name(
                        raw_name, sample=sample, label="Собеседник",
                        owner_name=owner_name, known=tuple(known_first),
                        voice=voice_pitch.register(_median_f0("Собеседник")),
                        name_gender=name_gender(raw_name))
                    if name:
                        tr.rename_speaker("Собеседник", name)
                        emit({"type": "rename", "from": "Собеседник", "to": name})
                        emit({"type": "status", "text": f"👤 Собеседник опознан: {name}"})
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
            except Exception as e:  # noqa: BLE001 — имена вспомогательны, но их
                # отказ до конца встречи неотличим от «имён не звучало»
                warn_names(e)

    def minutes_loop():
        """Живые минутки: черновик _minutes.md дорабатывается по ходу встречи.

        Идёт на лёгкой модели ПАРАЛЛЕЛЬНО подсказкам (другая модель Ollama).
        Финальную версию делает кнопка «Протокол» (26b).
        """
        seen = 0
        mpath = tr.path.with_name(tr.path.stem + "_minutes.md")
        while not stop.is_set():
            time.sleep(150)
            try:
                # кнопка «Протокол» пишет ФИНАЛЬНЫЕ минутки (26b) без маркера черновика —
                # авточерновик лёгкой модели не должен их затирать. Чтение — в try:
                # iCloud-заглушка или права убивали поток до конца встречи молча.
                if mpath.exists() and not mpath.read_text(encoding="utf-8").startswith("<!-- черновик"):
                    continue
            except Exception as e:  # noqa: BLE001
                emit_error(f"минутки: {short_error(e)}")
                continue
            full = tr.full()
            if len(full) - seen < 400:
                continue
            grown = len(full)   # seen двигаем после удачной генерации (см. thread_loop)
            # Вся стенограмма в промпт не влезает: num_ctx 8192 — это ~25 000
            # знаков, и Ollama молча режет ГОЛОВУ. Черновику отдаём начало
            # (повестка, участники) и хвост (свежие решения) — тот же приём,
            # что у финальных минуток (_fit) и debrief_excerpt.
            if len(full) > 18_000:
                full = full[:3_000] + "\n\n[… середина опущена …]\n\n" + full[-14_000:]
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
                seen = grown
                if out.strip():
                    # Маркер перепроверяем ПЕРЕД записью, а не только на входе
                    # в итерацию: генерация выше занимает десятки секунд, и
                    # если за это время человек нажал «Протокол», финальные
                    # минутки (26b, без маркера) уже лежат на диске — черновик
                    # лёгкой модели затирал их молча (аудит 0.46.0).
                    if mpath.exists() and not mpath.read_text(
                            encoding="utf-8").startswith("<!-- черновик"):
                        continue
                    mpath.write_text("<!-- черновик, встреча идёт -->\n" + out, encoding="utf-8")
                    emit({"type": "status", "text": f"🗒 минутки-черновик обновлены ({dt.datetime.now():%H:%M})"})
            except Exception as e:  # noqa: BLE001
                emit_error(f"минутки: {short_error(e)}")

    def deep_loop():
        """Глубокая проработка: 26b пересматривает заметки быстрой модели.

        Раз в ~5 минут: подтверждает/уточняет/отбрасывает 📌💭 от e4b,
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
                    deep_added = 0
                    for line in out.strip().splitlines():
                        line = line.strip()
                        if line and line != "NONE" and line.startswith("🔬"):
                            # глубокая мысль — строкой 💭 в нить (канал
                            # type:"thesis" мёртв с 04.08); знак 🔬 остаётся
                            # в файле-логе через tr.note
                            deep_added += thread.add_thesis(
                                "💭 " + line.lstrip("🔬 ").strip())
                            tr.note(line)
                    if deep_added:
                        emit({"type": "thread", "text": thread.render()})
                except Exception as e:  # noqa: BLE001
                    emit({"type": "status", "text": f"глубокий контур: {e}"})

    def gen_answer(question: str):
        """Вопрос пользователя из UI: ответ по живой стенограмме + графу/vault.

        Скорость — приоритет: владелец спрашивает ПОСРЕДИ встречи. Отклик в панель
        мгновенно (до lock), vault не дольше 2.5с, ответ каплен 220 токенами
        на лёгкой модели — итого первые слова ~1-2с, полный ответ ~4-6с.
        """
        emit({"type": "hint", "text": f"\n\n❓ {question}\n", "manual": True})
        manual_evt.set()  # авто-контуры уступают
        # vault ищем ДО лока: HTTP на 2.5с не смеет держать очередь подсказок
        # (⚡ и авто ждут тот же lock), а сам поиск в модели не нуждается
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
        with hint_lock:
            manual_evt.clear()
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
                    emit({"type": "hint", "text": tok, "manual": True})
                    parts.append(tok)
            except Exception as e:  # noqa: BLE001
                emit_error(f"ответ на вопрос: {short_error(e)}")
                emit({"type": "hint", "text": f"\n⚠ {short_error(e)} — попробуйте ещё раз", "manual": True})
            emit({"type": "hint_done", "manual": True})
            if parts:
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] ❓ {question}", "".join(parts))

    def _do_summary():
        manual_evt.set()   # фон (нить, тезисы, авто-подсказка) уступает, как ручной подсказке
        with hint_lock:
            manual_evt.clear()
            chunks: list[str] = []
            ok = True
            try:
                for tok in llm.minutes(tr.full() or "(пусто)"):
                    chunks.append(tok)
                    emit({"type": "hint", "text": tok, "manual": True})
            except Exception as e:  # noqa: BLE001
                ok = False
                emit_error(f"протокол: {short_error(e)}")
                emit({"type": "hint", "text": f"\n⚠ {short_error(e)} — протокол не сохранён, "
                                             "нажмите ещё раз", "manual": True})
            emit({"type": "hint_done", "manual": True})
            # Усечённый документ не пишем как готовый: обрыв стрима на длинной
            # встрече оставлял минутки без решений из хвоста, и внешне они
            # выглядели полными (аудит 18.08). Файл — только с полного ответа.
            if chunks and ok:  # минутки — отдельным файлом рядом со стенограммой
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
                # Через временное имя: обрыв посреди write_text оставлял бы
                # усечённые минутки поверх готовых (mcp_server это уже чинил,
                # здесь оставался прямой write_text — аудит 14.08)
                tmp = mpath.with_name(mpath.name + f".tmp{os.getpid()}")
                try:
                    tmp.write_text(doc, encoding="utf-8")
                    tmp.replace(mpath)
                finally:
                    tmp.unlink(missing_ok=True)
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
            elif cmd == "expand" or cmd.startswith("expand "):
                # ⏮: разбор темы нити по графу; без аргумента — текущая тема
                t = raw.strip()[7:].strip() if cmd.startswith("expand ") else ""
                threading.Thread(target=expand_topic, args=(t,), daemon=True).start()
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
        shown_nodes: set[str] = set()   # авто-⏮: узел показывается раз за встречу
        NODE_CAP = 4                    # и не больше четырёх узлов на встречу
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

            # Сверка хвоста разговора с узлами графа (ревью 15.08): старые
            # договорённости видны В НИТИ строками ⏮, а не только в промпте.
            # Строгий режим: авто-вставка требует точности — ложный узел в
            # нити дороже пропущенного. Опознанные спикеры — только реально
            # прозвучавшие на встрече (не весь список людей графа). Выборка
            # контекста НЕ ограничена капом показа: кап стережёт нить, а не
            # невидимую память промпта (ревью ×3). Показанным узел считается
            # только после реально добавленной строки; уже показанный или
            # пустой верхний кандидат не блокирует следующего.
            node_hits = []
            if node_index is not None:
                try:
                    node_index.refresh()
                    # выборка шире капа показа: три показанных узла не должны
                    # навсегда заслонять четвёртого (ревью 15.08 ×4)
                    node_hits = node_index.lookup(
                        tail, strict=True,
                        known_names=set(voice_names.values()),
                        limit=NODE_CAP + 1)
                except Exception:  # noqa: BLE001
                    node_hits = []
            if len(shown_nodes) < NODE_CAP:
                for node in node_hits:
                    if node.name in shown_nodes:
                        continue
                    lines = node_index.digest(node)
                    if not lines:
                        continue
                    added = thread.add_archive(node.name, lines, into_current=True)
                    if not added:
                        continue
                    shown_nodes.add(node.name)
                    emit({"type": "thread", "text": thread.render()})
                    emit({"type": "status",
                          "text": f"⏮ из графа: {node.name} ({added})"})
                    append_hint(tr.path,
                                f"[{dt.datetime.now():%H:%M}] ⏮ авто: {node.name}",
                                "\n".join(lines))
                    break   # одна вставка за такт: нить не заливается

            try:
                import requests as _rq
                _folder = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser().name
                v = _rq.post("http://127.0.0.1:8100/vault_search",
                             json={"query": query, "limit": 4, "folder": _folder,
                                   "snippet_chars": 500}, timeout=6).json().get("text", "")
            except Exception:  # noqa: BLE001
                # brain лежит — память собирается из узлов графа (ревью
                # 15.08): деградация мягкая, а не «архива нет вовсе»
                v = ""
            if not v or v.startswith("⚠") or "не найдено" in v.lower():
                fallback = "\n".join(
                    ln for n in (node_hits or []) for ln in node_index.digest(n))
                if not fallback:
                    continue   # ни brain, ни узлов — не портим то, что есть
                v = "Из узлов графа проекта:\n" + fallback
            llm.system = (system_base +
                          "\n\nПамять прошлых встреч (подобрано по теме идущей "
                          "встречи; договорённости и решения оттуда можно "
                          "упоминать как прошлые):\n" + v[:2600])
            topic = query.split(",")[0][:60]
            emit({"type": "status", "text": f"🧠 Контекст по теме «{topic}»: архив подтянут"})

    def autostop_loop():
        """Останавливает забытую запись: тишина или потолок длительности.

        17.08 запись шла 18 ч 25 мин в пустой комнате. Правило и пороги — в
        src/autostop.py (там же, почему считаем по распознанной речи, а не по
        громкости, и почему у встречи с двумя голосами порог втрое больше).

        Останавливаемся НЕ сами: просим приложение — событие `autostop`, оно
        отвечает командой `stop`, и дальше идёт ровно тот же путь, что по
        кнопке «Стоп» (финализация .pcm → .wav, пересборка, граф). Своим
        `stop.set()` пользуемся только без UI (запуск из терминала): старое
        приложение сочло бы самостоятельный выход демона крахом записи и
        подняло бы новую встречу — вместо одной забытой записи получились бы
        три пустых.
        """
        limits = autostop_rules.limits_from_cfg(cfg)
        if not limits.any_rule:
            return
        # Приложение подключает демона пайпом; терминал и /dev/null — это
        # запуск без UI, где команду `stop` слать некому. isatty() этого не
        # различает: `python daemon.py < /dev/null` — не терминал и не UI.
        try:
            ui_attached = stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode)
        except OSError:
            ui_attached = False
        ACK_S = 90.0
        watch = autostop_rules.Watch(limits)
        logged = False
        while not stop.is_set():
            time.sleep(5)
            if stop.is_set():
                return
            now = time.monotonic()
            spoken_at = heard["at"]
            d = watch.tick(now=now,
                           age_s=time.time() - record_started_wall,
                           quiet_s=now - (spoken_at if spoken_at is not None else record_started),
                           spoke=bool(heard["spoke"]),
                           last_speech_at=spoken_at)
            if not d:
                continue
            if d.action == "resumed":
                emit({"type": "status", "text": d.text})
                continue
            if d.action == "warn":
                emit({"type": "autostop_warning", "reason": d.reason,
                      "text": d.text, "seconds": round(d.seconds_left)})
                emit({"type": "status", "text": f"⏳ {d.text}"})
                continue
            if stop.is_set():
                return      # человек нажал «Стоп» между решением и просьбой
            emit({"type": "status", "text": f"⏹ Автостоп: {d.text}"})
            emit({"type": "autostop", "reason": d.reason, "text": d.text})
            if not logged:   # повторные просьбы лог не засоряют
                logged = True
                append_hint(tr.path, f"[{dt.datetime.now():%H:%M}] запрошен автостоп", d.text)
            if not ui_attached:
                time.sleep(3)   # дать событию уйти в stdout
                stop.set()
                return
            deadline = time.monotonic() + ACK_S
            while not stop.is_set() and time.monotonic() < deadline:
                time.sleep(1)
            if stop.is_set():
                return
            emit_error("автостоп: приложение не ответило (старая версия?) — "
                       "запись продолжается, остановите её кнопкой «Стоп»")

    threads = [threading.Thread(target=f, daemon=True) for f in (
        stt_loop, think_loop, thread_loop, auto_hint_loop, instant_loop, cloud_loop,
        fast_trigger_loop, deja_vu_loop, dialog_markup_loop, name_loop,
        minutes_loop, deep_loop, live_context_loop, stdin_loop, autostop_loop,
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
            statuses = MeetingStatusStore(ROOT)
            try:
                statuses.processing(tr.path, "waiting_for_audio")
            except Exception:  # статус вспомогателен; встречу всё равно обрабатываем
                pass
            subprocess.Popen(
                ["nice", "-n", "10", sys.executable,
                 str(pathlib.Path(__file__).parent / "rebuild_transcript.py"), str(tr.path)],
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=glog, stderr=subprocess.STDOUT,
            )
            # Не константа «2-4 мин»: живые встречи считаются и по пять минут,
            # и по двадцать — обещание расходилось с правдой в разы, и человек
            # шёл искать поломку там, где всё шло нормально. Берём медиану
            # прошлых обработок с этой самой машины; пока их мало — не обещаем.
            typical = None
            try:
                typical = statuses.typical_duration()
            except Exception:  # noqa: BLE001 — оценка не смеет мешать обработке
                pass
            how_long = (f"~{max(1, round(typical / 60))} мин" if typical
                        else "обычно несколько минут")
            emit({"type": "status",
                  "text": f"Финальная стенограмма и граф: фоном ({how_long})"})
        except Exception as e:  # noqa: BLE001 — UI должен показать, что фон не стартовал
            try:
                MeetingStatusStore(ROOT).failed(tr.path, f"не удалось запустить обработку: {e}")
            except Exception:
                pass
        hub.stop()  # финализирует записи .pcm → .wav — их и ждёт rebuild
        emit({"type": "status", "text": f"Стенограмма: {tr.path}"})


if __name__ == "__main__":
    main()
