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
    hub._mic_only_warned = False
    hub._system_dead = False
    hub._fail_streak = {}
    hub._scream_count = 0
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


def test_мёртвый_канал_не_уносит_соседей_при_старте(monkeypatch, tmp_path):
    """Отказ одного источника не должен лишать встречу остальных.

    В AudioHub.start() цикл открывает каналы без try: исключение на первом
    оставляет встречу вообще без записи, включая исправный микрофон.
    Отказ канала собеседников с №230 ещё и кричит — уведомление и capture.log
    здесь подменены, чтобы тест не стучался в боевой лог и центр уведомлений.
    """
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: None)
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


def test_устройство_собеседников_это_blackhole(monkeypatch):
    """Приложение не подняло ScreenCaptureKit — второй стороной остаётся
    драйвер BlackHole. Устройство «Charoite System Audio» (Core Audio tap,
    снят 02.09) больше не ищется, даже если сирота с таким именем ещё висит
    в системе: читать его демон всё равно не мог."""
    devices = {"Charoite System Audio": 7, "BlackHole 2ch": 3}
    monkeypatch.setattr(a, "find_device",
                        lambda s: next((i for n, i in devices.items() if s.lower() in n.lower()), None))
    assert a.find_system_audio() == 3


def test_нет_драйвера_честный_none(monkeypatch):
    """Источника нет — честный None, а не случайное устройство.

    Молча остаться без канала собеседников нельзя: в стенограмме пропадёт
    вторая сторона разговора, а узнаем мы об этом уже после встречи.
    """
    monkeypatch.setattr(a, "find_device", lambda s: None)
    assert a.find_system_audio() is None


# --- Открытие потока: лестница конфигураций и ресемплер -----------------------
#
# Устройство на 48 кГц (агрегат Core Audio tap, 06.08; любой интерфейс с
# фиксированной частотой) отвечало PaMacCore AUHAL err=-10851 и не
# открывалось вовсе — отсюда ноль байт в записи. Проверить это на железе
# нельзя: рабочая машина занята встречами, и ровно такие эксперименты её и
# подвесили. Поэтому весь узел покрыт без единого обращения к устройству —
# sd.InputStream подменяется.


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


# ── Поток приложения (ScreenCaptureKit): демон читает файл, а не устройство ──

def _manifest(tmp_path, sr=48000):
    """Манифест системного потока в форме, которую пишет SystemAudioCapture."""
    import json
    raw = tmp_path / "system.raw"
    raw.write_bytes(b"")
    m = {"engine": "screencapturekit", "system": str(raw), "system_rate": sr,
         "samplerate": sr, "format": "s16le", "channels": 1}
    (tmp_path / "sck_stream.json").write_text(json.dumps(m), encoding="utf-8")
    return m, raw


def test_поток_приложения_читается_и_даунсемплится(tmp_path):
    """Приложение пишет s16le 48 кГц, конвейер получает float32 16 кГц.

    Право на системный звук есть только у приложения (вердикт 06–07.08),
    поэтому демон берёт кадры из растущего файла — и они обязаны прийти
    в той же форме, что и из PortAudio."""
    m, raw = _manifest(tmp_path)
    cap = a.TapStreamCapture(m, 16000, "blackhole", key="system")
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


def test_поток_приложения_без_роста_падает_вслух(tmp_path):
    """Файл есть, но не растёт: канал обязан отказаться, а не писать тишину."""
    m, _raw = _manifest(tmp_path)
    cap = a.TapStreamCapture(m, 16000, "blackhole", key="system")
    try:
        cap.start()
    except RuntimeError as e:
        assert "не растёт" in str(e)
    else:
        cap.stop()
        raise AssertionError("старт без кадров обязан падать")


def test_нечётное_чтение_не_убивает_читателя(tmp_path):
    """Чтение застаёт запись посередине сэмпла: нечётный хвост переносится,
    а не роняет нить ValueError-ом — иначе канал глохнет молча."""
    m, raw = _manifest(tmp_path, sr=16000)
    cap = a.TapStreamCapture(m, 16000, "blackhole", key="system")
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
    cap = a.TapStreamCapture(m, 16000, "blackhole", key="system")
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


def test_every_physical_chunk_consumes_a_number_even_silent_or_echo():
    """Шов стенограммы считает соседями только n и n-1 одного канала: тихий
    чанк и mic-чанк, отброшенный как эхо, номер потребляют — иначе два
    речевых чанка через паузу стали бы «соседями» (luna, круг-2 #452; DS #453)."""
    hub = _hub()
    n = int(hub.sr * hub.chunk_s) + 100
    hub._bufs = {"blackhole": _tone(n), "mic": _tone(n)}          # оба звучат: mic — эхо, отброшен
    out = dict(hub.pull_labeled())
    assert hub.SPEAKER["mic"] not in out
    assert hub.chunk_seq(hub.SPEAKER["mic"]) == ("mic", 0), "эхо-чанк mic номер потребил"
    assert hub.chunk_seq(hub.SPEAKER["blackhole"]) == ("blackhole", 0)
    hub._bufs = {"blackhole": np.zeros(n, dtype=np.float32), "mic": np.zeros(n, dtype=np.float32)}   # тишина
    assert hub.pull_labeled() == []
    assert hub.chunk_seq(hub.SPEAKER["mic"]) == ("mic", 1)
    hub._bufs = {"blackhole": np.zeros(n, dtype=np.float32), "mic": _tone(n)}
    assert hub.SPEAKER["mic"] in dict(hub.pull_labeled())
    assert hub.chunk_seq(hub.SPEAKER["mic"]) == ("mic", 2), "речевой после тишины — не сосед первого"
    assert hub.chunk_seq("нет такого") is None



def test_missing_system_channel_screams_and_names_the_reason(tmp_path, monkeypatch):
    """Запись без канала собеседников обязана быть ГРОМКОЙ, с причиной.

    До 10.09 об этом сообщала только строка статуса «Слушаю: Микрофон
    (fallback)» рядом с названием модели — на встрече такое не замечают, а
    узнаю́т через час по пустой стенограмме. С удалением BlackHole (№137)
    запасного пути не осталось вовсе: если ScreenCaptureKit не поднялся,
    вторая сторона разговора не запишется, и предупредить надо СРАЗУ.
    """
    said = []
    hub = object.__new__(a.AudioHub)
    hub.captures = [type("_Mic", (), {"label": "mic"})()]   # как в бою при auto: микрофон открыт (текст — по составу, r2 #541)
    hub.on_status = said.append
    monkeypatch.setattr(a, "ROOT", tmp_path)          # свой logs/, боевой не трогаем
    calls = []
    # Popen, а не run: уведомление пускается без ожидания, чтобы залипший
    # osascript не отодвигал открытие каналов (круг 1, 10.09).
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: calls.append((args, kw)))

    hub._warn_no_system_channel(sck_missing=True, bh_missing=True)

    assert len(said) == 1, "предупреждение должно быть ровно одно"
    msg = said[0]
    assert "СОБЕСЕДНИКОВ" in msg, "текст обязан говорить, ЧТО потеряно, а не «fallback»"
    assert "ScreenCaptureKit" in msg and "право" in msg, "нужна причина и что проверить"
    assert calls, "уведомление macOS не отправлено — строку статуса не заметят"
    # не только факт вызова: звук и текст баннера — половина «громкости»,
    # их потеря проходила зелёной (Minor DS r2 по #531)
    cmd = calls[0][0][0]
    assert cmd[0] == "osascript" and 'sound name "Glass"' in cmd[-1] and "без собеседников" in cmd[-1], cmd
    assert "Запись экрана" in cmd[-1], "без канала вовсе совет — право TCC"
    assert calls[0][1].get("start_new_session") is True, "как у остальных fire-and-forget Popen"

    log = (tmp_path / "logs" / "capture.log").read_text(encoding="utf-8")
    assert "ЗАПИСЬ БЕЗ СИСТЕМНОГО ЗВУКА" in log, "причина обязана осесть в capture.log"


def test_предупреждение_переживает_недоступный_лог_и_упавший_notifier(tmp_path, monkeypatch):
    """Предупреждение важнее своих же побочных действий: недоступный лог и
    упавший osascript не должны ронять старт записи.

    «logs» здесь — ФАЙЛ, а не каталог: чтение хвоста даёт NotADirectoryError,
    запись — FileExistsError на mkdir(exist_ok=True); оба — OSError, и обе
    ветки except исполняются по-настоящему. Прежняя версия подставляла
    несуществующий путь, а mkdir(parents=True) его молча создавал — ветка
    except на записи не исполнялась ни разу, и мутация «убрать except»
    оставляла тест зелёным (круг 2, DS и GLM независимо, 11.09). Идёт боевым
    путём — конструктор → on_status → start(), — а не прямым вызовом метода."""
    _no_system_channel(monkeypatch, tmp_path)
    (tmp_path / "logs").write_bytes(b"")             # файл на месте каталога логов

    def boom(*args, **kw):
        raise OSError("уведомления недоступны")

    monkeypatch.setattr("subprocess.Popen", boom)
    hub = a.AudioHub(_hub_cfg())
    said = []
    hub.on_status = said.append
    hub.start()                                      # не должен бросить

    assert any("СОБЕСЕДНИКОВ" in m for m in said), "человек предупреждён, несмотря на отказы вокруг"
    assert (tmp_path / "logs").is_file(), \
        "запись в лог обязана была отказать, а не пересоздать каталог поверх файла"


# --- Проводка предупреждения: тесты идут БОЕВЫМ путём ------------------------
# Прежние тесты звали hub._warn_no_system_channel напрямую и потому
# пропустили главное: предупреждение уходило из __init__, а on_status демон
# вешает уже ПОСЛЕ конструктора — строка статуса не доходила до интерфейса
# никогда. Откат точки вызова они держали зелёными (круг 1, DS и GLM
# независимо, 10.09). Прямой вызов остался один — test_warning_reaches_all_three_channels,
# он проверяет состав сообщения, не проводку. Ниже — проверки через
# конструктор и start().

def _hub_cfg(device="auto"):
    return {
        "audio": {"samplerate": 16000, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                  "vad_energy_db": -45.0, "record": False, "device": device},
        "log": {"recordings_dir": "recordings"},
        "sufler": {"user_name": "Владелец"},
    }


def _no_system_channel(monkeypatch, tmp_path):
    """Машина без канала собеседников: ни потока приложения, ни устройства."""
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr(a, "fresh_sck_manifest", lambda: None)
    monkeypatch.setattr(a, "find_system_audio", lambda: None)
    monkeypatch.setattr(a.sd.default, "device", (1, None), raising=False)
    monkeypatch.setattr(a.Capture, "start", lambda self: None)
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *args, **kw: type("_T", (), {"start": lambda s: None})())
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: None)


def test_предупреждение_доходит_до_ui_потому_что_ждёт_start(tmp_path, monkeypatch):
    """Порядок проводки: demon строит AudioHub, ПОТОМ вешает on_status, потом
    start(). Если кричать из конструктора, крик уходит в None — и именно так
    было до этой правки. Тест повторяет боевой порядок целиком."""
    _no_system_channel(monkeypatch, tmp_path)

    hub = a.AudioHub(_hub_cfg())          # как daemon.py:537 — on_status ещё нет
    assert hub.on_status is None, "заглушка теста разошлась с боевым порядком"

    said = []
    hub.on_status = said.append           # как daemon.py:541 — уже после конструктора
    hub.start()                           # как daemon.py:626

    assert said, "предупреждение не дошло до интерфейса: крик ушёл в None"
    assert any("СОБЕСЕДНИКОВ" in m for m in said), \
        "в интерфейс ушло что-то не то — человек не поймёт, что потеряно"
    # связь с липкостью — по РЕАЛЬНОМУ тексту, а не по копии строки в тесте:
    # перестановка слов в предупреждении молча вернула бы «стёрлось через
    # секунду» при зелёном CI (№228, GLM r1 по #538)
    import stt_runtime
    warn = next(m for m in said if "СОБЕСЕДНИКОВ" in m)
    assert stt_runtime.is_sticky_status(warn), warn
    assert not stt_runtime.is_recording_failure(warn), "неполная запись — не отказ записи"


def test_режим_только_микрофон_не_поднимает_ложную_тревогу(tmp_path, monkeypatch):
    """device: mic — это диктовка и личные заметки: собеседников там нет по
    замыслу. Кричать о них на каждом запуске значит приучить не читать
    предупреждения вовсе."""
    _no_system_channel(monkeypatch, tmp_path)

    hub = a.AudioHub(_hub_cfg(device="mic"))
    # Прямо про гейт, а не про доставку: пустой said бывает и когда крик
    # просто не долетел (так этот тест и прошёл на мутации 11.09 — зелёный
    # по неверной причине). Факт не должен быть зафиксирован вовсе.
    assert hub._no_system_channel is None, "в режиме mic взведено предупреждение"

    said = []
    hub.on_status = said.append
    hub.start()

    assert not any("СОБЕСЕДНИКОВ" in m for m in said), \
        "ложная тревога в режиме одного микрофона"

    # Контроль: в auto на той же машине предупреждение обязано взводиться —
    # иначе тест выше зелёный просто потому, что сломан весь механизм.
    assert a.AudioHub(_hub_cfg())._no_system_channel is not None, \
        "в auto предупреждение не взводится — проверка режима mic ничего не значит"


def _system_device_that_fails_to_open(monkeypatch, tmp_path, *, fail=True):
    """Машина с устройством собеседников (BlackHole найден), у которого start()
    падает: занято другим процессом, отозвано. Микрофон открывается."""
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr(a, "fresh_sck_manifest", lambda: None)
    monkeypatch.setattr(a, "find_system_audio", lambda: 7)
    monkeypatch.setattr(a.sd.default, "device", (1, None), raising=False)

    def start(self):
        if fail and self.label == "blackhole":
            raise RuntimeError("PortAudio: device busy")

    monkeypatch.setattr(a.Capture, "start", start)
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *args, **kw: type("_T", (), {"start": lambda s: None})())
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: calls.append((args, kw)))
    return calls


def test_отказ_канала_собеседников_на_старте_кричит_так_же_громко(tmp_path, monkeypatch):
    """№230 (Important DS r2 по #531): гейт в конструкторе судит по НАЛИЧИЮ
    устройства, а канал умирает в start() — раньше это была одна тихая строка
    «канал blackhole не открылся», и встреча шла одним микрофоном без
    уведомления и без строки в capture.log. Тот же симптом, тот же крик."""
    calls = _system_device_that_fails_to_open(monkeypatch, tmp_path)
    hub = a.AudioHub(_hub_cfg())
    assert hub._no_system_channel is None, "гейт конструктора молчит: устройство найдено"
    said = []
    hub.on_status = said.append
    hub.start()                                       # blackhole падает, mic открывается

    msg = "\n".join(said)
    assert "СОБЕСЕДНИКОВ" in msg, "отказ канала при старте остался тихой строкой"
    assert "не открылся при старте" in msg and "device busy" in msg, "причина обязана быть названа"
    assert "устройства системного звука не видно" not in msg, "устройство есть — ложная причина"
    banner = calls[0][0][0][-1]
    assert calls and 'sound name "Glass"' in banner, "уведомление со звуком не ушло"
    # совет по причине: право в порядке, устройство занято — не гнать в настройки TCC
    assert "освободите" in banner and "Запись экрана" not in banner, banner
    assert said.count(next(m for m in said if "СОБЕСЕДНИКОВ" in m)) == 1 and \
        not any("канал blackhole не открылся" in m for m in said), "причина один раз, не тихой строкой и криком"
    log = (tmp_path / "logs" / "capture.log").read_text(encoding="utf-8")
    assert a.MIC_ONLY_LOG_MARK in log and "device busy" in log

    # контроль: тот же канал открылся — крика нет (иначе тест выше зелёный от шума)
    calls = _system_device_that_fails_to_open(monkeypatch, tmp_path, fail=False)
    hub = a.AudioHub(_hub_cfg())
    said = []
    hub.on_status = said.append
    hub.start()
    assert not any("СОБЕСЕДНИКОВ" in m for m in said) and not calls


def test_отказ_потока_sck_при_старте_не_винит_ни_право_ни_blackhole(tmp_path, monkeypatch):
    """Основной путь macOS 15+: ScreenCaptureKit жив (манифест свежий), но поток
    системного звука не растёт и канал падает в start(), микрофон из того же
    потока открывается. Причина — только отказ при старте: BlackHole на этой
    машине не искали (DS и GLM r1 по #537 — раньше в крик попадало ложное
    «устройства системного звука не видно» и совет проверить право)."""
    monkeypatch.setattr(a, "ROOT", tmp_path)
    monkeypatch.setattr(a, "fresh_sck_manifest",
                        lambda: {"samplerate": 16000, "system": "/tmp/sys.pcm", "mic": "/tmp/mic.pcm"})
    monkeypatch.setattr(a, "find_system_audio", lambda: None)
    monkeypatch.setattr(a.sd.default, "device", (1, None), raising=False)

    def start(self):
        if self.label == "blackhole":
            raise RuntimeError("поток приложения не растёт — приложение кадров не пишет")

    monkeypatch.setattr(a.TapStreamCapture, "start", start)
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *args, **kw: type("_T", (), {"start": lambda s: None})())
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: calls.append((args, kw)))

    hub = a.AudioHub(_hub_cfg())
    assert [c.label for c in hub.captures] == ["blackhole", "mic"] and hub._no_system_channel is None
    said = []
    hub.on_status = said.append
    hub.start()

    msg = "\n".join(said)
    assert "СОБЕСЕДНИКОВ" in msg and "не открылся при старте" in msg and "не растёт" in msg
    assert "устройства системного звука не видно" not in msg and "ScreenCaptureKit не поднялся" not in msg, msg
    assert "Запись экрана" not in calls[0][0][0][-1], "право в порядке — совет про TCC ложный"


def test_битый_лог_не_срывает_старт_записи(tmp_path, monkeypatch):
    """capture.log дописывает Swift-часть, и чтение может застать оборванную
    UTF-8 последовательность. Раньше UnicodeDecodeError (наследник ValueError,
    не OSError) летел сквозь except из конструктора и убивал старт записи.
    Теперь основная защита — errors="replace" при чтении (причина сохраняется
    с U+FFFD), а ValueError в except — страховка на случай снятия replace.
    Тест проверяет итог: старт не падает и предупреждение доходит; ветку
    except ValueError он при replace не исполняет — это задумано (круг 2)."""
    _no_system_channel(monkeypatch, tmp_path)
    log = tmp_path / "logs" / "capture.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # \xff\xfe — не-UTF-8 байты: ровно то, что читатель застаёт, если Swift
    # дописывает многобайтовую букву прямо сейчас.
    log.write_bytes(b"SCK: denied\n\xff\xfe" + "оборванный хвост".encode("utf-8"))

    hub = a.AudioHub(_hub_cfg())
    said = []
    hub.on_status = said.append
    hub.start()                            # не должен бросить

    assert any("СОБЕСЕДНИКОВ" in m for m in said), "предупреждение потерялось на битом логе"


def test_причиной_не_становится_собственное_предупреждение_прошлой_встречи(tmp_path, monkeypatch):
    """Мы пишем предупреждение в тот же capture.log, из которого берём причину.
    Без метки своей строки причиной следующей встречи становилось бы эхо
    предыдущей вместо сообщения Swift."""
    _no_system_channel(monkeypatch, tmp_path)
    log = tmp_path / "logs" / "capture.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "SCK: permission denied by user\n"
        f"2026-09-10 12:00:00 {a.MIC_ONLY_LOG_MARK}: прошлая встреча\n",
        encoding="utf-8")

    hub = a.AudioHub(_hub_cfg())
    said = []
    hub.on_status = said.append
    hub.start()

    msg = "".join(said)
    assert "permission denied by user" in msg, "причина от Swift потеряна"
    assert "прошлая встреча" not in msg, "причиной стало собственное предупреждение"


def test_смерть_канала_собеседников_после_старта_кричит_один_раз(monkeypatch, tmp_path):
    """№232: сторож переводит канал собеседников в _hung — раньше это была
    тихая строка «перезапуск завис, канал отключён», и человек до конца встречи
    не знал, что собеседников в записи больше нет. Тот же крик, что на старте
    (№230), но один на встречу: после крика на старте сторож молчит, повторный
    проход сторожа не кричит, микрофон про собеседников не кричит."""
    monkeypatch.setattr(a, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: calls.append((args, kw)))

    class _Dead:
        def __init__(self, label):
            self.label = label
            self.q = queue.Queue()
        def start(self): pass
        def stop(self): pass

    def dead_hub(labels, *, warned=False):
        hub = _hub()
        hub.captures = [_Dead(lbl) for lbl in labels]
        hub._mic_only_warned = warned
        hub.said = []
        hub.on_status = hub.said.append
        monkeypatch.setattr(hub, "_restart_guarded", lambda c: TimeoutError("не вернулся за 5с"))
        hub._last_frame = {lbl: a.time.time() - 40 for lbl in labels}
        return hub

    hub = dead_hub(["blackhole", "mic"])
    hub._last_frame["mic"] = a.time.time()                    # микрофон жив
    hub._watch_streams()
    assert hub._hung == {"blackhole"}
    scream = [m for m in hub.said if "СОБЕСЕДНИКОВ" in m]
    assert len(scream) == 1 and "умер во время встречи" in scream[0] and "пропал" in scream[0], hub.said
    assert not any("перезапуск завис" in m and "СОБЕСЕДНИКОВ" not in m for m in hub.said), \
        "причина один раз: криком, не тихой строкой сторожа и криком"
    assert len(calls) == 1, "уведомление со звуком не ушло"
    banner = calls[0][0][0][-1]
    assert 'sound name "Glass"' in banner and "остановите и запустите запись заново" in banner, banner
    assert "Запись экрана" not in banner and "освободите" not in banner, "умерший канал не чинится ни правом, ни устройством"
    log = (tmp_path / "logs" / "capture.log").read_text(encoding="utf-8")
    assert a.MIC_ONLY_LOG_MARK in log and "перезапуск завис" in log
    # второй проход сторожа: канал уже в _hung — ни крика, ни строки
    hub._last_check = 0.0
    hub._watch_streams()
    assert len([m for m in hub.said if "СОБЕСЕДНИКОВ" in m]) == 1 and len(calls) == 1

    # микрофон умер — обычная строка сторожа, крика про собеседников нет
    mic = dead_hub(["mic"])
    mic._watch_streams()
    assert mic._hung == {"mic"} and any("перезапуск завис" in m for m in mic.said)
    assert not any("СОБЕСЕДНИКОВ" in m for m in mic.said) and len(calls) == 1

    # уже кричали на старте — сторож второй раз не кричит, но канал отключает и говорит об этом
    again = dead_hub(["blackhole"], warned=True)
    again._watch_streams()
    assert again._hung == {"blackhole"} and any("перезапуск завис" in m for m in again.said)
    assert not any("СОБЕСЕДНИКОВ" in m for m in again.said) and len(calls) == 1

    # Основной путь macOS 15+ (Critical DS r1 по #541): поток ScreenCaptureKit перестал расти,
    # restart() возвращает обычную ошибку за ~3 с, не TimeoutError — первая неудача тихая,
    # вторая подряд кричит; канал в _hung не попадает, поток может ожить
    def dead_stream(hub):
        monkeypatch.setattr(hub, "_restart_guarded", lambda c: RuntimeError("поток приложения не растёт"))

    def tick(hub):
        hub._last_check = 0.0
        hub._last_try = {}
        hub._watch_streams()

    sck = dead_hub(["blackhole", "mic"])
    sck._last_frame["mic"] = a.time.time()
    dead_stream(sck)
    tick(sck)
    assert not sck._hung and not any("СОБЕСЕДНИКОВ" in m for m in sck.said) and len(calls) == 1, "одна неудача — ещё не смерть"
    tick(sck)
    scream = [m for m in sck.said if "СОБЕСЕДНИКОВ" in m]
    assert len(scream) == 1 and "2 раза подряд" in scream[0] and "поток приложения не растёт" in scream[0], sck.said
    assert not sck._hung and len(calls) == 2 and "пишем только микрофон" in scream[0]
    assert "пробуем перезапустить" in calls[-1][0][0][-1], "ветка повторов канал не бросает — совет не «перезапустите» сразу (критика DS r2)"
    # канал ожил — липкая строка снимается явно, следующая смерть кричит заново;
    # «снова пишется», а не «запись полная»: микрофон мог лежать в _hung (Important DS r2)
    monkeypatch.setattr(sck, "_restart_guarded", lambda c: None)
    tick(sck)
    back = [m for m in sck.said if a.stt_runtime.MIC_BACK_NOTICE in m]
    assert len(back) == 1 and not sck._mic_only_warned and not sck._system_dead and not sck._fail_streak, sck.said
    assert "полная" not in back[0] and "снова пишется" in back[0]
    dead_stream(sck)
    sck._last_frame["blackhole"] = a.time.time() - 40
    tick(sck)
    tick(sck)
    assert len([m for m in sck.said if "СОБЕСЕДНИКОВ" in m]) == 2 and len(calls) == 3, "после оживления смерть кричит снова"

    # device: blackhole — микрофона в записи нет, текст не обещает «только микрофон» (Important DS r1)
    solo = dead_hub(["blackhole"])
    dead_stream(solo)
    tick(solo)
    tick(solo)
    scream = [m for m in solo.said if "СОБЕСЕДНИКОВ" in m]
    assert len(scream) == 1 and "ни вас" in scream[0] and "только микрофон" not in scream[0], solo.said
    assert "ни вас" in calls[-1][0][0][-1]
    # то же на старте без микрофона (Important GLM r2): «не захвачен», но не «только микрофон»
    solo_start = dead_hub(["blackhole"])
    solo_start._warn_no_system_channel(start_error="device busy")
    assert "не захвачен: в записи не будет ни собеседников, ни вас" in solo_start.said[-1], solo_start.said
    assert "ни вас" in calls[-1][0][0][-1] and "только ваш микрофон" not in calls[-1][0][0][-1]

    # флапающий поток: строка статуса при каждой потере, звук — не больше трёх за встречу (критика GLM r2)
    storm = dead_hub(["blackhole", "mic"])
    storm._last_frame["mic"] = a.time.time()
    before = len(calls)
    for _ in range(5):
        dead_stream(storm)
        storm._last_frame["blackhole"] = a.time.time() - 40
        tick(storm)
        tick(storm)
        monkeypatch.setattr(storm, "_restart_guarded", lambda c: None)
        tick(storm)
    assert len([m for m in storm.said if "СОБЕСЕДНИКОВ" in m]) == 5, "каждая потеря — строка"
    assert len(calls) - before == a.AudioHub.LOUD_SCREAMS == 3, "звук — не чаще трёх за встречу"
