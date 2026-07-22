"""Суфлёр v1: слушает встречу, транскрибирует, подсказывает по Enter. Всё локально.

Запуск:  cd ~/Project/sufler && ./.venv/bin/python src/main.py
Клавиши: Enter — подсказка · s — саммари · d — устройства · q — выход
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import threading

import yaml
from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import fact_check  # noqa: E402
from audio import AudioHub, list_devices  # noqa: E402
from llm import LLM  # noqa: E402
from stt import STT  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
console = Console()

# Мусор, который whisper галлюцинирует на тишине/шуме
NOISE = {"продолжение следует...", "субтитры делал dimatorzok", "спасибо за просмотр!", "спасибо за просмотр"}


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))


class Transcript:
    """Стенограмма репликами по спикерам: соседние чанки одного голоса склеиваются.

    Файл перезаписывается целиком (реплики + заметки ко-мышления в конце).
    """

    # Пока говорит тот же спикер — клеим в ОДИН абзац; новый блок только после
    # смены спикера или совсем длинной паузы (иначе стенограмма рвётся на строчки).
    SPLIT_GAP = 180.0

    def __init__(self, out_dir: pathlib.Path):
        out_dir.mkdir(exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
        self.path = out_dir / f"{stamp}.md"
        self._title = f"# Встреча {stamp}"
        # блок: [t_start, t_last, speaker, text]
        self._blocks: list[list] = []
        self._notes: list[str] = []
        self._names: dict[str, str] = {}  # канальная метка → опознанное имя
        self._prev_chunk: dict[str, str] = {}  # спикер → последний чанк (дедуп швов)
        self._participants: list[str] = []  # групповая встреча: кто звучал
        self._lock = threading.Lock()
        self._save()

    @staticmethod
    def _cut_overlap(prev: str, new: str) -> str:
        """Режет повтор шва: чанки идут с перекрытием, хвост предыдущего = началу нового."""
        pw = prev.lower().replace(",", "").replace(".", "").split()
        nw = new.lower().replace(",", "").replace(".", "").split()
        for k in range(min(8, len(pw), len(nw)), 1, -1):
            if pw[-k:] == nw[:k]:
                return " ".join(new.split()[k:])
        return new

    def add(self, text: str, speaker: str | None = None) -> str | None:
        """Добавляет чанк; возвращает реально добавленный текст (после дедупа) или None."""
        now = dt.datetime.now()
        spk = speaker or "—"
        with self._lock:
            spk = self._names.get(spk, spk)
            # шов перекрытия чанков живёт ВНУТРИ канала: сверяем с последним текстом
            # этого же спикера, а не с чужим блоком (иначе дубль слов на смене голоса)
            prev = self._prev_chunk.get(spk, "")
            if prev:
                text = self._cut_overlap(prev, text)
                if not text:
                    return None
            self._prev_chunk[spk] = text
            if self._blocks:
                b = self._blocks[-1]
                same = b[2] == spk and (now - b[1]).total_seconds() < self.SPLIT_GAP
                if same:
                    b[3] = f"{b[3]} {text}"
                    b[1] = now
                else:
                    self._blocks.append([now, now, spk, text])
            else:
                self._blocks.append([now, now, spk, text])
        self._save()
        return text

    def note(self, line: str):
        """Заметка ко-мышления (📌/💎/💭) — в конец файла, отдельным разделом."""
        with self._lock:
            self._notes.append(f"{dt.datetime.now():%H:%M} {line}")
        self._save()

    def display_name(self, speaker: str) -> str:
        with self._lock:
            return self._names.get(speaker, speaker)

    def rename_speaker(self, old: str, new: str):
        """Опознали имя из разговора: заменить метку задним числом во всех блоках."""
        with self._lock:
            self._names[old] = new
            for b in self._blocks:
                if b[2] == old:
                    b[2] = new
        self._save()

    def set_participants(self, names: list[str]):
        """Групповая встреча: список звучавших имён — в шапку стенограммы."""
        with self._lock:
            self._participants = list(names)
        self._save()

    def _render(self) -> str:
        parts = [self._title]
        if self._participants:
            parts.append(f"Участники (звучали в разговоре): {', '.join(self._participants)}")
        parts.append("")
        for t0, t1, spk, text in self._blocks:
            span = f"{t0:%H:%M}" if f"{t0:%H:%M}" == f"{t1:%H:%M}" else f"{t0:%H:%M}–{t1:%H:%M}"
            parts.append(f"**{spk}** [{span}]:")
            parts.append(text)
            parts.append("")
        if self._notes:
            parts.append("---")
            parts.append("## Ко-мышление (📌 КТ · 💎 факты · 💭 мысли)")
            parts.extend(f"> {n}" for n in self._notes)
        return "\n".join(parts) + "\n"

    def _save(self):
        # Под локом целиком: _save дёргают 4 треда (stt/think/deep/name), а tmp-путь
        # один — конкурентные write_text мешали байты, второй replace ловил
        # FileNotFoundError и убивал stt_loop (профиль инцидента «стенограмма молчит»)
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self._render(), encoding="utf-8")
            tmp.replace(self.path)

    def tail(self, max_chars: int) -> str:
        with self._lock:
            out: list[str] = []
            total = 0
            for t0, _t1, spk, text in reversed(self._blocks):
                s = f"[{t0:%H:%M}] {spk}: {text}"
                total += len(s) + 1
                if total > max_chars:
                    if not out:  # монолог длиннее лимита: отдать усечённый хвост,
                        out.append(s[-max_chars:])  # иначе ⚡/☁️ молча глотали вопрос
                    break
                out.append(s)
        return "\n".join(reversed(out))

    def full(self) -> str:
        with self._lock:
            return "\n".join(f"[{t0:%H:%M}] {spk}: {text}" for t0, _t1, spk, text in self._blocks)

    def last(self) -> str:
        with self._lock:
            return self._blocks[-1][3] if self._blocks else ""

    def last_block(self) -> tuple[int, dt.datetime, str, str] | None:
        """(index, t_last, спикер, текст) последнего блока — для семантической разметки."""
        with self._lock:
            if not self._blocks:
                return None
            i = len(self._blocks) - 1
            _t0, t1, spk, text = self._blocks[i]
            return i, t1, spk, text

    def update_block_text(self, idx: int, old_text: str, new_text: str) -> bool:
        """Заменить текст блока, только если он не дописался с момента снапшота."""
        with self._lock:
            if 0 <= idx < len(self._blocks) and self._blocks[idx][3] == old_text:
                self._blocks[idx][3] = new_text
                ok = True
            else:
                ok = False
        if ok:
            self._save()
        return ok

    def notes(self) -> list[str]:
        with self._lock:
            return list(self._notes)

    def names(self) -> dict[str, str]:
        """Опознанные за встречу имена: «Собеседник N» → «Алексей».

        Нужны пересборке: без них rebuild диаризует заново и заново гадает
        имена, теряя всё, что демон выяснил за час разговора.
        """
        with self._lock:
            return dict(self._names)


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
    cfg = load_cfg()
    console.print(Panel.fit("[bold]Суфлёр v1[/bold] — локально, ничего не покидает машину", style="cyan"))

    console.print("[dim]Загружаю STT…[/dim]")
    stt = STT(cfg)
    llm = LLM(cfg)
    model = llm.resolve_model()
    hub = AudioHub(cfg)
    tr = Transcript(ROOT / cfg["log"]["transcripts_dir"])

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
