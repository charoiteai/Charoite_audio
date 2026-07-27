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
import json
import pathlib
import re
import sys
import threading

import numpy as np
import requests
import sounddevice as sd
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

SR = 16000

cfg = yaml.safe_load(_cfg_text(ROOT))
GRAPH = pathlib.Path(cfg["sufler"].get("graph_dir", "")).expanduser()
OLLAMA = "http://127.0.0.1:11434"
MODEL = cfg["sufler"].get("model", "qwen3.6:35b-a3b")

import os  # noqa: E402

# Дневник — отдельная граф-сфера РЯДОМ с рабочей (личное не всплывает в
# рабочем поиске), но в том же Obsidian-vault: ссылки и backlinks между
# сферами работают нативно. env — для тестов.
def diary_dir() -> pathlib.Path:
    raw = os.environ.get("SUFLER_DIARY_DIR") or cfg["sufler"].get("diary_dir", "")
    if raw:
        return pathlib.Path(raw).expanduser()
    return GRAPH.parent / "Дневник"


def last_meeting_today() -> tuple[str, str] | None:
    """(stamp, тема) последней сегодняшней стенограммы — кандидат на связь."""
    tdir = pathlib.Path(os.environ.get("SUFLER_TRANSCRIPTS_DIR")
                        or ROOT / cfg["log"]["transcripts_dir"])
    if not tdir.exists():
        return None
    today = f"{dt.datetime.now():%Y-%m-%d}"
    cands = sorted(p for p in tdir.glob(f"{today}_*.md")
                   if "_minutes" not in p.name and "_hints" not in p.name
                   and "_live" not in p.name)
    if not cands:
        return None
    stamp = cands[-1].stem
    first = cands[-1].read_text(encoding="utf-8").splitlines()[:1]
    topic = first[0].lstrip("# ").strip() if first else stamp
    # «# Встреча <stamp> — Тема» → только тема
    if "—" in topic:
        topic = topic.split("—", 1)[1].strip()
    return stamp, topic


def main():
    diary = "--diary" in sys.argv
    text_mode = "--text" in sys.argv
    stt_holder: dict = {}

    def warm():
        sys.path.insert(0, str(ROOT / "src"))
        from stt import STT
        stt_holder["stt"] = STT(cfg)

    warm_t = threading.Thread(target=warm, daemon=True)
    warm_t.start()

    if text_mode:
        raw = sys.stdin.read().strip()
        if not raw:
            return
    else:
        frames: list[np.ndarray] = []

        def cb(indata, *_):
            frames.append(indata.copy())

        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", callback=cb):
            print("REC", file=sys.stderr, flush=True)  # Swift ловит: запись пошла
            sys.stdin.buffer.read()  # EOF от Swift = стоп

        audio = np.concatenate(frames)[:, 0] if frames else np.zeros(0, dtype="float32")
        if len(audio) < SR * 0.4:
            return
        warm_t.join(timeout=60)
        stt = stt_holder.get("stt")
        if stt is None:
            print("STT не загрузился", file=sys.stderr)
            sys.exit(1)
        raw = stt.transcribe(audio, SR).strip()
        if not raw:
            return

    if diary:
        diary_entry(raw)
        return

    # qwen: заголовок + причёсанный текст + задачи. Фолбэк — сырой текст.
    title, body, tasks = "", raw, []
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json={
            "model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "user", "content":
                "Это голосовая заметка (сырой текст с распознавания речи). Верни ТОЛЬКО JSON:\n"
                '{"заголовок":"2-3 слова","текст":"тот же текст, но с пунктуацией и абзацами, '
                'ничего не выдумывай и не сокращай","задачи":["..."]}\n'
                "Задачи — только если в заметке есть явные «надо/сделать/не забыть», иначе [].\n\n"
                f"Заметка:\n{raw}"}],
            "options": {"temperature": 0.2, "num_predict": 1200, "num_ctx": 8192},
        }, timeout=90)
        content = r.json().get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            title = " ".join(str(data.get("заголовок", "")).split()[:3]).strip('",;: ')
            body = str(data.get("текст", "")).strip() or raw
            tasks = [str(t) for t in data.get("задачи", []) if str(t).strip()]
    except Exception as e:  # noqa: BLE001 — обработка вспомогательна, заметка важнее
        print(f"qwen обработка: {e}", file=sys.stderr)
    if not title:
        words = re.findall(r"[А-Яа-яЁёA-Za-z0-9-]+", raw)
        title = " ".join(words[:3]) or "заметка"

    now = dt.datetime.now()
    ndir = GRAPH / "Заметки"
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
    path.write_text("\n".join(parts), encoding="utf-8")

    # оглавление заметок — свежие сверху
    moc = ndir / "_ЗАМЕТКИ.md"
    notes = sorted((p for p in ndir.glob("*.md") if not p.name.startswith("_")), reverse=True)
    moc.write_text(
        "# Голосовые заметки\n\n" +
        "\n".join(f"- [[Заметки/{p.stem}|{p.stem[17:].replace('_', ' ') or p.stem}]] — {p.stem[:16].replace('_', ' ')}"
                  for p in notes) + "\n",
        encoding="utf-8")

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
    now = dt.datetime.now()
    meeting = last_meeting_today()

    # qwen: первое лицо, идеи, задачи, флаг связи со встречей. Ссылку
    # строим МЫ по флагу — модель не выдумывает пути.
    body, ideas, tasks, about_meeting = raw, [], [], False
    meet_hint = (f"Сегодня была встреча «{meeting[1]}». " if meeting else "")
    try:
        r = requests.post(f"{OLLAMA}/api/chat", json={
            "model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "user", "content":
                "Это надиктованная дневниковая запись (сырой текст с распознавания). "
                + meet_hint +
                "Верни ТОЛЬКО JSON:\n"
                '{"текст":"тот же текст от первого лица: пунктуация и абзацы, ход мысли '
                'и интонацию сохранить, ничего не выдумывать и не сокращать",'
                '"идеи":["отдельные идеи, если прозвучали"],'
                '"задачи":["явные надо/сделать/не забыть"],'
                '"о_встрече":true/false}\n'
                "о_встрече = true только если запись явно про сегодняшнюю встречу.\n\n"
                f"Запись:\n{raw}"}],
            "options": {"temperature": 0.2, "num_predict": 1600, "num_ctx": 8192},
        }, timeout=120)
        content = r.json().get("message", {}).get("content", "")
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
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
        day.write_text(f"---\ntype: diary\ndate: {now:%Y-%m-%d}\n---\n"
                       f"# Дневник {now:%Y-%m-%d}\n", encoding="utf-8")

    parts = [f"\n## {now:%H:%M}\n", body + "\n"]
    if ideas:
        parts.append("\n**Идеи**\n" + "\n".join(f"- {i}" for i in ideas) + "\n")
    if tasks:
        parts.append("\n**Задачи**\n" + "\n".join(f"- [ ] {x}" for x in tasks) + "\n")
    if about_meeting and meeting:
        stamp, topic = meeting
        # ссылка через имя рабочей сферы: backlink на встрече покажет мысль;
        # сфера не настроена — ссылка без префикса (тот же vault)
        prefix = f"{GRAPH.name}/" if GRAPH.name else ""
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
