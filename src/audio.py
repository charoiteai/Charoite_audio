"""Захват аудио: микрофон и/или BlackHole (системный звук), кольцевой буфер."""
from __future__ import annotations

import math
import pathlib
import dataclasses
import queue
import sys
import threading
import time
import wave
from collections import abc

import numpy as np
import sounddevice as sd

import meeting_stamp
import channel_labels
import stt_runtime

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


def find_system_audio() -> int | None:
    """Индекс устройства PortAudio с каналом собеседников (None — нет).

    Единственное устройство здесь — BlackHole: запасной путь на случай, когда
    приложение не подняло ScreenCaptureKit (нет права, старая macOS). Второго
    устройства нет: Core Audio tap (06–07.08) клинил CoreAudio на 26.5 и снят
    02.09. Молчаливого «ни того, ни другого» быть не должно: без этого канала
    в стенограмме не будет второй стороны разговора — вызывающий пишет это
    в источники.
    """
    return find_device("blackhole")


SCK_STREAM_MANIFEST = ROOT / "data" / "sck_stream.json"
# Метка своей строки в capture.log: этот же лог мы читаем, чтобы назвать
# причину, и без метки причиной становилось бы наше собственное
# предупреждение с прошлой встречи.
MIC_ONLY_LOG_MARK = "ЗАПИСЬ БЕЗ СИСТЕМНОГО ЗВУКА (только микрофон)"
# Своя метка для потери микрофона: строка «без системного звука» про умерший
# микрофон была бы ложью в логе и ломала бы разбор по метке (I1/I2 входного
# круга по №235). Обе метки — фильтр эха при чтении хвоста capture.log.
MIC_LOST_LOG_MARK = "ЗАПИСЬ БЕЗ МИКРОФОНА"
LOSS_LOG_MARKS = (MIC_ONLY_LOG_MARK, MIC_LOST_LOG_MARK)


# Виды события канала (№234): единственный структурный выход хаба о том, что
# канал перестал или снова начал писать. Из события собираются и крик человеку
# (статус, уведомление, capture.log), и след встречи (сайдкар, хвост
# стенограммы, нить) — до этого событие существовало только как строка
# `on_status`, и документы встречи о пропаже узнать не могли (входной круг DS
# и GLM: «событие записи живёт только в статусе UI и логе»).
CH_LOST = "lost"      # канал жил и пропал (или не захвачен с начала — died=False)
CH_BACK = "back"      # канал снова пишет; silent_s — от истинного начала тишины
CH_GAP = "gap"        # молчал ≥ порога, перезапуск удался с первой попытки — крика
#                       не было, но дыра в записи была (Critical GLM входного круга)
CH_END = "end"        # запись остановлена, канал так и не вернулся (закрытие эпизода)
# машинный класс причины — контракт для потребителей вместо разбора подстрок
# (критика GLM входного круга): restart_failed | hung | start_error | missing |
# restarted | stop
CH_CAUSES = ("restart_failed", "hung", "start_error", "missing", "restarted", "stop")


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelEvent:
    label: str
    kind: str
    at: float                         # момент события (стенные часы)
    stopped_at: float | None          # истинное начало тишины — последний кадр канала,
    #                                   не момент крика: крик опаздывает на порог сторожа
    #                                   и попытки рестарта (66–96 с; Critical GLM)
    silent_s: float | None            # длительность эпизода от stopped_at (back/gap/end)
    died: bool                        # жил и пропал / не захвачен с начала
    cause: str                        # из CH_CAUSES
    reason: str                       # человеческая причина, как в статусе

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


class Loss:
    """Запись реестра потерь: почему канал не пишет и что с ним будет.

    `retriable` — сторож ещё пробует перезапуск (две неудачи подряд, поток
    может ожить); False — канал брошен (`_hung`: перезапуск завис, повторных
    попыток не будет) или не открылся на старте. `died` — канал жил и пропал;
    False — не захвачен с начала. Совет человеку выводится ИЗ этого значения,
    а не подаётся вызывающим: подать совет, противоречащий причине, стало
    невыразимо (Critical DS и Important GLM выходного круга по №235 — «канал
    перезапускается сам» о канале, который сторож больше не трогает).
    """
    __slots__ = ("reason", "retriable", "died", "cause", "since", "stopped_at")

    def __init__(self, reason: str, *, retriable: bool, died: bool, cause: str = "",
                 since: float = 0.0, stopped_at: float | None = None) -> None:
        self.reason = reason
        self.retriable = retriable
        self.died = died
        # последний кадр канала перед потерей — истинная граница дыры в записи
        # для следа встречи (№234); `since` остаётся моментом постановки в
        # реестр: на нём построено «кадр после него — ожил»
        self.stopped_at = stopped_at
        # повод, когда канал не жил: "start_error" (устройство есть, не
        # открылось) или "missing" (канала нет вовсе) — совет выбирается по
        # нему, а не по подстроке причины (Minor DS/GLM круга 2)
        self.cause = cause
        # момент постановки в реестр: кадр ПОСЛЕ него — канал ожил; при смене
        # фазы (повторы → брошен) сохраняется, чтобы «без канала было N с»
        # считалось от начала эпизода, а не от последнего объявления (M3 DS)
        self.since = since

    def phase(self) -> bool:
        """Фаза, смена которой — новое событие для человека: канал ещё
        пробуют перезапустить или уже бросили. От неё зависит совет; смена
        «не захвачен → пропал» совет не меняет и звука не заслуживает
        (сценарий «кричали на старте, потом сторож бросил» из теста №232)."""
        return self.retriable

    def __repr__(self) -> str:                    # в логах и assert-ах тестов
        return f"Loss({self.reason!r}, retriable={self.retriable}, died={self.died}, cause={self.cause!r})"


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


class TapStreamCapture:
    """Системный звук из файла-потока приложения — интерфейс как у Capture.

    Читает растущий s16le-файл хвостом (как tail -f) с позиции на момент
    старта, даунсемплит родную частоту до целевой и кладёт float32-блоки
    в ту же очередь, что и PortAudio-каналы. Конвейеру всё равно, откуда
    кадры; сторож тишины и страховка перезапуска работают без изменений.
    """

    def __init__(self, manifest: dict, samplerate: int, label: str, key: str):
        self.label = label
        self.key = key
        self.samplerate = int(samplerate)
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        # key указывает, какой из потоков манифеста читаем: у ScreenCaptureKit
        # их два — «system» и «mic». Частота у каждого своя: система отдаёт
        # запрошенную, а микрофон — родную частоту устройства (48 кГц), и
        # спутать их значит растянуть голос.
        rate = manifest.get(f"{key}_rate", manifest["samplerate"])
        self._m = dict(manifest, path=manifest[key], samplerate=rate)
        engine = manifest.get("engine", "sck")
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
            # Первый старт. Приложение с #564 пишет в манифест «<key>_start» — с
            # какого байта читать: каталог сессии уникален, в файле только эта
            # встреча, и 0 означает «с первого кадра». Раньше прыгали в хвост, и
            # всё, что приёмник записал до старта демона (1,2 с ожидания кадров,
            # до 10 с ожидания микрофона, загрузка python), в стенограмму не
            # попадало (круг-1 по #564, DS Critical / GLM I1). Без поля — старое
            # приложение: хвост, как прежде.
            start = self._m.get(f"{self.key}_start")
            if (isinstance(start, (int, float)) and not isinstance(start, bool)
                    and 0 <= int(start) <= size and int(start) % 2 == 0):   # граница сэмпла s16 (GLM M1 r2)
                stream.seek(int(start))
            else:
                stream.seek(0, 2)           # хвост прошлой встречи не нужен
        # Первые байты обязаны прийти быстро: приложение выписывает манифест
        # только после реальных кадров. Нет роста — канала нет, и честнее
        # упасть здесь (поканальный старт скажет об этом вслух), чем писать
        # пустоту до конца встречи.
        # Рост считаем сверх размера на момент манифеста (`<key>_bytes`), а не
        # сверх позиции чтения: при чтении с нуля по непустому файлу проверка
        # иначе проходила сразу, и мёртвый приёмник ловил только сторож тишины
        # (критика GLM r2 по #564). Без поля — старое приложение, прежняя проверка.
        floor = self._m.get(f"{self.key}_bytes")
        floor = int(floor) if isinstance(floor, (int, float)) and not isinstance(floor, bool) else stream.tell()
        deadline = time.time() + 3.0
        while path.stat().st_size <= max(floor, stream.tell()):
            if time.time() > deadline:
                stream.close()
                raise RuntimeError("поток приложения не растёт — приложение кадров не пишет")
            time.sleep(0.1)
        down = (_Downsampler(src_sr, self.samplerate)
                if src_sr != self.samplerate else None)
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._pump_file, args=(stream, down),
            daemon=True, name=f"appstream-{self.label}")
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

        Дальше — ради устройств с фиксированной частотой. Так вёл себя агрегат
        Core Audio tap (06.08; сам тап снят 02.09): частоту он брал у физического
        выхода, то есть 44.1 или 48 кГц. Частоту агрегата PortAudio поменять не может,
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


def _safe_stderr(msg: str) -> None:
    """Строка в stderr демона с пути потока-потребителя: полный диск или закрытый
    stderr не должны ронять запись (I3 DS по №235). Одна обёртка на модуль —
    раньше их было три ручных, и четвёртая (сообщение о сбое записи) стояла
    без защиты (Critical GLM входного круга по №311)."""
    try:
        print(msg, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — носитель отказал, работа важнее следа
        pass


@dataclasses.dataclass(frozen=True)
class Discovery:
    """Итог обнаружения устройств: каналы, их человеческие имена и
    происхождение канала собеседников (для причины в предупреждении)."""
    captures: list
    sources: list[str]
    system_origin: dict


def discover_captures(mode: str, sr: int) -> Discovery:
    """Найти источники звука для режима `audio.device` — единственное место с
    вводом-выводом (опрос ScreenCaptureKit и PortAudio) при сборке хаба;
    состояния хаба не трогает. Конструктор `AudioHub` вызовов наружу не
    делает, и тесты зовут его напрямую (входной круг DS и GLM по №311).

    Порядок источников системного звука — от лучшего к запасному:
    1. ScreenCaptureKit: ничего не создаёт в CoreAudio, а с macOS 15
       приносит и микрофон тем же потоком — PortAudio не нужен вовсе;
    2. BlackHole — проверенный драйвер, но требует установки руками.
    Третьего пути нет: поток Core Audio tap снят 02.09 (см. find_system_audio).
    """
    captures: list = []
    sources: list[str] = []
    sck = fresh_sck_manifest()
    bh = None if sck else find_system_audio()
    mic = sd.default.device[0] if sd.default.device else None
    # Микрофон в манифесте = система отдаёт оба канала одним потоком.
    mic_from_stream = bool(sck and sck.get("mic"))

    if mode in ("auto", "mix", "blackhole") and sck is not None:
        captures.append(TapStreamCapture(sck, sr, "blackhole", key="system"))
        sources.append("Системный звук (ScreenCaptureKit)")
    elif mode in ("auto", "mix", "blackhole") and bh is not None:
        captures.append(Capture(bh, sr, "blackhole"))
        sources.append("BlackHole")
    # auto = система И микрофон: на встрече нужны обе стороны разговора
    if mode in ("mic", "mix", "auto") and mode != "blackhole":
        if mic_from_stream:
            # Микрофон тем же потоком (macOS 15+): PortAudio не открывается
            # вообще, и вместе с ним уходит класс аварий «мёртвый стрим
            # виснет на close», стоивший записей 20.07 и 06.08.
            captures.append(TapStreamCapture(sck, sr, "mic", key="mic"))
            sources.append("Микрофон (ScreenCaptureKit)")
        elif mode != "auto" or bh is None or mic is not None or sck is not None:
            captures.append(Capture(mic, sr, "mic"))
            sources.append("Микрофон")
    if not captures:  # blackhole запрошен, но не найден
        captures.append(Capture(mic, sr, "mic"))
        sources.append("Микрофон (fallback)")
    # BlackHole ищут только без SCK (строка выше), поэтому «устройства не видно»
    # — правда лишь при sck is None; иначе это ложная причина (DS и GLM r1 по #537)
    return Discovery(captures, sources,
                     {"sck_missing": sck is None, "bh_missing": bh is None and sck is None})


class AudioHub:
    # Подписка на сырые фреймы (для быстрого триггера gigastt): callback(source, float32[])
    on_frame = None
    # Статусы для UI (рестарт стрима и т.п.): callback(str)
    on_status = None
    # Событие канала (пропал / вернулся / дыра / не вернулся до конца): callback(ChannelEvent).
    # Структурный выход рядом со строковым: след встречи строится по нему, не по
    # подстрокам статуса (№234; строковый контракт статуса — №310)
    on_channel = None

    """Держит источники (mic = владелец, blackhole = собеседники) РАЗДЕЛЬНО.

    Раздельные каналы дают бесплатную диаризацию «я/они»: pull_labeled()
    отдаёт (speaker, chunk). При одновременной речи в обоих каналах
    микрофонный чанк отбрасывается — это эхо динамиков в микрофоне.
    """

    SPEAKER = {"blackhole": "Собеседник", "mic": "Я"}

    def __init__(self, cfg: dict, stamp: str | None = None, *,
                 captures: abc.Iterable = (), sources: abc.Iterable[str] = (),
                 system_origin: dict | None = None):
        """Состояние хаба — и только оно. Ни одного вызова наружу: устройства
        находит `discover_captures`, боевой путь собирает хаб через
        `for_meeting`. Раньше конструктор трогал ScreenCaptureKit/PortAudio, и
        тесты не могли его звать: четыре оснастки собирали хаб через
        `object.__new__` со своими списками полей, а боевой код 14 местами
        страховался от собственной оснастки `getattr`/`hasattr`. Все поля —
        здесь, одним списком, включая флаги жизненного цикла (`_closing`) и
        итоги обнаружения (дефолты «не видно», бой перезаписывает через
        аргументы) — входной круг DS и GLM по №311, 18.09."""
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
        # Правило одно на захват и демон — channel_labels.mic_label_for (D-П2)
        self.SPEAKER = {**self.SPEAKER,
                        "mic": channel_labels.mic_label_for(cfg, self.SPEAKER["blackhole"])}
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
        self.captures: list = list(captures)
        self.sources: list[str] = list(sources)
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
        self._closing = False          # stop() финализирует файлы: блоки в них больше не пишем
        self._pump_thread: threading.Thread | None = None
        self._restarting: set[str] = set()   # каналы, чей перезапуск сейчас в полёте (_restart_guarded)
        self.finalized: dict[str, pathlib.Path] = {}   # готовые .wav после stop() — по метке канала
        # сбои прохода потребителя: тип исключения → когда о нём говорили
        # последний раз (троттлинг репортёра, см. _tick)
        self._guard_said: dict[str, float] = {}
        self._pump_failures = 0        # проходов потребителя, упавших подряд (единица — проход, не блок)
        # Метка канала осталась «blackhole» намеренно: по ней названы файлы
        # записей (`..._blackhole.wav`), её знают rebuild_transcript и
        # meeting_stamp. Переименование метки сломало бы пересборку старых
        # встреч ради косметики.
        mode = a["device"]
        # Происхождение канала собеседников: start() судит ещё раз, уже по факту
        # открытия (№230), и ему нужна та же причина. Без обнаружения (тесты,
        # CLI без устройств) — «устройств не видно»: честный дефолт, а не
        # выдуманная причина (Minor DS/GLM r1 по #537)
        self._system_origin = dict(system_origin or {"sck_missing": True, "bh_missing": True})
        self._no_system_channel = None
        # Реестр потерь — единственное состояние «какие каналы сейчас не
        # пишут»: метка → причина. Заполняют все поводы (нет на старте, не
        # открылся, завис, две неудачи подряд), читает один компоновщик
        # текста; пока каналом был только собеседник, состояние жило двумя
        # флагами про него, и потеря микрофона осталась тихой строкой
        # (входной круг DS и GLM по №235). `_warned` — по каким меткам за эту
        # потерю уже звенели: уведомление — одно на потерю канала, строка
        # статуса — при каждой смене состава.
        self._lost: dict[str, Loss] = {}
        self._warned: set[str] = set()
        self.channel_log: list[ChannelEvent] = []   # журнал событий канала за запись (№234)
        self._ended: set[str] = set()                # эпизоды, закрытые остановкой (end — один раз)
        self._mode = mode                          # `device` из конфига: намеренный один канал ≠ авария (DS M4)
        self._fail_streak: dict[str, int] = {}   # неудачные рестарты подряд по каналу
        self._scream_count = 0            # криков за встречу — потолок звука LOUD_SCREAMS
        # Канала собеседников нет — встреча запишется ОДНИМ микрофоном, и в
        # стенограмме не будет второй стороны разговора. До 10.09 об этом
        # сообщала только строка статуса «Слушаю: Микрофон (fallback)» рядом
        # с названием модели: на встрече такое не замечают, а узнают через час
        # по пустой стенограмме. С удалением BlackHole (№137) запасного пути
        # не осталось вовсе, поэтому предупреждение обязано быть громким.
        #
        # Здесь только ЗАПОМИНАЕМ факт, а говорим в start(). Причина в порядке
        # проводки: daemon.py строит AudioHub и лишь потом вешает on_status, а
        # main.py не вешает его вовсе. Предупреждение из конструктора уходило в
        # `self.on_status is None` и не долетало до интерфейса НИКОГДА — то есть
        # «громко» было ровно наполовину (уведомление и лог), а обещанная строка
        # статуса молчала (круг 1, DS и GLM независимо, 10.09).
        #
        # Режим mic исключён сознательно: там пользователь сам просит один
        # микрофон (диктовка, личные заметки), и канала собеседников не будет
        # по построению. Кричать об этом — ложная тревога на каждом запуске.
        if mode != "mic" and self.captures and not any(c.label == "blackhole" for c in self.captures):
            self._no_system_channel = dict(self._system_origin)
        self._register_captures(self.captures)

    def _register_captures(self, captures: abc.Iterable) -> None:
        """Поканальные словари под состав: STT-буфер на каждую метку. Один
        путь для конструктора и для оснастки, которая подставляет каналы
        после (Important GLM 2 входного круга по №311)."""
        for c in captures:
            self._bufs.setdefault(c.label, np.zeros(0, dtype=np.float32))

    @classmethod
    def for_meeting(cls, cfg: dict, stamp: str | None = None) -> "AudioHub":
        """Боевой путь: найти устройства и собрать хаб. Единственное место,
        где конструирование хаба трогает ScreenCaptureKit и PortAudio."""
        a = cfg["audio"]
        found = discover_captures(a["device"], int(a["samplerate"]))
        return cls(cfg, stamp, captures=found.captures, sources=found.sources,
                   system_origin=found.system_origin)

    # Уведомлений со звуком за встречу — не больше трёх: флапающий поток
    # (то пишется, то нет) иначе звенел бы каждые полторы минуты до конца
    # встречи (критика GLM r2 по #541); строка статуса — при каждой потере
    LOUD_SCREAMS = 3

    def _warn_no_system_channel(self, *, sck_missing: bool = False, bh_missing: bool = False,
                                start_error: str | None = None, died: str | None = None,
                                retrying: bool = False) -> None:
        """Поводы потери канала СОБЕСЕДНИКОВ — и только они; кричит общий
        компоновщик `_announce_losses`. Причина и совет — `_system_loss`."""
        reason = self._system_loss(sck_missing=sck_missing, bh_missing=bh_missing,
                                   start_error=start_error, died=died)
        # повод старта без открывшегося устройства — не «перезапустим»: сторож
        # канала, которого нет в captures, не увидит; при отказе открытия и
        # зависании — тоже нет повторных попыток
        cause = "" if died else ("start_error" if start_error else "missing")
        self._announce_losses({"blackhole": Loss(reason, retriable=retrying, died=bool(died), cause=cause)})

    def _system_loss(self, *, sck_missing: bool = False, bh_missing: bool = False,
                     start_error: str | None = None, died: str | None = None) -> str:
        """Причина потери канала собеседников — с хвостом capture.log.

        Причину знает Swift-часть: она поднимает ScreenCaptureKit и пишет ход
        в logs/capture.log. Питон видит только отсутствие манифеста, поэтому
        последнюю строку лога подхватываем — иначе разбираться придётся
        вручную и уже после встречи. `start_error` — второй повод: устройство
        собеседников нашлось, но открыть его при старте не удалось (№230).
        `died` — третий: канал жил, но умер во время встречи (№232). Хвост
        capture.log — только здесь: для микрофона (PortAudio) строка Swift
        была бы чужой причиной (I1 входного круга по №235).
        """
        why = []
        if died:
            why.append(f"канал собеседников умер во время встречи: {died}")
        if start_error:
            why.append(f"канал собеседников найден, но не открылся при старте: {start_error}")
        if sck_missing:
            why.append("ScreenCaptureKit не поднялся (нет свежего "
                       f"{SCK_STREAM_MANIFEST.name}; проверить право «Запись экрана»)")
        if bh_missing:
            why.append("устройства системного звука не видно")
        tail = self._capture_log_tail()
        if tail:
            why.append(f"последняя строка capture.log: {tail}")
        return "; ".join(why) or "причина неизвестна"

    def _capture_log_tail(self) -> str | None:
        """Последняя строка Swift-части в logs/capture.log — причина смерти
        потока ScreenCaptureKit, которую питон иначе не знает.

        Битый хвост (Swift дописывает многобайтовую букву прямо сейчас)
        раньше давал UnicodeDecodeError — наследника ValueError, а не
        OSError, — и он убивал старт записи целиком (круг 1, DS 10.09).
        Основная защита — errors="replace": невалидные байты становятся
        U+FFFD, причина сохраняется. ValueError в except — страховка на
        случай, если replace когда-нибудь снимут; при replace эта ветка
        недостижима (круг 2, обе головы) — это задумано, а не забыто.

        Свои же строки в хвост не берём: предупреждение пишется в этот
        самый лог, и через встречу причиной становилось бы эхо прошлой
        встречи вместо сообщения Swift. Метки обеих потерь (собеседники,
        микрофон) — иначе строка о микрофоне стала бы «причиной» смерти
        собеседников."""
        try:
            log = ROOT / "logs" / "capture.log"
            tail = [ln.strip() for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip() and not any(mark in ln for mark in LOSS_LOG_MARKS)]
            return tail[-1] if tail else None
        except (OSError, ValueError):
            return None

    # ------------------------------------------------ потеря и возврат канала
    # Одно событие «канал не пишет» с тремя носителями — строка статуса (липкая,
    # до конца встречи), уведомление со звуком, строка в capture.log — и одним
    # компоновщиком текста по полному составу потерянных и живых каналов.
    # Раньше этим владел специальный случай собеседников (три повода, два флага,
    # тексты и совет — про них), а смерть микрофона оставалась тихой строкой
    # сторожа: владелец до конца встречи говорил в мёртвый микрофон и узнавал об
    # этом из стенограммы (№235). Липкий слой в приложении ОДИН, поэтому строка
    # пересобирается при каждой смене, а отбой уходит только когда потерянных
    # не осталось: два маркера с поканальным снятием стёрли бы предупреждение о
    # ещё мёртвом канале (Critical DS и GLM входного круга).
    _NAMES_GEN = {"blackhole": "собеседников", "mic": "вашего микрофона"}   # для «нет X»

    def live_labels(self) -> list[str]:
        """Каналы, которые пишут сейчас: состав минус потерянные."""
        return [c.label for c in self.captures if c.label not in self._lost]

    def _carrier(self, *, died: bool = True) -> tuple[str, str, str]:
        """(маркер липкой строки, что осталось — для строки статуса, то же —
        для баннера) по составу потерянных и живых, не по статике `captures`:
        на macOS 15+ оба канала — один поток ScreenCaptureKit, и умирают
        вместе (C2 DS). Формулировки собеседников — прежние (№230/№232),
        на них стоят тесты и глаза владельца."""
        live = set(self.live_labels())
        lost = set(self._lost)
        later = "дальше " if died else ""
        if self._mode == "mic" and lost == {"mic"}:
            # один микрофон выбран сознательно: о собеседниках здесь не
            # говорят вовсе — их и не должно было быть (Minor DS выходного круга)
            return (stt_runtime.RECORDING_EMPTY, ": микрофон не пишется, запись пуста",
                    "микрофон не пишется, запись пуста.")
        if "mic" in live and lost == {"blackhole"}:
            return (stt_runtime.MIC_ONLY_WARNING, f", {later}пишем только микрофон",
                    f"{later}в записи {'' if died else 'будет '}только ваш микрофон, без собеседников.")
        if "blackhole" in live and lost == {"mic"}:
            return (stt_runtime.OWNER_MIC_LOST, f", {later}пишем только собеседников, ваших реплик в записи не будет",
                    f"{later}в записи {'' if died else 'будут '}только собеседники, без ваших реплик.")
        mark = stt_runtime.MIC_ONLY_WARNING if lost == {"blackhole"} else stt_runtime.RECORDING_EMPTY
        return mark, ": в записи не будет ни собеседников, ни вас", "в записи не будет ни собеседников, ни вас."

    def _advice(self, losses: dict[str, "Loss"]) -> str:
        """Совет человеку — из ЗНАЧЕНИЙ потерь, одной таблицей. Подать совет,
        противоречащий причине, отсюда нельзя: у вызывающего нет параметра.

        Микрофон, который сторож ещё перезапускает: железо (наушники, хаб)
        пропало, перезапуск записи его не вернёт, зато разрежет встречу на
        два файла (№260) — не прерывать. Микрофон брошенный (`_hung`) или не
        открывшийся: повторных попыток не будет, единственное действие —
        перезапуск записи (Critical DS выходного круга по №235). Собеседники —
        по поводу, как с №230/№232: право «Запись экрана» при полном
        отсутствии канала, освобождение устройства при отказе открытия,
        перезапуск записи при смерти, «пробуем сами» при повторах.
        """
        if len(losses) > 1:
            if all(v.retriable for v in losses.values()):
                return ("Каналы пробуем перезапустить каждые полминуты; не снялось через пару минут — "
                        "остановите и запустите запись заново.")
            return "Запись потеряла все каналы — остановите и запустите её заново."
        (label, loss), = losses.items()
        if label == "mic":
            if loss.retriable:
                return ("Проверьте, на месте ли микрофон или наушники; запись не прерывайте — "
                        "канал перезапускается сам, предупреждение снимется.")
            if loss.died:
                # без «до конца встречи»: детектор по кадрам умеет снять потерю,
                # если зависший поток отлип (M3 GLM); без «не прерывайте»:
                # повторных попыток сторож не делает (Critical DS круга 1)
                return ("Микрофон потерян — проверьте устройство; не вернулся за минуту — "
                        "остановите и запустите запись заново.")
            return "Микрофон не открылся — проверьте устройство и запустите запись ещё раз."
        if loss.retriable:
            # ветка повторов канал не бросает — совет «перезапустите» тут
            # ложный: через полминуты поток может ожить (критика DS r2)
            return ("Канал пробуем перезапустить каждые полминуты; не снялось через пару "
                    "минут — остановите и запустите запись заново.")
        if loss.died:
            return "Канал собеседников отвалился во время встречи — остановите и запустите запись заново."
        if loss.cause == "start_error":
            # при отказе открытия право «Запись экрана» в порядке, и посылать
            # человека в настройки TCC — ложный адрес (Important DS и GLM r1 по #537)
            return "Устройство собеседников не открылось — освободите его и запустите запись ещё раз."
        return "Проверьте право «Запись экрана»."

    def _announce_loss(self, label: str, reason: str, *, died: bool = True,
                       retriable: bool = False, cause: str = "") -> None:
        """Один канал не пишет — см. `_announce_losses`."""
        self._announce_losses({label: Loss(reason, retriable=retriable, died=died, cause=cause)})

    def _announce_losses(self, losses: dict[str, "Loss"]) -> None:
        """Каналы `losses` (метка -> Loss) не пишут: запомнить все, потом
        пересобрать липкую строку по ПОЛНОМУ составу — один проход сторожа
        может потерять оба канала разом (один поток ScreenCaptureKit на
        macOS 15+), и крик по первому из них до проверки второго обещал бы
        «дальше только микрофон» при пустой записи (C2 DS). Одно уведомление
        на проход, если среди потерь есть новая и потолок LOUD_SCREAMS не
        исчерпан; строка в capture.log и stderr — на каждую метку. Фаза
        (жил и пропал / не захвачен) — у каждой потери своя (критика GLM 1)."""
        import datetime                       # локально: шапку аудио-модуля не трогаем
        import subprocess

        # фаза у пачки одна по построению (_loss_of ставит died=True всем потерям
        # прохода); смешанная — не повод молчать о потерях: raise здесь стоял в
        # finally сторожа и убил бы объявление вместе с потоком-потребителем
        # (Important DS входного круга по №311) — считаем «жил и пропал», если
        # так у любой из потерь, и оставляем след
        phases = {v.died for v in losses.values()}
        if len(phases) > 1:
            _safe_stderr("потери одного объявления пришли разных фаз: " + ", ".join(sorted(losses)))
        now = time.time()
        # событие — не «метка появилась», а «значение изменилось»: канал,
        # который пробовали перезапустить (звук «не прерывайте»), а потом
        # бросили, обязан прозвучать снова — совет стал требовать действия
        # (Important DS и GLM круга 2). Момент постановки при смене фазы
        # сохраняется: «без канала было N с» — от начала эпизода (M3 DS)
        fresh = []
        for lbl, loss in losses.items():
            prev = self._lost.get(lbl)
            if prev is None:
                # кадр ПОСЛЕ этого момента — канал ожил (Important GLM круга 1)
                loss.since = max(now, self._last_frame.get(lbl, 0.0))
                # граница дыры — последний кадр, не момент крика; канала без
                # кадров (не захвачен с начала) — начало записи неизвестно: None
                loss.stopped_at = self._last_frame.get(lbl) or None
                if not loss.cause:
                    loss.cause = (("restart_failed" if loss.retriable else "hung")
                                  if loss.died else "start_error")
                # смена фазы того же эпизода (повторы → брошен) — не новое событие:
                # эпизод один, след встречи считает по парам, не по крикам (I4 GLM)
                self._channel_event(CH_LOST, lbl, now, loss.stopped_at, None, loss)
            else:
                loss.since = prev.since
                loss.stopped_at = prev.stopped_at
                if not loss.cause:
                    loss.cause = prev.cause
            if lbl not in self._warned or (prev is not None and prev.phase() != loss.phase()):
                fresh.append(lbl)
        self._lost.update(losses)
        died = any(v.died for v in losses.values())   # смешанная фаза → «жил и пропал» (DS I3 по №311)
        mark, what, banner_what = self._carrier(died=died)
        names = {"blackhole": "системный звук", "mic": "ваш микрофон"}
        lost_names = [names.get(lbl, lbl) for lbl in losses]
        many = len(lost_names) > 1
        verb = ("пропали" if many else "пропал") if died else ("не захвачены" if many else "не захвачен")
        lost = f"{' и '.join(lost_names)} {verb}"
        reason = "; ".join(v.reason for v in losses.values())
        self._say(f"⚠️ {mark}: {lost}{what}. {reason}")
        # звук — на новую потерю канала и не чаще LOUD_SCREAMS за встречу:
        # флапающий поток иначе звенел бы каждые полторы минуты (GLM r2 по #541)
        loud = bool(fresh) and self._scream_count < self.LOUD_SCREAMS
        if fresh:
            self._scream_count += 1
            self._warned.update(fresh)
        try:
            # Popen, а не run: это вызывается на пути старта записи, и
            # залипший osascript (занятый центр уведомлений, первый показ под
            # TCC) отодвигал бы открытие каналов на весь таймаут — первые
            # секунды разговора не попали бы в файл. Уведомление со звуком:
            # беззвучный баннер за развёрнутым окном встречи не замечают.
            body = f"{lost[0].upper()}{lost[1:]} — {banner_what}"
            if not loud:
                raise RuntimeError("потолок уведомлений со звуком за встречу")   # строка и лог остаются
            subprocess.Popen(
                ["osascript", "-e",
                 f'display notification "{body} {self._advice(losses)}" '
                 'with title "Чароит: запись неполная" sound name "Glass"'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)       # как у остальных fire-and-forget Popen (DS r2 по #531)
        except Exception:                       # noqa: BLE001 — уведомление не должно ронять запись
            pass
        for label, loss in losses.items():
            log_mark = MIC_ONLY_LOG_MARK if label == "blackhole" else MIC_LOST_LOG_MARK
            try:
                log = ROOT / "logs" / "capture.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.datetime.now():%F %H:%M:%S} {log_mark}: {loss.reason}\n")
            except OSError:
                pass
            # след в stderr демона: статусы каналов в его лог не пишутся, и
            # частота потерь до сих пор была неизмерима (Minor DS входного круга)
            _safe_stderr(f"канал {label} потерян: {loss.reason}; пишут: {', '.join(self.live_labels()) or 'никто'}")

    def _announce_back(self, label: str, silent: float) -> str:
        """Канал `label` ожил -> строка статуса. Потерянных не осталось —
        отбой липкой строки (`sticky: false`); кто-то ещё потерян —
        пересобранная липкая строка о нём: снимать слой целиком нельзя,
        он один на все каналы. Канал возвращается и сторожу: из `_hung`
        снимается, иначе следующая смерть не получила бы ни рестарта, ни
        крика (Important GLM выходного круга)."""
        loss = self._lost.pop(label, None)
        self._warned.discard(label)          # следующая потеря этого канала кричит заново
        self._fail_streak.pop(label, None)
        self._hung.discard(label)
        if loss is not None:
            now = time.time()
            # длительность — от последнего кадра, не от крика: крик опаздывает на
            # порог сторожа и попытки рестарта (Critical GLM входного круга по №234)
            true_silent = now - loss.stopped_at if loss.stopped_at else silent
            self._channel_event(CH_BACK, label, now, loss.stopped_at, true_silent, loss)
        name = "канал собеседников" if label == "blackhole" else "ваш микрофон"
        if not self._lost:
            back = stt_runtime.MIC_BACK_NOTICE if label == "blackhole" else stt_runtime.OWNER_MIC_BACK
            # «снова пишется», не «запись снова полная»: остальные каналы могли
            # не открыться с начала (Important DS r2)
            return (f"✅ {back}: {name} снова пишется; "
                    f"без {'собеседников' if label == 'blackhole' else 'вашего микрофона'} было около {int(silent)}с")
        mark, what, _banner = self._carrier()
        still = ", ".join(self._NAMES_GEN.get(k, k) for k in self._lost)
        return f"⚠️ {mark}: {name} снова пишется, но в записи по-прежнему нет: {still}{what}"

    # Сколько ждём перезапуск канала, прежде чем считать его безнадёжным.
    # Пять секунд: закрытие живого стрима укладывается в доли секунды, а
    # мёртвый не возвращается никогда.
    RESTART_TIMEOUT = 5.0
    #: бюджет стоп-фазы (остановка каналов + ожидание _pump) — отдельно от потолка
    #: перезапуска: тот настраивают ради живой ленты, этот зажат грейсом
    #: приложения до terminate (8 с от «stop»; критика GLM r1 по #557)
    STOP_TIMEOUT = 5.0
    #: сколько stop() ждёт выхода _pump до дренажа очередей — в пределах ОБЩЕГО
    #: бюджета RESTART_TIMEOUT: join покупает лишь то, что _pump не окажется внутри
    #: sink.write при закрытии файла, хвост спасает _drain_queues; 3 с сверх бюджета
    #: съедали грейс приложения до terminate (DS r1 I1 по #557)
    PUMP_JOIN_TIMEOUT = 1.0

    def start(self):
        self._running = True
        # Предупреждение о записи без собеседников — здесь, а не в __init__:
        # к моменту start() потребитель уже повесил on_status (daemon.py и
        # main.py), и строка доходит до интерфейса. Первым делом, до открытия
        # файлов: человек должен увидеть это в начале встречи, а не после неё.
        # Честно про носители: статус в Swift нелипкий, следующий emit демона
        # (диаризация, первые чанки) его сменяет за секунды — главный носитель
        # для человека это уведомление со звуком и строка в capture.log; липкий
        # статус ошибки записи — отдельная работа (круг 2 GLM по #531).
        if self._no_system_channel:
            self._warn_no_system_channel(**self._no_system_channel)
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
        # Канал собеседников нашёлся в конструкторе, но открыть его не удалось
        # (устройство занято, отозвано, поток приложения не растёт): гейт выше
        # судит по НАЛИЧИЮ, и встреча шла одним микрофоном с одной тихой
        # строкой — тот же симптом, что чинил #531 (Important DS r2, №230).
        # Взаимоисключение с гейтом конструктора — по _no_system_channel: тот
        # взводится только когда канала в captures нет, а здесь канал был.
        # Не открылся ЛЮБОЙ канал — крик, не тихая строка: отказ микрофона на
        # старте — зеркало №230, встреча шла бы одними собеседниками без реплик
        # владельца (I2 входного круга по №235). Все каналы разом — RuntimeError
        # выше, там громко и так.
        # возраст кадров — ДО криков: постановка в реестр запоминает момент, и
        # кадр ПОСЛЕ него означает «канал ожил»; поставь возраст после крика —
        # сторож счёл бы неоткрывшийся канал ожившим на первом же проходе
        now = time.time()
        for c in self.captures:
            self._last_frame[c.label] = now
        for lbl, err in failed:
            short = " ".join(str(err).split())[:300]
            if lbl == "blackhole":
                if self._no_system_channel:
                    continue                  # канала не было с конструктора — уже кричали
                # одной строкой: текст уходит и в capture.log, где запись = строка,
                # а хвост без метки припишется Swift-части (DS r1 по #537)
                self._warn_no_system_channel(**self._system_origin, start_error=short)
            else:
                self._announce_loss(lbl, f"канал не открылся при старте: {short}", died=False)
        self._pump_thread = threading.Thread(target=self._pump, daemon=True, name="audio-pump")
        self._pump_thread.start()

    def stop(self):
        self._running = False
        self.end_channel_episodes()
        # Каналы останавливаем тем же приёмом, что _restart_guarded: stop()
        # мёртвого PortAudio-стрима не возвращается, и try/except от этого не
        # спасает — зависание не исключение. Канал из _hung (и канал, чей
        # перезапуск сейчас в полёте) уже держит поток в close/open того же
        # стрима: второй вход в PortAudio параллельно — недокументированная
        # территория, а stop() хаба на нём висел навсегда, до
        # _finalize_recordings дело не доходило (аудит 13.09, DS I1; DS r1 M4 по
        # #557). Решение явное: такой стрим не закрываем, его очередь читает
        # дренаж ниже, а рост очереди после финализации ограничен секундами до
        # выхода процесса (критика DS r1 по #557). Все каналы разом, один потолок
        # на всех: грейс приложения до terminate — секунды.
        skip = {c.label for c in self.captures if self._busy(c.label)}
        workers = [(c, threading.Thread(target=self._quiet_stop, args=(c,), daemon=True,
                                        name=f"stop-{c.label}"))
                   for c in self.captures if c.label not in skip]
        for _c, w in workers:
            w.start()
        deadline = time.monotonic() + self.STOP_TIMEOUT
        for c, w in workers:
            w.join(max(0.0, deadline - time.monotonic()))
            if w.is_alive():
                self._say(f"🎙 канал {c.label}: стрим не закрылся за {self.STOP_TIMEOUT:.0f}с — "
                          "бросаю, запись финализирую без него")
        # Хвост очередей: _pump выходит по _running, не дренируя c.q. В норме там
        # ≤1 блок (0,25 с), но пока _pump стоит в _restart_guarded, копится до
        # RESTART_TIMEOUT на канал. Сначала дожидаемся самого _pump (он же читает
        # очереди) — в пределах того же бюджета, потом добираем остаток в sink и
        # STT-буфер и только затем закрываем файлы: иначе _pump писал в уже
        # закрытый sink и кричал «ЗАПИСЬ НА ДИСК ОСТАНОВИЛАСЬ» о звуке, который
        # записан (аудит 13.09, DS I2/M3, GLM M3/M4). Финализация — в finally:
        # сбой дренажа не должен оставить .pcm без .wav (DS r1 I2 по #557).
        pump = self._pump_thread
        if pump is not None and pump is not threading.current_thread():
            pump.join(max(0.0, min(self.PUMP_JOIN_TIMEOUT, deadline - time.monotonic())))
        try:
            if pump is not None and pump.is_alive() and pump is not threading.current_thread():
                # _pump не вернулся за бюджет — он застрял внутри _consume (диск), а
                # не в _restart_guarded (оттуда выход по _running мгновенный). Второй
                # читатель той же очереди перемешал бы хвост, а запись в закрытый
                # sink кричала бы ложное «ЗАПИСЬ НА ДИСК ОСТАНОВИЛАСЬ»: очередь ему,
                # финализация — с флагом _closing, который _consume проверяет до
                # записи (GLM r1 I1 по #557).
                self._say("🎙 поток захвата не вернулся за бюджет стопа — хвост очередей "
                          "не добираю, запись финализирую")
            else:
                self._drain_queues()
            self._say_last_drops()
        finally:
            with self._lock:
                self._closing = True
            self._finalize_recordings()

    @staticmethod
    def _quiet_stop(c) -> None:
        try:
            c.stop()
        except Exception:  # noqa: BLE001 — исключение из мёртвого стрима не новость
            pass

    def _drain_queues(self) -> None:
        """Остаток очередей захвата после выхода _pump — тем же путём, что и
        живой блок: файл записи и STT-буфер; быстрый триггер не дёргаем —
        подсказка после «Стоп» никому не нужна (DS r1 M3 по #557). Сбой одного
        канала не останавливает дренаж остальных."""
        for c in self.captures:
            try:
                while True:
                    try:
                        part = c.q.get_nowait()
                    except queue.Empty:
                        break
                    self._consume(c, part, notify_frame=False)
            except Exception as e:  # noqa: BLE001 — хвост важен, но финализация важнее
                self._say(f"🎙 канал {c.label}: хвост очереди не дописан ({e})")

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
            # Уже открытые файлы соседних каналов — закрыть и убрать: кадров в них
            # нет, а без этого .pcm-заготовка висела бы до ретеншна, не попадая ни
            # в finalized, ни в уборку (аудит 13.09, DS M4).
            for f in self._sinks.values():
                try:
                    f.close()
                    pathlib.Path(f.name).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001 — уборка сирот не важнее статуса
                    pass
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
        файлы жили ВНЕ ретеншна: каталоги `sck/<uuid>/` убирались лишь при
        штатном стопе своей сессии — краш, SIGKILL или перезагрузка оставляли
        полное аудио встречи навсегда. На рабочей машине так пролежал 61 МБ
        системного звука девять дней при обещанных двух (аудит 16.08).
        PRIVACY.md обещает «записи временны» — обещание должно покрывать и
        этот слой. Наследие Core Audio tap (`tap_stream.raw|json`) убиралось
        здесь безусловно до 06.09; ветка снята вместе с `TapOrphanCleanup`
        в приложении (№154). Допущение, не замер: телеметрии обновлений
        нет, известные установки прошли через 0.69–0.72; отставший с ≤0.68
        получит лежащий без срока tap_stream.* — убрать руками.

        Живую сессию не трогаем: её каталог назван в свежем манифесте.
        Возвращает число удалённых путей.
        """
        if not data_dir.exists():
            return 0
        cutoff = time.time() - float(keep_days) * 86400
        alive: set[str] = set()
        manifest = fresh_sck_manifest() or {}
        for key in ("system", "mic"):
            p = manifest.get(key)
            if p:
                alive.add(str(pathlib.Path(p).resolve()))
        removed = 0

        def _old_enough(p: pathlib.Path) -> bool:
            try:
                return p.stat().st_mtime < cutoff
            except OSError:
                return False

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
        Готовые .wav — в self.finalized[label] (для тестов и вызывающих; демон их
        не читает — пересборку .wav подбирает rebuild_transcript.wait_recording)."""
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

    #: не чаще этого репортёр говорит об одном типе сбоя прохода: сторож идёт
    #: раз в 5 с, и без окна устойчивый баг дал бы строку каждые пять секунд
    GUARD_REPORT_S = 30.0

    def _pump(self):
        """Поток-потребитель: каждый источник — в свой буфер, без микса
        (спикеры не смешиваются). Цикл — только `_tick`: у потребителя одна
        точка, владеющая его живучестью, а не набор вызовов, каждый из
        которых защищён или нет по отдельности."""
        while self._running:
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — скелет прохода (get, атрибуты канала) тоже не роняет поток
                # два разных гварда, нужны оба: внутренние (у единицы работы) не
                # дают дурному блоку съесть соседей и сторож, внешний — не даёт
                # потоку умереть от исключения в самом скелете прохода (круг 2 DS
                # I1 по №311). Сон — против холостого цикла на 100 % ядра
                self._report_pump_failure(e)
                time.sleep(0.05)
        # Хвост, домолотый уже после `stop()`, иначе не озвучивает никто:
        # окно отчёта — полминуты, а досказ в `stop()` к этому моменту уже
        # отработал. Метод идемпотентен, двойной строки не будет
        # (ревью 20.08, круг 3, DeepSeek). Исключение здесь уже никого не
        # убивает, но унесло бы тишину о недобранном хвосте (Minor DS по №311)
        try:
            self._say_last_drops()
        except Exception as e:  # noqa: BLE001
            self._report_pump_failure(e)

    def _tick(self) -> None:
        """Один проход потребителя: блок каждого канала → файл и STT-буфер
        (`_consume`), затем сторож каналов (`_watch_streams`). Гвард — у каждой
        ЕДИНИЦЫ работы, а сторож идёт последним и всегда.

        До №311 сторож стоял в цикле голым, и любое исключение его прохода —
        баг в `_sweep`, незаведённое поле, отказ носителя — убивало поток:
        остаток встречи не писался ни на диск, ни в STT; приложение видело это
        лишь как «аудиовход замер» спустя до 100 с и отвечало перезапуском всей
        встречи. Один гвард на весь проход (первая правка) был хуже: устойчивый
        сбой блока одного канала обрывал проход до сторожа — мёртвый канал не
        детектировался никогда, очереди соседей росли, а `input_age_seconds`
        оставался нулевым, и приложение не перезапускало ничего (Critical DS
        выходного круга). Поэтому: дурной блок не съедает блоки соседей и не
        отменяет сторож. Сбой не тихий: репортёр (stderr + статус владельцу,
        окно по типу) и счётчик `pump_failures` в снапшоте здоровья."""
        failed = False
        for c in self.captures:
            try:
                part = c.q.get(timeout=0.15)
            except queue.Empty:
                continue
            try:
                self._consume(c, part)
            except Exception as e:  # noqa: BLE001 — потребитель обязан жить, пока _running
                failed = True
                self._report_pump_failure(e)
                # сбой обработки — видимая потеря живого звука, не тихая: кадр
                # пришёл (свежесть канала штампуется первой строкой _consume), но
                # до ленты не дошёл; иначе снаружи канал выглядел бы здоровым
                # (круг 2 DS I5)
                try:
                    self._note_drop(c.label, len(part) / float(self.sr), written=c.label in self._sinks)
                except Exception:  # noqa: BLE001 — отчёт о потере не важнее прохода
                    pass
        try:
            self._watch_streams()
        except Exception as e:  # noqa: BLE001 — сторож — вспомогательный контур, не цена записи
            failed = True
            self._report_pump_failure(e)
        # единица счётчика — ПРОХОД, как читает потребитель снапшота: два дурных
        # канала в одном проходе — один упавший проход, не два (круг 2 DS I2)
        self._pump_failures = self._pump_failures + 1 if failed else 0

    def _report_pump_failure(self, exc: BaseException) -> None:
        """Сбой единицы работы потребителя — не молча: полный текст в stderr и
        статус владельцу, не чаще GUARD_REPORT_S на тип исключения. Потерю
        каналов НЕ объявляем: каналы живы, сбой — у хаба, и ложное «канал
        потерян» в липкой строке было бы ложью (критика GLM входного круга).

        Репортёр — последний рубеж и обязан не бросать сам: `str(exc)` с битым
        `__str__` убил бы поток из except-блока (DS M1 / GLM I2 выходного
        круга) — всё тело под своим try, текст собирается защищённо."""
        try:
            key = type(exc).__name__
            now = time.monotonic()
            last = self._guard_said.get(key)
            if last is not None and now - last < self.GUARD_REPORT_S:
                return
            self._guard_said[key] = now
            try:
                text = " ".join(str(exc).split())[:300]
            except Exception:  # noqa: BLE001 — текст исключения недоступен, имя типа есть
                text = "<текст исключения недоступен>"
            # счётчик проходов растёт в конце прохода — этот сбой в него ещё не вошёл
            _safe_stderr(f"сбой аудиопотока ({key}: {text}), упавших проходов подряд {self._pump_failures + 1} — "
                         "поток жив, проход повторяется")
            self._say(f"⚠️ сбой аудиопотока: {key} — запись продолжается")
        except Exception:  # noqa: BLE001 — репортёр не роняет то, о чём докладывает
            pass

    def _consume(self, c, part, notify_frame: bool = True) -> None:
        """Один блок канала: файл записи, STT-буфер, триггер. Общий для _pump и
        дренажа очередей при stop()."""
        # под тем же локом, что и снапшот: новый ключ в словаре во
        # время его копирования — та же гонка, что и pop у _sinks
        with self._lock:
            self._last_frame[c.label] = time.time()
            # после _closing файлы закрываются — блок в них не пишем и не кричим
            # о «сбое диска»: sink для него уже «нет» (GLM r1 I1 по #557)
            sink = None if self._closing else self._sinks.get(c.label)
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
            _safe_stderr(msg)
            self._say(msg)
        if dropped:
            # Вне лока: статус уходит в UI через колбэк демона, и
            # держать на нём аудиопоток нельзя. Факт записи берём
            # ОТСЮДА, а не из `_sinks` позже: между этим местом и
            # отчётом стоп успевает обнулить словарь, и правдивое
            # «не вернуть» превращалось бы в ложное «будет полной»
            # (ревью 20.08, круг 3, DeepSeek).
            self._note_drop(c.label, dropped, written)
        if notify_frame and self.on_frame is not None:
            try:
                self.on_frame(c.label, part)
            except Exception:  # noqa: BLE001 — триггер не должен ронять захват
                pass

    def _restart_guarded(self, c):
        """Перезапустить канал, не подставив под удар конвейер.

        Возвращает None при успехе, исключение при отказе, TimeoutError если
        перезапуск не вернулся за RESTART_TIMEOUT. Отдельный поток нужен
        именно из-за последнего случая: `stop()` мёртвого PortAudio-стрима
        виснет, а зависание не ловится через try/except.
        """
        box: dict = {}
        restarting = self._restarting

        def run():
            try:
                c.restart()
                box["ok"] = True
            except Exception as e:  # noqa: BLE001 — доносим наружу как значение
                box["err"] = e
            finally:
                # метку «рестарт в полёте» снимает сам поток, когда вернулся из
                # close/open — сколько бы он ни висел: иначе канал, чей зависший
                # рестарт отлип и кадры пошли, оставался «в полёте» навсегда, и
                # stop() пропускал его хвост молча (Important DS круга 2 по №235)
                if restarting is not None:
                    restarting.discard(c.label)

        worker = threading.Thread(target=run, daemon=True, name=f"restart-{c.label}")
        if restarting is not None:
            restarting.add(c.label)
        try:
            worker.start()
        except RuntimeError as exc:           # потоки исчерпаны — тот же класс, что CPU-голодание 20.07
            if restarting is not None:
                restarting.discard(c.label)
            return exc                        # контракт функции — исход значением, не броском (GLM M4)
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
        # потери этого прохода: метка -> (почему, канал ещё пробуем). Объявляются
        # РАЗОМ после цикла — носитель считается по полному составу (C2 DS по №235)
        lost_now: dict[str, Loss] = {}
        try:
            self._sweep(now, lost_now)
        finally:
            # объявить накопленное даже если проход упал на соседнем канале:
            # иначе канал уже в _hung, в реестре его нет, и о потере не узнает
            # никто и никогда (Important DS выходного круга по №235)
            if lost_now:
                self._announce_losses(lost_now)

    def _sweep(self, now: float, lost_now: dict[str, Loss]) -> None:
        """Один проход сторожа по каналам: оживление по факту кадров, перезапуск
        молчащих, накопление потерь прохода в `lost_now`."""
        for c in self.captures:
            msg = None
            loss = self._lost.get(c.label)
            last = self._last_frame.get(c.label, 0.0)
            if loss is not None and last > loss.since:
                # кадр пришёл ПОСЛЕ постановки в реестр — канал пишет, каким бы
                # путём он ни ожил (зависший restart() отлип, устройство
                # вернулось): детектор — факт кадра, а не исход рестарта
                # (Important GLM выходного круга по №235). Без возраста
                # (канала нет в _last_frame) оживления нет — иначе метка
                # оживала бы мгновенно и ложно
                msg = self._announce_back(c.label, last - loss.since)
                self._emit(msg)
                continue
            silent = now - self._last_frame.get(c.label, now)
            if silent < 30:
                continue
            if self._busy(c.label):
                continue        # перезапуск этого канала завис или в полёте — не трогаем повторно
            if now - self._last_try.get(c.label, 0.0) < 30:
                continue        # анти-шторм: между попытками — пауза, но возраст честный
            self._last_try[c.label] = now
            outcome = self._restart_guarded(c)
            if outcome is None:
                msg = f"🎙 канал {c.label} молчал {int(silent)}с — аудио-стрим перезапущен"
                self._fail_streak.pop(c.label, None)
                if c.label not in self._lost:
                    # крика не было — дыра в записи была: ≥ порога тишины без
                    # единого события (Critical GLM входного круга по №234)
                    self._channel_event(CH_GAP, c.label, now, now - silent, silent,
                                        Loss(msg, retriable=True, died=True, cause="restarted"))
                if c.label in self._lost:
                    # Кричали о потере канала, а он ожил (приложение снова пишет
                    # поток, устройство освободилось): липкую строку снимаем
                    # явно — иначе «не будет» висит до конца встречи неправдой;
                    # следующая потеря кричит заново (№232). Снятие — только
                    # когда потерянных не осталось: слой один (№235)
                    msg = self._announce_back(c.label, silent)
            elif isinstance(outcome, TimeoutError):
                # Главный урок 06.08: закрытие мёртвого стрима не возвращается,
                # и вызов прямо из _pump останавливал конвейер целиком — вместе
                # с исправным микрофоном. Бросаем канал, встречу дописываем.
                self._hung.add(c.label)
                # Канал умер посреди встречи — тот же исход, что «не открылся на
                # старте» (№230), и та же была тихая строка: человек до конца
                # встречи не знал, что канала в записи больше нет. Кричим так же
                # громко (липкая строка, уведомление, capture.log); звук — один
                # на потерю канала. Для микрофона — та же дорога (№235): раньше
                # он получал «канал отключён — встреча пишется остальными», хотя
                # остальных могло и не быть. Крик запускает Popen без ожидания —
                # конвейер _pump это не задерживает (№232).
                lost_now[c.label] = self._loss_of(c, f"перезапуск завис после {int(silent)}с без кадров",
                                                  retriable=False)
                msg = None                    # причина уйдёт криком, не дважды (как в start)
            else:
                n = self._fail_streak[c.label] = self._fail_streak.get(c.label, 0) + 1
                msg = f"🎙 канал {c.label}: рестарт стрима не удался ({outcome}), попробую через 30с"
                # Основной путь macOS 15+ (ScreenCaptureKit, поток приложения
                # перестал расти): restart() возвращает обычную ошибку за ~3 с,
                # не TimeoutError, — и без этой ветки крик не звучал бы никогда
                # (Critical DS r1 по #541). Две неудачи подряд — минута без
                # канала, дальше ждать нечего. Канал не бросаем: поток может
                # ожить, и тогда ветка выше снимет строку
                if n >= 2 and c.label not in self._lost:
                    lost_now[c.label] = self._loss_of(c, f"рестарт не удался {n} раза подряд: {outcome}",
                                                      retriable=True)
                    msg = None
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
            self._emit(msg)

    def end_channel_episodes(self) -> None:
        """Открытые эпизоды закрываются остановкой записи: канал, потерянный и не
        вернувшийся, иначе остался бы без длительности, а итог встречи — без
        правой границы дыры (Important DS I3 / GLM I8 входного круга по №234).
        Идемпотентно: демон зовёт до спавна пересборки (итог должен лечь в хвост
        раньше неё), stop() — для остальных вызывающих."""
        now = time.time()
        for label, loss in list(self._lost.items()):
            if label in self._ended:
                continue
            self._ended.add(label)
            silent = now - loss.stopped_at if loss.stopped_at else None
            self._channel_event(CH_END, label, now, loss.stopped_at, silent,
                                Loss(loss.reason, retriable=False, died=loss.died, cause="stop"))

    def _channel_event(self, kind: str, label: str, at: float, stopped_at: float | None,
                       silent_s: float | None, loss: "Loss") -> ChannelEvent:
        """Единственная точка, где переход состояния канала становится событием:
        журнал хаба + подписчик. Все поводы (крик на старте, сторож, возврат по
        кадру, тихий перезапуск, остановка) проходят здесь — потребители следа
        не разбирают строки статуса (входной круг DS и GLM по №234). Отказ
        подписчика запись не роняет."""
        ev = ChannelEvent(label=label, kind=kind, at=at, stopped_at=stopped_at,
                          silent_s=silent_s, died=loss.died,
                          cause=loss.cause if loss.cause in CH_CAUSES else "hung",
                          reason=loss.reason)
        self.channel_log.append(ev)
        if self.on_channel is not None:
            try:
                self.on_channel(ev)
            except Exception:  # noqa: BLE001 — след встречи не должен ронять запись
                _safe_stderr(f"подписчик события канала упал: {kind} {label}")
        return ev

    def _emit(self, msg: str | None) -> None:
        if msg is not None and self.on_status is not None:
            try:
                self.on_status(msg)
            except Exception:  # noqa: BLE001
                pass

    def _busy(self, label: str) -> bool:
        """Канал нельзя трогать: перезапуск завис (`_hung`) или ещё в полёте
        (`_restarting`). Один предикат на сторож и stop() — раньше сторож
        смотрел одно множество, stop() — оба (I1 DS круга 2)."""
        return label in self._hung or label in self._restarting

    def _loss_of(self, c, why: str, *, retriable: bool) -> Loss:
        """Значение потери канала посреди встречи — формируется в момент
        потери, не в компоновщике (критика DS круга 2: признаки ехали
        позиционным кортежем). Причина собеседников — с хвостом capture.log
        (_system_loss); микрофона — исход рестарта и секунды тишины; хвост лога
        и микрофону, если он идёт тем же потоком ScreenCaptureKit
        (TapStreamCapture) и собеседники в этом проходе не потеряны — иначе
        один хвост склеился бы в строку дважды (M4 GLM круга 2)."""
        if c.label == "blackhole":
            return Loss(self._system_loss(died=why), retriable=retriable, died=True)
        reason = f"канал {c.label}: {why}" + ("; пробуем перезапустить каждые полминуты"
                                                if retriable else ", канал отключён")
        if isinstance(c, TapStreamCapture) and "blackhole" not in self._lost:
            tail = self._capture_log_tail()
            if tail:
                reason += f"; последняя строка capture.log: {tail}"
        return Loss(reason, retriable=retriable, died=True)

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
        pump = self._pump_thread
        return {
            # поток-потребитель жив и сколько его проходов подряд падают: до №311
            # приложение видело смерть помпы лишь как «аудиовход замер» спустя до
            # 100 с (input_age по min каналов) и перезапускало всю встречу; поле в
            # уже сериализуемом снапшоте даёт точный сигнал без нового наблюдателя
            "pump_alive": bool(pump is not None and pump.is_alive()),
            "pump_failures": self._pump_failures,
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
            chunk_no = self.chunk_no
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
            n = self.chunk_no.get(label)
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
