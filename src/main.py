"""Суфлёр v1: слушает встречу, транскрибирует, подсказывает по Enter. Всё локально.

Запуск:  .venv/bin/python src/main.py   (из корня репозитория)
Клавиши: Enter — подсказка · s — саммари · d — устройства · q — выход
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402

import fact_check  # noqa: E402
from audio import AudioHub, list_devices  # noqa: E402
from llm import LLM  # noqa: E402
from stt import STT  # noqa: E402
from transcript import NOISE, Transcript  # noqa: E402

from charoite_paths import harden_umask, resolve_root

ROOT = resolve_root(__file__)
console = Console()

def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))


def stt_loop(hub: AudioHub, stt: STT, tr: Transcript, stop: threading.Event):
    import time

    while not stop.is_set():
        chunk = hub.pull()
        if chunk is None:
            time.sleep(0.1)
            continue
        if not hub.is_speech(chunk):
            continue
        try:
            text = stt.transcribe(chunk, hub.sr)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]STT: {e}[/red]")
            continue
        if not text or text.lower().strip(" .!») ") in NOISE:
            continue
        tr.add(text)
        console.print(f"[dim]{dt.datetime.now():%H:%M:%S}[/dim] {text}")


def main():
    harden_umask()  # данные встреч — только владельцу (аудит 16.08)
    cfg = load_cfg()
    console.print(Panel.fit("[bold]Суфлёр v1[/bold] — локально, ничего не покидает машину", style="cyan"))

    console.print("[dim]Загружаю STT…[/dim]")
    stt = STT(cfg)
    llm = LLM(cfg)
    model = llm.resolve_model()
    # Один штамп на запись и стенограмму — как в демоне (daemon.py): иначе на
    # границе секунды файлы каналов и .md расходятся именами, и пересборка
    # запись не находит (контракт meeting_stamp; аудит DeepSeek 16.08).
    tr = Transcript(ROOT / cfg["log"]["transcripts_dir"])
    hub = AudioHub(cfg, stamp=tr.stamp)

    console.print(f"Аудио: [green]{' + '.join(hub.sources)}[/green] · STT: [green]{cfg['stt']['backend']}[/green] · LLM: [green]{model}[/green]")
    console.print("[dim]Прогреваю LLM (первая подсказка будет быстрой)…[/dim]")
    threading.Thread(target=llm.warmup, daemon=True).start()
    console.print(f"[dim]{cfg['sufler']['hotkey_hint']} · стенограмма: {tr.path}[/dim]\n")

    stop = threading.Event()
    hub.start()
    threading.Thread(target=stt_loop, args=(hub, stt, tr, stop), daemon=True).start()

    max_ctx = int(cfg["llm"]["max_context_chars"])
    try:
        while True:
            cmd = input().strip().lower()
            if cmd == "q":
                break
            if cmd == "d":
                for d in list_devices():
                    console.print(f"  [{d['index']}] {d['name']} (in:{d['in']})")
                continue
            if cmd == "s":
                console.print(Panel.fit("Саммари", style="yellow"))
                full = tr.full() or "(пусто)"
                parts: list[str] = []
                for tok in llm.summary(full):
                    parts.append(tok)
                    console.print(tok, end="")
                console.print("\n")
                bad = fact_check.unanchored("".join(parts), full)
                if bad:
                    console.print(f"[red]⚠️ Нет в стенограмме: {', '.join(bad)}[/red]\n")
                continue
            # Enter (или любой другой ввод) — подсказка
            tail = tr.tail(max_ctx)
            if not tail:
                console.print("[yellow]Стенограмма пока пуста.[/yellow]")
                continue
            console.print(Panel.fit("Подсказка", style="green"))
            for tok in llm.hint(tail):
                console.print(tok, end="")
            console.print("\n")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        hub.stop()
        console.print(f"\n[dim]Стенограмма сохранена: {tr.path}[/dim]")


if __name__ == "__main__":
    main()
