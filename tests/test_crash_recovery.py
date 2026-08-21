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
import time

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def rec_dir(tmp_path):
    d = tmp_path / "recordings"
    d.mkdir()
    return d


def test_does_not_touch_pcm_while_daemon_converts(rec_dir, monkeypatch):
    """Пока ЖИВОЙ демон пишет .wav.part, конвертировать чужой .pcm нельзя.

    Слово «живой» тут появилось не для красоты. Раньше тест не задавал
    состояние демона вовсе и проходил на том, что старое правило смотрело
    только на наличие файла. Правило заменено (см. соседний тест про
    осиротевший .part), поэтому предпосылку сценария теперь задаём явно —
    иначе тест проверял бы не то, что написано в его названии.
    """
    import rebuild_transcript as rt

    monkeypatch.setattr(rt, "WAIT_WAV_S", 2)
    monkeypatch.setattr(rt, "_daemon_alive", lambda: True)
    pcm = rec_dir / "s_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    # Ключ сценария: mtime замер в момент stop(), потому что демон уже читает
    # этот файл и больше в него не пишет. Именно так выглядит длинная встреча,
    # у которой конвертация идёт дольше десяти секунд, — и именно на этом
    # старое правило «mtime старше 10 секунд = брошен» ошибалось.
    old = pcm.stat().st_mtime - 600
    os.utime(pcm, (old, old))
    part = rec_dir / "s_mic.wav.part"
    part.write_bytes(b"")                           # демон сейчас пишет

    out = rt.wait_recording(rec_dir, "s", "mic", 16000)

    assert out is None, "полез в чужую конвертацию"
    assert pcm.exists(), "исходник удалён вторым писателем"
    assert part.exists(), "убрали .part из-под живого демона"


def test_осиротевший_part_не_держит_канал_вечно(rec_dir, monkeypatch):
    """Демон убит посреди финализации: .part — огрызок, а не работа.

    Стоимость старого поведения: `.part` не удалял никто (чистка сметала
    только .pcm и .wav), а пересборка из-за него навсегда отказывалась
    трогать целый .pcm. Финальная стенограмма звонка собиралась из одного
    микрофона — реплики собеседников исчезали из стенограммы, минуток и
    саммари, — а через record_keep_days ретеншн добивал и .pcm.
    """
    import rebuild_transcript as rt

    # Окно намеренно длинное: ждать его целиком при мёртвом демоне не только
    # бесполезно, но и дорого — два канала по 45 секунд на каждой пересборке.
    monkeypatch.setattr(rt, "WAIT_WAV_S", 8)
    monkeypatch.setattr(rt, "_daemon_alive", lambda: False)
    pcm = rec_dir / "s_blackhole.pcm"
    pcm.write_bytes(b"\0" * 4096)
    part = rec_dir / "s_blackhole.wav.part"
    part.write_bytes(b"")                           # остался от убитого демона

    started = time.monotonic()
    out = rt.wait_recording(rec_dir, "s", "blackhole", 16000)
    spent = time.monotonic() - started

    assert out is not None and out.suffix == ".wav", (
        "канал собеседника потерян: осиротевший .part заблокировал целый .pcm")
    assert not part.exists(), "осиротевший .part остался копиться в recordings/"
    assert spent < 4, (
        f"ждали {spent:.1f} с работы демона, которого нет: решение принимается "
        "по локу, а не по наличию файла")


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
    # в бою лок ставит конструктор; _finalize_recordings забирает _sinks
    # под ним (круг 3, GLM) — стаб обязан повторять боевые поля
    hub._lock = __import__("threading").Lock()
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
