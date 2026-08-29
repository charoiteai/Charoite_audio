"""Захват аудио: микрофон и/или BlackHole (системный звук), кольцевой буфер."""
from __future__ import annotations

import math
import pathlib
import queue
import sys
import threading
import time
import wave
from collections import abc

import numpy as np
import sounddevice as sd

import meeting_stamp
import owner_voice

from charoite_paths import resolve_root

ROOT = resolve_root(__file__)


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


TAP_STREAM_MANIFEST = ROOT / "data" / "tap_stream.json"
SCK_STREAM_MANIFEST = ROOT / "data" / "sck_stream.json"


def _fresh_manifest(path: pathlib.Path, *keys: str) -> dict | None:
    """Манифест потока приложения, если он есть и файлы растут.

    Демон не может захватывать системный звук сам — никогда: право выдаётся
    процессу-читателю, а дочерний python его не наследует (вердикт разбора
    06–07.08). Поэтому захватывает приложение и пишет PCM в файлы; манифест
    появляется только после первых реальных кадров. Свежесть проверяем по
    mtime самих потоков: манифест без растущего файла — труп прошлой встречи.
    """
    import json
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            if key in m and time.time() - pathlib.Path(m[key]).stat().st_mtime > 10:
                return None
        return m
    except (OSError, ValueError, KeyError):
        return None


def fresh_sck_manifest() -> dict | None:
    """Живой поток ScreenCaptureKit: системный звук и, с macOS 15, микрофон.

    Предпочтительный источник: ScreenCaptureKit не создаёт агрегатных
    устройств, поэтому не может подвесить CoreAudio — в отличие от Core
    Audio taps, стоивших четырёх подвесов звука 06–07.08. Микрофон в том же
    манифесте означает, что PortAudio этой встрече не нужен вовсе.
    """
    return _fresh_manifest(SCK_STREAM_MANIFEST, "system")


def fresh_tap_manifest() -> dict | None:
    """Живой поток Core Audio tap (наследие; тап выключен по умолчанию)."""
    return _fresh_manifest(TAP_STREAM_MANIFEST, "path")


class TapStreamCapture:
    """Системный звук из файла-потока приложения — интерфейс как у Capture.

    Читает растущий s16le-файл хвостом (как tail -f) с позиции на момент
    старта, даунсемплит родную частоту до целевой и кладёт float32-блоки
    в ту же очередь, что и PortAudio-каналы. Конвейеру всё равно, откуда
    кадры; сторож тишины и страховка перезапуска работают без изменений.
    """

    def __init__(self, manifest: dict, samplerate: int, label: str, key: str = "path"):
        self.label = label
        self.samplerate = int(samplerate)
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        # key указывает, какой из потоков манифеста читаем: у тапа он один
        # («path»), у ScreenCaptureKit их два — «system» и «mic». Частота у
        # каждого своя: система отдаёт запрошенную, а микрофон — родную
        # частоту устройства (48 кГц), и спутать их значит растянуть голос.
        if key != "path":
            rate = manifest.get(f"{key}_rate", manifest["samplerate"])
            self._m = dict(manifest, path=manifest[key], samplerate=rate)
        else:
            self._m = manifest
        engine = manifest.get("engine", "tap")
        self.opened_as = f"поток приложения ({engine}), {float(self._m['samplerate']):.0f} Гц"
        self._stop_flag = threading.Event()
        self._thread: threading.Thread | None = None
        # Позиция последнего прочитанного байта — переживает restart: сторож
        # перезапускает канал после тишины, но файл-то жив, и всё, что в нём
        # накопилось, читается с этого места, а не выбрасывается прыжком в
        # конец. Прыжок стоил бы до 30 секунд системного звука на ровном месте.
        self._pos: int | None = None

    def start(self):
        path = pathlib.Path(self._m["path"])
        src_sr = int(float(self._m["samplerate"]))
        stream = path.open("rb")
        size = path.stat().st_size
        if self._pos is not None and self._pos <= size:
            stream.seek(self._pos)          # рестарт: продолжаем, где остановились
        else:
            stream.seek(0, 2)               # первый старт: хвост прошлой встречи не нужен
        # Первые байты обязаны прийти быстро: приложение выписывает манифест
        # только после реальных кадров. Нет роста — канала нет, и честнее
        # упасть здесь (поканальный старт скажет об этом вслух), чем писать
        # пустоту до конца встречи.
        deadline = time.time() + 3.0
        while path.stat().st_size <= stream.tell():
            if time.time() > deadline:
                stream.close()
                raise RuntimeError("поток тапа не растёт — приложение кадров не пишет")
            time.sleep(0.1)
        down = (_Downsampler(src_sr, self.samplerate)
                if src_sr != self.samplerate else None)
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._pump_file, args=(stream, down),
            daemon=True, name=f"tapstream-{self.label}")
        self._thread.start()

    def _pump_file(self, stream, down):
        # 0.1 с исходного потока за чтение: тот же темп, что блоки PortAudio.
        chunk_bytes = max(2, int(float(self._m["samplerate"]) * 0.1)) * 2
        carry = b""   # нечётный хвост чтения: пол-сэмпла до дозаписи писателем
        while not self._stop_flag.is_set():
            data = stream.read(chunk_bytes)
            here = stream.tell()
            if data and carry:
                data = carry + data
                carry = b""
            if len(data) % 2:
                # Чтение застало запись посередине сэмпла. Без переноса
                # np.frombuffer падает ValueError, нить умирает молча — и
                # канал глохнет до вмешательства сторожа.
                carry = data[-1:]
                data = data[:-1]
            # Позиция для рестарта — граница ЦЕЛОГО сэмпла, а не место, где
            # остановилось чтение. `carry` живёт в этой нити и после stop()
            # гибнет вместе с ней; запомнив нечётную позицию, сторожевой
            # рестарт сделал бы seek на середину сэмпла, и дальше каждая пара
            # байт собиралась бы из половинок соседних — канал до конца
            # встречи превратился бы в шум (аудит 0.46.0).
            self._pos = here - len(carry)
            if not data:
                time.sleep(0.05)
                continue
            block = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            if down is not None:
                block = down.process(block)
            if len(block):
                self.q.put(block)
        stream.close()

    def stop(self):
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def restart(self):
        """Пересоединиться с файлом, продолжив с последней прочитанной позиции."""
        self.stop()
        self.start()


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
        # Метка своего канала — имя владельца из конфига. Имя, неотличимое
        # от нейтральной метки собеседников («Собеседник», «Собеседник 2»),
        # сюда не пускаем: по метке склеиваются абзацы, выбирается дорожка
        # для распознавания и работает переименование по имени из
        # разговора — слитая метка увела бы реплики владельца чужому.
        # Проверка стоит ЗДЕСЬ, в источнике: демон подменял метку уже после
        # старта захвата, и между стартом и подменой чанки успевали уйти со
        # старой (ревью 19.08, седьмой круг, локальная голова).
        own = (cfg.get("sufler", {}).get("user_name") or "").strip()
        if own and not owner_voice.collides_with_neutral(own, self.SPEAKER["blackhole"]):
            self.SPEAKER = {**self.SPEAKER, "mic": own}
        self.sr = int(a["samplerate"])
        self.chunk_s = float(a["chunk_seconds"])
        self.overlap_s = float(a["overlap_seconds"])
        self.vad_db = float(a["vad_energy_db"])
        self.record_on = bool(a.get("record", True))
        self.record_keep_days = a.get("record_keep_days", 2)
        # Штампы встреч, которые прямо сейчас пересобираются: их записи ретеншн
        # не трогает. Заполняет демон из _recover_orphans — он один знает, кого
        # догоняет; здесь по умолчанию пусто, чтобы AudioHub оставался
        # самодостаточным в тестах и в CLI.
        self.protect_stamps: abc.Collection[str] = frozenset()
        self.record_dir = ROOT / (cfg.get("log", {}) or {}).get("recordings_dir", "recordings")
        self.captures: list[Capture] = []
        self.sources: list[str] = []
        self._bufs: dict[str, np.ndarray] = {}
        self._sinks: dict = {}          # label → открытый .pcm (сырая запись встречи)
        self._last_frame: dict[str, float] = {}
        # когда канал в последний раз ПЫТАЛИСЬ перезапустить — анти-шторм
        # отдельно от возраста кадров: см. _watch_streams (ревью 21.08)
        self._last_try: dict[str, float] = {}
        self._last_check = 0.0
        self._hung: set[str] = set()   # каналы, чей перезапуск завис — больше не трогаем
        # label → [потеря с прошлого отчёта, время отчёта,
        #          итог за встречу, из них не записано на диск]
        self._drops: dict[str, list[float]] = {}
        self._sys_speech_until = 0.0   # окно эха: до этого момента динамики недавно звучали
        self.chunk_no: dict[str, int] = {}   # канал → номер последнего физического чанка
        self._lock = threading.Lock()
        self._running = False

        mode = a["device"]
        # Метка канала осталась «blackhole» намеренно: по ней названы файлы
        # записей (`..._blackhole.wav`), её знают rebuild_transcript и
        # meeting_stamp. Переименование метки сломало бы пересборку старых
        # встреч ради косметики.
        # Порядок источников системного звука — от лучшего к запасному:
        # 1. ScreenCaptureKit: ничего не создаёт в CoreAudio, а с macOS 15
        #    приносит и микрофон тем же потоком — PortAudio не нужен вовсе;
        # 2. поток Core Audio tap (наследие, тап выключен по умолчанию);
        # 3. BlackHole — проверенный драйвер, но требует установки руками.
        sck = fresh_sck_manifest()
        tap_stream = None if sck else fresh_tap_manifest()
        if sck:
            bh, self.system_audio_via = None, "screencapturekit"
        elif tap_stream:
            bh, self.system_audio_via = None, "tap-stream"
        else:
            bh, self.system_audio_via = find_system_audio()
        mic = sd.default.device[0] if sd.default.device else None
        # Микрофон в манифесте = система отдаёт оба канала одним потоком.
        mic_from_stream = bool(sck and sck.get("mic"))

        if mode in ("auto", "mix", "blackhole") and sck is not None:
            self.captures.append(
                TapStreamCapture(sck, self.sr, "blackhole", key="system"))
            self.sources.append("Системный звук (ScreenCaptureKit)")
        elif mode in ("auto", "mix", "blackhole") and tap_stream is not None:
            # Живой поток приложения важнее устройств: он означает, что тап
            # уже отдаёт кадры тому единственному процессу, которому система
            # это разрешает. BlackHole остаётся фолбэком на случай, когда
            # манифеста нет (нет права, старая macOS, тап не поднялся).
            self.captures.append(TapStreamCapture(tap_stream, self.sr, "blackhole"))
            self.sources.append("Системный звук (тап)")
        elif mode in ("auto", "mix", "blackhole") and bh is not None:
            self.captures.append(Capture(bh, self.sr, "blackhole"))
            self.sources.append("Тап системы" if self.system_audio_via == "tap" else "BlackHole")
        # auto = система И микрофон: на встрече нужны обе стороны разговора
        if mode in ("mic", "mix", "auto") and mode != "blackhole":
            if mic_from_stream:
                # Микрофон тем же потоком (macOS 15+): PortAudio не открывается
                # вообще, и вместе с ним уходит класс аварий «мёртвый стрим
                # виснет на close», стоивший записей 20.07 и 06.08.
                self.captures.append(
                    TapStreamCapture(sck, self.sr, "mic", key="mic"))
                self.sources.append("Микрофон (ScreenCaptureKit)")
            elif mode != "auto" or bh is None or mic is not None or sck is not None:
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
        self._say_last_drops()
        for c in self.captures:
            try:
                c.stop()
            except Exception:  # noqa: BLE001 — мёртвый PortAudio-стрим виснет на close,
                pass           # не даём ему сорвать финализацию записи и стоп демона
        self._finalize_recordings()

    def _say_last_drops(self) -> None:
        """Досказать потери, не дожившие до очередного отчёта.

        Окно отчёта — полминуты, и хвост после последней строки копился молча:
        встал STT на семьдесят секунд, ожил — человек так и не узнал, что
        десять секунд разговора живая лента не увидела (ревью 20.08, GLM).
        """
        with self._lock:
            # Снимок под локом, разговор с человеком — после: `_pump` ещё жив
            # (его останавливает вызывающий сразу за нами), а `_say` уходит в
            # UI через колбэк демона, и держать на нём захват нельзя.
            tail = []
            for label, st in self._drops.items():
                if st[0] >= 1.0:
                    tail.append((label, st[0], st[2], st[3]))
                    st[0] = st[3] = 0.0
        for label, recent, total, lost in tail:
            msg = (f"⚠️ подсказки отставали и в конце встречи: не увидено "
                   f"ещё до {math.ceil(recent)}с ({label}, всего за встречу "
                   f"до {math.ceil(total)}с)")
            if lost > 0:
                # Хвост, упёршийся в окно отчёта, доносит именно этот метод —
                # и он обязан нести ту же пометку, иначе незаписанный звук
                # объявляется как безобидный (ревью 20.08, круг 4).
                msg += ". ЗАПИСЬ НА ДИСК НЕ ИДЁТ — этот звук не вернуть"
            self._say(msg)

    def _open_sinks(self):
        """Сырое аудио каждого канала — на диск сразу: обрыв STT/демона больше не
        теряет встречу (20.07 потеряли 5+ минут безвозвратно). Пишем .pcm (s16le,
        без заголовка — переживает крэш), штатный стоп финализирует в .wav."""
        # Уборка старого и открытие нового — разные заботы, и раньше они делили
        # один try: файл, исчезнувший между iterdir() и stat(), или строка вместо
        # числа в record_keep_days выключали запись НА ВСЮ ВСТРЕЧУ, причём молча.
        # Отказывала ровно та страховка, ради которой всё это писалось.
        try:
            held = self.prune_recordings(self.record_dir, self.record_keep_days,
                                         protect=self.protect_stamps)
            if held:
                self._say(f"ретеншн придержал {held} записей: встречи ещё "
                          "восстанавливаются")
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
    def prune_recordings(record_dir: pathlib.Path, keep_days,
                         protect: abc.Collection[str] = ()) -> int:
        """Аудио встреч — чувствительный носитель (из него извлекаются голосовые
        эмбеддинги), держим не дольше страхового окна. Вызывается и на старте
        демона: раньше чистка жила только внутри _open_sinks, поэтому при
        record: false или простое в неделю записи не удалялись вовсе, хотя
        PRIVACY обещает удаление через record_keep_days.

        `protect` — штампы встреч, которые прямо сейчас пересобираются. Их
        записи не трогаем: это единственный источник финальной стенограммы, а
        пересборка идёт отдельным процессом и к моменту чистки ещё грузит
        интерпретатор. Возвращаем, сколько файлов придержали, — задержка сверх
        обещанного срока обязана быть видимой, а не тихой.

        Что считать записью, решает `meeting_stamp`, а не список суффиксов
        здесь. Временные имена конвертации (`.wav.part` у демона,
        `.wav.part<pid>` у пересборки) — тоже записи: обрыв посреди
        финализации оставлял их на диске навсегда, а это полный несжатый WAV
        часовой встречи, то есть молчаливое нарушение обещания PRIVACY об
        удалении через record_keep_days (аудит 0.46.0, P0-3).

        Осознанный трейд-офф: файл, чьё имя `meeting_stamp` не признал
        записью, не удаляется ВООБЩЕ — раньше сметался любой старый
        `*.pcm`/`*.wav`. Чужое имя означает чужой файл: удалять то, чего мы
        не создавали, страшнее, чем передержать. Плата — ручные копии и
        нестандартные имена в recordings/ живут дольше обещанного; кто кладёт
        файлы в эту папку руками, отвечает за них сам.
        """
        if not record_dir.exists():
            return 0
        cutoff = time.time() - float(keep_days) * 86400
        protected = set(protect)
        held = 0
        for old in record_dir.iterdir():
            try:
                stamp = meeting_stamp.stamp_of_recording(old.name)
                if stamp is None:
                    continue                    # не запись — не наша забота
                if old.stat().st_mtime >= cutoff:
                    continue
                if stamp in protected:
                    held += 1
                    continue
                old.unlink(missing_ok=True)
            except FileNotFoundError:
                continue  # файл убрали параллельно — не наша забота
        return held

    @staticmethod
    def prune_stream_files(data_dir: pathlib.Path, keep_days) -> int:
        """Сырые потоки приложения — под тот же срок, что и записи.

        Системный звук пишет приложение (демону права не наследуются), и эти
        файлы жили ВНЕ ретеншна: `tap_stream.raw` усекался только следующим
        стартом тапа, а каталоги `sck/<uuid>/` убирались лишь при штатном
        стопе своей сессии — краш, SIGKILL или перезагрузка оставляли полное
        аудио встречи навсегда. На рабочей машине так пролежал 61 МБ
        системного звука девять дней при обещанных двух (аудит 16.08).
        PRIVACY.md обещает «записи временны» — обещание должно покрывать и
        этот слой.

        Живую сессию не трогаем: её каталог назван в свежем манифесте.
        Возвращает число удалённых путей.
        """
        if not data_dir.exists():
            return 0
        cutoff = time.time() - float(keep_days) * 86400
        alive: set[str] = set()
        for manifest in (fresh_sck_manifest(), fresh_tap_manifest()):
            for key in ("system", "mic", "path"):
                p = (manifest or {}).get(key)
                if p:
                    alive.add(str(pathlib.Path(p).resolve()))
        removed = 0

        def _old_enough(p: pathlib.Path) -> bool:
            try:
                return p.stat().st_mtime < cutoff
            except OSError:
                return False

        raw = data_dir / "tap_stream.raw"
        if (raw.exists() and str(raw.resolve()) not in alive and _old_enough(raw)):
            raw.unlink(missing_ok=True)
            removed += 1

        sck = data_dir / "sck"
        if sck.is_dir():
            for session in sck.iterdir():
                if not session.is_dir():
                    continue
                files = list(session.glob("*.raw"))
                if any(str(f.resolve()) in alive for f in files):
                    continue  # идёт прямо сейчас
                # Пустой каталог — не «мусор»: приложение только что создало
                # его под сессию, файлов ещё нет, а prune идёт на старте
                # демона — то есть ровно в момент старта записи. Обе защиты
                # выше смотрят на файлы, которых нет; судим по возрасту
                # самого каталога (второе мнение по #324, 16.08).
                if not files:
                    if not _old_enough(session):
                        continue
                elif not all(_old_enough(f) for f in files):
                    continue
                for f in files:
                    f.unlink(missing_ok=True)
                    removed += 1
                try:
                    session.rmdir()
                except OSError:
                    pass  # в каталоге осталось чужое — пусть лежит
        return removed

    def _finalize_recordings(self):
        """.pcm → .wav при штатном стопе; при крэше остаётся .pcm — его дотранскрибирует
        transcribe_file.py. Почти пустые записи (нет встречи) убираем.
        Готовые .wav — в self.finalized[label]: демон отдаёт их диаризации."""
        self.finalized: dict[str, pathlib.Path] = {}
        # под локом: _pump может ещё жить между _running=False и выходом
        # потока и делать pop умершего sink — копия словаря на смене размера
        # уронила бы весь стоп-путь, и .pcm остались бы без финализации
        # (круг 3, GLM)
        with self._lock:
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
                # под тем же локом, что и снапшот: новый ключ в словаре во
                # время его копирования — та же гонка, что и pop у _sinks
                with self._lock:
                    self._last_frame[c.label] = time.time()
                sink = self._sinks.get(c.label)
                written = sink is not None
                sink_error = None
                if sink is not None:
                    try:
                        sink.write((np.clip(part, -1, 1) * 32767).astype("<i2").tobytes())
                        # flush, иначе `written` означает «принято в буфер
                        # файла»: кончившийся диск всплыл бы только на close(),
                        # где исключение глотается, — и мы бы уже пообещали
                        # полную стенограмму (ревью 20.08, круг 4, DeepSeek).
                        sink.flush()
                    except Exception as e:  # noqa: BLE001 — диск кончился: живём без записи
                        # pop — под локом: health_snapshot из STT-потока в это
                        # же время итерирует _sinks, и смена размера словаря на
                        # середине итерации роняла бы сам STT RuntimeError'ом
                        # (ревью 21.08, Gemini + локальная).
                        with self._lock:
                            self._sinks.pop(c.label, None)
                        written = False
                        sink_error = e
                dropped = self._append(c.label, part)
                if sink_error is not None:
                    # Не ждём переполнения минутного STT-буфера, чтобы сказать
                    # о смерти страховочной записи. После pop эта ветка для
                    # канала больше не повторится, то есть статус не спамит.
                    msg = (f"ЗАПИСЬ НА ДИСК ОСТАНОВИЛАСЬ ({c.label}: {sink_error}) — "
                           "после сбоя этот звук будет не восстановить")
                    print(msg, file=sys.stderr, flush=True)
                    self._say(msg)
                if dropped:
                    # Вне лока: статус уходит в UI через колбэк демона, и
                    # держать на нём аудиопоток нельзя. Факт записи берём
                    # ОТСЮДА, а не из `_sinks` позже: между этим местом и
                    # отчётом стоп успевает обнулить словарь, и правдивое
                    # «не вернуть» превращалось бы в ложное «будет полной»
                    # (ревью 20.08, круг 3, DeepSeek).
                    self._note_drop(c.label, dropped, written)
                if self.on_frame is not None:
                    try:
                        self.on_frame(c.label, part)
                    except Exception:  # noqa: BLE001 — триггер не должен ронять захват
                        pass
            self._watch_streams()
            if not got:
                continue
        # Хвост, домолотый уже после `stop()`, иначе не озвучивает никто:
        # окно отчёта — полминуты, а досказ в `stop()` к этому моменту уже
        # отработал. Метод идемпотентен, двойной строки не будет
        # (ревью 20.08, круг 3, DeepSeek).
        self._say_last_drops()

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
            if now - self._last_try.get(c.label, 0.0) < 30:
                continue        # анти-шторм: между попытками — пауза, но возраст честный
            self._last_try[c.label] = now
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
            if outcome is None:
                # Возраст кадров сбрасываем ТОЛЬКО при удачном перезапуске.
                # Раньше он сбрасывался «в обоих исходах» как анти-шторм, и у
                # выдернутого устройства возраст канала колебался 0..35с —
                # третий контур watchdog (аудиовход, порог 100с) не срабатывал
                # НИКОГДА, ровно в своём главном сценарии (ревью 21.08,
                # GLM + DeepSeek независимо). Анти-шторм теперь держит
                # _last_try, а _last_frame говорит правду.
                with self._lock:
                    self._last_frame[c.label] = time.time()
            if self.on_status is not None:
                try:
                    self.on_status(msg)
                except Exception:  # noqa: BLE001
                    pass

    BUF_CAP_S = 60            # сколько живого звука держим в памяти на канал

    def _append(self, label: str, part: np.ndarray) -> float:
        """Дописать кусок в буфер STT; вернуть, сколько секунд пришлось выбросить.

        Потолок нужен на случай мёртвого потребителя: запись на диск идёт
        отдельным sink, а буфер иначе рос бы до конца встречи (аудит 14.08).

        Режем РОВНО излишек. Прежнее «урезать до половины потолка»
        выбрасывало полминуты чужой речи из-за одного медленного чанка —
        молча, без строки в логе: офлайн-пересборка звук возвращала (он на
        диске), а живая лента шла кусками. Это и была жалоба «переводит
        кусками, не всю речь» (ревью 20.08, DeepSeek).
        """
        with self._lock:
            cap = self.sr * self.BUF_CAP_S
            merged = np.concatenate([self._bufs[label], part])
            dropped = 0.0
            if len(merged) > cap:
                # Срез, а не «выбросить из старого буфера»: кусок длиннее
                # потолка иначе оставил бы буфер выше лимита, а излишек
                # внутри самого куска не попал бы в счётчик потерь (ревью
                # 20.08 — нашли и локальная голова, и DeepSeek).
                dropped = (len(merged) - cap) / self.sr
                merged = merged[-cap:]
            self._bufs[label] = merged
        return dropped

    def health_snapshot(self, *, now: float | None = None) -> dict[str, object]:
        """Cheap live-pipeline gauges; never consumes or copies audio.

        ``input_age_seconds`` is the freshest channel age, so it grows only
        when *all* capture sources stop delivering frames.  Per-channel ages
        remain in ``channels`` for diagnosis.  The STT thread emits this
        snapshot as NDJSON; absence of that event is itself its liveness
        signal.  All three dicts are read under the same lock the audio
        thread mutates them with: iterating ``_sinks`` while ``_pump`` pops a
        dead one raised RuntimeError and killed the STT thread — the very
        failure this telemetry exists to expose (review 21.08).
        """
        now = time.time() if now is None else now
        with self._lock:
            backlog = {
                label: max(0.0, len(buf) / self.sr)
                for label, buf in self._bufs.items()
            }
            last_frame = dict(self._last_frame)
            sinks = set(self._sinks)
        labels = list(backlog)
        ages = {
            label: (max(0.0, now - seen) if (seen := last_frame.get(label)) is not None
                    else None)
            for label in labels
        }
        seen_ages = [age for age in ages.values() if age is not None]
        channels = {
            label: {
                "backlog_seconds": backlog[label],
                "input_age_seconds": ages[label],
                "recording": label in sinks,
            }
            for label in labels
        }
        return {
            "backlog_seconds": max(backlog.values(), default=0.0),
            "input_age_seconds": min(seen_ages, default=None),
            "recording_ok": (not self.record_on
                             or all(label in sinks for label in labels)),
            "channels": channels,
        }

    _DROP_REPORT_S = 30.0     # чаще — спам в ленте: отставание длится минутами

    def _note_drop(self, label: str, seconds: float, written: bool = True) -> None:
        """Живая лента отстала — звук из буфера потерян.

        Молчать здесь нельзя: человек видит рваные подсказки и считает, что
        сломалось распознавание, хотя причина — медленный потребитель.

        Но и утешать вслепую нельзя. «Финальная стенограмма будет полной» —
        правда, только пока звук пишется на диск отдельным sink. Записи может
        не быть тремя путями: `record: false` в конфиге, отказ открытия файла
        и смерть sink посреди встречи (диск кончился). В этих случаях
        выброшенный звук потерян НАВСЕГДА, и человеку надо действовать сейчас,
        а не читать успокоительную строку (ревью 20.08, GLM).
        """
        now = time.time()
        with self._lock:
            # ОДИН критический раздел: `_say_last_drops` читает и обнуляет те
            # же счётчики из потока `stop()`, пока `_pump` ещё жив. Три
            # раздельных замка (первая попытка) закрывали накопление и сброс
            # по отдельности, но не связку «проверил порог → забрал»: поток
            # остановки успевал вклиниться между ними, и выходило либо
            # «потеряно до 0с» вдогонку уже сказанному, либо проглоченная
            # пометка «не вернуть» (ревью 20.08, круг 4, DeepSeek).
            st = self._drops.setdefault(label, [0.0, 0.0, 0.0, 0.0])
            st[0] += seconds
            st[2] += seconds
            if not written:
                st[3] += seconds
            if now - st[1] < self._DROP_REPORT_S:
                return
            if st[0] < 1.0:
                # Буфер дозревает до потолка кратно куску захвата, поэтому
                # первое переполнение — четверть секунды. Ради неё не стоит ни
                # строки в ленте, ни тем более «звук не вернуть»: подождём,
                # пока накопится заметное (ревью 20.08, GLM).
                return
            st[1] = now
            # Копящаяся сумма без сброса не даёт прочитать, отстаём ли ПРЯМО
            # сейчас: строки «потеряно 300с / 600с / 900с» описывают одно и то
            # же отставание. Говорим интервал, итог — справочно.
            recent, st[0] = st[0], 0.0
            total, lost = st[2], st[3]
            st[3] = 0.0
        # «до Xс», а не «Xс»: часть вытесненного — перехлёст, который `_cut`
        # уже отдал потребителю в прошлом чанке, так что цифра сверху.
        # Округление ВВЕРХ: первое переполнение выбрасывает четверть секунды,
        # и «потеряно 0с» — предупреждение, отрицающее само себя
        # (ревью 20.08, DeepSeek).
        head = (f"⚠️ подсказки отстают: потеряно до {math.ceil(recent)}с живого "
                f"звука ({label}, всего за встречу до {math.ceil(total)}с). ")
        if lost > 0:
            # По факту записи кусков ЗА ИНТЕРВАЛ, а не по состоянию `_sinks`
            # сейчас. Гард по `_running` (первая попытка закрыть ту же ложную
            # тревогу) был хуже: он молчал и там, где кусок не записан.
            self._say(head + "ЗАПИСЬ НА ДИСК НЕ ИДЁТ — этот звук не вернуть "
                             "ни пересборкой, ни повтором")
        else:
            self._say(head + "Запись на диск не пострадала — финальная "
                             "стенограмма будет полной")

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
            # Номер ФИЗИЧЕСКОГО чанка канала — растёт и на тихих, и на отброшенных
            # как эхо: шов стенограммы считает соседями только n и n-1, а тихий
            # чанк между двумя речевыми — разрыв, не перекрытие (luna, круг-2 #452).
            # Под тем же локом, что и срез (GLM #453); ключ — физический канал.
            chunk_no = self.__dict__.setdefault("chunk_no", {})   # хаб в тестах собирают мимо __init__
            for label, c in cut.items():
                if c is not None:
                    chunk_no[label] = chunk_no.get(label, -1) + 1
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

    def channel_of(self, speaker: str) -> str:
        """Физический канал по имени спикера из pull_labeled («Я» → mic)."""
        for label, name in self.SPEAKER.items():
            if name == speaker:
                return label
        return speaker

    def chunk_seq(self, speaker: str) -> tuple[str, int] | None:
        """(канал, номер последнего физического чанка) для стенограммы:
        соседи — n и n-1 одного канала; тихий или отброшенный как эхо чанк
        номер тоже потребляет (luna, круг-2 #452)."""
        label = self.channel_of(speaker)
        with self._lock:
            n = self.__dict__.get("chunk_no", {}).get(label)
        return None if n is None else (label, n)

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
