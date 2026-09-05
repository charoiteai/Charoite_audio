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
    from transcript import Transcript

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
    from transcript import Transcript

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


def test_rebuild_разрешает_один_штамп_на_оба_канала(tmp_path, monkeypatch):
    """mic и blackhole обязаны получить один результат общего разрешения."""
    import rebuild_transcript as rt

    live = tmp_path / "2026-08-04_1203_Тема.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    resolved: list[tuple[str, tuple[str, ...]]] = []
    asked: list[tuple[str, str]] = []

    def resolve(_rec_dir, stamp, labels=meeting_stamp.RECORDING_LABELS, **kw):
        resolved.append((stamp, labels, kw))
        return "2026-08-04_120301"

    monkeypatch.setattr(rt.meeting_stamp, "resolve_stamp", resolve)
    monkeypatch.setattr(rt, "wait_recording",
                        lambda _dir, stamp, label, _sr: asked.append((stamp, label)))
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(tmp_path / "recordings"))

    rt.rebuild(live, {"audio": {"samplerate": 16000}})

    # сайдкара нет — точного штампа нет, каталог стенограмм передан для
    # проверки владения единственным кандидатом минуты
    assert resolved == [("2026-08-04_1203", meeting_stamp.RECORDING_LABELS,
                         {"exact": None, "tdir": tmp_path})]
    assert asked == [("2026-08-04_120301", "mic"),
                     ("2026-08-04_120301", "blackhole")]


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
    from transcript import Transcript

    d = tmp_path / "transcripts"
    first, second = Transcript(d), Transcript(d)
    assert second.stamp.endswith("-1"), f"нет суффикса коллизии: {second.stamp}"
    assert meeting_stamp.started_at(second.stamp) == meeting_stamp.started_at(first.stamp)


def test_чужое_имя_файла_пересборку_не_запускает():
    """Производное (`_minutes.md`, `_hints.md`) и посторонние файлы — не встречи."""
    for foreign in ("заметки", "2026-07-22_разбор", "readme", ""):
        assert meeting_stamp.started_at(foreign) is None, foreign
    for derived in ("2026-08-04_1203_Отчет_по_задачам_разбор",
                    "2026-08-04_120310_hints",
                    "2026-08-04_1203_Тема_ревизия_claude",
                    "2026-08-04_1203_Тема_minutes"):
        assert meeting_stamp.stamp_of(derived) is None, derived


def test_главный_файл_с_темой_остаётся_встречей():
    """Retry из приложения приходит по transcript_path — минутное имя с темой."""
    assert meeting_stamp.stamp_of("2026-08-04_1203_Отчет_по_задачам") == "2026-08-04_1203"
    assert meeting_stamp.stamp_of("2026-08-04_120310") == "2026-08-04_120310"
    assert meeting_stamp.stamp_of("2026-08-04_120310-1_Тема") == "2026-08-04_120310-1"


def test_retry_находит_посекундную_запись_по_минутному_имени(tmp_path):
    """Инцидент-близнец 28.07: демон назвал записи «…_120310_mic.wav», а retry
    из приложения знает файл «…_1203_Отчет_по_задачам.md». Точного имени на
    диске нет — resolve_stamp обязан довести до реального штампа записи."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-04_120310", "mic", "wav").write_bytes(b"RIFF")

    resolved = meeting_stamp.resolve_stamp(rec, "2026-08-04_1203")
    assert resolved == "2026-08-04_120310"

    # Две встречи в одну минуту — двусмысленность, штамп не трогаем.
    meeting_stamp.recording_path(rec, "2026-08-04_120355", "mic", "wav").write_bytes(b"RIFF")
    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203") == "2026-08-04_1203"


def test_retry_не_смешивает_каналы_двух_встреч_в_одну_минуту(tmp_path):
    """Репродукция аудита: mic первой + blackhole второй — не одна встреча."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(
        rec, "2026-08-04_120301", "mic", "wav").write_bytes(b"RIFF")
    meeting_stamp.recording_path(
        rec, "2026-08-04_120359", "blackhole", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203") == "2026-08-04_1203", (
        "два разных посекундных штампа нельзя разрешать по одному на канал")


def test_посекундная_встреча_не_берёт_запись_соседки(tmp_path):
    """Ревью 20.08 (GLM): своей записи нет — чужую брать НЕЛЬЗЯ.

    Демон после краха поднимается за две секунды, то есть внутри той же
    минуты: 143012 и 143047 — разные встречи разных людей. Если запись
    первой удалил ретеншн (или крэш не дал её дописать), в минуте остаётся
    ровно одна чужая — и прежний `len(found) == 1` объявлял её однозначной.
    Разговор соседней встречи молча уезжал в чужую стенограмму, оттуда в
    граф и в память: человек в этой цепочке не участвует вовсе.
    """
    rec = tmp_path / "recordings"
    rec.mkdir()
    for label in ("mic", "blackhole"):
        meeting_stamp.recording_path(
            rec, "2026-08-20_143047", label, "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(rec, "2026-08-20_143012") == "2026-08-20_143012", (
        "секунда встречи известна точно — приблизительное имя искать незачем")


def test_минутное_имя_с_точным_штампом_не_берёт_запись_соседки(tmp_path):
    """Ревью 04.09 (GLM): та же дыра, что закрыта 20.08, только вход минутный.

    После наката темы файл встречи зовётся «…_1203_Тема», своих записей нет
    (крэш не дописал, ретеншн удалил), а в минуте лежит одна запись
    соседки — глоб по минуте объявлял её однозначной. Сайдкар знает точный
    штамп: записи либо под ним, либо их нет.
    """
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(
        rec, "2026-08-04_120314", "mic", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(
        rec, "2026-08-04_1203", exact="2026-08-04_120301") == "2026-08-04_120301", (
        "точный штамп известен — запись соседки той же минуты не наша")


def test_точный_штамп_разрешает_две_встречи_в_минуту(tmp_path):
    """Обратная сторона: с точным штампом двусмысленность минуты исчезает."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    for s in ("2026-08-04_120301", "2026-08-04_120314"):
        meeting_stamp.recording_path(rec, s, "mic", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(
        rec, "2026-08-04_1203", exact="2026-08-04_120301") == "2026-08-04_120301"


def test_наследие_без_штампа_не_берёт_запись_соседки_с_живой_стенограммой(tmp_path):
    """Сайдкара со штампом нет (встречи до этой правки): единственный
    кандидат минуты принимается, только если у него нет собственной
    стенограммы — иначе это соседка, а не мы. Голый «…120314.md» — тоже
    улика: соседка, чей конвейер упал до наката темы; своего голого остатка
    рядом с минутным именем не бывает (переименования — через rename())."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-04_1203_Тема.md").write_text(
        "# Встреча 2026-08-04_1203 — Тема\n", encoding="utf-8")
    (tdir / "2026-08-04_120314.md").write_text(
        "# Встреча 2026-08-04_120314\n", encoding="utf-8")
    meeting_stamp.recording_path(
        rec, "2026-08-04_120314", "mic", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_1203"
    # соседка с темой под своим посекундным ключом — тоже соседка
    (tdir / "2026-08-04_120314.md").rename(tdir / "2026-08-04_120314_Другая.md")
    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_1203"
    # .md соседки стёрт руками, остался её сайдкар С КЛЮЧОМ stamp (новый демон) —
    # та же улика (GLM I3 по main 05.09)
    (tdir / "2026-08-04_120314_Другая.md").unlink()
    import json as _json
    (tdir / "2026-08-04_120314_Другая.md.live.json").write_text(
        _json.dumps({"stamp": "2026-08-04_120314"}), encoding="utf-8")
    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_1203"
    # сайдкар без ключа под посекундным именем — наследие до 0.69.1, с тем же
    # успехом наш собственный (DS r1 по #494): не улика, запись берётся
    (tdir / "2026-08-04_120314_Другая.md.live.json").write_text("{}", encoding="utf-8")
    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_120314"
    # без единого следа соседки единственный кандидат по-прежнему наш
    (tdir / "2026-08-04_120314_Другая.md.live.json").unlink()
    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_120314"


def test_наследие_без_штампа_по_прежнему_находит_свою_запись(tmp_path):
    """Кандидат без собственной стенограммы — наша запись под посекундным
    именем демона (инцидент 28.07 не возвращается)."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-04_1203_Тема.md").write_text(
        "# Встреча 2026-08-04_1203 — Тема\n", encoding="utf-8")
    (tdir / "2026-08-04_1203_Тема_minutes.md").write_text("минутки\n", encoding="utf-8")
    meeting_stamp.recording_path(
        rec, "2026-08-04_120310", "mic", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(rec, "2026-08-04_1203", tdir=tdir) == "2026-08-04_120310"


def test_сайдкар_отдаёт_точный_штамп_минутной_встречи(tmp_path):
    """Источник точного штампа — ключ `stamp` сайдкара; мусор и чужая минута
    точным штампом не считаются, посекундной стенограмме уточнять нечего."""
    import json

    import live_sidecar

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-08-04_1203_Тема.md"
    live.write_text("# Встреча 2026-08-04_1203 — Тема\n", encoding="utf-8")
    assert live_sidecar.exact_stamp(live) is None      # сайдкара нет
    sidecar = live.with_name(live.name + ".live.json")
    sidecar.write_text(json.dumps({"stamp": "2026-08-04_120301"}), encoding="utf-8")
    assert live_sidecar.exact_stamp(live) == "2026-08-04_120301"
    for bad in ("2026-08-04_120501", 120301, "2026-08-04_1203", "мусор",
                "2026-08-04_1203-1",     # минута с суффиксом коллизии — не секунды (DS M3)
                "2026-08-04_120399"):    # секунд 99 не бывает: strptime бросал бы ValueError (DS-FW M1)
        sidecar.write_text(json.dumps({"stamp": bad}), encoding="utf-8")
        assert live_sidecar.exact_stamp(live) is None, bad

    sec = tdir / "2026-08-04_120301.md"
    sec.write_text("# Встреча 2026-08-04_120301\n", encoding="utf-8")
    sec.with_name(sec.name + ".live.json").write_text(
        json.dumps({"stamp": "2026-08-04_120301"}), encoding="utf-8")
    assert live_sidecar.exact_stamp(sec) is None       # имя уже посекундное


def test_чужой_сайдкар_минуты_не_источник_точного_штампа(tmp_path):
    """GLM Critical r1 по #492: сирота удалённой соседки («…120314.md» стёрт
    руками, сайдкар остался) усыновляется owner_of как «владельца минуты» —
    для хешей это дёшево, для выбора записи — чужой разговор в нашей
    стенограмме. Точный штамп читается только из прямого сайдкара: ни имя
    наследия, ни ключ в чужом файле им не считаются."""
    import json

    import live_sidecar

    live = tmp_path / "2026-08-04_1203_Тема.md"
    live.write_text("# Встреча 2026-08-04_1203 — Тема\n", encoding="utf-8")
    # сайдкар-наследие (до 0.69.1) под посекундным именем — не источник
    (tmp_path / "2026-08-04_120301.md.live.json").write_text("{}", encoding="utf-8")
    assert live_sidecar.exact_stamp(live) is None
    # сирота соседки с ключом stamp — тем более не источник
    (tmp_path / "2026-08-04_120314.md.live.json").write_text(
        json.dumps({"stamp": "2026-08-04_120314"}), encoding="utf-8")
    assert live_sidecar.exact_stamp(live) is None
    # свой прямой сайдкар — единственный источник
    live.with_name(live.name + ".live.json").write_text(
        json.dumps({"stamp": "2026-08-04_120301"}), encoding="utf-8")
    assert live_sidecar.exact_stamp(live) == "2026-08-04_120301"


def test_rebuild_передаёт_точный_штамп_из_сайдкара(tmp_path, monkeypatch):
    """Пересборка озаглавленной встречи ищет записи по штампу из сайдкара."""
    import json

    import rebuild_transcript as rt

    live = tmp_path / "2026-08-04_1203_Тема.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    live.with_name(live.name + ".live.json").write_text(
        json.dumps({"stamp": "2026-08-04_120301"}), encoding="utf-8")
    seen: list[dict] = []

    def resolve(_rec_dir, stamp, labels=meeting_stamp.RECORDING_LABELS, **kw):
        seen.append(kw)
        return "2026-08-04_120301"

    monkeypatch.setattr(rt.meeting_stamp, "resolve_stamp", resolve)
    monkeypatch.setattr(rt, "wait_recording", lambda *_a: None)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(tmp_path / "recordings"))

    rt.rebuild(live, {"audio": {"samplerate": 16000}})

    assert seen == [{"exact": "2026-08-04_120301", "tdir": tmp_path}]


def test_rebuild_после_отказа_по_минуте_не_ждёт_записи(tmp_path, monkeypatch):
    """GLM M5 по main 05.09: отказ по минуте доказывает, что под минутным
    именем записи нет и не появится — 2×45 с ожидания пустые."""
    import rebuild_transcript as rt

    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    live = tdir / "2026-08-04_1203_Тема.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    (tdir / "2026-08-04_120314.md").write_text("# Встреча 2026-08-04_120314\n", encoding="utf-8")
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-04_120314", "mic", "wav").write_bytes(b"RIFF")
    waited: list[str] = []
    monkeypatch.setattr(rt, "wait_recording", lambda _d, stamp, label, _sr: waited.append(f"{stamp}/{label}"))
    monkeypatch.setattr(rt, "log", lambda _m: None)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(rec))

    assert rt.rebuild(live, {"audio": {"samplerate": 16000}}) is None
    assert waited == [], "после отказа ждать нечего"


def test_rebuild_не_считает_отказом_запись_под_минутным_именем(tmp_path, monkeypatch):
    """DS r2 M3 по #492: запись, названная самой минутой (демон до 28.07),
    resolve_stamp возвращает как есть — это находка, а не отвод чужих;
    лог отказа здесь врал бы."""
    import rebuild_transcript as rt

    live = tmp_path / "2026-08-03_1130_Тема.md"
    live.write_text("# Встреча\n", encoding="utf-8")
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(rec, "2026-08-03_1130", "mic", "wav").write_bytes(b"RIFF")
    logged: list[str] = []
    monkeypatch.setattr(rt, "log", lambda msg: logged.append(msg))
    monkeypatch.setattr(rt, "wait_recording", lambda *_a: None)
    monkeypatch.setenv("SUFLER_RECORDINGS_DIR", str(rec))

    rt.rebuild(live, {"audio": {"samplerate": 16000}})

    assert not any("однозначно не разрешена" in m for m in logged), logged


def test_список_расширений_записи_один_на_всех():
    """DS r3 M2 / GLM по #492: rebuild повторял тройку расширений своим
    списком; расхождение с resolve_stamp давало бы ложный лог отказа."""
    import pathlib

    import ast

    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "rebuild_transcript.py").read_text(encoding="utf-8")
    # в rebuild нет собственного литерала из трёх расширений — по дереву, а не
    # по строке (GLM r1 по #494, M2: строковый assert обходился пробелами/порядком)
    exts = set(meeting_stamp.RECORDING_EXTS)
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            vals = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            assert vals != exts, "rebuild_transcript держит свой список расширений записи"
    assert src.count("meeting_stamp.RECORDING_EXTS") >= 2
    assert meeting_stamp.RECORDING_EXTS == ("wav", "pcm", "wav.part")


def test_демон_пишет_посекундный_штамп_в_сайдкар():
    """Вторая сторона №164: без ключа `stamp` пересборке неоткуда взять
    точный штамп после наката темы — имя файла его теряет. Структурная
    проверка, как у имени записи: важен сам факт записи ключа демоном."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "daemon.py").read_text(encoding="utf-8")
    # якорь — сам словарь сайдкара, а не первое упоминание live.json выше по
    # файлу (DS r1 M1 по #492: срез от чужого упоминания молча уехал бы)
    start = src.index('json.dumps({"speakers": len(voice_names)')
    block = src[start:src.index("ensure_ascii", start)]
    assert '"stamp": tr.stamp' in block


def test_посекундный_штамп_находит_свою_запись(tmp_path):
    """Обратная сторона: точное совпадение обязано работать по-прежнему."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    meeting_stamp.recording_path(
        rec, "2026-08-20_143012", "mic", "wav").write_bytes(b"RIFF")

    assert meeting_stamp.resolve_stamp(rec, "2026-08-20_143012") == "2026-08-20_143012"


def test_штамп_до_28_июля_ещё_читается(tmp_path):
    """Старые встречи пятнадцатизначны — руками пересобрать их можно."""
    assert meeting_stamp.started_at("2026-07-20_1830") is not None


def test_files_with_stamp_stops_at_the_stamp_boundary(tmp_path):
    """Минутный штамп — префикс секундного: `2026-08-03_1130*` хватал файлы
    встречи `2026-08-03_113012` (крэш-рестарт в ту же минуту). Правило границы
    одно на forget, archive и облачный контекст (аудит DeepSeek 16.08)."""
    d = tmp_path
    mine = [d / "2026-08-03_1130.md", d / "2026-08-03_1130_Планёрка.md",
            d / "2026-08-03_1130_minutes.md"]
    theirs = [d / "2026-08-03_113012.md", d / "2026-08-03_113012_разбор.md",
              d / "2026-08-03_11300.md"]
    for f in mine + theirs:
        f.write_text("x", encoding="utf-8")
    (d / "2026-08-03_1130_dir.md").mkdir()   # каталог с похожим именем — не файл

    got = meeting_stamp.files_with_stamp(d, "2026-08-03_1130", suffix=".md")

    assert got == sorted(mine)
    assert meeting_stamp.files_with_stamp(d / "нет", "2026-08-03_1130") == []
    assert meeting_stamp.files_with_stamp(
        d, "2026-08-03_1130", prefix="graph_", suffix=".log") == []
