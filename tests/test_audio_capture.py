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
    hub._drops = {}
    hub._lock = __import__("threading").Lock()
    # Поля, которые в бою ставит конструктор: заглушка обязана их повторять,
    # иначе тест падает на AttributeError вместо проверки поведения.
    hub._hung = set()
    hub._last_frame = {}
    hub._last_try = {}
    hub._last_check = 0.0
    hub._running = False
    hub.captures = []
    hub._sys_speech_until = 0.0
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


def test_эхо_глушится_когда_системный_срез_запаздывает():
    """Фазы нарезки разъехались (перезапуск канала сторожем сбрасывает
    буфер): микрофонный чанк с эхом готов, системный ещё копится. Раньше
    both его не ловил — та же фраза уходила в стенограмму дважды, вторым
    экземпляром «от владельца»."""
    import time as _t
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": np.zeros(0, dtype=np.float32), "mic": _tone(n)}
    hub._sys_speech_until = _t.monotonic() + 1.0  # динамики звучали чанк назад

    out = dict(hub.pull_labeled())

    assert hub.SPEAKER["mic"] not in out, "эхо-дубль прошёл в стенограмму"


def test_ответ_сразу_после_собеседника_не_глушится():
    """Собеседник замолчал, владелец тут же отвечает: системный чанк готов и
    тихий — значит это живой ответ, а не эхо, окно молчать не заставляет."""
    import time as _t
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": np.zeros(n, dtype=np.float32), "mic": _tone(n)}
    hub._sys_speech_until = _t.monotonic() + 1.0  # окно ещё активно

    out = dict(hub.pull_labeled())

    assert hub.SPEAKER["mic"] in out, "живой ответ заглушён как эхо"


def test_речь_динамиков_взводит_окно_эха():
    """Речь в системном канале продлевает окно на длину чанка вперёд."""
    import time as _t
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": _tone(n), "mic": np.zeros(0, dtype=np.float32)}

    before = _t.monotonic()
    hub.pull_labeled()

    assert hub._sys_speech_until >= before + hub.chunk_s * 0.99


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


def test_мёртвый_канал_не_уносит_соседей_при_старте(monkeypatch):
    """Отказ одного источника не должен лишать встречу остальных.

    В AudioHub.start() цикл открывает каналы без try: исключение на первом
    оставляет встречу вообще без записи, включая исправный микрофон.
    """
    class _Failing:
        label = "blackhole"
        opened_as = None
        def __init__(self): self.q = queue.Queue()
        def start(self): raise RuntimeError("устройство не приняло конфигурацию")
        def stop(self): pass

    class _Working:
        label = "mic"
        opened_as = None          # как у настоящего Capture: ступень лестницы
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

    # start() не хранит handle фонового _pump. Перехватываем только создание
    # потока в этом тесте, чтобы он гарантированно завершился здесь, а не в
    # следующем тесте: именно поздний выход раньше маскировал неполный _hub.
    real_thread = a.threading.Thread
    pump_threads = []

    def tracked_thread(*args, **kwargs):
        worker = real_thread(*args, **kwargs)
        pump_threads.append(worker)
        return worker

    monkeypatch.setattr(a.threading, "Thread", tracked_thread)

    hub.start()
    try:
        assert mic.started, "исправный микрофон не открыли из-за отказа соседнего канала"
    finally:
        hub._running = False
        # Без pump_threads[0] до проверки длины: при падении start() ДО
        # создания потока IndexError маскировал бы настоящую ошибку;
        # join 3 c — как у соседних тестов файла (круг по #420, DS).
        for worker in pump_threads:
            worker.join(timeout=3.0)
    assert len(pump_threads) == 1, "start() не поднял единственный pump-поток"
    assert not pump_threads[0].is_alive(), "pump-поток пережил границу теста"


def test_blackhole_выигрывает_у_тапа(monkeypatch):
    """Есть оба — берём BlackHole: итог боевого теста 06.08.

    Тап открывается штатно, но демону не отдаёт ни кадра (0 байт за 94
    секунды записи). Пока причина не найдена, проверенный драйвер важнее
    красивой нативности: перевернуть приоритет обратно можно только вместе
    с разбором, почему тап молчит.
    """
    devices = {a.TAP_DEVICE: 7, "BlackHole 2ch": 3}
    monkeypatch.setattr(a, "find_device",
                        lambda s: next((i for n, i in devices.items() if s.lower() in n.lower()), None))
    assert a.find_system_audio() == (3, "blackhole")


def test_без_blackhole_берём_тап(monkeypatch):
    """Драйвера нет — тап остаётся единственным источником второй стороны."""
    monkeypatch.setattr(a, "find_device", lambda s: 7 if s == a.TAP_DEVICE else None)
    assert a.find_system_audio() == (7, "tap")


def test_без_тапа_откатываемся_на_blackhole(monkeypatch):
    """Старая macOS, отказ в разрешении, приложение не запущено — тапа нет.

    Молча остаться без канала собеседников нельзя: в стенограмме пропадёт
    вторая сторона разговора, а узнаем мы об этом уже после встречи.
    """
    monkeypatch.setattr(a, "find_device",
                        lambda s: 3 if "blackhole" in s.lower() else None)
    assert a.find_system_audio() == (3, "blackhole")


def test_нет_ни_тапа_ни_драйвера(monkeypatch):
    """Оба источника отсутствуют — честный None, а не случайное устройство."""
    monkeypatch.setattr(a, "find_device", lambda s: None)
    index, via = a.find_system_audio()
    assert index is None and via == "blackhole"


# --- Открытие потока: лестница конфигураций и ресемплер -----------------------
#
# Тап отвечал PaMacCore AUHAL err=-10851 и не открывался вовсе — отсюда ноль
# байт в записи. Проверить это на железе нельзя: рабочая машина занята
# встречами, и ровно такие эксперименты её и подвесили. Поэтому весь узел
# покрыт без единого обращения к устройству — sd.InputStream подменяется.


class _FakeStream:
    """Подмена sd.InputStream: запоминает, с чем её открыли."""

    def __init__(self, **kw):
        self.kw = kw
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        pass


def _ladder_probe(monkeypatch, fail_first: int, native_sr=48000):
    """Отказывать −10851 на первых fail_first попытках открыть поток.

    Возвращает (попытки открыть, опросы устройства) — второе тоже под счётом:
    опрашивать PortAudio на здоровом канале незачем.
    """
    attempts: list[dict] = []
    queries: list = []

    def factory(**kw):
        attempts.append(kw)
        if len(attempts) <= fail_first:
            raise a.sd.PortAudioError(
                "Error opening InputStream: Invalid Property Value "
                "[PaMacCore ( AUHAL )| Error on line 2523: err='-10851']")
        return _FakeStream(**kw)

    def query(*args, **kw):
        queries.append(args)
        return {"default_samplerate": float(native_sr)}

    monkeypatch.setattr(a.sd, "InputStream", factory)
    monkeypatch.setattr(a.sd, "query_devices", query)
    return attempts, queries


def test_обычное_устройство_открывается_первой_же_ступенью(monkeypatch):
    """Регрессия: BlackHole и микрофон обязаны видеть ровно прежний вызов.

    Они везут встречи прямо сейчас. Лестница не имеет права ни поменять им
    параметры потока, ни подсунуть ресемплер.
    """
    attempts, queries = _ladder_probe(monkeypatch, fail_first=0)
    c = a.Capture(3, 16000, "blackhole")

    c.start()

    assert len(attempts) == 1, "устройство трогали больше одного раза"
    assert queries == [], "лишний опрос PortAudio на здоровом канале"
    assert attempts[0]["samplerate"] == 16000
    assert attempts[0]["blocksize"] == 4000      # int(16000 * 0.25) — как было всегда
    assert attempts[0]["channels"] == 1
    assert c.opened_as == a.Capture.PLAIN
    assert c._resampler is None, "обычному устройству ресемплер не нужен"


def test_отказ_по_размеру_блока_чинится_второй_ступенью(monkeypatch):
    """Если −10851 давал размер блока — открываемся на своей частоте.

    Ресемплер при этом не поднимается: частота запрошена наша, пересчёт
    делает сам PortAudio.
    """
    attempts, _ = _ladder_probe(monkeypatch, fail_first=1)
    c = a.Capture(7, 16000, "blackhole")

    c.start()

    assert len(attempts) == 2
    assert attempts[1]["samplerate"] == 16000
    assert attempts[1]["blocksize"] == 0, "размер блока должен отдаваться PortAudio"
    assert c.opened_as == "свободный размер блока"
    assert c._resampler is None


def test_отказ_по_частоте_чинится_третьей_ступенью(monkeypatch):
    """Если −10851 давала частота — открываемся на родной и понижаем сами."""
    attempts, _ = _ladder_probe(monkeypatch, fail_first=2, native_sr=48000)
    c = a.Capture(7, 16000, "blackhole")

    c.start()

    assert len(attempts) == 3
    assert attempts[2]["samplerate"] == 48000
    assert attempts[2]["blocksize"] == 0
    assert c.opened_as == "частота устройства 48000 Гц"
    assert isinstance(c._resampler, a._Downsampler)


def test_очередь_всегда_в_целевой_частоте(monkeypatch):
    """Наружу Capture обязан отдавать 16 кГц, чем бы ни было открыто устройство.

    Иначе _cut нарежет чанки втрое короче, чем думает, и STT получит обрывки.
    """
    _ladder_probe(monkeypatch, fail_first=2, native_sr=48000)  # noqa: F841
    c = a.Capture(7, 16000, "blackhole")
    c.start()

    c._cb(np.zeros((4800, 1), dtype=np.float32), 4800, None, None)

    got = c.q.get_nowait()
    assert got.dtype == np.float32
    assert abs(len(got) - 1600) <= 1, f"48 кГц не превратились в 16 кГц: {len(got)}"


def test_отказ_всех_ступеней_объясняет_причину(monkeypatch):
    """Раньше улетал голый PortAudioError. Теперь видно, что именно пробовали."""
    _ladder_probe(monkeypatch, fail_first=99)
    c = a.Capture(7, 16000, "blackhole")

    try:
        c.start()
    except RuntimeError as e:
        text = str(e)
    else:
        raise AssertionError("отказ устройства прошёл незамеченным")

    assert "blackhole" in text
    assert a.Capture.PLAIN in text and "свободный размер блока" in text
    assert "-10851" in text, "код ошибки устройства обязан дойти до человека"


def test_ресемплер_не_склеивает_блоки_со_щелчком():
    """Поблочная обработка обязана совпасть с обработкой одним куском.

    Состояние (хвост фильтра, дробная позиция) переносится через шов; ошибка
    здесь дала бы щелчок каждые 250 мс — на слух почти незаметный, для
    распознавания разрушительный.
    """
    rng = np.random.default_rng(0)
    signal = (rng.standard_normal(4000 * 5) * 0.3).astype(np.float32)

    one_shot = a._Downsampler(48000, 16000).process(signal)
    blocked = a._Downsampler(48000, 16000)
    joined = np.concatenate([blocked.process(signal[i:i + 4000])
                             for i in range(0, len(signal), 4000)])

    assert len(joined) == len(one_shot)
    assert np.allclose(joined, one_shot, atol=1e-6)


def test_ресемплер_держит_среднюю_длину():
    """Длина выхода плавает по блокам, но не копит сдвиг."""
    d = a._Downsampler(48000, 16000)
    lengths = [len(d.process(np.zeros(4000, dtype=np.float32))) for _ in range(30)]

    assert set(lengths) <= {1333, 1334}, f"неожиданные длины: {sorted(set(lengths))}"
    assert abs(sum(lengths) - 30 * 4000 / 3) <= 1


def test_ресемплер_не_заворачивает_высокие_частоты():
    """12 кГц при 48→16 без фильтра сели бы на 4 кГц — прямо в речевую полосу."""
    sr = 48000
    t = np.arange(sr) / sr
    tone = np.sin(2 * np.pi * 12000 * t).astype(np.float32)

    out = a._Downsampler(sr, 16000).process(tone)
    naive = tone[::3]                       # то же самое без фильтра — для контраста

    assert np.sqrt(np.mean(naive**2)) > 0.5, "контроль: без фильтра тон остаётся"
    assert np.sqrt(np.mean(out[800:]**2)) < 0.01, "12 кГц завернулись в речевую полосу"


def test_ресемплер_пропускает_речевую_полосу():
    """Обратная проверка: 1 кГц обязан пройти, а не быть срезан заодно с шумом."""
    sr = 48000
    t = np.arange(sr) / sr
    tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32)

    out = a._Downsampler(sr, 16000).process(tone)

    assert 0.6 < np.sqrt(np.mean(out[800:]**2)) < 0.75, "речевая полоса просела"


# ── Поток тапа от приложения: демон читает файл, а не устройство ──────────

def _manifest(tmp_path, sr=48000):
    import json
    raw = tmp_path / "tap_stream.raw"
    raw.write_bytes(b"")
    m = {"path": str(raw), "samplerate": sr, "format": "s16le", "channels": 1}
    (tmp_path / "tap_stream.json").write_text(json.dumps(m), encoding="utf-8")
    return m, raw


def test_поток_тапа_читается_и_даунсемплится(tmp_path):
    """Приложение пишет s16le 48 кГц, конвейер получает float32 16 кГц.

    Право на системный звук есть только у приложения (вердикт 06–07.08),
    поэтому демон берёт кадры из растущего файла — и они обязаны прийти
    в той же форме, что и из PortAudio."""
    m, raw = _manifest(tmp_path)
    cap = a.TapStreamCapture(m, 16000, "blackhole")
    t = np.arange(48000, dtype=np.float32) / 48000
    tone = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    def writer():
        # как приложение: кадры капают маленькими порциями непрерывно
        with raw.open("ab") as f:
            for i in range(0, len(tone), 4800):
                f.write(tone[i:i + 4800].tobytes())
                f.flush()
                time.sleep(0.05)
    import threading as _t
    w = _t.Thread(target=writer, daemon=True)
    w.start()
    cap.start()
    try:
        w.join(timeout=5)
        got = []
        deadline = time.time() + 5
        while sum(len(g) for g in got) < 12000 and time.time() < deadline:
            try:
                got.append(cap.q.get(timeout=0.5))
            except queue.Empty:
                pass
    finally:
        cap.stop()
    out = np.concatenate(got) if got else np.zeros(0)
    assert len(out) >= 12000, "секунда исходника не превратилась в кадры 16 кГц"
    assert 0.2 < float(np.abs(out).max()) <= 1.0, "амплитуда тона потерялась"


def test_поток_тапа_без_роста_падает_вслух(tmp_path):
    """Файл есть, но не растёт: канал обязан отказаться, а не писать тишину."""
    m, _raw = _manifest(tmp_path)
    cap = a.TapStreamCapture(m, 16000, "blackhole")
    try:
        cap.start()
    except RuntimeError as e:
        assert "не растёт" in str(e)
    else:
        cap.stop()
        raise AssertionError("старт без кадров обязан падать")


def test_свежесть_манифеста(tmp_path, monkeypatch):
    """Манифест мёртвой встречи (файл давно не рос) не выбирается."""
    m, raw = _manifest(tmp_path)
    monkeypatch.setattr(a, "TAP_STREAM_MANIFEST", tmp_path / "tap_stream.json")
    raw.write_bytes(b"\0\0" * 100)
    assert a.fresh_tap_manifest() is not None, "живой манифест не распознан"
    old = time.time() - 60
    import os
    os.utime(raw, (old, old))
    assert a.fresh_tap_manifest() is None, "труп прошлой встречи прошёл за живого"


def test_нечётное_чтение_не_убивает_читателя(tmp_path):
    """Чтение застаёт запись посередине сэмпла: нечётный хвост переносится,
    а не роняет нить ValueError-ом — иначе канал глохнет молча."""
    m, raw = _manifest(tmp_path, sr=16000)
    cap = a.TapStreamCapture(m, 16000, "blackhole")
    tone = (np.ones(1600, dtype=np.float32) * 0.25 * 32767).astype("<i2").tobytes()

    def writer():
        time.sleep(0.25)                    # строго после старта читателя:
        with raw.open("ab") as f:           # первый старт читает только новое
            half = len(tone) // 2
            f.write(tone[:half + 1])        # нечётная граница — пол-сэмпла
            f.flush()
            time.sleep(0.15)
            f.write(tone[half + 1:])        # дозапись второй половины
            f.flush()
    import threading as _t
    w = _t.Thread(target=writer, daemon=True)
    w.start()
    # старт ждёт роста файла — писатель обеспечит его через четверть секунды
    cap.start()
    try:
        w.join(timeout=3)
        got = []
        deadline = time.time() + 3
        while sum(len(g) for g in got) < 1600 and time.time() < deadline:
            try:
                got.append(cap.q.get(timeout=0.3))
            except queue.Empty:
                pass
    finally:
        cap.stop()
    total = int(sum(len(g) for g in got))
    assert total == 1600, f"из 1600 сэмплов дошло {total} — нечётная граница съела данные"


def test_рестарт_продолжает_с_места_а_не_с_конца(tmp_path):
    """Сторож перезапустил канал — накопленное в файле читается, а не
    выбрасывается прыжком в конец: там могло лежать до 30 с встречи."""
    m, raw = _manifest(tmp_path, sr=16000)
    cap = a.TapStreamCapture(m, 16000, "blackhole")
    first = (np.ones(800, dtype=np.float32) * 0.2 * 32767).astype("<i2").tobytes()
    with raw.open("ab") as f:
        f.write(first)
    # первый старт: позиции ещё нет — читатель встаёт в конец и ждёт нового
    import threading as _t
    def writer(payload):
        def run():
            time.sleep(0.2)
            with raw.open("ab") as f:
                f.write(payload)
        w = _t.Thread(target=run, daemon=True)
        w.start()
        return w
    w = writer(first)
    cap.start()
    w.join(timeout=3)
    time.sleep(0.3)
    cap.stop()
    while not cap.q.empty():
        cap.q.get()
    # пока канал «висел», в файл приехало ещё 800 сэмплов
    with raw.open("ab") as f:
        f.write(first)
    w = writer(first)                       # и после рестарта пишется дальше
    cap.start()
    try:
        w.join(timeout=3)
        got = []
        deadline = time.time() + 3
        while sum(len(g) for g in got) < 1600 and time.time() < deadline:
            try:
                got.append(cap.q.get(timeout=0.3))
            except queue.Empty:
                pass
    finally:
        cap.stop()
    total = int(sum(len(g) for g in got))
    assert total >= 1600, (
        f"после рестарта дошло {total} из 1600 — накопленное выброшено прыжком в конец")


def test_частота_микрофона_берётся_своя_а_не_системная(tmp_path):
    """ScreenCaptureKit применяет запрошенную частоту только к системному
    звуку: микрофон приходит в родном формате устройства. Живой тест 07.08
    показал файл микрофона втрое больше системного — спутав частоты, демон
    растянул бы голос втрое."""
    sysraw, micraw = tmp_path / "s.raw", tmp_path / "m.raw"
    sysraw.write_bytes(b"\0\0" * 10)
    micraw.write_bytes(b"\0\0" * 10)
    m = {"engine": "screencapturekit", "format": "s16le",
         "samplerate": 16000,
         "system": str(sysraw), "system_rate": 16000,
         "mic": str(micraw), "mic_rate": 48000}

    system = a.TapStreamCapture(m, 16000, "blackhole", key="system")
    mic = a.TapStreamCapture(m, 16000, "mic", key="mic")

    assert "16000 Гц" in system.opened_as, system.opened_as
    assert "48000 Гц" in mic.opened_as, mic.opened_as


def test_манифест_screencapturekit_живой_только_с_растущим_потоком(tmp_path, monkeypatch):
    """Манифест без растущих файлов — труп прошлой встречи, брать нельзя."""
    import json, os
    sysraw = tmp_path / "s.raw"
    sysraw.write_bytes(b"\0\0" * 100)
    man = tmp_path / "sck.json"
    man.write_text(json.dumps({"engine": "screencapturekit", "samplerate": 16000,
                               "format": "s16le", "system": str(sysraw)}))
    monkeypatch.setattr(a, "SCK_STREAM_MANIFEST", man)
    assert a.fresh_sck_manifest() is not None, "живой манифест не распознан"
    old = time.time() - 60
    os.utime(sysraw, (old, old))
    assert a.fresh_sck_manifest() is None, "труп прошлой встречи прошёл за живого"


class _FailingCapture:
    """Канал, чей restart() падает — выдернутое устройство: PortAudio бросает."""

    label = "blackhole"

    def __init__(self):
        self.restarts = 0

    def restart(self):
        self.restarts += 1
        raise RuntimeError("device unplugged")


class _RevivableCapture:
    """Канал, который перезапускается успешно."""

    label = "blackhole"

    def __init__(self):
        self.restarts = 0

    def restart(self):
        self.restarts += 1


def test_неудачный_рестарт_не_омолаживает_возраст_и_гейтится_антищтормом():
    """Круг 3, GLM: главную правку — «возраст сбрасывается только при удачном
    рестарте» — не держал ни один тест. Мутация «вернуть безусловный сброс»
    делала третий контур watchdog слепым при зелёном прогоне.

    Возраст мёртвого канала растёт монотонно: никакие ПОПЫТКИ рестарта его
    не трогают (инвариант И-2). Повторная попытка — не раньше, чем через
    30с (_last_try), но и не позже: канал не бросается навсегда.
    """
    hub = _hub()
    hub.RESTART_TIMEOUT = 1.0
    dead = _FailingCapture()
    hub.captures = [dead]
    died_at = time.time() - 60
    hub._last_frame = {"blackhole": died_at}
    hub._last_check = 0.0
    hub.on_status = lambda _msg: None

    hub._watch_streams()
    assert dead.restarts == 1
    assert hub._last_frame["blackhole"] == died_at, (
        "неудачный рестарт омолодил возраст — третий контур watchdog ослеп")

    # немедленный повтор гейтится анти-штормом, возраст всё ещё честный
    hub._last_check = 0.0
    hub._watch_streams()
    assert dead.restarts == 1, "анти-шторм не сработал — рестарты каждые 5с"

    # состарился гейт попыток — попытка повторяется (канал не брошен)
    hub._last_try["blackhole"] = time.time() - 31
    hub._last_check = 0.0
    hub._watch_streams()
    assert dead.restarts == 2, "канал брошен навсегда — повторной попытки нет"
    assert hub._last_frame["blackhole"] == died_at


def test_удачный_рестарт_сбрасывает_возраст():
    """Обратная сторона: после успешного перезапуска канал не должен тут же
    считаться молчащим — возраст обнуляется до прихода первых кадров."""
    hub = _hub()
    hub.RESTART_TIMEOUT = 1.0
    cap = _RevivableCapture()
    hub.captures = [cap]
    hub._last_frame = {"blackhole": time.time() - 60}
    hub._last_check = 0.0
    hub.on_status = lambda _msg: None

    hub._watch_streams()

    assert cap.restarts == 1
    assert time.time() - hub._last_frame["blackhole"] < 5, (
        "возраст не сброшен после удачного рестарта — немедленный повторный цикл")


def test_анти_шторм_отпускает_ровно_на_тридцатой_секунде(monkeypatch):
    """Граница `< 30`: ровно через 30с после попытки канал перезапускается,
    а не молчит ещё один такт. Мутация Lt→LtE выживала — прежний тест
    использовал −31 (мутационный прогон 21.08)."""
    import audio as a
    hub = _hub()
    hub.RESTART_TIMEOUT = 1.0
    dead = _FailingCapture()
    hub.captures = [dead]
    clock = [1000.0]
    # `audio.time` — это сам stdlib-модуль: патч через фикстуру, чтобы
    # соседние тесты не получили 1000.0, если этот упадёт (ревью DeepSeek)
    monkeypatch.setattr(a.time, "time", lambda: clock[0])
    hub._last_frame = {"blackhole": 900.0}       # молчит 100с
    hub._last_try = {"blackhole": 970.0}         # попытка 30с назад — ровно
    hub._last_check = 0.0
    hub.on_status = lambda _msg: None
    hub._watch_streams()
    assert dead.restarts == 1, "ровно 30с — попытка обязана повториться"
    hub._last_try["blackhole"] = 1000.0 - 29.9
    hub._last_check = 0.0
    hub._watch_streams()
    assert dead.restarts == 1, "29.9с — ещё анти-шторм"
