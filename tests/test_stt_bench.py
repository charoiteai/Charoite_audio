"""Бенч распознавания обязан падать громко, а не мерить тишину.

20.08: голос «Eddy» числится в системе, но не скачан — `say` отрабатывает с
кодом 0 и пишет файл из одного заголовка. Скрипт брал первый голос локали
(им и оказался Eddy), три фразы из четырёх пропускались, четвёртая давала
CER 1.0 — и «бенч» показывал ничью двух движков на пустом месте.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import stt_bench  # noqa: E402


def test_молчащий_голос_не_выбирается(monkeypatch, tmp_path):
    """Голос, чей синтез даёт один заголовок, не годится — берём следующий."""
    monkeypatch.setattr(stt_bench, "voices_for", lambda lang: ["Немой", "Живой"])
    stt_bench.voice_for.cache_clear()
    monkeypatch.setattr(stt_bench.tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_say(cmd, **kw):
        out = pathlib.Path(cmd[cmd.index("-o") + 1])
        out.write_bytes(b"x" * (100 if cmd[2] == "Немой" else 50_000))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(stt_bench.subprocess, "run", fake_say)

    assert stt_bench.voice_for("zh") == "Живой"
    stt_bench.voice_for.cache_clear()


def test_когда_все_молчат_возвращаем_ничего(monkeypatch, tmp_path):
    """Молчание всех голосов — не повод мерить пустоту: пусть скажет прямо."""
    monkeypatch.setattr(stt_bench, "voices_for", lambda lang: ["Немой"])
    stt_bench.voice_for.cache_clear()
    monkeypatch.setattr(stt_bench.tempfile, "gettempdir", lambda: str(tmp_path))

    def silent(cmd, **kw):
        pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x" * 100)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(stt_bench.subprocess, "run", silent)

    assert stt_bench.voice_for("zh") is None
    stt_bench.voice_for.cache_clear()


def test_cer_считает_замены_и_пропуски():
    assert stt_bench.cer("认证通过", "认证通过") == 0.0
    assert stt_bench.cer("认证通过", "") == 1.0
    assert 0 < stt_bench.cer("下周一上午10点", "下周以上50点") < 1
