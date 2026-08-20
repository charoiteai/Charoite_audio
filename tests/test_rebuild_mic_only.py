"""Пересборка на машине без системного звука не должна падать.

20.08, ревью GLM: `bh_segs` объявлялась ВНУТРИ ветки «есть blackhole», а
читалась ниже безусловно — `call=bool(bh_segs)`. На свежей установке без
BlackHole (или пока не выдано разрешение на системный звук) ветка не
выполняется, и пересборка падала с NameError на КАЖДОЙ встрече. Падение
глотает `main()` строкой «пересборка не удалась — граф по живой версии»:
человек получает встречу без разбора по голосам, без распознавания по
абзацам и без имён, а через record_keep_days запись удаляется и вернуть
качество уже нечем.
"""
import pathlib
import sys

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rebuild_transcript as rt  # noqa: E402


@pytest.fixture
def mic_only(tmp_path, monkeypatch):
    """Встреча, записанная одним микрофоном: системного канала нет вовсе."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "recordings").mkdir()
    monkeypatch.setattr(rt, "ROOT", tmp_path)

    live = tmp_path / "transcripts" / "2026-08-20_143000.md"
    live.write_text("живая стенограмма", encoding="utf-8")

    mic = tmp_path / "recordings" / "2026-08-20_143000_mic.wav"
    mic.write_bytes(b"")

    monkeypatch.setattr(rt, "wait_recording",
                        lambda rec, stamp, label, sr: mic if label == "mic" else None)
    monkeypatch.setattr(rt, "load_wav", lambda p: (np.zeros(16000 * 60, dtype=np.float32), 16000))
    monkeypatch.setattr(rt, "diarize_channel",
                        lambda *a, **k: [(0.0, 20.0, 0), (25.0, 45.0, 0)])
    return live


def test_mic_only_reaches_owner_decision(mic_only, monkeypatch):
    """Дошли до решения о владельце — значит NameError больше нет."""
    seen: dict = {}
    real_heard = rt.owner_voice_rules.Heard

    def spy(**kw):
        seen.update(kw)
        return real_heard(**kw)

    monkeypatch.setattr(rt.owner_voice_rules, "Heard", spy)
    monkeypatch.setattr(rt, "STT", lambda cfg: object())
    monkeypatch.setattr(rt, "stt_segment", lambda *a: "реплика")
    monkeypatch.setattr(rt, "name_speakers", lambda cfg, lines: ({}, False))

    cfg = {"audio": {"samplerate": 16000}, "sufler": {"user_name": "Игорь Ветров"}}
    try:
        rt.rebuild(mic_only, cfg)
    except NameError as e:                       # pragma: no cover — это и есть регресс
        pytest.fail(f"пересборка упала без системного канала: {e}")

    assert seen, "до решения о владельце не дошли — пересборка оборвалась раньше"
    assert seen.get("call") is False, (
        "без системного канала звонка нет: call обязан быть False")
