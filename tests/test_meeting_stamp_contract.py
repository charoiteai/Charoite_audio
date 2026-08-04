"""Договор об имени встречи: демон пишет записи — пересборка их находит.

Договор ломался дважды и оба раза молча. Второй раз (28.07: штамп получил
секунды, а срез `live.stem[:15]` в пересборке остался прежним) стоил проекту
финальной пересборки ВСЕХ встреч за неделю: конвейер писал «записей нет —
оставляю живую стенограмму», в граф уходил черновик из чанков, а через
`record_keep_days` ретеншн удалял целые двухканальные записи.

Ни один тест этого не заметил, хотя `wait_recording` покрыт. Дело в том,
КАК он покрыт: `tests/test_crash_recovery.py` создаёт файл со штампом "s" и
ищет его же по штампу "s". Обе стороны договора собраны из одной выдуманной
константы — тест проверяет логику ожидания и физически не способен увидеть,
что стороны называют файлы по-разному.

Поэтому здесь выдуманных штампов нет: имя стенограммы берётся у
`Transcript`, а имя записи — у той же функции, которой пользуется
`AudioHub`, и отдельный тест следит, чтобы он ею и пользовался.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import meeting_stamp  # noqa: E402


def test_пересборка_находит_запись_которую_назвал_демон(tmp_path, monkeypatch):
    """Сквозной договор. Оба конца — продуктовый код, не константы теста."""
    import rebuild_transcript as rt
    from main import Transcript

    tr = Transcript(tmp_path / "transcripts")     # так называет стенограмму демон
    rec = tmp_path / "recordings"
    rec.mkdir()
    # daemon.py: AudioHub(cfg, stamp=tr.stamp) → запись зовут именем стенограммы
    for label in ("mic", "blackhole"):
        meeting_stamp.recording_path(rec, tr.stamp, label, "wav").write_bytes(b"RIFF")

    monkeypatch.setattr(rt, "WAIT_WAV_S", 1)
    found = rt.wait_recording(rec, tr.path.stem, "mic", 16000)

    assert found is not None, (
        "пересборка не нашла запись собственной встречи: договор об имени "
        "разъехался, финальной стенограммы не будет НИ У ОДНОЙ встречи, а "
        "через record_keep_days записи удалит ретеншн")
    assert found.name.startswith(tr.stamp), \
        f"нашлась чужая запись: {found.name} при штампе {tr.stamp}"


def test_rebuild_не_режет_штамп(tmp_path, monkeypatch):
    """Регрессия 28.07 в лоб: `rebuild` обязан искать по ПОЛНОМУ имени.

    Проверяем не текст функции, а поведение: перехватываем штамп, с которым
    `rebuild` пошёл за записями, и сравниваем с именем стенограммы.
    """
    import rebuild_transcript as rt
    from main import Transcript

    tr = Transcript(tmp_path / "transcripts")
    tr.path.write_text("# Встреча\n", encoding="utf-8")
    asked: list[str] = []
    monkeypatch.setattr(rt, "wait_recording",
                        lambda rec_dir, stamp, label, sr: asked.append(stamp))
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(tmp_path / "recordings"))
    rt.rebuild(tr.path, {"audio": {"samplerate": 16000}})

    assert asked, "rebuild не дошёл до поиска записей — имя встречи не распознано"
    assert asked[0] == tr.stamp, (
        f"rebuild ищет записи по «{asked[0]}», а демон записал их как "
        f"«{tr.stamp}»: файла с таким именем на диске не существует")


def test_демон_называет_записи_общей_функцией():
    """Вторая сторона договора: audio.py не собирает имя сам.

    Структурная проверка здесь уместна ровно потому, что проверяет ВЫЗОВ, а
    не упоминание: собственная f-строка с именем файла — это второе
    представление формата, а именно из-за второго представления всё и
    сломалось. Конструировать AudioHub в тесте нельзя: __init__ идёт в
    sounddevice за устройствами.
    """
    tree = ast.parse((ROOT / "src" / "audio.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "recording_path"]
    assert calls, "audio.py собирает имя записи сам, мимо meeting_stamp.recording_path"

    joined = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
              and any(isinstance(v, ast.Constant) and isinstance(v.value, str)
                      and v.value.endswith((".pcm", ".wav")) for v in n.values)]
    assert not joined, "в audio.py снова появилась своя f-строка с именем записи"


def test_секунды_в_штампе_не_теряются():
    """Ровно та потеря, что случилась 28.07: 17 знаков превратились в 15."""
    stamp = meeting_stamp.now()
    assert len(stamp) == 17, f"штамп встречи потерял секунды: {stamp}"
    assert meeting_stamp.started_at(stamp) is not None


def test_суффикс_коллизии_остаётся_частью_имени(tmp_path):
    """Две встречи в одну секунду дают `..._183145-1.md`, записи — с тем же
    суффиксом; значит и разбирать штамп нужно вместе с ним."""
    from main import Transcript

    d = tmp_path / "transcripts"
    first, second = Transcript(d), Transcript(d)
    assert second.stamp.endswith("-1"), f"нет суффикса коллизии: {second.stamp}"
    assert meeting_stamp.started_at(second.stamp) == meeting_stamp.started_at(first.stamp)


def test_чужое_имя_файла_пересборку_не_запускает():
    """Производное (`_minutes.md`, `_hints.md`) и посторонние файлы — не встречи."""
    for foreign in ("заметки", "2026-07-22_разбор", "readme", ""):
        assert meeting_stamp.started_at(foreign) is None, foreign


def test_штамп_до_28_июля_ещё_читается(tmp_path):
    """Старые встречи пятнадцатизначны — руками пересобрать их можно."""
    assert meeting_stamp.started_at("2026-07-20_1830") is not None
