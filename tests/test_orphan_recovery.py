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

    monkeypatch.setattr(daemon, "_start_orphan_chain", lambda lives: None)
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

    chains: list[list[pathlib.Path]] = []
    monkeypatch.setattr(daemon, "_start_orphan_chain",
                        lambda lives: chains.append(list(lives)))
    _orphan(data_root, "2026-08-07_181500", age_days=0.1)

    daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert chains and chains[0], "оборванная встреча не попала в цепочку пересборки"

    argv: list[list[str]] = []
    monkeypatch.setattr(daemon.subprocess, "run",
                        lambda cmd, **k: argv.append([str(x) for x in cmd]))
    daemon._rebuild_orphans_sequentially(chains[0])

    assert argv, "цепочка не запустила пересборку вовсе"
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


def test_преемник_убирает_осиротевший_part_прежнего_демона(data_root, monkeypatch):
    """Автоперезапуск — а не «демон умер и не поднялся» — главный сценарий краша.

    Watchdog приложения поднимает демона за 2 секунды. Лок переходит к
    преемнику, и для пересборки «демон жив»: осиротевший `.wav.part` прежнего
    демона она уважала бы до таймаута, а затем навсегда отказалась бы трогать
    целый .pcm — канал собеседника терялся, и его же штамп мы сами защищали
    от ретеншна бессрочно. Единственный, кто ЗНАЕТ, что автор `.part` мёртв, —
    преемник: лок в его руках. Значит, убирать огрызок — его работа.
    """
    import daemon

    monkeypatch.setattr(daemon, "_start_orphan_chain", lambda lives: None)
    _orphan(data_root, "2026-08-07_181500", age_days=0.1)
    stale = data_root / "recordings" / "2026-08-07_181500_blackhole.wav.part"
    stale.write_bytes(b"")            # огрызок финализации убитого демона

    protect = daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert "2026-08-07_181500" in protect
    assert not stale.exists(), (
        "осиротевший .part пережил преемника: пересборка увидит «живого» "
        "писателя по нашему локу и навсегда откажется трогать целый .pcm")


def test_цепочка_пересборок_последовательна_и_не_рвётся_на_ошибке(data_root, monkeypatch):
    """Сироты после аварийных выходных пересобираются ПО ОДНОЙ.

    Параллельный залп Popen по всем сиротам — это несколько одновременных
    diarize+STT+LLM: memory-thrash класса 12.08 (модель за прогон грузилась
    41 раз, сервер лёг). Очередь держит одну пересборку за раз — run вместо
    Popen; ошибка запуска одной встречи не хоронит остальные.
    """
    import daemon

    lives = [data_root / "transcripts" / f"2026-08-0{i}_120000.md" for i in (1, 2, 3)]
    for p in lives:
        p.write_text("черновик", encoding="utf-8")

    launched: list[str] = []

    def fake_run(cmd, **kw):
        target = pathlib.Path([a for a in cmd if str(a).endswith(".md")][0]).stem
        if target == "2026-08-02_120000":
            raise OSError("нет интерпретатора")
        launched.append(target)

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    monkeypatch.setattr(
        daemon.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
            "цепочка обязана ЖДАТЬ каждую пересборку (run), а не залпить Popen")))

    daemon._rebuild_orphans_sequentially(lives)

    assert launched == ["2026-08-01_120000", "2026-08-03_120000"], (
        "очередь потеряла встречу после ошибки соседней или перепутала порядок")


def test_ретеншн_не_родня_retry_пересборке():
    """Retry из приложения защищается возрастом файла, а не связью с демоном.

    `protect` знает только штампы со старта демона. Retry по «позавчерашней»
    встрече приходит мимо него — единственная защита от чистки, идущей
    параллельно, — свежий mtime, который пересборка ставит записям на входе.
    Проверяем сам механизм: rebuild трогает mtime до ожидания каналов.
    """
    import ast as _ast
    import pathlib as _pl

    src = (_pl.Path(__file__).resolve().parent.parent / "src"
           / "rebuild_transcript.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    rebuild = next(n for n in tree.body
                   if isinstance(n, _ast.FunctionDef) and n.name == "rebuild")
    calls = [n for n in _ast.walk(rebuild)
             if isinstance(n, _ast.Call)
             and getattr(getattr(n, "func", None), "attr", None) == "utime"]
    assert calls, (
        "rebuild больше не столбит записи свежим mtime: retry по старой "
        "встрече снова проигрывает гонку ретеншну (вход мимо protect)")


def test_сырые_потоки_приложения_живут_по_тому_же_сроку(tmp_path, monkeypatch):
    """`data/sck/*` — тоже записи встречи; `tap_stream.raw` — наследие тапа.

    Системный звук пишет приложение, а не демон, и эти файлы жили ВНЕ
    ретеншна: каталоги сессий убирались лишь при штатном стопе — краш
    оставлял полное аудио навсегда. На рабочей машине так пролежал 61 МБ
    девять дней при обещанных двух (аудит 16.08). PRIVACY.md обещает
    «записи временны». `tap_stream.raw` писали версии с Core Audio tap
    (снят 02.09): новые не пишут, старый файл уходит по тому же сроку.
    """
    import audio

    data = tmp_path / "data"
    (data / "sck" / "старая-сессия").mkdir(parents=True)
    (data / "sck" / "живая-сессия").mkdir(parents=True)
    old_raw = data / "tap_stream.raw"
    old_raw.write_bytes(b"\0" * 16)
    old_session = data / "sck" / "старая-сессия" / "system.raw"
    old_session.write_bytes(b"\0" * 16)
    live_session = data / "sck" / "живая-сессия" / "system.raw"
    live_session.write_bytes(b"\0" * 16)

    week_ago = time.time() - 7 * 86400
    for p in (old_raw, old_session, live_session):
        os.utime(p, (week_ago, week_ago))

    # живая сессия названа в свежем манифесте — её не трогаем даже старой
    monkeypatch.setattr(audio, "fresh_sck_manifest",
                        lambda: {"system": str(live_session)})

    removed = audio.AudioHub.prune_stream_files(data, 2)

    assert removed == 2, "старые сырые потоки остались лежать"
    assert not old_raw.exists(), "tap_stream.raw переживает срок хранения"
    assert not old_session.exists(), "каталог мёртвой сессии не убран"
    assert live_session.exists(), "убита запись ИДУЩЕЙ встречи"


def test_свежие_потоки_ретеншн_не_трогает(tmp_path, monkeypatch):
    import audio

    data = tmp_path / "data"
    (data / "sck" / "вчерашняя").mkdir(parents=True)
    fresh = data / "sck" / "вчерашняя" / "system.raw"
    fresh.write_bytes(b"\0" * 16)
    monkeypatch.setattr(audio, "fresh_sck_manifest", lambda: None)

    assert audio.AudioHub.prune_stream_files(data, 2) == 0
    assert fresh.exists()


def test_пустой_свежий_каталог_сессии_не_удаляется(tmp_path, monkeypatch):
    """Приложение создаёт `sck/<uuid>/` и только потом пишет туда `.raw`;
    prune идёт на старте демона — то есть ровно при старте записи. Пустой
    каталог обходил обе защиты (обе смотрят на файлы, которых ещё нет) и
    удалялся из-под живой сессии (второе мнение по #324, 16.08). Старый
    пустой каталог — мусор, свежий — не трогать."""
    import audio

    data = tmp_path / "data"
    fresh_dir = data / "sck" / "только-что"
    fresh_dir.mkdir(parents=True)
    stale_dir = data / "sck" / "давно-пустая"
    stale_dir.mkdir(parents=True)
    week_ago = time.time() - 7 * 86400
    os.utime(stale_dir, (week_ago, week_ago))
    monkeypatch.setattr(audio, "fresh_sck_manifest", lambda: None)

    audio.AudioHub.prune_stream_files(data, 2)

    assert fresh_dir.exists(), "каталог только что стартовавшей сессии удалён"
    assert not stale_dir.exists(), "давно пустой каталог должен уйти"
