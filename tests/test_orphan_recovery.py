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
import charoite_paths

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
    charoite_paths.use_data_root(tmp_path, replace=True)
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


def test_журнал_не_открылся_пересборка_всё_равно_идёт(data_root, monkeypatch):
    """Диск или права не дали открыть журнал — пересборка идёт без него, как до №495,
    а не падает статусом «не удалось запустить» (выходной круг 1, M1)."""
    import daemon

    live = data_root / "transcripts" / "2026-08-07_181500.md"
    live.write_text("живой черновик", encoding="utf-8")
    seen: dict = {}
    real_open = open

    def no_log(path, *a, **k):
        if str(path).endswith(".log"):
            raise PermissionError("нет прав")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", no_log)
    monkeypatch.setattr(daemon.subprocess, "run", lambda cmd, **kw: seen.update(stdout=kw.get("stdout")))
    failed = []
    monkeypatch.setattr(daemon.MeetingStatusStore, "failed", lambda self, *a: failed.append(a))
    daemon._rebuild_orphans_sequentially([live])

    assert seen.get("stdout") is daemon.subprocess.DEVNULL, seen
    assert failed == [], "без журнала пересборка обязана стартовать"


def test_журнал_закрывается_и_при_сбое_пересборки(data_root, monkeypatch):
    """Упал сам запуск — файл журнала закрыт, статус — «не удалось запустить»."""
    import daemon

    live = data_root / "transcripts" / "2026-08-07_181500.md"
    live.write_text("живой черновик", encoding="utf-8")
    opened: list = []

    def boom(cmd, **kw):
        opened.append(kw.get("stdout"))
        raise OSError("нет nice")

    monkeypatch.setattr(daemon.subprocess, "run", boom)
    failed = []
    monkeypatch.setattr(daemon.MeetingStatusStore, "failed", lambda self, *a: failed.append(a))
    daemon._rebuild_orphans_sequentially([live])

    assert opened and opened[0].closed, "журнал остался открытым"
    assert failed and "не удалось запустить восстановление" in failed[0][1]


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


def test_восстановление_пишет_журнал_разбора(data_root, monkeypatch):
    """№495: шапка стенограммы отсылает причину отказа движка «в журнал разбора»
    (logs/). У восстановления после падения демона он есть, как у основного пути и
    повтора, а не DEVNULL, куда причина пропадала целиком."""
    import daemon

    live = data_root / "transcripts" / "2026-08-07_181500.md"
    live.write_text("живой черновик", encoding="utf-8")
    seen: dict = {}

    def fake_run(cmd, **kw):
        out = kw.get("stdout")
        seen["name"] = getattr(out, "name", out)
        out.write("проба журнала\n")

    monkeypatch.setattr(daemon.subprocess, "run", fake_run)
    daemon._rebuild_orphans_sequentially([live])

    log = data_root / "logs" / "recover_2026-08-07_181500.log"
    assert seen.get("name") == str(log), seen
    assert log.read_text(encoding="utf-8") == "проба журнала\n"


def test_восстановление_в_своей_сессии_и_без_суда_над_кодом_возврата(data_root, monkeypatch, tmp_path):
    """Настоящий потомок, а не подмена `subprocess.run`: пересборка восстановления
    уходит в свою сессию (сигнал группе демона её не задевает — она переживает смерть
    демона), а её код возврата демон не судит: статус встречи пишет сама пересборка,
    и «не удалось запустить» поверх него было бы ложью. Оба флага до №495 не держал ни
    один тест — их нашёл мутатор диапазона."""
    import daemon

    code = tmp_path / "code"
    (code / "src").mkdir(parents=True)
    (code / "src" / "rebuild_transcript.py").write_text(
        "import os, sys\nprint('sid', os.getsid(0), flush=True)\nsys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr(daemon, "CODE", code)
    failed: list[str] = []
    monkeypatch.setattr(daemon.MeetingStatusStore, "failed",
                        lambda _self, _live, why: failed.append(str(why)))
    live = data_root / "transcripts" / "2026-08-07_181500.md"
    live.write_text("живой черновик", encoding="utf-8")

    daemon._rebuild_orphans_sequentially([live])

    log = (data_root / "logs" / "recover_2026-08-07_181500.log").read_text(encoding="utf-8")
    assert log.startswith("sid "), log
    assert int(log.split()[1]) != os.getsid(0), "пересборка в сессии демона — умрёт вместе с ним"
    assert failed == [], f"код возврата пересборки перекрасил её статус: {failed}"


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
    преемнику, и до №728 пересборка судила по нему «демон жив»: осиротевший
    `.wav.part` прежнего демона она уважала до таймаута, а затем навсегда
    отказывалась трогать целый .pcm. С №728 она судит по заявке на сам .pcm и
    убирает такой огрызок сама; преемник убирает его сразу, потому что ЗНАЕТ,
    что автор `.part` мёртв: лок встречи в его руках.
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
    """`data/sck/*` — тоже записи встречи.

    Системный звук пишет приложение, а не демон, и эти файлы жили ВНЕ
    ретеншна: каталоги сессий убирались лишь при штатном стопе — краш
    оставлял полное аудио навсегда. На рабочей машине так пролежал 61 МБ
    девять дней при обещанных двух (аудит 16.08). PRIVACY.md обещает
    «записи временны». Безусловная уборка наследия Core Audio tap
    (`tap_stream.*`) снята 06.09 (№154): чужие файлы с таким именем ретеншн
    больше не трогает.
    """
    import audio

    data = tmp_path / "data"
    (data / "sck" / "старая-сессия").mkdir(parents=True)
    (data / "sck" / "живая-сессия").mkdir(parents=True)
    stranger = data / "tap_stream.raw"                 # больше не наше наследие — не трогаем
    stranger.write_bytes(b"\0" * 16)
    old_session = data / "sck" / "старая-сессия" / "system.raw"
    old_session.write_bytes(b"\0" * 16)
    live_session = data / "sck" / "живая-сессия" / "system.raw"
    live_session.write_bytes(b"\0" * 16)

    week_ago = time.time() - 7 * 86400
    for p in (stranger, old_session, live_session):
        os.utime(p, (week_ago, week_ago))

    # живая сессия названа в свежем манифесте — её не трогаем даже старой
    monkeypatch.setattr(audio, "fresh_sck_manifest",
                        lambda: {"system": str(live_session)})

    removed = audio.AudioHub.prune_stream_files(data, 2)

    assert removed == 1, "старый сырой поток мёртвой сессии остался лежать"
    assert stranger.exists(), "ретеншн удалил файл, который не писал"
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


@pytest.fixture(autouse=True)
def _no_confirm_pause(monkeypatch):
    import daemon

    monkeypatch.setattr(daemon, "NO_FLOCK_CONFIRM_S", 0)


def test_an_orphan_on_a_volume_without_flock_is_not_held_forever(data_root, monkeypatch):
    """Том без flock: пересборка такую запись не тронет, и защита сделала бы
    встречу вечной сиротой — пересборка на каждом старте, звук сверх срока.
    Не защищаем и не пересобираем; человеку — строка «вручную» (финальный Opus I1б)."""
    import daemon
    import file_locks

    started, said = [], []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    monkeypatch.setattr(daemon, "emit", said.append)
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.NO_FLOCK)
    _orphan(data_root, "2026-08-07_181500", age_days=3)

    protect = daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert protect == set() and started == []
    assert any("без flock" in m.get("text", "") for m in said), said


@pytest.mark.parametrize("outcomes", [["taken", "taken"], ["no_flock", "taken"], ["no_flock", "busy"]])
def test_an_orphan_with_a_lockable_pcm_is_still_recovered(data_root, monkeypatch, outcomes):
    """Один TAKEN или BUSY — flock на томе есть: разовый NO_FLOCK другого канала
    (ENOLCK — любой OSError у `_try_lock`) не снимает восстановление и защиту
    ретеншна (opusfix2 Haiku C1)."""
    import daemon
    import file_locks

    started = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    table = {"taken": file_locks.TAKEN, "no_flock": file_locks.NO_FLOCK, "busy": file_locks.BUSY}
    got = iter([table[o] for o in outcomes])
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: next(got))
    pcm = _orphan(data_root, "2026-08-07_181500", age_days=3)
    (data_root / "recordings" / "2026-08-07_181500_blackhole.pcm").write_bytes(b"\0" * 4096)

    protect = daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert protect == {"2026-08-07_181500"} and len(started) == 1
    assert pcm.exists()


def test_a_busy_channel_does_not_hold_the_start_of_recording(data_root, monkeypatch):
    """Дверь пересборки прежнего демона держит заявку (BUSY): проба — одна
    попытка, без ретраев на пути к `hub.start()` (opusfix3 DS I3)."""
    import daemon
    import file_locks

    calls = []

    def probe(f, **kw):
        calls.append(kw)
        return file_locks.BUSY

    naps = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", lambda lives: None)
    monkeypatch.setattr(file_locks, "acquire_outcome", probe)
    monkeypatch.setattr(daemon.time, "sleep", naps.append)
    _orphan(data_root, "2026-08-07_181500", age_days=0.1)

    assert daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000") == {"2026-08-07_181500"}
    assert calls == [{"attempts": 1}] and naps == [], (calls, naps)


def test_a_volume_without_flock_is_confirmed_after_a_pause(data_root, monkeypatch):
    """«Том без flock» снимает защиту ретеншна. NO_FLOCK `acquire_outcome` не
    повторяет, поэтому вывод подтверждает вторая проба через паузу: разовый
    ENOLCK встречу не уводит (opusfix2 Haiku C1, opusfix4 DS I4)."""
    import daemon
    import file_locks

    calls, naps = [], []

    def probe(f, **kw):
        calls.append(kw)
        return file_locks.NO_FLOCK if len(calls) == 1 else file_locks.TAKEN

    monkeypatch.setattr(daemon, "_start_orphan_chain", lambda lives: None)
    monkeypatch.setattr(file_locks, "acquire_outcome", probe)
    monkeypatch.setattr(daemon.time, "sleep", naps.append)
    monkeypatch.setattr(daemon, "NO_FLOCK_CONFIRM_S", 0.5)   # снятую фикстурой паузу — назад
    _orphan(data_root, "2026-08-07_181500", age_days=3)

    assert daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000") == {"2026-08-07_181500"}
    assert calls == [{"attempts": 1}, {"attempts": 1}], calls
    assert naps == [0.5], naps


def test_a_stat_failure_keeps_the_orphan_marker(data_root, monkeypatch):
    """Сбой stat у `.pcm` — «не знаем»: встреча остаётся сиротой под защитой
    ретеншна, решает проба (`_has_sound`)."""
    import daemon
    import file_locks

    started = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.TAKEN)
    pcm = _orphan(data_root, "2026-08-07_181500", age_days=0.1)
    real = pathlib.Path.stat

    def fail(self, *a, **kw):
        if self == pcm:
            raise PermissionError(13, "EACCES")
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "stat", fail)

    assert daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000") == {"2026-08-07_181500"}
    assert len(started) == 1


def test_an_unreadable_channel_does_not_veto_a_volume_without_flock(data_root, monkeypatch):
    """mic на томе без flock, blackhole не открыть: пересборка всё равно вернёт
    None по NO_FLOCK — защита сделала бы встречу вечной сиротой (opusfix1 DS I3)."""
    import daemon
    import file_locks

    started = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.NO_FLOCK)
    _orphan(data_root, "2026-08-07_181500", age_days=0.1)
    bh = data_root / "recordings" / "2026-08-07_181500_blackhole.pcm"
    bh.write_bytes(b"\0" * 4096)
    real_open = pathlib.Path.open

    def deny(self, *a, **kw):
        if self == bh:
            raise PermissionError(self)
        return real_open(self, *a, **kw)

    for labels in (("mic", "blackhole"), ("blackhole", "mic")):
        monkeypatch.setattr(pathlib.Path, "open", deny)
        monkeypatch.setattr(daemon.meeting_stamp, "RECORDING_LABELS", labels)
        assert daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000") == set(), labels
    assert started == []


@pytest.mark.parametrize("wav", [False, True])
def test_an_empty_pcm_is_no_orphan_marker(data_root, monkeypatch, wav):
    """Пустой `.pcm` (писатель убит до первого блока) — не сирота: иначе
    пересборка на каждом старте и ретеншн её придерживает (финальный Opus I1а).
    Файл не трогаем — его уберёт ретеншн; снимать нельзя: писатель без заявки
    неотличим от убитого (opusfix1 DS C1). С `.wav` соседнего канала — опыт x4."""
    import audio
    import daemon

    started = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    rec = data_root / "recordings"
    stamp = "2026-08-07_181500"
    pcm = rec / f"{stamp}_blackhole.pcm"
    pcm.write_bytes(b"")
    if wav:
        (rec / f"{stamp}_mic.wav").write_bytes(b"RIFF")
    old = time.time() - 3 * 86400
    os.utime(pcm, (old, old))
    (data_root / "transcripts" / f"{stamp}.md").write_text("черновик", encoding="utf-8")

    protect = daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000")

    assert protect == set() and started == []
    assert pcm.exists(), "снимать пустой .pcm сирот некому — только ретеншн"
    audio.AudioHub.prune_recordings(rec, 2, protect=protect)
    assert not pcm.exists(), "ретеншн обязан убрать пустой .pcm по сроку"


def test_a_channel_with_its_wav_does_not_vote_for_a_volume_without_flock(data_root, monkeypatch):
    """Том без flock, `.wav` готов, `.pcm` рядом (крах между `replace` и
    `unlink`): финал из `.wav` соберётся на любом томе, остаток снимет
    `_drop_leftover` — встречу восстанавливаем (opusfix4 DS C1)."""
    import daemon
    import file_locks

    started = []
    monkeypatch.setattr(daemon, "_start_orphan_chain", started.append)
    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.NO_FLOCK)
    _orphan(data_root, "2026-08-07_181500", age_days=3)
    (data_root / "recordings" / "2026-08-07_181500_mic.wav").write_bytes(b"RIFF")

    assert daemon._recover_orphans(CFG, current_stamp="2026-08-10_090000") == {"2026-08-07_181500"}
    assert len(started) == 1


def test_a_stamp_without_recordings_is_not_a_volume_without_flock(data_root, monkeypatch):
    """Записи пропали между обходом и пробой: «том без flock» не выводится из пустоты."""
    import daemon
    import file_locks

    monkeypatch.setattr(file_locks, "acquire_outcome", lambda f, **kw: file_locks.NO_FLOCK)

    assert daemon._volume_without_flock(data_root / "recordings", "2026-08-07_181500") is False
