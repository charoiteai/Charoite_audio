"""Захват аудио: микрофон и/или BlackHole (системный звук), кольцевой буфер."""
from __future__ import annotations

import pathlib
import queue
import threading
import time
import wave

import numpy as np
import sounddevice as sd

import meeting_stamp

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


# Устройство, которое приложение поднимает на время встречи через Core Audio
# process tap (SystemAudioTap.swift). Имя — контракт между приложением и
# демоном: меняешь здесь — меняй и там.
TAP_DEVICE = "Charoite System Audio"


def find_system_audio() -> tuple[int | None, str]:
    """Индекс канала собеседников и чем он получен.

    Приоритет у BlackHole — осознанно (итог боевого теста 06.08). Тап
    создаётся, виден системе и из отдельного процесса отдаёт звук, но демону
    не приносит ни кадра: 0 байт за 94 секунды записи, причём поток
    открывается штатно (лестница конфигураций не понадобилась). Пока причина
    не найдена, рабочие встречи важнее эксперимента. Тап остаётся фолбэком
    для машин без BlackHole: хуже нуля байт он не даст, а после разбора
    может и заработать. Молчаливого «ни того, ни другого» быть не должно:
    без этого канала в стенограмме не будет второй стороны разговора.
    """
    bh = find_device("blackhole")
    if bh is not None:
        return bh, "blackhole"
    tap = find_device(TAP_DEVICE)
    if tap is not None:
        return tap, "tap"
    return None, "blackhole"


class _Downsampler:
    """Понижение частоты дискретизации на одном numpy, с состоянием между блоками.

    Почему не «брать каждый N-й отсчёт»: всё, что выше половины целевой
    частоты, при прореживании заворачивается в речевую полосу. Музыка из
    Zoom и шипящие на 12 кГц осели бы на 4 кГц прямо поверх речи — качество
    расшифровки упало бы, и ни одной строчки в логе об этом не появилось бы.
    Поэтому сначала ФНЧ, потом прореживание.

    Почему не scipy: его нет в зависимостях проекта, и тянуть его ради одной
    свёртки на 127 коэффициентов незачем.
    """

    TAPS = 127  # нечётное — фильтр линейнофазный, задержка ровно (TAPS-1)/2

    def __init__(self, src_sr: int, dst_sr: int):
        if src_sr <= dst_sr:
            raise ValueError(f"ресемплер работает только вниз: {src_sr} → {dst_sr}")
        self.src_sr = int(src_sr)
        self.dst_sr = int(dst_sr)
        self.ratio = self.src_sr / self.dst_sr
        # Срез на 0.45 целевой частоты (в долях исходной): запас до Найквиста
        # целевой (0.5) отдан переходной полосе окна Хэмминга.
        cutoff = 0.45 * self.dst_sr / self.src_sr
        n = np.arange(self.TAPS) - (self.TAPS - 1) / 2
        h = np.sinc(2 * cutoff * n) * np.hamming(self.TAPS)
        self._h = h / h.sum()                      # единичный коэффициент на постоянном токе
        self._tail = np.zeros(self.TAPS - 1)       # хвост входа для непрерывной свёртки
        self._carry = np.zeros(0)                  # последний отсчёт прошлого блока — для интерполяции через шов
        self._pos = 0.0                            # дробная позиция следующего выхода

    def process(self, block: np.ndarray) -> np.ndarray:
        """Блок входа → блок выхода. Длина выхода плавает (при 48→16 кГц это
        1334/1333/1333 на блок в 4000), в среднем ровно len(block)/ratio."""
        if block.size == 0:
            return np.zeros(0, dtype=np.float32)
        x = np.concatenate([self._tail, np.asarray(block, dtype=np.float64)])
        # .copy(), а не срез-вид: иначе хвост держал бы весь блок живым, а на
        # входе может лежать буфер PortAudio, который после колбэка недействителен.
        self._tail = x[-(self.TAPS - 1):].copy()
        y = np.convolve(x, self._h, mode="valid")  # ровно len(block) отсчётов
        s = np.concatenate([self._carry, y])
        last = s.size - 1
        if self._pos > last:
            # Блок короче шага прореживания: копим состояние, выхода нет.
            self._carry = s[last:].copy()
            self._pos -= last
            return np.zeros(0, dtype=np.float32)
        count = int((last - self._pos) // self.ratio) + 1
        pos = self._pos + self.ratio * np.arange(count)
        idx = pos.astype(np.int64)
        frac = pos - idx
        # На последнем отсчёте frac == 0, но индекс idx+1 всё равно вычисляется —
        # прижимаем его, чтобы не выйти за массив.
        out = s[idx] * (1.0 - frac) + s[np.minimum(idx + 1, last)] * frac
        self._carry = s[last:].copy()
        self._pos = self._pos + self.ratio * count - last
        return out.astype(np.float32)               # float32: иначе dtype буферов уплывёт в float64


class Capture:
    """Один входной поток → очередь float32-чанков (mono, samplerate).

    Очередь ВСЕГДА отдаёт self.samplerate. Если устройство удалось открыть
    только на его собственной частоте, понижение делает _Downsampler внутри —
    наружу, в AudioHub, это не протекает.
    """

    PLAIN = "как раньше"  # имя первой ступени лестницы; см. _ladder()

    def __init__(self, device_index: int | None, samplerate: int, label: str):
        self.device = device_index
        self.samplerate = samplerate
        self.label = label
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._resampler: _Downsampler | None = None
        self.opened_as: str | None = None  # сработавшая ступень — её показываем в статусе

    def _cb(self, indata, frames, time_info, status):  # noqa: ANN001
        if status:
            pass  # over/underflow не критичны для суфлёра
        if self._resampler is None:
            self.q.put(indata[:, 0].copy())
            return
        # Свёртка на 127 коэффициентов прямо в аудио-колбэке: около полумиллисекунды
        # на блок, без блокировок, диска и аллокаций сверх одного массива. Дешевле,
        # чем протаскивать частоту устройства через AudioHub, _pump и запись на диск.
        self.q.put(self._resampler.process(indata[:, 0]))

    def _device_samplerate(self) -> int | None:
        """Родная частота устройства: на ней оно откроется заведомо."""
        try:
            info = sd.query_devices(self.device, "input")
            return int(round(float(info["default_samplerate"])))
        except Exception:  # noqa: BLE001 — не смогли спросить, значит эта ступень пропускается
            return None

    def _ladder(self):
        """Три попытки открыть устройство, от самой безобидной к самой грубой.

        Первая ступень — ровно то, что делалось всегда. Микрофон и BlackHole
        открываются на ней и никакой новой логики не видят: виртуальный драйвер
        принимает любую частоту, поэтому пара «16 кГц + блок 4000» много лет
        выглядела безопасной.

        Дальше — ради Core Audio-тапа. Агрегат тапа берёт частоту у физического
        выхода (kAudioAggregateDeviceMainSubDeviceKey в SystemAudioTap.swift),
        то есть 44.1 или 48 кГц. Частоту агрегата PortAudio поменять не может,
        поэтому включает свой ресемплер и пересчитывает наш блок в кадры
        устройства: 250 мс на 48 кГц — это 12000 кадров, втрое больше типичного
        потолка kAudioDevicePropertyBufferFrameSizeRange (4096). AUHAL отвечает
        -10851, kAudioUnitErr_InvalidPropertyValue — отказ по ЗНАЧЕНИЮ свойства,
        а не по формату (-10868). Поток не открывается вовсе — отсюда ноль байт
        в записи вместо тишины.

        Что именно не принято, частота или размер блока, известно только машине
        с тапом. Поэтому лестница, а не одна догадка: ступень 2 чинит случай
        «мешал размер блока», ступень 3 — «мешала частота». Какая сработала,
        видно в opened_as, и AudioHub говорит об этом вслух.

        Генератор, а не список, намеренно: на здоровом устройстве всё кончается
        на первой ступени и до опроса устройства дело не доходит. Лишний вызов
        PortAudio на каждом рестарте сторожевого таймера нам не нужен.
        """
        yield self.PLAIN, self.samplerate, int(self.samplerate * 0.25)
        yield "свободный размер блока", self.samplerate, 0
        native = self._device_samplerate()
        if native and native > self.samplerate:
            yield f"частота устройства {native} Гц", native, 0

    def _open(self, samplerate: int, blocksize: int) -> None:
        resampler = (_Downsampler(samplerate, self.samplerate)
                     if samplerate != self.samplerate else None)
        stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=samplerate,
            dtype="float32",
            blocksize=blocksize,
            callback=self._cb,
        )
        self._resampler = resampler  # до start(): колбэк начнёт приходить только после него
        try:
            stream.start()
        except Exception:  # noqa: BLE001 — неудачная ступень не должна оставить полусостояние
            self._resampler = None
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._stream = stream

    def start(self):
        self._resampler = None
        self.opened_as = None
        errors: list[str] = []
        for name, samplerate, blocksize in self._ladder():
            try:
                self._open(samplerate, blocksize)
            except Exception as e:  # noqa: BLE001 — пробуем следующую ступень
                errors.append(f"«{name}» → {type(e).__name__}: {e}")
                continue
            self.opened_as = name
            return
        # Раньше отсюда улетал голый PortAudioError с кодом вроде -10851 и без
        # намёка, что именно устройство не приняло. Перечисляем все ступени.
        raise RuntimeError(
            f"канал {self.label}: устройство не приняло ни одну конфигурацию — "
            + "; ".join(errors))

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

    def __init__(self, cfg: dict, stamp: str | None = None):
        a = cfg["audio"]
        # Штамп берём у стенограммы, а не считаем свой: два независимых
        # datetime.now() на границе минуты давали `..._1359.md` и `..._1400_mic.pcm`,
        # после чего rebuild_transcript не находил записи и молча пропускал
        # финальную пересборку — пользователь оставался с черновиком чанков.
        self.stamp = stamp or meeting_stamp.now()
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
        self._hung: set[str] = set()   # каналы, чей перезапуск завис — больше не трогаем
        self._sys_speech_until = 0.0   # окно эха: до этого момента динамики недавно звучали
        self._lock = threading.Lock()
        self._running = False

        mode = a["device"]
        # Метка канала осталась «blackhole» намеренно: по ней названы файлы
        # записей (`..._blackhole.wav`), её знают rebuild_transcript и
        # meeting_stamp. Переименование метки сломало бы пересборку старых
        # встреч ради косметики.
        bh, self.system_audio_via = find_system_audio()
        mic = sd.default.device[0] if sd.default.device else None

        if mode in ("auto", "mix", "blackhole") and bh is not None:
            self.captures.append(Capture(bh, self.sr, "blackhole"))
            self.sources.append("Тап системы" if self.system_audio_via == "tap" else "BlackHole")
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

    # Сколько ждём перезапуск канала, прежде чем считать его безнадёжным.
    # Пять секунд: закрытие живого стрима укладывается в доли секунды, а
    # мёртвый не возвращается никогда.
    RESTART_TIMEOUT = 5.0

    def start(self):
        self._running = True
        if self.record_on:
            self._open_sinks()
        # Поканально, а не общим циклом: 06.08 отказ канала системного звука
        # оставил встречу вообще без записи — исключение вынесло цикл до
        # микрофона, который был полностью исправен.
        failed = []
        for c in self.captures:
            try:
                c.start()
            except Exception as e:  # noqa: BLE001 — сосед не должен уносить встречу
                failed.append((c.label, e))
                continue
            if c.opened_as and c.opened_as != Capture.PLAIN:
                # Не косметика: на какой ступени поднялся канал — это и есть ответ,
                # что именно устройству не нравилось. Иначе выяснять вручную.
                self._say(f"🎙 канал {c.label}: обычная конфигурация не принята, "
                          f"открыт через «{c.opened_as}»")
        if len(failed) == len(self.captures):
            raise RuntimeError("ни один аудиоканал не открылся: "
                               + "; ".join(f"{lbl} → {err}" for lbl, err in failed))
        for lbl, err in failed:
            self._say(f"🎙 канал {lbl} не открылся ({err}) — встреча пишется без него")
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
        # Уборка старого и открытие нового — разные заботы, и раньше они делили
        # один try: файл, исчезнувший между iterdir() и stat(), или строка вместо
        # числа в record_keep_days выключали запись НА ВСЮ ВСТРЕЧУ, причём молча.
        # Отказывала ровно та страховка, ради которой всё это писалось.
        try:
            self.prune_recordings(self.record_dir, self.record_keep_days)
        except Exception as e:  # noqa: BLE001 — уборка не должна мешать записи
            self._say(f"чистка старых записей не удалась: {e}")
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            for c in self.captures:
                path = meeting_stamp.recording_path(
                    self.record_dir, self.stamp, c.label, "pcm")
                # "xb", а не "wb": коллизия штампов должна быть видимой ошибкой,
                # а не молчаливым обнулением чужой записи.
                self._sinks[c.label] = path.open("xb")
        except Exception as e:  # noqa: BLE001 — захват важнее записи, но не молча
            self._sinks = {}
            self._say(f"ЗАПИСЬ НА ДИСК ВЫКЛЮЧЕНА: {e} — после сбоя встречу будет не восстановить")

    @staticmethod
    def prune_recordings(record_dir: pathlib.Path, keep_days) -> None:
        """Аудио встреч — чувствительный носитель (из него извлекаются голосовые
        эмбеддинги), держим не дольше страхового окна. Вызывается и на старте
        демона: раньше чистка жила только внутри _open_sinks, поэтому при
        record: false или простое в неделю записи не удалялись вовсе, хотя
        PRIVACY обещает удаление через record_keep_days."""
        if not record_dir.exists():
            return
        cutoff = time.time() - float(keep_days) * 86400
        for old in record_dir.iterdir():
            try:
                if old.suffix in (".pcm", ".wav") and old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
            except FileNotFoundError:
                continue  # файл убрали параллельно — не наша забота

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
                # Пишем во временное имя и переименовываем: rebuild_transcript
                # ждёт готовый .wav и до появления файла считает канал
                # незавершённым — иначе он видел полупустой .wav и начинал
                # конвертировать тот же .pcm параллельно нам.
                wav = p.with_suffix(".wav")
                tmp = p.with_suffix(".wav.part")
                with wave.open(str(tmp), "wb") as w, p.open("rb") as src:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(self.sr)
                    while chunk := src.read(1 << 20):
                        w.writeframes(chunk)
                tmp.replace(wav)
                p.unlink(missing_ok=True)
                self.finalized[label] = wav
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

    def _restart_guarded(self, c):
        """Перезапустить канал, не подставив под удар конвейер.

        Возвращает None при успехе, исключение при отказе, TimeoutError если
        перезапуск не вернулся за RESTART_TIMEOUT. Отдельный поток нужен
        именно из-за последнего случая: `stop()` мёртвого PortAudio-стрима
        виснет, а зависание не ловится через try/except.
        """
        box: dict = {}

        def run():
            try:
                c.restart()
                box["ok"] = True
            except Exception as e:  # noqa: BLE001 — доносим наружу как значение
                box["err"] = e

        worker = threading.Thread(target=run, daemon=True, name=f"restart-{c.label}")
        worker.start()
        worker.join(self.RESTART_TIMEOUT)
        if worker.is_alive():
            # Поток бросаем: убить его нельзя, но он daemon и уйдёт с процессом.
            return TimeoutError(f"перезапуск не вернулся за {self.RESTART_TIMEOUT:.0f}с")
        return None if box.get("ok") else box.get("err")

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
            if c.label in self._hung:
                continue        # перезапуск этого канала уже завис — не трогаем повторно
            outcome = self._restart_guarded(c)
            if outcome is None:
                msg = f"🎙 канал {c.label} молчал {int(silent)}с — аудио-стрим перезапущен"
            elif isinstance(outcome, TimeoutError):
                # Главный урок 06.08: закрытие мёртвого стрима не возвращается,
                # и вызов прямо из _pump останавливал конвейер целиком — вместе
                # с исправным микрофоном. Бросаем канал, встречу дописываем.
                self._hung.add(c.label)
                msg = (f"🎙 канал {c.label}: перезапуск завис, канал отключён — "
                       "встреча пишется остальными")
            else:
                msg = f"🎙 канал {c.label}: рестарт стрима не удался ({outcome}), попробую через 30с"
            # обновляем в обоих исходах: выдернутое устройство иначе даёт
            # рестарт-шторм с миганием статуса каждые 5 секунд
            self._last_frame[c.label] = time.time()
            if self.on_status is not None:
                try:
                    self.on_status(msg)
                except Exception:  # noqa: BLE001
                    pass

    def _say(self, msg: str) -> None:
        """Статус в UI. Отказ страховочной записи пользователь обязан увидеть
        до конца встречи, а не узнать о нём, когда восстанавливать уже нечего."""
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
        now = time.monotonic()
        if speech.get("blackhole"):
            # Эхо динамиков доживает в микрофоне до следующего среза, когда
            # фазы нарезки каналов разъехались (перезапуск канала сторожем
            # сбрасывает его буфер) — помним о недавней речи ещё один чанк.
            self._sys_speech_until = now + self.chunk_s
        out: list[tuple[str, np.ndarray]] = []
        for label, chunk in cut.items():
            if not speech.get(label):
                continue
            if label == "mic":
                if speech.get("blackhole"):
                    continue  # эхо динамиков в микрофоне: оба канала звучат
                if cut.get("blackhole") is None and now < self._sys_speech_until:
                    # Системный срез запаздывает, а динамики только что
                    # звучали: раньше этот чанк уходил в стенограмму вторым
                    # экземпляром той же фразы — «от владельца». Если же
                    # системный чанк есть и он тихий, собеседник реально
                    # замолчал — свой ответ глушить нельзя.
                    continue
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
