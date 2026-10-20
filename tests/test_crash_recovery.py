"""Аварийный обрыв встречи: что происходит, когда демона убили.

Три отказа, каждый из которых стоил встречи целиком.

1. Гонка конвертеров. Демон запускал rebuild_transcript ДО финализации
   записей, а тот через 10 секунд считал .pcm брошенным и конвертировал сам.
   На трёхчасовой встрече (345 МБ на канал) финализация в 10 секунд не
   укладывается — два процесса писали в один .wav и оба удаляли исходник.

2. После SIGKILL встреча не восстанавливалась никогда: finally не исполняется,
   пересборку запускать некому, а ретеншн через два дня удалял единственную
   запись.

3. emit ронял поток STT при закрытом пайпе, главный цикл продолжал слать hb —
   watchdog приложения видел «демон жив» при стоящей транскрипции.
"""
import os
import pathlib
import sys
import threading

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def rec_dir(tmp_path):
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def _writer(path: pathlib.Path):
    """Заявка писателя на `.pcm`, как у `AudioHub._claim_sink`: EX на своём
    дескрипторе. flock принадлежит описанию открытого файла — второй open в
    этом же процессе с ним конфликтует, как чужой процесс."""
    import fcntl

    f = path.open("ab")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def test_does_not_touch_pcm_while_its_writer_converts(rec_dir, monkeypatch):
    """Пока писатель держит заявку на `.pcm` (финализирует в `.wav.part`),
    конвертировать его второй раз нельзя — ни в цикле, ни по сроку.

    mtime исходника старый: демон уже читает этот файл и в него не пишет —
    так выглядит длинная встреча, у которой конвертация идёт дольше срока.
    На этом ошибалось правило «mtime старше 10 секунд», а правило «лок
    встречи» — на встрече, начатой после (e1 №728)."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 0.3)
    clock = _Clock()
    monkeypatch.setattr(rt, "time", clock)
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    old = pcm.stat().st_mtime - 600
    os.utime(pcm, (old, old))
    part = rec_dir / "s_mic.wav.part"
    part.write_bytes(b"")                           # писатель сейчас пишет
    holder = _writer(pcm)
    try:
        out = rt.wait_recording(rec_dir, "s", "mic", 16000)
    finally:
        holder.close()

    assert out == rt.file_locks.BUSY, "полез в чужую конвертацию"
    assert pcm.exists(), "исходник удалён вторым писателем"
    assert part.exists(), "убрали .part из-под живого писателя"
    assert not (rec_dir / "s_mic.wav").exists()
    assert clock.slept, "не ждал писателя — сдался сразу"


def test_осиротевший_part_не_держит_канал_даже_при_чужой_встрече(rec_dir, monkeypatch):
    """Писатель убит посреди финализации, а лок встречи держит уже ДРУГАЯ встреча
    (e1 №728: база ждала весь срок и отдавала None — канал собеседника терялся
    до ручного повтора). Заявка на `.pcm` свободна — писателя нет, `.part` —
    огрызок: убрать, `.pcm` сконвертировать сразу, без ожидания."""
    import live_gate
    import rebuild_transcript as rt

    root = rec_dir.parent
    monkeypatch.setattr(rt, "_root", lambda: root)
    monkeypatch.setattr(rt, "WAIT_WAV_S", 8)
    clock = _Clock()
    monkeypatch.setattr(rt, "time", clock)
    pcm = rec_dir / "s_blackhole.pcm"
    pcm.write_bytes(b"\0" * 4096)
    part = rec_dir / "s_blackhole.wav.part"
    part.write_bytes(b"")                           # остался от убитого писателя
    other, outcome = live_gate.hold_meeting(root, sleep=lambda s: None)
    try:
        assert outcome == live_gate.file_locks.TAKEN
        out = rt.wait_recording(rec_dir, "s", "blackhole", 16000)
    finally:
        other.close()

    assert out is not None and out.suffix == ".wav", (
        "канал собеседника потерян: лок чужой встречи заблокировал брошенный .pcm")
    assert not part.exists(), "осиротевший .part остался копиться в recordings/"
    assert not pcm.exists(), "исходник не снят после конвертации под заявкой"
    assert clock.slept == [], "ждали писателя, которого нет"


def test_конвертация_публикует_wav_одним_движением(rec_dir, monkeypatch):
    """Целевой .wav не должен существовать, пока он не дописан.

    Усечённый .wav опаснее его отсутствия: `wait_recording` видит файл и
    принимает за готовый, а целый .pcm к этому моменту уже удалён — встреча
    собирается из огрызка. Смотрим не на остатки после падения (их подчистил
    бы и `finally`), а на сам полёт: в момент записи кадров имени `.wav` на
    диске быть не может. Это и есть разница между «пишем в цель» и
    «пишем во временный, потом переименовываем».
    """
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * (1 << 21))              # два мегабайта: запись в два захода
    target = rec_dir / "s_mic.wav"

    real_open = rt.wave.open
    seen_mid_flight = []

    def watching(path, mode):
        w = real_open(path, mode)
        real_frames = w.writeframes

        def writeframes(data):
            seen_mid_flight.append(target.exists())
            return real_frames(data)

        w.writeframes = writeframes
        return w

    monkeypatch.setattr(rt.wave, "open", watching)
    out = rt.pcm_to_wav(pcm, 16000)

    assert seen_mid_flight, "конвертация не записала ни одного кадра"
    assert not any(seen_mid_flight), (
        "целевой .wav существовал ещё недописанным — обрыв оставит огрызок, "
        "который пересборка примет за готовую запись")
    assert out.exists() and out == target
    assert not pcm.exists(), "исходник должен уйти после успешной конвертации"
    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.wav"], (
        f"в recordings/ остался мусор: {sorted(p.name for p in rec_dir.iterdir())}")


def test_converts_pcm_itself_when_its_writer_is_gone(rec_dir, monkeypatch):
    """Писателя нет, .part нет — пересборка обязана добить запись сама."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 2)
    (rec_dir / "s_mic.pcm").write_bytes(b"\0" * 4096)

    out = rt.wait_recording(rec_dir, "s", "mic", 16000)

    assert out is not None and out.suffix == ".wav", "запись осталась несконвертированной"
    assert not (rec_dir / "s_mic.pcm").exists()


def test_pcm_to_wav_refuses_while_the_writer_holds_its_claim(rec_dir):
    """Дверь сама не трогает `.pcm` под чужой заявкой: ни `.wav`, ни удаления."""
    import file_locks
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    part = rec_dir / "s_mic.wav.part"
    part.write_bytes(b"")
    holder = _writer(pcm)
    try:
        got = rt.pcm_to_wav(pcm, 16000)
    finally:
        holder.close()

    assert got == file_locks.BUSY
    assert pcm.exists() and part.exists()
    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.pcm", "s_mic.wav.part"]


def test_pcm_to_wav_on_a_volume_without_flock_touches_nothing(rec_dir, monkeypatch):
    """Том без flock: заявку не взять — судить не по чему. Ничего не удаляем и
    не публикуем: частичный `.wav` под финальным именем пересборка приняла бы
    за готовый (in2 №728, Sonnet C1)."""
    import errno
    import fcntl
    import file_locks
    import rebuild_transcript as rt

    def no_flock(f, op):
        raise OSError(errno.ENOLCK, "no locks")

    monkeypatch.setattr(file_locks.fcntl, "flock", no_flock)
    monkeypatch.setattr(rt, "WAIT_WAV_S", 0.3)
    monkeypatch.setattr(rt, "time", _Clock())
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    part = rec_dir / "s_mic.wav.part"
    part.write_bytes(b"")

    assert rt.pcm_to_wav(pcm, 16000) == file_locks.NO_FLOCK
    clock = _Clock()
    monkeypatch.setattr(rt, "time", clock)
    assert rt.wait_recording(rec_dir, "s", "mic", 16000) == file_locks.NO_FLOCK
    assert clock.slept == [], "том без flock — свойство тома, ждать его нечего (out1 Sonnet I1)"
    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.pcm", "s_mic.wav.part"]
    assert fcntl.flock is no_flock


def test_pcm_to_wav_gives_up_when_the_name_is_gone_under_the_claim(rec_dir, monkeypatch):
    """Писатель снял имя между открытием и заявкой (его `.wav` уже опубликован):
    дверь не публикует свою копию поверх и ничего не удаляет."""
    import file_locks
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    wav = rec_dir / "s_mic.wav"
    real = file_locks.acquire_outcome

    def writer_finishes_first(f, *a, **kw):
        wav.write_bytes(b"writer")
        pcm.unlink()
        return real(f, *a, **kw)

    monkeypatch.setattr(rt.file_locks, "acquire_outcome", writer_finishes_first)

    assert rt.pcm_to_wav(pcm, 16000) == rt.GONE
    assert wav.read_bytes() == b"writer", "копия пересборки легла поверх .wav писателя"


def test_pcm_to_wav_without_a_file_is_gone(rec_dir):
    import rebuild_transcript as rt

    assert rt.pcm_to_wav(rec_dir / "s_mic.pcm", 16000) == rt.GONE
    assert not (rec_dir / "s_mic.pcm").exists(), "дверь создала .pcm — xb писателя упал бы"


def test_finalize_publishes_wav_atomically(tmp_path):
    """Готовый .wav появляется одним движением, а не растёт на глазах у чтеца."""
    import audio

    # настоящий конструктор — он без ввода-вывода (№311); стабу больше не
    # нужно повторять боевые поля (_lock, _sinks) руками
    hub = audio.AudioHub({"audio": {"samplerate": 16000, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                                    "vad_energy_db": -45.0, "record": False, "device": "auto"},
                          "log": {"recordings_dir": str(tmp_path)}, "sufler": {"user_name": "Владелец"}})
    pcm = tmp_path / "s_mic.pcm"
    pcm.write_bytes(b"\0" * (16000 * 2 * 6))       # 6 секунд, больше порога мусора
    hub._sinks = {"mic": pcm.open("ab")}

    hub._finalize_recordings()

    assert (tmp_path / "s_mic.wav").exists()
    assert not (tmp_path / "s_mic.wav.part").exists(), "временный файл остался"
    assert not pcm.exists(), "исходник не убран после успешной конвертации"


def _hub(tmp_path):
    import audio

    return audio.AudioHub({"audio": {"samplerate": 16000, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                                     "vad_energy_db": -45.0, "record": False, "device": "auto"},
                           "log": {"recordings_dir": str(tmp_path)}, "sufler": {"user_name": "Владелец"}})


def _claim_state(path):
    import file_locks

    with path.open("rb") as f:
        got = file_locks.acquire_outcome(f, attempts=1)
    return got


def test_the_writer_holds_its_claim_until_the_wav_is_published(tmp_path, monkeypatch):
    """Заявка писателя живёт с открытия `.pcm` до снятия его имени: пока идёт
    конвертация, пересборка видит «занято», а не брошенный файл (№728). И
    буфер файла выгружается до чтения: раньше это делал `close()` в начале."""
    import audio
    import file_locks

    hub = _hub(tmp_path)
    hub.stamp = "s"
    hub.captures = [type("C", (), {"label": "mic"})()]
    monkeypatch.setattr(hub, "prune_recordings", lambda *a, **kw: 0)
    hub._open_sinks()
    pcm = tmp_path / "s_mic.pcm"
    assert _claim_state(pcm) == file_locks.BUSY, "писатель не держит заявку на свой .pcm"
    hub._sinks["mic"].write(b"\1\0" * 16000 * 6)          # в буфере, без flush

    seen, late = [], []
    real = audio.wave.open
    sink = hub._sinks["mic"]

    def watching(path, mode):
        if pcm.exists():
            seen.append(_claim_state(pcm))
            try:                                   # застрявший _consume дописывает блок
                sink.write(b"\2\0" * 160)
            except ValueError:
                late.append("громко")
        return real(path, mode)

    monkeypatch.setattr(audio.wave, "open", watching)
    hub._finalize_recordings()

    assert seen == [file_locks.BUSY], "заявка снята до конвертации — пересборка сочла бы нас брошенными"
    assert late == ["громко"], "поздний блок ушёл молча в снимаемый .pcm (out1 Haiku C1)"
    wav = tmp_path / "s_mic.wav"
    assert wav.exists() and not pcm.exists()
    import wave as _w
    with _w.open(str(wav), "rb") as w:
        assert w.getnframes() == 16000 * 6, "хвост из буфера файла не попал в .wav"


def test_a_failing_close_does_not_skip_the_next_channel(tmp_path):
    """Сбой `close` первого канала не роняет стоп-путь и не оставляет второй
    канал сырым (in2 №728, DS C4)."""
    hub = _hub(tmp_path)

    class BadClose:
        def __init__(self, f):
            self._f, self.name = f, f.name

        def flush(self):
            self._f.flush()

        def fileno(self):
            return self._f.fileno()

        def close(self):
            self._f.close()
            raise OSError("ENOSPC")

    a = tmp_path / "s_mic.pcm"
    b = tmp_path / "s_blackhole.pcm"
    for p in (a, b):
        p.write_bytes(b"\0" * (16000 * 2 * 6))
    hub._sinks = {"mic": BadClose(a.open("ab")), "blackhole": b.open("ab")}

    hub._finalize_recordings()

    assert (tmp_path / "s_mic.wav").exists() and (tmp_path / "s_blackhole.wav").exists()


def test_a_sink_that_failed_mid_meeting_releases_its_claim(tmp_path):
    """Диск отказал посреди встречи: файл больше никто не допишет — заявка
    снимается сразу, а не когда отпустят ссылку (in1 №728, Sonnet M1 = DS C3)."""
    import file_locks

    hub = _hub(tmp_path)
    pcm = tmp_path / "s_mic.pcm"
    f = pcm.open("xb")
    assert file_locks.acquire_outcome(f) == file_locks.TAKEN
    keep = []

    class Failing:
        name = f.name

        def write(self, _):
            raise OSError("ENOSPC")

        def flush(self):
            pass

        def close(self):
            f.close()
            keep.append("closed")

    hub._sinks = {"mic": Failing()}
    c = type("C", (), {"label": "mic"})()
    hub._last_frame, hub._real_frame = {}, {}
    try:
        hub._consume(c, __import__("numpy").zeros(160, dtype="float32"), notify_frame=False)
    except Exception:  # noqa: BLE001 — нас интересует только судьба файла
        pass

    assert keep == ["closed"], "сбойный sink не закрыт — заявка держится без писателя"
    assert _claim_state(pcm) == file_locks.TAKEN


def test_broken_pipe_stops_the_daemon_instead_of_killing_one_thread():
    """Обрыв пайпа = штатный стоп, а не молчаливая смерть потока."""
    import daemon as d

    stop = threading.Event()
    d._stop_event = stop

    class Dead:
        def write(self, *_):
            raise BrokenPipeError("читатель ушёл")

        def flush(self):
            pass

    real = d.sys.stdout
    d.sys.stdout = Dead()
    try:
        d.emit({"type": "transcript", "text": "реплика"})   # не должно бросить
    finally:
        d.sys.stdout = real
        d._stop_event = None

    assert stop.is_set(), "поток умер бы молча, а главный цикл продолжал слать hb"


class _Clock:
    """Часы `wait_recording`: время идёт только во сне, срок не зависит от
    вытеснения процесса (выход 9 №725, DS I1). Код ставит срок и цикл на
    `time.monotonic` (№728); `time` со своей эпохой оставлен, чтобы
    смешанные часы валили тест. Других членов `time` нет:
    `perf_counter` и прочие падают здесь, а не уходят в настоящие часы
    (выходы 10–14)."""

    def __init__(self):
        self.t, self.slept = 0.0, []

    def time(self):
        return self.t

    def monotonic(self):
        # Своя эпоха: срок по одним часам, цикл по другим — тест падает (выход 12, Haiku I1).
        return self.t + 60     # не 10**6: смешанный мутант — десятки шагов, не полмиллиона (выход 13)

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_a_held_meeting_lock_alone_does_not_hold_a_dead_writers_pcm(rec_dir, monkeypatch):
    """Лок встречи держится, `.pcm` без заявки: прежний код ждал весь срок и
    конвертировал по сроку — в том числе `.pcm` соседки той же минуты (шаг Haiku
    out8 №725). Теперь решает только заявка: свободна — конвертируем сразу,
    срока «по сроку» нет."""
    import live_gate
    import rebuild_transcript as rt

    root = rec_dir.parent
    monkeypatch.setattr(rt, "_root", lambda: root)
    monkeypatch.setattr(rt, "WAIT_WAV_S", 5)
    clock = _Clock()
    monkeypatch.setattr(rt, "time", clock)
    (rec_dir / "s_mic.pcm").write_bytes(b"\0" * 4096)
    console, outcome = live_gate.hold_meeting(root, sleep=lambda s: None)
    try:
        assert outcome == live_gate.file_locks.TAKEN
        out = rt.wait_recording(rec_dir, "s", "mic", 16000)
    finally:
        console.close()

    assert out == rec_dir / "s_mic.wav" and out.exists()
    assert clock.slept == []


def test_a_writer_that_finishes_while_we_wait_hands_over_its_wav(rec_dir, monkeypatch):
    """Писатель держит заявку и дописывает `.wav` за время ожидания: пересборка
    берёт его `.wav`, своей копии не делает."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 10)
    clock = _Clock()
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    holder = _writer(pcm)

    def sleep(s):
        clock.sleep(s)
        if len(clock.slept) == 2:           # писатель опубликовал .wav и снял .pcm
            (rec_dir / "s_mic.wav").write_bytes(b"writer")
            pcm.unlink()
            holder.close()

    monkeypatch.setattr(rt, "time", clock)
    try:
        out = rt.wait_recording(rec_dir, "s", "mic", 16000, sleep=sleep)
    finally:
        holder.close()

    assert out == rec_dir / "s_mic.wav" and out.read_bytes() == b"writer"
    assert len(clock.slept) == 2


def test_a_writer_without_its_claim_says_so_for_the_whole_meeting(tmp_path, monkeypatch):
    """Заявка не взялась — запись идёт, но статус липкий: пересборка после сбоя
    может принять такую запись за брошенную (out1 DS C1)."""
    import file_locks
    import stt_runtime

    hub = _hub(tmp_path)
    hub.stamp = "s"
    hub.captures = [type("C", (), {"label": "mic"})()]
    said = []
    monkeypatch.setattr(hub, "_say", said.append)
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.BUSY)
    hub._open_sinks()

    assert "mic" in hub._sinks, "без заявки писатель выключил запись"
    claim = [m for m in said if m and "ЗАМОК ЗАПИСИ НЕ ВЗЯТ" in m]
    assert len(claim) == 1, said
    assert claim[0].sticky is True and claim[0].topic == stt_runtime.TOPIC_DISK and claim[0].error
    hub._sinks["mic"].close()


def test_a_claim_that_raises_does_not_stop_the_recording(tmp_path, monkeypatch, capsys):
    import file_locks

    hub = _hub(tmp_path)
    hub.stamp = "s"
    hub.captures = [type("C", (), {"label": "mic"})()]

    def boom(f, **kw):
        raise RuntimeError("сбой заявки")

    monkeypatch.setattr(file_locks, "acquire_outcome", boom)
    hub._open_sinks()

    assert "mic" in hub._sinks
    assert "замок записи mic не взят: сбой заявки" in capsys.readouterr().err
    hub._sinks["mic"].close()


def test_a_sink_already_taken_by_the_finalizer_is_not_closed_by_consume(tmp_path):
    """Сбой записи у sink, который уже в снимке финализации: закрывает его
    финализация, а не `_consume` посреди её конвертации (out1 Sonnet M1)."""
    hub = _hub(tmp_path)
    closed = []

    class Failing:
        name = str(tmp_path / "s_mic.pcm")

        def write(self, _):
            raise OSError("ENOSPC")

        def flush(self):
            pass

        def close(self):
            closed.append(1)

    fake = Failing()

    class Snapshotted(dict):
        """`_consume` взял sink до снимка, а `pop` уже ничего не находит."""

        def get(self, k, d=None):
            return fake

    hub._sinks = Snapshotted()
    c = type("C", (), {"label": "mic"})()
    hub._last_frame, hub._real_frame = {}, {}
    hub._closing = False
    try:
        hub._consume(c, __import__("numpy").zeros(160, dtype="float32"), notify_frame=False)
    except Exception:  # noqa: BLE001 — нас интересует только судьба файла
        pass

    assert closed == [], "_consume закрыл sink, который закрывает финализация"


def test_pcm_to_wav_leaves_an_empty_pcm_to_its_writer(rec_dir):
    """`.pcm` нулевой длины: писатель только что создал его и берёт заявку
    следующей строкой — снять имя значит потерять канал (out1 Haiku M2)."""
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"")

    assert rt.pcm_to_wav(pcm, 16000) == rt.EMPTY
    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.pcm"]


def test_a_rebuild_in_the_window_before_the_claim_leaves_the_writer_its_file(tmp_path):
    """Окно `open("xb")` → заявка: пересборка заходит в него и уходит ни с чем,
    писатель берёт заявку на своё имя (out1, шаг Haiku C1)."""
    import file_locks
    import rebuild_transcript as rt

    hub = _hub(tmp_path)
    said = []
    hub._say = said.append
    pcm = tmp_path / "s_mic.pcm"
    f = pcm.open("xb")
    try:
        assert rt.pcm_to_wav(pcm, 16000) == rt.EMPTY
        hub._claim_sink("mic", f)
        assert pcm.exists() and not (tmp_path / "s_mic.wav").exists()
        assert not [m for m in said if m], said
        assert _claim_state(pcm) == file_locks.BUSY
    finally:
        f.close()


def test_a_claim_on_an_unlinked_pcm_is_loud(tmp_path):
    """Заявка взялась, но имени у файла уже нет: писать есть куда, защищать
    нечего — громко, на всю встречу."""
    hub = _hub(tmp_path)
    said = []
    hub._say = said.append
    pcm = tmp_path / "s_mic.pcm"
    f = pcm.open("xb")
    pcm.unlink()
    try:
        hub._claim_sink("mic", f)
    finally:
        f.close()
    claim = [m for m in said if m and "ЗАМОК ЗАПИСИ НЕ ВЗЯТ" in m]
    assert len(claim) == 1 and claim[0].sticky is True, said


def test_a_pcm_removed_under_the_recording_is_reported(tmp_path):
    """`.pcm` сняли из-под записи: финализация говорит о потере, а не молчит."""
    hub = _hub(tmp_path)
    said = []
    hub._say = said.append
    pcm = tmp_path / "s_mic.pcm"
    f = pcm.open("xb")
    f.write(b"\0" * 16000 * 2 * 6)
    pcm.unlink()
    hub._sinks = {"mic": f}

    hub._finalize_recordings()

    lost = [m for m in said if m and "ПРОПАЛА" in m]
    assert len(lost) == 1 and lost[0].error, said
    assert f.closed


def test_an_empty_pcm_at_the_deadline_is_no_channel(rec_dir, monkeypatch):
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 4)
    clock = _Clock()
    monkeypatch.setattr(rt, "time", clock)
    (rec_dir / "s_mic.pcm").write_bytes(b"")

    assert rt.wait_recording(rec_dir, "s", "mic", 16000) is None
    assert (rec_dir / "s_mic.pcm").exists()
    # срок включительно: на 4-й секунде уже не спим (мутант `>` дал бы третий сон)
    assert clock.slept == [2.0, 2.0], "пустой файл живого писателя ждут ровно до срока"


def test_pcm_to_wav_on_an_unreadable_pcm_is_not_a_volume_without_flock(rec_dir):
    """Права не дали открыть — свой исход и свой текст: «том без flock» послал бы
    человека чинить не то (out1 DS M2)."""
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    pcm.chmod(0)
    try:
        assert rt.pcm_to_wav(pcm, 16000) == rt.UNREADABLE
    finally:
        pcm.chmod(0o600)
    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.pcm"]
    assert "права" in rt._REFUSAL_TEXT[rt.UNREADABLE]


def test_pcm_to_wav_broken_mid_conversion_leaves_no_partial(rec_dir, monkeypatch):
    """Обрыв посреди конвертации: временный файл убран, `.pcm` цел — иначе полный
    WAV встречи лежал бы под именем, которого никто не ждёт."""
    import rebuild_transcript as rt

    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)

    def broken(self, target):
        raise OSError("EIO")

    monkeypatch.setattr(rt.pathlib.Path, "replace", broken)
    with pytest.raises(OSError):
        rt.pcm_to_wav(pcm, 16000)

    assert sorted(p.name for p in rec_dir.iterdir()) == ["s_mic.pcm"]


def test_a_wav_published_right_before_the_deadline_is_taken(rec_dir, monkeypatch):
    """Писатель опубликовал `.wav` между последним заходом двери и сроком:
    берём его, а не «не готово» (out1 DS M3в)."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 0)
    monkeypatch.setattr(rt, "time", _Clock())
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    wav = rec_dir / "s_mic.wav"

    def writer_publishes(p, sr):
        wav.write_bytes(b"writer")
        return rt.file_locks.BUSY

    monkeypatch.setattr(rt, "pcm_to_wav", writer_publishes)

    assert rt.wait_recording(rec_dir, "s", "mic", 16000) == wav
