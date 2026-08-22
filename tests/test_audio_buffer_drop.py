"""Буфер живого распознавания не должен молча съедать полминуты речи.

20.08, ревью DeepSeek: при переполнении буфер урезался до ПОЛОВИНЫ потолка —
`buf = buf[-(cap // 2):]`. Один медленный чанк (а классификатор спорных
вопросов тогда ходил к модели прямо в горячем цикле) выбрасывал ~30 секунд чужой
речи. Ни лога, ни статуса: на диск звук пишется отдельным sink, поэтому
офлайн-пересборка его возвращала, а живая лента шла кусками — жалоба
«переводит кусками, не всю речь в онлайне».
"""
import io
import pathlib
import sys
import threading
import time

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import audio  # noqa: E402

SR = 16000


def Hub(recording: bool = True, chunk_s: float = 3.0, overlap_s: float = 0.5):
    """Настоящий AudioHub без __init__: устройства и потоки этой логике не
    нужны, а методы берём те же, что работают на встрече."""
    hub = audio.AudioHub.__new__(audio.AudioHub)
    hub.sr = SR
    hub._bufs = {"mic": np.zeros(0, dtype=np.float32)}
    hub._drops = {}
    hub._sys_speech_until = 0.0
    hub.on_frame = None
    hub._last_frame = {}
    hub._watch_streams = lambda: None
    # Настоящий file-like sink: `_pump` пишет и flush'ит его ровно как .pcm.
    # Голый object раньше был достаточен, пока тесты не проверяли немедленную
    # видимость смерти записи, но реальным состоянием runtime он не является.
    hub._sinks = {"mic": io.BytesIO()} if recording else {}
    hub.record_on = recording
    hub.chunk_s = chunk_s
    hub.overlap_s = overlap_s
    hub._running = True
    hub.captures = []
    hub._lock = threading.Lock()
    hub.said = []
    hub.on_status = hub.said.append
    return hub


@pytest.fixture
def clock(monkeypatch):
    """Часы под контролем: иначе тест окна отчёта зависит от того, успела ли
    машина прокрутить цикл за 30 секунд (флейк на занятом раннере)."""
    now = [1000.0]
    monkeypatch.setattr(audio.time, "time", lambda: now[0])
    return now


def _sec(n: float) -> np.ndarray:
    return np.ones(int(SR * n), dtype=np.float32)


def test_буфер_не_переполняется():
    hub = Hub()
    for _ in range(200):                      # 200 секунд при потолке 60
        hub._append("mic", _sec(1.0))

    assert len(hub._bufs["mic"]) <= SR * hub.BUF_CAP_S


def test_health_snapshot_measures_backlog_and_freshest_input_without_consuming():
    hub = Hub()
    hub._bufs["blackhole"] = _sec(7.0)
    hub._last_frame = {"mic": 99.0, "blackhole": 91.0}
    hub._sinks["blackhole"] = object()
    before = {label: buf.copy() for label, buf in hub._bufs.items()}

    health = hub.health_snapshot(now=100.0)

    assert health["backlog_seconds"] == pytest.approx(7.0)
    assert health["input_age_seconds"] == pytest.approx(1.0), (
        "пока хотя бы один канал приносит кадры, общий аудиовход жив")
    assert health["channels"]["blackhole"]["input_age_seconds"] == pytest.approx(9.0)
    assert health["recording_ok"] is True
    for label, buf in before.items():
        assert np.array_equal(hub._bufs[label], buf), "сенсор не вправе потреблять звук"


def test_health_snapshot_reports_missing_recording_sink():
    hub = Hub(recording=True)
    hub._sinks = {}

    assert hub.health_snapshot(now=100.0)["recording_ok"] is False


def test_sink_failure_is_reported_before_buffer_overflow(capsys):
    """Смерть .pcm раньше была видна только через минуту, когда переполнялся
    STT-буфер. Сенсор записи обязан сказать сразу на первом failed write."""
    import queue as _q

    class BrokenSink:
        def write(self, _data):
            raise OSError("disk full")

        def flush(self):
            raise AssertionError("flush after failed write")

    class Cap:
        label = "mic"

        def __init__(self):
            self.q = _q.Queue()

    hub = Hub(recording=True)
    cap = Cap()
    cap.q.put(_sec(0.25))
    hub.captures = [cap]
    hub._sinks = {"mic": BrokenSink()}
    worker = threading.Thread(target=hub._pump, daemon=True)
    worker.start()
    for _ in range(100):
        if hub.said:
            break
        time.sleep(0.01)
    hub._running = False
    worker.join(timeout=2)

    assert hub.said, "ошибка страховочной записи осталась невидимой"
    assert "disk full" in hub.said[0]
    assert "не восстановить" in hub.said[0]
    assert "mic" not in hub._sinks
    assert "disk full" in capsys.readouterr().err, "цифры/причина нужны и в логе"


def test_теряется_ровно_излишек_а_не_полминуты():
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))   # буфер полон ровно по потолок

    dropped = hub._append("mic", _sec(2.0))

    assert dropped == pytest.approx(2.0), (
        f"выброшено {dropped:.1f}с вместо 2с — вернулся сброс половины буфера")
    assert len(hub._bufs["mic"]) == SR * hub.BUF_CAP_S


def test_пока_есть_место_ничего_не_теряется():
    hub = Hub()
    assert hub._append("mic", _sec(59.0)) == 0.0
    assert hub._append("mic", _sec(1.0)) == 0.0


def test_потеря_звука_не_остаётся_молчаливой():
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(5.0)))

    assert hub.said, "человек обязан узнать, что живой звук потерян"
    assert "5" in hub.said[0], f"в статусе нет объёма потери: {hub.said[0]}"


def test_статус_не_спамит_на_каждом_чанке(clock):
    """Отставание длится минутами — строка в ленте нужна одна, не сотня."""
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    for _ in range(50):
        clock[0] += 0.5                      # полминуты окна не набирается
        hub._note_drop("mic", hub._append("mic", _sec(0.5)))

    assert len(hub.said) == 1, f"статусов {len(hub.said)} вместо одного"
    assert hub._drops["mic"][2] == pytest.approx(25.0), "итог потерь врёт"
    assert hub._drops["mic"][0] > 0, "интервальный счётчик обнулился не вовремя"


def test_кусок_длиннее_потолка_не_раздувает_буфер():
    """Аномально длинный кусок обязан обрезаться сам, а не оставлять буфер
    выше лимита с недосчитанной потерей (ревью 20.08: локальная и DeepSeek)."""
    hub = Hub()
    dropped = hub._append("mic", _sec(hub.BUF_CAP_S + 10))

    assert len(hub._bufs["mic"]) == SR * hub.BUF_CAP_S
    assert dropped == pytest.approx(10.0), f"потеря посчитана как {dropped:.1f}с"


def test_статус_говорит_свежую_потерю_а_не_сумму_с_начала(clock):
    """Копящаяся сумма не даёт понять, отстаём ли ПРЯМО сейчас."""
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(5.0)))
    clock[0] += hub._DROP_REPORT_S + 1            # окно отчёта прошло
    hub._note_drop("mic", hub._append("mic", _sec(3.0)))

    assert len(hub.said) == 2
    assert "3с" in hub.said[1], f"вторая строка врёт про свежую потерю: {hub.said[1]}"
    assert "8с" in hub.said[1], f"итог за встречу потерян: {hub.said[1]}"
    assert hub._drops["mic"][0] == pytest.approx(0.0), "интервал не сброшен"


def test_без_записи_на_диск_статус_не_утешает():
    """`record: false`, отказ открытия файла, смерть sink на полном диске —
    во всех трёх случаях выброшенный звук не вернуть ничем. Обещать человеку
    полную стенограмму в этот момент хуже, чем молчать (ревью 20.08, GLM)."""
    hub = Hub(recording=False)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(4.0)), written=False)

    assert hub.said, "потеря звука обязана быть озвучена и без записи"
    assert "не вернуть" in hub.said[0], f"статус утешает впустую: {hub.said[0]}"
    assert "стенограмма будет полной" not in hub.said[0]


def test_с_записью_на_диск_статус_успокаивает():
    hub = Hub(recording=True)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(4.0)))

    assert "стенограмма будет полной" in hub.said[0]


def test_потребитель_читает_буфер_без_дыр_и_перестановок():
    """Единственная связка, которую не проверял никто: `_append` дописывает
    в хвост, `_cut` режет с головы. Регрессия вроде «дописывать в голову»
    или «резать с хвоста» прошла бы мимо обоих наборов тестов, а на встрече
    дала бы перепутанные во времени реплики (ревью 20.08, DeepSeek)."""
    hub = Hub()
    # Пронумерованные сэмплы: по значению видно, какой кусок записи прочитан.
    hub._append("mic", np.arange(SR * 10, dtype=np.float32))

    first = hub._cut("mic")
    second = hub._cut("mic")

    assert first is not None and second is not None
    need = int(SR * hub.chunk_s)
    keep = int(SR * hub.overlap_s)
    assert first[0] == 0, "первый чанк обязан начинаться с головы буфера"
    assert np.all(np.diff(first) == 1), "порядок сэмплов внутри чанка нарушен"
    # Хвост первого чанка обязан повториться в начале второго — это перехлёст,
    # на нём держится склейка слов между чанками.
    assert np.array_equal(first[need - keep:], second[:keep]), "перехлёст разъехался"
    assert second[0] == need - keep, "между чанками дыра в записи"


def test_вытеснение_не_рвёт_порядок_для_потребителя():
    """После переполнения потребитель читает непрерывный кусок — пусть и без
    самого старого звука."""
    hub = Hub()
    hub._append("mic", np.arange(SR * hub.BUF_CAP_S, dtype=np.float32))
    dropped = hub._append("mic", np.arange(SR * hub.BUF_CAP_S,
                                          SR * (hub.BUF_CAP_S + 5), dtype=np.float32))

    assert dropped == pytest.approx(5.0)
    chunk = hub._cut("mic")
    assert chunk is not None
    assert chunk[0] == SR * 5, "голова буфера не совпала с границей вытеснения"
    assert np.all(np.diff(chunk) == 1), "вытеснение разорвало порядок сэмплов"


def test_четверть_секунды_не_повод_для_тревоги():
    """Буфер дозревает кратно куску захвата, поэтому первое переполнение —
    доли секунды. Строка «потеряно 0с», а тем более «звук не вернуть» из-за
    неё — шум, который учит не читать предупреждения (ревью 20.08, GLM)."""
    hub = Hub(recording=False)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(0.25)), written=False)

    assert not hub.said, f"тревога из-за четверти секунды: {hub.said}"
    assert hub._drops["mic"][0] == pytest.approx(0.25), "потеря должна копиться"


def test_потери_копятся_до_заметного_и_тогда_сообщаются():
    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    said_after = []
    for i in range(8):
        hub._note_drop("mic", hub._append("mic", _sec(0.25)))
        if i < 3:                         # накоплено меньше секунды
            said_after.append(len(hub.said))
    said_after = [max(said_after)] if said_after else [0]

    # Отчёт уходит, как только накопилась секунда, — остальное копится дальше
    # и попадёт в следующее окно или в досказ на стопе. Без порога первая же
    # четверть секунды дала бы строку СРАЗУ: проверяем, что до неё молчали.
    assert len(hub.said) == 1, f"ожидали одну строку, получили {hub.said}"
    assert "до 1с" in hub.said[0], hub.said[0]
    assert said_after[0] == 0, "строка ушла раньше, чем накопилась секунда"
    assert hub._drops["mic"][2] == pytest.approx(2.0), "итог за встречу недосчитан"


def test_хвост_потерь_доскажут_на_стопе(monkeypatch):
    """Окно отчёта — полминуты; всё, что накопилось после последней строки,
    иначе исчезает вместе со встречей."""
    hub = Hub()
    monkeypatch.setattr(hub, "_finalize_recordings", lambda: None)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(4.0)))
    hub.said.clear()
    hub._note_drop("mic", hub._append("mic", _sec(6.0)))   # в окно не попало

    hub.stop()

    assert hub.said, "хвост потерь исчез вместе со встречей"
    assert "6с" in hub.said[0], hub.said[0]


def test_записанный_кусок_не_пугает_даже_после_финализации():
    """`_finalize_recordings` обнуляет sink'и, но кусок, который УЖЕ записан,
    в финальной стенограмме будет — пугать им нельзя.

    Первая попытка закрыть эту ложную тревогу гардом по `_running` вышла
    хуже болезни: она молчала и в обратном случае — когда `_pump` домолол
    кусок ПОСЛЕ обнуления sink'ов, то есть на диск он не попал, а человек
    читал «стенограмма будет полной» (ревью 20.08, круг 3, DeepSeek).
    Поэтому судим по факту записи этого куска, а не по состоянию словаря.
    """
    hub = Hub(recording=True)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._running = False
    hub._sinks = {}                      # так делает _finalize_recordings
    hub._note_drop("mic", hub._append("mic", _sec(3.0)), written=True)

    assert hub.said
    assert "не вернуть" not in hub.said[0], f"ложная тревога: {hub.said[0]}"


def test_незаписанный_хвост_на_стопе_честно_объявлен():
    """Обратная сторона: кусок, домолотый после обнуления sink'ов, на диск не
    попал — молчать о нём нельзя."""
    hub = Hub(recording=True)
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._running = False
    hub._sinks = {}
    hub._note_drop("mic", hub._append("mic", _sec(3.0)), written=False)

    assert hub.said
    assert "не вернуть" in hub.said[0], f"потеря выдана за сохранённую: {hub.said[0]}"


def test_хвост_после_остановки_договаривает_сам_поток():
    """Досказ в `stop()` не покрывает то, что поток захвата домолол уже
    ПОСЛЕ него: окно отчёта — полминуты, и такая потеря не звучала никогда.
    Теперь досказ есть и в конце `_pump` (ревью 20.08, круг 4).

    Ключ теста — окно: обычный отчёт обязан ПРОМОЛЧАТЬ про хвост, иначе
    проверяли бы не досказ, а рядовое сообщение.
    """
    import queue as _q
    import threading as _th

    hub = Hub()
    hub._append("mic", _sec(hub.BUF_CAP_S))
    hub._note_drop("mic", hub._append("mic", _sec(3.0)))   # первый отчёт
    assert len(hub.said) == 1
    hub.said.clear()

    class Cap:
        label = "mic"

        def __init__(self):
            self.q = _q.Queue()

    cap = Cap()
    cap.q.put(_sec(4.0))          # хвост: попадёт в окно отчёта и промолчит
    hub.captures = [cap]
    hub._running = True

    t = _th.Thread(target=hub._pump, daemon=True)
    t.start()
    for _ in range(300):
        if hub._drops["mic"][0] >= 4.0:
            break
        time.sleep(0.01)
    assert not hub.said, "хвост попал в окно — рядовой отчёт обязан молчать"

    hub._running = False          # так делает stop()
    t.join(timeout=3)

    assert not t.is_alive(), "поток захвата не завершился"
    assert hub.said, "хвост, домолотый после остановки, не озвучен"
    assert len(hub.said) == 1, f"досказ продублировался: {hub.said}"
    assert "4с" in hub.said[0], hub.said[0]

    hub._say_last_drops()         # повторный вызов из stop()
    assert len(hub.said) == 1, "второй досказ выдал строку на пустом остатке"


def test_health_snapshot_reports_recording_per_channel():
    """Флаг `channels[].recording` — то, по чему UI рисует «канал не пишется».
    Мутация In→NotIn переворачивала его при зелёном наборе: проверялся
    только общий recording_ok (мутационный прогон 21.08)."""
    hub = Hub()
    # каналы снимка — это буферы: у Hub по умолчанию только mic
    hub._bufs["blackhole"] = _sec(0.5)
    hub._last_frame = {"mic": 99.0, "blackhole": 99.0}
    hub._sinks = {"mic": object()}

    channels = hub.health_snapshot(now=100.0)["channels"]

    assert channels["mic"]["recording"] is True
    assert channels["blackhole"]["recording"] is False
