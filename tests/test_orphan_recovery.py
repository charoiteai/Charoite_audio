"""Восстановление оборванной встречи и ретеншн не должны воевать.

Две находки аудита 0.46.0, у которых одна цена — безвозвратно потерянная
запись, и один корень: обещание в комментарии, которое код не выполняет.

**P0-1, гонка.** В `main` написано «добиваем ДО чистки — иначе ретеншн удалит
единственную запись раньше, чем кто-то её пересоберёт». Но `_recover_orphans`
только спавнит `Popen`, а `prune_recordings` идёт следом синхронно в том же
потоке: .pcm старше `record_keep_days` исчезал за миллисекунды, пока потомок
ещё грузил интерпретатор. Гонка была не вероятностной, а проигранной заранее —
пересборка по конструкции не трогает .pcm, пока жив лок демона, то есть честно
ждёт свои 45 секунд. Сценарий: авария в пятницу, старт в понедельник.

**P0-2, спавн мимо кода.** Путь пересборки строился от корня ДАННЫХ
(`ROOT / "src" / …`). В репозитории оба корня совпадают, и дефект не виден;
во вложенной установке `src/` в папке данных нет — `Popen` поднимается без
исключения, потомок умирает с кодом 2 в DEVNULL, `except` не срабатывает,
встреча вечно висит в «recovering», а через двое суток ретеншн добивает звук.

Тесты берут оба конца у продуктового кода: путь спрашиваем у самого спавна
(перехватом argv), а существование проверяем на диске.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CFG = {
    "log": {"transcripts_dir": "transcripts", "recordings_dir": "recordings"},
    "audio": {"record_keep_days": 2},
}


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Папка ДАННЫХ отдельно от кода — как во вложенной установке."""
    import daemon

    (tmp_path / "recordings").mkdir()
    (tmp_path / "transcripts").mkdir()
    monkeypatch.setattr(daemon, "ROOT", tmp_path)
    monkeypatch.setattr(daemon, "emit", lambda *_a, **_k: None)
    monkeypatch.setattr(daemon, "_prune_graph_logs", lambda *_a, **_k: None)
    return tmp_path


def _orphan(root: pathlib.Path, stamp: str, age_days: float) -> pathlib.Path:
    """Оборванная встреча: сырой .pcm и живой черновик, финала нет."""
    pcm = root / "recordings" / f"{stamp}_mic.pcm"
    pcm.write_bytes(b"\0" * 4096)
    old = time.time() - age_days * 86400
    os.utime(pcm, (old, old))
    (root / "transcripts" / f"{stamp}.md").write_text("живой черновик", encoding="utf-8")
    return pcm


def test_ретеншн_не_съедает_запись_которую_сейчас_восстанавливают(data_root, monkeypatch):
    """Тот самый понедельник: авария была в пятницу, записи старше двух суток."""
    import audio
    import daemon

    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: None)
    pcm = _orphan(data_root, "2026-08-07_181500", age_days=3)

    protect = daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")
    held = audio.AudioHub.prune_recordings(data_root / "recordings", 2, protect=protect)

    assert pcm.exists(), (
        "ретеншн удалил запись встречи, которую сам же объявил восстанавливаемой: "
        "финальной стенограммы не будет никогда, восстанавливать больше не из чего")
    assert held == 1, "задержку сверх обещанного срока обязаны считать и показывать"


def test_ретеншн_чистит_всё_остальное_как_обещано(data_root):
    """Защита адресная. Записи без ожидающей пересборки уходят по сроку —
    иначе мы молча нарушили бы обещание PRIVACY об удалении через N дней."""
    import audio

    rec = data_root / "recordings"
    for name in ("2026-01-01_120000_mic.pcm", "2026-01-01_120000_mic.wav.part"):
        p = rec / name
        p.write_bytes(b"\0" * 16)
        old = time.time() - 30 * 86400
        os.utime(p, (old, old))

    held = audio.AudioHub.prune_recordings(rec, 2, protect={"2026-08-07_181500"})

    assert held == 0
    assert not list(rec.iterdir()), (
        "старые записи остались лежать дольше обещанного, включая .wav.part, "
        "который раньше не сметал никто")


def test_восстановление_запускает_существующий_файл(data_root, monkeypatch):
    """P0-2 в лоб: путь берём у самого спавна, существование — на диске.

    `data_root` уводит ДАННЫЕ в tmp, код остаётся на месте — ровно то, что
    делает вложенная установка. Если путь снова поедет от корня данных,
    здесь будет несуществующий файл, а в бою — тихо умерший потомок.
    """
    import daemon

    argv: list[list[str]] = []
    monkeypatch.setattr(daemon.subprocess, "Popen",
                        lambda cmd, **k: argv.append([str(x) for x in cmd]))
    _orphan(data_root, "2026-08-07_181500", age_days=0.1)

    daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert argv, "оборванная встреча не запустила пересборку вовсе"
    scripts = [pathlib.Path(a) for a in argv[0] if a.endswith(".py")]
    assert scripts, f"в команде пересборки нет питон-файла: {argv[0]}"
    for path in scripts:
        assert path.exists(), (
            f"пересборка запускает несуществующий {path}: во вложенной установке "
            "потомок умрёт молча в DEVNULL, встреча навсегда останется "
            "«recovering», а ретеншн через двое суток удалит её запись")


def test_main_передаёт_чистке_защищённые_штампы():
    """Сторож проводки, а не текста.

    Обе функции можно починить и забыть связать — тогда тесты выше останутся
    зелёными, а продукт продолжит терять записи. Поэтому смотрим на сам вызов
    `prune_recordings` в `daemon.main`: у него обязан быть аргумент `protect`.
    """
    tree = ast.parse((ROOT / "src" / "daemon.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "prune_recordings"]
    assert calls, "main больше не чистит записи — ретеншн PRIVACY не выполняется"
    for call in calls:
        assert any(kw.arg == "protect" for kw in call.keywords), (
            f"строка {call.lineno}: чистка вызвана без protect — она снова "
            "может удалить запись встречи, которая в этот момент пересобирается")
