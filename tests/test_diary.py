"""Дневник: раскладка файла-дня, дозапись секций, ссылка на встречу.

Ollama в CI нет — постобработка честно падает в фолбэк (текст как есть),
что и делает тест детерминированным: проверяется раскладка, не LLM.
"""
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_diary(text: str, env_extra: dict) -> subprocess.CompletedProcess:
    env = os.environ | env_extra
    return subprocess.run(
        [sys.executable, str(ROOT / "src" / "dictate_note.py"), "--diary", "--text"],
        input=text, capture_output=True, text=True, env=env, timeout=180,
    )


def test_diary_day_file_and_sections(tmp_path):
    diary = tmp_path / "Дневник"
    tr = tmp_path / "transcripts"
    tr.mkdir()
    today = dt.date.today().isoformat()
    (tr / f"{today}_1000.md").write_text(
        "# Встреча " + today + "_1000 — Обсуждение запуска\nтело\n", encoding="utf-8")

    env = {"SUFLER_DIARY_DIR": str(diary), "SUFLER_TRANSCRIPTS_DIR": str(tr)}
    r1 = run_diary("первая мысль про наш запуск", env)
    assert r1.returncode == 0, r1.stderr
    day = diary / f"{today}.md"
    assert day.exists(), "файл дня не создан"
    text = day.read_text(encoding="utf-8")
    assert "type: diary" in text and f"# Дневник {today}" in text
    assert text.count("## ") == 1, "должна быть ровно одна секция времени"
    assert "Как сказано: первая мысль" in text

    r2 = run_diary("вторая мысль вечером", env)
    assert r2.returncode == 0, r2.stderr
    text = day.read_text(encoding="utf-8")
    assert text.count("## ") == 2, "вторая сессия должна дозаписаться секцией"
    assert text.count("# Дневник") == 1, "шапка дня не должна дублироваться"


def test_diary_without_transcripts_dir(tmp_path):
    env = {"SUFLER_DIARY_DIR": str(tmp_path / "Д"),
           "SUFLER_TRANSCRIPTS_DIR": str(tmp_path / "нет-такой")}
    r = run_diary("мысль без единой встречи сегодня", env)
    assert r.returncode == 0, r.stderr
    day = next((tmp_path / "Д").glob("*.md"))
    assert "Контекст:" not in day.read_text(encoding="utf-8")


def test_diary_does_not_touch_the_audio_stack():
    """Дневнику с текстом микрофон не нужен — и он за него не платит.

    PortAudio на машине без звуковых устройств роняет процесс при ВЫХОДЕ
    («terminate called without an active exception», код -6): работа сделана,
    ответ напечатан, а returncode -6. В CI это выглядело как регресс кода и
    дважды съело время на разбор (№122).
    """
    src = (ROOT / "src" / "dictate_note.py").read_text(encoding="utf-8")
    head = src[:src.index("def ")]
    assert "import sounddevice" not in head, (
        "sounddevice импортируется на уровне модуля — дневник снова платит "
        "за аудио-стек, которым не пользуется"
    )
    assert "import sounddevice" in src, "ленивый импорт потерялся вовсе"
