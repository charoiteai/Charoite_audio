"""Захват аудио: микрофон и/или BlackHole (системный звук), кольцевой буфер."""
from __future__ import annotations

import datetime as dt
import pathlib
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd

ROOT = pathlib.Path(__file__).resolve().parent.parent


def list_devices() -> list[dict]:
    return [
        {"index": i, "name": d["name"], "in": d["max_input_channels"], "sr": d["default_samplerate"]}
        for i, d in enumerate(sd.query_devices())
    ]


def find_device(substr: str) -> int | None:
    for i, d in enumerate(sd.query_devices()):
        if substr.lower() in d["name"].lower() and d["max_input_channels"] > 0:
            return i
    return None


class Capture:
    """Один входной поток → очередь float32-чанков (mono, samplerate)."""

    def __init__(self, device_index: int | None, samplerate: int, label: str):
        self.device = device_index
        self.samplerate = samplerate
        self.label = label
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None

    def _cb(self, indata, frames, time_info, status):  # noqa: ANN001
        if status:
            pass  # over/underflow не критичны для суфлёра
        self.q.put(indata[:, 0].copy())

    def start(self):
        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.samplerate,
            dtype="float32",
            blocksize=int(self.samplerate * 0.25),
            callback=self._cb,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()

    def restart(self):
        """Пересоздать InputStream: PortAudio-стрим умирает молча под CPU-голоданием
        (встреча 20.07 — демон жив, кадров нет), сам он не восстанавливается."""
        try:
            self.stop()
        except Exception:  # noqa: BLE001 — мёртвый стрим может не закрыться
            pass
        self._stream = None
        self.start()


class AudioHub:
    # Подписка на сырые фреймы (для быстрого триггера gigastt): callback(source, float32[])
    on_frame = None
    # Статусы для UI (рестарт стрима и т.п.): callback(str)
    on_status = None

    """Держит источники (mic = владелец, blackhole = собеседники) РАЗДЕЛЬНО.

    Раздельные каналы дают бесплатную диаризацию «я/они»: pull_labeled()
    отдаёт (speaker, chunk). При одновременной речи в обоих каналах
    микрофонный чанк отбрасывается — это эхо динамиков в микрофоне.
    """

    SPEAKER = {"blackhole": "Собеседник", "mic": "Я"}

    def __init__(self, cfg: dict):
        a = cfg["audio"]
        # метка своего канала — имя владельца из конфига
        own = (cfg.get("sufler", {}).get("user_name") or "").strip()
        if own:
            self.SPEAKER = {**self.SPEAKER, "mic": own}
        self.sr = int(a["samplerate"])
        self.chunk_s = float(a["chunk_seconds"])
        self.overlap_s = float(a["overlap_seconds"])
        self.vad_db = float(a["vad_energy_db"])
        self.record_on = bool(a.get("record", True))
        self.record_keep_days = a.get("record_keep_days", 2)
        self.record_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
        self.captures: list[Capture] = []
        self.sources: list[str] = []
        self._bufs: dict[str, np.ndarray] = {}
        self._sinks: dict = {}          # label → открытый .pcm (сырая запись встречи)
        self._last_frame: dict[str, float] = {}
        self._last_check = 0.0
        self._lock = threading.Lock()
        self._running = False

        mode = a["device"]
        bh = find_device("blackhole")
        mic = sd.default.device[0] if sd.default.device else None

        if mode in ("auto", "mix", "blackhole") and bh is not None:
            self.captures.append(Capture(bh, self.sr, "blackhole"))
            self.sources.append("BlackHole")
        # auto = система И микрофон: на встрече нужны обе стороны разговора
        if mode in ("mic", "mix", "auto") and (mode != "auto" or bh is None or mic is not None):
            if mode != "blackhole":
                self.captures.append(Capture(mic, self.sr, "mic"))
                self.sources.append("Микрофон")
        if not self.captures:  # blackhole запрошен, но не найден
            self.captures.append(Capture(mic, self.sr, "mic"))
            self.sources.append("Микрофон (fallback)")
        for c in self.captures:
            self._bufs[c.label] = np.zeros(0, dtype=np.float32)

    def start(self):
        self._running = True
        if self.record_on:
            self._open_sinks()
        for c in self.captures:
            c.start()
        now = time.time()
        for c in self.captures:
            self._last_frame[c.label] = now
        threading.Thread(target=self._pump, daemon=True).start()

    def stop(self):
        self._running = False
        for c in self.captures:
            try:
                c.stop()
            except Exception:  # noqa: BLE001 — мёртвый PortAudio-стрим виснет на close,
                pass           # не даём ему сорвать финализацию записи и стоп демона
        self._finalize_recordings()

    def _open_sinks(self):
        """Сырое аудио каждого канала — на диск сразу: обрыв STT/демона больше не
        теряет встречу (20.07 потеряли 5+ минут безвозвратно). Пишем .pcm (s16le,
        без заголовка — переживает крэш), штатный стоп финализирует в .wav."""
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            # Запись — только страховка от обрыва транскрипции, не архив: аудио
            # рабочих встреч — чувствительный носитель (из него извлекаются
            # голосовые эмбеддинги), не держим дольше страхового окна.
            keep_days = float(self.record_keep_days)
            cutoff = time.time() - keep_days * 86400
            for old in self.record_dir.iterdir():
                if old.suffix in (".pcm", ".wav") and old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
            stamp = f"{dt.datetime.now():%Y-%m-%d_%H%M}"
            for c in self.captures:
                self._sinks[c.label] = (self.record_dir / f"{stamp}_{c.label}.pcm").open("wb")
        except Exception:  # noqa: BLE001 — запись вспомогательна, захват важнее
            self._sinks = {}

    def _finalize_recordings(self):
        """.pcm → .wav при штатном стопе; при крэше остаётся .pcm — его дотранскрибирует
        transcribe_file.py. Почти пустые записи (нет встречи) убираем.
        Готовые .wav — в self.finalized[label]: демон отдаёт их диаризации."""
        self.finalized: dict[str, pathlib.Path] = {}
        sinks, self._sinks = dict(self._sinks), {}
        for label, f in sinks.items():
            try:
                f.close()
                p = pathlib.Path(f.name)
                if p.stat().st_size < self.sr * 2 * 5:  # меньше 5с звука — мусор
                    p.unlink(missing_ok=True)
                    continue
                with wave.open(str(p.with_suffix(".wav")), "wb") as w, p.open("rb") as src:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self.sr)
                    while chunk := src.read(1 << 20):
                        w.writeframes(chunk)
                p.unlink(missing_ok=True)
                self.finalized[label] = p.with_suffix(".wav")
            except Exception:  # noqa: BLE001 — .pcm остаётся, восстановим оффлайн
                pass

    def _pump(self):
        """Каждый источник — в свой буфер, без микса (спикеры не смешиваются)."""
        while self._running:
            got = False
            for c in self.captures:
                try:
                    part = c.q.get(timeout=0.15)
                except queue.Empty:
                    continue
                got = True
                self._last_frame[c.label] = time.time()
                sink = self._sinks.get(c.label)
                if sink is not None:
                    try:
                        sink.write((np.clip(part, -1, 1) * 32767).astype("<i2").tobytes())
                    except Exception:  # noqa: BLE001 — диск кончился: живём без записи
                        self._sinks.pop(c.label, None)
                with self._lock:
                    self._bufs[c.label] = np.concatenate([self._bufs[c.label], part])
                if self.on_frame is not None:
                    try:
                        self.on_frame(c.label, part)
                    except Exception:  # noqa: BLE001 — триггер не должен ронять захват
                        pass
            self._watch_streams()
            if not got:
                continue

    def _watch_streams(self):
        """InputStream шлёт кадры непрерывно даже в тишине: канал молчит 30с —
        значит PortAudio-стрим умер (CPU-голодание 20.07) — пересоздаём его."""
        now = time.time()
        if now - self._last_check < 5:
            return
        self._last_check = now
        for c in self.captures:
            silent = now - self._last_frame.get(c.label, now)
            if silent < 30:
                continue
            try:
                c.restart()
                msg = f"🎙 канал {c.label} молчал {int(silent)}с — аудио-стрим перезапущен"
            except Exception as e:  # noqa: BLE001
                msg = f"🎙 канал {c.label}: рестарт стрима не удался ({e}), попробую через 30с"
            # обновляем в обоих исходах: выдернутое устройство иначе даёт
            # рестарт-шторм с миганием статуса каждые 5 секунд
            self._last_frame[c.label] = time.time()
            if self.on_status is not None:
                try:
                    self.on_status(msg)
                except Exception:  # noqa: BLE001
                    pass

    def _cut(self, label: str) -> np.ndarray | None:
        need = int(self.sr * self.chunk_s)
        keep = int(self.sr * self.overlap_s)
        buf = self._bufs[label]
        if len(buf) < need:
            return None
        chunk = buf[:need].copy()
        self._bufs[label] = buf[need - keep:]
        return chunk

    def pull_labeled(self) -> list[tuple[str, np.ndarray]]:
        """Готовые речевые чанки по каналам: [(speaker, chunk)]."""
        with self._lock:
            cut = {label: self._cut(label) for label in self._bufs}
        speech = {label: (c is not None and self.is_speech(c)) for label, c in cut.items()}
        both = speech.get("blackhole") and speech.get("mic")
        out: list[tuple[str, np.ndarray]] = []
        for label, chunk in cut.items():
            if not speech.get(label):
                continue
            if both and label == "mic":
                continue  # эхо динамиков в микрофоне
            out.append((self.SPEAKER.get(label, label), chunk))
        return out

    def pull(self) -> np.ndarray | None:
        """Совместимость (CLI/тесты): первый готовый чанк любого канала."""
        for _, chunk in self.pull_labeled():
            return chunk
        return None

    def is_speech(self, chunk: np.ndarray) -> bool:
        """Энергетический гейт: RMS в дБFS выше порога = речь (v1)."""
        rms = float(np.sqrt(np.mean(chunk**2)) + 1e-9)
        db = 20 * np.log10(rms)
        return db > self.vad_db
