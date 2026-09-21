"""Голосовая заметка и дневник: запись до EOF stdin → GigaAM STT → qwen
причёсывает → .md в граф → remember в Чароит.

Режимы:
  (без флагов)  заметка → Заметки/<дата>_<слаг>.md (заголовок, задачи)
  --diary       дневник → <diary_dir>/YYYY-MM-DD.md, дозапись секцией
                «## HH:MM»: голос от первого лица, идеи, задачи-чекбоксы,
                «Как сказано»; если мысль о сегодняшней встрече — ссылка
                на неё (backlink свяжет сферы, текст остаётся в дневнике)
  --text        текст заметки читается из stdin вместо микрофона+STT —
                отладка и тесты без аудиотракта

stdout: JSON {"title": ..., "path": ...} — его читает Чароит.app.
Протокол записи тот же, что у dictate.py: пишем звук, пока Swift не закроет
stdin (EOF = стоп). STT греется параллельно записи.
"""
from __future__ import annotations

import datetime as dt
import faulthandler
import json
import pathlib
import re
import sys
import threading

import numpy as np
import requests

from charoite_paths import code_root, harden_umask, resolve_root
from config_loader import load_user_or_example

CODE = code_root(__file__)


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень (замер 21.09, №329).
    """
    return resolve_root(__file__)

SR = 16000

import graphs  # noqa: E402

_cfg_кэш: tuple[pathlib.Path, dict] | None = None


def cfg(корень: pathlib.Path | None = None) -> dict:
    """Конфиг владельца — по корню НА ВЫЗОВЕ, с кэшем, у которого есть отзыв.

    Кэш отличается от снимка одним: у него есть канал отзыва. Ключ — корень
    данных, поэтому смена названного корня (`use_data_root`) сама делает
    прежнее значение недействительным, а чтение с диска не повторяется на
    каждое обращение (№329).
    """
    global _cfg_кэш
    корень = корень or _root()
    if _cfg_кэш is None or _cfg_кэш[0] != корень:
        _cfg_кэш = (корень, load_user_or_example(корень))
    return _cfg_кэш[1]


def _graph() -> pathlib.Path:
    """Где граф — спрашиваем на вызове, а не запоминаем на импорте.

    Снимок на импорте считался раньше, чем тест (или приложение) успевал
    назвать граф: процесс держал путь владельца до конца прогона, а изоляция
    окружения его уже не догоняла — заметка и дневник уезжали в живой граф
    (круг 11 по коду №327, DS C1). Пустой путь — граф не настроен, как и был.
    """
    return graphs.graph_dir(cfg()) or pathlib.Path("")
# Модель и адрес — из llm.py по конфигу, а не свои: прежний хардкод читал
# несуществующий ключ sufler.model и после переезда конфига на mlx-сборку
# продолжал звать старую модель (аудит 14.08).
from llm import LLM, parse_json_block  # noqa: E402

_llm_кэш: tuple[pathlib.Path, LLM] | None = None


def _llm() -> LLM:
    """Движок — на вызове и по тому же ключу, что конфиг (№329)."""
    global _llm_кэш
    корень = _root()                     # один снимок на ключ И на значение:
    if _llm_кэш is None or _llm_кэш[0] != корень:
        _llm_кэш = (корень, LLM(cfg(корень)))   # иначе ключ от одного корня, конфиг от другого
                                                # (круг 1 по коду №329, DS I2)
    return _llm_кэш[1]

import os  # noqa: E402

import meeting_stamp  # noqa: E402
import safe_write  # noqa: E402

# Дневник — отдельная граф-сфера РЯДОМ с рабочей (личное не всплывает в
# рабочем поиске), но в том же Obsidian-vault: ссылки и backlinks между
# сферами работают нативно. env — для тестов.
def diary_dir() -> pathlib.Path:
    raw = os.environ.get("SUFLER_DIARY_DIR") or cfg()["sufler"].get("diary_dir", "")
    if raw:
        return pathlib.Path(raw).expanduser()
    return _graph().parent / "Дневник"


def _moment() -> dt.datetime:
    """Момент заметки: `--moment "YYYY-MM-DD HH:MM"` от импорта голосовых заметок
    (время записи на телефоне), иначе сейчас. До 13.09 заметка вчерашнего вечера
    ложилась в сегодняшний дневник под временем синка (GLM I3 / DS M4)."""
    if "--moment" in sys.argv:
        i = sys.argv.index("--moment") + 1
        raw = sys.argv[i] if i < len(sys.argv) else ""
        try:
            return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M")
        except ValueError:
            print(f"--moment «{raw}» не разобран — беру текущее время", file=sys.stderr)
    return dt.datetime.now()


def last_meeting_today(day: str | None = None) -> tuple[str, str] | None:
    """(stamp, тема) последней стенограммы дня — кандидат на связь. `day` —
    день записи (`--moment`), иначе сегодня: заметка вчерашнего вечера иначе
    искала встречу среди сегодняшних (DS/GLM r1 по #559)."""
    tdir = pathlib.Path(os.environ.get("SUFLER_TRANSCRIPTS_DIR")
                        or _root() / cfg()["log"]["transcripts_dir"])
    if not tdir.exists():
        return None
    today = day or f"{dt.datetime.now():%Y-%m-%d}"
    # Только главные файлы встреч: список производных знает meeting_stamp
    # (`_разбор`, `_ревизия_claude`, `_спикеры` тоже) — раньше три исключения
    # руками, и ссылка дневника вела в файл разбора (аудит DeepSeek 17.08).
    cands = sorted(p for p in tdir.glob(f"{today}_*.md") if meeting_stamp.stamp_of(p.stem))
    if not cands:
        return None
    # Ключ графа, не стем файла: заметка встречи называется минутным штампом
    # (`Встречи/<штамп>.md`), а стем после наката темы — «<штамп>_Тема»; ссылка
    # по стему висела в пустоте после любого наката (аудит 13.09, DS I3 / GLM I2)
    # graph_dir(cfg()) даёт None при незаданном графе; _graph() тогда Path("") = ".", и
    # ключ решался бы по чужому ./Встречи относительно CWD (DS/GLM r1 по #559)
    stamp = meeting_stamp.graph_key(tdir, cands[-1].stem, graphs.graph_dir(cfg()))
    first = cands[-1].read_text(encoding="utf-8").splitlines()[:1]
    topic = first[0].lstrip("# ").strip() if first else stamp
    # «# Встреча <stamp> — Тема» → только тема
    if "—" in topic:
        topic = topic.split("—", 1)[1].strip()
    return stamp, topic



def _record_and_transcribe(warm_t: threading.Thread, stt_holder: dict) -> str | None:
    """Ветка микрофона: пишем до EOF от Swift и распознаём. None — распознавать нечего."""
    frames: list[np.ndarray] = []

    def cb(indata, *_):
        frames.append(indata.copy())

    # Импорт здесь, а не наверху: PortAudio на машине без звуковых
    # устройств роняет процесс при ВЫХОДЕ («terminate called without an
    # active exception», код -6) — дневник успевал сделать работу и
    # напечатать ответ, а падал уже на закрытии. Диктовке микрофон нужен,
    # дневнику с текстом — нет, и он больше не платит за чужую библиотеку
    # (флейк CI, №122).
    import sounddevice as sd

    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", callback=cb):
        print("REC", file=sys.stderr, flush=True)  # Swift ловит: запись пошла
        sys.stdin.buffer.read()  # EOF от Swift = стоп

    audio = np.concatenate(frames)[:, 0] if frames else np.zeros(0, dtype="float32")
    if len(audio) < SR * 0.4:
        return None
    warm_t.join(timeout=60)
    stt = stt_holder.get("stt")
    if stt is None:
        print("STT не загрузился", file=sys.stderr)
        sys.exit(1)
    raw = stt.transcribe(audio, SR).strip()
    return raw or None


def main():
    harden_umask()  # данные встреч — только владельцу (аудит 16.08)
    faulthandler.enable()  # следующий SIGABRT оставит стек, а не одну цифру -6 (№174)
    diary = "--diary" in sys.argv
    text_mode = "--text" in sys.argv
    stt_holder: dict = {}

    def warm():
        sys.path.insert(0, str(CODE / "src"))
        from stt import STT
        stt_holder["stt"] = STT(cfg())

    if text_mode:
        # Текстовому режиму STT не нужен — и прогревать его нельзя: daemon-поток
        # грузил onnxruntime/GigaAM в фоне, а интерпретатор выходил раньше, чем
        # тот заканчивал, — и нативные библиотеки падали на выходе с SIGABRT
        # («terminate called without an active exception», код -6). Работа была
        # сделана, ответ напечатан, а returncode -6 красил CI через раз
        # (05.09 — #499 и #503, №174).
        raw = sys.stdin.read().strip()
        if not raw:
            return
    else:
        warm_t = threading.Thread(target=warm, daemon=True)
        warm_t.start()
        try:
            raw = _record_and_transcribe(warm_t, stt_holder)
        finally:
            # Любой выход из ветки микрофона — короткая запись, отмена,
            # ошибка — дожидается прогрева: брошенный посреди нативного init
            # daemon-поток ронял бы процесс на выходе тем же SIGABRT (GLM r1
            # по №174). Потолок — чтобы зависший onnxruntime не держал выход.
            warm_t.join(timeout=60)
        if raw is None:
            return

    if diary:
        diary_entry(raw)
        return

    # qwen: заголовок + причёсанный текст + задачи. Фолбэк — сырой текст.
    title, body, tasks = "", raw, []
    try:
        content = _llm().complete(
            "Это голосовая заметка (сырой текст с распознавания речи). Верни ТОЛЬКО JSON:\n"
            '{"заголовок":"2-3 слова","текст":"тот же текст, но с пунктуацией и абзацами, '
            'ничего не выдумывай и не сокращай","задачи":["..."]}\n'
            "Задачи — только если в заметке есть явные «надо/сделать/не забыть», иначе [].\n\n"
            f"Заметка:\n{raw}",
            think=False, temperature=0.2, num_predict=1200, num_ctx=8192,
            timeout=90)
        data = parse_json_block(content)
        if data:
            title = " ".join(str(data.get("заголовок", "")).split()[:3]).strip('",;: ')
            body = str(data.get("текст", "")).strip() or raw
            tasks = [str(t) for t in data.get("задачи", []) if str(t).strip()]
    except Exception as e:  # noqa: BLE001 — обработка вспомогательна, заметка важнее
        print(f"qwen обработка: {e}", file=sys.stderr)
    if not title:
        words = re.findall(r"[А-Яа-яЁёA-Za-z0-9-]+", raw)
        title = " ".join(words[:3]) or "заметка"

    now = _moment()
    ndir = _graph() / "Заметки"
    ndir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\wА-Яа-яЁё-]+", "_", title).strip("_")[:40]
    path = ndir / f"{now:%Y-%m-%d_%H%M}_{slug}.md"
    parts = [
        f"---\ntype: voice-note\ndate: {now:%Y-%m-%d %H:%M}\n---\n",
        f"# {title}\n",
        body + "\n",
    ]
    if tasks:
        parts.append("\n## Задачи\n" + "\n".join(f"- [ ] {t}" for t in tasks) + "\n")
    parts.append(f"\n## Как сказано\n> {raw}\n")
    # две заметки в минуту с одним заголовком: вторая молча затирала первую вместе с
    # «Как сказано» (аудит 13.09, GLM I1 / DS M5). Имя занимается эксклюзивным созданием
    # (safe_write.claim, O_EXCL): две одновременные диктовки разводятся по «-2» без окна
    # гонки, которое оставлял expect_absent (DS r2 M2 / GLM r2 M4 по #559)
    n = 2
    while not safe_write.claim(path):
        path = ndir / f"{now:%Y-%m-%d_%H%M}_{slug}-{n}.md"
        n += 1
    safe_write.write_text(path, "\n".join(parts))

    # оглавление заметок — свежие сверху
    moc = ndir / "_ЗАМЕТКИ.md"
    notes = sorted((p for p in ndir.glob("*.md") if not p.name.startswith("_")), reverse=True)
    safe_write.write_text(moc,
        "# Голосовые заметки\n\n" +
        "\n".join(f"- [[Заметки/{p.stem}|{p.stem[16:].replace('_', ' ') or p.stem}]] — {p.stem[:15].replace('_', ' ')}"
                  for p in notes) + "\n")

    # память Чароита: заметка находима через recall
    try:
        requests.post("http://127.0.0.1:8100/remember", json={
            "text": f"Голосовая заметка {now:%d.%m} «{title}»: {body[:300]}",
            "category": "voice_note",
        }, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"remember: {e}", file=sys.stderr)

    print(json.dumps({"title": title, "path": str(path)}, ensure_ascii=False))


def diary_entry(raw: str) -> None:
    """Дневниковая запись: причесать голосом автора и дозаписать в день."""
    now = _moment()
    meeting = last_meeting_today(f"{now:%Y-%m-%d}")

    # qwen: первое лицо, идеи, задачи, флаг связи со встречей. Ссылку
    # строим МЫ по флагу — модель не выдумывает пути.
    body, ideas, tasks, about_meeting = raw, [], [], False
    meet_hint = (f"В этот день ({now:%Y-%m-%d}) была встреча «{meeting[1]}». " if meeting else "")
    try:
        content = _llm().complete(
            "Это надиктованная дневниковая запись (сырой текст с распознавания). "
            + meet_hint +
            "Верни ТОЛЬКО JSON:\n"
            '{"текст":"тот же текст от первого лица: пунктуация и абзацы, ход мысли '
            'и интонацию сохранить, ничего не выдумывать и не сокращать",'
            '"идеи":["отдельные идеи, если прозвучали"],'
            '"задачи":["явные надо/сделать/не забыть"],'
            '"о_встрече":true/false}\n'
            "о_встрече = true только если запись явно про сегодняшнюю встречу.\n\n"
            f"Запись:\n{raw}",
            think=False, temperature=0.2, num_predict=1600, num_ctx=8192,
            timeout=120)
        data = parse_json_block(content)
        if data:
            body = str(data.get("текст", "")).strip() or raw
            ideas = [str(i).strip() for i in data.get("идеи", []) if str(i).strip()]
            tasks = [str(x).strip() for x in data.get("задачи", []) if str(x).strip()]
            about_meeting = bool(data.get("о_встрече")) and meeting is not None
    except Exception as e:  # noqa: BLE001 — обработка вспомогательна, запись важнее
        print(f"qwen дневник: {e}", file=sys.stderr)

    ddir = diary_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    day = ddir / f"{now:%Y-%m-%d}.md"
    if not day.exists():
        safe_write.write_text(day, f"---\ntype: diary\ndate: {now:%Y-%m-%d}\n---\n"
                                   f"# Дневник {now:%Y-%m-%d}\n", expect_absent=True)

    parts = [f"\n## {now:%H:%M}\n", body + "\n"]
    if ideas:
        parts.append("\n**Идеи**\n" + "\n".join(f"- {i}" for i in ideas) + "\n")
    if tasks:
        parts.append("\n**Задачи**\n" + "\n".join(f"- [ ] {x}" for x in tasks) + "\n")
    if about_meeting and meeting:
        stamp, topic = meeting
        # ссылка через имя рабочей сферы: backlink на встрече покажет мысль;
        # сфера не настроена — ссылка без префикса (тот же vault)
        граф = _graph()
        prefix = f"{граф.name}/" if граф.name else ""
        parts.append(f"\nКонтекст: [[{prefix}Встречи/{stamp}|встреча «{topic}»]]\n")
    parts.append(f"\n> Как сказано: {raw}\n")
    with day.open("a", encoding="utf-8") as f:
        f.write("".join(parts))

    try:
        requests.post("http://127.0.0.1:8100/remember", json={
            "text": f"Дневник {now:%d.%m %H:%M}: {body[:300]}",
            "category": "diary",
        }, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"remember: {e}", file=sys.stderr)

    print(json.dumps({"title": f"дневник {now:%H:%M}", "path": str(day)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
