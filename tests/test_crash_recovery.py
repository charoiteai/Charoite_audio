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


def test_does_not_touch_pcm_while_daemon_converts(rec_dir, monkeypatch):
    """Пока рядом лежит .wav.part, конвертировать чужой .pcm нельзя."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 2)
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    # Ключ сценария: mtime замер в момент stop(), потому что демон уже читает
    # этот файл и больше в него не пишет. Именно так выглядит длинная встреча,
    # у которой конвертация идёт дольше десяти секунд, — и именно на этом
    # старое правило «mtime старше 10 секунд = брошен» ошибалось.
    old = pcm.stat().st_mtime - 600
    os.utime(pcm, (old, old))
    (rec_dir / "s_mic.wav.part").write_bytes(b"")   # демон сейчас пишет

    out = rt.wait_recording(rec_dir, "s", "mic", 16000)

    assert out is None, "полез в чужую конвертацию"
    assert pcm.exists(), "исходник удалён вторым писателем"


def test_converts_pcm_itself_when_daemon_is_gone(rec_dir, monkeypatch):
    """Демона нет, .part нет — пересборка обязана добить запись сама."""
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 2)
    monkeypatch.setattr(rt, "_daemon_alive", lambda: False)
    (rec_dir / "s_mic.pcm").write_bytes(b"\0" * 4096)

    out = rt.wait_recording(rec_dir, "s", "mic", 16000)

    assert out is not None and out.suffix == ".wav", "запись осталась несконвертированной"


def test_finalize_publishes_wav_atomically(tmp_path):
    """Готовый .wav появляется одним движением, а не растёт на глазах у чтеца."""
    import audio

    hub = object.__new__(audio.AudioHub)
    hub.sr = 16000
    pcm = tmp_path / "s_mic.pcm"
    pcm.write_bytes(b"\0" * (16000 * 2 * 6))       # 6 секунд, больше порога мусора
    hub._sinks = {"mic": pcm.open("ab")}

    hub._finalize_recordings()

    assert (tmp_path / "s_mic.wav").exists()
    assert not (tmp_path / "s_mic.wav.part").exists(), "временный файл остался"
    assert not pcm.exists(), "исходник не убран после успешной конвертации"


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
