"""Голосовая заметка: запись до EOF stdin → GigaAM STT → qwen причёсывает
(заголовок, пунктуация, задачи) → .md в граф (Заметки/) → remember в Чароит.

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


def main():
    stt_holder: dict = {}

    def warm():
        sys.path.insert(0, str(ROOT / "src"))
        from stt import STT
        stt_holder["stt"] = STT(cfg)

    warm_t = threading.Thread(target=warm, daemon=True)
    warm_t.start()

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


if __name__ == "__main__":
    main()
