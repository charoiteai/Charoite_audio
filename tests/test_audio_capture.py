"""Захват аудио — единственная подсистема, где отказ невосстановим.

До этих тестов src/audio.py не импортировал ни один тест: не были покрыты
ни нарезка чанков с нахлёстом, ни подавление эха, ни финализация записи —
то есть весь путь, на котором теряется встреча. Устройство ввода для этого
не нужно: всё перечисленное — чистые функции над буфером.
"""
import pathlib
import queue
import sys
import time
import wave

import numpy as np

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import audio as a  # noqa: E402


def _hub(sr=16000, chunk_s=3.0, overlap_s=0.5, vad_db=-45.0):
    """AudioHub без устройств: конструктор трогает PortAudio, а нам нужна логика."""
    hub = object.__new__(a.AudioHub)
    hub.sr = sr
    hub.chunk_s = chunk_s
    hub.overlap_s = overlap_s
    hub.vad_db = vad_db
    hub.SPEAKER = a.AudioHub.SPEAKER
    hub._bufs = {}
    hub._sinks = {}
    hub._lock = __import__("threading").Lock()
    # Поля, которые в бою ставит конструктор: заглушка обязана их повторять,
    # иначе тест падает на AttributeError вместо проверки поведения.
    hub._hung = set()
    hub._last_frame = {}
    hub._last_check = 0.0
    hub._running = False
    hub.captures = []
    hub.sources = []
    hub.record_on = False
    hub.on_status = None
    hub.on_frame = None
    return hub


def _tone(n, amp=0.3):
    return (np.random.default_rng(0).standard_normal(n) * amp).astype(np.float32)


def test_cut_keeps_overlap_between_chunks():
    """Соседние чанки перекрываются: слово на стыке не должно пропасть."""
    hub = _hub()
    need = int(hub.sr * hub.chunk_s)
    keep = int(hub.sr * hub.overlap_s)
    hub._bufs["mic"] = _tone(need * 2)

    first = hub._cut("mic")

    assert first is not None and len(first) == need
    # в буфере остался хвост предыдущего чанка длиной keep
    assert len(hub._bufs["mic"]) == need * 2 - (need - keep)


def test_cut_waits_until_there_is_a_full_chunk():
    """Недобравший буфер не режем — иначе STT получает обрывки."""
    hub = _hub()
    hub._bufs["mic"] = _tone(int(hub.sr * hub.chunk_s) - 10)
    assert hub._cut("mic") is None


def test_speaker_echo_is_dropped_from_microphone():
    """Речь в обоих каналах одновременно = эхо динамиков, микрофон молчит.

    Без этого собственный голос собеседника попадал в стенограмму дважды —
    от него и «от владельца».
    """
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": _tone(n), "mic": _tone(n)}

    out = dict(hub.pull_labeled())

    assert hub.SPEAKER["blackhole"] in out, "речь собеседника потеряна"
    assert hub.SPEAKER["mic"] not in out, "эхо динамиков попало в микрофон"


def test_own_voice_survives_when_the_other_side_is_silent():
    """Обратная сторона: когда собеседник молчит, свой голос обязан пройти."""
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": np.zeros(n, dtype=np.float32), "mic": _tone(n)}

    out = dict(hub.pull_labeled())

    assert hub.SPEAKER["mic"] in out, "своя речь отброшена как эхо"


def test_finalize_drops_recordings_shorter_than_five_seconds(tmp_path):
    """Обрывок в пару секунд — не встреча, а мусор от случайного нажатия."""
    hub = _hub()
    pcm = tmp_path / "s_mic.pcm"
    pcm.write_bytes(b"\0" * (16000 * 2 * 2))     # 2 секунды
    hub._sinks = {"mic": pcm.open("ab")}

    hub._finalize_recordings()

    assert not pcm.exists()
    assert not (tmp_path / "s_mic.wav").exists()


def test_finalized_wav_is_playable(tmp_path):
    """Файл после финализации читается как WAV — иначе пересобирать нечего."""
    hub = _hub()
    pcm = tmp_path / "s_mic.pcm"
    seconds = 6
    pcm.write_bytes(b"\1\0" * (16000 * seconds))
    hub._sinks = {"mic": pcm.open("ab")}

    hub._finalize_recordings()

    with wave.open(str(tmp_path / "s_mic.wav"), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 16000
        assert w.getnframes() == 16000 * seconds


# ── Живучесть конвейера: 06.08 сторож увёл его в вечное ожидание ──────────

class _HangingCapture:
    """Канал, чей stop() виснет — так ведёт себя мёртвый PortAudio-стрим.

    Не выдумка: рядом в audio.py стоит комментарий «мёртвый PortAudio-стрим
    виснет на close», написанный по инциденту 20.07. Здесь он воспроизведён.
    """

    label = "blackhole"

    def __init__(self, hang_seconds=30.0):
        self.hang = hang_seconds
        self.restarts = 0

    def restart(self):
        self.restarts += 1
        time.sleep(self.hang)      # закрытие мёртвого стрима не возвращается

    def start(self):
        pass

    def stop(self):
        pass


def test_зависший_перезапуск_не_останавливает_конвейер():
    """06.08: четыре записи подряд оборвались на 31-й секунде.

    Механика: канал системного звука не отдавал кадров, на тридцатой секунде
    сторож пошёл его перезапускать и застрял на закрытии мёртвого стрима.
    Вызов идёт прямо из _pump, поэтому встал весь конвейер — вместе с
    микрофоном, который был полностью исправен. Попытки try/except от этого
    не спасают: зависание не исключение.

    Тест держит сторожа в рамках времени: он обязан вернуться, даже если
    перезапуск канала не возвращается никогда.
    """
    hub = _hub()
    hub.RESTART_TIMEOUT = 0.3      # в бою пять секунд; тесту столько ждать незачем
    dead = _HangingCapture(hang_seconds=5.0)
    hub.captures = [dead]
    hub._last_frame = {"blackhole": time.time() - 60}   # молчит минуту
    hub._last_check = 0.0
    hub.on_status = lambda _msg: None

    started = time.time()
    hub._watch_streams()
    spent = time.time() - started

    assert spent < 2, (
        f"сторож не вернулся за {spent:.0f}с — конвейер встал вместе с ним, "
        "и микрофон перестал писать, хотя был исправен")


def test_мёртвый_канал_не_уносит_соседей_при_старте():
    """Отказ одного источника не должен лишать встречу остальных.

    В AudioHub.start() цикл открывает каналы без try: исключение на первом
    оставляет встречу вообще без записи, включая исправный микрофон.
    """
    class _Failing:
        label = "blackhole"
        def __init__(self): self.q = queue.Queue()
        def start(self): raise RuntimeError("устройство не приняло конфигурацию")
        def stop(self): pass

    class _Working:
        label = "mic"
        def __init__(self):
            self.started = False
            self.q = queue.Queue()
        def start(self): self.started = True
        def stop(self): pass

    hub = _hub()
    mic = _Working()
    hub.captures = [_Failing(), mic]
    hub._bufs = {"blackhole": np.zeros(0, dtype=np.float32), "mic": np.zeros(0, dtype=np.float32)}
    hub.record_on = False
    hub.on_status = lambda _msg: None

    hub.start()
    try:
        assert mic.started, "исправный микрофон не открыли из-за отказа соседнего канала"
    finally:
        hub._running = False
