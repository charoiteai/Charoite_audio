"""Ночной цикл не должен делить машину с разбором встречи.

12.08 они совпали. Транскрипция, ревизия ядер и сборка досье одновременно на
64 ГБ: свободно 14 ГБ при 17 ГБ уже в компрессоре. Локальный сервер начал
выгружать и грузить модели по кругу — 41 раз за прогон, — запросы стали
висеть по 2-6 минут, а потом он лёг: 258 тем ушли без разбора.

Здесь проверяется само правило ожидания, без сна и без реального сервера.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import wait_for_idle  # noqa: E402


class FakeStore:
    """Занятость по сценарию: список ответов, по одному на опрос."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = 0

    def busy(self):
        self.asked += 1
        return self.answers.pop(0) if self.answers else []


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_free_machine_is_not_waited_for():
    c = Clock()
    store = FakeStore([[]])
    assert wait_for_idle.wait(store, timeout=3600, poll=60,
                              sleep=c.sleep, now=c.now) == []
    assert c.slept == [], "ждать нечего — а мы поспали"


def test_waits_until_processing_ends():
    c = Clock()
    store = FakeStore([["rebuilding_transcript"], ["updating_graph"], []])
    assert wait_for_idle.wait(store, timeout=3600, poll=60,
                              sleep=c.sleep, now=c.now) == []
    assert c.slept == [60, 60]


def test_gives_up_after_the_deadline():
    """Пропустить ночь целиком хуже, чем поработать в тесноте: досье и бриф
    не соберутся вовсе, и утром человек останется без контекста дня."""
    c = Clock()
    store = FakeStore([["rebuilding_transcript"]] * 100)
    left = wait_for_idle.wait(store, timeout=120, poll=60,
                              sleep=c.sleep, now=c.now)
    assert left == ["rebuilding_transcript"]
    assert sum(c.slept) <= 120, f"ждали дольше срока: {c.slept}"


def test_last_sleep_does_not_overshoot():
    """Час ожидания должен быть часом, а не часом с четвертью."""
    c = Clock()
    store = FakeStore([["updating_graph"]] * 100)
    wait_for_idle.wait(store, timeout=90, poll=60, sleep=c.sleep, now=c.now)
    assert sum(c.slept) == 90, c.slept


def test_stale_processing_is_not_busy(tmp_path):
    """Процесс умер, не дописав итог. Считать это занятостью — значит
    отменять ночи навсегда из-за одной мёртвой записи."""
    from meeting_processing import STALE_PROCESSING, MeetingStatusStore

    now = [1_000_000.0]
    store = MeetingStatusStore(tmp_path, now=lambda: now[0])
    transcript = tmp_path / "2026-08-12_0400.md"
    transcript.write_text("текст", encoding="utf-8")
    store.processing(transcript, "rebuilding_transcript")

    assert store.busy() != [], "свежий разбор обязан считаться занятостью"
    now[0] += STALE_PROCESSING + 1
    assert store.busy() == []


def test_nightly_waits_before_working():
    """Сторож проводки: ожидание должно стоять в самом скрипте и до шагов —
    иначе оно есть, но ничего не решает."""
    text = (REPO / "scripts" / "nightly.sh").read_text(encoding="utf-8")
    assert "wait_for_idle.py" in text, "ночной цикл больше никого не ждёт"
    assert text.index("wait_for_idle.py") < text.index("tier3_cores.py"), \
        "ожидание после первого тяжёлого шага бессмысленно"


def test_nightly_keeps_one_model_in_memory(monkeypatch):
    """Ночью большая и маленькая модели чередовались, и на занятой памяти
    сервер выгружал одну ради другой: 41 загрузка за прогон."""
    import llm as llm_mod

    cfg = {
        "llm": {"model": "big", "small_model": "small", "fallback_model": "tiny"},
        "sufler": {"role": "роль"},
    }
    monkeypatch.delenv("CHAROITE_ONE_MODEL", raising=False)
    day = llm_mod.LLM(cfg)
    assert (day.small, day.fallback) == ("small", "tiny"), \
        "днём мелкие задачи обязаны идти на маленькую модель"

    monkeypatch.setenv("CHAROITE_ONE_MODEL", "1")
    night = llm_mod.LLM(cfg)
    assert (night.small, night.fallback) == ("big", "big")


def test_nightly_script_asks_for_one_model():
    text = (REPO / "scripts" / "nightly.sh").read_text(encoding="utf-8")
    assert "CHAROITE_ONE_MODEL" in text, \
        "ночной цикл снова гоняет три модели по кругу"


def test_live_recording_counts_as_busy(monkeypatch, tmp_path):
    """18.08: суфлёр слушал, а ночь/пересборка держали модель — подсказки
    падали. Живая запись (лок демона) — такая же занятость, как разбор."""
    c = Clock()
    store = FakeStore([[], [], []])
    answers = iter([True, True, False])
    monkeypatch.setattr(wait_for_idle.live_gate, "daemon_alive", lambda root: next(answers))
    assert wait_for_idle.wait(store, timeout=3600, poll=60,
                              sleep=c.sleep, now=c.now, root=tmp_path) == []
    assert c.slept == [60, 60]


def test_live_recording_is_named_when_not_waited_out(monkeypatch, tmp_path):
    c = Clock()
    store = FakeStore([["updating_graph"]] * 5)
    monkeypatch.setattr(wait_for_idle.live_gate, "daemon_alive", lambda root: True)
    left = wait_for_idle.wait(store, timeout=120, poll=60,
                              sleep=c.sleep, now=c.now, root=tmp_path)
    assert left == ["updating_graph", wait_for_idle.LIVE]
